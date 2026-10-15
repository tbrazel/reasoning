"""Hierarchical delegation, narrow discovery, local replay and approach memory."""

import json
from concurrent.futures import ThreadPoolExecutor
from typing import get_args

from postgres_fixture import postgres_configuration as postgres_configuration
from test_public_work_contracts import specification
from test_public_work_jobs import claim, environment, expire, lease, queued, result, worker_post
from test_public_work_jobs import installation as installation

from reasoning_ledger.server.research_context import encoded
from reasoning_ledger.server.security import digest
from reasoning_ledger.server.work_contracts import Role
from reasoning_ledger.server.work_jobs import WorkJobs
from reasoning_ledger.server.work_tool_contracts import Delegate
from reasoning_ledger.server.work_tools import MODELS


def coordinator(installation, **limits):
    env = environment(installation, {"max_concurrency": 8, "allowed_tools": list(MODELS), **limits})
    response = env["client"].post(
        f"/api/organizations/{env['owner']['organization_id']}/work-workers",
        json={"name": "All-role fixture", "project_ids": [env["project"]["id"]], "roles": list(get_args(Role)), "max_parallel": 8},
    )
    assert response.status_code == 201
    env["worker"] = response.json()
    queued(env, "coordinate", role="coordinator")
    env["parent"] = claim(env)
    return env


def call(env, row, tool, client_id, **arguments):
    return worker_post(env, f"jobs/{row['id']}/tools/{tool}", lease(row) | {"client_id": client_id, **arguments})


def delegate(env, client_id="child", row=None, role="worker", **changes):
    return call(
        env,
        row or env["parent"],
        "work_delegate",
        client_id,
        expected_root_revision=1,
        specification=specification(role=role, resources=env["resources"], **changes),
    )


def knowledge(env, name, project=None, topic=None, scope="project", body="A primary source remains to be checked."):
    org = env["owner"]["organization_id"]
    response = env["client"].post(
        f"/api/organizations/{org}/knowledge",
        json={
            "client_id": "knowledge-fixture-" + name,
            "kind": "ledger",
            "title": name,
            "body": body,
            "state": "note",
            "scope_kind": "topic" if topic else scope,
            "scope_id": topic or project or (env["project"]["id"] if scope == "project" else None),
        },
    )
    assert response.status_code == 201, response.text
    entry = response.json()
    return {
        "kind": "knowledge",
        "id": entry["id"],
        "revision": entry["revision"],
        "sha256": digest(encoded({key: entry[key] for key in ("title", "body", "state")})),
    }


def test_delegate_commits_child_queue_receipt_and_accounting_once(installation):
    env = coordinator(installation)
    first = delegate(env)
    assert first.status_code == 200, first.text
    saved = first.json()
    assert saved["replayed"] is False and saved["sha256"] == digest(encoded(saved["result"]))
    child = saved["result"]["task"]
    assert child["parent_id"] == env["parent"]["task_id"]
    assert saved["result"]["job"]["status"] == "queued"
    replay = delegate(env).json()
    assert replay["replayed"] and replay["result"] == saved["result"]
    assert delegate(env, objective="Changed input").status_code == 409
    with env["store"].transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM work_tool_operations").fetchone()[0] == 1
        assert (
            db.execute(
                "SELECT tools FROM work_resource_accounts WHERE scope_kind='program' AND scope_id=?", (env["root"]["id"],)
            ).fetchone()[0]
            == 1
        )
        assert db.execute("SELECT COUNT(*) FROM work_jobs").fetchone()[0] == 2


def test_local_receipt_replays_after_worker_loss_without_duplicate_children(installation):
    env = coordinator(installation)
    saved = delegate(env).json()
    expire(env, env["parent"]["id"])
    WorkJobs(env["store"]).expire_due()
    fresh = claim(env, "fresh-coordinator")
    assert fresh["id"] == env["parent"]["id"] and fresh["fence"] == env["parent"]["fence"] + 1
    replay = delegate(env, row=fresh).json()
    assert replay["replayed"] and replay["result"] == saved["result"]
    assert delegate(env).status_code == 409
    with env["store"].transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM work_tasks WHERE parent_id=?", (fresh["task_id"],)).fetchone()[0] == 1


def test_concurrent_delegation_uses_one_transaction_and_one_child(installation):
    env = coordinator(installation)
    tools = env["client"].app.state.work_tools
    body = Delegate(
        **lease(env["parent"]),
        client_id="concurrent",
        expected_root_revision=1,
        specification=specification(role="librarian", resources=env["resources"]),
    )
    with ThreadPoolExecutor(max_workers=4) as pool:
        replies = list(pool.map(lambda _: tools.execute(env["worker"]["token"], env["parent"]["id"], "work_delegate", body), range(4)))
    assert sum(not reply["replayed"] for reply in replies) == 1
    assert len({reply["result"]["task"]["id"] for reply in replies}) == 1


