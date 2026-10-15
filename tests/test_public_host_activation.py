"""Real SQL/maintenance leases; explicit systemd, package and HTTPS fixtures."""

import fcntl
import hashlib
import json
import subprocess
import sys
from contextlib import ExitStack

import pytest
from test_public_host_upgrade import OPERATION, ORIGIN, operation, pack, prepare, refresh_plan, state
from test_public_host_upgrade import bootstrap as bootstrap
from test_public_host_upgrade import installed as installed
from test_public_host_upgrade import payload as payload
from test_public_host_upgrade import prepared_inputs as prepared_inputs

from reasoning_ledger.deployment import host
from reasoning_ledger.deployment import server_upgrade as upgrade
from reasoning_ledger.server import backup as snapshots
from reasoning_ledger.server import maintenance
from reasoning_ledger.server import store as store_module
from reasoning_ledger.server.mail import MailConfig
from reasoning_ledger.server.store import Store


@pytest.fixture
def activation_inputs(prepared_inputs):
    engine, *_, observation = prepared_inputs
    command = engine.command
    data = engine.root / "var/lib/reasoning"
    observation.update(starts=0, health_failure=False, health_calls=[])
    with ExitStack() as serving:

        def commands(args, **options):
            if args[:3] == ["systemctl", "start", "reasoning-server"]:
                original = state(prepared_inputs)
                assert original["phase"] == "activating"
                assert not (engine.root / upgrade.FENCE).exists()
                target = original["identity"]["target"]
                assert engine._read(engine.directory / "installation.json") == {**target, "phase": "packages"}
                expected = host.server_unit(target["release_id"], release_identity=True, maintenance_operation=OPERATION)
                assert (engine.root / "etc/systemd/system/reasoning-server.service").read_text() == expected.replace(
                    "PUBLIC_ORIGIN", ORIGIN
                )
                if not observation["active"]:
                    # Hold the real shared lifetime lease and record real
                    # activation before Store construction. This is not an ASGI
                    # lifespan/systemd/TLS test, and no execution worker starts.
                    serving.enter_context(maintenance.serving(data, release=target["release_id"], operation_id=OPERATION))
                    Store(data)
                    observation["starts"] += 1
                    observation["active"] = True
                observation["calls"].append(args)
                return subprocess.CompletedProcess(args, 0, stdout=b"")
            if args[:2] == ["systemctl", "stop"]:
                serving.close()
            try:
                return command(args, **options)
            except SystemExit:
                # An actual subprocess would return failure; never forward
                # arbitrary service diagnostics as a valid helper receipt.
                return subprocess.CompletedProcess(args, 2, stdout=b"")

        def health(origin, **options):
            observation["health_calls"].append(options)
            assert origin == ORIGIN and options["timeout"] in {0, 60}
            assert options.get("expected_release") in {
                None,
                prepared_inputs[5]["source"]["release_id"],
                prepared_inputs[5]["target"]["release_id"],
            }
            if options.get("expected_release") == prepared_inputs[5]["target"]["release_id"]:
                assert observation["active"]
                assert maintenance.journal(data)["phase"] in {"activation_started", "completed"}
            if observation["health_failure"]:
                raise host.InstallError("HTTPS fixture unavailable")
            return {"public_url": origin, "https_ready": True, "initialized": False}

        engine.command = engine.inspector.command = engine.installer.command = commands
        engine.inspector.health = health
        yield prepared_inputs


def activate(inputs, **changes):
    options = {
        "operation_id": OPERATION,
        "confirmed_plan": inputs[5]["plan_id"],
        "target_release": inputs[5]["target"]["release_id"],
        "activation_confirmed": True,
        "skip_platform": True,
    }
    options.update(changes)
    return inputs[0].activate(**options)


