"""Durable retirement/HTTP/cleanup fixtures, not physical host or cloud deletion proof."""

import asyncio
import json
import os
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from public_schema_fixture import remove_collaboration_schema
from test_public_compute_ui import ORIGIN, headers
from test_public_compute_ui import fixture as _http_fixture
from test_public_compute_worker import IMAGE, worker
from test_public_compute_worker import fixture as fixture

from reasoning_ledger.server import compute_worker
from reasoning_ledger.server.compute_catalog import DEFAULT_RESOURCES, ComputeImage
from reasoning_ledger.server.compute_docker import LABEL, CleanupUncertain, DockerRunner, RuntimeFault
from reasoning_ledger.server.compute_store import ComputeStore
from reasoning_ledger.server.compute_worker import ComputeWorker, WorkerFault, drain_status, retirement_receipt, write_state
from reasoning_ledger.server.migrations import MIGRATIONS
from reasoning_ledger.server.store import Store, StoreError

http_fixture = _http_fixture


def test_self_drain_is_idempotent_does_not_interrupt_or_renew_active_attempt_and_status_is_readonly(fixture):
    job = fixture.enqueue()
    token = fixture.registration["token"]
    claimed = fixture.state.claim(token)
    with fixture.state.store.transaction() as db:
        before = dict(db.execute("SELECT * FROM compute_jobs WHERE id=?", (job["id"],)).fetchone())
        seen = db.execute("SELECT last_seen_at FROM compute_workers").fetchone()[0]
    assert fixture.state.drain_self(token)["draining"] == 1
    fixture.state.drain_self(token)
    status = fixture.state.worker_status(token)
    assert status["unsettled_jobs"] == [{"id": job["id"], "status": "running"}] and status["quiescent"] is False
    assert fixture.state.claim(token) is None
    assert fixture.state.heartbeat(token, job["id"], claimed["attempt_id"], claimed["lease"])["stop"] is False
    with fixture.state.store.transaction() as db:
        after = dict(db.execute("SELECT * FROM compute_jobs WHERE id=?", (job["id"],)).fetchone())
        assert after["attempt_id"] == before["attempt_id"] and after["cancel_requested"] == 0 and after["status"] == "running"
        assert db.execute("SELECT COUNT(*) FROM audit_events WHERE action='compute.worker-self-drain'").fetchone()[0] == 1
    with fixture.state.store.transaction(write=True) as db:
        db.execute("UPDATE compute_jobs SET lease_expires_at=0 WHERE id=?", (job["id"],))
        db.execute("UPDATE compute_workers SET last_seen_at=?", (seen,))
    assert fixture.state.worker_status(token)["unsettled_jobs"][0]["status"] == "running"
    with fixture.state.store.transaction() as db:
        assert db.execute("SELECT lease_expires_at FROM compute_jobs").fetchone()[0] == 0
        assert db.execute("SELECT last_seen_at FROM compute_workers").fetchone()[0] == seen


@pytest.mark.parametrize("cleanup", [False, None, 1, "yes"])
def test_retirement_refuses_unconfirmed_cleanup_without_revoking_enrollment(fixture, cleanup):
    token = fixture.registration["token"]
    fixture.state.drain_self(token)
    with pytest.raises(StoreError):
        fixture.state.retire_worker(token, cleanup)
    assert fixture.state.worker_retirement(token)["retired"] is False
    assert fixture.state.worker_identity(token)["revoked_at"] is None


@pytest.mark.parametrize("blocked", ["not_drained", "running", "expired", "unknown"])
def test_retirement_refuses_any_unsettled_attempt_even_if_its_lease_expired(fixture, blocked):
    token = fixture.registration["token"]
    if blocked != "not_drained":
        job = fixture.enqueue()
        fixture.state.claim(token)
        fixture.state.drain_self(token)
        with fixture.state.store.transaction(write=True) as db:
            if blocked == "expired":
                db.execute("UPDATE compute_jobs SET lease_expires_at=0 WHERE id=?", (job["id"],))
            elif blocked == "unknown":
                db.execute("UPDATE compute_jobs SET status='outcome_unknown' WHERE id=?", (job["id"],))
    with pytest.raises(StoreError) as error:
        fixture.state.retire_worker(token, True)
    assert error.value.status == 409 and fixture.state.worker_retirement(token)["retired"] is False


