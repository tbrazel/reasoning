"""Credential-redacting Desktop authentication state machine.

The state machine is provider-neutral.  This module retains conspicuously fake
adapters for deterministic tests, while the live OIDC authority, system-browser
launcher, and operating-system credential-store adapters remain separately
injectable.  No provider coordinate or credential is embedded here.
"""

from __future__ import annotations

import base64
import hashlib
import re
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import NoReturn, Protocol, runtime_checkable
from urllib.parse import urlparse

from .cloud_client import require_credential_free
from .desktop_cloud import DesktopAuthenticatedCloudBoundary, DesktopCloudController

JsonObject = dict[str, object]

_SAFE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]{2,127}$")
_PUBLIC_CLIENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
_STORE_SLOT = "reasoning-desktop-current-session"
_LEGACY_FAKE_STORE_SLOT = "reasoning-desktop-current-fake-session"
DESKTOP_LOOPBACK_CALLBACK_PORTS = range(49152, 49168)


class DesktopAuthenticationError(RuntimeError):
    """A Desktop authentication transition failed closed."""


class DesktopAuthenticationStageError(DesktopAuthenticationError):
    """One fixed, non-secret stage for a failed post-OIDC transition."""

    _CODES = frozenset({"post_auth_boundary", "desktop_cloud_state", "credential_store"})

    def __init__(self, diagnostic_code: str) -> None:
        if diagnostic_code not in self._CODES:
            raise ValueError("Desktop authentication stage code is invalid")
        super().__init__("Desktop authentication could not complete its local setup")
        self.diagnostic_code = diagnostic_code


