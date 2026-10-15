from __future__ import annotations

import ast
import base64
import copy
import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "contracts" / "executor" / "v1alpha1"
CLOUD_CONTRACT = ROOT / "contracts" / "cloud" / "v1alpha1" / "execution.openapi.yaml"
CONFORMANCE_DIR = ROOT / "conformance" / "executor" / "v1alpha1"
FLOW_DIR = CONFORMANCE_DIR / "fixtures" / "deterministic-fake-local"
ACCEPTED_LEADING_SIGNATURE = "e281cfc799942f167255604f4a416bf66c624a92f91eac12598a921bf225919a"


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _literal_assignments(path: Path) -> dict[str, object]:
    assignments = {}
    for statement in ast.parse(path.read_text(encoding="utf-8"), filename=str(path)).body:
        if not isinstance(statement, ast.Assign) or len(statement.targets) != 1 or not isinstance(statement.targets[0], ast.Name):
            continue
        try:
            assignments[statement.targets[0].id] = ast.literal_eval(statement.value)
        except (TypeError, ValueError):
            continue
    return assignments


def _loaded_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)}


@pytest.fixture(scope="module")
def schemas() -> dict[str, dict]:
    loaded = {path.name: _load(path) for path in sorted(SCHEMA_DIR.glob("*.schema.json"))}
    assert loaded
    return loaded


@pytest.fixture(scope="module")
def registry(schemas: dict[str, dict]) -> Registry:
    result = Registry()
    for schema in schemas.values():
        result = result.with_resource(schema["$id"], Resource.from_contents(schema))
    return result


def _validate(instance, schema: dict, registry: Registry) -> None:
    Draft202012Validator(schema, registry=registry, format_checker=FormatChecker()).validate(instance)


