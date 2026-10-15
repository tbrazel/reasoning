"""Exact saved mathematical output is not production sandbox acceptance."""

from __future__ import annotations

import copy
import json
import shutil
import subprocess

import pytest
from test_science_evidence import FIXTURES, sha

from reasoning_ledger.agents import heisenberg_level25_singleton_orbits as singleton
from reasoning_ledger.agents import heisenberg_level25_three_block_coverage as coverage
from reasoning_ledger.agents import project_tools
from reasoning_ledger.agents.science_evidence import certified_receipt_json, profile_evidence_sha256, reviewed_profile_matches
from reasoning_ledger.agents.scientific import ScientificToolError
from reasoning_ledger.canonical import canonical_json

FACTORIES = {
    "singleton": singleton.level25_heisenberg_fixed_stratum_singleton_orbit_profile,
    "coverage": coverage.level25_heisenberg_singleton_orbit_three_block_coverage_profile,
    "local": coverage.level25_heisenberg_augmented_fixed_and_cyclic_local_profile,
    "twelve_row": coverage.level25_heisenberg_augmented_three_block_rational_profile,
}
TOOLS = {
    "singleton": "level25_heisenberg_fixed_stratum_singleton_orbit",
    "coverage": "level25_heisenberg_singleton_orbit_three_block_coverage",
    "local": "level25_heisenberg_augmented_fixed_and_cyclic_local",
    "twelve_row": "level25_heisenberg_augmented_three_block_rational",
}
PROGRAMS = {"singleton": singleton._PROGRAM, "coverage": coverage._PROGRAM, "twelve_row": coverage._TWELVE_ROW_PROGRAM}
PARSERS = {
    "singleton": singleton._parse_transcript,
    "coverage": coverage._parse_transcript,
    "twelve_row": coverage._parse_twelve_row_transcript,
}
TIMEOUTS = {"singleton": 60, "coverage": 300, "twelve_row": 60}


def native_output(name):
    return (FIXTURES / ("singleton_coverage_" + name + "_v1.txt")).read_text(encoding="utf-8")


def python_fixture(name):
    return json.loads((FIXTURES / ("singleton_coverage_" + name + "_python_v1.json")).read_text(encoding="utf-8"))


def install_saved_coverage(patch):
    programs = {program: name for name, program in PROGRAMS.items()}

    def saved_run(program, **kwargs):
        name = programs[program]
        assert kwargs["preserve_exact_transcript"] is True
        assert kwargs.get("timeout_seconds", 60) == TIMEOUTS[name]
        assert "runtime_address_space_bytes" not in kwargs
        stdout = native_output(name)
        return {
            "program_sha256": sha(program),
            "transcript_sha256": sha(stdout),
            "transcript": stdout,
            "protocol_version": 1,
            "compiler": "TEST-ONLY-RUNTIME-FIXTURE",
            "compiler_sha256": "0" * 64,
            "compiler_version": "TEST-ONLY-RUNTIME-FIXTURE",
            "binary_sha256": "0" * 64,
        }

    patch.setattr(singleton, "_run_source_hashed_program", saved_run)
    patch.setattr(coverage, "_run_source_hashed_program", saved_run)
    patch.setattr(singleton, "_python_census", lambda: python_fixture("singleton"))
    patch.setattr(coverage, "_replay_selected_on_first_witnesses", lambda computation: python_fixture("coverage"))
    patch.setattr(
        coverage,
        "_python_twelve_row_census",
        lambda support: python_fixture("twelve_row")["nonconsecutive" if support == (0, 1, 3) else "consecutive"],
    )


def expected(name):
    return (
        singleton._SINGLETON_EVIDENCE_SHA256
        if name == "singleton"
        else getattr(
            coverage,
            {
                "coverage": "_COVERAGE_EVIDENCE_SHA256",
                "local": "_LOCAL_EVIDENCE_SHA256",
                "twelve_row": "_THREE_BLOCK_EVIDENCE_SHA256",
            }[name],
        )
    )


@pytest.fixture(scope="module")
def profiles():
    with pytest.MonkeyPatch.context() as patch:
        install_saved_coverage(patch)
        return {name: factory() for name, factory in FACTORIES.items()}


