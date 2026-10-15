from reasoning_ledger.agents.contracts import AgentEvent, AgentEventKind
from reasoning_ledger.agents.harness_event_compat import canonical_harness_event
from reasoning_ledger.harnesses.contracts import HarnessEvent, HarnessEventKind


def test_legacy_agent_event_uses_the_canonical_harness_wire_contract():
    event = AgentEvent(
        run_id="run_123",
        sequence=4,
        kind=AgentEventKind.REASONING_DELTA,
        at="2026-08-30T12:00:00+00:00",
        data={"text": "checking the boundary"},
    )
    value = canonical_harness_event(event, session_id="wfrun_123")

    parsed = HarnessEvent(
        session_id=value["session_id"],
        turn_id=value["turn_id"],
        sequence=value["sequence"],
        kind=HarnessEventKind(value["kind"]),
        at=value["at"],
        data=value["data"],
    )
    assert parsed.kind is HarnessEventKind.REASONING_DELTA
    assert parsed.data == {"text": "checking the boundary"}


def test_tool_events_never_copy_arguments_or_results_into_workflow_events():
    event = AgentEvent(
        run_id="run_123",
        sequence=2,
        kind=AgentEventKind.TOOL_REQUESTED,
        at="2026-08-30T12:00:00+00:00",
        data={
            "tool_call_id": "call_1",
            "name": "external_query",
            "arguments": {"api_key": "must-not-cross-the-boundary", "query": "safe"},
        },
    )

    value = canonical_harness_event(event, session_id="wfrun_123")
    assert value["data"] == {"tool_call_id": "call_1", "name": "external_query"}


def test_presentation_fields_reject_nested_values_and_nonfinite_usage():
    tool = AgentEvent(
        run_id="run_123",
        sequence=2,
        kind=AgentEventKind.TOOL_COMPLETED,
        at="2026-08-30T12:00:00+00:00",
        data={
            "tool_call_id": {"token": "must-not-cross"},
            "name": ["not", "a", "name"],
            "ok": "true",
        },
    )
    usage = AgentEvent(
        run_id="run_123",
        sequence=3,
        kind=AgentEventKind.USAGE,
        at="2026-08-30T12:00:01+00:00",
        data={
            "total_tokens": 12,
            "cost_usd": float("inf"),
            "input_tokens": -1,
            "output_tokens": 10**10_000,
            "secret": 1,
        },
    )

    assert canonical_harness_event(tool, session_id="wfrun_123")["data"] == {
        "tool_call_id": None,
        "name": None,
        "ok": False,
        "has_error": True,
    }
    assert canonical_harness_event(usage, session_id="wfrun_123")["data"] == {
        "usage": {"total_tokens": 12}
    }
