"""Offline Spaces source-publication safety and resumable upload checks.

Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
"""

import base64
import hashlib
import io
import json
import subprocess
import tarfile
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from reasoning_ledger import image_review as review
from reasoning_ledger import image_spaces as spaces
from reasoning_ledger.deployment.host import InstallError

ACCESS = "fixture-access-key"
SECRET = "fixture-secret-key-not-real"


class ProviderError(Exception):
    def __init__(self, status):
        self.response = {"ResponseMetadata": {"HTTPStatusCode": status}}
        super().__init__(SECRET + " credential-bearing synthetic response")


class FakeSpaces:
    def __init__(self):
        self.bucket = True
        self.objects, self.parts, self.calls = {}, {}, []
        self.fail_part = None
        self.uncertain_complete = False
        self.truncated = False

    def head_bucket(self, **kwargs):
        self.calls.append(("head_bucket", kwargs))
        if not self.bucket:
            raise ProviderError(404)
        return {}

    def create_bucket(self, **kwargs):
        self.calls.append(("create_bucket", kwargs))
        assert kwargs["ACL"] == "private"
        self.bucket = True
        return {}

    def head_object(self, **kwargs):
        self.calls.append(("head_object", kwargs))
        if kwargs["Key"] not in self.objects:
            raise ProviderError(404)
        return {"ContentLength": len(self.objects[kwargs["Key"]])}

    def create_multipart_upload(self, **kwargs):
        self.calls.append(("create_multipart_upload", kwargs))
        assert kwargs["ACL"] == "public-read"
        return {"UploadId": "synthetic-owned-upload"}

    def list_parts(self, **kwargs):
        self.calls.append(("list_parts", kwargs))
        assert kwargs["UploadId"] == "synthetic-owned-upload"
        return {"IsTruncated": self.truncated, "Parts": [
            {"PartNumber": number, "ETag": record[0], "Size": len(record[1])} for number, record in sorted(self.parts.items())
        ]}

    def upload_part(self, **kwargs):
        self.calls.append(("upload_part", {key: value for key, value in kwargs.items() if key != "Body"}))
        number = kwargs["PartNumber"]
        if self.fail_part == number:
            self.fail_part = None
            raise ProviderError(503)
        body = kwargs["Body"]
        checksum = hashlib.md5(body, usedforsecurity=False)
        assert kwargs["ContentMD5"] == base64.b64encode(checksum.digest()).decode()
        etag = '"' + checksum.hexdigest() + '"'
        self.parts[number] = (etag, body)
        return {"ETag": etag}

    def complete_multipart_upload(self, **kwargs):
        self.calls.append(("complete_multipart_upload", kwargs))
        self.objects[kwargs["Key"]] = b"".join(self.parts[item["PartNumber"]][1] for item in kwargs["MultipartUpload"]["Parts"])
        if self.uncertain_complete:
            self.uncertain_complete = False
            raise ProviderError(503)
        return {}

    def close(self):
        self.calls.append(("close", {}))


def consent(prompt):
    if "Type '" in prompt:
        return prompt.split("Type '")[1].split("' to confirm:")[0]
    return "n"


@pytest.fixture
def source(tmp_path):
    bundle = tmp_path / "sources.tar"
    with tarfile.open(bundle, "w") as archive:
        body = b"synthetic test source and notice"
        entry = tarfile.TarInfo("COPYING.txt")
        entry.size = len(body)
        archive.addfile(entry, io.BytesIO(body))
    return bundle


