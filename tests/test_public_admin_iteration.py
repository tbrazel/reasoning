"""Owner-only creation, key accounting and retryable invitation batches.

Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
"""

import json

import pytest
from fastapi.testclient import TestClient
from test_public_inference import enqueue, prepare
from test_public_invitation_mail import CONFIG
from test_public_server import installation as installation
from test_public_server import invited
from test_public_usage import REPORTED_USAGE, finish

from reasoning_ledger.server.api import Config, create_app
from reasoning_ledger.server.mail import MailStore
from reasoning_ledger.server.store import StoreError
from reasoning_ledger.server.usage_store import UsageStore


def test_only_owners_create_organizations(installation):
    _, store, client, owner = installation
    _, admin_client, admin = invited(installation, role="admin", email="admin@example.org")
    with pytest.raises(StoreError, match="owner must"):
        store.create_organization(admin["user_id"], "Not allowed")
    created = store.create_organization(owner["user_id"], "Allowed")
    assert created["name"] == "Allowed"
    assert admin_client.post("/api/organizations", json={"name": "Blocked", "credential_mode": "none"}).status_code == 403


def test_key_accounting_known_held_unknown_and_disabled_keys(installation):
    _, state, client, owner, topics = prepare(installation)
    org, user = owner["organization_id"], owner["user_id"]
    finish(state, owner, topics[1]["id"])
    enqueue(state, owner, topics[1]["id"], client="unknown", models=["test/one"])
    claimed = state.claim()
    state.finish(claimed, "failed", usage=None)
    enqueue(state, owner, topics[1]["id"], client="held", models=["test/one"])
    totals = UsageStore(state.store).key_totals(user, org)
    assert sum(row["actual_microusd"] for row in totals) == REPORTED_USAGE.cost_microusd
    assert sum(row["calls"] for row in totals) == 3
    assert sum(row["unknown_calls"] for row in totals) == 1
    assert sum(row["reserved_microusd"] for row in totals) > 0
    assert sum(row["uncertain_microusd"] for row in totals) > 0
    endpoint = client.get(f"/api/organizations/{org}/provider-key-usage")
    assert endpoint.status_code == 200
    assert "ciphertext" not in endpoint.text and "fingerprint" not in endpoint.text
    state.store.disable_key(user, org, totals[0]["key_id"])
    assert UsageStore(state.store).key_totals(user, org)[0]["actual_microusd"] == REPORTED_USAGE.cost_microusd
    _, _, member = invited(installation)
    with pytest.raises(StoreError):
        UsageStore(state.store).key_totals(member["user_id"], org)


def test_batch_invites_deduplicate_retry_without_resending_and_keep_partial_results(installation):
    _, store, _, owner = installation
    user, org = owner["user_id"], owner["organization_id"]
    mail = MailStore(store, CONFIG, "https://chat.example.org")
    addresses = [f"member{i}@example.org" for i in range(12)]
    result = mail.enqueue_batch(user, org, addresses + [addresses[0].upper(), "bad"], "member", 3600, "a" * 32)
    assert len(result["results"]) == 13
    assert sum(row["status"] == "queued" for row in result["results"]) == 12
    assert sum(row["status"] == "retryable" for row in result["results"]) == 0
    assert result["results"][-1]["status"] == "failed"
    assert result["retry_after_seconds"] is None
    again = mail.enqueue_batch(user, org, addresses, "member", 3600, "a" * 32)
    assert [row.get("invitation_id") for row in again["results"]] == [row["invitation_id"] for row in result["results"][:12]]
    with store.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM invitations").fetchone()[0] == 12
        assert db.execute("SELECT COUNT(*) FROM invitation_mail").fetchone()[0] == 12
    assert all("token" not in row for row in result["results"])


def test_batch_permission_checks_happen_before_any_invitation(installation):
    _, store, _, owner = installation
    _, _, member = invited(installation)
    mail = MailStore(store, CONFIG, "https://chat.example.org")
    with pytest.raises(StoreError):
        mail.enqueue_batch(member["user_id"], owner["organization_id"], ["new@example.org"], "member", 3600, "a" * 32)
    _, _, admin = invited(installation, role="admin", email="admin@example.org")
    with pytest.raises(StoreError, match="only owners"):
        mail.enqueue_batch(admin["user_id"], owner["organization_id"], ["new@example.org"], "admin", 3600, "a" * 32)


def test_batch_http_contract_retries_and_validates_without_sending_mail(installation):
    config, store, _, owner = installation
    client = TestClient(create_app(Config(config.data_dir, config.public_url, mail=CONFIG)), base_url=config.public_url)
    client.headers["Authorization"] = "Bearer " + owner["access_token"]
    endpoint = f"/api/organizations/{owner['organization_id']}/invitations/batch"
    body = {"emails": ["one@example.org", "ONE@example.org", "two@example.org"], "client_id": "a" * 32}
    result = client.post(endpoint, json=body)
    assert result.status_code == 200 and len(result.json()["results"]) == 2
    assert all(row["status"] == "queued" for row in result.json()["results"])
    assert client.post(endpoint, json=body).json() == result.json()
    assert "token" not in result.text and CONFIG.api_key not in result.text
    assert client.post(endpoint, json={**body, "emails": ["three@example.org"] * 101}).status_code == 422
    with store.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM invitations").fetchone()[0] == 2


def test_key_calls_are_not_double_counted_with_provider_receipts(installation):
    from reasoning_ledger.server.openrouter import Quote

    _, state, client, owner, topics = prepare(installation)
    enqueue(state, owner, topics[1]["id"], models=["test/one"])
    claimed = state.claim()
    usage = UsageStore(state.store)
    call = usage.begin_call(claimed, Quote(**json.loads(claimed["quote_json"])))
    usage.finish_call(call, status="succeeded", usage=REPORTED_USAGE)
    state.finish(claimed, "succeeded", usage=REPORTED_USAGE)
    totals = usage.key_totals(owner["user_id"], owner["organization_id"])
    assert sum(row["calls"] for row in totals) == 1
    assert sum(row["actual_microusd"] for row in totals) == REPORTED_USAGE.cost_microusd
