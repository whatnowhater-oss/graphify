"""Codebase X-ray ($199): repo URL in, interactive map + written guide out.

intake -> fetch -> map -> write_guide -> qa -> package -> deliver
"""
from __future__ import annotations

import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

from ..core import Order, Pipeline, Reject, Stage, Wait
from .common import deliver_stage, package_stage, scan_secrets, writer_stage

HERE = Path(__file__).parent
ALLOWED_HOSTS = {"github.com", "gitlab.com", "bitbucket.org", "codeberg.org"}
CODE_EXT = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".kt", ".rb", ".php",
            ".cs", ".c", ".cc", ".cpp", ".h", ".hpp", ".swift", ".scala", ".ex", ".exs", ".vue", ".svelte"}
MAX_CODE_LINES = 400_000
REQUIRED_SECTIONS = ["one-minute version", "map of the code", "core abstractions",
                     "surprise", "where to make common changes"]
SKIP_DIRS = {".git", "node_modules", "vendor", "dist", "build", "graphify-out", "__pycache__", ".venv"}


def _validate(fields: dict) -> None:
    url = (fields.get("repo_url") or "").strip()
    if not url:
        raise Reject("order has no repo_url")
    if url.startswith(("/", "file://")):
        return  # local path: used by tests and for repos jay uploads by hand
    u = urlparse(url)
    if u.scheme != "https" or u.hostname not in ALLOWED_HOSTS or u.username or u.password:
        raise Reject(f"repo_url must be a plain https link on {sorted(ALLOWED_HOSTS)}: {url}")


def _repo_dir(o: Order) -> Path:
    return o.workdir / "repo"


def _code_files(root: Path) -> list[Path]:
    out = []
    for p in root.rglob("*"):
        if p.is_file() and p.suffix in CODE_EXT and not (SKIP_DIRS & set(p.relative_to(root).parts)):
            out.append(p)
    return out


def intake(o: Order) -> None:
    _validate(o.fields)
    if not o.fields.get("email"):
        raise Reject("order has no customer email")


def fetch(o: Order) -> None:
    dest = _repo_dir(o)
    if (dest / ".git").exists() or (dest.exists() and any(dest.iterdir())):
        return
    url = o.fields["repo_url"].strip()
    if url.startswith(("/", "file://")):
        src = Path(url.removeprefix("file://"))
        shutil.copytree(src, dest, ignore=shutil.ignore_patterns(".git", "graphify-out"))
    else:
        r = subprocess.run(["git", "clone", "--depth", "1", "--quiet", url, str(dest)],
                           capture_output=True, text=True, timeout=600,
                           env={"GIT_TERMINAL_PROMPT": "0", "PATH": "/usr/bin:/bin:/usr/local/bin"})
        if r.returncode != 0:
            shutil.rmtree(dest, ignore_errors=True)
            if "not found" in r.stderr.lower() or "authentication" in r.stderr.lower():
                raise Wait(f"repo is private or missing; ask the customer for read access ({r.stderr.strip()[:200]})", "jay")
            raise RuntimeError(r.stderr.strip()[:500])
    files = _code_files(dest)
    lines = sum(len(p.read_bytes().splitlines()) for p in files)
    o.data["repo_stats"] = {"code_files": len(files), "code_lines": lines,
                            "languages": dict(Counter(p.suffix for p in files).most_common(6))}
    if not files:
        raise Reject("no source code found in the repo")
    if lines > MAX_CODE_LINES:
        raise Wait(f"repo has {lines:,} lines, over the {MAX_CODE_LINES:,} limit; quote a custom price", "jay")
    o.log(f"fetched {len(files)} code files, {lines:,} lines")


def map_code(o: Order) -> None:
    repo = _repo_dir(o)
    target = repo / o.fields["subdir"] if o.fields.get("subdir") else repo
    r = subprocess.run(["graphify", "update", str(target), "--force"], capture_output=True,
                       text=True, timeout=1800)
    out = target / "graphify-out"
    if r.returncode != 0 or not (out / "graph.html").exists():
        raise RuntimeError(f"mapping failed: {(r.stderr or r.stdout)[-500:]}")
    for f in ("graph.html", "GRAPH_REPORT.md", "graph.json"):
        shutil.copy(out / f, o.workdir / f)
    o.log("code map built")


def _tree(repo: Path, limit: int = 300) -> str:
    rows = []
    for p in sorted(_code_files(repo))[:limit]:
        rows.append(f"{p.relative_to(repo)}  ({len(p.read_bytes().splitlines())} lines)")
    return "\n".join(rows)


def _excerpts(repo: Path, report: str, budget: int = 120_000) -> str:
    """Source of the files the graph says matter most, until the budget runs out."""
    files = sorted(_code_files(repo), key=lambda p: -report.count(p.name))
    chunks, used = [], 0
    for p in files:
        text = p.read_text(errors="ignore")
        if used + len(text) > budget:
            continue
        chunks.append(f"===== {p.relative_to(repo)} =====\n{text}")
        used += len(text)
    return "\n\n".join(chunks)


