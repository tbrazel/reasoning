"""Hermes Agent executor with a replaceable CLI/ACP transport boundary."""

from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from .contracts import (
    AgentEvent,
    AgentEventKind,
    AgentRunResult,
    EventSink,
    ExecutorCapabilities,
    PreparedAgentRun,
    PromptFidelity,
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


TransportEventSink = Callable[[AgentEventKind, dict[str, Any]], None]


class HermesTransport(Protocol):
    """Transport used by the stable executor; ACP can replace CLI later."""

    transport_name: str
    supports_streaming: bool

    def execute(
        self,
        request: PreparedAgentRun,
        emit: TransportEventSink,
    ) -> AgentRunResult: ...


class HermesCliTransport:
    """Hermes ``--oneshot`` transport using its machine-readable usage file."""

    transport_name = "cli"
    supports_streaming = False

    def __init__(
        self,
        command: Sequence[str] | str = ("hermes",),
        *,
        provider: str | None = None,
        environment: Mapping[str, str] | None = None,
        mcp_toolset: str = "reasoning",
    ):
        self.command = tuple(shlex.split(command) if isinstance(command, str) else command)
        if not self.command:
            raise ValueError("Hermes command cannot be empty")
        self.provider = provider
        self.environment = dict(environment or {})
        self.mcp_toolset = mcp_toolset

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

    def _toolsets(self, request: PreparedAgentRun) -> list[str]:
        toolsets: list[str] = []
        mode = request.spec.sandbox.mode
        if mode in {"workspace_write", "write"}:
            toolsets.extend(("terminal", "file", "code_execution"))
        if request.spec.sandbox.network:
            toolsets.append("web")
        # A manually prepared name-only binding refers to an already installed
        # Hermes skill. Reasoning registry packages carry directories and are
        # activated progressively through the run-scoped skill_read MCP tool.
        if any(not skill.directory for skill in request.skills):
            toolsets.append("skills")
        if request.tools:
            toolsets.append(self.mcp_toolset)
        # Passing no -t makes Hermes load the user's entire default tool set.
        # ``clarify`` is harmless in one-shot mode and keeps the selection
        # explicit for a prompt-only run.
        return list(dict.fromkeys(toolsets or ["clarify"]))

    def _provider_and_model(self, model: str) -> tuple[str | None, str]:
        """Translate Reasoning' routed model IDs into Hermes CLI overrides.

        Hermes accepts the inference provider and that provider's model ID as
        separate overrides.  Keeping ``openrouter/`` in the model while
        omitting ``--provider`` lets a user's persistent Hermes provider win;
        for a Codex-backed Hermes install that sends the OpenRouter model name
        to Codex and produces a misleading HTTP 400 response.
        """
        provider = self.provider
        if model.startswith("openrouter/"):
            provider = provider or "openrouter"
            if provider == "openrouter":
                model = model.removeprefix("openrouter/")
        return provider, model

    def execute(
        self,
        request: PreparedAgentRun,
        emit: TransportEventSink,
    ) -> AgentRunResult:
        if request.spec.output_schema:
            return AgentRunResult(
                request.run_id,
                "failed",
                error="Hermes CLI does not expose structured-output validation; use Reasoning Native or Claude Code",
            )
        if request.spec.session.mode == "fork":
            return AgentRunResult(
                request.run_id,
                "failed",
                error="Hermes CLI cannot fork sessions; use a fresh run, resume, or the future ACP transport",
            )

        with tempfile.TemporaryDirectory(prefix="reasoning-hermes-") as temporary:
            temporary_path = Path(temporary)
            usage_path = temporary_path / "usage.json"
            provider, model = self._provider_and_model(request.spec.model)
            argv = [
                *self.command,
                "--oneshot",
                self._user_prompt(request),
                "--usage-file",
                str(usage_path),
                "--model",
                model,
                "--toolsets",
                ",".join(self._toolsets(request)),
                "--ignore-rules",
            ]
            if provider:
                argv.extend(("--provider", provider))
            if request.spec.session.mode == "resume":
                argv.extend(("--resume", request.spec.session.session_id or ""))
            installed_skills = [skill.name for skill in request.skills if not skill.directory]
            if installed_skills:
                argv.extend(("--skills", ",".join(installed_skills)))

            environment = {
                **os.environ,
                **self.environment,
                "HERMES_EPHEMERAL_SYSTEM_PROMPT": request.system_prompt,
                "HERMES_IGNORE_RULES": "1",
            }
            workdir = Path(request.spec.sandbox.working_directory or temporary_path)
            workdir.mkdir(parents=True, exist_ok=True)
            try:
                process = subprocess.Popen(
                    argv,
                    cwd=workdir,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    start_new_session=True,
                )
            except OSError as exc:
                return AgentRunResult(
                    request.run_id,
                    "failed",
                    error=f"could not start Hermes Agent: {exc}",
                )
            try:
                stdout, stderr = process.communicate(
                    timeout=request.spec.budget.timeout_seconds
                )
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                return AgentRunResult(
                    request.run_id,
                    "failed",
                    error=f"Hermes Agent exceeded {request.spec.budget.timeout_seconds:g}s timeout",
                )

            usage: dict[str, Any] = {}
            if usage_path.is_file():
                try:
                    loaded = json.loads(usage_path.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        usage = loaded
                except json.JSONDecodeError:
                    usage = {"usage_file_error": "invalid JSON"}
            output = stdout.strip()
            usage_failed = usage.get("failed") is True or usage.get("completed") is False
            if process.returncode != 0 or usage_failed:
                return AgentRunResult(
                    request.run_id,
                    "failed",
                    output_text=output,
                    usage=usage,
                    error=(
                        stderr.strip()
                        or output
                        or (
                            "Hermes Agent reported that the provider request failed"
                            if usage_failed
                            else f"Hermes Agent exited with status {process.returncode}"
                        )
                    ),
                )
            emit(AgentEventKind.MESSAGE_DELTA, {"text": output})
            emit(AgentEventKind.MESSAGE_COMPLETED, {"text": output})
            emit(AgentEventKind.USAGE, usage)
            return AgentRunResult(
                request.run_id,
                "completed",
                output_text=output,
                usage=usage,
                session_id=usage.get("session_id"),
            )


class HermesAgentExecutor:
    """Stable executor facade around a replaceable Hermes transport."""

    def __init__(self, transport: HermesTransport | None = None):
        self.transport = transport or HermesCliTransport()

    @property
    def capabilities(self) -> ExecutorCapabilities:
        return ExecutorCapabilities(
            executor_id="hermes",
            display_name=f"Hermes Agent ({self.transport.transport_name.upper()})",
            prompt_fidelity=PromptFidelity.CONTROLLED_INPUT,
            supports_tools=True,
            supports_skills=True,
            supports_sessions=True,
            supports_streaming=self.transport.supports_streaming,
            supports_structured_output=False,
            model_options=(
                "openrouter/qwen/qwen3-coder",
                "openrouter/openai/gpt-oss-20b",
                "openrouter/openai/gpt-oss-120b",
            ),
        )

    def execute(
        self,
        request: PreparedAgentRun,
        emit: EventSink | None = None,
    ) -> AgentRunResult:
        sequence = 0

        def forward(kind: AgentEventKind, data: dict[str, Any]) -> None:
            nonlocal sequence
            sequence += 1
            if emit:
                emit(
                    AgentEvent(
                        run_id=request.run_id,
                        sequence=sequence,
                        kind=kind,
                        at=_now(),
                        data=data,
                    )
                )

        forward(
            AgentEventKind.RUN_STARTED,
            {
                "executor": self.capabilities.executor_id,
                "transport": self.transport.transport_name,
                "model": request.spec.model,
            },
        )
        result = self.transport.execute(request, forward)
        if result.status == "completed":
            forward(AgentEventKind.RUN_COMPLETED, {})
        else:
            forward(AgentEventKind.RUN_FAILED, {"error": result.error or "Hermes run failed"})
        return result
