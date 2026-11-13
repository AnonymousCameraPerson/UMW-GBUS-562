"""Command-line HR agent: drafts replies to employee emails with Claude's tool use.

Usage (from the top of the repository):
    .venv/bin/python week-04/agent.py --email week-04/hartwell-hr/sample-emails/03-megan-time-off.eml
    .venv/bin/python week-04/agent.py --inbox
    .venv/bin/python week-04/agent.py --inbox --save week-04/inbox-run.txt

--email FILE  answers one .eml file; save_reply writes a text draft to week-04/drafts/.
--inbox       signs in to the Gmail sandbox over IMAP (login from Agent-Email in the
              folder above the repository, read only at run time; the password is
              never printed or saved). Each unread INBOX email is marked as read and
              then handled as its own separate agent run. save_reply creates a real
              Gmail draft replying to the sender in the same thread; if
              needs_human_review is true the draft gets the Gmail label
              NEEDS HR REVIEW (created if missing), which shows in Gmail's lists.
              This version only reads mail and creates drafts. It cannot send email.
--save FILE   also writes everything printed to FILE (UTF-8).

What it does, for one email:
  1. Reads the sender's address from the email's From header.
  2. Starts week-04/hr_mcp_server.py --as-employee <sender>, so the leave balance
     tool can only ever return the sender's own balance.
  3. Offers Claude the server's tools plus one local tool, save_reply, which saves
     a draft (a file, or a Gmail draft with --inbox). Nothing is ever sent.
  4. Stops after 10 tool calls, or as soon as Claude replies without a tool request.

The system prompt is read fresh from week-04/hartwell-hr/system_prompt.txt on every run.
The API key is read from API-Key.txt in the folder above the repository (falling back
to API-Key.rtf there) and is only held in memory: it is never written to a file or printed.
"""

import argparse
import asyncio
import imaplib
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import formataddr, formatdate, make_msgid, parseaddr
from pathlib import Path

import anthropic
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from load_inbox import LOGIN_FILE, read_login   # same Agent-Email login as load_inbox.py

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
KEY_DIR = REPO.parent                     # the folder above the repository
SERVER = HERE / "hr_mcp_server.py"
SYSTEM_PROMPT_FILE = HERE / "hartwell-hr" / "system_prompt.txt"
DRAFTS_DIR = HERE / "drafts"
REVIEW_LABEL = "NEEDS HR REVIEW"     # Gmail label put on drafts that need HR review

MODEL = "claude-sonnet-5"
MAX_TOKENS = 16000
MAX_TOOL_CALLS = 10
PREVIEW_CHARS = 300

SAVE_REPLY_TOOL = {
    "name": "save_reply",
    "description": (
        "Save the finished reply to the employee as a draft for the HR team to review. "
        "This only saves the draft; it never sends anything. Call it exactly once, "
        "with the full reply text."),
    "input_schema": {
        "type": "object",
        "properties": {
            "reply_text": {
                "type": "string",
                "description": "The full text of the reply email to the employee.",
            },
            "needs_human_review": {
                "type": "boolean",
                "description": ("true if a person from HR must handle or check this before "
                                "anything is sent (sensitive topics, policies that don't "
                                "answer the question, missing records); otherwise false."),
            },
        },
        "required": ["reply_text", "needs_human_review"],
        "additionalProperties": False,
    },
    "strict": True,
}


# ---------------------------------------------------------------------------
# Inputs: API key, system prompt, email
# ---------------------------------------------------------------------------

