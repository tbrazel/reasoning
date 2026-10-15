"""Atomic native-work compute, shared CPU, retained receipts and lease fencing."""

import base64
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from postgres_fixture import postgres_configuration as postgres_configuration
from test_public_compute import IMAGE, finish, job_path, lease_body, worker
from test_public_project_compute import compute as compute_fixture
from test_public_work_jobs import claim, expire, lease, result, worker_post
from test_public_work_jobs import installation as installation
from test_public_work_tools import call, coordinator, delegate

from reasoning_ledger.server.compute_catalog import DEFAULT_RESOURCES
from reasoning_ledger.server.compute_store import CHUNK_BYTES
from reasoning_ledger.server.store import StoreError
from reasoning_ledger.server.work_compute import cancel_due
from reasoning_ledger.server.work_jobs import WorkJobs
from reasoning_ledger.server.work_tool_contracts import ComputeSubmit


def setup(installation, tmp_path, **limits):
    config, store, _, client, owner, topics = compute_fixture.__wrapped__(installation, tmp_path)
    if limits.get("allowed_models"):
        from test_public_inference import KEY

        root = f"/api/organizations/{owner['organization_id']}"
        client.put(root + "/policy", json={
            "allowed_models": limits["allowed_models"], "monthly_budget_microusd": 1_000_000,
        }).raise_for_status()
        client.post(root + "/provider-keys", json={"key": KEY, "label": "Simulated provider"}).raise_for_status()
    env = coordinator((config, store, client, owner), allowed_images=[IMAGE["id"]], **limits)
    env["compute"] = (config, store, client.app.state.compute, client, owner, topics)
    return env


def submit(env, client_id="compute", row=None, **changes):
    args = {"image_id": IMAGE["id"], "language": "python", "program": "print('protocol fixture')", "resources": DEFAULT_RESOURCES}
    args.update(changes)
    return call(env, row or env["parent"], "compute_submit", client_id, **args)


def complete(env, compute_id, *, status="succeeded", exit_code=0, elapsed=6001, artifacts=None):
    registration, agent = worker(env["compute"])
    claimed = agent.post("/api/worker/claim", json={}).json()
    assert claimed and claimed["id"] == compute_id, claimed
    for name, data in (artifacts or {}).items():
        path = f"/api/worker/jobs/{compute_id}/artifacts"
        saved = agent.post(path, json={**lease_body(claimed), "name": name,
                                      "size_bytes": len(data), "digest": "sha256:" + hashlib.sha256(data).hexdigest()})
        saved.raise_for_status()
        target = path + "/" + saved.json()["id"]
        for offset in range(0, len(data), CHUNK_BYTES):
            agent.put(target + "/chunks", json={
                **lease_body(claimed), "chunk_index": offset // CHUNK_BYTES,
                "content_base64": base64.b64encode(data[offset:offset + CHUNK_BYTES]).decode(),
            }).raise_for_status()
        agent.post(target + "/commit", json=lease_body(claimed)).raise_for_status()
    body = {"attempt_id": claimed["attempt_id"], "lease": claimed["lease"], "status": status,
            "elapsed_ms": elapsed, "exit_code": exit_code}
    reply = agent.post(f"/api/worker/jobs/{compute_id}/finish", json=body)
    assert reply.status_code == 200, reply.text
    return reply.json(), registration, agent, claimed


def artifact_execution(env, files):
    identity = submit(env).json()["result"]["compute_job_id"]
    complete(env, identity, artifacts=files)
    receipt = call(env, env["parent"], "compute_read", "receipt", compute_job_id=identity).json()["result"]["reference"]
    with env["store"].transaction() as db:
        headers = json.loads(db.execute("SELECT body_json FROM work_compute_receipts WHERE compute_job_id=?",
                                       (identity,)).fetchone()[0])["artifacts"]
    return receipt, {header["name"]: header for header in headers}


def output_read(env, receipt, artifact, request="output", row=None, **options):
    return call(env, row or env["parent"], "compute_output_read", request,
                execution=receipt, artifact_id=artifact["id"], **options)


