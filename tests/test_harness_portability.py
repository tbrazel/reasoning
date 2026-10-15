from __future__ import annotations

import copy
import time

import pytest

from reasoning_ledger.agents import PromptFidelity
from reasoning_ledger.harnesses import (
    CheckpointEffectState,
    CheckpointSafety,
    ExecutionTargetCapabilities,
    ExecutionTargetKind,
    HarnessCapabilities,
    HarnessEvent,
    HarnessEventKind,
    HarnessSession,
    HarnessSessionSpec,
    HarnessTransport,
    HarnessTurnRequest,
    HarnessTurnResult,
    NetworkAccess,
    PortableArtifactReference,
    PortableCheckpointError,
    PortableCheckpointSource,
    SessionMode,
    TargetIsolation,
    TurnSafetyTracker,
    WorkspaceAccess,
    assess_automatic_failover,
    checkpoint_messages,
    create_portable_checkpoint,
    portable_messages,
    promote_portable_checkpoint,
    validate_portable_checkpoint,
)
from reasoning_ledger.webapp import ChatSession


def _source() -> PortableCheckpointSource:
    return PortableCheckpointSource(
        harness_id="reasoning-native",
        target_id="local-stateless",
        model="example/model",
        operation_id="op_123",
        inference_route_id="openrouter",
        model_provider="openrouter",
        project_id="project-1",
        chat_id="chat-1",
    )


def _event(
    sequence: int,
    kind: HarnessEventKind,
    data: dict | None = None,
) -> HarnessEvent:
    return HarnessEvent(
        session_id="session-1",
        turn_id="turn-1",
        sequence=sequence,
        kind=kind,
        at="2026-08-30T00:00:00+00:00",
        data=data or {},
    )


def test_portable_checkpoint_round_trip_is_content_addressed() -> None:
    checkpoint = create_portable_checkpoint(
        source=_source(),
        messages=(
            {"role": "user", "content": "Plan a release."},
            {"role": "assistant", "content": "Here is the plan."},
        ),
        safety=CheckpointSafety(
            effect_state=CheckpointEffectState.NONE_OBSERVED,
            event_stream_complete=True,
            last_event_sequence=4,
        ),
        artifacts=(
            PortableArtifactReference(
                artifact_id="artifact-1",
                path="reports/plan.md",
                sha256="a" * 64,
            ),
        ),
        created_at="2026-08-30T00:00:00+00:00",
    )

    restored = validate_portable_checkpoint(checkpoint.to_dict())

    assert restored == checkpoint
    assert restored.checkpoint_id.startswith("ptchk_")
    assert checkpoint_messages(restored) == (
        {"role": "user", "content": "Plan a release."},
        {"role": "assistant", "content": "Here is the plan."},
    )
    assert assess_automatic_failover(restored).automatic


def test_portable_checkpoint_rejects_tampering_and_credentials() -> None:
    checkpoint = create_portable_checkpoint(
        source=_source(),
        messages=({"role": "user", "content": "Hello"},),
        safety=CheckpointSafety(
            effect_state=CheckpointEffectState.NONE_OBSERVED,
            event_stream_complete=True,
        ),
        created_at="2026-08-30T00:00:00+00:00",
    )
    tampered = copy.deepcopy(checkpoint.to_dict())
    tampered["messages"][0]["content"] = "Changed"
    with pytest.raises(PortableCheckpointError, match="digest"):
        validate_portable_checkpoint(tampered)

    credentialed = copy.deepcopy(checkpoint.to_dict())
    credentialed["source"]["api-key"] = "forbidden"
    with pytest.raises(PortableCheckpointError, match="credential-like"):
        validate_portable_checkpoint(credentialed)


def test_portable_messages_exclude_reasoning_activity_and_errors() -> None:
    messages = portable_messages(
        (
            {"role": "user", "text": "Question"},
            {"role": "reasoning", "text": "Private analysis"},
            {"role": "activity", "item": {"tool": "shell"}},
            {"role": "assistant", "text": "Answer"},
            {"role": "error", "text": "Failure"},
        )
    )

    assert [(message.role, message.content) for message in messages] == [
        ("user", "Question"),
        ("assistant", "Answer"),
    ]


def test_turn_safety_tracker_distinguishes_pre_effect_and_incomplete_streams() -> None:
    clean = TurnSafetyTracker()
    clean.observe(_event(1, HarnessEventKind.TURN_STARTED))
    clean.observe(_event(2, HarnessEventKind.MESSAGE_COMPLETED, {"text": "Done"}))
    clean.mark_stream_complete()
    clean_checkpoint = create_portable_checkpoint(
        source=_source(),
        messages=({"role": "assistant", "content": "Done"},),
        safety=clean.snapshot(),
        created_at="2026-08-30T00:00:00+00:00",
    )
    assert clean_checkpoint.safety.effect_state is CheckpointEffectState.NONE_OBSERVED
    assert clean_checkpoint.safe_for_automatic_failover

    incomplete = TurnSafetyTracker()
    incomplete.observe(_event(1, HarnessEventKind.TURN_STARTED))
    incomplete_checkpoint = create_portable_checkpoint(
        source=_source(),
        messages=({"role": "user", "content": "Question"},),
        safety=incomplete.snapshot(),
        created_at="2026-08-30T00:00:00+00:00",
    )
    assert incomplete_checkpoint.safety.effect_state is CheckpointEffectState.UNKNOWN
    assert not incomplete_checkpoint.safe_for_automatic_failover


