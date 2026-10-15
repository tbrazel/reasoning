from __future__ import annotations

import json
import threading
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from reasoning_ledger.agents import PromptFidelity
from reasoning_ledger.agents.contracts import dataclass_dict
from reasoning_ledger.harnesses import (
    ExecutionTargetCapabilities,
    ExecutionTargetKind,
    HarnessApprovalDecision,
    HarnessApprovalRequest,
    HarnessCapabilities,
    HarnessEvent,
    HarnessEventKind,
    HarnessSession,
    HarnessSessionSpec,
    HarnessSteerStatus,
    HarnessTransport,
    HarnessTurnRequest,
    HarnessTurnResult,
    HttpRemoteHarnessClient,
    NetworkAccess,
    ReasoningVisibility,
    RemoteHarnessAdapter,
    RemoteHarnessTransportError,
    RemoteTurnReceipt,
    SessionMode,
    TargetIsolation,
    WorkspaceAccess,
)


def _capabilities() -> HarnessCapabilities:
    return HarnessCapabilities(
        harness_id="cloud-reference",
        display_name="Cloud reference harness",
        adapter_version="2026.08",
        transport=HarnessTransport.REMOTE_SERVICE,
        prompt_fidelity=PromptFidelity.WIRE_EXACT,
        session_modes=(SessionMode.FRESH, SessionMode.RESUME),
        workspace_access=(WorkspaceAccess.NONE, WorkspaceAccess.READ_WRITE),
        reasoning_efforts=("low", "high"),
        reasoning_visibility=ReasoningVisibility.STREAMED_SUMMARY,
        supports_tools=True,
        supports_approvals=True,
        supports_interrupt=True,
        supports_steering=True,
        reports_usage=True,
        model_patterns=("example/*",),
    )


def _target() -> ExecutionTargetCapabilities:
    return ExecutionTargetCapabilities(
        target_id="sandbox.vm",
        display_name="Remote VM",
        kind=ExecutionTargetKind.REMOTE_SANDBOX,
        isolation=TargetIsolation.VM,
        workspace_access=(WorkspaceAccess.NONE, WorkspaceAccess.READ_WRITE),
        network_access=(NetworkAccess.DENIED, NetworkAccess.ALLOWLISTED),
        supports_processes=True,
        durable_workspace=True,
    )


def _spec() -> HarnessSessionSpec:
    return HarnessSessionSpec(
        harness_id="cloud-reference",
        target=_target(),
        model="example/deep",
        model_provider="example",
    )


def _event(sequence: int, kind: HarnessEventKind, data: dict[str, Any] | None = None) -> HarnessEvent:
    return HarnessEvent(
        session_id="chat.remote",
        turn_id="turn-1",
        sequence=sequence,
        kind=kind,
        at=datetime.now(UTC).isoformat(),
        data=data or {},
    )


