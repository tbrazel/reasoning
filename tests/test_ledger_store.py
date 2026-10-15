"""Tests for the per-project git-backed ledger store."""

import json
import subprocess

import pytest

from reasoning_ledger import LedgerStore, LedgerStoreError


def _git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        text=True,
        capture_output=True,
        check=False,
    )


def _events(store):
    return [e["event"] for e in store.journal_entries() if e["event"] != "ledger-init"]

AUTHOR = {"operator": "op", "agent": "agent-x", "session": "s1"}
# review-authority positives require the harness marker
REVIEWER = {"model": "fixture-reviewer", "effort": "xhigh", "review_id": "rev_" + "0" * 16}


def _argument(text="Because of the following argument."):
    return {"kind": "argument", "text": text, "format": "tex-prose-v1"}


def _store(tmp_path):
    return LedgerStore(tmp_path / "ledger")


def _proof_subject(revision):
    return {
        "claim_id": revision["claim_id"],
        "proof_id": revision["proof_id"],
        "revision_id": revision["revision_id"],
    }


def _claim_subject(revision):
    return {"claim_id": revision["claim_id"], "revision_id": revision["revision_id"]}


def _cited_claim(store, statement):
    ref_head, _ = store.create_reference(author="A", title="A Source")
    head, rev = store.create_claim(
        statement, reference_id=ref_head["reference_id"], author=AUTHOR
    )
    store.add_verification(
        _claim_subject(rev),
        verdict="established",
        authority="corpus",
        evidence={"kind": "citation-attested"},
    )
    return head, rev


