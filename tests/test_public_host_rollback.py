"""Root-host rollback preparation with real restoration and systemd fixtures."""

import fcntl
import json
import os
import sqlite3
import subprocess

import pytest
from test_public_host_activation import activate
from test_public_host_activation import activation_inputs as activation_inputs
from test_public_host_activation import bootstrap as bootstrap
from test_public_host_activation import installed as installed
from test_public_host_activation import payload as payload
from test_public_host_activation import prepared_inputs as prepared_inputs
from test_public_host_upgrade import OPERATION, operation, pack, prepare, refresh_plan, state

from reasoning_ledger.deployment import host
from reasoning_ledger.deployment import server_upgrade as upgrade
from reasoning_ledger.server import backup as snapshots
from reasoning_ledger.server import maintenance, rollback
from reasoning_ledger.server import store as store_module
from reasoning_ledger.server.mail import MailConfig
from reasoning_ledger.server.store import Store

ROLLBACK = "f" * 64


def rollback_prepare(inputs, **changes):
    options = {
        "operation_id": OPERATION,
        "rollback_id": ROLLBACK,
        "confirmed_plan": inputs[5]["plan_id"],
        "source_release": inputs[5]["source"]["release_id"],
        "target_release": inputs[5]["target"]["release_id"],
        "workers_fenced": True,
        "data_loss_confirmed": True,
        "skip_platform": True,
    }
    options.update(changes)
    return inputs[0].rollback_prepare(**options)


def rollback_state(inputs):
    return inputs[0]._read(operation(inputs) / "rollback.json")


def candidate(inputs):
    return inputs[0].root / "var/lib" / ("reasoning-rollback-" + ROLLBACK) / "state"


def test_preparation_retains_new_target_accounts_and_restores_fenced_source_without_starting_it(activation_inputs):
    inputs = activation_inputs
    prepare(inputs)
    activate(inputs)
    engine = inputs[0]
    original_upgrade = state(inputs)
    store = Store(engine.root / "var/lib/reasoning")
    store.bootstrap(store._setup_token, "new@example.org", "New Owner", "target-fixture-password", "New target data")
    first = rollback_prepare(inputs)
    assert first["phase"] == "prepared" and first["rollback_completed"] is first["source_activation_performed"] is False
    assert first["server_restart_fenced"] and first["displaced_state_retained"] and first["root_private_backup_retained"]
    assert first["displaced_database_validated"] is False
    assert state(inputs) == original_upgrade
    assert inputs[-1]["active"] is False and inputs[-1]["starts"] == 1
    displaced = operation(inputs) / "displaced-state"
    assert displaced.stat().st_uid == os.getuid() and displaced.stat().st_mode & 0o777 == 0o700
    with sqlite3.connect(displaced / "server.sqlite3") as database:
        assert database.execute("SELECT name FROM organizations").fetchone()[0] == "New target data"
    with sqlite3.connect(candidate(inputs) / "server.sqlite3") as database:
        assert database.execute("SELECT count(*) FROM organizations").fetchone()[0] == 0
        assert database.execute("PRAGMA user_version").fetchone()[0] == inputs[5]["source_database_schema"]
    with pytest.raises(maintenance.MaintenanceError):
        Store(candidate(inputs))
    with pytest.raises(maintenance.MaintenanceError):
        Store(engine.root / "var/lib/reasoning")
    assert rollback_prepare(inputs) == first
    assert inputs[-1]["helpers"].count("rollback-restore") == 1
    assert inputs[-1]["helpers"].count("backup") == 1
    with pytest.raises(host.InstallError, match="rollback owns"):
        activate(inputs)
    with pytest.raises(host.InstallError, match="rollback owns"):
        prepare(inputs)


