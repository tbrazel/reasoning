"""Reviewed AD5 diagnostics; saved native fixtures are NOT sandbox acceptance."""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from test_science_evidence import FACTORIES as BASE_FACTORIES
from test_science_evidence import FIXTURES, install_saved_runner, sha

from reasoning_ledger.agents import heisenberg_level25_four_block_ad5_central_key as central
from reasoning_ledger.agents import heisenberg_level25_four_block_ad5_join as join
from reasoning_ledger.agents import heisenberg_level25_four_block_ad5_orbit as orbit
from reasoning_ledger.agents import heisenberg_level25_four_block_ad5_prefix as prefix
from reasoning_ledger.agents import heisenberg_level25_four_block_ad5_signature as signature
from reasoning_ledger.agents import heisenberg_level25_four_block_ad5_slice as slice_profile
from reasoning_ledger.agents import project_tools
from reasoning_ledger.agents.science_evidence import (
    bounded_transcript_sha256,
    profile_evidence_sha256,
    reviewed_profile_matches,
)
from reasoning_ledger.agents.scientific import ScientificToolError
from reasoning_ledger.canonical import canonical_json

MODULES = {"prefix": prefix, "signature": signature, "central_key": central, "orbit": orbit, "slice": slice_profile, "join": join}
SUFFIXES = {
    "prefix": "ad5_prefix_shift_census",
    "signature": "ad5_coordinate3_functional_signature",
    "central_key": "ad5_coordinate3_central_key",
    "orbit": "ad5_coordinate3_tensor_orbit",
    "slice": "ad5_coordinate3_central_orbit_representative_preflight",
    "join": "ad5_coordinate3_join_partition_covariance",
}
PARENTS = {
    "prefix": ("fourier", "phase"),
    "signature": ("fourier", "phase"),
    "central_key": ("fourier", "phase"),
    "orbit": ("central_key",),
    "slice": ("orbit",),
    "join": ("orbit", "slice"),
}
PARENT_SUFFIXES = {"fourier": "fourier", "phase": "phase_quotient", **SUFFIXES}
PARENT_CHECKS = {
    "fourier": "corrected_fourier_domain_is_the_certified_prerequisite",
    "phase": "phase_quotient_is_the_certified_prerequisite",
    "central_key": "central_key_is_the_fresh_reviewed_prerequisite",
    "orbit": "orbit_is_the_fresh_reviewed_prerequisite",
    "slice": "slice_is_the_fresh_reviewed_prerequisite",
}
FACTORIES = {
    name: getattr(module, "level25_heisenberg_augmented_four_block_" + SUFFIXES[name] + "_profile")
    for name, module in MODULES.items()
}
EXPECTED = {
    "prefix": "619ea2102daed880ac662d6b119e8b324e5cafffb06ec9793213230696e48b24",
    "signature": "4b4eb7eac2fd0f7d4b19e91e424492e9f4e405b0c5872df1217f1c9574f871d8",
    "central_key": "7a3896a48b8e0c1de959856c7f07740df956d63cc568321f4a2052e28c27f403",
    "orbit": "8c526e2208aaa8007b63741cbd9f5c4d9f05c3d412033050bfd921df2f84f32e",
    "slice": "32b669dd063b82e62fd3d11a62ea3543d2b7d0683666f0c2601f688576912318",
    "join": "c9b5beeb78f48c3f05ef7bab2876c1c7ed5d88555c0084110d0299457cf58fb8",
}


def native_output(name):
    return (FIXTURES / ("ad5_" + name + "_v1.txt")).read_text(encoding="utf-8")


def replay_fixture(name):
    transcript = (FIXTURES / ("ad5_" + name + "_replay_v1.json")).read_text(encoding="utf-8")
    program = Path(MODULES[name].__file__).with_name("heisenberg_level25_four_block_ad5_" + name + "_replay.py")
    return {
        **json.loads(transcript),
        "transcript": transcript,
        "transcript_sha256": sha(transcript),
        "program_sha256": hashlib.sha256(program.read_bytes()).hexdigest(),
        "runtime_address_space_limit_bytes": 67108864,
        "interpreter": "TEST-ONLY-RUNTIME-FIXTURE",
    }


