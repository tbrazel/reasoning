"""Owned VM/SSH/bootstrap fixtures; no live cloud, systemd, or Docker execution."""

import copy
import fcntl
import hashlib
import json
import shlex
import subprocess
from pathlib import Path

import httpx
import pytest
from test_public_deployment import bundle as bundle
from test_public_deployment import payload as payload
from test_public_worker_cloud_retirement import DeletionProvider
from test_public_worker_deployment import IMAGE, ORIGIN, REGISTRATION, TOKEN
from test_public_worker_deployment import bootstrap as bootstrap
from test_public_worker_provisioning import apply
from test_public_worker_provisioning import worker_cloud as worker_cloud

from reasoning_ledger.deployment import cli, host, worker_host
from reasoning_ledger.deployment.digitalocean import DigitalOcean
from reasoning_ledger.deployment.private import locked_directory, read_document, write_document
from reasoning_ledger.deployment.worker_install import JOURNAL, WorkerInstaller
from reasoning_ledger.server.security import private_file


@pytest.fixture
def cloud_installation(worker_cloud, bundle, bootstrap, monkeypatch):
    apply(worker_cloud)
    remote, identity, config, images, saved, document, _, directory = worker_cloud
    provider = DeletionProvider(remote, directory)
    remote.client.close()

    def provider_request(request):
        if request.url.path.endswith("/firewalls") and "/droplets/" in request.url.path:
            return remote.handle(request)
        return provider.handle(request)

    remote.client = DigitalOcean(remote.token, transport=httpx.MockTransport(provider_request))
    controller = WorkerInstaller(remote.client, document, config, images, bundle, identity_client=identity.client)
    installer, commands = bootstrap
    (installer.root / "etc").mkdir(mode=0o755)
    request = {
        "format": 1,
        "plan_id": saved["plan_id"],
        "deployment_id": document["deployment_id"],
        "droplet_id": 456,
    }
    write_document(installer.root / "etc/reasoning-provisioning.json", {key: request[key] for key in ("plan_id", "deployment_id")})
    monkeypatch.setattr(installer, "_droplet_id", lambda: 456)
    observations = {"calls": 0}

    def install_host(address, confirmed):
        observations["calls"] += 1
        assert address == "139.59.1.2" and confirmed == request
        assert read_document(directory / JOURNAL)["phase"] == "installing"
        with pytest.raises(host.InstallError, match="another process"), locked_directory(directory):
            pytest.fail("installation must retain ownership lock during SSH")
        return installer.install_worker(
            bundle,
            hashlib.sha256(bundle.read_bytes()).hexdigest(),
            ORIGIN,
            REGISTRATION,
            [IMAGE],
            skip_platform=True,
            provisioning=confirmed,
        )

    arguments = {
        "confirm_plan": saved["plan_id"],
        "confirm_worker": config["id"],
        "confirm_release": controller.binding["release_id"],
        "confirm_droplet": 456,
        "install_host": install_host,
    }
    return controller, arguments, provider, observations, worker_cloud, bootstrap


def execute(fixture, **changes):
    controller, arguments, _, _, cloud, _ = fixture
    return controller.install(cloud[7], **{**arguments, **changes})


def test_owned_installation_is_exact_fresh_private_and_never_writes_provider(cloud_installation):
    controller, arguments, provider, observations, cloud, (installer, commands) = cloud_installation
    ownership = (cloud[7] / "digitalocean.json").read_bytes()
    writes = len(provider.remote.writes)
    result = execute(cloud_installation)
    assert result["phase"] == "installed" and result["worker_installation_verified"] is True
    assert result["container_execution_verified"] is False and result["fresh_installation_check"] is True
    assert result["installation"]["host_identity_verified"] is True
    assert result["installation"]["provisioning"]["droplet_id"] == 456
    assert commands.active and observations["calls"] == 1
    assert len(provider.remote.writes) == writes and not provider.deletions
    assert (cloud[7] / "digitalocean.json").read_bytes() == ownership
    assert (cloud[7] / JOURNAL).stat().st_mode & 0o777 == 0o600
    assert (installer.state_dir / "worker-provisioning.json").stat().st_mode & 0o777 == 0o600
    assert TOKEN not in json.dumps(result) and provider.remote.token not in (cloud[7] / JOURNAL).read_text()
    offset = len(commands.calls)
    assert execute(cloud_installation) == result
    assert observations["calls"] == 2 and len(commands.calls[offset:]) == 3
    assert all(call[0] not in {"apt-get", "python3"} and "configure" not in call for call in commands.calls[offset:])
    assert read_document(cloud[7] / JOURNAL)["receipt"] == result["installation"]
    assert arguments["confirm_release"] == controller.binding["release_id"]