def test_activation_completes_original_bindings_history_and_fresh_diagnosis_without_restarting(activation_inputs):
    inputs = activation_inputs
    prepare(inputs)
    before = state(inputs)
    result = activate(inputs)
    engine = inputs[0]
    assert result["upgrade_completed"] is result["activation_performed"] is result["https_release_verified"] is True
    assert result["runtime_dependency_attestation"] is result["server_restart_fenced"] is False
    assert state(inputs)["phase"] == "completed"
    assert state(inputs)["migration"] == before["migration"]
    assert not engine.active.exists() and not (engine.root / upgrade.FENCE).exists() and not (engine.root / upgrade.DROPIN).exists()
    assert engine._read(operation(inputs) / "completion.json") == result
    assert engine._read(engine.directory / "installation.json") == {**inputs[5]["target"], "phase": "services_started"}
    current = maintenance.journal(engine.root / "var/lib/reasoning")
    assert current == {**before["migration"], "phase": "completed"}
    history = engine.root / "var/lib/reasoning/maintenance-history" / (OPERATION + ".json")
    assert json.loads(history.read_bytes()) == current
    assert inputs[-1]["starts"] == 1
    assert inputs[-1]["helpers"].count("backup") == inputs[-1]["helpers"].count("migrate-offline") == 1
    calls = len(inputs[-1]["calls"])
    assert activate(inputs) == result
    assert all(
        args[:2] not in (["systemctl", "start"], ["systemctl", "stop"], ["systemctl", "restart"]) for args in inputs[-1]["calls"][calls:]
    )


@pytest.mark.parametrize("phase", ["switching", "activating", "ready", "finalizing", "completed"])
def test_each_durable_activation_boundary_resumes_without_new_snapshot_or_migration(activation_inputs, monkeypatch, phase):
    inputs = activation_inputs
    prepare(inputs)
    engine = inputs[0]
    original = engine._write
    interrupted = False

    def write(path, value):
        nonlocal interrupted
        original(path, value)
        if path.name == "operation.json" and value["phase"] == phase and not interrupted:
            interrupted = True
            raise RuntimeError("durable interruption fixture")

    monkeypatch.setattr(engine, "_write", write)
    with pytest.raises(RuntimeError, match="durable interruption"):
        activate(inputs)
    assert state(inputs)["phase"] == phase
    monkeypatch.setattr(engine, "_write", original)
    assert activate(inputs)["upgrade_completed"] is True
    assert inputs[-1]["starts"] == 1
    assert inputs[-1]["helpers"].count("backup") == inputs[-1]["helpers"].count("migrate-offline") == 1


@pytest.mark.parametrize(
    "confirmation",
    [
        {"activation_confirmed": False},
        {"activation_confirmed": 1},
        {"operation_id": "bad"},
        {"confirmed_plan": "0" * 64},
        {"target_release": "0" * 64},
    ],
)
def test_activation_requires_exact_original_confirmation_before_any_service_changes(activation_inputs, confirmation):
    prepare(activation_inputs)
    before = len(activation_inputs[-1]["calls"])
    with pytest.raises(host.InstallError):
        activate(activation_inputs, **confirmation)
    assert activation_inputs[-1]["active"] is False
    assert (activation_inputs[0].root / upgrade.FENCE).exists()
    assert len(activation_inputs[-1]["calls"]) == before


@pytest.mark.parametrize("command_name", ["start", "maintenance-finish"])
def test_lost_successful_activation_response_preserves_live_writes(activation_inputs, monkeypatch, command_name):
    inputs = activation_inputs
    prepare(inputs)
    engine = inputs[0]
    command = engine.command
    interrupted = False

    def lost(args, **options):
        nonlocal interrupted
        response = command(args, **options)
        if command_name in args and not interrupted:
            interrupted = True
            store = Store(engine.root / "var/lib/reasoning")
            store.bootstrap(store._setup_token, "owner@example.org", "Owner", "activation-fixture-password", "New live organization")
            raise RuntimeError("lost activation response fixture")
        return response

    monkeypatch.setattr(engine, "command", lost)
    with pytest.raises(RuntimeError, match="lost activation"):
        activate(inputs)
    assert state(inputs)["phase"] == ("activating" if command_name == "start" else "ready")
    monkeypatch.setattr(engine, "command", command)
    assert activate(inputs)["upgrade_completed"] is True
    store = Store(engine.root / "var/lib/reasoning")
    with store.transaction() as database:
        assert database.execute("SELECT name FROM organizations").fetchone()[0] == "New live organization"
        assert database.execute("SELECT email FROM users").fetchone()[0] == "owner@example.org"
        assert database.execute("SELECT count(*) FROM sessions").fetchone()[0] == 1
    assert inputs[-1]["starts"] == 1


