from __future__ import annotations

import copy
import hashlib
import itertools
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "contracts" / "platform" / "v1alpha2"
CONFORMANCE_DIR = ROOT / "conformance" / "platform" / "v1alpha2"
FIXTURE_DIR = CONFORMANCE_DIR / "fixtures"
POSITIVE_DIR = FIXTURE_DIR / "positive"
ALPHA1_SCHEMA_DIR = ROOT / "contracts" / "platform" / "v1alpha1"
ALPHA1_CONFORMANCE_DIR = ROOT / "conformance" / "platform" / "v1alpha1"
ALPHA1_POSITIVE_DIR = ALPHA1_CONFORMANCE_DIR / "fixtures" / "positive"
VERSION = "1.0.0-alpha.2"


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256_file(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _jcs_digest(instance) -> str:
    canonical = json.dumps(instance, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _walk_keys(instance):
    if isinstance(instance, dict):
        for key, value in instance.items():
            yield key
            yield from _walk_keys(value)
    elif isinstance(instance, list):
        for value in instance:
            yield from _walk_keys(value)


def _mutate(instance, mutations: list[dict]) -> dict:
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
                assert leaf in target
                target[leaf] = mutation["value"]
        elif mutation["op"] == "add":
            if isinstance(target, list):
                target.insert(int(leaf), mutation["value"])
            else:
                assert leaf not in target
                target[leaf] = mutation["value"]
        else:
            raise AssertionError(f"unknown mutation operation: {mutation['op']}")
    return result


@pytest.fixture(scope="module")
def schemas() -> dict[str, dict]:
    loaded = {path.name: _load(path) for path in sorted(SCHEMA_DIR.glob("*.schema.json"))}
    assert set(loaded) == {
        "common.schema.json",
        "compatibility.schema.json",
        "contract-bundle.schema.json",
        "pricing.schema.json",
        "route-plan.schema.json",
    }
    return loaded


@pytest.fixture(scope="module")
def registry(schemas: dict[str, dict]) -> Registry:
    result = Registry()
    for schema in schemas.values():
        result = result.with_resource(schema["$id"], Resource.from_contents(schema))
    return result


def _validate(instance, schema: dict, registry: Registry) -> None:
    Draft202012Validator(schema, registry=registry, format_checker=FormatChecker()).validate(instance)


def _alpha1_descriptors() -> tuple[dict, dict, dict, dict]:
    harnesses = {
        item["harness_id"]: (item, _sha256_file(path))
        for path in ALPHA1_POSITIVE_DIR.glob("harness-provider.*.json")
        for item in [_load(path)]
    }
    routes = {
        item["route_id"]: (item, _sha256_file(path))
        for path in ALPHA1_POSITIVE_DIR.glob("inference-route.*.json")
        for item in [_load(path)]
    }
    targets = {
        item["target_id"]: (item, _sha256_file(path))
        for path in ALPHA1_POSITIVE_DIR.glob("compute-target.*.json")
        for item in [_load(path)]
    }
    bundles = {item["bundle_id"]: item for path in ALPHA1_POSITIVE_DIR.glob("project-bundle.*.json") for item in [_load(path)]}
    return harnesses, routes, targets, bundles


def _validate_project_semantics(bundle: dict) -> None:
    assert len({item["transfer_id"] for item in bundle["transfer_manifests"]}) == len(bundle["transfer_manifests"])
    for transfer in bundle["transfer_manifests"]:
        assert transfer["binding"]["project_id"] == bundle["project"]["project_id"]
        assert transfer["binding"]["project_revision_digest"] == bundle["project"]["revision_digest"]
        assert transfer["approval"]["actor"] == transfer["binding"]["actor"]
        assert transfer["disclosure_digest"] == _jcs_digest(transfer["disclosure"])
        assert transfer["approval"]["approved_disclosure_digest"] == transfer["disclosure_digest"]


def _validate_route_semantics(plan: dict) -> None:
    harnesses, routes, targets, bundles = _alpha1_descriptors()
    assert plan["schema_version"] == VERSION
    assert plan["pricing_contract_version"] == VERSION
    assert plan["compute"]["executor_contract_version"] == "1.0.0-alpha.1"

    harness, harness_digest = harnesses[plan["harness"]["harness_id"]]
    route, route_digest = routes[plan["inference"]["route_id"]]
    target, target_digest = targets[plan["compute"]["target_id"]]
    bundle = bundles[plan["persistence"]["project_bundle_id"]]
    _validate_project_semantics(bundle)

    assert plan["harness"]["descriptor_digest"] == harness_digest
    assert plan["inference"]["route_digest"] == route_digest
    assert plan["compute"]["target_digest"] == target_digest
    assert plan["inference"]["route_id"] in harness["supported_inference_route_ids"]
    assert plan["compute"]["target_id"] in harness["supported_compute_target_ids"]
    assert plan["harness"]["harness_id"] in route["harness_ids"]
    assert plan["harness"]["harness_id"] in target["supported_harness_ids"]

    persistence = plan["persistence"]
    assert persistence["project_id"] == bundle["project"]["project_id"]
    assert persistence["project_revision_digest"] == bundle["project"]["revision_digest"]
    assert plan["actor"] == bundle["export"]["created_by"]
    hosted = "hosted" in {
        plan["harness"]["location"],
        plan["inference"]["location"],
        plan["compute"]["location"],
    }
    if hosted:
        transfers = [item for item in bundle["transfer_manifests"] if item["transfer_id"] == persistence["transfer_manifest_id"]]
        assert len(transfers) == 1
        transfer = transfers[0]
        assert transfer["operation_id"] == plan["operation_id"]
        assert transfer["binding"] == {
            "actor": plan["actor"],
            "tenant_id": plan["tenant_id"],
            "project_id": persistence["project_id"],
            "project_revision_digest": persistence["project_revision_digest"],
        }
    else:
        assert "transfer_manifest_id" not in persistence


def _validate_pricing_semantics(lifecycle: dict, route_plan: dict) -> None:
    quote = lifecycle["quote"]
    reservation = lifecycle["reservation"]
    dispatches = lifecycle["dispatches"]
    usage = lifecycle["usage"]
    settlement = lifecycle["settlement"]
    operation = lifecycle["operation"]

    assert lifecycle["schema_version"] == VERSION
    assert lifecycle["pricing_lifecycle_id"] == route_plan["pricing_lifecycle_id"]
    assert route_plan["pricing_contract_version"] == VERSION
    assert operation["route_plan_id"] == route_plan["route_plan_id"]
    assert operation["operation_id"] == route_plan["operation_id"]
    assert operation["actor"] == route_plan["actor"]
    assert operation["tenant_id"] == route_plan["tenant_id"]
    assert operation["project_id"] == route_plan["persistence"]["project_id"]
    assert _parse_time(route_plan["decision"]["decided_at"]) <= _parse_time(quote["issued_at"])

    issued_at = _parse_time(quote["issued_at"])
    expires_at = _parse_time(quote["expires_at"])
    reserved_at = _parse_time(reservation["reserved_at"])
    assert issued_at < expires_at, "quote expiry must follow issuance"
    assert issued_at <= reserved_at <= expires_at, "reservation must occur while quote is valid"
    assert quote["quote_id"] == reservation["quote_id"], "reservation must bind exact quote"
    assert settlement["reservation_id"] == reservation["reservation_id"], "terminal record must bind exact reservation"

    for item in quote["dimensions"]:
        assert Decimal(item["maximum_amount"]) == (Decimal(item["maximum_quantity"]) * Decimal(item["unit_price"]))
    quote_maximum = sum(Decimal(item["maximum_amount"]) for item in quote["dimensions"])
    assert quote_maximum == Decimal(quote["maximum_amount"])
    assert Decimal(reservation["reserved_amount"]) <= quote_maximum

    if settlement.get("release_phase") == "pre_dispatch":
        released_at = _parse_time(settlement["released_at"])
        assert dispatches == [], "pre-dispatch release must have no dispatch"
        assert usage == [], "pre-dispatch release must have no usage"
        assert [quote["sequence"], reservation["sequence"], settlement["sequence"]] == [1, 2, 3]
        assert reserved_at <= released_at, "release must follow reservation"
        if settlement["release_reason"] == "quote_expired":
            assert expires_at <= released_at, "expiry release cannot precede quote expiry"
        else:
            assert settlement["release_reason"] == "cancelled_before_dispatch"
            assert released_at < expires_at, "cancellation reason requires release before expiry"
        assert settlement["actual_amount"] == "0"
        assert Decimal(settlement["released_amount"]) == Decimal(reservation["reserved_amount"])
        assert settlement["reservation_state"] == "released"
        assert settlement["atomic"] is True
        assert set(settlement) == {
            "settlement_id",
            "sequence",
            "state",
            "release_phase",
            "release_reason",
            "reservation_id",
            "released_at",
            "currency",
            "actual_amount",
            "released_amount",
            "reservation_state",
            "atomic",
        }
        forbidden_execution_keys = {
            "request_id",
            "job_id",
            "dispatched_at",
            "provider",
            "model",
            "executor_contract_version",
            "executor_provenance_id",
            "image_digest",
            "capabilities_digest",
            "attestation_digest",
            "artifact_id",
            "usage_id",
            "usage_digest",
            "provenance",
            "actual_provenance_complete",
        }
        assert forbidden_execution_keys.isdisjoint(_walk_keys(lifecycle)), (
            "pre-dispatch release contains execution or actual-provenance claim"
        )
        return

    assert dispatches, "dispatched lifecycle must have dispatch evidence"
    assert usage, "dispatched lifecycle must have usage evidence"
    assert {item["reservation_id"] for item in dispatches} == {reservation["reservation_id"]}
    dispatch_times = [_parse_time(item["dispatched_at"]) for item in dispatches]
    assert all(reserved_at <= item <= expires_at for item in dispatch_times)

    sequences = [quote["sequence"], reservation["sequence"]]
    sequences.extend(item["sequence"] for item in dispatches)
    sequences.extend(item["sequence"] for item in usage)
    sequences.append(settlement["sequence"])
    assert sequences == sorted(sequences)
    assert len(sequences) == len(set(sequences))
    assert settlement["usage_digest"] == _jcs_digest(usage)

    quoted_meters = {(item["kind"], item["meter"]): item for item in quote["dimensions"]}
    assert len(quoted_meters) == len(quote["dimensions"])
    for usage_record in usage:
        provenance = usage_record["provenance"]
        if usage_record["kind"] == "inference":
            assert provenance["route_id"] == route_plan["inference"]["route_id"]
            assert provenance["provider"] == route_plan["inference"]["provider"]
            assert provenance["model"] == route_plan["inference"]["model"]
            assert provenance["request_id"] in {item["request_id"] for item in dispatches if item["kind"] == "inference"}
        else:
            assert provenance["target_id"] == route_plan["compute"]["target_id"]
            assert provenance["job_id"] in {item["job_id"] for item in dispatches if item["kind"] == "compute"}
            for provenance_key, route_key in {
                "image_digest": "digest",
                "capabilities_digest": "capabilities_digest",
                "attestation_digest": "attestation_digest",
            }.items():
                assert provenance[provenance_key] == route_plan["compute"]["image"][route_key]
        if usage_record["source"] == "unknown":
            assert usage_record["amount"] is None
            assert usage_record["dimensions"] == []
            continue
        assert usage_record["amount"] is not None
        assert usage_record["dimensions"]
        calculated_amount = Decimal("0")
        for dimension in usage_record["dimensions"]:
            quote_dimension = quoted_meters[(usage_record["kind"], dimension["meter"])]
            assert Decimal(dimension["quantity"]) <= Decimal(quote_dimension["maximum_quantity"])
            calculated_amount += Decimal(dimension["quantity"]) * Decimal(quote_dimension["unit_price"])
        assert calculated_amount == Decimal(usage_record["amount"])

    if settlement["state"] == "settled":
        assert _parse_time(settlement["settled_at"]) >= max(dispatch_times)
        assert all(item["source"] != "unknown" for item in usage)
        actual = sum(Decimal(item["amount"]) for item in usage)
        assert actual == Decimal(settlement["actual_amount"])
        assert actual + Decimal(settlement["released_amount"]) == Decimal(reservation["reserved_amount"])
        assert settlement["actual_provenance_complete"] is True
    elif settlement["state"] == "released":
        assert settlement["release_phase"] == "post_dispatch"
        assert _parse_time(settlement["settled_at"]) >= max(dispatch_times)
        assert all(item["source"] != "unknown" and Decimal(item["amount"]) == 0 for item in usage)
        assert all(Decimal(dimension["quantity"]) == 0 for item in usage for dimension in item["dimensions"]), (
            "post-dispatch release contains nonzero usage quantity"
        )
        assert settlement["actual_amount"] == "0"
        assert Decimal(settlement["released_amount"]) == Decimal(reservation["reserved_amount"])
        assert settlement["actual_provenance_complete"] is True
    else:
        assert settlement["state"] == "outcome_unknown"
        assert _parse_time(settlement["observed_at"]) >= max(dispatch_times)
        assert any(item["source"] == "unknown" and item["amount"] is None for item in usage)
        assert settlement["actual_amount"] is None
        assert settlement["released_amount"] == "0"
        assert settlement["reservation_state"] == "remains_reserved"
        assert settlement["reconciliation_required"] is True
    assert settlement["atomic"] is True


def test_alpha2_schemas_are_valid_draft_2020_12(schemas: dict[str, dict]):
    ids = set()
    for schema in schemas.values():
        Draft202012Validator.check_schema(schema)
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["$id"] not in ids
        ids.add(schema["$id"])


def test_declared_positive_fixtures_are_complete_and_validate(schemas: dict[str, dict], registry: Registry):
    suite = _load(CONFORMANCE_DIR / "suite.json")
    declared = {entry["fixture"] for entry in suite["positive"]}
    observed = {path.relative_to(CONFORMANCE_DIR).as_posix() for path in POSITIVE_DIR.glob("*.json")}
    assert declared == observed
    for entry in suite["positive"]:
        _validate(_load(CONFORMANCE_DIR / entry["fixture"]), schemas[entry["schema"]], registry)


def test_pricing_and_route_plan_semantics_cover_each_terminal_state():
    plans = {path.name.removeprefix("route-plan.").removesuffix(".json"): _load(path) for path in POSITIVE_DIR.glob("route-plan.*.json")}
    lifecycles = {path.name.removeprefix("pricing.").removesuffix(".json"): _load(path) for path in POSITIVE_DIR.glob("pricing.*.json")}
    assert set(plans) == set(lifecycles)
    assert {
        (
            item["settlement"]["state"],
            item["settlement"].get("release_phase"),
            item["settlement"].get("release_reason"),
        )
        for item in lifecycles.values()
    } == {
        ("released", "pre_dispatch", "quote_expired"),
        ("released", "pre_dispatch", "cancelled_before_dispatch"),
        ("released", "post_dispatch", None),
        ("settled", None, None),
        ("outcome_unknown", None, None),
    }
    for name, lifecycle in lifecycles.items():
        _validate_route_semantics(plans[name])
        _validate_pricing_semantics(lifecycle, plans[name])


def test_alpha2_compatibility_is_exact_and_alpha1_dependencies_are_explicit(schemas: dict[str, dict], registry: Registry):
    compatibility = _load(SCHEMA_DIR / "compatibility.json")
    _validate(compatibility, schemas["compatibility.schema.json"], registry)
    assert compatibility["contracts"] == [
        {
            "schema_id": "reasoning.platform.pricing",
            "version": VERSION,
            "schema_path": "pricing.schema.json",
        },
        {
            "schema_id": "reasoning.platform.route-plan",
            "version": VERSION,
            "schema_path": "route-plan.schema.json",
        },
    ]
    assert set(compatibility["dependencies"].values()) == {"1.0.0-alpha.1"}
    assert compatibility["rules"]["prerelease_negotiation"] == "exact_version"
    assert compatibility["rules"]["alpha_1_pricing_migration"] == "negotiate_alpha_2_never_reinterpret_alpha_1_dispatch"
    mismatched_contract_path = copy.deepcopy(compatibility)
    mismatched_contract_path["contracts"][0]["schema_path"] = "route-plan.schema.json"
    with pytest.raises(ValidationError):
        _validate(
            mismatched_contract_path,
            schemas["compatibility.schema.json"],
            registry,
        )


def test_alpha1_and_alpha2_reject_cross_version_records(schemas: dict[str, dict], registry: Registry):
    alpha1_schemas = {path.name: _load(path) for path in sorted(ALPHA1_SCHEMA_DIR.glob("*.schema.json"))}
    alpha1_registry = Registry()
    for schema in alpha1_schemas.values():
        alpha1_registry = alpha1_registry.with_resource(schema["$id"], Resource.from_contents(schema))
    pairs = (
        (
            _load(ALPHA1_POSITIVE_DIR / "pricing.compute-released.json"),
            _load(POSITIVE_DIR / "pricing.compute-released-post-dispatch.json"),
            "pricing.schema.json",
        ),
        (
            _load(ALPHA1_POSITIVE_DIR / "route-plan.compute-released.json"),
            _load(POSITIVE_DIR / "route-plan.compute-released-post-dispatch.json"),
            "route-plan.schema.json",
        ),
    )
    for alpha1_record, alpha2_record, schema_name in pairs:
        with pytest.raises(ValidationError):
            _validate(alpha2_record, alpha1_schemas[schema_name], alpha1_registry)
        with pytest.raises(ValidationError):
            _validate(alpha1_record, schemas[schema_name], registry)


def test_all_negative_fixtures_are_executable_and_rejected(schemas: dict[str, dict], registry: Registry):
    cases = _load(FIXTURE_DIR / "negative" / "cases.json")["cases"]
    assert len(cases) >= 20
    assert len({case["id"] for case in cases}) == len(cases)
    for case in cases:
        instance = _mutate(_load(FIXTURE_DIR / case["base"]), case["mutations"])
        if case["validation"] == "schema":
            with pytest.raises(ValidationError, match=".+"):
                _validate(instance, schemas[case["schema"]], registry)
            continue
        assert case["validation"] == "pricing_semantic"
        _validate(instance, schemas[case["schema"]], registry)
        route_plan = _load(FIXTURE_DIR / case["route_plan"])
        with pytest.raises(AssertionError):
            _validate_pricing_semantics(instance, route_plan)


def test_chronology_fixture_covers_positive_relations_and_rejection_cases():
    chronology = _load(FIXTURE_DIR / "chronology" / "cases.json")
    for case in chronology["positive"]:
        lifecycle = _load(FIXTURE_DIR / case["fixture"])
        quote = lifecycle["quote"]
        reservation = lifecycle["reservation"]
        settlement = lifecycle["settlement"]
        assert settlement["release_reason"] == case["reason"]
        released_at = _parse_time(settlement["released_at"])
        if case["release_relation"] == "at_or_after_quote_expiry":
            assert released_at >= _parse_time(quote["expires_at"])
        else:
            assert case["release_relation"] == "after_reservation_before_quote_expiry"
            assert _parse_time(reservation["reserved_at"]) <= released_at
            assert released_at < _parse_time(quote["expires_at"])
    negative_ids = {case["id"] for case in _load(FIXTURE_DIR / "negative" / "cases.json")["cases"]}
    assert set(chronology["negative_case_ids"]) <= negative_ids


def test_canonicalization_fixture_is_reproducible():
    cases = _load(FIXTURE_DIR / "canonicalization" / "cases.json")
    assert cases["canonicalization"] == "RFC 8785 JSON Canonicalization Scheme"
    assert cases["encoding"] == "UTF-8"
    assert cases["hash_algorithm"] == "SHA-256"
    for case in cases["cases"]:
        assert case["digest"] == _jcs_digest(case["value"])


def test_bundle_integrity_fixture_rejects_member_mutations():
    manifest = _load(SCHEMA_DIR / "bundle-manifest.json")
    original_digest = manifest["bundle_digest"]
    cases = _load(FIXTURE_DIR / "integrity" / "cases.json")["cases"]
    assert {case["operation"] for case in cases} == {
        "replace_digest",
        "remove_member",
        "add_member",
    }
    for case in cases:
        mutated = copy.deepcopy(manifest)
        if case["operation"] == "replace_digest":
            member = next(item for item in mutated["members"] if item["path"] == case["member_path"])
            member["digest"] = case["replacement"]
            assert member["digest"] != _sha256_file(ROOT / member["path"])
        elif case["operation"] == "remove_member":
            mutated["members"] = [item for item in mutated["members"] if item["path"] != case["member_path"]]
            assert len(mutated["members"]) + 1 == len(manifest["members"])
        else:
            assert case["operation"] == "add_member"
            assert not (ROOT / case["member_path"]).exists()
            mutated["members"].append({"path": case["member_path"], "digest": case["replacement"]})
            mutated["members"].sort(key=lambda item: item["path"])
        digest_input = {key: value for key, value in mutated.items() if key != "bundle_digest"}
        assert _jcs_digest(digest_input) != original_digest


def test_alpha1_bundle_files_are_byte_identical_to_the_accepted_input():
    baseline = _load(FIXTURE_DIR / "compatibility" / "alpha1-file-digests.json")
    declared = {item["path"]: item["digest"] for item in baseline["files"]}
    observed_paths = {
        path.relative_to(ROOT).as_posix()
        for path in itertools.chain(
            ALPHA1_SCHEMA_DIR.rglob("*"),
            ALPHA1_CONFORMANCE_DIR.rglob("*"),
        )
        if path.is_file()
    }
    assert set(declared) == observed_paths
    for path, digest in declared.items():
        assert _sha256_file(ROOT / path) == digest


def test_alpha2_contract_bundle_manifest_is_complete_and_reproducible(schemas: dict[str, dict], registry: Registry):
    manifest = _load(SCHEMA_DIR / "bundle-manifest.json")
    _validate(manifest, schemas["contract-bundle.schema.json"], registry)
    member_paths = [entry["path"] for entry in manifest["members"]]
    assert member_paths == sorted(member_paths)
    assert len(member_paths) == len(set(member_paths))
    expected = {
        path.relative_to(ROOT).as_posix()
        for path in itertools.chain(
            SCHEMA_DIR.glob("*.schema.json"),
            [SCHEMA_DIR / "compatibility.json", CONFORMANCE_DIR / "suite.json"],
            FIXTURE_DIR.rglob("*.json"),
        )
    }
    assert set(member_paths) == expected
    for member in manifest["members"]:
        assert member["digest"] == _sha256_file(ROOT / member["path"])
    digest_input = {key: value for key, value in manifest.items() if key != "bundle_digest"}
    assert manifest["bundle_digest"] == _jcs_digest(digest_input)


def test_migration_guidance_requires_negotiation_without_alpha1_reinterpretation():
    guidance = " ".join((SCHEMA_DIR / "README.md").read_text(encoding="utf-8").split())
    assert "Do not delete a synthetic alpha.1 dispatch and relabel the record." in guidance
    assert "Consumers that support only alpha.1 must reject alpha.2." in guidance
    assert "Post-dispatch release" in guidance
    assert "`outcome_unknown` retain their dispatch, usage, and provenance" in guidance
