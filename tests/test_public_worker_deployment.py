"""Separate-host bootstrap/SSH/service fixtures, not real Docker, VM, or TLS execution."""

import copy
import fcntl
import hashlib
import json
import os
import shlex
import subprocess
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from test_public_deployment import bundle as bundle
from test_public_deployment import pack
from test_public_deployment import payload as payload

from reasoning_ledger.deployment import cli, host, worker_host
from reasoning_ledger.deployment.worker_host import WorkerHostInstaller, inputs, private_source, worker_unit
from reasoning_ledger.server import compute_worker
from reasoning_ledger.server.compute_catalog import ComputeImage, canonical, resources
from reasoning_ledger.server.compute_worker import WorkerClient, WorkerFault, verify_enrollment
from reasoning_ledger.server.security import private_file

ORIGIN = "https://chat.example.org"
TOKEN = "fixture-token-" + "x" * 40
REGISTRATION = {
    "id": "a" * 32,
    "organization_id": "b" * 32,
    "token": TOKEN,
    "image_ids": ["fixture"],
    "resources": {
        "cpu": 1,
        "memory_bytes": 1073741824,
        "disk_bytes": 268435456,
        "pids": 128,
        "wall_time_seconds": 120,
        "artifact_bytes": 1048576,
    },
    "name": "Fixture",
    "created_at": 1,
    "last_seen_at": None,
    "draining": 0,
    "revoked_at": None,
}
IMAGE = {
    "id": "fixture",
    "name": "Not an executing image",
    "reference": "registry.example.org/research/cpu@sha256:" + "c" * 64,
    "languages": ["sage", "python"],
    "source_revision": "d" * 40,
    "capabilities_digest": "sha256:" + "e" * 64,
    "sbom_digest": "sha256:" + "f" * 64,
    "capabilities_path": "/etc/reasoning/capabilities.json",
    "sbom_path": "/usr/share/reasoning/sbom.json",
    "maximum_resources": REGISTRATION["resources"],
}


class Commands:
    def __init__(self):
        self.calls, self.account, self.active, self.fail, self.containers, self.ce, self.verification = (
            [],
            False,
            False,
            None,
            b"",
            False,
            True,
        )

    def __call__(self, args, *, check=True):
        self.calls.append(args)
        if self.fail and self.fail(args):
            raise host.InstallError("fixture command interruption")
        if args[0] == "useradd":
            self.account = True
        if args[:2] == ["systemctl", "is-active"]:
            return subprocess.CompletedProcess(args, 0 if self.active else 3, stdout=b"")
        if args[:2] == ["systemctl", "show"]:
            return subprocess.CompletedProcess(
                args,
                0,
                stdout=(
                    b"FragmentPath=/etc/systemd/system/reasoning-compute.service\nDropInPaths=\n"
                    b"User=reasoning-compute\nGroup=reasoning-compute\nLoadState=loaded\nNeedDaemonReload=no\n"
                ),
            )
        if args[:3] == ["systemctl", "enable", "--now"] and args[-1] == "reasoning-compute":
            self.active = True
        if args[0] == "dpkg-query":
            return subprocess.CompletedProcess(args, 0 if self.ce else 1, stdout=b"install ok installed" if self.ce else b"")
        if "container" in args and "ls" in args:
            return subprocess.CompletedProcess(args, 0, stdout=self.containers)
        if args[0] == "runuser":
            data = Path(args[args.index("--data-dir") + 1])
            if "configure" in args:
                registration = Path(args[args.index("--registration") + 1])
                record = json.loads(private_source(registration))
                config = compute_worker.enrollment(args[args.index("--server-url") + 1], record)
                private_file(data / "enrollment.json", canonical(config).encode())
            if "verify-images" in args and not self.verification:
                raise host.InstallError("fixture image/enrollment check failed")
        return subprocess.CompletedProcess(args, 0, stdout=b"")


