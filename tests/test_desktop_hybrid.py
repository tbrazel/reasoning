# Ruff 0.3.4 and 0.15.x classify the src-layout package differently.
# ruff: noqa: I001
from __future__ import annotations

import copy
import hashlib
import itertools
import json
import os
import shutil
import sqlite3
import sys
import threading
from dataclasses import fields, replace
from datetime import datetime
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from reasoning_ledger.harnesses.desktop_hybrid import (
    MAX_TRANSFER_BYTES,
    MAX_TRANSFER_INPUTS,
    AcceptedContractRecord,
    ApprovedProjectTransfer,
    DesktopCapabilityRequirements,
    DesktopHybridPlanner,
    DesktopHybridPlannerFactory,
    DesktopHybridRoute,
    DesktopTransferReceiptStore,
    HybridPlanningError,
    ProjectContentSelection,
    ProjectTransferApproval,
    ProjectTransferConsentError,
    RetainedCloudData,
    UnsupportedHybridRoute,
    approve_project_transfer,
    connect_account,
)
from reasoning_ledger.harnesses.scoped_connector import (
    ConnectorCredentialLeak,
    ConnectorGrantRejected,
    ConnectorReconnectError,
    ConnectorReplayRejected,
    ConnectorRequest,
    ConnectorTransportDisconnected,
    InMemoryGrantUseStore,
    InMemoryReplayStore,
    InMemoryRevocationStore,
    ScopedComputeConnector,
)

ROOT = Path(__file__).resolve().parents[1]
PLATFORM_FIXTURES = ROOT / "conformance" / "platform" / "v1alpha1" / "fixtures" / "positive"
PLATFORM_SCHEMAS = ROOT / "contracts" / "platform" / "v1alpha1"
PLATFORM_ALPHA2_FIXTURES = ROOT / "conformance" / "platform" / "v1alpha2" / "fixtures" / "positive"
EXECUTOR_SCHEMAS = ROOT / "contracts" / "executor" / "v1alpha1"
DESKTOP_FIXTURE = ROOT / "tests" / "fixtures" / "desktop-hybrid" / "fake-cloud-execution.json"
MATRIX_CODES = ("lll", "llh", "lhl", "lhh", "hll", "hlh", "hhl", "hhh")
GRANT_POLICY_KEYS = (
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
BANNED_CREDENTIAL_KEYS = {
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
EXPECTED_RUNTIME_READINESS = {
    "status": "ready",
    "route_plan_versions": ["1.0.0-alpha.1", "1.0.0-alpha.2"],
    "pricing_versions": ["1.0.0-alpha.1", "1.0.0-alpha.2"],
    "bundles": [
        {
            "schema_version": "1.0.0-alpha.1",
            "bundle_id": "reasoning-platform-v1alpha1",
            "bundle_digest": "sha256:1c5e3221b21ffb6ecce27407c561b5d7ff2d3647bc435ce986c713c903252eb8",
        },
        {
            "schema_version": "1.0.0-alpha.2",
            "bundle_id": "reasoning-platform-v1alpha2",
            "bundle_digest": "sha256:f0988632e10c92095b4de66bc89d193b8931b62a67bedbf562e32794a2175daf",
        },
    ],
}


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _record(path: Path) -> AcceptedContractRecord:
    return AcceptedContractRecord.from_bytes(path.read_bytes())


def _copy_frozen_platform_assets(root: Path) -> Path:
    for version in ("v1alpha1", "v1alpha2"):
        source = ROOT / "contracts" / "platform" / version
        destination = root / "contracts" / "platform" / version
        destination.mkdir(parents=True)
        for path in source.glob("*.json"):
            shutil.copyfile(path, destination / path.name)
    return root


def _pricing_for_plan(plan: AcceptedContractRecord) -> AcceptedContractRecord:
    """Build a schema-valid alpha.1 lifecycle bound to one matrix route in memory."""

    value = _load(PLATFORM_FIXTURES / "pricing.hybrid-settled.json")
    route = plan.document()
    operation = value["operation"]
    operation.update(
        {
            "route_plan_id": route["route_plan_id"],
            "operation_id": route["operation_id"],
            "actor": route["actor"],
            "tenant_id": route["tenant_id"],
            "project_id": route["persistence"]["project_id"],
        }
    )
    inference = next(item for item in value["usage"] if item["kind"] == "inference")["provenance"]
    inference.update(
        {
            "route_id": route["inference"]["route_id"],
            "provider": route["inference"]["provider"],
            "model": route["inference"]["model"],
        }
    )
    compute = next(item for item in value["usage"] if item["kind"] == "compute")["provenance"]
    compute.update(
        {
            "target_id": route["compute"]["target_id"],
            "image_digest": route["compute"]["image"]["digest"],
            "capabilities_digest": route["compute"]["image"]["capabilities_digest"],
            "attestation_digest": route["compute"]["image"]["attestation_digest"],
        }
    )
    value["settlement"]["usage_digest"] = _jcs_digest(value["usage"])
    return AcceptedContractRecord.from_document(value)


def _alpha2_pair(name: str) -> tuple[AcceptedContractRecord, AcceptedContractRecord]:
    return (
        _record(PLATFORM_ALPHA2_FIXTURES / f"route-plan.{name}.json"),
        _record(PLATFORM_ALPHA2_FIXTURES / f"pricing.{name}.json"),
    )


def _jcs_digest(value) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _make_planner(receipt_store: DesktopTransferReceiptStore) -> DesktopHybridPlanner:
    return DesktopHybridPlanner(
        harnesses=tuple(_record(path) for path in sorted(PLATFORM_FIXTURES.glob("harness-provider.*.json"))),
        inference_routes=tuple(_record(path) for path in sorted(PLATFORM_FIXTURES.glob("inference-route.*.json"))),
        compute_targets=tuple(_record(path) for path in sorted(PLATFORM_FIXTURES.glob("compute-target.*.json"))),
        transfer_receipts=receipt_store,
    )


def test_desktop_runtime_factory_uses_verified_source_contracts(
    tmp_path: Path,
) -> None:
    factory = DesktopHybridPlannerFactory()
    store = DesktopTransferReceiptStore(tmp_path / "source-runtime-receipts.sqlite3")
    planner = factory.create(
        harnesses=tuple(_record(path) for path in sorted(PLATFORM_FIXTURES.glob("harness-provider.*.json"))),
        inference_routes=tuple(_record(path) for path in sorted(PLATFORM_FIXTURES.glob("inference-route.*.json"))),
        compute_targets=tuple(_record(path) for path in sorted(PLATFORM_FIXTURES.glob("compute-target.*.json"))),
        transfer_receipts=store,
    )
    plan = _record(PLATFORM_FIXTURES / "route-plan.lll.json")
    bundle = _record(PLATFORM_FIXTURES / "project-bundle.local-unlinked.json")

    assert factory.readiness() == EXPECTED_RUNTIME_READINESS
    assert planner.resolve_plan(plan, _pricing_for_plan(plan), bundle).combination == "lll"
    store.close()


def test_desktop_runtime_factory_uses_exact_frozen_contracts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frozen_root = _copy_frozen_platform_assets(tmp_path / "frozen")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(frozen_root), raising=False)

    factory = DesktopHybridPlannerFactory()
    alpha1 = _record(PLATFORM_FIXTURES / "route-plan.lll.json")
    alpha2, pricing = _alpha2_pair("compute-expired-pre-dispatch")

    assert factory.readiness() == EXPECTED_RUNTIME_READINESS
    assert alpha1.schema_version == "1.0.0-alpha.1"
    assert (alpha2.schema_version, pricing.schema_version) == (
        "1.0.0-alpha.2",
        "1.0.0-alpha.2",
    )


def test_desktop_runtime_factory_never_falls_back_without_frozen_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)

    with pytest.raises(HybridPlanningError, match="frozen Desktop platform contract root is unavailable"):
        DesktopHybridPlannerFactory()


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ("absent", "asset set"),
        ("altered", "asset digest"),
        ("unexpected", "asset set"),
        ("duplicate-id", "duplicated"),
        ("cross-version", "wrong ID"),
        ("bundle-digest", "bundle identity"),
    ),
)
def test_desktop_runtime_factory_rejects_invalid_frozen_contracts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
    message: str,
) -> None:
    frozen_root = _copy_frozen_platform_assets(tmp_path / mutation)
    alpha1 = frozen_root / "contracts" / "platform" / "v1alpha1"
    if mutation == "absent":
        (alpha1 / "pricing.schema.json").unlink()
    elif mutation == "altered":
        schema = _load(alpha1 / "pricing.schema.json")
        schema["title"] = "Altered pricing schema"
        (alpha1 / "pricing.schema.json").write_text(json.dumps(schema), encoding="utf-8")
    elif mutation == "unexpected":
        (alpha1 / "unexpected.json").write_text("{}", encoding="utf-8")
    elif mutation == "duplicate-id":
        schema = _load(alpha1 / "route-plan.schema.json")
        schema["$id"] = _load(alpha1 / "common.schema.json")["$id"]
        (alpha1 / "route-plan.schema.json").write_text(json.dumps(schema), encoding="utf-8")
    elif mutation == "cross-version":
        shutil.copyfile(
            frozen_root / "contracts" / "platform" / "v1alpha2" / "pricing.schema.json",
            alpha1 / "pricing.schema.json",
        )
    elif mutation == "bundle-digest":
        manifest = _load(alpha1 / "bundle-manifest.json")
        manifest["bundle_digest"] = f"sha256:{'0' * 64}"
        (alpha1 / "bundle-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    else:  # pragma: no cover - the parameter table is closed above
        raise AssertionError(mutation)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(frozen_root), raising=False)

    with pytest.raises(HybridPlanningError, match=message):
        DesktopHybridPlannerFactory()


