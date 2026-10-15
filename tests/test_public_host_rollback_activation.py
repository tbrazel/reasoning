"""Actual source restoration/leases; explicit package, systemd and HTTPS fixtures."""

import json
import sqlite3
import subprocess
from contextlib import ExitStack

import pytest
from test_public_host_activation import activate
from test_public_host_activation import activation_inputs as activation_inputs
from test_public_host_activation import bootstrap as bootstrap
from test_public_host_activation import installed as installed
from test_public_host_activation import payload as payload
from test_public_host_activation import prepared_inputs as prepared_inputs
from test_public_host_rollback import ROLLBACK, candidate, rollback_prepare, rollback_state
from test_public_host_upgrade import OPERATION, ORIGIN, operation, pack, prepare, refresh_plan, state

from reasoning_ledger.deployment import host
from reasoning_ledger.deployment import server_upgrade as upgrade
from reasoning_ledger.server import backup as snapshots
from reasoning_ledger.server import maintenance, rollback
from reasoning_ledger.server import store as store_module
from reasoning_ledger.server.mail import MailConfig
from reasoning_ledger.server.store import Store


@pytest.fixture
def rollback_inputs(activation_inputs):
    inputs = activation_inputs
    engine, *_, observation = inputs
    command, health = engine.command, engine.inspector.health
    data = engine.root / "var/lib/reasoning"
    observation["source_starts"] = 0
    with ExitStack() as serving:

        def commands(args, **options):
            if args[:3] == ["systemctl", "start", "reasoning-server"] and (operation(inputs) / "rollback.json").exists():
                assert rollback_state(inputs)["phase"] == "activating"
                assert not (engine.root / upgrade.FENCE).exists()
                source = inputs[5]["source"]
                assert engine._read(engine.directory / "installation.json") == {**source, "phase": "packages"}
                assert (engine.root / "etc/systemd/system/reasoning-server.service").read_text() == host.server_unit(
                    source["release_id"], release_identity=True
                ).replace("PUBLIC_ORIGIN", ORIGIN)
                assert rollback._read(data, rollback.JOURNAL)["phase"] == "released"
                if not observation["active"]:
                    serving.enter_context(maintenance.serving(data, release=source["release_id"]))
                    Store(data)
                    observation["source_starts"] += 1
                    observation["active"] = True
                observation["calls"].append(args)
                return subprocess.CompletedProcess(args, 0, stdout=b"")
            if args[:2] == ["systemctl", "stop"]:
                serving.close()
            return command(args, **options)

        def source_health(origin, **options):
            if options.get("expected_release") == inputs[5]["source"]["release_id"] and (operation(inputs) / "rollback.json").exists():
                assert observation["active"]
                assert rollback._read(data, rollback.JOURNAL)["phase"] == "released"
            return health(origin, **options)

        engine.command = engine.installer.command = engine.inspector.command = commands
        engine.inspector.health = source_health
        yield inputs


def rollback_activate(inputs, **changes):
    options = {
        "operation_id": OPERATION,
        "rollback_id": ROLLBACK,
        "confirmed_plan": inputs[5]["plan_id"],
        "source_release": inputs[5]["source"]["release_id"],
        "target_release": inputs[5]["target"]["release_id"],
        "activation_confirmed": True,
        "skip_platform": True,
    }
    options.update(changes)
    return inputs[0].rollback_activate(**options)


def ready(inputs, *, upgrade_completed=True):
    prepare(inputs)
    if upgrade_completed:
        activate(inputs)
    rollback_prepare(inputs)


