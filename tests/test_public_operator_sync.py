"""Independent operators recover newer retained releases using verified host evidence."""

import hashlib
import json
import sqlite3

import pytest
from test_public_operator_handoff import deployment_state
from test_public_server_operations import bootstrap as bootstrap
from test_public_server_operations import fingerprint
from test_public_server_operations import installed as installed
from test_public_server_operations import payload as payload

from reasoning_ledger.deployment import host, operator_sync, operator_sync_host
from reasoning_ledger.deployment.private import read_document, write_document
from reasoning_ledger.deployment.release_state import archived_release, current_binding


def sync_fixture(tmp_path, installed):
    inspector, manifest, release, checksum, archive, _state, _observations, _payload = installed
    installed_archive = archive.read_bytes()
    state, deployment, request, binding, token, identity = deployment_state(tmp_path)
    live = inspector.root / "var/lib/reasoning-deploy"
    actual = {
        "release_id": release,
        "bundle_sha256": checksum,
        "source_revision": manifest["source_revision"],
        "public_url": binding["public_url"],
        "dirty": False,
    }
    host.atomic_write(live / "server-provisioning.json", host.canonical({"format": 1, "binding": actual, "request": request}))
    host.atomic_write(
        inspector.root / "etc/reasoning-provisioning.json", host.canonical({key: request[key] for key in ("plan_id", "deployment_id")})
    )
    history = live / "server-upgrades"
    operation = history / ("f" * 64)
    host.HostInstaller(root=inspector.root)._directory(str(operation.relative_to(inspector.root)), 0o700)
    host.atomic_write(operation / "target.tar.gz", installed_archive)
    calls = []

    def command(arguments):
        assert arguments[0] == "do-ssh-access"
        calls.append(arguments)
        print(json.dumps({"phase": "closed" if "--close" in arguments else "open"}))
        return 0

    class Team:
        def __init__(self, secret):
            assert secret == token.read_text()

        def identity(self):
            return deployment["plan"]["account_identity"]

        def close(self):
            pass

    def fetch(address, private, known_hosts, expected, original_request, expected_manifest):
        assert address == "139.59.1.2" and private == identity
        assert known_hosts.read_text().startswith("139.59.1.2 ssh-ed25519 ")
        assert original_request == request
        return operator_sync_host.export_state(expected, request, expected_manifest, inspector=inspector)

    return state, binding, actual, inspector, command, Team, fetch, calls, token, identity, operation


def test_independent_operator_adopts_newer_release_without_original_computer(tmp_path, installed, monkeypatch):
    state, previous, actual, inspector, command, provider, fetch, calls, token, identity, _operation = sync_fixture(tmp_path, installed)
    assert _operation.parent.stat().st_mode & 0o777 == 0o755
    preserved = read_document(state / "setup.json")
    original = fingerprint(inspector.root)
    monkeypatch.setattr(sqlite3, "connect", lambda *_args, **_kwargs: pytest.fail("operator sync must not open application SQLite"))
    result = operator_sync.synchronize_operator(
        state, command, detect_public_ipv4=lambda: "203.0.113.20/32", fetch=fetch, provider_factory=provider
    )
    assert result["phase"] == "operator_synchronized" and result["changed"] is True and result["ssh_closed"] is True
    assert current_binding(state)["release_id"] == actual["release_id"] != previous["release_id"]
    assert host.inspect_bundle(archived_release(state, actual["release_id"]))[2] == actual["bundle_sha256"]
    assert host.inspect_bundle(archived_release(state, previous["release_id"]))[2] == previous["bundle_sha256"]
    assert read_document(state / "setup.json") == preserved
    assert token.exists() and identity.exists()
    assert read_document(state / "setup-completion.json")["release_id"] == actual["release_id"]
    assert read_document(state / "ownership/digitalocean-installation.json")["intent"]["binding"] == actual
    assert fingerprint(inspector.root) == original
    assert len(calls) == 2 and "--close" in calls[-1]
    assert (
        operator_sync.synchronize_operator(
            state, command, detect_public_ipv4=lambda: "203.0.113.20/32", fetch=fetch, provider_factory=provider
        )["changed"]
        is False
    )
    assert (
        operator_sync.synchronize_operator(
            state, command, detect_public_ipv4=lambda: "203.0.113.20/32", fetch=fetch, provider_factory=provider
        )["phase"]
        == "operator_synchronized"
    )


