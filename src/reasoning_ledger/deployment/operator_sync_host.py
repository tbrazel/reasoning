"""Read-only export of verified installed release custody for another operator."""

from __future__ import annotations

import fcntl
import hashlib
import os
import re

try:
    from . import host, server_operations
except ImportError:
    import installer as host
    import reasoning_server_operations as server_operations


def export_state(expected: dict, request: dict, expected_manifest: dict, *, inspector=None) -> bytes:
    inspector = inspector or server_operations.ServerInspector()
    root = inspector.root
    state = root / "var/lib/reasoning-deploy"
    inspector._directory(state, private=True)
    request = host.provisioning_request(request)
    if (
        server_operations.release_binding(expected_manifest, expected["release_id"], expected["bundle_sha256"], expected["public_url"])
        != expected
    ):
        raise host.InstallError("original operator manifest differs from its retained binding")
    lock_path = state / "bootstrap.lock"
    inspector._file(lock_path, 0, private=True)
    fd = os.open(lock_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as lock:
        info, original = os.fstat(lock.fileno()), lock_path.lstat()
        if (info.st_dev, info.st_ino) != (original.st_dev, original.st_ino):
            raise host.InstallError("bootstrap lock changed during operator sync")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise host.InstallError("another host operation is running; resume or wait for that operation") from exc
        cloud = inspector._document(state / "server-provisioning.json")
        if set(cloud) != {"format", "binding", "request"} or cloud["format"] != 1 or cloud["request"] != request:
            raise host.InstallError("installed cloud ownership differs from this operator's original VM")
        marker = inspector._document(root / "etc/reasoning-provisioning.json")
        if marker != {key: request[key] for key in ("plan_id", "deployment_id")} or inspector.droplet_id() != request["droplet_id"]:
            raise host.InstallError("live VM identity differs from the original deployment")
        active = state / "active-server-upgrade.json"
        if active.exists() or active.is_symlink():
            pointer = inspector._document(active)
            if (
                set(pointer) != {"format", "operation_id", "plan_id"}
                or pointer["format"] != 1
                or any(
                    not isinstance(pointer[key], str) or not re.fullmatch(r"[a-f0-9]{64}", pointer[key])
                    for key in ("operation_id", "plan_id")
                )
            ):
                raise host.InstallError("active host operation identity is invalid")
            operation = state / "server-upgrades" / pointer["operation_id"]
            inspector._directory(operation, private=True)
            record = inspector._document(operation / "operation.json")
            if record["operation_id"] != pointer["operation_id"] or record["plan_id"] != pointer["plan_id"]:
                raise host.InstallError("active host operation records disagree")
            if (operation / "rollback.json").exists() or (operation / "rollback.json").is_symlink():
                raise host.InstallError("a rollback is active; resume its original operator transaction")
            pending = {
                "format": 1,
                **{key: pointer[key] for key in ("operation_id", "plan_id")},
                "source_release": record["identity"]["source"]["release_id"],
                "target_release": record["identity"]["target"]["release_id"],
            }
            metadata = {"format": 1, "operation": "operator-sync", "phase": "upgrade_active", "request": request, "pending": pending}
            body = host.canonical(metadata)
            return len(body).to_bytes(4, "big") + body
        fence = state / "server-upgrade-fence"
        if fence.exists() or fence.is_symlink():
            raise host.InstallError("server is restart-fenced; operator sync cannot adopt an incomplete installation")
        journal = inspector._document(state / "installation.json")
        if set(journal) != {"release_id", "bundle_sha256", "public_url", "source_revision", "dirty", "phase"}:
            raise host.InstallError("installed release journal has unsupported fields")
        binding = {name: value for name, value in journal.items() if name != "phase"}
        if journal["phase"] != "services_started" or binding["public_url"] != expected["public_url"] or binding["dirty"] is not False:
            raise host.InstallError("installed release is incomplete or belongs to a different origin")
        if (
            any(
                not isinstance(binding[key], str) or not re.fullmatch(pattern, binding[key])
                for key, pattern in (
                    ("release_id", r"[a-f0-9]{64}"),
                    ("bundle_sha256", r"[a-f0-9]{64}"),
                    ("source_revision", r"[a-f0-9]{40}"),
                )
            )
            or cloud["binding"] != binding
        ):
            raise host.InstallError("installed cloud and release bindings disagree")
        manifest = expected_manifest
        archive = b""
        retained = None
        if binding != expected:
            history = state / "server-upgrades"
            # The installer creates this intermediate directory as 0755;
            # the private state directory and each operation protect custody.
            inspector._directory(history)
            candidates = list(history.iterdir())
            if len(candidates) > 10_000:
                raise host.InstallError("server release history exceeds the inspection limit")
            for operation in sorted(candidates):
                if not re.fullmatch(r"[a-f0-9]{64}", operation.name):
                    continue
                inspector._directory(operation, private=True)
                for role in ("target", "source"):
                    path = operation / (role + ".tar.gz")
                    if not path.exists() and not path.is_symlink():
                        continue
                    candidate = inspector._file(path, host.MAX_BUNDLE, private=True)
                    if hashlib.sha256(candidate).hexdigest() == binding["bundle_sha256"]:
                        manifest, release, checksum = host.inspect_bundle(path, binding["bundle_sha256"])
                        if server_operations.release_binding(manifest, release, checksum, binding["public_url"]) != binding:
                            raise host.InstallError("retained archive differs from the installed release binding")
                        archive, retained = candidate, {"operation_id": operation.name, "archive": role}
                        break
                if archive:
                    break
            if not archive:
                raise host.InstallError(
                    "the host has no retained archive matching its current release; preserve the existing operator state"
                )
        report = inspector.inspect(
            manifest, binding["release_id"], binding["bundle_sha256"], binding["public_url"], owned_bootstrap_fd=lock.fileno()
        )
        if report["all_checks_passed"] is not True:
            failed = ", ".join(key for key, value in report["checks"].items() if value not in {"pass", "not_applicable"})
            raise host.InstallError("installed release diagnosis failed: " + failed + "; operator state was not adopted")
        ready = inspector.health(binding["public_url"], timeout=0, expected_release=binding["release_id"])
        if (
            ready.get("https_ready") is not True
            or ready.get("public_url") != binding["public_url"]
            or type(ready.get("initialized")) is not bool
        ):
            raise host.InstallError("current HTTPS installation readiness is unverified")
        receipt = host.installation_result(
            {
                **binding,
                "phase": "services_started",
                "service_active": True,
                "https_ready": True,
                "initialized": ready["initialized"],
                "setup_token_file": "/var/lib/reasoning/setup-token",
                "provisioning": request,
                "host_identity_verified": True,
            },
            binding,
            request,
        )
        metadata = {
            "format": 1,
            "operation": "operator-sync",
            "phase": "verified",
            "request": request,
            "installation": binding,
            "diagnosis": report,
            "installation_receipt": receipt,
            "archive_size": len(archive),
            "retained": retained,
        }
        body = host.canonical(metadata)
        if len(body) > server_operations.MAX_REPORT:
            raise host.InstallError("operator sync receipt exceeds its bounded envelope")
        return len(body).to_bytes(4, "big") + body + archive
