"""Real encrypted snapshots, schema upgrades and immutable rollback handoffs."""

import json
import sqlite3
import subprocess
import sys

import pytest
from test_public_offline_migration import source as source
from test_public_rollback_state import OPERATION, PLAN, ROLLBACK, SOURCE

from reasoning_ledger.deployment import host
from reasoning_ledger.server import backup as snapshots
from reasoning_ledger.server import maintenance, rollback
from reasoning_ledger.server import store as store_module
from reasoning_ledger.server.store import Store

NEXT_OPERATION, NEXT_RELEASE = "1" * 64, "2" * 64


@pytest.fixture
def restored(source):
    original, archive, key, receipt, _ = source
    root = original.parent / "restored"
    rollback.restore_prepared(
        archive,
        key,
        receipt["sha256"],
        root,
        operation_id=OPERATION,
        rollback_id=ROLLBACK,
        plan_id=PLAN,
        source_release=SOURCE,
        source_stopped=True,
        data_loss_confirmed=True,
    )
    rollback.release(root, rollback_id=ROLLBACK, source_release=SOURCE, activation_confirmed=True)
    store = Store(root)
    store.bootstrap(store._setup_token, "live@example.org", "Owner", "source-handoff-password", "Live source data")
    new_archive = original.parent / "successor.rnbk"
    new_receipt = snapshots.backup(root, new_archive, key)
    return root, new_archive, key, new_receipt, store


def migrate(inputs, **changes):
    root, archive, key, receipt, _ = inputs
    options = {
        "source_schema": len(store_module.MIGRATIONS),
        "target_release": NEXT_RELEASE,
        "operation_id": NEXT_OPERATION,
        "source_stopped": True,
    }
    options.update(changes)
    return maintenance.migrate(root, archive, key, receipt["sha256"], **options)


def patch_schema(monkeypatch, migrations):
    for module in (maintenance, snapshots, rollback, store_module):
        monkeypatch.setattr(module, "MIGRATIONS", migrations)


def first_future(monkeypatch):
    original = store_module.MIGRATIONS
    target = (*original, ("CREATE TABLE successor_probe(value TEXT)", "INSERT INTO successor_probe VALUES('verified successor')"))
    patch_schema(monkeypatch, target)
    return len(original), target


def history(root):
    return root / rollback.HISTORY / (ROLLBACK + ".json")


def finish(root, *, operation_id=NEXT_OPERATION, release=NEXT_RELEASE):
    with maintenance.serving(root, release=release, operation_id=operation_id):
        assert Store(root).initialized()
        return maintenance.finish(root, operation_id=operation_id, target_release=release, activation_verified=True)


def test_fresh_authenticated_upgrade_archives_source_provenance_preserves_live_data_and_starts_exact_target(restored, monkeypatch):
    root = restored[0]
    original = rollback._read(root, rollback.JOURNAL)
    source_schema, target = first_future(monkeypatch)
    result = migrate(restored, source_schema=source_schema)
    value = rollback._read(root, rollback.JOURNAL)
    assert value == {**original, "phase": "superseded", "superseded_by": {key: result[key] for key in maintenance.INTENT_FIELDS}}
    assert history(root).read_bytes() == host.canonical(original)
    assert history(root).stat().st_mode & 0o777 == 0o600
    assert history(root).parent.stat().st_mode & 0o777 == 0o700
    with sqlite3.connect(root / "server.sqlite3") as database:
        assert database.execute("PRAGMA user_version").fetchone()[0] == len(target)
        assert database.execute("SELECT value FROM successor_probe").fetchone()[0] == "verified successor"
        assert database.execute("SELECT name FROM organizations").fetchone()[0] == "Live source data"
        assert database.execute("SELECT count(*) FROM sessions").fetchone()[0] == 1
    assert maintenance.verify_prepared(root, operation_id=NEXT_OPERATION, target_release=NEXT_RELEASE)["prepared_state_verified"] is True
    with pytest.raises(maintenance.MaintenanceError), maintenance.serving(root, release=SOURCE, operation_id=NEXT_OPERATION):
        pytest.fail("old source must not start the successor")
    with pytest.raises(maintenance.MaintenanceError):
        Store(root)
    assert migrate(restored, source_schema=source_schema) == result
    with maintenance.serving(root, release=NEXT_RELEASE, operation_id=NEXT_OPERATION):
        assert Store(root).initialized()
        observed = maintenance.observe_activation(root, operation_id=NEXT_OPERATION, target_release=NEXT_RELEASE)
        assert observed["activation_state_verified"] is True
        maintenance.finish(root, operation_id=NEXT_OPERATION, target_release=NEXT_RELEASE, activation_verified=True)
    assert Store(root).initialized()
    assert history(root).read_bytes() == host.canonical(original)


