from __future__ import annotations

import base64
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from reasoning_ledger.cloud_client import (
    AcceptedCloudClientRecord,
    CloudClientContractError,
    CloudClientContractValidator,
    CloudClientStateError,
    InMemoryCloudClient,
    byte_digest,
    jcs_digest,
)
from reasoning_ledger.cloud_product_policy import (
    AcceptedCloudProductPolicyRecord,
    AccountPolicyAuthority,
    CloudProductPolicyCasConflict,
    CloudProductPolicyClient,
    CloudProductPolicyContractError,
    CloudProductPolicyIdempotencyConflict,
    CloudProductPolicyScopeError,
    CloudProductPolicyTransitionError,
    CloudProductPolicyValidator,
    DeterministicPolicyClock,
    InMemoryCloudProductPolicyClient,
    InMemoryCloudProductPolicyStore,
    canonical_entitlement_digest,
    canonical_optional_use_request_digest,
    canonical_policy_snapshot_digest,
    canonical_product_policy_digest,
    digest_idempotency_key,
)
from reasoning_ledger.desktop_cloud import (
    DesktopAuthenticatedCloudBoundary,
    DesktopCloudController,
    DesktopCloudRuntimeError,
)
from reasoning_ledger.harnesses.desktop_hybrid import AcceptedContractRecord

ROOT = Path(__file__).resolve().parents[1]
CLOUD_FIXTURES = ROOT / "conformance" / "cloud-client" / "v1alpha1" / "fixtures" / "positive"
POLICY_FIXTURES = ROOT / "conformance" / "cloud-product-policy" / "v1alpha1" / "fixtures" / "positive"
PLATFORM_FIXTURES = ROOT / "conformance" / "platform" / "v1alpha1" / "fixtures" / "positive"


@dataclass
class _DesktopClock:
    current: datetime

    def __call__(self) -> datetime:
        return self.current


class _RecordingPolicyClient:
    def __init__(self, client: InMemoryCloudProductPolicyClient) -> None:
        self.client = client
        self.changes: list[tuple[AcceptedCloudProductPolicyRecord, str]] = []
        self.account_override: AcceptedCloudProductPolicyRecord | None = None
        self.lose_next_response = False
        self.response_override: AcceptedCloudProductPolicyRecord | None = None

    def account_policy(self) -> AcceptedCloudProductPolicyRecord:
        return self.account_override or self.client.account_policy()

    def change_optional_use(
        self,
        change: AcceptedCloudProductPolicyRecord,
        *,
        idempotency_key: str,
    ) -> AcceptedCloudProductPolicyRecord:
        self.changes.append((change, idempotency_key))
        result = self.client.change_optional_use(change, idempotency_key=idempotency_key)
        if self.lose_next_response:
            self.lose_next_response = False
            raise ConnectionError("simulated lost product-policy response")
        return self.response_override or result


def _cloud_record(name: str, validator: CloudClientContractValidator) -> AcceptedCloudClientRecord:
    return AcceptedCloudClientRecord.from_bytes((CLOUD_FIXTURES / name).read_bytes(), validator=validator)


def _descriptors() -> dict[str, bytes]:
    paths = (
        PLATFORM_FIXTURES / "harness-provider.local.json",
        PLATFORM_FIXTURES / "inference-route.hosted.json",
        PLATFORM_FIXTURES / "compute-target.hosted.json",
    )
    return {byte_digest(path.read_bytes()): path.read_bytes() for path in paths}


def _project() -> tuple[AcceptedContractRecord, dict[str, bytes]]:
    document = json.loads((PLATFORM_FIXTURES / "project-bundle.local-unlinked.json").read_text(encoding="utf-8"))
    contents = {"README.md": b"problem\n", "inputs/cyclic5.json": b'{"x":1}'}
    for descriptor in document["contents"]:
        payload = contents[descriptor["path"]]
        descriptor["size_bytes"] = len(payload)
        descriptor["digest"] = byte_digest(payload)
    manifest = document["transfer_manifests"][0]
    for descriptor, transfer_input in zip(document["contents"], manifest["disclosure"]["inputs"], strict=True):
        transfer_input["size_bytes"] = descriptor["size_bytes"]
        transfer_input["digest"] = descriptor["digest"]
    manifest["disclosure_digest"] = jcs_digest(manifest["disclosure"])
    manifest["approval"]["approved_disclosure_digest"] = manifest["disclosure_digest"]
    return AcceptedContractRecord.from_document(document), contents


