# ruff: noqa: E501
"""Exact AD5 join batch on canonical central representatives 5 through 8."""

from __future__ import annotations

import hashlib
from typing import Any

from ..canonical import canonical_json
from .heisenberg_level25_four_block_ad5 import (
    _AD5_EVIDENCE_SHA256,
    _RESIDENT_BYTE_CAP,
    _source_manifest,
    level25_heisenberg_augmented_four_block_bounded_ad5_profile,
)
from .heisenberg_level25_four_block_ad5_batch import (
    _BATCH_EVIDENCE_SHA256,
    level25_heisenberg_augmented_four_block_bounded_ad5_context_batch_profile,
)
from .heisenberg_level25_four_block_ad5_batch import (
    _PROGRAM as _PRIOR_BATCH_PROGRAM,
)
from .heisenberg_level25_four_block_ad5_batch import (
    _SUPPORT as _EVALUATOR_SUPPORT,
)
from .heisenberg_level25_four_block_ad5_batch import (
    _parse_transcript as _parse_prior_batch_transcript,
)
from .heisenberg_level25_four_block_ad5_join import (
    _JOIN_EVIDENCE_SHA256,
    level25_heisenberg_augmented_four_block_ad5_coordinate3_join_partition_covariance_profile,
)
from .heisenberg_level25_four_block_ad5_slice import (
    _SLICE_EVIDENCE_SHA256,
    level25_heisenberg_augmented_four_block_ad5_coordinate3_central_orbit_representative_preflight_profile,
)
from .heisenberg_level25_three_block import _run_source_hashed_program
from .science_evidence import bounded_execution_matches, bounded_transcript_sha256, reviewed_profile_matches, seal_profile_evidence
from .scientific import ScientificToolError

_CONTEXT_COUNT = 4
_CONTEXT_POSITIONS = (5, 6, 7, 8)
_EXPECTED_CODES = (280, 281, 480, 489, 506, 514, 752, 753, 1202)
_ENGINE_CHARGE_CAP = 270_000_000
_COMBINED_CHARGE_CAP = 540_000_000
_REPRESENTATIVE_EVIDENCE_SHA256 = "2714b93ca2b26587ff4dc1b08d38728892166bbc8f5c2864f75194eb6649c9f9"
_COVARIANCE_RECEIPT_SHA256 = "a2c96c3b973a96cb9589297af4dff727916068f9e93bd1d7f2a14818d414ab1c"
_SLICE_SHAPE_SHA256 = "f160568c8456b21812d2a67a380f9224bf03f8be39070d5aaf1278fd195c5e15"
_SLICE_REPRESENTATIVE_SHA256 = "a9d128cc5614b2dbffad31c2fa04aa372f5bbacb4397143e4f74e52f23704dc2"
_FIRST_CONTEXT_RECEIPT_SHA256 = "12a4667534b366e72110fe383631fa04b6fb29dc5b8ccc21294e6eb376c2d7cc"
_NEXT_FOUR_RECEIPT_SHA256 = "1e20108b0a39aa7ec257695dd0e158cdf5abfd1124ea5ecbd4b2f9ef220ea9ed"
_NATIVE_DESIGN_SHA256 = "5d145c6ca6d3e03ef46bfa4f03c92f64cb1aa923ce80d8c47b86c21c92abf824"
_CODEX_AUDIT_SHA256 = "73d0169d35f1c90f19b9a4c3be3d046335dab3686c3da5a4f2ae12a6719f02fd"
_REVIEWED_PROGRAM_SHA256 = "d67863f733b843e71fb7ebd5325263e144c94a1805897900df4c4ddf6009aa2f"
_REVIEWED_MATHEMATICAL_TRANSCRIPT_SHA256 = "f1f2b101e36a4bb764458d7c4d5305f83adc84bfeef8903de2b17c95915e4077"
_REVIEWED_REPLAY_PROGRAM_SHA256 = "2c27e0bcc2428ddb3b8598de98fd9fa9c46c3c227eb2590601d91d4efa0d299e"
_REVIEWED_REPLAY_MATHEMATICAL_TRANSCRIPT_SHA256 = "8e83caa5c96e30f25ec55638fee611d162e7768d8e53c3eaa06c18986ce90882"


