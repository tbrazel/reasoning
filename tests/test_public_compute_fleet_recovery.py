"""Reserved setup recovery/cancellation fixtures, never live cloud mutation."""

import copy
import json
import time
from pathlib import Path

import httpx
import pytest
from test_public_compute_fleet import managed_fleet as managed_fleet
from test_public_compute_fleet import two_workers as two_workers
from test_public_compute_scaling import cloud_fleet as cloud_fleet
from test_public_compute_scaling import compute_fixture as compute_fixture
from test_public_compute_scaling import decision as decision
from test_public_worker_deployment import REGISTRATION, TOKEN
from test_public_worker_provisioning import apply as unmanaged_apply
from test_public_worker_provisioning import worker_cloud as worker_cloud

from reasoning_ledger.deployment import cli, fleet, scaling
from reasoning_ledger.deployment.digitalocean import DigitalOcean
from reasoning_ledger.deployment.host import InstallError
from reasoning_ledger.deployment.private import locked_directory, read_document, write_document
from reasoning_ledger.deployment.worker_cancel import CANCELLATION


def test_two_unfinished_reservations_do_not_deadlock_each_other(two_workers):
    coordinator, advice, snapshot, cloud, _, _ = two_workers
    cloud[0].firewall_status = "waiting"
    original = coordinator()
    for profile in original.profiles:
        assert profile.apply(Path(profile.fleet_paths["state_dir"]), profile.identity)["phase"] == "firewall_pending"
    report = coordinator().reconcile()
    assert report["reserved_workers"] == 2 and advice()["actions"] == []
    before = copy.deepcopy(report["reservations"])
    for firewall in cloud[0].firewalls:
        firewall["status"] = "succeeded"
    for profile in original.profiles:
        result = coordinator().apply(
            TOKEN,
            confirm_worker=profile.config["id"],
            confirm_plan=profile.identity,
            resume_reservation=before[profile.config["id"]]["reservation_id"],
        )
        assert result["phase"] == "ssh_verification_required"
        next(worker for worker in snapshot["workers"] if worker["id"] == profile.config["id"])["last_seen_at"] = int(time.time())
    assert coordinator().reconcile()["reservations"] == before
    assert len(cloud[0].writes) == 6 and len(cloud[0].droplets) == 2


@pytest.mark.parametrize("change", ["no-demand", "blocked", "oversized", "draining", "revoked", "polled", "busy", "catalog", "resources"])
def test_existing_reservation_recovery_still_requires_current_admissible_need(decision, change):
    configuration, snapshot, owned, now = decision
    owned[0].update(phase="provisioning", hourly_usd="0.035")
    worker = snapshot["workers"][0]
    if change == "no-demand":
        snapshot["jobs"] = []
    elif change == "blocked":
        snapshot["jobs"][0]["runnable"] = False
    elif change == "oversized":
        snapshot["jobs"][0]["resources"]["cpu"] += 1
    elif change == "draining":
        worker["draining"] = 1
    elif change == "revoked":
        worker["revoked_at"] = now
    elif change == "polled":
        worker["last_seen_at"] = now
    elif change == "busy":
        snapshot["jobs"][0].update(status="outcome_unknown", runnable=False, worker_id=worker["id"], lease_expires_at=0, deadline_at=0)
    elif change == "catalog":
        owned[0]["catalog"][0]["source_revision"] = "e" * 40
    else:
        worker["resources"]["cpu"] += 1
    before = copy.deepcopy(snapshot)
    assert not scaling.resume_needed(configuration, snapshot, owned, worker["id"], now=now)
    assert before == snapshot


