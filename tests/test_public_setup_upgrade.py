"""Guided release custody and upgrade orchestration without cloud or SSH access."""

from __future__ import annotations

import io
import json
from contextlib import contextmanager
from pathlib import Path

import pytest
import test_public_deployment as deployment_fixtures
from rich.console import Console

from reasoning_ledger.deployment import setup_ui, setup_upgrade
from reasoning_ledger.deployment.host import InstallError, inspect_bundle
from reasoning_ledger.deployment.private import read_document, write_document, write_private
from reasoning_ledger.deployment.release_state import archived_release, current_binding, stage_current


def test_upgrade_progress_is_immediate_named_and_terminal(monkeypatch):
    output = io.StringIO()
    monkeypatch.setattr(setup_ui, "console", Console(file=output, force_terminal=False, color_system=None, width=120))
    with setup_ui.upgrade_progress("Preparing release", ("upload exact releases", "verify encrypted backup")):
        pass
    setup_ui.upgrade_prepared({"operation_id": "a" * 64})
    rendered = output.getvalue()
    assert "→ Preparing release" in rendered
    assert "upload exact releases · verify encrypted backup" in rendered
    assert "Working… this can take several minutes; keep this terminal open." in rendered
    assert "✓ Preparing release" in rendered
    assert "Preparation verified" in rendered and "Encrypted root backup retained" in rendered


def test_upgrade_progress_marks_unverified_failure(monkeypatch):
    output = io.StringIO()
    monkeypatch.setattr(setup_ui, "console", Console(file=output, force_terminal=False, color_system=None, width=120))
    with pytest.raises(InstallError, match="fixture failure"), setup_ui.upgrade_progress("Preparing release"):
        raise InstallError("fixture failure")
    assert "No verified completion receipt" in output.getvalue()


@pytest.fixture
def upgrade_payload():
    return deployment_fixtures.payload.__wrapped__()


def deployment_state(tmp_path, source):
    state = tmp_path / "operator"
    state.mkdir(mode=0o700)
    ownership = state / "ownership"
    ownership.mkdir(mode=0o700)
    manifest, release, checksum = inspect_bundle(source)
    public_url, plan_id = "https://chat.example.org", "c" * 64
    binding = {
        "release_id": release,
        "bundle_sha256": checksum,
        "source_revision": manifest["source_revision"],
        "dirty": False,
        "public_url": public_url,
    }
    write_document(
        state / "setup-completion.json",
        {
            "format": 1,
            "release_id": release,
            "plan_id": plan_id,
            "public_url": public_url,
            "organization_id": "d" * 32,
            "compute_backend": "none",
            "compute_setup": None,
        },
        new=True,
    )
    write_document(state / "plan.json", {"plan": {"account_identity": "team:fixture"}}, new=True)
    write_document(
        ownership / "digitalocean-installation.json",
        {
            "intent": {
                "binding": binding,
                "request": {
                    "format": 1,
                    "plan_id": plan_id,
                    "deployment_id": "e" * 32,
                    "droplet_id": 123,
                },
                "ssh_host": "203.0.113.10",
            },
        },
        new=True,
    )
    write_document(
        ownership / "digitalocean.json",
        {
            "plan_id": plan_id,
            "resources": {"droplet": {"status": "created", "id": 123}},
        },
        new=True,
    )
    token, identity = tmp_path / "token", tmp_path / "identity"
    write_private(token, b"x" * 40)
    write_private(identity, b"fixture identity")
    write_document(state / "setup.json", {"token_file": str(token), "identity": str(identity)}, new=True)
    return state, binding


def test_stage_retains_exact_installed_release_and_records_current(tmp_path, upgrade_payload):
    source = deployment_fixtures.pack(
        tmp_path / "source.tar.gz", upgrade_payload, manifest_change=lambda value: value.update(database_schema=13)
    )
    state, binding = deployment_state(tmp_path, source)

    result = stage_current(source, state)

    assert result["phase"] == "current_release_retained" and result["release_id"] == binding["release_id"]
    archive = archived_release(state, binding["release_id"])
    assert inspect_bundle(archive, binding["bundle_sha256"])[1] == binding["release_id"]
    assert archive.stat().st_mode & 0o077 == 0
    assert current_binding(state)["release_id"] == binding["release_id"]
    assert stage_current(None, state)["archive"] == str(archive)