@pytest.fixture
def harness(source, tmp_path, monkeypatch):
    cloud = FakeSpaces()
    state = tmp_path / "publication-state"
    monkeypatch.setattr(spaces, "PART_BYTES", 1024)
    def fetch(audit):
        body = cloud.objects.get(urlsplit(audit["source_bundle_url"]).path.lstrip("/"), b"")
        if (hashlib.sha256(body).hexdigest(), len(body)) != (audit["source_bundle_sha256"], audit["source_bundle_bytes"]):
            raise ValueError("anonymous source hash mismatch")
    monkeypatch.setattr(spaces.publication, "fetch_source", fetch)
    factories = []
    def factory(region, access, secret):
        assert (region, access, secret) == ("ams3", ACCESS, SECRET)
        factories.append(region)
        return cloud
    def run(**kwargs):
        options = {"bundle": source, "bucket": "reasoning-sources", "region": "ams3", "state": state,
                   "ask": consent, "password": lambda prompt: SECRET if "secret key" in prompt else ACCESS, "factory": factory}
        options.update(kwargs)
        return spaces.publish_source(**options)
    return source, state, cloud, factories, run


def names(cloud):
    return [name for name, _ in cloud.calls]


def test_spaces_publication_is_public_exact_and_does_not_save_secrets(harness):
    source, state, cloud, factories, run = harness
    result = run()
    assert result["source_bundle_bytes"] == source.stat().st_size
    assert result["source_bundle_url"].startswith("https://reasoning-sources.ams3.digitaloceanspaces.com/compute-sources/")
    assert "create_bucket" not in names(cloud) and factories == ["ams3"]
    assert names(cloud).count("create_multipart_upload") == 1 and names(cloud).count("complete_multipart_upload") == 1
    for path in state.rglob("*"):
        if path.is_file():
            body = path.read_bytes()
            assert ACCESS.encode() not in body and SECRET.encode() not in body
            assert path.stat().st_mode & 0o077 == 0
    assert not list(state.rglob("compute-images.json")) and not list(state.rglob("source-review.ed25519"))
    calls = len(cloud.calls)
    assert run() == result
    assert len(cloud.calls) == calls and len(factories) == 1, "completed retries recheck anonymous bytes without credentials"


def test_cancel_before_publication_never_connects(harness):
    _, state, cloud, factories, run = harness
    with pytest.raises(ValueError, match="paused"):
        run(ask=lambda _: "")
    assert not factories and not cloud.calls and not list(state.rglob("source-upload-complete.json"))


@pytest.mark.parametrize("approved", [False, True])
def test_paid_bucket_creation_requires_separate_consent_and_stays_private(harness, approved):
    _, state, cloud, _, run = harness
    cloud.bucket = False
    prompts = []
    def ask(prompt):
        prompts.append(prompt)
        return "" if "create source bucket" in prompt and not approved else consent(prompt)
    if approved:
        run(ask=ask)
        assert names(cloud).count("create_bucket") == 1
        assert next(arguments for name, arguments in cloud.calls if name == "create_bucket")["ACL"] == "private"
    else:
        with pytest.raises(ValueError, match="paused"):
            run(ask=ask)
        assert "create_bucket" not in names(cloud) and "create_multipart_upload" not in names(cloud)
        assert not list(state.rglob("source-upload-complete.json"))
    assert any("create source bucket" in prompt for prompt in prompts)


def test_retry_uses_only_own_upload_and_skips_acknowledged_parts(harness):
    _, state, cloud, _, run = harness
    cloud.fail_part = 2
    with pytest.raises(spaces.SpacesError) as error:
        run()
    assert SECRET not in str(error.value) and "HTTP 503" in str(error.value)
    assert not list(state.rglob("source-upload-complete.json"))
    result = run()
    assert result and names(cloud).count("create_multipart_upload") == 1
    assert sum(name == "upload_part" and arguments["PartNumber"] == 1 for name, arguments in cloud.calls) == 1
    assert "abort_multipart_upload" not in names(cloud)


def test_uncertain_completion_recovers_actual_object_without_replacing_it(harness):
    _, state, cloud, _, run = harness
    cloud.uncertain_complete = True
    with pytest.raises(spaces.SpacesError):
        run()
    assert not list(state.rglob("source-upload-complete.json"))
    assert run()
    assert names(cloud).count("create_multipart_upload") == 1 and names(cloud).count("complete_multipart_upload") == 1