def load_api_key():
    """Return the key from API-Key.txt (or API-Key.rtf) above the repo. Never print it."""
    txt, rtf = KEY_DIR / "API-Key.txt", KEY_DIR / "API-Key.rtf"
    if txt.exists():
        text = txt.read_text(encoding="utf-8")
    elif rtf.exists():
        # RTF markup has to be stripped; macOS textutil does that.
        text = subprocess.run(["textutil", "-convert", "txt", "-stdout", str(rtf)],
                              capture_output=True, text=True, check=True).stdout
    else:
        sys.exit(f"No API key found: expected {txt} (or {rtf.name} in the same folder).")
    match = re.search(r"sk-ant-[A-Za-z0-9_\-]+", text)
    if not match:
        sys.exit(f"No Anthropic API key (sk-ant-...) found in the key file in {KEY_DIR}.")
    return match.group(0)


class EmailError(Exception):
    """An email that can't be handled (for example, no sender address)."""


def read_email(raw, label):
    """Parse raw email bytes. Return (sender address, sender name, subject, text, message)."""
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    name, address = parseaddr(msg.get("From", ""))
    address = address.strip().lower()
    if not address or "@" not in address:
        raise EmailError(f"Couldn't read a sender address from the From header of {label}.")
    body_part = msg.get_body(preferencelist=("plain",))
    body = body_part.get_content() if body_part else ""
    if not body:
        html_part = msg.get_body(preferencelist=("html",))
        if html_part:   # rough HTML-to-text, enough for Claude to read the request
            body = re.sub(r"<[^>]+>", " ", html_part.get_content())
    headers = "\n".join(f"{h}: {msg[h]}" for h in ("From", "To", "Subject", "Date") if msg[h])
    return address, name, str(msg.get("Subject", "")), f"{headers}\n\n{body.strip()}\n", msg


# ---------------------------------------------------------------------------
# The local tool
# ---------------------------------------------------------------------------

def reply_subject(subject):
    subject = subject.strip()
    return subject if subject.lower().startswith("re:") else f"Re: {subject}"


def file_saver(email_path):
    """save_reply for --email: write the draft to week-04/drafts/. Saves only, never sends."""
    def save(sender, sender_name, subject, original, reply_text, needs_human_review):
        DRAFTS_DIR.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = DRAFTS_DIR / f"{stamp}-{email_path.stem}.txt"
        text = (f"DRAFT - NOT SENT\n"
                f"To: {sender}\n"
                f"Subject: {reply_subject(subject)}\n"
                f"In reply to: {email_path.name}\n"
                f"Needs human review: {'YES' if needs_human_review else 'no'}\n"
                f"{'-' * 60}\n"
                f"{reply_text.strip()}\n")
        path.write_text(text, encoding="utf-8")
        where = path.relative_to(REPO)
        return f"Draft saved to {where}. It has not been sent.", f"Draft ({where}):\n\n{text}"
    return save


# ---------------------------------------------------------------------------
# Gmail over IMAP (--inbox): read unread mail, create drafts. No sending.
# ---------------------------------------------------------------------------

def imap_sign_in():
    """Sign in with the Agent-Email login. Returns (connection, sandbox address)."""
    user, password, host = read_login(LOGIN_FILE)
    print(f"Signing in to {host} as {user} ...")
    try:
        imap = imaplib.IMAP4_SSL(host)
        imap.login(user, password)
    except imaplib.IMAP4.error as exc:
        reason = exc.args[0] if exc.args else exc
        if isinstance(reason, bytes):
            reason = reason.decode(errors="replace")
        sys.exit(f"Sign-in failed. Gmail said: {reason}")
    except OSError as exc:
        sys.exit(f"Could not connect to {host}: {exc.__class__.__name__}")
    finally:
        password = None
    return imap, user


def find_drafts_mailbox(imap):
    """Return the Drafts folder name (Gmail marks it with the \\Drafts attribute)."""
    status, lines = imap.list()
    if status == "OK":
        for line in lines or []:
            text = line.decode(errors="replace") if isinstance(line, bytes) else str(line)
            if "\\Drafts" in text:
                match = re.search(r'"([^"]+)"\s*$', text) or re.search(r"(\S+)\s*$", text)
                if match:
                    return match.group(1)
    return "[Gmail]/Drafts"


