"""Confirmed budget/action HTTP fixtures, never real paid cloud provisioning."""

import copy
import json
import time
from pathlib import Path

import httpx
import pytest
from test_public_compute_scaling import cloud_fleet as cloud_fleet
from test_public_deployment import bundle as bundle
from test_public_deployment import payload as payload
from test_public_worker_cloud_retirement import DeletionProvider
from test_public_worker_cloud_retirement import cloud_retirement as cloud_retirement
from test_public_worker_deployment import IMAGE, REGISTRATION, TOKEN
from test_public_worker_deployment import bootstrap as bootstrap
from test_public_worker_host_retirement import managed as managed
from test_public_worker_provisioning import apply as unmanaged_apply
from test_public_worker_provisioning import worker_cloud as worker_cloud

from reasoning_ledger.deployment import cli, fleet, scaling
from reasoning_ledger.deployment.digitalocean import DigitalOcean
from reasoning_ledger.deployment.host import InstallError
from reasoning_ledger.deployment.private import locked_directory, read_document, write_document
from reasoning_ledger.deployment.provision import save_plan
from reasoning_ledger.deployment.worker_provision import enrollment_binding, validate_worker_plan, worker_inputs
from reasoning_ledger.server.compute_worker import WorkerClient, write_state


@pytest.fixture
def managed_fleet(cloud_fleet):
    configuration, path, cloud = cloud_fleet
    remote = cloud[0]
    observer = DeletionProvider(remote, cloud[7])
    remote.client.close()

    def provider_request(request):
        if request.url.path.endswith("/firewalls") and "/droplets/" in request.url.path:
            return remote.handle(request)
        return observer.handle(request)

    remote.client = DigitalOcean(remote.token, transport=httpx.MockTransport(provider_request))
    snapshot = {
        "format": 1,
        "organization_id": configuration["organization_id"],
        "observed_at": int(time.time()),
        "max_active_jobs": 2,
        "images": [IMAGE],
        "jobs": [],
        "workers": [
            {
                **{key: value for key, value in REGISTRATION.items() if key != "token"},
                "retired_at": None,
                "retirement_id": None,
                "idle_since_at": 1,
            }
        ],
    }
    reads = []

    def read_snapshot(origin, organization, credential):
        assert origin == configuration["server_url"] and organization == configuration["organization_id"] and credential == TOKEN
        reads.append(credential)
        snapshot["observed_at"] = int(time.time())
        return copy.deepcopy(snapshot)

    directory = path.parent / "fleet-state"

    def coordinator():
        return fleet.FleetCoordinator(remote.client, path, directory, snapshot_reader=read_snapshot, identity_client=cloud[1].client)

    def advice():
        specification, profiles = scaling.load_fleet(path)
        return scaling.decide(
            specification,
            read_snapshot(configuration["server_url"], configuration["organization_id"], TOKEN),
            scaling.inventory(remote.client, profiles),
        )

    def execute(controller=None, decision=None, **kwargs):
        decision = decision or advice()
        return (controller or coordinator()).apply(
            TOKEN,
            confirm_worker=REGISTRATION["id"],
            confirm_plan=cloud[4]["plan_id"],
            advice=decision,
            confirm_decision=decision["decision_id"],
            **kwargs,
        )

    return coordinator, advice, execute, snapshot, cloud, directory, reads, path


def resume(fixture, controller=None):
    coordinator, _, _, _, cloud, directory, _, _ = fixture
    row = read_document(directory / fleet.BUDGET)["reservations"][REGISTRATION["id"]]
    return (controller or coordinator()).apply(
        TOKEN, confirm_worker=REGISTRATION["id"], confirm_plan=cloud[4]["plan_id"], resume_reservation=row["reservation_id"]
    )


