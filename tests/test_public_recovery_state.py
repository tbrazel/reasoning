"""Actual authenticated recovery, SQLite, startup leases and successor history."""

import hashlib
import json
import sqlite3

import pytest
from test_public_backup import installation as installation
from test_public_backup import snapshot

from reasoning_ledger.deployment import host
from reasoning_ledger.deployment.private import read_private
from reasoning_ledger.server import backup, cli, maintenance, recovery
from reasoning_ledger.server.compute_catalog import DEFAULT_RESOURCES
from reasoning_ledger.server.mail import MailConfig
from reasoning_ledger.server.store import Store

OPERATION, RELEASE, NEXT_OPERATION, NEXT_RELEASE = (character * 64 for character in "abcd")


@pytest.fixture
def inputs(installation, tmp_path):
    archive, checksum, key = snapshot(installation, tmp_path)
    return installation, archive, checksum, key, tmp_path / "replacement"


def prepare(inputs, **changes):
    _, archive, checksum, key, destination = inputs
    options = {"operation_id": OPERATION, "target_release": RELEASE, "source_stopped": True}
    options.update(changes)
    return recovery.prepare(archive, key, checksum, destination, **options)


def test_restore_publishes_exact_prepared_fence_and_preserves_accounts_and_encryption(inputs):
    result = prepare(inputs)
    state, owner, *_ = inputs[0]
    destination = inputs[-1]
    assert result["server_started"] is result["activation_performed"] is result["sql_migration_performed"] is False
    assert result["prepared_state_verified"]
    assert result["maintenance"]["phase"] == "migrated"
    assert result["maintenance"]["source_state_sha256"] != result["maintenance"]["target_state_sha256"]
    assert (destination / "encryption.key").read_bytes() == (state.data_dir / "encryption.key").read_bytes()
    setup = (destination / "setup-token").read_bytes()
    assert setup != (state.data_dir / "setup-token").read_bytes()
    assert prepare(inputs) == result and (destination / "setup-token").read_bytes() == setup
    with pytest.raises(maintenance.MaintenanceError, match="unfinished"):
        Store(destination)
    with pytest.raises(maintenance.MaintenanceError), maintenance.serving(destination, release=RELEASE):
        pytest.fail("ordinary startup cannot activate a prepared restoration")
    with maintenance.serving(destination, release=RELEASE, operation_id=OPERATION):
        restored = Store(destination)
        assert restored.initialized()
        with restored.transaction() as db:
            assert db.execute("SELECT name FROM organizations").fetchone()[0] == "Org"
            assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
            assert db.execute("SELECT COUNT(*) FROM compute_workers WHERE revoked_at IS NULL").fetchone()[0] == 0
        assert result["recovery"]["sessions_revoked"] == 1
        assert owner["organization_id"]
        observed = maintenance.observe_activation(destination, operation_id=OPERATION, target_release=RELEASE)
        assert observed["activation_state_verified"] and observed["health_verified_by_command"] is False
        maintenance.finish(destination, operation_id=OPERATION, target_release=RELEASE, activation_verified=True)
    with maintenance.serving(destination, release=RELEASE):
        Store(destination)
    assert recovery._read(destination) == result
    with pytest.raises(maintenance.MaintenanceError):
        prepare(inputs)


