"""Canonical harness-event envelopes for legacy one-shot executors.

Workflow executors still emit :class:`AgentEvent` while interactive harnesses
emit the provider-neutral harness contract directly. This small compatibility
seam lets durable workflow runs and the web UI consume one event vocabulary
without pretending a one-shot executor has persistent-session capabilities.

Only presentation-safe fields cross this boundary. Tool arguments and results
remain in the executor's own run log, where their existing access controls and
retention rules apply.
"""

from __future__ import annotations

import math
from typing import Any

from .contracts import AgentEvent, AgentEventKind

_KINDS = {
    AgentEventKind.RUN_STARTED: "turn.started",
    AgentEventKind.MESSAGE_DELTA: "message.delta",
    AgentEventKind.MESSAGE_COMPLETED: "message.completed",
    AgentEventKind.REASONING_DELTA: "reasoning.delta",
    AgentEventKind.TOOL_REQUESTED: "tool.requested",
    AgentEventKind.TOOL_COMPLETED: "tool.completed",
    AgentEventKind.ARTIFACT_CREATED: "progress",
    AgentEventKind.CHECKPOINT_CREATED: "checkpoint.created",
    AgentEventKind.USAGE: "usage",
    AgentEventKind.RUN_COMPLETED: "turn.completed",
    AgentEventKind.RUN_CANCELLED: "turn.interrupted",
    AgentEventKind.RUN_FAILED: "turn.failed",
}


def _text(data: dict[str, Any]) -> dict[str, Any]:
    value = data.get("text")
    return {"text": value if isinstance(value, str) else ""}


def _string(data: dict[str, Any], key: str) -> str | None:
    value = data.get(key)
    return value if isinstance(value, str) else None


def _finite_usage_number(value: Any) -> bool:
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and 0 <= value <= 1_000_000_000_000_000
        and math.isfinite(value)
    )


def _safe_data(event: AgentEvent) -> dict[str, Any]:
    data = event.data
    if event.kind in {
        AgentEventKind.MESSAGE_DELTA,
        AgentEventKind.MESSAGE_COMPLETED,
        AgentEventKind.REASONING_DELTA,
    }:
        return _text(data)
    if event.kind is AgentEventKind.TOOL_REQUESTED:
        return {
            "tool_call_id": _string(data, "tool_call_id"),
            "name": _string(data, "name"),
        }
    if event.kind is AgentEventKind.TOOL_COMPLETED:
        ok = data.get("ok") is True
        return {
            "tool_call_id": _string(data, "tool_call_id"),
            "name": _string(data, "name"),
            "ok": ok,
            "has_error": not ok,
        }
    if event.kind is AgentEventKind.ARTIFACT_CREATED:
        return {
            "category": "artifact",
            "artifact_id": _string(data, "artifact_id"),
            "kind": _string(data, "kind"),
        }
    if event.kind is AgentEventKind.CHECKPOINT_CREATED:
        return {"checkpoint_id": _string(data, "checkpoint_id")}
    if event.kind is AgentEventKind.USAGE:
        allowed = {
            key: value
            for key, value in data.items()
            if key in {
                "input_tokens",
                "output_tokens",
                "reasoning_tokens",
                "total_tokens",
                "prompt_tokens",
                "completion_tokens",
                "cost_usd",
                "estimated_cost_usd",
            }
            and _finite_usage_number(value)
        }
        return {"usage": allowed}
    if event.kind is AgentEventKind.RUN_FAILED:
        error = data.get("error")
        return {"error": error if isinstance(error, str) else "agent run failed"}
    return {}


def canonical_harness_event(
    event: AgentEvent,
    *,
    session_id: str,
    turn_id: str | None = None,
) -> dict[str, Any]:
    """Return the v1 wire representation of one canonical harness event."""

    return {
        "session_id": session_id,
        "turn_id": turn_id or event.run_id,
        "sequence": event.sequence,
        "kind": _KINDS[event.kind],
        "at": event.at,
        "data": _safe_data(event),
    }
