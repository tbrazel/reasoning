"""Ledger commits reject LaTeX that KaTeX cannot render."""

import pytest

from reasoning_ledger import LedgerStore, LedgerStoreError, latex_check, service

AUTHOR = {"operator": "op", "agent": "agent-x", "session": "s1"}

pytestmark = pytest.mark.skipif(
    not latex_check.available(), reason="Node + KaTeX checker unavailable"
)


def _store(tmp_path):
    return LedgerStore(tmp_path / "ledger")


class TestClaimValidation:
    def test_undefined_macro_in_statement_is_rejected(self, tmp_path):
        store = _store(tmp_path)
        with pytest.raises(LedgerStoreError, match="Undefined control sequence"):
            service.claim_payload(store, r"The ring \(\RR\) is nice.", author=AUTHOR)
        # nothing committed
        assert service.status_payload(store)["claims"] == []

    def test_clean_statement_commits(self, tmp_path):
        store = _store(tmp_path)
        out = service.claim_payload(store, r"\(x^2 \ge 0\) for real \(x\).", author=AUTHOR)
        assert out["claim_id"].startswith("clm_")

    def test_bad_notes_are_rejected(self, tmp_path):
        store = _store(tmp_path)
        with pytest.raises(LedgerStoreError, match="notes contains LaTeX"):
            service.claim_payload(
                store, "A fine statement.", notes=r"see \(\undefinedmacro\)", author=AUTHOR
            )

    def test_refine_validates(self, tmp_path):
        store = _store(tmp_path)
        made = service.claim_payload(store, "Original.", author=AUTHOR)
        with pytest.raises(LedgerStoreError, match="Undefined control sequence"):
            service.refine_payload(store, made["claim_id"], r"Now with \(\RR\).", author=AUTHOR)


class TestProofAndReferenceValidation:
    def test_bad_argument_is_rejected(self, tmp_path):
        store = _store(tmp_path)
        claim = service.claim_payload(store, "A claim.", author=AUTHOR)
        with pytest.raises(LedgerStoreError, match="argument contains LaTeX"):
            service.prove_payload(
                store, claim["claim_id"], r"Because \(\badmacro\) holds.", author=AUTHOR
            )

    def test_bad_new_premise_is_rejected(self, tmp_path):
        store = _store(tmp_path)
        claim = service.claim_payload(store, "A claim.", author=AUTHOR)
        with pytest.raises(LedgerStoreError, match="new premise 1"):
            service.prove_payload(
                store,
                claim["claim_id"],
                "Sound argument.",
                new_premises=[r"A lemma about \(\nope\)."],
                author=AUTHOR,
            )

    def test_bad_reference_title_is_rejected(self, tmp_path):
        store = _store(tmp_path)
        with pytest.raises(LedgerStoreError, match="title contains LaTeX"):
            service.reference_payload(
                store, author="Author", title=r"On \(\RR\)-modules", year=2020
            )

    def test_clean_reference_with_math_commits(self, tmp_path):
        store = _store(tmp_path)
        out = service.reference_payload(
            store, author="Serre", title=r"\(p\)-adic Hodge theory", year=1979
        )
        assert out["reference_id"].startswith("ref_")


class TestEvidenceIsExempt:
    def test_verification_evidence_is_not_validated(self, tmp_path):
        # verification logs are repaired-for-display, never rejected
        store = _store(tmp_path)
        claim = service.claim_payload(store, "A claim.", author=AUTHOR)
        proof = service.prove_payload(store, claim["claim_id"], "Argument.", author=AUTHOR)
        # a gap summary with broken TeX still records
        out = service.verify_payload(
            store,
            verdict="flawed",
            authority="review",
            evidence={"kind": "gap", "summary": r"step 2 assumes \(\RR\) is finite"},
            proof_id=proof["proof_id"],
            author=AUTHOR,
        )
        assert out["verification_id"].startswith("ver_")