class TestStore:
    def test_round_trip_establishment(self, tmp_path):
        store = _store(tmp_path)
        b_head, b_rev = _cited_claim(store, "The bound.")
        a_head, _ = store.create_claim("Main result.", author=AUTHOR)
        _, a_proof = store.add_proof(
            a_head["claim_id"],
            _argument(),
            premises=[{"claim_id": b_head["claim_id"], "revision_id": b_rev["revision_id"]}],
            author=AUTHOR,
        )
        store.add_verification(
            _proof_subject(a_proof),
            verdict="verified",
            authority="review",
            evidence={"kind": "review-panel"},
            reviewer=REVIEWER,
        )

        # A fresh store instance over the same directory sees the same state.
        state = LedgerStore(store.root).state()
        assert state.claim_status[b_head["claim_id"]]["status"] == "established"
        assert state.claim_status[a_head["claim_id"]]["status"] == "established"
        assert state.claim_status[a_head["claim_id"]]["authority"] == "review"

    def test_create_claim_is_idempotent(self, tmp_path):
        store = _store(tmp_path)
        first_head, first_rev = store.create_claim("Same.", author=AUTHOR)
        second_head, second_rev = store.create_claim("Same.", author=AUTHOR)
        assert first_head["claim_id"] == second_head["claim_id"]
        assert first_rev["revision_id"] == second_rev["revision_id"]
        assert _events(store) == ["claim-created"]

    def test_refine_advances_head_and_journals(self, tmp_path):
        store = _store(tmp_path)
        head, rev = store.create_claim("First.", author=AUTHOR)
        refined = store.refine(head["claim_id"], "Second.", author=AUTHOR)
        assert store.claim_head(head["claim_id"])["current_revision_id"] == refined["revision_id"]
        assert refined["parent_revision_id"] == rev["revision_id"]
        assert _events(store) == ["claim-created", "claim-refined"]
        entry = store.journal_entries()[-1]
        assert entry["claim_id"] == head["claim_id"]
        assert entry["at"]  # commit timestamp

    def test_uncited_establishment_is_operator_only(self, tmp_path):
        store = _store(tmp_path)
        _, rev = store.create_claim("Uncited.", author=AUTHOR)
        with pytest.raises(LedgerStoreError, match="operator-only"):
            store.add_verification(
                _claim_subject(rev),
                verdict="established",
                authority="review",
                evidence={"kind": "say-so"},
                reviewer=REVIEWER,
            )
        record = store.add_verification(
            _claim_subject(rev),
            verdict="established",
            authority="operator",
            evidence={"kind": "operator-assertion"},
        )
        assert record["authority"] == "operator"
        ref_head, _ = store.create_reference(author="A", title="A Source", year=1990)
        _, cited_rev = store.create_claim(
            "Cited.", reference_id=ref_head["reference_id"], author=AUTHOR
        )
        record = store.add_verification(
            _claim_subject(cited_rev),
            verdict="established",
            authority="review",
            evidence={"kind": "citation-attested"},
            reviewer=REVIEWER,
        )
        assert record["verdict"] == "established"

    def test_every_mutation_is_a_commit(self, tmp_path):
        store = _store(tmp_path)
        head, _ = store.create_claim("Committed.", author=AUTHOR)
        store.refine(head["claim_id"], "Committed, sharper.", author=AUTHOR)
        store.set_withdrawn(head["claim_id"])
        log = _git(store.root, "log", "--format=%s").stdout.splitlines()
        assert log[0].startswith("claim-withdrawn")
        assert log[-1] == "ledger-init"
        assert len(log) == 4
        status = _git(store.root, "status", "--porcelain").stdout.strip()
        assert status == ""  # nothing uncommitted, .lock ignored
        assert store.head_commit() is not None

    def test_fsck_clean_and_detects_dangling_pins(self, tmp_path):
        store = _store(tmp_path)
        b_head, b_rev = store.create_claim("Premise.", author=AUTHOR)
        a_head, _ = store.create_claim("Dependent.", author=AUTHOR)
        store.add_proof(
            a_head["claim_id"],
            _argument(),
            premises=[{"claim_id": b_head["claim_id"], "revision_id": b_rev["revision_id"]}],
            author=AUTHOR,
        )
        assert store.fsck() == {"issues": [], "ok": True}

        # Deleting a pinned revision file is detectable damage.
        (
            store.claims_dir
            / b_head["claim_id"]
            / "revisions"
            / f"{b_rev['revision_id']}.json"
        ).unlink()
        report = store.fsck()
        assert not report["ok"]
        assert any("pins missing premise" in issue for issue in report["issues"])
        assert any("head" in issue for issue in report["issues"])

    def test_load_is_cached_by_head_commit(self, tmp_path):
        store = _store(tmp_path)
        store.create_claim("Cache subject.", author=AUTHOR)
        first = store.load()
        assert store.load() is first  # same head -> same shared snapshot
        head, _ = store.create_claim("Invalidates.", author=AUTHOR)
        fresh = store.load()
        assert fresh is not first
        assert head["claim_id"] in fresh.claim_heads
        assert head["claim_id"] not in first.claim_heads  # old snapshot intact

    def test_branches_union_merge_and_head_conflicts_surface(self, tmp_path):
        store = _store(tmp_path)
        head, rev = store.create_claim("Shared base.", author=AUTHOR)
        root = store.root

        # Two divergent branches, each adding its own claim: union merge.
        _git(root, "branch", "side")
        store.create_claim("Main-side claim.", author=AUTHOR)
        _git(root, "checkout", "-q", "side")
        side_store = LedgerStore(root)
        side_store.create_claim("Side-branch claim.", author=AUTHOR)
        _git(root, "checkout", "-q", "-")
        merge = _git(root, "merge", "-q", "--no-edit", "side")
        assert merge.returncode == 0, merge.stderr
        merged = LedgerStore(root)
        assert len(merged.load().claim_heads) == 3
        assert merged.fsck()["ok"]

        # Both branches refine the same claim: the head file conflicts.
        _git(root, "branch", "restate")
        merged.refine(head["claim_id"], "Main restatement.", author=AUTHOR)
        _git(root, "checkout", "-q", "restate")
        LedgerStore(root).refine(head["claim_id"], "Side restatement.", author=AUTHOR)
        _git(root, "checkout", "-q", "-")
        conflict = _git(root, "merge", "--no-edit", "restate")
        assert conflict.returncode != 0
        conflicted = _git(root, "diff", "--name-only", "--diff-filter=U").stdout
        assert "head.json" in conflicted
        assert conflicted.strip().count("\n") == 0  # only the head conflicts

    def test_immutable_record_conflict_is_an_error(self, tmp_path):
        store = _store(tmp_path)
        head, rev = store.create_claim("Fixed.", author=AUTHOR)
        path = (
            store.claims_dir
            / head["claim_id"]
            / "revisions"
            / f"{rev['revision_id']}.json"
        )
        record = json.loads(path.read_text(encoding="utf-8"))
        record["notes"] = "silently edited"
        path.write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")
        with pytest.raises(LedgerStoreError, match="immutable record differs"):
            store.create_claim("Fixed.", author=AUTHOR)

    def test_verification_requires_existing_subject(self, tmp_path):
        store = _store(tmp_path)
        head, rev = store.create_claim("Exists.", author=AUTHOR)
        with pytest.raises(LedgerStoreError, match="missing record"):
            store.add_verification(
                {"claim_id": head["claim_id"], "revision_id": "clmrev_" + "0" * 24},
                verdict="refuted",
                authority="review",
                evidence={"kind": "counterexample"},
            )
        with pytest.raises(LedgerStoreError, match="unknown proof"):
            store.add_verification(
                {
                    "claim_id": head["claim_id"],
                    "proof_id": "prf_" + "0" * 24,
                    "revision_id": "prfrev_" + "0" * 24,
                },
                verdict="verified",
                authority="review",
                evidence={"kind": "review-panel"},
                reviewer=REVIEWER,
            )

    def test_proof_premises_must_exist_unless_unresolved(self, tmp_path):
        store = _store(tmp_path)
        head, _ = store.create_claim("Needs premise.", author=AUTHOR)
        with pytest.raises(LedgerStoreError, match="missing record"):
            store.add_proof(
                head["claim_id"],
                _argument(),
                premises=[{"claim_id": "clm_" + "0" * 24, "revision_id": "clmrev_" + "0" * 24}],
                author=AUTHOR,
            )
        proof_head, _ = store.add_proof(
            head["claim_id"],
            _argument(),
            premises=[{"unresolved": "the missing bound"}],
            author=AUTHOR,
        )
        report = store.state().proof_reports[proof_head["proof_id"]]
        assert report["premises"][0]["state"] == "unresolved"

    def test_rebase_via_revise_proof(self, tmp_path):
        store = _store(tmp_path)
        b_head, b_rev = store.create_claim("Premise v1.", author=AUTHOR)
        a_head, _ = store.create_claim("Dependent.", author=AUTHOR)
        proof_head, proof = store.add_proof(
            a_head["claim_id"],
            _argument(),
            premises=[{"claim_id": b_head["claim_id"], "revision_id": b_rev["revision_id"]}],
            author=AUTHOR,
        )
        fixed = store.refine(b_head["claim_id"], "Premise v2.", author=AUTHOR)
        rebased = store.revise_proof(
            proof_head["proof_id"],
            premises=[{"claim_id": b_head["claim_id"], "revision_id": fixed["revision_id"]}],
            author=AUTHOR,
        )
        assert rebased["parent_revision_id"] == proof["revision_id"]
        assert rebased["body"] == proof["body"]
        report = store.state().proof_reports[proof_head["proof_id"]]
        assert report["revision_id"] == rebased["revision_id"]
        assert report["premises"][0]["state"] == "current"

    def test_abandon_round_trip(self, tmp_path):
        store = _store(tmp_path)
        head, _ = store.create_claim("Abandonable.", author=AUTHOR)
        proof_head, proof = store.add_proof(head["claim_id"], _argument(), author=AUTHOR)
        store.add_verification(
            _proof_subject(proof),
            verdict="verified",
            authority="review",
            evidence={"kind": "review-panel"},
            reviewer=REVIEWER,
        )
        assert store.state().claim_status[head["claim_id"]]["status"] == "established"
        store.set_abandoned(proof_head["proof_id"])
        assert store.state().claim_status[head["claim_id"]]["status"] == "conjectured"
        store.set_abandoned(proof_head["proof_id"], abandoned=False)
        assert store.state().claim_status[head["claim_id"]]["status"] == "established"
        events = [entry["event"] for entry in store.journal_entries()]
        assert events[-2:] == ["proof-abandoned", "proof-resumed"]