class ReconnectingClient:
    def __init__(self) -> None:
        self.capabilities = _capabilities()
        self.session = HarnessSession(
            session_id="chat.remote",
            capabilities=self.capabilities,
            target=_target(),
            model="example/deep",
            model_provider="example",
            adapter_session_ref="service/session with slash",
        )
        self.submit_keys: list[str] = []
        self.stream_cursors: list[int] = []
        self.released = False
        self.approval_resolutions: list[tuple[str, HarnessApprovalDecision]] = []

    def describe(self) -> HarnessCapabilities:
        return self.capabilities

    def create_session(self, spec: HarnessSessionSpec, *, idempotency_key: str) -> HarnessSession:
        assert idempotency_key == "chat.remote"
        assert spec.target == _target()
        return self.session

    def submit_turn(
        self,
        adapter_session_ref: str,
        request: HarnessTurnRequest,
        *,
        idempotency_key: str,
    ) -> RemoteTurnReceipt:
        assert adapter_session_ref == self.session.adapter_session_ref
        self.submit_keys.append(idempotency_key)
        if len(self.submit_keys) == 1:
            raise RemoteHarnessTransportError("lost acknowledgement", transient=True)
        return RemoteTurnReceipt("chat.remote", request.turn_id, "existing")

    def stream_events(
        self,
        adapter_session_ref: str,
        turn_id: str,
        *,
        after_sequence: int,
    ) -> Iterable[HarnessEvent]:
        self.stream_cursors.append(after_sequence)
        if len(self.stream_cursors) == 1:
            yield _event(1, HarnessEventKind.TURN_STARTED)
            yield _event(2, HarnessEventKind.REASONING_DELTA, {"text": "checking"})
            raise RemoteHarnessTransportError("connection reset", transient=True)
        yield _event(2, HarnessEventKind.REASONING_DELTA, {"text": "checking"})
        yield _event(3, HarnessEventKind.MESSAGE_COMPLETED, {"text": "done"})
        yield _event(4, HarnessEventKind.USAGE, {"total_tokens": 11})
        yield _event(5, HarnessEventKind.TURN_COMPLETED)

    def turn_result(self, adapter_session_ref: str, turn_id: str) -> HarnessTurnResult:
        return HarnessTurnResult(
            session_id="chat.remote",
            turn_id=turn_id,
            status="completed",
            output_text="done",
            usage={"total_tokens": 11},
        )

    def interrupt(self, adapter_session_ref: str, turn_id: str) -> bool:
        return True

    def steer(
        self,
        adapter_session_ref: str,
        turn_id: str,
        messages: tuple[Mapping[str, Any], ...],
    ) -> HarnessSteerStatus:
        return HarnessSteerStatus.STEERED

    def inject_context(
        self,
        adapter_session_ref: str,
        turn_id: str,
        messages: tuple[Mapping[str, Any], ...],
    ) -> bool:
        return True

    def pending_approvals(self, adapter_session_ref: str) -> tuple[HarnessApprovalRequest, ...]:
        return (HarnessApprovalRequest("approval-1", "command", command="git status"),)

    def resolve_approval(
        self,
        adapter_session_ref: str,
        request_id: str,
        decision: HarnessApprovalDecision,
    ) -> bool:
        self.approval_resolutions.append((request_id, decision))
        return True

    def release_session(self, adapter_session_ref: str) -> None:
        self.released = True


def test_remote_adapter_retries_idempotently_and_resumes_from_exact_cursor():
    client = ReconnectingClient()
    adapter = RemoteHarnessAdapter(
        session_id="chat.remote",
        capabilities=_capabilities(),
        client=client,
        reconnect_delay=0,
    )
    session = adapter.create_session(_spec())
    emitted: list[HarnessEvent] = []

    result = adapter.run_turn(
        session,
        HarnessTurnRequest(turn_id="turn-1", messages=({"role": "user", "content": "hello"},)),
        emitted.append,
    )

    assert result.status == "completed"
    assert client.submit_keys == ["turn-1", "turn-1"]
    assert client.stream_cursors == [0, 2]
    assert [event.sequence for event in emitted] == [1, 2, 3, 4, 5]
    assert adapter.pending_approvals(session)[0].request_id == "approval-1"
    assert adapter.resolve_approval(session, "approval-1", HarnessApprovalDecision.ACCEPT)
    assert client.approval_resolutions == [("approval-1", HarnessApprovalDecision.ACCEPT)]
    adapter.close_session(session)
    assert client.released is True


class BlockingClient(ReconnectingClient):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.interrupted = threading.Event()

    def submit_turn(
        self,
        adapter_session_ref: str,
        request: HarnessTurnRequest,
        *,
        idempotency_key: str,
    ) -> RemoteTurnReceipt:
        return RemoteTurnReceipt("chat.remote", request.turn_id, "accepted")

    def stream_events(
        self,
        adapter_session_ref: str,
        turn_id: str,
        *,
        after_sequence: int,
    ) -> Iterable[HarnessEvent]:
        yield _event(1, HarnessEventKind.TURN_STARTED)
        self.started.set()
        assert self.interrupted.wait(timeout=2)
        yield _event(2, HarnessEventKind.TURN_INTERRUPTED)

    def turn_result(self, adapter_session_ref: str, turn_id: str) -> HarnessTurnResult:
        return HarnessTurnResult(session_id="chat.remote", turn_id=turn_id, status="interrupted")

    def interrupt(self, adapter_session_ref: str, turn_id: str) -> bool:
        self.interrupted.set()
        return True


