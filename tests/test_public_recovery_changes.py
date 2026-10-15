"""Verified-SSH recovery with real SQL/crypto; root, packages, systemd and TLS fixtures."""

import copy
import fcntl
import hashlib
import io
import json
import shlex
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest
from test_public_host_recovery import ORIGIN, RECOVERY, state
from test_public_host_recovery_activation import activation_inputs as activation_inputs
from test_public_host_recovery_activation import bootstrap as bootstrap
from test_public_host_recovery_activation import inputs as inputs
from test_public_host_recovery_activation import installation as installation
from test_public_host_recovery_activation import payload as payload
from test_public_server_operations import ssh_inputs

from reasoning_ledger.deployment import cli, host, recovery_changes, server_changes
from reasoning_ledger.deployment.private import read_document, write_document


@pytest.fixture
def operator(activation_inputs, tmp_path, monkeypatch):
    inputs = activation_inputs
    engine = inputs[0]
    key, known = ssh_inputs(tmp_path)
    plan = recovery_changes.plan_recovery(
        inputs[1], inputs[3], inputs[5], ORIGIN, confirmed_release=inputs[8]["installation"]["release_id"], expected_backup=inputs[4]
    )
    for name in ("prepare", "activate"):
        method = getattr(engine, name)

        def wrapper(*args, _method=method, **options):
            return _method(*args, **options, skip_platform=True)

        monkeypatch.setattr(engine, name, wrapper)
    requests, packets = [], []

    def command(args, chunks):
        assert args[:2] == ["ssh", "-T"]
        assert shlex.split(args[-1]) == ["python3", "-I", "-S", "-B", "-c", server_changes.LAUNCHER]
        assert "StrictHostKeyChecking=yes" in args and "BatchMode=yes" in args
        assert args[args.index("-F") + 1] == "/dev/null"
        packet = b"".join(chunks)
        packets.append(packet)
        stream = io.BytesIO(packet)
        script = stream.read(int(stream.readline()))

        def endpoint(request, _stdin):
            requests.append(request)
            receipt = recovery_changes.execute_remote(request, stream, engine=engine)
            receipt["recovery_directory"] = receipt["recovery_directory"].removeprefix(str(engine.root))
            return receipt

        script = script.replace(b"if os.geteuid() != 0:", b"if False:", 1)
        script = script.replace(b"package.recovery_changes.execute_remote(request, sys.stdin.buffer)", b"endpoint(request, None)", 1)
        with redirect_stdout(io.StringIO()) as output:
            exec(compile(script, "<recovery-transport-fixture>", "exec"), {"endpoint": endpoint})
        return subprocess.CompletedProcess(args, 0, stdout=output.getvalue().encode())

    def change(mode, **changes):
        options = {
            "recovery_id": RECOVERY,
            "confirmed_recovery": plan["input_sha256"],
            "command": command,
            "execution_fenced": mode == "recovery-prepare",
            "activation_confirmed": mode == "recovery-activate",
        }
        if mode == "recovery-prepare":
            options.update(bundle=inputs[1], backup=inputs[3], key_file=inputs[5])
        options.update(changes)
        return recovery_changes.recover_remote(mode, plan, "replacement.example.org", key, known, tmp_path / "operator", **options)

    change.remote_command = command
    return change, inputs, plan, requests, packets, key, known


def test_operator_prepares_and_activates_exact_recovery_and_retains_immutable_receipts(operator, tmp_path):
    change, inputs, plan, requests, packets, *_ = operator
    for mode in ("recovery-prepare", "recovery-activate"):
        receipt = change(mode)
        assert receipt["operation"] == mode and receipt["input_sha256"] == plan["input_sha256"]
        assert read_document(tmp_path / "operator" / (mode + ".json")) == receipt
        assert change(mode) == receipt
    assert state(inputs)["phase"] == "completed" and not inputs[0].active.exists()
    assert inputs[-1]["helpers"].count("recovery-prepare") == inputs[-1]["starts"] == 1
    assert len(requests) == len(packets) == 4
    assert all(len(value["upload_sizes"]) == 3 for value in requests[:2])
    assert all(value["upload_sizes"] == [] for value in requests[2:])
    assert "init" not in inputs[-1]["helpers"] and "migrate-offline" not in inputs[-1]["helpers"]
    assert not list((inputs[0].directory / "staging").glob("server-recovery-*"))


