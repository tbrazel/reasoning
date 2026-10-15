"""Provider-neutral, credential-free reference for scoped Cloud computation.

The connector consumes an alpha.1 verifier record and reauthorizes an already
resolved, exactly priced Desktop route with its originating planner.
Authentication wire format is deliberately absent: callers inject a transport,
while this layer enforces audience, route, project, expiry, revocation, replay,
one-job, event-cursor, and credential-exclusion rules.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol
from urllib.parse import urlsplit

from .desktop_hybrid import (
    PLATFORM_VERSION,
    AcceptedContractRecord,
    DesktopHybridPlanner,
    DesktopHybridRoute,
    HybridPlanningError,
)

JsonObject = dict[str, Any]
ContractValidator = Callable[[str, JsonObject], None]

_DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")
_NONCE = re.compile(r"^[A-Za-z0-9._:-]{16,255}$")
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:-]{16,255}$")
_EXACT_ACTIONS = ("submit", "read", "events", "artifacts", "cancel")
_GRANT_POLICY_KEYS = (
    "connector_grant_id",
    "issued_at",
    "expires_at",
    "audience",
    "binding",
    "capability_set",
    "budget",
    "replay_protection",
    "presentation",
)
_SECRET_KEYS = {
    "access_token",
    "account_credential",
    "api_key",
    "authorization",
    "bearer",
    "bearer_token",
    "cookie",
    "inference_credential",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "session_token",
}
_SECRET_VALUE_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}"),
    re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
    re.compile(
        r"\b(?:api[_-]?key|access[_-]?token|password|secret)\s*[:=]\s*[^\s,;]{8,}",
        re.IGNORECASE,
    ),
)
_TERMINAL_STATES = frozenset({"succeeded", "failed", "cancelled"})


class ConnectorError(RuntimeError):
    """Base error for scoped connector failures."""


class ConnectorGrantRejected(ConnectorError):
    """The grant or request is out of scope, expired, or revoked."""


class ConnectorReplayRejected(ConnectorGrantRejected):
    """A nonce or one-job grant was reused outside its allowed identity."""


class ConnectorCredentialLeak(ConnectorGrantRejected):
    """Reusable account or inference material reached the connector boundary."""


class ConnectorTransportDisconnected(ConnectorError):
    """Injected transport interruption; operation state remains unchanged."""


class ConnectorReconnectError(ConnectorError):
    """Event replay violated the exclusive opaque-cursor contract."""


class ConnectorAction(StrEnum):
    SUBMIT = "submit"
    READ = "read"
    EVENTS = "events"
    ARTIFACTS = "artifacts"
    CANCEL = "cancel"


@dataclass(frozen=True)
class ConnectorRequest:
    """Fresh request proof metadata; no bearer or reusable credential value."""

    audience: str
    nonce: str
    idempotency_key: str


@dataclass(frozen=True)
class ConnectorCall:
    connector_grant_id: str
    audience: str
    action: str
    request_nonce: str
    idempotency_key: str
    operation_id: str
    route_plan_id: str
    job_id: str | None

    def to_dict(self) -> JsonObject:
        value: JsonObject = {
            "connector_grant_id": self.connector_grant_id,
            "audience": self.audience,
            "action": self.action,
            "request_nonce": self.request_nonce,
            "idempotency_key": self.idempotency_key,
            "operation_id": self.operation_id,
            "route_plan_id": self.route_plan_id,
        }
        if self.job_id is not None:
            value["job_id"] = self.job_id
        return value


@dataclass(frozen=True)
class EventPage:
    events: tuple[JsonObject, ...]
    next_cursor: str | None
    complete: bool


@dataclass(frozen=True)
class ComputeOperationSnapshot:
    job_id: str
    idempotency_key: str
    state: str
    last_event_cursor: str | None
    last_sequence: int
    event_ids: tuple[str, ...]
    committed_artifact_ids: tuple[str, ...]


class ExecutorTransport(Protocol):
    """Injected Cloud Executor operations; production authentication is external."""

    def submit_job(self, call: JsonObject, job_spec: JsonObject) -> JsonObject: ...

    def get_job(self, call: JsonObject, job_id: str) -> JsonObject: ...

    def list_job_events(self, call: JsonObject, job_id: str, *, after: str | None, limit: int) -> JsonObject: ...

    def download_artifact(self, call: JsonObject, artifact_id: str) -> tuple[bytes, str]: ...

    def cancel_job(self, call: JsonObject, job_id: str, *, idempotency_key: str) -> JsonObject: ...


class ReplayStore(Protocol):
    def consume(
        self,
        grant_id: str,
        nonce: str,
        *,
        now: datetime,
        expires_at: datetime,
    ) -> None: ...


class RevocationStore(Protocol):
    def is_revoked(self, grant_id: str) -> bool: ...


class GrantUseStore(Protocol):
    def bind(self, grant_id: str, binding: tuple[str, str, str]) -> None: ...


class InMemoryReplayStore:
    """Atomic reference nonce store; deployments replace this with durable state."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._expires: dict[tuple[str, str], datetime] = {}

    def consume(
        self,
        grant_id: str,
        nonce: str,
        *,
        now: datetime,
        expires_at: datetime,
    ) -> None:
        key = (grant_id, nonce)
        with self._lock:
            self._expires = {observed: expiry for observed, expiry in self._expires.items() if expiry > now}
            if key in self._expires:
                raise ConnectorReplayRejected("request nonce has already been consumed")
            self._expires[key] = expires_at


