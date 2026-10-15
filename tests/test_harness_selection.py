from __future__ import annotations

import pytest

from reasoning_ledger.agents import PromptFidelity
from reasoning_ledger.harnesses.contracts import (
    ExecutionTargetCapabilities,
    ExecutionTargetKind,
    HarnessCapabilities,
    HarnessRequirements,
    HarnessTransport,
    NetworkAccess,
    SessionMode,
    TargetIsolation,
    WorkspaceAccess,
)
from reasoning_ledger.harnesses.selection import (
    HarnessSelectionCatalog,
    InferenceRoute,
    SelectedRunConfiguration,
    SessionBinding,
)


def _catalog(*, route_ready: bool = True) -> HarnessSelectionCatalog:
    harness = HarnessCapabilities(
        harness_id="reference",
        display_name="Reference harness",
        adapter_version="test",
        transport=HarnessTransport.IN_PROCESS,
        prompt_fidelity=PromptFidelity.WIRE_EXACT,
        session_modes=(SessionMode.FRESH, SessionMode.RESUME),
        workspace_access=(WorkspaceAccess.NONE,),
        reasoning_efforts=("low", "high"),
        supports_interrupt=True,
        model_patterns=("example/*",),
    )
    route = InferenceRoute(
        route_id="example",
        display_name="Example provider",
        provider="example",
        harness_ids=("reference",),
        model_patterns=("example/*",),
        model_options=("example/fast", "example/deep"),
        reasoning_efforts=("low", "high"),
        ready=route_ready,
        readiness="Example credentials are missing" if not route_ready else "ready",
    )
    target = ExecutionTargetCapabilities(
        target_id="stateless",
        display_name="Stateless tools",
        kind=ExecutionTargetKind.STATELESS,
        isolation=TargetIsolation.PROCESS,
        workspace_access=(WorkspaceAccess.NONE,),
        network_access=(NetworkAccess.DENIED,),
    )
    return HarnessSelectionCatalog((harness,), (route,), (target,))


def test_selection_preflight_accepts_independent_compatible_choices():
    result = _catalog().preflight(
        SelectedRunConfiguration(
            harness_id="reference",
            inference_route_id="example",
            target_id="stateless",
            model="example/deep",
            reasoning_effort="high",
        ),
        HarnessRequirements(
            session_mode=SessionMode.RESUME,
            requires_interrupt=True,
        ),
    )

    assert result.compatible is True
    assert result.issues == ()
    assert result.route is not None and result.route.provider == "example"


def test_selection_preflight_reports_route_target_model_and_reasoning_issues():
    result = _catalog(route_ready=False).preflight(
        SelectedRunConfiguration(
            harness_id="reference",
            inference_route_id="example",
            target_id="stateless",
            model="other/model",
            reasoning_effort="medium",
        ),
        HarnessRequirements(),
    )

    assert result.compatible is False
    assert {issue.code for issue in result.issues} == {
        "route_unavailable",
        "route_model",
        "harness_model",
        "route_reasoning_effort",
        "reasoning_effort",
    }
    assert next(issue.detail for issue in result.issues if issue.code == "route_unavailable") == "Example credentials are missing"


def test_selection_preflight_fails_closed_for_unknown_identifiers():
    result = _catalog().preflight(
        SelectedRunConfiguration(
            harness_id="missing",
            inference_route_id="absent",
            target_id="nowhere",
            model="example/deep",
        ),
        HarnessRequirements(),
    )

    assert result.compatible is False
    assert {issue.scope for issue in result.issues} == {
        "harness",
        "route",
        "target",
    }


def test_catalog_rejects_routes_bound_to_unregistered_harnesses():
    route = InferenceRoute(
        route_id="example",
        display_name="Example",
        provider="example",
        harness_ids=("missing",),
    )

    with pytest.raises(ValueError, match="unknown harnesses: missing"):
        HarnessSelectionCatalog((), (route,), ())


def test_persistent_session_binding_rejects_silent_selection_changes():
    result = _catalog().preflight(
        SelectedRunConfiguration(
            harness_id="reference",
            inference_route_id="example",
            target_id="stateless",
            model="example/fast",
            reasoning_effort="low",
        ),
        HarnessRequirements(),
        binding=SessionBinding(
            harness_id="reference",
            provider="example",
            target_id="stateless",
            model="example/deep",
        ),
    )

    assert result.compatible is False
    assert [issue.code for issue in result.issues] == ["session_model"]
    assert "start a new chat" in result.issues[0].detail


def test_session_binding_rejects_blank_pinned_values():
    with pytest.raises(ValueError, match="model is required"):
        SessionBinding(
            harness_id="reference",
            provider="example",
            target_id="stateless",
            model=" ",
        )