def _system(
    tmp_path: Path,
    *,
    desktop_account_id: str = "account_alpha",
    desktop_actor_type: str = "user",
    desktop_actor_id: str = "user_alice",
    desktop_tenant_id: str = "tenant_alpha",
    desktop_capability_snapshot_id: str = "capability_snapshot_local_001",
    desktop_entitlement_version: str = "2026-09-01.1",
):
    cloud_validator = CloudClientContractValidator(ROOT)
    policy_validator = CloudProductPolicyValidator(ROOT)
    disconnected = _cloud_record("session.disconnected.json", cloud_validator)
    policy_connected = _cloud_record("session.connected.json", cloud_validator)
    policy_capabilities = _cloud_record("capabilities.json", cloud_validator)
    capabilities_document = policy_capabilities.document()
    capabilities_document["snapshot_id"] = desktop_capability_snapshot_id
    capabilities_document["actor"] = {"type": desktop_actor_type, "id": desktop_actor_id}
    capabilities_document["tenant_id"] = desktop_tenant_id
    capabilities = AcceptedCloudClientRecord.from_document(capabilities_document, validator=cloud_validator)
    connected_document = policy_connected.document()
    connected_document["actor"] = copy.deepcopy(capabilities_document["actor"])
    connected_document["tenant_id"] = desktop_tenant_id
    connected_document["capability_snapshot_digest"] = jcs_digest(capabilities_document)
    connected = AcceptedCloudClientRecord.from_document(connected_document, validator=cloud_validator)
    descriptors = _descriptors()
    provider = InMemoryCloudClient(
        validator=cloud_validator,
        disconnected_session=disconnected,
        connected_session=connected,
        capabilities=capabilities,
        contract_records=descriptors,
    )
    authority = AccountPolicyAuthority.from_connected(
        account_id="account_alpha",
        session=policy_connected,
        capabilities=policy_capabilities,
        cloud_client_validator=cloud_validator,
        descriptor_resolver=lambda reference: descriptors[reference.digest],
    )
    policy_clock = DeterministicPolicyClock("2026-09-01T21:01:00Z")
    policy_store = InMemoryCloudProductPolicyStore(policy_validator, clock=policy_clock)
    initial = AcceptedCloudProductPolicyRecord.from_bytes(
        (POLICY_FIXTURES / "account-policy.json").read_bytes(),
        validator=policy_validator,
    )
    policy_store.install(authority, initial)
    policy_client = _RecordingPolicyClient(InMemoryCloudProductPolicyClient(store=policy_store, authority=authority))
    desktop_clock = _DesktopClock(datetime(2026, 9, 1, 21, 1, tzinfo=UTC))
    controller = DesktopCloudController(
        (tmp_path / "desktop-cloud").resolve(),
        validator=cloud_validator,
        product_policy_client=policy_client,
        product_policy_validator=policy_validator,
        policy_clock=desktop_clock,
    )
    controller.bind_authenticated_provider(
        provider,
        account_id=desktop_account_id,
        entitlement_version=desktop_entitlement_version,
        actor={"type": desktop_actor_type, "id": desktop_actor_id},
        tenant_id=desktop_tenant_id,
        session_guard=lambda: None,
    )
    controller.connect()
    return controller, provider, policy_client, initial, desktop_clock, policy_clock, cloud_validator, policy_validator, authority


def test_controller_injects_only_the_canonical_policy_protocol(tmp_path: Path) -> None:
    with pytest.raises(DesktopCloudRuntimeError, match="canonical protocol"):
        DesktopCloudController(
            (tmp_path / "invalid").resolve(),
            product_policy_client=object(),  # type: ignore[arg-type]
        )


def _prepared_authenticated_boundary(
    tmp_path: Path,
) -> tuple[DesktopAuthenticatedCloudBoundary, InMemoryCloudClient, _RecordingPolicyClient]:
    seeded, provider, policy_client, *_rest = _system(tmp_path / "seed")
    seeded.close()
    restarted = provider.restarted()
    return (
        DesktopAuthenticatedCloudBoundary(
            provider=restarted,
            product_policy_client=policy_client,
        ),
        restarted,
        policy_client,
    )


def _bind_boundary(
    controller: DesktopCloudController,
    boundary: DesktopAuthenticatedCloudBoundary,
    **scope_override: str,
) -> None:
    scope = {
        "account_id": "account_alpha",
        "entitlement_version": "2026-09-01.1",
        "actor_type": "user",
        "actor_id": "user_alice",
        "tenant_id": "tenant_alpha",
    }
    scope.update(scope_override)
    controller.bind_authenticated_boundary(
        boundary,
        account_id=scope["account_id"],
        entitlement_version=scope["entitlement_version"],
        actor={"type": scope["actor_type"], "id": scope["actor_id"]},
        tenant_id=scope["tenant_id"],
        session_guard=lambda: None,
    )


def _assert_closed_boundary_detached(controller: DesktopCloudController) -> None:
    assert controller.readiness()["status"] == "disconnected"
    assert controller.readiness()["provider_injected"] is False
    assert controller._provider is None
    assert controller._product_policy_client is None
    assert controller._product_policy_authority is None
    assert controller._product_policy_snapshot is None
    assert controller._product_policy_entitlement_binding is None
    assert controller._policy_continuity is None
    assert controller.invalidate_authenticated_provider() is False


def test_authenticated_boundary_rejects_partial_clients_without_publishing_state(tmp_path: Path) -> None:
    boundary, provider, _policy_client = _prepared_authenticated_boundary(tmp_path)
    controller = DesktopCloudController((tmp_path / "target").resolve())
    invalid = DesktopAuthenticatedCloudBoundary(
        provider=provider,
        product_policy_client=object(),  # type: ignore[arg-type]
    )
    try:
        with pytest.raises(DesktopCloudRuntimeError, match="canonical protocol"):
            _bind_boundary(controller, invalid)
        assert controller.readiness()["provider_injected"] is False

        # The failed pair did not retain its otherwise-valid provider or its
        # invalid policy half. The legacy provider-only seam remains usable and
        # cannot see a stale policy client.
        controller.bind_authenticated_provider(
            provider,
            account_id="account_alpha",
            entitlement_version="2026-09-01.1",
            actor={"type": "user", "id": "user_alice"},
            tenant_id="tenant_alpha",
            session_guard=lambda: None,
        )
        controller.connect()
        with pytest.raises(CloudClientStateError, match="product-policy client"):
            controller.refresh_account_policy()
    finally:
        controller.close()