@pytest.mark.parametrize("initial_refusal", [False, True])
def test_guided_upgrade_reuses_plan_and_operation_after_uncertain_activation(tmp_path, upgrade_payload, monkeypatch, initial_refusal):
    source = deployment_fixtures.pack(
        tmp_path / "source.tar.gz", upgrade_payload, manifest_change=lambda value: value.update(database_schema=13)
    )
    target = deployment_fixtures.pack(
        tmp_path / "target.tar.gz",
        upgrade_payload,
        manifest_change=lambda value: value.update(
            source_revision="b" * 40,
            database_schema=13,
        ),
    )
    state, source_identity = deployment_state(tmp_path, source)
    stage_current(source, state)
    target_manifest, target_release, target_checksum = inspect_bundle(target)
    target_identity = {
        "release_id": target_release,
        "bundle_sha256": target_checksum,
        "dirty": False,
        "source_revision": target_manifest["source_revision"],
        "public_url": source_identity["public_url"],
    }
    known = tmp_path / "known-hosts"
    write_private(known, b"203.0.113.10 ssh-ed25519 fixture\n")

    @contextmanager
    def trust(*_args, **_kwargs):
        yield known

    monkeypatch.setattr(setup_upgrade, "server_trust", trust)
    approvals, calls, operations = [], [], []
    monkeypatch.setattr(setup_upgrade, "approve", lambda prompt, phrase: approvals.append((prompt, phrase)))
    fail_activation = {"value": True}
    refuse_preflight = {"value": initial_refusal}
    rendered = io.StringIO()
    monkeypatch.setattr(setup_ui, "console", Console(file=rendered, force_terminal=False, color_system=None, width=120))

    def command(arguments):
        calls.append(list(arguments))
        name = arguments[0]
        if name == "do-ssh-access":
            value = {"phase": "closed" if "--close" in arguments else "open"}
        elif name == "upgrade-plan":
            value = {
                "format": 1,
                "operation": "upgrade-plan",
                "plan_id": "f" * 64,
                "source": source_identity,
                "target": target_identity,
                "source_database_schema": 13,
                "target_database_schema": 13,
                "preflight_checks_passed": True,
                "refusals": [],
            }
            if refuse_preflight["value"]:
                refuse_preflight["value"] = False
                value.update(preflight_checks_passed=False, refusals=["installed_host_checks_failed_or_unverified"],
                             diagnosis={"checks": {"loaded_server_unit": "fail", "server_active": "unverified",
                                                    "https_health": "fail"}})
            write_document(Path(arguments[arguments.index("--output") + 1]), value, new=True)
        elif name == "upgrade-prepare":
            operations.append(arguments[arguments.index("--operation-id") + 1])
            value = {
                "format": 1,
                "operation": "upgrade-prepare",
                "operation_id": operations[-1],
                "plan_id": "f" * 64,
                "source": source_identity,
                "target": target_identity,
                "phase": "prepared",
                "server_restart_fenced": True,
                "root_private_backup_retained": True,
                "activation_performed": False,
                "upgrade_completed": False,
                "runtime_dependency_attestation": False,
                "recovery_directory": "/var/lib/reasoning-deploy/server-upgrades/" + operations[-1],
            }
        elif name == "upgrade-activate":
            operations.append(arguments[arguments.index("--operation-id") + 1])
            if fail_activation["value"]:
                fail_activation["value"] = False
                raise InstallError("uncertain activation fixture")
            value = {"phase": "completed", "upgrade_completed": True}
        else:
            raise AssertionError(name)
        print(json.dumps(value))
        return 0

    if initial_refusal:
        with pytest.raises(InstallError, match="loaded_server_unit=fail"):
            setup_upgrade.run_upgrade(target, state, command, detect_public_ipv4=lambda: "203.0.113.20/32")
        assert not any(call[0] in {"upgrade-prepare", "upgrade-activate"} for call in calls)
        assert not approvals
        assert "✓ Checking the installed server" not in rendered.getvalue()
        assert "No verified completion receipt" in rendered.getvalue()
        assert calls[-1][0] == "do-ssh-access" and "--close" in calls[-1]

        operation_file = state / "upgrades" / target_release / "operation.json"
        write_document(operation_file, {"format": 1}, new=True)
        with pytest.raises(InstallError, match="operation already exists"):
            setup_upgrade.run_upgrade(target, state, command, detect_public_ipv4=lambda: "203.0.113.20/32")
        assert sum(call[0] == "upgrade-plan" for call in calls) == 1
        assert not list(operation_file.parent.glob("refused-plan-*.json"))
        operation_file.unlink()

    with pytest.raises(InstallError, match="uncertain activation"):
        setup_upgrade.run_upgrade(target, state, command, detect_public_ipv4=lambda: "203.0.113.20/32")
    operation = read_document(state / "upgrades" / target_release / "operation.json")["operation_id"]
    assert current_binding(state)["release_id"] == source_identity["release_id"]

    result = setup_upgrade.run_upgrade(target, state, command, detect_public_ipv4=lambda: "203.0.113.20/32")

    assert result["phase"] == "upgrade_complete" and current_binding(state)["release_id"] == target_release
    assert operations == [operation, operation, operation]
    assert [phrase for _, phrase in approvals] == [
        "prepare upgrade https://chat.example.org",
        "activate https://chat.example.org",
        "activate https://chat.example.org",
    ]
    assert sum(call[0] == "upgrade-plan" for call in calls) == (2 if initial_refusal else 1)
    refused = list((state / "upgrades" / target_release).glob("refused-plan-*.json"))
    assert len(refused) == int(initial_refusal)
    if refused:
        assert read_document(refused[0])["preflight_checks_passed"] is False
        assert refused[0].stat().st_mode & 0o077 == 0
    assert sum(call[0] == "upgrade-prepare" for call in calls) == 1
    assert setup_upgrade.run_upgrade(target, state, command, detect_public_ipv4=lambda: "203.0.113.20/32")["phase"] == "already_current"


