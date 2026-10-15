"""Standard-library bootstrap for a separate Ubuntu 24.04 Docker compute host."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
import platform
import pwd
import re
import stat
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path

if __package__:
    from . import host
else:
    # The operator uploads both verified public scripts into private root staging.
    # Load that exact sibling, never an environment-selected 'installer' module.
    sibling = Path(__file__).absolute().with_name("installer.py")
    for candidate in (sibling.parent, sibling):
        info = candidate.lstat()
        if info.st_uid != os.getuid() or info.st_mode & 0o022 or candidate.is_symlink():
            raise SystemExit("unsafe worker bootstrap script location")
    if not sibling.is_file() or sibling.stat().st_nlink != 1 or sibling.stat().st_size > 1024**2:
        raise SystemExit("invalid worker bootstrap dependency")
    specification = importlib.util.spec_from_file_location("reasoning_host_bootstrap", sibling)
    host = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(host)

RESOURCE_BOUNDS = {
    "cpu": (1, 64),
    "memory_bytes": (64 * 1024**2, 256 * 1024**3),
    "disk_bytes": (8 * 1024**2, 64 * 1024**3),
    "pids": (16, 4096),
    "wall_time_seconds": (1, 86400),
    "artifact_bytes": (1, 1024**3),
}
ACCOUNT = "reasoning-compute"
DATA = "/var/lib/reasoning-compute"
PHASES = {"packages", "configured", "starting", "services_started"}
RETIREMENT_PHASES = {"draining", "stopping", "retired"}


def decode_input(body: bytes):
    def unique(pairs):
        value = {}
        for key, entry in pairs:
            if key in value:
                raise ValueError("duplicate input fields")
            value[key] = entry
        return value

    return json.loads(body, object_pairs_hook=unique)


def limits(value: dict) -> dict:
    if (
        not isinstance(value, dict)
        or set(value) != set(RESOURCE_BOUNDS)
        or any(type(value[key]) is not int or not low <= value[key] <= high for key, (low, high) in RESOURCE_BOUNDS.items())
        or value["artifact_bytes"] > value["disk_bytes"] // 2
    ):
        raise host.InstallError("worker resources require six bounded integers and a valid artifact/scratch allowance")
    return dict(value)


def inputs(origin: str, registration: dict, catalog: list) -> tuple[dict, list]:
    """Validate before commands; output contains only the explicitly enrolled images."""
    origin = host.public_origin(origin)
    required = {"id", "organization_id", "token", "image_ids", "resources"}
    optional = {"server_url", "name", "created_at", "last_seen_at", "draining", "revoked_at", "retired_at", "retirement_id"}
    if (
        not isinstance(registration, dict)
        or not required <= registration.keys()
        or not registration.keys() <= required | optional
        or any(
            not isinstance(registration[key], str) or not re.fullmatch(r"[a-f0-9]{32}", registration[key])
            for key in ("id", "organization_id")
        )
        or not isinstance(registration["token"], str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", registration["token"])
        or registration.get("server_url", origin) != origin
        or registration.get("revoked_at") is not None
    ):
        raise host.InstallError("invalid worker enrollment or server binding")
    ids = registration["image_ids"]
    if (
        not isinstance(ids, list)
        or not 1 <= len(ids) <= 32
        or any(not isinstance(item, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", item) for item in ids)
        or len(set(ids)) != len(ids)
        or not isinstance(catalog, list)
        or not 1 <= len(catalog) <= 32
    ):
        raise host.InstallError("worker enrollment requires distinct approved image identifiers")
    normalized = {key: registration[key] for key in required}
    normalized["resources"] = limits(registration["resources"])
    normalized["server_url"] = origin
    selected, seen = [], set()
    fields = {
        "id",
        "name",
        "reference",
        "languages",
        "source_revision",
        "capabilities_digest",
        "sbom_digest",
        "capabilities_path",
        "sbom_path",
        "maximum_resources",
    }
    for image in catalog:
        if (
            not isinstance(image, dict)
            or set(image) != fields
            or not isinstance(image["id"], str)
            or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", image["id"])
            or image["id"] in seen
            or not isinstance(image["name"], str)
            or not 1 <= len(image["name"]) <= 200
            or not isinstance(image["reference"], str)
            or not re.fullmatch(
                r"[a-z0-9]+(?:[.-][a-z0-9]+)*(?::[0-9]{1,5})?/[a-z0-9]+(?:[./_-][a-z0-9]+)*@sha256:[a-f0-9]{64}", image["reference"]
            )
            or not isinstance(image["source_revision"], str)
            or not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", image["source_revision"])
            or any(
                not isinstance(image[key], str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", image[key])
                for key in ("capabilities_digest", "sbom_digest")
            )
            or any(
                not isinstance(image[key], str)
                or not re.fullmatch(r"/(?:etc|usr/share)/[A-Za-z0-9._/-]{1,180}", image[key])
                or ".." in image[key].split("/")
                for key in ("capabilities_path", "sbom_path")
            )
            or not isinstance(image["languages"], list)
            or not image["languages"]
            or any(
                not isinstance(item, str) or item not in {"sage", "python", "julia", "gap", "pari", "lean"} for item in image["languages"]
            )
            or len(image["languages"]) != len(set(image["languages"]))
        ):
            raise host.InstallError("invalid immutable worker image catalog")
        limits(image["maximum_resources"])
        seen.add(image["id"])
        if image["id"] in ids:
            selected.append(image)
    if not set(ids) <= seen:
        raise host.InstallError("worker enrollment names an image absent from the approved catalog")
    selected.sort(key=lambda image: image["id"])
    if len(host.canonical(normalized)) > 65536 or len(host.canonical(selected)) > 65536:
        raise host.InstallError("worker enrollment/catalog exceeds its byte allowance")
    return normalized, selected


def private_source(path: Path, maximum: int = 65536, *, owner: int | None = None) -> bytes:
    path = path.absolute()
    if ".." in path.parts:
        raise host.InstallError("private worker inputs cannot traverse parent paths")
    for parent in path.parents:
        info = parent.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid not in {os.getuid(), Path("/").stat().st_uid, owner}
            or info.st_mode & 0o022
            and not (parent != path.parent and info.st_uid == Path("/").stat().st_uid and info.st_mode & stat.S_ISVTX)
        ):
            raise host.InstallError("private worker input parents must be protected, not symlinks")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != (os.getuid() if owner is None else owner)
            or info.st_mode & 0o077
            or info.st_nlink != 1
            or info.st_size > maximum
        ):
            raise host.InstallError("worker input must be a bounded owner-owned private regular file")
        result = stream.read(maximum + 1)
        if len(result) > maximum:
            raise host.InstallError("worker input exceeds its byte allowance")
        return result


def worker_unit(release: str) -> str:
    if not re.fullmatch(r"[a-f0-9]{64}", release):
        raise host.InstallError("invalid worker release identity")
    return (
        host.MANAGED
        + f"""[Unit]
