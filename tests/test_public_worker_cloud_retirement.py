"""Provider/host lifecycle fixtures, not live paid resource destruction."""

import copy
import fcntl
import hashlib
import json
import os
import shlex
import subprocess
from pathlib import Path

import httpx
import pytest
from test_public_deployment import bundle as bundle
from test_public_deployment import payload as payload
from test_public_worker_deployment import IMAGE, ORIGIN, REGISTRATION, TOKEN
from test_public_worker_deployment import bootstrap as bootstrap
from test_public_worker_host_retirement import managed as managed
from test_public_worker_provisioning import apply
from test_public_worker_provisioning import worker_cloud as worker_cloud

from reasoning_ledger.deployment import cli, host, worker_host
from reasoning_ledger.deployment.digitalocean import DigitalOcean
from reasoning_ledger.deployment.private import read_document, write_document
from reasoning_ledger.deployment.worker_retire import JOURNAL, WorkerRetirer
from reasoning_ledger.server.compute_worker import write_state


class DeletionProvider:
    def __init__(self, remote, directory):
        self.remote, self.directory = remote, directory
        self.failures, self.denied_reads, self.pending = {}, set(), set()
        self.before_delete = None
        self.visibility = {"images": [], "volumes": [], "snapshots": [], "databases": []}

    @property
    def deletions(self):
        return [request for request in self.remote.requests if request.method == "DELETE"]

    def rows(self, collection):
        return getattr(self.remote, collection)

    def handle(self, request):
        path = request.url.path.removeprefix("/v2/")
        collection, _, identifier = path.partition("/")
        if request.method == "DELETE" or path in self.visibility or identifier and collection in {"tags", "firewalls", "droplets"}:
            self.remote.requests.append(request)
            assert request.url.host == "api.digitalocean.com" and request.url.scheme == "https"
            assert request.headers["authorization"] == "Bearer " + self.remote.token
            if request.method == "GET":
                if path in self.denied_reads:
                    return httpx.Response(403, json={"message": self.remote.token})
                if path in self.visibility:
                    return httpx.Response(200, json={path: self.visibility[path]})
                key = "name" if collection == "tags" else "id"
                matches = [row for row in self.rows(collection) if str(row[key]) == identifier]
                if not matches:
                    return httpx.Response(404, json={"message": self.remote.token})
                field = {"tags": "tag", "firewalls": "firewall", "droplets": "droplet"}[collection]
                return httpx.Response(200, json={field: matches[0]})
            assert request.method == "DELETE" and request.content == b""
            kind = {"tags": "tag", "firewalls": "firewall", "droplets": "droplet"}[collection]
            assert read_document(self.directory / JOURNAL)["deletion"][kind] == "request_pending"
            if self.before_delete:
                self.before_delete(kind)
            failure = self.failures.pop(kind, None)
            if failure == "rejected":
                return httpx.Response(403, json={"message": self.remote.token})
            if failure == "lost_before":
                raise httpx.ReadTimeout(self.remote.token, request=request)
            if kind not in self.pending:
                self.rows(collection).clear()
                if kind == "droplet":
                    self.remote.firewalls[0]["droplet_ids"] = []
                    self.remote.tags[0]["resources"]["droplets"]["count"] = 0
                    self.remote.tags[0]["resources"]["count"] -= 1
            if failure == "lost_after":
                raise httpx.ReadTimeout(self.remote.token, request=request)
            if failure == "body":
                return httpx.Response(204, content=self.remote.token)
            return httpx.Response(204)
        return self.remote.handle(request)


