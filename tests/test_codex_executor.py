import json
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest

from reasoning_ledger.agents import (
    AgentRunSpec,
    CodexWorkflowExecutor,
    CompletionArtifactRequirement,
    PreparedAgentRun,
    PreparedTool,
    PromptBinding,
    RunBudget,
    SandboxPolicy,
    ToolBinding,
)
from reasoning_ledger.codex_client import CodexClientError


def _request(
    tmp_path,
    message="reason",
    *,
    timeout=10,
    max_tool_calls=2,
    model_provider="openrouter",
):
    tool = PreparedTool("ledger_status", "Read status", {"type": "object"})
    spec = AgentRunSpec(
        executor_id="codex",
        model="stealth/ox-alpha",
        model_provider=model_provider,
        system_prompt=PromptBinding("role", "s1"),
        user_prompt=PromptBinding("task", "u1"),
        tools=(ToolBinding("ledger_status"),),
        budget=RunBudget(
            max_cost_usd=1,
            max_output_tokens=20,
            max_tool_calls=max_tool_calls,
            timeout_seconds=timeout,
        ),
        sandbox=SandboxPolicy(mode="read_only"),
        effort="low",
    )
    return PreparedAgentRun(
        run_id="run_codex",
        spec=spec,
        system_prompt="Use the exact Reasoning research contract.",
        user_prompt=message,
        prompt_hashes={"system": "a", "user": "b"},
        context_messages=({"role": "user", "content": "<reasoning-agent-output>prior</reasoning-agent-output>"},),
        tools=(tool,),
        mcp_servers={
            "reasoning": {
                "command": "reasoning-ledger",
                "args": ["agent-tools", "--allow", "ledger_status"],
            }
        },
    )


def _executor(tmp_path):
    log = tmp_path / "requests.jsonl"
    fake = Path(__file__).parent / "fake_appserver.py"
    return (
        CodexWorkflowExecutor(
            (sys.executable, str(fake)),
            environment={"CODEX_APPSERVER_REQUEST_LOG": str(log)},
            outer_sandbox=False,
            model_options=("gpt-5.6-sol",),
            completion_idle_steer_seconds=0.05,
            tool_budget_idle_steer_seconds=0.05,
        ),
        log,
    )


def _deadline_executor(tmp_path):
    log = tmp_path / "deadline-requests.jsonl"
    fake = Path(__file__).parent / "fake_appserver.py"
    return (
        CodexWorkflowExecutor(
            (sys.executable, str(fake)),
            environment={"CODEX_APPSERVER_REQUEST_LOG": str(log)},
            outer_sandbox=False,
            deadline_steer_min_timeout_seconds=0,
        ),
        log,
    )


def test_codex_accepts_optional_tool_ceiling_without_budget_steering(tmp_path):
    executor, log = _executor(tmp_path)
    request = _request(tmp_path, max_tool_calls=None)
    request = replace(request, spec=replace(request.spec, completion_markers=("well then",)))
    result = executor.execute(request)
    assert result.status == "completed"
    entries = [json.loads(line) for line in log.read_text().splitlines()]
    assert not any(entry.get("method") == "turn/steer" for entry in entries)
    assert "no additional Reasoning tool-call ceiling" in json.dumps(entries)