def test_exact_compute_outputs_allow_bounded_binary_and_unicode_reads(installation, tmp_path, monkeypatch):
    env = setup(installation, tmp_path)
    data = b"x" * (CHUNK_BYTES - 2) + "😊diagnostics".encode()
    receipt, files = artifact_execution(env, {"diagnostic.txt": data, "binary.dat": b"\xff\x00", "empty.txt": b""})
    first = output_read(env, receipt, files["diagnostic.txt"], length=10)
    assert first.status_code == 200, first.text
    body = first.json()["result"]
    assert body["text"] == "x" * 10 and body["next_offset"] == 10 and body["truncated"]
    assert output_read(env, receipt, files["diagnostic.txt"], length=10).json()["replayed"]
    compute = env["client"].app.state.compute
    original, touched = compute._read_chunk, []

    def observed(artifact, index):
        touched.append(index)
        return original(artifact, index)

    with monkeypatch.context() as context:
        context.setattr(compute, "_read_chunk", observed)
        crossing = output_read(env, receipt, files["diagnostic.txt"], "crossing", offset=CHUNK_BYTES - 2, length=100)
    assert touched == [0, 1]
    assert crossing.status_code == 200, crossing.text
    assert crossing.json()["result"]["text"] == "😊diagnostics"
    assert base64.b64decode(crossing.json()["result"]["base64"]) == data[CHUNK_BYTES - 2:]
    split = output_read(env, receipt, files["diagnostic.txt"], "split", offset=CHUNK_BYTES - 1, length=1).json()["result"]
    assert split["text"] is None and base64.b64decode(split["base64"]) == data[CHUNK_BYTES - 1:CHUNK_BYTES]
    binary = output_read(env, receipt, files["binary.dat"], "binary").json()["result"]
    assert binary["text"] is None and base64.b64decode(binary["base64"]) == b"\xff\x00"
    empty = output_read(env, receipt, files["empty.txt"], "empty").json()["result"]
    assert empty["base64"] == "" and empty["text"] == "" and empty["next_offset"] is None
    assert output_read(env, receipt, files["diagnostic.txt"], "past-end", offset=len(data) + 1).status_code == 400
    assert output_read(env, receipt, files["diagnostic.txt"], "oversize", length=12289).status_code == 422
    assert not binary["scope"].endswith("proof_checked")


def test_output_scope_requires_assigned_receipt_and_its_exact_manifest(installation, tmp_path):
    env = setup(installation, tmp_path)
    receipt, files = artifact_execution(env, {"diagnostic.txt": b"Retained CAS diagnostic, not a proof."})
    assert delegate(env, "unassigned").status_code == 200
    child = claim(env, "unassigned-reader")
    assert output_read(env, receipt, files["diagnostic.txt"], row=child).status_code == 404
    assert delegate(env, "assigned-reviewer", role="critic", inputs=[receipt]).status_code == 200
    reviewer = claim(env, "assigned-reader")
    allowed = output_read(env, receipt, files["diagnostic.txt"], row=reviewer)
    assert allowed.status_code == 200 and allowed.json()["result"]["text"].startswith("Retained CAS")
    assert output_read(env, receipt, {"id": "0" * 32}, "guessed", row=reviewer).status_code == 404
    assert output_read(env, receipt | {"sha256": "0" * 64}, files["diagnostic.txt"], "wrong-digest").status_code == 404
    assert output_read(env, receipt | {"kind": "research_artifact"}, files["diagnostic.txt"], "wrong-kind").status_code == 422


def test_output_chunk_tampering_fails_without_publication_or_extra_charge(installation, tmp_path):
    env = setup(installation, tmp_path)
    receipt, files = artifact_execution(env, {"diagnostic.txt": b"exact output"})
    retained = output_read(env, receipt, files["diagnostic.txt"])
    path = env["store"].data_dir / "artifacts" / files["diagnostic.txt"]["id"] / "0"
    path.write_bytes(b"altered bytes")
    assert output_read(env, receipt, files["diagnostic.txt"]).json() == retained.json() | {"replayed": True}
    failed = output_read(env, receipt, files["diagnostic.txt"], "fresh-read")
    assert failed.status_code == 409 and "integrity" in failed.json()["detail"]
    with env["store"].transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM work_tool_operations WHERE tool='compute_output_read'").fetchone()[0] == 1
        assert WorkJobs._usage(db, "program", env["root"]["id"])["cpu_seconds"] == 7


