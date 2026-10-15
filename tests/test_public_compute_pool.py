"""Real server enrollment plus provider/host fixtures; not live cloud or CAS proof."""

import copy
import hashlib
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from test_public_compute_scaler import bridge, grant
from test_public_compute_ui import IMAGE, ORIGIN
from test_public_compute_ui import fixture as fixture
from test_public_deployment import bundle as bundle
from test_public_deployment import payload as payload
from test_public_provisioning import ProviderFixture
from test_public_worker_deployment import Commands

from reasoning_ledger.deployment import cli, controller, enrollment, fleet, pool, scaling, worker_host, worker_provision
from reasoning_ledger.deployment.host import InstallError, canonical
from reasoning_ledger.deployment.private import locked_directory, read_document, read_private, write_document, write_private
from reasoning_ledger.deployment.provision import save_plan
from reasoning_ledger.deployment.ssh_host import worker_trust
from reasoning_ledger.deployment.worker_retire import WorkerRetirer
from reasoning_ledger.server.compute_catalog import DEFAULT_RESOURCES
from reasoning_ledger.server.compute_worker import ComputeWorker, WorkerClient, drain_status


class Provider(ProviderFixture):
    def __init__(self):
        super().__init__()
        self.serial, self.deletions = 0, []

    def create(self, kind, body):
        self.serial += 1
        if kind == "firewall":
            row = {
                **copy.deepcopy(body),
                "id": f"{self.serial:08x}-2222-4333-8444-555555555555",
                "status": self.firewall_status,
                "pending_changes": [],
                "droplet_ids": [],
            }
            self.firewalls.append(row)
            return row
        if kind == "droplet":
            firewall = next(row for row in self.firewalls if row["tags"] == body["tags"])
            first = self.firewalls[0]
            row = super().create(kind, body)
            row["id"] = 450 + self.serial
            row["networks"]["v4"][0]["ip_address"] = f"139.59.1.{self.serial}"
            first["droplet_ids"] = [vm["id"] for vm in self.droplets if vm["tags"] == first["tags"]]
            firewall["droplet_ids"] = [row["id"]]
            for tag in self.tags:
                count = sum(tag["name"] in vm["tags"] for vm in self.droplets)
                tag["resources"] = {"count": count, "droplets": {"count": count}}
            return row
        return super().create(kind, body)

    def handle(self, request):
        path = request.url.path.removeprefix("/v2/")
        if request.method == "GET" and path.split("/")[0] in {"droplets", "firewalls", "tags"} and "/" in path:
            self.requests.append(request)
            collection, identifier, *tail = path.split("/")
            rows = getattr(self, collection)
            row = next((row for row in rows if str(row.get("id", row.get("name"))) == identifier), None)
            if row is None:
                return httpx.Response(404, json={"message": "absent fixture"})
            if tail:
                assert tail == ["firewalls"]
                return httpx.Response(200, json={"firewalls": [fw for fw in self.firewalls if row["id"] in fw["droplet_ids"]]})
            return httpx.Response(200, json={collection[:-1]: copy.deepcopy(row)})
        if request.method == "GET" and path in {"images", "volumes", "snapshots", "databases"}:
            self.requests.append(request)
            return httpx.Response(200, json={path: []})
        if request.method == "DELETE":
            self.requests.append(request)
            collection, identifier = path.split("/")
            self.deletions.append((collection, identifier))
            rows = getattr(self, collection)
            rows[:] = [row for row in rows if str(row.get("id", row.get("name"))) != identifier]
            for tag in self.tags:
                count = sum(tag["name"] in vm["tags"] for vm in self.droplets)
                tag["resources"] = {"count": count, "droplets": {"count": count}}
            return httpx.Response(204)
        return super().handle(request)