def gmail_saver(imap, drafts_box, own_address):
    """save_reply for --inbox: create a Gmail draft in the sender's thread. Never sends."""
    def save(sender, sender_name, subject, original, reply_text, needs_human_review):
        reply = EmailMessage()
        reply["From"] = own_address
        reply["To"] = formataddr((sender_name, sender))
        reply["Subject"] = reply_subject(subject)
        reply["Date"] = formatdate(localtime=True)
        reply["Message-ID"] = make_msgid(domain=own_address.split("@")[-1])
        original_id = original.get("Message-ID")
        if original_id:   # these two headers put the draft in the same thread
            reply["In-Reply-To"] = original_id
            reply["References"] = f"{original.get('References', '')} {original_id}".strip()
        reply.set_content(reply_text.strip() + "\n")
        data = reply.as_bytes(policy=policy.SMTP)

        status, detail = imap.append(f'"{drafts_box}"', "(\\Draft)",
                                     imaplib.Time2Internaldate(time.time()), data)
        if status != "OK":
            raise RuntimeError(f"Gmail did not accept the draft: {detail}")

        label_note = ""
        if needs_human_review:
            try:
                add_review_label(imap, drafts_box, detail)
                label_note = f" It has the Gmail label {REVIEW_LABEL}."
            except (RuntimeError, imaplib.IMAP4.error) as exc:
                label_note = f" WARNING: the draft was saved, but labelling it failed ({exc})."

        shown = (f"Gmail draft (in {drafts_box}, NOT SENT):\n\n"
                 f"To: {reply['To']}\nSubject: {reply['Subject']}\n"
                 f"Label: {REVIEW_LABEL if needs_human_review else '(none)'}\n"
                 f"In-Reply-To: {reply.get('In-Reply-To', '(none: original had no Message-ID)')}\n"
                 f"{'-' * 60}\n{reply_text.strip()}\n")
        return (f"Gmail draft created in {drafts_box}, replying to {sender}. "
                f"It has not been sent.{label_note}"), shown
    return save


def add_review_label(imap, drafts_box, append_detail):
    """Put the NEEDS HR REVIEW label on the draft just appended to drafts_box."""
    text = b" ".join(d for d in append_detail if isinstance(d, bytes)).decode(errors="replace")
    match = re.search(r"APPENDUID \d+ (\d+)", text)   # Gmail reports the new draft's UID
    if not match:
        raise RuntimeError("Gmail didn't report the new draft's UID")
    imap.create(f'"{REVIEW_LABEL}"')        # creates the label; harmless if it already exists
    status, _ = imap.select(f'"{drafts_box}"')
    try:
        if status != "OK":
            raise RuntimeError(f"couldn't open {drafts_box}")
        status, _ = imap.uid("STORE", match.group(1), "+X-GM-LABELS", f'("{REVIEW_LABEL}")')
        if status != "OK":
            raise RuntimeError("Gmail refused the label")
    finally:
        imap.select("INBOX")                # the inbox loop's UIDs belong to INBOX


def fetch_unread(imap):
    """Return the UIDs of unread INBOX messages, oldest first."""
    status, _ = imap.select("INBOX")          # read-write, so flags can be changed
    if status != "OK":
        sys.exit("Could not open INBOX.")
    status, data = imap.uid("SEARCH", None, "UNSEEN")
    if status != "OK":
        sys.exit("Could not search INBOX for unread messages.")
    return data[0].split() if data and data[0] else []


def mark_read_and_fetch(imap, uid):
    """Mark one message as read, then return its raw bytes (None if it's gone)."""
    status, _ = imap.uid("STORE", uid, "+FLAGS", "(\\Seen)")
    if status != "OK":
        raise RuntimeError("Could not mark the message as read; skipping it.")
    status, data = imap.uid("FETCH", uid, "(BODY.PEEK[])")
    if status != "OK":
        raise RuntimeError("Could not download the message.")
    for item in data or []:
        if isinstance(item, tuple) and len(item) == 2:
            return item[1]
    return None


