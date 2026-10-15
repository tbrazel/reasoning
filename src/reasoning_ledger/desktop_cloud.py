"""Desktop-owned wiring for the public, post-authentication Cloud boundary.

This module deliberately supplies no authentication, credentials, endpoint,
or transport. A provider-only caller may inject the canonical Cloud session
and client protocols, while the private authentication seam atomically injects
those protocols together with the canonical product-policy client. Without an
injected boundary every Desktop process starts disconnected and Cloud
mutations fail closed.
"""

from __future__ import annotations

import base64
import binascii
import copy
import json
import os
import re
import stat
import threading
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .cloud_client import (
    MAX_PROJECT_CONTENT_BYTES,
    AcceptedCloudClientRecord,
    CloudClient,
    CloudClientContractError,
    CloudClientContractValidator,
    CloudClientStateError,
    CloudSessionProvider,
    ContractRecordReference,
    EventReplayState,
    VerifiedArtifact,
    accept_event_page,
    byte_digest,
    canonical_cloud_client_validator,
    idempotency_key_digest,
    jcs_digest,
    require_credential_free,
)
from .cloud_product_policy import (
    MAX_CLOUD_PRODUCT_POLICY_RECORD_BYTES,
    AcceptedCloudProductPolicyRecord,
    AccountPolicyAuthority,
    CloudProductPolicyClient,
    CloudProductPolicyContractError,
    CloudProductPolicyIdempotencyConflict,
    CloudProductPolicyScopeError,
    CloudProductPolicyValidator,
    canonical_optional_use_request_digest,
    digest_idempotency_key,
)
from .harnesses.desktop_hybrid import (
    AcceptedContractRecord,
    DesktopCapabilityRequirements,
    DesktopHybridPlanner,
    DesktopHybridPlannerFactory,
    DesktopHybridRoute,
    DesktopTransferReceiptStore,
    HybridPlanningError,
)

JsonObject = dict[str, Any]

DESKTOP_CLOUD_STATE_VERSION = 1
MAX_DESKTOP_CLOUD_STATE_BYTES = 1_048_576
MAX_CLOUD_PROJECTS = 256
MAX_TRACKED_RUNS = 1024
MAX_REPLAY_EVENTS = 4096
MAX_DESKTOP_ARTIFACT_BYTES = 67_108_864
MAX_POLICY_CONTINUITY_BYTES = 16_777_216
MAX_POLICY_CONTINUITY_REQUESTS = 128

_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]{0,127}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")
_DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")


@runtime_checkable
class DesktopCloudProvider(CloudSessionProvider, CloudClient, Protocol):
    """One already-authenticated provider implementing both public protocols."""


@runtime_checkable
class DesktopCollaborationProvider(Protocol):
    """Private authenticated transport for shared workspace collaboration."""

    def collaboration_bootstrap(self) -> JsonObject: ...

    def collaboration_members(self, organization_id: str) -> tuple[JsonObject, ...]: ...

    def collaboration_invite_member(
        self, organization_id: str, email: str, role_id: str, *, idempotency_key: str
    ) -> JsonObject: ...

    def collaboration_topics(self, organization_id: str, workspace_id: str) -> tuple[JsonObject, ...]: ...

    def collaboration_create_topic(
        self, organization_id: str, workspace_id: str, title: str, *, idempotency_key: str
    ) -> JsonObject: ...

    def collaboration_messages(
        self, organization_id: str, workspace_id: str, topic_id: str, *, after: str | None = None
    ) -> JsonObject: ...

    def collaboration_post_message(
        self, organization_id: str, workspace_id: str, topic_id: str, content: str, *, idempotency_key: str
    ) -> JsonObject: ...

    def collaboration_queue_inference(
        self,
        organization_id: str,
        workspace_id: str,
        topic_id: str,
        request: Mapping[str, object],
        *,
        idempotency_key: str,
    ) -> JsonObject: ...

    def collaboration_operations(
        self, organization_id: str, workspace_id: str, topic_id: str
    ) -> tuple[JsonObject, ...]: ...

    def collaboration_cancel_inference(
        self, organization_id: str, workspace_id: str, topic_id: str, operation_id: str
    ) -> JsonObject: ...

    def collaboration_register_runner(
        self, organization_id: str, runner_id: str, document: Mapping[str, object]
    ) -> JsonObject: ...

    def collaboration_claim_lease(self, organization_id: str, runner_id: str) -> JsonObject | None: ...

    def collaboration_start_lease(
        self, organization_id: str, runner_id: str, lease_id: str
    ) -> JsonObject: ...

    def collaboration_heartbeat_lease(
        self, organization_id: str, runner_id: str, lease_id: str, *, progress: str
    ) -> JsonObject: ...

    def collaboration_finish_lease(
        self,
        organization_id: str,
        runner_id: str,
        lease_id: str,
        *,
        state: str,
        content: str,
        usage: Mapping[str, object] | None = None,
    ) -> JsonObject: ...


@dataclass(frozen=True, repr=False, slots=True)
class DesktopAuthenticatedCloudBoundary:
    """Closed private pair installed by one successful authentication."""

    provider: DesktopCloudProvider
    product_policy_client: CloudProductPolicyClient


class DesktopCloudRuntimeError(RuntimeError):
    """The Desktop Cloud controller rejected a state or operation."""


def _strict_object(payload: bytes, *, maximum: int = MAX_DESKTOP_CLOUD_STATE_BYTES) -> JsonObject:
    if not payload or len(payload) > maximum:
        raise DesktopCloudRuntimeError("Desktop Cloud state is empty or exceeds its limit")

    def unique(pairs: list[tuple[str, Any]]) -> JsonObject:
        value: JsonObject = {}
        for key, item in pairs:
            if key in value:
                raise DesktopCloudRuntimeError("Desktop Cloud state contains a duplicate key")
            value[key] = item
        return value

    try:
        document = json.loads(payload.decode("utf-8"), object_pairs_hook=unique)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise DesktopCloudRuntimeError("Desktop Cloud state is not strict UTF-8 JSON") from error
    if not isinstance(document, dict):
        raise DesktopCloudRuntimeError("Desktop Cloud state must be one JSON object")
    return document


def _safe_identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise DesktopCloudRuntimeError(f"{label} is invalid")
    return value


def _safe_version(value: object, label: str) -> str:
    if not isinstance(value, str) or _VERSION.fullmatch(value) is None:
        raise DesktopCloudRuntimeError(f"{label} is invalid")
    return value


def _scope_key(session: JsonObject) -> str:
    return jcs_digest({"actor": session["actor"], "tenant_id": session["tenant_id"]})


def _submission_binding(run: JsonObject) -> JsonObject:
    return {
        "cloud_project_id": run["cloud_project_id"],
        "bundle_digest": run["project_binding"]["bundle_digest"],
        "request_digest": run["request_digest"],
        "idempotency_key_digest": run["idempotency_key_digest"],
        "route_plan_digest": run["route_plan_digest"],
        "pricing_digest": run["pricing_digest"],
    }


