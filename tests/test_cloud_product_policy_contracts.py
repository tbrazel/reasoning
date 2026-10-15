from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from referencing import Registry, Resource

from reasoning_ledger.cloud_client import CLOUD_CLIENT_BUNDLE_DIGEST, byte_digest, jcs_digest

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_ROOT = ROOT / "contracts" / "cloud-product-policy" / "v1alpha1"
CONFORMANCE_ROOT = ROOT / "conformance" / "cloud-product-policy" / "v1alpha1"
POSITIVE_ROOT = CONFORMANCE_ROOT / "fixtures" / "positive"
CLOUD_CLIENT_MANIFEST = ROOT / "contracts" / "cloud-client" / "v1alpha1" / "bundle-manifest.json"
VERSION = "1.0.0-alpha.1"
BUNDLE_DIGEST = "sha256:c78d03c0204a2524af0e78209a4cc51c1aaf40cac092b3fb4d0063cc81540b26"
DOMAIN_SCHEMAS = {
    "reasoning.cloud-product-policy.account-policy": "account-policy.schema.json",
    "reasoning.cloud-product-policy.optional-use-change": "optional-use-change.schema.json",
}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key: {key}")
        result[key] = value
    return result


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)


@pytest.fixture(scope="module")
def schemas():
    documents = [_load(path) for path in sorted(CONTRACT_ROOT.glob("*.schema.json"))]
    registry = Registry()
    by_name = {}
    for document in documents:
        Draft202012Validator.check_schema(document)
        registry = registry.with_resource(document["$id"], Resource.from_contents(document))
        by_name[Path(document["$id"]).name] = document
    return by_name, registry


def _validate(document, schema_name, schemas):
    by_name, registry = schemas
    Draft202012Validator(by_name[schema_name], registry=registry, format_checker=FormatChecker()).validate(document)


def _mutate(document, case):
    result = copy.deepcopy(document)
    parts = [part.replace("~1", "/").replace("~0", "~") for part in case["pointer"].removeprefix("/").split("/")]
    target = result
    for part in parts[:-1]:
        target = target[int(part)] if isinstance(target, list) else target[part]
    leaf = parts[-1]
    if case["op"] == "remove":
        target.pop(int(leaf)) if isinstance(target, list) else target.pop(leaf)
    elif case["op"] == "replace":
        if isinstance(target, list):
            target[int(leaf)] = case["value"]
        else:
            target[leaf] = case["value"]
    else:
        assert case["op"] == "add"
        if isinstance(target, list):
            target.insert(int(leaf), case["value"])
        else:
            target[leaf] = case["value"]
    return result


def _time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _without(value, key):
    result = copy.deepcopy(value)
    result.pop(key)
    return result


def test_family_has_exactly_two_public_domain_records_and_bounded_closed_schemas(schemas):
    by_name, _registry = schemas
    assert sorted(by_name) == [
        "account-policy.schema.json",
        "common.schema.json",
        "compatibility.schema.json",
        "contract-bundle.schema.json",
        "optional-use-change.schema.json",
    ]
    compatibility = _load(CONTRACT_ROOT / "compatibility.json")
    assert {item["schema_id"]: item["schema_path"] for item in compatibility["contracts"]} == DOMAIN_SCHEMAS
    assert all(item["version"] == VERSION for item in compatibility["contracts"])
    for schema_id, name in DOMAIN_SCHEMAS.items():
        schema = by_name[name]
        assert schema["properties"]["schema_id"] == {"const": schema_id}
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
    common_text = (CONTRACT_ROOT / "common.schema.json").read_text(encoding="utf-8")
    assert '"maxItems": 64' in common_text
    assert '"maxLength": 32' in common_text
    assert '"maxLength": 40' in common_text


def test_positive_records_schema_validate_and_declared_suite_is_complete(schemas):
    suite = _load(CONFORMANCE_ROOT / "suite.json")
    observed = set()
    for entry in suite["positive"]:
        document = _load(CONFORMANCE_ROOT / entry["fixture"])
        _validate(document, entry["schema"], schemas)
        observed.add(document["schema_id"])
    assert observed == set(DOMAIN_SCHEMAS)
    assert {entry["fixture"] for entry in suite["positive"]} == {
        "fixtures/positive/account-policy.json",
        "fixtures/positive/optional-use-change.grant.json",
    }