@pytest.mark.parametrize("name", FACTORIES)
def test_saved_output_and_real_local_replays_have_reviewed_scope(profiles, name):
    profile = profiles[name]
    assert profile["schema"].endswith(".v2")
    assert profile["certificate_verified"] is True
    assert profile["evidence_sha256"] == expected(name) == profile_evidence_sha256(profile)
    assert reviewed_profile_matches(profile, expected(name))
    assert certified_receipt_json(profile) == canonical_json(profile)
    if name in PROGRAMS:
        assert profile["computation"]["transcript"] == native_output(name)
        assert profile["computation"]["transcript_sha256"] == sha(native_output(name))


@pytest.mark.parametrize("name", PROGRAMS)
def test_genuine_compiled_mathematics_reproduces_entire_reviewed_transcript(tmp_path, name):
    compiler = shutil.which("g++")
    if compiler is None:
        pytest.skip("g++ unavailable for trusted-source mathematical diagnostic")
    binary = tmp_path / "diagnostic"
    subprocess.run(
        [compiler, "-O3", "-std=c++20", "-x", "c++", "-", "-o", str(binary)],
        input=PROGRAMS[name],
        text=True,
        capture_output=True,
        check=True,
        timeout=TIMEOUTS[name],
    )
    result = subprocess.run([str(binary)], text=True, capture_output=True, check=True, timeout=TIMEOUTS[name])
    assert not result.stderr
    assert result.stdout == native_output(name)


def test_genuine_python_singleton_census_matches_entire_reviewed_domain():
    assert singleton._python_census() == python_fixture("singleton")


def test_genuine_independent_first_witness_replay_matches_reviewed_values_and_ranks():
    assert coverage._replay_selected_on_first_witnesses(PARSERS["coverage"](native_output("coverage"))) == python_fixture("coverage")


@pytest.mark.parametrize("kind,support", [("nonconsecutive", (0, 1, 3)), ("consecutive", (0, 1, 2))])
def test_genuine_twelve_row_python_census_matches_independent_classification(kind, support):
    assert coverage._python_twelve_row_census(support) == python_fixture("twelve_row")[kind]


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


def recompute(profiles, patch, name, computation=None, parent=None, independent=None, cyclic=None):
    if name == "singleton":
        patch.setattr(
            singleton, "_run_program", lambda: copy.deepcopy(profiles[name]["computation"] if computation is None else computation)
        )
        patch.setattr(
            singleton,
            "_python_census",
            lambda: copy.deepcopy(profiles[name]["independent_python_census"] if independent is None else independent),
        )
    else:
        key = "singleton" if name == "coverage" else "coverage"
        patch.setattr(coverage, TOOLS[key] + "_profile", lambda: copy.deepcopy(profiles[key] if parent is None else parent))
        if name == "coverage":
            patch.setattr(
                coverage, "_run_program", lambda: copy.deepcopy(profiles[name]["computation"] if computation is None else computation)
            )
            patch.setattr(
                coverage,
                "_replay_selected_on_first_witnesses",
                lambda comp: copy.deepcopy(profiles[name]["first_witness_replays"] if independent is None else independent),
            )
        elif name == "twelve_row":
            patch.setattr(
                coverage,
                "_run_twelve_row_program",
                lambda: copy.deepcopy(profiles[name]["computation"] if computation is None else computation),
            )
            patch.setattr(
                coverage,
                "_python_twelve_row_census",
                lambda support: copy.deepcopy(
                    (profiles[name]["independent_python_censuses"] if independent is None else independent)[
                        "nonconsecutive" if support == (0, 1, 3) else "consecutive"
                    ]
                ),
            )
        elif name == "local":
            patch.setattr(
                coverage,
                "_augmented_cyclic_eigenpoint_census",
                lambda: copy.deepcopy(profiles[name]["cyclic_eigenpoint_replay"] if cyclic is None else cyclic),
            )
    return FACTORIES[name]()


PARSED_CASES = [(name, field) for name in PROGRAMS for field in PARSERS[name](native_output(name))]


@pytest.mark.parametrize("name,field", PARSED_CASES)
def test_every_cached_native_field_is_bound_to_reviewed_raw_output(profiles, monkeypatch, name, field):
    computation = copy.deepcopy(profiles[name]["computation"])
    computation[field] = changed(computation[field])
    result = recompute(profiles, monkeypatch, name, computation=computation)
    assert result["certificate_verified"] is False
    assert result["decision"]["status"] == "PROFILE_INVALID"
    if name == "singleton":
        assert result["representative_candidates"] == []


