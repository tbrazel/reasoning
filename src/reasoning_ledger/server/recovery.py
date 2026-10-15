"""Authenticated replacement-state preparation using the foreground maintenance fence."""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

from ..deployment.host import InstallError, atomic_write, canonical, mail_binding
from ..deployment.private import read_private
from . import backup as snapshots
from . import maintenance
from .migrations import MIGRATIONS
from .rollback import COUNTS, OPTIONAL_COUNTS

RECEIPT = "server-recovery.json"
HISTORY = "recovery-history"


def _read(root):
    path = root / RECEIPT
    if not path.exists() and not path.is_symlink():
        if (root / HISTORY).exists() or (root / HISTORY).is_symlink():
            raise maintenance.MaintenanceError("replacement recovery receipt is missing; retained provenance cannot be bypassed")
        return None
    try:
        body = read_private(path, 8192)
        value = json.loads(body)
        fields = {
            "format",
            "operation",
            "operation_id",
            "target_release",
            "backup_sha256",
            "backup_key_sha256",
            "mail_sha256",
            "maximum_bytes",
            "maintenance",
            "recovery",
            "prepared_state_verified",
            "server_started",
            "activation_performed",
            "sql_migration_performed",
        }
        if (
            not isinstance(value, dict)
            or set(value) != fields
            or canonical(value) != body
            or type(value["format"]) is not int
            or value["format"] != 1
            or value["operation"] != "recovery-prepare"
            or any(not maintenance._hex(value[name]) for name in ("operation_id", "target_release", "backup_sha256", "backup_key_sha256"))
            or value["mail_sha256"] is not None
            and not maintenance._hex(value["mail_sha256"])
            or type(value["maximum_bytes"]) is not int
            or not 1024**2 <= value["maximum_bytes"] <= snapshots.MAX_BYTES
            or value["prepared_state_verified"] is not True
            or value["server_started"] is not False
            or value["activation_performed"] is not False
            or value["sql_migration_performed"] is not False
            or not isinstance(value["maintenance"], dict)
            or set(value["maintenance"]) != maintenance.INTENT_FIELDS | {"phase"}
            or value["maintenance"]["phase"] != "migrated"
            or not isinstance(value["recovery"], dict)
            or not COUNTS <= set(value["recovery"]) <= COUNTS | OPTIONAL_COUNTS
            or any(type(count) is not int or not 0 <= count <= 2**63 - 1 for count in value["recovery"].values())
        ):
            raise ValueError
        intent = maintenance._intent({key: value["maintenance"][key] for key in maintenance.INTENT_FIELDS})
        if (
            any(
                type(intent[key]) is not type(value[key]) or intent[key] != value[key]
                for key in ("operation_id", "target_release", "backup_sha256", "backup_key_sha256", "maximum_bytes")
            )
            or intent["source_schema"] != intent["target_schema"]
        ):
            raise ValueError
        snapshots._directory(root / HISTORY)
        if read_private(root / HISTORY / (value["operation_id"] + ".json"), 8192) != body:
            raise ValueError
        return value
    except (OSError, KeyError, TypeError, ValueError, RecursionError, InstallError) as exc:
        raise maintenance.MaintenanceError(
            "replacement recovery provenance is missing, malformed or insecure; retain original evidence"
        ) from exc


def assert_maintenance_allowed(root, current):
    """Return True only for this exact restoration; later migrations stay idle-only."""
    value = _read(root)
    if value is None:
        return False
    original = value["maintenance"]
    if current is None:
        raise maintenance.MaintenanceError("replacement recovery requires its original maintenance journal")
    if current["operation_id"] == value["operation_id"]:
        if canonical({key: current[key] for key in maintenance.INTENT_FIELDS}) != canonical(
            {key: original[key] for key in maintenance.INTENT_FIELDS}
        ):
            raise maintenance.MaintenanceError("replacement recovery maintenance intent changed")
        if current["phase"] == "completed":
            maintenance._history(root, {**original, "phase": "completed"}, publish=False)
        return True
    maintenance._history(root, {**original, "phase": "completed"}, publish=False)
    if current["source_schema"] < original["target_schema"] or current["target_release"] == value["target_release"]:
        raise maintenance.MaintenanceError("successor migration cannot bypass completed replacement recovery")
    return False