@pytest.mark.parametrize("phase", ["target_stopped", "restoring", "sealing", "restored", "displacing", "prepared"])
def test_original_rollback_recovers_all_durable_preparation_phases(activation_inputs, monkeypatch, phase):
    inputs = activation_inputs
    prepare(inputs)
    activate(inputs)
    engine = inputs[0]
    original = engine._write
    interrupted = False

    def lost(path, value):
        nonlocal interrupted
        original(path, value)
        if path.name == "rollback.json" and value["phase"] == phase and not interrupted:
            interrupted = True
            raise RuntimeError("rollback phase interruption fixture")

    monkeypatch.setattr(engine, "_write", lost)
    with pytest.raises(RuntimeError, match="phase interruption"):
        rollback_prepare(inputs)
    monkeypatch.setattr(engine, "_write", original)
    assert rollback_prepare(inputs)["phase"] == "prepared"
    assert inputs[-1]["helpers"].count("rollback-restore") == 1
    assert inputs[-1]["starts"] == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"workers_fenced": False},
        {"data_loss_confirmed": False},
        {"data_loss_confirmed": 1},
        {"rollback_id": "bad"},
        {"confirmed_plan": "0" * 64},
        {"source_release": "0" * 64},
    ],
)
def test_invalid_confirmation_never_stops_or_displaces_the_active_target(activation_inputs, changes):
    inputs = activation_inputs
    prepare(inputs)
    activate(inputs)
    calls = len(inputs[-1]["calls"])
    with pytest.raises(host.InstallError):
        rollback_prepare(inputs, **changes)
    assert inputs[-1]["active"] is True
    assert len(inputs[-1]["calls"]) == calls
    assert not (operation(inputs) / "rollback.json").exists()


def test_lost_source_restore_reply_reuses_exact_credentials_and_original_backup(activation_inputs, monkeypatch):
    inputs = activation_inputs
    prepare(inputs)
    activate(inputs)
    engine = inputs[0]
    original = engine.command
    interrupted = False

    def lost(args, **options):
        nonlocal interrupted
        result = original(args, **options)
        if "rollback-restore" in args and not interrupted:
            interrupted = True
            raise RuntimeError("lost restore reply fixture")
        return result

    monkeypatch.setattr(engine, "command", lost)
    with pytest.raises(RuntimeError, match="lost restore reply"):
        rollback_prepare(inputs)
    setup = (candidate(inputs) / "setup-token").read_bytes()
    monkeypatch.setattr(engine, "command", original)
    assert rollback_prepare(inputs)["phase"] == "prepared"
    assert (candidate(inputs) / "setup-token").read_bytes() == setup
    assert inputs[-1]["helpers"].count("rollback-restore") == 2


def test_lost_no_replace_displacement_reply_retains_every_original_target_byte(activation_inputs, monkeypatch):
    inputs = activation_inputs
    prepare(inputs)
    activate(inputs)
    engine = inputs[0]
    rename = engine._rename

    def lost(original, target):
        rename(original, target)
        raise RuntimeError("lost displacement reply fixture")

    monkeypatch.setattr(engine, "_rename", lost)
    with pytest.raises(RuntimeError, match="lost displacement"):
        rollback_prepare(inputs)
    assert rollback_state(inputs)["phase"] == "displacing"
    monkeypatch.setattr(engine, "_rename", rename)
    assert rollback_prepare(inputs)["phase"] == "prepared"
    assert inputs[-1]["helpers"].count("rollback-restore") == 1


def test_corrupt_target_database_is_preserved_without_requiring_target_sql_health(activation_inputs):
    inputs = activation_inputs
    prepare(inputs)
    activate(inputs)
    path = inputs[0].root / "var/lib/reasoning/server.sqlite3"
    # No live SQLite connection is held by the service fixture after Store
    # construction. Root stop closes the real shared lease before capture.
    path.write_bytes(b"corrupt target database evidence")
    result = rollback_prepare(inputs)
    assert result["displaced_database_validated"] is False
    assert (operation(inputs) / "displaced-state/server.sqlite3").read_bytes() == b"corrupt target database evidence"
    assert rollback.verify_prepared(candidate(inputs), rollback_id=ROLLBACK, source_release=inputs[5]["source"]["release_id"])[
        "prepared_state_verified"
    ]


def test_prepared_upgrade_can_restore_before_any_target_activation(activation_inputs):
    inputs = activation_inputs
    prepare(inputs)
    assert rollback_prepare(inputs)["phase"] == "prepared"
    assert inputs[-1]["starts"] == 0