def _replace_once(source: str, old: str, new: str) -> str:
    if source.count(old) != 1:
        raise RuntimeError("prior AD5 batch source no longer has the expected splice point")
    return source.replace(old, new)


_REPRESENTATIVE_HELPERS = r"""
using RepWord=std::array<int,5>;
RepWord rep_word(std::uint32_t code){std::array<int,4>tail{};(void)decode_shape(code,&tail);return {{0,tail[0],tail[1],tail[2],tail[3]}};}
std::uint32_t rep_code(const RepWord&word){std::uint32_t code=0;for(int q=1;q<5;++q)code=25*code+word[q];return code;}
std::uint32_t rep_transform_code(std::uint32_t code,int b){auto word=rep_word(code);RepWord out{};for(int q=0;q<5;++q){int value=word[(q+1)%5]-word[1]+5*b*q;value%=25;if(value<0)value+=25;out[q]=value;}return rep_code(out);}
int rep_position(const std::vector<std::uint32_t>&zero,std::uint32_t code){auto found=std::lower_bound(zero.begin(),zero.end(),code);return found==zero.end()||*found!=code?-1:int(found-zero.begin());}
std::array<u64,45> rep_prefix_orbits(const std::vector<std::uint32_t>&zero,u64&failures){std::array<u64,45>out{};for(int packed=0;packed<9;++packed){int a_position=0,d_position=packed,start=packed;std::array<u64,5>orbit{};for(int step=0;step<5;++step){u64 current=u64(a_position)*ZERO_COUNT+d_position;orbit[step]=current;out[5*packed+step]=current;auto a_word=rep_word(zero[a_position]),d_word=rep_word(zero[d_position]);int beta=2*(a_word[1]-d_word[1])%5;if(beta<0)beta+=5;int next_a=rep_position(zero,rep_transform_code(zero[a_position],beta)),next_d=rep_position(zero,rep_transform_code(zero[d_position],beta));if(next_a<0||next_d<0){++failures;next_a=0;next_d=0;}a_position=next_a;d_position=next_d;}failures+=u64(a_position)*ZERO_COUNT+d_position!=u64(start);auto sorted=orbit;std::sort(sorted.begin(),sorted.end());failures+=std::adjacent_find(sorted.begin(),sorted.end())!=sorted.end();failures+=sorted[0]!=u64(packed);}return out;}
"""

_REPLAY_REPRESENTATIVE_HELPERS = r"""
std::array<u64,45> rep_prefix_orbits(const std::vector<std::uint32_t>&zero,u64&failures){
 std::array<u64,45>result{};
 for(int seed=0;seed<9;++seed){
  int left=0,right=seed;std::array<u64,5>seen{};
  for(int step=0;step<5;++step){
   u64 packed=u64(left)*ZERO_COUNT+right;seen[step]=packed;result[5*seed+step]=packed;
   std::array<int,4>left_tail{},right_tail{};(void)decode_shape(zero[left],&left_tail);(void)decode_shape(zero[right],&right_tail);int beta=2*(left_tail[0]-right_tail[0]);while(beta<0)beta+=5;beta%=5;
   auto advance=[&](int position){std::array<int,4>tail{};(void)decode_shape(zero[position],&tail);std::array<int,5>word{{0,tail[0],tail[1],tail[2],tail[3]}},transformed{};for(int q=1;q<5;++q){int value=word[(q+1)%5]-word[1]+5*beta*q;while(value<0)value+=25;transformed[q]=value%25;}std::uint32_t code=0;for(int q=1;q<5;++q)code=25*code+transformed[q];for(int candidate=0;candidate<int(zero.size());++candidate)if(zero[candidate]==code)return candidate;return -1;};
   int next_left=advance(left),next_right=advance(right);if(next_left<0||next_right<0){++failures;next_left=0;next_right=0;}left=next_left;right=next_right;
  }
  failures+=u64(left)*ZERO_COUNT+right!=u64(seed);u64 minimum=seen[0];for(int i=0;i<5;++i){minimum=std::min(minimum,seen[i]);for(int j=i+1;j<5;++j)failures+=seen[i]==seen[j];}failures+=minimum!=u64(seed);
 }
 return result;
}
"""

