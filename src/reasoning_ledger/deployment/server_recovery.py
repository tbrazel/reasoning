"""Root-owned preparation and separately confirmed replacement-host activation."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import pwd
import re
import sys
from pathlib import Path

try:
    from . import server_upgrade
except ImportError:
    specification = importlib.util.spec_from_file_location("reasoning_server_upgrade", Path(__file__).with_name("server-upgrade.py"))
    if specification is None or specification.loader is None:
        raise RuntimeError("verified release-change tooling is unavailable") from None
    server_upgrade = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = server_upgrade
    specification.loader.exec_module(server_upgrade)

host, operations = server_upgrade.host, server_upgrade.server_operations
FENCE = "var/lib/reasoning-deploy/server-recovery-fence"
DROPIN = "etc/systemd/system/reasoning-server.service.d/90-reasoning-recovery.conf"
FENCE_UNIT = host.MANAGED + "[Unit]\nConditionPathExists=!/var/lib/reasoning-deploy/server-recovery-fence\n"
PHASES = {"preparing", "installing", "installed", "restoring", "restored", "prepared"}
ACTIVATION_PHASES = {"publishing", "switching", "activating", "ready", "finalizing", "completed"}
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


def recovery_identity(manifest, release, checksum, origin, backup_sha256, key_sha256, *, maximum, mail=None, provisioning=None):
    binding = operations.release_binding(manifest, release, checksum, origin)
    if (
        binding["dirty"]
        or manifest["database_schema"] < 13
        or not {"server-maintenance-v1", "server-release-identity-v1", "server-recovery-preparation-v1"}
        <= set(manifest.get("installation_features", []))
        or not all(map(server_upgrade._hex, (backup_sha256, key_sha256)))
        or type(maximum) is not int
        or not 1024**2 <= maximum <= 64 * 1024**3
    ):
        raise host.InstallError("replacement recovery requires a clean supported matching release and exact backup/key identities")
    if mail is not None:
        host.require_mail_release(manifest)
    identity = {
        "format": 1,
        "operation": "recovery-plan",
        "installation": binding,
        "database_schema": manifest["database_schema"],
        "backup_sha256": backup_sha256,
        "backup_key_sha256": key_sha256,
        "maximum_bytes": maximum,
        "mail": host.mail_binding(mail) if mail is not None else None,
        "provisioning": host.provisioning_request(provisioning) if provisioning is not None else None,
    }
    return identity, hashlib.sha256(host.canonical(identity)).hexdigest()


def restoration_result(value, state):
    identity, recovery_id = state["identity"], state["recovery_id"]
    expected = {
        "format": 1,
        "operation": "recovery-prepare",
        "operation_id": recovery_id,
        "target_release": identity["installation"]["release_id"],
        "backup_sha256": identity["backup_sha256"],
        "backup_key_sha256": identity["backup_key_sha256"],
        "mail_sha256": identity["mail"]["input_sha256"] if identity["mail"] else None,
        "maximum_bytes": identity["maximum_bytes"],
        "prepared_state_verified": True,
        "server_started": False,
        "activation_performed": False,
        "sql_migration_performed": False,
    }
    try:
        current, recovery = value["maintenance"], value["recovery"]
        fields = {
            "format",
            "operation_id",
            "backup_sha256",
            "backup_key_sha256",
            "source_schema",
            "target_schema",
            "target_release",
            "maximum_bytes",
            "source_state_sha256",
            "target_state_sha256",
            "phase",
        }
        fixed = {
            "format": 1,
            "operation_id": recovery_id,
            "backup_sha256": identity["backup_sha256"],
            "backup_key_sha256": identity["backup_key_sha256"],
            "source_schema": identity["database_schema"],
            "target_schema": identity["database_schema"],
            "target_release": identity["installation"]["release_id"],
            "maximum_bytes": identity["maximum_bytes"],
            "phase": "migrated",
        }
        if (
            not isinstance(value, dict)
            or set(value) != set(expected) | {"maintenance", "recovery"}
            or any(type(value[key]) is not type(item) or value[key] != item for key, item in expected.items())
            or not isinstance(current, dict)
            or set(current) != fields
            or any(type(current[key]) is not type(item) or current[key] != item for key, item in fixed.items())
            or any(not server_upgrade._hex(current[key]) for key in ("source_state_sha256", "target_state_sha256"))
            or not isinstance(recovery, dict)
            or not COUNTS <= set(recovery) <= COUNTS | {"workflow_runs_held", "work_workers_revoked"}
            or any(type(count) is not int or not 0 <= count <= 2**63 - 1 for count in recovery.values())
        ):
            raise ValueError
        return value
    except (KeyError, TypeError, ValueError) as exc:
        raise host.InstallError("replacement restoration returned an invalid or differently bound receipt") from exc


class HostRecovery(server_upgrade.HostUpgrade):
    def __init__(self, **options):
        super().__init__(**options)
        self.active = self.directory / "active-server-recovery.json"

    @staticmethod
    def _pointer(state):
        return {"format": 1, "recovery_id": state["recovery_id"], "input_sha256": state["input_sha256"]}

    def _fresh(self):
        self.inspector._directory(self.root)
        for relative in (
            "var/lib/reasoning",
            "etc/reasoning",
            "opt/reasoning",
            "var/lib/reasoning-compute",
            "opt/reasoning-compute",
            "etc/systemd/system/reasoning-server.service",
            "etc/systemd/system/reasoning-proxy.service",
            "etc/systemd/system/reasoning-compute.service",
            "var/lib/reasoning-deploy/installation.json",
            "var/lib/reasoning-deploy/worker-installation.json",
            "var/lib/reasoning-deploy/active-server-upgrade.json",
            "etc/caddy/Caddyfile",
        ):
            path = self.root / relative
            if path.exists() or path.is_symlink():
                raise host.InstallError("replacement recovery requires fresh dedicated hardware; refusing existing state or configuration")
        try:
            pwd.getpwnam("reasoning")
        except KeyError:
            pass
        else:
            raise host.InstallError("replacement recovery cannot adopt an existing unmanaged service account")
        for name in ("caddy", "nginx", "apache2", "reasoning-server", "reasoning-proxy"):
            if self.command(["systemctl", "is-active", "--quiet", name], check=False).returncode == 0:
                raise host.InstallError("replacement recovery refuses an active existing web service")

    def _fenced(self, state, *, install=False):
        pointer = self._pointer(state)
        path, dropin = self.root / FENCE, self.root / DROPIN
        if install:
            if path.exists() or path.is_symlink():
                if host.canonical(self._read(path)) != host.canonical(pointer):
                    raise host.InstallError("replacement restart fence belongs to another operation")
            else:
                self._write(path, pointer)
            self.installer._directory(str(dropin.parent.relative_to(self.root)))
            self.installer._managed(dropin, FENCE_UNIT)
        if host.canonical(self._read(path)) != host.canonical(pointer) or self.inspector._file(dropin, 4096) != FENCE_UNIT.encode():
            raise host.InstallError("original replacement restart fence changed")

    def _service_state(self, *, active):
        fields = ("FragmentPath", "DropInPaths", "User", "Group", "LoadState", "NeedDaemonReload", "ActiveState", "MainPID", "SubState")
        response = self.command(["systemctl", "show", "--no-pager", "--property=" + ",".join(fields), "reasoning-server"])
        expected = {
            "FragmentPath": "/etc/systemd/system/reasoning-server.service",
            "DropInPaths": "/" + DROPIN if (self.root / DROPIN).exists() else "",
            "User": "reasoning",
            "Group": "reasoning",
            "LoadState": "loaded",
            "NeedDaemonReload": "no",
            "ActiveState": "active" if active else "inactive",
            "SubState": "running" if active else "dead",
        }
        try:
            if response.returncode != 0 or not isinstance(response.stdout, bytes) or len(response.stdout) > 4096:
                raise ValueError
            rows = [line.split("=", 1) for line in response.stdout.decode().splitlines()]
            value = server_upgrade._pairs(rows)
            if (
                set(value) != set(fields)
                or any(value[key] != item for key, item in expected.items())
                or (re.fullmatch(r"[1-9][0-9]{0,9}", value["MainPID"]) is None if active else value["MainPID"] != "0")
            ):
                raise ValueError
        except (AttributeError, TypeError, ValueError, UnicodeError) as exc:
            raise host.InstallError("replacement service differs from its exact loaded running/stopped state") from exc

    def _stopped_replacement(self, state):
        self._fenced(state)
        self._service_state(active=False)

    def _running_target(self):
        self._service_state(active=True)

    def _retained_recovery(self, state, operation):
        identity = state["identity"]
        for name, checksum, maximum in (
            ("release.tar.gz", identity["installation"]["bundle_sha256"], host.MAX_BUNDLE),
            ("source.rnbk", identity["backup_sha256"], server_upgrade.MAX_BACKUP),
            ("backup.key", identity["backup_key_sha256"], 32),
        ):
            size, actual = self._hash(operation / name, maximum)
            if actual != checksum or name == "backup.key" and size != 32:
                raise host.InstallError("original replacement release, encrypted backup or key changed")
        if identity["mail"] is not None and self._hash(operation / "mail.json", host.MAX_MAIL_INPUT)[1] != identity["mail"]["input_sha256"]:
            raise host.InstallError("original replacement mail input changed")

    def _configuration_recovery(self, state, manifest, *, activation=False):
        identity, binding = state["identity"], state["identity"]["installation"]
        phase = state["phase"]
        allowed = ["packages"]
        if activation and phase in {"ready", "finalizing", "completed"}:
            allowed = ["packages", "services_started"] if phase == "ready" else ["services_started"]
        if host.canonical(self._read(self.installer.state_path)) not in [host.canonical({**binding, "phase": item}) for item in allowed]:
            raise host.InstallError("replacement installation binding changed")
        self.inspector._payload(manifest, binding["release_id"])
        self.inspector._python_sources(manifest, binding["release_id"])
        for relative, body in (
            ("etc/reasoning/Caddyfile", host.caddyfile(binding["public_url"])),
            ("etc/systemd/system/reasoning-proxy.service", host.proxy_unit()),
        ):
            if self.inspector._file(self.root / relative, host.MAX_MANIFEST) != body.encode():
                raise host.InstallError("replacement managed unit/proxy bytes changed")
        ordinary = host.server_unit(binding["release_id"], release_identity=True).replace("PUBLIC_ORIGIN", binding["public_url"])
        activation_unit = host.server_unit(
            binding["release_id"], release_identity=True, maintenance_operation=state["recovery_id"]
        ).replace("PUBLIC_ORIGIN", binding["public_url"])
        units = [ordinary]
        if activation:
            if phase in {"switching", "finalizing"}:
                units = [ordinary, activation_unit]
            elif phase in {"activating", "ready"}:
                units = [activation_unit]
        if self.inspector._file(self.root / "etc/systemd/system/reasoning-server.service", 32768) not in [item.encode() for item in units]:
            raise host.InstallError("replacement server unit differs from its exact managed phase")
        catalog = self.inspector._file(self.root / "etc/reasoning/compute-images.json", 65536)
        if hashlib.sha256(catalog).hexdigest() != state["catalog_sha256"]:
            raise host.InstallError("replacement approved-image catalog changed")
        for name, expected in (
            ("server-mail.json", {"installation": binding, "mail": identity["mail"]} if identity["mail"] is not None else None),
            (
                "server-provisioning.json",
                {"format": 1, "binding": binding, "request": identity["provisioning"]} if identity["provisioning"] is not None else None,
            ),
        ):
            path = self.directory / name
            if expected is None:
                if path.exists() or path.is_symlink():
                    raise host.InstallError("replacement cannot adopt newly added mail/VM metadata")
            elif host.canonical(self._read(path)) != host.canonical(expected):
                raise host.InstallError("replacement mail/VM metadata changed")
        if identity["provisioning"] is not None:
            self.installer._verify_provisioning(identity["provisioning"])
        elif (self.root / "etc/reasoning-provisioning.json").exists() or (self.root / "etc/reasoning-provisioning.json").is_symlink():
            raise host.InstallError("replacement cannot adopt newly added VM ownership")
        if not activation:
            self._stopped_replacement(state)
        else:
            fence, dropin = self.root / FENCE, self.root / DROPIN
            if fence.exists() or fence.is_symlink():
                if phase not in {"prepared", "publishing", "switching", "activating"} or host.canonical(
                    self._read(fence)
                ) != host.canonical(self._pointer(state)):
                    raise host.InstallError("replacement activation restart fence changed")
            elif phase in {"prepared", "publishing", "switching"}:
                raise host.InstallError("original replacement restart fence is missing")
            if dropin.exists() or dropin.is_symlink():
                if phase == "completed" or self.inspector._file(dropin, 4096) != FENCE_UNIT.encode():
                    raise host.InstallError("replacement activation restart condition changed")
            elif phase not in {"finalizing", "completed"}:
                raise host.InstallError("original replacement restart condition is missing")

    def _candidate(self, state):
        return self.root / "var/lib" / ("reasoning-recovery-" + state["recovery_id"])

    def _verify_restoration(self, state, uid):
        parent = self._candidate(state)
        self.inspector._directory(parent, private=True)
        # Only the restored state's matching verifier needs temporary traversal;
        # backup/key inputs remain in the separately sealed runtime directory.
        os.chmod(parent, 0o711)
        try:
            data = parent / "state"
            self._verify_data(state, data, uid)
        finally:
            os.chmod(parent, 0o700)

    def _provenance(self, state, data, uid):
        self.inspector._directory(data, uid=uid, private=True)
        with self._input(data / "server-recovery.json", 8192, uid=uid, parent_uid=uid) as (stream, _):
            value = json.loads(stream.read(8193), object_pairs_hook=server_upgrade._pairs)
        if host.canonical(restoration_result(value, state)) != host.canonical(state["restoration"]):
            raise host.InstallError("replacement application restoration provenance changed")

    def _verify_data(self, state, data, uid):
        self._provenance(state, data, uid)
        release = state["identity"]["installation"]["release_id"]
        result = self._json(
            self._helper(release, data_dir=data)
            + ["maintenance-verify", "--operation-id", state["recovery_id"], "--target-release", release]
        )
        expected = {**state["restoration"]["maintenance"], "prepared_state_verified": True, "server_started": False}
        if host.canonical(result) != host.canonical(expected):
            raise host.InstallError("replacement prepared state differs from original restoration")

    @staticmethod
    def _application(state):
        """Use the existing strict maintenance observation/completion protocol."""
        identity = state["identity"]
        return {
            "operation_id": state["recovery_id"],
            "migration": state["restoration"]["maintenance"],
            "identity": {
                "target": identity["installation"],
                "configuration": {"mail_input_sha256": identity["mail"]["input_sha256"] if identity["mail"] else None},
            },
        }

    @staticmethod
    def _receipt(state, operation):
        return {
            "format": 1,
            "operation": "recovery-activate",
            "recovery_id": state["recovery_id"],
            "input_sha256": state["input_sha256"],
            "installation": state["identity"]["installation"],
            "phase": "completed",
            "server_restart_fenced": False,
            "root_private_inputs_retained": True,
            "replacement_data_published": True,
            "activation_performed": True,
            "recovery_completed": True,
            "https_release_verified": True,
            "database_schema_verified": True,
            "physical_source_fencing_verified": False,
            "runtime_dependency_attestation": False,
            "restoration": state["restoration"],
            "recovery_directory": str(operation),
        }

    def _activation_recovery_inputs(self, recovery_id, confirmation, target_release):
        operation = self.directory / "server-recoveries" / recovery_id
        state = self._read(operation / "operation.json")
        if (
            set(state) != {"format", "recovery_id", "input_sha256", "identity", "phase", "restoration", "catalog_sha256"}
            or type(state["format"]) is not int
            or state["format"] != 1
            or state["recovery_id"] != recovery_id
            or state["input_sha256"] != confirmation
            or state["phase"] not in {"prepared", *ACTIVATION_PHASES}
            or not server_upgrade._hex(state["catalog_sha256"])
        ):
            raise host.InstallError("replacement activation requires its exact prepared original recovery journal")
        self._retained_recovery(state, operation)
        identity = state["identity"]
        manifest, release, checksum = host.inspect_bundle(operation / "release.tar.gz", identity["installation"]["bundle_sha256"])
        mail = self.inspector._file(operation / "mail.json", host.MAX_MAIL_INPUT, private=True) if identity["mail"] else None
        expected, digest = recovery_identity(
            manifest,
            release,
            checksum,
            identity["installation"]["public_url"],
            identity["backup_sha256"],
            identity["backup_key_sha256"],
            maximum=identity["maximum_bytes"],
            mail=mail,
            provisioning=identity["provisioning"],
        )
        if (
            digest != confirmation
            or release != target_release
            or host.canonical(identity) != host.canonical(expected)
            or "server-recovery-activation-v1" not in manifest.get("installation_features", [])
        ):
            raise host.InstallError("replacement activation inputs differ or the release lacks activation support")
        restoration_result(state["restoration"], state)
        if self.active.exists() or self.active.is_symlink():
            if host.canonical(self._read(self.active)) != host.canonical(self._pointer(state)):
                raise host.InstallError("another recovery owns this replacement")
        elif state["phase"] != "completed":
            raise host.InstallError("original replacement ownership is missing")
        pending = self.directory / "active-server-upgrade.json"
        if pending.exists() or pending.is_symlink():
            raise host.InstallError("another upgrade owns this replacement")
        receipt, completion = self._receipt(state, operation), operation / "completion.json"
        if completion.exists() or completion.is_symlink():
            if state["phase"] not in {"finalizing", "completed"} or host.canonical(self._read(completion)) != host.canonical(receipt):
                raise host.InstallError("original replacement completion history changed")
        elif state["phase"] == "completed":
            raise host.InstallError("original replacement completion history is missing")
        return state, operation, manifest

    def activate(self, *, recovery_id, confirmed_recovery, target_release, activation_confirmed=False, skip_platform=False):
        if activation_confirmed is not True or not all(map(server_upgrade._hex, (recovery_id, confirmed_recovery, target_release))):
            raise host.InstallError("replacement activation requires exact original identities and explicit external-effect confirmation")
        self._platform(skip_platform)
        with self._bootstrap() as fd:
            state, operation, manifest = self._activation_recovery_inputs(recovery_id, confirmed_recovery, target_release)
            self._configuration_recovery(state, manifest, activation=True)
            uid = pwd.getpwnam("reasoning").pw_uid
            if not skip_platform and uid == os.getuid():
                raise host.InstallError("the application service account must not own root recovery evidence")
            candidate, data = self._candidate(state), self.root / "var/lib/reasoning"
            self.inspector._directory(candidate, private=True)
            self.inspector._directory(self.root / "run" / ("reasoning-recovery-" + recovery_id), private=True)
            path = operation / "operation.json"
            binding = state["identity"]["installation"]
            unit_path = self.root / "etc/systemd/system/reasoning-server.service"
            ordinary = host.server_unit(target_release, release_identity=True).replace("PUBLIC_ORIGIN", binding["public_url"]).encode()
            activation_unit = (
                host.server_unit(target_release, release_identity=True, maintenance_operation=recovery_id)
                .replace("PUBLIC_ORIGIN", binding["public_url"])
                .encode()
            )
            application = self._application(state)

            def advance(phase):
                state["phase"] = phase
                self._write(path, state)

            if state["phase"] == "prepared":
                self._stopped_replacement(state)
                self._verify_restoration(state, uid)
                if data.exists() or data.is_symlink():
                    raise host.InstallError("replacement publication refuses existing canonical data")
                advance("publishing")
            if state["phase"] == "publishing":
                self._stopped_replacement(state)
                source_exists = (candidate / "state").exists() or (candidate / "state").is_symlink()
                target_exists = data.exists() or data.is_symlink()
                if source_exists == target_exists:
                    raise host.InstallError("replacement publication has ambiguous or missing original state; refusing overwrite")
                if source_exists:
                    self._verify_restoration(state, uid)
                    with self._state_leases(candidate / "state", uid):
                        self._rename(candidate / "state", data)
                self._verify_data(state, data, uid)
                advance("switching")
            if (candidate / "state").exists() or (candidate / "state").is_symlink():
                raise host.InstallError("published replacement still has ambiguous staging state")
            self._provenance(state, data, uid)
            if state["phase"] == "switching":
                self.command(["systemctl", "daemon-reload"])
                self._stopped_replacement(state)
                self._verify_data(state, data, uid)
                self._transition(unit_path, ordinary, activation_unit, private=False)
                self.command(["systemctl", "daemon-reload"])
                self._stopped_replacement(state)
                # The server and proxy may start/restart after this durable
                # boundary. Never restore the snapshot over their live writes.
                advance("activating")
            if state["phase"] == "activating":
                self._remove(self.root / FENCE, host.canonical(self._pointer(state)))
                self.command(["systemctl", "enable", "reasoning-server", "reasoning-proxy"])
                self.command(["systemctl", "start", "reasoning-server", "reasoning-proxy"])
                self._readiness(application)
                advance("ready")
            if state["phase"] == "ready":
                self._readiness(application)
                self._finish_application(application)
                self._transition(
                    self.installer.state_path,
                    host.canonical({**binding, "phase": "packages"}),
                    host.canonical({**binding, "phase": "services_started"}),
                )
                advance("finalizing")
            if state["phase"] == "finalizing":
                self._observe(application, completed=True)
                self._transition(unit_path, activation_unit, ordinary, private=False)
                self._remove(self.root / DROPIN, FENCE_UNIT.encode(), private=False)
                self.command(["systemctl", "daemon-reload"])
            self._configuration_recovery(state, manifest, activation=True)
            self._retained_recovery(state, operation)
            self._readiness(application, completed=True)
            report = self.inspector.inspect(
                manifest, target_release, binding["bundle_sha256"], binding["public_url"], owned_bootstrap_fd=fd
            )
            expected_configuration = {
                "compute_catalog_sha256": state["catalog_sha256"],
                "mail_input_sha256": state["identity"]["mail"]["input_sha256"] if state["identity"]["mail"] else None,
            }
            if report["all_checks_passed"] is not True or host.canonical(report["configuration"]) != host.canonical(expected_configuration):
                raise host.InstallError("final replacement installation diagnosis failed; preserve original recovery and live state")
            receipt, completion = self._receipt(state, operation), operation / "completion.json"
            if completion.exists() or completion.is_symlink():
                if host.canonical(self._read(completion)) != host.canonical(receipt):
                    raise host.InstallError("original replacement completion history changed")
            elif state["phase"] == "completed":
                raise host.InstallError("original replacement completion history is missing")
            else:
                self._write(completion, receipt)
            if state["phase"] != "completed":
                advance("completed")
            self._remove(self.active, host.canonical(self._pointer(state)))
            return receipt

    def prepare(
        self,
        bundle,
        checksum,
        origin,
        archive,
        backup_checksum,
        key_file,
        key_checksum,
        *,
        recovery_id,
        confirmed_recovery,
        execution_fenced=False,
        maximum=8 * 1024**3,
        mail=None,
        provisioning=None,
        skip_platform=False,
    ):
        if execution_fenced is not True or not server_upgrade._hex(recovery_id) or not server_upgrade._hex(confirmed_recovery):
            raise host.InstallError("replacement preparation requires exact identities and physical source/execution fencing confirmation")
        self._platform(skip_platform)
        manifest, release, _ = host.inspect_bundle(bundle, checksum)
        identity, input_checksum = recovery_identity(
            manifest, release, checksum, origin, backup_checksum, key_checksum, maximum=maximum, mail=mail, provisioning=provisioning
        )
        marker = self.root / "etc/reasoning-provisioning.json"
        if identity["provisioning"] is None:
            if marker.exists() or marker.is_symlink():
                raise host.InstallError("owned replacement hardware requires its original exact provisioning request")
        else:
            self.installer._verify_provisioning(identity["provisioning"])
        if (
            input_checksum != confirmed_recovery
            or self._hash(key_file, 32) != (32, key_checksum)
            or self._hash(archive)[1] != backup_checksum
        ):
            raise host.InstallError("replacement preparation inputs differ from the exact confirmed recovery")
        operation = self.directory / "server-recoveries" / recovery_id
        path = operation / "operation.json"
        if not path.exists() and not path.is_symlink():
            self._fresh()
            for directory in (
                self.root / "var/lib" / ("reasoning-recovery-" + recovery_id),
                self.root / "run" / ("reasoning-recovery-" + recovery_id),
            ):
                if directory.exists() or directory.is_symlink():
                    raise host.InstallError("replacement cannot adopt an existing unowned staging directory")
        self.installer._directory("var/lib/reasoning-deploy", 0o700)
        lock = self.directory / "bootstrap.lock"
        if not lock.exists() and not lock.is_symlink():
            host.atomic_write(lock, b"")
        with self._bootstrap():
            state = self._read(path) if path.exists() or path.is_symlink() else None
            if state is None:
                self._fresh()
                if self.active.exists() or self.active.is_symlink():
                    raise host.InstallError("another or missing original recovery owns this replacement")
                self.installer._directory(str(operation.relative_to(self.root)), 0o700)
                state = {
                    "format": 1,
                    "recovery_id": recovery_id,
                    "input_sha256": input_checksum,
                    "identity": identity,
                    "phase": "preparing",
                    "restoration": None,
                    "catalog_sha256": None,
                }
                self._write(path, state)
            if (
                set(state) != {"format", "recovery_id", "input_sha256", "identity", "phase", "restoration", "catalog_sha256"}
                or type(state["format"]) is not int
                or state["format"] != 1
                or state["recovery_id"] != recovery_id
                or state["input_sha256"] != input_checksum
                or host.canonical(state["identity"]) != host.canonical(identity)
                or state["phase"] not in PHASES
                or (
                    state["catalog_sha256"] is not None
                    if state["phase"] in {"preparing", "installing"}
                    else not server_upgrade._hex(state["catalog_sha256"])
                )
                or (state["restoration"] is None) != (state["phase"] not in {"restored", "prepared"})
            ):
                raise host.InstallError("replacement journal differs from original recovery inputs or activation owns it")
            if state["restoration"] is not None:
                restoration_result(state["restoration"], state)
            pointer = self._pointer(state)
            if self.active.exists() or self.active.is_symlink():
                if host.canonical(self._read(self.active)) != host.canonical(pointer):
                    raise host.InstallError("another recovery owns this replacement")
            elif state["phase"] != "preparing":
                raise host.InstallError("original replacement ownership is missing")
            if (self.directory / "active-server-upgrade.json").exists() or (self.directory / "active-server-upgrade.json").is_symlink():
                raise host.InstallError("an upgrade owns this replacement")
            self._write(self.active, pointer)
            if state["phase"] == "preparing":
                for supplied, name, digest in (
                    (bundle, "release.tar.gz", checksum),
                    (archive, "source.rnbk", backup_checksum),
                    (key_file, "backup.key", key_checksum),
                ):
                    self._copy(supplied, operation / name, digest)
                if mail is not None:
                    saved = operation / "mail.json"
                    if saved.exists() or saved.is_symlink():
                        if self._hash(saved, host.MAX_MAIL_INPUT)[1] != identity["mail"]["input_sha256"]:
                            raise host.InstallError("retained replacement mail changed")
                    else:
                        host.atomic_write(saved, mail)
            self._retained_recovery(state, operation)

            def advance(phase):
                state["phase"] = phase
                self._write(path, state)

            if state["phase"] == "preparing":
                self._fenced(state, install=True)
                advance("installing")
            self._fenced(state)
            if state["phase"] == "installing":
                self.installer._install(
                    operation / "release.tar.gz",
                    checksum,
                    origin,
                    skip_platform=skip_platform,
                    provisioning=identity["provisioning"],
                    mail=mail,
                    recovery=pointer,
                )
                state["catalog_sha256"] = hashlib.sha256(
                    self.inspector._file(self.root / "etc/reasoning/compute-images.json", 65536)
                ).hexdigest()
                advance("installed")
            self._configuration_recovery(state, manifest)
            uid = pwd.getpwnam("reasoning").pw_uid
            if not skip_platform and uid == os.getuid():
                raise host.InstallError("the service account must not own root recovery evidence")
            candidate = self._candidate(state)
            runtime = self.root / "run" / ("reasoning-recovery-" + recovery_id)
            if state["phase"] == "installed":
                advance("restoring")
            if state["phase"] == "restoring":
                for directory in (candidate, runtime):
                    if not directory.exists() and not directory.is_symlink():
                        self.installer._directory(str(directory.relative_to(self.root)), 0o700)
                        os.chown(directory, uid, -1)
                    elif uid != os.getuid() and directory.lstat().st_uid == os.getuid():
                        self.inspector._directory(directory, private=True)
                        if any(directory.iterdir()):
                            raise host.InstallError("unfinished staging creation cannot adopt existing contents")
                        os.chown(directory, uid, -1)
                self.inspector._directory(candidate, uid=uid, private=True)
                self.inspector._directory(runtime, uid=uid, private=True)
                for name, digest in (
                    ("source.rnbk", backup_checksum),
                    ("backup.key", key_checksum),
                    *([("mail.json", identity["mail"]["input_sha256"])] if mail is not None else []),
                ):
                    self._copy(operation / name, runtime / name, digest, target_uid=uid)
                args = self._helper(release, data_dir=candidate / "state") + [
                    "recovery-prepare",
                    "--backup",
                    str(runtime / "source.rnbk"),
                    "--key-file",
                    str(runtime / "backup.key"),
                    "--expected-sha256",
                    backup_checksum,
                    "--operation-id",
                    recovery_id,
                    "--target-release",
                    release,
                    "--maximum-bytes",
                    str(maximum),
                    "--confirm-source-stopped",
                ]
                if mail is not None:
                    args += ["--mail-file", str(runtime / "mail.json"), "--expected-mail-sha256", identity["mail"]["input_sha256"]]
                state["restoration"] = restoration_result(self._json(args), state)
                advance("restored")
            if state["phase"] == "restored":
                for directory in (candidate, runtime):
                    owner = directory.lstat().st_uid
                    if owner not in {uid, os.getuid()}:
                        raise host.InstallError("replacement staging ownership changed")
                    self.inspector._directory(directory, uid=owner, private=True)
                    os.chown(directory, os.getuid(), -1)
                    os.chmod(directory, 0o700)
                advance("prepared")
            self.inspector._directory(runtime, private=True)
            restoration_result(state["restoration"], state)
            self._verify_restoration(state, uid)
            self._retained_recovery(state, operation)
            self._configuration_recovery(state, manifest)
            if (self.root / "var/lib/reasoning").exists() or (self.root / "var/lib/reasoning").is_symlink():
                raise host.InstallError("replacement preparation cannot adopt canonical application state")
            return {
                "format": 1,
                "operation": "recovery-prepare",
                "recovery_id": recovery_id,
                "input_sha256": input_checksum,
                "installation": identity["installation"],
                "phase": "prepared",
                "server_restart_fenced": True,
                "root_private_inputs_retained": True,
                "replacement_data_published": False,
                "activation_performed": False,
                "recovery_completed": False,
                "physical_source_fencing_verified": False,
                "runtime_dependency_attestation": False,
                "restoration": state["restoration"],
                "recovery_directory": str(operation),
            }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--activate", action="store_true")
    modes.add_argument("--plan", action="store_true", help="report recovery input identity without accessing or changing a host")
    for name in ("bundle", "backup", "key-file"):
        parser.add_argument("--" + name, type=Path)
    for name in ("expected-sha256", "backup-sha256", "key-sha256", "public-url", "target-release"):
        parser.add_argument("--" + name)
    for name in ("recovery-id", "confirm-recovery"):
        parser.add_argument("--" + name)
    parser.add_argument("--confirm-execution-fenced", action="store_true")
    parser.add_argument("--confirm-activation", action="store_true")
    parser.add_argument("--maximum-bytes", type=int)
    parser.add_argument("--mail-input", type=Path)
    parser.add_argument("--expected-mail-sha256")
    parser.add_argument("--provisioning", type=Path)
    parser.add_argument("--expected-provisioning-sha256")
    args = parser.parse_args(argv)
    try:
        preparation = (
            args.bundle,
            args.backup,
            args.key_file,
            args.expected_sha256,
            args.backup_sha256,
            args.key_sha256,
            args.public_url,
        )
        if not args.plan and (args.recovery_id is None or args.confirm_recovery is None):
            raise host.InstallError("replacement mutations require original recovery ID and exact input confirmation")
        if args.activate:
            if (
                not args.confirm_activation
                or args.target_release is None
                or any(item is not None for item in preparation)
                or args.confirm_execution_fenced
                or args.maximum_bytes is not None
                or any(
                    item is not None
                    for item in (args.mail_input, args.expected_mail_sha256, args.provisioning, args.expected_provisioning_sha256)
                )
            ):
                raise host.InstallError("replacement activation accepts only exact original identities and activation confirmation")
            result = HostRecovery().activate(
                recovery_id=args.recovery_id,
                confirmed_recovery=args.confirm_recovery,
                target_release=args.target_release,
                activation_confirmed=args.confirm_activation,
            )
            print(json.dumps(result, sort_keys=True))
            return 0
        if args.plan:
            if (
                any(item is None for item in preparation[3:])
                or args.backup is not None
                or args.key_file is not None
                or args.bundle is None
                or args.confirm_execution_fenced
                or args.confirm_activation
                or args.target_release is not None
                or args.recovery_id is not None
                or args.confirm_recovery is not None
            ):
                raise host.InstallError(
                    "read-only replacement planning requires a release, URL and backup/key digests, not mutation options"
                )
        elif (
            any(item is None for item in preparation)
            or not args.confirm_execution_fenced
            or args.confirm_activation
            or args.target_release is not None
        ):
            raise host.InstallError(
                "replacement preparation requires exact backup/release inputs and execution fencing, not activation options"
            )
        mail = provisioning = None
        if args.mail_input is not None or args.expected_mail_sha256 is not None:
            if args.mail_input is None or args.expected_mail_sha256 is None:
                raise host.InstallError("replacement mail requires its exact private input and digest")
            _, mail = host.private_document(args.mail_input, host.MAX_MAIL_INPUT)
            if host.mail_binding(mail)["input_sha256"] != args.expected_mail_sha256:
                raise host.InstallError("replacement mail input differs from its confirmed digest")
        if args.provisioning is not None or args.expected_provisioning_sha256 is not None:
            if args.provisioning is None or args.expected_provisioning_sha256 is None:
                raise host.InstallError("replacement provisioning requires its exact private request and digest")
            provisioning, body = host.private_document(args.provisioning)
            if hashlib.sha256(body).hexdigest() != args.expected_provisioning_sha256:
                raise host.InstallError("replacement provisioning input differs from its confirmed digest")
            provisioning = host.provisioning_request(provisioning)
        if args.plan:
            manifest, release, checksum = host.inspect_bundle(args.bundle, args.expected_sha256)
            identity, digest = recovery_identity(
                manifest,
                release,
                checksum,
                args.public_url,
                args.backup_sha256,
                args.key_sha256,
                maximum=args.maximum_bytes if args.maximum_bytes is not None else 8 * 1024**3,
                mail=mail,
                provisioning=provisioning,
            )
            print(json.dumps({**identity, "input_sha256": digest, "host_changed": False, "archive_authenticated": False}, sort_keys=True))
            return 0
        print(
            json.dumps(
                HostRecovery().prepare(
                    args.bundle,
                    args.expected_sha256,
                    args.public_url,
                    args.backup,
                    args.backup_sha256,
                    args.key_file,
                    args.key_sha256,
                    recovery_id=args.recovery_id,
                    confirmed_recovery=args.confirm_recovery,
                    execution_fenced=args.confirm_execution_fenced,
                    maximum=args.maximum_bytes if args.maximum_bytes is not None else 8 * 1024**3,
                    mail=mail,
                    provisioning=provisioning,
                ),
                sort_keys=True,
            )
        )
        return 0
    except (host.InstallError, OSError, ValueError) as exc:
        message = (
            str(exc) if isinstance(exc, host.InstallError) else "replacement recovery failed; preserve original inputs and host evidence"
        )
        parser.exit(2, "reasoning-deploy: " + message + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
