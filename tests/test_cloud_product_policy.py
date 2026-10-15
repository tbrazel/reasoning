from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from reasoning_ledger.cloud_client import (
    AcceptedCloudClientRecord,
    CloudClientContractValidator,
    ContractRecordReference,
    byte_digest,
    jcs_digest,
)
from reasoning_ledger.cloud_product_policy import (
    CLOUD_CLIENT_MANIFEST_BYTE_DIGEST,
    CLOUD_PRODUCT_POLICY_BUNDLE_DIGEST,
    AcceptedCloudProductPolicyRecord,
    AccountPolicyAuthority,
    CloudProductPolicyCasConflict,
    CloudProductPolicyClient,
    CloudProductPolicyContractError,
    CloudProductPolicyCredentialError,
    CloudProductPolicyHistoryLimitError,
    CloudProductPolicyIdempotencyConflict,
    CloudProductPolicyScopeError,
    CloudProductPolicyTransitionError,
    CloudProductPolicyValidator,
    DeterministicPolicyClock,
    InMemoryCloudProductPolicyClient,
    InMemoryCloudProductPolicyStore,
    canonical_decision_entry_digest,
    canonical_optional_policy_digest,
    canonical_optional_use_request_digest,
    canonical_policy_snapshot_digest,
    canonical_product_policy_digest,
    digest_idempotency_key,
    parse_strict_cloud_product_policy_json,
)

ROOT = Path(__file__).resolve().parents[1]
POLICY_FIXTURES = ROOT / "conformance" / "cloud-product-policy" / "v1alpha1" / "fixtures" / "positive"
CLOUD_FIXTURES = ROOT / "conformance" / "cloud-client" / "v1alpha1" / "fixtures" / "positive"
PLATFORM_FIXTURES = ROOT / "conformance" / "platform" / "v1alpha1" / "fixtures" / "positive"


@pytest.fixture(scope="module")
def policy_validator() -> CloudProductPolicyValidator:
    return CloudProductPolicyValidator(ROOT)


@pytest.fixture(scope="module")
def cloud_validator() -> CloudClientContractValidator:
    return CloudClientContractValidator(ROOT)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _accepted_cloud(name: str, validator: CloudClientContractValidator) -> AcceptedCloudClientRecord:
    return AcceptedCloudClientRecord.from_bytes((CLOUD_FIXTURES / name).read_bytes(), validator=validator)


def _descriptor_resolver(reference: ContractRecordReference) -> bytes:
    paths = {
        ("reasoning.platform.harness-provider", "harness_provider_codex_local"): "harness-provider.local.json",
        ("reasoning.platform.inference-route", "route_cloud_openrouter"): "inference-route.hosted.json",
        ("reasoning.platform.inference-route", "route_local_ollama"): "inference-route.local.json",
        ("reasoning.platform.compute-target", "target_cloud_research_cpu"): "compute-target.hosted.json",
        ("reasoning.platform.compute-target", "target_local_container"): "compute-target.local.json",
    }
    try:
        name = paths[(reference.schema_id, reference.record_id)]
    except KeyError as error:
        raise CloudProductPolicyContractError("unknown test descriptor") from error
    return (PLATFORM_FIXTURES / name).read_bytes()


def _descriptor_reference(name: str) -> dict:
    payload = (PLATFORM_FIXTURES / name).read_bytes()
    document = json.loads(payload)
    field = "route_id" if document["schema_id"] == "reasoning.platform.inference-route" else "target_id"
    return {
        "schema_id": document["schema_id"],
        "schema_version": document["schema_version"],
        "record_id": document[field],
        "digest": byte_digest(payload),
    }


def _connected_authority(
    cloud_validator: CloudClientContractValidator,
    *,
    account_id: str = "account_alpha",
    actor_type: str = "user",
    actor_id: str = "user_alice",
    tenant_id: str = "tenant_alpha",
    snapshot_id: str = "capability_snapshot_local_001",
    inference_fixture: str = "inference-route.hosted.json",
    compute_fixture: str = "compute-target.hosted.json",
) -> AccountPolicyAuthority:
    capabilities = _load(CLOUD_FIXTURES / "capabilities.json")
    capabilities["snapshot_id"] = snapshot_id
    capabilities["actor"] = {"type": actor_type, "id": actor_id}
    capabilities["tenant_id"] = tenant_id
    capabilities["inference_routes"] = [_descriptor_reference(inference_fixture)]
    capabilities["compute_targets"] = [_descriptor_reference(compute_fixture)]
    accepted_capabilities = AcceptedCloudClientRecord.from_document(capabilities, validator=cloud_validator)
    session = _load(CLOUD_FIXTURES / "session.connected.json")
    session["actor"] = copy.deepcopy(capabilities["actor"])
    session["tenant_id"] = tenant_id
    session["capability_snapshot_digest"] = jcs_digest(capabilities)
    accepted_session = AcceptedCloudClientRecord.from_document(session, validator=cloud_validator)
    return AccountPolicyAuthority.from_connected(
        account_id=account_id,
        session=accepted_session,
        capabilities=accepted_capabilities,
        cloud_client_validator=cloud_validator,
        descriptor_resolver=_descriptor_resolver,
    )