def _keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _keys(child)


@pytest.fixture(scope="module")
def project_bundle(tmp_path_factory: pytest.TempPathFactory) -> AcceptedContractRecord:
    canonical_value = _load(PLATFORM_FIXTURES / "project-bundle.local-unlinked.json")
    transfer_inputs = canonical_value["transfer_manifests"][0]["disclosure"]["inputs"]
    for content, transfer_input in zip(canonical_value["contents"], transfer_inputs, strict=True):
        content["digest"] = transfer_input["digest"]
    canonical = AcceptedContractRecord.from_document(canonical_value)
    source = _unconsented_bundle(canonical)
    route = _record(PLATFORM_FIXTURES / "route-plan.lhh.json")
    pricing = _pricing_for_plan(route)
    state = tmp_path_factory.mktemp("t015-approved-bundle")
    store = DesktopTransferReceiptStore(state / "receipts.sqlite3")
    planner = _make_planner(store)
    approved = approve_project_transfer(planner, source, route, pricing, _approval(source, route))
    attached = approved.attach(source, route, receipt_store=store)
    store.close()
    return attached


@pytest.fixture(scope="module")
def route_plan() -> AcceptedContractRecord:
    return _record(PLATFORM_FIXTURES / "route-plan.lhh.json")


@pytest.fixture(scope="module")
def pricing_lifecycle(route_plan: AcceptedContractRecord) -> AcceptedContractRecord:
    return _pricing_for_plan(route_plan)


@pytest.fixture()
def planner(tmp_path: Path, project_bundle: AcceptedContractRecord) -> DesktopHybridPlanner:
    receipt_store = DesktopTransferReceiptStore(tmp_path / "transfer-receipts.sqlite3")
    planner = _make_planner(receipt_store)
    source_bundle = _unconsented_bundle(project_bundle)
    for code in MATRIX_CODES:
        plan = _record(PLATFORM_FIXTURES / f"route-plan.{code}.json")
        if plan.document()["persistence"].get("transfer_manifest_id") is not None:
            approved = approve_project_transfer(
                planner,
                source_bundle,
                plan,
                _pricing_for_plan(plan),
                _approval(source_bundle, plan),
            )
            attached = approved.attach(source_bundle, plan, receipt_store=receipt_store)
            assert attached.document() == project_bundle.document()
    return planner


@pytest.fixture(scope="module")
def contract_validator():
    schemas = {}
    registry = Registry()
    for directory in (PLATFORM_SCHEMAS, EXECUTOR_SCHEMAS):
        for path in sorted(directory.glob("*.schema.json")):
            schema = _load(path)
            schemas[path.name] = schema
            registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
    by_label = {
        "local-harness-connector": "local-harness-connector.schema.json",
        "job-spec": "job-spec.schema.json",
        "job-status": "job-status.schema.json",
        "job-event": "job-event.schema.json",
    }

    def validate(label: str, value: dict) -> None:
        Draft202012Validator(
            schemas[by_label[label]],
            registry=registry,
            format_checker=FormatChecker(),
        ).validate(value)

    return validate


@pytest.fixture()
def execution_fixture() -> dict:
    return _load(DESKTOP_FIXTURE)


class FakeExecutorTransport:
    def __init__(self, fixture: dict) -> None:
        self.fixture = copy.deepcopy(fixture)
        self.calls: list[dict] = []
        self.afters: list[str | None] = []
        self.submit_count = 0
        self.disconnect_next = False
        self.repeat_last = False
        self.mutate_replayed_event = False
        self.stall_cursor = False
        self.replay_after_new = False
        self.event_after_terminal = False
        self.regress_event_state = False
        self.conflicting_terminal = False
        self.events_entered: threading.Event | None = None
        self.events_release: threading.Event | None = None
        self.status_override: dict | None = None

    def submit_job(self, call: dict, job_spec: dict) -> dict:
        self.calls.append(copy.deepcopy(call))
        self.submit_count += 1
        assert job_spec == self.fixture["job"]
        return copy.deepcopy(self.fixture["accepted_status"])

    def get_job(self, call: dict, job_id: str) -> dict:
        self.calls.append(copy.deepcopy(call))
        return copy.deepcopy(self.status_override or self.fixture["terminal_status"])

    def list_job_events(self, call: dict, job_id: str, *, after: str | None, limit: int) -> dict:
        self.calls.append(copy.deepcopy(call))
        self.afters.append(after)
        if self.disconnect_next:
            self.disconnect_next = False
            raise ConnectorTransportDisconnected("fixture disconnect")
        if self.events_entered is not None and self.events_release is not None:
            self.events_entered.set()
            if not self.events_release.wait(timeout=5):
                raise AssertionError("fixture event barrier was not released")
        events = self.fixture["events"]
        if after == "opaque:hybrid:001":
            return {
                "events": copy.deepcopy(events[1:3]),
                "next_cursor": "opaque:hybrid:003",
                "complete": False,
            }
        if after == "opaque:hybrid:003":
            if self.regress_event_state:
                regressed = copy.deepcopy(events[0])
                regressed.update(
                    {
                        "event_id": "event_hybrid_regressed_004",
                        "sequence": 4,
                        "cursor": "opaque:hybrid:regressed:004",
                    }
                )
                returned = [regressed]
            elif self.replay_after_new:
                returned = copy.deepcopy([events[3], events[2], events[4]])
            else:
                returned = copy.deepcopy(events[2:] if self.repeat_last else events[3:])
            if self.mutate_replayed_event:
                returned[0]["payload"] = {"runner_version": "substituted"}
            if self.conflicting_terminal:
                returned[-1]["type"] = "job.failed"
                returned[-1]["state"] = "failed"
                returned[-1]["payload"] = {"fixture": "conflicting terminal outcome"}
            return {
                "events": returned,
                "next_cursor": ("opaque:hybrid:003" if self.stall_cursor else "opaque:hybrid:005"),
                "complete": not self.stall_cursor,
            }
        if after == "opaque:hybrid:005":
            if self.event_after_terminal:
                event = copy.deepcopy(events[2])
                event.update(
                    {
                        "event_id": "event_hybrid_006",
                        "sequence": 6,
                        "cursor": "opaque:hybrid:006",
                    }
                )
                return {
                    "events": [event],
                    "next_cursor": "opaque:hybrid:006",
                    "complete": True,
                }
            return {"events": [], "next_cursor": None, "complete": True}
        raise AssertionError(f"unexpected exclusive cursor: {after!r}")

    def download_artifact(self, call: dict, artifact_id: str) -> tuple[bytes, str]:
        self.calls.append(copy.deepcopy(call))
        assert artifact_id == "artifact_hybrid_result_001"
        return (
            self.fixture["artifact_text"].encode(),
            "sha256:9349619e4beed54d8895cfa0a69ba13d23437d6c57ccbfaaaadc2b7e2659e850",
        )

    def cancel_job(self, call: dict, job_id: str, *, idempotency_key: str) -> dict:
        self.calls.append(copy.deepcopy(call))
        value = copy.deepcopy(self.fixture["accepted_status"])
        value["cancellation_requested"] = True
        return value


