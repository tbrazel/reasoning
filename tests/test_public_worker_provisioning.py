"""Enrollment/provider HTTP fixtures, not live VM provisioning or container execution."""

import copy
import fcntl
import json
import os
from pathlib import Path

import httpx
import pytest
from test_public_provisioning import ProviderFixture
from test_public_worker_deployment import IMAGE, ORIGIN, REGISTRATION, TOKEN

from reasoning_ledger.deployment import cli, worker_provision
from reasoning_ledger.deployment.digitalocean import DigitalOcean
from reasoning_ledger.deployment.host import InstallError, canonical
from reasoning_ledger.deployment.private import read_document, write_document
from reasoning_ledger.deployment.provision import Provisioner, plan_identity, save_plan
from reasoning_ledger.deployment.worker_provision import (
    WorkerProvisioner,
    plan_worker,
    validate_worker_plan,
    worker_inputs,
    worker_plan_identity,
)
from reasoning_ledger.server.compute_worker import WorkerClient

PARAMETERS = {
    "name": "algebra",
    "region": "fra1",
    "size": "s-2vcpu-4gb",
    "ssh_key_id": 123,
    "admin_cidr": "203.0.113.10/32",
    "maximum_hourly_usd": "0.10",
}


class WorkerIdentity:
    def __init__(self, config):
        self.record = {key: value for key, value in copy.deepcopy(REGISTRATION).items() if key != "token"}
        self.requests, self.failure = [], None
        self.client = WorkerClient(config, transport=httpx.MockTransport(self.handle))

    def handle(self, request):
        self.requests.append(request)
        assert request.url.scheme == "https" and request.url.host == "chat.example.org"
        assert request.method == "GET" and request.url.path == "/api/worker/identity" and request.content == b""
        assert request.headers["authorization"] == "Bearer " + TOKEN
        if self.failure == "timeout":
            raise httpx.ReadTimeout(TOKEN, request=request)
        if self.failure == "redirect":
            return httpx.Response(302, headers={"location": "https://attacker.example.org"})
        if self.failure == "revoked":
            return httpx.Response(401, json={"detail": TOKEN})
        if self.failure == "malformed":
            return httpx.Response(200, content=TOKEN)
        return httpx.Response(200, json=self.record)


@pytest.fixture
def worker_cloud(tmp_path):
    remote = ProviderFixture()
    config, images = worker_inputs(ORIGIN, REGISTRATION, [IMAGE])
    identity = WorkerIdentity(config)
    plan = plan_worker(remote.client, config, images, identity_client=identity.client, **PARAMETERS)
    saved = save_plan(tmp_path / "worker-plan.json", plan, validator=validate_worker_plan)
    document = read_document(Path(saved["plan_file"]))
    instance = WorkerProvisioner(remote.client, document, config, images, identity_client=identity.client)
    directory = tmp_path / "worker-ownership"
    yield remote, identity, config, images, saved, document, instance, directory
    remote.client.close()
    identity.client.close()


def apply(fixture):
    return fixture[6].apply(fixture[7], fixture[4]["plan_id"])


def restart(fixture):
    remote, identity, config, images, saved, document, _, directory = fixture
    return WorkerProvisioner(remote.client, document, config, images, identity_client=identity.client).apply(directory, saved["plan_id"])


def test_worker_plan_is_readonly_enrollment_bound_private_and_needs_no_dns_zone(worker_cloud):
    remote, identity, config, _images, saved, document, _, _directory = worker_cloud
    plan = document["plan"]
    assert not remote.writes and len(identity.requests) == 1
    assert {request.url.path for request in remote.requests} == {"/v2/account", "/v2/sizes", "/v2/account/keys/123"}
    assert plan["purpose"] == "compute-worker" and plan["creates_resources"] is False
    assert plan["enrollment"]["worker_id"] == config["id"] and plan["enrollment"]["organization_id"] == config["organization_id"]
    assert not {"public_url", "dns_zone", "dns_record_name"} & plan.keys()
    assert Path(saved["plan_file"]).stat().st_mode & 0o777 == 0o600
    assert TOKEN not in json.dumps(document) and remote.token not in json.dumps(document)
    assert worker_plan_identity(document) == saved["plan_id"]
    with pytest.raises(InstallError):
        plan_identity(document)  # A server command must never interpret a worker plan.
    with pytest.raises(InstallError, match="already exists"):
        save_plan(Path(saved["plan_file"]), plan, validator=validate_worker_plan)


