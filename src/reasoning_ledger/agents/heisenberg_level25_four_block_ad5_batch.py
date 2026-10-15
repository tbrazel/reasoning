# ruff: noqa: E501
"""Bounded exact AD5 batch on the next four central contexts."""

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
from .heisenberg_level25_four_block_ad5 import (
    _FOURIER_EVIDENCE_SHA256,
    _PHASE_EVIDENCE_SHA256,
    _RESIDENT_BYTE_CAP,
    _run_independent_replay,
    _source_manifest,
)
from .heisenberg_level25_four_block_ad5 import (
    _PROGRAM as _FIRST_CONTEXT_PROGRAM,
)
from .heisenberg_level25_three_block import (
    _address_space_limited_command,
    _run_source_hashed_program,
)
from .heisenberg_level25_three_block_coverage import (
    level25_heisenberg_augmented_four_block_fourier_profile,
    level25_heisenberg_augmented_four_block_phase_quotient_profile,
)
from .science_evidence import (
    bounded_execution_matches,
    bounded_transcript_sha256,
    independent_replay_matches,
    reviewed_profile_matches,
    seal_profile_evidence,
)
from .scientific import ScientificToolError

_PROTOCOL_VERSION = 1
_CONTEXT_COUNT = 4
_CONTEXT_SEARCH_CHARGE_CAP = 54_327_397
_AGGREGATE_SEARCH_CHARGE_CAP = 270_000_000
_PRIMARY_REPLAY_CHARGE_CAP = 10_000_000
_DOMAIN_REPLAY_CHARGE_CAP = 20_000_000
_WITNESS_REPLAY_CHARGE_CAP = 20_000_000
_BATCH_EVIDENCE_SHA256 = "49835bb6dc187a1cabfae48e4a9aa2c7afbe3f9334799d518e5a94a7a0f2d873"
_REVIEWED_PROGRAM_SHA256 = "b413cc1f9f12921e4a93cf05cdcd71394844cde765d15feb7abb3b06fa414b63"
_REVIEWED_MATHEMATICAL_TRANSCRIPT_SHA256 = "1cc298dc0dfcf393d87242d13b266acacabfb660ef3808e0f8578d8e217cfd66"
_REVIEWED_DOMAIN_REPLAY_PROGRAM_SHA256 = "8df30281ce9d058c1d5afd4de2bea2de67f455b4b79c3d2d36b1a635dbb26aa7"
_REVIEWED_DOMAIN_REPLAY_TRANSCRIPT_SHA256 = "2c72e3e1f1c48ed99fb67412009f5a1df55d9d9310137b2d1175441a934671ee"

_SUPPORT, _MAIN_SEPARATOR, _OLD_MAIN = _FIRST_CONTEXT_PROGRAM.partition(
    "\nint main(){"
)
if not _MAIN_SEPARATOR:
    raise RuntimeError("bounded AD5 support program no longer has the expected main")