def test_reservation_and_binding_are_durable_before_every_provider_post(managed_fleet):
    coordinator, _, execute, _, cloud, directory, reads, path = managed_fleet
    remote = cloud[0]

    def before_post(kind, body):
        ledger = read_document(directory / fleet.BUDGET)
        row = ledger["reservations"][REGISTRATION["id"]]
        assert row["status"] == "reserved" and fleet.hash_id(row["reservation_id"])
        assert read_document(cloud[7] / fleet.BINDING)["budget_dir"] == str(directory)
        assert read_document(cloud[7] / "digitalocean.json")["resources"][kind]["status"] == "request_pending"
        with pytest.raises(InstallError, match="another process"):
            coordinator().reconcile()
        with pytest.raises(InstallError, match="another process"):
            unmanaged_apply(cloud)
        assert TOKEN not in json.dumps(body) and remote.token not in json.dumps(body)

    remote.before_post = before_post
    result = execute()
    assert result["phase"] == "ssh_verification_required" and result["reserved_owned_hourly_usd"] == "0.035"
    assert result["worker_installation_verified"] is False and result["container_execution_verified"] is False
    assert len(remote.writes) == 3 and len(reads) >= 5
    assert result["reservation_id"] == read_document(directory / fleet.BUDGET)["reservations"][REGISTRATION["id"]]["reservation_id"]
    assert directory.stat().st_mode & 0o777 == 0o700 and (directory / fleet.BUDGET).stat().st_mode & 0o777 == 0o600
    assert (cloud[7] / fleet.BINDING).stat().st_mode & 0o777 == 0o600
    assert TOKEN not in json.dumps(result) + (directory / fleet.BUDGET).read_text() + (cloud[7] / fleet.BINDING).read_text()
    assert remote.token not in json.dumps(result) + (directory / fleet.BUDGET).read_text()
    assert path.exists()


def test_fleet_reconciliation_is_local_only_binding_without_cloud_writes(managed_fleet):
    coordinator, _, _, _, cloud, directory, _, _ = managed_fleet
    report = coordinator().reconcile()
    assert report["reserved_workers"] == 0 and report["reserved_hourly_usd"] == "0"
    assert report["creates_resources"] is False and report["destroys_resources"] is False
    assert read_document(cloud[7] / "digitalocean.json")["resources"]["droplet"]["status"] == "not_started"
    assert not cloud[0].writes and directory.exists()
    with pytest.raises(InstallError, match="fleet-managed"):
        unmanaged_apply(cloud)


@pytest.mark.parametrize("kind", ["tag", "firewall", "droplet"])
@pytest.mark.parametrize("failure", ["lost_before", "lost_after", "rejected", "redirect"])
def test_failed_create_retains_full_budget_and_original_resumption_rules(managed_fleet, kind, failure):
    coordinator, _, execute, _, cloud, directory, _, _ = managed_fleet
    cloud[0].fail[kind] = failure
    with pytest.raises(InstallError):
        execute()
    count = len(cloud[0].writes)
    report = coordinator().reconcile()
    assert report["reserved_workers"] == 1 and report["reserved_hourly_usd"] == "0.035"
    assert report["reservations"][REGISTRATION["id"]]["status"] == "reserved"
    if failure == "lost_before":
        with pytest.raises(InstallError, match="uncertain"):
            resume(managed_fleet)
        assert len(cloud[0].writes) == count
        assert coordinator().reconcile()["reserved_workers"] == 1
    else:
        result = resume(managed_fleet)
        assert result["phase"] == "ssh_verification_required"
        assert len(cloud[0].writes) == 4 if failure == "rejected" else len(cloud[0].writes) == 3
        assert read_document(directory / fleet.BUDGET)["reservations"][REGISTRATION["id"]]["status"] == "reserved"


@pytest.mark.parametrize("change", ["expires", "hash", "worker", "plan", "fleet", "confirmation", "format", "side-effects"])
def test_invalid_advice_has_no_local_or_provider_side_effects(managed_fleet, change):
    coordinator, advice, _, _, cloud, directory, _, _ = managed_fleet
    document = advice()
    confirmation = document["decision_id"]
    worker, plan = REGISTRATION["id"], cloud[4]["plan_id"]
    if change == "expires":
        document["observed_at"] -= 31
        document["expires_at"] -= 31
    elif change == "hash":
        document["runnable_queued_jobs"] = 8
    elif change == "worker":
        worker = "e" * 32
    elif change == "plan":
        plan = "e" * 64
    elif change == "fleet":
        document["fleet_sha256"] = "e" * 64
    elif change == "confirmation":
        confirmation = "e" * 64
    elif change == "format":
        document["format"] = True
    else:
        document["creates_resources"] = True
    if change not in {"hash", "confirmation"}:
        document["decision_id"] = fleet.digest({key: value for key, value in document.items() if key != "decision_id"})
        confirmation = document["decision_id"]
    controller = coordinator()
    count = len(cloud[0].requests)
    with pytest.raises(InstallError):
        controller.apply(TOKEN, confirm_worker=worker, confirm_plan=plan, advice=document, confirm_decision=confirmation)
    assert not directory.exists() and not cloud[7].exists() and len(cloud[0].requests) == count


