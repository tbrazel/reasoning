"""Actual project-worker/native-agent execution against the scoped server API."""

import json

import httpx
import pytest
from postgres_fixture import postgres_configuration as postgres_configuration
from test_public_work_jobs import installation as installation
from test_public_work_jobs import lease, worker_post
from test_public_work_provider import posts, prepared

from reasoning_ledger.server.work_worker import Assignment, Journal, Transport, WorkerError, origin, run_once


class LocalTransport:
    def __init__(self, env):
        self.env = env
        self.calls = []

    def post(self, path, body):
        self.calls.append((path, body))
        reply = worker_post(self.env, path, body)
        if reply.status_code != 200:
            raise WorkerError(f"fixture HTTP {reply.status_code}")
        return reply.json()


def test_worker_executes_pinned_assignment_through_broker_and_returns_for_review(installation, tmp_path):
    env, row, fixture = prepared(installation)
    state = tmp_path / "worker-state"
    state.mkdir(mode=0o700)
    transport = LocalTransport(env)
    Journal(state).save("pending", {"claim_id": "claim-1", "row": row})
    saved = run_once(transport, state)
    assert saved["result"]["outcome"] == "result" and saved["result"]["summary"] == "α = 1."
    assert len(posts(fixture)) == 1
    payload = json.loads(posts(fixture)[0].content)
    assert payload["messages"][0]["content"] == row["packet"]["prompt"]["system_prompt"]
    assert "max_tokens" not in payload and "max_completion_tokens" not in payload
    task = env["client"].get(env["target"] + "/tasks/" + row["task_id"]).json()
    assert task["status"] == "review", task
    events = env["client"].get(env["target"] + "/jobs/" + row["id"] + "/events").json()
    assert {event["event"]["phase"] for event in events["items"]} >= {"run.started", "run.completed", "round", "content"}
    assert Journal(state).saved("pending")["claim_id"] != "claim-1"
    assert all(path.stat().st_mode & 0o077 == 0 for path in state.rglob("*.json"))


def test_lost_inference_reply_uses_retained_identity_without_a_second_paid_request(installation, tmp_path):
    env, row, fixture = prepared(installation)
    state = tmp_path / "retry"
    state.mkdir(mode=0o700)
    journal = Journal(state)
    normal = LocalTransport(env)

    class LostReply(LocalTransport):
        def post(self, path, body):
            super().post(path, body)
            raise WorkerError("fixture loses reply after server retains it")

    payload = {
        "model": "test/one",
        "messages": [
            {"role": "system", "content": row["packet"]["prompt"]["system_prompt"]},
            {"role": "user", "content": "Retry exact input"},
        ],
    }
    with pytest.raises(WorkerError):
        Assignment(LostReply(env), journal, row).complete(payload, timeout=30)
    saved = Assignment(normal, journal, row).complete(payload, timeout=30)
    assert saved["choices"][0]["message"]["content"] == "α = 1." and len(posts(fixture)) == 1
    with pytest.raises(WorkerError, match="differs"):
        Assignment(normal, journal, row).complete({**payload, "messages": [{"role": "user", "content": "Changed"}]}, timeout=30)


def test_progress_is_brief_idempotent_and_fenced(installation):
    env, row, _ = prepared(installation)
    body = lease(row) | {"client_id": "thinking", "phase": "reasoning", "summary": "Checking scoped sources"}
    first = worker_post(env, f"jobs/{row['id']}/progress", body)
    assert first.status_code == 200, first.text
    assert worker_post(env, f"jobs/{row['id']}/progress", body).json() == first.json()
    assert worker_post(env, f"jobs/{row['id']}/progress", body | {"summary": "Changed"}).status_code == 409
    assert worker_post(env, f"jobs/{row['id']}/progress", body | {"fence": row["fence"] + 1}).status_code == 409


def test_lost_terminal_reply_recovers_before_heartbeat_of_completed_job(installation, tmp_path):
    env, row, fixture = prepared(installation)
    state = tmp_path / "terminal-retry"
    state.mkdir(mode=0o700)
    Journal(state).save("pending", {"claim_id": "claim-1", "row": row})

    class LostTerminal(LocalTransport):
        def post(self, path, body):
            reply = super().post(path, body)
            if path.endswith("/result"):
                raise WorkerError("fixture loses terminal reply")
            return reply

    with pytest.raises(WorkerError):
        run_once(LostTerminal(env), state)
    retry = LocalTransport(env)
    saved = run_once(retry, state)
    assert saved["result"]["summary"] == "α = 1." and len(posts(fixture)) == 1
    assert len(retry.calls) == 1 and retry.calls[0][0].endswith("/result")