# ---------------------------------------------------------------------------
# The agent loop
# ---------------------------------------------------------------------------

def mcp_result_text(result):
    """Flatten an MCP tool result into text for Claude."""
    parts = [c.text for c in (result.content or []) if getattr(c, "type", "") == "text"]
    if parts:
        return "\n".join(parts)
    if result.structured_content is not None:
        return json.dumps(result.structured_content)
    return "(the tool returned no content)"


def preview(text):
    text = text.replace("\n", " ")
    return text if len(text) <= PREVIEW_CHARS else text[:PREVIEW_CHARS] + "..."


async def run(raw_email, label, saver, api_key):
    """One complete, separate agent run for one email."""
    sender, sender_name, subject, email_text, original = read_email(raw_email, label)
    system_prompt = SYSTEM_PROMPT_FILE.read_text(encoding="utf-8")
    client = anthropic.AsyncAnthropic(api_key=api_key)

    print(f"Email:  {label}")
    print(f"Subject: {subject}")
    print(f"Sender: {sender_name + ' ' if sender_name else ''}<{sender}>")
    print(f"Model:  {MODEL}\n")

    server = StdioServerParameters(
        command=sys.executable,
        args=[str(SERVER), "--as-employee", sender],
        cwd=str(REPO))

    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            mcp_tools = (await session.list_tools()).tools
            tools = [{"name": t.name, "description": t.description or "",
                      "input_schema": t.input_schema} for t in mcp_tools]
            tools.append(SAVE_REPLY_TOOL)
            mcp_names = {t.name for t in mcp_tools}
            print("Tools:  " + ", ".join(t["name"] for t in tools) + "\n")

            messages = [{
                "role": "user",
                "content": (
                    f"Prepare a reply to this employee email. The sender's address, taken "
                    f"from the From header, is {sender}.\n\n"
                    f"<email>\n{email_text}</email>"),
            }]

            tool_calls = 0
            input_tokens = output_tokens = 0
            draft_shown = None
            stop_reason = None

            while stop_reason is None:
                response = await client.messages.create(
                    model=MODEL, max_tokens=MAX_TOKENS, system=system_prompt,
                    tools=tools, messages=messages)
                input_tokens += response.usage.input_tokens
                output_tokens += response.usage.output_tokens
                messages.append({"role": "assistant", "content": response.content})

                for block in response.content:
                    if block.type == "text" and block.text.strip():
                        print(f"Claude: {block.text.strip()}\n")

                if response.stop_reason != "tool_use":
                    stop_reason = (f"Claude replied without a tool request "
                                   f"(stop reason: {response.stop_reason}).")
                    break

                results = []
                for block in (b for b in response.content if b.type == "tool_use"):
                    if tool_calls >= MAX_TOOL_CALLS:
                        stop_reason = f"Reached the limit of {MAX_TOOL_CALLS} tool calls."
                        break
                    tool_calls += 1
                    print(f"Step {tool_calls}: {block.name}")
                    print(f"  input:  {json.dumps(block.input)}")

                    is_error = False
                    if block.name == "save_reply":
                        if draft_shown is not None:
                            text, is_error = "A draft was already saved. save_reply can be called only once.", True
                        else:
                            try:
                                text, draft_shown = saver(
                                    sender, sender_name, subject, original,
                                    block.input["reply_text"],
                                    bool(block.input["needs_human_review"]))
                            except (RuntimeError, OSError, imaplib.IMAP4.error) as exc:
                                text, is_error = f"Saving the draft failed: {exc}", True
                    elif block.name in mcp_names:
                        result = await session.call_tool(block.name, block.input)
                        text = mcp_result_text(result)
                        is_error = bool(getattr(result, "is_error", False))
                    else:
                        text, is_error = f"Unknown tool: {block.name}", True

                    print(f"  result{' (error)' if is_error else ''}: {preview(text)}\n")
                    results.append({"type": "tool_result", "tool_use_id": block.id,
                                    "content": text, "is_error": is_error})

                if stop_reason is None:
                    messages.append({"role": "user", "content": results})

    print("=" * 60)
    print(f"Stopped: {stop_reason}")
    print(f"Tool calls: {tool_calls}")
    print("=" * 60)
    if draft_shown:
        print(draft_shown)
    else:
        print("No draft was saved (Claude never called save_reply).\n")
    print(f"Tokens: {input_tokens:,} input, {output_tokens:,} output")


