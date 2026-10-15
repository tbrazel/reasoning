"""Reviewed sweep mathematics; saved native output is not sandbox acceptance."""

from __future__ import annotations

import copy
import shutil
import subprocess
from functools import partial

import pytest
from test_science_evidence import FIXTURES, sha
from test_science_representative_evidence import changed_value, install_saved_representatives, leaf_paths

from reasoning_ledger.agents import heisenberg_level25_four_block_ad5_representative_sweep as sweep
from reasoning_ledger.agents import project_tools
from reasoning_ledger.agents.science_evidence import bounded_transcript_sha256, profile_evidence_sha256, reviewed_profile_matches
from reasoning_ledger.agents.scientific import ScientificToolError
from reasoning_ledger.canonical import canonical_json

FACTORY = sweep.level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_batch_13_16_profile
EXPECTED = "af8541f6d8b8d7ba054af7c80a683048b2f85d59c14179c99e839ce1ed4ba525"
TOOL = "level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_batch_13_16"


def engine_key(replay):
    return "independent_sorted_join_replay" if replay else "computation"


def parser(replay=False):
    return partial(sweep._parse_sweep_transcript, replay=replay)


def native_output(replay=False):
    return (FIXTURES / ("ad5_sweep_" + ("sorted" if replay else "primary") + "_v1.txt")).read_text(encoding="utf-8")


def install_saved_sweep(patch):
    install_saved_representatives(patch)

    def saved_run(program, **kwargs):
        assert kwargs["preserve_exact_transcript"] is True
        assert kwargs["runtime_address_space_bytes"] == 67108864
        assert kwargs["timeout_seconds"] == 900
        replay = program == sweep._REPLAY_PROGRAM
        assert replay or program == sweep._PROGRAM
        stdout = native_output(replay)
        return {
            "program_sha256": sha(program),
            "transcript_sha256": sha(stdout),
            "transcript": stdout,
            "protocol_version": 1,
            "compiler": "TEST-ONLY-RUNTIME-FIXTURE",
            "compiler_sha256": "0" * 64,
            "compiler_version": "TEST-ONLY-RUNTIME-FIXTURE",
            "binary_sha256": ("1" if replay else "2") * 64,
            "runtime_address_space_limit_bytes": 67108864,
        }

    patch.setattr(sweep, "_run_source_hashed_program", saved_run)


@pytest.fixture(scope="module")
def profile():
    with pytest.MonkeyPatch.context() as patch:
        install_saved_sweep(patch)
        return FACTORY()


@pytest.fixture
def make_profile(profile, monkeypatch):
    def make(computation=None, replay=None, prior=None):
        monkeypatch.setattr(
            sweep,
            "_run_engine",
            lambda program, **kwargs: copy.deepcopy(
                (profile["independent_sorted_join_replay"] if replay is None else replay)
                if kwargs["replay"]
                else (profile["computation"] if computation is None else computation)
            ),
        )
        monkeypatch.setattr(
            sweep,
            "level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_batch_9_12_profile",
            lambda: copy.deepcopy(profile["bundle"]["prior_profile"] if prior is None else prior),
        )
        return FACTORY()

    return make


def test_saved_sweep_is_reviewed(profile):
    assert profile["certificate_verified"] is True
    assert profile["evidence_sha256"] == EXPECTED == profile_evidence_sha256(profile)
    assert sweep.require_reviewed_sweep_profile(profile) == profile["bundle"]
    assert profile["decision"]["public_next_unaccepted_packed"] == 13
    assert profile["decision"]["next_ordinal_if_committed"] == 17
    assert profile["decision"]["exact_key_match_counts"] == [0, 0, 0, 1]
    assert profile["bundle"]["paired_subreceipt"]["aggregate"]["combined_charge_total"] == 434942152


def assert_unaccepted(result):
    assert result["certificate_verified"] is False
    assert result["decision"]["pair_receipt_accepted"] is False
    assert result["decision"]["public_next_unaccepted_packed"] == 13
    assert result["decision"]["next_ordinal_if_committed"] == 13
    assert result["decision"]["packed_representatives_exhausted"] == []
    assert result["decision"]["common_zero_found"] is False
    assert result["decision"]["full_contraction_authorized"] is False
    with pytest.raises(ScientificToolError):
        sweep.require_reviewed_sweep_profile(result)


