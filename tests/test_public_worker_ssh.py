"""Local key/provider/SSH argument fixtures, not proof of live cloud bootstrap."""

import copy
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_public_compute_controller import cli_run, poll
from test_public_compute_controller import controller_fleet as controller_fleet
from test_public_compute_fleet import managed_fleet as managed_fleet
from test_public_compute_scaling import cloud_fleet as cloud_fleet
from test_public_deployment import bundle as bundle
from test_public_deployment import payload as payload
from test_public_worker_deployment import TOKEN
from test_public_worker_deployment import bootstrap as bootstrap
from test_public_worker_provisioning import worker_cloud as worker_cloud

from reasoning_ledger.deployment import cli, controller, fleet, ssh_host
from reasoning_ledger.deployment.host import InstallError
from reasoning_ledger.deployment.private import read_document, write_document
from reasoning_ledger.deployment.worker_provision import REQUIREMENTS, WorkerProvisioner, validate_worker_plan, worker_plan_identity


def pin_document(document):
    document["plan"]["requirements"] = [REQUIREMENTS[0], ssh_host.PIN_REQUIREMENT, *REQUIREMENTS[2:]]
    return worker_plan_identity(document)


@pytest.fixture
def pinned_cloud(worker_cloud):
    remote, identity, config, images, saved, document, _, directory = worker_cloud
    plan_id = pin_document(document)
    write_document(Path(saved["plan_file"]), document)
    instance = WorkerProvisioner(remote.client, document, config, images, identity_client=identity.client)
    return remote, document, directory, instance, plan_id


def apply(cloud):
    return cloud[3].apply(cloud[2], cloud[4])


def request(cloud, **changes):
    return {"format": 1, "plan_id": cloud[4], "deployment_id": cloud[1]["deployment_id"], "droplet_id": 456, **changes}


def test_pin_is_a_distinct_confirmed_plan_without_secret_or_resource_creation(pinned_cloud, worker_cloud):
    remote, document, directory, _, plan_id = pinned_cloud
    assert plan_id != worker_cloud[4]["plan_id"]
    validate_worker_plan(document["plan"])
    assert not remote.writes and not directory.exists()
    assert "private_key" not in json.dumps(document) and TOKEN not in json.dumps(document)
    with pytest.raises(InstallError, match="confirm-plan"):
        pinned_cloud[3].apply(directory, worker_cloud[4]["plan_id"])
    assert not directory.exists()


def test_key_is_private_persisted_before_vm_intent_and_not_printed(pinned_cloud):
    remote, document, directory, _, plan_id = pinned_cloud
    original = None

    def before_post(kind, body):
        nonlocal original
        if kind == "droplet":
            original = read_document(directory / ssh_host.JOURNAL)
            state = read_document(directory / "digitalocean.json")
            assert state["resources"]["droplet"]["status"] == "request_pending"
            config = json.loads(body["user_data"].split("\n", 1)[1])
            assert config["ssh_keys"] == {"ed25519_private": original["private_key"], "ed25519_public": original["public_key"]}
            assert config["ssh_deletekeys"] is True and config["ssh_genkeytypes"] == []
            assert TOKEN not in body["user_data"] and remote.token not in body["user_data"]

    remote.before_post = before_post
    result = apply(pinned_cloud)
    assert original is not None and result["phase"] == "ssh_key_pinned"
    assert result["worker_installation_verified"] is False and result["container_execution_verified"] is False
    assert (directory / ssh_host.JOURNAL).stat().st_mode & 0o777 == 0o600
    assert original["private_key"].splitlines()[1] not in json.dumps(result) + json.dumps(document)
    assert apply(pinned_cloud) == result and len(remote.writes) == 3
    assert read_document(directory / ssh_host.JOURNAL) == original
    assert ssh_host.validate(original, plan_id, document["deployment_id"]) == original


def test_generated_key_is_readable_by_real_openssh(pinned_cloud, tmp_path):
    executable = shutil.which("ssh-keygen")
    if executable is None:
        pytest.skip("OpenSSH key tooling is unavailable")
    apply(pinned_cloud)
    value = read_document(pinned_cloud[2] / ssh_host.JOURNAL)
    from reasoning_ledger.deployment.private import write_private

    path = tmp_path / "host-key"
    write_private(path, value["private_key"].encode())
    result = subprocess.run([executable, "-y", "-f", str(path)], check=True, capture_output=True)
    assert result.stdout.strip().decode() == value["public_key"]


