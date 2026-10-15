"""Deterministic evaluator registry for generic workflow runs."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from .benchmarks import score_benchmark, validate_benchmark
from .workflow_answers import ValidationError, score_typed_answers
from .workflow_contract import apply_variant


def _paper_moderation_benchmark(dataset: dict[str, Any]) -> dict[str, Any]:
    return validate_benchmark(
        {
            "kind": "paper_moderation_v1",
            "schema_version": 1,
            "cases": [
                {
                    "case_id": case["case_id"],
                    "name": case["name"],
                    "paper_text": str((case.get("inputs") or {}).get("paper_text") or ""),
                    "expected": case.get("expected"),
                }
                for case in dataset.get("cases") or []
            ],
        }
    )


def _variant_subjects(
    nodes: list[dict[str, Any]],
    variants: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    agents = [node for node in nodes if node.get("kind") == "agent"]
    if len(variants) == 1 and variants[0].get("variant_id") == "default":
        return agents
    subjects = []
    for variant in variants:
        for node in agents:
            effective = apply_variant(node, variant)
            subjects.append(
                {
                    **effective,
                    "id": f"{variant['variant_id']}--{node['id']}",
                    "name": variant.get("name") or variant["variant_id"],
                }
            )
    return subjects


def _subject_outputs(outputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    values = []
    for output in outputs:
        variant_id = output.get("variant_id")
        if variant_id and variant_id != "default":
            values.append({**output, "node_id": f"{variant_id}--{output.get('node_id')}"})
        else:
            values.append(output)
    return values


def _target_node_ids(
    evaluator: dict[str, Any],
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> set[str]:
    config = evaluator.get("config") or {}
    explicit = config.get("node_id")
    if explicit:
        return {str(explicit)}
    configured = config.get("node_ids")
    if configured:
        return {str(value) for value in configured}
    agents = {str(node.get("id")) for node in nodes if node.get("kind") == "agent"}
    outputs = {str(node.get("id")) for node in nodes if node.get("kind") == "output"}
    terminal_sources = {
        str(edge.get("source"))
        for edge in edges
        if str(edge.get("target")) in outputs and str(edge.get("source")) in agents
    }
    return terminal_sources or agents


def _exact_match(
    dataset: dict[str, Any],
    outputs: list[dict[str, Any]],
) -> dict[str, Any]:
    cases = {case["case_id"]: case for case in dataset.get("cases") or []}
    results = []
    correct = 0
    scored = 0
    for output in outputs:
        case = cases.get(output.get("case_id"))
        expected = (case or {}).get("expected")
        if expected is None:
            continue
        scored += 1
        if isinstance(expected, str):
            actual: Any = str(output.get("text") or "").strip()
            matches = actual == expected.strip()
        else:
            actual = output.get("structured_output")
            if actual is None:
                try:
                    actual = json.loads(str(output.get("text") or ""))
                except (TypeError, ValueError):
                    actual = None
            matches = actual == expected
        correct += int(matches)
        results.append(
            {
                "case_id": output.get("case_id"),
                "variant_id": output.get("variant_id"),
                "node_id": output.get("node_id"),
                "matches": matches,
            }
        )
    return {
        "kind": "exact_match_v1",
        "scored_outputs": scored,
        "accuracy": round(correct / scored, 6) if scored else None,
        "results": results,
    }


def evaluate_workflow(
    evaluators: list[dict[str, Any]],
    *,
    dataset: dict[str, Any],
    variants: list[dict[str, Any]],
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    outputs: list[dict[str, Any]],
    dataset_ref: dict[str, Any] | None = None,
    answer_key_resolver: Callable[[str, str | None], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run each registered evaluator and return results by stable evaluator id."""
    results: dict[str, Any] = {}
    for evaluator in evaluators:
        kind = evaluator["kind"]
        target_ids = _target_node_ids(evaluator, nodes, edges)
        target_nodes = [node for node in nodes if str(node.get("id")) in target_ids]
        target_outputs = [output for output in outputs if str(output.get("node_id")) in target_ids]
        if kind == "paper_moderation_v1":
            results[evaluator["evaluator_id"]] = score_benchmark(
                _paper_moderation_benchmark(dataset),
                _subject_outputs(target_outputs),
                _variant_subjects(target_nodes, variants),
            )
        elif kind == "exact_match_v1":
            results[evaluator["evaluator_id"]] = _exact_match(dataset, target_outputs)
        elif kind == "typed_answer_v1":
            if answer_key_resolver is None:
                raise ValidationError("typed answer evaluator cannot resolve its private answer key")
            config = evaluator["config"]
            answer_key_ref = config["answer_key_ref"]
            answer_key = answer_key_resolver(
                answer_key_ref["answer_key_id"],
                answer_key_ref["revision_id"],
            )
            if answer_key.get("dataset_ref") != dataset_ref:
                raise ValidationError("typed answer key is bound to a different dataset revision")
            results[evaluator["evaluator_id"]] = score_typed_answers(
                dataset=dataset,
                variants=variants,
                node_id=config["node_id"],
                outputs=target_outputs,
                answer_key=answer_key,
            )
    return results
