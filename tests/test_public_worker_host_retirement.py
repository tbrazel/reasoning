"""Managed-host retirement/SSH fixtures; no real service, container, or VM is changed."""

import copy
import fcntl
import hashlib
import json
import os
import shlex
import subprocess
from pathlib import Path

import pytest
from test_public_deployment import bundle as bundle
from test_public_deployment import pack
from test_public_deployment import payload as payload
from test_public_worker_deployment import IMAGE, ORIGIN, REGISTRATION, TOKEN, install
from test_public_worker_deployment import bootstrap as bootstrap

from reasoning_ledger.deployment import cli, host, worker_host
from reasoning_ledger.server.compute_worker import write_state
from reasoning_ledger.server.security import private_file


class Lifecycle:
    def __init__(self, installer, commands):
        self.installer, self.commands = installer, commands
        self.jobs, self.receipt, self.seals, self.enabled = [], None, 0, True
        self.failure, self.refuse_stop, self.output_change, self.retain_marker = None, False, None, True

    def __call__(self, args, *, check=True):
        response = self.commands(args, check=check)
        phase = None
        if args[:3] == ["systemctl", "disable", "--now"]:
            phase = "stop"
            assert json.loads((self.installer.state_dir / "worker-retirement.json").read_bytes())["phase"] in {"stopping", "retired"}
            if not self.refuse_stop:
                self.commands.active, self.enabled = False, False
        if args[:2] == ["systemctl", "show"] and "--property=ActiveState" in args:
            phase = "service-state"
            response = subprocess.CompletedProcess(args, 0, stdout=b"active\n" if self.commands.active else b"inactive\n")
        elif args[:2] == ["systemctl", "show"]:
            response = subprocess.CompletedProcess(
                args,
                0,
                stdout=(
                    b"FragmentPath=/etc/systemd/system/reasoning-compute.service\nDropInPaths=\n"
                    b"User=reasoning-compute\nGroup=reasoning-compute\nLoadState=loaded\nNeedDaemonReload=no\n"
                ),
            )
        if args[:2] == ["systemctl", "is-enabled"]:
            response = subprocess.CompletedProcess(args, 0 if self.enabled else 1, stdout=b"enabled\n" if self.enabled else b"disabled\n")
        if args[0] == "runuser" and "drain" in args:
            phase = "drain"
            value = {
                "worker_id": REGISTRATION["id"],
                "organization_id": REGISTRATION["organization_id"],
                "draining": True,
                "unsettled_jobs": self.jobs,
                "quiescent": not self.jobs,
                "physical_cleanup_verified": False,
                "cloud_resources_deleted": False,
            }
            if self.output_change:
                self.output_change(value)
            response = subprocess.CompletedProcess(args, 0, stdout=host.canonical(value))
        if args[0] == "runuser" and "retire" in args:
            phase = "retire"
            assert not self.commands.active and not self.enabled
            if self.receipt is None:
                self.seals += 1
                self.receipt = {
                    "worker_id": REGISTRATION["id"],
                    "organization_id": REGISTRATION["organization_id"],
                    "retired": True,
                    "retirement_id": "c" * 32,
                    "retired_at": 1,
                }
            if self.retain_marker:
                write_state(Path(args[args.index("--data-dir") + 1]) / "retirement.json", self.receipt)
            value = {**self.receipt, "container_cleanup_verified": True, "local_state_quiescent": True, "cloud_resources_deleted": False}
            if self.output_change:
                self.output_change(value)
            response = subprocess.CompletedProcess(args, 0, stdout=host.canonical(value))
        if self.failure is not None and phase == self.failure:
            self.failure = None
            raise host.InstallError("lost lifecycle response fixture")
        return response


@pytest.fixture
def managed(bundle, bootstrap):
    installer, commands = bootstrap
    state = install(bundle, bootstrap)
    executable = installer.root / "opt/reasoning-compute/releases" / state["release_id"] / "venv/bin/reasoning-compute-worker"
    executable.parent.mkdir(parents=True)
    executable.parent.chmod(0o755)
    executable.parent.parent.chmod(0o755)
    executable.write_bytes(b"nonexecuting command fixture\n")
    executable.chmod(0o755)
    lifecycle = Lifecycle(installer, commands)
    installer.command = lifecycle
    commands.calls.clear()
    return installer, lifecycle


def retire(bundle, managed, **changes):
    return managed[0].retire_worker(
        bundle,
        hashlib.sha256(bundle.read_bytes()).hexdigest(),
        changes.get("origin", ORIGIN),
        changes.get("registration", REGISTRATION),
        changes.get("catalog", [IMAGE]),
        skip_platform=True,
    )


