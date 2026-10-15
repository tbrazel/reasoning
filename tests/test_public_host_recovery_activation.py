"""Real restored SQL/crypto/lifetime leases; explicit systemd and HTTPS fixtures."""

import fcntl
import json
import subprocess
from contextlib import ExitStack

import pytest
from test_public_host_recovery import ORIGIN, RECOVERY, prepare, state
from test_public_host_recovery import bootstrap as bootstrap
from test_public_host_recovery import inputs as inputs
from test_public_host_recovery import installation as installation
from test_public_host_recovery import payload as payload

from reasoning_ledger.deployment import host, server_recovery
from reasoning_ledger.server import maintenance, recovery
from reasoning_ledger.server.store import Store


@pytest.fixture
def activation_inputs(inputs):
    engine = inputs[0]
    original_command = engine.command
    observations = inputs[-1]
    observations.update(active=False, proxy=False, starts=0, calls=[], health_failure=False, unit={}, health_calls=[])
    data = engine.root / "var/lib/reasoning"
    with ExitStack() as serving:

        def command(args, **options):
            observations["calls"].append(args)
            if args[:2] == ["systemctl", "enable"]:
                assert state(inputs)["phase"] == "activating"
                return subprocess.CompletedProcess(args, 0, stdout=b"")
            if args[:2] == ["systemctl", "start"]:
                assert args[2:] == ["reasoning-server", "reasoning-proxy"]
                current = state(inputs)
                assert current["phase"] == "activating" and not (engine.root / server_recovery.FENCE).exists()
                unit = host.server_unit(
                    current["identity"]["installation"]["release_id"], release_identity=True, maintenance_operation=RECOVERY
                ).replace("PUBLIC_ORIGIN", ORIGIN)
                assert (engine.root / "etc/systemd/system/reasoning-server.service").read_text() == unit
                if not observations["active"]:
                    serving.enter_context(
                        maintenance.serving(data, release=current["identity"]["installation"]["release_id"], operation_id=RECOVERY)
                    )
                    Store(data)
                    observations["active"] = True
                    observations["starts"] += 1
                observations["proxy"] = True
                return subprocess.CompletedProcess(args, 0, stdout=b"")
            if args[:2] in (["systemctl", "restart"], ["systemctl", "stop"]):
                pytest.fail("replacement recovery must not restart or stop a live activated installation")
            if args[:2] == ["systemctl", "show"]:
                is_server = args[-1] == "reasoning-server"
                active = observations["active"] if is_server else observations["proxy"]
                properties = {
                    "FragmentPath": "/etc/systemd/system/" + args[-1] + ".service",
                    "DropInPaths": "/" + server_recovery.DROPIN if is_server and (engine.root / server_recovery.DROPIN).exists() else "",
                    "User": "reasoning" if is_server else "caddy",
                    "Group": "reasoning" if is_server else "caddy",
                    "LoadState": "loaded",
                    "NeedDaemonReload": "no",
                    "ActiveState": "active" if active else "inactive",
                    "MainPID": "123" if active else "0",
                    "SubState": "running" if active else "dead",
                    **observations["unit"],
                }
                requested = args[3].removeprefix("--property=").split(",")
                body = "".join(key + "=" + properties[key] + "\n" for key in requested).encode()
                return subprocess.CompletedProcess(args, 0, stdout=body)
            return original_command(args, **options)

        def health(origin, **options):
            observations["health_calls"].append(options)
            assert origin == ORIGIN and observations["active"] and observations["proxy"]
            assert options["timeout"] in {0, 60}
            assert options.get("expected_release") in {None, inputs[8]["installation"]["release_id"]}
            if observations["health_failure"]:
                raise host.InstallError("HTTPS fixture unavailable")
            return {"public_url": origin, "https_ready": True, "initialized": True}

        engine.command = engine.inspector.command = engine.installer.command = command
        engine.inspector.health = health
        yield inputs


def activate(inputs, **changes):
    options = {
        "recovery_id": RECOVERY,
        "confirmed_recovery": inputs[7],
        "target_release": inputs[8]["installation"]["release_id"],
        "activation_confirmed": True,
        "skip_platform": True,
    }
    options.update(changes)
    return inputs[0].activate(**options)