class DesktopCloudStateStore:
    """Small, non-secret durable indexes for reconnect and event replay."""

    def __init__(self, path: Path) -> None:
        if not path.is_absolute() or path.name in {"", ".", ".."}:
            raise DesktopCloudRuntimeError("Desktop Cloud state requires an absolute file path")
        self.path = path
        self._lock = threading.RLock()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(path.parent, 0o700)
        except OSError as error:
            raise DesktopCloudRuntimeError("Desktop Cloud state directory is unavailable") from error
        self._read()

    @staticmethod
    def _empty() -> JsonObject:
        return {"version": DESKTOP_CLOUD_STATE_VERSION, "scopes": {}}

    def _read(self) -> JsonObject:
        if not self.path.exists():
            return self._empty()
        try:
            metadata = self.path.lstat()
            if self.path.is_symlink() or not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_DESKTOP_CLOUD_STATE_BYTES:
                raise DesktopCloudRuntimeError("Desktop Cloud state is not one bounded regular file")
            document = _strict_object(self.path.read_bytes())
        except OSError as error:
            raise DesktopCloudRuntimeError("Desktop Cloud state could not be read") from error
        self._validate(document)
        return document

    def _write(self, document: JsonObject) -> None:
        self._validate(document)
        payload = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        if len(payload) > MAX_DESKTOP_CLOUD_STATE_BYTES:
            raise DesktopCloudRuntimeError("Desktop Cloud state exceeds its limit")
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
        finally:
            if temporary.exists():
                temporary.unlink()

    @staticmethod
    def _validate(document: JsonObject) -> None:
        if set(document) != {"version", "scopes"} or document.get("version") != DESKTOP_CLOUD_STATE_VERSION:
            raise DesktopCloudRuntimeError("Desktop Cloud state has an unsupported shape")
        scopes = document.get("scopes")
        if not isinstance(scopes, dict) or len(scopes) > 32:
            raise DesktopCloudRuntimeError("Desktop Cloud state scopes are invalid")
        for scope_key, scope in scopes.items():
            if not isinstance(scope_key, str) or _DIGEST.fullmatch(scope_key) is None or not isinstance(scope, dict):
                raise DesktopCloudRuntimeError("Desktop Cloud state scope is invalid")
            if set(scope) != {"projects", "runs", "replays", "artifacts"}:
                raise DesktopCloudRuntimeError("Desktop Cloud scope has an unsupported shape")
            projects, runs, replays, artifacts = (scope[key] for key in ("projects", "runs", "replays", "artifacts"))
            if not isinstance(projects, dict) or len(projects) > MAX_CLOUD_PROJECTS:
                raise DesktopCloudRuntimeError("Desktop Cloud project index is invalid")
            if not isinstance(runs, dict) or len(runs) > MAX_TRACKED_RUNS:
                raise DesktopCloudRuntimeError("Desktop Cloud run index is invalid")
            if not isinstance(replays, dict) or len(replays) > MAX_TRACKED_RUNS:
                raise DesktopCloudRuntimeError("Desktop Cloud replay index is invalid")
            if not isinstance(artifacts, list) or len(artifacts) > MAX_REPLAY_EVENTS:
                raise DesktopCloudRuntimeError("Desktop Cloud artifact index is invalid")
            for cloud_project_id, binding in projects.items():
                _safe_identifier(cloud_project_id, "stored cloud project id")
                if (
                    not isinstance(binding, dict)
                    or set(binding) != {"project_id", "bundle_digest"}
                    or _DIGEST.fullmatch(str(binding.get("bundle_digest", ""))) is None
                ):
                    raise DesktopCloudRuntimeError("Desktop Cloud project binding is invalid")
                _safe_identifier(binding.get("project_id"), "stored project id")
            for operation_id, binding in runs.items():
                _safe_identifier(operation_id, "stored operation id")
                if (
                    not isinstance(binding, dict)
                    or set(binding)
                    != {
                        "cloud_project_id",
                        "bundle_digest",
                        "request_digest",
                        "idempotency_key_digest",
                        "route_plan_digest",
                        "pricing_digest",
                    }
                    or any(
                        _DIGEST.fullmatch(str(binding.get(key, ""))) is None
                        for key in (
                            "bundle_digest",
                            "request_digest",
                            "idempotency_key_digest",
                            "route_plan_digest",
                        )
                    )
                    or (
                        binding.get("pricing_digest") is not None
                        and _DIGEST.fullmatch(str(binding["pricing_digest"])) is None
                    )
                ):
                    raise DesktopCloudRuntimeError("Desktop Cloud run binding is invalid")
                _safe_identifier(binding.get("cloud_project_id"), "stored run project id")
            for operation_id, replay in replays.items():
                _safe_identifier(operation_id, "stored replay operation id")
                if not isinstance(replay, dict) or set(replay) != {
                    "operation_id",
                    "cursor",
                    "last_sequence",
                    "last_state",
                    "last_occurred_at",
                    "event_digests",
                }:
                    raise DesktopCloudRuntimeError("Desktop Cloud replay binding is invalid")
                if replay.get("operation_id") != operation_id or not isinstance(replay.get("last_sequence"), int):
                    raise DesktopCloudRuntimeError("Desktop Cloud replay identity is invalid")
                event_digests = replay.get("event_digests")
                if not isinstance(event_digests, list) or len(event_digests) > MAX_REPLAY_EVENTS:
                    raise DesktopCloudRuntimeError("Desktop Cloud replay history is invalid")
                if any(
                    not isinstance(item, list) or len(item) != 2 or not isinstance(item[0], str) or _DIGEST.fullmatch(str(item[1])) is None
                    for item in event_digests
                ):
                    raise DesktopCloudRuntimeError("Desktop Cloud replay digest is invalid")
            if any(not isinstance(item, str) or _safe_identifier(item, "stored artifact id") != item for item in artifacts):
                raise DesktopCloudRuntimeError("Desktop Cloud artifact identity is invalid")

    @staticmethod
    def _scope(document: JsonObject, scope_key: str) -> JsonObject:
        scopes = document["scopes"]
        return scopes.setdefault(scope_key, {"projects": {}, "runs": {}, "replays": {}, "artifacts": []})

    def record_project(self, scope_key: str, cloud_project_id: str, project: AcceptedContractRecord) -> None:
        document = project.document()
        binding = {"project_id": document["project"]["project_id"], "bundle_digest": jcs_digest(document)}
        with self._lock:
            state = self._read()
            scope = self._scope(state, scope_key)
            previous = scope["projects"].get(cloud_project_id)
            if previous is not None and previous != binding:
                raise DesktopCloudRuntimeError("per-project Cloud opt-in is bound to different project bytes")
            scope["projects"][cloud_project_id] = binding
            self._write(state)

    def project_is_opted_in(self, scope_key: str, cloud_project_id: str, project: AcceptedContractRecord) -> bool:
        with self._lock:
            scope = self._read()["scopes"].get(scope_key, {})
        return scope.get("projects", {}).get(cloud_project_id) == {
            "project_id": project.document()["project"]["project_id"],
            "bundle_digest": jcs_digest(project.document()),
        }

    def record_run(self, scope_key: str, run: AcceptedCloudClientRecord) -> None:
        binding = _submission_binding(run.document())
        with self._lock:
            state = self._read()
            scope = self._scope(state, scope_key)
            previous = scope["runs"].get(run.record_id)
            if previous is not None and previous != binding:
                raise DesktopCloudRuntimeError("tracked Cloud operation identity changed")
            scope["runs"][run.record_id] = binding
            self._write(state)

    def tracked_runs(self, scope_key: str) -> tuple[str, ...]:
        with self._lock:
            scope = self._read()["scopes"].get(scope_key, {})
        return tuple(sorted(scope.get("runs", {})))

    def run_binding(self, scope_key: str, operation_id: str) -> JsonObject | None:
        with self._lock:
            scope = self._read()["scopes"].get(scope_key, {})
            value = scope.get("runs", {}).get(operation_id)
        return copy.deepcopy(value) if isinstance(value, dict) else None

    def replay(self, scope_key: str, operation_id: str) -> EventReplayState:
        with self._lock:
            scope = self._read()["scopes"].get(scope_key, {})
            value = scope.get("replays", {}).get(operation_id)
        if value is None:
            return EventReplayState(operation_id=operation_id)
        restored = copy.deepcopy(value)
        restored["event_digests"] = tuple(tuple(item) for item in restored["event_digests"])
        return EventReplayState(**restored)

    def record_replay(self, scope_key: str, replay: EventReplayState, events: tuple[JsonObject, ...]) -> None:
        value = asdict(replay)
        value["event_digests"] = [list(item) for item in replay.event_digests]
        artifact_ids = {
            event["payload"]["artifact"]["artifact_id"]
            for event in events
            if event.get("type") == "artifact.committed"
            and isinstance(event.get("payload"), dict)
            and isinstance(event["payload"].get("artifact"), dict)
        }
        with self._lock:
            state = self._read()
            scope = self._scope(state, scope_key)
            scope["replays"][replay.operation_id] = value
            scope["artifacts"] = sorted(set(scope["artifacts"]).union(artifact_ids))
            self._write(state)

    def artifact_is_observed(self, scope_key: str, artifact_id: str) -> bool:
        with self._lock:
            scope = self._read()["scopes"].get(scope_key, {})
        return artifact_id in scope.get("artifacts", [])

    def tracked_artifacts(self, scope_key: str) -> tuple[str, ...]:
        with self._lock:
            scope = self._read()["scopes"].get(scope_key, {})
        return tuple(scope.get("artifacts", []))