def test_hierarchical_coordinators_inspect_only_direct_children_and_cancel_a_branch(installation):
    env = coordinator(installation)
    sub = delegate(env, "sub-coordinator", role="coordinator").json()["result"]
    sibling = delegate(env, "sibling").json()["result"]
    subordinate = claim(env, "subordinate")
    assert subordinate["task_id"] == sub["task"]["id"]
    grand = delegate(env, "grandchild", row=subordinate, role="librarian").json()["result"]
    listing = call(env, env["parent"], "work_children", "page", limit=1).json()["result"]
    assert len(listing["items"]) == 1 and listing["next"]
    second = call(env, env["parent"], "work_children", "next-page", after=listing["next"], limit=1).json()["result"]
    assert {listing["items"][0]["id"], second["items"][0]["id"]} == {sub["task"]["id"], sibling["task"]["id"]}
    assert "specification" not in listing["items"][0]
    assert call(env, env["parent"], "work_child_read", "not-grandchild", child_id=grand["task"]["id"]).status_code == 404
    assert call(env, subordinate, "work_child_cancel", "not-sibling", child_id=sibling["task"]["id"], reason="Stop").status_code == 404
    stopped = call(env, env["parent"], "work_child_cancel", "stop-subtree", child_id=sub["task"]["id"], reason="Redirect this branch")
    assert stopped.status_code == 200, stopped.text
    with env["store"].transaction() as db:
        assert db.execute("SELECT cancel_requested FROM work_jobs WHERE id=?", (subordinate["id"],)).fetchone()[0] == 1
        assert db.execute("SELECT status FROM work_jobs WHERE id=?", (grand["job"]["id"],)).fetchone()[0] == "cancelled"
        assert db.execute("SELECT status FROM work_jobs WHERE id=?", (sibling["job"]["id"],)).fetchone()[0] == "queued"
    assert result(env, subordinate, outcome="cancelled").status_code == 200


def test_retrieval_explicitly_binds_only_project_and_org_versions(installation):
    env = coordinator(installation)
    own = knowledge(env, "source-project")
    global_ref = knowledge(env, "source-organization", scope="organization")
    knowledge(env, "source-topic", topic=env["topics"][1]["id"])
    other = env["client"].post(f"/api/organizations/{env['owner']['organization_id']}/projects", json={"name": "Other"}).json()
    knowledge(env, "source-other", project=other["id"])
    row = env["parent"]
    assert call(env, row, "input_read", "unassigned", reference=own).status_code == 404
    search = call(env, row, "knowledge_search", "discover", query="source-").json()["result"]
    refs = [item["reference"] for item in search["items"]]
    assert {item["id"] for item in refs} == {own["id"], global_ref["id"]}
    assert all("body" not in item for item in search["items"])
    source = call(env, row, "input_read", "read-source", reference=own).json()["result"]
    assert "primary source" in source["text"]
    child = delegate(env, "pin-source", inputs=[own]).json()["result"]["task"]
    assert child["specification"]["inputs"] == [own]
    body = lease(row) | {"client_id": "compact-discovery", "summary": "Retain retrieved source", "retained_inputs": [own]}
    assert worker_post(env, f"jobs/{row['id']}/compact", body).status_code == 200


def test_unassigned_source_and_unrelated_dependency_cannot_be_delegated(installation):
    env = coordinator(installation)
    unassigned = knowledge(env, "not-retrieved")
    assert delegate(env, "unassigned-source", inputs=[unassigned]).status_code == 404
    outsider, _ = queued(env, "not-a-child")
    reply = call(
        env,
        env["parent"],
        "work_delegate",
        "unrelated-dependency",
        expected_root_revision=1,
        specification=specification(resources=env["resources"]),
        depends_on=[outsider["id"]],
    )
    assert reply.status_code == 404
    with env["store"].transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM work_tool_operations").fetchone()[0] == 0


def test_child_results_bind_exact_outputs_not_sibling_transcripts(installation):
    env = coordinator(installation)
    source = knowledge(env, "child-evidence")
    child = delegate(env).json()["result"]["task"]
    row = claim(env, "child-result")
    assert row["task_id"] == child["id"]
    body = lease(row) | {"outcome": "result", "summary": "A scoped finding needing source review", "outputs": [source]}
    assert worker_post(env, f"jobs/{row['id']}/result", body).status_code == 200
    inspected = call(env, env["parent"], "work_child_read", "inspect-result", child_id=child["id"]).json()["result"]
    assert inspected["result"]["outputs"] == [source] and inspected["result_sha256"]
    assert "transcript" not in inspected
    assert call(env, env["parent"], "input_read", "retained-output", reference=source).status_code == 200
    retained = call(env, env["parent"], "input_read", "retained-child-result", reference=inspected["result_reference"])
    assert retained.status_code == 200 and "scoped finding" in retained.json()["result"]["text"]
    reviewed = delegate(env, "critic", role="critic", inputs=[inspected["result_reference"]])
    assert reviewed.status_code == 200 and reviewed.json()["result"]["job"]["lane"] == "review"


