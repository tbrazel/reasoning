# ruff: noqa: E501
"""Resumable dual-engine AD5 representative sweep receipts."""

from __future__ import annotations

import hashlib
import math
import re
import time
from collections.abc import Iterable
from functools import partial
from typing import Any

from ..canonical import canonical_json
from .heisenberg_level25_four_block_ad5 import _RESIDENT_BYTE_CAP, _source_manifest
from .heisenberg_level25_four_block_ad5_prefix import _PROGRAM as _PREFIX_PROGRAM
from .heisenberg_level25_four_block_ad5_representative_batch import (
    _parse_program_transcript,
    _parse_replay_transcript,
    _representative_prefix_sha256,
)
from .heisenberg_level25_four_block_ad5_representative_batch_9_12 import (
    _PROGRAM as _PRIOR_PROGRAM,
)
from .heisenberg_level25_four_block_ad5_representative_batch_9_12 import (
    _REPLAY_PROGRAM as _PRIOR_REPLAY_PROGRAM,
)
from .heisenberg_level25_four_block_ad5_representative_batch_9_12 import (
    _REPRESENTATIVE_9_12_EVIDENCE_SHA256,
    level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_batch_9_12_profile,
)
from .heisenberg_level25_three_block import _run_source_hashed_program
from .science_evidence import bounded_execution_matches, bounded_transcript_sha256, reviewed_profile_matches, seal_profile_evidence
from .scientific import ScientificToolError

_CONTEXT_POSITIONS = (13, 14, 15, 16)
_EXPECTED_PREFIX_CODES = (
    280,
    281,
    480,
    489,
    506,
    514,
    752,
    753,
    1202,
    1221,
    1228,
    1246,
    2527,
    2549,
    2553,
    2574,
    2627,
)
_ENGINE_CHARGE_CAP = 270_000_000
_SWEEP_EVIDENCE_SHA256 = "af8541f6d8b8d7ba054af7c80a683048b2f85d59c14179c99e839ce1ed4ba525"
_REPRESENTATIVE_STREAM_SHA256 = "a9d128cc5614b2dbffad31c2fa04aa372f5bbacb4397143e4f74e52f23704dc2"
_DESIGN_ARTIFACT_PATH = "research/four-block/iteration-144/ad5-coordinate3-scalable-representative-join-design-native.md"
_DESIGN_ARTIFACT_SHA256 = "879cb0d97c3bc4affbc698ce118a10dc94630ee0b469cf8682381ad6e8fbd6c5"
_AUDIT_ARTIFACT_PATH = "research/four-block/iteration-144/ad5-coordinate3-scalable-representative-join-design-audit-codex.md"
_AUDIT_ARTIFACT_SHA256 = "941d0eb06e8f18071975a51f527f6c1333b3268b5c4f1e00a92c13c1d8d9eb4f"
_REVIEWED_PROGRAM_SHA256 = "0a4d01d419d9e93330b5d1e1d9e75e5b01754d55b355091608a2c28bdbbd4233"
_REVIEWED_MATHEMATICAL_TRANSCRIPT_SHA256 = "803a6e042658172dfe3a5236b134ab12ee7519c0ba4622fc4013e1cab4fef5f4"
_REVIEWED_REPLAY_PROGRAM_SHA256 = "ebb5488f30aa949e7695d2f798d041a40fcf29d473ad699192e7be27d26aff14"
_REVIEWED_REPLAY_MATHEMATICAL_TRANSCRIPT_SHA256 = "47aaa96db389e010afa3f412ffc3ff316f562a325144f7d88fe8b125b2bcfc1a"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _replace_exact(source: str, old: str, new: str, *, count: int = 1) -> str:
    if source.count(old) != count:
        raise RuntimeError("prior representative sweep source no longer has the expected splice point")
    return source.replace(old, new)


_SHA_START = _PREFIX_PROGRAM.index("struct Sha256{")
_SHA_END = _PREFIX_PROGRAM.index("template<std::size_t Size>std::string dense_digest", _SHA_START)
_SHA256_SUPPORT = (
    "#include <iomanip>\n"
    + _PREFIX_PROGRAM[_SHA_START:_SHA_END]
    + r"""
void hash_tag(Sha256&hash,const std::string&tag){for(unsigned char value:tag)hash.byte(value);hash.byte(0);}
void hash_u32(Sha256&hash,std::uint32_t value){for(int byte=0;byte<4;++byte)hash.byte((value>>(8*byte))&255);}
void hash_u64(Sha256&hash,u64 value){for(int byte=0;byte<8;++byte)hash.byte((value>>(8*byte))&255);}
struct CanonicalMatch{std::uint32_t cid=0,bid=0;u64 key=0;std::array<int,7>residual{};bool decomposition_failure=false,survivor=false;};
bool canonical_match_less(const CanonicalMatch&left,const CanonicalMatch&right){return left.cid<right.cid||(left.cid==right.cid&&left.bid<right.bid);}
u64 sha_stream_charge(u64 bytes){return bytes+64*((bytes+72)/64);}
void finish_context_hashes(int d_position,std::uint32_t d_code,Sha256&b_hash,Sha256&c_hash,std::vector<CanonicalMatch>&matches,std::string&b_sha,std::string&c_sha,std::string&pairs_sha,std::string&match_sha,std::string&survivors_sha,u64&stream_bytes,u64&stream_charge){
 std::sort(matches.begin(),matches.end(),canonical_match_less);Sha256 pair_hash,match_hash,survivor_hash;hash_tag(pair_hash,"AD5-EQUAL-KEY-PAIRS-v2.1");hash_tag(match_hash,"AD5-MATCH-RESIDUALS-v2.1");hash_tag(survivor_hash,"AD5-RESIDUAL-SURVIVORS-v2.1");
 for(auto*hash:std::array<Sha256*,3>{{&pair_hash,&match_hash,&survivor_hash}}){hash_u32(*hash,d_position);hash_u32(*hash,d_code);}
 for(const auto&item:matches){hash_u32(pair_hash,item.cid);hash_u32(pair_hash,item.bid);hash_u32(match_hash,item.cid);hash_u32(match_hash,item.bid);hash_u64(match_hash,item.key);for(int value:item.residual)hash_u32(match_hash,value);hash_u32(match_hash,item.decomposition_failure);hash_u32(match_hash,item.survivor);if(item.survivor){hash_u32(survivor_hash,item.cid);hash_u32(survivor_hash,item.bid);}}
 u64 survivors=0;for(const auto&item:matches)survivors+=item.survivor;std::array<u64,5>bytes{{u64(std::string("AD5-B-KEYED-RECORDS-v2.1").size()+1+8)+12ULL*NB,u64(std::string("AD5-C-KEYED-RECORDS-v2.1").size()+1+8)+12ULL*NC,u64(std::string("AD5-EQUAL-KEY-PAIRS-v2.1").size()+1+8)+8ULL*matches.size(),u64(std::string("AD5-MATCH-RESIDUALS-v2.1").size()+1+8)+52ULL*matches.size(),u64(std::string("AD5-RESIDUAL-SURVIVORS-v2.1").size()+1+8)+8ULL*survivors}};for(u64 value:bytes){stream_bytes+=value;stream_charge+=sha_stream_charge(value);}
 b_sha=b_hash.finish();c_sha=c_hash.finish();pairs_sha=pair_hash.finish();match_sha=match_hash.finish();survivors_sha=survivor_hash.finish();
}
"""
)