def _clock(value: str = "2026-09-01T18:05:30Z"):
    instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return lambda: instant


def _request(number: int, *, audience: str = "https://executor.example.org/v1/jobs", key: str | None = None):
    return ConnectorRequest(
        audience=audience,
        nonce=f"fixture-nonce-{number:08d}",
        idempotency_key=key or f"fixture-request-{number:08d}",
    )


def _connector(
    planner: DesktopHybridPlanner,
    project_bundle: AcceptedContractRecord,
    route_plan: AcceptedContractRecord,
    execution_fixture: dict,
    contract_validator,
    *,
    clock=None,
    revocations=None,
    grant_uses=None,
    replay_store=None,
):
    pricing = _pricing_for_plan(route_plan)
    route = planner.resolve_plan(route_plan, pricing, project_bundle)
    transport = FakeExecutorTransport(execution_fixture)
    connector = ScopedComputeConnector(
        grant=_record(PLATFORM_FIXTURES / "local-harness-connector.json"),
        planner=planner,
        route=route,
        transport=transport,
        validator=contract_validator,
        clock=clock or _clock(),
        revocations=revocations,
        grant_uses=grant_uses,
        replay_store=replay_store,
    )
    return connector, transport, route


def _unconsented_bundle(project_bundle: AcceptedContractRecord) -> AcceptedContractRecord:
    value = project_bundle.document()
    value["transfer_manifests"] = []
    return AcceptedContractRecord.from_document(value)


def _approval(bundle: AcceptedContractRecord, plan: AcceptedContractRecord) -> ProjectTransferApproval:
    value = bundle.document()
    project = value["project"]
    return ProjectTransferApproval(
        route_plan_digest=plan.digest,
        project_bundle_digest=bundle.digest,
        project_id=project["project_id"],
        project_revision_digest=project["revision_digest"],
        transfer_id=plan.document()["persistence"]["transfer_manifest_id"],
        approval_id="approval_transfer_001",
        actor_type="user",
        actor_id="user_alice",
        approved_at="2026-09-01T18:01:00Z",
        selections=(
            ProjectContentSelection("README.md", "artifact_problem_statement", "inference_context"),
            ProjectContentSelection(
                "inputs/cyclic5.json",
                "artifact_polynomial_system",
                "compute_input",
            ),
        ),
        retained_cloud_data=(
            RetainedCloudData("outputs", "account_policy", "Return the requested result."),
            RetainedCloudData("logs", "operation_lifetime", "Diagnose this durable operation."),
            RetainedCloudData(
                "operation_metadata",
                "indefinite_audit",
                "Preserve idempotency and reconciliation.",
            ),
            RetainedCloudData("usage", "indefinite_audit", "Settle the accepted quote."),
            RetainedCloudData("provenance", "indefinite_audit", "Attribute the actual route and image."),
        ),
    )


def test_planner_displays_all_eight_independent_t007_combinations(planner: DesktopHybridPlanner, project_bundle: AcceptedContractRecord):
    plans = tuple(_record(PLATFORM_FIXTURES / f"route-plan.{code}.json") for code in MATRIX_CODES)
    pairs = tuple((plan, _pricing_for_plan(plan)) for plan in plans)

    resolved = planner.resolve_matrix(pairs, project_bundle)

    assert [route.combination for route in resolved] == sorted(MATRIX_CODES)
    assert {
        (
            route.harness_location,
            route.inference_location,
            route.compute_location,
        )
        for route in resolved
    } == set(itertools.product(("local", "hosted"), repeat=3))
    assert {route.project_location for route in resolved} == {"local"}
    assert sum(route.transfer_required for route in resolved) == 7
    assert {route.combination for route in resolved if route.connector_required} == {
        "llh",
        "lhh",
    }
    assert all(BANNED_CREDENTIAL_KEYS.isdisjoint(_keys(route.display())) for route in resolved)


def test_planner_negotiates_exact_alpha1_and_alpha2_route_pricing_pairs(
    planner: DesktopHybridPlanner,
    project_bundle: AcceptedContractRecord,
):
    alpha1_plan = _record(PLATFORM_FIXTURES / "route-plan.lll.json")
    alpha1_pricing = _pricing_for_plan(alpha1_plan)
    alpha1 = planner.resolve_plan(alpha1_plan, alpha1_pricing, project_bundle)
    assert alpha1.route_plan_version == "1.0.0-alpha.1"
    assert alpha1.pricing_contract_version == "1.0.0-alpha.1"
    assert alpha1.pricing_lifecycle_digest == alpha1_pricing.digest

    source = _unconsented_bundle(project_bundle)
    alpha2_plan, alpha2_pricing = _alpha2_pair("hybrid-settled")
    approved = approve_project_transfer(
        planner,
        source,
        alpha2_plan,
        alpha2_pricing,
        _approval(source, alpha2_plan),
    )
    attached = approved.attach(source, alpha2_plan, receipt_store=planner.transfer_receipts)
    alpha2 = planner.resolve_plan(alpha2_plan, alpha2_pricing, attached)

    assert alpha2.route_plan_version == "1.0.0-alpha.2"
    assert alpha2.pricing_contract_version == "1.0.0-alpha.2"
    assert alpha2.pricing_lifecycle_id == alpha2_pricing.record_id
    assert alpha2.pricing_lifecycle_digest == alpha2_pricing.digest
    assert alpha2.dispatch_permitted is True
    assert alpha2.display()["contracts"] == {
        "route_plan": "1.0.0-alpha.2",
        "pricing": "1.0.0-alpha.2",
        "executor": "1.0.0-alpha.1",
    }


def test_planner_rejects_mixed_unknown_and_substituted_pricing_pairs(
    planner: DesktopHybridPlanner,
    project_bundle: AcceptedContractRecord,
):
    alpha1_plan = _record(PLATFORM_FIXTURES / "route-plan.lll.json")
    alpha1_pricing = _pricing_for_plan(alpha1_plan)
    alpha2_plan, alpha2_pricing = _alpha2_pair("hybrid-cancelled-pre-dispatch")
    source = _unconsented_bundle(project_bundle)

    with pytest.raises(UnsupportedHybridRoute, match="exact pair"):
        planner.resolve_plan(alpha1_plan, alpha2_pricing, source)
    with pytest.raises(UnsupportedHybridRoute, match="exact pair"):
        planner.resolve_plan(alpha2_plan, alpha1_pricing, source)

    unknown = alpha2_plan.document()
    unknown["schema_version"] = "1.0.0-alpha.3"
    with pytest.raises(HybridPlanningError, match="unsupported platform contract pair"):
        AcceptedContractRecord.from_document(unknown)

    substitutions = {
        "pricing lifecycle": ("pricing_lifecycle_id", "pricing_substituted"),
        "pricing route-plan": ("route_plan_id", "route_plan_substituted"),
        "pricing operation": ("operation_id", "operation_substituted"),
        "pricing actor": ("actor", {"type": "user", "id": "user_substituted"}),
        "pricing tenant": ("tenant_id", "tenant_substituted"),
        "pricing project": ("project_id", "project_substituted"),
    }
    for message, (key, replacement) in substitutions.items():
        value = alpha1_pricing.document()
        target = value if key == "pricing_lifecycle_id" else value["operation"]
        target[key] = replacement
        with pytest.raises(UnsupportedHybridRoute, match=message):
            planner.resolve_plan(alpha1_plan, AcceptedContractRecord.from_document(value), source)


