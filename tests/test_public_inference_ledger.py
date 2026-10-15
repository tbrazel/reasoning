"""Provider receipts retain exact prices and uncertain charges without revealing private content."""

import json
import sqlite3

import pytest
from test_public_inference import QUOTES, enqueue, prepare
from test_public_server import installation as installation
from test_public_server import invited

from reasoning_ledger.server.model_recommendations import SHORTLIST, recommendations
from reasoning_ledger.server.openrouter import Usage, rejection_error
from reasoning_ledger.server.usage_store import UsageStore


def call(installation, *, unknown=False):
    _, state, client, owner, topics = prepare(installation)
    enqueue(state, owner, topics[1]["id"], models=["test/one"])
    row = state.claim()
    ledger = UsageStore(state.store)
    original = ledger.begin_call(row, QUOTES[0], harness_version="native-v1", prompt_version="research-v1")
    usage = None if unknown else Usage(12, 100, 20, cached_tokens=10, reasoning_tokens=5, total_tokens=120)
    ledger.finish_call(
        original,
        status="failed" if unknown else "succeeded",
        usage=usage,
        provider_generation_id="gen-ledger",
        diagnostics=rejection_error(503).diagnostics if unknown else None,
    )
    state.finish(row, "failed" if unknown else "succeeded", usage=usage)
    return state, ledger, client, owner, topics, row, original


def test_ledger_paginates_filters_prices_child_calls_and_exports(installation):
    state, ledger, client, owner, topics, row, original = call(installation)
    child = ledger.begin_call(
        row,
        QUOTES[0],
        purpose="compaction",
        parent_call_id=original,
        context=[{"role": "system", "content": "A separate compaction prompt"}],
        reserved_microusd=100,
    )
    ledger.finish_call(child, status="succeeded", usage=Usage(8, 30, 10))
    root = f"/api/organizations/{owner['organization_id']}/inference-ledger"
    first = client.get(root, params={"limit": 1}).json()
    assert first["calls"][0]["id"] == child and first["next_before"] is not None
    older = client.get(root, params={"before": first["next_before"], "limit": 1}).json()
    assert older["calls"][0]["id"] == original and older["next_before"] is None
    detail = client.get(root + "/" + original).json()
    assert detail["quote"]["completion"] == "0.000002"
    assert detail["cached_tokens"] == 10 and detail["reasoning_tokens"] == 5
    assert detail["harness_version"] == "native-v1" and detail["prompt_version"] == "research-v1"
    assert detail["latency_ms"] >= 0
    assert len(detail["receipts"]) == 2
    assert "context_json" not in detail and "key_id" not in detail
    assert client.get(root, params={"purpose": "compaction"}).json()["calls"][0]["parent_call_id"] == original
    assert client.get(root, params={"model": "test/two"}).json()["calls"] == []
    exported = client.get(root + "/export").text
    assert len([json.loads(line) for line in exported.splitlines()]) == 2
    total = client.get(f"/api/organizations/{owner['organization_id']}/usage").json()["total"]
    assert total["runs"] == 2 and total["actual_microusd"] == 20 and total["prompt_tokens"] == 130
    with state.store.transaction(write=True) as db:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute("UPDATE inference_calls SET model='fabricated' WHERE id=?", (original,))
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute("DELETE FROM inference_call_events WHERE call_id=?", (original,))


def test_first_token_timing_is_once_only_and_survives_completion_and_reconciliation(installation):
    _, state, client, owner, topics = prepare(installation)
    enqueue(state, owner, topics[1]["id"], models=["test/one"])
    row = state.claim()
    ledger = UsageStore(state.store)
    original = ledger.begin_call(row, QUOTES[0])
    ledger.first_token(original)
    ledger.first_token(original)
    root = f"/api/organizations/{owner['organization_id']}/inference-ledger/{original}"
    running = client.get(root).json()
    assert running["status"] == "running" and running["accounting_state"] == "reserved"
    timing = running["time_to_first_token_ms"]
    assert timing >= 0 and len(running["receipts"]) == 2
    ledger.finish_call(original, status="failed", error_code="provider_incomplete")
    state.finish(row, "failed", error="provider_incomplete")
    ledger.first_token(original)
    ended = client.get(root).json()
    assert ended["time_to_first_token_ms"] == timing and len(ended["receipts"]) == 3
    assert client.put(root + "/cost", json={"actual_microusd": 3}).status_code == 204
    assert client.get(root).json()["time_to_first_token_ms"] == timing


