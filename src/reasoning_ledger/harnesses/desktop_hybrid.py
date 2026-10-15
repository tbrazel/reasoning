"""Pure Desktop planning and project-consent enforcement for hybrid routes.

The public wire records remain owned by the immutable platform bundles. This
module consumes exact alpha.1 route/pricing pairs and exact alpha.2
route/pricing pairs while keeping every unchanged descriptor and Executor
dependency on alpha.1. It checks semantic bindings that JSON Schema cannot
compare and returns credential-free Desktop views. It does not discover
providers, read credentials, upload project data, or invoke a harness.
"""

from __future__ import annotations

import copy
import fcntl
import fnmatch
import hashlib
import itertools
import json
import os
import re
import sqlite3
import stat
import sys
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field, fields
from datetime import datetime
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

JsonObject = dict[str, Any]

PLATFORM_ALPHA1_VERSION = "1.0.0-alpha.1"
PLATFORM_ALPHA2_VERSION = "1.0.0-alpha.2"
# Kept as the explicit version of unchanged descriptor and Executor dependencies.
PLATFORM_VERSION = PLATFORM_ALPHA1_VERSION
MAX_CONTRACT_RECORD_BYTES = 1_048_576
MAX_TRANSFER_INPUTS = 32
MAX_TRANSFER_BYTES = 67_108_864
MAX_RETAINED_CLOUD_CLASSES = 5
MAX_RECEIPT_DATABASE_BYTES = 16_777_216

_DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")
_STABLE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]{0,127}$")
_ARTIFACT_ID = re.compile(r"^artifact_[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_TRANSFER_ID = re.compile(r"^transfer_[A-Za-z0-9._-]{3,127}$")
_APPROVAL_ID = re.compile(r"^approval_[A-Za-z0-9._-]{3,127}$")
_SCHEMA_RECORD_IDS = {
    "reasoning.platform.harness-provider": "descriptor_id",
    "reasoning.platform.inference-route": "route_id",
    "reasoning.platform.compute-target": "target_id",
    "reasoning.platform.project-bundle": "bundle_id",
    "reasoning.platform.route-plan": "route_plan_id",
    "reasoning.platform.pricing": "pricing_lifecycle_id",
    "reasoning.platform.local-harness-connector": "connector_grant_id",
}
_SCHEMA_FILENAMES = {schema_id: f"{schema_id.removeprefix('reasoning.platform.')}.schema.json" for schema_id in _SCHEMA_RECORD_IDS}
_SUPPORTED_SCHEMA_VERSIONS = {
    schema_id: frozenset(
        {PLATFORM_ALPHA1_VERSION, PLATFORM_ALPHA2_VERSION}
        if schema_id in {"reasoning.platform.route-plan", "reasoning.platform.pricing"}
        else {PLATFORM_ALPHA1_VERSION}
    )
    for schema_id in _SCHEMA_RECORD_IDS
}


@dataclass(frozen=True)
class _PlatformBundlePin:
    directory: str
    bundle_id: str
    bundle_digest: str
    schema_filenames: tuple[str, ...]


_PLATFORM_BUNDLE_PINS = {
    PLATFORM_ALPHA1_VERSION: _PlatformBundlePin(
        directory="v1alpha1",
        bundle_id="reasoning-platform-v1alpha1",
        bundle_digest="sha256:1c5e3221b21ffb6ecce27407c561b5d7ff2d3647bc435ce986c713c903252eb8",
        schema_filenames=(
            "common.schema.json",
            "compatibility.schema.json",
            "compute-target.schema.json",
            "contract-bundle.schema.json",
            "harness-provider.schema.json",
            "inference-route.schema.json",
            "local-harness-connector.schema.json",
            "pricing.schema.json",
            "project-bundle.schema.json",
            "route-plan.schema.json",
        ),
    ),
    PLATFORM_ALPHA2_VERSION: _PlatformBundlePin(
        directory="v1alpha2",
        bundle_id="reasoning-platform-v1alpha2",
        bundle_digest="sha256:f0988632e10c92095b4de66bc89d193b8931b62a67bedbf562e32794a2175daf",
        schema_filenames=(
            "common.schema.json",
            "compatibility.schema.json",
            "contract-bundle.schema.json",
            "pricing.schema.json",
            "route-plan.schema.json",
        ),
    ),
}
_RECEIPT_VERSION = 1
_APPROVAL_VERSION = 1
_APPROVED_TRANSFER_VERSION = 1
_SQLITE_SIDECAR_SUFFIXES = ("-journal", "-wal", "-shm")
_ROUTE_AUTHORIZATION_ID = re.compile(r"^desktop-route-[0-9a-f]{32}$")
_FEATURE_NAMES = {
    "streaming",
    "tools",
    "skills",
    "structured_output",
    "mcp",
    "approvals",
    "interrupt",
    "steering",
    "checkpoints",
    "subagents",
    "context_compaction",
    "reports_usage",
    "reports_cost",
}
_RETAINED_KINDS = {
    "outputs",
    "logs",
    "operation_metadata",
    "usage",
    "provenance",
}
_RETENTION_POLICIES = {
    "operation_lifetime",
    "account_policy",
    "indefinite_audit",
}
_OMITTED_DATA_CLASSES = (
    "unlisted_project_files",
    "local_harness_state",
    "authentication_material",
    "local_paths",
)
_SECRET_KEYS = {
    "access_token",
    "api_key",
    "authorization",
    "bearer",
    "bearer_token",
    "cookie",
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
)


class HybridPlanningError(ValueError):
    """Base error for fail-closed hybrid planning."""


class UnsupportedHybridRoute(HybridPlanningError):
    """The advertised records cannot support the requested combination."""


class ProjectTransferConsentError(HybridPlanningError):
    """A local-project disclosure is missing, stale, or out of bounds."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> JsonObject:
    result: JsonObject = {}
    for key, value in pairs:
        if key in result:
            raise HybridPlanningError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _parse_document(payload: bytes) -> JsonObject:
    if not payload or len(payload) > MAX_CONTRACT_RECORD_BYTES:
        raise HybridPlanningError("contract record is empty or exceeds the Desktop limit")
    try:
        value = json.loads(payload, object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HybridPlanningError("contract record is not valid UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise HybridPlanningError("contract record must be a JSON object")
    return value


def _platform_contract_root() -> Path:
    """Locate canonical contracts without falling back out of a frozen app."""

    if getattr(sys, "frozen", False):
        extraction_root = getattr(sys, "_MEIPASS", None)
        if not isinstance(extraction_root, str) or not extraction_root or not Path(extraction_root).is_absolute():
            raise HybridPlanningError("frozen Desktop platform contract root is unavailable")
        return Path(extraction_root) / "contracts" / "platform"
    return Path(__file__).resolve().parents[3] / "contracts" / "platform"


@dataclass(frozen=True)
class _VerifiedPlatformBundle:
    schema_version: str
    bundle_id: str
    bundle_digest: str


class _CanonicalPlatformSchemas:
    """Exact repository-owned schema validators for one negotiated version."""

    def __init__(self, version: str, root: Path) -> None:
        try:
            pin = _PLATFORM_BUNDLE_PINS[version]
        except KeyError as error:
            raise HybridPlanningError(f"unsupported platform schema version: {version!r}") from error
        directory = root / pin.directory
        expected_assets = frozenset((*pin.schema_filenames, "compatibility.json", "bundle-manifest.json"))
        try:
            if directory.is_symlink() or not directory.is_dir():
                raise OSError("canonical platform schema directory is unavailable")
            observed_assets = {path.name for path in directory.iterdir()}
            allowed_source_assets = {"README.md"} if not getattr(sys, "frozen", False) else set()
            unexpected_assets = observed_assets.difference(expected_assets, allowed_source_assets)
            missing_assets = expected_assets.difference(observed_assets)
            if unexpected_assets or missing_assets:
                raise ValueError("canonical platform runtime asset set does not match its pin")
            payloads: dict[str, bytes] = {}
            for filename in sorted(expected_assets):
                path = directory / filename
                if path.is_symlink() or not path.is_file():
                    raise OSError("canonical platform runtime asset is not a regular file")
                payloads[filename] = path.read_bytes()
        except (OSError, ValueError) as error:
            raise HybridPlanningError(f"canonical platform schemas are unavailable for {version}: {error}") from error

        manifest = _parse_document(payloads["bundle-manifest.json"])
        compatibility = _parse_document(payloads["compatibility.json"])
        schemas: dict[str, JsonObject] = {}
        schema_ids: set[str] = set()
        for filename in pin.schema_filenames:
            schema = _parse_document(payloads[filename])
            schema_id = schema.get("$id")
            expected_schema_id = f"https://schemas.reasoning.invalid/platform/{pin.directory}/{filename}"
            if schema_id in schema_ids:
                raise HybridPlanningError(f"canonical platform schema ID is duplicated for {version}")
            if schema_id != expected_schema_id:
                raise HybridPlanningError(f"canonical platform schema has the wrong ID for {version}: {filename}")
            schema_ids.add(schema_id)
            schemas[filename] = schema

        if (
            manifest.get("schema_id") != "reasoning.platform.contract-bundle"
            or manifest.get("schema_version") != version
            or manifest.get("bundle_id") != pin.bundle_id
            or manifest.get("bundle_digest") != pin.bundle_digest
        ):
            raise HybridPlanningError(f"canonical platform bundle identity does not match its pin for {version}")
        manifest_without_digest = dict(manifest)
        manifest_without_digest.pop("bundle_digest", None)
        if _jcs_digest(manifest_without_digest) != pin.bundle_digest:
            raise HybridPlanningError(f"canonical platform bundle digest does not match its pin for {version}")

        members = manifest.get("members")
        if not isinstance(members, list):
            raise HybridPlanningError(f"canonical platform bundle members are malformed for {version}")
        member_digests: dict[str, str] = {}
        for member in members:
            if not isinstance(member, dict) or set(member) != {"path", "digest"}:
                raise HybridPlanningError(f"canonical platform bundle member is malformed for {version}")
            member_path = member.get("path")
            member_digest = member.get("digest")
            if (
                not isinstance(member_path, str)
                or member_path in member_digests
                or not isinstance(member_digest, str)
                or not _DIGEST.fullmatch(member_digest)
            ):
                raise HybridPlanningError(f"canonical platform bundle member is invalid for {version}")
            member_digests[member_path] = member_digest
        for filename, payload in payloads.items():
            if filename == "bundle-manifest.json":
                continue
            member_path = f"contracts/platform/{pin.directory}/{filename}"
            if member_digests.get(member_path) != _sha256_bytes(payload):
                raise HybridPlanningError(f"canonical platform asset digest does not match its manifest for {version}: {filename}")

        registry = Registry()
        try:
            for schema in schemas.values():
                registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
        except (TypeError, ValueError) as error:
            raise HybridPlanningError(f"canonical platform schemas are malformed for {version}") from error
        for filename, schema_filename, document in (
            ("bundle-manifest.json", "contract-bundle.schema.json", manifest),
            ("compatibility.json", "compatibility.schema.json", compatibility),
        ):
            validator = Draft202012Validator(
                schemas[schema_filename],
                registry=registry,
                format_checker=FormatChecker(),
            )
            if next(validator.iter_errors(document), None) is not None:
                raise HybridPlanningError(f"canonical platform {filename} failed schema validation for {version}")

        advertised_contracts = compatibility.get("contracts")
        expected_contracts = {
            (schema_id, version, _SCHEMA_FILENAMES[schema_id])
            for schema_id, supported_versions in _SUPPORTED_SCHEMA_VERSIONS.items()
            if version in supported_versions
        }
        if (
            not isinstance(advertised_contracts, list)
            or {
                (item.get("schema_id"), item.get("version"), item.get("schema_path"))
                for item in advertised_contracts
                if isinstance(item, dict)
            }
            != expected_contracts
        ):
            raise HybridPlanningError(f"canonical platform compatibility set is invalid for {version}")
        self._registry = registry
        self._schemas = schemas
        self.status = _VerifiedPlatformBundle(
            schema_version=version,
            bundle_id=pin.bundle_id,
            bundle_digest=pin.bundle_digest,
        )

    def validate(self, schema_id: str, document: JsonObject) -> None:
        filename = _SCHEMA_FILENAMES[schema_id]
        validator = Draft202012Validator(
            self._schemas[filename],
            registry=self._registry,
            format_checker=FormatChecker(),
        )
        if next(validator.iter_errors(document), None) is not None:
            raise HybridPlanningError(f"{schema_id}@{document.get('schema_version')} failed canonical schema validation")


@lru_cache(maxsize=4)
def _canonical_platform_schemas(version: str, root: Path) -> _CanonicalPlatformSchemas:
    return _CanonicalPlatformSchemas(version, root)


@dataclass(frozen=True, init=False)
class AcceptedContractRecord:
    """An exact accepted contract document with its byte digest retained."""

    schema_id: str
    schema_version: str
    record_id: str
    digest: str
    _payload: bytes = field(repr=False)

    @classmethod
    def from_bytes(cls, payload: bytes) -> AcceptedContractRecord:
        immutable = bytes(payload)
        document = _parse_document(immutable)
        schema_id = document.get("schema_id")
        if schema_id not in _SCHEMA_RECORD_IDS:
            raise HybridPlanningError(f"unsupported platform schema_id: {schema_id!r}")
        schema_version = document.get("schema_version")
        if not isinstance(schema_version, str) or schema_version not in _SUPPORTED_SCHEMA_VERSIONS[schema_id]:
            raise HybridPlanningError(f"unsupported platform contract pair: {schema_id}@{schema_version}")
        _canonical_platform_schemas(schema_version, _platform_contract_root()).validate(schema_id, document)
        record_id = document.get(_SCHEMA_RECORD_IDS[schema_id])
        if not isinstance(record_id, str) or not record_id:
            raise HybridPlanningError(f"{schema_id} lacks its stable record id")
        instance = cls.__new__(cls)
        object.__setattr__(instance, "schema_id", schema_id)
        object.__setattr__(instance, "schema_version", schema_version)
        object.__setattr__(instance, "record_id", record_id)
        object.__setattr__(instance, "digest", f"sha256:{hashlib.sha256(immutable).hexdigest()}")
        object.__setattr__(instance, "_payload", immutable)
        return instance

    @classmethod
    def from_document(cls, document: JsonObject) -> AcceptedContractRecord:
        return cls.from_bytes(_canonical_json(document))

    def document(self) -> JsonObject:
        try:
            payload = bytes(self._payload)
            schema_id = self.schema_id
            schema_version = self.schema_version
            record_id = self.record_id
            digest = self.digest
            identity_key = _SCHEMA_RECORD_IDS[schema_id]
        except (AttributeError, KeyError, TypeError) as error:
            raise HybridPlanningError("accepted contract record construction is invalid") from error
        if digest != f"sha256:{hashlib.sha256(payload).hexdigest()}":
            raise HybridPlanningError("accepted contract record bytes no longer match their digest")
        document = _parse_document(payload)
        if (
            document.get("schema_id") != schema_id
            or document.get("schema_version") != schema_version
            or document.get(identity_key) != record_id
        ):
            raise HybridPlanningError("accepted contract record identity changed")
        return document

    def to_bytes(self) -> bytes:
        """Return the exact bytes retained by this accepted record."""

        self.document()
        return bytes(self._payload)


@dataclass(frozen=True)
class DesktopTransferReceipt:
    """Desktop-local consent evidence; this is not a public protocol record."""

    route_plan_digest: str
    source_bundle_content_digest: str
    attached_bundle_content_digest: str
    project_contents_digest: str
    transfer_manifest_digest: str
    transfer_id: str
    approval_id: str
    operation_id: str
    actor_type: str
    actor_id: str
    tenant_id: str
    project_id: str
    project_revision_digest: str

    def to_bytes(self) -> bytes:
        return _canonical_json(
            {
                "receipt_version": _RECEIPT_VERSION,
                "route_plan_digest": self.route_plan_digest,
                "source_bundle_content_digest": self.source_bundle_content_digest,
                "attached_bundle_content_digest": self.attached_bundle_content_digest,
                "project_contents_digest": self.project_contents_digest,
                "transfer_manifest_digest": self.transfer_manifest_digest,
                "transfer_id": self.transfer_id,
                "approval_id": self.approval_id,
                "operation_id": self.operation_id,
                "actor": {"type": self.actor_type, "id": self.actor_id},
                "tenant_id": self.tenant_id,
                "project_id": self.project_id,
                "project_revision_digest": self.project_revision_digest,
            }
        )

    @classmethod
    def from_bytes(cls, payload: bytes) -> DesktopTransferReceipt:
        value = _parse_document(payload)
        expected_keys = {
            "receipt_version",
            "route_plan_digest",
            "source_bundle_content_digest",
            "attached_bundle_content_digest",
            "project_contents_digest",
            "transfer_manifest_digest",
            "transfer_id",
            "approval_id",
            "operation_id",
            "actor",
            "tenant_id",
            "project_id",
            "project_revision_digest",
        }
        if set(value) != expected_keys or value.get("receipt_version") != _RECEIPT_VERSION:
            raise ProjectTransferConsentError("Desktop transfer receipt has an unsupported shape")
        actor = _actor(value.get("actor"), "receipt actor")
        digest_keys = (
            "route_plan_digest",
            "source_bundle_content_digest",
            "attached_bundle_content_digest",
            "project_contents_digest",
            "transfer_manifest_digest",
            "project_revision_digest",
        )
        if any(not _DIGEST.fullmatch(str(value.get(key, ""))) for key in digest_keys):
            raise ProjectTransferConsentError("Desktop transfer receipt contains an invalid digest")
        for key in ("transfer_id", "approval_id", "operation_id", "tenant_id", "project_id"):
            if not isinstance(value.get(key), str) or not _STABLE_ID.fullmatch(value[key]):
                raise ProjectTransferConsentError("Desktop transfer receipt contains an invalid identity")
        if not _TRANSFER_ID.fullmatch(value["transfer_id"]) or not _APPROVAL_ID.fullmatch(value["approval_id"]):
            raise ProjectTransferConsentError("Desktop transfer receipt contains an invalid approval identity")
        _reject_security_material(value)
        return cls(
            route_plan_digest=value["route_plan_digest"],
            source_bundle_content_digest=value["source_bundle_content_digest"],
            attached_bundle_content_digest=value["attached_bundle_content_digest"],
            project_contents_digest=value["project_contents_digest"],
            transfer_manifest_digest=value["transfer_manifest_digest"],
            transfer_id=value["transfer_id"],
            approval_id=value["approval_id"],
            operation_id=value["operation_id"],
            actor_type=actor["type"],
            actor_id=actor["id"],
            tenant_id=value["tenant_id"],
            project_id=value["project_id"],
            project_revision_digest=value["project_revision_digest"],
        )


class DesktopTransferReceiptStore:
    """Immutable SQLite receipts persisted only through a bound descriptor."""

    def __init__(self, path: Path) -> None:
        if not isinstance(path, Path) or not path.is_absolute() or path.name in {"", ".", ".."}:
            raise ValueError("Desktop receipt store requires an explicit file path")
        try:
            parent = path.parent.resolve(strict=True)
            parent_metadata = path.parent.lstat()
        except OSError as error:
            raise ProjectTransferConsentError("Desktop receipt store parent is unavailable") from error
        if (
            parent != path.parent.absolute()
            or not stat.S_ISDIR(parent_metadata.st_mode)
            or parent_metadata.st_uid != os.getuid()
            or stat.S_IMODE(parent_metadata.st_mode) & 0o077
        ):
            raise ProjectTransferConsentError("Desktop receipt store parent must be canonical and private")
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        directory_flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        try:
            self._directory_fd = os.open(parent, directory_flags)
        except OSError as error:
            raise ProjectTransferConsentError("Desktop receipt store parent could not be bound") from error
        self._directory_identity = os.fstat(self._directory_fd)
        self._path = path
        self._lock = threading.RLock()
        self._closed = False
        self._initialized = False
        try:
            self._database_fd = self._open_database_file()
            self._database_identity = os.fstat(self._database_fd)
            self._assert_storage_binding()
            self._database = self._open_sqlite_connection()
            self._assert_storage_binding()
            self._initialize()
        except BaseException:
            database = getattr(self, "_database", None)
            if database is not None:
                database.close()
            database_fd = getattr(self, "_database_fd", None)
            if database_fd is not None:
                os.close(database_fd)
            os.close(self._directory_fd)
            self._closed = True
            raise

    @property
    def path(self) -> Path:
        return self._path

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._database.close()
            os.close(self._database_fd)
            os.close(self._directory_fd)
            self._closed = True

    def __del__(self) -> None:
        with suppress(AttributeError, OSError, sqlite3.Error):
            self.close()

    def _put_approved(self, receipt: DesktopTransferReceipt, *, manifest: JsonObject) -> None:
        if _jcs_digest(manifest) != receipt.transfer_manifest_digest:
            raise ProjectTransferConsentError("Desktop transfer receipt requires exact approved-transfer evidence")
        payload = receipt.to_bytes()
        if DesktopTransferReceipt.from_bytes(payload) != receipt:
            raise ProjectTransferConsentError("Desktop transfer receipt does not round-trip exactly")
        payload_digest = f"sha256:{hashlib.sha256(payload).hexdigest()}"
        with self._operation(write=True) as database:
            existing = database.execute(
                "SELECT payload, payload_digest FROM transfer_receipts "
                "WHERE attached_bundle_content_digest = ? AND transfer_id = ? AND route_plan_digest = ?",
                (
                    receipt.attached_bundle_content_digest,
                    receipt.transfer_id,
                    receipt.route_plan_digest,
                ),
            ).fetchone()
            if existing is not None:
                if bytes(existing[0]) != payload or existing[1] != payload_digest:
                    raise ProjectTransferConsentError("Desktop transfer receipt identity is immutable")
                return
            database.execute(
                "INSERT INTO transfer_receipts "
                "(attached_bundle_content_digest, transfer_id, route_plan_digest, payload, payload_digest) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    receipt.attached_bundle_content_digest,
                    receipt.transfer_id,
                    receipt.route_plan_digest,
                    payload,
                    payload_digest,
                ),
            )

    def receipts_for_bundle(self, attached_bundle_content_digest: str) -> tuple[DesktopTransferReceipt, ...]:
        with self._operation() as database:
            rows = database.execute(
                "SELECT transfer_id, route_plan_digest, payload, payload_digest FROM transfer_receipts "
                "WHERE attached_bundle_content_digest = ? ORDER BY transfer_id",
                (attached_bundle_content_digest,),
            ).fetchall()
        receipts: list[DesktopTransferReceipt] = []
        for stored_transfer_id, stored_route_digest, payload_value, payload_digest in rows:
            payload = bytes(payload_value)
            if payload_digest != f"sha256:{hashlib.sha256(payload).hexdigest()}":
                raise ProjectTransferConsentError("Desktop transfer receipt integrity check failed")
            receipt = DesktopTransferReceipt.from_bytes(payload)
            if receipt.attached_bundle_content_digest != attached_bundle_content_digest:
                raise ProjectTransferConsentError("Desktop transfer receipt index binding failed")
            if receipt.transfer_id != stored_transfer_id or receipt.route_plan_digest != stored_route_digest:
                raise ProjectTransferConsentError("Desktop transfer receipt route index binding failed")
            receipts.append(receipt)
        return tuple(receipts)

    def _open_database_file(self) -> int:
        flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            try:
                descriptor = os.open(
                    self._path.name,
                    flags | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=self._directory_fd,
                )
            except FileExistsError:
                descriptor = os.open(self._path.name, flags, dir_fd=self._directory_fd)
        except OSError as error:
            raise ProjectTransferConsentError("Desktop receipt database path is unsafe") from error
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
        ):
            os.close(descriptor)
            raise ProjectTransferConsentError("Desktop receipt database must be one private owned file")
        return descriptor

    def _open_sqlite_connection(self) -> sqlite3.Connection:
        database = sqlite3.connect(":memory:", timeout=5.0, check_same_thread=False)
        database.execute("PRAGMA trusted_schema = OFF")
        database.execute("PRAGMA temp_store = MEMORY")
        return database

    def _load_descriptor_snapshot(self) -> None:
        metadata = os.fstat(self._database_fd)
        if metadata.st_size == 0:
            if self._initialized:
                raise ProjectTransferConsentError("Desktop receipt database was truncated")
            return
        if metadata.st_size > MAX_RECEIPT_DATABASE_BYTES:
            raise ProjectTransferConsentError("Desktop receipt database exceeds the local size limit")
        chunks: list[bytes] = []
        offset = 0
        while offset < metadata.st_size:
            chunk = os.pread(self._database_fd, min(1_048_576, metadata.st_size - offset), offset)
            if not chunk:
                raise ProjectTransferConsentError("Desktop receipt database descriptor read was incomplete")
            chunks.append(chunk)
            offset += len(chunk)
        try:
            self._database.deserialize(b"".join(chunks))
            integrity = self._database.execute("PRAGMA quick_check").fetchone()
        except sqlite3.Error as error:
            raise ProjectTransferConsentError("Desktop receipt database is not valid SQLite state") from error
        if integrity != ("ok",):
            raise ProjectTransferConsentError("Desktop receipt database integrity check failed")

    def _persist_descriptor_snapshot(self) -> None:
        try:
            payload = self._database.serialize()
        except sqlite3.Error as error:
            raise ProjectTransferConsentError("Desktop receipt database could not be serialized") from error
        if not payload or len(payload) > MAX_RECEIPT_DATABASE_BYTES:
            raise ProjectTransferConsentError("Desktop receipt database exceeds the local size limit")
        offset = 0
        while offset < len(payload):
            written = os.pwrite(self._database_fd, payload[offset:], offset)
            if written <= 0:
                raise ProjectTransferConsentError("Desktop receipt database descriptor write was incomplete")
            offset += written
        os.ftruncate(self._database_fd, len(payload))
        os.fsync(self._database_fd)

    def _assert_storage_binding(self) -> None:
        if self._closed:
            raise ProjectTransferConsentError("Desktop receipt store is closed")
        try:
            directory_path = self._path.parent.lstat()
            directory_fd = os.fstat(self._directory_fd)
            database_path = os.stat(self._path.name, dir_fd=self._directory_fd, follow_symlinks=False)
            database_fd = os.fstat(self._database_fd)
        except OSError as error:
            raise ProjectTransferConsentError("Desktop receipt store binding is unavailable") from error
        if (
            not stat.S_ISDIR(directory_path.st_mode)
            or directory_path.st_dev != self._directory_identity.st_dev
            or directory_path.st_ino != self._directory_identity.st_ino
            or directory_fd.st_dev != self._directory_identity.st_dev
            or directory_fd.st_ino != self._directory_identity.st_ino
            or directory_path.st_uid != os.getuid()
            or stat.S_IMODE(directory_path.st_mode) & 0o077
        ):
            raise ProjectTransferConsentError("Desktop receipt store parent identity changed")
        if (
            not stat.S_ISREG(database_path.st_mode)
            or database_path.st_dev != self._database_identity.st_dev
            or database_path.st_ino != self._database_identity.st_ino
            or database_fd.st_dev != self._database_identity.st_dev
            or database_fd.st_ino != self._database_identity.st_ino
            or database_path.st_uid != os.getuid()
            or database_fd.st_uid != os.getuid()
            or stat.S_IMODE(database_path.st_mode) != 0o600
            or stat.S_IMODE(database_fd.st_mode) != 0o600
            or database_path.st_nlink != 1
            or database_fd.st_nlink != 1
            or database_fd.st_size > MAX_RECEIPT_DATABASE_BYTES
        ):
            raise ProjectTransferConsentError("Desktop receipt database identity changed")
        for suffix in _SQLITE_SIDECAR_SUFFIXES:
            try:
                os.stat(self._path.name + suffix, dir_fd=self._directory_fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            raise ProjectTransferConsentError("Desktop receipt SQLite sidecar path is not allowed")

    @contextmanager
    def _operation(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        with self._lock:
            locked = False
            try:
                fcntl.flock(self._database_fd, fcntl.LOCK_EX if write else fcntl.LOCK_SH)
                locked = True
                self._assert_storage_binding()
                self._load_descriptor_snapshot()
                if write:
                    self._database.execute("BEGIN IMMEDIATE")
                yield self._database
                if write:
                    self._database.commit()
                    self._persist_descriptor_snapshot()
            except BaseException:
                if write and self._database.in_transaction:
                    self._database.rollback()
                raise
            finally:
                if locked:
                    try:
                        self._assert_storage_binding()
                    finally:
                        fcntl.flock(self._database_fd, fcntl.LOCK_UN)

    def _initialize(self) -> None:
        with self._operation(write=True) as database:
            database.execute(
                "CREATE TABLE IF NOT EXISTS transfer_receipts ("
                "attached_bundle_content_digest TEXT NOT NULL, "
                "transfer_id TEXT NOT NULL, "
                "route_plan_digest TEXT NOT NULL, "
                "payload BLOB NOT NULL, "
                "payload_digest TEXT NOT NULL, "
                "PRIMARY KEY (attached_bundle_content_digest, transfer_id, route_plan_digest))"
            )
            columns = tuple((row[1], row[2], row[3], row[4], row[5]) for row in database.execute("PRAGMA table_info(transfer_receipts)"))
            if columns != (
                ("attached_bundle_content_digest", "TEXT", 1, None, 1),
                ("transfer_id", "TEXT", 1, None, 2),
                ("route_plan_digest", "TEXT", 1, None, 3),
                ("payload", "BLOB", 1, None, 0),
                ("payload_digest", "TEXT", 1, None, 0),
            ):
                raise ProjectTransferConsentError("Desktop transfer receipt store schema is incompatible")
        self._initialized = True


@dataclass(frozen=True)
class DesktopCapabilityRequirements:
    """Capabilities a Desktop operation requires from its chosen harness."""

    harness_features: tuple[str, ...] = ()
    require_resume: bool = False
    require_reconnect: bool = False

    def __post_init__(self) -> None:
        if len(set(self.harness_features)) != len(self.harness_features):
            raise ValueError("harness_features must be unique")
        unknown = set(self.harness_features).difference(_FEATURE_NAMES)
        if unknown:
            raise ValueError("unknown harness features: " + ", ".join(sorted(unknown)))


@dataclass(frozen=True)
class _PricingDisposition:
    state: str
    release_phase: str | None
    release_reason: str | None
    dispatch_permitted: bool


@dataclass(frozen=True, init=False)
class DesktopHybridRoute:
    """Credential-free route issued only by the exact Desktop planner."""

    route_plan_id: str
    route_plan_digest: str
    route_plan_version: str
    pricing_lifecycle_id: str
    pricing_lifecycle_digest: str
    pricing_contract_version: str
    pricing_state: str
    pricing_release_phase: str | None
    pricing_release_reason: str | None
    dispatch_permitted: bool
    operation_id: str
    actor_type: str
    actor_id: str
    tenant_id: str
    project_bundle_id: str
    project_id: str
    project_revision_digest: str
    project_location: str
    harness_id: str
    harness_name: str
    harness_location: str
    inference_route_id: str
    inference_name: str
    inference_provider: str
    inference_model: str
    inference_location: str
    compute_target_id: str
    compute_name: str
    compute_location: str
    executor_adapter_id: str
    executor_adapter_version: str
    executor_target_kind: str
    image_repository: str
    image_digest: str
    capabilities_digest: str
    attestation_digest: str
    transfer_manifest_id: str | None
    connector_grant_id: str | None
    transfer_inputs: tuple[tuple[str, str, int, str], ...]
    _authorization_id: str = field(repr=False, compare=False)
    _claims_digest: str = field(repr=False, compare=False)

    @classmethod
    def _from_planner(cls, *, authorization_id: str, **values: Any) -> DesktopHybridRoute:
        if not _ROUTE_AUTHORIZATION_ID.fullmatch(authorization_id):
            raise HybridPlanningError("Desktop planner route authorization id is invalid")
        public_fields = tuple(item.name for item in fields(cls) if not item.name.startswith("_"))
        if set(values) != set(public_fields):
            raise HybridPlanningError("Desktop planner route projection is incomplete")
        instance = cls.__new__(cls)
        for name in public_fields:
            object.__setattr__(instance, name, values[name])
        object.__setattr__(instance, "_authorization_id", authorization_id)
        object.__setattr__(instance, "_claims_digest", _jcs_digest(instance._authenticity_claims()))
        return instance

    def _authenticity_claims(self) -> JsonObject:
        return {item.name: getattr(self, item.name) for item in fields(self) if not item.name.startswith("_")}

    def _assert_claims_intact(self) -> None:
        if not _ROUTE_AUTHORIZATION_ID.fullmatch(str(getattr(self, "_authorization_id", ""))) or getattr(
            self, "_claims_digest", None
        ) != _jcs_digest(self._authenticity_claims()):
            raise HybridPlanningError("Desktop route is not an exact planner-issued result")

    @property
    def combination(self) -> str:
        letters = (
            "l" if self.harness_location == "local" else "h",
            "l" if self.inference_location == "local" else "h",
            "l" if self.compute_location == "local" else "h",
        )
        return "".join(letters)

    @property
    def transfer_required(self) -> bool:
        return (self.dispatch_permitted or self.pricing_state == "pending") and "hosted" in {
            self.harness_location,
            self.inference_location,
            self.compute_location,
        }

    @property
    def connector_required(self) -> bool:
        return (
            (self.dispatch_permitted or self.pricing_state == "pending")
            and self.harness_location == "local"
            and self.compute_location == "hosted"
        )

    def display(self) -> JsonObject:
        """Return only fields suitable for the Desktop route chooser."""

        return {
            "route_plan_id": self.route_plan_id,
            "combination": self.combination,
            "project_location": self.project_location,
            "contracts": {
                "route_plan": self.route_plan_version,
                "pricing": self.pricing_contract_version,
                "executor": PLATFORM_ALPHA1_VERSION,
            },
            "pricing": {
                "pricing_lifecycle_id": self.pricing_lifecycle_id,
                "state": self.pricing_state,
                "release_phase": self.pricing_release_phase,
                "release_reason": self.pricing_release_reason,
                "dispatch_permitted": self.dispatch_permitted,
            },
            "harness": {
                "id": self.harness_id,
                "name": self.harness_name,
                "location": self.harness_location,
            },
            "inference": {
                "id": self.inference_route_id,
                "name": self.inference_name,
                "provider": self.inference_provider,
                "model": self.inference_model,
                "location": self.inference_location,
            },
            "compute": {
                "id": self.compute_target_id,
                "name": self.compute_name,
                "location": self.compute_location,
            },
            "transfer_required": self.transfer_required,
            "connector_required": self.connector_required,
        }


class DesktopHybridPlanner:
    """Resolve exact paired route/pricing records across three route axes."""

    def __init__(
        self,
        *,
        harnesses: tuple[AcceptedContractRecord, ...],
        inference_routes: tuple[AcceptedContractRecord, ...],
        compute_targets: tuple[AcceptedContractRecord, ...],
        transfer_receipts: DesktopTransferReceiptStore,
    ) -> None:
        self._harnesses = _index_records(harnesses, "reasoning.platform.harness-provider", "harness_id")
        self._routes = _index_records(inference_routes, "reasoning.platform.inference-route", "route_id")
        self._targets = _index_records(compute_targets, "reasoning.platform.compute-target", "target_id")
        self._transfer_receipts = transfer_receipts
        self._authorization_lock = threading.Lock()
        self._route_authorizations: dict[
            str,
            tuple[
                AcceptedContractRecord,
                AcceptedContractRecord,
                AcceptedContractRecord,
                DesktopCapabilityRequirements,
                bool,
            ],
        ] = {}
        for record in (*harnesses, *inference_routes, *compute_targets):
            _reject_security_material(record.document())

    @property
    def transfer_receipts(self) -> DesktopTransferReceiptStore:
        return self._transfer_receipts

    def resolve_plan(
        self,
        plan: AcceptedContractRecord,
        pricing: AcceptedContractRecord,
        bundle: AcceptedContractRecord,
        *,
        requirements: DesktopCapabilityRequirements | None = None,
    ) -> DesktopHybridRoute:
        return self._resolve(
            plan,
            pricing,
            bundle,
            requirements=requirements or DesktopCapabilityRequirements(),
            allow_missing_transfer=False,
        )

    def inspect_plan(
        self,
        plan: AcceptedContractRecord,
        pricing: AcceptedContractRecord,
        bundle: AcceptedContractRecord,
        *,
        requirements: DesktopCapabilityRequirements | None = None,
    ) -> DesktopHybridRoute:
        """Validate and display a route without retaining authorization."""

        return self._resolve(
            plan,
            pricing,
            bundle,
            requirements=requirements or DesktopCapabilityRequirements(),
            allow_missing_transfer=True,
            store_authorization=False,
            require_transfer_receipt=False,
        )

    def inspect_pending_plan(
        self,
        plan: AcceptedContractRecord,
        bundle: AcceptedContractRecord,
        *,
        requirements: DesktopCapabilityRequirements | None = None,
    ) -> DesktopHybridRoute:
        """Validate a queued route without inventing terminal pricing evidence.

        The returned route is display-only and cannot authorize dispatch.
        Exact provider pricing remains mandatory once the run is terminal.
        """
        return self._resolve(
            plan,
            None,
            bundle,
            requirements=requirements or DesktopCapabilityRequirements(),
            allow_missing_transfer=True,
            store_authorization=False,
            require_transfer_receipt=False,
        )

    def resolve_matrix(
        self,
        pairs: tuple[tuple[AcceptedContractRecord, AcceptedContractRecord], ...],
        bundle: AcceptedContractRecord,
    ) -> tuple[DesktopHybridRoute, ...]:
        requirements = DesktopCapabilityRequirements()
        preview = tuple(
            self._resolve(
                plan,
                pricing,
                bundle,
                requirements=requirements,
                allow_missing_transfer=False,
                store_authorization=False,
            )
            for plan, pricing in pairs
        )
        combinations = [route.combination for route in preview]
        expected = {"".join(values) for values in itertools.product("lh", repeat=3)}
        if len(preview) != 8 or set(combinations) != expected:
            raise UnsupportedHybridRoute("the Desktop matrix requires exactly one accepted plan for each of lll through hhh")
        if len(set(combinations)) != len(combinations):
            raise UnsupportedHybridRoute("the Desktop matrix contains a duplicate combination")

        issued: list[
            tuple[
                DesktopHybridRoute,
                str,
                AcceptedContractRecord,
                AcceptedContractRecord,
            ]
        ] = []
        for plan, pricing in pairs:
            authorization_id = f"desktop-route-{uuid.uuid4().hex}"
            route = self._resolve(
                plan,
                pricing,
                bundle,
                requirements=requirements,
                allow_missing_transfer=False,
                authorization_id=authorization_id,
                store_authorization=False,
            )
            issued.append((route, authorization_id, plan, pricing))

        with self._authorization_lock:
            authorization_ids = {authorization_id for _, authorization_id, _, _ in issued}
            if len(authorization_ids) != len(issued) or authorization_ids.intersection(self._route_authorizations):
                raise HybridPlanningError("Desktop matrix authorization identity collision")
            self._route_authorizations.update(
                {
                    authorization_id: (plan, pricing, bundle, requirements, False)
                    for route, authorization_id, plan, pricing in issued
                    if route.dispatch_permitted
                }
            )
        return tuple(sorted((route for route, _, _, _ in issued), key=lambda route: route.combination))

    def prepare_transfer_route(
        self,
        plan: AcceptedContractRecord,
        pricing: AcceptedContractRecord,
        bundle: AcceptedContractRecord,
    ) -> DesktopHybridRoute:
        """Resolve a hosted route before consent without inventing a transfer."""

        route = self._resolve(
            plan,
            pricing,
            bundle,
            requirements=DesktopCapabilityRequirements(),
            allow_missing_transfer=True,
            store_authorization=False,
        )
        if not route.dispatch_permitted:
            raise ProjectTransferConsentError("released pre-dispatch pricing cannot authorize a project transfer")
        return route

    def reauthorize_connector_route(self, route: DesktopHybridRoute) -> DesktopHybridRoute:
        """Re-resolve the exact records behind a connector-bound route."""

        try:
            route._assert_claims_intact()
            authorization_id = route._authorization_id
        except (AttributeError, HybridPlanningError) as error:
            raise HybridPlanningError("Desktop route is not an exact planner-issued result") from error
        with self._authorization_lock:
            authorization = self._route_authorizations.get(authorization_id)
        if authorization is None:
            raise HybridPlanningError("Desktop route authorization is unknown to this planner")
        plan, pricing, bundle, requirements, allow_missing_transfer = authorization
        if allow_missing_transfer:
            raise HybridPlanningError("connector routes require completed project transfer consent")
        expected = self._resolve(
            plan,
            pricing,
            bundle,
            requirements=requirements,
            allow_missing_transfer=False,
            authorization_id=authorization_id,
        )
        if expected._authenticity_claims() != route._authenticity_claims():
            raise HybridPlanningError("Desktop route no longer matches exact planner resolution")
        return expected

    def _resolve(
        self,
        plan_record: AcceptedContractRecord,
        pricing_record: AcceptedContractRecord | None,
        bundle_record: AcceptedContractRecord,
        *,
        requirements: DesktopCapabilityRequirements,
        allow_missing_transfer: bool,
        authorization_id: str | None = None,
        store_authorization: bool = True,
        require_transfer_receipt: bool = True,
    ) -> DesktopHybridRoute:
        _expect_schema(plan_record, "reasoning.platform.route-plan")
        if pricing_record is not None:
            _expect_schema(pricing_record, "reasoning.platform.pricing")
        _expect_schema(bundle_record, "reasoning.platform.project-bundle")
        plan = plan_record.document()
        pricing = pricing_record.document() if pricing_record is not None else None
        bundle = bundle_record.document()
        _reject_security_material(plan)
        if pricing is not None:
            _reject_security_material(pricing)
        _reject_security_material(bundle)
        if pricing_record is None:
            if (
                plan_record.schema_version != PLATFORM_ALPHA2_VERSION
                or plan.get("pricing_contract_version") != PLATFORM_ALPHA2_VERSION
            ):
                raise UnsupportedHybridRoute("pending route requires an exact alpha.2 pricing pair")
            pricing_disposition = _PricingDisposition("pending", None, None, False)
        else:
            pricing_disposition = _validate_pricing_pair(plan_record, pricing_record)

        harness_ref = _mapping(plan, "harness")
        inference_ref = _mapping(plan, "inference")
        compute_ref = _mapping(plan, "compute")
        persistence = _mapping(plan, "persistence")
        actor = _actor(plan.get("actor"), "route plan actor")
        harness = self._lookup(self._harnesses, harness_ref.get("harness_id"), "harness")
        inference = self._lookup(self._routes, inference_ref.get("route_id"), "inference route")
        target = self._lookup(self._targets, compute_ref.get("target_id"), "compute target")

        comparisons = (
            (harness_ref.get("descriptor_digest"), harness.digest, "harness descriptor digest"),
            (inference_ref.get("route_digest"), inference.digest, "inference route digest"),
            (compute_ref.get("target_digest"), target.digest, "compute target digest"),
        )
        for observed, expected, label in comparisons:
            if observed != expected:
                raise UnsupportedHybridRoute(f"{label} does not match the accepted record")

        harness_doc = harness.document()
        inference_doc = inference.document()
        target_doc = target.document()
        if harness_ref.get("location") != harness_doc.get("location"):
            raise UnsupportedHybridRoute("harness location substitution")
        if inference_ref.get("location") != inference_doc.get("location"):
            raise UnsupportedHybridRoute("inference location substitution")
        if compute_ref.get("location") != target_doc.get("location"):
            raise UnsupportedHybridRoute("compute location substitution")
        harness_id = _string(harness_doc.get("harness_id"), "harness_id")
        route_id = _string(inference_doc.get("route_id"), "route_id")
        target_id = _string(target_doc.get("target_id"), "target_id")
        if route_id not in _string_list(
            harness_doc.get("supported_inference_route_ids"),
            "supported_inference_route_ids",
        ):
            raise UnsupportedHybridRoute("harness does not advertise the inference route")
        if target_id not in _string_list(
            harness_doc.get("supported_compute_target_ids"),
            "supported_compute_target_ids",
        ):
            raise UnsupportedHybridRoute("harness does not advertise the compute target")
        if harness_id not in _string_list(inference_doc.get("harness_ids"), "harness_ids"):
            raise UnsupportedHybridRoute("inference route does not advertise the harness")
        if harness_id not in _string_list(target_doc.get("supported_harness_ids"), "supported_harness_ids"):
            raise UnsupportedHybridRoute("compute target does not advertise the harness")

        if inference_ref.get("provider") != inference_doc.get("provider"):
            raise UnsupportedHybridRoute("inference provider substitution")
        model = _string(inference_ref.get("model"), "inference model")
        patterns = _string_list(inference_doc.get("model_patterns"), "model_patterns")
        if not any(fnmatch.fnmatchcase(model, pattern) for pattern in patterns):
            raise UnsupportedHybridRoute("model is outside the accepted inference patterns")
        effort = _string(inference_ref.get("reasoning_effort"), "reasoning_effort")
        if effort not in _string_list(inference_doc.get("reasoning_efforts"), "inference reasoning_efforts"):
            raise UnsupportedHybridRoute("inference route does not support the reasoning effort")
        features = _mapping(harness_doc, "features")
        harness_efforts = _string_list(features.get("reasoning_efforts"), "harness reasoning_efforts")
        if effort not in harness_efforts:
            raise UnsupportedHybridRoute("harness does not support the reasoning effort")
        for feature in requirements.harness_features:
            if features.get(feature) is not True:
                raise UnsupportedHybridRoute(f"harness does not provide required feature: {feature}")
        session = _mapping(harness_doc, "session")
        modes = _string_list(session.get("modes"), "session modes")
        if requirements.require_resume and "resume" not in modes:
            raise UnsupportedHybridRoute("harness does not support resume")
        if requirements.require_reconnect:
            reconnect = _mapping(session, "reconnect")
            expected_reconnect = {
                "supported": True,
                "cursor": "opaque",
                "after": "exclusive",
                "delivery": "at_least_once",
                "deduplicate_by": "event_id",
            }
            if reconnect != expected_reconnect:
                raise UnsupportedHybridRoute("harness reconnect semantics are not supported")

        if compute_ref.get("executor_contract_version") != PLATFORM_ALPHA1_VERSION:
            raise UnsupportedHybridRoute("compute plan has an unsupported Executor contract")
        executor = _mapping(target_doc, "executor")
        if executor.get("contract_version") != PLATFORM_ALPHA1_VERSION:
            raise UnsupportedHybridRoute("compute target has an unsupported Executor contract")
        image = _mapping(compute_ref, "image")
        for digest_key in ("digest", "capabilities_digest", "attestation_digest"):
            if not _DIGEST.fullmatch(str(image.get(digest_key, ""))):
                raise UnsupportedHybridRoute(f"compute image has an invalid {digest_key}")

        project = _mapping(bundle, "project")
        export = _mapping(bundle, "export")
        if persistence.get("project_bundle_id") != bundle.get("bundle_id"):
            raise UnsupportedHybridRoute("project bundle substitution")
        if persistence.get("project_id") != project.get("project_id"):
            raise UnsupportedHybridRoute("project id substitution")
        if persistence.get("project_revision_digest") != project.get("revision_digest"):
            raise UnsupportedHybridRoute("project revision substitution")
        if export.get("created_by") != actor:
            raise UnsupportedHybridRoute("route actor does not own the project export")
        if project.get("origin") == "local" and persistence.get("location") != "local":
            raise UnsupportedHybridRoute("local projects remain locally persisted")
        if export.get("security_material") != "excluded" or export.get("server_policy") != "excluded":
            raise UnsupportedHybridRoute("project export must exclude security material and server policy")

        locations = {
            harness_ref.get("location"),
            inference_ref.get("location"),
            compute_ref.get("location"),
        }
        pending = pricing_record is None
        hosted = (pricing_disposition.dispatch_permitted or pending) and "hosted" in locations
        transfer_id = persistence.get("transfer_manifest_id")
        transfer_inputs: tuple[tuple[str, str, int, str], ...] = ()
        if hosted:
            if not isinstance(transfer_id, str):
                raise ProjectTransferConsentError("hosted participation requires per-project transfer consent")
            transfer = next(
                (
                    value
                    for value in bundle.get("transfer_manifests", [])
                    if isinstance(value, dict) and value.get("transfer_id") == transfer_id
                ),
                None,
            )
            if transfer is None:
                if not allow_missing_transfer:
                    raise ProjectTransferConsentError("the exact approved transfer manifest is unavailable")
            else:
                transfer_inputs = _validate_transfer_manifest(transfer, plan, project)
                if require_transfer_receipt:
                    self._validate_persisted_receipt(
                        plan_record=plan_record,
                        bundle=bundle,
                        transfer=transfer,
                    )
        elif transfer_id is not None:
            raise UnsupportedHybridRoute("an all-local plan cannot consume a transfer manifest")

        connector_id = compute_ref.get("connector_grant_id")
        connector_required = (
            (pricing_disposition.dispatch_permitted or pending)
            and harness_ref.get("location") == "local"
            and compute_ref.get("location") == "hosted"
        )
        if connector_required and not isinstance(connector_id, str):
            raise UnsupportedHybridRoute("a local harness needs a scoped hosted-compute connector")
        if (pricing_disposition.dispatch_permitted or pending) and not connector_required and connector_id is not None:
            raise UnsupportedHybridRoute("connector grants are restricted to local-harness hosted-compute routes")

        decision = _mapping(plan, "decision")
        if decision.get("compatibility") != "passed" or decision.get("policy") not in {
            "passed",
            "user_override",
        }:
            raise UnsupportedHybridRoute("route decision has not passed compatibility and policy")

        issued_authorization_id = authorization_id or f"desktop-route-{uuid.uuid4().hex}"
        result = DesktopHybridRoute._from_planner(
            authorization_id=issued_authorization_id,
            route_plan_id=_string(plan.get("route_plan_id"), "route_plan_id"),
            route_plan_digest=plan_record.digest,
            route_plan_version=plan_record.schema_version,
            pricing_lifecycle_id=(
                plan["pricing_lifecycle_id"] if pending else pricing_record.record_id
            ),
            pricing_lifecycle_digest=(
                _jcs_digest({"pending_route_plan_digest": plan_record.digest})
                if pending else pricing_record.digest
            ),
            pricing_contract_version=(
                plan["pricing_contract_version"] if pending else pricing_record.schema_version
            ),
            pricing_state=pricing_disposition.state,
            pricing_release_phase=pricing_disposition.release_phase,
            pricing_release_reason=pricing_disposition.release_reason,
            dispatch_permitted=pricing_disposition.dispatch_permitted,
            operation_id=_string(plan.get("operation_id"), "operation_id"),
            actor_type=actor["type"],
            actor_id=actor["id"],
            tenant_id=_string(plan.get("tenant_id"), "tenant_id"),
            project_bundle_id=_string(bundle.get("bundle_id"), "bundle_id"),
            project_id=_string(project.get("project_id"), "project_id"),
            project_revision_digest=_string(project.get("revision_digest"), "revision_digest"),
            project_location=_string(persistence.get("location"), "persistence location"),
            harness_id=harness_id,
            harness_name=_string(harness_doc.get("display_name"), "harness display_name"),
            harness_location=_string(harness_doc.get("location"), "harness location"),
            inference_route_id=route_id,
            inference_name=_string(inference_doc.get("display_name"), "inference display_name"),
            inference_provider=_string(inference_doc.get("provider"), "inference provider"),
            inference_model=model,
            inference_location=_string(inference_doc.get("location"), "inference location"),
            compute_target_id=target_id,
            compute_name=_string(target_doc.get("display_name"), "compute display_name"),
            compute_location=_string(target_doc.get("location"), "compute location"),
            executor_adapter_id=_string(executor.get("adapter_id"), "executor adapter_id"),
            executor_adapter_version=_string(executor.get("adapter_version"), "executor adapter_version"),
            executor_target_kind=_string(executor.get("target_kind"), "executor target_kind"),
            image_repository=_string(image.get("repository"), "image repository"),
            image_digest=_string(image.get("digest"), "image digest"),
            capabilities_digest=_string(image.get("capabilities_digest"), "image capabilities_digest"),
            attestation_digest=_string(image.get("attestation_digest"), "image attestation_digest"),
            transfer_manifest_id=transfer_id if isinstance(transfer_id, str) else None,
            connector_grant_id=connector_id if isinstance(connector_id, str) else None,
            transfer_inputs=transfer_inputs,
        )
        if authorization_id is None and store_authorization and pricing_disposition.dispatch_permitted:
            with self._authorization_lock:
                self._route_authorizations[issued_authorization_id] = (
                    plan_record,
                    pricing_record,
                    bundle_record,
                    requirements,
                    allow_missing_transfer,
                )
        return result

    @staticmethod
    def _lookup(records: dict[str, AcceptedContractRecord], record_id: Any, label: str) -> AcceptedContractRecord:
        if not isinstance(record_id, str) or record_id not in records:
            raise UnsupportedHybridRoute(f"unknown {label}: {record_id!r}")
        return records[record_id]

    def _validate_persisted_receipt(
        self,
        *,
        plan_record: AcceptedContractRecord,
        bundle: JsonObject,
        transfer: JsonObject,
    ) -> None:
        attached_digest = _jcs_digest(bundle)
        receipts = self._transfer_receipts.receipts_for_bundle(attached_digest)
        if not receipts:
            raise ProjectTransferConsentError("hosted use requires a persisted Desktop transfer receipt")
        transfer_id = _string(transfer.get("transfer_id"), "transfer_id")
        matching = tuple(
            receipt for receipt in receipts if receipt.transfer_id == transfer_id and receipt.route_plan_digest == plan_record.digest
        )
        if len(matching) != 1:
            raise ProjectTransferConsentError("Desktop transfer receipt is missing or ambiguous")
        receipt = matching[0]
        project = _mapping(bundle, "project")
        approval = _mapping(transfer, "approval")
        actor = _actor(_mapping(transfer, "binding").get("actor"), "transfer actor")
        source_bundle = copy.deepcopy(bundle)
        source_bundle["transfer_manifests"] = [
            item
            for item in source_bundle.get("transfer_manifests", [])
            if not isinstance(item, dict) or item.get("transfer_id") != transfer_id
        ]
        expected = DesktopTransferReceipt(
            route_plan_digest=plan_record.digest,
            source_bundle_content_digest=_jcs_digest(source_bundle),
            attached_bundle_content_digest=attached_digest,
            project_contents_digest=_jcs_digest(bundle.get("contents")),
            transfer_manifest_digest=_jcs_digest(transfer),
            transfer_id=transfer_id,
            approval_id=_string(approval.get("approval_id"), "approval_id"),
            operation_id=_string(transfer.get("operation_id"), "operation_id"),
            actor_type=actor["type"],
            actor_id=actor["id"],
            tenant_id=_string(_mapping(transfer, "binding").get("tenant_id"), "tenant_id"),
            project_id=_string(project.get("project_id"), "project_id"),
            project_revision_digest=_string(project.get("revision_digest"), "project revision digest"),
        )
        if receipt != expected:
            raise ProjectTransferConsentError("Desktop transfer receipt does not bind this exact route and project")


class DesktopHybridPlannerFactory:
    """A schema-verified, credential-free factory registered by Desktop."""

    def __init__(self) -> None:
        root = _platform_contract_root()
        self._bundles = tuple(
            _canonical_platform_schemas(version, root).status for version in (PLATFORM_ALPHA1_VERSION, PLATFORM_ALPHA2_VERSION)
        )

    def create(
        self,
        *,
        harnesses: tuple[AcceptedContractRecord, ...],
        inference_routes: tuple[AcceptedContractRecord, ...],
        compute_targets: tuple[AcceptedContractRecord, ...],
        transfer_receipts: DesktopTransferReceiptStore,
    ) -> DesktopHybridPlanner:
        """Create a planner from records supplied by a later integration."""

        return DesktopHybridPlanner(
            harnesses=harnesses,
            inference_routes=inference_routes,
            compute_targets=compute_targets,
            transfer_receipts=transfer_receipts,
        )

    def readiness(self) -> JsonObject:
        """Return credential-free contract readiness for the existing health API."""

        versions = [bundle.schema_version for bundle in self._bundles]
        return {
            "status": "ready",
            "route_plan_versions": versions,
            "pricing_versions": versions,
            "bundles": [
                {
                    "schema_version": bundle.schema_version,
                    "bundle_id": bundle.bundle_id,
                    "bundle_digest": bundle.bundle_digest,
                }
                for bundle in self._bundles
            ],
        }


@dataclass(frozen=True)
class AccountConnectionState:
    """A sign-in result that says nothing about transferring project content."""

    actor_type: str
    actor_id: str
    tenant_id: str
    project_id: str
    project_location: str
    linkage_state: str
    existing_transfer_manifest_count: int
    new_transfer_manifest: None = None

    def to_dict(self) -> JsonObject:
        return {
            "actor": {"type": self.actor_type, "id": self.actor_id},
            "tenant_id": self.tenant_id,
            "project_id": self.project_id,
            "project_location": self.project_location,
            "linkage_state": self.linkage_state,
            "existing_transfer_manifest_count": self.existing_transfer_manifest_count,
            "new_transfer_manifest": None,
        }


def connect_account(bundle: AcceptedContractRecord, *, actor_type: str, actor_id: str, tenant_id: str) -> AccountConnectionState:
    """Connect an account while leaving a local project byte-for-byte untouched."""

    _expect_schema(bundle, "reasoning.platform.project-bundle")
    document = bundle.document()
    project = _mapping(document, "project")
    linkage = _mapping(document, "linkage")
    if project.get("origin") != "local":
        raise HybridPlanningError("the Desktop local-project boundary requires local origin")
    if actor_type not in {"user", "service", "harness"}:
        raise HybridPlanningError("invalid account actor type")
    for value, label in (
        (actor_id, "actor_id"),
        (tenant_id, "tenant_id"),
        (project.get("project_id"), "project_id"),
    ):
        if not isinstance(value, str) or not _STABLE_ID.fullmatch(value):
            raise HybridPlanningError(f"invalid {label}")
    manifests = document.get("transfer_manifests")
    if not isinstance(manifests, list):
        raise HybridPlanningError("project bundle transfer_manifests must be an array")
    return AccountConnectionState(
        actor_type=actor_type,
        actor_id=actor_id,
        tenant_id=tenant_id,
        project_id=project["project_id"],
        project_location="local",
        linkage_state=_string(linkage.get("state"), "linkage state"),
        existing_transfer_manifest_count=len(manifests),
    )


@dataclass(frozen=True)
class ProjectContentSelection:
    path: str
    artifact_id: str
    purpose: str

    def __post_init__(self) -> None:
        if not self.path or self.path.startswith("/") or ".." in self.path.split("/"):
            raise ValueError("selected project content must use a safe relative path")
        if not _ARTIFACT_ID.fullmatch(self.artifact_id):
            raise ValueError("invalid transfer artifact_id")
        if self.purpose not in {"inference_context", "compute_input", "both"}:
            raise ValueError("invalid transfer input purpose")


@dataclass(frozen=True)
class RetainedCloudData:
    kind: str
    retention: str
    purpose: str

    def __post_init__(self) -> None:
        if self.kind not in _RETAINED_KINDS:
            raise ValueError("invalid retained Cloud data kind")
        if self.retention not in _RETENTION_POLICIES:
            raise ValueError("invalid retained Cloud data policy")
        if not self.purpose.strip() or len(self.purpose) > 255:
            raise ValueError("retained Cloud data purpose must contain 1-255 characters")


@dataclass(frozen=True)
class ProjectTransferApproval:
    """Non-wire UI approval bound to exact project and route bytes."""

    route_plan_digest: str
    project_bundle_digest: str
    project_id: str
    project_revision_digest: str
    transfer_id: str
    approval_id: str
    actor_type: str
    actor_id: str
    approved_at: str
    selections: tuple[ProjectContentSelection, ...]
    retained_cloud_data: tuple[RetainedCloudData, ...]

    def to_bytes(self) -> bytes:
        return _canonical_json(
            {
                "approval_version": _APPROVAL_VERSION,
                "route_plan_digest": self.route_plan_digest,
                "project_bundle_digest": self.project_bundle_digest,
                "project_id": self.project_id,
                "project_revision_digest": self.project_revision_digest,
                "transfer_id": self.transfer_id,
                "approval_id": self.approval_id,
                "actor": {"type": self.actor_type, "id": self.actor_id},
                "approved_at": self.approved_at,
                "selections": [
                    {
                        "path": item.path,
                        "artifact_id": item.artifact_id,
                        "purpose": item.purpose,
                    }
                    for item in self.selections
                ],
                "retained_cloud_data": [
                    {
                        "kind": item.kind,
                        "retention": item.retention,
                        "purpose": item.purpose,
                    }
                    for item in self.retained_cloud_data
                ],
            }
        )

    @classmethod
    def from_bytes(cls, payload: bytes) -> ProjectTransferApproval:
        value = _parse_document(payload)
        if (
            set(value)
            != {
                "approval_version",
                "route_plan_digest",
                "project_bundle_digest",
                "project_id",
                "project_revision_digest",
                "transfer_id",
                "approval_id",
                "actor",
                "approved_at",
                "selections",
                "retained_cloud_data",
            }
            or value.get("approval_version") != _APPROVAL_VERSION
        ):
            raise ProjectTransferConsentError("project transfer approval has an unsupported shape")
        for key in ("route_plan_digest", "project_bundle_digest", "project_revision_digest"):
            if not _DIGEST.fullmatch(str(value.get(key, ""))):
                raise ProjectTransferConsentError("project transfer approval contains an invalid digest")
        for key in ("project_id", "transfer_id", "approval_id"):
            if not isinstance(value.get(key), str) or not _STABLE_ID.fullmatch(value[key]):
                raise ProjectTransferConsentError("project transfer approval contains an invalid identity")
        if not _TRANSFER_ID.fullmatch(value["transfer_id"]) or not _APPROVAL_ID.fullmatch(value["approval_id"]):
            raise ProjectTransferConsentError("project transfer approval contains an invalid approval identity")
        actor = _actor(value.get("actor"), "project transfer approval actor")
        approved_at = _string(value.get("approved_at"), "approved_at")
        _parse_time(approved_at, "approved_at")
        selections = value.get("selections")
        retained = value.get("retained_cloud_data")
        if not isinstance(selections, list) or not isinstance(retained, list):
            raise ProjectTransferConsentError("project transfer approval selections must be arrays")
        if set(actor) != {"type", "id"}:
            raise ProjectTransferConsentError("project transfer approval actor has an unsupported shape")
        if any(not isinstance(item, dict) or set(item) != {"path", "artifact_id", "purpose"} for item in selections):
            raise ProjectTransferConsentError("project transfer approval selection has an unsupported shape")
        if any(not isinstance(item, dict) or set(item) != {"kind", "retention", "purpose"} for item in retained):
            raise ProjectTransferConsentError("project transfer approval retained data has an unsupported shape")
        try:
            result = cls(
                route_plan_digest=value["route_plan_digest"],
                project_bundle_digest=value["project_bundle_digest"],
                project_id=value["project_id"],
                project_revision_digest=value["project_revision_digest"],
                transfer_id=value["transfer_id"],
                approval_id=value["approval_id"],
                actor_type=actor["type"],
                actor_id=actor["id"],
                approved_at=approved_at,
                selections=tuple(
                    ProjectContentSelection(
                        path=_string(_object(item, "selection").get("path"), "selection path"),
                        artifact_id=_string(_object(item, "selection").get("artifact_id"), "selection artifact_id"),
                        purpose=_string(_object(item, "selection").get("purpose"), "selection purpose"),
                    )
                    for item in selections
                ),
                retained_cloud_data=tuple(
                    RetainedCloudData(
                        kind=_string(_object(item, "retained data").get("kind"), "retained data kind"),
                        retention=_string(
                            _object(item, "retained data").get("retention"),
                            "retained data retention",
                        ),
                        purpose=_string(
                            _object(item, "retained data").get("purpose"),
                            "retained data purpose",
                        ),
                    )
                    for item in retained
                ),
            )
        except (HybridPlanningError, TypeError, ValueError) as error:
            raise ProjectTransferConsentError("project transfer approval contents are invalid") from error
        _reject_security_material(value)
        return result


@dataclass(frozen=True, init=False)
class ApprovedProjectTransfer:
    """Durable local authorization whose approval is rechecked on attachment."""

    route_plan_digest: str
    project_bundle_digest: str
    transfer_id: str
    _manifest_payload: bytes = field(repr=False)
    _approval_payload: bytes = field(repr=False)
    _claims_digest: str = field(repr=False, compare=False)

    @classmethod
    def _issue(
        cls,
        *,
        route_plan_digest: str,
        project_bundle_digest: str,
        transfer_id: str,
        manifest_payload: bytes,
        approval_payload: bytes,
    ) -> ApprovedProjectTransfer:
        instance = cls.__new__(cls)
        object.__setattr__(instance, "route_plan_digest", route_plan_digest)
        object.__setattr__(instance, "project_bundle_digest", project_bundle_digest)
        object.__setattr__(instance, "transfer_id", transfer_id)
        object.__setattr__(instance, "_manifest_payload", bytes(manifest_payload))
        object.__setattr__(instance, "_approval_payload", bytes(approval_payload))
        object.__setattr__(instance, "_claims_digest", instance._expected_claims_digest())
        return instance

    def _expected_claims_digest(self) -> str:
        return _jcs_digest(
            {
                "route_plan_digest": self.route_plan_digest,
                "project_bundle_digest": self.project_bundle_digest,
                "transfer_id": self.transfer_id,
                "manifest_payload_digest": _sha256_bytes(self._manifest_payload),
                "approval_payload_digest": _sha256_bytes(self._approval_payload),
            }
        )

    def _assert_authorized(self) -> ProjectTransferApproval:
        try:
            expected_claims_digest = self._expected_claims_digest()
        except (AttributeError, TypeError, ValueError) as error:
            raise ProjectTransferConsentError("project transfer authorization construction is invalid") from error
        if (
            getattr(self, "_claims_digest", None) != expected_claims_digest
            or not _DIGEST.fullmatch(str(self.route_plan_digest))
            or not _DIGEST.fullmatch(str(self.project_bundle_digest))
            or not _TRANSFER_ID.fullmatch(str(self.transfer_id))
        ):
            raise ProjectTransferConsentError("project transfer was not issued from explicit validated approval")
        approval = ProjectTransferApproval.from_bytes(self._approval_payload)
        if (
            approval.route_plan_digest != self.route_plan_digest
            or approval.project_bundle_digest != self.project_bundle_digest
            or approval.transfer_id != self.transfer_id
        ):
            raise ProjectTransferConsentError("project transfer authorization approval binding failed")
        manifest = _parse_document(self._manifest_payload)
        manifest_approval = _mapping(manifest, "approval")
        if (
            manifest.get("transfer_id") != self.transfer_id
            or manifest_approval.get("approval_id") != approval.approval_id
            or manifest_approval.get("approved_at") != approval.approved_at
        ):
            raise ProjectTransferConsentError("project transfer authorization manifest binding failed")
        _reject_security_material(manifest)
        return approval

    def to_bytes(self) -> bytes:
        self._assert_authorized()
        value = {
            "authorization_version": _APPROVED_TRANSFER_VERSION,
            "route_plan_digest": self.route_plan_digest,
            "project_bundle_digest": self.project_bundle_digest,
            "transfer_id": self.transfer_id,
            "approval": _parse_document(self._approval_payload),
            "manifest": _parse_document(self._manifest_payload),
        }
        value["authorization_digest"] = _jcs_digest(value)
        return _canonical_json(value)

    @classmethod
    def from_bytes(cls, payload: bytes) -> ApprovedProjectTransfer:
        value = _parse_document(payload)
        if (
            set(value)
            != {
                "authorization_version",
                "authorization_digest",
                "route_plan_digest",
                "project_bundle_digest",
                "transfer_id",
                "approval",
                "manifest",
            }
            or value.get("authorization_version") != _APPROVED_TRANSFER_VERSION
        ):
            raise ProjectTransferConsentError("project transfer authorization has an unsupported shape")
        authorization_digest = value.pop("authorization_digest")
        if authorization_digest != _jcs_digest(value):
            raise ProjectTransferConsentError("project transfer authorization integrity check failed")
        approval_payload = _canonical_json(_mapping(value, "approval"))
        ProjectTransferApproval.from_bytes(approval_payload)
        instance = cls._issue(
            route_plan_digest=_string(value.get("route_plan_digest"), "route plan digest"),
            project_bundle_digest=_string(value.get("project_bundle_digest"), "project bundle digest"),
            transfer_id=_string(value.get("transfer_id"), "transfer_id"),
            manifest_payload=_canonical_json(_mapping(value, "manifest")),
            approval_payload=approval_payload,
        )
        instance._assert_authorized()
        return instance

    def manifest(self) -> JsonObject:
        self._assert_authorized()
        return _parse_document(self._manifest_payload)

    def attach(
        self,
        bundle: AcceptedContractRecord,
        plan: AcceptedContractRecord,
        *,
        receipt_store: DesktopTransferReceiptStore,
    ) -> AcceptedContractRecord:
        approval = self._assert_authorized()
        if bundle.digest != self.project_bundle_digest:
            raise ProjectTransferConsentError("transfer approval belongs to another project bundle")
        if plan.digest != self.route_plan_digest:
            raise ProjectTransferConsentError("transfer approval belongs to another route plan")
        document = bundle.document()
        manifests = document.get("transfer_manifests")
        if not isinstance(manifests, list):
            raise ProjectTransferConsentError("project bundle transfer_manifests must be an array")
        if any(isinstance(item, dict) and item.get("transfer_id") == self.transfer_id for item in manifests):
            raise ProjectTransferConsentError("transfer id already exists in the project bundle")
        result = copy.deepcopy(document)
        manifest = _derive_transfer_manifest(bundle, plan, approval)
        if _canonical_json(manifest) != self._manifest_payload:
            raise ProjectTransferConsentError("project transfer authorization does not rederive exactly")
        result["transfer_manifests"].append(manifest)
        attached = AcceptedContractRecord.from_document(result)
        project = _mapping(document, "project")
        binding = _mapping(manifest, "binding")
        actor = _actor(binding.get("actor"), "transfer actor")
        approval = _mapping(manifest, "approval")
        receipt_store._put_approved(
            DesktopTransferReceipt(
                route_plan_digest=self.route_plan_digest,
                source_bundle_content_digest=_jcs_digest(document),
                attached_bundle_content_digest=_jcs_digest(result),
                project_contents_digest=_jcs_digest(document.get("contents")),
                transfer_manifest_digest=_jcs_digest(manifest),
                transfer_id=self.transfer_id,
                approval_id=_string(approval.get("approval_id"), "approval_id"),
                operation_id=_string(manifest.get("operation_id"), "operation_id"),
                actor_type=actor["type"],
                actor_id=actor["id"],
                tenant_id=_string(binding.get("tenant_id"), "tenant_id"),
                project_id=_string(project.get("project_id"), "project_id"),
                project_revision_digest=_string(project.get("revision_digest"), "project revision digest"),
            ),
            manifest=manifest,
        )
        return attached


def approve_project_transfer(
    planner: DesktopHybridPlanner,
    bundle: AcceptedContractRecord,
    plan: AcceptedContractRecord,
    pricing: AcceptedContractRecord,
    approval: ProjectTransferApproval,
) -> ApprovedProjectTransfer:
    """Generate one bounded transfer only from an explicit, exact approval."""

    route = planner.prepare_transfer_route(plan, pricing, bundle)
    manifest = _derive_transfer_manifest(bundle, plan, approval, route=route)
    approval_payload = approval.to_bytes()
    if ProjectTransferApproval.from_bytes(approval_payload) != approval:
        raise ProjectTransferConsentError("project transfer approval does not round-trip exactly")
    return ApprovedProjectTransfer._issue(
        route_plan_digest=plan.digest,
        project_bundle_digest=bundle.digest,
        transfer_id=approval.transfer_id,
        manifest_payload=_canonical_json(manifest),
        approval_payload=approval_payload,
    )


def _derive_transfer_manifest(
    bundle: AcceptedContractRecord,
    plan: AcceptedContractRecord,
    approval: ProjectTransferApproval,
    *,
    route: DesktopHybridRoute | None = None,
) -> JsonObject:
    """Independently derive approved disclosure from exact local records."""

    _expect_schema(plan, "reasoning.platform.route-plan")
    _expect_schema(bundle, "reasoning.platform.project-bundle")
    if approval.route_plan_digest != plan.digest:
        raise ProjectTransferConsentError("approval route digest does not match the selected plan")
    if approval.project_bundle_digest != bundle.digest:
        raise ProjectTransferConsentError("approval project digest does not match the local project")
    document = bundle.document()
    plan_document = plan.document()
    project = _mapping(document, "project")
    linkage = _mapping(document, "linkage")
    persistence = _mapping(plan_document, "persistence")
    actor = _actor(plan_document.get("actor"), "route plan actor")
    if project.get("origin") != "local" or linkage != {"state": "unlinked"}:
        raise ProjectTransferConsentError("this reference issues one-step consent for unlinked local projects")
    if persistence.get("location") != "local":
        raise ProjectTransferConsentError("explicit approval cannot relocate local project persistence")
    if persistence.get("project_bundle_id") != document.get("bundle_id"):
        raise ProjectTransferConsentError("approval route substitutes the project bundle")
    if persistence.get("project_id") != project.get("project_id"):
        raise ProjectTransferConsentError("approval route substitutes the project id")
    if persistence.get("project_revision_digest") != project.get("revision_digest"):
        raise ProjectTransferConsentError("approval route substitutes the project revision")
    references = (
        _mapping(plan_document, "harness"),
        _mapping(plan_document, "inference"),
        _mapping(plan_document, "compute"),
    )
    if not any(reference.get("location") == "hosted" for reference in references):
        raise ProjectTransferConsentError("the selected route does not require a transfer")
    transfer_manifest_id = persistence.get("transfer_manifest_id")
    if approval.transfer_id != transfer_manifest_id or not _TRANSFER_ID.fullmatch(approval.transfer_id):
        raise ProjectTransferConsentError("approval transfer id does not match the route")
    if approval.project_id != project.get("project_id"):
        raise ProjectTransferConsentError("approval project id does not match the route")
    if approval.project_revision_digest != project.get("revision_digest"):
        raise ProjectTransferConsentError("approval project revision does not match the route")
    if approval.actor_type != actor["type"] or approval.actor_id != actor["id"]:
        raise ProjectTransferConsentError("approval actor does not match the route actor")
    if not _APPROVAL_ID.fullmatch(approval.approval_id):
        raise ProjectTransferConsentError("invalid transfer approval id")
    _parse_time(approval.approved_at, "approved_at")
    operation_id = _string(plan_document.get("operation_id"), "operation_id")
    tenant_id = _string(plan_document.get("tenant_id"), "tenant_id")
    project_id = _string(project.get("project_id"), "project_id")
    revision_digest = _string(project.get("revision_digest"), "project revision digest")
    if route is not None:
        route._assert_claims_intact()
        expected_route = {
            "route_plan_digest": plan.digest,
            "project_bundle_id": document.get("bundle_id"),
            "project_id": project_id,
            "project_revision_digest": revision_digest,
            "transfer_manifest_id": transfer_manifest_id,
            "operation_id": operation_id,
            "tenant_id": tenant_id,
            "actor_type": actor["type"],
            "actor_id": actor["id"],
        }
        if any(getattr(route, key) != value for key, value in expected_route.items()):
            raise ProjectTransferConsentError("planner route does not match the exact approval records")

    selections = approval.selections
    if not selections or len(selections) > MAX_TRANSFER_INPUTS:
        raise ProjectTransferConsentError(f"a transfer must select 1-{MAX_TRANSFER_INPUTS} exact inputs")
    if len({item.path for item in selections}) != len(selections):
        raise ProjectTransferConsentError("transfer content paths must be unique")
    if len({item.artifact_id for item in selections}) != len(selections):
        raise ProjectTransferConsentError("transfer artifact ids must be unique")
    contents = document.get("contents")
    if not isinstance(contents, list):
        raise ProjectTransferConsentError("project contents must be an array")
    by_path = {item.get("path"): item for item in contents if isinstance(item, dict) and isinstance(item.get("path"), str)}
    disclosed_inputs: list[JsonObject] = []
    total_bytes = 0
    for selection in selections:
        content = by_path.get(selection.path)
        if content is None:
            raise ProjectTransferConsentError(f"approved input is absent from the exact project revision: {selection.path}")
        size = content.get("size_bytes")
        digest = content.get("digest")
        media_type = content.get("media_type")
        if (
            not isinstance(size, int)
            or isinstance(size, bool)
            or size < 0
            or not isinstance(digest, str)
            or not _DIGEST.fullmatch(digest)
            or not isinstance(media_type, str)
            or not media_type
        ):
            raise ProjectTransferConsentError("project content metadata is invalid")
        total_bytes += size
        if total_bytes > MAX_TRANSFER_BYTES:
            raise ProjectTransferConsentError(f"approved transfer exceeds {MAX_TRANSFER_BYTES} bytes")
        disclosed_inputs.append(
            {
                "artifact_id": selection.artifact_id,
                "digest": digest,
                "size_bytes": size,
                "media_type": media_type,
                "purpose": selection.purpose,
            }
        )

    retained = approval.retained_cloud_data
    if not retained or len(retained) > MAX_RETAINED_CLOUD_CLASSES:
        raise ProjectTransferConsentError(f"retained Cloud data must name 1-{MAX_RETAINED_CLOUD_CLASSES} classes")
    if len({item.kind for item in retained}) != len(retained):
        raise ProjectTransferConsentError("retained Cloud data kinds must be unique")
    disclosure = {
        "inputs": disclosed_inputs,
        "retained_cloud_data": [
            {
                "kind": item.kind,
                "retention": item.retention,
                "purpose": item.purpose,
            }
            for item in retained
        ],
        "omitted_data_classes": list(_OMITTED_DATA_CLASSES),
    }
    disclosure_digest = _jcs_digest(disclosure)
    approval_actor = {"type": approval.actor_type, "id": approval.actor_id}
    manifest = {
        "transfer_id": approval.transfer_id,
        "operation_id": operation_id,
        "binding": {
            "actor": approval_actor,
            "tenant_id": tenant_id,
            "project_id": project_id,
            "project_revision_digest": revision_digest,
        },
        "mode": "one_step",
        "disclosure": disclosure,
        "disclosure_digest": disclosure_digest,
        "approval": {
            "approval_id": approval.approval_id,
            "actor": approval_actor,
            "approved_at": approval.approved_at,
            "scope": "project_transfer",
            "approved_disclosure_digest": disclosure_digest,
        },
        "future_transfers_authorized": False,
    }
    _reject_security_material(manifest)
    return manifest


def _validate_transfer_manifest(transfer: JsonObject, plan: JsonObject, project: JsonObject) -> tuple[tuple[str, str, int, str], ...]:
    binding = _mapping(transfer, "binding")
    approval = _mapping(transfer, "approval")
    disclosure = _mapping(transfer, "disclosure")
    expected_binding = {
        "actor": plan.get("actor"),
        "tenant_id": plan.get("tenant_id"),
        "project_id": project.get("project_id"),
        "project_revision_digest": project.get("revision_digest"),
    }
    if transfer.get("operation_id") != plan.get("operation_id"):
        raise ProjectTransferConsentError("transfer operation binding does not match the route")
    if binding != expected_binding:
        raise ProjectTransferConsentError("transfer project binding does not match the route")
    digest = _jcs_digest(disclosure)
    if transfer.get("disclosure_digest") != digest:
        raise ProjectTransferConsentError("transfer disclosure digest mismatch")
    if approval.get("actor") != plan.get("actor"):
        raise ProjectTransferConsentError("transfer approval actor mismatch")
    if approval.get("scope") != "project_transfer":
        raise ProjectTransferConsentError("transfer approval has the wrong scope")
    if approval.get("approved_disclosure_digest") != digest:
        raise ProjectTransferConsentError("transfer approval does not bind the disclosure")
    inputs = disclosure.get("inputs")
    if not isinstance(inputs, list) or not inputs or len(inputs) > MAX_TRANSFER_INPUTS:
        raise ProjectTransferConsentError("transfer input count is outside the Desktop limit")
    if len({item.get("artifact_id") for item in inputs if isinstance(item, dict)}) != len(inputs):
        raise ProjectTransferConsentError("transfer artifact ids must be unique")
    total = 0
    summaries: list[tuple[str, str, int, str]] = []
    for item in inputs:
        if not isinstance(item, dict):
            raise ProjectTransferConsentError("transfer input must be an object")
        artifact_id = item.get("artifact_id")
        digest_value = item.get("digest")
        size = item.get("size_bytes")
        purpose = item.get("purpose")
        if (
            not isinstance(artifact_id, str)
            or not _ARTIFACT_ID.fullmatch(artifact_id)
            or not isinstance(digest_value, str)
            or not _DIGEST.fullmatch(digest_value)
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size < 0
            or purpose not in {"inference_context", "compute_input", "both"}
        ):
            raise ProjectTransferConsentError("transfer input metadata is invalid")
        total += size
        summaries.append((artifact_id, digest_value, size, str(purpose)))
    if total > MAX_TRANSFER_BYTES:
        raise ProjectTransferConsentError("transfer input bytes exceed the Desktop limit")
    retained = disclosure.get("retained_cloud_data")
    if not isinstance(retained, list) or not retained or len(retained) > MAX_RETAINED_CLOUD_CLASSES:
        raise ProjectTransferConsentError("retained Cloud data is outside the Desktop limit")
    if disclosure.get("omitted_data_classes") != list(_OMITTED_DATA_CLASSES):
        raise ProjectTransferConsentError("transfer must explicitly omit local and authentication data")
    if transfer.get("mode") == "one_step" and transfer.get("future_transfers_authorized") is not False:
        raise ProjectTransferConsentError("one-step consent cannot authorize future transfers")
    _reject_security_material(transfer)
    return tuple(summaries)


def _validate_pricing_pair(
    plan_record: AcceptedContractRecord,
    pricing_record: AcceptedContractRecord,
) -> _PricingDisposition:
    """Validate cross-record pricing semantics for one exact negotiated pair."""

    plan = plan_record.document()
    pricing = pricing_record.document()
    version = plan_record.schema_version
    if pricing_record.schema_version != version:
        raise UnsupportedHybridRoute("route-plan and pricing versions must be negotiated as an exact pair")
    if version == PLATFORM_ALPHA2_VERSION:
        if plan.get("pricing_contract_version") != PLATFORM_ALPHA2_VERSION:
            raise UnsupportedHybridRoute("alpha.2 route-plan does not declare its exact pricing contract")
    elif version != PLATFORM_ALPHA1_VERSION:
        raise UnsupportedHybridRoute("unsupported route-plan and pricing version")

    operation = _mapping(pricing, "operation")
    persistence = _mapping(plan, "persistence")
    bindings = (
        (pricing.get("pricing_lifecycle_id"), plan.get("pricing_lifecycle_id"), "pricing lifecycle"),
        (operation.get("route_plan_id"), plan.get("route_plan_id"), "pricing route-plan"),
        (operation.get("operation_id"), plan.get("operation_id"), "pricing operation"),
        (operation.get("actor"), plan.get("actor"), "pricing actor"),
        (operation.get("tenant_id"), plan.get("tenant_id"), "pricing tenant"),
        (operation.get("project_id"), persistence.get("project_id"), "pricing project"),
    )
    for observed, expected, label in bindings:
        if observed != expected:
            raise UnsupportedHybridRoute(f"{label} binding does not match the route-plan")

    quote = _mapping(pricing, "quote")
    reservation = _mapping(pricing, "reservation")
    settlement = _mapping(pricing, "settlement")
    dispatches = pricing.get("dispatches")
    usage = pricing.get("usage")
    if not isinstance(dispatches, list) or not isinstance(usage, list):
        raise UnsupportedHybridRoute("pricing dispatch and usage collections must be arrays")
    issued_at = _parse_time(_string(quote.get("issued_at"), "quote issued_at"), "quote issued_at")
    expires_at = _parse_time(_string(quote.get("expires_at"), "quote expires_at"), "quote expires_at")
    reserved_at = _parse_time(_string(reservation.get("reserved_at"), "reservation reserved_at"), "reservation reserved_at")
    decided_at = _parse_time(
        _string(_mapping(plan, "decision").get("decided_at"), "route decision decided_at"),
        "route decision decided_at",
    )
    if not decided_at <= issued_at < expires_at:
        raise UnsupportedHybridRoute("route decision and quote chronology is invalid")
    if not issued_at <= reserved_at <= expires_at:
        raise UnsupportedHybridRoute("reservation must occur during the exact quote window")
    if quote.get("quote_id") != reservation.get("quote_id"):
        raise UnsupportedHybridRoute("reservation does not bind the exact quote")
    if settlement.get("reservation_id") != reservation.get("reservation_id"):
        raise UnsupportedHybridRoute("pricing settlement does not bind the exact reservation")

    dimensions = quote.get("dimensions")
    if not isinstance(dimensions, list):
        raise UnsupportedHybridRoute("pricing quote dimensions must be an array")
    maximum_amount = Decimal("0")
    quoted_meters: dict[tuple[Any, Any], JsonObject] = {}
    for dimension in dimensions:
        item = _object(dimension, "pricing quote dimension")
        amount = Decimal(_string(item.get("maximum_amount"), "dimension maximum_amount"))
        calculated = Decimal(_string(item.get("maximum_quantity"), "dimension maximum_quantity")) * Decimal(
            _string(item.get("unit_price"), "dimension unit_price")
        )
        if amount != calculated:
            raise UnsupportedHybridRoute("pricing quote dimension amount is inconsistent")
        key = (item.get("kind"), item.get("meter"))
        if key in quoted_meters:
            raise UnsupportedHybridRoute("pricing quote meter is ambiguous")
        quoted_meters[key] = item
        maximum_amount += amount
    if maximum_amount != Decimal(_string(quote.get("maximum_amount"), "quote maximum_amount")):
        raise UnsupportedHybridRoute("pricing quote maximum amount is inconsistent")
    reserved_amount = Decimal(_string(reservation.get("reserved_amount"), "reservation reserved_amount"))
    if reserved_amount > maximum_amount:
        raise UnsupportedHybridRoute("pricing reservation exceeds the quote")

    state = _string(settlement.get("state"), "pricing settlement state")
    release_phase_value = settlement.get("release_phase")
    release_phase = release_phase_value if isinstance(release_phase_value, str) else None
    release_reason_value = settlement.get("release_reason")
    release_reason = release_reason_value if isinstance(release_reason_value, str) else None
    if release_phase == "pre_dispatch":
        if version != PLATFORM_ALPHA2_VERSION:
            raise UnsupportedHybridRoute("alpha.1 pricing dispatch evidence cannot be reinterpreted as pre-dispatch release")
        if dispatches or usage:
            raise UnsupportedHybridRoute("pre-dispatch release must not contain dispatch or usage evidence")
        if [quote.get("sequence"), reservation.get("sequence"), settlement.get("sequence")] != [1, 2, 3]:
            raise UnsupportedHybridRoute("pre-dispatch release chronology must be quote, reservation, release")
        released_at = _parse_time(_string(settlement.get("released_at"), "pricing released_at"), "pricing released_at")
        if released_at < reserved_at:
            raise UnsupportedHybridRoute("pre-dispatch release precedes its reservation")
        if release_reason == "quote_expired":
            if released_at < expires_at:
                raise UnsupportedHybridRoute("quote-expired release precedes quote expiry")
        elif release_reason == "cancelled_before_dispatch":
            if released_at >= expires_at:
                raise UnsupportedHybridRoute("pre-dispatch cancellation did not occur before quote expiry")
        else:
            raise UnsupportedHybridRoute("pre-dispatch release reason is unsupported")
        if (
            state != "released"
            or settlement.get("actual_amount") != "0"
            or Decimal(_string(settlement.get("released_amount"), "pricing released_amount")) != reserved_amount
            or settlement.get("reservation_state") != "released"
            or settlement.get("atomic") is not True
        ):
            raise UnsupportedHybridRoute("pre-dispatch release does not completely and atomically release the reservation")
        forbidden_execution_keys = {
            "request_id",
            "job_id",
            "dispatched_at",
            "provider",
            "model",
            "executor_contract_version",
            "executor_provenance_id",
            "image_digest",
            "capabilities_digest",
            "attestation_digest",
            "artifact_id",
            "usage_id",
            "usage_digest",
            "provenance",
            "actual_provenance_complete",
        }
        if forbidden_execution_keys.intersection(_walk_keys(pricing)):
            raise UnsupportedHybridRoute("pre-dispatch release contains an execution or actual-provenance claim")
        return _PricingDisposition(state, release_phase, release_reason, False)

    if not dispatches or not usage:
        raise UnsupportedHybridRoute("dispatched pricing requires dispatch and usage evidence")
    if {item.get("reservation_id") for item in dispatches if isinstance(item, dict)} != {reservation.get("reservation_id")}:
        raise UnsupportedHybridRoute("pricing dispatches do not bind the exact reservation")
    dispatch_times = [
        _parse_time(_string(_object(item, "pricing dispatch").get("dispatched_at"), "dispatch dispatched_at"), "dispatch dispatched_at")
        for item in dispatches
    ]
    if any(not reserved_at <= dispatch_at <= expires_at for dispatch_at in dispatch_times):
        raise UnsupportedHybridRoute("pricing dispatch falls outside the reserved quote window")
    sequences = [quote.get("sequence"), reservation.get("sequence")]
    sequences.extend(_object(item, "pricing dispatch").get("sequence") for item in dispatches)
    sequences.extend(_object(item, "pricing usage").get("sequence") for item in usage)
    sequences.append(settlement.get("sequence"))
    if any(not isinstance(sequence, int) or isinstance(sequence, bool) for sequence in sequences):
        raise UnsupportedHybridRoute("pricing chronology contains an invalid sequence")
    if sequences != sorted(sequences) or len(sequences) != len(set(sequences)):
        raise UnsupportedHybridRoute("pricing chronology is not strictly increasing")
    if settlement.get("usage_digest") != _jcs_digest(usage):
        raise UnsupportedHybridRoute("pricing settlement does not bind exact usage")

    dispatch_request_ids = {item.get("request_id") for item in dispatches if isinstance(item, dict) and item.get("kind") == "inference"}
    dispatch_job_ids = {item.get("job_id") for item in dispatches if isinstance(item, dict) and item.get("kind") == "compute"}
    actual_amount = Decimal("0")
    for usage_value in usage:
        usage_item = _object(usage_value, "pricing usage")
        provenance = _mapping(usage_item, "provenance")
        if usage_item.get("kind") == "inference":
            expected_provenance = {
                "route_id": _mapping(plan, "inference").get("route_id"),
                "provider": _mapping(plan, "inference").get("provider"),
                "model": _mapping(plan, "inference").get("model"),
            }
            if any(provenance.get(key) != value for key, value in expected_provenance.items()):
                raise UnsupportedHybridRoute("inference usage provenance does not match the route")
            if provenance.get("request_id") not in dispatch_request_ids:
                raise UnsupportedHybridRoute("inference usage does not bind an exact dispatch")
        else:
            compute = _mapping(plan, "compute")
            image = _mapping(compute, "image")
            expected_provenance = {
                "target_id": compute.get("target_id"),
                "image_digest": image.get("digest"),
                "capabilities_digest": image.get("capabilities_digest"),
                "attestation_digest": image.get("attestation_digest"),
            }
            if any(provenance.get(key) != value for key, value in expected_provenance.items()):
                raise UnsupportedHybridRoute("compute usage provenance does not match the route")
            if provenance.get("job_id") not in dispatch_job_ids:
                raise UnsupportedHybridRoute("compute usage does not bind an exact dispatch")
        if usage_item.get("source") == "unknown":
            if usage_item.get("amount") is not None or usage_item.get("dimensions") != []:
                raise UnsupportedHybridRoute("unknown pricing usage contains an actual amount")
            continue
        usage_amount = Decimal(_string(usage_item.get("amount"), "pricing usage amount"))
        usage_dimensions = usage_item.get("dimensions")
        if not isinstance(usage_dimensions, list) or not usage_dimensions:
            raise UnsupportedHybridRoute("actual pricing usage has no dimensions")
        calculated_amount = Decimal("0")
        for usage_dimension in usage_dimensions:
            item = _object(usage_dimension, "pricing usage dimension")
            quoted = quoted_meters.get((usage_item.get("kind"), item.get("meter")))
            if quoted is None:
                raise UnsupportedHybridRoute("pricing usage meter was not quoted")
            quantity = Decimal(_string(item.get("quantity"), "pricing usage quantity"))
            if quantity > Decimal(_string(quoted.get("maximum_quantity"), "quoted maximum quantity")):
                raise UnsupportedHybridRoute("pricing usage exceeds the quoted maximum")
            calculated_amount += quantity * Decimal(_string(quoted.get("unit_price"), "quoted unit price"))
        if calculated_amount != usage_amount:
            raise UnsupportedHybridRoute("pricing usage amount is inconsistent")
        actual_amount += usage_amount

    if state == "settled":
        settled_at = _parse_time(_string(settlement.get("settled_at"), "settlement settled_at"), "settlement settled_at")
        if settled_at < max(dispatch_times):
            raise UnsupportedHybridRoute("pricing settlement precedes dispatch")
        if any(_object(item, "pricing usage").get("source") == "unknown" for item in usage):
            raise UnsupportedHybridRoute("settled pricing contains unknown usage")
        if actual_amount != Decimal(_string(settlement.get("actual_amount"), "settlement actual_amount")):
            raise UnsupportedHybridRoute("settled pricing actual amount is inconsistent")
        released_amount = Decimal(_string(settlement.get("released_amount"), "settlement released_amount"))
        if actual_amount + released_amount != reserved_amount or settlement.get("actual_provenance_complete") is not True:
            raise UnsupportedHybridRoute("settled pricing does not reconcile its reservation")
    elif state == "released":
        settled_at = _parse_time(_string(settlement.get("settled_at"), "settlement settled_at"), "settlement settled_at")
        if settled_at < max(dispatch_times):
            raise UnsupportedHybridRoute("pricing release precedes dispatch")
        if version == PLATFORM_ALPHA2_VERSION and release_phase != "post_dispatch":
            raise UnsupportedHybridRoute("alpha.2 dispatched release lacks the post-dispatch discriminator")
        if any(
            _object(item, "pricing usage").get("source") == "unknown"
            or Decimal(_string(_object(item, "pricing usage").get("amount"), "released usage amount")) != 0
            or any(
                Decimal(_string(_object(dimension, "released usage dimension").get("quantity"), "released usage quantity")) != 0
                for dimension in _object(item, "pricing usage").get("dimensions", [])
            )
            for item in usage
        ):
            raise UnsupportedHybridRoute("released post-dispatch pricing contains nonzero or unknown usage")
        if (
            settlement.get("actual_amount") != "0"
            or Decimal(_string(settlement.get("released_amount"), "settlement released_amount")) != reserved_amount
            or settlement.get("actual_provenance_complete") is not True
        ):
            raise UnsupportedHybridRoute("released post-dispatch pricing does not reconcile its reservation")
    elif state == "outcome_unknown":
        observed_at = _parse_time(_string(settlement.get("observed_at"), "settlement observed_at"), "settlement observed_at")
        if observed_at < max(dispatch_times):
            raise UnsupportedHybridRoute("pricing unknown-outcome observation precedes dispatch")
        if not any(_object(item, "pricing usage").get("source") == "unknown" for item in usage):
            raise UnsupportedHybridRoute("outcome-unknown pricing lacks unknown usage")
        if (
            settlement.get("actual_amount") is not None
            or settlement.get("released_amount") != "0"
            or settlement.get("reservation_state") != "remains_reserved"
            or settlement.get("reconciliation_required") is not True
        ):
            raise UnsupportedHybridRoute("outcome-unknown pricing does not retain its reservation")
    else:
        raise UnsupportedHybridRoute("pricing settlement state is unsupported")
    if settlement.get("atomic") is not True:
        raise UnsupportedHybridRoute("pricing settlement is not atomic")
    return _PricingDisposition(state, release_phase, release_reason, True)


def _walk_keys(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_keys(child)


def _index_records(
    records: tuple[AcceptedContractRecord, ...],
    expected_schema: str,
    lookup_key: str,
) -> dict[str, AcceptedContractRecord]:
    result: dict[str, AcceptedContractRecord] = {}
    for record in records:
        _expect_schema(record, expected_schema)
        document = record.document()
        key = document.get(lookup_key)
        if not isinstance(key, str) or key in result:
            raise HybridPlanningError(f"duplicate or invalid {expected_schema} record")
        result[key] = record
    if not result:
        raise HybridPlanningError(f"at least one {expected_schema} record is required")
    return result


def _expect_schema(record: AcceptedContractRecord, schema_id: str) -> None:
    if record.schema_id != schema_id:
        raise HybridPlanningError(f"expected {schema_id}, received {record.schema_id}")


def _mapping(value: JsonObject, key: str) -> JsonObject:
    result = value.get(key)
    if not isinstance(result, dict):
        raise HybridPlanningError(f"{key} must be an object")
    return result


def _object(value: Any, label: str) -> JsonObject:
    if not isinstance(value, dict):
        raise HybridPlanningError(f"{label} must be an object")
    return value


def _actor(value: Any, label: str) -> JsonObject:
    if (
        not isinstance(value, dict)
        or value.get("type") not in {"user", "service", "harness"}
        or not isinstance(value.get("id"), str)
        or not _STABLE_ID.fullmatch(value["id"])
    ):
        raise HybridPlanningError(f"invalid {label}")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise HybridPlanningError(f"{label} must be a non-empty string")
    return value


def _string_list(value: Any, label: str) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item for item in value)
        or len(set(value)) != len(value)
    ):
        raise HybridPlanningError(f"{label} must contain unique non-empty strings")
    return tuple(value)


def _reject_security_material(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower() in _SECRET_KEYS:
                raise HybridPlanningError(f"security material is forbidden at {path}.{key}")
            _reject_security_material(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_security_material(child, f"{path}[{index}]")
    elif isinstance(value, str) and any(pattern.search(value) for pattern in _SECRET_VALUE_PATTERNS):
        raise HybridPlanningError(f"credential-like security material is forbidden at {path}")


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _jcs_digest(value: Any) -> str:
    return _sha256_bytes(_canonical_json(value))


def _parse_time(value: str, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as error:
        raise HybridPlanningError(f"invalid {label}") from error
    if parsed.tzinfo is None:
        raise HybridPlanningError(f"{label} must be timezone-aware")
    return parsed
