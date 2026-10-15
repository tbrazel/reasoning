# ruff: noqa: E501
"""Lossless fixed-fiber pair-table calibration for the four-block row-0 stage."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from ..canonical import canonical_json
from .heisenberg_level25_four_block_row0_kernel import (
    _ROW0_KERNEL_RANK_EVIDENCE_SHA256,
    level25_heisenberg_augmented_four_block_row0_kernel_rank_profile,
)
from .heisenberg_level25_three_block import _run_source_hashed_program
from .science_evidence import exact_execution_matches, reviewed_profile_matches, seal_profile_evidence
from .scientific import ScientificToolError

_PROTOCOL_VERSION = 1
_REVIEWED_PROGRAM_SHA256 = "69d2ae72c2056f6aead3f0f72fc8051cb6bf68e49f7d9667112a6ea0823def9d"
_REVIEWED_TRANSCRIPT_SHA256 = "0bbfd2df77e9d8089b55b4af6e7cad18108bbf89ce22eb1bae1ace8de1217c0c"
_ROW0_PAIR_EVIDENCE_SHA256 = "cb473fbb7a70dacabd981444bc7e582e3cf0523d3fc3ba8e504f6253ea3e15c5"
_REVIEWED_REPLAY_PROGRAM_SHA256 = "b228f6087eb271c512f63451ce9d6936258476cd0c70aa98b06e0c6a20a8775b"
_REVIEWED_REPLAY_TRANSCRIPT_SHA256 = "0bbfd2df77e9d8089b55b4af6e7cad18108bbf89ce22eb1bae1ace8de1217c0c"
_FIELD_PRIME = 101
_EDGE_VALUE_COUNT = 65
_RAW_LIST_HISTOGRAM_LENGTH = _EDGE_VALUE_COUNT + 1
_PHASE_LIST_HISTOGRAM_LENGTH = _FIELD_PRIME + 1
_RESIDENT_BYTE_CAP = 64 * 1024**2
_PAIR_VISIT_CAP = 2 * 4420**2
_PER_IMPLEMENTATION_CHARGE_BOUND = (
    390625 * 45
    + 625 * 625 * 5
    + _PAIR_VISIT_CAP
    + _PAIR_VISIT_CAP
    + 5 * _PAIR_VISIT_CAP
    + 5 * _PAIR_VISIT_CAP
    + 4 * _PAIR_VISIT_CAP
)
_COMBINED_CHARGE_BOUND = 2 * _PER_IMPLEMENTATION_CHARGE_BOUND
_OPERATION_CAP = 2**31

_PROGRAM = r"""
#include <algorithm>
#include <array>
#include <bit>
#include <cstdint>
#include <iostream>
#include <limits>
#include <vector>

using u32=std::uint32_t;
using u64=std::uint64_t;
constexpr int P=101,N=25,ZETA=5,OMEGA=95,LABELS=625,EDGE_VALUES=65;
constexpr int RAW_BINS=P*P*EDGE_VALUES,PHASE_BINS=P*P*P;
constexpr u64 FNV_OFFSET=1469598103934665603ULL;

int pw(int b,int e){int r=1;while(e){if(e&1)r=r*b%P;b=b*b%P;e>>=1;}return r;}
int coeff(int a,int i){int e=(-a*i)%N;if(e<0)e+=N;return pw(ZETA,e);}
void mix(u64&h,u64 value){for(int byte=0;byte<8;++byte){h^=(value>>(8*byte))&255;h*=1099511628211ULL;}}
u64 rot(u64 x,unsigned n){return n?std::rotl(x,int(n)):x;}

struct Shape{std::uint16_t label;std::array<std::uint8_t,4>b6,b8;};
struct Digest{u64 sum=0,xors=0,squares=0;};
struct SideResult{
 u64 pair_visits=0,raw_mass=0,phase_mass=0,raw_support=0,phase_support=0;
 u64 raw_max=0,phase_max=0,phase_collision_merges=0,transport_checks=0,transport_failures=0;
 Digest raw_digest{},phase_digest{};u64 raw_boundary_checksum=FNV_OFFSET,phase_boundary_checksum=FNV_OFFSET;
 int raw_boundary_min=RAW_BINS,raw_boundary_max=0,phase_boundary_min=PHASE_BINS,phase_boundary_max=0;
 std::array<u64,66>raw_hist{};std::array<u64,102>phase_hist{};
 std::array<u64,P>raw_value_mass{},phase_value_mass{};
};