@pytest.fixture
def cloud_retirement(worker_cloud, bundle, managed, monkeypatch):
    apply(worker_cloud)
    remote, identity, config, images, saved, document, _, directory = worker_cloud
    remote.tags[0]["resources"] = {"count": 1, "droplets": {"count": 1}, "images": {"count": 0}}
    provider = DeletionProvider(remote, directory)
    remote.client.close()
    remote.client = DigitalOcean(remote.token, transport=httpx.MockTransport(provider.handle))
    controller = WorkerRetirer(remote.client, document, config, images, bundle)
    installer, lifecycle = managed
    request = {
        "format": 1,
        "plan_id": saved["plan_id"],
        "deployment_id": document["deployment_id"],
        "droplet_id": 456,
        "delete_host_data": True,
    }
    write_state(installer.root / "etc/reasoning-provisioning.json", {key: request[key] for key in ("plan_id", "deployment_id")})
    runtime = {"format": 1, "worker_id": config["id"], "socket": "/run/docker.sock", "daemon_id": "fixture-daemon"}
    write_state(installer.root / "var/lib/reasoning-compute/docker-runtime.json", runtime)
    monkeypatch.setattr(installer, "_droplet_id", lambda: 456)
    observations = {"calls": 0, "containers": b"", "daemon_id": "fixture-daemon", "after_id": "fixture-daemon", "info_calls": 0}

    def command(args, *, check=True):
        response = lifecycle(args, check=check)
        if args[0] == "/usr/bin/docker" and "info" in args:
            observations["info_calls"] += 1
            daemon = observations["daemon_id"] if observations["info_calls"] % 2 else observations["after_id"]
            return subprocess.CompletedProcess(args, 0, stdout=host.canonical({"ID": daemon}))
        if args[0] == "/usr/bin/docker" and "container" in args and "ls" in args:
            assert "--filter" not in args and "--all" in args
            return subprocess.CompletedProcess(args, 0, stdout=observations["containers"])
        return response

    installer.command = command

    def prepare(address, confirmed):
        observations["calls"] += 1
        assert address == "139.59.1.2" and confirmed == request
        return installer.retire_worker(
            bundle,
            hashlib.sha256(bundle.read_bytes()).hexdigest(),
            ORIGIN,
            REGISTRATION,
            [IMAGE],
            skip_platform=True,
            destruction=confirmed,
        )

    arguments = {
        "confirm_plan": saved["plan_id"],
        "confirm_worker": config["id"],
        "confirm_release": controller.binding["release_id"],
        "confirm_droplet": 456,
        "confirm_delete_host_data": True,
        "prepare_host": prepare,
    }
    identity.failure = "revoked"  # Retirement must not require ordinary enrollment authorization.
    return controller, arguments, provider, observations, worker_cloud, managed


def execute(fixture, **changes):
    controller, arguments, _, _, cloud, _ = fixture
    return controller.retire(cloud[7], **{**arguments, **changes})


def test_exact_cloud_retirement_checks_host_deletes_in_order_and_preserves_ownership(cloud_retirement):
    controller, _, provider, observations, cloud, managed = cloud_retirement
    before = (cloud[7] / "digitalocean.json").read_bytes()
    result = execute(cloud_retirement)
    assert result["phase"] == "deleted" and result["cloud_resources_deleted"]
    assert result["owned_vm_absence_observed"] and result["fresh_host_destruction_check"]
    assert observations["calls"] == 1 and managed[1].seals == 1
    assert [request.url.path.split("/")[2] for request in provider.deletions] == ["droplets", "firewalls", "tags"]
    assert (cloud[7] / "digitalocean.json").read_bytes() == before
    assert (cloud[7] / JOURNAL).stat().st_mode & 0o777 == 0o600
    assert TOKEN not in json.dumps(result) and provider.remote.token not in (cloud[7] / JOURNAL).read_text()
    assert execute(cloud_retirement)["fresh_host_destruction_check"] is False
    assert len(provider.deletions) == 3 and observations["calls"] == 1
    with pytest.raises(host.InstallError, match="retirement intent"):
        cloud[6].apply(cloud[7], cloud[4]["plan_id"])
    assert controller.retirement["proof"]["destruction"]["all_containers_absent"]


