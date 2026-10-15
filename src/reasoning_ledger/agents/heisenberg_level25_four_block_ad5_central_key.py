# ruff: noqa: E501
"""Exact AD5 coordinate-three central-key determination diagnostic."""

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
    _source_manifest,
)
from .heisenberg_level25_four_block_ad5_signature import (
    _PROGRAM as _SIGNATURE_PROGRAM,
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
_COMBINED_CHARGE_CAP = 300_000_000
_CENTRAL_KEY_EVIDENCE_SHA256 = "7a3896a48b8e0c1de959856c7f07740df956d63cc568321f4a2052e28c27f403"
_REVIEWED_PROGRAM_SHA256 = "18215d957f43c747650885721729482a354f600e028281452c03cef17ad91db7"
_REVIEWED_MATHEMATICAL_TRANSCRIPT_SHA256 = "ba6cb15d0aea5a3863e2e5538bfd31fe2a9a94e51ea2f802d3422c791928c02e"
_REVIEWED_REPLAY_PROGRAM_SHA256 = "e75117f7df3530a928806f903df51ab9b4de8e6c7a7fde34e4a934d7a67d8b29"
_REVIEWED_REPLAY_TRANSCRIPT_SHA256 = "418d00ebd30bfc27cbe81e56f85e4a4f5bfb017da0a608b23ab0c3b276168030"

_SUPPORT, _MAIN_SEPARATOR, _OLD_MAIN = _SIGNATURE_PROGRAM.partition("\nint main(){")
if not _MAIN_SEPARATOR:
    raise RuntimeError("AD5 signature support program no longer has the expected main")

_PROGRAM = (
    _SUPPORT
    + r"""
#include <tuple>

constexpr int FOURIER_MODULUS=607;
constexpr u64 CENTRAL_KEY_CAP=300000000ULL;
using CentralKey=std::array<std::uint8_t,18>;

struct DRecord{
 Signature signature{};
 CentralKey key{};
 u64 state=0;
 bool operator<(const DRecord&other)const{return std::tie(signature,key,state)<std::tie(other.signature,other.key,other.state);}
};

void hash_u64_be(Sha256&hash,u64 value,u64&charge){for(int byte=7;byte>=0;--byte)hash_byte(hash,(value>>(8*byte))&255,charge);}
void hash_signature_be(Sha256&hash,const Signature&value,u64&charge){for(auto item:value)hash_byte(hash,item,charge);}
void hash_central_key(Sha256&hash,const CentralKey&value,u64&charge){for(auto item:value)hash_byte(hash,item,charge);}
std::string signature_digest(const Signature&value){Sha256 hash;for(auto item:value)hash.byte(item);return hash.finish();}
std::string key_digest(const CentralKey&value){Sha256 hash;for(auto item:value)hash.byte(item);return hash.finish();}

Signature coordinates(const Basis&basis,const Feature&row,Feature&reconstructed,Charges&charge,u64&failures){
 Signature result{};int position=0;for(int pivot=0;pivot<FEATURE_DIM;++pivot)if(basis.used[pivot]){int coefficient=row[pivot];result[position++]=coefficient;for(int column=0;column<FEATURE_DIM;++column){++charge.mul;int term=coefficient*basis.rows[pivot][column]%P;++charge.add;reconstructed[column]+=term;if(reconstructed[column]>=P)reconstructed[column]-=P;}}
 if(reconstructed!=row)++failures;return result;
}

int mod_power(int base,u64 exponent,u64&charge){int value=1;while(exponent){if(exponent&1){value=int(u64(value)*base%FOURIER_MODULUS);++charge;}exponent>>=1;if(exponent){base=int(u64(base)*base%FOURIER_MODULUS);++charge;}}return value;}

struct FourierSentinel{int omega=0,inverse_m=0,first_direct=0,first_character=0,second_direct=0,second_character=0;u64 charge=0;std::string digest;};
FourierSentinel fourier_sentinel(u64&hash_charge){
 FourierSentinel out;for(int candidate=2;candidate<FOURIER_MODULUS;++candidate){if(mod_power(candidate,101,out.charge)==1){out.omega=candidate;break;}}if(!out.omega)return out;
 std::array<int,101>powers{};powers[0]=1;for(int exponent=1;exponent<101;++exponent){powers[exponent]=int(u64(powers[exponent-1])*out.omega%FOURIER_MODULUS);++out.charge;}
 out.inverse_m=mod_power(int(DENSE_CELLS%FOURIER_MODULUS),FOURIER_MODULUS-2,out.charge);
 std::array<int,3>u{{1,2,3}},v{{4,5,6}};std::array<std::array<int,3>,2>central{{{{96,94,92}},{{96,94,93}}}};std::array<int,2>characters{};
 for(int which=0;which<2;++which){u64 sum=0;for(int t0=0;t0<101;++t0)for(int t1=0;t1<101;++t1)for(int t2=0;t2<101;++t2){std::array<int,3>t{{t0,t1,t2}};int exponent=0;for(int coordinate=0;coordinate<3;++coordinate){out.charge+=2;exponent=(exponent+t[coordinate]*(u[coordinate]+v[coordinate]+central[which][coordinate]))%101;}sum+=powers[(101-exponent)%101];++out.charge;if(sum>=(1ULL<<61))sum%=FOURIER_MODULUS;}characters[which]=int((sum%FOURIER_MODULUS)*out.inverse_m%FOURIER_MODULUS);out.charge+=2;}
 auto direct=[&](const std::array<int,3>&c){for(int coordinate=0;coordinate<3;++coordinate)if((u[coordinate]+v[coordinate]+c[coordinate])%101)return 0;return 1;};out.first_direct=direct(central[0]);out.second_direct=direct(central[1]);out.first_character=characters[0];out.second_character=characters[1];
 Sha256 hash;hash_u64_be(hash,out.omega,hash_charge);hash_u64_be(hash,out.inverse_m,hash_charge);for(int value:std::array<int,4>{{out.first_direct,out.first_character,out.second_direct,out.second_character}})hash_u64_be(hash,value,hash_charge);out.digest=hash.finish();return out;
}

u64 record_sort_bound(u64 count){u64 levels=0,power=1;while(power<count){power*=2;++levels;}return checked_product(checked_product(count,levels),34);}

int main(){
 struct rlimit limit{};getrlimit(RLIMIT_AS,&limit);if(limit.rlim_cur!=67108864ULL||limit.rlim_max!=67108864ULL)return 70;
 for(int value=0;value<P;++value){POW[value][0]=1;for(int exponent=1;exponent<=25;++exponent)POW[value][exponent]=POW[value][exponent-1]*value%P;}for(int character=0;character<N;++character)for(int index=0;index<N;++index)COEFF[character][index]=coeff(character,index);
 bool source_guard=CH[5].first==2&&CH[5].second==15&&CH[6].second==20&&CH[8].second==5;std::array<std::array<int,3>,3>expected{{{{1,4,20}},{{2,13,10}},{{4,6,15}}}};int pattern=0;for(auto pivot:PIV)if(pivot[0]==2){if(pattern>=3||std::array<int,3>{{pivot[1],pivot[2],pivot[3]}}!=expected[pattern])source_guard=false;++pattern;}source_guard=source_guard&&pattern==3;
 u64 hash_charge=0,state_iterations=0,coordinate_failures=0;Sha256 definition_hash;hash_u64_be(definition_hash,P,hash_charge);hash_u64_be(definition_hash,FEATURE_DIM,hash_charge);for(int row:std::array<int,4>{{5,6,8,3}})hash_u64_be(definition_hash,row,hash_charge);for(int h=0;h<3;++h)for(int q=0;q<5;++q){hash_u64_be(definition_hash,h,hash_charge);hash_u64_be(definition_hash,q,hash_charge);for(int exponent:expected[h])hash_u64_be(definition_hash,exponent,hash_charge);for(int residue:std::array<int,3>{{0,1,3}}){hash_u64_be(definition_hash,COEFF[2][residue+5*q],hash_charge);hash_u64_be(definition_hash,(residue+2)/5,hash_charge);}}auto definition_sha=definition_hash.finish();
 Charges search;std::vector<std::uint32_t>zero;zero.reserve(ZERO_COUNT);for(std::uint32_t code=0;code<390625;++code){auto shape=decode_shape(code);if(!cyclic_bucket(shape,search))zero.push_back(code);++state_iterations;}if(zero.size()!=ZERO_COUNT)return 71;
 Charges feature_charge;u64 rank_charge=0;Basis b_basis,c_basis,w_a3_basis;
 for(int jb=0;jb<5;++jb)for(int phase_position=0;phase_position<5;++phase_position)for(auto code:zero){auto shape=decode_shape(code);Block B{1,pw(GEN,4*jb+20*PHASE_ORDER[phase_position]),&shape};insert_basis(phi_feature(1,B,feature_charge),b_basis,rank_charge);++state_iterations;}
 canonicalize_basis(b_basis,rank_charge);
 for(int jc=0;jc<5;++jc)for(auto code:zero){auto shape=decode_shape(code);Block C{2,pw(GEN,4*jc),&shape};insert_basis(psi_feature(0,C,feature_charge),c_basis,rank_charge);++state_iterations;}
 canonicalize_basis(c_basis,rank_charge);
 for(auto code:zero){auto shape=decode_shape(code);Block A{0,1,&shape};insert_basis(psi_feature(3,A,feature_charge),w_a3_basis,rank_charge);++state_iterations;}
 canonicalize_basis(w_a3_basis,rank_charge);
 auto b_basis_sha=basis_digest(b_basis,hash_charge),c_basis_sha=basis_digest(c_basis,hash_charge),w_a3_sha=basis_digest(w_a3_basis,hash_charge);
 Charges signature_charge,coordinate_charge,central_charge,identity_charge;Sha256 a_states,d_states,a_map,d_map,a_coordinates,d_central_map,orientation_hash;std::vector<Signature>a_signatures,d_signatures;std::vector<DRecord>d_records;a_signatures.reserve(NA);d_signatures.reserve(ND);d_records.reserve(ND);
 u64 state=0;for(auto code:zero){auto shape=decode_shape(code);Block A{0,1,&shape};auto a_signature=signature(c_basis,phi_feature(0,A,feature_charge),signature_charge);auto psi_a=psi_feature(3,A,feature_charge);Feature reconstructed{};auto rho=coordinates(w_a3_basis,psi_a,reconstructed,coordinate_charge,coordinate_failures);a_signatures.push_back(a_signature);hash_u64_be(a_states,state,hash_charge);hash_u64_be(a_states,code,hash_charge);hash_byte(a_states,1,hash_charge);hash_u64_be(a_map,state,hash_charge);hash_signature_be(a_map,a_signature,hash_charge);hash_u64_be(a_coordinates,state,hash_charge);hash_signature_be(a_coordinates,rho,hash_charge);++state;++state_iterations;}if(state!=NA)return 72;
 state=0;for(int jd=0;jd<5;++jd)for(auto code:zero){auto shape=decode_shape(code);int scale=pw(GEN,4*jd);Block D{3,scale,&shape};auto d_signature=signature(b_basis,psi_feature(1,D,feature_charge),signature_charge);auto kappa=signature(w_a3_basis,phi_feature(3,D,feature_charge),central_charge);CentralKey key{};key[0]=unary_value(6,D,central_charge);key[1]=unary_value(8,D,central_charge);key[2]=unary_value(5,D,central_charge);for(int index=0;index<FEATURE_DIM;++index)key[index+3]=kappa[index];d_signatures.push_back(d_signature);d_records.push_back({d_signature,key,state});hash_u64_be(d_states,state,hash_charge);hash_u64_be(d_states,code,hash_charge);hash_byte(d_states,scale,hash_charge);hash_u64_be(d_map,state,hash_charge);hash_signature_be(d_map,d_signature,hash_charge);++state;++state_iterations;}if(state!=ND)return 73;
 u64 identity_failures=0;for(u64 sample=0;sample<256;++sample){u64 did=(sample*97+11)%ND,aid=(sample*53+7)%NA;int jd=did/ZERO_COUNT,dpos=did%ZERO_COUNT;auto dshape=decode_shape(zero[dpos]),ashape=decode_shape(zero[aid]);Block D{3,pw(GEN,4*jd),&dshape},A{0,1,&ashape};int direct=pivot_pair_value(5,D,A,identity_charge),dotted=dot_feature(phi_feature(3,D,identity_charge),psi_feature(3,A,identity_charge),identity_charge);if(direct!=dotted)++identity_failures;hash_u64_be(orientation_hash,did,hash_charge);hash_u64_be(orientation_hash,aid,hash_charge);hash_u64_be(orientation_hash,direct,hash_charge);hash_u64_be(orientation_hash,dotted,hash_charge);++state_iterations;}
 u64 sort_charge=sort_bound(a_signatures.size())+sort_bound(d_signatures.size())+record_sort_bound(d_records.size());auto a_summary=summarize(a_signatures,c_basis.rank,hash_charge),d_summary=summarize(d_signatures,b_basis.rank,hash_charge);std::sort(d_records.begin(),d_records.end());
 u64 distinct_pairs=0,nonconstant_fibres=0,max_key_multiplicity=0,first_state=std::numeric_limits<u64>::max(),second_state=std::numeric_limits<u64>::max();Signature first_signature{};CentralKey first_key{},second_key{};
 for(std::size_t begin=0;begin<d_records.size();){std::size_t end=begin+1;while(end<d_records.size()&&d_records[end].signature==d_records[begin].signature)++end;u64 keys_in_fibre=0;std::size_t cursor=begin;while(cursor<end){std::size_t next=cursor+1;while(next<end&&d_records[next].key==d_records[cursor].key)++next;++distinct_pairs;++keys_in_fibre;max_key_multiplicity=std::max(max_key_multiplicity,u64(next-cursor));if(keys_in_fibre==2&&first_state==std::numeric_limits<u64>::max()){first_signature=d_records[begin].signature;first_key=d_records[begin].key;second_key=d_records[cursor].key;first_state=d_records[begin].state;second_state=d_records[cursor].state;}cursor=next;}if(keys_in_fibre>1)++nonconstant_fibres;begin=end;}
 for(const auto&record:d_records){hash_signature_be(d_central_map,record.signature,hash_charge);hash_central_key(d_central_map,record.key,hash_charge);hash_u64_be(d_central_map,record.state,hash_charge);}
 auto sentinel=fourier_sentinel(hash_charge);bool sentinel_verified=sentinel.omega&&sentinel.first_direct==1&&sentinel.first_character==1&&sentinel.second_direct==0&&sentinel.second_character==0;
 auto a_state_sha=a_states.finish(),d_state_sha=d_states.finish(),a_map_sha=a_map.finish(),d_map_sha=d_map.finish(),a_coordinate_sha=a_coordinates.finish(),d_central_sha=d_central_map.finish(),orientation_sha=orientation_hash.finish();auto first_signature_sha=signature_digest(first_signature),first_key_sha=key_digest(first_key),second_key_sha=key_digest(second_key);
 u64 engine_charge=search.total()+feature_charge.total()+rank_charge+signature_charge.total()+coordinate_charge.total()+central_charge.total()+identity_charge.total()+sentinel.charge+hash_charge+state_iterations+sort_charge;u64 projected_combined=2*engine_charge;std::string status="CAP_ABORT";if(source_guard&&identity_failures==0&&coordinate_failures==0&&sentinel_verified&&projected_combined<CENTRAL_KEY_CAP)status=nonconstant_fibres?"D1_D_CENTRAL_KEY_NOT_DETERMINED":"D1_PASS";std::string next_gate="ROUTE_REJECTED";if(status=="D1_PASS")next_gate="D2_PREFLIGHT_REQUIRED";else if(status=="D1_D_CENTRAL_KEY_NOT_DETERMINED")next_gate=distinct_pairs==ND&&max_key_multiplicity==1?"COORDINATE3_TENSOR_ORBIT_DIAGNOSTIC_REQUIRED":"CENTRAL_KEY_REFINEMENT_REQUIRED";
 std::cout<<"REASONING_AD5_COORDINATE3_CENTRAL_KEY_V1\nstatus="<<status<<"\nnext_gate="<<next_gate<<"\nfield_prime="<<P<<"\nfeature_dimension="<<FEATURE_DIM<<"\npivot_pattern_count="<<pattern<<"\nzero_bucket_count="<<zero.size()<<"\na_state_count="<<NA<<"\nd_state_count="<<ND<<"\nb_feature_rank="<<b_basis.rank<<"\nc_feature_rank="<<c_basis.rank<<"\nw_a3_rank="<<w_a3_basis.rank<<"\na_signature_count="<<a_summary.distinct<<"\na_signature_minimum_multiplicity="<<a_summary.minimum<<"\na_signature_maximum_multiplicity="<<a_summary.maximum<<"\nd_signature_count="<<d_summary.distinct<<"\nd_signature_multiplicity_mass="<<d_summary.mass<<"\nd_signature_minimum_multiplicity="<<d_summary.minimum<<"\nd_signature_maximum_multiplicity="<<d_summary.maximum<<"\nd_signature_collision_pair_count="<<d_summary.collision_pairs<<"\ndistinct_sigd_central_key_count="<<distinct_pairs<<"\nnonconstant_sigd_fibre_count="<<nonconstant_fibres<<"\nmaximum_central_key_multiplicity="<<max_key_multiplicity<<"\nfirst_conflict_first_state="<<first_state<<"\nfirst_conflict_second_state="<<second_state<<"\ncoordinate_reconstruction_failure_count="<<coordinate_failures<<"\norientation_sample_count=256\norientation_sample_failure_count="<<identity_failures<<"\nfourier_modulus="<<FOURIER_MODULUS<<"\nfourier_omega="<<sentinel.omega<<"\nfourier_inverse_m="<<sentinel.inverse_m<<"\nfourier_first_direct="<<sentinel.first_direct<<"\nfourier_first_character="<<sentinel.first_character<<"\nfourier_second_direct="<<sentinel.second_direct<<"\nfourier_second_character="<<sentinel.second_character<<"\nfourier_sign_sentinel_verified="<<sentinel_verified<<"\nshape_search_charge_total="<<search.total()<<"\nfeature_charge_total="<<feature_charge.total()<<"\nrank_charge_total="<<rank_charge<<"\nsignature_charge_total="<<signature_charge.total()<<"\ncoordinate_charge_total="<<coordinate_charge.total()<<"\ncentral_key_charge_total="<<central_charge.total()<<"\nidentity_charge_total="<<identity_charge.total()<<"\nfourier_charge_total="<<sentinel.charge<<"\nhash_byte_charge_total="<<hash_charge<<"\nstate_iteration_charge_total="<<state_iterations<<"\nsort_charge_bound="<<sort_charge<<"\nengine_preflight_charge="<<engine_charge<<"\nprojected_combined_diagnostic_charge="<<projected_combined<<"\ncombined_charge_cap="<<CENTRAL_KEY_CAP<<"\nsource_guard="<<source_guard<<"\ncentral_key_definition_sha256="<<definition_sha<<"\na_state_stream_sha256="<<a_state_sha<<"\nd_state_stream_sha256="<<d_state_sha<<"\na_signature_map_sha256="<<a_map_sha<<"\nd_signature_map_sha256="<<d_map_sha<<"\nb_basis_sha256="<<b_basis_sha<<"\nc_basis_sha256="<<c_basis_sha<<"\nw_a3_rref_sha256="<<w_a3_sha<<"\na_coordinate_map_sha256="<<a_coordinate_sha<<"\nd_central_key_map_sha256="<<d_central_sha<<"\na_signature_multiplicity_sha256="<<a_summary.digest<<"\nd_signature_multiplicity_sha256="<<d_summary.digest<<"\norientation_samples_sha256="<<orientation_sha<<"\nfourier_sentinel_sha256="<<sentinel.digest<<"\nfirst_conflict_signature_sha256="<<first_signature_sha<<"\nfirst_conflict_first_key_sha256="<<first_key_sha<<"\nfirst_conflict_second_key_sha256="<<second_key_sha<<"\nrlimit_as_soft="<<limit.rlim_cur<<"\nrlimit_as_hard="<<limit.rlim_max<<"\nvm_peak_kib="<<vm_peak_kib()<<"\nREASONING_END\n";
}
"""
)

_INTEGER_FIELDS = {
    "field_prime", "feature_dimension", "pivot_pattern_count", "zero_bucket_count",
    "a_state_count", "d_state_count", "b_feature_rank", "c_feature_rank", "w_a3_rank",
    "a_signature_count", "a_signature_minimum_multiplicity", "a_signature_maximum_multiplicity",
    "d_signature_count", "d_signature_multiplicity_mass", "d_signature_minimum_multiplicity",
    "d_signature_maximum_multiplicity", "d_signature_collision_pair_count",
    "distinct_sigd_central_key_count", "nonconstant_sigd_fibre_count",
    "maximum_central_key_multiplicity", "first_conflict_first_state", "first_conflict_second_state",
    "coordinate_reconstruction_failure_count", "orientation_sample_count",
    "orientation_sample_failure_count", "fourier_modulus", "fourier_omega",
    "fourier_inverse_m", "fourier_first_direct", "fourier_first_character",
    "fourier_second_direct", "fourier_second_character", "fourier_sign_sentinel_verified",
    "shape_search_charge_total", "feature_charge_total", "rank_charge_total",
    "signature_charge_total", "coordinate_charge_total", "central_key_charge_total",
    "identity_charge_total", "fourier_charge_total", "hash_byte_charge_total",
    "state_iteration_charge_total", "sort_charge_bound", "engine_preflight_charge",
    "projected_combined_diagnostic_charge", "combined_charge_cap", "source_guard",
    "rlimit_as_soft", "rlimit_as_hard", "vm_peak_kib",
}
_HASH_FIELDS = {
    "central_key_definition_sha256", "a_state_stream_sha256", "d_state_stream_sha256",
    "a_signature_map_sha256", "d_signature_map_sha256", "b_basis_sha256", "c_basis_sha256",
    "w_a3_rref_sha256", "a_coordinate_map_sha256", "d_central_key_map_sha256",
    "a_signature_multiplicity_sha256", "d_signature_multiplicity_sha256",
    "orientation_samples_sha256", "fourier_sentinel_sha256",
    "first_conflict_signature_sha256", "first_conflict_first_key_sha256",
    "first_conflict_second_key_sha256",
}
_STRING_FIELDS = {"status", "next_gate"}


def _parse_transcript(stdout: str) -> dict[str, Any]:
    lines = stdout.strip().splitlines()
    frame = f"REASONING_AD5_COORDINATE3_CENTRAL_KEY_V{_PROTOCOL_VERSION}"
    if len(lines) < 3 or lines[0] != frame or lines[-1] != "REASONING_END":
        raise ScientificToolError("AD5 coordinate-three central-key transcript has invalid framing")
    fields: dict[str, str] = {}
    for line in lines[1:-1]:
        if "=" not in line:
            raise ScientificToolError("AD5 central-key transcript has an invalid field")
        name, value = line.split("=", 1)
        if name in fields:
            raise ScientificToolError("AD5 central-key transcript has duplicate fields")
        fields[name] = value
    if set(fields) != _INTEGER_FIELDS | _HASH_FIELDS | _STRING_FIELDS:
        raise ScientificToolError("AD5 central-key transcript is incomplete")
    try:
        parsed: dict[str, Any] = {name: int(fields[name]) for name in _INTEGER_FIELDS}
    except ValueError as error:
        raise ScientificToolError("AD5 central-key transcript has a nonintegral field") from error
    for name in _HASH_FIELDS:
        value = fields[name]
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ScientificToolError("AD5 central-key transcript has an invalid digest")
        parsed[name] = value
    for name in _STRING_FIELDS:
        parsed[name] = fields[name]
    return parsed


def _run_program() -> dict[str, Any]:
    computation = _run_source_hashed_program(
        _PROGRAM,
        label="ad5-coordinate3-central-key",
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
    replay_path = Path(__file__).with_name("heisenberg_level25_four_block_ad5_central_key_replay.py")
    try:
        program = replay_path.read_bytes()
    except OSError as error:
        raise ScientificToolError(f"independent AD5 central-key replay is unavailable: {error}") from error
    replay_python = shutil.which("python3", path="/usr/local/bin:/usr/bin:/bin") or sys.executable
    command = _address_space_limited_command([replay_python, "-I", "-S", str(replay_path)], _RESIDENT_BYTE_CAP)
    environment = {
        "HOME": "", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
    }
    try:
        completed = subprocess.run(
            sandbox.wrap(command, ro=[replay_path], network=False), text=True,
            capture_output=True, timeout=900, env=environment, check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise ScientificToolError("independent AD5 central-key replay exceeded 900 seconds") from error
    except OSError as error:
        raise ScientificToolError(f"independent AD5 central-key replay could not start: {error}") from error
    if completed.returncode or completed.stderr.strip():
        detail = (completed.stderr.strip() or completed.stdout.strip() or "unknown replay failure")[:2000]
        raise ScientificToolError(f"independent AD5 central-key replay failed: {detail}")
    try:
        replay = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise ScientificToolError("independent AD5 central-key replay returned invalid JSON") from error
    if not isinstance(replay, dict):
        raise ScientificToolError("independent AD5 central-key replay returned a non-object")
    return {
        **replay,
        "program_sha256": hashlib.sha256(program).hexdigest(),
        "transcript_sha256": hashlib.sha256(completed.stdout.encode()).hexdigest(),
        "transcript": completed.stdout,
        "runtime_address_space_limit_bytes": _RESIDENT_BYTE_CAP,
        "interpreter": str(Path(replay_python).resolve()),
    }


def level25_heisenberg_augmented_four_block_ad5_coordinate3_central_key_profile() -> dict[str, Any]:
    """Test whether D signatures determine the complete coordinate-three central key."""

    fourier = level25_heisenberg_augmented_four_block_fourier_profile()
    fourier_sha256 = hashlib.sha256(canonical_json(fourier).encode()).hexdigest()
    phase = level25_heisenberg_augmented_four_block_phase_quotient_profile()
    computation = _run_program()
    replay = _run_replay()
    source_manifest = _source_manifest()
    compared_fields = (
        "status", "next_gate", *(_INTEGER_FIELDS - {"rlimit_as_soft", "rlimit_as_hard", "vm_peak_kib"}), *_HASH_FIELDS,
    )
    replay_agreement = all(replay.get(name) == computation[name] for name in compared_fields)
    actual_combined_charge = computation["engine_preflight_charge"] + replay["engine_preflight_charge"]
    expected_status = (
        "D1_D_CENTRAL_KEY_NOT_DETERMINED"
        if computation["nonconstant_sigd_fibre_count"]
        else "D1_PASS"
    )
    certificate = {
        "corrected_fourier_domain_is_the_certified_prerequisite": reviewed_profile_matches(fourier, _FOURIER_EVIDENCE_SHA256),
        "phase_quotient_is_the_certified_prerequisite": reviewed_profile_matches(phase, _PHASE_EVIDENCE_SHA256),
        "source_manifest_matches_executable_source": source_manifest["raw_record_count"] == 725,
        "primary_and_independent_replay_agree": replay_agreement,
        "input_signature_census_is_rederived_exactly": computation["a_state_count"] == computation["a_signature_count"] == 4_420
        and computation["a_signature_minimum_multiplicity"] == computation["a_signature_maximum_multiplicity"] == 1
        and computation["d_state_count"] == computation["d_signature_multiplicity_mass"] == 22_100
        and computation["d_signature_count"] == 1_755
        and computation["d_signature_minimum_multiplicity"] == 5
        and computation["d_signature_maximum_multiplicity"] == 120
        and computation["d_signature_collision_pair_count"] == 194_950,
        "wrapped_orientation_and_coordinates_are_replayed": computation["source_guard"] == 1
        and computation["orientation_sample_failure_count"] == 0
        and computation["coordinate_reconstruction_failure_count"] == 0,
        "fourier_reflection_sign_is_checked_by_character_sentinels": computation["fourier_modulus"] == 607
        and computation["fourier_first_direct"] == computation["fourier_first_character"] == 1
        and computation["fourier_second_direct"] == computation["fourier_second_character"] == 0
        and computation["fourier_sign_sentinel_verified"] == 1,
        "status_matches_the_exact_central_key_fibre_census": computation["status"] == expected_status,
        "combined_execution_respects_the_hard_cap": actual_combined_charge == computation["projected_combined_diagnostic_charge"] < _COMBINED_CHARGE_CAP,
        "both_engines_respect_the_hard_address_space_limit": computation["rlimit_as_soft"] == computation["rlimit_as_hard"] == replay["runtime_address_space_limit_bytes"] == _RESIDENT_BYTE_CAP,
        "resident_telemetry_respects_the_address_space_cap": 0 < computation["vm_peak_kib"] * 1024 <= _RESIDENT_BYTE_CAP,
        "programs_and_transcripts_match_reviewed_executions": bounded_execution_matches(
            computation,
            parser=_parse_transcript,
            expected_program=_REVIEWED_PROGRAM_SHA256,
            expected_mathematics=_REVIEWED_MATHEMATICAL_TRANSCRIPT_SHA256,
        )
        and independent_replay_matches(
            replay,
            expected_program=_REVIEWED_REPLAY_PROGRAM_SHA256,
            expected_transcript=_REVIEWED_REPLAY_TRANSCRIPT_SHA256,
        ),
    }
    return seal_profile_evidence({
        "schema": "reasoning.level25-heisenberg-ad5-coordinate3-central-key-diagnostic.v2",
        "request": {},
        "request_sha256": hashlib.sha256(canonical_json({}).encode()).hexdigest(),
        "source_manifest": source_manifest,
        "fourier_receipt_sha256": fourier_sha256,
        "fourier_evidence_sha256": fourier["evidence_sha256"],
        "phase_receipt_sha256": hashlib.sha256(canonical_json(phase).encode()).hexdigest(),
        "phase_evidence_sha256": phase["evidence_sha256"],
        "computation": computation,
        "independent_replay": replay,
        "actual_combined_charge": actual_combined_charge,
        "decision": {
            "status": computation["status"],
            "next_gate": computation["next_gate"],
            "w_a3_rank": computation["w_a3_rank"],
            "distinct_sigd_central_key_count": computation["distinct_sigd_central_key_count"],
            "nonconstant_sigd_fibre_count": computation["nonconstant_sigd_fibre_count"],
            "signature_only_central_factorization_verified": computation["status"] == "D1_PASS",
            "full_contraction_authorized": False,
            "coordinate3_census_completed": False,
            "full_key_match_or_common_zero_found": False,
        },
        "certificate": certificate,
        "certificate_verified": all(certificate.values()),
        "interpretation": (
            "The diagnostic exhaustively tests whether every measured D signature determines d6, d8, L5, and the restriction of Phi_3(D) to the wrapped A-side Psi_3 span. It also evaluates two full additive-character delta sentinels with the corrected negative central phase."
        ),
        "not_certified": [
            "construction of any joint F_101^3 B or C profile",
            "a global prefix/value tensor factorization",
            "a Fourier transform, convolution, join, or full character contraction",
            "a coordinate-three solution count or census",
            "a full match in AD5 key coordinates four and five",
            "a seven-equation survivor or common zero",
            "geometric points over extensions of F_101 or a characteristic-zero lift",
            "global smoothness, a Brauer class, period, index, or period-index counterexample",
        ],
        "next_gate": {
            "feature": (
                "level25_heisenberg_augmented_four_block_ad5_coordinate3_d2_preflight"
                if computation["status"] == "D1_PASS"
                else "level25_heisenberg_augmented_four_block_ad5_coordinate3_tensor_orbit_diagnostic"
                if computation["next_gate"] == "COORDINATE3_TENSOR_ORBIT_DIAGNOSTIC_REQUIRED"
                else "level25_heisenberg_augmented_four_block_ad5_coordinate3_central_key_refinement"
            ),
            "requirement": (
                "If D1 passes, price the conditional 7,757,100-label stream before entering it. If the central-key refinement is injective on all 22,100 D states, reject signature-character reuse and seek a separately proved source tensor or orbit collapse; otherwise refine by the measured classes. No branch here authorizes a full contraction."
            ),
        },
    })