@pytest.mark.parametrize("moment", ["before-action", "before-vm", "free-resume"])
def test_fresh_queue_change_prevents_new_paid_intent_and_retains_existing_reservation(managed_fleet, moment):
    coordinator, advice, execute, snapshot, cloud, directory, _, _ = managed_fleet
    original = advice()
    if moment == "before-action":
        snapshot["workers"][0]["draining"] = 1
    elif moment == "before-vm":
        cloud[0].before_post = lambda kind, _: snapshot["workers"][0].update(draining=1) if kind == "firewall" else None
    else:
        cloud[0].firewall_status = "waiting"
        assert execute(decision=original)["phase"] == "firewall_pending"
        cloud[0].firewalls[0]["status"] = "succeeded"
        snapshot["workers"][0]["draining"] = 1
    with pytest.raises(InstallError, match="no longer recommends"):
        resume(managed_fleet) if moment == "free-resume" else execute(decision=original)
    assert not cloud[0].droplets
    assert read_document(cloud[7] / "digitalocean.json")["resources"]["droplet"]["status"] == "not_started"
    assert coordinator().reconcile()["reserved_workers"] == (0 if moment == "before-action" else 1)
    assert directory.exists()


def test_changed_queue_does_not_duplicate_already_pending_paid_creation(managed_fleet):
    coordinator, _, execute, snapshot, cloud, _, _, _ = managed_fleet
    cloud[0].fail["droplet"] = "lost_after"
    with pytest.raises(InstallError):
        execute()
    snapshot["workers"][0]["draining"] = 1
    assert resume(managed_fleet)["phase"] == "ssh_verification_required"
    assert len(cloud[0].writes) == 3 and coordinator().reconcile()["reserved_workers"] == 1


def test_existing_owned_host_is_counted_and_requires_its_adoption_reservation(managed_fleet):
    coordinator, _, _, _, cloud, _, _, _ = managed_fleet
    # This fixture replaces the provider; update the original unmanaged object.
    cloud[6].provider = cloud[0].client
    unmanaged_apply(cloud)
    report = coordinator().reconcile()
    assert report["reserved_workers"] == 1 and report["reserved_hourly_usd"] == "0.035"
    assert report["reservations"][REGISTRATION["id"]]["decision_id"] is None
    assert resume(managed_fleet)["phase"] == "ssh_verification_required" and len(cloud[0].writes) == 3


@pytest.mark.parametrize("change", ["ledger-missing", "binding-missing", "other-coordinator", "scope", "reservation", "symlink"])
def test_budget_cannot_be_reset_or_rebound_to_ignore_paid_intent(managed_fleet, change):
    coordinator, _, execute, _, cloud, directory, _, path = managed_fleet
    execute()
    ledger_path = directory / fleet.BUDGET
    if change == "ledger-missing":
        ledger_path.unlink()
    elif change == "binding-missing":
        # Original ledger remains; missing marker is safely rebound by the
        # coordinator before it can resume any provider operation.
        (cloud[7] / fleet.BINDING).unlink()
        assert resume(managed_fleet)["phase"] == "ssh_verification_required"
        assert coordinator().reconcile()["reserved_workers"] == 1
        return
    elif change == "other-coordinator":
        with pytest.raises(InstallError):
            fleet.FleetCoordinator(cloud[0].client, path, path.parent / "other-budget").reconcile()
        assert len(cloud[0].writes) == 3
        return
    elif change == "symlink":
        displaced = directory / "saved-budget.json"
        ledger_path.rename(displaced)
        ledger_path.symlink_to(displaced)
    else:
        state = read_document(ledger_path)
        if change == "scope":
            state["scope"]["fleet"]["maximum_workers"] += 1
        else:
            state["reservations"][REGISTRATION["id"]]["reservation_id"] = "f" * 64
        write_document(ledger_path, state)
    with pytest.raises(InstallError):
        coordinator().reconcile()
    assert len(cloud[0].writes) == 3


def test_expired_process_capability_cannot_be_reused(managed_fleet):
    coordinator, _, execute, _, cloud, _, _, _ = managed_fleet
    controller = coordinator()
    execute(controller=controller)
    profile = controller.profiles[0]
    profile.fleet_lease = fleet.FleetLease(controller, profile)
    with pytest.raises(InstallError, match="live budget"):
        profile.apply(cloud[7], cloud[4]["plan_id"])
    assert len(cloud[0].writes) == 3


