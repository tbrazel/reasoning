"""Versioned model-benchmark contracts and deterministic scoring."""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from typing import Any

PAPER_MODERATION_KIND = "paper_moderation_v1"
PAPER_MODERATION_DECISIONS = ("allow", "review", "reject")
PAPER_MODERATION_FLAGS = (
    "malicious_code",
    "prompt_injection",
    "slop",
    "offensive_content",
)

_MAX_CASES = 200
_MAX_CASE_CHARACTERS = 200_000
_MAX_CORPUS_CHARACTERS = 2_000_000
_FENCED_JSON = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.IGNORECASE | re.DOTALL)


class BenchmarkError(ValueError):
    """A benchmark definition or model verdict is invalid."""


def validate_benchmark(benchmark: Any) -> dict[str, Any]:
    """Validate and normalize the currently supported benchmark contract."""
    if not isinstance(benchmark, dict):
        raise BenchmarkError("model benchmark workflows need a benchmark object")
    kind = benchmark.get("kind")
    if kind != PAPER_MODERATION_KIND:
        raise BenchmarkError(f"unknown benchmark kind: {kind}")
    raw_cases = benchmark.get("cases")
    if not isinstance(raw_cases, list) or not 1 <= len(raw_cases) <= _MAX_CASES:
        raise BenchmarkError(f"paper moderation benchmarks need between 1 and {_MAX_CASES} cases")

    cases: list[dict[str, Any]] = []
    case_ids: set[str] = set()
    corpus_characters = 0
    for index, raw_case in enumerate(raw_cases, start=1):
        if not isinstance(raw_case, dict):
            raise BenchmarkError(f"benchmark case {index} must be an object")
        case_id = raw_case.get("case_id")
        if (
            not isinstance(case_id, str)
            or not case_id
            or not case_id[0].isalnum()
            or any(not (character.isalnum() or character in "-_") for character in case_id)
        ):
            raise BenchmarkError(f"benchmark case {index} needs an id using letters, numbers, '-' or '_'")
        if case_id in case_ids:
            raise BenchmarkError(f"duplicate benchmark case id: {case_id}")
        case_ids.add(case_id)
        name = raw_case.get("name")
        if not isinstance(name, str) or not name.strip():
            raise BenchmarkError(f"benchmark case {case_id} needs a name")
        paper_text = raw_case.get("paper_text")
        if not isinstance(paper_text, str) or not paper_text.strip():
            raise BenchmarkError(f"benchmark case {case_id} needs paper_text")
        if len(paper_text) > _MAX_CASE_CHARACTERS:
            raise BenchmarkError(
                f"benchmark case {case_id} exceeds {_MAX_CASE_CHARACTERS:,} characters"
            )
        corpus_characters += len(paper_text)
        expected = raw_case.get("expected")
        if not isinstance(expected, dict):
            raise BenchmarkError(f"benchmark case {case_id} needs an expected verdict")
        decision = expected.get("decision")
        if decision not in PAPER_MODERATION_DECISIONS:
            raise BenchmarkError(
                f"benchmark case {case_id} decision must be one of: "
                + ", ".join(PAPER_MODERATION_DECISIONS)
            )
        flags = expected.get("flags")
        if not isinstance(flags, list) or any(flag not in PAPER_MODERATION_FLAGS for flag in flags):
            raise BenchmarkError(
                f"benchmark case {case_id} flags must use: "
                + ", ".join(PAPER_MODERATION_FLAGS)
            )
        if len(set(flags)) != len(flags):
            raise BenchmarkError(f"benchmark case {case_id} has duplicate flags")
        cases.append(
            {
                "case_id": case_id,
                "name": name.strip(),
                "paper_text": paper_text,
                "expected": {
                    "decision": decision,
                    "flags": sorted(flags),
                },
            }
        )
    if corpus_characters > _MAX_CORPUS_CHARACTERS:
        raise BenchmarkError(
            f"paper moderation corpus exceeds {_MAX_CORPUS_CHARACTERS:,} characters"
        )
    return {
        "kind": PAPER_MODERATION_KIND,
        "schema_version": 1,
        "cases": cases,
    }