def test_failed_https_never_completes_or_restores_an_already_activated_target(activation_inputs):
    inputs = activation_inputs
    prepare(inputs)
    inputs[-1]["health_failure"] = True
    with pytest.raises(host.InstallError, match="HTTPS"):
        activate(inputs)
    assert state(inputs)["phase"] == "activating" and inputs[-1]["active"] is True
    assert maintenance.journal(inputs[0].root / "var/lib/reasoning")["phase"] == "activation_started"
    assert (operation(inputs) / "source.rnbk").exists() and (operation(inputs) / "backup.key").exists()
    assert not (operation(inputs) / "completion.json").exists()
    with pytest.raises(host.InstallError, match="external effects"):
        prepare(inputs)
    inputs[-1]["health_failure"] = False
    assert activate(inputs)["upgrade_completed"] is True


@pytest.mark.parametrize(
    "field,value",
    [
        ("MainPID", "0"),
        ("ActiveState", "inactive"),
        ("SubState", "dead"),
        ("NeedDaemonReload", "yes"),
        ("User", "root"),
        ("DropInPaths", "/unknown.conf"),
    ],
)
def test_start_success_is_not_proof_of_loaded_target_identity_or_process_readiness(activation_inputs, field, value):
    prepare(activation_inputs)
    original = activation_inputs[0]._remove

    def remove(path, expected, **options):
        original(path, expected, **options)
        if path == activation_inputs[0].root / upgrade.FENCE:
            activation_inputs[-1]["loaded"][field] = value

    activation_inputs[0]._remove = remove
    with pytest.raises(host.InstallError, match="target server"):
        activate(activation_inputs)
    assert state(activation_inputs)["phase"] == "activating"
    assert not (operation(activation_inputs) / "completion.json").exists()


@pytest.mark.parametrize("record", ["backup", "key", "original-installation", "catalog", "unit", "dropin", "pointer", "fence"])
def test_activation_refuses_changed_original_evidence_before_start(activation_inputs, record):
    inputs = activation_inputs
    prepare(inputs)
    engine = inputs[0]
    paths = {
        "backup": operation(inputs) / "source.rnbk",
        "key": operation(inputs) / "backup.key",
        "original-installation": operation(inputs) / "source-installation.json",
        "catalog": engine.root / "etc/reasoning/compute-images.json",
        "unit": engine.root / "etc/systemd/system/reasoning-server.service",
        "dropin": engine.root / upgrade.DROPIN,
        "pointer": engine.active,
        "fence": engine.root / upgrade.FENCE,
    }
    paths[record].write_bytes(b"changed original evidence fixture")
    with pytest.raises(host.InstallError):
        activate(inputs)
    assert paths[record].read_bytes() == b"changed original evidence fixture"
    assert inputs[-1]["active"] is False and inputs[-1]["starts"] == 0


@pytest.mark.parametrize("record", ["root-missing", "root-changed", "app-missing", "app-changed"])
def test_completed_replay_requires_immutable_root_and_application_history(activation_inputs, record):
    inputs = activation_inputs
    prepare(inputs)
    activate(inputs)
    path = (
        operation(inputs) / "completion.json"
        if record.startswith("root")
        else inputs[0].root / "var/lib/reasoning/maintenance-history" / (OPERATION + ".json")
    )
    if record.endswith("missing"):
        path.unlink()
    else:
        path.write_bytes(b"changed completed evidence fixture")
    with pytest.raises(host.InstallError):
        activate(inputs)
    assert not path.exists() if record.endswith("missing") else path.read_bytes() == b"changed completed evidence fixture"
    assert state(inputs)["phase"] == "completed" and inputs[-1]["starts"] == 1


def test_activation_requires_original_exclusive_bootstrap_lease(activation_inputs):
    prepare(activation_inputs)
    with (activation_inputs[0].directory / "bootstrap.lock").open("rb") as fd:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(host.InstallError):
            activate(activation_inputs)
    assert activation_inputs[-1]["starts"] == 0


