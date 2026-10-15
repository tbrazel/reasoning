"""Durable, serialized budget reservations for confirmed owned worker creation."""

from __future__ import annotations

import copy
import hashlib
import re
import time
from decimal import Decimal
from pathlib import Path

from . import scaling
from .host import InstallError, canonical
from .private import locked_directory, read_document, write_document
from .worker_cancel import CANCELLATION, WorkerCancellation
from .worker_host import destruction_result
from .worker_provision import enrollment_binding
from .worker_retire import JOURNAL

BUDGET = "fleet-budget.json"
BINDING = "fleet-binding.json"
AMENDMENT = "fleet-amendment.json"


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def hash_id(value):
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def reservation_id(fleet_hash, worker, decision, timestamp):
    return digest({"fleet_sha256": fleet_hash, "worker_id": worker, "decision_id": decision, "reserved_at": timestamp})


def scope_for(specification, profiles):
    return {
        "format": 1,
        "fleet_sha256": digest(specification),
        "fleet": specification,
        "slots": {
            profile.config["id"]: {
                "plan_id": profile.identity,
                "deployment_id": profile.document["deployment_id"],
                "paths": profile.fleet_paths,
                "account_identity": profile.plan["account_identity"],
                "hourly_usd": profile.plan["quote"]["compute_hourly_usd"],
            }
            for profile in profiles
        },
    }


def validate_rows(scope, rows, origins):
    if not isinstance(rows, dict) or set(rows) != set(scope["slots"]) or not isinstance(origins, dict) or set(origins) != set(rows):
        raise ValueError
    for worker, row in rows.items():
        if (
            not hash_id(origins[worker])
            or not isinstance(row, dict)
            or set(row) != {"status", "reservation_id", "decision_id", "reserved_at"}
        ):
            raise ValueError
        if row["status"] == "unreserved":
            if any(row[key] is not None for key in ("reservation_id", "decision_id", "reserved_at")):
                raise ValueError
        elif row["status"] in {"reserved", "closed", "cancelled"}:
            if (
                not scaling.integer(row["reserved_at"], 0, 2**63 - 1)
                or row["decision_id"] is not None
                and not hash_id(row["decision_id"])
                or row["reservation_id"] != reservation_id(origins[worker], worker, row["decision_id"], row["reserved_at"])
            ):
                raise ValueError
        else:
            raise ValueError


def validate_lineage(value):
    """Validate birth namespaces, unchanged ownership, and only terminal slot removal."""
    history = value["history"]
    if not isinstance(history, list) or not 1 <= len(history) <= 128:
        raise ValueError
    seen, identities, paths, account = {}, set(), set(), None
    previous = None
    for frame in [*history, {key: value[key] for key in ("scope", "origins", "reservations")}]:
        if not isinstance(frame, dict) or set(frame) != {"scope", "origins", "reservations"}:
            raise ValueError
        scope = frame["scope"]
        specification, profiles = scaling.fleet_inputs(scope["fleet"])
        if canonical(scope_for(specification, profiles)) != canonical(scope):
            raise ValueError
        if (
            specification["organization_id"] != value["scope"]["fleet"]["organization_id"]
            or specification["server_url"] != value["scope"]["fleet"]["server_url"]
        ):
            raise ValueError
        validate_rows(scope, frame["reservations"], frame["origins"])
        for worker, slot in scope["slots"].items():
            account = slot["account_identity"] if account is None else account
            if slot["account_identity"] != account:
                raise ValueError
            if worker in seen:
                if previous is None or worker not in previous["scope"]["slots"] or canonical(slot) != canonical(seen[worker]):
                    raise ValueError
                old = previous["reservations"][worker]
                new = frame["reservations"][worker]
                if frame["origins"][worker] != previous["origins"][worker]:
                    raise ValueError
                if old["status"] != "unreserved" and (
                    any(old[key] != new[key] for key in ("reservation_id", "decision_id", "reserved_at"))
                    or new["status"] not in ({"reserved", "closed", "cancelled"} if old["status"] == "reserved" else {old["status"]})
                ):
                    raise ValueError
            else:
                if (
                    frame["origins"][worker] != scope["fleet_sha256"]
                    or slot["paths"]["state_dir"] in paths
                    or identities & {slot["plan_id"], slot["deployment_id"]}
                ):
                    raise ValueError
                for path in paths:
                    if Path(path) in Path(slot["paths"]["state_dir"]).parents or Path(slot["paths"]["state_dir"]) in Path(path).parents:
                        raise ValueError
                seen[worker] = slot
                paths.add(slot["paths"]["state_dir"])
                identities.update((slot["plan_id"], slot["deployment_id"]))
        if previous is not None and any(
            previous["reservations"][worker]["status"] not in {"closed", "cancelled"}
            for worker in set(previous["scope"]["slots"]) - set(scope["slots"])
        ):
            raise ValueError
        previous = frame


