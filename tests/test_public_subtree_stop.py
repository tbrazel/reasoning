"""Confirmed deletion fences subtree work without erasing unknown accounting."""

import pytest
from test_public_bot_runtime import fixture as bot_fixture
from test_public_bot_runtime import setup as setup_bot
from test_public_deletion import add_job
from test_public_inference import enqueue, prepare
from test_public_server import installation as installation

from reasoning_ledger.server.store import StoreError


def test_stop_and_delete_project_retains_unknown_charge_and_cancels_queue(installation):
    _, inference, client, owner, topics = prepare(installation)
    org, user, store = owner["organization_id"], owner["user_id"], inference.store
    runs = enqueue(inference, owner, topics[1]["id"])
    with store.transaction(write=True) as db:
        db.execute("UPDATE llm_runs SET status='failed',accounting_state='uncertain' WHERE id=?", (runs[0]["id"],))
        add_job(db, org, user, topics[2]["id"])
        db.execute("UPDATE compute_jobs SET parameters_json=?", ('{"resources":{"cpu":1}}',))
        project = dict(db.execute("SELECT p.* FROM projects p JOIN channels c ON c.project_id=p.id JOIN topics t "
                                 "ON t.channel_id=c.id WHERE t.id=?", (topics[1]["id"],)).fetchone())
    plan = store.deletion_plan(user, org, "projects", project["id"])
    assert not plan["can_delete"]
    response = client.request("DELETE", f"/api/organizations/{org}/projects/{project['id']}",
                              json={"confirmation": project["name"], "plan_token": plan["plan_token"], "stop_active": True})
    assert response.status_code == 204, response.text
    assert client.get(f"/api/organizations/{org}/hierarchy").json()["projects"] == []
    with store.transaction() as db:
        assert db.execute("SELECT accounting_state FROM llm_runs WHERE id=?", (runs[0]["id"],)).fetchone()[0] == "uncertain"
        assert db.execute("SELECT status FROM compute_jobs").fetchone()[0] == "cancelled"
        assert db.execute("SELECT COUNT(*) FROM topics WHERE deleted_at IS NULL").fetchone()[0] == 0
    retained = client.get(f"/api/organizations/{org}/retained-work")
    assert retained.status_code == 200
    assert retained.json()["inference"][0]["id"] == runs[0]["id"]
    assert "context_json" not in str(retained.json()) and "program" not in str(retained.json())
    response = client.put(f"/api/organizations/{org}/inference-runs/{runs[0]['id']}/cost", json={"actual_microusd": 7})
    assert response.status_code == 204, response.text
    assert client.get(f"/api/organizations/{org}/retained-work").json()["inference"] == []


def test_deleting_a_topic_fences_leased_bot_results_and_keeps_the_unknown_outcome(tmp_path):
    fixture = bot_fixture.__wrapped__(tmp_path)
    store, owner, topics, _, _, _ = fixture
    runtime, bot, _, run, _ = setup_bot(fixture)
    claimed = runtime.claim(bot["token"])
    user, org, topic = owner["user_id"], owner["organization_id"], topics[1]
    plan = store.deletion_plan(user, org, "topics", topic)
    store.delete_entity(user, org, "topics", topic, plan["name"], plan["plan_token"], stop_active=True)
    with pytest.raises(StoreError):
        runtime.finish(bot["token"], run["id"], claimed["lease"], "succeeded", "A late result must not publish.")
    with store.transaction() as db:
        row = db.execute("SELECT status,lease_hash FROM bot_runs WHERE id=?", (run["id"],)).fetchone()
        assert row["status"] == "outcome_unknown" and row["lease_hash"] is None
        assert db.execute("SELECT COUNT(*) FROM messages WHERE content=?", ("A late result must not publish.",)).fetchone()[0] == 0
