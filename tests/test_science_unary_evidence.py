"""Unary identities and genuine Python math; saved native output is not isolation proof."""

from __future__ import annotations

import copy

import pytest
from test_science_evidence import EXPECTED, FACTORIES, install_saved_runner, saved_output, sha, unary_python_fixture

from reasoning_ledger.agents import heisenberg_level25_four_block as unary
from reasoning_ledger.agents import heisenberg_level25_three_block_coverage as coverage
from reasoning_ledger.agents.science_evidence import exact_execution_matches, profile_evidence_sha256, reviewed_profile_matches


@pytest.fixture(scope="module")
def profiles():
    with pytest.MonkeyPatch.context() as patch:
        install_saved_runner(patch)
        return {name: factory() for name, factory in FACTORIES.items()}


def test_genuine_python_histogram_and_five_direct_convolutions_match_reviewed_evidence():
    result = unary._python_replay()
    assert result == unary_python_fixture()
    assert result["histogram_checksum"] == 6465411173863107267
    assert result["support_checksum"] == 17561451370375839639


_PARSED = unary._parse_transcript(saved_output("unary"))


@pytest.mark.parametrize("name", _PARSED)
def test_every_cached_unary_field_must_agree_with_exact_raw_bytes(profiles, name):
    computation = copy.deepcopy(profiles["unary"]["computation"])
    value = computation[name]
    if isinstance(value, list):
        value[0] += 1
    else:
        computation[name] += 1
    assert (
        exact_execution_matches(
            computation,
            parser=unary._parse_transcript,
            expected_program=unary._REVIEWED_PROGRAM_SHA256,
            expected_transcript=unary._REVIEWED_TRANSCRIPT_SHA256,
        )
        is False
    )


@pytest.mark.parametrize("name", _PARSED)
def test_rehashed_changed_unary_transcript_is_not_reviewed_math(profiles, name):
    computation = copy.deepcopy(profiles["unary"]["computation"])
    stdout = computation["transcript"]
    line = next(line for line in stdout.splitlines() if line.startswith(name + "="))
    values = line.split("=", 1)[1].split(",")
    values[0] = str(int(values[0]) + 1)
    changed = stdout.replace(line + "\n", name + "=" + ",".join(values) + "\n")
    computation.update(unary._parse_transcript(changed))
    computation.update(transcript=changed, transcript_sha256=sha(changed))
    assert (
        exact_execution_matches(
            computation,
            parser=unary._parse_transcript,
            expected_program=unary._REVIEWED_PROGRAM_SHA256,
            expected_transcript=unary._REVIEWED_TRANSCRIPT_SHA256,
        )
        is False
    )


@pytest.mark.parametrize(
    "name,parser,program,transcript",
    [
        (
            "alphabet",
            coverage._parse_four_block_transcript,
            coverage._FOUR_BLOCK_REVIEWED_PROGRAM_SHA256,
            coverage._FOUR_BLOCK_REVIEWED_TRANSCRIPT_SHA256,
        ),
        (
            "phase",
            coverage._parse_four_block_phase_transcript,
            coverage._FOUR_BLOCK_PHASE_REVIEWED_PROGRAM_SHA256,
            coverage._FOUR_BLOCK_PHASE_REVIEWED_TRANSCRIPT_SHA256,
        ),
        (
            "fourier",
            coverage._parse_four_block_fourier_transcript,
            coverage._FOUR_BLOCK_FOURIER_REVIEWED_PROGRAM_SHA256,
            coverage._FOUR_BLOCK_FOURIER_REVIEWED_TRANSCRIPT_SHA256,
        ),
        ("unary", unary._parse_transcript, unary._REVIEWED_PROGRAM_SHA256, unary._REVIEWED_TRANSCRIPT_SHA256),
    ],
)
@pytest.mark.parametrize("field", ["program_sha256", "transcript_sha256", "transcript"])
def test_deterministic_primary_identities_cannot_be_copied_or_inconsistent(profiles, name, parser, program, transcript, field):
    computation = copy.deepcopy(profiles[name]["computation"])
    computation[field] = "0" * 64 if field.endswith("sha256") else computation[field] + "\n"
    assert exact_execution_matches(computation, parser=parser, expected_program=program, expected_transcript=transcript) is False


@pytest.mark.parametrize("field", ["histogram_checksum", "support_checksum"])
def test_large_integer_checksums_are_exact_not_binary_float_approximations(profiles, field):
    profile = copy.deepcopy(profiles["unary"])
    profile["independent_python_replay"][field] = int(float(profile["independent_python_replay"][field]))
    assert profile_evidence_sha256(profile) != EXPECTED["unary"]
    assert reviewed_profile_matches(profile, unary._UNARY_EVIDENCE_SHA256) is False


def test_changed_independent_histogram_and_copied_prerequisite_seal_do_not_verify(profiles, monkeypatch):
    with pytest.MonkeyPatch.context() as patch:
        install_saved_runner(patch)
        changed = copy.deepcopy(unary_python_fixture())
        changed["histogram_checksum"] += 1
        patch.setattr(unary, "_python_replay", lambda: changed)
        result = unary.level25_heisenberg_augmented_four_block_unary_convolution_profile()
        assert result["certificate"]["compiled_and_python_histograms_agree"] is False
        assert result["certificate_verified"] is False
    with pytest.MonkeyPatch.context() as patch:
        install_saved_runner(patch)
        prerequisite = copy.deepcopy(profiles["fourier"])
        prerequisite["request"]["unreviewed"] = True
        patch.setattr(unary, "level25_heisenberg_augmented_four_block_fourier_profile", lambda: prerequisite)
        result = unary.level25_heisenberg_augmented_four_block_unary_convolution_profile()
        assert result["certificate"]["corrected_fourier_domain_is_the_certified_prerequisite"] is False
        assert result["certificate_verified"] is False
