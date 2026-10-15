"""Private client-owned enrollment intent, recoverable after lost server replies."""

from __future__ import annotations

import hashlib
import re
import secrets
from pathlib import Path

import httpx

from reasoning_ledger.server.compute_catalog import ComputeImage, fits, resources

from .host import InstallError, canonical, public_origin
from .private import locked_directory, protected_parent, read_document, read_private, write_document, write_private
from .worker_host import decode_input
from .worker_provision import worker_inputs

INTENT = "enrollment-intent.json"
RECEIPT = "enrollment-receipt.json"


class ScalerClient:
    def __init__(self, origin, credential, *, transport=None):
        if public_origin(origin) != origin or not isinstance(credential, str) or not re.fullmatch(r"rns_[A-Za-z0-9_-]{43}", credential):
            raise InstallError("recoverable enrollment requires a public HTTPS origin and scoped scaler token")
        self.client = httpx.Client(
            base_url=origin,
            headers={"Authorization": "Bearer " + credential},
            trust_env=False,
            follow_redirects=False,
            timeout=10,
            transport=transport,
        )

    def close(self):
        self.client.close()

    def request(self, operation, body=None):
        if operation not in {"identity", "enrollments"} or (operation == "identity") != (body is None):
            raise InstallError("unsupported scoped enrollment operation")
        try:
            with self.client.stream("GET" if body is None else "POST", "/api/compute-scaler/" + operation, json=body) as response:
                if response.status_code != (200 if body is None else 201):
                    raise InstallError(
                        "scaler enrollment is unavailable, unauthorized, over its limits, or changed; preserve original intent"
                    )
                result = bytearray()
                for chunk in response.iter_bytes(chunk_size=8192):
                    result.extend(chunk)
                    if len(result) > 256 * 1024:
                        raise InstallError("scoped enrollment reply exceeds its byte allowance")
                return decode_input(result)
        except (httpx.HTTPError, ValueError, UnicodeError) as exc:
            raise InstallError(
                "scoped enrollment reply is uncertain; resume the original private intent without creating a replacement"
            ) from exc


