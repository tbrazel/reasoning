from __future__ import annotations

import sys
from pathlib import Path

import reasoning_ledger.desktop_collaboration_runner as runner_module
from reasoning_ledger.desktop_collaboration_runner import DesktopCollaborationRunner


class _UnusedCloud:
    pass


def test_contributed_runner_executes_bounded_network_denied_codex_request(tmp_path: Path, monkeypatch) -> None:
    fake = Path(__file__).with_name("fake_appserver.py")
    monkeypatch.setenv("REASONING_CODEX_COMMAND", f"{sys.executable} {fake}")
    runner = DesktopCollaborationRunner(  # type: ignore[arg-type]
        _UnusedCloud(),
        tmp_path / "settings" / "runner.json",
        tmp_path,
    )
    try:
        lease = {
            "organization_id": "organization_test",
            "workspace_id": "workspace_test",
            "topic_id": "topic_test",
            "operation_id": "operation_test",
            "lease_id": "lease_test",
            "request": {
                "harness_id": "codex.local",
                "model": "gpt-5.6-sol",
                "reasoning_effort": "low",
                "prompt": "quick pooled answer",
                "timeout_seconds": 30,
                "max_output_tokens": 128,
                "network_access": "denied",
            },
        }
        state, content, usage = runner._execute(lease)
        assert state == "succeeded"
        assert content == "hello"
        assert usage is None

        lease["request"]["network_access"] = "unrestricted"
        state, content, usage = runner._execute(lease)
        assert state == "failed"
        assert "policy refused" in content
        assert usage is None
    finally:
        runner.close()


def test_metered_route_uses_local_key_and_returns_prompt_free_usage(tmp_path: Path, monkeypatch) -> None:
    class _OpenRouter:
        def __init__(self, *, api_key: str) -> None:
            assert api_key == "test-key"

        def complete(self, payload: dict, *, timeout: float) -> dict:
            assert payload["messages"][-1]["content"] == "shared API prompt"
            assert timeout == 30
            return {
                "id": "response_test",
                "model": "openrouter/auto",
                "choices": [{"message": {"content": "metered answer"}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 4,
                    "total_tokens": 16,
                    "cost": 0.002,
                },
            }

    monkeypatch.setattr(runner_module, "OpenRouterClient", _OpenRouter)
    runner = DesktopCollaborationRunner(  # type: ignore[arg-type]
        _UnusedCloud(), tmp_path / "settings" / "runner.json", tmp_path
    )
    runner._api_keys.set("openrouter", "test-key")
    try:
        state, content, usage = runner._execute(
            {
                "organization_id": "organization_test",
                "workspace_id": "workspace_test",
                "topic_id": "topic_test",
                "operation_id": "operation_api",
                "lease_id": "lease_api",
                "request": {
                    "harness_id": "openrouter.api",
                    "model": "openrouter/auto",
                    "reasoning_effort": "low",
                    "prompt": "shared API prompt",
                    "timeout_seconds": 30,
                    "max_output_tokens": 128,
                    "network_access": "unrestricted",
                },
            }
        )
        assert (state, content) == ("succeeded", "metered answer")
        assert usage == {
            "provider": "openrouter",
            "prompt_tokens": 12,
            "completion_tokens": 4,
            "total_tokens": 16,
            "cost_usd": 0.002,
        }
        record = next((tmp_path / "usage" / "openrouter-calls").glob("orcall_*.json")).read_text()
        assert "shared API prompt" not in record
    finally:
        runner.close()