_PROGRAM = (
    _SUPPORT
    + r"""

constexpr u64 CONTEXT_CAP=54327397ULL;
constexpr int BATCH_CONTEXTS=4;
constexpr std::array<int,BATCH_CONTEXTS> D_POSITIONS{{1,2,3,4}};

void absorb(Charges&target,const Charges&source){target.add+=source.add;target.sub+=source.sub;target.neg+=source.neg;target.mul+=source.mul;target.inverse_lookup+=source.inverse_lookup;target.power_lookup+=source.power_lookup;target.source_record+=source.source_record;target.hash_slot+=source.hash_slot;target.key_compare+=source.key_compare;target.duplicate_link+=source.duplicate_link;target.checksum_word+=source.checksum_word;}

struct ContextResult{
 int ordinal=0,d_position=0;std::uint32_t d_code=0;std::array<int,4>d_exponents{};std::array<int,5>d_values{};std::string status="NOT_STARTED";Charges charge{};
 u64 decomposition_sample_count=0,decomposition_sample_failure_count=0,b_states_completed=0,c_states_completed=0,occupied=0,max_insert_probe=0,max_lookup_probe=0,matches=0,residual_per_match=0,residual_tests=0,survivors=0,decomposition_failures=0,b_checksum=0,c_checksum=0,match_checksum=0,primary_replay_charge=0;
 bool has_witness=false;std::array<int,N>witness{};std::array<int,22>witness_values{};std::array<std::array<int,N>,22>witness_jacobian{};int witness_rank=-1;
};

ContextResult context_metadata(int ordinal,const std::vector<std::uint32_t>&zero){ContextResult result;result.ordinal=ordinal;result.d_position=D_POSITIONS[ordinal];result.d_code=zero[result.d_position];result.d_values=decode_shape(result.d_code,&result.d_exponents);return result;}

ContextResult run_context(ContextResult result,const std::vector<std::uint32_t>&zero,const std::array<int,5>&a_shape){
 result.status="RUNNING";Block A{0,1,&a_shape},D{3,1,&result.d_values};
 for(int sample=0;sample<64;++sample){int jb=sample%5,phase_position=(sample/5)%5,jc=(sample/25)%5;auto bshape=decode_shape(zero[(sample*37)%zero.size()]),cshape=decode_shape(zero[(sample*101+17)%zero.size()]);Block B{1,pw(GEN,4*jb+20*PHASE_ORDER[phase_position]),&bshape},C{2,pw(GEN,4*jc),&cshape};auto bk=b_key(B,D,A,result.charge),ck=c_key(C,D,A,result.charge);std::array<int,N>x{};put_block(A,x);put_block(B,x);put_block(C,x);put_block(D,x);std::array<int,5>rows{{6,8,5,7,11}};bool valid=true;for(int key_index=0;key_index<5;++key_index)if(augmented_value(rows[key_index],x,result.charge)!=(bk[key_index]-ck[key_index]+P)%P)valid=false;int e0=unary_value(0,A,result.charge);e0=plusc(e0,unary_value(0,B,result.charge),result.charge);e0=plusc(e0,unary_value(0,C,result.charge),result.charge);e0=plusc(e0,unary_value(0,D,result.charge),result.charge);e0=plusc(e0,row0_pair_value(A,B,result.charge),result.charge);e0=plusc(e0,row0_pair_value(B,C,result.charge),result.charge);e0=plusc(e0,row0_pair_value(C,D,result.charge),result.charge);if(augmented_value(0,x,result.charge)!=e0)valid=false;int q7=unary_value(9,A,result.charge);q7=plusc(q7,unary_value(9,B,result.charge),result.charge);q7=plusc(q7,unary_value(9,C,result.charge),result.charge);q7=plusc(q7,unary_value(9,D,result.charge),result.charge);q7=plusc(q7,selected_value(1,A,B,C,result.charge),result.charge);q7=plusc(q7,selected_value(1,B,C,D,result.charge),result.charge);if(augmented_value(9,x,result.charge)!=q7)valid=false;++result.decomposition_sample_count;if(!valid)++result.decomposition_sample_failure_count;if(result.charge.total()>CONTEXT_CAP){result.status="CONTEXT_SEARCH_CAP_ABORT";return result;}}
 if(result.decomposition_sample_failure_count){result.status="DECOMPOSITION_MISMATCH";return result;}
 std::vector<u64>keys(SLOTS,EMPTY);std::vector<std::uint32_t>heads(SLOTS,std::numeric_limits<std::uint32_t>::max()),counts(SLOTS),next(NB,std::numeric_limits<std::uint32_t>::max()),cslots(NC);std::vector<u64>ckeys(NC);result.b_checksum=1469598103934665603ULL;result.c_checksum=1469598103934665603ULL;result.match_checksum=1469598103934665603ULL;
 std::uint32_t state=0;for(int jb=0;jb<5;++jb)for(int phase_position=0;phase_position<5;++phase_position){int scale=pw(GEN,4*jb+20*PHASE_ORDER[phase_position]);for(std::uint32_t shape_position=0;shape_position<zero.size();++shape_position){auto shape=decode_shape(zero[shape_position]);Block B{1,scale,&shape};u64 key=pack5(b_key(B,D,A,result.charge));mixc(result.b_checksum,state,result.charge);mixc(result.b_checksum,key,result.charge);std::size_t slot=splitmix(key^SPLITMIX_SEED)&(SLOTS-1);u64 probes=0;while(true){++result.charge.hash_slot;++probes;if(keys[slot]==EMPTY){keys[slot]=key;heads[slot]=state;counts[slot]=1;++result.occupied;break;}++result.charge.key_compare;if(keys[slot]==key){next[state]=heads[slot];heads[slot]=state;++counts[slot];break;}slot=(slot+1)&(SLOTS-1);}result.max_insert_probe=std::max(result.max_insert_probe,probes);++state;result.b_states_completed=state;if(result.charge.total()>CONTEXT_CAP){result.status="CONTEXT_SEARCH_CAP_ABORT";return result;}}}
 if(state!=NB){result.status="DOMAIN_COUNT_MISMATCH";return result;}
 state=0;for(int jc=0;jc<5;++jc){int scale=pw(GEN,4*jc);for(std::uint32_t shape_position=0;shape_position<zero.size();++shape_position){auto shape=decode_shape(zero[shape_position]);Block C{2,scale,&shape};u64 key=pack5(c_key(C,D,A,result.charge));std::size_t slot=splitmix(key^SPLITMIX_SEED)&(SLOTS-1);u64 probes=0,matched=0;while(true){++result.charge.hash_slot;++probes;if(keys[slot]==EMPTY)break;++result.charge.key_compare;if(keys[slot]==key){matched=counts[slot];break;}slot=(slot+1)&(SLOTS-1);}ckeys[state]=key;cslots[state]=slot;result.matches+=matched;mixc(result.c_checksum,state,result.charge);mixc(result.c_checksum,key,result.charge);mixc(result.c_checksum,probes,result.charge);mixc(result.c_checksum,matched,result.charge);result.max_lookup_probe=std::max(result.max_lookup_probe,probes);++state;result.c_states_completed=state;if(result.charge.total()>CONTEXT_CAP){result.status="CONTEXT_SEARCH_CAP_ABORT";return result;}}}
 if(state!=NC){result.status="DOMAIN_COUNT_MISMATCH";return result;}
 Charges one_residual;std::array<int,N>dummy{};for(int row:std::array<int,7>{{0,5,6,7,8,9,11}})(void)augmented_value(row,dummy,one_residual);result.residual_per_match=one_residual.total()+3;u64 remaining=result.charge.total()<=CONTEXT_CAP?CONTEXT_CAP-result.charge.total():0;bool fanout=result.matches&&remaining/result.residual_per_match<result.matches;if(fanout){result.status="CONTEXT_FANOUT_PROFILED";return result;}
 for(std::uint32_t cid=0;cid<NC&&!result.survivors;++cid){std::size_t slot=cslots[cid];if(keys[slot]==EMPTY||keys[slot]!=ckeys[cid])continue;int jc=cid/zero.size(),shape_position=cid%zero.size(),cscale=pw(GEN,4*jc);auto cshape=decode_shape(zero[shape_position]);Block C{2,cscale,&cshape};for(std::uint32_t bid=heads[slot];bid!=std::numeric_limits<std::uint32_t>::max();bid=next[bid]){++result.charge.duplicate_link;++result.residual_tests;mixc(result.match_checksum,cid,result.charge);mixc(result.match_checksum,bid,result.charge);int work=bid/zero.size(),bshape_position=bid%zero.size(),phase_position=work%5,jb=work/5,bscale=pw(GEN,4*jb+20*PHASE_ORDER[phase_position]);auto bshape=decode_shape(zero[bshape_position]);Block B{1,bscale,&bshape};std::array<int,N>x{};put_block(A,x);put_block(B,x);put_block(C,x);put_block(D,x);std::array<int,7>values{};int vi=0;for(int row:std::array<int,7>{{0,5,6,7,8,9,11}})values[vi++]=augmented_value(row,x,result.charge);if(values[1]||values[2]||values[3]||values[4]||values[6]){++result.decomposition_failures;break;}if(values[0]||values[5])continue;++result.survivors;result.has_witness=true;result.witness=x;Charges replay;for(int row=0;row<22;++row)result.witness_values[row]=augmented_value(row,x,replay);result.witness_jacobian=augmented_jacobian(x);result.witness_rank=matrix_rank(result.witness_jacobian);result.primary_replay_charge=replay.total()+250000;break;}}
 if(result.decomposition_failures)result.status="DECOMPOSITION_MISMATCH";else if(result.survivors)result.status=result.witness_rank<22?"PRIMARY_SINGULAR_WITNESS_PENDING_INDEPENDENT_REPLAY":"PRIMARY_COMMON_ZERO_RANK_22_PENDING_INDEPENDENT_REPLAY";else result.status="CONTEXT_EMPTY";return result;
}

void print_charges(const std::string&prefix,const Charges&charge){std::cout<<prefix<<"add_charges="<<charge.add<<"\n"<<prefix<<"subtract_charges="<<charge.sub<<"\n"<<prefix<<"negate_charges="<<charge.neg<<"\n"<<prefix<<"multiply_charges="<<charge.mul<<"\n"<<prefix<<"inverse_lookup_charges="<<charge.inverse_lookup<<"\n"<<prefix<<"power_lookup_charges="<<charge.power_lookup<<"\n"<<prefix<<"source_record_charges="<<charge.source_record<<"\n"<<prefix<<"hash_slot_charges="<<charge.hash_slot<<"\n"<<prefix<<"key_compare_charges="<<charge.key_compare<<"\n"<<prefix<<"duplicate_link_charges="<<charge.duplicate_link<<"\n"<<prefix<<"checksum_word_charges="<<charge.checksum_word<<"\n"<<prefix<<"charge_total="<<charge.total()<<"\n";}

int main(){
 struct rlimit limit{};getrlimit(RLIMIT_AS,&limit);if(limit.rlim_cur!=67108864ULL||limit.rlim_max!=67108864ULL)return 70;
 for(int value=0;value<P;++value){POW[value][0]=1;for(int exponent=1;exponent<=25;++exponent)POW[value][exponent]=POW[value][exponent-1]*value%P;}for(int character=0;character<N;++character)for(int index=0;index<N;++index)COEFF[character][index]=coeff(character,index);
 Charges fixed;std::array<u64,P>histogram{};std::vector<std::uint32_t>zero;zero.reserve(ZERO_COUNT);u64 histogram_checksum=1469598103934665603ULL,zero_checksum=1469598103934665603ULL;
 for(std::uint32_t code=0;code<390625;++code){auto shape=decode_shape(code);int bucket=cyclic_bucket(shape,fixed);++histogram[bucket];if(!bucket)zero.push_back(code);}for(int bucket=0;bucket<P;++bucket){mixc(histogram_checksum,bucket,fixed);mixc(histogram_checksum,histogram[bucket],fixed);}for(auto code:zero)mixc(zero_checksum,code,fixed);
 std::array<std::uint32_t,5>expected_codes{{280,281,480,489,506}};bool prefix_guard=zero.size()==ZERO_COUNT;for(int position=0;position<5&&prefix_guard;++position)prefix_guard=zero[position]==expected_codes[position];if(!prefix_guard)return 71;std::array<int,4>a_exponents{};auto a_shape=decode_shape(zero[0],&a_exponents);if(a_exponents!=std::array<int,4>{{0,0,11,5}}||a_shape!=std::array<int,5>{{1,1,1,79,95}})return 72;
 u64 reserved_upper_bound=fixed.total()+u64(BATCH_CONTEXTS)*CONTEXT_CAP;if(reserved_upper_bound>SEARCH_CAP)return 73;
 std::array<ContextResult,BATCH_CONTEXTS>contexts{};for(int ordinal=0;ordinal<BATCH_CONTEXTS;++ordinal)contexts[ordinal]=context_metadata(ordinal,zero);int contexts_started=0,contexts_exhausted=0,witness_context=-1;std::string batch_status="NEXT_FOUR_AD5_CONTEXTS_EMPTY";
 for(int ordinal=0;ordinal<BATCH_CONTEXTS;++ordinal){contexts[ordinal]=run_context(contexts[ordinal],zero,a_shape);++contexts_started;if(contexts[ordinal].status=="CONTEXT_EMPTY"){++contexts_exhausted;continue;}batch_status=contexts[ordinal].status=="CONTEXT_FANOUT_PROFILED"?"BATCH_PREFIX_FANOUT_PROFILED":contexts[ordinal].status;if(contexts[ordinal].has_witness)witness_context=ordinal;break;}
 if(contexts_exhausted!=BATCH_CONTEXTS&&batch_status=="NEXT_FOUR_AD5_CONTEXTS_EMPTY")batch_status="BATCH_PREFIX_INCOMPLETE";Charges aggregate=fixed;for(int ordinal=0;ordinal<contexts_started;++ordinal)absorb(aggregate,contexts[ordinal].charge);
 std::cout<<"REASONING_BOUNDED_AD5_CONTEXT_BATCH_V1\nstatus="<<batch_status<<"\nfield_prime=101\nsource_record_count=725\nshape_count=390625\nzero_bucket_count="<<zero.size()<<"\nhistogram_checksum="<<histogram_checksum<<"\nzero_bucket_checksum="<<zero_checksum<<"\nzero_bucket_prefix_codes=";for(int position=0;position<5;++position){if(position)std::cout<<",";std::cout<<zero[position];}std::cout<<"\na_shape_code="<<zero[0]<<"\na_shape_exponents=";print_list(a_exponents);std::cout<<"a_shape_values=";print_list(a_shape);std::cout<<"context_count_target="<<BATCH_CONTEXTS<<"\ncontexts_started="<<contexts_started<<"\ncontexts_exhausted="<<contexts_exhausted<<"\ncontext_search_charge_cap="<<CONTEXT_CAP<<"\naggregate_search_charge_cap="<<SEARCH_CAP<<"\nreserved_upper_bound="<<reserved_upper_bound<<"\n";print_charges("fixed_",fixed);print_charges("aggregate_",aggregate);
 for(int ordinal=0;ordinal<BATCH_CONTEXTS;++ordinal){const auto&result=contexts[ordinal];std::string prefix="context_"+std::to_string(ordinal)+"_";std::cout<<prefix<<"status="<<result.status<<"\n"<<prefix<<"d_position="<<result.d_position<<"\n"<<prefix<<"d_code="<<result.d_code<<"\n"<<prefix<<"d_exponents=";print_list(result.d_exponents);std::cout<<prefix<<"d_values=";print_list(result.d_values);std::cout<<prefix<<"decomposition_sample_count="<<result.decomposition_sample_count<<"\n"<<prefix<<"decomposition_sample_failure_count="<<result.decomposition_sample_failure_count<<"\n"<<prefix<<"b_states_completed="<<result.b_states_completed<<"\n"<<prefix<<"c_states_completed="<<result.c_states_completed<<"\n"<<prefix<<"hash_occupied_count="<<result.occupied<<"\n"<<prefix<<"maximum_insert_probe="<<result.max_insert_probe<<"\n"<<prefix<<"maximum_lookup_probe="<<result.max_lookup_probe<<"\n"<<prefix<<"exact_key_match_count="<<result.matches<<"\n"<<prefix<<"residual_charge_per_match="<<result.residual_per_match<<"\n"<<prefix<<"residual_tests_completed="<<result.residual_tests<<"\n"<<prefix<<"residual_survivor_count="<<result.survivors<<"\n"<<prefix<<"decomposition_failure_count="<<result.decomposition_failures<<"\n"<<prefix<<"b_record_checksum="<<result.b_checksum<<"\n"<<prefix<<"c_record_checksum="<<result.c_checksum<<"\n"<<prefix<<"match_stream_checksum="<<result.match_checksum<<"\n"<<prefix<<"primary_replay_charge_total="<<result.primary_replay_charge<<"\n";print_charges(prefix,result.charge);}
 int witness_rank=-1;std::array<int,N>witness{};std::array<int,22>witness_values{};std::array<std::array<int,N>,22>witness_jacobian{};if(witness_context>=0){witness_rank=contexts[witness_context].witness_rank;witness=contexts[witness_context].witness;witness_values=contexts[witness_context].witness_values;witness_jacobian=contexts[witness_context].witness_jacobian;}
 std::cout<<"primary_replay_charge_cap=10000000\ndomain_replay_charge_cap=20000000\nwitness_replay_charge_cap=20000000\nrlimit_as_soft="<<limit.rlim_cur<<"\nrlimit_as_hard="<<limit.rlim_max<<"\nvm_peak_kib="<<vm_peak_kib()<<"\nwitness_context_ordinal="<<witness_context<<"\nwitness_rank="<<witness_rank<<"\nwitness_coordinates=";if(witness_context>=0)print_list(witness);else std::cout<<"\n";std::cout<<"witness_equation_values=";if(witness_context>=0)print_list(witness_values);else std::cout<<"\n";std::cout<<"witness_jacobian=";if(witness_context>=0){bool first=true;for(auto&row:witness_jacobian)for(int value:row){if(!first)std::cout<<",";first=false;std::cout<<value;}std::cout<<"\n";}else std::cout<<"\n";std::cout<<"REASONING_END\n";
}
"""
)

