"""Terminal cancellation of enrolled cloud setup before any paid VM request."""

from __future__ import annotations

from pathlib import Path

from .digitalocean import DeletionRejected
from .host import InstallError, canonical
from .private import read_document, write_document
from .worker_retire import JOURNAL, owned_resource, unused_tag

CANCELLATION = "fleet-cancellation.json"
ORDER = ("firewall", "tag")
UNKNOWN = object()


def completed_cancellation(profile):
    """Local terminal shape for advisory inventory, not mutation authority.

    The caller must also freshly observe every owned resource absent. The
    budget coordinator independently validates the original reservation.
    """
    value = read_document(Path(profile.fleet_paths["state_dir"]) / CANCELLATION)
    try:
        intent = value["intent"]
        return (
            set(value) == {"intent", "owned", "deletion"}
            and set(intent) == {"format", "fleet_sha256", "worker_id", "plan_id", "deployment_id", "reservation_id"}
            and type(intent["format"]) is int
            and intent["format"] == 1
            and intent["fleet_sha256"] == profile.fleet_sha256
            and intent["worker_id"] == profile.config["id"]
            and intent["plan_id"] == profile.identity
            and intent["deployment_id"] == profile.document["deployment_id"]
            and isinstance(intent["reservation_id"], str)
            and len(intent["reservation_id"]) == 64
            and all(char in "0123456789abcdef" for char in intent["reservation_id"])
            and value["deletion"] == dict.fromkeys(ORDER, "absent")
            and value["owned"] == {kind: profile.state["resources"][kind]["id"] for kind in ORDER}
            and profile.state["resources"]["droplet"] == {"status": "not_started", "id": None}
            and all(profile.state["resources"][kind]["status"] != "request_pending" for kind in ORDER)
        )
    except (KeyError, TypeError, AttributeError):
        return False