class InMemoryRevocationStore:
    """Immediate process-local revocation reference for offline fixtures."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._revoked: set[str] = set()

    def revoke(self, grant_id: str) -> None:
        with self._lock:
            self._revoked.add(grant_id)

    def is_revoked(self, grant_id: str) -> bool:
        with self._lock:
            return grant_id in self._revoked


class InMemoryGrantUseStore:
    """Atomic one-job binding reference; deployments replace with durable state."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._bindings: dict[str, tuple[str, str, str]] = {}

    def bind(self, grant_id: str, binding: tuple[str, str, str]) -> None:
        with self._lock:
            existing = self._bindings.get(grant_id)
            if existing is not None and existing != binding:
                raise ConnectorReplayRejected("one-job connector grant is already bound")
            self._bindings[grant_id] = binding


@dataclass
class _OperationState:
    job_id: str
    idempotency_key: str
    spec_digest: str
    state: str
    event_state: str
    projection_version: int
    last_event_cursor: str | None
    last_sequence: int
    event_digests: dict[str, str]
    artifact_commitments: dict[str, tuple[str, int]]

    def snapshot(self) -> ComputeOperationSnapshot:
        return ComputeOperationSnapshot(
            job_id=self.job_id,
            idempotency_key=self.idempotency_key,
            state=self.state,
            last_event_cursor=self.last_event_cursor,
            last_sequence=self.last_sequence,
            event_ids=tuple(self.event_digests),
            committed_artifact_ids=tuple(self.artifact_commitments),
        )