_CHARGE_SUFFIXES = {
    "add_charges",
    "subtract_charges",
    "negate_charges",
    "multiply_charges",
    "inverse_lookup_charges",
    "power_lookup_charges",
    "source_record_charges",
    "hash_slot_charges",
    "key_compare_charges",
    "duplicate_link_charges",
    "checksum_word_charges",
    "charge_total",
}
_CONTEXT_INTEGER_SUFFIXES = {
    "d_position",
    "d_code",
    "decomposition_sample_count",
    "decomposition_sample_failure_count",
    "b_states_completed",
    "c_states_completed",
    "hash_occupied_count",
    "maximum_insert_probe",
    "maximum_lookup_probe",
    "exact_key_match_count",
    "residual_charge_per_match",
    "residual_tests_completed",
    "residual_survivor_count",
    "decomposition_failure_count",
    "b_record_checksum",
    "c_record_checksum",
    "match_stream_checksum",
    "primary_replay_charge_total",
} | _CHARGE_SUFFIXES
_GLOBAL_INTEGER_FIELDS = {
    "field_prime",
    "source_record_count",
    "shape_count",
    "zero_bucket_count",
    "histogram_checksum",
    "zero_bucket_checksum",
    "a_shape_code",
    "context_count_target",
    "contexts_started",
    "contexts_exhausted",
    "context_search_charge_cap",
    "aggregate_search_charge_cap",
    "reserved_upper_bound",
    "primary_replay_charge_cap",
    "domain_replay_charge_cap",
    "witness_replay_charge_cap",
    "rlimit_as_soft",
    "rlimit_as_hard",
    "vm_peak_kib",
    "witness_context_ordinal",
    "witness_rank",
} | {f"fixed_{suffix}" for suffix in _CHARGE_SUFFIXES} | {
    f"aggregate_{suffix}" for suffix in _CHARGE_SUFFIXES
}