_PROGRAM = _PRIOR_PROGRAM
for _old, _new, _count in (
    ("D_POSITIONS{{9,10,11,12}}", "D_POSITIONS{{13,14,15,16}}", 1),
    ("std::array<u64,65> rep_prefix_orbits", "std::array<u64,85> rep_prefix_orbits", 1),
    ("std::array<u64,65>out", "std::array<u64,85>out", 1),
    ("packed<13", "packed<17", 1),
    ("representative_prefix_check_count=13", "representative_prefix_check_count=17", 1),
    ("SECOND_REPRESENTATIVE_JOIN_BATCH_EMPTY", "THIRD_REPRESENTATIVE_JOIN_BATCH_EMPTY", 2),
):
    _PROGRAM = _replace_exact(_PROGRAM, _old, _new, count=_count)
_PROGRAM = _replace_exact(_PROGRAM, "struct ContextResult{", _SHA256_SUPPORT + "\nstruct ContextResult{")
_PROGRAM = _replace_exact(
    _PROGRAM,
    "u64 decomposition_sample_count=0,decomposition_sample_failure_count=0,b_states_completed=0,c_states_completed=0,occupied=0,max_insert_probe=0,max_lookup_probe=0,matches=0,residual_per_match=0,residual_tests=0,survivors=0,decomposition_failures=0,b_checksum=0,c_checksum=0,c_key_match_checksum=0,match_checksum=0,primary_replay_charge=0;",
    "u64 decomposition_sample_count=0,decomposition_sample_failure_count=0,b_states_completed=0,c_states_completed=0,occupied=0,max_insert_probe=0,max_lookup_probe=0,matches=0,residual_per_match=0,residual_tests=0,survivors=0,decomposition_failures=0,b_checksum=0,c_checksum=0,c_key_match_checksum=0,match_checksum=0,primary_replay_charge=0,stream_hash_byte_count=0,stream_hash_charge=0;std::string b_keyed_records_sha256,c_keyed_records_sha256,equal_key_pairs_sha256,match_stream_sha256,residual_survivors_sha256;",
)
_PROGRAM = _replace_exact(
    _PROGRAM,
    "std::vector<u64>keys(SLOTS,EMPTY);",
    'Sha256 b_stream,c_stream;hash_tag(b_stream,"AD5-B-KEYED-RECORDS-v2.1");hash_tag(c_stream,"AD5-C-KEYED-RECORDS-v2.1");hash_u32(b_stream,result.d_position);hash_u32(b_stream,result.d_code);hash_u32(c_stream,result.d_position);hash_u32(c_stream,result.d_code);std::vector<CanonicalMatch>canonical_matches;std::vector<u64>keys(SLOTS,EMPTY);',
)
_PROGRAM = _replace_exact(
    _PROGRAM,
    "mixc(result.b_checksum,state,result.charge);mixc(result.b_checksum,key,result.charge);std::size_t slot=",
    "mixc(result.b_checksum,state,result.charge);mixc(result.b_checksum,key,result.charge);hash_u32(b_stream,state);hash_u64(b_stream,key);std::size_t slot=",
)
_PROGRAM = _replace_exact(
    _PROGRAM,
    "ckeys[state]=key;cslots[state]=slot;result.matches+=matched;",
    "ckeys[state]=key;cslots[state]=slot;hash_u32(c_stream,state);hash_u64(c_stream,key);result.matches+=matched;",
)
_PROGRAM = _replace_exact(
    _PROGRAM,
    "for(int row:std::array<int,7>{{0,5,6,7,8,9,11}})values[vi++]=augmented_value(row,x,result.charge);if(values[1]||values[2]||values[3]||values[4]||values[6]){++result.decomposition_failures;break;}if(values[0]||values[5])continue;++result.survivors;",
    "for(int row:std::array<int,7>{{0,5,6,7,8,9,11}})values[vi++]=augmented_value(row,x,result.charge);bool decomposition_failure=values[1]||values[2]||values[3]||values[4]||values[6],survivor=!decomposition_failure&&!values[0]&&!values[5];canonical_matches.push_back(CanonicalMatch{cid,bid,ckeys[cid],values,decomposition_failure,survivor});if(decomposition_failure){++result.decomposition_failures;break;}if(!survivor)continue;++result.survivors;",
)
_PROGRAM = _replace_exact(
    _PROGRAM,
    'if(result.decomposition_failures)result.status="DECOMPOSITION_MISMATCH";',
    "finish_context_hashes(result.d_position,result.d_code,b_stream,c_stream,canonical_matches,result.b_keyed_records_sha256,result.c_keyed_records_sha256,result.equal_key_pairs_sha256,result.match_stream_sha256,result.residual_survivors_sha256,result.stream_hash_byte_count,result.stream_hash_charge);if(result.decomposition_failures)result.status=\"DECOMPOSITION_MISMATCH\";",
)
_PROGRAM = _replace_exact(
    _PROGRAM,
    '<<prefix<<"match_stream_checksum="<<result.match_checksum<<"\\n"<<prefix<<"primary_replay_charge_total="',
    '<<prefix<<"match_stream_checksum="<<result.match_checksum<<"\\n"<<prefix<<"stream_hash_byte_count="<<result.stream_hash_byte_count<<"\\n"<<prefix<<"stream_hash_charge="<<result.stream_hash_charge<<"\\n"<<prefix<<"b_keyed_records_sha256="<<result.b_keyed_records_sha256<<"\\n"<<prefix<<"c_keyed_records_sha256="<<result.c_keyed_records_sha256<<"\\n"<<prefix<<"equal_key_pairs_sha256="<<result.equal_key_pairs_sha256<<"\\n"<<prefix<<"match_stream_sha256="<<result.match_stream_sha256<<"\\n"<<prefix<<"residual_survivors_sha256="<<result.residual_survivors_sha256<<"\\n"<<prefix<<"primary_replay_charge_total="',
)