def test_completed_rollback_restores_bindings_retains_evidence_and_retries_without_effects(rollback_inputs):
    inputs = rollback_inputs
    ready(inputs)
    engine = inputs[0]
    original = state(inputs)
    result = rollback_activate(inputs)
    assert result["rollback_completed"] is result["source_activation_performed"] is result["https_release_verified"] is True
    assert result["database_schema_verified"] is result["displaced_state_retained"] is True
    assert result["server_restart_fenced"] is result["displaced_database_validated"] is result["runtime_dependency_attestation"] is False
    assert rollback_state(inputs)["phase"] == "completed"
    assert engine._read(operation(inputs) / "rollback-completion.json") == result
    assert state(inputs) == original
    assert (operation(inputs) / "completion.json").exists()
    assert not engine.active.exists()
    assert not (engine.root / upgrade.FENCE).exists() and not (engine.root / upgrade.DROPIN).exists()
    assert engine._read(engine.directory / "installation.json") == {**inputs[5]["source"], "phase": "services_started"}
    assert rollback._read(engine.root / "var/lib/reasoning", rollback.JOURNAL) == {**result["restoration"], "phase": "released"}
    assert (candidate(inputs) / rollback.BLOCK).exists()
    assert candidate(inputs).parent.stat().st_mode & 0o777 == 0o700
    assert inputs[-1]["source_starts"] == 1
    before = len(inputs[-1]["calls"])
    assert rollback_activate(inputs) == result
    assert all(
        args[:2] not in (["systemctl", "start"], ["systemctl", "stop"], ["systemctl", "restart"], ["systemctl", "daemon-reload"])
        for args in inputs[-1]["calls"][before:]
    )
    assert inputs[-1]["helpers"].count("rollback-restore") == inputs[-1]["helpers"].count("backup") == 1
    with pytest.raises(host.InstallError, match="preparation cannot replay"):
        rollback_prepare(inputs)
    with pytest.raises(host.InstallError, match="rollback owns"):
        activate(inputs)


@pytest.mark.parametrize("phase", ["publishing", "switching", "activating", "ready", "finalizing", "completed"])
def test_every_activation_intent_recovers_without_replacing_source_data(rollback_inputs, monkeypatch, phase):
    inputs = rollback_inputs
    ready(inputs)
    engine = inputs[0]
    original = engine._write
    interrupted = False

    def lost(path, value):
        nonlocal interrupted
        original(path, value)
        if path.name == "rollback.json" and value["phase"] == phase and not interrupted:
            interrupted = True
            raise RuntimeError("durable rollback activation interruption")

    monkeypatch.setattr(engine, "_write", lost)
    with pytest.raises(RuntimeError, match="durable rollback"):
        rollback_activate(inputs)
    assert rollback_state(inputs)["phase"] == phase
    monkeypatch.setattr(engine, "_write", original)
    assert rollback_activate(inputs)["rollback_completed"] is True
    assert inputs[-1]["source_starts"] == 1
    assert inputs[-1]["helpers"].count("rollback-restore") == 1


def test_exchange_lost_response_recognizes_both_locations_without_swapping_back(rollback_inputs, monkeypatch):
    inputs = rollback_inputs
    ready(inputs)
    engine = inputs[0]
    original = engine._exchange
    setup = (candidate(inputs) / "setup-token").read_bytes()

    def lost(source, target):
        original(source, target)
        raise RuntimeError("lost exchange acknowledgment")

    monkeypatch.setattr(engine, "_exchange", lost)
    with pytest.raises(RuntimeError, match="lost exchange"):
        rollback_activate(inputs)
    assert rollback_state(inputs)["phase"] == "publishing"
    assert (engine.root / "var/lib/reasoning/setup-token").read_bytes() == setup
    assert (candidate(inputs) / rollback.BLOCK).exists()
    monkeypatch.setattr(engine, "_exchange", lambda *args: pytest.fail("must not exchange again"))
    assert rollback_activate(inputs)["rollback_completed"] is True