def _csv_ints(value: str, *, count: int | tuple[int, ...], label: str) -> list[int]:
    if not value:
        parsed: list[int] = []
    else:
        try:
            parsed = [int(item) for item in value.split(",")]
        except ValueError as error:
            raise ScientificToolError(
                f"bounded AD5 batch transcript has a nonintegral {label}"
            ) from error
    allowed = (count,) if isinstance(count, int) else count
    if len(parsed) not in allowed:
        raise ScientificToolError(
            f"bounded AD5 batch transcript has a malformed {label}"
        )
    return parsed


def _parse_transcript(stdout: str) -> dict[str, Any]:
    lines = stdout.strip().splitlines()
    frame = f"REASONING_BOUNDED_AD5_CONTEXT_BATCH_V{_PROTOCOL_VERSION}"
    if len(lines) < 3 or lines[0] != frame or lines[-1] != "REASONING_END":
        raise ScientificToolError("bounded AD5 batch transcript has invalid framing")
    fields: dict[str, str] = {}
    for line in lines[1:-1]:
        if "=" not in line:
            raise ScientificToolError(
                "bounded AD5 batch transcript has an invalid field"
            )
        name, value = line.split("=", 1)
        if name in fields:
            raise ScientificToolError(
                "bounded AD5 batch transcript has duplicate fields"
            )
        fields[name] = value

    expected = set(_GLOBAL_INTEGER_FIELDS) | {
        "status",
        "zero_bucket_prefix_codes",
        "a_shape_exponents",
        "a_shape_values",
        "witness_coordinates",
        "witness_equation_values",
        "witness_jacobian",
    }
    for ordinal in range(_CONTEXT_COUNT):
        expected.add(f"context_{ordinal}_status")
        expected.add(f"context_{ordinal}_d_exponents")
        expected.add(f"context_{ordinal}_d_values")
        expected.update(
            f"context_{ordinal}_{suffix}"
            for suffix in _CONTEXT_INTEGER_SUFFIXES
        )
    if set(fields) != expected:
        raise ScientificToolError("bounded AD5 batch transcript is incomplete")
    try:
        parsed: dict[str, Any] = {
            name: int(fields[name]) for name in _GLOBAL_INTEGER_FIELDS
        }
    except ValueError as error:
        raise ScientificToolError(
            "bounded AD5 batch transcript has a nonintegral global field"
        ) from error
    parsed.update(
        {
            "status": fields["status"],
            "zero_bucket_prefix_codes": _csv_ints(
                fields["zero_bucket_prefix_codes"],
                count=5,
                label="zero-bucket prefix",
            ),
            "a_shape_exponents": _csv_ints(
                fields["a_shape_exponents"], count=4, label="A exponents"
            ),
            "a_shape_values": _csv_ints(
                fields["a_shape_values"], count=5, label="A values"
            ),
            "witness_coordinates": _csv_ints(
                fields["witness_coordinates"], count=(0, 25), label="witness"
            ),
            "witness_equation_values": _csv_ints(
                fields["witness_equation_values"],
                count=(0, 22),
                label="witness equations",
            ),
            "witness_jacobian": _csv_ints(
                fields["witness_jacobian"],
                count=(0, 22 * 25),
                label="witness Jacobian",
            ),
        }
    )
    contexts = []
    for ordinal in range(_CONTEXT_COUNT):
        prefix = f"context_{ordinal}_"
        try:
            context = {
                suffix: int(fields[f"{prefix}{suffix}"])
                for suffix in _CONTEXT_INTEGER_SUFFIXES
            }
        except ValueError as error:
            raise ScientificToolError(
                "bounded AD5 batch transcript has a nonintegral context field"
            ) from error
        context.update(
            {
                "ordinal": ordinal,
                "status": fields[f"{prefix}status"],
                "d_exponents": _csv_ints(
                    fields[f"{prefix}d_exponents"],
                    count=4,
                    label=f"context {ordinal} D exponents",
                ),
                "d_values": _csv_ints(
                    fields[f"{prefix}d_values"],
                    count=5,
                    label=f"context {ordinal} D values",
                ),
            }
        )
        contexts.append(context)
    parsed["contexts"] = contexts
    return parsed


