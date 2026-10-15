"""Unified cases × variants → graph → evaluators workflow contract."""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from typing import Any

from .benchmarks import benchmark_case_variables, validate_benchmark
from .datasets import DatasetError, validate_dataset
from .workflow_answers import ValidationError, validate_public_validation_dataset

WORKFLOW_SCHEMA_VERSION = 2
PRESENTATION_RECIPES = (
    "graph",
    "model_comparison",
    "paper_moderation",
    "dataset_generator",
)
EVALUATOR_KINDS = ("paper_moderation_v1", "exact_match_v1", "typed_answer_v1")


class WorkflowContractError(ValueError):
    """The generic workflow execution contract is invalid."""


def _safe_identifier(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or not value[0].isalnum()
        or any(not (character.isalnum() or character in "-_") for character in value)
    ):
        raise WorkflowContractError(f"{label} must use only letters, numbers, '-' and '_'")
    return value


def _copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _legacy_recipe(workflow: dict[str, Any]) -> str:
    workflow_type = workflow.get("workflow_type")
    if workflow_type == "model_comparison":
        return "model_comparison"
    if workflow_type == "model_benchmark":
        return "paper_moderation"
    return "graph"


def _legacy_benchmark_dataset(workflow: dict[str, Any]) -> dict[str, Any] | None:
    if workflow.get("workflow_type") != "model_benchmark" or workflow.get("benchmark") is None:
        return None
    benchmark = validate_benchmark(workflow["benchmark"])
    cases = []
    for index, case in enumerate(benchmark["cases"], start=1):
        variables = benchmark_case_variables(benchmark, index)
        cases.append(
            {
                "case_id": case["case_id"],
                "name": case["name"],
                "inputs": variables,
                "expected": case["expected"],
            }
        )
    return validate_dataset(
        {
            "name": "Paper moderation corpus",
            "description": "Versioned moderation cases with a hidden answer key.",
            "cases": cases,
        }
    )


def _validate_presentation(value: Any, workflow: dict[str, Any]) -> dict[str, Any]:
    if value is None:
        return {"recipe": _legacy_recipe(workflow)}
    if not isinstance(value, dict):
        raise WorkflowContractError("workflow presentation must be an object")
    recipe = value.get("recipe", "graph")
    legacy_recipe = _legacy_recipe(workflow)
    if recipe == "graph" and legacy_recipe != "graph":
        recipe = legacy_recipe
    if recipe not in PRESENTATION_RECIPES:
        raise WorkflowContractError(f"unknown workflow presentation recipe: {recipe}")
    return {"recipe": recipe, **{str(key): _copy(item) for key, item in value.items() if key != "recipe"}}


def _validate_variants(value: Any, node_ids: set[str]) -> list[dict[str, Any]]:
    if value is None:
        return [{"variant_id": "default", "name": "Default", "node_overrides": {}}]
    if not isinstance(value, list) or not 1 <= len(value) <= 64:
        raise WorkflowContractError("workflow variants must contain between 1 and 64 variants")
    variants: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(value, start=1):
        if not isinstance(raw, dict):
            raise WorkflowContractError(f"workflow variant {index} must be an object")
        variant_id = _safe_identifier(raw.get("variant_id"), f"workflow variant {index} id")
        if variant_id in seen:
            raise WorkflowContractError(f"duplicate workflow variant id: {variant_id}")
        seen.add(variant_id)
        name = raw.get("name")
        if not isinstance(name, str) or not name.strip():
            raise WorkflowContractError(f"workflow variant {variant_id} needs a name")
        overrides = raw.get("node_overrides") or {}
        if not isinstance(overrides, dict):
            raise WorkflowContractError(f"workflow variant {variant_id} node_overrides must be an object")
        unknown = sorted(set(overrides) - node_ids)
        if unknown:
            raise WorkflowContractError(
                f"workflow variant {variant_id} overrides unknown nodes: " + ", ".join(unknown)
            )
        if any(not isinstance(item, dict) for item in overrides.values()):
            raise WorkflowContractError(f"workflow variant {variant_id} node overrides must be objects")
        variants.append(
            {
                "variant_id": variant_id,
                "name": name.strip(),
                "node_overrides": _copy(overrides),
                **({"metadata": _copy(raw["metadata"])} if isinstance(raw.get("metadata"), dict) else {}),
            }
        )
    return variants