u64 record_hash(int label,u32 key,u32 count){u64 h=FNV_OFFSET;mix(h,label);mix(h,key);mix(h,count);return h;}
void add_digest(Digest&d,int label,u32 key,u32 count){u64 h=record_hash(label,key,count);d.sum+=h;d.xors^=rot(h,(label+key)%64);d.squares+=h*h;}
int raw_key(int base,int yi){return base*EDGE_VALUES+yi;}
int phase_key(int base,int z){return base*P+z;}

SideResult calibrate(
 const std::vector<Shape>&shapes,
 const std::vector<u32>&zero,
 const std::array<std::vector<u32>,LABELS>&by_label,
 const std::array<std::uint8_t,LABELS*LABELS>&edge,
 const std::array<int,P>&edge_index,
 const std::array<int,EDGE_VALUES>&edge_values,
 const std::array<int,5>&omega,
 int first_residue,int second_residue,int beta,bool right_order
){
 SideResult out;
 std::vector<u32>raw(RAW_BINS),phase(PHASE_BINS),raw_touched,phase_touched;
 raw_touched.reserve(RAW_BINS);phase_touched.reserve(PHASE_BINS);
 std::array<std::uint8_t,P*P>raw_lengths{},phase_lengths{};
 std::vector<u32>raw_bases,phase_bases;raw_bases.reserve(P*P);phase_bases.reserve(P*P);
 for(int boundary=0;boundary<LABELS;++boundary){
  const auto&fixed=by_label[boundary];
  if(!right_order){
   for(u32 second_code:fixed){const auto&b=shapes[second_code];for(u32 first_code:zero){const auto&a=shapes[first_code];
    int first=(a.b6[first_residue]+b.b6[second_residue])%P,second=(a.b8[first_residue]+b.b8[second_residue])%P;
    int y=edge[a.label*LABELS+boundary],base=first*P+second,key=raw_key(base,edge_index[y]);
    if(!raw[key]){raw_touched.push_back(key);if(!raw_lengths[base]++)raw_bases.push_back(base);}if(raw[key]==std::numeric_limits<u32>::max())return SideResult{};++raw[key];++out.pair_visits;
   }}
  }else{
   for(u32 first_code:fixed){const auto&a=shapes[first_code];for(u32 second_code:zero){const auto&b=shapes[second_code];
    int first=(a.b6[first_residue]+b.b6[second_residue])%P,second=(a.b8[first_residue]+b.b8[second_residue])%P;
    int y=edge[boundary*LABELS+b.label],base=first*P+second,key=raw_key(base,edge_index[y]);
    if(!raw[key]){raw_touched.push_back(key);if(!raw_lengths[base]++)raw_bases.push_back(base);}if(raw[key]==std::numeric_limits<u32>::max())return SideResult{};++raw[key];++out.pair_visits;
   }}
  }
  u64 raw_boundary=raw_touched.size();mix(out.raw_boundary_checksum,boundary);mix(out.raw_boundary_checksum,raw_boundary);
  out.raw_boundary_min=std::min(out.raw_boundary_min,int(raw_boundary));out.raw_boundary_max=std::max(out.raw_boundary_max,int(raw_boundary));out.raw_support+=raw_boundary;
  out.raw_hist[0]+=P*P-raw_bases.size();for(u32 base:raw_bases){++out.raw_hist[raw_lengths[base]];raw_lengths[base]=0;}raw_bases.clear();
  for(u32 key:raw_touched){u32 count=raw[key];int base=key/EDGE_VALUES,yi=key%EDGE_VALUES,y=edge_values[yi];
   out.raw_mass+=count;out.raw_value_mass[y]+=count;out.raw_max=std::max(out.raw_max,u64(count));add_digest(out.raw_digest,boundary,key,count);
   int first=base/P,second=base%P;for(int a=1;a<5;++a){int moved=(first*omega[a]%P)*P+(second*pw(omega[a],P-2)%P);u32 moved_count=raw[raw_key(moved,yi)];++out.transport_checks;if(moved_count!=count)++out.transport_failures;}
   for(int n=0;n<5;++n){int z=beta*omega[n]%P*y%P,pkey=phase_key(base,z);if(!phase[pkey]){phase_touched.push_back(pkey);if(!phase_lengths[base]++)phase_bases.push_back(base);}else ++out.phase_collision_merges;if(u64(phase[pkey])+count>std::numeric_limits<u32>::max())return SideResult{};phase[pkey]+=count;}
  }
  u64 phase_boundary=phase_touched.size();mix(out.phase_boundary_checksum,boundary);mix(out.phase_boundary_checksum,phase_boundary);
  out.phase_boundary_min=std::min(out.phase_boundary_min,int(phase_boundary));out.phase_boundary_max=std::max(out.phase_boundary_max,int(phase_boundary));out.phase_support+=phase_boundary;
  out.phase_hist[0]+=P*P-phase_bases.size();for(u32 base:phase_bases){++out.phase_hist[phase_lengths[base]];phase_lengths[base]=0;}phase_bases.clear();
  for(u32 key:phase_touched){u32 count=phase[key];int z=key%P;out.phase_mass+=count;out.phase_value_mass[z]+=count;out.phase_max=std::max(out.phase_max,u64(count));add_digest(out.phase_digest,boundary,key,count);phase[key]=0;}phase_touched.clear();
  for(u32 key:raw_touched)raw[key]=0;raw_touched.clear();
 }
 return out;
}