def test_worker_creation_has_only_ssh_inbound_no_dns_no_secrets_and_resumes_without_writes(worker_cloud):
    remote, identity, config, _, saved, document, instance, directory = worker_cloud

    def pending(kind, _body):
        state = read_document(directory / "digitalocean.json")
        assert state["plan_id"] == saved["plan_id"] and state["resources"][kind] == {"status": "request_pending", "id": None}

    remote.before_post = pending
    result = apply(worker_cloud)
    assert result["purpose"] == "compute-worker" and result["phase"] == "ssh_verification_required"
    assert result["ssh_host"] == "139.59.1.2" and result["worker_id"] == config["id"]
    assert result["worker_installation_verified"] is False and result["container_execution_verified"] is False
    assert result["provider_charges_possible"] is True and "public_url" not in result
    assert [request.url.path for request in remote.writes] == ["/v2/tags", "/v2/firewalls", "/v2/droplets"]
    firewall = json.loads(remote.writes[1].content)
    assert firewall["inbound_rules"] == [{"protocol": "tcp", "ports": "22", "sources": {"addresses": [PARAMETERS["admin_cidr"]]}}]
    assert firewall["tags"] == [instance.marker] and instance.marker.startswith("reasoning-compute-")
    body = json.loads(remote.writes[2].content)
    assert body["name"].startswith("reasoning-compute-") and body["ssh_keys"] == [123]
    assert body["backups"] is False and body["ipv6"] is False and body["monitoring"] is False
    user_data = json.loads(body["user_data"].split("\n", 1)[1])
    assert user_data["ssh_pwauth"] is False and len(user_data["write_files"]) == 1
    assert config["id"] not in body["user_data"] and ORIGIN not in body["user_data"]
    assert TOKEN not in json.dumps(body) + json.dumps(result) + json.dumps(document)
    assert not remote.records and all("/domains/" not in request.url.path for request in remote.requests)
    state = read_document(directory / "digitalocean.json")
    assert set(state["resources"]) == {"tag", "firewall", "droplet"} and TOKEN not in json.dumps(state)
    assert directory.stat().st_mode & 0o777 == 0o700
    assert restart(worker_cloud) == result and len(remote.writes) == 3 and len(identity.requests) == 6


@pytest.mark.parametrize("kind", ["tag", "firewall", "droplet"])
@pytest.mark.parametrize("failure", ["lost_after", "invalid_json", "redirect"])
def test_lost_worker_creation_receipts_are_reconciled_without_duplicate_writes(worker_cloud, kind, failure):
    remote = worker_cloud[0]
    remote.fail[kind] = failure
    with pytest.raises(InstallError) as error:
        apply(worker_cloud)
    assert remote.token not in str(error.value) and TOKEN not in str(error.value)
    assert read_document(worker_cloud[7] / "digitalocean.json")["resources"][kind]["status"] == "request_pending"
    assert restart(worker_cloud)["phase"] == "ssh_verification_required" and len(remote.writes) == 3


@pytest.mark.parametrize("kind", ["tag", "firewall", "droplet"])
def test_uncertain_absent_worker_resources_are_not_blindly_created_again(worker_cloud, kind):
    remote = worker_cloud[0]
    remote.fail[kind] = "lost_before"
    with pytest.raises(InstallError):
        apply(worker_cloud)
    count = len(remote.writes)
    with pytest.raises(InstallError, match="uncertain"):
        restart(worker_cloud)
    assert len(remote.writes) == count


@pytest.mark.parametrize("kind", ["tag", "firewall", "droplet"])
def test_definitively_rejected_worker_creation_resumes_without_forgetting_prior_ownership(worker_cloud, kind):
    remote = worker_cloud[0]
    remote.fail[kind] = "rejected"
    with pytest.raises(InstallError, match="rejected"):
        apply(worker_cloud)
    assert read_document(worker_cloud[7] / "digitalocean.json")["resources"][kind] == {"status": "not_started", "id": None}
    assert restart(worker_cloud)["phase"] == "ssh_verification_required" and len(remote.writes) == 4


