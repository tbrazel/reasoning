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
SCHEMA_DIR = ROOT / "contracts" / "platform" / "v1alpha1"
CONFORMANCE_DIR = ROOT / "conformance" / "platform" / "v1alpha1"
FIXTURE_DIR = CONFORMANCE_DIR / "fixtures"
POSITIVE_DIR = FIXTURE_DIR / "positive"
VERSION = "1.0.0-alpha.1"


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256_file(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _fixture_jcs_digest(instance) -> str:
    # Contract fixtures contain only ASCII strings and exactly represented JSON
    # integers, so this is byte-for-byte RFC 8785/JCS for the fixture domain.
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
    assert len(loaded) == 10
    return loaded


@pytest.fixture(scope="module")
def registry(schemas: dict[str, dict]) -> Registry:
    result = Registry()
    for schema in schemas.values():
        result = result.with_resource(schema["$id"], Resource.from_contents(schema))
    return result


def _validate(instance, schema: dict, registry: Registry) -> None:
    Draft202012Validator(schema, registry=registry, format_checker=FormatChecker()).validate(instance)


def _validate_pricing_semantics(lifecycle: dict, route_plan: dict) -> None:
    quote = lifecycle["quote"]
    reservation = lifecycle["reservation"]
    dispatches = lifecycle["dispatches"]
    usage = lifecycle["usage"]
    settlement = lifecycle["settlement"]
    operation = lifecycle["operation"]

    assert lifecycle["pricing_lifecycle_id"] == route_plan["pricing_lifecycle_id"]
    assert operation["route_plan_id"] == route_plan["route_plan_id"]
    assert operation["operation_id"] == route_plan["operation_id"]
    assert operation["actor"] == route_plan["actor"]
    assert operation["tenant_id"] == route_plan["tenant_id"]
    assert operation["project_id"] == route_plan["persistence"]["project_id"]

    assert quote["quote_id"] == reservation["quote_id"]
    assert {item["reservation_id"] for item in dispatches} == {reservation["reservation_id"]}
    assert settlement["reservation_id"] == reservation["reservation_id"]
    assert _parse_time(quote["issued_at"]) <= _parse_time(reservation["reserved_at"])
    assert all(_parse_time(reservation["reserved_at"]) <= _parse_time(item["dispatched_at"]) for item in dispatches)
    assert all(_parse_time(item["dispatched_at"]) <= _parse_time(quote["expires_at"]) for item in dispatches)

    sequences = [quote["sequence"], reservation["sequence"]]
    sequences.extend(item["sequence"] for item in dispatches)
    sequences.extend(item["sequence"] for item in usage)
    sequences.append(settlement["sequence"])
    assert sequences == sorted(sequences)
    assert len(sequences) == len(set(sequences))

    for item in quote["dimensions"]:
        assert Decimal(item["maximum_amount"]) == Decimal(item["maximum_quantity"]) * Decimal(item["unit_price"])
    quote_maximum = sum(Decimal(item["maximum_amount"]) for item in quote["dimensions"])
    assert quote_maximum == Decimal(quote["maximum_amount"])
    assert Decimal(reservation["reserved_amount"]) <= quote_maximum
    assert settlement["usage_digest"] == _fixture_jcs_digest(usage)
    quoted_meters = {(item["kind"], item["meter"]): item for item in quote["dimensions"]}
    assert len(quoted_meters) == len(quote["dimensions"])
    for usage_record in usage:
        if usage_record["kind"] == "inference":
            provenance = usage_record["provenance"]
            assert provenance["route_id"] == route_plan["inference"]["route_id"]
            assert provenance["provider"] == route_plan["inference"]["provider"]
            assert provenance["model"] == route_plan["inference"]["model"]
            assert provenance["request_id"] in {item["request_id"] for item in dispatches if item["kind"] == "inference"}
        else:
            provenance = usage_record["provenance"]
            assert provenance["target_id"] == route_plan["compute"]["target_id"]
            assert provenance["job_id"] in {item["job_id"] for item in dispatches if item["kind"] == "compute"}
            image_keys = {
                "image_digest": "digest",
                "capabilities_digest": "capabilities_digest",
                "attestation_digest": "attestation_digest",
            }
            for provenance_key, route_key in image_keys.items():
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
        assert all(item["source"] != "unknown" for item in usage)
        actual = sum(Decimal(item["amount"]) for item in usage)
        assert actual == Decimal(settlement["actual_amount"])
        assert actual + Decimal(settlement["released_amount"]) == Decimal(reservation["reserved_amount"])
        assert settlement["actual_provenance_complete"] is True
        assert settlement["atomic"] is True
    elif settlement["state"] == "released":
        assert all(item["source"] != "unknown" and Decimal(item["amount"]) == 0 for item in usage)
        assert all(
            Decimal(dimension["quantity"]) == 0 for item in usage for dimension in item["dimensions"]
        ), "released settlement contains nonzero usage quantity"
        assert settlement["actual_amount"] == "0"
        assert Decimal(settlement["released_amount"]) == Decimal(reservation["reserved_amount"])
        assert settlement["actual_provenance_complete"] is True
        assert settlement["atomic"] is True
    else:
        assert settlement["state"] == "outcome_unknown"
        assert any(item["source"] == "unknown" and item["amount"] is None for item in usage)
        assert settlement["actual_amount"] is None
        assert settlement["released_amount"] == "0"
        assert settlement["reservation_state"] == "remains_reserved"
        assert settlement["reconciliation_required"] is True
        assert settlement["atomic"] is True


def _validate_project_semantics(bundle: dict) -> None:
    assert len({transfer["transfer_id"] for transfer in bundle["transfer_manifests"]}) == len(bundle["transfer_manifests"])
    for transfer in bundle["transfer_manifests"]:
        assert transfer["binding"]["project_id"] == bundle["project"]["project_id"]
        assert transfer["binding"]["project_revision_digest"] == bundle["project"]["revision_digest"]
        assert transfer["approval"]["actor"] == transfer["binding"]["actor"]
        assert transfer["disclosure_digest"] == _fixture_jcs_digest(transfer["disclosure"])
        assert transfer["approval"]["approved_disclosure_digest"] == transfer["disclosure_digest"]
    if bundle["linkage"]["state"] == "linked":
        link_policy = {
            key: bundle["linkage"][key]
            for key in (
                "tenant_id",
                "cloud_project_id",
                "authorized_data_classes",
                "retained_output_classes",
                "future_transfers_revocable",
            )
        }
        assert bundle["linkage"]["link_policy_digest"] == _fixture_jcs_digest(link_policy)
        assert bundle["linkage"]["approval"]["approved_link_policy_digest"] == bundle["linkage"]["link_policy_digest"]


def _validate_route_semantics(plan: dict, bundle: dict) -> None:
    _validate_project_semantics(bundle)
    persistence = plan["persistence"]
    assert persistence["project_bundle_id"] == bundle["bundle_id"]
    assert persistence["project_id"] == bundle["project"]["project_id"]
    assert persistence["project_revision_digest"] == bundle["project"]["revision_digest"]
    assert plan["actor"] == bundle["export"]["created_by"]
    hosted = "hosted" in {plan["harness"]["location"], plan["inference"]["location"], plan["compute"]["location"]}
    if hosted:
        transfer_id = persistence["transfer_manifest_id"]
        transfers = [transfer for transfer in bundle["transfer_manifests"] if transfer["transfer_id"] == transfer_id]
        assert len(transfers) == 1
        transfer = transfers[0]
        assert transfer["operation_id"] == plan["operation_id"]
        assert transfer["binding"] == {
            "actor": plan["actor"],
            "tenant_id": plan["tenant_id"],
            "project_id": persistence["project_id"],
            "project_revision_digest": persistence["project_revision_digest"],
        }
        assert transfer["approval"]["actor"] == plan["actor"]
        assert transfer["approval"]["approved_disclosure_digest"] == transfer["disclosure_digest"]
    else:
        assert "transfer_manifest_id" not in persistence


def _validate_connector_semantics(connector: dict, route_plan: dict, lifecycle: dict) -> None:
    lifetime = _parse_time(connector["expires_at"]) - _parse_time(connector["issued_at"])
    assert 0 < lifetime.total_seconds() <= 900
    assert connector["audience"].startswith("https://")
    assert connector["binding"]["max_jobs"] == 1
    assert connector["capability_set"]["scope"] == "reasoning.executor.compute-job-lifecycle"
    assert all(
        connector["capability_set"][key] is False
        for key in ("account_access", "billing_access", "inference_access", "project_export_access")
    )
    assert all(connector["presentation"][key] is False for key in connector["presentation"])
    policy_keys = (
        "connector_grant_id",
        "issued_at",
        "expires_at",
        "audience",
        "binding",
        "capability_set",
        "budget",
        "replay_protection",
        "presentation",
    )
    assert connector["grant_policy_digest"] == _fixture_jcs_digest({key: connector[key] for key in policy_keys})
    assert connector["approval"]["approved_grant_policy_digest"] == connector["grant_policy_digest"]
    assert connector["approval"]["actor"] == connector["binding"]["actor"] == route_plan["actor"]

    binding = connector["binding"]
    assert route_plan["compute"]["connector_grant_id"] == connector["connector_grant_id"]
    assert binding["tenant_id"] == route_plan["tenant_id"] == lifecycle["operation"]["tenant_id"]
    assert binding["project_id"] == route_plan["persistence"]["project_id"] == lifecycle["operation"]["project_id"]
    assert binding["operation_id"] == route_plan["operation_id"] == lifecycle["operation"]["operation_id"]
    assert binding["route_plan_id"] == route_plan["route_plan_id"] == lifecycle["operation"]["route_plan_id"]
    assert binding["harness_id"] == route_plan["harness"]["harness_id"]
    assert binding["target_id"] == route_plan["compute"]["target_id"]
    if connector["budget"]["kind"] == "monetary":
        assert connector["budget"]["reservation_id"] == lifecycle["reservation"]["reservation_id"]
        assert Decimal(connector["budget"]["maximum_amount"]) == Decimal(lifecycle["reservation"]["reserved_amount"])
    else:
        assert lifecycle["reservation"]["funding"] == "free_quota"
        compute_limits = {
            item["meter"]: Decimal(item["maximum_quantity"]) for item in lifecycle["quote"]["dimensions"] if item["kind"] == "compute"
        }
        assert compute_limits.get("vcpu_seconds", Decimal("0")) <= Decimal(connector["budget"]["maximum_vcpu_seconds"])
        assert compute_limits.get("memory_gib_seconds", Decimal("0")) <= Decimal(connector["budget"]["maximum_memory_gib_seconds"])


def test_platform_schemas_are_valid_draft_2020_12(schemas: dict[str, dict]):
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


def test_compatibility_versions_and_dependencies_are_explicit(schemas: dict[str, dict], registry: Registry):
    compatibility = _load(SCHEMA_DIR / "compatibility.json")
    _validate(compatibility, schemas["compatibility.schema.json"], registry)

    public_contracts = {
        _load(path)["properties"]["schema_id"]["const"]: path.name
        for path in SCHEMA_DIR.glob("*.schema.json")
        if "schema_id" in _load(path).get("properties", {})
        and _load(path)["properties"]["schema_id"].get("const", "").startswith("reasoning.platform.")
        and path.name not in {"compatibility.schema.json", "contract-bundle.schema.json"}
    }
    declared = {entry["schema_id"]: entry["schema_path"] for entry in compatibility["contracts"]}
    assert declared == public_contracts
    assert {entry["version"] for entry in compatibility["contracts"]} == {VERSION}
    assert set(compatibility["dependencies"].values()) == {VERSION}
    assert compatibility["rules"] == {
        "prerelease_negotiation": "exact_version",
        "unknown_schema": "reject",
        "unknown_required_field": "reject",
        "additive_change": "new_version_and_explicit_consumer_support",
        "breaking_change": "new_major_version_with_compatibility_window",
        "legacy_selection_migration": "separate_observed_fields_or_explicit_legacy_unknown_never_infer",
        "local_project_migration": "remain_local_and_unlinked",
        "digest_canonicalization": "RFC 8785 JSON Canonicalization Scheme",
        "digest_encoding": "UTF-8",
        "digest_algorithm": "SHA-256",
    }


def test_project_sign_in_does_not_link_and_transfers_are_exactly_approved():
    unlinked = _load(POSITIVE_DIR / "project-bundle.local-unlinked.json")
    linked = _load(POSITIVE_DIR / "project-bundle.local-linked.json")

    assert unlinked["project"]["origin"] == "local"
    assert unlinked["linkage"] == {"state": "unlinked"}
    _validate_project_semantics(unlinked)
    _validate_project_semantics(linked)
    transfer = unlinked["transfer_manifests"][0]
    assert transfer["mode"] == "one_step"
    assert transfer["future_transfers_authorized"] is False
    assert transfer["disclosure_digest"] == _fixture_jcs_digest(transfer["disclosure"])
    assert transfer["approval"]["actor"]["type"] == "user"
    assert transfer["approval"]["approved_disclosure_digest"] == transfer["disclosure_digest"]
    assert {item["artifact_id"] for item in transfer["disclosure"]["inputs"]} == {
        "artifact_polynomial_system",
        "artifact_problem_statement",
    }
    assert {item["kind"] for item in transfer["disclosure"]["retained_cloud_data"]} == {
        "outputs",
        "logs",
        "operation_metadata",
        "usage",
        "provenance",
    }

    assert linked["linkage"]["state"] == "linked"
    assert linked["linkage"]["approval"]["actor"]["type"] == "user"
    assert linked["linkage"]["approval"]["scope"] == "project_link"
    assert linked["linkage"]["future_transfers_revocable"] is True
    assert linked["transfer_manifests"][0]["mode"] == "linked_project"
    assert linked["transfer_manifests"][0]["future_transfers_authorized"] is True

    banned_keys = {
        "access_token",
        "api_key",
        "authorization",
        "cookie",
        "credential",
        "password",
        "private_key",
        "refresh_token",
        "secret",
        "session_token",
    }
    for bundle in (unlinked, linked):
        assert banned_keys.isdisjoint(_walk_keys(bundle))
        assert bundle["export"]["security_material"] == "excluded"
        assert bundle["export"]["server_policy"] == "excluded"


def test_harness_inference_and_compute_are_independent_and_digest_bound():
    all_plans = [_load(path) for path in sorted(POSITIVE_DIR.glob("route-plan.*.json"))]
    matrix_ids = {f"route_plan_{''.join(locations)}" for locations in itertools.product(("l", "h"), repeat=3)}
    plans = [plan for plan in all_plans if plan["route_plan_id"] in matrix_ids]
    assert len(plans) == 8
    observed = {(plan["harness"]["location"], plan["inference"]["location"], plan["compute"]["location"]) for plan in plans}
    assert observed == set(itertools.product(("local", "hosted"), repeat=3))

    harnesses = {
        descriptor["harness_id"]: (descriptor, _sha256_file(path))
        for path in POSITIVE_DIR.glob("harness-provider.*.json")
        for descriptor in [_load(path)]
    }
    routes = {
        route["route_id"]: (route, _sha256_file(path)) for path in POSITIVE_DIR.glob("inference-route.*.json") for route in [_load(path)]
    }
    targets = {
        target["target_id"]: (target, _sha256_file(path)) for path in POSITIVE_DIR.glob("compute-target.*.json") for target in [_load(path)]
    }
    bundles = {bundle["bundle_id"]: bundle for path in POSITIVE_DIR.glob("project-bundle.*.json") for bundle in [_load(path)]}

    for plan in all_plans:
        harness, harness_digest = harnesses[plan["harness"]["harness_id"]]
        route, route_digest = routes[plan["inference"]["route_id"]]
        target, target_digest = targets[plan["compute"]["target_id"]]
        assert plan["harness"]["descriptor_digest"] == harness_digest
        assert plan["inference"]["route_digest"] == route_digest
        assert plan["compute"]["target_digest"] == target_digest
        assert plan["inference"]["route_id"] in harness["supported_inference_route_ids"]
        assert plan["compute"]["target_id"] in harness["supported_compute_target_ids"]
        assert plan["harness"]["harness_id"] in route["harness_ids"]
        assert plan["harness"]["harness_id"] in target["supported_harness_ids"]
        _validate_route_semantics(plan, bundles[plan["persistence"]["project_bundle_id"]])
        assert plan["persistence"]["location"] == "local"
        if "hosted" in {plan["harness"]["location"], plan["inference"]["location"], plan["compute"]["location"]}:
            assert "transfer_manifest_id" in plan["persistence"]
        if plan["harness"]["location"] == "local" and plan["compute"]["location"] == "hosted":
            assert plan["compute"]["connector_grant_id"] == "connector_grant_hybrid_001"
        else:
            assert "connector_grant_id" not in plan["compute"]
        assert "backend" not in set(_walk_keys(plan))


def test_local_harness_connector_is_downscoped_expiring_and_lifecycle_bound():
    connector = _load(POSITIVE_DIR / "local-harness-connector.json")
    route_plan = _load(POSITIVE_DIR / "route-plan.lhh.json")
    pricing = _load(POSITIVE_DIR / "pricing.hybrid-settled.json")

    _validate_connector_semantics(connector, route_plan, pricing)
    assert connector["binding"]["route_plan_id"] == route_plan["route_plan_id"]
    assert connector["binding"]["operation_id"] == route_plan["operation_id"]
    assert connector["binding"]["harness_id"] == route_plan["harness"]["harness_id"]
    assert connector["binding"]["target_id"] == route_plan["compute"]["target_id"]
    assert connector["capability_set"] == {
        "scope": "reasoning.executor.compute-job-lifecycle",
        "actions": ["submit", "read", "events", "artifacts", "cancel"],
        "account_access": False,
        "billing_access": False,
        "inference_access": False,
        "project_export_access": False,
    }
    assert connector["budget"]["reservation_id"] == pricing["reservation"]["reservation_id"]
    assert Decimal(connector["budget"]["maximum_amount"]) == Decimal(pricing["reservation"]["reserved_amount"])
    assert connector["presentation"] == {
        "exchangeable": False,
        "refreshable": False,
        "reusable_account_material": False,
        "inference_material": False,
    }


def test_pricing_lifecycle_reserves_then_atomically_settles_actual_provenance():
    plans = {plan["route_plan_id"]: plan for path in POSITIVE_DIR.glob("route-plan.*.json") for plan in [_load(path)]}
    lifecycles = [_load(path) for path in sorted(POSITIVE_DIR.glob("pricing.*.json"))]
    assert {lifecycle["settlement"]["state"] for lifecycle in lifecycles} == {
        "settled",
        "released",
        "outcome_unknown",
    }
    for lifecycle in lifecycles:
        _validate_pricing_semantics(lifecycle, plans[lifecycle["operation"]["route_plan_id"]])

    lifecycle = next(item for item in lifecycles if item["settlement"]["state"] == "settled")
    route_plan = plans[lifecycle["operation"]["route_plan_id"]]
    assert lifecycle["operation"]["kind"] == "hybrid"
    assert {item["kind"] for item in lifecycle["quote"]["dimensions"]} == {"inference", "compute"}
    assert {item["kind"] for item in lifecycle["dispatches"]} == {"inference", "compute"}
    assert {item["kind"] for item in lifecycle["usage"]} == {"inference", "compute"}
    assert route_plan["pricing_lifecycle_id"] == lifecycle["pricing_lifecycle_id"]


def test_all_negative_fixtures_are_executable_and_rejected(schemas: dict[str, dict], registry: Registry):
    cases = _load(FIXTURE_DIR / "negative" / "cases.json")["cases"]
    assert len(cases) >= 14
    assert len({case["id"] for case in cases}) == len(cases)

    for case in cases:
        instance = _mutate(_load(FIXTURE_DIR / case["base"]), case["mutations"])
        if case["validation"] == "schema":
            with pytest.raises(ValidationError, match=".+"):
                _validate(instance, schemas[case["schema"]], registry)
        elif case["validation"] == "pricing_semantic":
            _validate(instance, schemas[case["schema"]], registry)
            route_plan = _load(FIXTURE_DIR / case.get("route_plan", "positive/route-plan.lhh.json"))
            with pytest.raises(AssertionError, match=case.get("error_match")):
                _validate_pricing_semantics(instance, route_plan)
        elif case["validation"] == "project_semantic":
            _validate(instance, schemas[case["schema"]], registry)
            with pytest.raises(AssertionError):
                _validate_project_semantics(instance)
        elif case["validation"] == "connector_semantic":
            route_plan = _load(FIXTURE_DIR / case.get("route_plan", "positive/route-plan.lhh.json"))
            lifecycle = _load(FIXTURE_DIR / case.get("pricing", "positive/pricing.hybrid-settled.json"))
            _validate(instance, schemas[case["schema"]], registry)
            with pytest.raises(AssertionError):
                _validate_connector_semantics(instance, route_plan, lifecycle)
        else:
            assert case["validation"] == "route_semantic"
            bundle = _load(FIXTURE_DIR / case.get("project_bundle", "positive/project-bundle.local-unlinked.json"))
            _validate(instance, schemas[case["schema"]], registry)
            with pytest.raises(AssertionError):
                _validate_route_semantics(instance, bundle)


def test_contract_bundle_manifest_is_complete_and_reproducible(schemas: dict[str, dict], registry: Registry):
    manifest_path = SCHEMA_DIR / "bundle-manifest.json"
    manifest = _load(manifest_path)
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
    assert manifest["bundle_digest"] == _fixture_jcs_digest(digest_input)
