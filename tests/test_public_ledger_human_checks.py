"""Exact-revision human judgments are attributed but never mathematical certification."""

import hashlib
import json
import sqlite3

import pytest
from test_public_server import installation as installation
from test_public_server import invited, structure

from reasoning_ledger.canonical import canonical_json


def claim(client, org, project, statement="For every integer n, n equals itself."):
    response = client.post(
        f"/api/organizations/{org}/mathematics",
        json={
            "name": "ledger_claim",
            "project_id": project,
            "client_id": "human-check-subject",
            "arguments": {"statement": statement},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_human_check_pins_content_actor_and_time_without_promoting_claim(installation):
    _, store, client, owner = installation
    org = owner["organization_id"]
    project, _, _ = structure(client, org)
    target = claim(client, org, project["id"])
    path = f"/api/organizations/{org}/mathematics/{target['claim_id']}"
    response = client.post(path + "/human-checks", json={"revision_id": target["revision_id"], "note": "Checked the quantifiers."})
    assert response.status_code == 200, response.text
    checked = response.json()
    assert checked["actor_id"] == owner["user_id"] and checked["actor_name"] == "Owner"
    assert checked["authority"] == "human-judgment" and checked["current"] and checked["created_at"] > 0
    with store.transaction() as db:
        record = json.loads(db.execute("SELECT body_json FROM mathematical_records WHERE path=?", (checked["record_path"],)).fetchone()[0])
        assert checked["record_sha256"] == hashlib.sha256(canonical_json(record).encode()).hexdigest()
        assert db.execute("SELECT COUNT(*) FROM mathematical_records WHERE record_type='verification'").fetchone()[0] == 0
    read = client.get(path).json()
    assert read["status"] == "conjectured"
    assert read["human_checks"]["checks"][0]["actor_id"] == owner["user_id"]
    assert read["human_checks"]["checks"][0]["authority"] == "human-judgment"
    assert (
        client.post(path + "/human-checks", json={"revision_id": target["revision_id"], "note": "Checked the quantifiers."}).json()
        == checked
    )
    assert client.post(path + "/human-checks", json={"revision_id": target["revision_id"], "note": "Changed judgment"}).status_code == 409
    assert client.get(path + "/human-checks").json() == [checked]
    with pytest.raises(sqlite3.IntegrityError, match="immutable"), store.transaction(write=True) as db:
        db.execute("UPDATE ledger_human_checks SET note='overwritten' WHERE id=?", (checked["id"],))
    with pytest.raises(sqlite3.IntegrityError, match="retained"), store.transaction(write=True) as db:
        db.execute("DELETE FROM ledger_human_checks WHERE id=?", (checked["id"],))


def test_new_revision_does_not_inherit_a_human_check_and_stale_new_checks_are_refused(installation):
    _, _, client, owner = installation
    org = owner["organization_id"]
    project, _, _ = structure(client, org)
    target = claim(client, org, project["id"])
    path = f"/api/organizations/{org}/mathematics/{target['claim_id']}"
    body = {"revision_id": target["revision_id"]}
    original = client.post(path + "/human-checks", json=body).json()
    response = client.post(
        f"/api/organizations/{org}/mathematics",
        json={
            "name": "ledger_refine",
            "client_id": "refine-human-check-subject",
            "arguments": {
                "claim_id": target["claim_id"],
                "statement": "For every real number x, x equals itself.",
                "expected_revision_id": target["revision_id"],
            },
        },
    )
    assert response.status_code == 200, response.text
    newer = response.json()
    rows = client.get(path + "/human-checks").json()
    assert len(rows) == 1 and not rows[0]["current"] and rows[0]["revision_id"] == target["revision_id"]
    assert client.get(path).json()["human_checks"]["checks"] == []
    historical = client.get(f"/api/organizations/{org}/mathematics/{target['revision_id']}").json()
    assert historical["human_checks"]["checks"][0]["id"] == original["id"]
    assert client.post(path + "/human-checks", json=body).json()["id"] == original["id"]
    _, other, _ = invited(installation)
    assert other.post(path + "/human-checks", json=body).status_code == 409
    assert other.post(path + "/human-checks", json={"revision_id": newer["revision_id"]}).json()["current"]


def test_private_scope_viewers_and_spoofed_actors_cannot_write_checks(installation):
    _, store, client, owner = installation
    org = owner["organization_id"]
    project, _, _ = structure(client, org)
    target = claim(client, org, project["id"])
    path = f"/api/organizations/{org}/mathematics/{target['claim_id']}/human-checks"
    _, viewer, _ = invited(installation, role="viewer", email="viewer@example.org")
    assert viewer.get(path).status_code == 200
    assert viewer.post(path, json={"revision_id": target["revision_id"]}).status_code == 403
    assert client.post(path, json={"revision_id": target["revision_id"], "actor_id": "pretend-person"}).status_code == 422
    _, other, _ = invited(installation)
    with store.transaction(write=True) as db:
        db.execute("UPDATE projects SET visibility='private' WHERE id=?", (project["id"],))
    assert other.get(path).status_code == 404
    assert other.post(path, json={"revision_id": target["revision_id"]}).status_code == 404
    assert client.post(path, json={"revision_id": target["revision_id"]}).status_code == 200


def test_proof_and_reference_checks_bind_their_own_exact_revision(installation):
    _, _, client, owner = installation
    org = owner["organization_id"]
    project, _, _ = structure(client, org)
    target = claim(client, org, project["id"])
    root = f"/api/organizations/{org}/mathematics"
    for name, arguments, key in (
        (
            "ledger_prove",
            {"claim_id": target["claim_id"], "argument": "Reflexivity of equality.", "expected_revision_id": target["revision_id"]},
            "proof_id",
        ),
        ("ledger_reference", {"author": "Author", "title": "Equality"}, "reference_id"),
    ):
        response = client.post(root, json={"name": name, "client_id": "check-" + name, "arguments": arguments})
        assert response.status_code == 200, response.text
        created = response.json()
        check = client.post(root + "/" + created[key] + "/human-checks", json={"revision_id": created["revision_id"]})
        assert check.status_code == 200, check.text
        assert check.json()["object_id"] == created[key] and check.json()["current"]
