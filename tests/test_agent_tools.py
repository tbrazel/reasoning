import asyncio
import hashlib
import json

import pytest
from jsonschema import ValidationError

from reasoning_ledger import LedgerStore, service
from reasoning_ledger.agents import (
    SkillBinding,
    SkillRegistry,
    ToolDefinition,
    ToolGrant,
    ToolRegistry,
    ToolRegistryError,
    build_mcp_bridge,
    build_project_tool_registry,
    project_mcp_config,
)


def test_registry_validates_grants_arguments_and_outputs():
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="double",
            description="Double an integer.",
            input_schema={
                "type": "object",
                "properties": {"value": {"type": "integer"}},
                "required": ["value"],
                "additionalProperties": False,
            },
            output_schema={"type": "integer"},
            handler=lambda value: value * 2,
            capabilities=("math:compute",),
        )
    )
    selected = registry.select(ToolGrant(("double",), capabilities=("math:compute",)))
    assert registry.prepared(selected)[0].name == "double"
    assert registry.invoke("double", {"value": 4}) == 8
    with pytest.raises(ValidationError):
        registry.invoke("double", {"value": "four"})
    with pytest.raises(ToolRegistryError, match="capabilities"):
        registry.select(ToolGrant(("double",)))


def test_project_tools_share_implementations_and_confine_artifacts(tmp_path):
    store = LedgerStore(tmp_path / "ledger")
    claim = service.claim_payload(
        store,
        "A workflow-visible claim.",
        author={"operator": "op", "agent": "a", "session": "s"},
    )
    registry = build_project_tool_registry(store, tmp_path / "artifacts")
    artifact_write = registry.get("artifact_write")
    assert "use plain Markdown and Unicode" in artifact_write.description
    assert "double-escape every TeX backslash" in artifact_write.description
    expected_sha = artifact_write.input_schema["properties"][
        "expected_sha256"
    ]
    assert "omit when creating a new path" in expected_sha["description"]
    assert "not a request, receipt, or proposed-content hash" in (
        expected_sha["description"]
    )
    assert registry.invoke("ledger_status", {"claim_id": claim["claim_id"]})["statement_text"] == "A workflow-visible claim."
    written = registry.invoke(
        "artifact_write",
        {"path": "notes/week-1.txt", "text": "progress"},
    )
    assert written == {
        "path": "notes/week-1.txt",
        "bytes": 8,
        "sha256": hashlib.sha256(b"progress").hexdigest(),
    }
    compatibility_write = registry.invoke(
        "artifact_write",
        {"path": "notes/content-alias.txt", "content": "compatible"},
    )
    assert compatibility_write["sha256"] == hashlib.sha256(b"compatible").hexdigest()
    body_compatibility_write = registry.invoke(
        "artifact_write",
        {"path": "notes/body-alias.txt", "body": "also compatible"},
    )
    assert body_compatibility_write["sha256"] == hashlib.sha256(
        b"also compatible"
    ).hexdigest()
    for ambiguous in (
        {"text": "one", "content": "two"},
        {"text": "one", "body": "two"},
        {"content": "one", "body": "two"},
        {"text": "one", "content": "two", "body": "three"},
    ):
        with pytest.raises(ValidationError):
            registry.invoke(
                "artifact_write",
                {"path": "notes/ambiguous.txt", **ambiguous},
            )
    read = registry.invoke("artifact_read", {"path": "notes/week-1.txt"})
    assert read == {"path": "notes/week-1.txt", "text": "progress", "bytes": 8, "sha256": written["sha256"]}
    chunk = registry.invoke(
        "artifact_read_chunk",
        {"path": "notes/week-1.txt", "start_character": 1, "max_characters": 3},
    )
    assert chunk == {
        "path": "notes/week-1.txt",
        "text": "rog",
        "start_character": 1,
        "end_character": 4,
        "total_characters": 8,
        "complete": False,
        "next_start_character": 4,
        "bytes": 8,
        "sha256": written["sha256"],
    }
    with pytest.raises(ValueError, match="already exists"):
        registry.invoke(
            "artifact_write",
            {"path": "notes/week-1.txt", "text": "lost update"},
        )
    with pytest.raises(ValueError, match="changed since"):
        registry.invoke(
            "artifact_write",
            {
                "path": "notes/week-1.txt",
                "text": "lost update",
                "expected_sha256": "0" * 64,
            },
        )
    replaced = registry.invoke(
        "artifact_write",
        {
            "path": "notes/week-1.txt",
            "text": "next",
            "expected_sha256": read["sha256"],
        },
    )
    assert replaced["sha256"] == hashlib.sha256(b"next").hexdigest()
    for control in ("\b", "\t", "\r", "\x7f"):
        with pytest.raises(ValueError, match="forbidden control character"):
            registry.invoke(
                "artifact_write",
                {"path": f"notes/control-{ord(control)}.txt", "text": f"bad{control}text"},
            )
    with pytest.raises(ValueError, match="does not exist"):
        registry.invoke(
            "artifact_write",
            {
                "path": "notes/missing.txt",
                "text": "new",
                "expected_sha256": replaced["sha256"],
            },
        )
    run_state = {
        "run_id": "wfrun_example",
        "workflow_id": "wf_advisor",
        "workflow_snapshot": {"name": "Advisor and student"},
        "status": "completed",
        "iterations": 3,
        "cost": {"total_usd": 0.125},
        "artifact_path": "workflow-runs/wfrun_example.md",
        "created_at": "2026-08-06T10:00:00+00:00",
        "updated_at": "2026-08-06T10:03:00+00:00",
        "outputs": {"nodes": [{"node_id": "advisor", "text": "Review"}]},
    }
    registry.invoke(
        "artifact_write",
        {
            "path": "workflow-runs/wfrun_example.json",
            "text": json.dumps(run_state),
        },
    )
    bundle = registry.invoke(
        "artifact_read_many",
        {"paths": ["notes/week-1.txt", "workflow-runs/wfrun_example.json"]},
    )
    assert [artifact["path"] for artifact in bundle["artifacts"]] == [
        "notes/week-1.txt",
        "workflow-runs/wfrun_example.json",
    ]
    assert bundle["bytes"] == sum(artifact["bytes"] for artifact in bundle["artifacts"])
    listed = registry.invoke("workflow_run_list", {})["runs"]
    assert listed == [
        {
            "run_id": "wfrun_example",
            "workflow_id": "wf_advisor",
            "workflow_name": "Advisor and student",
            "status": "completed",
            "iterations": 3,
            "cost": {"total_usd": 0.125},
            "artifact_path": "workflow-runs/wfrun_example.md",
            "created_at": "2026-08-06T10:00:00+00:00",
            "updated_at": "2026-08-06T10:03:00+00:00",
        }
    ]
    assert registry.invoke("workflow_run_read", {"run_id": "wfrun_example"})["outputs"]["nodes"][0]["text"] == "Review"
    with pytest.raises(ValueError, match="escapes"):
        registry.invoke("artifact_read", {"path": "../secret"})
    with pytest.raises(ValueError, match="unknown artifact"):
        registry.invoke(
            "artifact_read_many",
            {"paths": ["notes/week-1.txt", "notes/missing.txt"]},
        )
    with pytest.raises(ValueError, match="exceeds artifact length"):
        registry.invoke(
            "artifact_read_chunk",
            {"path": "notes/week-1.txt", "start_character": 5},
        )
    registry.invoke(
        "artifact_write",
        {"path": "notes/large.txt", "text": "x" * (256 * 1024)},
    )
    with pytest.raises(ValueError, match="256 KiB"):
        registry.invoke(
            "artifact_read_many",
            {"paths": ["notes/large.txt", "notes/week-1.txt"]},
        )
    with pytest.raises(ValidationError):
        registry.invoke(
            "artifact_read_many",
            {"paths": ["notes/week-1.txt", "notes/week-1.txt"]},
        )
    with pytest.raises(ValueError, match="unknown workflow run"):
        registry.invoke("workflow_run_read", {"run_id": "wfrun_missing"})