@pytest.fixture
def bootstrap(tmp_path, monkeypatch):
    root = tmp_path / "host"
    root.mkdir(mode=0o700)
    commands = Commands()

    def identity(name):
        assert name == worker_host.ACCOUNT
        if not commands.account:
            raise KeyError(name)
        return SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid(), pw_dir=worker_host.DATA, pw_shell="/usr/sbin/nologin")

    monkeypatch.setattr(worker_host.pwd, "getpwnam", identity)
    return WorkerHostInstaller(root=root, command=commands), commands


def install(bundle, fixture, **changes):
    return fixture[0].install_worker(
        bundle,
        hashlib.sha256(bundle.read_bytes()).hexdigest(),
        ORIGIN,
        changes.get("registration", REGISTRATION),
        changes.get("catalog", [IMAGE]),
        skip_platform=True,
    )


def test_bootstrap_installs_only_separate_compute_service_and_does_not_reinstall_or_restart_on_resume(bundle, bootstrap):
    installer, commands = bootstrap
    state = install(bundle, bootstrap)
    assert state["service_active"] is True and state["container_execution_verified"] is False
    assert TOKEN not in json.dumps(state) and TOKEN not in json.dumps(commands.calls)
    data = installer.root / "var/lib/reasoning-compute"
    assert data.stat().st_mode & 0o777 == 0o700
    assert (data / "enrollment.json").stat().st_mode & 0o777 == 0o600
    assert not (data / "bootstrap-enrollment.json").exists()
    assert json.loads((data / "images.json").read_bytes()) == [IMAGE]
    unit = (installer.root / "etc/systemd/system/reasoning-compute.service").read_text()
    assert "User=reasoning-compute" in unit and "SupplementaryGroups=docker" in unit and "ReadWritePaths=/var/lib/reasoning-compute" in unit
    assert not (installer.root / "var/lib/reasoning").exists() and not (installer.root / "etc/reasoning").exists()
    pulls = [call for call in commands.calls if "pull" in call]
    assert len(pulls) == 1 and pulls[0][-1] == IMAGE["reference"] and "--quiet" in pulls[0] and "linux/amd64" in pulls[0]
    assert all("caddy" not in call for call in commands.calls)
    offset = len(commands.calls)
    assert install(bundle, bootstrap) == state
    assert len(commands.calls[offset:]) == 3 and "verify-images" in commands.calls[offset]
    assert commands.calls[-2] == ["systemctl", "is-active", "--quiet", "reasoning-compute"]
    assert commands.calls[-1][:2] == ["systemctl", "show"]


@pytest.mark.parametrize("phase", ["apt-get", "pip", "configure", "pull", "verify-images", "start"])
def test_interrupted_bootstrap_resumes_exact_binding_without_losing_enrollment(bundle, bootstrap, phase):
    installer, commands = bootstrap
    commands.fail = lambda args: (
        args[0] == "apt-get"
        if phase == "apt-get"
        else args[0].endswith("/pip")
        if phase == "pip"
        else "configure" in args
        if phase == "configure"
        else "pull" in args
        if phase == "pull"
        else "verify-images" in args
        if phase == "verify-images"
        else args[:3] == ["systemctl", "enable", "--now"] and args[-1] == "reasoning-compute"
    )
    with pytest.raises(host.InstallError, match="interruption"):
        install(bundle, bootstrap)
    assert not commands.active and TOKEN not in installer.state_path.read_text()
    commands.fail = None
    assert install(bundle, bootstrap)["service_active"] is True
    assert not (installer.root / "var/lib/reasoning-compute/bootstrap-enrollment.json").exists()


def test_lost_service_start_receipt_never_reinstalls_python_or_restarts_active_worker(bundle, bootstrap):
    installer, commands = bootstrap
    original = commands.__call__

    def receipt(args, **kwargs):
        result = original(args, **kwargs)
        if args[:3] == ["systemctl", "enable", "--now"] and args[-1] == "reasoning-compute":
            raise host.InstallError("lost startup receipt")
        return result

    installer.command = receipt
    with pytest.raises(host.InstallError, match="lost startup"):
        install(bundle, bootstrap)
    assert commands.active and json.loads(installer.state_path.read_bytes())["phase"] == "starting"
    installer.command = commands
    offset = len(commands.calls)
    assert install(bundle, bootstrap)["service_active"]
    assert all(
        call[0] not in {"apt-get", "python3"} and "configure" not in call and "restart" not in call for call in commands.calls[offset:]
    )