def _authority(cloud_validator: CloudClientContractValidator, *, account_id: str = "account_alpha") -> AccountPolicyAuthority:
    return _connected_authority(cloud_validator, account_id=account_id)


def _snapshot(validator: CloudProductPolicyValidator) -> AcceptedCloudProductPolicyRecord:
    return AcceptedCloudProductPolicyRecord.from_bytes((POLICY_FIXTURES / "account-policy.json").read_bytes(), validator=validator)


def _accepted_snapshot_document(document: dict, validator: CloudProductPolicyValidator) -> AcceptedCloudProductPolicyRecord:
    document["snapshot_digest"] = canonical_policy_snapshot_digest(document)
    return AcceptedCloudProductPolicyRecord.from_document(document, validator=validator)


def _snapshot_document_for_authority(
    authority: AccountPolicyAuthority,
    *,
    local_descriptors: bool = False,
) -> dict:
    document = _load(POLICY_FIXTURES / "account-policy.json")
    document["account_id"] = authority.account_id
    document["actor"] = {"type": authority.actor_type, "id": authority.actor_id}
    document["tenant_id"] = authority.tenant_id
    document["capability_snapshot"]["record_id"] = authority.capability_snapshot_id
    document["capability_snapshot"]["digest"] = authority.capability_snapshot_digest
    if local_descriptors:
        for axis, name in (
            ("inference_offers", "inference-route.local.json"),
            ("compute_offers", "compute-target.local.json"),
        ):
            offer = document[axis][0]
            offer["reference"] = _descriptor_reference(name)
            offer["data_path"] = {
                "state": "verified",
                "location": "device_local",
                "region": None,
                "subprocessors": [],
                "retention_seconds": 0,
                "provider_training": "prohibited",
            }
            offer["required_telemetry"] = {
                "state": "not_applicable",
                "policy_id": None,
                "version": None,
                "digest": None,
                "purposes": [],
                "data_classes": [],
                "processors": [],
                "region": None,
                "retention_seconds": None,
                "field_allowlist": [],
            }
    return document


def _change_document(snapshot: AcceptedCloudProductPolicyRecord, *, key: str, action: str, requested_at: str) -> dict:
    current = snapshot.document()
    purpose_name = "product_analytics"
    purpose = current["optional_uses"][purpose_name]
    capability = current["capability_snapshot"]
    entitlement = current["entitlement"]
    product_policy = current["product_policy"]
    consent_policy = purpose["policy"] if action == "grant" else purpose["decision_history"][-1]["policy"]
    document = {
        "schema_id": "reasoning.cloud-product-policy.optional-use-change",
        "schema_version": "1.0.0-alpha.1",
        "account_id": current["account_id"],
        "actor": copy.deepcopy(current["actor"]),
        "tenant_id": current["tenant_id"],
        "purpose": purpose_name,
        "action": action,
        "expected": {
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
        },
        "consent_policy": copy.deepcopy(consent_policy),
        "requested_at": requested_at,
        "idempotency_key_digest": digest_idempotency_key(key),
        "request_digest": "",
    }
    document["request_digest"] = canonical_optional_use_request_digest(document)
    return document


def _accepted_change(
    snapshot: AcceptedCloudProductPolicyRecord,
    validator: CloudProductPolicyValidator,
    *,
    key: str,
    action: str,
    requested_at: str,
) -> AcceptedCloudProductPolicyRecord:
    return AcceptedCloudProductPolicyRecord.from_document(
        _change_document(snapshot, key=key, action=action, requested_at=requested_at),
        validator=validator,
    )


def test_validator_pins_exact_bundle_and_distinct_cloud_manifest_byte_digest(policy_validator):
    assert policy_validator.manifest["bundle_digest"] == CLOUD_PRODUCT_POLICY_BUNDLE_DIGEST
    cloud_dependency = next(item for item in policy_validator.manifest["dependencies"] if item["id"] == "cloud-client")
    assert cloud_dependency["digest"] == CLOUD_CLIENT_MANIFEST_BYTE_DIGEST
    cloud_manifest_bytes = (ROOT / cloud_dependency["path"]).read_bytes()
    assert byte_digest(cloud_manifest_bytes) == CLOUD_CLIENT_MANIFEST_BYTE_DIGEST
    assert byte_digest(cloud_manifest_bytes) != jcs_digest(json.loads(cloud_manifest_bytes))


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"[]",
        b'{"schema_id":"a","schema_id":"b"}',
        b'{"value":NaN}',
        b"\xff",
    ],
)
def test_strict_parser_rejects_empty_nonobject_duplicate_nonfinite_and_non_utf8(payload):
    with pytest.raises(CloudProductPolicyContractError):
        parse_strict_cloud_product_policy_json(payload)


def test_validator_rejects_unknown_schema_version_and_extra_fields(policy_validator):
    document = _load(POLICY_FIXTURES / "account-policy.json")
    for key, value in (("schema_id", "reasoning.cloud-product-policy.third-record"), ("schema_version", "1.0.0")):
        changed = copy.deepcopy(document)
        changed[key] = value
        with pytest.raises(CloudProductPolicyContractError):
            policy_validator.accept(json.dumps(changed).encode())
    changed = copy.deepcopy(document)
    changed["extra"] = "forbidden"
    changed["snapshot_digest"] = canonical_policy_snapshot_digest(changed)
    with pytest.raises(CloudProductPolicyContractError):
        policy_validator.accept(json.dumps(changed).encode())