@pytest.mark.parametrize("field", ["token", "id", "organization_id", "image_ids", "resources", "server_url", "catalog"])
def test_changed_private_worker_inputs_cannot_rebind_saved_plan_or_touch_provider(worker_cloud, field):
    remote, identity, config, images, _, document, _, directory = worker_cloud
    config, images = copy.deepcopy(config), copy.deepcopy(images)
    if field == "catalog":
        images[0]["sbom_digest"] = "sha256:" + "1" * 64
    elif field == "resources":
        config["resources"]["wall_time_seconds"] = 119
    elif field == "server_url":
        config[field] = "https://other.example.org"
    elif field == "image_ids":
        config[field] = ["other"]
    else:
        config[field] = "c" * 40 if field == "token" else "c" * 32
    before, reads = len(remote.requests), len(identity.requests)
    with pytest.raises(InstallError):
        WorkerProvisioner(remote.client, document, config, images, identity_client=identity.client)
    assert not directory.exists() and len(remote.requests) == before and len(identity.requests) == reads


@pytest.mark.parametrize("change", ["account", "team", "price", "memory", "key", "availability", "unverified"])
def test_live_worker_provider_drift_refuses_all_new_creation(worker_cloud, change):
    remote = worker_cloud[0]
    if change == "account":
        remote.account["uuid"] = "b" * 40
    elif change == "team":
        remote.account["team"] = {"uuid": "b" * 40}
    elif change == "price":
        remote.size["price_hourly"] = 0.04
    elif change == "memory":
        remote.size["memory"] = 8192
    elif change == "key":
        remote.key["public_key"] += "-changed"
    elif change == "availability":
        remote.size["available"] = False
    else:
        remote.account["email_verified"] = False
    with pytest.raises(InstallError):
        apply(worker_cloud)
    assert not remote.writes


@pytest.mark.parametrize("failure", ["revoked", "redirect", "timeout", "malformed"])
def test_remote_worker_enrollment_must_verify_before_any_provider_request(worker_cloud, failure):
    remote, identity, *_ = worker_cloud
    identity.failure = failure
    before = len(remote.requests)
    with pytest.raises(InstallError) as error:
        apply(worker_cloud)
    assert TOKEN not in str(error.value) and len(remote.requests) == before and not remote.writes


@pytest.mark.parametrize("field,value", [("id", "c" * 32), ("organization_id", "c" * 32), ("draining", True), ("revoked_at", 1)])
def test_remote_worker_identity_drift_never_creates_hardware(worker_cloud, field, value):
    remote, identity, *_ = worker_cloud
    identity.record[field] = value
    before = len(remote.requests)
    with pytest.raises(InstallError):
        apply(worker_cloud)
    assert len(remote.requests) == before and not remote.writes


def test_drained_worker_can_be_provisioned_without_claiming_or_renewing_any_job(worker_cloud):
    worker_cloud[1].record["draining"] = 1
    assert apply(worker_cloud)["phase"] == "ssh_verification_required"
    assert all(request.url.path == "/api/worker/identity" for request in worker_cloud[1].requests)


def test_revocation_during_free_firewall_creation_prevents_billable_vm_creation(worker_cloud):
    remote, identity, *_ = worker_cloud
    remote.before_post = lambda kind, body: setattr(identity, "failure", "revoked") if kind == "firewall" else None
    with pytest.raises(InstallError):
        apply(worker_cloud)
    assert len(remote.writes) == 2 and not remote.droplets
    state = read_document(worker_cloud[7] / "digitalocean.json")
    assert state["resources"]["droplet"] == {"status": "not_started", "id": None}
    identity.failure = None
    assert restart(worker_cloud)["phase"] == "ssh_verification_required" and len(remote.writes) == 3