def install_saved_diagnostics(patch):
    install_saved_runner(patch)
    programs = {module._PROGRAM: name for name, module in MODULES.items()}

    def saved_run(program, **kwargs):
        assert kwargs["preserve_exact_transcript"] is True
        assert kwargs["runtime_address_space_bytes"] == 67108864
        stdout = native_output(programs[program])
        return {
            "program_sha256": sha(program),
            "transcript_sha256": sha(stdout),
            "transcript": stdout,
            "protocol_version": 1,
            "compiler": "TEST-ONLY-RUNTIME-FIXTURE",
            "compiler_sha256": "0" * 64,
            "compiler_version": "TEST-ONLY-RUNTIME-FIXTURE",
            "binary_sha256": "0" * 64,
            "runtime_address_space_limit_bytes": 67108864,
        }

    for name, module in MODULES.items():
        patch.setattr(module, "_run_source_hashed_program", saved_run)
        patch.setattr(module, "_run_replay", lambda name=name: replay_fixture(name))


@pytest.fixture(scope="module")
def profiles():
    with pytest.MonkeyPatch.context() as patch:
        install_saved_diagnostics(patch)
        return {
            **{name: factory() for name, factory in FACTORIES.items()},
            **{name: BASE_FACTORIES[name]() for name in ("fourier", "phase")},
        }


@pytest.fixture
def make_profile(profiles, monkeypatch):
    def make(name, computation=None, replay=None):
        module = MODULES[name]
        monkeypatch.setattr(
            module, "_run_program", lambda: copy.deepcopy(profiles[name]["computation"] if computation is None else computation)
        )
        monkeypatch.setattr(
            module, "_run_replay", lambda: copy.deepcopy(profiles[name]["independent_replay"] if replay is None else replay)
        )
        for parent in PARENTS[name]:
            suffix = PARENT_SUFFIXES[parent]
            monkeypatch.setattr(
                module, "level25_heisenberg_augmented_four_block_" + suffix + "_profile",
                lambda parent=parent: copy.deepcopy(profiles[parent]),
            )
        return FACTORIES[name]()

    return make


@pytest.mark.parametrize("name", MODULES)
def test_saved_exact_native_math_and_independent_replay_match_reviewed_scope(profiles, name):
    result = profiles[name]
    assert result["certificate_verified"] is True
    assert result["evidence_sha256"] == EXPECTED[name] == profile_evidence_sha256(result)
    assert reviewed_profile_matches(result, EXPECTED[name])
    assert result["computation"]["transcript"] == native_output(name)
    assert result["computation"]["transcript_sha256"] == sha(native_output(name))
    assert result["independent_replay"] == replay_fixture(name)


_FIELDS = [(name, field) for name, module in MODULES.items() for field in sorted(module._parse_transcript(native_output(name)))]


@pytest.mark.parametrize("name,field", _FIELDS)
def test_every_cached_primary_field_must_agree_with_exact_raw_transcript(profiles, make_profile, name, field):
    computation = copy.deepcopy(profiles[name]["computation"])
    value = computation[field]
    computation[field] = value + 1 if isinstance(value, int) else "0" * 64 if field.endswith("sha256") else "UNREVIEWED"
    result = make_profile(name, computation)
    assert result["certificate"]["programs_and_transcripts_match_reviewed_executions"] is False
    assert result["certificate_verified"] is False


@pytest.mark.parametrize("name,field", [(name, field) for name, field in _FIELDS if field != "vm_peak_kib"])
def test_rehashing_any_nontelemetry_field_cannot_approve_changed_mathematics(profiles, make_profile, name, field):
    computation = copy.deepcopy(profiles[name]["computation"])
    line = next(line for line in computation["transcript"].splitlines() if line.startswith(field + "="))
    value = computation[field]
    changed = str(value + 1) if isinstance(value, int) else "0" * 64 if field.endswith("sha256") else "UNREVIEWED"
    stdout = computation["transcript"].replace(line + "\n", field + "=" + changed + "\n")
    computation.update(MODULES[name]._parse_transcript(stdout), transcript=stdout, transcript_sha256=sha(stdout))
    if "vm_peak_kib" in computation:
        computation["mathematical_transcript_sha256"] = bounded_transcript_sha256(stdout, parser=MODULES[name]._parse_transcript)
    result = make_profile(name, computation)
    assert result["certificate"]["programs_and_transcripts_match_reviewed_executions"] is False
    assert result["certificate_verified"] is False


