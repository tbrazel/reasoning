"""Fenced target capture and authenticated, non-overwriting rollback restoration."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import ExitStack
from pathlib import Path

from ..deployment.host import InstallError, atomic_write, canonical, mail_binding
from ..deployment.private import read_private
from . import backup as snapshots
from . import maintenance
from .migrations import MIGRATIONS

REQUEST = "server-rollback-request.json"
JOURNAL = "server-rollback.json"
BLOCK = "server-rollback-blocked.json"
HISTORY = "rollback-history"
COUNTS = {
    "sessions_revoked",
    "invitations_revoked",
    "workers_revoked",
    "bots_revoked",
    "webhooks_revoked",
    "scalers_revoked",
    "inference_runs_held",
    "compute_jobs_held",
}
IDENTITY = {"format", "operation_id", "rollback_id", "plan_id", "source_release", "backup_sha256", "maximum_bytes"}
OPTIONAL_COUNTS = {"workflow_runs_held", "work_workers_revoked"}


def _read(root, name):
    path = root / name
    if not path.exists() and not path.is_symlink():
        if name == JOURNAL and ((root / HISTORY).exists() or (root / HISTORY).is_symlink()):
            raise maintenance.MaintenanceError("rollback provenance is missing; retained history cannot be bypassed")
        return None
    try:
        raw = read_private(path, 8192)
        value = json.loads(raw)
        common = set(IDENTITY) | {"phase", "state_sha256"}
        extra = (
            {"target_release", "source_schema", "target_schema"}
            if name == REQUEST
            else {"source_schema", "backup_key_sha256", "mail_sha256", "recovery"}
        )
        if isinstance(value, dict) and name == JOURNAL and value.get("phase") == "superseded":
            extra |= {"superseded_by"}
        if (
            not isinstance(value, dict)
            or set(value) != common | extra
            or canonical(value) != raw
            or type(value["format"]) is not int
            or value["format"] != 1
            or any(
                not maintenance._hex(value[key])
                for key in ("operation_id", "rollback_id", "plan_id", "source_release", "backup_sha256", "state_sha256")
            )
            or type(value["maximum_bytes"]) is not int
            or not 1024**2 <= value["maximum_bytes"] <= snapshots.MAX_BYTES
            or type(value["source_schema"]) is not int
            or not 13 <= value["source_schema"] <= 1000
        ):
            raise ValueError
        if name == REQUEST:
            if (
                value["phase"] != "fenced"
                or not maintenance._hex(value["target_release"])
                or type(value["target_schema"]) is not int
                or not value["source_schema"] <= value["target_schema"] <= 1000
            ):
                raise ValueError
        else:
            recovery = value["recovery"]
            if (
                not isinstance(value["phase"], str)
                or value["phase"] not in {"prepared", "released", "superseded"}
                or not maintenance._hex(value["backup_key_sha256"])
                or value["mail_sha256"] is not None
                and not maintenance._hex(value["mail_sha256"])
                or not isinstance(recovery, dict)
                or not COUNTS <= set(recovery) <= COUNTS | OPTIONAL_COUNTS
                or any(type(count) is not int or not 0 <= count <= 2**63 - 1 for count in recovery.values())
            ):
                raise ValueError
            if value["phase"] == "superseded":
                successor = maintenance._intent(value["superseded_by"])
                if (
                    successor["source_schema"] != value["source_schema"]
                    or successor["operation_id"] == value["operation_id"]
                    or successor["target_release"] == value["source_release"]
                ):
                    raise ValueError
        return value
    except (OSError, ValueError, TypeError, KeyError, RecursionError, InstallError) as exc:
        raise maintenance.MaintenanceError(
            "rollback state record is malformed, insecure or differently bound; preserve recovery evidence"
        ) from exc


def _history(root, value, *, publish):
    """Retain the original released provenance, never reconstruct completed history."""
    original = {key: item for key, item in value.items() if key != "superseded_by"}
    original["phase"] = "released"
    directory = root / HISTORY
    if publish:
        directory.mkdir(mode=0o700, exist_ok=True)
    snapshots._directory(directory)
    path = directory / (value["rollback_id"] + ".json")
    expected = canonical(original)
    if path.exists() or path.is_symlink():
        if read_private(path, 8192) != expected:
            raise maintenance.MaintenanceError("original released rollback history changed; refusing to overwrite it")
    elif publish:
        atomic_write(path, expected)
    else:
        raise maintenance.MaintenanceError("original released rollback history is missing")


def _lineage(root, value, current):
    _history(root, value, publish=False)
    successor = value["superseded_by"]
    if current is None:
        raise maintenance.MaintenanceError("superseded rollback requires its retained maintenance journal")
    intent = maintenance._intent({key: current[key] for key in maintenance.INTENT_FIELDS})
    if current["operation_id"] == successor["operation_id"]:
        if canonical(intent) != canonical(successor):
            raise maintenance.MaintenanceError("rollback successor differs from the original exact migration")
    else:
        # Further upgrades must retain completion of the first successor.
        # Ordinary migration additionally verifies its immediately preceding
        # completed journal/history before authorizing another handoff.
        maintenance._history(root, {**successor, "phase": "completed"}, publish=False)
        if (
            current["source_schema"] < successor["target_schema"]
            or current["operation_id"] == value["operation_id"]
            or current["target_release"] == value["source_release"]
        ):
            raise maintenance.MaintenanceError("later migration cannot return to superseded rollback source ownership")
    return current


def _unblocked(root):
    if (root / BLOCK).exists() or (root / BLOCK).is_symlink():
        raise maintenance.MaintenanceError("root-managed rollback fences this installation; resume its original operation")
    if _read(root, REQUEST) is not None:
        raise maintenance.MaintenanceError("rollback capture fences target initialization; resume the original rollback")


def preflight_migration(root, current, *, source_schema, target_release, operation_id):
    """Called under exclusive maintenance/worker leases, before writing intent."""
    _unblocked(root)
    value = _read(root, JOURNAL)
    if value is None:
        return
    if value["phase"] == "prepared":
        raise maintenance.MaintenanceError("prepared rollback cannot be superseded by an upgrade")
    if value["phase"] == "superseded":
        _lineage(root, value, current)
        return
    if (
        value["source_schema"] != source_schema
        or value["source_release"] == target_release
        or value["operation_id"] == operation_id
        or current is not None
        and (current["operation_id"] != operation_id or current["phase"] not in {"validated", "migrating"})
    ):
        raise maintenance.MaintenanceError("released rollback requires a fresh exact source-to-successor migration")
    if current is not None and (
        maintenance._version(root) != source_schema
        or maintenance.state_identity(root, source_schema, current["maximum_bytes"]) != current["source_state_sha256"]
    ):
        raise maintenance.MaintenanceError("missing rollback handoff cannot adopt already migrated or changed source state")
    directory = root / HISTORY
    if directory.exists() or directory.is_symlink():
        # Only a lost history-publication acknowledgment can leave a released
        # marker with retained history; migration intent must already be durable.
        if current is None or current["phase"] != "migrating":
            raise maintenance.MaintenanceError("rollback supersession record is missing; preserve its migration history")
        snapshots._directory(directory)
        path = directory / (value["rollback_id"] + ".json")
        if path.exists() or path.is_symlink():
            _history(root, value, publish=False)


def supersede_for_migration(root, current):
    """Archive/rebind provenance after authenticated dry-run validation, before SQL."""
    _unblocked(root)
    value = _read(root, JOURNAL)
    if value is None:
        return
    if value["phase"] == "superseded":
        _lineage(root, value, current)
        return
    intent = maintenance._intent({key: current[key] for key in maintenance.INTENT_FIELDS})
    if (
        value["phase"] != "released"
        or current["phase"] != "migrating"
        or intent["source_schema"] != value["source_schema"]
        or intent["target_release"] == value["source_release"]
        or intent["operation_id"] == value["operation_id"]
    ):
        raise maintenance.MaintenanceError("rollback supersession requires its exact authenticated migration intent")
    _history(root, value, publish=True)
    atomic_write(root / JOURNAL, canonical({**value, "phase": "superseded", "superseded_by": intent}))


def assert_maintenance_allowed(root, current):
    _unblocked(root)
    value = _read(root, JOURNAL)
    if value is not None:
        if value["phase"] != "superseded":
            raise maintenance.MaintenanceError("maintenance cannot bypass original rollback provenance")
        _lineage(root, value, current)


def assert_initialization_allowed(root):
    _unblocked(root)
    value = _read(root, JOURNAL)
    if value is not None:
        if value["phase"] == "superseded":
            current = _lineage(root, value, maintenance.journal(root))
            if current["target_schema"] != len(MIGRATIONS) or current["phase"] not in {"migrated", "activation_started", "completed"}:
                raise maintenance.MaintenanceError("rollback successor requires exact prepared or activated maintenance schema")
        elif value["phase"] != "released" or value["source_schema"] != len(MIGRATIONS):
            raise maintenance.MaintenanceError("prepared rollback refuses initialization or implicit target migration")
        elif (root / HISTORY).exists() or (root / HISTORY).is_symlink():
            raise maintenance.MaintenanceError("pending rollback supersession refuses initialization")
        with snapshots._input(root / "server.sqlite3", snapshots.MAX_BYTES), snapshots._connect(root / "server.sqlite3") as database:
            if database.execute("PRAGMA user_version").fetchone()[0] != len(MIGRATIONS):
                raise maintenance.MaintenanceError("released rollback refuses implicit database migration")


def assert_serving_allowed(root, release):
    value = _read(root, JOURNAL)
    expected = value["source_release"] if value is not None else None
    if value is not None and value["phase"] == "superseded":
        expected = _lineage(root, value, maintenance.journal(root))["target_release"]
    if value is not None and release != expected:
        raise maintenance.MaintenanceError("rollback server requires the original exact source release")
    assert_initialization_allowed(root)


def _identity(operation_id, rollback_id, plan_id, source_release, expected, maximum):
    if not all(maintenance._hex(value) for value in (operation_id, rollback_id, plan_id, source_release, expected)):
        raise maintenance.MaintenanceError("rollback requires exact original identities")
    snapshots._allowance(maximum)
    return {
        "format": 1,
        "operation_id": operation_id,
        "rollback_id": rollback_id,
        "plan_id": plan_id,
        "source_release": source_release,
        "backup_sha256": expected,
        "maximum_bytes": maximum,
    }


def _leases(root, *, exclusive):
    stack = ExitStack()
    try:
        stack.enter_context(maintenance._lock(root, maintenance.LOCK, exclusive=exclusive, create=False))
        if exclusive:
            for name in maintenance.WORKER_LOCKS:
                if (root / name).exists() or (root / name).is_symlink():
                    stack.enter_context(maintenance._lock(root, name, exclusive=True, create=False))
        return stack
    except BaseException:
        stack.close()
        raise


def capture(
    root: Path,
    *,
    operation_id,
    rollback_id,
    plan_id,
    source_release,
    target_release,
    source_schema,
    expected,
    source_stopped=False,
    data_loss_confirmed=False,
    maximum=snapshots.DEFAULT_BYTES,
):
    if source_stopped is not True or data_loss_confirmed is not True:
        raise maintenance.MaintenanceError("rollback capture requires physical execution fencing and explicit newer-data-loss confirmation")
    identity = _identity(operation_id, rollback_id, plan_id, source_release, expected, maximum)
    if not maintenance._hex(target_release) or type(source_schema) is not int or not 13 <= source_schema <= len(MIGRATIONS):
        raise maintenance.MaintenanceError("rollback capture requires supported exact source/target schemas and releases")
    root = root.absolute()
    try:
        with _leases(root, exclusive=True):
            value = _read(root, REQUEST)
            base = {
                **identity,
                "source_schema": source_schema,
                "target_schema": len(MIGRATIONS),
                "target_release": target_release,
                "phase": "fenced",
            }
            if value is not None and canonical({key: value[key] for key in base}) != canonical(base):
                raise maintenance.MaintenanceError("another rollback owns the captured target")
            current = maintenance.state_identity(root, len(MIGRATIONS), maximum)
            if value is None:
                value = {**base, "state_sha256": current}
                # The application fence is durable before returning/releasing
                # leases. Ordinary target startup cannot mutate this capture.
                atomic_write(root / REQUEST, canonical(value))
            elif value["state_sha256"] != current:
                raise maintenance.MaintenanceError("captured target state changed; never overwrite original rollback evidence")
            return {**value, "state_verified": True, "server_started": False}
    except (OSError, sqlite3.Error, snapshots.BackupError, InstallError) as exc:
        raise maintenance.MaintenanceError("rollback target capture failed; preserve original state and recovery inputs") from exc


def verify_prepared(root: Path, *, rollback_id, source_release):
    if not maintenance._hex(rollback_id) or not maintenance._hex(source_release):
        raise maintenance.MaintenanceError("rollback verification requires exact source and rollback identities")
    root = root.absolute()
    try:
        with _leases(root, exclusive=True):
            value = _read(root, JOURNAL)
            if (
                value is None
                or value["phase"] != "prepared"
                or value["rollback_id"] != rollback_id
                or value["source_release"] != source_release
                or value["source_schema"] != len(MIGRATIONS)
                or _read(root, REQUEST) is not None
            ):
                raise maintenance.MaintenanceError(
                    "rollback verification refuses missing, released or differently bound source restoration"
                )
            if maintenance.state_identity(root, len(MIGRATIONS), value["maximum_bytes"]) != value["state_sha256"]:
                raise maintenance.MaintenanceError("prepared source restoration changed; retain original rollback evidence")
            return {**value, "prepared_state_verified": True, "server_started": False}
    except (OSError, sqlite3.Error, snapshots.BackupError, InstallError) as exc:
        raise maintenance.MaintenanceError("rollback source verification failed; no server was started") from exc


def restore_prepared(
    archive: Path,
    key_file: Path,
    expected: str,
    destination: Path,
    *,
    operation_id,
    rollback_id,
    plan_id,
    source_release,
    source_stopped=False,
    data_loss_confirmed=False,
    maximum=snapshots.DEFAULT_BYTES,
    mail_file: Path | None = None,
    expected_mail: str | None = None,
):
    if source_stopped is not True or data_loss_confirmed is not True:
        raise maintenance.MaintenanceError(
            "rollback restoration requires physical execution fencing and explicit newer-data-loss confirmation"
        )
    identity = _identity(operation_id, rollback_id, plan_id, source_release, expected, maximum)
    destination = destination.absolute()
    try:
        key_digest = hashlib.sha256(snapshots._key(key_file)).hexdigest()
        mail = None
        if mail_file is not None or expected_mail is not None:
            if mail_file is None or not maintenance._hex(expected_mail):
                raise maintenance.MaintenanceError("rollback mail requires its original private input and exact digest")
            mail = read_private(mail_file, 8192)
            if mail_binding(mail)["input_sha256"] != expected_mail:
                raise maintenance.MaintenanceError("original rollback mail input changed")
        base = {**identity, "source_schema": len(MIGRATIONS), "backup_key_sha256": key_digest, "mail_sha256": expected_mail}
        if len(MIGRATIONS) < 13:
            raise maintenance.MaintenanceError("rollback restoration requires a compatible source release")
        maintenance._retained(archive, key_file, expected, key_digest, maximum)
        if destination.exists() or destination.is_symlink():
            value = _read(destination, JOURNAL)
            if value is None or canonical({key: value[key] for key in base}) != canonical(base):
                raise maintenance.MaintenanceError("rollback cannot adopt an unowned existing restoration")
        else:

            def before_publish(root, _manifest, recovery):
                if mail is not None:
                    atomic_write(root / "mail.json", mail)
                fd = os.open(root / maintenance.LOCK, os.O_RDONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                try:
                    os.fchmod(fd, 0o600)
                    os.fsync(fd)
                finally:
                    os.close(fd)
                value = {
                    **base,
                    "phase": "prepared",
                    "recovery": recovery,
                    "state_sha256": maintenance.state_identity(root, len(MIGRATIONS), maximum),
                }
                atomic_write(root / JOURNAL, canonical(value))

            # Provenance and the application startup fence are synced INSIDE
            # the new state before atomic no-replace publication. A lost reply
            # reuses this exact restoration rather than rotating credentials.
            snapshots.restore(
                archive, key_file, expected, destination, source_stopped=True, maximum=maximum, _before_publish=before_publish
            )
        result = verify_prepared(destination, rollback_id=rollback_id, source_release=source_release)
        maintenance._retained(archive, key_file, expected, key_digest, maximum)
        return result
    except (OSError, sqlite3.Error, snapshots.BackupError, InstallError) as exc:
        raise maintenance.MaintenanceError("rollback restoration failed; no existing installation was overwritten") from exc


def release(root: Path, *, rollback_id, source_release, activation_confirmed=False):
    """Explicit activation boundary; does not start a service or verify health."""
    if activation_confirmed is not True or not maintenance._hex(rollback_id) or not maintenance._hex(source_release):
        raise maintenance.MaintenanceError("source release requires exact rollback identity and explicit activation confirmation")
    root = root.absolute()
    try:
        with _leases(root, exclusive=True):
            value = _read(root, JOURNAL)
            if (
                value is None
                or value["phase"] not in {"prepared", "released"}
                or value["rollback_id"] != rollback_id
                or value["source_release"] != source_release
                or value["source_schema"] != len(MIGRATIONS)
                or _read(root, REQUEST) is not None
                or (root / HISTORY).exists()
                or (root / HISTORY).is_symlink()
            ):
                raise maintenance.MaintenanceError("source activation requires the original bound rollback restoration")
            if value["phase"] == "prepared":
                if maintenance.state_identity(root, len(MIGRATIONS), value["maximum_bytes"]) != value["state_sha256"]:
                    raise maintenance.MaintenanceError("prepared source changed before rollback activation")
                value["phase"] = "released"
                atomic_write(root / JOURNAL, canonical(value))
            else:
                with snapshots._input(root / "server.sqlite3", snapshots.MAX_BYTES), snapshots._connect(root / "server.sqlite3") as db:
                    snapshots._schema(db)
            return {**value, "activation_permitted": True, "server_started": False, "health_verified": False}
    except (OSError, sqlite3.Error, snapshots.BackupError, InstallError) as exc:
        raise maintenance.MaintenanceError("rollback source release failed; no server was started") from exc


def observe(root: Path, *, rollback_id, source_release):
    if not maintenance._hex(rollback_id) or not maintenance._hex(source_release):
        raise maintenance.MaintenanceError("rollback observation requires exact source and rollback identities")
    root = root.absolute()
    try:
        with _leases(root, exclusive=False):
            value = _read(root, JOURNAL)
            if (
                value is None
                or value["phase"] != "released"
                or value["rollback_id"] != rollback_id
                or value["source_release"] != source_release
                or value["source_schema"] != len(MIGRATIONS)
                or _read(root, REQUEST) is not None
                or (root / HISTORY).exists()
                or (root / HISTORY).is_symlink()
            ):
                raise maintenance.MaintenanceError("rollback observation requires the original released source")
            with snapshots._input(root / "server.sqlite3", snapshots.MAX_BYTES), snapshots._connect(root / "server.sqlite3") as db:
                snapshots._schema(db)
            return {**value, "database_schema_verified": True, "server_started": False, "health_verified": False}
    except (OSError, sqlite3.Error, snapshots.BackupError, InstallError) as exc:
        raise maintenance.MaintenanceError("rollback source observation failed; no server was started") from exc