@pytest.mark.parametrize(
    ("name", "reason"),
    (
        ("compute-expired-pre-dispatch", "quote_expired"),
        ("hybrid-cancelled-pre-dispatch", "cancelled_before_dispatch"),
    ),
)
def test_alpha2_pre_dispatch_release_cannot_authorize_transfer_or_connector(
    planner: DesktopHybridPlanner,
    project_bundle: AcceptedContractRecord,
    execution_fixture: dict,
    contract_validator,
    name: str,
    reason: str,
):
    plan, pricing = _alpha2_pair(name)
    before_authorizations = dict(planner._route_authorizations)
    route = planner.resolve_plan(plan, pricing, _unconsented_bundle(project_bundle))

    assert route.pricing_state == "released"
    assert route.pricing_release_phase == "pre_dispatch"
    assert route.pricing_release_reason == reason
    assert route.dispatch_permitted is False
    assert route.transfer_required is False
    assert route.connector_required is False
    assert planner._route_authorizations == before_authorizations
    with pytest.raises(HybridPlanningError, match="authorization is unknown"):
        planner.reauthorize_connector_route(route)
    with pytest.raises(ProjectTransferConsentError, match="cannot authorize a project transfer"):
        planner.prepare_transfer_route(plan, pricing, _unconsented_bundle(project_bundle))

    transport = FakeExecutorTransport(execution_fixture)
    replay = InMemoryReplayStore()
    grant_uses = InMemoryGrantUseStore()
    with pytest.raises(ConnectorGrantRejected, match="forbids connector construction"):
        ScopedComputeConnector(
            grant=_record(PLATFORM_FIXTURES / "local-harness-connector.json"),
            planner=planner,
            route=route,
            transport=transport,
            validator=contract_validator,
            clock=_clock(),
            replay_store=replay,
            grant_uses=grant_uses,
        )
    assert transport.calls == []
    assert replay._expires == {}
    assert grant_uses._bindings == {}


@pytest.mark.parametrize(
    ("name", "state", "release_phase"),
    (
        ("compute-released-post-dispatch", "released", "post_dispatch"),
        ("hybrid-outcome-unknown", "outcome_unknown", None),
    ),
)
def test_alpha2_dispatched_pricing_states_preserve_execution_evidence(
    planner: DesktopHybridPlanner,
    project_bundle: AcceptedContractRecord,
    name: str,
    state: str,
    release_phase: str | None,
):
    plan, pricing = _alpha2_pair(name)
    route = planner.resolve_plan(plan, pricing, _unconsented_bundle(project_bundle))

    assert route.pricing_state == state
    assert route.pricing_release_phase == release_phase
    assert route.dispatch_permitted is True
    assert route.route_plan_version == "1.0.0-alpha.2"
    assert route.pricing_contract_version == "1.0.0-alpha.2"


def test_alpha2_pricing_release_semantics_fail_closed_before_authorization(
    planner: DesktopHybridPlanner,
    project_bundle: AcceptedContractRecord,
):
    plan, pricing = _alpha2_pair("hybrid-cancelled-pre-dispatch")
    source = _unconsented_bundle(project_bundle)
    before_authorizations = dict(planner._route_authorizations)
    value = pricing.document()
    value["settlement"]["released_amount"] = "0.999999"
    with pytest.raises(UnsupportedHybridRoute, match="completely and atomically release"):
        planner.resolve_plan(plan, AcceptedContractRecord.from_document(value), source)
    assert planner._route_authorizations == before_authorizations

    value = pricing.document()
    value["settlement"]["released_at"] = value["quote"]["expires_at"]
    with pytest.raises(UnsupportedHybridRoute, match="did not occur before quote expiry"):
        planner.resolve_plan(plan, AcceptedContractRecord.from_document(value), source)
    assert planner._route_authorizations == before_authorizations

    post_dispatch_plan, post_dispatch_pricing = _alpha2_pair("compute-released-post-dispatch")
    value = post_dispatch_pricing.document()
    value["settlement"]["settled_at"] = value["reservation"]["reserved_at"]
    with pytest.raises(UnsupportedHybridRoute, match="release precedes dispatch"):
        planner.resolve_plan(post_dispatch_plan, AcceptedContractRecord.from_document(value), source)
    assert planner._route_authorizations == before_authorizations


def test_matrix_negotiation_does_not_leak_partial_authorizations(
    planner: DesktopHybridPlanner,
    project_bundle: AcceptedContractRecord,
):
    plans = tuple(_record(PLATFORM_FIXTURES / f"route-plan.{code}.json") for code in MATRIX_CODES)
    pairs = tuple((plan, _pricing_for_plan(plan)) for plan in plans)
    invalid = (*pairs[:-1], (pairs[-1][0], _alpha2_pair("hybrid-cancelled-pre-dispatch")[1]))
    before = dict(planner._route_authorizations)

    with pytest.raises(UnsupportedHybridRoute, match="exact pair"):
        planner.resolve_matrix(invalid, project_bundle)
    assert planner._route_authorizations == before

    resolved = planner.resolve_matrix(pairs, project_bundle)
    assert len(resolved) == 8
    assert len(planner._route_authorizations) == len(before) + 8


def test_planner_uses_advertised_capabilities_and_rejects_unsupported_combinations(
    planner: DesktopHybridPlanner, project_bundle: AcceptedContractRecord
):
    local_plan = _record(PLATFORM_FIXTURES / "route-plan.lhh.json")
    route = planner.resolve_plan(
        local_plan,
        _pricing_for_plan(local_plan),
        project_bundle,
        requirements=DesktopCapabilityRequirements(
            harness_features=("mcp", "subagents", "tools"),
            require_resume=True,
            require_reconnect=True,
        ),
    )
    assert route.harness_id == "codex"

    hosted_plan = _record(PLATFORM_FIXTURES / "route-plan.hhh.json")
    with pytest.raises(UnsupportedHybridRoute, match="required feature: mcp"):
        planner.resolve_plan(
            hosted_plan,
            _pricing_for_plan(hosted_plan),
            project_bundle,
            requirements=DesktopCapabilityRequirements(harness_features=("mcp",)),
        )

    substituted = local_plan.document()
    substituted["inference"]["model"] = "local/not-a-hosted-model"
    with pytest.raises(UnsupportedHybridRoute, match="outside the accepted inference patterns"):
        substituted_record = AcceptedContractRecord.from_document(substituted)
        planner.resolve_plan(substituted_record, _pricing_for_plan(substituted_record), project_bundle)


def test_planner_rejects_descriptor_drift_and_incomplete_matrix(planner: DesktopHybridPlanner, project_bundle: AcceptedContractRecord):
    plan = _load(PLATFORM_FIXTURES / "route-plan.lll.json")
    plan["harness"]["descriptor_digest"] = "sha256:" + "0" * 64
    with pytest.raises(UnsupportedHybridRoute, match="harness descriptor digest"):
        changed = AcceptedContractRecord.from_document(plan)
        planner.resolve_plan(changed, _pricing_for_plan(changed), project_bundle)

    plans = tuple(_record(PLATFORM_FIXTURES / f"route-plan.{code}.json") for code in MATRIX_CODES[:-1])
    with pytest.raises(UnsupportedHybridRoute, match="exactly one accepted plan"):
        planner.resolve_matrix(tuple((item, _pricing_for_plan(item)) for item in plans), project_bundle)


def test_connecting_account_preserves_local_project_and_emits_no_transfer(
    project_bundle: AcceptedContractRecord,
):
    bundle = _unconsented_bundle(project_bundle)
    before = bundle.document()

    connection = connect_account(bundle, actor_type="user", actor_id="user_alice", tenant_id="tenant_alpha")

    assert bundle.document() == before
    assert connection.project_location == "local"
    assert connection.linkage_state == "unlinked"
    assert connection.existing_transfer_manifest_count == 0
    assert connection.new_transfer_manifest is None
    assert connection.to_dict()["new_transfer_manifest"] is None
    assert BANNED_CREDENTIAL_KEYS.isdisjoint(_keys(connection.to_dict()))


def test_explicit_project_approval_generates_an_exact_bounded_route_receipt(
    planner: DesktopHybridPlanner,
    project_bundle: AcceptedContractRecord,
    route_plan: AcceptedContractRecord,
    contract_validator,
):
    bundle = _unconsented_bundle(project_bundle)
    pricing = _pricing_for_plan(route_plan)
    with pytest.raises(ProjectTransferConsentError, match="exact approved transfer"):
        planner.resolve_plan(route_plan, pricing, bundle)

    approved = approve_project_transfer(planner, bundle, route_plan, pricing, _approval(bundle, route_plan))
    manifest = approved.manifest()
    attached = approved.attach(bundle, route_plan, receipt_store=planner.transfer_receipts)
    contract_validator_bundle = _load(PLATFORM_SCHEMAS / "project-bundle.schema.json")
    platform_registry = Registry()
    for path in PLATFORM_SCHEMAS.glob("*.schema.json"):
        schema = _load(path)
        platform_registry = platform_registry.with_resource(schema["$id"], Resource.from_contents(schema))
    Draft202012Validator(
        contract_validator_bundle,
        registry=platform_registry,
        format_checker=FormatChecker(),
    ).validate(attached.document())
    route = planner.resolve_plan(route_plan, pricing, attached)

    assert approved.route_plan_digest == route_plan.digest
    assert manifest["mode"] == "one_step"
    assert manifest["future_transfers_authorized"] is False
    assert manifest["disclosure_digest"] == _jcs_digest(manifest["disclosure"])
    assert manifest["approval"]["approved_disclosure_digest"] == manifest["disclosure_digest"]
    assert sum(item[2] for item in route.transfer_inputs) == 1308
    assert len(route.transfer_inputs) == 2
    assert manifest["disclosure"]["omitted_data_classes"] == [
        "unlisted_project_files",
        "local_harness_state",
        "authentication_material",
        "local_paths",
    ]
    assert BANNED_CREDENTIAL_KEYS.isdisjoint(_keys(manifest))
    assert "route_plan_digest" not in manifest


