"""python -m ops <command>

  tick                      ingest the inbox, push every open order forward, refresh the board
  new <product> k=v ...     create an order by hand (e.g. new xray repo_url=... email=...)
  webhook <provider> FILE   turn a saved Lemon Squeezy / Gumroad payload into an order
  board                     print the status board
  show <id>                 print one order with its history
  todo [claude|jay]         list orders waiting on someone
  resume <id> [note]        clear a wait after fixing what it asked for
  sent <id>                 record that the delivery email went out (finishes the order)
  stages                    list each product's stages
"""
from __future__ import annotations

import json
import sys

from .core import Reject, get_order, load_orders, now, resume, tick, write_board
from .intake import order_from_message
from .pipelines import PIPELINES


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd, args = argv[0], argv[1:]
    if cmd == "tick":
        orders = tick(PIPELINES)
        for o in orders:
            w = o.data.get("waiting_on") or {}
            print(f"{o.id:28} {o.data['stage']:12} {o.data['status']:8} {w.get('who', '')}: {w.get('reason', '')}"[:200])
    elif cmd == "new":
        fields = dict(a.split("=", 1) for a in args[1:])
        msg = {"product": args[0], "fields": fields, "external_id": fields.pop("external_id", None)}
        try:
            o = order_from_message(msg, PIPELINES, "manual")
        except Reject as e:
            print(f"rejected: {e}")
            return 1
        print(o.id)
    elif cmd == "webhook":
        payload = json.load(open(args[1]))
        o = order_from_message({"provider": args[0], "payload": payload}, PIPELINES, f"webhook:{args[0]}")
        print(o.id)
    elif cmd == "board":
        print(write_board(load_orders(), PIPELINES).read_text())
    elif cmd == "show":
        print(json.dumps(get_order(args[0]).data, indent=2))
    elif cmd == "todo":
        who = args[0] if args else None
        for o in load_orders():
            w = o.data.get("waiting_on")
            if w and (who is None or w["who"] == who):
                print(f"{o.id}  [{w['who']}]  {w['reason']}")
    elif cmd == "resume":
        resume(get_order(args[0]), " ".join(args[1:]) or "manual")
    elif cmd == "sent":
        o = get_order(args[0])
        o.data["sent_at"] = now()
        resume(o, "delivery email sent")
        tick(PIPELINES)
    elif cmd == "stages":
        for p in PIPELINES.values():
            print(f"{p.product} ({p.title}, ${p.price_usd}):")
            for s in p.stages:
                print(f"  {s.name:12} {s.help}")
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
