"""Two-stage, guarded LaTeX repair for display-only text.

Chat messages and verification logs are shown to the user but not
authored as the mathematical record, so instead of rejecting malformed
LaTeX we repair it for readability while keeping the original as the
source of truth. A cheap model does the work; the guarantee that it only
touches formatting comes from the structure, not from trusting it:

- **Stage A** fixes the *contents* of each KaTeX-failing math span. Only
  the span is ever sent to the model — never the prose — so prose cannot
  change. Each returned span is re-validated and spliced back only if it
  now renders.
- **Stage B** runs when the delimiters themselves are unbalanced (a
  missing/broken `$` that inverts what is math and what is text). The
  whole message is sent, but the model may only move delimiters; the
  guard strips all delimiters and requires the remaining characters to be
  byte-identical, so any reworded prose is rejected.

Either stage falls back to the input on any failure, and the final
result is returned only if it renders strictly better than the original.
The model is run like the reviewer (codex exec, ledger invisible); set
REASONING_REPAIR_COMMAND to a script for tests.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from contextlib import suppress
from pathlib import Path

from . import latex_check
from .store import LedgerStoreError

DEFAULT_REPAIR_MODEL = "gpt-5.6-luna"
DEFAULT_REPAIR_EFFORT = "medium"
DEFAULT_REPAIR_TIMEOUT = 300.0

# Delimiter tokens, for the balance check and Stage B's invariant.
_DELIMS = re.compile(r"\\\(|\\\)|\\\[|\\\]|\$\$|(?<!\\)\$")
_CODE = re.compile(r"```.*?```|`[^`]*`", re.DOTALL)


def _mask_code(text: str) -> str:
    return _CODE.sub(lambda m: " " * (m.end() - m.start()), text)


def has_unbalanced_delimiters(text: str) -> bool:
    """Whether any math delimiter is left dangling once every well-formed
    span is removed — the signal that the delimiters, not the contents,
    are broken."""
    residue = latex_check._SPAN.sub(" ", _mask_code(text))
    return bool(_DELIMS.search(residue))


def _strip_delims(text: str) -> str:
    """The message with delimiters removed and whitespace collapsed — the
    invariant Stage B must preserve (it may only move delimiters)."""
    return re.sub(r"\s+", " ", _DELIMS.sub("", _mask_code(text))).strip()


# --------------------------------------------------------------- model call


def _repair_command(scratch: str) -> list[str]:
    override = os.environ.get("REASONING_REPAIR_COMMAND")
    if override:
        return shlex.split(override)
    from . import sandbox

    executable = shutil.which("codex")
    if not executable:
        raise LedgerStoreError("codex executable not found on PATH")
    model = os.environ.get("REASONING_REPAIR_MODEL", DEFAULT_REPAIR_MODEL)
    effort = os.environ.get("REASONING_REPAIR_EFFORT", DEFAULT_REPAIR_EFFORT)
    command = [
        executable,
        "exec",
        "--model",
        model,
        "-c",
        f'model_reasoning_effort="{effort}"',
        "--sandbox",
        "read-only",
        "--cd",
        scratch,
        "--skip-git-repo-check",
        "--output-last-message",
        str(Path(scratch) / "out.txt"),
        "-",
    ]
    # No tools, no web, no ledger — pure text-to-text, like the reviewer.
    return sandbox.wrap(command, rw=[Path(scratch), sandbox.codex_home_bind()], chdir=Path(scratch))


def _run_model(prompt: str, *, stop_event: threading.Event | None = None) -> str | None:
    if stop_event is not None and stop_event.is_set():
        return None
    timeout = float(os.environ.get("REASONING_REPAIR_TIMEOUT", DEFAULT_REPAIR_TIMEOUT))
    try:
        with tempfile.TemporaryDirectory(prefix="latex-repair-") as scratch:
            from . import sandbox

            command = _repair_command(scratch)
            if stop_event is None:
                result = subprocess.run(command, input=prompt, capture_output=True, text=True, timeout=timeout, env=sandbox.codex_env())
                returncode, stdout = result.returncode, result.stdout
            else:
                with subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=sandbox.codex_env(),
                    start_new_session=True,
                ) as process:
                    deadline = time.monotonic() + timeout
                    data = prompt
                    completed = False
                    try:
                        while True:
                            remaining = deadline - time.monotonic()
                            if stop_event.is_set() or remaining <= 0:
                                return None
                            try:
                                stdout, _ = process.communicate(input=data, timeout=min(0.1, remaining))
                                completed = True
                                break
                            except subprocess.TimeoutExpired:
                                data = None
                        returncode = process.returncode
                    finally:
                        # The process group is owned by this invocation. Kill
                        # descendants too, including those retaining its pipes.
                        if not completed:
                            with suppress(ProcessLookupError):
                                os.killpg(process.pid, signal.SIGTERM)
                            try:
                                process.communicate(timeout=1)
                            except subprocess.TimeoutExpired:
                                with suppress(ProcessLookupError):
                                    os.killpg(process.pid, signal.SIGKILL)
                                process.communicate(timeout=1)
            if returncode != 0 or (stop_event is not None and stop_event.is_set()):
                return None
            out = Path(scratch) / "out.txt"
            return out.read_text(encoding="utf-8") if out.exists() else stdout
    except (OSError, subprocess.SubprocessError, LedgerStoreError):
        return None


def _extract_json(text: str) -> dict | None:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    body = text[start : end + 1]
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        # TeX backslashes are not valid JSON escapes; double lone ones.
        token = re.compile(r'\\(u[0-9a-fA-F]{4}|["\\/bfnrt])|\\')
        repaired = token.sub(lambda m: m.group(0) if m.group(1) else r"\\", body)
        try:
            return json.loads(repaired, strict=False)
        except json.JSONDecodeError:
            return None


# ------------------------------------------------------------------- stages

_STAGE_A_PROMPT = """\
You fix LaTeX math so it renders in KaTeX, changing only formatting, not
meaning. Below are math snippets (with their render mode and the KaTeX
error each produced) that failed. Rewrite each to render in KaTeX while
preserving its mathematical meaning exactly: replace undefined macros
with their standard KaTeX equivalent (e.g. \\RR -> \\mathbb{R}), fix bad
syntax, drop unsupported text-layout constructs. Do not add commentary.

