from __future__ import annotations

# Ruff 0.3.4 and 0.15.x classify the src-layout package differently.
# ruff: noqa: I001

import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from reasoning_ledger.cloud_client import (
    AcceptedCloudClientRecord,
    CloudClientContractValidator,
    CloudClientCredentialError,
    CloudClientStateError,
    InMemoryCloudClient,
    byte_digest,
    jcs_digest,
)
from reasoning_ledger.cloud_product_policy import (
    AcceptedCloudProductPolicyRecord,
    AccountPolicyAuthority,
    CloudProductPolicyClient,
    CloudProductPolicyValidator,
    DeterministicPolicyClock,
    InMemoryCloudProductPolicyClient,
    InMemoryCloudProductPolicyStore,
    canonical_entitlement_digest,
    canonical_policy_snapshot_digest,
)
from reasoning_ledger.desktop_auth import (
    AuthenticatedDesktopIdentity,
    DesktopAuthenticationController,
    DesktopAuthenticationError,
    DesktopAuthenticationStageError,
    DesktopOidcConfiguration,
    DesktopPostAuthProviderFactory,
    FakeDesktopAuthEntropy,
    FakeDesktopCredentialStore,
    FakeOidcAuthority,
    FakeOidcConfiguration,
    FakeSystemBrowserLauncher,
)
from reasoning_ledger.desktop_cloud import (
    DesktopAuthenticatedCloudBoundary,
    DesktopCloudController,
    DesktopCloudRuntimeError,
)
from reasoning_ledger.desktop_policy import (
    DesktopDataPathPresentation,
    DesktopPolicyError,
    DesktopPolicyPresenter,
    DesktopPricePresentation,
    DesktopRoutePolicy,
    DesktopRoutePolicyEvidence,
    DesktopTelemetryPresentation,
)
from reasoning_ledger.harnesses.desktop_hybrid import AcceptedContractRecord

ROOT = Path(__file__).resolve().parents[1]
CLOUD_FIXTURES = ROOT / "conformance" / "cloud-client" / "v1alpha1" / "fixtures" / "positive"
POLICY_FIXTURES = ROOT / "conformance" / "cloud-product-policy" / "v1alpha1" / "fixtures" / "positive"
PLATFORM_FIXTURES = ROOT / "conformance" / "platform" / "v1alpha1" / "fixtures" / "positive"