@pytest.mark.parametrize("failure", [
    "wrong_vm", "bad_archive", "failed_diagnosis", "writable_history", "public_operation", "public_archive",
    "public_state", "linked_history",
])
def test_sync_refusals_preserve_source_state_and_close_ssh(tmp_path, installed, failure):
    state, previous, _actual, inspector, command, provider, fetch, calls, _token, _identity, operation = sync_fixture(tmp_path, installed)
    if failure == "wrong_vm":
        inspector.droplet_id = lambda: 999
    elif failure == "bad_archive":
        host.atomic_write(operation / "target.tar.gz", b"not the exact installed archive")
    elif failure == "failed_diagnosis":
        inspector.health = lambda *_args, **_kwargs: {"public_url": previous["public_url"], "https_ready": False, "initialized": False}
    elif failure == "writable_history":
        operation.parent.chmod(0o777)
    elif failure == "public_operation":
        operation.chmod(0o755)
    elif failure == "public_archive":
        (operation / "target.tar.gz").chmod(0o644)
    elif failure == "public_state":
        operation.parent.parent.chmod(0o755)
    elif failure == "linked_history":
        history = operation.parent
        target = history.with_name("linked-upgrades")
        history.rename(target)
        history.symlink_to(target, target_is_directory=True)
    before = read_document(state / "setup-completion.json"), read_document(state / "ownership/digitalocean-installation.json")
    with pytest.raises(host.InstallError):
        operator_sync.synchronize_operator(
            state, command, detect_public_ipv4=lambda: "203.0.113.20/32", fetch=fetch, provider_factory=provider
        )
    assert current_binding(state) == previous
    assert (read_document(state / "setup-completion.json"), read_document(state / "ownership/digitalocean-installation.json")) == before
    assert len(calls) == 2 and "--close" in calls[-1]


def test_sync_defers_only_for_exact_locally_owned_pending_upgrade(tmp_path, installed):
    state, previous, actual, inspector, command, provider, fetch, calls, _token, _identity, operation = sync_fixture(tmp_path, installed)
    plan_id = "e" * 64
    live = inspector.root / "var/lib/reasoning-deploy"
    pointer = {"format": 1, "operation_id": operation.name, "plan_id": plan_id}
    host.atomic_write(live / "active-server-upgrade.json", host.canonical(pointer))
    host.atomic_write(
        operation / "operation.json",
        host.canonical(
            {**pointer, "identity": {"source": {"release_id": previous["release_id"]}, "target": {"release_id": actual["release_id"]}}}
        ),
    )
    with pytest.raises(host.InstallError):
        operator_sync.synchronize_operator(
            state, command, detect_public_ipv4=lambda: "203.0.113.20/32", fetch=fetch, provider_factory=provider
        )
    pending = {**pointer, "source_release": previous["release_id"], "target_release": actual["release_id"]}
    local = state / "upgrades" / actual["release_id"]
    local.parent.mkdir(mode=0o700, exist_ok=True)
    local.mkdir(parents=True, mode=0o700)
    write_document(local / "operation.json", pending, new=True)
    result = operator_sync.synchronize_operator(
        state, command, detect_public_ipv4=lambda: "203.0.113.20/32", fetch=fetch, provider_factory=provider
    )
    assert result["phase"] == "operator_sync_deferred" and current_binding(state) == previous
    assert "--close" in calls[-1]
    assert result["target_release"] == actual["release_id"]
    with pytest.raises(host.InstallError, match="make upgrade-resume"):
        operator_sync.synchronize_operator(
            state, command, detect_public_ipv4=lambda: "203.0.113.20/32", fetch=fetch, provider_factory=provider,
            require_no_pending_upgrade=True,
        )
    assert current_binding(state) == previous and "--close" in calls[-1]