def test_mcp_bridge_exposes_only_granted_registry_tools(tmp_path):
    store = LedgerStore(tmp_path / "ledger")
    registry = build_project_tool_registry(store, tmp_path / "artifacts")
    server = build_mcp_bridge(
        registry,
        allowed_tools=("artifact_list", "artifact_write"),
    )

    async def flow():
        tools = await server.reasoning_list_tools()
        assert [tool.name for tool in tools] == ["artifact_list", "artifact_write"]
        assert tools[0].description.startswith(
            "Canonical Reasoning tool ID: artifact_list\n\n"
        )
        assert tools[1].description.startswith(
            "Canonical Reasoning tool ID: artifact_write\n\n"
        )
        assert tools[0].annotations.readOnlyHint is True
        assert tools[1].annotations.readOnlyHint is False
        content, structured = await server.reasoning_call_tool(
            "artifact_write", {"path": "x.txt", "text": "hello"}
        )
        expected = {
            "path": "x.txt",
            "bytes": 5,
            "sha256": hashlib.sha256(b"hello").hexdigest(),
        }
        assert json.loads(content[0].text) == expected
        assert structured == expected
        with pytest.raises(Exception, match="not granted"):
            await server.reasoning_call_tool("ledger_status", {})

    asyncio.run(flow())