@pytest.mark.parametrize("evidence", ["source-backup", "key", "displaced-data", "placeholder", "unit", "catalog", "pointer"])
def test_prepared_replay_refuses_evidence_drift_and_never_overwrites_it(activation_inputs, evidence):
    inputs = activation_inputs
    prepare(inputs)
    activate(inputs)
    rollback_prepare(inputs)
    engine = inputs[0]
    paths = {
        "source-backup": operation(inputs) / "source.rnbk",
        "key": operation(inputs) / "backup.key",
        "displaced-data": operation(inputs) / "displaced-state/server.sqlite3",
        "placeholder": engine.root / "var/lib/reasoning" / rollback.BLOCK,
        "unit": engine.root / "etc/systemd/system/reasoning-server.service",
        "catalog": engine.root / "etc/reasoning/compute-images.json",
        "pointer": engine.active,
    }
    path = paths[evidence]
    path.write_bytes(b"changed rollback evidence fixture")
    with pytest.raises(host.InstallError):
        rollback_prepare(inputs)
    assert path.read_bytes() == b"changed rollback evidence fixture"
    assert inputs[-1]["active"] is False


def test_held_initializer_lease_prevents_displacement_but_keeps_the_prepared_restoration(activation_inputs):
    inputs = activation_inputs
    prepare(inputs)
    activate(inputs)
    lock = inputs[0].root / "var/lib/reasoning/initialization.lock"
    with lock.open("rb") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(host.InstallError):
            rollback_prepare(inputs)
    assert rollback_state(inputs)["phase"] == "restored"
    assert not (operation(inputs) / "displaced-state").exists()
    assert rollback_prepare(inputs)["phase"] == "prepared"


def test_configured_mail_is_restored_before_source_fingerprinting(activation_inputs):
    engine = activation_inputs[0]
    source = activation_inputs[5]["source"]
    body = host.canonical(MailConfig("invites@example.org", "private-root-rollback-mail-fixture", 100).document())
    host.atomic_write(engine.root / "var/lib/reasoning/mail.json", body)
    host.atomic_write(engine.directory / "server-mail.json", host.canonical({"installation": source, "mail": host.mail_binding(body)}))
    inputs = refresh_plan(activation_inputs)
    prepare(inputs)
    activate(inputs)
    result = rollback_prepare(inputs)
    assert (candidate(inputs) / "mail.json").read_bytes() == body
    assert result["restoration"]["mail_sha256"] == host.mail_binding(body)["input_sha256"]
    assert "private-root-rollback-mail-fixture" not in json.dumps(result)
    assert rollback_prepare(inputs) == result


@pytest.mark.parametrize("helper", ["rollback-restore", "rollback-verify"])
@pytest.mark.parametrize("change", ["secret-field", "operation", "false-start-flag"])
def test_bad_source_receipts_never_displace_target_or_overwrite_original_restoration(activation_inputs, monkeypatch, helper, change):
    inputs = activation_inputs
    prepare(inputs)
    activate(inputs)
    engine = inputs[0]
    original = engine.command

    def corrupt(args, **options):
        response = original(args, **options)
        if helper in args:
            value = json.loads(response.stdout)
            if change == "secret-field":
                value["secret"] = "private-source-receipt-fixture"
            elif change == "operation":
                value["operation_id"] = "0" * 64
            else:
                value["server_started"] = True
            return subprocess.CompletedProcess(args, 0, stdout=json.dumps(value).encode())
        return response

    monkeypatch.setattr(engine, "command", corrupt)
    with pytest.raises(host.InstallError) as error:
        rollback_prepare(inputs)
    assert "private-source-receipt-fixture" not in str(error.value)
    assert not (operation(inputs) / "displaced-state").exists()
    nonce = (candidate(inputs) / "setup-token").read_bytes()
    monkeypatch.setattr(engine, "command", original)
    assert rollback_prepare(inputs)["phase"] == "prepared"
    assert (candidate(inputs) / "setup-token").read_bytes() == nonce


def test_resume_refuses_a_different_rollback_identity(activation_inputs):
    prepare(activation_inputs)
    activate(activation_inputs)
    first = rollback_prepare(activation_inputs)
    with pytest.raises(host.InstallError):
        rollback_prepare(activation_inputs, rollback_id="e" * 64)
    assert rollback_prepare(activation_inputs) == first


