"""Scoped navigation accounting, rename authorization and deletion evidence."""

import pytest
from test_public_deletion import add_job
from test_public_inference import enqueue, prepare
from test_public_server import installation as installation
from test_public_server import invited, structure
from test_public_usage import finish
from test_public_workflow_runs import compute_node
from test_public_workflow_runs import installation as workflow_installation

from reasoning_ledger.server.navigation_api import install_routes
from reasoning_ledger.server.openrouter import Usage
from reasoning_ledger.server.store import StoreError
from reasoning_ledger.server.usage_store import UsageStore
from reasoning_ledger.server.workflow_store import WorkflowStore


def routes(client, store):
    route = next(route for route in client.app.routes if getattr(route, "path", None) == "/api/organizations/{organization_id}/hierarchy")
    authenticated = route.dependant.dependencies[0].call
    install_routes(client.app, store, authenticated)


def test_topic_rename_keeps_history_and_enforces_channel_management(installation):
    _, store, client, owner = installation
    org, user = owner["organization_id"], owner["user_id"]
    _, _, topics = structure(client, org)
    root = f"/api/organizations/{org}"
    routes(client, store)
    topic = topics[1]
    store.post_message(user, org, topic["id"], "Retained conversation", "retain")
    renamed = client.patch(root + "/topics/" + topic["id"], json={"name": "New topic title"})
    assert renamed.status_code == 200 and renamed.json()["id"] == topic["id"]
    assert next(item for item in client.get(root + "/hierarchy").json()["topics"] if item["id"] == topic["id"])["name"] == "New topic title"
    assert client.get(root + f"/topics/{topic['id']}/messages").json()[0]["content"] == "Retained conversation"
    assert client.patch(root + "/topics/" + topic["id"], json={"name": "   "}).status_code == 422
    _, _, member = invited(installation)
    with pytest.raises(StoreError) as denied:
        store.rename_entity(member["user_id"], org, "topics", topic["id"], "Unauthorized")
    assert denied.value.status == 403


def test_confirmed_navigation_costs_do_not_double_count_calls_or_unknown_reservations(installation):
    _, state, client, owner, topics = prepare(installation)
    org, user = owner["organization_id"], owner["user_id"]
    routes(client, state.store)
    state.store.set_policy(user, org, ["test/one", "test/two"], 10000000)
    first = finish(state, owner, topics[1]["id"], usage=Usage(2500000, 2, 3))[0]
    usage = UsageStore(state.store)
    with state.store.transaction() as db:
        original = dict(db.execute("SELECT r.*,q.topic_id,q.requested_by,q.context_json FROM llm_runs r "
                                   "JOIN llm_requests q ON q.id=r.request_id WHERE r.id=?", (first["id"],)).fetchone())
    from reasoning_ledger.server.openrouter import Quote

    quote = Quote("test/one", "0.000001", "0.000002", "0", 200000, 8192)
    for amount in (100000, 200000):
        call = usage.begin_call(original, quote)
        usage.finish_call(call, status="succeeded", usage=Usage(amount, 2, 3))
    legacy = finish(state, owner, topics[1]["id"], client="legacy-navigation", usage=Usage(1230000, 2, 3))[0]
    uncertain = enqueue(state, owner, topics[1]["id"], client="uncertain-navigation", models=["test/one"])[0]
    result = client.get(f"/api/organizations/{org}/navigation-usage")
    assert result.status_code == 200, result.text
    summary = result.json()
    assert summary["topics"][topics[1]["id"]] == {"actual_microusd": 1530000, "unknown_calls": 1, "external_runs": 0}
    with state.store.transaction() as db:
        channel = db.execute("SELECT channel_id FROM topics WHERE id=?", (topics[1]["id"],)).fetchone()[0]
        project = db.execute("SELECT project_id FROM channels WHERE id=?", (channel,)).fetchone()[0]
    assert summary["channels"][channel]["actual_microusd"] == summary["projects"][project]["actual_microusd"] == 1530000
    assert uncertain["id"] != legacy["id"]