_PROGRAM = _replace_once(
    _PRIOR_BATCH_PROGRAM,
    "D_POSITIONS{{1,2,3,4}}",
    "D_POSITIONS{{5,6,7,8}}",
)
_PROGRAM = _replace_once(
    _PROGRAM,
    "u64 decomposition_sample_count=0,decomposition_sample_failure_count=0,b_states_completed=0,c_states_completed=0,occupied=0,max_insert_probe=0,max_lookup_probe=0,matches=0,residual_per_match=0,residual_tests=0,survivors=0,decomposition_failures=0,b_checksum=0,c_checksum=0,match_checksum=0,primary_replay_charge=0;",
    "u64 decomposition_sample_count=0,decomposition_sample_failure_count=0,b_states_completed=0,c_states_completed=0,occupied=0,max_insert_probe=0,max_lookup_probe=0,matches=0,residual_per_match=0,residual_tests=0,survivors=0,decomposition_failures=0,b_checksum=0,c_checksum=0,c_key_match_checksum=0,match_checksum=0,primary_replay_charge=0;",
)
_PROGRAM = _replace_once(
    _PROGRAM,
    "result.b_checksum=1469598103934665603ULL;result.c_checksum=1469598103934665603ULL;result.match_checksum=1469598103934665603ULL;",
    "result.b_checksum=1469598103934665603ULL;result.c_checksum=1469598103934665603ULL;result.c_key_match_checksum=1469598103934665603ULL;result.match_checksum=1469598103934665603ULL;",
)
_PROGRAM = _replace_once(
    _PROGRAM,
    "mixc(result.c_checksum,matched,result.charge);result.max_lookup_probe",
    "mixc(result.c_checksum,matched,result.charge);mixc(result.c_key_match_checksum,state,result.charge);mixc(result.c_key_match_checksum,key,result.charge);mixc(result.c_key_match_checksum,matched,result.charge);result.max_lookup_probe",
)
_PROGRAM = _replace_once(
    _PROGRAM,
    '<<prefix<<"c_record_checksum="<<result.c_checksum<<"\\n"<<prefix<<"match_stream_checksum="',
    '<<prefix<<"c_record_checksum="<<result.c_checksum<<"\\n"<<prefix<<"c_key_match_checksum="<<result.c_key_match_checksum<<"\\n"<<prefix<<"match_stream_checksum="',
)
_PROGRAM = _replace_once(_PROGRAM, "\nint main(){", "\n" + _REPRESENTATIVE_HELPERS + "\nint main(){")
_PROGRAM = _replace_once(
    _PROGRAM,
    "if(!prefix_guard)return 71;std::array<int,4>a_exponents{};",
    "if(!prefix_guard)return 71;u64 representative_prefix_failure_count=0;auto representative_prefix_orbits=rep_prefix_orbits(zero,representative_prefix_failure_count);if(representative_prefix_failure_count)return 74;std::array<int,4>a_exponents{};",
)
_PROGRAM = _replace_once(
    _PROGRAM,
    'std::cout<<"\\na_shape_code="<<zero[0]',
    'std::cout<<"\\nrepresentative_prefix_check_count=9\\nrepresentative_prefix_failure_count="<<representative_prefix_failure_count<<"\\nrepresentative_prefix_orbits=";for(std::size_t item=0;item<representative_prefix_orbits.size();++item){if(item)std::cout<<",";std::cout<<representative_prefix_orbits[item];}std::cout<<"\\na_shape_code="<<zero[0]',
)
_PROGRAM = _PROGRAM.replace(
    "NEXT_FOUR_AD5_CONTEXTS_EMPTY", "FIRST_REPRESENTATIVE_JOIN_BATCH_EMPTY"
)

