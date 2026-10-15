"""Canonical, provider-neutral Cloud product-policy boundary.

This module validates the public contract family and supplies a deterministic
in-memory implementation for conformance tests. It contains no transport,
authentication, payment, provider, telemetry, or data-use activation behavior.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from .cloud_client import (
    CLOUD_CLIENT_BUNDLE_DIGEST,
    AcceptedCloudClientRecord,
    CloudClientContractValidator,
    ContractRecordReference,
    byte_digest,
    jcs_digest,
    parse_strict_json_object,
)

JsonObject = dict[str, Any]
DescriptorResolver = Callable[[ContractRecordReference], bytes]
DescriptorIdentity = tuple[str, str, str, str]
DescriptorBinding = tuple[str, str, str, str, str]

CLOUD_PRODUCT_POLICY_VERSION = "1.0.0-alpha.1"
CLOUD_PRODUCT_POLICY_BUNDLE_ID = "reasoning-cloud-product-policy-v1alpha1"
CLOUD_PRODUCT_POLICY_BUNDLE_DIGEST = "sha256:c78d03c0204a2524af0e78209a4cc51c1aaf40cac092b3fb4d0063cc81540b26"
CLOUD_CLIENT_MANIFEST_BYTE_DIGEST = "sha256:5c858b9346cfc796c50c7e2cc48ffee2d6c61334fc14f63d7ba24f46522e8bf3"
MAX_CLOUD_PRODUCT_POLICY_RECORD_BYTES = 1_048_576

_SCHEMA_FILES = {
    "reasoning.cloud-product-policy.account-policy": "account-policy.schema.json",
    "reasoning.cloud-product-policy.optional-use-change": "optional-use-change.schema.json",
}
_RECORD_ID_FIELDS = {
    "reasoning.cloud-product-policy.account-policy": "snapshot_id",
    "reasoning.cloud-product-policy.optional-use-change": "request_digest",
}
_STABLE_ID = re.compile(r"^[a-z][A-Za-z0-9._:-]{2,127}$")
_CREDENTIAL_KEYS = frozenset(
    {
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
)
_CREDENTIAL_VALUES = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}"),
    re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
)


class CloudProductPolicyContractError(ValueError):
    """A public product-policy record or binding is invalid."""


class CloudProductPolicyCredentialError(CloudProductPolicyContractError):
    """Credential-shaped material reached the public policy boundary."""


class CloudProductPolicyScopeError(RuntimeError):
    """The connected authority does not match the requested policy scope."""


class CloudProductPolicyCasConflict(RuntimeError):
    """A mutation's compare-and-swap bindings are stale."""


class CloudProductPolicyIdempotencyConflict(RuntimeError):
    """A scoped idempotency key was reused with different content."""


class CloudProductPolicyTransitionError(RuntimeError):
    """An optional-use state transition is not legal."""


