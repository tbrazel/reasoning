"""Project-only compute, explicit queue policies and populated-schema recovery."""

import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from postgres_fixture import postgres_configuration as postgres_configuration
from test_public_compute import IMAGE, finish, lease_body, submission, worker
from test_public_compute import compute as compute_fixture
from test_public_server import installation as base_installation
from test_public_server import invited, structure
from test_public_work_jobs import installation as installation

from reasoning_ledger.deployment.scaling import validate_snapshot
from reasoning_ledger.server.compute_catalog import DEFAULT_RESOURCES, ComputeImage, canonical
from reasoning_ledger.server.compute_store import ComputeStore
from reasoning_ledger.server.migrations import MIGRATIONS
from reasoning_ledger.server.schema_operations import PROJECT_COMPUTE
from reasoning_ledger.server.security import digest
from reasoning_ledger.server.store import Store


@pytest.fixture
def compute(installation, tmp_path):
    return compute_fixture.__wrapped__(installation, tmp_path)


def project(compute):
    _, _, _, client, owner, _ = compute
    response = client.post(f"/api/organizations/{owner['organization_id']}/projects", json={"name": "A project without topics"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def submit(compute, project_id, name="project-job", client=None, **changes):
    _, _, _, original, owner, _ = compute
    return (client or original).post(
        f"/api/organizations/{owner['organization_id']}/projects/{project_id}/compute-jobs", json=submission(name, **changes),
    )


def test_project_job_replays_without_creating_a_topic_and_delivers_existing_worker_protocol(compute):
    _, store, _, client, owner, _ = compute
    target = project(compute)
    with store.transaction() as db:
        topics_before = db.execute("SELECT COUNT(*) FROM topics").fetchone()[0]
    response = submit(compute, target)
    assert response.status_code == 202, response.text
    job = response.json()
    assert job["topic_id"] is None and job["project_id"] == target
    assert submit(compute, target).json()["id"] == job["id"]
    assert submit(compute, target, program="print('different')").status_code == 409
    other = project(compute)
    assert submit(compute, other).status_code == 409
    _, agent = worker(compute)
    row = agent.post("/api/worker/claim", json={}).json()
    assert row["id"] == job["id"] and row["project_id"] == target and row["topic_id"] is None
    assert agent.post(f"/api/worker/jobs/{job['id']}/logs", json={
        **lease_body(row), "chunk_index": 0, "text": "diagnostic\n",
    }).status_code == 204
    completed = finish(agent, row)
    assert completed.status_code == 200 and completed.json()["status"] == "succeeded", completed.text
    path = f"/api/organizations/{owner['organization_id']}/compute-jobs/{job['id']}"
    assert client.get(path).json()["used_cpu_seconds"] == 7
    assert client.get(path + "/events").json()[-1]["event"]["status"] == "succeeded"
    assert client.get(path + "/artifacts").json() == []
    with store.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM topics").fetchone()[0] == topics_before


def test_project_jobs_page_exactly_once_without_other_project_or_topic_jobs(compute):
    _, _, _, client, owner, topics = compute
    target, other = project(compute), project(compute)
    ids = [submit(compute, target, f"page-{index}").json()["id"] for index in range(5)]
    assert submit(compute, other, "different-project").status_code == 202
    root = f"/api/organizations/{owner['organization_id']}"
    assert client.post(root + "/topics/" + topics[2]["id"] + "/compute-jobs", json=submission("topic-job")).status_code == 202
    path, before, seen = root + "/projects/" + target + "/compute-jobs", 2**63 - 1, []
    while rows := client.get(path, params={"limit": 2, "before": before}).json():
        assert all(row["project_id"] == target and row["topic_id"] is None for row in rows)
        seen += [row["id"] for row in rows]
        before = rows[-1]["sequence"]
    assert seen == ids[::-1]
    assert client.get(path, params={"limit": 51}).status_code == 422


def test_queue_fanout_is_not_execution_concurrency_or_an_implicit_request_cap(compute):
    _, store, _, client, owner, _ = compute
    root = f"/api/organizations/{owner['organization_id']}"
    configured = client.put(root + "/compute-policy", json={
        "allowed_images": [IMAGE["id"]], "monthly_cpu_seconds": 10000, "max_active_jobs": 1,
    })
    assert configured.status_code == 200, configured.text
    target = project(compute)
    queued = [submit(compute, target, f"fanout-{index}") for index in range(25)]
    assert all(reply.status_code == 202 for reply in queued), [reply.text for reply in queued if reply.status_code != 202]
    snapshot = compute[2].scaling_snapshot(owner["user_id"], owner["organization_id"])
    assert snapshot["format"] == 2 and len(snapshot["jobs"]) == 1 and snapshot["queued_remaining"] == 24
    validate_snapshot(snapshot, owner["organization_id"], int(time.time()))
    _, first = worker(compute)
    _, second = worker(compute)
    row = first.post("/api/worker/claim", json={}).json()
    assert row["id"] == queued[0].json()["id"]
    assert second.post("/api/worker/claim", json={}).json() is None
    assert finish(first, row).status_code == 200
    assert second.post("/api/worker/claim", json={}).json()["id"] == queued[1].json()["id"]
    active_snapshot = compute[2].scaling_snapshot(owner["user_id"], owner["organization_id"])
    assert len([row for row in active_snapshot["jobs"] if row["status"] != "queued"]) == 1
    assert len(active_snapshot["workers"]) == 2
    validate_snapshot(active_snapshot, owner["organization_id"], int(time.time()))
    with store.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM compute_jobs WHERE status='queued'").fetchone()[0] == 23
    limited = client.put(root + "/compute-policy", json={
        "allowed_images": [IMAGE["id"]], "monthly_cpu_seconds": 10000, "max_active_jobs": 4096, "max_pending_jobs": 24,
    })
    assert limited.status_code == 200, limited.text
    assert submit(compute, target, "explicit-queue-limit").status_code == 429
    assert client.put(root + "/compute-policy", json={
        "allowed_images": [IMAGE["id"]], "monthly_cpu_seconds": 10000, "max_active_jobs": 4097,
    }).status_code == 422


def test_enrollment_and_snapshot_are_not_capped_at_sixty_four_workers(compute):
    _, _, state, _, owner, _ = compute
    for index in range(75):
        state.register_worker(owner["user_id"], owner["organization_id"], f"worker-{index}", [IMAGE["id"]], DEFAULT_RESOURCES)
    snapshot = state.scaling_snapshot(owner["user_id"], owner["organization_id"])
    assert snapshot["format"] == 2 and snapshot["queued_remaining"] == 0
    assert len(snapshot["workers"]) == 75
    validate_snapshot(snapshot, owner["organization_id"], int(time.time()))


def test_concurrent_project_submission_reserves_once_and_quota_remains_binding(compute):
    _, store, state, _, owner, _ = compute
    target = project(compute)
    args = owner["user_id"], owner["organization_id"], target, "one-submission"
    params = state.parameters(IMAGE["id"], "python", "print('protocol test')", DEFAULT_RESOURCES)
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(lambda _: state.enqueue_project(*args, params), range(8)))
    assert len({row["id"] for row in rows}) == 1
    with store.transaction() as db:
        assert state._balance(db, owner["organization_id"])["held_cpu_seconds"] == 120
    state.set_policy(owner["user_id"], owner["organization_id"], [IMAGE["id"]], 120, 1)
    assert submit(compute, target, "no-quota-left").status_code == 409


def test_private_project_observation_and_submission_deny_outsiders_and_viewers(compute):
    config, store, _, client, owner, _ = compute
    _, outsider, _ = invited((config, store, client, owner), role="viewer")
    private = client.post(f"/api/organizations/{owner['organization_id']}/projects", json={
        "name": "Private compute", "visibility": "private", "member_ids": [owner["user_id"]], "owner_id": owner["user_id"],
    })
    assert private.status_code == 201, private.text
    target = private.json()["id"]
    row = submit(compute, target).json()
    assert submit(compute, target, "outsider", client=outsider).status_code == 404
    assert submit(compute, project(compute), "viewer", client=outsider).status_code == 403
    path = f"/api/organizations/{owner['organization_id']}/compute-jobs/{row['id']}"
    for suffix in ["", "/events", "/artifacts"]:
        assert outsider.get(path + suffix).status_code == 404
    assert outsider.post(path + "/cancel", json={}).status_code == 404
    org = client.post("/api/organizations", json={"name": "Another organization", "credential_mode": "none"}).json()["id"]
    assert client.get(path.replace(owner["organization_id"], org)).status_code == 404


def test_project_grant_is_rechecked_before_execution(compute):
    config, store, _, client, owner, _ = compute
    _, member_client, member = invited((config, store, client, owner))
    root = f"/api/organizations/{owner['organization_id']}"
    response = client.post(root + "/projects", json={
        "name": "Revoke computation", "visibility": "private", "owner_id": owner["user_id"],
        "member_ids": [owner["user_id"], member["user_id"]],
    })
    assert response.status_code == 201, response.text
    target = response.json()["id"]
    queued = submit(compute, target, client=member_client)
    assert queued.status_code == 202, queued.text
    assert client.delete(root + "/projects/" + target + "/members/" + member["user_id"]).status_code == 204
    _, agent = worker(compute)
    assert agent.post("/api/worker/claim", json={}).json() is None
    row = client.get(root + "/compute-jobs/" + queued.json()["id"]).json()
    assert row["status"] == "failed" and row["used_cpu_seconds"] == 0


def test_project_only_job_and_authorization_survive_portable_encrypted_recovery(compute, tmp_path):
    from reasoning_ledger.server.backup import backup, create_key, restore

    _, store, _, _, owner, _ = compute
    target = project(compute)
    row = submit(compute, target).json()
    key, archive, destination = tmp_path / "backup.key", tmp_path / "project.rnbk", tmp_path / "recovered"
    create_key(key)
    saved = backup(store.data_dir, archive, key)
    restore(archive, key, saved["sha256"], destination, source_stopped=True)
    recovered = Store(destination)
    state = ComputeStore(recovered, {IMAGE["id"]: ComputeImage.parse(IMAGE)})
    retained = state.job(owner["user_id"], owner["organization_id"], row["id"])
    assert retained["project_id"] == target and retained["topic_id"] is None
    assert retained["status"] == "outcome_unknown" and retained["client_id"] == row["client_id"]
    assert retained["cancel_requested"]
    assert state.project_jobs(owner["user_id"], owner["organization_id"], target)[0]["id"] == row["id"]


@pytest.fixture(params=["sqlite", "postgresql"])
def old_compute_installation(request, tmp_path, monkeypatch):
    import reasoning_ledger.server.store as store_module

    if request.param == "postgresql":
        request.getfixturevalue("postgres_configuration")
    before_project_compute = next(i for i, batch in enumerate(MIGRATIONS) if PROJECT_COMPUTE in batch)
    with monkeypatch.context() as context:
        context.setattr(store_module, "MIGRATIONS", MIGRATIONS[:before_project_compute])
        installation = base_installation.__wrapped__(tmp_path)
    _, store, client, owner = installation
    project, _, topics = structure(client, owner["organization_id"])
    params = ComputeStore.parameters(IMAGE["id"], "python", "print('retained')", DEFAULT_RESOURCES)
    with store.transaction(write=True) as db:
        db.execute(
            "INSERT INTO compute_jobs(id,organization_id,topic_id,requested_by,client_id,parameters_json,image_json,"
            "program_digest,status,created_at,reserved_cpu_seconds) VALUES(?,?,?,?,?,?,?,?,'queued',1,120)",
            ("8" * 32, owner["organization_id"], topics[2]["id"], owner["user_id"], "retained-job", canonical(params),
             canonical(IMAGE), "sha256:" + digest(params["program"])),
        )
        db.execute("INSERT INTO compute_events(organization_id,job_id,event_json,created_at) VALUES(?,?,?,1)",
                   (owner["organization_id"], "8" * 32, json.dumps({"type": "state", "status": "queued"})))
        db.execute("INSERT INTO compute_job_sequences(job_id) VALUES(?)", ("8" * 32,))
    return installation, project["id"]


def test_populated_compute_migration_rolls_back_and_retries_without_erasing_job_or_events(old_compute_installation, monkeypatch):
    import reasoning_ledger.server.store as store_module

    installation, project_id = old_compute_installation
    _, store, _, owner = installation
    project_migration = next(i for i, batch in enumerate(MIGRATIONS) if PROJECT_COMPUTE in batch)
    broken = (*MIGRATIONS[:project_migration],
              (*MIGRATIONS[project_migration], "INSERT INTO nonexistent_compute_migration VALUES(1)"),
              *MIGRATIONS[project_migration + 1:])
    with monkeypatch.context() as context:
        context.setattr(store_module, "MIGRATIONS", broken)
        with pytest.raises(Exception, match="nonexistent_compute_migration"):
            Store(store.data_dir)
    with store.transaction() as db:
        version = db.schema_version() if store._postgres else db.execute("PRAGMA user_version").fetchone()[0]
        assert version == next(i for i, batch in enumerate(MIGRATIONS) if PROJECT_COMPUTE in batch)
    recovered = Store(store.data_dir)
    state = ComputeStore(recovered, {IMAGE["id"]: ComputeImage.parse(IMAGE)})
    row = state.job(owner["user_id"], owner["organization_id"], "8" * 32)
    assert row["status"] == "queued" and row["project_id"] == project_id
    assert state.events(owner["user_id"], owner["organization_id"], "8" * 32)[0]["event"]["status"] == "queued"
    with recovered.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM compute_job_sequences").fetchone()[0] == 1