@pytest.mark.parametrize(
    "replay,path", [(replay, path) for replay in (False, True) for path in leaf_paths(parser(replay)(native_output(replay)))]
)
def test_every_cached_parsed_field_is_bound_to_exact_raw_output(profile, make_profile, replay, path):
    engine = copy.deepcopy(profile[engine_key(replay)])
    value = engine
    for part in path[:-1]:
        value = value[part]
    original = value[path[-1]]
    value[path[-1]] = (
        ("1" if original[0] == "0" else "0") + original[1:]
        if isinstance(path[-1], str) and path[-1].endswith("sha256")
        else changed_value(original)
    )
    assert_unaccepted(make_profile(**{"replay" if replay else "computation": engine}))


@pytest.mark.parametrize(
    "replay,field",
    [
        (replay, line.split("=", 1)[0])
        for replay in (False, True)
        for line in native_output(replay).splitlines()[1:-1]
        if not line.startswith("vm_peak_kib=")
    ],
)
def test_rehashed_changed_output_is_not_reviewed(profile, make_profile, replay, field):
    engine = copy.deepcopy(profile[engine_key(replay)])
    lines = engine["transcript"].splitlines()
    for index, line in enumerate(lines):
        if line.startswith(field + "="):
            value = line.split("=", 1)[1]
            if field.endswith("sha256"):
                replacement = ("1" if value[0] == "0" else "0") + value[1:]
            elif value and all(part.lstrip("-").isdigit() for part in value.split(",")):
                parts = value.split(",")
                replacement = ",".join([str(int(parts[0]) + 1), *parts[1:]])
            else:
                replacement = value + "_UNREVIEWED"
            lines[index] = field + "=" + replacement
    stdout = "\n".join(lines) + "\n"
    try:
        engine.update(
            parser(replay)(stdout),
            transcript=stdout,
            transcript_sha256=sha(stdout),
            mathematical_transcript_sha256=bounded_transcript_sha256(stdout, parser=parser(replay)),
        )
        result = make_profile(**{"replay" if replay else "computation": engine})
    except ScientificToolError:
        return
    assert_unaccepted(result)


@pytest.mark.parametrize("replay,line", [(replay, line) for replay in (False, True) for line in native_output(replay).splitlines()[1:-1]])
def test_duplicate_transcript_fields_are_rejected(replay, line):
    stdout = native_output(replay).replace("REASONING_END\n", line + "\nREASONING_END\n")
    with pytest.raises(ScientificToolError):
        parser(replay)(stdout)


@pytest.mark.parametrize("replay", [False, True])
@pytest.mark.parametrize("field", ["context_0_stream_hash_byte_count", "context_0_stream_hash_charge", "context_0_b_keyed_records_sha256"])
def test_invalid_stream_evidence_is_a_tool_error(replay, field):
    stdout = native_output(replay)
    line = next(line for line in stdout.splitlines() if line.startswith(field + "="))
    with pytest.raises(ScientificToolError):
        parser(replay)(stdout.replace(line + "\n", field + "=not-valid\n"))


@pytest.mark.parametrize("replay", [False, True])
@pytest.mark.parametrize(
    "field", ["program_sha256", "transcript_sha256", "mathematical_transcript_sha256", "runtime_address_space_limit_bytes"]
)
def test_copied_source_raw_math_and_runtime_caps_do_not_approve(profile, make_profile, replay, field):
    engine = copy.deepcopy(profile[engine_key(replay)])
    engine[field] = "0" * 64 if field.endswith("sha256") else 1
    assert_unaccepted(make_profile(**{"replay" if replay else "computation": engine}))


