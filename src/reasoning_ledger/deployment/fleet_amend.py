"""Confirmed, resumable fleet generations without cloud creation or deletion."""

from __future__ import annotations

import copy
from contextlib import ExitStack
from pathlib import Path

from . import scaling
from .fleet import AMENDMENT, BINDING, BUDGET, FleetCoordinator, digest, hash_id, validate_lineage
from .host import InstallError, canonical
from .private import MAX_STATE, locked_directory, read_document, write_document
from .worker_cancel import WorkerCancellation


def read_amendment(path):
    value = read_document(path)
    try:
        if (
            set(value)
            != {
                "format",
                "old_fleet_sha256",
                "new_fleet_sha256",
                "next_fleet_file",
                "budget_before_sha256",
                "next_state",
                "phase",
                "transition_id",
            }
            or type(value["format"]) is not int
            or value["format"] != 1
            or value["phase"] not in {"prepared", "applied"}
            or any(not hash_id(value[key]) for key in ("old_fleet_sha256", "new_fleet_sha256", "budget_before_sha256", "transition_id"))
            or value["old_fleet_sha256"] == value["new_fleet_sha256"]
            or not isinstance(value["next_fleet_file"], str)
            or not Path(value["next_fleet_file"]).is_absolute()
            or str(Path(value["next_fleet_file"])) != value["next_fleet_file"]
            or ".." in Path(value["next_fleet_file"]).parts
            or value["transition_id"] != digest({key: item for key, item in value.items() if key not in {"phase", "transition_id"}})
            or set(value["next_state"]) != {"scope", "origins", "reservations", "history"}
            or value["next_state"]["scope"]["fleet_sha256"] != value["new_fleet_sha256"]
            or value["next_state"]["history"][-1]["scope"]["fleet_sha256"] != value["old_fleet_sha256"]
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise InstallError("invalid original fleet amendment; preserve the coordinator and every ownership journal") from exc
    return value


def _frame(state):
    return {
        "scope": copy.deepcopy(state["scope"]),
        "reservations": copy.deepcopy(state["reservations"]),
        "origins": copy.deepcopy(state.get("origins", dict.fromkeys(state["reservations"], state["scope"]["fleet_sha256"]))),
    }


def _fresh(profile):
    journal = Path(profile.fleet_paths["state_dir"]) / "digitalocean.json"
    if journal.exists() or journal.is_symlink():
        profile.state = read_document(journal)
        profile._validate_state()
    else:
        write_document(journal, profile.state, new=True)


def _terminal(owner, profiles, state, retained):
    """No current slot is forgotten on incomplete or lost cleanup evidence."""
    for profile in profiles:
        worker = profile.config["id"]
        if worker in retained:
            continue
        row = state["reservations"][worker]
        if row["status"] == "closed":
            absent = owner._closed(profile)
        elif row["status"] == "cancelled":
            absent = WorkerCancellation(owner, profile, row).verified_absent()
        else:
            absent = False
        if not absent:
            raise InstallError(
                "remove only a terminal cancelled/retired slot with fresh exact owned-resource absence; retain every unresolved slot"
            )


def amend(owner, next_fleet_path, *, confirm_fleet, confirm_next_fleet):
    """Hold the original coordinator and every old/new slot lock through activation."""
    next_fleet_path = Path(next_fleet_path).absolute()
    next_owner = FleetCoordinator(owner.provider, next_fleet_path, owner.directory)
    if (
        not hash_id(confirm_fleet)
        or not hash_id(confirm_next_fleet)
        or confirm_fleet != owner.fleet_hash
        or confirm_next_fleet != next_owner.fleet_hash
        or owner.fleet_hash == next_owner.fleet_hash
        or owner.fleet["server_url"] != next_owner.fleet["server_url"]
        or owner.fleet["organization_id"] != next_owner.fleet["organization_id"]
    ):
        raise InstallError("confirm exact distinct original and next fleet hashes in the same organization and HTTPS origin")
    path = owner.directory / AMENDMENT
    with locked_directory(owner.directory):
        owner.locked = True
        try:
            record = read_amendment(path) if path.exists() or path.is_symlink() else None
            matches = (
                record is not None
                and record["old_fleet_sha256"] == owner.fleet_hash
                and record["new_fleet_sha256"] == next_owner.fleet_hash
                and record["next_fleet_file"] == str(next_fleet_path)
            )
            if record is not None and record["phase"] == "prepared" and not matches:
                raise InstallError("another fleet amendment is pending; resume the exact original fleet pair and target path")
            if matches and record["phase"] == "applied":
                next_owner._open()
                return _result(record, owner.directory)
            if not matches:
                if not (owner.directory / BUDGET).exists():
                    raise InstallError("amend an existing coordinator; reconcile its original fleet first")
                owner._open()
            with ExitStack() as locks:
                directories = sorted({Path(p.fleet_paths["state_dir"]) for p in [*owner.profiles, *next_owner.profiles]}, key=str)
                for directory in directories:
                    locks.enter_context(locked_directory(directory))
                for profile in owner.profiles:
                    _fresh(profile)
                if not matches:
                    owner._observe()
                    frame = _frame(owner.state)
                    kept = set(next_owner.scope["slots"])
                    _terminal(owner, owner.profiles, owner.state, kept)
                    state = {
                        "scope": next_owner.scope,
                        "reservations": {},
                        "origins": {},
                        "history": [*copy.deepcopy(owner.state.get("history", [])), frame],
                    }
                    for worker in kept:
                        state["reservations"][worker] = copy.deepcopy(
                            frame["reservations"].get(
                                worker, {"status": "unreserved", "reservation_id": None, "decision_id": None, "reserved_at": None}
                            )
                        )
                        state["origins"][worker] = frame["origins"].get(worker, next_owner.fleet_hash)
                    try:
                        validate_lineage(state)
                    except (ValueError, TypeError, KeyError) as exc:
                        raise InstallError(
                            "amendment changes original ownership/inputs, reuses historical identities, or drops reservations"
                        ) from exc
                    next_owner.state = state
                    next_owner._budget()
                    previous_workers = set(owner.scope["slots"])
                    for profile in next_owner.profiles:
                        _fresh(profile)
                        if profile.config["id"] in previous_workers:
                            owner._binding(profile)
                        else:
                            marker = Path(profile.fleet_paths["state_dir"]) / BINDING
                            if (
                                marker.exists()
                                or marker.is_symlink()
                                or any(item["status"] != "not_started" for item in profile.state["resources"].values())
                            ):
                                raise InstallError("new fleet slots must be unbound and have no original cloud creation intent")
                    scaling.inventory(owner.provider, next_owner.profiles)
                    record = {
                        "format": 1,
                        "old_fleet_sha256": owner.fleet_hash,
                        "new_fleet_sha256": next_owner.fleet_hash,
                        "next_fleet_file": str(next_fleet_path),
                        "budget_before_sha256": digest(owner.state),
                        "next_state": state,
                        "phase": "prepared",
                    }
                    record["transition_id"] = digest({key: value for key, value in record.items() if key != "phase"})
                    if len(canonical(record)) > MAX_STATE:
                        raise InstallError("fleet amendment history exceeds its protected journal allowance; preserve the current fleet")
                    write_document(path, record, new=not path.exists())
                return _finish(owner, next_owner, record)
        finally:
            owner.locked = False


def _finish(owner, next_owner, record):
    # Both sides refuse ordinary creation while this prepared intent exists.
    if canonical(record["next_state"]["scope"]) != canonical(next_owner.scope):
        raise InstallError("original amendment target inputs changed; no replacement is authorized")
    state = record["next_state"]
    try:
        validate_lineage(state)
    except (ValueError, KeyError, TypeError) as exc:
        raise InstallError("original amendment lineage changed; preserve all ownership") from exc
    accounts = [
        slot["account_identity"] for frame in [*state["history"], {"scope": state["scope"]}] for slot in frame["scope"]["slots"].values()
    ]
    if accounts and owner.provider.identity() != accounts[0]:
        raise InstallError("DigitalOcean account changed; preserve the exact original fleet amendment")
    before = read_document(owner.directory / BUDGET)
    if digest(before) not in {record["budget_before_sha256"], digest(state)}:
        raise InstallError("fleet budget changed after amendment intent; do not overwrite its reservations")
    owner.state = state["history"][-1]
    # The source frame's origins retain each worker's original reservation namespace.
    owner.state = {**owner.state, "history": state["history"][:-1]} if len(state["history"]) > 1 else owner.state
    for profile in owner.profiles:
        owner._binding(profile)
    _terminal(owner, owner.profiles, owner.state, set(next_owner.scope["slots"]))
    next_owner.state = state
    next_owner._budget()
    for profile in next_owner.profiles:
        _fresh(profile)
        marker = Path(profile.fleet_paths["state_dir"]) / BINDING
        if marker.exists() or marker.is_symlink():
            next_owner._binding(profile)
        else:
            if profile.config["id"] in owner.scope["slots"]:
                raise InstallError("original retained fleet binding is missing; restore it")
            if any(item["status"] != "not_started" for item in profile.state["resources"].values()):
                raise InstallError("unbound new slot acquired cloud intent; no migration is authorized")
            write_document(marker, next_owner._marker(profile), new=True)
    scaling.inventory(owner.provider, next_owner.profiles)
    write_document(owner.directory / BUDGET, state)
    record["phase"] = "applied"
    write_document(owner.directory / AMENDMENT, record)
    return _result(record, owner.directory)


def _result(record, directory):
    return {
        "phase": "amended",
        "old_fleet_sha256": record["old_fleet_sha256"],
        "fleet_sha256": record["new_fleet_sha256"],
        "transition_id": record["transition_id"],
        "fleet_file": record["next_fleet_file"],
        "fleet_state_dir": str(directory),
        "creates_resources": False,
        "destroys_resources": False,
        "worker_ids": sorted(record["next_state"]["reservations"]),
        "historical_generations": len(record["next_state"]["history"]),
    }
