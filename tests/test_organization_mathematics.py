"""The organization uses the workbench domain without local Git or paid calls."""

import json

import pytest
from test_public_server import installation as installation
from test_public_server import invited, structure

from reasoning_ledger import service
from reasoning_ledger.server.mathematical_import import import_records
from reasoning_ledger.server.mathematical_store import MathematicalStore
from reasoning_ledger.server.store import StoreError
from reasoning_ledger.server.workbench_tools import MathematicalLedger
from reasoning_ledger.service import RevisionObservations
from reasoning_ledger.store import LedgerStore


def setup(installation):
    _, store, client, owner = installation
    projects, _, topics = structure(client, owner["organization_id"])
    ledger = MathematicalLedger(store)
    seen = RevisionObservations()

    def call(name, arguments, client_id="request", observations=seen):
        return ledger.execute(
            owner["user_id"],
            owner["organization_id"],
            name,
            arguments,
            client_id=client_id,
            project=projects["id"],
            topic=topics[1]["id"],
            agent=True,
            observations=observations,
        )

    return store, owner, projects, topics, call


def test_automatic_claim_proof_and_revision_pinning(installation):
    store, owner, _, _, call = setup(installation)
    premise = call("ledger_claim", {"statement": "Every positive integer is at least one."}, "premise")
    target = call("ledger_claim", {"statement": "Every positive integer is positive."}, "target")
    proof = call(
        "ledger_prove",
        {
            "claim_id": target["claim_id"],
            "argument": "By " + premise["claim_id"] + ", n >= 1 > 0.",
            "premises": [{"claim_id": premise["claim_id"]}],
        },
        "proof",
    )
    status = call("ledger_status", {"claim_id": target["claim_id"]})
    assert proof["proof_id"] and status
    revised = call("ledger_refine", {"claim_id": premise["claim_id"], "statement": "Every integer n >= 1 satisfies n >= 1."}, "revise")
    assert revised["parent_revision_id"] == premise["revision_id"]
    with store.transaction() as db:
        view = MathematicalStore(store, db, owner["user_id"], owner["organization_id"]).load()
        assert view.proof_revisions[proof["revision_id"]]["premises"][0]["revision_id"] == premise["revision_id"]
        assert "stale" in json.dumps(MathematicalStore(store, db, owner["user_id"], owner["organization_id"]).state().proof_reports)


def test_replay_and_stale_write_are_safe(installation):
    _, _, _, _, call = setup(installation)
    original = call("ledger_claim", {"statement": "Let V be a rational vector space."}, "same")
    assert call("ledger_claim", {"statement": "Let V be a rational vector space."}, "same") == original
    with pytest.raises(StoreError):
        call("ledger_claim", {"statement": "Another statement."}, "same")
    old = RevisionObservations()
    old.note(original["claim_id"], original["revision_id"])
    call("ledger_refine", {"claim_id": original["claim_id"], "statement": "Let V be a finite-dimensional rational vector space."}, "new")
    refused = call(
        "ledger_refine", {"claim_id": original["claim_id"], "statement": "Let V be a complex vector space."}, "stale", observations=old
    )
    assert refused.get("refused") == "stale-read"


def test_api_does_not_allow_author_to_certify_proof(installation):
    _, _, client, owner = installation
    path = f"/api/organizations/{owner['organization_id']}/mathematics"
    assert client.get(path).status_code == 200
    assert client.post(path, json={"name": "ledger_verify", "arguments": {"verdict": "verified"}, "client_id": "forged"}).status_code == 403


def test_mathematical_history_is_immutable_in_database(installation):
    store, _, _, _, call = setup(installation)
    call("ledger_claim", {"statement": "An immutable statement."}, "immutable")
    with pytest.raises(Exception, match="immutable"), store.transaction(write=True) as db:
        db.execute("UPDATE mathematical_records SET body_json='{}' WHERE record_type='claim-revision'")
    with pytest.raises(Exception, match="retained"), store.transaction(write=True) as db:
        db.execute("DELETE FROM mathematical_records")


