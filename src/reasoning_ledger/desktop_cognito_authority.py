"""Production Desktop OAuth authority over verified Cognito and hosted scope.

The refresh grant lives only in the OS credential store. Access tokens are
short-lived process memory and are presented only in the HTTPS bearer header.
The server remains authoritative for tenant/user membership on every call.
"""

from __future__ import annotations

import hmac
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx

from .cloud_client import CloudClientContractValidator, canonical_cloud_client_validator, require_credential_free
from .desktop_auth import (
    AuthenticatedDesktopIdentity,
    DesktopAuthenticationError,
    DesktopAuthorizationAuthority,
    DesktopAuthorizationCallback,
    DesktopCredentialGrant,
    DesktopCredentialStore,
    DesktopOidcConfiguration,
    DesktopOidcExchange,
)
from .desktop_cloud_http import (
    DesktopBearerTokenSource,
    DesktopCloudHttpConfiguration,
    DesktopCloudHttpProvider,
    DesktopCloudHttpTransport,
)
from .desktop_cognito_tokens import CognitoDesktopTokenClient, VerifiedCognitoDesktopTokens

_IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9._:-]{2,127}\Z")
_GRANT = re.compile(r"desktop_grant_([0-9a-f]{32})\Z")
_STORE_SLOT = "reasoning-desktop-current-session"


@dataclass(frozen=True)
class DesktopCognitoScope:
    """Non-secret, operator-provisioned single-account private-alpha scope."""

    account_id: str
    user_id: str
    tenant_id: str
    entitlement_version: str

    def __post_init__(self) -> None:
        values = (self.account_id, self.user_id, self.tenant_id, self.entitlement_version)
        if any(not isinstance(value, str) or not _IDENTIFIER.fullmatch(value) for value in values):
            raise DesktopAuthenticationError("Desktop hosted account scope is invalid")
        require_credential_free(list(values))


class _CurrentBearer(DesktopBearerTokenSource):
    def __init__(self, token: str) -> None:
        self._token = token

    def access_token(self) -> str:
        return self._token