Description=Reasoning separate-host compute worker
After=network-online.target docker.service
Wants=network-online.target
Requires=docker.service

[Service]
Type=simple
User={ACCOUNT}
Group={ACCOUNT}
SupplementaryGroups=docker
WorkingDirectory={DATA}
ExecStart=/opt/reasoning-compute/releases/{release}/venv/bin/reasoning-compute-worker --data-dir {DATA} run
Restart=on-failure
RestartSec=10
TimeoutStopSec=180
UMask=0077
NoNewPrivileges=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths={DATA}
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
LockPersonality=yes
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
CapabilityBoundingSet=
LimitNOFILE=65536
Environment=PATH=/usr/sbin:/usr/bin:/sbin:/bin
Environment=LANG=C.UTF-8

[Install]
WantedBy=multi-user.target
"""
    )


def sealed_receipt(value, config: dict) -> dict:
    try:
        if (
            not isinstance(value, dict)
            or set(value) != {"worker_id", "organization_id", "retired", "retirement_id", "retired_at"}
            or value["worker_id"] != config["id"]
            or value["organization_id"] != config["organization_id"]
            or value["retired"] is not True
            or not re.fullmatch(r"[a-f0-9]{32}", value["retirement_id"])
            or type(value["retired_at"]) is not int
            or not 0 < value["retired_at"] < 2**63
        ):
            raise ValueError
        return dict(value)
    except (KeyError, TypeError, ValueError) as exc:
        raise host.InstallError("invalid worker retirement receipt") from exc


def unsettled_jobs(value) -> list:
    try:
        if not isinstance(value, list) or len(value) > 9:
            raise ValueError
        for job in value:
            if (
                not isinstance(job, dict)
                or set(job) != {"id", "status"}
                or not re.fullmatch(r"[a-f0-9]{32}", job["id"])
                or job["status"] not in {"running", "outcome_unknown"}
            ):
                raise ValueError
        return [{"id": job["id"], "status": job["status"]} for job in value]
    except (KeyError, TypeError, ValueError) as exc:
        raise host.InstallError("invalid bounded worker settlement status") from exc


def provisioning_request(value) -> dict:
    """Exact VM identity, without any authority to delete resources or data."""
    try:
        if (
            not isinstance(value, dict)
            or set(value) != {"format", "plan_id", "deployment_id", "droplet_id"}
            or type(value["format"]) is not int
            or value["format"] != 1
            or not re.fullmatch(r"[a-f0-9]{64}", value["plan_id"])
            or not re.fullmatch(r"[a-f0-9]{32}", value["deployment_id"])
            or type(value["droplet_id"]) is not int
            or not 0 < value["droplet_id"] < 2**63
        ):
            raise ValueError
        return dict(value)
    except (KeyError, TypeError, ValueError) as exc:
        raise host.InstallError("invalid exact worker VM installation binding") from exc


def installation_result(value, binding: dict, provisioning=None) -> dict:
    """Accept only a successful, exact, secret-free bootstrap receipt."""
    request = provisioning_request(provisioning) if provisioning is not None else None
    try:
        fields = {"phase", "service_active", "container_execution_verified"}
        if request is not None:
            fields |= {"provisioning", "host_identity_verified"}
        if (
            not isinstance(value, dict)
            or set(value) != set(binding) | fields
            or any(host.canonical(value[key]) != host.canonical(item) for key, item in binding.items())
            or value["phase"] != "services_started"
            or value["service_active"] is not True
            or value["container_execution_verified"] is not False
            or request is not None
            and (value["host_identity_verified"] is not True or host.canonical(value["provisioning"]) != host.canonical(request))
        ):
            raise ValueError
        return dict(value)
    except (KeyError, TypeError, ValueError) as exc:
        raise host.InstallError("invalid or mismatched worker host installation response") from exc


def retirement_result(value, binding: dict) -> dict:
    """Validate fixed host output before displaying it or using its evidence."""
    try:
        fields = {
            "phase",
            "service_active",
            "service_disabled",
            "container_cleanup_verified",
            "local_state_quiescent",
            "cloud_resources_deleted",
            "host_deletion_verified",
            "unsettled_jobs",
            "retirement",
        }
        if (
            not isinstance(value, dict)
            or set(value) != set(binding) | fields
            or any(host.canonical(value[key]) != host.canonical(item) for key, item in binding.items())
            or value["phase"] not in {"draining", "retired"}
            or type(value["service_active"]) is not bool
            or value["cloud_resources_deleted"] is not False
            or value["host_deletion_verified"] is not False
        ):
            raise ValueError
        jobs = unsettled_jobs(value["unsettled_jobs"])
        if value["phase"] == "retired":
            if (
                value["service_active"] is not False
                or value["service_disabled"] is not True
                or value["container_cleanup_verified"] is not True
                or value["local_state_quiescent"] is not True
                or jobs
            ):
                raise ValueError
            sealed_receipt(value["retirement"], {"id": binding["worker_id"], "organization_id": binding["organization_id"]})
        elif (
            not jobs
            or value["service_disabled"] is not None
            or value["container_cleanup_verified"] is not False
            or value["local_state_quiescent"] is not False
            or value["retirement"] is not None
        ):
            raise ValueError
        return dict(value)
    except (KeyError, TypeError, ValueError) as exc:
        raise host.InstallError("invalid worker host retirement response") from exc


def destruction_request(value) -> dict:
    try:
        if (
            not isinstance(value, dict)
            or set(value) != {"format", "plan_id", "deployment_id", "droplet_id", "delete_host_data"}
            or type(value["format"]) is not int
            or value["format"] != 1
            or not re.fullmatch(r"[a-f0-9]{64}", value["plan_id"])
            or not re.fullmatch(r"[a-f0-9]{32}", value["deployment_id"])
            or type(value["droplet_id"]) is not int
            or not 0 < value["droplet_id"] < 2**63
            or value["delete_host_data"] is not True
        ):
            raise ValueError
        return dict(value)
    except (KeyError, TypeError, ValueError) as exc:
        raise host.InstallError("invalid exact worker VM/data destruction confirmation") from exc


def destruction_result(value, binding, request):
    request = destruction_request(request)
    try:
        if not isinstance(value, dict) or set(value) != {"retirement", "destruction"}:
            raise ValueError
        retired = retirement_result(value["retirement"], binding)
        proof = value["destruction"]
        if retired["phase"] == "draining":
            if proof is not None:
                raise ValueError
        elif (
            not isinstance(proof, dict)
            or set(proof) != {"request", "all_containers_absent", "socket", "daemon_id"}
            or host.canonical(proof["request"]) != host.canonical(request)
            or proof["all_containers_absent"] is not True
            or proof["socket"] not in {"/run/docker.sock", "/var/run/docker.sock"}
            or not isinstance(proof["daemon_id"], str)
            or not 1 <= len(proof["daemon_id"]) <= 256
            or any(ord(char) < 32 for char in proof["daemon_id"])
        ):
            raise ValueError
        return dict(value)
    except (KeyError, TypeError, ValueError) as exc:
        raise host.InstallError("invalid or mismatched fresh worker destruction proof") from exc


class WorkerHostInstaller(host.HostInstaller):
    def __init__(self, *, root: Path = Path("/"), command=None):
        super().__init__(root=root, command=command or self._command)
        self.state_path = self.state_dir / "worker-installation.json"

    @staticmethod
    def _command(args: list[str], *, check: bool = True):
        result = subprocess.run(args, env=host.ENVIRONMENT, capture_output=True, check=False, timeout=1200)
        if check and result.returncode:
            raise host.InstallError("worker host command failed; retain bootstrap state and inspect local package/service diagnostics")
        return result

    def _private(self, path: Path, body: bytes, owner: int, group: int):
        if path.exists() or path.is_symlink():
            if private_source(path, owner=owner) != body:
                raise host.InstallError("refusing to replace changed private worker configuration")
            return
        host.atomic_write(path, body)
        os.chown(path, owner, group)

    def install_worker(
        self, bundle: Path, expected: str, origin: str, registration: dict, catalog: list, *, skip_platform=False, provisioning=None
    ):
        if provisioning is not None:
            provisioning = provisioning_request(provisioning)
        config, images = inputs(origin, registration, catalog)
        manifest, release, checksum = host.inspect_bundle(bundle, expected)
        if "worker-installer.py" not in manifest["files"]:
            raise host.InstallError("release does not include the worker bootstrap")
        self._platform(skip_platform)
        if provisioning is not None:
            self._verify_provisioning(provisioning)
        with self._bootstrap_ownership():
            marker = self.state_dir / "worker-retirement.json"
            if marker.exists() or marker.is_symlink():
                raise host.InstallError("worker retirement was requested; bootstrap cannot reactivate this enrollment")
            binding = self._binding(manifest, release, checksum, config, images)
            self._provisioning_state(binding, provisioning)
            result = self._install_worker(bundle, manifest, release, checksum, config, images)
            if provisioning is not None:
                self._verify_provisioning(provisioning)
                result.update(provisioning=provisioning, host_identity_verified=True)
            return installation_result(result, binding, provisioning)

    def _verify_provisioning(self, request):
        marker = decode_input(private_source(self.root / "etc/reasoning-provisioning.json"))
        if (
            host.canonical(marker) != host.canonical({key: request[key] for key in ("plan_id", "deployment_id")})
            or self._droplet_id() != request["droplet_id"]
        ):
            raise host.InstallError("SSH host does not match the owned DigitalOcean deployment and Droplet")

    def _provisioning_state(self, binding, request):
        path = self.state_dir / "worker-provisioning.json"
        if path.exists() or path.is_symlink():
            body = private_source(path)
            expected = {"format": 1, "binding": binding, "request": request}
            if request is None or host.canonical(decode_input(body)) != body or host.canonical(expected) != body:
                raise host.InstallError("owned worker installation requires its original exact VM, release, enrollment, and catalog")
        elif request is not None:
            # Do not attach a new VM identity to a different existing installation.
            self._installation_state(binding)
            host.atomic_write(path, host.canonical({"format": 1, "binding": binding, "request": request}))

    def _platform(self, skip_platform):
        if not skip_platform:
            if os.geteuid() != 0 or platform.system() != "Linux" or platform.machine() not in {"x86_64", "amd64"}:
                raise host.InstallError("worker bootstrap requires root on a dedicated Linux amd64 host")
            fields = dict(line.split("=", 1) for line in (self.root / "etc/os-release").read_text().splitlines() if "=" in line)
            if fields.get("ID", "").strip('"') != "ubuntu" or fields.get("VERSION_ID", "").strip('"') != "24.04":
                raise host.InstallError("automated worker bootstrap supports Ubuntu 24.04")

    @contextmanager
    def _bootstrap_ownership(self):
        directory = self._directory("var/lib/reasoning-deploy", 0o700)
        if directory.stat().st_mode & 0o077:
            raise host.InstallError("worker bootstrap journal must be private")
        fd = os.open(directory / "bootstrap.lock", os.O_RDONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_nlink != 1:
                raise host.InstallError("invalid private worker bootstrap lock")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise host.InstallError("another bootstrap owns this host") from exc
            yield
        finally:
            os.close(fd)

    def _separate_host(self):
        for name in (
            "var/lib/reasoning",
            "opt/reasoning",
            "etc/reasoning",
            "etc/systemd/system/reasoning-server.service",
            "var/lib/reasoning-deploy/installation.json",
            "var/lib/reasoning-deploy/active-server-recovery.json",
        ):
            if (self.root / name).exists() or (self.root / name).is_symlink():
                raise host.InstallError("compute bootstrap refuses a chat-server host; use separate hardware")

    @staticmethod
    def _binding(manifest, release, checksum, config, images):
        return {
            "release_id": release,
            "bundle_sha256": checksum,
            "source_revision": manifest["source_revision"],
            "dirty": manifest["dirty"],
            "server_url": config["server_url"],
            "worker_id": config["id"],
            "organization_id": config["organization_id"],
            "enrollment_sha256": hashlib.sha256(host.canonical(config)).hexdigest(),
            "catalog_sha256": hashlib.sha256(host.canonical(images)).hexdigest(),
        }

    def _installation_state(self, binding):
        if self.state_path.exists() or self.state_path.is_symlink():
            state_body = private_source(self.state_path, host.MAX_MANIFEST)
            state = decode_input(state_body)
            if (
                not isinstance(state, dict)
                or set(state) != set(binding) | {"phase"}
                or host.canonical(state) != state_body
                or any(host.canonical(state[key]) != host.canonical(value) for key, value in binding.items())
                or not isinstance(state["phase"], str)
                or state["phase"] not in PHASES
            ):
                raise host.InstallError("worker bootstrap can resume only the exact release, enrollment, origin, and image catalog")
            return state
        return None

    def _install_worker(self, bundle, manifest, release, checksum, config, images):
        self._separate_host()
        binding = self._binding(manifest, release, checksum, config, images)
        state = self._installation_state(binding)
        if state is None:
            for name in ("var/lib/reasoning-compute", "opt/reasoning-compute", "etc/systemd/system/reasoning-compute.service"):
                if (self.root / name).exists() or (self.root / name).is_symlink():
                    raise host.InstallError("refusing to adopt unmanaged compute state or configuration")
            try:
                pwd.getpwnam(ACCOUNT)
            except KeyError:
                pass
            else:
                raise host.InstallError("refusing to adopt an unmanaged compute account")
            state = {**binding, "phase": "packages"}
            host.atomic_write(self.state_path, host.canonical(state))
        unit = self.root / "etc/systemd/system/reasoning-compute.service"
        self._check_managed(unit, worker_unit(release))
        target = self._directory("opt/reasoning-compute/releases") / release
        data = self.root / DATA.removeprefix("/")
        if state["phase"] != "packages" and (not unit.exists() or not data.exists()):
            raise host.InstallError("managed worker unit or state is missing; bootstrap does not recreate an active installation")
        if target.exists() or target.is_symlink():
            self._check_release(target, manifest)
        verified = False
        if state["phase"] == "packages":
            if self.command(["systemctl", "is-active", "--quiet", "reasoning-compute"], check=False).returncode == 0:
                raise host.InstallError("refusing package changes while a compute service is active")
            for package in ("docker-ce", "docker-ce-cli", "containerd.io"):
                result = self.command(["dpkg-query", "-W", "--showformat=${Status}", package], check=False)
                if result.returncode == 0 and result.stdout == b"install ok installed":
                    raise host.InstallError("worker bootstrap uses Ubuntu docker.io; do not replace an existing Docker CE installation")
            self.command(["apt-get", "update"])
            self.command(["apt-get", "install", "--yes", "ca-certificates", "python3-venv", "docker.io"])
            self.command(["systemctl", "enable", "--now", "docker"])
            docker_config = self._directory("var/lib/reasoning-deploy/docker-client", 0o700)
            self._private(docker_config / "config.json", b'{"auths":{}}\n', os.getuid(), os.getgid())
            docker = ["/usr/bin/docker", "--config", str(docker_config), "--host", "unix:///var/run/docker.sock"]
            if self.command([*docker, "container", "ls", "--all", "--quiet"]).stdout.strip():
                raise host.InstallError("use a dedicated compute host without existing containers; no container was stopped or deleted")
            if not target.exists():
                temporary = Path(tempfile.mkdtemp(prefix=".release-", dir=target.parent))
                host.extract_bundle(bundle, temporary)
                os.chmod(temporary, 0o755)
                os.rename(temporary, target)
            self._check_release(target, manifest)
            venv = target / "venv"
            if venv.is_symlink():
                raise host.InstallError("worker Python environment must not be a symlink")
            self.command(["python3", "-m", "venv", str(venv)])
            self.command(
                [
                    str(venv / "bin/pip"),
                    "--disable-pip-version-check",
                    "install",
                    "--no-cache-dir",
                    "--require-hashes",
                    "--index-url",
                    "https://pypi.org/simple",
                    "-r",
                    str(target / "requirements.txt"),
                ]
            )
            host.install_python_payload(self.command, target, manifest)
            try:
                identity = pwd.getpwnam(ACCOUNT)
            except KeyError:
                self.command(
                    [
                        "useradd",
                        "--system",
                        "--user-group",
                        "--groups",
                        "docker",
                        "--home-dir",
                        DATA,
                        "--shell",
                        "/usr/sbin/nologin",
                        ACCOUNT,
                    ]
                )
                identity = pwd.getpwnam(ACCOUNT)
            self._identity(identity)
            self._data_directory(data, identity)
            registration = data / "bootstrap-enrollment.json"
            self._private(registration, host.canonical(config), identity.pw_uid, identity.pw_gid)
            self._private(data / "images.json", host.canonical(images), identity.pw_uid, identity.pw_gid)
            worker = ["runuser", "-u", ACCOUNT, "--", str(venv / "bin/reasoning-compute-worker"), "--data-dir", str(data)]
            self.command([*worker, "configure", "--server-url", config["server_url"], "--registration", str(registration)])
            if private_source(data / "enrollment.json", owner=identity.pw_uid) != host.canonical(config).rstrip(b"\n"):
                raise host.InstallError("worker enrollment was not installed exactly")
            registration.unlink()
            for image in images:
                self.command([*docker, "image", "pull", "--quiet", "--platform", "linux/amd64", image["reference"]])
            self.command([*worker, "verify-images", "--catalog", str(data / "images.json")])
            verified = True
            self._directory("etc/systemd/system")
            self._managed(unit, worker_unit(release))
            state["phase"] = "configured"
            host.atomic_write(self.state_path, host.canonical(state))
        # Resume never reinstalls packages or restarts an already active worker.
        self._check_release(target, manifest)
        self._check_managed(unit, worker_unit(release))
        identity = pwd.getpwnam(ACCOUNT)
        self._identity(identity)
        self._data_directory(data, identity)
        if private_source(data / "enrollment.json", owner=identity.pw_uid) != host.canonical(config).rstrip(b"\n") or private_source(
            data / "images.json", owner=identity.pw_uid
        ) != host.canonical(images):
            raise host.InstallError("managed worker enrollment or image catalog changed")
        if not verified:
            self.command(
                [
                    "runuser",
                    "-u",
                    ACCOUNT,
                    "--",
                    str(target / "venv/bin/reasoning-compute-worker"),
                    "--data-dir",
                    str(data),
                    "verify-images",
                    "--catalog",
                    str(data / "images.json"),
                ]
            )
        if state["phase"] in {"configured", "starting"}:
            state["phase"] = "starting"
            host.atomic_write(self.state_path, host.canonical(state))
            self.command(["systemctl", "daemon-reload"])
            self.command(["systemctl", "enable", "--now", "reasoning-compute"])
            state["phase"] = "services_started"
            host.atomic_write(self.state_path, host.canonical(state))
        active = self.command(["systemctl", "is-active", "--quiet", "reasoning-compute"], check=False).returncode == 0
        if not active:
            raise host.InstallError("compute service is not active; inspect local diagnostics without rerunning an attempt")
        self._service_identity()
        return {**binding, "phase": state["phase"], "service_active": True, "container_execution_verified": False}

    def retire_worker(
        self, bundle: Path, expected: str, origin: str, registration: dict, catalog: list, *, skip_platform=False, destruction=None
    ):
        if destruction is not None:
            destruction = destruction_request(destruction)
        config, images = inputs(origin, registration, catalog)
        manifest, release, checksum = host.inspect_bundle(bundle, expected)
        self._platform(skip_platform)
        with self._bootstrap_ownership():
            self._separate_host()
            binding = self._binding(manifest, release, checksum, config, images)
            installation = self._installation_state(binding)
            if installation is None or installation["phase"] != "services_started":
                raise host.InstallError("retirement requires the exact fully installed managed worker")
            target = self.root / "opt/reasoning-compute/releases" / release
            self._check_release(target, manifest)
            unit = self.root / "etc/systemd/system/reasoning-compute.service"
            if not unit.exists():
                raise host.InstallError("managed compute service is missing")
            self._check_managed(unit, worker_unit(release))
            identity = pwd.getpwnam(ACCOUNT)
            self._identity(identity)
            data = self.root / DATA.removeprefix("/")
            if not data.exists():
                raise host.InstallError("managed worker state is missing")
            self._data_directory(data, identity)
            if private_source(data / "enrollment.json", owner=identity.pw_uid) != host.canonical(config).rstrip(b"\n") or private_source(
                data / "images.json", owner=identity.pw_uid
            ) != host.canonical(images):
                raise host.InstallError("managed worker enrollment or catalog changed")
            executable = target / "venv/bin/reasoning-compute-worker"
            for path in (executable.parent.parent, executable.parent, executable):
                info = path.lstat()
                if info.st_uid != os.getuid() or info.st_mode & 0o022 or path.is_symlink():
                    raise host.InstallError("managed worker executable or environment is not protected")
            if not executable.is_file() or executable.stat().st_nlink != 1:
                raise host.InstallError("managed worker executable is invalid")
            if destruction is not None:
                marker = decode_input(private_source(self.root / "etc/reasoning-provisioning.json"))
                if (
                    marker != {key: destruction[key] for key in ("plan_id", "deployment_id")}
                    or self._droplet_id() != destruction["droplet_id"]
                ):
                    raise host.InstallError("SSH host does not match the owned DigitalOcean deployment and Droplet")
            result = self._retire_worker(binding, config, identity, data, executable)
            if destruction is None:
                return result
            proof = self._destruction_proof(destruction, identity, data, config) if result["phase"] == "retired" else None
            return destruction_result({"retirement": result, "destruction": proof}, binding, destruction)

    @staticmethod
    def _droplet_id():
        try:
            opener = host.urllib.request.build_opener(host.urllib.request.ProxyHandler({}), host.NoRedirect())
            with opener.open("http://169.254.169.254/metadata/v1/id", timeout=3) as response:
                value = response.read(65)
                if response.status != 200 or not re.fullmatch(rb"[1-9][0-9]{0,18}\n?", value):
                    raise ValueError
                return int(value)
        except (OSError, TypeError, ValueError) as exc:
            raise host.InstallError("DigitalOcean host metadata is unavailable or invalid") from exc

    def _destruction_proof(self, request, identity, data, config):
        runtime = decode_input(private_source(data / "docker-runtime.json", 4096, owner=identity.pw_uid))
        if (
            not isinstance(runtime, dict)
            or set(runtime) != {"format", "worker_id", "socket", "daemon_id"}
            or type(runtime["format"]) is not int
            or runtime["format"] != 1
            or runtime["worker_id"] != config["id"]
            or runtime["socket"] not in {"/run/docker.sock", "/var/run/docker.sock"}
            or not isinstance(runtime["daemon_id"], str)
            or not 1 <= len(runtime["daemon_id"]) <= 256
            or any(ord(char) < 32 for char in runtime["daemon_id"])
        ):
            raise host.InstallError("worker runtime binding is missing or changed")
        docker_config = self.state_dir / "docker-client"
        if private_source(docker_config / "config.json") != b'{"auths":{}}\n':
            raise host.InstallError("managed Docker client configuration changed")
        docker = ["/usr/bin/docker", "--config", str(docker_config), "--host", "unix://" + runtime["socket"]]
        info = self._worker_output([*docker, "info", "--format", "{{json .}}"])
        if not isinstance(info, dict) or info.get("ID") != runtime["daemon_id"]:
            raise host.InstallError("Docker daemon identity changed before VM destruction")
        containers = self.command([*docker, "container", "ls", "--all", "--quiet"])
        if containers.returncode != 0 or not isinstance(containers.stdout, bytes) or containers.stdout.strip():
            raise host.InstallError("VM destruction refuses any remaining container, including unrelated or stopped containers")
        after = self._worker_output([*docker, "info", "--format", "{{json .}}"])
        if not isinstance(after, dict) or after.get("ID") != runtime["daemon_id"]:
            raise host.InstallError("Docker daemon identity changed during VM destruction checks")
        self._service_stopped()
        return {"request": request, "all_containers_absent": True, "socket": runtime["socket"], "daemon_id": runtime["daemon_id"]}

    def _worker_output(self, args):
        response = self.command(args)
        try:
            if response.returncode != 0 or not isinstance(response.stdout, bytes) or len(response.stdout) > 65536:
                raise ValueError
            return decode_input(response.stdout)
        except (AttributeError, TypeError, ValueError) as exc:
            raise host.InstallError("worker lifecycle command did not return a bounded valid response") from exc

    def _service_identity(self):
        response = self.command(
            [
                "systemctl",
                "show",
                "--no-pager",
                "--property=FragmentPath,DropInPaths,User,Group,LoadState,NeedDaemonReload",
                "reasoning-compute",
            ]
        )
        expected = {
            "FragmentPath": "/etc/systemd/system/reasoning-compute.service",
            "DropInPaths": "",
            "User": ACCOUNT,
            "Group": ACCOUNT,
            "LoadState": "loaded",
            "NeedDaemonReload": "no",
        }
        try:
            if response.returncode != 0 or not isinstance(response.stdout, bytes) or len(response.stdout) > 4096:
                raise ValueError
            pairs = [line.split("=", 1) for line in response.stdout.decode().splitlines()]
            if len(pairs) != len(expected) or dict(pairs) != expected:
                raise ValueError
        except (AttributeError, TypeError, ValueError) as exc:
            raise host.InstallError("loaded worker service has overrides, changed identity, or unverified configuration") from exc

    def _service_active(self):
        response = self.command(["systemctl", "show", "--property=ActiveState", "--value", "reasoning-compute"])
        if response.returncode != 0 or response.stdout not in {
            b"inactive\n",
            b"failed\n",
            b"active\n",
            b"activating\n",
            b"deactivating\n",
            b"reloading\n",
            b"refreshing\n",
        }:
            raise host.InstallError("worker service activity is unavailable or unverified")
        return response.stdout not in {b"inactive\n", b"failed\n"}

    def _service_stopped(self):
        self._service_identity()
        active = self._service_active()
        enabled = self.command(["systemctl", "is-enabled", "reasoning-compute"], check=False)
        if active or enabled.returncode <= 0 or enabled.stdout != b"disabled\n":
            raise host.InstallError("worker retirement requires a stopped and disabled managed service")

    def _retire_worker(self, binding, config, identity, data, executable):
        path = self.state_dir / "worker-retirement.json"
        if path.exists() or path.is_symlink():
            raw = private_source(path)
            state = decode_input(raw)
            if (
                not isinstance(state, dict)
                or set(state) != set(binding) | {"format", "phase", "retirement"}
                or type(state["format"]) is not int
                or state["format"] != 1
                or any(host.canonical(state[key]) != host.canonical(value) for key, value in binding.items())
                or state["phase"] not in RETIREMENT_PHASES
                or host.canonical(state) != raw
            ):
                raise host.InstallError("worker retirement journal has changed or belongs to another installation")
            if state["phase"] == "retired":
                sealed_receipt(state["retirement"], config)
            elif state["retirement"] is not None:
                raise host.InstallError("unsealed retirement journal cannot contain a receipt")
        else:
            state = {**binding, "format": 1, "phase": "draining", "retirement": None}
        self._service_identity()
        if not path.exists():
            host.atomic_write(path, host.canonical(state))
        worker = ["runuser", "-u", ACCOUNT, "--", str(executable), "--data-dir", str(data)]
        local = data / "retirement.json"
        if local.exists() or local.is_symlink():
            # A manual or lost-receipt seal is independently rechecked by retire.
            sealed_receipt(decode_input(private_source(local, owner=identity.pw_uid)), config)
        elif state["phase"] == "draining":
            status = self._worker_output([*worker, "drain", "--confirm-worker", config["id"]])
            if (
                not isinstance(status, dict)
                or set(status)
                != {
                    "worker_id",
                    "organization_id",
                    "draining",
                    "unsettled_jobs",
                    "quiescent",
                    "physical_cleanup_verified",
                    "cloud_resources_deleted",
                }
                or status["worker_id"] != config["id"]
                or status["organization_id"] != config["organization_id"]
                or status["draining"] is not True
                or type(status["quiescent"]) is not bool
                or status["physical_cleanup_verified"] is not False
                or status["cloud_resources_deleted"] is not False
            ):
                raise host.InstallError("worker drain response has changed or mismatched its enrollment")
            jobs = unsettled_jobs(status["unsettled_jobs"])
            if status["quiescent"] != (not jobs):
                raise host.InstallError("worker drain response has inconsistent settlement status")
            if jobs:
                return retirement_result(
                    {
                        **binding,
                        "phase": "draining",
                        "service_active": self._service_active(),
                        "service_disabled": None,
                        "container_cleanup_verified": False,
                        "local_state_quiescent": False,
                        "cloud_resources_deleted": False,
                        "host_deletion_verified": False,
                        "unsettled_jobs": jobs,
                        "retirement": None,
                    },
                    binding,
                )
        if state["phase"] == "draining":
            state["phase"] = "stopping"
            host.atomic_write(path, host.canonical(state))
        self._service_identity()
        self.command(["systemctl", "disable", "--now", "reasoning-compute"])
        self._service_stopped()
        value = self._worker_output([*worker, "retire", "--confirm-worker", config["id"]])
        if (
            not isinstance(value, dict)
            or set(value)
            != {
                "worker_id",
                "organization_id",
                "retired",
                "retirement_id",
                "retired_at",
                "container_cleanup_verified",
                "local_state_quiescent",
                "cloud_resources_deleted",
            }
            or value["container_cleanup_verified"] is not True
            or value["local_state_quiescent"] is not True
            or value["cloud_resources_deleted"] is not False
        ):
            raise host.InstallError("worker retirement did not prove cleanup and settlement")
        receipt = sealed_receipt(
            {key: value[key] for key in ("worker_id", "organization_id", "retired", "retirement_id", "retired_at")}, config
        )
        if state["retirement"] is not None and receipt != state["retirement"]:
            raise host.InstallError("worker retirement receipt changed")
        if decode_input(private_source(local, owner=identity.pw_uid)) != receipt:
            raise host.InstallError("worker retirement receipt is not durably present on the host")
        self._service_stopped()
        state.update(phase="retired", retirement=receipt)
        host.atomic_write(path, host.canonical(state))
        return retirement_result(
            {
                **binding,
                "phase": "retired",
                "service_active": False,
                "service_disabled": True,
                "container_cleanup_verified": True,
                "local_state_quiescent": True,
                "cloud_resources_deleted": False,
                "host_deletion_verified": False,
                "unsettled_jobs": [],
                "retirement": receipt,
            },
            binding,
        )

    @staticmethod
    def _identity(identity):
        if identity.pw_uid == 0 or identity.pw_gid == 0 or identity.pw_dir != DATA or identity.pw_shell != "/usr/sbin/nologin":
            raise host.InstallError("managed compute identity has changed")

    def _data_directory(self, data: Path, identity):
        self._directory("var/lib")
        if not data.exists() and not data.is_symlink():
            data.mkdir(mode=0o700)
            os.chown(data, identity.pw_uid, identity.pw_gid)
        info = data.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != identity.pw_uid or info.st_mode & 0o077:
            raise host.InstallError("compute state must be a private directory owned by its service account")

    def _check_release(self, target, manifest):
        if target.is_symlink() or not target.is_dir():
            raise host.InstallError("invalid worker release directory")
        for name, record in manifest["files"].items():
            path = target / name
            for parent in path.parents:
                info = parent.lstat()
                if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
                    raise host.InstallError("worker release parents must be protected directories")
                if parent == self.root:
                    break
            info = path.lstat()
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_nlink != 1
                or info.st_mode & 0o022
                or info.st_size != record["size"]
                or hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]
            ):
                raise host.InstallError("worker release differs from its verified manifest")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--server-url", required=True)
    parser.add_argument("--registration", required=True, type=Path)
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--expected-enrollment-sha256", required=True)
    parser.add_argument("--expected-catalog-sha256", required=True)
    parser.add_argument("--operation", choices=("install", "retire", "prepare-destroy"), default="install")
    parser.add_argument("--destruction", type=Path)
    parser.add_argument("--expected-destruction-sha256")
    parser.add_argument("--provisioning", type=Path)
    parser.add_argument("--expected-provisioning-sha256")
    args = parser.parse_args(argv)
    try:
        registration = private_source(args.registration)
        catalog = private_source(args.catalog)
        if (
            hashlib.sha256(registration).hexdigest() != args.expected_enrollment_sha256
            or hashlib.sha256(catalog).hexdigest() != args.expected_catalog_sha256
        ):
            raise host.InstallError("private worker bootstrap inputs do not match their confirmed hashes")
        extra = {}
        if args.provisioning is not None or args.expected_provisioning_sha256 is not None:
            if args.operation != "install" or args.provisioning is None or args.expected_provisioning_sha256 is None:
                raise host.InstallError("confirmed private provisioning inputs are only valid for install")
            body = private_source(args.provisioning)
            if hashlib.sha256(body).hexdigest() != args.expected_provisioning_sha256:
                raise host.InstallError("provisioning input hash does not match confirmation")
            extra["provisioning"] = provisioning_request(decode_input(body))
        if args.operation == "prepare-destroy":
            if args.destruction is None or args.expected_destruction_sha256 is None:
                raise host.InstallError("VM retirement requires confirmed private destruction inputs")
            body = private_source(args.destruction)
            if hashlib.sha256(body).hexdigest() != args.expected_destruction_sha256:
                raise host.InstallError("destruction input hash does not match confirmation")
            extra["destruction"] = destruction_request(decode_input(body))
        elif args.destruction is not None or args.expected_destruction_sha256 is not None:
            raise host.InstallError("destruction inputs are only valid for prepare-destroy")
        installer = WorkerHostInstaller()
        operation = installer.retire_worker if args.operation != "install" else installer.install_worker
        result = operation(args.bundle, args.expected_sha256, args.server_url, decode_input(registration), decode_input(catalog), **extra)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (host.InstallError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        message = str(exc) if isinstance(exc, host.InstallError) else "worker bootstrap input or state is invalid or unavailable"
        parser.exit(2, f"reasoning-deploy: {message}\n")


if __name__ == "__main__":
    raise SystemExit(main())
