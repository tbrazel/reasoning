"""Durable, prompt-free accounting records for direct OpenRouter calls."""

from __future__ import annotations

import json
import math
import os
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and result >= 0 else None


def _tokens(value: Any) -> int | None:
    result = _number(value)
    return int(result) if result is not None else None


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    priced = [record for record in records if record.get("cost_usd") is not None]
    timestamps = [
        value
        for record in records
        if (value := _text(record.get("started_at"))) is not None
    ]
    return {
        "calls": len(records),
        "completed_calls": sum(record.get("status") == "completed" for record in records),
        "failed_calls": sum(record.get("status") == "failed" for record in records),
        "priced_calls": len(priced),
        "unpriced_calls": len(records) - len(priced),
        "cost_usd": sum(float(record["cost_usd"]) for record in priced),
        "prompt_tokens": sum(int(record.get("prompt_tokens") or 0) for record in records),
        "completion_tokens": sum(int(record.get("completion_tokens") or 0) for record in records),
        "total_tokens": sum(int(record.get("total_tokens") or 0) for record in records),
        "first_call_at": min(timestamps) if timestamps else None,
        "last_call_at": max(timestamps) if timestamps else None,
    }


class OpenRouterCallStore:
    """Append-only call records with aggregate views across projects.

    Each call is a separate atomically written JSON file. Records intentionally
    exclude request messages and generated content: the paper trail covers
    billing, routing, token counts, timing, and termination only.
    """

    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def record(self, project_id: str, call: Mapping[str, Any]) -> dict[str, Any]:
        project_id = project_id.strip()
        if not project_id:
            raise ValueError("project_id is required")
        status = call.get("status")
        if status not in {"completed", "failed"}:
            raise ValueError("OpenRouter call status must be completed or failed")

        usage = call.get("usage") if isinstance(call.get("usage"), Mapping) else {}
        prompt_details = (
            usage.get("prompt_tokens_details")
            if isinstance(usage.get("prompt_tokens_details"), Mapping)
            else {}
        )
        completion_details = (
            usage.get("completion_tokens_details")
            if isinstance(usage.get("completion_tokens_details"), Mapping)
            else {}
        )
        cost_details = (
            usage.get("cost_details")
            if isinstance(usage.get("cost_details"), Mapping)
            else {}
        )
        call_id = f"orcall_{uuid.uuid4().hex}"
        completed_at = _text(call.get("completed_at")) or _now()
        record = {
            "call_id": call_id,
            "provider": "openrouter",
            "project_id": project_id,
            "run_id": _text(call.get("run_id")),
            "sequence": _tokens(call.get("sequence")),
            "status": status,
            "purpose": _text(call.get("purpose")) or "answer",
            "started_at": _text(call.get("started_at")) or completed_at,
            "completed_at": completed_at,
            "duration_ms": _number(call.get("duration_ms")),
            "requested_model": _text(call.get("requested_model")),
            "resolved_model": _text(call.get("resolved_model")),
            "upstream_provider": _text(call.get("upstream_provider")),
            "response_id": _text(call.get("response_id")),
            "finish_reason": _text(call.get("finish_reason")),
            "native_finish_reason": _text(call.get("native_finish_reason")),
            "error": _text(call.get("error")),
            "retryable": bool(call.get("retryable")),
            "retry_after_seconds": _number(call.get("retry_after_seconds")),
            "cost_usd": _number(usage.get("cost")),
            "upstream_cost_usd": _number(cost_details.get("upstream_inference_cost")),
            "prompt_tokens": _tokens(usage.get("prompt_tokens")),
            "completion_tokens": _tokens(usage.get("completion_tokens")),
            "total_tokens": _tokens(usage.get("total_tokens")),
            "reasoning_tokens": _tokens(completion_details.get("reasoning_tokens")),
            "cached_tokens": _tokens(prompt_details.get("cached_tokens")),
            "cache_write_tokens": _tokens(prompt_details.get("cache_write_tokens")),
            "usage": dict(usage),
        }
        _atomic_json(self.root / f"{call_id}.json", record)
        return record

    def _records(self) -> list[dict[str, Any]]:
        records = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in self.root.glob("orcall_*.json")
            if path.is_file()
        ]
        records.sort(
            key=lambda record: (
                str(record.get("started_at") or ""),
                str(record.get("call_id") or ""),
            ),
            reverse=True,
        )
        return records

    def list(
        self,
        *,
        project_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        if offset < 0:
            raise ValueError("offset cannot be negative")
        records = self._records()
        projects: list[dict[str, Any]] = []
        for candidate in sorted({str(record["project_id"]) for record in records}):
            aggregate = _summary(
                [record for record in records if record.get("project_id") == candidate]
            )
            projects.append({"project_id": candidate, **aggregate})
        projects.sort(
            key=lambda aggregate: (
                str(aggregate.get("last_call_at") or ""),
                str(aggregate["project_id"]),
            ),
            reverse=True,
        )
        filtered = (
            [record for record in records if record.get("project_id") == project_id]
            if project_id
            else records
        )
        return {
            "provider": "openrouter",
            "scope": "reasoning-native",
            "summary": _summary(filtered),
            "projects": projects,
            "calls": filtered[offset : offset + limit],
            "total": len(filtered),
            "limit": limit,
            "offset": offset,
        }