@dataclass
class _Clock:
    current: datetime = datetime(2030, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.current

    def advance(self, seconds: int) -> None:
        self.current += timedelta(seconds=seconds)


class _NeverCalledPolicyClient:
    def account_policy(self) -> AcceptedCloudProductPolicyRecord:
        raise AssertionError("product policy was consulted unexpectedly")

    def change_optional_use(
        self,
        change: AcceptedCloudProductPolicyRecord,
        *,
        idempotency_key: str,
    ) -> AcceptedCloudProductPolicyRecord:
        raise AssertionError("product policy was mutated unexpectedly")


class _ProviderFactory(DesktopPostAuthProviderFactory):
    def __init__(self, provider: InMemoryCloudClient, policy_client: CloudProductPolicyClient | None = None) -> None:
        self.provider = provider
        self.policy_client = policy_client or _NeverCalledPolicyClient()
        self.calls: list[dict[str, object]] = []
        self.last_provider: InMemoryCloudClient | None = None

    def create(self, identity: AuthenticatedDesktopIdentity) -> DesktopAuthenticatedCloudBoundary:
        self.calls.append(identity.presentation())
        self.last_provider = self.provider.restarted()
        return DesktopAuthenticatedCloudBoundary(
            provider=self.last_provider,
            product_policy_client=self.policy_client,
        )


def _record(path: Path) -> AcceptedContractRecord:
    return AcceptedContractRecord.from_bytes(path.read_bytes())


def _fake_provider(
    validator: CloudClientContractValidator,
    *,
    actor: dict[str, str] | None = None,
    tenant_id: str = "tenant_test_alpha",
) -> InMemoryCloudClient:
    def cloud(name: str) -> AcceptedCloudClientRecord:
        return AcceptedCloudClientRecord.from_bytes((CLOUD_FIXTURES / name).read_bytes(), validator=validator)

    capabilities = cloud("capabilities.json").document()
    capabilities["snapshot_id"] = "capability_snapshot_test_desktop"
    capabilities["actor"] = actor or {"type": "user", "id": "user_test_alice"}
    capabilities["tenant_id"] = tenant_id
    accepted_capabilities = AcceptedCloudClientRecord.from_document(capabilities, validator=validator)
    connected = cloud("session.connected.json").document()
    connected["actor"] = capabilities["actor"]
    connected["tenant_id"] = capabilities["tenant_id"]
    connected["capability_snapshot_digest"] = jcs_digest(capabilities)
    accepted_connected = AcceptedCloudClientRecord.from_document(connected, validator=validator)
    descriptors = (
        PLATFORM_FIXTURES / "harness-provider.local.json",
        PLATFORM_FIXTURES / "inference-route.hosted.json",
        PLATFORM_FIXTURES / "compute-target.hosted.json",
    )
    return InMemoryCloudClient(
        validator=validator,
        disconnected_session=cloud("session.disconnected.json"),
        connected_session=accepted_connected,
        capabilities=accepted_capabilities,
        contract_records={byte_digest(path.read_bytes()): path.read_bytes() for path in descriptors},
    )


def _fake_scoped_policy_client(
    provider: InMemoryCloudClient,
) -> tuple[InMemoryCloudProductPolicyClient, AcceptedCloudProductPolicyRecord]:
    cloud_validator = CloudClientContractValidator(ROOT)
    policy_validator = CloudProductPolicyValidator(ROOT)
    connected = provider.connect()
    capabilities = provider.capabilities()
    authority = AccountPolicyAuthority.from_connected(
        account_id="account_test_alice",
        session=connected,
        capabilities=capabilities,
        cloud_client_validator=cloud_validator,
        descriptor_resolver=lambda reference: provider.resolve_contract(reference).to_bytes(),
    )
    provider.disconnect()

    document = json.loads((POLICY_FIXTURES / "account-policy.json").read_text(encoding="utf-8"))
    document["snapshot_id"] = "policy_snapshot_test_invitee_001"
    document["account_id"] = "account_test_alice"
    document["actor"] = {"type": "user", "id": "user_test_alice"}
    document["tenant_id"] = "tenant_test_alpha"
    document["capability_snapshot"] = {
        "schema_id": capabilities.schema_id,
        "schema_version": capabilities.schema_version,
        "record_id": capabilities.record_id,
        "digest": jcs_digest(capabilities.document()),
    }
    document["entitlement"] = {
        "id": "entitlement_test_invitee_free",
        "version": "test-entitlements-v1",
        "digest": "",
    }
    document["entitlement"]["digest"] = canonical_entitlement_digest(document["entitlement"])
    document["snapshot_digest"] = canonical_policy_snapshot_digest(document)
    accepted = AcceptedCloudProductPolicyRecord.from_document(document, validator=policy_validator)
    store = InMemoryCloudProductPolicyStore(
        policy_validator,
        clock=DeterministicPolicyClock(document["observed_at"]),
    )
    store.install(authority, accepted)
    return InMemoryCloudProductPolicyClient(store=store, authority=authority), accepted


def _auth_system(tmp_path: Path, *, platform: str = "darwin"):
    clock = _Clock()
    configuration = FakeOidcConfiguration(
        issuer="https://identity.test.invalid",
        client_id="test-desktop-public-client",
        audience="test-reasoning-cloud",
        redirect_uri="https://desktop-callback.test.invalid/complete",
        tenant_id="tenant_test_alpha",
    )
    validator = CloudClientContractValidator(ROOT)
    provider = _fake_provider(validator)
    factory = _ProviderFactory(provider)
    authority = FakeOidcAuthority(configuration, clock=clock)
    browser = FakeSystemBrowserLauncher()
    store = FakeDesktopCredentialStore(platform)
    cloud = DesktopCloudController((tmp_path / "cloud-state").resolve(), validator=validator)
    auth = DesktopAuthenticationController(
        configuration,
        authority=authority,
        browser=browser,
        credential_store=store,
        provider_factory=factory,
        cloud=cloud,
        entropy=FakeDesktopAuthEntropy(),
        clock=clock,
    )
    return auth, authority, browser, store, cloud, factory, clock


def _complete(auth, authority, browser, *, overrides=None):
    pending = auth.begin_sign_in()
    callback = browser.complete(authority, pending["attempt_id"], overrides=overrides)
    return callback


@pytest.mark.parametrize(
    "changes",
    [
        {"issuer": "https://login.example.com"},
        {"redirect_uri": "http://desktop-callback.test.invalid/complete"},
        {"client_id": "desktop-public-client"},
        {"audience": "reasoning-cloud"},
        {"tenant_id": "tenant_alpha"},
    ],
    ids=("live-issuer", "insecure-redirect", "non-test-client", "non-test-audience", "non-test-tenant"),
)
def test_authentication_configuration_accepts_only_explicit_fake_coordinates(changes) -> None:
    values = {
        "issuer": "https://identity.test.invalid",
        "client_id": "test-desktop-public-client",
        "audience": "test-reasoning-cloud",
        "redirect_uri": "https://desktop-callback.test.invalid/complete",
        "tenant_id": "tenant_test_alpha",
    }
    values.update(changes)
    with pytest.raises(DesktopAuthenticationError, match="only explicit .invalid fake OIDC"):
        FakeOidcConfiguration(**values)


def test_authentication_controller_accepts_the_provider_neutral_configuration(tmp_path: Path) -> None:
    clock = _Clock()
    values = {
        "issuer": "https://identity.test.invalid",
        "client_id": "test-desktop-public-client",
        "audience": "test-reasoning-cloud",
        "redirect_uri": "https://desktop-callback.test.invalid/complete",
        "tenant_id": "tenant_test_alpha",
    }
    configuration = DesktopOidcConfiguration(**values)
    fake_configuration = FakeOidcConfiguration(**values)
    validator = CloudClientContractValidator(ROOT)
    provider = _fake_provider(validator)
    factory = _ProviderFactory(provider)
    authority = FakeOidcAuthority(fake_configuration, clock=clock)
    browser = FakeSystemBrowserLauncher()
    store = FakeDesktopCredentialStore("linux")
    cloud = DesktopCloudController((tmp_path / "cloud-state").resolve(), validator=validator)
    auth = DesktopAuthenticationController(
        configuration,
        authority=authority,
        browser=browser,
        credential_store=store,
        provider_factory=factory,
        cloud=cloud,
        entropy=FakeDesktopAuthEntropy(),
        clock=clock,
    )
    try:
        callback = browser.complete(authority, auth.begin_sign_in()["attempt_id"])
        assert auth.accept_callback(callback)["state"] == "authenticated"
        assert auth.logout()["state"] == "signed_out"
    finally:
        cloud.close()


@pytest.mark.parametrize("port", [49152, 49167])
def test_desktop_oidc_configuration_accepts_registered_ipv4_loopback(port: int) -> None:
    configuration = DesktopOidcConfiguration(
        issuer="https://cognito-idp.us-west-2.amazonaws.com/us-west-2_example",
        client_id="desktop-public-client",
        audience="reasoning-cloud",
        redirect_uri=f"http://127.0.0.1:{port}/auth/callback",
        tenant_id="tenant_alpha",
    )
    assert configuration.redirect_uri == f"http://127.0.0.1:{port}/auth/callback"


@pytest.mark.parametrize(
    "redirect_uri",
    [
        "http://localhost:49152/auth/callback",
        "http://[::1]:49152/auth/callback",
        "http://127.0.0.2:49152/auth/callback",
        "http://127.0.0.1:49151/auth/callback",
        "http://127.0.0.1:49168/auth/callback",
        "http://127.0.0.1:049152/auth/callback",
        "http://127.0.0.1/auth/callback",
        "http://127.0.0.1:49152/other",
        "http://127.0.0.1:49152/auth/callback?next=example",
        "http://127.0.0.1:49152/auth/callback#fragment",
    ],
)
def test_desktop_oidc_configuration_refuses_unregistered_loopback(redirect_uri: str) -> None:
    with pytest.raises(DesktopAuthenticationError, match="configuration is invalid"):
        DesktopOidcConfiguration(
            issuer="https://cognito-idp.us-west-2.amazonaws.com/us-west-2_example",
            client_id="desktop-public-client",
            audience="reasoning-cloud",
            redirect_uri=redirect_uri,
            tenant_id="tenant_alpha",
        )


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_fake_authentication_binds_post_auth_provider_without_upload_or_live_side_effect(
    platform: str,
    tmp_path: Path,
    caplog,
) -> None:
    auth, authority, browser, store, cloud, factory, clock = _auth_system(tmp_path, platform=platform)
    callback = _complete(auth, authority, browser)
    callback_repr = repr(callback)
    result = auth.accept_callback(callback)
    try:
        assert result["state"] == "authenticated"
        assert result["identity"]["actor"] == {"type": "user", "id": "user_test_alice"}
        assert callback.code not in callback_repr
        assert callback.state not in callback_repr
        assert "PKCE" not in json.dumps(result)
        assert "REFRESH" not in json.dumps(result)
        assert browser.presentation() == {"implementation": "fake", "launch_count": 1, "opened_browser": False}
        assert store.presentation()["os_store_accessed"] is False
        assert store.presentation()["contains_session"] is True
        grant = store.load("reasoning-desktop-current-fake-session")
        assert grant is not None
        assert "REFRESH" not in repr(grant)
        assert grant.identity.nonce not in repr(grant)
        assert cloud.readiness()["provider_injected"] is True
        assert cloud.readiness()["status"] == "disconnected"
        assert factory.calls == [result["identity"]]
        assert factory.last_provider is not None
        assert factory.last_provider.store.projects == {}
        assert factory.last_provider.store.runs == {}
        assert cloud.connect()["state"] == "connected"
        assert auth.logout()["state"] == "signed_out"
        with pytest.raises(DesktopAuthenticationError, match="revoked or unknown"):
            authority.validate_grant(grant, expected=authority.configuration, now=clock())
        assert cloud.readiness()["provider_injected"] is False
        assert store.presentation()["contains_session"] is False
        assert caplog.text == ""
        forbidden = (
            callback.code.encode(),
            b"TEST_ONLY_PKCE_VERIFIER",
            b"TEST_ONLY_REFRESH_MATERIAL",
            b"sk-abcdefghijklmnop",
        )
        for path in tmp_path.rglob("*"):
            if path.is_file():
                payload = path.read_bytes()
                assert all(value not in payload for value in forbidden)
    finally:
        cloud.close()


def test_fake_invitee_authentication_composes_exact_free_policy_without_product_side_effects(tmp_path: Path) -> None:
    auth, authority, browser, store, cloud, factory, clock = _auth_system(tmp_path)
    policy_client, accepted_policy = _fake_scoped_policy_client(factory.provider)
    factory.policy_client = policy_client
    before = (
        dict(factory.provider.store.projects),
        dict(factory.provider.store.runs),
        dict(factory.provider.store.mutations),
    )
    callback = _complete(auth, authority, browser)
    assert auth.accept_callback(callback)["state"] == "authenticated"
    assert cloud.readiness()["status"] == "disconnected"
    with pytest.raises(CloudClientStateError, match="disconnected"):
        cloud.refresh_account_policy()
    assert cloud.connect()["state"] == "connected"
    presented = cloud.refresh_account_policy()
    assert presented == accepted_policy.document()
    assert presented["entitlement"]["id"] == "entitlement_test_invitee_free"
    assert presented["entitlement"]["version"] == "test-entitlements-v1"
    assert presented["catalogue"]["plan_id"] == "plan_free_alpha"
    for axis in ("inference_offers", "compute_offers"):
        limits = presented[axis][0]["limits"]
        assert limits
        assert all(item["maximum"].isdigit() for item in limits)
    inference_limits = {item["kind"]: item["maximum"] for item in presented["inference_offers"][0]["limits"]}
    compute_limits = {item["kind"]: item["maximum"] for item in presented["compute_offers"][0]["limits"]}
    assert inference_limits["operations"] == "20"
    assert compute_limits["operations"] == "5"
    assert set(presented["optional_uses"]) == {
        "product_analytics",
        "research_evaluation",
        "model_training",
    }
    assert all(item["decision"]["state"] == "off" for item in presented["optional_uses"].values())
    assert (
        dict(factory.provider.store.projects),
        dict(factory.provider.store.runs),
        dict(factory.provider.store.mutations),
    ) == before

    # Reconstruct both clients from the retained fake grant and the same
    # non-secret continuity bytes; the server snapshot remains authoritative.
    cloud.close()
    resumed_cloud = DesktopCloudController(
        (tmp_path / "cloud-state").resolve(),
        validator=CloudClientContractValidator(ROOT),
    )
    resumed = DesktopAuthenticationController(
        authority.configuration,
        authority=authority,
        browser=FakeSystemBrowserLauncher(),
        credential_store=store,
        provider_factory=factory,
        cloud=resumed_cloud,
        entropy=FakeDesktopAuthEntropy(),
        clock=clock,
    )
    try:
        assert resumed.resume()["state"] == "authenticated"
        assert resumed_cloud.readiness()["status"] == "disconnected"
        assert resumed_cloud.connect()["state"] == "connected"
        assert resumed_cloud.refresh_account_policy() == accepted_policy.document()
        assert (
            dict(factory.provider.store.projects),
            dict(factory.provider.store.runs),
            dict(factory.provider.store.mutations),
        ) == before
        assert resumed.logout()["state"] == "signed_out"
    finally:
        resumed_cloud.close()


def test_authenticated_scope_mismatch_is_refused_before_desktop_connection(tmp_path: Path) -> None:
    auth, authority, browser, _store, cloud, factory, _clock = _auth_system(tmp_path)
    validator = CloudClientContractValidator(ROOT)
    factory.provider = _fake_provider(
        validator,
        actor={"type": "user", "id": "user_test_other"},
        tenant_id="tenant_test_other",
    )
    auth.accept_callback(_complete(auth, authority, browser))
    try:
        with pytest.raises(DesktopCloudRuntimeError, match="authenticated Desktop scope"):
            cloud.connect()
        assert cloud.readiness()["status"] == "disconnected"
        assert cloud.readiness()["provider_injected"] is False
        assert factory.last_provider is not None
        assert factory.last_provider.store.projects == {}
        assert factory.last_provider.store.runs == {}
    finally:
        auth.logout()
        cloud.close()


@pytest.mark.parametrize(
    "overrides",
    [
        {"state": "test-substituted-state"},
        {"redirect_uri": "https://other-callback.test.invalid/complete"},
        {"nonce": "test-substituted-nonce"},
        {"issuer": "https://other-identity.test.invalid"},
        {"client_id": "test-other-desktop-client"},
        {"audience": "test-other-audience"},
        {"tenant_id": "tenant_test_other"},
        {"code_challenge": "test-substituted-challenge"},
        {"session_expires_at": datetime(2029, 1, 1, tzinfo=UTC)},
    ],
    ids=("state", "redirect", "nonce", "issuer", "client", "audience", "tenant", "pkce", "identity-expiry"),
)
def test_fake_callback_substitution_fails_before_provider_binding(overrides, tmp_path: Path) -> None:
    auth, authority, browser, store, cloud, factory, _clock = _auth_system(tmp_path)
    callback = _complete(auth, authority, browser, overrides=overrides)
    try:
        with pytest.raises(DesktopAuthenticationError):
            auth.accept_callback(callback)
        assert cloud.readiness()["provider_injected"] is False
        assert store.presentation()["contains_session"] is False
        assert factory.calls == []
    finally:
        cloud.close()


@pytest.mark.parametrize(
    "partial",
    [
        object(),
        DesktopAuthenticatedCloudBoundary(
            provider=object(),  # type: ignore[arg-type]
            product_policy_client=_NeverCalledPolicyClient(),
        ),
        DesktopAuthenticatedCloudBoundary(
            provider=object(),  # type: ignore[arg-type]
            product_policy_client=object(),  # type: ignore[arg-type]
        ),
    ],
    ids=("not-a-boundary", "invalid-provider", "invalid-both"),
)
def test_partial_post_auth_boundary_fails_without_provider_or_stored_grant(partial, tmp_path: Path, monkeypatch) -> None:
    auth, authority, browser, store, cloud, factory, _clock = _auth_system(tmp_path)
    monkeypatch.setattr(factory, "create", lambda _identity: partial)
    callback = _complete(auth, authority, browser)
    try:
        with pytest.raises(DesktopAuthenticationStageError) as failure:
            auth.accept_callback(callback)
        assert failure.value.diagnostic_code == "desktop_cloud_state"
        assert auth.presentation()["state"] == "signed_out"
        assert cloud.readiness()["provider_injected"] is False
        assert store.presentation()["contains_session"] is False
        assert not (tmp_path / "cloud-state" / "product-policy-continuity.json").exists()
    finally:
        cloud.close()


def test_post_auth_factory_failure_has_a_fixed_public_stage(tmp_path: Path, monkeypatch) -> None:
    auth, authority, browser, store, cloud, factory, _clock = _auth_system(tmp_path)

    def fail_create(_identity: AuthenticatedDesktopIdentity) -> DesktopAuthenticatedCloudBoundary:
        raise RuntimeError("private local details")

    monkeypatch.setattr(factory, "create", fail_create)
    callback = _complete(auth, authority, browser)
    try:
        with pytest.raises(DesktopAuthenticationStageError) as failure:
            auth.accept_callback(callback)
        assert failure.value.diagnostic_code == "post_auth_boundary"
        assert "private local details" not in str(failure.value)
        assert cloud.readiness()["provider_injected"] is False
        assert store.presentation()["contains_session"] is False
    finally:
        cloud.close()


def test_callback_failure_remains_visible_until_the_next_attempt(tmp_path: Path) -> None:
    auth, _authority, _browser, _store, cloud, _factory, _clock = _auth_system(tmp_path)
    try:
        auth.record_callback_failure("desktop_cloud_state")
        assert auth.presentation()["failure_code"] == "desktop_cloud_state"
        pending = auth.begin_sign_in()
        assert pending["state"] == "awaiting_callback"
        assert "failure_code" not in pending
        with pytest.raises(DesktopAuthenticationError, match="diagnostic"):
            auth.record_callback_failure("not/a/public/code")
    finally:
        cloud.close()


def test_credential_store_save_failure_detaches_both_clients(tmp_path: Path, monkeypatch) -> None:
    auth, authority, browser, store, cloud, factory, _clock = _auth_system(tmp_path)

    def fail_save(_slot: str, _grant) -> None:
        raise DesktopAuthenticationError("injected fake credential-store save failure")

    monkeypatch.setattr(store, "save", fail_save)
    callback = _complete(auth, authority, browser)
    try:
        with pytest.raises(DesktopAuthenticationError, match="credential-store save failure"):
            auth.accept_callback(callback)
        assert auth.presentation()["state"] == "signed_out"
        assert cloud.readiness()["provider_injected"] is False
        assert store.presentation()["contains_session"] is False

        # A later provider-only binding cannot inherit the policy client from
        # the failed authentication-owned pair.
        assert factory.last_provider is not None
        replacement = factory.last_provider.restarted()
        cloud.bind_authenticated_provider(
            replacement,
            account_id="account_test_alice",
            entitlement_version="test-entitlements-v1",
            actor={"type": "user", "id": "user_test_alice"},
            tenant_id="tenant_test_alpha",
            session_guard=lambda: None,
        )
        cloud.connect()
        with pytest.raises(CloudClientStateError, match="product-policy client"):
            cloud.refresh_account_policy()
    finally:
        cloud.close()


def test_callback_attempt_expiry_replay_and_credential_value_fail_closed(tmp_path: Path) -> None:
    auth, authority, browser, store, cloud, factory, clock = _auth_system(tmp_path)
    callback = _complete(auth, authority, browser)
    credential_callback = replace(callback, code="sk-abcdefghijklmnop")
    try:
        with pytest.raises(CloudClientCredentialError, match="credential-like"):
            auth.accept_callback(credential_callback)
        assert factory.calls == []
        clock.advance(301)
        with pytest.raises(DesktopAuthenticationError, match="binding failed"):
            auth.accept_callback(callback)
        assert store.presentation()["contains_session"] is False
    finally:
        cloud.close()

    replay_auth, replay_authority, replay_browser, _store, replay_cloud, _factory, _clock = _auth_system(tmp_path / "replay")
    replay_callback = _complete(replay_auth, replay_authority, replay_browser)
    replay_auth.accept_callback(replay_callback)
    try:
        with pytest.raises(DesktopAuthenticationError, match="no Desktop authorization callback"):
            replay_auth.accept_callback(replay_callback)
    finally:
        replay_auth.logout()
        replay_cloud.close()


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_fake_secure_store_resume_revocation_and_expiry_are_fail_closed(platform: str, tmp_path: Path) -> None:
    auth, authority, browser, store, first_cloud, factory, clock = _auth_system(tmp_path, platform=platform)
    callback = _complete(auth, authority, browser)
    auth.accept_callback(callback)
    grant = store.load("reasoning-desktop-current-fake-session")
    assert grant is not None

    validator = CloudClientContractValidator(ROOT)
    resumed_cloud = DesktopCloudController((tmp_path / "resumed-cloud").resolve(), validator=validator)
    resumed = DesktopAuthenticationController(
        authority.configuration,
        authority=authority,
        browser=FakeSystemBrowserLauncher(),
        credential_store=store,
        provider_factory=factory,
        cloud=resumed_cloud,
        entropy=FakeDesktopAuthEntropy(),
        clock=clock,
    )
    try:
        assert resumed.resume()["state"] == "authenticated"
        assert resumed_cloud.readiness()["provider_injected"] is True
        authority.revoke(grant)
        with pytest.raises(DesktopAuthenticationError, match="revoked or unknown"):
            resumed.validate_session()
        assert store.presentation()["contains_session"] is False
        assert resumed_cloud.readiness()["provider_injected"] is False
    finally:
        first_cloud.close()
        resumed_cloud.close()

    expired_auth, expired_authority, expired_browser, expired_store, expired_cloud, _factory, expired_clock = _auth_system(
        tmp_path / "expired",
        platform=platform,
    )
    expired_auth.accept_callback(_complete(expired_auth, expired_authority, expired_browser))
    expired_clock.advance(901)
    try:
        with pytest.raises(DesktopAuthenticationError, match="expired or changed scope"):
            expired_auth.validate_session()
        assert expired_store.presentation()["contains_session"] is False
        assert expired_cloud.readiness()["provider_injected"] is False
    finally:
        expired_cloud.close()


@pytest.mark.parametrize("invalidation", ["revoked", "expired"])
@pytest.mark.parametrize("entrypoint", ["connect", "connected-read"])
def test_auth_owned_cloud_provider_is_guarded_before_direct_use(
    invalidation: str,
    entrypoint: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    auth, authority, browser, store, cloud, factory, clock = _auth_system(tmp_path)
    auth.accept_callback(_complete(auth, authority, browser))
    grant = store.load("reasoning-desktop-current-fake-session")
    provider = factory.last_provider
    assert grant is not None
    assert provider is not None
    if entrypoint == "connected-read":
        assert cloud.connect()["state"] == "connected"

    calls = {"current_session": 0, "connect": 0, "list_projects": 0}
    original_current_session = provider.current_session
    original_connect = provider.connect
    original_list_projects = provider.list_projects

    def counted_current_session():
        calls["current_session"] += 1
        return original_current_session()

    def counted_connect():
        calls["connect"] += 1
        return original_connect()

    def counted_list_projects():
        calls["list_projects"] += 1
        return original_list_projects()

    monkeypatch.setattr(provider, "current_session", counted_current_session)
    monkeypatch.setattr(provider, "connect", counted_connect)
    monkeypatch.setattr(provider, "list_projects", counted_list_projects)
    durable_before = (
        dict(provider.store.projects),
        dict(provider.store.runs),
        dict(provider.store.mutations),
    )
    if invalidation == "revoked":
        authority.revoke(grant)
    else:
        clock.advance(901)

    try:
        with pytest.raises(DesktopAuthenticationError, match="revoked or unknown|expired or changed scope"):
            cloud.connect() if entrypoint == "connect" else cloud.list_projects()
        assert calls == {"current_session": 0, "connect": 0, "list_projects": 0}
        assert (
            dict(provider.store.projects),
            dict(provider.store.runs),
            dict(provider.store.mutations),
        ) == durable_before
        assert cloud.readiness()["status"] == "disconnected"
        assert cloud.readiness()["provider_injected"] is False
        assert auth.presentation()["state"] == "signed_out"
        assert store.presentation()["contains_session"] is False
    finally:
        cloud.close()


def test_validate_session_load_failure_still_clears_auth_and_cloud(
    tmp_path: Path,
    monkeypatch,
) -> None:
    auth, authority, browser, store, cloud, factory, _clock = _auth_system(tmp_path)
    auth.accept_callback(_complete(auth, authority, browser))
    provider = factory.last_provider
    assert provider is not None
    assert cloud.connect()["state"] == "connected"
    delete_calls = 0
    provider_calls = 0
    original_delete = store.delete

    def fail_load(_slot: str):
        raise DesktopAuthenticationError("injected fake credential-store load failure")

    def counted_delete(slot: str) -> None:
        nonlocal delete_calls
        delete_calls += 1
        original_delete(slot)

    monkeypatch.setattr(store, "load", fail_load)
    monkeypatch.setattr(store, "delete", counted_delete)
    original_current_session = provider.current_session

    def counted_current_session():
        nonlocal provider_calls
        provider_calls += 1
        return original_current_session()

    monkeypatch.setattr(provider, "current_session", counted_current_session)
    try:
        with pytest.raises(DesktopAuthenticationError, match="credential-store load failure"):
            auth.validate_session()
        assert delete_calls == 1
        assert provider_calls == 0
        assert auth.presentation()["state"] == "signed_out"
        assert cloud.readiness()["status"] == "disconnected"
        assert cloud.readiness()["provider_injected"] is False
        assert store.presentation()["contains_session"] is False
    finally:
        cloud.close()


def test_validate_session_preserves_primary_error_when_store_cleanup_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    auth, authority, browser, store, cloud, _factory, _clock = _auth_system(tmp_path)
    auth.accept_callback(_complete(auth, authority, browser))
    grant = store.load("reasoning-desktop-current-fake-session")
    assert grant is not None
    authority.revoke(grant)

    def fail_delete(_slot: str) -> None:
        raise DesktopAuthenticationError("injected fake credential-store delete failure")

    monkeypatch.setattr(store, "delete", fail_delete)
    try:
        with pytest.raises(DesktopAuthenticationError, match="revoked or unknown") as raised:
            auth.validate_session()
        assert any(
            note == "secondary credential-store deletion cleanup failed (DesktopAuthenticationError)"
            for note in getattr(raised.value, "__notes__", ())
        )
        assert "injected fake credential-store delete failure" not in " ".join(getattr(raised.value, "__notes__", ()))
        assert auth.presentation()["state"] == "signed_out"
        assert cloud.readiness()["provider_injected"] is False
        assert store.presentation()["contains_session"] is True
    finally:
        cloud.close()


@pytest.mark.parametrize("failure", ["load", "revoke", "delete"])
def test_logout_cleans_up_after_each_injected_dependency_failure(
    failure: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    auth, authority, browser, store, cloud, factory, _clock = _auth_system(tmp_path)
    auth.accept_callback(_complete(auth, authority, browser))
    grant = store.load("reasoning-desktop-current-fake-session")
    provider = factory.last_provider
    assert grant is not None
    assert provider is not None
    assert cloud.connect()["state"] == "connected"
    primary = DesktopAuthenticationError(f"injected fake {failure} failure")
    disconnect_calls = 0
    original_disconnect = provider.disconnect

    def counted_disconnect():
        nonlocal disconnect_calls
        disconnect_calls += 1
        return original_disconnect()

    monkeypatch.setattr(provider, "disconnect", counted_disconnect)
    if failure == "load":

        def fail_load(_slot: str):
            raise primary

        monkeypatch.setattr(store, "load", fail_load)
    elif failure == "revoke":

        def fail_revoke(_grant) -> None:
            raise primary

        monkeypatch.setattr(authority, "revoke", fail_revoke)
    else:

        def fail_delete(_slot: str) -> None:
            raise primary

        monkeypatch.setattr(store, "delete", fail_delete)

    try:
        with pytest.raises(DesktopAuthenticationError) as raised:
            auth.logout()
        assert raised.value is primary
        assert disconnect_calls == 0
        assert auth.presentation()["state"] == "signed_out"
        assert cloud.readiness()["status"] == "disconnected"
        assert cloud.readiness()["provider_injected"] is False
        assert store.presentation()["contains_session"] is (failure == "delete")
    finally:
        cloud.close()


def test_logout_retains_load_error_and_reports_sanitized_secondary_cleanup_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    auth, authority, browser, store, cloud, _factory, _clock = _auth_system(tmp_path)
    auth.accept_callback(_complete(auth, authority, browser))
    primary = DesktopAuthenticationError("injected fake load primary")

    def fail_load(_slot: str):
        raise primary

    def fail_delete(_slot: str) -> None:
        raise DesktopAuthenticationError("sk-abcdefghijklmnop")

    monkeypatch.setattr(store, "load", fail_load)
    monkeypatch.setattr(store, "delete", fail_delete)
    try:
        with pytest.raises(DesktopAuthenticationError) as raised:
            auth.logout()
        assert raised.value is primary
        notes = " ".join(getattr(raised.value, "__notes__", ()))
        assert notes == "secondary credential-store deletion cleanup failed (DesktopAuthenticationError)"
        assert "sk-abcdefghijklmnop" not in notes
        assert auth.presentation()["state"] == "signed_out"
        assert cloud.readiness()["provider_injected"] is False
    finally:
        cloud.close()


def test_logout_does_not_remove_a_preexisting_non_auth_provider(tmp_path: Path) -> None:
    clock = _Clock()
    configuration = FakeOidcConfiguration(
        issuer="https://identity.test.invalid",
        client_id="test-desktop-public-client",
        audience="test-reasoning-cloud",
        redirect_uri="https://desktop-callback.test.invalid/complete",
        tenant_id="tenant_test_alpha",
    )
    validator = CloudClientContractValidator(ROOT)
    provider = _fake_provider(validator)
    cloud = DesktopCloudController((tmp_path / "cloud-state").resolve(), validator=validator, provider=provider)
    auth = DesktopAuthenticationController(
        configuration,
        authority=FakeOidcAuthority(configuration, clock=clock),
        browser=FakeSystemBrowserLauncher(),
        credential_store=FakeDesktopCredentialStore("darwin"),
        provider_factory=_ProviderFactory(provider),
        cloud=cloud,
        entropy=FakeDesktopAuthEntropy(),
        clock=clock,
    )
    try:
        assert auth.logout()["state"] == "signed_out"
        assert cloud.readiness()["provider_injected"] is True
        assert cloud.connect()["state"] == "connected"
        assert cloud.disconnect()["state"] == "disconnected"
    finally:
        cloud.close()


def _policy_presenter() -> DesktopPolicyPresenter:
    route_records = (
        _record(PLATFORM_FIXTURES / "harness-provider.local.json"),
        _record(PLATFORM_FIXTURES / "harness-provider.hosted.json"),
        _record(PLATFORM_FIXTURES / "inference-route.local.json"),
        _record(PLATFORM_FIXTURES / "inference-route.hosted.json"),
        _record(PLATFORM_FIXTURES / "compute-target.local.json"),
        _record(PLATFORM_FIXTURES / "compute-target.hosted.json"),
    )
    local_limits = {
        "harness": {"turns": "10"},
        "inference": {"input_tokens": "1000"},
        "compute": {"vcpu_seconds": "60"},
    }
    policies: list[DesktopRoutePolicy] = []
    for record in route_records:
        document = record.document()
        axis = {
            "reasoning.platform.harness-provider": "harness",
            "reasoning.platform.inference-route": "inference",
            "reasoning.platform.compute-target": "compute",
        }[record.schema_id]
        evidence = (
            DesktopRoutePolicyEvidence.local(hard_limits=local_limits[axis])
            if document["location"] == "local"
            else DesktopRoutePolicyEvidence.remote_disabled(price_state="unavailable" if axis == "compute" else "unknown")
        )
        policies.append(DesktopRoutePolicy(descriptor=record, evidence=evidence))
    return DesktopPolicyPresenter(
        tuple(policies),
        route_pricing_pairs=(("1.0.0-alpha.1", "1.0.0-alpha.1"), ("1.0.0-alpha.2", "1.0.0-alpha.2")),
    )


def _alpha2_route_and_pricing() -> tuple[AcceptedContractRecord, AcceptedContractRecord]:
    run = json.loads((CLOUD_FIXTURES / "run.succeeded.json").read_text(encoding="utf-8"))
    return AcceptedContractRecord.from_document(run["route_plan"]), AcceptedContractRecord.from_document(run["pricing"])


def _independent_route_and_pricing() -> tuple[AcceptedContractRecord, AcceptedContractRecord]:
    plan = _record(PLATFORM_FIXTURES / "route-plan.lhl.json")
    pricing = json.loads((PLATFORM_FIXTURES / "pricing.hybrid-settled.json").read_text(encoding="utf-8"))
    pricing["operation"]["route_plan_id"] = "route_plan_lhl"
    return plan, AcceptedContractRecord.from_document(pricing)


def _local_route_with_inconsistent_price() -> tuple[AcceptedContractRecord, AcceptedContractRecord]:
    plan = _record(PLATFORM_FIXTURES / "route-plan.lll.json")
    pricing = json.loads((PLATFORM_FIXTURES / "pricing.hybrid-settled.json").read_text(encoding="utf-8"))
    pricing["operation"]["route_plan_id"] = "route_plan_lll"
    return plan, AcceptedContractRecord.from_document(pricing)


def test_policy_presentation_is_exact_independent_and_fail_closed() -> None:
    presenter = _policy_presenter()
    presentation = presenter.presentation()
    assert presentation["scope"] == "private_desktop_presentation"
    assert "canonical_gap" not in presentation
    assert len(presentation["routes"]) == 6
    assert presentation["disabled_remote_paths"] == [
        "aws_fargate",
        "aws_ec2_research",
        "modal",
        "hosted_models",
        "consent_gated_use",
        "spend_gated_dispatch",
    ]
    assert {item["purpose"] for item in presentation["optional_data_use"]} == {
        "product_analytics",
        "research_evaluation",
        "model_training",
    }
    assert all(item["enabled"] is False and item["policy_version"] is None for item in presentation["optional_data_use"])
    hosted = [route for route in presentation["routes"] if route["location"] == "hosted"]
    assert all(route["availability"] == {"available": False, "reason": "activation_evidence_absent"} for route in hosted)
    assert {route["price"]["state"] for route in hosted} == {"unknown", "unavailable"}
    assert all(route["hard_limits"] == {"state": "unavailable", "values": {}} for route in hosted)
    assert all(
        route["data_path"] == {"state": "unavailable", "location": "unverified", "subprocessors": ["unconfigured"]} for route in hosted
    )
    assert all(_is_digest(route["capability"]["digest"]) for route in presentation["routes"])
    expected_capabilities = {}
    for name in (
        "harness-provider.local.json",
        "harness-provider.hosted.json",
        "inference-route.local.json",
        "inference-route.hosted.json",
        "compute-target.local.json",
        "compute-target.hosted.json",
    ):
        record = _record(PLATFORM_FIXTURES / name)
        document = record.document()
        axis, identity_key = {
            "reasoning.platform.harness-provider": ("harness", "harness_id"),
            "reasoning.platform.inference-route": ("inference", "route_id"),
            "reasoning.platform.compute-target": ("compute", "target_id"),
        }[record.schema_id]
        identity = document[identity_key]
        expected_capabilities[(axis, identity)] = {
            "schema_id": record.schema_id,
            "schema_version": record.schema_version,
            "record_id": record.record_id,
            "digest": record.digest,
        }
    assert {(route["axis"], route["identity"]): route["capability"] for route in presentation["routes"]} == expected_capabilities

    independent_plan, independent_pricing = _independent_route_and_pricing()
    independent = presenter.present_route(
        independent_plan,
        independent_pricing,
        requested_limits={"vcpu_seconds": "60"},
    )
    assert independent["placements"] == {"inference": "hosted", "compute": "local"}
    by_axis = {item["axis"]: item for item in independent["selected"]}
    assert by_axis["inference"]["available"] is False
    assert by_axis["compute"]["available"] is True

    local_plan, inconsistent_price = _local_route_with_inconsistent_price()
    inconsistent = presenter.present_route(local_plan, inconsistent_price, requested_limits={})
    assert inconsistent["available"] is False
    assert inconsistent["reasons"] == ["price_estimate_mismatch"]
    assert inconsistent["price"]["state"] == "unavailable"

    plan, pricing = _alpha2_route_and_pricing()
    selected = presenter.present_route(plan, pricing, requested_limits={"turns": "11"})
    assert selected["placements"] == {"inference": "hosted", "compute": "hosted"}
    assert selected["available"] is False
    assert "activation_evidence_absent" in selected["reasons"]
    assert "hard_limit_exhausted:turns" in selected["reasons"]
    assert selected["price"]["state"] == "unavailable"
    assert all(item["enabled"] is False for item in selected["optional_data_use"])

    missing_cap = presenter.present_route(plan, pricing, requested_limits={"unpriced_meter": "1"})
    assert missing_cap["available"] is False
    assert "hard_limit_unavailable:unpriced_meter" in missing_cap["reasons"]


def _is_digest(value: object) -> bool:
    return isinstance(value, str) and value.startswith("sha256:") and len(value) == 71


def test_policy_rejects_mixed_versions_remote_activation_and_credential_values() -> None:
    presenter = _policy_presenter()
    plan, pricing = _alpha2_route_and_pricing()
    alpha1_pricing = _record(PLATFORM_FIXTURES / "pricing.hybrid-settled.json")
    with pytest.raises(DesktopPolicyError, match="not jointly advertised"):
        presenter.present_route(plan, alpha1_pricing, requested_limits={})
    cross_tenant_pricing = pricing.document()
    cross_tenant_pricing["operation"]["tenant_id"] = "tenant_test_other"
    with pytest.raises(DesktopPolicyError, match="identity binding changed"):
        presenter.present_route(
            plan,
            AcceptedContractRecord.from_document(cross_tenant_pricing),
            requested_limits={},
        )

    hosted = _record(PLATFORM_FIXTURES / "compute-target.hosted.json")
    active_evidence = DesktopRoutePolicyEvidence(
        available=True,
        reason=None,
        price=DesktopPricePresentation.estimated(
            minimum_amount="0",
            maximum_amount="1.000000",
            source_version="test-fake-estimate",
        ),
        hard_limits={"vcpu_seconds": "60"},
        data_path=DesktopDataPathPresentation(state="local", location="device_local", subprocessors=("none",)),
        required_telemetry=DesktopTelemetryPresentation(
            state="local_only",
            purpose="local_operation_status",
            data_classes=("operation_metadata",),
        ),
    )
    with pytest.raises(DesktopPolicyError, match="cannot activate a hosted route"):
        DesktopPolicyPresenter(
            (DesktopRoutePolicy(descriptor=hosted, evidence=active_evidence),),
            route_pricing_pairs=(("1.0.0-alpha.2", "1.0.0-alpha.2"),),
        )
    with pytest.raises(CloudClientCredentialError, match="credential-like"):
        DesktopRoutePolicyEvidence.remote_disabled(reason="sk-abcdefghijklmnop")
    with pytest.raises(DesktopPolicyError, match="remain on the device"):
        DesktopDataPathPresentation(state="local", location="us-west-2", subprocessors=("none",))
    with pytest.raises(DesktopPolicyError, match="unapproved field"):
        DesktopTelemetryPresentation(
            state="local_only",
            purpose="local_operation_status",
            data_classes=("prompt_content",),
        )
    credential_descriptor = hosted.document()
    credential_descriptor["display_name"] = "sk-abcdefghijklmnop"
    with pytest.raises(CloudClientCredentialError, match="credential-like"):
        DesktopPolicyPresenter(
            (
                DesktopRoutePolicy(
                    descriptor=AcceptedContractRecord.from_document(credential_descriptor),
                    evidence=DesktopRoutePolicyEvidence.remote_disabled(),
                ),
            ),
            route_pricing_pairs=(("1.0.0-alpha.2", "1.0.0-alpha.2"),),
        )
