"""Incremental overview counts, bounded neighborhoods, tenant scope and recovery."""

import pytest
from postgres_fixture import postgres_configuration as postgres_configuration
from test_public_work_jobs import installation as installation
from test_public_work_tools import call, coordinator, delegate


@pytest.fixture(params=["sqlite", "postgresql"])
def old_installation(request, tmp_path, monkeypatch):
    from test_public_server import installation as base_installation

    import reasoning_ledger.server.store as module
    from reasoning_ledger.server.migrations import MIGRATIONS, WORK_OVERVIEW_SCHEMA

    if request.param == "postgresql":
        request.getfixturevalue("postgres_configuration")
    with monkeypatch.context() as context:
        context.setattr(module, "MIGRATIONS", MIGRATIONS[:MIGRATIONS.index(WORK_OVERVIEW_SCHEMA)])
        return base_installation.__wrapped__(tmp_path)


def test_incremental_status_counts_and_bounded_branch_search(installation):
    env = coordinator(installation)
    for index in range(5):
        delegate(env, f"branch-{index}", objective=f"Inspect evidence {index} with 100% fidelity").raise_for_status()
    overview = env["client"].get(env["target"] + "/overview").json()
    assert overview["task_total"] == 6 and overview["tasks"] == {"ready": 5, "running": 1}
    assert overview["resources"]["tools"] == 5
    path = env["target"] + "/branches"
    page = env["client"].get(path, params={"limit": 2, "parent": env["parent"]["task_id"]}).json()
    assert len(page["items"]) == 2 and page["next"] == page["items"][-1]["id"]
    assert "specification" not in page["items"][0] and "inputs" not in page["items"][0]
    last = env["client"].get(path, params={"limit": 3, "after": page["next"], "parent": env["parent"]["task_id"]}).json()
    assert len(last["items"]) == 3 and last["next"] is None
    assert len(env["client"].get(path, params={"q": "100%", "status": "ready"}).json()["items"]) == 5
    assert env["client"].get(path, params={"q": "100_"}).json()["items"] == []
    with env["store"].transaction(write=True) as db:
        db.execute("UPDATE work_tasks SET status='blocked' WHERE id=?", (page["items"][0]["id"],))
        db.execute("UPDATE work_tasks SET status='blocked' WHERE id=?", (page["items"][0]["id"],))
    current = env["client"].get(env["target"] + "/overview").json()
    assert current["task_total"] == 6 and current["tasks"]["blocked"] == 1 and current["tasks"]["ready"] == 4
    with pytest.raises(RuntimeError), env["store"].transaction(write=True) as db:
        db.execute("UPDATE work_tasks SET status='complete' WHERE id=?", (page["items"][0]["id"],))
        raise RuntimeError("transaction rollback")
    assert env["client"].get(env["target"] + "/overview").json()["tasks"] == current["tasks"]
    assert env["client"].get(path, params={"limit": 101}).status_code == 422
    assert env["client"].get(path, params={"parent": "0" * 32}).status_code == 404


def test_private_project_overview_uses_the_same_project_authority(installation):
    from test_public_server import invited

    env = coordinator(installation)
    root = f"/api/organizations/{env['owner']['organization_id']}"
    project = env["client"].post(root + "/projects", json={
        "name": "Private work", "visibility": "private", "owner_id": env["owner"]["user_id"],
    }).json()
    path = root + "/projects/" + project["id"] + "/work"
    created = env["client"].post(path, json={
        "client_id": "private-work", "specification": env["root"]["version"]["specification"],
    })
    created.raise_for_status()
    target = path + "/" + created.json()["id"]
    _, outsider, _ = invited(installation)
    for suffix in ("/overview", "/branches", "/controllers"):
        assert outsider.get(target + suffix).status_code == 404
    reference = {"kind": "work_spec", "id": created.json()["id"], "revision": 1,
                 "sha256": created.json()["version"]["sha256"]}
    assert outsider.post(target + "/tasks/unknown/inputs/read", json={"reference": reference}).status_code == 404