def test_managed_retirement_drains_stops_verifies_and_seals_without_install_or_execution(bundle, managed):
    installer, lifecycle = managed
    result = retire(bundle, managed)
    assert result["phase"] == "retired" and result["service_active"] is False and result["service_disabled"] is True
    assert result["container_cleanup_verified"] and result["local_state_quiescent"]
    assert result["host_deletion_verified"] is False and result["cloud_resources_deleted"] is False
    assert result["retirement"] == lifecycle.receipt and lifecycle.seals == 1
    calls = lifecycle.commands.calls
    assert [call[call.index("--data-dir") + 2] for call in calls if call[0] == "runuser"] == ["drain", "retire"]
    assert all("configure" not in call and "run" not in call and "pull" not in call and "enable" not in call for call in calls)
    assert all(call[0] not in {"apt-get", "python3"} for call in calls)
    marker = installer.state_dir / "worker-retirement.json"
    assert marker.stat().st_mode & 0o777 == 0o600
    assert TOKEN not in json.dumps(result) and TOKEN not in marker.read_text() and TOKEN not in json.dumps(calls)
    before = len(calls)
    assert retire(bundle, managed) == result and lifecycle.seals == 1
    assert any("retire" in call for call in calls[before:]) and not any("drain" in call for call in calls[before:])
    with pytest.raises(host.InstallError, match="reactivate"):
        installer.install_worker(bundle, hashlib.sha256(bundle.read_bytes()).hexdigest(), ORIGIN, REGISTRATION, [IMAGE], skip_platform=True)


@pytest.mark.parametrize("status", ["running", "outcome_unknown"])
def test_busy_or_unknown_worker_stays_draining_without_service_shutdown(bundle, managed, status):
    installer, lifecycle = managed
    lifecycle.jobs = [{"id": "d" * 32, "status": status}]
    first = retire(bundle, managed)
    assert first["phase"] == "draining" and first["unsettled_jobs"] == lifecycle.jobs
    assert first["service_active"] and first["service_disabled"] is None and first["retirement"] is None
    assert lifecycle.commands.active and lifecycle.enabled and lifecycle.seals == 0
    assert all("disable" not in call and "retire" not in call for call in lifecycle.commands.calls)
    assert json.loads((installer.state_dir / "worker-retirement.json").read_bytes())["phase"] == "draining"
    lifecycle.jobs = []
    assert retire(bundle, managed)["phase"] == "retired"


@pytest.mark.parametrize("phase", ["drain", "stop", "service-state", "retire"])
def test_lost_lifecycle_responses_resume_same_intent_without_program_or_restart(bundle, managed, phase):
    installer, lifecycle = managed
    lifecycle.failure = phase
    with pytest.raises(host.InstallError, match="lost lifecycle"):
        retire(bundle, managed)
    expected = "draining" if phase == "drain" else "stopping"
    assert json.loads((installer.state_dir / "worker-retirement.json").read_bytes())["phase"] == expected
    assert retire(bundle, managed)["phase"] == "retired" and lifecycle.seals == 1
    assert all("run" not in call and "restart" not in call and "enable" not in call for call in lifecycle.commands.calls)


def test_failed_service_shutdown_prevents_seal_and_preserves_stop_intent(bundle, managed):
    installer, lifecycle = managed
    lifecycle.refuse_stop = True
    with pytest.raises(host.InstallError, match="stopped and disabled"):
        retire(bundle, managed)
    assert lifecycle.seals == 0 and lifecycle.commands.active
    assert json.loads((installer.state_dir / "worker-retirement.json").read_bytes())["phase"] == "stopping"
    lifecycle.refuse_stop = False
    assert retire(bundle, managed)["phase"] == "retired"


def test_missing_durable_worker_receipt_refuses_host_success_then_recovers(bundle, managed):
    installer, lifecycle = managed
    lifecycle.retain_marker = False
    with pytest.raises(FileNotFoundError):
        retire(bundle, managed)
    assert json.loads((installer.state_dir / "worker-retirement.json").read_bytes())["phase"] == "stopping"
    lifecycle.retain_marker = True
    assert retire(bundle, managed)["phase"] == "retired" and lifecycle.seals == 1