def test_submission_receipt_and_cpu_settlement_are_atomic_and_replayable(installation, tmp_path):
    env = setup(installation, tmp_path, cpu_seconds=200)
    reply = submit(env)
    assert reply.status_code == 200, reply.text
    queued_result = reply.json()["result"]
    assert queued_result["status"] == "queued" and queued_result["reference"] is None
    assert submit(env).json()["replayed"]
    assert submit(env, program="changed").status_code == 409
    with env["store"].transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM work_computations").fetchone()[0] == 1
        assert WorkJobs._usage(db, "program", env["root"]["id"])["cpu_seconds"] == 120
        assert db.execute("SELECT tools FROM work_resource_accounts WHERE scope_kind='program'").fetchone()[0] == 1
    done, _, _, _ = complete(env, queued_result["compute_job_id"])
    assert done["used_cpu_seconds"] == 7
    read = call(env, env["parent"], "compute_read", "read-result", compute_job_id=done["id"])
    assert read.status_code == 200, read.text
    evidence = read.json()["result"]
    assert evidence["status"] == "succeeded" and evidence["reference"]["kind"] == "compute_result"
    text = call(env, env["parent"], "input_read", "read-evidence", reference=evidence["reference"])
    receipt = json.loads(text.json()["result"]["text"])
    assert receipt["schema"] == "reasoning.compute-execution.v1" and receipt["used_cpu_seconds"] == 7
    assert "not a proof" in receipt["attestation"]
    with env["store"].transaction() as db:
        assert WorkJobs._usage(db, "program", env["root"]["id"])["cpu_seconds"] == 7
        assert db.execute("SELECT state FROM work_effects").fetchone()[0] == "settled"
        assert db.execute("SELECT actual_microusd FROM work_effects").fetchone()[0] is None
        assert db.execute("SELECT COUNT(*) FROM work_input_bindings WHERE input_kind='compute_result'").fetchone()[0] == 1
    assert result(env, env["parent"]).status_code == 200


def test_parent_cpu_authorization_is_shared_with_children(installation, tmp_path):
    env = setup(installation, tmp_path, cpu_seconds=200)
    assert submit(env).status_code == 200
    assert delegate(env).status_code == 200
    child = claim(env, "child")
    assert submit(env, "over-shared-budget", row=child).status_code == 409
    with env["store"].transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM compute_jobs").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM work_effects").fetchone()[0] == 1


def test_organization_cpu_quota_applies_without_a_program_cpu_ceiling(installation, tmp_path):
    env = setup(installation, tmp_path)
    env["client"].app.state.compute.set_policy(
        env["owner"]["user_id"], env["owner"]["organization_id"], [IMAGE["id"]], 120, 2
    )
    assert submit(env).status_code == 200
    refused = submit(env, "organization-over-quota")
    assert refused.status_code == 409, refused.text
    with env["store"].transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM compute_jobs").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM work_effects").fetchone()[0] == 1
        assert WorkJobs._usage(db, "program", env["root"]["id"])["cpu_seconds"] == 120


def test_exact_assigned_workspace_source_is_used_and_pinned(installation, tmp_path):
    env = setup(installation, tmp_path)
    saved = call(env, env["parent"], "workspace_write", "source", path="main.py", text="print('exact source')")
    ref = saved.json()["result"]["reference"]
    response = submit(env, program=None, source=ref)
    assert response.status_code == 200, response.text
    identity = response.json()["result"]["compute_job_id"]
    with env["store"].transaction() as db:
        params = json.loads(db.execute("SELECT parameters_json FROM compute_jobs WHERE id=?", (identity,)).fetchone()[0])
        assert params["program"] == "print('exact source')"
        assert json.loads(db.execute("SELECT source_json FROM work_computations").fetchone()[0]) == ref
    assert delegate(env, "unassigned-child").status_code == 200
    child = claim(env, "child")
    assert submit(env, "unassigned", row=child, program=None, source=ref).status_code == 404
    assert call(env, child, "compute_read", "foreign-compute", compute_job_id=identity).status_code == 404


def test_compute_worker_not_model_owns_settlement_and_reconciliation(installation, tmp_path):
    env = setup(installation, tmp_path)
    submit(env).raise_for_status()
    with env["store"].transaction() as db:
        effect = dict(db.execute("SELECT * FROM work_effects").fetchone())
    forged = worker_post(env, f"jobs/{env['parent']['id']}/effects/{effect['id']}/receipt", lease(env["parent"]) | {
        "state": "settled", "actual_microusd": 0, "actual_cpu_seconds": 0, "receipt": "model says free execution",
    })
    assert forged.status_code == 403
    human = env["client"].post(env["target"] + "/effects/" + effect["id"] + "/reconcile", json={
        "expected_request_sha256": effect["request_sha256"], "state": "settled", "actual_microusd": 0,
        "actual_cpu_seconds": 0, "receipt": "must reconcile the compute job instead",
    })
    assert human.status_code == 403, human.text


