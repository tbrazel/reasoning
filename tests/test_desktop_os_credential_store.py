from __future__ import annotations

import json
import sys
import types
from datetime import UTC, datetime, timedelta

import pytest

from reasoning_ledger.desktop_auth import (
    AuthenticatedDesktopIdentity,
    DesktopAuthenticationError,
    DesktopCredentialGrant,
    FakeCredentialGrant,
)
from reasoning_ledger.desktop_os_credential_store import DesktopOsCredentialStore

SLOT = "reasoning-desktop-current-session"
SERVICE = "reasoning-desktop-credential.test.invalid"
REFRESH = "test-refresh-material-secret-0123456789"


class _MemoryBackend:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def set_password(self, service: str, username: str, password: str) -> None:
        self.values[(service, username)] = password

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))

    def delete_password(self, service: str, username: str) -> None:
        self.values.pop((service, username))


def _grant() -> DesktopCredentialGrant:
    return DesktopCredentialGrant(
        grant_id="cognito_grant_001",
        identity=AuthenticatedDesktopIdentity(
            account_id="account_alpha_001",
            actor={"type": "user", "id": "user_alice_001"},
            tenant_id="tenant_alpha_001",
            issuer="https://cognito-idp.us-west-2.amazonaws.com/us-west-2_TESTALPHA",
            client_id="desktop-public-client-id",
            audience="desktop-public-client-id",
            nonce="test-nonce-value-0123456789",
            expires_at=datetime.now(UTC) + timedelta(minutes=15),
            entitlement_version="free-alpha-v1",
        ),
        material=REFRESH,
    )


def _store(backend: _MemoryBackend) -> DesktopOsCredentialStore:
    return DesktopOsCredentialStore(test_backend=backend, test_service=SERVICE)


def test_store_round_trips_one_grant_across_instances_without_exposing_secret() -> None:
    backend = _MemoryBackend()
    original = _grant()
    first = _store(backend)
    assert first.platform == sys.platform
    assert first.custody == "test-injected-os-store"
    assert first.load(SLOT) is None
    first.save(SLOT, original)
    saved = backend.values[(SERVICE, SLOT)]
    assert json.loads(saved)["material"] == REFRESH
    assert REFRESH not in repr(original)
    assert _store(backend).load(SLOT) == original
    first.delete(SLOT)
    first.delete(SLOT)
    assert _store(backend).load(SLOT) is None


def test_invalid_forged_and_noncanonical_records_fail_closed() -> None:
    backend = _MemoryBackend()
    store = _store(backend)
    with pytest.raises(DesktopAuthenticationError, match="grant"):
        store.save(SLOT, FakeCredentialGrant(**vars(_grant())))
    store.save(SLOT, _grant())
    original = backend.values[(SERVICE, SLOT)]
    invalid = [
        original.replace('"schema_version":1', '"schema_version":1,"schema_version":1'),
        original.replace('"schema_version":1', '"schema_version":2'),
        original.replace('"material":"' + REFRESH + '"', '"material":"short"'),
        original.replace('"tenant_id":"tenant_alpha_001"', '"tenant_id":"bad/tenant"'),
        original + " ",
        "not-json",
    ]
    for payload in invalid:
        backend.values[(SERVICE, SLOT)] = payload
        with pytest.raises(DesktopAuthenticationError, match="OS credential is invalid") as error:
            store.load(SLOT)
        assert REFRESH not in str(error.value)


def test_unknown_slot_and_fake_backend_outside_test_namespace_rejected(monkeypatch) -> None:
    backend = _MemoryBackend()
    with pytest.raises(DesktopAuthenticationError, match="explicit"):
        DesktopOsCredentialStore(test_backend=backend, test_service="ai.reasoning.desktop.oauth")
    with pytest.raises(DesktopAuthenticationError, match="cannot change"):
        DesktopOsCredentialStore(test_service=SERVICE)
    store = _store(backend)
    with pytest.raises(DesktopAuthenticationError, match="slot"):
        store.save("other-slot", _grant())
    monkeypatch.setitem(sys.modules, "keyring", types.SimpleNamespace(get_keyring=lambda: backend))
    with pytest.raises(DesktopAuthenticationError, match="trusted system store"):
        DesktopOsCredentialStore()


def test_failed_write_or_read_never_returns_an_untrusted_grant() -> None:
    class Broken(_MemoryBackend):
        def set_password(self, service: str, username: str, password: str) -> None:
            return

    with pytest.raises(DesktopAuthenticationError, match="not durable"):
        _store(Broken()).save(SLOT, _grant())

    class Unreadable(_MemoryBackend):
        def get_password(self, service: str, username: str) -> str:
            raise RuntimeError("secret-value-should-not-be-printed")

    with pytest.raises(DesktopAuthenticationError, match="read failed") as error:
        _store(Unreadable()).load(SLOT)
    assert "secret-value" not in str(error.value)