@pytest.mark.parametrize("effect", ["rollback-release", "start", "fence", "completion", "pointer"])
def test_lost_effect_acknowledgments_resume_and_preserve_live_accounts(rollback_inputs, monkeypatch, effect):
    inputs = rollback_inputs
    ready(inputs)
    engine = inputs[0]
    command, remove, write = engine.command, engine._remove, engine._write
    interrupted = False

    def interrupt():
        nonlocal interrupted
        if not interrupted:
            interrupted = True
            if effect in {"start", "completion", "pointer"}:
                store = Store(engine.root / "var/lib/reasoning")
                store.bootstrap(store._setup_token, "live@example.org", "Owner", "source-fixture-password", "Live restored source")
            raise RuntimeError("lost rollback effect acknowledgment")

    def lost_command(args, **options):
        result = command(args, **options)
        if effect in args:
            interrupt()
        return result

    def lost_remove(path, *args, **options):
        remove(path, *args, **options)
        if (effect == "fence" and path == engine.root / upgrade.FENCE) or (effect == "pointer" and path == engine.active):
            interrupt()

    def lost_write(path, value):
        write(path, value)
        if effect == "completion" and path.name == "rollback-completion.json":
            interrupt()

    monkeypatch.setattr(engine, "command", lost_command)
    monkeypatch.setattr(engine, "_remove", lost_remove)
    monkeypatch.setattr(engine, "_write", lost_write)
    with pytest.raises(RuntimeError, match="lost rollback effect"):
        rollback_activate(inputs)
    assert rollback_activate(inputs)["rollback_completed"] is True
    assert inputs[-1]["source_starts"] == 1
    assert inputs[-1]["helpers"].count("rollback-restore") == 1
    if effect in {"start", "completion", "pointer"}:
        with Store(engine.root / "var/lib/reasoning").transaction() as database:
            assert database.execute("SELECT name FROM organizations").fetchone()[0] == "Live restored source"
            assert database.execute("SELECT count(*) FROM sessions").fetchone()[0] == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"activation_confirmed": False},
        {"activation_confirmed": 1},
        {"rollback_id": "0" * 64},
        {"source_release": "0" * 64},
        {"target_release": "0" * 64},
        {"confirmed_plan": "0" * 64},
    ],
)
def test_identity_and_confirmation_refusals_have_no_service_effects(rollback_inputs, changes):
    inputs = rollback_inputs
    ready(inputs)
    calls = len(inputs[-1]["calls"])
    with pytest.raises(host.InstallError):
        rollback_activate(inputs, **changes)
    assert len(inputs[-1]["calls"]) == calls
    assert rollback_state(inputs)["phase"] == "prepared"


@pytest.mark.parametrize("name", ["installation.json", "unit"])
def test_lost_binding_write_recovers_only_known_before_or_source_bytes(rollback_inputs, monkeypatch, name):
    inputs = rollback_inputs
    ready(inputs)
    engine = inputs[0]
    original = engine._transition
    interrupted = False

    def lost(path, *args, **options):
        nonlocal interrupted
        original(path, *args, **options)
        if not interrupted and path.name == ("reasoning-server.service" if name == "unit" else name):
            interrupted = True
            raise RuntimeError("lost binding switch")

    monkeypatch.setattr(engine, "_transition", lost)
    with pytest.raises(RuntimeError, match="lost binding"):
        rollback_activate(inputs)
    assert rollback_state(inputs)["phase"] == "switching"
    assert rollback_activate(inputs)["rollback_completed"] is True


def test_rollback_from_prepared_upgrade_completes_and_retries_after_pointer_removal(rollback_inputs):
    inputs = rollback_inputs
    ready(inputs, upgrade_completed=False)
    assert state(inputs)["phase"] == "prepared"
    result = rollback_activate(inputs)
    assert rollback_activate(inputs) == result
    assert inputs[-1]["starts"] == 0 and inputs[-1]["source_starts"] == 1
    assert not (operation(inputs) / "completion.json").exists()


def test_failed_health_keeps_live_source_without_completion_or_automatic_restore(rollback_inputs):
    inputs = rollback_inputs
    ready(inputs)
    inputs[-1]["health_failure"] = True
    with pytest.raises(host.InstallError, match="HTTPS"):
        rollback_activate(inputs)
    assert rollback_state(inputs)["phase"] == "activating"
    assert inputs[-1]["active"] is True
    assert not (operation(inputs) / "rollback-completion.json").exists()
    with pytest.raises(host.InstallError, match="preparation cannot replay"):
        rollback_prepare(inputs)
    inputs[-1]["health_failure"] = False
    assert rollback_activate(inputs)["rollback_completed"] is True
    assert inputs[-1]["source_starts"] == 1