@pytest.mark.parametrize("mode", sorted(recovery_changes.MODES))
def test_lost_remote_reply_recovers_original_root_operation_without_repeated_restore_or_start(operator, mode):
    change, inputs, _, requests, *_ = operator
    if mode == "recovery-activate":
        change("recovery-prepare")
    callback = change.remote_command

    def lost(args, chunks):
        callback(args, chunks)
        return subprocess.CompletedProcess(args, 255, stdout=b"private incomplete reply")

    with pytest.raises(host.InstallError, match="changes may have occurred"):
        change(mode, command=lost)
    assert change(mode)["operation"] == mode
    if mode == "recovery-prepare":
        change("recovery-activate")
    assert inputs[-1]["helpers"].count("recovery-prepare") == inputs[-1]["starts"] == 1
    assert {value["recovery_id"] for value in requests} == {RECOVERY}


def test_activation_requires_original_operator_journal_not_only_a_plan(operator):
    change, _, _, requests, *_ = operator
    with pytest.raises(host.InstallError, match="original operator recovery directory"):
        change("recovery-activate")
    assert not requests


@pytest.mark.parametrize(
    "changes",
    [
        {"execution_fenced": False},
        {"execution_fenced": 1},
        {"activation_confirmed": True},
        {"recovery_id": "invalid"},
        {"confirmed_recovery": "0" * 64},
        {"backup": None},
        {"key_file": None},
        {"bundle": None},
    ],
)
def test_bad_preparation_acknowledgments_request_no_network_or_root_changes(operator, changes):
    change, inputs, _, requests, *_ = operator
    with pytest.raises(host.InstallError):
        change("recovery-prepare", **changes)
    assert not requests and not inputs[0].active.exists()


@pytest.mark.parametrize(
    "mutation",
    [
        "format",
        "dirty",
        "schema",
        "release",
        "revision",
        "origin",
        "backup",
        "key",
        "maximum",
        "mail",
        "provisioning",
        "extra",
        "host_changed",
        "archive_authenticated",
    ],
)
def test_rehashed_malformed_saved_plan_is_not_host_mutation_authority(operator, mutation):
    change, inputs, plan, requests, *_ = operator
    broken = copy.deepcopy(plan)
    if mutation == "format":
        broken["format"] = True
    elif mutation == "dirty":
        broken["installation"]["dirty"] = True
    elif mutation == "schema":
        broken["database_schema"] = True
    elif mutation == "release":
        broken["installation"]["release_id"] = "not a digest"
    elif mutation == "revision":
        broken["installation"]["source_revision"] = "main"
    elif mutation == "origin":
        broken["installation"]["public_url"] = "http://replacement.example.org"
    elif mutation == "backup":
        broken["backup_sha256"] = "not a digest"
    elif mutation == "key":
        broken["backup_key_sha256"] = True
    elif mutation == "maximum":
        broken["maximum_bytes"] = True
    elif mutation == "mail":
        broken["mail"] = {"input_sha256": "a" * 64, "credential": "private"}
    elif mutation == "provisioning":
        broken["provisioning"] = {"format": 1, "plan_id": "a" * 64, "deployment_id": "b" * 32, "droplet_id": True}
    elif mutation == "extra":
        broken["unbound_credential"] = "private"
    else:
        broken[mutation] = True
    broken["input_sha256"] = hashlib.sha256(host.canonical({name: broken[name] for name in recovery_changes.FIELDS})).hexdigest()
    with pytest.raises(host.InstallError):
        recovery_changes.validate_plan(broken, broken["input_sha256"])
    assert not requests and not inputs[0].active.exists() and change


