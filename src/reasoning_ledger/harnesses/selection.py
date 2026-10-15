"""Product-facing selection and preflight for independent agent controls.

The durable harness contract deliberately keeps the agent loop, inference
route, and execution target separate.  This module turns those independent
records into one selected run configuration without teaching the web layer
how to infer capabilities from harness or model names.

Availability is deliberately transient: it participates in live preflight
but is not part of a checkpoint or workflow revision.  Durable execution
records pin the selected identifiers and the capability snapshots supplied by
the harness session contract.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable
from dataclasses import dataclass, field

from .contracts import (
    ExecutionTargetCapabilities,
    HarnessCapabilities,
    HarnessPreflightIssue,
    HarnessRequirements,
    preflight_harness,
)


@dataclass(frozen=True)
class InferenceRoute:
    """One credential-free provider/model route exposed by a deployment."""

    route_id: str
    display_name: str
    provider: str
    harness_ids: tuple[str, ...]
    model_patterns: tuple[str, ...] = ("*",)
    model_options: tuple[str, ...] = ()
    reasoning_efforts: tuple[str, ...] = ()
    ready: bool = True
    readiness: str = "ready"

    def __post_init__(self) -> None:
        if not self.route_id.strip():
            raise ValueError("route_id is required")
        if not self.display_name.strip():
            raise ValueError("route display_name is required")
        if not self.provider.strip():
            raise ValueError("route provider is required")
        if not self.harness_ids:
            raise ValueError("an inference route must name at least one harness")
        if len(self.harness_ids) != len(set(self.harness_ids)):
            raise ValueError("route harness_ids cannot contain duplicates")
        if not self.model_patterns:
            raise ValueError("route model_patterns cannot be empty")

    def supports_model(self, model: str) -> bool:
        return any(fnmatch.fnmatchcase(model, pattern) for pattern in self.model_patterns)


@dataclass(frozen=True)
class SelectedRunConfiguration:
    """The four independent choices needed to start an agent session."""

    harness_id: str
    inference_route_id: str
    target_id: str
    model: str
    reasoning_effort: str | None = None

    def __post_init__(self) -> None:
        for label, value in (
            ("harness_id", self.harness_id),
            ("inference_route_id", self.inference_route_id),
            ("target_id", self.target_id),
            ("model", self.model),
        ):
            if not value.strip():
                raise ValueError(f"{label} is required")


@dataclass(frozen=True)
class SessionBinding:
    """The immutable choices already pinned by a persistent chat session."""

    harness_id: str
    provider: str | None
    target_id: str
    model: str

    def __post_init__(self) -> None:
        for label, value in (
            ("harness_id", self.harness_id),
            ("target_id", self.target_id),
            ("model", self.model),
        ):
            if not value.strip():
                raise ValueError(f"{label} is required")
        if self.provider is not None and not self.provider.strip():
            raise ValueError("provider cannot be blank")


@dataclass(frozen=True)
class SelectedRunPreflight:
    """Live compatibility result for a selected run configuration."""

    compatible: bool
    issues: tuple[HarnessPreflightIssue, ...] = ()
    harness: HarnessCapabilities | None = None
    route: InferenceRoute | None = None
    target: ExecutionTargetCapabilities | None = None


@dataclass(frozen=True)
class HarnessSelectionCatalog:
    """A deployment's advertised harnesses, routes, and execution targets."""

    harnesses: tuple[HarnessCapabilities, ...]
    routes: tuple[InferenceRoute, ...]
    targets: tuple[ExecutionTargetCapabilities, ...]
    _harnesses: dict[str, HarnessCapabilities] = field(init=False, repr=False)
    _routes: dict[str, InferenceRoute] = field(init=False, repr=False)
    _targets: dict[str, ExecutionTargetCapabilities] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_harnesses", _index(self.harnesses, "harness_id", "harness"))
        object.__setattr__(self, "_routes", _index(self.routes, "route_id", "inference route"))
        object.__setattr__(self, "_targets", _index(self.targets, "target_id", "execution target"))
        unknown = {harness_id for route in self.routes for harness_id in route.harness_ids if harness_id not in self._harnesses}
        if unknown:
            raise ValueError("inference routes reference unknown harnesses: " + ", ".join(sorted(unknown)))

    def preflight(
        self,
        selection: SelectedRunConfiguration,
        requirements: HarnessRequirements,
        *,
        binding: SessionBinding | None = None,
    ) -> SelectedRunPreflight:
        issues: list[HarnessPreflightIssue] = []
        harness = self._harnesses.get(selection.harness_id)
        route = self._routes.get(selection.inference_route_id)
        target = self._targets.get(selection.target_id)

        if harness is None:
            issues.append(
                HarnessPreflightIssue(
                    code="unknown_harness",
                    scope="harness",
                    detail=f"Unknown harness: {selection.harness_id}",
                )
            )
        if route is None:
            issues.append(
                HarnessPreflightIssue(
                    code="unknown_inference_route",
                    scope="route",
                    detail=f"Unknown inference route: {selection.inference_route_id}",
                )
            )
        if target is None:
            issues.append(
                HarnessPreflightIssue(
                    code="unknown_execution_target",
                    scope="target",
                    detail=f"Unknown execution target: {selection.target_id}",
                )
            )
        if issues:
            return SelectedRunPreflight(False, tuple(issues), harness, route, target)

        assert harness is not None and route is not None and target is not None
        if not route.ready:
            issues.append(
                HarnessPreflightIssue(
                    code="route_unavailable",
                    scope="route",
                    detail=route.readiness,
                )
            )
        if harness.harness_id not in route.harness_ids:
            issues.append(
                HarnessPreflightIssue(
                    code="route_harness",
                    scope="route",
                    detail=(f"{route.display_name} is not available through {harness.display_name}"),
                )
            )
        if not route.supports_model(selection.model):
            issues.append(
                HarnessPreflightIssue(
                    code="route_model",
                    scope="route",
                    detail=(f"{selection.model} is not advertised by {route.display_name}"),
                )
            )
        if not any(fnmatch.fnmatchcase(selection.model, pattern) for pattern in harness.model_patterns):
            issues.append(
                HarnessPreflightIssue(
                    code="harness_model",
                    scope="harness",
                    detail=(f"{harness.display_name} does not advertise support for {selection.model}"),
                )
            )
        if selection.reasoning_effort is not None and route.reasoning_efforts and selection.reasoning_effort not in route.reasoning_efforts:
            issues.append(
                HarnessPreflightIssue(
                    code="route_reasoning_effort",
                    scope="route",
                    detail=(f"{route.display_name} does not advertise {selection.reasoning_effort} reasoning"),
                )
            )

        effective_requirements = HarnessRequirements(
            **{
                **requirements.__dict__,
                "reasoning_effort": selection.reasoning_effort,
            }
        )
        issues.extend(preflight_harness(harness, target, effective_requirements).issues)
        if binding is not None:
            bound_values = (
                (
                    "session_harness",
                    "harness",
                    selection.harness_id,
                    binding.harness_id,
                    "harness",
                ),
                (
                    "session_provider",
                    "route",
                    route.provider,
                    binding.provider,
                    "provider",
                ),
                (
                    "session_target",
                    "target",
                    selection.target_id,
                    binding.target_id,
                    "execution target",
                ),
                (
                    "session_model",
                    "route",
                    selection.model,
                    binding.model,
                    "model",
                ),
            )
            for code, scope, selected, pinned, label in bound_values:
                if selected != pinned:
                    issues.append(
                        HarnessPreflightIssue(
                            code=code,
                            scope=scope,
                            detail=(f"This persistent chat is pinned to {label} {pinned}; start a new chat to use {selected}."),
                        )
                    )
        return SelectedRunPreflight(
            compatible=not issues,
            issues=tuple(issues),
            harness=harness,
            route=route,
            target=target,
        )


def _index(values: Iterable[object], attribute: str, label: str) -> dict[str, object]:
    result: dict[str, object] = {}
    for value in values:
        key = str(getattr(value, attribute))
        if key in result:
            raise ValueError(f"duplicate {label}: {key}")
        result[key] = value
    return result
