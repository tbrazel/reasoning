"""Durable owner consent, exact previews and post-consent lease safety.

Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
"""

import asyncio
import json
import time

import httpx
import pytest
from test_public_bot_runtime import setup
from test_public_bots import fixture as bot_fixture
from test_public_bots import member

from reasoning_ledger.server.api import Config, create_app
from reasoning_ledger.server.bot_developer import BotDeveloper
from reasoning_ledger.server.bot_runtime import BotRuntime
from reasoning_ledger.server.store import StoreError


@pytest.fixture
def fixture(tmp_path):
    return bot_fixture.__wrapped__(tmp_path)


@pytest.mark.parametrize("kind", ["execution", "issue"])
def test_owner_consent_does_not_expire_with_heartbeat_age_but_execution_does(fixture, kind):
    store, owner, _, _, _, _ = fixture
    runtime, bot, _, run, _ = setup(fixture, local_tools=True)
    developer = BotDeveloper(runtime)
    developer.configure(owner["user_id"], owner["organization_id"], bot["id"], True, ["research/bench"], [])
    args = (bot["token"], run["id"], runtime.claim(bot["token"])["lease"])
    handler = runtime if kind == "execution" else developer
    approval = (runtime.request_execution(*args, ["python3", "-c", "print(4)"]) if kind == "execution"
                else developer.request(*args, "research/bench", "Exact title", "Exact body", "issue"))
    with store.transaction(write=True) as db:
        db.execute("UPDATE bot_runs SET updated_at=? WHERE id=?", (int(time.time()) - 86_400, run["id"]))
        BotRuntime.expire(db, owner["organization_id"])
    assert runtime.claim(bot["token"]) is None
    assert handler.approvals(owner["user_id"], owner["organization_id"])[0]["id"] == approval["id"]
    assert runtime.progress(*args)["waiting_for_approval"]
    handler.approve(owner["user_id"], owner["organization_id"], approval["id"], True)
    consume = runtime.execution if kind == "execution" else developer.receipt
    assert consume(*args, approval["id"], consume=True)["status"] == "consumed"
    assert not runtime.progress(*args)["waiting_for_approval"]
    with store.transaction(write=True) as db:
        db.execute("UPDATE bot_runs SET updated_at=0 WHERE id=?", (run["id"],))
        BotRuntime.expire(db, owner["organization_id"])
    assert runtime.get(owner["user_id"], owner["organization_id"], run["id"])["status"] == "outcome_unknown"
    with pytest.raises(StoreError):
        consume(*args, approval["id"], consume=True)


@pytest.mark.parametrize("kind", ["execution", "issue"])
def test_decision_and_consumption_reject_changed_exact_request(fixture, kind):
    store, owner, _, _, _, _ = fixture
    runtime, bot, _, run, _ = setup(fixture, local_tools=True)
    developer = BotDeveloper(runtime)
    developer.configure(owner["user_id"], owner["organization_id"], bot["id"], True, ["research/bench"], [])
    args = (bot["token"], run["id"], runtime.claim(bot["token"])["lease"])
    handler = runtime if kind == "execution" else developer
    approval = (runtime.request_execution(*args, ["python3", "-c", "print(4)"]) if kind == "execution"
                else developer.request(*args, "research/bench", "Exact title", "Exact body", "issue"))
    with pytest.raises(StoreError, match="changed"):
        handler.approve(owner["user_id"], owner["organization_id"], approval["id"], True, request_sha256="f" * 64)
    correct_hash = approval["command_sha256" if kind == "execution" else "document_sha256"]
    handler.approve(owner["user_id"], owner["organization_id"], approval["id"], True, request_sha256=correct_hash)
    with store.transaction(write=True) as db:
        if kind == "execution":
            db.execute("UPDATE bot_execution_approvals SET command_json=? WHERE id=?", ('["python3","-c","print(999)"]', approval["id"]))
        else:
            db.execute("UPDATE bot_issue_approvals SET body='Changed after approval' WHERE id=?", (approval["id"],))
    consume = runtime.execution if kind == "execution" else developer.receipt
    with pytest.raises(StoreError, match="changed"):
        consume(*args, approval["id"], consume=True)


def test_disabled_execution_hidden_and_cannot_be_consumed_after_approval(fixture):
    _, owner, _, _, _, _ = fixture
    runtime, bot, _, run, _ = setup(fixture, local_tools=True)
    args = (bot["token"], run["id"], runtime.claim(bot["token"])["lease"])
    approval = runtime.request_execution(*args, ["python3", "-c", "print(4)"])
    runtime.approve(owner["user_id"], owner["organization_id"], approval["id"], True)
    runtime.advertise(bot["token"], "codex", ["default"], False, False)
    assert runtime.approvals(owner["user_id"], owner["organization_id"]) == []
    with pytest.raises(StoreError, match="disabled local execution"):
        runtime.execution(*args, approval["id"], consume=True)