class DesktopPolicyContinuityStore:
    """Bounded exact public requests retained for safe idempotent replay."""

    def __init__(self, path: Path) -> None:
        if not path.is_absolute() or path.name in {"", ".", ".."}:
            raise DesktopCloudRuntimeError("Desktop policy continuity requires an absolute file path")
        self.path = path
        self._lock = threading.RLock()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path.parent, 0o700)
        self._read()

    @staticmethod
    def _empty() -> JsonObject:
        return {"version": 1, "entitlement_bindings": {}, "scopes": {}}

    def _read(self) -> JsonObject:
        if not self.path.exists():
            return self._empty()
        try:
            metadata = self.path.lstat()
            if self.path.is_symlink() or not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_POLICY_CONTINUITY_BYTES:
                raise DesktopCloudRuntimeError("Desktop policy continuity is not one bounded regular file")
            document = _strict_object(self.path.read_bytes(), maximum=MAX_POLICY_CONTINUITY_BYTES)
        except OSError as error:
            raise DesktopCloudRuntimeError("Desktop policy continuity could not be read") from error
        self._validate(document)
        return document

    @staticmethod
    def _validate(document: JsonObject) -> None:
        if set(document) != {"version", "entitlement_bindings", "scopes"} or document.get("version") != 1:
            raise DesktopCloudRuntimeError("Desktop policy continuity has an unsupported shape")
        bindings = document.get("entitlement_bindings")
        if not isinstance(bindings, dict) or len(bindings) > 32:
            raise DesktopCloudRuntimeError("Desktop policy continuity entitlement bindings are invalid")
        for scope_key, binding in bindings.items():
            if (
                _DIGEST.fullmatch(str(scope_key)) is None
                or not isinstance(binding, dict)
                or set(binding) != {"id", "version", "digest"}
                or not isinstance(binding["id"], str)
                or _IDENTIFIER.fullmatch(binding["id"]) is None
                or not isinstance(binding["version"], str)
                or _VERSION.fullmatch(binding["version"]) is None
                or not isinstance(binding["digest"], str)
                or _DIGEST.fullmatch(binding["digest"]) is None
            ):
                raise DesktopCloudRuntimeError("Desktop policy continuity entitlement binding is invalid")
        scopes = document.get("scopes")
        if not isinstance(scopes, dict) or len(scopes) > 32:
            raise DesktopCloudRuntimeError("Desktop policy continuity scopes are invalid")
        total = 0
        for scope_key, requests in scopes.items():
            if _DIGEST.fullmatch(str(scope_key)) is None or not isinstance(requests, dict):
                raise DesktopCloudRuntimeError("Desktop policy continuity scope is invalid")
            total += len(requests)
            for key_digest, retained in requests.items():
                if _DIGEST.fullmatch(str(key_digest)) is None or not isinstance(retained, dict):
                    raise DesktopCloudRuntimeError("Desktop policy continuity key is invalid")
                if set(retained) != {"purpose", "action", "request", "prior_snapshot"}:
                    raise DesktopCloudRuntimeError("Desktop policy continuity request has an unsupported shape")
                if retained["purpose"] not in {"product_analytics", "research_evaluation", "model_training"}:
                    raise DesktopCloudRuntimeError("Desktop policy continuity purpose is invalid")
                if (
                    retained["action"] not in {"grant", "withdraw"}
                    or not isinstance(retained["request"], str)
                    or not isinstance(retained["prior_snapshot"], str)
                ):
                    raise DesktopCloudRuntimeError("Desktop policy continuity action or payload is invalid")
                for encoded in (retained["request"], retained["prior_snapshot"]):
                    try:
                        payload = base64.b64decode(encoded, validate=True)
                    except (binascii.Error, ValueError, UnicodeEncodeError) as error:
                        raise DesktopCloudRuntimeError("Desktop policy continuity payload is invalid") from error
                    if not payload or len(payload) > MAX_CLOUD_PRODUCT_POLICY_RECORD_BYTES:
                        raise DesktopCloudRuntimeError("Desktop policy continuity payload is invalid")
        if total > MAX_POLICY_CONTINUITY_REQUESTS:
            raise DesktopCloudRuntimeError("Desktop policy continuity contains too many requests")

    def _write(self, document: JsonObject) -> None:
        self._validate(document)
        payload = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        if len(payload) > MAX_POLICY_CONTINUITY_BYTES:
            raise DesktopCloudRuntimeError("Desktop policy continuity exceeds its limit")
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
        finally:
            if temporary.exists():
                temporary.unlink()

    def get(self, scope_key: str, key_digest: str) -> tuple[str, str, bytes, bytes] | None:
        with self._lock:
            value = self._read()["scopes"].get(scope_key, {}).get(key_digest)
        if not isinstance(value, dict):
            return None
        return (
            value["purpose"],
            value["action"],
            base64.b64decode(value["request"], validate=True),
            base64.b64decode(value["prior_snapshot"], validate=True),
        )

    def entitlement_binding(self, scope_key: str) -> tuple[str, str, str] | None:
        with self._lock:
            binding = self._read()["entitlement_bindings"].get(scope_key)
        if not isinstance(binding, dict):
            return None
        return binding["id"], binding["version"], binding["digest"]

    def bind_entitlement(self, scope_key: str, binding: tuple[str, str, str]) -> None:
        value = {"id": binding[0], "version": binding[1], "digest": binding[2]}
        with self._lock:
            document = self._read()
            existing = document["entitlement_bindings"].get(scope_key)
            if existing is not None:
                if existing != value:
                    raise CloudProductPolicyScopeError("connected policy authority is unavailable")
                return
            document["entitlement_bindings"][scope_key] = value
            self._write(document)

    def retain(
        self,
        scope_key: str,
        key_digest: str,
        *,
        purpose: str,
        action: str,
        request: bytes,
        prior_snapshot: bytes,
    ) -> None:
        retained = {
            "purpose": purpose,
            "action": action,
            "request": base64.b64encode(request).decode("ascii"),
            "prior_snapshot": base64.b64encode(prior_snapshot).decode("ascii"),
        }
        with self._lock:
            document = self._read()
            requests = document["scopes"].setdefault(scope_key, {})
            existing = requests.get(key_digest)
            if existing is not None:
                if existing != retained:
                    raise CloudProductPolicyIdempotencyConflict("Desktop idempotency key content changed")
                return
            requests[key_digest] = retained
            self._write(document)