def _run_program() -> dict[str, Any]:
    computation = _run_source_hashed_program(
        _PROGRAM,
        label="bounded-ad5-next-four-contexts",
        timeout_seconds=900,
        runtime_address_space_bytes=_RESIDENT_BYTE_CAP,
        preserve_exact_transcript=True,
    )
    return {
        **computation,
        **_parse_transcript(computation["transcript"]),
        "mathematical_transcript_sha256": bounded_transcript_sha256(computation["transcript"], parser=_parse_transcript),
    }


def _run_domain_replay() -> dict[str, Any]:
    replay_path = Path(__file__).with_name(
        "heisenberg_level25_four_block_ad5_batch_replay.py"
    )
    try:
        program = replay_path.read_bytes()
    except OSError as error:
        raise ScientificToolError(
            f"independent bounded AD5 batch domain replay is unavailable: {error}"
        ) from error
    replay_python = (
        shutil.which("python3", path="/usr/local/bin:/usr/bin:/bin") or sys.executable
    )
    command = _address_space_limited_command(
        [replay_python, "-I", "-S", str(replay_path)],
        _RESIDENT_BYTE_CAP,
    )
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
            timeout=300,
            env=environment,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise ScientificToolError(
            "independent bounded AD5 batch domain replay exceeded 300 seconds"
        ) from error
    except OSError as error:
        raise ScientificToolError(
            f"independent bounded AD5 batch domain replay could not start: {error}"
        ) from error
    if completed.returncode or completed.stderr.strip():
        detail = (
            completed.stderr.strip()
            or completed.stdout.strip()
            or "unknown replay failure"
        )[:2000]
        raise ScientificToolError(
            f"independent bounded AD5 batch domain replay failed: {detail}"
        )
    try:
        replay = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise ScientificToolError(
            "independent bounded AD5 batch domain replay returned invalid JSON"
        ) from error
    if not isinstance(replay, dict):
        raise ScientificToolError(
            "independent bounded AD5 batch domain replay returned a non-object"
        )
    return {
        **replay,
        "program_sha256": hashlib.sha256(program).hexdigest(),
        "transcript_sha256": hashlib.sha256(completed.stdout.encode()).hexdigest(),
        "transcript": completed.stdout,
        "runtime_address_space_limit_bytes": _RESIDENT_BYTE_CAP,
        "interpreter": str(Path(replay_python).resolve()),
    }