@pytest.mark.parametrize("change", ["token", "id", "organization", "origin", "image", "resources"])
def test_resume_refuses_changed_binding_before_commands(bundle, bootstrap, change):
    installer, commands = bootstrap
    install(bundle, bootstrap)
    count = len(commands.calls)
    record, catalog, origin = copy.deepcopy(REGISTRATION), copy.deepcopy([IMAGE]), ORIGIN
    if change == "token":
        record["token"] = "other-token-" + "y" * 40
    elif change == "id":
        record["id"] = "f" * 32
    elif change == "organization":
        record["organization_id"] = "f" * 32
    elif change == "origin":
        origin = "https://other.example.org"
    elif change == "image":
        catalog[0]["reference"] = "registry.example.org/research/cpu@sha256:" + "a" * 64
    else:
        record["resources"]["wall_time_seconds"] = 60
    with pytest.raises(host.InstallError, match="exact release"):
        installer.install_worker(bundle, hashlib.sha256(bundle.read_bytes()).hexdigest(), origin, record, catalog, skip_platform=True)
    assert len(commands.calls) == count


@pytest.mark.parametrize(
    "relative",
    [
        "var/lib/reasoning",
        "etc/reasoning",
        "opt/reasoning",
        "etc/systemd/system/reasoning-server.service",
        "var/lib/reasoning-deploy/installation.json",
    ],
)
def test_compute_host_refuses_chat_state_without_package_or_service_commands(bundle, bootstrap, relative):
    installer, commands = bootstrap
    target = installer.root / relative
    installer._directory(str(target.parent.relative_to(installer.root)), 0o700 if target.parent.name == "reasoning-deploy" else 0o755)
    target.write_text("existing chat state")
    with pytest.raises(host.InstallError, match="chat-server"):
        install(bundle, bootstrap)
    assert commands.calls == [] and target.read_text() == "existing chat state"


@pytest.mark.parametrize("relative", ["var/lib/reasoning-compute", "opt/reasoning-compute", "etc/systemd/system/reasoning-compute.service"])
def test_compute_host_refuses_unmanaged_worker_paths_without_commands(bundle, bootstrap, relative):
    installer, commands = bootstrap
    target = installer.root / relative
    installer._directory(str(target.parent.relative_to(installer.root)))
    target.write_text("preserve")
    with pytest.raises(host.InstallError, match="unmanaged compute"):
        install(bundle, bootstrap)
    assert commands.calls == [] and target.read_text() == "preserve"


def test_enrollment_or_image_verification_failure_never_starts_worker_service(bundle, bootstrap):
    installer, commands = bootstrap
    commands.verification = False
    with pytest.raises(host.InstallError, match="image/enrollment"):
        install(bundle, bootstrap)
    assert not commands.active and json.loads(installer.state_path.read_bytes())["phase"] == "packages"


@pytest.mark.parametrize("change", ["unit", "release", "catalog", "enrollment", "state"])
def test_resume_preserves_operator_changes_and_never_starts_or_reinstalls(bundle, bootstrap, change):
    installer, commands = bootstrap
    state = install(bundle, bootstrap)
    path = {
        "unit": installer.root / "etc/systemd/system/reasoning-compute.service",
        "release": installer.root / "opt/reasoning-compute/releases" / state["release_id"] / "NOTICE",
        "catalog": installer.root / "var/lib/reasoning-compute/images.json",
        "enrollment": installer.root / "var/lib/reasoning-compute/enrollment.json",
        "state": installer.state_path,
    }[change]
    path.write_bytes(b"operator changed this")
    count = len(commands.calls)
    with pytest.raises((host.InstallError, ValueError)):
        install(bundle, bootstrap)
    assert len(commands.calls) == count and path.read_bytes() == b"operator changed this"


@pytest.mark.parametrize("existing", ["containers", "docker-ce"])
def test_existing_docker_installation_or_containers_are_not_deleted(bundle, bootstrap, existing):
    _, commands = bootstrap
    commands.containers = b"a" * 64 if existing == "containers" else b""
    commands.ce = existing == "docker-ce"
    with pytest.raises(host.InstallError):
        install(bundle, bootstrap)
    assert not commands.active and all("rm" not in call and "remove" not in call and "purge" not in call for call in commands.calls)