def test_authenticated_boundary_validates_continuity_before_provider_access(tmp_path: Path, monkeypatch) -> None:
    boundary, provider, _policy_client = _prepared_authenticated_boundary(tmp_path)
    state_directory = (tmp_path / "target").resolve()
    controller = DesktopCloudController(state_directory)
    continuity = state_directory / "product-policy-continuity.json"
    continuity.write_text('{"version":1,"version":1}\n', encoding="utf-8")
    calls = 0
    original = provider.current_session

    def counted_current_session():
        nonlocal calls
        calls += 1
        return original()

    monkeypatch.setattr(provider, "current_session", counted_current_session)
    try:
        with pytest.raises(DesktopCloudRuntimeError, match="duplicate key"):
            _bind_boundary(controller, boundary)
        assert calls == 0
        assert controller.readiness()["provider_injected"] is False
        continuity.unlink()
        _bind_boundary(controller, boundary)
        assert calls == 1
        assert controller.readiness()["provider_injected"] is True
    finally:
        controller.close()


def test_authentication_owned_policy_detaches_locally_with_its_provider(tmp_path: Path, monkeypatch) -> None:
    boundary, provider, policy_client = _prepared_authenticated_boundary(tmp_path)
    controller = DesktopCloudController((tmp_path / "target").resolve())
    _bind_boundary(controller, boundary)
    controller.connect()
    controller.refresh_account_policy()
    provider_calls = 0
    policy_calls = 0

    def forbidden_provider_call(*_args, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        raise AssertionError("provider must not be called while invalidating")

    def forbidden_policy_call(*_args, **_kwargs):
        nonlocal policy_calls
        policy_calls += 1
        raise AssertionError("policy client must not be called while invalidating")

    for name in ("current_session", "connect", "disconnect"):
        monkeypatch.setattr(provider, name, forbidden_provider_call)
    monkeypatch.setattr(policy_client, "account_policy", forbidden_policy_call)
    try:
        assert controller.invalidate_authenticated_provider() is True
        assert (provider_calls, policy_calls) == (0, 0)
        assert controller.readiness()["provider_injected"] is False

        replacement = boundary.provider.restarted()
        controller.bind_authenticated_provider(
            replacement,
            account_id="account_alpha",
            entitlement_version="2026-09-01.1",
            actor={"type": "user", "id": "user_alice"},
            tenant_id="tenant_alpha",
            session_guard=lambda: None,
        )
        controller.connect()
        with pytest.raises(CloudClientStateError, match="product-policy client"):
            controller.refresh_account_policy()
    finally:
        controller.close()


@pytest.mark.parametrize(
    "scope_override, fails_during_connect",
    [
        ({"actor_id": "user_other"}, True),
        ({"tenant_id": "tenant_other"}, True),
        ({"account_id": "account_other"}, False),
        ({"entitlement_version": "2026-09-01.2"}, False),
    ],
    ids=("actor", "tenant", "account", "entitlement"),
)
def test_authenticated_boundary_scope_mismatch_detaches_both_clients(
    scope_override: dict[str, str],
    fails_during_connect: bool,
    tmp_path: Path,
) -> None:
    boundary, _provider, _policy_client = _prepared_authenticated_boundary(tmp_path)
    controller = DesktopCloudController((tmp_path / "target").resolve())
    _bind_boundary(controller, boundary, **scope_override)
    try:
        if fails_during_connect:
            with pytest.raises(DesktopCloudRuntimeError, match="authenticated Desktop scope"):
                controller.connect()
        else:
            controller.connect()
            with pytest.raises(CloudProductPolicyScopeError, match="authority is unavailable"):
                controller.refresh_account_policy()
        assert controller.readiness()["status"] == "disconnected"
        assert controller.readiness()["provider_injected"] is False
        with pytest.raises(CloudClientStateError, match="no post-authentication Cloud provider"):
            controller.connect()
    finally:
        controller.close()


def test_authenticated_boundary_capability_mismatch_detaches_both_clients(tmp_path: Path) -> None:
    boundary, _provider, policy_client = _prepared_authenticated_boundary(tmp_path)
    validator = CloudClientContractValidator(ROOT)
    capabilities = _cloud_record("capabilities.json", validator).document()
    capabilities["snapshot_id"] = "capability_snapshot_other"
    drifted_capabilities = AcceptedCloudClientRecord.from_document(capabilities, validator=validator)
    connected = _cloud_record("session.connected.json", validator).document()
    connected["capability_snapshot_digest"] = jcs_digest(capabilities)
    drifted_connected = AcceptedCloudClientRecord.from_document(connected, validator=validator)
    drifted_provider = InMemoryCloudClient(
        validator=validator,
        disconnected_session=_cloud_record("session.disconnected.json", validator),
        connected_session=drifted_connected,
        capabilities=drifted_capabilities,
        contract_records=_descriptors(),
    )
    drifted_boundary = DesktopAuthenticatedCloudBoundary(
        provider=drifted_provider,
        product_policy_client=policy_client,
    )
    controller = DesktopCloudController((tmp_path / "target").resolve())
    _bind_boundary(controller, drifted_boundary)
    try:
        controller.connect()
        with pytest.raises(CloudProductPolicyScopeError, match="authority is unavailable"):
            controller.refresh_account_policy()
        assert controller.readiness()["provider_injected"] is False
    finally:
        controller.close()


def test_authenticated_boundary_invalid_policy_result_detaches_both_clients(tmp_path: Path) -> None:
    boundary, _provider, policy_client = _prepared_authenticated_boundary(tmp_path)
    controller = DesktopCloudController((tmp_path / "target").resolve())
    _bind_boundary(controller, boundary)
    controller.connect()
    policy_client.account_override = object()  # type: ignore[assignment]
    try:
        with pytest.raises(DesktopCloudRuntimeError, match="non-canonical record"):
            controller.refresh_account_policy()
        assert controller.readiness()["status"] == "disconnected"
        assert controller.readiness()["provider_injected"] is False
    finally:
        controller.close()


def test_connected_provider_session_drift_detaches_closed_boundary_before_project_access(
    tmp_path: Path,
    monkeypatch,
) -> None:
    boundary, provider, policy_client = _prepared_authenticated_boundary(tmp_path)
    controller = DesktopCloudController((tmp_path / "target").resolve())
    _bind_boundary(controller, boundary)
    controller.connect()
    controller.refresh_account_policy()
    cloud_before = copy.deepcopy(provider.store)
    provider.disconnect()
    calls = {"current_session": 0, "list_projects": 0, "account_policy": 0}
    original_current_session = provider.current_session
    original_list_projects = provider.list_projects

    def counted_current_session():
        calls["current_session"] += 1
        return original_current_session()

    def counted_list_projects():
        calls["list_projects"] += 1
        return original_list_projects()

    def counted_account_policy():
        calls["account_policy"] += 1
        return policy_client.client.account_policy()

    monkeypatch.setattr(provider, "current_session", counted_current_session)
    monkeypatch.setattr(provider, "list_projects", counted_list_projects)
    monkeypatch.setattr(policy_client, "account_policy", counted_account_policy)
    try:
        with pytest.raises(DesktopCloudRuntimeError, match="session changed"):
            controller.list_projects()
        assert calls == {"current_session": 1, "list_projects": 0, "account_policy": 0}
        _assert_closed_boundary_detached(controller)

        for operation in (
            controller.list_projects,
            controller.route_options,
            controller.refresh_account_policy,
        ):
            with pytest.raises(CloudClientStateError, match="no post-authentication Cloud provider"):
                operation()
        assert calls == {"current_session": 1, "list_projects": 0, "account_policy": 0}
        assert provider.store == cloud_before
    finally:
        controller.close()


def test_noncanonical_optional_use_response_detaches_closed_boundary_without_cloud_mutation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    boundary, provider, policy_client = _prepared_authenticated_boundary(tmp_path)
    controller = DesktopCloudController(
        (tmp_path / "target").resolve(),
        policy_clock=lambda: datetime(2026, 9, 1, 21, 1, tzinfo=UTC),
    )
    _bind_boundary(controller, boundary)
    controller.connect()
    controller.refresh_account_policy()
    cloud_before = copy.deepcopy(provider.store)
    calls = {"current_session": 0, "list_projects": 0}
    original_current_session = provider.current_session
    original_list_projects = provider.list_projects

    def counted_current_session():
        calls["current_session"] += 1
        return original_current_session()

    def counted_list_projects():
        calls["list_projects"] += 1
        return original_list_projects()

    monkeypatch.setattr(provider, "current_session", counted_current_session)
    monkeypatch.setattr(provider, "list_projects", counted_list_projects)
    policy_client.response_override = object()  # type: ignore[assignment]
    try:
        with pytest.raises(DesktopCloudRuntimeError, match="non-canonical record"):
            controller.grant_optional_use(
                "product_analytics",
                idempotency_key="desktop-malformed-response-01",
            )
        assert calls == {"current_session": 1, "list_projects": 0}
        assert len(policy_client.changes) == 1
        _assert_closed_boundary_detached(controller)

        with pytest.raises(CloudClientStateError, match="no post-authentication Cloud provider"):
            controller.list_projects()
        with pytest.raises(CloudClientStateError, match="no post-authentication Cloud provider"):
            controller.grant_optional_use(
                "product_analytics",
                idempotency_key="desktop-malformed-response-01",
            )
        assert calls == {"current_session": 1, "list_projects": 0}
        assert len(policy_client.changes) == 1
        assert provider.store == cloud_before
    finally:
        controller.close()


def test_post_connect_descriptor_drift_during_optional_use_detaches_closed_boundary(
    tmp_path: Path,
    monkeypatch,
) -> None:
    boundary, provider, policy_client = _prepared_authenticated_boundary(tmp_path)
    controller = DesktopCloudController(
        (tmp_path / "target").resolve(),
        policy_clock=lambda: datetime(2026, 9, 1, 21, 1, tzinfo=UTC),
    )
    _bind_boundary(controller, boundary)
    controller.connect()
    controller.refresh_account_policy()
    cloud_before = copy.deepcopy(provider.store)
    descriptor_digest = provider._capabilities.document()["inference_routes"][0]["digest"]
    provider._contract_records[descriptor_digest] = b"{}\n"
    calls = {"current_session": 0, "resolve_contract": 0, "list_projects": 0, "submit_run": 0}
    original_current_session = provider.current_session
    original_resolve_contract = provider.resolve_contract
    original_list_projects = provider.list_projects
    original_submit_run = provider.submit_run

    def counted_current_session():
        calls["current_session"] += 1
        return original_current_session()

    def counted_resolve_contract(reference):
        calls["resolve_contract"] += 1
        return original_resolve_contract(reference)

    def counted_list_projects():
        calls["list_projects"] += 1
        return original_list_projects()

    def counted_submit_run(*args, **kwargs):
        calls["submit_run"] += 1
        return original_submit_run(*args, **kwargs)

    monkeypatch.setattr(provider, "current_session", counted_current_session)
    monkeypatch.setattr(provider, "resolve_contract", counted_resolve_contract)
    monkeypatch.setattr(provider, "list_projects", counted_list_projects)
    monkeypatch.setattr(provider, "submit_run", counted_submit_run)
    try:
        with pytest.raises(CloudProductPolicyScopeError, match="authority is unavailable") as error:
            controller.grant_optional_use(
                "product_analytics",
                idempotency_key="desktop-descriptor-drift-01",
            )
        assert isinstance(error.value.__cause__, CloudClientContractError)
        assert "digest mismatch" in str(error.value.__cause__)
        assert calls["current_session"] == 1
        assert calls["resolve_contract"] > 0
        assert calls["list_projects"] == 0
        assert calls["submit_run"] == 0
        assert policy_client.changes == []
        _assert_closed_boundary_detached(controller)

        calls_after_detach = copy.deepcopy(calls)
        for operation in (
            controller.list_projects,
            controller.route_options,
            lambda: controller.grant_optional_use(
                "product_analytics",
                idempotency_key="desktop-descriptor-drift-01",
            ),
        ):
            with pytest.raises(CloudClientStateError, match="no post-authentication Cloud provider"):
                operation()
        assert calls == calls_after_detach
        assert policy_client.changes == []
        assert provider.store == cloud_before
    finally:
        controller.close()


def test_authenticated_boundary_rejects_stale_continuity_without_retaining_clients(tmp_path: Path) -> None:
    boundary, _provider, policy_client = _prepared_authenticated_boundary(tmp_path)
    state_directory = (tmp_path / "target").resolve()
    first = DesktopCloudController(state_directory)
    _bind_boundary(first, boundary)
    first.connect()
    first.refresh_account_policy()
    assert first.invalidate_authenticated_provider() is True
    first.close()

    continuity_path = state_directory / "product-policy-continuity.json"
    continuity = json.loads(continuity_path.read_text(encoding="utf-8"))
    persisted = next(iter(continuity["entitlement_bindings"].values()))
    persisted["digest"] = "sha256:" + "0" * 64
    continuity_path.write_text(
        json.dumps(continuity, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )

    second = DesktopCloudController(state_directory)
    rebound = DesktopAuthenticatedCloudBoundary(
        provider=boundary.provider.restarted(),
        product_policy_client=policy_client,
    )
    _bind_boundary(second, rebound)
    try:
        second.connect()
        with pytest.raises(CloudProductPolicyScopeError, match="authority is unavailable"):
            second.refresh_account_policy()
        assert second.readiness()["status"] == "disconnected"
        assert second.readiness()["provider_injected"] is False
    finally:
        second.close()


def test_refresh_revalidates_exact_connected_authority_and_preserves_policy_bytes(tmp_path: Path) -> None:
    controller, provider, policy_client, initial, _clock, _policy_clock, _cloud_validator, _policy_validator, _authority = _system(tmp_path)
    try:
        assert isinstance(policy_client, CloudProductPolicyClient)
        before = copy.deepcopy(provider.store)
        policy = controller.refresh_account_policy()
        assert policy == json.loads(initial.to_bytes())
        assert policy_client.account_policy().to_bytes() == initial.to_bytes()
        assert policy["account_id"] == "account_alpha"
        assert policy["actor"] == {"type": "user", "id": "user_alice"}
        assert policy["tenant_id"] == "tenant_alpha"
        assert [offer["reference"]["schema_id"] for offer in policy["inference_offers"]] == ["reasoning.platform.inference-route"]
        assert [offer["reference"]["schema_id"] for offer in policy["compute_offers"]] == ["reasoning.platform.compute-target"]
        assert set(policy["optional_uses"]) == {"product_analytics", "research_evaluation", "model_training"}
        assert provider.store == before
    finally:
        controller.close()


@pytest.mark.parametrize(
    "scope_override",
    [
        {"desktop_account_id": "account_other"},
        {"desktop_actor_type": "service"},
        {"desktop_actor_id": "user_other"},
        {"desktop_tenant_id": "tenant_other"},
        {"desktop_capability_snapshot_id": "capability_snapshot_local_002"},
    ],
    ids=("account", "actor-type", "actor-id", "tenant", "capability"),
)
def test_scope_mismatch_is_uniformly_refused_before_presentation(tmp_path: Path, scope_override: dict[str, str]) -> None:
    controller, _provider, _client, _initial, _clock, _policy_clock, _cloud_validator, _policy_validator, _authority = _system(
        tmp_path,
        **scope_override,
    )
    try:
        with pytest.raises(CloudProductPolicyScopeError, match="authority is unavailable"):
            controller.refresh_account_policy()
    finally:
        controller.close()


def test_entitlement_version_transplant_is_refused_before_presentation(tmp_path: Path) -> None:
    controller, _provider, _client, _initial, _clock, _policy_clock, _cloud_validator, _policy_validator, _authority = _system(
        tmp_path,
        desktop_entitlement_version="2026-09-01.2",
    )
    try:
        with pytest.raises(CloudProductPolicyScopeError, match="authority is unavailable"):
            controller.refresh_account_policy()
    finally:
        controller.close()


def test_same_version_entitlement_transplant_is_refused_after_initial_binding(tmp_path: Path) -> None:
    (
        controller,
        _provider,
        policy_client,
        initial,
        _clock,
        _policy_clock,
        _cloud_validator,
        policy_validator,
        _authority,
    ) = _system(tmp_path)
    try:
        controller.refresh_account_policy()
        transplanted = initial.document()
        transplanted["entitlement"]["id"] = "entitlement_alpha_other"
        transplanted["entitlement"]["digest"] = canonical_entitlement_digest(transplanted["entitlement"])
        transplanted["snapshot_digest"] = canonical_policy_snapshot_digest(transplanted)
        policy_client.account_override = AcceptedCloudProductPolicyRecord.from_document(
            transplanted,
            validator=policy_validator,
        )
        with pytest.raises(CloudProductPolicyScopeError, match="authority is unavailable"):
            controller.refresh_account_policy()
    finally:
        controller.close()


def test_same_version_entitlement_transplant_is_refused_after_restart(tmp_path: Path) -> None:
    (
        controller,
        provider,
        policy_client,
        initial,
        desktop_clock,
        _policy_clock,
        cloud_validator,
        policy_validator,
        _authority,
    ) = _system(tmp_path)
    try:
        controller.refresh_account_policy()
    finally:
        controller.close()

    transplanted = initial.document()
    transplanted["entitlement"]["id"] = "entitlement_alpha_other"
    transplanted["entitlement"]["digest"] = canonical_entitlement_digest(transplanted["entitlement"])
    transplanted["snapshot_digest"] = canonical_policy_snapshot_digest(transplanted)
    restarted_policy = _RecordingPolicyClient(policy_client.client)
    restarted_policy.account_override = AcceptedCloudProductPolicyRecord.from_document(
        transplanted,
        validator=policy_validator,
    )
    restarted = DesktopCloudController(
        (tmp_path / "desktop-cloud").resolve(),
        validator=cloud_validator,
        product_policy_client=restarted_policy,
        product_policy_validator=policy_validator,
        policy_clock=desktop_clock,
    )
    restarted.bind_authenticated_provider(
        provider.restarted(),
        account_id="account_alpha",
        entitlement_version="2026-09-01.1",
        actor={"type": "user", "id": "user_alice"},
        tenant_id="tenant_alpha",
        session_guard=lambda: None,
    )
    try:
        restarted.connect()
        with pytest.raises(CloudProductPolicyScopeError, match="authority is unavailable"):
            restarted.refresh_account_policy()
    finally:
        restarted.close()


def test_grant_and_withdraw_construct_complete_canonical_requests_without_cloud_side_effects(tmp_path: Path) -> None:
    controller, provider, policy_client, initial, desktop_clock, policy_clock, _cloud_validator, _policy_validator, _authority = _system(
        tmp_path
    )
    try:
        project, contents = _project()
        project_bytes = project.to_bytes()
        controller.put_project(
            "project_cloud_algebra",
            project,
            contents,
            idempotency_key="project-put-before-policy-01",
            opt_in=True,
        )
        controller.refresh_account_policy()
        cloud_before = copy.deepcopy(provider.store)
        routes_before = controller.route_options()
        grant_key = "desktop-grant-0001"
        granted = controller.grant_optional_use("product_analytics", idempotency_key=grant_key)
        assert granted["snapshot_revision"] == 1
        assert granted["optional_uses"]["product_analytics"]["decision"]["state"] == "granted"
        assert granted["optional_uses"]["research_evaluation"] == initial.document()["optional_uses"]["research_evaluation"]
        assert granted["optional_uses"]["model_training"] == initial.document()["optional_uses"]["model_training"]
        assert granted["inference_offers"] == initial.document()["inference_offers"]
        assert granted["compute_offers"] == initial.document()["compute_offers"]
        assert controller.route_options() == routes_before
        assert project.to_bytes() == project_bytes
        request, raw_key = policy_client.changes[-1]
        request_document = request.document()
        assert raw_key == grant_key
        assert request_document["idempotency_key_digest"] == digest_idempotency_key(grant_key)
        assert request_document["request_digest"] == canonical_optional_use_request_digest(request_document)
        assert request_document["expected"] == {
            "snapshot_id": initial.document()["snapshot_id"],
            "snapshot_revision": 0,
            "snapshot_digest": initial.document()["snapshot_digest"],
            "capability_snapshot_id": initial.document()["capability_snapshot"]["record_id"],
            "capability_snapshot_digest": initial.document()["capability_snapshot"]["digest"],
            "entitlement_id": initial.document()["entitlement"]["id"],
            "entitlement_version": initial.document()["entitlement"]["version"],
            "entitlement_digest": initial.document()["entitlement"]["digest"],
            "product_policy_id": initial.document()["product_policy"]["policy_id"],
            "product_policy_version": initial.document()["product_policy"]["version"],
            "product_policy_digest": initial.document()["product_policy"]["digest"],
            "decision_revision": 0,
            "decision_digest": initial.document()["optional_uses"]["product_analytics"]["decision"]["digest"],
        }
        assert provider.store == cloud_before

        desktop_clock.current = datetime(2026, 9, 1, 21, 2, tzinfo=UTC)
        policy_clock.advance(seconds=60)
        withdrawn = controller.withdraw_optional_use("product_analytics", idempotency_key="desktop-withdraw-01")
        assert withdrawn["snapshot_revision"] == 2
        assert withdrawn["optional_uses"]["product_analytics"]["decision"]["state"] == "withdrawn"
        withdrawal = policy_client.changes[-1][0].document()
        assert withdrawal["consent_policy"] == request_document["consent_policy"]
        assert controller.route_options() == routes_before
        assert project.to_bytes() == project_bytes
        assert provider.store == cloud_before
    finally:
        controller.close()


def test_policy_operations_fail_closed_after_disconnect(tmp_path: Path) -> None:
    controller, _provider, _client, _initial, _clock, _policy_clock, _cloud_validator, _policy_validator, _authority = _system(tmp_path)
    try:
        controller.refresh_account_policy()
        controller.disconnect()
        with pytest.raises(CloudClientStateError, match="disconnected"):
            controller.refresh_account_policy()
    finally:
        controller.close()


def test_lost_response_replays_exact_persisted_request_after_process_reconstruction(tmp_path: Path) -> None:
    (
        controller,
        provider,
        policy_client,
        initial,
        desktop_clock,
        _policy_clock,
        cloud_validator,
        policy_validator,
        _authority,
    ) = _system(tmp_path)
    key = "desktop-lost-response-01"
    policy_client.lose_next_response = True
    try:
        controller.refresh_account_policy()
        with pytest.raises(ConnectionError, match="lost product-policy response"):
            controller.grant_optional_use("product_analytics", idempotency_key=key)
        first_request = policy_client.changes[-1][0].to_bytes()
        continuity = tmp_path / "desktop-cloud" / "product-policy-continuity.json"
        assert continuity.is_file()
        assert key.encode() not in continuity.read_bytes()
        continuity_document = json.loads(continuity.read_bytes())
        retained = next(iter(next(iter(continuity_document["scopes"].values())).values()))
        assert base64.b64decode(retained["prior_snapshot"], validate=True) == initial.to_bytes()
    finally:
        controller.close()

    restarted_provider = provider.restarted()
    restarted_policy = _RecordingPolicyClient(policy_client.client)
    restarted = DesktopCloudController(
        (tmp_path / "desktop-cloud").resolve(),
        validator=cloud_validator,
        product_policy_client=restarted_policy,
        product_policy_validator=policy_validator,
        policy_clock=desktop_clock,
    )
    restarted.bind_authenticated_provider(
        restarted_provider,
        account_id="account_alpha",
        entitlement_version="2026-09-01.1",
        actor={"type": "user", "id": "user_alice"},
        tenant_id="tenant_alpha",
        session_guard=lambda: None,
    )
    try:
        restarted.connect()
        replay = restarted.grant_optional_use("product_analytics", idempotency_key=key)
        assert replay["snapshot_revision"] == 1
        assert restarted_policy.changes[-1][0].to_bytes() == first_request
        assert restarted.refresh_account_policy() == replay
        calls = len(restarted_policy.changes)
        with pytest.raises(CloudProductPolicyIdempotencyConflict, match="content changed"):
            restarted.withdraw_optional_use("product_analytics", idempotency_key=key)
        assert len(restarted_policy.changes) == calls
    finally:
        restarted.close()


def test_successful_lost_response_replay_advances_local_snapshot_for_fresh_change(tmp_path: Path) -> None:
    (
        controller,
        _provider,
        policy_client,
        _initial,
        desktop_clock,
        policy_clock,
        _cloud_validator,
        _policy_validator,
        _authority,
    ) = _system(tmp_path)
    key = "desktop-lost-response-same-process-01"
    policy_client.lose_next_response = True
    try:
        controller.refresh_account_policy()
        with pytest.raises(ConnectionError, match="lost product-policy response"):
            controller.grant_optional_use("product_analytics", idempotency_key=key)
        granted = controller.grant_optional_use("product_analytics", idempotency_key=key)
        assert granted["snapshot_revision"] == 1

        desktop_clock.current = datetime(2026, 9, 1, 21, 2, tzinfo=UTC)
        policy_clock.advance(seconds=60)
        withdrawn = controller.withdraw_optional_use(
            "product_analytics",
            idempotency_key="desktop-after-replay-withdraw-01",
        )
        assert withdrawn["snapshot_revision"] == 2
        assert withdrawn["optional_uses"]["product_analytics"]["decision"]["state"] == "withdrawn"
    finally:
        controller.close()


def test_replay_rejects_canonical_successor_that_changes_an_unrequested_axis(tmp_path: Path) -> None:
    (
        controller,
        provider,
        policy_client,
        _initial,
        desktop_clock,
        _policy_clock,
        cloud_validator,
        policy_validator,
        _authority,
    ) = _system(tmp_path)
    key = "desktop-replay-tamper-01"
    policy_client.lose_next_response = True
    try:
        controller.refresh_account_policy()
        with pytest.raises(ConnectionError, match="lost product-policy response"):
            controller.grant_optional_use("product_analytics", idempotency_key=key)
        exact_request = policy_client.changes[-1][0].to_bytes()
        altered = policy_client.client.account_policy().document()
        altered["inference_offers"][0]["limits"][1]["maximum"] = "19"
        altered["snapshot_digest"] = canonical_policy_snapshot_digest(altered)
        altered_successor = AcceptedCloudProductPolicyRecord.from_document(altered, validator=policy_validator)
    finally:
        controller.close()

    restarted_policy = _RecordingPolicyClient(policy_client.client)
    restarted_policy.response_override = altered_successor
    restarted = DesktopCloudController(
        (tmp_path / "desktop-cloud").resolve(),
        validator=cloud_validator,
        product_policy_client=restarted_policy,
        product_policy_validator=policy_validator,
        policy_clock=desktop_clock,
    )
    restarted.bind_authenticated_provider(
        provider.restarted(),
        account_id="account_alpha",
        entitlement_version="2026-09-01.1",
        actor={"type": "user", "id": "user_alice"},
        tenant_id="tenant_alpha",
        session_guard=lambda: None,
    )
    try:
        restarted.connect()
        with pytest.raises(CloudProductPolicyContractError, match="outside the requested transition"):
            restarted.grant_optional_use("product_analytics", idempotency_key=key)
        assert restarted_policy.changes[-1][0].to_bytes() == exact_request
        restarted_policy.response_override = None
        replay = restarted.grant_optional_use("product_analytics", idempotency_key=key)
        assert replay["snapshot_revision"] == 1
        assert restarted_policy.changes[-1][0].to_bytes() == exact_request
    finally:
        restarted.close()


def test_stale_policy_response_is_rejected_and_never_replaces_local_authority(tmp_path: Path) -> None:
    (
        controller,
        _provider,
        policy_client,
        initial,
        _desktop_clock,
        _policy_clock,
        _cloud_validator,
        _policy_validator,
        _authority,
    ) = _system(tmp_path)
    policy_client.response_override = initial
    key = "desktop-stale-response-01"
    try:
        controller.refresh_account_policy()
        with pytest.raises(CloudProductPolicyContractError, match="exact requested transition"):
            controller.grant_optional_use("product_analytics", idempotency_key=key)
        policy_client.response_override = None
        replay = controller.grant_optional_use("product_analytics", idempotency_key=key)
        assert replay["snapshot_revision"] == 1
        assert policy_client.changes[-1][0].to_bytes() == policy_client.changes[-2][0].to_bytes()
    finally:
        controller.close()


def test_concurrent_desktop_changes_admit_one_without_partial_cloud_side_effects(tmp_path: Path) -> None:
    (
        controller,
        provider,
        _policy_client,
        _initial,
        _desktop_clock,
        _policy_clock,
        _cloud_validator,
        _policy_validator,
        _authority,
    ) = _system(tmp_path)
    try:
        controller.refresh_account_policy()
        cloud_before = copy.deepcopy(provider.store)

        def grant(purpose: str):
            try:
                return controller.grant_optional_use(purpose, idempotency_key=f"desktop-concurrent-{purpose}")
            except CloudProductPolicyCasConflict as error:
                return error

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(grant, ("product_analytics", "research_evaluation")))
        assert sum(isinstance(item, dict) for item in outcomes) == 1
        assert sum(isinstance(item, CloudProductPolicyCasConflict) for item in outcomes) == 1
        current = controller.refresh_account_policy()
        assert current["snapshot_revision"] == 1
        assert sum(item["decision"]["state"] == "granted" for item in current["optional_uses"].values()) == 1
        assert provider.store == cloud_before
    finally:
        controller.close()


def test_reconstructed_desktop_withdraws_exact_historical_grant_after_policy_unavailable(tmp_path: Path) -> None:
    (
        controller,
        provider,
        policy_client,
        _initial,
        desktop_clock,
        policy_clock,
        cloud_validator,
        policy_validator,
        authority,
    ) = _system(tmp_path)
    try:
        controller.refresh_account_policy()
        granted = controller.grant_optional_use("product_analytics", idempotency_key="desktop-historical-grant-01")
    finally:
        controller.close()

    superseded = copy.deepcopy(granted)
    superseded["product_policy"]["version"] = "2026-09-01.2"
    superseded["product_policy"]["state"] = "unavailable"
    superseded["product_policy"]["digest"] = canonical_product_policy_digest(superseded["product_policy"])
    for axis in ("inference_offers", "compute_offers"):
        for offer in superseded[axis]:
            offer["availability"] = "unavailable"
            offer["price"] = {
                "state": "unavailable",
                "currency": None,
                "lower_bound": None,
                "upper_bound": None,
                "basis": None,
            }
    for purpose_name, optional in superseded["optional_uses"].items():
        optional["availability"] = "unavailable"
        optional["allowed_action"] = "withdraw" if purpose_name == "product_analytics" else "none"
    superseded["snapshot_digest"] = canonical_policy_snapshot_digest(superseded)
    accepted_superseded = AcceptedCloudProductPolicyRecord.from_document(superseded, validator=policy_validator)
    policy_clock.advance(seconds=60)
    desktop_clock.current = datetime(2026, 9, 1, 21, 2, tzinfo=UTC)
    restored_store = InMemoryCloudProductPolicyStore(policy_validator, clock=policy_clock)
    restored_store.restore_snapshot(authority, accepted_superseded)
    restored_policy = _RecordingPolicyClient(InMemoryCloudProductPolicyClient(store=restored_store, authority=authority))
    restarted = DesktopCloudController(
        (tmp_path / "desktop-cloud").resolve(),
        validator=cloud_validator,
        product_policy_client=restored_policy,
        product_policy_validator=policy_validator,
        policy_clock=desktop_clock,
    )
    restarted.bind_authenticated_provider(
        provider.restarted(),
        account_id="account_alpha",
        entitlement_version="2026-09-01.1",
        actor={"type": "user", "id": "user_alice"},
        tenant_id="tenant_alpha",
        session_guard=lambda: None,
    )
    try:
        restarted.connect()
        assert restarted.refresh_account_policy()["product_policy"]["state"] == "unavailable"
        withdrawn = restarted.withdraw_optional_use(
            "product_analytics",
            idempotency_key="desktop-historical-withdraw-01",
        )
        optional = withdrawn["optional_uses"]["product_analytics"]
        assert optional["decision"]["state"] == "withdrawn"
        assert optional["allowed_action"] == "none"
        request = restored_policy.changes[-1][0].document()
        assert request["consent_policy"] == granted["optional_uses"]["product_analytics"]["policy"]
        assert policy_client.changes[0][0].document()["consent_policy"] == request["consent_policy"]
    finally:
        restarted.close()


def test_desktop_grant_fails_closed_at_product_policy_expiry(tmp_path: Path) -> None:
    (
        controller,
        _provider,
        policy_client,
        initial,
        desktop_clock,
        policy_clock,
        _cloud_validator,
        _policy_validator,
        _authority,
    ) = _system(tmp_path)
    expiry = datetime(2026, 10, 1, 20, 0, tzinfo=UTC)
    seconds = int((expiry - desktop_clock.current).total_seconds())
    desktop_clock.current = expiry
    policy_clock.advance(seconds=seconds)
    try:
        controller.refresh_account_policy()
        with pytest.raises(CloudProductPolicyTransitionError, match="not legal"):
            controller.grant_optional_use("product_analytics", idempotency_key="desktop-expired-grant-01")
        assert policy_client.account_policy().to_bytes() == initial.to_bytes()
    finally:
        controller.close()