@pytest.mark.parametrize("replay", [False, True])
@pytest.mark.parametrize("memory", [1, 8276, 11372, 65535, -1, 0, 65536, 65537])
def test_memory_provenance_varies_but_original_strict_cap_is_required(profile, make_profile, replay, memory):
    engine = copy.deepcopy(profile[engine_key(replay)])
    stdout = engine["transcript"].replace(f"vm_peak_kib={engine['vm_peak_kib']}\n", f"vm_peak_kib={memory}\n")
    engine.update(transcript=stdout, transcript_sha256=sha(stdout), vm_peak_kib=memory)
    engine["mathematical_transcript_sha256"] = bounded_transcript_sha256(stdout, parser=parser(replay))
    result = make_profile(**{"replay" if replay else "computation": engine})
    valid = 0 < memory < 65536
    assert result["certificate_verified"] is valid
    assert (result["evidence_sha256"] == EXPECTED) is valid
    if valid:
        assert sweep.require_reviewed_sweep_profile(result) == result["bundle"]
    else:
        assert_unaccepted(result)


@pytest.mark.parametrize("replay", [False, True])
@pytest.mark.parametrize("wall", [1, 2, 900, 0, -1, 901])
def test_wall_measurements_do_not_change_math_but_cannot_exceed_budget(profile, make_profile, replay, wall):
    engine = copy.deepcopy(profile[engine_key(replay)])
    engine["wall_seconds"] = wall
    result = make_profile(**{"replay" if replay else "computation": engine})
    valid = 0 < wall <= 900
    assert result["certificate_verified"] is valid
    assert (result["evidence_sha256"] == EXPECTED) is valid
    if not valid:
        assert_unaccepted(result)


@pytest.mark.parametrize("failure", ["copied_seal", "rehashed_scope", "failed_certificate", "cursor", "accepted", "prefix", "source"])
def test_fresh_prior_approval_and_coverage_are_required(profile, make_profile, failure):
    prior = copy.deepcopy(profile["bundle"]["prior_profile"])
    if failure == "failed_certificate":
        prior["certificate_verified"] = False
    elif failure in ("copied_seal", "rehashed_scope"):
        prior["request"]["unreviewed"] = True
    elif failure == "cursor":
        prior["decision"]["public_next_unaccepted_packed"] = 12
    elif failure == "accepted":
        prior["decision"]["accepted_exact"] = False
    elif failure == "prefix":
        prior["computation"]["representative_prefix_orbits"][0] += 1
    else:
        prior["source_manifest"]["records_sha256"] = "0" * 64
    if failure != "copied_seal":
        prior["evidence_sha256"] = profile_evidence_sha256(prior)
    assert_unaccepted(make_profile(prior=prior))


def test_each_profile_recomputes_prior_and_separates_actual_raw_binding(profile, make_profile, monkeypatch):
    make_profile()
    calls = []

    def fresh():
        calls.append(True)
        return copy.deepcopy(profile["bundle"]["prior_profile"])

    monkeypatch.setattr(sweep, "level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_batch_9_12_profile", fresh)
    for _ in range(2):
        result = FACTORY()
        reference = result["bundle"]["paired_subreceipt"]["prior"]
        assert reference["accepted_receipt_sha256"] == sha(canonical_json(result["bundle"]["prior_profile"]))
        assert reference["accepted_evidence_sha256"] == sweep._REPRESENTATIVE_9_12_EVIDENCE_SHA256
        assert "accepted_receipt_artifact_sha256" not in reference
        assert result["bundle"]["source_manifest"]["review_reference_policy"] == {
            "kind": "declared_source_literals",
            "external_contents_verified": False,
        }
        assert result["certificate_verified"] is True
    assert calls == [True, True]


@pytest.mark.parametrize("replay", [False, True])
@pytest.mark.parametrize("field", ["compiler", "compiler_sha256", "compiler_version", "binary_sha256"])
def test_fresh_compiled_provenance_changes_raw_receipts_not_mathematics(profile, make_profile, replay, field):
    engine = copy.deepcopy(profile[engine_key(replay)])
    engine[field] = "3" * 64 if field.endswith("sha256") else "OTHER-EXPLICIT-RUNTIME-PROVENANCE"
    result = make_profile(**{"replay" if replay else "computation": engine})
    assert result["certificate_verified"] is True
    assert result["evidence_sha256"] == EXPECTED
    assert sha(canonical_json(result)) != sha(canonical_json(profile))
    assert sweep.require_reviewed_sweep_profile(result) == result["bundle"]