_REPLAY_PROGRAM = _PRIOR_REPLAY_PROGRAM
for _old, _new, _count in (
    ("std::array<u64,65> rep_prefix_orbits", "std::array<u64,85> rep_prefix_orbits", 1),
    ("std::array<u64,65>result", "std::array<u64,85>result", 1),
    ("seed<13", "seed<17", 1),
    ("positions{{9,10,11,12}}", "positions{{13,14,15,16}}", 1),
    (
        "std::array<std::uint32_t,13>expected{{280,281,480,489,506,514,752,753,1202,1221,1228,1246,2527}};for(int position=0;position<13",
        "std::array<std::uint32_t,17>expected{{280,281,480,489,506,514,752,753,1202,1221,1228,1246,2527,2549,2553,2574,2627}};for(int position=0;position<17",
        1,
    ),
    ("representative_prefix_check_count=13", "representative_prefix_check_count=17", 1),
    ("SECOND_REPRESENTATIVE_JOIN_BATCH_EMPTY", "THIRD_REPRESENTATIVE_JOIN_BATCH_EMPTY", 1),
):
    _REPLAY_PROGRAM = _replace_exact(_REPLAY_PROGRAM, _old, _new, count=_count)
_REPLAY_PROGRAM = _replace_exact(_REPLAY_PROGRAM, "struct ReplayContext{", _SHA256_SUPPORT + "\nstruct ReplayContext{")
_REPLAY_PROGRAM = _replace_exact(
    _REPLAY_PROGRAM,
    'u64 b_states=0,c_states=0,matches=0,residual_tests=0,survivors=0,decomposition_failures=0,b_checksum=1469598103934665603ULL,c_key_match_checksum=1469598103934665603ULL,match_checksum=1469598103934665603ULL,sort_comparisons=0;std::string status="NOT_STARTED";',
    'u64 b_states=0,c_states=0,matches=0,residual_tests=0,survivors=0,decomposition_failures=0,b_checksum=1469598103934665603ULL,c_key_match_checksum=1469598103934665603ULL,match_checksum=1469598103934665603ULL,sort_comparisons=0,context_charge=0,stream_hash_byte_count=0,stream_hash_charge=0;std::string b_keyed_records_sha256,c_keyed_records_sha256,equal_key_pairs_sha256,match_stream_sha256,residual_survivors_sha256,status="NOT_STARTED";',
)
_REPLAY_PROGRAM = _replace_exact(
    _REPLAY_PROGRAM,
    "Block D{3,1,&d_shape};std::vector<std::pair<u64,std::uint32_t>>records;",
    'Block D{3,1,&d_shape};u64 context_charge_start=charge.total();Sha256 b_stream,c_stream;hash_tag(b_stream,"AD5-B-KEYED-RECORDS-v2.1");hash_tag(c_stream,"AD5-C-KEYED-RECORDS-v2.1");hash_u32(b_stream,result.d_position);hash_u32(b_stream,result.d_code);hash_u32(c_stream,result.d_position);hash_u32(c_stream,result.d_code);std::vector<CanonicalMatch>canonical_matches;std::vector<std::pair<u64,std::uint32_t>>records;',
)
_REPLAY_PROGRAM = _replace_exact(
    _REPLAY_PROGRAM,
    "mixc(result.b_checksum,state,charge);mixc(result.b_checksum,key,charge);records.emplace_back(key,state);",
    "mixc(result.b_checksum,state,charge);mixc(result.b_checksum,key,charge);hash_u32(b_stream,state);hash_u64(b_stream,key);records.emplace_back(key,state);",
)
_REPLAY_PROGRAM = _replace_exact(
    _REPLAY_PROGRAM,
    "u64 matched=upper-lower;result.matches+=matched;",
    "u64 matched=upper-lower;hash_u32(c_stream,state);hash_u64(c_stream,key);result.matches+=matched;",
)
_REPLAY_PROGRAM = _replace_exact(
    _REPLAY_PROGRAM,
    "for(int row:std::array<int,7>{{0,5,6,7,8,9,11}})values[vi++]=augmented_value(row,x,charge);if(values[1]||values[2]||values[3]||values[4]||values[6]){++result.decomposition_failures;break;}if(!values[0]&&!values[5])++result.survivors;",
    "for(int row:std::array<int,7>{{0,5,6,7,8,9,11}})values[vi++]=augmented_value(row,x,charge);bool decomposition_failure=values[1]||values[2]||values[3]||values[4]||values[6],survivor=!decomposition_failure&&!values[0]&&!values[5];canonical_matches.push_back(CanonicalMatch{state,bid,key,values,decomposition_failure,survivor});if(decomposition_failure){++result.decomposition_failures;break;}if(survivor)++result.survivors;",
)
_REPLAY_PROGRAM = _replace_exact(
    _REPLAY_PROGRAM,
    'result.c_states=state+(result.survivors?1:0);result.status=',
    'result.c_states=state+(result.survivors?1:0);finish_context_hashes(result.d_position,result.d_code,b_stream,c_stream,canonical_matches,result.b_keyed_records_sha256,result.c_keyed_records_sha256,result.equal_key_pairs_sha256,result.match_stream_sha256,result.residual_survivors_sha256,result.stream_hash_byte_count,result.stream_hash_charge);result.context_charge=charge.total()-context_charge_start+result.sort_comparisons;result.status=',
)
_REPLAY_PROGRAM = _replace_exact(
    _REPLAY_PROGRAM,
    '<<prefix<<"match_stream_checksum="<<result.match_checksum<<"\\n";',
    '<<prefix<<"match_stream_checksum="<<result.match_checksum<<"\\n"<<prefix<<"stream_hash_byte_count="<<result.stream_hash_byte_count<<"\\n"<<prefix<<"stream_hash_charge="<<result.stream_hash_charge<<"\\n"<<prefix<<"b_keyed_records_sha256="<<result.b_keyed_records_sha256<<"\\n"<<prefix<<"c_keyed_records_sha256="<<result.c_keyed_records_sha256<<"\\n"<<prefix<<"equal_key_pairs_sha256="<<result.equal_key_pairs_sha256<<"\\n"<<prefix<<"match_stream_sha256="<<result.match_stream_sha256<<"\\n"<<prefix<<"residual_survivors_sha256="<<result.residual_survivors_sha256<<"\\n"<<prefix<<"context_charge="<<result.context_charge<<"\\n";',
)

_STREAM_HASH_FIELDS = (
    "b_keyed_records_sha256",
    "c_keyed_records_sha256",
    "equal_key_pairs_sha256",
    "match_stream_sha256",
    "residual_survivors_sha256",
)


