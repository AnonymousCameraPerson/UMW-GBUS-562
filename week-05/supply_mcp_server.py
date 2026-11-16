"""MCP server with Brookfield Bicycle Company's supply data tools.

Usage (from the top of the repository):
    .venv/bin/python week-05/supply_mcp_server.py
    .venv/bin/python week-05/supply_mcp_server.py --data week-05/brookfield-bikes

--data FOLDER is the company folder with the CSV files (default: week-05/brookfield-bikes):
    parts.csv               part_id, description, on_hand, supplier
    inbound.csv             po_number, part_id, supplier, qty, arrives_in_days
    bill-of-materials.csv   model, part_id, qty_per_bike
    orders.csv              order_id, customer, contact_email, model, qty, ship_in_days, late_penalty

Tools:
    get_part(part_id)                     stock on hand, scheduled shipments, models that use it
    get_orders_for_part(part_id)          open orders that need the part, in ship-day order
    check_coverage(part_id, arrivals)     which orders a new arrival schedule covers

Days are counted from today: day 0 is today, day 5 is five days from now. Stock on hand
is available on day 0, and an arrival can be used on the day it arrives.

Units are parts, not bikes: an order's units needed = bikes ordered x parts per bike.

How check_coverage fills orders: the new arrivals replace all of the part's scheduled
shipments. Orders are filled complete, one at a time, in ship-day order (ties by order
number). An order is filled only once every earlier order has been filled, so a short
order holds up the ones after it. A covered order ships on its ship day. A short
order's first ship day is the first day on which the stock and arrivals so far cover
it and every order before it.

The CSV files are read fresh on every call. The server never calls Claude, so it needs
no API key. It talks MCP over stdin/stdout, so everything it logs goes to stderr.
"""

import argparse
import csv
import sys
from pathlib import Path
from typing import TypedDict

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

HERE = Path(__file__).resolve().parent
DEFAULT_DATA = HERE / "brookfield-bikes"
DATA_DIR = DEFAULT_DATA        # set from --data in main()
FILES = ("parts.csv", "inbound.csv", "bill-of-materials.csv", "orders.csv")

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                            idempotentHint=True, openWorldHint=False)


# ---------------------------------------------------------------------------
# Result types (they become the tools' output schemas)
# ---------------------------------------------------------------------------

class Shipment(TypedDict):
    po_number: str
    supplier: str
    qty: int
    arrives_in_days: int


class ModelUse(TypedDict):
    model: str
    parts_per_bike: int


class PartInfo(TypedDict):
    part_id: str
    description: str
    supplier: str
    on_hand: int
    scheduled_shipments: list[Shipment]
    used_by_models: list[ModelUse]


class OrderNeed(TypedDict):
    order_id: str
    customer: str
    model: str
    bikes: int
    parts_per_bike: int
    units_needed: int          # bikes x parts per bike
    ship_day: int              # days from today
    late_penalty: bool


class OrdersForPart(TypedDict):
    part_id: str
    total_units_needed: int
    orders: list[OrderNeed]


class Arrival(TypedDict):
    qty: int                   # parts arriving
    day: int                   # days from today


class OrderCoverage(TypedDict):
    order_id: str
    customer: str
    model: str
    units_needed: int
    ship_day: int
    late_penalty: bool
    covered: bool              # true if it can ship complete on its ship day
    units_short: int           # parts it is short on its ship day (0 if covered)
    first_ship_day: int | None # its ship day if covered; else first day it can ship complete,
                               # or None if the arrivals never cover it
    days_late: int | None      # first_ship_day - ship_day when short; 0 if covered


class Coverage(TypedDict):
    part_id: str
    on_hand: int
    arrivals: list[Arrival]
    total_supply: int
    total_units_needed: int
    orders_covered: int
    orders_short: int
    orders: list[OrderCoverage]