def test_retirement_permanently_fences_claims_and_delivery_but_receipt_replays_after_restart(fixture):
    token = fixture.registration["token"]
    queued = fixture.enqueue()
    fixture.state.drain_self(token)
    with ThreadPoolExecutor(max_workers=4) as pool:
        receipts = list(pool.map(lambda _: fixture.state.retire_worker(token, True), range(8)))
    assert all(value == receipts[0] for value in receipts) and receipts[0]["retired"] is True
    assert len(receipts[0]["retirement_id"]) == 32 and token not in json.dumps(receipts)
    reopened = ComputeStore(Store(fixture.state.store.data_dir), {"fixture": ComputeImage.parse(IMAGE)})
    assert reopened.worker_retirement(token) == reopened.retire_worker(token, True) == receipts[0]
    for operation in (lambda: reopened.worker_identity(token), lambda: reopened.worker_status(token), lambda: reopened.claim(token)):
        with pytest.raises(StoreError) as error:
            operation()
        assert error.value.status == 401
    with pytest.raises(StoreError) as error:
        reopened.change_worker(fixture.user, fixture.org, fixture.config["id"], draining=False)
    assert error.value.status == 409
    assert reopened.job(fixture.user, fixture.org, queued["id"])["status"] == "queued"
    with reopened.store.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM audit_events WHERE action='compute.worker-retire'").fetchone()[0] == 1


def test_administratively_revoked_token_cannot_obtain_or_forge_retirement_receipt(fixture):
    token = fixture.registration["token"]
    fixture.state.change_worker(fixture.user, fixture.org, fixture.config["id"], revoke=True)
    for operation in (lambda: fixture.state.worker_retirement(token), lambda: fixture.state.retire_worker(token, True)):
        with pytest.raises(StoreError) as error:
            operation()
        assert error.value.status == 401


def test_schema_six_upgrade_preserves_random_lease_worker_and_jobs_without_retiring_them(fixture):
    job = fixture.enqueue()
    token = fixture.registration["token"]
    claimed = fixture.state.claim(token)
    with fixture.state.store.transaction(write=True) as db:
        remove_collaboration_schema(db)
        for table in (
            "knowledge_writes",
            "knowledge_versions",
            "knowledge_entries",
            "user_presence",
            "bot_webhook_requests",
            "bot_webhook_deliveries",
            "bot_webhooks",
            "workflow_run_actions",
            "workflow_run_events",
            "workflow_node_attempts",
            "workflow_runs",
            "workflow_writes",
            "workflow_versions",
            "workflows",
        ):
            db.execute("DROP TABLE " + table)
        db.execute("DROP INDEX compute_worker_retirement")
        db.execute("DROP TABLE invitation_mail")
        db.execute("DROP INDEX invitation_org_identity")
        for table in ("host_setup_receipt", "compute_scaler_requests", "compute_enrollments", "compute_scalers"):
            db.execute(f"DROP TABLE {table}")
        db.execute("ALTER TABLE compute_workers DROP COLUMN retirement_id")
        db.execute("ALTER TABLE compute_workers DROP COLUMN retired_at")
        db.execute("PRAGMA user_version=6")
        db.execute("DROP TABLE model_impact_profiles")
        db.execute("ALTER TABLE llm_runs DROP COLUMN impact_profile_json")
        db.execute("ALTER TABLE topics DROP COLUMN default_model")
        db.execute("ALTER TABLE topics DROP COLUMN knowledge_policy_json")
        db.execute("DROP TABLE project_memberships")
        db.execute("ALTER TABLE projects DROP COLUMN visibility")
        db.execute("DROP INDEX message_org_identity")
    reopened = ComputeStore(Store(fixture.state.store.data_dir), {"fixture": ComputeImage.parse(IMAGE)})
    assert reopened.worker_identity(token)["retired_at"] is None and reopened.worker_retirement(token)["retired"] is False
    assert reopened.finish(token, job["id"], claimed["attempt_id"], claimed["lease"], "succeeded", 1000, 0, None)["status"] == "succeeded"
    with reopened.store.transaction() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)