def test_existing_mismatched_object_is_never_overwritten(harness):
    _, state, cloud, _, run = harness
    cloud.uncertain_complete = True
    with pytest.raises(spaces.SpacesError):
        run()
    cloud.objects[next(iter(cloud.objects))] = b"other existing bytes"
    before = len(cloud.calls)
    with pytest.raises(ValueError, match="anonymous source hash"):
        run()
    assert not any(name in {"upload_part", "create_multipart_upload", "complete_multipart_upload"} for name, _ in cloud.calls[before:])
    assert not list(state.rglob("source-upload-complete.json"))


def test_changed_destination_is_refused_before_credentials_or_cloud_access(harness):
    _, _, cloud, factories, run = harness
    run()
    before = len(cloud.calls)
    with pytest.raises(ValueError, match="original inputs"):
        run(bucket="other-source-bucket")
    assert len(cloud.calls) == before and len(factories) == 1


def test_retained_completion_never_substitutes_for_fresh_anonymous_bytes(harness):
    _, _, cloud, factories, run = harness
    run()
    cloud.objects[next(iter(cloud.objects))] = b"changed public object"
    before = len(cloud.calls)
    with pytest.raises(ValueError, match="anonymous source hash"):
        run()
    assert len(cloud.calls) == before and len(factories) == 1


def test_permission_error_is_not_treated_as_permission_to_create_bucket(harness, monkeypatch):
    _, state, cloud, _, run = harness
    def denied(**kwargs):
        raise ProviderError(403)
    monkeypatch.setattr(cloud, "head_bucket", denied)
    with pytest.raises(spaces.SpacesError, match="HTTP 403"):
        run()
    assert "create_bucket" not in names(cloud) and "create_multipart_upload" not in names(cloud)
    assert not list(state.rglob("source-upload-complete.json"))


def test_denied_bucket_creation_can_resume_with_the_original_publication_intent(harness, monkeypatch):
    _, state, cloud, _, run = harness
    cloud.bucket = False
    original = cloud.create_bucket

    def denied(**kwargs):
        raise ProviderError(403)

    monkeypatch.setattr(cloud, "create_bucket", denied)
    with pytest.raises(spaces.SpacesError, match="HTTP 403") as error:
        run()
    assert ACCESS not in str(error.value) and SECRET not in str(error.value)
    intent_path = next(state.rglob("source-upload-intent.json"))
    intent = intent_path.read_bytes()
    assert "create_multipart_upload" not in names(cloud)
    assert not list(state.rglob("source-upload-complete.json"))

    monkeypatch.setattr(cloud, "create_bucket", original)
    result = run()
    assert result["source_bundle_url"] == json.loads(intent)["source_bundle_url"]
    assert intent_path.read_bytes() == intent
    assert names(cloud).count("create_bucket") == 1
    assert names(cloud).count("create_multipart_upload") == 1


def test_replaced_remote_part_is_retransferred_before_completion(harness):
    _, _, cloud, _, run = harness
    cloud.fail_part = 2
    with pytest.raises(spaces.SpacesError):
        run()
    cloud.parts[1] = ('"changed-etag"', b"bad remote part")
    assert run()
    assert sum(name == "upload_part" and arguments["PartNumber"] == 1 for name, arguments in cloud.calls) == 2


def test_source_transfer_deadline_retains_owned_journal_without_completion(harness, monkeypatch):
    _, state, cloud, _, run = harness
    times = iter([0, 5401])
    monkeypatch.setattr(spaces.time, "monotonic", lambda: next(times))
    with pytest.raises(ValueError, match="90 minute"):
        run()
    assert "complete_multipart_upload" not in names(cloud) and not list(state.rglob("source-upload-complete.json"))


@pytest.mark.parametrize("bucket,region", [("has.dots", "ams3"), ("A-bucket", "ams3"), ("ok-bucket", "localhost"),
                                          ("x", "ams3"), ("ok-bucket", "ams3.example.org"), ("ok-bucket", "ams3/secret")])