def test_recovery_uses_only_ready_capacity_and_preserves_unknown_concurrency(decision):
    configuration, snapshot, owned, now = decision
    target = snapshot["workers"][0]
    owned[0].update(phase="uncertain", hourly_usd="0.035")
    peer = {**copy.deepcopy(target), "id": "d" * 32, "last_seen_at": now}
    snapshot["workers"].append(peer)
    # An external healthy worker covers demand; an unresolved owned VM does not.
    assert not scaling.resume_needed(configuration, snapshot, owned, target["id"], now=now)
    owned.append({**copy.deepcopy(owned[0]), "worker_id": peer["id"]})
    assert scaling.resume_needed(configuration, snapshot, owned, target["id"], now=now)
    snapshot["max_active_jobs"] = 1
    busy = {
        **copy.deepcopy(snapshot["jobs"][0]),
        "id": "f" * 32,
        "status": "outcome_unknown",
        "runnable": False,
        "worker_id": peer["id"],
        "lease_expires_at": 0,
        "deadline_at": 0,
    }
    snapshot["jobs"].append(busy)
    assert not scaling.resume_needed(configuration, snapshot, owned, target["id"], now=now)


def test_usable_owned_warm_minimum_does_not_count_unfinished_or_unmanaged_workers(decision):
    configuration, snapshot, owned, now = decision
    configuration["minimum_workers"] = 1
    snapshot["jobs"] = []
    target = snapshot["workers"][0]
    owned[0].update(phase="provisioning", hourly_usd="0.035")
    peer = {**copy.deepcopy(target), "id": "d" * 32, "last_seen_at": now}
    snapshot["workers"].append(peer)
    assert scaling.resume_needed(configuration, snapshot, owned, target["id"], now=now)
    owned.append({**copy.deepcopy(owned[0]), "worker_id": peer["id"], "phase": "active"})
    assert not scaling.resume_needed(configuration, snapshot, owned, target["id"], now=now)
    peer["created_at"] = now - 100
    peer["last_seen_at"] = now - scaling.FRESH_SECONDS - 1
    assert scaling.resume_needed(configuration, snapshot, owned, target["id"], now=now)


@pytest.fixture
def cancellation(managed_fleet):
    coordinator, _, execute, _, cloud, directory, _, _ = managed_fleet
    remote = cloud[0]
    original_transport = remote.client.client._transport
    failures, pending, denied, deletions = {}, set(), set(), []
    observations = {"before_delete": None}

    def handle(request):
        path = request.url.path.removeprefix("/v2/")
        if request.method == "GET" and path in denied:
            remote.requests.append(request)
            return httpx.Response(403, json={"message": remote.token})
        if request.method != "DELETE":
            return original_transport.handle_request(request)
        remote.requests.append(request)
        deletions.append(request)
        assert request.content == b"" and request.url.host == "api.digitalocean.com"
        assert request.headers["authorization"] == "Bearer " + remote.token
        collection, identifier = path.split("/")
        assert collection in {"firewalls", "tags"}, "never DELETE a VM or bulk/tag-filtered endpoint"
        kind = {"firewalls": "firewall", "tags": "tag"}[collection]
        state = read_document(cloud[7] / CANCELLATION)
        assert state["deletion"][kind] == "request_pending" and str(state["owned"][kind]) == identifier
        assert read_document(cloud[7] / "digitalocean.json")["resources"]["droplet"] == {"status": "not_started", "id": None}
        assert read_document(directory / fleet.BUDGET)["reservations"][REGISTRATION["id"]]["status"] == "reserved"
        if observations["before_delete"]:
            observations["before_delete"](kind)
        failure = failures.pop(kind, None)
        if failure == "rejected":
            return httpx.Response(403, json={"message": remote.token})
        if failure == "lost_before":
            raise httpx.ReadTimeout(remote.token, request=request)
        if kind not in pending:
            rows = getattr(remote, collection)
            key = "name" if kind == "tag" else "id"
            rows[:] = [row for row in rows if str(row[key]) != identifier]
        if failure == "lost_after":
            raise httpx.ReadTimeout(remote.token, request=request)
        return httpx.Response(204, content=remote.token if failure == "body" else b"")

    remote.client.close()
    remote.client = DigitalOcean(remote.token, transport=httpx.MockTransport(handle))

    def reserve(stage="firewall"):
        if stage == "firewall":
            remote.firewall_status = "waiting"
            assert execute()["phase"] == "firewall_pending"
            remote.firewalls[0]["status"] = "succeeded"
        else:
            kind, failure = {
                "empty": ("tag", "rejected"),
                "tag": ("firewall", "rejected"),
                "lost-tag": ("tag", "lost_after"),
                "lost-firewall": ("firewall", "lost_after"),
                "uncertain-tag": ("tag", "lost_before"),
                "uncertain-firewall": ("firewall", "lost_before"),
                "unknown-vm": ("droplet", "lost_before"),
                "paid-vm": ("droplet", "lost_after"),
            }[stage]
            remote.fail[kind] = failure
            with pytest.raises(InstallError):
                execute()
        return read_document(directory / fleet.BUDGET)["reservations"][REGISTRATION["id"]]["reservation_id"]

    def cancel(reservation, **kwargs):
        return coordinator().cancel(
            confirm_worker=REGISTRATION["id"], confirm_plan=cloud[4]["plan_id"], confirm_reservation=reservation, **kwargs
        )

    return reserve, cancel, coordinator, cloud, directory, failures, pending, denied, deletions, observations


