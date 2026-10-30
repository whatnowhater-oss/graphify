"""Done-for-you customer list cleanup: messy CSV in, clean CSV + review list + report out.

intake -> map_columns -> clean -> qa -> package -> deliver

Shows the same pattern with a different work step. When the customer's column
names differ from the cleaner's, the order waits for Claude to write a column
map instead of guessing.
"""
from __future__ import annotations

import csv
import json
import contextlib
import io
import shutil
from pathlib import Path

from ..core import Order, Pipeline, Reject, Stage, Wait
from . import clean_customers
from .common import deliver_stage, package_stage

EXPECTED = ["Customer Name", "Email", "Phone", "City", "State", "First Order", "Lifetime Spend", "Notes"]


def _validate(fields: dict) -> None:
    if not fields.get("csv_path"):
        raise Reject("order has no csv_path (the uploaded file)")


def intake(o: Order) -> None:
    src = Path(o.fields["csv_path"])
    if not src.exists():
        raise Wait(f"customer file {src} not found; ask the customer to upload it", "jay")
    if src.stat().st_size > 50_000_000:
        raise Wait("file is over 50 MB; quote a custom price", "jay")
    shutil.copy(src, o.workdir / "original.csv")


def map_columns(o: Order) -> None:
    with open(o.workdir / "original.csv", newline="", encoding="utf-8-sig") as fh:
        header = next(csv.reader(fh))
    mapping_p = o.workdir / "column_map.json"
    if set(EXPECTED) <= set(header):
        mapping = {c: c for c in EXPECTED}
    elif mapping_p.exists():
        mapping = json.loads(mapping_p.read_text())
    else:
        raise Wait(f"columns {header} don't match {EXPECTED}; write {mapping_p} as "
                   f'{{"Expected Name": "Their Column"}}, then `ops resume {o.id}`', "claude")
    with open(o.workdir / "original.csv", newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    with open(o.workdir / "mapped.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=EXPECTED)
        w.writeheader()
        for r in rows:
            w.writerow({e: r.get(mapping.get(e, ""), "") for e in EXPECTED})
    o.data["rows_in"] = len(rows)


def clean(o: Order) -> None:
    clean_customers.changes.clear()
    with contextlib.redirect_stdout(io.StringIO()):
        clean_customers.main(str(o.workdir / "mapped.csv"), str(o.workdir / "clean.csv"),
                             str(o.workdir / "CHANGES_REPORT.md"))


def qa(o: Order) -> None:
    with open(o.workdir / "clean.csv", newline="") as fh:
        out = list(csv.DictReader(fh))
    report = (o.workdir / "CHANGES_REPORT.md").read_text()
    problems = []
    if not out:
        problems.append("clean file is empty")
    if f"**Rows in:** {o.data['rows_in']}" not in report:
        problems.append("report row count doesn't match the input")
    merged = int(report.split("**Duplicates merged:** ")[1].split()[0])
    if len(out) + merged != o.data["rows_in"]:
        problems.append(f"rows lost: {o.data['rows_in']} in, {len(out)} out + {merged} merged")
    if problems:
        raise Wait("QA failed: " + "; ".join(problems), "claude")


def _name(o: Order) -> str:
    return f"customer-list-cleanup-{o.id[-6:]}"


PIPELINE = Pipeline(
    product="cleanup", title="Customer list cleanup", price_usd=149, validate=_validate,
    stages=[
        Stage("intake", intake, "find the uploaded file"),
        Stage("map_columns", map_columns, "match their columns to the cleaner's"),
        Stage("clean", clean, "normalize, merge duplicates, flag problems"),
        Stage("qa", qa, "no rows lost, report matches the data"),
        package_stage(["clean.csv", "clean_needs_review.csv", "CHANGES_REPORT.md"], _name),
        deliver_stage(lambda o: "Your cleaned customer list",
                      lambda o: f"Hi {o.fields.get('name') or 'there'},\n\nYour cleaned list is attached. "
                                "CHANGES_REPORT.md explains every fix; clean_needs_review.csv lists the rows "
                                "where a guess would have been worse than a blank.\n\nThanks,\n{sender}\n"),
    ],
)
