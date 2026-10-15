import json
import os
import sys
from dataclasses import replace

from reasoning_ledger.agents import (
    AgentRunSpec,
    OpenCodeExecutor,
    PreparedAgentRun,
    PreparedTool,
    PromptBinding,
    RunBudget,
    SandboxPolicy,
    SessionPolicy,
    ToolBinding,
)

FAKE_OPENCODE = r'''import json
import os
import sys

args = sys.argv[1:]
json.dump(
    {
        "args": args,
        "cwd": os.getcwd(),
        "config": json.loads(os.environ["OPENCODE_CONFIG_CONTENT"]),
        "config_dir": os.environ["OPENCODE_CONFIG_DIR"],
        "disable_plugins": os.environ.get("OPENCODE_DISABLE_DEFAULT_PLUGINS"),
    },
    open(os.environ["OPENCODE_TEST_LOG"], "w"),
)
session = "ses_reasoning"
def emit(kind, **values):
    print(json.dumps({"type": kind, "timestamp": 1, "sessionID": session, **values}), flush=True)

emit("step_start", part={"type":"step-start"})
emit("reasoning", part={"type":"reasoning", "text":"Check the source."})
emit("tool_use", part={
    "id":"part_1", "callID":"call_1", "tool":"reasoning_ledger_status",
    "state":{"status":"completed", "input":{}, "output":"ledger ok"},
})
emit("step_finish", part={
    "type":"step-finish", "cost":0.012,
    "tokens":{"input":10,"output":3,"reasoning":2,"total":15,"cache":{"read":4,"write":1}},
})
emit("text", part={"type":"text", "text":"A controlled answer."})
'''


def _request(tmp_path, *, session=None, output_schema=None):
    spec = AgentRunSpec(
        executor_id="opencode",
        model="openrouter/qwen/qwen3-coder",
        system_prompt=PromptBinding("role", "s1"),
        user_prompt=PromptBinding("turn", "u1"),
        tools=(ToolBinding("ledger_status"),),
        mcp_connections=("scholar",),
        output_schema=output_schema,
        budget=RunBudget(max_cost_usd=0.5, max_tool_calls=4, timeout_seconds=10),
        sandbox=SandboxPolicy(
            mode="read_only",
            working_directory=str(tmp_path / "work"),
            network=True,
        ),
        session=session or SessionPolicy(),
        effort="high",
    )
    return PreparedAgentRun(
        run_id="run_opencode",
        spec=spec,
        system_prompt="You are an exacting research advisor.",
        user_prompt="Review the result.",
        prompt_hashes={"system": "a", "user": "b"},
        context_messages=({"role": "assistant", "content": "Earlier result."},),
        tools=(PreparedTool("ledger_status", "Read status", {"type": "object"}),),
        mcp_servers={
            "reasoning": {
                "command": "reasoning-ledger",
                "args": ["agent-tools", "--allowed-tools", "ledger_status"],
                "env": {"REASONING_LEDGER_ROOT": str(tmp_path / "ledger")},
            },
            "scholar": {
                "type": "http",
                "url": "https://research.example/mcp",
                "headers": {"Authorization": "Bearer private"},
            },
        },
    )


def test_opencode_streams_events_usage_and_ephemeral_configuration(tmp_path):
    script = tmp_path / "fake_opencode.py"
    script.write_text(FAKE_OPENCODE, encoding="utf-8")
    log = tmp_path / "invocation.json"
    executor = OpenCodeExecutor(
        (sys.executable, str(script)),
        environment={"OPENCODE_TEST_LOG": str(log)},
    )
    events = []

    result = executor.execute(_request(tmp_path), events.append)

    assert result.status == "completed"
    assert result.output_text == "A controlled answer."
    assert result.session_id == "ses_reasoning"
    assert result.usage == {
        "cost_usd": 0.012,
        "input_tokens": 10,
        "output_tokens": 3,
        "reasoning_tokens": 2,
        "total_tokens": 15,
        "cache_read_tokens": 4,
        "cache_write_tokens": 1,
    }
    assert [event.kind.value for event in events] == [
        "run.started",
        "reasoning.delta",
        "tool.requested",
        "tool.completed",
        "message.delta",
        "message.completed",
        "usage",
        "run.completed",
    ]

    invocation = json.loads(log.read_text(encoding="utf-8"))
    args = invocation["args"]
    assert args[:4] == ["--pure", "run", "--format", "json"]
    assert args[args.index("--variant") + 1] == "high"
    assert args[-1].endswith(
        "<reasoning-user-prompt>\nReview the result.\n</reasoning-user-prompt>"
    )
    assert invocation["cwd"] == str(tmp_path / "work")
    assert invocation["disable_plugins"] == "1"
    config = invocation["config"]
    assert config["agent"]["reasoning"]["prompt"] == (
        "You are an exacting research advisor."
    )
    permissions = config["agent"]["reasoning"]["permission"]
    assert permissions["*"] == "deny"
    assert permissions["read"] == "allow"
    assert permissions.get("edit", "deny") == "deny"
    assert permissions["reasoning_*"] == "allow"
    assert permissions["scholar_*"] == "allow"
    assert config["mcp"]["reasoning"] == {
        "type": "local",
        "command": [
            "reasoning-ledger",
            "agent-tools",
            "--allowed-tools",
            "ledger_status",
        ],
        "enabled": True,
        "environment": {"REASONING_LEDGER_ROOT": str(tmp_path / "ledger")},
    }
    assert config["mcp"]["scholar"] == {
        "type": "remote",
        "url": "https://research.example/mcp",
        "enabled": True,
        "headers": {"Authorization": "Bearer private"},
        "oauth": False,
    }
    assert os.path.isdir(invocation["config_dir"]) is False


def test_opencode_optional_tool_ceiling_omits_steps_and_preserves_permissions(tmp_path):
    request = _request(tmp_path)
    request = replace(request, spec=replace(request.spec, budget=RunBudget(max_tool_calls=None)))
    config = OpenCodeExecutor(("opencode",))._configuration(request)
    assert "steps" not in config["agent"]["reasoning"]
    assert config["agent"]["reasoning"]["permission"]["*"] == "deny"
    assert config["agent"]["reasoning"]["permission"]["reasoning_*"] == "allow"


def test_opencode_resume_and_fork_are_explicit(tmp_path):
    executor = OpenCodeExecutor(("opencode",))
    request = _request(
        tmp_path,
        session=SessionPolicy(mode="fork", session_id="ses_parent"),
    )

    arguments = executor._arguments(request)

    assert arguments[arguments.index("--session") + 1] == "ses_parent"
    assert "--fork" in arguments
    assert "--auto" not in arguments
    assert "--dangerously-skip-permissions" not in arguments


def test_opencode_rejects_structured_output_before_starting(tmp_path):
    executor = OpenCodeExecutor((str(tmp_path / "missing-opencode"),))
    events = []

    result = executor.execute(
        _request(tmp_path, output_schema={"type": "object"}), events.append
    )

    assert result.status == "failed"
    assert "structured-output" in result.error
    assert [event.kind.value for event in events] == ["run.started", "run.failed"]


def test_opencode_start_failure_is_a_run_result(tmp_path):
    executor = OpenCodeExecutor((str(tmp_path / "missing-opencode"),))
    events = []

    result = executor.execute(_request(tmp_path), events.append)

    assert result.status == "failed"
    assert "could not start OpenCode" in result.error
    assert events[-1].kind.value == "run.failed"