def log(message):
    """stdout carries the MCP messages, so logs must go to stderr."""
    print(message, file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Reading the CSV files (fresh on every call)
# ---------------------------------------------------------------------------

def read_csv(name):
    with (DATA_DIR / name).open(newline="", encoding="utf-8-sig") as f:
        return [{k.strip(): (v or "").strip() for k, v in row.items() if k}
                for row in csv.DictReader(f)]


def to_int(value, what):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        raise ToolError(f"The data has a bad number for {what}: {value!r}")


def find_part(part_id):
    """Return the parts.csv row for part_id (case-insensitive), or raise a ToolError."""
    wanted = (part_id or "").strip().upper()
    parts = read_csv("parts.csv")
    for row in parts:
        if row["part_id"].upper() == wanted:
            return row
    known = ", ".join(r["part_id"] for r in parts)
    raise ToolError(f"No part {part_id!r}. Known parts: {known}. "
                    "Do not guess stock or orders for an unknown part.")


def models_using(part_id):
    return [{"model": r["model"],
             "parts_per_bike": to_int(r["qty_per_bike"], f"{r['model']} qty_per_bike")}
            for r in read_csv("bill-of-materials.csv") if r["part_id"] == part_id]


def orders_needing(part_id):
    """Open orders that need the part, in ship-day order (ties by order number)."""
    per_bike = {m["model"]: m["parts_per_bike"] for m in models_using(part_id)}
    orders = []
    for r in read_csv("orders.csv"):
        if r["model"] not in per_bike:
            continue
        bikes = to_int(r["qty"], f"{r['order_id']} qty")
        orders.append({
            "order_id": r["order_id"],
            "customer": r["customer"],
            "model": r["model"],
            "bikes": bikes,
            "parts_per_bike": per_bike[r["model"]],
            "units_needed": bikes * per_bike[r["model"]],
            "ship_day": to_int(r["ship_in_days"], f"{r['order_id']} ship_in_days"),
            "late_penalty": r["late_penalty"].lower() in ("yes", "y", "true", "1"),
        })
    orders.sort(key=lambda o: (o["ship_day"], o["order_id"]))
    return orders


# ---------------------------------------------------------------------------
# The coverage calculation
# ---------------------------------------------------------------------------

def coverage(part_id, arrivals):
    part = find_part(part_id)
    part_id = part["part_id"]
    on_hand = to_int(part["on_hand"], f"{part_id} on_hand")

    cleaned = []
    for i, a in enumerate(arrivals or [], 1):
        qty, day = a.get("qty"), a.get("day")
        if not isinstance(qty, int) or isinstance(qty, bool) or qty <= 0:
            raise ToolError(f"Arrival {i}: qty must be a whole number above 0 (got {qty!r}).")
        if not isinstance(day, int) or isinstance(day, bool) or day < 0:
            raise ToolError(f"Arrival {i}: day must be a whole number of days from today, "
                            f"0 or more (got {day!r}).")
        cleaned.append({"qty": qty, "day": day})
    cleaned.sort(key=lambda a: a["day"])

    # Supply over time: on hand at day 0, then each arrival on its day.
    steps = [(0, on_hand)]
    for a in cleaned:
        steps.append((a["day"], steps[-1][1] + a["qty"]))

    def supply_by(day):
        """Total parts available on or before this day."""
        return max(total for d, total in steps if d <= day) if day >= 0 else 0

    def first_day_with(units):
        """The first day on which total supply reaches units, or None."""
        return next((d for d, total in steps if total >= units), None)

    results = []
    need_before = 0                       # units needed by all earlier orders
    for o in orders_needing(part_id):
        need_through = need_before + o["units_needed"]
        ready = first_day_with(need_through)
        ready = max(ready, 0) if ready is not None else None
        covered = ready is not None and ready <= o["ship_day"]
        left_for_this = supply_by(o["ship_day"]) - need_before
        short = 0 if covered else o["units_needed"] - max(0, min(o["units_needed"], left_for_this))
        results.append({
            "order_id": o["order_id"],
            "customer": o["customer"],
            "model": o["model"],
            "units_needed": o["units_needed"],
            "ship_day": o["ship_day"],
            "late_penalty": o["late_penalty"],
            "covered": covered,
            "units_short": short,
            "first_ship_day": o["ship_day"] if covered else ready,
            "days_late": 0 if covered else (ready - o["ship_day"] if ready is not None else None),
        })
        need_before = need_through

    return {
        "part_id": part_id,
        "on_hand": on_hand,
        "arrivals": cleaned,
        "total_supply": steps[-1][1],
        "total_units_needed": need_before,
        "orders_covered": sum(r["covered"] for r in results),
        "orders_short": sum(not r["covered"] for r in results),
        "orders": results,
    }


# ---------------------------------------------------------------------------
# The MCP server
# ---------------------------------------------------------------------------

def create_server():
    mcp = MCPServer(
        name="brookfield-supply",
        instructions=(
            "Supply data tools for Brookfield Bicycle Company: parts, scheduled shipments "
            "and customer orders. Days are counted from today (day 0). Units are parts, "
            "not bikes. Use check_coverage for any question about which orders a shipment "
            "schedule covers; never work out coverage yourself."),
    )

    @mcp.tool(annotations=READ_ONLY, description=(
        "Look up one part: its description, supplier, stock on hand, its scheduled "
        "shipments (purchase order, quantity, arrives_in_days counted from today), and "
        "the bike models that use it with parts per bike.\n\n"
        "Args:\n    part_id: the part number, e.g. \"MC-500\""))
    def get_part(part_id: str) -> PartInfo:
        part = find_part(part_id)
        pid = part["part_id"]
        shipments = [{"po_number": r["po_number"], "supplier": r["supplier"],
                      "qty": to_int(r["qty"], f"{r['po_number']} qty"),
                      "arrives_in_days": to_int(r["arrives_in_days"],
                                                f"{r['po_number']} arrives_in_days")}
                     for r in read_csv("inbound.csv") if r["part_id"] == pid]
        shipments.sort(key=lambda s: s["arrives_in_days"])
        return {"part_id": pid, "description": part["description"],
                "supplier": part["supplier"],
                "on_hand": to_int(part["on_hand"], f"{pid} on_hand"),
                "scheduled_shipments": shipments, "used_by_models": models_using(pid)}

    @mcp.tool(annotations=READ_ONLY, description=(
        "List the open customer orders that need a part, in ship-day order. Each order "
        "has its order number, customer, model, bikes ordered, parts per bike, units "
        "needed (bikes x parts per bike), ship day (days from today) and whether a late "
        "penalty applies.\n\n"
        "Args:\n    part_id: the part number, e.g. \"TR-29\""))
    def get_orders_for_part(part_id: str) -> OrdersForPart:
        pid = find_part(part_id)["part_id"]
        orders = orders_needing(pid)
        return {"part_id": pid, "total_units_needed": sum(o["units_needed"] for o in orders),
                "orders": orders}

    @mcp.tool(annotations=READ_ONLY, description=(
        "Check which open orders a new arrival schedule for a part covers. The arrivals "
        "you give REPLACE all of the part's scheduled shipments; stock on hand is "
        "counted on day 0. Orders are filled complete, in ship-day order, so a short "
        "order holds up the ones after it. For each order it returns covered (true or "
        "false), units_short (parts missing on its ship day), first_ship_day (its ship "
        "day if covered; otherwise the first day it can ship complete, or null if the "
        "arrivals never cover it) and days_late. "
        "To check the current schedule, pass the scheduled shipments from get_part. To "
        "compare options, call it once per option.\n\n"
        "Args:\n"
        "    part_id: the part number, e.g. \"MC-500\"\n"
        "    arrivals: every shipment of the part still to come, each with qty (parts) "
        "and day (days from today), e.g. [{\"qty\": 120, \"day\": 5}, "
        "{\"qty\": 280, \"day\": 26}]"))
    def check_coverage(part_id: str, arrivals: list[Arrival]) -> Coverage:
        return coverage(part_id, arrivals)

    return mcp


def main():
    global DATA_DIR
    parser = argparse.ArgumentParser(
        description="MCP server with Brookfield Bicycle Company's supply data tools.")
    parser.add_argument("--data", type=Path, metavar="FOLDER", default=DEFAULT_DATA,
                        help="folder with parts.csv, inbound.csv, bill-of-materials.csv "
                             "and orders.csv (default: week-05/brookfield-bikes)")
    args = parser.parse_args()
    DATA_DIR = args.data.resolve()
    missing = [n for n in FILES if not (DATA_DIR / n).is_file()]
    if missing:
        parser.error(f"--data {args.data} is missing: {', '.join(missing)}")
    log(f"Brookfield supply data: {DATA_DIR}")
    create_server().run("stdio")


if __name__ == "__main__":
    main()
