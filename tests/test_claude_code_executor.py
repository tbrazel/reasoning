import json
import sys

from reasoning_ledger.agents import (
    AgentRunSpec,
    ClaudeCodeExecutor,
    PreparedAgentRun,
    PreparedTool,
    PromptBinding,
    RunBudget,
    SandboxPolicy,
    ToolBinding,
)

FAKE_CLAUDE = r'''import json
import os
import sys

args = sys.argv[1:]
mcp = None
if "--mcp-config" in args:
    path = args[args.index("--mcp-config") + 1]
    mcp = json.loads(open(path, encoding="utf-8").read())
json.dump({"args": args, "cwd": os.getcwd(), "mcp": mcp}, open(os.environ["CLAUDE_TEST_LOG"], "w"))
print(json.dumps({"type":"stream_event","event":{"delta":{"type":"text_delta","text":"Hello "}}}), flush=True)
tool_use = {"type":"tool_use","id":"tool_1","name":"mcp__reasoning__lookup","input":{"id":"clm_1"}}
print(json.dumps({"type":"assistant","message":{"content":[tool_use]}}), flush=True)
print(json.dumps({"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"tool_1","content":"found"}]}}), flush=True)
print(json.dumps({"type":"stream_event","event":{"delta":{"type":"text_delta","text":"world"}}}), flush=True)
result = {"type":"result","subtype":"success","is_error":False,"result":"Hello world"}
result.update({"session_id":"session-1","total_cost_usd":0.02})
result["usage"] = {"input_tokens":12,"output_tokens":4}
print(json.dumps(result), flush=True)
'''


def _request(tmp_path, *, timeout=10, external_mcp=False):
    tool = PreparedTool("lookup", "Look up a claim", {"type": "object"})
    spec = AgentRunSpec(
        executor_id="claude-code",
        model="sonnet",
        system_prompt=PromptBinding("advisor", "s1"),
        user_prompt=PromptBinding("meeting", "u1"),
        tools=(ToolBinding("lookup"),),
        mcp_connections=("scholar",) if external_mcp else (),
        budget=RunBudget(max_cost_usd=0.50, timeout_seconds=timeout),
        sandbox=SandboxPolicy(
            mode="workspace_write",
            working_directory=str(tmp_path / "work"),
            writable_paths=(str(tmp_path / "artifacts"),),
        ),
        effort="high",
    )
    return PreparedAgentRun(
        run_id="run_claude",
        spec=spec,
        system_prompt="You are an exacting advisor.",
        user_prompt="Review this week's result exactly.",
        prompt_hashes={"system": "a", "user": "b"},
        tools=(tool,),
        mcp_servers={
            "reasoning": {
                "command": "reasoning-ledger",
                "args": ["agent-tools"],
            },
            **(
                {
                    "scholar": {
                        "type": "http",
                        "url": "https://research.example/mcp",
                    }
                }
                if external_mcp
                else {}
            ),
        },
    )


def test_claude_code_streams_events_and_uses_explicit_controls(tmp_path):
    script = tmp_path / "fake_claude.py"
    script.write_text(FAKE_CLAUDE, encoding="utf-8")
    log = tmp_path / "invocation.json"
    events = []
    executor = ClaudeCodeExecutor(
        (sys.executable, str(script)),
        environment={"CLAUDE_TEST_LOG": str(log)},
    )

    result = executor.execute(_request(tmp_path), events.append)

    assert result.status == "completed"
    assert result.output_text == "Hello world"
    assert result.session_id == "session-1"
    assert result.usage == {"input_tokens": 12, "output_tokens": 4, "cost_usd": 0.02}
    assert [event.kind.value for event in events] == [
        "run.started",
        "message.delta",
        "tool.requested",
        "tool.completed",
        "message.delta",
        "message.completed",
        "usage",
        "run.completed",
    ]

    invocation = json.loads(log.read_text())
    args = invocation["args"]
    assert args[args.index("-p") + 1] == "Review this week's result exactly."
    assert args[args.index("--system-prompt") + 1] == "You are an exacting advisor."
    assert args[args.index("--permission-mode") + 1] == "dontAsk"
    assert "--dangerously-skip-permissions" not in args
    assert args[args.index("--tools") + 1] == "Read,Glob,Grep,Edit,Write,Bash"
    assert args[args.index("--allowedTools") + 1] == "mcp__reasoning__lookup"
    assert "--no-session-persistence" in args
    assert invocation["mcp"]["mcpServers"]["reasoning"]["command"] == "reasoning-ledger"
    assert invocation["cwd"] == str(tmp_path / "work")


def test_claude_code_start_failure_is_a_run_result(tmp_path):
    executor = ClaudeCodeExecutor((str(tmp_path / "missing-claude"),))
    events = []
    result = executor.execute(_request(tmp_path), events.append)
    assert result.status == "failed"
    assert "could not start Claude Code" in result.error
    assert events[-1].kind.value == "run.failed"


def test_claude_code_grants_selected_external_mcp_server(tmp_path):
    script = tmp_path / "fake_claude.py"
    script.write_text(FAKE_CLAUDE, encoding="utf-8")
    log = tmp_path / "invocation.json"
    executor = ClaudeCodeExecutor(
        (sys.executable, str(script)),
        environment={"CLAUDE_TEST_LOG": str(log)},
    )

    result = executor.execute(_request(tmp_path, external_mcp=True))

    assert result.status == "completed"
    invocation = json.loads(log.read_text())
    allowed = invocation["args"][invocation["args"].index("--allowedTools") + 1]
    assert allowed == "mcp__reasoning__lookup,mcp__scholar"
    assert invocation["mcp"]["mcpServers"]["scholar"]["url"] == (
        "https://research.example/mcp"
    )