def _parse_sweep_transcript(transcript: str, *, replay: bool) -> dict[str, Any]:
    extra_names = {
        *(f"context_{ordinal}_{name}" for ordinal in range(4) for name in _STREAM_HASH_FIELDS),
        *(
            f"context_{ordinal}_{name}"
            for ordinal in range(4)
            for name in ("stream_hash_byte_count", "stream_hash_charge")
        ),
        *((f"context_{ordinal}_context_charge" for ordinal in range(4)) if replay else ()),
    }
    extras: dict[str, str] = {}
    base_lines = []
    for line in transcript.strip().splitlines():
        name = line.split("=", 1)[0]
        if name in extra_names:
            if name in extras or "=" not in line:
                raise ScientificToolError("AD5 sweep transcript has duplicate or malformed stream evidence")
            extras[name] = line.split("=", 1)[1]
        else:
            base_lines.append(line)
    if set(extras) != extra_names:
        raise ScientificToolError("AD5 v2.1 transcript lacks complete stream evidence")
    parsed = (
        _parse_replay_transcript("\n".join(base_lines) + "\n", representative_prefix_check_count=17)
        if replay
        else _parse_program_transcript("\n".join(base_lines) + "\n", representative_prefix_check_count=17)
    )
    for ordinal, context in enumerate(parsed["contexts"]):
        for name in _STREAM_HASH_FIELDS:
            value = extras[f"context_{ordinal}_{name}"]
            if not _HEX64.fullmatch(value):
                raise ScientificToolError("AD5 v2.1 transcript has an invalid stream digest")
            context[name] = value
        try:
            context["stream_hash_byte_count"] = int(extras[f"context_{ordinal}_stream_hash_byte_count"])
            context["stream_hash_charge"] = int(extras[f"context_{ordinal}_stream_hash_charge"])
            if replay:
                context["context_charge"] = int(extras[f"context_{ordinal}_context_charge"])
        except ValueError as error:
            raise ScientificToolError("AD5 sweep transcript has nonintegral stream evidence") from error
    return parsed


def _run_engine(program: str, *, label: str, replay: bool) -> dict[str, Any]:
    started = time.monotonic()
    result = _run_source_hashed_program(
        program,
        label=label,
        timeout_seconds=900,
        runtime_address_space_bytes=_RESIDENT_BYTE_CAP,
        preserve_exact_transcript=True,
    )
    elapsed = max(1, math.ceil(time.monotonic() - started))
    parser = partial(_parse_sweep_transcript, replay=replay)
    return {**result, **parser(result["transcript"]), "wall_seconds": elapsed,
            "mathematical_transcript_sha256": bounded_transcript_sha256(result["transcript"], parser=parser)}


def _canonical_payload(receipt: dict[str, Any]) -> dict[str, Any]:
    payload = dict(receipt)
    payload.pop("canonical_payload_sha256", None)
    return payload


def _seal(receipt: dict[str, Any]) -> dict[str, Any]:
    payload = _canonical_payload(receipt)
    return {
        **payload,
        "canonical_payload_sha256": hashlib.sha256(canonical_json(payload).encode()).hexdigest(),
    }


def _spec_sha256(tag: str, value: Any) -> str:
    return hashlib.sha256(tag.encode() + b"\0" + canonical_json(value).encode()).hexdigest()


def _immutable_source_manifest() -> dict[str, Any]:
    source = _source_manifest()
    five_key_spec = {
        "field_prime": 101,
        "key_order": ["row6_unary_B", "row8_unary_B", "row5_B_plus_BD", "row7_B_plus_BD", "row0_selected_BDA"],
        "encoding": "five F_101 values packed base 101 into uint64",
        "b_state_order": ["j_B", "phase_order=(0,3,1,4,2)", "zero_shape_ordinal"],
        "c_state_order": ["j_C", "zero_shape_ordinal"],
        "d_dependency": "all five keys are recomputed for every D context",
    }
    residual_spec = {
        "direct_rows": [0, 5, 6, 7, 8, 9, 11],
        "decomposition_rows": [5, 6, 7, 8, 11],
        "survivor_rows": [0, 9],
        "all_exact_key_matches_are_tested": True,
    }
    constants = {
        "field_prime": 101,
        "shape_count": 390625,
        "zero_bucket_count": 4420,
        "b_state_count_per_context": 110500,
        "c_state_count_per_context": 22100,
        "engine_charge_cap": _ENGINE_CHARGE_CAP,
        "resident_byte_cap": _RESIDENT_BYTE_CAP,
    }
    shared_code_sha256s = sorted(
        {
            hashlib.sha256(_SHA256_SUPPORT.encode()).hexdigest(),
            source["records_sha256"],
            source["four_block_restricted_records_sha256"],
        }
    )
    return {
        "schema": "reasoning.level25-heisenberg-ad5-representative-sweep-source.v2.2",
        "source_record_count": source["raw_record_count"],
        "source_records_sha256": source["records_sha256"],
        "four_block_restricted_records_sha256": source["four_block_restricted_records_sha256"],
        "representative_stream_sha256": _REPRESENTATIVE_STREAM_SHA256,
        "five_key_spec_sha256": _spec_sha256("AD5-FIVE-KEY-SPEC-v2.1", five_key_spec),
        "residual_spec_sha256": _spec_sha256("AD5-RESIDUAL-SPEC-v2.1", residual_spec),
        "constants_sha256": _spec_sha256("AD5-SWEEP-CONSTANTS-v2.1", constants),
        "shared_code_sha256s": shared_code_sha256s,
        "five_key_spec": five_key_spec,
        "residual_spec": residual_spec,
        "constants": constants,
        "design_artifact": {"path": _DESIGN_ARTIFACT_PATH, "sha256": _DESIGN_ARTIFACT_SHA256},
        "audit_artifact": {"path": _AUDIT_ARTIFACT_PATH, "sha256": _AUDIT_ARTIFACT_SHA256},
        "review_reference_policy": {"kind": "declared_source_literals", "external_contents_verified": False},
    }


def _shape_values(code: int) -> list[int]:
    exponents = [0, 0, 0, 0]
    work = code
    for index in range(3, -1, -1):
        exponents[index] = work % 25
        work //= 25
    return [1, *(pow(16, exponent, 101) for exponent in exponents)]


def _runtime_identity(computation: dict[str, Any]) -> str:
    return _spec_sha256(
        "AD5-RUNTIME-IDENTITY-v2.1",
        {
            "compiler": computation["compiler"],
            "compiler_sha256": computation["compiler_sha256"],
            "compiler_version": computation["compiler_version"],
            "protocol_version": computation["protocol_version"],
            "runtime_address_space_limit_bytes": computation["runtime_address_space_limit_bytes"],
        },
    )


