# ruff: noqa: E501
"""Exact AD5 join batch on canonical central representatives 9 through 12."""

from __future__ import annotations

import hashlib
from functools import partial
from typing import Any

from ..canonical import canonical_json
from .heisenberg_level25_four_block_ad5 import _RESIDENT_BYTE_CAP, _source_manifest
from .heisenberg_level25_four_block_ad5_join import (
    _JOIN_EVIDENCE_SHA256,
    level25_heisenberg_augmented_four_block_ad5_coordinate3_join_partition_covariance_profile,
)
from .heisenberg_level25_four_block_ad5_representative_batch import (
    _PROGRAM as _PRIOR_PROGRAM,
)
from .heisenberg_level25_four_block_ad5_representative_batch import (
    _REPLAY_PROGRAM as _PRIOR_REPLAY_PROGRAM,
)
from .heisenberg_level25_four_block_ad5_representative_batch import (
    _REPRESENTATIVE_EVIDENCE_SHA256,
    _parse_program_transcript,
    _parse_replay_transcript,
    _representative_prefix_sha256,
    level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_batch_profile,
)
from .heisenberg_level25_four_block_ad5_slice import (
    _SLICE_EVIDENCE_SHA256,
    level25_heisenberg_augmented_four_block_ad5_coordinate3_central_orbit_representative_preflight_profile,
)
from .heisenberg_level25_three_block import _run_source_hashed_program
from .science_evidence import bounded_execution_matches, bounded_transcript_sha256, reviewed_profile_matches, seal_profile_evidence

_CONTEXT_COUNT = 4
_CONTEXT_POSITIONS = (9, 10, 11, 12)
_EXPECTED_PREFIX_CODES = (280, 281, 480, 489, 506, 514, 752, 753, 1202, 1221, 1228, 1246, 2527)
_ENGINE_CHARGE_CAP = 270_000_000
_COMBINED_CHARGE_CAP = 540_000_000
_REPRESENTATIVE_9_12_EVIDENCE_SHA256 = "bd0bfbe69db2eeb922529526e3c4c64742e7e62b272cdf3e910123e58864f944"
_COVARIANCE_RECEIPT_SHA256 = "a2c96c3b973a96cb9589297af4dff727916068f9e93bd1d7f2a14818d414ab1c"
_SLICE_SHAPE_SHA256 = "f160568c8456b21812d2a67a380f9224bf03f8be39070d5aaf1278fd195c5e15"
_SLICE_REPRESENTATIVE_SHA256 = "a9d128cc5614b2dbffad31c2fa04aa372f5bbacb4397143e4f74e52f23704dc2"
_FIRST_CONTEXT_RECEIPT_SHA256 = "12a4667534b366e72110fe383631fa04b6fb29dc5b8ccc21294e6eb376c2d7cc"
_NEXT_FOUR_RECEIPT_SHA256 = "1e20108b0a39aa7ec257695dd0e158cdf5abfd1124ea5ecbd4b2f9ef220ea9ed"
_PRIOR_REPRESENTATIVE_BATCH_RECEIPT_SHA256 = "5bfa939205869ccc365cf5d7b231c8eeb6f2f925e5d606a2df65887b656068ca"
_NATIVE_DESIGN_SHA256 = "5d145c6ca6d3e03ef46bfa4f03c92f64cb1aa923ce80d8c47b86c21c92abf824"
_CODEX_AUDIT_SHA256 = "73d0169d35f1c90f19b9a4c3be3d046335dab3686c3da5a4f2ae12a6719f02fd"
_REVIEWED_PROGRAM_SHA256 = "03e6337fda02411e8c7b4fdc28a96be45140bb65646bbfc1cd01834fa3f24212"
_REVIEWED_MATHEMATICAL_TRANSCRIPT_SHA256 = "6f542b95f7d65b2b896a627ed01b3c4ca2ab17bb9265d924dfe4ef393fae65b7"
_REVIEWED_REPLAY_PROGRAM_SHA256 = "4b150a79c49d8ff026eb466eab50c78cef9cdd46fa7e327d567d9fa0cffe5e92"
_REVIEWED_REPLAY_MATHEMATICAL_TRANSCRIPT_SHA256 = "367c3d755b00534d6963c3604e9a9d5e6d76e4ec4c8bde02709555895b249773"


def _replace_exact(source: str, old: str, new: str, *, count: int = 1) -> str:
    if source.count(old) != count:
        raise RuntimeError("prior representative batch source no longer has the expected splice point")
    return source.replace(old, new)


