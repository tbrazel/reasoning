"""Canonical, provider-neutral Cloud client and session boundary.

The module validates the public Cloud-client contract family and supplies a
deterministic in-memory conformance implementation. It deliberately contains no
endpoint, HTTP, authentication, credential-storage, provider, or UI behavior.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import sys
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from .harnesses.desktop_hybrid import AcceptedContractRecord, HybridPlanningError

JsonObject = dict[str, Any]
ContractResolver = Callable[["ContractRecordReference"], bytes]

CLOUD_CLIENT_VERSION = "1.0.0-alpha.1"
CLOUD_CLIENT_BUNDLE_ID = "reasoning-cloud-client-v1alpha1"
# Updated only when a newly reviewed immutable bundle is accepted.
CLOUD_CLIENT_BUNDLE_DIGEST = "sha256:58f45ca5f9e5a53382b40444399e4cc792d297ead59b6387fd482ab72e0ad6b7"
MAX_CLOUD_CLIENT_RECORD_BYTES = 1_048_576
MAX_PROJECT_CONTENT_BYTES = 67_108_864

_SCHEMA_FILES = {
    "reasoning.cloud-client.capabilities": "capabilities.schema.json",
    "reasoning.cloud-client.run": "run.schema.json",
    "reasoning.cloud-client.run-event-page": "run-event-page.schema.json",
    "reasoning.cloud-client.session": "session.schema.json",
}
_RECORD_ID_FIELDS = {
    "reasoning.cloud-client.capabilities": "snapshot_id",
    "reasoning.cloud-client.run": "operation_id",
    "reasoning.cloud-client.run-event-page": "operation_id",
    "reasoning.cloud-client.session": "state",
}
_DEPENDENCY_ID_FIELDS = {
    "reasoning.platform.compute-target": "target_id",
    "reasoning.platform.harness-provider": "descriptor_id",
    "reasoning.platform.inference-route": "route_id",
}
_ALLOWED_ROUTE_PRICING_PAIRS = frozenset(
    {
        ("1.0.0-alpha.1", "1.0.0-alpha.1"),
        ("1.0.0-alpha.2", "1.0.0-alpha.2"),
    }
)
_TERMINAL_STATES = frozenset({"succeeded", "failed", "cancelled", "outcome_unknown"})
_EVENT_TRANSITIONS = {
    "queued": frozenset({"queued", "running", "cancelled"}),
    "running": frozenset({"running", "succeeded", "failed", "cancelled", "outcome_unknown"}),
    "outcome_unknown": frozenset({"outcome_unknown", "succeeded", "failed", "cancelled"}),
    "succeeded": frozenset({"succeeded"}),
    "failed": frozenset({"failed"}),
    "cancelled": frozenset({"cancelled"}),
}
_SECRET_KEYS = {
    "access_token",
    "api_key",
    "authorization",
    "bearer",
    "bearer_token",
    "client_secret",
    "cookie",
    "credential",
    "credentials",
    "id_token",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "session_token",
    "token",
}
_SECRET_VALUE_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}"),
    re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
)
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:-]{16,255}$")


class CloudClientContractError(ValueError):
    """A public Cloud-client record or cross-record binding is invalid."""


class CloudClientCredentialError(CloudClientContractError):
    """Credential material reached the credential-free public boundary."""


class CloudClientStateError(RuntimeError):
    """The deterministic client cannot perform an operation in this state."""


class CloudClientIdempotencyConflict(CloudClientStateError):
    """One scoped idempotency key was reused for a different request."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> JsonObject:
    result: JsonObject = {}
    for key, value in pairs:
        if key in result:
            raise CloudClientContractError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_nonfinite_number(_value: str) -> None:
    raise CloudClientContractError("non-finite JSON number")