def _validate_evaluators(
    value: Any,
    recipe: str,
    agent_node_ids: set[str],
) -> list[dict[str, Any]]:
    if value is None or (recipe == "paper_moderation" and value == []):
        value = (
            [{"evaluator_id": "paper-moderation", "kind": "paper_moderation_v1", "config": {}}]
            if recipe == "paper_moderation"
            else []
        )
    if not isinstance(value, list) or len(value) > 32:
        raise WorkflowContractError("workflow evaluators must be a list of at most 32 evaluators")
    evaluators: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(value, start=1):
        if not isinstance(raw, dict):
            raise WorkflowContractError(f"workflow evaluator {index} must be an object")
        evaluator_id = _safe_identifier(raw.get("evaluator_id"), f"workflow evaluator {index} id")
        if evaluator_id in seen:
            raise WorkflowContractError(f"duplicate workflow evaluator id: {evaluator_id}")
        seen.add(evaluator_id)
        kind = raw.get("kind")
        if kind not in EVALUATOR_KINDS:
            raise WorkflowContractError(f"unknown workflow evaluator kind: {kind}")
        config = raw.get("config") or {}
        if not isinstance(config, dict):
            raise WorkflowContractError(f"workflow evaluator {evaluator_id} config must be an object")
        if kind == "typed_answer_v1":
            unknown_config = sorted(set(config) - {"node_id", "answer_key_ref"})
            if unknown_config:
                raise WorkflowContractError(
                    f"workflow evaluator {evaluator_id} has unsupported typed-answer config: "
                    + ", ".join(unknown_config)
                )
            node_id = config.get("node_id")
            if not isinstance(node_id, str) or node_id not in agent_node_ids:
                raise WorkflowContractError(
                    f"workflow evaluator {evaluator_id} node_id must name one explicit agent node"
                )
            if "node_ids" in config:
                raise WorkflowContractError(
                    f"workflow evaluator {evaluator_id} must use one explicit node_id"
                )
            answer_key_ref = config.get("answer_key_ref")
            if not isinstance(answer_key_ref, dict):
                raise WorkflowContractError(
                    f"workflow evaluator {evaluator_id} answer_key_ref must be an object"
                )
            unknown_reference = sorted(set(answer_key_ref) - {"answer_key_id", "revision_id"})
            if unknown_reference:
                raise WorkflowContractError(
                    f"workflow evaluator {evaluator_id} answer_key_ref has unsupported fields: "
                    + ", ".join(unknown_reference)
                )
            _safe_identifier(
                answer_key_ref.get("answer_key_id"),
                f"workflow evaluator {evaluator_id} answer_key_id",
            )
            _safe_identifier(
                answer_key_ref.get("revision_id"),
                f"workflow evaluator {evaluator_id} answer key revision_id",
            )
        targets = config.get("node_ids")
        if targets is not None:
            if not isinstance(targets, list) or not targets or any(not isinstance(item, str) for item in targets):
                raise WorkflowContractError(
                    f"workflow evaluator {evaluator_id} node_ids must be a non-empty list of agent node ids"
                )
            unknown_targets = sorted(set(targets) - agent_node_ids)
            if unknown_targets:
                raise WorkflowContractError(
                    f"workflow evaluator {evaluator_id} targets unknown agent nodes: "
                    + ", ".join(unknown_targets)
                )
        evaluators.append({"evaluator_id": evaluator_id, "kind": kind, "config": _copy(config)})
    return evaluators