@pytest.mark.parametrize(
    "key,value",
    [
        ("confirm_plan", "0" * 64),
        ("confirm_worker", "0" * 32),
        ("confirm_release", "0" * 64),
        ("confirm_droplet", True),
        ("confirm_droplet", 789),
        ("confirm_droplet", 0),
    ],
)
def test_wrong_confirmation_precedes_provider_and_ssh(cloud_installation, key, value):
    _, _, provider, observations, cloud, _ = cloud_installation
    count = len(provider.remote.requests)
    with pytest.raises(host.InstallError, match="confirm"):
        execute(cloud_installation, **{key: value})
    assert len(provider.remote.requests) == count and observations["calls"] == 0
    assert not (cloud[7] / JOURNAL).exists()


@pytest.mark.parametrize(
    "change",
    ["account", "id", "volume", "tag", "firewall", "duplicate", "missing", "inactive", "pending"],
)
def test_owned_resource_drift_is_refused_before_ssh(cloud_installation, change):
    _, _, provider, observations, cloud, _ = cloud_installation
    remote = provider.remote
    if change == "account":
        remote.account["uuid"] = "0" * 40
    elif change == "id":
        remote.droplets[0]["id"] = 789
    elif change == "volume":
        remote.droplets[0]["volume_ids"] = ["external"]
    elif change == "tag":
        remote.droplets[0]["tags"].append("external")
    elif change == "firewall":
        remote.firewalls[0]["inbound_rules"] = []
    elif change == "duplicate":
        remote.droplets.append({**copy.deepcopy(remote.droplets[0]), "id": 789})
    elif change == "missing":
        remote.droplets.clear()
    elif change == "inactive":
        remote.droplets[0]["status"] = "off"
    else:
        remote.firewalls[0]["status"] = "waiting"
    with pytest.raises(host.InstallError):
        execute(cloud_installation)
    assert observations["calls"] == 0 and not (cloud[7] / JOURNAL).exists()


@pytest.mark.parametrize("marker", ["digitalocean-retirement.json", "fleet-cancellation.json"])
def test_terminal_intent_never_reactivates_worker(cloud_installation, marker):
    _, _, provider, observations, cloud, _ = cloud_installation
    write_document(cloud[7] / marker, {"intent": "exists"})
    count = len(provider.remote.requests)
    with pytest.raises(host.InstallError, match="reactivate"):
        execute(cloud_installation)
    assert len(provider.remote.requests) == count and observations["calls"] == 0


@pytest.mark.parametrize("change", ["marker", "metadata", "extra-marker-field", "public-marker", "marker-symlink"])
def test_wrong_host_identity_refuses_all_package_and_service_commands(cloud_installation, monkeypatch, change):
    _, _, _, observations, cloud, (installer, commands) = cloud_installation
    path = installer.root / "etc/reasoning-provisioning.json"
    if change == "marker":
        write_document(path, {"plan_id": "0" * 64, "deployment_id": "0" * 32})
    elif change == "metadata":
        monkeypatch.setattr(installer, "_droplet_id", lambda: 789)
    elif change == "extra-marker-field":
        write_document(path, {**read_document(path), "other": True})
    elif change == "public-marker":
        path.chmod(0o644)
    else:
        path.rename(path.with_name("original-marker"))
        path.symlink_to(path.with_name("original-marker"))
    with pytest.raises((host.InstallError, OSError)):
        execute(cloud_installation)
    assert not commands.calls and observations["calls"] == 1
    assert read_document(cloud[7] / JOURNAL)["phase"] == "installing"


