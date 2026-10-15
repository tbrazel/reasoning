"""Queue/provider/host controller fixtures, not live cloud or Docker execution."""

import copy
import hashlib
import json
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from test_public_compute_fleet import managed_fleet as managed_fleet
from test_public_compute_scaling import cloud_fleet as cloud_fleet
from test_public_deployment import bundle as bundle
from test_public_deployment import payload as payload
from test_public_worker_cloud_retirement import DeletionProvider
from test_public_worker_deployment import IMAGE, ORIGIN, REGISTRATION, TOKEN
from test_public_worker_deployment import bootstrap as bootstrap
from test_public_worker_host_retirement import Lifecycle
from test_public_worker_provisioning import worker_cloud as worker_cloud

from reasoning_ledger.deployment import cli, controller, fleet
from reasoning_ledger.deployment.digitalocean import DigitalOcean
from reasoning_ledger.deployment.host import InstallError, canonical
from reasoning_ledger.deployment.private import locked_directory, read_document, write_document
from reasoning_ledger.deployment.worker_cancel import CANCELLATION
from reasoning_ledger.deployment.worker_install import JOURNAL as INSTALLATION
from reasoning_ledger.deployment.worker_retire import JOURNAL as RETIREMENT
from reasoning_ledger.server.compute_worker import write_state
from reasoning_ledger.server.security import private_file


def queued():
    return {
        "id": "1" * 32,
        "created_at": 1,
        "status": "queued",
        "worker_id": None,
        "lease_expires_at": None,
        "deadline_at": None,
        "runnable": True,
        "image_id": IMAGE["id"],
        "image_reference": IMAGE["reference"],
        "resources": copy.deepcopy(REGISTRATION["resources"]),
    }


