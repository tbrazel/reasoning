"""Actual private-journal fleet amendments with synthetic provider HTTP only."""

import copy
import json
import time
from pathlib import Path

import httpx
import pytest
from test_public_compute_fleet import managed_fleet as managed_fleet
from test_public_compute_fleet import test_only_fully_retired_and_freshly_absent_resources_release_budget as retire_and_reconcile
from test_public_compute_fleet_recovery import cancellation as cancellation
from test_public_compute_scaling import cloud_fleet as cloud_fleet
from test_public_deployment import bundle as bundle
from test_public_deployment import payload as payload
from test_public_worker_cloud_retirement import cloud_retirement as cloud_retirement
from test_public_worker_deployment import IMAGE, REGISTRATION, TOKEN
from test_public_worker_deployment import bootstrap as bootstrap
from test_public_worker_host_retirement import managed as managed
from test_public_worker_provisioning import worker_cloud as worker_cloud

from reasoning_ledger.deployment import cli, fleet, scaling
from reasoning_ledger.deployment import fleet_amend as module
from reasoning_ledger.deployment.host import InstallError
from reasoning_ledger.deployment.private import locked_directory, read_document, read_private, write_document
from reasoning_ledger.deployment.provision import save_plan
from reasoning_ledger.deployment.worker_cancel import CANCELLATION
from reasoning_ledger.deployment.worker_provision import enrollment_binding, validate_worker_plan, worker_inputs
from reasoning_ledger.server.compute_worker import WorkerClient, write_state


def proposal(fixture, *, replace=False, identity="c", filename="next-fleet.json", **limits):
    _, _, _, _, cloud, _, _, source = fixture
    configuration = read_document(source)
    registration = {**REGISTRATION, "id": identity * 32, "name": "Replacement " + identity, "token": "fixture-token-" + identity * 40}
    config, images = worker_inputs(configuration["server_url"], registration, [IMAGE])
    plan = copy.deepcopy(cloud[5]["plan"])
    plan["enrollment"] = enrollment_binding(config, images)
    saved = save_plan(source.parent / (identity + "-plan.json"), plan, validator=validate_worker_plan)
    path = source.parent / (identity + "-registration.json")
    write_state(path, registration)
    entry = {
        "plan": saved["plan_file"],
        "registration": str(path),
        "catalog": configuration["workers"][0]["catalog"],
        "state_dir": str(source.parent / (identity + "-ownership")),
    }
    configuration["workers"] = [entry] if replace else [*configuration["workers"], entry]
    configuration.update(limits)
    target = source.parent / filename
    write_document(target, configuration, new=True)
    return target, registration


def change(controller, target):
    return module.amend(controller, target, confirm_fleet=controller.fleet_hash, confirm_next_fleet=fleet.digest(read_document(target)))


def test_append_keeps_original_reserved_identity_and_bindings_without_cloud_writes(managed_fleet):
    coordinator, _, execute, _, cloud, directory, _, source = managed_fleet
    execute()
    original = read_document(directory / fleet.BUDGET)
    marker = read_private(cloud[7] / fleet.BINDING)
    target, registration = proposal(managed_fleet, minimum_workers=2)
    writes = len(cloud[0].writes)
    result = change(coordinator(), target)
    assert result["phase"] == "amended" and not result["creates_resources"] and not result["destroys_resources"]
    current = read_document(directory / fleet.BUDGET)
    assert current["reservations"][REGISTRATION["id"]] == original["reservations"][REGISTRATION["id"]]
    assert current["history"][-1]["scope"] == original["scope"]
    assert current["origins"][REGISTRATION["id"]] == original["scope"]["fleet_sha256"]
    assert current["origins"][registration["id"]] == result["fleet_sha256"]
    assert current["reservations"][registration["id"]]["status"] == "unreserved"
    assert read_private(cloud[7] / fleet.BINDING) == marker and len(cloud[0].writes) == writes
    following = fleet.FleetCoordinator(cloud[0].client, target, directory)
    assert following.reconcile()["reserved_hourly_usd"] == "0.035"
    with pytest.raises(InstallError, match="superseded"):
        coordinator().reconcile()
    assert read_document(source)["workers"] != read_document(target)["workers"]
    assert TOKEN not in json.dumps(result) + json.dumps(current) and cloud[0].token not in json.dumps(current)
    assert change(coordinator(), target) == result