def test_mcp_bridge_rejects_queued_calls_before_dispatch(tmp_path):
    store = LedgerStore(tmp_path / "ledger")
    registry = build_project_tool_registry(store, tmp_path / "artifacts")
    server = build_mcp_bridge(
        registry,
        allowed_tools=("artifact_write",),
        max_calls=1,
    )

    async def flow():
        await server.reasoning_call_tool(
            "artifact_write", {"path": "first.txt", "text": "written"}
        )
        with pytest.raises(ToolRegistryError, match="queued call was not executed"):
            await server.reasoning_call_tool(
                "artifact_write", {"path": "second.txt", "text": "forbidden"}
            )

    asyncio.run(flow())
    assert (tmp_path / "artifacts" / "first.txt").read_text() == "written"
    assert not (tmp_path / "artifacts" / "second.txt").exists()


def test_mcp_bridge_reserves_required_calls_without_spending_denials(tmp_path):
    store = LedgerStore(tmp_path / "ledger")
    registry = build_project_tool_registry(store, tmp_path / "artifacts")
    server = build_mcp_bridge(
        registry,
        allowed_tools=("artifact_list", "artifact_write", "ledger_status"),
        max_calls=3,
        completion_tool_calls={"ledger_status": 1},
        completion_artifacts=("result.md",),
    )

    async def flow():
        await server.reasoning_call_tool("artifact_list", {})
        with pytest.raises(ToolRegistryError, match="completion capacity is reserved"):
            await server.reasoning_call_tool("artifact_list", {})
        await server.reasoning_call_tool("ledger_status", {})
        await server.reasoning_call_tool(
            "artifact_write",
            {"path": "result.md", "text": "complete"},
        )

    asyncio.run(flow())
    assert (tmp_path / "artifacts" / "result.md").read_text() == "complete"


def test_mcp_bridge_counts_a_required_machine_report_as_its_artifact_write(tmp_path):
    store = LedgerStore(tmp_path / "ledger")
    registry = build_project_tool_registry(store, tmp_path / "artifacts")

    @registry.tool(
        name="canonical_report",
        description="Write one canonical report.",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
        risk="write",
        capabilities=("artifacts:write",),
    )
    def canonical_report(path):
        return registry.invoke(
            "artifact_write",
            {"path": path, "text": "machine receipt"},
        )

    server = build_mcp_bridge(
        registry,
        allowed_tools=("canonical_report",),
        max_calls=1,
        completion_tool_calls={"canonical_report": 1},
        completion_artifacts=("result.md",),
    )

    async def flow():
        await server.reasoning_call_tool("canonical_report", {"path": "result.md"})

    asyncio.run(flow())
    assert (tmp_path / "artifacts" / "result.md").read_text() == "machine receipt"


def test_mcp_bridge_rejects_artifact_completion_without_a_writer(tmp_path):
    registry = build_project_tool_registry(
        LedgerStore(tmp_path / "ledger"),
        tmp_path / "artifacts",
    )

    with pytest.raises(ValueError, match="artifacts:write"):
        build_mcp_bridge(
            registry,
            allowed_tools=("artifact_list",),
            completion_artifacts=("result.md",),
        )



