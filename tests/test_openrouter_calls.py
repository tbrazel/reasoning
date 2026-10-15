import json
import time

from fastapi.testclient import TestClient

from reasoning_ledger.agents import OpenRouterCallStore, OpenRouterClient
from reasoning_ledger.webapp import build_app


def _call(
    *,
    run_id: str,
    started_at: str,
    cost: float | str | None,
    status: str = "completed",
):
    usage = {
        "prompt_tokens": 100,
        "completion_tokens": 25,
        "total_tokens": 125,
        "prompt_tokens_details": {"cached_tokens": 40, "cache_write_tokens": 5},
        "completion_tokens_details": {"reasoning_tokens": 10},
        "cost_details": {"upstream_inference_cost": 0.001},
    }
    if cost is not None:
        usage["cost"] = cost
    return {
        "run_id": run_id,
        "sequence": 1,
        "status": status,
        "started_at": started_at,
        "completed_at": started_at,
        "duration_ms": 42.5,
        "requested_model": "openrouter/auto",
        "resolved_model": "example/model",
        "upstream_provider": "Example Provider",
        "response_id": f"gen_{run_id}",
        "finish_reason": "stop",
        "usage": usage,
    }


def test_compaction_call_purpose_survives_accounting_store_reload(tmp_path):
    root = tmp_path / "calls"
    store = OpenRouterCallStore(root)
    store.record("project-a", {**_call(run_id="summary", started_at="2026-08-21T11:58:00.000+00:00", cost=0.001), "purpose": "compaction"})
    summary = OpenRouterCallStore(root).list()["calls"][0]
    assert summary["purpose"] == "compaction" and summary["cost_usd"] == 0.001


def test_openrouter_call_store_is_durable_prompt_free_and_aggregated(tmp_path):
    root = tmp_path / "calls"
    store = OpenRouterCallStore(root)
    latest = store.record(
        "project-a",
        _call(
            run_id="run_latest",
            started_at="2026-08-21T12:01:00.000+00:00",
            cost="0.0025",
        ),
    )
    store.record(
        "project-a",
        _call(
            run_id="run_earlier",
            started_at="2026-08-21T12:00:00.000+00:00",
            cost=0.0015,
        ),
    )
    store.record(
        "project-b",
        _call(
            run_id="run_failed",
            started_at="2026-08-21T11:59:00.000+00:00",
            cost=None,
            status="failed",
        ),
    )

    assert latest["cost_usd"] == 0.0025
    assert latest["reasoning_tokens"] == 10
    assert latest["cached_tokens"] == 40
    assert "messages" not in latest

    payload = OpenRouterCallStore(root).list(limit=2)
    assert payload["total"] == 3
    assert [call["run_id"] for call in payload["calls"]] == [
        "run_latest",
        "run_earlier",
    ]
    assert payload["summary"] == {
        "calls": 3,
        "completed_calls": 2,
        "failed_calls": 1,
        "priced_calls": 2,
        "unpriced_calls": 1,
        "cost_usd": 0.004,
        "prompt_tokens": 300,
        "completion_tokens": 75,
        "total_tokens": 375,
        "first_call_at": "2026-08-21T11:59:00.000+00:00",
        "last_call_at": "2026-08-21T12:01:00.000+00:00",
    }
    project_a = next(item for item in payload["projects"] if item["project_id"] == "project-a")
    assert project_a["calls"] == 2
    assert project_a["cost_usd"] == 0.004
    assert OpenRouterCallStore(root).list(limit=1, offset=1)["calls"][0]["run_id"] == "run_earlier"
    filtered = OpenRouterCallStore(root).list(project_id="project-b")
    assert filtered["total"] == 1
    assert filtered["summary"]["failed_calls"] == 1


def test_openrouter_call_store_preserves_rate_limit_retry_evidence(tmp_path):
    record = OpenRouterCallStore(tmp_path / "calls").record(
        "project-a",
        {
            **_call(
                run_id="run_rate_limited",
                started_at="2026-08-21T12:02:00.000+00:00",
                cost=None,
                status="failed",
            ),
            "retryable": True,
            "retry_after_seconds": 2,
        },
    )

    assert record["retryable"] is True
    assert record["retry_after_seconds"] == 2


def test_openrouter_call_api_adds_project_names(tmp_path, monkeypatch):
    workbench = tmp_path / "workbench"
    monkeypatch.setenv("REASONING_WORKBENCH_ROOT", str(workbench))
    client = TestClient(build_app())
    project_id = client.post("/api/projects", json={"name": "Arithmetic"}).json()["id"]
    OpenRouterCallStore(workbench / "usage" / "openrouter-calls").record(
        project_id,
        _call(
            run_id="run_api",
            started_at="2026-08-21T12:00:00.000+00:00",
            cost=0.003,
        ),
    )

    response = client.get("/api/openrouter-calls")
    assert response.status_code == 200
    payload = response.json()
    assert payload["scope"] == "reasoning-native"
    assert payload["calls"][0]["project_name"] == "Arithmetic"
    assert payload["projects"][0]["project_name"] == "Arithmetic"
    assert client.get("/api/openrouter-calls?limit=0").status_code == 422


