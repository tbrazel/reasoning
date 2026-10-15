"""Install an exact owned compute VM without creating or deleting cloud resources."""

from __future__ import annotations

from pathlib import Path

from .host import InstallError, canonical, inspect_bundle
from .private import locked_directory, read_document, write_document
from .worker_host import WorkerHostInstaller, installation_result, provisioning_request
from .worker_provision import WorkerProvisioner, verify_remote_binding
from .worker_retire import owned_resource

JOURNAL = "digitalocean-installation.json"


class WorkerInstaller(WorkerProvisioner):
    def __init__(self, provider, document, config, images, bundle: Path, *, identity_client=None):
        super().__init__(provider, document, config, images, identity_client=identity_client)
        manifest, release, checksum = inspect_bundle(bundle)
        self.binding = WorkerHostInstaller._binding(manifest, release, checksum, self.config, self.images)

    def install(self, directory, *, confirm_plan, confirm_worker, confirm_release, confirm_droplet, install_host):
        if (
            confirm_plan != self.identity
            or confirm_worker != self.config["id"]
            or confirm_release != self.binding["release_id"]
            or type(confirm_droplet) is not int
            or not 0 < confirm_droplet < 2**63
        ):
            raise InstallError("confirm the exact plan, worker, release, and owned Droplet before host changes")
        with locked_directory(directory) as directory:
            for name in ("digitalocean-retirement.json", "fleet-cancellation.json"):
                path = directory / name
                if path.exists() or path.is_symlink():
                    raise InstallError("retirement or cancellation intent exists; installation must not reactivate this worker")
            self.state = read_document(directory / "digitalocean.json")
            self._validate_state()
            if any(row["status"] != "created" for row in self.state["resources"].values()):
                raise InstallError("installation requires the original complete provisioning ownership journal")
            if confirm_droplet != self.state["resources"]["droplet"]["id"]:
                raise InstallError("confirm-droplet must match the original owned VM ID")
            request = provisioning_request(
                {
                    "format": 1,
                    "plan_id": self.identity,
                    "deployment_id": self.document["deployment_id"],
                    "droplet_id": confirm_droplet,
                }
            )
            address = self._ready_host()
            intent = {
                "format": 1,
                "binding": self.binding,
                "request": request,
                "resources": {kind: self.state["resources"][kind]["id"] for kind in self.kinds},
                "ssh_host": address,
            }
            path = directory / JOURNAL
            if path.exists() or path.is_symlink():
                state = read_document(path)
                self._validate_installation(state, intent)
            else:
                state = {"intent": intent, "phase": "installing", "receipt": None}
            verify_remote_binding(self.config, client=self.identity_client)
            # The exact target survives SSH errors, timeouts, and lost receipts.
            # Never replace provider ownership or infer success from an old receipt.
            write_document(path, {**state, "phase": "installing"}, new=not path.exists())
            receipt = installation_result(install_host(address, request), self.binding, request)
            if self._ready_host() != address:
                raise InstallError("owned VM address changed during installation; preserve the original installation intent")
            verify_remote_binding(self.config, client=self.identity_client)
            write_document(path, {"intent": intent, "phase": "installed", "receipt": receipt})
            result = self._result("installed", address=address)
            result.update(
                worker_installation_verified=True,
                fresh_installation_check=True,
                installation=receipt,
                next_step="verify a real containerized computation; service activation alone is not execution evidence",
            )
            return result

    def _ready_host(self):
        if self.provider.identity() != self.plan["account_identity"]:
            raise InstallError("DigitalOcean account changed; no worker host will be modified")
        resources = {kind: owned_resource(self, kind) for kind in self.kinds}
        if any(resource is None for resource in resources.values()):
            raise InstallError("owned resource is missing; installation cannot adopt or create a replacement")
        if resources["droplet"].get("status") != "active":
            raise InstallError("owned worker VM is not active; wait without changing the original ownership")
        if not self._firewall_ready(resources["firewall"]):
            raise InstallError("owned worker firewall is not ready; wait without changing the original ownership")
        applied = self.provider.pages(f"droplets/{resources['droplet']['id']}/firewalls", "firewalls")
        if len(applied) != 1 or applied[0].get("id") != resources["firewall"]["id"]:
            raise InstallError("owned worker firewall association is missing or changed")
        self._check("firewall", applied[0])
        if not self._firewall_ready(applied[0]):
            raise InstallError("owned worker firewall application is not ready")
        return self._address(resources["droplet"])

    def _validate_installation(self, state, intent):
        try:
            if (
                not isinstance(state, dict)
                or set(state) != {"intent", "phase", "receipt"}
                or canonical(state["intent"]) != canonical(intent)
                or state["phase"] not in {"installing", "installed"}
                or state["phase"] == "installed"
                and state["receipt"] is None
            ):
                raise ValueError
            if state["receipt"] is not None:
                installation_result(state["receipt"], self.binding, intent["request"])
        except (KeyError, TypeError, ValueError) as exc:
            raise InstallError("owned worker installation journal is invalid or belongs to different inputs") from exc
