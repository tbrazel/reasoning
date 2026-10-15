"""CLI approvals and actual organization APIs, without SSH or paid Modal jobs."""

import hashlib
import json
from pathlib import Path

import httpx
import pytest
from test_public_compute import IMAGE, compute  # noqa: F401
from test_public_server import PASSWORD, installation  # noqa: F401
from test_public_setup import wizard  # noqa: F401

from reasoning_ledger.deployment import modal_setup, setup
from reasoning_ledger.deployment.host import InstallError
from reasoning_ledger.deployment.private import write_document, write_private
from reasoning_ledger.server.compute_catalog import DEFAULT_RESOURCES
from reasoning_ledger.server.compute_modal import SUPERVISOR_IMAGE
from reasoning_ledger.server.store import StoreError


def selection(tmp_path):
    token, secret = tmp_path / "modal-id", tmp_path / "modal-secret"
    write_private(token, b"ak_fixture_identifier")
    write_private(secret, b"as_fixture_private_secret")
    return {"format": 1, "release_id": "a" * 64, "images": [IMAGE], "token_id_file": str(token), "token_secret_file": str(secret),
            "app": "reasoning-compute", "environment": "main", "supervisor_image": SUPERVISOR_IMAGE,
            "resources": DEFAULT_RESOURCES, "workers": 2, "monthly_cpu_seconds": 10000}


@pytest.fixture
def organization(compute, tmp_path):  # noqa: F811
    config, store, state, client, owner, _ = compute
    state.set_policy(owner["user_id"], owner["organization_id"], [], 0, 2)
    value = selection(tmp_path)
    owner = {**owner, "setup_id": "f" * 32, "email": "owner@example.org", "password": PASSWORD}
    calls, tokens = [], []
    mode = {"lose": None}

    def handle(request):
        calls.append((request.method, request.url.path))
        response = client.request(request.method, str(request.url), content=request.content, headers=dict(request.headers))
        if request.url.path == "/api/auth/login":
            tokens.append(response.json()["access_token"])
        if mode["lose"] is not None and request.method == "POST" and request.url.path.endswith(mode["lose"]):
            mode["lose"] = None
            raise httpx.ReadError("private fixture transport diagnostic")
        return httpx.Response(response.status_code, content=response.content, headers=dict(response.headers))

    def configure():
        return modal_setup.configure_modal_organization(value, {"public_url": config.public_url}, owner,
                                                        owner["organization_id"], tmp_path, transport=httpx.MockTransport(handle))

    return value, owner, state, store, configure, calls, tokens, mode


def test_actual_compute_policy_scoped_enrollments_and_session_revocation_are_replay_safe(organization):
    value, owner, state, store, configure, calls, tokens, _ = organization
    first = configure()
    assert configure() == first
    assert len(first) == 2 and first[0]["enrollment"]["id"] != first[1]["enrollment"]["id"]
    assert state.configuration(owner["user_id"], owner["organization_id"])["policy"] == {
        "allowed_images": [IMAGE["id"]], "monthly_cpu_seconds": value["monthly_cpu_seconds"], "max_active_jobs": 2}
    assert sum(method == "POST" and path.endswith("/compute-scalers") for method, path in calls) == 1
    assert len(state.workers(owner["user_id"], owner["organization_id"])) == 2
    for token in tokens:
        with pytest.raises(StoreError):
            store.session(token)


def test_lost_enrollment_reply_recovers_original_worker_instead_of_replacing_it(organization):
    _, owner, state, _, configure, _, _, mode = organization
    mode["lose"] = "/enrollments"
    with pytest.raises(InstallError, match="uncertain"):
        configure()
    assert len(state.workers(owner["user_id"], owner["organization_id"])) == 1
    result = configure()
    assert len(result) == 2 and len(state.workers(owner["user_id"], owner["organization_id"])) == 2