def preflight_receipt():
    source = {"release_id": "a" * 64, "bundle_sha256": "b" * 64, "source_revision": "c" * 40,
              "public_url": "https://chat.example.org"}
    target = {**source, "release_id": "d" * 64}
    value = {"operation": "upgrade-plan", "plan_id": "e" * 64,
             "source": {**source, "dirty": False}, "target": {**target, "dirty": False},
             "preflight_checks_passed": False, "refusals": ["installed_host_checks_failed_or_unverified"],
             "diagnosis": {"checks": {"loaded_server_unit": "fail", "https_health": "fail", "server_active": "unverified"}}}
    return value, source, target


def test_preflight_refusal_names_checks_without_remote_secret_output():
    value, source, target = preflight_receipt()
    value["refusals"].append("secret-remote-output")
    value["diagnosis"]["checks"].update({"secret-remote-output": "fail", "proxy_active": "secret-remote-output"})
    value["diagnosis"]["logs"] = "secret-remote-output"
    with pytest.raises(InstallError) as error:
        setup_upgrade._plan(value, source, target)
    message = str(error.value)
    assert "loaded_server_unit=fail" in message and "https_health=fail" in message
    assert "server_active=unverified" in message
    assert "no preparation or activation was attempted" in message
    assert "does not prove the existing service is healthy" in message
    assert "secret-remote-output" not in message
    assert setup_upgrade._plan(value, source, target, allow_refused=True) is value


@pytest.mark.parametrize("change", [
    {"source": {}}, {"target": {}}, {"plan_id": "bad"}, {"refusals": []},
    {"refusals": [42]}, {"preflight_checks_passed": 0}, {"preflight_checks_passed": True},
])
def test_preflight_refusal_does_not_relax_identity_or_receipt_checks(change):
    value, source, target = preflight_receipt()
    value.update(change)
    with pytest.raises(InstallError, match="differs from the retained releases"):
        setup_upgrade._plan(value, source, target, allow_refused=True)
