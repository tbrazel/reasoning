"""Explicit operator opt-in for web controller SSH egress, not inbound SSH.

Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
"""

from pathlib import Path
from urllib.parse import urlsplit

from reasoning_ledger.server.bot_client import bot_panel

from .digitalocean import DigitalOcean
from .host import InstallError
from .private import locked_directory, read_document, read_private, write_document
from .provision import Provisioner, plan_identity


def enable(provider, document, directory):
    instance = Provisioner(provider, document)
    with locked_directory(directory):
        if provider.identity() != instance.plan["account_identity"]:
            raise InstallError("DigitalOcean account differs from this owned chat deployment")
        instance.state = read_document(directory / "digitalocean.json")
        instance._validate_state()
        instance._load_ssh_access(directory)
        if any(instance.state["resources"][key]["status"] != "created" for key in ("tag", "firewall", "droplet")):
            raise InstallError("web controller access needs a verified existing chat server")
        firewall_id = instance.state["resources"]["firewall"]["id"]
        droplet_id = instance.state["resources"]["droplet"]["id"]
        instance._check("droplet", provider.get(f"droplets/{droplet_id}").get("droplet"))
        applied = provider.pages(f"droplets/{droplet_id}/firewalls", "firewalls")
        if len(applied) != 1 or applied[0].get("id") != firewall_id:
            raise InstallError("chat server firewall attachment changed; no update attempted")
        instance._check("firewall", applied[0])
        journal = directory / "digitalocean-bot-egress.json"
        if not instance.bot_controller_ssh:
            write_document(journal, {"format": 1, "plan_id": instance.identity, "firewall_id": firewall_id, "phase": "updating"})
            instance.bot_controller_ssh = True
            # Exact owned PUT, never a write loop. An uncertain result is
            # reconciled by the next explicit operator command's initial read.
            provider.update_firewall(firewall_id, instance._firewall_body())
        verified = provider.get("firewalls/" + firewall_id).get("firewall")
        instance._check("firewall", verified)
        if not instance.bot_controller_ssh or not instance._firewall_ready(verified):
            raise InstallError("controller SSH egress not ready; resume this same command")
        write_document(journal, {"format": 1, "plan_id": instance.identity, "firewall_id": firewall_id, "phase": "enabled"})
        return {"outbound_ssh_enabled": True, "inbound_ssh_changed": False, "firewall_id": firewall_id}


def wizard(state: Path):
    config = read_document(state / "setup.json")
    document = read_document(state / "plan.json")
    plan_identity(document)
    hostname = urlsplit(document["plan"]["public_url"]).hostname
    bot_panel("REASONING · ENABLE WEB CLOUD BOTS", [("Server", hostname),
        ("Change", "Allow outbound TCP 22 for the web bot installer"),
        ("Preserved", "Inbound SSH restrictions, HTTP/HTTPS, VM ownership and all other rules"),
        ("Permissions", "Same-Team account:read, droplet:read, tag:read, firewall:read/update")])
    expected = "enable cloud bots " + hostname
    if input(f"Type '{expected}' to confirm: ").strip() != expected:
        raise InstallError("web cloud bot access not confirmed")
    provider = DigitalOcean(read_private(Path(config["token_file"]), 4096).decode().strip())
    try:
        result = enable(provider, document, state / "ownership")
    finally:
        provider.close()
    bot_panel("WEB CLOUD BOTS ENABLED", [("Server", hostname), ("Inbound SSH", "Unchanged"),
        ("Next", "My bots → Create a cloud bot · DigitalOcean")])
    return result