def test_preserved_pending_paid_receipt_resumes_under_new_fleet_without_duplicate_creation(managed_fleet):
    coordinator, _, execute, snapshot, cloud, directory, _, _ = managed_fleet
    cloud[0].fail["droplet"] = "lost_after"
    with pytest.raises(InstallError):
        execute()
    row = read_document(directory / fleet.BUDGET)["reservations"][REGISTRATION["id"]]
    target, _ = proposal(managed_fleet)
    change(coordinator(), target)
    following = fleet.FleetCoordinator(
        cloud[0].client,
        target,
        directory,
        snapshot_reader=lambda *_: {**snapshot, "observed_at": int(time.time())},
        identity_client=cloud[1].client,
    )
    result = following.apply(
        TOKEN, confirm_worker=REGISTRATION["id"], confirm_plan=cloud[4]["plan_id"], resume_reservation=row["reservation_id"]
    )
    assert result["reservation_id"] == row["reservation_id"] and len(cloud[0].writes) == 3
    assert result["phase"] == "ssh_verification_required"


@pytest.mark.parametrize("change_kind", ["dollars", "count", "organization", "origin", "paths", "quote", "account"])
def test_invalid_amendment_cannot_reset_or_hide_original_reservation(managed_fleet, change_kind):
    coordinator, _, execute, _, cloud, directory, _, source = managed_fleet
    cloud[0].fail["tag"] = "lost_before"
    with pytest.raises(InstallError):
        execute()
    original = read_private(directory / fleet.BUDGET)
    target, _ = proposal(managed_fleet)
    definition = read_document(target)
    if change_kind == "dollars":
        definition["maximum_hourly_usd"] = "0.034"
    elif change_kind == "count":
        definition.update(minimum_workers=0, maximum_workers=0)
    elif change_kind == "organization":
        definition["organization_id"] = "f" * 32
    elif change_kind == "origin":
        definition["server_url"] = "https://other.example.org"
    elif change_kind == "paths":
        definition["workers"][0]["state_dir"] = str(source.parent / "moved-ownership")
    else:
        index = 0 if change_kind == "quote" else 1
        document = read_document(Path(definition["workers"][index]["plan"]))
        if change_kind == "quote":
            document["plan"]["quote"]["compute_hourly_usd"] = "0.04"
            document["plan"]["quote"]["estimated_730_hours_usd"] = "29.20"
        else:
            document["plan"]["account_identity"] = "user:" + "f" * 40
        new_plan = save_plan(source.parent / "changed-plan.json", document["plan"], validator=validate_worker_plan)
        definition["workers"][index]["plan"] = new_plan["plan_file"]
    write_document(target, definition)
    with pytest.raises(InstallError):
        change(coordinator(), target)
    assert read_private(directory / fleet.BUDGET) == original
    assert not (directory / fleet.AMENDMENT).exists() and len(cloud[0].writes) == 1


@pytest.mark.parametrize("phase", ["unreserved", "reserved", "paid-pending"])
def test_unresolved_or_unreserved_slot_cannot_be_removed(managed_fleet, phase):
    coordinator, _, execute, _, cloud, directory, _, _ = managed_fleet
    if phase == "unreserved":
        coordinator().reconcile()
    else:
        cloud[0].fail["tag" if phase == "reserved" else "droplet"] = "lost_before"
        with pytest.raises(InstallError):
            execute()
    original = read_private(directory / fleet.BUDGET)
    target, _ = proposal(managed_fleet, replace=True)
    with pytest.raises(InstallError, match="terminal"):
        change(coordinator(), target)
    assert read_private(directory / fleet.BUDGET) == original and not (directory / fleet.AMENDMENT).exists()