def test_mcp_bridge_delivers_small_artifact_bundle_as_complete_bounded_blocks(tmp_path):
    store = LedgerStore(tmp_path / "ledger")
    registry = build_project_tool_registry(store, tmp_path / "artifacts")
    registry.invoke(
        "artifact_write",
        {"path": "first.txt", "text": "first body"},
    )
    registry.invoke(
        "artifact_write",
        {"path": "second.txt", "text": "second body"},
    )
    server = build_mcp_bridge(registry, allowed_tools=("artifact_read_many",))

    async def flow():
        result, structured = await server.reasoning_call_tool(
            "artifact_read_many",
            {"paths": ["first.txt", "second.txt"]},
        )
        assert len(result) == 4
        header = json.loads(result[0].text)["artifact_bundle"]
        assert header == {
            "delivery": "multi-block-v2",
            "content_complete": True,
            "artifact_count": 2,
            "bytes": 21,
            "paths": ["first.txt", "second.txt"],
        }
        first = json.loads(result[1].text)
        second = json.loads(result[2].text)
        assert (first["ordinal"], first["artifact_count"]) == (1, 2)
        assert first["artifact"]["path"] == "first.txt"
        assert first["artifact"]["text"] == "first body"
        assert (second["ordinal"], second["artifact_count"]) == (2, 2)
        assert second["artifact"]["path"] == "second.txt"
        assert second["artifact"]["text"] == "second body"
        receipt = json.loads(result[3].text)["artifact_bundle_receipt"]
        assert receipt == {
            "delivery": "multi-block-v2",
            "content_complete": True,
            "complete": True,
            "artifact_count": 2,
            "paths": ["first.txt", "second.txt"],
        }
        assert [artifact["text"] for artifact in structured["artifacts"]] == [
            "first body",
            "second body",
        ]

    asyncio.run(flow())


def test_mcp_bridge_manifests_and_chunks_large_artifact_bundle(tmp_path):
    store = LedgerStore(tmp_path / "ledger")
    registry = build_project_tool_registry(store, tmp_path / "artifacts")
    text = "α\"" * 9000 + "TAIL_MARKER"
    written = registry.invoke(
        "artifact_write",
        {"path": "large.txt", "text": text},
    )
    server = build_mcp_bridge(
        registry,
        allowed_tools=("artifact_read", "artifact_read_many", "artifact_read_chunk"),
    )

    async def flow():
        single, single_structured = await server.reasoning_call_tool(
            "artifact_read", {"path": "large.txt"}
        )
        single_manifest = json.loads(single[0].text)["artifact_manifest"]
        assert single_structured["artifact_manifest"] == single_manifest
        assert single_manifest["content_complete"] is False
        assert single_manifest["required_tool"] == "artifact_read_chunk"
        assert "TAIL_MARKER" not in single[0].text

        bundled, bundled_structured = await server.reasoning_call_tool(
            "artifact_read_many",
            {"paths": ["large.txt"]},
        )
        manifest = json.loads(bundled[0].text)["artifact_bundle_manifest"]
        assert bundled_structured["artifact_bundle_manifest"] == manifest
        assert len(bundled) == 1
        assert manifest["delivery"] == "manifest-only-v1"
        assert manifest["content_complete"] is False
        assert manifest["artifacts"][0]["sha256"] == written["sha256"]
        assert "TAIL_MARKER" not in bundled[0].text

        chunks = []
        start = 0
        while True:
            result, structured = await server.reasoning_call_tool(
                "artifact_read_chunk",
                {
                    "path": "large.txt",
                    "start_character": start,
                    "max_characters": 12000,
                },
            )
            assert len(result) == 3
            metadata = json.loads(result[0].text)["artifact_chunk"]
            receipt = json.loads(result[2].text)["artifact_chunk_receipt"]
            assert metadata["delivery_complete"] is True
            assert receipt["delivery_complete"] is True
            assert metadata["sha256"] == written["sha256"]
            assert structured["next_start_character"] == metadata["next_start_character"]
            assert structured["text"] == result[1].text
            chunks.append(result[1].text)
            if metadata["complete"]:
                break
            start = metadata["next_start_character"]
        assert "".join(chunks) == text
        assert "TAIL_MARKER" in chunks[-1]

    asyncio.run(flow())


