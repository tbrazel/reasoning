"""Task-aware, evidence-backed rankings for workflow model configurations."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections import defaultdict
from copy import deepcopy
from typing import Any

_CANDIDATE_KEYS = {
    "executor_id",
    "model",
    "model_provider",
    "effort",
    "max_cost_usd",
}
_UI_KEYS = {"position", "created_at", "updated_at"}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _strip_candidate_identity(value: Any) -> Any:
    if isinstance(value, list):
        return [_strip_candidate_identity(item) for item in value]
    if not isinstance(value, dict):
        return value
    return {
        key: _strip_candidate_identity(item)
        for key, item in sorted(value.items())
        if key not in _CANDIDATE_KEYS and key not in _UI_KEYS
    }


def workflow_task_fingerprint(workflow: dict[str, Any]) -> str:
    """Fingerprint task semantics while intentionally ignoring the candidate roster."""

    workflow_type = str(workflow.get("workflow_type") or "reasoning")
    presentation = workflow.get("presentation") if isinstance(workflow.get("presentation"), dict) else {}
    recipe = str(presentation.get("recipe") or workflow_type)
    if workflow_type == "model_comparison":
        recipe = "model_comparison"
    elif workflow_type == "model_benchmark":
        recipe = str((workflow.get("benchmark") or {}).get("kind") or "model_benchmark")

    nodes = workflow.get("nodes") if isinstance(workflow.get("nodes"), list) else []
    if workflow_type in {"model_comparison", "model_benchmark"}:
        normalized_nodes = []
        seen: set[str] = set()
        for node in nodes:
            if not isinstance(node, dict):
                continue
            normalized = {
                "kind": node.get("kind"),
                "config": _strip_candidate_identity(node.get("config") or {}),
            }
            encoded = _canonical(normalized)
            if encoded not in seen:
                normalized_nodes.append(normalized)
                seen.add(encoded)
        normalized_edges: list[dict[str, Any]] = []
        variant_conditions = sorted({
            _canonical(_strip_candidate_identity(override))
            for variant in workflow.get("variants") or []
            if isinstance(variant, dict)
            for override in (variant.get("node_overrides") or {}).values()
            if isinstance(override, dict)
        })
    else:
        normalized_nodes = [_strip_candidate_identity(node) for node in nodes if isinstance(node, dict)]
        normalized_edges = _strip_candidate_identity(workflow.get("edges") or [])
        variant_conditions = _strip_candidate_identity(workflow.get("variants") or [])

    payload = {
        "workflow_type": workflow_type,
        "recipe": recipe,
        "nodes": normalized_nodes,
        "edges": normalized_edges,
        "variant_conditions": variant_conditions,
        "dataset": _strip_candidate_identity(workflow.get("dataset")),
        "dataset_ref": _strip_candidate_identity(workflow.get("dataset_ref")),
        "benchmark": _strip_candidate_identity(workflow.get("benchmark")),
        "evaluators": _strip_candidate_identity(workflow.get("evaluators") or []),
        "input_contract": _strip_candidate_identity(workflow.get("input_contract")),
        "output_contract": _strip_candidate_identity(workflow.get("output_contract")),
    }
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _merge_config(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value
    return merged


def _effective_config(snapshot: dict[str, Any], output: dict[str, Any]) -> dict[str, Any]:
    raw_node_id = str(output.get("node_id") or "")
    node_ids = [raw_node_id]
    if "--" in raw_node_id:
        node_ids.extend(reversed(raw_node_id.split("--")))
    nodes = {
        str(node.get("id")): node
        for node in snapshot.get("nodes") or []
        if isinstance(node, dict)
    }
    node = next((nodes[node_id] for node_id in node_ids if node_id in nodes), {})
    config = deepcopy(node.get("config") or {}) if isinstance(node, dict) else {}
    variant_id = str(output.get("variant_id") or "default")
    for variant in snapshot.get("variants") or []:
        if not isinstance(variant, dict) or str(variant.get("variant_id")) != variant_id:
            continue
        overrides = variant.get("node_overrides") or {}
        for node_id in node_ids:
            override = overrides.get(node_id)
            if isinstance(override, dict):
                config = _merge_config(config, override)
                break
        break
    return config


def _configuration(output: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    effective = _effective_config(snapshot, output)
    value = {
        "executor_id": str(output.get("executor_id") or effective.get("executor_id") or "unknown"),
        "model_provider": output.get("model_provider", effective.get("model_provider")),
        "model": str(output.get("model") or effective.get("model") or "unknown"),
        "effort": effective.get("effort"),
    }
    value["configuration_id"] = hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()[:20]
    return value


def _finite(value: Any) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _quality_key(item: dict[str, Any]) -> tuple[Any, ...]:
    benchmark = item.get("benchmark") or {}
    return (
        benchmark.get("false_allow_rate") if benchmark.get("false_allow_rate") is not None else math.inf,
        -(benchmark.get("decision_accuracy") if benchmark.get("decision_accuracy") is not None else -math.inf),
        -(benchmark.get("flag_f1") if benchmark.get("flag_f1") is not None else -math.inf),
        -(benchmark.get("schema_valid_rate") if benchmark.get("schema_valid_rate") is not None else -math.inf),
        item.get("median_cost_usd") if item.get("median_cost_usd") is not None else math.inf,
        item["model"],
    )


def _quality_vector(item: dict[str, Any]) -> tuple[float, float, float, float]:
    benchmark = item.get("benchmark") or {}
    return (
        benchmark.get("false_allow_rate") if benchmark.get("false_allow_rate") is not None else math.inf,
        -(benchmark.get("decision_accuracy") if benchmark.get("decision_accuracy") is not None else -math.inf),
        -(benchmark.get("flag_f1") if benchmark.get("flag_f1") is not None else -math.inf),
        -(benchmark.get("schema_valid_rate") if benchmark.get("schema_valid_rate") is not None else -math.inf),
    )


def _dominates(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_cost = left.get("median_cost_usd")
    right_cost = right.get("median_cost_usd")
    if left_cost is None or right_cost is None:
        return False
    left_values = (*_quality_vector(left), left_cost)
    right_values = (*_quality_vector(right), right_cost)
    return all(a <= b for a, b in zip(left_values, right_values, strict=True)) and any(
        a < b for a, b in zip(left_values, right_values, strict=True)
    )


def _publisher(model: str) -> str:
    return model.split("/", 1)[0].lower()


def _mode(mode_id: str, label: str, description: str, values: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "mode_id": mode_id,
        "label": label,
        "description": description,
        "available": len(values) >= 2,
        "configuration_ids": [item["configuration_id"] for item in values],
        "model_ids": [item["model"] for item in values],
    }


def aggregate_workflow_model_rankings(
    workflow: dict[str, Any],
    runs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Aggregate compatible durable runs into explainable selection modes."""

    fingerprint = workflow_task_fingerprint(workflow)
    compatible = [
        run for run in runs
        if isinstance(run.get("workflow_snapshot"), dict)
        and workflow_task_fingerprint(run["workflow_snapshot"]) == fingerprint
    ]
    aggregates: dict[str, dict[str, Any]] = {}
    score_samples: dict[str, dict[str, list[tuple[float, int]]]] = defaultdict(lambda: defaultdict(list))
    false_allow_counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])

    def ensure(config: dict[str, Any]) -> dict[str, Any]:
        key = config["configuration_id"]
        if key not in aggregates:
            aggregates[key] = {
                **config,
                "calls": 0,
                "completed_calls": 0,
                "failed_calls": 0,
                "priced_calls": 0,
                "unpriced_calls": 0,
                "latencies": [],
                "costs": [],
                "serving_providers": set(),
                "latest_at": None,
                "benchmark_cases": 0,
            }
        return aggregates[key]

    for run in compatible:
        snapshot = run["workflow_snapshot"]
        model_configs: dict[tuple[str, str, str | None], list[dict[str, Any]]] = defaultdict(list)
        for output in (run.get("outputs") or {}).get("nodes") or []:
            if not isinstance(output, dict):
                continue
            config = _configuration(output, snapshot)
            item = ensure(config)
            item["calls"] += 1
            if output.get("status") == "completed":
                item["completed_calls"] += 1
            elif output.get("status") == "failed":
                item["failed_calls"] += 1
            latency = _finite(output.get("duration_seconds"))
            if latency is not None:
                item["latencies"].append(latency)
            cost = _finite((output.get("cost") or {}).get("amount_usd"))
            if cost is None:
                item["unpriced_calls"] += 1
            else:
                item["priced_calls"] += 1
                item["costs"].append(cost)
            serving_provider = output.get("serving_provider")
            if isinstance(serving_provider, str) and serving_provider:
                item["serving_providers"].add(serving_provider)
            observed_at = output.get("completed_at") or run.get("updated_at") or run.get("created_at")
            if observed_at and (item["latest_at"] is None or observed_at > item["latest_at"]):
                item["latest_at"] = observed_at
            model_configs[(config["executor_id"], config["model"], config["model_provider"])].append(config)

        benchmark = (run.get("outputs") or {}).get("benchmark") or {}
        for score in benchmark.get("models") or []:
            if not isinstance(score, dict):
                continue
            lookup = (
                str(score.get("executor_id") or "unknown"),
                str(score.get("model") or "unknown"),
                score.get("model_provider"),
            )
            candidates = model_configs.get(lookup) or []
            config = candidates[0] if candidates else {
                "executor_id": lookup[0],
                "model_provider": lookup[2],
                "model": lookup[1],
                "effort": None,
            }
            if "configuration_id" not in config:
                config["configuration_id"] = hashlib.sha256(_canonical(config).encode("utf-8")).hexdigest()[:20]
            item = ensure(config)
            cases = int(score.get("cases") or 0)
            item["benchmark_cases"] += cases
            for field in ("decision_accuracy", "flag_f1", "flag_precision", "flag_recall", "schema_valid_rate"):
                value = _finite(score.get(field))
                if value is not None and cases > 0:
                    score_samples[item["configuration_id"]][field].append((value, cases))
            false_allow_counts[item["configuration_id"]][0] += int(score.get("false_allow_count") or 0)
            unsafe_cases = sum(
                1 for case in score.get("case_results") or []
                if isinstance(case, dict) and (case.get("expected") or {}).get("decision") != "allow"
            )
            false_allow_counts[item["configuration_id"]][1] += unsafe_cases

    configurations: list[dict[str, Any]] = []
    for key, raw in aggregates.items():
        calls = raw["calls"]
        completed = raw["completed_calls"]
        samples = score_samples.get(key, {})
        benchmark = None
        if raw["benchmark_cases"] > 0:
            benchmark = {
                "cases": raw["benchmark_cases"],
                **{
                    field: (sum(value * weight for value, weight in values) / sum(weight for _, weight in values))
                    for field, values in samples.items()
                    if values
                },
            }
            false_allows, unsafe = false_allow_counts[key]
            benchmark["false_allow_count"] = false_allows
            benchmark["unsafe_cases"] = unsafe
            benchmark["false_allow_rate"] = false_allows / unsafe if unsafe else None
        configurations.append({
            "configuration_id": key,
            "executor_id": raw["executor_id"],
            "model_provider": raw["model_provider"],
            "model": raw["model"],
            "effort": raw["effort"],
            "calls": calls,
            "completed_calls": completed,
            "failed_calls": raw["failed_calls"],
            "success_rate": completed / calls if calls else None,
            "median_latency_seconds": _median(raw["latencies"]),
            "average_latency_seconds": _mean(raw["latencies"]),
            "priced_calls": raw["priced_calls"],
            "unpriced_calls": raw["unpriced_calls"],
            "median_cost_usd": _median(raw["costs"]),
            "average_cost_usd": _mean(raw["costs"]),
            "serving_providers": sorted(raw["serving_providers"]),
            "latest_at": raw["latest_at"],
            "benchmark": benchmark,
        })

    reliable = [item for item in configurations if item["calls"] and (item["success_rate"] or 0) >= 0.8]
    quality = sorted([item for item in configurations if item["benchmark"]], key=_quality_key)
    fastest = sorted(
        [item for item in reliable if item["median_latency_seconds"] is not None],
        key=lambda item: (item["median_latency_seconds"], -(item["success_rate"] or 0), item["model"]),
    )
    cheapest = sorted(
        [item for item in reliable if item["median_cost_usd"] is not None],
        key=lambda item: (item["median_cost_usd"], -(item["success_rate"] or 0), item["model"]),
    )
    frontier: list[dict[str, Any]] = []
    for candidate in quality:
        candidate_cost = candidate["median_cost_usd"]
        if candidate_cost is None:
            continue
        dominated = any(other is not candidate and _dominates(other, candidate) for other in quality)
        if not dominated:
            frontier.append(candidate)
    value = sorted(frontier, key=lambda item: (item["median_cost_usd"], _quality_key(item)))
    diverse_source = quality or fastest or cheapest
    diverse: list[dict[str, Any]] = []
    publishers: set[str] = set()
    for item in diverse_source:
        publisher = _publisher(item["model"])
        if publisher not in publishers:
            diverse.append(item)
            publishers.add(publisher)
    for item in diverse_source:
        if item not in diverse:
            diverse.append(item)

    return {
        "schema_version": 1,
        "workflow_id": workflow.get("workflow_id"),
        "task_fingerprint": fingerprint,
        "scope": "compatible workflow revisions",
        "total_runs": len(runs),
        "compatible_runs": len(compatible),
        "configurations": sorted(configurations, key=lambda item: (item["model"], item["executor_id"])),
        "modes": [
            _mode(
                "best_quality",
                "Best quality",
                "Benchmark order: false allows, decision accuracy, flag F1, schema validity, then observed cost.",
                quality,
            ),
            _mode("best_value", "Best value", "Non-dominated benchmark quality and observed-cost frontier, cheapest first.", value),
            _mode(
                "fastest_reliable",
                "Fastest reliable",
                "Median observed latency among configurations with at least 80% successful calls.",
                fastest,
            ),
            _mode(
                "cheapest_reliable",
                "Cheapest reliable",
                "Median observed call cost among configurations with at least 80% successful calls.",
                cheapest,
            ),
            _mode("diverse", "Diverse observed", "Best available evidence with publisher families spread before repeats.", diverse),
        ],
        "notes": [
            "Catalog prices and capabilities are not benchmark evidence.",
            "Quality recommendations require evaluator output; ordinary comparisons contribute reliability, latency, and cost only.",
            "Effort is part of the ranked configuration when it is available in the saved workflow snapshot.",
        ],
    }