def test_activation_publishes_original_state_and_completes_both_histories_without_restoring_twice(activation_inputs):
    inputs, engine = activation_inputs, activation_inputs[0]
    preparation = prepare(inputs)
    original = state(inputs)
    result = activate(inputs)
    assert result["replacement_data_published"] is result["activation_performed"] is result["recovery_completed"] is True
    assert result["https_release_verified"] is result["database_schema_verified"] is True
    assert (
        result["physical_source_fencing_verified"] is result["runtime_dependency_attestation"] is result["server_restart_fenced"] is False
    )
    assert result["restoration"] == preparation["restoration"]
    assert state(inputs)["phase"] == "completed" and state(inputs)["restoration"] == original["restoration"]
    assert not (engine._candidate(original) / "state").exists()
    data = engine.root / "var/lib/reasoning"
    assert recovery._read(data) == preparation["restoration"]
    completed = {**original["restoration"]["maintenance"], "phase": "completed"}
    assert maintenance.journal(data) == completed
    assert json.loads((data / "maintenance-history" / (RECOVERY + ".json")).read_bytes()) == completed
    assert engine._read(engine.directory / "server-recoveries" / RECOVERY / "completion.json") == result
    assert engine._read(engine.installer.state_path) == {**inputs[8]["installation"], "phase": "services_started"}
    assert (
        not engine.active.exists()
        and not (engine.root / server_recovery.FENCE).exists()
        and not (engine.root / server_recovery.DROPIN).exists()
    )
    assert inputs[-1]["starts"] == inputs[-1]["helpers"].count("recovery-prepare") == 1
    calls = len(inputs[-1]["calls"])
    assert activate(inputs) == result
    assert all(args[:2] not in (["systemctl", "start"], ["systemctl", "enable"]) for args in inputs[-1]["calls"][calls:])
    with pytest.raises(host.InstallError):
        prepare(inputs)


@pytest.mark.parametrize("phase", sorted(server_recovery.ACTIVATION_PHASES))
def test_each_durable_activation_phase_resumes_without_repeating_restore(activation_inputs, monkeypatch, phase):
    inputs, engine = activation_inputs, activation_inputs[0]
    prepare(inputs)
    write = engine._write
    interrupted = False

    def lost(path, value):
        nonlocal interrupted
        write(path, value)
        if path.name == "operation.json" and value["phase"] == phase and not interrupted:
            interrupted = True
            raise RuntimeError("durable phase reply loss fixture")

    monkeypatch.setattr(engine, "_write", lost)
    with pytest.raises(RuntimeError):
        activate(inputs)
    assert state(inputs)["phase"] == phase
    monkeypatch.setattr(engine, "_write", write)
    assert activate(inputs)["recovery_completed"] is True
    assert inputs[-1]["starts"] == inputs[-1]["helpers"].count("recovery-prepare") == 1


def test_lost_atomic_publication_reply_recognizes_canonical_state_without_overwrite(activation_inputs, monkeypatch):
    inputs, engine = activation_inputs, activation_inputs[0]
    prepare(inputs)
    rename = engine._rename
    calls = []

    def lost(source, target):
        rename(source, target)
        calls.append((source, target))
        raise RuntimeError("publication reply loss fixture")

    monkeypatch.setattr(engine, "_rename", lost)
    with pytest.raises(RuntimeError):
        activate(inputs)
    assert state(inputs)["phase"] == "publishing" and (engine.root / "var/lib/reasoning").is_dir()
    monkeypatch.setattr(engine, "_rename", rename)
    assert activate(inputs)["recovery_completed"] and len(calls) == 1


@pytest.mark.parametrize("boundary", ["start", "maintenance-finish", "unit", "binding", "dropin", "completion", "pointer"])
def test_effect_or_metadata_reply_loss_preserves_live_writes_and_original_identity(activation_inputs, monkeypatch, boundary):
    inputs, engine = activation_inputs, activation_inputs[0]
    prepare(inputs)
    command, transition, remove, write = engine.command, engine._transition, engine._remove, engine._write
    lost = False

    def lose():
        nonlocal lost
        if not lost:
            lost = True
            raise RuntimeError("effect reply loss fixture")

    def commands(args, **options):
        result = command(args, **options)
        if boundary == "start" and args[:2] == ["systemctl", "start"]:
            lose()
        if boundary == "maintenance-finish" and args[:4] == ["runuser", "-u", "reasoning", "--"] and args[7] == "maintenance-finish":
            lose()
        return result

    def transitions(path, before, after, **options):
        transition(path, before, after, **options)
        if boundary == "unit" and path.name == "reasoning-server.service":
            lose()
        if boundary == "binding" and path.name == "installation.json":
            lose()

    def removals(path, expected, **options):
        remove(path, expected, **options)
        if boundary == "dropin" and path == engine.root / server_recovery.DROPIN:
            lose()
        if boundary == "pointer" and path == engine.active:
            lose()

    def writes(path, value):
        write(path, value)
        if boundary == "completion" and path.name == "completion.json":
            lose()

    monkeypatch.setattr(engine, "command", commands)
    monkeypatch.setattr(engine, "_transition", transitions)
    monkeypatch.setattr(engine, "_remove", removals)
    monkeypatch.setattr(engine, "_write", writes)
    with pytest.raises(RuntimeError):
        activate(inputs)
    data = engine.root / "var/lib/reasoning"
    if inputs[-1]["active"]:
        store = Store(data)
        with store.transaction(write=True) as database:
            database.execute("UPDATE projects SET name='live-write-evidence' WHERE id IN (SELECT id FROM projects LIMIT 1)")
    assert activate(inputs)["recovery_completed"]
    assert inputs[-1]["starts"] == inputs[-1]["helpers"].count("recovery-prepare") == 1
    if boundary != "unit":
        with Store(data).transaction() as database:
            assert database.execute("SELECT name FROM projects WHERE name='live-write-evidence'").fetchone()