def test_explicit_tool_ceiling_is_shared_and_replays_do_not_spend_again(installation):
    env = coordinator(installation, max_tool_calls=1)
    assert delegate(env).status_code == 200
    assert delegate(env).json()["replayed"]
    assert delegate(env, "over-limit").status_code == 409
    child = claim(env, "child-tool-limit")
    assert call(env, child, "knowledge_search", "shared-parent-cap").status_code == 409


def test_obstructed_approach_memory_uses_existing_versioned_ledger(installation):
    env = coordinator(installation)
    row = env["parent"]
    args = {
        "title": "An obstructed approach",
        "approach": "Try the direct construction",
        "status": "obstructed",
        "obstruction": "The required hypothesis is missing",
        "reopening_condition": "A source supplies the missing hypothesis",
    }
    write = call(env, row, "approach_write", "memory-1", **args)
    assert write.status_code == 200, write.text
    reference = write.json()["result"]["reference"]
    assert call(env, row, "approach_write", "memory-1", **args).json()["replayed"]
    with env["store"].transaction() as db:
        entry = db.execute("SELECT kind,project_id FROM knowledge_entries WHERE id=?", (reference["id"],)).fetchone()
        assert entry["kind"] == "ledger" and entry["project_id"] == env["project"]["id"]
        stored = db.execute("SELECT body,state FROM knowledge_versions WHERE entry_id=? AND revision=1", (reference["id"],)).fetchone()
        assert stored["state"] == "note"
        note = json.loads(stored["body"])
        assert note["schema_type"] == "work_approach" and note["reopening_condition"] == args["reopening_condition"]
    revised = call(
        env, row, "approach_write", "memory-2", **(args | {"status": "promising"}), entry_id=reference["id"], expected_revision=1
    )
    assert revised.status_code == 200 and revised.json()["result"]["reference"]["revision"] == 2
    assert call(env, row, "approach_write", "stale-memory", **args, entry_id=reference["id"], expected_revision=1).status_code == 409
    assert call(env, row, "input_read", "original-obstruction", reference=reference).status_code == 200


def test_memory_does_not_replace_unrelated_knowledge_or_omit_reopening_conditions(installation):
    env = coordinator(installation)
    original = knowledge(env, "human-ledger-note")
    args = {"title": "A proposed approach", "approach": "Investigate", "status": "obstructed"}
    assert call(env, env["parent"], "approach_write", "missing-obstruction", **args).status_code == 422
    args |= {"obstruction": "Missing source", "reopening_condition": "Obtain the source"}
    assert call(env, env["parent"], "approach_write", "not-memory", **args, entry_id=original["id"], expected_revision=1).status_code == 403


def test_available_function_schemas_omit_worker_credentials(installation):
    env = coordinator(installation)
    schemas = env["parent"]["packet"]["tools"]
    assert {item["function"]["name"] for item in schemas} == set(MODELS)
    for schema in schemas:
        assert not {"lease", "attempt_id", "fence", "client_id"} & set(schema["function"]["parameters"]["properties"])
        assert "$ref" not in json.dumps(schema) and "$defs" not in json.dumps(schema)
    assert env["worker"]["token"] not in json.dumps(schemas)


def test_narrowed_root_grants_hide_and_refuse_local_tools(installation):
    env = coordinator(installation)
    narrowed = env["resources"] | {"allowed_tools": []}
    response = env["client"].patch(
        env["target"],
        json={"expected_revision": 1, "reason": "Stop automatic local tools", "specification": specification(resources=narrowed)},
    )
    assert response.status_code == 200, response.text
    context = worker_post(env, f"jobs/{env['parent']['id']}/context", lease(env["parent"])).json()
    assert context["tools"] == [] and context["authorization_revision"] == 2
    assert call(env, env["parent"], "knowledge_search", "revoked-tool").status_code == 403