@pytest.fixture
def controller_fleet(managed_fleet, bootstrap, bundle, monkeypatch, request):
    factory, _, _, snapshot, cloud, directory, _, path = managed_fleet
    minimum = getattr(request, "param", 1)
    specification = read_document(path)
    specification["minimum_workers"] = minimum
    write_document(path, specification)
    if minimum == 0:
        snapshot["jobs"] = [queued()]
    remote = cloud[0]
    observer = DeletionProvider(remote, cloud[7])
    remote.client.close()

    def provider_request(req):
        if req.url.path.endswith("/firewalls") and "/droplets/" in req.url.path:
            return remote.handle(req)
        if req.method == "DELETE" and (cloud[7] / CANCELLATION).exists():
            remote.requests.append(req)
            collection, identifier = req.url.path.removeprefix("/v2/").split("/")
            kind = {"firewalls": "firewall", "tags": "tag"}[collection]
            assert read_document(cloud[7] / CANCELLATION)["deletion"][kind] == "request_pending"
            rows = getattr(remote, collection)
            rows[:] = [row for row in rows if str(row.get("id", row.get("name"))) != identifier]
            if kind == "firewall" and remote.tags:
                remote.tags[0]["resources"] = {"count": 0, "droplets": {"count": 0}}
            return httpx.Response(204)
        return observer.handle(req)

    remote.client = DigitalOcean(remote.token, transport=httpx.MockTransport(provider_request))
    identity, known = path.parent / "identity", path.parent / "known"
    private_file(identity, b"private SSH identity fixture")
    private_file(known, b"139.59.1.2 ssh-ed25519 independently-verified-public-key")
    policy = controller.plan_controller(
        path, directory, bundle, identity, known, allow_provision=True, allow_retirement=True, delete_host_data=True
    )
    policy_path = path.parent / "controller-policy.json"
    write_document(policy_path, policy, new=True)
    installer, commands = bootstrap
    observations = {"installs": 0, "retirements": 0, "containers": b""}
    runtime = SimpleNamespace(lifecycle=None)
    reader = factory().snapshot_reader

    def install_host(profile, address, binding):
        observations["installs"] += 1
        assert address == "139.59.1.2" and profile.config["id"] == REGISTRATION["id"]
        assert read_document(directory / controller.JOURNAL)["last_action"]["action"] == "install"
        with pytest.raises(InstallError, match="another process"), locked_directory(directory):
            pytest.fail("controller must retain global budget ownership through SSH")
        # Simulate cloud-init delivery independently from the installation request.
        body = json.loads(next(req.content for req in remote.writes if req.url.path.endswith("/droplets")))
        cloud_init = json.loads(body["user_data"].split("\n", 1)[1])
        marker = json.loads(cloud_init["write_files"][0]["content"])
        (installer.root / "etc").mkdir(mode=0o755, exist_ok=True)
        write_document(installer.root / "etc/reasoning-provisioning.json", marker)
        monkeypatch.setattr(installer, "_droplet_id", lambda: remote.droplets[0]["id"])
        result = installer.install_worker(
            bundle,
            hashlib.sha256(bundle.read_bytes()).hexdigest(),
            ORIGIN,
            profile.config,
            profile.images,
            skip_platform=True,
            provisioning=binding,
        )
        if runtime.lifecycle is None:
            executable = installer.root / "opt/reasoning-compute/releases" / result["release_id"] / "venv/bin/reasoning-compute-worker"
            executable.parent.mkdir(parents=True, mode=0o755)
            executable.parent.chmod(0o755)
            executable.parent.parent.chmod(0o755)
            executable.write_bytes(b"nonexecuting worker command fixture\n")
            executable.chmod(0o755)
            write_state(
                installer.root / "var/lib/reasoning-compute/docker-runtime.json",
                {
                    "format": 1,
                    "worker_id": profile.config["id"],
                    "socket": "/run/docker.sock",
                    "daemon_id": "fixture-daemon",
                },
            )
            runtime.lifecycle = Lifecycle(installer, commands)

            def host_command(args, **kwargs):
                result = runtime.lifecycle(args, **kwargs)
                if args[0] == "/usr/bin/docker" and "info" in args:
                    return subprocess.CompletedProcess(args, 0, stdout=canonical({"ID": "fixture-daemon"}))
                if args[0] == "/usr/bin/docker" and "container" in args and "ls" in args:
                    return subprocess.CompletedProcess(args, 0, stdout=observations["containers"])
                return result

            installer.command = host_command
        return result

    def retire_host(profile, address, binding):
        observations["retirements"] += 1
        assert address == "139.59.1.2" and binding["delete_host_data"] is True
        return installer.retire_worker(
            bundle,
            hashlib.sha256(bundle.read_bytes()).hexdigest(),
            ORIGIN,
            profile.config,
            profile.images,
            skip_platform=True,
            destruction=binding,
        )

    def make(policy_override=None, **kwargs):
        return controller.WorkerController(
            remote.client, policy_override or policy, bundle=bundle, snapshot_reader=reader, identity_client=cloud[1].client, **kwargs
        )

    def cycle(instance=None, **kwargs):
        instance = instance or make()
        return instance.cycle(
            TOKEN,
            confirm_controller=fleet.digest(instance.policy),
            install_host=install_host,
            retire_host=retire_host,
            **kwargs,
        )

    return SimpleNamespace(
        make=make,
        cycle=cycle,
        policy=policy,
        policy_path=policy_path,
        install=install_host,
        retire=retire_host,
        observations=observations,
        snapshot=snapshot,
        cloud=cloud,
        budget=directory,
        fleet_path=path,
        installer=installer,
        commands=commands,
        runtime=runtime,
        observer=observer,
        reader=reader,
    )


def provision_install(fixture):
    assert fixture.cycle()["action"] == "provision"
    assert fixture.cycle()["action"] == "install"


def poll(fixture):
    fixture.snapshot["workers"][0]["last_seen_at"] = int(time.time())