Return ONLY a JSON object: {"fixed": ["<snippet 0>", "<snippet 1>", ...]}
with one corrected snippet per input, in order.

Snippets:
%s
"""

_STAGE_B_PROMPT = """\
The message below has unbalanced or misplaced math delimiters (\\( \\),
\\[ \\], $, $$), so text and math are inverted in places. Fix ONLY the
delimiters: insert, move, or remove delimiter characters so every math
span is well-formed and the right parts are math. You MUST NOT change,
add, remove, or reword any other character — every non-delimiter
character stays exactly as-is, in the same order. Return ONLY the
corrected message, nothing else.

Message:
%s
"""


def _stage_a(text: str, *, stop_event: threading.Event | None = None) -> str:
    if stop_event is not None and stop_event.is_set():
        return text
    failures = latex_check.check_text(text)
    if not failures:
        return text
    spans = latex_check.extract_spans(text)
    failing = []
    for span in spans:
        if stop_event is not None and stop_event.is_set():
            return text
        if latex_check.check_text(_wrap(span)):
            failing.append(span)
    if not failing:
        return text
    listing = "\n".join(f"[{i}] mode={'display' if s.display else 'inline'}: {s.text}" for i, s in enumerate(failing))
    response = _run_model(_STAGE_A_PROMPT % listing, stop_event=stop_event)
    if not response:
        return text
    parsed = _extract_json(response)
    if not parsed or not isinstance(parsed.get("fixed"), list):
        return text
    fixed = parsed["fixed"]
    # Splice back-to-front so earlier offsets stay valid; only keep a fix
    # that actually renders in the span's mode.
    result = text
    for span, replacement in zip(reversed(failing), reversed(fixed[: len(failing)]), strict=False):
        if stop_event is not None and stop_event.is_set():
            return text
        if not isinstance(replacement, str):
            continue
        candidate = latex_check.Span(replacement, span.display, span.start, span.end)
        if latex_check.check_text(_wrap(candidate)):
            continue  # the "fix" still fails; leave the original span
        opened, closed = _delims(span, text)
        result = result[: span.start] + opened + replacement + closed + result[span.end :]
    return result


def _stage_b(text: str, *, stop_event: threading.Event | None = None) -> str | None:
    response = _run_model(_STAGE_B_PROMPT % text, stop_event=stop_event)
    if response is None:
        return None
    candidate = response.strip("\n")
    # Hard guard: only delimiters may have moved.
    if _strip_delims(candidate) != _strip_delims(text):
        return None
    if has_unbalanced_delimiters(candidate):
        return None
    return candidate


def _wrap(span: latex_check.Span) -> str:
    d = "\\[%s\\]" if span.display else "\\(%s\\)"
    return d % span.text


def _delims(span: latex_check.Span, text: str) -> tuple[str, str]:
    """The exact opening/closing delimiters the span used, so a spliced
    fix keeps the author's `$`-vs-`\\(` style."""
    head = text[span.start : span.start + 2]
    if head == "\\[":
        return "\\[", "\\]"
    if head == "\\(":
        return "\\(", "\\)"
    if text[span.start : span.start + 2] == "$$":
        return "$$", "$$"
    return "$", "$"


# The prose fields of a verification's evidence, in display-priority
# order. Repaired copies of these land in the record's evidence_repair.
EVIDENCE_TEXT_FIELDS = ("report", "summary", "text", "strongest_objection")


def repair_evidence(evidence: dict, *, stop_event: threading.Event | None = None) -> dict | None:
    """KaTeX-repaired copies of a verification evidence's prose fields, or
    None if nothing needed (or could be) repaired."""
    repaired: dict[str, str] = {}
    for field in EVIDENCE_TEXT_FIELDS:
        if stop_event is not None and stop_event.is_set():
            return None
        value = evidence.get(field)
        if isinstance(value, str):
            fixed = repair_text(value, stop_event=stop_event)
            if fixed is not None:
                repaired[field] = fixed
    return repaired or None


def _severity(text: str) -> tuple[int, int]:
    """How broken `text` is: unbalanced delimiters dominate (they hide
    spans from the checker entirely), then the count of render failures.
    Lower is better."""
    return (1 if has_unbalanced_delimiters(text) else 0, len(latex_check.check_text(text)))


def repair_text(text: str, *, stop_event: threading.Event | None = None) -> str | None:
    """Repaired text if it renders strictly better than the input, else
    None (nothing to fix, or nothing safe could be done)."""
    if not text or not latex_check.available() or (stop_event is not None and stop_event.is_set()):
        return None
    before = _severity(text)
    if before == (0, 0) or (stop_event is not None and stop_event.is_set()):
        return None  # nothing to fix
    working = text
    # Stage B first: unbalanced delimiters hide spans, so fixing them is a
    # prerequisite for Stage A to even see the real content.
    if has_unbalanced_delimiters(text):
        fixed = _stage_b(text, stop_event=stop_event)
        if fixed is not None:
            working = fixed
    working = _stage_a(working, stop_event=stop_event)
    if stop_event is not None and stop_event.is_set():
        return None
    if working != text and _severity(working) < before:
        return working
    return None
