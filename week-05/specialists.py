"""Specialists shared by Brookfield Bicycle Company's coordinators.

Four specialists, each a Claude call (model claude-sonnet-5) whose system prompt is read
fresh from week-05/brookfield-bikes/prompts on every run:

    run_analyst(email)                  agent with the tools of supply_mcp_server.py; stops
                                        after 10 tool calls; returns its brief (a dict)
    write_supplier_reply(email, brief)  one call; customer names are removed first
    write_customer_notice(order)        one call; given only that one order's facts
    review(draft, facts, recipient)     one call; returns {"verdict": "APPROVE"|"REVISE", ...}

The review step (reviewed_supplier_reply, reviewed_customer_notice) runs a writer, then
the reviewer, and revises at most 2 times. It then checks in code that no customer name
is in a supplier draft and no supplier name is in a customer draft, and saves the draft in
week-05/drafts. A draft is marked for a person if it fails either check, if its order has
a late penalty (for a supplier reply: any order at risk has one), or if the reviewer never
approved it.

Every handoff between specialists is printed, and so are the tokens each specialist used
(token_report() prints the totals).

The API key is read from API-Key.txt in the folder above the repository (falling back to
API-Key.rtf there). It is only held in memory: never written to a file or printed.

Quick test, from the top of the repository (this calls Claude and saves drafts):
    .venv/bin/python week-05/specialists.py week-05/brookfield-bikes/supplier-emails/01-northgate-controller-delay.eml
"""

import asyncio
import csv
import json
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import anthropic
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
KEY_DIR = REPO.parent                      # the folder above the repository
DATA_DIR = HERE / "brookfield-bikes"
PROMPTS = DATA_DIR / "prompts"
DRAFTS = HERE / "drafts"
SERVER = HERE / "supply_mcp_server.py"

MODEL = "claude-sonnet-5"
MAX_TOKENS = 16000
MAX_TOOL_CALLS = 10
MAX_REVISIONS = 2

PROMPT_FILES = {
    "analyst": "analyst.txt",
    "supplier_writer": "supplier-writer.txt",
    "customer_writer": "customer-writer.txt",
    "reviewer": "reviewer.txt",
}

# The only order facts a customer notice is written from. Day numbers (days from today)
# are left out so notices don't tell customers "day 26".
CUSTOMER_FACT_FIELDS = ("order_id", "customer", "model", "bikes", "days_late")

BRIEF_SCHEMA = {
    "type": "object",
    "properties": {
        "part_id": {"type": "string"},
        "what_changed": {"type": "string"},
        "orders_at_risk": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string"},
                    "customer": {"type": "string"},
                    "units_short": {"type": "integer"},
                    "ship_day": {"type": "integer"},
                    "ready_day": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
                    "late_penalty": {"type": "boolean"},
                },
                "required": ["order_id", "customer", "units_short", "ship_day",
                             "ready_day", "late_penalty"],
                "additionalProperties": False,
            },
        },
        "options": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "order_ids_at_risk": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["name", "order_ids_at_risk"],
                "additionalProperties": False,
            },
        },
        "decisions_for_a_person": {"type": "array", "items": {"type": "string"}},
        "supplier_questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["part_id", "what_changed", "orders_at_risk", "options",
                 "decisions_for_a_person", "supplier_questions"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# Inputs: API key, prompts, company data
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


_api_key = None


def api_key():
    global _api_key
    if _api_key is None:
        _api_key = load_api_key()
    return _api_key


def prompt(name):
    """Read a specialist's system prompt fresh, so edits apply on the next run."""
    return (PROMPTS / PROMPT_FILES[name]).read_text(encoding="utf-8").strip()


def read_csv(name):
    with (DATA_DIR / name).open(newline="", encoding="utf-8-sig") as f:
        return [{k.strip(): (v or "").strip() for k, v in row.items() if k}
                for row in csv.DictReader(f)]


def customer_terms():
    """Customer names and contact emails, from orders.csv."""
    rows = read_csv("orders.csv")
    return sorted({r["customer"] for r in rows} | {r["contact_email"] for r in rows},
                  key=len, reverse=True)


def supplier_terms():
    """Supplier names, the first word of each (e.g. 'Northgate'), contact names and emails."""
    terms = set()
    for r in read_csv("suppliers.csv"):
        terms |= {r["supplier"], r["supplier"].split()[0], r["contact_name"],
                  r["contact_email"]}
    for r in read_csv("parts.csv") + read_csv("inbound.csv"):
        terms |= {r["supplier"], r["supplier"].split()[0]}
    return sorted(t for t in terms if t)


def term_pattern(term):
    return re.compile(r"(?<!\w)" + re.escape(term) + r"(?!\w)", re.IGNORECASE)