def test_plan_is_local_immutable_secret_free_and_does_not_authorize_by_itself(controller_fleet):
    fixture = controller_fleet
    assert not fixture.cloud[0].writes and not fixture.budget.exists() and not fixture.cloud[7].exists()
    assert read_document(fixture.policy_path) == fixture.policy
    assert TOKEN not in json.dumps(fixture.policy) and fixture.cloud[0].token not in json.dumps(fixture.policy)
    assert fixture.policy_path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(InstallError, match="confirm"):
        fixture.make().cycle(TOKEN, confirm_controller="0" * 64, install_host=fixture.install, retire_host=fixture.retire)
    assert not fixture.budget.exists() and not fixture.cloud[0].writes


def test_controller_provisions_installs_waits_for_poll_and_preserves_budget(controller_fleet):
    fixture = controller_fleet
    first = fixture.cycle()
    assert first["action"] == "provision" and first["reserved_workers"] == 1
    reservation = read_document(fixture.budget / fleet.BUDGET)["reservations"]
    second = fixture.cycle()
    assert second["action"] == "install" and second["result"]["worker_installation_verified"]
    assert second["container_execution_verified"] is False
    assert fixture.cycle()["reason"] == "worker_readiness_or_original_intent_held"
    assert fixture.observations["installs"] == 1 and fixture.commands.active
    poll(fixture)
    assert fixture.cycle()["reason"] == "steady"
    assert len(fixture.cloud[0].writes) == 3 and not fixture.observer.deletions
    assert read_document(fixture.budget / fleet.BUDGET)["reservations"] == reservation
    state = read_document(fixture.budget / controller.JOURNAL)
    assert state["cycles"] == 4 and state["policy_sha256"] == fleet.digest(fixture.policy)
    assert state["last_action"]["action"] == "install"


@pytest.mark.parametrize("controller_fleet", [0], indirect=True)
def test_idle_retirement_checks_host_releases_budget_and_never_recycles_slot(controller_fleet):
    fixture = controller_fleet
    provision_install(fixture)
    poll(fixture)
    fixture.snapshot["jobs"] = []
    fixture.cloud[0].tags[0]["resources"] = {"count": 1, "droplets": {"count": 1}}
    result = fixture.cycle()
    assert result["action"] == "retire" and result["result"]["phase"] == "deleted"
    assert result["reserved_workers"] == 0 and result["reserved_hourly_usd"] == "0"
    assert fixture.observations["retirements"] == 1 and fixture.runtime.lifecycle.seals == 1
    assert not fixture.commands.active and len(fixture.observer.deletions) == 3
    assert read_document(fixture.budget / fleet.BUDGET)["reservations"][REGISTRATION["id"]]["status"] == "closed"
    fixture.snapshot["jobs"] = [queued()]
    assert fixture.cycle()["reason"] == "prepared_slots_exhausted"
    assert len(fixture.cloud[0].writes) == 6


@pytest.mark.parametrize("controller_fleet", [0], indirect=True)
def test_draining_worker_resumes_original_retirement_without_stopping_busy_job(controller_fleet):
    fixture = controller_fleet
    provision_install(fixture)
    poll(fixture)
    fixture.snapshot["jobs"] = []
    fixture.runtime.lifecycle.jobs = [{"id": "d" * 32, "status": "running"}]
    fixture.cloud[0].tags[0]["resources"] = {"count": 1, "droplets": {"count": 1}}
    assert fixture.cycle()["result"]["phase"] == "draining"
    assert fixture.commands.active and not fixture.observer.deletions
    fixture.snapshot["workers"][0]["draining"] = 1
    fixture.runtime.lifecycle.jobs = []
    result = fixture.cycle()
    assert result["reason"] == "original_cleanup_resumed" and result["result"]["phase"] == "deleted"
    assert fixture.observations["retirements"] == 2 and result["reserved_workers"] == 0


