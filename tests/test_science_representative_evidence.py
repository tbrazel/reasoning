"""Saved compiled transcripts are mathematical fixtures, not sandbox acceptance."""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
from functools import partial

import pytest
from test_science_diagnostic_evidence import FACTORIES as DIAGNOSTICS
from test_science_diagnostic_evidence import install_saved_diagnostics
from test_science_evidence import FACTORIES as BASE_FACTORIES
from test_science_evidence import FIXTURES, sha

from reasoning_ledger.agents import heisenberg_level25_four_block_ad5_representative_batch as first
from reasoning_ledger.agents import heisenberg_level25_four_block_ad5_representative_batch_9_12 as second
from reasoning_ledger.agents import project_tools
from reasoning_ledger.agents.science_evidence import bounded_transcript_sha256, profile_evidence_sha256, reviewed_profile_matches
from reasoning_ledger.agents.scientific import ScientificToolError
from reasoning_ledger.canonical import canonical_json

MODULES = {"5_8": first, "9_12": second}
SUFFIXES = {"5_8": "ad5_coordinate3_representative_join_batch", "9_12": "ad5_coordinate3_representative_join_batch_9_12"}
FACTORIES = {
    name: getattr(module, "level25_heisenberg_augmented_four_block_" + SUFFIXES[name] + "_profile") for name, module in MODULES.items()
}
COUNTS = {"5_8": 9, "9_12": 13}
EXPECTED = {
    "5_8": "2714b93ca2b26587ff4dc1b08d38728892166bbc8f5c2864f75194eb6649c9f9",
    "9_12": "bd0bfbe69db2eeb922529526e3c4c64742e7e62b272cdf3e910123e58864f944",
}
PARENTS = {"5_8": ("covariance", "slice", "first_context", "next_four"), "9_12": ("covariance", "slice", "prior_batch")}
PARENT_SUFFIXES = {
    "covariance": "ad5_coordinate3_join_partition_covariance",
    "slice": "ad5_coordinate3_central_orbit_representative_preflight",
    "first_context": "bounded_ad5",
    "next_four": "bounded_ad5_context_batch",
    "prior_batch": SUFFIXES["5_8"],
}
PARENT_CHECKS = {
    "covariance": "covariance_certificate_is_bound_and_verified",
    "slice": "slice_is_the_fresh_reviewed_prerequisite",
    "first_context": "first_context_is_the_fresh_reviewed_prerequisite",
    "next_four": "next_four_are_the_fresh_reviewed_prerequisite",
    "prior_batch": "prior_batch_is_the_fresh_reviewed_prerequisite",
}


def parent_profile(profiles, parent):
    return profiles["5_8" if parent == "prior_batch" else parent]


def engine_key(replay):
    return "independent_sorted_join_replay" if replay else "computation"


def parser(name, replay=False):
    return partial(
        first._parse_replay_transcript if replay else first._parse_program_transcript, representative_prefix_check_count=COUNTS[name]
    )


def native_output(name, replay=False):
    return (FIXTURES / ("ad5_representative_" + name + ("_replay" if replay else "") + "_v1.txt")).read_text(encoding="utf-8")


def install_saved_representatives(patch):
    install_saved_diagnostics(patch)
    programs = {
        getattr(module, "_REPLAY_PROGRAM" if replay else "_PROGRAM"): (name, replay)
        for name, module in MODULES.items()
        for replay in (False, True)
    }

    def saved_run(program, **kwargs):
        assert kwargs["preserve_exact_transcript"] is True
        assert kwargs["runtime_address_space_bytes"] == 67108864
        assert kwargs["timeout_seconds"] == 900
        stdout = native_output(*programs[program])
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

    for module in MODULES.values():
        patch.setattr(module, "_run_source_hashed_program", saved_run)


@pytest.fixture(scope="module")
def profiles():
    with pytest.MonkeyPatch.context() as patch:
        install_saved_representatives(patch)
        return {
            **{name: factory() for name, factory in FACTORIES.items()},
            "covariance": DIAGNOSTICS["join"](),
            "slice": DIAGNOSTICS["slice"](),
            "first_context": BASE_FACTORIES["ad5"](),
            "next_four": BASE_FACTORIES["ad5_batch"](),
        }