def find_terms(text, terms):
    return [t for t in terms if term_pattern(t).search(text)]


def read_email(email):
    """Accept the email's text, or a path to an .eml file."""
    if isinstance(email, Path) or (isinstance(email, str) and "\n" not in email
                                   and email.endswith(".eml") and Path(email).is_file()):
        return Path(email).read_text(encoding="utf-8")
    return email


# ---------------------------------------------------------------------------
# Printing handoffs and token use
# ---------------------------------------------------------------------------

TOKENS = defaultdict(lambda: {"calls": 0, "input": 0, "output": 0})


def handoff(sender, receiver, what, payload=None):
    print(f"\n>>> HANDOFF  {sender} -> {receiver}: {what}")
    if payload is not None:
        text = payload if isinstance(payload, str) else json.dumps(payload, indent=2)
        print("    " + text.replace("\n", "\n    "))
    sys.stdout.flush()


def count_tokens(specialist, response):
    t = TOKENS[specialist]
    t["calls"] += 1
    t["input"] += response.usage.input_tokens
    t["output"] += response.usage.output_tokens
    print(f"    [tokens] {specialist}: {response.usage.input_tokens:,} in, "
          f"{response.usage.output_tokens:,} out  "
          f"(so far: {t['input']:,} in, {t['output']:,} out, {t['calls']} calls)")


def token_report():
    print("\nTokens used by each specialist")
    print(f"  {'specialist':<18}{'calls':>6}{'input':>10}{'output':>10}")
    for name, t in TOKENS.items():
        print(f"  {name:<18}{t['calls']:>6}{t['input']:>10,}{t['output']:>10,}")
    print(f"  {'total':<18}{sum(t['calls'] for t in TOKENS.values()):>6}"
          f"{sum(t['input'] for t in TOKENS.values()):>10,}"
          f"{sum(t['output'] for t in TOKENS.values()):>10,}")


def check_stop(response, specialist):
    if response.stop_reason == "refusal":
        raise RuntimeError(f"{specialist}: Claude declined the request (refusal).")
    if response.stop_reason == "max_tokens":
        raise RuntimeError(f"{specialist}: the answer was cut off at {MAX_TOKENS} tokens.")


def text_of(response):
    return "\n".join(b.text for b in response.content if b.type == "text").strip()


def one_call(specialist, system, user_text):
    """A single model call for a writer or the reviewer."""
    client = anthropic.Anthropic(api_key=api_key())
    response = client.messages.create(
        model=MODEL, max_tokens=MAX_TOKENS, system=system,
        messages=[{"role": "user", "content": user_text}])
    count_tokens(specialist, response)
    check_stop(response, specialist)
    return text_of(response)


# ---------------------------------------------------------------------------
# Specialist 1: the supply analyst (an agent with the MCP server's tools)
# ---------------------------------------------------------------------------

def mcp_result_text(result):
    parts = [c.text for c in (result.content or []) if getattr(c, "type", "") == "text"]
    if parts:
        return "\n".join(parts)
    if result.structured_content is not None:
        return json.dumps(result.structured_content)
    return "(the tool returned no content)"


async def run_analyst_async(email):
    """The analyst agent. Returns its brief as a dict."""
    email = read_email(email)
    handoff("coordinator", "analyst", "supplier email", email)
    system = prompt("analyst")
    client = anthropic.AsyncAnthropic(api_key=api_key())
    server = StdioServerParameters(command=sys.executable, args=[str(SERVER)], cwd=str(REPO))

    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            mcp_tools = (await session.list_tools()).tools
            tools = [{"name": t.name, "description": t.description or "",
                      "input_schema": t.input_schema} for t in mcp_tools]
            messages = [{"role": "user", "content": f"<email>\n{email}\n</email>"}]
            tool_calls = 0
            limit_hit = False

            while True:
                response = await client.messages.create(
                    model=MODEL, max_tokens=MAX_TOKENS, system=system, tools=tools,
                    # After the limit, tools are off and Claude must write the brief.
                    tool_choice={"type": "none"} if limit_hit else {"type": "auto"},
                    output_config={"format": {"type": "json_schema", "schema": BRIEF_SCHEMA}},
                    messages=messages)
                count_tokens("analyst", response)
                check_stop(response, "analyst")
                messages.append({"role": "assistant", "content": response.content})
                if response.stop_reason != "tool_use":
                    break

                results = []
                for block in (b for b in response.content if b.type == "tool_use"):
                    if tool_calls >= MAX_TOOL_CALLS:
                        limit_hit = True
                        results.append({
                            "type": "tool_result", "tool_use_id": block.id, "is_error": True,
                            "content": f"Not run: the limit of {MAX_TOOL_CALLS} tool calls "
                                       "was reached. Write your brief now from the tool "
                                       "results you have."})
                        continue
                    tool_calls += 1
                    print(f"    analyst tool call {tool_calls}: {block.name} "
                          f"{json.dumps(block.input)}")
                    result = await session.call_tool(block.name, block.input)
                    results.append({"type": "tool_result", "tool_use_id": block.id,
                                    "content": mcp_result_text(result),
                                    "is_error": bool(getattr(result, "is_error", False))})
                if tool_calls >= MAX_TOOL_CALLS:
                    limit_hit = True
                    print(f"    analyst stopped after {MAX_TOOL_CALLS} tool calls")
                messages.append({"role": "user", "content": results})

    brief = json.loads(text_of(response))
    handoff("analyst", "coordinator", f"brief ({tool_calls} tool calls)", brief)
    return brief