def test_research_chat_records_openrouter_cost_against_its_project(tmp_path, monkeypatch):
    monkeypatch.setenv("REASONING_WORKBENCH_ROOT", str(tmp_path / "workbench"))

    def complete(_self, payload, *, timeout):
        assert payload["model"] == "example/model"
        assert timeout > 0
        return {
            "id": "gen_chat",
            "model": "example/model-v2",
            "provider": "Example Provider",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "Recorded."},
                }
            ],
            "usage": {
                "prompt_tokens": 80,
                "completion_tokens": 4,
                "total_tokens": 84,
                "cost": 0.0008,
            },
        }

    monkeypatch.setattr(OpenRouterClient, "complete", complete)
    client = TestClient(build_app())
    project_id = client.post("/api/projects", json={"name": "Topology"}).json()["id"]
    chat_id = client.post(f"/api/projects/{project_id}/chats", json={}).json()["id"]
    base = f"/api/projects/{project_id}/chats/{chat_id}"
    assert (
        client.post(
            f"{base}/messages",
            json={
                "message": "Check the example.",
                "executor_id": "reasoning-native",
                "model": "example/model",
            },
        ).status_code
        == 200
    )
    deadline = time.time() + 2
    while time.time() < deadline:
        history = client.get(f"{base}/history?tail=0").json()
        if not history["turn_active"]:
            break
        time.sleep(0.01)

    response = client.get("/api/openrouter-calls", params={"project_id": project_id})
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["cost_usd"] == 0.0008
    assert payload["calls"][0]["project_id"] == project_id
    assert payload["calls"][0]["response_id"] == "gen_chat"
    assert payload["calls"][0]["resolved_model"] == "example/model-v2"


def test_native_research_chat_resumes_history_and_exposes_reasoning(tmp_path, monkeypatch):
    workbench = tmp_path / "workbench"
    monkeypatch.setenv("REASONING_WORKBENCH_ROOT", str(workbench))
    payloads = []

    def complete(_self, payload, *, timeout):
        payloads.append(payload)
        index = len(payloads)
        if index == 2:
            visible = [(message.get("role"), message.get("content")) for message in payload["messages"] if message.get("role") != "system"]
            assert visible == [
                ("user", "First question"),
                ("assistant", "First answer"),
                ("user", "Second question"),
            ]
        return {
            "id": f"gen_{index}",
            "model": "example/model",
            "provider": "Example Provider",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "reasoning": f"Reasoning {index}",
                        "content": "First answer" if index == 1 else "Second answer",
                    },
                }
            ],
            "usage": {
                "prompt_tokens": 10 * index,
                "completion_tokens": index,
                "total_tokens": 11 * index,
            },
        }

    monkeypatch.setattr(OpenRouterClient, "complete", complete)
    client = TestClient(build_app())
    project_id = client.post("/api/projects", json={"name": "Persistent"}).json()["id"]
    chat_id = client.post(f"/api/projects/{project_id}/chats", json={}).json()["id"]
    base = f"/api/projects/{project_id}/chats/{chat_id}"

    for question in ("First question", "Second question"):
        response = client.post(
            f"{base}/messages",
            json={
                "message": question,
                "executor_id": "reasoning-native",
                "model": "example/model",
                "effort": "high",
            },
        )
        assert response.status_code == 200, response.text
        deadline = time.time() + 2
        while time.time() < deadline:
            history = client.get(f"{base}/history?tail=0").json()
            if not history["turn_active"]:
                break
            time.sleep(0.01)
        assert history["turn_active"] is False

    roles = [message["role"] for message in history["messages"]]
    assert roles == [
        "user",
        "reasoning",
        "assistant",
        "user",
        "reasoning",
        "assistant",
    ]
    assert history["usage"]["total"] == {
        "inputTokens": 30,
        "outputTokens": 3,
        "totalTokens": 33,
    }
    checkpoint = workbench / "projects" / project_id / "chats" / chat_id / "reasoning-native-session.json"
    state = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert state["checkpoint"]["checkpoint_id"].startswith("chk_")
    assert [message["role"] for message in state["history"]] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]

    options = client.get(f"{base}/execution-options").json()
    assert options["session_binding"] == {
        "harness_id": "reasoning-native",
        "provider": "openrouter",
        "target_id": "project-tools",
        "model": "example/model",
    }
    assert options["default"]["model"] == "example/model"
    incompatible_preflight = client.post(
        f"{base}/execution-preflight",
        json={
            **options["default"],
            "model": "other/model",
        },
    ).json()
    assert "session_model" in {issue["code"] for issue in incompatible_preflight["issues"]}

    before = len(history["messages"])
    incompatible = client.post(
        f"{base}/messages",
        json={
            "message": "Do not record this",
            "executor_id": "reasoning-native",
            "model": "other/model",
        },
    )
    assert incompatible.status_code == 422
    assert "pinned model" in incompatible.json()["detail"]
    assert len(client.get(f"{base}/history?tail=0").json()["messages"]) == before