class ScopedComputeConnector:
    """Enforce one alpha.1 local-harness grant around an injected transport."""

    def __init__(
        self,
        *,
        grant: AcceptedContractRecord,
        planner: DesktopHybridPlanner,
        route: DesktopHybridRoute,
        transport: ExecutorTransport,
        validator: ContractValidator,
        clock: Callable[[], datetime] | None = None,
        replay_store: ReplayStore | None = None,
        revocations: RevocationStore | None = None,
        grant_uses: GrantUseStore | None = None,
    ) -> None:
        if not getattr(route, "dispatch_permitted", False):
            raise ConnectorGrantRejected("released pre-dispatch pricing forbids connector construction")
        if grant.schema_id != "reasoning.platform.local-harness-connector":
            raise ConnectorGrantRejected("connector requires a T007 verifier record")
        try:
            route = planner.reauthorize_connector_route(route)
        except (AttributeError, HybridPlanningError) as error:
            raise ConnectorGrantRejected(
                "connector requires an exact planner-issued Desktop route reauthorized from accepted records"
            ) from error
        self._grant = grant.document()
        self._route = route
        self._transport = transport
        self._validator = validator
        self._clock = clock if clock is not None else (lambda: datetime.now(UTC))
        self._replay = replay_store if replay_store is not None else InMemoryReplayStore()
        self._revocations = revocations if revocations is not None else InMemoryRevocationStore()
        self._grant_uses = grant_uses if grant_uses is not None else InMemoryGrantUseStore()
        self._operations_lock = threading.Lock()
        self._operations: dict[str, _OperationState] = {}
        self._validate_static_grant()

    @property
    def audience(self) -> str:
        return str(self._grant["audience"])

    @property
    def grant_id(self) -> str:
        return str(self._grant["connector_grant_id"])

    def submit(self, job_spec: JsonObject, request: ConnectorRequest) -> ComputeOperationSnapshot:
        self._reject_credentials(job_spec, allow_workload_reference=True)
        self._validator("job-spec", job_spec)
        self._validate_job(job_spec, request)
        job_id = _required_string(job_spec, "job_id")
        spec_digest = _jcs_digest(job_spec)
        call = self._authorize(
            ConnectorAction.SUBMIT,
            request,
            job_id=job_id,
        )
        binding = (job_id, spec_digest, request.idempotency_key)
        self._grant_uses.bind(self.grant_id, binding)
        status = self._transport.submit_job(call.to_dict(), copy.deepcopy(job_spec))
        self._validate_status(status, job_id, request.idempotency_key, spec_digest=spec_digest)
        if status.get("state") != "queued" or status.get("last_event_cursor") is None:
            raise ConnectorReconnectError("accepted submission must be queued with an initial event cursor")
        with self._operations_lock:
            existing = self._operations.get(job_id)
            if existing is None:
                existing = _OperationState(
                    job_id=job_id,
                    idempotency_key=request.idempotency_key,
                    spec_digest=spec_digest,
                    state=_required_string(status, "state"),
                    event_state="queued",
                    projection_version=0,
                    last_event_cursor=_optional_string(status.get("last_event_cursor")),
                    last_sequence=(1 if status.get("last_event_cursor") is not None else 0),
                    event_digests={},
                    artifact_commitments={},
                )
                self._operations[job_id] = existing
            return existing.snapshot()

    def read(self, job_id: str, request: ConnectorRequest) -> ComputeOperationSnapshot:
        operation = self._operation(job_id)
        before_version = operation.projection_version
        call = self._authorize(ConnectorAction.READ, request, job_id=job_id)
        status = self._transport.get_job(call.to_dict(), job_id)
        self._validate_status(
            status,
            job_id,
            operation.idempotency_key,
            spec_digest=operation.spec_digest,
        )
        with self._operations_lock:
            current = self._operations[job_id]
            if current.projection_version != before_version:
                raise ConnectorReconnectError("concurrent operation projection changed during status read")
            observed_state = _required_string(status, "state")
            _reject_status_regression(current.state, observed_state)
            if current.state != observed_state:
                current.state = observed_state
                current.projection_version += 1
            return current.snapshot()

    def reconnect_events(
        self,
        job_id: str,
        request: ConnectorRequest,
        *,
        limit: int = 100,
    ) -> EventPage:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
            raise ConnectorReconnectError("event page limit must be between 1 and 1000")
        operation = self._operation(job_id)
        before_version = operation.projection_version
        before_cursor = operation.last_event_cursor
        before_sequence = operation.last_sequence
        before_digests = dict(operation.event_digests)
        before_artifacts = dict(operation.artifact_commitments)
        call = self._authorize(ConnectorAction.EVENTS, request, job_id=job_id)
        page = self._transport.list_job_events(call.to_dict(), job_id, after=before_cursor, limit=limit)
        events, next_cursor, complete = self._validate_event_page(
            page,
            job_id=job_id,
            after=before_cursor,
            limit=limit,
            last_sequence=before_sequence,
            known_events=before_digests,
            event_state=operation.event_state,
        )
        new_digests = dict(before_digests)
        new_artifacts = dict(before_artifacts)
        state = operation.state
        event_state = operation.event_state
        sequence = before_sequence
        for event in events:
            event_id = _required_string(event, "event_id")
            digest = _jcs_digest(event)
            known = new_digests.get(event_id)
            if known is not None:
                if known != digest:
                    raise ConnectorReconnectError("an event_id was replayed with different content")
                continue
            observed_sequence = event.get("sequence")
            assert isinstance(observed_sequence, int)
            if observed_sequence <= sequence:
                raise ConnectorReconnectError("new event sequences must increase")
            sequence = observed_sequence
            new_digests[event_id] = digest
            observed_state = _required_string(event, "state")
            _reject_event_state_regression(event_state, observed_state)
            event_state = observed_state
            state = _merge_projection_state(state, observed_state)
            if event.get("type") == "artifact.committed":
                payload = event.get("payload")
                if not isinstance(payload, dict):
                    raise ConnectorReconnectError("artifact event lacks a payload")
                artifact_id = _required_string(payload, "artifact_id")
                artifact_digest = _required_string(payload, "digest")
                if not _DIGEST.fullmatch(artifact_digest):
                    raise ConnectorReconnectError("artifact event has an invalid digest")
                artifact_size = payload.get("size_bytes")
                if not isinstance(artifact_size, int) or isinstance(artifact_size, bool) or artifact_size < 0:
                    raise ConnectorReconnectError("artifact event has an invalid size")
                existing = new_artifacts.get(artifact_id)
                commitment = (artifact_digest, artifact_size)
                if existing is not None and existing != commitment:
                    raise ConnectorReconnectError("artifact identity changed during replay")
                new_artifacts[artifact_id] = commitment

        with self._operations_lock:
            current = self._operations[job_id]
            if current.projection_version != before_version:
                raise ConnectorReconnectError("concurrent operation projection changed during event replay")
            current.last_event_cursor = next_cursor if next_cursor is not None else before_cursor
            current.last_sequence = sequence
            current.event_state = event_state
            current.event_digests = new_digests
            current.artifact_commitments = new_artifacts
            current.state = state
            current.projection_version += 1
        return EventPage(tuple(copy.deepcopy(events)), next_cursor, complete)

    def download_artifact(
        self,
        job_id: str,
        artifact_id: str,
        request: ConnectorRequest,
    ) -> bytes:
        operation = self._operation(job_id)
        expected = operation.artifact_commitments.get(artifact_id)
        if expected is None:
            raise ConnectorGrantRejected("artifact is not committed for this operation")
        call = self._authorize(ConnectorAction.ARTIFACTS, request, job_id=job_id)
        payload, declared_digest = self._transport.download_artifact(call.to_dict(), artifact_id)
        observed = f"sha256:{hashlib.sha256(payload).hexdigest()}"
        expected_digest, expected_size = expected
        if declared_digest != expected_digest or observed != expected_digest or len(payload) != expected_size:
            raise ConnectorReconnectError("downloaded artifact failed digest verification")
        return payload

    def cancel(self, job_id: str, request: ConnectorRequest) -> ComputeOperationSnapshot:
        operation = self._operation(job_id)
        before_version = operation.projection_version
        call = self._authorize(ConnectorAction.CANCEL, request, job_id=job_id)
        status = self._transport.cancel_job(call.to_dict(), job_id, idempotency_key=request.idempotency_key)
        self._validate_status(
            status,
            job_id,
            operation.idempotency_key,
            spec_digest=operation.spec_digest,
        )
        with self._operations_lock:
            current = self._operations[job_id]
            if current.projection_version != before_version:
                raise ConnectorReconnectError("concurrent operation projection changed during cancellation")
            observed_state = _required_string(status, "state")
            _reject_status_regression(current.state, observed_state)
            if current.state != observed_state:
                current.state = observed_state
                current.projection_version += 1
            return current.snapshot()

    def operation(self, job_id: str) -> ComputeOperationSnapshot:
        return self._operation(job_id).snapshot()

    def _operation(self, job_id: str) -> _OperationState:
        with self._operations_lock:
            operation = self._operations.get(job_id)
            if operation is None:
                raise ConnectorGrantRejected("job is not bound to this connector grant")
            return copy.deepcopy(operation)

    def _authorize(
        self,
        action: ConnectorAction,
        request: ConnectorRequest,
        *,
        job_id: str | None,
    ) -> ConnectorCall:
        now = self._now()
        issued_at = _parse_time(self._grant["issued_at"], "issued_at")
        expires_at = _parse_time(self._grant["expires_at"], "expires_at")
        if now < issued_at:
            raise ConnectorGrantRejected("connector grant is not active yet")
        if now >= expires_at:
            raise ConnectorGrantRejected("connector grant has expired")
        if self._grant["revocation"]["revoked_at"] is not None or self._revocations.is_revoked(self.grant_id):
            raise ConnectorGrantRejected("connector grant is revoked")
        if request.audience != self.audience:
            raise ConnectorGrantRejected("connector audience mismatch")
        if not _NONCE.fullmatch(request.nonce):
            raise ConnectorGrantRejected("request nonce must be a fresh bounded value")
        if not _IDEMPOTENCY_KEY.fullmatch(request.idempotency_key):
            raise ConnectorGrantRejected("request idempotency key is invalid")
        if action.value not in self._grant["capability_set"]["actions"]:
            raise ConnectorGrantRejected("connector action is outside the compute-only grant")
        nonce_ttl = int(self._grant["replay_protection"]["nonce_ttl_seconds"])
        nonce_expiry = min(
            expires_at,
            datetime.fromtimestamp(now.timestamp() + nonce_ttl, tz=UTC),
        )
        self._replay.consume(
            self.grant_id,
            request.nonce,
            now=now,
            expires_at=nonce_expiry,
        )
        call = ConnectorCall(
            connector_grant_id=self.grant_id,
            audience=request.audience,
            action=action.value,
            request_nonce=request.nonce,
            idempotency_key=request.idempotency_key,
            operation_id=self._route.operation_id,
            route_plan_id=self._route.route_plan_id,
            job_id=job_id,
        )
        self._reject_credentials(call.to_dict())
        return call

    def _validate_static_grant(self) -> None:
        grant = self._grant
        self._reject_credentials(grant)
        self._validator("local-harness-connector", grant)
        if grant.get("schema_version") != PLATFORM_VERSION:
            raise ConnectorGrantRejected("unsupported connector contract version")
        audience = grant.get("audience")
        if not isinstance(audience, str):
            raise ConnectorGrantRejected("connector audience is required")
        parsed_audience = urlsplit(audience)
        if (
            parsed_audience.scheme != "https"
            or not parsed_audience.hostname
            or parsed_audience.username is not None
            or parsed_audience.password is not None
            or parsed_audience.query
            or parsed_audience.fragment
        ):
            raise ConnectorGrantRejected("connector audience must be an exact HTTPS endpoint")
        issued_at = _parse_time(grant.get("issued_at"), "issued_at")
        expires_at = _parse_time(grant.get("expires_at"), "expires_at")
        lifetime = (expires_at - issued_at).total_seconds()
        if not 0 < lifetime <= 900:
            raise ConnectorGrantRejected("connector lifetime must be between 1 and 900 seconds")

        policy = {key: grant.get(key) for key in _GRANT_POLICY_KEYS}
        policy_digest = _jcs_digest(policy)
        if grant.get("grant_policy_digest") != policy_digest:
            raise ConnectorGrantRejected("connector policy digest mismatch")
        binding = _required_mapping(grant, "binding")
        expected_binding = {
            "actor": {
                "type": self._route.actor_type,
                "id": self._route.actor_id,
            },
            "tenant_id": self._route.tenant_id,
            "project_id": self._route.project_id,
            "operation_id": self._route.operation_id,
            "route_plan_id": self._route.route_plan_id,
            "harness_id": self._route.harness_id,
            "target_id": self._route.compute_target_id,
            "max_jobs": 1,
        }
        if binding != expected_binding:
            raise ConnectorGrantRejected("connector binding does not match the exact route")
        if self._route.connector_grant_id != grant.get("connector_grant_id"):
            raise ConnectorGrantRejected("route does not select this connector grant")
        if not self._route.connector_required:
            raise ConnectorGrantRejected("connector is only valid for local-harness hosted-compute routes")

        capabilities = grant.get("capability_set")
        expected_capabilities = {
            "scope": "reasoning.executor.compute-job-lifecycle",
            "actions": list(_EXACT_ACTIONS),
            "account_access": False,
            "billing_access": False,
            "inference_access": False,
            "project_export_access": False,
        }
        if capabilities != expected_capabilities:
            raise ConnectorGrantRejected("connector is not an exact least-privilege compute grant")
        expected_presentation = {
            "exchangeable": False,
            "refreshable": False,
            "reusable_account_material": False,
            "inference_material": False,
        }
        if grant.get("presentation") != expected_presentation:
            raise ConnectorGrantRejected("connector presentation permits reusable material")
        replay = grant.get("replay_protection")
        if (
            not isinstance(replay, dict)
            or replay.get("request_nonce_required") is not True
            or replay.get("idempotency_key_required") is not True
        ):
            raise ConnectorGrantRejected("connector replay protection is incomplete")
        nonce_ttl = replay.get("nonce_ttl_seconds")
        if not isinstance(nonce_ttl, int) or isinstance(nonce_ttl, bool) or not 1 <= nonce_ttl <= 300:
            raise ConnectorGrantRejected("connector nonce TTL is invalid")
        revocation = grant.get("revocation")
        if not isinstance(revocation, dict) or revocation.get("supported") is not True:
            raise ConnectorGrantRejected("connector must support immediate revocation")
        approval = grant.get("approval")
        if (
            not isinstance(approval, dict)
            or approval.get("scope") != "compute_connector"
            or approval.get("actor") != expected_binding["actor"]
            or approval.get("approved_grant_policy_digest") != policy_digest
        ):
            raise ConnectorGrantRejected("connector approval does not bind the policy and actor")

    def _validate_job(self, job: JsonObject, request: ConnectorRequest) -> None:
        if job.get("schema_version") != PLATFORM_VERSION:
            raise ConnectorGrantRejected("unsupported Executor job contract")
        if job.get("actor") != {
            "type": self._route.actor_type,
            "id": self._route.actor_id,
        }:
            raise ConnectorGrantRejected("job actor is outside the connector binding")
        for key, expected in (
            ("tenant_id", self._route.tenant_id),
            ("project_id", self._route.project_id),
            ("idempotency_key", request.idempotency_key),
        ):
            if job.get(key) != expected:
                raise ConnectorGrantRejected(f"job {key} is outside the connector binding")
        expected_executor = {
            "adapter_id": self._route.executor_adapter_id,
            "adapter_version": self._route.executor_adapter_version,
            "target_kind": self._route.executor_target_kind,
            "target_id": self._route.compute_target_id,
        }
        if job.get("executor") != expected_executor:
            raise ConnectorGrantRejected("job Executor target substitution")
        expected_image = {
            "repository": self._route.image_repository,
            "digest": self._route.image_digest,
            "capabilities_digest": self._route.capabilities_digest,
            "attestation_digest": self._route.attestation_digest,
        }
        if job.get("image") != expected_image:
            raise ConnectorGrantRejected("job image substitution")
        workload = job.get("workload_identity")
        expected_workload = {
            "audience": self.audience,
            "credential_reference": f"connector-grant://{self.grant_id}",
            "expires_at": self._grant["expires_at"],
        }
        if workload != expected_workload:
            raise ConnectorGrantRejected("job workload identity is not the scoped connector reference")
        deadline = _parse_time(job.get("deadline"), "job deadline")
        if deadline > _parse_time(self._grant["expires_at"], "expires_at"):
            raise ConnectorGrantRejected("job deadline exceeds the connector grant")

        approved = {artifact_id: (digest, purpose) for artifact_id, digest, _size, purpose in self._route.transfer_inputs}
        inputs = job.get("inputs")
        if not isinstance(inputs, list):
            raise ConnectorGrantRejected("job inputs must be an array")
        seen: set[str] = set()
        for item in inputs:
            if not isinstance(item, dict):
                raise ConnectorGrantRejected("job input must be an object")
            artifact_id = item.get("artifact_id")
            if not isinstance(artifact_id, str) or artifact_id in seen:
                raise ConnectorGrantRejected("job input artifact ids must be unique")
            seen.add(artifact_id)
            accepted = approved.get(artifact_id)
            if accepted is None or accepted[1] not in {"compute_input", "both"} or item.get("digest") != accepted[0]:
                raise ConnectorGrantRejected("job input is outside the exact approved transfer")
        required_inputs = {artifact_id for artifact_id, (_digest, purpose) in approved.items() if purpose in {"compute_input", "both"}}
        if seen != required_inputs:
            raise ConnectorGrantRejected("job inputs do not equal the exact approved compute transfer")

    def _validate_status(
        self,
        status: JsonObject,
        job_id: str,
        idempotency_key: str,
        *,
        spec_digest: str,
    ) -> None:
        self._reject_credentials(status)
        self._validator("job-status", status)
        if status.get("job_id") != job_id:
            raise ConnectorReconnectError("Executor status job substitution")
        if status.get("idempotency_key") != idempotency_key:
            raise ConnectorReconnectError("Executor status idempotency substitution")
        if status.get("spec_digest") != spec_digest:
            raise ConnectorReconnectError("Executor status specification substitution")

    def _validate_event_page(
        self,
        page: JsonObject,
        *,
        job_id: str,
        after: str | None,
        limit: int,
        last_sequence: int,
        known_events: dict[str, str],
        event_state: str,
    ) -> tuple[list[JsonObject], str | None, bool]:
        if not isinstance(page, dict) or set(page) != {"events", "next_cursor", "complete"}:
            raise ConnectorReconnectError("event page has an unsupported shape")
        events = page["events"]
        next_cursor = page["next_cursor"]
        complete = page["complete"]
        if not isinstance(events, list) or len(events) > limit:
            raise ConnectorReconnectError("event page exceeds the requested limit")
        if next_cursor is not None and (not isinstance(next_cursor, str) or not next_cursor or len(next_cursor) > 2048):
            raise ConnectorReconnectError("event page has an invalid opaque cursor")
        if not isinstance(complete, bool):
            raise ConnectorReconnectError("event page complete flag must be boolean")
        if not complete and (next_cursor is None or next_cursor == after):
            raise ConnectorReconnectError("incomplete event page did not advance its cursor")
        page_ids: set[str] = set()
        sequence = last_sequence
        saw_new = False
        projected_event_state = event_state
        for event in events:
            if not isinstance(event, dict):
                raise ConnectorReconnectError("event page item must be an object")
            self._reject_credentials(event)
            self._validator("job-event", event)
            if event.get("job_id") != job_id:
                raise ConnectorReconnectError("event job substitution")
            event_id = _required_string(event, "event_id")
            if event_id in page_ids:
                raise ConnectorReconnectError("event page contains duplicate event ids")
            page_ids.add(event_id)
            observed_sequence = event.get("sequence")
            if not isinstance(observed_sequence, int) or isinstance(observed_sequence, bool):
                raise ConnectorReconnectError("event sequence must be an integer")
            known = known_events.get(event_id)
            if known is not None:
                if saw_new:
                    raise ConnectorReconnectError("exact event replays must precede new events")
                if known != _jcs_digest(event):
                    raise ConnectorReconnectError("replayed event content changed")
                continue
            if observed_sequence <= sequence:
                raise ConnectorReconnectError("exclusive cursor returned an older new event")
            observed_state = _required_string(event, "state")
            _reject_event_state_regression(projected_event_state, observed_state)
            projected_event_state = observed_state
            saw_new = True
            sequence = observed_sequence
        if events and next_cursor is None:
            raise ConnectorReconnectError("event page with events must return a next cursor")
        return events, next_cursor, complete

    def _reject_credentials(
        self,
        value: Any,
        path: str = "$",
        *,
        allow_workload_reference: bool = False,
    ) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                normalized = key.lower()
                if normalized in _SECRET_KEYS:
                    raise ConnectorCredentialLeak(f"reusable credential field is forbidden at {path}.{key}")
                if normalized == "credential_reference":
                    allowed = allow_workload_reference and child == f"connector-grant://{self.grant_id}"
                    if not allowed:
                        raise ConnectorCredentialLeak("only the scoped connector credential reference may cross the boundary")
                self._reject_credentials(
                    child,
                    f"{path}.{key}",
                    allow_workload_reference=allow_workload_reference,
                )
        elif isinstance(value, list):
            for index, child in enumerate(value):
                self._reject_credentials(
                    child,
                    f"{path}[{index}]",
                    allow_workload_reference=allow_workload_reference,
                )
        elif isinstance(value, str) and any(pattern.search(value) for pattern in _SECRET_VALUE_PATTERNS):
            raise ConnectorCredentialLeak(f"credential-like material is forbidden at {path}")

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ConnectorGrantRejected("connector clock must return a timezone-aware datetime")
        return value.astimezone(UTC)


