"""Confirmed fresh worker generations within an original owned fleet budget."""

from __future__ import annotations

import copy
import os
import stat
from pathlib import Path

from . import controller, enrollment, scaling
from .fleet import BUDGET, digest, hash_id, scope_for
from .fleet_amend import amend
from .host import InstallError
from .private import locked_directory, read_document, read_private, write_document
from .provision import save_plan
from .ssh_host import pinned
from .worker_host import decode_input
from .worker_provision import WorkerProvisioner, plan_worker, validate_worker_plan, worker_inputs

JOURNAL = "fleet-pool.json"
BINDING = "fleet-pool-binding.json"


def validate_policy(value):
    try:
        if (
            not isinstance(value, dict)
            or set(value) != {"format", "initial_policy", "directory", "template_workers", "scaler_id", "maximum_new_workers"}
            or type(value["format"]) is not int
            or value["format"] != 1
            or not isinstance(value["directory"], str)
            or not Path(value["directory"]).is_absolute()
            or str(Path(value["directory"])) != value["directory"]
            or ".." in Path(value["directory"]).parts
            or value["directory"].startswith("//")
            or not scaling.identifier(value["scaler_id"])
            or not scaling.integer(value["maximum_new_workers"], 1, 64)
            or not isinstance(value["template_workers"], list)
            or not 1 <= len(value["template_workers"]) <= 64
            or any(not scaling.identifier(worker) for worker in value["template_workers"])
            or sorted(set(value["template_workers"])) != value["template_workers"]
        ):
            raise ValueError
        initial = controller.validate_policy(value["initial_policy"])
        if not initial["allow_provision"]:
            raise ValueError
        return copy.deepcopy(value)
    except (KeyError, TypeError, ValueError) as exc:
        raise InstallError("invalid bounded worker-pool policy; paid growth and original template authority are required") from exc


def plan_pool(initial_policy, directory, template_workers, scaler_id, maximum_new_workers, *, pin_ssh_host_key=False):
    if pin_ssh_host_key is not True:
        raise InstallError("fresh worker pools require explicit --pin-ssh-host-key consent to provider user-data host identities")
    value = validate_policy(
        {
            "format": 1,
            "initial_policy": initial_policy,
            "directory": str(Path(directory).absolute()),
            "template_workers": sorted(template_workers),
            "scaler_id": scaler_id,
            "maximum_new_workers": maximum_new_workers,
        }
    )
    WorkerPool(None, value)
    return value


