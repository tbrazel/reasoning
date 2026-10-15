"""Guided private email configuration for an existing verified deployment.

Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import shlex
import subprocess
from pathlib import Path

from reasoning_ledger.server.mail import MailConfig

from .host import InstallError, canonical, inspect_bundle, installation_binding
from .mail_host import result as mail_result
from .mail_host import validate_request
from .private import locked_directory, protected_temporary_directory, read_document, read_private, write_document, write_private
from .release_state import archived_release, current_binding, operator_origin
from .setup import approve, ask, close_ssh_with_token_recovery, temporary_ssh_cidr
from .setup_upgrade import _invoke, _ssh_action


def collect_mail(directory, *, opener=None):
    path = directory / "mail.json"
    if path.exists() or path.is_symlink():
        return MailConfig.read(path).document()
    from .setup_auth import open_page

    opener = opener or open_page
    print("Verify your sending domain in Resend, then create a sending-only key scoped to that domain.")
    opener("https://resend.com/domains")
    sender = ask("Verified sender email address")
    opener("https://resend.com/api-keys")
    key_path = input("Private Resend key file path (blank to paste a hidden key): ").strip()
    key = (read_private(Path(key_path).expanduser().absolute(), 4096).decode().strip() if key_path
           else getpass.getpass("Resend sending-only API key (hidden): ").strip())
    value = MailConfig(sender, key, int(ask("Daily invitation and verification email limit", 100))).document()
    write_document(path, value, new=True)
    return value


def run_mail_setup(directory, command, *, collect=collect_mail, remote=None, detect_public_ipv4=None):
    """Caller synchronizes the operator; consent and credentials remain local."""
    remote = remote or owned_mail_remote
    with locked_directory(directory.absolute()) as state:
        current = current_binding(state)
        bundle = archived_release(state, current["release_id"])
        manifest, release, checksum = inspect_bundle(bundle, current["bundle_sha256"])
        if (release != current["release_id"] or checksum != current["bundle_sha256"]
                or "existing-mail-setup-v1" not in manifest.get("installation_features", [])):
            raise InstallError("run make upgrade before make email-setup; the installed release cannot safely enable existing-server mail")
        config_path, plan_path, ownership = state / "setup.json", state / "plan.json", state / "ownership"
        config, document = read_document(config_path), read_document(plan_path)
        operator_origin(config, document, current)
        from rich.panel import Panel
        from rich.text import Text

        from .setup_ui import console

        console.print(Panel.fit(Text("EMAIL SETUP\nPrivate sending credentials for your existing organization server."),
                                title=" REASONING ", border_style="bright_cyan", padding=(1, 3)))
        with locked_directory(state / "mail-setup") as private:
            mail = collect(private)
            binding = installation_binding(manifest, release, checksum, current["public_url"])
            mail_identity = {"installation": binding, "mail": {"input_sha256": hashlib.sha256(canonical(mail)).hexdigest()}}
            operation = private / release
            with locked_directory(operation):
                saved = operation / "intent.json"
                if saved.exists() or saved.is_symlink():
                    if read_document(saved) != mail_identity:
                        raise InstallError("email setup differs from its saved sender/key/limit or installation; preserve original inputs")
                else:
                    write_document(saved, mail_identity, new=True)
                print(f"Site: {current['public_url']}\nSender: {mail['sender']}\nDaily limit: {mail['daily_limit']}")
                print("The service restarts briefly. Once enabled, queued invitations and verification emails can be sent.")
                print("No test email is submitted. Domain verification and provider spending controls remain in Resend.")
                approve("Enable email sending with this exact private configuration?", "enable email " + current["public_url"])
                plan_id = document.get("plan_id") or read_document(state / "setup-completion.json")["plan_id"]
                cidr = temporary_ssh_cidr(detect_public_ipv4)
                try:
                    config, opened = _ssh_action(command, state, config_path, config, plan_path, plan_id, ownership,
                                                 ["--open-cidr", cidr, "--wait-seconds", 60])
                    if opened.get("phase") != "open":
                        raise InstallError("temporary SSH access is unverified")
                    receipt = remote(config, document, ownership, bundle, mail)
                    write_document(operation / "completion.json", receipt)
                finally:
                    config, closed = close_ssh_with_token_recovery(
                        lambda arguments: _invoke(command, arguments), state, config_path, config, plan_path,
                        ownership, plan_id, document["plan"])
                    if closed.get("phase") != "closed":
                        raise InstallError("SSH closure is unverified; run make doctor with the same deployment state")
                print("Mail worker is ready; inbound SSH is closed. Send invitations from organization settings.")
                return {**receipt, "ssh_closed": True}


def owned_mail_remote(config, document, directory, bundle, mail, *, command=None):
    """Use original provider ownership, release custody and independently pinned SSH trust."""
    from .cli import STAGE_SCRIPT, ssh_arguments
    from .digitalocean import DigitalOcean
    from .server_install import ServerInstaller
    from .ssh_host import server_trust

    operator_origin(config, document)
    provider = DigitalOcean(read_private(Path(config["token_file"]), 4096).decode().strip())
    try:
        installer = ServerInstaller(provider, document, bundle, existing_server=True)
        if "existing-mail-setup-v1" not in installer.manifest.get("installation_features", []):
            raise InstallError("upgrade the installed release before enabling email")
        with locked_directory(directory):
            installer.state = read_document(directory / "digitalocean.json")
            installer._validate_state()
            installer._load_ssh_access(directory)
            previous = read_document(directory / "digitalocean-installation.json")
            address = installer._ready_host()
            provisioning = {"format": 1, "plan_id": installer.identity, "deployment_id": document["deployment_id"],
                            "droplet_id": installer.state["resources"]["droplet"]["id"]}
            intent = {"format": 1, "binding": installer.binding, "request": provisioning, "ssh_host": address,
                      "resources": {kind: installer.state["resources"][kind]["id"] for kind in installer.kinds}}
            installer._validate_installation(previous, intent)
            if previous["phase"] != "installed":
                raise InstallError("email setup requires the original verified installed release")
            digest = hashlib.sha256(canonical({"installation": installer.binding, "provisioning": provisioning,
                                               "mail": {"input_sha256": hashlib.sha256(canonical(mail)).hexdigest()}})).hexdigest()
            value = validate_request({"format": 1, "setup_id": digest, "installation": installer.binding,
                                      "provisioning": provisioning, "mail": mail, "manifest": installer.manifest})
            with server_trust(document, directory, address, provisioning) as trusted:
                options = ssh_arguments(address, Path(config["identity"]), trusted)

                def run(arguments, *, capture=False):
                    if command is not None:
                        return command(arguments, capture=capture)
                    environment = {"PATH": os.defpath, "LANG": "C.UTF-8"}
                    if os.environ.get("SSH_AUTH_SOCK"):
                        environment["SSH_AUTH_SOCK"] = os.environ["SSH_AUTH_SOCK"]
                    completed = subprocess.run(arguments, env=environment, stdin=subprocess.DEVNULL,
                                               stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                                               stderr=subprocess.DEVNULL, timeout=1200, check=False)
                    if completed.returncode or capture and len(completed.stdout) > 65536:
                        raise InstallError("email SSH setup is unverified; preserve exact private inputs and resume make email-setup")
                    return completed.stdout if capture else None

                destination = "/var/lib/reasoning-deploy/staging/" + digest
                run(["ssh", *options, "root@" + address, shlex.join(["python3", "-c", STAGE_SCRIPT, destination])])
                with protected_temporary_directory("reasoning-mail-install-") as temporary:
                    path = temporary / "mail-input.json"
                    write_private(path, canonical(value))
                    run(["scp", *options, str(path), "root@" + address + ":" + destination + "/mail-input.json"])
                executable = "/opt/reasoning/releases/" + installer.binding["release_id"] + "/venv/bin/python"
                output = run(["ssh", *options, "root@" + address, shlex.join([executable, "-m", "reasoning_ledger.deployment.mail_host",
                              "--input", destination + "/mail-input.json"])], capture=True)
                if installer._ready_host() != address:
                    raise InstallError("owned email setup host changed during installation")
                try:
                    receipt = json.loads(output)
                except (TypeError, ValueError):
                    raise InstallError("invalid private email setup receipt") from None
                if receipt != mail_result(value):
                    raise InstallError("email setup did not return an exact secret-free readiness receipt")
                return receipt
    finally:
        provider.close()