def test_source_revision_head_changes_do_not_rewrite_discovery(installation):
    env = coordinator(installation)
    reference = knowledge(env, "pinned-discovery", body="Original source")
    search = call(env, env["parent"], "knowledge_search", "initial-search", query="pinned-discovery")
    assert search.status_code == 200
    path = f"/api/organizations/{env['owner']['organization_id']}/knowledge/{reference['id']}"
    changed = env["client"].put(
        path,
        json={
            "client_id": "knowledge-fixture-update-0001",
            "expected_revision": 1,
            "kind": "ledger",
            "title": "pinned-discovery",
            "body": "Revised source",
            "state": "note",
        },
    )
    assert changed.status_code == 200, changed.text
    read = call(env, env["parent"], "input_read", "old-source", reference=reference).json()["result"]
    assert "Original source" in read["text"] and "Revised source" not in read["text"]
    latest = call(env, env["parent"], "knowledge_search", "new-search", query="pinned-discovery").json()["result"]["items"][0]["reference"]
    assert latest["revision"] == 2 and latest["sha256"] != reference["sha256"]


def test_atomic_operation_and_discovery_history_survive_portable_recovery(installation, tmp_path):
    from reasoning_ledger.server.backup import backup, create_key, restore
    from reasoning_ledger.server.store import Store

    env = coordinator(installation)
    reference = knowledge(env, "recovery-source")
    response = call(env, env["parent"], "knowledge_search", "recover-search", query="recovery-source")
    assert response.status_code == 200
    child = delegate(env, "recover-child", inputs=[reference]).json()["result"]["task"]
    key, archive, destination = tmp_path / "key", tmp_path / "archive.rnbk", tmp_path / "recovered"
    create_key(key)
    saved = backup(env["store"].data_dir, archive, key)
    restore(archive, key, saved["sha256"], destination, source_stopped=True)
    recovered = Store(destination)
    with recovered.transaction() as db:
        retained = db.execute(
            "SELECT result_sha256 FROM work_tool_operations WHERE task_id=? AND client_id=?", (env["parent"]["task_id"], "recover-search")
        ).fetchone()[0]
        assert retained == response.json()["sha256"]
        assert (
            db.execute(
                "SELECT input_sha256 FROM work_input_bindings WHERE task_id=? AND input_id=?", (env["parent"]["task_id"], reference["id"])
            ).fetchone()[0]
            == reference["sha256"]
        )
        assert db.execute("SELECT status FROM work_tasks WHERE id=?", (child["id"],)).fetchone()[0] == "cancelled"


def test_failed_local_operation_rolls_back_task_queue_and_tool_account(installation, monkeypatch):
    env = coordinator(installation)
    jobs = env["client"].app.state.work_jobs

    def unavailable(*arguments):
        raise RuntimeError("Injected database operation failure before commit")

    monkeypatch.setattr(jobs, "_enqueue", unavailable)
    model = Delegate(
        **lease(env["parent"]),
        client_id="interrupted-local",
        expected_root_revision=1,
        specification=specification(role="worker", resources=env["resources"]),
    )
    try:
        env["client"].app.state.work_tools.execute(env["worker"]["token"], env["parent"]["id"], "work_delegate", model)
    except RuntimeError as exc:
        assert str(exc).startswith("Injected database operation failure")
    else:
        raise AssertionError("injected local failure was not propagated")
    with env["store"].transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM work_tasks").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM work_jobs").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM work_tool_operations").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM work_resource_accounts").fetchone()[0] == 0


def test_worker_role_revocation_refuses_new_tools_and_hides_replayed_context(installation):
    env = coordinator(installation)
    with env["store"].transaction(write=True) as db:
        db.execute("UPDATE work_workers SET roles_json='[\"worker\"]' WHERE id=?", (env["worker"]["id"],))
    assert call(env, env["parent"], "knowledge_search", "revoked-role").status_code == 403
    effect = lease(env["parent"]) | {"client_id": "revoked-effect", "kind": "local", "tool": "knowledge_search", "request_sha256": "a" * 64}
    assert worker_post(env, f"jobs/{env['parent']['id']}/effects", effect).status_code == 403
    replay = claim(env)
    assert replay["dispatch"] is False and replay["packet"] is None


def test_retained_work_result_reference_cannot_cross_project_boundaries(installation):
    env = coordinator(installation)
    child = delegate(env).json()["result"]["task"]
    row = claim(env, "completed-child")
    assert row["task_id"] == child["id"]
    completed = result(env, row)
    assert completed.status_code == 200
    reference = {"kind": "work_result", "id": row["id"], "revision": row["fence"], "sha256": completed.json()["sha256"]}
    org = env["owner"]["organization_id"]
    other = env["client"].post(f"/api/organizations/{org}/projects", json={"name": "Other"}).json()
    foreign_path = env["path"].replace(env["project"]["id"], other["id"])
    response = env["client"].post(foreign_path, json={"client_id": "foreign-result", "specification": specification(inputs=[reference])})
    assert response.status_code == 404
    bad = reference | {"sha256": "a" * 64}
    response = env["client"].post(env["path"], json={"client_id": "bad-result-digest", "specification": specification(inputs=[bad])})
    assert response.status_code == 409
