"""Batch evidence checks with saved runtime fixtures, not sandbox acceptance."""

from __future__ import annotations

import copy
import json
import subprocess

import pytest
from test_science_evidence import EXPECTED, FACTORIES, batch_domain_fixture, install_saved_runner, saved_output, sha

from reasoning_ledger.agents import heisenberg_level25_four_block_ad5_batch as batch
from reasoning_ledger.agents.science_evidence import bounded_transcript_sha256, profile_evidence_sha256
from reasoning_ledger.agents.scientific import ScientificToolError
from reasoning_ledger.canonical import canonical_json


@pytest.fixture(scope="module")
def profiles():
    with pytest.MonkeyPatch.context() as patch:
        install_saved_runner(patch)
        return {name: factory() for name, factory in FACTORIES.items()}


@pytest.fixture
def batch_profile(profiles, monkeypatch):
    def profile(computation=None, domain=None):
        monkeypatch.setattr(
            batch, "_run_program", lambda: copy.deepcopy(profiles["ad5_batch"]["computation"] if computation is None else computation)
        )
        monkeypatch.setattr(
            batch,
            "_run_domain_replay",
            lambda: copy.deepcopy(profiles["ad5_batch"]["independent_domain_replay"] if domain is None else domain),
        )
        monkeypatch.setattr(batch, "level25_heisenberg_augmented_four_block_fourier_profile", lambda: copy.deepcopy(profiles["fourier"]))
        monkeypatch.setattr(
            batch, "level25_heisenberg_augmented_four_block_phase_quotient_profile", lambda: copy.deepcopy(profiles["phase"])
        )

        def no_witness_replay(witness):
            pytest.fail("the reviewed empty batch must not execute a fabricated witness")

        monkeypatch.setattr(batch, "_run_independent_replay", no_witness_replay)
        return batch.level25_heisenberg_augmented_four_block_bounded_ad5_context_batch_profile()

    return profile


_LINES = [line for line in saved_output("ad5_batch").splitlines()[1:-1] if not line.startswith("vm_peak_kib=")]


@pytest.mark.parametrize("line", _LINES, ids=lambda line: line.split("=")[0])
def test_every_batch_field_except_memory_is_in_the_reviewed_mathematics(line):
    name, value = line.split("=", 1)
    if name == "status" or name.endswith("_status"):
        changed = "UNREVIEWED_RESULT"
    elif name in ("witness_coordinates", "witness_equation_values", "witness_jacobian"):
        count = {"witness_coordinates": 25, "witness_equation_values": 22, "witness_jacobian": 550}[name]
        changed = ",".join(["1"] * count)
    elif "," in value:
        values = value.split(",")
        values[0] = str(int(values[0]) + 1)
        changed = ",".join(values)
    else:
        changed = str(int(value) + 1)
    stdout = saved_output("ad5_batch").replace(line + "\n", name + "=" + changed + "\n")
    assert bounded_transcript_sha256(stdout, parser=batch._parse_transcript) != batch._REVIEWED_MATHEMATICAL_TRANSCRIPT_SHA256


@pytest.mark.parametrize("memory", [1, 11332, 11336, 64000, 65536, -1, 0, 65537])
def test_memory_variation_does_not_change_mathematics_but_cannot_waive_the_cap(profiles, batch_profile, memory):
    computation = copy.deepcopy(profiles["ad5_batch"]["computation"])
    stdout = computation["transcript"].replace("vm_peak_kib=11336\n", f"vm_peak_kib={memory}\n")
    computation.update(transcript=stdout, transcript_sha256=sha(stdout), vm_peak_kib=memory)
    computation["mathematical_transcript_sha256"] = bounded_transcript_sha256(stdout, parser=batch._parse_transcript)
    assert computation["mathematical_transcript_sha256"] == batch._REVIEWED_MATHEMATICAL_TRANSCRIPT_SHA256
    result = batch_profile(computation)
    valid = 0 < memory <= 65536
    assert result["certificate_verified"] is valid
    assert result["certificate"]["resident_telemetry_respects_the_address_space_cap"] is valid
    assert (result["evidence_sha256"] == EXPECTED["ad5_batch"]) is valid
    if memory != 11336:
        assert sha(canonical_json(result)) != sha(canonical_json(profiles["ad5_batch"]))


@pytest.mark.parametrize("field", ["program_sha256", "transcript_sha256", "mathematical_transcript_sha256", "aggregate_charge_total"])
def test_copied_or_inconsistent_primary_identity_does_not_verify(profiles, batch_profile, field):
    computation = copy.deepcopy(profiles["ad5_batch"]["computation"])
    computation[field] = "0" * 64 if field.endswith("sha256") else 1
    assert batch_profile(computation)["certificate_verified"] is False


@pytest.mark.parametrize("field", ["charge_total", "status", "d_code", "residual_tests_completed"])
def test_cached_context_fields_must_agree_with_raw_bytes(profiles, batch_profile, field):
    computation = copy.deepcopy(profiles["ad5_batch"]["computation"])
    computation["contexts"][0][field] = "UNREVIEWED" if field == "status" else -1
    result = batch_profile(computation)
    assert result["certificate"]["program_and_mathematical_transcript_match_reviewed_execution"] is False
    assert result["certificate_verified"] is False