def test_owned_install_requires_original_request_and_rechecks_metadata_on_retry(cloud_installation, monkeypatch, bundle):
    _, _, _, _, _, (installer, commands) = cloud_installation
    result = execute(cloud_installation)
    count = len(commands.calls)
    with pytest.raises(host.InstallError, match="original exact"):
        installer.install_worker(bundle, hashlib.sha256(bundle.read_bytes()).hexdigest(), ORIGIN, REGISTRATION, [IMAGE], skip_platform=True)
    assert len(commands.calls) == count
    monkeypatch.setattr(installer, "_droplet_id", lambda: 789)
    with pytest.raises(host.InstallError, match="does not match"):
        execute(cloud_installation)
    assert len(commands.calls) == count and result["installation"]["service_active"]


def test_lost_installation_response_resumes_same_service_without_reinstall(cloud_installation):
    _, arguments, _, observations, cloud, (_, commands) = cloud_installation
    original = arguments["install_host"]

    def lost(address, request):
        original(address, request)
        raise host.InstallError("fixture lost SSH receipt")

    with pytest.raises(host.InstallError, match="lost SSH"):
        execute(cloud_installation, install_host=lost)
    before = read_document(cloud[7] / JOURNAL)
    assert before["phase"] == "installing" and before["receipt"] is None and commands.active
    offset = len(commands.calls)
    assert execute(cloud_installation)["worker_installation_verified"]
    assert observations["calls"] == 2 and len(commands.calls[offset:]) == 3
    assert read_document(cloud[7] / JOURNAL)["intent"] == before["intent"]


def test_post_install_provider_drift_leaves_unverified_intent(cloud_installation):
    _, arguments, provider, _, cloud, _ = cloud_installation
    original = arguments["install_host"]

    def changed(address, request):
        result = original(address, request)
        provider.remote.droplets[0]["networks"]["v4"][0]["ip_address"] = "139.59.1.3"
        return result

    with pytest.raises(host.InstallError, match="address changed"):
        execute(cloud_installation, install_host=changed)
    assert read_document(cloud[7] / JOURNAL)["phase"] == "installing"
    with pytest.raises(host.InstallError, match="different inputs"):
        execute(cloud_installation)


@pytest.mark.parametrize("change", ["inactive", "phase", "vm", "execution", "extra", "enrollment", "host-proof"])
def test_installation_receipt_must_match_every_binding_and_fixed_claim(cloud_installation, change):
    _, arguments, _, _, cloud, _ = cloud_installation
    original = arguments["install_host"]

    def wrong(address, request):
        value = original(address, request)
        if change == "inactive":
            value["service_active"] = False
        elif change == "phase":
            value["phase"] = "starting"
        elif change == "vm":
            value["provisioning"]["droplet_id"] = 789
        elif change == "execution":
            value["container_execution_verified"] = True
        elif change == "extra":
            value["token"] = TOKEN
        elif change == "enrollment":
            value["enrollment_sha256"] = "0" * 64
        else:
            value["host_identity_verified"] = 1
        return value

    with pytest.raises(host.InstallError, match="installation response"):
        execute(cloud_installation, install_host=wrong)
    assert read_document(cloud[7] / JOURNAL)["phase"] == "installing"


@pytest.mark.parametrize("change", ["public", "symlink", "intent", "receipt"])
def test_tampered_operator_journal_is_not_overwritten(cloud_installation, change):
    _, _, _, observations, cloud, _ = cloud_installation
    execute(cloud_installation)
    path = cloud[7] / JOURNAL
    if change == "public":
        path.chmod(0o644)
    elif change == "symlink":
        path.rename(path.with_name("original-installation"))
        path.symlink_to(path.with_name("original-installation"))
    else:
        state = read_document(path)
        if change == "intent":
            state["intent"]["binding"]["worker_id"] = "0" * 32
        else:
            state["receipt"]["service_active"] = False
        write_document(path, state)
    body = path.read_bytes()
    with pytest.raises(host.InstallError):
        execute(cloud_installation)
    assert observations["calls"] == 1 and path.read_bytes() == body


