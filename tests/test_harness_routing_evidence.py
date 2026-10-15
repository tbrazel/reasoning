from __future__ import annotations

from reasoning_ledger.harnesses import (
    RoutingObjective,
    SelectedRunConfiguration,
    chat_operation_evidence,
    estimate_catalog_call_cost,
    product_routing_policy,
    prompt_task_fingerprint,
    workflow_ranking_evidence,
)


def _selection(*, effort: str | None = "high") -> SelectedRunConfiguration:
    return SelectedRunConfiguration(
        harness_id="reasoning-native",
        inference_route_id="openrouter",
        target_id="project-tools",
        model="example/fast",
        reasoning_effort=effort,
    )


def test_prompt_fingerprint_normalizes_transport_whitespace_without_exposing_text():
    left = prompt_task_fingerprint("moderate  this\n paper")
    right = prompt_task_fingerprint("moderate this paper")

    assert left == right
    assert "moderate" not in left
    assert len(left) == 64


def test_chat_operation_evidence_requires_exact_task_and_configuration():
    fingerprint = prompt_task_fingerprint("same task")
    operations = [
        {
            "task_fingerprint": fingerprint,
            "harness_id": "reasoning-native",
            "inference_route_id": "openrouter",
            "target_id": "project-tools",
            "model": "example/fast",
            "reasoning_effort": "high",
            "status": "completed",
            "duration_seconds": 2.0,
            "cost": {"amount_usd": 0.002},
        },
        {
            "task_fingerprint": prompt_task_fingerprint("another task"),
            "harness_id": "reasoning-native",
            "inference_route_id": "openrouter",
            "target_id": "project-tools",
            "model": "example/fast",
            "reasoning_effort": "high",
            "status": "completed",
            "duration_seconds": 0.1,
            "cost": {"amount_usd": 0.0001},
        },
    ]

    evidence = chat_operation_evidence(
        _selection(),
        operations,
        task_fingerprint=fingerprint,
        estimated_cost_usd=0.003,
    )

    assert evidence.observed_calls == 1
    assert evidence.successful_calls == 1
    assert evidence.median_latency_seconds == 2.0
    assert evidence.median_cost_usd == 0.002
    assert evidence.estimated_cost_usd == 0.003


def test_catalog_estimate_and_product_evidence_gates_are_explicit():
    estimate = estimate_catalog_call_cost(
        {
            "input_mtok_usd": 1.0,
            "output_mtok_usd": 2.0,
            "request_usd": 0.001,
        },
        prompt="abcd" * 4,
        assumed_output_tokens=1_000,
    )
    assert estimate == 0.003004
    assert product_routing_policy(RoutingObjective.COST, source="chat").require_cost_evidence
    assert not product_routing_policy(RoutingObjective.COST, source="chat").require_observed_cost
    assert product_routing_policy(RoutingObjective.COST, source="workflow").require_observed_cost
    assert product_routing_policy(RoutingObjective.BALANCED, source="chat").require_cost_evidence
    assert product_routing_policy(RoutingObjective.BALANCED, source="workflow").min_observed_calls == 1


def test_workflow_ranking_translation_preserves_effort_and_safety_weighting():
    evidence = workflow_ranking_evidence(
        _selection(effort="xhigh"),
        {
            "calls": 10,
            "completed_calls": 9,
            "median_cost_usd": 0.01,
            "median_latency_seconds": 4.0,
            "benchmark": {
                "cases": 20,
                "false_allow_rate": 0.0,
                "decision_accuracy": 0.8,
                "flag_f1": 0.7,
                "schema_valid_rate": 1.0,
            },
        },
        task_fingerprint="workflow-task",
    )

    assert evidence.selection.reasoning_effort == "xhigh"
    assert evidence.observed_calls == 10
    assert evidence.successful_calls == 9
    assert evidence.benchmark_cases == 20
    assert evidence.quality_score == 0.94
