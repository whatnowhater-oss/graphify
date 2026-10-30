"""Stages every product reuses: packaging, the delivery email, and the writer agent."""
from __future__ import annotations

import json
import os
import re
import zipfile
from pathlib import Path
from typing import Callable

from ..core import Order, Stage, Wait, ops_home

SECRET_RE = re.compile(
    r"(sk-ant-[\w-]{10,}|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{30,}|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|xox[abpr]-[\w-]{10,})")


def config() -> dict:
    p = ops_home() / "config.json"
    cfg = {"auto_send": False, "sender_name": "jay", "writer_model": "claude-opus-5-5"}
    if p.exists():
        cfg.update(json.loads(p.read_text()))
    return cfg


def scan_secrets(paths: list[Path]) -> list[str]:
    hits = []
    for p in paths:
        text = p.read_text(errors="ignore")
        for m in SECRET_RE.finditer(text):
            hits.append(f"{p.name}: {m.group(0)[:12]}…")
    return hits


def package_stage(files: list[str], bundle_name: Callable) -> Stage:
    """Copy the named workdir files into deliverables/<id>/ and zip them."""

    def run(o: Order) -> None:
        out = o.outdir
        name = bundle_name(o)
        zpath = out / f"{name}.zip"
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            for f in files:
                src = o.workdir / f
                if not src.exists():
                    raise FileNotFoundError(f"missing deliverable {f}")
                (out / src.name).write_bytes(src.read_bytes())
                z.write(src, f"{name}/{src.name}")
        o.data["artifacts"]["bundle"] = str(zpath)
        o.log(f"packaged {zpath.name} ({zpath.stat().st_size // 1024} KB)")

    return Stage("package", run, "zip the deliverable")


def deliver_stage(subject: Callable, body: Callable) -> Stage:
    """Write the delivery email, then wait until someone confirms it was sent.

    Sending is never done by this code. With config auto_send=true (jay's
    standing OK) the wait is on Claude, who sends it with the Gmail connector;
    otherwise it waits on jay to approve.
    """

    def run(o: Order) -> None:
        if o.data.get("sent_at"):
            return
        draft = o.outdir / "delivery_email.md"
        if not draft.exists():
            to = o.fields.get("email") or "(no email on order)"
            draft.write_text(f"To: {to}\nSubject: {subject(o)}\nAttach: {o.data['artifacts'].get('bundle')}\n\n"
                             + body(o).replace("{sender}", config()["sender_name"]))
            o.data["artifacts"]["email_draft"] = str(draft)
        if config()["auto_send"]:
            raise Wait(f"send {draft.name} with the zip attached, then run `ops sent {o.id}`", "claude")
        raise Wait(f"approve the delivery email at {draft}, then run `ops sent {o.id}` once sent", "jay")

    return Stage("deliver", run, "email the customer")


def have_api_credentials() -> bool:
    return any(os.environ.get(k) for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"))


def write_with_claude(system: str, prompt: str, model: str) -> str:
    """One long-form writing call. Streams so a long guide can't hit the HTTP timeout."""
    import anthropic

    client = anthropic.Anthropic()
    with client.beta.messages.stream(
        model=model,
        max_tokens=32000,
        system=system,
        messages=[{"role": "user", "content": prompt}],
        output_config={"effort": "high"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    ) as stream:
        msg = stream.get_final_message()
    if msg.stop_reason == "refusal":
        raise RuntimeError("model declined the writing request")
    if msg.stop_reason == "max_tokens":
        raise RuntimeError("guide was cut off at max_tokens")
    return "".join(b.text for b in msg.content if b.type == "text").strip()


def writer_stage(name: str, output: str, build_brief: Callable, system: str) -> Stage:
    """The writer agent. Uses the Claude API when a key is configured; otherwise
    it leaves a brief and waits for a Claude session (the scheduled routine, or
    any thread) to write `output` into the workdir and resume the order."""

    def run(o: Order) -> None:
        target = o.workdir / output
        if target.exists() and target.stat().st_size > 200:
            return
        brief = build_brief(o)
        (o.workdir / "BRIEF.md").write_text(f"<!-- system -->\n{system}\n\n<!-- task -->\n{brief}")
        if have_api_credentials():
            text = write_with_claude(system, brief, config()["writer_model"])
            target.write_text(text + "\n")
            o.log(f"{output} written by API ({len(text.split())} words)")
            return
        raise Wait(f"write {target} from {o.workdir / 'BRIEF.md'}, then `ops resume {o.id}`", "claude")

    return Stage(name, run, f"write {output}")