@pytest.mark.parametrize("failure", ["lost_before", "lost_after", "rejected", "redirect"])
def test_pending_paid_creation_recovers_only_original_identity_without_duplicate_post(controller_fleet, failure):
    fixture = controller_fleet
    fixture.cloud[0].fail["droplet"] = failure
    with pytest.raises(InstallError):
        fixture.cycle()
    reservation = read_document(fixture.budget / fleet.BUDGET)["reservations"][REGISTRATION["id"]]
    writes = len(fixture.cloud[0].writes)
    if failure == "lost_before":
        with pytest.raises(InstallError, match="uncertain"):
            fixture.cycle()
        assert len(fixture.cloud[0].writes) == writes and not fixture.observations["installs"]
    else:
        result = fixture.cycle()
        assert result["action"] == "resume" and result["result"]["phase"] == "ssh_verification_required"
        assert len(fixture.cloud[0].writes) == (writes + 1 if failure == "rejected" else writes)
        assert read_document(fixture.budget / fleet.BUDGET)["reservations"][REGISTRATION["id"]] == reservation
        assert fixture.cycle()["action"] == "install"


@pytest.mark.parametrize("controller_fleet", [0], indirect=True)
def test_withdrawn_demand_cancels_only_unpaid_original_setup(controller_fleet):
    fixture = controller_fleet
    fixture.cloud[0].firewall_status = "waiting"
    assert fixture.cycle()["result"]["phase"] == "firewall_pending"
    fixture.snapshot["jobs"] = []
    fixture.cloud[0].firewalls[0]["status"] = "succeeded"
    fixture.cloud[0].tags[0]["resources"] = {"count": 0, "droplets": {"count": 0}}
    result = fixture.cycle()
    assert result["action"] == "cancel" and result["result"]["phase"] == "cancelled"
    assert result["reserved_workers"] == 0 and not fixture.cloud[0].droplets
    assert not fixture.observations["installs"]
    fixture.snapshot["jobs"] = [queued()]
    assert fixture.cycle()["reason"] == "prepared_slots_exhausted"


def test_lost_install_receipt_recovers_exact_service_without_reinstallation(controller_fleet):
    fixture = controller_fleet
    fixture.cycle()

    def lost(*args):
        fixture.install(*args)
        raise InstallError("lost SSH response fixture")

    with pytest.raises(InstallError, match="lost SSH"):
        fixture.make().cycle(TOKEN, confirm_controller=fleet.digest(fixture.policy), install_host=lost, retire_host=fixture.retire)
    assert fixture.commands.active and read_document(fixture.cloud[7] / INSTALLATION)["phase"] == "installing"
    offset = len(fixture.commands.calls)
    assert fixture.cycle()["action"] == "install"
    assert len(fixture.commands.calls[offset:]) == 3 and len(fixture.cloud[0].writes) == 3


@pytest.mark.parametrize("controller_fleet", [0], indirect=True)
def test_uncertain_vm_deletion_is_not_retried_and_keeps_original_full_reservation(controller_fleet):
    fixture = controller_fleet
    provision_install(fixture)
    poll(fixture)
    fixture.snapshot["jobs"] = []
    fixture.cloud[0].tags[0]["resources"] = {"count": 1, "droplets": {"count": 1}}
    fixture.observer.failures["droplet"] = "lost_before"
    with pytest.raises(InstallError):
        fixture.cycle()
    before = read_document(fixture.budget / fleet.BUDGET)["reservations"]
    assert read_document(fixture.cloud[7] / RETIREMENT)["deletion"]["droplet"] == "request_pending"
    assert fixture.cycle()["result"]["phase"] == "deletion_pending"
    assert len(fixture.observer.deletions) == 1
    assert read_document(fixture.budget / fleet.BUDGET)["reservations"] == before


@pytest.mark.parametrize("change", ["worker-stale", "revoked", "draining", "retired", "catalog", "worker-resources"])
def test_unready_or_changed_worker_does_not_authorize_more_hardware(controller_fleet, change):
    fixture = controller_fleet
    provision_install(fixture)
    poll(fixture)
    worker = fixture.snapshot["workers"][0]
    if change == "worker-stale":
        worker["last_seen_at"] -= 100
    elif change == "revoked":
        worker["revoked_at"] = int(time.time())
    elif change == "draining":
        worker["draining"] = 1
    elif change == "retired":
        worker["revoked_at"] = int(time.time())
        worker["retired_at"] = int(time.time())
        worker["retirement_id"] = "e" * 32
    elif change == "catalog":
        fixture.snapshot["images"][0]["source_revision"] = "0" * 40
    else:
        worker["resources"]["cpu"] += 1
    assert fixture.cycle()["reason"] == "worker_readiness_or_original_intent_held"
    assert len(fixture.cloud[0].writes) == 3 and fixture.observations["installs"] == 1


