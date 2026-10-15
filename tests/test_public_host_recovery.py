"""Real restored SQL/crypto with explicit account, package and systemd fixtures."""

import hashlib
import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest
from test_public_backup import installation as installation
from test_public_backup import snapshot
from test_public_deployment import bootstrap as bootstrap
from test_public_deployment import pack
from test_public_deployment import payload as payload
from test_public_server_operations import wheel_fixture

from reasoning_ledger.deployment import host, server_recovery, server_upgrade
from reasoning_ledger.server import cli, maintenance, recovery
from reasoning_ledger.server.mail import MailConfig
from reasoning_ledger.server.store import Store

RECOVERY = "f" * 64
ORIGIN = "https://replacement.example.org"


@pytest.fixture
def inputs(bootstrap, payload, installation, tmp_path):
    installer, fixture_commands = bootstrap
    wheel, contents = wheel_fixture()
    payload["wheel/reasoning_ledger-0.1.0-py3-none-any.whl"] = wheel
    stage = installer._directory("var/lib/reasoning-deploy/staging", 0o700)
    installer.state_dir.chmod(0o700)
    bundle = pack(
        stage / "release.tar.gz",
        payload,
        manifest_change=lambda value: value.update(
            database_schema=len(maintenance.MIGRATIONS),
            installation_features=list(host.INSTALLATION_FEATURES),
        ),
    )
    bundle.chmod(0o600)
    archive, checksum, key = snapshot(installation, tmp_path)
    copied_archive, copied_key = stage / "source.rnbk", stage / "backup.key"
    copied_archive.write_bytes(archive.read_bytes())
    copied_key.write_bytes(key.read_bytes())
    copied_archive.chmod(0o600)
    copied_key.chmod(0o600)
    manifest, release, bundle_checksum = host.inspect_bundle(bundle)
    key_checksum = hashlib.sha256(copied_key.read_bytes()).hexdigest()
    identity, confirmation = server_recovery.recovery_identity(
        manifest, release, bundle_checksum, ORIGIN, checksum, key_checksum, maximum=8 * 1024**3
    )
    observations = {"helpers": [], "restore_loss": False}

    def command(args, **options):
        if args[:2] in (["systemctl", "start"], ["systemctl", "restart"], ["systemctl", "enable"]):
            pytest.fail("preparation must not activate a server or proxy")
        if args[:2] == ["systemctl", "show"]:
            expected = {
                "FragmentPath": "/etc/systemd/system/reasoning-server.service",
                "DropInPaths": "/" + server_recovery.DROPIN,
                "User": "reasoning",
                "Group": "reasoning",
                "LoadState": "loaded",
                "NeedDaemonReload": "no",
                "ActiveState": "inactive",
                "MainPID": "0",
                "SubState": "dead",
            }
            assert (installer.root / "etc/systemd/system/reasoning-server.service").exists()
            return subprocess.CompletedProcess(
                args, 0, stdout="".join(key + "=" + value + "\n" for key, value in expected.items()).encode()
            )
        if args[0].endswith("/bin/pip") and "--no-deps" in args:
            site = installer.root / "opt/reasoning/releases" / release / "venv/lib/python3.12/site-packages"
            for name, body in contents.items():
                path = site / name
                path.parent.mkdir(parents=True, exist_ok=True)
                for parent in path.parents:
                    if parent == installer.root:
                        break
                    parent.chmod(0o755)
                path.write_bytes(body)
                path.chmod(0o644)
        if args[:4] == ["runuser", "-u", "reasoning", "--"]:
            observations["helpers"].append(args[7])
            assert args[7] != "init"
            with redirect_stdout(io.StringIO()) as output:
                try:
                    assert cli.main(args[5:]) == 0
                except SystemExit:
                    return subprocess.CompletedProcess(args, 2, stdout=b"")
            if args[7] == "recovery-prepare" and observations["restore_loss"]:
                observations["restore_loss"] = False
                raise host.InstallError("lost restoration reply fixture")
            return subprocess.CompletedProcess(args, 0, stdout=output.getvalue().encode())
        return fixture_commands(args, **options)

    engine = server_recovery.HostRecovery(
        root=installer.root, command=command, health=lambda *args, **kwargs: pytest.fail("preparation cannot verify HTTPS")
    )
    return engine, bundle, bundle_checksum, copied_archive, checksum, copied_key, key_checksum, confirmation, identity, observations


def prepare(inputs, **changes):
    engine, bundle, bundle_checksum, archive, checksum, key, key_checksum, confirmation, *_ = inputs
    options = {"recovery_id": RECOVERY, "confirmed_recovery": confirmation, "execution_fenced": True, "skip_platform": True}
    options.update(changes)
    return engine.prepare(bundle, bundle_checksum, ORIGIN, archive, checksum, key, key_checksum, **options)