def test_cancelled_slot_is_archived_and_new_worker_gets_fresh_budgeted_creation(cancellation, managed_fleet):
    reserve, cancel, coordinator, cloud, directory, _, _, _, _, _ = cancellation
    reservation = reserve("empty")
    assert cancel(reservation)["phase"] == "cancelled"
    source_cancel = read_private(cloud[7] / CANCELLATION)
    target, registration = proposal(managed_fleet, replace=True)
    result = change(coordinator(), target)
    current = read_document(directory / fleet.BUDGET)
    assert current["history"][-1]["reservations"][REGISTRATION["id"]]["status"] == "cancelled"
    assert current["history"][-1]["reservations"][REGISTRATION["id"]]["reservation_id"] == reservation
    assert REGISTRATION["id"] not in current["reservations"] and read_private(cloud[7] / CANCELLATION) == source_cancel
    snapshot = copy.deepcopy(managed_fleet[3])
    snapshot["workers"].append({**snapshot["workers"][0], **{key: value for key, value in registration.items() if key != "token"}})
    following = fleet.FleetCoordinator(
        cloud[0].client, target, directory, snapshot_reader=lambda *_: {**snapshot, "observed_at": int(time.time())}
    )
    own = WorkerClient(
        following.profiles[0].config, transport=httpx.MockTransport(lambda _: httpx.Response(200, json=snapshot["workers"][-1]))
    )
    following.profiles[0].identity_client = own
    try:
        decision = scaling.decide(
            following.fleet, {**snapshot, "observed_at": int(time.time())}, scaling.inventory(cloud[0].client, following.profiles)
        )
        created = following.apply(
            TOKEN,
            confirm_worker=registration["id"],
            confirm_plan=following.profiles[0].identity,
            advice=decision,
            confirm_decision=decision["decision_id"],
        )
        assert created["phase"] == "ssh_verification_required" and created["fleet_sha256"] == result["fleet_sha256"]
        assert following.reconcile()["reserved_workers"] == 1
        assert created["reservation_id"] != reservation
    finally:
        own.close()


def test_cancelled_retained_slot_uses_original_birth_namespace_in_inventory(cancellation, managed_fleet):
    reserve, cancel, coordinator, cloud, directory, _, _, _, _, _ = cancellation
    reservation = reserve("empty")
    cancel(reservation)
    target, _ = proposal(managed_fleet)
    change(coordinator(), target)
    following = fleet.FleetCoordinator(cloud[0].client, target, directory)
    assert following.reconcile()["reservations"][REGISTRATION["id"]]["status"] == "cancelled"
    _, profiles = scaling.load_fleet(target)
    assert scaling.inventory(cloud[0].client, profiles)[0]["phase"] == "closed"
    assert (
        following.cancel(confirm_worker=REGISTRATION["id"], confirm_plan=cloud[4]["plan_id"], confirm_reservation=reservation)["phase"]
        == "cancelled"
    )
    (cloud[7] / fleet.BINDING).unlink()
    assert (
        fleet.FleetCoordinator(cloud[0].client, target, directory).reconcile()["reservations"][REGISTRATION["id"]]["status"] == "cancelled"
    )
    assert read_document(cloud[7] / fleet.BINDING)["fleet_sha256"] == read_document(cloud[7] / CANCELLATION)["intent"]["fleet_sha256"]


@pytest.mark.parametrize("failure", ["new-binding", "budget-before", "budget-after", "receipt"])
def test_partial_amendment_blocks_both_fleets_and_exact_retry_finishes_once(managed_fleet, monkeypatch, failure):
    coordinator, _, execute, _, cloud, directory, _, _ = managed_fleet
    execute()
    target, registration = proposal(managed_fleet)
    original = module.write_document
    triggered = []

    def fail(path, value, **kwargs):
        selected = (
            failure == "new-binding"
            and path.name == fleet.BINDING
            or failure in {"budget-before", "budget-after"}
            and path.name == fleet.BUDGET
            or failure == "receipt"
            and path.name == fleet.AMENDMENT
            and value["phase"] == "applied"
        )
        if selected and not triggered:
            triggered.append(True)
            if failure == "budget-after":
                original(path, value, **kwargs)
            raise OSError("fixture interrupted local write")
        return original(path, value, **kwargs)

    monkeypatch.setattr(module, "write_document", fail)
    with pytest.raises(OSError):
        change(coordinator(), target)
    intent = module.read_amendment(directory / fleet.AMENDMENT)
    assert intent["phase"] == "prepared"
    for definition in (managed_fleet[7], target):
        with pytest.raises(InstallError, match="pending"):
            fleet.FleetCoordinator(cloud[0].client, definition, directory).reconcile()
    writes = len(cloud[0].writes)
    monkeypatch.setattr(module, "write_document", original)
    result = change(coordinator(), target)
    assert result["transition_id"] == intent["transition_id"] and len(cloud[0].writes) == writes
    current = read_document(directory / fleet.BUDGET)
    assert current["reservations"][registration["id"]]["status"] == "unreserved"
    assert change(coordinator(), target) == result and len(current["history"]) == 1


def test_same_coordinator_and_worker_locks_exclude_amendment(managed_fleet):
    coordinator, _, _, _, cloud, directory, _, _ = managed_fleet
    coordinator().reconcile()
    target, _ = proposal(managed_fleet)
    for lock in (directory, cloud[7]):
        with locked_directory(lock), pytest.raises(InstallError, match="another process"):
            change(coordinator(), target)
    assert not cloud[0].writes and not (directory / fleet.AMENDMENT).exists()