class DesktopCloudController:
    """Bounded Desktop operations over one injected canonical Cloud boundary."""

    def __init__(
        self,
        state_directory: Path,
        *,
        provider: DesktopCloudProvider | None = None,
        validator: CloudClientContractValidator | None = None,
        product_policy_client: CloudProductPolicyClient | None = None,
        product_policy_validator: CloudProductPolicyValidator | None = None,
        policy_clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not state_directory.is_absolute():
            raise DesktopCloudRuntimeError("Desktop Cloud state directory must be absolute")
        self._validator = validator or canonical_cloud_client_validator()
        if product_policy_client is not None and not isinstance(product_policy_client, CloudProductPolicyClient):
            raise DesktopCloudRuntimeError("injected product-policy client does not implement the canonical protocol")
        if product_policy_validator is not None and not isinstance(product_policy_validator, CloudProductPolicyValidator):
            raise DesktopCloudRuntimeError("injected product-policy validator is not canonical")
        if product_policy_client is None and product_policy_validator is not None:
            raise DesktopCloudRuntimeError("a product-policy validator requires an injected canonical client")
        if policy_clock is not None and not callable(policy_clock):
            raise DesktopCloudRuntimeError("Desktop product-policy clock must be callable")
        if product_policy_client is not None and provider is not None:
            raise DesktopCloudRuntimeError("Desktop product policy requires an authentication-owned Cloud provider")
        if provider is not None:
            if not isinstance(provider, CloudSessionProvider) or not isinstance(provider, CloudClient):
                raise DesktopCloudRuntimeError("injected Desktop Cloud provider does not implement both canonical protocols")
            initial = self._accept_cloud(provider.current_session())
            if self._validator.validate_session(initial)["state"] != "disconnected":
                raise DesktopCloudRuntimeError("injected Desktop Cloud provider must start disconnected")
        state_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(state_directory, 0o700)
        self._state_directory = state_directory
        self._planner_factory = DesktopHybridPlannerFactory()
        self._state = DesktopCloudStateStore(state_directory / "state.json")
        self._policy_continuity = (
            DesktopPolicyContinuityStore(state_directory / "product-policy-continuity.json") if product_policy_client is not None else None
        )
        self._receipts = DesktopTransferReceiptStore(state_directory / "transfer-receipts.sqlite3")
        self._provider = provider
        self._product_policy_client = product_policy_client
        self._authentication_owns_product_policy_client = False
        self._product_policy_validator = product_policy_validator
        if product_policy_client is not None and self._product_policy_validator is None:
            self._product_policy_validator = CloudProductPolicyValidator(
                self._validator.repository_root,
                frozen_layout=self._validator.frozen_layout,
            )
        self._policy_clock = policy_clock or (lambda: datetime.now(UTC))
        self._lock = threading.RLock()
        self._session: AcceptedCloudClientRecord | None = None
        self._capabilities: AcceptedCloudClientRecord | None = None
        self._planner: DesktopHybridPlanner | None = None
        self._scope: str | None = None
        self._authenticated_scope: tuple[JsonObject, str] | None = None
        self._authenticated_account_id: str | None = None
        self._authenticated_entitlement_version: str | None = None
        self._session_guard: Callable[[], object] | None = None
        self._product_policy_authority: AccountPolicyAuthority | None = None
        self._product_policy_snapshot: AcceptedCloudProductPolicyRecord | None = None
        self._product_policy_entitlement_binding: tuple[str, str, str] | None = None

    def close(self) -> None:
        self._receipts.close()

    def _accept_cloud(self, record: object) -> AcceptedCloudClientRecord:
        if not isinstance(record, AcceptedCloudClientRecord):
            raise DesktopCloudRuntimeError("provider returned a non-canonical Cloud-client record")
        return self._validator.accept(record.to_bytes())

    @staticmethod
    def _accept_platform(record: object) -> AcceptedContractRecord:
        if not isinstance(record, AcceptedContractRecord):
            raise DesktopCloudRuntimeError("provider returned a non-canonical platform record")
        return AcceptedContractRecord.from_bytes(record.to_bytes())

    def _require_provider(self) -> DesktopCloudProvider:
        if self._provider is None:
            raise CloudClientStateError("no post-authentication Cloud provider is injected")
        return self._provider

    def _collaboration(self) -> DesktopCollaborationProvider:
        self._validate_authenticated_session()
        provider = self._require_provider()
        if not isinstance(provider, DesktopCollaborationProvider):
            raise CloudClientStateError("the authenticated Cloud provider does not support collaboration")
        return provider

    def collaboration_bootstrap(self) -> JsonObject:
        return copy.deepcopy(self._collaboration().collaboration_bootstrap())

    def collaboration_members(self, organization_id: str) -> list[JsonObject]:
        return [
            copy.deepcopy(item)
            for item in self._collaboration().collaboration_members(organization_id)
        ]

    def collaboration_invite_member(
        self, organization_id: str, email: str, role_id: str
    ) -> JsonObject:
        return copy.deepcopy(
            self._collaboration().collaboration_invite_member(
                organization_id,
                email,
                role_id,
                idempotency_key=f"desktop_member_{uuid.uuid4().hex}",
            )
        )

    def collaboration_topics(self, organization_id: str, workspace_id: str) -> list[JsonObject]:
        return [copy.deepcopy(item) for item in self._collaboration().collaboration_topics(organization_id, workspace_id)]

    def collaboration_create_topic(self, organization_id: str, workspace_id: str, title: str) -> JsonObject:
        return copy.deepcopy(
            self._collaboration().collaboration_create_topic(
                organization_id,
                workspace_id,
                title,
                idempotency_key=f"desktop_topic_{uuid.uuid4().hex}",
            )
        )

    def collaboration_messages(self, organization_id: str, workspace_id: str, topic_id: str) -> JsonObject:
        return copy.deepcopy(self._collaboration().collaboration_messages(organization_id, workspace_id, topic_id))

    def collaboration_post_message(
        self, organization_id: str, workspace_id: str, topic_id: str, content: str
    ) -> JsonObject:
        return copy.deepcopy(
            self._collaboration().collaboration_post_message(
                organization_id,
                workspace_id,
                topic_id,
                content,
                idempotency_key=f"desktop_message_{uuid.uuid4().hex}",
            )
        )

    def collaboration_queue_inference(
        self, organization_id: str, workspace_id: str, topic_id: str, request: Mapping[str, object]
    ) -> JsonObject:
        return copy.deepcopy(
            self._collaboration().collaboration_queue_inference(
                organization_id,
                workspace_id,
                topic_id,
                request,
                idempotency_key=f"desktop_inference_{uuid.uuid4().hex}",
            )
        )

    def collaboration_operations(
        self, organization_id: str, workspace_id: str, topic_id: str
    ) -> list[JsonObject]:
        return [
            copy.deepcopy(item)
            for item in self._collaboration().collaboration_operations(
                organization_id, workspace_id, topic_id
            )
        ]

    def collaboration_cancel_inference(
        self, organization_id: str, workspace_id: str, topic_id: str, operation_id: str
    ) -> JsonObject:
        return copy.deepcopy(
            self._collaboration().collaboration_cancel_inference(
                organization_id, workspace_id, topic_id, operation_id
            )
        )

    def collaboration_register_runner(
        self, organization_id: str, runner_id: str, document: Mapping[str, object]
    ) -> JsonObject:
        return copy.deepcopy(self._collaboration().collaboration_register_runner(organization_id, runner_id, document))

    def collaboration_claim_lease(self, organization_id: str, runner_id: str) -> JsonObject | None:
        return copy.deepcopy(self._collaboration().collaboration_claim_lease(organization_id, runner_id))

    def collaboration_start_lease(
        self, organization_id: str, runner_id: str, lease_id: str
    ) -> JsonObject:
        return copy.deepcopy(
            self._collaboration().collaboration_start_lease(
                organization_id, runner_id, lease_id
            )
        )

    def collaboration_heartbeat_lease(
        self, organization_id: str, runner_id: str, lease_id: str, *, progress: str
    ) -> JsonObject:
        return copy.deepcopy(
            self._collaboration().collaboration_heartbeat_lease(
                organization_id, runner_id, lease_id, progress=progress
            )
        )

    def collaboration_finish_lease(
        self,
        organization_id: str,
        runner_id: str,
        lease_id: str,
        *,
        state: str,
        content: str,
        usage: Mapping[str, object] | None = None,
    ) -> JsonObject:
        return copy.deepcopy(
            self._collaboration().collaboration_finish_lease(
                organization_id,
                runner_id,
                lease_id,
                state=state,
                content=content,
                usage=usage,
            )
        )

    def bind_authenticated_provider(
        self,
        provider: DesktopCloudProvider,
        *,
        account_id: str,
        entitlement_version: str,
        actor: Mapping[str, str],
        tenant_id: str,
        session_guard: Callable[[], object],
    ) -> None:
        """Inject an already-authenticated provider without connecting it."""

        if not isinstance(actor, Mapping):
            raise DesktopCloudRuntimeError("authenticated provider actor has an unsupported shape")
        if not callable(session_guard):
            raise DesktopCloudRuntimeError("authenticated provider requires a session-validity guard")
        require_credential_free(
            {
                "account_id": account_id,
                "actor": dict(actor),
                "entitlement_version": entitlement_version,
                "tenant_id": tenant_id,
            }
        )
        if set(actor) != {"type", "id"}:
            raise DesktopCloudRuntimeError("authenticated provider actor has an unsupported shape")
        accepted_actor = {
            "type": _safe_identifier(actor.get("type"), "authenticated actor type"),
            "id": _safe_identifier(actor.get("id"), "authenticated actor id"),
        }
        accepted_tenant = _safe_identifier(tenant_id, "authenticated tenant id")
        accepted_account = _safe_identifier(account_id, "authenticated account id")
        accepted_entitlement = _safe_version(entitlement_version, "authenticated entitlement version")
        if not isinstance(provider, CloudSessionProvider) or not isinstance(provider, CloudClient):
            raise DesktopCloudRuntimeError("authenticated provider does not implement both canonical protocols")
        with self._lock:
            if self._provider is not None or self._session is not None:
                raise DesktopCloudRuntimeError("Desktop Cloud already has a provider boundary")
        initial = self._accept_cloud(provider.current_session())
        if self._validator.validate_session(initial)["state"] != "disconnected":
            raise DesktopCloudRuntimeError("authenticated provider must start disconnected")
        with self._lock:
            # Recheck after consulting provider-owned code so concurrent
            # authentication attempts cannot replace one another.
            if self._provider is not None or self._session is not None:
                raise DesktopCloudRuntimeError("Desktop Cloud already has a provider boundary")
            self._provider = provider
            self._authenticated_scope = (accepted_actor, accepted_tenant)
            self._authenticated_account_id = accepted_account
            self._authenticated_entitlement_version = accepted_entitlement
            self._session_guard = session_guard

    def bind_authenticated_boundary(
        self,
        boundary: DesktopAuthenticatedCloudBoundary,
        *,
        account_id: str,
        entitlement_version: str,
        actor: Mapping[str, str],
        tenant_id: str,
        session_guard: Callable[[], object],
    ) -> None:
        """Atomically install both authentication-owned public clients."""

        if type(boundary) is not DesktopAuthenticatedCloudBoundary:
            raise DesktopCloudRuntimeError("authenticated Desktop boundary has an unsupported shape")
        if not isinstance(boundary.product_policy_client, CloudProductPolicyClient):
            raise DesktopCloudRuntimeError("authenticated product-policy client does not implement the canonical protocol")
        if not isinstance(actor, Mapping):
            raise DesktopCloudRuntimeError("authenticated provider actor has an unsupported shape")
        if not callable(session_guard):
            raise DesktopCloudRuntimeError("authenticated provider requires a session-validity guard")
        require_credential_free(
            {
                "account_id": account_id,
                "actor": dict(actor),
                "entitlement_version": entitlement_version,
                "tenant_id": tenant_id,
            }
        )
        if set(actor) != {"type", "id"}:
            raise DesktopCloudRuntimeError("authenticated provider actor has an unsupported shape")
        accepted_actor = {
            "type": _safe_identifier(actor.get("type"), "authenticated actor type"),
            "id": _safe_identifier(actor.get("id"), "authenticated actor id"),
        }
        accepted_tenant = _safe_identifier(tenant_id, "authenticated tenant id")
        accepted_account = _safe_identifier(account_id, "authenticated account id")
        accepted_entitlement = _safe_version(entitlement_version, "authenticated entitlement version")
        provider = boundary.provider
        if not isinstance(provider, CloudSessionProvider) or not isinstance(provider, CloudClient):
            raise DesktopCloudRuntimeError("authenticated provider does not implement both canonical protocols")
        with self._lock:
            if self._provider is not None or self._session is not None or self._product_policy_client is not None:
                raise DesktopCloudRuntimeError("Desktop Cloud already has a provider boundary")

        # Validate every fallible local input before publishing either client.
        # The continuity constructor revalidates existing bytes without writing
        # them, while the validator binds the same source/frozen contract set as
        # the Cloud-client validator already accepted for this controller.
        continuity = DesktopPolicyContinuityStore(self._state_directory / "product-policy-continuity.json")
        policy_validator = CloudProductPolicyValidator(
            self._validator.repository_root,
            frozen_layout=self._validator.frozen_layout,
        )
        initial = self._accept_cloud(provider.current_session())
        if self._validator.validate_session(initial)["state"] != "disconnected":
            raise DesktopCloudRuntimeError("authenticated provider must start disconnected")

        with self._lock:
            # Recheck after validation and provider-owned code so concurrent
            # attempts cannot publish a mixed provider/policy pair.
            if self._provider is not None or self._session is not None or self._product_policy_client is not None:
                raise DesktopCloudRuntimeError("Desktop Cloud already has a provider boundary")
            self._provider = provider
            self._product_policy_client = boundary.product_policy_client
            self._product_policy_validator = policy_validator
            self._policy_continuity = continuity
            self._authentication_owns_product_policy_client = True
            self._authenticated_scope = (accepted_actor, accepted_tenant)
            self._authenticated_account_id = accepted_account
            self._authenticated_entitlement_version = accepted_entitlement
            self._session_guard = session_guard

    def clear_authenticated_provider(self) -> None:
        """Remove an authentication-owned provider only while disconnected."""

        with self._lock:
            if self._session is not None:
                raise DesktopCloudRuntimeError("connected Desktop Cloud provider must disconnect before removal")
            if self._authenticated_scope is None:
                raise DesktopCloudRuntimeError("Desktop Cloud provider is not authentication-owned")
            self._provider = None
            self._authenticated_scope = None
            self._authenticated_account_id = None
            self._authenticated_entitlement_version = None
            self._session_guard = None
            self._product_policy_authority = None
            self._product_policy_snapshot = None
            self._product_policy_entitlement_binding = None
            if self._authentication_owns_product_policy_client:
                self._product_policy_client = None
                self._product_policy_validator = None
                self._policy_continuity = None
                self._authentication_owns_product_policy_client = False

    def invalidate_authenticated_provider(self) -> bool:
        """Locally detach auth-owned state without consulting provider code."""

        with self._lock:
            if self._authenticated_scope is None:
                return False
            self._provider = None
            self._session = None
            self._capabilities = None
            self._planner = None
            self._scope = None
            self._authenticated_scope = None
            self._authenticated_account_id = None
            self._authenticated_entitlement_version = None
            self._session_guard = None
            self._product_policy_authority = None
            self._product_policy_snapshot = None
            self._product_policy_entitlement_binding = None
            if self._authentication_owns_product_policy_client:
                self._product_policy_client = None
                self._product_policy_validator = None
                self._policy_continuity = None
                self._authentication_owns_product_policy_client = False
            return True

    def _invalidate_closed_authenticated_boundary(self) -> None:
        with self._lock:
            owned = self._authentication_owns_product_policy_client
        if owned:
            self.invalidate_authenticated_provider()

    @contextmanager
    def _closed_authenticated_validation(self) -> Iterator[None]:
        """Detach an auth-owned client pair when accepted local evidence fails."""

        try:
            yield
        except (
            CloudClientContractError,
            CloudProductPolicyContractError,
            CloudProductPolicyScopeError,
            DesktopCloudRuntimeError,
        ):
            self._invalidate_closed_authenticated_boundary()
            raise

    def _validate_authenticated_session(self) -> None:
        with self._lock:
            guard = self._session_guard
        if guard is None:
            return
        try:
            guard()
        except Exception as primary:
            # The guard owns authentication state. Locally detach any
            # remaining auth-owned Cloud state without calling the provider.
            try:
                self.invalidate_authenticated_provider()
            except Exception as secondary:
                primary.add_note(f"secondary Cloud-provider detachment failed ({type(secondary).__name__})")
            raise

    def _connected(self) -> tuple[DesktopCloudProvider, AcceptedCloudClientRecord, AcceptedCloudClientRecord, str]:
        self._validate_authenticated_session()
        provider = self._require_provider()
        with self._lock:
            session = self._session
            capabilities = self._capabilities
            scope = self._scope
        if session is None or capabilities is None or scope is None:
            raise CloudClientStateError("Desktop Cloud controller is disconnected")
        with self._closed_authenticated_validation():
            current = self._accept_cloud(provider.current_session())
            if current.to_bytes() != session.to_bytes():
                raise DesktopCloudRuntimeError("provider session changed outside the Desktop controller")
            self._validator.validate_session(current, capabilities)
        return provider, current, capabilities, scope

    def readiness(self) -> JsonObject:
        with self._lock:
            connected = self._session is not None
        return {
            "status": "connected" if connected else "disconnected",
            "provider_injected": self._provider is not None,
            "provider_boundary": "post_authentication",
            "cloud_client_version": "1.0.0-alpha.1",
            "bundle_id": self._validator.manifest["bundle_id"],
            "bundle_digest": self._validator.manifest["bundle_digest"],
        }

    def session(self) -> JsonObject:
        self._validate_authenticated_session()
        with self._lock:
            provider = self._provider
        if provider is None:
            return {
                "schema_id": "reasoning.cloud-client.session",
                "schema_version": "1.0.0-alpha.1",
                "state": "disconnected",
            }
        record = self._accept_cloud(provider.current_session())
        with self._lock:
            capabilities = self._capabilities if self._session is not None else None
        return self._validator.validate_session(record, capabilities)

    def connect(self) -> JsonObject:
        self._validate_authenticated_session()
        provider = self._require_provider()
        try:
            session = self._accept_cloud(provider.connect())
            capabilities = self._accept_cloud(provider.capabilities())
            capability_document = self._validator.validate_capabilities(
                capabilities,
                lambda reference: self._resolved_contract(provider, reference).to_bytes(),
            )
            session_document = self._validator.validate_session(session, capabilities)
            if session_document["state"] != "connected":
                raise DesktopCloudRuntimeError("provider did not establish a connected canonical session")
            with self._lock:
                authenticated_scope = self._authenticated_scope
            if authenticated_scope is not None and (
                session_document["actor"] != authenticated_scope[0] or session_document["tenant_id"] != authenticated_scope[1]
            ):
                raise DesktopCloudRuntimeError("Cloud provider differs from the authenticated Desktop scope")
            harnesses, inference_routes, compute_targets = self._resolve_descriptors(provider, capability_document)
            planner = self._planner_factory.create(
                harnesses=harnesses,
                inference_routes=inference_routes,
                compute_targets=compute_targets,
                transfer_receipts=self._receipts,
            )
            if self._accept_cloud(provider.current_session()).to_bytes() != session.to_bytes():
                raise DesktopCloudRuntimeError("provider session changed during Desktop connection")
            policy_authority = self._mint_product_policy_authority(provider, session, capabilities)
        except Exception:
            # Public disconnect is idempotent and cannot cancel provider-owned
            # work. Restore a safe state if validation fails partway through.
            with suppress(Exception):
                self._validator.validate_session(self._accept_cloud(provider.disconnect()))
            self._invalidate_closed_authenticated_boundary()
            raise
        with self._lock:
            self._session = session
            self._capabilities = capabilities
            self._planner = planner
            self._scope = _scope_key(session_document)
            self._product_policy_authority = policy_authority
            self._product_policy_snapshot = None
            self._product_policy_entitlement_binding = None
        return copy.deepcopy(session_document)

    def disconnect(self) -> JsonObject:
        self._validate_authenticated_session()
        with self._lock:
            provider = self._provider
        if provider is None:
            return self.session()
        disconnected = self._accept_cloud(provider.disconnect())
        document = self._validator.validate_session(disconnected)
        if document["state"] != "disconnected":
            raise DesktopCloudRuntimeError("provider did not establish a disconnected canonical session")
        with self._lock:
            self._session = None
            self._capabilities = None
            self._planner = None
            self._scope = None
            self._product_policy_authority = None
            self._product_policy_snapshot = None
            self._product_policy_entitlement_binding = None
        return copy.deepcopy(document)

    def _mint_product_policy_authority(
        self,
        provider: DesktopCloudProvider,
        session: AcceptedCloudClientRecord,
        capabilities: AcceptedCloudClientRecord,
    ) -> AccountPolicyAuthority | None:
        if self._product_policy_client is None:
            return None
        with self._lock:
            account_id = self._authenticated_account_id
        if account_id is None:
            raise CloudProductPolicyScopeError("connected policy authority is unavailable")
        return AccountPolicyAuthority.from_connected(
            account_id=account_id,
            session=session,
            capabilities=capabilities,
            cloud_client_validator=self._validator,
            descriptor_resolver=lambda reference: self._resolved_contract(provider, reference).to_bytes(),
        )

    def _connected_product_policy(
        self,
    ) -> tuple[CloudProductPolicyClient, CloudProductPolicyValidator, AccountPolicyAuthority]:
        provider, session, capabilities, _scope = self._connected()
        client = self._product_policy_client
        validator = self._product_policy_validator
        if client is None or validator is None:
            raise CloudClientStateError("no canonical Cloud product-policy client is injected")
        with self._closed_authenticated_validation():
            authority = self._mint_product_policy_authority(provider, session, capabilities)
            with self._lock:
                expected = self._product_policy_authority
            if authority is None or expected is None or authority != expected:
                raise CloudProductPolicyScopeError("connected policy authority is unavailable")
        return client, validator, authority

    @staticmethod
    def _product_policy_scope_key(authority: AccountPolicyAuthority, entitlement_version: str) -> str:
        return jcs_digest(
            {
                "account_id": authority.account_id,
                "actor": {"type": authority.actor_type, "id": authority.actor_id},
                "tenant_id": authority.tenant_id,
                "capability_snapshot_id": authority.capability_snapshot_id,
                "capability_snapshot_digest": authority.capability_snapshot_digest,
                "inference_descriptors": authority.inference_descriptors,
                "compute_descriptors": authority.compute_descriptors,
                "entitlement_version": entitlement_version,
                "mutation": "optional-use-change",
            }
        )

    @staticmethod
    def _product_policy_entitlement_scope_key(authority: AccountPolicyAuthority, entitlement_version: str) -> str:
        return jcs_digest(
            {
                "account_id": authority.account_id,
                "actor": {"type": authority.actor_type, "id": authority.actor_id},
                "tenant_id": authority.tenant_id,
                "entitlement_version": entitlement_version,
                "binding": "account-entitlement",
            }
        )

    @staticmethod
    def _accept_product_policy(
        record: object,
        *,
        validator: CloudProductPolicyValidator,
    ) -> AcceptedCloudProductPolicyRecord:
        if not isinstance(record, AcceptedCloudProductPolicyRecord):
            raise DesktopCloudRuntimeError("product-policy client returned a non-canonical record")
        return validator.accept(record.to_bytes())

    def _validate_product_policy_snapshot(
        self,
        record: AcceptedCloudProductPolicyRecord,
        *,
        validator: CloudProductPolicyValidator,
        authority: AccountPolicyAuthority,
    ) -> JsonObject:
        try:
            document = validator.validate_account_policy(record, authority=authority)
        except (CloudProductPolicyContractError, CloudProductPolicyScopeError, DesktopCloudRuntimeError):
            self._invalidate_closed_authenticated_boundary()
            raise
        entitlement = document["entitlement"]
        binding = (entitlement["id"], entitlement["version"], entitlement["digest"])
        with self._lock:
            entitlement_version = self._authenticated_entitlement_version
            expected_binding = self._product_policy_entitlement_binding
            continuity = self._policy_continuity
        if entitlement_version is None or continuity is None:
            raise CloudProductPolicyScopeError("connected policy authority is unavailable")
        entitlement_scope = self._product_policy_entitlement_scope_key(authority, entitlement_version)
        persisted_binding = continuity.entitlement_binding(entitlement_scope)
        if (
            entitlement["version"] != entitlement_version
            or expected_binding not in {None, binding}
            or persisted_binding not in {None, binding}
        ):
            self._invalidate_closed_authenticated_boundary()
            raise CloudProductPolicyScopeError("connected policy authority is unavailable")
        return document

    def refresh_account_policy(self) -> JsonObject:
        try:
            client, validator, authority = self._connected_product_policy()
            accepted = self._accept_product_policy(client.account_policy(), validator=validator)
            document = self._validate_product_policy_snapshot(accepted, validator=validator, authority=authority)
            entitlement = document["entitlement"]
            binding = (entitlement["id"], entitlement["version"], entitlement["digest"])
            with self._lock:
                continuity = self._policy_continuity
                entitlement_version = self._authenticated_entitlement_version
            if continuity is None or entitlement_version is None:
                raise CloudProductPolicyScopeError("connected policy authority is unavailable")
            continuity.bind_entitlement(
                self._product_policy_entitlement_scope_key(authority, entitlement_version),
                binding,
            )
            with self._lock:
                self._product_policy_snapshot = accepted
                self._product_policy_entitlement_binding = binding
            return copy.deepcopy(document)
        except (CloudProductPolicyContractError, CloudProductPolicyScopeError, DesktopCloudRuntimeError):
            self._invalidate_closed_authenticated_boundary()
            raise

    def account_policy(self) -> JsonObject:
        """Refresh and present the canonical account policy for this connection."""

        return self.refresh_account_policy()

    def _policy_timestamp(self) -> str:
        value = self._policy_clock()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise DesktopCloudRuntimeError("Desktop product-policy clock must return an aware datetime")
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")

    def change_optional_use(self, purpose: str, action: str, *, idempotency_key: str) -> JsonObject:
        if purpose not in {"product_analytics", "research_evaluation", "model_training"}:
            raise CloudProductPolicyContractError("optional-use purpose is unsupported")
        if action not in {"grant", "withdraw"}:
            raise CloudProductPolicyContractError("optional-use action is unsupported")
        key_digest = digest_idempotency_key(idempotency_key)
        client, validator, authority = self._connected_product_policy()
        with self._lock:
            continuity = self._policy_continuity
            entitlement_version = self._authenticated_entitlement_version
        if continuity is None or entitlement_version is None:
            raise CloudProductPolicyScopeError("connected policy authority is unavailable")
        continuity_scope = self._product_policy_scope_key(authority, entitlement_version)
        retained = continuity.get(continuity_scope, key_digest)
        prior_snapshot: JsonObject | None = None
        if retained is not None:
            retained_purpose, retained_action, payload, prior_payload = retained
            if (retained_purpose, retained_action) != (purpose, action):
                raise CloudProductPolicyIdempotencyConflict("Desktop idempotency key content changed")
            with self._closed_authenticated_validation():
                accepted_request = AcceptedCloudProductPolicyRecord.from_bytes(payload, validator=validator)
                request = accepted_request.document()
                accepted_prior = AcceptedCloudProductPolicyRecord.from_bytes(prior_payload, validator=validator)
                prior_snapshot = self._validate_product_policy_snapshot(
                    accepted_prior,
                    validator=validator,
                    authority=authority,
                )
                expected = request["expected"]
                if (
                    request["account_id"] != authority.account_id
                    or request["actor"] != {"type": authority.actor_type, "id": authority.actor_id}
                    or request["tenant_id"] != authority.tenant_id
                    or request["purpose"] != purpose
                    or request["action"] != action
                    or request["idempotency_key_digest"] != key_digest
                    or expected["capability_snapshot_id"] != authority.capability_snapshot_id
                    or expected["capability_snapshot_digest"] != authority.capability_snapshot_digest
                    or expected["entitlement_version"] != entitlement_version
                ):
                    raise CloudProductPolicyScopeError("connected policy authority is unavailable")
                self._validate_retained_prior_snapshot(request, prior_snapshot)
        else:
            with self._closed_authenticated_validation():
                accepted_request, request, accepted_prior, prior_snapshot = self._build_optional_use_change(
                    purpose,
                    action,
                    key_digest=key_digest,
                    validator=validator,
                    authority=authority,
                )
                continuity.retain(
                    continuity_scope,
                    key_digest,
                    purpose=purpose,
                    action=action,
                    request=accepted_request.to_bytes(),
                    prior_snapshot=accepted_prior.to_bytes(),
                )
        request_bytes = accepted_request.to_bytes()
        response = client.change_optional_use(accepted_request, idempotency_key=idempotency_key)
        with self._closed_authenticated_validation():
            returned = self._accept_product_policy(response, validator=validator)
            if accepted_request.to_bytes() != request_bytes:
                raise CloudProductPolicyContractError("product-policy client changed the accepted request bytes")
            returned_document = self._validate_product_policy_snapshot(returned, validator=validator, authority=authority)
            self._validate_optional_use_response(request, returned_document, prior_snapshot=prior_snapshot)
        with self._lock:
            self._product_policy_snapshot = returned
        return copy.deepcopy(returned_document)

    def _build_optional_use_change(
        self,
        purpose: str,
        action: str,
        *,
        key_digest: str,
        validator: CloudProductPolicyValidator,
        authority: AccountPolicyAuthority,
    ) -> tuple[AcceptedCloudProductPolicyRecord, JsonObject, AcceptedCloudProductPolicyRecord, JsonObject]:
        with self._lock:
            current = self._product_policy_snapshot
        if current is None:
            self.refresh_account_policy()
            with self._lock:
                current = self._product_policy_snapshot
        if current is None:
            raise CloudClientStateError("Desktop product-policy snapshot is unavailable")
        current_document = self._validate_product_policy_snapshot(current, validator=validator, authority=authority)
        optional = current_document["optional_uses"][purpose]
        consent_policy = optional["policy"] if action == "grant" else optional["decision_history"][-1]["policy"]
        if consent_policy is None:
            raise CloudProductPolicyContractError("optional-use action lacks an exact consent policy")
        capability = current_document["capability_snapshot"]
        entitlement = current_document["entitlement"]
        product_policy = current_document["product_policy"]
        request: JsonObject = {
            "schema_id": "reasoning.cloud-product-policy.optional-use-change",
            "schema_version": "1.0.0-alpha.1",
            "account_id": authority.account_id,
            "actor": {"type": authority.actor_type, "id": authority.actor_id},
            "tenant_id": authority.tenant_id,
            "purpose": purpose,
            "action": action,
            "expected": {
                "snapshot_id": current_document["snapshot_id"],
                "snapshot_revision": current_document["snapshot_revision"],
                "snapshot_digest": current_document["snapshot_digest"],
                "capability_snapshot_id": capability["record_id"],
                "capability_snapshot_digest": capability["digest"],
                "entitlement_id": entitlement["id"],
                "entitlement_version": entitlement["version"],
                "entitlement_digest": entitlement["digest"],
                "product_policy_id": product_policy["policy_id"],
                "product_policy_version": product_policy["version"],
                "product_policy_digest": product_policy["digest"],
                "decision_revision": optional["decision"]["revision"],
                "decision_digest": optional["decision"]["digest"],
            },
            "consent_policy": copy.deepcopy(consent_policy),
            "requested_at": self._policy_timestamp(),
            "idempotency_key_digest": key_digest,
            "request_digest": "",
        }
        request["request_digest"] = canonical_optional_use_request_digest(request)
        accepted_request = AcceptedCloudProductPolicyRecord.from_document(request, validator=validator)
        return accepted_request, request, current, current_document

    @staticmethod
    def _validate_retained_prior_snapshot(request: JsonObject, prior_snapshot: JsonObject) -> None:
        expected = request["expected"]
        capability = prior_snapshot["capability_snapshot"]
        entitlement = prior_snapshot["entitlement"]
        product_policy = prior_snapshot["product_policy"]
        decision = prior_snapshot["optional_uses"][request["purpose"]]["decision"]
        if (
            prior_snapshot["snapshot_id"] != expected["snapshot_id"]
            or prior_snapshot["snapshot_revision"] != expected["snapshot_revision"]
            or prior_snapshot["snapshot_digest"] != expected["snapshot_digest"]
            or capability["record_id"] != expected["capability_snapshot_id"]
            or capability["digest"] != expected["capability_snapshot_digest"]
            or entitlement
            != {
                "id": expected["entitlement_id"],
                "version": expected["entitlement_version"],
                "digest": expected["entitlement_digest"],
            }
            or product_policy["policy_id"] != expected["product_policy_id"]
            or product_policy["version"] != expected["product_policy_version"]
            or product_policy["digest"] != expected["product_policy_digest"]
            or decision["revision"] != expected["decision_revision"]
            or decision["digest"] != expected["decision_digest"]
        ):
            raise CloudProductPolicyContractError("retained product-policy authority does not match its request")

    @staticmethod
    def _validate_optional_use_response(
        request: JsonObject,
        returned: JsonObject,
        *,
        prior_snapshot: JsonObject | None,
    ) -> None:
        expected = request["expected"]
        capability = returned["capability_snapshot"]
        entitlement = returned["entitlement"]
        product_policy = returned["product_policy"]
        optional = returned["optional_uses"][request["purpose"]]
        final = optional["decision_history"][-1]
        expected_state = "granted" if request["action"] == "grant" else "withdrawn"
        if (
            returned["snapshot_id"] != expected["snapshot_id"]
            or returned["snapshot_revision"] != expected["snapshot_revision"] + 1
            or returned["observed_at"] != request["requested_at"]
            or capability["record_id"] != expected["capability_snapshot_id"]
            or capability["digest"] != expected["capability_snapshot_digest"]
            or entitlement
            != {
                "id": expected["entitlement_id"],
                "version": expected["entitlement_version"],
                "digest": expected["entitlement_digest"],
            }
            or product_policy["policy_id"] != expected["product_policy_id"]
            or product_policy["version"] != expected["product_policy_version"]
            or product_policy["digest"] != expected["product_policy_digest"]
            or optional["decision"]["revision"] != expected["decision_revision"] + 1
            or final["revision"] != expected["decision_revision"] + 1
            or final["action"] != request["action"]
            or final["state"] != expected_state
            or final["decided_at"] != request["requested_at"]
            or final["policy_digest"] != request["consent_policy"]["digest"]
            or final["policy"] != request["consent_policy"]
            or optional["decision"] != {key: final[key] for key in optional["decision"]}
        ):
            raise CloudProductPolicyContractError("product-policy response is not the exact requested transition")
        if prior_snapshot is None:
            return
        unchanged = (
            "schema_id",
            "schema_version",
            "snapshot_id",
            "account_id",
            "actor",
            "tenant_id",
            "capability_snapshot",
            "entitlement",
            "catalogue",
            "product_policy",
            "inference_offers",
            "compute_offers",
        )
        other_purposes = set(prior_snapshot["optional_uses"]) - {request["purpose"]}
        selected_before = prior_snapshot["optional_uses"][request["purpose"]]
        selected_after = returned["optional_uses"][request["purpose"]]
        if (
            any(returned[key] != prior_snapshot[key] for key in unchanged)
            or any(returned["optional_uses"][name] != prior_snapshot["optional_uses"][name] for name in other_purposes)
            or selected_after["availability"] != selected_before["availability"]
            or selected_after["policy"] != selected_before["policy"]
            or selected_after["decision_history"][:-1] != selected_before["decision_history"]
        ):
            raise CloudProductPolicyContractError("product-policy response changed state outside the requested transition")

    def grant_optional_use(self, purpose: str, *, idempotency_key: str) -> JsonObject:
        return self.change_optional_use(purpose, "grant", idempotency_key=idempotency_key)

    def withdraw_optional_use(self, purpose: str, *, idempotency_key: str) -> JsonObject:
        return self.change_optional_use(purpose, "withdraw", idempotency_key=idempotency_key)

    def _resolved_contract(
        self,
        provider: DesktopCloudProvider,
        reference: ContractRecordReference,
    ) -> AcceptedContractRecord:
        record = self._accept_platform(provider.resolve_contract(reference))
        if (
            record.schema_id != reference.schema_id
            or record.schema_version != reference.schema_version
            or record.record_id != reference.record_id
            or record.digest != reference.digest
        ):
            raise CloudClientContractError("provider descriptor differs from its capability reference")
        return record

    def _resolve_descriptors(
        self,
        provider: DesktopCloudProvider,
        capabilities: JsonObject,
    ) -> tuple[tuple[AcceptedContractRecord, ...], tuple[AcceptedContractRecord, ...], tuple[AcceptedContractRecord, ...]]:
        values: list[tuple[AcceptedContractRecord, ...]] = []
        for key in ("harnesses", "inference_routes", "compute_targets"):
            values.append(
                tuple(
                    self._resolved_contract(provider, ContractRecordReference.from_document(reference)) for reference in capabilities[key]
                )
            )
        return values[0], values[1], values[2]

    def capabilities(self) -> JsonObject:
        provider, _session, capabilities, _scope = self._connected()
        return copy.deepcopy(
            self._validator.validate_capabilities(
                capabilities,
                lambda reference: self._resolved_contract(provider, reference).to_bytes(),
            )
        )

    def route_options(self) -> JsonObject:
        provider, _session, capabilities, _scope = self._connected()
        document = self._validator.validate_capabilities(
            capabilities,
            lambda reference: self._resolved_contract(provider, reference).to_bytes(),
        )
        return {
            "route_pricing_pairs": copy.deepcopy(document["route_pricing_pairs"]),
            "harnesses": [
                self._resolved_contract(provider, ContractRecordReference.from_document(item)).document() for item in document["harnesses"]
            ],
            "inference_routes": [
                self._resolved_contract(provider, ContractRecordReference.from_document(item)).document()
                for item in document["inference_routes"]
            ],
            "compute_targets": [
                self._resolved_contract(provider, ContractRecordReference.from_document(item)).document()
                for item in document["compute_targets"]
            ],
        }

    def inspect_route(
        self,
        plan: AcceptedContractRecord,
        pricing: AcceptedContractRecord,
        project: AcceptedContractRecord,
        *,
        requirements: DesktopCapabilityRequirements | None = None,
    ) -> DesktopHybridRoute:
        accepted_plan = self._accept_platform(plan)
        accepted_pricing = self._accept_platform(pricing)
        accepted_project = self._accept_platform(project)
        _provider, session, capabilities, _scope = self._connected()
        with self._lock:
            planner = self._planner
        if planner is None:
            raise CloudClientStateError("Desktop Cloud planner is unavailable")
        return self._inspect_route_with_planner(
            planner,
            session,
            capabilities,
            accepted_plan,
            accepted_pricing,
            accepted_project,
            requirements=requirements,
        )

    def _inspect_route_with_planner(
        self,
        planner: DesktopHybridPlanner,
        session: AcceptedCloudClientRecord,
        capabilities: AcceptedCloudClientRecord,
        plan: AcceptedContractRecord,
        pricing: AcceptedContractRecord | None,
        project: AcceptedContractRecord,
        *,
        requirements: DesktopCapabilityRequirements | None = None,
    ) -> DesktopHybridRoute:
        session_document = session.document()
        plan_document = plan.document()
        if plan_document["actor"] != session_document["actor"] or plan_document["tenant_id"] != session_document["tenant_id"]:
            raise CloudClientContractError("route inspection differs from the connected session scope")
        self._validate_project_scope(project, session_document)
        pair = (
            plan.schema_version,
            pricing.schema_version if pricing is not None else plan_document.get("pricing_contract_version"),
        )
        accepted_pairs = {(item["route_plan"], item["pricing"]) for item in capabilities.document()["route_pricing_pairs"]}
        if pair not in accepted_pairs:
            raise HybridPlanningError("route and pricing pair was not advertised by this Cloud session")
        # Cloud-client project opt-in is authoritative for Cloud submission.
        # Inspection validates the exact project and route semantics without
        # consuming or issuing a T026 dispatch authorization.
        if pricing is None:
            return planner.inspect_pending_plan(
                plan, project, requirements=requirements
            )
        return planner.inspect_plan(plan, pricing, project, requirements=requirements)

    @staticmethod
    def _pricing_for_run(document: JsonObject) -> AcceptedContractRecord | None:
        pricing = document["pricing"]
        return None if pricing is None else AcceptedContractRecord.from_document(pricing)

    def _validate_project_scope(
        self,
        project: AcceptedContractRecord,
        session: JsonObject,
        *,
        cloud_project_id: str | None = None,
    ) -> JsonObject:
        accepted = self._accept_platform(project)
        if accepted.schema_id != "reasoning.platform.project-bundle":
            raise CloudClientContractError("Cloud project is not a canonical project bundle")
        document = accepted.document()
        require_credential_free(document)
        if document["export"]["created_by"] != session["actor"]:
            raise CloudClientContractError("Cloud project actor differs from the connected session")
        linkage = document["linkage"]
        if linkage["state"] == "linked" and (
            linkage["tenant_id"] != session["tenant_id"] or cloud_project_id is not None and linkage["cloud_project_id"] != cloud_project_id
        ):
            raise CloudClientContractError("Cloud project linkage differs from the connected session")
        return document

    def list_projects(self) -> tuple[JsonObject, ...]:
        provider, session, _capabilities, _scope = self._connected()
        projects = provider.list_projects()
        if not isinstance(projects, tuple) or len(projects) > MAX_CLOUD_PROJECTS:
            raise DesktopCloudRuntimeError("provider returned an unbounded Cloud project collection")
        accepted: list[JsonObject] = []
        for project in projects:
            record = self._accept_platform(project)
            accepted.append(copy.deepcopy(self._validate_project_scope(record, session.document())))
        return tuple(accepted)

    def put_project(
        self,
        cloud_project_id: str,
        project: AcceptedContractRecord,
        contents: Mapping[str, bytes],
        *,
        idempotency_key: str,
        opt_in: bool,
    ) -> JsonObject:
        if not isinstance(contents, Mapping):
            raise CloudClientContractError("project contents must be one bounded mapping")
        require_credential_free([cloud_project_id, idempotency_key])
        cloud_project_id = _safe_identifier(cloud_project_id, "cloud project id")
        if opt_in is not True:
            raise DesktopCloudRuntimeError("project upload requires explicit per-project opt-in")
        idempotency_key_digest(idempotency_key)
        accepted = self._accept_platform(project)
        document = accepted.document()
        if document["project"]["origin"] != "local":
            raise CloudClientContractError("Desktop put_project accepts only an explicit local project")
        expected = {item["path"]: item for item in document["contents"]}
        if len(contents) != len(expected):
            raise CloudClientContractError("project content mapping is incomplete or too large")
        require_credential_free(list(contents.keys()))
        if any(path not in contents for path in expected):
            raise CloudClientContractError("project content mapping is incomplete or too large")
        supplied: dict[str, bytes] = {}
        total_size = 0
        for path in sorted(expected):
            payload = contents[path]
            if not isinstance(payload, bytes):
                raise CloudClientContractError("project content values must be exact bytes")
            total_size += len(payload)
            if total_size > MAX_PROJECT_CONTENT_BYTES:
                raise CloudClientContractError("project content mapping is incomplete or too large")
            supplied[path] = bytes(payload)
        for path, descriptor in expected.items():
            payload = supplied[path]
            if len(payload) != descriptor["size_bytes"] or byte_digest(payload) != descriptor["digest"]:
                raise CloudClientContractError("project content failed size or digest verification")
        provider, session, _capabilities, scope = self._connected()
        self._validate_project_scope(accepted, session.document(), cloud_project_id=cloud_project_id)
        before = accepted.to_bytes()
        with self._lock:
            # Persist the explicit consent before the mutation. A lost response
            # can then be retried with the same caller-held idempotency key.
            self._state.record_project(scope, cloud_project_id, accepted)
            returned = self._accept_platform(
                provider.put_project(
                    cloud_project_id,
                    accepted,
                    supplied,
                    idempotency_key=idempotency_key,
                )
            )
        if accepted.to_bytes() != before or returned.to_bytes() != before:
            raise CloudClientContractError("provider changed the explicitly accepted project bytes")
        return copy.deepcopy(returned.document())

    def submit_run(
        self,
        run: AcceptedCloudClientRecord,
        project: AcceptedContractRecord,
        *,
        idempotency_key: str,
    ) -> JsonObject:
        require_credential_free(idempotency_key)
        idempotency_digest = idempotency_key_digest(idempotency_key)
        accepted_run = self._accept_cloud(run)
        accepted_project = self._accept_platform(project)
        provider, session, capabilities, scope = self._connected()
        value = self._validator.validate_run(
            accepted_run,
            session=session,
            capabilities=capabilities,
            project=accepted_project,
        )
        if value["idempotency_key_digest"] != idempotency_digest:
            raise CloudClientContractError("run idempotency digest does not match the caller key")
        cloud_project_id = value["cloud_project_id"]
        if not self._state.project_is_opted_in(scope, cloud_project_id, accepted_project):
            raise DesktopCloudRuntimeError("run requires an exact per-project put_project opt-in")
        with self._lock:
            planner = self._planner
        if planner is None:
            raise CloudClientStateError("Desktop Cloud planner is unavailable")
        submitted_route = self._inspect_route_with_planner(
            planner,
            session,
            capabilities,
            AcceptedContractRecord.from_document(value["route_plan"]),
            self._pricing_for_run(value),
            accepted_project,
        )
        submitted_binding = _submission_binding(value)
        with self._lock:
            # Bind the operation locally before dispatch so a lost response
            # cannot make durable provider-owned work undiscoverable.
            self._state.record_run(scope, accepted_run)
            returned = self._accept_cloud(provider.submit_run(accepted_run, accepted_project, idempotency_key=idempotency_key))
        returned_document = self._validator.validate_run(
            returned,
            session=session,
            capabilities=capabilities,
            project=accepted_project,
        )
        if returned.record_id != accepted_run.record_id:
            raise CloudClientContractError("provider changed the submitted operation identity")
        returned_route = self._inspect_route_with_planner(
            planner,
            session,
            capabilities,
            AcceptedContractRecord.from_document(returned_document["route_plan"]),
            self._pricing_for_run(returned_document),
            accepted_project,
        )
        if _submission_binding(returned_document) != submitted_binding:
            raise CloudClientContractError("provider changed the canonical submission lineage")
        if returned_route.display() != submitted_route.display():
            raise CloudClientContractError("provider changed the planner-validated route")
        binding = self._state.run_binding(scope, returned.record_id)
        if binding != submitted_binding:
            raise CloudClientContractError("provider changed the submitted run binding")
        return copy.deepcopy(returned.document())

    def tracked_runs(self) -> tuple[str, ...]:
        _provider, _session, _capabilities, scope = self._connected()
        return self._state.tracked_runs(scope)

    def tracked_artifacts(self) -> tuple[str, ...]:
        _provider, _session, _capabilities, scope = self._connected()
        return self._state.tracked_artifacts(scope)

    def _project_for_run(self, provider: DesktopCloudProvider, session: JsonObject, run: JsonObject) -> AcceptedContractRecord:
        projects = provider.list_projects()
        if not isinstance(projects, tuple) or len(projects) > MAX_CLOUD_PROJECTS:
            raise DesktopCloudRuntimeError("provider returned an unbounded Cloud project collection")
        matches: list[AcceptedContractRecord] = []
        for candidate in projects:
            project = self._accept_platform(candidate)
            document = self._validate_project_scope(project, session)
            linkage = document["linkage"]
            if linkage["state"] == "linked" and linkage["cloud_project_id"] != run["cloud_project_id"]:
                continue
            if jcs_digest(document) == run["project_binding"]["bundle_digest"]:
                matches.append(project)
        if len(matches) != 1:
            raise CloudClientContractError("run project binding is unavailable or ambiguous")
        return matches[0]

    def get_run(self, operation_id: str) -> JsonObject:
        require_credential_free(operation_id)
        operation_id = _safe_identifier(operation_id, "operation id")
        provider, session, capabilities, scope = self._connected()
        binding = self._state.run_binding(scope, operation_id)
        if binding is None:
            raise DesktopCloudRuntimeError("operation is not tracked in this Desktop session scope")
        run = self._accept_cloud(provider.get_run(operation_id))
        project = self._project_for_run(provider, session.document(), run.document())
        document = self._validator.validate_run(run, session=session, capabilities=capabilities, project=project)
        with self._lock:
            planner = self._planner
        if planner is None:
            raise CloudClientStateError("Desktop Cloud planner is unavailable")
        self._inspect_route_with_planner(
            planner,
            session,
            capabilities,
            AcceptedContractRecord.from_document(document["route_plan"]),
            self._pricing_for_run(document),
            project,
        )
        if (
            binding["cloud_project_id"] != document["cloud_project_id"]
            or binding["bundle_digest"] != document["project_binding"]["bundle_digest"]
            or binding["idempotency_key_digest"] != document["idempotency_key_digest"]
            or binding["route_plan_digest"] != document["route_plan_digest"]
        ):
            raise CloudClientContractError("provider run differs from the durable Desktop binding")
        return copy.deepcopy(document)

    def events(self, operation_id: str) -> JsonObject:
        require_credential_free(operation_id)
        operation_id = _safe_identifier(operation_id, "operation id")
        provider, _session, _capabilities, scope = self._connected()
        self.get_run(operation_id)
        previous = self._state.replay(scope, operation_id)
        page = self._accept_cloud(provider.list_run_events(operation_id, after=previous.cursor))
        current, events = accept_event_page(self._validator, page, previous)
        self._state.record_replay(scope, current, events)
        return {
            "operation_id": operation_id,
            "after_cursor": previous.cursor,
            "next_cursor": current.cursor,
            "events": copy.deepcopy(list(events)),
        }

    def cancel_run(self, operation_id: str, *, idempotency_key: str) -> JsonObject:
        require_credential_free([operation_id, idempotency_key])
        operation_id = _safe_identifier(operation_id, "operation id")
        idempotency_key_digest(idempotency_key)
        provider, session, capabilities, scope = self._connected()
        self.get_run(operation_id)
        returned = self._accept_cloud(provider.cancel_run(operation_id, idempotency_key=idempotency_key))
        project = self._project_for_run(provider, session.document(), returned.document())
        document = self._validator.validate_run(returned, session=session, capabilities=capabilities, project=project)
        if returned.record_id != operation_id:
            raise CloudClientContractError("provider changed the cancelled operation identity")
        binding = self._state.run_binding(scope, operation_id)
        with self._lock:
            planner = self._planner
        if binding is None or planner is None:
            raise CloudClientStateError("Desktop Cloud run binding or planner is unavailable")
        self._inspect_route_with_planner(
            planner,
            session,
            capabilities,
            AcceptedContractRecord.from_document(document["route_plan"]),
            self._pricing_for_run(document),
            project,
        )
        if (
            binding["cloud_project_id"] != document["cloud_project_id"]
            or binding["bundle_digest"] != document["project_binding"]["bundle_digest"]
            or binding["idempotency_key_digest"] != document["idempotency_key_digest"]
            or binding["route_plan_digest"] != document["route_plan_digest"]
        ):
            raise CloudClientContractError("provider cancellation changed the accepted submission lineage")
        return copy.deepcopy(document)

    def download_artifact(self, artifact_id: str) -> VerifiedArtifact:
        require_credential_free(artifact_id)
        artifact_id = _safe_identifier(artifact_id, "artifact id")
        provider, _session, _capabilities, scope = self._connected()
        if not self._state.artifact_is_observed(scope, artifact_id):
            raise DesktopCloudRuntimeError("artifact was not observed in this Desktop session scope")
        artifact = provider.download_artifact(artifact_id)
        if not isinstance(artifact, VerifiedArtifact):
            raise DesktopCloudRuntimeError("provider returned a non-canonical artifact")
        metadata = copy.deepcopy(artifact.metadata)
        content = bytes(artifact.content)
        declared_size = metadata.get("size_bytes")
        if len(content) > MAX_DESKTOP_ARTIFACT_BYTES or (isinstance(declared_size, int) and declared_size > MAX_DESKTOP_ARTIFACT_BYTES):
            raise DesktopCloudRuntimeError("artifact exceeds the Desktop Cloud download limit")
        self._validator.validate_artifact(metadata, content)
        if metadata["artifact_id"] != artifact_id:
            raise CloudClientContractError("provider changed the requested artifact identity")
        return VerifiedArtifact(metadata=metadata, content=content)