@pytest.mark.parametrize(
    "change",
    [
        {"activation_confirmed": False},
        {"activation_confirmed": 1},
        {"recovery_id": "0" * 64},
        {"confirmed_recovery": "0" * 64},
        {"target_release": "0" * 64},
    ],
)
def test_wrong_activation_confirmation_never_publishes_or_starts(activation_inputs, change):
    prepare(activation_inputs)
    calls = len(activation_inputs[-1]["calls"])
    with pytest.raises(host.InstallError):
        activate(activation_inputs, **change)
    assert not (activation_inputs[0].root / "var/lib/reasoning").exists()
    assert len(activation_inputs[-1]["calls"]) == calls


def test_existing_canonical_data_is_never_overwritten(activation_inputs):
    inputs, engine = activation_inputs, activation_inputs[0]
    prepare(inputs)
    data = engine.root / "var/lib/reasoning"
    data.mkdir(mode=0o700)
    marker = data / "operator-evidence"
    marker.write_bytes(b"retain")
    with pytest.raises(host.InstallError, match="existing canonical"):
        activate(inputs)
    assert marker.read_bytes() == b"retain" and state(inputs)["phase"] == "prepared"


def test_failed_https_leaves_activation_owned_and_preserves_live_data(activation_inputs):
    inputs = activation_inputs
    prepare(inputs)
    inputs[-1]["health_failure"] = True
    with pytest.raises(host.InstallError, match="HTTPS"):
        activate(inputs)
    assert state(inputs)["phase"] == "activating" and inputs[0].active.exists()
    data = inputs[0].root / "var/lib/reasoning"
    assert maintenance.journal(data)["phase"] == "activation_started"
    with Store(data).transaction(write=True) as database:
        database.execute("UPDATE projects SET name='live-write-evidence' WHERE id IN (SELECT id FROM projects LIMIT 1)")
    inputs[-1]["health_failure"] = False
    assert activate(inputs)["recovery_completed"]
    with Store(data).transaction() as database:
        assert database.execute("SELECT name FROM projects WHERE name='live-write-evidence'").fetchone()
    assert inputs[-1]["starts"] == inputs[-1]["helpers"].count("recovery-prepare") == 1


@pytest.mark.parametrize("evidence", ["root_history", "application_history", "recovery_history", "receipt", "key", "backup"])
def test_completed_retry_requires_original_root_and_application_evidence(activation_inputs, evidence):
    inputs, engine = activation_inputs, activation_inputs[0]
    prepare(inputs)
    activate(inputs)
    data, operation = engine.root / "var/lib/reasoning", engine.directory / "server-recoveries" / RECOVERY
    paths = {
        "root_history": operation / "completion.json",
        "application_history": data / "maintenance-history" / (RECOVERY + ".json"),
        "recovery_history": data / recovery.HISTORY / (RECOVERY + ".json"),
        "receipt": data / recovery.RECEIPT,
        "key": operation / "backup.key",
        "backup": operation / "source.rnbk",
    }
    paths[evidence].unlink()
    calls = len(inputs[-1]["calls"])
    with pytest.raises(host.InstallError):
        activate(inputs)
    assert all(args[:2] not in (["systemctl", "start"], ["systemctl", "enable"]) for args in inputs[-1]["calls"][calls:])


@pytest.mark.parametrize("field,value", [("MainPID", "0"), ("DropInPaths", "/other.conf"), ("NeedDaemonReload", "yes"), ("User", "root")])
def test_started_process_must_match_loaded_service_identity(activation_inputs, monkeypatch, field, value):
    inputs, engine = activation_inputs, activation_inputs[0]
    prepare(inputs)
    remove = engine._remove

    def change(path, expected, **options):
        remove(path, expected, **options)
        if path == engine.root / server_recovery.FENCE:
            inputs[-1]["unit"][field] = value

    monkeypatch.setattr(engine, "_remove", change)
    with pytest.raises(host.InstallError, match="loaded"):
        activate(inputs)
    assert state(inputs)["phase"] == "activating" and not (engine.directory / "server-recoveries" / RECOVERY / "completion.json").exists()


