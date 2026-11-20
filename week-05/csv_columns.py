"""Read a company folder's CSV files without assuming their column names.

Each file's header row is matched to the roles the tools need (the product, the quantity,
the parts per unit, the ship day, and so on) by the words in each column name. For
example, Brookfield's orders.csv has model, qty and ship_in_days, and Copperline's has
product, units and ship_by_day; both are read as product, quantity and ship_day.

    read_table(folder, "orders.csv")   rows as dicts keyed by role
    column_map(folder, "orders.csv")   {role: the column name found for it}

If a role matches no column, or more than one, a ColumnError names the file, the role and
the header row, so a person can fix the data instead of the tools guessing.
"""

import csv
import re
from pathlib import Path

# The roles read from each file, in the order they are matched.
TABLES = {
    "parts.csv": ("part_id", "description", "on_hand", "supplier"),
    "inbound.csv": ("po_number", "part_id", "supplier", "quantity", "arrives_in_days"),
    "bill-of-materials.csv": ("product", "part_id", "per_unit"),
    "orders.csv": ("order_id", "customer", "contact_email", "product", "quantity",
                   "ship_day", "late_penalty"),
    "suppliers.csv": ("supplier", "contact_name", "contact_email", "part_id"),
}

DESCRIBE = {
    "part_id": "part number", "description": "part description",
    "on_hand": "stock on hand", "supplier": "supplier name",
    "po_number": "purchase order number", "quantity": "quantity",
    "arrives_in_days": "arrival day (days from today)", "product": "product",
    "per_unit": "parts per unit", "order_id": "order number",
    "customer": "customer name", "contact_email": "contact email",
    "ship_day": "ship day (days from today)", "late_penalty": "late penalty",
    "contact_name": "contact name",
}

ID_WORDS = {"id", "number", "no", "num", "code"}
QTY_WORDS = {"qty", "quantity", "units", "unit", "count", "amount", "pieces", "pcs"}


def words(column):
    """'Ship_By_Day' -> {'ship', 'by', 'day'}"""
    return set(w for w in re.split(r"[^a-z0-9]+", column.lower()) if w)


def is_day(w):
    return bool(w & {"day", "days"}) or any(x.endswith("days") for x in w)


RULES = {
    "part_id": lambda w: "part" in w and bool(w & ID_WORDS),
    "order_id": lambda w: "order" in w and bool(w & ID_WORDS),
    "po_number": lambda w: bool(w & {"po", "purchase"}),
    "description": lambda w: bool(w & {"description", "desc"}),
    "on_hand": lambda w: bool(w & {"hand", "onhand", "stock", "inventory", "available"}),
    "supplier": lambda w: bool(w & {"supplier", "vendor", "manufacturer"})
                          and not (w & {"email", "contact", "name", "id"}),
    "contact_name": lambda w: "name" in w and not (w & {"email", "mail"}),
    "contact_email": lambda w: bool(w & {"email", "mail", "e"}) and not is_day(w),
    "customer": lambda w: bool(w & {"customer", "client", "buyer", "account"})
                          and not (w & {"email", "mail", "id"}),
    "product": lambda w: bool(w & {"product", "model", "item", "sku", "machine"})
                         and not (w & ID_WORDS - {"code"}),
    "quantity": lambda w: bool(w & QTY_WORDS) and "per" not in w and not is_day(w),
    "per_unit": lambda w: "per" in w or bool(w & QTY_WORDS),
    "ship_day": lambda w: is_day(w) and bool(w & {"ship", "shipping", "due", "deliver",
                                                   "delivery", "by", "in"}),
    "arrives_in_days": lambda w: is_day(w),
    "late_penalty": lambda w: bool(w & {"penalty", "penalties"}),
}


class ColumnError(Exception):
    """A role that matches no column, or more than one."""


def header(folder, name):
    with (Path(folder) / name).open(newline="", encoding="utf-8-sig") as f:
        return [c.strip() for c in next(csv.reader(f), []) if c.strip()]


def column_map(folder, name):
    """Return {role: column name} for one file, or raise ColumnError."""
    columns = header(folder, name)
    found = {}
    for role in TABLES[name]:
        left = [c for c in columns if c not in found.values()]
        matches = [c for c in left if RULES[role](words(c))]
        if len(matches) != 1:
            problem = "no column" if not matches else f"more than one column ({', '.join(matches)})"
            raise ColumnError(
                f"{name}: {problem} looks like the {DESCRIBE[role]}. "
                f"Header row: {', '.join(columns)}")
        found[role] = matches[0]
    return found


def read_table(folder, name):
    """The file's rows, as dicts keyed by role (values stripped)."""
    mapping = column_map(folder, name)
    with (Path(folder) / name).open(newline="", encoding="utf-8-sig") as f:
        rows = [{k.strip(): (v or "").strip() for k, v in row.items() if k}
                for row in csv.DictReader(f)]
    return [{role: row.get(col, "") for role, col in mapping.items()} for row in rows]


def describe_columns(folder):
    """One line per file: role=column for every role, as found from its header row."""
    lines = []
    for name in TABLES:
        if not (Path(folder) / name).is_file():
            continue
        mapping = column_map(folder, name)
        lines.append(f"  {name}: " + ", ".join(
            f"{role}={col}" for role, col in mapping.items()))
    return "\n".join(lines)
