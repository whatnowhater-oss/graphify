"""Offline end-to-end tests for the order pipelines (no network, no API key)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("OPS_HOME", str(tmp_path / "ops"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    return tmp_path / "ops"


@pytest.fixture()
def pipelines():
    from ops.pipelines import PIPELINES
    return PIPELINES


def _tiny_repo(root: Path) -> Path:
    pkg = root / "shop" / "src" / "shop"
    pkg.mkdir(parents=True)
    (pkg / "cart.py").write_text(
        "from .prices import price_of\n\nclass Cart:\n    def __init__(self):\n        self.items = []\n\n"
        "    def add(self, sku):\n        self.items.append(sku)\n\n"
        "    def total(self):\n        return sum(price_of(s) for s in self.items)\n" * 3)
    (pkg / "prices.py").write_text("PRICES = {'a': 1}\n\ndef price_of(sku):\n    return PRICES[sku]\n")
    (pkg / "checkout.py").write_text("from .cart import Cart\n\ndef checkout(cart: Cart):\n    return cart.total()\n")
    (root / "shop" / "README.md").write_text("# Shop\nA tiny shop.\n")
    return root / "shop"


def _guide(paths: list[str]) -> str:
    refs = " ".join(f"`{p}`" for p in paths)
    filler = "This sentence explains how the parts fit together in plain words. " * 120
    return (f"# Codebase X-ray: shop\n\n## The one-minute version\n{refs}\n\n## Map of the code\n{filler}\n\n"
            "## The core abstractions\nCart.\n\n## Things that surprise new contributors\nNone.\n\n"
            "## Where to make common changes\nHere.\n")


@pytest.mark.skipif(shutil.which("graphify") is None, reason="graphify CLI not installed")
def test_xray_order_runs_to_delivery(home, pipelines, tmp_path):
    from ops.core import get_order, resume, tick
    from ops.intake import order_from_message

    repo = _tiny_repo(tmp_path)
    o = order_from_message({"product": "xray", "fields": {"repo_url": str(repo), "email": "c@x.io"}},
                           pipelines, "test")
    tick(pipelines)
    o = get_order(o.id)
    # no API key: the writer agent leaves a brief and waits for a Claude session
    assert o.data["stage"] == "write_guide" and o.data["waiting_on"]["who"] == "claude"
    assert (o.workdir / "BRIEF.md").exists() and (o.workdir / "graph.html").exists()

    # a guide that cites a file that doesn't exist must fail QA
    (o.workdir / "XRAY_GUIDE.md").write_text(_guide(
        ["src/shop/cart.py", "src/shop/prices.py", "src/shop/checkout.py", "cart.py", "src/shop/payments.py"]))
    resume(o, "guide written")
    tick(pipelines)
    o = get_order(o.id)
    assert o.data["stage"] == "qa" and "payments.py" in o.data["waiting_on"]["reason"]

    (o.workdir / "XRAY_GUIDE.md").write_text(_guide(
        ["src/shop/cart.py", "src/shop/prices.py", "src/shop/checkout.py", "cart.py", "checkout.py"]))
    resume(o, "guide fixed")
    tick(pipelines)
    o = get_order(o.id)
    assert o.data["stage"] == "deliver" and o.data["waiting_on"]["who"] == "jay"
    assert Path(o.data["artifacts"]["bundle"]).exists()
    assert "To: c@x.io" in (o.outdir / "delivery_email.md").read_text()


def test_auto_send_hands_email_to_claude(home, pipelines):
    from ops.core import get_order, tick
    from ops.intake import order_from_message

    (home).mkdir(parents=True, exist_ok=True)
    (home / "config.json").write_text(json.dumps({"auto_send": True}))
    o = order_from_message({"product": "cleanup", "fields": {
        "csv_path": str(FIXTURES / "before_customers.csv"), "email": "d@x.io"}}, pipelines, "test")
    tick(pipelines)
    o = get_order(o.id)
    assert o.data["stage"] == "deliver" and o.data["waiting_on"]["who"] == "claude"


def test_cleanup_pipeline_and_board(home, pipelines):
    from ops.__main__ import main
    from ops.core import get_order, load_orders, tick
    from ops.intake import order_from_message

    o = order_from_message({"product": "cleanup", "fields": {
        "csv_path": str(FIXTURES / "before_customers.csv"), "email": "d@x.io"}}, pipelines, "test")
    tick(pipelines)
    o = get_order(o.id)
    assert o.data["stage"] == "deliver"
    main(["sent", o.id])
    assert get_order(o.id).data["status"] == "done"
    board = (home / "STATUS.md").read_text()
    assert "1 delivered, $149" in board
    assert len(load_orders()) == 1


def test_unknown_columns_wait_for_a_column_map(home, pipelines, tmp_path):
    from ops.core import get_order, resume, tick
    from ops.intake import order_from_message

    src = FIXTURES / "before_customers.csv"
    lines = src.read_text().splitlines()
    lines[0] = "Full Name,E-mail,Tel,Town,Region,Since,Spend,Comments"
    odd = tmp_path / "odd.csv"
    odd.write_text("\n".join(lines) + "\n")
    o = order_from_message({"product": "cleanup", "fields": {"csv_path": str(odd), "email": "e@x.io"}},
                           pipelines, "test")
    tick(pipelines)
    o = get_order(o.id)
    assert o.data["stage"] == "map_columns" and o.data["waiting_on"]["who"] == "claude"
    theirs = lines[0].split(",")
    from ops.pipelines.cleanup import EXPECTED
    (o.workdir / "column_map.json").write_text(json.dumps(dict(zip(EXPECTED, theirs))))
    resume(o, "map written")
    tick(pipelines)
    assert get_order(o.id).data["stage"] == "deliver"


def test_store_webhooks_and_inbox(home, pipelines):
    from ops.core import load_orders, tick
    from ops.intake import drop_in_inbox

    ls = {"meta": {"event_name": "order_created", "custom_data": {"Repo URL": "https://github.com/a/b"}},
          "data": {"id": "42", "attributes": {"user_email": "f@x.io", "user_name": "F", "total": 19900,
                                              "first_order_item": {"product_name": "Codebase X-ray"}}}}
    drop_in_inbox({"provider": "lemonsqueezy", "payload": ls}, "ls1")
    drop_in_inbox({"provider": "lemonsqueezy", "payload": ls}, "ls1-retry")  # webhook retried
    gr = {"sale_id": "g1", "email": "g@x.io", "price": "19900", "product_name": "Codebase X-ray",
          "Repo URL": "https://user:pw@github.com/a/b"}
    drop_in_inbox({"provider": "gumroad", "payload": gr}, "gr1")
    from ops.core import ops_home
    ingest_only = __import__("ops.intake", fromlist=["ingest_inbox"]).ingest_inbox
    ingest_only(pipelines)
    orders = load_orders()
    assert len(orders) == 1  # duplicate webhook ignored, credential-bearing URL rejected
    o = orders[0]
    assert o.fields["repo_url"] == "https://github.com/a/b" and o.data["price_usd"] == 199
    assert (ops_home() / "inbox" / "rejected" / "gr1.json").exists()
    assert not list((ops_home() / "inbox").glob("*.json"))


def test_crashing_stage_retries_then_escalates(home, pipelines, monkeypatch):
    from ops.core import MAX_ATTEMPTS, get_order, tick
    from ops.intake import order_from_message
    from ops.pipelines import xray

    def boom(o):
        raise RuntimeError("network down")

    stage = next(s for s in xray.PIPELINE.stages if s.name == "fetch")
    monkeypatch.setattr(stage, "run", boom)
    o = order_from_message({"product": "xray", "fields": {"repo_url": "https://github.com/a/b",
                                                          "email": "h@x.io"}}, pipelines, "test")
    for _ in range(MAX_ATTEMPTS):
        tick(pipelines)
    o = get_order(o.id)
    assert o.data["status"] == "failed" and "network down" in o.data["waiting_on"]["reason"]