def test_native_research_chat_enforces_required_artifact_write(tmp_path, monkeypatch):
    workbench = tmp_path / "workbench"
    monkeypatch.setenv("REASONING_WORKBENCH_ROOT", str(workbench))
    payloads = []

    def complete(_self, payload, *, timeout):
        payloads.append(payload)
        assert 0 < timeout <= 180
        assert payload["max_tokens"] <= 12000
        if len(payloads) == 1:
            assert payload["tool_choice"] == {
                "type": "function",
                "function": {"name": "artifact_write"},
            }
            tool_names = {tool["function"]["name"] for tool in payload["tools"]}
            assert tool_names == {"artifact_write"}
            return {
                "id": "gen_premature",
                "model": "stealth/ox-alpha",
                "provider": "Stealth",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": "Claimed success without the report.",
                        },
                    }
                ],
                "usage": {},
            }
        if len(payloads) == 2:
            assert payload["tool_choice"] == {
                "type": "function",
                "function": {"name": "artifact_write"},
            }
            assert [tool["function"]["name"] for tool in payload["tools"]] == ["artifact_write"]
            return {
                "id": "gen_report",
                "model": "stealth/ox-alpha",
                "provider": "Stealth",
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "report_1",
                                    "type": "function",
                                    "function": {
                                        "name": "artifact_write",
                                        "arguments": json.dumps(
                                            {
                                                "path": "research/native-contract.md",
                                                "content": "NATIVE_REPORT_COMPLETE\n",
                                            }
                                        ),
                                    },
                                }
                            ],
                        },
                    }
                ],
                "usage": {},
            }
        return {
            "id": "gen_verified",
            "model": "stealth/ox-alpha",
            "provider": "Stealth",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": "Verified after the required report.",
                    },
                }
            ],
            "usage": {},
        }

    monkeypatch.setattr(OpenRouterClient, "complete", complete)
    client = TestClient(build_app())
    project_id = client.post("/api/projects", json={"name": "Topology"}).json()["id"]
    chat_id = client.post(f"/api/projects/{project_id}/chats", json={}).json()["id"]
    base = f"/api/projects/{project_id}/chats/{chat_id}"

    unsupported = client.post(
        f"{base}/messages",
        json={
            "message": "Do not run.",
            "executor_id": "hermes",
            "required_tool_calls": {"artifact_write": 1},
        },
    )
    assert unsupported.status_code == 422

    response = client.post(
        f"{base}/messages",
        json={
            "message": "Write the exact report.",
            "executor_id": "reasoning-native",
            "model": "stealth/ox-alpha",
            "timeout_seconds": 180,
            "max_output_tokens": 12000,
            "max_tool_calls": 1,
            "required_tool_calls": {"artifact_write": 1},
        },
    )
    assert response.status_code == 200
    deadline = time.time() + 8
    while time.time() < deadline:
        history = client.get(f"{base}/history?tail=0").json()
        if not history["turn_active"]:
            break
        time.sleep(0.01)

    assert history["turn_active"] is False
    assert history["messages"][-1]["text"] == ("Verified after the required report.")
    assert len(payloads) == 3
    assert "tools" not in payloads[2]
    assert "tool_choice" not in payloads[2]
    assert any("does not replace your semantic analysis" in message.get("content", "") for message in payloads[2]["messages"])
    artifact = workbench / "projects" / project_id / "artifacts" / "research" / "native-contract.md"
    assert artifact.read_text(encoding="utf-8").endswith("NATIVE_REPORT_COMPLETE\n")


def test_native_research_budget_overrides_are_bounded(tmp_path, monkeypatch):
    monkeypatch.setenv("REASONING_WORKBENCH_ROOT", str(tmp_path / "workbench"))
    client = TestClient(build_app())
    project_id = client.post("/api/projects", json={"name": "Budgets"}).json()["id"]
    chat_id = client.post(f"/api/projects/{project_id}/chats", json={}).json()["id"]
    endpoint = f"/api/projects/{project_id}/chats/{chat_id}/messages"

    for field, value in (
        ("timeout_seconds", 1801),
        ("max_output_tokens", 128_001),
        ("max_tool_calls", 129),
    ):
        response = client.post(
            endpoint,
            json={
                "message": "Do not run.",
                "executor_id": "reasoning-native",
                "model": "stealth/ox-alpha",
                field: value,
            },
        )
        assert response.status_code == 422

    unsupported = client.post(
        endpoint,
        json={
            "message": "Do not run.",
            "executor_id": "hermes",
            "timeout_seconds": 120,
        },
    )
    assert unsupported.status_code == 422

    insufficient = client.post(
        endpoint,
        json={
            "message": "Do not run.",
            "executor_id": "reasoning-native",
            "model": "stealth/ox-alpha",
            "max_tool_calls": 1,
            "required_tool_calls": {
                "artifact_read": 1,
                "artifact_write": 1,
            },
        },
    )
    assert insufficient.status_code == 422
    assert "cannot be smaller" in insufficient.json()["detail"]