@pytest.mark.parametrize("field", ["program_sha256", "transcript_sha256", "charged_operations", "runtime_address_space_limit_bytes"])
def test_independent_replay_source_raw_math_and_limits_are_not_replaceable(profiles, batch_profile, field):
    domain = copy.deepcopy(profiles["ad5_batch"]["independent_domain_replay"])
    domain[field] = "0" * 64 if field.endswith("sha256") else 1
    assert batch_profile(domain=domain)["certificate_verified"] is False


def test_rehashing_changed_independent_domain_is_not_reviewed_evidence(profiles, batch_profile):
    domain = copy.deepcopy(profiles["ad5_batch"]["independent_domain_replay"])
    values = json.loads(domain["transcript"])
    values["charged_operations"] += 1
    domain.update(values)
    domain["transcript"] = canonical_json(values) + "\n"
    domain["transcript_sha256"] = sha(domain["transcript"])
    assert batch_profile(domain=domain)["certificate_verified"] is False


@pytest.mark.parametrize("name", ["fourier", "phase"])
def test_copied_prerequisite_seal_is_recomputed(profiles, batch_profile, monkeypatch, name):
    batch_profile()
    changed = copy.deepcopy(profiles[name])
    changed["request"]["unreviewed"] = True
    tool = "level25_heisenberg_augmented_four_block_" + ("fourier" if name == "fourier" else "phase_quotient") + "_profile"
    monkeypatch.setattr(batch, tool, lambda: changed)
    assert batch.level25_heisenberg_augmented_four_block_bounded_ad5_context_batch_profile()["certificate_verified"] is False


@pytest.mark.parametrize(
    "change",
    [
        lambda s: s.rstrip(),
        lambda s: s.replace("\n", "\r\n"),
        lambda s: s.replace("vm_peak_kib=11336\n", ""),
        lambda s: s.replace("vm_peak_kib=11336\n", "vm_peak_kib=11336\nvm_peak_kib=11336\n"),
        lambda s: s.replace("vm_peak_kib=11336", "vm_peak_kib=011336"),
        lambda s: s.replace("vm_peak_kib=11336", "vm_peak_kib=unknown"),
        lambda s: s.replace("context_0_d_code=281", "context_0_d_code=281\ncontext_0_d_code=281"),
        lambda s: s.replace("context_0_d_code=281\n", ""),
        lambda s: s.replace("context_0_d_code=281", "context_4_d_code=281"),
        lambda s: s.replace("REASONING_END", "OTHER_END"),
    ],
)
def test_incomplete_duplicate_or_ambiguous_transcripts_cannot_normalize(change):
    with pytest.raises(ScientificToolError):
        bounded_transcript_sha256(change(saved_output("ad5_batch")), parser=batch._parse_transcript)


@pytest.mark.parametrize("field", ["interpreter", "transcript"])
def test_raw_replay_provenance_is_kept_separate_from_mathematical_identity(profiles, field):
    profile = copy.deepcopy(profiles["ad5_batch"])
    profile["independent_domain_replay"][field] = "OTHER-EXPLICIT-RUNTIME-PROVENANCE"
    assert profile_evidence_sha256(profile) == EXPECTED["ad5_batch"]
    assert sha(canonical_json(profile)) != sha(canonical_json(profiles["ad5_batch"]))


@pytest.mark.parametrize("missing", ["independent_domain_replay", "independent_witness_replay", "phase_evidence_sha256"])
def test_missing_batch_evidence_has_no_normalization_fallback(profiles, missing):
    profile = copy.deepcopy(profiles["ad5_batch"])
    del profile[missing]
    with pytest.raises(ScientificToolError):
        profile_evidence_sha256(profile)


def test_domain_replay_preserves_raw_output_and_keeps_its_isolated_resource_limit(monkeypatch):
    wraps = []
    fixture = batch_domain_fixture()

    def wrap(command, **kwargs):
        wraps.append((command, kwargs))
        return ["TEST-ONLY-SANDBOX-BOUNDARY", *command]

    def run(command, **kwargs):
        assert command[0] == "TEST-ONLY-SANDBOX-BOUNDARY"
        assert kwargs["timeout"] == 300
        return subprocess.CompletedProcess(command, 0, fixture["transcript"], "")

    monkeypatch.setattr(batch.sandbox, "wrap", wrap)
    monkeypatch.setattr(batch.subprocess, "run", run)
    result = batch._run_domain_replay()
    assert len(wraps) == 1
    assert wraps[0][1]["network"] is False
    assert wraps[0][1]["ro"][0].name == "heisenberg_level25_four_block_ad5_batch_replay.py"
    assert any("setrlimit" in argument for argument in wraps[0][0])
    assert "67108864" in wraps[0][0]
    assert "-I" in wraps[0][0] and "-S" in wraps[0][0]
    assert result["transcript"] == fixture["transcript"]
    assert result["transcript_sha256"] == sha(result["transcript"])
    assert result["program_sha256"] == batch._REVIEWED_DOMAIN_REPLAY_PROGRAM_SHA256
