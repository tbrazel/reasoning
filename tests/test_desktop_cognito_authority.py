from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from reasoning_ledger.cloud_client import CloudClientContractValidator
from reasoning_ledger.desktop_auth import (
    DesktopAuthenticationError,
    DesktopAuthorizationCallback,
    DesktopCredentialGrant,
    DesktopOidcConfiguration,
)
from reasoning_ledger.desktop_cloud_http import DesktopCloudHttpConfiguration
from reasoning_ledger.desktop_cognito_authority import DesktopCognitoAuthority, DesktopCognitoScope
from reasoning_ledger.desktop_cognito_tokens import CognitoDesktopTokenClient, VerifiedCognitoDesktopTokens

ROOT = Path(__file__).resolve().parents[1]
ISSUER = "https://cognito-idp.us-west-2.amazonaws.com/us-west-2_TESTALPHA"
CLIENT = "testdesktoppublicclient000001"
SUBJECT = "f48b8d3d-85ec-43c6-82a6-72d6d791c150"
REDIRECT = "http://127.0.0.1:49152/auth/callback"
NONCE = "test-nonce-at-least-sixteen-chars"
SESSION = ROOT / "conformance/cloud-client/v1alpha1/fixtures/positive/session.connected.json"


class _Tokens(CognitoDesktopTokenClient):
    def __init__(self) -> None:
        self.issuer = ISSUER
        self.client_id = CLIENT
        self.exchanges = 0
        self.refreshes = 0
        self.revoked: list[str] = []

    @staticmethod
    def pair(access: str, refresh: str, expiry: int) -> VerifiedCognitoDesktopTokens:
        return VerifiedCognitoDesktopTokens(
            subject=SUBJECT,
            issuer=ISSUER,
            client_id=CLIENT,
            expires_at=expiry,
            access_token=access,
            refresh_token=refresh,
        )

    def exchange_code(self, **kwargs: str) -> VerifiedCognitoDesktopTokens:
        assert kwargs["expected_nonce"] == NONCE
        self.exchanges += 1
        return self.pair("test-access-material-0001", "test-refresh-material-0001", int(time.time()) + 50)

    def refresh(self, **kwargs: str) -> VerifiedCognitoDesktopTokens:
        assert kwargs["expected_subject"] == SUBJECT
        self.refreshes += 1
        return self.pair("test-access-material-0002", "test-refresh-material-0002", int(time.time()) + 900)

    def revoke(self, *, refresh_token: str) -> None:
        self.revoked.append(refresh_token)


class _Store:
    platform = "linux"
    custody = "test-injected-os-store"

    def __init__(self) -> None:
        self.grant: DesktopCredentialGrant | None = None

    def save(self, slot: str, grant: DesktopCredentialGrant) -> None:
        assert slot == "reasoning-desktop-current-session"
        self.grant = grant

    def load(self, slot: str) -> DesktopCredentialGrant | None:
        assert slot == "reasoning-desktop-current-session"
        return self.grant

    def delete(self, slot: str) -> None:
        assert slot == "reasoning-desktop-current-session"
        self.grant = None


def _configuration() -> DesktopOidcConfiguration:
    return DesktopOidcConfiguration(
        issuer=ISSUER,
        client_id=CLIENT,
        audience=CLIENT,
        redirect_uri=REDIRECT,
        tenant_id="tenant_alpha",
    )


def _authority(*, actor_id: str = "user_alice") -> tuple[DesktopCognitoAuthority, _Tokens, _Store, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = SESSION.read_bytes().replace(b"user_alice", actor_id.encode())
        return httpx.Response(200, content=payload, headers={"Content-Type": "application/json"})

    tokens = _Tokens()
    store = _Store()
    authority = DesktopCognitoAuthority(
        tokens,
        DesktopCloudHttpConfiguration("https://desktop-api.example.org"),
        DesktopCognitoScope("account_alpha", "user_alice", "tenant_alpha", "entitlement_alpha"),
        store,
        validator=CloudClientContractValidator(ROOT),
        transport_factory=lambda: httpx.MockTransport(handler),
    )
    return authority, tokens, store, requests


def _exchange(authority: DesktopCognitoAuthority):
    callback = DesktopAuthorizationCallback("attempt_alpha", REDIRECT, "state_alpha", "code_alpha")
    return authority.exchange(
        callback,
        verifier="test-verifier-with-at-least-forty-three-ascii-characters-0123456789",
        expected=_configuration(),
        expected_nonce=NONCE,
        now=datetime.now(UTC),
    )


def test_exchange_binds_hosted_scope_and_keeps_bearer_in_header() -> None:
    authority, tokens, _store, requests = _authority()
    exchange = _exchange(authority)
    assert exchange.identity.account_id == "account_alpha"
    assert exchange.identity.actor == {"type": "user", "id": "user_alice"}
    assert exchange.grant.material == "test-refresh-material-0001"
    assert authority.access_token() == "test-access-material-0001"
    assert requests[0].headers["authorization"] == "Bearer test-access-material-0001"
    assert tokens.exchanges == 1


def test_refresh_rotation_is_durable_before_reusing_cloud_provider() -> None:
    authority, tokens, store, requests = _authority()
    exchange = _exchange(authority)
    store.save("reasoning-desktop-current-session", exchange.grant)
    identity = authority.validate_grant(exchange.grant, expected=_configuration(), now=datetime.now(UTC))
    assert identity.account_id == exchange.identity.account_id
    assert tokens.refreshes == 1
    assert store.grant is not None and store.grant.material == "test-refresh-material-0002"
    assert authority.access_token() == "test-access-material-0002"
    assert requests[-1].headers["authorization"] == "Bearer test-access-material-0002"
    authority.validate_grant(store.grant, expected=_configuration(), now=datetime.now(UTC))
    assert tokens.refreshes == 1
    authority.revoke(store.grant)
    assert tokens.revoked == ["test-refresh-material-0002"]
    with pytest.raises(DesktopAuthenticationError, match="unavailable"):
        authority.access_token()


def test_hosted_actor_drift_refuses_and_revokes_new_exchange() -> None:
    authority, tokens, _store, _requests = _authority(actor_id="user_other")
    with pytest.raises(DesktopAuthenticationError, match="scope"):
        _exchange(authority)
    assert tokens.revoked == ["test-refresh-material-0001"]
