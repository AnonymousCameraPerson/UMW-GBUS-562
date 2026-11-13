"""MCP server for Hartwell Foods HR: a policy search and a leave balance lookup.

Usage (from the top of the repository):
    .venv/bin/python week-04/hr_mcp_server.py               # HR staff mode (stdio)
    .venv/bin/python week-04/hr_mcp_server.py --as-employee dana.whitfield@hartwellfoods.example
    .venv/bin/python week-04/hr_mcp_server.py --rebuild     # rebuild the database and exit

Tools:
    search_policy(query)        the 4 closest chunks of the HR documents, with source files
    get_leave_balance(email)    HR staff mode: any employee's balance, by work email
    get_leave_balance()         --as-employee mode: only that one employee's balance

The HR documents in week-04/hartwell-hr/hr-docs/ are loaded and chunked with the
same code as week-03/ask.py (one chunk per section, each starting with its
document title and section heading), and stored in a Chroma database in
week-04/chroma_db/ (gitignored). The database rebuilds itself when a document
is added, removed or edited, or on demand with --rebuild.

The server never calls Claude, so it needs no API key. It talks MCP over
stdin/stdout, so everything it logs goes to stderr.
"""

import argparse
import contextlib
import csv
import importlib.util
import re
import sys
from pathlib import Path
from typing import TypedDict

import chromadb
from chromadb.config import Settings
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "hartwell-hr"
DOCS_DIR = DATA_DIR / "hr-docs"
EMPLOYEES_CSV = DATA_DIR / "employees.csv"
DB_DIR = HERE / "chroma_db"
COLLECTION = "hr_docs"
TOP_K = 4
BALANCE_FIELDS = ("vacation_days_left", "sick_days_left", "personal_days_left")

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                            idempotentHint=True, openWorldHint=False)


class PolicyChunk(TypedDict):
    rank: int
    source: str       # file name to cite, e.g. leave-policy.html
    title: str
    section: str
    similarity: float  # cosine similarity, higher is closer
    text: str


class SearchResult(TypedDict):
    query: str
    results: list[PolicyChunk]


class LeaveBalance(TypedDict):
    email: str
    name: str
    department: str
    manager: str | None
    vacation_days_left: int
    sick_days_left: int
    personal_days_left: int


def log(message):
    """stdout carries the MCP messages, so logs must go to stderr."""
    print(message, file=sys.stderr, flush=True)


