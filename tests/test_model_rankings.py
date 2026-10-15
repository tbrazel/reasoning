from copy import deepcopy

from reasoning_ledger.agents.model_rankings import (
    aggregate_workflow_model_rankings,
    workflow_task_fingerprint,
)


def _config(model: str) -> dict:
    return {
        "executor_id": "reasoning-native",
        "model_provider": None,
        "model": model,
        "effort": "high",
        "system_prompt": {"prompt_id": "role", "revision_id": "role-1"},
        "user_prompt": {"prompt_id": "question", "revision_id": "question-1"},
        "tools": [],
        "skills": [],
        "budget": {"max_cost_usd": 1, "timeout_seconds": 120},
    }


def _workflow(models: list[str]) -> dict:
    base = _config(models[0])
    return {
        "workflow_id": "compare",
        "revision_id": "revision-current",
        "workflow_type": "model_comparison",
        "presentation": {"recipe": "model_comparison"},
        "nodes": [
            {"id": "input", "kind": "input", "name": "Question"},
            {"id": "agent", "kind": "agent", "name": "Compared model", "config": base},
            {"id": "output", "kind": "output", "name": "Responses"},
        ],
        "edges": [
            {"id": "in-agent", "source": "input", "target": "agent"},
            {"id": "agent-out", "source": "agent", "target": "output"},
        ],
        "variants": [
            {
                "variant_id": f"variant-{index}",
                "name": f"Model {index}",
                "node_overrides": {"agent": _config(model)},
            }
            for index, model in enumerate(models)
        ],
        "evaluators": [{"evaluator_id": "paper-moderation"}],
    }


def _output(model: str, *, status: str, latency: float, cost: float | None) -> dict:
    value = {
        "node_id": "agent",
        "variant_id": f"variant-{0 if model.endswith('a') else 1}",
        "executor_id": "reasoning-native",
        "model_provider": None,
        "model": model,
        "status": status,
        "duration_seconds": latency,
        "serving_provider": "Route A",
    }
    if cost is not None:
        value["cost"] = {"amount_usd": cost, "basis": "provider_reported"}
    return value


def test_task_fingerprint_ignores_candidates_but_not_prompt_contracts():
    first = _workflow(["publisher/model-a", "publisher/model-b"])
    changed_roster = _workflow(["other/model-c", "third/model-d", "fourth/model-e"])
    assert workflow_task_fingerprint(first) == workflow_task_fingerprint(changed_roster)

    changed_prompt = deepcopy(first)
    changed_prompt["nodes"][1]["config"]["user_prompt"]["revision_id"] = "question-2"
    assert workflow_task_fingerprint(first) != workflow_task_fingerprint(changed_prompt)


def test_aggregation_separates_catalog_metadata_from_observed_task_evidence():
    workflow = _workflow(["publisher/model-a", "other/model-b"])
    run = {
        "workflow_snapshot": deepcopy(workflow),
        "created_at": "2026-08-28T00:00:00+00:00",
        "outputs": {
            "nodes": [
                _output("publisher/model-a", status="completed", latency=2.0, cost=0.02),
                _output("other/model-b", status="completed", latency=1.0, cost=0.01),
            ],
            "benchmark": {
                "models": [
                    {
                        "executor_id": "reasoning-native",
                        "model_provider": None,
                        "model": "publisher/model-a",
                        "cases": 4,
                        "decision_accuracy": 1.0,
                        "flag_f1": 1.0,
                        "schema_valid_rate": 1.0,
                        "false_allow_count": 0,
                        "case_results": [
                            {"expected": {"decision": "reject"}},
                            {"expected": {"decision": "allow"}},
                            {"expected": {"decision": "review"}},
                            {"expected": {"decision": "allow"}},
                        ],
                    },
                    {
                        "executor_id": "reasoning-native",
                        "model_provider": None,
                        "model": "other/model-b",
                        "cases": 4,
                        "decision_accuracy": 0.75,
                        "flag_f1": 0.8,
                        "schema_valid_rate": 1.0,
                        "false_allow_count": 1,
                        "case_results": [
                            {"expected": {"decision": "reject"}},
                            {"expected": {"decision": "allow"}},
                            {"expected": {"decision": "review"}},
                            {"expected": {"decision": "allow"}},
                        ],
                    },
                ]
            },
        },
    }
    incompatible = deepcopy(run)
    incompatible["workflow_snapshot"]["nodes"][1]["config"]["user_prompt"]["revision_id"] = "question-2"

    result = aggregate_workflow_model_rankings(workflow, [run, incompatible])

    assert result["compatible_runs"] == 1
    assert result["total_runs"] == 2
    by_model = {item["model"]: item for item in result["configurations"]}
    assert by_model["publisher/model-a"]["success_rate"] == 1
    assert by_model["other/model-b"]["median_latency_seconds"] == 1
    assert by_model["other/model-b"]["benchmark"]["false_allow_rate"] == 0.5
    modes = {mode["mode_id"]: mode for mode in result["modes"]}
    assert modes["best_quality"]["model_ids"] == ["publisher/model-a", "other/model-b"]
    assert modes["fastest_reliable"]["model_ids"] == ["other/model-b", "publisher/model-a"]
    assert modes["diverse"]["available"] is True


def test_rankings_keep_reasoning_effort_as_part_of_the_configuration():
    workflow = _workflow(["publisher/model-a", "other/model-b"])
    high_snapshot = deepcopy(workflow)
    low_snapshot = deepcopy(workflow)
    low_snapshot["nodes"][1]["config"]["effort"] = "low"
    low_snapshot["variants"][0]["node_overrides"]["agent"]["effort"] = "low"
    high_run = {
        "workflow_snapshot": high_snapshot,
        "created_at": "2026-08-28T00:00:00+00:00",
        "outputs": {
            "nodes": [_output("publisher/model-a", status="completed", latency=2.0, cost=0.02)],
        },
    }
    low_run = {
        "workflow_snapshot": low_snapshot,
        "created_at": "2026-08-28T01:00:00+00:00",
        "outputs": {
            "nodes": [_output("publisher/model-a", status="completed", latency=1.0, cost=0.01)],
        },
    }

    result = aggregate_workflow_model_rankings(workflow, [high_run, low_run])

    assert {item["effort"] for item in result["configurations"]} == {"high", "low"}
    fastest = next(mode for mode in result["modes"] if mode["mode_id"] == "fastest_reliable")
    assert len(fastest["configuration_ids"]) == 2
    assert fastest["model_ids"] == ["publisher/model-a", "publisher/model-a"]