def _engine_receipt(
    computation: dict[str, Any],
    *,
    algorithm_id: str,
    source_manifest: dict[str, Any],
    source_manifest_sha256: str,
    replay: bool,
) -> dict[str, Any]:
    contexts = []
    for context in computation["contexts"]:
        contexts.append(
            {
                "ordinal": context["d_position"],
                "packed_representative": context["packed_representative"],
                "d_position": context["d_position"],
                "d_code": context["d_code"],
                "d_values": _shape_values(context["d_code"]),
                "b_states_started": context["b_states_completed"],
                "b_states_completed": context["b_states_completed"],
                "c_states_started": context["c_states_completed"],
                "c_states_completed": context["c_states_completed"],
                **{name: context[name] for name in _STREAM_HASH_FIELDS},
                "exact_key_match_count": context["exact_key_match_count"],
                "residual_tests_completed": context["residual_tests_completed"],
                "residual_survivor_count": context["residual_survivor_count"],
                "decomposition_failure_count": context["decomposition_failure_count"],
                "algebraic_and_join_charge": (
                    context["context_charge"] if replay else context["charge_total"]
                ),
                "stream_hash_byte_count": context["stream_hash_byte_count"],
                "stream_hash_charge": context["stream_hash_charge"],
                "context_charge": (
                    context["context_charge"] if replay else context["charge_total"]
                )
                + context["stream_hash_charge"],
                "status": (
                    "CONTEXT_EXHAUSTED_EMPTY"
                    if context["status"] == "CONTEXT_EMPTY"
                    else "CONTEXT_SURVIVOR_FOUND"
                    if context["residual_survivor_count"]
                    else "CONTEXT_INTERNAL_ERROR"
                ),
            }
        )
    algebraic_charge_total = (
        computation["charged_operations"] if replay else computation["aggregate_charge_total"]
    )
    stream_hash_charge_total = sum(
        context["stream_hash_charge"] for context in computation["contexts"]
    )
    charge_total = algebraic_charge_total + stream_hash_charge_total
    certificate = {
        "source_transcript_and_every_cached_field_are_reviewed": bounded_execution_matches(
            computation, parser=partial(_parse_sweep_transcript, replay=replay),
            expected_program=_REVIEWED_REPLAY_PROGRAM_SHA256 if replay else _REVIEWED_PROGRAM_SHA256,
            expected_mathematics=_REVIEWED_REPLAY_MATHEMATICAL_TRANSCRIPT_SHA256 if replay else _REVIEWED_MATHEMATICAL_TRANSCRIPT_SHA256,
        ),
        "memory_and_observed_runtime_limits_respect_cap": 0 < computation["vm_peak_kib"] * 1024 < _RESIDENT_BYTE_CAP
        and computation["rlimit_as_soft"] == computation["rlimit_as_hard"] == computation["runtime_address_space_limit_bytes"] == _RESIDENT_BYTE_CAP,
        "wall_time_respects_bounded_execution": 0 < computation["wall_seconds"] <= 900,
        "combined_algebraic_and_hash_charge_respects_cap": 0 < charge_total < _ENGINE_CHARGE_CAP,
    }
    runtime_identity = _runtime_identity(computation)
    engine = {
        "schema": "reasoning.level25-heisenberg-ad5-representative-join-engine.v2.2",
        "receipt_kind": "ENGINE_SUBRECEIPT",
        "attempt_id": _spec_sha256(
            "AD5-ENGINE-ATTEMPT-v2.1",
            {
                "source_manifest_sha256": source_manifest_sha256,
                "algorithm_id": algorithm_id,
                "program_sha256": computation["program_sha256"],
                "transcript_sha256": computation["transcript_sha256"],
                "cursor": [13, 17],
            },
        ),
        "engine": {
            "algorithm_id": algorithm_id,
            "engine_source_sha256": computation["program_sha256"],
            "engine_executable_sha256": computation["binary_sha256"],
            "shared_code_sha256s": source_manifest["shared_code_sha256s"],
            "runtime_identity_sha256": runtime_identity,
        },
        "source": {
            "design_artifact_path": _DESIGN_ARTIFACT_PATH,
            "design_artifact_sha256": _DESIGN_ARTIFACT_SHA256,
            "audit_artifact_path": _AUDIT_ARTIFACT_PATH,
            "audit_artifact_sha256": _AUDIT_ARTIFACT_SHA256,
            "source_manifest_sha256": source_manifest_sha256,
            "source_records_sha256": source_manifest["source_records_sha256"],
            "source_record_count": source_manifest["source_record_count"],
            "representative_stream_sha256": source_manifest["representative_stream_sha256"],
            "accepted_representative_prefix_length": 13,
            "derived_representative_prefix_length": computation["representative_prefix_check_count"],
            "derived_representative_prefix_sha256": _representative_prefix_sha256(computation["representative_prefix_orbits"]),
            "field_prime": 101,
            "shape_count": 390625,
            "zero_bucket_count": computation.get("zero_bucket_count", 4420),
            "zero_bucket_checksum": str(computation.get("zero_bucket_checksum", 3700178448759310635)),
            "a_shape_code": 280,
            "five_key_spec_sha256": source_manifest["five_key_spec_sha256"],
            "residual_spec_sha256": source_manifest["residual_spec_sha256"],
        },
        "cursor": {"start_ordinal": 13, "end_ordinal_exclusive": 17, "requested_context_count": 4},
        "limits": {
            "charge_cap": _ENGINE_CHARGE_CAP,
            "rlimit_as_soft_bytes": _RESIDENT_BYTE_CAP,
            "rlimit_as_hard_bytes": _RESIDENT_BYTE_CAP,
            "wall_budget_identity": f"{runtime_identity}:initial-calibration",
            "wall_budget_seconds": 900,
        },
        "contexts": contexts,
        "certificate": certificate,
        "resources": {
            "contexts_started": computation.get("contexts_started", len(contexts)),
            "contexts_exhausted": computation.get("contexts_exhausted", len(contexts)),
            "charge_total": charge_total,
            "algebraic_and_join_charge_total": algebraic_charge_total,
            "stream_hash_charge_total": stream_hash_charge_total,
            "vm_peak_bytes": computation["vm_peak_kib"] * 1024,
            "wall_seconds": computation["wall_seconds"],
            "tripped_limit": None,
        },
        "terminal_status": (
            "ENGINE_EXHAUSTED_EMPTY"
            if len(contexts) == 4 and all(context["status"] == "CONTEXT_EXHAUSTED_EMPTY" for context in contexts)
            else "ENGINE_SURVIVOR_FOUND"
            if any(context["status"] == "CONTEXT_SURVIVOR_FOUND" for context in contexts)
            else "ENGINE_INTERNAL_ERROR"
        ),
    }
    return _seal(engine)