def test_retiring_idle_worker_never_claims_or_executes_queued_work_and_retired_run_fails_locally(fixture, tmp_path):
    queued = fixture.enqueue()
    agent, runner, wire = worker(tmp_path, fixture)
    receipt = agent.retire()
    assert receipt["retired"] and receipt["container_cleanup_verified"] and receipt["local_state_quiescent"]
    assert receipt["cloud_resources_deleted"] is False and runner.calls == 0
    assert fixture.state.job(fixture.user, fixture.org, queued["id"])["status"] == "queued"
    assert not any(path.endswith("/claim") for _, path, _ in wire.calls)
    marker = agent.data_dir / "retirement.json"
    assert marker.stat().st_mode & 0o777 == 0o600 and fixture.registration["token"] not in marker.read_text()
    assert agent.retire() == receipt
    count, cleaned = len(wire.calls), runner.cleanups
    with pytest.raises(WorkerFault, match="worker_retired"):
        agent.run(once=True)
    assert len(wire.calls) == count and runner.cleanups == cleaned


@pytest.mark.parametrize("phase", ["claimed", "executing"])
def test_retirement_recovers_crashed_attempt_as_failure_without_program_execution(fixture, tmp_path, phase):
    job = fixture.enqueue()
    claimed = fixture.state.claim(fixture.registration["token"])
    agent, runner, wire = worker(tmp_path, fixture)
    write_state(agent.journal, {"phase": phase, "job": claimed})
    assert agent.retire()["retired"] is True
    result = fixture.state.job(fixture.user, fixture.org, job["id"])
    assert result["status"] == "failed" and result["error_code"] == "worker_stopped" and runner.calls == 0
    assert result["used_cpu_seconds"] == (0 if phase == "claimed" else 120)
    assert not any(path.endswith("/claim") for _, path, _ in wire.calls)


@pytest.mark.parametrize("lost", ["/logs", "/chunks", "/finish"])
def test_retirement_delivers_retained_output_and_settlement_without_running_again(fixture, tmp_path, lost):
    job = fixture.enqueue()
    agent, runner, wire = worker(tmp_path, fixture)
    wire.lose = lost
    with pytest.raises(WorkerFault):
        agent.run(once=True)
    count = len(wire.calls)
    assert agent.retire()["retired"] is True and runner.calls == 1
    assert fixture.state.job(fixture.user, fixture.org, job["id"])["status"] == "succeeded"
    assert len(fixture.state.artifacts(fixture.user, fixture.org, job["id"])) == 1
    assert not any(path.endswith("/claim") for _, path, _ in wire.calls[count:])


def test_lost_retirement_receipt_is_recovered_with_revoked_token_and_fresh_cleanup(fixture, tmp_path):
    agent, runner, wire = worker(tmp_path, fixture)
    wire.lose = "/retirement"
    with pytest.raises(WorkerFault, match="server_unavailable"):
        agent.retire()
    assert not (agent.data_dir / "retirement.json").exists()
    assert fixture.state.worker_retirement(fixture.registration["token"])["retired"] is True
    cleaned = runner.cleanups
    restarted = ComputeWorker(agent.data_dir, fixture.config, agent.client, runner)
    assert restarted.retire()["retired"] is True and runner.cleanups > cleaned
    assert len([path for method, path, _ in wire.calls if method == "POST" and path.endswith("/retirement")]) == 1


def test_live_worker_lock_prevents_retirement_requests_or_cleanup(fixture, tmp_path):
    agent, runner, wire = worker(tmp_path, fixture)
    with agent.ownership(), pytest.raises(WorkerFault, match="another_worker_owns_state"):
        agent.retire()
    assert not wire.calls and runner.cleanups == 0