@pytest.mark.parametrize("mutation", ["key", "backup", "identity", "known", "hostname", "operation", "journal"])
def test_drift_in_original_private_inputs_or_ssh_binding_refuses_before_retry(operator, tmp_path, mutation):
    change, inputs, _, requests, _, key, known = operator
    change("recovery-prepare")
    calls = len(requests)
    options = {}
    if mutation in {"key", "backup"}:
        inputs[5 if mutation == "key" else 3].write_bytes(b"changed private input")
    elif mutation == "identity":
        key.write_bytes(b"changed SSH identity")
    elif mutation == "known":
        known.write_bytes(b"changed verified host")
    elif mutation == "operation":
        options["recovery_id"] = "a" * 64
    else:
        path = tmp_path / "operator/operation.json"
        value = read_document(path)
        if mutation == "hostname":
            value["transport"]["hostname"] = "other.example.org"
        else:
            value["identity"]["maximum_bytes"] += 1
        write_document(path, value)
    with pytest.raises(host.InstallError):
        change("recovery-prepare", **options)
    assert len(requests) == calls


def test_original_paths_changing_after_network_start_do_not_change_bound_snapshots(operator):
    change, inputs, _, _, _, key, known = operator
    key_body, known_body = key.read_bytes(), known.read_bytes()
    callback = change.remote_command

    def replacing(args, chunks):
        key.write_bytes(b"changed original SSH key")
        known.write_bytes(b"changed original known host")
        inputs[3].write_bytes(b"changed original backup")
        inputs[5].write_bytes(b"changed original encryption key")
        inputs[1].write_bytes(b"changed original release")
        assert Path(args[args.index("-i") + 1]).read_bytes() == key_body
        quoted = next(value for value in args if value.startswith('UserKnownHostsFile="'))
        assert Path(quoted.removeprefix('UserKnownHostsFile="').removesuffix('"')).read_bytes() == known_body
        return callback(args, chunks)

    assert change("recovery-prepare", command=replacing)["phase"] == "prepared"
    assert inputs[-1]["helpers"].count("recovery-prepare") == 1


@pytest.mark.parametrize("mutation", ["truncated", "checksum", "extra_bytes", "size", "key_size", "schema", "acknowledgment"])
def test_bad_remote_upload_never_calls_the_mutating_backend(operator, monkeypatch, mutation):
    change, inputs, _, requests, *_ = operator
    captured = []

    def capture(args, chunks):
        captured.append(b"".join(chunks))
        return subprocess.CompletedProcess(args, 255, stdout=b"")

    with pytest.raises(host.InstallError):
        change("recovery-prepare", command=capture)
    stream = io.BytesIO(captured[0])
    script = stream.read(int(stream.readline()))
    script = script.replace(b"if os.geteuid() != 0:", b"if False:", 1)
    script = script.replace(b"package.recovery_changes.execute_remote(request, sys.stdin.buffer)", b"capture_request(request)", 1)
    with redirect_stdout(io.StringIO()):
        exec(compile(script, "<upload-fixture>", "exec"), {"capture_request": lambda value: requests.append(value) or {}})
    request, body = copy.deepcopy(requests[-1]), stream.read()
    if mutation == "truncated":
        body = body[:-5]
    elif mutation == "checksum":
        body = b"bad release" + body[11:]
    elif mutation == "extra_bytes":
        body += b"replacement input"
    elif mutation == "size":
        request["upload_sizes"][0] = True
    elif mutation == "key_size":
        request["upload_sizes"][2] = 31
    elif mutation == "schema":
        request["identity"]["database_schema"] += 1
        request["input_sha256"] = hashlib.sha256(host.canonical(request["identity"])).hexdigest()
    else:
        request["execution_fenced"] = 1
    calls = []
    monkeypatch.setattr(inputs[0], "prepare", lambda *args, **kwargs: calls.append(args))
    with pytest.raises(host.InstallError):
        recovery_changes.execute_remote(request, io.BytesIO(body), engine=inputs[0])
    assert not calls and not inputs[0].active.exists()
    assert not list((inputs[0].directory / "staging").glob("server-recovery-*"))