def test_recursive_credential_rejection_precedes_schema_acceptance(policy_validator):
    document = _load(POLICY_FIXTURES / "account-policy.json")
    document["optional_uses"]["product_analytics"]["policy"]["api_key"] = "benign-shape"
    document["snapshot_digest"] = canonical_policy_snapshot_digest(document)
    with pytest.raises(CloudProductPolicyCredentialError):
        policy_validator.accept(json.dumps(document).encode())

    document = _load(POLICY_FIXTURES / "account-policy.json")
    document["optional_uses"]["product_analytics"]["policy"]["processors"][0] = "Bearer abcdefghijklmnopqrstuvwxyz"
    document["snapshot_digest"] = canonical_policy_snapshot_digest(document)
    with pytest.raises(CloudProductPolicyCredentialError):
        policy_validator.accept(json.dumps(document).encode())


def test_accepted_wrapper_retains_exact_bytes_and_distinguishes_semantic_digest(policy_validator):
    payload = (POLICY_FIXTURES / "account-policy.json").read_bytes()
    accepted = AcceptedCloudProductPolicyRecord.from_bytes(payload, validator=policy_validator)
    assert accepted.to_bytes() == payload
    assert accepted.digest == byte_digest(payload)
    assert accepted.semantic_digest == jcs_digest(accepted.document())
    assert accepted.digest != accepted.semantic_digest
    assert accepted.record_id == accepted.document()["snapshot_id"]


def test_derived_snapshot_and_request_digests_are_checked(policy_validator):
    snapshot = _load(POLICY_FIXTURES / "account-policy.json")
    assert snapshot["snapshot_digest"] == canonical_policy_snapshot_digest(snapshot)
    request = _load(POLICY_FIXTURES / "optional-use-change.grant.json")
    assert request["request_digest"] == canonical_optional_use_request_digest(request)
    for document, field in ((snapshot, "snapshot_digest"), (request, "request_digest")):
        changed = copy.deepcopy(document)
        changed[field] = "sha256:" + "0" * 64
        with pytest.raises(CloudProductPolicyContractError):
            policy_validator.accept(json.dumps(changed).encode())


def test_connected_authority_binds_separate_account_and_complete_cloud_scope(cloud_validator):
    authority = _authority(cloud_validator)
    assert authority.scope_key == ("account_alpha", "user", "user_alice", "tenant_alpha")
    capability = _load(CLOUD_FIXTURES / "capabilities.json")
    assert authority.capability_snapshot_id == capability["snapshot_id"]
    assert authority.capability_snapshot_digest == jcs_digest(capability)
    with pytest.raises(CloudProductPolicyScopeError):
        _authority(cloud_validator, account_id="invalid account")
    with pytest.raises(CloudProductPolicyScopeError):
        AccountPolicyAuthority.from_connected(
            account_id="account_alpha",
            session=_accepted_cloud("session.disconnected.json", cloud_validator),
            capabilities=_accepted_cloud("capabilities.json", cloud_validator),
            cloud_client_validator=cloud_validator,
            descriptor_resolver=_descriptor_resolver,
        )


def test_connected_authority_semantically_resolves_every_capability_descriptor(cloud_validator):
    capabilities = _load(CLOUD_FIXTURES / "capabilities.json")
    capabilities["inference_routes"][0]["digest"] = "sha256:" + "0" * 64
    accepted_capabilities = AcceptedCloudClientRecord.from_document(capabilities, validator=cloud_validator)
    session = _load(CLOUD_FIXTURES / "session.connected.json")
    session["capability_snapshot_digest"] = jcs_digest(capabilities)
    accepted_session = AcceptedCloudClientRecord.from_document(session, validator=cloud_validator)
    with pytest.raises(CloudProductPolicyScopeError, match="authority is unavailable"):
        AccountPolicyAuthority.from_connected(
            account_id="account_alpha",
            session=accepted_session,
            capabilities=accepted_capabilities,
            cloud_client_validator=cloud_validator,
            descriptor_resolver=_descriptor_resolver,
        )


def test_semantic_account_policy_validation_checks_projection_digests(policy_validator, cloud_validator):
    authority = _authority(cloud_validator)
    assert policy_validator.validate_account_policy(_snapshot(policy_validator), authority=authority)["snapshot_revision"] == 0
    base = _load(POLICY_FIXTURES / "account-policy.json")
    mutations = (
        ("entitlement", "digest"),
        ("catalogue", "catalogue_digest"),
        ("catalogue", "plan_digest"),
        ("product_policy", "digest"),
    )
    for parent, field in mutations:
        changed = copy.deepcopy(base)
        changed[parent][field] = "sha256:" + "0" * 64
        accepted = _accepted_snapshot_document(changed, policy_validator)
        with pytest.raises(CloudProductPolicyContractError):
            policy_validator.validate_account_policy(accepted, authority=authority)