@pytest.fixture
def managed_pool(fixture, bundle, tmp_path, monkeypatch, request):
    store, app, owner, _, _ = fixture
    minimum = getattr(request, "param", 2)
    scoped = grant(fixture, maximum_workers=8)
    transport = bridge(app)
    client = enrollment.ScalerClient(ORIGIN, scoped["token"], transport=transport)
    remote = Provider()
    configuration_client = WorkerClient
    monkeypatch.setattr(worker_provision, "WorkerClient", lambda config: configuration_client(config, transport=transport))
    initial_dir = tmp_path / "original"
    initial_dir.mkdir(mode=0o700)
    enrollment.enroll_worker(
        client,
        initial_dir / "enrollment",
        ORIGIN,
        owner["organization_id"],
        "Original",
        [IMAGE["id"]],
        DEFAULT_RESOURCES,
        initial_dir / "registration.json",
        initial_dir / "catalog.json",
    )
    config, images = worker_provision.worker_inputs(ORIGIN, read_document(initial_dir / "registration.json"), [IMAGE])
    shape = worker_provision.plan_worker(
        remote.client,
        config,
        images,
        pin_ssh_host_key=True,
        name="algebra",
        region="fra1",
        size="s-2vcpu-4gb",
        ssh_key_id=123,
        admin_cidr="203.0.113.10/32",
        maximum_hourly_usd="0.10",
    )
    saved = save_plan(initial_dir / "plan.json", shape, validator=worker_provision.validate_worker_plan)
    original_paths = {
        "plan": saved["plan_file"],
        "registration": str(initial_dir / "registration.json"),
        "catalog": str(initial_dir / "catalog.json"),
        "state_dir": str(initial_dir / "ownership"),
    }
    specification = {
        "format": 1,
        "server_url": ORIGIN,
        "organization_id": owner["organization_id"],
        "minimum_workers": minimum,
        "maximum_workers": 3,
        "maximum_hourly_usd": "0.105",
        "idle_seconds": 60,
        "workers": [original_paths],
    }
    fleet_path = tmp_path / "fleet.json"
    write_document(fleet_path, specification)
    identity = tmp_path / "ssh-identity"
    write_private(identity, b"private operator identity fixture")
    budget = tmp_path / "fleet-state"
    initial_policy = controller.plan_controller(
        fleet_path, budget, bundle, identity, allow_provision=True, allow_retirement=True, delete_host_data=True
    )
    policy = pool.plan_pool(initial_policy, tmp_path / "pool-state", [config["id"]], scoped["id"], 4, pin_ssh_host_key=True)
    state = tmp_path / "pool-policy.json"
    write_document(state, policy)
    hosts, callbacks = {}, []
    environment = {"commands": None}

    def account(name):
        assert name == worker_host.ACCOUNT
        if environment["commands"] is None or not environment["commands"].account:
            raise KeyError(name)
        return SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid(), pw_dir=worker_host.DATA, pw_shell="/usr/sbin/nologin")

    monkeypatch.setattr(worker_host.pwd, "getpwnam", account)

    def install(profile, address, binding):
        callbacks.append(("install", profile.config["id"]))
        worker = profile.config["id"]
        if worker not in hosts:
            root = tmp_path / ("host-" + worker)
            root.mkdir(mode=0o700)
            (root / "etc").mkdir(mode=0o755)
            body = json.loads(
                next(
                    req.content
                    for req in remote.writes
                    if req.url.path.endswith("/droplets") and json.loads(req.content)["tags"] == [profile.marker]
                )
            )
            cloud_init = json.loads(body["user_data"].split("\n", 1)[1])
            marker = json.loads(cloud_init["write_files"][0]["content"])
            write_document(root / "etc/reasoning-provisioning.json", marker)
            commands = Commands()
            installer = worker_host.WorkerHostInstaller(root=root, command=commands)
            monkeypatch.setattr(installer, "_droplet_id", lambda: binding["droplet_id"])
            hosts[worker] = installer, commands
        installer, commands = hosts[worker]
        environment["commands"] = commands
        with worker_trust(profile.document, Path(profile.fleet_paths["state_dir"]), address, binding) as known:
            assert known.read_text().startswith(address + " ssh-ed25519 ")
            result = installer.install_worker(
                bundle,
                hashlib.sha256(bundle.read_bytes()).hexdigest(),
                ORIGIN,
                profile.config,
                profile.images,
                skip_platform=True,
                provisioning=binding,
            )
        app.state.compute.claim(profile.config["token"])
        return result

    def retire(profile, address, binding):
        pytest.fail("these fixture checks must not delete a host without explicit lifecycle setup")

    snapshot_reader = scaling.read_snapshot

    def reader(origin, organization, credential):
        return snapshot_reader(origin, organization, credential, transport=transport)

    def make(policy_override=None, **kwargs):
        return pool.WorkerPool(remote.client, policy_override or policy, bundle=bundle, snapshot_reader=reader, **kwargs)

    def cycle(instance=None, **kwargs):
        instance = instance or make()
        return instance.cycle(
            scoped["token"],
            confirm_pool=fleet.digest(instance.policy),
            client=client,
            install_host=install,
            retire_host=retire,
            **kwargs,
        )

    yield SimpleNamespace(
        make=make,
        cycle=cycle,
        policy=policy,
        policy_path=state,
        remote=remote,
        client=client,
        original=config,
        paths=original_paths,
        budget=budget,
        directory=Path(policy["directory"]),
        initial=initial_policy,
        hosts=hosts,
        callbacks=callbacks,
        server=fixture,
        grant=scoped,
        reader=reader,
        install=install,
        retire=retire,
        transport=transport,
    )
    client.close()
    remote.client.close()


