"""Deterministic, explainable routing across preflighted run configurations.

The router never discovers models, reads credentials, or guesses capabilities
from names.  Its caller supplies a finite candidate set from the live
``HarnessSelectionCatalog`` plus task-scoped evidence and catalog estimates.
Compatibility and policy are hard gates; the remaining candidates receive a
small, inspectable utility score with stable tie-breaking.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from .contracts import HarnessPreflightIssue, HarnessRequirements
from .selection import (
    HarnessSelectionCatalog,
    SelectedRunConfiguration,
    SessionBinding,
)


class RoutingObjective(StrEnum):
    BALANCED = "balanced"
    QUALITY = "quality"
    RELIABILITY = "reliability"
    COST = "cost"
    LATENCY = "latency"


@dataclass(frozen=True)
class RoutingEvidence:
    """Task-scoped observations and non-benchmark estimates for one choice."""

    selection: SelectedRunConfiguration
    task_fingerprint: str | None = None
    observed_calls: int = 0
    successful_calls: int = 0
    benchmark_cases: int = 0
    quality_score: float | None = None
    median_cost_usd: float | None = None
    estimated_cost_usd: float | None = None
    median_latency_seconds: float | None = None

    def __post_init__(self) -> None:
        for label, value in (
            ("observed_calls", self.observed_calls),
            ("successful_calls", self.successful_calls),
            ("benchmark_cases", self.benchmark_cases),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{label} must be a non-negative integer")
        if self.successful_calls > self.observed_calls:
            raise ValueError("successful_calls cannot exceed observed_calls")
        if self.quality_score is not None:
            _validate_unit(self.quality_score, "quality_score")
        for label, value in (
            ("median_cost_usd", self.median_cost_usd),
            ("estimated_cost_usd", self.estimated_cost_usd),
            ("median_latency_seconds", self.median_latency_seconds),
        ):
            if value is not None and (not math.isfinite(value) or value < 0):
                raise ValueError(f"{label} must be finite and non-negative")

    @property
    def success_rate(self) -> float | None:
        if self.observed_calls == 0:
            return None
        return self.successful_calls / self.observed_calls


@dataclass(frozen=True)
class AutoRoutingPolicy:
    """Hard policy gates and the operator's declared optimization objective."""

    objective: RoutingObjective = RoutingObjective.BALANCED
    allowed_harness_ids: tuple[str, ...] = ()
    allowed_providers: tuple[str, ...] = ()
    allowed_target_ids: tuple[str, ...] = ()
    max_cost_usd: float | None = None
    max_latency_seconds: float | None = None
    min_success_rate: float | None = None
    min_observed_calls: int = 0
    min_benchmark_cases: int = 0
    require_quality_evidence: bool = False
    require_cost_evidence: bool = False
    require_observed_cost: bool = False
    require_observed_latency: bool = False
    cost_scale_usd: float = 0.01
    latency_scale_seconds: float = 10.0
    evidence_scale_calls: int = 20

    def __post_init__(self) -> None:
        for label, values in (
            ("allowed_harness_ids", self.allowed_harness_ids),
            ("allowed_providers", self.allowed_providers),
            ("allowed_target_ids", self.allowed_target_ids),
        ):
            if len(values) != len(set(values)) or any(not value.strip() for value in values):
                raise ValueError(f"{label} must contain unique non-empty values")
        for label, value in (
            ("max_cost_usd", self.max_cost_usd),
            ("max_latency_seconds", self.max_latency_seconds),
        ):
            if value is not None and (not math.isfinite(value) or value < 0):
                raise ValueError(f"{label} must be finite and non-negative")
        if self.min_success_rate is not None:
            _validate_unit(self.min_success_rate, "min_success_rate")
        for label, value in (
            ("min_observed_calls", self.min_observed_calls),
            ("min_benchmark_cases", self.min_benchmark_cases),
            ("evidence_scale_calls", self.evidence_scale_calls),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{label} must be a non-negative integer")
        if self.evidence_scale_calls < 1:
            raise ValueError("evidence_scale_calls must be positive")
        for label, value in (
            ("cost_scale_usd", self.cost_scale_usd),
            ("latency_scale_seconds", self.latency_scale_seconds),
        ):
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{label} must be finite and positive")


@dataclass(frozen=True)
class RoutingScoreComponent:
    name: str
    value: float
    weight: float
    contribution: float
    source: str


@dataclass(frozen=True)
class RoutingAssessment:
    selection: SelectedRunConfiguration
    eligible: bool
    score: float | None
    components: tuple[RoutingScoreComponent, ...] = ()
    issues: tuple[HarnessPreflightIssue, ...] = ()
    explanations: tuple[str, ...] = ()


@dataclass(frozen=True)
class AutoRoutingDecision:
    selected: SelectedRunConfiguration | None
    objective: RoutingObjective
    task_fingerprint: str | None
    assessments: tuple[RoutingAssessment, ...]

    @property
    def routed(self) -> bool:
        return self.selected is not None


_WEIGHTS = {
    RoutingObjective.BALANCED: {
        "quality": 0.35,
        "reliability": 0.25,
        "cost": 0.20,
        "latency": 0.15,
        "evidence": 0.05,
    },
    RoutingObjective.QUALITY: {
        "quality": 0.60,
        "reliability": 0.20,
        "cost": 0.05,
        "latency": 0.05,
        "evidence": 0.10,
    },
    RoutingObjective.RELIABILITY: {
        "quality": 0.15,
        "reliability": 0.60,
        "cost": 0.05,
        "latency": 0.05,
        "evidence": 0.15,
    },
    RoutingObjective.COST: {
        "quality": 0.10,
        "reliability": 0.20,
        "cost": 0.55,
        "latency": 0.05,
        "evidence": 0.10,
    },
    RoutingObjective.LATENCY: {
        "quality": 0.10,
        "reliability": 0.20,
        "cost": 0.05,
        "latency": 0.55,
        "evidence": 0.10,
    },
}


def route_automatically(
    catalog: HarnessSelectionCatalog,
    requirements: HarnessRequirements,
    candidates: tuple[RoutingEvidence, ...],
    *,
    policy: AutoRoutingPolicy | None = None,
    task_fingerprint: str | None = None,
    binding: SessionBinding | None = None,
) -> AutoRoutingDecision:
    """Rank a finite candidate set after fail-closed preflight and policy.

    Evidence with another task fingerprint is deliberately ignored.  Catalog
    estimates may influence cost policy and scoring, but are labeled separately
    and never become quality evidence.
    """

    policy = policy or AutoRoutingPolicy()
    if not candidates:
        return AutoRoutingDecision(None, policy.objective, task_fingerprint, ())
    identities = [_selection_key(candidate.selection) for candidate in candidates]
    if len(identities) != len(set(identities)):
        raise ValueError("auto-routing candidates cannot contain duplicate selections")

    assessments = tuple(
        _assess(
            catalog,
            requirements,
            evidence,
            policy,
            task_fingerprint=task_fingerprint,
            binding=binding,
        )
        for evidence in candidates
    )
    eligible = [item for item in assessments if item.eligible and item.score is not None]
    selected = None
    if eligible:
        winner = min(
            eligible,
            key=lambda item: (
                -float(item.score),
                _selection_key(item.selection),
            ),
        )
        selected = winner.selection
    return AutoRoutingDecision(
        selected=selected,
        objective=policy.objective,
        task_fingerprint=task_fingerprint,
        assessments=assessments,
    )


def _assess(
    catalog: HarnessSelectionCatalog,
    requirements: HarnessRequirements,
    supplied: RoutingEvidence,
    policy: AutoRoutingPolicy,
    *,
    task_fingerprint: str | None,
    binding: SessionBinding | None,
) -> RoutingAssessment:
    selection = supplied.selection
    preflight = catalog.preflight(selection, requirements, binding=binding)
    issues = list(preflight.issues)
    explanations: list[str] = []
    route = preflight.route

    if policy.allowed_harness_ids and selection.harness_id not in policy.allowed_harness_ids:
        issues.append(_policy_issue("policy_harness", f"Harness {selection.harness_id} is not allowed by policy"))
    if route is not None and policy.allowed_providers and route.provider not in policy.allowed_providers:
        issues.append(_policy_issue("policy_provider", f"Provider {route.provider} is not allowed by policy"))
    if policy.allowed_target_ids and selection.target_id not in policy.allowed_target_ids:
        issues.append(_policy_issue("policy_target", f"Target {selection.target_id} is not allowed by policy"))

    evidence = supplied
    if (
        task_fingerprint is not None
        and supplied.task_fingerprint != task_fingerprint
    ):
        evidence = RoutingEvidence(
            selection=selection,
            estimated_cost_usd=supplied.estimated_cost_usd,
        )
        explanations.append("Task-mismatched observations were ignored; only the catalog cost estimate remains.")

    success_rate = evidence.success_rate
    observed_cost = evidence.median_cost_usd
    effective_cost = observed_cost if observed_cost is not None else evidence.estimated_cost_usd
    if observed_cost is not None:
        cost_source = "observed median"
    elif evidence.estimated_cost_usd is not None:
        cost_source = "catalog estimate"
    else:
        cost_source = "unavailable"

    if evidence.observed_calls < policy.min_observed_calls:
        issues.append(
            _policy_issue(
                "policy_observations",
                f"Policy requires at least {policy.min_observed_calls} task observations",
            )
        )
    if evidence.benchmark_cases < policy.min_benchmark_cases:
        issues.append(
            _policy_issue(
                "policy_benchmark_cases",
                f"Policy requires at least {policy.min_benchmark_cases} benchmark cases",
            )
        )
    if policy.require_quality_evidence and evidence.quality_score is None:
        issues.append(_policy_issue("policy_quality", "Policy requires task-specific quality evidence"))
    if policy.require_cost_evidence and effective_cost is None:
        issues.append(_policy_issue("policy_cost_evidence", "Policy requires observed or catalog cost evidence"))
    if policy.min_success_rate is not None and (
        success_rate is None or success_rate < policy.min_success_rate
    ):
        issues.append(
            _policy_issue(
                "policy_reliability",
                f"Policy requires an observed success rate of at least {policy.min_success_rate:.3f}",
            )
        )
    if policy.require_observed_cost and observed_cost is None:
        issues.append(_policy_issue("policy_observed_cost", "Policy requires observed cost evidence"))
    if policy.max_cost_usd is not None and (
        effective_cost is None or effective_cost > policy.max_cost_usd
    ):
        issues.append(
            _policy_issue(
                "policy_cost",
                f"Policy requires a known cost no greater than ${policy.max_cost_usd:g}",
            )
        )
    latency = evidence.median_latency_seconds
    if policy.require_observed_latency and latency is None:
        issues.append(_policy_issue("policy_observed_latency", "Policy requires observed latency evidence"))
    if policy.max_latency_seconds is not None and (
        latency is None or latency > policy.max_latency_seconds
    ):
        issues.append(
            _policy_issue(
                "policy_latency",
                f"Policy requires known median latency no greater than {policy.max_latency_seconds:g} seconds",
            )
        )

    if issues:
        return RoutingAssessment(
            selection=selection,
            eligible=False,
            score=None,
            issues=tuple(issues),
            explanations=tuple(explanations),
        )

    quality = evidence.quality_score or 0.0
    reliability = success_rate or 0.0
    cost = _inverse_utility(effective_cost, policy.cost_scale_usd)
    latency_utility = _inverse_utility(latency, policy.latency_scale_seconds)
    evidence_utility = min(1.0, evidence.observed_calls / policy.evidence_scale_calls)
    values = {
        "quality": (quality, "task benchmark" if evidence.quality_score is not None else "unavailable"),
        "reliability": (reliability, "task observations" if success_rate is not None else "unavailable"),
        "cost": (cost, cost_source),
        "latency": (latency_utility, "observed median" if latency is not None else "unavailable"),
        "evidence": (evidence_utility, f"{evidence.observed_calls} task observations"),
    }
    components = tuple(
        RoutingScoreComponent(
            name=name,
            value=value,
            weight=_WEIGHTS[policy.objective][name],
            contribution=value * _WEIGHTS[policy.objective][name],
            source=source,
        )
        for name, (value, source) in values.items()
    )
    score = sum(item.contribution for item in components)
    explanations.append(
        f"Eligible under live preflight and policy; {policy.objective.value} utility is {score:.6f}."
    )
    return RoutingAssessment(
        selection=selection,
        eligible=True,
        score=score,
        components=components,
        explanations=tuple(explanations),
    )


def _policy_issue(code: str, detail: str) -> HarnessPreflightIssue:
    return HarnessPreflightIssue(code=code, scope="policy", detail=detail)


def _inverse_utility(value: float | None, scale: float) -> float:
    if value is None:
        return 0.0
    return 1.0 / (1.0 + value / scale)


def _selection_key(selection: SelectedRunConfiguration) -> tuple[str, ...]:
    return (
        selection.harness_id,
        selection.inference_route_id,
        selection.target_id,
        selection.model,
        selection.reasoning_effort or "",
    )


def _validate_unit(value: float, label: str) -> None:
    if not math.isfinite(value) or value < 0 or value > 1:
        raise ValueError(f"{label} must be between zero and one")