def test_offer_semantics_refuse_cross_axis_duplicate_price_limit_and_path_defaults(policy_validator, cloud_validator):
    authority = _authority(cloud_validator)
    base = _load(POLICY_FIXTURES / "account-policy.json")
    variants = []

    changed = copy.deepcopy(base)
    changed["inference_offers"][0]["reference"] = copy.deepcopy(changed["compute_offers"][0]["reference"])
    variants.append(changed)

    changed = copy.deepcopy(base)
    changed["inference_offers"].append(copy.deepcopy(changed["inference_offers"][0]))
    variants.append(changed)

    changed = copy.deepcopy(base)
    changed["inference_offers"][0]["price"]["lower_bound"] = "0.06"
    variants.append(changed)

    changed = copy.deepcopy(base)
    changed["compute_offers"][0]["limits"][0]["kind"] = "operations"
    variants.append(changed)

    changed = copy.deepcopy(base)
    changed["compute_offers"][0]["data_path"]["state"] = "planned"
    variants.append(changed)

    changed = copy.deepcopy(base)
    changed["compute_offers"][0]["required_telemetry"]["field_allowlist"] = []
    variants.append(changed)

    for changed in variants:
        with pytest.raises(CloudProductPolicyContractError):
            accepted = _accepted_snapshot_document(changed, policy_validator)
            policy_validator.validate_account_policy(accepted, authority=authority)


@pytest.mark.parametrize("axis", ["inference_offers", "compute_offers"])
def test_hosted_descriptor_cannot_be_presented_as_device_local(policy_validator, cloud_validator, axis):
    authority = _authority(cloud_validator)
    changed = _load(POLICY_FIXTURES / "account-policy.json")
    offer = changed[axis][0]
    offer["data_path"] = {
        "state": "verified",
        "location": "device_local",
        "region": None,
        "subprocessors": [],
        "retention_seconds": 0,
        "provider_training": "prohibited",
    }
    offer["required_telemetry"] = {
        "state": "not_applicable",
        "policy_id": None,
        "version": None,
        "digest": None,
        "purposes": [],
        "data_classes": [],
        "processors": [],
        "region": None,
        "retention_seconds": None,
        "field_allowlist": [],
    }
    accepted = _accepted_snapshot_document(changed, policy_validator)
    with pytest.raises(CloudProductPolicyContractError, match="location differs"):
        policy_validator.validate_account_policy(accepted, authority=authority)


@pytest.mark.parametrize("axis", ["inference_offers", "compute_offers"])
def test_local_descriptor_cannot_be_presented_as_hosted(policy_validator, cloud_validator, axis):
    authority = _connected_authority(
        cloud_validator,
        snapshot_id="capability_snapshot_local_002",
        inference_fixture="inference-route.local.json",
        compute_fixture="compute-target.local.json",
    )
    changed = _snapshot_document_for_authority(authority, local_descriptors=True)
    changed[axis][0]["data_path"]["location"] = "hosted"
    accepted = _accepted_snapshot_document(changed, policy_validator)
    with pytest.raises(CloudProductPolicyContractError, match="location differs"):
        policy_validator.validate_account_policy(accepted, authority=authority)


def test_estimated_zero_only_range_must_use_explicit_zero_state(policy_validator, cloud_validator):
    authority = _authority(cloud_validator)
    changed = _load(POLICY_FIXTURES / "account-policy.json")
    changed["inference_offers"][0]["price"]["upper_bound"] = "0"
    accepted = _accepted_snapshot_document(changed, policy_validator)
    with pytest.raises(CloudProductPolicyContractError, match="zero charge"):
        policy_validator.validate_account_policy(accepted, authority=authority)


def test_policy_chronology_and_decision_lineage_fail_closed(policy_validator, cloud_validator):
    authority = _authority(cloud_validator)
    base = _load(POLICY_FIXTURES / "account-policy.json")

    changed = copy.deepcopy(base)
    changed["product_policy"]["state"] = "expired"
    changed["product_policy"]["digest"] = jcs_digest({key: value for key, value in changed["product_policy"].items() if key != "digest"})
    with pytest.raises(CloudProductPolicyContractError):
        policy_validator.validate_account_policy(_accepted_snapshot_document(changed, policy_validator), authority=authority)

    changed = copy.deepcopy(base)
    changed["optional_uses"]["product_analytics"]["decision"]["revision"] = 1
    with pytest.raises(CloudProductPolicyContractError):
        policy_validator.validate_account_policy(_accepted_snapshot_document(changed, policy_validator), authority=authority)

    changed = copy.deepcopy(base)
    changed["optional_uses"]["product_analytics"]["allowed_action"] = "none"
    with pytest.raises(CloudProductPolicyContractError):
        policy_validator.validate_account_policy(_accepted_snapshot_document(changed, policy_validator), authority=authority)


