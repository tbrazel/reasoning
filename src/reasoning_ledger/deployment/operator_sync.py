"""Reconcile an independent operator with the verified current host release."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shlex
import tempfile
from pathlib import Path

from . import host, operator_sync_host, server_diagnostics, server_operations, setup_ui
from .digitalocean import DigitalOcean
from .private import locked_directory, read_document, read_private, write_document
from .release_state import archived_release, current_binding, record_current, release_binding, retain_release
from .setup import temporary_ssh_cidr
from .setup_upgrade import _operation, _private_directory, _ssh_action
from .ssh_host import server_trust


def remote_script(expected: dict, request: dict, manifest: dict) -> bytes:
    sources = []
    for module in (host, server_operations, operator_sync_host):
        source = Path(module.__file__).read_bytes()
        if len(source) > 256 * 1024:
            raise host.InstallError("operator sync tooling exceeds its source allowance")
        sources.append(base64.b64encode(source).decode("ascii"))
    inputs = base64.b64encode(host.canonical({"expected": expected, "request": request, "manifest": manifest})).decode("ascii")
    return f"""import base64, json, os, sys, types
if os.geteuid() != 0:
    raise SystemExit('operator sync requires root read access')
installer = types.ModuleType('installer')
sys.modules['installer'] = installer
exec(compile(base64.b64decode({sources[0]!r}), '<reasoning-installer>', 'exec'), installer.__dict__)
operations = types.ModuleType('reasoning_server_operations')
sys.modules[operations.__name__] = operations
exec(compile(base64.b64decode({sources[1]!r}), '<reasoning-server-operations>', 'exec'), operations.__dict__)
synchronizer = types.ModuleType('reasoning_operator_sync')
sys.modules[synchronizer.__name__] = synchronizer
exec(compile(base64.b64decode({sources[2]!r}), '<reasoning-operator-sync>', 'exec'), synchronizer.__dict__)
request = json.loads(base64.b64decode({inputs!r}))
sys.stdout.buffer.write(synchronizer.export_state(request['expected'], request['request'], request['manifest']))
""".encode("ascii")


def fetch_current(address, identity, known_hosts, expected, request, manifest, *, runner=server_diagnostics._run) -> bytes:
    from .cli import ssh_arguments

    arguments = ["ssh", *ssh_arguments(address, identity, known_hosts), "root@" + address, "python3 -I -S -B -"]
    response = runner(
        arguments, remote_script(expected, request, manifest), maximum_output=host.MAX_BUNDLE + server_operations.MAX_REPORT + 4
    )
    if not isinstance(response.stdout, bytes):
        raise host.InstallError("operator sync returned an invalid binary receipt")
    return response.stdout


def decode_receipt(body: bytes, request: dict) -> tuple[dict, bytes]:
    if not isinstance(body, bytes) or not 4 < len(body) <= host.MAX_BUNDLE + server_operations.MAX_REPORT + 4:
        raise host.InstallError("operator sync returned an oversized or truncated receipt")
    length = int.from_bytes(body[:4], "big")
    if not 1 <= length <= server_operations.MAX_REPORT or 4 + length > len(body):
        raise host.InstallError("operator sync returned an invalid envelope")
    try:
        metadata = json.loads(body[4 : 4 + length])
    except (UnicodeError, ValueError) as exc:
        raise host.InstallError("operator sync returned invalid JSON") from exc
    if (
        not isinstance(metadata, dict)
        or host.canonical(metadata) != body[4 : 4 + length]
        or type(metadata.get("format")) is not int
        or metadata["format"] != 1
    ):
        raise host.InstallError("operator sync returned a noncanonical receipt")
    if metadata.get("operation") != "operator-sync" or metadata.get("request") != request:
        raise host.InstallError("operator sync returned a different cloud ownership identity")
    archive = body[4 + length :]
    if metadata.get("phase") == "upgrade_active":
        if set(metadata) != {"format", "operation", "phase", "request", "pending"} or archive:
            raise host.InstallError("operator sync returned an invalid pending receipt")
        pending = metadata["pending"]
        if (
            not isinstance(pending, dict)
            or set(pending) != {"format", "operation_id", "plan_id", "source_release", "target_release"}
            or (type(pending["format"]) is not int or pending["format"] != 1)
            or any(
                not isinstance(pending[key], str) or not re.fullmatch(r"[a-f0-9]{64}", pending[key]) for key in pending if key != "format"
            )
        ):
            raise host.InstallError("operator sync returned an invalid pending operation identity")
    elif metadata.get("phase") == "verified":
        if set(metadata) != {
            "format",
            "operation",
            "phase",
            "request",
            "installation",
            "diagnosis",
            "installation_receipt",
            "archive_size",
            "retained",
        }:
            raise host.InstallError("operator sync receipt has unexpected fields")
        if type(metadata["archive_size"]) is not int or metadata["archive_size"] != len(archive):
            raise host.InstallError("operator sync archive is truncated or changed")
        retained = metadata["retained"]
        if archive and (
            not isinstance(retained, dict)
            or set(retained) != {"operation_id", "archive"}
            or not isinstance(retained["operation_id"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", retained["operation_id"])
            or retained["archive"] not in {"source", "target"}
        ):
            raise host.InstallError("operator sync archive custody is invalid")
        if not archive and retained is not None:
            raise host.InstallError("operator sync archive custody differs from its envelope")
    else:
        raise host.InstallError("operator sync returned an unsupported phase")
    return metadata, archive


def synchronize_operator(directory: Path, command, *, detect_public_ipv4=None, fetch=fetch_current,
                         provider_factory=DigitalOcean, require_no_pending_upgrade=False) -> dict:
    with locked_directory(directory.absolute()) as state:
        source = current_binding(state)
        manifest, release, checksum = host.inspect_bundle(archived_release(state, source["release_id"]), source["bundle_sha256"])
        expected = server_operations.release_binding(manifest, release, checksum, source["public_url"])
        config_path, plan_path, ownership = state / "setup.json", state / "plan.json", state / "ownership"
        config, deployment = read_document(config_path), read_document(plan_path)
        completion = read_document(state / "setup-completion.json")
        installation = read_document(ownership / "digitalocean-installation.json")
        try:
            request = host.provisioning_request(installation["intent"]["request"])
            address = installation["intent"]["ssh_host"]
            if (
                request["plan_id"] != completion["plan_id"]
                or request["deployment_id"] != deployment["deployment_id"]
                or (
                    read_document(ownership / "digitalocean.json")["resources"]["droplet"]
                    != {"status": "created", "id": request["droplet_id"]}
                )
                or completion["public_url"] != source["public_url"]
            ):
                raise ValueError
        except (KeyError, TypeError, ValueError) as exc:
            raise host.InstallError("operator sync requires the original cloud ownership and completed setup identity") from exc
        provider = provider_factory(read_private(Path(config["token_file"]), 4096).decode("ascii").strip())
        try:
            if provider.identity() != deployment["plan"]["account_identity"]:
                raise host.InstallError("DigitalOcean token belongs to a different deployment Team")
        finally:
            provider.close()
        cidr = temporary_ssh_cidr(detect_public_ipv4)
        try:
            config, opened = _ssh_action(
                command, state, config_path, config, plan_path, request["plan_id"], ownership, ["--open-cidr", cidr, "--wait-seconds", 60]
            )
            if opened.get("phase") != "open":
                raise host.InstallError("temporary SSH access is unverified; retry operator sync with the same state")
            with (
                server_trust(deployment, ownership, address, request) as known_hosts,
                setup_ui.upgrade_progress(
                    "Synchronizing this operator with the verified installed release",
                    ("verify VM ownership", "pin SSH trust", "retain current release"),
                ),
            ):
                metadata, archive = decode_receipt(
                    fetch(address, Path(config["identity"]), known_hosts, expected, request, manifest), request
                )
            if metadata["phase"] == "upgrade_active":
                pending = metadata["pending"]
                operation = state / "upgrades" / pending["target_release"] / "operation.json"
                if not operation.exists() and not operation.is_symlink():
                    raise host.InstallError("another operator owns the active upgrade; wait for it to finish")
                local = _operation(read_document(operation), pending["source_release"], pending["target_release"], pending["plan_id"])
                if local != pending:
                    raise host.InstallError("another operator owns the active upgrade; wait for it to finish")
                if require_no_pending_upgrade:
                    raise host.InstallError(
                        "this operator has an unfinished upgrade; no new release may be built. "
                        "Resume its exact retained archive and operation with: make upgrade-resume DEPLOY_STATE="
                        + shlex.quote(str(state))
                    )
                result = {
                    "phase": "operator_sync_deferred",
                    "public_url": source["public_url"],
                    "state_dir": str(state),
                    "operation_id": pending["operation_id"],
                    "target_release": pending["target_release"],
                    "next_step": "resume this operator's existing upgrade",
                }
            else:
                actual = metadata["installation"]
                if not isinstance(actual, dict) or actual.get("public_url") != source["public_url"] or actual.get("dirty") is not False:
                    raise host.InstallError("operator sync cannot adopt a different origin or uncommitted release")
                with tempfile.TemporaryDirectory(prefix=".operator-sync-", dir=state) as temporary:
                    if archive:
                        bundle = Path(temporary) / "release.tar.gz"
                        fd = os.open(bundle, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                        with os.fdopen(fd, "wb") as stream:
                            stream.write(archive)
                        current_manifest, current_release, current_checksum = host.inspect_bundle(bundle, actual["bundle_sha256"])
                    else:
                        bundle = archived_release(state, source["release_id"])
                        current_manifest, current_release, current_checksum = manifest, release, checksum
                    if (
                        server_operations.release_binding(current_manifest, current_release, current_checksum, source["public_url"])
                        != actual
                    ):
                        raise host.InstallError("operator sync archive and installation identities disagree")
                    report = server_operations.validate_report(metadata["diagnosis"], actual, current_manifest["database_schema"])
                    if report["all_checks_passed"] is not True:
                        raise host.InstallError("operator sync diagnosis did not pass every installation check")
                    host.installation_result(metadata["installation_receipt"], actual, request)
                    target = release_binding(current_manifest, current_release, current_checksum, source["public_url"])
                    retain_release(bundle, state, target)
                # Keep source authority independently addressable if a later
                # completion/installation write succeeds but its reply is lost.
                record_current(state, source)
                history = _private_directory(state / "operator-sync")
                # Configuration/readiness can change without a release change.
                # Keep each independently verified observation immutable.
                observation = hashlib.sha256(host.canonical(metadata)).hexdigest()
                record = history / (source["release_id"] + "-" + target["release_id"] + "-" + observation + ".json")
                before = {"current-release": source, "setup-completion": completion, "installation": installation}
                saved = {"format": 1, "before": before, "receipt": metadata}
                if record.exists() or record.is_symlink():
                    previous = read_document(record)
                    if previous["receipt"] != metadata:
                        raise host.InstallError("saved operator sync evidence differs; preserve the original state")
                else:
                    write_document(record, saved, new=True)
                write_document(state / "setup-completion.json", {**completion, "release_id": target["release_id"]})
                write_document(
                    ownership / "digitalocean-installation.json",
                    {
                        **installation,
                        "intent": {**installation["intent"], "binding": actual},
                        "phase": "installed",
                        "receipt": metadata["installation_receipt"],
                    },
                )
                record_current(state, target)
                result = {
                    "phase": "operator_synchronized",
                    "release_id": target["release_id"],
                    "public_url": target["public_url"],
                    "state_dir": str(state),
                    "changed": source != target,
                }
        finally:
            config, closed = _ssh_action(
                command, state, config_path, config, plan_path, request["plan_id"], ownership, ["--close", "--wait-seconds", 60]
            )
            if closed.get("phase") != "closed":
                raise host.InstallError("SSH closure is unverified; rerun operator sync with this same state")
        return {**result, "ssh_closed": True}


def resume_operator_upgrade(directory: Path, command, *, detect_public_ipv4=None, fetch=fetch_current,
                            provider_factory=DigitalOcean) -> dict:
    """Resume only a remotely verified, locally owned operation, without a build."""
    result = synchronize_operator(directory, command, detect_public_ipv4=detect_public_ipv4,
                                  fetch=fetch, provider_factory=provider_factory)
    if result["phase"] != "operator_sync_deferred":
        return {**result, "phase": "no_pending_upgrade"}
    from .setup_upgrade import run_upgrade

    return run_upgrade(archived_release(directory.absolute(), result["target_release"]), directory, command,
                       detect_public_ipv4=detect_public_ipv4)
