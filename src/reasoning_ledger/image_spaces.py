"""Guided, resumable public source delivery to DigitalOcean Spaces.

Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import base64
import getpass
import hashlib
import os
import re
import stat
import sys
import time
import uuid
from pathlib import Path

from rich.panel import Panel
from rich.progress import Progress
from rich.text import Text

from . import image_publication as publication
from .deployment.private import locked_directory, read_private, write_document, write_private
from .deployment.setup_auth import open_page
from .deployment.setup_ui import console
from .image_release import json_body
from .image_review import MAX_REVIEW, confirm, inventory
from .server.compute_catalog import canonical

PART_BYTES = 64 * 1024**2
REGIONS = ("ams3", "fra1", "nyc3", "sfo2", "sfo3", "sgp1", "syd1", "tor1", "blr1", "atl1", "lon1")
KEY_PAGE = "https://cloud.digitalocean.com/spaces/access_keys"
PRICING_PAGE = "https://docs.digitalocean.com/products/spaces/details/pricing/"


class SpacesError(ValueError):
    def __init__(self, operation, status=None):
        self.status = status
        suffix = f" (HTTP {status})" if type(status) is int and 100 <= status <= 599 else ""
        super().__init__(f"Spaces {operation} failed{suffix}; check key permissions and bucket region. "
                         "Preserve the private journal and retry the same inputs; provider details are suppressed.")


def request(client, operation, **arguments):
    """Never expose credential-bearing SDK errors or assume an uncertain write succeeded."""
    try:
        result = getattr(client, operation)(**arguments)
        if operation != "close" and not isinstance(result, dict):
            raise ValueError("unexpected provider response")
        return result
    except Exception as exc:
        response = getattr(exc, "response", {})
        metadata = response.get("ResponseMetadata", {}) if isinstance(response, dict) else {}
        status = metadata.get("HTTPStatusCode") if isinstance(metadata, dict) else None
        raise SpacesError(operation, status) from None


def head(client, operation, **arguments):
    try:
        return request(client, operation, **arguments)
    except SpacesError as exc:
        if exc.status == 404:
            return None
        raise


def client_for(region, access_key, secret_key):
    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        raise ValueError("Spaces publication needs the publication extra: uv sync --locked --extra publication") from None
    # Explicit endpoint/credentials; no instance metadata, environment credentials,
    # credential helper subprocesses, HTTP proxies or custom caller endpoints.
    try:
        return boto3.session.Session().client(
            "s3", region_name=region, endpoint_url=f"https://{region}.digitaloceanspaces.com",
            aws_access_key_id=access_key, aws_secret_access_key=secret_key, verify=True,
            config=Config(signature_version="s3v4", proxies={}, s3={"addressing_style": "virtual"},
                          connect_timeout=15, read_timeout=120,
                          retries={"mode": "standard", "total_max_attempts": 4},
                          request_checksum_calculation="when_required", response_checksum_validation="when_required"),
        )
    except Exception:
        raise ValueError("Spaces SDK initialization failed; check local SDK/configuration. Credential details suppressed.") from None


def credentials(path, *, password=None, browser=open_page, ask=input):
    if path is not None:
        value = json_body(read_private(path, 4096))
    else:
        answer = ask("Open DigitalOcean's Spaces access-key page? [Y/n]: ").strip().lower()
        if answer not in {"", "y", "yes", "n", "no"}:
            raise ValueError("answer yes or no; no credential was requested")
        if answer in {"", "y", "yes"}:
            browser(KEY_PAGE)
        console.print(Text("Existing bucket: use a bucket-limited Read/Write/Delete key.\n"
                           "Creating a bucket requires a full-access key; revoke it or switch to a limited key afterward.\n"
                           "Enter the Access Key ID first, then its matching Secret Key.\n"
                           "Spaces keys are separate from DigitalOcean API tokens. Keys stay in memory, not in the journal."))
        if password is None:
            if not sys.stdin.isatty():
                raise ValueError("hidden key entry requires a terminal; alternatively supply --credentials-file with mode 0600")
            password = getpass.getpass
        value = {"access_key": password("Spaces access key ID (hidden): "), "secret_key": password("Spaces secret key (hidden): ")}
    if (not isinstance(value, dict) or set(value) != {"access_key", "secret_key"}
            or not isinstance(value["access_key"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,256}", value["access_key"])
            or not isinstance(value["secret_key"], str) or not re.fullmatch(r"[A-Za-z0-9/+=_-]{16,512}", value["secret_key"])):
        raise ValueError("supply a valid Spaces access-key pair; secret values are not displayed")
    return value["access_key"], value["secret_key"]


def read_state(path):
    return json_body(read_private(path, MAX_REVIEW))


def save(path, value):
    write_private(path, canonical(value).encode(), maximum=MAX_REVIEW)


def destination(bucket, region):
    if (not isinstance(bucket, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", bucket)
            or re.fullmatch(r"\d+\.\d+\.\d+\.\d+", bucket) or region not in REGIONS):
        raise ValueError("choose a 3–63 character lowercase bucket name without dots and a listed Spaces region")


def upload(bundle, client, intent, directory, *, progress=None):
    """Own only this upload ID/key; never adopt unrelated multipart uploads or change bucket ACLs."""
    bucket, key = intent["bucket"], intent["key"]
    existing = head(client, "head_object", Bucket=bucket, Key=key)
    if existing is not None:
        # An uncertain completion is recovered by actual anonymous bytes, not
        # metadata, ETags, or editable completion flags. Never replace this object.
        publication.fetch_source(intent)
        return
    journal_path = directory / "multipart.json"
    if journal_path.exists() or journal_path.is_symlink():
        journal = read_state(journal_path)
        if (set(journal) != {"format", "bucket", "key", "upload_id", "parts"} or journal["format"] != 1
                or journal["bucket"] != bucket or journal["key"] != key
                or not isinstance(journal["upload_id"], str) or not 1 <= len(journal["upload_id"]) <= 2048
                or not isinstance(journal["parts"], dict) or len(journal["parts"]) > 1024):
            raise ValueError("multipart journal differs from this exact source destination")
    else:
        response = request(client, "create_multipart_upload", Bucket=bucket, Key=key, ACL="public-read",
                           ContentType="application/x-tar", Metadata={"sha256": intent["source_bundle_sha256"]})
        upload_id = response.get("UploadId")
        if not isinstance(upload_id, str) or not 1 <= len(upload_id) <= 2048:
            raise ValueError("Spaces returned no bounded upload identity; no public completion is claimed")
        journal = {"format": 1, "bucket": bucket, "key": key, "upload_id": upload_id, "parts": {}}
        save(journal_path, journal)
    arguments = {"Bucket": bucket, "Key": key, "UploadId": journal["upload_id"]}
    remote = request(client, "list_parts", **arguments, MaxParts=1000)
    if remote.get("IsTruncated") is not False or not isinstance(remote.get("Parts", []), list) or len(remote.get("Parts", [])) > 1024:
        raise ValueError("multipart listing is incomplete or exceeds the upload allowance; no completion was attempted")
    parts = remote.get("Parts", [])
    if any(not isinstance(part, dict) or type(part.get("PartNumber")) is not int or not 1 <= part["PartNumber"] <= 1024
           or type(part.get("Size")) is not int or not 1 <= part["Size"] <= PART_BYTES
           or not isinstance(part.get("ETag"), str) or not 1 <= len(part["ETag"]) <= 256 for part in parts):
        raise ValueError("multipart listing contains invalid part receipts; no completion attempted")
    remote_parts = {part["PartNumber"]: part for part in parts}
    if len(remote_parts) != len(remote.get("Parts", [])):
        raise ValueError("Spaces returned duplicated multipart identities")
    complete, hasher, count = [], hashlib.sha256(), 0
    began = time.monotonic()
    info = bundle.lstat()
    fd = os.open(bundle, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if (not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino)
                or opened.st_size != intent["source_bundle_bytes"]):
            raise ValueError("source archive changed before transfer")
        number = 0
        while body := stream.read(PART_BYTES):
            if time.monotonic() - began > 5400:
                raise ValueError("source transfer exceeds its 90 minute allowance; retain journal and retry")
            number += 1
            count += len(body)
            if count > intent["source_bundle_bytes"]:
                raise ValueError("source archive grew; incomplete upload retained, nothing completed")
            hasher.update(body)
            sha = hashlib.sha256(body).hexdigest()
            retained = journal["parts"].get(str(number))
            found = remote_parts.get(number, {})
            if (isinstance(retained, dict) and retained.get("sha256") == sha and retained.get("bytes") == len(body)
                    and retained.get("etag") == found.get("ETag") and found.get("Size") == len(body)):
                etag = retained["etag"]
            else:
                response = request(client, "upload_part", **arguments, PartNumber=number, Body=body,
                                   ContentMD5=base64.b64encode(hashlib.md5(body, usedforsecurity=False).digest()).decode())
                etag = response.get("ETag")
                if not isinstance(etag, str) or not 1 <= len(etag) <= 256:
                    raise ValueError("Spaces returned no bounded part receipt; retry this owned upload")
                journal["parts"][str(number)] = {"sha256": sha, "bytes": len(body), "etag": etag}
                write_document(journal_path, journal)
            complete.append({"ETag": etag, "PartNumber": number})
            if progress is not None:
                progress(len(body))
    if not complete or (hasher.hexdigest(), count) != (intent["source_bundle_sha256"], intent["source_bundle_bytes"]):
        raise ValueError("source archive changed during transfer; multipart upload retained, nothing completed")
    if time.monotonic() - began > 5400:
        raise ValueError("source transfer exceeds its 90 minute allowance; no completion attempted")
    if head(client, "head_object", Bucket=bucket, Key=key) is not None:
        publication.fetch_source(intent)
        return
    # The retained random key isolates concurrent publications. Existing keys
    # above are never overwritten; this is not a provider object-lock policy.
    request(client, "complete_multipart_upload", **arguments, MultipartUpload={"Parts": complete})
    publication.fetch_source(intent)


def publish_source(bundle=None, bucket=None, region=None, state=None, credentials_file=None,
                   *, ask=input, password=None, browser=open_page, factory=client_for):
    if bundle is None:
        name = ask("Source archive path (blank to pause): ").strip()
        if not name:
            raise ValueError("source publication paused; no cloud access was attempted")
        bundle = Path(name).expanduser()
    sha, size = publication.hash_file(bundle)
    files = inventory(bundle)
    if publication.hash_file(bundle) != (sha, size):
        raise ValueError("source bundle changed during inventory; no cloud access was attempted")
    state = state or Path.home() / ".reasoning-compute-publication"
    with locked_directory(state) as base:
        host_path = base / "spaces-host.json"
        host = read_state(host_path) if host_path.exists() or host_path.is_symlink() else {}
        if host and set(host) != {"bucket", "region"}:
            raise ValueError("retained source-host configuration is invalid")
        if host:
            destination(host["bucket"], host["region"])
        region = region or ask(f"Spaces region [{host.get('region', 'ams3')}]: ").strip() or host.get("region", "ams3")
        bucket = bucket or ask(f"Spaces bucket [{host.get('bucket', '')}]: ").strip() or host.get("bucket", "")
        destination(bucket, region)
        if not host:
            save(host_path, {"bucket": bucket, "region": region})
        with locked_directory(base / sha) as directory:
            intent_path = directory / "source-upload-intent.json"
            existing = read_state(intent_path) if intent_path.exists() or intent_path.is_symlink() else None
            key = existing.get("key") if existing else f"compute-sources/{sha}/{uuid.uuid4().hex}.tar"
            if not isinstance(key, str) or not re.fullmatch(r"compute-sources/" + sha + r"/[a-f0-9]{32}\.tar", key):
                raise ValueError("retained object key is not this operation's digest-namespaced publication")
            audit = {"source_bundle_url": f"https://{bucket}.{region}.digitaloceanspaces.com/{key}",
                     "source_bundle_sha256": sha, "source_bundle_bytes": size}
            intent = {"format": 1, **audit, "bucket": bucket, "region": region, "key": key, "files": files}
            if existing is not None and existing != intent:
                raise ValueError("source upload differs from retained exact bytes/bucket/region; use the original inputs")
            if existing is None:
                save(intent_path, intent)
            console.print(Panel(Text(f"Destination  {audit['source_bundle_url']}\n"
                                     f"SHA-256      {sha}\nBytes        {size:,} · {len(files):,} files\n"
                                     f"Inventory    {intent_path}\n\n"
                                     "Every byte of this archive will be public. Check for private code, credentials and datasets.\n"
                                     "Storage and public download bandwidth may incur Spaces charges.\n"
                                     f"Pricing: {PRICING_PAGE}\n"
                                     "No image, review approval or organization compute configuration is changed."),
                                title="Review public source delivery", border_style="yellow"))
            confirm("Publish or recheck these exact source bytes?", f"publish sources {sha[:12]}", ask=ask)
            receipt = directory / "source-upload-complete.json"
            if receipt.exists() or receipt.is_symlink():
                if read_state(receipt) != audit:
                    raise ValueError("completion receipt differs from this source intent")
                publication.fetch_source(audit)
                return audit
            access, secret = credentials(credentials_file, ask=ask, password=password, browser=browser)
            client = factory(region, access, secret)
            try:
                if head(client, "head_bucket", Bucket=bucket) is None:
                    console.print(Text("Creating the first bucket can start a $5/month Standard Spaces subscription.\n"
                                       "Additional storage and bandwidth are billable; this is not an invoice cap.\n"
                                       "The new bucket stays private; only the selected source object becomes public.", style="yellow"))
                    confirm("Create this paid bucket in your DigitalOcean account?", f"create source bucket {bucket}", ask=ask)
                    request(client, "create_bucket", Bucket=bucket, ACL="private")
                    if head(client, "head_bucket", Bucket=bucket) is None:
                        raise ValueError("new bucket creation remains unverified; preserve state and retry")
                with Progress(console=console) as display:
                    task = display.add_task("Source transfer / public verification", total=size)
                    upload(bundle, client, intent, directory, progress=lambda count: display.advance(task, count))
                save(receipt, audit)
            finally:
                request(client, "close")
    return audit
