import json
import sys
from dataclasses import replace

from reasoning_ledger.agents import (
    AgentRunSpec,
    HermesAgentExecutor,
    HermesCliTransport,
    PreparedAgentRun,
    PreparedSkill,
    PreparedTool,
    PromptBinding,
    SandboxPolicy,
    SkillBinding,
    ToolBinding,
)

FAKE_HERMES = r'''import json
import os
import sys

args = sys.argv[1:]
usage_path = args[args.index("--usage-file") + 1]
json.dump({"model":"test/model","input_tokens":9,"output_tokens":3,"estimated_cost_usd":0.01}, open(usage_path, "w"))
invocation = {"args":args,"system":os.environ.get("HERMES_EPHEMERAL_SYSTEM_PROMPT")}
invocation.update({"rules":os.environ.get("HERMES_IGNORE_RULES"),"cwd":os.getcwd()})
json.dump(invocation, open(os.environ["HERMES_TEST_LOG"], "w"))
print("Hermes result")
'''

FAILED_HERMES = r'''import json
import sys

args = sys.argv[1:]
usage_path = args[args.index("--usage-file") + 1]
json.dump({"completed": False, "failed": True, "api_calls": 1}, open(usage_path, "w"))
print('HTTP 400: {"detail":"model is unavailable for this provider"}')
'''


def _request(tmp_path, *, output_schema=None):
    skill = PreparedSkill("literature", "sk1", "literature", "Search carefully")
    tool = PreparedTool("ledger_status", "Read the ledger", {"type": "object"})
    spec = AgentRunSpec(
        executor_id="hermes",
        model="openrouter/qwen/qwen3-coder",
        system_prompt=PromptBinding("student", "s1"),
        user_prompt=PromptBinding("work", "u1"),
        skills=(SkillBinding("literature", "sk1"),),
        tools=(ToolBinding("ledger_status"),),
        output_schema=output_schema,
        sandbox=SandboxPolicy(
            mode="workspace_write",
            working_directory=str(tmp_path / "work"),
            network=True,
        ),
    )
    return PreparedAgentRun(
        run_id="run_hermes",
        spec=spec,
        system_prompt="Act as the weekly student.",
        user_prompt="Try the next lemma.",
        prompt_hashes={"system": "a", "user": "b"},
        context_messages=({"role": "assistant", "content": "Last week: lemma 1"},),
        skills=(skill,),
        tools=(tool,),
        mcp_servers={"reasoning": {"command": "reasoning-ledger"}},
    )


def test_hermes_cli_uses_ephemeral_prompt_usage_and_explicit_toolsets(tmp_path):
    script = tmp_path / "fake_hermes.py"
    script.write_text(FAKE_HERMES, encoding="utf-8")
    log = tmp_path / "invocation.json"
    executor = HermesAgentExecutor(
        HermesCliTransport(
            (sys.executable, str(script)),
            provider="openrouter",
            environment={"HERMES_TEST_LOG": str(log)},
        )
    )
    events = []

    result = executor.execute(_request(tmp_path), events.append)

    assert result.status == "completed"
    assert result.output_text == "Hermes result"
    assert result.usage["estimated_cost_usd"] == 0.01
    assert [event.kind.value for event in events] == [
        "run.started",
        "message.delta",
        "message.completed",
        "usage",
        "run.completed",
    ]
    invocation = json.loads(log.read_text())
    args = invocation["args"]
    prompt = args[args.index("--oneshot") + 1]
    assert "<reasoning-context>" in prompt and "Last week: lemma 1" in prompt
    assert "<reasoning-user-prompt>\nTry the next lemma." in prompt
    assert args[args.index("--toolsets") + 1] == "terminal,file,code_execution,web,skills,reasoning"
    assert args[args.index("--skills") + 1] == "literature"
    assert args[args.index("--provider") + 1] == "openrouter"
    assert args[args.index("--model") + 1] == "qwen/qwen3-coder"
    assert "--yolo" not in args
    assert invocation["system"] == "Act as the weekly student."
    assert invocation["rules"] == "1"
    assert invocation["cwd"] == str(tmp_path / "work")


def test_hermes_cli_rejects_unsupported_structured_output(tmp_path):
    result = HermesAgentExecutor(
        HermesCliTransport((str(tmp_path / "does-not-run"),))
    ).execute(_request(tmp_path, output_schema={"type": "object"}))
    assert result.status == "failed"
    assert "does not expose structured-output" in result.error


def test_hermes_uses_run_scoped_skill_tools_for_materialized_packages(tmp_path):
    request = _request(tmp_path)
    materialized = replace(request.skills[0], directory=str(tmp_path / "skill"))
    request = replace(request, skills=(materialized,))
    transport = HermesCliTransport(("hermes",))

    assert transport._toolsets(request) == [
        "terminal",
        "file",
        "code_execution",
        "web",
        "reasoning",
    ]


def test_hermes_cli_infers_openrouter_and_honors_failed_usage_report(tmp_path):
    script = tmp_path / "failed_hermes.py"
    script.write_text(FAILED_HERMES, encoding="utf-8")
    result = HermesAgentExecutor(
        HermesCliTransport((sys.executable, str(script)))
    ).execute(_request(tmp_path))

    assert result.status == "failed"
    assert "HTTP 400" in result.error
    assert result.usage["failed"] is True
