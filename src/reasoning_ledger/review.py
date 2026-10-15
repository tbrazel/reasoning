"""Independent adversarial review of ledger claims and proofs.

The requesting agent chooses only *what* gets reviewed. Everything the
reviewer sees is composed here, from the ledger records alone — the
requester's framing never reaches the reviewer, and the reviewer runs in
a fresh `codex exec` context with its own model (REASONING_REVIEW_MODEL,
default gpt-5.6-sol at REASONING_REVIEW_EFFORT, default xhigh).

Reviews are asynchronous. `request_review` writes a state file under
`<ledger_root>/.reviews/` (gitignored — mutable state, not a record) and
spawns a detached runner (`reasoning-ledger review-run`); the runner
drives the reviewer, parses its structured verdict, and records the
verification through the store with the `reviewer` marker — the only
path that mints positive review-authority verdicts.

Verdict mapping: a proof review records verified/flawed/inconclusive
verbatim. A claim (citation) review records established on "verified"
and records no refutation on "flawed": a failed citation does not refute
the claim. An inconclusive citation check records nothing; the review
state carries the outcome. A flawed definition review records refuted.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import traceback
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from .store import LedgerStore, LedgerStoreError

DEFAULT_REVIEW_MODEL = "gpt-5.6-sol"
DEFAULT_REVIEW_EFFORT = "xhigh"
DEFAULT_REVIEW_TIMEOUT = 3600.0  # seconds of wall clock for one review

_REVIEWS_DIR = ".reviews"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def reviews_dir(ledger_root: Path) -> Path:
    return Path(ledger_root) / _REVIEWS_DIR


def _ensure_reviews_dir(ledger_root: Path) -> Path:
    directory = reviews_dir(ledger_root)
    directory.mkdir(parents=True, exist_ok=True)
    # Mutable state must stay out of the record history (the store
    # commits the whole tree).
    gitignore = Path(ledger_root) / ".gitignore"
    line = f"{_REVIEWS_DIR}/"
    existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    if line not in existing.split("\n"):
        gitignore.write_text(
            (existing.rstrip("\n") + "\n" if existing else "") + line + "\n",
            encoding="utf-8",
        )
    return directory


def settled_dir(ledger_root: Path) -> Path:
    """Terminal, fully-announced review states live in a subdirectory so
    the announcer's tick only globs the (almost always tiny) active set
    instead of re-parsing every review that ever ran."""
    return reviews_dir(ledger_root) / "settled"


def _state_path(ledger_root: Path, review_id: str) -> Path:
    active = reviews_dir(ledger_root) / f"{review_id}.json"
    if active.exists():
        return active
    settled = settled_dir(ledger_root) / f"{review_id}.json"
    if settled.exists():
        return settled
    return active


def _write_state(ledger_root: Path, state: dict) -> None:
    path = _state_path(ledger_root, state["review_id"])
    # Unique temp name: the announcer and an interrupt handler can
    # rewrite the same state concurrently (last writer wins), and a
    # shared .tmp made one of them fail its rename.
    handle, tmp = tempfile.mkstemp(
        dir=path.parent, prefix=path.stem + ".", suffix=".tmp"
    )
    with os.fdopen(handle, "w", encoding="utf-8") as out:
        out.write(json.dumps(state, ensure_ascii=False, indent=1) + "\n")
    os.replace(tmp, path)


def _load_dir(directory: Path, prefix: str) -> list[dict]:
    if not directory.is_dir():
        return []
    states = []
    for path in sorted(directory.glob(f"{prefix}_*.json")):
        try:
            states.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return states


def load_reviews(ledger_root: Path) -> list[dict]:
    """All review states, settled included, oldest first."""
    states = _load_dir(reviews_dir(ledger_root), "rev") + _load_dir(
        settled_dir(ledger_root), "rev"
    )
    return sorted(states, key=lambda s: s.get("started_at", ""))


def active_reviews(ledger_root: Path) -> list[dict]:
    """Review states still needing attention (not yet settled), oldest
    first. The announcer polls this every few seconds, so it must stay
    O(live work), not O(all reviews ever)."""
    return sorted(
        _load_dir(reviews_dir(ledger_root), "rev"),
        key=lambda s: s.get("started_at", ""),
    )


def settled_usage(directory: Path, session: str) -> dict:
    """The summed token usage of a session's settled states, accrued
    when each state settles so live reads never re-parse settled files."""
    try:
        data = json.loads((directory / "settled-usage.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    entry = data.get(session) or {}
    return {
        "input": int(entry.get("input", 0) or 0),
        "cached": int(entry.get("cached", 0) or 0),
        "output": int(entry.get("output", 0) or 0),
    }


def accrue_settled_usage(directory: Path, state: dict) -> None:
    """Fold a state's token usage into the per-session settled rollup."""
    session = (state.get("requested_by") or {}).get("session")
    if not session:
        return
    spent = state.get("usage") or {}
    path = directory / "settled-usage.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    entry = data.setdefault(session, {"input": 0, "cached": 0, "output": 0})
    entry["input"] += spent.get("input_tokens", 0) or 0
    entry["cached"] += spent.get("cached_input_tokens", 0) or 0
    entry["output"] += spent.get("output_tokens", 0) or 0
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    tmp.replace(path)


def settle_review(ledger_root: Path, review_id: str) -> None:
    """Move a terminal, fully-announced review's state (and runner log)
    out of the announcer's scan set, folding its usage into the settled
    rollup."""
    state = load_review(ledger_root, review_id)
    target = settled_dir(ledger_root)
    target.mkdir(parents=True, exist_ok=True)
    moved = False
    for suffix in (".json", ".log"):
        path = reviews_dir(ledger_root) / f"{review_id}{suffix}"
        if path.exists():
            path.replace(target / path.name)
            moved = True
    if moved and state is not None:
        accrue_settled_usage(reviews_dir(ledger_root), state)