template<std::size_t S>void print_array(const std::array<u64,S>&a){for(std::size_t i=0;i<S;++i){if(i)std::cout<<",";std::cout<<a[i];}std::cout<<"\n";}
void print2(u64 a,u64 b){std::cout<<a<<","<<b<<"\n";}
void print_digest(const Digest&a,const Digest&b){std::cout<<a.sum<<","<<a.xors<<","<<a.squares<<","<<b.sum<<","<<b.xors<<","<<b.squares<<"\n";}

int main(){
 std::array<int,5>omega{};for(int n=0;n<5;++n)omega[n]=pw(OMEGA,n);
 std::array<std::array<std::uint8_t,5>,LABELS>label_values{};for(int label=0;label<LABELS;++label){int work=label;label_values[label][0]=1;for(int q=1;q<5;++q){label_values[label][q]=omega[work%5];work/=5;}}
 std::array<std::uint8_t,LABELS*LABELS>edge{};std::array<bool,P>seen{};u64 edge_checksum=FNV_OFFSET;
 for(int a=0;a<LABELS;++a)for(int b=0;b<LABELS;++b){int y=0;for(int q=0;q<5;++q)y=(y+pw(label_values[a][q],P-2)*label_values[b][q])%P;edge[a*LABELS+b]=y;seen[y]=true;mix(edge_checksum,a);mix(edge_checksum,b);mix(edge_checksum,y);}
 std::array<int,P>edge_index{};std::array<int,EDGE_VALUES>edge_values{};edge_index.fill(-1);int edge_count=0;for(int y=0;y<P;++y)if(seen[y]){edge_index[y]=edge_count;edge_values[edge_count++]=y;}
 if(edge_count!=EDGE_VALUES)return 61;
 std::array<int,P>phase_preimages{};for(int y=0;y<P;++y)if(seen[y])for(int n=0;n<5;++n)++phase_preimages[omega[n]*y%P];int phase_multi=0,phase_max_preimage=0;for(int z=0;z<P;++z){if(phase_preimages[z]>1)++phase_multi;phase_max_preimage=std::max(phase_max_preimage,phase_preimages[z]);}
 std::vector<Shape>shapes(390625);std::vector<u32>zero;std::array<std::vector<u32>,LABELS>by_label;std::array<u64,LABELS>zero_label_counts{};
 for(u32 code=0;code<shapes.size();++code){u32 work=code;std::array<int,5>u{{1}};int label=0,place=1;for(int q=1;q<5;++q){int exponent=work%25;work/=25;u[q]=pw(ZETA,exponent);label+=(exponent%5)*place;place*=5;}std::array<int,5>inverse{};for(int q=0;q<5;++q)inverse[q]=pw(u[q],24);int bucket=0;for(int q=0;q<5;++q)bucket=(bucket+inverse[q]*u[(q+1)%5])%P;Shape shape{};shape.label=label;for(int residue=0;residue<4;++residue)for(int q=0;q<5;++q){int coordinate=residue+5*q;shape.b6[residue]=(shape.b6[residue]+coeff(3,coordinate)*inverse[q]%P*u[(q+1)%5])%P;shape.b8[residue]=(shape.b8[residue]+coeff(6,coordinate)*inverse[q]%P*u[(q+4)%5])%P;}shapes[code]=shape;if(!bucket){zero.push_back(code);by_label[label].push_back(code);++zero_label_counts[label];}}
 SideResult left=calibrate(shapes,zero,by_label,edge,edge_index,edge_values,omega,0,1,1,false);
 SideResult right=calibrate(shapes,zero,by_label,edge,edge_index,edge_values,omega,2,3,87,true);
 if(!left.raw_mass||!right.raw_mass)return 62;
 u64 shape_bytes=u64(shapes.size())*sizeof(Shape),zero_code_bytes=u64(zero.size())*2*sizeof(u32);
 u64 dense_payload_bytes=u64(RAW_BINS+PHASE_BINS)*2*sizeof(u32);
 std::cout<<"REASONING_AUGMENTED_FOUR_BLOCK_ROW0_FIXED_FIBER_PAIR_CALIBRATION_V1\nfield_prime="<<P<<"\nomega="<<OMEGA<<"\nlabel_count="<<LABELS<<"\nedge_value_count="<<edge_count<<"\nedge_checksum="<<edge_checksum<<"\n";
 std::cout<<"edge_phase_multiple_preimage_value_count="<<phase_multi<<"\nedge_phase_maximum_preimage_count="<<phase_max_preimage<<"\nshape_count="<<shapes.size()<<"\nzero_bucket_population="<<zero.size()<<"\nzero_bucket_label_support="<<std::count_if(zero_label_counts.begin(),zero_label_counts.end(),[](u64 n){return n>0;})<<"\n";
 std::cout<<"beta_values=1,84,87\nblock_scale_coefficients=1,1,1,1\npair_visits=";print2(left.pair_visits,right.pair_visits);std::cout<<"raw_masses=";print2(left.raw_mass,right.raw_mass);std::cout<<"phase_masses=";print2(left.phase_mass,right.phase_mass);std::cout<<"raw_support_counts=";print2(left.raw_support,right.raw_support);std::cout<<"phase_support_counts=";print2(left.phase_support,right.phase_support);std::cout<<"raw_maximum_coefficients=";print2(left.raw_max,right.raw_max);std::cout<<"phase_maximum_coefficients=";print2(left.phase_max,right.phase_max);std::cout<<"phase_collision_merge_counts=";print2(left.phase_collision_merges,right.phase_collision_merges);
 std::cout<<"raw_record_digests=";print_digest(left.raw_digest,right.raw_digest);std::cout<<"phase_record_digests=";print_digest(left.phase_digest,right.phase_digest);std::cout<<"raw_boundary_support_checksums=";print2(left.raw_boundary_checksum,right.raw_boundary_checksum);std::cout<<"phase_boundary_support_checksums=";print2(left.phase_boundary_checksum,right.phase_boundary_checksum);std::cout<<"raw_boundary_support_minima=";print2(left.raw_boundary_min,right.raw_boundary_min);std::cout<<"raw_boundary_support_maxima=";print2(left.raw_boundary_max,right.raw_boundary_max);std::cout<<"phase_boundary_support_minima=";print2(left.phase_boundary_min,right.phase_boundary_min);std::cout<<"phase_boundary_support_maxima=";print2(left.phase_boundary_max,right.phase_boundary_max);
 std::cout<<"raw_list_length_histogram_left=";print_array(left.raw_hist);std::cout<<"raw_list_length_histogram_right=";print_array(right.raw_hist);std::cout<<"phase_list_length_histogram_left=";print_array(left.phase_hist);std::cout<<"phase_list_length_histogram_right=";print_array(right.phase_hist);std::cout<<"raw_edge_value_masses_left=";print_array(left.raw_value_mass);std::cout<<"raw_edge_value_masses_right=";print_array(right.raw_value_mass);std::cout<<"phase_value_masses_left=";print_array(left.phase_value_mass);std::cout<<"phase_value_masses_right=";print_array(right.phase_value_mass);
 std::cout<<"raw_transport_check_counts=";print2(left.transport_checks,right.transport_checks);std::cout<<"raw_transport_failure_counts=";print2(left.transport_failures,right.transport_failures);std::cout<<"shape_payload_bytes="<<shape_bytes<<"\nzero_code_payload_bytes="<<zero_code_bytes<<"\ndense_counter_and_touched_payload_bytes="<<dense_payload_bytes<<"\nREASONING_END\n";
}
"""


def _parse_int_list(value: str, *, count: int, label: str) -> list[int]:
    try:
        parsed = [int(item) for item in value.split(",")]
    except ValueError as error:
        raise ScientificToolError(f"fixed-fiber pair transcript has a nonintegral {label}") from error
    if len(parsed) != count:
        raise ScientificToolError(f"fixed-fiber pair transcript has a malformed {label}")
    return parsed


def _parse_transcript(stdout: str) -> dict[str, Any]:
    lines = stdout.strip().splitlines()
    frame = f"REASONING_AUGMENTED_FOUR_BLOCK_ROW0_FIXED_FIBER_PAIR_CALIBRATION_V{_PROTOCOL_VERSION}"
    if len(lines) < 3 or lines[0] != frame or lines[-1] != "REASONING_END":
        raise ScientificToolError("fixed-fiber pair transcript has invalid framing")
    fields: dict[str, str] = {}
    for line in lines[1:-1]:
        if "=" not in line:
            raise ScientificToolError("fixed-fiber pair transcript has an invalid field")
        name, value = line.split("=", 1)
        if name in fields:
            raise ScientificToolError("fixed-fiber pair transcript has duplicate fields")
        fields[name] = value
    list_fields = {
        "beta_values": 3,
        "block_scale_coefficients": 4,
        "pair_visits": 2,
        "raw_masses": 2,
        "phase_masses": 2,
        "raw_support_counts": 2,
        "phase_support_counts": 2,
        "raw_maximum_coefficients": 2,
        "phase_maximum_coefficients": 2,
        "phase_collision_merge_counts": 2,
        "raw_record_digests": 6,
        "phase_record_digests": 6,
        "raw_boundary_support_checksums": 2,
        "phase_boundary_support_checksums": 2,
        "raw_boundary_support_minima": 2,
        "raw_boundary_support_maxima": 2,
        "phase_boundary_support_minima": 2,
        "phase_boundary_support_maxima": 2,
        "raw_list_length_histogram_left": _RAW_LIST_HISTOGRAM_LENGTH,
        "raw_list_length_histogram_right": _RAW_LIST_HISTOGRAM_LENGTH,
        "phase_list_length_histogram_left": _PHASE_LIST_HISTOGRAM_LENGTH,
        "phase_list_length_histogram_right": _PHASE_LIST_HISTOGRAM_LENGTH,
        "raw_edge_value_masses_left": _FIELD_PRIME,
        "raw_edge_value_masses_right": _FIELD_PRIME,
        "phase_value_masses_left": _FIELD_PRIME,
        "phase_value_masses_right": _FIELD_PRIME,
        "raw_transport_check_counts": 2,
        "raw_transport_failure_counts": 2,
    }
    integer_fields = {
        "field_prime",
        "omega",
        "label_count",
        "edge_value_count",
        "edge_checksum",
        "edge_phase_multiple_preimage_value_count",
        "edge_phase_maximum_preimage_count",
        "shape_count",
        "zero_bucket_population",
        "zero_bucket_label_support",
        "shape_payload_bytes",
        "zero_code_payload_bytes",
        "dense_counter_and_touched_payload_bytes",
    }
    if set(fields) != integer_fields | set(list_fields):
        raise ScientificToolError("fixed-fiber pair transcript is incomplete")
    try:
        parsed: dict[str, Any] = {name: int(fields[name]) for name in integer_fields}
    except ValueError as error:
        raise ScientificToolError("fixed-fiber pair transcript has a nonintegral field") from error
    parsed.update({name: _parse_int_list(fields[name], count=count, label=name) for name, count in list_fields.items()})
    return parsed


def _run_program() -> dict[str, Any]:
    computation = _run_source_hashed_program(
        _PROGRAM,
        label="augmented-four-block-row0-fixed-fiber-pair-calibration",
        timeout_seconds=240,
        runtime_address_space_bytes=_RESIDENT_BYTE_CAP,
        preserve_exact_transcript=True,
    )
    return {**computation, **_parse_transcript(computation["transcript"])}


def _run_replay() -> dict[str, Any]:
    replay_path = Path(__file__).with_name("heisenberg_level25_four_block_row0_pair_replay.cpp")
    try:
        replay_program = replay_path.read_text(encoding="utf-8")
    except OSError as error:
        raise ScientificToolError(f"independent fixed-fiber pair replay is unavailable: {error}") from error
    computation = _run_source_hashed_program(
        replay_program,
        label="augmented-four-block-row0-fixed-fiber-pair-calibration-replay",
        timeout_seconds=240,
        runtime_address_space_bytes=_RESIDENT_BYTE_CAP,
        preserve_exact_transcript=True,
    )
    return {**computation, **_parse_transcript(computation["transcript"])}


def level25_heisenberg_augmented_four_block_row0_fixed_fiber_pair_calibration_profile() -> dict[str, Any]:
    """Measure both lossless pair tables for the zero fixed fiber."""

    prerequisite = level25_heisenberg_augmented_four_block_row0_kernel_rank_profile()
    prerequisite_verified = reviewed_profile_matches(prerequisite, _ROW0_KERNEL_RANK_EVIDENCE_SHA256)
    prerequisite_evidence_sha256 = prerequisite["evidence_sha256"]
    prerequisite_sha256 = hashlib.sha256(canonical_json(prerequisite).encode()).hexdigest()
    del prerequisite
    computation = _run_program()
    replay = _run_replay()
    comparable_fields = tuple(
        name
        for name in computation
        if name
        not in {
            "protocol_version",
            "program_sha256",
            "transcript_sha256",
            "transcript",
            "compiler",
            "compiler_sha256",
            "compiler_version",
            "binary_sha256",
        }
    )
    expected_pair_mass = 4420**2
    certificate = {
        "kernel_rank_receipt_is_the_certified_prerequisite": prerequisite_verified,
        "program_and_transcript_match_reviewed_computation": exact_execution_matches(computation, parser=_parse_transcript, expected_program=_REVIEWED_PROGRAM_SHA256, expected_transcript=_REVIEWED_TRANSCRIPT_SHA256),
        "independent_program_and_transcript_match_reviewed_replay": exact_execution_matches(replay, parser=_parse_transcript, expected_program=_REVIEWED_REPLAY_PROGRAM_SHA256, expected_transcript=_REVIEWED_REPLAY_TRANSCRIPT_SHA256),
        "compiled_and_independent_replays_agree": all(computation[name] == replay[name] for name in comparable_fields),
        "fixed_fiber_and_source_coefficients_are_exact": computation["zero_bucket_population"] == 4420 and computation["zero_bucket_label_support"] == 351 and computation["beta_values"] == [1, 84, 87] and computation["block_scale_coefficients"] == [1, 1, 1, 1],
        "raw_tables_are_lossless": computation["pair_visits"] == [expected_pair_mass, expected_pair_mass] and computation["raw_masses"] == [expected_pair_mass, expected_pair_mass],
        "phase_closure_preserves_exact_multiplicity_five": computation["phase_masses"] == [5 * expected_pair_mass, 5 * expected_pair_mass] and all(value > 0 for value in computation["phase_collision_merge_counts"]),
        "raw_transport_is_exact_on_the_zero_fiber": computation["raw_transport_check_counts"] == [4 * computation["raw_support_counts"][0], 4 * computation["raw_support_counts"][1]] and computation["raw_transport_failure_counts"] == [0, 0],
        "histograms_cover_every_boundary_and_unary_key": sum(computation["raw_list_length_histogram_left"]) == 625 * 101**2 and sum(computation["raw_list_length_histogram_right"]) == 625 * 101**2 and sum(computation["phase_list_length_histogram_left"]) == 625 * 101**2 and sum(computation["phase_list_length_histogram_right"]) == 625 * 101**2,
        "hard_caps_cover_both_sequential_implementations": _COMBINED_CHARGE_BOUND < _OPERATION_CAP and computation["runtime_address_space_limit_bytes"] == _RESIDENT_BYTE_CAP and replay["runtime_address_space_limit_bytes"] == _RESIDENT_BYTE_CAP,
    }
    request: dict[str, Any] = {}
    return seal_profile_evidence({
        "schema": "reasoning.level25-heisenberg-augmented-four-block-row0-fixed-fiber-pair-calibration-profile.v2",
        "request": request,
        "request_sha256": hashlib.sha256(canonical_json(request).encode()).hexdigest(),
        "kernel_rank_receipt_sha256": prerequisite_sha256,
        "kernel_rank_evidence_sha256": prerequisite_evidence_sha256,
        "fiber": {"t": 0, "scale_classes": [0, 0, 0], "bucket_targets": [0, 0, 0, 0], "block_scale_coefficients": [1, 1, 1, 1], "edge_phase_coefficients": [1, 84, 87]},
        "table_definitions": {
            "left": "P_AB(x_B,P,Q,y) with residue-specific P=K6,0(A)+K6,1(B), Q=K8,0(A)+K8,1(B), y=W(x_A,x_B)",
            "right": "P_CD(x_C,P,Q,y) with residue-specific P=K6,2(C)+K6,3(D), Q=K8,2(C)+K8,3(D), y=W(x_C,x_D)",
            "phase_closure": "T(x,P,Q,z)=sum_y P(x,P,Q,y)*#{n:z=beta*95^n*y}; collisions are accumulated in a separate dense z counter",
        },
        "computation": computation,
        "independent_compiled_replay": replay,
        "engineering_caps": {
            "scope": "two sequential named implementations, excluding prerequisite execution",
            "charged_operations": _OPERATION_CAP,
            "per_implementation_conservative_record_operation_bound": _PER_IMPLEMENTATION_CHARGE_BOUND,
            "combined_conservative_record_operation_bound": _COMBINED_CHARGE_BOUND,
            "resident_bytes_per_implementation": _RESIDENT_BYTE_CAP,
            "hard_address_space_bytes_per_implementation": _RESIDENT_BYTE_CAP,
        },
        "certificate": certificate,
        "certificate_verified": all(certificate.values()),
        "decision": {
            "status": "FIXED_FIBER_PAIR_CALIBRATION_VALID" if all(certificate.values()) else "PROFILE_INVALID",
            "reason": "The two source-hashed implementations reproduce the complete coefficient-bearing raw and phase-closed pair-table distributions for the single zero fiber, including all phase collisions.",
        },
        "interpretation": "This calibration measures the lossless joint state needed to price a future crossing join and to search for exact reuse across fibers; it does not itself authorize such a schedule.",
        "not_certified": [
            "a crossing B-C join or a row-zero survivor count",
            "the cost of all 1,344 representative fibers",
            "a reuse theorem across scale classes or nonzero t-orbits",
            "satisfaction of rows 5 and 7 or the selected Q7 and Q9 couplings",
            "a common zero, geometric lift, Brauer class, index bound, or period-index counterexample",
        ],
        "next_gate": {
            "feature": "level25_heisenberg_augmented_four_block_row0_pair_reuse_theorem",
            "requirement": "Use the measured joint list distributions to prove an exact cache, permutation, or transform reuse across the 64 scale classes and 21 t-orbits before pricing an all-fiber contraction.",
        },
    })