@pytest.fixture
def make_profile(profiles, monkeypatch):
    def make(name, computation=None, replay=None):
        module = MODULES[name]
        monkeypatch.setattr(
            module, "_run_program", lambda: copy.deepcopy(profiles[name]["computation"] if computation is None else computation)
        )
        monkeypatch.setattr(
            module, "_run_replay", lambda: copy.deepcopy(profiles[name]["independent_sorted_join_replay"] if replay is None else replay)
        )
        for parent in PARENTS[name]:
            monkeypatch.setattr(
                module,
                "level25_heisenberg_augmented_four_block_" + PARENT_SUFFIXES[parent] + "_profile",
                lambda parent=parent: copy.deepcopy(parent_profile(profiles, parent)),
            )
        return FACTORIES[name]()

    return make


def assert_no_advance(result, name):
    assert result["certificate_verified"] is False
    assert result["decision"]["accepted_exact"] is False
    assert result["decision"]["public_next_unaccepted_packed"] == (5 if name == "5_8" else 9)
    assert result["decision"]["packed_representatives_exhausted"] == []
    assert result["decision"]["full_contraction_authorized"] is False
    assert result["decision"]["common_zero_found"] is False


@pytest.mark.parametrize("name", MODULES)
def test_saved_exact_native_math_and_sorted_replay_match_reviewed_scope(profiles, name):
    result = profiles[name]
    assert result["certificate_verified"] is True
    assert result["evidence_sha256"] == EXPECTED[name] == profile_evidence_sha256(result)
    assert reviewed_profile_matches(result, EXPECTED[name])
    for replay in (False, True):
        engine = result[engine_key(replay)]
        assert engine["transcript"] == native_output(name, replay)
        assert engine["transcript_sha256"] == sha(engine["transcript"])
        assert engine["mathematical_transcript_sha256"] == bounded_transcript_sha256(engine["transcript"], parser=parser(name, replay))
    assert result["actual_combined_charge"] == (409501621 if name == "5_8" else 409483494)
    assert result["decision"]["public_next_unaccepted_packed"] == (9 if name == "5_8" else 13)


def leaf_paths(value, prefix=()):
    if isinstance(value, dict):
        return [path for key, item in value.items() for path in leaf_paths(item, (*prefix, key))]
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return [path for index, item in enumerate(value) for path in leaf_paths(item, (*prefix, index))]
    return [prefix]


def changed_value(value):
    if isinstance(value, int):
        return value + 1
    if isinstance(value, list):
        return [value[0] + 1, *value[1:]] if value else [1]
    return value + "_UNREVIEWED"


@pytest.mark.parametrize(
    "name,replay,path",
    [
        (name, replay, path)
        for name in MODULES
        for replay in (False, True)
        for path in leaf_paths(parser(name, replay)(native_output(name, replay)))
    ],
)
def test_every_cached_parsed_field_is_bound_to_exact_output(profiles, make_profile, name, replay, path):
    engine = copy.deepcopy(profiles[name][engine_key(replay)])
    value = engine
    for part in path[:-1]:
        value = value[part]
    value[path[-1]] = changed_value(value[path[-1]])
    result = make_profile(name, **{"replay" if replay else "computation": engine})
    assert_no_advance(result, name)


@pytest.mark.parametrize("name", MODULES)
@pytest.mark.parametrize("replay", [False, True])
@pytest.mark.parametrize(
    "field", ["program_sha256", "transcript_sha256", "mathematical_transcript_sha256", "runtime_address_space_limit_bytes"]
)
def test_source_raw_math_and_declared_runtime_limits_cannot_be_copied(profiles, make_profile, name, replay, field):
    engine = copy.deepcopy(profiles[name][engine_key(replay)])
    engine[field] = "0" * 64 if field.endswith("sha256") else 1
    assert_no_advance(make_profile(name, **{"replay" if replay else "computation": engine}), name)