def test_owned_installation_lock_excludes_all_other_lifecycle_operations(cloud_installation):
    _, _, _, observations, cloud, _ = cloud_installation
    with (cloud[7] / "provision.lock").open("rb") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(host.InstallError, match="another process"):
            execute(cloud_installation)
    assert observations["calls"] == 0


@pytest.mark.parametrize("failure", ["revoked", "timeout", "redirect", "malformed"])
def test_unavailable_enrollment_refuses_host_changes(cloud_installation, failure):
    _, _, _, observations, cloud, _ = cloud_installation
    cloud[1].failure = failure
    with pytest.raises(host.InstallError, match="enrollment"):
        execute(cloud_installation)
    assert observations["calls"] == 0 and not (cloud[7] / JOURNAL).exists()


def test_real_remote_wrapper_passes_private_vm_binding_to_host_bootstrap(cloud_installation, bundle, tmp_path):
    _, arguments, _, _, _, _ = cloud_installation
    registration, catalog, identity, known = [tmp_path / name for name in ("registration", "catalog", "identity", "known")]
    for path, body in (
        (registration, host.canonical(REGISTRATION)),
        (catalog, host.canonical([IMAGE])),
        (identity, b"fixture identity"),
        (known, b"139.59.1.2 ssh-ed25519 independently-verified"),
    ):
        private_file(path, body)
    uploads = {}

    def command(args):
        if args[0] == "scp":
            source = Path(args[-2])
            uploads[source.name] = source.read_bytes()
            if source.name == "provisioning.json":
                assert source.stat().st_mode & 0o777 == 0o600
        elif "/worker-installer.py" in args[-1]:
            remote = shlex.split(args[-1])
            request = worker_host.decode_input(uploads["provisioning.json"])
            assert remote[remote.index("--expected-provisioning-sha256") + 1] == hashlib.sha256(uploads["provisioning.json"]).hexdigest()
            receipt = arguments["install_host"]("139.59.1.2", request)
            return subprocess.CompletedProcess(args, 0, stdout=host.canonical(receipt))
        return subprocess.CompletedProcess(args, 0, stdout=b"")

    result = execute(
        cloud_installation,
        install_host=lambda address, request: cli.install_worker_remote(
            bundle,
            ORIGIN,
            registration,
            catalog,
            address,
            identity,
            known,
            command=command,
            confirmed_release=arguments["confirm_release"],
            confirmed_worker=REGISTRATION["id"],
            provisioning=request,
        ),
    )
    assert result["worker_installation_verified"] is True and b"delete_host_data" not in uploads["provisioning.json"]


def cli_arguments(cloud_installation, bundle, tmp_path):
    controller, _, _, _, cloud, _ = cloud_installation
    paths = {name: tmp_path / name for name in ("token", "registration", "catalog", "identity", "known")}
    for name, body in (
        ("token", cloud[0].token.encode()),
        ("registration", host.canonical(REGISTRATION)),
        ("catalog", host.canonical([IMAGE])),
        ("identity", b"fixture identity"),
        ("known", b"independently verified host key"),
    ):
        private_file(paths[name], body)
    return [
        "do-worker-install",
        "--token-file",
        str(paths["token"]),
        "--plan",
        cloud[4]["plan_file"],
        "--state-dir",
        str(cloud[7]),
        "--bundle",
        str(bundle),
        "--server-url",
        ORIGIN,
        "--registration",
        str(paths["registration"]),
        "--catalog",
        str(paths["catalog"]),
        "--identity",
        str(paths["identity"]),
        "--known-hosts",
        str(paths["known"]),
        "--confirm-plan",
        controller.identity,
        "--confirm-worker",
        REGISTRATION["id"],
        "--confirm-release",
        controller.binding["release_id"],
        "--confirm-droplet",
        "456",
    ], paths


