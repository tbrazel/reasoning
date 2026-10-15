"""Saved recovery plans and separately confirmed changes over verified SSH."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import tempfile
from contextlib import ExitStack, contextmanager
from pathlib import Path

from . import host, server_changes, server_operations, server_recovery, server_upgrade

MODES = {"recovery-prepare", "recovery-activate"}
FIELDS = {
    "format",
    "operation",
    "installation",
    "database_schema",
    "backup_sha256",
    "backup_key_sha256",
    "maximum_bytes",
    "mail",
    "provisioning",
}
UNCERTAIN = "remote recovery is unverified; host changes may have occurred; resume the original recovery and inputs"


def validate_plan(plan, confirmation):
    try:
        if not isinstance(plan, dict) or set(plan) != FIELDS | {"input_sha256", "host_changed", "archive_authenticated"}:
            raise ValueError
        identity = {name: plan[name] for name in FIELDS}
        binding = identity["installation"]
        if (
            type(identity["format"]) is not int
            or identity["format"] != 1
            or identity["operation"] != "recovery-plan"
            or plan["host_changed"] is not False
            or plan["archive_authenticated"] is not False
            or not server_upgrade._hex(confirmation)
            or plan["input_sha256"] != confirmation
            or hashlib.sha256(host.canonical(identity)).hexdigest() != confirmation
            or not isinstance(binding, dict)
            or set(binding) != {"release_id", "bundle_sha256", "public_url", "source_revision", "dirty"}
            or binding["dirty"] is not False
            or any(not server_upgrade._hex(binding[name]) for name in ("release_id", "bundle_sha256"))
            or not isinstance(binding["source_revision"], str)
            or re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", binding["source_revision"]) is None
            or host.public_origin(binding["public_url"]) != binding["public_url"]
            or type(identity["database_schema"]) is not int
            or not 13 <= identity["database_schema"] <= 1000
            or any(not server_upgrade._hex(identity[name]) for name in ("backup_sha256", "backup_key_sha256"))
            or type(identity["maximum_bytes"]) is not int
            or not 1024**2 <= identity["maximum_bytes"] <= 64 * 1024**3
        ):
            raise ValueError
        mail = identity["mail"]
        if mail is not None and (
            not isinstance(mail, dict) or set(mail) != {"input_sha256"} or not server_upgrade._hex(mail["input_sha256"])
        ):
            raise ValueError
        if identity["provisioning"] is not None and host.canonical(host.provisioning_request(identity["provisioning"])) != host.canonical(
            identity["provisioning"]
        ):
            raise ValueError
        return identity
    except (host.InstallError, KeyError, TypeError, ValueError, RecursionError) as exc:
        raise host.InstallError("recovery requires the exact saved clean-release input plan") from exc


@contextmanager
def binary_snapshot(original, maximum, *, expected=None, exact_size=None):
    """Stream private operator data into an immutable-for-this-request snapshot."""
    from .private import protected_parent

    original = original.absolute()
    protected_parent(original, writable=False)
    with tempfile.TemporaryDirectory(prefix="reasoning-recovery-input-") as temporary:
        target = Path(temporary) / "input"
        fd = os.open(original, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as source:
            info = os.fstat(source.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_mode & 0o077
                or info.st_nlink != 1
                or not 0 < info.st_size <= maximum
                or exact_size is not None
                and info.st_size != exact_size
            ):
                raise host.InstallError("recovery inputs must be private owner-owned unlinked files within their exact allowance")
            digest, total = hashlib.sha256(), 0
            out = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(out, "wb") as output:
                os.fchmod(output.fileno(), 0o600)
                while body := source.read(65536):
                    total += len(body)
                    if total > info.st_size:
                        raise host.InstallError("recovery input changed during snapshot")
                    digest.update(body)
                    output.write(body)
            checksum = digest.hexdigest()
            if total != info.st_size or expected is not None and checksum != expected:
                raise host.InstallError("recovery input differs from its exact original identity")
        yield target, checksum


def plan_recovery(
    bundle, backup, key_file, origin, *, confirmed_release, expected_backup, maximum=8 * 1024**3, mail_file=None, provisioning_file=None
):
    from .cli import bundle_snapshot
    from .private import read_document, read_private

    if (
        not server_upgrade._hex(expected_backup)
        or not server_upgrade._hex(confirmed_release)
        or type(maximum) is not int
        or not 1024**2 <= maximum <= 64 * 1024**3
    ):
        raise host.InstallError("recovery planning requires exact release/backup identities and a bounded payload allowance")
    host.public_origin(origin)
    mail = read_private(mail_file, host.MAX_MAIL_INPUT) if mail_file is not None else None
    provisioning = read_document(provisioning_file) if provisioning_file is not None else None
    with (
        bundle_snapshot(bundle, confirmed_release) as archive,
        binary_snapshot(backup, server_upgrade.MAX_BACKUP, expected=expected_backup),
        binary_snapshot(key_file, 32, exact_size=32) as (_, key_checksum),
    ):
        manifest, release, checksum = host.inspect_bundle(archive)
        if "server-recovery-activation-v1" not in manifest.get("installation_features", []):
            raise host.InstallError("recovery operator commands require a release with preparation and activation support")
        identity, digest = server_recovery.recovery_identity(
            manifest,
            release,
            checksum,
            origin,
            expected_backup,
            key_checksum,
            maximum=maximum,
            mail=mail,
            provisioning=provisioning,
        )
    return {**identity, "input_sha256": digest, "host_changed": False, "archive_authenticated": False}


def remote_script(request):
    return server_changes._remote_script(
        request,
        (
            ("host", host),
            ("server_operations", server_operations),
            ("server_upgrade", server_upgrade),
            ("server_recovery", server_recovery),
            ("server_changes", server_changes),
            ("recovery_changes", sys.modules[__name__]),
        ),
        endpoint="recovery_changes",
    )


def _receive(stream, path, size, checksum):
    digest = hashlib.sha256()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as output:
        os.fchmod(output.fileno(), 0o600)
        remaining = size
        while remaining:
            body = stream.read(min(remaining, 65536))
            if not body or len(body) > remaining:
                raise host.InstallError("recovery upload was interrupted or exceeded its original size")
            digest.update(body)
            output.write(body)
            remaining -= len(body)
    if digest.hexdigest() != checksum:
        raise host.InstallError("uploaded recovery input differs from its confirmed digest")


def execute_remote(request, stream, *, engine=None):
    """Standard-library root endpoint; uploaded bytes are data, never tool sources."""
    if not isinstance(request, dict) or set(request) != {
        "mode",
        "identity",
        "recovery_id",
        "input_sha256",
        "execution_fenced",
        "activation_confirmed",
        "upload_sizes",
    }:
        raise host.InstallError("invalid recovery endpoint request")
    mode = request["mode"]
    if (
        not isinstance(mode, str)
        or mode not in MODES
        or not server_upgrade._hex(request["recovery_id"])
        or request["execution_fenced"] is not (mode == "recovery-prepare")
        or request["activation_confirmed"] is not (mode == "recovery-activate")
    ):
        raise host.InstallError("recovery endpoint requires exact identities and separate mode-specific acknowledgments")
    identity = validate_plan(
        {
            **request["identity"],
            "input_sha256": request["input_sha256"],
            "host_changed": False,
            "archive_authenticated": False,
        },
        request["input_sha256"],
    )
    engine = engine or server_recovery.HostRecovery()
    confirmation, recovery_id = request["input_sha256"], request["recovery_id"]
    binding = identity["installation"]
    sizes = request["upload_sizes"]
    if mode == "recovery-activate":
        if sizes != [] or stream.read(1):
            raise host.InstallError("recovery activation cannot accept replacement inputs")
        retained = engine._read(engine.directory / "server-recoveries" / recovery_id / "operation.json")
        if retained["input_sha256"] != confirmation or host.canonical(retained["identity"]) != host.canonical(identity):
            raise host.InstallError("retained host recovery differs from the saved operator plan")
        return engine.activate(
            recovery_id=recovery_id,
            confirmed_recovery=confirmation,
            target_release=binding["release_id"],
            activation_confirmed=True,
        )
    specifications = [
        ("release.tar.gz", host.MAX_BUNDLE, binding["bundle_sha256"]),
        ("source.rnbk", server_upgrade.MAX_BACKUP, identity["backup_sha256"]),
        ("backup.key", 32, identity["backup_key_sha256"]),
    ]
    if identity["mail"] is not None:
        specifications.append(("mail.json", host.MAX_MAIL_INPUT, identity["mail"]["input_sha256"]))
    if (
        not isinstance(sizes, list)
        or len(sizes) != len(specifications)
        or any(type(size) is not int or not 0 < size <= specification[1] for size, specification in zip(sizes, specifications, strict=True))
        or sizes[2] != 32
    ):
        raise host.InstallError("recovery upload sizes differ from their bounded original inputs")
    engine.installer._directory("var/lib/reasoning-deploy", 0o700)
    engine.inspector._directory(engine.directory, private=True)
    parent = engine.installer._directory("var/lib/reasoning-deploy/staging", 0o700)
    engine.inspector._directory(parent, private=True)
    with tempfile.TemporaryDirectory(prefix="server-recovery-", dir=parent) as temporary:
        stage = Path(temporary)
        for size, (name, _, checksum) in zip(sizes, specifications, strict=True):
            _receive(stream, stage / name, size, checksum)
        if stream.read(1):
            raise host.InstallError("unexpected bytes after original recovery inputs")
        mail = engine.inspector._file(stage / "mail.json", host.MAX_MAIL_INPUT, private=True) if identity["mail"] is not None else None
        manifest, release, checksum = host.inspect_bundle(stage / "release.tar.gz", binding["bundle_sha256"])
        actual, digest = server_recovery.recovery_identity(
            manifest,
            release,
            checksum,
            binding["public_url"],
            identity["backup_sha256"],
            identity["backup_key_sha256"],
            maximum=identity["maximum_bytes"],
            mail=mail,
            provisioning=identity["provisioning"],
        )
        if (
            digest != confirmation
            or host.canonical(actual) != host.canonical(identity)
            or "server-recovery-activation-v1" not in manifest.get("installation_features", [])
        ):
            raise host.InstallError("uploaded release differs from the original recovery plan or lacks activation support")
        return engine.prepare(
            stage / "release.tar.gz",
            checksum,
            binding["public_url"],
            stage / "source.rnbk",
            identity["backup_sha256"],
            stage / "backup.key",
            identity["backup_key_sha256"],
            recovery_id=recovery_id,
            confirmed_recovery=confirmation,
            execution_fenced=True,
            maximum=identity["maximum_bytes"],
            mail=mail,
            provisioning=identity["provisioning"],
        )


def validate_receipt(value, request):
    try:
        activated = request["mode"] == "recovery-activate"
        restoration = server_recovery.restoration_result(value["restoration"], request)
        expected = {
            "format": 1,
            "operation": request["mode"],
            "recovery_id": request["recovery_id"],
            "input_sha256": request["input_sha256"],
            "installation": request["identity"]["installation"],
            "phase": "completed" if activated else "prepared",
            "server_restart_fenced": not activated,
            "root_private_inputs_retained": True,
            "replacement_data_published": activated,
            "activation_performed": activated,
            "recovery_completed": activated,
            "physical_source_fencing_verified": False,
            "runtime_dependency_attestation": False,
            "restoration": restoration,
            "recovery_directory": "/var/lib/reasoning-deploy/server-recoveries/" + request["recovery_id"],
        }
        if activated:
            expected.update(https_release_verified=True, database_schema_verified=True)
        if (
            not isinstance(value, dict)
            or set(value) != set(expected)
            or any(
                type(value[key]) is not type(item) or host.canonical(value[key]) != host.canonical(item) for key, item in expected.items()
            )
        ):
            raise ValueError
        return value
    except (host.InstallError, KeyError, TypeError, ValueError, RecursionError) as exc:
        raise host.InstallError(UNCERTAIN) from exc


def recover_remote(
    mode,
    plan,
    hostname,
    identity,
    known_hosts,
    state_directory,
    *,
    recovery_id,
    confirmed_recovery,
    bundle=None,
    backup=None,
    key_file=None,
    mail_file=None,
    execution_fenced=False,
    activation_confirmed=False,
    command=None,
):
    from .cli import bundle_snapshot
    from .private import locked_directory, read_document, write_document

    binding = validate_plan(plan, confirmed_recovery)
    preparing = mode == "recovery-prepare"
    if (
        mode not in MODES
        or not server_upgrade._hex(recovery_id)
        or execution_fenced is not preparing
        or activation_confirmed is not (not preparing)
        or preparing
        and any(value is None for value in (bundle, backup, key_file))
        or not preparing
        and any(value is not None for value in (bundle, backup, key_file, mail_file))
        or preparing
        and ((mail_file is None) != (binding["mail"] is None))
    ):
        raise host.InstallError("recovery requires exact original identities and mode-specific inputs/acknowledgments")
    with ExitStack() as stack:
        options, transport = stack.enter_context(server_changes.snapshot_ssh(hostname, identity, known_hosts))
        files = []
        if preparing:
            archive = stack.enter_context(bundle_snapshot(bundle, binding["installation"]["release_id"]))
            manifest, release, checksum = host.inspect_bundle(archive, binding["installation"]["bundle_sha256"])
            files = [archive]
            for original, maximum, expected, size in (
                (backup, server_upgrade.MAX_BACKUP, binding["backup_sha256"], None),
                (key_file, 32, binding["backup_key_sha256"], 32),
                *([(mail_file, host.MAX_MAIL_INPUT, binding["mail"]["input_sha256"], None)] if mail_file is not None else []),
            ):
                snapshot, _ = stack.enter_context(binary_snapshot(original, maximum, expected=expected, exact_size=size))
                files.append(snapshot)
            mail = files[3].read_bytes() if mail_file is not None else None
            actual, digest = server_recovery.recovery_identity(
                manifest,
                release,
                checksum,
                binding["installation"]["public_url"],
                binding["backup_sha256"],
                binding["backup_key_sha256"],
                maximum=binding["maximum_bytes"],
                mail=mail,
                provisioning=binding["provisioning"],
            )
            if (
                digest != confirmed_recovery
                or host.canonical(actual) != host.canonical(binding)
                or "server-recovery-activation-v1" not in manifest.get("installation_features", [])
            ):
                raise host.InstallError("recovery release/input snapshot differs from the saved plan")
        directory = stack.enter_context(locked_directory(state_directory))
        local = directory / "operation.json"
        expected = {
            "format": 1,
            "recovery_id": recovery_id,
            "input_sha256": confirmed_recovery,
            "identity": binding,
            "transport": transport,
        }
        if local.exists() or local.is_symlink():
            if host.canonical(read_document(local)) != host.canonical(expected):
                raise host.InstallError("original operator recovery, plan or SSH trust changed")
        elif not preparing:
            raise host.InstallError("retain the original operator recovery directory for activation")
        else:
            write_document(local, expected, new=True)
        request = {
            "mode": mode,
            "identity": binding,
            "recovery_id": recovery_id,
            "input_sha256": confirmed_recovery,
            "execution_fenced": execution_fenced,
            "activation_confirmed": activation_confirmed,
            "upload_sizes": [path.stat().st_size for path in files],
        }
        script = remote_script(request)

        def chunks():
            yield (str(len(script)) + "\n").encode("ascii")
            for offset in range(0, len(script), 65536):
                yield script[offset : offset + 65536]
            for path in files:
                with path.open("rb") as stream:
                    while body := stream.read(65536):
                        yield body

        args = ["ssh", "-T", *options, "root@" + hostname, shlex.join(["python3", "-I", "-S", "-B", "-c", server_changes.LAUNCHER])]
        try:
            response = (command or server_changes._run)(args, chunks())
            if response.returncode != 0 or not isinstance(response.stdout, bytes) or len(response.stdout) > server_changes.MAX_RECEIPT:
                raise ValueError
            value = json.loads(response.stdout, object_pairs_hook=server_upgrade._pairs)
            receipt = validate_receipt(value, request)
        except (host.InstallError, AttributeError, OSError, TypeError, ValueError, RecursionError, subprocess.SubprocessError) as exc:
            raise host.InstallError(UNCERTAIN) from exc
        receipt_path = directory / (mode + ".json")
        if receipt_path.exists() or receipt_path.is_symlink():
            if host.canonical(read_document(receipt_path)) != host.canonical(receipt):
                raise host.InstallError(UNCERTAIN)
        else:
            write_document(receipt_path, receipt, new=True)
        return receipt