def test_original_bootstrap_lock_must_be_exclusively_owned(activation_inputs):
    prepare(activation_inputs)
    with (activation_inputs[0].directory / "bootstrap.lock").open("rb") as lease:
        fcntl.flock(lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(host.InstallError):
            activate(activation_inputs)
    assert state(activation_inputs)["phase"] == "prepared"


@pytest.mark.parametrize("evidence", ["pointer", "catalog", "unit", "fence", "key", "restoration", "database"])
def test_changed_prepared_evidence_never_reaches_publication_or_activation(activation_inputs, evidence):
    inputs, engine = activation_inputs, activation_inputs[0]
    prepare(inputs)
    original = state(inputs)
    data = engine._candidate(original) / "state"
    operation = engine.directory / "server-recoveries" / RECOVERY
    paths = {
        "pointer": engine.active,
        "catalog": engine.root / "etc/reasoning/compute-images.json",
        "unit": engine.root / "etc/systemd/system/reasoning-server.service",
        "fence": engine.root / server_recovery.FENCE,
        "key": operation / "backup.key",
        "restoration": data / recovery.RECEIPT,
    }
    if evidence == "database":
        import sqlite3

        with sqlite3.connect(data / "server.sqlite3") as database:
            database.execute("UPDATE projects SET name='unexpected prepared mutation'")
    else:
        paths[evidence].unlink()
    with pytest.raises(host.InstallError):
        activate(inputs)
    assert state(inputs) == original and not (engine.root / "var/lib/reasoning").exists() and inputs[-1]["starts"] == 0


def test_managed_mail_activates_under_exact_unchanged_input_and_live_worker_health(activation_inputs):
    from reasoning_ledger.server.mail import MailConfig

    inputs, engine = activation_inputs, activation_inputs[0]
    mail = host.canonical(MailConfig("operator@example.org", "private-fixture-mail-key", 10).document())
    manifest, release, _ = host.inspect_bundle(inputs[1])
    _, confirmation = server_recovery.recovery_identity(
        manifest, release, inputs[2], ORIGIN, inputs[4], inputs[6], maximum=8 * 1024**3, mail=mail
    )
    prepare(inputs, confirmed_recovery=confirmation, mail=mail)
    result = activate(inputs, confirmed_recovery=confirmation)
    assert (engine.root / "var/lib/reasoning/mail.json").read_bytes() == mail
    assert all(item["require_mail"] is True for item in inputs[-1]["health_calls"])
    assert "private-fixture-mail-key" not in host.canonical(result).decode()


def test_original_vm_identity_is_rechecked_before_publication_and_on_completed_replay(activation_inputs, monkeypatch):
    inputs, engine = activation_inputs, activation_inputs[0]
    request = {"format": 1, "plan_id": "a" * 64, "deployment_id": "b" * 32, "droplet_id": 123}
    engine.installer._directory("etc")
    host.atomic_write(
        engine.root / "etc/reasoning-provisioning.json", host.canonical({key: request[key] for key in ("plan_id", "deployment_id")})
    )
    live = [123]
    monkeypatch.setattr(host.HostInstaller, "_droplet_id", staticmethod(lambda: live[0]))
    engine.inspector.droplet_id = lambda: live[0]
    manifest, release, _ = host.inspect_bundle(inputs[1])
    _, confirmation = server_recovery.recovery_identity(
        manifest, release, inputs[2], ORIGIN, inputs[4], inputs[6], maximum=8 * 1024**3, provisioning=request
    )
    prepare(inputs, confirmed_recovery=confirmation, provisioning=request)
    live[0] = 124
    with pytest.raises(host.InstallError):
        activate(inputs, confirmed_recovery=confirmation)
    assert not (engine.root / "var/lib/reasoning").exists()
    live[0] = 123
    assert activate(inputs, confirmed_recovery=confirmation)["recovery_completed"]
    live[0] = 124
    with pytest.raises(host.InstallError):
        activate(inputs, confirmed_recovery=confirmation)


@pytest.mark.parametrize(
    "options",
    [
        [],
        ["--activate"],
        ["--activate", "--confirm-activation", "--target-release", "a" * 64, "--backup", "snapshot"],
        ["--activate", "--confirm-activation", "--target-release", "a" * 64, "--confirm-execution-fenced"],
    ],
)
def test_root_cli_refuses_incomplete_or_mixed_modes_before_host_access(monkeypatch, options):
    monkeypatch.setattr(server_recovery, "HostRecovery", lambda: pytest.fail("invalid CLI must not access the host"))
    with pytest.raises(SystemExit) as result:
        server_recovery.main(["--recovery-id", RECOVERY, "--confirm-recovery", "b" * 64, *options])
    assert result.value.code == 2
