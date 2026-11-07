"""Answer questions about a folder of documents with RAG.

Usage (from the top of the repository):
    .venv/bin/python week-03/ask.py "How much can I spend on dinner in Boston?"
    .venv/bin/python week-03/ask.py              # ask questions interactively
    .venv/bin/python week-03/ask.py --rebuild    # re-index after adding/removing documents
    .venv/bin/python week-03/ask.py --docs week-03/larkfield-docs "What is the overdraft fee?"
    .venv/bin/python week-03/ask.py "..." --save week-03/runs/q1.txt   # also save the output

Documents are read from the folder given with --docs (default:
week-03/copperline-docs/) and can be HTML, PDF, Markdown or plain text. Each
document is split into one chunk per section, and every chunk starts with its
document title and section heading so it makes sense on its own. Each document
folder gets its own Chroma database under week-03/chroma_db/, embedded with
Chroma's built-in embedding model.
"""

import argparse
import hashlib
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import anthropic
import chromadb
import pdfplumber
from bs4 import BeautifulSoup
from chromadb.config import Settings

HERE = Path(__file__).resolve().parent
DEFAULT_DOCS_DIR = HERE / "copperline-docs"
DB_ROOT = HERE / "chroma_db"  # one database per document folder inside this
COLLECTION = "documents"
KEY_DIR = HERE.parent.parent  # the folder that contains the repository

MODEL = "claude-sonnet-5"
TOP_K = 4
PREVIEW_CHARS = 150
MAX_CHUNK_WORDS = 400  # longer sections are split at paragraph boundaries

SYSTEM_PROMPT = """You answer questions about an organization's internal \
documents, such as its policies and procedures.

Answer using ONLY the sources provided in the user's message. Do not use outside \
knowledge or make assumptions about the organization's policies.
- After each fact you state, cite the source file it came from in square \
brackets, e.g. [travel-expense-policy.html].
- If the sources do not contain the answer, say clearly that the provided \
documents don't answer the question. If they answer only part of it, answer \
that part and say what is missing.
- Keep the answer short and direct."""


# ---------------------------------------------------------------------------
# Loading documents into sections
# ---------------------------------------------------------------------------
# Every loader returns (title, doc_info, sections), where sections is a list of
# (heading, [paragraph, ...]) and doc_info is a version/owner line (or "").


def clean(text):
    return re.sub(r"\s+", " ", text).strip()


def load_html(path):
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    for tag in soup(["script", "style", "nav"]):
        tag.decompose()

    h1 = soup.find("h1")
    if h1:
        title = clean(h1.get_text())
    elif soup.title:
        title = clean(soup.title.get_text().split("|")[0])
    else:
        title = path.stem.replace("-", " ").title()

    meta = soup.find(class_="meta")
    doc_info = clean(meta.get_text()) if meta else ""

    body = soup.find("main") or soup.body or soup
    sections = []
    heading, paragraphs = "Overview", []
    for el in body.find_all(["h2", "h3", "p", "ul", "ol", "table"]):
        if el.name in ("p", "ul", "ol") and el.find_parent(["li", "table"]):
            continue  # nested inside a list or table already handled
        if el is meta:
            continue
        if el.name == "h2":
            if paragraphs:
                sections.append((heading, paragraphs))
            heading, paragraphs = clean(el.get_text()), []
        elif el.name == "h3":
            paragraphs.append(clean(el.get_text()) + ":")
        elif el.name in ("ul", "ol"):
            items = ["- " + clean(li.get_text()) for li in el.find_all("li", recursive=False)]
            if paragraphs and paragraphs[-1].endswith(":"):
                # keep a list together with the sentence that introduces it
                paragraphs[-1] += "\n" + "\n".join(items)
            else:
                paragraphs.append("\n".join(items))
        elif el.name == "table":
            rows = [" | ".join(clean(c.get_text()) for c in tr.find_all(["th", "td"]))
                    for tr in el.find_all("tr")]
            paragraphs.append("\n".join(rows))
        else:
            text = clean(el.get_text())
            if text:
                paragraphs.append(text)
    if paragraphs:
        sections.append((heading, paragraphs))

    footer = soup.find("footer")
    if footer and clean(footer.get_text()):
        sections.append(("Contact", [clean(footer.get_text())]))
    return title, doc_info, sections