def state(inputs):
    engine = inputs[0]
    return engine._read(engine.directory / "server-recoveries" / RECOVERY / "operation.json")


def test_fresh_host_stages_matching_release_and_fenced_restoration_without_initializing_or_activating(inputs):
    engine, *_, observations = inputs
    result = prepare(inputs)
    assert result["phase"] == "prepared" and result["server_restart_fenced"]
    assert result["root_private_inputs_retained"] and result["replacement_data_published"] is result["activation_performed"] is False
    assert result["recovery_completed"] is result["physical_source_fencing_verified"] is result["runtime_dependency_attestation"] is False
    original = state(inputs)
    candidate = engine._candidate(original)
    assert candidate.stat().st_uid == os.getuid() and candidate.stat().st_mode & 0o777 == 0o700
    assert not (engine.root / "var/lib/reasoning").exists()
    assert engine._read(engine.installer.state_path)["phase"] == "packages"
    assert recovery._read(candidate / "state") == result["restoration"]
    with pytest.raises(maintenance.MaintenanceError):
        Store(candidate / "state")
    assert prepare(inputs) == result and state(inputs) == original
    assert observations["helpers"].count("recovery-prepare") == 1
    assert "migrate-offline" not in observations["helpers"] and "init" not in observations["helpers"]
    with pytest.raises(host.InstallError, match="managed recovery"):
        engine.installer.install(inputs[1], inputs[2], ORIGIN, skip_platform=True)
    assert (engine.root / server_recovery.DROPIN).read_text() == server_recovery.FENCE_UNIT


@pytest.mark.parametrize("phase", sorted(server_recovery.PHASES))
def test_each_durable_phase_resumes_original_preparation_without_repeated_restore(inputs, monkeypatch, phase):
    engine = inputs[0]
    original = engine._write
    interrupted = False

    def lost(path, value):
        nonlocal interrupted
        original(path, value)
        if path.name == "operation.json" and value["phase"] == phase and not interrupted:
            interrupted = True
            raise RuntimeError("phase reply loss fixture")

    monkeypatch.setattr(engine, "_write", lost)
    with pytest.raises(RuntimeError):
        prepare(inputs)
    monkeypatch.setattr(engine, "_write", original)
    assert prepare(inputs)["phase"] == "prepared"
    assert inputs[-1]["helpers"].count("recovery-prepare") == 1


def test_lost_restore_reply_recognizes_prepared_state_without_repeated_secret_rotation(inputs):
    inputs[-1]["restore_loss"] = True
    with pytest.raises(host.InstallError, match="lost restoration"):
        prepare(inputs)
    engine = inputs[0]
    data = engine._candidate(state(inputs)) / "state"
    setup = (data / "setup-token").read_bytes()
    result = prepare(inputs)
    assert result["phase"] == "prepared" and (data / "setup-token").read_bytes() == setup
    assert inputs[-1]["helpers"].count("recovery-prepare") == 2


@pytest.mark.parametrize(
    "changes",
    [
        {"execution_fenced": False},
        {"execution_fenced": 1},
        {"confirmed_recovery": "0" * 64},
        {"recovery_id": "invalid"},
        {"maximum": 1},
        {"maximum": True},
    ],
)
def test_invalid_confirmations_do_not_claim_host_ownership_or_stage_packages(inputs, changes):
    with pytest.raises(host.InstallError):
        prepare(inputs, **changes)
    assert not inputs[0].active.exists() and not inputs[0].installer.state_path.exists()


@pytest.mark.parametrize(
    "relative",
    ["var/lib/reasoning", "opt/reasoning", "etc/reasoning", "var/lib/reasoning-compute", "var/lib/reasoning-recovery-" + RECOVERY],
)
def test_existing_host_state_or_unowned_staging_is_not_adopted(inputs, relative):
    path = inputs[0].root / relative
    path.mkdir(parents=True, mode=0o700)
    marker = path / "operator-evidence"
    marker.write_bytes(b"must remain")
    with pytest.raises(host.InstallError):
        prepare(inputs)
    assert marker.read_bytes() == b"must remain" and not inputs[0].active.exists()


@pytest.mark.parametrize("mutation", ["pointer", "key", "backup", "fence", "dropin", "unit", "catalog", "receipt", "history", "journal"])
def test_changed_original_root_or_application_evidence_refuses_preparation_replay(inputs, mutation):
    prepare(inputs)
    engine = inputs[0]
    original = state(inputs)
    operation = engine.directory / "server-recoveries" / RECOVERY
    data = engine._candidate(original) / "state"
    targets = {
        "pointer": engine.active,
        "key": operation / "backup.key",
        "backup": operation / "source.rnbk",
        "fence": engine.root / server_recovery.FENCE,
        "dropin": engine.root / server_recovery.DROPIN,
        "unit": engine.root / "etc/systemd/system/reasoning-server.service",
        "catalog": engine.root / "etc/reasoning/compute-images.json",
        "receipt": data / recovery.RECEIPT,
        "history": data / recovery.HISTORY / (RECOVERY + ".json"),
        "journal": data / maintenance.JOURNAL,
    }
    targets[mutation].unlink()
    with pytest.raises((host.InstallError, SystemExit, OSError)):
        prepare(inputs)
    assert state(inputs) == original and not (engine.root / "var/lib/reasoning").exists()