@pytest.mark.parametrize("invalid", ["fifo", "hardlink", "nonempty", "exposed", "symlink"])
def test_invalid_worker_lock_refuses_retirement_before_network_or_cleanup(fixture, tmp_path, invalid):
    agent, runner, wire = worker(tmp_path, fixture)
    lock = agent.data_dir / "worker.lock"
    if invalid == "fifo":
        os.mkfifo(lock, 0o600)
    elif invalid == "symlink":
        lock.symlink_to(agent.data_dir / "missing-lock")
    else:
        lock.write_bytes(b"unexpected" if invalid == "nonempty" else b"")
        lock.chmod(0o644 if invalid == "exposed" else 0o600)
        if invalid == "hardlink":
            os.link(lock, agent.data_dir / "second-lock")
    with pytest.raises(WorkerFault) as error:
        agent.retire()
    assert str(error.value) == "invalid_private_state"
    assert not wire.calls and runner.cleanups == 0 and runner.calls == 0


@pytest.mark.parametrize("failure", ["cleanup", "verification", "delivery", "undrain"])
def test_failed_physical_cleanup_delivery_or_authority_change_never_returns_retirement_success(fixture, tmp_path, failure):
    agent, runner, wire = worker(tmp_path, fixture)
    if failure == "cleanup":
        runner.uncertain = True
    elif failure == "verification":

        def uncertain():
            raise CleanupUncertain("cleanup_uncertain")

        runner.verify_cleanup = uncertain
    elif failure == "delivery":
        fixture.enqueue()
        wire.lose = "/logs"
        with pytest.raises(WorkerFault):
            agent.run(once=True)
        wire.lose = "/logs"
    else:
        handler = wire.handle

        def undrain(request):
            if request.method == "POST" and request.url.path.endswith("/retirement"):
                fixture.state.change_worker(fixture.user, fixture.org, fixture.config["id"], draining=False)
            return handler(request)

        agent.client.client.close()
        agent.client = compute_worker.WorkerClient(fixture.config, transport=httpx.MockTransport(undrain))
    with pytest.raises((WorkerFault, CleanupUncertain)):
        agent.retire()
    assert not (agent.data_dir / "retirement.json").exists()
    assert fixture.state.worker_retirement(fixture.registration["token"])["retired"] is False


def test_lost_unobserved_claim_blocks_retirement_until_the_original_attempt_is_reconciled(fixture, tmp_path):
    job = fixture.enqueue()
    agent, runner, wire = worker(tmp_path, fixture)
    wire.lose = "/claim"
    with pytest.raises(WorkerFault):
        agent.run(once=True)
    assert agent.state()["phase"] == "claiming"
    with pytest.raises(WorkerFault, match="attempt_rejected"):
        agent.retire()
    assert runner.calls == 0
    with fixture.state.store.transaction(write=True) as db:
        db.execute("UPDATE compute_jobs SET lease_expires_at=0 WHERE id=?", (job["id"],))
    fixture.state.job(fixture.user, fixture.org, job["id"])
    fixture.state.reconcile(fixture.user, fixture.org, job["id"], 0, True)
    assert agent.retire()["retired"] is True and runner.calls == 0


def test_orphaned_attempt_output_is_preserved_and_prevents_retirement(fixture, tmp_path):
    agent, runner, _ = worker(tmp_path, fixture)
    orphan = agent.attempts / ("c" * 32)
    orphan.mkdir(mode=0o700)
    with pytest.raises(WorkerFault, match="retirement_retained_output"):
        agent.retire()
    assert orphan.exists() and runner.calls == 0
    assert fixture.state.worker_retirement(fixture.registration["token"])["retired"] is False