def test_approved_transfer_cannot_be_directly_constructed_or_replaced(
    planner: DesktopHybridPlanner,
    project_bundle: AcceptedContractRecord,
    route_plan: AcceptedContractRecord,
):
    bundle = _unconsented_bundle(project_bundle)
    pricing = _pricing_for_plan(route_plan)
    approved = approve_project_transfer(planner, bundle, route_plan, pricing, _approval(bundle, route_plan))

    with pytest.raises(TypeError):
        ApprovedProjectTransfer(
            route_plan_digest=approved.route_plan_digest,
            project_bundle_digest=approved.project_bundle_digest,
            transfer_id=approved.transfer_id,
            _manifest_payload=approved._manifest_payload,
        )
    with pytest.raises(TypeError):
        replace(approved, route_plan_digest="sha256:" + "0" * 64)
    copied = copy.copy(approved)
    object.__setattr__(copied, "transfer_id", "transfer_forged_001")
    with pytest.raises(ProjectTransferConsentError, match="explicit validated approval"):
        copied.attach(bundle, route_plan, receipt_store=planner.transfer_receipts)
    with pytest.raises(ProjectTransferConsentError, match="exact approved transfer"):
        planner.resolve_plan(route_plan, pricing, bundle)


def test_approved_transfer_is_durable_and_rederived_from_explicit_approval(
    planner: DesktopHybridPlanner,
    project_bundle: AcceptedContractRecord,
    route_plan: AcceptedContractRecord,
):
    bundle = _unconsented_bundle(project_bundle)
    approval = _approval(bundle, route_plan)
    approved = approve_project_transfer(planner, bundle, route_plan, _pricing_for_plan(route_plan), approval)
    restarted = ApprovedProjectTransfer.from_bytes(approved.to_bytes())
    assert restarted.manifest() == approved.manifest()
    assert restarted.attach(bundle, route_plan, receipt_store=planner.transfer_receipts).document()["transfer_manifests"]

    altered_approval = replace(approval, selections=approval.selections[:1])
    reconstructed = ApprovedProjectTransfer._issue(
        route_plan_digest=approved.route_plan_digest,
        project_bundle_digest=approved.project_bundle_digest,
        transfer_id=approved.transfer_id,
        manifest_payload=approved._manifest_payload,
        approval_payload=altered_approval.to_bytes(),
    )
    with pytest.raises(ProjectTransferConsentError, match="does not rederive exactly"):
        reconstructed.attach(bundle, route_plan, receipt_store=planner.transfer_receipts)


def test_transfer_consent_cannot_be_transplanted_or_exceed_bounds(
    planner: DesktopHybridPlanner,
    project_bundle: AcceptedContractRecord,
    route_plan: AcceptedContractRecord,
):
    bundle = _unconsented_bundle(project_bundle)
    pricing = _pricing_for_plan(route_plan)
    approved = approve_project_transfer(planner, bundle, route_plan, pricing, _approval(bundle, route_plan))
    other_route = _record(PLATFORM_FIXTURES / "route-plan.llh.json")
    with pytest.raises(ProjectTransferConsentError, match="another route plan"):
        approved.attach(bundle, other_route, receipt_store=planner.transfer_receipts)

    too_many = _approval(bundle, route_plan)
    too_many = ProjectTransferApproval(
        **{
            **too_many.__dict__,
            "selections": tuple(
                ProjectContentSelection("README.md", f"artifact_many_{index:03d}", "both") for index in range(MAX_TRANSFER_INPUTS + 1)
            ),
        }
    )
    with pytest.raises(ProjectTransferConsentError, match="select 1-32"):
        approve_project_transfer(planner, bundle, route_plan, pricing, too_many)

    oversized_value = bundle.document()
    oversized_value["contents"][0]["size_bytes"] = MAX_TRANSFER_BYTES + 1
    oversized = AcceptedContractRecord.from_document(oversized_value)
    oversized_approval = _approval(oversized, route_plan)
    with pytest.raises(ProjectTransferConsentError, match="exceeds"):
        approve_project_transfer(planner, oversized, route_plan, pricing, oversized_approval)


def test_transfer_receipt_survives_restart_and_fails_closed_on_loss_or_route_transplant(
    tmp_path: Path,
    project_bundle: AcceptedContractRecord,
    route_plan: AcceptedContractRecord,
):
    bundle = _unconsented_bundle(project_bundle)
    receipt_path = tmp_path / "desktop-state" / "transfer-receipts.sqlite3"
    receipt_path.parent.mkdir(mode=0o700)
    authoring_store = DesktopTransferReceiptStore(receipt_path)
    authoring_planner = _make_planner(authoring_store)
    pricing = _pricing_for_plan(route_plan)
    approved = approve_project_transfer(authoring_planner, bundle, route_plan, pricing, _approval(bundle, route_plan))
    attached = approved.attach(bundle, route_plan, receipt_store=authoring_store)

    concurrent_store = DesktopTransferReceiptStore(receipt_path)
    concurrent = _make_planner(concurrent_store)
    assert concurrent.resolve_plan(route_plan, pricing, attached).route_plan_id == "route_plan_lhh"
    concurrent_store.close()
    authoring_store.close()

    serialized = json.dumps(attached.document(), indent=2).encode()
    reloaded = AcceptedContractRecord.from_bytes(serialized)
    restarted = _make_planner(DesktopTransferReceiptStore(receipt_path))
    assert restarted.resolve_plan(route_plan, pricing, reloaded).route_plan_id == "route_plan_lhh"

    transplanted = _record(PLATFORM_FIXTURES / "route-plan.llh.json")
    with pytest.raises(ProjectTransferConsentError, match="missing or ambiguous"):
        restarted.resolve_plan(transplanted, _pricing_for_plan(transplanted), reloaded)

    empty_parent = tmp_path / "empty-state"
    empty_parent.mkdir(mode=0o700)
    empty_store = DesktopTransferReceiptStore(empty_parent / "transfer-receipts.sqlite3")
    with pytest.raises(ProjectTransferConsentError, match="persisted Desktop transfer receipt"):
        _make_planner(empty_store).resolve_plan(route_plan, pricing, reloaded)


def test_receipt_store_rejects_symlinks_sidecars_and_post_init_replacement(tmp_path: Path):
    state = tmp_path / "receipt-state"
    state.mkdir(mode=0o700)
    real_parent = tmp_path / "real-receipt-parent"
    real_parent.mkdir(mode=0o700)
    linked_parent = tmp_path / "linked-receipt-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    with pytest.raises(ProjectTransferConsentError, match="canonical and private"):
        DesktopTransferReceiptStore(linked_parent / "receipts.sqlite3")

    victim = tmp_path / "victim.sqlite3"
    victim.write_bytes(b"unchanged")
    victim.chmod(0o600)
    linked = state / "linked.sqlite3"
    linked.symlink_to(victim)
    with pytest.raises(ProjectTransferConsentError, match="database path is unsafe"):
        DesktopTransferReceiptStore(linked)
    assert victim.read_bytes() == b"unchanged"

    sidecar_path = state / "sidecar.sqlite3-journal"
    sidecar_path.write_bytes(b"preseeded")
    sidecar_path.chmod(0o600)
    with pytest.raises(ProjectTransferConsentError, match="sidecar"):
        DesktopTransferReceiptStore(state / "sidecar.sqlite3")

    public_database = state / "public.sqlite3"
    public_database.write_bytes(b"")
    public_database.chmod(0o644)
    with pytest.raises(ProjectTransferConsentError, match="private owned file"):
        DesktopTransferReceiptStore(public_database)

    database_path = state / "stable.sqlite3"
    store = DesktopTransferReceiptStore(database_path)
    assert database_path.stat().st_mode & 0o777 == 0o600
    assert not tuple(state.glob(".stable.sqlite3.*.open"))
    assert not any((state / (database_path.name + suffix)).exists() for suffix in ("-journal", "-wal", "-shm"))
    original_path = state / "stable-original.sqlite3"
    os.replace(database_path, original_path)
    database_path.symlink_to(original_path.name)
    with pytest.raises(ProjectTransferConsentError, match="database identity changed"):
        store.receipts_for_bundle("sha256:" + "0" * 64)
    store.close()
    database_path.unlink()
    os.replace(original_path, database_path)


