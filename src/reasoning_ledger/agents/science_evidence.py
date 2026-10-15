"""Deterministic mathematical identities separate from execution provenance."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any

from ..canonical import canonical_json
from .scientific import ScientificToolError

_PREFIX = "reasoning.level25-heisenberg-augmented-four-block-"
_SIGNATURE_SCHEMA = "reasoning.level25-heisenberg-ad5-coordinate3-functional-signature-diagnostic.v3"
_CENTRAL_KEY_SCHEMA = "reasoning.level25-heisenberg-ad5-coordinate3-central-key-diagnostic.v2"
_PREFIX_CENSUS_SCHEMA = _PREFIX + "ad5-prefix-shift-census-profile.v2"
_ORBIT_SCHEMA = "reasoning.level25-heisenberg-ad5-coordinate3-tensor-orbit-diagnostic.v2"
_SLICE_SCHEMA = "reasoning.level25-heisenberg-ad5-coordinate3-central-slice-preflight.v2"
_JOIN_SCHEMA = "reasoning.level25-heisenberg-ad5-coordinate3-join-partition-covariance.v2"
_REPRESENTATIVE_SCHEMA = "reasoning.level25-heisenberg-ad5-coordinate3-representative-join-batch.v2"
_REPRESENTATIVE_9_12_SCHEMA = "reasoning.level25-heisenberg-ad5-coordinate3-representative-join-batch-9-12.v2"
_SWEEP_SCHEMA = "reasoning.level25-heisenberg-ad5-representative-join-batch-13-16.v2.2"
_ROW0_PAIR_SCHEMA = _PREFIX + "row0-fixed-fiber-pair-calibration-profile.v2"
_SINGLETON_SCHEMA = "reasoning.level25-heisenberg-fixed-stratum-singleton-orbit-profile.v2"
_COVERAGE_SCHEMA = "reasoning.level25-heisenberg-singleton-orbit-three-block-coverage-profile.v2"
_LOCAL_SCHEMA = "reasoning.level25-heisenberg-augmented-fixed-and-cyclic-local-profile.v2"
_THREE_BLOCK_SCHEMA = "reasoning.level25-heisenberg-augmented-three-block-rational-profile.v2"
_ORIGINAL_SUPPORT_PREFIX = "reasoning.level25-heisenberg-balanced-five-pivot-"
_ORIGINAL_ALPHABET_SCHEMA = _ORIGINAL_SUPPORT_PREFIX + "pure-power-alphabet-profile.v2"
_ORIGINAL_TWO_BLOCK_SCHEMA = _ORIGINAL_SUPPORT_PREFIX + "two-block-rational-profile.v2"
_ORIGINAL_NONCONSECUTIVE_SCHEMA = _ORIGINAL_SUPPORT_PREFIX + "nonconsecutive-three-block-rational-profile.v2"
_ORIGINAL_CONSECUTIVE_SCHEMA = _ORIGINAL_SUPPORT_PREFIX + "consecutive-three-block-rational-profile.v2"
_ORIGINAL_SUPPORT_SCHEMAS = {
    _ORIGINAL_ALPHABET_SCHEMA, _ORIGINAL_TWO_BLOCK_SCHEMA,
    _ORIGINAL_NONCONSECUTIVE_SCHEMA, _ORIGINAL_CONSECUTIVE_SCHEMA,
}
_NESTED_PREREQUISITES = {_ORIGINAL_TWO_BLOCK_SCHEMA: ("pure_power_prerequisite",)}
_PYTHON_ONLY_SCHEMAS = {_LOCAL_SCHEMA}
_COVERAGE_NATIVE_SCHEMAS = {_SINGLETON_SCHEMA, _COVERAGE_SCHEMA, _THREE_BLOCK_SCHEMA}
_ROW0_SCHEMAS = {
    _PREFIX + "row0-feasibility-profile.v2",
    _PREFIX + "row0-label-unary-rank-profile.v2",
    _PREFIX + "row0-kernel-rank-profile.v2",
    _ROW0_PAIR_SCHEMA,
    _PREFIX + "row0-pair-reuse-profile.v2",
    _PREFIX + "row0-edge-indicator-rank-profile.v2",
}
_SCHEMAS = {
    _ORIGINAL_ALPHABET_SCHEMA: (),
    _ORIGINAL_TWO_BLOCK_SCHEMA: (),
    _ORIGINAL_NONCONSECUTIVE_SCHEMA: ("pure_power_prerequisite",),
    _ORIGINAL_CONSECUTIVE_SCHEMA: ("pure_power_prerequisite",),
    _SINGLETON_SCHEMA: (),
    _COVERAGE_SCHEMA: ("fixed_stratum_screen",),
    _LOCAL_SCHEMA: ("coverage",),
    _THREE_BLOCK_SCHEMA: ("coverage",),
    _PREFIX + "alphabet-profile.v2": (),
    _PREFIX + "phase-quotient-profile.v2": (),
    _PREFIX + "fourier-profile.v2": ("alphabet",),
    _PREFIX + "unary-convolution-profile.v2": ("fourier",),
    _PREFIX + "row0-feasibility-profile.v2": ("unary",),
    _PREFIX + "row0-label-unary-rank-profile.v2": ("row0_feasibility",),
    _PREFIX + "row0-kernel-rank-profile.v2": ("label_unary_rank",),
    _ROW0_PAIR_SCHEMA: ("kernel_rank",),
    _PREFIX + "row0-pair-reuse-profile.v2": ("pair_calibration",),
    _PREFIX + "row0-edge-indicator-rank-profile.v2": ("pair_reuse",),
    _PREFIX + "bounded-ad5-profile.v2": ("fourier", "phase"),
    _PREFIX + "bounded-ad5-context-batch-profile.v2": ("fourier", "phase"),
    _PREFIX_CENSUS_SCHEMA: ("fourier", "phase"),
    _SIGNATURE_SCHEMA: ("fourier", "phase"),
    _CENTRAL_KEY_SCHEMA: ("fourier", "phase"),
    _ORBIT_SCHEMA: ("central_key",),
    _SLICE_SCHEMA: ("orbit",),
    _JOIN_SCHEMA: ("orbit", "slice"),
    _REPRESENTATIVE_SCHEMA: ("covariance", "slice", "first_context", "next_four"),
    _REPRESENTATIVE_9_12_SCHEMA: ("covariance", "slice", "prior_batch"),
    _SWEEP_SCHEMA: ("prior_batch",),
}
_COMPILED_REPLAYS = {
    _REPRESENTATIVE_SCHEMA: ("independent_sorted_join_replay",),
    _REPRESENTATIVE_9_12_SCHEMA: ("independent_sorted_join_replay",),
    _SWEEP_SCHEMA: ("independent_sorted_join_replay",),
}
_INDEPENDENT_REPLAYS = {
    _PREFIX + "row0-kernel-rank-profile.v2": ("independent_python_replay",),
    _PREFIX + "row0-pair-reuse-profile.v2": ("independent_python_replay",),
    _PREFIX + "row0-edge-indicator-rank-profile.v2": ("independent_python_replay",),
    _PREFIX + "bounded-ad5-profile.v2": ("independent_replay",),
    _PREFIX + "bounded-ad5-context-batch-profile.v2": ("independent_domain_replay", "independent_witness_replay"),
    _PREFIX_CENSUS_SCHEMA: ("independent_replay",),
    _SIGNATURE_SCHEMA: ("independent_replay",),
    _CENTRAL_KEY_SCHEMA: ("independent_replay",),
    _ORBIT_SCHEMA: ("independent_replay",),
    _SLICE_SCHEMA: ("independent_replay",),
    _JOIN_SCHEMA: ("independent_replay",),
}
_MEMORY_TELEMETRY_SCHEMAS = {
    _PREFIX + "bounded-ad5-profile.v2",
    _PREFIX + "bounded-ad5-context-batch-profile.v2",
    _CENTRAL_KEY_SCHEMA,
    _ORBIT_SCHEMA,
    _SLICE_SCHEMA,
    _JOIN_SCHEMA,
    _REPRESENTATIVE_SCHEMA,
    _REPRESENTATIVE_9_12_SCHEMA,
    _SWEEP_SCHEMA,
}
_REVIEWED_PROFILES = {
    _ORIGINAL_ALPHABET_SCHEMA: "6c5203617f2123454fdf41c6f50ea658d4c0edb8da4a6da4891f733ddbda3de0",
    _ORIGINAL_TWO_BLOCK_SCHEMA: "2000fce3966e4592c0e35f975ecf00282fa2162ce6f769b9f115ffa4a9f1d9ae",
    _ORIGINAL_NONCONSECUTIVE_SCHEMA: "ad3630016b6b3d2e23da038f971d6c53d020955536035d2ad6f2eb744dde5452",
    _ORIGINAL_CONSECUTIVE_SCHEMA: "bad2c7f1acb71276f0c807fefa5b8a72909127da123bdf210d5a6f74d9352b69",
    _SINGLETON_SCHEMA: "c30c8ec6ea77e72838fe365ebacec7df3b8226753ce6b557123c548feb56cf93",
    _COVERAGE_SCHEMA: "438fb3a52ef40fa7e41aace29658a66b4fc3c642388054a97e02bd3941e61ee1",
    _LOCAL_SCHEMA: "43e14323b47fbb532c19f58d1b7de175d0a6e855fc18ed63efd9c6a8c916f795",
    _THREE_BLOCK_SCHEMA: "1f6117123f053dcb07180e2cde3896b2dd0e1b2d5231bc1eb7f7a0d80f1250fe",
    _PREFIX + "row0-feasibility-profile.v2": "e904ad668cb66292c867f01d7a018a0a37af44ee3d9a572349bb652b4e4a87d7",
    _PREFIX + "row0-label-unary-rank-profile.v2": "5e6264db7b66415510212f1c4468b3e85a5860c4b78642825f7e4b2dac29a100",
    _PREFIX + "row0-kernel-rank-profile.v2": "972baf6f162875276e315ffa92421714df8a10418acc8e62e819df787ca13f18",
    _PREFIX + "row0-fixed-fiber-pair-calibration-profile.v2": "cb473fbb7a70dacabd981444bc7e582e3cf0523d3fc3ba8e504f6253ea3e15c5",
    _PREFIX + "row0-pair-reuse-profile.v2": "f5cf3ac39d594a70d0f66e2449cfa4db541dc62a2093a29be1e4168ef75dac4a",
    _PREFIX + "row0-edge-indicator-rank-profile.v2": "75dc83534947f323ec2c5ef82e3b8fd0a30f3aedbc25b275c6fb595ff2d3b427",
    _PREFIX + "alphabet-profile.v2": "bf392cc8d623d78ed3eef029132335fa1d974cbfa42c1294bedd2e899aade9b2",
    _PREFIX + "phase-quotient-profile.v2": "ddff1f79ec58bd2a093038a70d01483341988cc8d5c829e6fd8939fd6fecc95c",
    _PREFIX + "fourier-profile.v2": "8da803c2ceffb48c1cf5590e9c6334ed37d2760383231ee71b7720db99c46317",
    _PREFIX + "unary-convolution-profile.v2": "f39b6bf089f400ef9677502a4b5caee5173071136795abb0c113ef747bf0f542",
    _PREFIX + "bounded-ad5-profile.v2": "f425e93832a5b366a0c8e6e4b1618c184a9ec8b1f6aa874e98a9b0d87326bcb8",
    _PREFIX + "bounded-ad5-context-batch-profile.v2": "49835bb6dc187a1cabfae48e4a9aa2c7afbe3f9334799d518e5a94a7a0f2d873",
    _PREFIX_CENSUS_SCHEMA: "619ea2102daed880ac662d6b119e8b324e5cafffb06ec9793213230696e48b24",
    _SIGNATURE_SCHEMA: "4b4eb7eac2fd0f7d4b19e91e424492e9f4e405b0c5872df1217f1c9574f871d8",
    _CENTRAL_KEY_SCHEMA: "7a3896a48b8e0c1de959856c7f07740df956d63cc568321f4a2052e28c27f403",
    _ORBIT_SCHEMA: "8c526e2208aaa8007b63741cbd9f5c4d9f05c3d412033050bfd921df2f84f32e",
    _SLICE_SCHEMA: "32b669dd063b82e62fd3d11a62ea3543d2b7d0683666f0c2601f688576912318",
    _JOIN_SCHEMA: "c9b5beeb78f48c3f05ef7bab2876c1c7ed5d88555c0084110d0299457cf58fb8",
    _REPRESENTATIVE_SCHEMA: "2714b93ca2b26587ff4dc1b08d38728892166bbc8f5c2864f75194eb6649c9f9",
    _REPRESENTATIVE_9_12_SCHEMA: "bd0bfbe69db2eeb922529526e3c4c64742e7e62b272cdf3e910123e58864f944",
    _SWEEP_SCHEMA: "af8541f6d8b8d7ba054af7c80a683048b2f85d59c14179c99e839ce1ed4ba525",
}
_COMPILER_PROVENANCE = (
    "compiler",
    "compiler_sha256",
    "compiler_version",
    "binary_sha256",
    "transcript",
)


def _sweep_bundle_projection(bundle: dict[str, Any]) -> dict[str, Any]:
    """Retain paired mathematics while replacing runtime-derived hash links."""
    body = dict(bundle)
    prior = body["prior_profile"]
    body["prior_profile"] = {"schema": prior["schema"], "evidence_sha256": profile_evidence_sha256(prior)}
    for name in ("primary_engine_receipt", "sorted_engine_receipt"):
        engine = dict(body[name])
        del engine["attempt_id"]
        del engine["canonical_payload_sha256"]
        identity = dict(engine["engine"])
        del identity["engine_executable_sha256"]
        del identity["runtime_identity_sha256"]
        engine["engine"] = identity
        limits = dict(engine["limits"])
        del limits["wall_budget_identity"]
        engine["limits"] = limits
        resources = dict(engine["resources"])
        del resources["vm_peak_bytes"]
        del resources["wall_seconds"]
        engine["resources"] = resources
        body[name] = engine
    pair = dict(body["paired_subreceipt"])
    del pair["canonical_payload_sha256"]
    for key, name in (
        ("primary_engine_receipt_sha256", "primary_engine_receipt"),
        ("sorted_engine_receipt_sha256", "sorted_engine_receipt"),
    ):
        pair[key] = hashlib.sha256(canonical_json(body[name]).encode()).hexdigest()
    reference = dict(pair["prior"])
    del reference["accepted_receipt_sha256"]
    pair["prior"] = reference
    aggregate = dict(pair["aggregate"])
    del aggregate["primary_vm_peak_bytes"]
    del aggregate["sorted_vm_peak_bytes"]
    pair["aggregate"] = aggregate
    body["paired_subreceipt"] = pair
    return body


def profile_evidence_sha256(profile: dict[str, Any]) -> str:
    """Hash all profile claims, excluding only schema-declared runtime evidence.

    Raw receipts retain compiler/binary identities, paths, and full transcripts.
    Mathematical evidence retains source and transcript identities, every parsed
    mathematical field, limits, checks, independent replay, and declared scope.
    Telemetry-bearing AD5 profiles bind the mathematical transcript instead of their
    measured memory line; memory still has a separate certificate cap check.
    """
    schema = profile.get("schema")
    if not isinstance(schema, str) or schema not in _SCHEMAS:
        raise ScientificToolError("unsupported mathematical evidence profile")
    body = {key: value for key, value in profile.items() if key != "evidence_sha256"}
    try:
        computation = dict(body["computation"]) if schema not in _PYTHON_ONLY_SCHEMAS else None
        if computation is not None:
            for name in _COMPILER_PROVENANCE:
                del computation[name]
        for prerequisite in _SCHEMAS[schema]:
            del body[prerequisite + "_receipt_sha256"]
            evidence = body[prerequisite + "_evidence_sha256"]
            if not isinstance(evidence, str) or len(evidence) != 64 or any(c not in "0123456789abcdef" for c in evidence):
                raise ScientificToolError("invalid prerequisite mathematical evidence identity")
        for prerequisite in _NESTED_PREREQUISITES.get(schema, ()):
            child = dict(body[prerequisite])
            del child["receipt_sha256"]
            evidence = child["evidence_sha256"]
            if not isinstance(evidence, str) or len(evidence) != 64 or any(c not in "0123456789abcdef" for c in evidence):
                raise ScientificToolError("invalid prerequisite mathematical evidence identity")
            body[prerequisite] = child
        if schema in _MEMORY_TELEMETRY_SCHEMAS:
            del computation["vm_peak_kib"]
            del computation["transcript_sha256"]
            if "mathematical_transcript_sha256" not in computation:
                raise ScientificToolError("missing bounded AD5 mathematical transcript identity")
        if schema == _SWEEP_SCHEMA:
            del computation["wall_seconds"]
            body["bundle"] = _sweep_bundle_projection(body["bundle"])
        if schema in _INDEPENDENT_REPLAYS:
            for name in _INDEPENDENT_REPLAYS[schema]:
                if body[name] is None and name == "independent_witness_replay":
                    continue
                independent = dict(body[name])
                del independent["interpreter"]
                del independent["transcript"]
                body[name] = independent
        if schema in _COMPILED_REPLAYS:
            for name in _COMPILED_REPLAYS[schema]:
                independent = dict(body[name])
                for field in _COMPILER_PROVENANCE:
                    del independent[field]
                del independent["vm_peak_kib"]
                del independent["transcript_sha256"]
                if schema == _SWEEP_SCHEMA:
                    del independent["wall_seconds"]
                if "mathematical_transcript_sha256" not in independent:
                    raise ScientificToolError("missing compiled replay mathematical transcript identity")
                body[name] = independent
        if schema == _ROW0_PAIR_SCHEMA:
            independent = dict(body["independent_compiled_replay"])
            for field in _COMPILER_PROVENANCE:
                del independent[field]
            body["independent_compiled_replay"] = independent
    except (KeyError, TypeError, ValueError) as error:
        raise ScientificToolError("incomplete mathematical evidence profile") from error
    if computation is not None:
        body["computation"] = computation
    envelope = {"schema": "reasoning.science-mathematical-evidence.v1", "profile": body}
    return hashlib.sha256(canonical_json(envelope).encode()).hexdigest()


def seal_profile_evidence(profile: dict[str, Any]) -> dict[str, Any]:
    """Attach evidence identity without changing or discarding raw provenance."""
    if "evidence_sha256" in profile:
        raise ScientificToolError("mathematical evidence profile is already sealed")
    return {**profile, "evidence_sha256": profile_evidence_sha256(profile)}


def reviewed_profile_matches(profile: dict[str, Any], expected: str) -> bool:
    """Require fresh identity and a successful certificate, not a copied seal."""
    return profile.get("certificate_verified") is True and profile.get("evidence_sha256") == expected == profile_evidence_sha256(profile)


def certified_receipt_json(profile: dict[str, Any]) -> str:
    """Refuse a certified artifact unless the complete mathematical scope is reviewed."""
    schema = profile.get("schema")
    expected = _REVIEWED_PROFILES.get(schema) if isinstance(schema, str) else None
    if expected is None or not reviewed_profile_matches(profile, expected):
        raise ScientificToolError("scientific certificate or reviewed mathematical identity does not verify; report not written")
    if schema in _ROW0_SCHEMAS and not _row0_raw_evidence_matches(profile):
        raise ScientificToolError("row-zero raw execution evidence does not verify; report not written")
    if schema in _COVERAGE_NATIVE_SCHEMAS and not _coverage_raw_evidence_matches(profile):
        raise ScientificToolError("singleton/coverage raw execution evidence does not verify; report not written")
    if schema in _ORIGINAL_SUPPORT_SCHEMAS and not _original_support_raw_evidence_matches(profile):
        raise ScientificToolError("original support raw execution evidence does not verify; report not written")
    return canonical_json(profile)


def _original_support_raw_evidence_matches(profile: dict[str, Any]) -> bool:
    """Bind complete native bodies even though their digests already identify them."""
    from . import heisenberg_level25_alphabet as alphabet
    from . import heisenberg_level25_three_block as nonconsecutive
    from . import heisenberg_level25_three_block_consecutive as consecutive
    from . import heisenberg_level25_two_block as two_block

    modules = {
        _ORIGINAL_ALPHABET_SCHEMA: alphabet,
        _ORIGINAL_TWO_BLOCK_SCHEMA: two_block,
        _ORIGINAL_NONCONSECUTIVE_SCHEMA: nonconsecutive,
        _ORIGINAL_CONSECUTIVE_SCHEMA: consecutive,
    }
    module = modules[profile["schema"]]
    try:
        return exact_execution_matches(
            profile["computation"],
            parser=module._parse_transcript,
            expected_program=module._REVIEWED_PROGRAM_SHA256,
            expected_transcript=module._REVIEWED_TRANSCRIPT_SHA256,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ScientificToolError("incomplete original support raw evidence; report not written") from error


def _coverage_raw_evidence_matches(profile: dict[str, Any]) -> bool:
    """Recheck excluded native transcript bodies before publishing their receipts."""
    from . import heisenberg_level25_singleton_orbits as singleton
    from . import heisenberg_level25_three_block_coverage as coverage

    bindings = {
        _SINGLETON_SCHEMA: (singleton._parse_transcript, singleton._REVIEWED_PROGRAM_SHA256, singleton._REVIEWED_TRANSCRIPT_SHA256),
        _COVERAGE_SCHEMA: (coverage._parse_transcript, coverage._REVIEWED_PROGRAM_SHA256, coverage._REVIEWED_TRANSCRIPT_SHA256),
        _THREE_BLOCK_SCHEMA: (
            coverage._parse_twelve_row_transcript,
            coverage._TWELVE_ROW_REVIEWED_PROGRAM_SHA256,
            coverage._TWELVE_ROW_REVIEWED_TRANSCRIPT_SHA256,
        ),
    }
    parser, program, transcript = bindings[profile["schema"]]
    try:
        return exact_execution_matches(profile["computation"], parser=parser, expected_program=program, expected_transcript=transcript)
    except (KeyError, TypeError, ValueError) as error:
        raise ScientificToolError("incomplete singleton/coverage raw evidence; report not written") from error


def _row0_raw_evidence_matches(profile: dict[str, Any]) -> bool:
    """Recheck excluded duplicate bodies before publishing a row-zero receipt."""
    from . import heisenberg_level25_four_block_row0 as feasibility
    from . import heisenberg_level25_four_block_row0_edge_indicator as edge
    from . import heisenberg_level25_four_block_row0_kernel as kernel
    from . import heisenberg_level25_four_block_row0_pair as pair
    from . import heisenberg_level25_four_block_row0_pair_reuse as reuse
    from . import heisenberg_level25_four_block_row0_rank as rank

    modules = {
        _PREFIX + "row0-feasibility-profile.v2": feasibility,
        _PREFIX + "row0-label-unary-rank-profile.v2": rank,
        _PREFIX + "row0-kernel-rank-profile.v2": kernel,
        _ROW0_PAIR_SCHEMA: pair,
        _PREFIX + "row0-pair-reuse-profile.v2": reuse,
        _PREFIX + "row0-edge-indicator-rank-profile.v2": edge,
    }
    module = modules[profile["schema"]]
    try:
        primary_matches = exact_execution_matches(
            profile["computation"],
            parser=module._parse_transcript,
            expected_program=module._REVIEWED_PROGRAM_SHA256,
            expected_transcript=module._REVIEWED_TRANSCRIPT_SHA256,
        )
        if module is pair:
            independent_matches = exact_execution_matches(
                profile["independent_compiled_replay"],
                parser=module._parse_transcript,
                expected_program=module._REVIEWED_REPLAY_PROGRAM_SHA256,
                expected_transcript=module._REVIEWED_REPLAY_TRANSCRIPT_SHA256,
            )
        elif module in (kernel, reuse, edge):
            independent_matches = independent_replay_matches(
                profile["independent_python_replay"],
                expected_program=module._REVIEWED_REPLAY_PROGRAM_SHA256,
                expected_transcript=module._REVIEWED_REPLAY_TRANSCRIPT_SHA256,
            )
        else:
            independent_matches = True  # In-process result dictionaries are retained in full mathematical identity.
        return primary_matches and independent_matches
    except (KeyError, TypeError, ValueError) as error:
        raise ScientificToolError("incomplete row-zero raw execution evidence; report not written") from error


def bounded_transcript_sha256(stdout: str, *, parser: Callable[[str], dict[str, Any]]) -> str:
    """Bind exact validated output except its single canonical memory measurement."""
    parsed = parser(stdout)
    if not stdout.endswith("REASONING_END\n") or "\r" in stdout:
        raise ScientificToolError("bounded transcript does not preserve exact LF framing")
    lines = stdout.splitlines(keepends=True)
    metric = f"vm_peak_kib={parsed['vm_peak_kib']}\n"
    if lines.count(metric) != 1:
        raise ScientificToolError("bounded transcript has noncanonical memory telemetry")
    return hashlib.sha256("".join(line for line in lines if line != metric).encode()).hexdigest()


def bounded_execution_matches(
    computation: dict[str, Any],
    *,
    parser: Callable[[str], dict[str, Any]],
    expected_program: str,
    expected_mathematics: str,
) -> bool:
    """Bind source, raw bytes, deterministic transcript, and all parsed fields."""
    transcript = computation["transcript"]
    parsed = parser(transcript)
    mathematical_sha256 = bounded_transcript_sha256(transcript, parser=parser)
    return (
        computation["program_sha256"] == expected_program
        and computation["transcript_sha256"] == hashlib.sha256(transcript.encode()).hexdigest()
        and computation["mathematical_transcript_sha256"] == mathematical_sha256 == expected_mathematics
        and all(computation[name] == value for name, value in parsed.items())
    )


def exact_execution_matches(
    computation: dict[str, Any],
    *,
    parser: Callable[[str], dict[str, Any]],
    expected_program: str,
    expected_transcript: str,
) -> bool:
    """Bind a fully deterministic primary transcript, with no omitted telemetry."""
    transcript = computation["transcript"]
    parsed = parser(transcript)
    return (
        computation["program_sha256"] == expected_program
        and computation["transcript_sha256"] == hashlib.sha256(transcript.encode()).hexdigest() == expected_transcript
        and all(computation[name] == value for name, value in parsed.items())
    )


def independent_replay_matches(replay: dict[str, Any], *, expected_program: str, expected_transcript: str) -> bool:
    """Verify a deterministic independent JSON replay without trusting its seal."""
    transcript = replay["transcript"]
    parsed = json.loads(transcript)
    return (
        isinstance(parsed, dict)
        and replay["program_sha256"] == expected_program
        and replay["transcript_sha256"] == hashlib.sha256(transcript.encode()).hexdigest() == expected_transcript
        and all(replay[name] == value for name, value in parsed.items())
    )