@pytest.mark.parametrize("pending", [False, True])
def test_resume_uses_only_verified_pending_archive_and_never_builds(tmp_path, monkeypatch, pending):
    from reasoning_ledger.deployment import setup_upgrade

    state = tmp_path / "operator"
    state.mkdir(mode=0o700)
    calls = []
    result = {"phase": "operator_sync_deferred" if pending else "operator_synchronized",
              "target_release": "a" * 64, "ssh_closed": True}
    monkeypatch.setattr(operator_sync, "synchronize_operator", lambda *_args, **_options: result)

    def resume(bundle, directory, command, **options):
        calls.append((bundle, directory, command))
        return {"phase": "upgrade_complete"}

    monkeypatch.setattr(setup_upgrade, "run_upgrade", resume)
    def command(*_args):
        pytest.fail("no build or unverified command may run")
    returned = operator_sync.resume_operator_upgrade(state, command)
    assert returned["phase"] == ("upgrade_complete" if pending else "no_pending_upgrade")
    assert calls == ([(state / "releases" / ("a" * 64 + ".tar.gz"), state, command)] if pending else [])


def test_operator_sync_envelope_rejects_traversal_and_other_cloud_identity():
    request = {"format": 1, "plan_id": "a" * 64, "deployment_id": "b" * 32, "droplet_id": 456}
    value = {
        "format": 1,
        "operation": "operator-sync",
        "phase": "upgrade_active",
        "request": request,
        "pending": {
            "format": 1,
            "plan_id": "c" * 64,
            "operation_id": "d" * 64,
            "source_release": "e" * 64,
            "target_release": "../../setup.json",
        },
    }
    body = host.canonical(value)
    with pytest.raises(host.InstallError, match="operation identity"):
        operator_sync.decode_receipt(len(body).to_bytes(4, "big") + body, request)


def test_sync_requires_original_team_before_opening_ssh(tmp_path, installed):
    state, previous, _actual, _inspector, command, provider, fetch, calls, _token, _identity, _operation = sync_fixture(tmp_path, installed)

    class OtherTeam(provider):
        def identity(self):
            return {"account_uuid": "different-team"}

    with pytest.raises(host.InstallError, match="different deployment Team"):
        operator_sync.synchronize_operator(
            state, command, detect_public_ipv4=lambda: "203.0.113.20/32", fetch=fetch, provider_factory=OtherTeam
        )
    assert calls == [] and current_binding(state) == previous


@pytest.mark.parametrize("guarded", [False, True])
def test_cli_operator_sync_uses_selected_state(tmp_path, monkeypatch, capsys, guarded):
    from reasoning_ledger.deployment.cli import main

    calls = []

    def synchronize(directory, command, *, require_no_pending_upgrade=False):
        calls.append((directory, require_no_pending_upgrade))
        return {"phase": "operator_synchronized", "ssh_closed": True}

    monkeypatch.setattr(operator_sync, "synchronize_operator", synchronize)
    assert main(["operator-sync", "--state-dir", str(tmp_path)] + (["--require-no-pending-upgrade"] if guarded else [])) == 0
    assert calls == [(tmp_path, guarded)]
    assert json.loads(capsys.readouterr().out)["ssh_closed"] is True


@pytest.mark.parametrize("checkpoint", ["completion", "installation", "current"])
def test_sync_reconciles_lost_local_adoption_receipts_without_changing_identity(tmp_path, installed, monkeypatch, checkpoint):
    state, previous, actual, _inspector, command, provider, fetch, calls, _token, _identity, _operation = sync_fixture(tmp_path, installed)
    original_write, original_current = operator_sync.write_document, operator_sync.record_current
    failed = False

    def write(path, value, **options):
        nonlocal failed
        original_write(path, value, **options)
        match = path.name == ("setup-completion.json" if checkpoint == "completion" else "digitalocean-installation.json")
        if checkpoint != "current" and match and not failed:
            failed = True
            raise host.InstallError("simulated lost local adoption receipt")

    def current(directory, binding):
        nonlocal failed
        original_current(directory, binding)
        if checkpoint == "current" and binding["release_id"] == actual["release_id"] and not failed:
            failed = True
            raise host.InstallError("simulated lost current-release receipt")

    monkeypatch.setattr(operator_sync, "write_document", write)
    monkeypatch.setattr(operator_sync, "record_current", current)
    with pytest.raises(host.InstallError, match="simulated lost"):
        operator_sync.synchronize_operator(
            state, command, detect_public_ipv4=lambda: "203.0.113.20/32", fetch=fetch, provider_factory=provider
        )
    assert "--close" in calls[-1]
    outcome = operator_sync.synchronize_operator(
        state, command, detect_public_ipv4=lambda: "203.0.113.20/32", fetch=fetch, provider_factory=provider
    )
    assert outcome["phase"] == "operator_synchronized"
    assert current_binding(state)["release_id"] == actual["release_id"]
    assert read_document(state / "setup-completion.json")["release_id"] == actual["release_id"]
    assert read_document(state / "ownership/digitalocean-installation.json")["intent"]["binding"] == actual
    records = list((state / "operator-sync").glob(previous["release_id"] + "-" + actual["release_id"] + "-*.json"))
    assert len(records) == 1
    journal = read_document(records[0])
    assert journal["before"]["current-release"] == previous
    assert "--close" in calls[-1]