@pytest.mark.parametrize(
    "key,value",
    [
        ("confirm_plan", "0" * 64),
        ("confirm_worker", "0" * 32),
        ("confirm_release", "0" * 64),
        ("confirm_droplet", 789),
        ("confirm_droplet", True),
        ("confirm_delete_host_data", False),
        ("confirm_delete_host_data", 1),
        ("retry_delete", "all"),
    ],
)
def test_wrong_confirmations_are_local_and_do_not_touch_host_or_provider(cloud_retirement, key, value):
    _, _, provider, observations, _, _ = cloud_retirement
    count = len(provider.remote.requests)
    with pytest.raises(host.InstallError, match="confirm"):
        execute(cloud_retirement, **{key: value})
    assert len(provider.remote.requests) == count and observations["calls"] == 0 and not provider.deletions


@pytest.mark.parametrize("status", ["running", "outcome_unknown"])
def test_busy_worker_drains_without_deleting_or_stopping(cloud_retirement, status):
    _, _, provider, _, _, managed = cloud_retirement
    managed[1].jobs = [{"id": "a" * 32, "status": status}]
    assert execute(cloud_retirement)["phase"] == "draining"
    assert not provider.deletions and managed[1].commands.active
    managed[1].jobs = []
    assert execute(cloud_retirement)["phase"] == "deleted"


@pytest.mark.parametrize("change", ["marker", "metadata", "runtime", "daemon", "after-daemon", "containers", "docker-config"])
def test_host_destruction_refuses_wrong_target_or_any_remaining_container(cloud_retirement, monkeypatch, change):
    _, _, provider, observations, _, managed = cloud_retirement
    installer, lifecycle = managed
    if change == "marker":
        write_state(installer.root / "etc/reasoning-provisioning.json", {"plan_id": "0" * 64, "deployment_id": "0" * 32})
    elif change == "metadata":
        monkeypatch.setattr(installer, "_droplet_id", lambda: 789)
    elif change == "runtime":
        write_state(installer.root / "var/lib/reasoning-compute/docker-runtime.json", {"format": 1})
    elif change == "daemon":
        observations["daemon_id"] = "different"
    elif change == "after-daemon":
        observations["after_id"] = "different"
    elif change == "containers":
        observations["containers"] = b"unrelated-stopped-container\n"
    else:
        (installer.state_dir / "docker-client/config.json").write_bytes(b'{"auths":{"registry":"secret"}}\n')
    with pytest.raises(host.InstallError):
        execute(cloud_retirement)
    assert not provider.deletions
    if change in {"marker", "metadata"}:
        assert lifecycle.seals == 0 and not lifecycle.commands.calls


@pytest.mark.parametrize("kind", ["droplet", "firewall", "tag"])
@pytest.mark.parametrize("failure", ["lost_before", "lost_after", "rejected", "body"])
def test_uncertain_provider_deletion_never_blindly_retries(cloud_retirement, kind, failure):
    _, _, provider, observations, cloud, _ = cloud_retirement
    provider.failures[kind] = failure
    with pytest.raises(host.InstallError):
        execute(cloud_retirement)
    state = read_document(cloud[7] / JOURNAL)["deletion"][kind]
    assert state == ("not_started" if failure == "rejected" else "request_pending")
    count = len(provider.deletions)
    if failure == "lost_before":
        assert execute(cloud_retirement)["phase"] == "deletion_pending"
        assert len(provider.deletions) == count
        before = observations["calls"]
        assert execute(cloud_retirement, retry_delete=kind)["phase"] == "deleted"
        assert observations["calls"] == before + (kind == "droplet")
    else:
        assert execute(cloud_retirement)["phase"] == "deleted"
        expected = 4 if failure == "rejected" else 3
        assert len(provider.deletions) == expected