@pytest.mark.parametrize("stage", ["empty", "tag", "firewall", "lost-tag", "lost-firewall"])
def test_exact_unpaid_cancellation_cleans_only_owned_free_resources_and_releases_budget(cancellation, stage):
    reserve, cancel, coordinator, cloud, directory, _, _, _, deletions, _ = cancellation
    reservation = reserve(stage)
    before = len(cloud[0].writes)
    result = cancel(reservation)
    assert result["phase"] == "cancelled" and result["reservation_released"] is True
    assert result["paid_vm_request_started"] is False and result["cloud_vm_deleted"] is False
    assert result["worker_enrollment_revoked"] is False and not cloud[0].droplets and not cloud[0].tags and not cloud[0].firewalls
    assert len(cloud[0].writes) == before + len(deletions)
    assert len(deletions) == (0 if stage == "empty" else 1 if stage in {"tag", "lost-tag"} else 2)
    assert coordinator().reconcile()["reserved_workers"] == 0
    assert read_document(directory / fleet.BUDGET)["reservations"][REGISTRATION["id"]]["status"] == "cancelled"
    assert (cloud[7] / CANCELLATION).stat().st_mode & 0o777 == 0o600
    assert TOKEN not in json.dumps(result) + (cloud[7] / CANCELLATION).read_text() and cloud[0].token not in json.dumps(result)
    count = len(deletions)
    assert cancel(reservation) == result and len(deletions) == count
    cloud[6].provider = cloud[0].client
    with pytest.raises(InstallError, match="terminal fleet cancellation"):
        unmanaged_apply(cloud)
    config, profiles = scaling.load_fleet(coordinator().fleet_path)
    observed = scaling.inventory(cloud[0].client, profiles)
    assert observed[0]["phase"] == "closed" and observed[0]["hourly_usd"] == "0"
    assert (
        scaling.decide(config, coordinator().snapshot_reader(config["server_url"], config["organization_id"], TOKEN), observed)["actions"]
        == []
    )


@pytest.mark.parametrize("stage", ["uncertain-tag", "uncertain-firewall"])
def test_unknown_free_creation_cannot_be_forgotten_or_reissued(cancellation, stage):
    reserve, cancel, coordinator, cloud, _, _, _, _, deletions, _ = cancellation
    reservation = reserve(stage)
    result = cancel(reservation)
    assert result["phase"] == "free_creation_uncertain" and result["reservation_released"] is False and not deletions
    assert coordinator().reconcile()["reserved_workers"] == 1
    assert cancel(reservation) == result and not deletions and not cloud[0].droplets


@pytest.mark.parametrize("stage", ["unknown-vm", "paid-vm"])
def test_cancellation_never_uses_empty_discovery_to_erase_pending_paid_vm(cancellation, stage):
    reserve, cancel, coordinator, cloud, _, _, _, _, deletions, _ = cancellation
    reservation = reserve(stage)
    with pytest.raises(InstallError, match="no paid VM request"):
        cancel(reservation)
    assert not (cloud[7] / CANCELLATION).exists() and not deletions and coordinator().reconcile()["reserved_workers"] == 1