def _sha256_file(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _fixture_jcs_digest(instance: dict) -> str:
    # These fixtures use only ASCII object keys and JSON numbers exactly representable
    # as integers, so this encoding is byte-for-byte RFC 8785/JCS.
    canonical = json.dumps(instance, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def _tamper_committed_json(path: Path, pointer: str, replacement) -> tuple[dict, bytes]:
    original_text = path.read_text(encoding="utf-8")
    instance = json.loads(original_text)
    parts = [part.replace("~1", "/").replace("~0", "~") for part in pointer.removeprefix("/").split("/")]
    target = instance
    for part in parts[:-1]:
        target = target[int(part)] if isinstance(target, list) else target[part]
    final = parts[-1]
    current = target[int(final)] if isinstance(target, list) else target[final]
    assert current != replacement

    current_token = json.dumps(current, ensure_ascii=False)
    replacement_token = json.dumps(replacement, ensure_ascii=False)
    if isinstance(target, list):
        needle = current_token
        substituted = replacement_token
    else:
        needle = f"{json.dumps(final)}: {current_token}"
        substituted = f"{json.dumps(final)}: {replacement_token}"
    assert original_text.count(needle) == 1

    tampered_text = original_text.replace(needle, substituted, 1)
    tampered = json.loads(tampered_text)
    tampered_target = tampered
    for part in parts[:-1]:
        tampered_target = tampered_target[int(part)] if isinstance(tampered_target, list) else tampered_target[part]
    observed = tampered_target[int(final)] if isinstance(tampered_target, list) else tampered_target[final]
    assert observed == replacement
    return tampered, tampered_text.encode("utf-8")


def _validate_trace(events: list[dict], state_machine: dict, event_schema: dict, registry: Registry) -> None:
    assert events
    transitions = {(transition["from"], transition["to"], transition["event"]) for transition in state_machine["transitions"]}
    observations = state_machine["observations_without_transition"]
    event_ids: set[str] = set()
    cursors: set[str] = set()
    previous_state = None

    for expected_sequence, event in enumerate(events, start=1):
        _validate(event, event_schema, registry)
        assert event["sequence"] == expected_sequence
        assert event["event_id"] not in event_ids
        assert event["cursor"] not in cursors
        event_ids.add(event["event_id"])
        cursors.add(event["cursor"])

        if previous_state is None:
            assert event["state"] == state_machine["initial_state"]
            assert event["type"] in observations
            assert event["state"] in observations[event["type"]]
        elif event["state"] == previous_state:
            assert event["type"] in observations
            assert event["state"] in observations[event["type"]]
        else:
            assert (previous_state, event["state"], event["type"]) in transitions
        previous_state = event["state"]


def test_executor_schemas_are_valid_draft_2020_12(schemas: dict[str, dict]):
    ids = set()
    for schema in schemas.values():
        Draft202012Validator.check_schema(schema)
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["$id"] not in ids
        ids.add(schema["$id"])


def test_fake_and_local_fixtures_validate_against_the_same_contract(schemas: dict[str, dict], registry: Registry):
    comparable_fields = (
        "actor",
        "tenant_id",
        "project_id",
        "run_id",
        "step_id",
        "submitted_at",
        "image",
        "command",
        "inputs",
        "resources",
        "deadline",
        "cancellation",
        "network_policy",
        "outputs",
        "log_policy",
        "provenance_required",
    )
    jobs = {}

    for profile in ("fake", "local"):
        job = _load(FLOW_DIR / f"{profile}.job.json")
        events = _load(FLOW_DIR / f"{profile}.events.json")
        result = _load(FLOW_DIR / f"{profile}.result.json")

        _validate(job, schemas["job-spec.schema.json"], registry)
        for event in events:
            _validate(event, schemas["job-event.schema.json"], registry)
        _validate(result, schemas["job-result.schema.json"], registry)

        assert {event["job_id"] for event in events} == {job["job_id"]}
        assert result["job_id"] == job["job_id"]
        assert result["provenance"]["executor"] == job["executor"]
        assert result["provenance"]["environment"]["image_digest"] == job["image"]["digest"]
        assert result["provenance"]["environment"]["capabilities_digest"] == job["image"]["capabilities_digest"]
        assert result["provenance"]["environment"]["attestation_digest"] == job["image"]["attestation_digest"]
        jobs[profile] = job

    assert jobs["fake"]["executor"]["target_kind"] == "fake"
    assert jobs["local"]["executor"]["target_kind"] == "local"
    assert {field: jobs["fake"][field] for field in comparable_fields} == {field: jobs["local"][field] for field in comparable_fields}


def test_durable_job_projection_validates(schemas: dict[str, dict], registry: Registry):
    status = _load(FLOW_DIR / "fake.status.json")
    job = _load(FLOW_DIR / "fake.job.json")
    _validate(status, schemas["job-status.schema.json"], registry)
    assert status["spec_digest"] == _fixture_jcs_digest(job)
    assert status["state"] == "queued"
    assert status["delivery_attempt"] == 0
    assert status["last_event_cursor"] == "opaque:fake:001"


def test_conformance_traces_follow_the_durable_state_machine(schemas: dict[str, dict], registry: Registry):
    state_machine = _load(SCHEMA_DIR / "state-machine.yaml")
    traces = [
        FLOW_DIR / "fake.events.json",
        FLOW_DIR / "local.events.json",
        CONFORMANCE_DIR / "fixtures" / "failure" / "events.json",
        CONFORMANCE_DIR / "fixtures" / "cancellation" / "events.json",
        CONFORMANCE_DIR / "fixtures" / "cancellation" / "running-cancellation-wins.events.json",
        CONFORMANCE_DIR / "fixtures" / "cancellation" / "running-completion-wins.events.json",
    ]

    for trace in traces:
        _validate_trace(_load(trace), state_machine, schemas["job-event.schema.json"], registry)

    assert state_machine["submission"] == {
        "persist_before_acknowledgement": True,
        "idempotency_scope": ["actor.type", "actor.id", "tenant_id", "project_id", "idempotency_key"],
        "same_key_same_spec": "return_existing_job",
        "same_key_different_spec": "reject_conflict",
        "spec_digest": {
            "canonicalization": "RFC 8785 JSON Canonicalization Scheme",
            "encoding": "UTF-8",
            "hash": "SHA-256",
            "wire_format": "sha256:<lowercase-hex>",
        },
    }
    assert state_machine["delivery"]["semantics"] == "at_least_once"
    assert state_machine["delivery"]["redelivery_must_not_rewind_state"] is True
    assert state_machine["replay"]["cursor_is_opaque"] is True
    assert state_machine["replay"]["after_is_exclusive"] is True
    assert state_machine["replay"]["deduplicate_by"] == "event_id"
    assert state_machine["client_disconnect"]["causes_state_transition"] is False
    assert state_machine["client_disconnect"]["implies_cancellation"] is False
    assert state_machine["terminal_states"] == ["succeeded", "failed", "cancelled"]
    assert state_machine["reconciliation_states"] == ["outcome_unknown"]

    cancellation = state_machine["cancellation"]
    assert cancellation["request_idempotency_scope"] == [
        "actor.type",
        "actor.id",
        "tenant_id",
        "project_id",
        "idempotency_key",
    ]
    assert cancellation["request_key_binds"] == ["job_id"]
    assert cancellation["same_key_same_job"] == "return_existing_cancellation_request"
    assert cancellation["same_key_different_job"] == "reject_conflict"
    assert cancellation["retry_semantics"] == "at_least_once_request_delivery_with_single_durable_intent"
    assert cancellation["terminal_race"] == {
        "rule": "first_terminal_transition_wins",
        "storage_requirement": "compare_and_set_from_nonterminal_state",
        "loser_behavior": "return_current_terminal_job_without_appending_a_second_terminal_event",
    }

    race_dir = CONFORMANCE_DIR / "fixtures" / "cancellation"
    race_expectations = {
        "running-cancellation-wins.events.json": ("job.cancelled", "cancelled"),
        "running-completion-wins.events.json": ("job.succeeded", "succeeded"),
    }
    for filename, expected in race_expectations.items():
        race = _load(race_dir / filename)
        terminal = [event for event in race if event["state"] in state_machine["terminal_states"]]
        assert [(event["type"], event["state"]) for event in terminal] == [expected]


def test_artifact_bytes_match_committed_metadata(schemas: dict[str, dict], registry: Registry):
    artifact_bytes = (FLOW_DIR / "output" / "result.json").read_bytes()
    expected_digest = f"sha256:{hashlib.sha256(artifact_bytes).hexdigest()}"

    for profile in ("fake", "local"):
        result = _load(FLOW_DIR / f"{profile}.result.json")
        artifact = result["artifacts"][0]
        _validate(artifact, schemas["artifact.schema.json"], registry)
        assert artifact["digest"] == expected_digest
        assert artifact["size_bytes"] == len(artifact_bytes)
        assert result["provenance"]["output_digests"] == [expected_digest]
        committed = next(event for event in _load(FLOW_DIR / f"{profile}.events.json") if event["type"] == "artifact.committed")
        assert committed["payload"] == {
            "artifact_id": artifact["artifact_id"],
            "digest": artifact["digest"],
            "size_bytes": artifact["size_bytes"],
            "media_type": artifact["media_type"],
            "provenance_id": artifact["provenance_id"],
        }
        assert artifact["provenance_id"] == result["provenance"]["provenance_id"]


def test_capability_manifest_and_external_attestation_validate(schemas: dict[str, dict], registry: Registry):
    capability_path = FLOW_DIR / "image-capabilities.json"
    attestation_path = FLOW_DIR / "image-attestation.json"
    sbom_path = FLOW_DIR / "sbom.spdx.json"
    oci_config_path = FLOW_DIR / "oci-config.json"
    oci_manifest_path = FLOW_DIR / "oci-manifest.json"
    capabilities = _load(FLOW_DIR / "image-capabilities.json")
    attestation = _load(FLOW_DIR / "image-attestation.json")
    oci_manifest = _load(oci_manifest_path)
    _validate(capabilities, schemas["image-capabilities.schema.json"], registry)
    _validate(attestation, schemas["image-attestation.schema.json"], registry)

    assert "image_digest" not in capabilities
    assert attestation["image_digest"] == _sha256_file(oci_manifest_path)
    assert attestation["capabilities_digest"] == _sha256_file(capability_path)
    assert attestation["sbom_digest"] == _sha256_file(sbom_path)
    assert oci_manifest["config"]["digest"] == _sha256_file(oci_config_path)
    assert oci_manifest["config"]["size"] == len(oci_config_path.read_bytes())

    signature = attestation["signature"]
    assert signature["algorithm"] == "ed25519"
    assert signature["encoding"] == "base64"
    assert len(base64.b64decode(signature["value"], validate=True)) == 64
    assert attestation["verification_status"] == "unverified_fixture"

    for profile in ("fake", "local"):
        job = _load(FLOW_DIR / f"{profile}.job.json")
        result = _load(FLOW_DIR / f"{profile}.result.json")
        assert job["image"] == {
            "repository": "reasoning/fixture",
            "digest": attestation["image_digest"],
            "capabilities_digest": attestation["capabilities_digest"],
            "attestation_digest": _sha256_file(attestation_path),
        }
        assert result["provenance"]["environment"] == {
            "image_digest": job["image"]["digest"],
            "capabilities_digest": job["image"]["capabilities_digest"],
            "attestation_digest": job["image"]["attestation_digest"],
            "runner_version": "1.0.0-alpha.1",
        }

    circular_manifest = copy.deepcopy(capabilities)
    circular_manifest["image_digest"] = attestation["image_digest"]
    with pytest.raises(ValidationError):
        _validate(circular_manifest, schemas["image-capabilities.schema.json"], registry)


def test_integrity_tamper_vectors_are_schema_valid_but_break_bindings(schemas: dict[str, dict], registry: Registry):
    integrity_dir = CONFORMANCE_DIR / "fixtures" / "integrity"
    vectors = _load(integrity_dir / "cases.json")
    base_dir = (integrity_dir / vectors["base_path"]).resolve()
    attestation = _load(base_dir / "image-attestation.json")
    job = _load(base_dir / "fake.job.json")
    status = _load(base_dir / "fake.status.json")
    detected = set()

    for case in vectors["cases"]:
        target_path = base_dir / case["target"]
        tampered, tampered_bytes = _tamper_committed_json(target_path, case["json_pointer"], case["replacement"])
        assert tampered_bytes != target_path.read_bytes()

        if case["target"] == "image-capabilities.json":
            _validate(tampered, schemas["image-capabilities.schema.json"], registry)
            digest = f"sha256:{hashlib.sha256(tampered_bytes).hexdigest()}"
            assert digest != attestation["capabilities_digest"]
        elif case["target"] == "image-attestation.json":
            _validate(tampered, schemas["image-attestation.schema.json"], registry)
            digest = f"sha256:{hashlib.sha256(tampered_bytes).hexdigest()}"
            assert digest != job["image"]["attestation_digest"]
            assert tampered["sbom_digest"] != _sha256_file(base_dir / "sbom.spdx.json")
        elif case["target"] == "fake.job.json":
            _validate(tampered, schemas["job-spec.schema.json"], registry)
            assert _fixture_jcs_digest(tampered) != status["spec_digest"]
        else:
            pytest.fail(f"undeclared tamper target: {case['target']}")
        detected.add(case["expected_refusal"])

    assert detected == {
        "capabilities_digest_mismatch",
        "attestation_digest_mismatch",
        "spec_digest_mismatch",
    }

    suite = _load(CONFORMANCE_DIR / "suite.yaml")
    assert suite["integrity_gates"] == {
        "committed_fixture_digest_binding": "verified_by_offline_contract_tests",
        "ed25519_cryptographic_verification": "pending_executor_image_gate",
        "oci_image_provenance_verification": "pending_executor_image_gate",
    }
    integrity = next(fixture for fixture in suite["fixtures"] if fixture["id"] == "integrity-tamper-negative")
    assert integrity["cryptographic_verification"] == "pending_executor_image_gate"


def test_state_correlations_reject_inconsistent_schema_valid_shapes(schemas: dict[str, dict], registry: Registry):
    events = _load(FLOW_DIR / "fake.events.json")
    result = _load(FLOW_DIR / "fake.result.json")
    status = _load(FLOW_DIR / "fake.status.json")

    wrong_event_state = copy.deepcopy(events[-1])
    wrong_event_state["state"] = "running"
    with pytest.raises(ValidationError):
        _validate(wrong_event_state, schemas["job-event.schema.json"], registry)

    incomplete_artifact_event = copy.deepcopy(events[-2])
    incomplete_artifact_event["payload"].pop("provenance_id")
    with pytest.raises(ValidationError):
        _validate(incomplete_artifact_event, schemas["job-event.schema.json"], registry)

    queued_with_result = copy.deepcopy(status)
    queued_with_result["result"] = result
    with pytest.raises(ValidationError):
        _validate(queued_with_result, schemas["job-status.schema.json"], registry)

    terminal_without_result = copy.deepcopy(status)
    terminal_without_result["state"] = "succeeded"
    with pytest.raises(ValidationError):
        _validate(terminal_without_result, schemas["job-status.schema.json"], registry)

    succeeded_status = copy.deepcopy(status)
    succeeded_status["state"] = "succeeded"
    succeeded_status["result"] = result
    _validate(succeeded_status, schemas["job-status.schema.json"], registry)
    succeeded_status["state"] = "failed"
    with pytest.raises(ValidationError):
        _validate(succeeded_status, schemas["job-status.schema.json"], registry)

    mismatched_provenance = copy.deepcopy(result)
    mismatched_provenance["provenance"]["outcome"] = "failed"
    with pytest.raises(ValidationError):
        _validate(mismatched_provenance, schemas["job-result.schema.json"], registry)


def test_structured_failure_validates(schemas: dict[str, dict], registry: Registry):
    error = _load(CONFORMANCE_DIR / "fixtures" / "failure" / "error.json")
    _validate(error, schemas["error.schema.json"], registry)
    assert error["category"] == "integrity"
    assert error["retryable"] is False


def test_job_contract_rejects_embedded_credential_values(schemas: dict[str, dict], registry: Registry):
    job = _load(FLOW_DIR / "fake.job.json")
    job["workload_identity"]["token"] = "must-not-cross-the-contract"
    with pytest.raises(ValidationError):
        _validate(job, schemas["job-spec.schema.json"], registry)


def test_openapi_uses_canonical_executor_schemas():
    document = _load(CLOUD_CONTRACT)
    assert document["openapi"] == "3.1.0"
    assert document["info"]["version"] == "1.0.0-alpha.1"
    assert set(document["paths"]) == {
        "/v1/jobs",
        "/v1/jobs/{job_id}",
        "/v1/jobs/{job_id}/events",
        "/v1/jobs/{job_id}:cancel",
        "/v1/artifacts/{artifact_id}",
        "/v1/capabilities",
    }

    expected_refs = {
        "JobSpec": "job-spec.schema.json",
        "JobStatus": "job-status.schema.json",
        "JobEvent": "job-event.schema.json",
        "Artifact": "artifact.schema.json",
        "ExecutorError": "error.schema.json",
    }
    for name, filename in expected_refs.items():
        reference = document["components"]["schemas"][name]
        assert reference == {"$ref": f"../../executor/v1alpha1/{filename}"}
        assert (SCHEMA_DIR / filename).is_file()

    submit = document["paths"]["/v1/jobs"]["post"]
    assert submit["responses"]["202"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/JobStatus"}
    assert submit["parameters"] == [{"$ref": "#/components/parameters/IdempotencyKey"}]
    assert "Idempotency-Key header must equal the job specification idempotency_key" in submit["description"]

    cancellation = document["paths"]["/v1/jobs/{job_id}:cancel"]["post"]
    assert "stable actor type and id, tenant, and project" in cancellation["description"]
    assert "binds one job" in cancellation["description"]
    assert "first durably committed terminal transition wins" in cancellation["description"]


def test_black_box_walking_skeleton_cases_are_declared_and_pending():
    suite = _load(CONFORMANCE_DIR / "suite.yaml")
    declaration = suite["black_box_scenarios"]
    scenarios = _load(CONFORMANCE_DIR / declaration["path"])

    assert declaration["status"] == "pending_implementation"
    assert scenarios["evidence_status"] == "declaration_only"
    required_operations = set(scenarios["driver_interface"]["required_operations"])
    assert scenarios["driver_interface"]["invocation"] == [
        "{driver}",
        "--scenario",
        "{scenario_id}",
        "--request",
        "{request_json}",
        "--response",
        "{response_json}",
    ]

    walking_skeleton = [scenario for scenario in scenarios["scenarios"] if "assertion" in scenario]
    assert {scenario["id"] for scenario in walking_skeleton} == {
        "WS-01-idempotent-single-dispatch",
        "WS-02-disconnect-does-not-cancel",
        "WS-03-control-plane-restart-durability",
        "WS-04-exclusive-cursor-redelivery-dedup",
        "WS-05-artifact-round-trip",
        "WS-06-fake-local-parity",
        "WS-07-integrity-refusal",
        "WS-08-outcome-unknown-reconciliation",
    }
    assert sorted(scenario["assertion"] for scenario in walking_skeleton) == list(range(1, 9))
    assert all(scenario["status"] == "pending_implementation" for scenario in walking_skeleton)
    assert all(set(scenario["steps"]) <= required_operations for scenario in walking_skeleton)

    cancellation_races = [scenario for scenario in scenarios["scenarios"] if scenario["id"].startswith("CR-")]
    assert {scenario["id"] for scenario in cancellation_races} == {
        "CR-01-cancellation-wins",
        "CR-02-completion-wins",
    }
    assert all(scenario["status"] == "pending_implementation" for scenario in cancellation_races)
    assert not any(scenario["status"] == "passed" for scenario in scenarios["scenarios"])


def test_suite_paths_and_characterized_noether_fixture_are_explicit():
    suite = _load(CONFORMANCE_DIR / "suite.yaml")
    for fixture in suite["fixtures"]:
        assert (CONFORMANCE_DIR / fixture["path"]).is_dir()

    noether = next(fixture for fixture in suite["fixtures"] if fixture["id"] == "noether-v0.1.0-smoke")
    fixture_dir = CONFORMANCE_DIR / noether["path"]
    fixture = _load(fixture_dir / "fixture.json")

    assert noether["required"] is False
    assert noether["status"] == "characterized"
    assert noether["profile"] == "research-cpu"
    assert noether["execution_lane"] == "optional-pinned-image"
    assert fixture["status"] == "characterized"
    assert fixture["release"] == {
        "distribution": "reasoning-noether",
        "version": "0.1.0",
        "tag": "v0.1.0",
        "commit": "6d8d64309d3a01395d142249505beb001c56b97c",
    }
    assert fixture["health_smoke"]["expected"]["sha256"] == ("dbbdda3865b1b4dae69b029d69c993adb47b53b1195b0e2afdb7128065924c9c")
    assert fixture["parallel_case"]["algorithm_parallelism"] == "serial-per-shard"
    assert fixture["parallel_case"]["batch_parallelism"] == "four-independent-executor-jobs"
    expected_shards = {
        101: (20, 232, "2d0644dff58035b763b878c5cca0f80275457cf8f315cfe5484dc620bc6fac48"),
        103: (20, 232, "6a384a7ad8b3ab73490e6f40b4393fdd5e149a422a39f674c4417f1b5bcc9517"),
        107: (20, 232, "9ff7f9e580d3297a4f06784195643a275c28abd85ef01667a7b1ff18f20f068f"),
        109: (20, 231, "4a51b01deac2c66a9e8dcc934fedb600795501c3ecfb7bd796681f2bf6aaf2c9"),
    }
    assert {
        shard["prime"]: (shard["basis_count"], shard["term_count"], shard["sha256"])
        for shard in fixture["parallel_case"]["expected_shards"]
    } == expected_shards

    leading = fixture["parallel_case"]["shared_ordered_leading_exponents"]
    signature = hashlib.sha256(json.dumps(leading, separators=(",", ":")).encode()).hexdigest()
    assert signature == ACCEPTED_LEADING_SIGNATURE
    assert fixture["parallel_case"]["shared_ordered_leading_exponents_sha256"] == ACCEPTED_LEADING_SIGNATURE
    assert fixture["runtime_semantics"]["resource_limit_failure"] == {
        "python_type": "ValueError",
        "characterized_generator_count": 129,
        "message": "native Groebner computation exceeded its basis size limit of 128",
    }

    health_script = fixture_dir / "health_smoke.py"
    cyclic_script = fixture_dir / "cyclic5_shard.py"
    for script in (health_script, cyclic_script):
        compile(script.read_text(encoding="utf-8"), str(script), "exec")

    health_constants = _literal_assignments(health_script)
    cyclic_constants = _literal_assignments(cyclic_script)
    assert {"CONSTRUCTION", "EXPECTED"} <= _loaded_names(health_script)
    assert {"CONSTRUCTION", "EXPECTED", "EXPECTED_LEADING_EXPONENTS", "EXPECTED_LEADING_EXPONENTS_SHA256"} <= _loaded_names(cyclic_script)
    assert health_constants["CONSTRUCTION"] == fixture["health_smoke"]["construction"]
    assert health_constants["EXPECTED"] == fixture["health_smoke"]["expected"]
    assert cyclic_constants["NOETHER_VERSION"] == fixture["release"]["version"]
    assert list(cyclic_constants["PRIMES"]) == sorted(expected_shards)
    assert cyclic_constants["CONSTRUCTION"] == fixture["parallel_case"]["construction"]
    assert cyclic_constants["EXPECTED"] == {
        prime: {"basis_count": basis_count, "term_count": term_count, "sha256": digest}
        for prime, (basis_count, term_count, digest) in expected_shards.items()
    }
    assert cyclic_constants["EXPECTED_LEADING_EXPONENTS"] == leading
    assert cyclic_constants["EXPECTED_LEADING_EXPONENTS_SHA256"] == ACCEPTED_LEADING_SIGNATURE

    installation = fixture["installation"]
    assert installation["wheel_build"]["command"] == ["make", "wheel"]
    assert installation["wheel_build"]["post_install_probe"] == ["python3", "-c", "import noether; noether.capabilities()"]
    assert installation["rejected_builds"] == [
        {
            "command_prefix": ["maturin", "build"],
            "reason": "Bare maturin build omits the generated capability-discovery JSON, so noether.capabilities() fails.",
        }
    ]
    assert fixture["image_provenance_binding"] == {
        "status": "required_at_image_build",
        "attestation_component_name": "reasoning-noether",
        "source_tag": "v0.1.0",
        "source_commit": "6d8d64309d3a01395d142249505beb001c56b97c",
        "required_digests": ["wheel_sha256", "capability_manifest_sha256", "sbom_sha256", "oci_image_sha256"],
    }
    characterization = fixture["parallel_case"]["characterization"]
    assert characterization["platform"]["os"] == "macos"
    assert characterization["platform"]["architecture"] == "arm64"
    assert characterization["platform"]["build_profile"] == "release"
    assert characterization["normative_sla"] is False

    assert not list(fixture_dir.glob("*PLACEHOLDER*"))


def test_noether_v031_fixture_retains_v010_invariants_and_adds_verified_semiregular_bases():
    suite = _load(CONFORMANCE_DIR / "suite.yaml")
    entries = {fixture["id"]: fixture for fixture in suite["fixtures"]}
    previous = entries["noether-v0.1.0-smoke"]
    # The accepted v1alpha1 suite is digest-bound by downstream contracts.
    # Successor release evidence lives beside it without rewriting those bytes.
    assert "noether-v0.3.1-smoke" not in entries

    previous_fixture = _load(CONFORMANCE_DIR / previous["path"] / "fixture.json")
    fixture_dir = CONFORMANCE_DIR / "fixtures/noether-v0.3.1-smoke"
    fixture = _load(fixture_dir / "fixture.json")
    assert fixture["schema_id"] == previous_fixture["schema_id"]
    assert fixture["schema_version"] == previous_fixture["schema_version"]
    assert fixture["fixture_id"] == "noether-v0.3.1-smoke"
    assert fixture["status"] == "characterized"
    release = fixture["release"]
    assert release["distribution"] == "reasoning-noether"
    assert release["version"] == "0.3.1"
    assert release["tag"] == "v0.3.1"
    assert release["commit"] == "ca8fc50f3d9a902a827fbd558dba138ad7bf0f8f"
    assert release["tag_object"] == "d6b19346770112e2527ee730cf7cabd85c506815"
    assert release["source_archive_sha256"] == "84ca3d7ceb08d5132f0a45ac1841fb449fa65f5b97b13368f26205e8a73a4100"
    assert release["license"] == "MIT"
    assert release["predecessor"]["fixture_id"] == previous["id"]
    assert release["predecessor"]["commit"] == previous_fixture["release"]["commit"]
    assert release["source_archive_command"].startswith("git archive --format=tar v0.3.1")
    assert fixture["installation"]["wheel_build"]["command"] == ["make", "wheel"]
    assert fixture["installation"]["removed_since_predecessor"] == {
        "python_modules": ["noether.bridges"],
        "combin_exports": [
            "EnumerationCoverage",
            "PosetEnumeration",
            "PosetEnumerationCheckpoint",
            "enumerate_posets",
            "enumerate_simplicial_complexes",
        ],
    }
    assert fixture["installation"]["rejected_builds"] == previous_fixture["installation"]["rejected_builds"]
    assert fixture["image_provenance_binding"] == {
        "status": "required_at_image_build",
        "attestation_component_name": "reasoning-noether",
        "source_tag": "v0.3.1",
        "source_commit": "ca8fc50f3d9a902a827fbd558dba138ad7bf0f8f",
        "required_digests": ["wheel_sha256", "capability_manifest_sha256", "sbom_sha256", "oci_image_sha256"],
    }

    # The v0.1.0 mathematical invariants are retained verbatim; only the version literal moves.
    expected_health = dict(previous_fixture["health_smoke"]["expected"], noether_version="0.3.1")
    assert fixture["health_smoke"]["construction"] == previous_fixture["health_smoke"]["construction"]
    assert fixture["health_smoke"]["expected"] == expected_health
    assert fixture["parallel_case"]["construction"] == previous_fixture["parallel_case"]["construction"]
    assert fixture["parallel_case"]["shard_command"] == previous_fixture["parallel_case"]["shard_command"]
    assert fixture["parallel_case"]["expected_shards"] == previous_fixture["parallel_case"]["expected_shards"]
    assert (
        fixture["parallel_case"]["shared_ordered_leading_exponents"]
        == previous_fixture["parallel_case"]["shared_ordered_leading_exponents"]
    )
    assert fixture["parallel_case"]["shared_ordered_leading_exponents_sha256"] == ACCEPTED_LEADING_SIGNATURE
    assert fixture["parallel_case"]["characterization"]["platform"]["os"] == "linux"
    assert fixture["parallel_case"]["characterization"]["platform"]["architecture"] == "x86_64"
    assert fixture["parallel_case"]["characterization"]["normative_sla"] is False

    groebner = fixture["verified_groebner_case"]
    expected_sizes = {
        6: (39, 2028, 447289, "fd111468ad97a761ecbb57a5d2493bb63d87ca59c154f4be31f844db4977edfb"),
        7: (67, 6714, 1482973, "1247f57d6ef4d4d4c4de74ceb8966da70703a6ff15a29eb7c5c9edd662888081"),
    }
    assert {
        size["size"]: (size["basis_count"], size["term_count"], size["ideal_json_bytes"], size["sha256"])
        for size in groebner["expected_sizes"]
    } == expected_sizes
    assert groebner["construction"]["prime"] == 65521
    assert groebner["construction"]["term_order"] == "grevlex"
    assert groebner["algorithm_parallelism"] == "serial-per-size"
    assert groebner["size_command"] == ["python3", "semiregular_verify.py", "{size}", "/outputs/semiregular-{size}.json"]
    assert "1,000,000" in groebner["predecessor_limitation"]
    assert groebner["size_resources"]["wall_time_seconds"] >= 60
    assert groebner["characterization"]["normative_sla"] is False

    semantics = fixture["runtime_semantics"]["resource_limit_failure"]
    assert semantics["python_type"] == "ValueError"
    assert (
        semantics["predecessor_characterization"]["message"] == previous_fixture["runtime_semantics"]["resource_limit_failure"]["message"]
    )
    assert "MAX_REDUCTION_STEPS" in semantics["verification_budget_characterized_by"]
    assert "critical-pair verification steps limit of 1000000" in semantics["verification_budget_messages"][0]
    assert "critical-pair verification total steps limit of 64000000" in semantics["verification_budget_messages"][1]

    health_script = fixture_dir / "health_smoke.py"
    cyclic_script = fixture_dir / "cyclic5_shard.py"
    semiregular_script = fixture_dir / "semiregular_verify.py"
    for script in (health_script, cyclic_script, semiregular_script):
        compile(script.read_text(encoding="utf-8"), str(script), "exec")
    health_constants = _literal_assignments(health_script)
    cyclic_constants = _literal_assignments(cyclic_script)
    semiregular_constants = _literal_assignments(semiregular_script)
    assert health_constants["CONSTRUCTION"] == fixture["health_smoke"]["construction"]
    assert health_constants["EXPECTED"] == fixture["health_smoke"]["expected"]
    assert cyclic_constants["NOETHER_VERSION"] == "0.3.1"
    assert list(cyclic_constants["PRIMES"]) == [shard["prime"] for shard in fixture["parallel_case"]["expected_shards"]]
    assert cyclic_constants["CONSTRUCTION"] == fixture["parallel_case"]["construction"]
    assert cyclic_constants["EXPECTED"] == {
        shard["prime"]: {"basis_count": shard["basis_count"], "term_count": shard["term_count"], "sha256": shard["sha256"]}
        for shard in fixture["parallel_case"]["expected_shards"]
    }
    assert cyclic_constants["EXPECTED_LEADING_EXPONENTS"] == fixture["parallel_case"]["shared_ordered_leading_exponents"]
    assert cyclic_constants["EXPECTED_LEADING_EXPONENTS_SHA256"] == ACCEPTED_LEADING_SIGNATURE
    assert semiregular_constants["NOETHER_VERSION"] == "0.3.1"
    assert list(semiregular_constants["SIZES"]) == sorted(expected_sizes)
    assert semiregular_constants["CONSTRUCTION"] == groebner["construction"]
    assert semiregular_constants["EXPECTED"] == {
        size: {"basis_count": basis_count, "term_count": term_count, "ideal_json_bytes": json_bytes, "sha256": digest}
        for size, (basis_count, term_count, json_bytes, digest) in expected_sizes.items()
    }
    assert {"CONSTRUCTION", "EXPECTED"} <= _loaded_names(health_script)
    assert {"CONSTRUCTION", "EXPECTED", "EXPECTED_LEADING_EXPONENTS", "EXPECTED_LEADING_EXPONENTS_SHA256", "PRIMES"} <= _loaded_names(
        cyclic_script
    )
    assert {"CONSTRUCTION", "EXPECTED", "SIZES", "NOETHER_VERSION"} <= _loaded_names(semiregular_script)

    # The previous fixture and its scripts are untouched by the successor.
    assert previous_fixture["release"]["version"] == "0.1.0"
    assert not list(fixture_dir.glob("*PLACEHOLDER*"))