def test_accepted_but_present_vm_only_reconciles_until_explicit_retry(cloud_retirement):
    _, _, provider, observations, _, _ = cloud_retirement
    provider.pending.add("droplet")
    assert execute(cloud_retirement)["phase"] == "deletion_pending"
    assert execute(cloud_retirement)["phase"] == "deletion_pending"
    assert len(provider.deletions) == 1 and observations["calls"] == 1
    provider.pending.clear()
    assert execute(cloud_retirement, retry_delete="droplet")["phase"] == "deleted"
    assert observations["calls"] == 2


@pytest.mark.parametrize(
    "change", ["account", "vm-id", "volume", "extra-tag", "firewall-rule", "firewall-member", "duplicate", "missing-vm"]
)
def test_provider_ownership_drift_refuses_before_host_changes(cloud_retirement, change):
    _, _, provider, observations, _, _ = cloud_retirement
    remote = provider.remote
    if change == "account":
        remote.account["uuid"] = "b" * 40
    elif change == "vm-id":
        remote.droplets[0]["id"] = 789
    elif change == "volume":
        remote.droplets[0]["volume_ids"] = ["external-volume"]
    elif change == "extra-tag":
        remote.droplets[0]["tags"].append("external")
    elif change == "firewall-rule":
        remote.firewalls[0]["inbound_rules"] = []
    elif change == "firewall-member":
        remote.firewalls[0]["droplet_ids"].append(789)
    elif change == "duplicate":
        row = copy.deepcopy(remote.droplets[0])
        row["id"] = 789
        remote.droplets.append(row)
    else:
        remote.droplets.clear()
    with pytest.raises(host.InstallError):
        execute(cloud_retirement)
    assert not provider.deletions and observations["calls"] == 0


@pytest.mark.parametrize("path", ["images", "volumes", "snapshots", "databases"])
def test_hidden_tag_resource_types_refuse_tag_deletion(cloud_retirement, path):
    _, _, provider, _, _, _ = cloud_retirement
    provider.denied_reads.add(path)
    with pytest.raises(host.InstallError):
        execute(cloud_retirement)
    assert len(provider.deletions) == 2 and provider.remote.tags
    provider.denied_reads.clear()
    assert execute(cloud_retirement)["phase"] == "deleted"


@pytest.mark.parametrize(
    "resources",
    [
        {"count": 1},
        {"count": False},
        {"count": 0, "images": {"count": 1}},
        {"count": 0, "future_resource": {}},
        {"count": 0, "future_resource": {"count": 0}},
        {"count": 0, "volumes": {"count": False}},
    ],
)
def test_nonempty_or_uncertain_tag_associations_are_preserved(cloud_retirement, resources):
    _, _, provider, _, _, _ = cloud_retirement

    def change(kind):
        if kind == "firewall":
            provider.remote.tags[0]["resources"] = resources

    provider.before_delete = change
    with pytest.raises(host.InstallError, match="tag"):
        execute(cloud_retirement)
    assert len(provider.deletions) == 2 and provider.remote.tags


@pytest.mark.parametrize("path", ["images", "volumes", "snapshots", "databases"])
def test_visible_association_contradicting_empty_tag_counts_is_preserved(cloud_retirement, path):
    _, _, provider, _, _, _ = cloud_retirement
    provider.visibility[path] = [{"id": "external", "tags": [provider.remote.tags[0]["name"]]}]
    with pytest.raises(host.InstallError, match="another resource"):
        execute(cloud_retirement)
    assert len(provider.deletions) == 2 and provider.remote.tags


@pytest.mark.parametrize(
    "path",
    [
        "droplets",
        "droplets?tag_name=x",
        "droplets/456/destroy_with_associated_resources",
        "tags/other",
        "firewalls",
        "droplets/0",
        "droplets/../456",
    ],
)
def test_provider_forbids_bulk_associated_and_unowned_deletion_paths(path):
    provider = DigitalOcean("x" * 32, transport=httpx.MockTransport(lambda _: pytest.fail("no request allowed")))
    try:
        for method in (provider.get_owned, provider.delete_owned):
            with pytest.raises(host.InstallError):
                method(path)
    finally:
        provider.close()