@pytest.mark.parametrize("name,field", PARSED_CASES)
def test_duplicate_native_fields_are_refused(name, field):
    stdout = native_output(name)
    line = next(line for line in stdout.splitlines() if line.startswith(field + "="))
    with pytest.raises(ScientificToolError, match="duplicate"):
        PARSERS[name](stdout.replace("REASONING_END\n", line + "\nREASONING_END\n"))


@pytest.mark.parametrize("name,field", PARSED_CASES)
def test_changed_rehashed_native_bytes_do_not_approve_new_mathematics(profiles, monkeypatch, name, field):
    import re

    computation = copy.deepcopy(profiles[name]["computation"])
    stdout = computation["transcript"]
    line = next(line for line in stdout.splitlines() if line.startswith(field + "="))
    values = line.split("=", 1)[1]
    altered = re.sub(r"\d+", lambda match: str(int(match.group()) + 1), values, count=1)
    stdout = stdout.replace(line + "\n", field + "=" + altered + "\n")
    computation.update(PARSERS[name](stdout), transcript=stdout, transcript_sha256=sha(stdout))
    result = recompute(profiles, monkeypatch, name, computation=computation)
    assert result["certificate_verified"] is False
    assert result["decision"]["status"] == "PROFILE_INVALID"
    assert result["evidence_sha256"] != expected(name)


@pytest.mark.parametrize("name", ["coverage", "local", "twelve_row"])
@pytest.mark.parametrize("mutation", ["certificate", "copied_seal", "rehashed_scope", "runtime_only"])
def test_fresh_prerequisite_scope_not_raw_runtime_identity_controls_approval(profiles, monkeypatch, name, mutation):
    parent = copy.deepcopy(profiles["singleton" if name == "coverage" else "coverage"])
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
        key = "fixed_stratum_screen" if name == "coverage" else "coverage"
        assert result[key + "_receipt_sha256"] != profiles[name][key + "_receipt_sha256"]
    else:
        assert result["decision"]["status"] == "PROFILE_INVALID"


@pytest.mark.parametrize("name", ["coverage", "local", "twelve_row"])
def test_every_invocation_obtains_a_new_prerequisite(profiles, monkeypatch, name):
    calls = []
    parent_name = "singleton" if name == "coverage" else "coverage"
    recompute(profiles, monkeypatch, name)

    def fresh():
        calls.append(True)
        return copy.deepcopy(profiles[parent_name])

    monkeypatch.setattr(coverage, TOOLS[parent_name] + "_profile", fresh)
    assert FACTORIES[name]()["certificate_verified"] is True
    assert FACTORIES[name]()["certificate_verified"] is True
    assert len(calls) == 2


INDEPENDENT_CASES = (
    [("singleton", field) for field in python_fixture("singleton")]
    + [("coverage", field) for field in python_fixture("coverage")[0]]
    + [("twelve_row", field) for field in python_fixture("twelve_row")["nonconsecutive"]]
)


@pytest.mark.parametrize("name,field", INDEPENDENT_CASES)
def test_independent_mathematical_claims_are_checked(profiles, monkeypatch, name, field):
    key = {"singleton": "independent_python_census", "coverage": "first_witness_replays", "twelve_row": "independent_python_censuses"}[name]
    independent = copy.deepcopy(profiles[name][key])
    container = independent if name == "singleton" else independent[0] if name == "coverage" else independent["nonconsecutive"]
    container[field] = changed(container[field])
    result = recompute(profiles, monkeypatch, name, independent=independent)
    # Labels and individual witness values are bound by complete reviewed scope,
    # while count/rank and census disagreement additionally fail the certificate.
    assert result["evidence_sha256"] != expected(name)
    with pytest.raises(ScientificToolError, match="report not written"):
        certified_receipt_json(result)


@pytest.mark.parametrize("name", PROGRAMS)
@pytest.mark.parametrize("field", ["compiler", "compiler_sha256", "compiler_version", "binary_sha256"])
def test_execution_provenance_varies_without_changing_reviewed_mathematics(profiles, monkeypatch, name, field):
    computation = copy.deepcopy(profiles[name]["computation"])
    computation[field] = changed(computation[field])
    result = recompute(profiles, monkeypatch, name, computation=computation)
    assert result["certificate_verified"] is True
    assert result["evidence_sha256"] == expected(name)
    assert result["computation"][field] == computation[field]
    assert sha(canonical_json(result)) != sha(canonical_json(profiles[name]))