@pytest.mark.parametrize("name", ["central_key", "orbit", "slice", "join"])
@pytest.mark.parametrize("memory", [1, 8012, 8016, 65536, -1, 0, 65537])
def test_memory_variation_keeps_math_but_never_waives_the_cap(profiles, make_profile, name, memory):
    computation = copy.deepcopy(profiles[name]["computation"])
    measured = computation["vm_peak_kib"]
    stdout = computation["transcript"].replace(f"vm_peak_kib={measured}\n", f"vm_peak_kib={memory}\n")
    computation.update(transcript=stdout, transcript_sha256=sha(stdout), vm_peak_kib=memory)
    computation["mathematical_transcript_sha256"] = bounded_transcript_sha256(stdout, parser=MODULES[name]._parse_transcript)
    assert computation["mathematical_transcript_sha256"] == MODULES[name]._REVIEWED_MATHEMATICAL_TRANSCRIPT_SHA256
    result = make_profile(name, computation)
    valid = 0 < memory <= 65536
    assert result["certificate"]["programs_and_transcripts_match_reviewed_executions"] is True
    assert result["certificate"]["resident_telemetry_respects_the_address_space_cap"] is valid
    assert result["certificate_verified"] is valid
    assert (result["evidence_sha256"] == EXPECTED[name]) is valid
    if memory != measured:
        assert sha(canonical_json(result)) != sha(canonical_json(profiles[name]))


@pytest.mark.parametrize("name", MODULES)
@pytest.mark.parametrize("field", ["program_sha256", "transcript_sha256", "engine_preflight_charge", "runtime_address_space_limit_bytes"])
def test_independent_source_raw_math_and_limits_cannot_be_copied(profiles, make_profile, name, field):
    replay = copy.deepcopy(profiles[name]["independent_replay"])
    replay[field] = "0" * 64 if field.endswith("sha256") else 1
    assert make_profile(name, replay=replay)["certificate_verified"] is False


@pytest.mark.parametrize("name", MODULES)
def test_changed_rehashed_independent_replay_is_not_reviewed(profiles, make_profile, name):
    replay = copy.deepcopy(profiles[name]["independent_replay"])
    values = json.loads(replay["transcript"])
    values["engine_preflight_charge"] += 1
    replay.update(values)
    replay["transcript"] = canonical_json(values) + "\n"
    replay["transcript_sha256"] = sha(replay["transcript"])
    assert make_profile(name, replay=replay)["certificate_verified"] is False


@pytest.mark.parametrize("name,parent", [(name, parent) for name, parents in PARENTS.items() for parent in parents])
def test_copied_prerequisite_seal_does_not_approve_changed_scope(profiles, make_profile, monkeypatch, name, parent):
    make_profile(name)
    changed = copy.deepcopy(profiles[parent])
    changed["request"]["unreviewed"] = True
    suffix = PARENT_SUFFIXES[parent]
    monkeypatch.setattr(MODULES[name], "level25_heisenberg_augmented_four_block_" + suffix + "_profile", lambda: changed)
    result = FACTORIES[name]()
    check = PARENT_CHECKS[parent]
    assert result["certificate"][check] is False
    assert result["certificate_verified"] is False


@pytest.mark.parametrize("name", MODULES)
@pytest.mark.parametrize("field", ["compiler", "compiler_sha256", "compiler_version", "binary_sha256"])
def test_runtime_provenance_remains_in_raw_receipt_not_math_identity(profiles, name, field):
    profile = copy.deepcopy(profiles[name])
    profile["computation"][field] = "OTHER-EXPLICIT-RUNTIME-PROVENANCE"
    assert profile_evidence_sha256(profile) == EXPECTED[name]
    assert sha(canonical_json(profile)) != sha(canonical_json(profiles[name]))