@pytest.mark.parametrize("missing", ["root-journal", "source-journal", "source-key", "placeholder-marker"])
def test_prepared_rollback_never_recreates_missing_original_evidence(activation_inputs, missing):
    inputs = activation_inputs
    prepare(inputs)
    activate(inputs)
    rollback_prepare(inputs)
    path = {
        "root-journal": operation(inputs) / "rollback.json",
        "source-journal": candidate(inputs) / rollback.JOURNAL,
        "source-key": operation(inputs) / "backup.key",
        "placeholder-marker": inputs[0].root / "var/lib/reasoning" / rollback.BLOCK,
    }[missing]
    path.unlink()
    with pytest.raises(host.InstallError):
        rollback_prepare(inputs)
    assert not path.exists()


def test_restore_uses_matching_original_schema_while_preserving_failed_future_target(activation_inputs, monkeypatch):
    engine, source, target, source_checksum, *_ = activation_inputs
    original_schema = maintenance.MIGRATIONS
    future = (
        *original_schema,
        ("CREATE TABLE rollback_future_probe(value TEXT)", "INSERT INTO rollback_future_probe VALUES('retained future state')"),
    )
    directory = engine.installer._directory("var/lib/reasoning-deploy/rollback-future-fixture", 0o700)
    host.extract_bundle(target, directory)
    files = {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in directory.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    }
    target = pack(
        target.parent / "rollback-future.tar.gz",
        files,
        manifest_change=lambda value: value.update(
            database_schema=len(future), installation_features=list(host.INSTALLATION_FEATURES), source_revision="e" * 40
        ),
    )
    target.chmod(0o600)
    _, target_release, checksum = host.inspect_bundle(target)
    inputs = refresh_plan((engine, source, target, source_checksum, checksum, {}, activation_inputs[-1]))
    original_command = engine.command

    def command(args, **options):
        target_helper = args[:4] == ["runuser", "-u", "reasoning", "--"] and target_release in args[4]
        if target_helper or args[:3] == ["systemctl", "start", "reasoning-server"]:
            with monkeypatch.context() as patch:
                for module in (maintenance, snapshots, rollback, store_module):
                    patch.setattr(module, "MIGRATIONS", future)
                return original_command(args, **options)
        return original_command(args, **options)

    engine.command = command
    prepare(inputs)
    engine.inspector.health = lambda origin, **options: {"public_url": origin, "https_ready": True, "initialized": False}
    activate(inputs)
    result = rollback_prepare(inputs)
    assert result["restoration"]["source_schema"] == len(original_schema)
    with sqlite3.connect(candidate(inputs) / "server.sqlite3") as database:
        assert database.execute("PRAGMA user_version").fetchone()[0] == len(original_schema)
        assert database.execute("SELECT 1 FROM sqlite_schema WHERE name='rollback_future_probe'").fetchone() is None
    with sqlite3.connect(operation(inputs) / "displaced-state/server.sqlite3") as database:
        assert database.execute("PRAGMA user_version").fetchone()[0] == len(future)
        assert database.execute("SELECT value FROM rollback_future_probe").fetchone()[0] == "retained future state"


@pytest.mark.parametrize(
    "options",
    [
        ["--rollback-prepare"],
        ["--rollback-prepare", "--confirm-newer-data-loss"],
        [
            "--rollback-prepare",
            "--rollback-id",
            ROLLBACK,
            "--confirm-source-release",
            "a" * 64,
            "--confirm-target-release",
            "b" * 64,
            "--confirm-newer-data-loss",
        ],
        ["--activate", "--confirm-activation", "--confirm-target-release", "a" * 64, "--confirm-newer-data-loss"],
    ],
)
def test_root_cli_refuses_missing_or_mixed_rollback_acknowledgments_before_host_access(monkeypatch, options):
    monkeypatch.setattr(upgrade, "HostUpgrade", lambda: pytest.fail("invalid rollback CLI must not inspect or change a host"))
    with pytest.raises(SystemExit) as error:
        upgrade.main(["--operation-id", OPERATION, "--confirm-plan", "a" * 64, *options])
    assert error.value.code == 2