_REPLAY_PROGRAM = (
    _EVALUATOR_SUPPORT
    + _REPLAY_REPRESENTATIVE_HELPERS
    + r"""
#include <algorithm>

struct ReplayContext{int d_position=0;std::uint32_t d_code=0;u64 b_states=0,c_states=0,matches=0,residual_tests=0,survivors=0,decomposition_failures=0,b_checksum=1469598103934665603ULL,c_key_match_checksum=1469598103934665603ULL,match_checksum=1469598103934665603ULL,sort_comparisons=0;std::string status="NOT_STARTED";};

struct PairLess{u64*charge;bool operator()(const std::pair<u64,std::uint32_t>&left,const std::pair<u64,std::uint32_t>&right)const{++*charge;return left<right;}};

int main(){
 struct rlimit limit{};getrlimit(RLIMIT_AS,&limit);if(limit.rlim_cur!=67108864ULL||limit.rlim_max!=67108864ULL)return 70;
 for(int value=0;value<P;++value){POW[value][0]=1;for(int exponent=1;exponent<=25;++exponent)POW[value][exponent]=POW[value][exponent-1]*value%P;}for(int character=0;character<N;++character)for(int index=0;index<N;++index)COEFF[character][index]=coeff(character,index);
 Charges charge;std::vector<std::uint32_t>zero;zero.reserve(ZERO_COUNT);for(std::uint32_t code=0;code<390625;++code){auto shape=decode_shape(code);if(!cyclic_bucket(shape,charge))zero.push_back(code);}if(zero.size()!=ZERO_COUNT)return 71;
 std::array<std::uint32_t,9>expected{{280,281,480,489,506,514,752,753,1202}};for(int position=0;position<9;++position)if(zero[position]!=expected[position])return 72;
 u64 representative_prefix_failure_count=0;auto representative_prefix_orbits=rep_prefix_orbits(zero,representative_prefix_failure_count);if(representative_prefix_failure_count)return 73;
 auto a_shape=decode_shape(zero[0]);Block A{0,1,&a_shape};std::array<ReplayContext,4>contexts{};const std::array<int,4>positions{{5,6,7,8}};
 for(int ordinal=0;ordinal<4;++ordinal){auto&result=contexts[ordinal];result.d_position=positions[ordinal];result.d_code=zero[result.d_position];auto d_shape=decode_shape(result.d_code);Block D{3,1,&d_shape};std::vector<std::pair<u64,std::uint32_t>>records;records.reserve(NB);std::uint32_t state=0;
  for(int jb=0;jb<5;++jb)for(int h=0;h<5;++h){int scale=pw(GEN,4*jb+20*PHASE_ORDER[h]);for(std::uint32_t shape_position=0;shape_position<zero.size();++shape_position,++state){auto shape=decode_shape(zero[shape_position]);Block B{1,scale,&shape};u64 key=pack5(b_key(B,D,A,charge));mixc(result.b_checksum,state,charge);mixc(result.b_checksum,key,charge);records.emplace_back(key,state);}}result.b_states=state;if(state!=NB)return 74;
  std::sort(records.begin(),records.end(),PairLess{&result.sort_comparisons});state=0;
  for(int jc=0;jc<5&&!result.survivors;++jc){int scale=pw(GEN,4*jc);for(std::uint32_t shape_position=0;shape_position<zero.size()&&!result.survivors;++shape_position,++state){auto shape=decode_shape(zero[shape_position]);Block C{2,scale,&shape};u64 key=pack5(c_key(C,D,A,charge));auto lower=std::lower_bound(records.begin(),records.end(),key,[&](const auto&item,u64 value){++result.sort_comparisons;return item.first<value;}),upper=std::upper_bound(lower,records.end(),key,[&](u64 value,const auto&item){++result.sort_comparisons;return value<item.first;});u64 matched=upper-lower;result.matches+=matched;mixc(result.c_key_match_checksum,state,charge);mixc(result.c_key_match_checksum,key,charge);mixc(result.c_key_match_checksum,matched,charge);for(auto found=upper;found!=lower&&!result.survivors;){--found;std::uint32_t bid=found->second;++result.residual_tests;mixc(result.match_checksum,state,charge);mixc(result.match_checksum,bid,charge);int work=bid/zero.size(),bshape_position=bid%zero.size(),h=work%5,jb=work/5,bscale=pw(GEN,4*jb+20*PHASE_ORDER[h]);auto bshape=decode_shape(zero[bshape_position]);Block B{1,bscale,&bshape};std::array<int,N>x{};put_block(A,x);put_block(B,x);put_block(C,x);put_block(D,x);std::array<int,7>values{};int vi=0;for(int row:std::array<int,7>{{0,5,6,7,8,9,11}})values[vi++]=augmented_value(row,x,charge);if(values[1]||values[2]||values[3]||values[4]||values[6]){++result.decomposition_failures;break;}if(!values[0]&&!values[5])++result.survivors;}}}result.c_states=state+(result.survivors?1:0);result.status=result.decomposition_failures?"DECOMPOSITION_MISMATCH":result.survivors?"WITNESS_PENDING_REPLAY":"CONTEXT_EMPTY";
 }
 u64 sort_comparisons=0;for(const auto&context:contexts)sort_comparisons+=context.sort_comparisons;u64 charged_operations=charge.total()+sort_comparisons;std::cout<<"REASONING_AD5_REPRESENTATIVE_JOIN_REPLAY_V1\nstatus=";bool empty=true;for(const auto&context:contexts)empty=empty&&context.status=="CONTEXT_EMPTY";std::cout<<(empty?"FIRST_REPRESENTATIVE_JOIN_BATCH_EMPTY":"REPLAY_BATCH_NOT_EMPTY")<<"\nrepresentative_prefix_check_count=9\nrepresentative_prefix_failure_count="<<representative_prefix_failure_count<<"\nrepresentative_prefix_orbits=";for(std::size_t item=0;item<representative_prefix_orbits.size();++item){if(item)std::cout<<",";std::cout<<representative_prefix_orbits[item];}std::cout<<"\ncharged_operations="<<charged_operations<<"\nsort_comparisons="<<sort_comparisons<<"\nrlimit_as_soft="<<limit.rlim_cur<<"\nrlimit_as_hard="<<limit.rlim_max<<"\nvm_peak_kib="<<vm_peak_kib()<<"\n";
 for(int ordinal=0;ordinal<4;++ordinal){const auto&result=contexts[ordinal];std::string prefix="context_"+std::to_string(ordinal)+"_";std::cout<<prefix<<"status="<<result.status<<"\n"<<prefix<<"d_position="<<result.d_position<<"\n"<<prefix<<"d_code="<<result.d_code<<"\n"<<prefix<<"b_states_completed="<<result.b_states<<"\n"<<prefix<<"c_states_completed="<<result.c_states<<"\n"<<prefix<<"exact_key_match_count="<<result.matches<<"\n"<<prefix<<"residual_tests_completed="<<result.residual_tests<<"\n"<<prefix<<"residual_survivor_count="<<result.survivors<<"\n"<<prefix<<"decomposition_failure_count="<<result.decomposition_failures<<"\n"<<prefix<<"b_record_checksum="<<result.b_checksum<<"\n"<<prefix<<"c_key_match_checksum="<<result.c_key_match_checksum<<"\n"<<prefix<<"match_stream_checksum="<<result.match_checksum<<"\n";}
 std::cout<<"REASONING_END\n";
}
"""
)