def test_lagging_workflow_parent_with_terminal_nodes_does_not_block_channel_deletion(installation):
    _, store, client, owner = installation
    org, user = owner["organization_id"], owner["user_id"]
    _, channel, topics = structure(client, org)
    saved = WorkflowStore(store).create(user, org, topics[2]["id"], "navigation-workflow-definition", {
        "format": 1, "name": "Historical approval", "nodes": [{"id": "approve", "kind": "approval", "depends_on": [], "inputs": {},
            "parameters": {"message": "Review", "required_role": "admin"}}]})
    run = client.app.state.workflow_runs.start(user, org, saved["workflow_id"], 1, "navigation-workflow-start")
    assert not store.deletion_plan(user, org, "channels", channel["id"])["can_delete"]
    # A worker may stop after retaining the node completion, before updating its parent summary.
    with store.transaction(write=True) as db:
        db.execute("UPDATE workflow_node_attempts SET status='succeeded',finished_at=1 WHERE run_id=?", (run["id"],))
    plan = store.deletion_plan(user, org, "channels", channel["id"])
    assert plan["active"]["workflows"] == 0 and plan["can_delete"]
    store.delete_entity(user, org, "channels", channel["id"], channel["name"], plan["plan_token"])
    with store.transaction() as db:
        assert db.execute("SELECT status FROM workflow_runs WHERE id=?", (run["id"],)).fetchone()[0] == "running"


def test_unknown_execution_stays_blocking_and_names_exact_action(installation):
    _, store, client, owner = installation
    org, user = owner["organization_id"], owner["user_id"]
    _, channel, topics = structure(client, org)
    with store.transaction(write=True) as db:
        add_job(db, org, user, topics[2]["id"])
        db.execute("UPDATE compute_jobs SET status='outcome_unknown'")
    plan = store.deletion_plan(user, org, "channels", channel["id"])
    assert not plan["can_delete"]
    assert plan["blockers"] == [{"kind": "compute", "id": "d" * 32, "topic_id": topics[2]["id"], "status": "outcome_unknown",
                                  "action": "Cancel the compute job; reconcile any unknown outcome."}]


def test_navigation_spend_does_not_expose_private_project(installation):
    _, store, client, owner = installation
    _, _, member = invited(installation)
    org = owner["organization_id"]
    project = client.post(f"/api/organizations/{org}/projects", json={"name": "Private", "visibility": "private",
        "owner_id": owner["user_id"], "member_ids": [owner["user_id"]]}).json()
    channel = client.post(f"/api/organizations/{org}/channels", json={"name": "private", "project_id": project["id"]}).json()
    topic = client.post(f"/api/organizations/{org}/topics", json={"name": "private", "kind": "alpha", "channel_id": channel["id"]}).json()
    result = UsageStore(store).navigation_totals(member["user_id"], org)
    assert project["id"] not in result["projects"] and channel["id"] not in result["channels"] and topic["id"] not in result["topics"]


def test_lagging_workflow_checks_real_child_accounting_before_allowing_deletion(tmp_path):
    env = workflow_installation.__wrapped__(tmp_path)
    run = env.start([compute_node()])
    env.runs.advance(run["id"])
    child = env.claim()
    assert not env.store.deletion_plan(env.user, env.org, "topics", env.topic)["can_delete"]
    env.finish(child)
    # Parent and node summaries lag, but the actual job has an authenticated terminal receipt.
    with env.store.transaction() as db:
        assert db.execute("SELECT status FROM workflow_runs WHERE id=?", (run["id"],)).fetchone()[0] == "running"
        assert db.execute("SELECT status FROM workflow_node_attempts WHERE run_id=?", (run["id"],)).fetchone()[0] == "running"
    plan = env.store.deletion_plan(env.user, env.org, "topics", env.topic)
    assert plan["active"]["workflows"] == 0 and plan["can_delete"]
    with env.store.transaction(write=True) as db:
        db.execute("UPDATE compute_jobs SET status='outcome_unknown',accounted_at=NULL WHERE id=?", (child["id"],))
    plan = env.store.deletion_plan(env.user, env.org, "topics", env.topic)
    assert plan["active"]["workflows"] == 1 and plan["active"]["compute"] == 1 and not plan["can_delete"]
