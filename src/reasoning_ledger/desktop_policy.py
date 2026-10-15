"""Private fail-closed Desktop presentation policy.

Canonical route, pricing, descriptor, and Cloud product-policy records remain
unchanged. This module projects the private fail-closed state used when Desktop
has no accepted connected account-policy snapshot.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any

from .cloud_client import require_credential_free
from .harnesses.desktop_hybrid import AcceptedContractRecord, HybridPlanningError

JsonObject = dict[str, Any]

_POLICY_ID = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]{2,127}$")
_SCHEMA_AXES = {
    "reasoning.platform.harness-provider": ("harness", "harness_id"),
    "reasoning.platform.inference-route": ("inference", "route_id"),
    "reasoning.platform.compute-target": ("compute", "target_id"),
}
_PLAN_REFERENCES = {
    "harness": ("harness_id", "descriptor_digest"),
    "inference": ("route_id", "route_digest"),
    "compute": ("target_id", "target_digest"),
}
_OPTIONAL_PURPOSES = ("product_analytics", "research_evaluation", "model_training")


class DesktopPolicyError(RuntimeError):
    """Desktop policy evidence is incomplete, inconsistent, or exhausted."""


def _decimal(value: object, label: str) -> Decimal:
    if not isinstance(value, str):
        raise DesktopPolicyError(f"{label} must be an exact decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise DesktopPolicyError(f"{label} is invalid") from error
    if not parsed.is_finite() or parsed < 0 or format(parsed, "f") != value:
        raise DesktopPolicyError(f"{label} is not canonical")
    return parsed


def _policy_id(value: object, label: str) -> str:
    if not isinstance(value, str) or _POLICY_ID.fullmatch(value) is None:
        raise DesktopPolicyError(f"{label} is invalid")
    require_credential_free(value)
    return value


def _accepted(record: AcceptedContractRecord) -> AcceptedContractRecord:
    if not isinstance(record, AcceptedContractRecord):
        raise DesktopPolicyError("Desktop policy requires an accepted canonical record")
    try:
        accepted = AcceptedContractRecord.from_bytes(record.to_bytes())
    except HybridPlanningError as error:
        raise DesktopPolicyError("Desktop policy record is not canonical") from error
    require_credential_free(accepted.document())
    return accepted


def _contract_identity(record: AcceptedContractRecord) -> JsonObject:
    return {
        "schema_id": record.schema_id,
        "schema_version": record.schema_version,
        "record_id": record.record_id,
        "digest": record.digest,
    }


@dataclass(frozen=True)
class DesktopPricePresentation:
    state: str
    currency: str | None = None
    minimum_amount: str | None = None
    maximum_amount: str | None = None
    source_version: str | None = None

    def __post_init__(self) -> None:
        if self.state not in {"estimated", "unknown", "unavailable"}:
            raise DesktopPolicyError("price presentation state is invalid")
        if self.state != "estimated":
            if any(value is not None for value in (self.currency, self.minimum_amount, self.maximum_amount, self.source_version)):
                raise DesktopPolicyError("unknown or unavailable price cannot contain an estimate")
            return
        if (
            self.currency not in {"USD"}
            or self.source_version is None
            or not self.source_version.startswith("test-")
            or self.minimum_amount is None
            or self.maximum_amount is None
            or _decimal(self.minimum_amount, "minimum price") > _decimal(self.maximum_amount, "maximum price")
        ):
            raise DesktopPolicyError("estimated price evidence is incomplete")
        require_credential_free(self.source_version)

    @classmethod
    def estimated(
        cls,
        *,
        minimum_amount: str,
        maximum_amount: str,
        source_version: str,
    ) -> DesktopPricePresentation:
        return cls(
            state="estimated",
            currency="USD",
            minimum_amount=minimum_amount,
            maximum_amount=maximum_amount,
            source_version=source_version,
        )

    @classmethod
    def unknown(cls) -> DesktopPricePresentation:
        return cls(state="unknown")

    @classmethod
    def unavailable(cls) -> DesktopPricePresentation:
        return cls(state="unavailable")

    def presentation(self) -> JsonObject:
        return {
            "state": self.state,
            "currency": self.currency,
            "minimum_amount": self.minimum_amount,
            "maximum_amount": self.maximum_amount,
            "source_version": self.source_version,
        }


@dataclass(frozen=True)
class DesktopDataPathPresentation:
    state: str
    location: str
    subprocessors: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.subprocessors, tuple):
            raise DesktopPolicyError("subprocessor path must be an immutable tuple")
        if self.state not in {"local", "planned_unverified", "unavailable"}:
            raise DesktopPolicyError("data-path state is invalid")
        _policy_id(self.location, "data location")
        if not self.subprocessors or len(self.subprocessors) > 8:
            raise DesktopPolicyError("subprocessor path must be explicit and bounded")
        for item in self.subprocessors:
            _policy_id(item, "subprocessor path item")
        if self.state == "local" and (self.location != "device_local" or self.subprocessors != ("none",)):
            raise DesktopPolicyError("local data path must remain on the device without a subprocessor")

    def presentation(self) -> JsonObject:
        return {"state": self.state, "location": self.location, "subprocessors": list(self.subprocessors)}


@dataclass(frozen=True)
class DesktopTelemetryPresentation:
    state: str
    purpose: str
    data_classes: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.data_classes, tuple):
            raise DesktopPolicyError("telemetry data classes must be an immutable tuple")
        if self.state not in {"local_only", "unavailable"}:
            raise DesktopPolicyError("required telemetry state is invalid for offline Desktop policy")
        _policy_id(self.purpose, "telemetry purpose")
        if not self.data_classes or len(self.data_classes) > 16:
            raise DesktopPolicyError("telemetry data classes are incomplete")
        for item in self.data_classes:
            _policy_id(item, "telemetry data class")
        allowed = {
            "local_only": ("local_operation_status", ("operation_metadata",)),
            "unavailable": ("cloud_operations_and_audit", ("allowlist_unapproved",)),
        }
        if (self.purpose, self.data_classes) != allowed[self.state]:
            raise DesktopPolicyError("offline required telemetry cannot include an unapproved field or purpose")

    def presentation(self) -> JsonObject:
        return {"state": self.state, "purpose": self.purpose, "data_classes": list(self.data_classes)}


@dataclass(frozen=True)
class DesktopRoutePolicyEvidence:
    available: bool
    reason: str | None
    price: DesktopPricePresentation
    hard_limits: Mapping[str, str]
    data_path: DesktopDataPathPresentation
    required_telemetry: DesktopTelemetryPresentation

    def __post_init__(self) -> None:
        if not isinstance(self.available, bool) or not isinstance(self.hard_limits, Mapping):
            raise DesktopPolicyError("route hard limits are invalid")
        if (
            not isinstance(self.price, DesktopPricePresentation)
            or not isinstance(self.data_path, DesktopDataPathPresentation)
            or not isinstance(self.required_telemetry, DesktopTelemetryPresentation)
        ):
            raise DesktopPolicyError("route policy evidence has an unsupported shape")
        if self.available != (self.reason is None):
            raise DesktopPolicyError("route availability and reason disagree")
        if self.reason is not None:
            _policy_id(self.reason, "route unavailability reason")
        if len(self.hard_limits) > 32:
            raise DesktopPolicyError("route hard limits are unbounded")
        for key, value in self.hard_limits.items():
            _policy_id(key, "hard-limit meter")
            _decimal(value, "hard-limit value")
        if self.available and (not self.hard_limits or self.price.state != "estimated"):
            raise DesktopPolicyError("available route requires an estimate and hard limits")
        object.__setattr__(self, "hard_limits", MappingProxyType(dict(self.hard_limits)))

    @classmethod
    def local(cls, *, hard_limits: Mapping[str, str]) -> DesktopRoutePolicyEvidence:
        return cls(
            available=True,
            reason=None,
            price=DesktopPricePresentation.estimated(
                minimum_amount="0",
                maximum_amount="0",
                source_version="test-local-zero-charge",
            ),
            hard_limits=dict(hard_limits),
            data_path=DesktopDataPathPresentation(state="local", location="device_local", subprocessors=("none",)),
            required_telemetry=DesktopTelemetryPresentation(
                state="local_only",
                purpose="local_operation_status",
                data_classes=("operation_metadata",),
            ),
        )

    @classmethod
    def remote_disabled(
        cls,
        *,
        reason: str = "activation_evidence_absent",
        price_state: str = "unknown",
    ) -> DesktopRoutePolicyEvidence:
        if price_state not in {"unknown", "unavailable"}:
            raise DesktopPolicyError("disabled remote price must be unknown or unavailable")
        price = DesktopPricePresentation.unknown() if price_state == "unknown" else DesktopPricePresentation.unavailable()
        return cls(
            available=False,
            reason=reason,
            price=price,
            hard_limits={},
            data_path=DesktopDataPathPresentation(
                state="unavailable",
                location="unverified",
                subprocessors=("unconfigured",),
            ),
            required_telemetry=DesktopTelemetryPresentation(
                state="unavailable",
                purpose="cloud_operations_and_audit",
                data_classes=("allowlist_unapproved",),
            ),
        )


@dataclass(frozen=True)
class DesktopRoutePolicy:
    descriptor: AcceptedContractRecord
    evidence: DesktopRoutePolicyEvidence


class DesktopPolicyPresenter:
    """Validate and render private Desktop activation evidence fail-closed."""

    def __init__(
        self,
        routes: tuple[DesktopRoutePolicy, ...],
        *,
        route_pricing_pairs: tuple[tuple[str, str], ...],
    ) -> None:
        if not routes or len(routes) > 128:
            raise DesktopPolicyError("Desktop route policy set is empty or unbounded")
        self._routes: dict[tuple[str, str], tuple[AcceptedContractRecord, DesktopRoutePolicyEvidence]] = {}
        for route in routes:
            if not isinstance(route, DesktopRoutePolicy) or not isinstance(route.evidence, DesktopRoutePolicyEvidence):
                raise DesktopPolicyError("Desktop route policy entry is invalid")
            descriptor = _accepted(route.descriptor)
            try:
                axis, identity_key = _SCHEMA_AXES[descriptor.schema_id]
            except KeyError as error:
                raise DesktopPolicyError("Desktop route policy descriptor has an unsupported schema") from error
            document = descriptor.document()
            identity = _policy_id(document.get(identity_key), f"{axis} identity")
            location = document.get("location")
            if location not in {"local", "hosted"}:
                raise DesktopPolicyError("Desktop route policy descriptor location is invalid")
            if location == "hosted" and route.evidence.available:
                raise DesktopPolicyError("offline Desktop policy cannot activate a hosted route")
            if route.evidence.available and route.evidence.data_path.state != "local":
                raise DesktopPolicyError("offline available route requires a local data path")
            if location == "hosted" and route.evidence.data_path.state == "local":
                raise DesktopPolicyError("hosted descriptor cannot claim a local data path")
            key = (axis, identity)
            if key in self._routes:
                raise DesktopPolicyError("Desktop route policy contains a duplicate identity")
            self._routes[key] = (descriptor, route.evidence)
        pairs = set(route_pricing_pairs)
        allowed = {
            ("1.0.0-alpha.1", "1.0.0-alpha.1"),
            ("1.0.0-alpha.2", "1.0.0-alpha.2"),
        }
        if not pairs or len(pairs) != len(route_pricing_pairs) or not pairs.issubset(allowed):
            raise DesktopPolicyError("Desktop route/pricing compatibility set is invalid")
        self._pairs = frozenset(pairs)
        self._consents = tuple(
            {"purpose": purpose, "enabled": False, "state": "off", "policy_version": None} for purpose in _OPTIONAL_PURPOSES
        )

    def presentation(self) -> JsonObject:
        routes: list[JsonObject] = []
        for (axis, identity), (descriptor, evidence) in sorted(self._routes.items()):
            document = descriptor.document()
            routes.append(
                {
                    "axis": axis,
                    "identity": identity,
                    "display_name": document["display_name"],
                    "location": document["location"],
                    "capability": _contract_identity(descriptor),
                    "availability": {
                        "available": evidence.available,
                        "reason": evidence.reason,
                    },
                    "price": evidence.price.presentation(),
                    "hard_limits": {
                        "state": "configured" if evidence.hard_limits else "unavailable",
                        "values": dict(sorted(evidence.hard_limits.items())),
                    },
                    "data_path": evidence.data_path.presentation(),
                    "required_telemetry": evidence.required_telemetry.presentation(),
                }
            )
        return {
            "scope": "private_desktop_presentation",
            "disabled_remote_paths": [
                "aws_fargate",
                "aws_ec2_research",
                "modal",
                "hosted_models",
                "consent_gated_use",
                "spend_gated_dispatch",
            ],
            "route_pricing_pairs": [{"route_plan": plan, "pricing": pricing} for plan, pricing in sorted(self._pairs)],
            "routes": routes,
            "optional_data_use": copy.deepcopy(list(self._consents)),
        }

    def present_route(
        self,
        plan: AcceptedContractRecord,
        pricing: AcceptedContractRecord,
        *,
        requested_limits: Mapping[str, str],
    ) -> JsonObject:
        accepted_plan = _accepted(plan)
        accepted_pricing = _accepted(pricing)
        if accepted_plan.schema_id != "reasoning.platform.route-plan" or accepted_pricing.schema_id != "reasoning.platform.pricing":
            raise DesktopPolicyError("Desktop policy selection requires route-plan and pricing records")
        pair = (accepted_plan.schema_version, accepted_pricing.schema_version)
        if pair not in self._pairs:
            raise DesktopPolicyError("route and pricing versions were not jointly advertised")
        plan_document = accepted_plan.document()
        pricing_document = accepted_pricing.document()
        declared_pricing_version = plan_document.get("pricing_contract_version", accepted_plan.schema_version)
        pricing_operation = pricing_document.get("operation")
        persistence = plan_document.get("persistence")
        if (
            not isinstance(pricing_operation, dict)
            or not isinstance(persistence, dict)
            or declared_pricing_version != accepted_pricing.schema_version
            or plan_document["pricing_lifecycle_id"] != pricing_document["pricing_lifecycle_id"]
            or pricing_operation.get("route_plan_id") != plan_document["route_plan_id"]
            or pricing_operation.get("operation_id") != plan_document["operation_id"]
            or pricing_operation.get("actor") != plan_document["actor"]
            or pricing_operation.get("tenant_id") != plan_document["tenant_id"]
            or pricing_operation.get("project_id") != persistence.get("project_id")
        ):
            raise DesktopPolicyError("route and pricing identity binding changed")
        if not isinstance(requested_limits, Mapping) or len(requested_limits) > 32:
            raise DesktopPolicyError("requested route limits are invalid")
        requested = {
            _policy_id(key, "requested-limit meter"): _decimal(value, "requested limit") for key, value in requested_limits.items()
        }
        selected: list[JsonObject] = []
        reasons: set[str] = set()
        price_states: set[str] = set()
        bounded_meters: set[str] = set()
        minimum_amount = Decimal(0)
        maximum_amount = Decimal(0)
        for axis in ("harness", "inference", "compute"):
            reference = plan_document[axis]
            identity_key, digest_key = _PLAN_REFERENCES[axis]
            identity = reference[identity_key]
            try:
                descriptor, evidence = self._routes[(axis, identity)]
            except KeyError as error:
                raise DesktopPolicyError(f"selected {axis} capability is unavailable") from error
            if reference[digest_key] != descriptor.digest or reference["location"] != descriptor.document()["location"]:
                raise DesktopPolicyError(f"selected {axis} capability identity changed")
            axis_reasons: list[str] = []
            if not evidence.available:
                axis_reasons.append(str(evidence.reason))
            price_states.add(evidence.price.state)
            if evidence.price.state == "estimated":
                minimum_amount += _decimal(evidence.price.minimum_amount, "minimum price")
                maximum_amount += _decimal(evidence.price.maximum_amount, "maximum price")
            bounded_meters.update(evidence.hard_limits)
            for meter, requested_value in requested.items():
                if meter in evidence.hard_limits and requested_value > _decimal(evidence.hard_limits[meter], "hard limit"):
                    axis_reasons.append(f"hard_limit_exhausted:{meter}")
            reasons.update(axis_reasons)
            selected.append(
                {
                    "axis": axis,
                    "identity": identity,
                    "location": reference["location"],
                    "capability": _contract_identity(descriptor),
                    "available": not axis_reasons,
                    "reasons": axis_reasons,
                    "price": evidence.price.presentation(),
                    "hard_limits": {
                        "state": "configured" if evidence.hard_limits else "unavailable",
                        "values": dict(sorted(evidence.hard_limits.items())),
                    },
                    "data_path": evidence.data_path.presentation(),
                    "required_telemetry": evidence.required_telemetry.presentation(),
                }
            )
        reasons.update(f"hard_limit_unavailable:{meter}" for meter in requested if meter not in bounded_meters)
        quote = pricing_document.get("quote")
        price: JsonObject
        if reasons or "unavailable" in price_states:
            price = DesktopPricePresentation.unavailable().presentation()
        elif "unknown" in price_states or not isinstance(quote, dict):
            price = DesktopPricePresentation.unknown().presentation()
        elif _decimal(quote.get("maximum_amount"), "canonical maximum price") != maximum_amount:
            reasons.add("price_estimate_mismatch")
            price = DesktopPricePresentation.unavailable().presentation()
        else:
            price = DesktopPricePresentation.estimated(
                minimum_amount=format(minimum_amount, "f"),
                maximum_amount=format(maximum_amount, "f"),
                source_version="test-composed-route-policy",
            ).presentation()
            price["contract_digest"] = accepted_pricing.digest
        return {
            "available": not reasons,
            "reasons": sorted(reasons),
            "contracts": {
                "route_plan": _contract_identity(accepted_plan),
                "pricing": _contract_identity(accepted_pricing),
            },
            "placements": {
                "inference": plan_document["inference"]["location"],
                "compute": plan_document["compute"]["location"],
            },
            "selected": selected,
            "price": price,
            "optional_data_use": copy.deepcopy(list(self._consents)),
        }


__all__ = [
    "DesktopDataPathPresentation",
    "DesktopPolicyError",
    "DesktopPolicyPresenter",
    "DesktopPricePresentation",
    "DesktopRoutePolicy",
    "DesktopRoutePolicyEvidence",
    "DesktopTelemetryPresentation",
]
