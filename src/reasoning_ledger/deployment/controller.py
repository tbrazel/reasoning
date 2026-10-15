"""Explicitly authorized queue-driven lifecycle for an original bounded DO fleet."""

from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path

from . import scaling
from .fleet import FleetCoordinator, digest, hash_id, scope_for
from .host import InstallError, canonical, inspect_bundle
from .private import locked_directory, read_document, read_private, write_document
from .ssh_host import pinned
from .worker_cancel import CANCELLATION, WorkerCancellation
from .worker_host import provisioning_request
from .worker_install import JOURNAL as INSTALLATION
from .worker_install import WorkerInstaller
from .worker_retire import JOURNAL as RETIREMENT
from .worker_retire import WorkerRetirer, owned_resource

JOURNAL = "fleet-controller.json"
PATHS = {"fleet_path", "fleet_state_dir", "bundle_path", "identity", "known_hosts"}
HASHES = {"fleet_sha256", "scope_sha256", "release_id", "bundle_sha256", "identity_sha256"}
ACTIONS = {"provision", "resume", "install", "retire", "cancel"}


def validate_policy(value):
    try:
        if (
            not isinstance(value, dict)
            or set(value) != PATHS | HASHES | {"format", "allow_provision", "allow_retirement", "delete_host_data"}
            or type(value["format"]) is not int
            or value["format"] != 1
            or any(not hash_id(value[key]) for key in HASHES)
            or any(
                not isinstance(value[key], str)
                or not Path(value[key]).is_absolute()
                or str(Path(value[key])) != value[key]
                or ".." in Path(value[key]).parts
                or value[key].startswith("//")
                for key in PATHS
                if key != "known_hosts" or value[key] is not None
            )
            or any(type(value[key]) is not bool for key in ("allow_provision", "allow_retirement", "delete_host_data"))
            or value["delete_host_data"] != value["allow_retirement"]
        ):
            raise ValueError
        return dict(value)
    except (KeyError, TypeError, ValueError) as exc:
        raise InstallError("invalid immutable bounded worker-controller policy") from exc


def check_ssh(identity, known_hosts, profiles):
    from .cli import ssh_arguments

    if known_hosts is not None:
        ssh_arguments("203.0.113.1", identity, known_hosts)
    else:
        read_private(identity, 65536)
        if not profiles or any(not pinned(profile.plan) for profile in profiles):
            raise InstallError("controller without --known-hosts requires every worker plan to pin its SSH host key")


def plan_controller(
    fleet_path, directory, bundle, identity, known_hosts=None, *, allow_provision=False, allow_retirement=False, delete_host_data=False
):
    """Local plan only; its hash must be explicitly authorized when running."""
    coordinator = FleetCoordinator(None, fleet_path, Path(directory).absolute())
    manifest, release, checksum = inspect_bundle(bundle)
    if "worker-installer.py" not in manifest["files"]:
        raise InstallError("controller release requires the public worker bootstrap")
    check_ssh(identity, known_hosts, coordinator.profiles)
    return validate_policy(
        {
            "format": 1,
            "fleet_path": str(Path(fleet_path).absolute()),
            "fleet_state_dir": str(Path(directory).absolute()),
            "bundle_path": str(Path(bundle).absolute()),
            "identity": str(Path(identity).absolute()),
            "known_hosts": str(Path(known_hosts).absolute()) if known_hosts is not None else None,
            "identity_sha256": hashlib.sha256(read_private(identity, 65536)).hexdigest(),
            "fleet_sha256": coordinator.fleet_hash,
            "scope_sha256": digest(coordinator.scope),
            "release_id": release,
            "bundle_sha256": checksum,
            "allow_provision": allow_provision,
            "allow_retirement": allow_retirement,
            "delete_host_data": delete_host_data,
        }
    )


