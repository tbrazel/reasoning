"""KaTeX renderability checking, shared by the ledger and the workbench.

The only faithful oracle for "does this render in KaTeX?" is KaTeX
itself, so we run the real library under Node. Node is already visible in
the sandbox (the bwrap profile binds /usr), and katex.min.js is vendored
beside the checker script in this package, so this works identically from
the CLI, the sandboxed MCP server, and the webapp with no extra
dependency and no new sandbox surface.

If Node or the asset is missing the checker degrades to "cannot check"
(callers treat that as "assume fine, warn once") rather than blocking.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

# Math spans, mirroring the frontend's delimiters (Markdown.tsx): display
# \[...\] and $$...$$, inline \(...\) and $...$. Code spans/fences are
# stripped first so a `$` inside code is never treated as math.
_CODE = re.compile(r"```.*?```|`[^`]*`", re.DOTALL)
_SPAN = re.compile(
    r"\\\[(?P<disp1>.*?)\\\]"
    r"|\$\$(?P<disp2>.*?)\$\$"
    r"|\\\((?P<inl1>.*?)\\\)"
    r"|\$(?P<inl2>(?:[^$\\]|\\.)+?)\$",
    re.DOTALL,
)


@dataclass(frozen=True)
class Span:
    text: str
    display: bool
    start: int
    end: int


@dataclass(frozen=True)
class Failure:
    span: str
    display: bool
    error: str


def extract_spans(text: str) -> list[Span]:
    """Every math span in `text`, in source order, code stripped."""
    # Blank out code regions (keep offsets) so their `$`/`\(` are inert.
    masked = _CODE.sub(lambda m: " " * (m.end() - m.start()), text)
    spans: list[Span] = []
    for m in _SPAN.finditer(masked):
        display = m.group("disp1") is not None or m.group("disp2") is not None
        body = next(g for g in m.groups() if g is not None)
        spans.append(Span(body, display, m.start(), m.end()))
    return spans


_probe_lock = threading.Lock()
_available: bool | None = None


def _node() -> str | None:
    return shutil.which("node")


def _script() -> Path:
    return Path(resources.files("reasoning_ledger") / "assets" / "katex_check.js")


def available() -> bool:
    """Whether the Node + KaTeX checker can run here (cached)."""
    global _available
    with _probe_lock:
        if _available is not None:
            return _available
        node = _node()
        ok = False
        if node and _script().exists():
            try:
                result = _run_node([{"span": "x^2", "display": False}])
                ok = bool(result) and result[0].get("ok") is True
            except Exception:
                ok = False
        if not ok:
            print(
                "[reasoning-ledger] KaTeX checker unavailable (need Node + the "
                "vendored katex asset) — LaTeX will not be validated.",
                file=sys.stderr,
                flush=True,
            )
        _available = ok
        return ok


def _run_node(spans: list[dict]) -> list[dict]:
    node = _node()
    if not node:
        raise RuntimeError("node not found")
    proc = subprocess.run(
        [node, str(_script())],
        input=json.dumps(spans),
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip()[-500:] or "node exited nonzero")
    data = json.loads(proc.stdout)
    if isinstance(data, dict) and data.get("fatal"):
        raise RuntimeError(data["fatal"])
    return data


def check_text(text: str) -> list[Failure]:
    """The KaTeX failures in `text`, or [] when it is clean or unchecked.

    Returns an empty list when the checker is unavailable, so a missing
    Node never blocks a commit — callers decide how loudly to warn.
    """
    spans = extract_spans(text)
    if not spans or not available():
        return []
    results = _run_node([{"span": s.text, "display": s.display} for s in spans])
    failures: list[Failure] = []
    for span, result in zip(spans, results, strict=False):
        if not result.get("ok"):
            failures.append(
                Failure(span.text, span.display, result.get("error") or "parse error")
            )
    return failures


def check_fields(fields: dict[str, str | None]) -> str | None:
    """First rendering error across named text fields, formatted for a
    rejection message, or None if all clean/unchecked. `fields` maps a
    human label to its text."""
    for label, text in fields.items():
        if not text:
            continue
        failures = check_text(text)
        if failures:
            f = failures[0]
            opening = "\\[" if f.display else "\\("
            return (
                f"{label} contains LaTeX that KaTeX cannot render: "
                f"{f.error} (in {opening}{f.span.strip()[:80]}…)"
            )
    return None