WRITER_SYSTEM = """You write "Codebase X-ray" guides: a plain-English tour of a codebase for an engineer joining the team. You are paid per report, and customers compare the guide against their own code, so every claim must be checkable.

Rules:
- Only name files, classes and functions that appear in the material you are given. Write file paths in backticks exactly as they appear in the file list.
- Prefer concrete line counts, connection counts and file paths over adjectives.
- Never mention the tool that produced the graph by name. Call it "the code map" or "the graph".
- Output only the Markdown guide, starting with the "# Codebase X-ray: <project>" heading."""


def _brief(o: Order) -> str:
    repo = _repo_dir(o)
    report = (o.workdir / "GRAPH_REPORT.md").read_text()
    readme = next((p.read_text(errors="ignore")[:6000] for p in repo.glob("README*")), "(no README)")
    example = (HERE / "xray_example_guide.md").read_text()
    stats = o.data.get("repo_stats", {})
    return f"""Write the X-ray guide for this repository: {o.fields['repo_url']}
Customer notes: {o.fields.get('notes') or '(none)'}
Stats: {stats}

Use exactly the section structure of the example guide below (same headings, adapted to this codebase). Rename "How a request flows" to the main flow that fits this project.

<example_guide>
{example}
</example_guide>

<code_map_report>
{report}
</code_map_report>

<file_list>
{_tree(repo)}
</file_list>

<readme>
{readme}
</readme>

<source_excerpts>
{_excerpts(repo, report)}
</source_excerpts>"""


PATH_RE = re.compile(r"`([\w./-]+\.(?:" + "|".join(e[1:] for e in CODE_EXT) + r"))(?::\d+)?`")


def qa(o: Order) -> None:
    guide_p = o.workdir / "XRAY_GUIDE.md"
    guide = guide_p.read_text()
    repo = _repo_dir(o)
    known = {str(p.relative_to(repo)) for p in _code_files(repo)}
    problems = []
    low = guide.lower()
    problems += [f"missing section: {s}" for s in REQUIRED_SECTIONS if s not in low]
    words = len(guide.split())
    if not 600 <= words <= 6000:
        problems.append(f"length {words} words, expected 600-6000")
    refs = set(PATH_RE.findall(guide))
    bad = sorted(r for r in refs if not any(k == r or k.endswith("/" + r) for k in known))
    if bad:
        problems.append(f"file paths not in the repo: {', '.join(bad[:10])}")
    if len(refs) < 5:
        problems.append(f"only {len(refs)} file references; a useful guide cites at least 5")
    if "graphify" in low:
        problems.append("mentions the mapping tool by name")
    if (o.workdir / "graph.html").stat().st_size < 10_000:
        problems.append("graph.html looks empty")
    problems += [f"possible secret: {h}" for h in scan_secrets([guide_p, o.workdir / "GRAPH_REPORT.md"])]
    (o.workdir / "QA_REPORT.md").write_text(
        "# QA\n\n" + ("\n".join(f"- {p}" for p in problems) if problems else "All checks passed.") + "\n")
    if problems:
        raise Wait(f"QA failed ({'; '.join(problems)[:300]}); fix {guide_p} then `ops resume {o.id}`", "claude")
    o.log(f"QA passed: {words} words, {len(refs)} file references, all real")


def _name(o: Order) -> str:
    return "codebase-xray-" + re.sub(r"[^\w-]", "", o.fields["repo_url"].rstrip("/").split("/")[-1].removesuffix(".git"))


def _subject(o: Order) -> str:
    return f"Your Codebase X-ray: {o.fields['repo_url'].rstrip('/').split('/')[-1]}"


def _body(o: Order) -> str:
    name = o.fields.get("name") or "there"
    return f"""Hi {name},

Your Codebase X-ray is ready. It's attached as a zip with three files:

- XRAY_GUIDE.md: the written tour. Start with "The one-minute version".
- graph.html: the interactive map. Open it in any browser and click a node to see what it connects to.
- GRAPH_REPORT.md: the raw numbers behind the map.

If anything in the guide doesn't match your code, reply to this email and I'll fix it the same day.

Thanks for your order,
{{sender}}
"""


PIPELINE = Pipeline(
    product="xray", title="Codebase X-ray", price_usd=199, validate=_validate,
    stages=[
        Stage("intake", intake, "check the order has a usable repo link and email"),
        Stage("fetch", fetch, "shallow-clone the repo and size it"),
        Stage("map", map_code, "build the interactive code map (no LLM)"),
        writer_stage("write_guide", "XRAY_GUIDE.md", _brief, WRITER_SYSTEM),
        Stage("qa", qa, "check sections, length, every cited file is real, no secrets"),
        package_stage(["XRAY_GUIDE.md", "graph.html", "GRAPH_REPORT.md"], _name),
        deliver_stage(_subject, _body),
    ],
)