def test_bootstrap_lock_is_private_and_exclusive(bundle, bootstrap):
    installer, commands = bootstrap
    directory = installer.root / "var/lib/reasoning-deploy"
    installer._directory("var/lib/reasoning-deploy", 0o700)
    lock = directory / "bootstrap.lock"
    fd = os.open(lock, os.O_RDONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(host.InstallError, match="another bootstrap"):
            install(bundle, bootstrap)
    finally:
        os.close(fd)
    assert commands.calls == []


@pytest.mark.parametrize(
    "relative",
    [
        "var/lib/reasoning-compute",
        "opt/reasoning-compute",
        "etc/systemd/system/reasoning-compute.service",
        "var/lib/reasoning-deploy/worker-installation.json",
    ],
)
def test_chat_installer_refuses_compute_host_before_package_commands(bundle, bootstrap, relative):
    installer, commands = bootstrap
    target = installer.root / relative
    installer._directory(str(target.parent.relative_to(installer.root)), 0o700 if target.parent.name == "reasoning-deploy" else 0o755)
    target.write_text("compute state")
    with pytest.raises(host.InstallError, match="compute-worker host"):
        host.HostInstaller(root=installer.root, command=commands).install(
            bundle, hashlib.sha256(bundle.read_bytes()).hexdigest(), ORIGIN, skip_platform=True
        )
    assert commands.calls == [] and target.read_text() == "compute state"


@pytest.mark.parametrize("missing", ["unit", "data"])
def test_resume_never_recreates_deleted_worker_state_or_unit(bundle, bootstrap, missing):
    installer, commands = bootstrap
    install(bundle, bootstrap)
    path = installer.root / ("etc/systemd/system/reasoning-compute.service" if missing == "unit" else "var/lib/reasoning-compute")
    path.rename(path.with_name(path.name + ".saved"))
    count = len(commands.calls)
    with pytest.raises(host.InstallError, match="missing"):
        install(bundle, bootstrap)
    assert len(commands.calls) == count and not path.exists()


@pytest.mark.parametrize("change", ["dirty", "phase", "duplicate"])
def test_journal_canonical_types_and_duplicate_fields_are_not_adopted(bundle, bootstrap, change):
    installer, commands = bootstrap
    install(bundle, bootstrap)
    state = json.loads(installer.state_path.read_bytes())
    if change == "dirty":
        state["dirty"] = int(state["dirty"])
        body = host.canonical(state)
    elif change == "phase":
        state["phase"] = []
        body = host.canonical(state)
    else:
        body = host.canonical(state).replace(b"{", b'{"phase":"services_started",', 1)
    installer.state_path.write_bytes(body)
    count = len(commands.calls)
    with pytest.raises((host.InstallError, ValueError)):
        install(bundle, bootstrap)
    assert len(commands.calls) == count


def test_verify_images_cli_reads_identity_and_local_metadata_without_claiming_jobs(tmp_path, monkeypatch, capsys):
    data = tmp_path / "worker"
    data.mkdir(mode=0o700)
    config, images = inputs(ORIGIN, REGISTRATION, [IMAGE])
    private_file(data / "enrollment.json", canonical(config).encode())
    catalog = tmp_path / "images.json"
    private_file(catalog, host.canonical(images))
    calls = []
    fake = SimpleNamespace(request=lambda method, path: calls.append((method, path)) or REGISTRATION, close=lambda: calls.append("closed"))
    monkeypatch.setattr(compute_worker, "WorkerClient", lambda _: fake)
    monkeypatch.setattr(compute_worker, "DockerRunner", lambda *args: SimpleNamespace(preflight=lambda *values: calls.append("metadata")))
    assert compute_worker.main(["--data-dir", str(data), "verify-images", "--catalog", str(catalog)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["claims_job"] is False and result["enrollment_verified"] is True
    assert calls == [("GET", "/api/worker/identity"), "metadata", "closed"]
    assert not (data / "job.json").exists() and TOKEN not in json.dumps(result)


@pytest.mark.parametrize("change", ["symlink", "hardlink", "fifo", "public", "large", "parent"])
def test_private_input_refuses_unsafe_files_and_paths_without_blocking(tmp_path, change):
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    source = directory / "registration"
    private_file(source, b"private")
    if change == "symlink":
        link = directory / "link"
        link.symlink_to(source)
        source = link
    elif change == "hardlink":
        os.link(source, directory / "duplicate")
    elif change == "fifo":
        source = directory / "pipe"
        os.mkfifo(source, 0o600)
    elif change == "public":
        source.chmod(0o644)
    elif change == "large":
        source.write_bytes(b"x" * 65537)
    else:
        directory.chmod(0o777)
    with pytest.raises((host.InstallError, OSError)):
        private_source(source)


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", "wrong"),
        ("organization_id", "wrong"),
        ("token", "bad secret\n"),
        ("image_ids", []),
        ("image_ids", ["fixture", "fixture"]),
        ("revoked_at", 1),
        ("server_url", "https://other.example.org"),
    ],
)
def test_enrollment_input_rejects_invalid_identity_or_scope_without_echoing_token(field, value):
    record = {**REGISTRATION, field: value}
    with pytest.raises(host.InstallError) as error:
        inputs(ORIGIN, record, [IMAGE])
    assert TOKEN not in str(error.value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("reference", "registry.example.org/research:latest"),
        ("reference", "--bad"),
        ("source_revision", "bad"),
        ("languages", ["bash"]),
        ("capabilities_path", "/etc/../secret"),
        ("sbom_digest", "bad"),
        ("languages", ["sage", "sage"]),
    ],
)
def test_catalog_rejects_mutable_or_unapproved_image_records(field, value):
    with pytest.raises(host.InstallError):
        inputs(ORIGIN, REGISTRATION, [{**IMAGE, field: value}])


