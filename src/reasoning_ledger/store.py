"""Per-project ledger store: a git repository of immutable records.

Layout under the ledger root (one per research project):

    claims/<claim_id>/head.json
    claims/<claim_id>/revisions/<revision_id>.json
    claims/<claim_id>/proofs/<proof_id>/head.json
    claims/<claim_id>/proofs/<proof_id>/revisions/<revision_id>.json
    claims/<claim_id>/verifications/<verification_id>.json
    .git/                (revision layer: every mutation is one commit)
    .lock                (untracked; serializes writers in one checkout)

Records are immutable content-addressed JSON; heads are the only files
rewritten. Git is the revision layer: each mutation is a commit whose
subject is the event, so `git log` is the journal, git's DAG is the
tamper evidence, and branches/worktrees give concurrent agents their own
checkouts that union-merge by construction (only head files can
conflict — a genuine semantic event, two competing restatements). The
working tree always carries the complete ledger, superseded revisions
included: the tree is the mathematics, the history is the sociology.
There is no database: the whole ledger loads into a LedgerView and state
is derived on demand.
"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import threading
from contextlib import contextmanager, suppress
from pathlib import Path

from .canonical import canonical_json, content_id
from .derive import LedgerState, LedgerView, derive_state, integrity_issues
from .records import (
    _UNSET,
    new_claim,
    new_proof,
    new_reference,
    new_verification,
    refine_claim,
    refine_reference,
    revise_proof,
    validate_record,
)


class LedgerStaleReadError(RuntimeError):
    """A compare-and-set write found the head had moved past the caller's
    last-read revision. Carries the current revision id."""

    def __init__(self, claim_id: str, current_revision_id: str):
        super().__init__(
            f"claim {claim_id} was refined since it was last read; "
            f"current revision is {current_revision_id}"
        )
        self.claim_id = claim_id
        self.current_revision_id = current_revision_id


class LedgerStoreError(RuntimeError):
    pass


class LedgerStore:
    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.claims_dir = self.root / "claims"
        self.references_dir = self.root / "references"
        # Loaded-view cache keyed by git head. Every mutation is a commit,
        # so the head hash is an exact invalidation key. Cached views are
        # shared read-only snapshots — payload code never mutates records.
        self._view_cache: tuple[str, LedgerView] | None = None
        # Derived state memo, keyed by view snapshot identity: deriving
        # over a 6k-record view costs ~0.6s and read payloads otherwise
        # recompute it per request.
        self._state_cache: tuple[LedgerView, LedgerState] | None = None
        self._order_cache: tuple[LedgerView, dict[str, int]] | None = None
        self._view_cache_lock = threading.Lock()
        # Single-flight for derivation: concurrent readers of a fresh view
        # wait for one computation instead of each running their own.
        self._derive_lock = threading.Lock()

    # ------------------------------------------------------------- plumbing

    @contextmanager
    def _lock(self, *, read_only: bool = False):
        self.root.mkdir(parents=True, exist_ok=True)
        with open(self.root / ".lock", "a+b") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            if not read_only:
                from .organization_ledger import linked_configuration

                if linked_configuration(self.root) is not None:
                    raise LedgerStoreError(
                        "this workbench uses the organization's canonical ledger; "
                        "publish through shared ledger tools or organization settings"
                    )
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    @staticmethod
    def _write_atomic(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)

    def _write_record(self, path: Path, record: dict) -> bool:
        """Write an immutable record; returns False when it already exists.

        An existing file must be byte-identical — the same content id with
        different bytes means corruption, never a legal update.
        """
        text = canonical_json(record)
        if path.exists():
            if path.read_text(encoding="utf-8") != text:
                raise LedgerStoreError(f"immutable record differs on disk: {path}")
            return False
        self._write_atomic(path, text)
        return True

    # ------------------------------------------------------------------ git

    def _git(self, *args: str, check: bool = True) -> str:
        result = subprocess.run(
            ["git", "-C", str(self.root), *args],
            text=True,
            capture_output=True,
            check=False,
        )
        if check and result.returncode != 0:
            raise LedgerStoreError(
                f"git {' '.join(args[:2])} failed: {result.stderr.strip()[-500:]}"
            )
        return result.stdout.strip()

    def _ensure_repo(self) -> None:
        """The ledger root is a git repository; every mutation is a commit.

        Records are write-once with content-addressed names, so branch
        merges are union-by-construction; only head files can conflict,
        and a head conflict is a genuine semantic event (two competing
        restatements). Git history is the journal — the working tree
        always carries the complete ledger, including superseded
        revisions.
        """
        if (self.root / ".git").exists():
            return
        self._git("init", "-q")
        gitignore = self.root / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text(".lock\n", encoding="utf-8")
        self._git("add", "-A")
        self._commit_staged("ledger-init", {})

    def _commit_staged(self, event: str, details: dict) -> None:
        subject_id = details.get("claim_id") or details.get("proof_id") or ""
        subject = f"{event} {subject_id}".strip()
        body = canonical_json({"event": event, **details}).strip()
        agent = os.environ.get("REASONING_LEDGER_AGENT", "reasoning-ledger")
        self._git(
            "-c",
            "user.name=reasoning-ledger",
            "-c",
            "user.email=ledger@local",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-q",
            "--author",
            f"{agent} <ledger@local>",
            "-m",
            f"{subject}\n\n{body}",
        )

    def _commit(self, event: str, **details) -> None:
        session = os.environ.get("REASONING_LEDGER_SESSION")
        if session:
            details = {**details, "session": session}
        self._git("add", "-A")
        self._commit_staged(event, details)

    def head_commit(self) -> str | None:
        if not (self.root / ".git").exists():
            return None
        return self._git("rev-parse", "HEAD", check=False) or None

    def journal_entries(self) -> list[dict]:
        """The operational journal, read from git history (oldest first)."""
        if not (self.root / ".git").exists():
            return []
        raw = self._git("log", "--reverse", "--format=%aI%x1f%B%x1e", check=False)
        entries = []
        for chunk in raw.split("\x1e"):
            chunk = chunk.strip()
            if not chunk:
                continue
            at, _, body = chunk.partition("\x1f")
            lines = body.strip().splitlines()
            payload: dict = {}
            for line in lines[1:]:
                line = line.strip()
                if line.startswith("{"):
                    with suppress(json.JSONDecodeError):
                        payload = json.loads(line)
                    break
            if "event" not in payload:
                payload["event"] = (lines[0].split(" ") if lines else ["unknown"])[0]
            payload["at"] = at
            entries.append(payload)
        return entries

    # ----------------------------------------------------------- claim path

    def _reference_dir(self, reference_id: str) -> Path:
        return self.references_dir / reference_id

    def reference_heads(self) -> set[str]:
        if not self.references_dir.exists():
            return set()
        return {
            d.name for d in self.references_dir.iterdir() if (d / "head.json").exists()
        }

    def reference_head(self, reference_id: str) -> dict:
        return self._read(self._reference_dir(reference_id) / "head.json", "reference-head")

    def reference_revision(self, reference_id: str, revision_id: str) -> dict:
        return self._read(
            self._reference_dir(reference_id) / "revisions" / f"{revision_id}.json",
            "reference-revision",
        )

    def _claim_dir(self, claim_id: str) -> Path:
        return self.claims_dir / claim_id

    def _claim_head_path(self, claim_id: str) -> Path:
        return self._claim_dir(claim_id) / "head.json"

    def _read(self, path: Path, schema_name: str) -> dict:
        if not path.exists():
            raise LedgerStoreError(f"missing record: {path}")
        record = json.loads(path.read_text(encoding="utf-8"))
        validate_record(record, schema_name)
        return record

    def _read_raw(self, path: Path) -> dict:
        """Bulk read for _load_view: records were validated when they
        were written (and fsck audits the tree), so the thousands-of-
        records view load skips per-record schema validation."""
        return json.loads(path.read_text(encoding="utf-8"))

    def claim_head(self, claim_id: str) -> dict:
        return self._read(self._claim_head_path(claim_id), "claim-head")

    def claim_revision(self, claim_id: str, revision_id: str) -> dict:
        return self._read(
            self._claim_dir(claim_id) / "revisions" / f"{revision_id}.json",
            "claim-revision",
        )

    def _find_proof(self, proof_id: str) -> tuple[str, dict]:
        for head_path in self.claims_dir.glob(f"*/proofs/{proof_id}/head.json"):
            head = self._read(head_path, "proof-head")
            return head["claim_id"], head
        raise LedgerStoreError(f"unknown proof: {proof_id}")

    def proof_head(self, proof_id: str) -> dict:
        return self._find_proof(proof_id)[1]

    # ------------------------------------------------------------ mutations

    def create_reference(self, **fields) -> tuple[dict, dict]:
        head, revision = new_reference(**fields)
        with self._lock():
            self._ensure_repo()
            ref_dir = self._reference_dir(head["reference_id"])
            fresh = self._write_record(
                ref_dir / "revisions" / f"{revision['revision_id']}.json", revision
            )
            if not fresh:
                # Byte-identical reference already recorded: echo the stored
                # head (which may have advanced past this initial revision).
                return self.reference_head(head["reference_id"]), revision
            self._write_record(ref_dir / "head.json", head)
            self._commit(
                "reference-created",
                reference_id=head["reference_id"],
                revision_id=revision["revision_id"],
            )
        return head, revision

    def refine_reference(self, reference_id: str, changes: dict) -> dict:
        with self._lock():
            self._ensure_repo()
            head = self.reference_head(reference_id)
            parent = self.reference_revision(reference_id, head["current_revision_id"])
            revision = refine_reference(parent, changes)
            fresh = self._write_record(
                self._reference_dir(reference_id)
                / "revisions"
                / f"{revision['revision_id']}.json",
                revision,
            )
            if fresh:
                head["current_revision_id"] = revision["revision_id"]
                self._write_atomic(
                    self._reference_dir(reference_id) / "head.json",
                    canonical_json(head),
                )
                self._commit(
                    "reference-refined",
                    reference_id=reference_id,
                    revision_id=revision["revision_id"],
                    parent_revision_id=parent["revision_id"],
                )
        return revision

    def create_claim(
        self,
        statement_text: str,
        *,
        kind: str = "claim",
        reference_id: str | None = None,
        reference_revision_id: str | None = None,
        locator: str | None = None,
        notes: str | None = None,
        author: dict | None = None,
    ) -> tuple[dict, dict]:
        if reference_id is not None and reference_id not in self.reference_heads():
            raise LedgerStoreError(f"unknown reference: {reference_id}")
        if reference_revision_id is not None:
            if reference_id is None:
                raise LedgerStoreError("a source revision requires a reference")
            self.reference_revision(reference_id, reference_revision_id)
        head, revision = new_claim(
            statement_text,
            kind=kind,
            reference_id=reference_id,
            reference_revision_id=reference_revision_id,
            locator=locator,
            notes=notes,
            author=author,
        )
        with self._lock():
            self._ensure_repo()
            claim_dir = self._claim_dir(head["claim_id"])
            fresh = self._write_record(
                claim_dir / "revisions" / f"{revision['revision_id']}.json", revision
            )
            if not fresh:
                # Byte-identical claim already recorded: echo the stored head
                # (which may have advanced past this initial revision).
                return self.claim_head(head["claim_id"]), revision
            self._write_record(claim_dir / "head.json", head)
            self._commit(
                "claim-created",
                claim_id=head["claim_id"],
                revision_id=revision["revision_id"],
            )
        return head, revision

    def refine(
        self,
        claim_id: str,
        statement_text: str,
        *,
        kind: str | None = None,
        reference_id: str | None | object = _UNSET,
        locator: str | None | object = _UNSET,
        notes: str | None = None,
        author: dict | None = None,
        expected_revision_id: str | None = None,
    ) -> dict:
        if (
            reference_id is not _UNSET
            and reference_id is not None
            and reference_id not in self.reference_heads()
        ):
            raise LedgerStoreError(f"unknown reference: {reference_id}")
        with self._lock():
            self._ensure_repo()
            head = self.claim_head(claim_id)
            # Compare-and-set under the write lock: refining a statement
            # one has not actually read replaces wording sight-unseen.
            if (
                expected_revision_id is not None
                and head["current_revision_id"] != expected_revision_id
            ):
                raise LedgerStaleReadError(claim_id, head["current_revision_id"])
            parent = self.claim_revision(claim_id, head["current_revision_id"])
            revision = refine_claim(
                parent,
                statement_text,
                kind=kind,
                reference_id=reference_id,
                locator=locator,
                notes=notes,
                author=author,
            )
            fresh = self._write_record(
                self._claim_dir(claim_id) / "revisions" / f"{revision['revision_id']}.json",
                revision,
            )
            if fresh:
                head["current_revision_id"] = revision["revision_id"]
                self._write_atomic(self._claim_head_path(claim_id), canonical_json(head))
                self._commit(
                    "claim-refined",
                    claim_id=claim_id,
                    revision_id=revision["revision_id"],
                    parent_revision_id=parent["revision_id"],
                )
        return revision

    def set_withdrawn(self, claim_id: str, withdrawn: bool = True) -> dict:
        with self._lock():
            self._ensure_repo()
            head = self.claim_head(claim_id)
            if head["withdrawn"] != withdrawn:
                head["withdrawn"] = withdrawn
                self._write_atomic(self._claim_head_path(claim_id), canonical_json(head))
                self._commit(
                    "claim-withdrawn" if withdrawn else "claim-restored",
                    claim_id=claim_id,
                )
        return head

    def add_proof(
        self,
        claim_id: str,
        body: dict,
        *,
        premises: list[dict] | None = None,
        artifacts: list[dict] | None = None,
        target_revision_id: str | None = None,
        notes: str | None = None,
        author: dict | None = None,
    ) -> tuple[dict, dict]:
        with self._lock():
            self._ensure_repo()
            claim_head = self.claim_head(claim_id)
            target = self.claim_revision(
                claim_id, target_revision_id or claim_head["current_revision_id"]
            )
            for premise in premises or []:
                if "unresolved" not in premise:
                    self.claim_revision(premise["claim_id"], premise["revision_id"])
            head, revision = new_proof(
                target,
                body,
                premises=premises,
                artifacts=artifacts,
                notes=notes,
                author=author,
            )
            proof_dir = self._claim_dir(claim_id) / "proofs" / head["proof_id"]
            fresh = self._write_record(
                proof_dir / "revisions" / f"{revision['revision_id']}.json", revision
            )
            if not fresh:
                return self._read(proof_dir / "head.json", "proof-head"), revision
            self._write_record(proof_dir / "head.json", head)
            self._commit(
                "proof-added",
                claim_id=claim_id,
                proof_id=head["proof_id"],
                revision_id=revision["revision_id"],
            )
        return head, revision

    def revise_proof(
        self,
        proof_id: str,
        *,
        body: dict | None = None,
        premises: list[dict] | None = None,
        target_revision_id: str | None = None,
        notes: str | None = None,
        author: dict | None = None,
    ) -> dict:
        with self._lock():
            self._ensure_repo()
            claim_id, head = self._find_proof(proof_id)
            proof_dir = self._claim_dir(claim_id) / "proofs" / proof_id
            parent = self._read(
                proof_dir / "revisions" / f"{head['current_revision_id']}.json",
                "proof-revision",
            )
            if target_revision_id is not None:
                self.claim_revision(claim_id, target_revision_id)
            for premise in premises or []:
                if "unresolved" not in premise:
                    self.claim_revision(premise["claim_id"], premise["revision_id"])
            revision = revise_proof(
                parent,
                body=body,
                premises=premises,
                target_revision_id=target_revision_id,
                notes=notes,
                author=author,
            )
            fresh = self._write_record(
                proof_dir / "revisions" / f"{revision['revision_id']}.json", revision
            )
            if fresh:
                head["current_revision_id"] = revision["revision_id"]
                self._write_atomic(proof_dir / "head.json", canonical_json(head))
                self._commit(
                    "proof-revised",
                    claim_id=claim_id,
                    proof_id=proof_id,
                    revision_id=revision["revision_id"],
                    parent_revision_id=parent["revision_id"],
                )
        return revision

    def set_abandoned(self, proof_id: str, abandoned: bool = True) -> dict:
        with self._lock():
            self._ensure_repo()
            claim_id, head = self._find_proof(proof_id)
            if head["abandoned"] != abandoned:
                head["abandoned"] = abandoned
                self._write_atomic(
                    self._claim_dir(claim_id) / "proofs" / proof_id / "head.json",
                    canonical_json(head),
                )
                self._commit(
                    "proof-abandoned" if abandoned else "proof-resumed",
                    claim_id=claim_id,
                    proof_id=proof_id,
                )
        return head

    def add_verification(
        self,
        subject: dict,
        *,
        verdict: str,
        authority: str,
        evidence: dict,
        author: dict | None = None,
        reviewer: dict | None = None,
    ) -> dict:
        # Positive review-authority verdicts are minted only by the
        # independent review harness (which passes `reviewer`): a prover
        # cannot verify its own work, whatever surface it calls through.
        if (
            authority == "review"
            and verdict in ("verified", "established")
            and reviewer is None
        ):
            raise LedgerStoreError(
                f"a review-authority '{verdict}' verdict requires the independent "
                "review harness — request one with ledger_request_review"
            )
        with self._lock():
            self._ensure_repo()
            claim_id = subject["claim_id"]
            if "proof_id" in subject:
                proof_claim, head = self._find_proof(subject["proof_id"])
                if proof_claim != claim_id:
                    raise LedgerStoreError(
                        f"proof {subject['proof_id']} does not belong to {claim_id}"
                    )
                self._read(
                    self._claim_dir(claim_id)
                    / "proofs"
                    / subject["proof_id"]
                    / "revisions"
                    / f"{subject['revision_id']}.json",
                    "proof-revision",
                )
            else:
                revision = self.claim_revision(claim_id, subject["revision_id"])
                # Definitions are exempt: they are stipulative, so a
                # passing definition review (well-posedness, no smuggled
                # notation) is what establishment means for them.
                if (
                    verdict == "established"
                    and authority != "operator"
                    and revision["reference_id"] is None
                    and revision.get("kind") != "definition"
                ):
                    raise LedgerStoreError(
                        "direct establishment of an unreferenced claim is operator-only; "
                        "attach a reference to the claim or give it a proof"
                    )
            record = new_verification(
                subject,
                verdict=verdict,
                authority=authority,
                evidence=evidence,
                author=author,
                reviewer=reviewer,
            )
            fresh = self._write_record(
                self._claim_dir(claim_id)
                / "verifications"
                / f"{record['verification_id']}.json",
                record,
            )
            if fresh:
                self._commit(
                    "verification-added",
                    claim_id=claim_id,
                    verification_id=record["verification_id"],
                    verdict=verdict,
                    authority=authority,
                )
        return record

    def set_evidence_repair(
        self, claim_id: str, verification_id: str, repair: dict | None
    ) -> dict:
        """Attach (or clear) the display-only KaTeX repair of a
        verification's evidence, in place.

        This is the one sanctioned mutation of an existing record: the
        repair field is excluded from the content id, so the id (hence the
        filename) is unchanged and still certifies that the authoritative
        evidence was not touched. Model-facing reads ignore the field;
        only the browser detail path shows it."""
        with self._lock():
            path = (
                self._claim_dir(claim_id)
                / "verifications"
                / f"{verification_id}.json"
            )
            record = self._read(path, "verification")
            expected = content_id(
                "ver", record, blank_fields=("verification_id", "evidence_repair")
            )
            if expected != verification_id:
                raise LedgerStoreError(
                    f"verification {verification_id} fails its content id — refusing "
                    "to write a repair onto a record that does not certify"
                )
            record["evidence_repair"] = repair
            validate_record(record, "verification")
            path.write_text(canonical_json(record), encoding="utf-8")
            self._commit(
                "evidence-repaired",
                claim_id=claim_id,
                verification_id=verification_id,
            )
        return record

    # -------------------------------------------------------------- queries

    def load(self) -> LedgerView:
        head = self.head_commit()
        if head is not None:
            with self._view_cache_lock:
                cached = self._view_cache
            if cached is not None and cached[0] == head:
                return cached[1]
        view = self._load_view()
        # Cache only when the head is unchanged across the scan, so a write
        # landing mid-scan can't pin a torn snapshot.
        if head is not None and head == self.head_commit():
            with self._view_cache_lock:
                self._view_cache = (head, view)
        return view

    def _load_view(self) -> LedgerView:
        view = LedgerView()
        if self.references_dir.exists():
            for ref_dir in sorted(self.references_dir.iterdir()):
                if not (ref_dir / "head.json").exists():
                    continue
                head = self._read_raw(ref_dir / "head.json")
                view.reference_heads[head["reference_id"]] = head
                for path in sorted((ref_dir / "revisions").glob("*.json")):
                    revision = self._read_raw(path)
                    view.reference_revisions[revision["revision_id"]] = revision
        if not self.claims_dir.exists():
            return view
        for claim_dir in sorted(self.claims_dir.iterdir()):
            if not (claim_dir / "head.json").exists():
                continue
            head = self._read_raw(claim_dir / "head.json")
            view.claim_heads[head["claim_id"]] = head
            for path in sorted((claim_dir / "revisions").glob("*.json")):
                revision = self._read_raw(path)
                view.claim_revisions[revision["revision_id"]] = revision
            for proof_dir in sorted((claim_dir / "proofs").glob("*")):
                proof_head = self._read_raw(proof_dir / "head.json")
                view.proof_heads[proof_head["proof_id"]] = proof_head
                for path in sorted((proof_dir / "revisions").glob("*.json")):
                    revision = self._read_raw(path)
                    view.proof_revisions[revision["revision_id"]] = revision
            for path in sorted((claim_dir / "verifications").glob("*.json")):
                record = self._read_raw(path)
                view.verifications[record["verification_id"]] = record
        return view

    def state(self) -> LedgerState:
        return self.derived(self.load())

    def derived(self, view: LedgerView) -> LedgerState:
        """derive_state(view), memoized per view snapshot. Cached views
        are shared read-only objects recreated on every head change, so
        their identity is an exact key."""
        with self._view_cache_lock:
            cached = self._state_cache
            if cached is not None and cached[0] is view:
                return cached[1]
        with self._derive_lock:
            with self._view_cache_lock:
                cached = self._state_cache
                if cached is not None and cached[0] is view:
                    return cached[1]
            state = derive_state(view)
            with self._view_cache_lock:
                self._state_cache = (view, state)
        return state

    def claim_order(self, view: LedgerView) -> dict[str, int]:
        """Creation order of claims (0 = oldest) from the journal,
        memoized per view snapshot like `derived`. Claims are content-
        addressed, so the journal is the only record of when each
        arrived."""
        with self._view_cache_lock:
            cached = self._order_cache
            if cached is not None and cached[0] is view:
                return cached[1]
        order: dict[str, int] = {}
        if (self.root / ".git").exists():
            raw = self._git("log", "--reverse", "--format=%s", check=False)
            for line in raw.splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[0] == "claim-created":
                    order.setdefault(parts[1], len(order))
        with self._view_cache_lock:
            self._order_cache = (view, order)
        return order

    def fsck(self) -> dict:
        """Referential integrity over the working tree, plus git's own
        object check when a repository exists. The post-merge safety net."""
        # fsck exists to catch out-of-band damage (manual edits, botched
        # merges) that does not move the git head — bypass the view cache.
        issues = integrity_issues(self._load_view())
        if (self.root / ".git").exists():
            result = subprocess.run(
                ["git", "-C", str(self.root), "fsck", "--no-progress"],
                text=True,
                capture_output=True,
                check=False,
            )
            if result.returncode != 0:
                issues.append(f"git fsck failed: {result.stderr.strip()[-300:]}")
        return {"issues": issues, "ok": not issues}