def _parameters(origin, organization, name, image_ids, limits, registration, catalog):
    try:
        if (
            public_origin(origin) != origin
            or not isinstance(organization, str)
            or not re.fullmatch(r"[a-f0-9]{32}", organization)
            or not isinstance(name, str)
            or not name.strip()
            or len(name) > 200
            or "\x00" in name
            or not isinstance(image_ids, list)
            or not 1 <= len(image_ids) <= 32
            or any(not isinstance(item, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", item) for item in image_ids)
            or len(set(image_ids)) != len(image_ids)
            or registration == catalog
        ):
            raise ValueError
        name.encode("utf8")
        return {
            "server_url": origin,
            "organization_id": organization,
            "name": name,
            "image_ids": sorted(image_ids),
            "resources": resources(limits),
            "registration_output": str(registration),
            "catalog_output": str(catalog),
        }
    except (ValueError, TypeError, UnicodeError) as exc:
        raise InstallError("invalid original enrollment profile, organization, HTTPS origin, or distinct output paths") from exc


def _catalog(grant, parameters):
    try:
        if (
            not isinstance(grant, dict)
            or grant["enabled"] is not True
            or grant["revoked_at"] is not None
            or not re.fullmatch(r"[a-f0-9]{32}", grant["id"])
            or grant["organization_id"] != parameters["organization_id"]
            or grant["scopes"] != ["compute:snapshot", "workers:enroll"]
            or not isinstance(grant["images"], list)
            or len(grant["images"]) > 32
            or not set(parameters["image_ids"]) <= set(grant["image_ids"])
            or not fits(parameters["resources"], resources(grant["resources"]))
        ):
            raise ValueError
        images = {record["id"]: ComputeImage.parse(record).record() for record in grant["images"]}
        if len(images) != len(grant["images"]):
            raise ValueError
        selected = [images[identity] for identity in parameters["image_ids"]]
        if any(not fits(parameters["resources"], record["maximum_resources"]) for record in selected) or len(canonical(selected)) > 65536:
            raise ValueError
        return selected
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise InstallError("scaler identity/catalog does not authorize the exact organization and enrollment profile") from exc


def enroll_worker(client, directory, origin, organization, name, image_ids, limits, registration_output, catalog_output):
    directory = Path(directory).absolute()
    registration, catalog = Path(registration_output).absolute(), Path(catalog_output).absolute()
    parameters = _parameters(origin, organization, name, image_ids, limits, registration, catalog)
    with locked_directory(directory):
        for path in (registration, catalog):
            protected_parent(path)
            if path in {directory / INTENT, directory / RECEIPT, directory / "provision.lock"}:
                raise InstallError("enrollment output cannot overwrite its private intent, receipt, or lock")
        if any(
            (directory / name).exists() or (directory / name).is_symlink()
            for name in ("digitalocean.json", "fleet-budget.json", "fleet-binding.json")
        ):
            raise InstallError("use a separate enrollment directory, not a cloud ownership or budget journal")
        path = directory / INTENT
        if path.exists() or path.is_symlink():
            intent = read_document(path)
            try:
                if (
                    set(intent) != {"format", "parameters", "scaler_id", "request_id", "worker_token", "catalog"}
                    or type(intent["format"]) is not int
                    or intent["format"] != 1
                    or canonical(intent["parameters"]) != canonical(parameters)
                    or not re.fullmatch(r"[a-f0-9]{32}", intent["scaler_id"])
                    or not re.fullmatch(r"[a-f0-9]{32}", intent["request_id"])
                    or not re.fullmatch(r"rnw_[A-Za-z0-9_-]{43}", intent["worker_token"])
                ):
                    raise ValueError
            except (TypeError, ValueError, KeyError) as exc:
                raise InstallError("invalid or changed enrollment intent; preserve the original credential and request") from exc
        else:
            if (directory / RECEIPT).exists() or (directory / RECEIPT).is_symlink():
                raise InstallError("enrollment receipt exists without its original intent; do not create a replacement")
            if any(output.exists() or output.is_symlink() for output in (registration, catalog)):
                raise InstallError("enrollment outputs already exist without this intent; do not overwrite them")
            grant = client.request("identity")
            images = _catalog(grant, parameters)
            intent = {
                "format": 1,
                "parameters": parameters,
                "scaler_id": grant["id"],
                "request_id": secrets.token_hex(16),
                "worker_token": "rnw_" + secrets.token_urlsafe(32),
                "catalog": images,
            }
            # Neither the request nonce nor worker credential is lost when a
            # POST or receipt write fails. The server stores only the credential hash.
            write_document(path, intent, new=True)
        grant = client.request("identity")
        current_catalog = _catalog(grant, parameters)
        if grant["id"] != intent["scaler_id"] or canonical(current_catalog) != canonical(intent["catalog"]):
            raise InstallError("scaler identity or catalog changed; preserve original enrollment without replacement")
        reply = client.request(
            "enrollments",
            {
                "request_id": intent["request_id"],
                "worker_token": intent["worker_token"],
                **{key: parameters[key] for key in ("name", "image_ids", "resources")},
            },
        )
        try:
            if (
                not isinstance(reply, dict)
                or set(reply) != {"format", "scaler_id", "worker"}
                or type(reply["format"]) is not int
                or reply["format"] != 1
                or reply["scaler_id"] != intent["scaler_id"]
            ):
                raise ValueError
            record = reply["worker"]
            if (
                not isinstance(record, dict)
                or "token" in record
                or "token_hash" in record
                or record["organization_id"] != organization
                or record["name"] != name
            ):
                raise ValueError
            if record["image_ids"] != parameters["image_ids"] or canonical(record["resources"]) != canonical(parameters["resources"]):
                raise ValueError
            worker_inputs(origin, {**record, "token": intent["worker_token"]}, intent["catalog"])
        except (ValueError, TypeError, KeyError) as exc:
            raise InstallError("invalid enrollment receipt; retain the original intent and recover by the same request") from exc
        checksum = hashlib.sha256(canonical(intent)).hexdigest()
        receipt_path = directory / RECEIPT
        if receipt_path.exists() or receipt_path.is_symlink():
            receipt = read_document(receipt_path)
            if (
                set(receipt) != {"intent_sha256", "worker"}
                or receipt["intent_sha256"] != checksum
                or not isinstance(receipt["worker"], dict)
                or any(
                    canonical(receipt["worker"].get(key)) != canonical(record[key])
                    for key in ("id", "organization_id", "name", "image_ids", "resources", "created_at")
                )
            ):
                raise InstallError("saved enrollment receipt changed; preserve the original ownership")
            record = receipt["worker"]
            if "token" in record or "token_hash" in record:
                raise InstallError("saved enrollment receipt must contain metadata only")
            worker_inputs(origin, {**record, "token": intent["worker_token"]}, intent["catalog"])
        else:
            write_document(receipt_path, {"intent_sha256": checksum, "worker": record}, new=True)
        registration_value = {**record, "token": intent["worker_token"]}
        for output, value in ((registration, registration_value), (catalog, intent["catalog"])):
            if output.exists() or output.is_symlink():
                if read_private(output) != canonical(value):
                    raise InstallError("enrollment output differs from its original receipt; do not overwrite it")
            else:
                write_private(output, canonical(value))
        return {
            "worker_id": record["id"],
            "organization_id": organization,
            "scaler_id": intent["scaler_id"],
            "registration_file": str(registration),
            "catalog_file": str(catalog),
            "enrollment_state_dir": str(directory),
            "creates_cloud_resources": False,
        }