def test_bootstrap_schema_matches_installed_catalog_and_resource_types_and_filters_unenrolled_images():
    other = {**IMAGE, "id": "other"}
    config, images = inputs(ORIGIN, REGISTRATION, [other, IMAGE])
    assert set(config) == {"id", "organization_id", "server_url", "token", "image_ids", "resources"}
    assert [ComputeImage.parse(image).record() for image in images] == [IMAGE]
    assert resources(config["resources"]) == worker_host.limits(config["resources"])
    for value in (True, "1", 1.5, 0, 65):
        invalid = {**REGISTRATION["resources"], "cpu": value}
        with pytest.raises(host.InstallError):
            worker_host.limits(invalid)
        with pytest.raises(ValueError):
            resources(invalid)
    with pytest.raises(ValueError):
        worker_host.decode_input(b'{"id":"first","id":"second"}')


def remote_install_receipt(bundle):
    manifest, release, checksum = host.inspect_bundle(bundle)
    config, images = inputs(ORIGIN, REGISTRATION, [IMAGE])
    return {
        **WorkerHostInstaller._binding(manifest, release, checksum, config, images),
        "phase": "services_started",
        "service_active": True,
        "container_execution_verified": False,
    }


def test_verified_ssh_uploads_private_canonical_enrollment_without_secret_arguments(bundle, tmp_path):
    files = {}
    for name, body in (
        ("registration.json", json.dumps(REGISTRATION).encode()),
        ("images.json", json.dumps([IMAGE]).encode()),
        ("identity", b"private key"),
        ("known-hosts", b"203.0.113.20 ssh-ed25519 verified-key"),
    ):
        path = tmp_path / name
        private_file(path, body)
        files[name] = path
    calls, uploaded = [], {}

    def command(args):
        calls.append(args)
        if args[0] == "scp":
            path = Path(args[-2])
            uploaded[path.name] = path.read_bytes()
            if path.name in {"enrollment.json", "images.json"}:
                assert path.stat().st_mode & 0o777 == 0o600
        if args[0] == "ssh" and "/worker-installer.py" in args[-1]:
            return subprocess.CompletedProcess(args, 0, stdout=host.canonical(remote_install_receipt(bundle)))

    result = cli.install_worker_remote(
        bundle,
        ORIGIN,
        files["registration.json"],
        files["images.json"],
        "203.0.113.20",
        files["identity"],
        files["known-hosts"],
        command=command,
    )
    config, images = inputs(ORIGIN, REGISTRATION, [IMAGE])
    assert uploaded["enrollment.json"] == host.canonical(config) and uploaded["images.json"] == host.canonical(images)
    assert TOKEN not in json.dumps(calls) and TOKEN not in json.dumps(result)
    assert all("StrictHostKeyChecking=yes" in call and "BatchMode=yes" in call for call in calls)
    remote = shlex.split(calls[-1][-1])
    assert "--expected-enrollment-sha256" in remote and "--expected-catalog-sha256" in remote
    assert result["worker_id"] == REGISTRATION["id"] and result["container_execution_verified"] is False