@pytest.mark.parametrize("change", ["cpu", "memory", "image_maximum"])
def test_worker_plan_refuses_host_and_image_allowances_that_cannot_run_enrolled_jobs(worker_cloud, change):
    remote, identity, config, images, *_ = worker_cloud
    config, images = copy.deepcopy(config), copy.deepcopy(images)
    if change == "cpu":
        config["resources"]["cpu"] = 3
        images[0]["maximum_resources"]["cpu"] = 3
    elif change == "memory":
        config["resources"]["memory_bytes"] = 4 * 1024**3
        images[0]["maximum_resources"]["memory_bytes"] = 4 * 1024**3
    else:
        images[0]["maximum_resources"]["wall_time_seconds"] = 119
    identity.record["resources"] = copy.deepcopy(config["resources"])
    with pytest.raises(InstallError):
        plan_worker(remote.client, config, images, identity_client=identity.client, **PARAMETERS)
    assert not remote.writes


def test_larger_cpu_memory_worker_uses_selected_shape_and_still_creates_only_one_vm(worker_cloud, tmp_path, monkeypatch):
    remote, identity, config, images, *_ = worker_cloud
    config, images = copy.deepcopy(config), copy.deepcopy(images)
    config["resources"].update(cpu=8, memory_bytes=16 * 1024**3)
    images[0]["maximum_resources"] = copy.deepcopy(config["resources"])
    identity.record["resources"] = copy.deepcopy(config["resources"])
    remote.size.update(slug="c-8", vcpus=8, memory=32768, disk=200, price_hourly=0.40)
    create = remote.create

    def selected_shape(kind, body):
        row = create(kind, body)
        if kind == "droplet":
            row.update(vcpus=remote.size["vcpus"], memory=remote.size["memory"], disk=remote.size["disk"])
        return row

    monkeypatch.setattr(remote, "create", selected_shape)
    plan = plan_worker(
        remote.client, config, images, identity_client=identity.client, **{**PARAMETERS, "size": "c-8", "maximum_hourly_usd": "0.50"}
    )
    assert plan["resources"] == {"vcpus": 8, "memory_mib": 32768, "disk_gib": 200}
    saved = save_plan(tmp_path / "large-worker.json", plan, validator=validate_worker_plan)
    document = read_document(Path(saved["plan_file"]))
    instance = WorkerProvisioner(remote.client, document, config, images, identity_client=identity.client)
    result = instance.apply(tmp_path / "large-ownership", saved["plan_id"])
    assert result["phase"] == "ssh_verification_required" and len(remote.droplets) == 1
    assert json.loads(remote.writes[-1].content)["size"] == "c-8" and len(remote.writes) == 3


@pytest.mark.parametrize("regions", ["fra1", [], [True], None])
def test_worker_size_requires_structured_region_availability_not_a_substring_match(worker_cloud, regions):
    remote, identity, config, images, *_ = worker_cloud
    remote.size["regions"] = regions
    with pytest.raises(InstallError):
        plan_worker(remote.client, config, images, identity_client=identity.client, **PARAMETERS)
    assert not remote.writes


def test_decreased_live_worker_quote_does_not_modify_confirmed_plan(worker_cloud):
    remote = worker_cloud[0]
    remote.size["price_hourly"] = 0.02
    assert apply(worker_cloud)["phase"] == "ssh_verification_required"
    assert worker_cloud[5]["plan"]["quote"]["compute_hourly_usd"] == "0.035"
    assert worker_plan_identity(worker_cloud[5]) == worker_cloud[4]["plan_id"]


@pytest.mark.parametrize("change", ["purpose", "dns", "token", "cpu_bool", "hash", "ids", "image", "quote"])
def test_saved_worker_plan_strict_schema_rejects_drift_and_secret_fields(worker_cloud, change):
    plan = copy.deepcopy(worker_cloud[5]["plan"])
    if change == "purpose":
        plan["purpose"] = "server"
    elif change == "dns":
        plan["dns_zone"] = "example.org"
    elif change == "token":
        plan["enrollment"]["token"] = TOKEN
    elif change == "cpu_bool":
        plan["enrollment"]["resources"]["cpu"] = True
    elif change == "hash":
        plan["enrollment"]["enrollment_sha256"] = "invalid"
    elif change == "ids":
        plan["enrollment"]["image_ids"] *= 2
    elif change == "image":
        plan["image"] = "ubuntu-22-04-x64"
    else:
        plan["quote"]["compute_hourly_usd"] = "NaN"
    with pytest.raises(InstallError):
        validate_worker_plan(plan)


