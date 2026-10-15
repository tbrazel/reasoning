"""Durable general controllers, independent reviews and exact evidence gates."""

import json
from concurrent.futures import ThreadPoolExecutor
from typing import get_args

import httpx
import pytest
from postgres_fixture import postgres_configuration as postgres_configuration
from test_public_inference import KEY, Fixture
from test_public_provider_stream import WireBody, content, event, usage
from test_public_work_contracts import specification
from test_public_work_jobs import claim, environment, expire, lease, worker_post
from test_public_work_jobs import installation as installation

from reasoning_ledger.server.work_contracts import Role


def setup(installation, controller="explore_review_revise", check="llm_review", models=None, tools=None):
    models = models or ["test/one"]
    _, _, client, owner = installation
    client.put(
        f"/api/organizations/{owner['organization_id']}/policy", json={"allowed_models": models, "monthly_budget_microusd": 1_000_000}
    ).raise_for_status()
    client.post(f"/api/organizations/{owner['organization_id']}/provider-keys", json={"key": KEY, "label": "Fixture"}).raise_for_status()
    env = environment(
        installation,
        {
            "max_concurrency": 8,
            "allowed_models": models or [],
            "allowed_tools": tools if tools is not None else ["work_delegate", "input_read", "work_children"],
        },
    )
    response = env["client"].patch(
        env["target"],
        json={
            "expected_revision": 1,
            "reason": "Use fixture controller",
            "specification": specification(
                controller=controller,
                resources=env["resources"],
                acceptance=[{"id": "coverage", "description": "Deliver scoped evidence", "check": check}],
            ),
        },
    )
    assert response.status_code == 200, response.text
    env["root"]["head_revision"] = 2
    enrollment = env["client"].post(
        f"/api/organizations/{env['owner']['organization_id']}/work-workers",
        json={
            "name": "All roles",
            "project_ids": [env["project"]["id"]],
            "roles": list(get_args(Role)),
            "max_parallel": 8,
        },
    )
    assert enrollment.status_code == 201, enrollment.text
    env["worker"] = enrollment.json()
    response = env["client"].post(env["target"] + "/controllers", json={"client_id": "flow", "expected_revision": 2})
    assert response.status_code == 201, response.text
    env["flow"] = response.json()
    env["flow_path"] = env["target"] + "/controllers/" + env["flow"]["id"]
    env["controllers"] = env["client"].app.state.work_controllers
    fixture = Fixture()
    queue = []
    catalog = fixture.handle

    async def respond(request):
        if request.method == "GET":
            response = await catalog(request)
            body = response.json()
            for item in body["data"]:
                item["supported_parameters"] += ["tools", "response_format"]
            return httpx.Response(200, json=body)
        fixture.requests.append(request)
        assert queue, "fixture must supply the simulated provider response"
        body = event(content(queue.pop(0))) + event(usage()) + b"data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=WireBody(body, 8192))

    fixture.provider.transport = httpx.MockTransport(respond)
    env["client"].app.state.work_provider.provider = fixture.provider
    env["provider"], env["response_queue"] = fixture, queue
    return env


def flow(env):
    reply = env["client"].get(env["flow_path"])
    assert reply.status_code == 200, reply.text
    return reply.json()


def verdict(decision="approve", criterion="coverage"):
    return json.dumps(
        {
            "decision": decision,
            "summary": "A scoped appraisal, not a proof certificate.",
            "assessments": [{"criterion_id": criterion, "satisfied": decision == "approve", "reason": "Fixture evidence"}],
        }
    )


def deliver(env, identity, summary="A candidate result", decision=None):
    row = claim(env, identity)
    assert row and row["dispatch"]
    response = submit(env, row, verdict(decision) if decision else summary)
    env["controllers"].tick(env["root"]["id"])
    return row, response