def test_unsupported_private_key_errors_do_not_expose_key_material(pinned_cloud, monkeypatch):
    from cryptography.exceptions import UnsupportedAlgorithm

    apply(pinned_cloud)
    value = read_document(pinned_cloud[2] / ssh_host.JOURNAL)

    def unsupported(*args, **kwargs):
        raise UnsupportedAlgorithm(value["private_key"])

    monkeypatch.setattr(ssh_host.serialization, "load_ssh_private_key", unsupported)
    with pytest.raises(InstallError) as error:
        ssh_host.validate(value, pinned_cloud[4], pinned_cloud[1]["deployment_id"])
    assert value["private_key"].splitlines()[1] not in str(error.value)


def test_real_openssh_effective_configuration_uses_only_explicit_trust(pinned_cloud, tmp_path):
    executable = shutil.which("ssh")
    if executable is None:
        pytest.skip("OpenSSH client is unavailable")
    apply(pinned_cloud)
    from reasoning_ledger.deployment.private import write_private

    identity = tmp_path / "operator-key"
    write_private(identity, b"fixture operator identity")
    with ssh_host.worker_trust(pinned_cloud[1], pinned_cloud[2], "139.59.1.2", request(pinned_cloud)) as known:
        result = subprocess.run(
            [executable, "-G", *cli.ssh_arguments("139.59.1.2", identity, known), "root@139.59.1.2"],
            check=True,
            capture_output=True,
            text=True,
        )
        values = dict(row.split(" ", 1) for row in result.stdout.splitlines() if " " in row)
        assert values["stricthostkeychecking"] == "true" and values["globalknownhostsfile"] == "/dev/null"
        assert values["userknownhostsfile"] == str(known)
        assert values["verifyhostkeydns"] == "false" and values["updatehostkeys"] == "false"
        assert values.get("knownhostscommand", "none") == "none"


@pytest.mark.parametrize("failure", ["lost_after", "lost_before", "invalid_json", "redirect"])
def test_uncertain_paid_request_keeps_original_key_and_never_retries(pinned_cloud, failure):
    remote, _, directory, _, _ = pinned_cloud
    remote.fail["droplet"] = failure
    with pytest.raises(InstallError):
        apply(pinned_cloud)
    original = (directory / ssh_host.JOURNAL).read_bytes()
    writes = len(remote.writes)
    remote.fail.clear()
    if failure == "lost_before":
        with pytest.raises(InstallError, match="uncertain"):
            apply(pinned_cloud)
    else:
        assert apply(pinned_cloud)["phase"] == "ssh_key_pinned"
    assert len(remote.writes) == writes and (directory / ssh_host.JOURNAL).read_bytes() == original


@pytest.mark.parametrize("moment", ["created", "pending"])
def test_missing_key_after_vm_intent_never_rotates_or_posts_again(pinned_cloud, moment):
    remote, _, directory, _, _ = pinned_cloud
    if moment == "pending":
        remote.fail["droplet"] = "lost_after"
        with pytest.raises(InstallError):
            apply(pinned_cloud)
        remote.fail.clear()
    else:
        apply(pinned_cloud)
    key_path = directory / ssh_host.JOURNAL
    key_path.rename(directory / "preserved-key.json")
    writes = len(remote.writes)
    with pytest.raises(InstallError, match="missing"):
        apply(pinned_cloud)
    assert not key_path.exists() and len(remote.writes) == writes


@pytest.mark.parametrize("change", ["plan", "deployment", "public", "private", "format", "extra", "symlink", "permissions"])
def test_invalid_key_journal_fails_without_new_cloud_writes(pinned_cloud, change):
    apply(pinned_cloud)
    directory = pinned_cloud[2]
    path = directory / ssh_host.JOURNAL
    value = read_document(path)
    if change == "symlink":
        preserved = directory / "preserved-key.json"
        path.rename(preserved)
        path.symlink_to(preserved)
    elif change == "permissions":
        path.chmod(0o644)
    else:
        field, replacement = {
            "plan": ("plan_id", "0" * 64),
            "deployment": ("deployment_id", "0" * 32),
            "public": ("public_key", value["public_key"] + " attacker"),
            "private": ("private_key", "not a key"),
            "format": ("format", True),
            "extra": ("unrecognized", True),
        }[change]
        value[field] = replacement
        write_document(path, value)
    with pytest.raises(InstallError):
        apply(pinned_cloud)
    assert len(pinned_cloud[0].writes) == 3