def test_lost_one_time_grant_reply_stops_without_duplicate_grants(organization):
    _, _, _, _, configure, calls, _, mode = organization
    mode["lose"] = "/compute-scalers"
    with pytest.raises(InstallError, match="uncertain"):
        configure()
    with pytest.raises(InstallError, match="grant creation is uncertain"):
        configure()
    assert sum(method == "POST" and path.endswith("/compute-scalers") for method, path in calls) == 1


def test_changed_policy_catalog_and_revoked_workers_are_not_overwritten(organization):
    _, owner, state, _, configure, _, _, _ = organization
    workers = configure()
    state.set_policy(owner["user_id"], owner["organization_id"], [], 0, 2)
    with pytest.raises(InstallError, match="administrator edits"):
        configure()
    state.set_policy(owner["user_id"], owner["organization_id"], [IMAGE["id"]], 10000, 2)
    state.change_worker(owner["user_id"], owner["organization_id"], workers[0]["enrollment"]["id"], revoke=True)
    with pytest.raises(InstallError):
        configure()


def test_modal_credentials_and_catalog_are_collected_once_privately(tmp_path, monkeypatch, capsys):
    catalog = tmp_path / "approved.json"
    write_private(catalog, json.dumps([IMAGE]).encode())
    answers = iter([str(catalog), "manual", "", "", "1", "1024", "120", "", "", "", ""])
    secrets = iter(["ak_fixture_identifier", "as_fixture_private_secret"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    monkeypatch.setattr(setup.getpass, "getpass", lambda prompt: next(secrets))
    first = modal_setup.collect_modal(tmp_path, "a" * 64)
    assert modal_setup.collect_modal(tmp_path, "a" * 64) == first
    assert "as_fixture_private_secret" not in json.dumps(first) and "as_fixture_private_secret" not in capsys.readouterr().out
    with pytest.raises(InstallError, match="invalid Modal selection"):
        modal_setup.collect_modal(tmp_path, "b" * 64)


@pytest.mark.parametrize("field,value", [("workers", 9), ("monthly_cpu_seconds", 0), ("images", []),
                                         ("release_id", "b" * 64), ("resources", {**DEFAULT_RESOURCES, "cpu": 5})])
def test_invalid_compute_limits_refuse_setup(tmp_path, field, value):
    chosen = selection(tmp_path)
    chosen[field] = value
    with pytest.raises(InstallError):
        modal_setup.validate_modal_input(chosen, "a" * 64)


def test_modal_wizard_requires_same_backend_on_resume_and_confirms_before_activation(wizard, monkeypatch, capsys):  # noqa: F811
    state, _, calls, approvals, _, _, command, provider = wizard
    chosen = selection(state.parent)
    monkeypatch.setattr(setup, "inspect_bundle", lambda _: ({"dirty": False, "installation_features": ["modal-relay-v1"]},
                                                          "a" * 64, "b" * 64))

    def collect(directory, release):
        if not (directory / "modal-input.json").exists():
            write_document(directory / "modal-input.json", chosen, new=True)
        return chosen

    monkeypatch.setattr(modal_setup, "collect_modal", collect)
    monkeypatch.setattr(modal_setup, "verify_modal_credentials", lambda value: None)
    activations = []

    def configure(*arguments):
        assert "do-launch" in calls and approvals[-1] == "launch https://chat.example.org"
        activations.append(arguments)
        return {"live_compute_verified": False, "relays_active": 2}

    result = setup.run_setup(Path("fixture"), state, command, configure_provider=provider,
                             compute_backend="modal", configure_compute=configure,
                             detect_public_ipv4=lambda: "203.0.113.10/32")
    assert result["compute_backend"] == "modal" and result["compute_setup"]["relays_active"] == 2
    assert len(activations) == 1
    output = capsys.readouterr().out
    assert "not a Modal dollar/invoice cap" in output and "on-demand paid Modal VMs" in output
    with pytest.raises(InstallError, match="--compute-backend modal"):
        setup.run_setup(Path("fixture"), state, command, configure_provider=provider,
                        detect_public_ipv4=lambda: "203.0.113.10/32")


def test_modal_configuration_is_not_activated_on_pending_dns_or_denied_launch(wizard, monkeypatch):  # noqa: F811
    state, _, calls, _, active, _, command, provider = wizard
    chosen = selection(state.parent)
    monkeypatch.setattr(setup, "inspect_bundle", lambda _: ({"dirty": False, "installation_features": ["modal-relay-v1"]},
                                                          "a" * 64, "b" * 64))
    monkeypatch.setattr(modal_setup, "collect_modal", lambda *args: chosen)
    monkeypatch.setattr(modal_setup, "verify_modal_credentials", lambda value: None)
    activations = []
    active["delegated"] = False
    result = setup.run_setup(Path("fixture"), state, command, configure_provider=provider,
                             compute_backend="modal", configure_compute=lambda *a: activations.append(a),
                             detect_public_ipv4=lambda: "203.0.113.10/32")
    assert result["phase"] == "dns_delegation_required" and "do-launch" not in calls and activations == []


def test_modal_auth_failure_stops_before_dns_and_billable_server_requests(wizard, monkeypatch):  # noqa: F811
    state, _, calls, _, _, _, command, provider = wizard
    chosen = selection(state.parent)
    monkeypatch.setattr(setup, "inspect_bundle", lambda _: ({"dirty": False, "installation_features": ["modal-relay-v1"]},
                                                          "a" * 64, "b" * 64))
    monkeypatch.setattr(modal_setup, "collect_modal", lambda *args: chosen)

    def reject(value):
        raise InstallError("Modal authentication failed")

    monkeypatch.setattr(modal_setup, "verify_modal_credentials", reject)
    with pytest.raises(InstallError, match="authentication failed"):
        setup.run_setup(Path("fixture"), state, command, configure_provider=provider, compute_backend="modal",
                        detect_public_ipv4=lambda: "203.0.113.10/32")
    assert calls == []


def test_modal_credential_probe_is_read_only_and_redacts_provider_errors(tmp_path, monkeypatch):
    value = selection(tmp_path)
    calls = []

    class SDK:
        def __init__(self, profile):
            assert profile["token_secret"] == "as_fixture_private_secret"

        def connect(self):
            return self

        def hello(self):
            calls.append("hello")

    monkeypatch.setattr(modal_setup, "ModalSDK", SDK)
    modal_setup.verify_modal_credentials(value)
    assert calls == ["hello"]

    def reject(self):
        raise OSError("private provider text as_fixture_private_secret")

    monkeypatch.setattr(SDK, "hello", reject)
    with pytest.raises(InstallError) as error:
        modal_setup.verify_modal_credentials(value)
    assert "as_fixture" not in str(error.value) and "private provider" not in str(error.value)


def test_catalog_prepare_precedes_enrollment_and_activation_receipt_is_verified(tmp_path, monkeypatch):
    value = selection(tmp_path)
    events = []
    monkeypatch.setattr(modal_setup, "configure_modal_organization", lambda *args: events.append("enroll") or [
        {"profile": modal_setup.profile_from_input(value), "enrollment": {"fixture": True}}])
    identity = hashlib.sha256(json.dumps({"selection": value, "organization_id": "b" * 32},
                                      sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()

    def remote(payload, operation):
        events.append(operation)
        if operation == "prepare":
            assert payload["workers"] == []
            return {"setup_id": identity, "catalog_installed": True, "relays_active": False}
        return {"setup_id": identity, "relays_active": 1, "live_compute_verified": False}

    result = modal_setup.setup_modal(value, {"public_url": "https://chat.example.org"}, {}, "b" * 32, tmp_path, remote)
    assert result["live_compute_verified"] is False and events == ["prepare", "enroll", "activate"]
