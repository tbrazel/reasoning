# ruff: noqa: E501
"""Exact AD5 coordinate-three central-slice orbit preflight."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from .. import sandbox
from ..canonical import canonical_json
from .heisenberg_level25_four_block_ad5 import _RESIDENT_BYTE_CAP, _source_manifest
from .heisenberg_level25_four_block_ad5_orbit import (
    _ORBIT_EVIDENCE_SHA256,
    _SUPPORT,
    level25_heisenberg_augmented_four_block_ad5_coordinate3_tensor_orbit_profile,
)
from .heisenberg_level25_three_block import _address_space_limited_command, _run_source_hashed_program
from .science_evidence import (
    bounded_execution_matches,
    bounded_transcript_sha256,
    independent_replay_matches,
    reviewed_profile_matches,
    seal_profile_evidence,
)
from .scientific import ScientificToolError

_PROTOCOL_VERSION = 1
_COMBINED_CHARGE_CAP = 300_000_000
_ENGINE_PREFLIGHT_CAP = 150_000_000
_ORBIT_ARTIFACT_PATH = "research/four-block/iteration-128/ad5-coordinate3-tensor-orbit-native.md"
_ORBIT_ARTIFACT_SHA256 = "6e82e5e14d114990c8fa424ece26c9643dcb902d07d8a4d132c14ee81eef2c11"
_ORBIT_RECEIPT_SHA256 = "0ae2c3a469647b3824dd3b18c035603097a1f2b2208a1d3de3d5e8b2f285b41f"
_NATIVE_DESIGN_SHA256 = "ca088f7b4fbc32696fff972e92023eda169519343992246a39c1d3b5b33f4a38"
_CODEX_CORRECTION_SHA256 = "efeaeaeeeb88c986b72e4c0324300579d86215cc1dd7bd17db94ab31b3065901"
_REVIEWED_PROGRAM_SHA256 = "86ecfe0d0a7c6f67c8959df3c1183eeb1b46dc75b0099a8fda6b37654241c9d4"
_REVIEWED_MATHEMATICAL_TRANSCRIPT_SHA256 = "e25dee91c21964e7b13068fd199408782939c52cc615b7856e1ad26796570081"
_SLICE_EVIDENCE_SHA256 = "32b669dd063b82e62fd3d11a62ea3543d2b7d0683666f0c2601f688576912318"
_REVIEWED_REPLAY_PROGRAM_SHA256 = "b44d46fe5a50e5e24f77e71e00c99fee38328327175523ce6f630819e672cbdf"
_REVIEWED_REPLAY_TRANSCRIPT_SHA256 = "930f78193f5484b57a6fe32d5a5f752ac82f17482ea12bf2db7d0c103494e00d"

_PROGRAM = (
    _SUPPORT
    + r"""
#include <array>
#include <cstdint>
#include <vector>

constexpr u64 SLICE_SHAPES=4420ULL,SLICE_COUNT=19536400ULL,REP_COUNT=3907280ULL;
constexpr u64 ACTION_ENTRIES=22100ULL,ACTION_BYTES=48620ULL,BITMAP_BYTES=2442050ULL,REP_BYTES=15629120ULL;
constexpr u64 ENUMERATOR_CHARGE=136776900ULL,SHAPE_GENERATION_CHARGE=5859375ULL;
constexpr u64 GROUP_CHECKS=2762500ULL,COCYCLE_CHECKS=2762500ULL,UNIQUE_B_CHECKS=625ULL,ENCODER_CHECKS=4420ULL;
constexpr u64 SENTINEL_CHARGE=5530045ULL,ENGINE_CHARGE=148166320ULL,ENGINE_CAP=150000000ULL,SLICE_COMBINED_CAP=300000000ULL;
constexpr u64 DECLARED_RESIDENT=18141890ULL,FIXED_BUFFER_CAP=1048576ULL,PEAK_ALLOCATION_CAP=19190466ULL;

using Exp=std::array<std::uint8_t,5>;
using ActionRecord=std::array<std::uint8_t,11>;