def load_pdf(path):
    """Find headings by font: body text is the most common size, headings are
    the most common larger bold size, and anything smaller than body text
    (page footers) is dropped."""
    lines = []
    with pdfplumber.open(path) as pdf:
        meta_title = (pdf.metadata or {}).get("Title", "")
        for page in pdf.pages:
            for line in page.extract_text_lines():
                size = round(line["chars"][0]["size"], 1)
                bold = "bold" in line["chars"][0]["fontname"].lower()
                lines.append((size, bold, line["text"], line["top"], line["bottom"]))
    if not lines:
        return path.stem, "", []

    size_counts = Counter()
    for size, _, text, _, _ in lines:
        size_counts[size] += len(text)
    body_size = size_counts.most_common(1)[0][0]
    bigger_bold = Counter(s for s, b, *_ in lines if b and s > body_size * 1.1)
    heading_size = bigger_bold.most_common(1)[0][0] if bigger_bold else None

    title, preamble, sections = "", [], []
    heading, paragraphs = None, []
    prev_bottom = None
    for size, bold, text, top, bottom in lines:
        if size < body_size * 0.9:
            continue  # page header/footer text
        if heading_size and size > heading_size * 1.02:
            if heading is None:
                title = text  # the last large line before the first heading
            continue
        if heading_size and bold and abs(size - heading_size) < 0.3:
            if heading is not None and paragraphs:
                sections.append((heading, paragraphs))
            heading, paragraphs, prev_bottom = text.strip(), [], None
            continue
        if heading is None:
            preamble.append(text)
            continue
        # a large vertical gap starts a new paragraph; otherwise join wrapped lines
        if paragraphs and prev_bottom is not None and top - prev_bottom < size * 0.8:
            paragraphs[-1] += " " + text
        else:
            paragraphs.append(text)
        prev_bottom = bottom
    if heading is not None and paragraphs:
        sections.append((heading, paragraphs))

    title = clean(title or meta_title or path.stem.replace("-", " ").title())
    if not sections:  # no detectable headings: treat the text as one section
        return title, "", [("Full text", [clean(" ".join(line[2] for line in lines))])]
    # in the preamble, the version/owner line is the last body-size line
    doc_info = clean(preamble[-1]) if preamble else ""
    return title, doc_info, [(h, [clean(p) for p in ps]) for h, ps in sections]


def load_markdown(path):
    title, sections = path.stem.replace("-", " ").title(), []
    heading, paragraphs = "Overview", []
    for block in re.split(r"\n\s*\n", path.read_text(encoding="utf-8")):
        block = block.strip()
        if block.startswith("# "):
            title, block = block[2:].split("\n", 1)[0].strip(), "\n".join(block.split("\n")[1:]).strip()
        elif block.startswith("## "):
            if paragraphs:
                sections.append((heading, paragraphs))
            first, _, rest = block.partition("\n")
            heading, paragraphs, block = first[3:].strip(), [], rest.strip()
        if block:
            paragraphs.append(block)
    if paragraphs:
        sections.append((heading, paragraphs))
    return title, "", sections


def load_text(path):
    paragraphs = [clean(p) for p in re.split(r"\n\s*\n", path.read_text(encoding="utf-8")) if p.strip()]
    return path.stem.replace("-", " ").title(), "", [("Full text", paragraphs)]


LOADERS = {
    ".html": load_html,
    ".htm": load_html,
    ".pdf": load_pdf,
    ".md": load_markdown,
    ".txt": load_text,
}


# ---------------------------------------------------------------------------
# Chunking and the Chroma database
# ---------------------------------------------------------------------------

def split_long(paragraphs):
    """Group paragraphs into pieces of at most MAX_CHUNK_WORDS words."""
    pieces, current, words = [], [], 0
    for p in paragraphs:
        n = len(p.split())
        if current and words + n > MAX_CHUNK_WORDS:
            pieces.append(current)
            current, words = [], 0
        current.append(p)
        words += n
    if current:
        pieces.append(current)
    return pieces