def test_codex_workflow_executor_routes_provider_context_tools_and_usage(tmp_path):
    executor, log = _executor(tmp_path)
    events = []

    result = executor.execute(_request(tmp_path), events.append)

    assert result.status == "completed"
    assert result.output_text == "well then"
    assert result.usage == {
        "input_tokens": 40,
        "cached_input_tokens": 10,
        "cache_write_input_tokens": 0,
        "output_tokens": 10,
        "reasoning_output_tokens": 5,
        "total_tokens": 50,
    }
    assert result.metadata == {
        "model_provider": "openrouter",
        "thread_id": "t-0",
        "turn_status": "completed",
        "mcp_servers_ready": ["reasoning"],
        "tool_calls": 0,
        "max_cost_usd_enforced": False,
        "tool_budget_steer_attempted": False,
        "tool_budget_steer_succeeded": False,
        "output_budget_steer_attempted": False,
        "output_budget_steer_succeeded": False,
        "deadline_steer_attempted": False,
        "deadline_steer_succeeded": False,
    }
    entries = [json.loads(line) for line in log.read_text().splitlines()]
    argv = entries[0]["argv"]
    assert any("mcp_servers.reasoning.command" in value for value in argv)
    assert "apps" in argv
    thread = next(entry["params"] for entry in entries if entry.get("method") == "thread/start")
    assert thread["model"] == "stealth/ox-alpha"
    assert thread["modelProvider"] == "openrouter"
    assert thread["developerInstructions"] == "Use the exact Reasoning research contract."
    assert thread["ephemeral"] is True
    assert len(thread["runtimeWorkspaceRoots"]) == 1
    assert Path(thread["runtimeWorkspaceRoots"][0]).name.startswith("reasoning-codex-workflow-")
    mcp_status = next(
        entry["params"] for entry in entries if entry.get("method") == "mcpServerStatus/list"
    )
    assert mcp_status == {
        "threadId": "t-0",
        "detail": "toolsAndAuthOnly",
        "limit": 100,
    }
    methods = [entry.get("method") for entry in entries]
    assert methods.index("mcpServerStatus/list") < methods.index("turn/start")
    turn = next(entry["params"] for entry in entries if entry.get("method") == "turn/start")
    assert [item["text"] for item in turn["input"]] == [
        "<reasoning-agent-output>prior</reasoning-agent-output>",
        "reason",
    ]
    assert turn["effort"] == "low"
    kinds = [event.kind.value for event in events]
    assert kinds[0] == "run.started"
    assert "reasoning.delta" in kinds
    assert "message.completed" in kinds
    assert "usage" in kinds
    assert kinds[-1] == "run.completed"


def test_codex_workflow_executor_makes_completion_artifacts_mcp_only(tmp_path):
    request = _request(tmp_path)
    request = PreparedAgentRun(
        **{
            **request.__dict__,
            "spec": AgentRunSpec(
                **{
                    **request.spec.__dict__,
                    "completion_artifacts": (
                        CompletionArtifactRequirement("research/result.md"),
                    ),
                    "completion_markers": ("RESULT_COMPLETE",),
                    "completion_tool_calls": {"ledger_status": 1},
                }
            ),
        }
    )

    instructions = CodexWorkflowExecutor._developer_instructions(request)

    assert instructions.startswith(request.system_prompt)
    assert "only with the granted Reasoning `artifact_write` MCP tool" in instructions
    assert "workspace is ephemeral" in instructions
    assert "raw TeX backslash" in instructions
    assert "research/result.md" in instructions
    assert "RESULT_COMPLETE" in instructions
    assert "ledger_status: at least 1" in instructions
    assert "list_mcp_resources" in instructions
    assert "ledger_status" in instructions
    assert "needs at least 2 of the 2 permitted tool calls" in instructions
    assert "At most 0 calls are available" in instructions


@pytest.mark.parametrize(
    ("policy", "isolated", "expected"),
    [
        ("read_only", True, "read-only"),
        ("research", True, "read-only"),
        ("workspace_write", True, "danger-full-access"),
        ("write", True, "danger-full-access"),
        ("workspace_write", False, "read-only"),
    ],
)
def test_codex_workflow_executor_preserves_inner_read_only_boundary(
    tmp_path, policy, isolated, expected
):
    request = _request(tmp_path)
    request = PreparedAgentRun(
        **{
            **request.__dict__,
            "spec": AgentRunSpec(
                **{
                    **request.spec.__dict__,
                    "sandbox": SandboxPolicy(mode=policy),
                }
            ),
        }
    )

    assert (
        CodexWorkflowExecutor._codex_sandbox_mode(request, isolated=isolated)
        == expected
    )


def test_codex_workflow_executor_accepts_scoped_reasoning_elicitation_and_records_success(tmp_path):
    executor, _log = _executor(tmp_path)
    events = []

    result = executor.execute(_request(tmp_path, "mcp"), events.append)

    assert result.status == "completed"
    assert result.output_text == "mcp-ok"
    assert result.metadata["tool_calls"] == 1
    assert result.metadata["successful_tool_calls"] == {"ledger_status": 1}
    assert [event.kind.value for event in events if event.kind.value.startswith("tool.")] == ["tool.requested", "tool.completed"]


def test_codex_workflow_executor_still_declines_command_approval(tmp_path):
    executor, _log = _executor(tmp_path)

    result = executor.execute(_request(tmp_path, "approve"))

    assert result.status == "completed"
    assert result.output_text == "decision=decline"


def test_codex_workflow_executor_enables_the_openai_code_mode_tool_gateway(tmp_path):
    executor, log = _executor(tmp_path)

    result = executor.execute(_request(tmp_path, model_provider="openai"))

    assert result.status == "completed"
    argv = json.loads(log.read_text().splitlines()[0])["argv"]
    feature_modes = {
        argv[index + 1]: argv[index]
        for index in range(len(argv) - 1)
        if argv[index] in {"--enable", "--disable"}
    }
    assert feature_modes["code_mode_host"] == "--enable"