def submit(env, row, summary):
    if row["packet"].get("output_schema"):
        env["response_queue"].append(summary)
        response = worker_post(
            env,
            f"jobs/{row['id']}/inference",
            lease(row)
            | {
                "client_id": "fixture-review",
                "payload": {
                    "model": "test/one",
                    "messages": [
                        {"role": "system", "content": row["packet"]["prompt"]["system_prompt"]},
                        {"role": "user", "content": "Review assigned inputs"},
                    ],
                },
            },
        )
        assert response.status_code == 200 and response.json()["state"] == "complete", response.text
    response = worker_post(
        env,
        f"jobs/{row['id']}/result",
        lease(row)
        | {
            "outcome": "result",
            "summary": summary,
            "outputs": [],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def approved_candidate(env):
    reviewer, _ = deliver(env, "specification", decision="approve")
    assert reviewer["packet"]["specification"]["role"] == "specification_reviewer"
    assert reviewer["packet"]["output_schema"]
    candidate, saved = deliver(env, "candidate")
    return candidate, saved


def test_compaction_output_cannot_act_as_a_specification_review(installation):
    env = setup(installation)
    reviewer = claim(env, "specification")
    text = verdict()
    env["response_queue"].append(text)
    body = lease(reviewer) | {
        "client_id": "memory",
        "purpose": "compaction",
        "payload": {
            "model": "test/one",
            "messages": [
                {"role": "system", "content": reviewer["packet"]["prompt"]["system_prompt"]},
                {"role": "user", "content": "A memory note, not an acceptance verdict."},
            ],
        },
    }
    response = worker_post(env, f"jobs/{reviewer['id']}/inference", body)
    assert response.status_code == 200 and response.json()["purpose"] == "compaction", response.text
    assert worker_post(env, f"jobs/{reviewer['id']}/inference", body | {"purpose": "work"}).status_code == 409
    response = worker_post(env, f"jobs/{reviewer['id']}/result", lease(reviewer) | {"outcome": "result", "summary": text, "outputs": []})
    assert response.status_code == 200, response.text
    env["controllers"].tick(env["root"]["id"])
    assert flow(env)["phase"] == "specification" and flow(env)["status"] == "blocked"
    with env["store"].transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM work_checks").fetchone()[0] == 0


def test_explore_reviews_revises_then_accepts_only_the_latest_exact_result(installation):
    env = setup(installation)
    first, _ = approved_candidate(env)
    reviewer, _ = deliver(env, "critical-review", decision="revise")
    assert reviewer["packet"]["specification"]["role"] == "critic"
    assert flow(env)["phase"] == "working" and flow(env)["round"] == 2
    repaired, saved = deliver(env, "repair", summary="A repaired candidate")
    assert repaired["task_id"] != first["task_id"]
    assert repaired["packet"]["specification"]["inputs"][0]["id"] == first["id"]
    deliver(env, "fresh-review", decision="approve")
    final = flow(env)
    assert final["status"] == final["phase"] == "complete"
    assert final["result"]["id"] == repaired["id"] and final["result"]["sha256"] == saved["sha256"]
    assert not final["missing_checks"]
    assert env["client"].get(env["target"]).json()["status"] == "complete"
    with env["store"].transaction() as db:
        checks = db.execute("SELECT check_kind,source,subject_json FROM work_checks").fetchall()
        assert len(checks) == 1 and checks[0]["check_kind"] == "llm_review" and checks[0]["source"] == "server_model_review"
        assert json.loads(checks[0]["subject_json"])["id"] == repaired["id"]


@pytest.mark.parametrize("kind", ["human_review", "source_check", "tests_passed", "proof_checked"])
def test_model_review_does_not_substitute_for_a_distinct_required_check(installation, kind):
    env = setup(installation, check=kind)
    approved_candidate(env)
    deliver(env, "critical-review", decision="approve")
    waiting = flow(env)
    assert waiting["status"] == "waiting" and waiting["phase"] == "acceptance"
    assert waiting["missing_checks"] == [{"criterion_id": "coverage", "check": kind}]
    request = {
        "client_id": "human-check",
        "expected_version": waiting["version"],
        "subject": waiting["result"],
        "criterion_id": "coverage",
        "check": kind,
        "passed": True,
        "explanation": "Explicit human source judgment",
        "evidence": [waiting["result"]],
    }
    response = env["client"].post(env["flow_path"] + "/checks", json=request)
    if kind in {"tests_passed", "proof_checked"}:
        assert response.status_code == 422
        assert env["client"].get(env["target"]).json()["status"] == "active"
    else:
        assert response.status_code == 201, response.text
        assert env["client"].post(env["flow_path"] + "/checks", json=request).json() == response.json()
        env["controllers"].tick(env["root"]["id"])
        assert flow(env)["status"] == "complete"


def test_build_has_check_repair_and_separate_validation_jobs(installation):
    env = setup(installation, controller="build_check_repair")
    approved_candidate(env)
    assert flow(env)["phase"] == "checking"
    checking, _ = deliver(env, "check", decision="revise")
    assert checking["packet"]["specification"]["role"] == "executor"
    assert flow(env)["phase"] == "working"
    deliver(env, "repair", summary="A rebuilt artifact")
    deliver(env, "check-again", decision="approve")
    assert flow(env)["phase"] == "review"
    validation, _ = deliver(env, "validate", decision="approve")
    assert validation["packet"]["specification"]["role"] == "critic"
    assert flow(env)["status"] == "complete"


def test_malformed_review_creates_a_new_repair_job_without_replaying_the_old_one(installation):
    env = setup(installation)
    first, _ = deliver(env, "bad-json", summary="I approve, trust me.")
    current = flow(env)
    assert current["status"] == "running" and current["phase"] == "specification" and current["current_job"] != first["id"]
    repaired, _ = deliver(env, "valid-json", decision="approve")
    assert repaired["packet"]["specification"]["inputs"][0]["id"] == first["id"]
    assert flow(env)["phase"] == "working"


def test_concurrent_controller_ticks_create_one_successor_and_replay_start(installation):
    env = setup(installation)
    row = claim(env)
    submit(env, row, verdict())
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: env["controllers"].tick(env["root"]["id"]), range(4)))
    assert len(env["client"].get(env["flow_path"] + "/steps").json()["items"]) == 2
    replay = env["client"].post(env["target"] + "/controllers", json={"client_id": "flow", "expected_revision": 2})
    assert replay.status_code == 201 and replay.json()["id"] == env["flow"]["id"]
    assert env["client"].post(env["target"] + "/controllers", json={"client_id": "flow", "expected_revision": 1}).status_code == 409