@pytest.mark.parametrize("mutation", ["extra", "secret", "nonzero", "string", "oversized", "duplicate", "id", "nested"])
def test_remote_reply_requires_exact_bounded_secret_free_evidence(operator, tmp_path, mutation):
    change, _, _, _, *_ = operator
    callback = change.remote_command

    def corrupt(args, chunks):
        result = callback(args, chunks)
        value = json.loads(result.stdout)
        if mutation == "secret":
            return subprocess.CompletedProcess(args, 0, stdout=b"private secret")
        if mutation == "nonzero":
            return subprocess.CompletedProcess(args, 2, stdout=result.stdout)
        if mutation == "string":
            return subprocess.CompletedProcess(args, 0, stdout="not byte output")
        if mutation == "oversized":
            return subprocess.CompletedProcess(args, 0, stdout=b"x" * (server_changes.MAX_RECEIPT + 1))
        if mutation == "duplicate":
            return subprocess.CompletedProcess(args, 0, stdout=result.stdout[:-1] + b',"format":1}')
        if mutation == "extra":
            value["credential"] = "private secret"
        elif mutation == "id":
            value["recovery_id"] = "a" * 64
        else:
            value["restoration"]["maximum_bytes"] += 1
        return subprocess.CompletedProcess(args, 0, stdout=host.canonical(value))

    with pytest.raises(host.InstallError, match="changes may have occurred") as failure:
        change("recovery-prepare", command=corrupt)
    assert "private secret" not in str(failure.value)
    assert not (tmp_path / "operator/recovery-prepare.json").exists()
    assert change("recovery-prepare")["phase"] == "prepared"


@pytest.mark.parametrize(
    "change",
    [
        {"bundle": Path("replacement")},
        {"backup": Path("replacement")},
        {"key_file": Path("replacement")},
        {"mail_file": Path("replacement")},
        {"activation_confirmed": 1},
        {"execution_fenced": True},
    ],
)
def test_activation_cannot_accept_new_inputs_or_wrong_acknowledgments(operator, change):
    run, _, _, requests, *_ = operator
    run("recovery-prepare")
    count = len(requests)
    with pytest.raises(host.InstallError):
        run("recovery-activate", **change)
    assert len(requests) == count


def test_read_only_cli_plan_is_private_immutable_and_not_archive_authentication(operator, tmp_path, capsys):
    _, inputs, plan, requests, *_ = operator
    output = tmp_path / "saved-plan.json"
    args = [
        "recovery-plan",
        "--bundle",
        str(inputs[1]),
        "--backup",
        str(inputs[3]),
        "--key-file",
        str(inputs[5]),
        "--confirm-release",
        plan["installation"]["release_id"],
        "--expected-backup-sha256",
        inputs[4],
        "--public-url",
        ORIGIN,
        "--output",
        str(output),
    ]
    assert cli.main(args) == 0
    assert read_document(output) == json.loads(capsys.readouterr().out) == plan
    assert output.stat().st_mode & 0o777 == 0o600 and not requests and not inputs[0].active.exists()
    with pytest.raises(SystemExit) as result:
        cli.main(args)
    assert result.value.code == 2 and read_document(output) == plan


@pytest.mark.parametrize("mode", sorted(recovery_changes.MODES))
def test_cli_routes_exact_recovery_mode_specific_inputs(operator, tmp_path, monkeypatch, capsys, mode):
    _, inputs, plan, _, _, key, known = operator
    path = tmp_path / "plan.json"
    write_document(path, plan, new=True)
    calls = []

    def recover(*args, **kwargs):
        calls.append((args, kwargs))
        return {"operation": mode}

    monkeypatch.setattr(recovery_changes, "recover_remote", recover)
    args = [
        mode,
        "--plan",
        str(path),
        "--confirm-recovery",
        plan["input_sha256"],
        "--recovery-id",
        RECOVERY,
        "--state-dir",
        str(tmp_path / "operator"),
        "--ssh-host",
        "replacement.example.org",
        "--identity",
        str(key),
        "--known-hosts",
        str(known),
    ]
    if mode == "recovery-prepare":
        args += ["--bundle", str(inputs[1]), "--backup", str(inputs[3]), "--key-file", str(inputs[5]), "--confirm-execution-fenced"]
    else:
        args += ["--confirm-activation"]
    assert cli.main(args) == 0 and json.loads(capsys.readouterr().out) == {"operation": mode}
    assert calls[0][0][:2] == (mode, plan)
    assert calls[0][1]["confirmed_recovery"] == plan["input_sha256"] and calls[0][1]["recovery_id"] == RECOVERY
    assert calls[0][1]["execution_fenced"] is (mode == "recovery-prepare")
    assert calls[0][1]["activation_confirmed"] is (mode == "recovery-activate")


