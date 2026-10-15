"""Claude Code CLI adapter with explicit, streamed run boundaries."""

from __future__ import annotations

import json
import os
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


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class ClaudeCodeError(RuntimeError):
    """Claude Code could not be started or returned an invalid result."""


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


def _blocks_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "".join(
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    )


class ClaudeCodeExecutor:
    """Execute a prepared run through ``claude -p`` stream-json mode."""

    def __init__(
        self,
        command: Sequence[str] | str = ("claude",),
        *,
        environment: Mapping[str, str] | None = None,
        bare: bool = False,
    ):
        self.command = tuple(shlex.split(command) if isinstance(command, str) else command)
        if not self.command:
            raise ValueError("Claude Code command cannot be empty")
        self.environment = dict(environment or {})
        self.bare = bare

    @property
    def capabilities(self) -> ExecutorCapabilities:
        return ExecutorCapabilities(
            executor_id="claude-code",
            display_name="Claude Code CLI",
            prompt_fidelity=PromptFidelity.CONTROLLED_INPUT,
            supports_tools=True,
            supports_skills=True,
            supports_sessions=True,
            supports_structured_output=True,
            supports_mcp=True,
            model_patterns=("claude-*", "sonnet", "opus", "fable"),
            model_options=("sonnet", "opus", "haiku"),
        )

    def _built_in_tools(self, request: PreparedAgentRun) -> str:
        if request.spec.sandbox.mode in {"none", "disabled"}:
            return ""
        tools = ["Read", "Glob", "Grep"]
        if request.spec.sandbox.network:
            tools.extend(("WebSearch", "WebFetch"))
        if request.spec.sandbox.mode in {"workspace_write", "write"}:
            tools.extend(("Edit", "Write", "Bash"))
        return ",".join(tools)

    def _arguments(
        self,
        request: PreparedAgentRun,
        *,
        mcp_path: Path | None,
    ) -> list[str]:
        args = [
            *self.command,
            "-p",
            request.user_prompt,
            "--output-format",
            "stream-json",
            "--verbose",
            "--include-partial-messages",
            "--model",
            request.spec.model,
            "--system-prompt",
            request.system_prompt,
            "--permission-mode",
            "dontAsk",
            "--tools",
            self._built_in_tools(request),
        ]
        if self.bare:
            args.append("--bare")
        if request.spec.effort:
            args.extend(("--effort", request.spec.effort))
        if request.spec.budget.max_cost_usd is not None:
            args.extend(("--max-budget-usd", str(request.spec.budget.max_cost_usd)))
        if request.spec.output_schema:
            args.extend(
                (
                    "--json-schema",
                    json.dumps(request.spec.output_schema, separators=(",", ":")),
                )
            )
        if request.spec.session.mode == "fresh":
            args.append("--no-session-persistence")
        else:
            args.extend(("--resume", request.spec.session.session_id or ""))
            if request.spec.session.mode == "fork":
                args.append("--fork-session")
        for path in request.spec.sandbox.writable_paths:
            args.extend(("--add-dir", path))
        if mcp_path is not None:
            args.extend(("--mcp-config", str(mcp_path), "--strict-mcp-config"))
            allowed = [f"mcp__reasoning__{tool.name}" for tool in request.tools]
            allowed.extend(f"mcp__{name}" for name in request.spec.mcp_connections)
            if allowed:
                args.extend(("--allowedTools", ",".join(allowed)))
        for skill in request.skills:
            if skill.directory:
                args.extend(("--plugin-dir", skill.directory))
        return args

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
        with tempfile.TemporaryDirectory(prefix="reasoning-claude-") as temporary:
            temporary_path = Path(temporary)
            mcp_path = None
            if request.mcp_servers:
                mcp_path = temporary_path / "mcp.json"
                mcp_path.write_text(
                    json.dumps({"mcpServers": request.mcp_servers}, ensure_ascii=False),
                    encoding="utf-8",
                )
            workdir = Path(request.spec.sandbox.working_directory or temporary_path)
            workdir.mkdir(parents=True, exist_ok=True)
            argv = self._arguments(request, mcp_path=mcp_path)
            environment = {**os.environ, **self.environment}
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
                    bufsize=1,
                    start_new_session=True,
                )
            except OSError as exc:
                error = f"could not start Claude Code: {exc}"
                events.emit(AgentEventKind.RUN_FAILED, {"error": error})
                return AgentRunResult(request.run_id, "failed", error=error)

            output = ""
            result_event: dict[str, Any] | None = None
            stderr_parts: list[str] = []
            selector = selectors.DefaultSelector()
            assert process.stdout is not None and process.stderr is not None
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
            deadline = time.monotonic() + request.spec.budget.timeout_seconds
            try:
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError(
                            f"Claude Code exceeded {request.spec.budget.timeout_seconds:g}s timeout"
                        )
                    ready = selector.select(min(remaining, 0.25))
                    if not ready and process.poll() is not None:
                        for fileobj in list(selector.get_map().values()):
                            selector.unregister(fileobj.fileobj)
                        break
                    for key, _ in ready:
                        line = key.fileobj.readline()
                        if line == "":
                            selector.unregister(key.fileobj)
                            continue
                        if key.data == "stderr":
                            stderr_parts.append(line)
                            continue
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            stderr_parts.append(f"non-JSON stdout: {line}")
                            continue
                        event_type = event.get("type")
                        if event_type == "stream_event":
                            stream = event.get("event") or {}
                            delta = stream.get("delta") or {}
                            if delta.get("type") == "text_delta":
                                text = delta.get("text") or ""
                                output += text
                                events.emit(AgentEventKind.MESSAGE_DELTA, {"text": text})
                            elif delta.get("type") == "thinking_delta":
                                events.emit(
                                    AgentEventKind.REASONING_DELTA,
                                    {"text": delta.get("thinking") or ""},
                                )
                        elif event_type == "assistant":
                            for block in (event.get("message") or {}).get("content") or []:
                                if isinstance(block, dict) and block.get("type") == "tool_use":
                                    events.emit(
                                        AgentEventKind.TOOL_REQUESTED,
                                        {
                                            "tool_call_id": block.get("id"),
                                            "name": block.get("name"),
                                            "arguments": block.get("input") or {},
                                        },
                                    )
                        elif event_type == "user":
                            for block in (event.get("message") or {}).get("content") or []:
                                if isinstance(block, dict) and block.get("type") == "tool_result":
                                    events.emit(
                                        AgentEventKind.TOOL_COMPLETED,
                                        {
                                            "tool_call_id": block.get("tool_use_id"),
                                            "result": block.get("content"),
                                            "is_error": bool(block.get("is_error")),
                                        },
                                    )
                        elif event_type == "result":
                            result_event = event
                return_code = process.wait(timeout=2)
            except Exception as exc:
                if process.poll() is None:
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                        process.wait(timeout=2)
                    except (OSError, subprocess.TimeoutExpired):
                        with suppress(OSError):
                            os.killpg(process.pid, signal.SIGKILL)
                error = str(exc)
                events.emit(AgentEventKind.RUN_FAILED, {"error": error})
                return AgentRunResult(request.run_id, "failed", output_text=output, error=error)
            finally:
                selector.close()

        stderr = "".join(stderr_parts).strip()
        if result_event:
            final = result_event.get("result")
            if isinstance(final, str):
                output = final
            elif not output:
                output = _blocks_text((result_event.get("message") or {}).get("content"))
        usage = dict((result_event or {}).get("usage") or {})
        if result_event and result_event.get("total_cost_usd") is not None:
            usage["cost_usd"] = result_event["total_cost_usd"]
        failed = return_code != 0 or result_event is None or bool(result_event.get("is_error"))
        if failed:
            error = (
                (result_event or {}).get("result")
                or stderr
                or f"Claude Code exited with status {return_code}"
            )
            events.emit(AgentEventKind.USAGE, usage)
            events.emit(AgentEventKind.RUN_FAILED, {"error": str(error)})
            return AgentRunResult(
                request.run_id,
                "failed",
                output_text=output,
                usage=usage,
                session_id=(result_event or {}).get("session_id"),
                error=str(error),
            )
        structured = result_event.get("structured_output")
        if request.spec.output_schema and structured is None:
            try:
                structured = json.loads(output)
            except json.JSONDecodeError:
                structured = None
        events.emit(AgentEventKind.MESSAGE_COMPLETED, {"text": output})
        events.emit(AgentEventKind.USAGE, usage)
        events.emit(AgentEventKind.RUN_COMPLETED, {})
        return AgentRunResult(
            request.run_id,
            "completed",
            output_text=output,
            structured_output=structured,
            usage=usage,
            session_id=result_event.get("session_id"),
        )