@pytest.mark.parametrize("kind", ["server", "worker"])
def test_remote_install_uploads_one_verified_snapshot_even_if_original_archive_changes(bundle, tmp_path, kind):
    identity, known, record, catalog = [tmp_path / name for name in ("identity", "known", "record", "catalog")]
    for path, body in (
        (identity, b"private key"),
        (known, b"known public host key"),
        (record, host.canonical(REGISTRATION)),
        (catalog, host.canonical([IMAGE])),
    ):
        private_file(path, body)
    original = bundle.read_bytes()
    receipt = remote_install_receipt(bundle)
    manifest, server_release, checksum = host.inspect_bundle(bundle)
    server_receipt = {
        **host.installation_binding(manifest, server_release, checksum, ORIGIN),
        "phase": "services_started",
        "service_active": True,
        "https_ready": True,
        "initialized": False,
        "setup_token_file": "/var/lib/reasoning/setup-token",
    }
    uploaded = {}

    def command(args):
        if args[0] == "ssh":
            bundle.write_bytes(b"original source changed after snapshot verification")
            if "/worker-installer.py" in args[-1]:
                return subprocess.CompletedProcess(args, 0, stdout=host.canonical(receipt))
            if "--expected-sha256" in args[-1]:
                return subprocess.CompletedProcess(args, 0, stdout=host.canonical(server_receipt))
        elif args[0] == "scp":
            source = Path(args[-2])
            uploaded[source.name] = source.read_bytes()

    _, release, _ = host.inspect_bundle(bundle)
    if kind == "worker":
        result = cli.install_worker_remote(
            bundle,
            ORIGIN,
            record,
            catalog,
            "203.0.113.20",
            identity,
            known,
            command=command,
            confirmed_release=release,
            confirmed_worker=REGISTRATION["id"],
        )
    else:
        result = cli.install_remote(bundle, ORIGIN, "203.0.113.20", identity, known, command=command, confirmed_release=release)
    assert uploaded["release.tar.gz"] == original and result["bundle_sha256"] == hashlib.sha256(original).hexdigest()
    assert uploaded["installer.py"] == Path(host.__file__).read_bytes()


@pytest.mark.parametrize("kind", ["server", "worker", "worker-id"])
def test_snapshot_confirmation_mismatch_is_refused_before_any_remote_command(bundle, tmp_path, kind):
    record, catalog = tmp_path / "registration", tmp_path / "catalog"
    private_file(record, host.canonical(REGISTRATION))
    private_file(catalog, host.canonical([IMAGE]))
    calls = []
    with pytest.raises(host.InstallError, match="confirm-"):
        if kind == "server":
            cli.install_remote(
                bundle, ORIGIN, "203.0.113.20", tmp_path / "missing", tmp_path / "missing", command=calls.append, confirmed_release="wrong"
            )
        else:
            cli.install_worker_remote(
                bundle,
                ORIGIN,
                record,
                catalog,
                "203.0.113.20",
                tmp_path / "missing",
                tmp_path / "missing",
                command=calls.append,
                confirmed_release="wrong" if kind == "worker" else None,
                confirmed_worker="wrong" if kind == "worker-id" else None,
            )
    assert calls == []