@pytest.mark.parametrize("overlap", ["same", "parent", "child", "relative"])
def test_coordinator_paths_cannot_overlap_worker_state(managed_fleet, overlap):
    _, _, _, _, cloud, _, _, path = managed_fleet
    directory = {"same": cloud[7], "parent": cloud[7].parent, "child": cloud[7] / "fleet", "relative": Path("fleet")}[overlap]
    with pytest.raises(InstallError):
        fleet.FleetCoordinator(cloud[0].client, path, directory)
    assert not cloud[0].writes and not cloud[7].exists()


def test_resume_confirmation_is_exact_and_does_not_allocate_again(managed_fleet):
    coordinator, _, execute, _, cloud, directory, _, _ = managed_fleet
    execute()
    before = read_document(directory / fleet.BUDGET)
    with pytest.raises(InstallError, match="original durable"):
        coordinator().apply(TOKEN, confirm_worker=REGISTRATION["id"], confirm_plan=cloud[4]["plan_id"], resume_reservation="f" * 64)
    assert read_document(directory / fleet.BUDGET) == before and len(cloud[0].writes) == 3


def test_coordinator_lock_prevents_concurrent_binding_or_creation(managed_fleet):
    coordinator, _, execute, _, cloud, directory, _, _ = managed_fleet
    with locked_directory(directory), pytest.raises(InstallError, match="another process"):
        execute(controller=coordinator())
    assert not cloud[0].writes and not cloud[7].exists()


def test_cli_rejects_bad_confirmation_before_provider_or_coordinator(managed_fleet, monkeypatch, capsys):
    _, advice, _, _, cloud, directory, _, path = managed_fleet
    saved = path.parent / "decision.json"
    write_document(saved, advice(), new=True)
    monkeypatch.setattr(cli, "DigitalOcean", lambda _: pytest.fail("confirmation must precede provider construction"))
    with pytest.raises(SystemExit) as error:
        cli.main(
            [
                "do-worker-scale-apply",
                "--fleet",
                str(path),
                "--fleet-state-dir",
                str(directory),
                "--decision",
                str(saved),
                "--confirm-decision",
                "f" * 64,
                "--confirm-worker",
                REGISTRATION["id"],
                "--confirm-plan",
                cloud[4]["plan_id"],
                "--session-file",
                str(path.parent / "missing-session"),
                "--token-file",
                str(path.parent / "missing-token"),
            ]
        )
    assert error.value.code == 2
    assert "fresh fleet-bound" in capsys.readouterr().err and not directory.exists()


@pytest.fixture
def two_workers(managed_fleet):
    _, _, _, snapshot, cloud, directory, _, path = managed_fleet
    specification = read_document(path)
    specification["minimum_workers"] = 2
    second = copy.deepcopy(REGISTRATION)
    second.update(id="c" * 32, token="fixture-token-" + "y" * 40, name="Second")
    config, images = worker_inputs(specification["server_url"], second, [IMAGE])
    plan = copy.deepcopy(cloud[5]["plan"])
    plan["enrollment"] = enrollment_binding(config, images)
    saved = save_plan(path.parent / "second-plan.json", plan, validator=validate_worker_plan)
    registration = path.parent / "second-registration.json"
    write_state(registration, second)
    specification["workers"].append(
        {
            "plan": saved["plan_file"],
            "registration": str(registration),
            "catalog": specification["workers"][0]["catalog"],
            "state_dir": str(path.parent / "second-ownership"),
        }
    )
    write_document(path, specification)
    snapshot["workers"].append({**snapshot["workers"][0], "id": second["id"], "name": second["name"]})
    remote = cloud[0]
    original_create, original_handle = remote.create, remote.handle
    observation = DeletionProvider(remote, cloud[7])

    def create(kind, body):
        previous_members = [copy.deepcopy(row["droplet_ids"]) for row in remote.firewalls]
        row = original_create(kind, body)
        if kind == "firewall":
            row["id"] = f"11111111-2222-4333-8444-{len(remote.firewalls):012d}"
        elif kind == "droplet":
            row["id"] = 455 + len(remote.droplets)
            for firewall, members in zip(remote.firewalls, previous_members, strict=True):
                firewall["droplet_ids"] = [row["id"]] if firewall["tags"] == row["tags"] else members
        return row

    def handle(request):
        parts = request.url.path.removeprefix("/v2/").split("/")
        if request.method == "GET" and len(parts) == 3 and parts[0] == "droplets" and parts[2] == "firewalls":
            remote.requests.append(request)
            return httpx.Response(200, json={"firewalls": [row for row in remote.firewalls if int(parts[1]) in row["droplet_ids"]]})
        return observation.handle(request) if request.method == "GET" else original_handle(request)

    remote.create = create
    remote.client.close()
    remote.client = DigitalOcean(remote.token, transport=httpx.MockTransport(handle))
    clients = []

    def coordinator():
        result = fleet.FleetCoordinator(
            remote.client,
            path,
            directory,
            snapshot_reader=lambda *_: {
                **copy.deepcopy(snapshot),
                "observed_at": int(time.time()),
            },
        )
        for profile in result.profiles:
            record = next(worker for worker in snapshot["workers"] if worker["id"] == profile.config["id"])
            client = WorkerClient(profile.config, transport=httpx.MockTransport(lambda _, record=record: httpx.Response(200, json=record)))
            clients.append(client)
            profile.identity_client = client
        return result

    def advice():
        configuration, profiles = scaling.load_fleet(path)
        return scaling.decide(
            configuration, {**copy.deepcopy(snapshot), "observed_at": int(time.time())}, scaling.inventory(remote.client, profiles)
        )

    yield coordinator, advice, snapshot, cloud, directory, path
    for client in clients:
        client.close()