@pytest.mark.parametrize("name", MODULES)
def test_independent_interpreter_and_duplicate_body_are_retained_provenance(profiles, name):
    profile = copy.deepcopy(profiles[name])
    profile["independent_replay"].update(interpreter="OTHER-EXPLICIT-RUNTIME-PROVENANCE", transcript="OTHER-EXPLICIT-RAW-BODY")
    assert profile_evidence_sha256(profile) == EXPECTED[name]
    assert sha(canonical_json(profile)) != sha(canonical_json(profiles[name]))


@pytest.mark.parametrize("name", MODULES)
@pytest.mark.parametrize("field", ["request", "not_certified", "certificate", "computation", "unexpected_claim"])
def test_all_other_claims_and_unknown_fields_remain_bound(profiles, name, field):
    profile = copy.deepcopy(profiles[name])
    if field == "computation":
        profile[field]["program_sha256"] = "0" * 64
    elif field == "certificate":
        profile[field]["unreviewed_check"] = True
    else:
        profile[field] = {"unreviewed": True}
    assert profile_evidence_sha256(profile) != EXPECTED[name]
    assert reviewed_profile_matches(profile, EXPECTED[name]) is False


@pytest.mark.parametrize(
    "name,missing",
    [(name, "independent_replay") for name in MODULES]
    + [
        (name, parent + suffix)
        for name, parents in PARENTS.items()
        for parent in parents
        for suffix in ("_evidence_sha256", "_receipt_sha256")
    ],
)
def test_missing_required_evidence_has_no_normalization_fallback(profiles, name, missing):
    profile = copy.deepcopy(profiles[name])
    del profile[missing]
    with pytest.raises(ScientificToolError):
        profile_evidence_sha256(profile)


@pytest.mark.parametrize("name", MODULES)
def test_reports_bind_exact_rendered_receipt_and_reviewed_math(tmp_path, monkeypatch, profiles, name):
    from test_local_science_tools import _registry

    tool = "level25_heisenberg_augmented_four_block_" + SUFFIXES[name]
    monkeypatch.setattr(project_tools, tool + "_profile", lambda: copy.deepcopy(profiles[name]))
    result = _registry(tmp_path).invoke(tool + "_report", {"path": "research/evidence.md", "terminal_marker": "EVIDENCE_COMPLETE"})
    text = (tmp_path / "artifacts/research/evidence.md").read_text(encoding="utf-8")
    receipt = json.loads(text.split("```json\n", 1)[1].split("\n```", 1)[0])
    assert result["receipt_sha256"] == sha(canonical_json(receipt))
    assert result["evidence_sha256"] == EXPECTED[name] == profile_evidence_sha256(receipt)
    assert result["certificate_verified"] is True
    assert "Mathematical evidence SHA-256: `" + EXPECTED[name] + "`" in text
    assert text.endswith("EVIDENCE_COMPLETE\n")


@pytest.mark.parametrize("name", MODULES)
@pytest.mark.parametrize("failure", ["failed_certificate", "copied_seal", "rehashed_unreviewed_scope"])
def test_reports_never_label_failed_or_unreviewed_math_certified(tmp_path, monkeypatch, profiles, name, failure):
    from test_local_science_tools import _registry

    receipt = copy.deepcopy(profiles[name])
    if failure == "failed_certificate":
        receipt["certificate_verified"] = False
    else:
        receipt["request"]["unreviewed"] = True
        if failure == "rehashed_unreviewed_scope":
            receipt["evidence_sha256"] = profile_evidence_sha256(receipt)
    tool = "level25_heisenberg_augmented_four_block_" + SUFFIXES[name]
    monkeypatch.setattr(project_tools, tool + "_profile", lambda: receipt)
    with pytest.raises(ScientificToolError, match="report not written"):
        _registry(tmp_path).invoke(tool + "_report", {"path": "research/evidence.md", "terminal_marker": "EVIDENCE_COMPLETE"})
    assert not (tmp_path / "artifacts/research/evidence.md").exists()


@pytest.mark.parametrize("name", MODULES)
@pytest.mark.parametrize("kind", ["profile", "report"])
def test_existing_science_assertions_with_explicit_saved_runtime_fixtures(tmp_path, monkeypatch, name, kind):
    import test_local_science_tools as tests

    install_saved_diagnostics(monkeypatch)
    test = getattr(tests, "test_level25_heisenberg_augmented_four_block_" + SUFFIXES[name] + "_" + kind)
    if kind == "profile":
        test()
    else:
        test(tmp_path)


