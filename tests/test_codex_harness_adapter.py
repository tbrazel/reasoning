from __future__ import annotations

import queue
import sys
import threading
import time
from pathlib import Path

from reasoning_ledger.harnesses import (
    CodexAppServerHarnessAdapter,
    ExecutionTargetCapabilities,
    ExecutionTargetKind,
    HarnessApprovalDecision,
    HarnessEventKind,
    HarnessSessionSpec,
    HarnessTurnRequest,
    InteractiveHarnessAdapter,
    NetworkAccess,
    TargetIsolation,
    WorkspaceAccess,
    validate_harness_trace,
)


def _target() -> ExecutionTargetCapabilities:
    return ExecutionTargetCapabilities(
        target_id="test-sandbox",
        display_name="Test sandbox",
        kind=ExecutionTargetKind.LOCAL_SANDBOX,
        isolation=TargetIsolation.PROCESS,
        workspace_access=(WorkspaceAccess.READ_WRITE,),
        network_access=(NetworkAccess.DENIED,),
        supports_processes=True,
        durable_workspace=True,
    )


def _adapter(tmp_path: Path, *, interactive_approvals: bool = False) -> CodexAppServerHarnessAdapter:
    fake = Path(__file__).with_name("fake_appserver.py")
    return CodexAppServerHarnessAdapter(
        session_id="adapter-test",
        workdir=tmp_path,
        state_path=tmp_path / "chat-thread.json",
        stderr_path=tmp_path / "codex.log",
        usage_path=tmp_path / "usage.json",
        command=lambda: [sys.executable, str(fake)],
        environment=lambda: None,
        developer_instructions=lambda: "Test instructions.",
        sandbox_mode=lambda: "workspace-write",
        interactive_approvals=interactive_approvals,
        approval_timeout=2.0,
    )


def _session(adapter: CodexAppServerHarnessAdapter):
    return adapter.create_session(
        HarnessSessionSpec(
            harness_id="codex",
            target=_target(),
            model="gpt-5.6-sol",
            model_provider="openai",
        )
    )


def test_approval_resolution_is_published_before_a_racing_terminal_event(tmp_path):
    adapter = _adapter(tmp_path)
    adapter._event_queue = queue.Queue()
    adapter._active_turn_id = "approval-race"
    adapter._pending_approvals["approval"] = {"rpc_id": 1, "timer": threading.Timer(60, lambda: None), "public": {}}
    started, completed = threading.Event(), threading.Event()

    def terminal():
        started.set()
        adapter._enqueue(HarnessEventKind.TURN_COMPLETED, data={"status": "completed"})
        completed.set()

    thread = threading.Thread(target=terminal)

    class ImmediateClient:
        def respond(self, identity, result):
            assert identity == 1 and result == {"decision": "accept"}
            thread.start()
            assert started.wait(2)
            # Let an immediate subprocess completion race with the caller's
            # successful response; no provider or CLI is invoked by this test.
            completed.wait(0.1)

    adapter._client = ImmediateClient()
    assert adapter.resolve_approval_request("approval", "accept")
    thread.join(timeout=2)
    assert not thread.is_alive()
    events = [adapter._event_queue.get_nowait().canonical for _ in range(2)]
    assert [event.kind for event in events] == [HarnessEventKind.APPROVAL_RESOLVED, HarnessEventKind.TURN_COMPLETED]
    assert events[0].sequence < events[1].sequence


def test_codex_adapter_emits_a_conformant_canonical_trace(tmp_path):
    adapter = _adapter(tmp_path)
    assert isinstance(adapter, InteractiveHarnessAdapter)
    session = _session(adapter)
    events = []
    result = adapter.run_turn(
        session,
        HarnessTurnRequest(
            turn_id="turn-quick",
            messages=({"text": "quick"},),
            effort="high",
        ),
        events.append,
    )

    assert result.status == "completed"
    assert result.output_text == "hello"
    assert [event.kind for event in events] == [
        HarnessEventKind.TURN_STARTED,
        HarnessEventKind.MESSAGE_COMPLETED,
        HarnessEventKind.TURN_COMPLETED,
    ]
    assert validate_harness_trace(adapter.capabilities, events, result).valid is True
    assert adapter.thread_state() == (session.adapter_session_ref, "openai")
    adapter.close_session(session)


def test_codex_adapter_keeps_detailed_tool_payloads_out_of_canonical_events(tmp_path):
    adapter = _adapter(tmp_path)
    session = _session(adapter)
    events = []
    result = adapter.run_turn(
        session,
        HarnessTurnRequest(
            turn_id="turn-mcp",
            messages=({"text": "mcp"},),
        ),
        events.append,
    )

    tool = next(event for event in events if event.kind is HarnessEventKind.TOOL_COMPLETED)
    assert tool.data == {
        "tool_type": "mcp_tool_call",
        "server": "reasoning",
        "tool": "ledger_status",
        "status": "completed",
        "has_error": False,
    }
    assert "arguments" not in tool.data
    assert "result" not in tool.data
    assert validate_harness_trace(adapter.capabilities, events, result).valid is True
    adapter.close_session(session)


def test_codex_adapter_preserves_provider_turn_failure(tmp_path):
    adapter = _adapter(tmp_path)
    session = _session(adapter)
    events = []
    result = adapter.run_turn(
        session,
        HarnessTurnRequest(
            turn_id="turn-failed",
            messages=({"text": "turnfail"},),
        ),
        events.append,
    )

    assert result.status == "failed"
    assert result.error == "provider rejected the turn"
    assert events[-1].kind is HarnessEventKind.TURN_FAILED
    assert events[-1].data["error"] == "provider rejected the turn"
    assert validate_harness_trace(adapter.capabilities, events, result).valid is True
    adapter.close_session(session)


def test_codex_adapter_routes_interactive_approval_through_canonical_events(tmp_path):
    adapter = _adapter(tmp_path, interactive_approvals=True)
    session = _session(adapter)
    events = []
    outcome = {}

    def run() -> None:
        outcome["result"] = adapter.run_turn(
            session,
            HarnessTurnRequest(
                turn_id="turn-approval",
                messages=({"text": "approve"},),
            ),
            events.append,
        )

    worker = threading.Thread(target=run)
    worker.start()
    deadline = time.time() + 5
    pending = []
    while time.time() < deadline and not pending:
        pending = adapter.pending_approvals(session)
        time.sleep(0.01)
    assert len(pending) == 1
    assert adapter.resolve_approval(session, pending[0].request_id, HarnessApprovalDecision.ACCEPT) is True
    worker.join(timeout=5)
    assert not worker.is_alive()

    result = outcome["result"]
    assert result.status == "completed"
    assert HarnessEventKind.APPROVAL_REQUIRED in [event.kind for event in events]
    assert HarnessEventKind.APPROVAL_RESOLVED in [event.kind for event in events]
    assert validate_harness_trace(adapter.capabilities, events, result).valid is True
    adapter.close_session(session)