def test_two_real_fixture_workers_share_one_exact_aggregate_budget(two_workers):
    coordinator, advice, snapshot, cloud, directory, path = two_workers
    specification = read_document(path)
    specification["maximum_hourly_usd"] = "0.070000000000"
    write_document(path, specification)
    assert len(advice()["actions"]) == 2
    for index in range(2):
        controller, decision = coordinator(), advice()
        action = decision["actions"][0]
        result = controller.apply(
            TOKEN,
            confirm_worker=action["worker_id"],
            confirm_plan=action["plan_id"],
            advice=decision,
            confirm_decision=decision["decision_id"],
        )
        next(worker for worker in snapshot["workers"] if worker["id"] == action["worker_id"])["last_seen_at"] = int(time.time())
        assert result["reserved_owned_hourly_usd"] == ("0.035" if index == 0 else "0.070")
    report = coordinator().reconcile()
    assert report["reserved_workers"] == 2 and report["reserved_hourly_usd"] == "0.070" and not report["ceiling_exceeded"]
    assert len(cloud[0].writes) == 6 and {row["id"] for row in cloud[0].droplets} == {456, 457}
    assert len({row["reservation_id"] for row in read_document(directory / fleet.BUDGET)["reservations"].values()}) == 2


@pytest.mark.parametrize("limit", ["dollars", "count"])
def test_durable_ceiling_counts_reserved_intent_even_when_no_provider_resource_exists(two_workers, limit):
    coordinator, advice, _, cloud, directory, path = two_workers
    specification = read_document(path)
    if limit == "dollars":
        specification["maximum_hourly_usd"] = "0.069999999999"
    else:
        specification["maximum_workers"] = 1
        specification["minimum_workers"] = 1
    write_document(path, specification)
    decision = advice()
    action = decision["actions"][0]
    assert len(decision["actions"]) == 1
    cloud[0].fail["tag"] = "lost_before"
    with pytest.raises(InstallError):
        coordinator().apply(
            TOKEN,
            confirm_worker=action["worker_id"],
            confirm_plan=action["plan_id"],
            advice=decision,
            confirm_decision=decision["decision_id"],
        )
    assert not cloud[0].droplets and len(cloud[0].writes) == 1
    controller = coordinator()
    controller.reconcile()
    other = next(profile for profile in controller.profiles if profile.config["id"] != action["worker_id"])
    with pytest.raises(InstallError, match="ceiling"):
        controller._budget(extra=other)
    assert controller._totals() == (1, fleet.Decimal("0.035"))
    assert read_document(directory / fleet.BUDGET)["reservations"][action["worker_id"]]["status"] == "reserved"