def test_remote_adapter_forwards_live_controls_and_interruption():
    client = BlockingClient()
    adapter = RemoteHarnessAdapter(
        session_id="chat.remote",
        capabilities=_capabilities(),
        client=client,
        reconnect_delay=0,
    )
    session = adapter.create_session(_spec())
    outcome: list[HarnessTurnResult] = []
    worker = threading.Thread(
        target=lambda: outcome.append(
            adapter.run_turn(
                session,
                HarnessTurnRequest(turn_id="turn-1", messages=({"role": "user", "content": "hello"},)),
            )
        )
    )
    worker.start()
    assert client.started.wait(timeout=2)

    assert adapter.steer(session, "turn-1", ({"role": "user", "content": "focus"},)) is HarnessSteerStatus.STEERED
    assert adapter.inject_context(session, "turn-1", ({"role": "developer", "content": "context"},))
    assert adapter.interrupt(session, "turn-1")
    worker.join(timeout=2)

    assert not worker.is_alive()
    assert outcome[0].status == "interrupted"


def test_remote_adapter_fails_closed_when_target_snapshot_changes():
    client = ReconnectingClient()
    client.session = HarnessSession(
        session_id="chat.remote",
        capabilities=_capabilities(),
        target=ExecutionTargetCapabilities(
            target_id="weaker",
            display_name="Weaker target",
            kind=ExecutionTargetKind.STATELESS,
            isolation=TargetIsolation.PROCESS,
        ),
        model="example/deep",
        model_provider="example",
        adapter_session_ref="remote-1",
    )
    adapter = RemoteHarnessAdapter(
        session_id="chat.remote",
        capabilities=_capabilities(),
        client=client,
        reconnect_delay=0,
    )

    with pytest.raises(Exception, match="substituted a different execution target"):
        adapter.create_session(_spec())


def test_http_remote_client_keeps_auth_out_of_payload_and_encodes_opaque_refs():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1/harness":
            return httpx.Response(200, json=dataclass_dict(_capabilities()))
        if request.url.path.endswith("/events"):
            lines = "\n".join(json.dumps(event.to_dict()) for event in (_event(1, HarnessEventKind.TURN_STARTED),))
            return httpx.Response(200, text=lines + "\n", headers={"Content-Type": "application/x-ndjson"})
        raise AssertionError(request.url)

    transport = httpx.MockTransport(handler)
    client = HttpRemoteHarnessClient(
        "https://harness.example",
        headers=lambda: {"Authorization": "Bearer in-memory-only"},
        client=httpx.Client(transport=transport),
    )

    assert client.describe() == _capabilities()
    events = list(client.stream_events("service/session with slash", "turn-1", after_sequence=7))

    assert events[0].kind is HarnessEventKind.TURN_STARTED
    assert requests[0].headers["authorization"] == "Bearer in-memory-only"
    assert requests[1].url.raw_path == b"/v1/sessions/service%2Fsession%20with%20slash/turns/turn-1/events?after_sequence=7"
    assert requests[1].url.params["after_sequence"] == "7"
    assert b"in-memory-only" not in requests[1].content


def test_http_remote_client_requires_explicit_insecure_transport():
    with pytest.raises(ValueError, match="require HTTPS"):
        HttpRemoteHarnessClient(
            "http://harness.example",
            headers=lambda: {},
        )


def test_http_remote_client_bounds_events_and_json_responses():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/events"):
            return httpx.Response(200, text=json.dumps({"oversized": "value"}) + "\n")
        return httpx.Response(200, json={"oversized": "value"})

    client = HttpRemoteHarnessClient(
        "https://harness.example",
        headers=lambda: {},
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        max_event_bytes=8,
        max_response_bytes=8,
    )

    with pytest.raises(RemoteHarnessTransportError, match="event exceeded"):
        list(client.stream_events("session", "turn", after_sequence=0))
    with pytest.raises(RemoteHarnessTransportError, match="response exceeded"):
        client.describe()


def test_http_remote_client_sanitizes_authorization_and_decode_failures():
    def unavailable_headers() -> Mapping[str, str]:
        raise RuntimeError("Bearer credential-must-not-escape")

    unavailable = HttpRemoteHarnessClient(
        "https://harness.example",
        headers=unavailable_headers,
        client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200))),
    )
    with pytest.raises(RemoteHarnessTransportError) as authorization_error:
        unavailable.describe()
    assert "credential-must-not-escape" not in str(authorization_error.value)

    malformed = HttpRemoteHarnessClient(
        "https://harness.example",
        headers=lambda: {},
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={
                        "harness_id": "value-that-must-not-be-reflected",
                    },
                )
            )
        ),
    )
    with pytest.raises(RemoteHarnessTransportError) as decode_error:
        malformed.describe()
    assert "value-that-must-not-be-reflected" not in str(decode_error.value)