def run_analyst(email):
    """Synchronous wrapper for coordinators that aren't async."""
    return asyncio.run(run_analyst_async(email))


# ---------------------------------------------------------------------------
# Specialists 2-4: the writers and the reviewer (one model call each)
# ---------------------------------------------------------------------------

def remove_customer_names(value, terms=None):
    """Return a copy of a brief (or text) with every customer name and email removed."""
    terms = customer_terms() if terms is None else terms
    if isinstance(value, str):
        for t in terms:
            value = term_pattern(t).sub("[customer]", value)
        return value
    if isinstance(value, list):
        return [remove_customer_names(v, terms) for v in value]
    if isinstance(value, dict):
        return {k: remove_customer_names(v, terms) for k, v in value.items()
                if k not in ("customer", "contact_email")}
    return value


def revision_note(previous, feedback):
    if not previous:
        return ""
    return (f"\n\nA reviewer sent back your previous draft. Write a new draft that fixes "
            f"every point.\n<previous_draft>\n{previous}\n</previous_draft>\n"
            f"<reviewer_notes>\n{feedback}\n</reviewer_notes>")


def supplier_facts(email, brief):
    """The supplier writer's inputs, with all customer names removed."""
    terms = customer_terms()
    return {"email": remove_customer_names(read_email(email), terms),
            "brief": remove_customer_names(brief, terms)}


def write_supplier_reply(email, brief, previous=None, feedback=None):
    facts = supplier_facts(email, brief)
    user = (f"<supplier_email>\n{facts['email']}\n</supplier_email>\n\n"
            f"<analyst_brief>\n{json.dumps(facts['brief'], indent=2)}\n</analyst_brief>"
            + revision_note(previous, feedback))
    return one_call("supplier_writer", prompt("supplier_writer"), user)


def customer_order_facts(order_id, brief):
    """One order's facts, from orders.csv and the brief's ready day."""
    row = next((r for r in read_csv("orders.csv") if r["order_id"] == order_id), None)
    if row is None:
        raise ValueError(f"No order {order_id} in orders.csv")
    risk = next((o for o in brief["orders_at_risk"] if o["order_id"] == order_id), None)
    if risk is None:
        raise ValueError(f"Order {order_id} is not at risk in the brief")
    ship_day, ready = int(row["ship_in_days"]), risk["ready_day"]
    return {
        "order_id": order_id,
        "customer": row["customer"],
        "model": row["model"],
        "bikes": int(row["qty"]),
        "planned_ship_day": ship_day,
        "expected_ship_day": ready,
        "days_late": (ready - ship_day) if ready is not None else "not yet known",
        "late_penalty": row["late_penalty"].lower() in ("yes", "y", "true", "1"),
    }


def notice_facts(order):
    """Only the fields a customer notice may use (no penalty, no other orders)."""
    return {k: order[k] for k in CUSTOMER_FACT_FIELDS if k in order}


def write_customer_notice(order, previous=None, feedback=None):
    facts = notice_facts(order)
    user = (f"<order_facts>\n{json.dumps(facts, indent=2)}\n</order_facts>"
            + revision_note(previous, feedback))
    return one_call("customer_writer", prompt("customer_writer"), user)


def review(draft, facts, recipient):
    """Return {"verdict": "APPROVE" or "REVISE", "reasons": text}."""
    user = (f"Recipient: {recipient}\n\n<draft>\n{draft}\n</draft>\n\n"
            f"<facts>\n{facts if isinstance(facts, str) else json.dumps(facts, indent=2)}"
            f"\n</facts>")
    answer = one_call("reviewer", prompt("reviewer"), user)
    first, _, rest = answer.partition("\n")
    word = first.strip().strip("*#: ").upper()
    if word.startswith("APPROVE"):
        return {"verdict": "APPROVE", "reasons": rest.strip()}
    if word.startswith("REVISE"):
        return {"verdict": "REVISE", "reasons": rest.strip()}
    return {"verdict": "REVISE",
            "reasons": "The reviewer's answer did not start with APPROVE or REVISE:\n" + answer}