@pytest.mark.parametrize("name", FACTORIES)
@pytest.mark.parametrize("mutation", ["certificate", "scope", "unknown", "request", "decision"])
def test_failed_or_self_rehashed_scopes_cannot_write_a_certified_report(profiles, monkeypatch, tmp_path, name, mutation):
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
    else:
        profile["unreviewed"] = True
    profile["evidence_sha256"] = profile_evidence_sha256(profile)
    monkeypatch.setattr(project_tools, TOOLS[name] + "_profile", lambda: profile)
    with pytest.raises(ScientificToolError, match="report not written"):
        _registry(tmp_path).invoke(TOOLS[name] + "_report", {"path": "research/refused.md", "terminal_marker": "END"})
    assert not (tmp_path / "artifacts/research/refused.md").exists()


@pytest.mark.parametrize("name", PROGRAMS)
@pytest.mark.parametrize("rehashed", [False, True])
def test_changed_duplicate_transcript_body_cannot_be_published_under_copied_approval(profiles, name, rehashed):
    profile = copy.deepcopy(profiles[name])
    profile["computation"]["transcript"] += "\n"
    if rehashed:
        profile["computation"]["transcript_sha256"] = sha(profile["computation"]["transcript"])
    profile["evidence_sha256"] = profile_evidence_sha256(profile)
    with pytest.raises(ScientificToolError, match="report not written"):
        certified_receipt_json(profile)


@pytest.mark.parametrize("name", FACTORIES)
def test_valid_reports_render_both_identities_and_full_scope(profiles, monkeypatch, tmp_path, name):
    from test_local_science_tools import _registry

    monkeypatch.setattr(project_tools, TOOLS[name] + "_profile", lambda: copy.deepcopy(profiles[name]))
    result = _registry(tmp_path).invoke(TOOLS[name] + "_report", {"path": "research/approved.md", "terminal_marker": "COVERAGE_END"})
    text = (tmp_path / "artifacts/research/approved.md").read_text(encoding="utf-8")
    assert result["certificate_verified"] is True
    assert result["evidence_sha256"] == expected(name)
    assert result["receipt_sha256"] == sha(canonical_json(profiles[name]))
    assert result["evidence_sha256"] in text and result["receipt_sha256"] in text
    assert json.loads(text.split("```json\n", 1)[1].split("\n```", 1)[0]) == profiles[name]
    assert text.endswith("COVERAGE_END\n")


@pytest.mark.parametrize("name", PROGRAMS)
@pytest.mark.parametrize("mutation", ["frame", "end", "noninteger", "unknown", "missing", "no_equals"])
def test_malformed_native_output_is_refused(name, mutation):
    lines = native_output(name).splitlines()
    if mutation == "frame":
        lines[0] = "UNREVIEWED_FRAME"
    elif mutation == "end":
        lines[-1] = "UNREVIEWED_END"
    elif mutation == "noninteger":
        lines[1] = lines[1].split("=", 1)[0] + "=invalid"
    elif mutation == "unknown":
        lines.insert(-1, "unreviewed=1")
    elif mutation == "missing":
        del lines[1]
    else:
        lines[1] = "invalid"
    with pytest.raises(ScientificToolError):
        PARSERS[name]("\n".join(lines) + "\n")


@pytest.mark.parametrize("name", PROGRAMS)
@pytest.mark.parametrize("field", ["program_sha256", "transcript_sha256", "transcript"])
def test_native_identity_metadata_cannot_be_copied_inconsistently(profiles, monkeypatch, name, field):
    computation = copy.deepcopy(profiles[name]["computation"])
    computation[field] = computation[field] + "\n" if field == "transcript" else changed(computation[field])
    result = recompute(profiles, monkeypatch, name, computation=computation)
    assert result["certificate_verified"] is False
    assert result["decision"]["status"] == "PROFILE_INVALID"


@pytest.mark.parametrize("name", FACTORIES)
def test_no_receipt_claims_a_nonexistent_hard_memory_limit(profiles, name):
    limits = profiles[name]["execution_limits"]
    if name == "local":
        assert limits["mode"] == "in_process_without_a_separate_hard_memory_or_wall_limit"
        assert "computation" not in profiles[name]
    else:
        assert limits["primary_wall_seconds"] == TIMEOUTS[name]
        assert limits["primary_hard_address_space_bytes"] is None