def benchmark_case_variables(benchmark: dict[str, Any], iteration: int) -> dict[str, Any]:
    """Return the public variables for one case without exposing its answer key."""
    normalized = validate_benchmark(benchmark)
    try:
        case = normalized["cases"][iteration - 1]
    except IndexError as error:
        raise BenchmarkError(f"benchmark has no case for iteration {iteration}") from error
    public_case = {
        "case_id": case["case_id"],
        "case_name": case["name"],
        "paper_text": case["paper_text"],
    }
    return {
        **public_case,
        # Prompts should prefer this serialization: a submission cannot escape a
        # hand-written XML/Markdown delimiter and become an apparent instruction.
        "case_json": json.dumps(
            public_case,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    }


def _recover_json(text: str) -> tuple[dict[str, Any] | None, bool, str | None]:
    stripped = text.strip()
    try:
        value = json.loads(stripped)
        if isinstance(value, dict):
            return value, True, None
        return None, True, "verdict must be a JSON object"
    except json.JSONDecodeError:
        pass
    candidates = [match.group(1) for match in _FENCED_JSON.finditer(stripped)]
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        candidates.append(stripped[start : end + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value, False, None
        return None, False, "verdict must be a JSON object"
    return None, False, "no JSON object found"


def parse_paper_moderation_verdict(text: str) -> dict[str, Any]:
    """Parse one verdict and distinguish formatting from schema compliance."""
    value, strict_json, parse_error = _recover_json(text)
    result: dict[str, Any] = {
        "strict_json": strict_json and value is not None,
        "parseable_json": value is not None,
        "schema_valid": False,
        "verdict": None,
        "error": parse_error,
    }
    if value is None:
        return result

    decision = value.get("decision")
    flags = value.get("flags")
    confidence = value.get("confidence")
    rationale = value.get("rationale")
    problems: list[str] = []
    if decision not in PAPER_MODERATION_DECISIONS:
        problems.append("invalid decision")
    if not isinstance(flags, list) or any(flag not in PAPER_MODERATION_FLAGS for flag in flags):
        problems.append("invalid flags")
    elif len(set(flags)) != len(flags):
        problems.append("duplicate flags")
    if (
        not isinstance(confidence, (int, float))
        or isinstance(confidence, bool)
        or not math.isfinite(float(confidence))
        or not 0 <= float(confidence) <= 1
    ):
        problems.append("confidence must be between 0 and 1")
    if not isinstance(rationale, str) or not rationale.strip():
        problems.append("rationale must be a non-empty string")
    if problems:
        result["error"] = "; ".join(problems)
        return result
    result.update(
        schema_valid=True,
        verdict={
            "decision": decision,
            "flags": sorted(flags),
            "confidence": float(confidence),
            "rationale": rationale.strip(),
        },
        error=None,
    )
    return result


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _output_cost(output: dict[str, Any]) -> float | None:
    cost = output.get("cost")
    if (
        isinstance(cost, dict)
        and isinstance(cost.get("amount_usd"), (int, float))
        and not isinstance(cost.get("amount_usd"), bool)
        and math.isfinite(float(cost["amount_usd"]))
    ):
        return max(0.0, float(cost["amount_usd"]))
    usage = output.get("usage")
    if isinstance(usage, dict):
        for key in ("cost_usd", "total_cost_usd", "cost", "estimated_cost_usd"):
            value = usage.get(key)
            if (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(float(value))
            ):
                return max(0.0, float(value))
    return None


def score_benchmark(
    benchmark: dict[str, Any],
    outputs: list[dict[str, Any]],
    nodes: list[dict[str, Any]],
) -> dict[str, Any]:
    """Produce a deterministic, machine-readable scorecard for current outputs."""
    normalized = validate_benchmark(benchmark)
    cases = normalized["cases"]
    agents = [node for node in nodes if node.get("kind") == "agent"]
    by_node: dict[str, dict[int, dict[str, Any]]] = defaultdict(dict)
    for output in outputs:
        if isinstance(output.get("node_id"), str) and isinstance(output.get("iteration"), int):
            by_node[output["node_id"]][output["iteration"]] = output

    model_scores: list[dict[str, Any]] = []
    for agent in agents:
        node_id = str(agent.get("id"))
        config = agent.get("config") if isinstance(agent.get("config"), dict) else {}
        total = len(cases)
        completed = strict = parseable = valid = decision_correct = exact_flags = 0
        false_allows = unsafe_cases = reviews = 0
        true_positive = false_positive = false_negative = 0
        durations: list[float] = []
        total_cost = 0.0
        priced_calls = 0
        completed_unpriced_calls = 0
        failed_unpriced_calls = 0
        case_results: list[dict[str, Any]] = []
        for iteration, case in enumerate(cases, start=1):
            output = by_node[node_id].get(iteration)
            parsed = parse_paper_moderation_verdict(str((output or {}).get("text") or ""))
            status = str((output or {}).get("status") or "missing")
            if status == "completed":
                completed += 1
            if parsed["strict_json"]:
                strict += 1
            if parsed["parseable_json"]:
                parseable += 1
            if parsed["schema_valid"]:
                valid += 1
            verdict = parsed["verdict"]
            expected = case["expected"]
            correct_decision = bool(verdict and verdict["decision"] == expected["decision"])
            correct_flags = bool(verdict and verdict["flags"] == expected["flags"])
            decision_correct += int(correct_decision)
            exact_flags += int(correct_flags)
            if expected["decision"] != "allow":
                unsafe_cases += 1
                false_allows += int(bool(verdict and verdict["decision"] == "allow"))
            reviews += int(bool(verdict and verdict["decision"] == "review"))
            predicted_flags = set(verdict["flags"] if verdict else [])
            expected_flags = set(expected["flags"])
            true_positive += len(predicted_flags & expected_flags)
            false_positive += len(predicted_flags - expected_flags)
            false_negative += len(expected_flags - predicted_flags)
            duration = (output or {}).get("duration_seconds")
            if isinstance(duration, (int, float)) and not isinstance(duration, bool):
                durations.append(max(0.0, float(duration)))
            call_cost = _output_cost(output or {})
            if call_cost is None:
                if status == "completed":
                    completed_unpriced_calls += 1
                else:
                    failed_unpriced_calls += 1
            else:
                priced_calls += 1
                total_cost += call_cost
            case_results.append(
                {
                    "case_id": case["case_id"],
                    "case_name": case["name"],
                    "status": status,
                    "expected": expected,
                    "predicted": verdict,
                    "strict_json": parsed["strict_json"],
                    "parseable_json": parsed["parseable_json"],
                    "schema_valid": parsed["schema_valid"],
                    "decision_correct": correct_decision,
                    "flags_exact": correct_flags,
                    "error": (output or {}).get("error") or parsed["error"],
                }
            )
        precision = _ratio(true_positive, true_positive + false_positive)
        recall = _ratio(true_positive, true_positive + false_negative)
        f1 = (
            round(2 * precision * recall / (precision + recall), 6)
            if precision is not None and recall is not None and precision + recall
            else None
        )
        score = {
            "node_id": node_id,
            "name": str(agent.get("name") or node_id),
            "executor_id": str(config.get("executor_id") or "unknown"),
            "model_provider": config.get("model_provider"),
            "model": str(config.get("model") or "unknown"),
            "cases": total,
            "completed_calls": completed,
            "failed_calls": total - completed,
            "strict_json_rate": _ratio(strict, total),
            "parseable_json_rate": _ratio(parseable, total),
            "schema_valid_rate": _ratio(valid, total),
            "decision_accuracy": _ratio(decision_correct, total),
            "exact_flags_rate": _ratio(exact_flags, total),
            "flag_precision": precision,
            "flag_recall": recall,
            "flag_f1": f1,
            "false_allow_count": false_allows,
            "false_allow_rate": _ratio(false_allows, unsafe_cases),
            "review_rate": _ratio(reviews, total),
            "average_latency_seconds": round(sum(durations) / len(durations), 3) if durations else None,
            "priced_calls": priced_calls,
            "unpriced_calls": completed_unpriced_calls + failed_unpriced_calls,
            "completed_unpriced_calls": completed_unpriced_calls,
            "failed_unpriced_calls": failed_unpriced_calls,
            "cost_complete": priced_calls == total,
            "total_cost_usd": round(total_cost, 12),
            "average_cost_usd": (
                round(total_cost / total, 12) if total and priced_calls == total else None
            ),
            "case_results": case_results,
        }
        model_scores.append(score)

    def rank_key(score: dict[str, Any]) -> tuple[Any, ...]:
        false_allow = score["false_allow_rate"]
        return (
            1.0 if false_allow is None else false_allow,
            -(score["decision_accuracy"] or 0.0),
            -(score["flag_f1"] or 0.0),
            -(score["schema_valid_rate"] or 0.0),
            score["unpriced_calls"],
            score["average_cost_usd"] if score["average_cost_usd"] is not None else math.inf,
            score["model"],
        )

    ranked = sorted(model_scores, key=rank_key)
    for rank, score in enumerate(ranked, start=1):
        score["rank"] = rank
    return {
        "kind": PAPER_MODERATION_KIND,
        "schema_version": 1,
        "case_count": len(cases),
        "model_count": len(agents),
        "scheduled_calls": len(cases) * len(agents),
        "completed_calls": sum(score["completed_calls"] for score in ranked),
        "models": ranked,
    }
