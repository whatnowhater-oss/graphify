"""Turn incoming purchases into orders.

Three ways in, all ending in core.create_order:
  * a JSON file dropped in $OPS_HOME/inbox/ (from a Zapier/Make hook, a form, or by hand)
  * `python -m ops new <product> --field k=v ...`
  * a raw store webhook payload (Lemon Squeezy or Gumroad), via either of the above

Store payloads are matched to a product by keywords in the product name, so the
store listing just needs "X-ray" or "cleanup" in its title.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from .core import Order, Pipeline, Reject, create_order, ops_home

PRODUCT_KEYWORDS = {"x-ray": "xray", "xray": "xray", "cleanup": "cleanup", "clean-up": "cleanup"}


def _product_for(name: str) -> str:
    low = name.lower()
    for kw, product in PRODUCT_KEYWORDS.items():
        if kw in low:
            return product
    raise Reject(f"no pipeline matches store product {name!r}")


def _norm_key(k: str) -> str:
    return k.strip().lower().replace(" ", "_").replace("-", "_")


def from_lemonsqueezy(payload: dict) -> tuple[str, dict, str]:
    """order_created webhook. Checkout custom fields arrive in meta.custom_data."""
    if payload.get("meta", {}).get("event_name") != "order_created":
        raise Reject("ignored Lemon Squeezy event " + str(payload.get("meta", {}).get("event_name")))
    attrs = payload["data"]["attributes"]
    fields = {_norm_key(k): v for k, v in (payload["meta"].get("custom_data") or {}).items()}
    fields.setdefault("email", attrs.get("user_email"))
    fields.setdefault("name", attrs.get("user_name"))
    fields["price_usd"] = round((attrs.get("total") or 0) / 100)
    product = _product_for(attrs.get("first_order_item", {}).get("product_name", ""))
    return product, fields, f"lemonsqueezy:{payload['data']['id']}"


def from_gumroad(payload: dict) -> tuple[str, dict, str]:
    """Gumroad ping (form fields). Custom fields arrive as top-level keys or custom_fields."""
    fields = {_norm_key(k): v for k, v in (payload.get("custom_fields") or {}).items()}
    for k, v in payload.items():
        nk = _norm_key(k)
        if nk in ("repo_url", "repo", "repository", "file_url", "notes", "access_token"):
            fields.setdefault("repo_url" if nk in ("repo", "repository") else nk, v)
    fields.setdefault("email", payload.get("email"))
    fields.setdefault("name", payload.get("full_name"))
    fields["price_usd"] = round(int(payload.get("price") or 0) / 100)
    product = _product_for(payload.get("product_name") or payload.get("product_permalink", ""))
    return product, fields, f"gumroad:{payload['sale_id']}"


PROVIDERS = {"lemonsqueezy": from_lemonsqueezy, "gumroad": from_gumroad}


def order_from_message(msg: dict, pipelines: dict[str, Pipeline], source: str) -> Order:
    if "provider" in msg:
        product, fields, ext = PROVIDERS[msg["provider"]](msg["payload"])
    else:
        product, fields, ext = msg["product"], msg["fields"], msg.get("external_id")
    if product not in pipelines:
        raise Reject(f"unknown product {product!r}")
    return create_order(pipelines[product], fields, source=source, external_id=ext)


def ingest_inbox(pipelines: dict[str, Pipeline]) -> list[Order]:
    inbox = ops_home() / "inbox"
    made = []
    for f in sorted(inbox.glob("*.json")):
        dest = inbox / "processed"
        try:
            made.append(order_from_message(json.loads(f.read_text()), pipelines, f"inbox:{f.name}"))
        except (Reject, KeyError, ValueError) as e:
            dest = inbox / "rejected"
            dest.mkdir(exist_ok=True)
            (dest / f"{f.name}.why.txt").write_text(f"{type(e).__name__}: {e}\n")
        shutil.move(str(f), str(dest / f.name))
    return made


def drop_in_inbox(msg: dict, name: str) -> Path:
    p = ops_home() / "inbox" / f"{name}.json"
    p.write_text(json.dumps(msg, indent=2))
    return p