def test_pending_approval_wire_is_owner_only_with_exact_preview_and_csrf(fixture, monkeypatch):
    import fastapi.dependencies.utils
    import fastapi.routing

    async def inline(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(fastapi.dependencies.utils, "run_in_threadpool", inline)
    monkeypatch.setattr(fastapi.routing, "run_in_threadpool", inline)
    store, owner, _, _, _, _ = fixture
    other = member(fixture)
    runtime, bot, _, run, _ = setup(fixture, local_tools=True)
    args = (bot["token"], run["id"], runtime.claim(bot["token"])["lease"])
    approval = runtime.request_execution(*args, ["python3", "-c", "print(4)"])
    app = create_app(Config(store.data_dir, "https://server.example", inference_enabled=False))
    root = f"/api/organizations/{owner['organization_id']}/my-bots"

    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://server.example") as client:
            assert (await client.get(root + "/pending-approvals")).status_code == 401
            receipt_path = f"/api/bot/runs/{run['id']}/receipt"
            assert (await client.post(receipt_path, json={})).status_code == 401
            bearer = {"authorization": "Bearer " + bot["token"]}
            assert (await client.post(receipt_path, json={}, headers=bearer)).json()["status"] == "waiting_approval"
            client.cookies.set("__Host-reasoning_session", owner["access_token"])
            rows = (await client.get(root + "/pending-approvals")).json()
            assert rows[0]["kind"] == "execution" and rows[0]["bot_id"] == bot["id"]
            assert rows[0]["requester_name"] == "Owner" and rows[0]["topic_name"] == "beta"
            body = {"allow": True, "request_sha256": approval["command_sha256"]}
            path = root + "/execution-approvals/" + approval["id"]
            assert (await client.post(path, json=body)).status_code == 403
            headers = {"origin": "https://server.example", "x-csrf-token": owner["csrf_token"]}
            assert (await client.post(path, json={**body, "request_sha256": "f" * 64}, headers=headers)).status_code == 409
            assert (await client.post(path, json=body, headers=headers)).status_code == 200
            assert (await client.get(root + "/pending-approvals")).json() == []
            with store.transaction(write=True) as db:
                other_session = store._new_session(db, other)
            client.cookies.set("__Host-reasoning_session", other_session["access_token"])
            assert (await client.get(root + "/pending-approvals")).json() == []

    asyncio.run(check())


def test_cli_runtime_excludes_server_confirmed_waiting_for_owner(tmp_path, monkeypatch):
    from reasoning_ledger.server import bot_agent

    clock = [0.0]
    monkeypatch.setattr(bot_agent.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(bot_agent.time, "sleep", lambda duration: clock.__setitem__(0, clock[0] + 20))

    class Process:
        returncode = 0

        def __init__(self, arguments, **options):
            self.polls = 0
            for event in ({"type": "thread.started", "thread_id": "a" * 32},
                          {"type": "item.completed", "item": {"type": "agent_message", "text": "Approved result"}},
                          {"type": "turn.completed"}):
                options["stdout"].write((json.dumps(event) + "\n").encode())

        def poll(self):
            self.polls += 1
            return 0 if self.polls > 4 else None

    monkeypatch.setattr(bot_agent.subprocess, "Popen", Process)
    result = bot_agent.invoke("codex", tmp_path, "Test", deadline=30,
                              heartbeat=lambda: {"active": True, "waiting_for_approval": True})
    assert result["text"] == "Approved result" and clock[0] >= 60


@pytest.mark.parametrize("kind, already_approved", [("execution", False), ("execution", True), ("issue", False), ("issue", True)])
def test_transport_interruption_retains_unconsumed_consent_without_promising_delivery(fixture, kind, already_approved):
    _, owner, _, _, _, _ = fixture
    runtime, bot, _, run, _ = setup(fixture, local_tools=True)
    developer = BotDeveloper(runtime)
    developer.configure(owner["user_id"], owner["organization_id"], bot["id"], True, ["research/bench"], [])
    args = (bot["token"], run["id"], runtime.claim(bot["token"])["lease"])
    handler = runtime if kind == "execution" else developer
    approval = (runtime.request_execution(*args, ["python3", "-c", "print(4)"]) if kind == "execution"
                else developer.request(*args, "research/bench", "Exact title", "Exact body", "issue"))
    if already_approved:
        handler.approve(owner["user_id"], owner["organization_id"], approval["id"], True)
    receipt = runtime.finish(*args, "outcome_unknown", error="cli_failed")
    assert receipt["status"] == "waiting_approval" and receipt["error_code"] == "approval_transport_ended"
    retained = handler.approvals(owner["user_id"], owner["organization_id"])[0]
    assert retained["id"] == approval["id"] and retained["delivery_interrupted"]
    with pytest.raises(StoreError, match="transport ended"):
        handler.approve(owner["user_id"], owner["organization_id"], approval["id"], True)
    consume = runtime.execution if kind == "execution" else developer.receipt
    with pytest.raises(StoreError, match="transport ended"):
        consume(*args, approval["id"], consume=True)
    handler.approve(owner["user_id"], owner["organization_id"], approval["id"], False)
    assert runtime.stop(owner["user_id"], owner["organization_id"], run["id"])["status"] == "cancelled"
    assert runtime.receipt(bot["token"], run["id"])["status"] == "cancelled"


@pytest.mark.parametrize("kind", ["execution", "issue"])
def test_later_pending_consent_cannot_erase_uncertainty_after_consumed_effect(fixture, kind):
    _, owner, _, _, _, _ = fixture
    runtime, bot, _, run, _ = setup(fixture, local_tools=True)
    developer = BotDeveloper(runtime)
    developer.configure(owner["user_id"], owner["organization_id"], bot["id"], True, ["research/bench"], [])
    args = (bot["token"], run["id"], runtime.claim(bot["token"])["lease"])
    handler = runtime if kind == "execution" else developer
    approval = (runtime.request_execution(*args, ["python3", "-c", "print(4)"]) if kind == "execution"
                else developer.request(*args, "research/bench", "Exact title", "Exact body", "issue"))
    handler.approve(owner["user_id"], owner["organization_id"], approval["id"], True)
    consume = runtime.execution if kind == "execution" else developer.receipt
    consume(*args, approval["id"], consume=True)
    runtime.request_execution(*args, ["python3", "-c", "print(5)"])
    assert runtime.finish(*args, "outcome_unknown", error="cli_failed")["status"] == "outcome_unknown"


def test_owner_or_requester_revocation_invalidates_pending_execution(fixture):
    store, owner, topics, _, _, _ = fixture
    other = member(fixture)
    runtime, bot, model, first, _ = setup(fixture, local_tools=True, other_users=True)
    initial = runtime.claim(bot["token"])
    runtime.finish(bot["token"], first["id"], initial["lease"], "succeeded", "Initial answer")
    run = runtime.enqueue(other, owner["organization_id"], topics[1], model, "other-approval", prompt="Run locally")
    args = (bot["token"], run["id"], runtime.claim(bot["token"])["lease"])
    approval = runtime.request_execution(*args, ["python3", "-c", "print(4)"])
    store.change_member(owner["user_id"], owner["organization_id"], other, None)
    assert runtime.approvals(owner["user_id"], owner["organization_id"]) == []
    with pytest.raises(StoreError):
        runtime.approve(owner["user_id"], owner["organization_id"], approval["id"], True)
    with pytest.raises(StoreError):
        runtime.execution(*args, approval["id"], consume=True)


def test_interrupted_approval_runner_retains_receipt_and_never_replays_cli(fixture, tmp_path):
    from reasoning_ledger.deployment.private import read_document
    from reasoning_ledger.server.bot_client import BotClientError
    from reasoning_ledger.server.bot_runner import runtime_step

    _, owner, _, _, _, bots = fixture
    runtime, bot, _, run, _ = setup(fixture, local_tools=True)
    directory, workspace = tmp_path / "private", tmp_path / "workspace"
    directory.mkdir(mode=0o700)
    workspace.mkdir(mode=0o700)
    calls = []

    class Client:
        def messages(self, after, limit):
            return bots.messages(bot["token"], after, limit)

        def _request(self, method, path, body=None):
            if path == "/api/bot/runtime":
                return runtime.advertise(bot["token"], **body)
            if path.endswith("/claim"):
                return runtime.claim(bot["token"])
            if path.endswith("/receipt"):
                return runtime.receipt(bot["token"], run["id"])
            assert path.endswith("/finish")
            return runtime.finish(bot["token"], run["id"], **body)

    def runner(engine, cwd, prompt, local_tools, session, **options):
        calls.append("CLI invocation")
        gateway = read_document(options["gateway"])
        runtime.request_execution(bot["token"], run["id"], gateway["lease"], ["python3", "-c", "print(4)"])
        raise BotClientError("Native MCP transport fixture timeout")

    agent = {"engine": "codex", "workspace": str(workspace), "local_tools": True, "models": ["default", "test-model"]}
    binding = {"server": "https://example.org", "bot_id": bot["id"], "owner_id": owner["user_id"], "allow_other_users": False}
    configuration = {"server": binding["server"], "token": bot["token"]}
    client = Client()
    with pytest.raises(BotClientError, match="fixture timeout"):
        runtime_step(client, directory, configuration, agent, binding, runner=runner)
    assert read_document(directory / "runtime-runner.json")["pending"]["phase"] == "approval_interrupted"
    with pytest.raises(BotClientError, match="Approval retained"):
        runtime_step(client, directory, configuration, agent, binding, runner=runner)
    assert calls == ["CLI invocation"]
    runtime.stop(owner["user_id"], owner["organization_id"], run["id"])
    assert not runtime_step(client, directory, configuration, agent, binding, runner=runner)["replied"]
    assert read_document(directory / "runtime-runner.json")["pending"] is None and calls == ["CLI invocation"]