def _parse_csv_ints(value: str, *, count: int, label: str) -> list[int]:
    try:
        parsed = [int(item) for item in value.split(",")]
    except ValueError as error:
        raise ScientificToolError(f"AD5 representative batch has invalid {label}") from error
    if len(parsed) != count:
        raise ScientificToolError(f"AD5 representative batch has malformed {label}")
    return parsed


def _parse_program_transcript(
    stdout: str,
    *,
    representative_prefix_check_count: int = 9,
) -> dict[str, Any]:
    extra_names = {
        "representative_prefix_check_count",
        "representative_prefix_failure_count",
        "representative_prefix_orbits",
        *(f"context_{ordinal}_c_key_match_checksum" for ordinal in range(_CONTEXT_COUNT)),
    }
    extras: dict[str, str] = {}
    base_lines = []
    for line in stdout.strip().splitlines():
        name = line.split("=", 1)[0]
        if name in extra_names:
            if "=" not in line or name in extras:
                raise ScientificToolError("AD5 representative batch has malformed or duplicate extensions")
            extras[name] = line.split("=", 1)[1]
        else:
            base_lines.append(line)
    if set(extras) != extra_names:
        raise ScientificToolError("AD5 representative batch transcript lacks exact extensions")
    parsed = _parse_prior_batch_transcript("\n".join(base_lines) + "\n")
    try:
        parsed["representative_prefix_check_count"] = int(extras["representative_prefix_check_count"])
        parsed["representative_prefix_failure_count"] = int(extras["representative_prefix_failure_count"])
        checksums = [int(extras[f"context_{ordinal}_c_key_match_checksum"]) for ordinal in range(_CONTEXT_COUNT)]
    except ValueError as error:
        raise ScientificToolError("AD5 representative batch has nonintegral extensions") from error
    parsed["representative_prefix_orbits"] = _parse_csv_ints(
        extras["representative_prefix_orbits"],
        count=5 * representative_prefix_check_count,
        label="representative prefix",
    )
    for ordinal, context in enumerate(parsed["contexts"]):
        context["c_key_match_checksum"] = checksums[ordinal]
        context["batch_local_ordinal"] = ordinal
        context["packed_representative"] = context["d_position"]
    return parsed