def test_uncertain_call_reconciliation_preserves_original_receipt_and_releases_hold(installation):
    state, ledger, client, owner, topics, row, original = call(installation, unknown=True)
    root = f"/api/organizations/{owner['organization_id']}/inference-ledger"
    before = client.get(root + "/" + original).json()
    assert before["actual_microusd"] is None and before["reserved_microusd"] > 0
    assert before["diagnostics"]["http_status"] == 503
    assert client.put(root + "/" + original + "/cost", json={"actual_microusd": 3}).status_code == 204
    after = client.get(root + "/" + original).json()
    assert after["actual_microusd"] == 3 and after["accounting_state"] == "settled"
    assert after["receipts"][1] == before["receipts"][1]
    assert after["receipts"][-1]["kind"] == "reconciliation"
    assert state.run(owner["user_id"], owner["organization_id"], row["id"])["actual_microusd"] == 3
    assert client.put(root + "/" + original + "/cost", json={"actual_microusd": 0}).status_code == 409


def test_private_project_and_deleted_topic_calls_do_not_leak(installation):
    state, ledger, client, owner, topics, row, original = call(installation)
    org = owner["organization_id"]
    _, _, member = invited(installation)
    with state.store.transaction(write=True) as db:
        project = db.execute("SELECT project_id FROM channels WHERE id=?", (topics[1]["channel_id"],)).fetchone()[0]
        db.execute("UPDATE projects SET visibility='private' WHERE id=?", (project,))
    root = f"/api/organizations/{org}/inference-ledger"
    headers = {"Authorization": "Bearer " + member["access_token"]}
    assert client.get(root, headers=headers).json()["calls"] == []
    assert client.get(root + "/" + original, headers=headers).status_code == 404
    assert client.get(root + "/export", headers=headers).text == ""
    with state.store.transaction(write=True) as db:
        db.execute("UPDATE topics SET deleted_at=1 WHERE id=?", (topics[1]["id"],))
    assert client.get(root).json()["calls"] == []
    assert client.get(root + "/" + original).status_code == 404


def test_recommendations_only_match_exact_available_models_and_are_versioned():
    models = [{"id": item[0]} for item in SHORTLIST]
    value = recommendations(models)
    assert len(value["model_ids"]) == 20 and len(set(value["model_ids"])) == 20
    assert value["reviewed_at"] == "2026-10-05" and value["source"] == "https://matharena.ai/models"
    assert all(item["evidence_url"].startswith("https://matharena.ai/models/") for item in value["evidence"])
    assert recommendations([{"id": "qwen/qwen3.8-max-0902"}])["model_ids"] == []
    assert recommendations(models, pinned=["not/available", "openai/gpt-5.2"])["model_ids"] == ["openai/gpt-5.2"]


def test_safe_diagnostics_do_not_expose_arbitrary_payloads(installation):
    state, ledger, client, owner, topics, row, original = call(installation)
    child = ledger.begin_call(row, QUOTES[0], parent_call_id=original)
    ledger.finish_call(child, status="failed", diagnostics={"http_status": 400, "message": "SECRET prompt and sk-or-key"})
    detail = ledger.call(owner["user_id"], owner["organization_id"], child)
    assert "SECRET" not in json.dumps(detail) and "sk-or-key" not in json.dumps(detail)


def test_administrator_can_pin_recommendations_without_legacy_model_whitelist(installation):
    _, _, client, owner, _ = prepare(installation)
    root = f"/api/organizations/{owner['organization_id']}/model-recommendations"
    assert client.put(root, json={"model_ids": ["test/two", "test/one"]}).status_code == 204
    assert client.put(root, json={"model_ids": ["newly/available"]}).status_code == 204
    assert client.put(root, json={"model_ids": ["test/one", "test/one"]}).status_code == 400
    _, _, member = invited(installation)
    assert client.put(root, headers={"Authorization": "Bearer " + member["access_token"]}, json={"model_ids": []}).status_code == 403
    assert client.put(root, json={"model_ids": None}).status_code == 204


def test_recovery_retains_uncertain_abandoned_call_once(installation):
    state, ledger, client, owner, topics, row, original = call(installation)
    abandoned = ledger.begin_call(row, QUOTES[0], parent_call_id=original)
    ledger.recover_calls()
    result = ledger.call(owner["user_id"], owner["organization_id"], abandoned)
    assert result["status"] == "interrupted" and result["accounting_state"] == "uncertain"
    assert result["actual_microusd"] is None and result["reserved_microusd"] > 0
    ledger.recover_calls()
    assert ledger.call(owner["user_id"], owner["organization_id"], abandoned)["receipts"] == result["receipts"]
