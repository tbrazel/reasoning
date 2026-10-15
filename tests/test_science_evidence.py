"""Real mathematical replays with saved output, NOT runtime/sandbox acceptance."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from reasoning_ledger.agents import heisenberg_level25_four_block as unary
from reasoning_ledger.agents import heisenberg_level25_four_block_ad5 as ad5
from reasoning_ledger.agents import heisenberg_level25_four_block_ad5_batch as batch
from reasoning_ledger.agents import heisenberg_level25_three_block as runner
from reasoning_ledger.agents import heisenberg_level25_three_block_coverage as coverage
from reasoning_ledger.agents import project_tools
from reasoning_ledger.agents.science_evidence import (
    profile_evidence_sha256,
    reviewed_profile_matches,
    seal_profile_evidence,
)
from reasoning_ledger.agents.scientific import ScientificToolError
from reasoning_ledger.canonical import canonical_json

FIXTURES = Path(__file__).with_name("fixtures") / "science"
EXPECTED = {
    "alphabet": "bf392cc8d623d78ed3eef029132335fa1d974cbfa42c1294bedd2e899aade9b2",
    "phase": "ddff1f79ec58bd2a093038a70d01483341988cc8d5c829e6fd8939fd6fecc95c",
    "fourier": "8da803c2ceffb48c1cf5590e9c6334ed37d2760383231ee71b7720db99c46317",
    "ad5": "f425e93832a5b366a0c8e6e4b1618c184a9ec8b1f6aa874e98a9b0d87326bcb8",
    "ad5_batch": "49835bb6dc187a1cabfae48e4a9aa2c7afbe3f9334799d518e5a94a7a0f2d873",
    "unary": "f39b6bf089f400ef9677502a4b5caee5173071136795abb0c113ef747bf0f542",
}
FACTORIES = {
    "alphabet": coverage.level25_heisenberg_augmented_four_block_alphabet_profile,
    "phase": coverage.level25_heisenberg_augmented_four_block_phase_quotient_profile,
    "fourier": coverage.level25_heisenberg_augmented_four_block_fourier_profile,
    "ad5": ad5.level25_heisenberg_augmented_four_block_bounded_ad5_profile,
    "ad5_batch": batch.level25_heisenberg_augmented_four_block_bounded_ad5_context_batch_profile,
    "unary": unary.level25_heisenberg_augmented_four_block_unary_convolution_profile,
}


def sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def saved_output(name: str) -> str:
    return (FIXTURES / (name + "_v1.txt")).read_text(encoding="utf-8")


def replay_fixture() -> dict:
    transcript = (FIXTURES / "ad5_replay_v1.json").read_text(encoding="utf-8")
    program = Path(ad5.__file__).with_name("heisenberg_level25_four_block_ad5_replay.py").read_bytes()
    return {
        **json.loads(transcript),
        "transcript": transcript,
        "transcript_sha256": sha(transcript),
        "program_sha256": hashlib.sha256(program).hexdigest(),
        "runtime_address_space_limit_bytes": 67108864,
        "interpreter": "TEST-ONLY-RUNTIME-FIXTURE",
    }


def batch_domain_fixture() -> dict:
    transcript = (FIXTURES / "ad5_batch_domain_v1.json").read_text(encoding="utf-8")
    program = Path(batch.__file__).with_name("heisenberg_level25_four_block_ad5_batch_replay.py").read_bytes()
    return {
        **json.loads(transcript),
        "transcript": transcript,
        "transcript_sha256": sha(transcript),
        "program_sha256": hashlib.sha256(program).hexdigest(),
        "runtime_address_space_limit_bytes": 67108864,
        "interpreter": "TEST-ONLY-RUNTIME-FIXTURE",
    }


def unary_python_fixture() -> dict:
    return json.loads((FIXTURES / "unary_python_v1.json").read_text(encoding="utf-8"))


def install_saved_runner(patch):
    programs = {
        coverage._FOUR_BLOCK_PROGRAM: "alphabet",
        coverage._FOUR_BLOCK_PHASE_PROGRAM: "phase",
        coverage._FOUR_BLOCK_FOURIER_PROGRAM: "fourier",
        ad5._PROGRAM: "ad5",
        batch._PROGRAM: "ad5_batch",
        unary._PROGRAM: "unary",
    }

    def saved_run(program, **kwargs):
        assert kwargs["preserve_exact_transcript"] is True
        if program in (ad5._PROGRAM, batch._PROGRAM):
            assert kwargs["runtime_address_space_bytes"] == 67108864
        stdout = saved_output(programs[program])
        return {
            "program_sha256": sha(program),
            "transcript_sha256": sha(stdout),
            "transcript": stdout,
            "protocol_version": 1,
            "compiler": "TEST-ONLY-RUNTIME-FIXTURE",
            "compiler_sha256": "0" * 64,
            "compiler_version": "TEST-ONLY-RUNTIME-FIXTURE",
            "binary_sha256": "0" * 64,
            **({"runtime_address_space_limit_bytes": 67108864} if program in (ad5._PROGRAM, batch._PROGRAM) else {}),
        }

    def saved_replay(witness):
        assert witness == []
        return replay_fixture()

    patch.setattr(coverage, "_run_source_hashed_program", saved_run)
    patch.setattr(ad5, "_run_source_hashed_program", saved_run)
    patch.setattr(ad5, "_run_independent_replay", saved_replay)
    patch.setattr(batch, "_run_source_hashed_program", saved_run)
    patch.setattr(batch, "_run_domain_replay", batch_domain_fixture)
    patch.setattr(batch, "_run_independent_replay", saved_replay)
    patch.setattr(unary, "_run_source_hashed_program", saved_run)
    patch.setattr(unary, "_python_replay", unary_python_fixture)


@pytest.fixture(scope="module")
def profiles():
    with pytest.MonkeyPatch.context() as patch:
        install_saved_runner(patch)
        return {name: factory() for name, factory in FACTORIES.items()}


@pytest.fixture
def first_profile(profiles, monkeypatch):
    def profile(computation=None, independent=None):
        monkeypatch.setattr(ad5, "_run_program", lambda: copy.deepcopy(computation or profiles["ad5"]["computation"]))
        monkeypatch.setattr(
            ad5, "_run_independent_replay", lambda witness: copy.deepcopy(independent or profiles["ad5"]["independent_replay"])
        )
        monkeypatch.setattr(ad5, "level25_heisenberg_augmented_four_block_fourier_profile", lambda: copy.deepcopy(profiles["fourier"]))
        monkeypatch.setattr(ad5, "level25_heisenberg_augmented_four_block_phase_quotient_profile", lambda: copy.deepcopy(profiles["phase"]))
        return FACTORIES["ad5"]()

    return profile


@pytest.mark.parametrize("name", FACTORIES)
def test_exact_saved_output_and_real_python_mathematics_reproduce_reviewed_evidence(profiles, name):
    result = profiles[name]
    assert result["schema"].endswith(".v2")
    assert result["certificate_verified"] is True
    assert result["evidence_sha256"] == EXPECTED[name] == profile_evidence_sha256(result)
    assert reviewed_profile_matches(result, EXPECTED[name])
    assert result["computation"]["transcript"] == saved_output(name)
    assert result["computation"]["transcript_sha256"] == sha(saved_output(name))


@pytest.mark.parametrize("memory", [1, 11308, 11312, 32000, 65536])
def test_memory_telemetry_changes_raw_receipt_not_certified_mathematics(profiles, first_profile, memory):
    computation = copy.deepcopy(profiles["ad5"]["computation"])
    stdout = computation["transcript"].replace("vm_peak_kib=11312\n", f"vm_peak_kib={memory}\n")
    computation.update(transcript=stdout, transcript_sha256=sha(stdout), vm_peak_kib=memory)
    computation["mathematical_transcript_sha256"] = ad5._mathematical_transcript_sha256(stdout)
    result = first_profile(computation)
    assert result["certificate_verified"] is True
    assert result["evidence_sha256"] == EXPECTED["ad5"]
    if memory != 11312:
        assert sha(canonical_json(result)) != sha(canonical_json(profiles["ad5"]))


@pytest.mark.parametrize("memory", [-1, 0, 65537, 1_000_000])
def test_omitted_memory_line_still_requires_positive_telemetry_under_hard_cap(profiles, first_profile, memory):
    computation = copy.deepcopy(profiles["ad5"]["computation"])
    stdout = computation["transcript"].replace("vm_peak_kib=11312\n", f"vm_peak_kib={memory}\n")
    computation.update(transcript=stdout, transcript_sha256=sha(stdout), vm_peak_kib=memory)
    computation["mathematical_transcript_sha256"] = ad5._mathematical_transcript_sha256(stdout)
    result = first_profile(computation)
    assert result["certificate"]["program_and_mathematical_transcript_match_reviewed_execution"] is True
    assert result["certificate"]["resident_telemetry_respects_the_address_space_cap"] is False
    assert result["certificate_verified"] is False
    assert result["evidence_sha256"] != EXPECTED["ad5"]


_MATH_LINES = [line for line in saved_output("ad5").splitlines()[1:-1] if not line.startswith("vm_peak_kib=")]


@pytest.mark.parametrize("line", _MATH_LINES, ids=lambda line: line.split("=")[0])
def test_every_other_output_field_is_bound_by_mathematical_transcript(line):
    name, value = line.split("=", 1)
    if name == "status":
        changed = "UNREVIEWED_RESULT"
    elif name.startswith("witness_") and name != "witness_rank":
        count = {"witness_coordinates": 25, "witness_equation_values": 22, "witness_jacobian": 550}[name]
        changed = ",".join(["1"] * count)
    elif "," in value:
        values = value.split(",")
        values[0] = str(int(values[0]) + 1)
        changed = ",".join(values)
    else:
        changed = str(int(value) + 1)
    stdout = saved_output("ad5").replace(line + "\n", name + "=" + changed + "\n")
    assert ad5._mathematical_transcript_sha256(stdout) != ad5._REVIEWED_MATHEMATICAL_TRANSCRIPT_SHA256


@pytest.mark.parametrize(
    "change",
    [
        lambda s: s.rstrip(),
        lambda s: s.replace("\n", "\r\n"),
        lambda s: s.replace("vm_peak_kib=11312\n", ""),
        lambda s: s.replace("vm_peak_kib=11312\n", "vm_peak_kib=11312\nvm_peak_kib=11312\n"),
        lambda s: s.replace("vm_peak_kib=11312", "vm_peak_kib=011312"),
        lambda s: s.replace("vm_peak_kib=11312", "vm_peak_kib= 11312"),
        lambda s: s.replace("vm_peak_kib=11312", "vm_peak_kib=unknown"),
        lambda s: s.replace("REASONING_END", "OTHER_END"),
        lambda s: s.replace("witness_rank=-1", "unknown=0\nwitness_rank=-1"),
    ],
)
def test_normalization_cannot_hide_incomplete_or_ambiguous_framing(change):
    with pytest.raises(ScientificToolError):
        ad5._mathematical_transcript_sha256(change(saved_output("ad5")))


@pytest.mark.parametrize("field", ["program_sha256", "transcript_sha256", "mathematical_transcript_sha256", "search_charge_total"])
def test_copied_or_inconsistent_primary_identity_never_verifies(profiles, first_profile, field):
    computation = copy.deepcopy(profiles["ad5"]["computation"])
    computation[field] = "0" * 64 if field.endswith("sha256") else 1
    result = first_profile(computation)
    assert result["certificate_verified"] is False


@pytest.mark.parametrize("field", ["program_sha256", "transcript_sha256", "charged_operations", "histogram_checksum"])
def test_independent_replay_is_bound_to_source_raw_bytes_and_parsed_evidence(profiles, first_profile, field):
    replay = copy.deepcopy(profiles["ad5"]["independent_replay"])
    replay[field] = "0" * 64 if field.endswith("sha256") else 1
    assert first_profile(independent=replay)["certificate_verified"] is False


@pytest.mark.parametrize("name", FACTORIES)
@pytest.mark.parametrize("field", ["compiler", "compiler_version", "compiler_sha256", "binary_sha256"])
def test_execution_provenance_is_retained_but_not_a_platform_dependent_math_identity(profiles, name, field):
    result = copy.deepcopy(profiles[name])
    result["computation"][field] = "OTHER-EXPLICIT-RUNTIME-PROVENANCE"
    assert profile_evidence_sha256(result) == EXPECTED[name]
    assert sha(canonical_json(result)) != sha(canonical_json(profiles[name]))


@pytest.mark.parametrize("name", FACTORIES)
@pytest.mark.parametrize("field", ["request", "not_certified", "certificate", "computation", "unexpected_claim"])
def test_all_other_claims_scope_and_unknown_fields_change_evidence(profiles, name, field):
    result = copy.deepcopy(profiles[name])
    if field == "computation":
        result[field]["program_sha256"] = "0" * 64
    elif field == "certificate":
        result[field]["unreviewed_check"] = True
    else:
        result[field] = {"unreviewed": True}
    assert profile_evidence_sha256(result) != EXPECTED[name]
    assert reviewed_profile_matches(result, EXPECTED[name]) is False


@pytest.mark.parametrize("name", ["alphabet", "phase"])
def test_changed_prerequisite_with_copied_seal_is_not_accepted(profiles, first_profile, monkeypatch, name):
    changed = copy.deepcopy(profiles[name])
    changed["request"]["unreviewed"] = True
    first_profile()
    if name == "alphabet":
        monkeypatch.setattr(coverage, "level25_heisenberg_augmented_four_block_alphabet_profile", lambda: changed)
        with pytest.MonkeyPatch.context() as patch:
            install_saved_runner(patch)
            result = FACTORIES["fourier"]()
        check = "four_block_alphabet_reduction_is_the_certified_prerequisite"
    else:
        monkeypatch.setattr(ad5, "level25_heisenberg_augmented_four_block_phase_quotient_profile", lambda: changed)
        result = FACTORIES["ad5"]()
        check = "phase_quotient_is_the_certified_prerequisite"
    assert result["certificate"][check] is False
    assert result["certificate_verified"] is False


@pytest.mark.parametrize(
    "change",
    [
        lambda p: p.update(schema="unsupported.profile.v1"),
        lambda p: p["computation"].pop("compiler"),
        lambda p: p["computation"].pop("mathematical_transcript_sha256"),
        lambda p: p.pop("fourier_receipt_sha256"),
        lambda p: p.update(fourier_evidence_sha256="0" * 63),
        lambda p: p["independent_replay"].pop("transcript"),
    ],
)
def test_missing_or_unsupported_evidence_has_no_normalization_fallback(profiles, change):
    result = copy.deepcopy(profiles["ad5"])
    change(result)
    with pytest.raises(ScientificToolError):
        profile_evidence_sha256(result)


def test_sealing_an_existing_receipt_cannot_silently_replace_its_identity(profiles):
    with pytest.raises(ScientificToolError, match="already sealed"):
        seal_profile_evidence(profiles["ad5"])


def test_production_runner_preserves_raw_bytes_limits_and_network_isolation(monkeypatch):
    calls = []
    wraps = []
    stdout = saved_output("ad5")

    def wrap(command, **kwargs):
        wraps.append((command, kwargs))
        return ["TEST-ONLY-SANDBOX-BOUNDARY", *command]

    def run(command, **kwargs):
        calls.append((command, kwargs))
        if command[0] == "TEST-ONLY-SANDBOX-BOUNDARY" and "-o" in command:
            Path(command[-1]).write_bytes(b"TEST-ONLY-COMPILED-BINARY")
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, "TEST-ONLY-COMPILER\n", "")
        return subprocess.CompletedProcess(command, 0, stdout, "")

    monkeypatch.setattr(runner.sandbox, "wrap", wrap)
    monkeypatch.setattr(runner.subprocess, "run", run)
    result = ad5._run_program()
    assert len(wraps) == 2
    assert all(kwargs["network"] is False for _, kwargs in wraps)
    assert any("setrlimit" in argument for argument in wraps[1][0])
    assert "67108864" in wraps[1][0]
    assert calls[0][1]["input"] == ad5._PROGRAM
    assert result["transcript"] == stdout
    assert result["transcript_sha256"] == sha(stdout)
    assert result["mathematical_transcript_sha256"] == ad5._REVIEWED_MATHEMATICAL_TRANSCRIPT_SHA256


@pytest.mark.parametrize(
    "name,suffix",
    [
        ("alphabet", "alphabet"),
        ("phase", "phase_quotient"),
        ("fourier", "fourier"),
        ("ad5", "bounded_ad5"),
        ("ad5_batch", "bounded_ad5_context_batch"),
        ("unary", "unary_convolution"),
    ],
)
def test_registry_reports_bind_exact_rendered_receipt_and_stable_mathematics(tmp_path, monkeypatch, profiles, name, suffix):
    from test_local_science_tools import _registry

    tool = "level25_heisenberg_augmented_four_block_" + suffix
    monkeypatch.setattr(project_tools, tool + "_profile", lambda: copy.deepcopy(profiles[name]))
    result = _registry(tmp_path).invoke(tool + "_report", {"path": "research/evidence.md", "terminal_marker": "EVIDENCE_COMPLETE"})
    text = (tmp_path / "artifacts/research/evidence.md").read_text(encoding="utf-8")
    receipt = json.loads(text.split("```json\n", 1)[1].split("\n```", 1)[0])
    assert result["receipt_sha256"] == sha(canonical_json(receipt))
    assert result["evidence_sha256"] == EXPECTED[name] == profile_evidence_sha256(receipt)
    assert result["certificate_verified"] is True
    assert "Mathematical evidence SHA-256: `" + EXPECTED[name] + "`" in text
    assert text.endswith("EVIDENCE_COMPLETE\n")


@pytest.mark.parametrize(
    "name,suffix",
    [
        ("alphabet", "alphabet"),
        ("phase", "phase_quotient"),
        ("fourier", "fourier"),
        ("ad5", "bounded_ad5"),
        ("ad5_batch", "bounded_ad5_context_batch"),
        ("unary", "unary_convolution"),
    ],
)
@pytest.mark.parametrize("failure", ["failed_certificate", "copied_seal", "rehashed_unreviewed_scope"])
def test_report_refuses_to_label_unverified_or_unreviewed_evidence_certified(tmp_path, monkeypatch, profiles, name, suffix, failure):
    from test_local_science_tools import _registry

    receipt = copy.deepcopy(profiles[name])
    if failure == "failed_certificate":
        receipt["certificate_verified"] = False
    else:
        receipt["request"]["unreviewed"] = True
        if failure == "rehashed_unreviewed_scope":
            receipt["evidence_sha256"] = profile_evidence_sha256(receipt)
    tool = "level25_heisenberg_augmented_four_block_" + suffix
    monkeypatch.setattr(project_tools, tool + "_profile", lambda: receipt)
    with pytest.raises(ScientificToolError, match="report not written"):
        _registry(tmp_path).invoke(tool + "_report", {"path": "research/evidence.md", "terminal_marker": "EVIDENCE_COMPLETE"})
    assert not (tmp_path / "artifacts/research/evidence.md").exists()


@pytest.mark.parametrize(
    "suffix", ["alphabet", "phase_quotient", "fourier", "bounded_ad5", "bounded_ad5_context_batch", "unary_convolution"]
)
@pytest.mark.parametrize("kind", ["profile", "report"])
def test_existing_science_assertions_with_explicit_saved_runtime_fixtures(tmp_path, monkeypatch, suffix, kind):
    import test_local_science_tools as tests

    install_saved_runner(monkeypatch)
    test = getattr(tests, "test_level25_heisenberg_augmented_four_block_" + suffix + "_" + kind)
    if kind == "profile":
        test()
    else:
        test(tmp_path)