def test_activation_rebinds_mail_and_cloud_without_changing_credentials_or_vm_identity(activation_inputs):
    engine = activation_inputs[0]
    source = activation_inputs[5]["source"]
    mail = host.canonical(MailConfig("invites@example.org", "private-activation-mail-fixture", 100).document())
    host.atomic_write(engine.root / "var/lib/reasoning/mail.json", mail)
    host.atomic_write(engine.directory / "server-mail.json", host.canonical({"installation": source, "mail": host.mail_binding(mail)}))
    request = {"format": 1, "plan_id": "a" * 64, "deployment_id": "b" * 32, "droplet_id": 456}
    host.atomic_write(engine.directory / "server-provisioning.json", host.canonical({"format": 1, "binding": source, "request": request}))
    host.atomic_write(engine.root / "etc/reasoning-provisioning.json", host.canonical({"plan_id": "a" * 64, "deployment_id": "b" * 32}))
    inputs = refresh_plan(activation_inputs)
    prepare(inputs)
    result = activate(inputs)
    target = inputs[5]["target"]
    assert engine._read(engine.directory / "server-mail.json") == {"installation": target, "mail": host.mail_binding(mail)}
    assert engine._read(engine.directory / "server-provisioning.json") == {"format": 1, "binding": target, "request": request}
    assert (engine.root / "var/lib/reasoning/mail.json").read_bytes() == mail
    assert any(
        options["require_mail"] and options.get("expected_release") == target["release_id"] for options in inputs[-1]["health_calls"]
    )
    assert "private-activation-mail-fixture" not in json.dumps(result)
    engine.inspector.droplet_id = lambda: 999
    with pytest.raises(host.InstallError, match="original DigitalOcean VM"):
        activate(inputs)


@pytest.mark.parametrize(
    "boundary",
    ["installation.json", "reasoning-server.service", "server-upgrade-fence", "90-reasoning-upgrade.conf", "active-server-upgrade.json"],
)
def test_lost_binding_unit_or_owned_record_transition_resumes(activation_inputs, monkeypatch, boundary):
    inputs = activation_inputs
    prepare(inputs)
    engine = inputs[0]
    method = "_remove" if boundary in {"server-upgrade-fence", "90-reasoning-upgrade.conf", "active-server-upgrade.json"} else "_transition"
    original = getattr(engine, method)
    interrupted = False

    def lost(path, *args, **options):
        nonlocal interrupted
        original(path, *args, **options)
        if path.name == boundary and not interrupted:
            interrupted = True
            raise RuntimeError("lost transition fixture")

    monkeypatch.setattr(engine, method, lost)
    with pytest.raises(RuntimeError, match="lost transition"):
        activate(inputs)
    monkeypatch.setattr(engine, method, original)
    assert activate(inputs)["upgrade_completed"] is True
    assert inputs[-1]["starts"] == 1


@pytest.mark.parametrize("helper", ["maintenance-observe", "maintenance-finish"])
@pytest.mark.parametrize("change", ["operation", "secret-field", "false-flag"])
def test_false_or_secret_application_receipts_never_complete_the_host(activation_inputs, monkeypatch, helper, change):
    inputs = activation_inputs
    prepare(inputs)
    engine = inputs[0]
    original = engine.command

    def corrupt(args, **options):
        result = original(args, **options)
        if helper in args:
            value = json.loads(result.stdout)
            if change == "operation":
                value["operation_id"] = "0" * 64
            elif change == "secret-field":
                value["secret"] = "private-receipt-fixture"
            else:
                value["health_verified_by_command"] = True
            return subprocess.CompletedProcess(args, 0, stdout=json.dumps(value).encode())
        return result

    monkeypatch.setattr(engine, "command", corrupt)
    with pytest.raises(host.InstallError) as error:
        activate(inputs)
    assert "private-receipt-fixture" not in str(error.value)
    assert state(inputs)["phase"] in {"activating", "ready"}
    assert not (operation(inputs) / "completion.json").exists()
    monkeypatch.setattr(engine, "command", original)
    assert activate(inputs)["upgrade_completed"] is True