def test_same_schema_upgrade_still_requires_new_release_and_does_not_reenable_original_source(restored):
    root = restored[0]
    migrate(restored)
    finish(root)
    with maintenance.serving(root, release=NEXT_RELEASE):
        assert Store(root).initialized()
    with pytest.raises(maintenance.MaintenanceError), maintenance.serving(root, release=SOURCE):
        pytest.fail("same SQL schema does not authorize the old release")
    with pytest.raises(maintenance.MaintenanceError):
        rollback.release(root, rollback_id=ROLLBACK, source_release=SOURCE, activation_confirmed=True)
    with pytest.raises(maintenance.MaintenanceError):
        rollback.observe(root, rollback_id=ROLLBACK, source_release=SOURCE)


@pytest.mark.parametrize("boundary", ["history-directory", "history", "provenance", "sql", "migrated"])
def test_lost_handoff_or_sql_acknowledgment_recovers_exact_intent_and_original_history(restored, monkeypatch, boundary):
    root = restored[0]
    original = rollback._read(root, rollback.JOURNAL)
    source_schema, _ = first_future(monkeypatch)
    write, migrate_database, retain_history = host.atomic_write, maintenance._migrate_database, rollback._history
    interrupted = False

    def lost_write(path, body):
        nonlocal interrupted
        write(path, body)
        value = json.loads(body) if path.name != maintenance.LOCK else {}
        if not interrupted and (
            (boundary == "history" and path == history(root))
            or (boundary == "provenance" and path == root / rollback.JOURNAL)
            or (boundary == "migrated" and path == root / maintenance.JOURNAL and value.get("phase") == "migrated")
        ):
            interrupted = True
            raise RuntimeError("lost durable handoff acknowledgment")

    def lost_sql(path, schema):
        nonlocal interrupted
        migrate_database(path, schema)
        if boundary == "sql" and path == root and not interrupted:
            interrupted = True
            raise RuntimeError("lost durable SQL acknowledgment")

    def lost_history(path, value, *, publish):
        nonlocal interrupted
        if boundary == "history-directory" and publish and not interrupted:
            (path / rollback.HISTORY).mkdir(mode=0o700)
            interrupted = True
            raise RuntimeError("lost durable history directory acknowledgment")
        return retain_history(path, value, publish=publish)

    monkeypatch.setattr(rollback, "atomic_write", lost_write)
    monkeypatch.setattr(maintenance, "atomic_write", lost_write)
    monkeypatch.setattr(maintenance, "_migrate_database", lost_sql)
    monkeypatch.setattr(rollback, "_history", lost_history)
    with pytest.raises(RuntimeError, match="lost durable"):
        migrate(restored, source_schema=source_schema)
    with pytest.raises(maintenance.MaintenanceError):
        Store(root)
    assert migrate(restored, source_schema=source_schema)["phase"] == "migrated"
    assert history(root).read_bytes() == host.canonical(original)
    assert finish(root)["phase"] == "completed"


