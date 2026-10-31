"""Agent-Ready Repo Kit ($19 guide, $49 kit, $149 team license).

Stripe's after-payment redirect hands the buyer the download, so there is no
work to do. Sales are still logged as orders so the board counts every dollar
toward the weekly goal and refunds have a record to point at.
"""
from __future__ import annotations

from ..core import Order, Pipeline, Stage

TIERS = {19: "guide", 49: "kit", 149: "team"}


def record(o: Order) -> None:
    o.fields.setdefault("tier", TIERS.get(o.data["price_usd"], "unknown"))
    o.data["sent_at"] = o.data["created_at"]  # delivered by the Stripe redirect at checkout
    o.log(f"{o.fields['tier']} sale recorded; download delivered by the checkout redirect")


PIPELINE = Pipeline(
    product="kit", title="Repo Kit", price_usd=49, turnaround_hours=0, retention_days=0,
    customer_data=lambda o: [], stages=[Stage("record", record, "log the sale")],
)
