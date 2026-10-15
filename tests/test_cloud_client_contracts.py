# Ruff 0.3.4 and 0.15.x classify the src-layout package differently.
# ruff: noqa: I001
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from reasoning_ledger.cloud_client import (
    CLOUD_CLIENT_BUNDLE_DIGEST,
    CLOUD_CLIENT_VERSION,
    AcceptedCloudClientRecord,
    CloudClientContractError,
    CloudClientContractValidator,
    byte_digest,
    jcs_digest,
)
from reasoning_ledger.harnesses.desktop_hybrid import AcceptedContractRecord

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_ROOT = ROOT / "contracts" / "cloud-client" / "v1alpha1"
CONFORMANCE_ROOT = ROOT / "conformance" / "cloud-client" / "v1alpha1"
POSITIVE_ROOT = CONFORMANCE_ROOT / "fixtures" / "positive"
PLATFORM_POSITIVE = ROOT / "conformance" / "platform" / "v1alpha1" / "fixtures" / "positive"


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _strict_load(path: Path):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"duplicate key: {key}")
            value[key] = item
        return value

    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)


def _mutate(instance, mutations: list[dict]):
    result = copy.deepcopy(instance)
    for mutation in mutations:
        parts = [part.replace("~1", "/").replace("~0", "~") for part in mutation["pointer"].removeprefix("/").split("/")]
        target = result
        for part in parts[:-1]:
            target = target[int(part)] if isinstance(target, list) else target[part]
        leaf = parts[-1]
        if mutation["op"] == "remove":
            target.pop(int(leaf)) if isinstance(target, list) else target.pop(leaf)
        elif mutation["op"] == "replace":
            if isinstance(target, list):
                target[int(leaf)] = mutation["value"]
            else:
                target[leaf] = mutation["value"]
        elif mutation["op"] == "add":
            if isinstance(target, list):
                target.insert(int(leaf), mutation["value"])
            else:
                target[leaf] = mutation["value"]
        else:
            raise AssertionError(f"unknown mutation: {mutation['op']}")
    return result


@pytest.fixture(scope="module")
def validator() -> CloudClientContractValidator:
    return CloudClientContractValidator(ROOT)


@pytest.fixture(scope="module")
def accepted(validator: CloudClientContractValidator):
    return {
        path.stem: AcceptedCloudClientRecord.from_bytes(path.read_bytes(), validator=validator) for path in POSITIVE_ROOT.glob("*.json")
    }


@pytest.fixture(scope="module")
def descriptor_bytes() -> dict[str, bytes]:
    paths = (
        PLATFORM_POSITIVE / "harness-provider.local.json",
        PLATFORM_POSITIVE / "inference-route.hosted.json",
        PLATFORM_POSITIVE / "compute-target.hosted.json",
    )
    return {byte_digest(path.read_bytes()): path.read_bytes() for path in paths}


def test_schemas_meta_validate_with_exact_identity():
    paths = sorted(CONTRACT_ROOT.glob("*.schema.json"))
    assert [path.name for path in paths] == [
        "capabilities.schema.json",
        "common.schema.json",
        "compatibility.schema.json",
        "contract-bundle.schema.json",
        "run-event-page.schema.json",
        "run.schema.json",
        "session.schema.json",
    ]
    ids = set()
    for path in paths:
        schema = _strict_load(path)
        Draft202012Validator.check_schema(schema)
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["$id"] == f"https://schemas.reasoning.invalid/cloud-client/v1alpha1/{path.name}"
        assert schema["$id"] not in ids
        ids.add(schema["$id"])


def test_declared_positive_fixtures_are_complete_and_semantically_valid(
    validator: CloudClientContractValidator,
    accepted: dict[str, AcceptedCloudClientRecord],
    descriptor_bytes: dict[str, bytes],
):
    suite = _strict_load(CONFORMANCE_ROOT / "suite.json")
    declared = {item["fixture"] for item in suite["positive"]}
    observed = {f"fixtures/positive/{path.name}" for path in POSITIVE_ROOT.glob("*.json")}
    assert declared == observed
    capabilities = accepted["capabilities"]
    connected = accepted["session.connected"]
    validator.validate_capabilities(capabilities, lambda reference: descriptor_bytes[reference.digest])
    validator.validate_session(accepted["session.disconnected"])
    validator.validate_session(connected, capabilities)
    project = AcceptedContractRecord.from_bytes((PLATFORM_POSITIVE / "project-bundle.local-unlinked.json").read_bytes())
    validator.validate_run(accepted["run.succeeded"], session=connected, capabilities=capabilities, project=project)
    validator.validate_event_page(accepted["run-event-page.complete"])


def test_mutation_oriented_negative_suite_fails_closed(
    validator: CloudClientContractValidator,
    accepted: dict[str, AcceptedCloudClientRecord],
    descriptor_bytes: dict[str, bytes],
):
    negative = _strict_load(CONFORMANCE_ROOT / "fixtures" / "negative" / "cases.json")
    capabilities = accepted["capabilities"]
    connected = accepted["session.connected"]
    project = AcceptedContractRecord.from_bytes((PLATFORM_POSITIVE / "project-bundle.local-unlinked.json").read_bytes())
    seen = set()
    for case in negative["cases"]:
        assert case["id"] not in seen
        seen.add(case["id"])
        value = _mutate(_load(CONFORMANCE_ROOT / "fixtures" / case["base"]), case["mutations"])
        with pytest.raises(CloudClientContractError):
            record = AcceptedCloudClientRecord.from_document(value, validator=validator)
            if case["validation"] == "capabilities_semantic":
                validator.validate_capabilities(record, lambda _reference: descriptor_bytes[next(iter(descriptor_bytes))])
            elif case["validation"] == "run_semantic":
                validator.validate_run(record, session=connected, capabilities=capabilities, project=project)
            elif case["validation"] == "events_semantic":
                validator.validate_event_page(record)
            elif case["validation"] == "security_semantic":
                raise AssertionError("security validation should fail while accepting the record")
            else:
                assert case["validation"] == "schema"
                raise AssertionError("schema validation should fail while accepting the record")
    assert len(seen) == 19