@pytest.mark.parametrize("change", ["schema", "same-release", "same-operation", "stale-backup", "prepared", "root-block", "capture"])
def test_unowned_or_unauthenticated_handoff_never_changes_rollback_provenance(restored, source, change):
    root = restored[0]
    original = (root / rollback.JOURNAL).read_bytes()
    options = {}
    inputs = restored
    if change == "schema":
        options["source_schema"] = len(store_module.MIGRATIONS) - 1
    elif change == "same-release":
        options["target_release"] = SOURCE
    elif change == "same-operation":
        options["operation_id"] = OPERATION
    elif change == "stale-backup":
        inputs = root, source[1], source[2], source[3], restored[4]
    elif change == "prepared":
        value = json.loads(original)
        value["phase"] = "prepared"
        host.atomic_write(root / rollback.JOURNAL, host.canonical(value))
        original = (root / rollback.JOURNAL).read_bytes()
    elif change == "root-block":
        host.atomic_write(root / rollback.BLOCK, b"opaque root fence")
    else:
        host.atomic_write(root / rollback.REQUEST, b"malformed captured target")
    with pytest.raises(maintenance.MaintenanceError):
        migrate(inputs, **options)
    assert (root / rollback.JOURNAL).read_bytes() == original
    assert not history(root).exists()
    assert not (root / maintenance.JOURNAL).exists()


@pytest.mark.parametrize(
    "change",
    ["rollback-history", "rollback-journal", "maintenance-journal", "history-changed", "wrong-binding", "bad-intent", "phase-regressed"],
)
def test_missing_or_changed_prepared_lineage_is_not_recreated_by_verification_startup_or_retry(restored, change):
    root = restored[0]
    migrate(restored)
    path = {
        "rollback-history": history(root),
        "rollback-journal": root / rollback.JOURNAL,
        "maintenance-journal": root / maintenance.JOURNAL,
    }.get(change)
    if path is not None:
        path.unlink()
    elif change == "history-changed":
        history(root).write_bytes(b"changed private history")
    else:
        value = rollback._read(root, rollback.JOURNAL)
        if change == "wrong-binding":
            value["superseded_by"]["backup_sha256"] = "0" * 64
        elif change == "bad-intent":
            value["superseded_by"]["source_schema"] = True
        else:
            value.pop("superseded_by")
            value["phase"] = "released"
        host.atomic_write(root / rollback.JOURNAL, host.canonical(value))
    with pytest.raises(maintenance.MaintenanceError):
        maintenance.verify_prepared(root, operation_id=NEXT_OPERATION, target_release=NEXT_RELEASE)
    with pytest.raises(maintenance.MaintenanceError), maintenance.serving(root, release=NEXT_RELEASE, operation_id=NEXT_OPERATION):
        pytest.fail("changed lineage must not authorize activation")
    with pytest.raises(maintenance.MaintenanceError):
        migrate(restored)
    if path is not None:
        assert not path.exists()


def test_multiple_successive_upgrades_keep_first_handoff_and_require_its_completion_history(restored, monkeypatch):
    root = restored[0]
    original_schema, first_target = first_future(monkeypatch)
    migrate(restored, source_schema=original_schema)
    finish(root)
    first_value = (root / rollback.JOURNAL).read_bytes()
    second_archive = root.parent / "second-successor.rnbk"
    second_receipt = snapshots.backup(root, second_archive, restored[2])
    second = root, second_archive, restored[2], second_receipt, Store(root)
    next_operation, next_release = "3" * 64, "4" * 64
    second_target = (*first_target, ("CREATE TABLE later_probe(value TEXT)", "INSERT INTO later_probe VALUES('later')"))
    patch_schema(monkeypatch, second_target)
    migrate(second, source_schema=len(first_target), operation_id=next_operation, target_release=next_release)
    finish(root, operation_id=next_operation, release=next_release)
    assert (root / rollback.JOURNAL).read_bytes() == first_value
    assert len(list((root / "maintenance-history").iterdir())) == 2
    with maintenance.serving(root, release=next_release):
        observed = maintenance.observe_activation(root, operation_id=next_operation, target_release=next_release)
        assert observed["database_schema_verified"] is True
    (root / "maintenance-history" / (NEXT_OPERATION + ".json")).unlink()
    with pytest.raises(maintenance.MaintenanceError):
        Store(root)
    with pytest.raises(maintenance.MaintenanceError):
        maintenance.observe_activation(root, operation_id=next_operation, target_release=next_release)
    with pytest.raises(maintenance.MaintenanceError):
        maintenance.finish(root, operation_id=next_operation, target_release=next_release, activation_verified=True)