class WorkerPool:
    def __init__(self, provider, policy, *, bundle=None, snapshot_reader=None, identity_client=None):
        self.policy = validate_policy(policy)
        self.policy_hash = digest(self.policy)
        self.directory = Path(self.policy["directory"])
        self.provider = provider
        self.bundle = bundle
        self.snapshot_reader = snapshot_reader
        self.identity_client = identity_client
        self.initial = self._controller(self.policy["initial_policy"])
        self.templates = {
            profile.config["id"]: profile for profile in self.initial.profiles if profile.config["id"] in self.policy["template_workers"]
        }
        if set(self.templates) != set(self.policy["template_workers"]):
            raise InstallError("every pool template must be an original confirmed fleet worker")
        for path in [self.initial.directory, *[Path(profile.fleet_paths["state_dir"]) for profile in self.initial.profiles]]:
            if path == self.directory or path in self.directory.parents or self.directory in path.parents:
                raise InstallError("pool preparation, original fleet budget, and original worker ownership must not overlap")
        from .private import protected_parent

        protected_parent(self.directory)
        self.state = None

    def _controller(self, policy):
        return controller.WorkerController(
            self.provider, policy, bundle=self.bundle, snapshot_reader=self.snapshot_reader, identity_client=self.identity_client
        )

    def _paths(self, index):
        directory = self.directory / f"worker-{index:04d}"
        return {
            "plan": str(directory / "plan.json"),
            "registration": str(directory / "registration.json"),
            "catalog": str(directory / "catalog.json"),
            "state_dir": str(directory / "ownership"),
        }

    def _fleet_path(self, index):
        return self.directory / f"fleet-{index:04d}.json"

    def _save(self):
        write_document(self.directory / JOURNAL, self.state)

    def _state(self):
        path = self.directory / JOURNAL
        if path.exists() or path.is_symlink():
            value = read_document(path)
            try:
                if (
                    set(value) != {"format", "pool_sha256", "current_policy", "workers", "pending", "previous_controller"}
                    or type(value["format"]) is not int
                    or value["format"] != 1
                    or value["pool_sha256"] != self.policy_hash
                    or not isinstance(value["workers"], list)
                    or len(value["workers"]) > self.policy["maximum_new_workers"]
                    or value["previous_controller"] is not None
                    and not hash_id(value["previous_controller"])
                ):
                    raise ValueError
                controller.validate_policy(value["current_policy"])
                seen = set()
                for index, record in enumerate(value["workers"], 1):
                    self._record(record, index)
                    if record["worker_id"] in seen or record["worker_id"] in {p.config["id"] for p in self.initial.profiles}:
                        raise ValueError
                    seen.add(record["worker_id"])
                pending = value["pending"]
                if pending is not None and (
                    set(pending) != {"index", "template_worker", "source_policy_sha256"}
                    or not scaling.integer(pending["index"], 1, self.policy["maximum_new_workers"])
                    or pending["index"] != len(value["workers"]) + 1
                    or pending["template_worker"] not in self.templates
                    or pending["source_policy_sha256"] != digest(value["current_policy"])
                ):
                    raise ValueError
            except (KeyError, TypeError, ValueError) as exc:
                raise InstallError("invalid or changed original pool journal; preserve enrollment and fleet generations") from exc
            self.state = value
        else:
            if (self.initial.directory / BINDING).exists() or (self.initial.directory / BINDING).is_symlink():
                expected = {"format": 1, "pool_sha256": self.policy_hash, "pool_dir": str(self.directory)}
                if read_document(self.initial.directory / BINDING) != expected:
                    raise InstallError("original fleet budget belongs to another pool policy; do not split its authority")
                raise InstallError("original pool journal is missing from its bound budget; restore it without resetting generations")
            if any(item.name != "provision.lock" for item in self.directory.iterdir()):
                raise InstallError("pool files exist without their original journal; do not reset enrollment generations")
            self.state = {
                "format": 1,
                "pool_sha256": self.policy_hash,
                "current_policy": self.policy["initial_policy"],
                "workers": [],
                "pending": None,
                "previous_controller": None,
            }
            write_document(path, self.state, new=True)

    def _record(self, record, index):
        if (
            not isinstance(record, dict)
            or set(record) != {"index", "template_worker", "worker_id", "plan_id"}
            or type(record["index"]) is not int
            or record["index"] != index
            or record["template_worker"] not in self.templates
            or not scaling.identifier(record["worker_id"])
            or not hash_id(record["plan_id"])
        ):
            raise ValueError

    def _allowed(self, profile, template, index):
        name = template.plan["name"][:52] + f"-pool-{index}"
        fields = {
            "provider",
            "account_identity",
            "region",
            "size",
            "image",
            "ssh_key_id",
            "ssh_public_key_sha256",
            "admin_cidr",
            "resources",
        }
        if (
            profile.fleet_paths != self._paths(index)
            or profile.plan["name"] != name
            or any(profile.plan[key] != template.plan[key] for key in fields)
            or read_document(Path(profile.fleet_paths["registration"])).get("name") != template.plan["name"] + f" pool {index}"
            or any(profile.config[key] != template.config[key] for key in ("server_url", "organization_id", "image_ids", "resources"))
            or profile.images != template.images
            or not pinned(profile.plan)
            or scaling.dollars(profile.plan["quote"]["compute_hourly_usd"]) > scaling.dollars(template.plan["quote"]["compute_hourly_usd"])
            or profile.plan["quote"]["maximum_hourly_usd"] != template.plan["quote"]["compute_hourly_usd"]
        ):
            raise InstallError("fresh pool worker exceeds its confirmed original template, quote, paths, or host-identity authority")

    def _derived(self, count):
        original = self.policy["initial_policy"]
        if count == 0:
            return original
        path = self._fleet_path(count)
        specification, profiles = scaling.load_fleet(path)
        value = dict(original)
        value.update(fleet_path=str(path), fleet_sha256=digest(specification), scope_sha256=digest(scope_for(specification, profiles)))
        return value

    def _current(self):
        policy = self.state["current_policy"]
        count = len(self.state["workers"])
        if policy != self._derived(count) or self.state["previous_controller"] != (digest(self._derived(count - 1)) if count else None):
            raise InstallError("pool cannot change the original budget, release, identity, lifecycle permissions, or fleet namespace")
        current = self._controller(policy)
        if any(current.fleet[key] != self.initial.fleet[key] for key in current.fleet if key != "workers"):
            raise InstallError("pool generations cannot change original fleet limits or organization")
        originals = {profile.config["id"]: profile for profile in self.initial.profiles}
        generated = {record["worker_id"]: record for record in self.state["workers"]}
        for profile in current.profiles:
            worker = profile.config["id"]
            if worker in originals:
                original_profile = originals[worker]
                if any(getattr(profile, key) != getattr(original_profile, key) for key in ("document", "config", "images", "fleet_paths")):
                    raise InstallError("original pool worker inputs changed")
            elif worker in generated:
                record = generated[worker]
                self._allowed(profile, self.templates[record["template_worker"]], record["index"])
                if profile.identity != record["plan_id"]:
                    raise InstallError("original generated pool plan identity changed")
            else:
                raise InstallError("unapproved worker was added to the original pool fleet")
        return current

    def _claim(self, current):
        path = self.initial.directory / BINDING
        expected = {"format": 1, "pool_sha256": self.policy_hash, "pool_dir": str(self.directory)}
        with locked_directory(self.initial.directory):
            if path.exists() or path.is_symlink():
                if read_document(path) != expected:
                    raise InstallError("original fleet budget belongs to another pool policy; do not split its authority")
                if not (self.initial.directory / BUDGET).exists():
                    raise InstallError("original pool fleet budget is missing; restore it without resetting reservations")
            else:
                if self.state["workers"] or self.state["pending"] is not None:
                    raise InstallError("original pool budget binding is missing; preserve every generation")
                current.locked = True
                try:
                    current._open()
                    write_document(path, expected, new=True)
                finally:
                    current.locked = False

    def _candidate(self, current, session):
        with locked_directory(current.directory):
            current.locked, current.session = True, session
            try:
                current._open()
                owned, snapshot = current._observe(), current._snapshot()
                decision = scaling.decide(current.fleet, snapshot, owned)
                if decision["reconcile_workers"] or decision["actions"] or not (decision["minimum_unmet"] or decision["uncovered_demand"]):
                    return None
                count, cost = current._totals()
                retained = [
                    p for p in current.profiles if current.state["reservations"][p.config["id"]]["status"] not in {"closed", "cancelled"}
                ]
                if count >= current.fleet["maximum_workers"] or len(retained) >= 64:
                    return None
                busy = {job["worker_id"] for job in snapshot["jobs"] if job["status"] != "queued"}
                active = {slot["worker_id"] for slot in owned if slot["phase"] == "active"}
                owned_ids = {slot["worker_id"] for slot in owned}
                ready = [
                    worker
                    for worker in snapshot["workers"]
                    if worker["id"] not in busy
                    and worker["id"] not in decision["reconcile_workers"]
                    and (worker["id"] not in owned_ids or worker["id"] in active)
                    and not worker["draining"]
                    and worker["revoked_at"] is None
                    and worker["last_seen_at"] is not None
                    and snapshot["observed_at"] - worker["last_seen_at"] <= scaling.FRESH_SECONDS
                ]
                demand = [job for job in snapshot["jobs"] if job["status"] == "queued" and job["runnable"]]
                target = min(decision["available_concurrency_slots"], len(demand))
                baseline = min(target, scaling.coverage(demand, ready))
                options = []
                for worker, template in self.templates.items():
                    if any(image not in snapshot["images"] for image in template.images):
                        continue
                    fresh = self.provider.compute_plan(
                        **{key: template.plan[key] for key in ("name", "region", "size", "ssh_key_id", "admin_cidr")},
                        maximum_hourly_usd=template.plan["quote"]["compute_hourly_usd"],
                    )
                    if any(fresh[key] != template.plan[key] for key in ("account_identity", "ssh_public_key_sha256", "resources")):
                        raise InstallError("pool template account, SSH key, or provider resources changed")
                    price = scaling.dollars(fresh["quote"]["compute_hourly_usd"])
                    shape = {"id": "pool-candidate", "image_ids": template.config["image_ids"], "resources": template.config["resources"]}
                    gain = min(target, scaling.coverage(demand, [*ready, shape])) - baseline
                    if cost + price <= scaling.dollars(current.fleet["maximum_hourly_usd"]) and (gain > 0 or decision["minimum_unmet"]):
                        options.append((-gain, price, worker))
                return min(options)[2] if options else None
            finally:
                current.locked = False

    def _prepare(self, current, client):
        pending = self.state["pending"]
        index, template = pending["index"], self.templates[pending["template_worker"]]
        paths = self._paths(index)
        directory = Path(paths["plan"]).parent
        if not directory.exists() and not directory.is_symlink():
            directory.mkdir(mode=0o700)
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise InstallError("fresh pool slot directory must be private, owner-owned, and not linked")
        grant = client.request("identity")
        if not isinstance(grant, dict) or grant.get("id") != self.policy["scaler_id"]:
            raise InstallError("pool scaler identity changed; rotation of the same grant is allowed, replacement is not")
        parameters = enrollment._parameters(
            current.fleet["server_url"],
            current.fleet["organization_id"],
            template.plan["name"] + f" pool {index}",
            template.config["image_ids"],
            template.config["resources"],
            Path(paths["registration"]),
            Path(paths["catalog"]),
        )
        if enrollment._catalog(grant, parameters) != template.images:
            raise InstallError("pool scaler catalog differs from the confirmed template; no replacement enrollment is authorized")
        enrollment.enroll_worker(
            client,
            directory / "enrollment",
            parameters["server_url"],
            parameters["organization_id"],
            parameters["name"],
            parameters["image_ids"],
            parameters["resources"],
            Path(paths["registration"]),
            Path(paths["catalog"]),
        )
        config, images = worker_inputs(
            parameters["server_url"],
            decode_input(read_private(Path(paths["registration"]), 65536)),
            decode_input(read_private(Path(paths["catalog"]), 65536)),
        )
        plan_path = Path(paths["plan"])
        if not plan_path.exists() and not plan_path.is_symlink():
            plan = plan_worker(
                self.provider,
                config,
                images,
                identity_client=self.identity_client,
                pin_ssh_host_key=True,
                name=template.plan["name"][:52] + f"-pool-{index}",
                **{key: template.plan[key] for key in ("region", "size", "ssh_key_id", "admin_cidr")},
                maximum_hourly_usd=template.plan["quote"]["compute_hourly_usd"],
            )
            save_plan(plan_path, plan, validator=validate_worker_plan)
        profile = WorkerProvisioner(self.provider, read_document(plan_path), config, images, identity_client=self.identity_client)
        profile.fleet_paths = paths
        self._allowed(profile, template, index)
        record = {"index": index, "template_worker": pending["template_worker"], "worker_id": config["id"], "plan_id": profile.identity}
        target = self._fleet_path(index)
        if not target.exists() and not target.is_symlink():
            with locked_directory(current.directory):
                current.locked = True
                try:
                    current._open()
                    current._observe()
                    following = copy.deepcopy(current.fleet)
                    following["workers"] = [
                        p.fleet_paths
                        for p in current.profiles
                        if current.state["reservations"][p.config["id"]]["status"] not in {"closed", "cancelled"}
                    ] + [paths]
                    write_document(target, following, new=True)
                finally:
                    current.locked = False
        following, profiles = scaling.load_fleet(target)
        expected_paths = [
            p.fleet_paths
            for p in current.profiles
            if p.config["id"] in {p.config["id"] for p in profiles if p.config["id"] != config["id"]}
        ] + [paths]
        if following["workers"] != expected_paths or any(following[key] != current.fleet[key] for key in following if key != "workers"):
            raise InstallError("original pool amendment target changed; preserve the exact pending generation")
        amend(current, target, confirm_fleet=current.fleet_hash, confirm_next_fleet=digest(following))
        # The cached release may live in a per-run temporary snapshot. Preserve
        # the originally confirmed public bundle path and every non-fleet field.
        next_policy = self._derived(index)
        previous = digest(self.state["current_policy"])
        self.state.update(current_policy=next_policy, workers=[*self.state["workers"], record], pending=None, previous_controller=previous)
        self._current()
        self._save()
        return {
            "phase": "replenished",
            "worker_id": config["id"],
            "plan_id": profile.identity,
            "fleet_file": str(target),
            "controller_policy_sha256": digest(next_policy),
            "creates_cloud_resources": False,
            "destroys_cloud_resources": False,
        }

    def cycle(self, session, *, confirm_pool, client, install_host, retire_host):
        if confirm_pool != self.policy_hash:
            raise InstallError("confirm the exact bounded pool policy before fresh enrollment or cloud actions")
        with locked_directory(self.directory):
            self._state()
            current = self._current()
            self._claim(current)
            grant = client.request("identity")
            if not isinstance(grant, dict) or grant.get("id") != self.policy["scaler_id"]:
                raise InstallError("pool scaler identity changed; preserve the original grant and generations")
            if self.state["pending"] is not None:
                result = self._prepare(current, client)
                return self._result("replenish", result, "original_pending_generation_resumed")
            replacement = None
            path = current.directory / controller.JOURNAL
            if path.exists() or path.is_symlink():
                previous = read_document(path)["policy_sha256"]
                if previous != current.policy_hash:
                    if previous != self.state["previous_controller"]:
                        raise InstallError("original pool controller authority changed outside its recorded generation")
                    replacement = previous
            result = current.cycle(
                session,
                confirm_controller=current.policy_hash,
                replace_controller=replacement,
                install_host=install_host,
                retire_host=retire_host,
            )
            if result["action"] is not None or result["reason"] not in {"prepared_slots_exhausted", "capacity_or_budget_unmet"}:
                return self._result(result["action"], result, result["reason"])
            if len(self.state["workers"]) >= self.policy["maximum_new_workers"]:
                return self._result(None, result, "authorized_fresh_enrollment_limit_reached")
            template = self._candidate(current, session)
            if template is None:
                return self._result(None, result, "no_authorized_fresh_capacity")
            self.state["pending"] = {
                "index": len(self.state["workers"]) + 1,
                "template_worker": template,
                "source_policy_sha256": digest(self.state["current_policy"]),
            }
            self._save()  # Durable generation before minting any worker credential.
            prepared = self._prepare(current, client)
            return self._result("replenish", prepared, "fresh_template_capacity_prepared")

    def _result(self, action, result, reason):
        return {
            "mode": "worker-pool",
            "pool_sha256": self.policy_hash,
            "action": action,
            "reason": reason,
            "result": result,
            "fresh_workers": len(self.state["workers"]),
            "maximum_new_workers": self.policy["maximum_new_workers"],
            "fleet_file": self.state["current_policy"]["fleet_path"],
            "fleet_state_dir": self.policy["initial_policy"]["fleet_state_dir"],
            "container_execution_verified": False,
        }