def test_isolated_remote_tooling_requires_no_application_or_uploaded_python(operator):
    request = {"mode": "recovery-activate"}
    script = recovery_changes.remote_script(request)
    script = script.replace(b"if os.geteuid() != 0:", b"if False:", 1)
    script = script.replace(
        b"receipt = package.recovery_changes.execute_remote(request, sys.stdin.buffer)",
        b"receipt = {'modules': sorted(name for name in sys.modules if name.startswith('reasoning_change.')), "
        b"'remaining': sys.stdin.buffer.read().decode()}",
    )
    blocks = (
        [(str(len(script)) + "\n").encode()]
        + [script[offset : offset + 65536] for offset in range(0, len(script), 65536)]
        + [b"input-boundary"]
    )
    result = server_changes._run([sys.executable, "-I", "-S", "-B", "-c", server_changes.LAUNCHER], iter(blocks), timeout=5)
    value = json.loads(result.stdout)
    assert value["remaining"] == "input-boundary"
    assert value["modules"] == [
        "reasoning_change.host",
        "reasoning_change.recovery_changes",
        "reasoning_change.server_changes",
        "reasoning_change.server_operations",
        "reasoning_change.server_recovery",
        "reasoning_change.server_upgrade",
    ]


@pytest.mark.parametrize("mutation", ["public", "link", "symlink", "short_key", "backup_digest"])
def test_plan_refuses_insecure_or_wrong_original_inputs_before_any_host_access(operator, tmp_path, mutation):
    _, inputs, _, requests, *_ = operator
    key, backup = inputs[5], inputs[3]
    expected = inputs[4]
    if mutation == "public":
        key.chmod(0o644)
    elif mutation == "link":
        import os

        os.link(key, tmp_path / "key-alias")
    elif mutation == "symlink":
        original = key.with_name("original-key")
        key.rename(original)
        key.symlink_to(original)
    elif mutation == "short_key":
        key.write_bytes(b"short")
    else:
        expected = "0" * 64
    with pytest.raises((host.InstallError, OSError)):
        recovery_changes.plan_recovery(
            inputs[1], backup, key, ORIGIN, confirmed_release=inputs[8]["installation"]["release_id"], expected_backup=expected
        )
    assert not requests and not inputs[0].active.exists()


@pytest.mark.parametrize("mutation", ["dirty", "preparation_only"])
def test_planning_refuses_dirty_or_preparation_only_release(operator, monkeypatch, mutation):
    _, inputs, _, requests, *_ = operator
    original = host.inspect_bundle

    def inspect(*args, **kwargs):
        manifest, _, checksum = original(*args, **kwargs)
        if mutation == "dirty":
            manifest["dirty"] = True
        else:
            manifest["installation_features"].remove("server-recovery-activation-v1")
        return manifest, hashlib.sha256(host.canonical(manifest)).hexdigest(), checksum

    monkeypatch.setattr(host, "inspect_bundle", inspect)
    with pytest.raises(host.InstallError):
        recovery_changes.plan_recovery(
            inputs[1], inputs[3], inputs[5], ORIGIN, confirmed_release=inputs[8]["installation"]["release_id"], expected_backup=inputs[4]
        )
    assert not requests