def load_week03_ask():
    """Import week-03/ask.py by its path, without putting week-03 on sys.path."""
    path = HERE.parent / "week-03" / "ask.py"
    spec = importlib.util.spec_from_file_location("week03_ask", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ask = load_week03_ask()


# ---------------------------------------------------------------------------
# Policy search (Chroma database in week-04/chroma_db/)
# ---------------------------------------------------------------------------

_collection = None  # cached between tool calls


def build_index(client):
    files, skipped = ask.document_files(DOCS_DIR)
    for path in skipped:
        log(f"  Skipping {path.name}: unsupported file type {path.suffix or '(none)'}")
    if not files:
        raise RuntimeError(f"No supported documents found in {DOCS_DIR}")

    try:
        client.delete_collection(COLLECTION)
    except Exception:
        pass  # nothing to delete on the first build
    collection = client.create_collection(
        COLLECTION, metadata={"hnsw:space": "cosine",
                              "fingerprint": ask.fingerprint(files, DOCS_DIR)})
    total = 0
    for path in files:
        chunks = ask.make_chunks(path, DOCS_DIR)
        if chunks:
            collection.add(ids=[c["id"] for c in chunks],
                           documents=[c["text"] for c in chunks],
                           metadatas=[c["metadata"] for c in chunks])
        log(f"  {path.name}: {len(chunks)} chunks")
        total += len(chunks)
    log(f"Indexed {total} chunks from {len(files)} documents into {DB_DIR.relative_to(HERE.parent)}/")
    return collection


def get_collection(rebuild=False):
    """Open the database, rebuilding it if asked or if the documents changed."""
    global _collection
    files, _ = ask.document_files(DOCS_DIR)
    current = ask.fingerprint(files, DOCS_DIR)
    if (not rebuild and _collection is not None
            and (_collection.metadata or {}).get("fingerprint") == current):
        return _collection

    # Libraries can print (e.g. while downloading the embedding model);
    # keep that off stdout so it can't corrupt the MCP connection.
    with contextlib.redirect_stdout(sys.stderr):
        client = chromadb.PersistentClient(path=str(DB_DIR),
                                           settings=Settings(anonymized_telemetry=False))
        collection = None
        if not rebuild:
            try:
                collection = client.get_collection(COLLECTION)
                if (collection.metadata or {}).get("fingerprint") != current:
                    log("The HR documents have changed. Rebuilding the database...")
                    collection = None
            except Exception:
                log("No database yet. Building it...")
        else:
            log("Rebuilding the database...")
        if collection is None:
            collection = build_index(client)
    _collection = collection
    return collection


def search(query):
    query = query.strip()
    if not query:
        raise ToolError("The search query is empty. Pass a few words describing the policy question.")
    collection = get_collection()
    with contextlib.redirect_stdout(sys.stderr):
        result = collection.query(query_texts=[query], n_results=TOP_K)
    chunks = []
    for rank, (text, meta, dist) in enumerate(zip(result["documents"][0],
                                                  result["metadatas"][0],
                                                  result["distances"][0]), 1):
        chunks.append({
            "rank": rank,
            "source": meta["source"],
            "title": meta["title"],
            "section": meta["section"],
            "similarity": round(1 - dist, 3),
            "text": text,
        })
    return {"query": query, "results": chunks}


# ---------------------------------------------------------------------------
# Leave balances (employees.csv, read fresh on every call)
# ---------------------------------------------------------------------------

def normalize_email(value):
    """Accept 'dana@x.example' or 'Dana Whitfield <dana@x.example>'."""
    value = (value or "").strip()
    match = re.search(r"<([^<>]+)>", value)
    return (match.group(1) if match else value).strip().lower()


def no_record_error(email):
    shown = email or "(no address given)"
    return ToolError(
        f"No employee record matches {shown}. Do not state, estimate or guess any leave "
        "balance for this person. Say that HR will check their record and follow up, "
        "and flag the reply for human review.")


def lookup_balance(email):
    email = normalize_email(email)
    if email:
        with EMPLOYEES_CSV.open(newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                if normalize_email(row["email"]) == email:
                    record = {"email": row["email"].strip(),
                              "name": row["name"].strip(),
                              "department": row["department"].strip(),
                              "manager": row["manager"].strip() or None}
                    for field in BALANCE_FIELDS:
                        record[field] = int(row[field])
                    return record
    raise no_record_error(email)


# ---------------------------------------------------------------------------
# The MCP server
# ---------------------------------------------------------------------------

def create_server(as_employee=None):
    mcp = MCPServer(
        name="hartwell-hr",
        instructions=("Tools for answering Hartwell Foods employees' HR questions. "
                      "Use search_policy for policy facts and cite the source file of each; "
                      "use get_leave_balance for remaining leave days. Never state a policy "
                      "or a number that didn't come from these tools."),
    )

    @mcp.tool(annotations=READ_ONLY)
    def search_policy(query: str) -> SearchResult:
        """Search Hartwell Foods' HR policy documents (leave, vacation, parental and
        family leave, benefits enrollment, remote work, reporting workplace concerns).

        Returns the 4 closest document sections. Each result has the source file
        to cite, the document title and section heading, a similarity score
        (higher is closer) and the full section text. Base answers only on the
        returned text, and say so if it doesn't answer the question.

        Args:
            query: what to look for, e.g. "carry over unused vacation days"
        """
        return search(query)

    if as_employee is None:
        @mcp.tool(annotations=READ_ONLY)
        def get_leave_balance(email: str) -> LeaveBalance:
            """Look up one employee's remaining leave days by their work email address
            (for HR staff). Returns name, department, manager, and vacation, sick and
            personal days left. If no record matches, it returns an error: then do not
            state any balance.

            Args:
                email: the employee's work email, e.g. dana.whitfield@hartwellfoods.example
            """
            return lookup_balance(email)
    else:
        @mcp.tool(annotations=READ_ONLY,
                  description=(
                      f"Return the remaining leave days of the employee this server is set "
                      f"up for ({as_employee}): name, department, manager, and vacation, "
                      f"sick and personal days left. It can't look up anyone else. If no "
                      f"record matches, it returns an error: then do not state any balance."))
        def get_leave_balance() -> LeaveBalance:
            return lookup_balance(as_employee)

    return mcp


def main():
    parser = argparse.ArgumentParser(
        description="MCP server with Hartwell Foods HR tools (policy search, leave balances).")
    parser.add_argument("--as-employee", metavar="EMAIL",
                        help="serve only this employee's leave balance; "
                             "get_leave_balance then takes no inputs")
    parser.add_argument("--rebuild", action="store_true",
                        help="rebuild the Chroma database from hr-docs/ and exit")
    args = parser.parse_args()

    if args.rebuild:
        get_collection(rebuild=True)
        return

    as_employee = None
    if args.as_employee is not None:
        as_employee = normalize_email(args.as_employee)
        if not as_employee:
            parser.error("--as-employee needs an email address")
        try:
            lookup_balance(as_employee)
            log(f"Employee mode: get_leave_balance serves only {as_employee}")
        except ToolError:
            log(f"Warning: no employee record matches {as_employee}; "
                "get_leave_balance will return an error")

    get_collection()  # build or check the database before the first request
    create_server(as_employee).run("stdio")


if __name__ == "__main__":
    main()