@pytest.mark.parametrize("partial", [False, True])
def test_only_fully_retired_and_freshly_absent_resources_release_budget(cloud_retirement, partial):
    retire, arguments, provider, _, cloud, _ = cloud_retirement
    root = cloud[7].parent
    registration, catalog, path = root / "fleet-registration.json", root / "fleet-catalog.json", root / "fleet.json"
    write_state(registration, REGISTRATION)
    write_state(catalog, [IMAGE])
    write_document(
        path,
        {
            "format": 1,
            "server_url": REGISTRATION.get("server_url", "https://chat.example.org"),
            "organization_id": REGISTRATION["organization_id"],
            "minimum_workers": 0,
            "maximum_workers": 1,
            "maximum_hourly_usd": "0.035",
            "idle_seconds": 60,
            "workers": [
                {"plan": cloud[4]["plan_file"], "registration": str(registration), "catalog": str(catalog), "state_dir": str(cloud[7])}
            ],
        },
        new=True,
    )

    def coordinator():
        return fleet.FleetCoordinator(cloud[0].client, path, root / "fleet-state")

    original = coordinator().reconcile()
    assert original["reserved_workers"] == 1
    if partial:
        provider.pending.add("firewall")
        assert retire.retire(cloud[7], **arguments)["phase"] == "deletion_pending"
        pending = coordinator().reconcile()
        assert pending["reserved_workers"] == 1 and pending["reserved_hourly_usd"] == "0.035"
        provider.pending.clear()
        assert retire.retire(cloud[7], **arguments, retry_delete="firewall")["phase"] == "deleted"
    else:
        assert retire.retire(cloud[7], **arguments)["phase"] == "deleted"
    report = coordinator().reconcile()
    assert report["reserved_workers"] == 0 and report["reserved_hourly_usd"] == "0"
    row = report["reservations"][REGISTRATION["id"]]
    assert row["status"] == "closed" and row["reservation_id"] == original["reservations"][REGISTRATION["id"]]["reservation_id"]
    deletions = len(provider.deletions)
    assert coordinator().reconcile() == report and len(provider.deletions) == deletions


def test_cli_executes_and_resumes_exact_private_budgeted_action(managed_fleet, monkeypatch, capsys):
    _, advice, _, snapshot, cloud, directory, _, path = managed_fleet
    decision = advice()
    saved = path.parent / "decision.json"
    write_document(saved, decision, new=True)
    session, token = path.parent / "session-token", path.parent / "provider-token"
    write_document(session, {"fixture": True}, new=True)
    write_document(token, {"fixture": True}, new=True)
    # Fixture-only opaque secret files preserve the production private modes.
    session.write_text(TOKEN)
    token.write_text(cloud[0].token)
    real_coordinator = fleet.FleetCoordinator

    def coordinator(provider, fleet_path, state_dir):
        assert provider is cloud[0].client and fleet_path == path and state_dir == directory
        return real_coordinator(
            provider,
            fleet_path,
            state_dir,
            identity_client=cloud[1].client,
            snapshot_reader=lambda *_: {**copy.deepcopy(snapshot), "observed_at": int(time.time())},
        )

    monkeypatch.setattr(fleet, "FleetCoordinator", coordinator)
    monkeypatch.setattr(
        cli, "DigitalOcean", lambda credential: cloud[0].client if credential == cloud[0].token else pytest.fail("wrong token")
    )
    common = [
        "do-worker-scale-apply",
        "--fleet",
        str(path),
        "--fleet-state-dir",
        str(directory),
        "--session-file",
        str(session),
        "--token-file",
        str(token),
        "--confirm-worker",
        REGISTRATION["id"],
        "--confirm-plan",
        cloud[4]["plan_id"],
    ]
    assert cli.main([*common, "--decision", str(saved), "--confirm-decision", decision["decision_id"]]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["phase"] == "ssh_verification_required" and fleet.hash_id(result["reservation_id"])
    assert len(cloud[0].writes) == 3 and TOKEN not in json.dumps(result) and cloud[0].token not in json.dumps(result)
    # The CLI closes its provider after every call; the second call gets a new
    # adapter over the same fixture resources, just as a separate process does.
    old_transport = cloud[0].client.client._transport
    cloud[0].client = DigitalOcean(cloud[0].token, transport=old_transport)
    assert cli.main([*common, "--resume-reservation", result["reservation_id"]]) == 0
    assert json.loads(capsys.readouterr().out)["reservation_id"] == result["reservation_id"]
    assert len(cloud[0].writes) == 3
