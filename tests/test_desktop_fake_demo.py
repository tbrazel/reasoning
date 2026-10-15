from __future__ import annotations

import copy
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from reasoning_ledger.cloud_client import (
    AcceptedCloudClientRecord,
    CloudClientContractValidator,
    InMemoryCloudClient,
    byte_digest,
    jcs_digest,
)
from reasoning_ledger.desktop_cloud import DesktopCloudController, DesktopCloudRuntimeError
from reasoning_ledger.desktop_fake_demo import (
    _CONTENT,
    _PINNED_DESCRIPTOR_DIGESTS,
    build_desktop_fake_demo,
    submit_desktop_fake_demo,
)
from reasoning_ledger.harnesses.desktop_hybrid import AcceptedContractRecord
from reasoning_ledger.webapp import build_app

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "conformance/cloud-client/v1alpha1/fixtures/positive"


def _minimal_options() -> dict:
    return {
        "harnesses": [{"harness_id": "reasoning-fake", "location": "hosted"}],
        "inference_routes": [{
            "route_id": "route_cloud_fake_free", "location": "hosted",
            "provider": "provider_reasoning_fake", "model_options": ["reasoning/fake-echo-v1"],
            "reasoning_efforts": ["none"],
        }],
        "compute_targets": [{
            "target_id": "target_cloud_fake_compute", "location": "hosted",
            "executor": {"target_kind": "fake"},
        }],
    }


def _session() -> dict:
    return {
        "state": "connected", "actor": {"type": "user", "id": "user_private_alpha_owner"},
        "tenant_id": "tenant_private_alpha", "capability_snapshot_digest": "sha256:" + "a" * 64,
    }


def _capabilities() -> dict:
    return {
        "snapshot_id": "capability_fake_001",
        "route_pricing_pairs": [{"route_plan": "1.0.0-alpha.2", "pricing": "1.0.0-alpha.2"}],
    }


def test_demo_is_a_canonical_one_file_explicit_transfer_and_queued_fake_run() -> None:
    demo = build_desktop_fake_demo(
        _session(), _capabilities(), _minimal_options(),
        suffix="1" * 32, now=datetime(2026, 9, 21, 12, 0, tzinfo=UTC),
    )
    project = demo.project.document()
    run = demo.run.document()
    assert demo.contents == {"README.md": _CONTENT}
    assert len(_CONTENT) == 66
    assert project["export"]["created_by"] == _session()["actor"]
    assert project["transfer_manifests"][0]["future_transfers_authorized"] is False
    assert project["transfer_manifests"][0]["disclosure"]["inputs"][0]["size_bytes"] == 66
    assert run["state"] == "queued" and run["pricing"] is None
    assert run["route_plan"]["inference"]["model"] == "reasoning/fake-echo-v1"
    assert run["route_plan"]["compute"]["image"]["repository"] == "fixture.invalid/reasoning/fake-compute"


def test_demo_rejects_any_nonfake_route_or_implicit_transfer() -> None:
    options = _minimal_options()
    options["inference_routes"][0]["provider"] = "provider_other"
    with pytest.raises(DesktopCloudRuntimeError, match="fake-only route"):
        build_desktop_fake_demo(
            _session(), _capabilities(), options,
            suffix="1" * 32, now=datetime.now(UTC),
        )
    with pytest.raises(DesktopCloudRuntimeError, match="explicit opt-in"):
        submit_desktop_fake_demo(object(), opt_in=False)


def test_demo_web_route_requires_literal_opt_in(tmp_path: Path, monkeypatch) -> None:
    import reasoning_ledger.webapp as webapp

    monkeypatch.setenv("REASONING_DESKTOP_RUNTIME", "1")
    monkeypatch.setenv("REASONING_WORKBENCH_ROOT", str(tmp_path / "workbench"))
    monkeypatch.setattr(webapp, "submit_desktop_fake_demo", lambda _cloud, *, opt_in: {"opt_in": opt_in})
    app = build_app()
    with TestClient(app) as client:
        assert client.post("/api/desktop/cloud/demo", json={}).status_code == 422
        assert client.post("/api/desktop/cloud/demo", json={"opt_in": False}).status_code == 422
        assert client.post("/api/desktop/cloud/demo", json={"opt_in": True}).json() == {"opt_in": True}


