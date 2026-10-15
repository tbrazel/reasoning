from __future__ import annotations

from datetime import UTC, datetime

import pytest

from reasoning_ledger.agents import ExecutorCapabilities, PromptFidelity
from reasoning_ledger.harnesses import (
    ExecutionTargetCapabilities,
    ExecutionTargetKind,
    HarnessCapabilities,
    HarnessEvent,
    HarnessEventKind,
    HarnessRequirements,
    HarnessSessionSpec,
    HarnessTransport,
    HarnessTurnResult,
    NetworkAccess,
    ReasoningVisibility,
    SessionMode,
    TargetIsolation,
    WorkspaceAccess,
    codex_app_server_capabilities,
    legacy_executor_capabilities,
    preflight_harness,
    reasoning_native_capabilities,
    validate_harness_trace,
)


def _codex_harness() -> HarnessCapabilities:
    return codex_app_server_capabilities()


def _local_sandbox() -> ExecutionTargetCapabilities:
    return ExecutionTargetCapabilities(
        target_id="local-sandbox",
        display_name="Local OS sandbox",
        kind=ExecutionTargetKind.LOCAL_SANDBOX,
        isolation=TargetIsolation.OS_SANDBOX,
        workspace_access=(WorkspaceAccess.NONE, WorkspaceAccess.READ_ONLY, WorkspaceAccess.READ_WRITE),
        network_access=(NetworkAccess.DENIED, NetworkAccess.UNRESTRICTED),
        supports_processes=True,
        durable_workspace=True,
    )


def test_preflight_accepts_explicit_local_development_capabilities():
    result = preflight_harness(
        _codex_harness(),
        _local_sandbox(),
        HarnessRequirements(
            session_mode=SessionMode.RESUME,
            workspace_access=WorkspaceAccess.READ_WRITE,
            minimum_isolation=TargetIsolation.OS_SANDBOX,
            reasoning_effort="high",
            reasoning_visibility=ReasoningVisibility.SUMMARY,
            requires_tools=True,
            requires_mcp=True,
            requires_approvals=True,
            requires_interrupt=True,
            requires_steering=True,
            requires_processes=True,
            requires_durable_workspace=True,
        ),
    )

    assert result.compatible is True
    assert result.issues == ()


def test_preflight_accepts_persistent_reference_harness_capabilities():
    result = preflight_harness(
        reasoning_native_capabilities(),
        ExecutionTargetCapabilities(
            target_id="stateless",
            display_name="Stateless target",
            kind=ExecutionTargetKind.STATELESS,
            isolation=TargetIsolation.PROCESS,
            workspace_access=(WorkspaceAccess.NONE,),
            network_access=(NetworkAccess.DENIED,),
        ),
        HarnessRequirements(
            session_mode=SessionMode.RESUME,
            reasoning_effort="high",
            reasoning_visibility=ReasoningVisibility.SUMMARY,
            requires_tools=True,
            requires_skills=True,
            requires_structured_output=True,
            requires_interrupt=True,
            requires_checkpoints=True,
            requires_context_compaction=True,
            requires_usage=True,
            requires_cost=True,
        ),
    )

    assert result.compatible is True
    assert result.issues == ()


def test_preflight_fails_closed_for_remote_target_policy_gaps():
    remote = ExecutionTargetCapabilities(
        target_id="cloud-stateless",
        display_name="Cloud stateless worker",
        kind=ExecutionTargetKind.STATELESS,
        isolation=TargetIsolation.CONTAINER,
        workspace_access=(WorkspaceAccess.NONE, WorkspaceAccess.READ_ONLY),
        network_access=(NetworkAccess.UNRESTRICTED,),
        supports_processes=False,
        durable_workspace=False,
    )

    result = preflight_harness(
        _codex_harness(),
        remote,
        HarnessRequirements(
            workspace_access=WorkspaceAccess.READ_WRITE,
            network_access=NetworkAccess.DENIED,
            minimum_isolation=TargetIsolation.VM,
            requires_cost=True,
            requires_processes=True,
            requires_durable_workspace=True,
        ),
    )

    assert result.compatible is False
    assert {issue.code for issue in result.issues} == {
        "target_workspace_access",
        "network_access",
        "isolation",
        "cost",
        "processes",
        "durable_workspace",
    }
    assert {issue.scope for issue in result.issues} == {"harness", "target"}


