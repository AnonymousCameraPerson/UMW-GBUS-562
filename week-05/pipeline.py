"""Fixed-order pipeline for one supplier email, for any company folder (--org).

Runs the specialists from specialists.py in this order:
    1. The supply analyst.
    2. A customer notice for each order at risk (skipped if there are none).
    3. A reply to the supplier.
Every draft goes through the review step, is saved in week-05/drafts/<company folder
name> with "pipeline-" in front of its file name, and is printed as the final draft. At the end it prints a summary: drafts saved,
drafts marked for a person, decisions for a person, and total tokens.

Usage (from the top of the repository):
    .venv/bin/python week-05/pipeline.py --email week-05/brookfield-bikes/supplier-emails/01-northgate-controller-delay.eml
    .venv/bin/python week-05/pipeline.py --org week-05/copperline-coffee --email week-05/copperline-coffee/supplier-emails/01-danforth-pump-delay.eml --save week-05/runs/copperline-01-pipeline.txt

--org FOLDER is the company folder: five CSV files, supplier-emails/ and prompts/
(default: week-05/brookfield-bikes).
--save FILE also writes everything printed (including the MCP server's log lines) to FILE
as UTF-8.
"""

import argparse
import codecs
import os
import sys
import threading
import traceback
from contextlib import contextmanager
from pathlib import Path

import specialists as sp


@contextmanager
def tee_to(path):
    """Copy everything written to stdout and stderr (by this process and its child
    processes, such as the MCP server) to the terminal and to a UTF-8 file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    out = open(path, "w", encoding="utf-8")
    sys.stdout.flush()
    sys.stderr.flush()
    saved_out, saved_err = os.dup(1), os.dup(2)
    read_fd, write_fd = os.pipe()
    os.dup2(write_fd, 1)
    os.dup2(write_fd, 2)
    os.close(write_fd)

    def pump():
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        while chunk := os.read(read_fd, 4096):
            os.write(saved_out, chunk)
            out.write(decoder.decode(chunk))
        out.write(decoder.decode(b"", final=True))
        out.flush()

    thread = threading.Thread(target=pump, daemon=True)
    thread.start()
    try:
        yield
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        os.dup2(saved_out, 1)          # closes the last write ends of the pipe,
        os.dup2(saved_err, 2)          # so the pump reaches the end and stops
        thread.join(timeout=10)
        os.close(read_fd)
        os.close(saved_out)
        os.close(saved_err)
        out.close()


def section(title):
    print("\n" + "=" * 70 + f"\n{title}\n" + "=" * 70)
    sys.stdout.flush()


def run(email_path):
    stem = email_path.stem
    saved, problems = [], []

    print_header(email_path)

    section("Step 1: supply analyst")
    brief = sp.run_analyst(email_path)

    section("Step 2: customer notices")
    if not brief["orders_at_risk"]:
        print("No orders at risk, so no customer notices.")
    for risk in brief["orders_at_risk"]:
        try:
            order = sp.customer_order_facts(risk["order_id"], brief)
        except ValueError as exc:
            problems.append(f"No notice for {risk['order_id']}: {exc}")
            print(problems[-1])
            continue
        saved.append(sp.reviewed_customer_notice(order, name=f"{stem}-customer-{risk['order_id']}"))

    section("Step 3: supplier reply")
    saved.append(sp.reviewed_supplier_reply(email_path, brief, name=f"{stem}-supplier-reply"))

    print_summary(saved, brief, problems)


def print_header(email_path):
    print(f"Org:    {sp.shown(sp.DATA_DIR)}")
    print(f"Email:  {email_path}")
    print(f"Model:  {sp.MODEL}")
    print(f"Drafts: {sp.shown(sp.DRAFTS)}")
    print("Columns found:\n" + sp.describe_columns(sp.DATA_DIR))


def print_summary(saved, brief, problems):
    """The end-of-run summary (orchestrator.py prints the same one)."""
    section("Summary")
    print(f"Drafts saved: {len(saved)}")
    for d in saved:
        print(f"  {Path(d['path']).relative_to(sp.REPO)}")
    marked = [d for d in saved if d["needs_person"]]
    print(f"\nDrafts marked for a person: {len(marked)}")
    for d in marked:
        print(f"  {Path(d['path']).name}: {'; '.join(d['reasons'])}")
    decisions = brief["decisions_for_a_person"] if brief else []
    print(f"\nDecisions for a person: {len(decisions)}"
          + ("" if brief else " (the supply analyst did not run)"))
    for item in decisions:
        print(f"  - {item}")
    if problems:
        print(f"\nProblems: {len(problems)}")
        for p in problems:
            print(f"  - {p}")
    sp.token_report()
    total_in = sum(t["input"] for t in sp.TOKENS.values())
    total_out = sum(t["output"] for t in sp.TOKENS.values())
    print(f"\nTotal tokens: {total_in + total_out:,} ({total_in:,} input, {total_out:,} output)")


def main():
    cli(run, "Run the specialists in a fixed order on one supplier email.", "pipeline-")


def cli(run, description, prefix):
    """--email and --save handling, shared with orchestrator.py.

    prefix goes in front of every draft file name (e.g. "pipeline-").
    """
    sp.DRAFT_PREFIX = prefix
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--email", type=Path, required=True,
                        help="path of one supplier email (.eml)")
    parser.add_argument("--save", type=Path, metavar="FILE",
                        help="also write everything printed to this UTF-8 text file")
    parser.add_argument("--org", type=Path, metavar="FOLDER",
                        help="company folder with the five CSV files, supplier-emails/ "
                             "and prompts/ (default: week-05/brookfield-bikes)")
    args = parser.parse_args()
    if not args.email.is_file():
        parser.error(f"--email {args.email} is not a file")
    if args.org and not args.org.is_dir():
        parser.error(f"--org {args.org} is not a folder")
    try:
        sp.set_org(args.org)
    except sp.ColumnError as exc:
        parser.error(f"--org {args.org}: {exc}")

    if args.save:
        with tee_to(args.save):
            try:
                run(args.email)
            except Exception:
                traceback.print_exc()      # inside the tee, so the error is in the file too
                failed = True
            else:
                failed = False
        print(f"\nSaved this run's output to {args.save}")
        if failed:
            sys.exit(1)
    else:
        run(args.email)


if __name__ == "__main__":
    main()