@pytest.mark.parametrize("decided_at", ["2026-09-01T21:01:00Z", "2026-09-01T21:02:00Z"])
def test_recovered_grant_at_or_after_exact_policy_expiry_is_refused(policy_validator, cloud_validator, decided_at):
    authority = _authority(cloud_validator)
    changed = _load(POLICY_FIXTURES / "account-policy.json")
    changed["observed_at"] = "2026-09-01T21:02:00Z"
    product = changed["product_policy"]
    product["expires_at"] = "2026-09-01T23:00:00Z"
    product["digest"] = canonical_product_policy_digest(product)
    purpose = changed["optional_uses"]["product_analytics"]
    policy = purpose["policy"]
    policy["expires_at"] = "2026-09-01T21:01:00Z"
    policy["digest"] = canonical_optional_policy_digest(policy)
    entry = {
        "revision": 1,
        "digest": "",
        "state": "granted",
        "action": "grant",
        "decided_at": decided_at,
        "policy_digest": policy["digest"],
        "policy": copy.deepcopy(policy),
    }
    entry["digest"] = canonical_decision_entry_digest(entry)
    purpose["decision_history"].append(entry)
    purpose["decision"] = {key: entry[key] for key in ("state", "revision", "digest", "decided_at", "policy_digest")}
    purpose["prior_data_disposition"] = "not_applicable"
    purpose["allowed_action"] = "withdraw"
    accepted = _accepted_snapshot_document(changed, policy_validator)
    with pytest.raises(CloudProductPolicyContractError, match="illegal grant"):
        policy_validator.validate_account_policy(accepted, authority=authority)


def test_shared_clients_reconstruct_and_return_identical_exact_bytes(policy_validator, cloud_validator):
    authority = _authority(cloud_validator)
    store = InMemoryCloudProductPolicyStore(policy_validator)
    snapshot = _snapshot(policy_validator)
    store.install(authority, snapshot)
    browser = InMemoryCloudProductPolicyClient(store=store, authority=authority)
    desktop = InMemoryCloudProductPolicyClient(store=store, authority=authority)
    reconstructed = InMemoryCloudProductPolicyClient(store=store, authority=copy.deepcopy(authority))
    assert isinstance(browser, CloudProductPolicyClient)
    assert browser.account_policy().to_bytes() == desktop.account_policy().to_bytes() == reconstructed.account_policy().to_bytes()
    assert browser.account_policy().to_bytes() == snapshot.to_bytes()
    with pytest.raises(CloudProductPolicyContractError):
        browser.change_optional_use(snapshot, idempotency_key="0123456789abcdef")


def test_cross_account_authority_cannot_observe_existing_scope(policy_validator, cloud_validator):
    authority = _authority(cloud_validator)
    store = InMemoryCloudProductPolicyStore(policy_validator)
    store.install(authority, _snapshot(policy_validator))
    other = _authority(cloud_validator, account_id="account_beta")
    with pytest.raises(CloudProductPolicyScopeError, match="authority is unavailable"):
        InMemoryCloudProductPolicyClient(store=store, authority=other).account_policy()


@pytest.mark.parametrize(
    ("actor_type", "tenant_id"),
    [("service", "tenant_alpha"), ("user", "tenant_beta")],
)
def test_actor_type_and_tenant_are_independent_scope_boundaries(policy_validator, cloud_validator, actor_type, tenant_id):
    authority = _authority(cloud_validator)
    store = InMemoryCloudProductPolicyStore(policy_validator)
    store.install(authority, _snapshot(policy_validator))
    other = _connected_authority(cloud_validator, actor_type=actor_type, tenant_id=tenant_id)
    with pytest.raises(CloudProductPolicyScopeError, match="authority is unavailable"):
        InMemoryCloudProductPolicyClient(store=store, authority=other).account_policy()


def test_scope_transplant_is_refused_before_install(policy_validator, cloud_validator):
    authority = _authority(cloud_validator)
    document = _load(POLICY_FIXTURES / "account-policy.json")
    document["actor"]["type"] = "service"
    document["snapshot_digest"] = canonical_policy_snapshot_digest(document)
    transplanted = AcceptedCloudProductPolicyRecord.from_document(document, validator=policy_validator)
    with pytest.raises(CloudProductPolicyScopeError, match="authority is unavailable"):
        InMemoryCloudProductPolicyStore(policy_validator).install(authority, transplanted)


def test_deterministic_clock_and_idempotency_key_digest_are_bounded():
    clock = DeterministicPolicyClock("2026-09-01T21:00:00Z")
    assert clock.now() == "2026-09-01T21:00:00Z"
    assert clock.advance(seconds=60) == "2026-09-01T21:01:00Z"
    with pytest.raises(CloudProductPolicyContractError):
        clock.advance(seconds=True)
    assert digest_idempotency_key("0123456789abcdef").startswith("sha256:")
    with pytest.raises(CloudProductPolicyContractError):
        digest_idempotency_key("short")
    with pytest.raises(CloudProductPolicyCredentialError):
        digest_idempotency_key("sk-abcdefghijklmnop")


def test_account_authority_cannot_be_publicly_constructed():
    with pytest.raises(TypeError):
        AccountPolicyAuthority(
            account_id="account_alpha",
            actor_type="user",
            actor_id="user_alice",
            tenant_id="tenant_alpha",
            capability_snapshot_id="capability_snapshot_local_001",
            capability_snapshot_digest="sha256:" + "0" * 64,
            inference_descriptors=(),
            compute_descriptors=(),
            _accepted=True,
        )


