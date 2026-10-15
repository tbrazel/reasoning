"""Read-only queue/capacity decisions for an explicitly bounded owned worker fleet."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import time
from decimal import Decimal
from pathlib import Path

import httpx

from reasoning_ledger.server.compute_catalog import ComputeImage, fits, resources

from .host import InstallError, canonical, public_origin
from .private import protected_parent, read_document, read_private
from .worker_cancel import CANCELLATION, completed_cancellation
from .worker_host import decode_input
from .worker_provision import WorkerProvisioner, worker_inputs
from .worker_retire import JOURNAL

FRESH_SECONDS = 45
SNAPSHOT_SECONDS = 30


def integer(value, low, high):
    return type(value) is int and low <= value <= high


def identifier(value):
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{32}", value) is not None


def dollars(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,5}(?:\.[0-9]{1,12})?", value):
        raise InstallError("fleet dollar ceilings require bounded nonnegative decimal strings")
    amount = Decimal(value)
    if not 0 <= amount <= 10000:
        raise InstallError("fleet dollar ceiling exceeds its supported bound")
    return amount


def load_fleet(path: Path):
    return fleet_inputs(read_document(path))


def fleet_inputs(fleet):
    if (
        set(fleet)
        != {
            "format",
            "server_url",
            "organization_id",
            "minimum_workers",
            "maximum_workers",
            "maximum_hourly_usd",
            "idle_seconds",
            "workers",
        }
        or type(fleet["format"]) is not int
        or fleet["format"] != 1
        or public_origin(fleet["server_url"]) != fleet["server_url"]
        or not identifier(fleet["organization_id"])
        or not integer(fleet["maximum_workers"], 0, 64)
        or not integer(fleet["minimum_workers"], 0, fleet["maximum_workers"])
        or not integer(fleet["idle_seconds"], 60, 86400)
        or not isinstance(fleet["workers"], list)
        or len(fleet["workers"]) > 64
    ):
        raise InstallError("invalid bounded worker fleet configuration")
    dollars(fleet["maximum_hourly_usd"])
    profiles, seen, directories = [], set(), set()
    for entry in fleet["workers"]:
        if not isinstance(entry, dict) or set(entry) != {"plan", "registration", "catalog", "state_dir"}:
            raise InstallError("fleet slots require original plan, registration, catalog, and ownership paths")
        if any(
            not isinstance(value, str)
            or not Path(value).is_absolute()
            or ".." in Path(value).parts
            or str(Path(value)) != value
            or value.startswith("//")
            for value in entry.values()
        ):
            raise InstallError("fleet paths must be explicit absolute paths without traversal")
        document = read_document(Path(entry["plan"]))
        config, images = worker_inputs(
            fleet["server_url"],
            decode_input(read_private(Path(entry["registration"]), 65536)),
            decode_input(read_private(Path(entry["catalog"]), 65536)),
        )
        instance = WorkerProvisioner(None, document, config, images)
        if config["organization_id"] != fleet["organization_id"] or config["id"] in seen or entry["state_dir"] in directories:
            raise InstallError("fleet slots must have distinct workers/ownership directories in one organization")
        if any(item.identity == instance.identity or item.document["deployment_id"] == document["deployment_id"] for item in profiles):
            raise InstallError("fleet slots must not reuse a saved cloud identity")
        directory = Path(entry["state_dir"])
        protected_parent(directory.absolute())
        instance.state = {
            "format": 1,
            "plan_id": instance.identity,
            "resources": {kind: {"status": "not_started", "id": None} for kind in instance.kinds},
        }
        if directory.exists() or directory.is_symlink():
            info = directory.lstat()
            if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700 or info.st_uid != os.getuid():
                raise InstallError("fleet ownership directories must be private and owner-owned")
            journal = directory / "digitalocean.json"
            if journal.exists() or journal.is_symlink():
                instance.state = read_document(journal)
                instance._validate_state()
            elif any(path.name != "provision.lock" for path in directory.iterdir()):
                raise InstallError("ownership files exist without their original cloud journal; restore it")
        instance.fleet_paths = entry
        instance.fleet_sha256 = hashlib.sha256(canonical(fleet)).hexdigest()
        binding = directory / "fleet-binding.json"
        if binding.exists() or binding.is_symlink():
            marker = read_document(binding)
            if (
                set(marker) != {"format", "budget_dir", "fleet_sha256", "plan_id"}
                or type(marker["format"]) is not int
                or marker["format"] != 1
                or marker["plan_id"] != instance.identity
                or not isinstance(marker["fleet_sha256"], str)
                or not re.fullmatch(r"[a-f0-9]{64}", marker["fleet_sha256"])
                or not isinstance(marker["budget_dir"], str)
                or not Path(marker["budget_dir"]).is_absolute()
                or str(Path(marker["budget_dir"])) != marker["budget_dir"]
                or ".." in Path(marker["budget_dir"]).parts
            ):
                raise InstallError("invalid original fleet slot binding; preserve the coordinator")
            instance.fleet_sha256 = marker["fleet_sha256"]
        profiles.append(instance)
        seen.add(config["id"])
        directories.add(entry["state_dir"])
    return fleet, profiles


def read_snapshot(origin, organization, credential, *, transport=None):
    if public_origin(origin) != origin or not identifier(organization) or not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", credential):
        raise InstallError("invalid organization scaling session or HTTPS origin")
    try:
        with (
            httpx.Client(
                base_url=origin,
                headers={"Authorization": "Bearer " + credential},
                trust_env=False,
                follow_redirects=False,
                timeout=10,
                transport=transport,
            ) as client,
            client.stream(
                "GET",
                "/api/compute-scaler/snapshot"
                if credential.startswith("rns_")
                else f"/api/organizations/{organization}/compute-scaling-snapshot",
            ) as response,
        ):
            if response.status_code != 200:
                raise InstallError(
                    "scaling snapshot requires a current scoped scaler credential or owner/admin session; no action was taken"
                )
            body = bytearray()
            for block in response.iter_bytes(chunk_size=8192):
                body.extend(block)
                if len(body) > 16 * 1024 * 1024:
                    raise InstallError("scaling snapshot exceeds its byte allowance")
            return decode_input(body)
    except (httpx.HTTPError, ValueError) as exc:
        raise InstallError("scaling snapshot is unavailable or invalid; no action was taken") from exc


def validate_snapshot(value, org, now):
    try:
        expanded = isinstance(value, dict) and value.get("format") == 2
        keys = {"format", "organization_id", "observed_at", "max_active_jobs", "images", "jobs", "workers"}
        if expanded:
            keys.add("queued_remaining")
        if (
            not isinstance(value, dict)
            or set(value) != keys
            or type(value["format"]) is not int
            or value["format"] not in {1, 2}
            or value["organization_id"] != org
            or not integer(value["observed_at"], max(0, now - SNAPSHOT_SECONDS), now + 5)
            or not integer(value["max_active_jobs"], 1, 4096 if expanded else 8)
            or not isinstance(value["jobs"], list)
            or not expanded and len(value["jobs"]) > 8
            or not isinstance(value["workers"], list)
            or not expanded and len(value["workers"]) > 72
            or not isinstance(value["images"], list)
            or len(value["images"]) > 32
        ):
            raise ValueError
        if expanded and (
            not integer(value["queued_remaining"], 0, 9_000_000_000_000_000)
            or sum(job.get("status") == "queued" for job in value["jobs"] if isinstance(job, dict)) > value["max_active_jobs"]
            or len(canonical(value)) > 16 * 1024 * 1024
        ):
            raise ValueError
        catalog = {image["id"]: ComputeImage.parse(image).record() for image in value["images"]}
        if len(catalog) != len(value["images"]):
            raise ValueError
        seen = set()
        for worker in value["workers"]:
            if not isinstance(worker, dict) or set(worker) != {
                "id",
                "organization_id",
                "name",
                "created_at",
                "last_seen_at",
                "draining",
                "revoked_at",
                "retired_at",
                "retirement_id",
                "image_ids",
                "resources",
                "idle_since_at",
            }:
                raise ValueError
            if (
                not identifier(worker["id"])
                or worker["id"] in seen
                or worker["organization_id"] != org
                or not isinstance(worker["name"], str)
            ):
                raise ValueError
            seen.add(worker["id"])
            if (
                not integer(worker["draining"], 0, 1)
                or not integer(worker["created_at"], 0, value["observed_at"] + 5)
                or not integer(worker["idle_since_at"], worker["created_at"], value["observed_at"] + 5)
            ):
                raise ValueError
            for key in ("last_seen_at", "revoked_at", "retired_at"):
                if worker[key] is not None and not integer(worker[key], worker["created_at"], value["observed_at"] + 5):
                    raise ValueError
            if worker["retirement_id"] is not None and not identifier(worker["retirement_id"]):
                raise ValueError
            if (
                not isinstance(worker["image_ids"], list)
                or not 1 <= len(worker["image_ids"]) <= 32
                or len(set(worker["image_ids"])) != len(worker["image_ids"])
                or not all(isinstance(item, str) for item in worker["image_ids"])
            ):
                raise ValueError
            resources(worker["resources"])
        seen_jobs = set()
        for job in value["jobs"]:
            if not isinstance(job, dict) or set(job) != {
                "id",
                "status",
                "worker_id",
                "created_at",
                "image_id",
                "image_reference",
                "resources",
                "runnable",
                "lease_expires_at",
                "deadline_at",
            }:
                raise ValueError
            if (
                not identifier(job["id"])
                or job["id"] in seen_jobs
                or job["status"] not in {"queued", "running", "outcome_unknown"}
                or type(job["runnable"]) is not bool
                or not integer(job["created_at"], 0, value["observed_at"] + 5)
                or not isinstance(job["image_id"], str)
                or not isinstance(job["image_reference"], str)
            ):
                raise ValueError
            seen_jobs.add(job["id"])
            resources(job["resources"])
            if job["status"] == "queued":
                if job["worker_id"] is not None or job["lease_expires_at"] is not None or job["deadline_at"] is not None:
                    raise ValueError
                if job["runnable"] and (job["image_id"] not in catalog or catalog[job["image_id"]]["reference"] != job["image_reference"]):
                    raise ValueError
            elif job["worker_id"] not in seen or job["runnable"]:
                raise ValueError
            for key in ("lease_expires_at", "deadline_at"):
                if job[key] is not None and not integer(job[key], 0, 2**63 - 1):
                    raise ValueError
        return catalog
    except (KeyError, TypeError, ValueError) as exc:
        raise InstallError("invalid, stale, or mismatched organization scaling snapshot") from exc


def inventory(provider, profiles):
    result = []
    for instance in profiles:
        instance.provider = provider
        current = provider.compute_plan(
            **{key: instance.plan[key] for key in ("name", "region", "size", "ssh_key_id", "admin_cidr")},
            maximum_hourly_usd=instance.plan["quote"]["maximum_hourly_usd"],
        )
        if any(current[key] != instance.plan[key] for key in ("account_identity", "ssh_public_key_sha256", "resources")) or Decimal(
            current["quote"]["compute_hourly_usd"]
        ) > Decimal(instance.plan["quote"]["compute_hourly_usd"]):
            raise InstallError("fleet account, resources, key, or price changed; no scaling action was taken")
        if result and result[0]["account_identity"] != current["account_identity"]:
            raise InstallError("fleet slots must belong to the same DigitalOcean account")
        statuses = instance.state["resources"]
        observed_pending_vm = None
        if statuses["droplet"]["status"] == "request_pending":
            matches = instance._collect("droplet")
            if len(matches) > 1:
                raise InstallError("duplicate pending fleet VMs; reconcile without scaling")
            if matches:
                instance._check("droplet", matches[0])
                observed_pending_vm = matches[0]["id"]
        retiring = (Path(instance.fleet_paths["state_dir"]) / JOURNAL).exists() or (
            Path(instance.fleet_paths["state_dir"]) / JOURNAL
        ).is_symlink()
        cancelling = (Path(instance.fleet_paths["state_dir"]) / CANCELLATION).exists() or (
            Path(instance.fleet_paths["state_dir"]) / CANCELLATION
        ).is_symlink()
        phase, present_vm, uncertain, firewall_ready = "unprovisioned", False, False, False
        present_kinds = set()
        for kind in instance.kinds:
            matches = instance._collect(kind)
            if len(matches) > 1:
                raise InstallError("duplicate fleet ownership markers; reconcile without scaling")
            row = statuses[kind]
            if row["status"] == "not_started":
                if matches:
                    raise InstallError("unmanaged resource shares a fleet marker; do not adopt it")
                continue
            phase = "provisioning"
            if matches:
                present_kinds.add(kind)
                # A lost VM receipt can already affect tag-based firewall
                # membership. Use its observed identity only for this metadata
                # check, never adopting it into the journal or action authority.
                original_id = statuses["droplet"]["id"]
                if kind == "firewall" and original_id is None and observed_pending_vm is not None:
                    statuses["droplet"]["id"] = observed_pending_vm
                try:
                    instance._check(kind, matches[0])
                finally:
                    statuses["droplet"]["id"] = original_id
                if kind == "firewall":
                    firewall_ready = instance._firewall_ready(matches[0])
            if row["status"] == "created":
                collection = {"tag": "tags", "firewall": "firewalls", "droplet": "droplets"}[kind]
                exact = provider.get_owned(collection + "/" + str(row["id"]))
                if exact is None:
                    if matches:
                        raise InstallError("exact fleet resource absence and marker discovery disagree")
                    phase = "retiring" if retiring else "uncertain"
                    uncertain = True
                elif len(matches) != 1 or exact.get(kind) != matches[0]:
                    raise InstallError("exact fleet identity and marker discovery disagree")
                if kind == "droplet" and exact is not None:
                    present_vm = True
                    phase = "active" if exact[kind].get("status") == "active" and not retiring else "provisioning"
                    if phase == "active":
                        instance._address(exact[kind])
            else:
                phase = "uncertain"
                uncertain = True
        if cancelling:
            phase = "closed" if not present_kinds and completed_cancellation(instance) else "retiring"
        elif retiring:
            phase = "retiring" if present_vm else "closed"
        elif uncertain:
            phase = "uncertain"
        elif phase == "active" and not firewall_ready:
            phase = "provisioning"
        # Reserve a full declared VM rate for any incomplete provisioning intent,
        # even when the VM receipt was lost or only free resources exist so far.
        charge = Decimal(instance.plan["quote"]["compute_hourly_usd"]) if phase not in {"unprovisioned", "closed"} else Decimal(0)
        result.append(
            {
                "worker_id": instance.config["id"],
                "plan_id": instance.identity,
                "phase": phase,
                "resources": instance.config["resources"],
                "image_ids": instance.config["image_ids"],
                "catalog": instance.images,
                "hourly_usd": str(charge),
                "provision_hourly_usd": instance.plan["quote"]["compute_hourly_usd"],
                "account_identity": current["account_identity"],
            }
        )
    return result


def compatible(job, worker):
    return job["image_id"] in worker["image_ids"] and fits(job["resources"], worker["resources"])


def coverage(jobs, workers):
    """Maximum matching, not aggregate CPU: each worker accepts only one job."""
    owners = {}

    def assign(index, seen):
        for worker in workers:
            if worker["id"] in seen or not compatible(jobs[index], worker):
                continue
            seen.add(worker["id"])
            if worker["id"] not in owners or assign(owners[worker["id"]], seen):
                owners[worker["id"]] = index
                return True
        return False

    for index in range(len(jobs)):
        assign(index, set())
    return len(owners)


def resume_needed(fleet, snapshot, owned, worker_id, *, now=None):
    """Revalidate one existing reservation, not authority for fresh fleet growth.

    Unfinished peers reserve money but supply no usable capacity. They must not
    deadlock recovery of an already-confirmed slot. The caller independently
    enforces the durable aggregate budget before any provider creation.
    """
    now = int(time.time()) if now is None else now
    catalog = validate_snapshot(snapshot, fleet["organization_id"], now)
    slots = {slot["worker_id"]: slot for slot in owned}
    workers = {worker["id"]: worker for worker in snapshot["workers"]}
    target, worker = slots.get(worker_id), workers.get(worker_id)
    busy = {job["worker_id"] for job in snapshot["jobs"] if job["status"] != "queued"}

    def matches(slot, record):
        return (
            record is not None
            and all(canonical(record[key]) == canonical(slot[key]) for key in ("image_ids", "resources"))
            and all(catalog.get(image["id"]) == image for image in slot["catalog"])
        )

    if (
        target is None
        or target["phase"] not in {"uncertain", "provisioning"}
        or not matches(target, worker)
        or worker["last_seen_at"] is not None
        or worker["draining"]
        or worker["revoked_at"] is not None
        or worker["retired_at"] is not None
        or worker_id in busy
    ):
        return False
    usable = [
        record
        for record in workers.values()
        if not record["draining"]
        and record["revoked_at"] is None
        and record["retired_at"] is None
        and record["last_seen_at"] is not None
        and now - record["last_seen_at"] <= FRESH_SECONDS
        and (record["id"] not in slots or slots[record["id"]]["phase"] == "active" and matches(slots[record["id"]], record))
    ]
    if sum(record["id"] in slots for record in usable) < fleet["minimum_workers"]:
        return True
    demand = [job for job in snapshot["jobs"] if job["status"] == "queued" and job["runnable"]]
    free_slots = max(0, snapshot["max_active_jobs"] - sum(job["status"] != "queued" for job in snapshot["jobs"]))
    ready = [record for record in usable if record["id"] not in busy]
    profile = {"id": worker_id, "resources": target["resources"], "image_ids": target["image_ids"]}
    return min(free_slots, coverage(demand, [*ready, profile])) > min(free_slots, coverage(demand, ready))


def decide(fleet, snapshot, owned, *, now=None):
    now = int(time.time()) if now is None else now
    catalog = validate_snapshot(snapshot, fleet["organization_id"], now)
    ceiling = dollars(fleet["maximum_hourly_usd"])
    workers = {worker["id"]: worker for worker in snapshot["workers"]}
    busy = {job["worker_id"] for job in snapshot["jobs"] if job["status"] != "queued"}
    demand = [job for job in snapshot["jobs"] if job["status"] == "queued" and job["runnable"]]
    free_slots = max(0, snapshot["max_active_jobs"] - len([job for job in snapshot["jobs"] if job["status"] != "queued"]))
    ready = [
        worker
        for worker in workers.values()
        if worker["id"] not in busy
        and not worker["draining"]
        and worker["revoked_at"] is None
        and worker["last_seen_at"] is not None
        and now - worker["last_seen_at"] <= FRESH_SECONDS
    ]
    owned_ids = {slot["worker_id"] for slot in owned}
    active_ids = {slot["worker_id"] for slot in owned if slot["phase"] == "active"}
    ready = [worker for worker in ready if worker["id"] not in owned_ids or worker["id"] in active_ids]
    ready_ids = {worker["id"] for worker in ready}
    held, candidates, active = [], [], []
    for slot in owned:
        worker = workers.get(slot["worker_id"])
        if slot["phase"] == "closed":
            continue
        if (
            worker is None
            or any(canonical(worker[key]) != canonical(slot[key]) for key in ("image_ids", "resources"))
            or any(catalog.get(image["id"]) != image for image in slot["catalog"])
        ):
            held.append(slot["worker_id"])
        elif slot["phase"] == "unprovisioned":
            if worker["last_seen_at"] is None and not worker["draining"] and worker["revoked_at"] is None:
                candidates.append(slot)
            else:
                held.append(slot["worker_id"])
        elif slot["phase"] == "active":
            active.append(slot)
            if (
                worker["revoked_at"] is not None
                or worker["draining"]
                or worker["last_seen_at"] is None
                or now - worker["last_seen_at"] > FRESH_SECONDS
            ):
                held.append(slot["worker_id"])
        else:
            held.append(slot["worker_id"])
    occupied = sum(slot["phase"] not in {"unprovisioned", "closed"} for slot in owned)
    cost = sum((Decimal(slot["hourly_usd"]) for slot in owned), Decimal(0))
    baseline = min(free_slots, coverage(demand, ready))
    target = min(free_slots, len(demand))
    actions = []
    original_cost = cost
    while not held and occupied < fleet["maximum_workers"]:
        options = []
        for slot in candidates:
            price = Decimal(slot["provision_hourly_usd"])
            if cost + price > ceiling:
                continue
            profile = {"id": slot["worker_id"], "resources": slot["resources"], "image_ids": slot["image_ids"]}
            gain = min(target, coverage(demand, [*ready, profile])) - baseline
            if gain > 0 or occupied < fleet["minimum_workers"]:
                options.append((-gain, price, slot["worker_id"], slot, profile))
        if not options:
            break
        _, price, _, slot, profile = min(options, key=lambda item: item[:3])
        actions.append({"action": "provision", "worker_id": slot["worker_id"], "plan_id": slot["plan_id"]})
        candidates.remove(slot)
        ready.append(profile)
        occupied += 1
        cost += price
        baseline = min(target, coverage(demand, ready))
    if not held and not actions and not snapshot.get("queued_remaining", 0):
        for slot in sorted(active, key=lambda item: (workers[item["worker_id"]]["idle_since_at"], item["worker_id"])):
            worker = workers[slot["worker_id"]]
            if (
                occupied <= fleet["minimum_workers"]
                or worker["id"] not in ready_ids
                or now - worker["idle_since_at"] < fleet["idle_seconds"]
            ):
                continue
            remaining = [entry for entry in ready if entry["id"] != worker["id"]]
            if min(target, coverage(demand, remaining)) < baseline:
                continue
            # Preserve the last compatible machine for every queued requirement,
            # even when the current global concurrency ceiling is already full.
            if any(
                any(compatible(job, entry) for entry in ready) and not any(compatible(job, entry) for entry in remaining) for job in demand
            ):
                continue
            actions.append(
                {"action": "retire", "worker_id": slot["worker_id"], "plan_id": slot["plan_id"], "requires_fresh_host_checks": True}
            )
            ready = remaining
            occupied -= 1
            cost -= Decimal(slot["hourly_usd"])
    decision = {
        "format": 1,
        "organization_id": fleet["organization_id"],
        "server_url": fleet["server_url"],
        "observed_at": snapshot["observed_at"],
        "expires_at": snapshot["observed_at"] + SNAPSHOT_SECONDS,
        "creates_resources": False,
        "destroys_resources": False,
        "mode": "advisory",
        "fleet_sha256": hashlib.sha256(canonical(fleet)).hexdigest(),
        "snapshot_sha256": hashlib.sha256(canonical(snapshot)).hexdigest(),
        "inventory_sha256": hashlib.sha256(canonical(owned)).hexdigest(),
        "actions": actions,
        "reconcile_workers": sorted(held),
        "maximum_workers": fleet["maximum_workers"],
        "minimum_workers": fleet["minimum_workers"],
        "idle_seconds": fleet["idle_seconds"],
        "maximum_hourly_usd": fleet["maximum_hourly_usd"],
        "reserved_owned_hourly_usd": str(original_cost),
        "projected_owned_hourly_usd": str(cost),
        "current_ceiling_exceeded": original_cost > ceiling
        or sum(slot["phase"] not in {"unprovisioned", "closed"} for slot in owned) > fleet["maximum_workers"],
        "runnable_queued_jobs": len(demand),
        "available_concurrency_slots": free_slots,
        "covered_concurrency_slots": baseline,
        "policy_blocked_jobs": [job["id"] for job in snapshot["jobs"] if job["status"] == "queued" and not job["runnable"]],
        "uncovered_demand": baseline < target or bool(snapshot.get("queued_remaining", 0)),
        "minimum_unmet": occupied < fleet["minimum_workers"],
        "excluded_costs": ["unmanaged workers", "chat server", "tax", "bandwidth overages", "backups", "storage", "other services"],
    }
    decision["decision_id"] = hashlib.sha256(canonical(decision)).hexdigest()
    return decision
