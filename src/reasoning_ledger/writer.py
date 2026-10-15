"""The writer subagent: a persistent, resumable codex session that
turns ledger-verified material into a standalone mathematical document.

Unlike a reviewer (one-shot, fresh context by design), a writer KEEPS
its context: its first activation drafts the document, and each later
activation — fed the verbatim output of a writeup review plus a new
brief from the main agent — resumes the same codex session
(`codex exec resume`), so it remembers its own draft and choices.
Between activations the writer is "paused": it holds a handle
(writer_id) the main agent uses to continue or terminate it.

States live in <ledger_root>/.writers/, one JSON per writer, exactly
like review states: the MCP server (in the sandbox) writes requests,
and the workbench announcer (outside) spawns the detached runner,
announces returns, and kills cancelled runners.

Lifecycle: pending -> running -> paused -> (pending -> running ->
paused)* -> terminated, with "failed" on a runner error (the main
agent may continue a failed writer: the session, if any, survives) and
"cancelling" while the workbench kills a running one.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

from .derive import derive_state
from .review import (
    DEFAULT_REVIEW_EFFORT,
    DEFAULT_REVIEW_MODEL,
    _bibliography_block,
    _exec_event,
    _sources_dir,
    load_review,
)
from .store import LedgerStore, LedgerStoreError

DEFAULT_WRITER_TIMEOUT = 7200.0  # seconds of wall clock for one activation

_WRITERS_DIR = ".writers"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def writers_dir(ledger_root: Path) -> Path:
    return Path(ledger_root) / _WRITERS_DIR


def _ensure_writers_dir(ledger_root: Path) -> Path:
    directory = writers_dir(ledger_root)
    directory.mkdir(parents=True, exist_ok=True)
    gitignore = Path(ledger_root) / ".gitignore"
    line = f"{_WRITERS_DIR}/"
    existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    if line not in existing.split("\n"):
        gitignore.write_text(
            (existing.rstrip("\n") + "\n" if existing else "") + line + "\n",
            encoding="utf-8",
        )
    return directory


def settled_dir(ledger_root: Path) -> Path:
    """Terminated writers' states live in a subdirectory so the
    announcer's tick only globs the (almost always tiny) live set."""
    return writers_dir(ledger_root) / "settled"


def _state_path(ledger_root: Path, writer_id: str) -> Path:
    active = writers_dir(ledger_root) / f"{writer_id}.json"
    if active.exists():
        return active
    settled = settled_dir(ledger_root) / f"{writer_id}.json"
    if settled.exists():
        return settled
    return active


def _write_state(ledger_root: Path, state: dict) -> None:
    path = _state_path(ledger_root, state["writer_id"])
    # Unique temp name: concurrent writers of the same state (announcer
    # vs interrupt handler) must not share a rename source.
    handle, tmp = tempfile.mkstemp(
        dir=path.parent, prefix=path.stem + ".", suffix=".tmp"
    )
    with os.fdopen(handle, "w", encoding="utf-8") as out:
        out.write(json.dumps(state, ensure_ascii=False, indent=1) + "\n")
    os.replace(tmp, path)


def _load_dir(directory: Path) -> list[dict]:
    if not directory.is_dir():
        return []
    states = []
    for path in sorted(directory.glob("wrt_*.json")):
        try:
            states.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return states


def load_writers(ledger_root: Path) -> list[dict]:
    """All writer states, settled included, oldest first."""
    states = _load_dir(writers_dir(ledger_root)) + _load_dir(settled_dir(ledger_root))
    return sorted(states, key=lambda s: s.get("started_at", ""))


def active_writers(ledger_root: Path) -> list[dict]:
    """Writer states still needing attention (everything not settled:
    paused and failed writers stay live — they can be continued), oldest
    first. Polled every few seconds, so it must stay O(live writers)."""
    return sorted(
        _load_dir(writers_dir(ledger_root)), key=lambda s: s.get("started_at", "")
    )


def settle_writer(ledger_root: Path, writer_id: str) -> None:
    """Move a terminated writer's state (and runner log) out of the
    announcer's scan set, folding its usage into the settled rollup."""
    from .review import accrue_settled_usage

    state = load_writer(ledger_root, writer_id)
    target = settled_dir(ledger_root)
    target.mkdir(parents=True, exist_ok=True)
    moved = False
    for suffix in (".json", ".log"):
        path = writers_dir(ledger_root) / f"{writer_id}{suffix}"
        if path.exists():
            path.replace(target / path.name)
            moved = True
    if moved and state is not None:
        accrue_settled_usage(writers_dir(ledger_root), state)