def _parse_replay_transcript(
    stdout: str,
    *,
    representative_prefix_check_count: int = 9,
) -> dict[str, Any]:
    lines = stdout.strip().splitlines()
    if len(lines) < 3 or lines[0] != "REASONING_AD5_REPRESENTATIVE_JOIN_REPLAY_V1" or lines[-1] != "REASONING_END":
        raise ScientificToolError("AD5 representative replay transcript has invalid framing")
    fields: dict[str, str] = {}
    for line in lines[1:-1]:
        name, separator, value = line.partition("=")
        if not separator or name in fields:
            raise ScientificToolError("AD5 representative replay has malformed or duplicate fields")
        fields[name] = value
    global_integer_names = {
        "representative_prefix_check_count",
        "representative_prefix_failure_count",
        "charged_operations",
        "sort_comparisons",
        "rlimit_as_soft",
        "rlimit_as_hard",
        "vm_peak_kib",
    }
    context_integer_names = {
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
    }
    expected = {"status", "representative_prefix_orbits", *global_integer_names}
    for ordinal in range(_CONTEXT_COUNT):
        expected.add(f"context_{ordinal}_status")
        expected.update(f"context_{ordinal}_{name}" for name in context_integer_names)
    if set(fields) != expected:
        raise ScientificToolError("AD5 representative replay transcript is incomplete")
    integer_fields = global_integer_names | {
        f"context_{ordinal}_{name}" for ordinal in range(_CONTEXT_COUNT) for name in context_integer_names
    }
    try:
        integers = {name: int(fields[name]) for name in integer_fields}
    except ValueError as error:
        raise ScientificToolError("AD5 representative replay has nonintegral fields") from error
    replay: dict[str, Any] = {name: integers[name] for name in global_integer_names}
    replay["status"] = fields["status"]
    replay["representative_prefix_orbits"] = _parse_csv_ints(
        fields["representative_prefix_orbits"],
        count=5 * representative_prefix_check_count,
        label="replay representative prefix",
    )
    replay["contexts"] = []
    for ordinal in range(_CONTEXT_COUNT):
        prefix = f"context_{ordinal}_"
        context = {name: integers[f"{prefix}{name}"] for name in context_integer_names}
        context.update(
            {
                "status": fields[f"{prefix}status"],
                "batch_local_ordinal": ordinal,
                "packed_representative": context["d_position"],
            }
        )
        replay["contexts"].append(context)
    return replay


def _run_program() -> dict[str, Any]:
    result = _run_source_hashed_program(
        _PROGRAM,
        label="ad5-representative-join-batch-5-8",
        timeout_seconds=900,
        runtime_address_space_bytes=_RESIDENT_BYTE_CAP,
        preserve_exact_transcript=True,
    )
    return {**result, **_parse_program_transcript(result["transcript"]),
            "mathematical_transcript_sha256": bounded_transcript_sha256(result["transcript"], parser=_parse_program_transcript)}