def test_worker_readiness_observes_firewall_application_and_active_vm_not_creation_receipts(worker_cloud):
    remote = worker_cloud[0]
    remote.firewall_status = "waiting"
    result = apply(worker_cloud)
    assert result["phase"] == "firewall_pending" and result["provider_charges_possible"] is False and len(remote.writes) == 2
    remote.firewalls[0]["status"] = "succeeded"
    remote.droplet_status = "new"
    result = restart(worker_cloud)
    assert result["phase"] == "droplet_pending" and result["provider_charges_possible"] is True and len(remote.writes) == 3
    remote.droplets[0]["status"] = "active"
    assert restart(worker_cloud)["phase"] == "ssh_verification_required" and len(remote.writes) == 3


@pytest.mark.parametrize("change", ["extra", "missing", "waiting", "failed", "open_ssh", "web_port"])
def test_worker_readiness_requires_exact_applied_ssh_only_firewall(worker_cloud, change):
    remote = worker_cloud[0]

    def modify(kind, _body):
        if kind != "droplet":
            return
        if change == "extra":
            remote.extra_firewalls = [{"id": "66666666-2222-4333-8444-555555555555"}]
        elif change == "missing":
            remote.association_visible = False
        elif change in {"waiting", "failed"}:
            remote.firewalls[0]["status"] = change
        elif change == "open_ssh":
            remote.firewalls[0]["inbound_rules"][0]["sources"]["addresses"] = ["0.0.0.0/0"]
        else:
            rule = copy.deepcopy(remote.firewalls[0]["inbound_rules"][0])
            rule["ports"] = "443"
            remote.firewalls[0]["inbound_rules"].append(rule)

    remote.before_post = modify
    if change in {"waiting", "missing"}:
        assert apply(worker_cloud)["phase"] == "firewall_pending"
    else:
        with pytest.raises(InstallError):
            apply(worker_cloud)
    assert len(remote.writes) == 3 and not remote.records


@pytest.mark.parametrize("kind", ["tag", "firewall", "droplet"])
def test_worker_never_replaces_missing_owned_resources(worker_cloud, kind):
    remote = worker_cloud[0]
    apply(worker_cloud)
    getattr(remote, {"tag": "tags", "firewall": "firewalls", "droplet": "droplets"}[kind]).clear()
    with pytest.raises(InstallError, match="uncertain"):
        restart(worker_cloud)
    assert len(remote.writes) == 3


@pytest.mark.parametrize("change", ["unmanaged", "duplicate", "identity", "configuration", "journal"])
def test_worker_ownership_collision_or_drift_is_never_adopted_or_recreated(worker_cloud, change):
    remote, _, _, _, _, _, instance, directory = worker_cloud
    if change == "unmanaged":
        remote.tags.append({"name": instance.marker, "resources": {"count": 0}})
    elif change == "duplicate":
        remote.fail["droplet"] = "lost_after"
        with pytest.raises(InstallError):
            apply(worker_cloud)
        duplicate = copy.deepcopy(remote.droplets[0])
        duplicate["id"] = 457
        remote.droplets.append(duplicate)
    else:
        apply(worker_cloud)
        if change == "identity":
            remote.get_id_override = 457
        elif change == "configuration":
            remote.droplets[0]["tags"].append("unrelated")
        else:
            state = read_document(directory / "digitalocean.json")
            state["resources"]["droplet"]["id"] = True
            write_document(directory / "digitalocean.json", state)
    count = len(remote.writes)
    with pytest.raises(InstallError):
        restart(worker_cloud)
    assert len(remote.writes) == count


