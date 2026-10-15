"""Read-only installed-server diagnostics and release-change preflight.

This module is also distributed as a standalone standard-library script. It
does not install packages, open the application database, or start services.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import io
import json
import os
import re
import stat
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath

try:
    from . import host
except ImportError:  # Standalone release bundle, alongside installer.py.
    import installer as host

MAX_REPORT = 32768
CHECKS = (
    "installation_journal",
    "bootstrap_lock",
    "release_payload",
    "installed_python_sources",
    "server_unit",
    "proxy_unit",
    "proxy_configuration",
    "loaded_server_unit",
    "loaded_proxy_unit",
    "server_active",
    "proxy_active",
    "compute_catalog",
    "private_server_state",
    "mail_configuration",
    "provisioning_identity",
    "https_health",
    "https_release_identity",
)
STATUSES = {"pass", "fail", "unverified", "not_applicable"}
PENDING = [
    "physical_worker_drain_and_fencing",
    "encrypted_backup_and_matching_release_verification",
    "offline_database_schema_and_migration_verification",
    "runtime_dependencies_interpreter_and_bytecode_verification",
    "journaled_activation_and_interruption_recovery",
    "post_activation_health_and_original_vm_verification",
    "explicit_rollback_and_data_restore_policy",
]


def _digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def release_binding(manifest: dict, release: str, checksum: str, origin: str) -> dict:
    # Revalidate even decoded remote input; canonical manifest validation also
    # bounds names, sizes, schema numbers, features, and source identifiers.
    host.validate_manifest(host.canonical(manifest))
    for value in (release, checksum):
        if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
            raise host.InstallError("invalid operation release identity")
    if _digest(host.canonical(manifest)) != release:
        raise host.InstallError("operation manifest does not match its release identity")
    return host.installation_binding(manifest, release, checksum, origin)


class ServerInspector:
    """Bounded, secret-free evidence about one exact managed installation."""

    def __init__(self, *, root: Path = Path("/"), command=None, health=None, droplet_id=None):
        self.root = root.absolute()
        self.command = command or self._command
        self.health = health or host.ready
        self.droplet_id = droplet_id or host.HostInstaller._droplet_id

    @staticmethod
    def _command(args):
        # Only systemctl show is called. Do not use the installer's 20-minute
        # package-command deadline for a read-only diagnosis.
        return subprocess.run(args, env=host.ENVIRONMENT, capture_output=True, timeout=10, check=False)

    def _directory(self, path: Path, *, uid=None, private=False):
        uid = os.getuid() if uid is None else uid
        if self.root not in (path, *path.parents):
            raise host.InstallError("inspection path leaves the managed host")
        info = self.root.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise host.InstallError("inspection root is absent, linked, or insecure")
        if path == self.root:
            return
        for parent in reversed((path, *path.parents[: path.parents.index(self.root)])):
            info = parent.lstat()
            owner = uid if parent == path else os.getuid()
            mask = 0o077 if parent == path and private else 0o022
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != owner or info.st_mode & mask:
                raise host.InstallError("inspection directory is absent, linked, or insecure")

    def _file(self, path: Path, maximum: int, *, uid=None, private=False) -> bytes:
        self._directory(path.parent)
        uid = os.getuid() if uid is None else uid
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != uid
                or info.st_mode & (0o077 if private else 0o022)
                or info.st_nlink != 1
                or info.st_size > maximum
            ):
                raise host.InstallError("inspection file is absent, linked, oversized, or insecure")
            body = stream.read(maximum + 1)
            if len(body) > maximum or len(body) != info.st_size:
                raise host.InstallError("inspection file changed or exceeded its allowance")
        return body

    def _document(self, path: Path):
        body = self._file(path, host.MAX_MANIFEST, private=True)
        value = json.loads(body)
        if not isinstance(value, dict) or host.canonical(value) != body:
            raise host.InstallError("inspection document is not canonical")
        return value

    def _payload(self, manifest, release):
        directory = self.root / "opt/reasoning/releases" / release
        self._directory(directory)
        for name, row in manifest["files"].items():
            body = self._file(directory / name, row["size"])
            if len(body) != row["size"] or _digest(body) != row["sha256"]:
                raise host.InstallError("installed release payload differs from its manifest")

    def _python_sources(self, manifest, release):
        directory = self.root / "opt/reasoning/releases" / release
        source = "source/reasoning-source.zip" in manifest["files"]
        name = "source/reasoning-source.zip" if source else next(name for name in manifest["files"] if name.startswith("wheel/"))
        row = manifest["files"][name]
        body = self._file(directory / name, row["size"])
        if _digest(body) != row["sha256"]:
            raise host.InstallError("installed wheel identity differs")
        site = directory / "venv/lib/python3.12/site-packages"
        self._directory(site)
        expected = set()
        total = 0
        with zipfile.ZipFile(io.BytesIO(body)) as wheel:
            entries = wheel.infolist()
            if not 1 <= len(entries) <= 4096:
                raise host.InstallError("installed wheel entry allowance exceeded")
            seen = set()
            for entry in entries:
                path = PurePosixPath(entry.filename)
                if (
                    str(path) != entry.filename
                    or path.is_absolute()
                    or ".." in path.parts
                    or "\\" in entry.filename
                    or entry.filename in seen
                    or entry.is_dir()
                    or entry.file_size > 32 * 1024**2
                    or stat.S_ISLNK(entry.external_attr >> 16)
                ):
                    raise host.InstallError("installed wheel contains unsafe entries")
                seen.add(entry.filename)
                total += entry.file_size
                if total > host.MAX_BUNDLE:
                    raise host.InstallError("installed wheel expanded allowance exceeded")
                # pip regenerates RECORD and entry points; do not compare them
                # to wheel bytes or claim they have been attested here.
                if path.parts[0] != "reasoning_ledger":
                    if source and entry.filename in {"source-info.json", "requirements.txt", "modal-requirements.txt", "LICENSE", "NOTICE"}:
                        continue
                    if len(path.parts) < 2 or not re.fullmatch(r"reasoning_ledger-[A-Za-z0-9.]+\.dist-info", path.parts[0]):
                        raise host.InstallError("installed wheel has an unexpected package layout")
                    continue
                expected.add(entry.filename)
                installed = self._file(site / entry.filename, entry.file_size)
                if installed != wheel.read(entry):
                    raise host.InstallError("installed Python sources differ from the selected wheel")
            if source:
                metadata = json.loads(wheel.read("source-info.json"))
                for script, entry_point in metadata["scripts"].items():
                    if not re.fullmatch(r"reasoning-[a-z][a-z-]{0,63}", script):
                        raise host.InstallError("invalid source entry point")
                    module = entry_point.split(":")[0]
                    expected_script = (f"#!{directory}/venv/bin/python\nfrom {module} import main\n"
                                       "if __name__ == '__main__':\n    raise SystemExit(main())\n").encode()
                    if self._file(directory / "venv/bin" / script, 4096) != expected_script:
                        raise host.InstallError("installed source entry point differs")
        if not {"reasoning_ledger/server/api.py", "reasoning_ledger/server/cli.py"} <= expected:
            raise host.InstallError("installed wheel has no public server")
        count = 0
        for current, directories, files in os.walk(site / "reasoning_ledger", followlinks=False):
            self._directory(Path(current))
            for name in directories:
                self._directory(Path(current) / name)
                count += 1
            for name in files:
                count += 1
                path = Path(current) / name
                if path.is_symlink() or path.suffix in {".py", ".so", ".pyd"} and str(path.relative_to(site)) not in expected:
                    raise host.InstallError("unexpected installed Python source or native module")
            if count > 8192:
                raise host.InstallError("installed Python file allowance exceeded")

    def _loaded(self, unit, account):
        properties = ("FragmentPath", "DropInPaths", "User", "Group", "LoadState", "NeedDaemonReload", "ActiveState")
        response = self.command(["systemctl", "show", "--no-pager", "--property=" + ",".join(properties), unit])
        if response.returncode != 0 or not isinstance(response.stdout, bytes) or len(response.stdout) > 4096:
            raise host.InstallError("loaded service information is unavailable")
        rows = [line.split("=", 1) for line in response.stdout.decode().splitlines()]
        value = dict(rows)
        if len(rows) != len(properties) or set(value) != set(properties):
            raise host.InstallError("loaded service information is malformed")
        expected = {
            "FragmentPath": f"/etc/systemd/system/{unit}.service",
            "DropInPaths": "",
            "User": account,
            "Group": account,
            "LoadState": "loaded",
            "NeedDaemonReload": "no",
        }
        if any(value[key] != item for key, item in expected.items()):
            raise host.InstallError("loaded service identity differs from managed configuration")
        return value["ActiveState"] == "active"

    def inspect(self, manifest, release, checksum, origin, *, owned_bootstrap_fd=None):
        binding = release_binding(manifest, release, checksum, origin)
        checks = {name: "unverified" for name in CHECKS}
        configuration = {"compute_catalog_sha256": None, "mail_input_sha256": None}
        state_dir = self.root / "var/lib/reasoning-deploy"
        data_dir = self.root / "var/lib/reasoning"

        def check(name, action):
            try:
                value = action()
                checks[name] = "pass"
                return value
            except (
                host.InstallError,
                OSError,
                ValueError,
                TypeError,
                KeyError,
                subprocess.SubprocessError,
                zipfile.BadZipFile,
                RuntimeError,
            ):
                # Never include filenames from database contents, arbitrary
                # service output, exception strings, credentials, or log bodies.
                checks[name] = "fail"
                return None

        def journal():
            self._directory(state_dir, private=True)
            value = self._document(state_dir / "installation.json")
            if host.canonical(value) != host.canonical({**binding, "phase": "services_started"}):
                raise host.InstallError("installed release journal differs")

        check("installation_journal", journal)

        def lock():
            path = state_dir / "bootstrap.lock"
            self._file(path, 0, private=True)
            if owned_bootstrap_fd is not None:
                info = os.fstat(owned_bootstrap_fd)
                original = path.lstat()
                if (info.st_dev, info.st_ino) != (original.st_dev, original.st_ino):
                    raise host.InstallError("owned bootstrap descriptor differs from the installation lock")
                # The mutating caller keeps this original descriptor for its
                # whole operation. Do not unlock or open a conflicting lease.
                fcntl.flock(owned_bootstrap_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_nlink != 1:
                    raise host.InstallError("bootstrap lock changed")
                fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                # Inspecting is not ownership of an upgrade transaction.
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

        check("bootstrap_lock", lock)
        check("release_payload", lambda: self._payload(manifest, release))
        check("installed_python_sources", lambda: self._python_sources(manifest, release))

        modern = "server-release-identity-v1" in manifest.get("installation_features", [])
        unit = host.server_unit(release, release_identity=modern).replace("PUBLIC_ORIGIN", origin)

        def exact(relative, body):
            expected = body.encode()
            if self._file(self.root / relative, len(expected)) != expected:
                raise host.InstallError("managed configuration differs")

        check("server_unit", lambda: exact("etc/systemd/system/reasoning-server.service", unit))
        check("proxy_unit", lambda: exact("etc/systemd/system/reasoning-proxy.service", host.proxy_unit()))
        check("proxy_configuration", lambda: exact("etc/reasoning/Caddyfile", host.caddyfile(origin)))
        for short, account in (("server", "reasoning"), ("proxy", "caddy")):
            active = check("loaded_" + short + "_unit", lambda short=short, account=account: self._loaded("reasoning-" + short, account))
            if active is not None:
                checks[short + "_active"] = "pass" if active else "fail"

        def catalog():
            body = self._file(self.root / "etc/reasoning/compute-images.json", 65536)
            value = json.loads(body)
            # Actual capability/resource policy validation belongs to the
            # matching application. This checks protected bytes, not approval.
            if not isinstance(value, list):
                raise host.InstallError("compute catalog is not a list")
            configuration["compute_catalog_sha256"] = _digest(body)

        check("compute_catalog", catalog)

        def private_state():
            import pwd

            uid = pwd.getpwnam("reasoning").pw_uid
            self._directory(data_dir, uid=uid, private=True)
            # Never open SQLite: even mode=ro can initialize WAL shared memory.
            for name, maximum in (("server.sqlite3", 64 * 1024**3), ("encryption.key", 4096), ("setup-token", 4096)):
                path = data_dir / name
                info = path.lstat()
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != uid
                    or info.st_mode & 0o077
                    or info.st_nlink != 1
                    or not 0 < info.st_size <= maximum
                ):
                    raise host.InstallError("private installation state is missing or insecure")
            return uid

        uid = check("private_server_state", private_state)

        def mail():
            path = data_dir / "mail.json"
            managed = state_dir / "server-mail.json"
            if not path.exists() and not path.is_symlink() and not managed.exists() and not managed.is_symlink():
                checks["mail_configuration"] = "not_applicable"
                return
            if uid is None:
                raise host.InstallError("mail state ownership is unverified")
            # The service-owned data parent is intentional, unlike root-owned
            # configuration. Use the existing private-mail verifier.
            value = self._document(managed)
            if set(value) != {"installation", "mail"} or host.canonical(value["installation"]) != host.canonical(binding):
                raise host.InstallError("managed mail journal differs")
            host.HostInstaller(root=self.root)._mail_file(value["mail"], uid)
            configuration["mail_input_sha256"] = value["mail"]["input_sha256"]

        check("mail_configuration", mail)
        if not (data_dir / "mail.json").exists() and checks["mail_configuration"] == "pass":
            checks["mail_configuration"] = "not_applicable"

        def provisioning():
            path = state_dir / "server-provisioning.json"
            if not path.exists() and not path.is_symlink():
                # A cloud-init marker without its installation journal must not
                # be mistaken for an independently owned hardware deployment.
                marker = self.root / "etc/reasoning-provisioning.json"
                if marker.exists() or marker.is_symlink():
                    raise host.InstallError("cloud host installation binding is absent")
                return False
            value = self._document(path)
            if set(value) != {"format", "binding", "request"} or type(value["format"]) is not int or value["format"] != 1:
                raise host.InstallError("cloud installation journal is malformed")
            request = host.provisioning_request(value["request"])
            if host.canonical(value["binding"]) != host.canonical(binding):
                raise host.InstallError("cloud installation release binding differs")
            marker = self._document(self.root / "etc/reasoning-provisioning.json")
            if host.canonical(marker) != host.canonical({key: request[key] for key in ("plan_id", "deployment_id")}):
                raise host.InstallError("cloud host marker differs")
            if self.droplet_id() != request["droplet_id"]:
                raise host.InstallError("cloud VM identity differs")
            return True

        cloud = check("provisioning_identity", provisioning)
        if cloud is False:
            checks["provisioning_identity"] = "not_applicable"
        require_mail = configuration["mail_input_sha256"] is not None
        status = check("https_health", lambda: self.health(origin, timeout=0, require_mail=require_mail))
        if status is not None and (
            not isinstance(status, dict) or status.get("https_ready") is not True or status.get("public_url") != origin
        ):
            checks["https_health"] = "fail"
        if modern:
            value = check(
                "https_release_identity",
                lambda: self.health(origin, timeout=0, require_mail=require_mail, expected_release=release),
            )
            if value is not None and (
                not isinstance(value, dict) or value.get("https_ready") is not True or value.get("public_url") != origin
            ):
                checks["https_release_identity"] = "fail"
        return {
            "format": 1,
            "operation": "doctor",
            "installation": binding,
            "checks": checks,
            "configuration": configuration,
            "all_checks_passed": all(item in {"pass", "not_applicable"} for item in checks.values()),
            "expected_database_schema": manifest["database_schema"],
            "database_opened": False,
            "host_changed": False,
            "pending_upgrade_checks": list(PENDING),
        }


def validate_report(value, binding, schema):
    """Accept no extra remote fields, secret output, or stale release receipts."""
    fields = {
        "format",
        "operation",
        "installation",
        "checks",
        "configuration",
        "all_checks_passed",
        "expected_database_schema",
        "database_opened",
        "host_changed",
        "pending_upgrade_checks",
    }
    try:
        if (
            not isinstance(value, dict)
            or set(value) != fields
            or type(value["format"]) is not int
            or value["format"] != 1
            or value["operation"] != "doctor"
            or host.canonical(value["installation"]) != host.canonical(binding)
            or not isinstance(value["checks"], dict)
            or set(value["checks"]) != set(CHECKS)
            or any(item not in STATUSES for item in value["checks"].values())
            or any(
                item == "not_applicable" and name not in {"mail_configuration", "provisioning_identity"}
                for name, item in value["checks"].items()
            )
            or type(value["all_checks_passed"]) is not bool
            or value["all_checks_passed"] != all(item in {"pass", "not_applicable"} for item in value["checks"].values())
            or type(value["expected_database_schema"]) is not int
            or value["expected_database_schema"] != schema
            or value["database_opened"] is not False
            or value["host_changed"] is not False
            or value["pending_upgrade_checks"] != PENDING
            or not isinstance(value["configuration"], dict)
            or set(value["configuration"]) != {"compute_catalog_sha256", "mail_input_sha256"}
            or any(
                item is not None and (not isinstance(item, str) or not re.fullmatch(r"[a-f0-9]{64}", item))
                for item in value["configuration"].values()
            )
        ):
            raise ValueError
        return value
    except (KeyError, TypeError, ValueError) as exc:
        raise host.InstallError("server diagnosis returned an invalid or differently bound receipt") from exc


def upgrade_plan(report, current_manifest, target_manifest, target_release, target_checksum):
    binding = report["installation"]
    release_binding(current_manifest, binding["release_id"], binding["bundle_sha256"], binding["public_url"])
    validate_report(report, binding, current_manifest["database_schema"])
    target = release_binding(target_manifest, target_release, target_checksum, binding["public_url"])
    refusals = []
    if binding["dirty"] or target["dirty"]:
        refusals.append("uncommitted_release")
    if target_release == binding["release_id"]:
        refusals.append("target_is_current_release")
    if target_manifest["database_schema"] < current_manifest["database_schema"]:
        refusals.append("schema_downgrade_requires_explicit_snapshot_restore")
    if "server-release-identity-v1" not in target_manifest.get("installation_features", []):
        refusals.append("target_has_no_https_release_identity")
    # Old source releases legitimately lack the health header, but every other
    # failed/unverified check still blocks the preflight. None is an upgrade.
    legacy = "server-release-identity-v1" not in current_manifest.get("installation_features", [])
    if any(
        item not in {"pass", "not_applicable"}
        for name, item in report["checks"].items()
        if not (legacy and name == "https_release_identity")
    ):
        refusals.append("installed_host_checks_failed_or_unverified")
    identity = {
        "format": 1,
        "operation": "upgrade-plan",
        "source": binding,
        "target": target,
        "source_database_schema": current_manifest["database_schema"],
        "target_database_schema": target_manifest["database_schema"],
        "configuration": report["configuration"],
    }
    return {
        **identity,
        "plan_id": _digest(host.canonical(identity)),
        "refusals": refusals,
        "preflight_checks_passed": not refusals,
        "upgrade_performed": False,
        "host_changed": False,
        "pending_upgrade_checks": list(PENDING),
        "diagnosis": report,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--public-url", required=True)
    args = parser.parse_args(argv)
    try:
        if os.geteuid() != 0:
            raise host.InstallError("installed-server diagnosis requires root read access")
        manifest, release, checksum = host.inspect_bundle(args.bundle, args.expected_sha256)
        print(json.dumps(ServerInspector().inspect(manifest, release, checksum, args.public_url), sort_keys=True))
        return 0
    except (host.InstallError, OSError, ValueError) as exc:
        message = str(exc) if isinstance(exc, host.InstallError) else "server diagnosis input is unavailable or invalid"
        parser.exit(2, "reasoning-deploy: " + message + "\n")


if __name__ == "__main__":
    sys.exit(main())