def test_queued_cancel_settles_zero_and_failure_retains_execution_evidence(installation, tmp_path):
    env = setup(installation, tmp_path)
    first = submit(env).json()["result"]["compute_job_id"]
    canceled = call(env, env["parent"], "compute_cancel", "cancel", compute_job_id=first)
    assert canceled.status_code == 200, canceled.text
    assert canceled.json()["result"]["status"] == "cancelled" and canceled.json()["result"]["reference"]
    second = submit(env, "failed-execution").json()["result"]["compute_job_id"]
    done, _, _, _ = complete(env, second, status="failed", exit_code=1)
    assert done["status"] == "failed"
    with env["store"].transaction() as db:
        assert WorkJobs._usage(db, "program", env["root"]["id"])["cpu_seconds"] == 7
        assert db.execute("SELECT COUNT(*) FROM work_compute_receipts").fetchone()[0] == 2


def test_revoked_work_grant_cannot_start_compute(installation, tmp_path):
    env = setup(installation, tmp_path)
    identity = submit(env).json()["result"]["compute_job_id"]
    with env["store"].transaction(write=True) as db:
        db.execute("DELETE FROM work_worker_projects WHERE worker_id=?", (env["worker"]["id"],))
    _, agent = worker(env["compute"])
    assert agent.post("/api/worker/claim", json={}).json() is None
    with env["store"].transaction() as db:
        assert db.execute("SELECT status FROM compute_jobs WHERE id=?", (identity,)).fetchone()[0] == "failed"
        assert WorkJobs._usage(db, "program", env["root"]["id"])["cpu_seconds"] == 0
    assert submit(env, "revoked").status_code == 403


@pytest.mark.parametrize("change", ["image", "tool", "cpu"])
def test_current_program_authorization_is_rechecked_by_running_compute(installation, tmp_path, change):
    env = setup(installation, tmp_path)
    identity = submit(env).json()["result"]["compute_job_id"]
    _, agent = worker(env["compute"])
    claimed = agent.post("/api/worker/claim", json={}).json()
    spec = env["client"].get(env["target"]).json()["version"]["specification"]
    if change == "image":
        spec["resources"]["allowed_images"] = []
    elif change == "tool":
        spec["resources"]["allowed_tools"].remove("compute_submit")
    else:
        spec["resources"]["cpu_seconds"] = 50
    edited = env["client"].patch(env["target"], json={
        "expected_revision": 1, "reason": "Narrow current compute authorization", "specification": spec,
    })
    assert edited.status_code == 200, edited.text
    heartbeat = agent.post(f"/api/worker/jobs/{identity}/heartbeat", json=lease_body(claimed))
    assert heartbeat.json()["stop"] is True
    assert finish(agent, claimed).json()["status"] == "cancelled"
    with env["store"].transaction() as db:
        assert WorkJobs._usage(db, "program", env["root"]["id"])["cpu_seconds"] == 7


def test_compute_link_and_terminal_receipt_are_immutable(installation, tmp_path):
    env = setup(installation, tmp_path)
    identity = submit(env).json()["result"]["compute_job_id"]
    complete(env, identity)
    for query in (
        "UPDATE work_computations SET fence=999", "DELETE FROM work_computations",
        "UPDATE work_compute_receipts SET body_json='forged'", "DELETE FROM work_compute_receipts",
    ):
        with pytest.raises(Exception, match="(immutable|retained)"), env["store"].transaction(write=True) as db:
            db.execute(query)


def test_expired_assignment_releases_only_unstarted_compute_without_a_host(installation, tmp_path):
    env = setup(installation, tmp_path)
    identity = submit(env).json()["result"]["compute_job_id"]
    expire(env, env["parent"]["id"])
    WorkJobs(env["store"]).expire_due()
    cancel_due(env["client"].app.state.compute)
    cancel_due(env["client"].app.state.compute)
    with env["store"].transaction() as db:
        assert db.execute("SELECT status FROM compute_jobs WHERE id=?", (identity,)).fetchone()[0] == "cancelled"
        assert db.execute("SELECT COUNT(*) FROM work_compute_receipts").fetchone()[0] == 1
        assert WorkJobs._usage(db, "program", env["root"]["id"])["cpu_seconds"] == 0
        # A stopped assignment still needs explicit inspection/reopening; it is
        # not silently resubmitted merely because its queue reservation settled.
        assert db.execute("SELECT status FROM work_jobs WHERE id=?", (env["parent"]["id"],)).fetchone()[0] == "outcome_unknown"
    assert submit(env, "stale").status_code == 409


