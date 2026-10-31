"""Agent-Ready Repo Kit ($19 guide, $49 kit, $149 team license).

The zips are small (under 25 KB), so the download is emailed to the buyer as an
attachment instead of a shared Drive link. Each sale becomes an order:
record the tier, copy the matching zip into deliverables/<id>/, then the usual
deliver stage drafts the email (or, with auto_send, Claude sends it).
The zips live in config "kit_dir" (default /mnt/project-files/digital-products).
"""
from __future__ import annotations

import shutil
from pathlib import Path

from ..core import Order, Pipeline, Stage, Wait
from .common import config, deliver_stage

TIERS = {19: "guide", 49: "kit", 149: "team"}
ZIPS = {"guide": "agent-ready-guide-v1.zip", "kit": "agent-ready-kit-v1.zip",
        "team": "agent-ready-kit-team-v1.zip"}
NAMES = {"guide": "Agent-Ready Repo Guide", "kit": "Agent-Ready Repo Kit",
         "team": "Agent-Ready Repo Kit (team license)"}
DEFAULT_KIT_DIR = "/mnt/project-files/digital-products"


def record(o: Order) -> None:
    tier = o.fields.setdefault("tier", TIERS.get(o.data["price_usd"], "unknown"))
    if tier not in ZIPS:
        raise Wait(f"can't tell which kit ${o.data['price_usd']} buys; set fields.tier to "
                   f"guide, kit or team, then `ops resume {o.id}`", "jay")
    if not o.fields.get("email"):
        raise Wait(f"no buyer email on the Stripe payment; find it in the Stripe receipt, set "
                   f"fields.email, then `ops resume {o.id}`", "claude")
    o.log(f"{tier} sale recorded")


def attach(o: Order) -> None:
    src = Path(config().get("kit_dir", DEFAULT_KIT_DIR)) / ZIPS[o.fields["tier"]]
    if not src.exists():
        raise FileNotFoundError(f"kit file missing: {src}")
    dest = o.outdir / src.name
    shutil.copyfile(src, dest)
    o.data["artifacts"]["bundle"] = str(dest)
    o.log(f"attached {src.name} ({src.stat().st_size // 1024} KB)")


def _subject(o: Order) -> str:
    return f"Your {NAMES[o.fields['tier']]} download"


def _body(o: Order) -> str:
    return f"""Hi{(' ' + o.fields['name'].split()[0]) if o.fields.get('name') else ''},

Thanks for buying the {NAMES[o.fields['tier']]}. Your files are attached to this email as a zip. Start with START_HERE.md inside it.

Questions, or a refund within 14 days: just reply to this email.

{{sender}}
"""


PIPELINE = Pipeline(
    product="kit", title="Repo Kit", price_usd=49, turnaround_hours=4, retention_days=0,
    customer_data=lambda o: [],
    stages=[Stage("record", record, "log the sale"),
            Stage("attach", attach, "attach the matching zip"),
            deliver_stage(_subject, _body)],
)