def normalize_workflow_contract(
    workflow: dict[str, Any],
    *,
    iterations: int | None = None,
    dataset_resolver: Callable[[str, str | None], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return one validated execution contract for current and legacy workflows."""
    presentation = _validate_presentation(workflow.get("presentation"), workflow)
    raw_dataset = workflow.get("dataset")
    dataset_ref = workflow.get("dataset_ref")
    normalized_dataset_ref = None
    if raw_dataset is None and dataset_ref is not None:
        if not isinstance(dataset_ref, dict):
            raise WorkflowContractError("workflow dataset_ref must be an object")
        dataset_id = _safe_identifier(dataset_ref.get("dataset_id"), "workflow dataset_ref dataset_id")
        revision_id = dataset_ref.get("revision_id")
        if revision_id is not None:
            revision_id = _safe_identifier(revision_id, "workflow dataset_ref revision_id")
        if dataset_resolver is None:
            raise WorkflowContractError("workflow dataset_ref cannot be resolved")
        raw_dataset = dataset_resolver(dataset_id, revision_id)
        normalized_dataset_ref = {
            "dataset_id": dataset_id,
            **({"revision_id": revision_id} if revision_id is not None else {}),
        }
    if raw_dataset is None:
        raw_dataset = _legacy_benchmark_dataset(workflow)
    if raw_dataset is not None:
        try:
            dataset = validate_dataset(raw_dataset)
        except DatasetError as error:
            raise WorkflowContractError(str(error)) from error
        sequence_mode = False
    else:
        count = int(iterations if iterations is not None else workflow.get("iterations") or 1)
        if not 1 <= count <= 1000:
            raise WorkflowContractError("iterations must be between 1 and 1000")
        dataset = {
            "kind": "case_dataset_v1",
            "schema_version": 1,
            "name": "Workflow iterations",
            "description": "Synthetic cases for a repeated graph schedule.",
            "cases": [
                {"case_id": f"iteration-{index}", "name": f"Iteration {index}", "inputs": {}}
                for index in range(1, count + 1)
            ],
        }
        sequence_mode = True
    node_ids = {str(node.get("id")) for node in workflow.get("nodes") or [] if isinstance(node, dict)}
    agent_node_ids = {
        str(node.get("id"))
        for node in workflow.get("nodes") or []
        if isinstance(node, dict) and node.get("kind") == "agent"
    }
    variants = _validate_variants(workflow.get("variants"), node_ids)
    evaluators = _validate_evaluators(workflow.get("evaluators"), presentation["recipe"], agent_node_ids)
    downstream_agent_ids = {
        str(edge.get("source"))
        for edge in workflow.get("edges") or []
        if isinstance(edge, dict) and str(edge.get("target")) in agent_node_ids
    }
    for evaluator in evaluators:
        if evaluator["kind"] == "typed_answer_v1" and evaluator["config"]["node_id"] in downstream_agent_ids:
            raise WorkflowContractError(
                f"workflow evaluator {evaluator['evaluator_id']} node_id must select a terminal agent node"
            )
    if any(evaluator["kind"] == "typed_answer_v1" for evaluator in evaluators):
        if not isinstance(normalized_dataset_ref, dict) or not normalized_dataset_ref.get("revision_id"):
            raise WorkflowContractError("typed answer evaluation requires an exact dataset revision")
        try:
            validate_public_validation_dataset(dataset)
        except ValidationError as error:
            raise WorkflowContractError(str(error)) from error
    policy = workflow.get("execution_policy") or {}
    maximum_calls = policy.get("max_calls")
    if maximum_calls is not None and (
        not isinstance(maximum_calls, int) or isinstance(maximum_calls, bool) or maximum_calls < 1
    ):
        raise WorkflowContractError("workflow max_calls must be a positive integer")
    maximum_cost = policy.get("max_total_cost_usd")
    if maximum_cost is not None and (
        not isinstance(maximum_cost, (int, float))
        or isinstance(maximum_cost, bool)
        or not math.isfinite(float(maximum_cost))
        or float(maximum_cost) <= 0
    ):
        raise WorkflowContractError("workflow max_total_cost_usd must be a positive finite number")
    agent_count = len(agent_node_ids)
    scheduled_calls = len(dataset["cases"]) * len(variants) * agent_count
    if maximum_calls is not None and scheduled_calls > maximum_calls:
        raise WorkflowContractError(
            f"workflow schedules {scheduled_calls} agent calls, above its max_calls limit of {maximum_calls}"
        )
    return {
        "schema_version": WORKFLOW_SCHEMA_VERSION,
        "presentation": presentation,
        "dataset": dataset,
        "dataset_ref": normalized_dataset_ref,
        "variants": variants,
        "evaluators": evaluators,
        "sequence_mode": sequence_mode,
        "case_count": len(dataset["cases"]),
        "variant_count": len(variants),
        "scheduled_calls": scheduled_calls,
        "max_calls": maximum_calls,
        "max_total_cost_usd": float(maximum_cost) if maximum_cost is not None else None,
    }


def case_variables(case: dict[str, Any]) -> dict[str, Any]:
    """Expose case inputs without leaking expected values to an agent prompt."""
    public = {
        "case_id": case["case_id"],
        "case_name": case["name"],
        **_copy(case.get("inputs") or {}),
    }
    public.setdefault(
        "case_json",
        json.dumps(public, ensure_ascii=False, separators=(",", ":")),
    )
    return public


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = _copy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = _copy(value)
    return result


def apply_variant(node: dict[str, Any], variant: dict[str, Any]) -> dict[str, Any]:
    """Return a node with this variant's partial config override applied."""
    override = (variant.get("node_overrides") or {}).get(str(node.get("id")))
    if not isinstance(override, dict) or node.get("kind") != "agent":
        return node
    return {**node, "config": _merge(node.get("config") or {}, override)}