@pytest.mark.parametrize("kind", ["firewall", "tag"])
@pytest.mark.parametrize("failure", ["lost_before", "lost_after", "rejected", "body"])
def test_uncertain_delete_retains_intent_and_requires_exact_retry(cancellation, kind, failure):
    reserve, cancel, coordinator, _, _, failures, _, _, deletions, _ = cancellation
    reservation = reserve()
    failures[kind] = failure
    with pytest.raises(InstallError):
        cancel(reservation)
    count = len(deletions)
    assert coordinator().reconcile()["reserved_workers"] == 1
    result = cancel(reservation)
    if failure == "lost_before":
        assert result["phase"] == "cancellation_pending" and not result["reservation_released"] and len(deletions) == count
        result = cancel(reservation, retry_delete=kind)
    assert result["phase"] == "cancelled" and coordinator().reconcile()["reserved_workers"] == 0
    assert len(deletions) == (3 if failure in {"lost_before", "rejected"} else 2)


@pytest.mark.parametrize("scope", ["images", "volumes", "snapshots", "databases"])
def test_tag_cancellation_requires_full_visibility_even_after_firewall_cleanup(cancellation, scope):
    reserve, cancel, coordinator, cloud, _, _, _, denied, deletions, _ = cancellation
    reservation = reserve()
    denied.add(scope)
    with pytest.raises(InstallError):
        cancel(reservation)
    assert len(deletions) == 1 and deletions[0].url.path.startswith("/v2/firewalls/")
    assert cloud[0].tags and coordinator().reconcile()["reserved_workers"] == 1
    denied.clear()
    assert cancel(reservation)["reservation_released"] is True


@pytest.mark.parametrize("change", ["account", "duplicate-firewall", "unrelated-member", "tag-count", "unknown-tag-type", "unmanaged-vm"])
def test_changed_or_unmanaged_resources_are_preserved(cancellation, change):
    reserve, cancel, _, cloud, _, _, _, _, deletions, _ = cancellation
    reservation = reserve()
    remote = cloud[0]
    if change == "account":
        remote.account["uuid"] = "f" * 40
    elif change == "duplicate-firewall":
        remote.firewalls.append({**copy.deepcopy(remote.firewalls[0]), "id": "22222222-2222-4333-8444-555555555555"})
    elif change == "unrelated-member":
        remote.firewalls[0]["droplet_ids"] = [999]
    elif change == "tag-count":
        remote.tags[0]["resources"]["count"] = 1
    elif change == "unknown-tag-type":
        remote.tags[0]["resources"]["unknown"] = {"count": 0}
    else:
        remote.droplets.append({"id": 999, "tags": remote.firewalls[0]["tags"]})
    with pytest.raises(InstallError):
        cancel(reservation)
    assert remote.tags and len(deletions) == (1 if change in {"tag-count", "unknown-tag-type"} else 0)


def test_terminal_intent_and_both_locks_precede_delete(cancellation):
    reserve, cancel, _, cloud, directory, _, _, _, _, observations = cancellation
    reservation = reserve()

    # Inspect the already-held lock rather than trying to acquire it twice.
    def locked(_kind):
        with pytest.raises(InstallError, match="another process"), locked_directory(directory):
            pytest.fail("coordinator lock was not held")
        with pytest.raises(InstallError, match="another process"), locked_directory(cloud[7]):
            pytest.fail("ownership lock was not held")
        assert read_document(cloud[7] / CANCELLATION)["intent"]["reservation_id"] == reservation

    observations["before_delete"] = locked
    assert cancel(reservation)["reservation_released"] is True


def test_wrong_reservation_and_invalid_retry_have_no_deletion(cancellation):
    reserve, cancel, _, cloud, _, _, _, _, deletions, _ = cancellation
    reservation = reserve()
    with pytest.raises(InstallError, match="original reserved"):
        cancel("f" * 64)
    with pytest.raises(InstallError, match="saved pending"):
        cancel(reservation, retry_delete="tag")
    assert not (cloud[7] / CANCELLATION).exists() and not deletions