def test_running_compute_cancellation_waits_for_execution_outcome(installation, tmp_path):
    env = setup(installation, tmp_path)
    identity = submit(env).json()["result"]["compute_job_id"]
    _, agent = worker(env["compute"])
    claimed = agent.post("/api/worker/claim", json={}).json()
    cancelled = call(env, env["parent"], "compute_cancel", "cancel-running", compute_job_id=identity)
    assert cancelled.json()["result"]["status"] == "running" and cancelled.json()["result"]["reference"] is None
    cancel_due(env["client"].app.state.compute)
    with env["store"].transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM work_compute_receipts").fetchone()[0] == 0
        assert WorkJobs._usage(db, "program", env["root"]["id"])["cpu_seconds"] == 120
    heartbeat = agent.post(f"/api/worker/jobs/{identity}/heartbeat", json=lease_body(claimed))
    assert heartbeat.json()["stop"] is True
    assert finish(agent, claimed).json()["status"] == "cancelled"
    with env["store"].transaction() as db:
        assert WorkJobs._usage(db, "program", env["root"]["id"])["cpu_seconds"] == 7


def test_unknown_execution_retains_reservation_until_confirmed_cleanup(installation, tmp_path):
    env = setup(installation, tmp_path)
    identity = submit(env).json()["result"]["compute_job_id"]
    _, agent = worker(env["compute"])
    agent.post("/api/worker/claim", json={}).raise_for_status()
    with env["store"].transaction(write=True) as db:
        db.execute("UPDATE compute_jobs SET lease_expires_at=0 WHERE id=?", (identity,))
    expire(env, env["parent"]["id"])
    WorkJobs(env["store"]).expire_due()
    cancel_due(env["client"].app.state.compute)
    path = job_path(env["compute"][4], {"id": identity})
    assert env["client"].get(path).json()["status"] == "outcome_unknown"
    assert agent.post("/api/worker/claim", json={}).json() is None
    with env["store"].transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM work_compute_receipts").fetchone()[0] == 0
        assert db.execute("SELECT state FROM work_effects").fetchone()[0] == "unknown"
        assert WorkJobs._usage(db, "program", env["root"]["id"])["cpu_seconds"] == 120
    assert env["client"].put(path + "/reconciliation", json={"elapsed_ms": 6001, "cleanup_confirmed": False}).status_code == 400
    settled = env["client"].put(path + "/reconciliation", json={"elapsed_ms": 6001, "cleanup_confirmed": True})
    assert settled.status_code == 200, settled.text
    with env["store"].transaction() as db:
        assert WorkJobs._usage(db, "program", env["root"]["id"])["cpu_seconds"] == 7
        assert db.execute("SELECT state FROM work_effects").fetchone()[0] == "settled"
        assert db.execute("SELECT COUNT(*) FROM work_input_bindings WHERE input_kind='compute_result'").fetchone()[0] == 1


def test_native_wait_detects_lost_compute_host_without_an_unrelated_api_read(installation, tmp_path):
    from test_public_work_worker import LocalTransport

    from reasoning_ledger.server.work_worker import Assignment, Journal, WorkerError

    env = setup(installation, tmp_path)

    class ExpiredComputeHost(LocalTransport):
        def post(self, path, body):
            if path.endswith("/wait"):
                identity = path.split("/")[-2]
                _, agent = worker(env["compute"])
                assert agent.post("/api/worker/claim", json={}).json()["id"] == identity
                with env["store"].transaction(write=True) as db:
                    db.execute("UPDATE compute_jobs SET lease_expires_at=0 WHERE id=?", (identity,))
            return super().post(path, body)

    assignment = Assignment(ExpiredComputeHost(env), Journal(tmp_path), env["parent"])
    with pytest.raises(WorkerError, match="unresolved"):
        assignment.invoke("compute_submit", {
            "image_id": IMAGE["id"], "language": "python", "program": "pass", "resources": DEFAULT_RESOURCES,
        })
    assert assignment.stopped.is_set()
    with env["store"].transaction() as db:
        assert db.execute("SELECT status FROM compute_jobs").fetchone()[0] == "outcome_unknown"
        assert db.execute("SELECT COUNT(*) FROM compute_jobs").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM work_compute_receipts").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM work_results").fetchone()[0] == 0
        assert WorkJobs._usage(db, "program", env["root"]["id"])["cpu_seconds"] == 120


