"""Confirmed DNS-zone preparation and bounded public nameserver observation."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from .digitalocean import DigitalOcean, ProviderRejected
from .host import InstallError, public_origin
from .private import locked_directory, read_document, write_document

NAMESERVERS = ["ns1.digitalocean.com", "ns2.digitalocean.com", "ns3.digitalocean.com"]


def zone_name(value: str) -> str:
    if not isinstance(value, str) or public_origin("https://" + value) != "https://" + value:
        raise InstallError("DNS zone requires a lowercase fully qualified domain name")
    return value


def setup_zone(provider: DigitalOcean, zone: str, directory: Path, confirmed_zone: str) -> dict:
    zone = zone_name(zone)
    if confirmed_zone != zone:
        raise InstallError("confirm the exact DNS zone before provider access or creation")
    with locked_directory(directory) as state:
        account = provider.identity()
        path = state / "digitalocean-dns.json"
        if path.exists() or path.is_symlink():
            journal = read_document(path)
            if (
                set(journal) != {"format", "account_identity", "zone", "phase"}
                or type(journal["format"]) is not int
                or journal["format"] != 1
                or journal["account_identity"] != account
                or journal["zone"] != zone
                or journal["phase"] not in {"creating", "rejected", "ready"}
            ):
                raise InstallError("DNS setup journal does not match the account and zone; preserve it")
        else:
            journal = {"format": 1, "account_identity": account, "zone": zone, "phase": "rejected"}
        existing = provider.domain(zone)
        if existing is None:
            if journal["phase"] in {"creating", "ready"}:
                raise InstallError("DNS zone is absent after a recorded request; preserve the journal and reconcile, do not retry creation")
            journal["phase"] = "creating"
            write_document(path, journal, new=not path.exists())
            try:
                # No ip_address: preparing a zone must not create an apex A record.
                provider.post("domains", {"name": zone})
            except ProviderRejected:
                journal["phase"] = "rejected"
                write_document(path, journal)
                raise
            existing = provider.domain(zone)
        if not isinstance(existing, dict) or existing.get("name") != zone:
            raise InstallError("DNS zone readiness is unverified; preserve the journal and resume observation")
        journal["phase"] = "ready"
        write_document(path, journal, new=not path.exists())
        return {
            "zone": zone,
            "account_identity": account,
            "zone_ready": True,
            "nameservers": list(NAMESERVERS),
            "existing_records_modified": False,
            "registrar_modified": False,
            "delegation_verified": False,
            "next_step": "delegate this zone at its registrar, preserving existing DNS records, then run do-dns-check",
        }


def check_delegation(zone: str, *, query=None) -> dict:
    zone = zone_name(zone)
    executable = shutil.which("dig")
    if executable is None:
        raise InstallError("DNS checks require dig (Ubuntu: dnsutils; macOS: system dig)")
    try:
        result = (query or subprocess.run)(
            [executable, "+time=3", "+tries=1", "+noall", "+answer", "+noshort", "NS", zone + "."],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise InstallError("public DNS lookup is unavailable; delegation is unverified") from exc
    if result.returncode or not isinstance(result.stdout, str) or len(result.stdout) > 16384:
        raise InstallError("public DNS lookup failed; delegation is unverified")
    observed = set()
    for line in result.stdout.splitlines():
        if not line.strip() or line.startswith(";"):
            continue
        fields = line.split()
        if (
            len(fields) != 5
            or fields[0].lower() != zone + "."
            or not fields[1].isdigit()
            or fields[2:4] != ["IN", "NS"]
            or not re.fullmatch(r"[A-Za-z0-9.-]{1,253}\.", fields[4])
        ):
            raise InstallError("public DNS returned an unexpected nameserver answer; delegation is unverified")
        observed.add(fields[4].lower().removesuffix("."))
    verified = observed == set(NAMESERVERS)
    return {
        "zone": zone,
        "nameservers": list(NAMESERVERS),
        "observed_nameservers": sorted(observed),
        "delegation_verified": verified,
        "verification_scope": "current system recursive resolver; not global propagation or DNSSEC validation",
        "next_step": (
            "run do-plan for an unused hostname" if verified
            else "check registrar delegation and DNS propagation, then repeat do-dns-check"
        ),
    }