def _context_projection(context: dict[str, Any], *fields: str) -> list[Any]:
    return [context[field] for field in fields]


def _pair_receipt(primary: dict[str, Any], sorted_replay: dict[str, Any], source_manifest_sha256: str, *, prior_profile: dict[str, Any]) -> dict[str, Any]:
    pc = primary["contexts"]
    sc = sorted_replay["contexts"]
    comparison = {
        "context_ordinals_equal": [context["ordinal"] for context in pc]
        == [context["ordinal"] for context in sc],
        "packed_representatives_equal": [context["packed_representative"] for context in pc] == [context["packed_representative"] for context in sc],
        "state_counts_equal": [
            _context_projection(context, "b_states_completed", "c_states_completed") for context in pc
        ]
        == [_context_projection(context, "b_states_completed", "c_states_completed") for context in sc],
        "keyed_record_multisets_equal": [
            _context_projection(context, "b_keyed_records_sha256", "c_keyed_records_sha256") for context in pc
        ]
        == [_context_projection(context, "b_keyed_records_sha256", "c_keyed_records_sha256") for context in sc],
        "exact_key_match_counts_equal": [context["exact_key_match_count"] for context in pc] == [context["exact_key_match_count"] for context in sc],
        "match_streams_equal_after_canonical_sort": [
            _context_projection(context, "equal_key_pairs_sha256", "match_stream_sha256") for context in pc
        ]
        == [_context_projection(context, "equal_key_pairs_sha256", "match_stream_sha256") for context in sc],
        "residual_test_counts_equal": [context["residual_tests_completed"] for context in pc] == [context["residual_tests_completed"] for context in sc],
        "residual_survivor_sets_equal": [
            _context_projection(context, "residual_survivor_count", "residual_survivors_sha256") for context in pc
        ]
        == [_context_projection(context, "residual_survivor_count", "residual_survivors_sha256") for context in sc],
        "decomposition_failure_counts_equal": [context["decomposition_failure_count"] for context in pc] == [context["decomposition_failure_count"] for context in sc],
    }
    prior_approved = reviewed_profile_matches(prior_profile, _REPRESENTATIVE_9_12_EVIDENCE_SHA256)
    accepted = (
        prior_approved
        and all(primary["certificate"].values())
        and all(sorted_replay["certificate"].values())
        and primary["engine"]["engine_source_sha256"] != sorted_replay["engine"]["engine_source_sha256"]
        and primary["engine"]["engine_executable_sha256"] != sorted_replay["engine"]["engine_executable_sha256"]
        and primary["terminal_status"] == sorted_replay["terminal_status"] == "ENGINE_EXHAUSTED_EMPTY"
        and all(comparison.values())
        and [context["ordinal"] for context in pc] == list(range(13, 17))
        and all(
            context["b_states_completed"] == 110500
            and context["c_states_completed"] == 22100
            and context["residual_tests_completed"] == context["exact_key_match_count"]
            and context["residual_survivor_count"] == 0
            and context["decomposition_failure_count"] == 0
            for context in (*pc, *sc)
        )
        and primary["resources"]["charge_total"] < _ENGINE_CHARGE_CAP
        and sorted_replay["resources"]["charge_total"] < _ENGINE_CHARGE_CAP
        and primary["resources"]["vm_peak_bytes"] < _RESIDENT_BYTE_CAP
        and sorted_replay["resources"]["vm_peak_bytes"] < _RESIDENT_BYTE_CAP
        and primary["resources"]["tripped_limit"] is None
        and sorted_replay["resources"]["tripped_limit"] is None
    )
    terminal_status = (
        "ACCEPTED_EMPTY"
        if accepted
        else "REJECTED_PRIOR_EVIDENCE"
        if not prior_approved
        else "WITNESS_PENDING"
        if "ENGINE_SURVIVOR_FOUND" in {primary["terminal_status"], sorted_replay["terminal_status"]}
        else "RESOURCE_BLOCKED"
        if "ENGINE_RESOURCE_BLOCKED" in {primary["terminal_status"], sorted_replay["terminal_status"]}
        else "REJECTED_INTERNAL_ERROR"
        if "ENGINE_INTERNAL_ERROR" in {primary["terminal_status"], sorted_replay["terminal_status"]}
        else "REJECTED_ENGINE_MISMATCH"
    )
    pair = {
        "schema": "reasoning.level25-heisenberg-ad5-representative-join-pair.v2.2",
        "receipt_kind": "PAIRED_SUBRECEIPT",
        "sweep_id": _spec_sha256("AD5-REPRESENTATIVE-SWEEP-v2.1", source_manifest_sha256),
        "source_manifest_sha256": source_manifest_sha256,
        "prior": {
            "kind": "profile_reference",
            "accepted_receipt_sha256": hashlib.sha256(canonical_json(prior_profile).encode()).hexdigest(),
            "accepted_evidence_sha256": prior_profile["evidence_sha256"],
            "reviewed_profile_verified": prior_approved,
            "next_ordinal": 13,
        },
        "cursor": {"start_ordinal": 13, "end_ordinal_exclusive": 17, "context_count": 4},
        "primary_engine_receipt_sha256": primary["canonical_payload_sha256"],
        "sorted_engine_receipt_sha256": sorted_replay["canonical_payload_sha256"],
        "comparison": comparison,
        "aggregate": {
            "primary_charge_total": primary["resources"]["charge_total"],
            "sorted_charge_total": sorted_replay["resources"]["charge_total"],
            "combined_charge_total": primary["resources"]["charge_total"] + sorted_replay["resources"]["charge_total"],
            "primary_vm_peak_bytes": primary["resources"]["vm_peak_bytes"],
            "sorted_vm_peak_bytes": sorted_replay["resources"]["vm_peak_bytes"],
            "context_count": len(pc),
            "primary_b_states_completed": sum(context["b_states_completed"] for context in pc),
            "sorted_b_states_completed": sum(context["b_states_completed"] for context in sc),
            "primary_c_states_completed": sum(context["c_states_completed"] for context in pc),
            "sorted_c_states_completed": sum(context["c_states_completed"] for context in sc),
            "exact_key_match_count": sum(context["exact_key_match_count"] for context in pc),
            "residual_tests_completed": sum(context["residual_tests_completed"] for context in pc),
        },
        "terminal_status": terminal_status,
        "next_ordinal_if_accepted": 17 if accepted else 13,
    }
    return _seal(pair)