def selected_advice(advice, fleet, worker, plan, confirmation, *, now=None):
    """Validate an exact fresh decision; separate operator authority is required."""
    now = int(time.time()) if now is None else now
    try:
        body = {key: value for key, value in advice.items() if key != "decision_id"}
        if (
            not hash_id(confirmation)
            or advice["decision_id"] != confirmation
            or digest(body) != confirmation
            or type(advice["format"]) is not int
            or advice["format"] != 1
            or advice["fleet_sha256"] != digest(fleet)
            or advice["organization_id"] != fleet["organization_id"]
            or advice["server_url"] != fleet["server_url"]
            or advice["mode"] != "advisory"
            or advice["creates_resources"] is not False
            or advice["destroys_resources"] is not False
            or not scaling.integer(advice["observed_at"], max(0, now - scaling.SNAPSHOT_SECONDS), now + 5)
            or type(advice["expires_at"]) is not int
            or advice["expires_at"] != advice["observed_at"] + scaling.SNAPSHOT_SECONDS
            or now >= advice["expires_at"]
            or {"action": "provision", "worker_id": worker, "plan_id": plan} not in advice["actions"]
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise InstallError("confirm a fresh fleet-bound provision decision and exact worker/plan IDs") from exc


class FleetLease:
    """Process-local capability valid only during the coordinator's live lock."""

    def __init__(self, coordinator, profile):
        self.coordinator, self.profile = coordinator, profile

    def check(self, profile, *, creation):
        owner = self.coordinator
        if (
            not owner.locked
            or owner.lease is not self
            or profile is not self.profile
            or profile.fleet_directory != Path(profile.fleet_paths["state_dir"])
        ):
            raise InstallError("fleet reservation is not held by the live budget coordinator")
        owner._validate(read_document(owner.directory / BUDGET))
        owner._binding(profile)
        row = owner.state["reservations"][profile.config["id"]]
        if row["status"] != "reserved":
            raise InstallError("worker creation requires a durable fleet budget reservation")
        if creation:
            owner._budget()
            owner._demand(profile)


class FleetCoordinator:
    def __init__(self, provider, fleet_path, directory, *, snapshot_reader=None, identity_client=None):
        self.provider, self.fleet_path = provider, Path(fleet_path)
        self.directory = Path(directory).absolute()
        self.fleet, self.profiles = scaling.load_fleet(self.fleet_path)
        if ".." in self.directory.parts or str(Path(directory)) != str(self.directory):
            raise InstallError("fleet state directory must be an explicit canonical absolute path")
        directories = [Path(item.fleet_paths["state_dir"]) for item in self.profiles]
        if any(self.directory == path or self.directory in path.parents or path in self.directory.parents for path in directories):
            raise InstallError("fleet coordinator and worker ownership directories must not overlap")
        if any(a in b.parents or b in a.parents for index, a in enumerate(directories) for b in directories[index + 1 :]):
            raise InstallError("fleet worker ownership directories must not overlap")
        self.fleet_hash = digest(self.fleet)
        self.snapshot_reader = snapshot_reader or scaling.read_snapshot
        self.locked, self.state = False, None
        self.lease = None
        self.scope = scope_for(self.fleet, self.profiles)
        accounts = [item["account_identity"] for item in self.scope["slots"].values()]
        if any(account != accounts[0] for account in accounts):
            raise InstallError("fleet slots must belong to the same DigitalOcean account")
        for profile in self.profiles:
            profile.provider = provider
            profile.identity_client = identity_client

    def _validate(self, value):
        try:
            if set(value) not in ({"scope", "reservations"}, {"scope", "reservations", "origins", "history"}) or canonical(
                value["scope"]
            ) != canonical(self.scope):
                raise ValueError
            if "history" in value:
                validate_lineage(value)
                from .fleet_amend import read_amendment

                receipt = read_amendment(self.directory / AMENDMENT)
                if any(canonical(value[key]) != canonical(receipt["next_state"][key]) for key in ("scope", "origins", "history")):
                    raise ValueError
            else:
                if (self.directory / AMENDMENT).exists() or (self.directory / AMENDMENT).is_symlink():
                    raise ValueError
                validate_rows(self.scope, value["reservations"], dict.fromkeys(self.scope["slots"], self.fleet_hash))
        except (KeyError, TypeError, ValueError) as exc:
            raise InstallError("invalid fleet budget or changed immutable scope; preserve all journals") from exc
        if self.state is not None and value != self.state:
            raise InstallError("fleet budget changed while its coordinator lock was held")

    def origin(self, worker):
        return self.state.get("origins", {}).get(worker, self.fleet_hash)

    def _marker(self, profile):
        return {
            "format": 1,
            "budget_dir": str(self.directory),
            "fleet_sha256": self.origin(profile.config["id"]),
            "plan_id": profile.identity,
        }

    def _binding(self, profile):
        path = Path(profile.fleet_paths["state_dir"]) / BINDING
        if read_document(path) != self._marker(profile):
            raise InstallError("worker is bound to another fleet budget; preserve its original coordinator")
        profile.fleet_sha256 = self.origin(profile.config["id"])

    def _open(self):
        # Re-read operator inputs under the coordinator lock; the constructor's
        # observations must never replace journals changed by another process.
        self.state = None
        amendment = self.directory / AMENDMENT
        if amendment.exists() or amendment.is_symlink():
            from .fleet_amend import read_amendment

            record = read_amendment(amendment)
            if record["phase"] != "applied" or record["new_fleet_sha256"] != self.fleet_hash:
                raise InstallError("fleet amendment is pending or this fleet is superseded; resume its exact amendment")
            if not (self.directory / BUDGET).exists() and not (self.directory / BUDGET).is_symlink():
                raise InstallError("amended fleet budget is missing; restore its original history rather than resetting it")
        fleet, profiles = scaling.load_fleet(self.fleet_path)
        if fleet != self.fleet or any(
            current.document != original.document or current.config != original.config or current.images != original.images
            for current, original in zip(profiles, self.profiles, strict=True)
        ):
            raise InstallError("fleet inputs changed before acquiring the budget lock")
        for current, profile in zip(profiles, self.profiles, strict=True):
            profile.state = current.state
        path = self.directory / BUDGET
        if path.exists() or path.is_symlink():
            value = read_document(path)
            self._validate(value)
            self.state = value
        else:
            # A missing ledger cannot silently reset reservations for already
            # bound slots, even when their ownership journals look empty.
            if any(
                (Path(p.fleet_paths["state_dir"]) / BINDING).exists() or (Path(p.fleet_paths["state_dir"]) / BINDING).is_symlink()
                for p in self.profiles
            ):
                raise InstallError("bound worker exists without its original fleet budget; restore the coordinator")
            self.state = {
                "scope": self.scope,
                "reservations": {
                    p.config["id"]: {"status": "unreserved", "reservation_id": None, "decision_id": None, "reserved_at": None}
                    for p in self.profiles
                },
            }
            write_document(path, self.state, new=True)
        for profile in self.profiles:
            with locked_directory(Path(profile.fleet_paths["state_dir"])) as directory:
                journal = directory / "digitalocean.json"
                if journal.exists() or journal.is_symlink():
                    profile.state = read_document(journal)
                    profile._validate_state()
                else:
                    if self.state["reservations"][profile.config["id"]]["status"] != "unreserved":
                        raise InstallError("reserved worker ownership journal is missing; restore it without new creation")
                    write_document(journal, profile.state, new=True)
                marker = directory / BINDING
                if marker.exists() or marker.is_symlink():
                    self._binding(profile)
                else:
                    write_document(marker, self._marker(profile), new=True)
                    self._binding(profile)

    def _reserve(self, profile, decision):
        timestamp = int(time.time())
        worker = profile.config["id"]
        self.state["reservations"][worker] = {
            "status": "reserved",
            "reservation_id": reservation_id(self.origin(worker), worker, decision, timestamp),
            "decision_id": decision,
            "reserved_at": timestamp,
        }
        write_document(self.directory / BUDGET, self.state)

    def _closed(self, profile):
        """Release only complete, bound retirement plus fresh exact absence."""
        path = Path(profile.fleet_paths["state_dir"]) / JOURNAL
        if not path.exists() and not path.is_symlink():
            return False
        value = read_document(path)
        try:
            if set(value) != {"intent", "proof", "deletion"} or value["deletion"] != dict.fromkeys(profile.kinds, "absent"):
                return False
            if any(row["status"] != "created" for row in profile.state["resources"].values()):
                return False
            intent = value["intent"]
            request = {
                "format": 1,
                "plan_id": profile.identity,
                "deployment_id": profile.document["deployment_id"],
                "droplet_id": profile.state["resources"]["droplet"]["id"],
                "delete_host_data": True,
            }
            if set(intent) != {"format", "request", "binding", "resources"} or type(intent["format"]) is not int or intent["format"] != 1:
                raise ValueError
            if canonical(intent["request"]) != canonical(request) or intent["resources"] != {
                k: v["id"] for k, v in profile.state["resources"].items()
            }:
                raise ValueError
            binding = intent["binding"]
            enrollment = enrollment_binding(profile.config, profile.images)
            if any(
                binding.get(key) != enrollment[key]
                for key in ("server_url", "worker_id", "organization_id", "enrollment_sha256", "catalog_sha256")
            ):
                raise ValueError
            proof = destruction_result(value["proof"], binding, request)
            if proof["retirement"]["phase"] != "retired":
                raise ValueError
            for kind, row in profile.state["resources"].items():
                collection = {"tag": "tags", "firewall": "firewalls", "droplet": "droplets"}[kind]
                if self.provider.get_owned(collection + "/" + str(row["id"])) is not None or profile._collect(kind):
                    raise InstallError("retired fleet resource reappeared; do not release its reservation")
            return True
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise InstallError("retirement does not prove bound fleet resource absence; preserve the reservation") from exc

    def _observe(self):
        owned = scaling.inventory(self.provider, self.profiles)
        for profile, slot in zip(self.profiles, owned, strict=True):
            row = self.state["reservations"][profile.config["id"]]
            started = any(resource["status"] != "not_started" for resource in profile.state["resources"].values())
            if row["status"] == "unreserved" and started:
                self._reserve(profile, None)  # Existing cost, not new creation consent.
                row = self.state["reservations"][profile.config["id"]]
            cancellation = WorkerCancellation(self, profile, row)
            cancelling = (Path(profile.fleet_paths["state_dir"]) / CANCELLATION).exists() or (
                Path(profile.fleet_paths["state_dir"]) / CANCELLATION
            ).is_symlink()
            if row["status"] == "reserved" and cancelling and cancellation.verified_absent():
                row["status"] = "cancelled"
                write_document(self.directory / BUDGET, self.state)
            if row["status"] == "cancelled":
                if not cancellation.verified_absent():
                    raise InstallError("cancelled fleet slot has lost its terminal absence evidence")
                slot["phase"], slot["hourly_usd"] = "closed", "0"
                continue
            if row["status"] == "reserved" and self._closed(profile):
                row["status"] = "closed"
                write_document(self.directory / BUDGET, self.state)
            if row["status"] == "closed":
                if not self._closed(profile):
                    raise InstallError("closed fleet reservation has lost its absence evidence")
                slot["phase"], slot["hourly_usd"] = "closed", "0"
            elif row["status"] == "reserved":
                slot["hourly_usd"] = self.scope["slots"][profile.config["id"]]["hourly_usd"]
                if slot["phase"] in {"unprovisioned", "closed"}:
                    slot["phase"] = "uncertain"
                if cancelling:
                    slot["phase"] = "retiring"
        return owned

    def _totals(self):
        workers = [worker for worker, row in self.state["reservations"].items() if row["status"] == "reserved"]
        cost = sum((Decimal(self.scope["slots"][worker]["hourly_usd"]) for worker in workers), Decimal(0))
        return len(workers), cost

    def _budget(self, extra=None):
        count, cost = self._totals()
        if extra is not None:
            count += 1
            cost += Decimal(extra.plan["quote"]["compute_hourly_usd"])
        if count > self.fleet["maximum_workers"] or cost > scaling.dollars(self.fleet["maximum_hourly_usd"]):
            raise InstallError("durable fleet worker-count or hourly VM quote ceiling would be exceeded")

    def _demand(self, profile):
        owned = self._observe()
        snapshot = self.snapshot_reader(self.fleet["server_url"], self.fleet["organization_id"], self.session)
        if not scaling.resume_needed(self.fleet, snapshot, owned, profile.config["id"]):
            raise InstallError("fresh queue, capacity, policy, or budget no longer recommends this worker")

    def reconcile(self):
        with locked_directory(self.directory):
            self.locked = True
            try:
                self._open()
                self._observe()
                count, cost = self._totals()
                return {
                    "fleet_sha256": self.fleet_hash,
                    "fleet_state_dir": str(self.directory),
                    "creates_resources": False,
                    "destroys_resources": False,
                    "reserved_workers": count,
                    "reserved_hourly_usd": str(cost),
                    "maximum_workers": self.fleet["maximum_workers"],
                    "maximum_hourly_usd": self.fleet["maximum_hourly_usd"],
                    "ceiling_exceeded": count > self.fleet["maximum_workers"] or cost > scaling.dollars(self.fleet["maximum_hourly_usd"]),
                    "reservations": copy.deepcopy(self.state["reservations"]),
                }
            finally:
                self.locked = False

    def cancel(self, *, confirm_worker, confirm_plan, confirm_reservation, retry_delete=None):
        profiles = [p for p in self.profiles if p.config["id"] == confirm_worker and p.identity == confirm_plan]
        if len(profiles) != 1 or not hash_id(confirm_reservation):
            raise InstallError("cancel requires exact original fleet worker, plan, and reservation confirmations")
        profile = profiles[0]
        with locked_directory(self.directory):
            self.locked = True
            try:
                self._open()
                row = self.state["reservations"][confirm_worker]
                if row["status"] not in {"reserved", "cancelled"} or row["reservation_id"] != confirm_reservation:
                    raise InstallError("confirm-reservation must match the original reserved or cancelled worker intent")
                with locked_directory(Path(profile.fleet_paths["state_dir"])):
                    cancellation = WorkerCancellation(self, profile, row)
                    result = cancellation.run(retry_delete=retry_delete)
                    if result["phase"] == "cancelled":
                        row["status"] = "cancelled"
                        write_document(self.directory / BUDGET, self.state)
                    result["reservation_released"] = row["status"] == "cancelled"
                    return result
            finally:
                self.locked = False

    def apply(self, session, *, confirm_worker, confirm_plan, advice=None, confirm_decision=None, resume_reservation=None):
        profiles = [p for p in self.profiles if p.config["id"] == confirm_worker and p.identity == confirm_plan]
        if len(profiles) != 1 or (advice is None) == (resume_reservation is None):
            raise InstallError("select one exact fleet worker/plan and either a fresh decision or original reservation")
        profile = profiles[0]
        self.session = session
        if advice is not None:
            selected_advice(advice, self.fleet, confirm_worker, confirm_plan, confirm_decision)
        elif not hash_id(resume_reservation) or confirm_decision is not None:
            raise InstallError("resume requires the exact original reservation ID, not a new decision")
        with locked_directory(self.directory):
            self.locked = True
            try:
                self._open()
                return self._apply_locked(profile, advice=advice, confirm_decision=confirm_decision, resume_reservation=resume_reservation)
            finally:
                self.locked = False

    def _apply_locked(self, profile, *, advice=None, confirm_decision=None, resume_reservation=None):
        """Shared manual/controller path; caller holds the live original budget lock."""
        if not self.locked or self.state is None or profile not in self.profiles or (advice is None) == (resume_reservation is None):
            raise InstallError("worker application requires the live original fleet coordinator")
        worker, plan = profile.config["id"], profile.identity
        owned = self._observe()
        row = self.state["reservations"][worker]
        if advice is not None:
            selected_advice(advice, self.fleet, worker, plan, confirm_decision)
            if row["status"] != "unreserved":
                raise InstallError("worker is already reserved or closed; resume its original intent without reserving again")
        elif not hash_id(resume_reservation) or row["status"] != "reserved" or row["reservation_id"] != resume_reservation:
            raise InstallError("resume-reservation must match the original durable worker reservation")
        snapshot = self.snapshot_reader(self.fleet["server_url"], self.fleet["organization_id"], self.session)
        scaling.validate_snapshot(snapshot, self.fleet["organization_id"], int(time.time()))
        # Before any new paid VM intent, even a free-resource-only resume must
        # still be wanted by the current queue/warm minimum.
        if profile.state["resources"]["droplet"]["status"] == "not_started":
            if advice is not None:
                fresh = scaling.decide(self.fleet, snapshot, owned)
                wanted = {"action": "provision", "worker_id": worker, "plan_id": plan} in fresh["actions"]
            else:
                wanted = scaling.resume_needed(self.fleet, snapshot, owned, worker)
            if not wanted:
                raise InstallError("fresh queue, capacity, policy, or budget no longer recommends this worker")
            self._budget(extra=profile if row["status"] == "unreserved" else None)
        if advice is not None:
            selected_advice(advice, self.fleet, worker, plan, confirm_decision)
            self._reserve(profile, confirm_decision)
        self.lease = FleetLease(self, profile)
        profile.fleet_lease = self.lease
        try:
            result = profile.apply(Path(profile.fleet_paths["state_dir"]), plan)
            result["fleet_sha256"] = self.fleet_hash
            result["reservation_id"] = self.state["reservations"][worker]["reservation_id"]
            result["reserved_owned_hourly_usd"] = str(self._totals()[1])
            return result
        finally:
            profile.fleet_lease = None
            self.lease = None