class DesktopCognitoAuthority(DesktopAuthorizationAuthority, DesktopBearerTokenSource):
    """Exchange PKCE, rotate refresh grants, and recheck hosted membership."""

    def __init__(
        self,
        token_client: CognitoDesktopTokenClient,
        endpoint: DesktopCloudHttpConfiguration,
        scope: DesktopCognitoScope,
        credential_store: DesktopCredentialStore,
        *,
        validator: CloudClientContractValidator | None = None,
        transport_factory: Callable[[], httpx.BaseTransport | None] | None = None,
    ) -> None:
        if (
            not isinstance(token_client, CognitoDesktopTokenClient)
            or not isinstance(endpoint, DesktopCloudHttpConfiguration)
            or not isinstance(scope, DesktopCognitoScope)
            or not isinstance(credential_store, DesktopCredentialStore)
        ):
            raise DesktopAuthenticationError("Desktop Cognito authority dependencies are invalid")
        self._tokens = token_client
        self._endpoint = endpoint
        self._scope = scope
        self._store = credential_store
        self._validator = validator or canonical_cloud_client_validator()
        self._transport_factory = transport_factory
        self._lock = threading.RLock()
        self._current: VerifiedCognitoDesktopTokens | None = None

    def _configuration(self, expected: DesktopOidcConfiguration) -> None:
        if (
            not isinstance(expected, DesktopOidcConfiguration)
            or expected.issuer != self._tokens.issuer
            or expected.client_id != self._tokens.client_id
            or expected.audience != self._tokens.client_id
            or expected.tenant_id != self._scope.tenant_id
        ):
            raise DesktopAuthenticationError("Desktop Cognito identity coordinates changed")

    def _server_scope(self, access_token: str) -> None:
        transport = DesktopCloudHttpTransport(
            self._endpoint,
            _CurrentBearer(access_token),
            transport=self._transport_factory() if self._transport_factory is not None else None,
        )
        try:
            provider = DesktopCloudHttpProvider(transport, validator=self._validator)
            document = provider.current_session().document()
            if (
                document.get("state") != "connected"
                or document.get("actor") != {"type": "user", "id": self._scope.user_id}
                or document.get("tenant_id") != self._scope.tenant_id
            ):
                raise DesktopAuthenticationError("hosted Desktop membership differs from the provisioned scope")
        finally:
            transport.close()

    def _identity(
        self,
        tokens: VerifiedCognitoDesktopTokens,
        *,
        expected: DesktopOidcConfiguration,
        nonce: str,
        now: datetime,
    ) -> AuthenticatedDesktopIdentity:
        expiry = min(datetime.fromtimestamp(tokens.expires_at, UTC), now + timedelta(seconds=expected.session_ttl_seconds))
        if expiry <= now + timedelta(seconds=30):
            raise DesktopAuthenticationError("Cognito Desktop session is already expiring")
        return AuthenticatedDesktopIdentity(
            account_id=self._scope.account_id,
            actor={"type": "user", "id": self._scope.user_id},
            tenant_id=self._scope.tenant_id,
            issuer=expected.issuer,
            client_id=expected.client_id,
            audience=expected.audience,
            nonce=nonce,
            expires_at=expiry,
            entitlement_version=self._scope.entitlement_version,
        )

    def exchange(
        self,
        callback: DesktopAuthorizationCallback,
        *,
        verifier: str,
        expected: DesktopOidcConfiguration,
        expected_nonce: str,
        now: datetime,
    ) -> DesktopOidcExchange:
        self._configuration(expected)
        with self._lock:
            tokens = self._tokens.exchange_code(
                code=callback.code,
                verifier=verifier,
                redirect_uri=callback.redirect_uri,
                expected_nonce=expected_nonce,
            )
            try:
                self._server_scope(tokens.access_token)
                identity = self._identity(tokens, expected=expected, nonce=expected_nonce, now=now)
            except Exception as error:
                self._current = None
                try:
                    self._tokens.revoke(refresh_token=tokens.refresh_token)
                except Exception as cleanup_error:
                    error.add_note(f"secondary token revocation failed ({type(cleanup_error).__name__})")
                raise
            grant = DesktopCredentialGrant(
                grant_id=f"desktop_grant_{UUID(tokens.subject).hex}",
                identity=identity,
                material=tokens.refresh_token,
            )
            self._current = tokens
            return DesktopOidcExchange(identity=identity, grant=grant)

    def validate_grant(
        self,
        grant: DesktopCredentialGrant,
        *,
        expected: DesktopOidcConfiguration,
        now: datetime,
    ) -> AuthenticatedDesktopIdentity:
        self._configuration(expected)
        if type(grant) is not DesktopCredentialGrant or not _GRANT.fullmatch(grant.grant_id):
            raise DesktopAuthenticationError("Desktop Cognito grant is invalid")
        subject = str(UUID(hex=_GRANT.fullmatch(grant.grant_id).group(1)))
        if (
            grant.identity.issuer != expected.issuer
            or grant.identity.client_id != expected.client_id
            or grant.identity.audience != expected.audience
            or grant.identity.tenant_id != self._scope.tenant_id
            or grant.identity.account_id != self._scope.account_id
            or grant.identity.actor != {"type": "user", "id": self._scope.user_id}
            or grant.identity.entitlement_version != self._scope.entitlement_version
        ):
            raise DesktopAuthenticationError("Desktop Cognito grant changed scope")
        with self._lock:
            current = self._current
            if (
                current is None
                or current.subject != subject
                or not hmac.compare_digest(current.refresh_token, grant.material)
                or current.expires_at <= time.time() + 60
            ):
                current = self._tokens.refresh(refresh_token=grant.material, expected_subject=subject)
            try:
                self._server_scope(current.access_token)
                identity = self._identity(current, expected=expected, nonce=grant.identity.nonce, now=now)
                if not hmac.compare_digest(current.refresh_token, grant.material):
                    self._store.save(
                        _STORE_SLOT,
                        DesktopCredentialGrant(grant_id=grant.grant_id, identity=identity, material=current.refresh_token),
                    )
            except Exception:
                self._current = None
                raise
            self._current = current
            return identity

    def revoke(self, grant: DesktopCredentialGrant) -> None:
        if type(grant) is not DesktopCredentialGrant or not _GRANT.fullmatch(grant.grant_id):
            raise DesktopAuthenticationError("Desktop Cognito grant is invalid")
        with self._lock:
            self._current = None
            self._tokens.revoke(refresh_token=grant.material)

    def access_token(self) -> str:
        with self._lock:
            current = self._current
            if current is None or current.expires_at <= time.time() + 30:
                raise DesktopAuthenticationError("Desktop Cognito access token is unavailable")
            return current.access_token


__all__ = ["DesktopCognitoAuthority", "DesktopCognitoScope"]