@pytest.mark.parametrize("configuration", ["mail_input_sha256", "compute_catalog_sha256"])
def test_same_release_new_verified_configuration_preserves_each_observation_and_legacy_evidence(tmp_path, installed, configuration):
    state, _, actual, _, command, provider, fetch, calls, _, _, _ = sync_fixture(tmp_path, installed)
    options = {"detect_public_ipv4": lambda: "203.0.113.20/32", "provider_factory": provider}
    operator_sync.synchronize_operator(state, command, fetch=fetch, **options)
    operator_sync.synchronize_operator(state, command, fetch=fetch, **options)
    history = state / "operator-sync"
    before = {path.name: path.read_bytes() for path in history.glob("*.json")}
    repeated = next(read_document(history / name) for name in before if name.startswith(actual["release_id"] + "-"))
    legacy = history / (actual["release_id"] + "-" + actual["release_id"] + ".json")
    write_document(legacy, repeated, new=True)
    legacy_bytes = legacy.read_bytes()

    def updated(*arguments):
        metadata, archive = operator_sync.decode_receipt(fetch(*arguments), arguments[4])
        metadata["diagnosis"]["configuration"][configuration] = "a" * 64
        if configuration == "mail_input_sha256":
            metadata["diagnosis"]["checks"]["mail_configuration"] = "pass"
        encoded = host.canonical(metadata)
        return len(encoded).to_bytes(4, "big") + encoded + archive

    result = operator_sync.synchronize_operator(state, command, fetch=updated, **options)
    assert result["changed"] is False and result["ssh_closed"] is True
    assert legacy.read_bytes() == legacy_bytes
    assert all((history / name).read_bytes() == body for name, body in before.items())
    records = list(history.glob("*.json"))
    assert len(records) == len(before) + 2
    latest = next(path for path in records if path.name not in before and path != legacy)
    receipt = read_document(latest)["receipt"]
    assert latest.stem.endswith(hashlib.sha256(host.canonical(receipt)).hexdigest())
    assert receipt["diagnosis"]["configuration"][configuration] == "a" * 64
    assert operator_sync.synchronize_operator(state, command, fetch=updated, **options)["changed"] is False
    assert len(list(history.glob("*.json"))) == len(records)
    assert "--close" in calls[-1]


def test_content_bound_observation_does_not_accept_tampered_saved_receipt(tmp_path, installed):
    state, previous, actual, _, command, provider, fetch, calls, _, _, _ = sync_fixture(tmp_path, installed)
    options = {"detect_public_ipv4": lambda: "203.0.113.20/32", "provider_factory": provider, "fetch": fetch}
    operator_sync.synchronize_operator(state, command, **options)
    record = next((state / "operator-sync").glob(previous["release_id"] + "-" + actual["release_id"] + "-*.json"))
    changed = read_document(record)
    changed["receipt"]["diagnosis"]["configuration"]["mail_input_sha256"] = "b" * 64
    write_document(record, changed)
    # Simulate a retained source checkpoint so this exact observation is replayed.
    operator_sync.record_current(state, previous)
    with pytest.raises(host.InstallError, match="saved operator sync evidence differs"):
        operator_sync.synchronize_operator(state, command, **options)
    assert current_binding(state) == previous and "--close" in calls[-1]
