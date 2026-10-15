"""Fail-closed macOS/Linux OS secret custody for one Desktop OAuth grant."""

from __future__ import annotations

import hmac
import json
import re
import sys
from collections.abc import Mapping
from datetime import datetime
from typing import Protocol

from .desktop_auth import AuthenticatedDesktopIdentity, DesktopAuthenticationError, DesktopCredentialGrant

_SERVICE = "ai.reasoning.desktop.oauth"
_SLOT = "reasoning-desktop-current-session"
_IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9._:-]{2,127}\Z")
_MAX_STORED_BYTES = 32_768
_BACKENDS = {
    "darwin": ("keyring.backends.macOS.Keyring", "os-keychain"),
    "linux": ("keyring.backends.SecretService.Keyring", "os-secret-service"),
}


class _Backend(Protocol):
    def get_password(self, service: str, username: str) -> str | None: ...

    def set_password(self, service: str, username: str, password: str) -> None: ...

    def delete_password(self, service: str, username: str) -> None: ...


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate OS credential field")
        result[key] = value
    return result


def _identifier(value: object) -> bool:
    return isinstance(value, str) and bool(_IDENTIFIER.fullmatch(value))


class DesktopOsCredentialStore:
    """Store a grant in an OS-backed secret, never in repository or app files.

    Production selects only the built-in macOS Keychain or Linux Secret Service
    keyring implementation. A fake backend may be injected only with an
    explicit ``.invalid`` test-service namespace; this never selects a
    production credential slot or consults the host's default keyring.
    """

    def __init__(self, *, test_backend: _Backend | None = None, test_service: str | None = None) -> None:
        platform = sys.platform
        if platform not in _BACKENDS:
            raise DesktopAuthenticationError("Desktop OS credential platform is unsupported")
        expected, custody = _BACKENDS[platform]
        if test_backend is None:
            if test_service is not None:
                raise DesktopAuthenticationError("production Desktop credential service cannot change")
            try:
                import keyring

                backend = keyring.get_keyring()
            except Exception:
                raise DesktopAuthenticationError("Desktop OS credential backend is unavailable") from None
            name = f"{type(backend).__module__}.{type(backend).__qualname__}"
            if name != expected:
                raise DesktopAuthenticationError("Desktop OS credential backend is not a trusted system store")
            service = _SERVICE
        else:
            if test_service != "reasoning-desktop-credential.test.invalid" or not all(
                callable(getattr(test_backend, method, None)) for method in ("get_password", "set_password", "delete_password")
            ):
                raise DesktopAuthenticationError("test Desktop credential store must be explicit")
            backend = test_backend
            service = test_service
            custody = "test-injected-os-store"
        self.platform = platform
        self.custody = custody
        self._backend = backend
        self._service = service

    @staticmethod
    def _slot(slot: str) -> None:
        if slot != _SLOT:
            raise DesktopAuthenticationError("Desktop credential-store slot is invalid")

    @staticmethod
    def _encode(grant: DesktopCredentialGrant) -> str:
        if (
            type(grant) is not DesktopCredentialGrant
            or type(grant.identity) is not AuthenticatedDesktopIdentity
            or not _identifier(grant.grant_id)
            or not isinstance(grant.material, str)
            or not 16 <= len(grant.material) <= 8192
            or any(not 33 <= ord(char) <= 126 for char in grant.material)
        ):
            raise DesktopAuthenticationError("Desktop OS credential grant is invalid")
        identity = grant.identity
        payload = json.dumps(
            {
                "schema_version": 1,
                "grant_id": grant.grant_id,
                "material": grant.material,
                "identity": {
                    "account_id": identity.account_id,
                    "actor": dict(identity.actor),
                    "tenant_id": identity.tenant_id,
                    "issuer": identity.issuer,
                    "client_id": identity.client_id,
                    "audience": identity.audience,
                    "nonce": identity.nonce,
                    "expires_at": identity.expires_at.isoformat(),
                    "entitlement_version": identity.entitlement_version,
                },
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        if len(payload.encode("utf-8")) > _MAX_STORED_BYTES:
            raise DesktopAuthenticationError("Desktop OS credential grant is oversized")
        return payload

    @staticmethod
    def _decode(payload: str) -> DesktopCredentialGrant:
        try:
            if not isinstance(payload, str) or len(payload.encode("utf-8")) > _MAX_STORED_BYTES:
                raise ValueError("OS credential is invalid or oversized")
            record = json.loads(payload, object_pairs_hook=_unique)
            if not isinstance(record, dict) or set(record) != {"schema_version", "grant_id", "material", "identity"}:
                raise ValueError("OS credential shape is invalid")
            if type(record["schema_version"]) is not int or record["schema_version"] != 1:
                raise ValueError("OS credential version is invalid")
            identity = record["identity"]
            if not isinstance(identity, dict) or set(identity) != {
                "account_id",
                "actor",
                "tenant_id",
                "issuer",
                "client_id",
                "audience",
                "nonce",
                "expires_at",
                "entitlement_version",
            }:
                raise ValueError("OS credential identity is invalid")
            if not isinstance(identity["actor"], Mapping) or set(identity["actor"]) != {"type", "id"}:
                raise ValueError("OS credential actor is invalid")
            if not isinstance(identity["expires_at"], str):
                raise ValueError("OS credential expiry is invalid")
            authenticated = AuthenticatedDesktopIdentity(
                account_id=identity["account_id"],
                actor=identity["actor"],
                tenant_id=identity["tenant_id"],
                issuer=identity["issuer"],
                client_id=identity["client_id"],
                audience=identity["audience"],
                nonce=identity["nonce"],
                expires_at=datetime.fromisoformat(identity["expires_at"]),
                entitlement_version=identity["entitlement_version"],
            )
            grant = DesktopCredentialGrant(
                grant_id=record["grant_id"],
                identity=authenticated,
                material=record["material"],
            )
            if DesktopOsCredentialStore._encode(grant) != payload:
                raise ValueError("OS credential is not canonical")
            return grant
        except (KeyError, TypeError, ValueError, OverflowError, DesktopAuthenticationError):
            raise DesktopAuthenticationError("Desktop OS credential is invalid") from None

    def save(self, slot: str, grant: DesktopCredentialGrant) -> None:
        self._slot(slot)
        payload = self._encode(grant)
        try:
            self._backend.set_password(self._service, slot, payload)
            observed = self._backend.get_password(self._service, slot)
        except Exception:
            raise DesktopAuthenticationError("Desktop OS credential write failed") from None
        if not isinstance(observed, str) or not hmac.compare_digest(observed, payload):
            raise DesktopAuthenticationError("Desktop OS credential write was not durable")

    def load(self, slot: str) -> DesktopCredentialGrant | None:
        self._slot(slot)
        try:
            payload = self._backend.get_password(self._service, slot)
        except Exception:
            raise DesktopAuthenticationError("Desktop OS credential read failed") from None
        return None if payload is None else self._decode(payload)

    def delete(self, slot: str) -> None:
        self._slot(slot)
        try:
            if self._backend.get_password(self._service, slot) is not None:
                self._backend.delete_password(self._service, slot)
        except Exception:
            raise DesktopAuthenticationError("Desktop OS credential deletion failed") from None


__all__ = ["DesktopOsCredentialStore"]
