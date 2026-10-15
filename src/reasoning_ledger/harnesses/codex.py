"""Codex app-server adapter for persistent Reasoning development chats.

The transport client deliberately knows only JSON-RPC.  This adapter owns the
Codex-specific session lifecycle and translates its notifications into the
provider-neutral harness event vocabulary.  ``ChatSession`` remains the
Reasoning control plane: it owns transcripts, browser subscribers, operation
records, tasks, goals, reviews, and writers.

During the migration, the adapter also retains an ephemeral legacy rendering
event for each canonical event.  The web workbench consumes it immediately so
existing clients keep their current event shapes; it is never part of the
durable harness boundary.
"""

from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..codex_client import CodexAppServerClient, CodexClientError
from .contracts import (
    HarnessApprovalDecision,
    HarnessApprovalRequest,
    HarnessEvent,
    HarnessEventKind,
    HarnessEventSink,
    HarnessSession,
    HarnessSessionSpec,
    HarnessSteerStatus,
    HarnessTurnRequest,
    HarnessTurnResult,
)
from .profiles import codex_app_server_capabilities

_ACTIVITY_TEXT_LIMIT = 12_000
_MODEL_PROVIDER_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
APPROVAL_DECISIONS = tuple(decision.value for decision in HarnessApprovalDecision)
_TERMINAL_KINDS = {
    HarnessEventKind.TURN_COMPLETED,
    HarnessEventKind.TURN_FAILED,
    HarnessEventKind.TURN_INTERRUPTED,
}


class CodexHarnessError(RuntimeError):
    """A Codex adapter request could not be completed."""


@dataclass(frozen=True)
class _QueuedEvent:
    canonical: HarnessEvent
    legacy: dict[str, Any] | None = None


def map_codex_item(item: Mapping[str, Any]) -> dict[str, Any] | None:
    """Map a Codex ``ThreadItem`` to the workbench activity shape."""

    kind = item.get("type")
    if kind == "agentMessage":
        return {"type": "agent_message", "text": item.get("text", "")}
    if kind == "reasoning":
        text = "\n\n".join(part for part in item.get("summary", []) if part)
        return {"type": "reasoning", "text": text} if text else None
    if kind == "commandExecution":
        mapped: dict[str, Any] = {
            "type": "command_execution",
            "command": item.get("command", ""),
        }
        if item.get("cwd"):
            mapped["cwd"] = item["cwd"]
        output = item.get("aggregatedOutput", item.get("output"))
        if isinstance(output, str) and output:
            mapped["output"] = output[:_ACTIVITY_TEXT_LIMIT]
            if len(output) > _ACTIVITY_TEXT_LIMIT:
                mapped["output"] += "\n… output truncated"
        if item.get("exitCode") is not None:
            mapped["exit_code"] = item["exitCode"]
        if item.get("status"):
            mapped["status"] = item["status"]
        return mapped
    if kind == "mcpToolCall":
        mapped = {
            "type": "mcp_tool_call",
            "server": item.get("server", ""),
            "tool": item.get("tool", ""),
            "arguments": item.get("arguments"),
            "result": item.get("result"),
        }
        if item.get("error"):
            mapped["error"] = item["error"]
        if item.get("status"):
            mapped["status"] = item["status"]
        return mapped
    if kind == "webSearch":
        return {"type": "web_search", "query": item.get("query", "")}
    if kind == "fileChange":
        return {"type": "file_change", "changes": item.get("changes", [])}
    return None