def initial_ready(fixture):
    assert fixture.cycle()["action"] == "provision"
    assert fixture.cycle()["action"] == "install"


def test_pool_plan_is_local_secret_free_and_separately_confirmed(managed_pool):
    item = managed_pool
    assert not item.remote.writes and not item.directory.exists() and not item.budget.exists()
    assert item.grant["token"] not in json.dumps(item.policy) and item.original["token"] not in json.dumps(item.policy)
    with pytest.raises(InstallError, match="confirm"):
        item.make().cycle(
            item.grant["token"], confirm_pool="0" * 64, client=item.client, install_host=item.install, retire_host=item.retire
        )
    assert not item.directory.exists() and not item.remote.writes


def test_fresh_enrollment_plan_amendment_and_activation_preserve_original_budget(managed_pool):
    item = managed_pool
    initial_ready(item)
    before = read_document(item.budget / fleet.BUDGET)
    original_binding = read_private(Path(item.paths["state_dir"]) / fleet.BINDING)
    writes = len(item.remote.writes)
    prepared = item.cycle()
    assert prepared["action"] == "replenish" and prepared["fresh_workers"] == 1
    assert not prepared["result"]["creates_cloud_resources"] and len(item.remote.writes) == writes
    state = read_document(item.directory / pool.JOURNAL)
    assert state["pending"] is None and state["current_policy"]["bundle_path"] == item.initial["bundle_path"]
    after = read_document(item.budget / fleet.BUDGET)
    assert after["reservations"][item.original["id"]] == before["reservations"][item.original["id"]]
    assert read_private(Path(item.paths["state_dir"]) / fleet.BINDING) == original_binding
    assert after["history"][-1]["scope"] == before["scope"]
    fresh = prepared["result"]["worker_id"]
    assert fresh != item.original["id"] and after["reservations"][fresh]["status"] == "unreserved"
    assert item.cycle()["action"] == "provision"
    assert item.cycle()["action"] == "install"
    assert item.cycle()["reason"] == "steady"
    assert len(item.remote.droplets) == 2 and len(item.callbacks) == 2
    assert item.grant["token"] not in json.dumps(prepared) and item.original["token"] not in json.dumps(state)


@pytest.mark.parametrize(
    "change", ["no-consent", "unknown-template", "duplicates", "limit-zero", "limit-bool", "limit-large", "budget-overlap"]
)
def test_invalid_pool_authority_is_refused_locally(managed_pool, change):
    item = managed_pool
    args = dict(
        initial_policy=item.initial,
        directory=item.directory,
        template_workers=[item.original["id"]],
        scaler_id=item.grant["id"],
        maximum_new_workers=4,
        pin_ssh_host_key=True,
    )
    changes = {
        "no-consent": ("pin_ssh_host_key", False),
        "unknown-template": ("template_workers", ["0" * 32]),
        "duplicates": ("template_workers", [item.original["id"]] * 2),
        "limit-zero": ("maximum_new_workers", 0),
        "limit-bool": ("maximum_new_workers", True),
        "limit-large": ("maximum_new_workers", 65),
        "budget-overlap": ("directory", item.budget / "nested-pool"),
    }
    key, value = changes[change]
    args[key] = value
    with pytest.raises(InstallError):
        pool.plan_pool(**args)
    assert not item.remote.writes and not item.directory.exists()