def test_failed_submission_transaction_leaves_no_queue_reservation_or_receipt(installation, tmp_path, monkeypatch):
    env = setup(installation, tmp_path)
    work = env["client"].app.state.work_tools.work

    def fail(*args, **kwargs):
        raise StoreError(409, "injected publication failure")

    with monkeypatch.context() as context:
        context.setattr(work, "_provenance", fail)
        assert submit(env).status_code == 409
    with env["store"].transaction() as db:
        for table in ("compute_jobs", "work_effects", "work_computations", "work_tool_operations"):
            assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        assert WorkJobs._usage(db, "program", env["root"]["id"])["cpu_seconds"] == 0
    assert submit(env).status_code == 200


@pytest.mark.parametrize("completed", [False, True])
def test_portable_recovery_retains_compute_links_evidence_and_uncertain_cpu(installation, tmp_path, completed):
    from reasoning_ledger.server.backup import backup, create_key, restore
    from reasoning_ledger.server.store import Store

    env = setup(installation, tmp_path)
    identity = submit(env).json()["result"]["compute_job_id"]
    if completed:
        complete(env, identity, artifacts={"diagnostic.txt": b"recover exact execution evidence\n"})
    else:
        _, agent = worker(env["compute"])
        assert agent.post("/api/worker/claim", json={}).json()["id"] == identity
    key, archive, destination = tmp_path / "key", tmp_path / "compute.rnbk", tmp_path / "recovered"
    create_key(key)
    saved = backup(env["store"].data_dir, archive, key)
    restore(archive, key, saved["sha256"], destination, source_stopped=True)
    recovered = Store(destination)
    with recovered.transaction() as db:
        assert db.execute("SELECT compute_job_id FROM work_computations").fetchone()[0] == identity
        assert db.execute("SELECT status FROM work_programs").fetchone()[0] == "paused"
        assert db.execute("SELECT revoked_at FROM work_workers").fetchone()[0] is not None
        assert db.execute("SELECT revoked_at FROM compute_workers").fetchone()[0] is not None
        assert WorkJobs._usage(db, "program", env["root"]["id"])["cpu_seconds"] == (7 if completed else 120)
        assert db.execute("SELECT state FROM work_effects").fetchone()[0] == ("settled" if completed else "unknown")
        assert db.execute("SELECT status FROM compute_jobs").fetchone()[0] == ("succeeded" if completed else "outcome_unknown")
        assert db.execute("SELECT COUNT(*) FROM work_compute_receipts").fetchone()[0] == int(completed)
        assert db.execute("SELECT COUNT(*) FROM work_input_bindings WHERE input_kind='compute_result'").fetchone()[0] == int(completed)
        if completed:
            receipt = json.loads(db.execute("SELECT body_json FROM work_compute_receipts").fetchone()[0])
            artifact = receipt["artifacts"][0]
            content = (destination / "artifacts" / artifact["id"] / "0").read_bytes()
            assert content == b"recover exact execution evidence\n"
            assert "sha256:" + hashlib.sha256(content).hexdigest() == artifact["digest"]


def test_concurrent_submission_has_one_queue_job_and_resource_reservation(installation, tmp_path):
    env = setup(installation, tmp_path)
    tools, row = env["client"].app.state.work_tools, env["parent"]
    body = ComputeSubmit(
        **lease(row), client_id="race", image_id=IMAGE["id"], language="python", program="pass", resources=DEFAULT_RESOURCES
    )
    with ThreadPoolExecutor(max_workers=4) as pool:
        replies = list(pool.map(lambda _: tools.execute(env["worker"]["token"], row["id"], "compute_submit", body), range(4)))
    assert sum(not reply["replayed"] for reply in replies) == 1
    with env["store"].transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM compute_jobs").fetchone()[0] == 1
        assert WorkJobs._usage(db, "program", env["root"]["id"])["cpu_seconds"] == 120


