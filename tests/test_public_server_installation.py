"""Owned-server API/SSH/systemd/HTTPS fixtures, not a live public deployment."""

import copy
import hashlib
import json
import os
import shlex
import subprocess
from pathlib import Path

import httpx
import pytest
from test_public_deployment import bootstrap as bootstrap
from test_public_deployment import bundle as bundle
from test_public_deployment import payload as payload
from test_public_provisioning import ProviderFixture

from reasoning_ledger.deployment import cli, host, server_install
from reasoning_ledger.deployment.digitalocean import DigitalOcean
from reasoning_ledger.deployment.host import InstallError
from reasoning_ledger.deployment.private import locked_directory, read_document, write_document, write_private
from reasoning_ledger.deployment.provision import Provisioner, parameters, plan_identity, save_plan
from reasoning_ledger.deployment.server_install import JOURNAL, LAUNCH_JOURNAL, ServerInstaller
from reasoning_ledger.deployment.ssh_host import JOURNAL as SSH_JOURNAL
from reasoning_ledger.deployment.ssh_host import PIN_REQUIREMENT, server_trust

ORIGIN = "https://chat.example.org"


class ServerProvider(ProviderFixture):
    def __init__(self):
        super().__init__()
        self.client.close()
        self.denied = set()
        self.client = cli.DigitalOcean(self.token, transport=httpx.MockTransport(self.handle))

    def handle(self, request):
        path = request.url.path.removeprefix("/v2/")
        collections = {
            "tags": ("tags", "tag", "name"),
            "firewalls": ("firewalls", "firewall", "id"),
            "droplets": ("droplets", "droplet", "id"),
            "domains/example.org/records": ("records", "domain_record", "id"),
        }
        for prefix, (attribute, field, key) in collections.items():
            identifier = path.removeprefix(prefix + "/")
            if request.method == "GET" and path.startswith(prefix + "/") and "/" not in identifier:
                self.requests.append(request)
                if path in self.denied:
                    return httpx.Response(403, json={"message": self.token})
                rows = [row for row in getattr(self, attribute) if str(row[key]) == identifier]
                return httpx.Response(200, json={field: copy.deepcopy(rows[0])}) if rows else httpx.Response(404)
        return super().handle(request)


@pytest.fixture
def installation(tmp_path, bundle, bootstrap, monkeypatch, request):
    provider = ServerProvider()
    pinned = getattr(request, "param", True)
    plan = provider.client.plan(**parameters(provider.plan()), pin_ssh_host_key=pinned)
    saved = save_plan(tmp_path / "plan.json", plan)
    document = read_document(tmp_path / "plan.json")
    directory = tmp_path / "ownership"
    provisioner = Provisioner(provider.client, document)
    result = provisioner.apply(directory, saved["plan_id"])
    assert result["phase"] == ("ssh_key_pinned" if pinned else "ssh_verification_required")
    installer, commands = bootstrap
    body = next(json.loads(row.content) for row in provider.writes if row.url.path == "/v2/droplets")
    cloud_init = json.loads(body["user_data"].split("\n", 1)[1])
    (installer.root / "etc").mkdir(mode=0o755)
    write_private(installer.root / "etc/reasoning-provisioning.json", cloud_init["write_files"][0]["content"].encode())
    monkeypatch.setattr(installer, "_droplet_id", lambda: 456)
    observations = {"host": 0, "https": 0, "service": {}}
    original_command = commands.__call__

    def command(args, **kwargs):
        if args[:2] == ["systemctl", "show"]:
            commands.calls.append(args)
            unit = args[-1]
            account = "reasoning" if unit == "reasoning-server" else "caddy"
            value = {
                "FragmentPath": f"/etc/systemd/system/{unit}.service",
                "DropInPaths": "",
                "User": account,
                "Group": account,
                "LoadState": "loaded",
                "NeedDaemonReload": "no",
                "ActiveState": "active",
                **observations["service"],
            }
            return subprocess.CompletedProcess(args, 0, stdout="".join(f"{key}={item}\n" for key, item in value.items()).encode())
        return original_command(args, **kwargs)

    installer.command = command

    def check_https(origin):
        observations["https"] += 1
        assert origin == ORIGIN
        return {"public_url": origin, "https_ready": True, "initialized": False}

    monkeypatch.setattr(host, "ready", check_https)
    monkeypatch.setattr(server_install, "ready", check_https)
    controller = ServerInstaller(provider.client, document, bundle)

    def install_host(address, confirmed):
        observations["host"] += 1
        assert address == "139.59.1.2" and confirmed["droplet_id"] == 456
        assert read_document(directory / JOURNAL)["phase"] == "installing"
        with pytest.raises(InstallError, match="another process"), locked_directory(directory):
            pytest.fail("installation must retain the original ownership lock during SSH")
        state = installer.install(bundle, controller.binding["bundle_sha256"], ORIGIN, skip_platform=True, provisioning=confirmed)
        return installer.receipt(state, confirmed)

    arguments = {
        "confirm_plan": saved["plan_id"],
        "confirm_release": controller.binding["release_id"],
        "confirm_droplet": 456,
        "install_host": install_host,
        "check_https": check_https,
    }
    yield controller, arguments, provider, observations, installer, commands, directory, document, bundle, tmp_path
    provider.client.close()


