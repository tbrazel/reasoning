from __future__ import annotations

from datetime import UTC, datetime

from reasoning_ledger.chat_harness import HarnessChatEventMapper
from reasoning_ledger.harnesses import HarnessEvent, HarnessEventKind


def _event(kind: HarnessEventKind, data: dict | None = None, sequence: int = 1):
    return HarnessEvent(
        session_id="session",
        turn_id="op_turn",
        sequence=sequence,
        kind=kind,
        at=datetime.now(UTC).isoformat(),
        data=data or {},
    )


def _mapper(prior_usage: dict | None = None):
    return HarnessChatEventMapper(
        executor_id="reasoning-native",
        model="example/model",
        model_provider="openrouter",
        operation_id="op_turn",
        prior_usage=prior_usage,
    )


def test_reasoning_precedes_the_completed_assistant_message():
    mapper = _mapper()
    live = mapper.consume(_event(HarnessEventKind.REASONING_DELTA, {"text": "Consider the cases."}))
    completed = mapper.consume(_event(HarnessEventKind.MESSAGE_COMPLETED, {"text": "The result follows."}, 2))

    assert live[0].event["channel"] == "reasoning"
    assert [item.transcript["role"] for item in completed] == [
        "reasoning",
        "assistant",
    ]
    assert completed[1].event["item"]["executor_id"] == "reasoning-native"
    assert mapper.assistant_published


def test_tool_activity_never_exposes_arguments_or_results():
    mapper = _mapper()
    mapper.consume(
        _event(
            HarnessEventKind.TOOL_REQUESTED,
            {"tool_call_id": "call_1", "name": "artifact_read"},
        )
    )
    emissions = mapper.consume(
        _event(
            HarnessEventKind.TOOL_COMPLETED,
            {
                "tool_call_id": "call_1",
                "name": "artifact_read",
                "ok": True,
                "arguments": {"sensitive_input": "never"},
                "result": {"private": "never"},
            },
            2,
        )
    )

    item = emissions[0].event["item"]
    assert item["tool"] == "artifact_read"
    assert item["arguments"] == {}
    assert item["result"] == {}
    assert "sensitive_input" not in repr(emissions)
    assert "private" not in repr(emissions)


def test_usage_accumulates_in_the_existing_chat_payload_shape():
    mapper = _mapper(
        {
            "last": {"inputTokens": 8, "outputTokens": 2, "totalTokens": 10},
            "total": {"inputTokens": 8, "outputTokens": 2, "totalTokens": 10},
            "modelContextWindow": None,
        }
    )
    emissions = mapper.consume(
        _event(
            HarnessEventKind.USAGE,
            {"usage": {"input_tokens": 5, "output_tokens": 3}},
        )
    )

    usage = emissions[0].event["usage"]
    assert usage["last"] == {
        "inputTokens": 5,
        "outputTokens": 3,
        "totalTokens": 8,
    }
    assert usage["total"] == {
        "inputTokens": 13,
        "outputTokens": 5,
        "totalTokens": 18,
    }


def test_completed_message_fallback_is_idempotent():
    mapper = _mapper()
    first = mapper.completed_message("Visible answer")
    second = mapper.completed_message("Visible answer")

    assert len(first) == 1
    assert first[0].transcript["role"] == "assistant"
    assert second == ()