def test_regressed_source_marker_and_missing_history_cannot_adopt_already_migrated_sql(restored, monkeypatch):
    root = restored[0]
    original = (root / rollback.JOURNAL).read_bytes()
    source_schema, target = first_future(monkeypatch)
    migrate(restored, source_schema=source_schema)
    host.atomic_write(root / rollback.JOURNAL, original)
    history(root).unlink()
    current = maintenance.journal(root)
    current["phase"] = "migrating"
    host.atomic_write(root / maintenance.JOURNAL, host.canonical(current))
    with pytest.raises(maintenance.MaintenanceError, match="already migrated"):
        migrate(restored, source_schema=source_schema)
    assert not history(root).exists()
    assert (root / rollback.JOURNAL).read_bytes() == original
    with sqlite3.connect(root / "server.sqlite3") as database:
        assert database.execute("PRAGMA user_version").fetchone()[0] == len(target)


@pytest.mark.parametrize(
    "field,value",
    [
        ("format", True),
        ("operation_id", "bad"),
        ("source_schema", True),
        ("target_schema", 1001),
        ("backup_key_sha256", "bad"),
        ("maximum_bytes", 1),
        ("target_release", SOURCE),
        ("unexpected", "private-unknown-field"),
    ],
)
def test_malformed_successor_identity_cannot_authorize_initialization_or_observation(restored, field, value):
    root = restored[0]
    migrate(restored)
    record = rollback._read(root, rollback.JOURNAL)
    record["superseded_by"][field] = value
    host.atomic_write(root / rollback.JOURNAL, host.canonical(record))
    with pytest.raises(maintenance.MaintenanceError):
        Store(root)
    with pytest.raises(maintenance.MaintenanceError):
        maintenance.verify_prepared(root, operation_id=NEXT_OPERATION, target_release=NEXT_RELEASE)


def test_live_successor_observation_uses_shared_lease_and_preserves_new_writes(restored, monkeypatch):
    root = restored[0]
    migrate(restored)
    with maintenance.serving(root, release=NEXT_RELEASE, operation_id=NEXT_OPERATION):
        store = Store(root)
        with store.transaction() as database:
            database.execute("UPDATE organizations SET name='New target live data'")
        monkeypatch.setattr(maintenance, "state_identity", lambda *args: pytest.fail("do not replay live state fingerprint"))
        assert (
            maintenance.observe_activation(root, operation_id=NEXT_OPERATION, target_release=NEXT_RELEASE)["activation_state_verified"]
            is True
        )
        assert (
            maintenance.finish(root, operation_id=NEXT_OPERATION, target_release=NEXT_RELEASE, activation_verified=True)["phase"]
            == "completed"
        )
        process = subprocess.run(
            [
                sys.executable,
                "-m",
                "reasoning_ledger.server.cli",
                "--data-dir",
                str(root),
                "maintenance-observe",
                "--operation-id",
                NEXT_OPERATION,
                "--target-release",
                NEXT_RELEASE,
            ],
            capture_output=True,
            timeout=30,
            check=False,
        )
        assert process.returncode == 0, process.stderr.decode()
        assert json.loads(process.stdout)["database_schema_verified"] is True
    with Store(root).transaction() as database:
        assert database.execute("SELECT name FROM organizations").fetchone()[0] == "New target live data"


def test_old_schema_cannot_implicitly_initialize_a_superseded_successor(restored, monkeypatch):
    original_schema = store_module.MIGRATIONS
    count, _ = first_future(monkeypatch)
    migrate(restored, source_schema=count)
    patch_schema(monkeypatch, original_schema)
    with pytest.raises(maintenance.MaintenanceError):
        Store(restored[0])
    with pytest.raises(maintenance.MaintenanceError), maintenance.serving(restored[0], release=SOURCE):
        pytest.fail("an old source must not resume after supersession")