def test_policy_flags_are_authority_not_advisory_output(controller_fleet):
    fixture = controller_fleet
    disabled = {**fixture.policy, "allow_provision": False, "allow_retirement": False, "delete_host_data": False}
    first = fixture.make(disabled)
    assert fixture.cycle(first)["reason"] == "paid_creation_not_authorized" and not fixture.cloud[0].writes
    with pytest.raises(InstallError, match="replacement"):
        fixture.cycle()
    assert not fixture.cloud[0].writes
    assert fixture.cycle(replace_controller=fleet.digest(disabled))["action"] == "provision"
    assert read_document(fixture.budget / controller.JOURNAL)["policy"] == fixture.policy


@pytest.mark.parametrize("change", ["fleet", "scope", "identity", "release", "bundle", "known-public"])
def test_changed_pinned_policy_inputs_refuse_before_cloud_creation(controller_fleet, change):
    fixture = controller_fleet
    if change == "fleet":
        value = read_document(fixture.fleet_path)
        value["maximum_workers"] = 1
        write_document(fixture.fleet_path, value)
    elif change == "scope":
        value = read_document(Path(fixture.policy["fleet_path"]))
        plan = Path(value["workers"][0]["plan"])
        document = read_document(plan)
        document["deployment_id"] = "0" * 32
        write_document(plan, document)
    elif change == "identity":
        Path(fixture.policy["identity"]).write_bytes(b"changed SSH identity")
    elif change in {"release", "bundle"}:
        fixture.policy["release_id" if change == "release" else "bundle_sha256"] = "0" * 64
    else:
        Path(fixture.policy["known_hosts"]).chmod(0o666)
    with pytest.raises(InstallError):
        fixture.make()
    assert not fixture.cloud[0].writes and not fixture.budget.exists()


@pytest.mark.parametrize("change", ["public", "symlink", "hash", "count", "action"])
def test_corrupt_controller_journal_is_not_replaced_or_used_as_authority(controller_fleet, change):
    fixture = controller_fleet
    fixture.cycle()
    path = fixture.budget / controller.JOURNAL
    if change == "public":
        path.chmod(0o644)
    elif change == "symlink":
        path.rename(path.with_name("original-controller"))
        path.symlink_to(path.with_name("original-controller"))
    else:
        value = read_document(path)
        if change == "hash":
            value["policy_sha256"] = "0" * 64
        elif change == "count":
            value["cycles"] = True
        else:
            value["last_action"]["action"] = "delete-everything"
        write_document(path, value)
    original = path.read_bytes()
    with pytest.raises(InstallError):
        fixture.cycle()
    assert path.read_bytes() == original and not fixture.observations["installs"]


def test_live_controller_lock_excludes_manual_fleet_operations(controller_fleet):
    fixture = controller_fleet

    def before_post(kind, body):
        assert read_document(fixture.budget / controller.JOURNAL)["last_action"]["phase"] == "pending"
        assert read_document(fixture.budget / fleet.BUDGET)["reservations"][REGISTRATION["id"]]["status"] == "reserved"
        with pytest.raises(InstallError, match="another process"):
            fixture.cycle()

    fixture.cloud[0].before_post = before_post
    assert fixture.cycle()["action"] == "provision"


@pytest.mark.parametrize("value", [True, 1, None])
def test_retirement_requires_exact_boolean_data_destruction_authority(controller_fleet, value):
    fixture = controller_fleet
    if value is True:
        policy = {**fixture.policy, "allow_retirement": False, "delete_host_data": value}
    else:
        policy = {**fixture.policy, "delete_host_data": value}
    with pytest.raises(InstallError):
        controller.validate_policy(policy)