_PROGRAM = _PRIOR_PROGRAM
for _old, _new, _count in (
    ("D_POSITIONS{{5,6,7,8}}", "D_POSITIONS{{9,10,11,12}}", 1),
    ("std::array<u64,45> rep_prefix_orbits", "std::array<u64,65> rep_prefix_orbits", 1),
    ("std::array<u64,45>out", "std::array<u64,65>out", 1),
    ("packed<9", "packed<13", 1),
    ("representative_prefix_check_count=9", "representative_prefix_check_count=13", 1),
    ("FIRST_REPRESENTATIVE_JOIN_BATCH_EMPTY", "SECOND_REPRESENTATIVE_JOIN_BATCH_EMPTY", 2),
):
    _PROGRAM = _replace_exact(_PROGRAM, _old, _new, count=_count)

_REPLAY_PROGRAM = _PRIOR_REPLAY_PROGRAM
for _old, _new, _count in (
    ("std::array<u64,45> rep_prefix_orbits", "std::array<u64,65> rep_prefix_orbits", 1),
    ("std::array<u64,45>result", "std::array<u64,65>result", 1),
    ("seed<9", "seed<13", 1),
    ("positions{{5,6,7,8}}", "positions{{9,10,11,12}}", 1),
    (
        "std::array<std::uint32_t,9>expected{{280,281,480,489,506,514,752,753,1202}};for(int position=0;position<9",
        "std::array<std::uint32_t,13>expected{{280,281,480,489,506,514,752,753,1202,1221,1228,1246,2527}};for(int position=0;position<13",
        1,
    ),
    ("representative_prefix_check_count=9", "representative_prefix_check_count=13", 1),
    ("FIRST_REPRESENTATIVE_JOIN_BATCH_EMPTY", "SECOND_REPRESENTATIVE_JOIN_BATCH_EMPTY", 1),
):
    _REPLAY_PROGRAM = _replace_exact(_REPLAY_PROGRAM, _old, _new, count=_count)

_PRIMARY_PARSER = partial(_parse_program_transcript, representative_prefix_check_count=13)
_REPLAY_PARSER = partial(_parse_replay_transcript, representative_prefix_check_count=13)


def _run_program() -> dict[str, Any]:
    result = _run_source_hashed_program(
        _PROGRAM,
        label="ad5-representative-join-batch-9-12",
        timeout_seconds=900,
        runtime_address_space_bytes=_RESIDENT_BYTE_CAP,
        preserve_exact_transcript=True,
    )
    return {
        **result,
        **_PRIMARY_PARSER(result["transcript"]),
        "mathematical_transcript_sha256": bounded_transcript_sha256(result["transcript"], parser=_PRIMARY_PARSER),
    }


def _run_replay() -> dict[str, Any]:
    result = _run_source_hashed_program(
        _REPLAY_PROGRAM,
        label="ad5-representative-join-batch-9-12-replay",
        timeout_seconds=900,
        runtime_address_space_bytes=_RESIDENT_BYTE_CAP,
        preserve_exact_transcript=True,
    )
    return {
        **result,
        **_REPLAY_PARSER(result["transcript"]),
        "mathematical_transcript_sha256": bounded_transcript_sha256(result["transcript"], parser=_REPLAY_PARSER),
    }


