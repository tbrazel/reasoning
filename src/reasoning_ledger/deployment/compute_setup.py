"""Enable a recoverable Modal compute relay on an already-running organization.

Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
"""

import getpass
import re
import secrets
from pathlib import Path

from .host import InstallError, inspect_bundle
from .modal_setup import collect_modal, owned_modal_remote, setup_modal, verify_modal_credentials
from .private import locked_directory, read_document, write_document
from .release import save_release_catalog
from .release_state import archived_release, current_binding, operator_origin
from .setup import approve, ask, close_ssh_with_token_recovery, temporary_ssh_cidr
from .setup_upgrade import _ssh_action


def run_compute_setup(
    directory: Path,
    command,
    *,
    detect_public_ipv4=None,
    collect=collect_modal,
    verify=verify_modal_credentials,
    install=setup_modal,
    remote=owned_modal_remote,
):
    """The caller synchronizes the operator first. No new chat VM or Docker daemon is required here."""
    with locked_directory(directory.absolute()) as state:
        current = current_binding(state)
        bundle = archived_release(state, current["release_id"])
        manifest, release, checksum = inspect_bundle(bundle, current["bundle_sha256"])
        if (
            release != current["release_id"]
            or checksum != current["bundle_sha256"]
            or "modal-relay-v1" not in manifest.get("installation_features", [])
        ):
            raise InstallError("synchronize an installed Modal-enabled release before compute setup")
        config_path, plan_path, ownership = state / "setup.json", state / "plan.json", state / "ownership"
        config, document = read_document(config_path), read_document(plan_path)
        origin = operator_origin(config, document, current)
        compute_dir = state / "compute-setup"
        with locked_directory(compute_dir):
            save_release_catalog(bundle, manifest, compute_dir)
            value = collect(compute_dir, release)
            target_path = compute_dir / "target.json"
            if target_path.exists():
                target = read_document(target_path)
            else:
                completion = read_document(state / "setup-completion.json")
                target = {
                    "organization_id": ask("Existing organization ID", completion["organization_id"]),
                    "setup_id": secrets.token_hex(16),
                    "email": ask("Organization administrator email"),
                }
                if not re.fullmatch(r"[a-f0-9]{32}", target["organization_id"]):
                    raise InstallError("organization ID must be a 32-character identity")
                write_document(target_path, target, new=True)
            print("This installs credential-isolated Modal relays on your existing chat host. Programs run on Modal, not the chat VM.")
            print(
                f"Site: {current['public_url']}\nOrganization: {target['organization_id']}\nMaximum simultaneous jobs: {value['workers']}"
            )
            print(f"Monthly quota: {value['monthly_cpu_seconds']} logical CPU-seconds. This is not a dollar spending guarantee.")
            print("Set account spending limits in Modal. Jobs can become billable as soon as the relays are enabled.")
            verify(value)
            owner = {
                "setup_id": target["setup_id"],
                "email": target["email"],
                "password": getpass.getpass("Organization administrator password (not saved): "),
            }
            approve("Enable this exact compute policy and relay installation?", "enable compute " + current["public_url"])
            plan_id = document["plan_id"] if "plan_id" in document else read_document(state / "setup-completion.json")["plan_id"]
            cidr = temporary_ssh_cidr(detect_public_ipv4)
            try:
                config, opened = _ssh_action(
                    command, state, config_path, config, plan_path, plan_id, ownership, ["--open-cidr", cidr, "--wait-seconds", 60]
                )
                if opened.get("phase") != "open":
                    raise InstallError("temporary SSH access is unverified")
                result = install(
                    value,
                    {**config, "public_url": origin},
                    owner,
                    target["organization_id"],
                    compute_dir,
                    lambda payload, operation: remote(config, document, ownership, bundle, payload, operation),
                )
                write_document(compute_dir / "completion.json", result)
            finally:
                config, closed = close_ssh_with_token_recovery(
                    lambda arguments: _invoke(command, arguments),
                    state,
                    config_path,
                    config,
                    plan_path,
                    ownership,
                    plan_id,
                    document["plan"],
                )
                if closed.get("phase") != "closed":
                    raise InstallError("SSH closure is unverified; run make doctor with the same deployment state")
            print("Compute relays enabled. Use a gamma-topic for a small test run; live execution is not claimed until a job succeeds.")
            return {**result, "ssh_closed": True, "organization_id": target["organization_id"]}


def _invoke(command, arguments):
    from .setup_upgrade import _invoke as invoke

    return invoke(command, arguments)