@pytest.mark.parametrize("moment", ["enrollment", "saved-plan", "prepared-amendment", "applied-amendment", "saved-pool"])
def test_interrupted_generation_recovers_original_credentials_plans_and_budget(managed_pool, monkeypatch, moment):
    from reasoning_ledger.deployment import fleet_amend

    item = managed_pool
    initial_ready(item)
    before = read_document(item.budget / fleet.BUDGET)["reservations"][item.original["id"]]
    remaining = [True]

    def interrupt():
        if remaining.pop() if remaining else False:
            raise InstallError("lost durable pool step fixture")

    if moment == "enrollment":
        original = enrollment.enroll_worker

        def enroll(*args, **kwargs):
            result = original(*args, **kwargs)
            interrupt()
            return result

        monkeypatch.setattr(enrollment, "enroll_worker", enroll)
    elif moment == "saved-plan":
        original = pool.save_plan

        def save(*args, **kwargs):
            result = original(*args, **kwargs)
            interrupt()
            return result

        monkeypatch.setattr(pool, "save_plan", save)
    elif moment == "prepared-amendment":
        original = fleet_amend.write_document

        def write(path, value, **kwargs):
            result = original(path, value, **kwargs)
            if path == item.budget / fleet.BUDGET:
                interrupt()
            return result

        monkeypatch.setattr(fleet_amend, "write_document", write)
    elif moment == "applied-amendment":
        original = pool.amend

        def amendment(*args, **kwargs):
            result = original(*args, **kwargs)
            interrupt()
            return result

        monkeypatch.setattr(pool, "amend", amendment)
    else:
        original = pool.write_document

        def write(path, value, **kwargs):
            result = original(path, value, **kwargs)
            if path == item.directory / pool.JOURNAL and value["workers"]:
                interrupt()
            return result

        monkeypatch.setattr(pool, "write_document", write)
    with pytest.raises(InstallError, match="lost durable"):
        item.cycle()
    paths = item.make()._paths(1)
    original_intent = read_private(Path(paths["plan"]).parent / "enrollment" / enrollment.INTENT)
    original_plan = read_private(Path(paths["plan"])) if Path(paths["plan"]).exists() else None
    assert len(item.server[1].state.compute.workers(item.server[2]["user_id"], item.server[2]["organization_id"])) == 2
    resumed = item.cycle()
    assert resumed["action"] == ("provision" if moment == "saved-pool" else "replenish")
    assert read_private(Path(paths["plan"]).parent / "enrollment" / enrollment.INTENT) == original_intent
    if original_plan is not None:
        assert read_private(Path(paths["plan"])) == original_plan
    assert read_document(item.budget / fleet.BUDGET)["reservations"][item.original["id"]] == before
    assert len(item.server[1].state.compute.workers(item.server[2]["user_id"], item.server[2]["organization_id"])) == 2
    assert len(item.remote.droplets) == (2 if moment == "saved-pool" else 1)


def test_lost_real_enrollment_http_response_resumes_the_same_server_worker(managed_pool):
    item = managed_pool
    initial_ready(item)
    responses = []

    def lose(req, reply):
        if req.method == "POST":
            assert read_document(item.directory / pool.JOURNAL)["pending"]["index"] == 1
            responses.append(reply.json()["worker"]["id"])
            if len(responses) == 1:
                raise httpx.ReadError("private body fixture", request=req)

    remote = enrollment.ScalerClient(ORIGIN, item.grant["token"], transport=bridge(item.server[1], lose))
    try:
        with pytest.raises(InstallError, match="uncertain"):
            item.make().cycle(
                item.grant["token"],
                confirm_pool=fleet.digest(item.policy),
                client=remote,
                install_host=item.install,
                retire_host=item.retire,
            )
        result = item.make().cycle(
            item.grant["token"],
            confirm_pool=fleet.digest(item.policy),
            client=remote,
            install_host=item.install,
            retire_host=item.retire,
        )
    finally:
        remote.close()
    assert responses == [result["result"]["worker_id"]] * 2 and len(item.remote.droplets) == 1


