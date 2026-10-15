"""OpenCode CLI adapter with ephemeral prompts, permissions, and MCP grants."""

from __future__ import annotations

import json
import os
import re
import selectors
import shlex
import signal
import subprocess
import tempfile
import time
from collections.abc import Mapping, Sequence
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .contracts import (
    AgentEvent,
    AgentEventKind,
    AgentRunResult,
    EventSink,
    ExecutorCapabilities,
    PreparedAgentRun,
    PromptFidelity,
)


class OpenCodeError(RuntimeError):
    """OpenCode could not be started or returned an invalid event stream."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _tool_prefix(server: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", server)


def _bounded(value: Any, limit: int = 64 * 1024) -> Any:
    if not isinstance(value, str) or len(value) <= limit:
        return value
    return value[:limit] + "\n… output truncated by Reasoning …"


class _Events:
    def __init__(self, run_id: str, sink: EventSink | None):
        self.run_id = run_id
        self.sink = sink
        self.sequence = 0

    def emit(self, kind: AgentEventKind, data: dict[str, Any] | None = None) -> None:
        self.sequence += 1
        if self.sink:
            self.sink(
                AgentEvent(
                    run_id=self.run_id,
                    sequence=self.sequence,
                    kind=kind,
                    at=_now(),
                    data=data or {},
                )
            )


class OpenCodeExecutor:
    """Run an immutable Reasoning request through ``opencode run`` JSON events."""

    def __init__(
        self,
        command: Sequence[str] | str = ("opencode",),
        *,
        environment: Mapping[str, str] | None = None,
    ):
        self.command = tuple(shlex.split(command) if isinstance(command, str) else command)
        if not self.command:
            raise ValueError("OpenCode command cannot be empty")
        self.environment = dict(environment or {})

    @property
    def capabilities(self) -> ExecutorCapabilities:
        return ExecutorCapabilities(
            executor_id="opencode",
            display_name="OpenCode CLI",
            prompt_fidelity=PromptFidelity.CONTROLLED_INPUT,
            supports_tools=True,
            supports_skills=True,
            supports_sessions=True,
            supports_structured_output=False,
            supports_mcp=True,
            model_patterns=("*/*",),
            model_options=(
                "opencode/gpt-5.1-codex",
                "anthropic/claude-sonnet-4-5",
                "openrouter/qwen/qwen3-coder",
                "openrouter/openai/gpt-oss-120b",
            ),
        )

    @staticmethod
    def _user_prompt(request: PreparedAgentRun) -> str:
        if not request.context_messages:
            return request.user_prompt
        context = "\n\n".join(
            f"[{str(message.get('role', 'context')).upper()}]\n{message.get('content', '')}"
            for message in request.context_messages
        )
        return (
            "<reasoning-context>\n"
            f"{context}\n"
            "</reasoning-context>\n\n"
            "<reasoning-user-prompt>\n"
            f"{request.user_prompt}\n"
            "</reasoning-user-prompt>"
        )

    @staticmethod
    def _mcp(server: dict[str, Any]) -> dict[str, Any]:
        if server.get("command"):
            value: dict[str, Any] = {
                "type": "local",
                "command": [server["command"], *(server.get("args") or [])],
                "enabled": True,
            }
            if server.get("env"):
                value["environment"] = dict(server["env"])
            return value
        value = {
            "type": "remote",
            "url": server.get("url"),
            "enabled": True,
        }
        if server.get("headers"):
            value["headers"] = dict(server["headers"])
            value["oauth"] = False
        return value

    @staticmethod
    def _permissions(request: PreparedAgentRun) -> dict[str, Any]:
        permissions: dict[str, Any] = {
            "*": "deny",
            "read": "allow",
            "glob": "allow",
            "grep": "allow",
            "list": "allow",
            "question": "deny",
            "task": "deny",
            "skill": "deny",
            "doom_loop": "deny",
        }
        if request.spec.sandbox.mode in {"workspace_write", "write"}:
            permissions["edit"] = "allow"
            permissions["bash"] = "allow"
        if request.spec.sandbox.network:
            permissions["webfetch"] = "allow"
            permissions["websearch"] = "allow"
        external = [
            str(Path(path).expanduser().resolve())
            for path in request.spec.sandbox.writable_paths
        ]
        if external:
            permissions["external_directory"] = {
                "*": "deny",
                **{f"{path}/**": "allow" for path in external},
            }
        for server in request.mcp_servers:
            permissions[f"{_tool_prefix(server)}_*"] = "allow"
        return permissions

    def _configuration(self, request: PreparedAgentRun) -> dict[str, Any]:
        return {
            "$schema": "https://opencode.ai/config.json",
            "share": "disabled",
            "autoupdate": False,
            "instructions": [],
            "default_agent": "reasoning",
            "agent": {
                "reasoning": {
                    "description": "Ephemeral agent prepared by Reasoning",
                    "mode": "primary",
                    "prompt": request.system_prompt,
                    "model": request.spec.model,
                    **({"steps": max(1, request.spec.budget.max_tool_calls + 1)}
                       if request.spec.budget.max_tool_calls is not None else {}),
                    "permission": self._permissions(request),
                }
            },
            "mcp": {
                name: self._mcp(dict(server))
                for name, server in request.mcp_servers.items()
            },
        }

    def _arguments(self, request: PreparedAgentRun) -> list[str]:
        arguments = [
            *self.command,
            "--pure",
            "run",
            "--format",
            "json",
            "--agent",
            "reasoning",
            "--model",
            request.spec.model,
            "--title",
            f"Reasoning · {request.run_id}",
            "--thinking",
        ]
        if request.spec.effort:
            arguments.extend(("--variant", request.spec.effort))
        if request.spec.session.mode in {"resume", "fork"}:
            arguments.extend(("--session", request.spec.session.session_id or ""))
        if request.spec.session.mode == "fork":
            arguments.append("--fork")
        arguments.append(self._user_prompt(request))
        return arguments

    @staticmethod
    def _tokens(usage: dict[str, Any], value: Any) -> None:
        if not isinstance(value, dict):
            return
        mapping = {
            "input": "input_tokens",
            "output": "output_tokens",
            "reasoning": "reasoning_tokens",
            "total": "total_tokens",
        }
        for source, target in mapping.items():
            amount = value.get(source)
            if isinstance(amount, (int, float)) and not isinstance(amount, bool):
                usage[target] = usage.get(target, 0) + amount
        cache = value.get("cache")
        if isinstance(cache, dict):
            for source, target in (
                ("read", "cache_read_tokens"),
                ("write", "cache_write_tokens"),
            ):
                amount = cache.get(source)
                if isinstance(amount, (int, float)) and not isinstance(amount, bool):
                    usage[target] = usage.get(target, 0) + amount

    def execute(
        self,
        request: PreparedAgentRun,
        emit: EventSink | None = None,
    ) -> AgentRunResult:
        events = _Events(request.run_id, emit)
        events.emit(
            AgentEventKind.RUN_STARTED,
            {"executor": self.capabilities.executor_id, "model": request.spec.model},
        )
        if request.spec.output_schema:
            error = (
                "OpenCode CLI does not expose structured-output validation; "
                "use Reasoning Native or Claude Code"
            )
            events.emit(AgentEventKind.RUN_FAILED, {"error": error})
            return AgentRunResult(request.run_id, "failed", error=error)

        with tempfile.TemporaryDirectory(prefix="reasoning-opencode-") as temporary:
            temporary_path = Path(temporary)
            config_directory = temporary_path / "config"
            config_directory.mkdir()
            workdir = Path(request.spec.sandbox.working_directory or temporary_path)
            workdir.mkdir(parents=True, exist_ok=True)
            environment = {
                **os.environ,
                **self.environment,
                "OPENCODE_CONFIG_CONTENT": json.dumps(
                    self._configuration(request), ensure_ascii=False
                ),
                "OPENCODE_CONFIG_DIR": str(config_directory),
                "OPENCODE_DISABLE_AUTOUPDATE": "1",
                "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1",
                "OPENCODE_DISABLE_LSP_DOWNLOAD": "1",
                "OPENCODE_DISABLE_CLAUDE_CODE": "1",
            }
            try:
                process = subprocess.Popen(
                    self._arguments(request),
                    cwd=workdir,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    start_new_session=True,
                )
            except OSError as exc:
                error = f"could not start OpenCode: {exc}"
                events.emit(AgentEventKind.RUN_FAILED, {"error": error})
                return AgentRunResult(request.run_id, "failed", error=error)

            assert process.stdout is not None and process.stderr is not None
            selector = selectors.DefaultSelector()
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
            deadline = time.monotonic() + request.spec.budget.timeout_seconds
            output_parts: list[str] = []
            stderr_parts: list[str] = []
            usage: dict[str, Any] = {}
            session_id: str | None = None
            failure: str | None = None
            tool_calls = 0
            try:
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        failure = (
                            f"OpenCode exceeded {request.spec.budget.timeout_seconds:g}s timeout"
                        )
                        break
                    ready = selector.select(min(remaining, 0.25))
                    if not ready and process.poll() is not None:
                        for key in list(selector.get_map().values()):
                            selector.unregister(key.fileobj)
                        break
                    for key, _ in ready:
                        line = key.fileobj.readline()
                        if line == "":
                            selector.unregister(key.fileobj)
                            continue
                        if key.data == "stderr":
                            if sum(map(len, stderr_parts)) < 64 * 1024:
                                stderr_parts.append(line)
                            continue
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            stderr_parts.append(f"non-JSON stdout: {line}")
                            continue
                        if not isinstance(event, dict):
                            continue
                        session_id = str(event.get("sessionID") or session_id or "") or None
                        event_type = event.get("type")
                        part = event.get("part")
                        if not isinstance(part, dict):
                            part = {}
                        if event_type == "text":
                            text = part.get("text")
                            if isinstance(text, str) and text:
                                output_parts.append(text)
                                events.emit(AgentEventKind.MESSAGE_DELTA, {"text": text})
                        elif event_type == "reasoning":
                            text = part.get("text")
                            if isinstance(text, str) and text:
                                events.emit(AgentEventKind.REASONING_DELTA, {"text": text})
                        elif event_type == "tool_use":
                            tool_calls += 1
                            state = part.get("state")
                            if not isinstance(state, dict):
                                state = {}
                            name = str(part.get("tool") or "unknown")
                            call_id = str(part.get("callID") or part.get("id") or "")
                            events.emit(
                                AgentEventKind.TOOL_REQUESTED,
                                {
                                    "tool_call_id": call_id,
                                    "name": name,
                                    "arguments": state.get("input") or {},
                                },
                            )
                            events.emit(
                                AgentEventKind.TOOL_COMPLETED,
                                {
                                    "tool_call_id": call_id,
                                    "name": name,
                                    "result": _bounded(
                                        state.get("output")
                                        if state.get("status") == "completed"
                                        else state.get("error")
                                    ),
                                    "status": state.get("status"),
                                },
                            )
                            if request.spec.budget.max_tool_calls is not None and tool_calls > request.spec.budget.max_tool_calls:
                                failure = (
                                    "OpenCode exceeded the Reasoning tool-call budget of "
                                    f"{request.spec.budget.max_tool_calls}"
                                )
                                break
                        elif event_type == "step_finish":
                            cost = part.get("cost")
                            if isinstance(cost, (int, float)) and not isinstance(cost, bool):
                                usage["cost_usd"] = usage.get("cost_usd", 0.0) + float(cost)
                            self._tokens(usage, part.get("tokens"))
                            ceiling = request.spec.budget.max_cost_usd
                            if ceiling is not None and usage.get("cost_usd", 0.0) > ceiling:
                                failure = (
                                    f"OpenCode exceeded the ${ceiling:.6f} Reasoning cost ceiling"
                                )
                                break
                        elif event_type == "error":
                            error = event.get("error")
                            if isinstance(error, dict):
                                data = error.get("data")
                                failure = str(
                                    data.get("message")
                                    if isinstance(data, dict) and data.get("message")
                                    else error.get("name") or error
                                )
                            else:
                                failure = str(error or "OpenCode reported an error")
                            break
                    if failure:
                        break
            finally:
                selector.close()

            if failure and process.poll() is None:
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    with suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
            elif process.poll() is None:
                process.wait()

            output = "\n\n".join(output_parts).strip()
            stderr = "".join(stderr_parts).strip()
            if failure is None and process.returncode != 0:
                failure = stderr or f"OpenCode exited with status {process.returncode}"
            if failure is None and not output:
                failure = stderr or "OpenCode produced no completed text event"
            if failure:
                events.emit(AgentEventKind.RUN_FAILED, {"error": failure})
                return AgentRunResult(
                    request.run_id,
                    "failed",
                    output_text=output,
                    usage=usage,
                    session_id=session_id,
                    error=failure,
                )

            events.emit(AgentEventKind.MESSAGE_COMPLETED, {"text": output})
            events.emit(AgentEventKind.USAGE, usage)
            events.emit(AgentEventKind.RUN_COMPLETED, {"session_id": session_id})
            return AgentRunResult(
                request.run_id,
                "completed",
                output_text=output,
                usage=usage,
                session_id=session_id,
            )
