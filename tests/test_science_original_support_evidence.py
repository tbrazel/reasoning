"""Trusted census diagnostics and saved fixtures are not sandbox acceptance."""

from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess

import pytest
from test_science_evidence import FIXTURES, sha

from reasoning_ledger.agents import heisenberg_level25_alphabet as alphabet
from reasoning_ledger.agents import heisenberg_level25_three_block as nonconsecutive
from reasoning_ledger.agents import heisenberg_level25_three_block_consecutive as consecutive
from reasoning_ledger.agents import heisenberg_level25_two_block as two_block
from reasoning_ledger.agents import project_tools
from reasoning_ledger.agents.science_evidence import certified_receipt_json, profile_evidence_sha256, reviewed_profile_matches
from reasoning_ledger.agents.scientific import ScientificToolError
from reasoning_ledger.canonical import canonical_json

MODULES = {"alphabet": alphabet, "two_block": two_block, "nonconsecutive": nonconsecutive, "consecutive": consecutive}
TOOLS = {
    "alphabet": "level25_heisenberg_balanced_five_pivot_pure_power_alphabet",
    "two_block": "level25_heisenberg_balanced_five_pivot_two_block_rational",
    "nonconsecutive": "level25_heisenberg_balanced_five_pivot_nonconsecutive_three_block_rational",
    "consecutive": "level25_heisenberg_balanced_five_pivot_consecutive_three_block_rational",
}
FACTORIES = {name: getattr(module, TOOLS[name] + "_profile") for name, module in MODULES.items()}
PROGRAMS = {name: alphabet._program() if name == "alphabet" else module._PROGRAM for name, module in MODULES.items()}
TIMEOUTS = {"alphabet": 10, "two_block": 30, "nonconsecutive": 60, "consecutive": 60}
PARENT_FACTORY = TOOLS["alphabet"] + "_profile"


def native_output(name):
    return (FIXTURES / ("original_support_" + name + "_v1.txt")).read_text(encoding="utf-8")


def native_receipt(name):
    stdout = native_output(name)
    return {
        "program_sha256": sha(PROGRAMS[name]),
        "transcript_sha256": sha(stdout),
        "transcript": stdout,
        "protocol_version": 1,
        "compiler": "TEST-ONLY-RUNTIME-FIXTURE",
        "compiler_sha256": "0" * 64,
        "compiler_version": "TEST-ONLY-RUNTIME-FIXTURE",
        "binary_sha256": "0" * 64,
        **MODULES[name]._parse_transcript(stdout),
    }


def install_saved_support(patch):
    patch.setattr(alphabet, "_run_program", lambda: native_receipt("alphabet"))
    patch.setattr(two_block, "_run_program", lambda: native_receipt("two_block"))
    programs = {program: name for name, program in PROGRAMS.items()}

    def saved_run(program, **kwargs):
        name = programs[program]
        assert kwargs["preserve_exact_transcript"] is True
        assert kwargs.get("timeout_seconds", 60) == TIMEOUTS[name]
        assert "runtime_address_space_bytes" not in kwargs
        return native_receipt(name)

    patch.setattr(nonconsecutive, "_run_source_hashed_program", saved_run)
    patch.setattr(consecutive, "_run_source_hashed_program", saved_run)


def expected(name):
    constant = {
        "alphabet": "_PURE_POWER_EVIDENCE_SHA256",
        "two_block": "_TWO_BLOCK_EVIDENCE_SHA256",
        "nonconsecutive": "_NONCONSECUTIVE_EVIDENCE_SHA256",
        "consecutive": "_CONSECUTIVE_EVIDENCE_SHA256",
    }[name]
    return getattr(MODULES[name], constant)


@pytest.fixture(scope="module")
def profiles():
    with pytest.MonkeyPatch.context() as patch:
        install_saved_support(patch)
        return {name: factory() for name, factory in FACTORIES.items()}