def test_uncertain_compute_is_preserved_held_without_idle_waiver_for_future_upgrades(installation, tmp_path):
    state, owner, topic, _, compute, _, key = installation
    compute.enqueue(
        owner["user_id"],
        owner["organization_id"],
        topic,
        "f" * 32,
        {
            "image_id": "fixture",
            "language": "python",
            "program": "print(1)",
            "resources": DEFAULT_RESOURCES,
        },
    )
    archive, checksum, _ = snapshot(installation, tmp_path)
    destination = tmp_path / "held-replacement"
    result = recovery.prepare(archive, key, checksum, destination, operation_id=OPERATION, target_release=RELEASE, source_stopped=True)
    assert result["recovery"]["compute_jobs_held"] == 1
    maintenance.verify_prepared(destination, operation_id=OPERATION, target_release=RELEASE)
    with pytest.raises(maintenance.MaintenanceError, match="settled"):
        maintenance._idle(destination)
    with maintenance.serving(destination, release=RELEASE, operation_id=OPERATION):
        restored = Store(destination)
        with restored.transaction() as db:
            assert db.execute("SELECT status,cancel_requested FROM compute_jobs").fetchone()[:] == ("outcome_unknown", 1)
            assert db.execute("SELECT COUNT(*) FROM compute_workers WHERE revoked_at IS NULL").fetchone()[0] == 0
        maintenance.finish(destination, operation_id=OPERATION, target_release=RELEASE, activation_verified=True)
    later = tmp_path / "later.rnbk"
    receipt = backup.backup(destination, later, key)
    with pytest.raises(maintenance.MaintenanceError, match="settled"):
        maintenance.migrate(
            destination,
            later,
            key,
            receipt["sha256"],
            source_schema=len(maintenance.MIGRATIONS),
            target_release=NEXT_RELEASE,
            operation_id=NEXT_OPERATION,
            source_stopped=True,
        )
    assert maintenance.journal(destination)["phase"] == "completed"


@pytest.mark.parametrize(
    "changes",
    [
        {"source_stopped": False},
        {"source_stopped": 1},
        {"operation_id": "bad"},
        {"target_release": "bad"},
        {"maximum": True},
        {"maximum": 1},
        {"mail_file": None, "expected_mail": "a" * 64},
    ],
)
def test_invalid_recovery_inputs_do_not_publish_state(inputs, changes):
    with pytest.raises(ValueError):
        prepare(inputs, **changes)
    assert not inputs[-1].exists()


def test_existing_unmanaged_state_is_never_adopted_or_overwritten(inputs):
    inputs[-1].mkdir(mode=0o700)
    marker = inputs[-1] / "operator-state"
    marker.write_text("must remain")
    with pytest.raises(maintenance.MaintenanceError, match="cannot adopt"):
        prepare(inputs)
    assert marker.read_text() == "must remain" and not (inputs[-1] / "server.sqlite3").exists()


@pytest.mark.parametrize(
    "mutation",
    ["missing_receipt", "missing_journal", "missing_history", "operation", "release", "hash", "counts", "extra", "schema", "phase"],
)
def test_missing_or_changed_provenance_never_bypasses_prepared_startup_guard(inputs, mutation):
    prepare(inputs)
    destination = inputs[-1]
    path = destination / recovery.RECEIPT
    current = recovery._read(destination)
    if mutation == "missing_receipt":
        path.unlink()
    elif mutation == "missing_journal":
        (destination / maintenance.JOURNAL).unlink()
    elif mutation == "missing_history":
        (destination / recovery.HISTORY / (OPERATION + ".json")).unlink()
    else:
        if mutation == "operation":
            current["operation_id"] = NEXT_OPERATION
        elif mutation == "release":
            current["target_release"] = NEXT_RELEASE
        elif mutation == "hash":
            current["backup_sha256"] = "not a digest"
        elif mutation == "counts":
            current["recovery"]["sessions_revoked"] = True
        elif mutation == "extra":
            current["secret"] = "do not expose"
        elif mutation == "schema":
            current["maintenance"]["target_schema"] += 1
        else:
            current["maintenance"]["phase"] = "activation_started"
        host.atomic_write(path, host.canonical(current))
    with pytest.raises(maintenance.MaintenanceError):
        Store(destination)
    with pytest.raises(maintenance.MaintenanceError):
        prepare(inputs)


def test_prepared_sql_or_secret_changes_refuse_activation_and_recovery_replay(inputs):
    prepare(inputs)
    with sqlite3.connect(inputs[-1] / "server.sqlite3") as db:
        db.execute("UPDATE organizations SET name='changed prepared state'")
    with pytest.raises(maintenance.MaintenanceError, match="changed"):
        prepare(inputs)
    with (
        pytest.raises(maintenance.MaintenanceError, match="changed"),
        maintenance.serving(inputs[-1], release=RELEASE, operation_id=OPERATION),
    ):
        pytest.fail("changed prepared state must not activate")