def execute(fixture, **changes):
    controller, arguments, _, _, _, _, directory, *_ = fixture
    return controller.install(directory, **{**arguments, **changes})


@pytest.mark.parametrize("installation", [True, False], indirect=True)
def test_original_server_install_is_fresh_private_exact_and_never_creates_cloud(installation):
    controller, _, provider, observations, installer, _, directory, document, *_ = installation
    original = (directory / "digitalocean.json").read_bytes()
    writes = len(provider.writes)
    result = execute(installation)
    assert result["phase"] == "installed" and result["application_installed"] is True
    assert result["server_installation_verified"] is True and result["https_ready"] is True
    assert result["initialized"] is False and result["fresh_installation_check"] is True
    receipt = result["installation"]
    assert receipt["host_identity_verified"] is True and receipt["provisioning"]["plan_id"] == plan_identity(document)
    assert receipt["release_id"] == controller.binding["release_id"]
    assert observations["host"] == 1 and observations["https"] == 2
    assert read_document(directory / JOURNAL)["receipt"] == receipt
    assert (directory / JOURNAL).stat().st_mode & 0o777 == 0o600
    assert (installer.state_dir / "server-provisioning.json").stat().st_mode & 0o777 == 0o600
    assert provider.token not in json.dumps(result) and "private_key" not in json.dumps(result)
    assert execute(installation) == result
    assert observations["host"] == 2 and observations["https"] == 4
    assert len(provider.writes) == writes and (directory / "digitalocean.json").read_bytes() == original


@pytest.mark.parametrize("existing_server,features,accepted", [
    (False, [], True),
    (False, ["backups"], False),
    (True, ["backups"], True),
    (True, ["ipv6"], False),
    (True, ["backups", "ipv6"], False),
    (True, ["backups", 1], False),
    (True, "backups", False),
])
def test_only_existing_server_checks_accept_backups_without_mutating_provider_or_ownership(
    installation, existing_server, features, accepted
):
    _, _, provider, observations, _, commands, directory, document, bundle, _ = installation
    checker = ServerInstaller(provider.client, document, bundle, existing_server=existing_server)
    checker.state = read_document(directory / "digitalocean.json")
    checker._load_ssh_access(directory)
    provider.droplets[0]["features"] = features
    original = (directory / "digitalocean.json").read_bytes()
    writes = len(provider.writes)
    if accepted:
        assert checker._ready_host() == "139.59.1.2"
    else:
        with pytest.raises(InstallError):
            checker._ready_host()
    assert provider.droplets[0]["features"] == features
    assert len(provider.writes) == writes and observations["host"] == 0 and not commands.calls
    assert (directory / "digitalocean.json").read_bytes() == original


@pytest.mark.parametrize(
    "key,value",
    [
        ("confirm_plan", "0" * 64),
        ("confirm_release", "0" * 64),
        ("confirm_droplet", True),
        ("confirm_droplet", 0),
        ("confirm_droplet", 789),
    ],
)
def test_confirmation_precedes_provider_and_host_writes(installation, key, value):
    _, _, provider, observations, _, commands, directory, *_ = installation
    count = len(provider.requests)
    with pytest.raises(InstallError, match="confirm"):
        execute(installation, **{key: value})
    assert len(provider.requests) == count and observations["host"] == 0 and not commands.calls
    assert not (directory / JOURNAL).exists()


