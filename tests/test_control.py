import json

import httpx
import pytest

from reasoning_ledger.control import ControlError, ReasoningControlClient
from reasoning_ledger.control_mcp import build_server


def test_control_client_starts_and_waits_for_a_durable_turn():
    operation_id = "op_" + "a" * 32
    reads = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal reads
        path = request.url.path
        if path.endswith("/messages"):
            body = json.loads(request.content)
            assert body["model"] == "stealth/ox-alpha"
            assert body["model_provider"] == "openrouter"
            assert body["required_tool_calls"] == {"exact_report": 1}
            assert body["timeout_seconds"] == 1200
            assert body["max_output_tokens"] == 12000
            assert body["max_tool_calls"] == 2
            return httpx.Response(200, json={"started": True, "operation_id": operation_id})
        if path.endswith(f"/operations/{operation_id}"):
            reads += 1
            return httpx.Response(
                200,
                json={
                    "operation_id": operation_id,
                    "status": "running" if reads == 1 else "completed",
                },
            )
        if path.endswith("/history"):
            return httpx.Response(
                200,
                json={
                    "messages": [
                        {
                            "role": "assistant",
                            "text": "ok",
                            "operation_id": operation_id,
                        }
                    ],
                    "pending_approvals": [],
                    "turn_active": reads == 1,
                },
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    with ReasoningControlClient(
        transport=httpx.MockTransport(handler),
    ) as client:
        started = client.turn_start(
            project_id="p",
            chat_id="c",
            message="work",
            model="stealth/ox-alpha",
            model_provider="openrouter",
            required_tool_calls={"exact_report": 1},
            timeout_seconds=1200,
            max_output_tokens=12000,
            max_tool_calls=2,
        )
        assert started["operation_id"] == operation_id
        finished = client.turn_wait(
            project_id="p",
            chat_id="c",
            operation_id=operation_id,
            timeout_seconds=1,
            poll_seconds=0.001,
        )

    assert finished["operation"]["status"] == "completed"
    assert [item["status"] for item in finished["checkpoints"]] == [
        "running",
        "completed",
    ]
    assert finished["history"]["messages"][0]["text"] == "ok"


def test_control_client_returns_stable_error_codes():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"detail": "another chat turn is already running"})

    with ReasoningControlClient(transport=httpx.MockTransport(handler)) as client, pytest.raises(ControlError) as caught:
        client.project_create(name="test")

    assert caught.value.payload() == {
        "code": "busy",
        "detail": "another chat turn is already running",
        "http_status": 409,
    }


def test_control_client_manages_exact_workflow_revisions():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        requests.append((request.method, request.url.path, dict(request.url.params), body))
        if request.method == "GET" and request.url.path.endswith("/agent/prompts"):
            return httpx.Response(200, json={"prompts": []})
        if request.method == "GET" and "/agent/prompts/" in request.url.path:
            return httpx.Response(
                200,
                json={"prompt_id": "role", "revision_id": "prmrev-1"},
            )
        if request.method == "POST" and request.url.path.endswith("/agent/prompts"):
            return httpx.Response(
                200,
                json={"prompt_id": "role", "revision_id": "prmrev-1"},
            )
        if request.method == "POST" and request.url.path.endswith("/revisions"):
            return httpx.Response(
                200,
                json={"prompt_id": "role", "revision_id": "prmrev-2"},
            )
        if request.method == "GET" and request.url.path.endswith("/workflows"):
            return httpx.Response(200, json={"workflows": []})
        if request.method == "POST" and request.url.path.endswith("/workflows"):
            return httpx.Response(200, json={"workflow_id": "wf", "revision_id": "rev-1"})
        if request.method == "PUT":
            return httpx.Response(200, json={"workflow_id": "wf", "revision_id": "rev-2"})
        if request.url.path.endswith("/preflight"):
            return httpx.Response(200, json={"ready": True})
        if request.method == "POST" and request.url.path.endswith("/runs"):
            return httpx.Response(200, json={"run_id": "wfrun-1", "status": "pending"})
        return httpx.Response(200, json={"workflow_id": "wf", "revision_id": "rev-2"})

    graph = {"workflow_id": "wf", "name": "Workflow", "nodes": [], "edges": []}
    with ReasoningControlClient(transport=httpx.MockTransport(handler)) as client:
        assert client.workflow_list(project_id="p", include_archived=True) == {
            "workflows": []
        }
        assert client.prompt_list(project_id="p") == {"prompts": []}
        assert client.prompt_definition(
            project_id="p", prompt_id="role", revision_id="prmrev-1"
        )["revision_id"] == "prmrev-1"
        assert client.prompt_create(
            project_id="p",
            name="Research role",
            kind="system",
            template="Reason carefully.",
            description="A role prompt.",
            variable_schema={"type": "object"},
        )["revision_id"] == "prmrev-1"
        assert client.prompt_revise(
            project_id="p",
            prompt_id="role",
            template="Reason very carefully.",
            parent_revision_id="prmrev-1",
        )["revision_id"] == "prmrev-2"
        assert client.workflow_definition(
            project_id="p", workflow_id="wf", revision_id="rev-2"
        )["revision_id"] == "rev-2"
        assert client.workflow_create(
            project_id="p", name="Workflow", template_id="compare-models"
        )["revision_id"] == "rev-1"
        assert client.workflow_update(
            project_id="p",
            workflow_id="wf",
            graph=graph,
            expected_revision_id="rev-1",
        )["revision_id"] == "rev-2"
        assert client.workflow_preflight(
            project_id="p", workflow_id="wf", refresh=True
        )["ready"] is True
        assert client.workflow_start(
            project_id="p",
            workflow_id="wf",
            expected_revision_id="rev-2",
            iterations=4,
            variables={"question": "Q"},
        )["run_id"] == "wfrun-1"

    assert requests == [
        ("GET", "/api/projects/p/workflows", {"include_archived": "true"}, None),
        ("GET", "/api/projects/p/agent/prompts", {}, None),
        (
            "GET",
            "/api/projects/p/agent/prompts/role",
            {"revision_id": "prmrev-1"},
            None,
        ),
        (
            "POST",
            "/api/projects/p/agent/prompts",
            {},
            {
                "name": "Research role",
                "kind": "system",
                "template": "Reason carefully.",
                "description": "A role prompt.",
                "variable_schema": {"type": "object"},
            },
        ),
        (
            "POST",
            "/api/projects/p/agent/prompts/role/revisions",
            {},
            {
                "template": "Reason very carefully.",
                "parent_revision_id": "prmrev-1",
                "name": None,
                "kind": None,
                "description": None,
                "variable_schema": None,
            },
        ),
        ("GET", "/api/projects/p/workflows/wf", {"revision_id": "rev-2"}, None),
        (
            "POST",
            "/api/projects/p/workflows",
            {},
            {"name": "Workflow", "template_id": "compare-models"},
        ),
        (
            "PUT",
            "/api/projects/p/workflows/wf",
            {},
            {"graph": graph, "expected_revision_id": "rev-1"},
        ),
        (
            "GET",
            "/api/projects/p/workflows/wf/preflight",
            {"refresh": "true"},
            None,
        ),
        (
            "POST",
            "/api/projects/p/workflows/wf/runs",
            {},
            {
                "iterations": 4,
                "variables": {"question": "Q"},
                "initial_prompt": "",
                "expected_revision_id": "rev-2",
            },
        ),
    ]


def test_control_mcp_builds_with_the_documented_server_name():
    server = build_server("http://127.0.0.1:9119")
    assert server.name == "reasoning-control"
