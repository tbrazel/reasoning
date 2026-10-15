"""Small native-Codex setup helpers used by the Connections screen.

Reasoning does not create a second Codex executor or override provider settings
on the command line.  Codex reads its own workbench-local ``config.toml``;
Reasoning supplies saved provider keys through the environment names referenced
by that file, binds resumed chats to its effective current provider, and can
run one bounded end-to-end test of the resulting setup.
"""

from __future__ import annotations

import json
import os
import threading
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import sandbox
from .codex_client import CodexAppServerClient, CodexClientError

DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
CODEX_TEST_MARKER = "REASONING_CODEX_READY"
CODEX_TEST_COMMAND_MARKER = "reasoning-codex-test"


def native_openrouter_setup_prompt(model: str) -> str:
    """A pasteable prompt for a Codex chat running inside Reasoning."""

    selected = model.strip()
    if not selected or any(character in selected for character in "\r\n"):
        raise ValueError("model must be a non-empty single-line model ID")
    model_value = json.dumps(selected)
    base_url = json.dumps(
        os.environ.get("OPENROUTER_BASE_URL", DEFAULT_OPENROUTER_BASE_URL).rstrip("/")
    )
    return f"""Configure this Reasoning Codex installation to use OpenRouter natively.

1. Edit Codex's user-level config, preserving unrelated settings: use
   `$CODEX_HOME/config.toml` when `CODEX_HOME` is set, otherwise
   `~/.codex/config.toml`. Do not edit a project's `.codex/config.toml`.
2. Set `model = {model_value}` and `model_provider = \"openrouter\"`.
3. Add or update this provider without writing or printing an API key:

```toml
[model_providers.openrouter]
name = \"OpenRouter\"
base_url = {base_url}
env_key = \"OPENROUTER_API_KEY\"
wire_api = \"responses\"
requires_openai_auth = false
```

4. Check that the TOML parses, then report only the non-secret keys changed.
Do not modify repository files."""


@dataclass(frozen=True)
class CodexConfigurationTestResult:
    status: str
    model: str
    tested_at: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {
            "status": self.status,
            "model": self.model,
            "tested_at": self.tested_at,
            "detail": self.detail,
        }


class CodexConfigurationTest:
    """Run one real Codex turn through its native configuration."""

    def __init__(
        self,
        command: list[str],
        *,
        workdir: Path,
        stderr_path: Path | None = None,
        command_overridden: bool = False,
        environment: dict[str, str] | None = None,
    ):
        self.command = list(command)
        self.workdir = workdir.resolve()
        self.stderr_path = stderr_path
        self.command_overridden = command_overridden
        self.environment = dict(environment or {})

    def _command(self) -> list[str]:
        if self.command_overridden:
            return list(self.command)
        return sandbox.wrap(
            self.command,
            rw=[self.workdir, sandbox.codex_home_bind()],
            chdir=self.workdir,
        )

    def _environment(self) -> dict[str, str] | None:
        inherited = sandbox.codex_env()
        if not self.environment:
            return inherited
        return {
            **(os.environ if inherited is None else inherited),
            **self.environment,
        }

    def run(self, *, model: str, timeout: float = 120.0) -> CodexConfigurationTestResult:
        selected = model.strip()
        if not selected:
            raise ValueError("model is required")
        self.workdir.mkdir(parents=True, exist_ok=True)
        notifications: list[dict[str, Any]] = []
        finished = threading.Event()
        client_holder: dict[str, CodexAppServerClient] = {}

        def on_notification(message: dict) -> None:
            notifications.append(message)
            if message.get("method") == "turn/completed":
                finished.set()

        def on_server_request(message: dict) -> None:
            client = client_holder.get("client")
            if client is None:
                return
            method = message.get("method")
            if method == "mcpServer/elicitation/request":
                client.respond(message["id"], {"action": "decline", "content": {}, "_meta": None})
            elif method in {
                "item/commandExecution/requestApproval",
                "item/fileChange/requestApproval",
            }:
                client.respond(message["id"], {"decision": "decline"})
            else:
                client.respond_error(message["id"], -32601, "unsupported setup-test request")

        client = CodexAppServerClient(
            self._command(),
            on_notification=on_notification,
            on_server_request=on_server_request,
            stderr_path=self.stderr_path,
            env=self._environment(),
            client_name="reasoning-codex-configuration-test",
        )
        client_holder["client"] = client
        try:
            client.start(timeout=min(timeout, 30.0))
            thread = client.request(
                "thread/start",
                {
                    "model": selected,
                    "cwd": str(self.workdir),
                    "approvalPolicy": "never",
                    "approvalsReviewer": "user",
                    "sandbox": "read-only",
                    "developerInstructions": (
                        "This is a bounded connection test. Follow the request exactly, "
                        "make no file changes, and perform no other work."
                    ),
                },
                timeout=min(timeout, 30.0),
            )
            client.request(
                "turn/start",
                {
                    "threadId": thread["thread"]["id"],
                    "input": [
                        {
                            "type": "text",
                            "text": (
                                "Reasoning Codex setup test. Use the shell exactly once to run "
                                f"`printf {CODEX_TEST_COMMAND_MARKER}`, then answer exactly "
                                f"`{CODEX_TEST_MARKER}`."
                            ),
                            "text_elements": [],
                        }
                    ],
                    "model": selected,
                    "effort": "low",
                },
                timeout=min(timeout, 30.0),
            )
            if not finished.wait(timeout):
                raise CodexClientError(f"Codex test timed out after {timeout:g}s")
            return self._assess(selected, notifications)
        except (CodexClientError, OSError, KeyError, TypeError, ValueError) as error:
            return CodexConfigurationTestResult(
                status="error",
                model=selected,
                tested_at=_timestamp(),
                detail=str(error)[:1000],
            )
        finally:
            with suppress(Exception):
                client.close()

    @staticmethod
    def _assess(
        model: str,
        notifications: list[dict[str, Any]],
    ) -> CodexConfigurationTestResult:
        command_completed = False
        agent_output = ""
        completed = False
        for message in notifications:
            method = str(message.get("method") or "")
            params = message.get("params") or {}
            item = params.get("item") or {}
            if (
                method == "item/completed"
                and item.get("type") == "commandExecution"
                and item.get("exitCode") == 0
                and CODEX_TEST_COMMAND_MARKER in str(item.get("aggregatedOutput") or "")
            ):
                command_completed = True
            if method == "item/completed" and item.get("type") == "agentMessage":
                agent_output += str(item.get("text") or "")
            if method == "turn/completed":
                completed = (params.get("turn") or {}).get("status") == "completed"
        ready = completed and command_completed and CODEX_TEST_MARKER in agent_output
        return CodexConfigurationTestResult(
            status="ready" if ready else "failed",
            model=model,
            tested_at=_timestamp(),
            detail=(
                "Codex completed a tool-using turn with this native configuration"
                if ready
                else "Codex did not complete the expected tool-using turn; check its config and log"
            ),
        )


def _timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")