def test_import_retains_ids_but_never_trusts_imported_positive_reviews(installation, tmp_path):
    store, owner, project, _, _ = setup(installation)
    source = LedgerStore(tmp_path / "source-ledger")
    claim = service.claim_payload(source, "The integer 2 equals itself.")
    proof = service.prove_payload(source, claim["claim_id"], argument="Reflexivity of equality.")
    verification = source.add_verification(
        {"claim_id": claim["claim_id"], "proof_id": proof["proof_id"], "revision_id": proof["revision_id"]},
        verdict="verified",
        authority="review",
        evidence={"kind": "review", "text": "Checked."},
        reviewer={"model": "fixture/reviewer", "effort": "default", "review_id": "rev_" + "a" * 32},
    )
    records = [
        {"path": str(path.relative_to(source.root)), "record": json.loads(path.read_text())}
        for path in source.root.rglob("*.json")
        if path.is_relative_to(source.root / "claims")
    ]
    result = import_records(store, owner["user_id"], owner["organization_id"], project["id"], records, "import-exact-snapshot")
    assert result["imported_records"] == len(records)
    assert import_records(store, owner["user_id"], owner["organization_id"], project["id"], records, "import-exact-snapshot") == result
    ledger = MathematicalLedger(store)
    state = ledger.execute(owner["user_id"], owner["organization_id"], "ledger_status", {"claim_id": claim["claim_id"]})
    assert state["status"] != "established"
    archive = ledger.execute(owner["user_id"], owner["organization_id"], "ledger_read", {"ledger_id": verification["verification_id"]})
    assert archive["trust_state"] == "imported" and not archive["counts_as_evidence"]


def test_private_mathematics_and_alpha_scope_remain_unavailable(installation):
    _, store, client, owner = installation
    _, outsider, _ = invited(installation, email="outsider-math@example.org")
    root = "/api/organizations/" + owner["organization_id"]
    project = client.post(root + "/projects", json={"name": "Private proof", "visibility": "private", "owner_id": owner["user_id"]}).json()
    channel = client.post(root + "/channels", json={"name": "research", "project_id": project["id"]}).json()
    beta = client.post(root + "/topics", json={"name": "Research", "kind": "beta", "channel_id": channel["id"]}).json()
    alpha = client.post(root + "/topics", json={"name": "Human", "kind": "alpha", "channel_id": channel["id"]}).json()
    claim = MathematicalLedger(store).execute(
        owner["user_id"],
        owner["organization_id"],
        "ledger_claim",
        {"statement": "A confidential mathematical claim."},
        project=project["id"],
        topic=beta["id"],
        client_id="private-math",
    )
    assert outsider.get(root + "/mathematics").json()["claims"] == []
    assert outsider.get(root + "/mathematics/" + claim["claim_id"]).status_code == 409
    assert client.get(root + "/mathematics", params={"topic_id": alpha["id"]}).status_code == 404


def test_proof_revision_requires_current_read(installation):
    _, _, _, _, call = setup(installation)
    claim = call("ledger_claim", {"statement": "The integer 3 equals itself."}, "claim-proof-cas")
    proof = call("ledger_prove", {"claim_id": claim["claim_id"], "argument": "Reflexivity."}, "proof-cas")
    unseen = RevisionObservations()
    result = call("ledger_revise_proof", {"proof_id": proof["proof_id"], "argument": "A changed argument."}, "unseen-proof", unseen)
    assert result["refused"] == "stale-proof-read"


def test_bibliography_revision_requires_current_read(installation):
    _, _, _, _, call = setup(installation)
    reference = call("ledger_reference", {"author": "A. Researcher", "title": "A mathematical source."}, "reference-cas")
    result = call(
        "ledger_refine_reference",
        {"reference_id": reference["reference_id"], "changes": {"notes": "Unchecked replacement."}},
        "unseen-reference",
        RevisionObservations(),
    )
    assert result["refused"] == "stale-reference-read"
    seen = RevisionObservations()
    call("ledger_read", {"ledger_id": reference["reference_id"]}, observations=seen)
    result = call(
        "ledger_refine_reference",
        {"reference_id": reference["reference_id"], "changes": {"notes": "Read before updating."}},
        "seen-reference",
        seen,
    )
    assert result["parent_revision_id"] == reference["revision_id"]
