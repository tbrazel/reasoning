import sys
from pathlib import Path

from reasoning_ledger.codex_setup import (
    CodexConfigurationTest,
    native_openrouter_setup_prompt,
)


def test_native_setup_prompt_uses_codex_config_and_environment_key():
    prompt = native_openrouter_setup_prompt("provider/model")
    assert "$CODEX_HOME/config.toml" in prompt
    assert "Do not edit a project's `.codex/config.toml`" in prompt
    assert 'model = "provider/model"' in prompt
    assert 'model_provider = "openrouter"' in prompt
    assert 'env_key = "OPENROUTER_API_KEY"' in prompt
    assert 'wire_api = "responses"' in prompt
    assert "API key" in prompt


def test_fake_appserver_configuration_test_runs_one_real_tool_turn(tmp_path):
    fake = Path(__file__).parent / "fake_appserver.py"
    result = CodexConfigurationTest(
        [sys.executable, str(fake)],
        workdir=tmp_path / "codex-test",
        stderr_path=tmp_path / "codex-test.log",
        command_overridden=True,
        environment={"OPENROUTER_API_KEY": "secret-never-returned"},
    ).run(model="provider/model")

    assert result.status == "ready"
    assert result.model == "provider/model"
    assert "secret-never-returned" not in str(result.as_dict())