@pytest.mark.parametrize("remaining", [b"", b"a" * 64 + b"\n", b"invalid response"])
def test_docker_retirement_verification_is_readonly_and_requires_no_remaining_labeled_containers(tmp_path, monkeypatch, remaining):
    runner = DockerRunner(tmp_path, "a" * 32)
    calls = []
    monkeypatch.setattr(runner, "verify_runtime_identity", lambda: None)
    monkeypatch.setattr(runner, "_command", lambda args, **kwargs: calls.append(args) or remaining)
    if remaining:
        with pytest.raises(CleanupUncertain):
            runner.verify_cleanup()
    else:
        runner.verify_cleanup()
    assert calls == [["container", "ls", "--all", "--quiet", "--no-trunc", "--filter", f"label={LABEL}=" + "a" * 32]]


@pytest.mark.parametrize("change", ["none", "daemon", "socket", "missing", "public", "malformed"])
def test_runtime_binding_is_private_and_refuses_changed_or_unbound_cleanup_target(tmp_path, monkeypatch, change):
    runner = DockerRunner(tmp_path, "a" * 32)
    info = {"ID": "fixture-daemon-identity"}
    monkeypatch.setattr(runner, "_command", lambda args, **kwargs: json.dumps(info).encode())
    runner.verify_runtime_identity()
    path = tmp_path / "docker-runtime.json"
    original = path.read_bytes()
    assert path.stat().st_mode & 0o777 == 0o600 and json.loads(original)["socket"] == str(runner.socket_path)
    if change == "none":
        runner.verify_runtime_identity()
        assert path.read_bytes() == original
        return
    if change == "daemon":
        info["ID"] = "another-daemon"
    elif change == "socket":
        runner.socket_path = tmp_path / "different.sock"
    elif change == "missing":
        path.rename(tmp_path / "saved-binding.json")
        write_state(tmp_path / "job.json", {"phase": "idle"})
    elif change == "public":
        path.chmod(0o644)
    else:
        info["ID"] = True
    with pytest.raises(RuntimeFault):
        runner.verify_runtime_identity()
    if change != "missing":
        assert path.read_bytes() == original
    else:
        assert not path.exists()


def test_runtime_identity_failure_precedes_retirement_requests_and_cleanup(fixture, tmp_path):
    agent, runner, wire = worker(tmp_path, fixture)

    def mismatch():
        raise RuntimeFault("runtime_identity_changed")

    runner.verify_runtime_identity = mismatch
    with pytest.raises(RuntimeFault):
        agent.retire()
    assert not wire.calls and runner.cleanups == 0


def test_daemon_change_during_cleanup_is_uncertain_before_any_container_removal(tmp_path, monkeypatch):
    runner = DockerRunner(tmp_path, "a" * 32)

    def changed():
        raise RuntimeFault("runtime_identity_changed")

    monkeypatch.setattr(runner, "verify_runtime_identity", changed)
    monkeypatch.setattr(runner, "_command", lambda *args, **kwargs: pytest.fail("changed daemon must not be queried or mutated"))
    with pytest.raises(CleanupUncertain):
        runner.remove("reasoning-" + "a" * 32 + "-" + "b" * 32)