@pytest.mark.parametrize("destruction", [False, True])
def test_trust_file_contains_only_original_public_key_and_current_owned_address(pinned_cloud, destruction):
    apply(pinned_cloud)
    document, directory = pinned_cloud[1:3]
    value = read_document(directory / ssh_host.JOURNAL)
    binding = request(pinned_cloud, **({"delete_host_data": True} if destruction else {}))
    with ssh_host.worker_trust(document, directory, "139.59.1.2", binding) as path:
        assert path.read_text() == "139.59.1.2 " + value["public_key"] + "\n"
        assert path.stat().st_mode & 0o777 == 0o600
        assert value["private_key"] not in path.read_text()
    assert not path.exists()


@pytest.mark.parametrize(
    "change", ["plan", "deployment", "droplet", "bool-id", "extra", "format", "delete", "address", "private-address", "state"]
)
def test_pin_rejects_wrong_target_before_ssh(pinned_cloud, change):
    apply(pinned_cloud)
    binding = request(pinned_cloud)
    address = "139.59.1.2"
    changes = {
        "plan": ("plan_id", "0" * 64),
        "deployment": ("deployment_id", "0" * 32),
        "droplet": ("droplet_id", 789),
        "bool-id": ("droplet_id", True),
        "extra": ("unrecognized", True),
        "format": ("format", True),
        "delete": ("delete_host_data", False),
    }
    if change in changes:
        field, value = changes[change]
        binding[field] = value
    elif change == "state":
        path = pinned_cloud[2] / "digitalocean.json"
        state = read_document(path)
        state["resources"]["droplet"]["id"] = 789
        write_document(path, state)
    else:
        address = "127.0.0.1" if change == "private-address" else "139.59.1.2 -oProxyCommand=attack"
    with pytest.raises(InstallError), ssh_host.worker_trust(pinned_cloud[1], pinned_cloud[2], address, binding):
        pytest.fail("invalid target must not obtain SSH trust")


def test_manual_plan_never_infers_host_trust(worker_cloud, tmp_path):
    document, directory = worker_cloud[5], worker_cloud[7]
    with pytest.raises(InstallError, match="known-hosts"), ssh_host.worker_trust(document, directory, "139.59.1.2", None):
        pytest.fail("manual verification must remain required")
    known = tmp_path / "operator-known"
    with ssh_host.worker_trust(document, directory, "139.59.1.2", None, known) as selected:
        assert selected == known


def test_owned_wrapper_uses_pin_not_operator_override_and_excludes_global_trust(pinned_cloud, tmp_path, monkeypatch):
    apply(pinned_cloud)
    from reasoning_ledger.deployment.private import write_private

    identity = tmp_path / "identity"
    write_private(identity, b"private operator SSH identity fixture")
    expected = read_document(pinned_cloud[2] / ssh_host.JOURNAL)["public_key"]
    seen = []

    def ssh(bundle, origin, registration, catalog, address, identity, known, **kwargs):
        assert known.read_text() == address + " " + expected + "\n"
        arguments = cli.ssh_arguments(address, identity, known)
        assert "StrictHostKeyChecking=yes" in arguments and "GlobalKnownHostsFile=/dev/null" in arguments
        assert arguments[:2] == ["-F", "/dev/null"]
        assert "VerifyHostKeyDNS=no" in arguments and "KnownHostsCommand=none" in arguments and "UpdateHostKeys=no" in arguments
        assert not any("accept-new" in arg or "ssh-keyscan" in arg for arg in arguments)
        seen.append(known)
        return {"fixture": True}

    monkeypatch.setattr(cli, "install_worker_remote", ssh)
    result = cli.owned_worker_remote(
        pinned_cloud[1],
        pinned_cloud[2],
        None,
        None,
        None,
        None,
        "139.59.1.2",
        identity,
        tmp_path / "untrusted-override",
        provisioning=request(pinned_cloud),
    )
    assert result == {"fixture": True} and len(seen) == 1 and not seen[0].exists()


def test_controller_without_manual_keys_requires_every_plan_pinned(controller_fleet):
    fixture = controller_fleet
    with pytest.raises(InstallError, match="every worker plan"):
        controller.plan_controller(
            fixture.fleet_path, fixture.budget, Path(fixture.policy["bundle_path"]), Path(fixture.policy["identity"])
        )
    assert not fixture.cloud[0].writes