@pytest.mark.parametrize(
    "change",
    [
        "account",
        "missing",
        "duplicate",
        "shape",
        "volume",
        "firewall",
        "association",
        "pending",
        "off",
        "dns-target",
        "dns-id",
        "dns-duplicate",
        "read-denied",
    ],
)
def test_provider_or_dns_drift_refuses_ssh_without_cloud_mutations(installation, change):
    _, _, provider, observations, _, commands, directory, *_ = installation
    if change == "account":
        provider.account["uuid"] = "0" * 40
    elif change == "missing":
        provider.droplets.clear()
    elif change == "duplicate":
        provider.droplets.append({**copy.deepcopy(provider.droplets[0]), "id": 999})
    elif change == "shape":
        provider.droplets[0]["memory"] = 8192
    elif change == "volume":
        provider.droplets[0]["volume_ids"] = ["unmanaged"]
    elif change == "firewall":
        provider.firewalls[0]["inbound_rules"] = []
    elif change == "association":
        provider.association_visible = False
    elif change == "pending":
        provider.firewalls[0]["pending_changes"] = [{"droplet_id": 456}]
    elif change == "off":
        provider.droplets[0]["status"] = "off"
    elif change == "dns-target":
        provider.records[0]["data"] = "139.59.1.3"
    elif change == "dns-id":
        provider.records[0]["id"] = 999
    elif change == "dns-duplicate":
        provider.records.append({**provider.records[0], "id": 999})
    else:
        provider.denied.add("droplets/456")
    writes = len(provider.writes)
    with pytest.raises(InstallError):
        execute(installation)
    assert observations["host"] == 0 and not commands.calls and len(provider.writes) == writes
    assert not (directory / JOURNAL).exists()


@pytest.mark.parametrize("change", ["metadata", "marker", "public-marker", "marker-symlink", "marker-hardlink"])
def test_wrong_host_is_refused_before_package_commands(installation, monkeypatch, change):
    _, _, _, _, installer, commands, *_ = installation
    path = installer.root / "etc/reasoning-provisioning.json"
    if change == "metadata":
        monkeypatch.setattr(installer, "_droplet_id", lambda: 789)
    elif change == "marker":
        write_document(path, {"plan_id": "0" * 64, "deployment_id": "0" * 32})
    elif change == "public-marker":
        path.chmod(0o644)
    elif change == "marker-symlink":
        target = path.with_name("original.json")
        path.rename(target)
        path.symlink_to(target)
    else:
        os.link(path, path.with_name("alias.json"))
    with pytest.raises((InstallError, OSError)):
        execute(installation)
    assert not commands.calls


@pytest.mark.parametrize(
    "change",
    [
        {"ActiveState": "failed"},
        {"User": "root"},
        {"DropInPaths": "/override.conf"},
        {"FragmentPath": "/unmanaged.service"},
        {"NeedDaemonReload": "yes"},
    ],
)
def test_service_failure_is_not_saved_as_success(installation, change):
    _, _, _, observations, _, _, directory, *_ = installation
    observations["service"] = change
    with pytest.raises(InstallError, match="service"):
        execute(installation)
    assert read_document(directory / JOURNAL)["phase"] == "installing"
    observations["service"] = {}
    assert execute(installation)["server_installation_verified"] is True


def test_lost_host_receipt_and_https_failure_resume_original_intent(installation):
    _, arguments, provider, observations, _, _, directory, *_ = installation
    writes = len(provider.writes)

    def lost(address, request):
        arguments["install_host"](address, request)
        raise InstallError("lost original SSH reply")

    with pytest.raises(InstallError, match="lost"):
        execute(installation, install_host=lost)
    intent = read_document(directory / JOURNAL)["intent"]

    def unavailable(origin):
        raise InstallError("HTTPS not ready")

    with pytest.raises(InstallError, match="HTTPS"):
        execute(installation, check_https=unavailable)
    assert read_document(directory / JOURNAL)["intent"] == intent
    assert read_document(directory / JOURNAL)["phase"] == "installing"
    assert execute(installation)["https_ready"] is True
    assert observations["host"] == 3 and len(provider.writes) == writes