def test_cli_installs_only_confirmed_snapshots_even_if_original_inputs_change(cloud_installation, bundle, tmp_path, monkeypatch, capsys):
    from reasoning_ledger.deployment import worker_install

    _, _, provider, _, cloud, (installer, _) = cloud_installation
    arguments, paths = cli_arguments(cloud_installation, bundle, tmp_path)
    original_bundle = bundle.read_bytes()
    verify = worker_install.verify_remote_binding
    monkeypatch.setattr(worker_install, "verify_remote_binding", lambda config, **kwargs: verify(config, client=cloud[1].client))

    def provider_client(token):
        assert token == provider.remote.token
        bundle.write_bytes(b"original bundle changed after confirmation")
        paths["registration"].write_bytes(host.canonical({**REGISTRATION, "id": "0" * 32}))
        paths["catalog"].write_bytes(b"[]")
        return provider.remote.client

    def remote(snapshot, origin, registration, catalog, address, identity, known, **kwargs):
        assert snapshot.read_bytes() == original_bundle and address == "139.59.1.2"
        config, images = worker_host.inputs(ORIGIN, REGISTRATION, [IMAGE])
        assert registration.read_bytes() == host.canonical(config) and catalog.read_bytes() == host.canonical(images)
        assert registration != paths["registration"] and catalog != paths["catalog"]
        return installer.install_worker(
            snapshot,
            hashlib.sha256(original_bundle).hexdigest(),
            origin,
            config,
            images,
            skip_platform=True,
            provisioning=kwargs["provisioning"],
        )

    monkeypatch.setattr(cli, "DigitalOcean", provider_client)
    monkeypatch.setattr(cli, "install_worker_remote", remote)
    assert cli.main(arguments) == 0
    output = capsys.readouterr().out
    assert json.loads(output)["worker_installation_verified"] is True
    assert TOKEN not in output and provider.remote.token not in output


@pytest.mark.parametrize(
    "flag,value",
    [("--confirm-plan", "0" * 64), ("--confirm-worker", "0" * 32), ("--confirm-release", "0" * 64), ("--confirm-droplet", "0")],
)
def test_cli_confirmation_failures_precede_provider_access(cloud_installation, bundle, tmp_path, monkeypatch, flag, value):
    arguments, _ = cli_arguments(cloud_installation, bundle, tmp_path)
    arguments[arguments.index(flag) + 1] = value
    monkeypatch.setattr(cli, "DigitalOcean", lambda token: pytest.fail("provider must not be accessed"))
    monkeypatch.setattr(cli, "install_worker_remote", lambda *args, **kwargs: pytest.fail("SSH must not run"))
    with pytest.raises(SystemExit) as error:
        cli.main(arguments)
    assert error.value.code == 2


@pytest.mark.parametrize(
    "body", [b"", b"{}", b"not JSON", b"\xff", b"x" * 65537, b'{"phase":"services_started","phase":"services_started"}']
)
def test_ssh_success_without_exact_bounded_receipt_is_not_installation(bundle, tmp_path, body):
    paths = [tmp_path / name for name in ("registration", "catalog", "identity", "known")]
    for path, value in zip(paths, (host.canonical(REGISTRATION), host.canonical([IMAGE]), b"identity", b"known"), strict=True):
        private_file(path, value)
    with pytest.raises(host.InstallError):
        cli.install_worker_remote(
            bundle,
            ORIGIN,
            paths[0],
            paths[1],
            "139.59.1.2",
            paths[2],
            paths[3],
            command=lambda args: subprocess.CompletedProcess(args, 0, stdout=body),
        )


@pytest.mark.parametrize(
    "value", [None, {}, {"format": True}, {"format": 1, "plan_id": "0" * 64, "deployment_id": "0" * 32, "droplet_id": True}]
)
def test_invalid_vm_binding_is_rejected(value):
    with pytest.raises(host.InstallError):
        worker_host.provisioning_request(value)