def test_negative_schema_mutations_fail_closed(schemas):
    cases = _load(CONFORMANCE_ROOT / "fixtures" / "negative" / "cases.json")["cases"]
    seen = set()
    for case in cases:
        assert case["id"] not in seen
        seen.add(case["id"])
        document = _mutate(_load(CONFORMANCE_ROOT / case["base"]), case)
        schema = DOMAIN_SCHEMAS[document.get("schema_id", "")]
        with pytest.raises(ValidationError):
            _validate(document, schema, schemas)
    assert len(seen) == 15


def test_account_snapshot_digest_domains_and_chronology_are_exact():
    account = _load(POSITIVE_ROOT / "account-policy.json")
    capability = _load(ROOT / "conformance" / "cloud-client" / "v1alpha1" / "fixtures" / "positive" / "capabilities.json")
    assert account["capability_snapshot"]["digest"] == jcs_digest(capability)
    entitlement = account["entitlement"]
    assert entitlement["digest"] == jcs_digest({"id": entitlement["id"], "version": entitlement["version"]})
    catalogue = account["catalogue"]
    assert catalogue["catalogue_digest"] == jcs_digest(
        {"catalogue_id": catalogue["catalogue_id"], "catalogue_version": catalogue["catalogue_version"]}
    )
    assert catalogue["plan_digest"] == jcs_digest({"plan_id": catalogue["plan_id"], "plan_version": catalogue["plan_version"]})
    policy = account["product_policy"]
    assert policy["digest"] == jcs_digest(_without(policy, "digest"))
    assert _time(policy["effective_at"]) <= _time(account["observed_at"]) < _time(policy["expires_at"])
    assert account["snapshot_digest"] == jcs_digest(_without(account, "snapshot_digest"))


def test_offers_are_independent_exact_capability_references_and_fail_closed():
    account = _load(POSITIVE_ROOT / "account-policy.json")
    capability = _load(ROOT / "conformance" / "cloud-client" / "v1alpha1" / "fixtures" / "positive" / "capabilities.json")
    expected = {
        "reasoning.platform.inference-route": {(item["record_id"], item["digest"]) for item in capability["inference_routes"]},
        "reasoning.platform.compute-target": {(item["record_id"], item["digest"]) for item in capability["compute_targets"]},
    }
    descriptor_paths = {
        "reasoning.platform.inference-route": ROOT
        / "conformance"
        / "platform"
        / "v1alpha1"
        / "fixtures"
        / "positive"
        / "inference-route.hosted.json",
        "reasoning.platform.compute-target": ROOT
        / "conformance"
        / "platform"
        / "v1alpha1"
        / "fixtures"
        / "positive"
        / "compute-target.hosted.json",
    }
    for axis, schema_id in (
        ("inference_offers", "reasoning.platform.inference-route"),
        ("compute_offers", "reasoning.platform.compute-target"),
    ):
        seen = set()
        for offer in account[axis]:
            reference = offer["reference"]
            identity = (reference["record_id"], reference["digest"])
            assert reference["schema_id"] == schema_id
            assert identity in expected[schema_id]
            assert identity not in seen
            seen.add(identity)
            descriptor_bytes = descriptor_paths[schema_id].read_bytes()
            descriptor = _load(descriptor_paths[schema_id])
            assert reference["digest"] == byte_digest(descriptor_bytes)
            assert reference["digest"] != jcs_digest(descriptor)
            assert {item["kind"] for item in offer["limits"]} == {"operations", "concurrency", "spend"}
            assert len({item["kind"] for item in offer["limits"]}) == len(offer["limits"])
            price = offer["price"]
            if price["state"] == "zero":
                assert price["lower_bound"] == price["upper_bound"] == "0"
            elif price["state"] == "estimated":
                assert Decimal(price["lower_bound"]) <= Decimal(price["upper_bound"])
            else:
                assert price["state"] in {"unknown", "unavailable"}
                assert price["currency"] is price["lower_bound"] is price["upper_bound"] is price["basis"] is None
            if offer["availability"] == "available":
                assert offer["data_path"]["state"] == "verified"
                if offer["data_path"]["location"] == "hosted":
                    assert offer["required_telemetry"]["state"] == "configured"
                else:
                    assert offer["data_path"]["subprocessors"] == []
                    assert offer["data_path"]["provider_training"] == "prohibited"


