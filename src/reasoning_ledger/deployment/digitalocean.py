"""DigitalOcean planning and bounded, credential-private creation requests."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

import httpx

from .host import InstallError, public_origin
from .ssh_host import PIN_REQUIREMENT


class ProviderRejected(InstallError):
    """A creation request received a definitive client-error rejection."""


class ProviderUpdateRejected(InstallError):
    """An update request received a definitive client-error rejection."""


class DeletionRejected(InstallError):
    """An exact deletion request received a definitive client-error rejection."""


def dns_conflicts(records: list[dict], name: str) -> list[dict]:
    if any(not isinstance(row.get("name"), str) or not isinstance(row.get("type"), str) for row in records):
        raise InstallError("invalid DigitalOcean DNS record metadata")
    ancestors = {name.split(".", index)[-1] for index in range(1, len(name.split(".")))}
    return [
        row
        for row in records
        if (
            row.get("name") == name
            and row.get("type") in {"A", "AAAA", "CNAME"}
            or row.get("name") in ancestors
            and row.get("type") == "CNAME"
            or name != "@"
            and row.get("name") in ancestors | {name}
            and row.get("type") == "NS"
        )
    ]


def compute_selection(*, name, region, size, ssh_key_id, admin_cidr, maximum_hourly_usd):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", name) or not re.fullmatch(r"[a-z0-9]{2,12}", region):
        raise InstallError("invalid deployment name or region slug")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", size) or "gpu" in size or type(ssh_key_id) is not int or ssh_key_id <= 0:
        raise InstallError("select a CPU Droplet size and positive SSH-key ID")
    try:
        network = ipaddress.ip_network(admin_cidr, strict=True)
        if network.version != 4 or network.prefixlen == 0:
            raise ValueError
        maximum = Decimal(maximum_hourly_usd)
        if not maximum.is_finite() or not Decimal(0) < maximum <= Decimal(100):
            raise ValueError
    except (ValueError, InvalidOperation) as exc:
        raise InstallError("provide a bounded positive hourly budget and a restricted IPv4 administrator CIDR") from exc
    return network, maximum


def selection(*, name, region, size, origin, dns_zone, ssh_key_id, admin_cidr, maximum_hourly_usd):
    origin = public_origin(origin)
    hostname = urlsplit(origin).hostname
    network, maximum = compute_selection(
        name=name, region=region, size=size, ssh_key_id=ssh_key_id, admin_cidr=admin_cidr, maximum_hourly_usd=maximum_hourly_usd
    )
    if public_origin("https://" + dns_zone) != "https://" + dns_zone or not (hostname == dns_zone or hostname.endswith("." + dns_zone)):
        raise InstallError("DNS zone must contain the selected public hostname")
    return origin, hostname, network, maximum


class DigitalOcean:
    def __init__(self, token: str, *, transport: httpx.BaseTransport | None = None):
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", token):
            raise InstallError("invalid DigitalOcean token format")
        self.client = httpx.Client(
            base_url="https://api.digitalocean.com/v2/",
            timeout=10,
            headers={"Authorization": "Bearer " + token},
            trust_env=False,
            follow_redirects=False,
            transport=transport,
        )

    def close(self):
        self.client.close()

    def get(self, path: str, **params) -> dict:
        return self._request("GET", path, params=params)

    def post(self, path: str, body: dict) -> dict:
        if (path not in {"tags", "firewalls", "droplets", "domains", "account/keys"}
                and not re.fullmatch(r"domains/[a-z0-9.-]+/records", path)):
            raise InstallError("unsupported DigitalOcean creation endpoint")
        return self._request("POST", path, body=body)

    def update_firewall(self, identifier: str, body: dict) -> dict:
        if not re.fullmatch(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", identifier):
            raise InstallError("firewall update requires an exact owned firewall ID")
        return self._request("PUT", "firewalls/" + identifier, body=body)

    def domain(self, name: str) -> dict | None:
        if not isinstance(name, str) or public_origin("https://" + name) != "https://" + name:
            raise InstallError("invalid DigitalOcean DNS zone name")
        value = self._request("GET", "domains/" + name, missing=True)
        if value is None:
            return None
        domain = value.get("domain")
        if not isinstance(domain, dict) or domain.get("name") != name:
            raise InstallError("invalid DigitalOcean DNS zone metadata")
        return domain

    @staticmethod
    def _owned_endpoint(path: str):
        if not re.fullmatch(
            r"droplets/[1-9][0-9]{0,18}|firewalls/[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}|tags/reasoning-compute-[a-f0-9]{32}", path
        ):
            raise InstallError("deletion and absence checks require an exact owned worker resource endpoint")

    def get_owned(self, path: str) -> dict | None:
        self._owned_endpoint(path)
        return self._request("GET", path, missing=True)

    def delete_owned(self, path: str) -> None:
        self._owned_endpoint(path)
        self._request("DELETE", path)

    @staticmethod
    def _read_failure(path, status):
        # Report only fixed endpoint categories, not response bodies or IDs.
        permissions = (
            ("account/keys", "SSH key", "ssh_key:read"),
            ("account", "account", "account:read"),
            ("domains", "DNS", "domain:read"),
            ("firewalls", "firewall", "firewall:read"),
            ("droplets", "Droplet", "droplet:read"),
            ("tags", "tag", "tag:read"),
            ("sizes", "size", "sizes:read"),
        )
        if re.fullmatch(r"droplets/[1-9][0-9]*/firewalls", path):
            category, scope = "firewall association", "firewall:read, droplet:read, and tag:read"
        else:
            category, scope = next(
                ((category, scope) for prefix, category, scope in permissions if path == prefix or path.startswith(prefix + "/")),
                ("resource", "the endpoint's read"),
            )
        return InstallError(
            f"DigitalOcean {category} read failed (HTTP {status}); check {scope} permissions, "
            "token validity, Team access, and selected resources"
        )

    def _request(self, method: str, path: str, *, params=None, body=None, missing=False) -> dict | None:
        if not re.fullmatch(r"[a-z0-9/._-]+", path) or path.startswith("/") or ".." in path:
            raise InstallError("invalid DigitalOcean API path")
        try:
            with self.client.stream(method, path, params=params, json=body) as response:
                if method == "GET" and missing and response.status_code == 404:
                    return None
                if method == "DELETE":
                    if response.status_code in {400, 401, 403, 422, 429}:
                        raise DeletionRejected("DigitalOcean rejected deletion; preserve ownership and check permissions")
                    if response.status_code != 204 or any(response.iter_bytes(chunk_size=8192)):
                        raise InstallError("DigitalOcean deletion outcome is uncertain; retain the journal and reconcile reads")
                    return None
                if method == "POST" and response.status_code in {400, 401, 403, 404, 422, 429}:
                    raise ProviderRejected("DigitalOcean rejected creation; check token scopes, quota, and selected resources")
                if method == "PUT" and response.status_code in {400, 401, 403, 404, 422, 429}:
                    raise ProviderUpdateRejected("DigitalOcean rejected the firewall update; check firewall:update access")
                expected = {200} if method in {"GET", "PUT"} else {201, 202}
                if response.status_code not in expected:
                    if method == "POST":
                        raise InstallError("DigitalOcean creation outcome is uncertain; preserve the journal and resume reconciliation")
                    if method == "PUT":
                        raise InstallError("DigitalOcean firewall update is uncertain; preserve the journal and resume reconciliation")
                    raise self._read_failure(path, response.status_code)
                content = bytearray()
                for block in response.iter_bytes(chunk_size=8192):
                    content.extend(block)
                    if len(content) > 8 * 1024**2:
                        raise InstallError("DigitalOcean response exceeds its byte allowance")
                value = json.loads(content)
                if not isinstance(value, dict):
                    raise InstallError("invalid DigitalOcean response")
                return value
        except (httpx.HTTPError, ValueError) as exc:
            if method == "DELETE":
                raise InstallError("DigitalOcean deletion outcome is uncertain; retain the journal and reconcile reads") from exc
            if method == "POST":
                raise InstallError("DigitalOcean creation outcome is uncertain; preserve the journal and resume reconciliation") from exc
            if method == "PUT":
                raise InstallError("DigitalOcean firewall update is uncertain; preserve the journal and resume reconciliation") from exc
            raise InstallError("DigitalOcean read is unavailable or returned invalid data") from exc

    def identity(self) -> str:
        account = self.get("account").get("account")
        if not isinstance(account, dict) or account.get("status") != "active" or account.get("email_verified") is not True:
            raise InstallError("DigitalOcean account must be active with verified email")
        team = account.get("team")
        if team is not None and not isinstance(team, dict):
            raise InstallError("invalid DigitalOcean account identity")
        identifier = team.get("uuid") if team is not None else account.get("uuid")
        if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9-]{16,80}", identifier):
            raise InstallError("invalid DigitalOcean account identity")
        return ("team:" if team is not None else "user:") + identifier

    def pages(self, path: str, field: str, **params) -> list:
        rows = []
        for page in range(1, 51):
            body = self.get(path, page=page, per_page=200, **params)
            records = body.get(field)
            if not isinstance(records, list) or any(not isinstance(record, dict) for record in records):
                raise InstallError("invalid paginated DigitalOcean response")
            rows.extend(records)
            if len(rows) > 10000:
                raise InstallError("DigitalOcean list exceeds its item allowance")
            # Do not follow provider-supplied absolute next links with credentials.
            links = body.get("links", {})
            if not isinstance(links, dict) or not isinstance(links.get("pages", {}), dict):
                raise InstallError("invalid DigitalOcean pagination metadata")
            if not links.get("pages", {}).get("next"):
                return rows
        raise InstallError("DigitalOcean pagination exceeds its page allowance")

    def plan(
        self,
        *,
        name: str,
        region: str,
        size: str,
        origin: str,
        dns_zone: str,
        ssh_key_id: int,
        admin_cidr: str,
        maximum_hourly_usd: str,
        check_dns: bool = True,
        pin_ssh_host_key: bool = False,
    ) -> dict:
        if type(pin_ssh_host_key) is not bool:
            raise InstallError("SSH host-key initialization requires explicit boolean consent")
        origin, hostname, _, _ = selection(
            name=name,
            region=region,
            size=size,
            origin=origin,
            dns_zone=dns_zone,
            ssh_key_id=ssh_key_id,
            admin_cidr=admin_cidr,
            maximum_hourly_usd=maximum_hourly_usd,
        )
        common = self.compute_plan(
            name=name, region=region, size=size, ssh_key_id=ssh_key_id, admin_cidr=admin_cidr, maximum_hourly_usd=maximum_hourly_usd
        )
        zone = self.get("domains/" + dns_zone).get("domain", {})
        if not isinstance(zone, dict) or zone.get("name") != dns_zone:
            raise InstallError("DNS zone must already be managed by this DigitalOcean account")
        record_name = "@" if hostname == dns_zone else hostname[: -(len(dns_zone) + 1)]
        conflicts = dns_conflicts(self.pages(f"domains/{dns_zone}/records", "domain_records"), record_name)
        if check_dns and conflicts:
            raise InstallError("selected hostname already has address or alias records; choose an unused hostname")
        return {
            **common,
            "public_url": origin,
            "dns_zone": dns_zone,
            "dns_record_name": record_name,
            "requirements": [
                "delegate the DNS zone to DigitalOcean",
                "allow HTTPS/HTTP and restricted SSH",
                PIN_REQUIREMENT if pin_ssh_host_key else "verify the new host SSH key",
                "install a verified public release",
            ],
        }

    def compute_plan(self, *, name, region, size, ssh_key_id, admin_cidr, maximum_hourly_usd) -> dict:
        """Read account, size, and key metadata without requiring a DNS zone."""
        common = self.compute_quote(name=name, region=region, size=size, admin_cidr=admin_cidr,
                                    maximum_hourly_usd=maximum_hourly_usd)
        compute_selection(name=name, region=region, size=size, ssh_key_id=ssh_key_id,
                          admin_cidr=admin_cidr, maximum_hourly_usd=maximum_hourly_usd)
        key = self.get(f"account/keys/{ssh_key_id}").get("ssh_key", {})
        if (
            not isinstance(key, dict) or type(key.get("id")) is not int or key.get("id") != ssh_key_id
            or not isinstance(key.get("public_key"), str) or not 16 <= len(key["public_key"]) <= 16384
            or "\n" in key["public_key"] or "\r" in key["public_key"]
        ):
            raise InstallError("selected SSH public key is not available in this DigitalOcean account")
        return {**common, "ssh_key_id": ssh_key_id, "ssh_public_key_fingerprint": key.get("fingerprint"),
                "ssh_public_key_sha256": hashlib.sha256(key["public_key"].encode()).hexdigest()}

    def compute_quote(self, *, name, region, size, admin_cidr, maximum_hourly_usd) -> dict:
        """Read-only size/account quote before enrolling a new operator SSH key."""
        network, maximum = compute_selection(
            name=name, region=region, size=size, ssh_key_id=1, admin_cidr=admin_cidr, maximum_hourly_usd=maximum_hourly_usd
        )
        account_identity = self.identity()
        choices = [row for row in self.pages("sizes", "sizes") if row.get("slug") == size]
        if len(choices) != 1:
            raise InstallError("selected DigitalOcean size was not uniquely found")
        selected = choices[0]
        try:
            hourly = Decimal(str(selected["price_hourly"]))
            if (
                selected.get("available") is not True
                or not isinstance(selected.get("regions"), list)
                or not 1 <= len(selected["regions"]) <= 128
                or any(not isinstance(item, str) for item in selected["regions"])
                or region not in selected["regions"]
                or type(selected["memory"]) is not int
                or selected["memory"] < 2048
                or type(selected["vcpus"]) is not int
                or selected["vcpus"] < 1
                or type(selected["disk"]) is not int
                or selected["disk"] < 10
                or not hourly.is_finite()
                or hourly <= 0
                or hourly > maximum
            ):
                raise ValueError
        except (KeyError, TypeError, InvalidOperation, ValueError) as exc:
            raise InstallError("selected Droplet is unavailable, undersized, or above the hourly budget") from exc
        return {
            "provider": "digitalocean",
            "account_identity": account_identity,
            "name": name,
            "region": region,
            "size": size,
            "image": "ubuntu-24-04-x64",
            "admin_cidr": str(network),
            "resources": {"vcpus": selected["vcpus"], "memory_mib": selected["memory"], "disk_gib": selected["disk"]},
            "quote": {
                "compute_hourly_usd": str(hourly),
                "estimated_730_hours_usd": str(hourly * 730),
                "maximum_hourly_usd": str(maximum),
                "excluded": ["tax", "bandwidth overages", "backups", "storage", "other services"],
            },
            "creates_resources": False,
        }