@pytest.mark.parametrize("change", ["token", "worker", "organization", "origin", "catalog", "unit", "data", "executable", "phase"])
def test_retirement_refuses_changed_installation_before_any_service_command(bundle, managed, change):
    installer, lifecycle = managed
    record, catalog, origin = copy.deepcopy(REGISTRATION), copy.deepcopy([IMAGE]), ORIGIN
    if change in {"token", "worker", "organization", "origin", "catalog"}:
        if change == "token":
            record["token"] += "a"
        elif change == "worker":
            record["id"] = "c" * 32
        elif change == "organization":
            record["organization_id"] = "c" * 32
        elif change == "origin":
            origin = "https://different.example.org"
        else:
            catalog[0]["name"] = "Different"
    elif change == "unit":
        (installer.root / "etc/systemd/system/reasoning-compute.service").write_text("unmanaged service\n")
    elif change == "data":
        (installer.root / "var/lib/reasoning-compute/enrollment.json").write_bytes(b"changed")
    elif change == "executable":
        executable = next((installer.root / "opt/reasoning-compute/releases").glob("*/venv/bin/reasoning-compute-worker"))
        executable.chmod(0o777)
    else:
        state = json.loads(installer.state_path.read_bytes())
        state["phase"] = "configured"
        host.atomic_write(installer.state_path, host.canonical(state))
    with pytest.raises(host.InstallError):
        retire(bundle, managed, registration=record, catalog=catalog, origin=origin)
    assert not lifecycle.commands.calls
    assert not (installer.state_dir / "worker-retirement.json").exists()


@pytest.mark.parametrize("phase", ["draining", "retired"])
@pytest.mark.parametrize("field", ["worker_id", "organization_id", "cloud_resources_deleted", "extra", "proof"])
def test_lifecycle_output_must_be_exact_typed_scoped_and_cannot_claim_cloud_deletion(bundle, managed, phase, field):
    _installer, lifecycle = managed
    lifecycle.jobs = [{"id": "d" * 32, "status": "running"}] if phase == "draining" else []

    def alter(value):
        if ("retired" in value) != (phase == "retired"):
            return
        if field in {"worker_id", "organization_id"}:
            value[field] = "f" * 32
        elif field == "extra":
            value["secret"] = TOKEN
        elif field == "proof":
            value["container_cleanup_verified" if phase == "retired" else "draining"] = 1
        else:
            value[field] = True

    lifecycle.output_change = alter
    with pytest.raises(host.InstallError):
        retire(bundle, managed)
    lifecycle.output_change = None
    lifecycle.jobs = []
    assert retire(bundle, managed)["phase"] == "retired"