def make_chunks(path, docs_dir):
    title, doc_info, sections = LOADERS[path.suffix.lower()](path)
    source = path.relative_to(docs_dir).as_posix()
    chunks = []
    for heading, paragraphs in sections:
        pieces = split_long(paragraphs)
        for n, piece in enumerate(pieces, 1):
            section = heading if len(pieces) == 1 else f"{heading} (part {n} of {len(pieces)})"
            text = f"Document: {title}\nSection: {section}\n\n" + "\n\n".join(piece)
            chunks.append({
                "id": f"{source}#{len(chunks)}",
                "text": text,
                "metadata": {"source": source, "title": title,
                             "section": section, "doc_info": doc_info},
            })
    return chunks


def document_files(docs_dir):
    files, skipped = [], []
    for path in sorted(docs_dir.rglob("*")):
        if not path.is_file() or path.name.startswith("."):
            continue
        (files if path.suffix.lower() in LOADERS else skipped).append(path)
    return files, skipped


def fingerprint(files, docs_dir):
    """Changes whenever a document is added, removed or edited."""
    h = hashlib.sha256()
    for f in files:
        h.update(f"{f.relative_to(docs_dir)}:{f.stat().st_size}:{f.stat().st_mtime_ns}\n".encode())
    return h.hexdigest()


def db_dir_for(docs_dir):
    """Each document folder gets its own database, e.g. chroma_db/larkfield-docs-1a2b3c4d.
    The hash of the full path keeps two folders with the same name apart."""
    digest = hashlib.sha256(str(docs_dir).encode()).hexdigest()[:8]
    return DB_ROOT / f"{docs_dir.name}-{digest}"


def open_db(docs_dir):
    return chromadb.PersistentClient(path=str(db_dir_for(docs_dir)),
                                     settings=Settings(anonymized_telemetry=False))


def build_index(client, docs_dir):
    files, skipped = document_files(docs_dir)
    for path in skipped:
        print(f"  Skipping {path.name}: unsupported file type {path.suffix or '(none)'}")
    if not files:
        sys.exit(f"No supported documents found in {docs_dir}")

    try:
        client.delete_collection(COLLECTION)
    except Exception:
        pass  # nothing to delete on the first build
    collection = client.create_collection(
        COLLECTION, metadata={"hnsw:space": "cosine",
                              "fingerprint": fingerprint(files, docs_dir)})

    total = 0
    for path in files:
        chunks = make_chunks(path, docs_dir)
        if chunks:
            collection.add(ids=[c["id"] for c in chunks],
                           documents=[c["text"] for c in chunks],
                           metadatas=[c["metadata"] for c in chunks])
        print(f"  {path.relative_to(docs_dir)}: {len(chunks)} chunks")
        total += len(chunks)
    db_dir = db_dir_for(docs_dir).relative_to(HERE)
    print(f"Indexed {total} chunks from {len(files)} documents into {db_dir}/\n")
    return collection


def get_collection(docs_dir, rebuild=False):
    client = open_db(docs_dir)
    if not rebuild:
        try:
            collection = client.get_collection(COLLECTION)
            files, _ = document_files(docs_dir)
            if (collection.metadata or {}).get("fingerprint") == fingerprint(files, docs_dir):
                return collection
            print(f"The documents in {docs_dir.name}/ have changed. Rebuilding the database...")
        except Exception:
            print(f"No database for {docs_dir.name}/ yet. Building it...")
    else:
        print(f"Rebuilding the database for {docs_dir.name}/...")
    return build_index(client, docs_dir)


# ---------------------------------------------------------------------------
# Retrieval and answering
# ---------------------------------------------------------------------------

def retrieve(collection, question):
    result = collection.query(query_texts=[question], n_results=TOP_K)
    return [
        {"text": text, "metadata": meta, "similarity": 1 - dist}
        for text, meta, dist in zip(result["documents"][0],
                                    result["metadatas"][0],
                                    result["distances"][0])
    ]