def load_writer(ledger_root: Path, writer_id: str) -> dict | None:
    path = _state_path(ledger_root, writer_id)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def update_writer(ledger_root: Path, writer_id: str, **fields) -> dict | None:
    state = load_writer(ledger_root, writer_id)
    if state is None:
        return None
    state.update(fields)
    _write_state(ledger_root, state)
    return state


# ---------------------------------------------------------------- preamble

# Prefilled into a not-yet-existing .tex target so every produced paper
# starts from the same class and package set.
TEX_PREAMBLE = r"""\documentclass[11pt,reqno]{amsart}

\usepackage{amsmath}
\usepackage{amsthm}
\usepackage{thmtools}
\usepackage{microtype}
\usepackage[style=alphabetic,maxbibnames=99,minbibnames=99,maxalphanames=99,minalphanames=99,backref=true,useprefix=true]{biblatex}
\usepackage[bookmarksdepth=3,linktoc=all]{hyperref}
\usepackage[capitalize]{cleveref}

\declaretheorem[numberwithin=section]{theorem}
\declaretheorem[sibling=theorem]{lemma}
\declaretheorem[sibling=theorem]{proposition}
\declaretheorem[sibling=theorem]{corollary}
\declaretheorem[sibling=theorem,style=definition]{definition}
\declaretheorem[sibling=theorem,style=remark]{remark}

\addbibresource{%(stem)s.bib}

\begin{document}

\title{}
\author{}
\begin{abstract}
\end{abstract}
\maketitle
\tableofcontents

\section{Introduction}

\printbibliography

\end{document}
"""


# ------------------------------------------------------------------ prompt

_WRITER_PROMPT = """\
You are a mathematical writer. Your job is to turn the ledger material
below — statements, proofs, and definitions verified by an adversarial
review process — into ONE standalone document: a mathematical paper (or
note) that a reader who was not present for any of this can read from
start to finish.

Write the document into the file named under "The document" below; its
current contents are on disk (a prefilled LaTeX preamble for a fresh
.tex file, or your own previous draft when you are resumed). Edit it in
place. Your FINAL message is a report to the requesting agent — what
you wrote, how you structured it, what choices you made, anything left
open — never the document itself.

Requirements:

1. **Self-contained.** Modulo references to the literature, the
   document must stand alone: every non-standard term defined, every
   result stated precisely before use, no reference to the ledger, this
   process, or any conversation. Ledger ids (clm_…, prf_…) must not
   appear; translate the dependency structure into ordinary paper
   numbering (Lemma 2.1, Theorem 3.4, …).
2. **Readable, in the style of a mathematical paper.** Definitions
   before use, results in theorem environments, arguments in exposition.
   Break excessively long proofs into subresults (lemmas, propositions)
   with clear standalone statements — a proof that runs for pages
   without a break is a defect, not a feat.
3. **An introduction that orients a stranger.** Explain the results and
   the relevant background — including the historical background, which
   you should establish with a literature search (verify any background
   supplied in the brief independently; do not copy it on faith) — and
   give an overview of the proof. Someone unfamiliar with this work
   must be able to read the introduction alone and know what is proved,
   why it matters, where it sits in the literature, and how the proof
   goes.
4. **Faithful to the material.** The mathematics comes from the ledger
   material below. Do not silently strengthen, weaken, or "fix"
   results; if you find what you believe is an error, note it in your
   final report and write the document around the honest state.
5. **LaTeX.** When the document is a .tex file, keep the provided
   preamble (hyperref, cleveref, amsmath, amsthm, thmtools, biblatex
   with the alphabetic style, microtype), put the bibliography in the
   .bib file the preamble names, cite with biblatex commands, and
   cross-reference with \\cref. Compile with pdflatex (and biber) in
   your shell until the document builds cleanly; fix what the log
   flags.

You have web search for the literature and a shell in the document's
directory. Cached copies of the project's sources, when listed in the
bibliography below, are local files you can read directly.
"""