@pytest.mark.parametrize(
    "change",
    ["pool-hash", "previous-controller", "policy-budget", "pending-index", "permissions", "missing-journal", "missing-budget", "binding"],
)
def test_pool_journal_or_binding_drift_never_resets_or_enrolls(managed_pool, change):
    item = managed_pool
    initial_ready(item)
    path = item.directory / pool.JOURNAL
    value = read_document(path)
    if change == "pool-hash":
        value["pool_sha256"] = "0" * 64
    elif change == "previous-controller":
        value["previous_controller"] = "0" * 64
    elif change == "policy-budget":
        value["current_policy"]["fleet_state_dir"] = str(item.budget.parent / "wrong-budget")
    elif change == "pending-index":
        value["pending"] = {"index": 2, "template_worker": item.original["id"], "source_policy_sha256": fleet.digest(item.initial)}
    elif change == "permissions":
        path.chmod(0o644)
    elif change == "missing-journal":
        path.rename(item.directory / "preserved-pool.json")
    elif change == "missing-budget":
        (item.budget / fleet.BUDGET).rename(item.budget / "preserved-budget.json")
    elif change == "binding":
        binding = item.budget / pool.BINDING
        record = read_document(binding)
        record["pool_sha256"] = "0" * 64
        write_document(binding, record)
    if change in {"pool-hash", "previous-controller", "policy-budget", "pending-index"}:
        write_document(path, value)
    before = len(item.remote.writes)
    with pytest.raises(InstallError):
        item.cycle()
    assert len(item.remote.writes) == before
    assert len(item.server[1].state.compute.workers(item.server[2]["user_id"], item.server[2]["organization_id"])) == 1


@pytest.mark.parametrize("authority", ["another-grant", "revoked", "catalog", "resource-limit"])
def test_changed_scaler_authority_does_not_mint_replacement_credentials(managed_pool, authority):
    item = managed_pool
    initial_ready(item)
    if authority == "another-grant":
        following = grant(item.server, maximum_workers=8)
        credential = following["token"]
    else:
        credential = item.grant["token"]
    owner = item.server[2]
    if authority == "revoked":
        item.server[1].state.compute_scalers.revoke(owner["user_id"], owner["organization_id"], item.grant["id"])
    client = enrollment.ScalerClient(ORIGIN, credential, transport=item.transport)
    if authority in {"catalog", "resource-limit"}:
        native = client.request

        def changed(operation, body=None):
            value = native(operation, body)
            if operation == "identity":
                if authority == "catalog":
                    value["images"][0]["source_revision"] = "0" * 40
                else:
                    value["resources"]["memory_bytes"] = 256 * 1024**2
            return value

        client.request = changed
    try:
        with pytest.raises(InstallError):
            item.make().cycle(
                item.grant["token"],
                confirm_pool=fleet.digest(item.policy),
                client=client,
                install_host=item.install,
                retire_host=item.retire,
            )
    finally:
        client.close()
    assert len(item.server[1].state.compute.workers(owner["user_id"], owner["organization_id"])) == 1
    assert len(item.remote.droplets) == 1


def retire_original(item):
    specification, profiles = scaling.load_fleet(Path(item.initial["fleet_path"]))
    profile = profiles[0]
    installer, commands = item.hosts[profile.config["id"]]
    enabled = [True]

    class NoContainers:
        def verify_runtime_identity(self):
            pass

        def cleanup(self):
            pass

        def verify_cleanup(self):
            pass

    def lifecycle(args, *, check=True):
        result = commands(args, check=check)
        value = None
        if args[0] == "/usr/bin/docker" and "info" in args:
            return subprocess.CompletedProcess(args, 0, stdout=canonical({"ID": "pool-fixture-daemon"}))
        if args[:3] == ["systemctl", "disable", "--now"]:
            commands.active, enabled[0] = False, False
        if args[:2] == ["systemctl", "show"] and "--property=ActiveState" in args:
            return subprocess.CompletedProcess(args, 0, stdout=b"active\n" if commands.active else b"inactive\n")
        if args[:2] == ["systemctl", "is-enabled"]:
            return subprocess.CompletedProcess(args, 0 if enabled[0] else 1, stdout=b"enabled\n" if enabled[0] else b"disabled\n")
        if args[0] == "runuser":
            client = WorkerClient(profile.config, transport=item.transport)
            try:
                if "drain" in args:
                    client.request("POST", "/api/worker/drain", {})
                    value = drain_status(profile.config, client.request("GET", "/api/worker/status"))
                elif "retire" in args:
                    value = ComputeWorker(Path(args[args.index("--data-dir") + 1]), profile.config, client, NoContainers()).retire()
            finally:
                client.close()
        return subprocess.CompletedProcess(args, 0, stdout=canonical(value)) if value is not None else result

    installer.command = lifecycle
    write_document(
        installer.root / "var/lib/reasoning-compute/docker-runtime.json",
        {
            "format": 1,
            "worker_id": profile.config["id"],
            "socket": "/run/docker.sock",
            "daemon_id": "pool-fixture-daemon",
        },
    )
    archive = Path(item.initial["bundle_path"])
    release = item.initial["release_id"]
    executable = installer.root / "opt/reasoning-compute/releases" / release / "venv/bin/reasoning-compute-worker"
    executable.parent.mkdir(parents=True)
    executable.parent.chmod(0o755)
    executable.parent.parent.chmod(0o755)
    write_private(executable, b"nonexecuting fixture command\n")
    executable.chmod(0o755)
    ownership = Path(profile.fleet_paths["state_dir"])
    resource = read_document(ownership / "digitalocean.json")["resources"]["droplet"]["id"]
    result = WorkerRetirer(item.remote.client, profile.document, profile.config, profile.images, archive).retire(
        ownership,
        confirm_plan=profile.identity,
        confirm_worker=profile.config["id"],
        confirm_release=release,
        confirm_droplet=resource,
        confirm_delete_host_data=True,
        prepare_host=lambda address, binding: installer.retire_worker(
            archive,
            hashlib.sha256(archive.read_bytes()).hexdigest(),
            specification["server_url"],
            profile.config,
            profile.images,
            skip_platform=True,
            destruction=binding,
        ),
    )
    assert result["phase"] == "deleted" and not commands.active
    return result