def test_mcp_bridge_delivers_medium_artifact_in_one_read(tmp_path):
    store = LedgerStore(tmp_path / "ledger")
    registry = build_project_tool_registry(store, tmp_path / "artifacts")
    text = "bounded audit evidence\n" * 400 + "TERMINAL_MARKER"
    written = registry.invoke(
        "artifact_write",
        {"path": "medium.txt", "text": text},
    )
    server = build_mcp_bridge(registry, allowed_tools=("artifact_read",))

    async def flow():
        result, structured = await server.reasoning_call_tool(
            "artifact_read", {"path": "medium.txt"}
        )
        delivered = json.loads(result[0].text)
        assert delivered["text"] == text
        assert delivered["sha256"] == written["sha256"]
        assert structured["text"] == text
        assert "artifact_manifest" not in delivered

    asyncio.run(flow())


def test_project_mcp_config_is_ephemeral_and_explicit(tmp_path):
    config = project_mcp_config(
        tmp_path / "ledger",
        tmp_path / "artifacts",
        allowed_tools=("ledger_status",),
    )
    assert config["command"] == "reasoning-ledger"
    assert config["args"][-2:] == ["--allow", "ledger_status"]

    bounded_config = project_mcp_config(
        tmp_path / "ledger",
        tmp_path / "artifacts",
        allowed_tools=("artifact_write", "ledger_status"),
        max_calls=7,
        completion_tool_calls={"ledger_status": 2},
        completion_artifacts=("research/result.md",),
    )
    assert bounded_config["args"][-8:] == [
        "--max-calls",
        "7",
        "--require-call",
        "ledger_status=2",
        "--require-artifact",
        "research/result.md",
        "--allow",
        "artifact_write,ledger_status",
    ]


    scientific_config = project_mcp_config(
        tmp_path / "ledger",
        tmp_path / "artifacts",
        scientific_cache_root=tmp_path / "scientific-cache",
        allowed_tools=("arxiv_search",),
    )
    scientific_index = scientific_config["args"].index("--scientific-cache")
    assert scientific_config["args"][scientific_index + 1] == str(
        tmp_path / "scientific-cache"
    )

    bibliography_config = project_mcp_config(
        tmp_path / "ledger",
        tmp_path / "artifacts",
        allowed_tools=("bibliography_search",),
        bibliography_root=tmp_path / "global-bibliography",
        project_id="project-a",
        chat_id="chat-1",
    )
    bibliography_args = bibliography_config["args"]
    bibliography_index = bibliography_args.index("--bibliography-root")
    assert bibliography_args[bibliography_index : bibliography_index + 4] == [
        "--bibliography-root",
        str(tmp_path / "global-bibliography"),
        "--project-id",
        "project-a",
    ]
    assert bibliography_args[bibliography_index + 4 : bibliography_index + 6] == [
        "--chat-id",
        "chat-1",
    ]
    assert bibliography_args[-2:] == ["--allow", "bibliography_search"]

    workflow_config = project_mcp_config(
        tmp_path / "ledger",
        tmp_path / "artifacts",
        allowed_tools=("workflow_create", "workflow_start"),
        project_id="project-a",
        control_url="http://127.0.0.1:43119",
    )
    workflow_args = workflow_config["args"]
    control_index = workflow_args.index("--control-url")
    assert workflow_args[control_index + 1] == "http://127.0.0.1:43119"
    assert workflow_args[-2:] == [
        "--allow",
        "workflow_create,workflow_start",
    ]

    with pytest.raises(ValueError, match="project_id"):
        project_mcp_config(
            tmp_path / "ledger",
            tmp_path / "artifacts",
            control_url="http://127.0.0.1:43119",
        )