class WorkerCancellation:
    """Caller holds both the original fleet and selected ownership locks."""

    def __init__(self, coordinator, profile, reservation):
        self.owner, self.profile = coordinator, profile
        self.directory = Path(profile.fleet_paths["state_dir"])
        self.path = self.directory / CANCELLATION
        self.intent = {
            "format": 1,
            "fleet_sha256": coordinator.origin(profile.config["id"]),
            "worker_id": profile.config["id"],
            "plan_id": profile.identity,
            "deployment_id": profile.document["deployment_id"],
            "reservation_id": reservation["reservation_id"],
        }
        self.state = None

    def _never_vm(self):
        profile = self.profile
        if profile.provider.identity() != profile.plan["account_identity"]:
            raise InstallError("DigitalOcean account changed; preserve cancellation ownership")
        profile.state = read_document(self.directory / "digitalocean.json")
        profile._validate_state()
        if (
            profile.state["resources"]["droplet"] != {"status": "not_started", "id": None}
            or (self.directory / JOURNAL).exists()
            or (self.directory / JOURNAL).is_symlink()
            or profile._collect("droplet")
        ):
            raise InstallError("cancellation requires no paid VM request or matching VM; use original reconciliation/retirement")

    def _load(self):
        value = read_document(self.path)
        try:
            if set(value) != {"intent", "owned", "deletion"} or canonical(value["intent"]) != canonical(self.intent):
                raise ValueError
            if set(value["owned"]) != set(ORDER) or set(value["deletion"]) != set(ORDER):
                raise ValueError
            for index, kind in enumerate(ORDER):
                identifier = value["owned"][kind]
                if identifier is not None:
                    self.profile._identifier(kind, identifier)
                status = value["deletion"][kind]
                if status not in {"not_started", "request_pending", "absent"}:
                    raise ValueError
                if status == "request_pending" and identifier is None:
                    raise ValueError
                if status == "absent" and identifier is None and self.profile.state["resources"][kind]["status"] != "not_started":
                    raise ValueError
                if status != "not_started" and any(value["deletion"][prior] != "absent" for prior in ORDER[:index]):
                    raise ValueError
        except (KeyError, TypeError, ValueError) as exc:
            raise InstallError("invalid cancellation journal or changed original intent; retain the reservation") from exc
        self.state = value

    def _save(self, *, new=False):
        write_document(self.path, self.state, new=new)

    def _free(self, kind):
        profile = self.profile
        row = profile.state["resources"][kind]
        saved = self.state["owned"][kind]
        if saved is not None:
            if row != {"status": "created", "id": saved}:
                raise InstallError("owned free resource changed during cancellation; preserve it")
            return owned_resource(profile, kind)
        matches = profile._collect(kind)
        if row["status"] == "not_started":
            if matches:
                raise InstallError("unmanaged free resource shares the marker; cancellation will not adopt it")
            return None
        if row["status"] == "request_pending":
            if not matches:
                return UNKNOWN
            if len(matches) != 1:
                raise InstallError("duplicate pending free resources; cancel neither")
            profile._check(kind, matches[0])
            identifier = matches[0].get("name") if kind == "tag" else matches[0].get("id")
            profile._identifier(kind, identifier)
            # Reconcile only the original free creation receipt. No new POST,
            # VM adoption, or deletion-by-marker is authorized.
            row.update(status="created", id=identifier)
            write_document(self.directory / "digitalocean.json", profile.state)
        self.state["owned"][kind] = row["id"]
        self._save()
        return owned_resource(profile, kind)

    def verified_absent(self):
        if not self.path.exists() and not self.path.is_symlink():
            return False
        self._load()
        if self.state["deletion"] != dict.fromkeys(ORDER, "absent"):
            return False
        self._never_vm()
        return all(self._free(kind) is None for kind in ORDER)

    def run(self, *, retry_delete=None):
        if retry_delete not in (None, *ORDER):
            raise InstallError("cancellation retry must name the original pending firewall or tag deletion")
        self._never_vm()
        if self.path.exists() or self.path.is_symlink():
            self._load()
        else:
            if retry_delete is not None:
                raise InstallError("explicit cancellation retry requires a saved pending deletion")
            self.state = {
                "intent": self.intent,
                "owned": {kind: self.profile.state["resources"][kind]["id"] for kind in ORDER},
                "deletion": dict.fromkeys(ORDER, "not_started"),
            }
            # Terminal intent is fsynced before any free-resource DELETE and
            # makes every provisioning path refuse new activation.
            self._save(new=True)
        for kind in ORDER:
            self._never_vm()
            resource = self._free(kind)
            status = self.state["deletion"][kind]
            if resource is UNKNOWN:
                return self._result("free_creation_uncertain")
            if resource is None:
                self.state["deletion"][kind] = "absent"
                self._save()
                if retry_delete == kind:
                    retry_delete = None
                continue
            if status == "absent":
                raise InstallError("a cancelled owned identity reappeared; do not delete it again")
            if status == "request_pending" and retry_delete != kind:
                return self._result("cancellation_pending")
            if retry_delete is not None and (retry_delete != kind or status != "request_pending"):
                raise InstallError("retry-delete must name the current pending cancellation deletion")
            if kind == "firewall" and not self.profile._firewall_ready(resource):
                return self._result("firewall_pending")
            if kind == "tag":
                if self.profile._collect("firewall"):
                    raise InstallError("free firewalls remain; preserve the ownership tag")
                unused_tag(self.profile, resource)
            self._never_vm()
            if self._free(kind) != resource:
                raise InstallError("free resource changed before cancellation deletion; preserve it")
            self.state["deletion"][kind] = "request_pending"
            self._save()
            collection = {"firewall": "firewalls", "tag": "tags"}[kind]
            try:
                self.profile.provider.delete_owned(collection + "/" + str(self.state["owned"][kind]))
            except DeletionRejected:
                self.state["deletion"][kind] = "not_started"
                self._save()
                raise
            self._never_vm()
            if self._free(kind) is not None:
                return self._result("cancellation_pending")
            self.state["deletion"][kind] = "absent"
            self._save()
            retry_delete = None
        if retry_delete is not None:
            raise InstallError("cancellation retry has no pending deletion")
        if not self.verified_absent():
            raise InstallError("cancelled free-resource absence is uncertain; retain the reservation")
        return self._result("cancelled")

    def _result(self, phase):
        return {
            "worker_id": self.profile.config["id"],
            "plan_id": self.profile.identity,
            "reservation_id": self.intent["reservation_id"],
            "phase": phase,
            "free_resources": dict(self.state["owned"]),
            "deletion": dict(self.state["deletion"]),
            "paid_vm_request_started": False,
            "cloud_vm_deleted": False,
            "worker_enrollment_revoked": False,
        }