def _parse_json(payload: bytes, *, maximum: int = MAX_CLOUD_CLIENT_RECORD_BYTES) -> JsonObject:
    if not payload or len(payload) > maximum:
        raise CloudClientContractError("record is empty or exceeds the accepted size")
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite_number,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise CloudClientContractError("record is not strict UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise CloudClientContractError("record must be one JSON object")
    return value


def parse_strict_json_object(payload: bytes, *, maximum: int = MAX_CLOUD_CLIENT_RECORD_BYTES) -> JsonObject:
    """Parse one bounded JSON object without losing duplicate-key evidence."""

    if not isinstance(payload, bytes) or not isinstance(maximum, int) or maximum < 1:
        raise CloudClientContractError("strict JSON input requires exact bytes and a positive limit")
    return _parse_json(payload, maximum=maximum)


def _jcs_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def jcs_digest(value: Any) -> str:
    """Return the contract-domain RFC 8785/JCS SHA-256 digest."""

    return f"sha256:{hashlib.sha256(_jcs_bytes(value)).hexdigest()}"


def byte_digest(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def idempotency_key_digest(value: str) -> str:
    """Validate an idempotency key and return the only public projection of it."""

    if not isinstance(value, str) or _IDEMPOTENCY_KEY.fullmatch(value) is None:
        raise CloudClientContractError("idempotency key must contain 16 to 255 safe characters")
    return byte_digest(value.encode("utf-8"))


def canonical_run_request_digest(document: Mapping[str, Any]) -> str:
    """Digest exact submit-run content after removing its derived digests."""

    if not isinstance(document, Mapping):
        raise CloudClientContractError("run request must be one mapping")
    basis = copy.deepcopy(dict(document))
    if basis.get("schema_id") != "reasoning.cloud-client.run":
        raise CloudClientContractError("run request has the wrong schema")
    for key in ("idempotency_key_digest", "request_digest", "route_plan_digest", "pricing_digest"):
        basis.pop(key, None)
    pricing = basis.get("pricing")
    if isinstance(pricing, dict):
        operation = pricing.get("operation")
        if isinstance(operation, dict):
            operation.pop("request_digest", None)
    return jcs_digest({"mutation": "submit_run", "run": basis})


def _walk(value: Any):
    yield value
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def _reject_security_material(value: Any) -> None:
    for item in _walk(value):
        if isinstance(item, dict) and _SECRET_KEYS.intersection(key.casefold() for key in item):
            raise CloudClientCredentialError("credential-like field is forbidden")
        if isinstance(item, str) and any(pattern.search(item) for pattern in _SECRET_VALUE_PATTERNS):
            raise CloudClientCredentialError("credential-like value is forbidden")


def require_credential_free(value: Any) -> None:
    """Reject credential-shaped fields and values at a client integration seam."""

    _reject_security_material(value)


def _time(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise CloudClientContractError(f"{label} is not a timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise CloudClientContractError(f"{label} is not a timestamp") from error
    if parsed.tzinfo is None:
        raise CloudClientContractError(f"{label} is not an offset-aware timestamp")
    return parsed


@dataclass(frozen=True)
class ContractRecordReference:
    schema_id: str
    schema_version: str
    record_id: str
    digest: str

    @classmethod
    def from_document(cls, value: Mapping[str, Any]) -> ContractRecordReference:
        expected = {"schema_id", "schema_version", "record_id", "digest"}
        if set(value) != expected or not all(isinstance(value[key], str) for key in expected):
            raise CloudClientContractError("contract reference has an unsupported shape")
        return cls(**{key: value[key] for key in expected})


class CloudClientContractValidator:
    """Offline validator for one exact Cloud-client bundle and its dependencies."""

    def __init__(self, repository_root: Path, *, frozen_layout: bool = False) -> None:
        self.repository_root = repository_root.resolve()
        self.frozen_layout = frozen_layout
        self.contract_root = self.repository_root / "contracts" / "cloud-client" / "v1alpha1"
        self._schemas: dict[str, JsonObject] = {}
        self._registry = Registry()
        schema_paths = [
            *sorted(self.contract_root.glob("*.schema.json")),
            self.repository_root / "contracts" / "platform" / "v1alpha1" / "common.schema.json",
            self.repository_root / "contracts" / "platform" / "v1alpha1" / "route-plan.schema.json",
            self.repository_root / "contracts" / "platform" / "v1alpha1" / "pricing.schema.json",
            self.repository_root / "contracts" / "platform" / "v1alpha2" / "common.schema.json",
            self.repository_root / "contracts" / "platform" / "v1alpha2" / "route-plan.schema.json",
            self.repository_root / "contracts" / "platform" / "v1alpha2" / "pricing.schema.json",
            self.repository_root / "contracts" / "executor" / "v1alpha1" / "artifact.schema.json",
        ]
        for path in schema_paths:
            document = _parse_json(path.read_bytes())
            Draft202012Validator.check_schema(document)
            schema_id = document.get("$id")
            if not isinstance(schema_id, str) or schema_id in self._schemas:
                raise CloudClientContractError("schema identity is missing or duplicated")
            self._schemas[schema_id] = document
            self._registry = self._registry.with_resource(schema_id, Resource.from_contents(document))
        self.compatibility = _parse_json((self.contract_root / "compatibility.json").read_bytes())
        self.manifest = _parse_json((self.contract_root / "bundle-manifest.json").read_bytes())
        self._verify_bundle()
        self._verify_owned_asset_projection()

    def _schema(self, filename: str) -> JsonObject:
        schema_id = f"https://schemas.reasoning.invalid/cloud-client/v1alpha1/{filename}"
        try:
            return self._schemas[schema_id]
        except KeyError as error:
            raise CloudClientContractError("required Cloud-client schema is unavailable") from error

    def _validate(self, filename: str, document: JsonObject) -> None:
        validator = Draft202012Validator(
            self._schema(filename),
            registry=self._registry,
            format_checker=FormatChecker(),
        )
        error = next(validator.iter_errors(document), None)
        if error is not None:
            raise CloudClientContractError(f"record failed {filename} validation") from error

    def _verify_bundle(self) -> None:
        self._validate("compatibility.schema.json", self.compatibility)
        self._validate("contract-bundle.schema.json", self.manifest)
        if self.manifest.get("bundle_id") != CLOUD_CLIENT_BUNDLE_ID:
            raise CloudClientContractError("Cloud-client bundle identity changed")
        expected_bundle_digest = jcs_digest({key: value for key, value in self.manifest.items() if key != "bundle_digest"})
        if self.manifest.get("bundle_digest") != expected_bundle_digest:
            raise CloudClientContractError("Cloud-client bundle digest is invalid")
        if expected_bundle_digest != CLOUD_CLIENT_BUNDLE_DIGEST:
            raise CloudClientContractError("Cloud-client bundle is not the accepted immutable bundle")

        members = self.manifest.get("members")
        dependencies = self.manifest.get("dependencies")
        if not isinstance(members, list) or not isinstance(dependencies, list):
            raise CloudClientContractError("Cloud-client manifest lists are invalid")
        member_paths = [item.get("path") for item in members if isinstance(item, dict)]
        dependency_paths = [item.get("path") for item in dependencies if isinstance(item, dict)]
        if member_paths != sorted(member_paths) or len(member_paths) != len(set(member_paths)):
            raise CloudClientContractError("Cloud-client manifest members are not sorted and unique")
        if dependency_paths != sorted(dependency_paths) or len(dependency_paths) != len(set(dependency_paths)):
            raise CloudClientContractError("Cloud-client dependencies are not sorted and unique")
        if dependencies != self.compatibility.get("dependencies"):
            raise CloudClientContractError("manifest and compatibility dependencies differ")
        for item in (*members, *dependencies):
            if not isinstance(item, dict) or set(item).difference({"id", "version", "path", "digest"}):
                raise CloudClientContractError("manifest entry has an unsupported shape")
            path = self.repository_root / str(item["path"])
            try:
                payload = path.read_bytes()
            except OSError as error:
                raise CloudClientContractError("manifest member is unavailable") from error
            if byte_digest(payload) != item.get("digest"):
                raise CloudClientContractError("manifest member digest mismatch")

    def _verify_owned_asset_projection(self) -> None:
        """Reject missing or additional Cloud-client runtime inputs.

        The source checkout also carries one living README. Frozen Desktop
        builds contain only the immutable bundle, its conformance members, and
        the manifest-declared dependencies.
        """

        entries = [*self.manifest["members"], *self.manifest["dependencies"]]
        expected = {
            "contracts/cloud-client/v1alpha1/bundle-manifest.json",
            *(str(item["path"]) for item in entries if "/cloud-client/" in str(item["path"])),
        }
        roots = (
            self.repository_root / "contracts" / "cloud-client" / "v1alpha1",
            self.repository_root / "conformance" / "cloud-client" / "v1alpha1",
        )
        observed: set[str] = set()
        try:
            for root in roots:
                if root.is_symlink() or not root.is_dir():
                    raise OSError("Cloud-client asset directory is unavailable")
                for path in root.rglob("*"):
                    if path.is_dir():
                        if path.is_symlink():
                            raise OSError("Cloud-client asset directory is a symlink")
                        continue
                    if path.is_symlink() or not path.is_file():
                        raise OSError("Cloud-client asset is not a regular file")
                    observed.add(path.relative_to(self.repository_root).as_posix())
        except OSError as error:
            raise CloudClientContractError("Cloud-client runtime asset projection is unavailable") from error
        allowed = set(expected)
        if not self.frozen_layout:
            allowed.add("contracts/cloud-client/v1alpha1/README.md")
        if observed != allowed:
            raise CloudClientContractError("Cloud-client runtime asset projection does not match the accepted bundle")

    def accept(self, payload: bytes) -> AcceptedCloudClientRecord:
        document = _parse_json(payload)
        _reject_security_material(document)
        schema_id = document.get("schema_id")
        filename = _SCHEMA_FILES.get(str(schema_id))
        if filename is None or document.get("schema_version") != CLOUD_CLIENT_VERSION:
            raise CloudClientContractError("unknown Cloud-client contract identity")
        self._validate(filename, document)
        return AcceptedCloudClientRecord._accepted(payload, document)

    def validate_capabilities(self, record: AcceptedCloudClientRecord, resolver: ContractResolver) -> JsonObject:
        document = self._document(record, "reasoning.cloud-client.capabilities")
        if document["compatibility_digest"] != jcs_digest(self.compatibility):
            raise CloudClientContractError("capabilities compatibility binding changed")
        pairs = {(item["route_plan"], item["pricing"]) for item in document["route_pricing_pairs"]}
        if not pairs or not pairs.issubset(_ALLOWED_ROUTE_PRICING_PAIRS):
            raise CloudClientContractError("capabilities contain an unsupported route/pricing pair")
        references: list[ContractRecordReference] = []
        for key in ("harnesses", "inference_routes", "compute_targets"):
            references.extend(ContractRecordReference.from_document(item) for item in document[key])
        identities = {(item.schema_id, item.record_id) for item in references}
        if len(identities) != len(references):
            raise CloudClientContractError("capability references are ambiguous")
        for reference in references:
            payload = resolver(reference)
            if byte_digest(payload) != reference.digest:
                raise CloudClientContractError("capability descriptor digest mismatch")
            dependency = _parse_json(payload)
            _reject_security_material(dependency)
            try:
                accepted = AcceptedContractRecord.from_bytes(payload)
                observed = accepted.document()
            except HybridPlanningError as error:
                raise CloudClientContractError("capability descriptor is not canonical") from error
            identity_field = _DEPENDENCY_ID_FIELDS.get(reference.schema_id)
            if (
                identity_field is None
                or observed.get("schema_id") != reference.schema_id
                or observed.get("schema_version") != reference.schema_version
                or observed.get(identity_field) != reference.record_id
            ):
                raise CloudClientContractError("capability descriptor identity mismatch")
        return document

    def validate_session(
        self,
        session: AcceptedCloudClientRecord,
        capabilities: AcceptedCloudClientRecord | None = None,
    ) -> JsonObject:
        document = self._document(session, "reasoning.cloud-client.session")
        if document["state"] == "disconnected":
            if capabilities is not None:
                raise CloudClientContractError("disconnected session cannot bind capabilities")
            return document
        if capabilities is None:
            raise CloudClientContractError("connected session lacks capabilities")
        capability_document = self._document(capabilities, "reasoning.cloud-client.capabilities")
        if (
            document["actor"] != capability_document["actor"]
            or document["tenant_id"] != capability_document["tenant_id"]
            or document["capability_snapshot_digest"] != jcs_digest(capability_document)
        ):
            raise CloudClientContractError("session capability or scope binding changed")
        return document

    def validate_run(
        self,
        run: AcceptedCloudClientRecord,
        *,
        session: AcceptedCloudClientRecord,
        capabilities: AcceptedCloudClientRecord,
        project: AcceptedContractRecord,
    ) -> JsonObject:
        document = self._document(run, "reasoning.cloud-client.run")
        session_document = self.validate_session(session, capabilities)
        if session_document["state"] != "connected":
            raise CloudClientContractError("run requires a connected session")
        capability_document = self._document(capabilities, "reasoning.cloud-client.capabilities")
        project_document = project.document()
        if project.schema_id != "reasoning.platform.project-bundle":
            raise CloudClientContractError("run project is not a canonical project bundle")
        route = document["route_plan"]
        pricing = document["pricing"]
        binding = document["project_binding"]
        project_value = project_document["project"]
        persistence = route["persistence"]
        if (
            document["actor"] != session_document["actor"]
            or document["actor"] != route["actor"]
            or document["tenant_id"] != session_document["tenant_id"]
            or document["tenant_id"] != route["tenant_id"]
            or document["capability_snapshot_digest"] != session_document["capability_snapshot_digest"]
            or document["operation_id"] != route["operation_id"]
            or binding["project_bundle_id"] != project_document["bundle_id"]
            or binding["project_id"] != project_value["project_id"]
            or binding["project_revision_digest"] != project_value["revision_digest"]
            or binding["bundle_digest"] != jcs_digest(project_document)
            or persistence["project_bundle_id"] != binding["project_bundle_id"]
            or persistence["project_id"] != binding["project_id"]
            or persistence["project_revision_digest"] != binding["project_revision_digest"]
            or binding.get("transfer_manifest_id") != persistence.get("transfer_manifest_id")
            or document["route_plan_digest"] != jcs_digest(route)
        ):
            raise CloudClientContractError("run scope, project, or route binding changed")
        harness_digests = {item["digest"] for item in capability_document["harnesses"]}
        inference_identities = {(item["record_id"], item["digest"]) for item in capability_document["inference_routes"]}
        compute_identities = {(item["record_id"], item["digest"]) for item in capability_document["compute_targets"]}
        if (
            route["harness"]["descriptor_digest"] not in harness_digests
            or (route["inference"]["route_id"], route["inference"]["route_digest"]) not in inference_identities
            or (route["compute"]["target_id"], route["compute"]["target_digest"]) not in compute_identities
        ):
            raise CloudClientContractError("run selected a descriptor outside the capability snapshot")
        pairs = {(item["route_plan"], item["pricing"]) for item in capability_document["route_pricing_pairs"]}
        if pricing is None:
            if document["state"] in _TERMINAL_STATES or document["pricing_digest"] is not None:
                raise CloudClientContractError("terminal run lacks complete pricing")
            route_version = route["schema_version"]
            if not any(pair[0] == route_version for pair in pairs):
                raise CloudClientContractError("active run route version was not negotiated")
        else:
            pair = (route["schema_version"], pricing["schema_version"])
            pricing_operation = pricing["operation"]
            if (
                pair not in pairs
                or pair not in _ALLOWED_ROUTE_PRICING_PAIRS
                or document["pricing_digest"] != jcs_digest(pricing)
                or route["pricing_lifecycle_id"] != pricing["pricing_lifecycle_id"]
                or pricing_operation["operation_id"] != document["operation_id"]
                or pricing_operation["actor"] != document["actor"]
                or pricing_operation["tenant_id"] != document["tenant_id"]
                or pricing_operation["project_id"] != binding["project_id"]
                or pricing_operation["route_plan_id"] != route["route_plan_id"]
                or pricing_operation["request_digest"] != document["request_digest"]
                or idempotency_key_digest(pricing_operation["idempotency_key"]) != document["idempotency_key_digest"]
            ):
                raise CloudClientContractError("run pricing binding changed")
        created = _time(document["created_at"], "created_at")
        updated = _time(document["updated_at"], "updated_at")
        terminal = document["terminal_at"]
        if (
            updated < created
            or terminal is not None
            and (_time(terminal, "terminal_at") < created or _time(terminal, "terminal_at") > updated)
        ):
            raise CloudClientContractError("run timestamps are not monotone")
        if document["request_digest"] != canonical_run_request_digest(document):
            raise CloudClientContractError("run request digest does not cover the canonical mutation request")
        return document

    def validate_event_page(self, page: AcceptedCloudClientRecord) -> JsonObject:
        document = self._document(page, "reasoning.cloud-client.run-event-page")
        operation_id = document["operation_id"]
        after = document["after_cursor"]
        events = document["events"]
        previous_sequence = 0
        previous_time: datetime | None = None
        previous_state: str | None = None
        event_ids: set[str] = set()
        cursors: set[str] = set()
        for event in events:
            occurred = _time(event["occurred_at"], "event occurred_at")
            if (
                event["operation_id"] != operation_id
                or event["cursor"] == after
                or event["sequence"] <= previous_sequence
                or previous_time is not None
                and occurred < previous_time
                or event["event_id"] in event_ids
                or event["cursor"] in cursors
            ):
                raise CloudClientContractError("event page binding or ordering changed")
            if previous_state is not None and event["state"] not in _EVENT_TRANSITIONS[previous_state]:
                raise CloudClientContractError("event page contains an invalid state transition")
            previous_sequence = event["sequence"]
            previous_time = occurred
            previous_state = event["state"]
            event_ids.add(event["event_id"])
            cursors.add(event["cursor"])
        if after is None and events and (events[0]["sequence"] != 1 or events[0]["type"] != "run.accepted"):
            raise CloudClientContractError("initial event history must begin with acceptance")
        expected_cursor = events[-1]["cursor"] if events else after
        if document["next_cursor"] != expected_cursor:
            raise CloudClientContractError("event page next cursor is inconsistent")
        return document

    def validate_artifact(self, metadata: JsonObject, content: bytes) -> None:
        _reject_security_material(metadata)
        validator = Draft202012Validator(
            self._schemas["https://schemas.reasoning.invalid/executor/v1alpha1/artifact.schema.json"],
            registry=self._registry,
            format_checker=FormatChecker(),
        )
        if next(validator.iter_errors(metadata), None) is not None:
            raise CloudClientContractError("artifact metadata is not canonical")
        if metadata["size_bytes"] != len(content) or metadata["digest"] != byte_digest(content):
            raise CloudClientContractError("artifact content failed size or digest verification")

    @staticmethod
    def _document(record: AcceptedCloudClientRecord, schema_id: str) -> JsonObject:
        document = record.document()
        if record.schema_id != schema_id or document.get("schema_id") != schema_id:
            raise CloudClientContractError("Cloud-client record has the wrong schema")
        return document


def _cloud_client_repository_root() -> tuple[Path, bool]:
    if getattr(sys, "frozen", False):
        extraction_root = getattr(sys, "_MEIPASS", None)
        if not isinstance(extraction_root, str) or not extraction_root or not Path(extraction_root).is_absolute():
            raise CloudClientContractError("frozen Desktop Cloud-client root is unavailable")
        return Path(extraction_root), True
    return Path(__file__).resolve().parents[2], False


@lru_cache(maxsize=4)
def _canonical_cloud_client_validator(repository_root: Path, frozen_layout: bool) -> CloudClientContractValidator:
    return CloudClientContractValidator(repository_root, frozen_layout=frozen_layout)


def canonical_cloud_client_validator() -> CloudClientContractValidator:
    return _canonical_cloud_client_validator(*_cloud_client_repository_root())


@dataclass(frozen=True, init=False)
class AcceptedCloudClientRecord:
    """Exact accepted Cloud-client bytes, identity, and byte digest."""

    schema_id: str
    schema_version: str
    record_id: str
    digest: str
    _payload: bytes = field(repr=False)

    @classmethod
    def from_bytes(
        cls,
        payload: bytes,
        *,
        validator: CloudClientContractValidator | None = None,
    ) -> AcceptedCloudClientRecord:
        return (validator or canonical_cloud_client_validator()).accept(payload)

    @classmethod
    def from_document(
        cls,
        document: JsonObject,
        *,
        validator: CloudClientContractValidator | None = None,
    ) -> AcceptedCloudClientRecord:
        return cls.from_bytes(_jcs_bytes(document), validator=validator)

    @classmethod
    def _accepted(cls, payload: bytes, document: JsonObject) -> AcceptedCloudClientRecord:
        schema_id = str(document["schema_id"])
        identity_field = _RECORD_ID_FIELDS[schema_id]
        instance = cls.__new__(cls)
        object.__setattr__(instance, "schema_id", schema_id)
        object.__setattr__(instance, "schema_version", str(document["schema_version"]))
        object.__setattr__(instance, "record_id", str(document[identity_field]))
        object.__setattr__(instance, "digest", byte_digest(payload))
        object.__setattr__(instance, "_payload", bytes(payload))
        return instance

    def document(self) -> JsonObject:
        payload = bytes(self._payload)
        if byte_digest(payload) != self.digest:
            raise CloudClientContractError("accepted Cloud-client bytes changed")
        document = _parse_json(payload)
        identity_field = _RECORD_ID_FIELDS.get(self.schema_id)
        if (
            identity_field is None
            or document.get("schema_id") != self.schema_id
            or document.get("schema_version") != self.schema_version
            or document.get(identity_field) != self.record_id
        ):
            raise CloudClientContractError("accepted Cloud-client identity changed")
        _reject_security_material(document)
        return document

    def to_bytes(self) -> bytes:
        self.document()
        return bytes(self._payload)


@dataclass(frozen=True)
class VerifiedArtifact:
    metadata: JsonObject
    content: bytes


@dataclass(frozen=True)
class EventReplayState:
    operation_id: str
    cursor: str | None = None
    last_sequence: int = 0
    last_state: str | None = None
    last_occurred_at: str | None = None
    event_digests: tuple[tuple[str, str], ...] = ()


def accept_event_page(
    validator: CloudClientContractValidator,
    page: AcceptedCloudClientRecord,
    state: EventReplayState,
) -> tuple[EventReplayState, tuple[JsonObject, ...]]:
    """Atomically accept a page and return only events not seen by identity."""

    document = validator.validate_event_page(page)
    if document["operation_id"] != state.operation_id:
        raise CloudClientContractError("event page belongs to another operation")
    known = dict(state.event_digests)
    new_events: list[JsonObject] = []
    last_sequence = state.last_sequence
    last_state = state.last_state
    last_occurred = _time(state.last_occurred_at, "replay last_occurred_at") if state.last_occurred_at is not None else None
    has_history = state.cursor is not None or state.last_sequence != 0 or bool(known)
    if has_history != (last_state is not None) or (last_state is None) != (last_occurred is None):
        raise CloudClientContractError("replay cursor, sequence, state, and timestamp must be retained together")
    if has_history and (state.cursor is None or state.last_sequence < 1 or not known):
        raise CloudClientContractError("replay history is incomplete")
    if last_state is not None and last_state not in _EVENT_TRANSITIONS:
        raise CloudClientContractError("replay state is unknown")
    for event in document["events"]:
        digest = jcs_digest(event)
        existing = known.get(event["event_id"])
        if existing is not None:
            if existing != digest:
                raise CloudClientContractError("redelivered event identity changed")
            continue
        if document["after_cursor"] != state.cursor or event["sequence"] <= last_sequence:
            raise CloudClientContractError("new event does not continue the accepted cursor")
        occurred = _time(event["occurred_at"], "event occurred_at")
        if last_state is not None and event["state"] not in _EVENT_TRANSITIONS[last_state]:
            raise CloudClientContractError("event crosses the page boundary with an invalid state transition")
        if last_occurred is not None and occurred < last_occurred:
            raise CloudClientContractError("event crosses the page boundary with a decreasing timestamp")
        known[event["event_id"]] = digest
        new_events.append(copy.deepcopy(event))
        last_sequence = event["sequence"]
        last_state = event["state"]
        last_occurred = occurred
    cursor = document["next_cursor"] if new_events else state.cursor
    return (
        EventReplayState(
            operation_id=state.operation_id,
            cursor=cursor,
            last_sequence=last_sequence,
            last_state=last_state,
            last_occurred_at=new_events[-1]["occurred_at"] if new_events else state.last_occurred_at,
            event_digests=tuple(sorted(known.items())),
        ),
        tuple(new_events),
    )


@runtime_checkable
class CloudSessionProvider(Protocol):
    """Provider-owned connection lifecycle returning only canonical views."""

    def current_session(self) -> AcceptedCloudClientRecord: ...

    def connect(self) -> AcceptedCloudClientRecord: ...

    def disconnect(self) -> AcceptedCloudClientRecord: ...


@runtime_checkable
class CloudClient(Protocol):
    """Transport-neutral Cloud resources scoped by the provider session."""

    def capabilities(self) -> AcceptedCloudClientRecord: ...

    def resolve_contract(self, reference: ContractRecordReference) -> AcceptedContractRecord: ...

    def list_projects(self) -> tuple[AcceptedContractRecord, ...]: ...

    def put_project(
        self,
        cloud_project_id: str,
        project: AcceptedContractRecord,
        contents: Mapping[str, bytes],
        *,
        idempotency_key: str,
    ) -> AcceptedContractRecord: ...

    def submit_run(
        self,
        run: AcceptedCloudClientRecord,
        project: AcceptedContractRecord,
        *,
        idempotency_key: str,
    ) -> AcceptedCloudClientRecord: ...

    def get_run(self, operation_id: str) -> AcceptedCloudClientRecord: ...

    def list_run_events(self, operation_id: str, *, after: str | None) -> AcceptedCloudClientRecord: ...

    def cancel_run(self, operation_id: str, *, idempotency_key: str) -> AcceptedCloudClientRecord: ...

    def download_artifact(self, artifact_id: str) -> VerifiedArtifact: ...


@dataclass
class InMemoryCloudStore:
    """Durable state shared by deterministic client restarts in conformance tests."""

    projects: dict[tuple[str, str, str, str], tuple[AcceptedContractRecord, dict[str, bytes]]] = field(default_factory=dict)
    runs: dict[tuple[str, str, str, str], AcceptedCloudClientRecord] = field(default_factory=dict)
    events: dict[tuple[str, str, str, str], tuple[JsonObject, ...]] = field(default_factory=dict)
    artifacts: dict[tuple[str, str, str, str], tuple[JsonObject, bytes]] = field(default_factory=dict)
    mutations: dict[tuple[str, str, str, str, str], tuple[str, Any]] = field(default_factory=dict)


class InMemoryCloudClient(CloudSessionProvider, CloudClient):
    """Deterministic conformance client; not a product service or transport."""

    def __init__(
        self,
        *,
        validator: CloudClientContractValidator,
        disconnected_session: AcceptedCloudClientRecord,
        connected_session: AcceptedCloudClientRecord,
        capabilities: AcceptedCloudClientRecord,
        contract_records: Mapping[str, bytes],
        store: InMemoryCloudStore | None = None,
    ) -> None:
        self._validator = validator
        self._disconnected = disconnected_session
        self._connected = connected_session
        self._capabilities = capabilities
        self._contract_records = {key: bytes(value) for key, value in contract_records.items()}
        self._store = store or InMemoryCloudStore()
        self._lock = threading.RLock()
        self._session = disconnected_session
        validator.validate_session(disconnected_session)
        validator.validate_capabilities(capabilities, self._resolve_bytes)
        validator.validate_session(connected_session, capabilities)

    @property
    def store(self) -> InMemoryCloudStore:
        return self._store

    def current_session(self) -> AcceptedCloudClientRecord:
        return self._session

    def connect(self) -> AcceptedCloudClientRecord:
        with self._lock:
            self._session = self._connected
            return self._session

    def disconnect(self) -> AcceptedCloudClientRecord:
        with self._lock:
            self._session = self._disconnected
            return self._session

    def restarted(self) -> InMemoryCloudClient:
        return type(self)(
            validator=self._validator,
            disconnected_session=self._disconnected,
            connected_session=self._connected,
            capabilities=self._capabilities,
            contract_records=self._contract_records,
            store=self._store,
        )

    def _scope(self) -> tuple[str, str, str]:
        session = self._validator.validate_session(self._session, self._capabilities if self._session.record_id == "connected" else None)
        if session["state"] != "connected":
            raise CloudClientStateError("Cloud client is disconnected")
        return session["actor"]["type"], session["actor"]["id"], session["tenant_id"]

    def capabilities(self) -> AcceptedCloudClientRecord:
        self._scope()
        self._validator.validate_capabilities(self._capabilities, self._resolve_bytes)
        return self._capabilities

    def _resolve_bytes(self, reference: ContractRecordReference) -> bytes:
        try:
            return self._contract_records[reference.digest]
        except KeyError as error:
            raise CloudClientContractError("referenced contract is unavailable") from error

    def resolve_contract(self, reference: ContractRecordReference) -> AcceptedContractRecord:
        self._scope()
        payload = self._resolve_bytes(reference)
        if byte_digest(payload) != reference.digest:
            raise CloudClientContractError("referenced contract digest mismatch")
        try:
            accepted = AcceptedContractRecord.from_bytes(payload)
        except HybridPlanningError as error:
            raise CloudClientContractError("referenced contract is not canonical") from error
        identity_field = _DEPENDENCY_ID_FIELDS.get(reference.schema_id)
        document = accepted.document()
        if identity_field is None or document.get(identity_field) != reference.record_id:
            raise CloudClientContractError("referenced contract identity mismatch")
        return accepted

    def list_projects(self) -> tuple[AcceptedContractRecord, ...]:
        scope = self._scope()
        with self._lock:
            keys = sorted(key for key in self._store.projects if key[:3] == scope)
            return tuple(self._store.projects[key][0] for key in keys)

    def put_project(
        self,
        cloud_project_id: str,
        project: AcceptedContractRecord,
        contents: Mapping[str, bytes],
        *,
        idempotency_key: str,
    ) -> AcceptedContractRecord:
        actor_type, actor_id, tenant_id = self._scope()
        if project.schema_id != "reasoning.platform.project-bundle":
            raise CloudClientContractError("project mutation requires a canonical project bundle")
        document = project.document()
        _reject_security_material(document)
        if document["export"]["created_by"] != {"type": actor_type, "id": actor_id}:
            raise CloudClientContractError("project actor differs from the connected session")
        linkage = document["linkage"]
        if linkage["state"] == "linked" and (linkage["tenant_id"] != tenant_id or linkage["cloud_project_id"] != cloud_project_id):
            raise CloudClientContractError("linked project differs from the connected scope")
        expected = {item["path"]: item for item in document["contents"]}
        supplied = {path: bytes(payload) for path, payload in contents.items()}
        if set(expected) != set(supplied) or sum(map(len, supplied.values())) > MAX_PROJECT_CONTENT_BYTES:
            raise CloudClientContractError("project content mapping is incomplete or too large")
        for path, descriptor in expected.items():
            payload = supplied[path]
            if len(payload) != descriptor["size_bytes"] or byte_digest(payload) != descriptor["digest"]:
                raise CloudClientContractError("project content failed size or digest verification")
        key_digest = idempotency_key_digest(idempotency_key)
        request_digest = jcs_digest(
            {
                "cloud_project_id": cloud_project_id,
                "project_bundle": document,
                "contents": [{"path": path, "digest": byte_digest(supplied[path])} for path in sorted(supplied)],
            }
        )
        scope = (actor_type, actor_id, tenant_id)
        key = (*scope, cloud_project_id, key_digest)
        with self._lock:
            existing = self._store.mutations.get(key)
            if existing is not None:
                if existing[0] != request_digest:
                    raise CloudClientIdempotencyConflict("project idempotency key is bound to another request")
                return existing[1]
            self._store.projects[(*scope, cloud_project_id)] = (project, supplied)
            self._store.mutations[key] = (request_digest, project)
            return project

    def submit_run(
        self,
        run: AcceptedCloudClientRecord,
        project: AcceptedContractRecord,
        *,
        idempotency_key: str,
    ) -> AcceptedCloudClientRecord:
        actor_type, actor_id, tenant_id = self._scope()
        document = self._validator.validate_run(
            run,
            session=self._session,
            capabilities=self._capabilities,
            project=project,
        )
        if document["idempotency_key_digest"] != idempotency_key_digest(idempotency_key):
            raise CloudClientContractError("run idempotency digest does not match the caller key")
        cloud_project_id = document["cloud_project_id"]
        with self._lock:
            scope = (actor_type, actor_id, tenant_id)
            stored = self._store.projects.get((*scope, cloud_project_id))
            if stored is None or stored[0].document() != project.document():
                raise CloudClientContractError("run project has not been accepted in this scope")
            key = (*scope, cloud_project_id, document["idempotency_key_digest"])
            request_digest = canonical_run_request_digest(document)
            existing = self._store.mutations.get(key)
            if existing is not None:
                if existing[0] != request_digest:
                    raise CloudClientIdempotencyConflict("run idempotency key is bound to another request")
                return existing[1]
            operation_id = document["operation_id"]
            scoped_operation = (*scope, operation_id)
            previous = self._store.runs.get(scoped_operation)
            if previous is not None and previous.document() != document:
                raise CloudClientIdempotencyConflict("operation identity is already bound")
            self._store.runs[scoped_operation] = run
            self._store.mutations[key] = (request_digest, run)
            return run

    def get_run(self, operation_id: str) -> AcceptedCloudClientRecord:
        scope = self._scope()
        try:
            return self._store.runs[(*scope, operation_id)]
        except KeyError as error:
            raise CloudClientStateError("run is unavailable") from error

    def seed_event_page(self, page: AcceptedCloudClientRecord) -> None:
        scope = self._scope()
        document = self._validator.validate_event_page(page)
        with self._lock:
            self._store.events[(*scope, document["operation_id"])] = tuple(copy.deepcopy(document["events"]))
            for event in document["events"]:
                if event["type"] == "artifact.committed":
                    artifact = copy.deepcopy(event["payload"]["artifact"])
                    self._store.artifacts.setdefault((*scope, artifact["artifact_id"]), (artifact, b""))

    def list_run_events(self, operation_id: str, *, after: str | None) -> AcceptedCloudClientRecord:
        scope = self._scope()
        events = self._store.events.get((*scope, operation_id), ())
        start = 0
        if after is not None:
            indexes = [index for index, event in enumerate(events) if event["cursor"] == after]
            if len(indexes) != 1:
                raise CloudClientContractError("event cursor is not retained")
            start = indexes[0] + 1
        selected = copy.deepcopy(list(events[start:]))
        document: JsonObject = {
            "schema_id": "reasoning.cloud-client.run-event-page",
            "schema_version": CLOUD_CLIENT_VERSION,
            "operation_id": operation_id,
            "after_cursor": after,
            "events": selected,
            "next_cursor": selected[-1]["cursor"] if selected else after,
            "complete": True,
            "cursor_semantics": {
                "cursor": "opaque",
                "after": "exclusive",
                "delivery": "at_least_once",
                "deduplicate_by": "event_id",
            },
        }
        record = AcceptedCloudClientRecord.from_document(document, validator=self._validator)
        self._validator.validate_event_page(record)
        return record

    def cancel_run(self, operation_id: str, *, idempotency_key: str) -> AcceptedCloudClientRecord:
        scope = self._scope()
        run = self.get_run(operation_id)
        document = run.document()
        key_digest = idempotency_key_digest(idempotency_key)
        key = (*scope, operation_id, key_digest)
        with self._lock:
            existing = self._store.mutations.get(key)
            if existing is not None:
                return existing[1]
            if document["state"] not in _TERMINAL_STATES:
                document["cancellation_requested"] = True
                document["request_digest"] = canonical_run_request_digest(document)
                run = AcceptedCloudClientRecord.from_document(document, validator=self._validator)
                self._store.runs[(*scope, operation_id)] = run
            self._store.mutations[key] = (jcs_digest({"operation_id": operation_id}), run)
            return run

    def seed_artifact(self, metadata: JsonObject, content: bytes) -> None:
        scope = self._scope()
        self._validator.validate_artifact(metadata, content)
        with self._lock:
            self._store.artifacts[(*scope, str(metadata["artifact_id"]))] = (
                copy.deepcopy(metadata),
                bytes(content),
            )

    def download_artifact(self, artifact_id: str) -> VerifiedArtifact:
        scope = self._scope()
        try:
            metadata, content = self._store.artifacts[(*scope, artifact_id)]
        except KeyError as error:
            raise CloudClientStateError("artifact is unavailable") from error
        self._validator.validate_artifact(metadata, content)
        return VerifiedArtifact(metadata=copy.deepcopy(metadata), content=bytes(content))