@pytest.mark.parametrize("fan_in,groups,level,final_inputs", [(8, 3, 1, 4), (3, 8, 2, 3)])
def test_parallel_controller_reduces_many_children_through_independently_reviewed_batches(
    installation, monkeypatch, fan_in, groups, level, final_inputs
):
    monkeypatch.setattr("reasoning_ledger.server.work_integration.FAN_IN", fan_in)
    env = setup(installation, controller="parallel_integrate")
    deliver(env, "specification", decision="approve")
    parent = claim(env, "decomposition")
    for index in range(17):
        response = worker_post(
            env,
            f"jobs/{parent['id']}/tools/work_delegate",
            lease(parent)
            | {
                "client_id": f"branch-{index}",
                "expected_root_revision": 2,
                "specification": specification(
                    role="worker",
                    controller="explore_review_revise",
                    resources=env["resources"],
                    acceptance=[{"id": "coverage", "description": "Deliver scoped evidence", "check": "llm_review"}],
                ),
            },
        )
        assert response.status_code == 200, response.text
    submit(env, parent, "Seventeen branches require review and bounded integration.")
    env["controllers"].tick(env["root"]["id"])
    batches, integrated, frozen_checked = set(), False, False
    for index in range(120):
        if flow(env)["status"] == "complete":
            break
        row = claim(env, f"reduction-{index}")
        if row is None:
            env["controllers"].tick_due()
            continue
        if flow(env)["integration"] is not None and not frozen_checked:
            attempted = env["client"].post(
                env["target"] + "/tasks",
                json={
                    "client_id": "integration-spoofed",
                    "expected_root_revision": 2,
                    "parent_id": env["flow"]["target_task"],
                    "specification": specification(role="worker", resources=env["resources"]),
                },
            )
            assert attempted.status_code == 409 and "pinned" in attempted.text, attempted.text
            forged = attempted.request.content
            body = json.loads(forged) | {"controller_generated": True}
            assert env["client"].post(env["target"] + "/tasks", json=body).status_code == 422
            frozen_checked = True
        assignment = row["packet"]["specification"]
        criterion = assignment["acceptance"][0]["id"]
        if criterion == "scoped_batch" and assignment["role"] == "coordinator":
            batches.add(row["task_id"])
            assert len(assignment["inputs"]) <= fan_in + 1
            assert "not a claim" in assignment["objective"]
        if row["packet"].get("controller", {}).get("phase") == "integration":
            integrated = True
            assert len(assignment["inputs"]) == final_inputs, "root reads reviewed batches and decomposition, not 17 leaves"
        text = verdict(criterion=criterion) if row["packet"].get("output_schema") else "Scoped integrated findings and limitations."
        submit(env, row, text)
        env["controllers"].tick(env["root"]["id"])
    final = flow(env)
    assert final["status"] == "complete", final
    assert len(batches) == groups and integrated and frozen_checked
    assert final["integration"]["level"] == level and final["integration"]["pinned_leaves"] == 17
    with env["store"].transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM work_controllers WHERE status='complete'").fetchone()[0] == 18 + groups
        assert db.execute("SELECT COUNT(*) FROM work_integration_inputs").fetchone()[0] == 17 + groups