def test_invalid_capabilities_fail_before_descriptor_resolution(
    validator: CloudClientContractValidator,
):
    document = _load(POSITIVE_ROOT / "capabilities.json")
    document["compatibility_digest"] = "sha256:" + "f" * 64
    record = AcceptedCloudClientRecord.from_document(document, validator=validator)
    calls = 0

    def resolver(_reference):
        nonlocal calls
        calls += 1
        raise AssertionError("resolver must not be called")

    with pytest.raises(CloudClientContractError, match="compatibility"):
        validator.validate_capabilities(record, resolver)
    assert calls == 0


def test_strict_parser_rejects_duplicate_keys_and_nonfinite_numbers(validator: CloudClientContractValidator):
    duplicate = (
        b'{"schema_id":"reasoning.cloud-client.session","schema_id":"other",'
        b'"schema_version":"1.0.0-alpha.1","state":"disconnected"}'
    )
    nonfinite = b'{"schema_id":"reasoning.cloud-client.session","schema_version":"1.0.0-alpha.1","state":"disconnected","x":NaN}'
    with pytest.raises(CloudClientContractError, match="duplicate"):
        validator.accept(duplicate)
    with pytest.raises(CloudClientContractError, match="non-finite"):
        validator.accept(nonfinite)


def test_manifest_reproduces_member_dependency_and_bundle_digests(validator: CloudClientContractValidator):
    manifest = validator.manifest
    assert manifest["bundle_digest"] == CLOUD_CLIENT_BUNDLE_DIGEST
    assert manifest["schema_version"] == CLOUD_CLIENT_VERSION
    basis = {key: value for key, value in manifest.items() if key != "bundle_digest"}
    assert jcs_digest(basis) == CLOUD_CLIENT_BUNDLE_DIGEST
    entries = [*manifest["members"], *manifest["dependencies"]]
    assert len({item["path"] for item in entries}) == len(entries)
    for item in entries:
        assert item["digest"] == byte_digest((ROOT / item["path"]).read_bytes())
    assert manifest["dependencies"] == validator.compatibility["dependencies"]


def test_dependency_schemas_are_referenced_not_copied():
    owned_names = {path.name for path in CONTRACT_ROOT.glob("*.schema.json")}
    assert not {
        "project-bundle.schema.json",
        "harness-provider.schema.json",
        "inference-route.schema.json",
        "compute-target.schema.json",
        "local-harness-connector.schema.json",
        "pricing.schema.json",
        "route-plan.schema.json",
        "artifact.schema.json",
    }.intersection(owned_names)
    run_schema = (CONTRACT_ROOT / "run.schema.json").read_text(encoding="utf-8")
    events_schema = (CONTRACT_ROOT / "run-event-page.schema.json").read_text(encoding="utf-8")
    assert "../../platform/v1alpha1/route-plan.schema.json" in run_schema
    assert "../../platform/v1alpha2/pricing.schema.json" in run_schema
    assert "../../executor/v1alpha1/artifact.schema.json" in events_schema


def test_accepted_record_retains_exact_bytes_and_detects_tampering(
    accepted: dict[str, AcceptedCloudClientRecord],
):
    record = accepted["session.connected"]
    assert record.to_bytes() == (POSITIVE_ROOT / "session.connected.json").read_bytes()
    copied = copy.copy(record)
    object.__setattr__(copied, "digest", "sha256:" + "f" * 64)
    with pytest.raises(CloudClientContractError, match="bytes changed"):
        copied.document()


def test_capability_references_are_exact_accepted_platform_records(
    validator: CloudClientContractValidator,
    accepted: dict[str, AcceptedCloudClientRecord],
    descriptor_bytes: dict[str, bytes],
):
    observed = []

    def resolver(reference):
        payload = descriptor_bytes[reference.digest]
        observed.append(AcceptedContractRecord.from_bytes(payload).schema_id)
        return payload

    validator.validate_capabilities(accepted["capabilities"], resolver)
    assert observed == [
        "reasoning.platform.harness-provider",
        "reasoning.platform.inference-route",
        "reasoning.platform.compute-target",
    ]


def test_member_and_dependency_paths_are_sorted_and_unique(validator: CloudClientContractValidator):
    for key in ("members", "dependencies"):
        paths = [item["path"] for item in validator.manifest[key]]
        assert paths == sorted(paths)
        assert len(paths) == len(set(paths))


def test_manifest_hash_is_sha256_not_a_noncryptographic_summary(validator: CloudClientContractValidator):
    digest = validator.manifest["bundle_digest"].removeprefix("sha256:")
    assert len(digest) == hashlib.sha256().digest_size * 2
    int(digest, 16)
