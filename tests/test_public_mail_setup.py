"""Existing-server mail boundaries, private state and synthetic SSH receipts.

Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
"""

import copy
import fcntl
import hashlib
import json
from pathlib import Path

import pytest
from test_public_deployment import bootstrap, pack, payload  # noqa: F401
from test_public_server_installation import execute, installation  # noqa: F401

from reasoning_ledger.deployment import digitalocean, host, mail_host, mail_setup
from reasoning_ledger.deployment.host import InstallError, canonical
from reasoning_ledger.deployment.private import read_document, read_private, write_document, write_private
from reasoning_ledger.server.mail import MailConfig

MAIL = MailConfig("invites@example.org", "re_private_mail_fixture").document()


@pytest.fixture
def bundle(tmp_path, payload):  # noqa: F811
    return pack(tmp_path / "release.tar.gz", payload,
                manifest_change=lambda value: value.update(installation_features=list(host.INSTALLATION_FEATURES)))


@pytest.fixture
def managed(installation, monkeypatch):  # noqa: F811
    controller, arguments, provider, observations, installer, commands, directory, document, archive, tmp_path = installation
    health_calls, failures = [], {"health": False, "restart": False}

    def health(origin, **options):
        health_calls.append(options)
        assert origin == controller.binding["public_url"]
        if options.get("require_mail") and failures["health"]:
            raise InstallError("mail worker is not ready")
        return {"public_url": origin, "https_ready": True, "initialized": False}

    monkeypatch.setattr(host, "ready", health)
    execute(installation)
    provisioning = {"format": 1, "plan_id": controller.identity, "deployment_id": document["deployment_id"], "droplet_id": 456}
    value = {"format": 1, "installation": controller.binding, "provisioning": provisioning, "mail": MAIL, "manifest": controller.manifest}
    value["setup_id"] = hashlib.sha256(canonical({"installation": controller.binding, "provisioning": provisioning,
                                                "mail": host.mail_binding(canonical(MAIL))})).hexdigest()
    engine = mail_host.MailHostInstaller(installer.root, health=health)
    original = installer.command

    def command(args, **options):
        if args == ["systemctl", "restart", "reasoning-server"] and failures["restart"]:
            raise InstallError("restart receipt lost")
        return original(args, **options)

    installer.command = command
    engine.host = installer
    commands.calls.clear()
    return engine, value, commands, health_calls, failures, installation


def test_existing_setup_preserves_installation_and_binds_private_mail_and_exact_readiness(managed):
    engine, value, commands, health_calls, _, _ = managed
    state = engine.root / "var/lib/reasoning-deploy"
    original = (state / "installation.json").read_bytes()
    receipt = engine.install(value)
    assert receipt == mail_host.result(value)
    assert read_document(state / "server-mail.json") == {"installation": value["installation"], "mail": host.mail_binding(canonical(MAIL))}
    path = engine.root / "var/lib/reasoning/mail.json"
    assert read_private(path) == canonical(MAIL) and path.stat().st_mode & 0o777 == 0o600
    assert (state / "installation.json").read_bytes() == original
    assert ["systemctl", "restart", "reasoning-server"] in commands.calls
    assert any(call.get("require_mail") and call.get("expected_release") == value["installation"]["release_id"] for call in health_calls)
    commands.calls.clear()
    assert engine.install(value) == receipt
    assert ["systemctl", "restart", "reasoning-server"] not in commands.calls
    assert MAIL["api_key"] not in json.dumps(receipt) and MAIL["api_key"] not in (state / "server-mail.json").read_text()


@pytest.mark.parametrize("failure", ["health", "restart"])
def test_failed_health_or_lost_restart_resumes_exact_journal_without_replacing_credentials(managed, failure):
    engine, value, _, _, failures, _ = managed
    failures[failure] = True
    with pytest.raises(InstallError):
        engine.install(value)
    path = engine.root / "var/lib/reasoning/mail.json"
    inode, body = path.stat().st_ino, path.read_bytes()
    failures[failure] = False
    assert engine.install(value)["worker_running"] is True
    assert path.stat().st_ino == inode and path.read_bytes() == body


def test_interrupted_secret_success_recovers_existing_bytes_without_recreating_them(managed, monkeypatch):
    engine, value, *_ = managed
    original = mail_host.write_document
    interrupted = False

    def write(path, document, **options):
        nonlocal interrupted
        if path.name == "journal.json" and document.get("phase") == "verifying" and not interrupted:
            interrupted = True
            raise KeyboardInterrupt
        return original(path, document, **options)

    monkeypatch.setattr(mail_host, "write_document", write)
    with pytest.raises(KeyboardInterrupt):
        engine.install(value)
    path = engine.root / "var/lib/reasoning/mail.json"
    inode = path.stat().st_ino
    assert engine.install(value)["worker_running"] is True
    assert path.stat().st_ino == inode and read_private(path) == canonical(MAIL)