def test_native_worker_waits_without_paid_polling_then_returns_exact_execution_reference(installation, tmp_path):
    import httpx
    from test_public_inference import Fixture
    from test_public_provider_stream import WireBody, content, event, usage
    from test_public_work_provider import posts
    from test_public_work_worker import LocalTransport

    from reasoning_ledger.server.work_worker import Journal, run_once

    env = setup(installation, tmp_path, allowed_models=["test/one"])
    fixture = Fixture()
    catalog = fixture.handle

    async def respond(request):
        if request.method == "GET":
            response = await catalog(request)
            body = response.json()
            for model in body["data"]:
                model["supported_parameters"].append("tools")
            return httpx.Response(200, json=body)
        fixture.requests.append(request)
        if len(posts(fixture)) == 1:
            arguments = {"image_id": IMAGE["id"], "language": "python", "program": "print('fixture')", "resources": DEFAULT_RESOURCES}
            frame = {"id": "gen-fixture", "choices": [{"index": 0, "delta": {"tool_calls": [{
                "index": 0, "id": "compute-call", "type": "function",
                "function": {"name": "compute_submit", "arguments": json.dumps(arguments)},
            }]}, "finish_reason": "tool_calls"}]}
        elif len(posts(fixture)) == 2:
            result = json.loads(json.loads(request.content)["messages"][-1]["content"])["result"]
            assert result["status"] == "succeeded" and result["reference"]["kind"] == "compute_result"
            assert len(result["artifacts"]) == 1 and result["artifacts"][0]["name"] == "diagnostic.txt"
            arguments = {"execution": result["reference"], "artifact_id": result["artifacts"][0]["id"]}
            frame = {"id": "gen-fixture", "choices": [{"index": 0, "delta": {"tool_calls": [{
                "index": 0, "id": "output-call", "type": "function",
                "function": {"name": "compute_output_read", "arguments": json.dumps(arguments)},
            }]}, "finish_reason": "tool_calls"}]}
        else:
            result = json.loads(json.loads(request.content)["messages"][-1]["content"])["result"]
            assert result["text"] == "protocol execution output\n"
            assert result["scope"] == "untrusted_execution_output_not_verification"
            frame = content("The retained execution result awaits independent verification.")
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              stream=WireBody(event(frame) + event(usage()) + b"data: [DONE]\n\n", 8192))

    fixture.provider.transport = httpx.MockTransport(respond)
    env["client"].app.state.work_provider.provider = fixture.provider

    class CompleteWhenObserved(LocalTransport):
        def post(self, path, body):
            if path.endswith("/wait"):
                identity = path.split("/")[-2]
                complete(env, identity, artifacts={"diagnostic.txt": b"protocol execution output\n"})
            return super().post(path, body)

    state = tmp_path / "native-compute"
    state.mkdir(mode=0o700)
    Journal(state).save("pending", {"claim_id": "claim-1", "row": env["parent"]})
    saved = run_once(CompleteWhenObserved(env), state)
    assert saved["result"]["outcome"] == "result" and len(posts(fixture)) == 3
    assert len(saved["result"]["outputs"]) == 1 and saved["result"]["outputs"][0]["kind"] == "compute_result"
    with env["store"].transaction() as db:
        assert db.execute("SELECT tools FROM work_resource_accounts WHERE scope_kind='program'").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM compute_jobs").fetchone()[0] == 1


def test_lost_wait_reply_resumes_same_computation_without_duplicate_submission(installation, tmp_path):
    from test_public_work_worker import LocalTransport

    from reasoning_ledger.server.work_worker import Assignment, Journal, WorkerError

    env = setup(installation, tmp_path)
    journal = Journal(tmp_path)

    class LostWait(LocalTransport):
        def post(self, path, body):
            if path.endswith("/wait"):
                complete(env, path.split("/")[-2])
                super().post(path, body)
                raise WorkerError("lost acknowledged wait reply")
            return super().post(path, body)

    arguments = {"image_id": IMAGE["id"], "language": "python", "program": "pass", "resources": DEFAULT_RESOURCES}
    with pytest.raises(WorkerError):
        Assignment(LostWait(env), journal, env["parent"]).invoke("compute_submit", arguments)
    retry = Assignment(LocalTransport(env), journal, env["parent"]).invoke("compute_submit", arguments)
    assert retry["status"] == "succeeded" and retry["reference"]["kind"] == "compute_result"
    with env["store"].transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM compute_jobs").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM work_effects").fetchone()[0] == 1
        assert WorkJobs._usage(db, "program", env["root"]["id"])["cpu_seconds"] == 7