@pytest.mark.parametrize(
    "name,replay,field",
    [
        (name, replay, line.split("=", 1)[0])
        for name in MODULES
        for replay in (False, True)
        for line in native_output(name, replay).splitlines()[1:-1]
        if not line.startswith("vm_peak_kib=")
    ],
)
def test_rehashed_unreviewed_raw_fields_never_advance_the_cursor(profiles, make_profile, name, replay, field):
    engine = copy.deepcopy(profiles[name][engine_key(replay)])
    lines = engine["transcript"].splitlines()
    for index, line in enumerate(lines):
        if line.startswith(field + "="):
            original = line.split("=", 1)[1]
            if original and all(part.lstrip("-").isdigit() for part in original.split(",")):
                values = original.split(",")
                values[0] = str(int(values[0]) + 1)
                changed = ",".join(values)
            else:
                changed = original + "_UNREVIEWED"
            lines[index] = field + "=" + changed
    stdout = "\n".join(lines) + "\n"
    try:
        engine.update(
            parser(name, replay)(stdout),
            transcript=stdout,
            transcript_sha256=sha(stdout),
            mathematical_transcript_sha256=bounded_transcript_sha256(stdout, parser=parser(name, replay)),
        )
        result = make_profile(name, **{"replay" if replay else "computation": engine})
    except ScientificToolError:
        return
    assert_no_advance(result, name)


@pytest.mark.parametrize(
    "name,replay,line",
    [(name, replay, line) for name in MODULES for replay in (False, True) for line in native_output(name, replay).splitlines()[1:-1]],
)
def test_duplicate_fields_are_rejected_before_any_approval(name, replay, line):
    stdout = native_output(name, replay).replace("REASONING_END\n", line + "\nREASONING_END\n")
    with pytest.raises(ScientificToolError):
        parser(name, replay)(stdout)


@pytest.mark.parametrize("name", MODULES)
@pytest.mark.parametrize("replay", [False, True])
@pytest.mark.parametrize(
    "field", ["representative_prefix_check_count", "representative_prefix_failure_count", "context_0_c_key_match_checksum"]
)
@pytest.mark.parametrize("failure", ["missing_equals", "nonintegral"])
def test_malformed_extensions_and_replay_fields_are_tool_errors(name, replay, field, failure):
    stdout = native_output(name, replay)
    line = next(line for line in stdout.splitlines() if line.startswith(field + "="))
    replacement = field if failure == "missing_equals" else field + "=not-an-integer"
    with pytest.raises(ScientificToolError):
        parser(name, replay)(stdout.replace(line + "\n", replacement + "\n"))


@pytest.mark.parametrize("name", MODULES)
@pytest.mark.parametrize("replay", [False, True])
@pytest.mark.parametrize("memory", [1, 8240, 11340, 65536, -1, 0, 65537])
def test_memory_variation_never_waives_positive_64_mib_cap(profiles, make_profile, name, replay, memory):
    engine = copy.deepcopy(profiles[name][engine_key(replay)])
    stdout = engine["transcript"].replace(f"vm_peak_kib={engine['vm_peak_kib']}\n", f"vm_peak_kib={memory}\n")
    engine.update(transcript=stdout, transcript_sha256=sha(stdout), vm_peak_kib=memory)
    engine["mathematical_transcript_sha256"] = bounded_transcript_sha256(stdout, parser=parser(name, replay))
    result = make_profile(name, **{"replay" if replay else "computation": engine})
    valid = 0 < memory <= 65536
    assert result["certificate"]["programs_and_transcripts_match_reviewed_executions"] is True
    assert result["certificate"]["resident_telemetry_respects_the_address_space_cap"] is valid
    assert result["certificate_verified"] is valid
    assert (result["evidence_sha256"] == EXPECTED[name]) is valid
    if not valid:
        assert_no_advance(result, name)


@pytest.mark.parametrize("name,parent", [(name, parent) for name in MODULES for parent in PARENTS[name]])
@pytest.mark.parametrize("failure", ["copied_seal", "rehashed_scope", "failed_certificate"])
def test_unapproved_prerequisite_never_advances_a_cursor(profiles, make_profile, monkeypatch, name, parent, failure):
    make_profile(name)
    changed = copy.deepcopy(parent_profile(profiles, parent))
    if failure == "failed_certificate":
        changed["certificate_verified"] = False
    else:
        changed["request"]["unreviewed"] = True
        if failure == "rehashed_scope":
            changed["evidence_sha256"] = profile_evidence_sha256(changed)
    monkeypatch.setattr(MODULES[name], "level25_heisenberg_augmented_four_block_" + PARENT_SUFFIXES[parent] + "_profile", lambda: changed)
    result = FACTORIES[name]()
    assert result["certificate"][PARENT_CHECKS[parent]] is False
    assert_no_advance(result, name)


