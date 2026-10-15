"""Exact, journaled worker VM deletion after fresh managed-host retirement."""

from __future__ import annotations

from pathlib import Path

from .digitalocean import DeletionRejected
from .host import InstallError, canonical, inspect_bundle
from .private import locked_directory, read_document, write_document
from .worker_host import WorkerHostInstaller, destruction_request, destruction_result
from .worker_provision import WorkerProvisioner

JOURNAL = "digitalocean-retirement.json"
ORDER = ("droplet", "firewall", "tag")


def owned_resource(instance, kind):
    response = instance.provider.get_owned(
        {"droplet": "droplets", "firewall": "firewalls", "tag": "tags"}[kind] + "/" + str(instance.state["resources"][kind]["id"])
    )
    matches = instance._collect(kind)
    if len(matches) > 1:
        raise InstallError("multiple resources share the deployment marker; preserve all resources")
    if response is None:
        if matches:
            raise InstallError("exact absence and marker discovery disagree; preserve ownership resources")
        return None
    resource = response.get(kind)
    instance._check(kind, resource)
    if len(matches) != 1 or matches[0] != resource:
        raise InstallError("exact owned identity and marker discovery disagree")
    return resource


def unused_tag(instance, tag):
    # Tag counts omit resource types without read scope. Prove visibility
    # with successful bounded reads before trusting the aggregate count.
    for path, field, parameters in (
        ("images", "images", {}),
        ("volumes", "volumes", {}),
        ("snapshots", "snapshots", {"resource_type": "volume"}),
        ("databases", "databases", {}),
    ):
        records = instance.provider.pages(path, field, **parameters)
        if any(isinstance(row.get("tags"), list) and instance.marker in row["tags"] for row in records):
            raise InstallError("ownership tag is attached to another resource; preserve the tag")
    resources = tag.get("resources")
    if not isinstance(resources, dict) or type(resources.get("count")) is not int or resources["count"] != 0:
        raise InstallError("ownership tag still has resources or its visibility is unverified")
    for key, value in resources.items():
        if key in {"count", "last_tagged_uri"}:
            continue
        if (
            key not in {"droplets", "images", "volume_snapshots", "volumes", "databases"}
            or not isinstance(value, dict)
            or type(value.get("count")) is not int
            or value["count"] != 0
        ):
            raise InstallError("ownership tag has another resource or unrecognized resource metadata")