def _now(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise DesktopAuthenticationError("Desktop authentication clock must be timezone-aware")
    return value


def _safe(value: object, label: str) -> str:
    if not isinstance(value, str) or _SAFE_ID.fullmatch(value) is None:
        raise DesktopAuthenticationError(f"{label} is invalid")
    require_credential_free(value)
    return value


def _challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")


@dataclass(frozen=True)
class DesktopOidcConfiguration:
    """Public identity coordinates accepted by the provider-neutral controller."""

    issuer: str
    client_id: str
    audience: str
    redirect_uri: str
    tenant_id: str
    authorization_ttl_seconds: int = 300
    session_ttl_seconds: int = 900

    def __post_init__(self) -> None:
        values = (self.issuer, self.client_id, self.audience, self.redirect_uri, self.tenant_id)
        require_credential_free(list(values))
        issuer = urlparse(self.issuer)
        redirect = urlparse(self.redirect_uri)
        loopback_redirect = (
            redirect.scheme == "http"
            and redirect.hostname == "127.0.0.1"
            and redirect.path == "/auth/callback"
            and redirect.netloc in {
                f"127.0.0.1:{port}" for port in DESKTOP_LOOPBACK_CALLBACK_PORTS
            }
        )
        if (
            issuer.scheme != "https"
            or issuer.hostname is None
            or issuer.username is not None
            or issuer.password is not None
            or issuer.query
            or issuer.fragment
            or redirect.username is not None
            or redirect.password is not None
            or redirect.query
            or redirect.fragment
            or not (
                redirect.scheme == "reasoning"
                and redirect.netloc == "auth"
                and redirect.path == "/callback"
                or redirect.scheme == "https"
                and redirect.hostname is not None
                or loopback_redirect
            )
            or _PUBLIC_CLIENT_ID.fullmatch(self.client_id) is None
            or _PUBLIC_CLIENT_ID.fullmatch(self.audience) is None
            or _SAFE_ID.fullmatch(self.tenant_id) is None
            or not 30 <= self.authorization_ttl_seconds <= 600
            or not 60 <= self.session_ttl_seconds <= 3600
        ):
            raise DesktopAuthenticationError("Desktop OIDC configuration is invalid")


@dataclass(frozen=True)
class FakeOidcConfiguration(DesktopOidcConfiguration):
    """Explicit test-only identity coordinates; live values are rejected."""

    def __post_init__(self) -> None:
        try:
            super().__post_init__()
        except DesktopAuthenticationError as error:
            raise DesktopAuthenticationError("only explicit .invalid fake OIDC configuration is accepted") from error
        issuer = urlparse(self.issuer)
        redirect = urlparse(self.redirect_uri)
        if (
            issuer.hostname is None
            or not issuer.hostname.endswith(".invalid")
            or redirect.scheme != "https"
            or redirect.hostname is None
            or not redirect.hostname.endswith(".invalid")
            or not self.client_id.startswith("test-")
            or not self.audience.startswith("test-")
            or not self.tenant_id.startswith("tenant_test_")
        ):
            raise DesktopAuthenticationError("only explicit .invalid fake OIDC configuration is accepted")


@dataclass(frozen=True)
class DesktopAuthorizationRequest:
    attempt_id: str
    issuer: str
    client_id: str
    audience: str
    redirect_uri: str
    code_challenge_method: str
    expires_at: datetime
    state: str = field(repr=False)
    nonce: str = field(repr=False)
    code_challenge: str = field(repr=False)


@dataclass(frozen=True)
class DesktopAuthorizationCallback:
    attempt_id: str
    redirect_uri: str
    state: str = field(repr=False)
    code: str = field(repr=False)


@dataclass(frozen=True)
class AuthenticatedDesktopIdentity:
    account_id: str
    actor: Mapping[str, str]
    tenant_id: str
    issuer: str
    client_id: str
    audience: str
    nonce: str = field(repr=False)
    expires_at: datetime
    entitlement_version: str

    def __post_init__(self) -> None:
        if not isinstance(self.actor, Mapping) or set(self.actor) != {"type", "id"}:
            raise DesktopAuthenticationError("Desktop identity actor has an unsupported shape")
        actor = {
            "type": _safe(self.actor.get("type"), "actor type"),
            "id": _safe(self.actor.get("id"), "actor id"),
        }
        object.__setattr__(self, "actor", MappingProxyType(actor))
        _safe(self.account_id, "account id")
        _safe(self.tenant_id, "tenant id")
        _safe(self.entitlement_version, "entitlement version")
        if not isinstance(self.expires_at, datetime) or self.expires_at.tzinfo is None:
            raise DesktopAuthenticationError("Desktop identity expiry must be timezone-aware")
        require_credential_free([self.issuer, self.client_id, self.audience, self.nonce])

    def presentation(self) -> JsonObject:
        return {
            "account_id": self.account_id,
            "actor": dict(self.actor),
            "tenant_id": self.tenant_id,
            "expires_at": self.expires_at.isoformat(),
            "entitlement_version": self.entitlement_version,
        }


@dataclass(frozen=True)
class DesktopCredentialGrant:
    grant_id: str
    identity: AuthenticatedDesktopIdentity
    material: str = field(repr=False)


@dataclass(frozen=True)
class DesktopOidcExchange:
    identity: AuthenticatedDesktopIdentity
    grant: DesktopCredentialGrant = field(repr=False)


@dataclass(frozen=True)
class FakeCredentialGrant(DesktopCredentialGrant):
    """Test-only credential grant."""


@dataclass(frozen=True)
class FakeOidcExchange(DesktopOidcExchange):
    """Test-only OIDC exchange result."""

    grant: FakeCredentialGrant = field(repr=False)


@runtime_checkable
class DesktopAuthorizationAuthority(Protocol):
    def exchange(
        self,
        callback: DesktopAuthorizationCallback,
        *,
        verifier: str,
        expected: DesktopOidcConfiguration,
        expected_nonce: str,
        now: datetime,
    ) -> DesktopOidcExchange: ...

    def validate_grant(
        self,
        grant: DesktopCredentialGrant,
        *,
        expected: DesktopOidcConfiguration,
        now: datetime,
    ) -> AuthenticatedDesktopIdentity: ...

    def revoke(self, grant: DesktopCredentialGrant) -> None: ...


@runtime_checkable
class DesktopBrowserAuthorizationLauncher(Protocol):
    def launch(self, request: DesktopAuthorizationRequest) -> None: ...


@runtime_checkable
class DesktopCredentialStore(Protocol):
    platform: str
    custody: str

    def save(self, slot: str, grant: DesktopCredentialGrant) -> None: ...

    def load(self, slot: str) -> DesktopCredentialGrant | None: ...

    def delete(self, slot: str) -> None: ...


@runtime_checkable
class DesktopPostAuthProviderFactory(Protocol):
    def create(self, identity: AuthenticatedDesktopIdentity) -> DesktopAuthenticatedCloudBoundary: ...


@runtime_checkable
class DesktopAuthEntropy(Protocol):
    """One-use state, nonce, and PKCE verifier source."""

    def next(self, label: str) -> str: ...


class FakeDesktopAuthEntropy:
    """Deterministic fake correlation material for offline tests only."""

    _instance_lock = threading.Lock()
    _instance_count = 0

    def __init__(self) -> None:
        with self._instance_lock:
            type(self)._instance_count += 1
            self._instance_id = type(self)._instance_count
        self._lock = threading.Lock()
        self._counter = 0

    def next(self, label: str) -> str:
        with self._lock:
            self._counter += 1
            if label == "verifier":
                return f"TEST_ONLY_PKCE_VERIFIER_{self._instance_id:08d}_{self._counter:08d}_NOT_A_CREDENTIAL"
            return f"test-fakeentropy-{self._instance_id:08d}-{label}-{self._counter:08d}"


class FakeSystemBrowserLauncher:
    """Records a fake launch without opening any browser or URL."""

    def __init__(self) -> None:
        self._requests: dict[str, DesktopAuthorizationRequest] = {}

    def launch(self, request: DesktopAuthorizationRequest) -> None:
        if request.attempt_id in self._requests:
            raise DesktopAuthenticationError("fake browser attempt identity was reused")
        self._requests[request.attempt_id] = request

    def complete(
        self,
        authority: FakeOidcAuthority,
        attempt_id: str,
        *,
        overrides: Mapping[str, object] | None = None,
    ) -> DesktopAuthorizationCallback:
        try:
            request = self._requests[attempt_id]
        except KeyError as error:
            raise DesktopAuthenticationError("fake browser attempt is unavailable") from error
        return authority.authorize(request, overrides=overrides)

    def presentation(self) -> JsonObject:
        return {"implementation": "fake", "launch_count": len(self._requests), "opened_browser": False}


class FakeDesktopCredentialStore:
    """In-memory simulation of macOS or Linux credential-store lifecycle."""

    def __init__(self, platform: str) -> None:
        if platform not in {"darwin", "linux"}:
            raise DesktopAuthenticationError("fake Desktop credential store supports only macOS and Linux")
        self.platform = platform
        self.custody = f"fake-{platform}-store"
        self._values: dict[str, FakeCredentialGrant] = {}
        self._events: list[str] = []

    @staticmethod
    def _slot(slot: str) -> str:
        if slot not in {_STORE_SLOT, _LEGACY_FAKE_STORE_SLOT}:
            raise DesktopAuthenticationError("fake credential-store slot is invalid")
        return _STORE_SLOT

    def save(self, slot: str, grant: FakeCredentialGrant) -> None:
        if not isinstance(grant, FakeCredentialGrant):
            raise DesktopAuthenticationError("fake credential-store write is invalid")
        self._values[self._slot(slot)] = grant
        self._events.append("save")

    def load(self, slot: str) -> FakeCredentialGrant | None:
        self._events.append("load")
        return self._values.get(self._slot(slot))

    def delete(self, slot: str) -> None:
        self._values.pop(self._slot(slot), None)
        self._events.append("delete")

    def presentation(self) -> JsonObject:
        return {
            "implementation": "fake-memory",
            "platform": self.platform,
            "contains_session": _STORE_SLOT in self._values,
            "events": list(self._events),
            "os_store_accessed": False,
        }


class FakeOidcAuthority:
    """Deterministic one-use code authority with no signing or network path."""

    _OVERRIDES = {
        "issuer",
        "client_id",
        "audience",
        "redirect_uri",
        "state",
        "nonce",
        "tenant_id",
        "actor",
        "account_id",
        "code_challenge",
        "expires_at",
        "session_expires_at",
        "entitlement_version",
    }

    def __init__(self, configuration: FakeOidcConfiguration, *, clock: Callable[[], datetime]) -> None:
        self.configuration = configuration
        self._clock = clock
        self._codes: dict[str, JsonObject] = {}
        self._grants: dict[str, FakeCredentialGrant] = {}
        self._used_codes: set[str] = set()
        self._revoked: set[str] = set()
        self._counter = 0

    def authorize(
        self,
        request: DesktopAuthorizationRequest,
        *,
        overrides: Mapping[str, object] | None = None,
    ) -> DesktopAuthorizationCallback:
        if (
            request.issuer != self.configuration.issuer
            or request.client_id != self.configuration.client_id
            or request.audience != self.configuration.audience
            or request.redirect_uri != self.configuration.redirect_uri
            or request.code_challenge_method != "S256"
        ):
            raise DesktopAuthenticationError("fake authorization request configuration changed")
        changes = dict(overrides or {})
        if set(changes).difference(self._OVERRIDES):
            raise DesktopAuthenticationError("fake authority override is unsupported")
        self._counter += 1
        code = f"test-authorization-code-{self._counter:08d}"
        values: JsonObject = {
            "issuer": request.issuer,
            "client_id": request.client_id,
            "audience": request.audience,
            "redirect_uri": request.redirect_uri,
            "state": request.state,
            "nonce": request.nonce,
            "tenant_id": self.configuration.tenant_id,
            "actor": {"type": "user", "id": "user_test_alice"},
            "account_id": "account_test_alice",
            "code_challenge": request.code_challenge,
            "expires_at": request.expires_at,
            "session_expires_at": _now(self._clock) + timedelta(seconds=self.configuration.session_ttl_seconds),
            "entitlement_version": "test-entitlements-v1",
        }
        values.update(changes)
        self._codes[code] = values
        return DesktopAuthorizationCallback(
            attempt_id=request.attempt_id,
            redirect_uri=str(values["redirect_uri"]),
            state=str(values["state"]),
            code=code,
        )

    def exchange(
        self,
        callback: DesktopAuthorizationCallback,
        *,
        verifier: str,
        expected: FakeOidcConfiguration,
        expected_nonce: str,
        now: datetime,
    ) -> FakeOidcExchange:
        require_credential_free([callback.code, callback.state, callback.redirect_uri, verifier])
        if callback.code in self._used_codes:
            raise DesktopAuthenticationError("authorization callback was already consumed")
        try:
            values = self._codes[callback.code]
        except KeyError as error:
            raise DesktopAuthenticationError("authorization callback code is unknown") from error
        if not isinstance(values["expires_at"], datetime) or now >= values["expires_at"]:
            raise DesktopAuthenticationError("authorization callback expired")
        comparisons = (
            (values["issuer"], expected.issuer, "issuer"),
            (values["client_id"], expected.client_id, "client"),
            (values["audience"], expected.audience, "audience"),
            (values["redirect_uri"], expected.redirect_uri, "redirect"),
            (values["state"], callback.state, "state"),
            (values["nonce"], expected_nonce, "nonce"),
            (values["tenant_id"], expected.tenant_id, "tenant"),
            (values["code_challenge"], _challenge(verifier), "PKCE"),
        )
        for observed, required, label in comparisons:
            if observed != required:
                raise DesktopAuthenticationError(f"fake OIDC {label} binding failed")
        actor = values["actor"]
        if not isinstance(actor, dict) or set(actor) != {"type", "id"}:
            raise DesktopAuthenticationError("fake OIDC actor is invalid")
        session_expires_at = values["session_expires_at"]
        if not isinstance(session_expires_at, datetime) or session_expires_at.tzinfo is None or now >= session_expires_at:
            raise DesktopAuthenticationError("fake OIDC identity assertion expired")
        self._used_codes.add(callback.code)
        identity = AuthenticatedDesktopIdentity(
            account_id=_safe(values["account_id"], "account id"),
            actor={"type": _safe(actor["type"], "actor type"), "id": _safe(actor["id"], "actor id")},
            tenant_id=_safe(values["tenant_id"], "tenant id"),
            issuer=str(values["issuer"]),
            client_id=str(values["client_id"]),
            audience=str(values["audience"]),
            nonce=str(values["nonce"]),
            expires_at=session_expires_at,
            entitlement_version=_safe(values["entitlement_version"], "entitlement version"),
        )
        grant_id = f"test-grant-{self._counter:08d}"
        grant = FakeCredentialGrant(
            grant_id=grant_id,
            identity=identity,
            material=f"TEST_ONLY_REFRESH_MATERIAL_{self._counter:08d}_CANNOT_AUTHENTICATE",
        )
        self._grants[grant_id] = grant
        return FakeOidcExchange(identity=identity, grant=grant)

    def validate_grant(
        self,
        grant: FakeCredentialGrant,
        *,
        expected: FakeOidcConfiguration,
        now: datetime,
    ) -> AuthenticatedDesktopIdentity:
        if self._grants.get(grant.grant_id) != grant or grant.grant_id in self._revoked:
            raise DesktopAuthenticationError("fake Desktop session is revoked or unknown")
        identity = grant.identity
        if (
            now >= identity.expires_at
            or identity.issuer != expected.issuer
            or identity.client_id != expected.client_id
            or identity.audience != expected.audience
            or identity.tenant_id != expected.tenant_id
        ):
            raise DesktopAuthenticationError("fake Desktop session expired or changed scope")
        return identity

    def revoke(self, grant: FakeCredentialGrant) -> None:
        if grant.grant_id in self._grants:
            self._revoked.add(grant.grant_id)


@dataclass
class _PendingAttempt:
    request: DesktopAuthorizationRequest
    verifier: str = field(repr=False)


class DesktopAuthenticationController:
    """Credential-redacting state machine in front of the closed Cloud seam."""

    def __init__(
        self,
        configuration: DesktopOidcConfiguration,
        *,
        authority: DesktopAuthorizationAuthority,
        browser: DesktopBrowserAuthorizationLauncher,
        credential_store: DesktopCredentialStore,
        provider_factory: DesktopPostAuthProviderFactory,
        cloud: DesktopCloudController,
        entropy: DesktopAuthEntropy,
        clock: Callable[[], datetime],
    ) -> None:
        if (
            not isinstance(configuration, DesktopOidcConfiguration)
            or not isinstance(cloud, DesktopCloudController)
            or not isinstance(entropy, DesktopAuthEntropy)
            or getattr(credential_store, "platform", None) not in {"darwin", "linux"}
            or not isinstance(getattr(credential_store, "custody", None), str)
            or not all(
                (
                    isinstance(authority, DesktopAuthorizationAuthority),
                    isinstance(browser, DesktopBrowserAuthorizationLauncher),
                    isinstance(credential_store, DesktopCredentialStore),
                    isinstance(provider_factory, DesktopPostAuthProviderFactory),
                )
            )
        ):
            raise DesktopAuthenticationError("Desktop authentication dependencies do not implement the required seams")
        self._configuration = configuration
        self._authority = authority
        self._browser = browser
        self._store = credential_store
        self._provider_factory = provider_factory
        self._cloud = cloud
        self._entropy = entropy
        self._clock = clock
        self._lock = threading.RLock()
        self._pending: _PendingAttempt | None = None
        self._identity: AuthenticatedDesktopIdentity | None = None
        self._last_callback_failure: str | None = None

    def presentation(self) -> JsonObject:
        with self._lock:
            pending = self._pending
            identity = self._identity
            failure = self._last_callback_failure
        cloud_connected = self._cloud.readiness()["status"] == "connected"
        if identity is not None:
            return {
                "state": "authenticated",
                "identity": identity.presentation(),
                "cloud_connected": cloud_connected,
                "credential_custody": self._store.custody,
            }
        result: JsonObject = {
            "state": "awaiting_callback" if pending is not None else "signed_out",
            "attempt_id": pending.request.attempt_id if pending is not None else None,
            "cloud_connected": cloud_connected,
            "credential_custody": self._store.custody,
        }
        if failure is not None:
            result["failure_code"] = failure
        return result

    def record_callback_failure(self, diagnostic_code: str) -> None:
        """Retain one fixed public code so the Desktop UI does not lose it."""

        if not isinstance(diagnostic_code, str) or _SAFE_ID.fullmatch(diagnostic_code) is None:
            raise DesktopAuthenticationError("Desktop callback diagnostic is invalid")
        require_credential_free(diagnostic_code)
        with self._lock:
            self._last_callback_failure = diagnostic_code

    def begin_sign_in(self) -> JsonObject:
        with self._lock:
            if self._identity is not None or self._pending is not None:
                raise DesktopAuthenticationError("Desktop authentication is already active")
            self._last_callback_failure = None
            now = _now(self._clock)
            attempt_id = _safe(self._entropy.next("attempt"), "attempt id")
            state = _safe(self._entropy.next("state"), "authorization state")
            nonce = _safe(self._entropy.next("nonce"), "authorization nonce")
            verifier = self._entropy.next("verifier")
            require_credential_free(verifier)
            request = DesktopAuthorizationRequest(
                attempt_id=attempt_id,
                issuer=self._configuration.issuer,
                client_id=self._configuration.client_id,
                audience=self._configuration.audience,
                redirect_uri=self._configuration.redirect_uri,
                code_challenge_method="S256",
                expires_at=now + timedelta(seconds=self._configuration.authorization_ttl_seconds),
                state=state,
                nonce=nonce,
                code_challenge=_challenge(verifier),
            )
            self._pending = _PendingAttempt(request=request, verifier=verifier)
            try:
                self._browser.launch(request)
            except Exception:
                self._pending = None
                raise
        return self.presentation()

    def accept_callback(self, callback: DesktopAuthorizationCallback) -> JsonObject:
        if not isinstance(callback, DesktopAuthorizationCallback):
            raise DesktopAuthenticationError("Desktop authorization callback has an unsupported shape")
        require_credential_free([callback.attempt_id, callback.redirect_uri, callback.state, callback.code])
        with self._lock:
            pending = self._pending
            if pending is None:
                raise DesktopAuthenticationError("no Desktop authorization callback is pending")
            request = pending.request
            if (
                callback.attempt_id != request.attempt_id
                or callback.redirect_uri != self._configuration.redirect_uri
                or callback.state != request.state
                or _now(self._clock) >= request.expires_at
            ):
                self._pending = None
                raise DesktopAuthenticationError("Desktop authorization callback binding failed")
            try:
                exchanged: DesktopOidcExchange | None = None
                exchanged = self._authority.exchange(
                    callback,
                    verifier=pending.verifier,
                    expected=self._configuration,
                    expected_nonce=request.nonce,
                    now=_now(self._clock),
                )
                identity = exchanged.identity
                self._validate_identity(identity, request.nonce)
                try:
                    boundary = self._provider_factory.create(identity)
                except Exception:
                    raise DesktopAuthenticationStageError("post_auth_boundary") from None
                try:
                    self._cloud.bind_authenticated_boundary(
                        boundary,
                        account_id=identity.account_id,
                        entitlement_version=identity.entitlement_version,
                        actor=identity.actor,
                        tenant_id=identity.tenant_id,
                        session_guard=self._guard_current_session,
                    )
                except Exception:
                    raise DesktopAuthenticationStageError("desktop_cloud_state") from None
                try:
                    self._store.save(_STORE_SLOT, exchanged.grant)
                except DesktopAuthenticationError:
                    raise
                except Exception:
                    raise DesktopAuthenticationStageError("credential_store") from None
            except Exception as error:
                self._pending = None
                if exchanged is not None:
                    try:
                        self._authority.revoke(exchanged.grant)
                    except Exception as cleanup_error:
                        self._retain_primary(error, cleanup_error, "grant revocation")
                try:
                    self._store.delete(_STORE_SLOT)
                except Exception as cleanup_error:
                    self._retain_primary(error, cleanup_error, "credential-store deletion")
                try:
                    self._cloud.invalidate_authenticated_provider()
                except Exception as cleanup_error:
                    self._retain_primary(error, cleanup_error, "Cloud-provider detachment")
                raise
            self._pending = None
            self._identity = identity
            self._last_callback_failure = None
        return self.presentation()

    def _validate_identity(self, identity: AuthenticatedDesktopIdentity, nonce: str) -> None:
        if (
            not isinstance(identity, AuthenticatedDesktopIdentity)
            or identity.issuer != self._configuration.issuer
            or identity.client_id != self._configuration.client_id
            or identity.audience != self._configuration.audience
            or identity.tenant_id != self._configuration.tenant_id
            or identity.nonce != nonce
            or _now(self._clock) >= identity.expires_at
        ):
            raise DesktopAuthenticationError("Desktop identity assertion changed scope or expired")
        require_credential_free(identity.presentation())

    def resume(self) -> JsonObject:
        with self._lock:
            if self._identity is not None or self._pending is not None:
                raise DesktopAuthenticationError("Desktop authentication is already active")
            self._last_callback_failure = None
            grant = self._store.load(_STORE_SLOT)
            if grant is None:
                raise DesktopAuthenticationError("Desktop credential store contains no session")
            try:
                identity = self._authority.validate_grant(
                    grant,
                    expected=self._configuration,
                    now=_now(self._clock),
                )
                self._validate_identity(identity, identity.nonce)
                boundary = self._provider_factory.create(identity)
                self._cloud.bind_authenticated_boundary(
                    boundary,
                    account_id=identity.account_id,
                    entitlement_version=identity.entitlement_version,
                    actor=identity.actor,
                    tenant_id=identity.tenant_id,
                    session_guard=self._guard_current_session,
                )
            except Exception as error:
                try:
                    self._store.delete(_STORE_SLOT)
                except Exception as cleanup_error:
                    self._retain_primary(error, cleanup_error, "credential-store deletion")
                try:
                    self._cloud.invalidate_authenticated_provider()
                except Exception as cleanup_error:
                    self._retain_primary(error, cleanup_error, "Cloud-provider detachment")
                raise
            self._identity = identity
            self._last_callback_failure = None
        return self.presentation()

    def validate_session(self) -> JsonObject:
        self._validate_current_session()
        return self.presentation()

    def _guard_current_session(self) -> None:
        """Credential-free callback used before auth-owned provider access."""

        self._validate_current_session()

    def _validate_current_session(self) -> AuthenticatedDesktopIdentity:
        with self._lock:
            identity = self._identity
        if identity is None:
            raise DesktopAuthenticationError("Desktop is not authenticated")
        try:
            grant = self._store.load(_STORE_SLOT)
            if grant is None:
                raise DesktopAuthenticationError("Desktop credential-store session is unavailable")
            current = self._authority.validate_grant(
                grant,
                expected=self._configuration,
                now=_now(self._clock),
            )
            self._validate_identity(current, identity.nonce)
            if (
                current.account_id != identity.account_id
                or dict(current.actor) != dict(identity.actor)
                or current.tenant_id != identity.tenant_id
                or current.entitlement_version != identity.entitlement_version
            ):
                raise DesktopAuthenticationError("Desktop session account or entitlement scope changed")
            with self._lock:
                if self._identity is identity:
                    self._identity = current
        except Exception as error:
            self._fail_closed(error)
        return current

    def logout(self) -> JsonObject:
        primary: Exception | None = None
        grant: DesktopCredentialGrant | None = None
        try:
            grant = self._store.load(_STORE_SLOT)
        except Exception as error:
            primary = error
        if grant is not None:
            try:
                self._authority.revoke(grant)
            except Exception as error:
                primary = self._retain_primary(primary, error, "grant revocation")
        with self._lock:
            self._pending = None
            self._identity = None
            self._last_callback_failure = None
        try:
            self._store.delete(_STORE_SLOT)
        except Exception as error:
            primary = self._retain_primary(primary, error, "credential-store deletion")
        try:
            self._cloud.invalidate_authenticated_provider()
        except Exception as error:
            primary = self._retain_primary(primary, error, "Cloud-provider detachment")
        if primary is not None:
            raise primary
        return self.presentation()

    def _fail_closed(self, primary: Exception) -> NoReturn:
        with self._lock:
            self._pending = None
            self._identity = None
        try:
            self._store.delete(_STORE_SLOT)
        except Exception as error:
            self._retain_primary(primary, error, "credential-store deletion")
        try:
            self._cloud.invalidate_authenticated_provider()
        except Exception as error:
            self._retain_primary(primary, error, "Cloud-provider detachment")
        raise primary

    @staticmethod
    def _retain_primary(primary: Exception | None, secondary: Exception, label: str) -> Exception:
        if primary is None:
            return secondary
        primary.add_note(f"secondary {label} cleanup failed ({type(secondary).__name__})")
        return primary


__all__ = [
    "AuthenticatedDesktopIdentity",
    "DesktopAuthenticationController",
    "DesktopAuthenticationError",
    "DesktopAuthEntropy",
    "DesktopAuthorizationCallback",
    "DesktopAuthorizationAuthority",
    "DesktopBrowserAuthorizationLauncher",
    "DesktopAuthorizationRequest",
    "DesktopCredentialStore",
    "DesktopCredentialGrant",
    "DesktopOidcConfiguration",
    "DesktopOidcExchange",
    "DesktopPostAuthProviderFactory",
    "FakeCredentialGrant",
    "FakeDesktopAuthEntropy",
    "FakeDesktopCredentialStore",
    "FakeOidcAuthority",
    "FakeOidcConfiguration",
    "FakeOidcExchange",
    "FakeSystemBrowserLauncher",
]