@pytest.mark.parametrize("name", MODULES)
def test_fresh_parent_calls_and_literal_references_are_separate(profiles, make_profile, monkeypatch, name):
    make_profile(name)
    calls = []

    def fresh(parent):
        calls.append(parent)
        return copy.deepcopy(parent_profile(profiles, parent))

    for parent in PARENTS[name]:
        monkeypatch.setattr(
            MODULES[name],
            "level25_heisenberg_augmented_four_block_" + PARENT_SUFFIXES[parent] + "_profile",
            lambda parent=parent: fresh(parent),
        )
    for _ in range(2):
        result = FACTORIES[name]()
        assert result["certificate_verified"] is True
        assert result["review_reference_policy"] == {
            "kind": "declared_source_literals",
            "external_contents_verified": False,
            "receipt_references_are_current_approvals": False,
        }
        assert result["charge_scope"] == "named primary and independent sorted replay only; prerequisite executions are separate"
        for parent in PARENTS[name]:
            receipt = parent_profile(profiles, parent)
            assert result[parent + "_receipt_sha256"] == sha(canonical_json(receipt))
            assert result[parent + "_evidence_sha256"] == receipt["evidence_sha256"]
    assert calls == list(PARENTS[name]) * 2


@pytest.mark.parametrize("name", MODULES)
@pytest.mark.parametrize(
    "parent,field",
    [
        ("slice", "shape_stream_sha256"),
        ("slice", "representative_stream_sha256"),
        ("slice", "representative_count"),
        ("covariance", "slice_shape_sha256"),
        ("covariance", "slice_representative_sha256"),
        ("covariance", "representative_count"),
    ],
)
def test_live_parent_streams_and_domains_must_agree(profiles, make_profile, monkeypatch, name, parent, field):
    make_profile(name)
    changed = copy.deepcopy(parent_profile(profiles, parent))
    changed["computation"][field] = "0" * 64 if field.endswith("sha256") else 1
    changed["evidence_sha256"] = profile_evidence_sha256(changed)
    monkeypatch.setattr(MODULES[name], "level25_heisenberg_augmented_four_block_" + PARENT_SUFFIXES[parent] + "_profile", lambda: changed)
    result = FACTORIES[name]()
    check = (
        "prior_coverage_and_global_representative_stream_are_bound"
        if name == "5_8"
        else "accepted_cursor_9_and_global_representative_stream_are_bound"
    )
    assert result["certificate"][check] is False
    assert_no_advance(result, name)


@pytest.mark.parametrize(
    "name,parent,failure",
    [
        ("5_8", "first_context", "context"),
        ("5_8", "first_context", "exhausted"),
        ("5_8", "next_four", "context"),
        ("5_8", "next_four", "exhausted"),
        ("9_12", "prior_batch", "cursor"),
        ("9_12", "prior_batch", "accepted"),
        ("9_12", "prior_batch", "prefix"),
        ("9_12", "prior_batch", "exhausted"),
        *[(name, parent, "source") for name in MODULES for parent in PARENTS[name]],
    ],
)
def test_coverage_source_and_accepted_prefix_are_substantive_checks(profiles, make_profile, monkeypatch, name, parent, failure):
    make_profile(name)
    changed = copy.deepcopy(parent_profile(profiles, parent))
    if failure == "source":
        changed["source_manifest"]["records_sha256"] = "0" * 64
    elif parent == "first_context":
        if failure == "context":
            changed["context"]["j_d"] = 1
        else:
            changed["decision"]["context_exhausted"] = False
    elif parent == "next_four":
        if failure == "context":
            changed["context_prefix"][0]["shape_d"] += 1
        else:
            changed["decision"]["contexts_exhausted"] = [0, 1, 2]
    elif failure == "cursor":
        changed["decision"]["public_next_unaccepted_packed"] = 8
    elif failure == "accepted":
        changed["decision"]["accepted_exact"] = False
    elif failure == "exhausted":
        changed["decision"]["packed_representatives_exhausted"] = [5, 6, 7]
    else:
        changed["computation"]["representative_prefix_orbits"][0] += 1
    changed["evidence_sha256"] = profile_evidence_sha256(changed)
    monkeypatch.setattr(MODULES[name], "level25_heisenberg_augmented_four_block_" + PARENT_SUFFIXES[parent] + "_profile", lambda: changed)
    result = FACTORIES[name]()
    check = (
        "prior_coverage_and_global_representative_stream_are_bound"
        if name == "5_8"
        else "accepted_cursor_9_and_global_representative_stream_are_bound"
    )
    assert result["certificate"][check] is False
    assert_no_advance(result, name)


