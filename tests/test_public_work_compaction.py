"""Actual native/broker compaction, bounded conversation and lost-note recovery."""

import json

import httpx
import pytest
from postgres_fixture import postgres_configuration as postgres_configuration
from test_public_provider_stream import WireBody, content, event, usage
from test_public_work_jobs import installation as installation
from test_public_work_jobs import lease, worker_post
from test_public_work_provider import prepared
from test_public_work_worker import LocalTransport

from reasoning_ledger.server.work_worker import Journal, WorkerError, run_once


def long_turn(installation, tmp_path, malformed=0):
    env, row, fixture = prepared(installation, tools=["knowledge_search"])
    catalog, bodies, rounds, repairs = fixture.handle, [], [], []

    async def respond(request):
        if request.method == "GET":
            response = await catalog(request)
            body = response.json()
            for model in body["data"]:
                model["context_length"] = 32000
                model["supported_parameters"].append("tools")
            return httpx.Response(200, json=body)
        body = json.loads(request.content)
        bodies.append(body)
        assert "max_tokens" not in body and "max_completion_tokens" not in body
        assert body["messages"][0] == {"role": "system", "content": row["packet"]["prompt"]["system_prompt"]}
        compacting = "internal_context_compaction" in body["messages"][1]["content"]
        if compacting:
            repairs.append(body)
            answer = (
                "invalid memory"
                if len(repairs) <= malformed
                else json.dumps(
                    {
                        "summary": "Research continues; this is internal memory, not acceptance evidence.",
                        "unresolved": ["Coverage remains open."],
                        "next_actions": ["Continue the scoped investigation."],
                        "obstructions": ["No independently checked result is present."],
                    }
                )
            )
            frame = content(answer)
        else:
            rounds.append(body)
            if len(rounds) <= 35:
                frame = {
                    "id": "gen-fixture",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "content": "Checking scoped source evidence. " * 20,
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": f"source-{len(rounds)}",
                                        "type": "function",
                                        "function": {"name": "knowledge_search", "arguments": json.dumps({"query": "missing-source"})},
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                }
            else:
                frame = content("Thirty-five rounds completed; the research obligation remains open.")
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=WireBody(event(frame) + event(usage()) + b"data: [DONE]\n\n", 8192)
        )

    fixture.provider.transport = httpx.MockTransport(respond)
    state = tmp_path / "compacting-worker"
    state.mkdir(mode=0o700)
    Journal(state).save("pending", {"claim_id": "claim-1", "row": row})
    return env, row, state, bodies, rounds, repairs


def test_automatic_compaction_keeps_long_agent_running_and_evicts_native_history(installation, tmp_path):
    env, _, state, bodies, rounds, repairs = long_turn(installation, tmp_path)
    result = run_once(LocalTransport(env), state)
    assert result["result"]["outcome"] == "result" and len(rounds) == 36
    assert repairs and any("internal_context_checkpoint" in json.dumps(body["messages"]) for body in rounds)
    assert max(len(body["messages"]) for body in rounds) < 70, "native must not retain the entire 72-message conversation"
    for body in rounds:
        calls = set()
        for message in body["messages"]:
            if message["role"] == "assistant":
                calls.update(item["id"] for item in message.get("tool_calls", []))
            if message["role"] == "tool":
                assert message["tool_call_id"] in calls, "compaction must not orphan tool replies"
    with env["store"].transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM work_compactions").fetchone()[0] >= 1
        assert db.execute("SELECT COUNT(*) FROM work_provider_calls WHERE purpose='compaction'").fetchone()[0] == len(repairs)
        assert db.execute("SELECT COUNT(*) FROM work_effects WHERE state='settled'").fetchone()[0] == len(bodies)
        assert db.execute("SELECT COUNT(*) FROM work_tool_operations").fetchone()[0] == 35


def test_lost_compaction_receipt_recovers_without_repeating_paid_calls_or_tools(installation, tmp_path):
    env, row, state, bodies, rounds, repairs = long_turn(installation, tmp_path)

    class LostNote(LocalTransport):
        def post(self, path, body):
            reply = super().post(path, body)
            if path.endswith("/compact"):
                raise WorkerError("fixture loses the retained compaction note receipt")
            return reply

    with pytest.raises(WorkerError):
        run_once(LostNote(env), state)
    with env["store"].transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM work_results").fetchone()[0] == 0
    paid_before = len(bodies)
    result = run_once(LocalTransport(env), state)
    assert result["result"]["outcome"] == "result" and len(rounds) == 36
    assert len(bodies) > paid_before
    with env["store"].transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM work_provider_calls").fetchone()[0] == len(bodies)
        assert db.execute("SELECT COUNT(*) FROM work_tool_operations").fetchone()[0] == 35
        assert db.execute("SELECT COUNT(*) FROM work_results WHERE job_id=?", (row["id"],)).fetchone()[0] == 1


def test_malformed_confirmed_compaction_repairs_without_an_implicit_attempt_limit(installation, tmp_path):
    env, _, state, _, rounds, repairs = long_turn(installation, tmp_path, malformed=4)
    result = run_once(LocalTransport(env), state)
    assert result["result"]["outcome"] == "result" and len(rounds) == 36 and len(repairs) >= 5


def test_model_info_is_read_only_and_checks_lease_and_grants(installation):
    env, row, _ = prepared(installation)
    response = worker_post(env, f"jobs/{row['id']}/model-info", lease(row) | {"model": "test/one"})
    assert response.status_code == 200 and response.json()["context_length"] == 200000, response.text
    assert worker_post(env, f"jobs/{row['id']}/model-info", lease(row) | {"model": "not/granted"}).status_code == 403
    assert (
        worker_post(env, f"jobs/{row['id']}/model-info", lease(row) | {"model": "test/one", "fence": row["fence"] + 1}).status_code == 409
    )
    with env["store"].transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM work_effects").fetchone()[0] == 0
