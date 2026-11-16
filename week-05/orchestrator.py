"""Orchestrator for one Brookfield Bicycle Company supplier email: Claude decides which
specialists to use.

An agent (claude-sonnet-5) with the system prompt in brookfield-bikes/prompts/orchestrator.txt
(read fresh on every run) and four tools:
    run_supply_analyst      runs the supply analyst from specialists.py on the email
    draft_supplier_reply    writes, reviews and saves a reply to the supplier
    draft_customer_notice   writes, reviews and saves a notice for one order at risk
    finish                  a summary for a person, and whether a person must decide
The drafts go through the same review step as pipeline.py and are saved in week-05/drafts.
It stops after 10 tool calls. Each choice it makes is printed, then the same summary as
the pipeline.

Usage (from the top of the repository):
    .venv/bin/python week-05/orchestrator.py --email week-05/brookfield-bikes/supplier-emails/02-kendall-tire-options.eml
    .venv/bin/python week-05/orchestrator.py --email EMAIL.eml --save week-05/runs/02-orchestrator.txt
"""

import json

import anthropic

import specialists as sp
from pipeline import cli, print_summary, section

MAX_TOOL_CALLS = 10

NO_INPUT = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}

TOOLS = [
    {
        "name": "run_supply_analyst",
        "description": (
            "Run the supply analyst on this supplier email. It looks up the part, its "
            "scheduled shipments and the open orders that need it, checks which orders the "
            "new schedule (or each option) puts at risk, and returns its brief as JSON: "
            "part_id, what_changed, orders_at_risk, options, decisions_for_a_person, "
            "supplier_questions. The drafting tools need this brief, so run it first "
            "whenever the email might change how much arrives or when."),
        "input_schema": NO_INPUT,
        "strict": True,
    },
    {
        "name": "draft_supplier_reply",
        "description": (
            "Draft a reply to the supplier from the email and the analyst's brief (customer "
            "names are removed first). The draft is reviewed, revised if needed, checked "
            "and saved for a person. Returns where it was saved and whether it is marked "
            "for a person. Needs run_supply_analyst first. Call it at most once."),
        "input_schema": NO_INPUT,
        "strict": True,
    },
    {
        "name": "draft_customer_notice",
        "description": (
            "Draft a notice to the customer of one order at risk in the analyst's brief, "
            "from that order's facts only. The draft is reviewed, revised if needed, "
            "checked and saved for a person. Returns where it was saved and whether it is "
            "marked for a person. Needs run_supply_analyst first. One order per call.\n\n"
            "Args:\n    order_id: an order_id from the brief's orders_at_risk, e.g. \"O-1043\""),
        "input_schema": {
            "type": "object",
            "properties": {"order_id": {"type": "string"}},
            "required": ["order_id"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "finish",
        "description": (
            "End the job. Give a short summary for Brookfield's purchasing manager, and say "
            "whether a person needs to make a decision."),
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "person_must_decide": {"type": "boolean"},
            },
            "required": ["summary", "person_must_decide"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]


def draft_result(outcome):
    """What the orchestrator is told about a saved draft."""
    return json.dumps({k: outcome[k] for k in
                       ("path", "needs_person", "reasons", "verdict", "revisions", "draft")},
                      indent=2)


class Job:
    """The state of one orchestrator run, and the tools that act on it."""

    def __init__(self, email_path):
        self.email_path = email_path
        self.stem = email_path.stem
        self.brief = None
        self.saved = []
        self.problems = []
        self.supplier_done = False
        self.notices_done = set()
        self.finished = None

    def call(self, name, args):
        """Run one tool. Return (text for Claude, is_error)."""
        if name == "run_supply_analyst":
            if self.brief is not None:
                return "The analyst already ran; its brief is above.", True
            self.brief = sp.run_analyst(self.email_path)    # prints its own handoffs
            return json.dumps(self.brief, indent=2), False

        if name == "draft_supplier_reply":
            if self.brief is None:
                return "Run run_supply_analyst first: the reply is written from its brief.", True
            if self.supplier_done:
                return "A supplier reply was already drafted and saved.", True
            self.supplier_done = True
            outcome = sp.reviewed_supplier_reply(self.email_path, self.brief,
                                                 name=f"{self.stem}-supplier-reply")
            self.saved.append(outcome)
            return draft_result(outcome), False

        if name == "draft_customer_notice":
            order_id = args["order_id"].strip().upper()
            if self.brief is None:
                return "Run run_supply_analyst first: notices are for orders in its brief.", True
            if order_id in self.notices_done:
                return f"A notice for {order_id} was already drafted and saved.", True
            try:
                order = sp.customer_order_facts(order_id, self.brief)
            except ValueError as exc:
                self.problems.append(f"No notice for {order_id}: {exc}")
                return f"{exc}. Only orders in the brief's orders_at_risk get a notice.", True
            self.notices_done.add(order_id)
            outcome = sp.reviewed_customer_notice(order, name=f"{self.stem}-customer-{order_id}")
            self.saved.append(outcome)
            return draft_result(outcome), False

        if name == "finish":
            self.finished = args
            return "Finished.", False

        return f"Unknown tool: {name}", True


def run(email_path):
    print(f"Email: {email_path}")
    print(f"Model: {sp.MODEL}")
    job = Job(email_path)
    email = sp.read_email(email_path)
    client = anthropic.Anthropic(api_key=sp.api_key())
    system = (sp.PROMPTS / "orchestrator.txt").read_text(encoding="utf-8").strip()
    messages = [{"role": "user", "content":
                 f"Handle this supplier email.\n\n<email>\n{email}\n</email>"}]
    tool_calls = 0
    stop = None

    section("Orchestrator")
    while stop is None:
        response = client.messages.create(
            model=sp.MODEL, max_tokens=sp.MAX_TOKENS, system=system, tools=TOOLS,
            messages=messages)
        sp.count_tokens("orchestrator", response)
        sp.check_stop(response, "orchestrator")
        messages.append({"role": "assistant", "content": response.content})
        for block in response.content:
            if block.type == "text" and block.text.strip():
                print(f"\nOrchestrator says: {block.text.strip()}")

        if response.stop_reason != "tool_use":
            stop = f"the orchestrator stopped without calling finish ({response.stop_reason})"
            break

        results = []
        for block in (b for b in response.content if b.type == "tool_use"):
            if job.finished is not None or tool_calls >= MAX_TOOL_CALLS:
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "is_error": True, "content": "Not run: the job has ended."})
                continue
            tool_calls += 1
            print(f"\n*** CHOICE {tool_calls}: {block.name}"
                  + (f" {json.dumps(block.input)}" if block.input else ""))
            try:
                text, is_error = job.call(block.name, block.input)
            except Exception as exc:          # a specialist failed; tell the orchestrator
                text, is_error = f"{block.name} failed: {exc}", True
                job.problems.append(text)
                print(f"    {text}")
            results.append({"type": "tool_result", "tool_use_id": block.id,
                            "content": text, "is_error": is_error})
        messages.append({"role": "user", "content": results})

        if job.finished is not None:
            stop = "finish was called"
        elif tool_calls >= MAX_TOOL_CALLS:
            stop = f"reached the limit of {MAX_TOOL_CALLS} tool calls without finish"

    print(f"\nStopped: {stop} ({tool_calls} tool calls)")
    if job.finished is None:
        job.problems.append(f"No finish summary: {stop}.")
    print_summary(job.saved, job.brief, job.problems)

    section("Orchestrator's summary for a person")
    if job.finished:
        print(job.finished["summary"])
        print(f"\nA person must make a decision: "
              f"{'YES' if job.finished['person_must_decide'] else 'no'}")
    else:
        print("(none: finish was not called)")


def main():
    cli(run, "Let Claude choose which Brookfield specialists to run on one supplier email.")


if __name__ == "__main__":
    main()