def test_parallel_revision_pins_a_new_reduction_round_and_retains_the_old_snapshot(installation):
    env = setup(installation, controller="parallel_integrate")
    deliver(env, "specification", decision="approve")
    initial, _ = deliver(env, "initial-decomposition")
    first, _ = deliver(env, "first-integration")
    deliver(env, "reject-first-integration", decision="revise")
    changed, _ = deliver(env, "revised-decomposition")
    assert changed["task_id"] != initial["task_id"]
    second, _ = deliver(env, "second-integration")
    assert second["id"] != first["id"]
    assert second["packet"]["specification"]["inputs"][0]["id"] == changed["id"]
    deliver(env, "approve-revision", decision="approve")
    assert flow(env)["status"] == "complete" and flow(env)["integration"]["round"] == 2
    with env["store"].transaction() as db:
        rows = db.execute("SELECT round,parent_task_id FROM work_integration_state ORDER BY round").fetchall()
        assert [(row["round"], row["parent_task_id"]) for row in rows] == [(1, initial["task_id"]), (2, changed["task_id"])]


def test_parallel_controller_waits_for_reviewed_children_then_integrates_exact_results(installation):
    env = setup(installation, controller="parallel_integrate")
    deliver(env, "specification", decision="approve")
    coordinator = claim(env, "decompose")
    for name in ("branch-a", "branch-b"):
        response = worker_post(
            env,
            f"jobs/{coordinator['id']}/tools/work_delegate",
            lease(coordinator)
            | {
                "client_id": name,
                "expected_root_revision": 2,
                "specification": specification(
                    role="worker",
                    controller="explore_review_revise",
                    resources=env["resources"],
                    acceptance=[{"id": "coverage", "description": "Deliver child result", "check": "llm_review"}],
                ),
            },
        )
        assert response.status_code == 200, response.text
    saved = worker_post(
        env,
        f"jobs/{coordinator['id']}/result",
        lease(coordinator)
        | {
            "outcome": "result",
            "summary": "Two explicit branches need independent review.",
            "outputs": [],
        },
    )
    assert saved.status_code == 200
    env["controllers"].tick(env["root"]["id"])
    assert flow(env)["phase"] == "children"
    integration = None
    for index in range(20):
        if flow(env)["status"] == "complete":
            break
        row = claim(env, f"child-or-integration-{index}")
        if row is None:
            env["controllers"].tick_due()
            if flow(env)["status"] == "complete":
                break
            row = claim(env, f"awakened-{index}")
        assert row and row["dispatch"]
        phase = row["packet"].get("controller", {}).get("phase")
        role = row["packet"]["specification"]["role"]
        if phase == "integration":
            integration = row
            refs = row["packet"]["specification"]["inputs"]
            assert len(refs) == 3 and all(ref["kind"] == "work_result" for ref in refs)
        summary = verdict() if role in {"critic", "specification_reviewer"} else "A versioned branch result"
        submit(env, row, summary)
        env["controllers"].tick(env["root"]["id"])
    assert integration and flow(env)["status"] == "complete" and flow(env)["result"]["id"] == integration["id"]
    with env["store"].transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM work_controllers WHERE status='complete'").fetchone()[0] == 3


