"""Translate provider-neutral harness events into durable chat events.

Harness adapters speak the canonical event vocabulary in
``reasoning_ledger.harnesses``.  The workbench chat UI has its own stable,
provider-independent vocabulary for streamed text, completed items, usage,
and transcript entries.  This module is the only bridge between those two
contracts for non-Codex harnesses.

The bridge deliberately omits tool arguments and results.  Harness adapters
may execute tools over remote or differently trusted targets, so their chat
activity records expose only the tool name, call identifier, and outcome.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from .harnesses import HarnessEvent, HarnessEventKind


@dataclass(frozen=True)
class ChatHarnessEmission:
    """One browser event and its optional durable transcript counterpart."""

    event: dict[str, Any]
    transcript: dict[str, Any] | None = None
    repair_target: tuple[str, str] | None = None


class HarnessChatEventMapper:
    """Stateful canonical-event bridge for one harness turn."""

    def __init__(
        self,
        *,
        executor_id: str,
        model: str,
        model_provider: str | None,
        operation_id: str,
        prior_usage: dict[str, Any] | None = None,
    ) -> None:
        self._metadata = {
            "executor_id": executor_id,
            "model": model,
            "model_provider": model_provider,
            "operation_id": operation_id,
        }
        self._message_stream_id = f"msgstream_{uuid.uuid4().hex[:12]}"
        self._reasoning_stream_id = f"reasoning_{uuid.uuid4().hex[:12]}"
        self._reasoning_parts: list[str] = []
        self._reasoning_published = False
        self._assistant_published = False
        self._error_published = False
        self._tool_names: dict[str, str] = {}
        self._prior_usage = _usage_bucket((prior_usage or {}).get("total"))
        self.latest_usage: dict[str, Any] | None = None
        self.terminal_status: str | None = None

    @property
    def assistant_published(self) -> bool:
        return self._assistant_published

    @property
    def error_published(self) -> bool:
        return self._error_published

    def consume(self, event: HarnessEvent) -> tuple[ChatHarnessEmission, ...]:
        """Map one canonical event without retaining provider-private data."""

        if event.kind is HarnessEventKind.MESSAGE_DELTA:
            text = _text(event.data.get("text"))
            return self._live_delta("message", self._message_stream_id, text)

        if event.kind is HarnessEventKind.REASONING_DELTA:
            text = _text(event.data.get("text"))
            if text:
                self._reasoning_parts.append(text)
            return self._live_delta("reasoning", self._reasoning_stream_id, text)

        if event.kind is HarnessEventKind.REASONING_COMPLETED:
            text = _text(event.data.get("text")) or "".join(self._reasoning_parts)
            return self._completed_reasoning(text)

        if event.kind is HarnessEventKind.MESSAGE_COMPLETED:
            emissions = list(self._completed_reasoning("".join(self._reasoning_parts)))
            text = _text(event.data.get("text"))
            if text and not self._assistant_published:
                self._assistant_published = True
                message_id = f"msg_{uuid.uuid4().hex[:12]}"
                item = {
                    "type": "agent_message",
                    "text": text,
                    "message_id": message_id,
                    **self._metadata,
                }
                emissions.append(
                    ChatHarnessEmission(
                        event={"type": "item.completed", "item": item},
                        transcript={
                            "role": "assistant",
                            "text": text,
                            "message_id": message_id,
                            **self._metadata,
                        },
                        repair_target=(message_id, text),
                    )
                )
            return tuple(emissions)

        if event.kind is HarnessEventKind.TOOL_REQUESTED:
            call_id = _text(event.data.get("tool_call_id"))
            name = _text(event.data.get("name"))
            if call_id and name:
                self._tool_names[call_id] = name
            return ()

        if event.kind is HarnessEventKind.TOOL_COMPLETED:
            call_id = _text(event.data.get("tool_call_id"))
            name = _text(event.data.get("name")) or self._tool_names.get(call_id, "tool")
            ok = event.data.get("ok") is True and event.data.get("has_error") is not True
            item = {
                "type": "mcp_tool_call",
                "server": "reasoning",
                "tool": name,
                "call_id": call_id,
                "status": "completed" if ok else "failed",
                "arguments": {},
                "result": {},
                **self._metadata,
            }
            if not ok:
                item["error"] = {"message": "The mediated tool call failed."}
            return (
                ChatHarnessEmission(
                    event={"type": "item.completed", "item": item},
                    transcript={
                        "role": "activity",
                        "item": item,
                        "operation_id": self._metadata["operation_id"],
                    },
                ),
            )

        if event.kind is HarnessEventKind.PROGRESS:
            category = _text(event.data.get("category")) or "progress"
            detail = _progress_detail(event.data)
            item = {
                "type": "harness_progress",
                "category": category,
                "detail": detail,
                **self._metadata,
            }
            return (
                ChatHarnessEmission(
                    event={"type": "item.completed", "item": item},
                    transcript={
                        "role": "activity",
                        "item": item,
                        "operation_id": self._metadata["operation_id"],
                    },
                ),
            )

        if event.kind is HarnessEventKind.USAGE:
            last = _usage_bucket(event.data.get("usage"))
            total = dict(self._prior_usage)
            for key, value in last.items():
                total[key] = total.get(key, 0) + value
            self.latest_usage = {
                "last": last,
                "total": total,
                "modelContextWindow": None,
            }
            return (ChatHarnessEmission(event={"type": "token_usage", "usage": self.latest_usage}),)

        terminal = {
            HarnessEventKind.TURN_COMPLETED: "completed",
            HarnessEventKind.TURN_FAILED: "failed",
            HarnessEventKind.TURN_INTERRUPTED: "interrupted",
        }.get(event.kind)
        if terminal is not None:
            self.terminal_status = terminal
            error = _text(event.data.get("error"))
            if terminal == "failed" and error and not self._error_published:
                self._error_published = True
                return (
                    ChatHarnessEmission(
                        event={"type": "error", "message": error},
                        transcript={"role": "error", "text": error, **self._metadata},
                    ),
                )
        return ()

    def completed_message(self, text: str) -> tuple[ChatHarnessEmission, ...]:
        """Publish a result fallback when an adapter omitted completion events."""

        synthetic = HarnessEvent(
            session_id="fallback",
            turn_id=str(self._metadata["operation_id"]),
            sequence=1,
            kind=HarnessEventKind.MESSAGE_COMPLETED,
            at="fallback",
            data={"text": text},
        )
        return self.consume(synthetic)

    def failed(self, message: str) -> tuple[ChatHarnessEmission, ...]:
        """Publish one terminal error if the canonical stream did not."""

        if self._error_published:
            return ()
        self._error_published = True
        return (
            ChatHarnessEmission(
                event={"type": "error", "message": message},
                transcript={"role": "error", "text": message, **self._metadata},
            ),
        )

    def _completed_reasoning(self, text: str) -> tuple[ChatHarnessEmission, ...]:
        if not text or self._reasoning_published:
            return ()
        self._reasoning_published = True
        item = {
            "type": "reasoning",
            "text": text,
            **self._metadata,
        }
        return (
            ChatHarnessEmission(
                event={"type": "item.completed", "item": item},
                transcript={
                    "role": "reasoning",
                    "text": text,
                    "operation_id": self._metadata["operation_id"],
                },
            ),
        )

    @staticmethod
    def _live_delta(
        channel: str,
        stream_id: str,
        text: str,
    ) -> tuple[ChatHarnessEmission, ...]:
        if not text:
            return ()
        return (
            ChatHarnessEmission(
                event={
                    "type": "delta",
                    "channel": channel,
                    "id": stream_id,
                    "text": text,
                }
            ),
        )


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _usage_bucket(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    aliases = {
        "inputTokens": ("inputTokens", "input_tokens"),
        "cachedInputTokens": ("cachedInputTokens", "cached_input_tokens"),
        "outputTokens": ("outputTokens", "output_tokens"),
        "reasoningTokens": ("reasoningTokens", "reasoning_tokens"),
        "totalTokens": ("totalTokens", "total_tokens"),
    }
    bucket: dict[str, int] = {}
    for target, sources in aliases.items():
        number = next(
            (value[source] for source in sources if isinstance(value.get(source), int | float) and not isinstance(value.get(source), bool)),
            None,
        )
        if number is not None:
            bucket[target] = int(number)
    if "totalTokens" not in bucket and ("inputTokens" in bucket or "outputTokens" in bucket):
        bucket["totalTokens"] = bucket.get("inputTokens", 0) + bucket.get("outputTokens", 0)
    return bucket


def _progress_detail(data: dict[str, Any]) -> str:
    category = _text(data.get("category"))
    if category == "artifact":
        artifact_id = _text(data.get("artifact_id"))
        return f"Created {artifact_id}" if artifact_id else "Created an artifact"
    if category == "context_compaction":
        count = data.get("messages_compacted")
        return f"Compacted {count} earlier messages" if isinstance(count, int) else "Compacted earlier context"
    return _text(data.get("detail")) or category or "Harness progress"
