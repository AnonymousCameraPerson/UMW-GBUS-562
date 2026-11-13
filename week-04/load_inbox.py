"""Load the sample HR emails into a Gmail sandbox inbox as unread messages.

Usage (from the repository root):
    .venv/bin/python week-04/load_inbox.py

What it does:
  1. Reads the sandbox login (address and app password) from Agent-Email (a TextEdit
     file, Agent-Email.rtf) in the folder above the repository. The file is read only when this runs; the
     password is never printed, logged, or written anywhere.
  2. Signs in to Gmail over IMAP (imap.gmail.com, SSL).
  3. For each .eml file in week-04/hartwell-hr/sample-emails, sets its Date to
     now, and APPENDs it to INBOX without the \\Seen flag, so it shows as unread.

Nothing is sent. IMAP APPEND places the message straight into the mailbox; no
SMTP is used. Running it twice adds a second copy of each message.

Gmail requires IMAP access and an app password (not the normal account password).
"""

import imaplib
import re
import subprocess
import sys
import time
from email import policy
from email.parser import BytesParser
from email.utils import formatdate, make_msgid
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
LOGIN_FILE = REPO.parent / "Agent-Email.rtf"
EMAILS_DIR = HERE / "hartwell-hr" / "sample-emails"

DEFAULT_HOST = "imap.gmail.com"
MAILBOX = "INBOX"

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# Labels are compared after lowercasing and turning "_" / "-" into spaces,
# so EMAIL_ADDRESS matches "email address" and APP_PASSWORD matches "app password".
USER_LABELS = {"email address", "email", "e mail", "user", "username", "login",
               "address", "account", "gmail"}
PASSWORD_LABELS = {"app password", "password", "pass", "passwd", "apppassword", "pw"}
HOST_LABELS = {"host", "server", "imap", "imap host", "imap server"}


def load_text(path):
    """Return the file's plain text. TextEdit saves .rtf files, so convert those
    with macOS textutil instead of reading the RTF markup directly."""
    if path.suffix.lower() in {".rtf", ".rtfd"}:
        try:
            result = subprocess.run(
                ["textutil", "-convert", "txt", "-stdout", str(path)],
                capture_output=True, check=True,
            )
        except (OSError, subprocess.CalledProcessError):
            sys.exit(f"Could not convert {path.name} to plain text with textutil.")
        return result.stdout.decode("utf-8-sig")
    return path.read_text(encoding="utf-8-sig")


def read_login(path):
    """Return (user, password, host) from the login file.

    Expected layout:
        EMAIL_ADDRESS=your.sandbox.address@gmail.com
        APP_PASSWORD=abcdefghijklmnop
    Also accepts other labeled lines ("Email: ...", "Password = ..."),
    two plain lines (address, then password), or one "address password" /
    "address:password" line. Error messages never include the password.
    """
    if not path.is_file():
        sys.exit(f"Login file not found: {path}")
    text = load_text(path)

    user = password = None
    host = DEFAULT_HOST
    unlabeled = []

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^([^:=]+?)\s*[:=]\s*(.+)$", line)
        if match:
            # "EMAIL_ADDRESS", "App-Password" -> "email address", "app password"
            label = re.sub(r"[_\-\s]+", " ", match.group(1).strip().lower())
            value = match.group(2).strip().strip('"').strip("'")
            if label in USER_LABELS:
                user = value
                continue
            if label in PASSWORD_LABELS:
                password = value
                continue
            if label in HOST_LABELS:
                host = value
                continue
            if EMAIL_RE.match(match.group(1).strip()):  # "address:password"
                user, password = match.group(1).strip(), value
                continue
        unlabeled.append(line)

    for line in unlabeled:
        parts = line.split(None, 1)
        if user is None and EMAIL_RE.match(parts[0]):
            user = parts[0]
            if password is None and len(parts) == 2:
                password = parts[1].strip()
        elif password is None and not EMAIL_RE.match(line):
            password = line

    if not user:
        sys.exit(f"Could not find an email address in {path.name}.")
    if not password:
        sys.exit(f"Could not find a password in {path.name}.")
    # Google shows app passwords as "abcd efgh ijkl mnop"; the spaces (including
    # non-breaking ones TextEdit may insert) are not part of the password.
    password = re.sub(r"\s+", "", password.replace("\u00a0", " "))
    user = user.strip().strip("\u201c\u201d\u2018\u2019")
    return user, password, host


def prepare_message(path):
    """Return the email's bytes with today's Date (and a Message-ID if missing)."""
    msg = BytesParser(policy=policy.SMTP).parse(path.open("rb"))
    del msg["Date"]
    msg["Date"] = formatdate(localtime=True)
    if msg["Message-ID"] is None:
        msg["Message-ID"] = make_msgid(domain="hartwellfoods.example")
    return msg.as_bytes(), msg["Subject"] or "(no subject)"


def main():
    files = sorted(EMAILS_DIR.glob("*.eml"))
    if not files:
        sys.exit(f"No .eml files found in {EMAILS_DIR}")

    user, password, host = read_login(LOGIN_FILE)

    print(f"Signing in to {host} as {user} ...")
    try:
        imap = imaplib.IMAP4_SSL(host)
        imap.login(user, password)
    except imaplib.IMAP4.error as exc:
        # Gmail's reply never contains the password, so it is safe to show.
        reason = exc.args[0] if exc.args else exc
        if isinstance(reason, bytes):
            reason = reason.decode(errors="replace")
        hint = ""
        if not re.fullmatch(r"[A-Za-z]{16}", password):
            hint = (f"\n  Note: the password read from the file is {len(password)} characters; "
                    "a Gmail app password is 16 letters.")
        sys.exit(f"Sign-in failed. Gmail said: {reason}{hint}\n"
                 "  Check the address and app password in Agent-Email, "
                 "and that IMAP is enabled for the account.")
    except OSError as exc:
        sys.exit(f"Could not connect to {host}: {exc.__class__.__name__}")
    password = None

    try:
        loaded = 0
        for path in files:
            data, subject = prepare_message(path)
            # Empty flags = no \Seen, so the message arrives unread.
            status, _ = imap.append(MAILBOX, "", imaplib.Time2Internaldate(time.time()), data)
            if status == "OK":
                loaded += 1
                print(f"  added  {path.name}  ({subject})")
            else:
                print(f"  FAILED {path.name}  (server said {status})")
        print(f"Done: {loaded} of {len(files)} messages placed in {MAILBOX} as unread.")
    finally:
        try:
            imap.logout()
        except imaplib.IMAP4.error:
            pass


if __name__ == "__main__":
    main()
