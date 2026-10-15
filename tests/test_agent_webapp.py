import json
import shlex
import stat
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from reasoning_ledger import LedgerStore, service
from reasoning_ledger.agents import (
    AgentRunResult,
    BibliographyStore,
    ExecutorCapabilities,
    PromptFidelity,
    model_pricing,
)
from reasoning_ledger.connection_state import ConnectionStateStore
from reasoning_ledger.harnesses import (
    ExecutionTargetCapabilities,
    ExecutionTargetDeployment,
    ExecutionTargetKind,
    HarnessCapabilities,
    HarnessDeployment,
    HarnessDeploymentRegistry,
    HarnessEvent,
    HarnessEventKind,
    HarnessSession,
    HarnessTransport,
    HarnessTurnResult,
    InferenceRoute,
    InferenceRouteDeployment,
    NetworkAccess,
    SessionMode,
    TargetIsolation,
    WorkspaceAccess,
)
from reasoning_ledger.webapp import _workflow_ledger_command, build_app


def test_failed_codex_test_warns_without_disabling_model_recovery(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    fake = Path(__file__).parent / "fake_appserver.py"
    monkeypatch.setenv("REASONING_CODEX_COMMAND", f"{sys.executable} {fake}")
    client = _client(tmp_path, monkeypatch)
    client.put("/api/settings/api-keys/openrouter", json={"api_key": "sk-or-native-codex-secret"})
    ConnectionStateStore(tmp_path / "workbench" / "settings" / "connection-state.json").update(
        "codex",
        validation_status="invalid",
        validated_at="2026-08-22T04:17:58.937+00:00",
        validation_detail="The selected provider is no longer configured",
        tested_model="provider/old-model",
    )
    status = next(
        item for item in client.get("/api/agent/connections").json()["connections"] if item["executor_id"] == "codex"
    )
    assert status["installed"] is True
    assert status["authenticated"] is True
    assert status["validation_status"] == "invalid"
    assert status["ready"] is True
    assert "last native Codex test failed" in status["readiness"]


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("REASONING_WORKBENCH_ROOT", str(tmp_path / "workbench"))
    return TestClient(build_app())


def test_chat_execution_options_separate_harness_route_target_and_reasoning(
    tmp_path,
    monkeypatch,
):
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / ".git").mkdir()
    client = _client(tmp_path, monkeypatch)
    project_id = client.post("/api/projects", json={"name": "Controls"}).json()["id"]
    research_id = client.post(
        f"/api/projects/{project_id}/chats",
        json={"title": "Research"},
    ).json()["id"]
    research_base = f"/api/projects/{project_id}/chats/{research_id}"

    options = client.get(f"{research_base}/execution-options").json()

    assert options["mode"] == "research"
    assert {item["harness_id"] for item in options["harnesses"]} == {
        "reasoning-native",
        "codex",
        "claude-code",
        "hermes",
        "opencode",
    }
    assert options["default"] == {
        "harness_id": "reasoning-native",
        "inference_route_id": "openrouter",
        "target_id": "project-tools",
        "model": "openrouter/auto",
        "reasoning_effort": "xhigh",
    }
    assert [item["target_id"] for item in options["execution_targets"]] == ["project-tools"]
    openrouter = next(item for item in options["inference_routes"] if item["route_id"] == "openrouter")
    assert openrouter["provider"] == "openrouter"
    assert openrouter["harness_ids"] == ["reasoning-native"]
    assert options["requirements"]["workspace_access"] == "none"
    assert options["requirements"]["requires_tools"] is True
    assert options["session_binding"] is None

    preflight = client.post(
        f"{research_base}/execution-preflight",
        json=options["default"],
    ).json()
    assert {
        "compatible": preflight["compatible"],
        "issues": preflight["issues"],
    } == options["default_preflight"]
    assert preflight["selection"] == options["default"]
    assert isinstance(preflight["compatible"], bool)
    if not openrouter["ready"]:
        assert "route_unavailable" in {issue["code"] for issue in preflight["issues"]}

    rejected = client.post(
        f"{research_base}/messages",
        json={
            "message": "Do not run this",
            "harness_id": "codex",
            "inference_route_id": "openrouter",
            "target_id": "project-tools",
            "model": "openrouter/auto",
            "reasoning_effort": "xhigh",
        },
    )
    assert rejected.status_code == 422
    detail = rejected.json()["detail"]
    assert detail["message"] == "selected run configuration is not compatible"
    assert "route_harness" in {issue["code"] for issue in detail["issues"]}

    workspace = client.post(
        "/api/settings/development-workspaces",
        json={"name": "Repository", "path": str(repository)},
    ).json()["workspace"]
    development_id = client.post(
        f"/api/projects/{project_id}/chats",
        json={
            "title": "Development",
            "workspace_id": workspace["workspace_id"],
        },
    ).json()["id"]
    development_base = f"/api/projects/{project_id}/chats/{development_id}"
    development = client.get(f"{development_base}/execution-options").json()

    assert development["mode"] == "development"
    assert development["default"]["harness_id"] == "codex"
    assert development["default"]["inference_route_id"] == "codex-openai"
    assert development["default"]["target_id"] == (f"workspace.{workspace['workspace_id']}")
    target = next(item for item in development["execution_targets"] if item["target_id"] == development["default"]["target_id"])
    assert target["workspace_access"] == ["none", "read_only", "read_write"]
    assert target["supports_processes"] is True
    assert development["requirements"]["requires_approvals"] is True
    assert development["requirements"]["requires_durable_workspace"] is True


def test_chat_auto_routing_uses_live_compatibility_and_labeled_catalog_cost(
    tmp_path,
    monkeypatch,
):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    def fetch_json(url, headers, timeout):
        assert url.endswith("/models/user")
        assert headers["Authorization"] == "Bearer sk-or-auto-route"
        assert timeout == 12.0
        return {
            "data": [
                {
                    "id": "provider/cheap",
                    "architecture": {"output_modalities": ["text"]},
                    "supported_parameters": ["tools"],
                    "pricing": {"prompt": "0.000001", "completion": "0.000001"},
                },
                {
                    "id": "provider/expensive",
                    "architecture": {"output_modalities": ["text"]},
                    "supported_parameters": ["tools"],
                    "pricing": {"prompt": "0.000004", "completion": "0.000004"},
                },
            ]
        }

    monkeypatch.setattr(model_pricing, "_request_json", fetch_json)
    client = _client(tmp_path, monkeypatch)
    client.put(
        "/api/settings/api-keys/openrouter",
        json={"api_key": "sk-or-auto-route"},
    )
    project_id = client.post("/api/projects", json={"name": "Auto route"}).json()["id"]
    chat_id = client.post(
        f"/api/projects/{project_id}/chats",
        json={"title": "Choose a model"},
    ).json()["id"]
    endpoint = f"/api/projects/{project_id}/chats/{chat_id}/auto-route"

    missing_prompt = client.post(endpoint, json={"objective": "balanced", "prompt": ""})
    assert missing_prompt.status_code == 422

    response = client.post(
        endpoint,
        json={
            "objective": "balanced",
            "prompt": "Summarize this paper safely.",
            "reasoning_effort": None,
        },
    )
    assert response.status_code == 200, response.text
    decision = response.json()
    assert decision["selected"] == {
        "harness_id": "reasoning-native",
        "inference_route_id": "openrouter",
        "target_id": "project-tools",
        "model": "provider/cheap",
        "reasoning_effort": None,
        "provider": "openrouter",
    }
    assert decision["eligible_count"] == 2
    assert "Summarize this paper safely." not in decision["task_fingerprint"]
    selected = next(
        item
        for item in decision["assessments"]
        if item["selection"]["model"] == "provider/cheap"
    )
    cost = next(item for item in selected["components"] if item["name"] == "cost")
    assert cost["weight"] == 0.2
    assert cost["source"] == "catalog estimate"
    assert all(
        item["source"] == "unavailable"
        for item in selected["components"]
        if item["name"] in {"quality", "reliability", "latency"}
    )
    assert selected["evidence"]["observed_calls"] == 0
    assert selected["evidence"]["estimated_cost_usd"] > 0