def test_portable_snapshot_rebuilds_counts_once_and_recovery_updates_them(installation, tmp_path):
    from reasoning_ledger.server.backup import backup, create_key, restore
    from reasoning_ledger.server.store import Store
    from reasoning_ledger.server.work_overview import WorkOverview

    env = coordinator(installation)
    for index in range(3):
        delegate(env, f"retained-{index}").raise_for_status()
    key, archive, destination = tmp_path / "key", tmp_path / "work.rnbk", tmp_path / "recovered"
    create_key(key)
    saved = backup(env["store"].data_dir, archive, key)
    restore(archive, key, saved["sha256"], destination, source_stopped=True)
    recovered = Store(destination)
    overview = WorkOverview(recovered).read(env["owner"]["user_id"], env["owner"]["organization_id"],
                                           env["project"]["id"], env["root"]["id"])
    assert overview["task_total"] == 4
    assert overview["tasks"]["cancelled"] == 3 and overview["tasks"]["outcome_unknown"] == 1


def test_populated_migration_backfills_task_states_before_live_transitions(old_installation):
    from reasoning_ledger.server.store import Store
    from reasoning_ledger.server.work_overview import WorkOverview

    env = coordinator(old_installation)
    for index in range(4):
        delegate(env, f"existing-{index}").raise_for_status()
    migrated = Store(env["store"].data_dir)
    service = WorkOverview(migrated)
    args = env["owner"]["user_id"], env["owner"]["organization_id"], env["project"]["id"], env["root"]["id"]
    assert service.read(*args)["tasks"] == {"ready": 4, "running": 1}
    with migrated.transaction(write=True) as db:
        db.execute("UPDATE work_tasks SET status='review' WHERE id=?", (env["parent"]["task_id"],))
    assert service.read(*args)["tasks"] == {"ready": 4, "review": 1}


def test_counter_migration_failure_rolls_back_backfill_and_can_retry(old_installation, monkeypatch):
    import reasoning_ledger.server.store as module
    from reasoning_ledger.server.migrations import MIGRATIONS, WORK_OVERVIEW_SCHEMA
    from reasoning_ledger.server.work_overview import WorkOverview

    env = coordinator(old_installation)
    delegate(env, "retained-before-retry").raise_for_status()
    counter_index = MIGRATIONS.index(WORK_OVERVIEW_SCHEMA)
    broken = MIGRATIONS[:counter_index] + (
        WORK_OVERVIEW_SCHEMA + ("SELECT no_such_work_overview_column FROM work_tasks",),
    ) + MIGRATIONS[counter_index + 1:]
    with monkeypatch.context() as context:
        context.setattr(module, "MIGRATIONS", broken)
        with pytest.raises(Exception, match="no_such_work_overview_column"):
            module.Store(env["store"].data_dir)
    with env["store"].transaction() as db:
        if env["store"]._postgres:
            present = db.execute("SELECT 1 FROM information_schema.tables WHERE table_schema=? AND table_name='work_task_counts'",
                                 (env["store"]._postgres.config.schema,)).fetchone()
        else:
            present = db.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='work_task_counts'").fetchone()
        assert present is None
    migrated = module.Store(env["store"].data_dir)
    overview = WorkOverview(migrated).read(env["owner"]["user_id"], env["owner"]["organization_id"],
                                          env["project"]["id"], env["root"]["id"])
    assert overview["task_total"] == 2 and overview["tasks"] == {"running": 1, "ready": 1}


def test_controller_and_check_pages_separate_headers_from_exact_evidence(installation):
    from reasoning_ledger.server.work_controllers import WorkControllers

    env = coordinator(installation)
    started = env["client"].post(env["target"] + "/controllers", json={"client_id": "view", "expected_revision": 1})
    started.raise_for_status()
    flow = started.json()
    page = env["client"].get(env["target"] + "/controllers").json()
    assert len(page["items"]) == 1 and page["items"][0]["id"] == flow["id"]
    assert "result" not in page["items"][0] and "specification" not in page["items"][0]
    subject = {"kind": "work_task", "id": flow["target_task"], "revision": 1, "sha256": "a" * 64}
    with env["store"].transaction(write=True) as db:
        for index in range(3):
            # Populate immutable check rows using the same retained-record writer.
            # This verifies pagination, not acceptance of fabricated evidence.
            WorkControllers(env["store"])._record(db, flow, f"view-{index}", "coverage", "human_review", subject,
                                                   False, "fixture_no_acceptance", {"explanation": "Unchecked fixture evidence"}, None)
    path = env["target"] + "/controllers/" + flow["id"] + "/checks"
    first = env["client"].get(path, params={"limit": 2}).json()
    assert len(first["items"]) == 2 and first["next"]
    assert "body" not in first["items"][0] and "body_json" not in first["items"][0]
    detail = env["client"].get(path + "/" + first["items"][0]["id"]).json()
    assert detail["body"]["explanation"] == "Unchecked fixture evidence" and detail["sha256"] == first["items"][0]["sha256"]
    assert len(env["client"].get(path, params={"after": first["next"]}).json()["items"]) == 1
    assert env["client"].get(path + "/unknown").status_code == 404