def test_workflow_tools_create_reviewable_reasoning_drafts_and_guard_start(tmp_path):
    class WorkflowControl:
        def __init__(self):
            self.prompt = {
                "prompt_id": "research-role",
                "revision_id": "prmrev_1",
                "name": "Research role",
                "kind": "system",
            }
            self.graph = {
                "workflow_id": "compare-models",
                "revision_id": "workflowrev_1",
                "name": "Compare models",
                "nodes": [
                    {
                        "id": "model",
                        "kind": "agent",
                        "config": {"effort": "high"},
                    }
                ],
                "edges": [],
            }
            self.started = None
            self.dataset = {
                "dataset_id": "public-benchmark",
                "revision_id": "dsrev_1",
                "name": "Public benchmark",
                "description": "Downloaded fixture",
                "cases": [
                    {"case_id": "one", "name": "One", "inputs": {"question": "1 + 1"}, "expected": "2"}
                ],
            }
            self.complete_graph = None

        def prompt_list(self, *, project_id):
            assert project_id == "project-a"
            return {
                "prompts": [
                    self.prompt,
                    {
                        "prompt_id": "task",
                        "revision_id": "prmrev_task",
                        "name": "Task",
                        "kind": "user",
                    },
                ]
            }

        def prompt_definition(self, *, project_id, prompt_id, revision_id=None):
            assert project_id == "project-a"
            assert prompt_id == "research-role"
            assert revision_id in (None, "prmrev_1")
            return {**self.prompt, "template": "Reason carefully."}

        def prompt_create(
            self,
            *,
            project_id,
            name,
            kind,
            template,
            description="",
            variable_schema=None,
        ):
            assert project_id == "project-a"
            assert (name, kind, template) == (
                "Research role",
                "system",
                "Reason carefully.",
            )
            assert description == "A role prompt."
            assert variable_schema == {}
            return self.prompt

        def prompt_revise(
            self,
            *,
            project_id,
            prompt_id,
            template,
            parent_revision_id,
            name=None,
            kind=None,
            description=None,
            variable_schema=None,
        ):
            assert project_id == "project-a"
            assert (prompt_id, template, parent_revision_id) == (
                "research-role",
                "Reason very carefully.",
                "prmrev_1",
            )
            self.prompt = {**self.prompt, "revision_id": "prmrev_2"}
            return self.prompt

        def dataset_list(self, *, project_id):
            assert project_id == "project-a"
            return {"datasets": [{key: self.dataset[key] for key in ("dataset_id", "revision_id", "name")}]}

        def dataset_definition(self, *, project_id, dataset_id, revision_id=None):
            assert project_id == "project-a"
            assert dataset_id == "public-benchmark"
            assert revision_id in (None, "dsrev_1")
            return self.dataset

        def dataset_create(self, *, project_id, name, description="", cases):
            assert (project_id, name, description) == (
                "project-a",
                "Public benchmark",
                "Downloaded fixture",
            )
            assert cases == self.dataset["cases"]
            return self.dataset

        def model_catalog(self, *, project_id, scope="account", refresh=False):
            assert (project_id, scope, refresh) == ("project-a", "account", True)
            return {
                "source": {"provider": "openrouter", "status": "live"},
                "models": [
                    {
                        "model_id": "provider/medium",
                        "input_modalities": ["text"],
                        "output_modalities": ["text"],
                        "supported_parameters": ["structured_outputs"],
                        "supports_tools": False,
                        "supports_reasoning": False,
                        "input_mtok_usd": 0.2,
                        "output_mtok_usd": 0.8,
                    },
                    {
                        "model_id": "provider/free",
                        "input_modalities": ["text"],
                        "output_modalities": ["text"],
                        "supported_parameters": ["structured_outputs"],
                        "supports_tools": False,
                        "supports_reasoning": False,
                        "input_mtok_usd": 0.0,
                        "output_mtok_usd": 0.0,
                    },
                    {
                        "model_id": "provider/no-structured-output",
                        "input_modalities": ["text"],
                        "output_modalities": ["text"],
                        "supported_parameters": [],
                        "supports_tools": False,
                        "supports_reasoning": False,
                        "input_mtok_usd": 0.01,
                        "output_mtok_usd": 0.01,
                    },
                    {
                        "model_id": "provider/agentic-only",
                        "description": "Only available for use with agentic harnesses.",
                        "input_modalities": ["text"],
                        "output_modalities": ["text"],
                        "supported_parameters": ["structured_outputs"],
                        "supports_tools": False,
                        "supports_reasoning": False,
                        "input_mtok_usd": 0.0,
                        "output_mtok_usd": 0.0,
                    },
                ],
            }

        def workflow_list(self, *, project_id, include_archived=False):
            assert project_id == "project-a"
            return {"workflows": [self.graph], "include_archived": include_archived}

        def workflow_definition(self, *, project_id, workflow_id, revision_id=None):
            assert project_id == "project-a"
            assert workflow_id == "compare-models"
            assert revision_id in (None, "workflowrev_1")
            return self.graph

        def workflow_create(self, *, project_id, name, template_id=None, graph=None):
            if graph is not None:
                assert (project_id, name, template_id) == (
                    "project-a",
                    "Atomic benchmark",
                    None,
                )
                self.complete_graph = graph
                return self.graph
            assert (project_id, name, template_id) == (
                "project-a",
                "My comparison",
                "compare-models",
            )
            return self.graph

        def workflow_update(
            self,
            *,
            project_id,
            workflow_id,
            graph,
            expected_revision_id,
        ):
            assert project_id == "project-a"
            assert workflow_id == "compare-models"
            assert expected_revision_id == "workflowrev_1"
            assert graph["nodes"][0]["config"]["effort"] == "high"
            return self.graph

        def workflow_preflight(self, *, project_id, workflow_id, refresh=False):
            assert (project_id, workflow_id) == ("project-a", "compare-models")
            return {"ready": True, "refresh": refresh}

        def workflow_start(
            self,
            *,
            project_id,
            workflow_id,
            expected_revision_id,
            iterations=None,
            variables=None,
            initial_prompt="",
        ):
            self.started = {
                "project_id": project_id,
                "workflow_id": workflow_id,
                "expected_revision_id": expected_revision_id,
                "iterations": iterations,
                "variables": variables,
                "initial_prompt": initial_prompt,
            }
            return {"run_id": "wfrun_1", "status": "pending"}

    control = WorkflowControl()
    registry = build_project_tool_registry(
        LedgerStore(tmp_path / "ledger"),
        tmp_path / "artifacts",
        project_id="project-a",
        workflow_control=control,
    )
    names = {tool.name for tool in registry.list()}
    assert {
        "prompt_list",
        "prompt_read",
        "prompt_create",
        "prompt_revise",
        "dataset_list",
        "dataset_read",
        "dataset_create",
        "model_catalog",
        "workflow_list",
        "workflow_read",
        "workflow_create",
        "workflow_update",
        "workflow_preflight",
        "workflow_start",
    }.issubset(names)

    listed = registry.invoke("prompt_list", {"kind": "system"})
    assert [prompt["prompt_id"] for prompt in listed["prompts"]] == [
        "research-role"
    ]
    read_prompt = registry.invoke(
        "prompt_read",
        {"prompt_id": "research-role", "revision_id": "prmrev_1"},
    )
    assert read_prompt["prompt"]["template"] == "Reason carefully."
    created_prompt = registry.invoke(
        "prompt_create",
        {
            "name": "Research role",
            "kind": "system",
            "template": "Reason carefully.",
            "description": "A role prompt.",
            "variable_schema": {},
        },
    )
    assert created_prompt["prompt"]["revision_id"] == "prmrev_1"
    revised_prompt = registry.invoke(
        "prompt_revise",
        {
            "prompt_id": "research-role",
            "template": "Reason very carefully.",
            "parent_revision_id": "prmrev_1",
        },
    )
    assert revised_prompt["prompt"]["revision_id"] == "prmrev_2"

    assert registry.invoke("dataset_list", {})["datasets"][0]["revision_id"] == "dsrev_1"
    assert registry.invoke(
        "dataset_read",
        {"dataset_id": "public-benchmark", "revision_id": "dsrev_1"},
    )["dataset"]["cases"][0]["expected"] == "2"
    created_dataset = registry.invoke(
        "dataset_create",
        {
            "name": "Public benchmark",
            "description": "Downloaded fixture",
            "cases": control.dataset["cases"],
        },
    )
    assert created_dataset["dataset"]["revision_id"] == "dsrev_1"
    cheapest = registry.invoke(
        "model_catalog",
        {
            "scope": "account",
            "refresh": True,
            "limit": 2,
            "require_structured_output": True,
        },
    )
    assert [model["model_id"] for model in cheapest["models"]] == [
        "provider/free",
        "provider/medium",
    ]
    assert cheapest["selection"]["eligible_count"] == 2

    complete_graph = {
        "dataset_ref": {"dataset_id": "public-benchmark", "revision_id": "dsrev_1"},
        "variants": [
            {
                "variant_id": "free",
                "name": "Free",
                "node_overrides": {"candidate": {"model": "provider/free"}},
            },
            {
                "variant_id": "medium",
                "name": "Medium",
                "node_overrides": {"candidate": {"model": "provider/medium"}},
            },
        ],
        "nodes": [{"id": "candidate", "kind": "agent", "config": {}}],
        "edges": [],
    }
    atomic = registry.invoke(
        "workflow_create",
        {"name": "Atomic benchmark", "graph": complete_graph},
    )
    assert atomic["status"] == "draft"
    assert control.complete_graph == complete_graph
    assert atomic["next_step"].startswith("Call workflow_preflight")

    created = registry.invoke(
        "workflow_create",
        {"name": "My comparison", "template_id": "compare-models"},
    )
    assert created["status"] == "draft"
    assert created["review_required"] is True
    assert created["workflow"]["nodes"][0]["config"]["effort"] == "high"
    assert "presentation.recipe" in created["authoring_note"]
    assert "plain-language name" in created["authoring_note"]
    updated = registry.invoke(
        "workflow_update",
        {
            "workflow_id": "compare-models",
            "expected_revision_id": "workflowrev_1",
            "graph": control.graph,
        },
    )
    assert updated["status"] == "draft"
    preflight = registry.invoke(
        "workflow_preflight",
        {"workflow_id": "compare-models", "refresh": True},
    )
    assert preflight == {
        "workflow_id": "compare-models",
        "revision_id": "workflowrev_1",
        "preflight": {"ready": True, "refresh": True},
    }

    for unauthorized in (
        {
            "workflow_id": "compare-models",
            "expected_revision_id": "workflowrev_1",
        },
        {
            "workflow_id": "compare-models",
            "expected_revision_id": "workflowrev_1",
            "user_authorized": False,
        },
    ):
        with pytest.raises(ValidationError):
            registry.invoke("workflow_start", unauthorized)

    started = registry.invoke(
        "workflow_start",
        {
            "workflow_id": "compare-models",
            "expected_revision_id": "workflowrev_1",
            "user_authorized": True,
            "iterations": 3,
            "variables": {"question": "Why?"},
        },
    )
    assert started == {"run_id": "wfrun_1", "status": "pending"}
    assert control.started == {
        "project_id": "project-a",
        "workflow_id": "compare-models",
        "expected_revision_id": "workflowrev_1",
        "iterations": 3,
        "variables": {"question": "Why?"},
        "initial_prompt": "",
    }


