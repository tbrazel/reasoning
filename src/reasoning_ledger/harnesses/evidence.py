"""Auditable adapters from product observations to generic routing evidence.

This module deliberately contains no filesystem, credential, or network access.
Callers discover a finite candidate set, load durable observations, and pass
those records here. Keeping the translation pure makes the evidence rules
straightforward to test and keeps provider-specific catalogs out of the router.
"""

from __future__ import annotations

import hashlib
import math
import statistics
from collections.abc import Iterable, Mapping
from typing import Any

from .routing import AutoRoutingPolicy, RoutingEvidence, RoutingObjective
from .selection import SelectedRunConfiguration


def prompt_task_fingerprint(prompt: str) -> str:
    """Return a prompt-free identifier for repeat observations of one task.

    Whitespace is normalized so transport wrapping does not split evidence for
    otherwise identical prompts. This is intentionally stricter than semantic
    similarity: observations from merely related prompts are not silently mixed.
    """

    normalized = " ".join(prompt.split())
    payload = f"reasoning.chat-prompt.v1\0{normalized}".encode()
    return hashlib.sha256(payload).hexdigest()


def estimate_catalog_call_cost(
    model: Mapping[str, Any],
    *,
    prompt: str,
    assumed_output_tokens: int = 1_000,
) -> float | None:
    """Estimate one call using visible token rates and an explicit output budget."""

    input_rate = _finite(model.get("input_mtok_usd"))
    output_rate = _finite(model.get("output_mtok_usd"))
    request_price = _finite(model.get("request_usd")) or 0.0
    if input_rate is None or output_rate is None:
        return None
    # Four UTF-8 bytes per token is deliberately rough. This value is never
    # recorded as realized spend or promoted to benchmark evidence.
    input_tokens = max(1, math.ceil(len(prompt.encode()) / 4))
    return request_price + input_rate * input_tokens / 1_000_000 + output_rate * assumed_output_tokens / 1_000_000


def chat_operation_evidence(
    selection: SelectedRunConfiguration,
    operations: Iterable[Mapping[str, Any]],
    *,
    task_fingerprint: str,
    estimated_cost_usd: float | None = None,
) -> RoutingEvidence:
    """Aggregate exact-task, exact-configuration chat operation records."""

    matching = [
        operation
        for operation in operations
        if operation.get("task_fingerprint") == task_fingerprint
        and _operation_selection_key(operation) == _selection_key(selection)
    ]
    costs = [
        value
        for operation in matching
        if (value := _operation_cost(operation)) is not None
    ]
    latencies = [
        value
        for operation in matching
        if (value := _finite(operation.get("duration_seconds"))) is not None
    ]
    return RoutingEvidence(
        selection=selection,
        task_fingerprint=task_fingerprint,
        observed_calls=len(matching),
        successful_calls=sum(operation.get("status") == "completed" for operation in matching),
        median_cost_usd=statistics.median(costs) if costs else None,
        estimated_cost_usd=estimated_cost_usd,
        median_latency_seconds=statistics.median(latencies) if latencies else None,
    )


def workflow_ranking_evidence(
    selection: SelectedRunConfiguration,
    ranking: Mapping[str, Any],
    *,
    task_fingerprint: str,
) -> RoutingEvidence:
    """Translate one task-compatible workflow ranking without broadening scope."""

    benchmark = ranking.get("benchmark")
    benchmark = benchmark if isinstance(benchmark, Mapping) else {}
    cases = _non_negative_int(benchmark.get("cases"))
    return RoutingEvidence(
        selection=selection,
        task_fingerprint=task_fingerprint,
        observed_calls=_non_negative_int(ranking.get("calls")),
        successful_calls=_non_negative_int(ranking.get("completed_calls")),
        benchmark_cases=cases,
        quality_score=workflow_quality_score(benchmark) if cases else None,
        median_cost_usd=_finite(ranking.get("median_cost_usd")),
        median_latency_seconds=_finite(ranking.get("median_latency_seconds")),
    )


def workflow_quality_score(benchmark: Mapping[str, Any]) -> float | None:
    """Map the moderation benchmark's published measures to one utility.

    False allows receive 70% of the utility because an unsafe acceptance is
    the moderation benchmark's primary failure. Existing workflow ranking
    modes retain their lexicographic safety ordering; this scalar exists only
    for the generic router and is visible in its scorecard.
    """

    false_allow_rate = _unit(benchmark.get("false_allow_rate"))
    accuracy = _unit(benchmark.get("decision_accuracy"))
    flag_f1 = _unit(benchmark.get("flag_f1"))
    schema = _unit(benchmark.get("schema_valid_rate"))
    if all(value is None for value in (false_allow_rate, accuracy, flag_f1, schema)):
        return None
    values = (
        1.0 - false_allow_rate if false_allow_rate is not None else 0.0,
        accuracy or 0.0,
        flag_f1 or 0.0,
        schema or 0.0,
    )
    return sum(value * weight for value, weight in zip(values, (0.70, 0.15, 0.10, 0.05), strict=True))


def product_routing_policy(
    objective: RoutingObjective,
    *,
    source: str,
) -> AutoRoutingPolicy:
    """Return fail-closed evidence gates for chat and workflow surfaces."""

    if objective is RoutingObjective.BALANCED:
        # A balanced score must still have some grounded signal. New chat
        # tasks can use labeled catalog cost estimates; workflows require at
        # least one observed call from a compatible immutable revision.
        return AutoRoutingPolicy(
            objective=objective,
            require_cost_evidence=source == "chat",
            min_observed_calls=1 if source == "workflow" else 0,
        )
    if objective is RoutingObjective.QUALITY:
        return AutoRoutingPolicy(
            objective=objective,
            min_benchmark_cases=1,
            require_quality_evidence=True,
        )
    if objective is RoutingObjective.RELIABILITY:
        return AutoRoutingPolicy(objective=objective, min_observed_calls=1)
    if objective is RoutingObjective.COST:
        return AutoRoutingPolicy(
            objective=objective,
            require_cost_evidence=True,
            require_observed_cost=source == "workflow",
        )
    if objective is RoutingObjective.LATENCY:
        return AutoRoutingPolicy(
            objective=objective,
            min_observed_calls=1,
            require_observed_latency=True,
        )
    return AutoRoutingPolicy(objective=objective)


def _selection_key(selection: SelectedRunConfiguration) -> tuple[str, ...]:
    return (
        selection.harness_id,
        selection.inference_route_id,
        selection.target_id,
        selection.model,
        selection.reasoning_effort or "",
    )


def _operation_selection_key(operation: Mapping[str, Any]) -> tuple[str, ...]:
    harness = operation.get("harness") if isinstance(operation.get("harness"), Mapping) else {}
    target = operation.get("target") if isinstance(operation.get("target"), Mapping) else {}
    return (
        str(operation.get("harness_id") or harness.get("harness_id") or operation.get("executor_id") or ""),
        str(operation.get("inference_route_id") or ""),
        str(operation.get("target_id") or target.get("target_id") or ""),
        str(operation.get("model") or ""),
        str(operation.get("reasoning_effort") or ""),
    )


def _operation_cost(operation: Mapping[str, Any]) -> float | None:
    cost = operation.get("cost")
    if not isinstance(cost, Mapping):
        return None
    return _finite(cost.get("amount_usd"))


def _finite(value: Any) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    number = float(value)
    return number if math.isfinite(number) and number >= 0 else None


def _unit(value: Any) -> float | None:
    number = _finite(value)
    return number if number is not None and number <= 1 else None


def _non_negative_int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0