def test_all_optional_uses_start_off_with_exact_independent_history():
    account = _load(POSITIVE_ROOT / "account-policy.json")
    assert set(account["optional_uses"]) == {"product_analytics", "research_evaluation", "model_training"}
    for purpose in account["optional_uses"].values():
        policy = purpose["policy"]
        assert policy["digest"] == jcs_digest(_without(policy, "digest"))
        assert purpose["decision"]["state"] == "off"
        assert purpose["decision"]["revision"] == 0
        assert purpose["allowed_action"] == "grant"
        assert len(purpose["decision_history"]) == 1
        final = purpose["decision_history"][-1]
        assert final["digest"] == jcs_digest(_without(final, "digest"))
        assert purpose["decision"] == {key: final[key] for key in purpose["decision"]}
        assert final["policy"] is None


def test_change_digest_includes_purpose_action_every_cas_term_and_exact_policy():
    change = _load(POSITIVE_ROOT / "optional-use-change.grant.json")
    account = _load(POSITIVE_ROOT / "account-policy.json")
    assert change["request_digest"] == jcs_digest(_without(change, "request_digest"))
    assert change["consent_policy"] == account["optional_uses"][change["purpose"]]["policy"]
    basis = _without(change, "request_digest")
    for pointer in (
        ("purpose",),
        ("action",),
        ("expected", "snapshot_digest"),
        ("expected", "capability_snapshot_digest"),
        ("expected", "entitlement_digest"),
        ("expected", "product_policy_digest"),
        ("expected", "decision_digest"),
    ):
        changed = copy.deepcopy(basis)
        target = changed
        for key in pointer[:-1]:
            target = target[key]
        target[pointer[-1]] = "withdraw" if pointer == ("action",) else "sha256:" + "f" * 64
        assert jcs_digest(changed) != change["request_digest"]


def test_manifest_is_complete_sorted_reproducible_and_pins_distinct_t030_digest_domains(schemas):
    manifest = _load(CONTRACT_ROOT / "bundle-manifest.json")
    compatibility = _load(CONTRACT_ROOT / "compatibility.json")
    _validate(manifest, "contract-bundle.schema.json", schemas)
    _validate(compatibility, "compatibility.schema.json", schemas)
    assert manifest["bundle_digest"] == BUNDLE_DIGEST == jcs_digest(_without(manifest, "bundle_digest"))
    members = manifest["members"]
    paths = [item["path"] for item in members]
    assert paths == sorted(paths)
    assert len(paths) == len(set(paths)) == 10
    assert paths == sorted(
        path.relative_to(ROOT).as_posix()
        for root in (CONTRACT_ROOT, CONFORMANCE_ROOT)
        for path in root.rglob("*.json")
        if path.name != "bundle-manifest.json"
    )
    for member in members:
        assert member["digest"] == byte_digest((ROOT / member["path"]).read_bytes())
    assert manifest["dependencies"] == compatibility["dependencies"]
    dependency = manifest["dependencies"][0]
    assert dependency["digest"] == byte_digest(CLOUD_CLIENT_MANIFEST.read_bytes())
    assert dependency["digest"] == "sha256:5c858b9346cfc796c50c7e2cc48ffee2d6c61334fc14f63d7ba24f46522e8bf3"
    assert dependency["internal_bundle_digest"] == CLOUD_CLIENT_BUNDLE_DIGEST
    assert dependency["digest"] != dependency["internal_bundle_digest"]
    assert _load(CLOUD_CLIENT_MANIFEST)["bundle_digest"] == dependency["internal_bundle_digest"]


def test_t030_and_t040_protected_bytes_remain_exact():
    assert hashlib.sha256(CLOUD_CLIENT_MANIFEST.read_bytes()).hexdigest() == (
        "5c858b9346cfc796c50c7e2cc48ffee2d6c61334fc14f63d7ba24f46522e8bf3"
    )
    assert hashlib.sha256((ROOT / "src" / "reasoning_ledger" / "desktop_policy.py").read_bytes()).hexdigest() == (
        "6b8f00b6e2919cca14da73e5dfba1c59b215a24cdaac0c7598fed5ba404adf7b"
    )


def test_public_records_exclude_private_and_client_identity_material():
    forbidden = {
        "access_token",
        "api_key",
        "authorization",
        "client_class",
        "cookie",
        "csrf",
        "membership",
        "outbox",
        "provider_identity",
        "session_handle",
        "session_token",
        "telemetry_body",
    }
    for path in POSITIVE_ROOT.glob("*.json"):
        document = _load(path)
        stack = [document]
        keys = set()
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                keys.update(key.casefold() for key in value)
                stack.extend(value.values())
            elif isinstance(value, list):
                stack.extend(value)
        assert not forbidden.intersection(keys)