def test_terminal_worker_is_archived_and_replaced_without_recycling_budget_or_enrollment(managed_pool):
    item = managed_pool
    initial_ready(item)
    assert item.cycle()["action"] == "replenish"
    assert item.cycle()["action"] == "provision"
    assert item.cycle()["action"] == "install"
    original = read_document(item.budget / fleet.BUDGET)
    retired = retire_original(item)
    assert len(item.remote.droplets) == 1
    prepared = item.cycle()
    assert prepared["action"] == "replenish" and prepared["fresh_workers"] == 2
    following = read_document(item.budget / fleet.BUDGET)
    assert item.original["id"] not in following["reservations"]
    historical = next(frame for frame in following["history"] if frame["reservations"][item.original["id"]]["status"] == "closed")
    assert (
        historical["reservations"][item.original["id"]]["reservation_id"] == original["reservations"][item.original["id"]]["reservation_id"]
    )
    assert read_document(Path(item.paths["state_dir"]) / "digitalocean-retirement.json")["deletion"]["droplet"] == "absent"
    assert prepared["result"]["worker_id"] not in original["reservations"]
    assert item.cycle()["action"] == "provision" and item.cycle()["action"] == "install"
    assert item.cycle()["reason"] == "steady" and len(item.remote.droplets) == 2
    assert retired["resources"]["droplet"] not in {vm["id"] for vm in item.remote.droplets}


def test_pool_and_original_budget_locks_exclude_competing_cycles(managed_pool):
    item = managed_pool
    initial_ready(item)
    for directory in (item.directory, item.budget):
        with locked_directory(directory), pytest.raises(InstallError, match="another process"):
            item.cycle()
    assert len(item.remote.droplets) == 1
    assert len(item.server[1].state.compute.workers(item.server[2]["user_id"], item.server[2]["organization_id"])) == 1


@pytest.mark.parametrize("managed_pool", [3], indirect=True)
def test_lifetime_enrollment_ceiling_stops_replenishment_without_resetting_generations(managed_pool):
    item = managed_pool
    item.policy["maximum_new_workers"] = 1
    initial_ready(item)
    assert item.cycle()["action"] == "replenish"
    assert item.cycle()["action"] == "provision" and item.cycle()["action"] == "install"
    result = item.cycle()
    assert result["reason"] == "authorized_fresh_enrollment_limit_reached" and result["fresh_workers"] == 1
    assert len(item.remote.droplets) == 2
    assert item.cycle()["reason"] == result["reason"]