def test_local_lifetime_journal_lease_prevents_concurrent_operator_requests(operator, tmp_path):
    change, _, _, requests, *_ = operator
    change("recovery-prepare")
    count = len(requests)
    with (tmp_path / "operator/provision.lock").open("rb") as lease:
        fcntl.flock(lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(host.InstallError, match="another process"):
            change("recovery-activate")
    assert len(requests) == count


@pytest.mark.parametrize("mutation", ["extra", "schema", "count", "phase", "hash", "mail", "migration", "physical_fencing"])
def test_nested_restoration_receipt_cannot_add_secrets_or_weaken_original_evidence(operator, mutation):
    change, _, _, requests, *_ = operator
    receipt = change("recovery-prepare")
    modified = copy.deepcopy(receipt)
    nested = modified["restoration"]
    if mutation == "extra":
        nested["credential"] = "private secret"
    elif mutation == "schema":
        nested["maintenance"]["target_schema"] = True
    elif mutation == "count":
        nested["recovery"]["sessions_revoked"] = True
    elif mutation == "phase":
        nested["maintenance"]["phase"] = "completed"
    elif mutation == "hash":
        nested["maintenance"]["target_state_sha256"] = "not a digest"
    elif mutation == "mail":
        nested["mail_sha256"] = "a" * 64
    elif mutation == "migration":
        nested["sql_migration_performed"] = True
    else:
        modified["physical_source_fencing_verified"] = True
    with pytest.raises(host.InstallError, match="changes may have occurred"):
        recovery_changes.validate_receipt(modified, requests[-1])


def test_mail_and_owned_vm_request_are_bound_without_secret_output_or_activation_upload(operator, tmp_path, monkeypatch):
    from reasoning_ledger.server.mail import MailConfig

    change, inputs, plan, requests, packets, key, known = operator
    engine = inputs[0]
    mail = tmp_path / "mail.json"
    secret = "private-fixture-mail-key"
    write_document(mail, MailConfig("operator@example.org", secret, 10).document(), new=True)
    request = {"format": 1, "plan_id": "a" * 64, "deployment_id": "b" * 32, "droplet_id": 123}
    provisioning = tmp_path / "vm.json"
    write_document(provisioning, request, new=True)
    engine.installer._directory("etc")
    host.atomic_write(
        engine.root / "etc/reasoning-provisioning.json", host.canonical({name: request[name] for name in ("plan_id", "deployment_id")})
    )
    live = [123]
    monkeypatch.setattr(host.HostInstaller, "_droplet_id", staticmethod(lambda: live[0]))
    engine.inspector.droplet_id = lambda: live[0]
    owned = recovery_changes.plan_recovery(
        inputs[1],
        inputs[3],
        inputs[5],
        ORIGIN,
        confirmed_release=plan["installation"]["release_id"],
        expected_backup=inputs[4],
        mail_file=mail,
        provisioning_file=provisioning,
    )
    options = {"recovery_id": RECOVERY, "confirmed_recovery": owned["input_sha256"], "command": change.remote_command}
    args = (owned, "replacement.example.org", key, known, tmp_path / "owned-operator")
    receipt = recovery_changes.recover_remote(
        "recovery-prepare", *args, **options, bundle=inputs[1], backup=inputs[3], key_file=inputs[5], mail_file=mail, execution_fenced=True
    )
    assert receipt["restoration"]["mail_sha256"] == owned["mail"]["input_sha256"]
    assert requests[-1]["identity"]["provisioning"] == request and len(requests[-1]["upload_sizes"]) == 4
    assert secret not in host.canonical(owned).decode() and secret not in host.canonical(receipt).decode()
    packet = io.BytesIO(packets[-1])
    script = packet.read(int(packet.readline()))
    assert secret.encode() not in script
    assert inputs[5].read_bytes() not in script
    live[0] = 124
    with pytest.raises((host.InstallError, SystemExit)):
        recovery_changes.recover_remote("recovery-activate", *args, **options, activation_confirmed=True)
    assert state(inputs)["phase"] == "prepared"
    live[0] = 123
    result = recovery_changes.recover_remote("recovery-activate", *args, **options, activation_confirmed=True)
    assert result["recovery_completed"] and requests[-1]["upload_sizes"] == []
    assert (engine.root / "var/lib/reasoning/mail.json").read_bytes() == mail.read_bytes()


@pytest.mark.parametrize("mode", sorted(recovery_changes.MODES))
def test_cli_requires_separate_acknowledgments_before_network_access(operator, tmp_path, monkeypatch, mode):
    _, inputs, plan, _, _, key, known = operator
    path = tmp_path / "plan.json"
    write_document(path, plan, new=True)
    monkeypatch.setattr(recovery_changes, "recover_remote", lambda *args, **kwargs: pytest.fail("invalid CLI cannot request networking"))
    args = [
        mode,
        "--plan",
        str(path),
        "--confirm-recovery",
        plan["input_sha256"],
        "--recovery-id",
        RECOVERY,
        "--state-dir",
        str(tmp_path / "operator"),
        "--ssh-host",
        "replacement.example.org",
        "--identity",
        str(key),
        "--known-hosts",
        str(known),
    ]
    if mode == "recovery-prepare":
        args += ["--bundle", str(inputs[1]), "--backup", str(inputs[3]), "--key-file", str(inputs[5])]
    with pytest.raises(SystemExit) as result:
        cli.main(args)
    assert result.value.code == 2