def _artifacts_dir(store: LedgerStore) -> Path:
    """The project's shared artifacts directory, as laid out by the
    webapp (artifacts/ beside the ledger)."""
    return store.root.parent / "artifacts"


def _normalize_filename(filename: str) -> str:
    """The document's path relative to artifacts/, from either an
    "artifacts/…" path or a bare relative one."""
    relative = filename.strip().strip("/")
    if relative.startswith("artifacts/"):
        relative = relative[len("artifacts/") :]
    if not relative or any(
        part in ("", ".", "..") or part.startswith(".")
        for part in Path(relative).parts
    ):
        raise LedgerStoreError(f"unusable document filename: {filename!r}")
    return relative


def _material_block(store: LedgerStore, slugs: list[str]) -> str:
    """The requested entries — exactly those, no transitive closure:
    each claim's statement, status, citation, and every live proof with
    its premise ids. The full closure of a deep result can exceed the
    writer's whole context window, so the requesting model curates the
    list (premise ids in proofs tell it what else the writer may need).
    Ledger ids are for the writer's orientation; the document itself
    must not carry them."""
    view = store.load()
    state = derive_state(view)
    seen: list[str] = []
    for slug in slugs:
        if slug in view.claim_heads:
            claim_id = slug
        elif slug in view.proof_heads:
            claim_id = view.proof_heads[slug]["claim_id"]
        else:
            raise LedgerStoreError(f"unknown ledger entry: {slug}")
        if claim_id not in seen:
            seen.append(claim_id)
    blocks = []
    for claim_id in seen:
        head = view.claim_heads[claim_id]
        revision = view.claim_revisions[head["current_revision_id"]]
        status = state.claim_status.get(claim_id, {})
        label = "Definition" if revision["kind"] == "definition" else "Claim"
        lines = [
            f"### {label} [{claim_id}] — {status.get('status', '?')}"
            + (f" ({status.get('authority')})" if status.get("authority") else ""),
            revision["statement_text"],
        ]
        if revision.get("reference_id"):
            ref_head = view.reference_heads.get(revision["reference_id"])
            if ref_head:
                ref = view.reference_revisions[ref_head["current_revision_id"]]
                cite = f"Cited: {ref['author']}, {ref['title']}"
                if revision.get("locator"):
                    cite += f", {revision['locator']}"
                lines.append(cite)
        for proof_head in view.proof_heads.values():
            if proof_head["claim_id"] != claim_id or proof_head["abandoned"]:
                continue
            revision_p = view.proof_revisions[proof_head["current_revision_id"]]
            report = state.proof_reports.get(proof_head["proof_id"], {})
            premises = ", ".join(
                p.get("claim_id", f"(unresolved: {p.get('unresolved')})")
                for p in revision_p["premises"]
            )
            lines.append(
                f"Proof [{proof_head['proof_id']}]"
                + (f" — argument {report['argument']}" if report.get("argument") else "")
                + (f"; premises: {premises}" if premises else "; no premises")
                + f"\n\n{revision_p['body']['text']}"
            )
        blocks.append("\n\n".join(lines))
    return "## Ledger material\n\n" + "\n\n".join(blocks)


def compose_writer_input(store: LedgerStore, state: dict) -> str:
    """The full prompt for one activation. The first activation gets the
    writer's role, the material, and the brief; later activations —
    resuming the same codex session, which already holds all of that —
    get the verbatim reviewer output and the new brief."""
    target = _artifacts_dir(store) / state["filename"]
    if state.get("continuation") and not state.get("session_id"):
        # Continued, but no session ever started (stopped or failed
        # while pending): there is no context to resume, so this is a
        # first activation again, under the continuation's brief.
        state = {**state, "brief": state["continuation"]["brief"], "continuation": None}
    if state.get("continuation"):
        continuation = state["continuation"]
        parts = ["You are resumed on the same document: " + str(target) + "."]
        review = (
            load_review(store.root, continuation["review_id"])
            if continuation.get("review_id")
            else None
        )
        if review is not None:
            issues = review.get("issues") or []
            parts.append(
                "## Independent review of your draft (verbatim)\n\n"
                + (
                    "Issues:\n"
                    + "\n".join(f"- {issue}" for issue in issues)
                    + "\n\n"
                    if issues
                    else ""
                )
                + (review.get("report") or "(no report)")
            )
        parts.append("## New brief\n\n" + continuation["brief"])
        return "\n\n".join(parts)
    bibliography = _bibliography_block(store.load(), _sources_dir(store))
    return (
        f"{_WRITER_PROMPT}\n---\n\n"
        f"## The document\n\nWrite to: {target}\n\n"
        f"## Brief\n\n{state['brief']}\n\n"
        + (f"{bibliography}\n\n" if bibliography else "")
        + f"{_material_block(store, state['slugs'])}\n"
    )