def test_completed_retry_requires_original_identity_not_a_modified_plan(activation_inputs):
    prepare(activation_inputs)
    activate(activation_inputs)
    engine = activation_inputs[0]
    path = operation(activation_inputs) / "operation.json"
    value = engine._read(path)
    value["identity"]["configuration"]["compute_catalog_sha256"] = hashlib.sha256(b"other config").hexdigest()
    engine._write(path, value)
    with pytest.raises(host.InstallError):
        activate(activation_inputs)
    assert not engine.active.exists()


def test_final_unit_write_interruption_recovers_without_restarting_or_reverting_live_state(activation_inputs, monkeypatch):
    inputs = activation_inputs
    prepare(inputs)
    engine = inputs[0]
    original = engine._transition
    units = 0

    def lost(path, *args, **options):
        nonlocal units
        original(path, *args, **options)
        if path.name == "reasoning-server.service":
            units += 1
            if units == 2:
                raise RuntimeError("final unit transition fixture")

    monkeypatch.setattr(engine, "_transition", lost)
    with pytest.raises(RuntimeError, match="final unit"):
        activate(inputs)
    assert state(inputs)["phase"] == "finalizing"
    assert maintenance.journal(engine.root / "var/lib/reasoning")["phase"] == "completed"
    monkeypatch.setattr(engine, "_transition", original)
    assert activate(inputs)["upgrade_completed"] is True
    assert inputs[-1]["starts"] == 1


def test_switching_recovers_a_unit_write_before_its_daemon_reload(activation_inputs, monkeypatch):
    inputs = activation_inputs
    prepare(inputs)
    engine = inputs[0]
    transition, command = engine._transition, engine.command
    interrupted = False

    def lost(path, *args, **options):
        nonlocal interrupted
        transition(path, *args, **options)
        if path.name == "reasoning-server.service" and not interrupted:
            inputs[-1]["loaded"]["NeedDaemonReload"] = "yes"
            interrupted = True
            raise RuntimeError("unit written before reload fixture")

    def reload(args, **options):
        if args == ["systemctl", "daemon-reload"]:
            inputs[-1]["loaded"].pop("NeedDaemonReload", None)
        return command(args, **options)

    monkeypatch.setattr(engine, "_transition", lost)
    monkeypatch.setattr(engine, "command", reload)
    with pytest.raises(RuntimeError, match="unit written"):
        activate(inputs)
    assert state(inputs)["phase"] == "switching" and inputs[-1]["active"] is False
    assert activate(inputs)["upgrade_completed"] is True
    assert inputs[-1]["starts"] == 1


@pytest.mark.parametrize("change", ["ready-flag", "origin", "initialized"])
def test_malformed_readiness_never_acknowledges_application_completion(activation_inputs, change):
    inputs = activation_inputs
    prepare(inputs)
    values = {"public_url": ORIGIN, "https_ready": True, "initialized": False}
    if change == "ready-flag":
        values["https_ready"] = 1
    elif change == "origin":
        values["public_url"] = "https://other.example.org"
    else:
        values["initialized"] = "false"
    inputs[0].inspector.health = lambda *_args, **_options: values
    with pytest.raises(host.InstallError, match="HTTPS release readiness"):
        activate(inputs)
    assert state(inputs)["phase"] == "activating"
    assert "maintenance-finish" not in inputs[-1]["helpers"]


def test_preparation_only_target_cannot_be_activated(activation_inputs):
    inputs = activation_inputs
    engine, source, target, source_checksum, *_ = inputs
    directory = engine.installer._directory("var/lib/reasoning-deploy/preparation-only-fixture", 0o700)
    host.extract_bundle(target, directory)
    files = {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in directory.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    }
    target = pack(
        target.parent / "preparation-only.tar.gz",
        files,
        manifest_change=lambda value: value.update(
            database_schema=inputs[5]["source_database_schema"],
            installation_features=[feature for feature in host.INSTALLATION_FEATURES if feature != "server-upgrade-activation-v1"],
            source_revision="e" * 40,
        ),
    )
    target.chmod(0o600)
    _, _, checksum = host.inspect_bundle(target)
    adjusted = refresh_plan((engine, source, target, source_checksum, checksum, {}, inputs[-1]))
    prepare(adjusted)
    with pytest.raises(host.InstallError, match="supported release-change plan"):
        activate(adjusted)
    assert state(adjusted)["phase"] == "prepared" and inputs[-1]["starts"] == 0


