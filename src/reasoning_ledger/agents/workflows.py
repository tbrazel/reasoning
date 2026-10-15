"""Immutable workflow graphs and their checkpointed execution runtime."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..canonical import canonical_json
from .benchmarks import BenchmarkError, validate_benchmark
from .contracts import (
    AgentEvent,
    AgentEventKind,
    AgentRunResult,
    AgentRunSpec,
    CompletionArtifactRequirement,
    ContextPolicy,
    EventSink,
    PreparedAgentRun,
    PreparedTool,
    PromptBinding,
    RunBudget,
    SandboxPolicy,
    SessionPolicy,
    SkillBinding,
    ToolBinding,
)
from .datasets import DatasetStore
from .evaluators import evaluate_workflow
from .harness_event_compat import canonical_harness_event
from .mcp_connections import McpConnectionError, McpConnectionStore
from .openrouter_errors import sanitize_provider_error_message
from .project_tools import project_mcp_config
from .prompts import PromptRegistry
from .run_store import RunStore
from .skills import SkillRegistry
from .tools import ToolGrant, ToolRegistry, ToolRegistryError
from .workflow_answers import AnswerKeyStore, ValidationError
from .workflow_contract import (
    WorkflowContractError,
    apply_variant,
    case_variables,
    normalize_workflow_contract,
)

WORKFLOW_NODE_KINDS = ("input", "agent", "output")
CONTEXT_MODES = ("none", "predecessors", "all", "selected", "rolling")
WORKFLOW_TYPES = ("graph", "model_comparison", "model_benchmark")
WORKFLOW_FAILURE_POLICIES = ("fail_fast", "continue")
_WORKFLOW_SLOT = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_.-]*)\s*}}")


class WorkflowError(ValueError):
    """A workflow graph, revision, or execution request is invalid."""


def _legacy_recipe_for_type(workflow: dict[str, Any]) -> str:
    if workflow.get("workflow_type") == "model_comparison":
        return "model_comparison"
    if workflow.get("workflow_type") == "model_benchmark":
        return "paper_moderation"
    return "graph"


def _safe_identifier(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or not value[0].isalnum()
        or any(not (character.isalnum() or character in "-_") for character in value)
    ):
        raise WorkflowError(f"{label} must use only letters, numbers, '-' and '_'")
    return value


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


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(value, encoding="utf-8")
    os.replace(temporary, path)


def _call_cost(output: dict[str, Any]) -> dict[str, Any] | None:
    usage = output.get("usage")
    if not isinstance(usage, dict):
        return None
    for key in ("cost_usd", "total_cost_usd", "cost", "estimated_cost_usd"):
        value = usage.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)) and float(value) >= 0:
            return {
                "amount_usd": float(value),
                "basis": "estimated" if key == "estimated_cost_usd" else "provider_reported",
                "source_field": key,
            }
    return None


def _usage_number(usage: Any, *keys: str) -> int | None:
    if not isinstance(usage, dict):
        return None
    for key in keys:
        value = usage.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return max(0, int(value))
    return None


def _completion_details(result: AgentRunResult | None) -> dict[str, Any]:
    if result is None:
        return {}
    details: dict[str, Any] = {}
    completions = result.metadata.get("completions")
    if isinstance(completions, list):
        for candidate in reversed(completions):
            if isinstance(candidate, dict):
                details.update(candidate)
                break
    for key in (
        "response_id",
        "model",
        "provider",
        "finish_reason",
        "native_finish_reason",
    ):
        if key not in details and result.metadata.get(key) is not None:
            details[key] = result.metadata[key]
    return details


def _workflow_cost(outputs: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[tuple[str, str | None, str], dict[str, Any]] = {}
    provider_reported = 0.0
    estimated = 0.0
    priced_calls = 0
    estimated_calls = 0
    unpriced_calls = 0
    completed_unpriced_calls = 0
    failed_unpriced_calls = 0
    for output in outputs:
        executor_id = str(output.get("executor_id") or "unknown")
        model_provider = output.get("model_provider")
        if not isinstance(model_provider, str) or not model_provider:
            model_provider = None
        model = str(output.get("model") or "unknown")
        group = groups.setdefault(
            (executor_id, model_provider, model),
            {
                "executor_id": executor_id,
                **({"model_provider": model_provider} if model_provider else {}),
                "model": model,
                "calls": 0,
                "priced_calls": 0,
                "estimated_calls": 0,
                "unpriced_calls": 0,
                "completed_unpriced_calls": 0,
                "failed_unpriced_calls": 0,
                "total_usd": 0.0,
            },
        )
        group["calls"] += 1
        cost = _call_cost(output)
        if cost is None:
            unpriced_calls += 1
            group["unpriced_calls"] += 1
            if output.get("status") == "completed":
                completed_unpriced_calls += 1
                group["completed_unpriced_calls"] += 1
            else:
                failed_unpriced_calls += 1
                group["failed_unpriced_calls"] += 1
            continue
        amount = cost["amount_usd"]
        output["cost"] = cost
        priced_calls += 1
        group["priced_calls"] += 1
        group["total_usd"] += amount
        if cost["basis"] == "estimated":
            estimated += amount
            estimated_calls += 1
            group["estimated_calls"] += 1
        else:
            provider_reported += amount
    return {
        "currency": "USD",
        "total_usd": provider_reported + estimated,
        "provider_reported_usd": provider_reported,
        "estimated_usd": estimated,
        "priced_calls": priced_calls,
        "estimated_calls": estimated_calls,
        "unpriced_calls": unpriced_calls,
        "completed_unpriced_calls": completed_unpriced_calls,
        "failed_unpriced_calls": failed_unpriced_calls,
        "by_executor": list(groups.values()),
    }


def _workflow_report(state: dict[str, Any]) -> str:
    workflow = state.get("workflow_snapshot") or {}
    workflow_name = str(workflow.get("name") or state.get("workflow_id") or "Workflow")
    cost = state.get("cost") or {}
    variables = state.get("variables") or {}
    outputs = (state.get("outputs") or {}).get("nodes") or []
    resolved_contract = workflow.get("resolved_contract") or {}
    recipe = (resolved_contract.get("presentation") or workflow.get("presentation") or {}).get("recipe")
    workflow_type = workflow.get("workflow_type")
    comparison = recipe == "model_comparison" or workflow_type == "model_comparison"
    benchmark = recipe == "paper_moderation" or workflow_type == "model_benchmark"
    completed_unpriced = int(cost.get("completed_unpriced_calls") or 0)
    failed_unpriced = int(cost.get("failed_unpriced_calls") or 0)
    dataset = resolved_contract.get("dataset") or {}
    dataset_ref = resolved_contract.get("dataset_ref") or {}
    variants = resolved_contract.get("variants") or []
    evaluators = resolved_contract.get("evaluators") or []
    lines = [
        f"# {workflow_name} · saved run",
        "",
        f"- Run: `{state.get('run_id')}`",
        f"- Workflow revision: `{state.get('workflow_revision_id')}`",
        f"- Status: **{state.get('status')}**",
        f"- Iterations: {state.get('iterations')}",
        f"- Started: {state.get('created_at')}",
        f"- Updated: {state.get('updated_at')}",
        "",
        "## Execution plan",
        "",
        f"- Recipe: `{recipe or 'graph'}`",
        f"- Dataset: {dataset.get('name') or 'Workflow iterations'}",
        (
            f"- Dataset revision: `{dataset_ref.get('dataset_id')}@{dataset_ref.get('revision_id')}`"
            if dataset_ref
            else "- Dataset revision: embedded in the workflow revision"
        ),
        f"- Cases: {int(resolved_contract.get('case_count') or state.get('iterations') or 0)}",
        f"- Variants: {int(resolved_contract.get('variant_count') or 1)}",
        f"- Scheduled agent calls: {int(resolved_contract.get('scheduled_calls') or 0)}",
        f"- Call limit: {resolved_contract.get('max_calls') or 'not set'}",
        (
            f"- Run cost limit: ${float(resolved_contract['max_total_cost_usd']):.6f} USD"
            if resolved_contract.get("max_total_cost_usd") is not None
            else "- Run cost limit: not set"
        ),
        "- Variant IDs: " + (", ".join(f"`{item.get('variant_id')}`" for item in variants) or "`default`"),
        "- Evaluators: " + (", ".join(f"`{item.get('evaluator_id')}`" for item in evaluators) or "none"),
        "",
        "## Cost",
        "",
        f"- Total recorded: **${float(cost.get('total_usd') or 0):.6f} USD**",
        f"- Provider-reported: ${float(cost.get('provider_reported_usd') or 0):.6f}",
        f"- Estimated: ${float(cost.get('estimated_usd') or 0):.6f}",
    ]
    if completed_unpriced:
        lines.append(f"- Completed calls with no reported price: {completed_unpriced}")
    if failed_unpriced:
        lines.append(f"- Calls that failed before a price was reported: {failed_unpriced}")
    if not completed_unpriced and not failed_unpriced:
        lines.append("- Calls with no reported price: 0")
    lines.append("")
    if benchmark:
        scorecard = (state.get("outputs") or {}).get("benchmark") or {}
        lines.extend(
            [
                "## Benchmark scorecard",
                "",
                (
                    f"{int(scorecard.get('completed_calls') or 0)} / "
                    f"{int(scorecard.get('scheduled_calls') or 0)} calls completed across "
                    f"{int(scorecard.get('case_count') or 0)} cases."
                ),
                "",
                "| rank | model | decision | flag F1 | false allow | schema | latency | cost |",
                "| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for model_score in scorecard.get("models") or []:
            if not isinstance(model_score, dict):
                continue

            def percent(value: Any) -> str:
                return "—" if value is None else f"{float(value) * 100:.1f}%"

            latency = model_score.get("average_latency_seconds")
            unpriced = int(model_score.get("unpriced_calls") or 0)
            reported_cost = float(model_score.get("total_cost_usd") or 0)
            cost_label = (
                f"${reported_cost:.6f}"
                if not unpriced
                else (
                    f"${reported_cost:.6f} + {unpriced} not reported"
                    if int(model_score.get("priced_calls") or 0)
                    else "not reported"
                )
            )
            lines.append(
                "| "
                + " | ".join(
                    (
                        str(model_score.get("rank") or "—"),
                        f"`{model_score.get('model') or 'unknown'}`",
                        percent(model_score.get("decision_accuracy")),
                        percent(model_score.get("flag_f1")),
                        percent(model_score.get("false_allow_rate")),
                        percent(model_score.get("schema_valid_rate")),
                        "—" if latency is None else f"{float(latency):.2f}s",
                        cost_label,
                    )
                )
                + " |"
            )
        lines.extend(["", "## Case-level results", ""])
        for model_score in scorecard.get("models") or []:
            if not isinstance(model_score, dict):
                continue
            lines.extend([f"### {model_score.get('name') or model_score.get('model')}", ""])
            for case in model_score.get("case_results") or []:
                if not isinstance(case, dict):
                    continue
                predicted = case.get("predicted") or {}
                expected = case.get("expected") or {}
                marker = "✓" if case.get("decision_correct") and case.get("flags_exact") else "×"
                lines.append(
                    f"- {marker} **{case.get('case_name') or case.get('case_id')}** — "
                    f"expected `{expected.get('decision')}` {expected.get('flags') or []}; "
                    f"received `{predicted.get('decision') or case.get('status')}` "
                    f"{predicted.get('flags') or []}"
                )
                if case.get("error"):
                    lines.append(f"  - {case['error']}")
            lines.append("")
        lines.extend(["## Raw model outputs", ""])
    elif comparison:
        question = next(
            (
                str(variables.get(key)).strip()
                for key in ("question", "prompt", "input")
                if variables.get(key) is not None and str(variables.get(key)).strip()
            ),
            "",
        )
        lines.extend(["## Question", "", question or "_No question was recorded._", ""])
        lines.extend(["## Model results", ""])
    else:
        lines.extend(
            [
                "## Initial values",
                "",
                "```json",
                json.dumps(variables, ensure_ascii=False, indent=2, sort_keys=True),
                "```",
                "",
                "## Agent outputs",
                "",
            ]
        )
    if not outputs:
        lines.extend(["_Waiting for the first agent output._", ""])
    node_names = {
        str(node.get("id")): str(node.get("name") or node.get("id")) for node in workflow.get("nodes") or [] if isinstance(node, dict)
    }
    for output in outputs:
        cost_info = output.get("cost") or _call_cost(output)
        usage = output.get("usage")
        total_tokens = _usage_number(usage, "total_tokens", "totalTokens", "tokens")
        if total_tokens is None:
            prompt_tokens = _usage_number(usage, "prompt_tokens", "input_tokens")
            completion_tokens = _usage_number(
                usage,
                "completion_tokens",
                "output_tokens",
            )
            if prompt_tokens is not None and completion_tokens is not None:
                total_tokens = prompt_tokens + completion_tokens
        reasoning_tokens = _usage_number(
            usage,
            "reasoning_tokens",
            "reasoningTokens",
        )
        if reasoning_tokens is None and isinstance(usage, dict):
            details = usage.get("completion_tokens_details") or usage.get("output_tokens_details")
            reasoning_tokens = _usage_number(details, "reasoning_tokens")
        heading = (
            output.get("variant_name")
            if output.get("variant_id") not in {None, "default"}
            else node_names.get(str(output.get("node_id")), output.get("node_id"))
        )
        lines.extend(
            [
                (
                    f"### Case {output.get('iteration')} · {heading}"
                    if benchmark
                    else f"### {heading}"
                    if comparison
                    else (
                        f"### {output.get('case_name') or ('Case ' + str(output.get('iteration')))} · {heading}"
                        if not resolved_contract.get("sequence_mode", True)
                        else f"### Week {output.get('iteration')} · {heading}"
                    )
                ),
                "",
                f"- Status: {output.get('status')}",
                f"- Executor: `{output.get('executor_id') or 'unknown'}`",
                f"- Model route: `{output.get('model_provider') or 'executor default'}`",
                f"- Model: `{output.get('model') or 'unknown'}`",
                f"- Resolved model: `{output.get('resolved_model') or 'not reported'}`",
                f"- Serving provider: `{output.get('serving_provider') or 'not reported'}`",
                f"- Prompt fidelity: `{output.get('prompt_fidelity') or 'not reported'}`",
                f"- Finish reason: `{output.get('finish_reason') or 'not reported'}`",
                f"- Case: `{output.get('case_id') or 'not reported'}`",
                f"- Variant: `{output.get('variant_id') or 'default'}`",
                f"- Work item: `{output.get('work_item_id') or 'not reported'}`",
                f"- Duration: {float(output.get('duration_seconds') or 0):.3f} seconds",
                f"- Tokens: {total_tokens if total_tokens is not None else 'not reported'}",
                (f"- Reasoning tokens: {reasoning_tokens}" if reasoning_tokens is not None else "- Reasoning tokens: not reported"),
                (
                    f"- Price: ${float(cost_info['amount_usd']):.6f} ({str(cost_info['basis']).replace('_', ' ')})"
                    if cost_info
                    else (
                        "- Price: call failed before usage was reported"
                        if output.get("status") != "completed"
                        else "- Price: not reported by executor"
                    )
                ),
                "",
            ]
        )
        if output.get("error"):
            lines.extend([f"**Error:** {output['error']}", ""])
        if output.get("text"):
            lines.extend([str(output["text"]), ""])
        if output.get("structured_output") is not None:
            lines.extend(
                [
                    "```json",
                    json.dumps(output["structured_output"], ensure_ascii=False, indent=2),
                    "```",
                    "",
                ]
            )
    evaluations = (state.get("outputs") or {}).get("evaluations") or {}
    if evaluations and not benchmark:
        lines.extend(["## Evaluation results", ""])
        for evaluator_id, result in evaluations.items():
            lines.extend(
                [
                    f"### {evaluator_id}",
                    "",
                    "```json",
                    json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True),
                    "```",
                    "",
                ]
            )
    return "\n".join(lines).rstrip() + "\n"


def _validate_graph(
    graph: dict[str, Any],
    *,
    dataset_resolver=None,
) -> None:
    name = graph.get("name")
    nodes = graph.get("nodes")
    edges = graph.get("edges")
    if not isinstance(name, str) or not name.strip():
        raise WorkflowError("workflow needs a name")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise WorkflowError("workflow nodes and edges must be lists")
    workflow_type = graph.get("workflow_type", "graph")
    if workflow_type not in WORKFLOW_TYPES:
        raise WorkflowError(f"unknown workflow type: {workflow_type}")
    if workflow_type == "model_benchmark":
        try:
            validate_benchmark(graph.get("benchmark"))
        except BenchmarkError as error:
            raise WorkflowError(str(error)) from error
    try:
        normalize_workflow_contract(graph, dataset_resolver=dataset_resolver)
    except WorkflowContractError as error:
        raise WorkflowError(str(error)) from error
    execution_policy = graph.get("execution_policy") or {}
    if not isinstance(execution_policy, dict):
        raise WorkflowError("workflow execution_policy must be an object")
    max_concurrency = execution_policy.get("max_concurrency", 1)
    if not isinstance(max_concurrency, int) or isinstance(max_concurrency, bool) or not 1 <= max_concurrency <= 8:
        raise WorkflowError("workflow max_concurrency must be between 1 and 8")
    failure_policy = execution_policy.get("failure_policy", "fail_fast")
    if failure_policy not in WORKFLOW_FAILURE_POLICIES:
        raise WorkflowError(f"unknown workflow failure policy: {failure_policy}")
    ids: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict):
            raise WorkflowError("each workflow node must be an object")
        node_id = node.get("id")
        if not isinstance(node_id, str) or not node_id:
            raise WorkflowError("each workflow node needs an id")
        if node_id in ids:
            raise WorkflowError(f"duplicate workflow node: {node_id}")
        ids.add(node_id)
        if node.get("kind") not in WORKFLOW_NODE_KINDS:
            raise WorkflowError(f"unknown workflow node kind: {node.get('kind')}")
        position = node.get("position")
        if not isinstance(position, dict) or not all(isinstance(position.get(axis), (int, float)) for axis in ("x", "y")):
            raise WorkflowError(f"node {node_id} needs a numeric position")
        if node.get("kind") == "agent" and not isinstance(node.get("config"), dict):
            raise WorkflowError(f"agent node {node_id} needs a config object")
        completion = (node.get("config") or {}).get("completion")
        if completion is not None:
            if not isinstance(completion, dict):
                raise WorkflowError(f"agent node {node_id} completion must be an object")
            for field in ("required_markers", "required_artifacts"):
                values = completion.get(field, [])
                if not isinstance(values, list) or any(not isinstance(value, str) or not value.strip() for value in values):
                    raise WorkflowError(f"agent node {node_id} completion {field} must be a list of non-empty strings")
            required_tool_calls = completion.get("required_tool_calls", {})
            if not isinstance(required_tool_calls, dict) or any(
                not isinstance(tool_id, str)
                or not tool_id.strip()
                or not isinstance(minimum, int)
                or isinstance(minimum, bool)
                or minimum < 1
                for tool_id, minimum in required_tool_calls.items()
            ):
                raise WorkflowError(f"agent node {node_id} completion required_tool_calls must map non-empty tool ids to positive integers")
            missing_tools = sorted(set(required_tool_calls) - set((node.get("config") or {}).get("tools") or []))
            if missing_tools:
                raise WorkflowError(f"agent node {node_id} completion requires tools not granted to the node: " + ", ".join(missing_tools))
            if not completion.get("required_markers") and not completion.get("required_artifacts") and not required_tool_calls:
                raise WorkflowError(f"agent node {node_id} completion needs a marker, artifact, or successful tool call")
    edge_ids: set[str] = set()
    for edge in edges:
        if not isinstance(edge, dict):
            raise WorkflowError("each workflow edge must be an object")
        edge_id = edge.get("id")
        if not isinstance(edge_id, str) or not edge_id or edge_id in edge_ids:
            raise WorkflowError("workflow edge ids must be non-empty and unique")
        edge_ids.add(edge_id)
        if edge.get("source") not in ids or edge.get("target") not in ids:
            raise WorkflowError(f"edge {edge_id} names an unknown node")
        if edge["source"] == edge["target"]:
            raise WorkflowError(f"edge {edge_id} cannot connect a node to itself")
    _topological_order(graph)


def _topological_order(graph: dict[str, Any]) -> list[str]:
    nodes = [node["id"] for node in graph.get("nodes", [])]
    incoming = {node_id: 0 for node_id in nodes}
    outgoing: dict[str, list[str]] = defaultdict(list)
    for edge in graph.get("edges", []):
        outgoing[edge["source"]].append(edge["target"])
        incoming[edge["target"]] += 1
    ready = deque(node_id for node_id in nodes if incoming[node_id] == 0)
    ordered: list[str] = []
    while ready:
        node_id = ready.popleft()
        ordered.append(node_id)
        for target in outgoing[node_id]:
            incoming[target] -= 1
            if incoming[target] == 0:
                ready.append(target)
    if len(ordered) != len(nodes):
        raise WorkflowError("workflow graph contains a cycle; use iterations for repeated schedules")
    return ordered


def _topological_batches(graph: dict[str, Any]) -> list[list[str]]:
    """Return dependency layers while preserving the graph's node order."""
    nodes = [node["id"] for node in graph.get("nodes", [])]
    incoming = {node_id: 0 for node_id in nodes}
    outgoing: dict[str, list[str]] = defaultdict(list)
    for edge in graph.get("edges", []):
        outgoing[edge["source"]].append(edge["target"])
        incoming[edge["target"]] += 1
    ready = [node_id for node_id in nodes if incoming[node_id] == 0]
    batches: list[list[str]] = []
    seen = 0
    while ready:
        batch = ready
        batches.append(batch)
        seen += len(batch)
        next_ready: set[str] = set()
        for node_id in batch:
            for target in outgoing[node_id]:
                incoming[target] -= 1
                if incoming[target] == 0:
                    next_ready.add(target)
        ready = [node_id for node_id in nodes if node_id in next_ready]
    if seen != len(nodes):
        raise WorkflowError("workflow graph contains a cycle; use iterations for repeated schedules")
    return batches


