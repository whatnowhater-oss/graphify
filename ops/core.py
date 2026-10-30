"""Order store, stage runner and status board shared by every product pipeline.

An order is one JSON file under $OPS_HOME/orders/. Each pipeline is an ordered
list of stages. `tick()` walks every open order forward until it finishes,
fails, or has to wait for something outside the pipeline (a writer, or jay's
OK to send an email). Everything is idempotent, so tick can run on any
schedule and a crash mid-stage is simply retried on the next tick.
"""
from __future__ import annotations

import datetime as _dt
import html
import json
import os
import secrets
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

MAX_ATTEMPTS = 3

# Order status values. "waiting" always carries a reason in order["waiting_on"].
QUEUED, WAITING, FAILED, DONE = "queued", "waiting", "failed", "done"


def now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ops_home() -> Path:
    home = Path(os.environ.get("OPS_HOME", "ops-data")).resolve()
    for sub in ("orders", "work", "deliverables", "inbox", "inbox/processed"):
        (home / sub).mkdir(parents=True, exist_ok=True)
    return home


class Wait(Exception):
    """Raised by a stage that cannot finish yet. The order pauses until resumed."""

    def __init__(self, reason: str, who: str = "claude"):
        super().__init__(reason)
        self.reason, self.who = reason, who


class Reject(Exception):
    """Raised when an order can never succeed (bad input). No retries."""


@dataclass
class Stage:
    name: str
    run: Callable[["Order"], None]
    help: str = ""


@dataclass
class Pipeline:
    product: str
    title: str
    price_usd: int
    stages: list[Stage]
    validate: Callable[[dict], None] = lambda fields: None
    turnaround_hours: int = 48

    def stage_names(self) -> list[str]:
        return [s.name for s in self.stages]


@dataclass
class Order:
    data: dict
    path: Path
    home: Path = field(repr=False)

    @property
    def id(self) -> str:
        return self.data["id"]

    @property
    def fields(self) -> dict:
        return self.data["fields"]

    @property
    def workdir(self) -> Path:
        d = self.home / "work" / self.id
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def outdir(self) -> Path:
        d = self.home / "deliverables" / self.id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def log(self, msg: str) -> None:
        self.data["history"].append({"at": now(), "stage": self.data["stage"], "msg": msg})

    def save(self) -> None:
        self.data["updated_at"] = now()
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2))
        tmp.replace(self.path)


# ---------------------------------------------------------------- store

def create_order(pipeline: Pipeline, fields: dict, source: str = "manual",
                 external_id: str | None = None) -> Order:
    home = ops_home()
    if external_id:
        for o in load_orders():
            if o.data.get("external_id") == external_id:
                return o  # webhook retries must not create duplicate orders
    pipeline.validate(fields)
    oid = f"{pipeline.product}-{_dt.date.today():%Y%m%d}-{secrets.token_hex(3)}"
    data = {
        "id": oid, "product": pipeline.product, "fields": fields, "source": source,
        "external_id": external_id, "price_usd": fields.get("price_usd", pipeline.price_usd),
        "stage": pipeline.stages[0].name, "status": QUEUED, "waiting_on": None,
        "attempts": 0, "created_at": now(), "updated_at": now(), "history": [],
        "due_at": (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=pipeline.turnaround_hours))
        .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "artifacts": {},
    }
    order = Order(data, home / "orders" / f"{oid}.json", home)
    order.log(f"order received from {source}")
    order.save()
    return order


def load_orders() -> list[Order]:
    home = ops_home()
    out = []
    for p in sorted((home / "orders").glob("*.json")):
        out.append(Order(json.loads(p.read_text()), p, home))
    return out


def get_order(oid: str) -> Order:
    """Exact id, or any unique fragment of one (so `ops show b9f04f` works)."""
    orders = load_orders()
    exact = [o for o in orders if o.id == oid]
    matches = exact or [o for o in orders if oid in o.id]
    if len(matches) != 1:
        raise SystemExit(f"{'no' if not matches else 'more than one'} order matching {oid}")
    return matches[0]


# ---------------------------------------------------------------- runner

def advance(order: Order, pipeline: Pipeline) -> None:
    """Run stages for one order until it waits, fails or finishes."""
    names = pipeline.stage_names()
    while order.data["status"] == QUEUED:
        stage = pipeline.stages[names.index(order.data["stage"])]
        try:
            stage.run(order)
        except Wait as w:
            order.data.update(status=WAITING, waiting_on={"who": w.who, "reason": w.reason})
            order.log(f"waiting on {w.who}: {w.reason}")
            break
        except Reject as r:
            order.data.update(status=FAILED, waiting_on={"who": "jay", "reason": f"rejected: {r}"})
            order.log(f"rejected: {r}")
            break
        except Exception as e:  # noqa: BLE001 - any stage crash is retried, then escalated
            order.data["attempts"] += 1
            order.log(f"error ({order.data['attempts']}/{MAX_ATTEMPTS}): {e!r}")
            order.data["last_error"] = traceback.format_exc()[-2000:]
            if order.data["attempts"] >= MAX_ATTEMPTS:
                order.data.update(status=FAILED,
                                  waiting_on={"who": "claude", "reason": f"{stage.name} failed: {e}"})
            order.save()
            break
        order.log(f"{stage.name} done")
        order.data["attempts"] = 0
        i = names.index(stage.name)
        if i + 1 == len(names):
            order.data.update(status=DONE, stage="done", waiting_on=None)
        else:
            order.data["stage"] = names[i + 1]
        order.save()
    order.save()


