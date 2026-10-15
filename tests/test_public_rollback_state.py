"""Real encrypted restoration and locks; no root/cloud service replacement."""

import fcntl
import json
import sqlite3
from pathlib import Path

import pytest
from test_public_offline_migration import source as source

from reasoning_ledger.deployment import host
from reasoning_ledger.server import backup as snapshots
from reasoning_ledger.server import maintenance, rollback
from reasoning_ledger.server.mail import MailConfig
from reasoning_ledger.server.store import Store

OPERATION, ROLLBACK, PLAN, SOURCE, TARGET = (value * 64 for value in "abcde")


def restore(source, **changes):
    root, archive, key, receipt, _ = source
    options = {
        "operation_id": OPERATION,
        "rollback_id": ROLLBACK,
        "plan_id": PLAN,
        "source_release": SOURCE,
        "source_stopped": True,
        "data_loss_confirmed": True,
    }
    options.update(changes)
    return rollback.restore_prepared(archive, key, receipt["sha256"], root.parent / "restored", **options)


def capture(source, **changes):
    root, _, _, receipt, _ = source
    # Source creation has no maintenance lease until this explicit fixture
    # establishes one. Actual upgrade/serve creates the same private lease.
    fd = Path(root / maintenance.LOCK)
    if not fd.exists():
        fd.touch(mode=0o600)
    options = {
        "operation_id": OPERATION,
        "rollback_id": ROLLBACK,
        "plan_id": PLAN,
        "source_release": SOURCE,
        "target_release": TARGET,
        "source_schema": len(rollback.MIGRATIONS),
        "expected": receipt["sha256"],
        "source_stopped": True,
        "data_loss_confirmed": True,
    }
    options.update(changes)
    return rollback.capture(root, **options)


def test_restore_publishes_bound_startup_fence_and_replays_without_rotating_credentials(source):
    result = restore(source)
    destination = source[0].parent / "restored"
    setup = (destination / "setup-token").read_bytes()
    assert result["phase"] == "prepared" and result["server_started"] is False
    assert restore(source) == result
    assert (destination / "setup-token").read_bytes() == setup
    assert setup != (source[0] / "setup-token").read_bytes()
    assert (destination / "encryption.key").read_bytes() == (source[0] / "encryption.key").read_bytes()
    with pytest.raises(maintenance.MaintenanceError, match="prepared rollback"):
        Store(destination)
    with pytest.raises(maintenance.MaintenanceError), maintenance.serving(destination, release=SOURCE):
        pytest.fail("prepared restoration must never start")


@pytest.mark.parametrize(
    "changes",
    [
        {"source_stopped": False},
        {"source_stopped": 1},
        {"data_loss_confirmed": False},
        {"data_loss_confirmed": 1},
        {"rollback_id": "bad"},
        {"maximum": True},
    ],
)
def test_restoration_requires_exact_identities_and_literal_fencing_loss_acknowledgments(source, changes):
    with pytest.raises(ValueError):
        restore(source, **changes)
    assert not (source[0].parent / "restored").exists()


@pytest.mark.parametrize(
    "changes",
    [{"rollback_id": "f" * 64}, {"plan_id": "f" * 64}, {"operation_id": "f" * 64}, {"source_release": "f" * 64}, {"maximum": 1024**2}],
)
def test_prepared_replay_refuses_different_original_inputs(source, changes):
    first = restore(source)
    with pytest.raises(maintenance.MaintenanceError):
        restore(source, **changes)
    assert restore(source) == first


def test_lost_atomic_publication_acknowledgment_reuses_exact_restoration(source, monkeypatch):
    rename = snapshots._rename_noreplace
    interrupted = False

    def lost(original, target):
        nonlocal interrupted
        rename(original, target)
        if not interrupted:
            interrupted = True
            raise RuntimeError("lost publication fixture")

    monkeypatch.setattr(snapshots, "_rename_noreplace", lost)
    with pytest.raises(RuntimeError, match="lost publication"):
        restore(source)
    destination = source[0].parent / "restored"
    original = rollback._read(destination, rollback.JOURNAL)
    setup = (destination / "setup-token").read_bytes()
    assert restore(source) == {**original, "prepared_state_verified": True, "server_started": False}
    assert (destination / "setup-token").read_bytes() == setup


def test_prepublication_failure_never_adopts_or_overwrites_existing_state(source, monkeypatch):
    monkeypatch.setattr(rollback, "atomic_write", lambda *_args: (_ for _ in ()).throw(RuntimeError("before publication fixture")))
    with pytest.raises(RuntimeError, match="before publication"):
        restore(source)
    assert not (source[0].parent / "restored").exists()


def test_restoration_refuses_an_existing_unowned_directory(source):
    destination = source[0].parent / "restored"
    destination.mkdir(mode=0o700)
    (destination / "evidence").write_bytes(b"unowned evidence")
    with pytest.raises(maintenance.MaintenanceError, match="unowned"):
        restore(source)
    assert (destination / "evidence").read_bytes() == b"unowned evidence"