@pytest.mark.parametrize(
    ("kind", "data"),
    (
        (HarnessEventKind.TOOL_STARTED, {"name": "write"}),
        (HarnessEventKind.TOOL_COMPLETED, {"name": "write", "ok": True}),
        (HarnessEventKind.FILE_CHANGED, {"path": "README.md"}),
        (HarnessEventKind.APPROVAL_RESOLVED, {"decision": "accept"}),
        (HarnessEventKind.PROGRESS, {"category": "artifact"}),
    ),
)
def test_potential_effects_require_explicit_promotion(
    kind: HarnessEventKind,
    data: dict,
) -> None:
    tracker = TurnSafetyTracker()
    tracker.observe(_event(1, HarnessEventKind.TURN_STARTED))
    tracker.observe(_event(2, kind, data))
    tracker.observe(_event(3, HarnessEventKind.CHECKPOINT_CREATED, {"digest": "opaque"}))
    tracker.mark_stream_complete()
    checkpoint = create_portable_checkpoint(
        source=_source(),
        messages=({"role": "assistant", "content": "State after effect"},),
        safety=tracker.snapshot(),
        created_at="2026-08-30T00:00:00+00:00",
    )

    assert checkpoint.safety.effect_state is CheckpointEffectState.POTENTIAL_EFFECTS
    assert not checkpoint.safe_for_automatic_failover
    assert checkpoint.safety.advertised_checkpoint_sequences == (3,)

    promoted = promote_portable_checkpoint(
        checkpoint,
        actor="user:local",
        reason="Reviewed the resulting workspace and semantic context.",
        promoted_at="2026-08-30T00:01:00+00:00",
    )
    decision = assess_automatic_failover(promoted)
    assert decision.automatic
    assert "user:local" in decision.reason
    assert promoted.checkpoint_id != checkpoint.checkpoint_id


def test_tracker_rejects_replayed_or_out_of_order_events() -> None:
    tracker = TurnSafetyTracker()
    tracker.observe(_event(2, HarnessEventKind.TURN_STARTED))

    with pytest.raises(PortableCheckpointError, match="increase monotonically"):
        tracker.observe(_event(2, HarnessEventKind.MESSAGE_DELTA, {"text": "again"}))


def test_artifact_references_cannot_escape_the_portable_namespace() -> None:
    with pytest.raises(PortableCheckpointError, match="relative"):
        PortableArtifactReference(
            artifact_id="artifact-1",
            path="../secrets.txt",
            sha256="a" * 64,
        )


class _EffectHarness:
    capabilities = HarnessCapabilities(
        harness_id="effect-harness",
        display_name="Effect harness",
        adapter_version="test",
        transport=HarnessTransport.IN_PROCESS,
        prompt_fidelity=PromptFidelity.WIRE_EXACT,
        session_modes=(SessionMode.FRESH,),
        workspace_access=(WorkspaceAccess.READ_WRITE,),
        supports_tools=True,
        supports_checkpoints=True,
        model_patterns=("example/*",),
    )

    def __init__(self) -> None:
        self.last_messages: tuple[dict, ...] = ()

    def create_session(self, spec: HarnessSessionSpec) -> HarnessSession:
        return HarnessSession(
            session_id="effect-session",
            capabilities=self.capabilities,
            target=spec.target,
            model=spec.model,
            model_provider=spec.model_provider,
        )

    def run_turn(self, session, request, emit=None) -> HarnessTurnResult:
        assert emit is not None
        self.last_messages = request.messages
        events = (
            _event(1, HarnessEventKind.TURN_STARTED),
            _event(2, HarnessEventKind.TOOL_COMPLETED, {"name": "write", "ok": True}),
            _event(3, HarnessEventKind.MESSAGE_COMPLETED, {"text": "Wrote the plan."}),
            _event(4, HarnessEventKind.TURN_COMPLETED),
        )
        for event in events:
            emit(
                HarnessEvent(
                    session_id=session.session_id,
                    turn_id=request.turn_id,
                    sequence=event.sequence,
                    kind=event.kind,
                    at=event.at,
                    data=event.data,
                )
            )
        return HarnessTurnResult(
            session_id=session.session_id,
            turn_id=request.turn_id,
            status="completed",
            output_text="Wrote the plan.",
        )

    def interrupt(self, session: HarnessSession, turn_id: str) -> bool:
        return False

    def close_session(self, session: HarnessSession) -> None:
        return None


