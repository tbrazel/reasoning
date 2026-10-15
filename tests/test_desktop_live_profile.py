from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from reasoning_ledger.desktop_auth import DesktopAuthenticationError, DesktopOidcConfiguration
from reasoning_ledger.desktop_live_profile import load_desktop_live_profile

ROOT = Path(__file__).resolve().parents[1]


def test_provider_profile_requires_explicit_operator_configuration(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("REASONING_DESKTOP_PROFILE", raising=False)
    with pytest.raises(DesktopAuthenticationError, match="explicitly configured"):
        load_desktop_live_profile()
    document = {
        "schema_version": 1,
        "api_origin": "https://provider.example.org",
        "login_origin": "https://fixture.auth.us-west-2.amazoncognito.com",
        "user_pool_id": "us-west-2_Fixture123",
        "desktop_client_id": "7fixturepublicclient",
        "redirect_uri": "http://127.0.0.1:49152/auth/callback",
        "account_id": "account_fixture",
        "user_id": "user_fixture",
        "tenant_id": "tenant_fixture",
        "entitlement_version": "entitlement_fixture_v1",
    }
    path = tmp_path / "operator-profile.json"
    path.write_text(json.dumps(document))
    monkeypatch.setenv("REASONING_DESKTOP_PROFILE", str(path))
    profile = load_desktop_live_profile()
    assert profile.api_origin.startswith("https://")
    assert profile.login_origin.startswith("https://")
    assert profile.redirect_uri == "http://127.0.0.1:49152/auth/callback"
    assert profile.account_id == "account_fixture"
    # Cognito assigns alphanumeric public client IDs, including digit-first
    # values. Operator profiles must pass the Desktop auth boundary.
    configuration = DesktopOidcConfiguration(
        issuer=f"https://cognito-idp.us-west-2.amazonaws.com/{profile.user_pool_id}",
        client_id=profile.desktop_client_id,
        audience=profile.desktop_client_id,
        redirect_uri=profile.redirect_uri,
        tenant_id=profile.tenant_id,
    )
    assert configuration.client_id == profile.desktop_client_id
    assert not set(document).intersection({"password", "client_secret", "refresh_token", "access_token", "aws_access_key_id"})
    assert not (ROOT / "src/reasoning_ledger/assets/private_alpha_profile.json").exists()
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert "assets/*.json" in metadata["tool"]["setuptools"]["package-data"]["reasoning_ledger"]
    spec = (ROOT / "desktop/reasoning-backend.spec").read_text()
    assert 'collect_data_files("reasoning_ledger")' in spec


@pytest.mark.parametrize(
    "payload",
    [
        b"{}",
        b'{"schema_version":1,"schema_version":1}',
        b"not json",
    ],
)
def test_provider_profile_rejects_unexpected_or_duplicate_fields(tmp_path: Path, payload: bytes) -> None:
    path = tmp_path / "profile.json"
    path.write_bytes(payload)
    with pytest.raises(DesktopAuthenticationError):
        load_desktop_live_profile(path)