def level25_heisenberg_augmented_four_block_bounded_ad5_context_batch_profile() -> dict[
    str, Any
]:
    """Exhaust or profile the next four deterministic AD5 central contexts."""

    fourier = level25_heisenberg_augmented_four_block_fourier_profile()
    fourier_sha256 = hashlib.sha256(canonical_json(fourier).encode()).hexdigest()
    phase = level25_heisenberg_augmented_four_block_phase_quotient_profile()
    phase_sha256 = hashlib.sha256(canonical_json(phase).encode()).hexdigest()
    computation = _run_program()
    source_manifest = _source_manifest()
    domain_replay = _run_domain_replay()
    witness = computation["witness_coordinates"]
    witness_replay = _run_independent_replay(witness) if witness else None

    expected_prefix = [
        {
            "position": 0,
            "code": 280,
            "exponents": [0, 0, 11, 5],
            "values": [1, 1, 1, 79, 95],
        },
        {
            "position": 1,
            "code": 281,
            "exponents": [0, 0, 11, 6],
            "values": [1, 1, 1, 79, 71],
        },
        {
            "position": 2,
            "code": 480,
            "exponents": [0, 0, 19, 5],
            "values": [1, 1, 1, 37, 95],
        },
        {
            "position": 3,
            "code": 489,
            "exponents": [0, 0, 19, 14],
            "values": [1, 1, 1, 37, 78],
        },
        {
            "position": 4,
            "code": 506,
            "exponents": [0, 0, 20, 6],
            "values": [1, 1, 1, 84, 71],
        },
    ]
    context_prefix = [
        {
            "ordinal": context["ordinal"],
            "j_d": 0,
            "scale_a": 1,
            "scale_d": 1,
            "shape_a": 280,
            "shape_d": context["d_code"],
            "shape_d_position": context["d_position"],
        }
        for context in computation["contexts"]
    ]
    domain_agreement = all(
        domain_replay[name] == computation[name]
        for name in (
            "source_record_count",
            "shape_count",
            "zero_bucket_count",
            "histogram_checksum",
            "zero_bucket_checksum",
        )
    ) and domain_replay["zero_bucket_prefix"] == expected_prefix
    witness_agreement = (
        not witness
        or (
            witness_replay is not None
            and witness_replay.get("coordinates") == witness
            and witness_replay.get("equation_values")
            == computation["witness_equation_values"]
            and witness_replay.get("jacobian") == computation["witness_jacobian"]
            and witness_replay.get("rank") == computation["witness_rank"]
        )
    )
    status = computation["status"]
    if (
        status == "PRIMARY_SINGULAR_WITNESS_PENDING_INDEPENDENT_REPLAY"
        and witness_agreement
    ):
        status = "REPLAYED_SINGULAR_WITNESS_FOUND"
    elif (
        status == "PRIMARY_COMMON_ZERO_RANK_22_PENDING_INDEPENDENT_REPLAY"
        and witness_agreement
    ):
        status = "REPLAYED_COMMON_ZERO_RANK_22"

    contexts_atomic = all(
        (
            context["status"] == "CONTEXT_EMPTY"
            and context["residual_tests_completed"]
            == context["exact_key_match_count"]
            and context["residual_survivor_count"] == 0
        )
        or (
            context["status"] == "CONTEXT_FANOUT_PROFILED"
            and context["residual_tests_completed"] == 0
        )
        or context["status"] == "NOT_STARTED"
        or context["residual_survivor_count"] == 1
        for context in computation["contexts"]
    )
    contexts_under_cap = all(
        context["charge_total"] <= computation["context_search_charge_cap"]
        for context in computation["contexts"]
        if context["status"] != "NOT_STARTED"
    )
    primary_replay_charge = sum(
        context["primary_replay_charge_total"]
        for context in computation["contexts"]
    )
    certificate = {
        "corrected_fourier_domain_is_the_certified_prerequisite": reviewed_profile_matches(fourier, _FOURIER_EVIDENCE_SHA256),
        "phase_quotient_is_the_certified_prerequisite": reviewed_profile_matches(phase, _PHASE_EVIDENCE_SHA256)
        and phase["computation"]["canonical_representatives"]
        == [[0, 0, 0, 0], [0, 3, 0, 0], [0, 1, 0, 0], [0, 4, 0, 0], [0, 2, 0, 0]],
        "source_manifest_matches_executable_source": source_manifest[
            "raw_record_count"
        ]
        == computation["source_record_count"]
        == domain_replay["source_record_count"]
        == 725,
        "ordered_zero_bucket_prefix_is_recomputed": computation[
            "zero_bucket_prefix_codes"
        ]
        == [entry["code"] for entry in expected_prefix]
        and domain_agreement,
        "next_four_contexts_follow_the_certified_order": context_prefix
        == [
            {
                "ordinal": ordinal,
                "j_d": 0,
                "scale_a": 1,
                "scale_d": 1,
                "shape_a": 280,
                "shape_d": expected_prefix[ordinal + 1]["code"],
                "shape_d_position": ordinal + 1,
            }
            for ordinal in range(_CONTEXT_COUNT)
        ],
        "fixed_domain_charge_is_shared_once": computation["fixed_charge_total"]
        == domain_replay["charged_operations"]
        == 5_863_997,
        "reserved_batch_bound_respects_aggregate_cap": computation[
            "reserved_upper_bound"
        ]
        == computation["fixed_charge_total"]
        + _CONTEXT_COUNT * _CONTEXT_SEARCH_CHARGE_CAP
        <= _AGGREGATE_SEARCH_CHARGE_CAP
        == computation["aggregate_search_charge_cap"],
        "aggregate_charge_is_exact_sum": computation["aggregate_charge_total"]
        == computation["fixed_charge_total"]
        + sum(
            context["charge_total"]
            for context in computation["contexts"]
            if context["status"] != "NOT_STARTED"
        ),
        "contexts_respect_atomic_fanout_semantics": contexts_atomic,
        "contexts_respect_individual_charge_caps": contexts_under_cap,
        "decomposition_samples_match_direct_source_evaluation": all(
            context["decomposition_sample_count"] == 64
            and context["decomposition_sample_failure_count"] == 0
            and context["decomposition_failure_count"] == 0
            for context in computation["contexts"]
            if context["status"] != "NOT_STARTED"
        ),
        "search_and_replays_respect_hard_caps": computation[
            "aggregate_charge_total"
        ]
        <= _AGGREGATE_SEARCH_CHARGE_CAP
        and primary_replay_charge <= _PRIMARY_REPLAY_CHARGE_CAP
        and domain_replay["charged_operations"] <= _DOMAIN_REPLAY_CHARGE_CAP
        and (
            witness_replay is None
            or witness_replay["charged_operations"] <= _WITNESS_REPLAY_CHARGE_CAP
        )
        and computation["rlimit_as_soft"]
        == computation["rlimit_as_hard"]
        == domain_replay["runtime_address_space_limit_bytes"]
        == _RESIDENT_BYTE_CAP,
        "independent_replay_agrees_when_a_witness_exists": witness_agreement,
        "resident_telemetry_respects_the_address_space_cap": 0 < computation["vm_peak_kib"] * 1024 <= _RESIDENT_BYTE_CAP,
        "program_and_mathematical_transcript_match_reviewed_execution": bounded_execution_matches(
            computation,
            parser=_parse_transcript,
            expected_program=_REVIEWED_PROGRAM_SHA256,
            expected_mathematics=_REVIEWED_MATHEMATICAL_TRANSCRIPT_SHA256,
        ) and independent_replay_matches(
            domain_replay,
            expected_program=_REVIEWED_DOMAIN_REPLAY_PROGRAM_SHA256,
            expected_transcript=_REVIEWED_DOMAIN_REPLAY_TRANSCRIPT_SHA256,
        ),
    }
    request: dict[str, Any] = {}
    profile = {
        "schema": "reasoning.level25-heisenberg-augmented-four-block-bounded-ad5-context-batch-profile.v2",
        "request": request,
        "request_sha256": hashlib.sha256(
            canonical_json(request).encode()
        ).hexdigest(),
        "context_prefix": context_prefix,
        "fourier_receipt_sha256": fourier_sha256,
        "fourier_evidence_sha256": fourier["evidence_sha256"],
        "phase_receipt_sha256": phase_sha256,
        "phase_evidence_sha256": phase["evidence_sha256"],
        "source_manifest": source_manifest,
        "computation": computation,
        "independent_domain_replay": domain_replay,
        "independent_witness_replay": witness_replay,
        "certificate": certificate,
        "certificate_verified": all(certificate.values()),
        "decision": {
            "status": status,
            "smoothness_candidate_rejected": status
            == "REPLAYED_SINGULAR_WITNESS_FOUND",
            "contexts_exhausted": [
                context["ordinal"]
                for context in computation["contexts"]
                if context["status"] == "CONTEXT_EMPTY"
            ],
            "complete_target_batch_exhausted": status
            == "NEXT_FOUR_AD5_CONTEXTS_EMPTY",
        },
        "interpretation": (
            "This source-hashed batch shares the ordered zero-bucket enumeration once, "
            "then executes the next four AD5 central contexts sequentially with exact "
            "five-coordinate joins, atomic residual gates, direct seven-equation replay, "
            "and a separate ordered-domain replay under 64 MiB address-space limits."
        ),
        "not_certified": [
            "the already-exhausted first context beyond its separate receipt",
            "any central context after D-shape position four or any other Fourier fiber",
            "absence of geometric common zeros over extensions of F_101",
            "a characteristic-zero lift, global smoothness, a Brauer class, period, index bound, or period-index counterexample",
        ],
        "next_gate": {
            "feature": "level25_heisenberg_augmented_four_block_bounded_ad5_context_batch_continuation",
            "requirement": "Use the measured per-context fanout and exact fixed/context charge split to choose the next deterministic prefix without weakening replay or route constraints.",
        },
    }
    return seal_profile_evidence(profile)