def test_cli_cloud_retirement_uses_provider_address_and_private_staged_proof(cloud_retirement, bundle, monkeypatch, capsys):
    _, arguments, provider, _, cloud, _ = cloud_retirement
    directory = cloud[7].parent
    token, registration, catalog = [directory / name for name in ("token", "enrollment.json", "images.json")]
    write_state(token, {})
    token.write_text(provider.remote.token)
    write_state(registration, REGISTRATION)
    write_state(catalog, [IMAGE])
    monkeypatch.setattr(cli, "DigitalOcean", lambda _: provider.remote.client)

    def ssh(*args, **kwargs):
        assert args[4] == "139.59.1.2" and kwargs["operation"] == "prepare-destroy"
        return arguments["prepare_host"](args[4], kwargs["destruction"])

    monkeypatch.setattr(cli, "install_worker_remote", ssh)
    argv = [
        "do-worker-retire",
        "--server-url",
        ORIGIN,
        "--token-file",
        str(token),
        "--plan",
        cloud[4]["plan_file"],
        "--state-dir",
        str(cloud[7]),
        "--bundle",
        str(bundle),
        "--registration",
        str(registration),
        "--catalog",
        str(catalog),
        "--identity",
        str(directory / "key"),
        "--known-hosts",
        str(directory / "hosts"),
        "--confirm-plan",
        arguments["confirm_plan"],
        "--confirm-worker",
        arguments["confirm_worker"],
        "--confirm-release",
        arguments["confirm_release"],
        "--confirm-droplet",
        "456",
        "--confirm-delete-host-data",
    ]
    assert cli.main(argv) == 0
    assert json.loads(capsys.readouterr().out)["phase"] == "deleted"


@pytest.mark.parametrize("change", ["intent", "proof", "ordering", "boolean", "extra", "symlink"])
def test_changed_or_unsafe_retirement_journal_refuses_before_provider_reads(cloud_retirement, change):
    _, _, provider, observations, cloud, managed = cloud_retirement
    managed[1].jobs = [{"id": "a" * 32, "status": "running"}]
    assert execute(cloud_retirement)["phase"] == "draining"
    path = cloud[7] / JOURNAL
    state = read_document(path)
    if change == "intent":
        state["intent"]["request"]["droplet_id"] = 789
    elif change == "proof":
        state["proof"] = {"retirement": {}, "destruction": None}
    elif change == "ordering":
        state["deletion"]["tag"] = "absent"
    elif change == "boolean":
        state["intent"]["request"]["format"] = True
    elif change == "extra":
        state["extra"] = "invalid"
    else:
        target = cloud[7] / "copy.json"
        path.rename(target)
        path.symlink_to(target)
    if change != "symlink":
        write_document(path, state)
    reads = len(provider.remote.requests)
    calls = observations["calls"]
    with pytest.raises(host.InstallError):
        execute(cloud_retirement)
    assert len(provider.remote.requests) == reads and observations["calls"] == calls and not provider.deletions