def test_lost_atomic_publication_reply_recovers_without_repeated_revocation_or_rotation(inputs, monkeypatch):
    original = backup._rename_noreplace

    def lost(source, destination):
        original(source, destination)
        raise OSError("lost publication fixture")

    monkeypatch.setattr(backup, "_rename_noreplace", lost)
    with pytest.raises(maintenance.MaintenanceError):
        prepare(inputs)
    receipt = recovery._read(inputs[-1])
    setup = (inputs[-1] / "setup-token").read_bytes()
    monkeypatch.setattr(backup, "_rename_noreplace", original)
    assert prepare(inputs) == receipt and (inputs[-1] / "setup-token").read_bytes() == setup


def test_private_mail_is_bound_and_included_in_prepared_fingerprint(inputs, tmp_path):
    path = tmp_path / "mail.json"
    mail = host.canonical(MailConfig("operator@example.org", "private-fixture-key", 10).document())
    host.atomic_write(path, mail)
    checksum = hashlib.sha256(mail).hexdigest()
    result = prepare(inputs, mail_file=path, expected_mail=checksum)
    assert result["mail_sha256"] == checksum and read_private(inputs[-1] / "mail.json") == mail
    with pytest.raises(maintenance.MaintenanceError):
        prepare(inputs)
    assert prepare(inputs, mail_file=path, expected_mail=checksum) == result
    host.atomic_write(inputs[-1] / "mail.json", host.canonical(MailConfig("different@example.org", "another-key", 10).document()))
    with pytest.raises(maintenance.MaintenanceError, match="changed"):
        prepare(inputs, mail_file=path, expected_mail=checksum)


def test_successor_upgrade_requires_exact_original_completed_recovery_history(inputs, tmp_path):
    original = prepare(inputs)
    destination = inputs[-1]
    with maintenance.serving(destination, release=RELEASE, operation_id=OPERATION):
        Store(destination)
        maintenance.finish(destination, operation_id=OPERATION, target_release=RELEASE, activation_verified=True)
    archive = tmp_path / "next.rnbk"
    snapshot = backup.backup(destination, archive, inputs[3])
    result = maintenance.migrate(
        destination,
        archive,
        inputs[3],
        snapshot["sha256"],
        source_schema=len(maintenance.MIGRATIONS),
        target_release=NEXT_RELEASE,
        operation_id=NEXT_OPERATION,
        source_stopped=True,
    )
    assert result["phase"] == "migrated"
    assert recovery._read(destination) == original
    maintenance.verify_prepared(destination, operation_id=NEXT_OPERATION, target_release=NEXT_RELEASE)
    history = destination / "maintenance-history" / (OPERATION + ".json")
    retained = history.read_bytes()
    history.unlink()
    with pytest.raises(maintenance.MaintenanceError), maintenance.serving(destination, release=NEXT_RELEASE, operation_id=NEXT_OPERATION):
        pytest.fail("successor cannot invent recovery completion")
    host.atomic_write(history, retained)
    with maintenance.serving(destination, release=NEXT_RELEASE, operation_id=NEXT_OPERATION):
        Store(destination)
        maintenance.finish(destination, operation_id=NEXT_OPERATION, target_release=NEXT_RELEASE, activation_verified=True)
    with maintenance.serving(destination, release=NEXT_RELEASE):
        Store(destination)


def test_recovery_cli_does_not_start_server_or_print_provider_secrets(inputs, capsys):
    assert (
        cli.main(
            [
                "--data-dir",
                str(inputs[-1]),
                "recovery-prepare",
                "--backup",
                str(inputs[1]),
                "--key-file",
                str(inputs[3]),
                "--expected-sha256",
                inputs[2],
                "--operation-id",
                OPERATION,
                "--target-release",
                RELEASE,
                "--confirm-source-stopped",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    result = json.loads(output)
    assert result["operation"] == "recovery-prepare" and result["server_started"] is False
    assert "provider-secret" not in output and "message secret" not in output
