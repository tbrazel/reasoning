"""Source-attributed statements remain ordinary dependency-graph claims, not certificates."""

import json

import pytest
from test_public_server import installation as installation
from test_public_server import invited

from reasoning_ledger.server.store import StoreError
from reasoning_ledger.server.workbench_tools import MathematicalLedger


def test_source_statement_pins_bibliography_revision_and_refinement_preserves_it(installation):
    _, store, _, owner = installation
    ledger = MathematicalLedger(store)
    user, org = owner["user_id"], owner["organization_id"]
    ref = ledger.execute(user, org, "ledger_reference", {"author": "A. Author", "title": "Source title"}, client_id="source-ref-1")
    claim = ledger.execute(user, org, "ledger_claim", {"statement": "An exact source statement.", "reference_id": ref["reference_id"],
                           "reference_revision_id": ref["revision_id"], "locator": "Theorem 2.3, p. 17"}, client_id="source-statement-1")
    ledger.execute(user, org, "ledger_refine_reference", {"reference_id": ref["reference_id"], "changes": {"title": "New title"}},
                   client_id="source-ref-2")
    result = ledger.execute(user, org, "ledger_status", {"claim_id": claim["claim_id"]})
    assert result["reference"]["title"] == "Source title"
    ledger.execute(user, org, "ledger_refine", {"claim_id": claim["claim_id"], "statement": "An improved transcription."},
                   client_id="source-statement-2")
    with store.transaction() as db:
        revisions = [json.loads(row[0]) for row in db.execute("SELECT body_json FROM mathematical_records WHERE object_id=? "
                                                             "AND record_type='claim-revision'", (claim["claim_id"],))]
    assert len(revisions) == 2 and all(row["reference_revision_id"] == ref["revision_id"] for row in revisions)


def test_source_statement_scope_inherits_private_reference_without_exposing_it(installation):
    _, store, _, owner = installation
    user, org = owner["user_id"], owner["organization_id"]
    project = store.create_entity(user, org, "projects", "Private sources", visibility="private", owner_id=user, member_ids=[])
    ledger = MathematicalLedger(store)
    ref = ledger.execute(user, org, "ledger_reference", {"author": "Private author", "title": "Private source"},
                         project=project["id"], client_id="private-source-ref")
    claim = ledger.execute(user, org, "ledger_claim", {"statement": "A private source statement.", "reference_id": ref["reference_id"],
                           "reference_revision_id": ref["revision_id"], "locator": "Lemma 1"}, client_id="private-source-claim")
    with store.transaction() as db:
        rows = db.execute("SELECT project_id FROM mathematical_records WHERE object_id=?", (claim["claim_id"],)).fetchall()
        assert rows and all(row[0] == project["id"] for row in rows)
    _, _, member = invited(installation)
    with pytest.raises(StoreError):
        ledger.execute(member["user_id"], org, "ledger_read", {"ledger_id": claim["claim_id"]})