def test_source_mail_and_cloud_bindings_restore_without_replacing_credentials_or_vm(rollback_inputs):
    engine = rollback_inputs[0]
    source = rollback_inputs[5]["source"]
    mail = host.canonical(MailConfig("invites@example.org", "private-rollback-activation-mail", 100).document())
    host.atomic_write(engine.root / "var/lib/reasoning/mail.json", mail)
    host.atomic_write(engine.directory / "server-mail.json", host.canonical({"installation": source, "mail": host.mail_binding(mail)}))
    request = {"format": 1, "plan_id": "a" * 64, "deployment_id": "b" * 32, "droplet_id": 456}
    host.atomic_write(engine.directory / "server-provisioning.json", host.canonical({"format": 1, "binding": source, "request": request}))
    host.atomic_write(engine.root / "etc/reasoning-provisioning.json", host.canonical({"plan_id": "a" * 64, "deployment_id": "b" * 32}))
    inputs = refresh_plan(rollback_inputs)
    ready(inputs)
    assert engine._read(engine.directory / "server-mail.json")["installation"] == inputs[5]["target"]
    result = rollback_activate(inputs)
    assert engine._read(engine.directory / "server-mail.json") == {"installation": source, "mail": host.mail_binding(mail)}
    assert engine._read(engine.directory / "server-provisioning.json") == {"format": 1, "binding": source, "request": request}
    assert (engine.root / "var/lib/reasoning/mail.json").read_bytes() == mail
    assert any(item["require_mail"] and item.get("expected_release") == source["release_id"] for item in inputs[-1]["health_calls"])
    assert "private-rollback-activation-mail" not in json.dumps(result)
    engine.inspector.droplet_id = lambda: 999
    with pytest.raises(host.InstallError, match="original DigitalOcean VM"):
        rollback_activate(inputs)


@pytest.mark.parametrize(
    "evidence", ["root-history", "upgrade-history", "application-history", "displaced", "backup", "placeholder", "active"]
)
def test_completed_replay_refuses_missing_or_changed_evidence_without_recreating_it(rollback_inputs, evidence):
    inputs = rollback_inputs
    ready(inputs)
    result = rollback_activate(inputs)
    engine = inputs[0]
    paths = {
        "root-history": operation(inputs) / "rollback-completion.json",
        "upgrade-history": operation(inputs) / "completion.json",
        "application-history": engine.root / "var/lib/reasoning" / rollback.JOURNAL,
        "displaced": operation(inputs) / "displaced-state/server.sqlite3",
        "backup": operation(inputs) / "source.rnbk",
        "placeholder": candidate(inputs) / rollback.BLOCK,
        "active": engine.active,
    }
    path = paths[evidence]
    if evidence == "active":
        host.atomic_write(path, host.canonical({"foreign": True}))
    else:
        path.unlink()
    calls = len(inputs[-1]["calls"])
    with pytest.raises((host.InstallError, OSError)):
        rollback_activate(inputs)
    assert all(
        args[:2] not in (["systemctl", "start"], ["systemctl", "stop"], ["systemctl", "restart"]) for args in inputs[-1]["calls"][calls:]
    )
    if evidence != "active":
        assert not path.exists()
    assert rollback_state(inputs)["phase"] == "completed"
    assert result["rollback_completed"] is True