def level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_batch_9_12_profile() -> dict[str, Any]:
    """Exhaust the exact joins for canonical packed representatives 9 through 12."""

    covariance = level25_heisenberg_augmented_four_block_ad5_coordinate3_join_partition_covariance_profile()
    covariance_receipt = hashlib.sha256(canonical_json(covariance).encode()).hexdigest()
    slice_profile = level25_heisenberg_augmented_four_block_ad5_coordinate3_central_orbit_representative_preflight_profile()
    prior_batch = level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_batch_profile()
    prior_batch_receipt = hashlib.sha256(canonical_json(prior_batch).encode()).hexdigest()
    computation = _run_program()
    replay = _run_replay()
    source_manifest = _source_manifest()
    expected_status = "SECOND_REPRESENTATIVE_JOIN_BATCH_EMPTY"
    shared_context_fields = (
        "status",
        "d_position",
        "d_code",
        "b_states_completed",
        "c_states_completed",
        "exact_key_match_count",
        "residual_tests_completed",
        "residual_survivor_count",
        "decomposition_failure_count",
        "b_record_checksum",
        "c_key_match_checksum",
        "match_stream_checksum",
    )
    context_agreement = all(
        all(primary[field] == independent[field] for field in shared_context_fields)
        for primary, independent in zip(computation["contexts"], replay["contexts"], strict=True)
    )
    prefix_sha256 = _representative_prefix_sha256(computation["representative_prefix_orbits"])
    replay_prefix_sha256 = _representative_prefix_sha256(replay["representative_prefix_orbits"])
    context_hashes = {
        str(context["packed_representative"]): hashlib.sha256(
            canonical_json(
                {
                    "batch_local_ordinal": context["batch_local_ordinal"],
                    "packed_representative": context["packed_representative"],
                    "d_code": context["d_code"],
                    "d_exponents": context["d_exponents"],
                    "d_values": context["d_values"],
                }
            ).encode()
        ).hexdigest()
        for context in computation["contexts"]
    }
    input_manifest = {
        "covariance_receipt_sha256": _COVARIANCE_RECEIPT_SHA256,
        "slice_shape_sha256": _SLICE_SHAPE_SHA256,
        "slice_representative_sha256": _SLICE_REPRESENTATIVE_SHA256,
        "first_context_receipt_sha256": _FIRST_CONTEXT_RECEIPT_SHA256,
        "next_four_receipt_sha256": _NEXT_FOUR_RECEIPT_SHA256,
        "prior_representative_batch_receipt_sha256": _PRIOR_REPRESENTATIVE_BATCH_RECEIPT_SHA256,
        "native_design_sha256": _NATIVE_DESIGN_SHA256,
        "codex_audit_sha256": _CODEX_AUDIT_SHA256,
        "source_records_sha256": source_manifest["records_sha256"],
    }
    input_manifest_sha256 = hashlib.sha256(canonical_json(input_manifest).encode()).hexdigest()
    complete_contexts = all(
        context["status"] == "CONTEXT_EMPTY"
        and context["b_states_completed"] == 110_500
        and context["c_states_completed"] == 22_100
        and context["residual_tests_completed"] == context["exact_key_match_count"]
        and context["residual_survivor_count"] == 0
        and context["decomposition_failure_count"] == 0
        for context in computation["contexts"]
    )
    certificate = {
        "covariance_certificate_is_bound_and_verified": reviewed_profile_matches(covariance, _JOIN_EVIDENCE_SHA256),
        "slice_is_the_fresh_reviewed_prerequisite": reviewed_profile_matches(slice_profile, _SLICE_EVIDENCE_SHA256),
        "prior_batch_is_the_fresh_reviewed_prerequisite": reviewed_profile_matches(prior_batch, _REPRESENTATIVE_EVIDENCE_SHA256),
        "accepted_cursor_9_and_global_representative_stream_are_bound": prior_batch["decision"]["accepted_exact"] is True
        and prior_batch["decision"]["public_cursor_start"] == 5
        and prior_batch["decision"]["public_next_unaccepted_packed"] == 9
        and prior_batch["decision"]["packed_representatives_exhausted"] == [5, 6, 7, 8]
        and computation["representative_prefix_orbits"][:45] == prior_batch["computation"]["representative_prefix_orbits"]
        and all(computation[name] == prior_batch["computation"][name] for name in
                ("zero_bucket_prefix_codes", "shape_count", "zero_bucket_count", "histogram_checksum", "zero_bucket_checksum", "a_shape_code", "a_shape_exponents", "a_shape_values"))
        and covariance["decision"]["representative_outer_domain_authorized"] is True
        and covariance["computation"]["slice_shape_sha256"] == slice_profile["computation"]["shape_stream_sha256"] == _SLICE_SHAPE_SHA256
        and covariance["computation"]["slice_representative_sha256"] == slice_profile["computation"]["representative_stream_sha256"] == _SLICE_REPRESENTATIVE_SHA256
        and covariance["computation"]["representative_count"] == slice_profile["computation"]["representative_count"] == 3_907_280
        and all(parent["source_manifest"]["records_sha256"] == source_manifest["records_sha256"] for parent in (covariance, slice_profile, prior_batch)),
        "local_representative_prefix_is_derived_twice": computation[
            "representative_prefix_check_count"
        ]
        == replay["representative_prefix_check_count"]
        == 13
        and computation["representative_prefix_failure_count"]
        == replay["representative_prefix_failure_count"]
        == 0
        and computation["representative_prefix_orbits"]
        == replay["representative_prefix_orbits"]
        and prefix_sha256 == replay_prefix_sha256,
        "contexts_are_exactly_packed_representatives_9_through_12": [
            context["packed_representative"] for context in computation["contexts"]
        ]
        == list(_CONTEXT_POSITIONS)
        and [context["d_code"] for context in computation["contexts"]]
        == list(_EXPECTED_PREFIX_CODES[9:13])
        and [context["batch_local_ordinal"] for context in computation["contexts"]]
        == list(range(_CONTEXT_COUNT)),
        "primary_contexts_are_atomic_and_complete": computation["contexts_started"]
        == computation["contexts_exhausted"]
        == _CONTEXT_COUNT
        and complete_contexts,
        "sorted_independent_join_replay_agrees_exactly": replay["status"]
        == computation["status"]
        == expected_status
        and context_agreement,
        "source_and_state_domains_are_exact": source_manifest["raw_record_count"]
        == computation["source_record_count"]
        == 725
        and computation["zero_bucket_count"] == 4_420
        and all(
            context["b_states_completed"] == 110_500
            and context["c_states_completed"] == 22_100
            for context in replay["contexts"]
        ),
        "both_engines_respect_hard_resource_caps": computation[
            "aggregate_charge_total"
        ]
        <= computation["aggregate_search_charge_cap"]
        == _ENGINE_CHARGE_CAP
        and replay["charged_operations"] <= _ENGINE_CHARGE_CAP
        and computation["aggregate_charge_total"] + replay["charged_operations"]
        <= _COMBINED_CHARGE_CAP
        and computation["rlimit_as_soft"]
        == computation["rlimit_as_hard"]
        == replay["rlimit_as_soft"]
        == replay["rlimit_as_hard"]
        == computation["runtime_address_space_limit_bytes"]
        == replay["runtime_address_space_limit_bytes"]
        == _RESIDENT_BYTE_CAP,
        "resident_telemetry_respects_the_address_space_cap": all(0 < engine["vm_peak_kib"] * 1024 <= _RESIDENT_BYTE_CAP for engine in (computation, replay)),
        "programs_and_transcripts_match_reviewed_executions": bounded_execution_matches(
            computation, parser=_PRIMARY_PARSER, expected_program=_REVIEWED_PROGRAM_SHA256,
            expected_mathematics=_REVIEWED_MATHEMATICAL_TRANSCRIPT_SHA256,
        ) and bounded_execution_matches(
            replay, parser=_REPLAY_PARSER, expected_program=_REVIEWED_REPLAY_PROGRAM_SHA256,
            expected_mathematics=_REVIEWED_REPLAY_MATHEMATICAL_TRANSCRIPT_SHA256,
        ),
        "terminal_status_advances_only_the_atomic_public_cursor": computation["status"]
        == expected_status
        and complete_contexts,
    }
    verified = all(certificate.values())
    return seal_profile_evidence({
        "schema": "reasoning.level25-heisenberg-ad5-coordinate3-representative-join-batch-9-12.v2",
        "request": {},
        "request_sha256": hashlib.sha256(canonical_json({}).encode()).hexdigest(),
        "input_manifest": input_manifest,
        "input_manifest_sha256": input_manifest_sha256,
        "review_reference_policy": {"kind": "declared_source_literals", "external_contents_verified": False, "receipt_references_are_current_approvals": False},
        "covariance_receipt_sha256": covariance_receipt,
        "covariance_evidence_sha256": covariance["evidence_sha256"],
        "slice_receipt_sha256": hashlib.sha256(canonical_json(slice_profile).encode()).hexdigest(),
        "slice_evidence_sha256": slice_profile["evidence_sha256"],
        "prior_batch_receipt_sha256": prior_batch_receipt,
        "prior_batch_evidence_sha256": prior_batch["evidence_sha256"],
        "charge_scope": "named primary and independent sorted replay only; prerequisite executions are separate",
        "representative_prefix_sha256": prefix_sha256,
        "context_hashes": context_hashes,
        "source_manifest": source_manifest,
        "computation": computation,
        "independent_sorted_join_replay": replay,
        "actual_combined_charge": computation["aggregate_charge_total"] + replay["charged_operations"],
        "certificate": certificate,
        "certificate_verified": verified,
        "decision": {
            "status": computation["status"],
            "accepted_exact": verified,
            "public_cursor_start": 9,
            "public_next_unaccepted_packed": 13 if verified else 9,
            "packed_representatives_exhausted": list(_CONTEXT_POSITIONS) if verified else [],
            "exact_key_match_counts": [
                context["exact_key_match_count"] for context in computation["contexts"]
            ],
            "residual_survivor_count": sum(
                context["residual_survivor_count"] for context in computation["contexts"]
            ),
            "common_zero_found": False,
            "full_contraction_authorized": False,
        },
        "interpretation": (
            "Two source-hashed compiled engines with different join algorithms exhaust "
            "the exact B/C joins for canonical packed representatives 9 through 12. "
            "The public cursor advances atomically to 13 only after exact stream and "
            "residual agreement for all four contexts."
        ),
        "not_certified": [
            "any packed representative at or after 13",
            "an execution or resource price for the complete 3,907,280-representative domain",
            "absence of geometric common zeros over extensions of F_101",
            "a Fourier transform, convolution, tensor contraction, or full contraction",
            "a characteristic-zero lift or global smoothness",
            "a Brauer class, period, index bound, or period-index counterexample",
        ],
        "next_gate": {
            "feature": "level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_batch_13_16_profile",
            "requirement": (
                "Derive the next four canonical packed representatives from the residual action, "
                "then run the same atomic primary and sorted-replay join gate without extrapolating "
                "a full-domain price."
            ),
        },
    })