def _required_mapping(value: JsonObject, key: str) -> JsonObject:
    observed = value.get(key)
    if not isinstance(observed, dict):
        raise ConnectorGrantRejected(f"{key} must be an object")
    return observed


def _required_string(value: JsonObject, key: str) -> str:
    observed = value.get(key)
    if not isinstance(observed, str) or not observed:
        raise ConnectorReconnectError(f"{key} must be a non-empty string")
    return observed


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ConnectorReconnectError("cursor must be a non-empty string or null")
    return value


def _reject_status_regression(current: str, observed: str) -> None:
    allowed = {
        "queued": {"queued", "provisioning", "running", "outcome_unknown", *_TERMINAL_STATES},
        "provisioning": {"provisioning", "running", "outcome_unknown", *_TERMINAL_STATES},
        "running": {"running", "outcome_unknown", *_TERMINAL_STATES},
        "outcome_unknown": {"outcome_unknown", *_TERMINAL_STATES},
        "succeeded": {"succeeded"},
        "failed": {"failed"},
        "cancelled": {"cancelled"},
    }
    if observed not in allowed.get(current, set()):
        raise ConnectorReconnectError("Executor status regressed the operation state")


def _reject_event_state_regression(current: str, observed: str) -> None:
    if current in _TERMINAL_STATES:
        raise ConnectorReconnectError("new event followed a terminal event state")
    allowed = {
        "queued": {"queued", "provisioning", "running", "outcome_unknown", *_TERMINAL_STATES},
        "provisioning": {"provisioning", "running", "outcome_unknown", *_TERMINAL_STATES},
        "running": {"running", "outcome_unknown", *_TERMINAL_STATES},
        "outcome_unknown": {"outcome_unknown", *_TERMINAL_STATES},
    }
    if observed not in allowed.get(current, set()):
        raise ConnectorReconnectError("Executor event regressed the event state")


def _merge_projection_state(current: str, observed: str) -> str:
    if current in _TERMINAL_STATES:
        if observed in _TERMINAL_STATES and observed != current:
            raise ConnectorReconnectError("Executor reported conflicting terminal outcomes")
        return current
    if observed in _TERMINAL_STATES:
        return observed
    rank = {"queued": 0, "provisioning": 1, "running": 2, "outcome_unknown": 3}
    if current not in rank or observed not in rank:
        raise ConnectorReconnectError("Executor reported an unsupported operation state")
    return observed if rank[observed] > rank[current] else current


def _parse_time(value: Any, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as error:
        raise ConnectorGrantRejected(f"invalid {label}") from error
    if parsed.tzinfo is None:
        raise ConnectorGrantRejected(f"{label} must be timezone-aware")
    return parsed.astimezone(UTC)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _jcs_digest(value: Any) -> str:
    return f"sha256:{hashlib.sha256(_canonical_json(value)).hexdigest()}"