def test_upgrade_cannot_bypass_pending_replacement_recovery(inputs):
    prepare(inputs)
    upgrade = server_upgrade.HostUpgrade(root=inputs[0].root, command=inputs[0].command)
    with pytest.raises(host.InstallError, match="recovery owns"):
        upgrade.activate(
            operation_id="a" * 64,
            confirmed_plan="b" * 64,
            target_release=inputs[8]["installation"]["release_id"],
            activation_confirmed=True,
            skip_platform=True,
        )


def test_managed_mail_stays_separate_from_archive_and_never_activates_during_preparation(inputs):
    engine = inputs[0]
    mail = host.canonical(MailConfig("operator@example.org", "private-mail-fixture-key", 10).document())
    manifest, release, _ = host.inspect_bundle(inputs[1])
    identity, confirmation = server_recovery.recovery_identity(
        manifest, release, inputs[2], ORIGIN, inputs[4], inputs[6], maximum=8 * 1024**3, mail=mail
    )
    result = prepare(inputs, confirmed_recovery=confirmation, mail=mail)
    candidate = engine._candidate(state(inputs))
    assert (candidate / "state/mail.json").read_bytes() == mail
    assert result["restoration"]["mail_sha256"] == identity["mail"]["input_sha256"]
    assert prepare(inputs, confirmed_recovery=confirmation, mail=mail) == result
    with pytest.raises(host.InstallError):
        prepare(inputs)
    assert "private-mail-fixture-key" not in host.canonical(result).decode()


def test_owned_hardware_requires_exact_vm_request_before_any_package_changes(inputs, monkeypatch):
    engine = inputs[0]
    request = {"format": 1, "plan_id": "a" * 64, "deployment_id": "b" * 32, "droplet_id": 123}
    marker = engine.root / "etc/reasoning-provisioning.json"
    engine.installer._directory("etc")
    host.atomic_write(
        marker,
        host.canonical({key: request[key] for key in ("plan_id", "deployment_id")}),
    )
    monkeypatch.setattr(host.HostInstaller, "_droplet_id", staticmethod(lambda: 123))
    with pytest.raises(host.InstallError, match="provisioning request"):
        prepare(inputs)
    assert not engine.active.exists()
    manifest, release, _ = host.inspect_bundle(inputs[1])
    _, confirmation = server_recovery.recovery_identity(
        manifest, release, inputs[2], ORIGIN, inputs[4], inputs[6], maximum=8 * 1024**3, provisioning=request
    )
    assert prepare(inputs, confirmed_recovery=confirmation, provisioning=request)["phase"] == "prepared"
    assert engine._read(engine.directory / "server-provisioning.json")["request"] == request
    monkeypatch.setattr(host.HostInstaller, "_droplet_id", staticmethod(lambda: 124))
    with pytest.raises(host.InstallError):
        prepare(inputs, confirmed_recovery=confirmation, provisioning=request)


def test_standalone_recovery_tooling_imports_without_application_or_site_packages(inputs, tmp_path):
    directory = tmp_path / "standalone"
    directory.mkdir(mode=0o700)
    for name, module in (
        ("installer.py", host),
        ("server-operations.py", server_upgrade.server_operations),
        ("server-upgrade.py", server_upgrade),
        ("server-recovery.py", server_recovery),
    ):
        (directory / name).write_bytes(Path(module.__file__).read_bytes())
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", str(directory / "server-recovery.py"), "--help"], capture_output=True, timeout=5
    )
    assert result.returncode == 0 and b"--confirm-recovery" in result.stdout and b"--confirm-execution-fenced" in result.stdout
    assert b"--mail-input" in result.stdout and b"--provisioning" in result.stdout


def test_root_plan_reports_exact_identity_without_host_access_or_authentication(inputs, monkeypatch, capsys):
    monkeypatch.setattr(server_recovery, "HostRecovery", lambda: pytest.fail("planning cannot access the host"))
    assert (
        server_recovery.main(
            [
                "--plan",
                "--bundle",
                str(inputs[1]),
                "--expected-sha256",
                inputs[2],
                "--backup-sha256",
                inputs[4],
                "--key-sha256",
                inputs[6],
                "--public-url",
                ORIGIN,
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result == {**inputs[8], "input_sha256": inputs[7], "host_changed": False, "archive_authenticated": False}
    assert not inputs[0].active.exists() and not inputs[0].installer.state_path.exists()