def test_codex_workflow_executor_rejects_an_incomplete_mcp_inventory(tmp_path):
    class MissingInventory:
        def request(self, method, params, *, timeout):
            assert method == "mcpServerStatus/list"
            return {"data": [], "nextCursor": None}

    with pytest.raises(CodexClientError, match="required MCP servers: reasoning"):
        CodexWorkflowExecutor._await_mcp_inventory(
            MissingInventory(),
            _request(tmp_path),
            "t-0",
            timeout=1,
        )


def test_codex_workflow_executor_interrupts_timeout(tmp_path):
    executor, _log = _executor(tmp_path)

    result = executor.execute(_request(tmp_path, "wait", timeout=0.5))

    assert result.status == "failed"
    assert result.error == "Codex exceeded 0.5s timeout"
    assert result.metadata["turn_status"] == "interrupted"


def test_codex_workflow_executor_steers_before_a_long_deadline(tmp_path):
    executor, log = _deadline_executor(tmp_path)

    result = executor.execute(_request(tmp_path, "wait", timeout=0.5))

    assert result.status == "completed"
    assert result.output_text.startswith("steered:Reasoning deadline:")
    assert result.metadata["deadline_steer_attempted"] is True
    assert result.metadata["deadline_steer_succeeded"] is True
    entries = [json.loads(line) for line in log.read_text().splitlines()]
    steer = next(entry["params"] for entry in entries if entry.get("method") == "turn/steer")
    assert steer["expectedTurnId"] == "turn-1"
    assert "persist every required artifact" in steer["input"][0]["text"]


def test_codex_workflow_executor_steers_when_the_last_tool_call_finishes(tmp_path):
    executor, log = _executor(tmp_path)

    result = executor.execute(_request(tmp_path, "toolwait", timeout=10, max_tool_calls=1))

    assert result.status == "completed"
    assert result.output_text.startswith("steered:Reasoning tool-call budget")
    assert result.metadata["tool_calls"] == 1
    assert result.metadata["tool_budget_steer_attempted"] is True
    assert result.metadata["tool_budget_steer_succeeded"] is True
    entries = [json.loads(line) for line in log.read_text().splitlines()]
    steer = next(entry["params"] for entry in entries if entry.get("method") == "turn/steer")
    assert "Do not call another tool" in steer["input"][0]["text"]


def test_codex_workflow_executor_finalizes_a_satisfied_contract_at_the_exact_cap(
    tmp_path,
):
    executor, log = _executor(tmp_path)
    request = _request(tmp_path, "contractwait", timeout=10, max_tool_calls=1)
    request = PreparedAgentRun(
        **{
            **request.__dict__,
            "spec": AgentRunSpec(
                **{
                    **request.spec.__dict__,
                    "completion_tool_calls": {"ledger_status": 1},
                }
            ),
        }
    )

    result = executor.execute(request)

    assert result.status == "completed"
    assert result.metadata["tool_calls"] == 1
    assert result.metadata["successful_tool_calls"] == {"ledger_status": 1}
    assert result.output_text.startswith(
        "steered:Reasoning confirms that every required successful tool call"
    )
    entries = [json.loads(line) for line in log.read_text().splitlines()]
    steer = next(entry["params"] for entry in entries if entry.get("method") == "turn/steer")
    assert "Do not repeat any tool call" in steer["input"][0]["text"]


def test_codex_workflow_executor_records_exact_cap_success_before_emitting_event(
    tmp_path,
):
    executor, _log = _executor(tmp_path)
    request = _request(tmp_path, "contractwait", timeout=10, max_tool_calls=1)
    request = PreparedAgentRun(
        **{
            **request.__dict__,
            "spec": AgentRunSpec(
                **{
                    **request.spec.__dict__,
                    "completion_tool_calls": {"ledger_status": 1},
                }
            ),
        }
    )

    def slow_completed_event(event):
        if event.kind.value == "tool.completed":
            time.sleep(0.25)

    result = executor.execute(request, slow_completed_event)

    assert result.status == "completed"
    assert result.metadata["tool_calls"] == 1
    assert result.metadata["successful_tool_calls"] == {"ledger_status": 1}