def test_worker_exclusive_lock_and_wrong_confirmation_prevent_provider_and_identity_reads(worker_cloud):
    remote, identity, _, _, _, _, instance, directory = worker_cloud
    before, reads = len(remote.requests), len(identity.requests)
    with pytest.raises(InstallError, match="confirm-plan"):
        instance.apply(directory, "0" * 64)
    assert not directory.exists()
    directory.mkdir(mode=0o700)
    fd = os.open(directory / "provision.lock", os.O_RDONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(InstallError, match="another process"):
            apply(worker_cloud)
    finally:
        os.close(fd)
    assert len(remote.requests) == before and len(identity.requests) == reads


def test_server_and_worker_journals_cannot_rebind_each_other(worker_cloud, tmp_path):
    remote, identity, _, _, _, _, _, directory = worker_cloud
    apply(worker_cloud)
    saved = save_plan(tmp_path / "server-plan.json", remote.plan())
    server = Provisioner(remote.client, read_document(Path(saved["plan_file"])))
    before, reads = len(remote.requests), len(identity.requests)
    with pytest.raises(InstallError):
        server.apply(directory, saved["plan_id"])
    assert len(remote.requests) == before and len(identity.requests) == reads


def test_worker_apply_cli_refuses_wrong_confirmation_before_loading_secrets_or_clients(worker_cloud, monkeypatch, capsys):
    saved, directory = worker_cloud[4], worker_cloud[7]
    monkeypatch.setattr(cli, "DigitalOcean", lambda _: pytest.fail("provider must not be initialized"))
    with pytest.raises(SystemExit) as error:
        cli.main(
            [
                "do-worker-apply",
                "--plan",
                saved["plan_file"],
                "--confirm-plan",
                "0" * 64,
                "--token-file",
                "absent",
                "--server-url",
                ORIGIN,
                "--registration",
                "absent",
                "--catalog",
                "absent",
                "--state-dir",
                str(directory),
                "--wait-seconds",
                "0",
            ]
        )
    assert error.value.code == 2 and "confirm-plan" in capsys.readouterr().err and not directory.exists()


def test_worker_plan_and_apply_cli_keep_tokens_private_and_require_exact_inputs(tmp_path, monkeypatch, capsys):
    remote = ProviderFixture()
    config, _ = worker_inputs(ORIGIN, REGISTRATION, [IMAGE])
    identity = WorkerIdentity(config)
    token, registration, catalog = (tmp_path / name for name in ("do-token", "registration.json", "images.json"))
    for path, body in ((token, remote.token.encode()), (registration, canonical(REGISTRATION)), (catalog, canonical([IMAGE]))):
        path.write_bytes(body)
        path.chmod(0o600)
    monkeypatch.setattr(cli, "DigitalOcean", lambda value: DigitalOcean(value, transport=httpx.MockTransport(remote.handle)))
    monkeypatch.setattr(worker_provision, "WorkerClient", lambda value: WorkerClient(value, transport=httpx.MockTransport(identity.handle)))
    common = ["--token-file", str(token), "--server-url", ORIGIN, "--registration", str(registration), "--catalog", str(catalog)]
    plan_file = tmp_path / "cli-plan.json"
    assert (
        cli.main(
            [
                "do-worker-plan",
                *common,
                "--name",
                "algebra",
                "--region",
                "fra1",
                "--size",
                "s-2vcpu-4gb",
                "--ssh-key-id",
                "123",
                "--admin-cidr",
                PARAMETERS["admin_cidr"],
                "--maximum-hourly-usd",
                "0.10",
                "--output",
                str(plan_file),
            ]
        )
        == 0
    )
    saved = json.loads(capsys.readouterr().out)
    assert not remote.writes and TOKEN not in json.dumps(saved) and remote.token not in json.dumps(saved)
    command = [
        "do-worker-apply",
        *common,
        "--plan",
        str(plan_file),
        "--confirm-plan",
        saved["plan_id"],
        "--state-dir",
        str(tmp_path / "cli-ownership"),
        "--wait-seconds",
        "0",
    ]
    assert cli.main(command) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["worker_installation_verified"] is False and result["ssh_host"] == "139.59.1.2" and len(remote.writes) == 3
    assert cli.main(command) == 0 and len(remote.writes) == 3
    capsys.readouterr()
    registration.write_bytes(canonical({**REGISTRATION, "token": "other-private-" + "z" * 40}))
    before = len(remote.requests)
    with pytest.raises(SystemExit) as error:
        cli.main(command)
    assert error.value.code == 2 and len(remote.requests) == before
    output = capsys.readouterr().err
    assert TOKEN not in output and remote.token not in output
    remote.client.close()
    identity.client.close()