def test_quote_budget_does_not_mint_unaffordable_worker_credentials(managed_pool):
    item = managed_pool
    specification = read_document(Path(item.initial["fleet_path"]))
    specification["maximum_hourly_usd"] = "0.035"
    write_document(Path(item.initial["fleet_path"]), specification)
    item.policy["initial_policy"] = controller.plan_controller(
        Path(item.initial["fleet_path"]),
        item.budget,
        Path(item.initial["bundle_path"]),
        Path(item.initial["identity"]),
        allow_provision=True,
        allow_retirement=True,
        delete_host_data=True,
    )
    initial_ready(item)
    result = item.cycle()
    assert result["reason"] == "no_authorized_fresh_capacity" and result["fresh_workers"] == 0
    assert len(item.server[1].state.compute.workers(item.server[2]["user_id"], item.server[2]["organization_id"])) == 1


def cli_run(item):
    provider, scaler = item.directory.parent / "provider-token", item.directory.parent / "scaler-token"
    for path, value in ((provider, item.remote.token), (scaler, item.grant["token"])):
        write_private(path, value.encode())
    return [
        "do-worker-pool-run",
        "--policy",
        str(item.policy_path),
        "--token-file",
        str(provider),
        "--scaler-token-file",
        str(scaler),
        "--confirm-pool",
        fleet.digest(item.policy),
    ]


@pytest.mark.parametrize(
    "flag,value",
    [("--confirm-pool", "0" * 64), ("--cycles", "0"), ("--cycles", "10001"), ("--interval-seconds", "9"), ("--interval-seconds", "61")],
)
def test_invalid_pool_cli_authority_precedes_provider_access(managed_pool, monkeypatch, flag, value):
    item = managed_pool
    args = cli_run(item)
    if flag in args:
        args[args.index(flag) + 1] = value
    else:
        args += [flag, value]
    monkeypatch.setattr(cli, "DigitalOcean", lambda token: pytest.fail("invalid authority must not access provider"))
    with pytest.raises(SystemExit) as error:
        cli.main(args)
    assert error.value.code == 2 and not item.directory.exists()


def test_cli_loop_replenishes_with_one_authority_and_preserves_original_bundle_path(managed_pool, monkeypatch, capsys):
    item = managed_pool
    args = cli_run(item) + ["--cycles", "6", "--interval-seconds", "10"]
    actual = pool.WorkerPool
    monkeypatch.setattr(
        pool, "WorkerPool", lambda provider, policy, **kwargs: actual(provider, policy, snapshot_reader=item.reader, **kwargs)
    )
    monkeypatch.setattr(enrollment, "ScalerClient", lambda origin, credential: item.client)
    monkeypatch.setattr(cli, "DigitalOcean", lambda token: item.remote.client)
    sleeps = []
    monkeypatch.setattr(cli.time, "sleep", sleeps.append)

    def ssh(archive, origin, registration, catalog, address, identity, known, **kwargs):
        state = read_document(item.directory / pool.JOURNAL)
        _, profiles = scaling.load_fleet(Path(state["current_policy"]["fleet_path"]))
        worker = json.loads(registration.read_bytes())["id"]
        profile = next(profile for profile in profiles if profile.config["id"] == worker)
        assert known.read_text().startswith(address + " ssh-ed25519 ")
        assert archive != Path(item.initial["bundle_path"]) and kwargs["confirmed_release"] == item.initial["release_id"]
        assert json.loads(catalog.read_bytes()) == profile.images
        return item.install(profile, address, kwargs["provisioning"])

    monkeypatch.setattr(cli, "install_worker_remote", ssh)
    assert cli.main(args) == 0
    output = capsys.readouterr().out
    values = [json.loads(line) for line in output.splitlines()]
    assert [value["action"] for value in values] == ["provision", "install", "replenish", "provision", "install", None]
    assert values[-1]["reason"] == "steady" and sleeps == [10] * 5
    assert read_document(item.directory / pool.JOURNAL)["current_policy"]["bundle_path"] == item.initial["bundle_path"]
    assert item.grant["token"] not in output and item.original["token"] not in output and item.remote.token not in output