def test_receipt_store_detects_parent_and_database_swap_during_operation(tmp_path: Path):
    state = tmp_path / "race-state"
    state.mkdir(mode=0o700)
    database_path = state / "receipts.sqlite3"
    store = DesktopTransferReceiptStore(database_path)
    entered = threading.Event()
    release = threading.Event()
    original_check = store._assert_storage_binding
    calls = 0

    def barrier_check() -> None:
        nonlocal calls
        calls += 1
        original_check()
        if calls == 1:
            entered.set()
            assert release.wait(timeout=5)

    store._assert_storage_binding = barrier_check
    failures: list[BaseException] = []

    def read_receipts() -> None:
        try:
            store.receipts_for_bundle("sha256:" + "0" * 64)
        except BaseException as error:
            failures.append(error)

    thread = threading.Thread(target=read_receipts)
    thread.start()
    assert entered.wait(timeout=5)
    moved = tmp_path / "race-state-original"
    state.rename(moved)
    state.symlink_to(moved.name, target_is_directory=True)
    release.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert len(failures) == 1
    assert isinstance(failures[0], ProjectTransferConsentError)
    assert "parent identity changed" in str(failures[0])
    store.close()
    state.unlink()
    moved.rename(state)


def test_receipt_store_opens_sqlite_only_in_memory_and_persists_by_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    opened: list[str] = []
    original_connect = sqlite3.connect

    def observe_connect(database, *args, **kwargs):
        opened.append(str(database))
        return original_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", observe_connect)
    store = DesktopTransferReceiptStore(tmp_path / "descriptor-receipts.sqlite3")
    assert opened == [":memory:"]
    assert store.path.stat().st_size > 0
    store.close()


def test_fake_cloud_fixture_is_contract_valid_and_uses_high_capability_local_harness(execution_fixture: dict, contract_validator):
    contract_validator("job-spec", execution_fixture["job"])
    contract_validator("job-status", execution_fixture["accepted_status"])
    contract_validator("job-status", execution_fixture["terminal_status"])
    for event in execution_fixture["events"]:
        contract_validator("job-event", event)
    assert execution_fixture["accepted_status"]["spec_digest"] == _jcs_digest(execution_fixture["job"])
    harness = _load(ROOT / execution_fixture["harness_fixture"])
    assert harness["location"] == "local"
    assert {feature for feature, supported in harness["features"].items() if supported is True}.issuperset(
        {"tools", "skills", "mcp", "approvals", "subagents", "steering"}
    )


def test_connector_rejects_audience_route_expiry_and_immediate_revocation(
    planner: DesktopHybridPlanner,
    project_bundle: AcceptedContractRecord,
    route_plan: AcceptedContractRecord,
    execution_fixture: dict,
    contract_validator,
):
    connector, transport, _route = _connector(planner, project_bundle, route_plan, execution_fixture, contract_validator)
    with pytest.raises(ConnectorGrantRejected, match="audience mismatch"):
        connector.submit(
            execution_fixture["job"],
            _request(
                1,
                audience="https://substitute.example/v1/jobs",
                key=execution_fixture["job"]["idempotency_key"],
            ),
        )
    assert transport.submit_count == 0

    expired, expired_transport, _ = _connector(
        planner,
        project_bundle,
        route_plan,
        execution_fixture,
        contract_validator,
        clock=_clock("2026-09-01T18:14:00Z"),
    )
    with pytest.raises(ConnectorGrantRejected, match="expired"):
        expired.submit(
            execution_fixture["job"],
            _request(2, key=execution_fixture["job"]["idempotency_key"]),
        )
    assert expired_transport.submit_count == 0

    revocations = InMemoryRevocationStore()
    revoked, revoked_transport, _ = _connector(
        planner,
        project_bundle,
        route_plan,
        execution_fixture,
        contract_validator,
        revocations=revocations,
    )
    revocations.revoke(revoked.grant_id)
    with pytest.raises(ConnectorGrantRejected, match="revoked"):
        revoked.submit(
            execution_fixture["job"],
            _request(3, key=execution_fixture["job"]["idempotency_key"]),
        )
    assert revoked_transport.submit_count == 0


def test_connector_rejects_route_transplant_and_policy_substitution(
    planner: DesktopHybridPlanner,
    project_bundle: AcceptedContractRecord,
    execution_fixture: dict,
    contract_validator,
):
    llh = _record(PLATFORM_FIXTURES / "route-plan.llh.json")
    llh_route = planner.resolve_plan(llh, _pricing_for_plan(llh), project_bundle)
    with pytest.raises(ConnectorGrantRejected, match="exact route"):
        ScopedComputeConnector(
            grant=_record(PLATFORM_FIXTURES / "local-harness-connector.json"),
            planner=planner,
            route=llh_route,
            transport=FakeExecutorTransport(execution_fixture),
            validator=contract_validator,
            clock=_clock(),
        )

    lhh = _record(PLATFORM_FIXTURES / "route-plan.lhh.json")
    lhh_route = planner.resolve_plan(lhh, _pricing_for_plan(lhh), project_bundle)
    grant = _load(PLATFORM_FIXTURES / "local-harness-connector.json")
    grant["capability_set"]["inference_access"] = True
    grant["grant_policy_digest"] = _jcs_digest({key: grant[key] for key in GRANT_POLICY_KEYS})
    grant["approval"]["approved_grant_policy_digest"] = grant["grant_policy_digest"]
    with pytest.raises(HybridPlanningError, match="failed canonical schema validation"):
        ScopedComputeConnector(
            grant=AcceptedContractRecord.from_document(grant),
            planner=planner,
            route=lhh_route,
            transport=FakeExecutorTransport(execution_fixture),
            validator=contract_validator,
            clock=_clock(),
        )


def test_connector_rejects_direct_or_copied_route_image_substitution(
    planner: DesktopHybridPlanner,
    project_bundle: AcceptedContractRecord,
    route_plan: AcceptedContractRecord,
    execution_fixture: dict,
    contract_validator,
):
    pricing = _pricing_for_plan(route_plan)
    route = planner.resolve_plan(route_plan, pricing, project_bundle)
    public_values = {item.name: getattr(route, item.name) for item in fields(route) if not item.name.startswith("_")}
    with pytest.raises(TypeError):
        DesktopHybridRoute(**public_values)
    with pytest.raises(TypeError):
        replace(route, image_digest="sha256:" + "0" * 64)

    forged = copy.copy(route)
    object.__setattr__(forged, "image_digest", "sha256:" + "0" * 64)
    object.__setattr__(forged, "_claims_digest", _jcs_digest(forged._authenticity_claims()))
    with pytest.raises(ConnectorGrantRejected, match="exact planner-issued"):
        ScopedComputeConnector(
            grant=_record(PLATFORM_FIXTURES / "local-harness-connector.json"),
            planner=planner,
            route=forged,
            transport=FakeExecutorTransport(execution_fixture),
            validator=contract_validator,
            clock=_clock(),
        )

    forged_pricing = copy.copy(route)
    object.__setattr__(forged_pricing, "pricing_lifecycle_digest", "sha256:" + "0" * 64)
    object.__setattr__(forged_pricing, "_claims_digest", _jcs_digest(forged_pricing._authenticity_claims()))
    with pytest.raises(ConnectorGrantRejected, match="exact planner-issued"):
        ScopedComputeConnector(
            grant=_record(PLATFORM_FIXTURES / "local-harness-connector.json"),
            planner=planner,
            route=forged_pricing,
            transport=FakeExecutorTransport(execution_fixture),
            validator=contract_validator,
            clock=_clock(),
        )

    pricing_document = pricing.document()
    pricing_document["settlement"]["actual_amount"] = "0.000001"
    object.__setattr__(pricing, "_payload", json.dumps(pricing_document).encode())
    with pytest.raises(ConnectorGrantRejected, match="reauthorized from accepted records"):
        ScopedComputeConnector(
            grant=_record(PLATFORM_FIXTURES / "local-harness-connector.json"),
            planner=planner,
            route=route,
            transport=FakeExecutorTransport(execution_fixture),
            validator=contract_validator,
            clock=_clock(),
        )

    changed_plan = _record(PLATFORM_FIXTURES / "route-plan.lhh.json")
    changed_route = planner.resolve_plan(changed_plan, _pricing_for_plan(changed_plan), project_bundle)
    changed_document = changed_plan.document()
    changed_document["compute"]["image"]["digest"] = "sha256:" + "0" * 64
    object.__setattr__(changed_plan, "_payload", json.dumps(changed_document).encode())
    with pytest.raises(ConnectorGrantRejected, match="reauthorized from accepted records"):
        ScopedComputeConnector(
            grant=_record(PLATFORM_FIXTURES / "local-harness-connector.json"),
            planner=planner,
            route=changed_route,
            transport=FakeExecutorTransport(execution_fixture),
            validator=contract_validator,
            clock=_clock(),
        )