def test_host_rejects_featureless_release_before_commands_or_credentials(managed):
    engine, value, commands, *_ = managed
    changed = copy.deepcopy(value)
    changed["manifest"]["installation_features"].remove("existing-mail-setup-v1")
    changed["installation"]["release_id"] = hashlib.sha256(canonical(changed["manifest"])).hexdigest()
    with pytest.raises(InstallError, match="upgrade"):
        engine.install(changed)
    assert commands.calls == []
    assert not (engine.root / "var/lib/reasoning/mail.json").exists()


@pytest.mark.parametrize("name", ["active-server-upgrade.json", "active-server-recovery.json"])
def test_pending_release_operations_refuse_before_mail_metadata_or_restart(managed, name):
    engine, value, commands, *_ = managed
    write_document(engine.root / "var/lib/reasoning-deploy" / name, {"pending": True})
    with pytest.raises(InstallError, match="pending"):
        engine.install(value)
    assert commands.calls == []
    assert not (engine.root / "var/lib/reasoning-deploy/server-mail.json").exists()


def test_unmanaged_mail_is_preserved_and_never_adopted(managed):
    engine, value, commands, *_ = managed
    path = engine.root / "var/lib/reasoning/mail.json"
    write_private(path, canonical(MAIL))
    with pytest.raises(InstallError, match="unmanaged"):
        engine.install(value)
    assert path.read_bytes() == canonical(MAIL)
    assert ["systemctl", "restart", "reasoning-server"] not in commands.calls
    assert not (engine.root / "var/lib/reasoning-deploy/server-mail.json").exists()


@pytest.mark.parametrize("change", ["removed", "changed", "symlink", "public"])
def test_managed_mail_removal_or_drift_is_not_overwritten(managed, change):
    engine, value, commands, *_ = managed
    engine.install(value)
    path = engine.root / "var/lib/reasoning/mail.json"
    if change == "removed":
        path.unlink()
    elif change == "changed":
        path.write_bytes(canonical({**MAIL, "api_key": "re_different_fixture"}))
    elif change == "symlink":
        original = path.with_name("retained-mail.json")
        path.rename(original)
        path.symlink_to(original)
    else:
        path.chmod(0o644)
    commands.calls.clear()
    with pytest.raises(InstallError):
        engine.install(value)
    assert ["systemctl", "restart", "reasoning-server"] not in commands.calls


def test_shared_host_lock_refuses_concurrent_changes(managed):
    engine, value, commands, *_ = managed
    with (engine.root / "var/lib/reasoning-deploy/bootstrap.lock").open("rb") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(InstallError, match="another managed"):
            engine.install(value)
    assert commands.calls == []