@pytest.mark.parametrize("change", ["phase", "receipt", "intent", "symlink", "public"])
def test_installation_journal_drift_never_rebinds_an_existing_host(installation, change):
    _, _, _, observations, _, _, directory, *_ = installation
    execute(installation)
    path = directory / JOURNAL
    value = read_document(path)
    if change == "phase":
        value["phase"] = "done"
    elif change == "receipt":
        value["receipt"]["release_id"] = "0" * 64
    elif change == "intent":
        value["intent"]["request"]["droplet_id"] = 789
    elif change == "public":
        path.chmod(0o644)
    else:
        target = path.with_name("saved-installation.json")
        path.rename(target)
        path.symlink_to(target)
    if change not in {"symlink", "public"}:
        write_document(path, value)
    with pytest.raises(InstallError):
        execute(installation)
    assert observations["host"] == 1


def test_owned_host_cannot_be_reinstalled_without_its_original_binding(installation):
    controller, arguments, _, _, installer, commands, _, _, bundle, _ = installation
    execute(installation)
    count = len(commands.calls)
    with pytest.raises(InstallError, match="original exact VM"):
        installer.install(bundle, controller.binding["bundle_sha256"], ORIGIN, skip_platform=True)
    assert len(commands.calls) == count
    request = {"format": 1, "plan_id": arguments["confirm_plan"], "deployment_id": "0" * 32, "droplet_id": 456}
    with pytest.raises(InstallError, match="does not match"):
        installer.install(bundle, controller.binding["bundle_sha256"], ORIGIN, skip_platform=True, provisioning=request)
    assert len(commands.calls) == count


@pytest.mark.parametrize("change", ["extra-secret", "wrong-release", "https-false", "initialized-string", "host-false", "bool-droplet"])
def test_receipts_are_exact_and_secret_free(installation, change):
    controller, arguments, _, _, _, _, directory, *_ = installation

    def invalid(address, request):
        receipt = arguments["install_host"](address, request)
        if change == "extra-secret":
            receipt["token"] = "never-echo-this"
        elif change == "wrong-release":
            receipt["release_id"] = "0" * 64
        elif change == "https-false":
            receipt["https_ready"] = False
        elif change == "initialized-string":
            receipt["initialized"] = "false"
        elif change == "host-false":
            receipt["host_identity_verified"] = False
        else:
            receipt["provisioning"]["droplet_id"] = True
        return receipt

    with pytest.raises(InstallError, match="verified exact") as error:
        execute(installation, install_host=invalid)
    assert "never-echo" not in str(error.value) and read_document(directory / JOURNAL)["phase"] == "installing"
    assert controller.binding["release_id"]


def test_provider_initialized_key_is_exact_private_reusable_and_never_replaced(installation):
    _, arguments, provider, _, _, _, directory, document, *_ = installation
    value = read_document(directory / SSH_JOURNAL)
    assert document["plan"]["requirements"][2] == PIN_REQUIREMENT
    body = next(json.loads(row.content) for row in provider.writes if row.url.path == "/v2/droplets")
    cloud_init = json.loads(body["user_data"].split("\n", 1)[1])
    assert cloud_init["ssh_keys"] == {"ed25519_private": value["private_key"], "ed25519_public": value["public_key"]}
    request = {"format": 1, "plan_id": arguments["confirm_plan"], "deployment_id": document["deployment_id"], "droplet_id": 456}
    with server_trust(document, directory, "139.59.1.2", request) as path:
        assert path.read_text() == "139.59.1.2 " + value["public_key"] + "\n"
        assert path.stat().st_mode & 0o777 == 0o600
    assert not path.exists()
    assert read_document(directory / SSH_JOURNAL) == value
    (directory / SSH_JOURNAL).rename(directory / "saved-ssh.json")
    with pytest.raises(InstallError, match="missing"), server_trust(document, directory, "139.59.1.2", request):
        pytest.fail("never generate a replacement key for an owned VM")