@pytest.mark.parametrize("confirmation", ["release", "worker"])
def test_install_worker_cli_refuses_wrong_confirmation_without_remote_commands(bundle, tmp_path, capsys, monkeypatch, confirmation):
    registration = tmp_path / "registration.json"
    private_file(registration, json.dumps(REGISTRATION).encode())
    _, release, _ = host.inspect_bundle(bundle)
    monkeypatch.setattr(cli, "install_worker_remote", lambda *args: pytest.fail("remote changes must not run"))
    with pytest.raises(SystemExit) as error:
        cli.main(
            [
                "install-worker",
                "--bundle",
                str(bundle),
                "--server-url",
                ORIGIN,
                "--registration",
                str(registration),
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


def test_enrollment_verification_reads_identity_and_metadata_without_claim_or_container_commands():
    config, _ = inputs(ORIGIN, REGISTRATION, [IMAGE])
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == "GET" and request.url.path == "/api/worker/identity" and request.content == b""
        assert request.headers["authorization"] == "Bearer " + TOKEN
        return httpx.Response(200, json={**REGISTRATION, "token": None})

    client = WorkerClient(config, transport=httpx.MockTransport(handler))
    checked = []
    runner = SimpleNamespace(preflight=lambda image, limits: checked.append((image, limits)))
    try:
        result = verify_enrollment(config, client, runner, {"fixture": ComputeImage.parse(IMAGE)}, backend="docker")
        assert result["enrollment_verified"] is True and result["claims_job"] is False and result["container_execution_verified"] is False
        assert len(requests) == len(checked) == 1 and checked[0][1] == REGISTRATION["resources"]
        assert TOKEN not in json.dumps(result)
    finally:
        client.close()


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", "other"),
        ("organization_id", "other"),
        ("image_ids", ["other"]),
        ("resources", {**REGISTRATION["resources"], "cpu": True}),
        ("revoked_at", 1),
        ("draining", True),
    ],
)
def test_remote_enrollment_mismatch_refuses_image_checks(field, value):
    config, _ = inputs(ORIGIN, REGISTRATION, [IMAGE])
    client = SimpleNamespace(request=lambda *args: {**REGISTRATION, field: value})
    runner = SimpleNamespace(preflight=lambda *args: pytest.fail("image checks must not proceed"))
    with pytest.raises(WorkerFault, match="enrollment_mismatch"):
        verify_enrollment(config, client, runner, {"fixture": ComputeImage.parse(IMAGE)}, backend="docker")


def test_worker_service_keeps_privileged_docker_access_out_of_chat_and_job_settings():
    unit = worker_unit("a" * 64)
    assert "SupplementaryGroups=docker" in unit and "NoNewPrivileges=yes" in unit and "CapabilityBoundingSet=\n" in unit
    assert "TimeoutStopSec=180" in unit and "--data-dir /var/lib/reasoning-compute run" in unit
    assert "reasoning-server" not in unit and "ExecStart=/usr/bin/docker" not in unit
    with pytest.raises(host.InstallError):
        worker_unit("untrusted\nExecStart=bad")


def test_standalone_worker_bootstrap_loads_its_exact_public_sibling_without_package_dependencies(tmp_path):
    directory = tmp_path / "standalone"
    directory.mkdir(mode=0o700)
    (directory / "installer.py").write_bytes(Path(host.__file__).read_bytes())
    (directory / "worker-installer.py").write_bytes(Path(worker_host.__file__).read_bytes())
    (directory / "installer.py").chmod(0o644)
    (directory / "worker-installer.py").chmod(0o644)
    result = subprocess.run([os.sys.executable, "-I", str(directory / "worker-installer.py"), "--help"], capture_output=True, timeout=10)
    assert result.returncode == 0 and b"--expected-enrollment-sha256" in result.stdout


def test_legacy_release_is_inspectable_but_cannot_start_worker_bootstrap(tmp_path, payload, bootstrap):
    payload.pop("worker-installer.py")
    legacy = pack(tmp_path / "legacy.tar.gz", payload)
    with pytest.raises(host.InstallError, match="worker bootstrap"):
        install(legacy, bootstrap)
    assert bootstrap[1].calls == []