@pytest.mark.parametrize("name,parent", [(name, parent) for name in MODULES for parent in PARENTS[name]])
def test_prerequisite_runtime_variation_does_not_require_an_archived_raw_receipt(profiles, make_profile, monkeypatch, name, parent):
    make_profile(name)
    changed = copy.deepcopy(parent_profile(profiles, parent))
    changed["computation"]["compiler"] = "OTHER-EXPLICIT-RUNTIME-PROVENANCE"
    assert profile_evidence_sha256(changed) == changed["evidence_sha256"]
    monkeypatch.setattr(MODULES[name], "level25_heisenberg_augmented_four_block_" + PARENT_SUFFIXES[parent] + "_profile", lambda: changed)
    result = FACTORIES[name]()
    assert result["certificate_verified"] is True
    assert result["evidence_sha256"] == EXPECTED[name]
    assert result[parent + "_receipt_sha256"] != profiles[name][parent + "_receipt_sha256"]


@pytest.mark.parametrize("name", MODULES)
@pytest.mark.parametrize("replay", [False, True])
@pytest.mark.parametrize("field", ["compiler", "compiler_sha256", "compiler_version", "binary_sha256", "transcript"])
def test_compiled_provenance_is_retained_but_not_mathematical_identity(profiles, name, replay, field):
    result = copy.deepcopy(profiles[name])
    result[engine_key(replay)][field] = "OTHER-EXPLICIT-RUNTIME-PROVENANCE"
    assert profile_evidence_sha256(result) == EXPECTED[name]
    assert sha(canonical_json(result)) != sha(canonical_json(profiles[name]))


@pytest.mark.parametrize("name", MODULES)
@pytest.mark.parametrize("field", ["request", "not_certified", "certificate", "decision", "unexpected_claim"])
def test_other_claims_and_unknown_fields_remain_bound(profiles, name, field):
    result = copy.deepcopy(profiles[name])
    result[field] = {"unreviewed": True}
    assert profile_evidence_sha256(result) != EXPECTED[name]
    assert reviewed_profile_matches(result, EXPECTED[name]) is False


@pytest.mark.parametrize("name,replay", [(name, replay) for name in MODULES for replay in (False, True)])
@pytest.mark.parametrize("missing", ["compiler", "vm_peak_kib", "transcript_sha256", "mathematical_transcript_sha256"])
def test_missing_compiled_evidence_has_no_normalization_fallback(profiles, name, replay, missing):
    result = copy.deepcopy(profiles[name])
    del result[engine_key(replay)][missing]
    with pytest.raises(ScientificToolError):
        profile_evidence_sha256(result)


@pytest.mark.parametrize(
    "name,parent,missing",
    [(name, parent, missing) for name in MODULES for parent in PARENTS[name] for missing in ("_receipt_sha256", "_evidence_sha256")],
)
def test_missing_prerequisite_identities_have_no_fallback(profiles, name, parent, missing):
    result = copy.deepcopy(profiles[name])
    del result[parent + missing]
    with pytest.raises(ScientificToolError):
        profile_evidence_sha256(result)


@pytest.mark.parametrize("name", MODULES)
@pytest.mark.parametrize("missing", ["computation", "independent_sorted_join_replay"])
def test_missing_engine_has_no_normalization_fallback(profiles, name, missing):
    result = copy.deepcopy(profiles[name])
    del result[missing]
    with pytest.raises(ScientificToolError):
        profile_evidence_sha256(result)


@pytest.mark.parametrize("name", MODULES)
@pytest.mark.parametrize("replay", [False, True])
@pytest.mark.parametrize("failure", ["leading_zero", "plus", "leading_space", "trailing_space", "crlf", "extra_lf"])
def test_only_one_canonical_measured_memory_line_can_be_excluded(name, replay, failure):
    stdout = native_output(name, replay)
    metric = str(parser(name, replay)(stdout)["vm_peak_kib"])
    if failure == "crlf":
        stdout = stdout.replace("\n", "\r\n")
    elif failure == "extra_lf":
        stdout += "\n"
    else:
        replacement = {"leading_zero": "0" + metric, "plus": "+" + metric, "leading_space": " " + metric, "trailing_space": metric + " "}[
            failure
        ]
        stdout = stdout.replace("vm_peak_kib=" + metric + "\n", "vm_peak_kib=" + replacement + "\n")
    with pytest.raises(ScientificToolError):
        bounded_transcript_sha256(stdout, parser=parser(name, replay))