class CloudProductPolicyHistoryLimitError(CloudProductPolicyTransitionError):
    """A bounded decision lineage cannot accept another transition."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> JsonObject:
    result: JsonObject = {}
    for key, value in pairs:
        if key in result:
            raise CloudProductPolicyContractError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_nonfinite(_value: str) -> None:
    raise CloudProductPolicyContractError("non-finite JSON number")


def parse_strict_cloud_product_policy_json(
    payload: bytes,
    *,
    maximum: int = MAX_CLOUD_PRODUCT_POLICY_RECORD_BYTES,
) -> JsonObject:
    """Parse one bounded UTF-8 JSON object without losing duplicate keys."""

    if not isinstance(payload, bytes) or not isinstance(maximum, int) or isinstance(maximum, bool) or maximum < 1:
        raise CloudProductPolicyContractError("strict JSON input requires exact bytes and a positive limit")
    if not payload or len(payload) > maximum:
        raise CloudProductPolicyContractError("record is empty or exceeds the accepted size")
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise CloudProductPolicyContractError("record is not strict UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise CloudProductPolicyContractError("record must be one JSON object")
    return value


def _jcs_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _walk(value: Any):
    yield value
    if isinstance(value, dict):
        for nested in value.values():
            yield from _walk(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk(nested)


def reject_cloud_product_policy_credentials(value: Any) -> None:
    """Reject credential-shaped keys and realistic credential values recursively."""

    for item in _walk(value):
        if isinstance(item, dict) and _CREDENTIAL_KEYS.intersection(str(key).casefold() for key in item):
            raise CloudProductPolicyCredentialError("credential-like field is forbidden")
        if isinstance(item, str) and any(pattern.search(item) for pattern in _CREDENTIAL_VALUES):
            raise CloudProductPolicyCredentialError("credential-like value is forbidden")


def _without(document: Mapping[str, Any], *keys: str) -> JsonObject:
    basis = copy.deepcopy(dict(document))
    for key in keys:
        basis.pop(key, None)
    return basis


def canonical_entitlement_digest(entitlement: Mapping[str, Any]) -> str:
    return jcs_digest({"id": entitlement.get("id"), "version": entitlement.get("version")})


def canonical_catalogue_digest(catalogue: Mapping[str, Any]) -> str:
    return jcs_digest({"catalogue_id": catalogue.get("catalogue_id"), "catalogue_version": catalogue.get("catalogue_version")})


def canonical_plan_digest(catalogue: Mapping[str, Any]) -> str:
    return jcs_digest({"plan_id": catalogue.get("plan_id"), "plan_version": catalogue.get("plan_version")})


def canonical_product_policy_digest(policy: Mapping[str, Any]) -> str:
    return jcs_digest(_without(policy, "digest"))


def canonical_optional_policy_digest(policy: Mapping[str, Any]) -> str:
    return jcs_digest(_without(policy, "digest"))


def canonical_decision_entry_digest(entry: Mapping[str, Any]) -> str:
    return jcs_digest(_without(entry, "digest"))


def canonical_policy_snapshot_digest(snapshot: Mapping[str, Any]) -> str:
    return jcs_digest(_without(snapshot, "snapshot_digest"))


def canonical_optional_use_request_digest(request: Mapping[str, Any]) -> str:
    return jcs_digest(_without(request, "request_digest"))


def _decimal(value: object, label: str) -> Decimal:
    if not isinstance(value, str):
        raise CloudProductPolicyContractError(f"{label} is not a canonical finite decimal")
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise CloudProductPolicyContractError(f"{label} is not a canonical finite decimal") from error
    if not parsed.is_finite() or parsed < 0:
        raise CloudProductPolicyContractError(f"{label} is not a non-negative finite decimal")
    return parsed


def _validate_product_policy_time(policy: JsonObject, observed: datetime) -> None:
    effective = _parse_time(policy["effective_at"], "product_policy.effective_at")
    expiry_value = policy["expires_at"]
    expiry = _parse_time(expiry_value, "product_policy.expires_at") if expiry_value is not None else None
    if expiry is not None and expiry <= effective:
        raise CloudProductPolicyContractError("product-policy expiry must follow its effective time")
    state = policy["state"]
    if state == "effective" and (observed < effective or expiry is not None and observed >= expiry):
        raise CloudProductPolicyContractError("effective product-policy chronology is inconsistent")
    if state == "expired" and (expiry is None or observed < expiry):
        raise CloudProductPolicyContractError("expired product-policy chronology is inconsistent")


def _validate_offer(offer: JsonObject, *, descriptor_location: str) -> None:
    price = offer["price"]
    price_state = price["state"]
    if price_state == "zero":
        if price["currency"] is None or price["lower_bound"] != "0" or price["upper_bound"] != "0" or price["basis"] is None:
            raise CloudProductPolicyContractError("zero price must be explicit and exact")
    elif price_state == "estimated":
        if price["currency"] is None or price["basis"] is None:
            raise CloudProductPolicyContractError("estimated price lacks explicit currency or basis")
        lower = _decimal(price["lower_bound"], "price.lower_bound")
        upper = _decimal(price["upper_bound"], "price.upper_bound")
        if lower > upper:
            raise CloudProductPolicyContractError("estimated price bounds are reversed")
        if upper == 0:
            raise CloudProductPolicyContractError("an exact zero charge must use the zero price state")
    elif any(price[field] is not None for field in ("currency", "lower_bound", "upper_bound", "basis")):
        raise CloudProductPolicyContractError("unknown or unavailable price cannot imply an amount")

    limits = offer["limits"]
    by_kind = {item["kind"]: item for item in limits}
    if len(by_kind) != len(limits) or set(by_kind) != {"operations", "concurrency", "spend"}:
        raise CloudProductPolicyContractError("offer requires one finite cap of every kind")
    for kind, limit in by_kind.items():
        _decimal(limit["maximum"], f"{kind} limit")
        if kind == "concurrency":
            if limit["period_seconds"] is not None or limit["unit"] not in {"operations", "jobs"}:
                raise CloudProductPolicyContractError("concurrency limit has inconsistent period or unit")
        elif limit["period_seconds"] is None:
            raise CloudProductPolicyContractError("periodic limit lacks a finite period")
        if kind == "spend" and limit["unit"] != "USD":
            raise CloudProductPolicyContractError("spend limit is not denominated in USD")
        if kind == "operations" and limit["unit"] not in {"operations", "jobs"}:
            raise CloudProductPolicyContractError("operation limit has an inconsistent unit")

    data_path = offer["data_path"]
    expected_location = "device_local" if descriptor_location == "local" else "hosted"
    if data_path["location"] != expected_location:
        raise CloudProductPolicyContractError("offer data-path location differs from its exact descriptor")
    telemetry = offer["required_telemetry"]
    if telemetry["state"] == "configured":
        if (
            any(telemetry[field] is None for field in ("policy_id", "version", "digest", "region", "retention_seconds"))
            or not telemetry["purposes"]
            or not telemetry["data_classes"]
            or not telemetry["processors"]
            or not telemetry["field_allowlist"]
        ):
            raise CloudProductPolicyContractError("configured required telemetry lacks exact bounded terms")
    elif (
        any(telemetry[field] is not None for field in ("policy_id", "version", "digest", "region", "retention_seconds"))
        or telemetry["purposes"]
        or telemetry["data_classes"]
        or telemetry["processors"]
        or telemetry["field_allowlist"]
    ):
        raise CloudProductPolicyContractError("inactive required telemetry carries implied configuration")

    if offer["availability"] == "available":
        if data_path["state"] != "verified" or price_state == "unavailable":
            raise CloudProductPolicyContractError("available offer lacks a verified path or price state")
        if data_path["location"] == "hosted":
            if data_path["region"] is None or telemetry["state"] != "configured":
                raise CloudProductPolicyContractError("hosted offer lacks region or configured required telemetry")
        elif (
            data_path["region"] is not None
            or data_path["subprocessors"]
            or data_path["provider_training"] != "prohibited"
            or telemetry["state"] != "not_applicable"
        ):
            raise CloudProductPolicyContractError("local offer is not wholly device-local")


def _validate_policy_terms(policy: JsonObject, label: str) -> tuple[datetime, datetime | None]:
    if policy["digest"] != canonical_optional_policy_digest(policy):
        raise CloudProductPolicyContractError(f"{label} digest is invalid")
    effective = _parse_time(policy["effective_at"], f"{label}.effective_at")
    expiry_value = policy["expires_at"]
    expiry = _parse_time(expiry_value, f"{label}.expires_at") if expiry_value is not None else None
    if expiry is not None and expiry <= effective:
        raise CloudProductPolicyContractError(f"{label} expiry must follow effective time")
    return effective, expiry


def _validate_optional_use(name: str, purpose: JsonObject, observed: datetime, product_policy_state: str) -> None:
    current_policy = purpose["policy"]
    current_effective, current_expiry = _validate_policy_terms(current_policy, f"optional_uses.{name}.policy")
    history = purpose["decision_history"]
    prior_time: datetime | None = None
    prior_state: str | None = None
    prior_policy: JsonObject | None = None
    for index, entry in enumerate(history):
        if entry["revision"] != index or entry["digest"] != canonical_decision_entry_digest(entry):
            raise CloudProductPolicyContractError("decision lineage revision or digest is invalid")
        decided = _parse_time(entry["decided_at"], "decision_history.decided_at")
        if (prior_time is not None and decided < prior_time) or decided > observed:
            raise CloudProductPolicyContractError("decision lineage chronology is invalid")
        action = entry["action"]
        if index == 0:
            if action != "initialize" or entry["state"] != "off" or entry["policy"] is not None or entry["policy_digest"] is not None:
                raise CloudProductPolicyContractError("decision lineage must initialize off")
        else:
            policy = entry["policy"]
            if policy is None:
                raise CloudProductPolicyContractError("decision transition lacks exact historical policy")
            effective, expiry = _validate_policy_terms(policy, "decision_history.policy")
            if entry["policy_digest"] != policy["digest"]:
                raise CloudProductPolicyContractError("decision lineage policy binding is invalid")
            if action == "grant":
                if (
                    prior_state not in {"off", "withdrawn"}
                    or entry["state"] != "granted"
                    or decided < effective
                    or (expiry is not None and decided >= expiry)
                ):
                    raise CloudProductPolicyContractError("decision lineage contains an illegal grant")
            elif action == "withdraw":
                if prior_state != "granted" or entry["state"] != "withdrawn" or policy != prior_policy:
                    raise CloudProductPolicyContractError("decision lineage contains an illegal withdrawal")
            else:
                raise CloudProductPolicyContractError("decision lineage contains an illegal transition")
        prior_time = decided
        prior_state = entry["state"]
        prior_policy = copy.deepcopy(entry["policy"])

    final = history[-1]
    if purpose["decision"] != {key: final[key] for key in purpose["decision"]}:
        raise CloudProductPolicyContractError("decision head does not equal final lineage entry")
    available_now = (
        purpose["availability"] == "available"
        and product_policy_state == "effective"
        and current_effective <= observed
        and (current_expiry is None or observed < current_expiry)
    )
    decision_state = purpose["decision"]["state"]
    expected_action = "withdraw" if decision_state == "granted" else "grant" if available_now else "none"
    if purpose["allowed_action"] != expected_action:
        raise CloudProductPolicyContractError("optional-use allowed action is inconsistent")
    if decision_state == "off" and len(history) != 1:
        raise CloudProductPolicyContractError("off is only the initial optional-use state")
    if decision_state == "granted" and purpose["prior_data_disposition"] != "not_applicable":
        raise CloudProductPolicyContractError("active grant cannot imply prior-data disposition")
    if decision_state == "withdrawn" and purpose["prior_data_disposition"] == "not_applicable":
        raise CloudProductPolicyContractError("withdrawal requires an explicit prior-data disposition")


class CloudProductPolicyValidator:
    """Offline validator for exactly one immutable product-policy bundle."""

    def __init__(self, repository_root: Path, *, frozen_layout: bool = False) -> None:
        self.repository_root = repository_root.resolve()
        self.frozen_layout = frozen_layout
        self.contract_root = self.repository_root / "contracts" / "cloud-product-policy" / "v1alpha1"
        self._schemas: dict[str, JsonObject] = {}
        self._registry = Registry()
        for path in sorted(self.contract_root.glob("*.schema.json")):
            document = parse_strict_cloud_product_policy_json(path.read_bytes())
            Draft202012Validator.check_schema(document)
            schema_id = document.get("$id")
            if not isinstance(schema_id, str) or schema_id in self._schemas:
                raise CloudProductPolicyContractError("schema identity is missing or duplicated")
            self._schemas[schema_id] = document
            self._registry = self._registry.with_resource(schema_id, Resource.from_contents(document))
        self.compatibility = parse_strict_cloud_product_policy_json((self.contract_root / "compatibility.json").read_bytes())
        self.manifest = parse_strict_cloud_product_policy_json((self.contract_root / "bundle-manifest.json").read_bytes())
        self._verify_bundle()
        self.cloud_client_validator = CloudClientContractValidator(self.repository_root, frozen_layout=frozen_layout)
        self._verify_owned_asset_projection()

    def _schema(self, filename: str) -> JsonObject:
        schema_id = f"https://schemas.reasoning.invalid/cloud-product-policy/v1alpha1/{filename}"
        try:
            return self._schemas[schema_id]
        except KeyError as error:
            raise CloudProductPolicyContractError("required product-policy schema is unavailable") from error

    def _validate(self, filename: str, document: JsonObject) -> None:
        validator = Draft202012Validator(self._schema(filename), registry=self._registry, format_checker=FormatChecker())
        error = next(validator.iter_errors(document), None)
        if error is not None:
            raise CloudProductPolicyContractError(f"record failed {filename} validation") from error

    def _verify_bundle(self) -> None:
        self._validate("compatibility.schema.json", self.compatibility)
        self._validate("contract-bundle.schema.json", self.manifest)
        if self.manifest.get("bundle_id") != CLOUD_PRODUCT_POLICY_BUNDLE_ID:
            raise CloudProductPolicyContractError("product-policy bundle identity changed")
        observed = jcs_digest(_without(self.manifest, "bundle_digest"))
        if observed != self.manifest.get("bundle_digest") or observed != CLOUD_PRODUCT_POLICY_BUNDLE_DIGEST:
            raise CloudProductPolicyContractError("product-policy bundle digest is invalid")
        members = self.manifest.get("members")
        dependencies = self.manifest.get("dependencies")
        if not isinstance(members, list) or not isinstance(dependencies, list):
            raise CloudProductPolicyContractError("product-policy manifest lists are invalid")
        member_paths = [item.get("path") for item in members if isinstance(item, dict)]
        dependency_paths = [item.get("path") for item in dependencies if isinstance(item, dict)]
        if member_paths != sorted(member_paths) or len(member_paths) != len(set(member_paths)):
            raise CloudProductPolicyContractError("product-policy manifest members are not sorted and unique")
        if dependency_paths != sorted(dependency_paths) or len(dependency_paths) != len(set(dependency_paths)):
            raise CloudProductPolicyContractError("product-policy dependencies are not sorted and unique")
        if dependencies != self.compatibility.get("dependencies"):
            raise CloudProductPolicyContractError("manifest and compatibility dependencies differ")
        for item in members:
            if not isinstance(item, dict) or set(item) != {"path", "digest"}:
                raise CloudProductPolicyContractError("manifest member has an unsupported shape")
            path = self.repository_root / str(item["path"])
            try:
                payload = path.read_bytes()
            except OSError as error:
                raise CloudProductPolicyContractError("manifest member is unavailable") from error
            if byte_digest(payload) != item.get("digest"):
                raise CloudProductPolicyContractError("manifest member digest mismatch")
        for item in dependencies:
            if not isinstance(item, dict) or set(item) != {"id", "version", "path", "digest", "internal_bundle_digest"}:
                raise CloudProductPolicyContractError("manifest dependency has an unsupported shape")
            path = self.repository_root / str(item["path"])
            try:
                payload = path.read_bytes()
            except OSError as error:
                raise CloudProductPolicyContractError("manifest dependency is unavailable") from error
            if byte_digest(payload) != item.get("digest"):
                raise CloudProductPolicyContractError("manifest dependency digest mismatch")
        cloud_dependencies = [item for item in dependencies if item.get("id") == "cloud-client"]
        if (
            len(cloud_dependencies) != 1
            or cloud_dependencies[0].get("digest") != CLOUD_CLIENT_MANIFEST_BYTE_DIGEST
            or cloud_dependencies[0].get("internal_bundle_digest") != CLOUD_CLIENT_BUNDLE_DIGEST
        ):
            raise CloudProductPolicyContractError("Cloud-client dependency digest changed")

    def _verify_owned_asset_projection(self) -> None:
        entries = self.manifest["members"]
        expected = {
            "contracts/cloud-product-policy/v1alpha1/bundle-manifest.json",
            *(str(item["path"]) for item in entries),
        }
        roots = (
            self.repository_root / "contracts" / "cloud-product-policy" / "v1alpha1",
            self.repository_root / "conformance" / "cloud-product-policy" / "v1alpha1",
        )
        observed: set[str] = set()
        try:
            for root in roots:
                if root.is_symlink() or not root.is_dir():
                    raise OSError("product-policy asset directory is unavailable")
                for path in root.rglob("*"):
                    if path.is_dir():
                        if path.is_symlink():
                            raise OSError("product-policy asset directory is a symlink")
                        continue
                    if path.is_symlink() or not path.is_file():
                        raise OSError("product-policy asset is not a regular file")
                    observed.add(path.relative_to(self.repository_root).as_posix())
        except OSError as error:
            raise CloudProductPolicyContractError("product-policy runtime asset projection is unavailable") from error
        if not self.frozen_layout:
            expected.add("contracts/cloud-product-policy/v1alpha1/README.md")
        if observed != expected:
            raise CloudProductPolicyContractError("product-policy runtime asset projection does not match the accepted bundle")

    def accept(self, payload: bytes) -> AcceptedCloudProductPolicyRecord:
        document = parse_strict_cloud_product_policy_json(payload)
        reject_cloud_product_policy_credentials(document)
        schema_id = document.get("schema_id")
        filename = _SCHEMA_FILES.get(str(schema_id))
        if filename is None or document.get("schema_version") != CLOUD_PRODUCT_POLICY_VERSION:
            raise CloudProductPolicyContractError("unknown product-policy contract identity")
        self._validate(filename, document)
        if schema_id == "reasoning.cloud-product-policy.account-policy":
            if document["snapshot_digest"] != canonical_policy_snapshot_digest(document):
                raise CloudProductPolicyContractError("account-policy snapshot digest is invalid")
        elif document["request_digest"] != canonical_optional_use_request_digest(document):
            raise CloudProductPolicyContractError("optional-use request digest is invalid")
        return AcceptedCloudProductPolicyRecord._accepted(payload, document)

    def validate_account_policy(
        self,
        record: AcceptedCloudProductPolicyRecord,
        *,
        authority: AccountPolicyAuthority,
    ) -> JsonObject:
        """Validate every semantic binding in one accepted account snapshot."""

        document = self.accept(record.to_bytes()).document()
        if record.schema_id != "reasoning.cloud-product-policy.account-policy":
            raise CloudProductPolicyContractError("record is not an account-policy snapshot")
        actor = document["actor"]
        capability = document["capability_snapshot"]
        if (
            document["account_id"] != authority.account_id
            or actor != {"type": authority.actor_type, "id": authority.actor_id}
            or document["tenant_id"] != authority.tenant_id
            or capability["record_id"] != authority.capability_snapshot_id
            or capability["digest"] != authority.capability_snapshot_digest
        ):
            raise CloudProductPolicyScopeError("connected policy authority is unavailable")

        entitlement = document["entitlement"]
        catalogue = document["catalogue"]
        product_policy = document["product_policy"]
        if entitlement["digest"] != canonical_entitlement_digest(entitlement):
            raise CloudProductPolicyContractError("entitlement projection digest is invalid")
        if catalogue["catalogue_digest"] != canonical_catalogue_digest(catalogue):
            raise CloudProductPolicyContractError("catalogue projection digest is invalid")
        if catalogue["plan_digest"] != canonical_plan_digest(catalogue):
            raise CloudProductPolicyContractError("plan projection digest is invalid")
        if product_policy["digest"] != canonical_product_policy_digest(product_policy):
            raise CloudProductPolicyContractError("product-policy definition digest is invalid")
        observed = _parse_time(document["observed_at"], "observed_at")
        _validate_product_policy_time(product_policy, observed)

        self._validate_offer_axis(
            document["inference_offers"],
            expected_schema="reasoning.platform.inference-route",
            allowed=authority.inference_descriptors,
        )
        self._validate_offer_axis(
            document["compute_offers"],
            expected_schema="reasoning.platform.compute-target",
            allowed=authority.compute_descriptors,
        )
        if product_policy["state"] != "effective" and any(
            offer["availability"] == "available" for axis in ("inference_offers", "compute_offers") for offer in document[axis]
        ):
            raise CloudProductPolicyContractError("an unavailable product policy cannot advertise an available offer")

        for purpose_name, purpose in document["optional_uses"].items():
            _validate_optional_use(purpose_name, purpose, observed, product_policy["state"])
        return document

    @staticmethod
    def _validate_offer_axis(
        offers: list[JsonObject],
        *,
        expected_schema: str,
        allowed: tuple[DescriptorBinding, ...],
    ) -> None:
        allowed_locations = {binding[:4]: binding[4] for binding in allowed}
        seen: set[DescriptorIdentity] = set()
        for offer in offers:
            reference = offer["reference"]
            identity = (
                reference["schema_id"],
                reference["schema_version"],
                reference["record_id"],
                reference["digest"],
            )
            if reference["schema_id"] != expected_schema or identity not in allowed_locations or identity in seen:
                raise CloudProductPolicyContractError("offer reference is absent, cross-axis, or duplicated")
            seen.add(identity)
            _validate_offer(offer, descriptor_location=allowed_locations[identity])


@dataclass(frozen=True, init=False)
class AcceptedCloudProductPolicyRecord:
    """Exact accepted policy bytes with byte-domain and JCS-domain digests."""

    schema_id: str
    schema_version: str
    record_id: str
    digest: str
    semantic_digest: str
    _payload: bytes = field(repr=False)

    @classmethod
    def from_bytes(cls, payload: bytes, *, validator: CloudProductPolicyValidator) -> AcceptedCloudProductPolicyRecord:
        return validator.accept(payload)

    @classmethod
    def from_document(cls, document: Mapping[str, Any], *, validator: CloudProductPolicyValidator) -> AcceptedCloudProductPolicyRecord:
        return validator.accept(_jcs_bytes(document))

    @classmethod
    def _accepted(cls, payload: bytes, document: JsonObject) -> AcceptedCloudProductPolicyRecord:
        schema_id = str(document["schema_id"])
        instance = cls.__new__(cls)
        object.__setattr__(instance, "schema_id", schema_id)
        object.__setattr__(instance, "schema_version", str(document["schema_version"]))
        object.__setattr__(instance, "record_id", str(document[_RECORD_ID_FIELDS[schema_id]]))
        object.__setattr__(instance, "digest", byte_digest(payload))
        object.__setattr__(instance, "semantic_digest", jcs_digest(document))
        object.__setattr__(instance, "_payload", bytes(payload))
        return instance

    def document(self) -> JsonObject:
        payload = bytes(self._payload)
        if byte_digest(payload) != self.digest:
            raise CloudProductPolicyContractError("accepted product-policy bytes changed")
        document = parse_strict_cloud_product_policy_json(payload)
        field_name = _RECORD_ID_FIELDS.get(self.schema_id)
        if (
            field_name is None
            or document.get("schema_id") != self.schema_id
            or document.get("schema_version") != self.schema_version
            or document.get(field_name) != self.record_id
            or jcs_digest(document) != self.semantic_digest
        ):
            raise CloudProductPolicyContractError("accepted product-policy identity changed")
        reject_cloud_product_policy_credentials(document)
        return document

    def to_bytes(self) -> bytes:
        self.document()
        return bytes(self._payload)


@dataclass(frozen=True, init=False)
class AccountPolicyAuthority:
    """Account authority bound to one accepted connected T030 scope."""

    account_id: str
    actor_type: str
    actor_id: str
    tenant_id: str
    capability_snapshot_id: str
    capability_snapshot_digest: str
    inference_descriptors: tuple[DescriptorBinding, ...]
    compute_descriptors: tuple[DescriptorBinding, ...]
    _accepted: bool = field(repr=False)

    @classmethod
    def from_connected(
        cls,
        *,
        account_id: str,
        session: AcceptedCloudClientRecord,
        capabilities: AcceptedCloudClientRecord,
        cloud_client_validator: CloudClientContractValidator,
        descriptor_resolver: DescriptorResolver,
    ) -> AccountPolicyAuthority:
        if not isinstance(account_id, str) or _STABLE_ID.fullmatch(account_id) is None:
            raise CloudProductPolicyScopeError("connected policy authority is unavailable")
        resolved_descriptors: dict[DescriptorIdentity, bytes] = {}

        def capture_descriptor(reference: ContractRecordReference) -> bytes:
            payload = descriptor_resolver(reference)
            identity = (reference.schema_id, reference.schema_version, reference.record_id, reference.digest)
            resolved_descriptors[identity] = bytes(payload)
            return payload

        try:
            session_document = cloud_client_validator.validate_session(session, capabilities)
            capability_document = cloud_client_validator.validate_capabilities(capabilities, capture_descriptor)
            inference_descriptors = _descriptor_bindings(capability_document["inference_routes"], resolved_descriptors)
            compute_descriptors = _descriptor_bindings(capability_document["compute_targets"], resolved_descriptors)
        except Exception as error:
            raise CloudProductPolicyScopeError("connected policy authority is unavailable") from error
        if session_document.get("state") != "connected":
            raise CloudProductPolicyScopeError("connected policy authority is unavailable")
        actor = session_document.get("actor")
        if not isinstance(actor, dict) or set(actor) != {"type", "id"}:
            raise CloudProductPolicyScopeError("connected policy authority is unavailable")
        instance = cls.__new__(cls)
        values = {
            "account_id": account_id,
            "actor_type": str(actor["type"]),
            "actor_id": str(actor["id"]),
            "tenant_id": str(session_document["tenant_id"]),
            "capability_snapshot_id": str(capability_document["snapshot_id"]),
            "capability_snapshot_digest": jcs_digest(capability_document),
            "inference_descriptors": inference_descriptors,
            "compute_descriptors": compute_descriptors,
            "_accepted": True,
        }
        for name, value in values.items():
            object.__setattr__(instance, name, value)
        return instance

    @property
    def scope_key(self) -> tuple[str, str, str, str]:
        if self._accepted is not True:
            raise CloudProductPolicyScopeError("connected policy authority is unavailable")
        return (self.account_id, self.actor_type, self.actor_id, self.tenant_id)


def _descriptor_bindings(
    references: list[JsonObject],
    resolved: Mapping[DescriptorIdentity, bytes],
) -> tuple[DescriptorBinding, ...]:
    bindings: list[DescriptorBinding] = []
    for item in references:
        identity: DescriptorIdentity = (item["schema_id"], item["schema_version"], item["record_id"], item["digest"])
        descriptor = parse_strict_json_object(resolved[identity])
        location = descriptor.get("location")
        if location not in {"local", "hosted"}:
            raise CloudProductPolicyContractError("capability descriptor location is unavailable")
        bindings.append((*identity, location))
    return tuple(sorted(bindings))


class DeterministicPolicyClock:
    """Small deterministic UTC clock used only by the in-memory conformance store."""

    def __init__(self, initial: str) -> None:
        self._lock = threading.RLock()
        self._value = _parse_time(initial, "initial policy clock")

    def now(self) -> str:
        with self._lock:
            return self._value.isoformat().replace("+00:00", "Z")

    def advance(self, *, seconds: int) -> str:
        if not isinstance(seconds, int) or isinstance(seconds, bool) or seconds < 0:
            raise CloudProductPolicyContractError("clock advance must be a non-negative integer")
        with self._lock:
            self._value += timedelta(seconds=seconds)
            return self.now()


def _parse_time(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise CloudProductPolicyContractError(f"{label} is not a timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise CloudProductPolicyContractError(f"{label} is not a timestamp") from error
    if parsed.tzinfo is None:
        raise CloudProductPolicyContractError(f"{label} is not offset-aware")
    return parsed.astimezone(UTC)


class InMemoryCloudProductPolicyStore:
    """Thread-safe shared exact-byte policy state for deterministic conformance."""

    def __init__(
        self,
        validator: CloudProductPolicyValidator,
        *,
        clock: DeterministicPolicyClock | None = None,
    ) -> None:
        self.validator = validator
        self.clock = clock or DeterministicPolicyClock("1970-01-01T00:00:00Z")
        self._lock = threading.RLock()
        self._snapshots: dict[tuple[str, str, str, str], AcceptedCloudProductPolicyRecord] = {}
        self._receipts: dict[tuple[str, str, str, str, str, str], _ReplayReceipt] = {}

    def install(self, authority: AccountPolicyAuthority, snapshot: AcceptedCloudProductPolicyRecord) -> None:
        document = _bound_snapshot(self.validator, authority, snapshot)
        if document["snapshot_revision"] != 0 or any(
            purpose["decision"]["state"] != "off" or purpose["decision"]["revision"] != 0 for purpose in document["optional_uses"].values()
        ):
            raise CloudProductPolicyContractError("initial account-policy snapshot must begin entirely off at revision zero")
        with self._lock:
            if authority.scope_key in self._snapshots:
                raise CloudProductPolicyScopeError("connected policy authority is unavailable")
            self._snapshots[authority.scope_key] = snapshot

    def restore_snapshot(self, authority: AccountPolicyAuthority, snapshot: AcceptedCloudProductPolicyRecord) -> None:
        """Restore validated snapshot state without inventing replay receipts."""

        _bound_snapshot(self.validator, authority, snapshot)
        with self._lock:
            if authority.scope_key in self._snapshots:
                raise CloudProductPolicyScopeError("connected policy authority is unavailable")
            self._snapshots[authority.scope_key] = snapshot

    def read(self, authority: AccountPolicyAuthority) -> AcceptedCloudProductPolicyRecord:
        with self._lock:
            snapshot = self._snapshots.get(authority.scope_key)
            if snapshot is None:
                raise CloudProductPolicyScopeError("connected policy authority is unavailable")
            _bound_snapshot(self.validator, authority, snapshot)
            return snapshot

    def change_optional_use(
        self,
        authority: AccountPolicyAuthority,
        change: AcceptedCloudProductPolicyRecord,
        *,
        idempotency_key: str,
    ) -> AcceptedCloudProductPolicyRecord:
        request = self.validator.accept(change.to_bytes()).document()
        if change.schema_id != "reasoning.cloud-product-policy.optional-use-change":
            raise CloudProductPolicyContractError("record is not an optional-use change")
        key_digest = digest_idempotency_key(idempotency_key)
        if request["idempotency_key_digest"] != key_digest:
            raise CloudProductPolicyContractError("idempotency key digest does not match the request")
        _validate_request_scope(request, authority)
        receipt_key = (*authority.scope_key, "optional-use-change", key_digest)

        with self._lock:
            receipt = self._receipts.get(receipt_key)
            if receipt is not None:
                if receipt.request_digest != request["request_digest"]:
                    raise CloudProductPolicyIdempotencyConflict("scoped idempotency key content changed")
                return receipt.response

            current = self._snapshots.get(authority.scope_key)
            if current is None:
                raise CloudProductPolicyScopeError("connected policy authority is unavailable")
            current_document = _bound_snapshot(self.validator, authority, current)
            _validate_request_against_current(request, current_document, self.clock.now())
            updated_document = _apply_optional_use_transition(request, current_document)
            updated = AcceptedCloudProductPolicyRecord.from_document(updated_document, validator=self.validator)
            self.validator.validate_account_policy(updated, authority=authority)
            receipt = _ReplayReceipt(request_digest=request["request_digest"], response=updated)
            self._snapshots[authority.scope_key] = updated
            self._receipts[receipt_key] = receipt
            return updated

    def restart(self) -> InMemoryCloudProductPolicyStore:
        """Return a reconstructed store retaining exact snapshot and replay bytes."""

        with self._lock:
            restarted = InMemoryCloudProductPolicyStore(self.validator, clock=self.clock)
            restarted._snapshots = dict(self._snapshots)
            restarted._receipts = dict(self._receipts)
            return restarted


@dataclass(frozen=True)
class _ReplayReceipt:
    request_digest: str
    response: AcceptedCloudProductPolicyRecord


def _validate_request_scope(request: JsonObject, authority: AccountPolicyAuthority) -> None:
    expected = request["expected"]
    if (
        request["account_id"] != authority.account_id
        or request["actor"] != {"type": authority.actor_type, "id": authority.actor_id}
        or request["tenant_id"] != authority.tenant_id
        or expected["capability_snapshot_id"] != authority.capability_snapshot_id
        or expected["capability_snapshot_digest"] != authority.capability_snapshot_digest
    ):
        raise CloudProductPolicyScopeError("connected policy authority is unavailable")


def _validate_request_against_current(request: JsonObject, current: JsonObject, clock_now: str) -> None:
    expected = request["expected"]
    capability = current["capability_snapshot"]
    entitlement = current["entitlement"]
    product_policy = current["product_policy"]
    purpose = current["optional_uses"][request["purpose"]]
    current_expected = {
        "snapshot_id": current["snapshot_id"],
        "snapshot_revision": current["snapshot_revision"],
        "snapshot_digest": current["snapshot_digest"],
        "capability_snapshot_id": capability["record_id"],
        "capability_snapshot_digest": capability["digest"],
        "entitlement_id": entitlement["id"],
        "entitlement_version": entitlement["version"],
        "entitlement_digest": entitlement["digest"],
        "product_policy_id": product_policy["policy_id"],
        "product_policy_version": product_policy["version"],
        "product_policy_digest": product_policy["digest"],
        "decision_revision": purpose["decision"]["revision"],
        "decision_digest": purpose["decision"]["digest"],
    }
    if expected != current_expected:
        raise CloudProductPolicyCasConflict("optional-use change bindings are stale")

    requested = _parse_time(request["requested_at"], "requested_at")
    if requested < _parse_time(current["observed_at"], "observed_at") or requested > _parse_time(clock_now, "policy clock"):
        raise CloudProductPolicyContractError("optional-use request time is outside the accepted chronology")
    selected_policy = purpose["policy"]
    if request["action"] == "grant":
        policy_effective, policy_expiry = _validate_policy_terms(selected_policy, "optional-use request policy")
        product_effective = _parse_time(product_policy["effective_at"], "product_policy.effective_at")
        product_expiry_value = product_policy["expires_at"]
        product_expiry = _parse_time(product_expiry_value, "product_policy.expires_at") if product_expiry_value is not None else None
        if (
            purpose["allowed_action"] != "grant"
            or purpose["availability"] != "available"
            or product_policy["state"] != "effective"
            or requested < policy_effective
            or (policy_expiry is not None and requested >= policy_expiry)
            or requested < product_effective
            or (product_expiry is not None and requested >= product_expiry)
            or request["consent_policy"] != selected_policy
        ):
            raise CloudProductPolicyTransitionError("optional-use grant is not legal under the exact current policy")
    else:
        history_policy = purpose["decision_history"][-1]["policy"]
        if purpose["decision"]["state"] != "granted" or request["consent_policy"] != history_policy:
            raise CloudProductPolicyTransitionError("optional-use withdrawal is not bound to the historical grant policy")
    if len(purpose["decision_history"]) >= 64:
        raise CloudProductPolicyHistoryLimitError("optional-use decision lineage is full")


def _apply_optional_use_transition(request: JsonObject, current: JsonObject) -> JsonObject:
    updated = copy.deepcopy(current)
    purpose = updated["optional_uses"][request["purpose"]]
    decision = purpose["decision"]
    state = "granted" if request["action"] == "grant" else "withdrawn"
    entry: JsonObject = {
        "revision": decision["revision"] + 1,
        "digest": "",
        "state": state,
        "action": request["action"],
        "decided_at": request["requested_at"],
        "policy_digest": request["consent_policy"]["digest"],
        "policy": copy.deepcopy(request["consent_policy"]),
    }
    entry["digest"] = canonical_decision_entry_digest(entry)
    purpose["decision_history"].append(entry)
    purpose["decision"] = {key: entry[key] for key in ("state", "revision", "digest", "decided_at", "policy_digest")}
    purpose["prior_data_disposition"] = "not_applicable" if state == "granted" else "retained_until_expiry"
    if state == "granted":
        purpose["allowed_action"] = "withdraw"
    else:
        product = updated["product_policy"]
        policy = purpose["policy"]
        observed = _parse_time(request["requested_at"], "requested_at")
        effective = _parse_time(policy["effective_at"], "optional policy effective_at")
        expires_value = policy["expires_at"]
        expires = _parse_time(expires_value, "optional policy expires_at") if expires_value is not None else None
        can_grant = (
            purpose["availability"] == "available"
            and product["state"] == "effective"
            and effective <= observed
            and (expires is None or observed < expires)
        )
        purpose["allowed_action"] = "grant" if can_grant else "none"
    updated["snapshot_revision"] += 1
    updated["observed_at"] = request["requested_at"]
    updated["snapshot_digest"] = canonical_policy_snapshot_digest(updated)
    return updated


def _bound_snapshot(
    validator: CloudProductPolicyValidator,
    authority: AccountPolicyAuthority,
    snapshot: AcceptedCloudProductPolicyRecord,
) -> JsonObject:
    if not isinstance(snapshot, AcceptedCloudProductPolicyRecord):
        raise CloudProductPolicyScopeError("connected policy authority is unavailable")
    try:
        return validator.validate_account_policy(snapshot, authority=authority)
    except CloudProductPolicyScopeError:
        raise
    except CloudProductPolicyContractError as error:
        raise CloudProductPolicyScopeError("connected policy authority is unavailable") from error


@runtime_checkable
class CloudProductPolicyClient(Protocol):
    """Transport-neutral public account-policy reads and optional-use changes."""

    def account_policy(self) -> AcceptedCloudProductPolicyRecord: ...

    def change_optional_use(
        self,
        change: AcceptedCloudProductPolicyRecord,
        *,
        idempotency_key: str,
    ) -> AcceptedCloudProductPolicyRecord: ...


class InMemoryCloudProductPolicyClient:
    """Reconstructable client over one separately bound shared policy store."""

    def __init__(self, *, store: InMemoryCloudProductPolicyStore, authority: AccountPolicyAuthority) -> None:
        if (
            not isinstance(store, InMemoryCloudProductPolicyStore)
            or not isinstance(authority, AccountPolicyAuthority)
            or getattr(authority, "_accepted", False) is not True
        ):
            raise CloudProductPolicyScopeError("connected policy authority is unavailable")
        self._store = store
        self._authority = authority

    def account_policy(self) -> AcceptedCloudProductPolicyRecord:
        return self._store.read(self._authority)

    def change_optional_use(
        self,
        change: AcceptedCloudProductPolicyRecord,
        *,
        idempotency_key: str,
    ) -> AcceptedCloudProductPolicyRecord:
        return self._store.change_optional_use(self._authority, change, idempotency_key=idempotency_key)


def digest_idempotency_key(key: str) -> str:
    """Return a one-way digest after validating the bounded public key syntax."""

    if not isinstance(key, str) or re.fullmatch(r"[A-Za-z0-9._:-]{16,255}", key) is None:
        raise CloudProductPolicyContractError("idempotency key must contain 16 to 255 safe characters")
    reject_cloud_product_policy_credentials(key)
    return f"sha256:{hashlib.sha256(key.encode('utf-8')).hexdigest()}"