def test_registered_remote_harness_runs_through_chat_and_pins_exact_binding(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("REASONING_WORKBENCH_ROOT", str(tmp_path / "workbench"))
    capability = HarnessCapabilities(
        harness_id="cloud-reference",
        display_name="Cloud reference",
        adapter_version="2026.08",
        transport=HarnessTransport.REMOTE_SERVICE,
        prompt_fidelity=PromptFidelity.WIRE_EXACT,
        session_modes=(SessionMode.FRESH, SessionMode.RESUME),
        workspace_access=(WorkspaceAccess.NONE,),
        supports_tools=True,
        model_patterns=("example/*",),
    )
    target = ExecutionTargetCapabilities(
        target_id="cloud-stateless",
        display_name="Cloud stateless",
        kind=ExecutionTargetKind.REMOTE_SANDBOX,
        isolation=TargetIsolation.CONTAINER,
        workspace_access=(WorkspaceAccess.NONE,),
        network_access=(NetworkAccess.DENIED,),
    )
    contexts = []
    requests = []

    class Adapter:
        capabilities = capability

        def __init__(self, context):
            self.context = context

        def create_session(self, spec):
            return HarnessSession(
                session_id=self.context.session_id,
                capabilities=capability,
                target=spec.target,
                model=spec.model,
                model_provider=spec.model_provider,
                adapter_session_ref="opaque-remote-ref",
            )

        def run_turn(self, session, request, emit=None):
            requests.append(request)
            at = datetime.now(UTC).isoformat()
            for event in (
                HarnessEvent(
                    session.session_id,
                    request.turn_id,
                    1,
                    HarnessEventKind.TURN_STARTED,
                    at,
                ),
                HarnessEvent(
                    session.session_id,
                    request.turn_id,
                    2,
                    HarnessEventKind.MESSAGE_COMPLETED,
                    at,
                    {"text": "remote answer"},
                ),
                HarnessEvent(
                    session.session_id,
                    request.turn_id,
                    3,
                    HarnessEventKind.TURN_COMPLETED,
                    at,
                ),
            ):
                if emit is not None:
                    emit(event)
            return HarnessTurnResult(
                session_id=session.session_id,
                turn_id=request.turn_id,
                status="completed",
                output_text="remote answer",
            )

        def interrupt(self, session, turn_id):
            return False

        def close_session(self, session):
            return None

    def factory(context):
        contexts.append(context)
        return Adapter(context)

    registry = HarnessDeploymentRegistry(
        harnesses=(
            HarnessDeployment(
                capability,
                factory,
                chat_modes=("research",),
            ),
        ),
        routes=(
            InferenceRouteDeployment(
                InferenceRoute(
                    route_id="cloud-example",
                    display_name="Cloud example",
                    provider="example",
                    harness_ids=("cloud-reference",),
                    model_patterns=("example/*",),
                    model_options=("example/deep",),
                ),
                chat_modes=("research",),
            ),
        ),
        targets=(
            ExecutionTargetDeployment(
                target,
                chat_modes=("research",),
            ),
        ),
    )
    client = TestClient(build_app(harness_deployments=registry))
    project_id = client.post("/api/projects", json={"name": "Remote"}).json()["id"]
    chat_id = client.post(
        f"/api/projects/{project_id}/chats",
        json={"title": "Cloud"},
    ).json()["id"]
    base = f"/api/projects/{project_id}/chats/{chat_id}"

    options = client.get(f"{base}/execution-options").json()
    assert "cloud-reference" in {item["harness_id"] for item in options["harnesses"]}
    assert "cloud-stateless" in {
        item["target_id"] for item in options["execution_targets"]
    }

    response = client.post(
        f"{base}/messages",
        json={
            "message": "Run remotely",
            "harness_id": "cloud-reference",
            "inference_route_id": "cloud-example",
            "target_id": "cloud-stateless",
            "model": "example/deep",
        },
    )
    assert response.status_code == 200
    operation_id = response.json()["operation_id"]
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        operation = client.get(f"{base}/operations/{operation_id}").json()
        if operation["status"] != "running":
            break
        threading.Event().wait(0.01)

    assert operation["status"] == "completed"
    assert operation["harness"]["harness_id"] == "cloud-reference"
    assert operation["target"]["target_id"] == "cloud-stateless"
    assert operation["inference_route_id"] == "cloud-example"
    assert len(operation["task_fingerprint"]) == 64
    assert operation["duration_seconds"] >= 0
    assert operation["reasoning_effort"] is None
    assert "Run remotely" not in json.dumps(operation)
    history = client.get(f"{base}/history", params={"full": True}).json()
    assert any(
        message.get("role") == "assistant"
        and message.get("text") == "remote answer"
        for message in history["messages"]
    )
    rebound = client.get(f"{base}/execution-options").json()
    assert rebound["session_binding"] == {
        "harness_id": "cloud-reference",
        "provider": "example",
        "target_id": "cloud-stateless",
        "model": "example/deep",
    }
    assert rebound["default"]["harness_id"] == "cloud-reference"
    assert contexts[0].session_id == f"remote.{chat_id}"
    assert requests[0].prompt_hashes == {}
    assert requests[0].metadata == {"chat_mode": "research"}


def test_workflow_mcp_prefers_the_server_virtualenv_over_path(tmp_path, monkeypatch):
    runtime_bin = tmp_path / "runtime" / "bin"
    runtime_bin.mkdir(parents=True)
    python = runtime_bin / "python"
    ledger = runtime_bin / "reasoning-ledger"
    python.touch()
    ledger.touch()
    monkeypatch.setattr(sys, "executable", str(python))
    monkeypatch.setattr(
        "reasoning_ledger.webapp.shutil.which",
        lambda _name: "/home/owner/.local/bin/reasoning-ledger",
    )

    assert _workflow_ledger_command() == str(ledger)

    monkeypatch.setenv("REASONING_LEDGER_COMMAND", "/explicit/reasoning-ledger")
    assert _workflow_ledger_command() == "/explicit/reasoning-ledger"


def test_agentic_registries_preview_workflows_and_runs(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    examples = client.get("/api/workflow-templates").json()["workflows"]
    assert {workflow["name"] for workflow in examples} == {
        "Benchmark · Paper moderation",
        "Compare models",
        "Example · Advisor–student research year",
        "Example · Adversarial conjecture review",
        "Example · Small scientific lab",
        "Generate a dataset",
    }
    comparison = client.get("/api/workflow-templates/compare-models").json()
    assert comparison["workflow_type"] == "model_comparison"
    assert comparison["execution_policy"] == {
        "max_concurrency": 4,
        "failure_policy": "continue",
    }
    assert [node["name"] for node in comparison["nodes"]] == [
        "Question",
        "Compared model",
        "Responses",
    ]
    assert [variant["name"] for variant in comparison["variants"]] == ["Model A", "Model B"]
    moderation = client.get("/api/workflow-templates/benchmark-paper-moderation").json()
    assert moderation["workflow_type"] == "model_benchmark"
    assert moderation["execution_policy"] == {
        "max_concurrency": 8,
        "failure_policy": "continue",
    }
    assert moderation["benchmark"]["kind"] == "paper_moderation_v1"
    assert len(moderation["benchmark"]["cases"]) == 6
    assert "prompt_injection" in moderation["benchmark"]["cases"][-1]["expected"]["flags"]
    assert all(not node.get("config", {}).get("tools") for node in moderation["nodes"])
    assert all(not node.get("config", {}).get("sandbox", {}).get("network") for node in moderation["nodes"])
    generator = client.get("/api/workflow-templates/generate-a-dataset").json()
    assert generator["presentation"] == {"recipe": "dataset_generator"}
    generator_agent = next(node for node in generator["nodes"] if node["kind"] == "agent")
    assert generator_agent["config"]["output_schema"]["required"] == ["name", "description", "cases"]
    advisor_example = client.get("/api/workflow-templates/example-advisor-student-research-year").json()
    assert advisor_example["iterations"] == 50
    assert [node["name"] for node in advisor_example["nodes"]] == [
        "Research question",
        "Student",
        "Advisor",
        "Next-week plan",
    ]
    project = client.post("/api/projects", json={"name": "Agents"}).json()["id"]
    base = f"/api/projects/{project}"

    executors = client.get(f"{base}/agent/executors").json()["executors"]
    assert {item["executor_id"] for item in executors} == {
        "reasoning-native",
        "codex",
        "claude-code",
        "hermes",
        "opencode",
    }
    assert next(item for item in executors if item["executor_id"] == "reasoning-native")["model_options"] == [
        "openrouter/auto",
        "openai/gpt-oss-20b",
        "openai/gpt-oss-120b",
        "qwen/qwen3-coder",
    ]
    codex_executor = next(item for item in executors if item["executor_id"] == "codex")
    assert codex_executor["model_options"] == ["gpt-6-astra", "gpt-5.6-sol"]
    assert codex_executor["supports_mcp"] is True
    tools = client.get(f"{base}/agent/tools").json()["tools"]
    assert any(tool["name"] == "ledger_status" for tool in tools)
    oeis = next(tool for tool in tools if tool["name"] == "oeis_search")
    assert oeis["risk"] == "external"
    assert oeis["capabilities"] == ["science:query"]
    assert {
        "arithmetic-geometry-research",
        "conjecture-audit",
        "formal-proof-audit",
        "literature-research",
        "sequence-research",
    }.issubset({skill["skill_id"] for skill in client.get("/api/agent/skills").json()["skills"]})
    literature = client.get("/api/agent/skills/literature-research").json()
    assert "arxiv_source_read" in literature["instructions"]
    tool_names = {tool["name"] for tool in tools}
    assert {
        "arxiv_lookup",
        "arxiv_read",
        "arxiv_search",
        "arxiv_source_list",
        "arxiv_source_read",
        "bibliography_add",
        "bibliography_export",
        "bibliography_get",
        "bibliography_resource_read",
        "bibliography_search",
        "bibliography_summary_add",
        "constant_lookup",
        "crossref_lookup",
        "crossref_search",
        "lean_check",
        "lean_environment",
        "lmfdb_elliptic_curves_q",
        "lmfdb_number_fields",
        "scientific_calculate",
        "unit_convert",
        "prompt_create",
        "prompt_list",
        "prompt_read",
        "prompt_revise",
        "dataset_create",
        "dataset_list",
        "dataset_read",
        "model_catalog",
        "workflow_create",
        "workflow_list",
        "workflow_preflight",
        "workflow_read",
        "workflow_start",
        "workflow_update",
    }.issubset(tool_names)

    system = client.post(
        f"{base}/agent/prompts",
        json={
            "name": "Student",
            "kind": "system",
            "template": "You are the student in week {{week}}.",
        },
    ).json()
    assert (
        client.get(
            f"{base}/agent/prompts/{system['prompt_id']}",
            params={"revision_id": system["revision_id"]},
        ).json()["template"]
        == "You are the student in week {{week}}."
    )
    user = client.post(
        f"{base}/agent/prompts",
        json={
            "name": "Research task",
            "kind": "user",
            "template": "Study {{question}}.",
        },
    ).json()
    skill = client.post(
        f"{base}/agent/skills",
        json={
            "name": "ledger-reader",
            "description": "Read ledger state before advising.",
            "instructions": "Inspect dependencies before making a claim.",
            "metadata": {
                "reasoning": {
                    "tools": ["ledger_status"],
                    "capabilities": ["ledger:read"],
                }
            },
        },
    ).json()

    workflow = client.post(f"{base}/workflows", json={"name": "Weekly lab"}).json()
    config = {
        "executor_id": "reasoning-native",
        "model": "openai/gpt-oss-20b",
        "system_prompt": {
            "prompt_id": system["prompt_id"],
            "revision_id": system["revision_id"],
        },
        "user_prompt": {
            "prompt_id": user["prompt_id"],
            "revision_id": user["revision_id"],
        },
        "skills": [{"skill_id": skill["skill_id"], "revision_id": skill["revision_id"]}],
        "tools": ["ledger_status"],
        "context_policy": {"mode": "predecessors", "max_tokens": 1000},
        "budget": {"max_cost_usd": 0.1, "timeout_seconds": 60},
        "sandbox": {"mode": "read_only"},
    }
    graph = {
        **workflow,
        "iterations": 50,
        "nodes": [
            {
                "id": "student",
                "kind": "agent",
                "name": "Student",
                "position": {"x": 120, "y": 90},
                "config": config,
            }
        ],
        "edges": [],
    }
    saved = client.put(
        f"{base}/workflows/{workflow['workflow_id']}",
        json={"graph": graph, "expected_revision_id": workflow["revision_id"]},
    )
    assert saved.status_code == 200
    assert saved.json()["iterations"] == 50

    # Editable definitions belong to their project. Reusable templates remain
    # workbench-wide and can seed an independent graph in another project.
    other_project = client.post("/api/projects", json={"name": "Another ledger"}).json()["id"]
    global_workflows = client.get("/api/workflow-templates").json()["workflows"]
    assert not any(item["name"] == "Weekly lab" for item in global_workflows)
    assert client.get("/api/agent/prompts").json()["prompts"]
    other_workflows = client.get(f"/api/projects/{other_project}/workflows").json()["workflows"]
    assert not any(item["workflow_id"] == workflow["workflow_id"] for item in other_workflows)
    cloned = client.post(
        f"/api/projects/{other_project}/workflows",
        json={
            "name": "My advisor year",
            "template_id": "example-advisor-student-research-year",
        },
    ).json()
    assert cloned["name"] == "My advisor year"
    assert cloned["iterations"] == 50
    assert [node["name"] for node in cloned["nodes"]] == [
        "Research question",
        "Student",
        "Advisor",
        "Next-week plan",
    ]

    preview = client.post(
        f"{base}/agent/preview",
        json={"config": config, "variables": {"week": 7, "question": "Lemma X"}},
    )
    assert preview.status_code == 200, preview.text
    rendered = preview.json()
    assert rendered["system_prompt"].startswith("You are the student in week 7.")
    assert "<reasoning-skill-catalog>" in rendered["system_prompt"]
    assert "Inspect dependencies before making a claim." not in rendered["system_prompt"]
    assert rendered["user_prompt"] == "Study Lemma X."
    assert rendered["tools"] == ["ledger_status", "skill_read"]
    assert rendered["prompt_fidelity"] == "wire_exact"
    assert rendered["warnings"] == []

    codex_batch_config = {
        **config,
        "executor_id": "codex",
        "model_provider": "openai",
        "model": "gpt-5.6-sol",
        "skills": [],
        "tools": ["artifact_read_many"],
    }
    batch_preview = client.post(
        f"{base}/agent/preview",
        json={
            "config": codex_batch_config,
            "variables": {"week": 7, "question": "Lemma X"},
        },
    )
    assert batch_preview.status_code == 200, batch_preview.text
    assert batch_preview.json()["warnings"] == [
        "MCP artifact_read_many returns a manifest when the bounded result cannot "
        "carry every body; grant artifact_read_chunk and reserve additional tool "
        "calls when complete large-artifact evidence is required."
    ]

    codex_batch_config["tools"].append("artifact_read_chunk")
    chunked_preview = client.post(
        f"{base}/agent/preview",
        json={
            "config": codex_batch_config,
            "variables": {"week": 7, "question": "Lemma X"},
        },
    )
    assert chunked_preview.status_code == 200, chunked_preview.text
    assert chunked_preview.json()["warnings"] == []

    # A graph with no agent nodes still exercises the background scheduler
    # and durable parent run state without making an external model request.
    empty = client.post(f"{base}/workflows", json={"name": "Empty smoke"}).json()
    run = client.post(
        f"{base}/workflows/{empty['workflow_id']}/runs",
        json={"iterations": 2, "variables": {}},
    ).json()
    deadline = time.time() + 2
    while time.time() < deadline:
        state = client.get(f"{base}/workflow-runs/{run['run_id']}").json()
        if state["status"] not in {"pending", "running"}:
            break
        time.sleep(0.01)
    assert state["status"] == "completed"
    assert [event["type"] for event in state["events"]].count("iteration.completed") == 2
    assert state["cost"]["total_usd"] == 0
    assert state["artifact_path"] == f"workflow-runs/{run['run_id']}.md"
    report = client.get(f"{base}/artifacts/{state['artifact_path']}")
    assert report.status_code == 200
    assert "# Empty smoke · saved run" in report.text
    assert "Status: **completed**" in report.text
    history = client.get(f"{base}/workflow-runs?workflow_id={empty['workflow_id']}").json()["runs"]
    assert [item["run_id"] for item in history] == [run["run_id"]]
    already_done = client.post(f"{base}/workflow-runs/{run['run_id']}/cancel").json()
    assert already_done["status"] == "completed"
    project_summary = next(item for item in client.get("/api/projects").json()["projects"] if item["id"] == project)
    assert {item["workflow_id"] for item in project_summary["workflows"]} == {
        workflow["workflow_id"],
        empty["workflow_id"],
    }
    assert project_summary["workflow_runs"][0]["run_id"] == run["run_id"]
    assert project_summary["workflow_runs"][0]["artifact_path"] == state["artifact_path"]

    disposable = client.post("/api/workflow-templates", json={"name": "Disposable template"}).json()
    deleted = client.delete(f"/api/workflow-templates/{disposable['workflow_id']}")
    assert deleted.status_code == 200
    assert all(item["workflow_id"] != disposable["workflow_id"] for item in client.get("/api/workflow-templates").json()["workflows"])

    deleted_example_id = "example-adversarial-conjecture-review"
    assert client.delete(f"/api/workflow-templates/{deleted_example_id}").status_code == 200
    restarted = _client(tmp_path, monkeypatch)
    template_ids = {item["workflow_id"] for item in restarted.get("/api/workflow-templates").json()["workflows"]}
    assert deleted_example_id not in template_ids
    assert not any(value.startswith(f"{deleted_example_id}-") for value in template_ids)


def test_existing_workbench_receives_new_starter_workflows(tmp_path, monkeypatch):
    workbench = tmp_path / "workbench"
    library = workbench / "agentic"
    library.mkdir(parents=True)
    (library / ".examples-seeded-v1").write_text("2026-08-06T08:51:00+00:00\n", encoding="utf-8")
    monkeypatch.setenv("REASONING_WORKBENCH_ROOT", str(workbench))

    client = TestClient(build_app())

    template_ids = {item["workflow_id"] for item in client.get("/api/workflow-templates").json()["workflows"]}
    assert "compare-models" in template_ids
    assert (library / ".examples-seeded-v4").is_file()


def test_prompt_can_be_deleted_without_erasing_its_revision(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    prompt = client.post(
        "/api/agent/prompts",
        json={"name": "Disposable", "kind": "user", "template": "Do {{task}}."},
    ).json()

    deleted = client.delete(f"/api/agent/prompts/{prompt['prompt_id']}")

    assert deleted.status_code == 200
    assert all(item["prompt_id"] != prompt["prompt_id"] for item in client.get("/api/agent/prompts").json()["prompts"])
    stored = client.post(
        f"/api/agent/prompts/{prompt['prompt_id']}/render",
        json={"revision_id": prompt["revision_id"], "variables": {"task": "X"}},
    )
    assert stored.json()["text"] == "Do X."


def test_prompt_revision_rejects_a_stale_parent(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    first = client.post(
        "/api/agent/prompts",
        json={"name": "Role", "kind": "system", "template": "Be careful."},
    ).json()
    second = client.post(
        f"/api/agent/prompts/{first['prompt_id']}/revisions",
        json={
            "template": "Be very careful.",
            "parent_revision_id": first["revision_id"],
        },
    )
    assert second.status_code == 200, second.text

    stale = client.post(
        f"/api/agent/prompts/{first['prompt_id']}/revisions",
        json={
            "template": "Overwrite the newer edit.",
            "parent_revision_id": first["revision_id"],
        },
    )
    assert stale.status_code == 409
    assert "prompt changed since it was read" in stale.json()["detail"]


def test_skill_can_be_revised_and_removed_without_erasing_pinned_packages(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    first = client.post(
        "/api/agent/skills",
        json={
            "name": "disposable-reviewer",
            "description": "Review a result.",
            "instructions": "Read the result.",
            "metadata": {"reasoning": {"tools": ["ledger_status"]}},
        },
    ).json()
    second = client.post(
        f"/api/agent/skills/{first['skill_id']}/revisions",
        json={
            "instructions": "Read the result and challenge its strongest claim.",
            "parent_revision_id": first["revision_id"],
        },
    )
    assert second.status_code == 200, second.text
    second_revision = second.json()
    revisions = client.get(f"/api/agent/skills/{first['skill_id']}/revisions").json()["revisions"]
    assert [revision["revision_id"] for revision in revisions] == [
        first["revision_id"],
        second_revision["revision_id"],
    ]

    deleted = client.delete(f"/api/agent/skills/{first['skill_id']}")
    assert deleted.status_code == 200
    assert all(item["skill_id"] != first["skill_id"] for item in client.get("/api/agent/skills").json()["skills"])
    pinned = client.get(
        f"/api/agent/skills/{first['skill_id']}",
        params={"revision_id": second_revision["revision_id"]},
    )
    assert pinned.status_code == 200
    assert "strongest claim" in pinned.json()["instructions"]


def test_api_keys_are_user_configurable_without_returning_secrets(tmp_path, monkeypatch):
    for variable in ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("REASONING_CODEX_COMMAND", str(tmp_path / "missing-codex"))
    monkeypatch.setenv("REASONING_CLAUDE_COMMAND", str(tmp_path / "missing-claude"))
    monkeypatch.setenv("REASONING_HERMES_COMMAND", str(tmp_path / "missing-hermes"))
    monkeypatch.setenv("REASONING_OPENCODE_COMMAND", str(tmp_path / "missing-opencode"))
    client = _client(tmp_path, monkeypatch)

    initial = client.get("/api/settings/api-keys").json()["providers"]
    assert not any(provider["configured"] for provider in initial)
    connections = client.get("/api/agent/connections").json()["connections"]
    assert {connection["executor_id"] for connection in connections} == {
        "codex",
        "reasoning-native",
        "claude-code",
        "hermes",
        "opencode",
    }
    assert not any(connection["ready"] for connection in connections)

    secret = "sk-or-test-super-secret"
    response = client.put("/api/settings/api-keys/openrouter", json={"api_key": secret})
    assert response.status_code == 200
    assert secret not in response.text
    status = next(provider for provider in response.json()["providers"] if provider["provider"] == "openrouter")
    assert status == {
        "provider": "openrouter",
        "label": "OpenRouter",
        "configured": True,
        "source": "user",
        "hint": "••••cret",
    }
    refreshed_connections = client.get("/api/agent/connections").json()["connections"]
    assert next(connection for connection in refreshed_connections if connection["executor_id"] == "reasoning-native")["ready"]
    assert secret not in json.dumps(refreshed_connections)

    credentials = tmp_path / "workbench" / "settings" / "api-keys.json"
    assert json.loads(credentials.read_text())["openrouter"] == secret
    assert stat.S_IMODE(credentials.stat().st_mode) == 0o600

    removed = client.delete("/api/settings/api-keys/openrouter")
    assert removed.status_code == 200
    assert not next(provider for provider in removed.json()["providers"] if provider["provider"] == "openrouter")["configured"]


def test_openrouter_key_validation_is_live_durable_and_key_specific(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    def fetch_json(url, headers, timeout):
        assert url.endswith("/models/user")
        assert headers["Authorization"] == "Bearer sk-or-valid-one"
        assert timeout == 12.0
        return {
            "data": [
                {
                    "id": "provider/model",
                    "name": "Model",
                    "supported_parameters": ["tools", "reasoning"],
                    "pricing": {"prompt": "0.000001", "completion": "0.000002"},
                }
            ]
        }

    monkeypatch.setattr(model_pricing, "_request_json", fetch_json)
    client = _client(tmp_path, monkeypatch)
    client.put("/api/settings/api-keys/openrouter", json={"api_key": "sk-or-valid-one"})

    validated = client.post("/api/agent/connections/openrouter/validate")
    assert validated.status_code == 200, validated.text
    assert validated.json()["validation_status"] == "valid"
    assert validated.json()["model_count"] == 1

    status = next(item for item in client.get("/api/agent/connections").json()["connections"] if item["executor_id"] == "reasoning-native")
    assert status["validation_status"] == "valid"
    assert "sk-or-valid-one" not in json.dumps(status)
    assert "credential_fingerprint" not in status

    # Replacing the credential invalidates the old verification even before
    # another network request is made.
    client.put("/api/settings/api-keys/openrouter", json={"api_key": "sk-or-different"})
    status = next(item for item in client.get("/api/agent/connections").json()["connections"] if item["executor_id"] == "reasoning-native")
    assert status["validation_status"] == "untested"


def test_openrouter_validation_failure_is_actionable_and_secret_free(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    def fail(*_args):
        raise model_pricing.ModelPricingError("OpenRouter rejected this credential")

    monkeypatch.setattr(model_pricing, "_request_json", fail)
    client = _client(tmp_path, monkeypatch)
    secret = "sk-or-invalid-secret"
    client.put("/api/settings/api-keys/openrouter", json={"api_key": secret})

    response = client.post("/api/agent/connections/openrouter/validate")
    assert response.status_code == 502
    assert "rejected" in response.json()["detail"]
    statuses = client.get("/api/agent/connections").json()["connections"]
    assert next(item for item in statuses if item["executor_id"] == "reasoning-native")["validation_status"] == "invalid"
    assert secret not in json.dumps(statuses)


def test_comparison_preflight_blocks_models_missing_from_live_catalog(
    tmp_path,
    monkeypatch,
):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    def fetch_json(url, headers, timeout):
        assert url.endswith("/models/user")
        assert headers["Authorization"] == "Bearer sk-or-comparison"
        assert timeout == 12.0
        return {
            "data": [
                {
                    "id": "provider/available",
                    "name": "Available",
                    "architecture": {"output_modalities": ["text"]},
                    "supported_parameters": [],
                    "pricing": {"prompt": "0.000001", "completion": "0.000002"},
                },
                {
                    "id": "thinkingmachines/inkling-small:free",
                    "name": "Inkling Small",
                    "description": ("The free Inkling Small endpoint is only available for use with agentic harnesses."),
                    "architecture": {"output_modalities": ["text"]},
                    "supported_parameters": [],
                    "pricing": {"prompt": "0", "completion": "0"},
                },
            ]
        }

    monkeypatch.setattr(model_pricing, "_request_json", fetch_json)
    client = _client(tmp_path, monkeypatch)
    client.put(
        "/api/settings/api-keys/openrouter",
        json={"api_key": "sk-or-comparison"},
    )
    project = client.post("/api/projects", json={"name": "Comparison"}).json()["id"]
    created = client.post(
        f"/api/projects/{project}/workflows",
        json={"name": "Compare", "template_id": "compare-models"},
    ).json()
    graph = client.get(f"/api/projects/{project}/workflows/{created['workflow_id']}").json()
    graph["variants"][0]["node_overrides"]["model"]["model"] = "provider/available"
    graph["variants"][1]["node_overrides"]["model"]["model"] = "provider/retired"
    draft_preflight = client.post(
        f"/api/projects/{project}/workflows/preflight?refresh=true",
        json={"graph": graph, "expected_revision_id": graph["revision_id"]},
    )
    assert draft_preflight.status_code == 200, draft_preflight.text
    assert draft_preflight.json()["plan"] == {
        "recipe": "model_comparison",
        "dataset": "Workflow iterations",
        "dataset_ref": None,
        "case_count": 1,
        "variant_count": 2,
        "agent_count": 1,
        "scheduled_calls": 2,
        "max_calls": None,
        "max_total_cost_usd": None,
        "evaluators": [],
    }
    saved = client.put(
        f"/api/projects/{project}/workflows/{created['workflow_id']}",
        json={"graph": graph, "expected_revision_id": graph["revision_id"]},
    )
    assert saved.status_code == 200, saved.text

    preflight = client.get(f"/api/projects/{project}/workflows/{created['workflow_id']}/preflight?refresh=true")
    assert preflight.status_code == 200, preflight.text
    payload = preflight.json()
    assert payload["ready"] is False
    assert payload["source"]["status"] == "live"
    assert [check["status"] for check in payload["checks"]] == [
        "available",
        "unavailable",
    ]
    assert "provider/retired" in payload["issues"][0]
    assert "retired" in payload["issues"][0]

    graph = saved.json()
    graph["variants"][1]["node_overrides"]["model"]["model"] = "thinkingmachines/inkling-small:free"
    restricted = client.put(
        f"/api/projects/{project}/workflows/{created['workflow_id']}",
        json={"graph": graph, "expected_revision_id": graph["revision_id"]},
    )
    assert restricted.status_code == 200, restricted.text
    preflight = client.get(f"/api/projects/{project}/workflows/{created['workflow_id']}/preflight?refresh=true")
    assert preflight.status_code == 200, preflight.text
    payload = preflight.json()
    assert payload["ready"] is False
    assert [check["status"] for check in payload["checks"]] == [
        "available",
        "incompatible",
    ]
    assert "agentic harnesses" in payload["issues"][0]
    assert "Codex" in payload["issues"][0]

    graph = restricted.json()
    graph["variants"][1]["node_overrides"]["model"]["model"] = "provider/available"
    model_node = next(node for node in graph["nodes"] if node["kind"] == "agent")
    model_node["config"]["effort"] = "high"
    reasoning_requested = client.put(
        f"/api/projects/{project}/workflows/{created['workflow_id']}",
        json={"graph": graph, "expected_revision_id": graph["revision_id"]},
    )
    assert reasoning_requested.status_code == 200, reasoning_requested.text
    preflight = client.get(f"/api/projects/{project}/workflows/{created['workflow_id']}/preflight?refresh=true")
    assert preflight.status_code == 200, preflight.text
    payload = preflight.json()
    assert payload["ready"] is False
    assert [check["effort"] for check in payload["checks"]] == ["high", "high"]
    assert [check["status"] for check in payload["checks"]] == [
        "incompatible",
        "incompatible",
    ]
    assert all("reasoning support" in issue for issue in payload["issues"])

    blocked = client.post(
        f"/api/projects/{project}/workflows/{created['workflow_id']}/runs",
        json={"initial_prompt": "Compare this."},
    )
    assert blocked.status_code == 422
    assert "Model preflight failed" in blocked.json()["detail"]


def test_workflow_run_can_pin_the_exact_approved_revision(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    project = client.post("/api/projects", json={"name": "Exact workflow"}).json()["id"]
    created = client.post(
        f"/api/projects/{project}/workflows",
        json={"name": "Draft"},
    ).json()
    original_revision = created["revision_id"]
    created["description"] = "A reviewed change"
    saved = client.put(
        f"/api/projects/{project}/workflows/{created['workflow_id']}",
        json={"graph": created, "expected_revision_id": original_revision},
    )
    assert saved.status_code == 200, saved.text

    stale = client.post(
        f"/api/projects/{project}/workflows/{created['workflow_id']}/runs",
        json={"expected_revision_id": original_revision},
    )
    assert stale.status_code == 409
    assert original_revision in stale.json()["detail"]
    assert saved.json()["revision_id"] in stale.json()["detail"]


def test_complete_benchmark_workflow_requires_an_exact_dataset_binding(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    project = client.post("/api/projects", json={"name": "Harness composition"}).json()["id"]
    base = f"/api/projects/{project}"
    system = client.post(
        f"{base}/agent/prompts",
        json={"name": "Solver", "kind": "system", "template": "Solve the case."},
    ).json()
    user = client.post(
        f"{base}/agent/prompts",
        json={"name": "Benchmark case", "kind": "user", "template": "{{case_json}}"},
    ).json()
    config = {
        "executor_id": "unconfigured-test-executor",
        "model": "provider/cheap",
        "system_prompt": {"prompt_id": system["prompt_id"], "revision_id": system["revision_id"]},
        "user_prompt": {"prompt_id": user["prompt_id"], "revision_id": user["revision_id"]},
        "skills": [],
        "tools": [],
        "context_policy": {"mode": "none", "max_tokens": 1000},
        "budget": {"max_cost_usd": 0.01, "timeout_seconds": 60},
        "sandbox": {"mode": "read_only", "network": False},
        "session": {"mode": "fresh"},
    }
    graph = {
        "description": "Map one solver harness over a benchmark.",
        "schema_version": 2,
        "workflow_type": "graph",
        "presentation": {"recipe": "graph"},
        "variants": [
            {
                "variant_id": "cheap",
                "name": "Cheap",
                "node_overrides": {"solver": {"model": "provider/cheap"}},
            }
        ],
        "evaluators": [],
        "execution_policy": {
            "max_concurrency": 1,
            "failure_policy": "continue",
            "max_calls": 1,
            "max_total_cost_usd": 0.05,
        },
        "iterations": 1,
        "nodes": [
            {
                "id": "solver",
                "kind": "agent",
                "name": "Solver harness",
                "position": {"x": 100, "y": 100},
                "config": config,
            }
        ],
        "edges": [],
    }
    created = client.post(
        f"{base}/workflows",
        json={"name": "Atomic benchmark", "graph": graph},
    )
    assert created.status_code == 200, created.text
    workflow = created.json()
    assert workflow["nodes"][0]["name"] == "Solver harness"

    preflight = client.get(f"{base}/workflows/{workflow['workflow_id']}/preflight")
    assert preflight.status_code == 200, preflight.text
    assert any("Attach an exact dataset revision" in issue for issue in preflight.json()["issues"])

    dataset = client.post(
        f"{base}/datasets",
        json={
            "name": "One public case",
            "cases": [
                {
                    "case_id": "one",
                    "name": "One",
                    "inputs": {"question": "1 + 1"},
                    "expected": "2",
                }
            ],
        },
    ).json()
    workflow["dataset_ref"] = {
        "dataset_id": dataset["dataset_id"],
        "revision_id": dataset["revision_id"],
    }
    saved = client.put(
        f"{base}/workflows/{workflow['workflow_id']}",
        json={"graph": workflow, "expected_revision_id": workflow["revision_id"]},
    )
    assert saved.status_code == 200, saved.text
    rebound = client.get(f"{base}/workflows/{workflow['workflow_id']}/preflight").json()
    assert not any("Attach an exact dataset revision" in issue for issue in rebound["issues"])


def test_project_dataset_library_pins_revisions_in_workflows(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    project = client.post("/api/projects", json={"name": "Dataset library"}).json()["id"]
    created = client.post(
        f"/api/projects/{project}/datasets",
        json={
            "name": "Moderation controls",
            "description": "A reusable corpus.",
            "cases": [
                {
                    "case_id": "benign",
                    "name": "Benign",
                    "inputs": {"paper_text": "A harmless abstract."},
                    "expected": {"decision": "allow"},
                }
            ],
        },
    )
    assert created.status_code == 200, created.text
    dataset = created.json()
    assert client.get(f"/api/projects/{project}/datasets").json()["datasets"][0]["revision_id"] == dataset["revision_id"]

    workflow = client.post(f"/api/projects/{project}/workflows", json={"name": "Pinned corpus"}).json()
    workflow["dataset_ref"] = {
        "dataset_id": dataset["dataset_id"],
        "revision_id": dataset["revision_id"],
    }
    saved = client.put(
        f"/api/projects/{project}/workflows/{workflow['workflow_id']}",
        json={"graph": workflow, "expected_revision_id": workflow["revision_id"]},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["dataset_ref"] == workflow["dataset_ref"]


def test_native_codex_setup_prompt_and_test_use_one_connection(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    fake = Path(__file__).parent / "fake_appserver.py"
    monkeypatch.setenv(
        "REASONING_CODEX_COMMAND",
        f"{shlex.quote(sys.executable)} {shlex.quote(str(fake))}",
    )
    client = _client(tmp_path, monkeypatch)
    secret = "sk-or-native-codex-secret"
    client.put("/api/settings/api-keys/openrouter", json={"api_key": secret})

    setup = client.get(
        "/api/agent/connections/codex/setup",
        params={"model": "provider/model"},
    )
    assert setup.status_code == 200, setup.text
    assert "~/.codex/config.toml" in setup.json()["prompt"]
    assert 'env_key = "OPENROUTER_API_KEY"' in setup.json()["prompt"]
    assert secret not in setup.text

    tested = client.post(
        "/api/agent/connections/codex/test",
        json={"model": "provider/model"},
    )
    assert tested.status_code == 200, tested.text
    assert tested.json()["status"] == "ready"
    assert secret not in tested.text

    status = next(item for item in client.get("/api/agent/connections").json()["connections"] if item["executor_id"] == "codex")
    assert status["ready"] is True
    assert status["tested_model"] == "provider/model"
    assert status["validation_status"] == "valid"
    assert secret not in json.dumps(status)

    client.put("/api/settings/api-keys/openrouter", json={"api_key": "replacement"})
    reset = next(item for item in client.get("/api/agent/connections").json()["connections"] if item["executor_id"] == "codex")
    assert reset["validation_status"] == "untested"


def test_recent_openrouter_models_are_durable_bounded_and_most_recent_first(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    assert client.get("/api/settings/recent-models").json() == {"openrouter": []}

    for index in range(14):
        response = client.put(
            "/api/settings/recent-models/openrouter",
            json={"model_id": f"provider/model-{index}"},
        )
        assert response.status_code == 200

    remembered = response.json()["openrouter"]
    assert remembered == [f"provider/model-{index}" for index in range(13, 1, -1)]

    repeated = client.put(
        "/api/settings/recent-models/openrouter",
        json={"model_id": "  provider/model-6  "},
    )
    assert repeated.json()["openrouter"] == [
        "provider/model-6",
        *(model for model in remembered if model != "provider/model-6"),
    ]

    path = tmp_path / "workbench" / "settings" / "recent-models.json"
    assert json.loads(path.read_text(encoding="utf-8")) == repeated.json()
    restarted = _client(tmp_path, monkeypatch)
    assert restarted.get("/api/settings/recent-models").json() == repeated.json()
    assert client.put("/api/settings/recent-models/openrouter", json={"model_id": "  "}).status_code == 422
    assert client.put("/api/settings/recent-models/unknown", json={"model_id": "a/model"}).status_code == 404


def test_pinned_models_and_empty_workflow_rankings_are_durable(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    assert client.get("/api/settings/pinned-models").json() == {"openrouter": []}
    saved = client.put(
        "/api/settings/pinned-models/openrouter",
        json={"model_ids": [" provider/model-b ", "provider/model-a", "provider/model-b"]},
    )
    assert saved.status_code == 200
    assert saved.json() == {"openrouter": ["provider/model-b", "provider/model-a"]}
    assert _client(tmp_path, monkeypatch).get("/api/settings/pinned-models").json() == saved.json()
    assert client.put("/api/settings/pinned-models/unknown", json={"model_ids": []}).status_code == 404

    project = client.post("/api/projects", json={"name": "Rankings"}).json()["id"]
    workflow = client.post(
        f"/api/projects/{project}/workflows",
        json={"name": "Comparison", "template_id": "compare-models"},
    ).json()
    ranking = client.get(f"/api/projects/{project}/workflows/{workflow['workflow_id']}/model-rankings")
    assert ranking.status_code == 200
    assert ranking.json()["compatible_runs"] == 0
    assert ranking.json()["configurations"] == []
    assert all(mode["available"] is False for mode in ranking.json()["modes"])

    routed = client.post(
        f"/api/projects/{project}/workflows/{workflow['workflow_id']}/auto-route",
        json={"objective": "balanced"},
    )
    assert routed.status_code == 200
    assert routed.json()["selected"] is None
    assert routed.json()["candidate_count"] == 0
    assert routed.json()["eligible_count"] == 0


def test_mcp_connections_are_private_reusable_and_user_configurable(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    secret = "Bearer private-research-token"

    created = client.put(
        "/api/settings/mcp-connections/scholar",
        json={
            "name": "Scholar archive",
            "transport": "http",
            "url": "https://research.example/mcp",
            "headers": {"Authorization": secret},
            "enabled": True,
        },
    )

    assert created.status_code == 200
    assert secret not in created.text
    public = created.json()["connection"]
    assert public["connection_id"] == "scholar"
    assert public["header_names"] == ["Authorization"]
    assert public["enabled"] is True

    disabled = client.put(
        "/api/settings/mcp-connections/scholar",
        json={
            "name": "Scholar archive",
            "transport": "http",
            "url": "https://research.example/mcp",
            "enabled": False,
        },
    )
    assert disabled.status_code == 200
    assert disabled.json()["connection"]["header_names"] == ["Authorization"]
    assert secret not in disabled.text

    path = tmp_path / "workbench" / "settings" / "mcp-connections.json"
    private = json.loads(path.read_text(encoding="utf-8"))["scholar"]
    assert private["headers"] == {"Authorization": secret}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600

    removed = client.delete("/api/settings/mcp-connections/scholar")
    assert removed.status_code == 200
    assert removed.json()["connections"] == []
    assert secret not in removed.text


def test_research_chat_can_use_a_scoped_alternative_executor(tmp_path, monkeypatch):
    class FakeHermes:
        def __init__(self):
            self.requests = []

        @property
        def capabilities(self):
            return ExecutorCapabilities(
                "hermes",
                "Hermes Agent (TEST)",
                PromptFidelity.CONTROLLED_INPUT,
                supports_tools=True,
                model_options=("test/hermes",),
            )

        def execute(self, request, emit=None):
            self.requests.append(request)
            return AgentRunResult(
                request.run_id,
                "completed",
                output_text="Hermes research response",
            )

    fake = FakeHermes()
    monkeypatch.setattr("reasoning_ledger.webapp.HermesAgentExecutor", lambda transport: fake)
    client = _client(tmp_path, monkeypatch)
    project = client.post("/api/projects", json={"name": "Research"}).json()["id"]
    ledger = LedgerStore(tmp_path / "workbench" / "projects" / project / "ledger")
    claim = service.claim_payload(
        ledger,
        "The current reviewed state must accompany every research run.",
    )
    chat = client.post(f"/api/projects/{project}/chats", json={}).json()["id"]
    base = f"/api/projects/{project}/chats/{chat}"

    sent = client.post(
        f"{base}/messages",
        json={
            "message": "Test the conjecture.",
            "executor_id": "hermes",
            "model": "test/hermes",
            "effort": "high",
        },
    )
    assert sent.status_code == 200
    deadline = time.time() + 2
    while time.time() < deadline:
        history = client.get(f"{base}/history?tail=0").json()
        if not history["turn_active"]:
            break
        time.sleep(0.01)

    assistant = next(entry for entry in history["messages"] if entry["role"] == "assistant")
    assert assistant["text"] == "Hermes research response"
    assert assistant["executor_id"] == "hermes"
    assert assistant["model"] == "test/hermes"
    prepared = fake.requests[0]
    assert prepared.user_prompt == "Test the conjecture."
    assert len(prepared.context_messages) == 1
    context = prepared.context_messages[0]
    assert context["role"] == "system"
    assert "Current claim ledger state for this run" in context["content"]
    assert "outranks narrative status labels in older artifacts" in context["content"]
    assert claim["claim_id"] in context["content"]
    assert claim["revision_id"] in context["content"]
    assert "The current reviewed state must accompany every research run." in context["content"]
    assert prepared.spec.sandbox.mode == "research"
    assert not prepared.spec.sandbox.network
    tool_names = {tool.name for tool in prepared.tools}
    assert {
        "ledger_status",
        "ledger_search",
        "ledger_graph",
        "artifact_list",
        "artifact_read",
        "artifact_write",
        "arxiv_lookup",
        "arxiv_read",
        "arxiv_search",
        "arxiv_source_list",
        "arxiv_source_read",
        "bibliography_export",
        "bibliography_get",
        "bibliography_resource_read",
        "bibliography_search",
        "constant_lookup",
        "crossref_lookup",
        "crossref_search",
        "lean_environment",
        "lmfdb_elliptic_curves_q",
        "lmfdb_number_fields",
        "oeis_bfile",
        "oeis_lookup",
        "oeis_search",
        "scientific_calculate",
        "unit_convert",
        "workflow_run_list",
        "workflow_run_read",
    }.issubset(tool_names)
    assert (
        not {
            "bibliography_add",
            "bibliography_summary_add",
            "lean_check",
        }
        & tool_names
    )


def test_research_chat_can_enforce_a_codex_tool_and_budget_contract(tmp_path, monkeypatch):
    class FakeCodex:
        def __init__(self):
            self.requests = []

        @property
        def capabilities(self):
            return ExecutorCapabilities(
                "codex",
                "Codex CLI (TEST)",
                PromptFidelity.CONTROLLED_INPUT,
                supports_tools=True,
                supports_mcp=True,
                model_options=("gpt-5.6-sol",),
            )

        def execute(self, request, emit=None):
            self.requests.append(request)
            return AgentRunResult(
                request.run_id,
                "completed",
                output_text="Codex completed the bounded audit.",
            )

    fake = FakeCodex()
    monkeypatch.setattr(
        "reasoning_ledger.webapp.CodexWorkflowExecutor",
        lambda *args, **kwargs: fake,
    )
    client = _client(tmp_path, monkeypatch)
    project = client.post("/api/projects", json={"name": "Research"}).json()["id"]
    chat = client.post(f"/api/projects/{project}/chats", json={}).json()["id"]
    base = f"/api/projects/{project}/chats/{chat}"

    sent = client.post(
        f"{base}/messages",
        json={
            "message": "Read once and persist the exact audit.",
            "executor_id": "codex",
            "model": "gpt-5.6-sol",
            "effort": "xhigh",
            "timeout_seconds": 180,
            "max_output_tokens": 12000,
            "max_tool_calls": 2,
            "required_tool_calls": {"artifact_read": 1, "artifact_write": 1},
        },
    )
    assert sent.status_code == 200
    deadline = time.time() + 2
    while time.time() < deadline:
        history = client.get(f"{base}/history?tail=0").json()
        if not history["turn_active"]:
            break
        time.sleep(0.01)

    assert history["turn_active"] is False
    assistant = next(entry for entry in history["messages"] if entry["role"] == "assistant")
    assert assistant["text"] == "Codex completed the bounded audit."
    assert assistant["executor_id"] == "codex"
    assert assistant["model"] == "gpt-5.6-sol"
    assert assistant["model_provider"] == "openai"
    prepared = fake.requests[0]
    assert prepared.spec.executor_id == "codex"
    assert prepared.spec.model == "gpt-5.6-sol"
    assert prepared.spec.model_provider == "openai"
    assert prepared.spec.effort == "xhigh"
    assert prepared.spec.budget.timeout_seconds == 180
    assert prepared.spec.budget.max_output_tokens == 12000
    assert prepared.spec.budget.max_tool_calls == 2
    assert prepared.spec.completion_tool_calls == {
        "artifact_read": 1,
        "artifact_write": 1,
    }
    assert prepared.spec.session.mode == "fresh"
    assert prepared.spec.sandbox.mode == "research"
    assert not prepared.spec.sandbox.network
    assert {"artifact_read", "artifact_write"}.issubset({tool.name for tool in prepared.tools})


def test_alternative_executor_failure_is_durable_and_never_blank(tmp_path, monkeypatch):
    class FailingHermes:
        @property
        def capabilities(self):
            return ExecutorCapabilities(
                "hermes",
                "Hermes Agent (TEST)",
                PromptFidelity.CONTROLLED_INPUT,
                model_options=("test/hermes",),
            )

        def execute(self, request, emit=None):
            return AgentRunResult(
                request.run_id,
                "failed",
                error="provider reached its output limit without an answer",
            )

    monkeypatch.setattr(
        "reasoning_ledger.webapp.HermesAgentExecutor",
        lambda transport: FailingHermes(),
    )
    client = _client(tmp_path, monkeypatch)
    project = client.post("/api/projects", json={"name": "Research"}).json()["id"]
    chat = client.post(f"/api/projects/{project}/chats", json={}).json()["id"]
    base = f"/api/projects/{project}/chats/{chat}"

    sent = client.post(
        f"{base}/messages",
        json={
            "message": "Test the conjecture.",
            "executor_id": "hermes",
            "model": "test/hermes",
        },
    )
    assert sent.status_code == 200
    deadline = time.time() + 2
    while time.time() < deadline:
        history = client.get(f"{base}/history?tail=0").json()
        if not history["turn_active"]:
            break
        time.sleep(0.01)

    errors = [entry for entry in history["messages"] if entry["role"] == "error"]
    assert [entry["text"] for entry in errors] == ["provider reached its output limit without an answer"]
    assert errors[0]["executor_id"] == "hermes"
    assert errors[0]["model"] == "test/hermes"
    assert not any(entry["role"] == "assistant" and not entry["text"].strip() for entry in history["messages"])


def test_alternative_executor_cannot_publish_a_blank_success(tmp_path, monkeypatch):
    class BlankHermes:
        @property
        def capabilities(self):
            return ExecutorCapabilities(
                "hermes",
                "Hermes Agent (TEST)",
                PromptFidelity.CONTROLLED_INPUT,
                model_options=("test/hermes",),
            )

        def execute(self, request, emit=None):
            return AgentRunResult(request.run_id, "completed", output_text="  ")

    monkeypatch.setattr(
        "reasoning_ledger.webapp.HermesAgentExecutor",
        lambda transport: BlankHermes(),
    )
    client = _client(tmp_path, monkeypatch)
    project = client.post("/api/projects", json={"name": "Research"}).json()["id"]
    chat = client.post(f"/api/projects/{project}/chats", json={}).json()["id"]
    base = f"/api/projects/{project}/chats/{chat}"

    assert (
        client.post(
            f"{base}/messages",
            json={
                "message": "Test the conjecture.",
                "executor_id": "hermes",
                "model": "test/hermes",
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

    assert [entry["text"] for entry in history["messages"] if entry["role"] == "error"] == [
        "hermes completed without visible assistant content"
    ]
    assert not any(entry["role"] == "assistant" for entry in history["messages"])


def test_global_bibliography_has_project_chat_views_and_bibtex_export(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    project = client.post("/api/projects", json={"name": "Geometry"}).json()["id"]
    chat = client.post(f"/api/projects/{project}/chats", json={}).json()["id"]

    created = client.post(
        "/api/bibliography",
        json={
            "item_type": "preprint",
            "title": "A theorem used everywhere",
            "authors": ["Emmy Noether"],
            "year": 2026,
            "arxiv_id": "2601.01234v2",
            "primary_category": "math.AG",
            "tags": ["geometry"],
        },
    )
    assert created.status_code == 200
    item_id = created.json()["item"]["bibliography_id"]
    ledger = LedgerStore(tmp_path / "workbench" / "projects" / project / "ledger")
    ledger_reference = service.reference_payload(
        ledger,
        author="Emmy Noether",
        title="A theorem used everywhere",
        year=2026,
        arxiv="2601.01234v2",
    )
    cited_claim = service.claim_payload(
        ledger,
        "The cited theorem supplies the geometric step.",
        reference_id=ledger_reference["reference_id"],
        locator="Theorem 1",
    )
    BibliographyStore(tmp_path / "workbench" / "agentic" / "bibliography").upsert(
        {
            "item_type": "preprint",
            "title": "A theorem used everywhere",
            "authors": ["Emmy Noether"],
            "year": 2026,
            "arxiv_id": "2601.01234v2",
            "provenance": {
                "legacy_references": [
                    {
                        "project_id": project,
                        "reference_id": ledger_reference["reference_id"],
                        "revision_id": ledger_reference["revision_id"],
                    }
                ]
            },
        }
    )
    assert client.get(f"/api/projects/{project}/bibliography").json()["items"] == []

    attached = client.put(
        f"/api/projects/{project}/bibliography/{item_id}",
        json={"notes": "Background", "tags": ["foundational"]},
    )
    assert attached.status_code == 200
    assert attached.json()["view"]["project_attached"] is True
    inherited = client.get(f"/api/projects/{project}/chats/{chat}/bibliography").json()["items"]
    assert inherited[0]["bibliography_id"] == item_id
    assert inherited[0]["view"]["chat_attached"] is False

    chat_attached = client.put(
        f"/api/projects/{project}/chats/{chat}/bibliography/{item_id}",
        json={"notes": "Discussed in this chat"},
    )
    assert chat_attached.json()["view"]["chat_attached"] is True
    exported = client.get(f"/api/projects/{project}/chats/{chat}/bibliography/export.bib")
    assert exported.status_code == 200
    assert "attachment;" in exported.headers["content-disposition"]
    assert "@misc{" in exported.text
    assert "eprint = {2601.01234v2}" in exported.text

    uploaded = client.put(
        f"/api/bibliography/{item_id}/resources?filename=reading-notes.md",
        content=b"# Reading notes\n\nA durable document.",
        headers={"Content-Type": "text/markdown"},
    )
    assert uploaded.status_code == 200
    resource = uploaded.json()["resource"]
    refreshed = client.get(f"/api/bibliography/{item_id}").json()
    assert refreshed["resources"] == [resource]
    opened = client.get(f"/api/bibliography/{item_id}/resources/{resource['resource_id']}")
    assert opened.status_code == 200
    assert opened.text.startswith("# Reading notes")
    assert opened.headers["content-type"].startswith("text/markdown")

    global_summary = client.post(
        f"/api/bibliography/{item_id}/summaries",
        json={
            "kind": "overview",
            "title": "Shared account",
            "text": "A summary for every project.",
            "provenance": {"model": "test-model", "run_id": "run-1"},
        },
    )
    assert global_summary.status_code == 200
    project_summary = client.post(
        f"/api/projects/{project}/bibliography/{item_id}/summaries",
        json={"kind": "relevance", "text": "Used by the geometry project."},
    )
    assert project_summary.status_code == 200
    chat_summary = client.post(
        f"/api/projects/{project}/chats/{chat}/bibliography/{item_id}/summaries",
        json={"kind": "critique", "text": "A chat-specific caveat."},
    )
    assert chat_summary.status_code == 200
    assert len(client.get(f"/api/bibliography/{item_id}").json()["summaries"]) == 1
    project_view = client.get(f"/api/projects/{project}/bibliography").json()["items"][0]
    assert [summary["scope"] for summary in project_view["summaries"]] == [
        "global",
        "project",
    ]
    chat_view = client.get(f"/api/projects/{project}/chats/{chat}/bibliography").json()["items"][0]
    assert [summary["scope"] for summary in chat_view["summaries"]] == [
        "global",
        "project",
        "chat",
    ]

    indexed = client.post(
        f"/api/bibliography/{item_id}/connections",
        json={
            "kind": "artifact",
            "project_id": project,
            "artifact_path": "notes/shared-result.md",
            "label": "Shared result",
            "provenance": {"run_id": "run-1"},
        },
    )
    assert indexed.status_code == 200
    assert indexed.json()["created"] is True
    connections = client.get(f"/api/bibliography/{item_id}/connections").json()["connections"]
    assert {connection["kind"] for connection in connections} >= {
        "project",
        "chat",
        "artifact",
        "ledger_reference",
        "claim",
    }
    assert next(connection for connection in connections if connection["kind"] == "project")["label"] == "Geometry"
    assert next(connection for connection in connections if connection["kind"] == "chat")["label"] == "chat 1"
    claim_connection = next(connection for connection in connections if connection["kind"] == "claim")
    assert claim_connection["claim_id"] == cited_claim["claim_id"]
    assert claim_connection["locator"] == "Theorem 1"