def test_mail_is_bound_before_source_provenance_and_never_added_by_replay(source):
    path = source[0].parent / "mail.json"
    body = host.canonical(MailConfig("invites@example.org", "private-rollback-mail-fixture", 100).document())
    host.atomic_write(path, body)
    checksum = host.mail_binding(body)["input_sha256"]
    first = restore(source, mail_file=path, expected_mail=checksum)
    assert first["mail_sha256"] == checksum
    assert (source[0].parent / "restored/mail.json").read_bytes() == body
    assert "private-rollback-mail-fixture" not in json.dumps(first)
    with pytest.raises(maintenance.MaintenanceError):
        restore(source)
    assert restore(source, mail_file=path, expected_mail=checksum) == first


def test_release_is_explicit_preserves_new_live_data_and_binds_ordinary_server_identity(source):
    restore(source)
    root = source[0].parent / "restored"
    with pytest.raises(maintenance.MaintenanceError):
        rollback.release(root, rollback_id=ROLLBACK, source_release=SOURCE)
    result = rollback.release(root, rollback_id=ROLLBACK, source_release=SOURCE, activation_confirmed=True)
    assert result["phase"] == "released" and result["activation_permitted"] is True
    assert result["server_started"] is result["health_verified"] is False
    with pytest.raises(maintenance.MaintenanceError, match="exact source release"), maintenance.serving(root, release=TARGET):
        pytest.fail("different binary claim must not start")
    with maintenance.serving(root, release=SOURCE):
        store = Store(root)
        store.bootstrap(store._setup_token, "owner@example.org", "Owner", "rollback-fixture-password", "Live organization")
        observed = rollback.observe(root, rollback_id=ROLLBACK, source_release=SOURCE)
        assert observed["database_schema_verified"] is True and observed["health_verified"] is False
        with pytest.raises(maintenance.MaintenanceError):
            rollback.release(root, rollback_id=ROLLBACK, source_release=SOURCE, activation_confirmed=True)
    assert rollback.release(root, rollback_id=ROLLBACK, source_release=SOURCE, activation_confirmed=True) == result
    assert Store(root).initialized()
    with pytest.raises(maintenance.MaintenanceError):
        restore(source)


def test_capture_durably_fences_target_and_recognizes_the_same_stopped_state(source):
    result = capture(source)
    assert result["phase"] == "fenced" and result["state_verified"] and result["server_started"] is False
    assert capture(source) == result
    with pytest.raises(maintenance.MaintenanceError, match="capture fences"):
        Store(source[0])
    with pytest.raises(maintenance.MaintenanceError), maintenance.serving(source[0], release=TARGET):
        pytest.fail("captured target may not restart")
    with pytest.raises(maintenance.MaintenanceError):
        capture(source, rollback_id="f" * 64)


def test_capture_refuses_an_actual_live_shared_lease(source):
    with maintenance.serving(source[0], release=TARGET), pytest.raises(maintenance.MaintenanceError, match="owns"):
        capture(source)
    assert not (source[0] / rollback.REQUEST).exists()


def test_prepared_source_guard_refuses_target_auto_migration_and_record_drift(source, monkeypatch):
    restore(source)
    root = source[0].parent / "restored"
    rollback.release(root, rollback_id=ROLLBACK, source_release=SOURCE, activation_confirmed=True)
    monkeypatch.setattr(rollback, "MIGRATIONS", (*rollback.MIGRATIONS, ("CREATE TABLE forbidden_auto_migration(value TEXT)",)))
    with pytest.raises(maintenance.MaintenanceError, match="implicit target migration"):
        Store(root)
    with sqlite3.connect(root / "server.sqlite3") as database:
        assert database.execute("PRAGMA user_version").fetchone()[0] == len(maintenance.MIGRATIONS)


@pytest.mark.parametrize("change", ["extra-field", "boolean-format", "corrupt", "public", "symlink"])
def test_prepared_source_refuses_malformed_or_insecure_provenance_without_recreating_it(source, change):
    restore(source)
    root = source[0].parent / "restored"
    path = root / rollback.JOURNAL
    value = json.loads(path.read_bytes())
    if change == "extra-field":
        value["secret"] = "private-provenance-fixture"
        host.atomic_write(path, host.canonical(value))
    elif change == "boolean-format":
        value["format"] = True
        host.atomic_write(path, host.canonical(value))
    elif change == "corrupt":
        path.write_bytes(b"corrupt provenance")
    elif change == "public":
        path.chmod(0o644)
    else:
        path.rename(path.with_suffix(".retained"))
        path.symlink_to(path.with_suffix(".retained"))
    with pytest.raises(maintenance.MaintenanceError) as error:
        rollback.verify_prepared(root, rollback_id=ROLLBACK, source_release=SOURCE)
    assert "private-provenance-fixture" not in str(error.value)


def test_prepared_source_verification_obeys_existing_worker_locks(source):
    restore(source)
    root = source[0].parent / "restored"
    lock = root / "initialization.lock"
    lock.touch(mode=0o600)
    with lock.open("rb") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(maintenance.MaintenanceError):
            rollback.verify_prepared(root, rollback_id=ROLLBACK, source_release=SOURCE)