# ----------------------------------------------------------------- request

def request_writer(
    store: LedgerStore,
    *,
    brief: str,
    filename: str,
    slugs: list[str],
    requested_by: dict | None = None,
) -> dict:
    """Validate and persist a pending writer. Like reviews, the runner
    is spawned outside the sandbox by the workbench announcer. A fresh
    .tex target is prefilled with the standard preamble here, so the
    writer always starts from the same class and packages."""
    if not brief or not brief.strip():
        raise LedgerStoreError("the writer needs a non-empty brief")
    if not slugs:
        raise LedgerStoreError("the writer needs at least one ledger entry (slugs)")
    relative = _normalize_filename(filename)
    compose_probe = _material_block(store, list(slugs))  # validates the slugs
    del compose_probe
    session = (requested_by or {}).get("session")
    for state in active_writers(store.root):
        if (
            state.get("status") in ("pending", "running", "paused", "cancelling")
            and state.get("filename") == relative
            and (state.get("requested_by") or {}).get("session") == session
        ):
            raise LedgerStoreError(
                f"writer {state['writer_id']} already owns {relative} "
                f"(status: {state['status']}). Continue it with "
                f"writer_continue(writer_id={state['writer_id']!r}, ...) or "
                f"terminate it first."
            )
    target = _artifacts_dir(store) / relative
    if relative.endswith(".tex") and not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            TEX_PREAMBLE % {"stem": Path(relative).stem}, encoding="utf-8"
        )
    _ensure_writers_dir(store.root)
    writer_id = f"wrt_{uuid.uuid4().hex[:16]}"
    state = {
        "writer_id": writer_id,
        "status": "pending",
        "activation": 1,
        "brief": brief,
        "filename": relative,
        "slugs": list(slugs),
        "model": os.environ.get("REASONING_WRITER_MODEL")
        or os.environ.get("REASONING_REVIEW_MODEL", DEFAULT_REVIEW_MODEL),
        "effort": os.environ.get("REASONING_WRITER_EFFORT")
        or os.environ.get("REASONING_REVIEW_EFFORT", DEFAULT_REVIEW_EFFORT),
        "requested_by": requested_by,
        "started_at": _now(),
        "announced": False,
    }
    _write_state(store.root, state)
    return state


def continue_writer(
    ledger_root: Path,
    writer_id: str,
    *,
    brief: str,
    review_id: str | None = None,
) -> dict:
    """Reactivate a paused (or failed) writer: the next activation
    resumes its codex session with the named review's verbatim output
    (when given) followed by the new brief."""
    if not brief or not brief.strip():
        raise LedgerStoreError("the writer needs a non-empty brief to continue")
    state = load_writer(ledger_root, writer_id)
    if state is None:
        raise LedgerStoreError(f"unknown writer: {writer_id}")
    if state.get("status") not in ("paused", "failed"):
        raise LedgerStoreError(
            f"writer {writer_id} is {state.get('status')} — only a paused "
            "or failed writer can be continued"
        )
    if review_id is not None and load_review(ledger_root, review_id) is None:
        raise LedgerStoreError(f"unknown review: {review_id}")
    return update_writer(
        ledger_root,
        writer_id,
        status="pending",
        activation=int(state.get("activation", 1)) + 1,
        continuation={"brief": brief, "review_id": review_id},
        announced=False,
        error=None,
        finished_at=None,
    )


