"""Codex app-server adapter for immutable Reasoning workflow nodes."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import sys
import tempfile
import threading
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .. import sandbox
from ..codex_client import (
    CodexAppServerClient,
    CodexClientError,
    local_codex_runtime_error,
)
from .contracts import (
    AgentEvent,
    AgentEventKind,
    AgentRunResult,
    EventSink,
    ExecutorCapabilities,
    PreparedAgentRun,
    PromptFidelity,
)

_MCP_SERVER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_TOOL_EVENT_KINDS = {"commandExecution", "fileChange", "mcpToolCall", "webSearch"}
_DEADLINE_STEER_PROMPT = (
    "Reasoning deadline: approximately {seconds:g} seconds remain. Stop exploration "
    "now. Use the already-granted tools immediately to persist every required "
    "artifact at its exact path and include every required terminal marker. If a "
    "complete result is impossible, persist an honest bounded report naming the "
    "unresolved items. Then return a concise terminal answer; do not start new analysis."
)
_TOOL_BUDGET_STEER_PROMPT = (
    "Reasoning tool-call budget is now exhausted. Do not call another tool and stop "
    "exploration. If every required artifact was already persisted, return the "
    "terminal markers now. Otherwise return an honest bounded terminal result naming "
    "what could not be persisted."
)
_SATISFIED_TOOL_BUDGET_STEER_PROMPT = (
    "Reasoning confirms that every required successful tool call and artifact is "
    "already complete, and the tool-call budget is now exhausted. Do not repeat any "
    "tool call. Return the concise terminal answer now, including every required "
    "marker; do not start new analysis."
)
_OUTPUT_BUDGET_STEER_PROMPT = (
    "Reasoning output-token budget is {limit:g}, and approximately {used:g} output "
    "tokens have already been consumed. Stop discretionary exploration and commentary "
    "now. Use the already-granted tools immediately to complete every outstanding "
    "required call, persist every required artifact at its exact path, and include "
    "every required terminal marker. If a complete result "
    "is impossible, persist an honest bounded report naming the unresolved items. "
    "Then return a concise terminal answer; do not start new analysis."
)
_COMPLETION_SHORTFALL_STEER_PROMPT = (
    "Reasoning completion requirements remain outstanding after your last message: "
    "{requirements}. Do not send another progress update. Use the already-granted "
    "tools immediately to finish every outstanding successful call and persist every "
    "required artifact at its exact path. Then return a concise terminal answer with "
    "every required marker; do not start new analysis."
)
_ARTIFACT_PERSISTENCE_INSTRUCTIONS = (
    "Reasoning completion artifacts are project-store records, not workspace files. "
    "Persist each required artifact only with the granted Reasoning `artifact_write` "
    "MCP tool at its exact project-relative path. Never use apply_patch, file-change, "
    "shell, or another local filesystem mechanism to satisfy an artifact requirement: "
    "the Codex workflow workspace is ephemeral and local files are rejected by the "
    "completion contract. When constructing an `artifact_write` call through "
    "code mode, prefer plain-text or Unicode math. A raw TeX backslash in a "
    "JavaScript string can become a forbidden control character; escape it twice "
    "or omit it."
)
_COMPLETION_RESERVE_ERROR = "Reasoning MCP completion capacity is reserved"
_TOOL_BUDGET_ERROR = "Reasoning MCP tool-call budget exhausted"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _bounded(value: Any, limit: int = 64 * 1024) -> Any:
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        encoded = str(value)
    if len(encoded) <= limit:
        return value
    return {"truncated": True, "preview": encoded[:limit]}


def _toml_map(value: Mapping[str, str]) -> str:
    return "{" + ",".join(f"{json.dumps(str(key))}={json.dumps(str(item))}" for key, item in value.items()) + "}"


def _usage(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    mapping = {
        "inputTokens": "input_tokens",
        "cachedInputTokens": "cached_input_tokens",
        "cacheWriteInputTokens": "cache_write_input_tokens",
        "outputTokens": "output_tokens",
        "reasoningOutputTokens": "reasoning_output_tokens",
        "totalTokens": "total_tokens",
    }
    return {
        target: amount
        for source, target in mapping.items()
        if isinstance((amount := value.get(source)), (int, float)) and not isinstance(amount, bool)
    }


def _is_completion_reserve_denial(item: Mapping[str, Any]) -> bool:
    return _COMPLETION_RESERVE_ERROR in json.dumps(
        {
            "error": item.get("error"),
            "result": item.get("result"),
            "output": item.get("output"),
        },
        ensure_ascii=False,
        default=str,
    )


def _is_tool_budget_denial(item: Mapping[str, Any]) -> bool:
    return _TOOL_BUDGET_ERROR in json.dumps(
        {
            "error": item.get("error"),
            "result": item.get("result"),
            "output": item.get("output"),
        },
        ensure_ascii=False,
        default=str,
    )


class _Events:
    def __init__(self, run_id: str, sink: EventSink | None):
        self.run_id = run_id
        self.sink = sink
        self.sequence = 0
        self.lock = threading.Lock()

    def emit(self, kind: AgentEventKind, data: dict[str, Any] | None = None) -> None:
        with self.lock:
            self.sequence += 1
            event = AgentEvent(
                run_id=self.run_id,
                sequence=self.sequence,
                kind=kind,
                at=_now(),
                data=data or {},
            )
        if self.sink:
            self.sink(event)


class CodexWorkflowExecutor:
    """Execute one prepared workflow node through a fresh Codex thread."""

    def __init__(
        self,
        command: Sequence[str] | str | None = None,
        *,
        environment: Mapping[str, str] | None = None,
        writable_roots: Sequence[Path | str] = (),
        read_only_roots: Sequence[Path | str] = (),
        outer_sandbox: bool | None = None,
        model_options: Sequence[str] = (),
        deadline_steer_min_timeout_seconds: float = 60.0,
        output_budget_steer_fraction: float = 0.6,
        completion_idle_steer_seconds: float = 15.0,
        tool_budget_idle_steer_seconds: float = 15.0,
    ):
        supplied = command is not None
        if command is None:
            executable = shutil.which("codex") or "codex"
            values = (executable, "app-server")
        else:
            values = tuple(shlex.split(command) if isinstance(command, str) else command)
        if not values:
            raise ValueError("Codex command cannot be empty")
        self.command = values
        self.environment = dict(environment or {})
        self.writable_roots = tuple(Path(path).resolve() for path in writable_roots)
        self.read_only_roots = tuple(Path(path).resolve() for path in read_only_roots)
        self.outer_sandbox = (not supplied) if outer_sandbox is None else outer_sandbox
        self.model_options = tuple(model_options)
        self.deadline_steer_min_timeout_seconds = deadline_steer_min_timeout_seconds
        if not 0 < output_budget_steer_fraction < 1:
            raise ValueError("output budget steer fraction must be between zero and one")
        self.output_budget_steer_fraction = output_budget_steer_fraction
        if completion_idle_steer_seconds <= 0:
            raise ValueError("completion idle steer seconds must be positive")
        self.completion_idle_steer_seconds = completion_idle_steer_seconds
        if tool_budget_idle_steer_seconds <= 0:
            raise ValueError("tool budget idle steer seconds must be positive")
        self.tool_budget_idle_steer_seconds = tool_budget_idle_steer_seconds

    @property
    def capabilities(self) -> ExecutorCapabilities:
        return ExecutorCapabilities(
            executor_id="codex",
            display_name="Codex CLI",
            prompt_fidelity=PromptFidelity.CONTROLLED_INPUT,
            supports_tools=True,
            supports_skills=True,
            supports_sessions=False,
            supports_structured_output=True,
            supports_mcp=True,
            model_patterns=("*",),
            model_options=self.model_options,
        )

    @staticmethod
    def _mcp_arguments(request: PreparedAgentRun) -> list[str]:
        arguments: list[str] = []
        if request.spec.budget.max_tool_calls == 0:
            return arguments
        for name, raw_server in request.mcp_servers.items():
            if not _MCP_SERVER.fullmatch(name):
                raise ValueError(f"invalid Codex MCP server id: {name}")
            server = dict(raw_server)
            prefix = f"mcp_servers.{name}"
            if server.get("command"):
                arguments.extend(("-c", f"{prefix}.command={json.dumps(str(server['command']))}"))
                arguments.extend(("-c", f"{prefix}.args={json.dumps(list(server.get('args') or []))}"))
                if server.get("env"):
                    arguments.extend(("-c", f"{prefix}.env={_toml_map(dict(server['env']))}"))
                continue
            if server.get("url"):
                arguments.extend(("-c", f"{prefix}.url={json.dumps(str(server['url']))}"))
                if server.get("headers"):
                    arguments.extend(
                        (
                            "-c",
                            f"{prefix}.http_headers={_toml_map(dict(server['headers']))}",
                        )
                    )
                continue
            raise ValueError(f"Codex MCP server {name} has no command or URL")
        return arguments

    def _base_command(self, request: PreparedAgentRun) -> list[str]:
        command = [
            *self.command,
            "-c",
            'model_reasoning_summary="detailed"',
            "-c",
            f"tools.web_search={'true' if request.spec.sandbox.network else 'false'}",
            "-c",
            (f"sandbox_workspace_write.network_access={'true' if request.spec.sandbox.network else 'false'}"),
            "--disable",
            "multi_agent_v2",
            "--disable",
            "multi_agent",
            "--disable",
            "guardian_approval",
            "--disable",
            "apps",
            "--disable",
            "plugins",
            "--disable",
            "skill_search",
            "--disable",
            "hooks",
            "--disable",
            "browser_use",
            "--disable",
            "computer_use",
            "--disable",
            "image_generation",
            "--disable",
            "goals",
            "--disable",
            "tool_suggest",
            "--enable" if request.spec.model_provider == "openai" else "--disable",
            "code_mode_host",
            "--disable",
            "shell_tool",
            "--disable",
            "unified_exec",
        ]
        return [*command, *self._mcp_arguments(request)]

    @staticmethod
    def _runtime_roots() -> tuple[Path, ...]:
        venv = Path(sys.executable).parent.parent
        python_runtime = Path(sys.base_prefix)
        try:
            launcher = Path(os.readlink(sys.executable))
        except OSError:
            launcher = Path(sys.executable)
        if not launcher.is_absolute():
            launcher = Path(sys.executable).parent / launcher
        source = Path(__file__).resolve().parent.parent.parent
        return venv, python_runtime, launcher.parent.parent, source

    def _isolated_command(
        self,
        request: PreparedAgentRun,
        workdir: Path,
    ) -> tuple[list[str], bool]:
        command = self._base_command(request)
        isolated = bool(self.outer_sandbox and sandbox.enabled() and sandbox.available())
        if not isolated:
            return command, False
        writable = [workdir, sandbox.codex_home_bind(), *self.writable_roots]
        writable.extend(Path(path).resolve() for path in request.spec.sandbox.writable_paths)
        read_only = [*self.read_only_roots, *self._runtime_roots()]
        return (
            sandbox.wrap(command, rw=writable, ro=read_only, chdir=workdir),
            True,
        )

    def _environment(self) -> dict[str, str] | None:
        inherited = sandbox.codex_env() if self.outer_sandbox else dict(os.environ)
        if not self.environment:
            return inherited
        return {
            **(os.environ if inherited is None else inherited),
            **self.environment,
        }

    @staticmethod
    def _await_mcp_inventory(
        client: CodexAppServerClient,
        request: PreparedAgentRun,
        thread_id: str,
        *,
        timeout: float,
    ) -> tuple[str, ...]:
        """Block until Codex has loaded every MCP server needed by the turn."""

        if request.spec.budget.max_tool_calls == 0 or not request.mcp_servers:
            return ()
        inventory = client.request(
            "mcpServerStatus/list",
            {
                "threadId": thread_id,
                "detail": "toolsAndAuthOnly",
                "limit": max(100, len(request.mcp_servers)),
            },
            timeout=timeout,
        )
        statuses = inventory.get("data")
        if not isinstance(statuses, list):
            raise CodexClientError("Codex returned an invalid MCP server inventory")
        by_name = {
            str(status.get("name")): status
            for status in statuses
            if isinstance(status, Mapping) and status.get("name")
        }
        missing_servers = sorted(set(request.mcp_servers) - set(by_name))
        if missing_servers:
            raise CodexClientError(
                "Codex did not start required MCP servers: " + ", ".join(missing_servers)
            )
        reasoning = by_name.get("reasoning")
        if reasoning is not None:
            raw_tools = reasoning.get("tools")
            if not isinstance(raw_tools, Mapping):
                raise CodexClientError("Codex returned an invalid Reasoning MCP tool inventory")
            available_tools = {
                str(tool.get("name") or key)
                for key, tool in raw_tools.items()
                if isinstance(tool, Mapping)
            }
            required_tools = {tool.name for tool in request.tools}
            missing_tools = sorted(required_tools - available_tools)
            if missing_tools:
                raise CodexClientError(
                    "Codex did not load required Reasoning tools: " + ", ".join(missing_tools)
                )
        return tuple(sorted(by_name))

    @staticmethod
    def _turn_input(request: PreparedAgentRun) -> list[dict[str, Any]]:
        values = [
            {
                "type": "text",
                "text": str(message.get("content") or ""),
                "text_elements": [],
            }
            for message in request.context_messages
            if str(message.get("content") or "")
        ]
        values.append({"type": "text", "text": request.user_prompt, "text_elements": []})
        return values

    @staticmethod
    def _developer_instructions(request: PreparedAgentRun) -> str:
        artifacts = request.spec.completion_artifacts
        markers = request.spec.completion_markers
        required_tool_calls = request.spec.completion_tool_calls
        if not artifacts and not markers and not required_tool_calls:
            return request.system_prompt
        contract: list[str] = [
            request.system_prompt,
            *([_ARTIFACT_PERSISTENCE_INSTRUCTIONS] if artifacts else []),
            "Reasoning has already verified the MCP inventory. Do not call "
            "list_mcp_resources, list_mcp_resource_templates, or any ambient "
            "discovery tool; those calls are outside the workflow grant and still "
            "consume the node's tool-call budget.",
        ]
        if artifacts:
            contract.append(
                "Required artifact paths (use these exact project-relative strings):\n"
                + "\n".join(f"- {requirement.path}" for requirement in artifacts)
            )
        if markers:
            contract.append(
                "Required terminal markers (include each in an artifact when one is "
                "required, and in the final response):\n"
                + "\n".join(f"- {marker}" for marker in markers)
            )
        if required_tool_calls:
            contract.append(
                "Required successful Reasoning tool calls (rejected or failed calls do "
                "not count):\n"
                + "\n".join(
                    f"- {name}: at least {minimum}"
                    for name, minimum in required_tool_calls.items()
                )
            )
        if request.tools:
            contract.append(
                "Granted Reasoning workflow tools:\n"
                + "\n".join(f"- {tool.name}" for tool in request.tools)
            )
        write_minimum = int(required_tool_calls.get("artifact_write", 0))
        minimum_required = sum(
            int(minimum)
            for name, minimum in required_tool_calls.items()
            if name != "artifact_write"
        ) + max(write_minimum, len(artifacts))
        if request.spec.budget.max_tool_calls is None:
            contract.append("There is no additional Reasoning tool-call ceiling. Complete required calls before reporting success.")
            return "\n\n".join(contract)
        discretionary = max(0, request.spec.budget.max_tool_calls - minimum_required)
        contract.append(
            f"The completion contract itself needs at least {minimum_required} of the "
            f"{request.spec.budget.max_tool_calls} permitted tool calls. At most "
            f"{discretionary} calls are available for skill activation, failed or "
            "broadened searches, and all other optional work. Complete required calls "
            "early. The MCP bridge rejects optional calls once the remaining capacity "
            "is needed by outstanding completion requirements."
        )
        return "\n\n".join(contract)

    @staticmethod
    def _codex_sandbox_mode(request: PreparedAgentRun, *, isolated: bool) -> str:
        if isolated and request.spec.sandbox.mode in {"workspace_write", "write"}:
            return "danger-full-access"
        return "read-only"

    def execute(
        self,
        request: PreparedAgentRun,
        emit: EventSink | None = None,
    ) -> AgentRunResult:
        events = _Events(request.run_id, emit)
        provider = request.spec.model_provider
        events.emit(
            AgentEventKind.RUN_STARTED,
            {
                "executor": self.capabilities.executor_id,
                "model": request.spec.model,
                "model_provider": provider,
            },
        )
        if not provider:
            error = "Codex workflow nodes require an explicit model_provider"
            events.emit(AgentEventKind.RUN_FAILED, {"error": error})
            return AgentRunResult(request.run_id, "failed", error=error)
        if request.spec.session.mode != "fresh":
            error = "Codex workflow nodes currently require a fresh session"
            events.emit(AgentEventKind.RUN_FAILED, {"error": error})
            return AgentRunResult(request.run_id, "failed", error=error)

        with tempfile.TemporaryDirectory(prefix="reasoning-codex-workflow-") as temporary:
            temporary_path = Path(temporary)
            workdir = Path(request.spec.sandbox.working_directory or temporary_path).resolve()
            workdir.mkdir(parents=True, exist_ok=True)
            command, isolated = self._isolated_command(request, workdir)
            stderr_path = temporary_path / "codex-appserver.log"
            finished = threading.Event()
            budget_wake = threading.Event()
            state_lock = threading.Lock()
            state: dict[str, Any] = {
                "thread_id": None,
                "turn_id": None,
                "turn_status": None,
                "turn_error": None,
                "messages": [],
                "usage": {},
                "seen_tools": set(),
                "counted_tools": set(),
                "tool_calls": 0,
                "completion_tool_calls_denied": 0,
                "tool_budget_calls_denied": 0,
                "successful_tool_calls": {},
                "successful_artifacts": set(),
                "observed_markers": set(),
                "last_activity_at": time.monotonic(),
                "last_message_completed_at": None,
                "budget_error": None,
            }
            holder: dict[str, CodexAppServerClient] = {}
            required_artifacts = {
                requirement.path for requirement in request.spec.completion_artifacts
            }
            required_markers = set(request.spec.completion_markers)

            def completion_shortfall() -> list[str]:
                with state_lock:
                    successful_calls = dict(state["successful_tool_calls"])
                    successful_artifacts = set(state["successful_artifacts"])
                    observed_markers = set(state["observed_markers"])
                missing = [
                    f"{name} call {successful_calls.get(name, 0)}/{minimum}"
                    for name, minimum in request.spec.completion_tool_calls.items()
                    if successful_calls.get(name, 0) < minimum
                ]
                missing.extend(
                    f"artifact {path}"
                    for path in sorted(required_artifacts - successful_artifacts)
                )
                missing.extend(
                    f"marker {marker}"
                    for marker in sorted(required_markers - observed_markers)
                )
                return missing

            def belongs(params: Mapping[str, Any]) -> bool:
                with state_lock:
                    thread_id = state["thread_id"]
                return thread_id is None or params.get("threadId") == thread_id

            def start_tool(item: Mapping[str, Any]) -> bool:
                item_id = str(item.get("id") or f"anonymous-{id(item)}")
                with state_lock:
                    state["last_activity_at"] = time.monotonic()
                    state["last_message_completed_at"] = None
                    if item_id in state["seen_tools"]:
                        return False
                    state["seen_tools"].add(item_id)
                    return True

            def count_completed_tool(item: Mapping[str, Any]) -> bool:
                item_id = str(item.get("id") or f"anonymous-{id(item)}")
                with state_lock:
                    if item_id in state["counted_tools"]:
                        return False
                    state["counted_tools"].add(item_id)
                    if _is_completion_reserve_denial(item):
                        state["completion_tool_calls_denied"] += 1
                        return False
                    if _is_tool_budget_denial(item):
                        state["tool_budget_calls_denied"] += 1
                        budget_wake.set()
                        return False
                    state["tool_calls"] += 1
                    error = item.get("error")
                    status = item.get("status")
                    if error in (None, "") and status in (
                        None,
                        "completed",
                        "success",
                        "succeeded",
                    ):
                        name = item.get("tool") or item.get("type")
                        if isinstance(name, str) and name:
                            successful = state["successful_tool_calls"]
                            successful[name] = successful.get(name, 0) + 1
                            arguments = item.get("arguments") or {}
                            if isinstance(arguments, Mapping):
                                path = arguments.get("path")
                                if path in required_artifacts:
                                    state["successful_artifacts"].add(path)
                                encoded = json.dumps(
                                    arguments,
                                    ensure_ascii=False,
                                    default=str,
                                )
                                state["observed_markers"].update(
                                    marker
                                    for marker in required_markers
                                    if marker in encoded
                                )
                    calls = state["tool_calls"]
                    if request.spec.budget.max_tool_calls is not None and calls > request.spec.budget.max_tool_calls:
                        state["budget_error"] = f"Codex exceeded the Reasoning tool-call budget of {request.spec.budget.max_tool_calls}"
                        budget_wake.set()
                    return True

            def tool_payload(item: Mapping[str, Any]) -> dict[str, Any]:
                return {
                    "tool_call_id": item.get("id"),
                    "server": item.get("server"),
                    "name": item.get("tool") or item.get("type"),
                    "arguments": item.get("arguments") or {},
                }

            def on_notification(message: dict[str, Any]) -> None:
                method = str(message.get("method") or "")
                params = message.get("params") or {}
                if not isinstance(params, Mapping) or not belongs(params):
                    return
                with state_lock:
                    state["last_activity_at"] = time.monotonic()
                if method == "turn/started":
                    turn = params.get("turn") or {}
                    with state_lock:
                        state["turn_id"] = turn.get("id")
                    return
                if method == "turn/completed":
                    turn = params.get("turn") or {}
                    with state_lock:
                        state["turn_status"] = turn.get("status")
                        state["turn_error"] = turn.get("error")
                    finished.set()
                    return
                if method == "thread/tokenUsage/updated":
                    token_usage = params.get("tokenUsage") or {}
                    normalized = _usage(token_usage.get("total"))
                    with state_lock:
                        state["usage"] = normalized
                    budget_wake.set()
                    events.emit(AgentEventKind.USAGE, normalized)
                    return
                if method == "item/reasoning/summaryTextDelta":
                    text = str(params.get("delta") or "")
                    if text:
                        events.emit(AgentEventKind.REASONING_DELTA, {"text": text})
                    return
                if method == "item/agentMessage/delta":
                    text = str(params.get("delta") or "")
                    if text:
                        events.emit(AgentEventKind.MESSAGE_DELTA, {"text": text})
                    return
                if method not in {"item/started", "item/completed"}:
                    return
                item = params.get("item") or {}
                if not isinstance(item, Mapping):
                    return
                kind = item.get("type")
                if kind in _TOOL_EVENT_KINDS:
                    first = start_tool(item)
                    if first:
                        events.emit(AgentEventKind.TOOL_REQUESTED, tool_payload(item))
                    if method == "item/completed":
                        counted = count_completed_tool(item)
                        error = item.get("error")
                        status = item.get("status")
                        events.emit(
                            AgentEventKind.TOOL_COMPLETED,
                            {
                                **tool_payload(item),
                                "status": status,
                                "result": _bounded(item.get("result") or item.get("output")),
                                "error": _bounded(error),
                            },
                        )
                        with state_lock:
                            calls = state["tool_calls"]
                        if counted and request.spec.budget.max_tool_calls is not None and calls >= request.spec.budget.max_tool_calls:
                            budget_wake.set()
                    return
                if method == "item/completed" and kind == "agentMessage":
                    text = str(item.get("text") or "")
                    with state_lock:
                        state["messages"].append(text)
                        state["observed_markers"].update(
                            marker for marker in required_markers if marker in text
                        )
                        state["last_message_completed_at"] = time.monotonic()
                        state["last_activity_at"] = state["last_message_completed_at"]
                    events.emit(AgentEventKind.MESSAGE_COMPLETED, {"text": text})

            def on_server_request(message: dict[str, Any]) -> None:
                client = holder.get("client")
                if client is None:
                    return
                method = str(message.get("method") or "")
                if method == "mcpServer/elicitation/request":
                    params = message.get("params") or {}
                    action = (
                        "accept"
                        if isinstance(params, Mapping)
                        and params.get("serverName") == "reasoning"
                        else "decline"
                    )
                    client.respond(
                        message["id"],
                        {"action": action, "content": {}, "_meta": None},
                    )
                elif method.endswith("requestApproval"):
                    client.respond(message["id"], {"decision": "decline"})
                else:
                    client.respond_error(message["id"], -32601, "unsupported unattended workflow request")

            client = CodexAppServerClient(
                command,
                on_notification=on_notification,
                on_server_request=on_server_request,
                stderr_path=stderr_path,
                env=self._environment(),
                client_name="reasoning-codex-workflow",
            )
            holder["client"] = client
            failure: str | None = None
            thread_id: str | None = None
            mcp_servers_ready: tuple[str, ...] = ()
            try:
                timeout = request.spec.budget.timeout_seconds
                started = time.monotonic()
                client.start(timeout=min(timeout, 30.0))
                thread = client.request(
                    "thread/start",
                    {
                        "model": request.spec.model,
                        "modelProvider": provider,
                        "cwd": str(workdir),
                        "approvalPolicy": "never",
                        "approvalsReviewer": "user",
                        "sandbox": self._codex_sandbox_mode(request, isolated=isolated),
                        "runtimeWorkspaceRoots": [str(workdir)],
                        "developerInstructions": self._developer_instructions(request),
                        "ephemeral": True,
                    },
                    timeout=min(max(0.1, timeout - (time.monotonic() - started)), 30.0),
                )
                thread_id = thread["thread"]["id"]
                with state_lock:
                    state["thread_id"] = thread_id
                remaining = max(0.1, timeout - (time.monotonic() - started))
                mcp_servers_ready = self._await_mcp_inventory(
                    client,
                    request,
                    thread_id,
                    timeout=min(remaining, 30.0),
                )
                turn_values: dict[str, Any] = {
                    "threadId": thread_id,
                    "input": self._turn_input(request),
                    "model": request.spec.model,
                }
                if request.spec.effort:
                    turn_values["effort"] = request.spec.effort
                if request.spec.output_schema:
                    turn_values["outputSchema"] = request.spec.output_schema
                turn = client.request(
                    "turn/start",
                    turn_values,
                    timeout=min(max(0.1, timeout - (time.monotonic() - started)), 30.0),
                )
                with state_lock:
                    state["turn_id"] = turn["turn"]["id"]

                interrupted = False
                tool_budget_steer_attempted = False
                tool_budget_steer_succeeded = False
                output_budget_steer_attempted = False
                output_budget_steer_succeeded = False
                completion_shortfall_steer_attempted = False
                completion_shortfall_steer_succeeded = False
                deadline_steer_attempted = False
                deadline_steer_succeeded = False
                deadline_steer_margin = min(300.0, max(1.0, timeout * 0.2))
                while not finished.wait(0.1):
                    elapsed = time.monotonic() - started
                    with state_lock:
                        budget_error = state["budget_error"]
                        turn_id = state["turn_id"]
                        tool_calls = state["tool_calls"]
                        tool_budget_calls_denied = state["tool_budget_calls_denied"]
                        usage = dict(state["usage"])
                        last_activity_at = state["last_activity_at"]
                        last_message_completed_at = state["last_message_completed_at"]
                    if budget_error and not interrupted:
                        failure = budget_error
                    elif (
                        request.spec.budget.max_tool_calls is not None and request.spec.budget.max_tool_calls > 0
                        and tool_calls >= request.spec.budget.max_tool_calls
                        and (shortfall := completion_shortfall())
                    ):
                        failure = (
                            "Codex exhausted the Reasoning tool-call budget before "
                            "satisfying completion requirements: "
                            + "; ".join(shortfall)
                        )
                    elif (
                        not tool_budget_steer_attempted
                        and request.spec.budget.max_tool_calls is not None and request.spec.budget.max_tool_calls > 0
                        and tool_calls >= request.spec.budget.max_tool_calls
                        and (
                            tool_budget_calls_denied > 0
                            or time.monotonic() - last_activity_at
                            >= self.tool_budget_idle_steer_seconds
                        )
                        and turn_id
                    ):
                        tool_budget_steer_attempted = True
                        steer_prompt = (
                            _SATISFIED_TOOL_BUDGET_STEER_PROMPT
                            if (
                                request.spec.completion_artifacts
                                or request.spec.completion_markers
                                or request.spec.completion_tool_calls
                            )
                            else _TOOL_BUDGET_STEER_PROMPT
                        )
                        try:
                            steered = client.request(
                                "turn/steer",
                                {
                                    "threadId": thread_id,
                                    "input": [
                                        {
                                            "type": "text",
                                            "text": steer_prompt,
                                            "text_elements": [],
                                        }
                                    ],
                                    "expectedTurnId": turn_id,
                                },
                                timeout=10.0,
                            )
                            rotated_turn_id = steered.get("turnId")
                            if isinstance(rotated_turn_id, str) and rotated_turn_id:
                                with state_lock:
                                    state["turn_id"] = rotated_turn_id
                            tool_budget_steer_succeeded = True
                        except CodexClientError:
                            pass
                    elif (
                        not completion_shortfall_steer_attempted
                        and last_message_completed_at is not None
                        and time.monotonic() - last_activity_at
                        >= self.completion_idle_steer_seconds
                        and (shortfall := completion_shortfall())
                        and turn_id
                    ):
                        completion_shortfall_steer_attempted = True
                        try:
                            steered = client.request(
                                "turn/steer",
                                {
                                    "threadId": thread_id,
                                    "input": [
                                        {
                                            "type": "text",
                                            "text": _COMPLETION_SHORTFALL_STEER_PROMPT.format(
                                                requirements="; ".join(shortfall)
                                            ),
                                            "text_elements": [],
                                        }
                                    ],
                                    "expectedTurnId": turn_id,
                                },
                                timeout=10.0,
                            )
                            rotated_turn_id = steered.get("turnId")
                            if isinstance(rotated_turn_id, str) and rotated_turn_id:
                                with state_lock:
                                    state["turn_id"] = rotated_turn_id
                                    state["last_message_completed_at"] = None
                                completion_shortfall_steer_succeeded = True
                        except CodexClientError:
                            pass
                    elif (
                        not output_budget_steer_attempted
                        and request.spec.budget.max_output_tokens is not None
                        and (
                            request.spec.completion_artifacts
                            or request.spec.completion_markers
                            or request.spec.completion_tool_calls
                        )
                        and usage.get("output_tokens", 0)
                        >= request.spec.budget.max_output_tokens
                        * self.output_budget_steer_fraction
                        and turn_id
                    ):
                        output_budget_steer_attempted = True
                        try:
                            steered = client.request(
                                "turn/steer",
                                {
                                    "threadId": thread_id,
                                    "input": [
                                        {
                                            "type": "text",
                                            "text": _OUTPUT_BUDGET_STEER_PROMPT.format(
                                                limit=request.spec.budget.max_output_tokens,
                                                used=usage.get("output_tokens", 0),
                                            ),
                                            "text_elements": [],
                                        }
                                    ],
                                    "expectedTurnId": turn_id,
                                },
                                timeout=10.0,
                            )
                            rotated_turn_id = steered.get("turnId")
                            if isinstance(rotated_turn_id, str) and rotated_turn_id:
                                with state_lock:
                                    state["turn_id"] = rotated_turn_id
                                output_budget_steer_succeeded = True
                        except CodexClientError:
                            pass
                    elif (
                        not deadline_steer_attempted
                        and timeout >= self.deadline_steer_min_timeout_seconds
                        and elapsed >= timeout - deadline_steer_margin
                        and turn_id
                    ):
                        deadline_steer_attempted = True
                        try:
                            steered = client.request(
                                "turn/steer",
                                {
                                    "threadId": thread_id,
                                    "input": [
                                        {
                                            "type": "text",
                                            "text": _DEADLINE_STEER_PROMPT.format(seconds=deadline_steer_margin),
                                            "text_elements": [],
                                        }
                                    ],
                                    "expectedTurnId": turn_id,
                                },
                                timeout=min(10.0, max(0.1, deadline_steer_margin)),
                            )
                            rotated_turn_id = steered.get("turnId")
                            if isinstance(rotated_turn_id, str) and rotated_turn_id:
                                with state_lock:
                                    state["turn_id"] = rotated_turn_id
                                deadline_steer_succeeded = True
                        except CodexClientError:
                            pass
                    elif elapsed >= timeout and not interrupted:
                        failure = f"Codex exceeded {timeout:g}s timeout"
                    if failure and not interrupted and turn_id:
                        interrupted = True
                        try:
                            client.request(
                                "turn/interrupt",
                                {"threadId": thread_id, "turnId": turn_id},
                                timeout=min(10.0, max(0.1, timeout)),
                            )
                        except CodexClientError:
                            finished.set()
                    if budget_wake.is_set():
                        budget_wake.clear()
                with state_lock:
                    turn_status = state["turn_status"]
                    turn_error = state["turn_error"]
                    messages = list(state["messages"])
                    usage = dict(state["usage"])
                    tool_calls = int(state["tool_calls"])
                    completion_tool_calls_denied = int(state["completion_tool_calls_denied"])
                    tool_budget_calls_denied = int(state["tool_budget_calls_denied"])
                    successful_tool_calls = dict(state["successful_tool_calls"])
                    failure = failure or state["budget_error"]
                if not failure and turn_status != "completed":
                    failure = str(
                        (turn_error or {}).get("message")
                        if isinstance(turn_error, Mapping)
                        else turn_error or f"Codex turn ended with status {turn_status}"
                    )
                output = messages[-1] if messages else ""
                if not failure and not output:
                    failure = "Codex completed without visible assistant content"
                output_limit = request.spec.budget.max_output_tokens
                if not failure and output_limit is not None and usage.get("output_tokens", 0) > output_limit:
                    failure = f"Codex exceeded the Reasoning output-token budget of {output_limit}"
                if not failure and (shortfall := completion_shortfall()):
                    failure = (
                        "Codex completion requirements were not satisfied: "
                        + "; ".join(shortfall)
                    )
                structured = None
                if not failure and request.spec.output_schema:
                    try:
                        structured = json.loads(output)
                        Draft202012Validator(request.spec.output_schema).validate(structured)
                    except Exception as exc:
                        failure = f"Codex returned invalid structured output: {exc}"
                metadata = {
                    "model_provider": provider,
                    "thread_id": thread_id,
                    "turn_status": turn_status,
                    "mcp_servers_ready": list(mcp_servers_ready),
                    "tool_calls": tool_calls,
                    "max_cost_usd_enforced": False,
                    "tool_budget_steer_attempted": tool_budget_steer_attempted,
                    "tool_budget_steer_succeeded": tool_budget_steer_succeeded,
                    "output_budget_steer_attempted": output_budget_steer_attempted,
                    "output_budget_steer_succeeded": output_budget_steer_succeeded,
                    "deadline_steer_attempted": deadline_steer_attempted,
                    "deadline_steer_succeeded": deadline_steer_succeeded,
                }
                if successful_tool_calls or request.spec.completion_tool_calls:
                    metadata["successful_tool_calls"] = successful_tool_calls
                if completion_tool_calls_denied:
                    metadata["completion_tool_calls_denied"] = completion_tool_calls_denied
                if tool_budget_calls_denied:
                    metadata["tool_budget_calls_denied"] = tool_budget_calls_denied
                if completion_shortfall_steer_attempted:
                    metadata["completion_shortfall_steer_attempted"] = True
                    metadata["completion_shortfall_steer_succeeded"] = (
                        completion_shortfall_steer_succeeded
                    )
                if failure:
                    events.emit(AgentEventKind.RUN_FAILED, {"error": failure})
                    return AgentRunResult(
                        request.run_id,
                        "failed",
                        output_text=output,
                        structured_output=structured,
                        usage=usage,
                        session_id=thread_id,
                        error=failure,
                        metadata=metadata,
                    )
                events.emit(AgentEventKind.RUN_COMPLETED, {})
                return AgentRunResult(
                    request.run_id,
                    "completed",
                    output_text=output,
                    structured_output=structured,
                    usage=usage,
                    session_id=thread_id,
                    metadata=metadata,
                )
            except (CodexClientError, OSError, KeyError, TypeError, ValueError) as exc:
                diagnosis = local_codex_runtime_error(stderr_path)
                detail = diagnosis or str(exc)
                if diagnosis is None and stderr_path.is_file():
                    stderr = stderr_path.read_text(encoding="utf-8", errors="replace").strip()
                    if stderr:
                        detail = f"{detail}: {stderr[-2000:]}"
                events.emit(AgentEventKind.RUN_FAILED, {"error": detail})
                return AgentRunResult(
                    request.run_id,
                    "failed",
                    error=detail,
                    session_id=thread_id,
                    metadata={"model_provider": provider},
                )
            finally:
                client.close()