def format_hits(hits):
    lines = [f"Top {len(hits)} retrieved chunks:"]
    for rank, hit in enumerate(hits, 1):
        preview = hit["text"][:PREVIEW_CHARS].replace("\n", " ")
        lines.append(f"  #{rank}  similarity {hit['similarity']:.3f}  {hit['metadata']['source']}")
        lines.append(f"      {preview}...")
    return "\n".join(lines) + "\n"


def load_api_key():
    """Read the key from API-Key.txt (or API-Key.rtf) next to the repository.
    The key is only held in memory; it is never printed or written anywhere."""
    txt, rtf = KEY_DIR / "API-Key.txt", KEY_DIR / "API-Key.rtf"
    if txt.exists():
        text = txt.read_text(encoding="utf-8")
    elif rtf.exists():
        try:  # RTF markup has to be converted to plain text first (macOS)
            text = subprocess.run(["textutil", "-convert", "txt", "-stdout", str(rtf)],
                                  capture_output=True, text=True, check=True).stdout
        except (OSError, subprocess.CalledProcessError):
            text = rtf.read_text(encoding="utf-8", errors="ignore")
    else:
        sys.exit(f"API key file not found: put your key in {txt}")
    match = re.search(r"sk-ant-[A-Za-z0-9_\-]+", text)
    key = match.group(0) if match else text.strip()
    if not key:
        sys.exit(f"The API key file in {KEY_DIR} is empty.")
    return key


def answer(client, question, hits):
    sources = "\n\n".join(
        f'<source file="{h["metadata"]["source"]}">\n{h["text"]}\n</source>' for h in hits)
    response = client.messages.create(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user",
                   "content": f"<sources>\n{sources}\n</sources>\n\nQuestion: {question}"}],
    )
    if response.stop_reason == "refusal":
        return "Claude declined to answer this question."
    return "".join(b.text for b in response.content if b.type == "text").strip()


def ask(collection, client, question, save_path=None):
    hits = retrieve(collection, question)
    hits_text = format_hits(hits)
    print(hits_text)
    try:
        text = answer(client, question, hits)
    except anthropic.AuthenticationError:
        sys.exit("The API key was rejected. Check the key file.")
    except anthropic.APIConnectionError:
        print("Could not reach the Claude API. Check your internet connection.\n")
        return
    except anthropic.APIStatusError as e:
        print(f"Claude API error {e.status_code}: {e.message}\n")
        return
    output = f"{hits_text}\nANSWER\n{text}\n"
    print(f"ANSWER\n{text}\n")
    if save_path:
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_text(output, encoding="utf-8")
        print(f"(Saved to {save_path})")


def main():
    parser = argparse.ArgumentParser(
        description="Answer questions about a folder of documents.")
    parser.add_argument("question", nargs="*", help="your question (omit for interactive mode)")
    parser.add_argument("--docs", type=Path, default=DEFAULT_DOCS_DIR,
                        help="folder of documents to answer from (default: week-03/copperline-docs)")
    parser.add_argument("--rebuild", action="store_true",
                        help="rebuild the database from the documents folder")
    parser.add_argument("--save", type=Path, metavar="FILE",
                        help="also write the run's output to this text file (UTF-8)")
    args = parser.parse_args()

    docs_dir = args.docs.resolve()
    if not docs_dir.is_dir():
        sys.exit(f"Document folder not found: {args.docs}")
    if args.save and not args.question:
        sys.exit("--save needs a question on the command line.")

    collection = get_collection(docs_dir, rebuild=args.rebuild)
    if args.rebuild and not args.question:
        return

    client = anthropic.Anthropic(api_key=load_api_key())
    if args.question:
        ask(collection, client, " ".join(args.question), args.save)
        return

    print(f"Ask a question about the documents in {docs_dir.name}/ (press Enter to quit).")
    while True:
        try:
            question = input("\nQuestion: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not question:
            break
        print()
        ask(collection, client, question)


if __name__ == "__main__":
    main()