class WorkerRetirer(WorkerProvisioner):
    def __init__(self, provider, document, config, images, bundle: Path):
        super().__init__(provider, document, config, images)
        manifest, release, checksum = inspect_bundle(bundle)
        self.binding = WorkerHostInstaller._binding(manifest, release, checksum, self.config, self.images)

    def retire(
        self,
        directory,
        *,
        confirm_plan,
        confirm_worker,
        confirm_release,
        confirm_droplet,
        confirm_delete_host_data,
        prepare_host,
        retry_delete=None,
    ):
        if (
            confirm_plan != self.identity
            or confirm_worker != self.config["id"]
            or confirm_release != self.binding["release_id"]
            or type(confirm_droplet) is not int
            or confirm_delete_host_data is not True
            or retry_delete not in (None, *ORDER)
        ):
            raise InstallError("confirm the exact plan, worker, release, Droplet, and destruction of all VM-local data")
        with locked_directory(directory) as directory:
            self.state = read_document(directory / "digitalocean.json")
            self._validate_state()
            if any(row["status"] != "created" for row in self.state["resources"].values()):
                raise InstallError("retirement requires the original complete provisioning ownership journal")
            if confirm_droplet != self.state["resources"]["droplet"]["id"]:
                raise InstallError("confirm-droplet must match the original owned VM ID")
            self.request = destruction_request(
                {
                    "format": 1,
                    "plan_id": self.identity,
                    "deployment_id": self.document["deployment_id"],
                    "droplet_id": confirm_droplet,
                    "delete_host_data": True,
                }
            )
            self.retirement_path = directory / JOURNAL
            self.intent = {
                "format": 1,
                "request": self.request,
                "binding": self.binding,
                "resources": {kind: self.state["resources"][kind]["id"] for kind in ORDER},
            }
            if self.retirement_path.exists() or self.retirement_path.is_symlink():
                self.retirement = read_document(self.retirement_path)
                self._validate_retirement()
            else:
                self.retirement = {
                    "intent": self.intent,
                    "proof": None,
                    "deletion": {kind: "not_started" for kind in ORDER},
                }
                # Locks and durable intent prevent apply from recreating resources.
                self._account()
                for kind in ORDER:
                    if self._resource(kind) is None:
                        raise InstallError("owned resource is missing before retirement; reconcile without adopting replacements")
                self._save(new=True)
            fresh = False
            for kind in ORDER:
                self._account()
                resource = self._resource(kind)
                status = self.retirement["deletion"][kind]
                if resource is None:
                    if kind == "droplet" and self.retirement["proof"] is None:
                        raise InstallError("VM absence without a sealed retirement proof is not verified retirement")
                    self.retirement["deletion"][kind] = "absent"
                    self._save()
                    if retry_delete == kind:
                        retry_delete = None
                    continue
                if status == "absent":
                    raise InstallError("a deleted owned identity has reappeared; no resource will be deleted")
                if status == "request_pending" and retry_delete != kind:
                    return self._retirement_result("deletion_pending", fresh)
                if retry_delete is not None and retry_delete != kind:
                    raise InstallError("retry-delete must name the current pending resource")
                if retry_delete == kind and status != "request_pending":
                    raise InstallError("explicit deletion retry requires a pending request")
                if kind == "droplet":
                    if resource.get("status") != "active":
                        raise InstallError("VM must be active for a fresh verified-host destruction check")
                    address = self._address(resource)
                    proof = destruction_result(prepare_host(address, self.request), self.binding, self.request)
                    if proof["retirement"]["phase"] == "draining":
                        return self._retirement_result("draining", False)
                    previous = self.retirement["proof"]
                    if previous is not None and previous != proof:
                        raise InstallError("fresh host retirement or daemon identity differs from the saved proof")
                    self.retirement["proof"] = proof
                    self._save()
                    fresh = True
                    self._account()
                    current = self._resource(kind)
                    if current is None or self._address(current) != address or current.get("status") != "active":
                        raise InstallError("VM disappeared or changed during host checks; resume read-only reconciliation")
                else:
                    if self._resource("droplet") is not None:
                        raise InstallError("do not delete network/ownership resources until the VM is observed absent")
                    if kind == "firewall" and not self._firewall_ready(resource):
                        return self._retirement_result("firewall_pending", fresh)
                    if kind == "tag":
                        self._tag_unused(resource)
                self._account()
                # Re-read exact ownership immediately before the durable DELETE intent.
                if self._resource(kind) != resource:
                    raise InstallError("provider resource changed before deletion; preserve it and resume inspection")
                self.retirement["deletion"][kind] = "request_pending"
                self._save()
                try:
                    self.provider.delete_owned(self._path(kind))
                except DeletionRejected:
                    self.retirement["deletion"][kind] = "not_started"
                    self._save()
                    raise
                # Acceptance is not disappearance. Failed/lost reads retain intent.
                self._account()
                if self._resource(kind) is not None:
                    return self._retirement_result("deletion_pending", fresh)
                self.retirement["deletion"][kind] = "absent"
                self._save()
                retry_delete = None
            if retry_delete is not None:
                raise InstallError("retry-delete has no pending resource")
            return self._retirement_result("deleted", fresh)

    def _account(self):
        if self.provider.identity() != self.plan["account_identity"]:
            raise InstallError("DigitalOcean account changed; no owned resource will be deleted")

    def _path(self, kind):
        collection = {"droplet": "droplets", "firewall": "firewalls", "tag": "tags"}[kind]
        return collection + "/" + str(self.state["resources"][kind]["id"])

    def _resource(self, kind):
        return owned_resource(self, kind)

    def _tag_unused(self, tag):
        unused_tag(self, tag)

    def _validate_retirement(self):
        try:
            value = self.retirement
            if (
                not isinstance(value, dict)
                or set(value) != {"intent", "proof", "deletion"}
                or canonical(value["intent"]) != canonical(self.intent)
            ):
                raise ValueError
            states = value["deletion"]
            if not isinstance(states, dict) or set(states) != set(ORDER):
                raise ValueError
            for index, kind in enumerate(ORDER):
                if states[kind] not in {"not_started", "request_pending", "absent"}:
                    raise ValueError
                if states[kind] != "not_started" and any(states[previous] != "absent" for previous in ORDER[:index]):
                    raise ValueError
            proof = value["proof"]
            if proof is not None:
                destruction_result(proof, self.binding, self.request)
                if proof["retirement"]["phase"] != "retired":
                    raise ValueError
            elif any(state != "not_started" for state in states.values()):
                raise ValueError
        except (KeyError, TypeError, ValueError) as exc:
            raise InstallError("invalid retirement journal or changed confirmations; preserve ownership state") from exc

    def _save(self, *, new=False):
        write_document(self.retirement_path, self.retirement, new=new)

    def _retirement_result(self, phase, fresh):
        return {
            "plan_id": self.identity,
            "worker_id": self.config["id"],
            "phase": phase,
            "resources": self.intent["resources"],
            "deletion": dict(self.retirement["deletion"]),
            "fresh_host_destruction_check": fresh,
            "cloud_resources_deleted": all(value == "absent" for value in self.retirement["deletion"].values()),
            "owned_vm_absence_observed": self.retirement["deletion"]["droplet"] == "absent",
            "retirement": self.retirement["proof"]["retirement"]["retirement"] if self.retirement["proof"] else None,
        }
