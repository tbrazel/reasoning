"""Unique provider-initialized SSH identities, bound to original VM ownership."""

from __future__ import annotations

import ipaddress
from contextlib import contextmanager
from pathlib import Path

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .host import InstallError
from .private import protected_temporary_directory, read_document, write_document, write_private

JOURNAL = "digitalocean-ssh.json"
PUBLIC_JOURNAL = "digitalocean-ssh-public.json"
PIN_REQUIREMENT = "initialize a pinned SSH host key using provider user data"


def pinned(plan):
    return PIN_REQUIREMENT in plan["requirements"]


def validate(value, plan_id, deployment_id):
    try:
        if (
            not isinstance(value, dict)
            or set(value) != {"format", "plan_id", "deployment_id", "private_key", "public_key"}
            or type(value["format"]) is not int
            or value["format"] != 1
            or value["plan_id"] != plan_id
            or value["deployment_id"] != deployment_id
            or not isinstance(value["private_key"], str)
            or len(value["private_key"]) > 4096
        ):
            raise ValueError
        key = serialization.load_ssh_private_key(value["private_key"].encode("ascii"), password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError
        public = key.public_key().public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH).decode("ascii")
        if value["public_key"] != public:
            raise ValueError
        return value
    except (KeyError, TypeError, ValueError, UnicodeError, UnsupportedAlgorithm) as exc:
        raise InstallError("invalid original SSH identity; preserve the ownership journal, never replace its key") from exc


def host_identity(directory, plan_id, deployment_id, *, allow_new=False):
    """Caller retains the original provisioning lock through every use."""
    path = Path(directory) / JOURNAL
    if path.exists() or path.is_symlink():
        return validate(read_document(path), plan_id, deployment_id)
    if not allow_new:
        raise InstallError("original pinned SSH identity is missing; do not generate a replacement for a requested VM")
    key = Ed25519PrivateKey.generate()
    value = {
        "format": 1,
        "plan_id": plan_id,
        "deployment_id": deployment_id,
        "private_key": key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, serialization.NoEncryption()
        ).decode("ascii"),
        "public_key": key.public_key().public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH).decode("ascii"),
    }
    write_document(path, value, new=True)
    return value


def public_host_identity(directory, plan_id, deployment_id):
    path = Path(directory) / PUBLIC_JOURNAL
    if not path.exists() and not path.is_symlink():
        raise InstallError("delegated SSH host identity is missing; obtain a fresh handoff from an existing operator")
    try:
        value = read_document(path)
        if (
            set(value) != {"format", "plan_id", "deployment_id", "public_key"}
            or type(value["format"]) is not int
            or value["format"] != 1
            or value["plan_id"] != plan_id
            or value["deployment_id"] != deployment_id
            or not isinstance(value["public_key"], str)
            or len(value["public_key"]) > 4096
        ):
            raise ValueError
        key = serialization.load_ssh_public_key(value["public_key"].encode("ascii"))
        if not isinstance(key, Ed25519PublicKey):
            raise ValueError
        normalized = key.public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH).decode("ascii")
        if value["public_key"] != normalized:
            raise ValueError
        return value
    except (KeyError, TypeError, ValueError, UnicodeError, UnsupportedAlgorithm) as exc:
        raise InstallError("invalid delegated SSH host identity; obtain a fresh handoff from an existing operator") from exc


@contextmanager
def worker_trust(document, directory, address, request, known_hosts=None):
    """Use only the original key for pinned plans, never TOFU or keyscan.

    The owned installer/retirer checks the provider VM and invokes this context
    inside its ownership lock with the exact confirmed original VM request.
    """
    if not pinned(document["plan"]):
        if known_hosts is None:
            raise InstallError("this worker plan requires independently verified --known-hosts")
        yield known_hosts
        return
    from .worker_provision import worker_plan_identity

    plan_id = worker_plan_identity(document)
    with _owned_trust(document, directory, address, request, known_hosts, plan_id) as trusted:
        yield trusted


@contextmanager
def server_trust(document, directory, address, request, known_hosts=None):
    """Pin only the original organization server VM, under its ownership lock."""
    from .provision import plan_identity

    if not pinned(document["plan"]):
        if known_hosts is None:
            raise InstallError("this server plan requires independently verified --known-hosts")
        yield known_hosts
        return
    from .host import provisioning_request

    request = provisioning_request(request)
    with _owned_trust(document, directory, address, request, known_hosts, plan_identity(document)) as trusted:
        yield trusted


@contextmanager
def _owned_trust(document, directory, address, request, known_hosts, plan_id):
    try:
        if (
            set(request)
            not in (
                {"format", "plan_id", "deployment_id", "droplet_id"},
                {"format", "plan_id", "deployment_id", "droplet_id", "delete_host_data"},
            )
            or "delete_host_data" in request
            and request["delete_host_data"] is not True
            or type(request["format"]) is not int
            or request["format"] != 1
            or request["plan_id"] != plan_id
            or request["deployment_id"] != document["deployment_id"]
            or type(request["droplet_id"]) is not int
            or not 0 < request["droplet_id"] < 2**63
            or not ipaddress.IPv4Address(address).is_global
            or str(ipaddress.IPv4Address(address)) != address
        ):
            raise ValueError
        state = read_document(Path(directory) / "digitalocean.json")
        if state["plan_id"] != plan_id or state["resources"]["droplet"] != {"status": "created", "id": request["droplet_id"]}:
            raise ValueError
    except (KeyError, TypeError, ValueError) as exc:
        raise InstallError("pinned SSH trust requires the exact original owned VM and public IPv4 address") from exc
    private_path = Path(directory) / JOURNAL
    value = (
        host_identity(directory, plan_id, document["deployment_id"])
        if private_path.exists() or private_path.is_symlink()
        else public_host_identity(directory, plan_id, document["deployment_id"])
    )
    with protected_temporary_directory("reasoning-pinned-ssh-") as temporary:
        path = temporary / "known_hosts"
        write_private(path, (address + " " + value["public_key"] + "\n").encode("ascii"))
        yield path