def test_corrupt_displaced_target_does_not_prevent_source_activation(rollback_inputs):
    inputs = rollback_inputs
    prepare(inputs)
    activate(inputs)
    inputs[0].command(["systemctl", "stop", "reasoning-server"])
    database = inputs[0].root / "var/lib/reasoning/server.sqlite3"
    database.write_bytes(b"failed target SQLite evidence")
    rollback_prepare(inputs)
    assert rollback_activate(inputs)["displaced_database_validated"] is False
    assert (operation(inputs) / "displaced-state/server.sqlite3").read_bytes() == b"failed target SQLite evidence"
    with sqlite3.connect(inputs[0].root / "var/lib/reasoning/server.sqlite3") as source:
        assert source.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_matching_source_activation_after_real_next_schema_upgrade(rollback_inputs, monkeypatch):
    engine, source, target, source_checksum, *_ = rollback_inputs
    original_schema = maintenance.MIGRATIONS
    future = (*original_schema, ("CREATE TABLE restored_future_probe(value TEXT)", "INSERT INTO restored_future_probe VALUES('newer')"))
    directory = engine.installer._directory("var/lib/reasoning-deploy/activation-future-fixture", 0o700)
    host.extract_bundle(target, directory)
    files = {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in directory.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    }
    target = pack(
        target.parent / "activation-future.tar.gz",
        files,
        manifest_change=lambda value: value.update(
            database_schema=len(future),
            installation_features=list(host.INSTALLATION_FEATURES),
            source_revision="e" * 40,
        ),
    )
    target.chmod(0o600)
    _, target_release, checksum = host.inspect_bundle(target)
    inputs = refresh_plan((engine, source, target, source_checksum, checksum, {}, rollback_inputs[-1]))
    command = engine.command

    def matching(args, **options):
        target_helper = args[:4] == ["runuser", "-u", "reasoning", "--"] and target_release in args[4]
        target_start = args[:3] == ["systemctl", "start", "reasoning-server"] and not (operation(inputs) / "rollback.json").exists()
        if target_helper or target_start:
            with monkeypatch.context() as patch:
                for module in (maintenance, snapshots, rollback, store_module):
                    patch.setattr(module, "MIGRATIONS", future)
                return command(args, **options)
        return command(args, **options)

    engine.command = matching
    engine.inspector.health = lambda origin, **options: {"public_url": origin, "https_ready": True, "initialized": False}
    ready(inputs)
    result = rollback_activate(inputs)
    assert result["restoration"]["source_schema"] == len(original_schema)
    with sqlite3.connect(engine.root / "var/lib/reasoning/server.sqlite3") as database:
        assert database.execute("PRAGMA user_version").fetchone()[0] == len(original_schema)
        assert database.execute("SELECT 1 FROM sqlite_schema WHERE name='restored_future_probe'").fetchone() is None
    with sqlite3.connect(operation(inputs) / "displaced-state/server.sqlite3") as database:
        assert database.execute("PRAGMA user_version").fetchone()[0] == len(future)
        assert database.execute("SELECT value FROM restored_future_probe").fetchone()[0] == "newer"


@pytest.mark.parametrize("evidence", ["catalog", "key", "archive", "unit", "pointer", "placeholder", "source-state", "displaced"])
def test_prepared_activation_refuses_changed_inputs_before_publication(rollback_inputs, evidence):
    inputs = rollback_inputs
    ready(inputs)
    engine = inputs[0]
    path = {
        "catalog": engine.root / "etc/reasoning/compute-images.json",
        "key": operation(inputs) / "backup.key",
        "archive": operation(inputs) / "source.tar.gz",
        "unit": engine.root / "etc/systemd/system/reasoning-server.service",
        "pointer": engine.active,
        "placeholder": engine.root / "var/lib/reasoning" / rollback.BLOCK,
        "source-state": candidate(inputs) / "setup-token",
        "displaced": operation(inputs) / "displaced-state/server.sqlite3",
    }[evidence]
    path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises((host.InstallError, ValueError)):
        rollback_activate(inputs)
    assert rollback_state(inputs)["phase"] == "prepared"
    assert inputs[-1]["source_starts"] == 0
    assert (candidate(inputs) / rollback.JOURNAL).exists()


@pytest.mark.parametrize("properties", [{"MainPID": "0"}, {"User": "root"}, {"DropInPaths": "/foreign"}, {"NeedDaemonReload": "yes"}])
def test_source_loaded_service_refusals_never_mark_completed(rollback_inputs, monkeypatch, properties):
    inputs = rollback_inputs
    ready(inputs)
    engine = inputs[0]
    original = engine.command

    def fail_loaded(args, **options):
        result = original(args, **options)
        if args[:3] == ["systemctl", "start", "reasoning-server"]:
            inputs[-1]["loaded"].update(properties)
        return result

    monkeypatch.setattr(engine, "command", fail_loaded)
    with pytest.raises(host.InstallError, match="running"):
        rollback_activate(inputs)
    assert rollback_state(inputs)["phase"] == "activating"
    assert not (operation(inputs) / "rollback-completion.json").exists()
    inputs[-1]["loaded"].clear()
    monkeypatch.setattr(engine, "command", original)
    assert rollback_activate(inputs)["rollback_completed"] is True