def test_failed_job_needs_explicit_reopening_before_controller_resume(installation):
    env = setup(installation)
    reviewer = claim(env)
    failed = worker_post(
        env,
        f"jobs/{reviewer['id']}/result",
        lease(reviewer)
        | {
            "outcome": "failed",
            "summary": "Provider did not complete",
            "outputs": [],
        },
    )
    assert failed.status_code == 200
    env["controllers"].tick(env["root"]["id"])
    blocked = flow(env)
    assert blocked["status"] == "blocked"
    resume = {"expected_version": blocked["version"], "reason": "Resume explicit retry"}
    assert env["client"].post(env["flow_path"] + "/resume", json=resume).status_code == 409
    reopened = env["client"].post(
        env["target"] + "/tasks/" + reviewer["task_id"] + "/reopen", json={"expected_epoch": 0, "reason": "Authorize a fresh attempt"}
    )
    assert reopened.status_code == 200, reopened.text
    queued = env["client"].post(env["target"] + "/tasks/" + reviewer["task_id"] + "/jobs", json={"expected_epoch": 1})
    assert queued.status_code == 202, queued.text
    resumed = env["client"].post(env["flow_path"] + "/resume", json=resume)
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["current_job"] != reviewer["id"]
    deliver(env, "fresh-specification", decision="approve")
    assert flow(env)["phase"] == "working"


def test_root_revision_change_blocks_controller_and_explicit_cancel_allows_restart(installation):
    env = setup(installation)
    response = env["client"].patch(
        env["target"],
        json={
            "expected_revision": 2,
            "reason": "Human redirect",
            "specification": specification(resources=env["resources"], objective="A changed objective"),
        },
    )
    assert response.status_code == 200
    env["controllers"].tick_due()
    blocked = flow(env)
    assert blocked["status"] == "blocked"
    canceled = env["client"].post(
        env["flow_path"] + "/cancel", json={"expected_version": blocked["version"], "reason": "Use new objective"}
    )
    assert canceled.status_code == 200, canceled.text
    restarted = env["client"].post(env["target"] + "/controllers", json={"client_id": "redirected", "expected_revision": 3})
    assert restarted.status_code == 201 and restarted.json()["root_revision"] == 3


def test_unattested_worker_approval_cannot_become_a_verified_model_review(installation):
    env = setup(installation)
    row = claim(env)
    completion = worker_post(
        env,
        f"jobs/{row['id']}/result",
        lease(row)
        | {
            "outcome": "result",
            "summary": verdict(),
            "outputs": [],
        },
    )
    assert completion.status_code == 200, completion.text
    env["controllers"].tick(env["root"]["id"])
    assert flow(env)["status"] == "blocked" and "attestation" in flow(env)["error"]
    with env["store"].transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM work_checks").fetchone()[0] == 0


def test_native_worker_and_wake_frontier_complete_an_actual_controller_cycle(installation, tmp_path):
    from test_public_work_worker import Journal, LocalTransport, run_once

    env = setup(installation)
    state = tmp_path / "native-controller"
    state.mkdir(mode=0o700)
    for reply in [verdict(), "A concrete candidate", verdict()]:
        env["response_queue"].append(reply)
        saved = run_once(LocalTransport(env), state)
        assert saved["result"]["outcome"] == "result"
        env["controllers"].tick_due()
    assert flow(env)["status"] == "complete"
    requests = [request for request in env["provider"].requests if request.method == "POST"]
    assert len(requests) == 3
    review_payload = json.loads(requests[-1].content)
    assignment = json.loads(next(message["content"] for message in review_payload["messages"] if message["role"] == "user"))
    assert any("concrete candidate" in item["text"] for item in assignment["assigned_inputs"])
    assert assignment["controller"]["phase"] == "review"
    assert Journal(state).saved("pending")["claim_id"]