@pytest.mark.parametrize("name", MODULES)
def test_genuine_scalar_python_replay_reproduces_exact_reviewed_math_not_runtime_isolation(name):
    limiter = shutil.which("prlimit")
    interpreter = shutil.which("python3", path="/usr/local/bin:/usr/bin:/bin")
    if limiter is None or interpreter is None:
        pytest.skip("Linux trusted-source diagnostic requires prlimit and Python")
    program = Path(MODULES[name].__file__).with_name("heisenberg_level25_four_block_ad5_" + name + "_replay.py")
    result = subprocess.run(
        [limiter, "--as=67108864:67108864", "--", interpreter, "-I", "-S", str(program)],
        text=True, capture_output=True, timeout=900, check=True,
        env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
    )
    assert result.stderr == ""
    assert result.stdout == replay_fixture(name)["transcript"]
    assert sha(result.stdout) == MODULES[name]._REVIEWED_REPLAY_TRANSCRIPT_SHA256


@pytest.mark.parametrize("name", MODULES)
def test_production_replay_construction_preserves_raw_bytes_and_isolation(monkeypatch, name):
    wraps = []
    fixture = replay_fixture(name)

    def wrap(command, **kwargs):
        wraps.append((command, kwargs))
        return ["TEST-ONLY-SANDBOX-BOUNDARY", *command]

    def run(command, **kwargs):
        assert command[0] == "TEST-ONLY-SANDBOX-BOUNDARY"
        assert kwargs["timeout"] == (180 if name == "join" else 900)
        return subprocess.CompletedProcess(command, 0, fixture["transcript"], "")

    module = MODULES[name]
    monkeypatch.setattr(module.sandbox, "wrap", wrap)
    monkeypatch.setattr(module.subprocess, "run", run)
    result = module._run_replay()
    assert len(wraps) == 1
    assert wraps[0][1]["network"] is False
    assert wraps[0][1]["ro"][0].name == "heisenberg_level25_four_block_ad5_" + name + "_replay.py"
    assert any("setrlimit" in argument for argument in wraps[0][0])
    assert "67108864" in wraps[0][0]
    assert "-I" in wraps[0][0] and "-S" in wraps[0][0]
    assert result["transcript"] == fixture["transcript"]
    assert result["transcript_sha256"] == sha(fixture["transcript"])
    assert result["program_sha256"] == module._REVIEWED_REPLAY_PROGRAM_SHA256


def assert_no_chain_authorization(profile, name):
    assert profile["certificate_verified"] is False
    if name == "orbit":
        assert profile["decision"]["simultaneous_c5_squared_action_certified"] is False
        assert profile["decision"]["authorized_central_reduction_factor"] == 1
    elif name == "slice":
        assert profile["decision"]["central_slice_enumerator_certified"] is False
        assert profile["decision"]["authorized_outer_loop_reduction_factor"] == 1
    else:
        assert profile["decision"]["five_key_join_predicate_equivariant"] is False
        assert profile["decision"]["direct_residual_predicate_equivariant"] is False
        assert profile["decision"]["representative_outer_domain_authorized"] is False


@pytest.mark.parametrize("name", ["orbit", "slice", "join"])
def test_declared_review_hashes_never_claim_external_contents_verified(profiles, name):
    profile = profiles[name]
    policy = profile["review_references" if name == "join" else "review_reference_policy"]
    assert policy["kind"] == "declared_source_literals"
    assert policy["external_contents_verified"] is False
    assert profile["charge_scope"] == "named primary and independent replay only; prerequisite executions are separate"
    for parent in PARENTS[name]:
        assert profile[parent + "_receipt_sha256"] == sha(canonical_json(profiles[parent]))
        assert profile[parent + "_evidence_sha256"] == EXPECTED[parent]
        assert profile["computation"][parent + "_receipt_sha256"] != profile[parent + "_receipt_sha256"]