def test_connector_rejects_replay_second_job_and_reusable_credentials(
    planner: DesktopHybridPlanner,
    project_bundle: AcceptedContractRecord,
    route_plan: AcceptedContractRecord,
    execution_fixture: dict,
    contract_validator,
):
    connector, transport, _route = _connector(planner, project_bundle, route_plan, execution_fixture, contract_validator)
    request = _request(10, key=execution_fixture["job"]["idempotency_key"])
    connector.submit(execution_fixture["job"], request)
    with pytest.raises(ConnectorReplayRejected, match="nonce"):
        connector.submit(execution_fixture["job"], request)

    second = copy.deepcopy(execution_fixture["job"])
    second["job_id"] = "job_hybrid_cloud_002"
    second["idempotency_key"] = "hybrid-submit-000000000002"
    with pytest.raises(ConnectorReplayRejected, match="one-job"):
        connector.submit(
            second,
            _request(11, key=second["idempotency_key"]),
        )
    assert transport.submit_count == 1

    leaking_connector, leaking_transport, _ = _connector(planner, project_bundle, route_plan, execution_fixture, contract_validator)
    leaking = copy.deepcopy(execution_fixture["job"])
    leaking["authorization"] = "Bearer reusable-account-material"
    with pytest.raises(ConnectorCredentialLeak, match="authorization"):
        leaking_connector.submit(
            leaking,
            _request(12, key=leaking["idempotency_key"]),
        )
    assert leaking_transport.submit_count == 0


def test_connector_one_job_binding_is_atomic_across_connector_instances(
    planner: DesktopHybridPlanner,
    project_bundle: AcceptedContractRecord,
    route_plan: AcceptedContractRecord,
    execution_fixture: dict,
    contract_validator,
):
    grant_uses = InMemoryGrantUseStore()
    replay_store = InMemoryReplayStore()
    first, first_transport, _ = _connector(
        planner,
        project_bundle,
        route_plan,
        execution_fixture,
        contract_validator,
        grant_uses=grant_uses,
        replay_store=replay_store,
    )
    second, second_transport, _ = _connector(
        planner,
        project_bundle,
        route_plan,
        execution_fixture,
        contract_validator,
        grant_uses=grant_uses,
        replay_store=replay_store,
    )
    first.submit(
        execution_fixture["job"],
        _request(13, key=execution_fixture["job"]["idempotency_key"]),
    )
    with pytest.raises(ConnectorReplayRejected, match="nonce"):
        second.submit(
            execution_fixture["job"],
            _request(13, key=execution_fixture["job"]["idempotency_key"]),
        )
    substituted = copy.deepcopy(execution_fixture["job"])
    substituted["job_id"] = "job_hybrid_cloud_002"
    substituted["idempotency_key"] = "hybrid-submit-000000000002"

    with pytest.raises(ConnectorReplayRejected, match="one-job"):
        second.submit(substituted, _request(14, key=substituted["idempotency_key"]))

    assert first_transport.submit_count == 1
    assert second_transport.submit_count == 0


def test_connector_rejects_literal_secret_status_substitution_and_cursorless_acceptance(
    planner: DesktopHybridPlanner,
    project_bundle: AcceptedContractRecord,
    route_plan: AcceptedContractRecord,
    execution_fixture: dict,
    contract_validator,
):
    leaking, leaking_transport, _ = _connector(
        planner,
        project_bundle,
        route_plan,
        execution_fixture,
        contract_validator,
    )
    job_with_literal = copy.deepcopy(execution_fixture["job"])
    job_with_literal["command"]["argv"].append("api_key=fixture-secret-value")
    with pytest.raises(ConnectorCredentialLeak, match="credential-like"):
        leaking.submit(
            job_with_literal,
            _request(15, key=job_with_literal["idempotency_key"]),
        )
    assert leaking_transport.submit_count == 0

    incomplete, incomplete_transport, _ = _connector(
        planner,
        project_bundle,
        route_plan,
        execution_fixture,
        contract_validator,
    )
    job_without_input = copy.deepcopy(execution_fixture["job"])
    job_without_input["inputs"] = []
    with pytest.raises(ConnectorGrantRejected, match="exact approved compute transfer"):
        incomplete.submit(
            job_without_input,
            _request(151, key=job_without_input["idempotency_key"]),
        )
    assert incomplete_transport.submit_count == 0

    substituted, substituted_transport, _ = _connector(
        planner,
        project_bundle,
        route_plan,
        execution_fixture,
        contract_validator,
    )
    substituted_transport.fixture["accepted_status"]["spec_digest"] = "sha256:" + "0" * 64
    with pytest.raises(ConnectorReconnectError, match="specification substitution"):
        substituted.submit(
            execution_fixture["job"],
            _request(16, key=execution_fixture["job"]["idempotency_key"]),
        )

    cursorless, cursorless_transport, _ = _connector(
        planner,
        project_bundle,
        route_plan,
        execution_fixture,
        contract_validator,
    )
    cursorless_transport.fixture["accepted_status"]["last_event_cursor"] = None
    with pytest.raises(ConnectorReconnectError, match="initial event cursor"):
        cursorless.submit(
            execution_fixture["job"],
            _request(17, key=execution_fixture["job"]["idempotency_key"]),
        )


def test_fake_cloud_operation_survives_disconnect_and_resumes_exclusively(
    planner: DesktopHybridPlanner,
    project_bundle: AcceptedContractRecord,
    route_plan: AcceptedContractRecord,
    execution_fixture: dict,
    contract_validator,
):
    connector, transport, route = _connector(planner, project_bundle, route_plan, execution_fixture, contract_validator)
    assert route.harness_id == "codex"
    submitted = connector.submit(
        execution_fixture["job"],
        _request(20, key=execution_fixture["job"]["idempotency_key"]),
    )
    assert submitted.last_event_cursor == "opaque:hybrid:001"
    assert submitted.last_sequence == 1

    first = connector.reconnect_events(
        submitted.job_id,
        _request(21),
    )
    assert [event["event_id"] for event in first.events] == [
        "event_hybrid_002",
        "event_hybrid_003",
    ]
    before_disconnect = connector.operation(submitted.job_id)
    assert before_disconnect.last_event_cursor == "opaque:hybrid:003"
    assert before_disconnect.state == "running"

    transport.disconnect_next = True
    with pytest.raises(ConnectorTransportDisconnected, match="fixture disconnect"):
        connector.reconnect_events(submitted.job_id, _request(22))
    assert connector.operation(submitted.job_id) == before_disconnect

    final = connector.reconnect_events(submitted.job_id, _request(23))
    assert [event["event_id"] for event in final.events] == [
        "event_hybrid_004",
        "event_hybrid_005",
    ]
    completed = connector.operation(submitted.job_id)
    assert completed.state == "succeeded"
    assert completed.last_event_cursor == "opaque:hybrid:005"
    assert completed.last_sequence == 5
    assert completed.committed_artifact_ids == ("artifact_hybrid_result_001",)
    assert transport.afters == [
        "opaque:hybrid:001",
        "opaque:hybrid:003",
        "opaque:hybrid:003",
    ]

    artifact = connector.download_artifact(
        submitted.job_id,
        "artifact_hybrid_result_001",
        _request(24),
    )
    assert artifact == execution_fixture["artifact_text"].encode()
    assert BANNED_CREDENTIAL_KEYS.isdisjoint(key for call in transport.calls for key in _keys(call))
    assert execution_fixture["job"]["workload_identity"]["credential_reference"] == ("connector-grant://connector_grant_hybrid_001")