def test_grant_is_atomic_independent_and_visible_as_identical_bytes(policy_validator, cloud_validator):
    authority = _authority(cloud_validator)
    clock = DeterministicPolicyClock("2026-09-01T21:01:00Z")
    store = InMemoryCloudProductPolicyStore(policy_validator, clock=clock)
    initial = _snapshot(policy_validator)
    store.install(authority, initial)
    browser = InMemoryCloudProductPolicyClient(store=store, authority=authority)
    desktop = InMemoryCloudProductPolicyClient(store=store, authority=authority)
    key = "grant-product-0001"
    change = _accepted_change(initial, policy_validator, key=key, action="grant", requested_at=clock.now())
    result = browser.change_optional_use(change, idempotency_key=key)
    document = result.document()
    assert document["snapshot_revision"] == 1
    assert document["optional_uses"]["product_analytics"]["decision"]["state"] == "granted"
    assert document["optional_uses"]["product_analytics"]["allowed_action"] == "withdraw"
    for name in ("research_evaluation", "model_training"):
        assert document["optional_uses"][name] == initial.document()["optional_uses"][name]
    assert document["inference_offers"] == initial.document()["inference_offers"]
    assert document["compute_offers"] == initial.document()["compute_offers"]
    assert desktop.account_policy().to_bytes() == result.to_bytes()


@pytest.mark.parametrize(
    ("requested_at", "accepted"),
    [
        ("2026-09-01T21:00:59Z", True),
        ("2026-09-01T21:01:00Z", False),
        ("2026-09-01T21:02:00Z", False),
    ],
)
def test_grant_rechecks_exact_policy_window_at_request_time(
    policy_validator,
    cloud_validator,
    requested_at,
    accepted,
):
    authority = _authority(cloud_validator)
    initial_document = _load(POLICY_FIXTURES / "account-policy.json")
    product = initial_document["product_policy"]
    product["expires_at"] = "2026-09-01T23:00:00Z"
    product["digest"] = canonical_product_policy_digest(product)
    policy = initial_document["optional_uses"]["product_analytics"]["policy"]
    policy["expires_at"] = "2026-09-01T21:01:00Z"
    policy["digest"] = canonical_optional_policy_digest(policy)
    initial = _accepted_snapshot_document(initial_document, policy_validator)
    clock = DeterministicPolicyClock(requested_at)
    store = InMemoryCloudProductPolicyStore(policy_validator, clock=clock)
    store.install(authority, initial)
    client = InMemoryCloudProductPolicyClient(store=store, authority=authority)
    key = f"grant-policy-window-{requested_at[-6:-1].replace(':', '')}"
    change = _accepted_change(initial, policy_validator, key=key, action="grant", requested_at=requested_at)
    before = client.account_policy().to_bytes()
    if accepted:
        assert client.change_optional_use(change, idempotency_key=key).document()["snapshot_revision"] == 1
    else:
        with pytest.raises(CloudProductPolicyTransitionError, match="not legal"):
            client.change_optional_use(change, idempotency_key=key)
        assert client.account_policy().to_bytes() == before


def test_grant_rechecks_product_policy_expiry_at_request_time(policy_validator, cloud_validator):
    authority = _authority(cloud_validator)
    initial_document = _load(POLICY_FIXTURES / "account-policy.json")
    product = initial_document["product_policy"]
    product["expires_at"] = "2026-09-01T21:01:00Z"
    product["digest"] = canonical_product_policy_digest(product)
    initial = _accepted_snapshot_document(initial_document, policy_validator)
    clock = DeterministicPolicyClock("2026-09-01T21:01:00Z")
    store = InMemoryCloudProductPolicyStore(policy_validator, clock=clock)
    store.install(authority, initial)
    client = InMemoryCloudProductPolicyClient(store=store, authority=authority)
    key = "grant-product-expiry-01"
    change = _accepted_change(initial, policy_validator, key=key, action="grant", requested_at=clock.now())
    before = client.account_policy().to_bytes()
    with pytest.raises(CloudProductPolicyTransitionError, match="not legal"):
        client.change_optional_use(change, idempotency_key=key)
    assert client.account_policy().to_bytes() == before


def test_exact_replay_precedes_cas_and_survives_later_change_restart_and_reconstruction(policy_validator, cloud_validator):
    authority = _authority(cloud_validator)
    clock = DeterministicPolicyClock("2026-09-01T21:01:00Z")
    store = InMemoryCloudProductPolicyStore(policy_validator, clock=clock)
    initial = _snapshot(policy_validator)
    store.install(authority, initial)
    client = InMemoryCloudProductPolicyClient(store=store, authority=authority)

    grant_key = "grant-product-0002"
    grant = _accepted_change(initial, policy_validator, key=grant_key, action="grant", requested_at=clock.now())
    grant_result = client.change_optional_use(grant, idempotency_key=grant_key)
    clock.advance(seconds=60)
    withdraw_key = "withdraw-product-01"
    withdraw = _accepted_change(grant_result, policy_validator, key=withdraw_key, action="withdraw", requested_at=clock.now())
    withdrawn = client.change_optional_use(withdraw, idempotency_key=withdraw_key)
    assert withdrawn.document()["snapshot_revision"] == 2

    restarted = store.restart()
    reconstructed = InMemoryCloudProductPolicyClient(store=restarted, authority=authority)
    replay = reconstructed.change_optional_use(grant, idempotency_key=grant_key)
    assert replay.to_bytes() == grant_result.to_bytes()
    assert replay.to_bytes() != reconstructed.account_policy().to_bytes()