def test_human_exact_input_reads_are_scoped_and_do_not_charge_worker_tools(installation):
    from test_public_server import invited

    env = coordinator(installation)
    written = call(env, env["parent"], "workspace_write", "human-source", path="evidence.txt", text="😊" * 8000)
    written.raise_for_status()
    added = call(env, env["parent"], "workspace_patch", "human-source-tail", path="evidence.txt",
                 expected_sha256=written.json()["result"]["reference"]["sha256"],
                 changes=[{"start": 8000, "end": 8000, "text": "😊" * 9000}])
    added.raise_for_status()
    reference = added.json()["result"]["reference"]
    path = env["target"] + "/tasks/" + env["parent"]["task_id"] + "/inputs/read"
    before = env["client"].get(env["target"] + "/overview").json()["resources"]
    _, viewer, _ = invited(installation, role="viewer")
    first = viewer.post(path, json={"reference": reference, "offset": 0, "length": 8000})
    assert first.status_code == 200, first.text
    assert first.json()["text"] == "😊" * 8000 and first.json()["truncated"]
    second = viewer.post(path, json={"reference": reference, "offset": 8000, "length": 8000})
    assert second.json()["text"] == "😊" * 8000 and second.json()["total_characters"] == 17000
    assert viewer.post(path, json={"reference": reference | {"sha256": "0" * 64}}).status_code == 404
    assert delegate(env, "unassigned-human").status_code == 200
    child = env["client"].get(env["target"] + "/branches", params={"parent": env["parent"]["task_id"]}).json()["items"][0]
    assert viewer.post(env["target"] + "/tasks/" + child["id"] + "/inputs/read", json={"reference": reference}).status_code == 404
    assert viewer.post(path, json={"reference": reference, "length": 16001}).status_code == 422
    after = env["client"].get(env["target"] + "/overview").json()["resources"]
    assert after["tools"] == before["tools"] + 1  # Only the explicit delegation counts.


def test_exact_revision_retry_is_actor_scoped_and_never_reapplies_old_input(installation):
    from concurrent.futures import ThreadPoolExecutor

    from test_public_server import invited

    from reasoning_ledger.server.work_contracts import WorkRevision

    env = coordinator(installation)
    user, org, project, program = env["owner"]["user_id"], env["owner"]["organization_id"], env["project"]["id"], env["root"]["id"]
    body = {"expected_revision": 1, "reason": "Narrow the evidence coverage", "specification":
            env["root"]["version"]["specification"] | {"objective": "Compare the retained explanations"}}
    work = env["client"].app.state.work
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: work.revise(user, org, project, program, WorkRevision(**body)), range(4)))
    assert all(row["head_revision"] == 2 for row in responses)
    with env["store"].transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM work_specifications WHERE program_id=?", (program,)).fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM work_events WHERE program_id=? AND event_json LIKE '%revised%'",
                          (program,)).fetchone()[0] == 1
    _, other, _ = invited(installation)
    assert other.patch(env["target"], json=body).status_code == 409
    assert env["client"].patch(env["target"], json=body | {"reason": "Different reason"}).status_code == 409
    changed = body | {"expected_revision": 2, "specification": body["specification"] | {"objective": "A later correction"}}
    assert env["client"].patch(env["target"], json=changed).json()["head_revision"] == 3
    replay = env["client"].patch(env["target"], json=body)
    assert replay.status_code == 200, replay.text
    assert replay.json()["head_revision"] == 3
    assert replay.json()["version"]["specification"]["objective"] == "A later correction"