def test_retirement_and_bootstrap_share_exclusive_host_lock(bundle, managed):
    installer, lifecycle = managed
    fd = os.open(installer.state_dir / "bootstrap.lock", os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(host.InstallError, match="another bootstrap"):
            retire(bundle, managed)
    finally:
        os.close(fd)
    assert not lifecycle.commands.calls


@pytest.mark.parametrize("change", ["json", "binding", "permissions", "receipt"])
def test_changed_root_retirement_journal_is_not_adopted(bundle, managed, change):
    installer, lifecycle = managed
    retire(bundle, managed)
    path = installer.state_dir / "worker-retirement.json"
    state = json.loads(path.read_bytes())
    if change == "json":
        path.write_bytes(b"{}")
    elif change == "permissions":
        path.chmod(0o644)
    else:
        if change == "binding":
            state["worker_id"] = "d" * 32
        else:
            state["retirement"]["organization_id"] = "d" * 32
        path.write_bytes(host.canonical(state))
    lifecycle.commands.calls.clear()
    with pytest.raises(host.InstallError):
        retire(bundle, managed)
    assert not lifecycle.commands.calls


@pytest.mark.parametrize("change", [None, "worker", "secret", "cloud", "proof", "malformed", "snapshot"])
def test_verified_ssh_retirement_uploads_bound_private_inputs_and_returns_only_validated_status(bundle, tmp_path, change):
    files = {}
    for name, body in (
        ("registration", json.dumps(REGISTRATION).encode()),
        ("catalog", json.dumps([IMAGE]).encode()),
        ("identity", b"private key fixture"),
        ("known", b"verified host key fixture"),
    ):
        files[name] = tmp_path / name
        private_file(files[name], body)
    config, images = worker_host.inputs(ORIGIN, REGISTRATION, [IMAGE])
    manifest, release, checksum = host.inspect_bundle(bundle)
    binding = worker_host.WorkerHostInstaller._binding(manifest, release, checksum, config, images)
    expected = {
        **binding,
        "phase": "retired",
        "service_active": False,
        "service_disabled": True,
        "container_cleanup_verified": True,
        "local_state_quiescent": True,
        "cloud_resources_deleted": False,
        "host_deletion_verified": False,
        "unsettled_jobs": [],
        "retirement": {
            "worker_id": config["id"],
            "organization_id": config["organization_id"],
            "retired": True,
            "retirement_id": "c" * 32,
            "retired_at": 1,
        },
    }
    calls, uploaded = [], {}
    original_bundle = bundle.read_bytes()
    response = copy.deepcopy(expected)
    if change == "worker":
        response["worker_id"] = "f" * 32
    elif change == "secret":
        response["secret"] = TOKEN
    elif change == "cloud":
        response["cloud_resources_deleted"] = True
    elif change == "proof":
        response["service_disabled"] = 1

    def command(args):
        calls.append(args)
        if change == "snapshot" and len(calls) == 1:
            bundle.write_bytes(b"changed original archive after inspection")
        if args[0] == "scp":
            uploaded[Path(args[-2]).name] = Path(args[-2]).read_bytes()
        return subprocess.CompletedProcess(args, 0, stdout=b"malformed" if change == "malformed" else host.canonical(response))

    def operation():
        return cli.install_worker_remote(
            bundle,
            ORIGIN,
            files["registration"],
            files["catalog"],
            "203.0.113.20",
            files["identity"],
            files["known"],
            command=command,
            confirmed_release=release,
            confirmed_worker=config["id"],
            operation="retire",
        )

    if change not in {None, "snapshot"}:
        with pytest.raises(host.InstallError):
            operation()
        return
    result = operation()
    assert result == expected and TOKEN not in json.dumps(calls) and TOKEN not in json.dumps(result)
    assert uploaded["enrollment.json"] == host.canonical(config)
    assert uploaded["images.json"] == host.canonical(images)
    assert uploaded["release.tar.gz"] == original_bundle
    assert all("StrictHostKeyChecking=yes" in args for args in calls)
    args = shlex.split(calls[-1][-1])
    assert args[-2:] == ["--operation", "retire"]


@pytest.mark.parametrize("confirmation", ["release", "worker"])
def test_retire_cli_checks_exact_confirmation_before_ssh_or_private_catalog_access(bundle, tmp_path, monkeypatch, capsys, confirmation):
    record = tmp_path / "registration.json"
    private_file(record, json.dumps(REGISTRATION).encode())
    _, release, _ = host.inspect_bundle(bundle)
    monkeypatch.setattr(cli, "install_worker_remote", lambda *args, **kwargs: pytest.fail("SSH must not run"))
    with pytest.raises(SystemExit) as error:
        cli.main(
            [
                "retire-worker",
                "--bundle",
                str(bundle),
                "--server-url",
                ORIGIN,
                "--registration",
                str(record),
                "--catalog",
                "missing",
                "--ssh-host",
                "203.0.113.20",
                "--identity",
                "missing",
                "--known-hosts",
                "missing",
                "--confirm-release",
                "wrong" if confirmation == "release" else release,
                "--confirm-worker",
                "wrong" if confirmation == "worker" else REGISTRATION["id"],
            ]
        )
    assert error.value.code == 2 and "confirm-" in capsys.readouterr().err


def test_retirement_requires_an_installed_worker_not_a_fresh_host(bundle, bootstrap):
    installer, commands = bootstrap
    with pytest.raises(host.InstallError, match="fully installed"):
        installer.retire_worker(bundle, hashlib.sha256(bundle.read_bytes()).hexdigest(), ORIGIN, REGISTRATION, [IMAGE], skip_platform=True)
    assert not commands.calls


def test_retirement_refuses_a_different_release_without_service_commands(bundle, payload, managed, tmp_path):
    _installer, lifecycle = managed
    changed = dict(payload, NOTICE=b"different release fixture")
    other = pack(tmp_path / "other-release.tar.gz", changed)
    with pytest.raises(host.InstallError, match="exact release"):
        retire(other, managed)
    assert not lifecycle.commands.calls


@pytest.mark.parametrize("change", ["fragment", "dropin", "user", "group", "reload", "load", "duplicate", "extra", "oversize"])
def test_loaded_service_mismatch_refuses_draining_and_shutdown(bundle, managed, change):
    installer, lifecycle = managed
    original = installer.command

    def changed(args, **kwargs):
        result = original(args, **kwargs)
        if args[:2] == ["systemctl", "show"] and "--no-pager" in args:
            if change == "oversize":
                result.stdout = b"x" * 4097
            elif change in {"duplicate", "extra"}:
                result.stdout += b"User=reasoning-compute\n" if change == "duplicate" else b"unexpected=value\n"
            else:
                field, value = {
                    "fragment": ("FragmentPath", "/different"),
                    "dropin": ("DropInPaths", "/override.conf"),
                    "user": ("User", "other"),
                    "group": ("Group", "other"),
                    "reload": ("NeedDaemonReload", "yes"),
                    "load": ("LoadState", "not-found"),
                }[change]
                rows = result.stdout.decode().splitlines()
                result.stdout = ("\n".join(field + "=" + value if row.startswith(field + "=") else row for row in rows) + "\n").encode()
        return result

    installer.command = changed
    with pytest.raises(host.InstallError, match="loaded worker service"):
        retire(bundle, managed)
    assert all("disable" not in call and "drain" not in call and "retire" not in call for call in lifecycle.commands.calls)
    assert not (installer.state_dir / "worker-retirement.json").exists()


def test_service_reactivation_after_worker_seal_prevents_host_success(bundle, managed):
    installer, lifecycle = managed
    original = installer.command

    def reactivated(args, **kwargs):
        result = original(args, **kwargs)
        if args[0] == "runuser" and "retire" in args:
            lifecycle.commands.active = True
        return result

    installer.command = reactivated
    with pytest.raises(host.InstallError, match="stopped and disabled"):
        retire(bundle, managed)
    assert json.loads((installer.state_dir / "worker-retirement.json").read_bytes())["phase"] == "stopping"
    installer.command = original
    assert retire(bundle, managed)["phase"] == "retired" and lifecycle.seals == 1


@pytest.mark.parametrize("activity", [b"missing\n", b"", b"inactive\nextra\n"])
def test_unknown_service_activity_does_not_become_false_quiescence(bundle, managed, activity):
    installer, lifecycle = managed
    original = installer.command
    lifecycle.jobs = [{"id": "d" * 32, "status": "running"}]

    def changed(args, **kwargs):
        result = original(args, **kwargs)
        if "--property=ActiveState" in args:
            result.stdout = activity
        return result

    installer.command = changed
    with pytest.raises(host.InstallError, match="activity"):
        retire(bundle, managed)
    assert lifecycle.commands.active and lifecycle.seals == 0


def test_retire_cli_dispatches_exact_operation_without_using_provider_credentials(bundle, tmp_path, monkeypatch, capsys):
    record = tmp_path / "registration.json"
    private_file(record, json.dumps(REGISTRATION).encode())
    _, release, _ = host.inspect_bundle(bundle)
    calls = []

    def remote(*args, **kwargs):
        calls.append((args, kwargs))
        return {"phase": "draining", "cloud_resources_deleted": False}

    monkeypatch.setattr(cli, "install_worker_remote", remote)
    assert (
        cli.main(
            [
                "retire-worker",
                "--bundle",
                str(bundle),
                "--server-url",
                ORIGIN,
                "--registration",
                str(record),
                "--catalog",
                "fixture-catalog",
                "--ssh-host",
                "203.0.113.20",
                "--identity",
                "fixture-identity",
                "--known-hosts",
                "fixture-known",
                "--confirm-release",
                release,
                "--confirm-worker",
                REGISTRATION["id"],
            ]
        )
        == 0
    )
    assert calls[0][1] == {"confirmed_release": release, "confirmed_worker": REGISTRATION["id"], "operation": "retire"}
    assert json.loads(capsys.readouterr().out)["cloud_resources_deleted"] is False


def test_standalone_worker_bootstrap_dispatches_retirement_with_exact_private_hashes(bundle, tmp_path, monkeypatch, capsys):
    record, catalog = tmp_path / "registration", tmp_path / "catalog"
    private_file(record, json.dumps(REGISTRATION).encode())
    private_file(catalog, json.dumps([IMAGE]).encode())
    calls = []

    class Installer:
        def retire_worker(self, *args):
            calls.append(args)
            return {"phase": "retired", "cloud_resources_deleted": False}

        def install_worker(self, *args):
            pytest.fail("retirement must never dispatch installation")

    monkeypatch.setattr(worker_host, "WorkerHostInstaller", Installer)
    assert (
        worker_host.main(
            [
                "--bundle",
                str(bundle),
                "--expected-sha256",
                hashlib.sha256(bundle.read_bytes()).hexdigest(),
                "--server-url",
                ORIGIN,
                "--registration",
                str(record),
                "--catalog",
                str(catalog),
                "--expected-enrollment-sha256",
                hashlib.sha256(record.read_bytes()).hexdigest(),
                "--expected-catalog-sha256",
                hashlib.sha256(catalog.read_bytes()).hexdigest(),
                "--operation",
                "retire",
            ]
        )
        == 0
    )
    assert calls[0][-2:] == (REGISTRATION, [IMAGE])
    assert TOKEN not in capsys.readouterr().out