def test_activation_executes_and_observes_a_real_synthetic_next_schema(activation_inputs, monkeypatch):
    inputs = activation_inputs
    engine, source, target, source_checksum, *_ = inputs
    future = (
        *maintenance.MIGRATIONS,
        (
            "CREATE TABLE activation_schema_probe(id INTEGER PRIMARY KEY, value TEXT)",
            "INSERT INTO activation_schema_probe VALUES(1,'real SQL target')",
        ),
    )
    directory = engine.installer._directory("var/lib/reasoning-deploy/next-schema-fixture", 0o700)
    host.extract_bundle(target, directory)
    files = {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in directory.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    }
    target = pack(
        target.parent / "next-activation-schema.tar.gz",
        files,
        manifest_change=lambda value: value.update(
            database_schema=len(future), installation_features=list(host.INSTALLATION_FEATURES), source_revision="e" * 40
        ),
    )
    target.chmod(0o600)
    _, target_release, checksum = host.inspect_bundle(target)
    adjusted = refresh_plan((engine, source, target, source_checksum, checksum, {}, inputs[-1]))
    command = engine.command

    def target_helper(args, **options):
        if "migrate-offline" in args or "maintenance-verify" in args:
            with monkeypatch.context() as patch:
                patch.setattr(maintenance, "MIGRATIONS", future)
                patch.setattr(snapshots, "MIGRATIONS", future)
                return command(args, **options)
        return command(args, **options)

    monkeypatch.setattr(engine, "command", target_helper)
    prepare(adjusted)
    monkeypatch.setattr(maintenance, "MIGRATIONS", future)
    monkeypatch.setattr(snapshots, "MIGRATIONS", future)
    monkeypatch.setattr(store_module, "MIGRATIONS", future)

    def health(origin, **options):
        assert origin == ORIGIN and options.get("expected_release") in {None, target_release}
        assert inputs[-1]["active"] and maintenance.journal(engine.root / "var/lib/reasoning")["target_schema"] == len(future)
        return {"public_url": origin, "https_ready": True, "initialized": False}

    monkeypatch.setattr(engine.inspector, "health", health)
    assert activate(adjusted)["upgrade_completed"] is True
    with Store(engine.root / "var/lib/reasoning").transaction() as database:
        assert database.execute("PRAGMA user_version").fetchone()[0] == len(future)
        assert database.execute("SELECT value FROM activation_schema_probe").fetchone()[0] == "real SQL target"
    assert inputs[-1]["starts"] == 1 and inputs[-1]["helpers"].count("backup") == 1


@pytest.mark.parametrize(
    "options",
    [
        [],
        ["--activate"],
        ["--activate", "--confirm-activation"],
        ["--confirm-activation"],
        ["--activate", "--confirm-activation", "--confirm-target-release", "d" * 64, "--public-url", ORIGIN],
        ["--activate", "--confirm-activation", "--confirm-target-release", "d" * 64, "--maximum-bytes", "1048576"],
    ],
)
def test_root_cli_rejects_incomplete_or_mixed_modes_before_host_access(monkeypatch, options):
    monkeypatch.setattr(upgrade, "HostUpgrade", lambda: pytest.fail("invalid CLI must not inspect or change a host"))
    with pytest.raises(SystemExit) as error:
        upgrade.main(["--operation-id", OPERATION, "--confirm-plan", "a" * 64, *options])
    assert error.value.code == 2


def test_real_observation_subprocess_verifies_completed_state_under_held_server_lease(activation_inputs):
    inputs = activation_inputs
    prepare(inputs)
    activate(inputs)
    engine = inputs[0]
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "reasoning_ledger.server.cli",
            "--data-dir",
            str(engine.root / "var/lib/reasoning"),
            "maintenance-observe",
            "--operation-id",
            OPERATION,
            "--target-release",
            inputs[5]["target"]["release_id"],
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode()
    value = json.loads(result.stdout)
    assert value == {
        **state(inputs)["migration"],
        "phase": "completed",
        "activation_state_verified": True,
        "database_schema_verified": True,
        "server_started_by_command": False,
        "health_verified_by_command": False,
    }
    assert inputs[-1]["starts"] == 1
