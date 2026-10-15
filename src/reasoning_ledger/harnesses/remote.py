"""Remote-service transport for the provider-neutral harness contract.

The adapter in this module owns retry, replay, and conformance behavior.  The
transport owns authentication and HTTP mechanics.  Authentication material is
supplied through an in-memory callback and never enters a session, turn,
checkpoint, event, or adapter representation.

The HTTP implementation uses a deliberately small versioned API.  Event
streams are newline-delimited JSON so a service can implement them with an
ordinary streaming response while reconnecting clients resume from an exact
``after_sequence`` cursor.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar, runtime_checkable
from urllib.parse import quote, urlsplit

import httpx

from ..agents.contracts import PromptFidelity, dataclass_dict
from .conformance import validate_harness_trace
from .contracts import (
    ExecutionTargetCapabilities,
    ExecutionTargetKind,
    HarnessApprovalDecision,
    HarnessApprovalRequest,
    HarnessCapabilities,
    HarnessEvent,
    HarnessEventKind,
    HarnessEventSink,
    HarnessSession,
    HarnessSessionSpec,
    HarnessSteerStatus,
    HarnessTransport,
    HarnessTurnRequest,
    HarnessTurnResult,
    NetworkAccess,
    ReasoningVisibility,
    SessionMode,
    TargetIsolation,
    WorkspaceAccess,
)

_TERMINAL_EVENTS = {
    HarnessEventKind.TURN_COMPLETED,
    HarnessEventKind.TURN_FAILED,
    HarnessEventKind.TURN_INTERRUPTED,
}
_TERMINAL_BY_STATUS = {
    "completed": HarnessEventKind.TURN_COMPLETED,
    "failed": HarnessEventKind.TURN_FAILED,
    "cancelled": HarnessEventKind.TURN_INTERRUPTED,
    "interrupted": HarnessEventKind.TURN_INTERRUPTED,
}
_RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}
_T = TypeVar("_T")


class RemoteHarnessError(RuntimeError):
    """A remote harness violated its contract or could not finish a request."""


class RemoteHarnessTransportError(RemoteHarnessError):
    """A transport failure, optionally safe to retry with the same identity."""

    def __init__(self, message: str, *, transient: bool) -> None:
        super().__init__(message)
        self.transient = transient


@dataclass(frozen=True)
class RemoteTurnReceipt:
    """Acknowledgement of an idempotent turn submission."""

    session_id: str
    turn_id: str
    disposition: str

    def __post_init__(self) -> None:
        if self.disposition not in {"accepted", "existing", "terminal"}:
            raise ValueError("unknown remote turn disposition")


@runtime_checkable
class RemoteHarnessClient(Protocol):
    """Authenticated service client consumed by ``RemoteHarnessAdapter``.

    Implementations keep endpoints and credentials out of every durable
    contract object.  ``idempotency_key`` must be honored across connection
    loss: repeating a create or submit call returns the original resource.
    """

    def describe(self) -> HarnessCapabilities: ...

    def create_session(
        self,
        spec: HarnessSessionSpec,
        *,
        idempotency_key: str,
    ) -> HarnessSession: ...

    def submit_turn(
        self,
        adapter_session_ref: str,
        request: HarnessTurnRequest,
        *,
        idempotency_key: str,
    ) -> RemoteTurnReceipt: ...

    def stream_events(
        self,
        adapter_session_ref: str,
        turn_id: str,
        *,
        after_sequence: int,
    ) -> Iterable[HarnessEvent]: ...

    def turn_result(
        self,
        adapter_session_ref: str,
        turn_id: str,
    ) -> HarnessTurnResult: ...

    def interrupt(self, adapter_session_ref: str, turn_id: str) -> bool: ...

    def steer(
        self,
        adapter_session_ref: str,
        turn_id: str,
        messages: tuple[Mapping[str, Any], ...],
    ) -> HarnessSteerStatus: ...

    def inject_context(
        self,
        adapter_session_ref: str,
        turn_id: str,
        messages: tuple[Mapping[str, Any], ...],
    ) -> bool: ...

    def pending_approvals(
        self,
        adapter_session_ref: str,
    ) -> tuple[HarnessApprovalRequest, ...]: ...

    def resolve_approval(
        self,
        adapter_session_ref: str,
        request_id: str,
        decision: HarnessApprovalDecision,
    ) -> bool: ...

    def release_session(self, adapter_session_ref: str) -> None: ...


class RemoteHarnessAdapter:
    """Run one harness session through an authenticated remote client.

    Turn creation is idempotent, every reconnect resumes strictly after the
    last accepted event sequence, replayed events are deduplicated, and the
    completed trace is validated against the advertised capabilities before
    its result is accepted.  No automatic cross-harness failover occurs.
    """

    def __init__(
        self,
        *,
        session_id: str,
        capabilities: HarnessCapabilities,
        client: RemoteHarnessClient,
        max_reconnects: int = 4,
        reconnect_delay: float = 0.25,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not session_id.strip():
            raise ValueError("session_id is required")
        if capabilities.transport is not HarnessTransport.REMOTE_SERVICE:
            raise ValueError("remote adapters require remote_service capabilities")
        if max_reconnects < 0:
            raise ValueError("max_reconnects cannot be negative")
        if reconnect_delay < 0:
            raise ValueError("reconnect_delay cannot be negative")
        self.session_id = session_id
        self._capabilities = capabilities
        self._client = client
        self._max_reconnects = max_reconnects
        self._reconnect_delay = reconnect_delay
        self._sleep = sleep
        self._lock = threading.RLock()
        self._session: HarnessSession | None = None
        self._active_turn_id: str | None = None

    @property
    def capabilities(self) -> HarnessCapabilities:
        return self._capabilities

    def create_session(self, spec: HarnessSessionSpec) -> HarnessSession:
        if spec.harness_id != self.capabilities.harness_id:
            raise RemoteHarnessError(f"unsupported harness: {spec.harness_id}")
        with self._lock:
            if self._session is not None:
                raise RemoteHarnessError("a remote harness session is already open")
        observed = self._retry(
            "describe remote harness",
            self._client.describe,
        )
        if observed != self.capabilities:
            raise RemoteHarnessError("remote harness capabilities changed after selection preflight")
        session = self._retry(
            "create remote harness session",
            lambda: self._client.create_session(spec, idempotency_key=self.session_id),
        )
        self._validate_session(spec, session)
        with self._lock:
            self._session = session
        return session

    def run_turn(
        self,
        session: HarnessSession,
        request: HarnessTurnRequest,
        emit: HarnessEventSink | None = None,
    ) -> HarnessTurnResult:
        adapter_ref = self._check_session(session)
        with self._lock:
            if self._active_turn_id is not None:
                raise RemoteHarnessError("a remote harness turn is already active")
            self._active_turn_id = request.turn_id

        events: list[HarnessEvent] = []
        cursor = 0
        terminal_seen = False
        reconnects = 0
        try:
            receipt = self._retry(
                "submit remote harness turn",
                lambda: self._client.submit_turn(
                    adapter_ref,
                    request,
                    idempotency_key=request.turn_id,
                ),
            )
            if receipt.session_id != session.session_id or receipt.turn_id != request.turn_id:
                raise RemoteHarnessError("remote turn receipt identity mismatch")

            while not terminal_seen:
                try:
                    stream = self._client.stream_events(
                        adapter_ref,
                        request.turn_id,
                        after_sequence=cursor,
                    )
                    for event in stream:
                        self._validate_event(session, request, event)
                        if event.sequence <= cursor:
                            continue
                        cursor = event.sequence
                        events.append(event)
                        if emit is not None:
                            emit(event)
                        if event.kind in _TERMINAL_EVENTS:
                            terminal_seen = True
                            break
                    if terminal_seen:
                        break
                    raise RemoteHarnessTransportError(
                        "remote event stream ended before a terminal event",
                        transient=True,
                    )
                except RemoteHarnessTransportError as error:
                    if not error.transient or reconnects >= self._max_reconnects:
                        raise
                    reconnects += 1
                    self._sleep(self._reconnect_delay * reconnects)

            result = self._retry(
                "read remote harness result",
                lambda: self._client.turn_result(adapter_ref, request.turn_id),
            )
            if result.session_id != session.session_id or result.turn_id != request.turn_id:
                raise RemoteHarnessError("remote turn result identity mismatch")
            expected_terminal = _TERMINAL_BY_STATUS[result.status]
            if events[-1].kind is not expected_terminal:
                raise RemoteHarnessError(
                    f"remote result status {result.status} does not match {events[-1].kind.value}"
                )
            report = validate_harness_trace(self.capabilities, tuple(events), result)
            if not report.valid:
                details = "; ".join(f"{issue.code}: {issue.detail}" for issue in report.issues)
                raise RemoteHarnessError(f"remote harness emitted an invalid trace: {details}")
            return result
        finally:
            with self._lock:
                self._active_turn_id = None

    def interrupt(self, session: HarnessSession, turn_id: str) -> bool:
        if not self.capabilities.supports_interrupt:
            return False
        adapter_ref = self._check_session(session)
        with self._lock:
            if self._active_turn_id != turn_id:
                return False
        return self._client.interrupt(adapter_ref, turn_id)

    def steer(
        self,
        session: HarnessSession,
        turn_id: str,
        messages: tuple[Mapping[str, Any], ...],
    ) -> HarnessSteerStatus:
        if not self.capabilities.supports_steering:
            return HarnessSteerStatus.FAILED
        adapter_ref = self._check_active_turn(session, turn_id)
        return self._client.steer(adapter_ref, turn_id, messages)

    def inject_context(
        self,
        session: HarnessSession,
        turn_id: str,
        messages: tuple[Mapping[str, Any], ...],
    ) -> bool:
        adapter_ref = self._check_active_turn(session, turn_id)
        return self._client.inject_context(adapter_ref, turn_id, messages)

    def pending_approvals(self, session: HarnessSession) -> tuple[HarnessApprovalRequest, ...]:
        if not self.capabilities.supports_approvals:
            return ()
        return self._client.pending_approvals(self._check_session(session))

    def resolve_approval(
        self,
        session: HarnessSession,
        request_id: str,
        decision: HarnessApprovalDecision,
    ) -> bool:
        if not self.capabilities.supports_approvals:
            return False
        return self._client.resolve_approval(
            self._check_session(session),
            request_id,
            decision,
        )

    def close_session(self, session: HarnessSession) -> None:
        adapter_ref = self._check_session(session)
        with self._lock:
            if self._active_turn_id is not None:
                raise RemoteHarnessError("cannot release a remote session during an active turn")
        self._client.release_session(adapter_ref)
        with self._lock:
            self._session = None

    def _retry(self, label: str, operation: Callable[[], _T]) -> _T:
        attempts = 0
        while True:
            try:
                return operation()
            except RemoteHarnessTransportError as error:
                if not error.transient or attempts >= self._max_reconnects:
                    raise
                attempts += 1
                self._sleep(self._reconnect_delay * attempts)
            except RemoteHarnessError:
                raise
            except Exception:
                raise RemoteHarnessError(f"{label} failed") from None

    def _validate_session(
        self,
        spec: HarnessSessionSpec,
        session: HarnessSession,
    ) -> None:
        if session.session_id != self.session_id:
            raise RemoteHarnessError("remote session_id does not match the Reasoning session")
        if session.capabilities != self.capabilities:
            raise RemoteHarnessError("remote session capability snapshot mismatch")
        if session.target != spec.target:
            raise RemoteHarnessError("remote session substituted a different execution target")
        if session.model != spec.model or session.model_provider != spec.model_provider:
            raise RemoteHarnessError("remote session changed the selected inference route")
        if session.adapter_session_ref is None:
            raise RemoteHarnessError("remote session omitted its opaque adapter reference")

    def _check_session(self, session: HarnessSession) -> str:
        with self._lock:
            if self._session != session:
                raise RemoteHarnessError("remote harness session is not open")
        if session.adapter_session_ref is None:
            raise RemoteHarnessError("remote session has no adapter reference")
        return session.adapter_session_ref

    def _check_active_turn(self, session: HarnessSession, turn_id: str) -> str:
        adapter_ref = self._check_session(session)
        with self._lock:
            if self._active_turn_id != turn_id:
                raise RemoteHarnessError("remote harness turn is not active")
        return adapter_ref

    @staticmethod
    def _validate_event(
        session: HarnessSession,
        request: HarnessTurnRequest,
        event: HarnessEvent,
    ) -> None:
        if event.session_id != session.session_id:
            raise RemoteHarnessError("remote event session identity mismatch")
        if event.turn_id != request.turn_id:
            raise RemoteHarnessError("remote event turn identity mismatch")


class HttpRemoteHarnessClient:
    """HTTP/NDJSON implementation of ``RemoteHarnessClient``.

    ``headers`` is invoked for every request.  It is the only place where an
    authorization token or service credential belongs; returned values are
    never cached, serialized, logged, or included in exceptions by this class.
    """

    def __init__(
        self,
        base_url: str,
        *,
        headers: Callable[[], Mapping[str, str]],
        client: httpx.Client | None = None,
        timeout: float = 60.0,
        allow_insecure: bool = False,
        max_event_bytes: int = 1_000_000,
        max_response_bytes: int = 4_000_000,
    ) -> None:
        parsed = urlsplit(base_url)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("remote harness base_url cannot contain credentials, query, or fragment")
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("remote harness base_url must be an absolute HTTP(S) URL")
        local_http = parsed.hostname in {"127.0.0.1", "::1", "localhost"}
        if parsed.scheme != "https" and not (allow_insecure or local_http):
            raise ValueError("remote harness services require HTTPS unless insecure transport is explicit")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if max_event_bytes < 1 or max_response_bytes < 1:
            raise ValueError("remote harness payload limits must be positive")
        self.base_url = base_url.rstrip("/")
        self._headers = headers
        self._client = client or httpx.Client(timeout=timeout)
        self._max_event_bytes = max_event_bytes
        self._max_response_bytes = max_response_bytes

    def describe(self) -> HarnessCapabilities:
        payload = self._json_request("GET", "/v1/harness")
        return _decode("capabilities", lambda: _capabilities(payload))

    def create_session(
        self,
        spec: HarnessSessionSpec,
        *,
        idempotency_key: str,
    ) -> HarnessSession:
        payload = self._json_request(
            "POST",
            "/v1/sessions",
            json={
                "idempotency_key": idempotency_key,
                "spec": dataclass_dict(spec),
            },
        )
        return _decode("session", lambda: _session(payload))

    def submit_turn(
        self,
        adapter_session_ref: str,
        request: HarnessTurnRequest,
        *,
        idempotency_key: str,
    ) -> RemoteTurnReceipt:
        payload = self._json_request(
            "PUT",
            self._turn_path(adapter_session_ref, request.turn_id),
            json={
                "idempotency_key": idempotency_key,
                "request": dataclass_dict(request),
            },
        )
        return _decode(
            "turn receipt",
            lambda: RemoteTurnReceipt(
                session_id=str(payload["session_id"]),
                turn_id=str(payload["turn_id"]),
                disposition=str(payload["disposition"]),
            ),
        )

    def stream_events(
        self,
        adapter_session_ref: str,
        turn_id: str,
        *,
        after_sequence: int,
    ) -> Iterable[HarnessEvent]:
        path = self._turn_path(adapter_session_ref, turn_id) + "/events"
        try:
            with self._client.stream(
                "GET",
                self.base_url + path,
                params={"after_sequence": after_sequence},
                headers=self._request_headers(accept="application/x-ndjson"),
            ) as response:
                self._raise_for_status(response)
                for line in response.iter_lines():
                    if not line.strip():
                        continue
                    if len(line.encode("utf-8")) > self._max_event_bytes:
                        raise RemoteHarnessTransportError(
                            "remote harness event exceeded the configured size limit",
                            transient=False,
                        )
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError as error:
                        raise RemoteHarnessTransportError(
                            "remote harness event stream returned invalid JSON",
                            transient=False,
                        ) from error
                    if not isinstance(payload, dict):
                        raise RemoteHarnessTransportError(
                            "remote harness event must be a JSON object",
                            transient=False,
                        )
                    yield _decode("event", lambda payload=payload: _event(payload))
        except RemoteHarnessTransportError:
            raise
        except httpx.TransportError:
            raise RemoteHarnessTransportError(
                "remote harness event stream disconnected",
                transient=True,
            ) from None

    def turn_result(
        self,
        adapter_session_ref: str,
        turn_id: str,
    ) -> HarnessTurnResult:
        payload = self._json_request(
            "GET",
            self._turn_path(adapter_session_ref, turn_id) + "/result",
        )
        return _decode("turn result", lambda: _result(payload))

    def interrupt(self, adapter_session_ref: str, turn_id: str) -> bool:
        payload = self._json_request(
            "POST",
            self._turn_path(adapter_session_ref, turn_id) + "/interrupt",
        )
        return bool(payload.get("accepted"))

    def steer(
        self,
        adapter_session_ref: str,
        turn_id: str,
        messages: tuple[Mapping[str, Any], ...],
    ) -> HarnessSteerStatus:
        payload = self._json_request(
            "POST",
            self._turn_path(adapter_session_ref, turn_id) + "/steer",
            json={"messages": [dict(message) for message in messages]},
        )
        return _decode(
            "steering",
            lambda: HarnessSteerStatus(str(payload["status"])),
        )

    def inject_context(
        self,
        adapter_session_ref: str,
        turn_id: str,
        messages: tuple[Mapping[str, Any], ...],
    ) -> bool:
        payload = self._json_request(
            "POST",
            self._turn_path(adapter_session_ref, turn_id) + "/context",
            json={"messages": [dict(message) for message in messages]},
        )
        return bool(payload.get("accepted"))

    def pending_approvals(
        self,
        adapter_session_ref: str,
    ) -> tuple[HarnessApprovalRequest, ...]:
        payload = self._json_request(
            "GET",
            self._session_path(adapter_session_ref) + "/approvals",
        )
        values = payload.get("approvals")
        if not isinstance(values, list):
            raise RemoteHarnessTransportError(
                "remote harness approvals response is invalid",
                transient=False,
            )
        return _decode(
            "approvals",
            lambda: tuple(_approval(value) for value in values),
        )

    def resolve_approval(
        self,
        adapter_session_ref: str,
        request_id: str,
        decision: HarnessApprovalDecision,
    ) -> bool:
        payload = self._json_request(
            "POST",
            self._session_path(adapter_session_ref)
            + "/approvals/"
            + quote(request_id, safe=""),
            json={"decision": decision.value},
        )
        return bool(payload.get("accepted"))

    def release_session(self, adapter_session_ref: str) -> None:
        self._json_request("DELETE", self._session_path(adapter_session_ref))

    def close(self) -> None:
        self._client.close()

    def _session_path(self, adapter_session_ref: str) -> str:
        return "/v1/sessions/" + quote(adapter_session_ref, safe="")

    def _turn_path(self, adapter_session_ref: str, turn_id: str) -> str:
        return self._session_path(adapter_session_ref) + "/turns/" + quote(turn_id, safe="")

    def _request_headers(self, *, accept: str = "application/json") -> dict[str, str]:
        try:
            supplied = {
                str(key): str(value)
                for key, value in self._headers().items()
            }
        except Exception:
            raise RemoteHarnessTransportError(
                "remote harness authorization headers are unavailable",
                transient=True,
            ) from None
        return {"Accept": accept, "Content-Type": "application/json", **supplied}

    def _json_request(
        self,
        method: str,
        path: str,
        *,
        json: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            response = self._client.request(
                method,
                self.base_url + path,
                headers=self._request_headers(),
                json=json,
            )
            self._raise_for_status(response)
        except RemoteHarnessTransportError:
            raise
        except httpx.TransportError:
            raise RemoteHarnessTransportError(
                "remote harness request failed",
                transient=True,
            ) from None
        if len(response.content) > self._max_response_bytes:
            raise RemoteHarnessTransportError(
                "remote harness response exceeded the configured size limit",
                transient=False,
            )
        try:
            payload = response.json()
        except ValueError as error:
            raise RemoteHarnessTransportError(
                "remote harness response returned invalid JSON",
                transient=False,
            ) from error
        if not isinstance(payload, dict):
            raise RemoteHarnessTransportError(
                "remote harness response must be a JSON object",
                transient=False,
            )
        return payload

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError:
            status = response.status_code
            raise RemoteHarnessTransportError(
                f"remote harness returned HTTP {status}",
                transient=status in _RETRYABLE_STATUS_CODES,
            ) from None


def _capabilities(value: Mapping[str, Any]) -> HarnessCapabilities:
    return HarnessCapabilities(
        harness_id=str(value["harness_id"]),
        display_name=str(value["display_name"]),
        adapter_version=str(value["adapter_version"]),
        transport=HarnessTransport(str(value["transport"])),
        prompt_fidelity=PromptFidelity(str(value["prompt_fidelity"])),
        protocol_version=str(value.get("protocol_version", "1")),
        session_modes=tuple(SessionMode(item) for item in value.get("session_modes", ("fresh",))),
        workspace_access=tuple(WorkspaceAccess(item) for item in value.get("workspace_access", ("none",))),
        reasoning_efforts=tuple(str(item) for item in value.get("reasoning_efforts", ())),
        reasoning_visibility=ReasoningVisibility(str(value.get("reasoning_visibility", "none"))),
        supports_streaming=bool(value.get("supports_streaming", True)),
        supports_tools=bool(value.get("supports_tools", False)),
        supports_skills=bool(value.get("supports_skills", False)),
        supports_structured_output=bool(value.get("supports_structured_output", False)),
        supports_mcp=bool(value.get("supports_mcp", False)),
        supports_approvals=bool(value.get("supports_approvals", False)),
        supports_interrupt=bool(value.get("supports_interrupt", False)),
        supports_steering=bool(value.get("supports_steering", False)),
        supports_checkpoints=bool(value.get("supports_checkpoints", False)),
        supports_subagents=bool(value.get("supports_subagents", False)),
        supports_context_compaction=bool(value.get("supports_context_compaction", False)),
        reports_usage=bool(value.get("reports_usage", False)),
        reports_cost=bool(value.get("reports_cost", False)),
        model_patterns=tuple(str(item) for item in value.get("model_patterns", ("*",))),
        model_options=tuple(str(item) for item in value.get("model_options", ())),
    )


def _target(value: Mapping[str, Any]) -> ExecutionTargetCapabilities:
    return ExecutionTargetCapabilities(
        target_id=str(value["target_id"]),
        display_name=str(value["display_name"]),
        kind=ExecutionTargetKind(str(value["kind"])),
        isolation=TargetIsolation(str(value["isolation"])),
        workspace_access=tuple(WorkspaceAccess(item) for item in value.get("workspace_access", ("none",))),
        network_access=tuple(NetworkAccess(item) for item in value.get("network_access", ("denied",))),
        supports_processes=bool(value.get("supports_processes", False)),
        durable_workspace=bool(value.get("durable_workspace", False)),
    )


def _session(value: Mapping[str, Any]) -> HarnessSession:
    return HarnessSession(
        session_id=str(value["session_id"]),
        capabilities=_capabilities(_mapping(value["capabilities"], "capabilities")),
        target=_target(_mapping(value["target"], "target")),
        model=str(value["model"]),
        model_provider=(str(value["model_provider"]) if value.get("model_provider") is not None else None),
        adapter_session_ref=(str(value["adapter_session_ref"]) if value.get("adapter_session_ref") is not None else None),
    )


def _event(value: Mapping[str, Any]) -> HarnessEvent:
    return HarnessEvent(
        session_id=str(value["session_id"]),
        turn_id=(str(value["turn_id"]) if value.get("turn_id") is not None else None),
        sequence=int(value["sequence"]),
        kind=HarnessEventKind(str(value["kind"])),
        at=str(value["at"]),
        data=dict(_mapping(value.get("data", {}), "event data")),
    )


def _result(value: Mapping[str, Any]) -> HarnessTurnResult:
    return HarnessTurnResult(
        session_id=str(value["session_id"]),
        turn_id=str(value["turn_id"]),
        status=str(value["status"]),
        output_text=str(value.get("output_text", "")),
        structured_output=value.get("structured_output"),
        usage=dict(_mapping(value.get("usage", {}), "usage")),
        cost=dict(_mapping(value.get("cost", {}), "cost")),
        checkpoint=(dict(_mapping(value["checkpoint"], "checkpoint")) if value.get("checkpoint") is not None else None),
        error=(str(value["error"]) if value.get("error") is not None else None),
        metadata=dict(_mapping(value.get("metadata", {}), "metadata")),
    )


def _approval(value: Any) -> HarnessApprovalRequest:
    item = _mapping(value, "approval")
    return HarnessApprovalRequest(
        request_id=str(item["request_id"]),
        kind=str(item["kind"]),
        command=(str(item["command"]) if item.get("command") is not None else None),
        cwd=(str(item["cwd"]) if item.get("cwd") is not None else None),
        reason=(str(item["reason"]) if item.get("reason") is not None else None),
        created_at=(float(item["created_at"]) if item.get("created_at") is not None else None),
        expires_at=(float(item["expires_at"]) if item.get("expires_at") is not None else None),
    )


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RemoteHarnessTransportError(
            f"remote harness {label} must be a JSON object",
            transient=False,
        )
    return value


def _decode(label: str, decoder: Callable[[], _T]) -> _T:
    """Decode untrusted service data without reflecting it into an error."""

    try:
        return decoder()
    except RemoteHarnessTransportError:
        raise
    except (KeyError, TypeError, ValueError):
        raise RemoteHarnessTransportError(
            f"remote harness {label} response is invalid",
            transient=False,
        ) from None