def test_exact_replay_cannot_cross_connected_capability_snapshot(policy_validator, cloud_validator):
    authority = _authority(cloud_validator)
    clock = DeterministicPolicyClock("2026-09-01T21:01:00Z")
    store = InMemoryCloudProductPolicyStore(policy_validator, clock=clock)
    initial = _snapshot(policy_validator)
    store.install(authority, initial)
    client = InMemoryCloudProductPolicyClient(store=store, authority=authority)
    key = "grant-capability-0001"
    change = _accepted_change(initial, policy_validator, key=key, action="grant", requested_at=clock.now())
    original_response = client.change_optional_use(change, idempotency_key=key)

    replacement = _connected_authority(cloud_validator, snapshot_id="capability_snapshot_local_002")
    replacement_client = InMemoryCloudProductPolicyClient(store=store, authority=replacement)
    with pytest.raises(CloudProductPolicyScopeError, match="authority is unavailable"):
        replacement_client.change_optional_use(change, idempotency_key=key)
    with pytest.raises(CloudProductPolicyScopeError, match="authority is unavailable"):
        replacement_client.account_policy()
    assert client.account_policy().to_bytes() == original_response.to_bytes()


def test_changed_content_conflicts_before_cas_and_fresh_key_cannot_bypass_stale_state(policy_validator, cloud_validator):
    authority = _authority(cloud_validator)
    clock = DeterministicPolicyClock("2026-09-01T21:01:00Z")
    store = InMemoryCloudProductPolicyStore(policy_validator, clock=clock)
    initial = _snapshot(policy_validator)
    store.install(authority, initial)
    client = InMemoryCloudProductPolicyClient(store=store, authority=authority)
    key = "grant-product-0003"
    original = _accepted_change(initial, policy_validator, key=key, action="grant", requested_at=clock.now())
    accepted = client.change_optional_use(original, idempotency_key=key)

    changed = _change_document(initial, key=key, action="grant", requested_at="2026-09-01T21:00:30Z")
    changed_record = AcceptedCloudProductPolicyRecord.from_document(changed, validator=policy_validator)
    with pytest.raises(CloudProductPolicyIdempotencyConflict):
        client.change_optional_use(changed_record, idempotency_key=key)

    fresh_key = "grant-product-0004"
    stale = _accepted_change(initial, policy_validator, key=fresh_key, action="grant", requested_at=clock.now())
    with pytest.raises(CloudProductPolicyCasConflict):
        client.change_optional_use(stale, idempotency_key=fresh_key)
    assert client.account_policy().to_bytes() == accepted.to_bytes()


def test_raw_idempotency_key_digest_and_shape_are_validated_before_transition(policy_validator, cloud_validator):
    authority = _authority(cloud_validator)
    clock = DeterministicPolicyClock("2026-09-01T21:01:00Z")
    store = InMemoryCloudProductPolicyStore(policy_validator, clock=clock)
    initial = _snapshot(policy_validator)
    store.install(authority, initial)
    client = InMemoryCloudProductPolicyClient(store=store, authority=authority)
    change = _accepted_change(initial, policy_validator, key="grant-product-0005", action="grant", requested_at=clock.now())
    with pytest.raises(CloudProductPolicyContractError):
        client.change_optional_use(change, idempotency_key="another-valid-key")
    with pytest.raises(CloudProductPolicyCredentialError):
        client.change_optional_use(change, idempotency_key="sk-abcdefghijklmnop")
    assert client.account_policy().to_bytes() == initial.to_bytes()


def test_historical_grant_can_be_withdrawn_after_policy_becomes_unavailable(policy_validator, cloud_validator):
    authority = _authority(cloud_validator)
    clock = DeterministicPolicyClock("2026-09-01T21:01:00Z")
    store = InMemoryCloudProductPolicyStore(policy_validator, clock=clock)
    initial = _snapshot(policy_validator)
    store.install(authority, initial)
    client = InMemoryCloudProductPolicyClient(store=store, authority=authority)
    grant_key = "grant-product-0006"
    granted = client.change_optional_use(
        _accepted_change(initial, policy_validator, key=grant_key, action="grant", requested_at=clock.now()),
        idempotency_key=grant_key,
    )

    superseded = granted.document()
    superseded["product_policy"]["version"] = "2026-09-01.2"
    superseded["product_policy"]["state"] = "unavailable"
    superseded["product_policy"]["digest"] = jcs_digest(
        {key: value for key, value in superseded["product_policy"].items() if key != "digest"}
    )
    for axis in ("inference_offers", "compute_offers"):
        for offer in superseded[axis]:
            offer["availability"] = "unavailable"
            offer["price"] = {"state": "unavailable", "currency": None, "lower_bound": None, "upper_bound": None, "basis": None}
    for purpose_name, optional in superseded["optional_uses"].items():
        optional["availability"] = "unavailable"
        optional["allowed_action"] = "withdraw" if purpose_name == "product_analytics" else "none"
    restored_snapshot = _accepted_snapshot_document(superseded, policy_validator)
    restored_store = InMemoryCloudProductPolicyStore(policy_validator, clock=clock)
    restored_store.restore_snapshot(authority, restored_snapshot)
    reconstructed = InMemoryCloudProductPolicyClient(store=restored_store, authority=authority)

    clock.advance(seconds=60)
    withdraw_key = "withdraw-product-02"
    withdrawal = _accepted_change(
        restored_snapshot,
        policy_validator,
        key=withdraw_key,
        action="withdraw",
        requested_at=clock.now(),
    )
    result = reconstructed.change_optional_use(withdrawal, idempotency_key=withdraw_key)
    purpose = result.document()["optional_uses"]["product_analytics"]
    assert purpose["decision"]["state"] == "withdrawn"
    assert purpose["allowed_action"] == "none"
    assert purpose["decision_history"][-1]["policy"] == granted.document()["optional_uses"]["product_analytics"]["policy"]