struct SliceHash{
 Sha256 hash;u64 bytes=0;
 explicit SliceHash(const std::string&tag){raw(tag);byte(0);}
 void byte(int value){hash.byte(std::uint8_t(value));++bytes;}
 void raw(const std::string&value){for(unsigned char item:value)byte(item);}
 void raw_hex(const std::string&value){for(std::size_t offset=0;offset<value.size();offset+=2)byte(std::stoi(value.substr(offset,2),nullptr,16));}
 void u16le(std::uint16_t value){byte(value&255);byte((value>>8)&255);}
 void u32le(std::uint32_t value){for(int shift=0;shift<=24;shift+=8)byte((value>>shift)&255);}
 void u64le(u64 value){for(int shift=0;shift<=56;shift+=8)byte((value>>shift)&255);}
 std::string finish(){return hash.finish();}
};

Exp decode_exp(std::uint32_t code){Exp out{};for(int q=4;q>=1;--q){out[q]=code%25;code/=25;}return out;}
std::uint32_t encode_exp(const Exp&value){std::uint32_t code=0;for(int q=1;q<5;++q)code=25*code+value[q];return code;}
Exp transform_exp(const Exp&value,int a,int b){Exp out{};for(int q=0;q<5;++q){int item=int(value[(q+a)%5])-value[a]+5*b*q;item%=25;if(item<0)item+=25;out[q]=item;}return out;}
void set_target(ActionRecord&record,int b,std::uint16_t target){record[1+2*b]=target&255;record[2+2*b]=(target>>8)&255;}
std::uint16_t get_target(const ActionRecord&record,int b){return std::uint16_t(record[1+2*b])|(std::uint16_t(record[2+2*b])<<8);}
bool bitmap_test(const std::vector<std::uint8_t>&bitmap,std::uint32_t index){return bitmap[index>>3]&(1u<<(index&7));}
void bitmap_set(std::vector<std::uint8_t>&bitmap,std::uint32_t index){bitmap[index>>3]|=1u<<(index&7);}

