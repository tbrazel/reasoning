"""Enrollment-bound DigitalOcean compute hosts without public chat ports or DNS."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from reasoning_ledger.server.compute_catalog import ComputeImage
from reasoning_ledger.server.compute_worker import WorkerClient, WorkerFault, verify_identity

from .host import InstallError, canonical, public_origin
from .provision import PLAN_KEYS, Provisioner, firewall_body, plan_identity, validate_compute_plan
from .ssh_host import PIN_REQUIREMENT, pinned
from .worker_host import inputs, limits

WORKER_KEYS = (PLAN_KEYS - {"public_url", "dns_zone", "dns_record_name"}) | {"purpose", "enrollment"}
BINDING_KEYS = {"server_url", "worker_id", "organization_id", "image_ids", "resources", "enrollment_sha256", "catalog_sha256"}
REQUIREMENTS = [
    "use separate dedicated compute hardware",
    "verify the new host SSH key",
    "install the confirmed release and private worker enrollment",
    "drain and verify execution cleanup before retiring hardware",
]


def enrollment_binding(config: dict, images: list) -> dict:
    return {
        "server_url": config["server_url"],
        "worker_id": config["id"],
        "organization_id": config["organization_id"],
        "image_ids": config["image_ids"],
        "resources": config["resources"],
        "enrollment_sha256": hashlib.sha256(canonical(config)).hexdigest(),
        "catalog_sha256": hashlib.sha256(canonical(images)).hexdigest(),
    }


def worker_inputs(origin: str, registration: dict, catalog: list) -> tuple[dict, list]:
    config, images = inputs(origin, registration, catalog)
    for record in images:
        try:
            image = ComputeImage.parse(record)
        except (ValueError, TypeError, KeyError) as exc:
            raise InstallError("invalid approved worker image") from exc
        if any(value > image.maximum_resources[key] for key, value in config["resources"].items()):
            raise InstallError("worker allowance must fit every enrolled image's approved maxima")
    return config, images


def verify_remote_binding(config: dict, *, client=None) -> None:
    own_client = client is None
    client = client or WorkerClient(config)
    try:
        verify_identity(config, client)
    except WorkerFault as exc:
        raise InstallError("worker enrollment is unavailable, revoked, or mismatched; no new cloud resources will be created") from exc
    finally:
        if own_client:
            client.close()


def validate_worker_plan(plan: dict) -> None:
    try:
        if not isinstance(plan, dict) or set(plan) != WORKER_KEYS or plan["purpose"] != "compute-worker":
            raise ValueError
        validate_compute_plan(plan)
        binding = plan["enrollment"]
        if not isinstance(binding, dict) or set(binding) != BINDING_KEYS:
            raise ValueError
        if (
            public_origin(binding["server_url"]) != binding["server_url"]
            or any(not re.fullmatch(r"[a-f0-9]{32}", binding[key]) for key in ("worker_id", "organization_id"))
            or any(not re.fullmatch(r"[a-f0-9]{64}", binding[key]) for key in ("enrollment_sha256", "catalog_sha256"))
        ):
            raise ValueError
        ids = binding["image_ids"]
        if (
            not isinstance(ids, list)
            or not 1 <= len(ids) <= 32
            or any(not isinstance(item, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", item) for item in ids)
            or len(set(ids)) != len(ids)
        ):
            raise ValueError
        allowance = limits(binding["resources"])
        if (
            plan["resources"]["vcpus"] < allowance["cpu"]
            or plan["resources"]["memory_mib"] * 1024**2 < allowance["memory_bytes"] + 256 * 1024**2
            or plan["requirements"] not in (REQUIREMENTS, [REQUIREMENTS[0], PIN_REQUIREMENT, *REQUIREMENTS[2:]])
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError) as exc:
        raise InstallError("invalid or undersized DigitalOcean worker plan") from exc


def worker_plan_identity(document: dict) -> str:
    return plan_identity(document, validator=validate_worker_plan)


def plan_worker(provider, config: dict, images: list, *, identity_client=None, pin_ssh_host_key=False, **parameters) -> dict:
    if type(pin_ssh_host_key) is not bool:
        raise InstallError("SSH host pinning must be explicitly enabled or disabled")
    config, images = worker_inputs(config["server_url"], config, images)
    verify_remote_binding(config, client=identity_client)
    plan = {
        **provider.compute_plan(**parameters),
        "purpose": "compute-worker",
        "enrollment": enrollment_binding(config, images),
        "requirements": [REQUIREMENTS[0], PIN_REQUIREMENT, *REQUIREMENTS[2:]] if pin_ssh_host_key else list(REQUIREMENTS),
    }
    validate_worker_plan(plan)
    return plan


class WorkerProvisioner(Provisioner):
    kinds = ("tag", "firewall", "droplet")
    identity_function = staticmethod(worker_plan_identity)

    def __init__(self, provider, document, config: dict, images: list, *, identity_client=None):
        super().__init__(provider, document)
        self.config, self.images = worker_inputs(config["server_url"], config, images)
        if enrollment_binding(self.config, self.images) != self.plan["enrollment"]:
            raise InstallError("worker inputs do not match the saved plan; preserve existing resource ownership")
        self.identity_client = identity_client
        self.fleet_lease = None
        self.fleet_directory = None
        self.marker = "reasoning-compute-" + document["deployment_id"]
        self.vm_name = "reasoning-compute-" + self.plan["name"][:8] + "-" + document["deployment_id"]

    def _current_plan(self):
        verify_remote_binding(self.config, client=self.identity_client)
        return self.provider.compute_plan(
            **{name: self.plan[name] for name in ("name", "region", "size", "ssh_key_id", "admin_cidr")},
            maximum_hourly_usd=self.plan["quote"]["maximum_hourly_usd"],
        )

    def _firewall_body(self):
        body = firewall_body(self.marker, self.plan["admin_cidr"])
        body["inbound_rules"] = body["inbound_rules"][:1]
        return body

    def _dns_preflight(self):
        # Workers use outbound HTTPS to the existing organization URL, not DNS.
        pass

    def _publish_dns(self, address):
        pass

    def _before_creation(self, kind):
        # Recheck authority immediately before each creation, including the
        # billable VM, not only before the first free tag/firewall requests.
        verify_remote_binding(self.config, client=self.identity_client)
        self._fleet_guard(creation=True)

    def _before_apply(self, directory):
        self.fleet_directory = Path(directory)
        cancellation = self.fleet_directory / "fleet-cancellation.json"
        if cancellation.exists() or cancellation.is_symlink():
            raise InstallError("terminal fleet cancellation exists; provisioning must not create or reactivate this slot")
        self._fleet_guard(creation=False)

    def _fleet_guard(self, *, creation):
        if self.fleet_directory is None:
            return
        marker = self.fleet_directory / "fleet-binding.json"
        if marker.exists() or marker.is_symlink():
            # Imported lazily: the coordinator also constructs provisioners.
            from .fleet import FleetLease

            if not isinstance(self.fleet_lease, FleetLease):
                raise InstallError("fleet-managed worker requires its locked budget coordinator; use do-worker-scale-apply")
            self.fleet_lease.check(self, creation=creation)
        elif self.fleet_lease is not None:
            raise InstallError("managed fleet binding is missing; preserve the budget and ownership journals")

    def _result(self, phase, *, address=None):
        if address and pinned(self.plan):
            phase = "ssh_key_pinned"
        return {
            "plan_id": self.identity,
            "purpose": "compute-worker",
            "phase": phase,
            "deployment_tag": self.marker,
            "resources": {kind: self.state["resources"][kind]["id"] for kind in self.kinds},
            "provider_charges_possible": self.state["resources"]["droplet"]["status"] != "not_started",
            "server_url": self.config["server_url"],
            "worker_id": self.config["id"],
            "organization_id": self.config["organization_id"],
            "ssh_host": address,
            "worker_installation_verified": False,
            "container_execution_verified": False,
            "control_panel_url": "https://cloud.digitalocean.com/droplets"
            + ("/" + str(self.state["resources"]["droplet"]["id"]) if self.state["resources"]["droplet"]["id"] is not None else ""),
            "next_step": "do-worker-install uses the original pinned SSH identity; preserve digitalocean-ssh.json with ownership state"
            if address and pinned(self.plan)
            else "verify SSH host key in the provider console, then do-worker-install with the original ownership and exact inputs"
            if address
            else "resume the same saved worker plan and journal; do not discard ownership state",
        }
