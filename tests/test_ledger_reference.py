"""Tests for the bibliography: reference records and claim linkage."""

import pytest

from reasoning_ledger import LedgerStore, service
from reasoning_ledger.store import LedgerStoreError

AUTHOR = {"operator": "op", "agent": "a", "session": "s"}


def _store(tmp_path):
    return LedgerStore(tmp_path / "ledger")


class TestReferencePayload:
    def test_create_and_dedup(self, tmp_path):
        store = _store(tmp_path)
        a = service.reference_payload(
            store, author="Serre", title="Local Fields", year=1979,
            isbn="978-0-387-90424-5",
        )
        assert a["author"] == "Serre"
        assert "warnings" not in a  # isbn present
        b = service.reference_payload(
            store, author="Serre", title="Local Fields", year=1979,
            isbn="978-0-387-90424-5",
        )
        assert b["reference_id"] == a["reference_id"]  # content dedup
        assert len(service.references_payload(store)["references"]) == 1

    def test_warns_without_identifier(self, tmp_path):
        store = _store(tmp_path)
        r = service.reference_payload(store, author="A", title="Untraceable", year=2000)
        assert any("arxiv/url/doi/isbn" in w for w in r["warnings"])

    def test_warns_on_arxiv_doi_in_doi_field(self, tmp_path):
        store = _store(tmp_path)
        r = service.reference_payload(
            store, author="A", title="T", doi="10.48550/arXiv.2301.12345"
        )
        assert any("arXiv DOI" in w for w in r["warnings"])

    def test_missing_author_or_title_is_rejected(self, tmp_path):
        store = _store(tmp_path)
        with pytest.raises(ValueError):
            service.reference_payload(store, author="", title="T")
        with pytest.raises(ValueError):
            service.reference_payload(store, author="A", title="")

    def test_refine_adds_identifier_and_advances_head(self, tmp_path):
        store = _store(tmp_path)
        r = service.reference_payload(store, author="A", title="Paper", year=2023)
        assert "warnings" in r  # no id yet
        refined = service.refine_reference_payload(
            store, r["reference_id"], {"arxiv": "2301.12345"}
        )
        assert refined["arxiv"] == "2301.12345"
        assert "warnings" not in refined
        # the head now serves the corrected revision
        current = service.references_payload(store)["references"][0]
        assert current["arxiv"] == "2301.12345"
        assert current["reference_id"] == r["reference_id"]

    def test_search_references(self, tmp_path):
        store = _store(tmp_path)
        service.reference_payload(store, author="Deligne", title="Weil I", year=1974)
        service.reference_payload(store, author="Serre", title="Local Fields")
        hits = service.search_references_payload(store, "deligne")["matches"]
        assert [h["title"] for h in hits] == ["Weil I"]


class TestClaimLinkage:
    def test_claim_cites_reference_and_status_resolves_it(self, tmp_path):
        store = _store(tmp_path)
        r = service.reference_payload(store, author="Isaacs", title="FGT", year=2008)
        claim = service.claim_payload(
            store, "The bound holds.", reference_id=r["reference_id"],
            locator="Thm 1.19", author=AUTHOR,
        )
        detail = service.status_payload(store, claim["claim_id"])
        assert detail["reference_id"] == r["reference_id"]
        assert detail["locator"] == "Thm 1.19"
        assert detail["reference"]["title"] == "FGT"

    def test_claim_with_unknown_reference_is_rejected(self, tmp_path):
        store = _store(tmp_path)
        with pytest.raises(LedgerStoreError, match="unknown reference"):
            service.claim_payload(
                store, "Bad.", reference_id="ref_" + "0" * 24, author=AUTHOR
            )

    def test_reference_correction_flows_to_citing_claims(self, tmp_path):
        store = _store(tmp_path)
        r = service.reference_payload(store, author="A", title="Paper", year=2023)
        claim = service.claim_payload(
            store, "Rests on the paper.", reference_id=r["reference_id"], author=AUTHOR
        )
        service.refine_reference_payload(
            store, r["reference_id"], {"doi": "10.1112/example"}
        )
        # the claim pins the reference head, so it sees the new DOI
        detail = service.status_payload(store, claim["claim_id"])
        assert detail["reference"]["doi"] == "10.1112/example"

    def test_uncited_review_establishment_still_operator_only(self, tmp_path):
        store = _store(tmp_path)
        claim = service.claim_payload(store, "Unreferenced.", author=AUTHOR)
        head = store.claim_head(claim["claim_id"])
        with pytest.raises(LedgerStoreError, match="operator-only"):
            store.add_verification(
                {"claim_id": claim["claim_id"], "revision_id": head["current_revision_id"]},
                verdict="established",
                authority="review",
                evidence={"kind": "x"},
                reviewer={"model": "m", "effort": "high", "review_id": "rev_" + "0" * 16},
            )

    def test_fsck_flags_dangling_reference(self, tmp_path):
        store = _store(tmp_path)
        # a reference-bearing claim whose reference exists is clean
        r = service.reference_payload(store, author="A", title="T", year=1)
        service.claim_payload(
            store, "Cited.", reference_id=r["reference_id"], author=AUTHOR
        )
        assert store.fsck()["ok"]