def test_worker_drain_cli_needs_no_docker_and_retire_cli_recovers_and_prints_only_fixed_receipt(fixture, tmp_path, monkeypatch, capsys):
    agent, runner, wire = worker(tmp_path, fixture)
    write_state(agent.data_dir / "enrollment.json", fixture.config)
    client_type = compute_worker.WorkerClient
    monkeypatch.setattr(compute_worker, "WorkerClient", lambda config: client_type(config, transport=httpx.MockTransport(wire.handle)))
    monkeypatch.setattr(compute_worker, "DockerRunner", lambda *args: pytest.fail("draining must not initialize Docker"))
    arguments = ["--data-dir", str(agent.data_dir), "--confirm-worker", fixture.config["id"]]
    assert compute_worker.main([*arguments[:2], "drain", *arguments[2:]]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["draining"] is True and output["quiescent"] is True and output["physical_cleanup_verified"] is False
    monkeypatch.setattr(compute_worker, "DockerRunner", lambda *args: runner)
    assert compute_worker.main([*arguments[:2], "retire", *arguments[2:]]) == 0
    output = capsys.readouterr().out
    assert json.loads(output)["retired"] is True and fixture.registration["token"] not in output and runner.calls == 0


@pytest.mark.parametrize("field,value", [("worker_id", "c" * 32), ("organization_id", "c" * 32), ("retired", 1), ("retired_at", True)])
def test_receipt_parser_refuses_wrong_scope_and_coerced_types(fixture, field, value):
    receipt = {
        "worker_id": fixture.config["id"],
        "organization_id": fixture.org,
        "retired": True,
        "retirement_id": "d" * 32,
        "retired_at": 1,
    }
    with pytest.raises(WorkerFault, match="invalid_retirement_receipt"):
        retirement_receipt(fixture.config, {**receipt, field: value})


def test_drain_output_is_bounded_typed_and_never_prints_untrusted_response_fields(fixture):
    token = fixture.registration["token"]
    fixture.state.drain_self(token)
    raw = {**fixture.state.worker_status(token), "token": token, "name": token}
    output = drain_status(fixture.config, raw)
    assert token not in json.dumps(output) and output["physical_cleanup_verified"] is False
    with pytest.raises(WorkerFault):
        drain_status(fixture.config, {**raw, "quiescent": 1})


@pytest.mark.parametrize("command", ["drain", "retire"])
def test_cli_confirmation_is_checked_before_client_or_docker_creation(fixture, tmp_path, monkeypatch, capsys, command):
    data = tmp_path / "cli-worker"
    data.mkdir(mode=0o700)
    write_state(data / "enrollment.json", fixture.config)
    monkeypatch.setattr(compute_worker, "WorkerClient", lambda _: pytest.fail("client must not be created"))
    with pytest.raises(SystemExit) as error:
        compute_worker.main(["--data-dir", str(data), command, "--confirm-worker", "0" * 32])
    assert error.value.code == 2 and fixture.registration["token"] not in capsys.readouterr().err


def test_retirement_http_route_boundaries_strict_confirmation_and_revoked_receipt_only(http_fixture):
    store, app, owner, _topics, root = http_fixture
    state = app.state.compute
    image = next(iter(state.images))
    registration = state.register_worker(owner["user_id"], owner["organization_id"], "Separate", [image], DEFAULT_RESOURCES)
    credential = {"authorization": "Bearer " + registration["token"]}

    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
            assert (await client.get("/api/worker/status", headers=headers(owner))).status_code == 401
            assert (
                await client.post("/api/worker/drain", headers={**credential, "origin": "https://foreign.example"}, json={})
            ).status_code == 403
            assert (await client.post("/api/worker/drain", headers=credential, json={"draining": False})).status_code == 422
            assert (await client.post("/api/worker/drain", headers=credential, json={})).status_code == 200
            assert (await client.get("/api/worker/status", headers=credential)).json()["quiescent"] is True
            assert (await client.post("/api/worker/retirement", headers=credential, json={"cleanup_confirmed": 1})).status_code == 422
            assert (await client.post("/api/worker/retirement", headers=credential, json={"cleanup_confirmed": False})).status_code == 400
            response = await client.post("/api/worker/retirement", headers=credential, json={"cleanup_confirmed": True})
            assert response.status_code == 200 and response.json()["retired"] is True
            assert "no-store" in response.headers["cache-control"] and registration["token"] not in response.text
            assert (await client.get("/api/worker/retirement", headers=credential)).json() == response.json()
            assert (
                await client.post("/api/worker/retirement", headers=credential, json={"cleanup_confirmed": True})
            ).json() == response.json()
            for path in ("/api/worker/identity", "/api/worker/status", root + "/compute-configuration"):
                assert (await client.get(path, headers=credential)).status_code == 401
            assert (await client.post("/api/worker/claim", headers=credential, json={})).status_code == 401
            assert (await client.get("/api/worker/retirement", headers=headers(owner))).status_code == 401
            assert (
                await client.patch(root + "/compute-workers/" + registration["id"], headers=headers(owner), json={"draining": False})
            ).status_code == 409

    asyncio.run(check())
    assert state.workers(owner["user_id"], owner["organization_id"])[0]["retirement_id"] is not None