@pytest.mark.parametrize("engine", ["primary_engine_receipt", "sorted_engine_receipt"])
@pytest.mark.parametrize("field", ["context", "resources", "source", "certificate", "algorithm"])
def test_self_rehashed_engine_claims_are_not_reviewed_approval(profile, engine, field):
    changed = copy.deepcopy(profile)
    receipt = changed["bundle"][engine]
    if field == "context":
        receipt["contexts"][0]["b_keyed_records_sha256"] = "0" * 64
    elif field == "resources":
        receipt["resources"]["charge_total"] += 1
    elif field == "source":
        receipt["source"]["source_records_sha256"] = "0" * 64
    elif field == "certificate":
        receipt["certificate"]["unreviewed"] = True
    else:
        receipt["engine"]["algorithm_id"] = "UNREVIEWED"
    bundle = changed["bundle"]
    bundle[engine] = sweep._seal(receipt)
    bundle["paired_subreceipt"] = sweep._pair_receipt(
        bundle["primary_engine_receipt"],
        bundle["sorted_engine_receipt"],
        bundle["paired_subreceipt"]["source_manifest_sha256"],
        prior_profile=bundle["prior_profile"],
    )
    sweep.validate_representative_bundle(bundle)
    changed["evidence_sha256"] = profile_evidence_sha256(changed)
    with pytest.raises(ScientificToolError):
        sweep.require_reviewed_sweep_profile(changed)


@pytest.mark.parametrize("failure", ["copied_seal", "rehashed_scope", "failed_certificate", "raw_body", "raw_hash", "raw_bundle_only"])
@pytest.mark.parametrize("action", ["report", "summary", "commit", "macro"])
def test_all_artifact_paths_refuse_unreviewed_or_inconsistent_math(tmp_path, monkeypatch, profile, failure, action):
    from test_local_science_tools import _registry

    changed = copy.deepcopy(profile)
    if failure == "failed_certificate":
        changed["certificate_verified"] = False
    elif failure == "raw_body":
        changed["computation"]["transcript"] += "\n"
    elif failure == "raw_hash":
        changed["computation"]["transcript_sha256"] = "0" * 64
    elif failure == "raw_bundle_only":
        changed = changed["bundle"]
    else:
        changed["request"]["unreviewed"] = True
        if failure == "rehashed_scope":
            changed["evidence_sha256"] = profile_evidence_sha256(changed)
    registry = _registry(tmp_path)
    monkeypatch.setattr(project_tools, TOOL + "_profile", lambda: changed)
    if action == "report":
        invoke, arguments = TOOL + "_report", {"path": "research/result.md", "terminal_marker": "COMPLETE"}
    else:
        registry.invoke("artifact_write", {"path": "research/input.md", "text": "```json\n" + canonical_json(changed) + "\n```\n"})
        if action == "summary":
            invoke = "level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_receipt_summary"
            arguments = {"path": "research/input.md"}
        elif action == "commit":
            invoke, arguments = TOOL + "_commit", {"receipt_path": "research/input.md", "chain_tip_path": "research/tip.json"}
        else:
            invoke = "level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_macro_report"
            arguments = {"subreceipt_paths": ["research/input.md"], "path": "research/result.md", "terminal_marker": "COMPLETE"}
    with pytest.raises(ScientificToolError):
        registry.invoke(invoke, arguments)
    assert not (tmp_path / "artifacts/research/result.md").exists()
    assert not (tmp_path / "artifacts/research/tip.json").exists()


@pytest.mark.parametrize("field", ["request", "decision", "not_certified", "certificate", "unexpected_claim"])
def test_all_unknown_or_changed_scope_claims_remain_bound(profile, field):
    changed = copy.deepcopy(profile)
    changed[field] = {"unreviewed": True}
    assert profile_evidence_sha256(changed) != EXPECTED
    assert reviewed_profile_matches(changed, EXPECTED) is False