def test_project_skill_tool_is_pinned_and_confined_to_granted_revisions(tmp_path):
    skills = SkillRegistry(tmp_path / "skills")
    selected = skills.create(
        name="selected-skill",
        description="A selected skill.",
        instructions="Read the supporting method only when needed.",
    )
    other = skills.create(
        name="other-skill",
        description="Another skill.",
        instructions="This revision is not selected.",
    )
    binding = SkillBinding(selected.skill_id, selected.revision_id)
    registry = build_project_tool_registry(
        LedgerStore(tmp_path / "ledger"),
        tmp_path / "artifacts",
        skill_registry=skills,
        allowed_skills=(binding,),
    )

    activated = registry.invoke(
        "skill_read",
        {"skill_id": selected.skill_id, "revision_id": selected.revision_id},
    )
    assert "Read the supporting method" in activated["content"]
    with pytest.raises(ValueError, match="not granted"):
        registry.invoke(
            "skill_read",
            {"skill_id": other.skill_id, "revision_id": other.revision_id},
        )

    config = project_mcp_config(
        tmp_path / "ledger",
        tmp_path / "artifacts",
        allowed_tools=("skill_read",),
        skills_root=skills.root,
        skills=(binding,),
    )
    assert config["args"][-6:] == [
        "--skills-root",
        str(skills.root),
        "--skill",
        f"{selected.skill_id}@{selected.revision_id}",
        "--allow",
        "skill_read",
    ]
