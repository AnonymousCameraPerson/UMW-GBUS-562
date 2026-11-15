"""MCP server for an organization's HR: a policy search and a leave balance lookup.

Usage (from the top of the repository):
    .venv/bin/python week-04/hr_mcp_server.py               # HR staff mode (stdio), Hartwell Foods
    .venv/bin/python week-04/hr_mcp_server.py --org week-04/ridgeline-hotels
    .venv/bin/python week-04/hr_mcp_server.py --as-employee dana.whitfield@hartwellfoods.example
    .venv/bin/python week-04/hr_mcp_server.py --org week-04/ridgeline-hotels --rebuild

--org FOLDER names an organization folder laid out like this:
    FOLDER/hr-docs/            the HR policy documents (HTML, PDF, text, ...)
    FOLDER/employees.csv       one row per employee, with a header row
    FOLDER/system_prompt.txt   the agent's instructions (read by agent.py)
Without --org, week-04/hartwell-hr is used.

Tools:
    search_policy(query)        the 4 closest chunks of the HR documents, with source files
    get_leave_balance(email)    HR staff mode: any employee's balance, by work email
    get_leave_balance()         --as-employee mode: only that one employee's balance

Column names in employees.csv aren't fixed. They are worked out from the header row:
the email column is the one whose header mentions "email" (or, failing that, whose
values are all email addresses); balance columns are numeric columns whose header
mentions "left", "remaining", "balance", "available", or "days"/"hours". The name
column, if any, is the one whose header mentions "name"; every other column is
returned as extra details (department, hotel, supervisor, ...).

The HR documents are chunked with the same code as week-03/ask.py and stored in a
Chroma database in week-04/chroma_db/<organization folder name>/ (gitignored), so
each organization has its own database. It rebuilds itself when a document is
added, removed or edited, or on demand with --rebuild.

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
DEFAULT_ORG = HERE / "hartwell-hr"
DB_ROOT = HERE / "chroma_db"
COLLECTION = "hr_docs"
TOP_K = 4

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                            idempotentHint=True, openWorldHint=False)

EMAIL_RE = re.compile(r"^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$")
NUMBER_RE = re.compile(r"^-?\d+(\.\d+)?$")
BALANCE_WORDS = ("left", "remaining", "balance", "available")
UNIT_WORDS = ("days", "day", "hours", "hrs")


class PolicyChunk(TypedDict):
    rank: int
    source: str       # file name to cite, e.g. pto-policy.html
    title: str
    section: str
    similarity: float  # cosine similarity, higher is closer
    text: str


class SearchResult(TypedDict):
    query: str
    results: list[PolicyChunk]


class LeaveBalance(TypedDict):
    email: str
    name: str | None
    balances: dict[str, int | float | None]   # keyed by the column header, e.g. "PTO Days Remaining"
    details: dict[str, str]                   # the other columns, e.g. "Hotel", "Supervisor"


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
# The organization folder
# ---------------------------------------------------------------------------

class Org:
    """Paths and names for one organization folder."""

    def __init__(self, folder):
        self.dir = Path(folder).resolve()
        self.docs_dir = self.dir / "hr-docs"
        self.employees_csv = self.dir / "employees.csv"
        self.system_prompt = self.dir / "system_prompt.txt"
        self.db_dir = DB_ROOT / self.dir.name
        self.name = self._display_name()

    def check(self):
        """Return a list of problems with the folder (empty if it's usable)."""
        problems = []
        if not self.dir.is_dir():
            return [f"{self.dir} is not a folder"]
        if not self.docs_dir.is_dir():
            problems.append(f"missing folder {self.docs_dir.name}/")
        if not self.employees_csv.is_file():
            problems.append(f"missing file {self.employees_csv.name}")
        return problems

    def _display_name(self):
        """'Ridgeline Hotels' from 'You are the HR assistant for Ridgeline Hotels.',
        or else from the folder name."""
        try:
            text = self.system_prompt.read_text(encoding="utf-8")
            match = re.search(r"\bfor ([A-Z][\w&'.\- ]{1,60}?)(?:[.,;]|\s+You\b)", text)
            if match:
                return match.group(1).strip()
        except OSError:
            pass
        return self.dir.name.replace("-", " ").replace("_", " ").title()

    def topics(self):
        files, _ = ask.document_files(self.docs_dir)
        return ", ".join(p.stem.replace("-", " ").replace("_", " ") for p in files)


ORG = Org(DEFAULT_ORG)  # replaced by main() when --org is given


# ---------------------------------------------------------------------------
# Policy search (one Chroma database per organization)
# ---------------------------------------------------------------------------

_collection = None  # cached between tool calls


def build_index(client):
    files, skipped = ask.document_files(ORG.docs_dir)
    for path in skipped:
        log(f"  Skipping {path.name}: unsupported file type {path.suffix or '(none)'}")
    if not files:
        raise RuntimeError(f"No supported documents found in {ORG.docs_dir}")

    try:
        client.delete_collection(COLLECTION)
    except Exception:
        pass  # nothing to delete on the first build
    collection = client.create_collection(
        COLLECTION, metadata={"hnsw:space": "cosine",
                              "fingerprint": ask.fingerprint(files, ORG.docs_dir)})
    total = 0
    for path in files:
        chunks = ask.make_chunks(path, ORG.docs_dir)
        if chunks:
            collection.add(ids=[c["id"] for c in chunks],
                           documents=[c["text"] for c in chunks],
                           metadatas=[c["metadata"] for c in chunks])
        log(f"  {path.name}: {len(chunks)} chunks")
        total += len(chunks)
    log(f"Indexed {total} chunks from {len(files)} documents into "
        f"{ORG.db_dir.relative_to(HERE.parent)}/")
    return collection


def get_collection(rebuild=False):
    """Open the organization's database, rebuilding it if asked or if the documents changed."""
    global _collection
    files, _ = ask.document_files(ORG.docs_dir)
    current = ask.fingerprint(files, ORG.docs_dir)
    if (not rebuild and _collection is not None
            and (_collection.metadata or {}).get("fingerprint") == current):
        return _collection

    # Libraries can print (e.g. while downloading the embedding model);
    # keep that off stdout so it can't corrupt the MCP connection.
    with contextlib.redirect_stdout(sys.stderr):
        client = chromadb.PersistentClient(path=str(ORG.db_dir),
                                           settings=Settings(anonymized_telemetry=False))
        collection = None
        if not rebuild:
            try:
                collection = client.get_collection(COLLECTION)
                if (collection.metadata or {}).get("fingerprint") != current:
                    log("The HR documents have changed. Rebuilding the database...")
                    collection = None
            except Exception:
                log(f"No database yet for {ORG.dir.name}. Building it...")
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


def _words(header):
    """'PTO Days Remaining' / 'vacation_days_left' -> {'pto', 'days', 'remaining'} etc."""
    return set(re.sub(r"[^a-z0-9]+", " ", header.lower()).split())


def detect_columns(headers, rows):
    """Work out which columns hold the email, the name, and the balances.

    Returns (email column, name column or None, [balance columns]).
    Raises ValueError if there's no email column or no balance column.
    """
    headers = [h for h in headers if h is not None and h.strip()]

    def values(h):
        return [(r.get(h) or "").strip() for r in rows if (r.get(h) or "").strip()]

    def all_numeric(h):
        v = values(h)
        return bool(v) and all(NUMBER_RE.match(x) for x in v)

    def all_emails(h):
        v = values(h)
        return bool(v) and all(EMAIL_RE.match(normalize_email(x)) for x in v)

    mentions_email = [h for h in headers
                      if _words(h) & {"email", "mail"} or "e-mail" in h.lower()
                      or "email" in h.lower().replace(" ", "").replace("_", "")]
    email_col = next((h for h in mentions_email if all_emails(h)), None)
    email_col = email_col or next((h for h in headers if all_emails(h)), None)
    if email_col is None:
        raise ValueError(f"no column of email addresses (headers: {', '.join(headers)})")

    name_candidates = [h for h in headers if h != email_col and "name" in _words(h)]
    # prefer "name" / "employee name" / "full name" over "manager name" etc.
    name_col = next((h for h in name_candidates
                     if not _words(h) & {"manager", "supervisor", "hotel", "department"}),
                    name_candidates[0] if name_candidates else None)

    balance_cols = [h for h in headers
                    if h not in (email_col, name_col) and all_numeric(h)
                    and (_words(h) & set(BALANCE_WORDS) or _words(h) & set(UNIT_WORDS))]
    if not balance_cols:
        raise ValueError("no leave balance columns (numeric columns whose header mentions "
                         f"days, hours, left, remaining or balance; headers: {', '.join(headers)})")
    return email_col, name_col, balance_cols


def read_employees():
    """Return (rows, email column, name column, balance columns)."""
    with ORG.employees_csv.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        headers = reader.fieldnames or []
    return (rows, *detect_columns(headers, rows))


def to_number(value):
    value = (value or "").strip()
    if not value:
        return None
    number = float(value)
    return int(number) if number.is_integer() else number


def no_record_error(email):
    shown = email or "(no address given)"
    return ToolError(
        f"No employee record matches {shown}. Do not state, estimate or guess any leave "
        "balance for this person. Say that HR will check their record and follow up, "
        "and flag the reply for human review.")


def lookup_balance(email):
    email = normalize_email(email)
    if email:
        rows, email_col, name_col, balance_cols = read_employees()
        for row in rows:
            if normalize_email(row.get(email_col)) == email:
                skip = {email_col, name_col, *balance_cols}
                return {
                    "email": row[email_col].strip(),
                    "name": (row.get(name_col) or "").strip() or None if name_col else None,
                    "balances": {h: to_number(row.get(h)) for h in balance_cols},
                    "details": {h: (row.get(h) or "").strip() for h in row
                                if h and h not in skip},
                }
    raise no_record_error(email)


# ---------------------------------------------------------------------------
# The MCP server
# ---------------------------------------------------------------------------

def create_server(as_employee=None):
    org = ORG.name
    mcp = MCPServer(
        name=f"{ORG.dir.name}-hr",
        instructions=(f"Tools for answering {org} employees' HR questions. "
                      "Use search_policy for policy facts and cite the source file of each; "
                      "use get_leave_balance for remaining leave days. Never state a policy "
                      "or a number that didn't come from these tools."),
    )

    @mcp.tool(annotations=READ_ONLY, description=(
        f"Search {org}'s HR policy documents ({ORG.topics()}).\n\n"
        "Returns the 4 closest document sections. Each result has the source file "
        "to cite, the document title and section heading, a similarity score "
        "(higher is closer) and the full section text. Base answers only on the "
        "returned text, and say so if it doesn't answer the question.\n\n"
        "Args:\n    query: what to look for, e.g. \"carry over unused days off\""))
    def search_policy(query: str) -> SearchResult:
        return search(query)

    returns = ("the employee's name, their leave balances (keyed by the employee file's "
               "column names, e.g. days remaining of each leave type), and other details "
               "from their record such as department, location or supervisor")
    if as_employee is None:
        @mcp.tool(annotations=READ_ONLY, description=(
            f"Look up one {org} employee's remaining leave by their work email address "
            f"(for HR staff). Returns {returns}. If no record matches, it returns an "
            "error: then do not state any balance.\n\n"
            "Args:\n    email: the employee's work email address"))
        def get_leave_balance(email: str) -> LeaveBalance:
            return lookup_balance(email)
    else:
        @mcp.tool(annotations=READ_ONLY, description=(
            f"Return the remaining leave of the {org} employee this server is set up for "
            f"({as_employee}): {returns}. It can't look up anyone else. If no record "
            "matches, it returns an error: then do not state any balance."))
        def get_leave_balance() -> LeaveBalance:
            return lookup_balance(as_employee)

    return mcp


def main():
    global ORG
    parser = argparse.ArgumentParser(
        description="MCP server with an organization's HR tools (policy search, leave balances).")
    parser.add_argument("--org", type=Path, metavar="FOLDER", default=DEFAULT_ORG,
                        help="organization folder with hr-docs/, employees.csv and "
                             "system_prompt.txt (default: week-04/hartwell-hr)")
    parser.add_argument("--as-employee", metavar="EMAIL",
                        help="serve only this employee's leave balance; "
                             "get_leave_balance then takes no inputs")
    parser.add_argument("--rebuild", action="store_true",
                        help="rebuild the organization's Chroma database from hr-docs/ and exit")
    args = parser.parse_args()

    ORG = Org(args.org)
    problems = ORG.check()
    if problems:
        parser.error(f"--org {args.org}: " + "; ".join(problems))
    try:
        _, email_col, name_col, balance_cols = read_employees()
    except (ValueError, OSError, csv.Error) as exc:
        parser.error(f"can't read {ORG.employees_csv}: {exc}")
    shown = ORG.dir.relative_to(HERE.parent) if ORG.dir.is_relative_to(HERE.parent) else ORG.dir
    log(f"Organization: {ORG.name} ({shown})")
    log(f"Employee file columns: email = {email_col!r}, name = {name_col!r}, "
        f"balances = {', '.join(repr(c) for c in balance_cols)}")

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