def test_host_identity_is_rechecked_after_activation(cloud_installation, monkeypatch):
    _, _, _, _, cloud, (installer, commands) = cloud_installation
    readings = iter((456, 789))
    monkeypatch.setattr(installer, "_droplet_id", lambda: next(readings))
    with pytest.raises(host.InstallError, match="does not match"):
        execute(cloud_installation)
    assert commands.active and read_document(cloud[7] / JOURNAL)["phase"] == "installing"
    assert read_document(installer.state_path)["phase"] == "services_started"


def test_loaded_service_override_cannot_produce_installation_receipt(cloud_installation):
    _, _, _, _, cloud, (installer, commands) = cloud_installation

    def changed(args, **kwargs):
        response = commands(args, **kwargs)
        if args[:2] == ["systemctl", "show"]:
            response.stdout = response.stdout.replace(b"DropInPaths=\n", b"DropInPaths=/etc/external.conf\n")
        return response

    installer.command = changed
    with pytest.raises(host.InstallError, match="overrides"):
        execute(cloud_installation)
    assert read_document(cloud[7] / JOURNAL)["phase"] == "installing"


def standalone_arguments(cloud_installation, bundle, tmp_path):
    controller, _, _, _, _, _ = cloud_installation
    request = {"format": 1, "plan_id": controller.identity, "deployment_id": controller.document["deployment_id"], "droplet_id": 456}
    files = {}
    for name, value in (("registration", REGISTRATION), ("catalog", [IMAGE]), ("provisioning", request)):
        path = tmp_path / ("standalone-" + name)
        private_file(path, host.canonical(value))
        files[name] = path
    arguments = ["--bundle", str(bundle), "--expected-sha256", hashlib.sha256(bundle.read_bytes()).hexdigest(), "--server-url", ORIGIN]
    for name, expected in (("registration", "enrollment"), ("catalog", "catalog"), ("provisioning", "provisioning")):
        arguments += [
            "--" + name,
            str(files[name]),
            "--expected-" + expected + "-sha256",
            hashlib.sha256(files[name].read_bytes()).hexdigest(),
        ]
    return arguments, files


def test_standalone_bootstrap_dispatches_verified_owned_installation(cloud_installation, bundle, tmp_path, monkeypatch, capsys):
    _, _, _, _, _, (installer, _) = cloud_installation
    arguments, _ = standalone_arguments(cloud_installation, bundle, tmp_path)

    class Bootstrap:
        def install_worker(self, *args, **kwargs):
            return installer.install_worker(*args, skip_platform=True, **kwargs)

    monkeypatch.setattr(worker_host, "WorkerHostInstaller", Bootstrap)
    assert worker_host.main(arguments) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["host_identity_verified"] is True and result["provisioning"]["droplet_id"] == 456
    assert TOKEN not in json.dumps(result)


@pytest.mark.parametrize("change", ["hash", "missing-hash", "missing-path", "retire", "public", "destructive"])
def test_standalone_invalid_provisioning_never_reaches_host_setup(cloud_installation, bundle, tmp_path, monkeypatch, change):
    arguments, files = standalone_arguments(cloud_installation, bundle, tmp_path)
    if change == "hash":
        arguments[arguments.index("--expected-provisioning-sha256") + 1] = "0" * 64
    elif change in {"missing-hash", "missing-path"}:
        index = arguments.index("--expected-provisioning-sha256" if change == "missing-hash" else "--provisioning")
        del arguments[index : index + 2]
    elif change == "retire":
        arguments += ["--operation", "retire"]
    elif change == "public":
        files["provisioning"].chmod(0o644)
    else:
        value = read_document(files["provisioning"])
        value["delete_host_data"] = True
        write_document(files["provisioning"], value)
        arguments[arguments.index("--expected-provisioning-sha256") + 1] = hashlib.sha256(files["provisioning"].read_bytes()).hexdigest()
    monkeypatch.setattr(worker_host, "WorkerHostInstaller", lambda: pytest.fail("host setup must not be reached"))
    with pytest.raises(SystemExit) as error:
        worker_host.main(arguments)
    assert error.value.code == 2