def purge_code(days: int = 7) -> list[str]:
    """Delete customer code (work/<id>/repo) for orders delivered `days` or more ago.
    The delivered report stays in deliverables/."""
    import shutil

    cutoff = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    purged = []
    for o in load_orders():
        repo = o.home / "work" / o.id / "repo"
        if o.data["status"] == DONE and o.data.get("sent_at", "9") <= cutoff and repo.exists():
            shutil.rmtree(repo)
            o.data["code_deleted_at"] = now()
            o.log("customer code deleted")
            o.save()
            purged.append(o.id)
    return purged


def resume(order: Order, note: str) -> None:
    """Clear a wait (or a failure) so the next tick re-runs the current stage."""
    order.data.update(status=QUEUED, waiting_on=None, attempts=0)
    order.log(f"resumed: {note}")
    order.save()


def tick(pipelines: dict[str, Pipeline]) -> list[Order]:
    from .intake import ingest_inbox

    ingest_inbox(pipelines)
    orders = load_orders()
    for o in orders:
        if o.data["status"] == QUEUED:
            advance(o, pipelines[o.data["product"]])
    write_board(orders, pipelines)
    return orders


# ---------------------------------------------------------------- board

def _week_start() -> str:
    today = _dt.date.today()
    return str(today - _dt.timedelta(days=today.weekday()))


def summarize(orders: list[Order]) -> dict:
    week = _week_start()
    done_week = [o for o in orders if o.data["status"] == DONE and o.data["updated_at"][:10] >= week]
    return {
        "open": sum(o.data["status"] in (QUEUED, WAITING) for o in orders),
        "needs_jay": [o for o in orders if (o.data.get("waiting_on") or {}).get("who") == "jay"],
        "needs_claude": [o for o in orders if (o.data.get("waiting_on") or {}).get("who") == "claude"],
        "delivered_this_week": len(done_week),
        "revenue_this_week": sum(o.data["price_usd"] for o in done_week),
    }


def write_board(orders: list[Order], pipelines: dict[str, Pipeline]) -> Path:
    home = ops_home()
    s = summarize(orders)
    lines = [
        "# Order board", "",
        f"Updated {now()}. Week starting {_week_start()}: "
        f"**{s['delivered_this_week']} delivered, ${s['revenue_this_week']} of $1,000 goal**. "
        f"{s['open']} open.", "",
    ]
    if s["needs_jay"]:
        lines += ["## Needs jay", ""]
        lines += [f"- `{o.id}`: {o.data['waiting_on']['reason']}" for o in s["needs_jay"]]
        lines.append("")
    if s["needs_claude"]:
        lines += ["## Needs Claude", ""]
        lines += [f"- `{o.id}`: {o.data['waiting_on']['reason']}" for o in s["needs_claude"]]
        lines.append("")
    lines += ["## All orders", "", "| Order | Product | Stage | Status | Customer | Due | Updated |",
              "|---|---|---|---|---|---|---|"]
    for o in sorted(orders, key=lambda o: o.data["updated_at"], reverse=True):
        d = o.data
        title = pipelines[d["product"]].title if d["product"] in pipelines else d["product"]
        lines.append(f"| `{d['id']}` | {title} | {d['stage']} | {d['status']} | "
                     f"{d['fields'].get('email', '')} | {d.get('due_at', '')} | {d['updated_at']} |")
    md = "\n".join(lines) + "\n"
    (home / "STATUS.md").write_text(md)
    (home / "status.html").write_text(_board_html(orders, s, pipelines))
    return home / "STATUS.md"


def _board_html(orders, s, pipelines) -> str:
    rows = "".join(
        f"<tr class='{html.escape(o.data['status'])}'><td><code>{html.escape(o.id)}</code></td>"
        f"<td>{html.escape(pipelines.get(o.data['product']).title if o.data['product'] in pipelines else o.data['product'])}</td>"
        f"<td>{html.escape(o.data['stage'])}</td><td>{html.escape(o.data['status'])}</td>"
        f"<td>{html.escape((o.data.get('waiting_on') or {}).get('reason', ''))}</td>"
        f"<td>{html.escape(o.data['updated_at'])}</td></tr>"
        for o in sorted(orders, key=lambda o: o.data["updated_at"], reverse=True))
    pct = min(100, s["revenue_this_week"] // 10)
    return f"""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Order board</title>
<style>
:root{{--bg:#fff;--fg:#1a1a1a;--mut:#666;--line:#e3e3e3;--ok:#1f7a4d;--warn:#a86400;--bad:#b42318;--bar:#2563eb}}
@media (prefers-color-scheme:dark){{:root{{--bg:#141414;--fg:#eee;--mut:#999;--line:#333;--ok:#4ade80;--warn:#fbbf24;--bad:#f87171;--bar:#60a5fa}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif;margin:0;padding:24px 16px;max-width:1000px;margin:auto}}
.goal{{height:10px;background:var(--line);border-radius:5px;overflow:hidden}}.goal div{{height:100%;background:var(--bar);width:{pct}%}}
table{{width:100%;border-collapse:collapse;font-size:14px}}td,th{{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line)}}
.wrap{{overflow-x:auto}}.done td:nth-child(4){{color:var(--ok)}}.waiting td:nth-child(4){{color:var(--warn)}}.failed td:nth-child(4){{color:var(--bad)}}
p.m{{color:var(--mut)}}
</style>
<h1>Order board</h1>
<p><b>${s['revenue_this_week']}</b> of $1,000 this week · {s['delivered_this_week']} delivered · {s['open']} open · {len(s['needs_jay'])} need jay</p>
<div class="goal"><div></div></div>
<div class="wrap"><table><tr><th>Order</th><th>Product</th><th>Stage</th><th>Status</th><th>Waiting on</th><th>Updated</th></tr>{rows}</table></div>
<p class="m">Updated {now()}</p>
"""
