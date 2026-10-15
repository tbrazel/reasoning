from __future__ import annotations

import pytest

from reasoning_ledger.agents import PromptFidelity
from reasoning_ledger.harnesses import (
    AutoRoutingPolicy,
    ExecutionTargetCapabilities,
    ExecutionTargetKind,
    HarnessCapabilities,
    HarnessRequirements,
    HarnessSelectionCatalog,
    HarnessTransport,
    InferenceRoute,
    RoutingEvidence,
    RoutingObjective,
    SelectedRunConfiguration,
    SessionBinding,
    TargetIsolation,
    route_automatically,
)


def _catalog(*, second_ready: bool = True) -> HarnessSelectionCatalog:
    harnesses = tuple(
        HarnessCapabilities(
            harness_id=harness_id,
            display_name=harness_id,
            adapter_version="1",
            transport=HarnessTransport.REMOTE_SERVICE,
            prompt_fidelity=PromptFidelity.WIRE_EXACT,
            model_patterns=(f"{provider}/*",),
        )
        for harness_id, provider in (("alpha", "provider-a"), ("beta", "provider-b"))
    )
    routes = (
        InferenceRoute(
            route_id="route-a",
            display_name="Route A",
            provider="provider-a",
            harness_ids=("alpha",),
            model_patterns=("provider-a/*",),
        ),
        InferenceRoute(
            route_id="route-b",
            display_name="Route B",
            provider="provider-b",
            harness_ids=("beta",),
            model_patterns=("provider-b/*",),
            ready=second_ready,
            readiness="Route B is unavailable" if not second_ready else "ready",
        ),
    )
    target = ExecutionTargetCapabilities(
        target_id="stateless",
        display_name="Stateless",
        kind=ExecutionTargetKind.STATELESS,
        isolation=TargetIsolation.PROCESS,
    )
    return HarnessSelectionCatalog(harnesses, routes, (target,))


def _selection(harness: str, route: str, model: str) -> SelectedRunConfiguration:
    return SelectedRunConfiguration(harness, route, "stateless", model)


def test_auto_router_uses_task_evidence_and_exposes_every_score_component():
    expensive_quality = RoutingEvidence(
        selection=_selection("alpha", "route-a", "provider-a/deep"),
        task_fingerprint="paper-moderation-v1",
        observed_calls=20,
        successful_calls=20,
        benchmark_cases=100,
        quality_score=0.98,
        median_cost_usd=0.08,
        median_latency_seconds=18,
    )
    cheap_quality = RoutingEvidence(
        selection=_selection("beta", "route-b", "provider-b/fast"),
        task_fingerprint="paper-moderation-v1",
        observed_calls=20,
        successful_calls=19,
        benchmark_cases=100,
        quality_score=0.92,
        median_cost_usd=0.002,
        median_latency_seconds=2,
    )

    decision = route_automatically(
        _catalog(),
        HarnessRequirements(),
        (expensive_quality, cheap_quality),
        policy=AutoRoutingPolicy(objective=RoutingObjective.BALANCED),
        task_fingerprint="paper-moderation-v1",
    )

    assert decision.selected == cheap_quality.selection
    assert decision.routed is True
    selected = next(
        item for item in decision.assessments if item.selection == decision.selected
    )
    assert selected.eligible is True
    assert {component.name for component in selected.components} == {
        "quality",
        "reliability",
        "cost",
        "latency",
        "evidence",
    }
    assert sum(component.contribution for component in selected.components) == pytest.approx(
        selected.score
    )


def test_auto_router_applies_live_preflight_before_scoring():
    unavailable = RoutingEvidence(
        selection=_selection("beta", "route-b", "provider-b/fast"),
        task_fingerprint="task",
        observed_calls=100,
        successful_calls=100,
        quality_score=1,
    )
    available = RoutingEvidence(
        selection=_selection("alpha", "route-a", "provider-a/deep"),
        task_fingerprint="task",
    )

    decision = route_automatically(
        _catalog(second_ready=False),
        HarnessRequirements(),
        (unavailable, available),
        task_fingerprint="task",
    )

    assert decision.selected == available.selection
    rejected = decision.assessments[0]
    assert rejected.eligible is False
    assert "route_unavailable" in {issue.code for issue in rejected.issues}


def test_auto_router_ignores_observations_from_another_task():
    mismatch = RoutingEvidence(
        selection=_selection("alpha", "route-a", "provider-a/deep"),
        task_fingerprint="another-task",
        observed_calls=50,
        successful_calls=50,
        benchmark_cases=50,
        quality_score=1,
        median_cost_usd=0.001,
        estimated_cost_usd=0.03,
        median_latency_seconds=1,
    )

    decision = route_automatically(
        _catalog(),
        HarnessRequirements(),
        (mismatch,),
        task_fingerprint="this-task",
    )

    assessment = decision.assessments[0]
    components = {item.name: item for item in assessment.components}
    assert components["quality"].value == 0
    assert components["reliability"].value == 0
    assert components["cost"].source == "catalog estimate"
    assert "ignored" in assessment.explanations[0]


def test_auto_router_fails_closed_when_policy_requires_missing_evidence():
    candidate = RoutingEvidence(
        selection=_selection("alpha", "route-a", "provider-a/deep"),
        estimated_cost_usd=0.01,
    )
    policy = AutoRoutingPolicy(
        min_observed_calls=2,
        min_benchmark_cases=10,
        require_quality_evidence=True,
        require_cost_evidence=True,
        require_observed_cost=True,
        require_observed_latency=True,
        min_success_rate=0.8,
    )

    decision = route_automatically(
        _catalog(),
        HarnessRequirements(),
        (candidate,),
        policy=policy,
    )

    assert decision.selected is None
    assert {issue.code for issue in decision.assessments[0].issues} == {
        "policy_observations",
        "policy_benchmark_cases",
        "policy_quality",
        "policy_reliability",
        "policy_observed_cost",
        "policy_observed_latency",
    }


def test_auto_router_respects_session_binding_and_stable_tie_breaking():
    alpha = RoutingEvidence(
        selection=_selection("alpha", "route-a", "provider-a/deep"),
    )
    beta = RoutingEvidence(
        selection=_selection("beta", "route-b", "provider-b/fast"),
    )
    binding = SessionBinding(
        harness_id="beta",
        provider="provider-b",
        target_id="stateless",
        model="provider-b/fast",
    )

    bound = route_automatically(
        _catalog(),
        HarnessRequirements(),
        (alpha, beta),
        binding=binding,
    )
    tied = route_automatically(
        _catalog(),
        HarnessRequirements(),
        (beta, alpha),
    )

    assert bound.selected == beta.selection
    assert tied.selected == alpha.selection


def test_auto_router_rejects_duplicate_candidates_and_invalid_evidence():
    candidate = RoutingEvidence(
        selection=_selection("alpha", "route-a", "provider-a/deep"),
    )
    with pytest.raises(ValueError, match="duplicate selections"):
        route_automatically(
            _catalog(),
            HarnessRequirements(),
            (candidate, candidate),
        )
    with pytest.raises(ValueError, match="successful_calls"):
        RoutingEvidence(
            selection=candidate.selection,
            observed_calls=1,
            successful_calls=2,
        )