def test_cancel_cli_requires_exact_confirmation_before_token_access(cancellation, monkeypatch, capsys):
    reserve, _, coordinator, _, directory, _, _, _, _, _ = cancellation
    reserve()
    controller = coordinator()
    monkeypatch.setattr(cli, "DigitalOcean", lambda *_: pytest.fail("invalid local confirmation must precede provider access"))
    with pytest.raises(SystemExit) as error:
        cli.main(
            [
                "do-worker-fleet-cancel",
                "--fleet",
                str(controller.fleet_path),
                "--fleet-state-dir",
                str(directory),
                "--confirm-worker",
                REGISTRATION["id"],
                "--confirm-plan",
                controller.profiles[0].identity,
                "--confirm-reservation",
                "invalid",
                "--token-file",
                str(directory / "missing-token"),
            ]
        )
    assert error.value.code == 2 and "original reservation ID" in capsys.readouterr().err


@pytest.mark.parametrize("stage", ["uncertain-tag", "uncertain-firewall"])
def test_late_free_receipt_is_reconciled_without_another_post(cancellation, stage):
    reserve, cancel, coordinator, cloud, _, _, _, _, deletions, _ = cancellation
    reservation = reserve(stage)
    assert cancel(reservation)["phase"] == "free_creation_uncertain"
    before = len([request for request in cloud[0].writes if request.method == "POST"])
    kind = "tag" if stage == "uncertain-tag" else "firewall"
    body = {"name": cloud[6].marker} if kind == "tag" else cloud[6]._firewall_body()
    cloud[0].create(kind, body)  # Provider delivers the original delayed request.
    assert cancel(reservation)["reservation_released"] is True
    assert len([request for request in cloud[0].writes if request.method == "POST"]) == before
    assert len(deletions) == (1 if kind == "tag" else 2) and coordinator().reconcile()["reserved_workers"] == 0


@pytest.mark.parametrize("change", ["plan", "reservation", "owned", "ordering", "vm-stage"])
def test_changed_cancel_journal_cannot_authorize_deletion_or_budget_release(cancellation, change):
    reserve, cancel, coordinator, cloud, _, _, pending, _, deletions, _ = cancellation
    reservation = reserve()
    pending.add("firewall")
    assert cancel(reservation)["phase"] == "cancellation_pending"
    path = cloud[7] / CANCELLATION
    value = read_document(path)
    if change == "plan":
        value["intent"]["plan_id"] = "f" * 64
    elif change == "reservation":
        value["intent"]["reservation_id"] = "f" * 64
    elif change == "owned":
        value["owned"]["firewall"] = "22222222-2222-4333-8444-555555555555"
    elif change == "ordering":
        value["deletion"]["tag"] = "absent"
    else:
        journal = read_document(cloud[7] / "digitalocean.json")
        journal["resources"]["droplet"]["status"] = "request_pending"
        write_document(cloud[7] / "digitalocean.json", journal)
    write_document(path, value)
    count = len(deletions)
    with pytest.raises(InstallError):
        cancel(reservation, retry_delete="firewall")
    assert len(deletions) == count and cloud[0].firewalls and cloud[0].tags
    with pytest.raises(InstallError):
        coordinator().reconcile() if change in {"plan", "reservation", "ordering"} else cancel(reservation)


def test_cancel_cli_executes_with_exact_private_original_intent(cancellation, monkeypatch, capsys):
    reserve, _, coordinator, cloud, directory, _, _, _, deletions, _ = cancellation
    reservation = reserve()
    controller = coordinator()
    token = directory.parent / "provider-token"
    write_document(token, {"fixture": True}, new=True)
    token.write_text(cloud[0].token)
    monkeypatch.setattr(
        cli, "DigitalOcean", lambda credential: cloud[0].client if credential == cloud[0].token else pytest.fail("wrong key")
    )
    assert (
        cli.main(
            [
                "do-worker-fleet-cancel",
                "--fleet",
                str(controller.fleet_path),
                "--fleet-state-dir",
                str(directory),
                "--confirm-worker",
                REGISTRATION["id"],
                "--confirm-plan",
                controller.profiles[0].identity,
                "--confirm-reservation",
                reservation,
                "--token-file",
                str(token),
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["phase"] == "cancelled" and result["reservation_released"] and len(deletions) == 2
    assert TOKEN not in json.dumps(result) and cloud[0].token not in json.dumps(result)