@pytest.fixture
def wizard(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    state = tmp_path / "operator"
    state.mkdir(mode=0o700)
    config = {"public_url": "https://chat.example.org", "token_file": "/private/token", "identity": "/private/key"}
    write_document(state / "setup.json", config)
    write_document(state / "plan.json", {"plan": {"public_url": config["public_url"]}, "plan_id": "d" * 64})
    current = {"release_id": "a" * 64, "bundle_sha256": "b" * 64, "public_url": config["public_url"]}
    monkeypatch.setattr(mail_setup, "current_binding", lambda _: current)
    monkeypatch.setattr(mail_setup, "archived_release", lambda *_: state / "retained.tar.gz")
    manifest = {"installation_features": ["existing-mail-setup-v1"], "source_revision": "c" * 40, "dirty": False}
    monkeypatch.setattr(mail_setup, "inspect_bundle", lambda *_: (manifest, "a" * 64, "b" * 64))
    calls = []
    monkeypatch.setattr(mail_setup, "approve", lambda *_: calls.append("consent"))
    monkeypatch.setattr(mail_setup, "temporary_ssh_cidr", lambda *_: "203.0.113.1/32")
    monkeypatch.setattr(mail_setup, "_ssh_action", lambda *_: (calls.append("open") or config, {"phase": "open"}))
    monkeypatch.setattr(mail_setup, "close_ssh_with_token_recovery", lambda *_: (calls.append("close") or config, {"phase": "closed"}))
    return state, calls, manifest


def test_wizard_confirms_before_ssh_and_always_closes_it_on_interrupted_operation(wizard):
    state, calls, _ = wizard

    def remote(*args):
        assert calls == ["consent", "open"]
        assert args[-1] == MAIL
        calls.append("install")
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        mail_setup.run_mail_setup(state, None, collect=lambda _: MAIL, remote=remote)
    assert calls == ["consent", "open", "install", "close"]
    assert MAIL["api_key"] not in (state / "mail-setup" / ("a" * 64) / "intent.json").read_text()


def test_old_release_refuses_before_collection_consent_or_ssh(wizard):
    state, calls, manifest = wizard
    manifest["installation_features"] = ["invitation-mail-v1"]
    with pytest.raises(InstallError, match="make upgrade"):
        mail_setup.run_mail_setup(state, None, collect=lambda _: pytest.fail("must not request credentials"))
    assert calls == []


@pytest.mark.parametrize("independent_operator", [False, True])
def test_wizard_success_retains_secret_free_completion_and_closes_ssh(wizard, independent_operator):
    state, calls, _ = wizard
    config = read_document(state / "setup.json")
    if independent_operator:
        config.pop("public_url")
        write_document(state / "setup.json", config)

    def remote(*_):
        calls.append("install")
        return {"worker_running": True, "test_email_sent": False}

    result = mail_setup.run_mail_setup(state, None, collect=lambda _: MAIL, remote=remote)
    assert result["worker_running"] and result["ssh_closed"] and not result["test_email_sent"]
    assert calls == ["consent", "open", "install", "close"]
    assert MAIL["api_key"] not in (state / "mail-setup" / ("a" * 64) / "completion.json").read_text()
    assert read_document(state / "setup.json") == config


@pytest.mark.parametrize("failure", ["config", "plan", "missing_plan"])
def test_wizard_refuses_changed_or_missing_origin_before_collecting_credentials(wizard, failure):
    state, calls, _ = wizard
    path = state / ("setup.json" if failure == "config" else "plan.json")
    document = read_document(path)
    if failure == "config":
        document["public_url"] = "https://other.example.org"
    elif failure == "plan":
        document["plan"]["public_url"] = "https://other.example.org"
    else:
        document["plan"].pop("public_url")
    write_document(path, document)
    with pytest.raises(InstallError, match="verified deployment"):
        mail_setup.run_mail_setup(state, None, collect=lambda _: pytest.fail("must not request credentials"))
    assert calls == []


def test_refused_consent_never_opens_ssh(wizard, monkeypatch):
    state, calls, _ = wizard

    def refuse(*_):
        raise InstallError("not confirmed")

    monkeypatch.setattr(mail_setup, "approve", refuse)
    with pytest.raises(InstallError, match="not confirmed"):
        mail_setup.run_mail_setup(state, None, collect=lambda _: MAIL)
    assert calls == []


def test_collect_mail_uses_private_config_and_hidden_key_then_reuses_exact_saved_input(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    answers = iter([MAIL["sender"], "100"])
    pages = []
    monkeypatch.setattr(mail_setup, "ask", lambda *_: next(answers))
    monkeypatch.setattr("builtins.input", lambda _: "")
    monkeypatch.setattr(mail_setup.getpass, "getpass", lambda _: MAIL["api_key"])
    assert mail_setup.collect_mail(tmp_path, opener=pages.append) == MAIL
    assert pages == ["https://resend.com/domains", "https://resend.com/api-keys"]
    path = tmp_path / "mail.json"
    assert path.stat().st_mode & 0o777 == 0o600 and read_document(path) == MAIL
    assert mail_setup.collect_mail(tmp_path, opener=lambda _: pytest.fail("saved input must not reopen account setup")) == MAIL


def test_changed_service_identity_refuses_before_any_mail_write(managed):
    engine, value, commands, _, _, fixture = managed
    fixture[3]["service"]["DropInPaths"] = "/etc/foreign.conf"
    with pytest.raises(InstallError, match="service"):
        engine.install(value)
    assert ["systemctl", "restart", "reasoning-server"] not in commands.calls
    assert not (engine.root / "var/lib/reasoning-deploy/server-mail.json").exists()


@pytest.mark.parametrize("independent_operator", [False, True])
@pytest.mark.parametrize("backups", [False, True])
def test_owned_transport_uses_pinned_vm_private_payload_and_exact_secret_free_receipt(managed, monkeypatch, independent_operator, backups):
    _, value, _, _, _, fixture = managed
    controller, _, provider, _, _, _, directory, document, archive, tmp_path = fixture
    if backups:
        provider.droplets[0]["features"].append("backups")
    token, identity = tmp_path / "token", tmp_path / "identity"
    write_private(token, provider.token.encode())
    write_private(identity, b"nonfunctional identity fixture")

    class Provider:
        def __getattr__(self, name):
            return getattr(provider.client, name)

        def close(self):
            pass

    monkeypatch.setattr(digitalocean, "DigitalOcean", lambda _: Provider())
    config = {"token_file": str(token), "identity": str(identity), "public_url": controller.binding["public_url"]}
    if independent_operator:
        config.pop("public_url")
    calls, inputs = [], []

    def command(arguments, *, capture=False):
        calls.append(arguments)
        if arguments[0] == "scp":
            inputs.append(read_document(Path(arguments[-2])))
        if capture:
            return canonical(mail_host.result(value))

    writes = len(provider.writes)
    assert mail_setup.owned_mail_remote(config, document, directory, archive, MAIL, command=command) == mail_host.result(value)
    assert inputs == [value] and [call[0] for call in calls] == ["ssh", "scp", "ssh"]
    assert all("StrictHostKeyChecking=yes" in call for call in calls)
    assert MAIL["api_key"] not in str(calls) and provider.token not in str(calls)
    assert len(provider.writes) == writes
    assert ("backups" in provider.droplets[0]["features"]) is backups
    provider.account["uuid"] = "0" * 40
    calls.clear()
    with pytest.raises(InstallError):
        mail_setup.owned_mail_remote(config, document, directory, archive, MAIL, command=command)
    assert calls == []


def test_request_tampering_is_refused_without_exposing_keys(managed):
    _, value, *_ = managed
    changed = copy.deepcopy(value)
    changed["mail"]["api_key"] = "re_changed_fixture"
    with pytest.raises(InstallError):
        mail_host.validate_request(changed)