def load_review(ledger_root: Path, review_id: str) -> dict | None:
    path = _state_path(ledger_root, review_id)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


_DIGEST_FIELDS = (
    "review_id",
    "subject",
    "status",
    "verdict",
    "started_at",
    "finished_at",
    "verification_id",
    "error",
)


def _digest_entry(state: dict) -> dict:
    return {key: state[key] for key in _DIGEST_FIELDS if state.get(key) is not None}


def review_digest(ledger_root: Path, recent: int = 30) -> dict:
    """In-flight reviews plus the most recent settled verdicts, without
    report bodies. The full history can run to megabytes; reading one
    report goes through load_review by id. Only the `recent` newest
    settled files are even parsed."""
    running = [_digest_entry(s) for s in active_reviews(ledger_root)]
    directory = settled_dir(ledger_root)
    settled_files: list = []
    if directory.is_dir():
        settled_files = sorted(
            directory.glob("rev_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    settled = []
    for path in settled_files[:recent]:
        try:
            settled.append(_digest_entry(json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, json.JSONDecodeError):
            continue
    digest = {"reviews": running, "recent_settled": settled}
    if len(settled_files) > recent:
        digest["settled_omitted"] = (
            f"{len(settled_files) - recent} older settled reviews omitted — "
            "read any single review in full with review_id"
        )
    return digest


def update_review(ledger_root: Path, review_id: str, **fields) -> dict | None:
    state = load_review(ledger_root, review_id)
    if state is None:
        return None
    state.update(fields)
    _write_state(ledger_root, state)
    return state


RunnerLiveness = Literal["alive", "dead", "unknown"]


def _matches_runner_command(command: str, review_id: str) -> bool:
    return "review-run" in command and review_id in command


def _proc_runner_liveness(pid: int, review_id: str) -> RunnerLiveness:
    """Inspect a runner through procfs when the host provides it."""
    proc_root = Path("/proc")
    if not proc_root.is_dir():
        return "unknown"
    try:
        command = (
            (proc_root / str(pid) / "cmdline")
            .read_bytes()
            .decode(errors="replace")
            .replace("\0", " ")
        )
    except FileNotFoundError:
        return "dead"
    except OSError:
        return "unknown"
    return "alive" if _matches_runner_command(command, review_id) else "dead"


def _ps_runner_liveness(pid: int, review_id: str) -> RunnerLiveness:
    """Portable POSIX fallback used on macOS and procfs-less Linux hosts."""
    executable = shutil.which("ps")
    if executable is None:
        return "unknown"
    try:
        result = subprocess.run(
            [executable, "-ww", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    if result.returncode == 0:
        return (
            "alive"
            if _matches_runner_command(result.stdout, review_id)
            else "dead"
        )

    # A restricted `ps` is not evidence that the process died. `kill(pid, 0)`
    # distinguishes a missing process from a live process whose command line
    # we could not inspect, while leaving PID-reuse detection conservative.
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return "dead"
    except (PermissionError, OSError):
        return "unknown"
    return "unknown"


def runner_liveness(pid: object, review_id: str) -> RunnerLiveness:
    """Return whether a PID is the named review runner.

    ``unknown`` is deliberately distinct from ``dead``: inability to inspect
    a process must not convert an active review into a terminal failure.
    """
    try:
        process_id = int(pid)
    except (TypeError, ValueError):
        return "dead"
    if process_id <= 0:
        return "dead"
    status = _proc_runner_liveness(process_id, review_id)
    if status != "unknown":
        return status
    return _ps_runner_liveness(process_id, review_id)


def running_review_is_orphaned(
    state: dict,
    *,
    now: datetime | None = None,
    startup_grace_seconds: float = 300,
) -> bool:
    """Whether a running review is known to have lost its runner."""
    pid = state.get("pid")
    if pid:
        return runner_liveness(pid, state["review_id"]) == "dead"
    # Spawned but not yet claimed by a runner: allow startup time before
    # declaring that the launch itself was lost.
    spawned = state.get("spawned_at") or state.get("started_at") or ""
    try:
        started = datetime.fromisoformat(spawned)
    except (TypeError, ValueError):
        return True
    current = now or datetime.now(UTC)
    return (current - started).total_seconds() > startup_grace_seconds


# ------------------------------------------------------------------ prompt

_PROOF_PROMPT = (
"""\
You are an independent adversarial reviewer of a mathematical proof.
You were spawned with fresh context, deliberately: you have no stake in
this proof and no history with its author. Your job is to find the flaw
if there is one. A correct rejection and a correct verification are
equally successful reviews; an incorrect verification is the worst
possible outcome.

## What you are reviewing

You are reviewing ONE proof of ONE claim, stated below. The premises
listed under "Givens" are tracked separately by the ledger, with their
own proofs and reviews: for the purpose of THIS verdict, take each of
them as fact exactly as stated. Your verdict judges only the inferences
between the givens and the claim. Definitions listed are stipulative.
Each given is labeled with its ledger id (clm_…); the proof cites
premises inline by these ids ("by clm_…"), so match each citation to
its labeled given.

Two distinct situations, handled differently:

- The argument uses MORE than a given actually states (a stronger
  bound, a dropped hypothesis, a different quantifier): that is a gap
  in THIS proof and affects your verdict.
- A given itself seems wrong — false as stated, incoherent, or
  suspicious: that does NOT change your verdict here, but you must
  report it under "given_concerns" so the ledger can chase it. You are
  not asked to re-derive the givens, but you are never required to
  stay silent about one you distrust.

The proof must be self-contained modulo its givens: every step must
either be routine (and you must actually perform the routine check, not
wave at it) or follow from the givens. If a step needs a fact that is
neither a given nor routine, that is a gap: the verdict is "flawed"
(if the gap is essential or the step is false) or "inconclusive" (if
you cannot determine whether the gap is bridgeable). Do not extend the
benefit of the doubt: "the author surely meant..." is how false results
get established.

Definitional completeness is part of self-containment, and undefined
terms are how assumptions get smuggled in:

- Every term and notation used by the claim or the proof must be either
  standard (with its standard meaning) or defined — in the claim's own
  statement, in a given, or in a listed definition. Call out every term
  that is neither: if it hides a load-bearing assumption the proof is
  "flawed", and an entry you cannot pin down is at best "inconclusive".
- A claim about a constructed or named object must carry the object's
  definition among its givens (or define it in the statement itself),
  exactly as sublemmas must be pinned premises. Never reconstruct a
  plausible definition from context and review against that: what is
  not written into the entry or its dependencies does not exist.

## How to work

1. Restate the claim in your own words; check the statement itself is
   well-posed given the definitions, and that every non-standard term
   it uses is actually defined here.
2. Enumerate the proof obligations: every assertion the argument makes
   that is not literally a given.
3. Check each obligation, in writing. Use your shell for computations
   and web search for literature checks whenever they would settle a
   point; a five-minute computation beats a paragraph of plausibility.
4. Construct the strongest objection you can — an edge case, a
   quantifier slip, a wrong constant, a circularity — and see if the
   proof survives it.

## Verdict

Your FINAL message delivers the verdict in two parts. Its FIRST line is
one single-line JSON object — no code fence, nothing before it, the
whole object on that one line:

"""
    '{"verdict": "verified" | "flawed" | "inconclusive", "obligations": [{"statement": "...", "status": "'
    'holds" | "fails" | "unclear", "note": "..."}], "strongest_objection": "the strongest objection you f'
    'ound and why it does or does not sink the proof", "given_concerns": ["any doubts about the givens th'
    'emselves — empty if none"]}'
"""

Every line after the first is the report itself: your full review as
ordinary TeX-capable markdown — real newlines, unescaped TeX, no JSON —
covering the argument's structure, each obligation's check, exactly
where it fails if it fails, and a closing section on any doubts about
the givens. Do not put the report inside the JSON object.

The report renders with KaTeX: use \\( ... \\) inline math within KaTeX's
supported command set (no minipage/tabular or other text-layout LaTeX).

"verified" requires every obligation to hold with every routine check
actually performed. Any obligation "fails" means "flawed". Anything you
could not settle means "inconclusive" — an honest inconclusive is worth
more than a hopeful verification. given_concerns never change this
proof's verdict; they are a report to the ledger's operators.
"""
)

_CITATION_PROMPT = (
"""\
You are an independent reviewer checking a citation. A research ledger
contains the claim below, recorded as a known result from the
literature with the stated citation. Your job is to determine whether
the cited source actually states (or directly implies, by its own
explicitly stated results) the claim AS WRITTEN — mind the exact
hypotheses, quantifiers, and constants. Use web search to locate the
source; use your shell if checking an edge case helps. Do not extend
the benefit of the doubt to paraphrases that strengthen the result.
Also check the claim reads stand-alone: every term it uses must be
standard or defined in the entry or its listed definitions — an
undefined non-standard term means the recorded claim does not pin down
what the source is being cited for.

Access to the source is a hard requirement, not a formality. You have
verified nothing unless you have READ the actual text that is being
cited — the theorem statement at the cited spot, with its hypotheses.
If the reference lists a cached copy of the source, read that file. If
the source is paywalled, the link is broken, or you can only reach an
abstract, a review, a citing paper, or a different version whose
numbering may differ: you do not have access, and your verdict CANNOT
be "verified" — report "inconclusive" and say exactly what you could
not reach, so a copy can be obtained and cached for the next attempt.

Your FINAL message delivers the verdict in two parts. Its FIRST line is
one single-line JSON object — no code fence, nothing before it:

"""
    '{"verdict": "verified" | "flawed" | "inconclusive", "source_access": "full" | "partial" | "none", "o'
    'bligations": [{"statement": "the source states the claim as written", "status": "holds" | "fails" | '
    '"unclear", "note": "..."}], "strongest_objection": "the closest mismatch between claim and source"}'
"""

"source_access" states what you actually read: "full" — the cited text
itself, at the cited location; "partial" — some of the source but not
enough to settle the citation; "none" — you could not read the source.

Every line after the first is the report itself, as ordinary
TeX-capable markdown (real newlines, unescaped TeX): what you found —
where the source states it, or how it differs, or why you could not
verify. Do not put the report inside the JSON object.

The report renders with KaTeX: use \\( ... \\) inline math within KaTeX's
supported command set (no minipage/tabular or other text-layout LaTeX).

"verified": the source states the claim as recorded — requires
source_access "full". "flawed": the source says something relevantly
different (explain the difference). "inconclusive": you could not
locate or adequately inspect the source.
"""
)


_DEFINITION_PROMPT = (
"""\
You are an independent reviewer of a mathematical definition recorded
in a research ledger. Definitions are stipulative — the author may
define whatever they like, and you are not judging whether it is the
standard notion or a good idea. You are judging whether it actually
pins down what it purports to, without smuggling anything in. Undefined
notation is how assumptions sneak into a ledger: a term that sounds
standard but is not, a symbol never introduced, a "the" that quietly
presumes existence and uniqueness.

Check, in writing:

1. **Well-posedness.** The definition determines its object or property
   unambiguously: every case is covered, any recursion is well-founded,
   and anything defined via representatives or choices is independent
   of them (or the definition says it is not).
2. **No undefined terms.** Every term and notation is either genuinely
   standard (used with its standard meaning — check, don't assume) or
   defined, in this definition itself or in one of the other ledger
   definitions listed below. Never fill a gap with the "obvious"
   intended meaning: what is not written does not exist.
3. **No smuggled claims.** Phrases like "the unique x such that", "the
   natural map", "the limit" presume existence, uniqueness, or
   convergence facts. Each such presumption must be either immediate,
   or explicitly flagged by the definition as a convention; a
   load-bearing unproved presumption is a flaw.

Use your shell for computations and web search for standardness or
literature checks whenever they would settle a point.

Your FINAL message delivers the verdict in two parts. Its FIRST line is
one single-line JSON object — no code fence, nothing before it:

"""
    '{"verdict": "verified" | "flawed" | "inconclusive", "obligations": [{"statement": "...", "status": "'
    'holds" | "fails" | "unclear", "note": "..."}], "strongest_objection": "the strongest objection you f'
    'ound and why it does or does not sink the definition"}'
"""

Every line after the first is the report itself: your full review as
ordinary TeX-capable markdown (real newlines, unescaped TeX, no JSON).
Do not put the report inside the JSON object.

The report renders with KaTeX: use \\( ... \\) inline math within KaTeX's
supported command set.

"verified" means the definition is well-posed and free of undefined or
smuggled matter. Any failed obligation means "flawed". Anything you
could not settle means "inconclusive".
"""
)


_WRITEUP_PROMPT = """\
You are an independent reviewer of a mathematical writeup — a note or
paper meant to be read on its own, by someone who was not present for its
creation. Read the whole document, then judge it on four axes:

1. **Correctness.** The mathematics is right. Check the definitions are
   coherent, the statements are true as written (mind hypotheses,
   quantifiers, constants, edge cases), and every argument actually
   establishes what it claims — construct the strongest objection you can
   and use your shell or web search to settle any point a computation or
   a literature check would decide. A wrong or unjustified step is the
   most serious kind of issue.
2. **Style and readability.** It reads like a mathematical paper or
   note: definitions before use, results stated precisely, arguments in
   exposition rather than in fragments, consistent notation. Flag prose
   that reads like chat, a scratchpad, or a task list rather than a
   finished note — and flag unwieldy proofs: a proof that runs on for
   pages without being broken into subresults (lemmas, propositions
   with standalone statements) is a defect, even when correct.
3. **Self-containment.** Every non-standard term and notation is defined
   in the document or is genuinely standard; there are no dangling
   references to results, sections, or objects that the document does not
   itself provide. A reader with ordinary background can follow it start
   to finish without external material. When the document has an
   introduction, it must alone orient a reader unfamiliar with the
   work: what is proved, why it matters, the relevant background
   (historical included), and an overview of the proof — check it
   stands on its own, not merely that it exists.
4. **No out-of-place matter.** It never refers to a previous version of
   itself ("as before", "in the earlier draft", "now updated"), to the
   process that produced it (the ledger, this review, the conversation,
   the user), or to anything else that does not belong in a standalone
   note — no TODOs, no meta-commentary, no leftover instructions.

Check the document's citations as part of correctness: for each source
it cites for a load-bearing statement, try to read the cited text.
Start with the project bibliography below: when it lists a cached copy,
that is a local file — read it directly instead of searching the web.
Only search online for sources with no cached copy. List in issues
every citation whose source you could not access — readers must know
which citations rest unverified — and every one whose source does not
say what the document cites it for.

Be concrete: quote the offending passage and say what is wrong. Do not
rewrite the document; report what a careful reviewer would flag. Your
objections are advisory — the author has context you lack and may
override you — but a correctness objection is the one you should press
hardest.

Your FINAL message delivers the review in two parts. Its FIRST line is
one single-line JSON object — no code fence, nothing before it:

{"issues": ["one concrete issue per entry, quoting the passage", "..."]}

Every line after the first is the report itself: your overall
assessment, organized by the four axes above, as ordinary TeX-capable
markdown (real newlines, unescaped TeX). Do not put the report inside
the JSON object.

An empty "issues" list means the document is correct and fit for
independent consumption. The report renders with KaTeX: use \\( ... \\)
inline math within KaTeX's supported command set.
"""


def _sources_dir(store: LedgerStore) -> Path:
    """The project's cached-source documents, as laid out by the webapp
    (sources/ beside the ledger). Reviews of a bare ledger (no webapp
    project around it) simply find nothing here."""
    return store.root.parent / "sources"


def _reference_block(
    view, reference_id: str, locator: str | None, sources_dir: Path | None = None
) -> str:
    head = view.reference_heads.get(reference_id)
    if head is None:
        return "(reference record missing)"
    ref = view.reference_revisions[head["current_revision_id"]]
    lines = [f"author: {ref['author']}", f"title: {ref['title']}"]
    for field in ("year", "arxiv", "url", "doi", "isbn", "notes"):
        if ref[field]:
            lines.append(f"{field}: {ref[field]}")
    cached = ref.get("file")
    if cached and sources_dir is not None:
        copy = sources_dir / cached
        if copy.is_file():
            lines.append(
                f"cached copy of the source (read this file): {copy}"
            )
    if locator:
        lines.append(f"locator (where in the source): {locator}")
    return "\n".join(lines)


def _bibliography_block(view, sources_dir: Path | None) -> str | None:
    """Every reference in the project's bibliography, with the local path
    of any cached copy — handed to reviewers so checking a source never
    starts with hunting for it."""
    if not view.reference_heads:
        return None
    lines = []
    for reference_id in sorted(view.reference_heads):
        head = view.reference_heads[reference_id]
        ref = view.reference_revisions[head["current_revision_id"]]
        bits = [f"{ref['author']}, {ref['title']}"]
        if ref.get("year"):
            bits.append(str(ref["year"]))
        for field in ("arxiv", "url", "doi", "isbn"):
            if ref.get(field):
                bits.append(f"{field}:{ref[field]}")
        line = f"- [{reference_id}] " + " · ".join(bits)
        cached = ref.get("file")
        if cached and sources_dir is not None:
            copy = sources_dir / cached
            if copy.is_file():
                line += f"\n  cached copy (a local file — read it directly): {copy}"
        lines.append(line)
    return (
        "## Project bibliography\n\n"
        "Sources already recorded for this project; cached copies are "
        "local files you can open without any web access.\n\n"
        + "\n".join(lines)
    )


def compose_review_input(
    store: LedgerStore, subject: dict, *, document: str | None = None
) -> dict:
    """Build the reviewer's entire input.

    Returns {"prompt": str, "mode": "proof"|"claim"|"writeup",
    "subject": {...}}. Proof/citation reviews read only ledger records
    (plus the bibliography); a writeup review reads only the supplied
    document (plus the bibliography).
    """
    source_directory = None if getattr(store, 'remote_sources', False) else _sources_dir(store)
    if subject.get("kind") == "writeup":
        if not document or not document.strip():
            raise LedgerStoreError("a writeup review needs a non-empty document")
        bibliography = _bibliography_block(store.load(), source_directory)
        prompt = (
            f"{_WRITEUP_PROMPT}\n---\n\n"
            + (f"{bibliography}\n\n" if bibliography else "")
            + f"## The document\n\n{document}\n"
        )
        return {"prompt": prompt, "mode": "writeup", "subject": subject}
    view = store.load()
    claim_id = subject["claim_id"]
    if "proof_id" in subject:
        proof_revision = view.proof_revisions[subject["revision_id"]]
        target = view.claim_revisions[proof_revision["target_revision_id"]]
        givens = []
        for premise in proof_revision["premises"]:
            if "unresolved" in premise:
                raise LedgerStoreError(
                    "cannot review a proof with unresolved premises — backfill "
                    f"first (unresolved: {premise['unresolved']!r})"
                )
            pinned = view.claim_revisions[premise["revision_id"]]
            label = (
                "Definition (stipulative)"
                if pinned["kind"] == "definition"
                else "Given"
            )
            # Labeled with its ledger id: the proof text cites premises
            # inline by id ("by clm_…"), and the reviewer must be able
            # to resolve those citations.
            givens.append(
                f"{label} [{premise['claim_id']}]: {pinned['statement_text']}"
            )
        givens_text = "\n\n".join(givens) if givens else "(none — the proof claims to need no premises)"
        bibliography = _bibliography_block(view, source_directory)
        prompt = (
            f"{_PROOF_PROMPT}\n"
            f"---\n\n"
            f"## Claim under review\n\n{target['statement_text']}\n\n"
            f"## Givens (fact for this verdict; report doubts separately)\n\n{givens_text}\n\n"
            + (f"{bibliography}\n\n" if bibliography else "")
            + f"## The proof\n\n{proof_revision['body']['text']}\n"
        )
        return {"prompt": prompt, "mode": "proof", "subject": subject}
    revision = view.claim_revisions[subject["revision_id"]]
    if revision.get("kind") == "definition" and not revision.get("reference_id"):
        # A stipulative definition: reviewed for well-posedness and
        # smuggled/undefined notation. (A definition WITH a reference is
        # a cited one and gets the citation check below instead.)
        others = []
        for other_id in sorted(view.claim_heads):
            if other_id == claim_id:
                continue
            other = view.claim_revisions[
                view.claim_heads[other_id]["current_revision_id"]
            ]
            if other.get("kind") == "definition":
                others.append(f"[{other_id}] {other['statement_text']}")
        others_text = "\n\n".join(others) if others else "(none)"
        bibliography = _bibliography_block(view, source_directory)
        prompt = (
            f"{_DEFINITION_PROMPT}\n"
            f"---\n\n"
            f"## Definition under review\n\n{revision['statement_text']}\n\n"
            f"## Other definitions recorded in the ledger\n\n{others_text}\n"
            + (f"\n{bibliography}\n" if bibliography else "")
        )
        return {"prompt": prompt, "mode": "definition", "subject": subject}
    if not revision.get("reference_id"):
        raise LedgerStoreError(
            f"claim {claim_id} has no reference; review one of its proofs instead"
        )
    prompt = (
        f"{_CITATION_PROMPT}\n"
        f"---\n\n"
        f"## Claim as recorded\n\n{revision['statement_text']}\n\n"
        f"## Reference\n\n"
        f"{_reference_block(view, revision['reference_id'], revision['locator'], source_directory)}\n"
    )
    return {"prompt": prompt, "mode": "claim", "subject": subject}


# ------------------------------------------------------------------ request

def resolve_subject(
    store: LedgerStore, *, claim_id: str | None, proof_id: str | None
) -> dict:
    if (claim_id is None) == (proof_id is None):
        raise LedgerStoreError("request a review of exactly one claim_id or proof_id")
    if proof_id is not None:
        head = store.proof_head(proof_id)
        return {
            "claim_id": head["claim_id"],
            "proof_id": proof_id,
            "revision_id": head["current_revision_id"],
        }
    head = store.claim_head(claim_id)
    return {"claim_id": claim_id, "revision_id": head["current_revision_id"]}


def request_review(
    store: LedgerStore,
    *,
    claim_id: str | None = None,
    proof_id: str | None = None,
    requested_by: dict | None = None,
) -> dict:
    """Validate the subject and persist a pending state.

    Deliberately does NOT spawn the runner: the requester is usually the
    ledger MCP server, which lives inside the app-server's bwrap
    namespace — codex is not on its PATH there, and any child it spawned
    would die with the chat's namespace instead of surviving it. A
    process outside the sandbox (the workbench's review watcher, or the
    operator CLI) picks pending reviews up via spawn_runner."""
    subject = resolve_subject(store, claim_id=claim_id, proof_id=proof_id)
    compose_review_input(store, subject)  # validate up front (unresolved premises, no citation)
    return _persist_request(store.root, subject, requested_by)


def request_writeup_review(
    store: LedgerStore,
    *,
    document: str,
    document_name: str,
    requested_by: dict | None = None,
) -> dict:
    """Persist a pending review of a standalone writeup. Advisory: it
    records no verification, only feedback the author may act on or
    override. Like other reviews, the runner is spawned out of the
    sandbox (see spawn_runner).

    One per document: concurrent reviews of the same writeup are pure
    waste (any edit obsoletes the older run). `document_name` (the
    file's artifacts path) is required — it identifies the document, so
    reviews of *different* documents run concurrently while a second
    review of the same one is refused until the first finishes or is
    cancelled, and it names the review in the workbench UI."""
    if not document_name or not document_name.strip():
        raise LedgerStoreError(
            "document_name is required: pass the document's artifacts "
            "path (e.g. 'artifacts/note.tex') so the review is "
            "identifiable in the workbench and per-document concurrency "
            "can be enforced"
        )
    session = (requested_by or {}).get("session")
    for state in active_reviews(store.root):
        if (
            state.get("subject", {}).get("kind") != "writeup"
            or state.get("status") not in ("pending", "running")
            or (state.get("requested_by") or {}).get("session") != session
        ):
            continue
        existing_name = state.get("subject", {}).get("name")
        if document_name and existing_name and document_name != existing_name:
            continue  # a different document: runs concurrently
        raise LedgerStoreError(
            f"a writeup review ({state['review_id']}"
            + (f", on {existing_name}" if existing_name else "")
            + ") is already running for this document. Wait for its "
            "feedback, or — if you have modified the document since — "
            "cancel it with "
            f"ledger_cancel_review(review_id={state['review_id']!r}) "
            "and request a fresh one."
        )
    subject = {"kind": "writeup"}
    if document_name:
        subject["name"] = document_name
    compose_review_input(store, subject, document=document)  # validate non-empty
    return _persist_request(store.root, subject, requested_by, document=document)


def cancel_review(ledger_root: Path, review_id: str) -> dict:
    """Ask for a pending/running review to be cancelled.

    Callable from inside the sandbox (the MCP server), which cannot kill
    the detached runner itself: the state moves to "cancelling" and the
    workbench's watcher — outside the namespace — kills the runner and
    settles the state to "cancelled"."""
    state = load_review(ledger_root, review_id)
    if state is None:
        raise LedgerStoreError(f"unknown review: {review_id}")
    if state.get("status") not in ("pending", "running", "cancelling"):
        raise LedgerStoreError(
            f"review {review_id} is already {state.get('status')} — "
            "nothing to cancel"
        )
    return update_review(
        ledger_root, review_id, status="cancelling", live=None, finished_at=_now()
    )


def _persist_request(
    ledger_root: Path,
    subject: dict,
    requested_by: dict | None,
    *,
    document: str | None = None,
) -> dict:
    _ensure_reviews_dir(ledger_root)
    review_id = f"rev_{uuid.uuid4().hex[:16]}"
    state = {
        "review_id": review_id,
        "subject": subject,
        "status": "pending",
        "model": os.environ.get("REASONING_REVIEW_MODEL", DEFAULT_REVIEW_MODEL),
        "effort": os.environ.get("REASONING_REVIEW_EFFORT", DEFAULT_REVIEW_EFFORT),
        "requested_by": requested_by,
        "started_at": _now(),
        "announced": False,
    }
    if document is not None:
        state["document"] = document
    _write_state(ledger_root, state)
    return state


def spawn_runner(ledger_root: Path, review_id: str) -> None:
    """Mark a pending review running and launch its detached runner.

    Must be called from outside any codex sandbox namespace: the runner
    needs codex on PATH and must outlive the requesting chat. Marks the
    state first so a polling caller never double-spawns."""
    update_review(ledger_root, review_id, status="running", spawned_at=_now())
    runner = shutil.which("reasoning-ledger") or str(
        Path(sys.executable).with_name("reasoning-ledger")
    )
    log_path = _state_path(ledger_root, review_id).with_suffix(".log")
    with open(log_path, "ab") as log:
        subprocess.Popen(
            [runner, "--root", str(ledger_root), "review-run", review_id],
            stdout=log,
            stderr=log,
            stdin=subprocess.DEVNULL,
            start_new_session=True,  # survives the requesting session
            env=os.environ.copy(),
        )


# ------------------------------------------------------------------- runner

def _codex_env() -> dict[str, str] | None:
    from . import sandbox

    return sandbox.codex_env()


def _reviewer_command(
    prompt_file: str, scratch: str, state: dict, sources: Path | None = None
) -> list[str]:
    override = os.environ.get("REASONING_REVIEW_COMMAND")
    if override:
        return [*shlex.split(override), prompt_file]
    from . import sandbox

    executable = shutil.which("codex")
    if not executable:
        raise LedgerStoreError("codex executable not found on PATH")
    command = [
        executable,
        "exec",
        # JSONL events on stdout: the runner mirrors the reviewer's
        # reasoning steps into the review state as they stream.
        "--json",
        "--model",
        state["model"],
        "-c",
        f'model_reasoning_effort="{state["effort"]}"',
        "-c",
        'model_reasoning_summary="detailed"',
        "--sandbox",
        "workspace-write",
        "-c",
        "tools.web_search=true",
        "-c",
        "sandbox_workspace_write.network_access=true",
        "--cd",
        scratch,
        "--skip-git-repo-check",
        "--output-last-message",
        str(Path(scratch) / "verdict.json"),
        "-",
    ]
    # The reviewer needs codex's home (binary, auth, rollouts), its own
    # scratch, and read access to the project's cached sources (so a
    # citation check can read a paywalled paper the user uploaded) — but
    # deliberately NOT the ledger or the reasoning venv, which stay
    # invisible to it.
    return sandbox.wrap(
        command,
        rw=[Path(scratch), sandbox.codex_home_bind()],
        ro=[sources] if sources is not None else [],
        chdir=Path(scratch),
    )


# One valid JSON escape (consumed whole) or one lone backslash. Matching
# tokenizes left to right, so the second character of a valid escape is
# never re-examined as an escape opener of its own.
_ESCAPE_TOKEN = re.compile(r'\\(u[0-9a-fA-F]{4}|["\\/bfnrt])|\\')


def _repair_escapes(body: str) -> str:
    """Double every backslash that does not begin a valid JSON escape,
    turning literal TeX (`\\(`, `\\ge`, `\\underline`) into parseable
    strings while leaving already-valid escapes exactly as they are."""
    return _ESCAPE_TOKEN.sub(
        lambda match: match.group(0) if match.group(1) else r"\\", body
    )


def _json_from_model(text: str) -> dict:
    """The single JSON object in a reviewer's final message, tolerating a
    stray code fence and the TeX backslashes math carries."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in the reviewer's final message")
    body = text[start : end + 1]
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        # strict=False additionally tolerates literal newlines/tabs.
        return json.loads(_repair_escapes(body), strict=False)


def _split_header(text: str) -> tuple[dict, str] | None:
    """The reviewer message split into its leading JSON header object and
    the markdown report after it. The contract asks for a single-line
    header; a pretty-printed (multi-line) object is tolerated, as is a
    code fence around the whole message. A full-JSON legacy message
    parses too: the object consumes everything and the report stays
    inside it. None when the message does not start with an object."""
    text = text.strip()
    fenced = re.match(r"```(?:json)?\s*\n(.*?)\n?```\s*$", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    if not text.startswith("{"):
        return None
    first_line, _, rest = text.partition("\n")
    for candidate in (first_line, _repair_escapes(first_line)):
        try:
            header = json.loads(candidate, strict=False)
        except json.JSONDecodeError:
            continue
        if isinstance(header, dict):
            return header, rest.strip()
        return None
    try:
        header, end = json.JSONDecoder(strict=False).raw_decode(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(header, dict):
        return None
    return header, text[end:].strip()


def _parse_verdict(text: str) -> dict:
    split = _split_header(text)
    if split is None:
        # object buried in surrounding prose — last-resort extraction
        verdict = _json_from_model(text)
    else:
        header, body = split
        verdict = dict(header)
        if body:
            verdict["report"] = body
    if verdict.get("verdict") not in ("verified", "flawed", "inconclusive"):
        raise ValueError(f"invalid verdict: {verdict.get('verdict')!r}")
    verdict["report"] = str(verdict.get("report", ""))
    return verdict


def _parse_writeup(text: str) -> dict:
    split = _split_header(text)
    if split is None:
        parsed = _json_from_model(text)
        body = ""
    else:
        parsed, body = split
    issues = parsed.get("issues")
    if not isinstance(issues, list):
        issues = []
    report = body or str(parsed.get("report", ""))
    return {"issues": [str(i) for i in issues], "report": report}


def _exec_event(line: str) -> dict | None:
    """A `codex exec --json` event parsed from one stdout line, else None."""
    line = line.strip()
    if not line.startswith("{"):
        return None
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None
    return event if isinstance(event, dict) else None


def _cancelled(ledger_root: Path, review_id: str) -> bool:
    state = load_review(ledger_root, review_id)
    return state is not None and state.get("status") in ("cancelling", "cancelled")


def run_review(store: LedgerStore, review_id: str) -> dict:
    """Drive one review to completion; returns the final state."""
    ledger_root = store.root
    state = load_review(ledger_root, review_id)
    if state is None:
        raise LedgerStoreError(f"unknown review: {review_id}")
    # The runner's pid, so a cancellation (from the workbench, outside
    # the sandbox) can kill this process group.
    update_review(ledger_root, review_id, pid=os.getpid())
    timeout = float(os.environ.get("REASONING_REVIEW_TIMEOUT", DEFAULT_REVIEW_TIMEOUT))
    reviewer_usage: dict | None = None
    is_writeup = state["subject"].get("kind") == "writeup"
    try:
        composed = compose_review_input(
            store, state["subject"], document=state.get("document")
        )
        with tempfile.TemporaryDirectory(prefix="ledger-review-") as scratch:
            prompt_file = str(Path(scratch) / "prompt.md")
            Path(prompt_file).write_text(composed["prompt"], encoding="utf-8")
            command = _reviewer_command(
                prompt_file, scratch, state, sources=_sources_dir(store)
            )
            stdout_lines: list[str] = []
            last_message_text: str | None = None
            reasoning_steps: list[str] = []
            timed_out: list[bool] = []
            stderr_path = Path(scratch) / "reviewer-stderr.log"
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
                            continue
                        if event.get("type") == "turn.completed" and isinstance(
                            event.get("usage"), dict
                        ):
                            reviewer_usage = event["usage"]
                            continue
                        if event.get("type") != "item.completed":
                            continue
                        item = event.get("item")
                        if not isinstance(item, dict):
                            continue
                        # Mirror reasoning steps into the state as they
                        # stream, so the workbench can show the reviewer
                        # thinking: `steps` is the full list (the expanded
                        # view), `live` the latest (the collapsed snippet).
                        # The last agent message is the verdict fallback when
                        # --output-last-message is absent.
                        if item.get("type") == "reasoning" and item.get("text"):
                            reasoning_steps.append(item["text"])
                            update_review(
                                ledger_root,
                                review_id,
                                live=item["text"],
                                steps=list(reasoning_steps),
                            )
                        elif item.get("type") == "agent_message":
                            last_message_text = item.get("text")
                    proc.wait()
                finally:
                    watchdog.cancel()
                err_out.seek(0)
                stderr_text = err_out.read()
            if timed_out:
                raise LedgerStoreError(f"reviewer timed out after {int(timeout)}s")
            if proc.returncode != 0:
                tail = (stderr_text or "".join(stdout_lines) or "").strip()[-2000:]
                raise LedgerStoreError(
                    f"reviewer exited with {proc.returncode}: {tail}"
                )
            last_message = Path(scratch) / "verdict.json"
            if last_message.exists():
                raw = last_message.read_text(encoding="utf-8")
            elif last_message_text is not None:
                raw = last_message_text
            else:
                # Non-JSONL reviewer (REASONING_REVIEW_COMMAND override):
                # its stdout is the verdict itself.
                raw = "".join(stdout_lines)
        try:
            verdict = _parse_writeup(raw) if is_writeup else _parse_verdict(raw)
        except ValueError as error:
            # Keep a slice of what the reviewer actually said: the scratch
            # dir is gone by the time anyone reads the failure.
            snippet = " ".join(raw.split())[:300]
            raise LedgerStoreError(
                f"unparseable reviewer verdict ({error}): {snippet!r}"
            ) from error
        if composed["mode"] == "claim" and verdict["verdict"] == "verified" and verdict.get("source_access") != "full":
            # A citation check is only as good as the reviewer's access
            # to the source: "verified" without attested full access is
            # downgraded — never recorded as passing. (Definition
            # reviews have no source to access; the guard is
            # citation-only.)
            verdict["verdict"] = "inconclusive"
            verdict["report"] = (
                "**Downgraded to inconclusive by the harness:** the "
                "reviewer reported \"verified\" without attesting full "
                "access to the cited source (source_access: "
                f"{verdict.get('source_access')!r}). Obtain the source "
                "(upload it to the project's sources) and re-review.\n\n"
                + verdict.get("report", "")
            )
    except Exception as error:  # recorded, never raised past the runner
        if _cancelled(ledger_root, review_id):
            # a cancelled runner's death throes must not repaint the state
            return load_review(ledger_root, review_id)
        update_review(
            ledger_root,
            review_id,
            status="failed",
            error=str(error),
            announced=False,
            finished_at=_now(),
            live=None,
            usage=reviewer_usage,
        )
        return load_review(ledger_root, review_id)

    if _cancelled(ledger_root, review_id):
        return load_review(ledger_root, review_id)
    if is_writeup:
        # Advisory: no verification, just the feedback for the author to
        # weigh (it has the user's context and may override).
        update_review(
            ledger_root,
            review_id,
            status="completed",
            error=None,
            announced=False,
            verdict=None,
            issues=verdict.get("issues", []),
            report=verdict.get("report", ""),
            finished_at=_now(),
            live=None,
            usage=reviewer_usage,
        )
        return load_review(ledger_root, review_id)

    reviewer = {
        "model": state["model"],
        "effort": state["effort"],
        "review_id": review_id,
    }
    evidence = {
        "kind": "adversarial-review",
        "report": verdict.get("report", ""),
        "obligations": verdict.get("obligations", []),
        "strongest_objection": verdict.get("strongest_objection", ""),
        "given_concerns": verdict.get("given_concerns", []),
    }
    recorded = None
    outcome = verdict["verdict"]
    subject = state["subject"]
    if "proof_id" in subject:
        recorded = store.add_verification(
            subject,
            verdict=outcome,
            authority="review",
            evidence=evidence,
            reviewer=reviewer,
        )
    elif outcome == "verified":
        recorded = store.add_verification(
            subject,
            verdict="established",
            authority="review",
            evidence=evidence,
            reviewer=reviewer,
        )
    elif outcome == "flawed" and composed['mode'] == 'definition':
        recorded = store.add_verification(
            subject,
            verdict="refuted",
            authority="review",
            evidence=evidence,
            reviewer=reviewer,
        )
    if recorded is not None:
        # Display-only KaTeX repair of the evidence prose, applied at
        # creation time (this runner is unsandboxed, so the repair model
        # is available). Cosmetic: a failure must never fail the review.
        try:
            from . import repair as repair_module

            repaired = repair_module.repair_evidence(recorded["evidence"])
            if repaired:
                store.set_evidence_repair(
                    recorded["subject"]["claim_id"],
                    recorded["verification_id"],
                    repaired,
                )
        except Exception:
            traceback.print_exc()
    # an inconclusive citation check records nothing: the claim simply
    # stays unestablished, and the state below carries the report
    update_review(
        ledger_root,
        review_id,
        status="completed",
        error=None,
        announced=False,
        verdict=outcome,
        report=verdict.get("report", ""),
        strongest_objection=verdict.get("strongest_objection", ""),
        given_concerns=verdict.get("given_concerns", []),
        source_access=verdict.get("source_access"),
        verification_id=recorded["verification_id"] if recorded else None,
        finished_at=_now(),
        live=None,
        usage=reviewer_usage,
    )
    return load_review(ledger_root, review_id)