def test_chat_operation_exports_and_promotes_immutable_portable_checkpoint(
    tmp_path,
) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    session = ChatSession(
        scratch,
        tmp_path / "ledger",
        "example/model",
        "workspace-write",
        state_dir=tmp_path,
        project_id="project-1",
        chat_id="chat-1",
    )
    target = ExecutionTargetCapabilities(
        target_id="workspace",
        display_name="Workspace",
        kind=ExecutionTargetKind.LOCAL_SANDBOX,
        isolation=TargetIsolation.OS_SANDBOX,
        workspace_access=(WorkspaceAccess.READ_WRITE,),
        network_access=(NetworkAccess.DENIED,),
    )
    adapter = _EffectHarness()

    started = session.start_harness_turn(
        "Write the plan.",
        adapter.capabilities.harness_id,
        "example/model",
        "example",
        adapter,
        HarnessSessionSpec(
            harness_id=adapter.capabilities.harness_id,
            target=target,
            model="example/model",
            model_provider="example",
        ),
        lambda operation_id: HarnessTurnRequest(
            turn_id=operation_id,
            messages=({"role": "user", "content": "Write the plan."},),
        ),
        inference_route_id="example",
    )
    assert started == "started"
    deadline = time.monotonic() + 5
    while session.turn_active and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not session.turn_active

    operation_id = session.last_operation_id
    assert operation_id is not None
    operation = session.operation(operation_id)
    assert operation["side_effect_safety"]["effect_state"] == "potential_effects"
    assert operation["side_effect_safety"]["effect_kinds"] == ["tool.completed"]

    checkpoint = session.export_portable_checkpoint(operation_id)
    assert checkpoint["automatic_failover"]["allowed"] is False
    assert [message["role"] for message in checkpoint["messages"]] == [
        "user",
        "assistant",
    ]
    original_path = (
        session.portable_checkpoints_dir / f"{checkpoint['checkpoint_id']}.json"
    )
    assert original_path.is_file()

    promoted = session.promote_portable_checkpoint(
        checkpoint["checkpoint_id"],
        reason="Reviewed the workspace after the write.",
    )
    assert promoted["automatic_failover"]["allowed"] is True
    assert promoted["checkpoint_id"] != checkpoint["checkpoint_id"]
    assert original_path.is_file()
    assert (
        session.portable_checkpoints_dir / f"{promoted['checkpoint_id']}.json"
    ).is_file()


def test_fresh_chat_handoff_is_visible_role_preserving_and_audited(tmp_path) -> None:
    checkpoint = create_portable_checkpoint(
        source=_source(),
        messages=(
            {"role": "user", "content": "Plan a release."},
            {"role": "assistant", "content": "Start with a canary."},
        ),
        safety=CheckpointSafety(
            effect_state=CheckpointEffectState.NONE_OBSERVED,
            event_stream_complete=True,
            last_event_sequence=2,
        ),
        created_at="2026-08-30T00:00:00+00:00",
    )
    destination_root = tmp_path / "destination"
    scratch = destination_root / "scratch"
    scratch.mkdir(parents=True)
    destination = ChatSession(
        scratch,
        destination_root / "ledger",
        "example/model",
        "workspace-write",
        state_dir=destination_root,
        project_id="project-1",
        chat_id="chat-2",
    )
    destination.stage_portable_handoff(checkpoint, source_chat_id="chat-1")

    assert destination.pending_portable_handoff_messages() == (
        {"role": "user", "content": "Plan a release."},
        {"role": "assistant", "content": "Start with a canary."},
    )
    assert [entry["role"] for entry in destination.transcript()] == [
        "user",
        "assistant",
    ]
    assert all(
        entry["imported_checkpoint_id"] == checkpoint.checkpoint_id
        for entry in destination.transcript()
    )

    target = ExecutionTargetCapabilities(
        target_id="workspace",
        display_name="Workspace",
        kind=ExecutionTargetKind.LOCAL_SANDBOX,
        isolation=TargetIsolation.OS_SANDBOX,
        workspace_access=(WorkspaceAccess.READ_WRITE,),
        network_access=(NetworkAccess.DENIED,),
    )
    adapter = _EffectHarness()
    started = destination.start_harness_turn(
        "Turn it into a checklist.",
        adapter.capabilities.harness_id,
        "example/model",
        "example",
        adapter,
        HarnessSessionSpec(
            harness_id=adapter.capabilities.harness_id,
            target=target,
            model="example/model",
            model_provider="example",
        ),
        lambda operation_id: HarnessTurnRequest(
            turn_id=operation_id,
            messages=(
                *destination.pending_portable_handoff_messages(),
                {"role": "user", "content": "Turn it into a checklist."},
            ),
        ),
        inference_route_id="example",
    )
    assert started == "started"
    deadline = time.monotonic() + 5
    while destination.turn_active and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not destination.turn_active

    assert adapter.last_messages == (
        {"role": "user", "content": "Plan a release."},
        {"role": "assistant", "content": "Start with a canary."},
        {"role": "user", "content": "Turn it into a checklist."},
    )
    assert destination.pending_portable_handoff_messages() == ()
    operation_id = destination.last_operation_id
    assert operation_id is not None
    assert (
        destination.operation(operation_id)["imported_portable_checkpoint_id"]
        == checkpoint.checkpoint_id
    )