def test_controller_cli_plan_never_constructs_provider(controller_fleet, bundle, monkeypatch, capsys):
    fixture = controller_fleet
    target = fixture.fleet_path.parent / "cli-policy.json"
    monkeypatch.setattr(cli, "DigitalOcean", lambda token: pytest.fail("planning must not access provider"))
    assert (
        cli.main(
            [
                "do-worker-controller-plan",
                "--fleet",
                str(fixture.fleet_path),
                "--fleet-state-dir",
                str(fixture.budget),
                "--bundle",
                str(bundle),
                "--identity",
                fixture.policy["identity"],
                "--known-hosts",
                fixture.policy["known_hosts"],
                "--allow-provision",
                "--allow-retirement",
                "--confirm-delete-host-data",
                "--output",
                str(target),
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["policy_sha256"] == fleet.digest(read_document(target)) and result["creates_resources"] is False
    assert not fixture.budget.exists()


def cli_run(fixture):
    session, token = fixture.fleet_path.parent / "session", fixture.fleet_path.parent / "token"
    private_file(session, TOKEN.encode())
    private_file(token, fixture.cloud[0].token.encode())
    return [
        "do-worker-controller-run",
        "--policy",
        str(fixture.policy_path),
        "--confirm-controller",
        fleet.digest(fixture.policy),
        "--session-file",
        str(session),
        "--token-file",
        str(token),
    ]


@pytest.mark.parametrize(
    "flag,value",
    [
        ("--confirm-controller", "0" * 64),
        ("--cycles", "0"),
        ("--cycles", "10001"),
        ("--interval-seconds", "9"),
        ("--interval-seconds", "61"),
        ("--replace-controller", "invalid"),
    ],
)
def test_controller_cli_invalid_confirmation_and_loop_bounds_precede_provider(controller_fleet, monkeypatch, flag, value):
    arguments = cli_run(controller_fleet)
    if flag in arguments:
        arguments[arguments.index(flag) + 1] = value
    else:
        arguments += [flag, value]
    monkeypatch.setattr(cli, "DigitalOcean", lambda token: pytest.fail("provider must not be accessed"))
    with pytest.raises(SystemExit) as error:
        cli.main(arguments)
    assert error.value.code == 2 and not controller_fleet.budget.exists()


def test_cli_loop_uses_pinned_worker_inputs_and_emits_one_secret_free_result_per_cycle(controller_fleet, monkeypatch, capsys):
    from reasoning_ledger.deployment import scaling, worker_install, worker_provision

    fixture = controller_fleet
    arguments = cli_run(fixture) + ["--cycles", "3", "--interval-seconds", "10"]
    sleeps = []
    verify = worker_provision.verify_remote_binding
    monkeypatch.setattr(scaling, "read_snapshot", fixture.reader)
    monkeypatch.setattr(worker_provision, "verify_remote_binding", lambda config, **kwargs: verify(config, client=fixture.cloud[1].client))
    monkeypatch.setattr(worker_install, "verify_remote_binding", lambda config, **kwargs: verify(config, client=fixture.cloud[1].client))
    monkeypatch.setattr(cli, "DigitalOcean", lambda token: fixture.cloud[0].client)
    monkeypatch.setattr(cli.time, "sleep", sleeps.append)

    def ssh(snapshot, origin, registration, catalog, address, identity, known, **kwargs):
        config, images = json.loads(registration.read_bytes()), json.loads(catalog.read_bytes())
        assert kwargs["confirmed_worker"] == config["id"] and origin == ORIGIN
        assert registration.stat().st_mode & 0o777 == 0o600 and catalog.stat().st_mode & 0o777 == 0o600
        assert config["token"] == TOKEN and snapshot != Path(fixture.policy["bundle_path"])
        assert not kwargs["destruction"] and kwargs["operation"] == "install"
        result = fixture.install(SimpleNamespace(config=config, images=images), address, kwargs["provisioning"])
        poll(fixture)
        return result

    monkeypatch.setattr(cli, "install_worker_remote", ssh)
    assert cli.main(arguments) == 0
    output = capsys.readouterr().out
    results = [json.loads(line) for line in output.splitlines()]
    assert [row["action"] for row in results] == ["provision", "install", None]
    assert results[-1]["reason"] == "steady" and sleeps == [10, 10]
    assert TOKEN not in output and fixture.cloud[0].token not in output


@pytest.mark.parametrize("controller_fleet", [0], indirect=True)
@pytest.mark.parametrize("moment", ["before-reservation", "before-paid-post"])
def test_changed_demand_is_rechecked_before_reservation_and_paid_creation(controller_fleet, moment):
    fixture = controller_fleet
    instance = fixture.make()
    if moment == "before-reservation":
        count = 0

        def snapshot(*args):
            nonlocal count
            count += 1
            if count == 2:
                fixture.snapshot["jobs"] = []
            return fixture.reader(*args)

        instance.snapshot_reader = snapshot
    else:

        def before_post(kind, body):
            if kind == "firewall":
                fixture.snapshot["jobs"] = []

        fixture.cloud[0].before_post = before_post
    with pytest.raises(InstallError, match="no longer recommends"):
        fixture.cycle(instance)
    assert not fixture.cloud[0].droplets
    assert all(req.url.path != "/v2/droplets" for req in fixture.cloud[0].writes)
    assert len(fixture.cloud[0].writes) == (0 if moment == "before-reservation" else 2)
    row = read_document(fixture.budget / fleet.BUDGET)["reservations"][REGISTRATION["id"]]
    assert row["status"] == ("unreserved" if moment == "before-reservation" else "reserved")


def test_disabled_paid_growth_cannot_complete_a_free_resource_only_reservation(controller_fleet):
    fixture = controller_fleet
    fixture.cloud[0].firewall_status = "waiting"
    assert fixture.cycle()["result"]["phase"] == "firewall_pending"
    fixture.cloud[0].firewalls[0]["status"] = "succeeded"
    disabled = {**fixture.policy, "allow_provision": False}
    result = fixture.cycle(fixture.make(disabled), replace_controller=fleet.digest(fixture.policy))
    assert result["reason"] == "paid_creation_not_authorized" and not fixture.cloud[0].droplets
    assert len(fixture.cloud[0].writes) == 2


@pytest.mark.parametrize("controller_fleet", [0], indirect=True)
def test_new_queue_requirement_prevents_obsolete_idle_retirement(controller_fleet):
    fixture = controller_fleet
    provision_install(fixture)
    poll(fixture)
    fixture.snapshot["jobs"] = []
    instance = fixture.make()
    count = 0

    def snapshot(*args):
        nonlocal count
        count += 1
        if count == 2:
            fixture.snapshot["jobs"] = [queued()]
        return fixture.reader(*args)

    instance.snapshot_reader = snapshot
    assert fixture.cycle(instance)["reason"] == "retirement_no_longer_recommended"
    assert not fixture.observer.deletions and not fixture.observations["retirements"]
    assert not (fixture.cloud[7] / RETIREMENT).exists()


def test_fleet_amendment_needs_explicit_controller_replacement_and_keeps_birth_budget(controller_fleet, bundle):
    from reasoning_ledger.deployment.fleet_amend import amend

    fixture = controller_fleet
    provision_install(fixture)
    poll(fixture)
    original = read_document(fixture.budget / fleet.BUDGET)["reservations"]
    following = read_document(fixture.fleet_path)
    following["maximum_workers"] = 1
    path = fixture.fleet_path.parent / "next-fleet.json"
    write_document(path, following, new=True)
    amend(fixture.make(), path, confirm_fleet=fixture.policy["fleet_sha256"], confirm_next_fleet=fleet.digest(following))
    with pytest.raises(InstallError, match="superseded"):
        fixture.cycle()
    policy = controller.plan_controller(
        path,
        fixture.budget,
        bundle,
        Path(fixture.policy["identity"]),
        Path(fixture.policy["known_hosts"]),
        allow_provision=True,
        allow_retirement=True,
        delete_host_data=True,
    )
    instance = fixture.make(policy)
    with pytest.raises(InstallError, match="replacement"):
        fixture.cycle(instance)
    result = fixture.cycle(instance, replace_controller=fleet.digest(fixture.policy))
    assert result["reason"] == "steady" and result["fleet_sha256"] == fleet.digest(following)
    budget = read_document(fixture.budget / fleet.BUDGET)
    assert budget["reservations"] == original and budget["origins"][REGISTRATION["id"]] == fixture.policy["fleet_sha256"]
    assert len(fixture.cloud[0].writes) == 3 and not fixture.observer.deletions


@pytest.mark.parametrize("failure", ["revoked", "timeout", "redirect", "malformed"])
def test_unavailable_live_enrollment_prevents_new_provider_writes(controller_fleet, failure):
    fixture = controller_fleet
    fixture.cloud[1].failure = failure
    with pytest.raises(InstallError, match="enrollment"):
        fixture.cycle()
    assert not fixture.cloud[0].writes and not fixture.observations["installs"]


@pytest.mark.parametrize("cap,value", [("maximum_workers", 0), ("maximum_hourly_usd", "0.034")])
def test_original_fleet_count_and_quote_ceiling_prevent_controller_growth(controller_fleet, bundle, cap, value):
    fixture = controller_fleet
    specification = read_document(fixture.fleet_path)
    specification["minimum_workers"] = 0
    specification[cap] = value
    write_document(fixture.fleet_path, specification)
    fixture.snapshot["jobs"] = [queued()]
    policy = controller.plan_controller(
        fixture.fleet_path,
        fixture.budget,
        bundle,
        Path(fixture.policy["identity"]),
        Path(fixture.policy["known_hosts"]),
        allow_provision=True,
    )
    assert fixture.cycle(fixture.make(policy))["reason"] == "capacity_or_budget_unmet"
    assert not fixture.cloud[0].writes


@pytest.mark.parametrize("pending", ["retire", "cancel", "resume"])
def test_scheduler_prioritizes_later_pending_slot_over_earlier_uninstalled_vm(controller_fleet, pending, monkeypatch):
    """Pure scheduling check; mutation/lock behavior uses the lifecycle fixtures above."""
    fixture = controller_fleet
    instance = fixture.make()
    first = instance.profiles[0]
    directory = fixture.fleet_path.parent / "later-worker"
    directory.mkdir(mode=0o700)
    second = SimpleNamespace(
        config={"id": "c" * 32},
        identity="2" * 64,
        fleet_paths={"state_dir": str(directory)},
        state={"resources": {"droplet": {"status": "request_pending", "id": None}}},
    )
    instance.profiles.append(second)
    instance.state = {
        "reservations": {profile.config["id"]: {"status": "reserved", "reservation_id": "3" * 64} for profile in instance.profiles}
    }
    if pending != "resume":
        write_document(directory / (RETIREMENT if pending == "retire" else CANCELLATION), {"scheduler_fixture": True})
    selected = []
    monkeypatch.setattr(instance, "_checkpoint", lambda action, profile, phase: selected.append((action, profile.config["id"])))
    monkeypatch.setattr(instance, "_installer", lambda profile: pytest.fail("pending slot must precede installation"))
    monkeypatch.setattr(instance, "_retire", lambda *args: {"phase": "draining"})
    monkeypatch.setattr(instance, "_cancel", lambda *args: {"phase": "cancelled"})
    monkeypatch.setattr(instance, "_apply_locked", lambda *args, **kwargs: {"phase": "ssh_verification_required"})
    action, _, _ = instance._cycle(
        [{"worker_id": first.config["id"], "phase": "active"}, {"worker_id": second.config["id"], "phase": "uncertain"}],
        fixture.snapshot,
        {"reconcile_workers": [], "actions": []},
        fixture.install,
        fixture.retire,
    )
    assert action == pending and selected == [(pending, second.config["id"]), (pending, second.config["id"])]
    assert not fixture.cloud[0].writes