@pytest.mark.parametrize(
    "result",
    [
        None,
        {"https_ready": 1, "public_url": ORIGIN, "initialized": False},
        {"https_ready": True, "public_url": ORIGIN, "initialized": 0},
        {"https_ready": True, "public_url": "https://foreign.example.org", "initialized": False},
    ],
)
def test_malformed_source_health_never_marks_completed(rollback_inputs, result):
    inputs = rollback_inputs
    ready(inputs)
    engine = inputs[0]
    health = engine.inspector.health
    engine.inspector.health = lambda *args, **options: result
    with pytest.raises(host.InstallError, match="readiness"):
        rollback_activate(inputs)
    assert rollback_state(inputs)["phase"] == "activating"
    engine.inspector.health = health
    assert rollback_activate(inputs)["rollback_completed"] is True


@pytest.mark.parametrize("command_name", ["rollback-release", "rollback-observe"])
def test_malformed_source_receipt_never_completes_and_can_resume_original_operation(rollback_inputs, monkeypatch, command_name):
    inputs = rollback_inputs
    ready(inputs)
    engine = inputs[0]
    original = engine.command

    def bad(args, **options):
        result = original(args, **options)
        if command_name in args:
            value = json.loads(result.stdout)
            value["server_started"] = True
            return subprocess.CompletedProcess(args, 0, stdout=json.dumps(value).encode())
        return result

    monkeypatch.setattr(engine, "command", bad)
    with pytest.raises(host.InstallError, match="receipt differs"):
        rollback_activate(inputs)
    assert not (operation(inputs) / "rollback-completion.json").exists()
    monkeypatch.setattr(engine, "command", original)
    assert rollback_activate(inputs)["rollback_completed"] is True


@pytest.mark.parametrize(
    "extra",
    [[], ["--confirm-workers-fenced"], ["--confirm-newer-data-loss"], ["--maximum-bytes", "1048576"], ["--target-bundle", "/untrusted"]],
)
def test_root_cli_rejects_missing_confirmation_and_replacement_inputs_before_host_access(monkeypatch, extra):
    monkeypatch.setattr(upgrade, "HostUpgrade", lambda: pytest.fail("invalid CLI must not access a host"))
    args = [
        "--operation-id",
        OPERATION,
        "--confirm-plan",
        "a" * 64,
        "--rollback-activate",
        "--rollback-id",
        ROLLBACK,
        "--confirm-source-release",
        "b" * 64,
        "--confirm-target-release",
        "c" * 64,
    ]
    if extra:
        args.append("--confirm-activation")
    with pytest.raises(SystemExit) as error:
        upgrade.main(args + extra)
    assert error.value.code == 2


def test_root_cli_dispatches_exact_confirmed_source_activation(monkeypatch, capsys):
    class Engine:
        def rollback_activate(self, **options):
            assert options == {
                "operation_id": OPERATION,
                "rollback_id": ROLLBACK,
                "confirmed_plan": "a" * 64,
                "source_release": "b" * 64,
                "target_release": "c" * 64,
                "activation_confirmed": True,
            }
            return {"rollback_completed": True}

    monkeypatch.setattr(upgrade, "HostUpgrade", Engine)
    assert (
        upgrade.main(
            [
                "--operation-id",
                OPERATION,
                "--confirm-plan",
                "a" * 64,
                "--rollback-activate",
                "--rollback-id",
                ROLLBACK,
                "--confirm-source-release",
                "b" * 64,
                "--confirm-target-release",
                "c" * 64,
                "--confirm-activation",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out) == {"rollback_completed": True}