@pytest.mark.parametrize("known", [True, False])
def test_known_paid_protocol_failure_recovers_but_unknown_charge_does_not(installation, known):
    env = setup(installation)
    row = claim(env)
    normal = env["provider"].handle

    async def broken(request):
        if request.method == "GET":
            return await normal(request)
        body = event(content("Partial output")) + (event(usage()) if known else b"") + b"data: {invalid}\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=WireBody(body, 8192))

    env["provider"].provider.transport = httpx.MockTransport(broken)
    failed = worker_post(
        env,
        f"jobs/{row['id']}/inference",
        lease(row)
        | {
            "client_id": "known-protocol",
            "payload": {
                "model": "test/one",
                "messages": [
                    {"role": "system", "content": row["packet"]["prompt"]["system_prompt"]},
                    {"role": "user", "content": "Review assigned inputs"},
                ],
            },
        },
    )
    assert failed.status_code == 200
    assert (failed.json()["usage"]["cost_microusd"] if known else failed.json()["usage"]) == (8 if known else None)
    completion = worker_post(
        env,
        f"jobs/{row['id']}/result",
        lease(row) | {"outcome": "failed", "summary": "Paid stream could not be parsed", "outputs": []},
    )
    assert completion.status_code == (200 if known else 409), completion.text
    if not known:
        expire(env, row["id"])
        env["client"].app.state.work_jobs.expire_due()
    env["controllers"].tick_due()
    recovered = flow(env)
    assert recovered["status"] == ("running" if known else "blocked") and recovered["round"] == (2 if known else 1)
    assert (recovered["current_job"] != row["id"]) == known
    with env["store"].transaction() as db:
        assert db.execute("SELECT actual_microusd FROM work_effects WHERE job_id=?", (row["id"],)).fetchone()[0] == (8 if known else None)


def test_negative_human_review_deduplicates_pinned_inputs_and_restarts_revision(installation):
    env = setup(installation, check="human_review")
    approved_candidate(env)
    deliver(env, "critical-review", decision="approve")
    waiting = flow(env)
    response = env["client"].post(
        env["flow_path"] + "/checks",
        json={
            "client_id": "reject",
            "expected_version": waiting["version"],
            "subject": waiting["result"],
            "criterion_id": "coverage",
            "check": "human_review",
            "passed": False,
            "explanation": "Repair a concrete omission",
            "evidence": [waiting["result"]],
        },
    )
    assert response.status_code == 201, response.text
    assert flow(env)["status"] == "running" and flow(env)["phase"] == "working"
    row = claim(env, "human-requested-revision")
    assert row["packet"]["specification"]["inputs"] == [waiting["result"]]


def test_transition_refusal_rolls_back_partial_successor_and_preserves_prior_result(installation, monkeypatch):
    from reasoning_ledger.server.store import StoreError

    env = setup(installation)
    approved_candidate(env)
    row = claim(env, "revision-review")
    submit(env, row, verdict("revise"))
    previous = flow(env)
    original = env["controllers"].work._task_create

    def refused(*arguments, **keywords):
        original(*arguments, **keywords)
        raise StoreError(409, "fixture refuses after preparing a successor")

    with env["store"].transaction() as db:
        count = db.execute("SELECT COUNT(*) FROM work_tasks").fetchone()[0]
    monkeypatch.setattr(env["controllers"].work, "_task_create", refused)
    env["controllers"].tick_due()
    blocked = flow(env)
    assert blocked["status"] == "blocked" and blocked["round"] == previous["round"]
    assert blocked["result"] == previous["result"]
    with env["store"].transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM work_tasks").fetchone()[0] == count