def test_cli_can_plan_pinning_without_generating_keys_or_resources(worker_cloud, tmp_path, monkeypatch, capsys):
    from reasoning_ledger.deployment import worker_provision
    from reasoning_ledger.deployment.private import write_private

    remote, identity, config, images, *_ = worker_cloud
    paths = {name: tmp_path / name for name in ("token", "registration", "catalog", "plan")}
    write_private(paths["token"], remote.token.encode())
    write_document(paths["registration"], config)
    from reasoning_ledger.deployment.host import canonical

    write_private(paths["catalog"], canonical(images))
    monkeypatch.setattr(cli, "DigitalOcean", lambda token: remote.client)
    verify = worker_provision.verify_remote_binding
    monkeypatch.setattr(worker_provision, "verify_remote_binding", lambda config, **kwargs: verify(config, client=identity.client))
    assert (
        cli.main(
            [
                "do-worker-plan",
                "--token-file",
                str(paths["token"]),
                "--server-url",
                config["server_url"],
                "--registration",
                str(paths["registration"]),
                "--catalog",
                str(paths["catalog"]),
                "--name",
                "algebra",
                "--region",
                "fra1",
                "--size",
                "s-2vcpu-4gb",
                "--ssh-key-id",
                "123",
                "--admin-cidr",
                "203.0.113.10/32",
                "--maximum-hourly-usd",
                "0.10",
                "--pin-ssh-host-key",
                "--output",
                str(paths["plan"]),
            ]
        )
        == 0
    )
    saved = read_document(paths["plan"])
    assert ssh_host.pinned(saved["plan"]) and not remote.writes
    assert "private_key" not in capsys.readouterr().out and not list(tmp_path.rglob(ssh_host.JOURNAL))


def test_controller_cli_provisions_and_installs_using_original_pin_without_manual_key(controller_fleet, monkeypatch, capsys):
    from reasoning_ledger.deployment import scaling, worker_install, worker_provision

    fixture = controller_fleet
    document = copy.deepcopy(fixture.cloud[5])
    pin_document(document)
    write_document(Path(fixture.cloud[4]["plan_file"]), document)
    policy = controller.plan_controller(
        fixture.fleet_path,
        fixture.budget,
        Path(fixture.policy["bundle_path"]),
        Path(fixture.policy["identity"]),
        allow_provision=True,
        allow_retirement=True,
        delete_host_data=True,
    )
    assert policy["known_hosts"] is None
    write_document(fixture.policy_path, policy)
    arguments = cli_run(fixture)
    arguments[arguments.index("--confirm-controller") + 1] = fleet.digest(policy)
    arguments += ["--cycles", "3", "--interval-seconds", "10"]
    verify = worker_provision.verify_remote_binding
    monkeypatch.setattr(scaling, "read_snapshot", fixture.reader)
    monkeypatch.setattr(worker_provision, "verify_remote_binding", lambda config, **kwargs: verify(config, client=fixture.cloud[1].client))
    monkeypatch.setattr(worker_install, "verify_remote_binding", lambda config, **kwargs: verify(config, client=fixture.cloud[1].client))
    monkeypatch.setattr(cli, "DigitalOcean", lambda token: fixture.cloud[0].client)
    monkeypatch.setattr(cli.time, "sleep", lambda seconds: None)
    seen = []

    def ssh(bundle, origin, registration, catalog, address, identity, known, **kwargs):
        value = read_document(fixture.cloud[7] / ssh_host.JOURNAL)
        assert known.read_text() == address + " " + value["public_key"] + "\n"
        seen.append(known)
        target = SimpleNamespace(config=json.loads(registration.read_bytes()), images=json.loads(catalog.read_bytes()))
        result = fixture.install(target, address, kwargs["provisioning"])
        poll(fixture)
        return result

    monkeypatch.setattr(cli, "install_worker_remote", ssh)
    assert cli.main(arguments) == 0
    output = capsys.readouterr().out
    results = [json.loads(line) for line in output.splitlines()]
    assert [row["action"] for row in results] == ["provision", "install", None]
    assert results[0]["result"]["phase"] == "ssh_key_pinned" and results[-1]["reason"] == "steady"
    value = read_document(fixture.cloud[7] / ssh_host.JOURNAL)
    assert value["private_key"].splitlines()[1] not in output and TOKEN not in output and len(seen) == 1 and not seen[0].exists()