def test_second_generation_preserves_all_origins_and_rejects_history_edits(managed_fleet):
    coordinator, _, execute, _, cloud, directory, _, _ = managed_fleet
    execute()
    target, _ = proposal(managed_fleet)
    change(coordinator(), target)
    third = read_document(target)
    third["maximum_hourly_usd"] = "0.11"
    third_path = target.parent / "third-fleet.json"
    write_document(third_path, third, new=True)
    next_owner = fleet.FleetCoordinator(cloud[0].client, target, directory)
    change(next_owner, third_path)
    following = fleet.FleetCoordinator(cloud[0].client, third_path, directory)
    assert following.reconcile()["reserved_hourly_usd"] == "0.035"
    value = read_document(directory / fleet.BUDGET)
    assert len(value["history"]) == 2 and len(set(value["origins"].values())) == 2
    value["history"].pop(0)
    write_document(directory / fleet.BUDGET, value)
    with pytest.raises(InstallError):
        following.reconcile()


def test_cli_bad_confirmation_fails_before_provider_or_local_mutation(managed_fleet, monkeypatch, capsys):
    _, _, _, _, _, directory, _, source = managed_fleet
    target, _ = proposal(managed_fleet)
    monkeypatch.setattr(cli, "DigitalOcean", lambda _: pytest.fail("bad confirmation must precede provider construction"))
    with pytest.raises(SystemExit) as failure:
        cli.main(
            [
                "do-worker-fleet-amend",
                "--fleet",
                str(source),
                "--next-fleet",
                str(target),
                "--fleet-state-dir",
                str(directory),
                "--confirm-fleet",
                "f" * 64,
                "--confirm-next-fleet",
                fleet.digest(read_document(target)),
                "--token-file",
                str(source.parent / "absent-token"),
            ]
        )
    assert failure.value.code == 2 and "confirm exact" in capsys.readouterr().err and not directory.exists()


def test_verified_paid_retirement_can_leave_active_fleet_without_resetting_history(cloud_retirement):
    retire_and_reconcile(cloud_retirement, False)
    cloud = cloud_retirement[4]
    source = cloud[7].parent / "fleet.json"
    directory = source.parent / "fleet-state"
    definition = read_document(source)
    definition["workers"] = []
    target = source.parent / "empty-fleet.json"
    write_document(target, definition, new=True)
    owner = fleet.FleetCoordinator(cloud[0].client, source, directory)
    original = read_document(directory / fleet.BUDGET)
    deletions = len(cloud_retirement[2].deletions)
    change(owner, target)
    state = read_document(directory / fleet.BUDGET)
    assert state["history"][-1]["reservations"] == original["reservations"]
    assert state["history"][-1]["reservations"][REGISTRATION["id"]]["status"] == "closed"
    assert state["reservations"] == {} and len(cloud_retirement[2].deletions) == deletions
    assert fleet.FleetCoordinator(cloud[0].client, target, directory).reconcile()["reserved_workers"] == 0
    (directory / fleet.BUDGET).unlink()
    with pytest.raises(InstallError, match="amended fleet budget is missing"):
        fleet.FleetCoordinator(cloud[0].client, target, directory).reconcile()


def test_reserved_unpaid_worker_cancellation_after_amendment_uses_original_namespace(cancellation, managed_fleet):
    reserve, _, coordinator, cloud, directory, _, _, _, _, _ = cancellation
    reservation = reserve("empty")
    target, _ = proposal(managed_fleet)
    origin = coordinator().fleet_hash
    change(coordinator(), target)
    following = fleet.FleetCoordinator(cloud[0].client, target, directory)
    assert (
        following.cancel(confirm_worker=REGISTRATION["id"], confirm_plan=cloud[4]["plan_id"], confirm_reservation=reservation)["phase"]
        == "cancelled"
    )
    assert read_document(cloud[7] / CANCELLATION)["intent"]["fleet_sha256"] == origin


