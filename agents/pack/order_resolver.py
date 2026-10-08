"""Resolve Pack order information for Round 3 integration fixtures."""
from __future__ import annotations

import json
from pathlib import Path

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "orders.json"


def load_orders() -> list[dict]:
    with FIXTURE_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def resolve_order(org_id: str, unit_id: str) -> dict:
    for order in load_orders():
        if order["org_id"] == org_id and order["unit_id"] == unit_id:
            return order

    raise LookupError(
        f"No Pack order found for org_id={org_id}, unit_id={unit_id}"
    )


def parse_order_lines(order_lines: str) -> list[dict]:
    items = []

    for part in order_lines.split(";"):
        part = part.strip()
        if not part:
            continue

        sku, separator, quantity = part.rpartition(":")
        if not separator or not sku.strip():
            raise ValueError(f"Invalid order line: {part}")

        try:
            qty = int(quantity)
        except ValueError as exc:
            raise ValueError(f"Invalid quantity in order line: {part}") from exc

        if qty < 1:
            raise ValueError(f"Quantity must be positive: {part}")

        items.append({
            "sku": sku.strip(),
            "name": sku.strip(),
            "quantity": qty,
        })

    if not items:
        raise ValueError("Order contains no order lines")

    return items