def test_worker_invoker_refuses_model_supplied_transport_authority(installation, tmp_path):
    env, row, _ = prepared(installation)
    assignment = Assignment(LocalTransport(env), Journal(tmp_path), row)
    with pytest.raises(WorkerError, match="authority"):
        assignment.invoke("input_read", {"lease": "forged"})


def test_native_worker_executes_forty_scoped_calls_without_hidden_tool_ceiling(installation, tmp_path):
    from test_public_provider_stream import WireBody, content, event, usage

    env, row, fixture = prepared(installation, tools=["knowledge_search"])
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
            calls = [
                {
                    "index": index,
                    "id": f"call-{index}",
                    "type": "function",
                    "function": {"name": "knowledge_search", "arguments": json.dumps({"query": "unseen-source"})},
                }
                for index in range(40)
            ]
            frame = {"id": "gen-fixture", "choices": [{"index": 0, "delta": {"tool_calls": calls}, "finish_reason": "tool_calls"}]}
        else:
            frame = content("Forty searches completed; coverage remains open.")
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=WireBody(event(frame) + event(usage()) + b"data: [DONE]\n\n", 8192),
        )

    fixture.provider.transport = httpx.MockTransport(respond)
    state = tmp_path / "forty-tools"
    state.mkdir(mode=0o700)
    Journal(state).save("pending", {"claim_id": "claim-1", "row": row})
    saved = run_once(LocalTransport(env), state)
    assert saved["result"]["outcome"] == "result" and len(posts(fixture)) == 2
    with env["store"].transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM work_tool_operations").fetchone()[0] == 40
        assert db.execute("SELECT tools FROM work_resource_accounts WHERE scope_kind='program'").fetchone()[0] == 40
    second = json.loads(posts(fixture)[1].content)
    assert sum(message["role"] == "tool" for message in second["messages"]) == 40


@pytest.mark.parametrize(
    "server", ["http://remote.example", "https://user:secret@example.org", "https://example.org/other", "https://example.org?token=x"]
)
def test_worker_rejects_unsafe_server_origins(server):
    with pytest.raises(WorkerError):
        origin(server)


def test_worker_transport_refuses_redirects_and_does_not_expose_http_bodies():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(302, headers={"location": "https://other.example/steal"}, text="rnaw_private_response")

    transport = Transport({"server": "https://chat.example.org", "token": "rnaw_" + "a" * 43}, transport=httpx.MockTransport(respond))
    try:
        with pytest.raises(WorkerError) as error:
            transport.post("claim", {"claim_id": "test"})
        assert len(requests) == 1 and "rnaw_" not in str(error.value)
    finally:
        transport.close()


def test_worker_refuses_symlinked_journal_and_tampered_packet(installation, tmp_path):
    env, row, fixture = prepared(installation)
    target = tmp_path / "target.json"
    target.write_text("{}")
    (tmp_path / "evil.json").symlink_to(target)
    from reasoning_ledger.deployment.host import InstallError

    with pytest.raises(InstallError):
        Journal(tmp_path).save("evil", {})
    state = tmp_path / "tampered"
    state.mkdir(mode=0o700)
    row["packet"]["prompt"]["system_prompt"] = "Changed prompt"
    Journal(state).save("pending", {"claim_id": "claim-1", "row": row})
    with pytest.raises(WorkerError, match="digests"):
        run_once(LocalTransport(env), state)
    assert not posts(fixture)


def test_running_inference_is_not_cached_as_terminal_or_replaced_by_a_failed_result(tmp_path):
    class RunningTransport:
        def __init__(self):
            self.count = 0

        def post(self, path, body):
            self.count += 1
            if self.count == 1:
                return {"state": "running"}
            return {"state": "complete", "response": {"choices": [{"message": {"content": "Retained answer"}}]}}

    transport = RunningTransport()
    journal = Journal(tmp_path)
    row = {"id": "a" * 32, "attempt_id": "attempt", "fence": 1, "lease": "b" * 64}
    payload = {"model": "test/one", "messages": [{"role": "user", "content": "Exact input"}]}
    first = Assignment(transport, journal, row)
    with pytest.raises(WorkerError, match="still running"):
        first.complete(payload, timeout=30)
    assert first.stopped.is_set() and "response" not in journal.saved("call-1")
    resumed = Assignment(transport, journal, row).complete(payload, timeout=30)
    assert resumed["choices"][0]["message"]["content"] == "Retained answer" and transport.count == 2