def test_event_replay_deduplicates_exact_events_and_rejects_mutation_or_cursor_stall(
    planner: DesktopHybridPlanner,
    project_bundle: AcceptedContractRecord,
    route_plan: AcceptedContractRecord,
    execution_fixture: dict,
    contract_validator,
):
    connector, transport, _route = _connector(planner, project_bundle, route_plan, execution_fixture, contract_validator)
    job_id = connector.submit(
        execution_fixture["job"],
        _request(30, key=execution_fixture["job"]["idempotency_key"]),
    ).job_id
    connector.reconnect_events(job_id, _request(31))
    transport.repeat_last = True
    connector.reconnect_events(job_id, _request(32))
    snapshot = connector.operation(job_id)
    assert snapshot.event_ids == (
        "event_hybrid_002",
        "event_hybrid_003",
        "event_hybrid_004",
        "event_hybrid_005",
    )

    bad, bad_transport, _ = _connector(planner, project_bundle, route_plan, execution_fixture, contract_validator)
    bad_job = bad.submit(
        execution_fixture["job"],
        _request(40, key=execution_fixture["job"]["idempotency_key"]),
    ).job_id
    bad.reconnect_events(bad_job, _request(41))
    before = bad.operation(bad_job)
    bad_transport.repeat_last = True
    bad_transport.mutate_replayed_event = True
    with pytest.raises(ConnectorReconnectError, match="replayed event content changed"):
        bad.reconnect_events(bad_job, _request(42))
    assert bad.operation(bad_job) == before

    stalled, stalled_transport, _ = _connector(planner, project_bundle, route_plan, execution_fixture, contract_validator)
    stalled_job = stalled.submit(
        execution_fixture["job"],
        _request(50, key=execution_fixture["job"]["idempotency_key"]),
    ).job_id
    stalled.reconnect_events(stalled_job, _request(51))
    stalled_transport.stall_cursor = True
    with pytest.raises(ConnectorReconnectError, match="did not advance"):
        stalled.reconnect_events(stalled_job, _request(52))

    regressed, regressed_transport, _ = _connector(
        planner,
        project_bundle,
        route_plan,
        execution_fixture,
        contract_validator,
    )
    regressed_job = regressed.submit(
        execution_fixture["job"],
        _request(53, key=execution_fixture["job"]["idempotency_key"]),
    ).job_id
    regressed.reconnect_events(regressed_job, _request(54))
    before_regression = regressed.operation(regressed_job)
    regressed_transport.regress_event_state = True
    with pytest.raises(ConnectorReconnectError, match="regressed the event state"):
        regressed.reconnect_events(regressed_job, _request(55))
    assert regressed.operation(regressed_job) == before_regression


def test_whole_projection_cas_rejects_stale_reconnect_after_concurrent_terminal_read(
    planner: DesktopHybridPlanner,
    project_bundle: AcceptedContractRecord,
    route_plan: AcceptedContractRecord,
    execution_fixture: dict,
    contract_validator,
):
    connector, transport, _ = _connector(
        planner,
        project_bundle,
        route_plan,
        execution_fixture,
        contract_validator,
    )
    job_id = connector.submit(
        execution_fixture["job"],
        _request(56, key=execution_fixture["job"]["idempotency_key"]),
    ).job_id
    connector.reconnect_events(job_id, _request(57))
    transport.conflicting_terminal = True
    transport.events_entered = threading.Event()
    transport.events_release = threading.Event()
    errors: list[Exception] = []

    def stale_reconnect() -> None:
        try:
            connector.reconnect_events(job_id, _request(58))
        except Exception as error:
            errors.append(error)

    worker = threading.Thread(target=stale_reconnect, daemon=True)
    worker.start()
    assert transport.events_entered.wait(timeout=5)
    terminal = connector.read(job_id, _request(59))
    assert terminal.state == "succeeded"
    transport.events_release.set()
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], ConnectorReconnectError)
    assert "concurrent operation projection" in str(errors[0])
    after = connector.operation(job_id)
    assert after == terminal
    assert after.last_event_cursor == "opaque:hybrid:003"
    assert after.committed_artifact_ids == ()


def test_connector_rejects_nonprefix_replay_terminal_regression_and_artifact_size_mismatch(
    planner: DesktopHybridPlanner,
    project_bundle: AcceptedContractRecord,
    route_plan: AcceptedContractRecord,
    execution_fixture: dict,
    contract_validator,
):
    connector, transport, _ = _connector(
        planner,
        project_bundle,
        route_plan,
        execution_fixture,
        contract_validator,
    )
    job_id = connector.submit(
        execution_fixture["job"],
        _request(60, key=execution_fixture["job"]["idempotency_key"]),
    ).job_id
    connector.reconnect_events(job_id, _request(61))
    transport.replay_after_new = True
    with pytest.raises(ConnectorReconnectError, match="replays must precede"):
        connector.reconnect_events(job_id, _request(62))

    terminal, terminal_transport, _ = _connector(
        planner,
        project_bundle,
        route_plan,
        execution_fixture,
        contract_validator,
    )
    terminal_job = terminal.submit(
        execution_fixture["job"],
        _request(63, key=execution_fixture["job"]["idempotency_key"]),
    ).job_id
    assert terminal.read(terminal_job, _request(631)).state == "succeeded"
    terminal.reconnect_events(terminal_job, _request(64))
    assert terminal.operation(terminal_job).state == "succeeded"
    terminal.reconnect_events(terminal_job, _request(65))
    terminal_transport.status_override = terminal_transport.fixture["accepted_status"]
    with pytest.raises(ConnectorReconnectError, match="regressed the operation"):
        terminal.read(terminal_job, _request(66))
    terminal_transport.event_after_terminal = True
    with pytest.raises(ConnectorReconnectError, match="terminal event state"):
        terminal.reconnect_events(terminal_job, _request(67))

    sized, sized_transport, _ = _connector(
        planner,
        project_bundle,
        route_plan,
        execution_fixture,
        contract_validator,
    )
    sized_transport.fixture["events"][3]["payload"]["size_bytes"] = 18
    sized_job = sized.submit(
        execution_fixture["job"],
        _request(68, key=execution_fixture["job"]["idempotency_key"]),
    ).job_id
    sized.reconnect_events(sized_job, _request(69))
    sized.reconnect_events(sized_job, _request(70))
    with pytest.raises(ConnectorReconnectError, match="digest verification"):
        sized.download_artifact(
            sized_job,
            "artifact_hybrid_result_001",
            _request(71),
        )


def test_contract_loader_rejects_duplicate_keys_wrong_versions_unknown_fields_and_security_material(tmp_path: Path):
    with pytest.raises(HybridPlanningError, match="duplicate JSON object key"):
        AcceptedContractRecord.from_bytes(b'{"schema_id":"reasoning.platform.route-plan","schema_id":"duplicate"}')
    value = _load(PLATFORM_FIXTURES / "harness-provider.local.json")
    value["schema_version"] = "1.0.0-alpha.2"
    with pytest.raises(HybridPlanningError, match="unsupported platform contract pair"):
        AcceptedContractRecord.from_document(value)
    value = _load(PLATFORM_FIXTURES / "harness-provider.local.json")
    value["unknown_recomputed_field"] = "accepted-by-old-id-version-check"
    with pytest.raises(HybridPlanningError, match="failed canonical schema validation"):
        AcceptedContractRecord.from_document(value)
    value = _load(PLATFORM_FIXTURES / "harness-provider.local.json")
    value["display_name"] = "Bearer reusable-secret-value"
    with pytest.raises(HybridPlanningError, match="credential-like"):
        DesktopHybridPlanner(
            harnesses=(AcceptedContractRecord.from_document(value),),
            inference_routes=(_record(PLATFORM_FIXTURES / "inference-route.local.json"),),
            compute_targets=(_record(PLATFORM_FIXTURES / "compute-target.local.json"),),
            transfer_receipts=DesktopTransferReceiptStore(tmp_path / "transfer-receipts.sqlite3"),
        )