@pytest.mark.parametrize("tamper", ["budget", "target", "intent", "account", "binding", "other-target"])
def test_pending_amendment_refuses_changed_authoritative_inputs_without_overwrite(managed_fleet, monkeypatch, tamper):
    coordinator, _, execute, _, cloud, directory, _, source = managed_fleet
    execute()
    target, _ = proposal(managed_fleet)
    original = module.write_document

    def stop(path, value, **kwargs):
        if path.name == fleet.BINDING:
            raise OSError("fixture lost local binding write")
        return original(path, value, **kwargs)

    monkeypatch.setattr(module, "write_document", stop)
    with pytest.raises(OSError):
        change(coordinator(), target)
    monkeypatch.setattr(module, "write_document", original)
    saved = read_document(directory / fleet.BUDGET)
    if tamper == "budget":
        saved["reservations"][REGISTRATION["id"]]["reserved_at"] += 1
        write_document(directory / fleet.BUDGET, saved)
    elif tamper in {"target", "other-target"}:
        specification = read_document(target)
        specification["idle_seconds"] += 1
        if tamper == "other-target":
            other = source.parent / "other-fleet.json"
            write_document(other, specification, new=True)
            target = other
        else:
            write_document(target, specification)
    elif tamper == "intent":
        journal = read_document(directory / fleet.AMENDMENT)
        journal["next_state"]["origins"][REGISTRATION["id"]] = "f" * 64
        write_document(directory / fleet.AMENDMENT, journal)
    elif tamper == "account":
        cloud[0].account["uuid"] = "f" * 40
    else:
        marker = read_document(cloud[7] / fleet.BINDING)
        marker["budget_dir"] = str(source.parent / "other-coordinator")
        write_document(cloud[7] / fleet.BINDING, marker)
    before = read_private(directory / fleet.BUDGET)
    writes = len(cloud[0].writes)
    with pytest.raises(InstallError):
        change(coordinator(), target)
    assert read_private(directory / fleet.BUDGET) == before and len(cloud[0].writes) == writes


def test_archived_worker_and_ownership_paths_cannot_be_recycled(cancellation, managed_fleet):
    reserve, cancel, coordinator, cloud, directory, _, _, _, _, _ = cancellation
    cancel(reserve("empty"))
    target, _ = proposal(managed_fleet, replace=True)
    change(coordinator(), target)
    definition = read_document(target)
    definition["workers"].append(read_document(managed_fleet[7])["workers"][0])
    recycled = target.parent / "recycled-fleet.json"
    write_document(recycled, definition, new=True)
    before = read_private(directory / fleet.BUDGET)
    with pytest.raises(InstallError, match="historical identities"):
        change(fleet.FleetCoordinator(cloud[0].client, target, directory), recycled)
    assert read_private(directory / fleet.BUDGET) == before


def test_cli_amendment_prints_only_metadata_and_closes_provider(managed_fleet, monkeypatch, capsys):
    coordinator, _, _, _, cloud, directory, _, source = managed_fleet
    coordinator().reconcile()
    target, _ = proposal(managed_fleet)
    token = source.parent / "provider-token"
    write_state(token, {"fixture": True})
    token.write_text(cloud[0].token)
    monkeypatch.setattr(cli, "DigitalOcean", lambda _: cloud[0].client)
    assert (
        cli.main(
            [
                "do-worker-fleet-amend",
                "--fleet",
                str(source),
                "--next-fleet",
                str(target),
                "--fleet-state-dir",
                str(directory),
                "--confirm-fleet",
                fleet.digest(read_document(source)),
                "--confirm-next-fleet",
                fleet.digest(read_document(target)),
                "--token-file",
                str(token),
            ]
        )
        == 0
    )
    output = capsys.readouterr()
    assert json.loads(output.out)["phase"] == "amended" and not output.err
    assert TOKEN not in output.out and cloud[0].token not in output.out
    assert cloud[0].client.client.is_closed and not cloud[0].writes


@pytest.mark.parametrize("erase", ["budget", "history", "receipt"])
def test_completed_amendment_cannot_forget_history_even_when_current_rows_unreserved(managed_fleet, erase):
    coordinator, _, _, _, cloud, directory, _, _ = managed_fleet
    coordinator().reconcile()
    target, _ = proposal(managed_fleet)
    change(coordinator(), target)
    if erase == "receipt":
        (directory / fleet.AMENDMENT).unlink()
    elif erase == "budget":
        (directory / fleet.BUDGET).unlink()
    else:
        value = read_document(directory / fleet.BUDGET)
        value.pop("history")
        value.pop("origins")
        write_document(directory / fleet.BUDGET, value)
    with pytest.raises(InstallError):
        fleet.FleetCoordinator(cloud[0].client, target, directory).reconcile()
    assert not cloud[0].writes
