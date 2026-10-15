"""Pure validation of canonical harness event traces."""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import (
    HarnessCapabilities,
    HarnessEvent,
    HarnessEventKind,
    HarnessTurnResult,
    ReasoningVisibility,
    WorkspaceAccess,
)

_TERMINAL_EVENTS = {
    HarnessEventKind.TURN_COMPLETED,
    HarnessEventKind.TURN_FAILED,
    HarnessEventKind.TURN_INTERRUPTED,
}
_TERMINAL_BY_STATUS = {
    "completed": HarnessEventKind.TURN_COMPLETED,
    "failed": HarnessEventKind.TURN_FAILED,
    "cancelled": HarnessEventKind.TURN_INTERRUPTED,
    "interrupted": HarnessEventKind.TURN_INTERRUPTED,
}


@dataclass(frozen=True)
class HarnessTraceIssue:
    code: str
    detail: str


@dataclass(frozen=True)
class HarnessTraceReport:
    valid: bool
    issues: tuple[HarnessTraceIssue, ...] = ()


def validate_harness_trace(
    capabilities: HarnessCapabilities,
    events: tuple[HarnessEvent, ...],
    result: HarnessTurnResult,
) -> HarnessTraceReport:
    """Validate ordering, identity, terminal state, and capability claims."""

    issues: list[HarnessTraceIssue] = []

    def issue(code: str, detail: str) -> None:
        issues.append(HarnessTraceIssue(code=code, detail=detail))

    turn_events = [event for event in events if event.turn_id == result.turn_id]
    if any(event.session_id != result.session_id for event in events):
        issue("session_identity", "every event must belong to the result session")
    sequences = [event.sequence for event in events]
    if sequences != sorted(set(sequences)):
        issue("event_sequence", "event sequences must be unique and strictly increasing")
    if not turn_events or turn_events[0].kind is not HarnessEventKind.TURN_STARTED:
        issue("turn_start", "the first turn event must be turn.started")
    terminal = [event for event in turn_events if event.kind in _TERMINAL_EVENTS]
    if len(terminal) != 1:
        issue("terminal_count", "a turn must have exactly one terminal event")
    else:
        expected = _TERMINAL_BY_STATUS[result.status]
        if terminal[0].kind is not expected:
            issue("terminal_status", f"result status {result.status} requires {expected.value}")
        if turn_events[-1] is not terminal[0]:
            issue("terminal_order", "the terminal event must be the final event for its turn")

    for event in turn_events:
        if event.kind in {HarnessEventKind.MESSAGE_DELTA, HarnessEventKind.REASONING_DELTA} and not capabilities.supports_streaming:
            issue("undeclared_streaming", f"{event.kind.value} requires streaming support")
        if event.kind is HarnessEventKind.REASONING_DELTA and capabilities.reasoning_visibility is not ReasoningVisibility.STREAMED_SUMMARY:
            issue("undeclared_reasoning_stream", "reasoning.delta requires streamed-summary visibility")
        if event.kind is HarnessEventKind.REASONING_COMPLETED and capabilities.reasoning_visibility is ReasoningVisibility.NONE:
            issue("undeclared_reasoning", "reasoning.completed requires reasoning-summary visibility")
        if event.kind in {
            HarnessEventKind.TOOL_REQUESTED,
            HarnessEventKind.TOOL_STARTED,
            HarnessEventKind.TOOL_COMPLETED,
        } and not capabilities.supports_tools:
            issue("undeclared_tools", f"{event.kind.value} requires tool support")
        if event.kind in {HarnessEventKind.APPROVAL_REQUIRED, HarnessEventKind.APPROVAL_RESOLVED} and not capabilities.supports_approvals:
            issue("undeclared_approvals", f"{event.kind.value} requires approval support")
        if event.kind is HarnessEventKind.CHECKPOINT_CREATED and not capabilities.supports_checkpoints:
            issue("undeclared_checkpoints", "checkpoint.created requires checkpoint support")
        if event.kind is HarnessEventKind.USAGE and not capabilities.reports_usage:
            issue("undeclared_usage", "usage events require usage reporting")
        if event.kind is HarnessEventKind.COST and not capabilities.reports_cost:
            issue("undeclared_cost", "cost events require cost reporting")
        if event.kind is HarnessEventKind.FILE_CHANGED and WorkspaceAccess.READ_WRITE not in capabilities.workspace_access:
            issue("undeclared_workspace_write", "file.changed requires read-write workspace capability")
    return HarnessTraceReport(valid=not issues, issues=tuple(issues))