def _validate_hashes(value: Any, *, path: str = "receipt") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key.endswith("sha256") and (not isinstance(item, str) or not _HEX64.fullmatch(item)):
                raise ValueError(f"{path}.{key} is not a complete lowercase SHA-256")
            _validate_hashes(item, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            if path.endswith("shared_code_sha256s") and (not isinstance(item, str) or not _HEX64.fullmatch(item)):
                raise ValueError(f"{path}[{index}] is not a complete lowercase SHA-256")
            _validate_hashes(item, path=f"{path}[{index}]")


def validate_representative_bundle(bundle: dict[str, Any]) -> None:
    """Validate structure and raw links; this alone is not reviewed approval."""
    if set(bundle) != {"source_manifest", "primary_engine_receipt", "sorted_engine_receipt", "paired_subreceipt", "prior_profile"}:
        raise ValueError("AD5 sweep bundle has unexpected members")
    _validate_hashes(bundle)
    manifest = bundle["source_manifest"]
    manifest_sha = hashlib.sha256(canonical_json(manifest).encode()).hexdigest()
    primary = bundle["primary_engine_receipt"]
    replay = bundle["sorted_engine_receipt"]
    pair = bundle["paired_subreceipt"]
    for label, receipt in (("primary", primary), ("sorted", replay), ("paired", pair)):
        actual = hashlib.sha256(canonical_json(_canonical_payload(receipt)).encode()).hexdigest()
        if actual != receipt.get("canonical_payload_sha256"):
            raise ValueError(f"{label} receipt canonical hash is invalid")
    if not (
        primary["source"]["source_manifest_sha256"]
        == replay["source"]["source_manifest_sha256"]
        == manifest_sha
    ):
        raise ValueError("engine source manifests disagree")
    if pair["source_manifest_sha256"] != manifest_sha:
        raise ValueError("paired receipt source manifest disagrees")
    if pair["primary_engine_receipt_sha256"] != primary["canonical_payload_sha256"] or pair["sorted_engine_receipt_sha256"] != replay["canonical_payload_sha256"]:
        raise ValueError("paired receipt does not bind both engines")
    prior = bundle["prior_profile"]
    if pair["prior"]["accepted_receipt_sha256"] != hashlib.sha256(canonical_json(prior).encode()).hexdigest():
        raise ValueError("paired receipt does not bind its actual prior profile")
    if pair["prior"]["accepted_evidence_sha256"] != prior["evidence_sha256"]:
        raise ValueError("paired receipt prior mathematical identity disagrees")
    if pair["cursor"] != {"start_ordinal": 13, "end_ordinal_exclusive": 17, "context_count": 4}:
        raise ValueError("first sweep pair has a gap, overlap, or invalid size")
    rebuilt = _pair_receipt(primary, replay, manifest_sha, prior_profile=prior)
    if _canonical_payload(rebuilt) != _canonical_payload(pair):
        raise ValueError("paired receipt comparison or terminal decision is not reproducible")


def build_ad5_representative_macroreceipt(receipts: Iterable[dict[str, Any]]) -> dict[str, Any]:
    items = list(receipts)
    if not items or len(items) > 16:
        raise ValueError("an AD5 macroreceipt requires one through sixteen subreceipts")
    pairs = [item.get("paired_subreceipt", item) for item in items]
    for pair in pairs:
        _validate_hashes(pair)
        actual = hashlib.sha256(canonical_json(_canonical_payload(pair)).encode()).hexdigest()
        if actual != pair.get("canonical_payload_sha256"):
            raise ValueError("macroreceipt contains an invalid paired receipt hash")
        cursor = pair.get("cursor", {})
        if (
            cursor.get("end_ordinal_exclusive", 0) - cursor.get("start_ordinal", 0)
            != cursor.get("context_count")
            or cursor.get("context_count") not in {1, 2, 4}
        ):
            raise ValueError("macroreceipt contains an invalid atomic subbatch size")
    if any(pair["terminal_status"] != "ACCEPTED_EMPTY" for pair in pairs):
        raise ValueError("a complete macroreceipt may contain only ACCEPTED_EMPTY pairs")
    for prior, current in zip(pairs, pairs[1:], strict=False):
        if current["sweep_id"] != prior["sweep_id"] or current["source_manifest_sha256"] != prior["source_manifest_sha256"]:
            raise ValueError("macroreceipt sweep identities disagree")
        if current["prior"]["accepted_receipt_sha256"] != prior["canonical_payload_sha256"] or current["cursor"]["start_ordinal"] != prior["next_ordinal_if_accepted"]:
            raise ValueError("macroreceipt subreceipts are not a consecutive receipt chain")
    starts = [pair["cursor"]["start_ordinal"] for pair in pairs]
    if len(starts) != len(set(starts)):
        raise ValueError("macroreceipt repeats a subbatch")
    aggregate_names = (
        "primary_charge_total",
        "sorted_charge_total",
        "combined_charge_total",
        "context_count",
        "primary_b_states_completed",
        "sorted_b_states_completed",
        "primary_c_states_completed",
        "sorted_c_states_completed",
        "exact_key_match_count",
        "residual_tests_completed",
    )
    macro = {
        "schema": "reasoning.level25-heisenberg-ad5-representative-join-macro.v2.2",
        "receipt_kind": "ADMINISTRATIVE_MACRORECEIPT",
        "sweep_id": pairs[0]["sweep_id"],
        "source_manifest_sha256": pairs[0]["source_manifest_sha256"],
        "prior": pairs[0]["prior"],
        "cursor": {
            "start_ordinal": pairs[0]["cursor"]["start_ordinal"],
            "end_ordinal_exclusive": pairs[-1]["cursor"]["end_ordinal_exclusive"],
            "context_count": sum(pair["cursor"]["context_count"] for pair in pairs),
        },
        "paired_subreceipt_sha256s": [pair["canonical_payload_sha256"] for pair in pairs],
        "aggregate": {name: sum(pair["aggregate"][name] for pair in pairs) for name in aggregate_names},
        "peak_resources": {
            "primary_vm_peak_bytes": max(pair["aggregate"]["primary_vm_peak_bytes"] for pair in pairs),
            "sorted_vm_peak_bytes": max(pair["aggregate"]["sorted_vm_peak_bytes"] for pair in pairs),
        },
        "terminal_status": "MACRO_COMPLETE",
        "cursor_advancement_owned_by_macro": False,
        "mathematical_coverage_verified": False,
    }
    return _seal(macro)


def level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_batch_13_16_profile() -> dict[str, Any]:
    """Compute a reviewed, uncommitted pair for ordinals 13 through 16."""

    prior = level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_batch_9_12_profile()
    computation = _run_engine(_PROGRAM, label="ad5-representative-sweep-13-16-primary", replay=False)
    replay = _run_engine(_REPLAY_PROGRAM, label="ad5-representative-sweep-13-16-sorted", replay=True)
    return _profile_from_execution(prior, computation, replay)


def _profile_from_execution(prior: dict[str, Any], computation: dict[str, Any], replay: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct every raw and mathematical claim without running an engine."""
    source_manifest = _immutable_source_manifest()
    source_manifest_sha256 = hashlib.sha256(canonical_json(source_manifest).encode()).hexdigest()
    primary_receipt = _engine_receipt(
        computation,
        algorithm_id="independent_exact_hash_join_v1",
        source_manifest=source_manifest,
        source_manifest_sha256=source_manifest_sha256,
        replay=False,
    )
    sorted_receipt = _engine_receipt(
        replay,
        algorithm_id="independent_sorted_merge_join_v1",
        source_manifest=source_manifest,
        source_manifest_sha256=source_manifest_sha256,
        replay=True,
    )
    pair = _pair_receipt(primary_receipt, sorted_receipt, source_manifest_sha256, prior_profile=prior)
    bundle = {
        "source_manifest": source_manifest,
        "primary_engine_receipt": primary_receipt,
        "sorted_engine_receipt": sorted_receipt,
        "paired_subreceipt": pair,
        "prior_profile": prior,
    }
    validate_representative_bundle(bundle)
    certificate = {
        "prior_batch_is_fresh_reviewed_and_cursor_13_is_bound": reviewed_profile_matches(prior, _REPRESENTATIVE_9_12_EVIDENCE_SHA256)
        and prior["decision"]["accepted_exact"] is True
        and prior["decision"]["public_cursor_start"] == 9
        and prior["decision"]["public_next_unaccepted_packed"] == 13
        and prior["decision"]["packed_representatives_exhausted"] == [9, 10, 11, 12],
        "accepted_prefix_domain_and_source_match_prior_evidence": computation["representative_prefix_orbits"][:65] == prior["computation"]["representative_prefix_orbits"]
        and computation["representative_prefix_orbits"] == replay["representative_prefix_orbits"]
        and all(computation[name] == prior["computation"][name] for name in
                ("zero_bucket_prefix_codes", "shape_count", "zero_bucket_count", "histogram_checksum", "zero_bucket_checksum", "a_shape_code", "a_shape_exponents", "a_shape_values"))
        and source_manifest["source_records_sha256"] == prior["source_manifest"]["records_sha256"]
        and source_manifest["representative_stream_sha256"] == prior["input_manifest"]["slice_representative_sha256"],
        "immutable_source_manifest_is_bound": primary_receipt["source"]["source_manifest_sha256"]
        == sorted_receipt["source"]["source_manifest_sha256"]
        == pair["source_manifest_sha256"]
        == source_manifest_sha256,
        "independent_engine_sources_and_executables_differ": primary_receipt["engine"]["engine_source_sha256"]
        != sorted_receipt["engine"]["engine_source_sha256"]
        and primary_receipt["engine"]["engine_executable_sha256"] != sorted_receipt["engine"]["engine_executable_sha256"],
        "context_bound_cryptographic_streams_agree": pair["comparison"]["keyed_record_multisets_equal"]
        and pair["comparison"]["match_streams_equal_after_canonical_sort"]
        and len({context["b_keyed_records_sha256"] for context in primary_receipt["contexts"]}) == 4
        and len({context["c_keyed_records_sha256"] for context in primary_receipt["contexts"]}) == 4,
        "pair_is_accepted_but_not_self_committing": pair["terminal_status"] == "ACCEPTED_EMPTY"
        and pair["next_ordinal_if_accepted"] == 17,
        "both_engine_sources_outputs_and_resource_limits_are_reviewed": all(primary_receipt["certificate"].values()) and all(sorted_receipt["certificate"].values()),
    }
    verified = all(certificate.values())
    return seal_profile_evidence({
        "schema": "reasoning.level25-heisenberg-ad5-representative-join-batch-13-16.v2.2",
        "request": {},
        "request_sha256": hashlib.sha256(canonical_json({}).encode()).hexdigest(),
        "bundle": bundle,
        "prior_batch_receipt_sha256": hashlib.sha256(canonical_json(prior).encode()).hexdigest(),
        "prior_batch_evidence_sha256": prior["evidence_sha256"],
        "charge_scope": "named primary and sorted replay including stream hashing only; prerequisite executions are separate",
        "computation": computation,
        "independent_sorted_join_replay": replay,
        "certificate": certificate,
        "certificate_verified": verified,
        "decision": {
            "status": pair["terminal_status"],
            "pair_receipt_accepted": verified,
            "chain_commit_required": True,
            "public_cursor_start": 13,
            "public_next_unaccepted_packed": 13,
            "next_ordinal_if_committed": 17 if verified else 13,
            "packed_representatives_exhausted": list(_CONTEXT_POSITIONS) if verified else [],
            "d_codes": [context["d_code"] for context in primary_receipt["contexts"]],
            "exact_key_match_counts": [context["exact_key_match_count"] for context in primary_receipt["contexts"]],
            "residual_survivor_count": sum(context["residual_survivor_count"] for context in primary_receipt["contexts"]),
            "common_zero_found": False,
            "full_contraction_authorized": False,
        },
        "not_certified": [
            "public cursor advancement before a successful chain-tip compare-and-swap",
            "any representative at or after ordinal 17",
            "absence of geometric common zeros over extensions of F_101",
            "a Fourier transform, convolution, tensor contraction, or full contraction",
            "a characteristic-zero lift or global smoothness",
            "a Brauer class, period, index bound, or period-index counterexample",
        ],
        "next_gate": {
            "feature": "level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_batch_13_16_commit",
            "requirement": "Persist this exact reviewed profile and paired bundle, then advance the chain tip from its approved ordinal-13 prior evidence with one compare-and-swap.",
        },
    })


def require_reviewed_sweep_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """Approve a persisted profile only after reproducing all execution claims."""
    try:
        rebuilt = _profile_from_execution(profile["bundle"]["prior_profile"], profile["computation"], profile["independent_sorted_join_replay"])
        if rebuilt != profile or not reviewed_profile_matches(rebuilt, _SWEEP_EVIDENCE_SHA256):
            raise ScientificToolError("AD5 sweep scope or reviewed execution does not verify; report not written")
        return profile["bundle"]
    except (KeyError, TypeError, ValueError) as error:
        raise ScientificToolError("AD5 sweep evidence is incomplete or inconsistent; report not written") from error