@pytest.mark.parametrize("name", ["orbit", "slice", "join"])
def test_each_operation_invokes_fresh_prerequisite_profiles(profiles, make_profile, monkeypatch, name):
    make_profile(name)
    calls = []

    def fresh(parent):
        calls.append(parent)
        return copy.deepcopy(profiles[parent])

    for parent in PARENTS[name]:
        monkeypatch.setattr(
            MODULES[name], "level25_heisenberg_augmented_four_block_" + PARENT_SUFFIXES[parent] + "_profile",
            lambda parent=parent: fresh(parent),
        )
    assert FACTORIES[name]()["certificate_verified"] is True
    assert FACTORIES[name]()["certificate_verified"] is True
    assert calls == list(PARENTS[name]) * 2


@pytest.mark.parametrize("name,parent", [(name, parent) for name in ("orbit", "slice", "join") for parent in PARENTS[name]])
@pytest.mark.parametrize("failure", ["copied_seal", "rehashed_scope", "failed_certificate"])
def test_unapproved_prerequisite_cannot_grant_reduction_or_join_authority(profiles, make_profile, monkeypatch, name, parent, failure):
    make_profile(name)
    changed = copy.deepcopy(profiles[parent])
    if failure == "failed_certificate":
        changed["certificate_verified"] = False
    else:
        changed["request"]["unreviewed"] = True
        if failure == "rehashed_scope":
            changed["evidence_sha256"] = profile_evidence_sha256(changed)
    monkeypatch.setattr(MODULES[name], "level25_heisenberg_augmented_four_block_" + PARENT_SUFFIXES[parent] + "_profile", lambda: changed)
    result = FACTORIES[name]()
    assert result["certificate"][PARENT_CHECKS[parent]] is False
    assert_no_chain_authorization(result, name)


@pytest.mark.parametrize("name", ["orbit", "slice", "join"])
@pytest.mark.parametrize("field", ["program_sha256", "transcript_sha256", "mathematical_transcript_sha256"])
def test_copied_primary_identities_cannot_grant_chain_authority(profiles, make_profile, name, field):
    computation = copy.deepcopy(profiles[name]["computation"])
    computation[field] = "0" * 64
    result = make_profile(name, computation)
    assert result["certificate"]["programs_and_transcripts_match_reviewed_executions"] is False
    assert_no_chain_authorization(result, name)


@pytest.mark.parametrize(
    "parent,field",
    [
        ("orbit", "source_record_stream_sha256"),
        ("orbit", "zero_shape_stream_sha256"),
        ("orbit", "shape_action_table_sha256"),
        ("slice", "shape_stream_sha256"),
        ("slice", "action_table_sha256"),
        ("slice", "representative_stream_sha256"),
        ("slice", "representative_count"),
    ],
)
def test_join_declared_streams_must_match_fresh_parent_evidence(profiles, make_profile, monkeypatch, parent, field):
    make_profile("join")
    changed = copy.deepcopy(profiles[parent])
    changed["computation"][field] = "0" * 64 if field.endswith("sha256") else 1
    changed["evidence_sha256"] = profile_evidence_sha256(changed)
    monkeypatch.setattr(join, "level25_heisenberg_augmented_four_block_" + PARENT_SUFFIXES[parent] + "_profile", lambda: changed)
    result = FACTORIES["join"]()
    assert result["certificate"]["prior_mathematical_streams_match_fresh_certified_evidence"] is False
    assert_no_chain_authorization(result, "join")


@pytest.mark.parametrize("field", ["zero_shape_count", "central_state_count", "central_orbit_count"])
def test_slice_domain_must_match_the_fresh_certified_orbit_domain(profiles, make_profile, monkeypatch, field):
    make_profile("slice")
    changed = copy.deepcopy(profiles["orbit"])
    changed["computation"][field] += 1
    changed["evidence_sha256"] = profile_evidence_sha256(changed)
    monkeypatch.setattr(slice_profile, "level25_heisenberg_augmented_four_block_ad5_coordinate3_tensor_orbit_profile", lambda: changed)
    result = FACTORIES["slice"]()
    assert result["certificate"]["slice_cardinality_matches_the_certified_orbit_domain"] is False
    assert_no_chain_authorization(result, "slice")