def _run_replay() -> dict[str, Any]:
    result = _run_source_hashed_program(
        _REPLAY_PROGRAM,
        label="ad5-representative-join-batch-5-8-replay",
        timeout_seconds=900,
        runtime_address_space_bytes=_RESIDENT_BYTE_CAP,
        preserve_exact_transcript=True,
    )
    return {**result, **_parse_replay_transcript(result["transcript"]),
            "mathematical_transcript_sha256": bounded_transcript_sha256(result["transcript"], parser=_parse_replay_transcript)}


def _representative_prefix_sha256(orbits: list[int]) -> str:
    digest = hashlib.sha256(b"AD5C3-LOCAL-REPRESENTATIVE-PREFIX-v1\0")
    for value in orbits:
        digest.update(value.to_bytes(4, "little"))
    return digest.hexdigest()


def level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_batch_profile() -> dict[str, Any]:
    """Exhaust the exact joins for canonical representatives 5 through 8."""

    covariance = level25_heisenberg_augmented_four_block_ad5_coordinate3_join_partition_covariance_profile()
    covariance_receipt = hashlib.sha256(canonical_json(covariance).encode()).hexdigest()
    slice_profile = level25_heisenberg_augmented_four_block_ad5_coordinate3_central_orbit_representative_preflight_profile()
    first_context = level25_heisenberg_augmented_four_block_bounded_ad5_profile()
    next_four = level25_heisenberg_augmented_four_block_bounded_ad5_context_batch_profile()
    computation = _run_program()
    replay = _run_replay()
    source_manifest = _source_manifest()
    expected_status = "FIRST_REPRESENTATIVE_JOIN_BATCH_EMPTY"
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
        "first_context_is_the_fresh_reviewed_prerequisite": reviewed_profile_matches(first_context, _AD5_EVIDENCE_SHA256),
        "next_four_are_the_fresh_reviewed_prerequisite": reviewed_profile_matches(next_four, _BATCH_EVIDENCE_SHA256),
        "prior_coverage_and_global_representative_stream_are_bound": first_context["decision"]["context_exhausted"] is True
        and first_context["context"] == {"t": 0, "c_b": 0, "c_c": 0, "c_d": 0, "j_d": 0, "shape_a": 280, "shape_d": 280, "scale_a": 1, "scale_d": 1}
        and next_four["decision"]["complete_target_batch_exhausted"] is True
        and next_four["decision"]["contexts_exhausted"] == [0, 1, 2, 3]
        and next_four["context_prefix"] == [
            {"ordinal": ordinal, "j_d": 0, "scale_a": 1, "scale_d": 1, "shape_a": 280,
             "shape_d": _EXPECTED_CODES[ordinal + 1], "shape_d_position": ordinal + 1} for ordinal in range(4)
        ]
        and computation["zero_bucket_prefix_codes"] == list(_EXPECTED_CODES[:5])
        and all(computation[name] == next_four["computation"][name] == first_context["computation"][name]
                for name in ("shape_count", "zero_bucket_count", "histogram_checksum", "zero_bucket_checksum"))
        and computation["a_shape_code"] == next_four["computation"]["a_shape_code"] == first_context["computation"]["first_zero_shape_code"] == 280
        and computation["a_shape_exponents"] == next_four["computation"]["a_shape_exponents"] == first_context["computation"]["first_zero_shape_exponents"]
        and computation["a_shape_values"] == next_four["computation"]["a_shape_values"] == first_context["computation"]["first_zero_shape_values"]
        and covariance["decision"]["representative_outer_domain_authorized"] is True
        and covariance["computation"]["slice_shape_sha256"] == slice_profile["computation"]["shape_stream_sha256"] == _SLICE_SHAPE_SHA256
        and covariance["computation"]["slice_representative_sha256"] == slice_profile["computation"]["representative_stream_sha256"] == _SLICE_REPRESENTATIVE_SHA256
        and covariance["computation"]["representative_count"] == slice_profile["computation"]["representative_count"] == 3_907_280
        and all(parent["source_manifest"]["records_sha256"] == source_manifest["records_sha256"]
                for parent in (covariance, slice_profile, first_context, next_four)),
        "local_representative_prefix_is_derived_twice": computation[
            "representative_prefix_check_count"
        ]
        == replay["representative_prefix_check_count"]
        == 9
        and computation["representative_prefix_failure_count"]
        == replay["representative_prefix_failure_count"]
        == 0
        and computation["representative_prefix_orbits"]
        == replay["representative_prefix_orbits"]
        and prefix_sha256 == replay_prefix_sha256,
        "contexts_are_exactly_packed_representatives_5_through_8": [
            context["packed_representative"] for context in computation["contexts"]
        ]
        == list(_CONTEXT_POSITIONS)
        and [context["d_code"] for context in computation["contexts"]]
        == list(_EXPECTED_CODES[5:9])
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
            computation, parser=_parse_program_transcript, expected_program=_REVIEWED_PROGRAM_SHA256,
            expected_mathematics=_REVIEWED_MATHEMATICAL_TRANSCRIPT_SHA256,
        ) and bounded_execution_matches(
            replay, parser=_parse_replay_transcript, expected_program=_REVIEWED_REPLAY_PROGRAM_SHA256,
            expected_mathematics=_REVIEWED_REPLAY_MATHEMATICAL_TRANSCRIPT_SHA256,
        ),
        "terminal_status_advances_only_the_atomic_public_cursor": computation[
            "status"
        ]
        == expected_status
        and complete_contexts,
    }
    verified = all(certificate.values())
    return seal_profile_evidence({
        "schema": "reasoning.level25-heisenberg-ad5-coordinate3-representative-join-batch.v2",
        "request": {},
        "request_sha256": hashlib.sha256(canonical_json({}).encode()).hexdigest(),
        "input_manifest": input_manifest,
        "input_manifest_sha256": input_manifest_sha256,
        "review_reference_policy": {"kind": "declared_source_literals", "external_contents_verified": False, "receipt_references_are_current_approvals": False},
        "covariance_receipt_sha256": covariance_receipt,
        "covariance_evidence_sha256": covariance["evidence_sha256"],
        "slice_receipt_sha256": hashlib.sha256(canonical_json(slice_profile).encode()).hexdigest(),
        "slice_evidence_sha256": slice_profile["evidence_sha256"],
        "first_context_receipt_sha256": hashlib.sha256(canonical_json(first_context).encode()).hexdigest(),
        "first_context_evidence_sha256": first_context["evidence_sha256"],
        "next_four_receipt_sha256": hashlib.sha256(canonical_json(next_four).encode()).hexdigest(),
        "next_four_evidence_sha256": next_four["evidence_sha256"],
        "charge_scope": "named primary and independent sorted replay only; prerequisite executions are separate",
        "representative_prefix_sha256": prefix_sha256,
        "context_hashes": context_hashes,
        "source_manifest": source_manifest,
        "computation": computation,
        "independent_sorted_join_replay": replay,
        "actual_combined_charge": computation["aggregate_charge_total"]
        + replay["charged_operations"],
        "certificate": certificate,
        "certificate_verified": verified,
        "decision": {
            "status": computation["status"],
            "accepted_exact": verified,
            "public_cursor_start": 5,
            "public_next_unaccepted_packed": 9 if verified else 5,
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
            "the exact B/C joins for canonical packed representatives 5 through 8. "
            "The public cursor advances atomically to 9 only after exact stream and "
            "residual agreement for all four contexts."
        ),
        "not_certified": [
            "any packed representative at or after 9",
            "an execution or resource price for the complete 3,907,280-representative domain",
            "absence of geometric common zeros over extensions of F_101",
            "a Fourier transform, convolution, tensor contraction, or full contraction",
            "a characteristic-zero lift or global smoothness",
            "a Brauer class, period, index bound, or period-index counterexample",
        ],
        "next_gate": {
            "feature": "level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_batch_9_12_profile",
            "requirement": (
                "Derive the next four canonical packed representatives from the residual action, "
                "then run the same atomic primary and sorted-replay join gate without extrapolating "
                "a full-domain price."
            ),
        },
    })