def test_codex_workflow_executor_does_not_charge_bridge_budget_denials(tmp_path):
    executor, _log = _executor(tmp_path)
    request = _request(tmp_path, "exhausted", timeout=10, max_tool_calls=1)
    request = PreparedAgentRun(
        **{
            **request.__dict__,
            "spec": AgentRunSpec(
                **{
                    **request.spec.__dict__,
                    "completion_tool_calls": {"ledger_status": 1},
                }
            ),
        }
    )

    result = executor.execute(request)

    assert result.status == "completed"
    assert result.output_text == "budget enforced"
    assert result.metadata["tool_calls"] == 1
    assert result.metadata["tool_budget_calls_denied"] == 1
    assert result.metadata["successful_tool_calls"] == {"ledger_status": 1}


def test_codex_workflow_executor_reserves_output_for_completion(tmp_path):
    executor, log = _executor(tmp_path)
    request = _request(tmp_path, "outputwait", timeout=10)
    artifact_tool = PreparedTool(
        "artifact_write",
        "Write an artifact",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "text": {"type": "string"},
            },
            "required": ["path", "text"],
        },
    )
    request = PreparedAgentRun(
        **{
            **request.__dict__,
            "tools": (*request.tools, artifact_tool),
            "spec": AgentRunSpec(
                **{
                    **request.spec.__dict__,
                    "tools": (*request.spec.tools, ToolBinding("artifact_write")),
                    "completion_artifacts": (
                        CompletionArtifactRequirement("research/result.md"),
                    ),
                }
            ),
        }
    )

    result = executor.execute(request)

    assert result.status == "completed"
    assert result.output_text.startswith("steered:Reasoning output-token budget")
    assert result.metadata["output_budget_steer_attempted"] is True
    assert result.metadata["output_budget_steer_succeeded"] is True
    entries = [json.loads(line) for line in log.read_text().splitlines()]
    steer = next(entry["params"] for entry in entries if entry.get("method") == "turn/steer")
    assert "persist every required artifact" in steer["input"][0]["text"]
    assert "approximately 13 output tokens" in steer["input"][0]["text"]


def test_codex_workflow_executor_steers_after_progress_message_with_completion_shortfall(
    tmp_path,
):
    executor, log = _executor(tmp_path)
    request = _request(tmp_path, "completionwait", timeout=10)
    request = PreparedAgentRun(
        **{
            **request.__dict__,
            "spec": AgentRunSpec(
                **{
                    **request.spec.__dict__,
                    "completion_tool_calls": {"ledger_status": 1},
                }
            ),
        }
    )

    result = executor.execute(request)

    assert result.status == "completed"
    assert result.metadata["successful_tool_calls"] == {"ledger_status": 1}
    assert result.metadata["completion_shortfall_steer_attempted"] is True
    assert result.metadata["completion_shortfall_steer_succeeded"] is True
    entries = [json.loads(line) for line in log.read_text().splitlines()]
    steer = next(entry["params"] for entry in entries if entry.get("method") == "turn/steer")
    assert "ledger_status call 0/1" in steer["input"][0]["text"]


def test_codex_workflow_executor_rejects_a_terminal_completion_shortfall(tmp_path):
    executor, _log = _executor(tmp_path)
    request = _request(tmp_path, "quick", timeout=10)
    request = PreparedAgentRun(
        **{
            **request.__dict__,
            "spec": AgentRunSpec(
                **{
                    **request.spec.__dict__,
                    "completion_tool_calls": {"ledger_status": 1},
                }
            ),
        }
    )

    result = executor.execute(request)

    assert result.status == "failed"
    assert result.error == (
        "Codex completion requirements were not satisfied: ledger_status call 0/1"
    )


def test_codex_workflow_executor_does_not_charge_completion_reserve_denials(tmp_path):
    executor, _log = _executor(tmp_path)

    result = executor.execute(_request(tmp_path, "reserved", timeout=10, max_tool_calls=1))

    assert result.status == "completed"
    assert result.metadata["tool_calls"] == 1
    assert result.metadata["completion_tool_calls_denied"] == 1
    assert result.metadata["tool_budget_steer_attempted"] is True



def test_codex_workflow_executor_requires_explicit_provider(tmp_path):
    executor, _log = _executor(tmp_path)
    request = _request(tmp_path)
    request = PreparedAgentRun(
        **{
            **request.__dict__,
            "spec": AgentRunSpec(**{**request.spec.__dict__, "model_provider": None}),
        }
    )

    result = executor.execute(request)

    assert result.status == "failed"
    assert result.error == "Codex workflow nodes require an explicit model_provider"
