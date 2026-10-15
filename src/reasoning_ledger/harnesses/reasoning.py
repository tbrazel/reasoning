"""Persistent reference harness built on the Reasoning-owned model/tool loop.

The adapter deliberately separates three concerns:

* ``ReasoningNativeExecutor`` owns one bounded model/tool turn;
* this module owns provider-neutral session history and canonical events;
* the injected completion client and tool invoker own inference routing and
  execution-target access respectively.

The state file contains no credentials or provider-private handles.  It is a
small, auditable checkpoint that can be resumed by a local process or by a
service implementing the same harness contract.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from ..agents.contracts import (
    AgentEvent,
    AgentEventKind,
    AgentRunResult,
    AgentRunSpec,
    PreparedAgentRun,
    PreparedTool,
    PromptBinding,
    SandboxPolicy,
    SessionPolicy,
    SkillBinding,
    ToolBinding,
    ToolInvoker,
)
from ..agents.native import CompletionClient, ReasoningNativeExecutor
from ..agents.research_memory import COMPACTION_PROMPT, validate_memory
from .contracts import (
    HarnessEvent,
    HarnessEventKind,
    HarnessEventSink,
    HarnessSession,
    HarnessSessionSpec,
    HarnessTurnRequest,
    HarnessTurnResult,
    SessionMode,
)
from .profiles import reasoning_native_capabilities

_STATE_VERSION = 2
_TERMINAL_AGENT_EVENTS = {
    AgentEventKind.RUN_COMPLETED,
    AgentEventKind.RUN_CANCELLED,
    AgentEventKind.RUN_FAILED,
}
_USAGE_ALIASES = {
    "input_tokens": ("input_tokens", "prompt_tokens"),
    "output_tokens": ("output_tokens", "completion_tokens"),
    "reasoning_tokens": ("reasoning_tokens",),
    "total_tokens": ("total_tokens",),
}

CompletionClientResolver = Callable[[str | None, str], CompletionClient]
SystemPromptResolver = Callable[[HarnessSession, HarnessTurnRequest], str]


class ReasoningHarnessError(RuntimeError):
    """The persistent Reasoning harness could not satisfy a request."""


@dataclass(frozen=True)
class ReasoningSessionBinding:
    """Credential-free selections pinned by a persisted native session."""

    session_id: str
    model: str
    model_provider: str | None
    target_id: str


@dataclass
class _SessionState:
    session_id: str
    model: str
    model_provider: str | None
    harness: dict[str, Any]
    target: dict[str, Any]
    sandbox: dict[str, Any]
    sequence: int = 0
    history: list[dict[str, str]] = field(default_factory=list)
    compactions: list[dict[str, Any]] = field(default_factory=list)
    turns: list[dict[str, Any]] = field(default_factory=list)
    checkpoint: dict[str, Any] | None = None
    source_archive: list[dict[str, Any]] = field(default_factory=list)
    history_sources: list[str | None] = field(default_factory=list)
    working_memory: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": _STATE_VERSION,
            "session_id": self.session_id,
            "harness_id": "reasoning-native",
            "model": self.model,
            "model_provider": self.model_provider,
            "harness": self.harness,
            "target": self.target,
            "sandbox": self.sandbox,
            "sequence": self.sequence,
            "history": self.history,
            "compactions": self.compactions,
            "turns": self.turns,
            "checkpoint": self.checkpoint,
            "source_archive": self.source_archive,
            "history_sources": self.history_sources,
            "working_memory": self.working_memory,
        }

    def checkpoint_payload(self) -> dict[str, Any]:
        """Return semantic state whose digest is stable across event delivery."""

        value = self.to_dict()
        value.pop("sequence")
        value.pop("checkpoint")
        return value


class ReasoningNativeHarnessAdapter:
    """One durable, inference-agnostic Reasoning reference-harness session."""

    def __init__(
        self,
        *,
        session_id: str,
        state_path: Path,
        client_resolver: CompletionClientResolver,
        system_prompt: str | SystemPromptResolver,
        tool_invoker: ToolInvoker | None = None,
        call_recorder: Callable[[Mapping[str, Any]], Any] | None = None,
        max_context_characters: int = 120_000,
        retained_messages: int = 12,
        compaction_cost_bound: Callable[[HarnessSession, Mapping[str, Any]], float] | None = None,
    ) -> None:
        if not session_id.strip():
            raise ValueError("session_id is required")
        if max_context_characters < 1:
            raise ValueError("max_context_characters must be positive")
        if retained_messages < 1:
            raise ValueError("retained_messages must be positive")
        self.session_id = session_id
        self.state_path = state_path
        self._client_resolver = client_resolver
        self._system_prompt = system_prompt
        self._tool_invoker = tool_invoker
        self._call_recorder = call_recorder
        self._max_context_characters = max_context_characters
        self._retained_messages = retained_messages
        self._compaction_cost_bound = compaction_cost_bound

        self._lock = threading.RLock()
        self._session: HarnessSession | None = None
        self._state: _SessionState | None = None
        self._active_turn_id: str | None = None
        self._cancel_event: threading.Event | None = None

    def inspect_memory(self, session: HarnessSession) -> dict[str, Any]:
        self._check_session(session)
        with self._lock:
            state = self._require_state()
            return json.loads(
                json.dumps(
                    {
                        "working_memory": state.working_memory,
                        "compactions": state.compactions,
                        "source_ids": [item["id"] for item in state.source_archive],
                    }
                )
            )

    def read_source(self, session: HarnessSession, source_id: str, offset: int = 0, length: int = 12000) -> dict[str, Any]:
        self._check_session(session)
        if type(offset) is not int or offset < 0 or type(length) is not int or not 1 <= length <= 12000:
            raise ReasoningHarnessError("source reads require a nonnegative offset and 1–12000 characters")
        with self._lock:
            source = next((item for item in self._require_state().source_archive if item["id"] == source_id), None)
            if source is None:
                raise ReasoningHarnessError("source does not belong to this session")
            content = source["content"]
            return {
                **source,
                "content": content[offset : offset + length],
                "offset": offset,
                "total_characters": len(content),
                "sha256": hashlib.sha256(content.encode()).hexdigest(),
                "truncated": offset + length < len(content),
            }

    @property
    def capabilities(self):
        return reasoning_native_capabilities()

    def create_session(self, spec: HarnessSessionSpec) -> HarnessSession:
        if spec.harness_id != self.capabilities.harness_id:
            raise ReasoningHarnessError(f"unsupported harness: {spec.harness_id}")
        if spec.mode is SessionMode.FORK:
            raise ReasoningHarnessError("the reference harness does not support forked sessions")

        with self._lock:
            if self._session is not None:
                raise ReasoningHarnessError("a Reasoning harness session is already open")
            if spec.mode is SessionMode.RESUME:
                state = _read_state(self.state_path)
                if state.session_id != spec.source_session_id:
                    raise ReasoningHarnessError("the persisted checkpoint does not match source_session_id")
                _validate_resume(state, spec)
            else:
                if self.state_path.exists():
                    raise ReasoningHarnessError("fresh session state already exists; resume it or choose a new state path")
                state = _SessionState(
                    session_id=self.session_id,
                    model=spec.model,
                    model_provider=spec.model_provider,
                    harness=_json_value(asdict(self.capabilities)),
                    target=_json_value(asdict(spec.target)),
                    sandbox=_json_value(asdict(spec.sandbox)),
                )
                _write_state(self.state_path, state)

            if state.session_id != self.session_id:
                raise ReasoningHarnessError("adapter session_id does not match the persisted checkpoint")
            session = HarnessSession(
                session_id=state.session_id,
                capabilities=self.capabilities,
                target=spec.target,
                model=state.model,
                model_provider=state.model_provider,
                adapter_session_ref=f"reasoning:{state.session_id}",
            )
            self._state = state
            self._session = session
            return session

    def run_turn(
        self,
        session: HarnessSession,
        request: HarnessTurnRequest,
        emit: HarnessEventSink | None = None,
    ) -> HarnessTurnResult:
        self._check_session(session)
        if request.mcp_connection_ids:
            raise ReasoningHarnessError("the reference harness does not yet expose MCP connections")

        with self._lock:
            if self._active_turn_id is not None:
                raise ReasoningHarnessError("a Reasoning harness turn is already active")
            self._active_turn_id = request.turn_id
            self._cancel_event = threading.Event()
            cancel_event = self._cancel_event

        self._emit(request.turn_id, HarnessEventKind.TURN_STARTED, {"model": session.model}, emit)
        normalized_messages = tuple(_normalize_message(message) for message in request.messages)
        outcome: AgentRunResult
        prepared: PreparedAgentRun | None = None
        client = None
        deadline = time.monotonic() + request.budget.timeout_seconds
        initial_compaction = None
        try:
            client = self._client_resolver(session.model_provider, session.model)
            execution_request = request
            with self._lock:
                state = self._require_state()
                pending = bool(state.compactions and state.compactions[-1]["status"] == "deferred")
            if pending:
                initial_compaction = self._compact_history(
                    session,
                    request,
                    AgentRunResult(request.turn_id, "completed", usage={"completion_tokens": 0, "cost": 0}),
                    client,
                    deadline,
                    cancel_event,
                )
                if initial_compaction and initial_compaction["status"] != "completed":
                    raise ReasoningHarnessError(initial_compaction["reason"])
                summary_usage = initial_compaction.get("usage", {}) if initial_compaction else {"completion_tokens": 0, "cost": 0}
                output = summary_usage.get("completion_tokens", summary_usage.get("output_tokens"))
                amount = summary_usage.get("cost", summary_usage.get("cost_usd"))
                if request.budget.max_output_tokens is not None and not isinstance(output, int | float):
                    raise ReasoningHarnessError("summary usage is unknown; no further provider call was submitted")
                if request.budget.max_cost_usd is not None and not isinstance(amount, int | float):
                    raise ReasoningHarnessError("summary cost is unknown; no further provider call was submitted")
                remaining_output = request.budget.max_output_tokens - output if request.budget.max_output_tokens is not None else None
                remaining_cost = request.budget.max_cost_usd - amount if request.budget.max_cost_usd is not None else None
                if remaining_output is not None and remaining_output <= 0 or remaining_cost is not None and remaining_cost <= 0:
                    raise ReasoningHarnessError("the turn budget was exhausted during context preparation")
                execution_request = replace(
                    request,
                    budget=replace(
                        request.budget,
                        max_output_tokens=remaining_output,
                        max_cost_usd=remaining_cost,
                        timeout_seconds=max(0.001, deadline - time.monotonic()),
                    ),
                )
            executor = ReasoningNativeExecutor(
                client,
                tool_invoker=lambda name, arguments: self._invoke_tool(session, name, arguments),
                call_recorder=self._call_recorder,
            )
            prepared = self._prepare_run(session, execution_request, normalized_messages)
            outcome = executor.execute(
                prepared,
                lambda event: self._on_agent_event(request.turn_id, event, emit),
                cancelled=cancel_event.is_set,
            )
        except Exception as exc:
            outcome = AgentRunResult(
                run_id=request.turn_id,
                status="failed",
                error=str(exc),
            )
        if initial_compaction and initial_compaction.get("usage"):
            summary_usage = initial_compaction["usage"]
            combined = dict(outcome.usage)
            for key, value in summary_usage.items():
                if isinstance(value, int | float) and not isinstance(value, bool):
                    combined[key] = combined.get(key, 0) + value
            native_cost = outcome.usage.get("cost_usd", outcome.usage.get("cost"))
            summary_cost = summary_usage.get("cost_usd", summary_usage.get("cost"))
            combined.pop("cost", None)
            combined.pop("cost_usd", None)
            if isinstance(summary_cost, int | float) and isinstance(native_cost, int | float):
                combined["cost"] = native_cost + summary_cost
            elif prepared is None and isinstance(summary_cost, int | float):
                combined["cost"] = summary_cost
            outcome = replace(outcome, usage=combined)

        try:
            return self._settle_turn(
                session,
                request,
                normalized_messages,
                outcome,
                prepared.prompt_hashes if prepared is not None else request.prompt_hashes,
                emit,
                client,
                deadline,
                cancel_event,
            )
        except Exception as exc:
            error = f"failed to persist the Reasoning checkpoint: {exc}"
            self._emit(request.turn_id, HarnessEventKind.TURN_FAILED, {"error": error}, emit)
            return HarnessTurnResult(
                session_id=session.session_id,
                turn_id=request.turn_id,
                status="failed",
                output_text=outcome.output_text,
                error=error,
            )
        finally:
            with self._lock:
                self._active_turn_id = None
                self._cancel_event = None

    def interrupt(self, session: HarnessSession, turn_id: str) -> bool:
        self._check_session(session)
        with self._lock:
            if self._active_turn_id != turn_id or self._cancel_event is None:
                return False
            self._cancel_event.set()
            return True

    def close_session(self, session: HarnessSession) -> None:
        self._check_session(session)
        with self._lock:
            if self._active_turn_id is not None:
                raise ReasoningHarnessError("cannot close a Reasoning session while a turn is active")
            self._session = None
            self._state = None

    def _prepare_run(
        self,
        session: HarnessSession,
        request: HarnessTurnRequest,
        messages: tuple[dict[str, str], ...],
    ) -> PreparedAgentRun:
        with self._lock:
            state = self._require_state()
            prior = tuple(dict(message) for message in state.history)
            sandbox_policy = _sandbox_policy(state.sandbox)
            recovery = bool(state.source_archive and state.working_memory)

        tools = request.tools
        if recovery:
            tools += (
                PreparedTool(
                    "research_source_read",
                    "Recover an exact archived source message cited in mathematical working memory.",
                    {
                        "type": "object",
                        "properties": {
                            "source_id": {"type": "string"},
                            "offset": {"type": "integer", "minimum": 0},
                            "length": {"type": "integer", "minimum": 1, "maximum": 12000},
                        },
                        "required": ["source_id"],
                        "additionalProperties": False,
                    },
                ),
            )

        current_context = tuple(dict(message) for message in messages[:-1])
        user_prompt = messages[-1]["content"]
        system_prompt = self._system_prompt(session, request) if callable(self._system_prompt) else self._system_prompt
        prompt_hashes = dict(request.prompt_hashes)
        prompt_hashes["resolved_system"] = hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()
        prompt_hashes["resolved_user"] = hashlib.sha256(user_prompt.encode("utf-8")).hexdigest()
        spec = AgentRunSpec(
            executor_id=self.capabilities.harness_id,
            model=session.model,
            model_provider=session.model_provider,
            system_prompt=PromptBinding("harness-system", "runtime"),
            user_prompt=PromptBinding("harness-turn", request.turn_id),
            skills=tuple(SkillBinding(skill.skill_id, skill.revision_id) for skill in request.skills),
            tools=tuple(ToolBinding(tool.name) for tool in tools),
            mcp_connections=request.mcp_connection_ids,
            output_schema=request.output_schema,
            budget=request.budget,
            sandbox=sandbox_policy,
            session=SessionPolicy(mode="resume", session_id=session.session_id),
            effort=request.effort,
            completion_markers=request.completion_markers,
            completion_artifacts=request.completion_artifacts,
            completion_tool_calls=request.completion_tool_calls,
        )
        return PreparedAgentRun(
            run_id=request.turn_id,
            spec=spec,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            prompt_hashes=prompt_hashes,
            context_messages=(*prior, *current_context),
            tools=tools,
            skills=request.skills,
        )

    def _invoke_tool(self, session, name, arguments):
        if name == "research_source_read":
            if set(arguments) - {"source_id", "offset", "length"} or "source_id" not in arguments:
                raise ReasoningHarnessError("invalid source-reader arguments")
            return self.read_source(session, **arguments)
        if self._tool_invoker is None:
            raise ReasoningHarnessError("this session has no granted external tool invoker")
        return self._tool_invoker(name, arguments)

    def _on_agent_event(
        self,
        turn_id: str,
        event: AgentEvent,
        emit: HarnessEventSink | None,
    ) -> None:
        if event.kind in _TERMINAL_AGENT_EVENTS or event.kind in {
            AgentEventKind.RUN_STARTED,
            AgentEventKind.USAGE,
        }:
            return
        if event.kind is AgentEventKind.MESSAGE_DELTA:
            self._emit(turn_id, HarnessEventKind.MESSAGE_DELTA, _text_data(event.data), emit)
        elif event.kind is AgentEventKind.MESSAGE_COMPLETED:
            self._emit(turn_id, HarnessEventKind.MESSAGE_COMPLETED, _text_data(event.data), emit)
        elif event.kind is AgentEventKind.REASONING_DELTA:
            self._emit(turn_id, HarnessEventKind.REASONING_DELTA, _text_data(event.data), emit)
        elif event.kind is AgentEventKind.TOOL_REQUESTED:
            self._emit(
                turn_id,
                HarnessEventKind.TOOL_REQUESTED,
                {
                    "tool_call_id": event.data.get("tool_call_id"),
                    "name": event.data.get("name"),
                },
                emit,
            )
        elif event.kind is AgentEventKind.TOOL_COMPLETED:
            self._emit(
                turn_id,
                HarnessEventKind.TOOL_COMPLETED,
                {
                    "tool_call_id": event.data.get("tool_call_id"),
                    "name": event.data.get("name"),
                    "ok": bool(event.data.get("ok")),
                    "has_error": not bool(event.data.get("ok")),
                },
                emit,
            )
        elif event.kind is AgentEventKind.ARTIFACT_CREATED:
            self._emit(
                turn_id,
                HarnessEventKind.PROGRESS,
                {"category": "artifact", "artifact_id": event.data.get("artifact_id")},
                emit,
            )
        elif event.kind is AgentEventKind.CHECKPOINT_CREATED:
            return

    def _settle_turn(
        self,
        session: HarnessSession,
        request: HarnessTurnRequest,
        messages: tuple[dict[str, str], ...],
        outcome: AgentRunResult,
        prompt_hashes: Mapping[str, str],
        emit: HarnessEventSink | None,
        client: CompletionClient | None,
        deadline: float,
        cancel_event: threading.Event,
    ) -> HarnessTurnResult:
        usage, cost = _normalize_accounting(outcome.usage)

        status = "interrupted" if outcome.status == "cancelled" else outcome.status
        with self._lock:
            state = self._require_state()
            additions = [dict(message) for message in messages]
            if outcome.output_text:
                additions.append({"role": "assistant", "content": outcome.output_text})
            for message in additions:
                source_id = (
                    "src_" + hashlib.sha256(_canonical_json([state.session_id, len(state.source_archive), message])).hexdigest()[:32]
                )
                state.source_archive.append({"id": source_id, "turn_id": request.turn_id, "status": status, **message})
                if status == "completed":
                    state.history.append(message)
                    state.history_sources.append(source_id)
            state.turns.append(
                {
                    "turn_id": request.turn_id,
                    "status": status,
                    "effort": request.effort,
                    "prompt_hashes": [{"name": str(name), "sha256": str(digest)} for name, digest in sorted(prompt_hashes.items())],
                    "tools": [tool.name for tool in request.tools],
                    "skills": [{"skill_id": skill.skill_id, "revision_id": skill.revision_id} for skill in request.skills],
                    "budget": _json_value(asdict(request.budget)),
                    "usage": usage,
                    "cost": cost,
                    "error": outcome.error,
                    "completed_at": _now(),
                }
            )

        compaction = self._compact_history(session, request, outcome, client, deadline, cancel_event)
        if compaction and compaction.get("usage"):
            additional_usage, additional_cost = _normalize_accounting(compaction["usage"])
            usage = {key: usage.get(key, 0) + additional_usage.get(key, 0) for key in set(usage) | set(additional_usage)}
            cost = {**cost, "amount_usd": cost["amount_usd"] + additional_cost["amount_usd"]} if cost and additional_cost else {}
            with self._lock:
                self._require_state().turns[-1].update(usage=usage, cost=cost)
        if usage:
            self._emit(request.turn_id, HarnessEventKind.USAGE, {"usage": usage}, emit)
        if cost:
            self._emit(request.turn_id, HarnessEventKind.COST, cost, emit)
        if compaction is not None:
            self._emit(
                request.turn_id,
                HarnessEventKind.PROGRESS,
                {
                    "category": "context_compaction",
                    "messages_compacted": compaction.get("messages_compacted", 0),
                    "digest": compaction.get("digest"),
                    "status": compaction["status"],
                    "reason": compaction.get("reason"),
                },
                emit,
            )

        checkpoint = self._checkpoint()
        terminal_kind = {
            "completed": HarnessEventKind.TURN_COMPLETED,
            "failed": HarnessEventKind.TURN_FAILED,
            "interrupted": HarnessEventKind.TURN_INTERRUPTED,
        }[status]
        terminal_data = {"error": outcome.error} if outcome.error else {}
        self._persist_and_deliver_terminal(
            request.turn_id,
            checkpoint,
            terminal_kind,
            terminal_data,
            emit,
        )
        return HarnessTurnResult(
            session_id=session.session_id,
            turn_id=request.turn_id,
            status=status,
            output_text=outcome.output_text,
            structured_output=outcome.structured_output,
            usage=usage,
            cost=cost,
            checkpoint=checkpoint,
            error=outcome.error,
            metadata={"adapter_session_ref": session.adapter_session_ref},
        )

    def _compact_history(self, session, request, outcome, client, deadline, cancel_event) -> dict[str, Any] | None:
        if outcome.status != "completed":
            return None
        with self._lock:
            state = self._require_state()
            pressure = sum(len(message.get("content", "").encode()) + 64 for message in state.history)
            if pressure <= self._max_context_characters or len(state.history) < 2:
                return None
            keep, tail_bound = 0, 0
            for message in reversed(state.history):
                size = len(message["content"].encode()) + 64
                if keep and (tail_bound + size > self._max_context_characters // 3 or keep >= self._retained_messages):
                    break
                keep += 1
                tail_bound += size
            kept = state.history[-keep:]
            kept_sources = state.history_sources[-keep:]
            ids = [source for source in state.history_sources[:-keep] if source is not None]
            if not ids:
                return None
            archive = {item["id"]: item for item in state.source_archive}
            sources = [{"id": identity, "role": archive[identity]["role"], "text": archive[identity]["content"]} for identity in ids]
            memory = state.working_memory
        record = {
            "status": "deferred",
            "messages_compacted": len(sources),
            "digest": hashlib.sha256(_canonical_json(sources)).hexdigest(),
            "created_at": _now(),
            "usage": {},
        }
        used_tokens = outcome.usage.get("completion_tokens", outcome.usage.get("output_tokens"))
        used_cost = outcome.usage.get("cost", outcome.usage.get("cost_usd"))
        try:
            batches, batch, bound = [], [], 0
            for source in sources:
                size = len(_canonical_json(source)) + 64
                if batch and bound + size > 24000:
                    batches.append(batch)
                    batch, bound = [], 0
                batch.append(source)
                bound += size
            if batch:
                batches.append(batch)
            for batch in batches:
                remaining_time = deadline - time.monotonic()
                if client is None or cancel_event.is_set() or remaining_time <= 0:
                    raise ReasoningHarnessError("compaction deferred: the turn is interrupted or its time budget is exhausted")
                maximum = 8192
                if request.budget.max_output_tokens is not None:
                    if (
                        not isinstance(used_tokens, int | float)
                        or isinstance(used_tokens, bool)
                        or not math.isfinite(used_tokens)
                        or used_tokens < 0
                    ):
                        raise ReasoningHarnessError("compaction deferred: remaining output-token budget is unknown")
                    maximum = min(maximum, int(request.budget.max_output_tokens - used_tokens))
                    if maximum < 128:
                        raise ReasoningHarnessError("compaction deferred: the turn's output-token budget is exhausted")
                payload = {
                    "model": session.model,
                    "max_tokens": maximum,
                    "messages": [
                        {"role": "system", "content": COMPACTION_PROMPT},
                        {"role": "user", "content": _canonical_json({"previous_memory": memory, "source_messages": batch}).decode()},
                    ],
                }
                if request.budget.max_cost_usd is not None:
                    if (
                        self._compaction_cost_bound is None
                        or not isinstance(used_cost, int | float)
                        or isinstance(used_cost, bool)
                        or not math.isfinite(used_cost)
                        or used_cost < 0
                    ):
                        raise ReasoningHarnessError("compaction deferred: a price bound and known prior cost are required")
                    quoted = self._compaction_cost_bound(session, payload)
                    if not isinstance(quoted, int | float) or isinstance(quoted, bool) or not math.isfinite(quoted) or quoted < 0:
                        raise ReasoningHarnessError("compaction deferred: its price bound is invalid")
                    if used_cost + quoted > request.budget.max_cost_usd:
                        raise ReasoningHarnessError("compaction deferred: the turn's cost budget cannot cover the summary")
                response, started, call_started = None, _now(), time.monotonic()
                remaining_time = deadline - call_started
                if remaining_time <= 0 or cancel_event.is_set():
                    raise ReasoningHarnessError("compaction deferred: the turn is interrupted or its time budget is exhausted")
                try:
                    response = client.complete(payload, timeout=remaining_time)
                finally:
                    response_usage = response.get("usage", {}) if isinstance(response, dict) else {}
                    if self._call_recorder:
                        try:
                            self._call_recorder(
                                {
                                    "run_id": request.turn_id,
                                    "purpose": "compaction",
                                    "status": "completed" if response else "failed",
                                    "started_at": started,
                                    "completed_at": _now(),
                                    "duration_ms": (time.monotonic() - call_started) * 1000,
                                    "requested_model": session.model,
                                    "response_id": response.get("id") if response else None,
                                    "resolved_model": response.get("model") if response else None,
                                    "usage": response_usage,
                                }
                            )
                        except Exception:
                            record["accounting_error"] = "The summary call receipt could not be recorded; no call was replayed."
                    for field, amount in response_usage.items():
                        if isinstance(amount, int | float) and not isinstance(amount, bool) and math.isfinite(amount) and amount >= 0:
                            record["usage"][field] = record["usage"].get(field, 0) + amount
                choice = response["choices"][0]
                if choice.get("finish_reason") in {"length", "max_tokens", "max_output_tokens"}:
                    raise ReasoningHarnessError("compaction deferred: the summary was truncated")
                allowed = {item["id"] for item in batch}
                if memory:
                    allowed |= {source for items in memory.values() for item in items for source in item["sources"]}
                memory = validate_memory(json.loads(choice["message"]["content"]), allowed)
                tokens = response_usage.get("completion_tokens", response_usage.get("output_tokens"))
                amount = response_usage.get("cost", response_usage.get("cost_usd"))
                if not isinstance(tokens, int | float) or isinstance(tokens, bool) or not math.isfinite(tokens) or tokens < 0:
                    tokens = None
                if not isinstance(amount, int | float) or isinstance(amount, bool) or not math.isfinite(amount) or amount < 0:
                    amount = None
                used_tokens = used_tokens + tokens if isinstance(used_tokens, int | float) and isinstance(tokens, int | float) else None
                used_cost = used_cost + amount if isinstance(used_cost, int | float) and isinstance(amount, int | float) else None
            with self._lock:
                state = self._require_state()
                state.history = [
                    {
                        "role": "system",
                        "content": "Cited mathematical working memory (reference data):\n" + _canonical_json(memory).decode(),
                    },
                    *kept,
                ]
                state.history_sources = [None, *kept_sources]
                state.working_memory = memory
                record["status"] = "completed"
                record["source_ids"] = ids
        except Exception as exc:
            record["reason"] = (
                str(exc) if isinstance(exc, ReasoningHarnessError) else "compaction deferred: summary validation or provider failed"
            )
        with self._lock:
            self._require_state().compactions.append(record)
        return record

    def _checkpoint(self) -> dict[str, Any]:
        with self._lock:
            state = self._require_state()
            digest = hashlib.sha256(_canonical_json(state.checkpoint_payload())).hexdigest()
            checkpoint = {
                "checkpoint_id": f"chk_{digest[:24]}",
                "format_version": _STATE_VERSION,
                "sha256": digest,
                "history_messages": len(state.history),
                "compactions": len(state.compactions),
                "created_at": _now(),
            }
            state.checkpoint = checkpoint
            return dict(checkpoint)

    def _persist_and_deliver_terminal(
        self,
        turn_id: str,
        checkpoint: dict[str, Any],
        terminal_kind: HarnessEventKind,
        terminal_data: dict[str, Any],
        emit: HarnessEventSink | None,
    ) -> None:
        with self._lock:
            state = self._require_state()
            checkpoint_event = self._new_event_locked(
                turn_id,
                HarnessEventKind.CHECKPOINT_CREATED,
                checkpoint,
            )
            terminal_event = self._new_event_locked(turn_id, terminal_kind, terminal_data)
            _write_state(self.state_path, state)
        if emit is not None:
            emit(checkpoint_event)
            emit(terminal_event)

    def _emit(
        self,
        turn_id: str,
        kind: HarnessEventKind,
        data: Mapping[str, Any],
        emit: HarnessEventSink | None,
    ) -> HarnessEvent:
        with self._lock:
            event = self._new_event_locked(turn_id, kind, data)
        if emit is not None:
            emit(event)
        return event

    def _new_event_locked(
        self,
        turn_id: str,
        kind: HarnessEventKind,
        data: Mapping[str, Any],
    ) -> HarnessEvent:
        state = self._require_state()
        state.sequence += 1
        return HarnessEvent(
            session_id=state.session_id,
            turn_id=turn_id,
            sequence=state.sequence,
            kind=kind,
            at=_now(),
            data={key: value for key, value in data.items() if value is not None},
        )

    def _check_session(self, session: HarnessSession) -> None:
        with self._lock:
            if self._session is None or session.session_id != self._session.session_id:
                raise ReasoningHarnessError("unknown Reasoning harness session")

    def _require_state(self) -> _SessionState:
        if self._state is None:
            raise ReasoningHarnessError("Reasoning harness session is not open")
        return self._state


def _read_state(path: Path) -> _SessionState:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ReasoningHarnessError("the requested Reasoning checkpoint does not exist") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ReasoningHarnessError(f"could not read the Reasoning checkpoint: {exc}") from exc
    if not isinstance(value, dict) or value.get("version") not in {1, _STATE_VERSION}:
        raise ReasoningHarnessError("unsupported Reasoning checkpoint format")
    required_strings = ("session_id", "model")
    if any(not isinstance(value.get(key), str) or not value[key].strip() for key in required_strings):
        raise ReasoningHarnessError("Reasoning checkpoint is missing required session fields")
    if value.get("harness_id") != "reasoning-native":
        raise ReasoningHarnessError("checkpoint belongs to another harness")
    provider = value.get("model_provider")
    if provider is not None and not isinstance(provider, str):
        raise ReasoningHarnessError("Reasoning checkpoint has an invalid model provider")
    harness = value.get("harness")
    target = value.get("target")
    sandbox = value.get("sandbox")
    if not isinstance(harness, dict) or not isinstance(target, dict) or not isinstance(sandbox, dict):
        raise ReasoningHarnessError("Reasoning checkpoint is missing execution policy")
    history = value.get("history", [])
    compactions = value.get("compactions", [])
    turns = value.get("turns", [])
    if not all(isinstance(item, list) for item in (history, compactions, turns)):
        raise ReasoningHarnessError("Reasoning checkpoint collections are invalid")
    if not all(isinstance(item, Mapping) for item in history):
        raise ReasoningHarnessError("Reasoning checkpoint history is invalid")
    if not all(isinstance(item, dict) for item in (*compactions, *turns)):
        raise ReasoningHarnessError("Reasoning checkpoint audit records are invalid")
    sequence = value.get("sequence", 0)
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
        raise ReasoningHarnessError("Reasoning checkpoint sequence is invalid")
    if isinstance(value.get("checkpoint"), dict):
        semantic = {key: item for key, item in value.items() if key not in {"sequence", "checkpoint"}}
        if value["checkpoint"].get("sha256") != hashlib.sha256(_canonical_json(semantic)).hexdigest():
            raise ReasoningHarnessError("Reasoning checkpoint digest does not match its persisted state")
    archive, history_sources = value.get("source_archive", []), value.get("history_sources", [])
    if not isinstance(archive, list) or not isinstance(history_sources, list):
        raise ReasoningHarnessError("Reasoning checkpoint source archive is invalid")
    if not archive and history:
        for index, message in enumerate(history):
            identity = "src_" + hashlib.sha256(_canonical_json([value["session_id"], index, message])).hexdigest()[:32]
            archive.append({"id": identity, "turn_id": "checkpoint-import", **_normalize_message(message)})
            history_sources.append(identity)
    if len(history_sources) != len(history) or any(
        not isinstance(item, dict) or not isinstance(item.get("id"), str) or not isinstance(item.get("content"), str) for item in archive
    ):
        raise ReasoningHarnessError("Reasoning checkpoint source archive is invalid")
    state = _SessionState(
        session_id=value["session_id"],
        model=value["model"],
        model_provider=provider,
        harness=harness,
        target=target,
        sandbox=sandbox,
        sequence=sequence,
        history=[_normalize_message(item) for item in history],
        compactions=[dict(item) for item in compactions],
        turns=[dict(item) for item in turns],
        checkpoint=dict(value["checkpoint"]) if isinstance(value.get("checkpoint"), dict) else None,
        source_archive=archive,
        history_sources=history_sources,
        working_memory=value.get("working_memory"),
    )
    return state


def reasoning_session_binding(path: Path) -> ReasoningSessionBinding:
    """Read and verify the public binding of a persisted native session."""

    state = _read_state(path)
    target_id = state.target.get("target_id")
    if not isinstance(target_id, str) or not target_id:
        raise ReasoningHarnessError("Reasoning checkpoint has an invalid execution target")
    return ReasoningSessionBinding(
        session_id=state.session_id,
        model=state.model,
        model_provider=state.model_provider,
        target_id=target_id,
    )


def _validate_resume(state: _SessionState, spec: HarnessSessionSpec) -> None:
    if state.model != spec.model or state.model_provider != spec.model_provider:
        raise ReasoningHarnessError("a resumed session must keep its pinned model and provider")
    if state.harness != _json_value(asdict(reasoning_native_capabilities())):
        raise ReasoningHarnessError("the persisted checkpoint requires another Reasoning harness version")
    if state.target != _json_value(asdict(spec.target)):
        raise ReasoningHarnessError("a resumed session must keep its execution-target capability snapshot")
    if state.sandbox != _json_value(asdict(spec.sandbox)):
        raise ReasoningHarnessError("a resumed session must keep its sandbox policy")


def _write_state(path: Path, state: _SessionState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(state.to_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        with suppress(OSError):
            temporary.unlink()


def _normalize_message(message: Mapping[str, Any]) -> dict[str, str]:
    role = message.get("role", "user")
    if not isinstance(role, str) or role not in {"system", "developer", "user", "assistant", "tool"}:
        role = "user"
    content = message.get("content", message.get("text", ""))
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False, sort_keys=True)
    return {"role": role, "content": content}


def _text_data(data: Mapping[str, Any]) -> dict[str, Any]:
    text = data.get("text", "")
    return {"text": text if isinstance(text, str) else str(text)}


def _normalize_accounting(raw: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    usage: dict[str, Any] = {}
    for target, sources in _USAGE_ALIASES.items():
        value = next(
            (raw.get(source) for source in sources if isinstance(raw.get(source), int | float) and not isinstance(raw.get(source), bool)),
            None,
        )
        if value is not None:
            usage[target] = value
    details = raw.get("output_tokens_details", raw.get("completion_tokens_details"))
    if "reasoning_tokens" not in usage and isinstance(details, Mapping):
        reasoning = details.get("reasoning_tokens")
        if isinstance(reasoning, int | float) and not isinstance(reasoning, bool):
            usage["reasoning_tokens"] = reasoning
    if "total_tokens" not in usage and ("input_tokens" in usage or "output_tokens" in usage):
        usage["total_tokens"] = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
    amount = raw.get("cost_usd", raw.get("cost"))
    cost = (
        {"amount_usd": float(amount), "currency": "USD", "basis": "provider_reported"}
        if isinstance(amount, int | float) and not isinstance(amount, bool)
        else {}
    )
    return usage, cost


def _sandbox_policy(value: Mapping[str, Any]) -> SandboxPolicy:
    return SandboxPolicy(
        mode=str(value.get("mode", "read_only")),
        working_directory=value.get("working_directory"),
        writable_paths=tuple(value.get("writable_paths", ())),
        network=bool(value.get("network", False)),
    )


def _json_value(value: Any) -> Any:
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _json_value(child) for key, child in value.items()}
    if isinstance(value, list | tuple):
        return [_json_value(child) for child in value]
    return value


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")