@pytest.mark.parametrize("managed_pool", [1], indirect=True)
def test_heterogeneous_templates_choose_capacity_compatible_with_actual_queued_jobs(managed_pool):
    item = managed_pool
    directory = item.directory.parent / "smaller"
    directory.mkdir(mode=0o700)
    allowance = {**DEFAULT_RESOURCES, "memory_bytes": 512 * 1024**2}
    enrollment.enroll_worker(
        item.client,
        directory / "enrollment",
        ORIGIN,
        item.server[2]["organization_id"],
        "Smaller",
        [IMAGE["id"]],
        allowance,
        directory / "registration.json",
        directory / "catalog.json",
    )
    config, images = worker_provision.worker_inputs(ORIGIN, read_document(directory / "registration.json"), [IMAGE])
    plan = worker_provision.plan_worker(
        item.remote.client,
        config,
        images,
        pin_ssh_host_key=True,
        name="smaller",
        region="fra1",
        size="s-2vcpu-4gb",
        ssh_key_id=123,
        admin_cidr="203.0.113.10/32",
        maximum_hourly_usd="0.10",
    )
    saved = save_plan(directory / "plan.json", plan, validator=worker_provision.validate_worker_plan)
    path = Path(item.initial["fleet_path"])
    specification = read_document(path)
    specification["workers"].append(
        {
            "plan": saved["plan_file"],
            "registration": str(directory / "registration.json"),
            "catalog": str(directory / "catalog.json"),
            "state_dir": str(directory / "ownership"),
        }
    )
    write_document(path, specification)
    item.policy["initial_policy"] = controller.plan_controller(
        path,
        item.budget,
        Path(item.initial["bundle_path"]),
        Path(item.initial["identity"]),
        allow_provision=True,
        allow_retirement=True,
        delete_host_data=True,
    )
    item.policy["template_workers"] = sorted([item.original["id"], config["id"]])
    app, owner, topics = item.server[1:4]
    for identifier in ("one", "two"):
        app.state.compute.enqueue(
            owner["user_id"],
            owner["organization_id"],
            topics[2],
            identifier,
            app.state.compute.parameters(IMAGE["id"], "sage", "# nonexecuting fixture", DEFAULT_RESOURCES),
        )
    initial_ready(item)
    result = item.cycle()
    assert result["action"] == "replenish"
    record = read_document(item.directory / pool.JOURNAL)["workers"][0]
    assert record["template_worker"] == item.original["id"]
    assert len(item.remote.droplets) == 1  # The incompatible smaller standby was not billed.
    assert item.cycle()["action"] == "provision" and item.cycle()["action"] == "install"
    assert len(item.remote.droplets) == 2 and item.cycle()["reason"] == "steady"


@pytest.mark.parametrize("change", ["price", "account", "key", "size"])
def test_provider_drift_prevents_fresh_enrollment_and_preserves_reservations(managed_pool, change):
    item = managed_pool
    initial_ready(item)
    before = read_document(item.budget / fleet.BUDGET)
    if change == "price":
        item.remote.size["price_hourly"] = 0.07
    elif change == "account":
        item.remote.account["uuid"] = "b" * 40
    elif change == "key":
        item.remote.key["public_key"] += " changed"
    else:
        item.remote.size["vcpus"] += 1
    with pytest.raises(InstallError):
        item.cycle()
    assert read_document(item.budget / fleet.BUDGET) == before and len(item.remote.droplets) == 1
    assert len(item.server[1].state.compute.workers(item.server[2]["user_id"], item.server[2]["organization_id"])) == 1


def test_another_pool_cannot_split_the_original_budget_authority(managed_pool):
    item = managed_pool
    initial_ready(item)
    policy = copy.deepcopy(item.policy)
    policy["directory"] = str(item.directory.parent / "another-pool")
    instance = item.make(policy)
    with pytest.raises(InstallError, match="another pool"):
        item.cycle(instance)
    assert len(item.remote.droplets) == 1


def test_pool_cli_planning_is_local_and_persists_exact_parent_authority(managed_pool, monkeypatch, capsys):
    item = managed_pool
    parent = item.directory.parent / "initial-controller.json"
    write_document(parent, item.initial)
    output = item.directory.parent / "another-policy.json"
    monkeypatch.setattr(cli, "DigitalOcean", lambda token: pytest.fail("pool planning must be local"))
    assert (
        cli.main(
            [
                "do-worker-pool-plan",
                "--controller-policy",
                str(parent),
                "--pool-state-dir",
                str(item.directory),
                "--template-worker",
                item.original["id"],
                "--scaler-id",
                item.grant["id"],
                "--maximum-new-workers",
                "4",
                "--pin-ssh-host-key",
                "--output",
                str(output),
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert read_document(output) == item.policy and result["pool_sha256"] == fleet.digest(item.policy)
    assert result["creates_cloud_resources"] is False and result["enrolls_workers"] is False
    assert not item.directory.exists() and not item.budget.exists()