@pytest.mark.parametrize("name", MODULES)
def test_reports_render_raw_and_mathematical_identities(tmp_path, monkeypatch, profiles, name):
    from test_local_science_tools import _registry

    tool = "level25_heisenberg_augmented_four_block_" + SUFFIXES[name]
    monkeypatch.setattr(project_tools, tool + "_profile", lambda: copy.deepcopy(profiles[name]))
    result = _registry(tmp_path).invoke(tool + "_report", {"path": "research/evidence.md", "terminal_marker": "EVIDENCE_COMPLETE"})
    text = (tmp_path / "artifacts/research/evidence.md").read_text(encoding="utf-8")
    receipt = json.loads(text.split("```json\n", 1)[1].split("\n```", 1)[0])
    assert result["receipt_sha256"] == sha(canonical_json(receipt))
    assert result["evidence_sha256"] == EXPECTED[name] == profile_evidence_sha256(receipt)
    assert result["primary_mathematical_transcript_sha256"] == receipt["computation"]["mathematical_transcript_sha256"]
    assert result["replay_mathematical_transcript_sha256"] == receipt["independent_sorted_join_replay"]["mathematical_transcript_sha256"]
    assert "Mathematical evidence SHA-256: `" + EXPECTED[name] + "`" in text
    assert text.endswith("EVIDENCE_COMPLETE\n")


@pytest.mark.parametrize("name", MODULES)
@pytest.mark.parametrize("failure", ["failed_certificate", "copied_seal", "rehashed_unreviewed_scope"])
def test_failed_or_unreviewed_math_never_writes_a_certified_report(tmp_path, monkeypatch, profiles, name, failure):
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
def test_existing_science_assertions_with_explicit_saved_runtime(tmp_path, monkeypatch, name, kind):
    import test_local_science_tools as tests

    install_saved_representatives(monkeypatch)
    test = getattr(tests, "test_level25_heisenberg_augmented_four_block_" + SUFFIXES[name] + "_" + kind)
    test(tmp_path) if kind == "report" else test()


@pytest.mark.parametrize("name", MODULES)
@pytest.mark.parametrize("replay", [False, True])
def test_actual_trusted_cpp_reproduces_math_not_production_isolation(tmp_path, name, replay):
    compiler, limiter = shutil.which("g++"), shutil.which("prlimit")
    if compiler is None or limiter is None:
        pytest.skip("trusted-source Linux diagnostic requires g++ and prlimit")
    source = getattr(MODULES[name], "_REPLAY_PROGRAM" if replay else "_PROGRAM")
    binary = tmp_path / "trusted-diagnostic"
    environment = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
    compiled = subprocess.run(
        [compiler, "-O3", "-std=c++20", "-x", "c++", "-", "-o", str(binary)],
        input=source,
        text=True,
        capture_output=True,
        check=True,
        timeout=120,
        env=environment,
    )
    assert compiled.stderr == ""
    completed = subprocess.run(
        [limiter, "--as=67108864:67108864", "--", str(binary)], text=True, capture_output=True, check=True, timeout=900, env=environment
    )
    assert completed.stderr == ""
    assert sha(source) == getattr(MODULES[name], "_REVIEWED_REPLAY_PROGRAM_SHA256" if replay else "_REVIEWED_PROGRAM_SHA256")
    parse = parser(name, replay)
    actual, expected = parse(completed.stdout), parse(native_output(name, replay))
    assert 0 < actual.pop("vm_peak_kib") <= 65536
    expected.pop("vm_peak_kib")
    assert actual == expected
    assert bounded_transcript_sha256(completed.stdout, parser=parse) == getattr(
        MODULES[name], "_REVIEWED_REPLAY_MATHEMATICAL_TRANSCRIPT_SHA256" if replay else "_REVIEWED_MATHEMATICAL_TRANSCRIPT_SHA256"
    )
