"""Install the original organization VM and verify fresh public HTTPS readiness."""

from __future__ import annotations

from .host import (
    InstallError,
    canonical,
    inspect_bundle,
    installation_binding,
    installation_result,
    mail_result,
    owner_result,
    provisioning_request,
    ready,
    require_mail_release,
)
from .private import locked_directory, read_document, write_document
from .provision import Provisioner

JOURNAL = "digitalocean-installation.json"
LAUNCH_JOURNAL = "digitalocean-launch.json"
OWNER_JOURNAL = "digitalocean-owner.json"
MAIL_JOURNAL = "digitalocean-mail.json"


class ServerInstaller(Provisioner):
    def __init__(self, provider, document, bundle, *, ssh_cidr=None, existing_server=False):
        if type(existing_server) is not bool:
            raise InstallError("existing-server verification requires an explicit boolean")
        self.existing_server = existing_server
        super().__init__(provider, document, ssh_cidr=ssh_cidr)
        manifest, release, checksum = inspect_bundle(bundle)
        self.manifest = manifest
        self.binding = installation_binding(manifest, release, checksum, self.plan["public_url"])

    def _forbidden_droplet_features(self):
        # Existing-provider backups do not change the owned host or network.
        # IPv6 and all other resource/firewall checks remain fenced.
        return {"ipv6"} if self.existing_server else super()._forbidden_droplet_features()

    def prepare_launch(self, directory, *, owner=None, mail=None):
        """Bind the release before any paid request, including pending resumption."""
        with locked_directory(directory) as directory:
            retirement = directory / "digitalocean-retirement.json"
            if retirement.exists() or retirement.is_symlink():
                raise InstallError("retirement intent exists; launch must not recreate or reactivate this server")
            self._claim_mail(directory, mail)
            if owner is not None:
                self._claim_owner(directory, owner)
            path = directory / LAUNCH_JOURNAL
            intent = {"format": 1, "plan_id": self.identity, "binding": self.binding}
            if path.exists() or path.is_symlink():
                if canonical(read_document(path)) != canonical(intent):
                    raise InstallError("server launch requires its original plan, release, and URL; preserve the launch journal")
            else:
                installation = directory / JOURNAL
                if installation.exists() or installation.is_symlink():
                    state = read_document(installation)
                    if not isinstance(state.get("intent"), dict) or canonical(state["intent"].get("binding")) != canonical(self.binding):
                        raise InstallError("an existing server installation requires an explicit upgrade, not a new launch")
                write_document(path, intent, new=True)

    def install(self, directory, *, confirm_plan, confirm_release, confirm_droplet, install_host, check_https=None, owner=None, mail=None):
        if (
            confirm_plan != self.identity
            or confirm_release != self.binding["release_id"]
            or type(confirm_droplet) is not int
            or not 0 < confirm_droplet < 2**63
        ):
            raise InstallError("confirm the exact server plan, release, and owned Droplet before host changes")
        with locked_directory(directory) as directory:
            retirement = directory / "digitalocean-retirement.json"
            if retirement.exists() or retirement.is_symlink():
                raise InstallError("retirement intent exists; installation must not reactivate this server")
            self._claim_mail(directory, mail)
            if owner is not None:
                self._claim_owner(directory, owner)
            launch = directory / LAUNCH_JOURNAL
            if launch.exists() or launch.is_symlink():
                expected = {"format": 1, "plan_id": self.identity, "binding": self.binding}
                if canonical(read_document(launch)) != canonical(expected):
                    raise InstallError("server installation cannot replace the original confirmed launch release or URL")
            self.state = read_document(directory / "digitalocean.json")
            self._validate_state()
            self._load_ssh_access(directory, initialize=self.session_ssh_cidr)
            if any(row["status"] != "created" for row in self.state["resources"].values()):
                raise InstallError("server installation requires the original complete provisioning ownership journal")
            if confirm_droplet != self.state["resources"]["droplet"]["id"]:
                raise InstallError("confirm-droplet must match the original owned server VM ID")
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
                "ssh_host": address,
                "resources": {kind: self.state["resources"][kind]["id"] for kind in self.kinds},
            }
            path = directory / JOURNAL
            if path.exists() or path.is_symlink():
                previous = read_document(path)
                self._validate_installation(previous, intent)
            else:
                previous = {"intent": intent, "phase": "installing", "receipt": None}
            write_document(path, {**previous, "phase": "installing"}, new=not path.exists())
            receipt = installation_result(install_host(address, request), self.binding, request, owner, mail)
            if self._ready_host() != address:
                raise InstallError("owned server VM address changed during installation; preserve the original intent")
            status = (
                check_https(self.plan["public_url"])
                if check_https
                else ready(self.plan["public_url"], **({"require_mail": True} if mail is not None else {}))
            )
            if (
                not isinstance(status, dict)
                or set(status) != {"public_url", "https_ready", "initialized"}
                or status["public_url"] != self.plan["public_url"]
                or status["https_ready"] is not True
                or type(status["initialized"]) is not bool
                or owner is not None
                and status["initialized"] is not True
            ):
                raise InstallError("server public HTTPS readiness remains unverified")
            if self._ready_host() != address:
                raise InstallError("owned server VM address changed during HTTPS verification")
            core_receipt = {key: value for key, value in receipt.items() if key not in {"owner_setup", "mail_setup"}}
            write_document(path, {"intent": intent, "phase": "installed", "receipt": core_receipt})
            if owner is not None:
                write_document(
                    directory / OWNER_JOURNAL,
                    {
                        "format": 1,
                        "plan_id": self.identity,
                        "binding": owner,
                        "phase": "initialized",
                        "receipt": receipt["owner_setup"],
                    },
                )
            if mail is not None:
                write_document(
                    directory / MAIL_JOURNAL,
                    {"format": 1, "plan_id": self.identity, "binding": mail, "phase": "configured", "receipt": receipt["mail_setup"]},
                )
            return {
                **self._result("installed", address=address),
                **status,
                "application_installed": True,
                "fresh_installation_check": True,
                "server_installation_verified": True,
                "installation": receipt,
                **({"owner_setup": receipt["owner_setup"]} if owner is not None else {}),
                **({"mail_setup": receipt["mail_setup"]} if mail is not None else {}),
                "next_step": "sign in at the HTTPS URL with the initial owner account, then invite members and configure provider keys"
                if owner is not None
                else "create the initial owner using the private host setup token; organization onboarding remains separate",
            }

    def _claim_mail(self, directory, binding):
        import re

        path = directory / MAIL_JOURNAL
        exists = path.exists() or path.is_symlink()
        if binding is None:
            if exists:
                raise InstallError("repeat the original --mail-file input; preserve the mail ownership journal")
            return
        require_mail_release(self.manifest)
        try:
            if (
                not isinstance(binding, dict)
                or set(binding) != {"input_sha256"}
                or not re.fullmatch(r"[a-f0-9]{64}", binding["input_sha256"])
            ):
                raise ValueError
            if exists:
                state = read_document(path)
                if (
                    set(state) != {"format", "plan_id", "binding", "phase", "receipt"}
                    or type(state["format"]) is not int
                    or state["format"] != 1
                    or state["plan_id"] != self.identity
                    or canonical(state["binding"]) != canonical(binding)
                    or state["phase"] not in {"pending", "configured"}
                    or state["phase"] == "configured"
                    and state["receipt"] is None
                ):
                    raise ValueError
                if state["receipt"] is not None:
                    mail_result(state["receipt"], binding)
            else:
                write_document(
                    path, {"format": 1, "plan_id": self.identity, "binding": binding, "phase": "pending", "receipt": None}, new=True
                )
        except (KeyError, TypeError, ValueError) as exc:
            raise InstallError("mail setup requires its original exact private input and ownership journal") from exc

    def _claim_owner(self, directory, binding):
        import re

        try:
            if (
                not isinstance(binding, dict)
                or set(binding) != {"setup_id", "input_sha256"}
                or not re.fullmatch(r"[a-f0-9]{32}", binding["setup_id"])
                or not re.fullmatch(r"[a-f0-9]{64}", binding["input_sha256"])
            ):
                raise ValueError
            path = directory / OWNER_JOURNAL
            if path.exists() or path.is_symlink():
                state = read_document(path)
                if (
                    set(state) != {"format", "plan_id", "binding", "phase", "receipt"}
                    or type(state["format"]) is not int
                    or state["format"] != 1
                    or state["plan_id"] != self.identity
                    or canonical(state["binding"]) != canonical(binding)
                    or state["phase"] not in {"pending", "initialized"}
                    or state["phase"] == "initialized"
                    and state["receipt"] is None
                ):
                    raise ValueError
                if state["receipt"] is not None:
                    owner_result(state["receipt"], binding)
            else:
                write_document(
                    path, {"format": 1, "plan_id": self.identity, "binding": binding, "phase": "pending", "receipt": None}, new=True
                )
        except (KeyError, TypeError, ValueError) as exc:
            raise InstallError("owner setup requires its original exact private input and ownership journal") from exc

    def _owned(self, kind, *, address=None):
        paths = {"tag": "tags", "firewall": "firewalls", "droplet": "droplets", "dns": f"domains/{self.plan['dns_zone']}/records"}
        fields = {"tag": "tag", "firewall": "firewall", "droplet": "droplet", "dns": "domain_record"}
        # These are read-only server inspections. Worker deletion/absence
        # endpoints deliberately do not authorize server DNS/tag deletion.
        response = self.provider.get(paths[kind] + "/" + str(self.state["resources"][kind]["id"]))
        matches = self._collect(kind)
        if not isinstance(response, dict) or len(matches) != 1:
            raise InstallError("owned server resource is absent, uncertain, or duplicated; installation cannot replace it")
        resource = response.get(fields[kind])
        self._check(kind, resource, address=address)
        if resource != matches[0]:
            raise InstallError("owned server resource and discovery disagree; preserve original ownership")
        return resource

    def _ready_host(self):
        if self.provider.identity() != self.plan["account_identity"]:
            raise InstallError("DigitalOcean account changed; no server host will be modified")
        zone = self.provider.get("domains/" + self.plan["dns_zone"]).get("domain")
        if not isinstance(zone, dict) or zone.get("name") != self.plan["dns_zone"]:
            raise InstallError("owned server DNS zone is unavailable or changed")
        self._owned("tag")
        firewall, droplet = self._owned("firewall"), self._owned("droplet")
        if droplet.get("status") != "active" or not self._firewall_ready(firewall):
            raise InstallError("owned server VM or firewall is not ready; retain the original ownership")
        applied = self.provider.pages(f"droplets/{droplet['id']}/firewalls", "firewalls")
        if len(applied) != 1 or applied[0].get("id") != firewall["id"]:
            raise InstallError("owned server firewall association is missing or changed")
        self._check("firewall", applied[0])
        if not self._firewall_ready(applied[0]):
            raise InstallError("owned server firewall application is not ready")
        address = self._address(droplet)
        self._owned("dns", address=address)
        return address

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
            raise InstallError("owned server installation journal is invalid or belongs to different inputs") from exc
