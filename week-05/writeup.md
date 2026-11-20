## What I Built
The team of Claude agents consists of four specialists: an analyst, two writers, and a reviewer. Each specialist receives its own instructions, and they can be coordinated either through a fixed pipeline or a Claude orchestrator. To make the team work for Copperline, I downloaded its .zip file and gave Claude the prompt provided in the assignment. Claude added an --org option to redirect the agents to the new files instead of the brookfield bikes files. I then ran the eight runs with the commands Claude provided me, changing the file names for each run.

## Why This Approach
Task A: Agent. Because the task depends on what the agent finds. This is like our RAG assignment we had. An agent would look up its order use a RAG pipeline to search the warranty terms. It then can use that information to draft a reply. This task is simple enough that we wouldn't need a team of agents; we are just looking up one café's order.
Task B: Team. Because this requires jobs that need different information, rules, and audiences. For this task, you would need to find the machines that were already shipped, figure out the affected customers, draft a noticd to each customer, and draft a report for the safety regulator. This job includes several audiences and several different steps that need different information and techniques for 
Task C: Pipeline. Because the Pipeline coordinator fits a task when the steps are the same every time. Task C says that every new wholesale order goes through the same steps. Therefore, Pipeline would best fit this task.
Task D: Orchestrator. Because the Orchestrator coordinator fits a task when the steps depend on the case. In Task D, the inbox receives different kinds of emails and it says that each needs a different mix of specialists. This means that there are different cases, and each case will contain different steps and tasks. Therefore, the Orchestrator is the best corrdinator for Task D.

## Example Run
Org:    week-05/copperline-coffee
Email:  week-05/copperline-coffee/supplier-emails/04-pelham-maintenance-closure.eml
Model:  claude-sonnet-5
Drafts: week-05/drafts/copperline-coffee
Columns found:
  parts.csv: part_id=part_id, description=description, on_hand=on_hand, supplier=supplier
  inbound.csv: po_number=po_number, part_id=part_id, supplier=supplier, quantity=qty, arrives_in_days=arrives_in_days
  bill-of-materials.csv: product=product, part_id=part_id, per_unit=per_unit
  orders.csv: order_id=order_id, customer=customer, contact_email=contact_email, product=product, quantity=units, ship_day=ship_by_day, late_penalty=late_penalty
  suppliers.csv: supplier=supplier, contact_name=contact_name, contact_email=contact_email, part_id=part_id

======================================================================
Orchestrator
======================================================================
    [tokens] orchestrator: 1,526 in, 431 out  (so far: 1,526 in, 431 out, 1 calls)

*** CHOICE 1: run_supply_analyst

>>> HANDOFF  coordinator -> analyst: supplier email
    From: Owen Brady <o.brady@pelhamfittings.example>
    To: Purchasing <purchasing@copperlinecoffee.example>
    Subject: Plant maintenance closure - PO-5540 not affected
    
    Hello,
    
    A quick note for your planning: our plant will close for one week of annual
    maintenance, starting four weeks from today.
    
    Your open order, PO-5540 for 60 SW-3 steam wands, ships before the closure
    and will arrive on the scheduled date. No action is needed on your side.
    
    Kind regards,
    Owen Brady
    Customer Care, Pelham Fittings
    
Supply data: week-05/copperline-coffee
Columns found:
  parts.csv: part_id=part_id, description=description, on_hand=on_hand, supplier=supplier
  inbound.csv: po_number=po_number, part_id=part_id, supplier=supplier, quantity=qty, arrives_in_days=arrives_in_days
  bill-of-materials.csv: product=product, part_id=part_id, per_unit=per_unit
  orders.csv: order_id=order_id, customer=customer, contact_email=contact_email, product=product, quantity=units, ship_day=ship_by_day, late_penalty=late_penalty
  suppliers.csv: supplier=supplier, contact_name=contact_name, contact_email=contact_email, part_id=part_id
    [tokens] analyst: 2,642 in, 141 out  (so far: 2,642 in, 141 out, 1 calls)
    analyst tool call 1: get_part {"part_id": "SW-3"}
    analyst tool call 2: get_orders_for_part {"part_id": "SW-3"}
    [tokens] analyst: 3,389 in, 120 out  (so far: 6,031 in, 261 out, 2 calls)
    analyst tool call 3: check_coverage {"part_id": "SW-3", "arrivals": [{"qty": 60, "day": 11}]}
    [tokens] analyst: 4,006 in, 130 out  (so far: 10,037 in, 391 out, 3 calls)

>>> HANDOFF  analyst -> coordinator: the brief in full (3 tool calls)
    {
      "part_id": "SW-3",
      "what_changed": "PO-5540's 60 SW-3 steam wands remain scheduled to arrive in 11 days, unaffected by Pelham Fittings' one-week plant closure starting in 28 days (four weeks from today).",
      "orders_at_risk": [],
      "options": [],
      "decisions_for_a_person": [],
      "supplier_questions": []
    }
    [tokens] orchestrator: 2,103 in, 263 out  (so far: 3,629 in, 694 out, 2 calls)

Orchestrator says: No orders are at risk, and no decisions or supplier questions are flagged. Since the shipment is confirmed on schedule and the supplier explicitly said no action is needed, no reply or customer notice is required.

*** CHOICE 2: finish {"summary": "Pelham Fittings notified us of a one-week plant maintenance closure starting in 4 weeks. They confirmed PO-5540 (60 SW-3 steam wands) ships before the closure and will arrive on schedule in 11 days. Analyst confirmed no orders are at risk and no changes to quantity or timing. No reply or customer notice needed\u2014filing for reference only.", "person_must_decide": false}

Stopped: finish was called (2 tool calls)

======================================================================
Summary
======================================================================
Drafts saved: 0

Drafts marked for a person: 0

Decisions for a person: 0

Tokens used by each specialist
  specialist         calls     input    output
  orchestrator           2     3,629       694
  analyst                3    10,037       391
  total                  5    13,666     1,085

Total tokens: 14,751 (13,666 input, 1,085 output)

======================================================================
Orchestrator's summary for a person
======================================================================
Pelham Fittings notified us of a one-week plant maintenance closure starting in 4 weeks. They confirmed PO-5540 (60 SW-3 steam wands) ships before the closure and will arrive on schedule in 11 days. Analyst confirmed no orders are at risk and no changes to quantity or timing. No reply or customer notice needed—filing for reference only.

A person must make a decision: no


## Reflection
Email 1 reading: Matches. Diane said that they could only deliver 40 pumps on the scheduled date, and this is what the what_changed line says. Diane also said that the remaining 160 pumps would follow 15 days later, and this is what the what_changed line says. The day for the first 40 pumps that the brief gave was day 4, and the day for the remaining 160 pumps was day 19.
Email 1 customer names: No. It answered Diane's request for customer names by saying that they are not able to share customer names or order details, but that they can confirm the impact in units and days as needed.
Email 4 orchestrator: Supply Analyst. For the same email, the pipeline called the supply analyst, the supplier writer, and the reviewer.