# ---------------------------------------------------------------------------
# The review step: write, review, revise (at most 2 times), check, save
# ---------------------------------------------------------------------------

def write_and_review(writer_name, write, facts, recipient):
    draft = write(None, None)
    handoff(writer_name, "reviewer", f"draft to {recipient}", draft)
    for revision in range(MAX_REVISIONS + 1):
        result = review(draft, facts, recipient)
        handoff("reviewer", writer_name if result["verdict"] == "REVISE" else "coordinator",
                result["verdict"], result["reasons"] or None)
        if result["verdict"] == "APPROVE" or revision == MAX_REVISIONS:
            return draft, result, revision
        draft = write(draft, result["reasons"])
        handoff(writer_name, "reviewer", f"revision {revision + 1} to {recipient}", draft)


def save_draft(name, recipient, draft, approved, revisions, reasons):
    DRAFTS.mkdir(exist_ok=True)
    path = DRAFTS / f"{name}.txt"
    status = ("NEEDS A PERSON: " + "; ".join(reasons)) if reasons else "OK"
    header = (f"To: {recipient}\nStatus: {status}\n"
              f"Reviewer: {'APPROVE' if approved else 'not approved'} "
              f"after {revisions} revision(s)\n"
              f"Saved: {datetime.now():%Y-%m-%d %H:%M}\n" + "-" * 60 + "\n")
    path.write_text(header + draft + "\n", encoding="utf-8")
    return path


def finish(name, recipient, draft, result, revisions, banned, banned_what, penalty_reason):
    reasons = []
    found = find_terms(draft, banned)
    if found:
        reasons.append(f"names {banned_what}: {', '.join(found)}")
    if penalty_reason:
        reasons.append(penalty_reason)
    if result["verdict"] != "APPROVE":
        reasons.append(f"reviewer did not approve after {MAX_REVISIONS} revisions")
    path = save_draft(name, recipient, draft, result["verdict"] == "APPROVE",
                      revisions, reasons)
    outcome = {"draft": draft, "path": str(path), "needs_person": bool(reasons),
               "reasons": reasons, "verdict": result["verdict"], "revisions": revisions}
    handoff("review step", "coordinator",
            f"{recipient} draft saved to {path.relative_to(REPO)}"
            + (f"  [NEEDS A PERSON: {'; '.join(reasons)}]" if reasons else "  [OK]"))
    return outcome


def reviewed_supplier_reply(email, brief, name="supplier-reply"):
    """Write and review a supplier reply, check it, and save it as drafts/<name>.txt."""
    facts = supplier_facts(email, brief)
    handoff("coordinator", "supplier_writer",
            "email and brief (customer names removed)", facts["brief"])
    draft, result, revisions = write_and_review(
        "supplier_writer", lambda prev, fb: write_supplier_reply(email, brief, prev, fb),
        facts, "supplier")
    penalties = [o["order_id"] for o in brief["orders_at_risk"] if o["late_penalty"]]
    return finish(name, "supplier", draft, result, revisions, customer_terms(),
                  "a customer",
                  f"orders at risk with a late penalty: {', '.join(penalties)}"
                  if penalties else None)


def reviewed_customer_notice(order, name=None):
    """Write and review one customer notice, check it, and save it as drafts/<name>.txt.
    order is a dict from customer_order_facts()."""
    facts = notice_facts(order)
    handoff("coordinator", "customer_writer", f"facts for order {order['order_id']}", facts)
    draft, result, revisions = write_and_review(
        "customer_writer", lambda prev, fb: write_customer_notice(order, prev, fb),
        facts, "customer")
    return finish(name or f"customer-{order['order_id']}", "customer", draft, result,
                  revisions, supplier_terms(), "a supplier",
                  f"order {order['order_id']} has a late penalty"
                  if order.get("late_penalty") else None)


# ---------------------------------------------------------------------------
# Quick test (not a coordinator): analyst, supplier reply, a notice per order at risk
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) != 2:
        sys.exit("Usage: .venv/bin/python week-05/specialists.py EMAIL.eml")
    path = Path(sys.argv[1])
    stem = path.stem
    brief = run_analyst(path)
    reviewed_supplier_reply(path, brief, name=f"{stem}-supplier-reply")
    for risk in brief["orders_at_risk"]:
        order = customer_order_facts(risk["order_id"], brief)
        reviewed_customer_notice(order, name=f"{stem}-customer-{risk['order_id']}")
    token_report()


if __name__ == "__main__":
    main()