async def run_inbox(api_key):
    """Handle each unread INBOX email as its own agent run. Reads and drafts only."""
    imap, own_address = imap_sign_in()
    try:
        drafts_box = find_drafts_mailbox(imap)
        uids = fetch_unread(imap)
        print(f"Unread emails in INBOX: {len(uids)}  (drafts go to {drafts_box})\n")
        saver = gmail_saver(imap, drafts_box, own_address)
        done = 0
        for number, uid in enumerate(uids, 1):
            print("#" * 60)
            print(f"# Email {number} of {len(uids)}  (IMAP UID {uid.decode()})")
            print("#" * 60)
            try:
                # Marked as read first, so it is never handled twice, even if this run fails.
                raw = mark_read_and_fetch(imap, uid)
                if raw is None:
                    print("The message disappeared before it could be read; skipping.\n")
                    continue
                await run(raw, f"INBOX message (UID {uid.decode()})", saver, api_key)
                done += 1
            except (EmailError, RuntimeError, imaplib.IMAP4.error, anthropic.APIError) as exc:
                print(f"Skipped this email: {exc}")
            print()
        print(f"Finished: {done} of {len(uids)} unread emails handled. Nothing was sent.")
    finally:
        try:
            imap.logout()
        except (imaplib.IMAP4.error, OSError):
            pass


class Tee:
    """Write to the terminal and to the --save file at the same time."""
    def __init__(self, stream, log):
        self.stream, self.log = stream, log

    def write(self, text):
        self.stream.write(text)
        self.log.write(text)
        return len(text)

    def flush(self):
        self.stream.flush()
        self.log.flush()

    def __getattr__(self, name):
        return getattr(self.stream, name)


def main():
    parser = argparse.ArgumentParser(
        description="Draft replies to Hartwell Foods HR emails with Claude and the HR MCP server. "
                    "Never sends email.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--email", type=Path,
                        help="path of one email file (.eml) to answer; the draft is a text file")
    source.add_argument("--inbox", action="store_true",
                        help="answer every unread email in the Gmail sandbox INBOX, "
                             "creating Gmail drafts")
    parser.add_argument("--save", type=Path, metavar="FILE",
                        help="also write everything printed to FILE (UTF-8)")
    args = parser.parse_args()
    if args.email and not args.email.is_file():
        parser.error(f"no such email file: {args.email}")

    log = None
    if args.save:
        args.save.parent.mkdir(parents=True, exist_ok=True)
        log = args.save.open("w", encoding="utf-8")
        sys.stdout = Tee(sys.__stdout__, log)
        sys.stderr = Tee(sys.__stderr__, log)
    try:
        api_key = load_api_key()
        if args.inbox:
            asyncio.run(run_inbox(api_key))
        else:
            path = args.email.resolve()
            try:
                asyncio.run(run(path.read_bytes(), str(path), file_saver(path), api_key))
            except EmailError as exc:
                sys.exit(str(exc))
    except SystemExit as exc:
        if isinstance(exc.code, str):      # print exit messages so --save captures them too
            print(exc.code, file=sys.stderr)
            raise SystemExit(1)
        raise
    finally:
        if log:
            sys.stdout, sys.stderr = sys.__stdout__, sys.__stderr__
            log.close()
            print(f"Output saved to {args.save}")


if __name__ == "__main__":
    main()