@pytest.mark.parametrize(
    "path",
    [
        ("prior_batch_receipt_sha256",),
        ("prior_batch_evidence_sha256",),
        ("bundle", "prior_profile"),
        *[
            ("bundle", name, field)
            for name in ("primary_engine_receipt", "sorted_engine_receipt")
            for field in ("canonical_payload_sha256", "attempt_id")
        ],
        *[
            ("bundle", name, "resources", field)
            for name in ("primary_engine_receipt", "sorted_engine_receipt")
            for field in ("vm_peak_bytes", "wall_seconds")
        ],
        *[(name, "wall_seconds") for name in ("computation", "independent_sorted_join_replay")],
        ("bundle", "paired_subreceipt", "prior", "accepted_receipt_sha256"),
    ],
)
def test_missing_required_runtime_and_parent_evidence_has_no_projection_fallback(profile, path):
    changed = copy.deepcopy(profile)
    node = changed
    for part in path[:-1]:
        node = node[part]
    del node[path[-1]]
    with pytest.raises(ScientificToolError):
        profile_evidence_sha256(changed)
    with pytest.raises(ScientificToolError):
        sweep.require_reviewed_sweep_profile(changed)


def test_prior_runtime_variation_keeps_math_and_binds_the_actual_snapshot(profile, make_profile):
    prior = copy.deepcopy(profile["bundle"]["prior_profile"])
    prior["computation"]["compiler"] = "OTHER-EXPLICIT-RUNTIME-PROVENANCE"
    assert profile_evidence_sha256(prior) == prior["evidence_sha256"]
    result = make_profile(prior=prior)
    assert result["certificate_verified"] is True
    assert result["evidence_sha256"] == EXPECTED
    assert result["prior_batch_receipt_sha256"] != profile["prior_batch_receipt_sha256"]
    assert sweep.require_reviewed_sweep_profile(result) == result["bundle"]


def test_same_executable_claim_cannot_be_accepted_as_two_independent_engines(profile, make_profile):
    replay = copy.deepcopy(profile["independent_sorted_join_replay"])
    replay["binary_sha256"] = profile["computation"]["binary_sha256"]
    result = make_profile(replay=replay)
    assert result["bundle"]["paired_subreceipt"]["terminal_status"] != "ACCEPTED_EMPTY"
    assert_unaccepted(result)


@pytest.mark.parametrize(
    "test_name",
    [
        "test_level25_ad5_representative_join_batch_13_16_sweep_profile",
        "test_level25_ad5_sweep_hash_cursor_resource_and_witness_faults",
        "test_level25_ad5_sweep_macroreceipt_checks_sixty_four_context_arithmetic",
        "test_level25_ad5_sweep_report_and_exactly_once_chain_commit",
    ],
)
def test_existing_protocol_checks_with_explicit_saved_runtime(tmp_path, monkeypatch, profile, test_name):
    import test_local_science_tools as tests

    test = getattr(tests, test_name)
    if "report_and" in test_name:
        test(tmp_path, monkeypatch, profile)
    else:
        test(profile)


@pytest.mark.parametrize("replay", [False, True])
def test_actual_trusted_cpp_reproduces_reviewed_math_not_isolation(tmp_path, replay):
    compiler, limiter = shutil.which("g++"), shutil.which("prlimit")
    if compiler is None or limiter is None:
        pytest.skip("trusted-source Linux diagnostic requires g++ and prlimit")
    source = sweep._REPLAY_PROGRAM if replay else sweep._PROGRAM
    binary = tmp_path / "trusted-sweep"
    environment = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
    subprocess.run(
        [compiler, "-O3", "-std=c++20", "-x", "c++", "-", "-o", str(binary)],
        input=source,
        text=True,
        capture_output=True,
        check=True,
        timeout=120,
        env=environment,
    )
    completed = subprocess.run(
        [limiter, "--as=67108864:67108864", "--", str(binary)], text=True, capture_output=True, check=True, timeout=900, env=environment
    )
    assert completed.stderr == ""
    actual, expected = parser(replay)(completed.stdout), parser(replay)(native_output(replay))
    assert 0 < actual.pop("vm_peak_kib") < 65536
    expected.pop("vm_peak_kib")
    assert actual == expected
    assert bounded_transcript_sha256(completed.stdout, parser=parser(replay)) == (
        sweep._REVIEWED_REPLAY_MATHEMATICAL_TRANSCRIPT_SHA256 if replay else sweep._REVIEWED_MATHEMATICAL_TRANSCRIPT_SHA256
    )