def prepare(
    archive: Path,
    key_file: Path,
    expected: str,
    destination: Path,
    *,
    operation_id: str,
    target_release: str,
    source_stopped: bool = False,
    maximum: int = snapshots.DEFAULT_BYTES,
    mail_file: Path | None = None,
    expected_mail: str | None = None,
):
    if source_stopped is not True or not all(map(maintenance._hex, (operation_id, target_release, expected))):
        raise maintenance.MaintenanceError(
            "replacement recovery requires exact identities and physical source/execution fencing confirmation"
        )
    snapshots._allowance(maximum)
    if len(MIGRATIONS) < maintenance.MIN_SOURCE_SCHEMA:
        raise maintenance.MaintenanceError("replacement recovery requires a supported exact source schema")
    if (mail_file is None) != (expected_mail is None):
        raise maintenance.MaintenanceError("replacement mail requires both its original private input and digest")
    mail = None
    if mail_file is not None:
        mail = read_private(mail_file, 8192)
        if mail_binding(mail)["input_sha256"] != expected_mail:
            raise maintenance.MaintenanceError("replacement mail differs from its confirmed input")
    destination = destination.absolute()
    try:
        key = snapshots._key(key_file)
        key_digest = hashlib.sha256(key).hexdigest()
        # Authenticate the matching source schema and fingerprint the unmodified
        # archived state independently of the restore's revocation/rotation step.
        with tempfile.TemporaryDirectory(prefix=".reasoning-recovery-", dir=archive.absolute().parent) as temporary:
            raw = Path(temporary)
            snapshots._unpack(archive.absolute(), key, expected, raw, maximum)
            source_identity = maintenance.state_identity(raw, len(MIGRATIONS), maximum)
        expected_binding = {
            "operation_id": operation_id,
            "target_release": target_release,
            "backup_sha256": expected,
            "backup_key_sha256": key_digest,
            "mail_sha256": expected_mail,
            "maximum_bytes": maximum,
        }
        if destination.exists() or destination.is_symlink():
            snapshots._directory(destination)
            value = _read(destination)
            if value is None or any(type(value[name]) is not type(item) or value[name] != item for name, item in expected_binding.items()):
                raise maintenance.MaintenanceError("replacement recovery cannot adopt existing or differently bound state")
            if value["maintenance"]["source_state_sha256"] != source_identity:
                raise maintenance.MaintenanceError("replacement recovery source identity changed")
            maintenance.verify_prepared(destination, operation_id=operation_id, target_release=target_release)
            return value

        def before_publish(root, _manifest, recovery):
            if mail is not None:
                atomic_write(root / "mail.json", mail)
            target_identity = maintenance.state_identity(root, len(MIGRATIONS), maximum)
            current = {
                "format": 1,
                "operation_id": operation_id,
                "backup_sha256": expected,
                "backup_key_sha256": key_digest,
                "source_schema": len(MIGRATIONS),
                "target_schema": len(MIGRATIONS),
                "target_release": target_release,
                "maximum_bytes": maximum,
                "source_state_sha256": source_identity,
                "target_state_sha256": target_identity,
                "phase": "migrated",
            }
            # This is authenticated restore preparation, not migrate-offline:
            # uncertain jobs remain held, and no SQL migration/idle waiver for
            # ordinary upgrades is performed. The existing activation guard
            # still validates exact prepared state before any server workers run.
            value = {
                "format": 1,
                "operation": "recovery-prepare",
                **expected_binding,
                "maintenance": current,
                "recovery": recovery,
                "prepared_state_verified": True,
                "server_started": False,
                "activation_performed": False,
                "sql_migration_performed": False,
            }
            atomic_write(root / maintenance.JOURNAL, canonical(current))
            history = root / HISTORY
            history.mkdir(mode=0o700)
            atomic_write(history / (operation_id + ".json"), canonical(value))
            atomic_write(root / RECEIPT, canonical(value))
            for name in (maintenance.LOCK, "initialization.lock"):
                snapshots._write_new(root / name, b"")

        snapshots.restore(archive, key_file, expected, destination, source_stopped=True, maximum=maximum, _before_publish=before_publish)
        maintenance.verify_prepared(destination, operation_id=operation_id, target_release=target_release)
        return _read(destination)
    except (OSError, snapshots.BackupError, InstallError) as exc:
        raise maintenance.MaintenanceError(
            "replacement recovery preparation failed; preserve original inputs; no server was started"
        ) from exc