@pytest.mark.parametrize("mutation", ["policy_id", "version", "data_classes", "retention_seconds"])
def test_restored_withdrawal_must_retain_exact_preceding_grant_terms(policy_validator, cloud_validator, mutation):
    authority = _authority(cloud_validator)
    clock = DeterministicPolicyClock("2026-09-01T21:01:00Z")
    store = InMemoryCloudProductPolicyStore(policy_validator, clock=clock)
    initial = _snapshot(policy_validator)
    store.install(authority, initial)
    client = InMemoryCloudProductPolicyClient(store=store, authority=authority)
    grant_key = "grant-lineage-0001"
    granted = client.change_optional_use(
        _accepted_change(initial, policy_validator, key=grant_key, action="grant", requested_at=clock.now()),
        idempotency_key=grant_key,
    )
    clock.advance(seconds=60)
    withdraw_key = "withdraw-lineage-01"
    withdrawn = client.change_optional_use(
        _accepted_change(granted, policy_validator, key=withdraw_key, action="withdraw", requested_at=clock.now()),
        idempotency_key=withdraw_key,
    )

    changed = withdrawn.document()
    purpose = changed["optional_uses"]["product_analytics"]
    final = purpose["decision_history"][-1]
    policy = final["policy"]
    if mutation == "policy_id":
        policy["policy_id"] = "optional_product_analytics_changed"
    elif mutation == "version":
        policy["version"] = "2026-09-01.2"
    elif mutation == "data_classes":
        policy["data_classes"].append("error_metadata")
    else:
        policy["retention_seconds"] += 1
    policy["digest"] = canonical_optional_policy_digest(policy)
    final["policy_digest"] = policy["digest"]
    final["digest"] = canonical_decision_entry_digest(final)
    purpose["decision"] = {key: final[key] for key in ("state", "revision", "digest", "decided_at", "policy_digest")}
    accepted = _accepted_snapshot_document(changed, policy_validator)
    with pytest.raises(CloudProductPolicyContractError, match="illegal withdrawal"):
        policy_validator.validate_account_policy(accepted, authority=authority)


def test_concurrent_stale_changes_admit_exactly_one(policy_validator, cloud_validator):
    from concurrent.futures import ThreadPoolExecutor

    authority = _authority(cloud_validator)
    clock = DeterministicPolicyClock("2026-09-01T21:01:00Z")
    store = InMemoryCloudProductPolicyStore(policy_validator, clock=clock)
    initial = _snapshot(policy_validator)
    store.install(authority, initial)
    clients = [InMemoryCloudProductPolicyClient(store=store, authority=authority) for _ in range(2)]
    keys = ["grant-concurrent-01", "grant-concurrent-02"]
    changes = [_accepted_change(initial, policy_validator, key=key, action="grant", requested_at=clock.now()) for key in keys]

    def invoke(index):
        try:
            return clients[index].change_optional_use(changes[index], idempotency_key=keys[index])
        except CloudProductPolicyCasConflict as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(invoke, range(2)))
    assert sum(isinstance(item, AcceptedCloudProductPolicyRecord) for item in outcomes) == 1
    assert sum(isinstance(item, CloudProductPolicyCasConflict) for item in outcomes) == 1
    assert clients[0].account_policy().document()["snapshot_revision"] == 1


def test_full_history_refusal_is_atomic_and_prior_replay_survives(policy_validator, cloud_validator):
    authority = _authority(cloud_validator)
    clock = DeterministicPolicyClock("2026-09-01T21:01:00Z")
    store = InMemoryCloudProductPolicyStore(policy_validator, clock=clock)
    current = _snapshot(policy_validator)
    store.install(authority, current)
    client = InMemoryCloudProductPolicyClient(store=store, authority=authority)
    first_change = None
    first_result = None
    first_key = "history-transition-00"
    for index in range(63):
        action = "grant" if index % 2 == 0 else "withdraw"
        key = f"history-transition-{index:02d}"
        change = _accepted_change(current, policy_validator, key=key, action=action, requested_at=clock.now())
        result = client.change_optional_use(change, idempotency_key=key)
        if index == 0:
            first_change, first_result = change, result
        current = result
        clock.advance(seconds=60)
    assert len(current.document()["optional_uses"]["product_analytics"]["decision_history"]) == 64
    key = "history-transition-63"
    action = "withdraw"
    refused = _accepted_change(current, policy_validator, key=key, action=action, requested_at=clock.now())
    before = client.account_policy().to_bytes()
    with pytest.raises(CloudProductPolicyHistoryLimitError):
        client.change_optional_use(refused, idempotency_key=key)
    assert client.account_policy().to_bytes() == before
    assert first_change is not None and first_result is not None
    assert client.change_optional_use(first_change, idempotency_key=first_key).to_bytes() == first_result.to_bytes()
