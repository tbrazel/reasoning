"""Existing deployment compute wizard boundaries; no paid jobs or SSH in these tests.

Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
"""

import pytest

from reasoning_ledger.deployment import compute_setup
from reasoning_ledger.deployment.host import InstallError
from reasoning_ledger.deployment.private import read_document, write_document


@pytest.fixture
def setup(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    state = tmp_path / "operator"
    state.mkdir(mode=0o700)
    config = {"public_url": "https://chat.example.org", "token_file": "/private/token", "identity": "/private/key"}
    write_document(state / "setup.json", config)
    write_document(state / "plan.json", {"plan": {"public_url": config["public_url"]}})
    write_document(state / "setup-completion.json", {"organization_id": "c" * 32, "plan_id": "d" * 64})
    current = {"release_id": "a" * 64, "bundle_sha256": "b" * 64, "public_url": config["public_url"]}
    monkeypatch.setattr(compute_setup, "current_binding", lambda _: current)
    monkeypatch.setattr(compute_setup, "archived_release", lambda *_: state / "installed.tar.gz")
    monkeypatch.setattr(compute_setup, "inspect_bundle", lambda *_: ({"installation_features": ["modal-relay-v1"]}, "a" * 64, "b" * 64))
    monkeypatch.setattr(compute_setup, "save_release_catalog", lambda *_: None)
    monkeypatch.setattr(compute_setup, "ask", lambda prompt, default=None: default or "owner@example.org")
    monkeypatch.setattr(compute_setup.getpass, "getpass", lambda _: "password-not-persisted")
    calls = []
    monkeypatch.setattr(compute_setup, "approve", lambda *_: calls.append("consent"))
    monkeypatch.setattr(compute_setup, "temporary_ssh_cidr", lambda *_: "203.0.113.1/32")
    monkeypatch.setattr(compute_setup, "_ssh_action", lambda *args: (calls.append("open") or config, {"phase": "open"}))
    monkeypatch.setattr(
        compute_setup, "close_ssh_with_token_recovery", lambda *args: (calls.append("close") or config, {"phase": "closed"})
    )
    value = {"workers": 2, "monthly_cpu_seconds": 10000}
    return state, calls, value


@pytest.mark.parametrize("independent_operator", [False, True])
def test_compute_setup_confirms_before_mutation_keeps_password_in_memory_and_closes_ssh(setup, independent_operator):
    state, calls, value = setup
    config = {"token_file": "/private/token", "identity": "/private/key"}
    if independent_operator:
        write_document(state / "setup.json", config)

    def install(selection, config, owner, org, private, remote):
        assert calls == ["verify", "consent", "open"] and selection == value
        assert owner["password"] == "password-not-persisted" and org == "c" * 32
        assert config["public_url"] == "https://chat.example.org"
        assert "password" not in read_document(private / "target.json")
        calls.append("install")
        return {"setup_id": "e" * 64, "relays_active": 2, "live_compute_verified": False}

    result = compute_setup.run_compute_setup(
        state, None, collect=lambda *_: value, verify=lambda _: calls.append("verify"), install=install
    )
    assert calls == ["verify", "consent", "open", "install", "close"]
    assert result["ssh_closed"] and not result["live_compute_verified"]
    if independent_operator:
        assert read_document(state / "setup.json") == config


def test_failed_compute_activation_still_closes_ssh(setup):
    state, calls, value = setup

    def fail(*args):
        raise InstallError("retained failure")

    with pytest.raises(InstallError, match="retained failure"):
        compute_setup.run_compute_setup(state, None, collect=lambda *_: value, verify=lambda _: None, install=fail)
    assert calls == ["consent", "open", "close"]


def test_refused_consent_never_opens_ssh(setup, monkeypatch):
    state, calls, value = setup

    def refuse(*args):
        raise InstallError("not confirmed")

    monkeypatch.setattr(compute_setup, "approve", refuse)
    with pytest.raises(InstallError, match="not confirmed"):
        compute_setup.run_compute_setup(state, None, collect=lambda *_: value, verify=lambda _: None)
    assert calls == []


@pytest.mark.parametrize("failure", ["config", "plan", "missing_plan"])
def test_compute_setup_refuses_origin_drift_before_credentials_or_ssh(setup, failure):
    state, calls, _ = setup
    path = state / ("setup.json" if failure == "config" else "plan.json")
    document = read_document(path)
    if failure == "config":
        document["public_url"] = "https://different.example.org"
    elif failure == "plan":
        document["plan"]["public_url"] = "https://different.example.org"
    else:
        document["plan"].pop("public_url")
    write_document(path, document)
    with pytest.raises(InstallError, match="verified deployment"):
        compute_setup.run_compute_setup(state, None, collect=lambda *_: pytest.fail("must not request credentials"))
    assert calls == []