def test_destination_is_validated_before_any_credentials_or_network(harness, bucket, region):
    _, _, cloud, factories, run = harness
    with pytest.raises(ValueError, match="bucket name"):
        run(bucket=bucket, region=region)
    assert not factories and not cloud.calls


def test_truncated_remote_parts_cannot_complete_a_public_object(harness):
    _, state, cloud, _, run = harness
    cloud.truncated = True
    with pytest.raises(ValueError, match="incomplete"):
        run()
    assert "complete_multipart_upload" not in names(cloud) and not list(state.rglob("source-upload-complete.json"))


def test_source_mutation_during_transfer_keeps_it_uncompleted(harness, monkeypatch):
    source, state, cloud, _, run = harness
    original = cloud.upload_part
    def mutate(**arguments):
        result = original(**arguments)
        if arguments["PartNumber"] == 1:
            with source.open("ab") as stream:
                stream.write(b"changed input")
        return result
    monkeypatch.setattr(cloud, "upload_part", mutate)
    with pytest.raises(ValueError, match="grew|changed"):
        run()
    assert "complete_multipart_upload" not in names(cloud) and not list(state.rglob("source-upload-complete.json"))


def test_hidden_credentials_open_provider_page_and_reject_empty_key_pair():
    opened, prompts = [], []
    def password(prompt):
        prompts.append(prompt)
        return SECRET if "secret key" in prompt else ACCESS
    assert spaces.credentials(None, password=password, browser=opened.append, ask=lambda _: "") == (ACCESS, SECRET)
    assert opened == [spaces.KEY_PAGE] and all("hidden" in prompt for prompt in prompts)
    assert prompts == ["Spaces access key ID (hidden): ", "Spaces secret key (hidden): "]
    with pytest.raises(ValueError, match="key pair"):
        spaces.credentials(None, password=lambda _: "", ask=lambda _: "n")


def test_credentials_file_requires_private_owner_only_permissions(tmp_path):
    path = tmp_path / "spaces.json"
    path.write_text(json.dumps({"access_key": ACCESS, "secret_key": SECRET}))
    path.chmod(0o600)
    assert spaces.credentials(path) == (ACCESS, SECRET)
    path.chmod(0o644)
    with pytest.raises(InstallError):
        spaces.credentials(path)


def test_cli_dispatch_and_make_target_do_not_require_docker_or_node(monkeypatch, tmp_path):
    called = []
    def publish(*arguments):
        called.append(arguments)
        return {"source_bundle_url": "https://example.org/sources.tar"}
    monkeypatch.setattr(spaces, "publish_source", publish)
    assert review.main(["publish-source-spaces", "--source-bundle", str(tmp_path / "sources.tar")]) == 0
    assert called == [(tmp_path / "sources.tar", None, None, None, None)]
    result = subprocess.run(["make", "--dry-run", "compute-source-publish", "SOURCE_BUNDLE=/private/synthetic.tar"],
                            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, check=True)
    assert "--extra publication" in result.stdout and "publish-source-spaces" in result.stdout
    assert "with-node" not in result.stdout and "docker" not in result.stdout.lower()


def test_real_sdk_uses_explicit_spaces_endpoint_credentials_and_no_proxies():
    pytest.importorskip("boto3")
    client = spaces.client_for("ams3", ACCESS, SECRET)
    try:
        assert client.meta.endpoint_url == "https://ams3.digitaloceanspaces.com"
        assert client.meta.config.proxies == {} and client.meta.config.signature_version == "s3v4"
        assert client.meta.config.s3["addressing_style"] == "virtual"
        assert client.meta.config.request_checksum_calculation == "when_required"
        model = client.meta.service_model.operation_model("CreateMultipartUpload")
        assert "ACL" in model.input_shape.members
        assert "ContentMD5" in client.meta.service_model.operation_model("UploadPart").input_shape.members
    finally:
        client.close()