def pause_writer(ledger_root: Path, writer_id: str) -> dict | None:
    """The stop button's writer half: halt work without losing the
    handle. A pending writer parks as paused on the spot; a running one
    moves to "pausing" for the workbench (out of the sandbox) to kill
    its runner and settle to paused. Its codex session — recorded the
    moment the first activation started — survives for writer_continue.
    Returns None when there is nothing to stop (already paused, failed,
    or terminated)."""
    state = load_writer(ledger_root, writer_id)
    if state is None:
        raise LedgerStoreError(f"unknown writer: {writer_id}")
    status = state.get("status")
    if status == "pending":
        return update_writer(
            ledger_root, writer_id, status="paused", live=None,
            finished_at=_now(), announced=True,
        )
    if status == "running":
        return update_writer(
            ledger_root, writer_id, status="pausing", live=None,
            finished_at=_now(), announced=True,
        )
    return None


def terminate_writer(ledger_root: Path, writer_id: str) -> dict:
    """End a writer. A paused/failed/pending one terminates on the
    spot; a running one moves to "cancelling" for the workbench (out of
    the sandbox) to kill and settle."""
    state = load_writer(ledger_root, writer_id)
    if state is None:
        raise LedgerStoreError(f"unknown writer: {writer_id}")
    status = state.get("status")
    if status in ("terminated",):
        raise LedgerStoreError(f"writer {writer_id} is already terminated")
    if status == "running":
        return update_writer(
            ledger_root, writer_id, status="cancelling", live=None,
            finished_at=_now(), announced=True,
        )
    return update_writer(
        ledger_root, writer_id, status="terminated", live=None,
        finished_at=_now(), announced=True,
    )


def spawn_writer_runner(ledger_root: Path, writer_id: str) -> None:
    """Mark a pending writer running and launch its detached runner.
    Must be called from outside any codex sandbox namespace (see
    review.spawn_runner)."""
    update_writer(ledger_root, writer_id, status="running", spawned_at=_now())
    runner = shutil.which("reasoning-ledger") or str(
        Path(sys.executable).with_name("reasoning-ledger")
    )
    log_path = _state_path(ledger_root, writer_id).with_suffix(".log")
    with open(log_path, "ab") as log:
        subprocess.Popen(
            [runner, "--root", str(ledger_root), "writer-run", writer_id],
            stdout=log,
            stderr=log,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            env=os.environ.copy(),
        )


# ------------------------------------------------------------------ runner

def _codex_env() -> dict[str, str] | None:
    from . import sandbox

    return sandbox.codex_env()


def _writer_command(
    prompt_file: str,
    scratch: str,
    state: dict,
    *,
    artifacts: Path,
    sources: Path | None,
) -> list[str]:
    override = os.environ.get("REASONING_WRITER_COMMAND")
    if override:
        return [*shlex.split(override), prompt_file]
    from . import sandbox

    executable = shutil.which("codex")
    if not executable:
        raise LedgerStoreError("codex executable not found on PATH")
    session_id = state.get("session_id")
    resuming = state.get("continuation") and session_id
    command = [executable, "exec"]
    if resuming:
        command += ["resume", session_id]
    command += [
        "--json",
        "--model",
        state["model"],
        "-c",
        f'model_reasoning_effort="{state["effort"]}"',
        "-c",
        'model_reasoning_summary="detailed"',
        # exec resume takes no --sandbox flag; the config key works for
        # both forms.
        "-c",
        'sandbox_mode="workspace-write"',
        "-c",
        "tools.web_search=true",
        "-c",
        "sandbox_workspace_write.network_access=true",
        "--skip-git-repo-check",
        "--output-last-message",
        str(Path(scratch) / "report.txt"),
        "-",
    ]
    # The writer works in the artifacts directory (its document lives
    # there) with its scratch and codex home; the ledger itself stays
    # invisible — its material arrives in the prompt.
    return sandbox.wrap(
        command,
        rw=[Path(scratch), sandbox.codex_home_bind(), artifacts],
        ro=[sources] if sources is not None else [],
        chdir=artifacts,
    )


def _ended(ledger_root: Path, writer_id: str) -> bool:
    state = load_writer(ledger_root, writer_id)
    return state is not None and state.get("status") in (
        "cancelling",
        "terminated",
    )