def test_exact_cloud_fake_policy_can_submit_and_reconnect_offline(tmp_path: Path) -> None:
    # The optional cross-repository smoke test uses the locked Cloud source
    # when it is present in the operator workspace; normal Reasoning CI still
    # runs the standalone canonical-record tests above.
    cloud = pytest.importorskip("reasoning_cloud.hybrid")
    hosted_pricing = pytest.importorskip("reasoning_cloud.hosted_fake_pricing")
    cloud_platform = pytest.importorskip("reasoning_cloud.platform_contract")
    policy = cloud.HybridFreePolicy.fixture()
    assert policy.descriptor_digests == _PINNED_DESCRIPTOR_DIGESTS
    validator = CloudClientContractValidator(ROOT)
    options = {
        "harnesses": [policy.harness],
        "inference_routes": [policy.inference],
        "compute_targets": [policy.compute],
    }
    descriptors = [
        AcceptedContractRecord.from_document(item)
        for key in ("harnesses", "inference_routes", "compute_targets")
        for item in options[key]
    ]
    capabilities = json.loads((FIXTURES / "capabilities.json").read_text())
    capabilities["actor"] = _session()["actor"]
    capabilities["tenant_id"] = _session()["tenant_id"]
    capabilities["compatibility_digest"] = jcs_digest(validator.compatibility)
    for key, record in zip(("harnesses", "inference_routes", "compute_targets"), descriptors, strict=True):
        item = record.document()
        identity = {"harnesses": "descriptor_id", "inference_routes": "route_id", "compute_targets": "target_id"}[key]
        capabilities[key] = [{
            "schema_id": item["schema_id"], "schema_version": item["schema_version"],
            "record_id": item[identity], "digest": byte_digest(record.to_bytes()),
        }]
    capabilities_record = AcceptedCloudClientRecord.from_document(capabilities, validator=validator)
    session = copy.deepcopy(_session())
    session.update({
        "schema_id": "reasoning.cloud-client.session", "schema_version": "1.0.0-alpha.1",
        "capability_snapshot_digest": jcs_digest(capabilities),
    })
    connected = AcceptedCloudClientRecord.from_document(session, validator=validator)
    disconnected = AcceptedCloudClientRecord.from_bytes((FIXTURES / "session.disconnected.json").read_bytes(), validator=validator)
    provider = InMemoryCloudClient(
        validator=validator,
        disconnected_session=disconnected,
        connected_session=connected,
        capabilities=capabilities_record,
        contract_records={byte_digest(record.to_bytes()): record.to_bytes() for record in descriptors},
    )
    directory = (tmp_path / "desktop-cloud").resolve()
    controller = DesktopCloudController(directory, provider=provider, validator=validator)
    try:
        controller.connect()
        demo = build_desktop_fake_demo(
            controller.session(), controller.capabilities(), controller.route_options(),
            suffix="2" * 32, now=datetime.now(UTC),
        )
        platform = cloud_platform.PlatformContractValidator(
            cloud_platform.PlatformContractSource(ROOT)
        )
        platform.validate_project(demo.project.document())
        pricing = hosted_pricing.HostedFakePricing(platform=platform)
        admission = json.loads(pricing.admit(
            demo.run.to_bytes(), idempotency_key=demo.run_idempotency_key,
            accepted_at=datetime.now(UTC),
        ))
        assert admission["quote"]["maximum_amount"] == "0"
        validator.validate_run(demo.run, session=connected, capabilities=capabilities_record, project=demo.project)
        controller.put_project(
            demo.cloud_project_id, demo.project, demo.contents,
            idempotency_key=demo.project_idempotency_key, opt_in=True,
        )
        assert controller.submit_run(demo.run, demo.project, idempotency_key=demo.run_idempotency_key)["state"] == "queued"
        controller.disconnect()
    finally:
        controller.close()
    restarted = DesktopCloudController(directory, provider=provider.restarted(), validator=validator)
    try:
        restarted.connect()
        assert restarted.get_run(demo.run.record_id)["operation_id"] == demo.run.record_id
    finally:
        restarted.close()