class CodexAppServerHarnessAdapter:
    """One persistent Codex app-server session behind ``HarnessAdapter``.

    The adapter is intentionally configured with narrow callables rather than
    a ``ChatSession`` reference.  This keeps the dependency direction clear
    and makes the lifecycle independently testable with the fake app-server.
    """

    def __init__(
        self,
        *,
        session_id: str,
        workdir: Path,
        state_path: Path,
        stderr_path: Path,
        usage_path: Path,
        command: Callable[[], list[str]],
        environment: Callable[[], dict[str, str] | None],
        developer_instructions: Callable[[], str],
        sandbox_mode: Callable[[], str],
        interactive_approvals: bool = False,
        approval_timeout: float = 600.0,
        on_compacted: Callable[[], None] | None = None,
    ) -> None:
        self.session_id = session_id
        self.workdir = workdir.resolve()
        self.state_path = state_path
        self.stderr_path = stderr_path
        self.usage_path = usage_path
        self._command = command
        self._environment = environment
        self._developer_instructions = developer_instructions
        self._sandbox_mode = sandbox_mode
        self.interactive_approvals = interactive_approvals
        self.approval_timeout = approval_timeout
        self._on_compacted_external = on_compacted

        self._client: CodexAppServerClient | None = None
        self._client_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._publication_lock = threading.RLock()
        self._event_queue: queue.Queue[_QueuedEvent] | None = None
        self._sequence = 0
        self._active_turn_id: str | None = None
        self._active_adapter_turn_id: str | None = None
        self._live_thread_id: str | None = None
        self._live_model_provider: str | None = None
        self._pending_approvals: dict[str, dict[str, Any]] = {}
        self._unread_notes: list[str] = []
        self._agent_names: dict[str, str] = {}
        self._thread_usage: dict[str, dict[str, Any]] = {}
        self._own_last_usage: dict[str, Any] = {}
        self._own_context_window: int | None = None
        self._legacy_events: dict[int, dict[str, Any]] = {}
        self._session: HarnessSession | None = None

    @property
    def capabilities(self):
        return codex_app_server_capabilities()

    @property
    def client(self) -> CodexAppServerClient | None:
        return self._client

    @property
    def client_alive(self) -> bool:
        client = self._client
        return bool(client is not None and client.alive)

    @property
    def active_turn_id(self) -> str | None:
        with self._state_lock:
            return self._active_adapter_turn_id

    @active_turn_id.setter
    def active_turn_id(self, value: str | None) -> None:
        with self._state_lock:
            self._active_adapter_turn_id = value

    @property
    def live_thread_id(self) -> str | None:
        with self._state_lock:
            return self._live_thread_id

    @live_thread_id.setter
    def live_thread_id(self, value: str | None) -> None:
        with self._state_lock:
            self._live_thread_id = value

    @property
    def live_model_provider(self) -> str | None:
        with self._state_lock:
            return self._live_model_provider

    @live_model_provider.setter
    def live_model_provider(self, value: str | None) -> None:
        with self._state_lock:
            self._live_model_provider = value

    @property
    def unread_notes(self) -> list[str]:
        return self._unread_notes

    def thread_state(self) -> tuple[str | None, str | None]:
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return None, None
        if not isinstance(state, dict):
            return None, None
        thread_id = state.get("thread_id")
        provider = state.get("model_provider")
        if not isinstance(thread_id, str) or not thread_id.strip():
            thread_id = None
        if not isinstance(provider, str) or not provider.strip():
            provider = None
        return thread_id, provider

    def ensure_client(self) -> CodexAppServerClient:
        with self._client_lock:
            if self._client is not None and self._client.alive:
                return self._client
            stale, self._client = self._client, None
            with self._state_lock:
                self._live_thread_id = None
                self._live_model_provider = None
            if stale is not None:
                stale.close()
            client = CodexAppServerClient(
                self._command(),
                on_notification=self.on_notification,
                on_server_request=self.on_server_request,
                stderr_path=self.stderr_path,
                env=self._environment(),
            )
            client.start()
            self._client = client
            return client

    def ensure_thread(
        self,
        client: Any,
        model: str,
        *,
        model_provider: str | None = None,
    ) -> tuple[str, str]:
        requested_provider = _normalize_model_provider(model_provider)
        with self._state_lock:
            live_thread_id = self._live_thread_id
            live_provider = self._live_model_provider
        if live_thread_id is not None:
            if requested_provider is not None and live_provider is not None and requested_provider != live_provider:
                raise CodexHarnessError(f"this chat is bound to model provider {live_provider}; start a new chat to use another provider")
            return live_thread_id, live_provider or requested_provider or "openai"

        existing, persisted_provider = self.thread_state()
        if requested_provider is not None and persisted_provider is not None and requested_provider != persisted_provider:
            raise CodexHarnessError(f"this chat is bound to model provider {persisted_provider}; start a new chat to use another provider")
        provider = persisted_provider or requested_provider
        if provider is None:
            configured = client.request("config/read", {"cwd": str(self.workdir)})
            config = configured.get("config", {}) if isinstance(configured, dict) else {}
            provider = config.get("model_provider") if isinstance(config, dict) else None
            if not isinstance(provider, str) or not provider.strip():
                provider = "openai"
            provider = _normalize_model_provider(provider)
        assert provider is not None

        common = {
            "cwd": str(self.workdir),
            "approvalPolicy": "on-request",
            "approvalsReviewer": "user",
            "sandbox": self._sandbox_mode(),
            "developerInstructions": self._developer_instructions(),
            "modelProvider": provider,
        }
        if existing:
            result = client.request(
                "thread/resume",
                {"threadId": existing, "model": model, **common},
            )
        else:
            result = client.request("thread/start", {"model": model, **common})
        thread_id = result["thread"]["id"]
        _write_json_atomic(
            self.state_path,
            {"thread_id": thread_id, "model_provider": provider},
        )
        with self._state_lock:
            self._live_thread_id = thread_id
            self._live_model_provider = provider
        return thread_id, provider

    def create_session(self, spec: HarnessSessionSpec) -> HarnessSession:
        if spec.harness_id != self.capabilities.harness_id:
            raise CodexHarnessError(f"unsupported harness: {spec.harness_id}")
        client = self.ensure_client()
        thread_id, provider = self.ensure_thread(
            client,
            spec.model,
            model_provider=spec.model_provider,
        )
        session = HarnessSession(
            session_id=self.session_id,
            capabilities=self.capabilities,
            target=spec.target,
            model=spec.model,
            model_provider=provider,
            adapter_session_ref=thread_id,
        )
        self._session = session
        return session

    def run_turn(
        self,
        session: HarnessSession,
        request: HarnessTurnRequest,
        emit: HarnessEventSink | None = None,
    ) -> HarnessTurnResult:
        if session.harness_id != self.capabilities.harness_id:
            raise CodexHarnessError(f"unsupported harness: {session.harness_id}")
        client = self.ensure_client()
        thread_id = session.adapter_session_ref or self.live_thread_id
        if thread_id is None:
            raise CodexHarnessError("Codex session has no app-server thread")

        event_queue: queue.Queue[_QueuedEvent] = queue.Queue()
        with self._state_lock:
            if self._event_queue is not None:
                raise CodexHarnessError("a Codex turn is already active")
            self._event_queue = event_queue
            self._active_turn_id = request.turn_id
            self._active_adapter_turn_id = None
        output_parts: list[str] = []
        usage: dict[str, Any] = {}
        status = "failed"
        error_text: str | None = None

        try:
            self._deliver(
                self._new_event(HarnessEventKind.TURN_STARTED, data={"model": session.model}),
                emit,
            )
            turn_input = [_codex_input(message) for message in request.messages]
            result = client.request(
                "turn/start",
                {
                    "threadId": thread_id,
                    "input": turn_input,
                    "model": session.model,
                    "effort": request.effort,
                },
            )
            with self._state_lock:
                self._active_adapter_turn_id = result["turn"]["id"]

            while True:
                try:
                    queued = event_queue.get(timeout=1.0)
                except queue.Empty:
                    if not client.alive:
                        raise CodexClientError("codex app-server exited") from None
                    continue
                self._deliver(queued, emit)
                event = queued.canonical
                if event.kind is HarnessEventKind.MESSAGE_COMPLETED:
                    text = event.data.get("text")
                    if isinstance(text, str):
                        output_parts.append(text)
                elif event.kind is HarnessEventKind.USAGE:
                    value = event.data.get("usage")
                    if isinstance(value, dict):
                        usage = value
                if event.kind in _TERMINAL_KINDS:
                    status = {
                        HarnessEventKind.TURN_COMPLETED: "completed",
                        HarnessEventKind.TURN_INTERRUPTED: "interrupted",
                        HarnessEventKind.TURN_FAILED: "failed",
                    }[event.kind]
                    raw_error = event.data.get("error")
                    if isinstance(raw_error, str) and raw_error:
                        error_text = raw_error
                    break
        except (CodexClientError, OSError, KeyError, TypeError) as error:
            error_text = str(error)
            self._deliver(
                self._new_event(
                    HarnessEventKind.TURN_FAILED,
                    data={"error": error_text},
                    legacy={"type": "error", "message": error_text},
                ),
                emit,
            )
        finally:
            self.finish_turn()

        return HarnessTurnResult(
            session_id=session.session_id,
            turn_id=request.turn_id,
            status=status,
            output_text="\n\n".join(output_parts),
            usage=usage,
            error=error_text,
            metadata={"adapter_session_ref": thread_id},
        )

    def inject_items(self, items: list[dict[str, Any]]) -> bool:
        client = self._client
        thread_id = self.live_thread_id
        if client is None or thread_id is None:
            return False
        try:
            client.request("thread/inject_items", {"threadId": thread_id, "items": items})
        except CodexClientError:
            return False
        return True

    def inject_note(self, text: str) -> bool:
        client = self._client
        thread_id = self.live_thread_id
        with self._state_lock:
            active = self._active_turn_id is not None
        if not active or client is None or thread_id is None:
            return False
        with self._state_lock:
            self._unread_notes.append(text)
        if not self.inject_items(
            [
                {
                    "type": "message",
                    "role": "developer",
                    "content": [{"type": "input_text", "text": text}],
                }
            ]
        ):
            with self._state_lock, suppress(ValueError):
                self._unread_notes.remove(text)
            return False
        return True

    def steer_text(self, message: str) -> HarnessSteerStatus:
        client = self._client
        thread_id = self.live_thread_id
        with self._state_lock:
            adapter_turn_id = self._active_adapter_turn_id
            active = self._active_turn_id is not None
        if not active or adapter_turn_id is None or client is None or thread_id is None:
            return HarnessSteerStatus.NO_TURN
        for _ in range(3):
            try:
                result = client.request(
                    "turn/steer",
                    {
                        "threadId": thread_id,
                        "input": [{"type": "text", "text": message, "text_elements": []}],
                        "expectedTurnId": adapter_turn_id,
                    },
                )
            except CodexClientError:
                time.sleep(0.05)
                with self._state_lock:
                    fresh = self._active_adapter_turn_id
                    active = self._active_turn_id is not None
                if fresh is None or not active:
                    return HarnessSteerStatus.FAILED
                adapter_turn_id = fresh
                continue
            with self._state_lock:
                self._active_adapter_turn_id = result.get("turnId") or adapter_turn_id
            return HarnessSteerStatus.STEERED
        return HarnessSteerStatus.FAILED

    def steer(
        self,
        session: HarnessSession,
        turn_id: str,
        messages: tuple[Mapping[str, Any], ...],
    ) -> HarnessSteerStatus:
        self._check_active_session(session, turn_id)
        text = "\n\n".join(_message_text(message) for message in messages)
        return self.steer_text(text)

    def inject_context(
        self,
        session: HarnessSession,
        turn_id: str,
        messages: tuple[Mapping[str, Any], ...],
    ) -> bool:
        self._check_active_session(session, turn_id)
        return all(self.inject_note(_message_text(message)) for message in messages)

    def interrupt(self, session: HarnessSession | None = None, turn_id: str | None = None) -> bool:
        del session, turn_id  # stable contract ids; Codex needs its rotating id
        client = self._client
        thread_id = self.live_thread_id
        with self._state_lock:
            adapter_turn_id = self._active_adapter_turn_id
            active = self._active_turn_id is not None
        if not active or adapter_turn_id is None or client is None or thread_id is None:
            return False
        try:
            client.request(
                "turn/interrupt",
                {"threadId": thread_id, "turnId": adapter_turn_id},
            )
        except CodexClientError:
            return False
        return True

    def pending_approval_dicts(self) -> list[dict[str, Any]]:
        with self._state_lock:
            return [dict(pending["public"]) for pending in self._pending_approvals.values()]

    def pending_approvals(self, session: HarnessSession) -> tuple[HarnessApprovalRequest, ...]:
        self._check_session(session)
        return tuple(HarnessApprovalRequest(**approval) for approval in self.pending_approval_dicts())

    def resolve_approval_request(self, request_id: str, decision: str, *, emit_event: bool = True) -> bool:
        if decision not in APPROVAL_DECISIONS:
            raise CodexHarnessError(f"unknown approval decision: {decision}")
        with self._state_lock:
            pending = self._pending_approvals.pop(request_id, None)
        if pending is None:
            return False
        pending["timer"].cancel()
        client = self._client
        if client is None:
            if emit_event:
                self._enqueue(
                    HarnessEventKind.APPROVAL_RESOLVED,
                    data={"request_id": request_id, "status": "failed"},
                    legacy={
                        "type": "approval_failed",
                        "request_id": request_id,
                        "message": "the Codex session closed before the decision arrived",
                    },
                )
            return False
        # A subprocess can finish as soon as it receives this response. Keep
        # its terminal event behind the successful local decision receipt;
        # otherwise run_turn can close the stream before that receipt is read.
        with self._publication_lock:
            try:
                client.respond(pending["rpc_id"], {"decision": decision})
            except CodexClientError as error:
                if emit_event:
                    self._enqueue(
                        HarnessEventKind.APPROVAL_RESOLVED,
                        data={"request_id": request_id, "status": "failed"},
                        legacy={"type": "approval_failed", "request_id": request_id, "message": str(error)},
                    )
                return False
            if emit_event:
                self._enqueue(
                    HarnessEventKind.APPROVAL_RESOLVED,
                    data={"request_id": request_id, "decision": decision, "status": "resolved"},
                    legacy={"type": "approval_resolved", "request_id": request_id, "decision": decision},
                )
        return True

    def resolve_approval(
        self,
        session: HarnessSession,
        request_id: str,
        decision: HarnessApprovalDecision,
    ) -> bool:
        self._check_session(session)
        return self.resolve_approval_request(request_id, decision.value)

    def finish_turn(self) -> None:
        with self._state_lock:
            stale = list(self._pending_approvals)
        for request_id in stale:
            self.resolve_approval_request(request_id, "decline", emit_event=False)
        with self._state_lock:
            self._event_queue = None
            self._active_turn_id = None
            self._active_adapter_turn_id = None
            self._legacy_events.clear()

    def close_session(self, session: HarnessSession | None = None) -> None:
        del session
        self.finish_turn()
        with self._client_lock:
            client, self._client = self._client, None
        with self._state_lock:
            self._live_thread_id = None
            self._live_model_provider = None
        if client is not None:
            client.close()
        self._session = None

    def take_legacy_event(self, sequence: int) -> dict[str, Any] | None:
        with self._state_lock:
            return self._legacy_events.pop(sequence, None)

    # --------------------------------------------------------- callbacks

    def on_notification(self, msg: dict[str, Any]) -> None:
        method = msg.get("method", "")
        params = msg.get("params", {})
        thread_id = params.get("threadId")
        own = thread_id == self.live_thread_id
        if method == "turn/started" and own:
            with self._state_lock:
                self._active_adapter_turn_id = params.get("turn", {}).get("id")
            return
        if method == "turn/completed":
            if own:
                turn = params.get("turn", {})
                status = turn.get("status")
                error = turn.get("error") or {}
                error_text = error.get("message") if isinstance(error, dict) else str(error or "")
                if status == "interrupted":
                    kind = HarnessEventKind.TURN_INTERRUPTED
                elif status == "completed":
                    kind = HarnessEventKind.TURN_COMPLETED
                else:
                    kind = HarnessEventKind.TURN_FAILED
                legacy = None
                if kind is HarnessEventKind.TURN_FAILED:
                    legacy = {"type": "error", "message": error_text or "turn failed"}
                self._enqueue(
                    kind,
                    data={"status": status or "failed", "error": error_text or None},
                    legacy=legacy,
                )
            elif thread_id in self._agent_names:
                self._enqueue_subagent("complete", thread_id)
            return
        if method == "error" and own:
            error = params.get("error", {})
            message = error.get("message", "codex error") if isinstance(error, dict) else str(error)
            self._enqueue(
                HarnessEventKind.PROGRESS,
                data={"category": "error", "message": message, "will_retry": bool(params.get("willRetry"))},
                legacy={"type": "error", "message": message, "will_retry": params.get("willRetry", False)},
            )
            return
        if method == "thread/compacted" and own:
            self._on_compacted()
            return
        if method in {"item/started", "item/completed"}:
            item = params.get("item") or {}
            if own and item.get("type") == "contextCompaction":
                if method == "item/completed":
                    self._on_compacted()
                return
            if method == "item/started" and own:
                with self._state_lock:
                    self._unread_notes.clear()
            self._on_item(method, params, own)
            return
        if method == "item/agentMessage/delta" and own:
            legacy = {
                "type": "delta",
                "channel": "message",
                "id": params.get("itemId"),
                "text": params.get("delta", ""),
            }
            self._enqueue(
                HarnessEventKind.MESSAGE_DELTA,
                data={"message_id": params.get("itemId"), "text": params.get("delta", "")},
                legacy=legacy,
            )
            return
        if method == "item/reasoning/summaryTextDelta" and own:
            legacy = {
                "type": "delta",
                "channel": "reasoning",
                "id": params.get("itemId"),
                "text": params.get("delta", ""),
            }
            self._enqueue(
                HarnessEventKind.REASONING_DELTA,
                data={"reasoning_id": params.get("itemId"), "text": params.get("delta", "")},
                legacy=legacy,
            )
            return
        if method == "thread/tokenUsage/updated" and (own or thread_id in self._agent_names):
            self._on_usage(thread_id, params.get("tokenUsage", {}), own)

    def on_server_request(self, msg: dict[str, Any]) -> None:
        method = msg.get("method", "")
        client = self._client
        if client is None:
            return
        if method == "mcpServer/elicitation/request":
            client.respond(msg["id"], {"action": "accept", "content": {}, "_meta": None})
            return
        if method in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"}:
            if self.interactive_approvals:
                self._park_approval(msg)
            else:
                params = msg.get("params", {})
                client.respond(msg["id"], {"decision": "decline"})
                denied = params.get("command") or params.get("reason") or "file change"
                legacy = {
                    "type": "item.completed",
                    "item": {
                        "type": "command_execution",
                        "command": f"[sandbox escalation auto-denied: {denied}]",
                    },
                }
                self._enqueue(
                    HarnessEventKind.TOOL_COMPLETED,
                    data={"tool_type": "approval", "status": "declined"},
                    legacy=legacy,
                )
            return
        client.respond_error(msg["id"], -32601, "not supported by this client")
        self._enqueue(
            HarnessEventKind.TOOL_COMPLETED,
            data={"tool_type": "unsupported_request", "method": method, "status": "declined"},
            legacy={
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "command": f"[auto-denied unsupported request: {method}]",
                },
            },
        )

    def _on_item(self, method: str, params: dict[str, Any], own: bool) -> None:
        item = params.get("item", {})
        kind = item.get("type")
        if own:
            if kind == "subAgentActivity":
                if method == "item/started" and item.get("kind") == "started":
                    child = item.get("agentThreadId", "")
                    name = (item.get("agentPath") or "").rsplit("/", 1)[-1] or "agent"
                    with self._state_lock:
                        self._agent_names[child] = name
                    self._enqueue_subagent("started", child)
                return
            if kind == "collabAgentToolCall" or method != "item/completed":
                return
            mapped = map_codex_item(item)
            if mapped is None:
                return
            self._enqueue_mapped_item(mapped)
            return

        child = params.get("threadId")
        if not child or child not in self._agent_names:
            return
        if kind == "subAgentActivity":
            if method == "item/started" and item.get("kind") == "started":
                grandchild = item.get("agentThreadId", "")
                name = (item.get("agentPath") or "").rsplit("/", 1)[-1] or "agent"
                with self._state_lock:
                    self._agent_names[grandchild] = name
                self._enqueue_subagent("started", grandchild)
            return
        if method != "item/completed":
            return
        if kind == "mcpToolCall":
            self._enqueue_subagent(
                "tool",
                child,
                detail=f"{item.get('server')}.{item.get('tool')}",
                arguments=item.get("arguments"),
                error=bool(item.get("error")),
            )
        elif kind == "commandExecution":
            self._enqueue_subagent("tool", child, detail=item.get("command", ""))
        elif kind == "agentMessage":
            self._enqueue_subagent("message", child, text=item.get("text", ""))

    def _enqueue_mapped_item(self, mapped: dict[str, Any]) -> None:
        item_type = mapped.get("type")
        legacy = {"type": "item.completed", "item": mapped}
        if item_type == "agent_message":
            self._enqueue(HarnessEventKind.MESSAGE_COMPLETED, data={"text": mapped.get("text", "")}, legacy=legacy)
        elif item_type == "reasoning":
            self._enqueue(HarnessEventKind.REASONING_COMPLETED, data={"text": mapped.get("text", "")}, legacy=legacy)
        elif item_type == "file_change":
            changes = mapped.get("changes")
            count = len(changes) if isinstance(changes, list) else 0
            self._enqueue(HarnessEventKind.FILE_CHANGED, data={"change_count": count}, legacy=legacy)
        else:
            self._enqueue(
                HarnessEventKind.TOOL_COMPLETED,
                data={
                    "tool_type": item_type,
                    "server": mapped.get("server"),
                    "tool": mapped.get("tool"),
                    "status": mapped.get("status"),
                    "has_error": bool(mapped.get("error")),
                },
                legacy=legacy,
            )

    def _enqueue_subagent(self, kind: str, thread_id: str, **detail: Any) -> None:
        name = self._agent_names.get(thread_id, "agent")
        legacy = {"type": "subagent", "kind": kind, "thread_id": thread_id, "name": name, **detail}
        canonical = {
            "category": "subagent",
            "subagent_event": kind,
            "thread_id": thread_id,
            "name": name,
        }
        if "detail" in detail:
            canonical["detail"] = detail["detail"]
        if "text" in detail:
            canonical["text"] = detail["text"]
        canonical["has_error"] = bool(detail.get("error"))
        self._enqueue(HarnessEventKind.PROGRESS, data=canonical, legacy=legacy)

    def _on_usage(self, thread_id: str, usage: dict[str, Any], own: bool) -> None:
        with self._state_lock:
            self._thread_usage[thread_id] = usage.get("total", {}) or {}
            if own:
                self._own_last_usage = usage.get("last", {}) or {}
                self._own_context_window = usage.get("modelContextWindow")
            combined: dict[str, int] = {}
            for per_thread in self._thread_usage.values():
                for key, value in per_thread.items():
                    if isinstance(value, int | float):
                        combined[key] = combined.get(key, 0) + int(value)
            payload = {
                "total": combined,
                "last": dict(self._own_last_usage),
                "modelContextWindow": self._own_context_window,
            }
        with suppress(OSError):
            self.usage_path.write_text(json.dumps(payload), encoding="utf-8")
        self._enqueue(
            HarnessEventKind.USAGE,
            data={"usage": payload},
            legacy={"type": "token_usage", "usage": payload},
        )

    def _park_approval(self, msg: dict[str, Any]) -> None:
        params = msg.get("params", {})
        request_id = f"apr_{uuid.uuid4().hex[:10]}"
        kind = "command" if msg["method"] == "item/commandExecution/requestApproval" else "file_change"
        now = time.time()
        public = {
            "request_id": request_id,
            "kind": kind,
            "command": params.get("command"),
            "cwd": params.get("cwd"),
            "reason": params.get("reason"),
            "created_at": now,
            "expires_at": now + self.approval_timeout,
        }
        timer = threading.Timer(
            self.approval_timeout,
            self.resolve_approval_request,
            args=(request_id, "decline"),
        )
        timer.daemon = True
        with self._state_lock:
            self._pending_approvals[request_id] = {"rpc_id": msg["id"], "timer": timer, "public": public}
        timer.start()
        self._enqueue(
            HarnessEventKind.APPROVAL_REQUIRED,
            data=public,
            legacy={"type": "approval_request", **public},
        )

    def _on_compacted(self) -> None:
        if self._on_compacted_external is not None:
            self._on_compacted_external()
        with self._state_lock:
            lost = list(self._unread_notes)
            self._unread_notes.clear()
        self._enqueue(
            HarnessEventKind.PROGRESS,
            data={"category": "context_compaction"},
        )
        if not lost:
            return

        def reinject() -> None:
            for text in lost:
                self.inject_note(text)

        threading.Thread(target=reinject, daemon=True).start()

    # ------------------------------------------------------------ events

    def _new_event(
        self,
        kind: HarnessEventKind,
        *,
        data: dict[str, Any] | None = None,
        legacy: dict[str, Any] | None = None,
    ) -> _QueuedEvent:
        with self._state_lock:
            self._sequence += 1
            sequence = self._sequence
            turn_id = self._active_turn_id
            if legacy is not None:
                self._legacy_events[sequence] = legacy
        return _QueuedEvent(
            HarnessEvent(
                session_id=self.session_id,
                turn_id=turn_id,
                sequence=sequence,
                kind=kind,
                at=datetime.now(UTC).isoformat(timespec="milliseconds"),
                data={key: value for key, value in (data or {}).items() if value is not None},
            ),
            legacy,
        )

    def _enqueue(
        self,
        kind: HarnessEventKind,
        *,
        data: dict[str, Any] | None = None,
        legacy: dict[str, Any] | None = None,
    ) -> None:
        with self._publication_lock:
            queued = self._new_event(kind, data=data, legacy=legacy)
            with self._state_lock:
                event_queue = self._event_queue
            if event_queue is not None:
                event_queue.put(queued)

    @staticmethod
    def _deliver(queued: _QueuedEvent, emit: HarnessEventSink | None) -> None:
        if emit is not None:
            emit(queued.canonical)

    def _check_session(self, session: HarnessSession) -> None:
        if self._session is None or session.session_id != self._session.session_id:
            raise CodexHarnessError("unknown Codex harness session")

    def _check_active_session(self, session: HarnessSession, turn_id: str) -> None:
        self._check_session(session)
        with self._state_lock:
            active_turn_id = self._active_turn_id
        if active_turn_id != turn_id:
            raise CodexHarnessError("unknown active Codex harness turn")


def _normalize_model_provider(value: str | None) -> str | None:
    if value is None:
        return None
    provider = value.strip()
    if not _MODEL_PROVIDER_PATTERN.fullmatch(provider):
        raise CodexHarnessError(f"invalid model provider: {value}")
    return provider


def _codex_input(message: Mapping[str, Any]) -> dict[str, Any]:
    text = _message_text(message)
    return {"type": "text", "text": text, "text_elements": []}


def _message_text(message: Mapping[str, Any]) -> str:
    text = message.get("text", message.get("content", ""))
    if not isinstance(text, str):
        text = json.dumps(text, ensure_ascii=False)
    return text


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        with suppress(OSError):
            temporary.unlink()