def run_writer(store: LedgerStore, writer_id: str) -> dict:
    """Drive one writer activation to its pause; returns the state."""
    ledger_root = store.root
    state = load_writer(ledger_root, writer_id)
    if state is None:
        raise LedgerStoreError(f"unknown writer: {writer_id}")
    update_writer(ledger_root, writer_id, pid=os.getpid())
    timeout = float(
        os.environ.get("REASONING_WRITER_TIMEOUT", DEFAULT_WRITER_TIMEOUT)
    )
    usage_total: dict = dict(state.get("usage") or {})
    steps: list[str] = list(state.get("steps") or [])
    try:
        prompt = compose_writer_input(store, state)
        artifacts = _artifacts_dir(store)
        artifacts.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="ledger-writer-") as scratch:
            prompt_file = str(Path(scratch) / "prompt.md")
            Path(prompt_file).write_text(prompt, encoding="utf-8")
            command = _writer_command(
                prompt_file,
                scratch,
                state,
                artifacts=artifacts,
                sources=_sources_dir(store),
            )
            stdout_lines: list[str] = []
            plain_lines: list[str] = []
            last_message_text: str | None = None
            timed_out: list[bool] = []
            stderr_path = Path(scratch) / "writer-stderr.log"
            with (
                open(prompt_file, encoding="utf-8") as prompt_in,
                open(stderr_path, "w+", encoding="utf-8") as err_out,
            ):
                proc = subprocess.Popen(
                    command,
                    stdin=prompt_in,
                    stdout=subprocess.PIPE,
                    stderr=err_out,
                    text=True,
                    env=_codex_env(),
                )

                def _expire() -> None:
                    timed_out.append(True)
                    proc.kill()

                watchdog = threading.Timer(timeout, _expire)
                watchdog.start()
                try:
                    assert proc.stdout is not None
                    for line in proc.stdout:
                        stdout_lines.append(line)
                        event = _exec_event(line)
                        if event is None:
                            plain_lines.append(line)
                            continue
                        if (
                            event.get("type") == "thread.started"
                            and event.get("thread_id")
                            and not state.get("session_id")
                        ):
                            # the handle that makes later activations
                            # resume THIS session
                            state["session_id"] = event["thread_id"]
                            update_writer(
                                ledger_root,
                                writer_id,
                                session_id=event["thread_id"],
                            )
                        elif event.get("type") == "turn.completed" and isinstance(
                            event.get("usage"), dict
                        ):
                            for key, value in event["usage"].items():
                                if isinstance(value, (int, float)):
                                    usage_total[key] = usage_total.get(key, 0) + int(
                                        value
                                    )
                        elif event.get("type") == "item.completed":
                            item = event.get("item")
                            if not isinstance(item, dict):
                                continue
                            if item.get("type") == "reasoning" and item.get("text"):
                                steps.append(item["text"])
                                update_writer(
                                    ledger_root,
                                    writer_id,
                                    live=item["text"],
                                    steps=list(steps),
                                )
                            elif item.get("type") == "agent_message":
                                last_message_text = item.get("text")
                    proc.wait()
                finally:
                    watchdog.cancel()
                err_out.seek(0)
                stderr_text = err_out.read()
            if timed_out:
                raise LedgerStoreError(f"writer timed out after {int(timeout)}s")
            if proc.returncode != 0:
                tail = (stderr_text or "".join(stdout_lines) or "").strip()[-2000:]
                raise LedgerStoreError(f"writer exited with {proc.returncode}: {tail}")
            report_file = Path(scratch) / "report.txt"
            if report_file.exists():
                report = report_file.read_text(encoding="utf-8")
            elif last_message_text is not None:
                report = last_message_text
            else:
                # non-JSONL writer (REASONING_WRITER_COMMAND override);
                # event lines stay out of the report either way
                report = "".join(plain_lines) or "".join(stdout_lines)
    except Exception as error:  # recorded, never raised past the runner
        if _ended(ledger_root, writer_id):
            return load_writer(ledger_root, writer_id)
        update_writer(
            ledger_root,
            writer_id,
            status="failed",
            error=str(error),
            finished_at=_now(),
            live=None,
            usage=usage_total or None,
            announced=False,
        )
        return load_writer(ledger_root, writer_id)
    if _ended(ledger_root, writer_id):
        return load_writer(ledger_root, writer_id)
    update_writer(
        ledger_root,
        writer_id,
        status="paused",
        report=report.strip(),
        continuation=None,
        finished_at=_now(),
        live=None,
        usage=usage_total or None,
        announced=False,
    )
    return load_writer(ledger_root, writer_id)