@pytest.mark.parametrize("name", FACTORIES)
@pytest.mark.parametrize("kind", ["profile", "report"])
def test_existing_production_assertions_with_explicit_saved_runtime(profiles, monkeypatch, tmp_path, name, kind):
    import test_local_science_tools as tests

    factory_name = TOOLS[name] + "_profile"
    monkeypatch.setattr(tests, factory_name, lambda: copy.deepcopy(profiles[name]))
    monkeypatch.setattr(project_tools, factory_name, lambda: copy.deepcopy(profiles[name]))
    test = getattr(tests, "test_" + TOOLS[name] + "_" + kind)
    test(tmp_path) if kind == "report" else test()


CYCLIC_CASES = [
    ("aggregate", field)
    for field in (
        "lift_count", "eigenpoint_count", "invalid_eigenpoint_count", "common_zero_membership_change_count",
        "augmented_common_zero_count", "augmented_rank_22_count",
    )
] + [
    ("lift", field)
    for field in (
        "subgroup_label", "lift_index", "generator", "eigenpoint_count", "augmented_common_zero_count",
        "augmented_common_zero_rank_distribution",
    )
] + [("inventory", kind) for kind in ("missing", "duplicate", "reordered")]


@pytest.mark.parametrize("kind,field", CYCLIC_CASES)
def test_local_cyclic_replay_binds_complete_inventory_counts_and_ranks(profiles, monkeypatch, kind, field):
    cyclic = copy.deepcopy(profiles["local"]["cyclic_eigenpoint_replay"])
    if kind == "aggregate":
        cyclic[field] = changed(cyclic[field])
    elif kind == "lift":
        cyclic["lifts"][0][field] = changed(cyclic["lifts"][0][field])
    elif field == "missing":
        cyclic["lifts"].pop()
    elif field == "duplicate":
        cyclic["lifts"].append(copy.deepcopy(cyclic["lifts"][0]))
    else:
        cyclic["lifts"].reverse()
    result = recompute(profiles, monkeypatch, "local", cyclic=cyclic)
    assert result["certificate_verified"] is False
    assert result["decision"]["status"] == "PROFILE_INVALID"
    with pytest.raises(ScientificToolError, match="report not written"):
        certified_receipt_json(result)


def test_all_sixty_selected_fixed_stratum_substitutions_are_executed_and_checked(profiles, monkeypatch):
    recompute(profiles, monkeypatch, "local")
    calls = []

    def nonzero_restriction(*args):
        calls.append(args)
        return {(25, 0, 0, 0, 0): 1}

    monkeypatch.setattr(coverage, "_restricted_orbit", nonzero_restriction)
    result = FACTORIES["local"]()
    assert len(calls) == 60
    assert len({canonical_json(call[-1]) for call in calls}) == 30
    assert [row["nonzero_fixed_restriction_count"] for row in result["selected_fixed_restriction_replay"]] == [30, 30]
    assert result["certificate_verified"] is False
    assert result["decision"]["status"] == "PROFILE_INVALID"


@pytest.mark.parametrize("field", ["selected_augmentation", "selected_fixed_restriction_replay", "cyclic_eigenpoint_replay"])
def test_python_only_mathematical_claims_are_not_discarded_by_projection(profiles, field):
    profile = copy.deepcopy(profiles["local"])
    profile[field] = changed(profile[field])
    profile["evidence_sha256"] = profile_evidence_sha256(profile)
    assert profile["evidence_sha256"] != expected("local")
    with pytest.raises(ScientificToolError, match="report not written"):
        certified_receipt_json(profile)


@pytest.mark.parametrize("name", ["coverage", "local", "twelve_row"])
@pytest.mark.parametrize("mutation", ["missing_evidence", "missing_receipt", "invalid_evidence", "unknown"])
def test_prerequisite_links_cannot_be_omitted_or_silently_ignored(profiles, name, mutation):
    profile = copy.deepcopy(profiles[name])
    prefix = "fixed_stratum_screen" if name == "coverage" else "coverage"
    if mutation == "missing_evidence":
        del profile[prefix + "_evidence_sha256"]
    elif mutation == "missing_receipt":
        del profile[prefix + "_receipt_sha256"]
    elif mutation == "invalid_evidence":
        profile[prefix + "_evidence_sha256"] = "unreviewed"
    else:
        profile[prefix + "_unreviewed"] = True
    if mutation == "unknown":
        profile["evidence_sha256"] = profile_evidence_sha256(profile)
        assert not reviewed_profile_matches(profile, expected(name))
    else:
        with pytest.raises(ScientificToolError):
            profile_evidence_sha256(profile)
    with pytest.raises(ScientificToolError):
        certified_receipt_json(profile)