def test_legacy_executor_bridge_preserves_declared_capabilities():
    executor = ExecutorCapabilities(
        executor_id="claude-code",
        display_name="Claude Code CLI",
        prompt_fidelity=PromptFidelity.CONTROLLED_INPUT,
        supports_tools=True,
        supports_sessions=True,
        supports_structured_output=True,
        supports_mcp=True,
        model_patterns=("claude-*",),
    )

    harness = legacy_executor_capabilities(
        executor,
        transport=HarnessTransport.LOCAL_PROCESS,
        workspace_access=(WorkspaceAccess.NONE, WorkspaceAccess.READ_ONLY, WorkspaceAccess.READ_WRITE),
        reasoning_efforts=("low", "medium", "high"),
    )

    assert harness.harness_id == "claude-code"
    assert harness.session_modes == (SessionMode.FRESH, SessionMode.RESUME, SessionMode.FORK)
    assert harness.supports_tools is True
    assert harness.supports_mcp is True
    assert harness.model_patterns == ("claude-*",)


def test_session_contract_pins_target_snapshot_and_rejects_secret_metadata():
    session = HarnessSessionSpec(
        harness_id="codex",
        target=_local_sandbox(),
        model="gpt-5.6-sol",
        model_provider="openai",
        metadata={"project_id": "research"},
    )

    assert session.target.target_id == "local-sandbox"
    with pytest.raises(ValueError, match="credential-like key credentials"):
        HarnessSessionSpec(
            harness_id="codex",
            target=_local_sandbox(),
            model="gpt-5.6-sol",
            metadata={"credentials": {"api_key": "must-not-persist"}},
        )


def test_turn_result_rejects_credentials_nested_in_a_checkpoint():
    with pytest.raises(
        ValueError,
        match=r"credential-like key continuation\.authorization",
    ):
        HarnessTurnResult(
            session_id="session-1",
            turn_id="turn-1",
            status="completed",
            checkpoint={
                "continuation": {
                    "authorization": "Bearer must-not-persist",
                }
            },
        )


def test_conformance_accepts_a_capability_consistent_trace():
    at = datetime.now(UTC).isoformat()
    events = (
        HarnessEvent("session-1", "turn-1", 10, HarnessEventKind.TURN_STARTED, at),
        HarnessEvent("session-1", "turn-1", 11, HarnessEventKind.REASONING_DELTA, at, {"text": "Checking"}),
        HarnessEvent("session-1", "turn-1", 12, HarnessEventKind.MESSAGE_COMPLETED, at, {"text": "Done"}),
        HarnessEvent("session-1", "turn-1", 13, HarnessEventKind.USAGE, at, {"total_tokens": 12}),
        HarnessEvent("session-1", "turn-1", 14, HarnessEventKind.TURN_COMPLETED, at),
    )
    result = HarnessTurnResult(session_id="session-1", turn_id="turn-1", status="completed", output_text="Done")

    report = validate_harness_trace(_codex_harness(), events, result)

    assert report.valid is True
    assert report.issues == ()


def test_conformance_rejects_events_the_harness_did_not_advertise():
    capabilities = HarnessCapabilities(
        harness_id="minimal",
        display_name="Minimal harness",
        adapter_version="test",
        transport=HarnessTransport.IN_PROCESS,
        prompt_fidelity=PromptFidelity.WIRE_EXACT,
    )
    at = datetime.now(UTC).isoformat()
    events = (
        HarnessEvent("session-1", "turn-1", 2, HarnessEventKind.TURN_STARTED, at),
        HarnessEvent("session-1", "turn-1", 2, HarnessEventKind.TOOL_REQUESTED, at, {"name": "shell"}),
        HarnessEvent("session-1", "turn-1", 3, HarnessEventKind.COST, at, {"amount_usd": 0.01}),
        HarnessEvent("session-1", "turn-1", 4, HarnessEventKind.TURN_FAILED, at),
        HarnessEvent("session-1", "turn-1", 5, HarnessEventKind.MESSAGE_COMPLETED, at, {"text": "late"}),
    )
    result = HarnessTurnResult(session_id="session-1", turn_id="turn-1", status="completed")

    report = validate_harness_trace(capabilities, events, result)

    assert report.valid is False
    assert {issue.code for issue in report.issues} == {
        "event_sequence",
        "terminal_status",
        "terminal_order",
        "undeclared_tools",
        "undeclared_cost",
    }