class WorkerController(FleetCoordinator):
    def __init__(self, provider, policy, *, bundle=None, snapshot_reader=None, identity_client=None):
        self.policy = validate_policy(policy)
        self.policy_hash = digest(self.policy)
        super().__init__(
            provider,
            Path(policy["fleet_path"]),
            Path(policy["fleet_state_dir"]),
            snapshot_reader=snapshot_reader,
            identity_client=identity_client,
        )
        self.bundle = Path(bundle) if bundle is not None else Path(policy["bundle_path"])
        self.manifest, self.release, self.checksum = inspect_bundle(self.bundle)
        self._policy_binding()
        self.installers = {}

    def _policy_binding(self):
        check_ssh(
            Path(self.policy["identity"]),
            Path(self.policy["known_hosts"]) if self.policy["known_hosts"] is not None else None,
            self.profiles,
        )
        if (
            self.policy["fleet_sha256"] != self.fleet_hash
            or self.policy["scope_sha256"] != digest(scope_for(self.fleet, self.profiles))
            or self.policy["release_id"] != self.release
            or self.policy["bundle_sha256"] != self.checksum
            or "worker-installer.py" not in self.manifest["files"]
            or self.policy["identity_sha256"] != hashlib.sha256(read_private(Path(self.policy["identity"]), 65536)).hexdigest()
        ):
            raise InstallError("controller policy does not match the original fleet, release, or SSH identity")

    def _controller_state(self, replacement):
        path = self.directory / JOURNAL
        if path.exists() or path.is_symlink():
            value = read_document(path)
            try:
                old = validate_policy(value["policy"])
                action = value["last_action"]
                if (
                    set(value) != {"format", "policy", "policy_sha256", "cycles", "last_action"}
                    or type(value["format"]) is not int
                    or value["format"] != 1
                    or value["policy_sha256"] != digest(old)
                    or not scaling.integer(value["cycles"], 0, 2**63 - 2)
                    or action is not None
                    and (
                        not isinstance(action, dict)
                        or set(action) != {"action", "worker_id", "plan_id", "phase"}
                        or action["action"] not in ACTIONS
                        or not scaling.identifier(action["worker_id"])
                        or not hash_id(action["plan_id"])
                        or not isinstance(action["phase"], str)
                        or not re.fullmatch(r"[a-z_]{1,64}", action["phase"])
                    )
                ):
                    raise ValueError
            except (KeyError, TypeError, ValueError) as exc:
                raise InstallError("invalid original controller journal; restore it rather than resetting it") from exc
            if old != self.policy:
                if replacement != value["policy_sha256"] or any(
                    old[key] != self.policy[key] for key in ("fleet_state_dir", "release_id", "bundle_sha256")
                ):
                    raise InstallError("confirm replacement of the original controller policy; retain its budget and release")
                value.update(policy=self.policy, policy_sha256=self.policy_hash)
                write_document(path, value)
            elif replacement is not None:
                raise InstallError("replacement requires a distinct next policy and its exact current controller hash")
        else:
            if replacement is not None:
                raise InstallError("cannot replace a missing original controller journal")
            value = {"format": 1, "policy": self.policy, "policy_sha256": self.policy_hash, "cycles": 0, "last_action": None}
            write_document(path, value, new=True)
        self.controller_state = value

    def _snapshot(self):
        value = self.snapshot_reader(self.fleet["server_url"], self.fleet["organization_id"], self.session)
        scaling.validate_snapshot(value, self.fleet["organization_id"], int(time.time()))
        return value

    def _installer(self, profile):
        worker = profile.config["id"]
        if worker not in self.installers:
            self.installers[worker] = WorkerInstaller(
                self.provider, profile.document, profile.config, profile.images, self.bundle, identity_client=profile.identity_client
            )
        return self.installers[worker]

    def _installation(self, profile):
        path = Path(profile.fleet_paths["state_dir"]) / INSTALLATION
        if not path.exists() and not path.is_symlink():
            return None
        value = read_document(path)
        installer = self._installer(profile)
        request = provisioning_request(
            {
                "format": 1,
                "plan_id": profile.identity,
                "deployment_id": profile.document["deployment_id"],
                "droplet_id": profile.state["resources"]["droplet"]["id"],
            }
        )
        droplet = owned_resource(profile, "droplet")
        if droplet is None:
            raise InstallError("installed controller worker VM is missing; preserve its original intent")
        intent = {
            "format": 1,
            "binding": installer.binding,
            "request": request,
            "resources": {kind: profile.state["resources"][kind]["id"] for kind in profile.kinds},
            "ssh_host": profile._address(droplet),
        }
        installer._validate_installation(value, intent)
        return value

    def _checkpoint(self, action, profile, phase):
        self.controller_state["last_action"] = {
            "action": action,
            "worker_id": profile.config["id"],
            "plan_id": profile.identity,
            "phase": phase,
        }
        write_document(self.directory / JOURNAL, self.controller_state)

    def _retire(self, profile, retire_host):
        controller = WorkerRetirer(self.provider, profile.document, profile.config, profile.images, self.bundle)
        return controller.retire(
            Path(profile.fleet_paths["state_dir"]),
            confirm_plan=profile.identity,
            confirm_worker=profile.config["id"],
            confirm_release=self.release,
            confirm_droplet=profile.state["resources"]["droplet"]["id"],
            confirm_delete_host_data=True,
            prepare_host=lambda address, request: retire_host(profile, address, request),
        )

    def _cancel(self, profile):
        with locked_directory(Path(profile.fleet_paths["state_dir"])):
            return WorkerCancellation(self, profile, self.state["reservations"][profile.config["id"]]).run()

    def cycle(self, session, *, confirm_controller, install_host, retire_host, replace_controller=None):
        if confirm_controller != self.policy_hash or replace_controller is not None and not hash_id(replace_controller):
            raise InstallError("confirm the exact controller policy and any original policy replacement before actions")
        self._policy_binding()
        self.session = session
        with locked_directory(self.directory):
            self.locked = True
            try:
                self._open()
                owned, snapshot = self._observe(), self._snapshot()
                self._controller_state(replace_controller)
                decision = scaling.decide(self.fleet, snapshot, owned)
                action, result, reason = self._cycle(owned, snapshot, decision, install_host, retire_host)
                self._observe()  # Release only bound terminal absence, never service activation.
                self.controller_state["cycles"] += 1
                write_document(self.directory / JOURNAL, self.controller_state)
                count, cost = self._totals()
                return {
                    "policy_sha256": self.policy_hash,
                    "fleet_sha256": self.fleet_hash,
                    "mode": "controller",
                    "cycle": self.controller_state["cycles"],
                    "action": action,
                    "result": result,
                    "reason": reason,
                    "reserved_workers": count,
                    "reserved_hourly_usd": str(cost),
                    "maximum_workers": self.fleet["maximum_workers"],
                    "maximum_hourly_usd": self.fleet["maximum_hourly_usd"],
                    "ceiling_exceeded": count > self.fleet["maximum_workers"] or cost > scaling.dollars(self.fleet["maximum_hourly_usd"]),
                    "container_execution_verified": False,
                }
            finally:
                self.locked = False

    def _cycle(self, owned, snapshot, decision, install_host, retire_host):
        slots = {slot["worker_id"]: slot for slot in owned}
        workers = {worker["id"]: worker for worker in snapshot["workers"]}
        reserved = [profile for profile in self.profiles if self.state["reservations"][profile.config["id"]]["status"] == "reserved"]
        for profile in reserved:
            directory = Path(profile.fleet_paths["state_dir"])
            for name, action in ((RETIREMENT, "retire"), (CANCELLATION, "cancel")):
                path = directory / name
                if path.exists() or path.is_symlink():
                    if not self.policy["allow_retirement"]:
                        return None, None, "pending_cleanup_not_authorized"
                    self._checkpoint(action, profile, "pending")
                    result = self._retire(profile, retire_host) if action == "retire" else self._cancel(profile)
                    self._checkpoint(action, profile, result["phase"])
                    return action, result, "original_cleanup_resumed"
        for profile in reserved:
            worker = profile.config["id"]
            row = self.state["reservations"][worker]
            if slots[worker]["phase"] in {"provisioning", "uncertain"}:
                never_vm = profile.state["resources"]["droplet"]["status"] == "not_started"
                if never_vm and not scaling.resume_needed(self.fleet, snapshot, owned, worker):
                    if not self.policy["allow_retirement"]:
                        return None, None, "unneeded_setup_cleanup_not_authorized"
                    self._checkpoint("cancel", profile, "pending")
                    result = self._cancel(profile)
                    self._checkpoint("cancel", profile, result["phase"])
                    return "cancel", result, "unneeded_unpaid_setup_cancelled"
                if never_vm and not self.policy["allow_provision"]:
                    return None, None, "paid_creation_not_authorized"
                self._checkpoint("resume", profile, "pending")
                result = self._apply_locked(profile, resume_reservation=row["reservation_id"])
                self._checkpoint("resume", profile, result["phase"])
                return "resume", result, "original_reservation_resumed"
        for profile in reserved:
            worker = profile.config["id"]
            directory = Path(profile.fleet_paths["state_dir"])
            if slots[worker]["phase"] == "active":
                record = workers.get(worker)
                if record is None or record["revoked_at"] is not None or record["retired_at"] is not None or record["draining"]:
                    continue
                if any(canonical(record[key]) != canonical(profile.config[key]) for key in ("image_ids", "resources")) or any(
                    image not in snapshot["images"] for image in profile.images
                ):
                    continue
                installation = self._installation(profile)
                if installation is None or installation["phase"] != "installed":
                    self._checkpoint("install", profile, "pending")
                    result = self._installer(profile).install(
                        directory,
                        confirm_plan=profile.identity,
                        confirm_worker=worker,
                        confirm_release=self.release,
                        confirm_droplet=profile.state["resources"]["droplet"]["id"],
                        install_host=lambda address, request, target=profile: install_host(target, address, request),
                    )
                    self._checkpoint("install", profile, result["phase"])
                    return "install", result, "installed_waiting_for_worker_poll"
        if decision["reconcile_workers"]:
            return None, None, "worker_readiness_or_original_intent_held"
        for item in decision["actions"]:
            profile = next(profile for profile in self.profiles if profile.config["id"] == item["worker_id"])
            action = item["action"]
            if action == "provision":
                if not self.policy["allow_provision"]:
                    return None, None, "paid_creation_not_authorized"
                self._checkpoint(action, profile, "pending")
                result = self._apply_locked(profile, advice=decision, confirm_decision=decision["decision_id"])
            else:
                if not self.policy["allow_retirement"]:
                    return None, None, "idle_retirement_not_authorized"
                # Recheck immediately before committing the irreversible drain.
                fresh = scaling.decide(self.fleet, self._snapshot(), self._observe())
                if item not in fresh["actions"]:
                    return None, None, "retirement_no_longer_recommended"
                self._checkpoint(action, profile, "pending")
                result = self._retire(profile, retire_host)
            self._checkpoint(action, profile, result["phase"])
            return action, result, "fresh_queue_decision_applied"
        if decision["minimum_unmet"] or decision["uncovered_demand"]:
            available = any(row["status"] == "unreserved" for row in self.state["reservations"].values())
            return None, None, "capacity_or_budget_unmet" if available else "prepared_slots_exhausted"
        return None, None, "steady"
