"""Worker protocol, lost replies, fenced attempts and shared ancestor accounting."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from postgres_fixture import postgres_configuration as postgres_configuration
from test_public_server import installation as base_installation
from test_public_work_contracts import program, specification, task

from reasoning_ledger.server.store import StoreError
from reasoning_ledger.server.work_job_contracts import EffectIntent
from reasoning_ledger.server.work_jobs import WorkJobs


@pytest.fixture(params=["sqlite", "postgresql"])
def installation(request, tmp_path):
    if request.param == "postgresql":
        request.getfixturevalue("postgres_configuration")
    return base_installation.__wrapped__(tmp_path)


def environment(installation, resources=None, max_parallel=8):
    _, store, client, owner = installation
    resources = {"allowed_tools": ["fixture"], **(resources or {"max_concurrency": 8})}
    path, root, project, topics = program(installation, resources=resources)
    target = path + "/" + root["id"]
    client.post(target + "/control", json={"expected_revision": 1, "status": "active", "reason": "Start fixture work"}).raise_for_status()
    reply = client.post(
        f"/api/organizations/{owner['organization_id']}/work-workers",
        json={
            "name": "Fixture",
            "project_ids": [project["id"]],
            "roles": ["worker", "librarian", "critic"],
            "max_parallel": max_parallel,
        },
    )
    assert reply.status_code == 201, reply.text
    return {
        "client": client,
        "store": store,
        "owner": owner,
        "root": root,
        "path": path,
        "target": target,
        "project": project,
        "worker": reply.json(),
        "resources": resources,
        "topics": topics,
    }


def queued(env, name="task", role="worker", parent=None, resources=None, lane=None, depends_on=None):
    resources = {"allowed_tools": env["resources"].get("allowed_tools", []), **(resources or env["resources"])}
    spec = specification(role=role, resources=resources)
    response = task(env["client"], env["path"], env["root"], name, specification=spec, parent_id=parent, depends_on=depends_on or [])
    assert response.status_code == 201, response.text
    record = response.json()
    reply = env["client"].post(env["target"] + "/tasks/" + record["id"] + "/jobs", json={"expected_epoch": 0, "lane": lane})
    assert reply.status_code == 202, reply.text
    return record, reply.json()


def worker_post(env, path, body, credential=None):
    return env["client"].post(
        "/api/work-worker/" + path, json=body, headers={"authorization": "Bearer " + (credential or env["worker"]["token"])}
    )


def claim(env, request="claim-1"):
    response = worker_post(env, "claim", {"claim_id": request})
    assert response.status_code == 200, response.text
    return response.json()


def lease(row):
    return {key: row[key] for key in ("attempt_id", "fence", "lease")}


def effect(env, row, name="effect-1", usd=0, cpu=0):
    return worker_post(
        env,
        f"jobs/{row['id']}/effects",
        lease(row)
        | {
            "client_id": name,
            "kind": "local",
            "tool": "fixture",
            "request_sha256": "a" * 64,
            "held_microusd": usd,
            "held_cpu_seconds": cpu,
        },
    )


def result(env, row, summary="A scoped result, not an accepted proof.", outcome="result"):
    return worker_post(env, f"jobs/{row['id']}/result", lease(row) | {"summary": summary, "outcome": outcome, "outputs": []})


def expire(env, job):
    with env["store"].transaction(write=True) as db:
        db.execute("UPDATE work_jobs SET lease_expires_at=1 WHERE id=?", (job,))


def test_replay_completion_and_narrow_packet(installation):
    env = environment(installation)
    record, job = queued(env)
    first = claim(env)
    assert first == claim(env)
    assert first["dispatch"] and first["id"] == job["id"]
    assert first["packet"]["specification_sha256"] == record["sha256"]
    assert "messages" not in first["packet"] and "provider_keys" not in str(first)
    assert worker_post(env, f"jobs/{job['id']}/heartbeat", lease(first)).json()["cancel_requested"] is False
    saved = result(env, first)
    assert saved.status_code == 200, saved.text
    assert result(env, first).json() == saved.json()
    assert result(env, first, "Different completion").status_code == 409
    assert claim(env)["dispatch"] is False
    read = env["client"].get(env["target"] + "/jobs/" + job["id"]).json()
    assert read["status"] == "complete" and read["result"]["summary"] == saved.json()["result"]["summary"]
    assert env["client"].get(env["target"] + "/tasks/" + record["id"]).json()["status"] == "review"


def test_expired_unsubmitted_work_requeues_but_old_worker_is_fenced(installation):
    env = environment(installation)
    _, job = queued(env)
    old = claim(env)
    expire(env, job["id"])
    assert claim(env)["dispatch"] is False
    fresh = claim(env, "fresh-attempt")
    assert fresh["id"] == old["id"] and fresh["fence"] == old["fence"] + 1
    assert fresh["attempt_id"] != old["attempt_id"]
    assert result(env, old).status_code == 409
    assert effect(env, old).status_code == 409
    assert result(env, fresh).status_code == 200


def test_crash_after_effect_intent_retains_unknown_reservation_and_never_requeues(installation):
    env = environment(installation, {"budget_microusd": 100, "cpu_seconds": 100, "max_concurrency": 8})
    _, job = queued(env)
    row = claim(env)
    intent = effect(env, row, usd=70, cpu=5)
    assert intent.status_code == 201, intent.text
    assert intent.json()["submit"] is True
    replay = effect(env, row, usd=70, cpu=5).json()
    assert replay["id"] == intent.json()["id"] and replay["submit"] is False
    expire(env, job["id"])
    assert claim(env, "after-worker-loss") is None
    assert result(env, row).status_code == 409
    read = env["client"].get(env["target"] + "/jobs/" + job["id"]).json()
    assert read["status"] == "outcome_unknown"
    with env["store"].transaction() as db:
        usage = WorkJobs._usage(db, "program", env["root"]["id"])
    assert usage == {"microusd": 70, "cpu_seconds": 5}


def test_parent_budget_is_shared_not_copied_to_each_child(installation):
    env = environment(installation, {"budget_microusd": 200, "cpu_seconds": 200, "max_concurrency": 8})
    parent = task(
        env["client"],
        env["path"],
        env["root"],
        "parent",
        specification=specification(
            resources={"budget_microusd": 100, "cpu_seconds": 100, "max_concurrency": 8, "allowed_tools": ["fixture"]}
        ),
    ).json()
    child_limits = {"budget_microusd": 100, "cpu_seconds": 100, "max_concurrency": 8}
    queued(env, "child-a", parent=parent["id"], resources=child_limits)
    queued(env, "child-b", parent=parent["id"], resources=child_limits)
    first, second = claim(env, "first"), claim(env, "second")
    assert effect(env, first, usd=70).status_code == 201
    assert effect(env, second, usd=40).status_code == 409
    assert effect(env, second, usd=30).status_code == 201


def test_budget_reservations_are_atomic_under_sibling_races(installation):
    env = environment(installation, {"budget_microusd": 100, "cpu_seconds": 100, "max_concurrency": 8})
    queued(env, "a")
    queued(env, "b")
    rows = [claim(env, "a"), claim(env, "b")]
    jobs = WorkJobs(env["store"])

    def reserve(row):
        body = EffectIntent(**lease(row), client_id="race", kind="local", tool="fixture", request_sha256="a" * 64, held_microusd=60)
        try:
            return jobs.effect_intent(env["worker"]["token"], row["id"], body)
        except StoreError as exc:
            return exc.status

    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(reserve, rows))
    assert replies.count(409) == 1
    with env["store"].transaction() as db:
        assert WorkJobs._usage(db, "program", env["root"]["id"])["microusd"] == 60


def test_settlement_releases_only_the_unused_reservation(installation):
    env = environment(installation, {"budget_microusd": 100, "cpu_seconds": 100, "max_concurrency": 8})
    queued(env)
    row = claim(env)
    identity = effect(env, row, usd=80, cpu=80).json()["id"]
    assert result(env, row).status_code == 409
    body = lease(row) | {"state": "settled", "actual_microusd": 20, "actual_cpu_seconds": 5, "receipt": "Retained fixture receipt"}
    reply = worker_post(env, f"jobs/{row['id']}/effects/{identity}/receipt", body)
    assert reply.status_code == 200, reply.text
    assert worker_post(env, f"jobs/{row['id']}/effects/{identity}/receipt", body).json() == reply.json()
    assert effect(env, row, "second", usd=80, cpu=95).status_code == 201
    with env["store"].transaction() as db:
        assert WorkJobs._usage(db, "program", env["root"]["id"]) == {"microusd": 100, "cpu_seconds": 100}


def test_queue_length_is_not_execution_concurrency_and_full_program_does_not_starve_others(installation):
    env = environment(installation, {"max_concurrency": 1})
    for index in range(12):
        queued(env, f"queued-{index}")
    first = claim(env)
    assert claim(env, "too-many-active") is None
    created = env["client"].post(
        env["path"],
        json={"client_id": "other-program", "specification": specification(resources={"max_concurrency": 1, "allowed_tools": ["fixture"]})},
    )
    other = created.json()
    second_env = env | {"root": other, "target": env["path"] + "/" + other["id"]}
    env["client"].post(second_env["target"] + "/control", json={"expected_revision": 1, "status": "active", "reason": "Start"})
    queued(second_env, "second-program")
    second = claim(env, "another-program")
    assert second["program_id"] == other["id"] and first["program_id"] != second["program_id"]


def test_ancestor_concurrency_limits_apply_to_all_descendants(installation):
    env = environment(installation)
    parent = task(
        env["client"],
        env["path"],
        env["root"],
        "limited-parent",
        specification=specification(resources={"max_concurrency": 1, "allowed_tools": ["fixture"]}),
    ).json()
    for name in ("a", "b"):
        queued(env, name, parent=parent["id"], resources={"max_concurrency": 1})
    assert claim(env) is not None
    assert claim(env, "another-descendant") is None


def test_human_and_review_lanes_take_priority_and_pending_dependencies_are_not_dispatched(installation):
    env = environment(installation)
    prerequisite, _ = queued(env, "research")
    queued(env, "blocked", depends_on=[prerequisite["id"]])
    _, review = queued(env, "review", role="critic")
    _, human = queued(env, "human", lane="human")
    assert claim(env, "priority-1")["id"] == human["id"]
    assert claim(env, "priority-2")["id"] == review["id"]
    assert claim(env, "priority-3")["task_id"] == prerequisite["id"]
    assert claim(env, "blocked-child") is None


def test_program_cancellation_and_revocation_stop_effects(installation):
    env = environment(installation)
    _, job = queued(env)
    row = claim(env)
    response = env["client"].post(env["target"] + "/control", json={"expected_revision": 1, "status": "cancelled", "reason": "Stop work"})
    assert response.status_code == 200
    assert effect(env, row).status_code == 409
    assert result(env, row).status_code == 409
    assert result(env, row, outcome="cancelled").status_code == 200
    path = f"/api/organizations/{env['owner']['organization_id']}/work-workers/{env['worker']['id']}"
    assert env["client"].delete(path).status_code == 204
    assert worker_post(env, "claim", {"claim_id": "revoked"}).status_code == 401
    assert env["client"].get(env["target"] + "/jobs/" + job["id"]).status_code == 200


def test_worker_credentials_are_not_human_sessions_and_wrong_project_cannot_claim(installation):
    env = environment(installation)
    queued(env)
    assert worker_post(env, "claim", {"claim_id": "human-token"}, env["owner"]["access_token"]).status_code == 401
    assert worker_post(env, "claim", {"claim_id": "invalid"}, "rnaw_" + "x" * 43).status_code == 401
    with env["store"].transaction(write=True) as db:
        db.execute("DELETE FROM work_worker_projects WHERE worker_id=?", (env["worker"]["id"],))
    assert claim(env) is None


def test_reserved_slots_remain_available_after_research_fanout(installation):
    env = environment(installation, max_parallel=4)
    org = env["owner"]["organization_id"]
    reply = env["client"].put(
        f"/api/organizations/{org}/work-scheduler-policy",
        json={
            "max_parallel": 4,
            "human_slots": 1,
            "review_slots": 1,
        },
    )
    assert reply.status_code == 200
    for index in range(5):
        queued(env, f"research-{index}")
    assert claim(env, "research-a") is not None
    assert claim(env, "research-b") is not None
    assert claim(env, "reserved") is None
    _, human = queued(env, "human", lane="human")
    _, review = queued(env, "review", role="critic")
    assert claim(env, "human-slot")["id"] == human["id"]
    assert claim(env, "review-slot")["id"] == review["id"]
    assert claim(env, "full") is None


def test_branch_fairness_uses_recent_dispatch_not_all_time_counts(installation):
    env = environment(installation)
    a = task(env["client"], env["path"], env["root"], "branch-a", specification=specification(resources=env["resources"])).json()
    b = task(env["client"], env["path"], env["root"], "branch-b", specification=specification(resources=env["resources"])).json()
    for index in range(3):
        queued(env, f"a-{index}", parent=a["id"])
    for index in range(3):
        queued(env, f"b-{index}", parent=b["id"])
    names = []
    for index in range(4):
        row = claim(env, f"fair-{index}")
        with env["store"].transaction() as db:
            names.append(db.execute("SELECT parent_id FROM work_tasks WHERE id=?", (row["task_id"],)).fetchone()[0])
        assert result(env, row).status_code == 200
    assert names == [a["id"], b["id"], a["id"], b["id"]]


def test_program_fairness_does_not_drain_the_oldest_program(installation):
    env = environment(installation)
    other = (
        env["client"]
        .post(
            env["path"],
            json={
                "client_id": "second-program",
                "specification": specification(resources=env["resources"]),
            },
        )
        .json()
    )
    second = env | {"root": other, "target": env["path"] + "/" + other["id"]}
    env["client"].post(
        second["target"] + "/control", json={"expected_revision": 1, "status": "active", "reason": "Start"}
    ).raise_for_status()
    for branch in (env, second):
        for index in range(3):
            queued(branch, f"work-{index}")
    ids = []
    for index in range(4):
        row = claim(env, f"program-fair-{index}")
        ids.append(row["program_id"])
        assert result(env, row).status_code == 200
    assert ids == [env["root"]["id"], other["id"], env["root"]["id"], other["id"]]


def test_unknown_charge_requires_pinned_reconciliation_before_explicit_reopen(installation):
    env = environment(installation, {"budget_microusd": 100, "max_concurrency": 8})
    record, job = queued(env)
    old = claim(env)
    identity = effect(env, old, usd=80).json()["id"]
    reopen_path = env["target"] + "/tasks/" + record["id"] + "/reopen"
    reopen_body = {"expected_epoch": 0, "reason": "Operator verified the external outcome"}
    reconcile_path = env["target"] + "/effects/" + identity + "/reconcile"
    receipt = {
        "expected_request_sha256": "a" * 64,
        "state": "settled",
        "actual_microusd": 25,
        "actual_cpu_seconds": 0,
        "receipt": "Verified provider receipt, retained privately",
    }
    assert env["client"].post(reconcile_path, json=receipt).status_code == 409
    expire(env, job["id"])
    WorkJobs(env["store"]).expire_due()
    assert env["client"].post(reopen_path, json=reopen_body).status_code == 409
    assert env["client"].post(reconcile_path, json=receipt | {"expected_request_sha256": "b" * 64}).status_code == 409
    settled = env["client"].post(reconcile_path, json=receipt)
    assert settled.status_code == 200, settled.text
    assert env["client"].post(reconcile_path, json=receipt).json() == settled.json()
    assert env["client"].post(reconcile_path, json=receipt | {"actual_microusd": 24}).status_code == 409
    assert env["client"].post(reopen_path, json=reopen_body).json()["epoch"] == 1
    assert env["client"].post(reopen_path, json=reopen_body).status_code == 409
    new = env["client"].post(env["target"] + "/tasks/" + record["id"] + "/jobs", json={"expected_epoch": 1})
    assert new.status_code == 202 and new.json()["id"] != job["id"]
    fresh = claim(env, "explicit-second-attempt")
    assert fresh["task_epoch"] == 1 and fresh["id"] == new.json()["id"]
    assert result(env, old).status_code == 409
    assert effect(env, fresh, usd=75).status_code == 201
    with env["store"].transaction() as db:
        assert WorkJobs._usage(db, "program", env["root"]["id"])["microusd"] == 100
    page = env["client"].get(env["target"] + "/jobs/" + job["id"] + "/effects").json()
    assert page["items"][0]["state"] == "settled" and page["next"] is None
    assert "receipt" not in str(page)


def test_maintenance_expires_without_a_new_worker_claim(installation):
    env = environment(installation)
    _, job = queued(env)
    old = claim(env)
    expire(env, job["id"])
    WorkJobs(env["store"]).expire_due()
    read = env["client"].get(env["target"] + "/jobs/" + job["id"]).json()
    assert read["status"] == "queued" and read["lease_expires_at"] == 1
    assert result(env, old).status_code == 409


def test_explicit_tool_limit_is_shared_but_default_has_no_limit(installation):
    env = environment(installation, {"max_tool_calls": 1, "max_concurrency": 8})
    queued(env, "first")
    queued(env, "second")
    first, second = claim(env, "first"), claim(env, "second")
    assert effect(env, first).status_code == 201
    assert effect(env, second).status_code == 409
    assert effect(env, first).json()["submit"] is False  # Retry consumes no second call.


def test_concurrent_claims_never_duplicate_a_live_assignment(installation):
    env = environment(installation, max_parallel=8)
    for index in range(4):
        queued(env, f"parallel-{index}")
    jobs = WorkJobs(env["store"])
    with ThreadPoolExecutor(max_workers=4) as pool:
        claimed = list(pool.map(lambda i: jobs.claim(env["worker"]["token"], f"claim-{i}"), range(4)))
    assert all(row is not None for row in claimed)
    assert len({row["id"] for row in claimed}) == 4
    assert {row["fence"] for row in claimed} == {1}


def test_changed_root_authorization_cannot_poison_other_queued_work(installation):
    env = environment(installation)
    bad, job = queued(env, "obsolete")
    good = task(
        env["client"],
        env["path"],
        env["root"],
        "still-authorized",
        specification=specification(role="worker", resources={"max_concurrency": 1}),
    ).json()
    response = env["client"].patch(
        env["target"],
        json={"expected_revision": 1, "reason": "Narrow concurrency", "specification": specification(resources={"max_concurrency": 1})},
    )
    assert response.status_code == 200, response.text
    response = env["client"].post(env["target"] + "/tasks/" + good["id"] + "/jobs", json={"expected_epoch": 0})
    assert response.status_code == 202
    row = claim(env)
    assert row["task_id"] == good["id"]
    assert env["client"].get(env["target"] + "/tasks/" + bad["id"]).json()["status"] == "blocked"
    assert env["client"].get(env["target"] + "/jobs/" + job["id"]).json()["status"] == "failed"


def test_worker_project_revocation_is_rechecked_before_effect_submission(installation):
    env = environment(installation)
    queued(env)
    row = claim(env)
    with env["store"].transaction(write=True) as db:
        db.execute("DELETE FROM work_worker_projects WHERE worker_id=?", (env["worker"]["id"],))
    assert effect(env, row).status_code == 403


def test_effect_capability_is_required_and_cannot_be_mislabeled(installation):
    env = environment(installation)
    queued(env)
    row = claim(env)
    base = lease(row) | {"client_id": "invalid", "kind": "local", "request_sha256": "a" * 64}
    assert worker_post(env, f"jobs/{row['id']}/effects", base).status_code == 422
    assert worker_post(env, f"jobs/{row['id']}/effects", base | {"tool": "unapproved"}).status_code == 403
    assert worker_post(env, f"jobs/{row['id']}/effects", base | {"tool": "fixture", "model": "unapproved"}).status_code == 422
    assert effect(env, row).status_code == 201


def test_default_effect_authorization_has_no_hidden_24_tool_ceiling(installation):
    env = environment(installation)
    queued(env)
    row = claim(env)
    for index in range(30):
        identity = effect(env, row, f"call-{index}").json()["id"]
        receipt = lease(row) | {"state": "settled", "actual_microusd": 0, "actual_cpu_seconds": 0, "receipt": "Local result retained"}
        assert worker_post(env, f"jobs/{row['id']}/effects/{identity}/receipt", receipt).status_code == 200
    assert result(env, row).status_code == 200


def test_portable_recovery_fences_workers_and_retains_results_and_shared_holds(installation, tmp_path):
    from reasoning_ledger.server.backup import backup, create_key, restore
    from reasoning_ledger.server.store import Store

    env = environment(installation, {"budget_microusd": 100, "max_concurrency": 8})
    _, completed = queued(env, "completed")
    first = claim(env, "completed")
    saved = result(env, first).json()
    _, pending = queued(env, "pending")
    active = claim(env, "active")
    identity = effect(env, active, usd=70).json()["id"]
    _, waiting = queued(env, "waiting")
    key, archive, recovered_path = tmp_path / "backup-key", tmp_path / "archive.rnbk", tmp_path / "recovered"
    create_key(key)
    snapshot = backup(env["store"].data_dir, archive, key)
    restore(archive, key, snapshot["sha256"], recovered_path, source_stopped=True)
    recovered = Store(recovered_path)
    jobs = WorkJobs(recovered)
    with pytest.raises(StoreError) as denied:
        jobs.claim(env["worker"]["token"], "recovered-worker")
    assert denied.value.status == 401
    with recovered.transaction() as db:
        assert db.execute("SELECT status FROM work_jobs WHERE id=?", (completed["id"],)).fetchone()[0] == "complete"
        retained = db.execute("SELECT sha256 FROM work_results WHERE job_id=?", (completed["id"],)).fetchone()[0]
        assert retained == saved["sha256"]
        assert db.execute("SELECT status FROM work_jobs WHERE id=?", (pending["id"],)).fetchone()[0] == "outcome_unknown"
        assert db.execute("SELECT state FROM work_effects WHERE id=?", (identity,)).fetchone()[0] == "unknown"
        assert db.execute("SELECT status FROM work_jobs WHERE id=?", (waiting["id"],)).fetchone()[0] == "cancelled"
        assert db.execute("SELECT status FROM work_programs WHERE id=?", (env["root"]["id"],)).fetchone()[0] == "paused"
        assert jobs._usage(db, "program", env["root"]["id"])["microusd"] == 70