class WorkflowStore:
    """Project-local graph heads over immutable, content-addressed revisions."""

    def __init__(self, root: Path | str, *, dataset_resolver=None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.dataset_resolver = dataset_resolver

    @staticmethod
    def id_for_name(name: str) -> str:
        base = "".join(character if character.isalnum() else "-" for character in name.lower())
        return "-".join(filter(None, base.split("-"))) or "workflow"

    def _available_id(self, name: str) -> str:
        base = self.id_for_name(name)
        workflow_id = base
        counter = 2
        while (self.root / workflow_id).exists():
            workflow_id = f"{base}-{counter}"
            counter += 1
        return workflow_id

    @staticmethod
    def _empty_graph(name: str, workflow_id: str) -> dict[str, Any]:
        return {
            "workflow_id": workflow_id,
            "name": name.strip(),
            "description": "",
            "schema_version": 2,
            "workflow_type": "graph",
            "presentation": {"recipe": "graph"},
            "variants": [{"variant_id": "default", "name": "Default", "node_overrides": {}}],
            "evaluators": [],
            "execution_policy": {
                "max_concurrency": 1,
                "failure_policy": "fail_fast",
            },
            "iterations": 1,
            "nodes": [],
            "edges": [],
        }

    def create(self, name: str) -> dict[str, Any]:
        return self.save(self._empty_graph(name, self._available_id(name)))

    def create_from(self, name: str, source: dict[str, Any]) -> dict[str, Any]:
        """Create an independently versioned workflow from a template graph."""
        graph = self._empty_graph(name, self._available_id(name))
        graph.update(
            description=str(source.get("description") or ""),
            schema_version=int(source.get("schema_version") or 1),
            workflow_type=str(source.get("workflow_type") or "graph"),
            presentation=json.loads(json.dumps(source.get("presentation") or {"recipe": "graph"})),
            variants=json.loads(
                json.dumps(
                    source.get("variants")
                    or [{"variant_id": "default", "name": "Default", "node_overrides": {}}]
                )
            ),
            evaluators=json.loads(json.dumps(source.get("evaluators") or [])),
            execution_policy=json.loads(
                json.dumps(source.get("execution_policy") or {"max_concurrency": 1, "failure_policy": "fail_fast"})
            ),
            iterations=int(source.get("iterations") or 1),
            nodes=json.loads(json.dumps(source.get("nodes") or [])),
            edges=json.loads(json.dumps(source.get("edges") or [])),
        )
        if source.get("benchmark") is not None:
            graph["benchmark"] = json.loads(json.dumps(source["benchmark"]))
        if source.get("dataset") is not None:
            graph["dataset"] = json.loads(json.dumps(source["dataset"]))
        if source.get("dataset_ref") is not None:
            graph["dataset_ref"] = json.loads(json.dumps(source["dataset_ref"]))
        return self.save(graph)

    def save(
        self,
        graph: dict[str, Any],
        *,
        expected_revision_id: str | None = None,
    ) -> dict[str, Any]:
        _validate_graph(graph, dataset_resolver=self.dataset_resolver)
        workflow_id = _safe_identifier(graph.get("workflow_id"), "workflow_id")
        directory = self.root / workflow_id
        head_path = directory / "head.json"
        parent_revision_id = None
        head: dict[str, Any] = {}
        if head_path.is_file():
            head = json.loads(head_path.read_text(encoding="utf-8"))
            parent_revision_id = head["revision_id"]
            if expected_revision_id is not None and expected_revision_id != parent_revision_id:
                raise WorkflowError(f"workflow changed since it was loaded: expected {expected_revision_id}, current {parent_revision_id}")
        elif expected_revision_id is not None:
            raise WorkflowError("cannot match a revision for a new workflow")
        benchmark = None
        if graph.get("workflow_type") == "model_benchmark":
            try:
                benchmark = validate_benchmark(graph.get("benchmark"))
            except BenchmarkError as error:
                raise WorkflowError(str(error)) from error
        raw_execution_policy = graph.get("execution_policy") or {}
        stored_execution_policy: dict[str, Any] = {
            "max_concurrency": int(raw_execution_policy.get("max_concurrency", 1)),
            "failure_policy": str(raw_execution_policy.get("failure_policy", "fail_fast")),
        }
        if raw_execution_policy.get("max_calls") is not None:
            stored_execution_policy["max_calls"] = int(raw_execution_policy["max_calls"])
        if raw_execution_policy.get("max_total_cost_usd") is not None:
            stored_execution_policy["max_total_cost_usd"] = float(raw_execution_policy["max_total_cost_usd"])
        content = {
            "workflow_id": workflow_id,
            "name": graph["name"].strip(),
            "description": str(graph.get("description") or ""),
            "schema_version": int(graph.get("schema_version") or 1),
            "workflow_type": str(graph.get("workflow_type") or "graph"),
            "presentation": json.loads(json.dumps(graph.get("presentation") or {"recipe": _legacy_recipe_for_type(graph)})),
            "variants": json.loads(json.dumps(graph.get("variants") or [])),
            "evaluators": json.loads(json.dumps(graph.get("evaluators") or [])),
            "execution_policy": stored_execution_policy,
            "iterations": len(benchmark["cases"]) if benchmark else int(graph.get("iterations") or 1),
            "nodes": graph["nodes"],
            "edges": graph["edges"],
        }
        if benchmark is not None:
            content["benchmark"] = benchmark
        if graph.get("dataset") is not None:
            content["dataset"] = json.loads(json.dumps(graph["dataset"]))
        if graph.get("dataset_ref") is not None:
            content["dataset_ref"] = json.loads(json.dumps(graph["dataset_ref"]))
        if not 1 <= content["iterations"] <= 1000:
            raise WorkflowError("workflow iterations must be between 1 and 1000")
        identity = {"parent_revision_id": parent_revision_id, **content}
        revision_id = "wfrev_" + hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()[:24]
        revision = {
            **content,
            "revision_id": revision_id,
            "parent_revision_id": parent_revision_id,
            "created_at": _now(),
        }
        revision_path = directory / "revisions" / f"{revision_id}.json"
        if not revision_path.exists():
            _atomic_json(revision_path, revision)
        _atomic_json(
            head_path,
            {
                "workflow_id": workflow_id,
                "revision_id": revision_id,
                "name": content["name"],
                "created_at": head.get("created_at") or _now(),
                "pinned": bool(head.get("pinned")),
                "archived": bool(head.get("archived")),
                "updated_at": _now(),
            },
        )
        return revision

    def get(self, workflow_id: str, revision_id: str | None = None) -> dict[str, Any]:
        workflow_id = _safe_identifier(workflow_id, "workflow_id")
        directory = self.root / workflow_id
        head_path = directory / "head.json"
        if not head_path.is_file():
            raise KeyError(f"unknown workflow: {workflow_id}")
        if revision_id is None:
            revision_id = json.loads(head_path.read_text(encoding="utf-8"))["revision_id"]
        revision_id = _safe_identifier(revision_id, "workflow revision_id")
        path = directory / "revisions" / f"{revision_id}.json"
        if not path.is_file():
            raise KeyError(f"unknown workflow revision: {workflow_id}/{revision_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def list(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        values = [json.loads(path.read_text(encoding="utf-8")) for path in self.root.glob("*/head.json")]
        if not include_archived:
            values = [value for value in values if not value.get("archived")]
        values.sort(
            key=lambda item: (
                str(item.get("created_at") or item.get("updated_at") or ""),
                str(item["workflow_id"]),
            ),
            reverse=True,
        )
        values.sort(key=lambda item: not bool(item.get("pinned")))
        return values

    def update(
        self,
        workflow_id: str,
        *,
        pinned: bool | None = None,
        archived: bool | None = None,
    ) -> dict[str, Any]:
        """Update organizational metadata without creating a graph revision."""
        workflow_id = _safe_identifier(workflow_id, "workflow_id")
        path = self.root / workflow_id / "head.json"
        if not path.is_file():
            raise KeyError(f"unknown workflow: {workflow_id}")
        head = json.loads(path.read_text(encoding="utf-8"))
        if archived is not None:
            head["archived"] = archived
            if archived:
                head["pinned"] = False
        if pinned is not None and not head.get("archived"):
            head["pinned"] = pinned
        head["updated_at"] = _now()
        _atomic_json(path, head)
        return head

    def delete(self, workflow_id: str) -> dict[str, Any]:
        """Remove a workflow head while preserving immutable revisions."""
        workflow_id = _safe_identifier(workflow_id, "workflow_id")
        directory = self.root / workflow_id
        head_path = directory / "head.json"
        if not head_path.is_file():
            raise KeyError(f"unknown workflow: {workflow_id}")
        head = json.loads(head_path.read_text(encoding="utf-8"))
        tombstone = {**head, "deleted_at": _now()}
        _atomic_json(directory / "deleted.json", tombstone)
        head_path.unlink()
        return tombstone


def _prompt_binding(value: Any, label: str) -> PromptBinding:
    if not isinstance(value, dict):
        raise WorkflowError(f"{label} prompt binding is required")
    try:
        return PromptBinding(
            prompt_id=value["prompt_id"],
            revision_id=value["revision_id"],
            variables=value.get("variables") or {},
        )
    except KeyError as exc:
        raise WorkflowError(f"{label} prompt binding needs prompt_id and revision_id") from exc


def spec_from_node(config: dict[str, Any]) -> AgentRunSpec:
    context = config.get("context_policy") or {}
    completion = config.get("completion") or {}
    mode = context.get("mode", "none")
    if mode not in CONTEXT_MODES:
        raise WorkflowError(f"unknown context mode: {mode}")
    executor_id = str(config.get("executor_id") or "")
    model_provider = config.get("model_provider")
    if executor_id == "codex" and not str(model_provider or "").strip():
        raise WorkflowError("Codex workflow nodes require model_provider")
    return AgentRunSpec(
        executor_id=executor_id,
        model=str(config.get("model") or ""),
        model_provider=(str(model_provider) if model_provider is not None else None),
        effort=config.get("effort"),
        system_prompt=_prompt_binding(config.get("system_prompt"), "system"),
        user_prompt=_prompt_binding(config.get("user_prompt"), "user"),
        variables=config.get("variables") or {},
        skills=tuple(SkillBinding(skill["skill_id"], skill["revision_id"]) for skill in config.get("skills") or []),
        tools=tuple(ToolBinding(str(tool)) for tool in config.get("tools") or []),
        mcp_connections=tuple(str(item) for item in config.get("mcp_connections") or []),
        context_policy=ContextPolicy(
            mode=mode,
            max_tokens=context.get("max_tokens"),
            include_agent_outputs=tuple(context.get("include_agent_outputs") or ()),
            include_artifact_kinds=tuple(context.get("include_artifact_kinds") or ()),
        ),
        output_schema=config.get("output_schema"),
        budget=RunBudget(**(config.get("budget") or {})),
        sandbox=SandboxPolicy(**(config.get("sandbox") or {})),
        session=SessionPolicy(**(config.get("session") or {})),
        completion_markers=tuple(completion.get("required_markers") or ()),
        completion_tool_calls=dict(completion.get("required_tool_calls") or {}),
    )


@dataclass
class AgentRuntime:
    prompts: PromptRegistry
    skills: SkillRegistry
    tools: ToolRegistry
    runs: RunStore
    executors: dict[str, Any]
    ledger_root: Path
    artifacts_root: Path
    ledger_command: str = "reasoning-ledger"
    scientific_cache_root: Path | None = None
    bibliography_root: Path | None = None
    project_id: str | None = None
    control_url: str | None = None
    mcp_connections: McpConnectionStore | None = None

    @staticmethod
    def _values(revision, supplied: dict[str, Any]) -> dict[str, Any]:
        return {slot: supplied[slot] for slot in revision.slots if slot in supplied}

    def prepare(
        self,
        run_id: str,
        spec: AgentRunSpec,
        *,
        variables: dict[str, Any] | None = None,
        context_messages: tuple[dict[str, Any], ...] = (),
    ) -> PreparedAgentRun:
        if spec.executor_id not in self.executors:
            raise WorkflowError(f"unknown executor: {spec.executor_id}")
        merged = {**dict(spec.variables), **(variables or {})}
        system_revision = self.prompts.get(spec.system_prompt.prompt_id, spec.system_prompt.revision_id)
        user_revision = self.prompts.get(spec.user_prompt.prompt_id, spec.user_prompt.revision_id)
        system_values = {
            **merged,
            **dict(spec.system_prompt.variables),
        }
        user_values = {**merged, **dict(spec.user_prompt.variables)}
        system_prompt = system_revision.render(self._values(system_revision, system_values))
        user_prompt = user_revision.render(self._values(user_revision, user_values))
        prepared_skills = self.skills.prepare(spec.skills, executor_id=spec.executor_id)

        allowed_risks = ["read"]
        capabilities = [
            "bibliography:read",
            "ledger:read",
            "artifacts:read",
            "proof:inspect",
            "science:compute",
        ]
        if spec.sandbox.mode in {"workspace_write", "write"}:
            allowed_risks.append("write")
            capabilities.extend(
                (
                    "artifacts:write",
                    "bibliography:write",
                    "proof:check",
                )
            )
        elif spec.sandbox.mode == "research":
            # Direct research chats keep an executor's own filesystem and
            # network tools read-only/off, but may use the explicitly named
            # project artifact writer and curated external query gateways.
            # The requested tool ids below still provide the capability's
            # narrow allowlist; this does not grant the other write tools.
            allowed_risks.extend(("write", "external"))
            capabilities.extend(("artifacts:write", "science:query"))
        if spec.sandbox.network:
            allowed_risks.append("external")
            capabilities.append("science:query")
        definitions = self.tools.select(
            ToolGrant(
                tuple(binding.tool_id for binding in spec.tools),
                allowed_risks=tuple(allowed_risks),
                capabilities=tuple(capabilities),
            )
        )
        granted_names = {definition.name for definition in definitions}
        for skill_binding, prepared in zip(spec.skills, prepared_skills, strict=True):
            skill = self.skills.get(skill_binding.skill_id, skill_binding.revision_id)
            missing_tools = set(skill.tool_dependencies) - granted_names
            if missing_tools:
                raise ToolRegistryError(f"skill {prepared.name} needs tools not granted to the node: " + ", ".join(sorted(missing_tools)))
            missing_capabilities = set(skill.required_capabilities) - set(capabilities)
            if missing_capabilities:
                raise ToolRegistryError(
                    f"skill {prepared.name} needs capabilities not granted to the node: " + ", ".join(sorted(missing_capabilities))
                )

        if prepared_skills:
            catalog = [
                {
                    "skill_id": skill.skill_id,
                    "revision_id": skill.revision_id,
                    "name": skill.name,
                    "description": skill.description,
                }
                for skill in prepared_skills
            ]
            system_prompt += (
                "\n\n<reasoning-skill-catalog>\n"
                "These exact skill revisions are available to this run. Their full "
                "instructions are intentionally not preloaded. When a skill applies, "
                "call skill_read with its skill_id and revision_id to read SKILL.md; "
                "then read only supporting files that SKILL.md identifies as needed.\n"
                + json.dumps(catalog, ensure_ascii=False)
                + "\n</reasoning-skill-catalog>"
            )
        prompt_hashes = {
            "system_revision": system_revision.revision_id,
            "user_revision": user_revision.revision_id,
            "effective_system_sha256": hashlib.sha256(system_prompt.encode()).hexdigest(),
            "effective_user_sha256": hashlib.sha256(user_prompt.encode()).hexdigest(),
        }
        prepared_tools = list(self.tools.prepared(definitions))
        if prepared_skills:
            prepared_tools.append(
                PreparedTool(
                    "skill_read",
                    ("Activate a selected skill or read one supporting file. Read SKILL.md first and use only the exact bound revision."),
                    {
                        "type": "object",
                        "properties": {
                            "skill_id": {"type": "string", "minLength": 1},
                            "revision_id": {"type": "string", "minLength": 1},
                            "path": {"type": "string", "minLength": 1},
                        },
                        "required": ["skill_id", "revision_id"],
                        "additionalProperties": False,
                    },
                )
            )
        tool_names = tuple(tool.name for tool in prepared_tools)
        mcp_servers: dict[str, dict[str, Any]] = {}
        if tool_names and spec.executor_id != "reasoning-native":
            mcp_servers["reasoning"] = project_mcp_config(
                self.ledger_root,
                self.artifacts_root,
                command=self.ledger_command,
                allowed_tools=tool_names,
                max_calls=spec.budget.max_tool_calls,
                completion_tool_calls=dict(spec.completion_tool_calls),
                completion_artifacts=tuple(requirement.path for requirement in spec.completion_artifacts),
                scientific_cache_root=self.scientific_cache_root,
                skills_root=self.skills.root,
                skills=spec.skills,
                bibliography_root=self.bibliography_root,
                project_id=self.project_id,
                control_url=self.control_url,
            )
        if spec.mcp_connections:
            capabilities = self.executors[spec.executor_id].capabilities
            if not capabilities.supports_mcp:
                raise McpConnectionError(f"{capabilities.display_name} does not support per-run MCP connections")
            if self.mcp_connections is None:
                raise McpConnectionError("user MCP connections are not configured")
            if self.mcp_connections.requires_network(spec.mcp_connections) and not spec.sandbox.network:
                raise McpConnectionError("remote MCP connections require 'allow network tools' on this agent")
            mcp_servers.update(self.mcp_connections.runtime_config(spec.mcp_connections))
        return PreparedAgentRun(
            run_id=run_id,
            spec=spec,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            prompt_hashes=prompt_hashes,
            context_messages=context_messages,
            tools=tuple(prepared_tools),
            skills=prepared_skills,
            mcp_servers=mcp_servers,
        )

    def execute(
        self,
        spec: AgentRunSpec,
        *,
        variables: dict[str, Any] | None = None,
        context_messages: tuple[dict[str, Any], ...] = (),
        emit: EventSink | None = None,
    ) -> AgentRunResult:
        run_id = self.runs.create(spec)
        try:
            prepared = self.prepare(
                run_id,
                spec,
                variables=variables,
                context_messages=context_messages,
            )
            self.runs.update_manifest(
                run_id,
                status="running",
                prepared={
                    "prompt_hashes": dict(prepared.prompt_hashes),
                    "skills": [{"skill_id": skill.skill_id, "revision_id": skill.revision_id} for skill in prepared.skills],
                    "tools": [tool.name for tool in prepared.tools],
                    "executor": spec.executor_id,
                    "model": spec.model,
                    "model_provider": spec.model_provider,
                },
            )
            def record_event(event: AgentEvent) -> None:
                persisted = self.runs.append_event(run_id, event.kind, event.data)
                if emit is not None:
                    emit(persisted)

            result = self.executors[spec.executor_id].execute(prepared, record_event)
        except Exception as exc:
            result = AgentRunResult(run_id, "failed", error=str(exc))
        self.runs.update_manifest(
            run_id,
            status=result.status,
            result={
                "output_text": result.output_text,
                "structured_output": result.structured_output,
                "usage": dict(result.usage),
                "session_id": result.session_id,
                "error": result.error,
                "metadata": dict(result.metadata),
            },
        )
        return result


class WorkflowRunStore:
    """Thread-safe parent records for multi-node, multi-iteration runs."""

    def __init__(self, root: Path | str, artifacts_root: Path | str | None = None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.artifacts_root = Path(artifacts_root) if artifacts_root is not None else None
        self._lock = threading.Lock()

    def _persist(self, state: dict[str, Any]) -> None:
        _atomic_json(self.root / f"{state['run_id']}.json", state)
        if self.artifacts_root is None:
            return
        relative = Path("workflow-runs")
        _atomic_json(self.artifacts_root / relative / f"{state['run_id']}.json", state)
        _atomic_text(
            self.artifacts_root / relative / f"{state['run_id']}.md",
            _workflow_report(state),
        )

    def create(self, workflow: dict[str, Any], iterations: int, variables: dict[str, Any]) -> str:
        run_id = f"wfrun_{uuid.uuid4().hex}"
        created_at = _now()
        artifact_path = f"workflow-runs/{run_id}.md" if self.artifacts_root else None
        artifact_json_path = f"workflow-runs/{run_id}.json" if self.artifacts_root else None
        state = {
            "run_id": run_id,
            "workflow_id": workflow["workflow_id"],
            "workflow_revision_id": workflow["revision_id"],
            "workflow_snapshot": json.loads(json.dumps(workflow)),
            "status": "pending",
            "iterations": iterations,
            "variables": variables,
            "events": [],
            "outputs": {},
            "cost": _workflow_cost([]),
            "artifact_path": artifact_path,
            "artifact_json_path": artifact_json_path,
            "cancel_requested": False,
            "created_at": created_at,
            "updated_at": created_at,
        }
        self._persist(state)
        return run_id

    def get(self, run_id: str) -> dict[str, Any]:
        path = self.root / f"{run_id}.json"
        if not path.is_file():
            raise KeyError(f"unknown workflow run: {run_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def update(self, run_id: str, **changes: Any) -> dict[str, Any]:
        with self._lock:
            state = self.get(run_id)
            state.update(changes)
            outputs = (state.get("outputs") or {}).get("nodes") or []
            state["cost"] = _workflow_cost(outputs)
            state["updated_at"] = _now()
            self._persist(state)
            return state

    def event(self, run_id: str, event: dict[str, Any]) -> None:
        with self._lock:
            state = self.get(run_id)
            state["events"].append({"sequence": len(state["events"]) + 1, "at": _now(), **event})
            state["updated_at"] = _now()
            self._persist(state)

    def finish(
        self,
        run_id: str,
        status: str,
        event: dict[str, Any],
        **changes: Any,
    ) -> dict[str, Any]:
        """Persist a terminal state and its final event as one checkpoint."""
        with self._lock:
            state = self.get(run_id)
            state.update(changes)
            state["status"] = status
            outputs = (state.get("outputs") or {}).get("nodes") or []
            state["cost"] = _workflow_cost(outputs)
            state["events"].append({"sequence": len(state["events"]) + 1, "at": _now(), **event})
            state["updated_at"] = _now()
            self._persist(state)
            return state

    def list(self, *, workflow_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        states = [json.loads(path.read_text(encoding="utf-8")) for path in self.root.glob("wfrun_*.json")]
        if workflow_id is not None:
            states = [state for state in states if state["workflow_id"] == workflow_id]
        return sorted(states, key=lambda state: state["created_at"], reverse=True)[:limit]

    def request_cancel(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            state = self.get(run_id)
            if state["status"] not in {"pending", "running", "cancelling"}:
                return state
            state["cancel_requested"] = True
            state["status"] = "cancelling"
            state["updated_at"] = _now()
            self._persist(state)
            return state


class _WorkflowHarnessEventRelay:
    """Bound and coalesce executor events before checkpointing a parent run."""

    _DELTA_KINDS = {"message.delta", "reasoning.delta"}

    def __init__(
        self,
        record: Callable[[dict[str, Any]], None],
        *,
        workflow_run_id: str,
        scope: dict[str, Any],
    ):
        self._record = record
        self._workflow_run_id = workflow_run_id
        self._scope = scope
        self._pending: dict[str, Any] | None = None
        self._last_flush = 0.0

    def __call__(self, event: AgentEvent) -> None:
        value = canonical_harness_event(
            event,
            session_id=self._workflow_run_id,
            turn_id=event.run_id,
        )
        if value["kind"] not in self._DELTA_KINDS:
            self.flush()
            self._write(value, event.run_id)
            return

        text = value["data"].get("text")
        if not isinstance(text, str) or not text:
            return
        if self._pending is not None and self._pending["kind"] == value["kind"]:
            start = self._pending["data"].get(
                "source_sequence_start",
                self._pending["sequence"],
            )
            self._pending["data"]["text"] += text
            self._pending["data"]["source_sequence_start"] = start
            self._pending["sequence"] = value["sequence"]
            self._pending["at"] = value["at"]
        else:
            self.flush()
            self._pending = value
        now = time.monotonic()
        if self._last_flush == 0.0 or now - self._last_flush >= 0.25 or len(self._pending["data"]["text"]) >= 2_048:
            self.flush()

    def flush(self) -> None:
        if self._pending is None:
            return
        value = self._pending
        self._pending = None
        self._write(value, str(value["turn_id"]))

    def _write(self, value: dict[str, Any], agent_run_id: str) -> None:
        self._record(
            {
                "type": "harness.event",
                **self._scope,
                "agent_run_id": agent_run_id,
                "harness_event": value,
            }
        )
        self._last_flush = time.monotonic()


class WorkflowRunner:
    def __init__(
        self,
        runtime: AgentRuntime,
        runs: WorkflowRunStore,
        datasets: DatasetStore | None = None,
        answer_keys: AnswerKeyStore | None = None,
    ):
        self.runtime = runtime
        self.runs = runs
        self.datasets = datasets
        self.answer_keys = answer_keys
        self._threads: dict[str, threading.Thread] = {}
        self._threads_lock = threading.Lock()
        if self.runs.artifacts_root is None:
            self.runs.artifacts_root = runtime.artifacts_root
        self._settle_dead_process_runs()

    @staticmethod
    def _process_alive(pid: object) -> bool:
        try:
            os.kill(int(pid), 0)
        except (OSError, TypeError, ValueError):
            return False
        return True

    def _settle_dead_process_runs(self) -> None:
        """Close durable transient records whose owning server is gone."""
        for state in self.runs.list(limit=1_000_000):
            if state.get("status") not in {"pending", "running", "cancelling"}:
                continue
            pid = state.get("runner_pid")
            if pid is None or self._process_alive(pid):
                continue
            run_id = state["run_id"]
            if state.get("cancel_requested"):
                self.runs.finish(run_id, "cancelled", {"type": "workflow.cancelled"})
                continue
            error = "the workflow worker process died (for example during a service restart); start a new run from the saved revision"
            self.runs.finish(
                run_id,
                "failed",
                {"type": "workflow.failed", "error": error},
                error=error,
            )

    @staticmethod
    def _context(
        policy: ContextPolicy,
        node_id: str,
        predecessors: dict[str, list[str]],
        outputs: list[dict[str, Any]],
        input_values: dict[str, str],
    ) -> tuple[dict[str, Any], ...]:
        if policy.mode == "none":
            return ()
        messages = []
        if policy.mode in {"predecessors", "all"}:
            messages.extend(
                {
                    "role": "user",
                    "content": (f'<reasoning-workflow-input node="{source}">\n{input_values[source]}\n</reasoning-workflow-input>'),
                }
                for source in predecessors[node_id]
                if source in input_values and input_values[source]
            )
        selected = []
        for output in outputs:
            source = output["node_id"]
            if policy.mode == "predecessors" and source not in predecessors[node_id]:
                continue
            if policy.mode == "selected" and source not in policy.include_agent_outputs:
                continue
            selected.append(output)
        if policy.mode == "rolling":
            selected = selected[-max(1, len(policy.include_agent_outputs) or 1) :]
        messages.extend(
            {
                "role": "user",
                "content": (
                    f'<reasoning-agent-output node="{item["node_id"]}" '
                    f'iteration="{item["iteration"]}">\n'
                    f"{item['text']}\n"
                    "</reasoning-agent-output>"
                ),
            }
            for item in selected
            if item.get("text")
        )
        if policy.max_tokens:
            remaining = policy.max_tokens * 4
            clipped: list[dict[str, Any]] = []
            for message in reversed(messages):
                text = str(message["content"])
                if remaining <= 0:
                    break
                clipped.append({**message, "content": text[-remaining:]})
                remaining -= len(text)
            messages = list(reversed(clipped))
        return tuple(messages)

    @staticmethod
    def _render_completion_value(template: str, variables: dict[str, Any]) -> str:
        missing: set[str] = set()

        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            if name not in variables:
                missing.add(name)
                return match.group(0)
            return str(variables[name])

        rendered = _WORKFLOW_SLOT.sub(replace, template)
        if missing:
            raise WorkflowError("missing workflow completion variables: " + ", ".join(sorted(missing)))
        return rendered

    def _completion_artifacts(
        self,
        config: dict[str, Any],
        variables: dict[str, Any],
    ) -> dict[str, tuple[Path, str | None]]:
        completion = config.get("completion") or {}
        root = self.runtime.artifacts_root.resolve()
        artifacts: dict[str, tuple[Path, str | None]] = {}
        for template in completion.get("required_artifacts") or []:
            relative = self._render_completion_value(template, variables)
            target = (root / relative).resolve()
            if target == root or not target.is_relative_to(root):
                raise WorkflowError(f"completion artifact escapes the project: {relative}")
            before = hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else None
            artifacts[relative] = (target, before)
        return artifacts

    @staticmethod
    def _completion_error(
        config: dict[str, Any],
        result: AgentRunResult,
        artifacts: dict[str, tuple[Path, str | None]],
        events: list[dict[str, Any]],
    ) -> str | None:
        completion = config.get("completion") or {}
        artifact_contents: list[str] = []
        missing_artifacts: list[str] = []
        unchanged_artifacts: list[str] = []
        for relative, (target, before) in artifacts.items():
            if not target.is_file():
                missing_artifacts.append(relative)
                continue
            data = target.read_bytes()
            after = hashlib.sha256(data).hexdigest()
            if before == after:
                unchanged_artifacts.append(relative)
            artifact_contents.append(data.decode("utf-8", errors="replace"))
        marker_contents = "\n".join(artifact_contents) if artifacts else result.output_text
        missing_markers = [marker for marker in completion.get("required_markers") or [] if marker not in marker_contents]
        failures: list[str] = []
        if missing_markers:
            failures.append("missing markers: " + ", ".join(missing_markers))
        if missing_artifacts:
            failures.append("missing artifacts: " + ", ".join(missing_artifacts))
        if unchanged_artifacts:
            failures.append("artifacts were not updated: " + ", ".join(unchanged_artifacts))
        successful_tool_calls: dict[str, int] = defaultdict(int)
        for event in events:
            if event.get("kind") != AgentEventKind.TOOL_COMPLETED.value:
                continue
            data = event.get("data")
            if not isinstance(data, dict):
                continue
            name = data.get("name")
            if not isinstance(name, str) or not name:
                continue
            ok = data.get("ok")
            status = data.get("status")
            error = data.get("error")
            succeeded = ok is True or (ok is None and error in (None, "") and status in (None, "completed", "success", "succeeded"))
            if succeeded:
                successful_tool_calls[name] += 1
        insufficient_tool_calls = [
            f"{name} ({successful_tool_calls[name]}/{minimum})"
            for name, minimum in (completion.get("required_tool_calls") or {}).items()
            if successful_tool_calls[name] < minimum
        ]
        if insufficient_tool_calls:
            failures.append("insufficient successful tool calls: " + ", ".join(insufficient_tool_calls))
        return "; ".join(failures) or None

    def start(
        self,
        workflow: dict[str, Any],
        *,
        iterations: int | None = None,
        variables: dict[str, Any] | None = None,
        initial_prompt: str = "",
    ) -> str:
        resolver = self.datasets.get if self.datasets is not None else None
        try:
            contract = normalize_workflow_contract(
                workflow,
                iterations=iterations,
                dataset_resolver=resolver,
            )
        except (WorkflowContractError, KeyError) as error:
            raise WorkflowError(str(error)) from error
        for evaluator in contract["evaluators"]:
            if evaluator["kind"] != "typed_answer_v1":
                continue
            if self.answer_keys is None:
                raise WorkflowError("typed answer evaluation requires a private answer key store")
            answer_key_ref = evaluator["config"]["answer_key_ref"]
            try:
                answer_key = self.answer_keys.get_private(
                    answer_key_ref["answer_key_id"],
                    answer_key_ref["revision_id"],
                )
            except (KeyError, ValidationError) as error:
                raise WorkflowError(str(error)) from error
            if answer_key.get("dataset_ref") != contract["dataset_ref"]:
                raise WorkflowError("typed answer key is bound to a different dataset revision")
            expected_cases = {str(case["case_id"]) for case in contract["dataset"]["cases"]}
            key_cases = {str(item.get("case_id")) for item in answer_key.get("targets") or []}
            if expected_cases != key_cases:
                raise WorkflowError("typed answer key cases do not match the resolved dataset")
        count = int(contract["case_count"])
        supplied = dict(variables or {})
        prompt = initial_prompt.strip()
        if prompt:
            for name in ("question", "prompt", "input"):
                supplied.setdefault(name, prompt)
            for node in workflow.get("nodes", []):
                if node.get("kind") == "input":
                    supplied.setdefault(str(node["id"]), prompt)
        snapshot = json.loads(json.dumps(workflow))
        snapshot["resolved_contract"] = contract
        run_id = self.runs.create(snapshot, count, supplied)
        self.runs.update(run_id, runner_pid=os.getpid())
        thread = threading.Thread(
            target=self._execute_owned,
            args=(run_id, workflow, contract, count, supplied, prompt),
            daemon=True,
            name=f"workflow-{run_id}",
        )
        with self._threads_lock:
            self._threads[run_id] = thread
        thread.start()
        return run_id

    def request_cancel(self, run_id: str) -> dict[str, Any]:
        state = self.runs.request_cancel(run_id)
        if state.get("status") != "cancelling":
            return state
        with self._threads_lock:
            thread = self._threads.get(run_id)
        if thread is not None and thread.is_alive():
            return state
        pid = state.get("runner_pid")
        if pid is not None and int(pid) != os.getpid() and self._process_alive(pid):
            return state
        self.runs.finish(run_id, "cancelled", {"type": "workflow.cancelled"})
        return self.runs.get(run_id)

    def _execute_owned(
        self,
        run_id: str,
        workflow: dict[str, Any],
        contract: dict[str, Any],
        iterations: int,
        variables: dict[str, Any],
        initial_prompt: str,
    ) -> None:
        try:
            self._execute(run_id, workflow, contract, iterations, variables, initial_prompt)
        finally:
            with self._threads_lock:
                self._threads.pop(run_id, None)

    def _execute_agent_node(
        self,
        run_id: str,
        node_id: str,
        node: dict[str, Any],
        iteration: int,
        variables: dict[str, Any],
        initial_prompt: str,
        predecessors: dict[str, list[str]],
        outputs: list[dict[str, Any]],
        input_values: dict[str, str],
        *,
        case_id: str,
        case_name: str,
        variant_id: str,
        variant_name: str,
        work_item_id: str,
    ) -> dict[str, Any]:
        started_at = _now()
        started = time.monotonic()
        config = node.get("config") or {}
        self.runs.event(
            run_id,
            {
                "type": "node.started",
                "iteration": iteration,
                "case_id": case_id,
                "variant_id": variant_id,
                "work_item_id": work_item_id,
                "node_id": node_id,
            },
        )
        spec: AgentRunSpec | None = None
        result: AgentRunResult | None = None
        try:
            spec = spec_from_node(config)
            context = self._context(
                spec.context_policy,
                node_id,
                predecessors,
                outputs,
                input_values,
            )
            if initial_prompt and not predecessors[node_id]:
                user_revision = self.runtime.prompts.get(
                    spec.user_prompt.prompt_id,
                    spec.user_prompt.revision_id,
                )
                consumes_launch_prompt = bool({"question", "prompt", "input"}.intersection(user_revision.slots))
                if not consumes_launch_prompt:
                    context += (
                        {
                            "role": "user",
                            "content": (f"<reasoning-workflow-initial-prompt>\n{initial_prompt}\n</reasoning-workflow-initial-prompt>"),
                        },
                    )
            node_variables = {
                **variables,
                "iteration": iteration,
                "week": iteration,
            }
            completion_artifacts = self._completion_artifacts(config, node_variables)
            spec = replace(
                spec,
                completion_artifacts=tuple(
                    CompletionArtifactRequirement(relative, before) for relative, (_target, before) in completion_artifacts.items()
                ),
            )
            relay = _WorkflowHarnessEventRelay(
                lambda event: self.runs.event(run_id, event),
                workflow_run_id=run_id,
                scope={
                    "iteration": iteration,
                    "case_id": case_id,
                    "variant_id": variant_id,
                    "work_item_id": work_item_id,
                    "node_id": node_id,
                },
            )
            try:
                result = self.runtime.execute(
                    spec,
                    variables=node_variables,
                    context_messages=context,
                    emit=relay,
                )
            finally:
                relay.flush()
            completion_error = (
                self._completion_error(
                    config,
                    result,
                    completion_artifacts,
                    self.runtime.runs.events(result.run_id),
                )
                if result.status == "completed"
                else None
            )
            node_status = "failed" if completion_error else result.status
            node_error = completion_error or result.error
        except Exception as exc:
            node_status = "failed"
            node_error = str(exc)

        executor_id = spec.executor_id if spec is not None else str(config.get("executor_id") or "")
        uses_openrouter = bool(
            executor_id == "reasoning-native"
            or str(spec.model_provider if spec is not None else config.get("model_provider") or "").casefold() == "openrouter"
            or str(spec.model if spec is not None else config.get("model") or "").casefold().startswith("openrouter/")
        )
        if node_error and uses_openrouter:
            node_error = sanitize_provider_error_message(node_error)
        executor = self.runtime.executors.get(executor_id)
        prompt_fidelity = None
        if executor is not None:
            prompt_fidelity = executor.capabilities.prompt_fidelity.value
        completion = _completion_details(result)
        return {
            "iteration": iteration,
            "case_id": case_id,
            "case_name": case_name,
            "variant_id": variant_id,
            "variant_name": variant_name,
            "work_item_id": work_item_id,
            "node_id": node_id,
            "executor_id": executor_id,
            "model": spec.model if spec is not None else str(config.get("model") or ""),
            "model_provider": (spec.model_provider if spec is not None else config.get("model_provider")),
            "agent_run_id": result.run_id if result is not None else None,
            "status": node_status,
            "text": result.output_text if result is not None else "",
            "structured_output": result.structured_output if result is not None else None,
            "usage": dict(result.usage) if result is not None else {},
            "error": node_error,
            "started_at": started_at,
            "completed_at": _now(),
            "duration_seconds": round(time.monotonic() - started, 3),
            "prompt_fidelity": prompt_fidelity,
            "resolved_model": completion.get("model"),
            "serving_provider": completion.get("provider"),
            "response_id": completion.get("response_id"),
            "finish_reason": completion.get("finish_reason"),
            "native_finish_reason": completion.get("native_finish_reason"),
        }

    def _execute(
        self,
        run_id: str,
        workflow: dict[str, Any],
        contract: dict[str, Any],
        iterations: int,
        variables: dict[str, Any],
        initial_prompt: str,
    ) -> None:
        def output_state(outputs: list[dict[str, Any]]) -> dict[str, Any]:
            evaluations = evaluate_workflow(
                contract["evaluators"],
                dataset=contract["dataset"],
                variants=contract["variants"],
                nodes=workflow.get("nodes") or [],
                edges=workflow.get("edges") or [],
                outputs=outputs,
                dataset_ref=contract["dataset_ref"],
                answer_key_resolver=(self.answer_keys.get_private if self.answer_keys is not None else None),
            )
            value: dict[str, Any] = {"nodes": outputs, "evaluations": evaluations}
            for evaluator in contract["evaluators"]:
                if evaluator["kind"] == "paper_moderation_v1":
                    value["benchmark"] = evaluations.get(evaluator["evaluator_id"])
                    break
            return value

        def cancellation_requested() -> bool:
            return bool(self.runs.get(run_id).get("cancel_requested"))

        def finish_cancelled(outputs: list[dict[str, Any]]) -> None:
            self.runs.finish(
                run_id,
                "cancelled",
                {"type": "workflow.cancelled"},
                outputs=output_state(outputs),
            )

        if cancellation_requested():
            finish_cancelled([])
            return
        self.runs.update(run_id, status="running")
        self.runs.event(
            run_id,
            {
                "type": "workflow.started",
                "case_count": contract["case_count"],
                "variant_count": contract["variant_count"],
                "scheduled_calls": contract["scheduled_calls"],
            },
        )
        base_nodes = {node["id"]: node for node in workflow["nodes"]}
        predecessors: dict[str, list[str]] = defaultdict(list)
        for edge in workflow["edges"]:
            predecessors[edge["target"]].append(edge["source"])
        outputs: list[dict[str, Any]] = []
        output_lock = threading.Lock()
        budget_exhausted = threading.Event()
        budget_announced = threading.Event()
        budget_announcement_lock = threading.Lock()
        maximum_total_cost = contract.get("max_total_cost_usd")
        execution_policy = workflow.get("execution_policy") or {}
        max_concurrency = int(execution_policy.get("max_concurrency", 1))
        failure_policy = str(execution_policy.get("failure_policy", "fail_fast"))
        legacy_iteration_events = contract["sequence_mode"] and len(contract["variants"]) == 1

        def publish(output: dict[str, Any]) -> None:
            with output_lock:
                outputs.append(output)
                snapshot = list(outputs)
                self.runs.update(run_id, outputs=output_state(snapshot))
                self.runs.event(run_id, {"type": "node.completed", **output})
            recorded_cost = _workflow_cost(snapshot)["total_usd"]
            if (
                maximum_total_cost is not None
                and recorded_cost >= float(maximum_total_cost)
            ):
                budget_exhausted.set()
                with budget_announcement_lock:
                    if not budget_announced.is_set():
                        budget_announced.set()
                        self.runs.event(
                            run_id,
                            {
                                "type": "workflow.budget_exhausted",
                                "limit_usd": float(maximum_total_cost),
                                "recorded_usd": recorded_cost,
                            },
                        )

        def execute_work_item(
            iteration: int,
            case: dict[str, Any],
            variant: dict[str, Any],
            prior_outputs: list[dict[str, Any]],
            *,
            parallel_siblings: bool,
        ) -> list[dict[str, Any]]:
            if cancellation_requested() or budget_exhausted.is_set():
                return []
            work_item_id = f"{case['case_id']}--{variant['variant_id']}"
            work_variables = {
                **variables,
                **case_variables(case),
                "iteration": iteration,
                "week": iteration,
                "variant_id": variant["variant_id"],
                "variant_name": variant["name"],
                "work_item_id": work_item_id,
            }
            nodes = {
                node_id: apply_variant(node, variant)
                for node_id, node in base_nodes.items()
            }
            input_values = {
                node_id: str(work_variables[node_id])
                for node_id, node in nodes.items()
                if node["kind"] == "input" and work_variables.get(node_id) is not None
            }
            work_outputs: list[dict[str, Any]] = []
            self.runs.event(
                run_id,
                {
                    "type": "iteration.started" if legacy_iteration_events else "work_item.started",
                    "iteration": iteration,
                    "case_id": case["case_id"],
                    "variant_id": variant["variant_id"],
                    "work_item_id": work_item_id,
                },
            )

            def call(node_id: str, context_outputs: list[dict[str, Any]]) -> dict[str, Any]:
                return self._execute_agent_node(
                    run_id,
                    node_id,
                    nodes[node_id],
                    iteration,
                    work_variables,
                    initial_prompt,
                    predecessors,
                    context_outputs,
                    input_values,
                    case_id=case["case_id"],
                    case_name=case["name"],
                    variant_id=variant["variant_id"],
                    variant_name=variant["name"],
                    work_item_id=work_item_id,
                )

            for batch in _topological_batches(workflow):
                agent_ids = [node_id for node_id in batch if nodes[node_id]["kind"] == "agent"]
                if not agent_ids:
                    continue
                failed_outputs: list[dict[str, Any]] = []
                context_outputs = [*prior_outputs, *work_outputs]
                if parallel_siblings and max_concurrency > 1 and len(agent_ids) > 1:
                    with ThreadPoolExecutor(
                        max_workers=min(max_concurrency, len(agent_ids)),
                        thread_name_prefix=f"workflow-{run_id[:12]}",
                    ) as pool:
                        futures = {
                            pool.submit(call, node_id, context_outputs): node_id
                            for node_id in agent_ids
                        }
                        for future in as_completed(futures):
                            output = future.result()
                            work_outputs.append(output)
                            publish(output)
                            if output["status"] != "completed":
                                failed_outputs.append(output)
                else:
                    for node_id in agent_ids:
                        if cancellation_requested() or budget_exhausted.is_set():
                            return work_outputs
                        output = call(node_id, [*prior_outputs, *work_outputs])
                        work_outputs.append(output)
                        publish(output)
                        if output["status"] != "completed":
                            failed_outputs.append(output)
                            if failure_policy == "fail_fast":
                                break
                if failed_outputs and failure_policy == "fail_fast":
                    failed = failed_outputs[0]
                    failed_node = nodes[failed["node_id"]]
                    raise WorkflowError(
                        f"node {failed_node.get('name') or failed['node_id']} failed: "
                        f"{failed.get('error') or 'unknown error'}"
                    )
            if cancellation_requested():
                return work_outputs
            self.runs.event(
                run_id,
                {
                    "type": "iteration.completed" if legacy_iteration_events else "work_item.completed",
                    "iteration": iteration,
                    "case_id": case["case_id"],
                    "variant_id": variant["variant_id"],
                    "work_item_id": work_item_id,
                },
            )
            return work_outputs

        try:
            work_items = [
                (iteration, case, variant)
                for iteration, case in enumerate(contract["dataset"]["cases"], start=1)
                for variant in contract["variants"]
            ]
            expanded = not contract["sequence_mode"] or len(contract["variants"]) > 1
            if expanded and max_concurrency > 1 and len(work_items) > 1:
                with ThreadPoolExecutor(
                    max_workers=min(max_concurrency, len(work_items)),
                    thread_name_prefix=f"workflow-item-{run_id[:12]}",
                ) as pool:
                    futures = {
                        pool.submit(execute_work_item, iteration, case, variant, [], parallel_siblings=False): (
                            iteration,
                            case,
                            variant,
                        )
                        for iteration, case, variant in work_items
                    }
                    for future in as_completed(futures):
                        try:
                            future.result()
                        except Exception:
                            for pending in futures:
                                pending.cancel()
                            raise
                        if cancellation_requested():
                            for pending in futures:
                                pending.cancel()
                            break
                if cancellation_requested():
                    finish_cancelled(outputs)
                    return
            else:
                prior_outputs: list[dict[str, Any]] = []
                for iteration, case, variant in work_items:
                    if cancellation_requested():
                        finish_cancelled(outputs)
                        return
                    produced = execute_work_item(
                        iteration,
                        case,
                        variant,
                        prior_outputs if contract["sequence_mode"] else [],
                        parallel_siblings=contract["sequence_mode"] and len(contract["variants"]) == 1,
                    )
                    if contract["sequence_mode"]:
                        prior_outputs.extend(produced)
                if cancellation_requested():
                    finish_cancelled(outputs)
                    return
            final_status = (
                "partial"
                if len(outputs) < contract["scheduled_calls"]
                or any(output.get("status") != "completed" for output in outputs)
                else "completed"
            )
            self.runs.finish(
                run_id,
                final_status,
                {"type": f"workflow.{final_status}"},
                outputs=output_state(outputs),
            )
        except Exception as exc:
            self.runs.finish(
                run_id,
                "failed",
                {"type": "workflow.failed", "error": str(exc)},
                error=str(exc),
                outputs=output_state(outputs),
            )