@pytest.mark.parametrize("command_name", ["do-install", "do-launch", "fresh-launch"])
def test_cli_owned_install_uses_pinned_ssh_private_request_and_actual_host_fixture(installation, monkeypatch, capsys, command_name):
    controller, _, provider, observations, installer, _, directory, document, bundle, tmp_path = installation
    token, identity = tmp_path / "provider-token", tmp_path / "identity"
    write_private(token, provider.token.encode())
    write_private(identity, b"nonfunctional operator key fixture")
    calls = []
    if command_name == "fresh-launch":
        directory = tmp_path / "fresh-ownership"
        for rows in (provider.tags, provider.firewalls, provider.droplets, provider.records):
            rows.clear()

        def before_post(kind, body):
            assert read_document(directory / LAUNCH_JOURNAL)["binding"] == controller.binding
            if kind == "droplet":
                assert read_document(directory / SSH_JOURNAL)["plan_id"] == plan_identity(document)

        provider.before_post = before_post

    def remote(args, **kwargs):
        calls.append(args)
        assert "StrictHostKeyChecking=yes" in args and "GlobalKnownHostsFile=/dev/null" in args
        known = next(x for x in args if x.startswith("UserKnownHostsFile="))
        path = Path(known.split("=", 1)[1].strip('"'))
        assert read_document(directory / SSH_JOURNAL)["public_key"] in path.read_text()
        assert provider.token not in " ".join(args) and "PRIVATE KEY" not in " ".join(args)
        if args[0] == "ssh" and "--expected-sha256" not in args[-1]:
            destination = shlex.split(args[-1])[-1]
            installer._directory("var/lib/reasoning-deploy", 0o700)
            installer._directory("var/lib/reasoning-deploy/staging", 0o700)
            installer._directory(destination.lstrip("/"), 0o700)
        elif args[0] == "scp":
            destination = installer.root / args[-1].split(":", 1)[1].lstrip("/")
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            destination.write_bytes(Path(args[-2]).read_bytes())
            destination.chmod(0o600)
        elif "--expected-sha256" in args[-1]:
            parts = shlex.split(args[-1])

            def get(key):
                return parts[parts.index(key) + 1]

            request_path = installer.root / get("--provisioning").lstrip("/")
            request = read_document(request_path)
            assert hashlib.sha256(request_path.read_bytes()).hexdigest() == get("--expected-provisioning-sha256")
            state = installer.install(bundle, get("--expected-sha256"), get("--public-url"), skip_platform=True, provisioning=request)
            assert kwargs["capture_output"] is True
            return subprocess.CompletedProcess(args, 0, stdout=host.canonical(installer.receipt(state, request)))
        return subprocess.CompletedProcess(args, 0, stdout=b"")

    monkeypatch.setattr(cli.subprocess, "run", remote)
    monkeypatch.setattr(cli, "DigitalOcean", lambda token: provider.client)
    count = len(provider.writes)
    assert (
        cli.main(
            [
                "do-install" if command_name == "do-install" else "do-launch",
                "--token-file",
                str(token),
                "--plan",
                str(tmp_path / "plan.json"),
                "--state-dir",
                str(directory),
                "--bundle",
                str(bundle),
                "--identity",
                str(identity),
                "--confirm-plan",
                plan_identity(document),
                "--confirm-release",
                controller.binding["release_id"],
                *(["--confirm-droplet", "456"] if command_name == "do-install" else ["--wait-seconds", "0"]),
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["https_ready"] is True and result["server_installation_verified"] is True
    assert len(calls) == 5 and len(provider.writes) == count + (4 if command_name == "fresh-launch" else 0)
    assert observations["https"] == 2


def test_pending_launch_pins_release_before_paid_requests_and_resumes_without_duplicate_vms(installation, monkeypatch, capsys):
    controller, _, provider, _, _, _, _, document, bundle, tmp_path = installation
    directory = tmp_path / "pending-ownership"
    for rows in (provider.tags, provider.firewalls, provider.droplets, provider.records):
        rows.clear()
    provider.droplet_status = "new"
    token, identity = tmp_path / "token", tmp_path / "identity"
    write_private(token, provider.token.encode())
    write_private(identity, b"fixture-key")
    monkeypatch.setattr(cli, "DigitalOcean", lambda token: DigitalOcean(token, transport=httpx.MockTransport(provider.handle)))
    arguments = [
        "do-launch",
        "--token-file",
        str(token),
        "--plan",
        str(tmp_path / "plan.json"),
        "--state-dir",
        str(directory),
        "--bundle",
        str(bundle),
        "--identity",
        str(identity),
        "--confirm-plan",
        plan_identity(document),
        "--confirm-release",
        controller.binding["release_id"],
        "--wait-seconds",
        "0",
    ]
    assert cli.main(arguments) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["phase"] == "droplet_pending" and first["application_installed"] is False and first["https_ready"] is False
    assert read_document(directory / LAUNCH_JOURNAL)["binding"] == controller.binding
    assert len(provider.writes) == 7  # Four initial fixture resources, then tag/firewall/VM; no new DNS yet.
    assert cli.main(arguments) == 0
    assert json.loads(capsys.readouterr().out)["phase"] == "droplet_pending" and len(provider.writes) == 7
    # Preserve the original pin; a confirmed different release cannot expand
    # the original paid launch authority while provider readiness is pending.
    changed = {**read_document(directory / LAUNCH_JOURNAL), "binding": {**controller.binding, "release_id": "0" * 64}}
    write_document(directory / LAUNCH_JOURNAL, changed)
    count = len(provider.requests)
    with pytest.raises(SystemExit):
        cli.main(arguments)
    assert "original plan, release" in capsys.readouterr().err and len(provider.requests) == count


@pytest.mark.parametrize("installation", [False], indirect=True)
def test_launch_requires_explicit_original_pinning_before_any_provider_access(installation, monkeypatch, capsys):
    controller, _, _, _, _, _, directory, document, bundle, tmp_path = installation
    monkeypatch.setattr(cli, "DigitalOcean", lambda _: pytest.fail("no provider access without original consent"))
    with pytest.raises(SystemExit):
        cli.main(
            [
                "do-launch",
                "--token-file",
                "missing",
                "--plan",
                str(tmp_path / "plan.json"),
                "--state-dir",
                str(directory),
                "--bundle",
                str(bundle),
                "--identity",
                "missing",
                "--confirm-plan",
                plan_identity(document),
                "--confirm-release",
                controller.binding["release_id"],
            ]
        )
    assert "original pinned-SSH plan" in capsys.readouterr().err


def test_manual_install_cannot_bypass_a_different_durable_launch_release(installation):
    controller, _, provider, observations, _, _, directory, *_ = installation
    write_document(
        directory / LAUNCH_JOURNAL,
        {
            "format": 1,
            "plan_id": controller.identity,
            "binding": {**controller.binding, "release_id": "0" * 64},
        },
        new=True,
    )
    count = len(provider.requests)
    with pytest.raises(InstallError, match="original confirmed launch"):
        execute(installation)
    assert observations["host"] == 0 and len(provider.requests) == count


@pytest.mark.parametrize("change", ["retirement", "incomplete", "ownership-lock"])
def test_terminal_or_incomplete_or_concurrent_ownership_refuses_installation(installation, change):
    _, _, _, observations, _, _, directory, *_ = installation
    if change == "retirement":
        write_document(directory / "digitalocean-retirement.json", {"intent": "exists"})
    elif change == "incomplete":
        state = read_document(directory / "digitalocean.json")
        state["resources"]["dns"] = {"status": "request_pending", "id": None}
        write_document(directory / "digitalocean.json", state)
    else:
        with locked_directory(directory), pytest.raises(InstallError, match="another process"):
            execute(installation)
        assert observations["host"] == 0
        return
    with pytest.raises(InstallError):
        execute(installation)
    assert observations["host"] == 0 and not (directory / JOURNAL).exists()


@pytest.mark.parametrize("reply", [None, b"not-json", b"{}", b"x" * 65537, b'{"token":"secret-reply"}'])
def test_remote_success_exit_without_a_verified_receipt_is_refused(bundle, tmp_path, reply):
    identity, known = tmp_path / "identity", tmp_path / "known"
    write_private(identity, b"fixture")
    write_private(known, b"fixture")

    def command(args):
        return subprocess.CompletedProcess(args, 0, stdout=reply)

    with pytest.raises(InstallError) as error:
        cli.install_remote(bundle, ORIGIN, "139.59.1.2", identity, known, command=command)
    assert "secret-reply" not in str(error.value)