int main(){
 struct rlimit limit{};getrlimit(RLIMIT_AS,&limit);if(limit.rlim_cur!=67108864ULL||limit.rlim_max!=67108864ULL)return 70;
 for(int value=0;value<P;++value){POW[value][0]=1;for(int exponent=1;exponent<=25;++exponent)POW[value][exponent]=POW[value][exponent-1]*value%P;}for(int character=0;character<N;++character)for(int index=0;index<N;++index)COEFF[character][index]=coeff(character,index);
 const std::string orbit_artifact="6e82e5e14d114990c8fa424ece26c9643dcb902d07d8a4d132c14ee81eef2c11",orbit_receipt="0ae2c3a469647b3824dd3b18c035603097a1f2b2208a1d3de3d5e8b2f285b41f",native_design="ca088f7b4fbc32696fff972e92023eda169519343992246a39c1d3b5b33f4a38",codex_correction="efeaeaeeeb88c986b72e4c0324300579d86215cc1dd7bd17db94ab31b3065901";

 Charges search;std::vector<Exp>shapes;std::vector<std::uint32_t>codes;std::vector<std::int32_t>index_by_code(390625,-1);shapes.reserve(SLICE_SHAPES);codes.reserve(SLICE_SHAPES);
 SliceHash shape_hash("COORD3-SHAPES-MOD25-v2"),sentinel_hash("COORD3-SENTINELS-v2");u64 encoder_failures=0;
 for(std::uint32_t code=0;code<390625;++code){auto value=decode_shape(code);if(!cyclic_bucket(value,search)){auto exponent=decode_exp(code);auto shape=std::uint32_t(shapes.size());index_by_code[code]=shape;shapes.push_back(exponent);codes.push_back(code);for(auto item:exponent)shape_hash.byte(item);auto encoded=encode_exp(exponent);encoder_failures+=encoded!=code||decode_exp(encoded)!=exponent;sentinel_hash.u32le(shape);sentinel_hash.u32le(code);sentinel_hash.byte(encoded==code);}}

 std::vector<ActionRecord>table(shapes.size());u64 closure_failures=0,ramp_failures=0;SliceHash action_hash("COORD3-A1-ALLB-ACTION-v2");
 for(std::size_t shape=0;shape<shapes.size();++shape){table[shape][0]=shapes[shape][1];for(int b=0;b<5;++b){auto target_word=transform_exp(shapes[shape],1,b);int target=index_by_code[encode_exp(target_word)];if(target<0){++closure_failures;target=0;}ramp_failures+=shapes[target]!=target_word;set_target(table[shape],b,target);}for(auto value:table[shape])action_hash.byte(value);}

 u64 group_checks=0,group_failures=0,cocycle_checks=0,cocycle_failures=0;
 for(std::size_t shape=0;shape<shapes.size();++shape)for(int a=0;a<5;++a)for(int b=0;b<5;++b){auto first=transform_exp(shapes[shape],a,b);for(int c=0;c<5;++c)for(int d=0;d<5;++d){auto composed=transform_exp(first,c,d),direct=transform_exp(shapes[shape],(a+c)%5,(b+d)%5);group_failures+=composed!=direct;++group_checks;int lhs=shapes[shape][(a+c)%5],rhs=(int(shapes[shape][a])+first[c]-5*b*c)%25;if(rhs<0)rhs+=25;cocycle_failures+=lhs!=rhs;++cocycle_checks;sentinel_hash.u32le(shape);sentinel_hash.byte(a);sentinel_hash.byte(b);sentinel_hash.byte(c);sentinel_hash.byte(d);sentinel_hash.u32le(encode_exp(composed));sentinel_hash.u32le(encode_exp(direct));sentinel_hash.byte(lhs);sentinel_hash.byte(rhs);}}

 u64 unique_b_checks=0,unique_b_failures=0;for(int jd=0;jd<5;++jd)for(int ea=0;ea<5;++ea)for(int ed=0;ed<5;++ed){int chosen=2*(-jd-ed+ea)%5;if(chosen<0)chosen+=5;int solutions=0;for(int candidate=0;candidate<5;++candidate){solutions+=(jd+ed-ea+3*candidate)%5==0;++unique_b_checks;}unique_b_failures+=solutions!=1||(jd+ed-ea+3*chosen)%5!=0;sentinel_hash.byte(jd);sentinel_hash.byte(ea);sentinel_hash.byte(ed);sentinel_hash.byte(chosen);sentinel_hash.byte(solutions);}

 std::vector<std::int32_t>().swap(index_by_code);
 std::vector<std::uint8_t>bitmap(BITMAP_BYTES,0);std::vector<std::uint32_t>representatives;representatives.reserve(REP_COUNT);SliceHash edge_hash("COORD3-R1-EDGES-v2");
 u64 visited_bits=0,image_visits=0,collision_failures=0,early_return_failures=0,power5_failures=0;
 for(std::uint32_t packed=0;packed<SLICE_COUNT;++packed){if(bitmap_test(bitmap,packed))continue;representatives.push_back(packed);std::uint32_t current=packed;for(int step=0;step<5;++step){if(bitmap_test(bitmap,current))++collision_failures;else{bitmap_set(bitmap,current);++visited_bits;}std::uint32_t a_shape=current/SLICE_SHAPES,d_shape=current%SLICE_SHAPES;int beta=2*(int(table[a_shape][0]%5)-int(table[d_shape][0]%5))%5;if(beta<0)beta+=5;std::uint32_t next_a=get_target(table[a_shape],beta),next_d=get_target(table[d_shape],beta),following=next_a*SLICE_SHAPES+next_d;edge_hash.u32le(current);edge_hash.byte(beta);edge_hash.u32le(following);++image_visits;if(step<4)early_return_failures+=following==packed;else power5_failures+=following!=packed;current=following;}}

 SliceHash bitmap_hash("COORD3-SLICE-BITMAP-v2");for(auto value:bitmap)bitmap_hash.byte(value);SliceHash representatives_hash("COORD3-REPS-v2");for(auto value:representatives)representatives_hash.u32le(value);
 SliceHash manifest_hash("COORD3-SLICE-INPUT-MANIFEST-v2");for(const auto&value:std::array<std::string,4>{{orbit_artifact,orbit_receipt,native_design,codex_correction}})manifest_hash.raw_hex(value);manifest_hash.raw("e'[q]=e[q+a]-e[a]+5*b*q(mod25);jD'=jD+eD[a]-eA[a]+3*b(mod5)");
 SliceHash charge_hash("COORD3-SLICE-CHARGES-v2");for(auto value:std::array<u64,8>{{ENUMERATOR_CHARGE,SHAPE_GENERATION_CHARGE,GROUP_CHECKS,COCYCLE_CHECKS,UNIQUE_B_CHECKS,ENCODER_CHECKS,SENTINEL_CHARGE,ENGINE_CHARGE}})charge_hash.u64le(value);

 bool r1_free=!collision_failures&&!early_return_failures&&!power5_failures;bool success=shapes.size()==SLICE_SHAPES&&search.total()==SHAPE_GENERATION_CHARGE&&!encoder_failures&&!closure_failures&&!ramp_failures&&group_checks==GROUP_CHECKS&&!group_failures&&cocycle_checks==COCYCLE_CHECKS&&!cocycle_failures&&unique_b_checks==UNIQUE_B_CHECKS&&!unique_b_failures&&r1_free&&visited_bits==SLICE_COUNT&&representatives.size()==REP_COUNT&&image_visits==SLICE_COUNT&&ENGINE_CHARGE<ENGINE_CAP&&2*ENGINE_CHARGE<SLICE_COMBINED_CAP&&PEAK_ALLOCATION_CAP<67108864ULL;
 std::string status=success?"T2_STATE_DEPENDENT_C5_SLICE_ENUMERATOR_CERTIFIED":"SLICE_ENUMERATOR_CERTIFICATE_MISMATCH";
 std::cout<<"REASONING_AD5_COORDINATE3_SLICE_V1\nstatus="<<status<<"\nshape_cardinality="<<shapes.size()<<"\nslice_cardinality="<<SLICE_COUNT<<"\naction_table_entries="<<ACTION_ENTRIES<<"\naction_table_bytes="<<ACTION_BYTES<<"\nshape_encoder_check_count="<<ENCODER_CHECKS<<"\nshape_encoder_failure_count="<<encoder_failures<<"\naction_closure_failure_count="<<closure_failures<<"\nramp_failure_count="<<ramp_failures<<"\ngroup_law_check_count="<<group_checks<<"\ngroup_law_failure_count="<<group_failures<<"\ncocycle_check_count="<<cocycle_checks<<"\ncocycle_failure_count="<<cocycle_failures<<"\nunique_b_check_count="<<unique_b_checks<<"\nunique_b_failure_count="<<unique_b_failures<<"\ncollision_failure_count="<<collision_failures<<"\nearly_return_failure_count="<<early_return_failures<<"\npower5_failure_count="<<power5_failures<<"\nr1_power5_identity="<<!power5_failures<<"\nr1_order="<<(r1_free?5:0)<<"\nr1_free="<<r1_free<<"\nvisited_bits="<<visited_bits<<"\nrepresentative_count="<<representatives.size()<<"\nimage_visit_count="<<image_visits<<"\nenumerator_charge="<<ENUMERATOR_CHARGE<<"\nshape_generation_charge="<<SHAPE_GENERATION_CHARGE<<"\nsentinel_charge="<<SENTINEL_CHARGE<<"\nengine_preflight_charge="<<ENGINE_CHARGE<<"\nengine_preflight_cap="<<ENGINE_CAP<<"\ndeclared_resident_bytes="<<DECLARED_RESIDENT<<"\nfixed_buffer_cap="<<FIXED_BUFFER_CAP<<"\npeak_allocation_cap="<<PEAK_ALLOCATION_CAP<<"\nrlimit_as_soft="<<limit.rlim_cur<<"\nrlimit_as_hard="<<limit.rlim_max<<"\nvm_peak_kib="<<vm_peak_kib()<<"\norbit_artifact_path=research/four-block/iteration-128/ad5-coordinate3-tensor-orbit-native.md\norbit_artifact_sha256="<<orbit_artifact<<"\norbit_receipt_sha256="<<orbit_receipt<<"\nnative_design_sha256="<<native_design<<"\ncodex_correction_sha256="<<codex_correction<<"\ninput_manifest_sha256="<<manifest_hash.finish()<<"\nshape_stream_sha256="<<shape_hash.finish()<<"\naction_table_sha256="<<action_hash.finish()<<"\nsentinel_transcript_sha256="<<sentinel_hash.finish()<<"\nresidual_edge_stream_sha256="<<edge_hash.finish()<<"\nslice_bitmap_sha256="<<bitmap_hash.finish()<<"\nrepresentative_stream_sha256="<<representatives_hash.finish()<<"\ncharge_transcript_sha256="<<charge_hash.finish()<<"\njoin_equivariance=UNPROVED\ncontraction_authorized=0\ncommon_zero_established=0\nREASONING_END\n";
}
"""
)

_INTEGER_FIELDS = {
    "shape_cardinality",
    "slice_cardinality",
    "action_table_entries",
    "action_table_bytes",
    "shape_encoder_check_count",
    "shape_encoder_failure_count",
    "action_closure_failure_count",
    "ramp_failure_count",
    "group_law_check_count",
    "group_law_failure_count",
    "cocycle_check_count",
    "cocycle_failure_count",
    "unique_b_check_count",
    "unique_b_failure_count",
    "collision_failure_count",
    "early_return_failure_count",
    "power5_failure_count",
    "r1_power5_identity",
    "r1_order",
    "r1_free",
    "visited_bits",
    "representative_count",
    "image_visit_count",
    "enumerator_charge",
    "shape_generation_charge",
    "sentinel_charge",
    "engine_preflight_charge",
    "engine_preflight_cap",
    "declared_resident_bytes",
    "fixed_buffer_cap",
    "peak_allocation_cap",
    "rlimit_as_soft",
    "rlimit_as_hard",
    "vm_peak_kib",
    "contraction_authorized",
    "common_zero_established",
}
_HASH_FIELDS = {
    "orbit_artifact_sha256",
    "orbit_receipt_sha256",
    "native_design_sha256",
    "codex_correction_sha256",
    "input_manifest_sha256",
    "shape_stream_sha256",
    "action_table_sha256",
    "sentinel_transcript_sha256",
    "residual_edge_stream_sha256",
    "slice_bitmap_sha256",
    "representative_stream_sha256",
    "charge_transcript_sha256",
}
_STRING_FIELDS = {"status", "orbit_artifact_path", "join_equivariance"}


def _parse_transcript(stdout: str) -> dict[str, Any]:
    lines = stdout.strip().splitlines()
    frame = f"REASONING_AD5_COORDINATE3_SLICE_V{_PROTOCOL_VERSION}"
    if len(lines) < 3 or lines[0] != frame or lines[-1] != "REASONING_END":
        raise ScientificToolError("AD5 central-slice transcript has invalid framing")
    fields: dict[str, str] = {}
    for line in lines[1:-1]:
        if "=" not in line:
            raise ScientificToolError("AD5 central-slice transcript has an invalid field")
        name, value = line.split("=", 1)
        if name in fields:
            raise ScientificToolError("AD5 central-slice transcript has duplicate fields")
        fields[name] = value
    if set(fields) != _INTEGER_FIELDS | _HASH_FIELDS | _STRING_FIELDS:
        raise ScientificToolError("AD5 central-slice transcript is incomplete")
    try:
        parsed: dict[str, Any] = {name: int(fields[name]) for name in _INTEGER_FIELDS}
    except ValueError as error:
        raise ScientificToolError("AD5 central-slice transcript has a nonintegral field") from error
    for name in _HASH_FIELDS:
        value = fields[name]
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ScientificToolError("AD5 central-slice transcript has an invalid digest")
        parsed[name] = value
    for name in _STRING_FIELDS:
        parsed[name] = fields[name]
    return parsed


def _run_program() -> dict[str, Any]:
    computation = _run_source_hashed_program(
        _PROGRAM,
        label="ad5-coordinate3-slice",
        timeout_seconds=900,
        runtime_address_space_bytes=_RESIDENT_BYTE_CAP,
        preserve_exact_transcript=True,
    )
    return {
        **computation,
        **_parse_transcript(computation["transcript"]),
        "mathematical_transcript_sha256": bounded_transcript_sha256(computation["transcript"], parser=_parse_transcript),
    }


def _run_replay() -> dict[str, Any]:
    replay_path = Path(__file__).with_name("heisenberg_level25_four_block_ad5_slice_replay.py")
    try:
        program = replay_path.read_bytes()
    except OSError as error:
        raise ScientificToolError(f"independent AD5 central-slice replay is unavailable: {error}") from error
    replay_python = shutil.which("python3", path="/usr/local/bin:/usr/bin:/bin") or sys.executable
    command = _address_space_limited_command([replay_python, "-I", "-S", str(replay_path)], _RESIDENT_BYTE_CAP)
    environment = {
        "HOME": "",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
    }
    try:
        completed = subprocess.run(
            sandbox.wrap(command, ro=[replay_path], network=False),
            text=True,
            capture_output=True,
            timeout=900,
            env=environment,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise ScientificToolError("independent AD5 central-slice replay exceeded 900 seconds") from error
    except OSError as error:
        raise ScientificToolError(f"independent AD5 central-slice replay could not start: {error}") from error
    if completed.returncode or completed.stderr.strip():
        detail = (completed.stderr.strip() or completed.stdout.strip() or "unknown replay failure")[:2000]
        raise ScientificToolError(f"independent AD5 central-slice replay failed: {detail}")
    try:
        replay = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise ScientificToolError("independent AD5 central-slice replay returned invalid JSON") from error
    if not isinstance(replay, dict):
        raise ScientificToolError("independent AD5 central-slice replay returned a non-object")
    return {
        **replay,
        "program_sha256": hashlib.sha256(program).hexdigest(),
        "transcript_sha256": hashlib.sha256(completed.stdout.encode()).hexdigest(),
        "transcript": completed.stdout,
        "runtime_address_space_limit_bytes": _RESIDENT_BYTE_CAP,
        "interpreter": str(Path(replay_python).resolve()),
    }


def level25_heisenberg_augmented_four_block_ad5_coordinate3_central_orbit_representative_preflight_profile() -> dict[str, Any]:
    """Certify the corrected state-dependent C5 central-slice enumerator."""

    orbit = level25_heisenberg_augmented_four_block_ad5_coordinate3_tensor_orbit_profile()
    computation = _run_program()
    replay = _run_replay()
    compared = (
        "status",
        "orbit_artifact_path",
        "join_equivariance",
        *(_INTEGER_FIELDS - {"rlimit_as_soft", "rlimit_as_hard", "vm_peak_kib"}),
        *_HASH_FIELDS,
    )
    replay_agreement = all(replay.get(name) == computation[name] for name in compared)
    actual_combined_charge = computation["engine_preflight_charge"] + replay["engine_preflight_charge"]
    expected_status = "T2_STATE_DEPENDENT_C5_SLICE_ENUMERATOR_CERTIFIED"
    source_manifest = _source_manifest()
    certificate = {
        "declared_design_and_orbit_references_match_executable_input": computation["orbit_artifact_path"] == _ORBIT_ARTIFACT_PATH
        and computation["orbit_artifact_sha256"] == _ORBIT_ARTIFACT_SHA256
        and computation["orbit_receipt_sha256"] == _ORBIT_RECEIPT_SHA256
        and computation["native_design_sha256"] == _NATIVE_DESIGN_SHA256
        and computation["codex_correction_sha256"] == _CODEX_CORRECTION_SHA256,
        "orbit_is_the_fresh_reviewed_prerequisite": reviewed_profile_matches(orbit, _ORBIT_EVIDENCE_SHA256),
        "slice_cardinality_matches_the_certified_orbit_domain": computation["shape_cardinality"] == orbit["computation"]["zero_shape_count"]
        and computation["slice_cardinality"] * 5 == orbit["computation"]["central_state_count"]
        and computation["representative_count"] == orbit["computation"]["central_orbit_count"],
        "source_manifest_still_has_725_literal_records": source_manifest["raw_record_count"] == 725,
        "canonical_zero_shapes_are_regenerated": computation["shape_cardinality"] == 4_420
        and computation["shape_generation_charge"] == 5_859_375
        and computation["shape_encoder_failure_count"] == 0,
        "five_lane_ramped_action_is_closed": computation["action_table_entries"] == 22_100
        and computation["action_table_bytes"] == 48_620
        and computation["action_closure_failure_count"] == 0
        and computation["ramp_failure_count"] == 0,
        "full_action_group_law_and_mod25_cocycle_pass": computation["group_law_check_count"] == 2_762_500
        and computation["group_law_failure_count"] == 0
        and computation["cocycle_check_count"] == 2_762_500
        and computation["cocycle_failure_count"] == 0,
        "slice_parameter_is_unique": computation["unique_b_check_count"] == 625
        and computation["unique_b_failure_count"] == 0,
        "residual_generator_is_exhaustively_free": computation["slice_cardinality"] == 19_536_400
        and computation["collision_failure_count"] == 0
        and computation["early_return_failure_count"] == 0
        and computation["power5_failure_count"] == 0
        and computation["r1_power5_identity"] == 1
        and computation["r1_order"] == 5
        and computation["r1_free"] == 1,
        "all_slice_states_are_partitioned_once": computation["visited_bits"] == 19_536_400
        and computation["representative_count"] == 3_907_280
        and computation["image_visit_count"] == 19_536_400,
        "primary_and_independent_replay_agree": replay_agreement,
        "combined_execution_respects_the_hard_cap": actual_combined_charge < _COMBINED_CHARGE_CAP
        and computation["engine_preflight_charge"] < _ENGINE_PREFLIGHT_CAP
        and replay["engine_preflight_charge"] < _ENGINE_PREFLIGHT_CAP,
        "both_engines_respect_the_hard_address_space_limit": computation["rlimit_as_soft"] == computation["rlimit_as_hard"] == replay["runtime_address_space_limit_bytes"] == _RESIDENT_BYTE_CAP,
        "resident_telemetry_respects_the_address_space_cap": 0 < computation["vm_peak_kib"] * 1024 <= _RESIDENT_BYTE_CAP,
        "programs_and_transcripts_match_reviewed_executions": bounded_execution_matches(
            computation, parser=_parse_transcript, expected_program=_REVIEWED_PROGRAM_SHA256,
            expected_mathematics=_REVIEWED_MATHEMATICAL_TRANSCRIPT_SHA256,
        ) and independent_replay_matches(
            replay, expected_program=_REVIEWED_REPLAY_PROGRAM_SHA256, expected_transcript=_REVIEWED_REPLAY_TRANSCRIPT_SHA256,
        ),
        "terminal_status_matches_the_exact_partition": computation["status"] == expected_status,
        "join_and_contraction_remain_rejected": computation["join_equivariance"] == "UNPROVED"
        and computation["contraction_authorized"] == 0
        and computation["common_zero_established"] == 0,
    }
    verified = all(certificate.values())
    return seal_profile_evidence({
        "schema": "reasoning.level25-heisenberg-ad5-coordinate3-central-slice-preflight.v2",
        "request": {},
        "request_sha256": hashlib.sha256(canonical_json({}).encode()).hexdigest(),
        "source_manifest": source_manifest,
        "orbit_receipt_sha256": hashlib.sha256(canonical_json(orbit).encode()).hexdigest(),
        "orbit_evidence_sha256": orbit["evidence_sha256"],
        "review_reference_policy": {"kind": "declared_source_literals", "external_contents_verified": False},
        "charge_scope": "named primary and independent replay only; prerequisite executions are separate",
        "computation": computation,
        "independent_replay": replay,
        "actual_combined_charge": actual_combined_charge,
        "decision": {
            "status": computation["status"],
            "central_slice_enumerator_certified": verified and computation["status"] == expected_status,
            "slice_cardinality": computation["slice_cardinality"],
            "representative_count": computation["representative_count"],
            "authorized_outer_loop_reduction_factor": 5 if verified and computation["r1_free"] else 1,
            "join_equivariance": computation["join_equivariance"],
            "full_contraction_authorized": bool(computation["contraction_authorized"]),
            "common_zero_found": bool(computation["common_zero_established"]),
        },
        "certificate": certificate,
        "certificate_verified": verified,
        "interpretation": (
            "The corrected state-dependent residual C5 action partitions the j_D=0 slice into "
            "3,907,280 canonical central representatives. This realizes the already certified factor-25 "
            "central quotient as a factor-5 slice traversal, but does not prove that any compressed B/C join commutes with it."
        ),
        "not_certified": [
            "the ramp-free rotation action proposed in the first two model designs",
            "implementation-level B/C record or join equivariance",
            "completeness of compressed functional signatures for the raw polynomial predicate",
            "construction of any joint F_101^3 B or C profile",
            "a Fourier transform, convolution, join, or full contraction",
            "a coordinate-three solution count or census",
            "a seven-equation survivor or common zero",
            "geometric points over extensions of F_101 or a characteristic-zero lift",
            "global smoothness, a Brauer class, period, index, or period-index counterexample",
        ],
        "next_gate": {
            "feature": "level25_heisenberg_augmented_four_block_ad5_coordinate3_join_record_commutation_preflight",
            "requirement": (
                "Define the exact B/C key and payload records of the future join, then independently replay "
                "an exhaustive one-generator transform-then-encode versus encode-then-transform commutation census with the phase ramp restored."
            ),
        },
    })