def test_exclusive_cloud_lifecycle_lock_refuses_concurrent_retirement(cloud_retirement):
    _, _, provider, observations, cloud, _ = cloud_retirement
    fd = os.open(cloud[7] / "provision.lock", os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        reads = len(provider.remote.requests)
        with pytest.raises(host.InstallError, match="another process"):
            execute(cloud_retirement)
        assert len(provider.remote.requests) == reads and observations["calls"] == 0
    finally:
        os.close(fd)


@pytest.mark.parametrize("change", ["account", "address", "volume"])
def test_provider_is_rechecked_after_fresh_host_proof(cloud_retirement, change):
    _, arguments, provider, _, _, _ = cloud_retirement
    original = arguments["prepare_host"]

    def changed(address, request):
        proof = original(address, request)
        if change == "account":
            provider.remote.account["uuid"] = "b" * 40
        elif change == "address":
            provider.remote.droplets[0]["networks"]["v4"][0]["ip_address"] = "139.59.1.3"
        else:
            provider.remote.droplets[0]["volume_ids"] = ["unexpected"]
        return proof

    with pytest.raises(host.InstallError):
        execute(cloud_retirement, prepare_host=changed)
    assert not provider.deletions


@pytest.mark.parametrize("change", [None, "target", "extra", "boolean", "daemon", "malformed"])
def test_prepare_destroy_ssh_upload_is_bound_hashed_and_strict(cloud_retirement, bundle, change, tmp_path):
    controller, arguments, _, _, _, _ = cloud_retirement
    request = {
        "format": 1,
        "plan_id": controller.identity,
        "deployment_id": controller.document["deployment_id"],
        "droplet_id": 456,
        "delete_host_data": True,
    }
    response = arguments["prepare_host"]("139.59.1.2", request)
    if change == "target":
        response["destruction"]["request"]["droplet_id"] = 789
    elif change == "extra":
        response["secret"] = TOKEN
    elif change == "boolean":
        response["destruction"]["all_containers_absent"] = 1
    elif change == "daemon":
        response["destruction"]["daemon_id"] = "bad\n"
    files = {}
    for name, value in (
        ("enrollment", host.canonical(REGISTRATION)),
        ("catalog", host.canonical([IMAGE])),
        ("key", b"private-key"),
        ("hosts", b"verified-host"),
    ):
        files[name] = tmp_path / name
        write_state(files[name], {})
        files[name].write_bytes(value)
    uploaded, calls = {}, []

    def command(args):
        calls.append(args)
        if args[0] == "scp":
            uploaded[Path(args[-2]).name] = Path(args[-2]).read_bytes()
        return subprocess.CompletedProcess(args, 0, stdout=b"invalid" if change == "malformed" else host.canonical(response))

    def operation():
        return cli.install_worker_remote(
            bundle,
            ORIGIN,
            files["enrollment"],
            files["catalog"],
            "139.59.1.2",
            files["key"],
            files["hosts"],
            command=command,
            confirmed_release=controller.binding["release_id"],
            confirmed_worker=REGISTRATION["id"],
            operation="prepare-destroy",
            destruction=request,
        )

    if change is not None:
        with pytest.raises(host.InstallError):
            operation()
        return
    assert operation() == response
    assert uploaded["destruction.json"] == host.canonical(request)
    args = shlex.split(calls[-1][-1])
    assert args[args.index("--operation") + 1] == "prepare-destroy"
    assert args[args.index("--expected-destruction-sha256") + 1] == hashlib.sha256(uploaded["destruction.json"]).hexdigest()
    assert all("StrictHostKeyChecking=yes" in call for call in calls)
    assert TOKEN not in json.dumps(calls)


@pytest.mark.parametrize("value,status", [(b"456\n", 200), (b"0", 200), (b"456 extra", 200), (b"9" * 65, 200), (b"456", 302)])
def test_droplet_metadata_uses_fixed_no_proxy_no_redirect_bounded_request(monkeypatch, value, status):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, maximum):
            assert maximum == 65
            return value

    response = Response()
    response.status = status

    class Opener:
        def open(self, url, *, timeout):
            assert url == "http://169.254.169.254/metadata/v1/id" and timeout == 3
            return response

    def opener(proxy, redirect):
        assert proxy.proxies == {} and isinstance(redirect, host.NoRedirect)
        return Opener()

    monkeypatch.setattr(host.urllib.request, "build_opener", opener)
    if value == b"456\n":
        assert worker_host.WorkerHostInstaller._droplet_id() == 456
    else:
        with pytest.raises(host.InstallError, match="metadata"):
            worker_host.WorkerHostInstaller._droplet_id()