def changed(value):
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + 1
    if isinstance(value, str):
        return ("0" if value[0] != "0" else "1") + value[1:] if len(value) == 64 else value + "unreviewed"
    if isinstance(value, list):
        result = copy.deepcopy(value)
        if not result:
            return ["unreviewed"]
        result[len(result) // 2] = changed(result[len(result) // 2])
        return result
    if isinstance(value, dict):
        result = copy.deepcopy(value)
        if not result:
            return {"unreviewed": True}
        key = next(iter(result))
        result[key] = changed(result[key])
        return result
    if value is None:
        return 1
    raise AssertionError(value)


def recompute(profiles, patch, name, computation=None, parent=None, witness=None):
    module = MODULES[name]
    patch.setattr(module, "_run_program", lambda: copy.deepcopy(profiles[name]["computation"] if computation is None else computation))
    if name != "alphabet":
        patch.setattr(module, PARENT_FACTORY, lambda: copy.deepcopy(profiles["alphabet"] if parent is None else parent))
    if witness is not None:
        patch.setattr(module, "_replay_witness", lambda comp: copy.deepcopy(witness))
    return FACTORIES[name]()


@pytest.mark.parametrize("name", FACTORIES)
def test_exact_saved_output_and_real_python_checks_have_reviewed_scope(profiles, name):
    profile = profiles[name]
    assert profile["schema"].endswith(".v2")
    assert profile["certificate_verified"] is True
    assert profile["evidence_sha256"] == expected(name) == profile_evidence_sha256(profile)
    assert reviewed_profile_matches(profile, expected(name))
    assert certified_receipt_json(profile) == canonical_json(profile)
    assert profile["computation"]["transcript"] == native_output(name)
    assert profile["computation"]["transcript_sha256"] == MODULES[name]._REVIEWED_TRANSCRIPT_SHA256
    assert sha(PROGRAMS[name]) == MODULES[name]._REVIEWED_PROGRAM_SHA256


@pytest.mark.parametrize("name", PROGRAMS)
def test_genuine_compiled_mathematics_reproduces_complete_reviewed_transcript(tmp_path, name):
    compiler = shutil.which("g++")
    if compiler is None:
        pytest.skip("g++ unavailable for trusted-source mathematical diagnostic")
    binary = tmp_path / "trusted-diagnostic"
    subprocess.run(
        [compiler, "-O3", "-std=c++20", "-x", "c++", "-", "-o", str(binary)],
        input=PROGRAMS[name], text=True, capture_output=True, check=True, timeout=TIMEOUTS[name],
    )
    result = subprocess.run([str(binary)], text=True, capture_output=True, check=True, timeout=TIMEOUTS[name])
    assert not result.stderr
    assert result.stdout == native_output(name)


@pytest.mark.parametrize("name", ["nonconsecutive", "consecutive"])
def test_genuine_python_replay_evaluates_all_equations_and_full_jacobian(profiles, name):
    replay = nonconsecutive._replay_witness(MODULES[name]._parse_transcript(native_output(name)))
    assert replay == profiles[name]["first_witness_replay"]
    assert replay["nonzero_form_count"] == 0
    assert replay["jacobian_rank"] == 14
    assert replay["projective_tangent_dimension_lower_bound"] == 10
    residues = {index % 5 for index, value in enumerate(replay["point"]) if value}
    assert residues == set(profiles[name]["computation"]["support_residues"])


def leaf_paths(value, path=()):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from leaf_paths(child, (*path, key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from leaf_paths(child, (*path, index))
    else:
        yield path


PARSED_CASES = [(name, path) for name, module in MODULES.items() for path in leaf_paths(module._parse_transcript(native_output(name)))]
LINE_CASES = [(name, line.split("=", 1)[0]) for name in MODULES for line in native_output(name).splitlines()[1:-1]]


@pytest.mark.parametrize("name,path", PARSED_CASES)
def test_every_cached_native_leaf_is_bound_to_reviewed_output(profiles, monkeypatch, name, path):
    computation = copy.deepcopy(profiles[name]["computation"])
    container = computation
    for key in path[:-1]:
        container = container[key]
    container[path[-1]] = changed(container[path[-1]])
    result = recompute(profiles, monkeypatch, name, computation=computation)
    assert result["certificate_verified"] is False
    assert result["decision"]["status"] == "PROFILE_INVALID"
    with pytest.raises(ScientificToolError, match="report not written"):
        certified_receipt_json(result)


@pytest.mark.parametrize("name,field", LINE_CASES)
def test_duplicate_native_fields_are_refused(name, field):
    stdout = native_output(name)
    line = next(line for line in stdout.splitlines() if line.startswith(field + "="))
    with pytest.raises(ScientificToolError, match="duplicate"):
        MODULES[name]._parse_transcript(stdout.replace("REASONING_END\n", line + "\nREASONING_END\n"))


@pytest.mark.parametrize("name,field", LINE_CASES)
def test_changed_rehashed_native_bytes_cannot_approve_new_mathematics(profiles, monkeypatch, name, field):
    computation = copy.deepcopy(profiles[name]["computation"])
    stdout = computation["transcript"]
    line = next(line for line in stdout.splitlines() if line.startswith(field + "="))
    values = line.split("=", 1)[1]
    altered = re.sub(r"\d+(?!.*\d)", lambda match: str(int(match.group()) + 1), values)
    stdout = stdout.replace(line + "\n", field + "=" + altered + "\n")
    computation.update(MODULES[name]._parse_transcript(stdout), transcript=stdout, transcript_sha256=sha(stdout))
    result = recompute(profiles, monkeypatch, name, computation=computation)
    assert result["certificate_verified"] is False
    assert result["decision"]["status"] == "PROFILE_INVALID"
    assert result["evidence_sha256"] != expected(name)


@pytest.mark.parametrize("name", ["two_block", "nonconsecutive", "consecutive"])
@pytest.mark.parametrize("mutation", ["certificate", "copied_seal", "rehashed_scope", "runtime_only"])
def test_fresh_prerequisite_scope_not_raw_runtime_identity_controls_approval(profiles, monkeypatch, name, mutation):
    parent = copy.deepcopy(profiles["alphabet"])
    if mutation == "certificate":
        parent["certificate_verified"] = False
    elif mutation in ("copied_seal", "rehashed_scope"):
        parent["request"]["unreviewed"] = True
        if mutation == "rehashed_scope":
            parent["evidence_sha256"] = profile_evidence_sha256(parent)
    else:
        parent["computation"]["compiler"] = "/another/compiler"
    result = recompute(profiles, monkeypatch, name, parent=parent)
    valid = mutation == "runtime_only"
    assert result["certificate_verified"] is valid
    if valid:
        assert result["evidence_sha256"] == expected(name)
        if name == "two_block":
            assert result["pure_power_prerequisite"]["receipt_sha256"] != profiles[name]["pure_power_prerequisite"]["receipt_sha256"]
        else:
            assert result["pure_power_prerequisite_receipt_sha256"] != profiles[name]["pure_power_prerequisite_receipt_sha256"]
    else:
        assert result["decision"]["status"] == "PROFILE_INVALID"


@pytest.mark.parametrize("name", ["two_block", "nonconsecutive", "consecutive"])
def test_every_invocation_obtains_a_new_prerequisite(profiles, monkeypatch, name):
    calls = []
    recompute(profiles, monkeypatch, name)

    def fresh():
        calls.append(True)
        return copy.deepcopy(profiles["alphabet"])

    monkeypatch.setattr(MODULES[name], PARENT_FACTORY, fresh)
    assert FACTORIES[name]()["certificate_verified"] is True
    assert FACTORIES[name]()["certificate_verified"] is True
    assert len(calls) == 2


@pytest.mark.parametrize("name", MODULES)
@pytest.mark.parametrize("field", ["compiler", "compiler_sha256", "compiler_version", "binary_sha256"])
def test_execution_provenance_varies_without_changing_mathematics(profiles, monkeypatch, name, field):
    computation = copy.deepcopy(profiles[name]["computation"])
    computation[field] = changed(computation[field])
    result = recompute(profiles, monkeypatch, name, computation=computation)
    assert result["certificate_verified"] is True
    assert result["evidence_sha256"] == expected(name)
    assert result["computation"][field] == computation[field]
    assert sha(canonical_json(result)) != sha(canonical_json(profiles[name]))


@pytest.mark.parametrize("name", MODULES)
@pytest.mark.parametrize("mutation", ["certificate", "scope", "unknown", "request", "decision", "limits"])
def test_failed_or_self_rehashed_scopes_cannot_write_certified_reports(profiles, monkeypatch, tmp_path, name, mutation):
    from test_local_science_tools import _registry

    profile = copy.deepcopy(profiles[name])
    if mutation == "certificate":
        profile["certificate_verified"] = False
    elif mutation == "scope":
        profile["not_certified"] = []
    elif mutation == "request":
        profile["request"]["unreviewed"] = True
    elif mutation == "decision":
        profile["decision"]["status"] = "FULL_CAMPAIGN_COMPLETE"
    elif mutation == "limits":
        profile["execution_limits"]["primary_hard_address_space_bytes"] = 64 * 1024 * 1024
    else:
        profile["unreviewed"] = True
    profile["evidence_sha256"] = profile_evidence_sha256(profile)
    monkeypatch.setattr(project_tools, TOOLS[name] + "_profile", lambda: profile)
    with pytest.raises(ScientificToolError, match="report not written"):
        _registry(tmp_path).invoke(TOOLS[name] + "_report", {"path": "research/refused.md", "terminal_marker": "END"})
    assert not (tmp_path / "artifacts/research/refused.md").exists()


@pytest.mark.parametrize("name", MODULES)
@pytest.mark.parametrize("rehashed", [False, True])
def test_changed_duplicate_native_body_is_not_published_with_copied_approval(profiles, name, rehashed):
    profile = copy.deepcopy(profiles[name])
    profile["computation"]["transcript"] += "\n"
    if rehashed:
        profile["computation"]["transcript_sha256"] = sha(profile["computation"]["transcript"])
    profile["evidence_sha256"] = profile_evidence_sha256(profile)
    with pytest.raises(ScientificToolError, match="report not written"):
        certified_receipt_json(profile)


@pytest.mark.parametrize("name", MODULES)
def test_valid_reports_render_both_identities_and_complete_receipts(profiles, monkeypatch, tmp_path, name):
    from test_local_science_tools import _registry

    monkeypatch.setattr(project_tools, TOOLS[name] + "_profile", lambda: copy.deepcopy(profiles[name]))
    result = _registry(tmp_path).invoke(TOOLS[name] + "_report", {"path": "research/approved.md", "terminal_marker": "SUPPORT_END"})
    text = (tmp_path / "artifacts/research/approved.md").read_text(encoding="utf-8")
    assert result["certificate_verified"] is True
    assert result["evidence_sha256"] == expected(name)
    assert result["receipt_sha256"] == sha(canonical_json(profiles[name]))
    assert result["evidence_sha256"] in text and result["receipt_sha256"] in text
    assert json.loads(text.split("```json\n", 1)[1].split("\n```", 1)[0]) == json.loads(canonical_json(profiles[name]))
    assert text.endswith("SUPPORT_END\n")


@pytest.mark.parametrize("name", MODULES)
@pytest.mark.parametrize("mutation", ["frame", "end", "noninteger", "unknown", "missing", "no_equals"])
def test_malformed_native_output_is_refused(name, mutation):
    lines = native_output(name).splitlines()
    if mutation == "frame":
        lines[0] = "UNREVIEWED_FRAME"
    elif mutation == "end":
        lines[-1] = "UNREVIEWED_END"
    elif mutation == "noninteger":
        lines[2] = lines[2].split("=", 1)[0] + "=invalid"
    elif mutation == "unknown":
        lines.insert(-1, "unreviewed=1")
    elif mutation == "missing":
        del lines[1]
    else:
        lines[1] = "invalid"
    with pytest.raises(ScientificToolError):
        MODULES[name]._parse_transcript("\n".join(lines) + "\n")


@pytest.mark.parametrize("name", MODULES)
@pytest.mark.parametrize("field", ["program_sha256", "transcript_sha256", "transcript"])
def test_inconsistent_execution_identities_fail_the_certificate(profiles, monkeypatch, name, field):
    computation = copy.deepcopy(profiles[name]["computation"])
    computation[field] = computation[field] + "\n" if field == "transcript" else changed(computation[field])
    result = recompute(profiles, monkeypatch, name, computation=computation)
    assert result["certificate_verified"] is False
    assert result["decision"]["status"] == "PROFILE_INVALID"


@pytest.mark.parametrize("name", MODULES)
def test_receipts_do_not_claim_nonexistent_hard_memory_limits(profiles, name):
    limits = profiles[name]["execution_limits"]
    assert limits["primary_wall_seconds"] == TIMEOUTS[name]
    assert limits["primary_hard_address_space_bytes"] is None
    for field in ("python_checks", "python_witness_replay"):
        if field in limits:
            assert limits[field] == "in_process_without_a_separate_hard_memory_or_wall_limit"


@pytest.mark.parametrize("name", MODULES)
@pytest.mark.parametrize("kind", ["profile", "report"])
def test_existing_production_assertions_with_explicit_saved_runtime(profiles, monkeypatch, tmp_path, name, kind):
    import test_local_science_tools as tests

    factory_name = TOOLS[name] + "_profile"
    monkeypatch.setattr(tests, factory_name, lambda: copy.deepcopy(profiles[name]))
    monkeypatch.setattr(project_tools, factory_name, lambda: copy.deepcopy(profiles[name]))
    test = getattr(tests, "test_" + TOOLS[name] + "_" + kind)
    test(tmp_path) if kind == "report" else test()


WITNESS_CASES = [(name, path) for name in ("nonconsecutive", "consecutive") for path in leaf_paths({
    "point": [0] * 25, "nonzero_form_count": 0, "jacobian_rank": 14, "projective_tangent_dimension_lower_bound": 10,
})]


@pytest.mark.parametrize("name,path", WITNESS_CASES)
def test_every_independent_witness_claim_is_retained_in_reviewed_scope(profiles, monkeypatch, name, path):
    witness = copy.deepcopy(profiles[name]["first_witness_replay"])
    container = witness
    for key in path[:-1]:
        container = container[key]
    container[path[-1]] = changed(container[path[-1]])
    result = recompute(profiles, monkeypatch, name, witness=witness)
    assert result["evidence_sha256"] != expected(name)
    if path[0] in ("nonzero_form_count", "jacobian_rank"):
        assert result["certificate_verified"] is False
        assert result["decision"]["status"] == "PROFILE_INVALID"
    with pytest.raises(ScientificToolError, match="report not written"):
        certified_receipt_json(result)


@pytest.mark.parametrize("name", MODULES)
@pytest.mark.parametrize("field", ["protocol_version", "unreviewed_provenance"])
def test_undeclared_runtime_fields_are_not_silently_excluded(profiles, name, field):
    profile = copy.deepcopy(profiles[name])
    profile["computation"][field] = 999
    profile["evidence_sha256"] = profile_evidence_sha256(profile)
    assert profile["evidence_sha256"] != expected(name)
    with pytest.raises(ScientificToolError, match="report not written"):
        certified_receipt_json(profile)


@pytest.mark.parametrize("name", ["two_block", "nonconsecutive", "consecutive"])
@pytest.mark.parametrize("mutation", ["missing_receipt", "missing_evidence", "invalid_evidence", "changed_evidence", "unknown"])
def test_prerequisite_links_are_explicit_and_strict(profiles, name, mutation):
    profile = copy.deepcopy(profiles[name])
    container = profile["pure_power_prerequisite"] if name == "two_block" else profile
    receipt_key = "receipt_sha256" if name == "two_block" else "pure_power_prerequisite_receipt_sha256"
    evidence_key = "evidence_sha256" if name == "two_block" else "pure_power_prerequisite_evidence_sha256"
    if mutation == "missing_receipt":
        del container[receipt_key]
    elif mutation == "missing_evidence":
        del container[evidence_key]
    elif mutation == "invalid_evidence":
        container[evidence_key] = "not-a-digest"
    elif mutation == "changed_evidence":
        container[evidence_key] = changed(container[evidence_key])
    else:
        container["unreviewed"] = True
    if mutation in ("missing_receipt", "missing_evidence", "invalid_evidence"):
        with pytest.raises(ScientificToolError):
            profile_evidence_sha256(profile)
    else:
        profile["evidence_sha256"] = profile_evidence_sha256(profile)
        assert profile["evidence_sha256"] != expected(name)
        with pytest.raises(ScientificToolError, match="report not written"):
            certified_receipt_json(profile)


@pytest.mark.parametrize("name", MODULES)
@pytest.mark.parametrize("field", ["compiler", "compiler_sha256", "compiler_version", "binary_sha256", "transcript"])
def test_missing_raw_provenance_is_not_a_valid_evidence_projection(profiles, name, field):
    profile = copy.deepcopy(profiles[name])
    del profile["computation"][field]
    with pytest.raises(ScientificToolError, match="incomplete"):
        profile_evidence_sha256(profile)


@pytest.mark.parametrize("name", MODULES)
def test_refused_report_preserves_existing_artifact(profiles, monkeypatch, tmp_path, name):
    from test_local_science_tools import _registry

    registry = _registry(tmp_path)
    monkeypatch.setattr(project_tools, TOOLS[name] + "_profile", lambda: copy.deepcopy(profiles[name]))
    request = {"path": "research/existing.md", "terminal_marker": "END"}
    registry.invoke(TOOLS[name] + "_report", request)
    artifact = tmp_path / "artifacts/research/existing.md"
    original = artifact.read_bytes()
    failed = copy.deepcopy(profiles[name])
    failed["computation"]["transcript"] += "\n"
    assert profile_evidence_sha256(failed) == expected(name)
    monkeypatch.setattr(project_tools, TOOLS[name] + "_profile", lambda: failed)
    with pytest.raises(ScientificToolError, match="report not written"):
        registry.invoke(TOOLS[name] + "_report", {**request, "expected_sha256": sha(original.decode())})
    assert artifact.read_bytes() == original


@pytest.mark.parametrize("name", MODULES)
def test_native_wrapper_keeps_exact_bytes_and_existing_limits_with_explicit_process_fixture(monkeypatch, name):
    module = MODULES[name]
    runtime_module = module if name in ("alphabet", "two_block") else nonconsecutive
    calls = []
    wrapped = []

    def wrap(command, **kwargs):
        assert kwargs["network"] is False
        assert kwargs["rw"] == [kwargs["chdir"]]
        wrapped.append(command)
        return command

    def run(command, **kwargs):
        calls.append((command, kwargs))
        stdout = ""
        if "--version" in command:
            stdout = "TEST-ONLY-PROCESS-FIXTURE\n"
        elif "-O3" not in command:
            stdout = native_output(name)
        return subprocess.CompletedProcess(command, 0, stdout, "")

    monkeypatch.setattr(runtime_module.shutil, "which", lambda value: "/test-only-process-fixture/g++")
    monkeypatch.setattr(runtime_module, "_sha256_file", lambda path: "0" * 64)
    monkeypatch.setattr(runtime_module.sandbox, "wrap", wrap)
    monkeypatch.setattr(runtime_module.subprocess, "run", run)
    receipt = module._run_program()
    assert receipt["transcript"] == native_output(name)
    assert receipt["transcript_sha256"] == sha(native_output(name))
    assert calls[0][1]["input"] == PROGRAMS[name]
    assert [kwargs["timeout"] for _, kwargs in calls] == [TIMEOUTS[name], TIMEOUTS[name], 5]
    assert len(wrapped) == 2
    assert len(wrapped[1]) == 1
    assert "runtime_address_space_limit_bytes" not in receipt


@pytest.mark.parametrize("test_name", [
    "test_post_s25_diagonal_profile_rejects_both_invalid_surface_families",
    "test_level25_diagonal_2_5_filter_exhausts_quadric_ranks",
    "test_level25_heisenberg_balanced_two_pivot_obstruction_is_exact",
    "test_level25_heisenberg_balanced_two_pivot_obstruction_report_is_canonical",
])
def test_deterministic_python_profile_links_and_complete_receipts_with_real_execution(tmp_path, test_name):
    import test_local_science_tools as tests

    test = getattr(tests, test_name)
    test() if test_name.endswith("is_exact") else test(tmp_path)
