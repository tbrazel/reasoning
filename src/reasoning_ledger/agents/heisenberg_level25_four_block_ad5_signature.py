# ruff: noqa: E501
"""Exact row-5 functional-signature diagnostic for the AD5 four-block fiber."""

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
from .heisenberg_level25_four_block_ad5_prefix import (
    _PROGRAM as _PREFIX_PROGRAM,
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
    exact_execution_matches,
    independent_replay_matches,
    reviewed_profile_matches,
    seal_profile_evidence,
)
from .scientific import ScientificToolError

_PROTOCOL_VERSION = 1
_COMBINED_CHARGE_CAP = 300_000_000
_FEATURE_DIMENSION = 15
_DENSE_CELL_COUNT = 101**3
_REVIEWED_PROGRAM_SHA256 = "d3c8ee2585062a02b155269153b2754d73d82c6c446671dd7f072a59a76c08e6"
_REVIEWED_TRANSCRIPT_SHA256 = "1e1fc5c2f117e06f181d5af89dcc428981550be32b2bb9277f55cd1cda2a3d5c"
_REVIEWED_REPLAY_PROGRAM_SHA256 = "a054e0494e94cde4b0f16c9bd6725723ad1962ad6fe15a8ae5fc2d9f60dde116"
_REVIEWED_REPLAY_TRANSCRIPT_SHA256 = "8327cf3bdcf5404c8c7cc9f19e3a6539c3966ca7303a25919002e4878a63bdce"

_SUPPORT, _MAIN_SEPARATOR, _OLD_MAIN = _PREFIX_PROGRAM.partition("\nint main(){")
if not _MAIN_SEPARATOR:
    raise RuntimeError("AD5 prefix support program no longer has the expected main")

_PROGRAM = (
    _SUPPORT
    + r"""
#include <algorithm>

constexpr int FEATURE_DIM=15,NA=4420,ND=22100;
constexpr u64 DIAGNOSTIC_CAP=300000000ULL,DENSE_CELLS=1030301ULL;
using Feature=std::array<int,FEATURE_DIM>;
using Signature=std::array<std::uint8_t,FEATURE_DIM>;

struct Basis{
 std::array<Feature,FEATURE_DIM>rows{};
 std::array<bool,FEATURE_DIM>used{};
 int rank=0;
};

void hash_byte(Sha256&hash,std::uint8_t value,u64&charge){hash.byte(value);++charge;}
void hash_u64(Sha256&hash,u64 value,u64&charge){for(int byte=0;byte<8;++byte)hash_byte(hash,(value>>(8*byte))&255,charge);}
void hash_feature(Sha256&hash,u64 state,const Feature&row,u64&charge){hash_u64(hash,state,charge);for(auto value:row)hash_byte(hash,value,charge);}

Feature phi_feature(int residue,const Block&block,Charges&charge){
 std::array<int,5>values{};for(int q=0;q<5;++q)values[q]=mulc(block.scale,(*block.u)[q],charge);
 Feature row{};int offset=0;for(auto pivot:PIV)if(pivot[0]==2)for(int q=0;q<5;++q){int term=COEFF[2][residue+5*q];term=mulc(term,powc(values[q],pivot[1],charge),charge);term=mulc(term,powc(values[(q+1)%5],pivot[2],charge),charge);row[offset++]=term;}
 if(offset!=FEATURE_DIM)std::exit(81);return row;
}

Feature psi_feature(int first_residue,const Block&block,Charges&charge){
 std::array<int,5>values{};for(int q=0;q<5;++q)values[q]=mulc(block.scale,(*block.u)[q],charge);
 int carry=(first_residue+2)/5;Feature row{};int offset=0;for(auto pivot:PIV)if(pivot[0]==2)for(int q=0;q<5;++q)row[offset++]=powc(values[(q+carry)%5],pivot[3],charge);
 if(offset!=FEATURE_DIM)std::exit(82);return row;
}

int dot_feature(const Feature&first,const Feature&second,Charges&charge){
 int value=mulc(first[0],second[0],charge);for(int i=1;i<FEATURE_DIM;++i)value=plusc(value,mulc(first[i],second[i],charge),charge);return value;
}

void insert_basis(Feature row,Basis&basis,u64&charge){
 for(int column=0;column<FEATURE_DIM;++column){
  if(!row[column])continue;
  if(basis.used[column]){int factor=row[column];for(int j=column;j<FEATURE_DIM;++j){charge+=2;row[j]=(row[j]-factor*basis.rows[column][j])%P;if(row[j]<0)row[j]+=P;}continue;}
  ++charge;int inverse=pw(row[column],P-2);for(int j=column;j<FEATURE_DIM;++j){++charge;row[j]=row[j]*inverse%P;}
  for(int pivot=0;pivot<FEATURE_DIM;++pivot)if(basis.used[pivot]&&basis.rows[pivot][column]){int factor=basis.rows[pivot][column];for(int j=column;j<FEATURE_DIM;++j){charge+=2;basis.rows[pivot][j]=(basis.rows[pivot][j]-factor*row[j])%P;if(basis.rows[pivot][j]<0)basis.rows[pivot][j]+=P;}}
  basis.rows[column]=row;basis.used[column]=true;++basis.rank;return;
 }
}

void canonicalize_basis(Basis&basis,u64&charge){
 std::array<Feature,FEATURE_DIM>matrix{};int row_count=0;for(int pivot=0;pivot<FEATURE_DIM;++pivot)if(basis.used[pivot])matrix[row_count++]=basis.rows[pivot];std::array<int,FEATURE_DIM>pivot_columns{};int rank=0;
 for(int column=0;column<FEATURE_DIM&&rank<row_count;++column){int pivot=rank;while(pivot<row_count&&!matrix[pivot][column])++pivot;if(pivot==row_count)continue;if(pivot!=rank)std::swap(matrix[pivot],matrix[rank]);++charge;int inverse=pw(matrix[rank][column],P-2);for(int offset=0;offset<FEATURE_DIM;++offset){++charge;matrix[rank][offset]=matrix[rank][offset]*inverse%P;}for(int row=0;row<row_count;++row)if(row!=rank&&matrix[row][column]){int factor=matrix[row][column];for(int offset=0;offset<FEATURE_DIM;++offset){charge+=2;matrix[row][offset]=(matrix[row][offset]-factor*matrix[rank][offset])%P;if(matrix[row][offset]<0)matrix[row][offset]+=P;}}pivot_columns[rank]=column;++rank;}
 Basis canonical;canonical.rank=rank;for(int row=0;row<rank;++row){canonical.used[pivot_columns[row]]=true;canonical.rows[pivot_columns[row]]=matrix[row];}basis=canonical;
}

Signature signature(const Basis&basis,const Feature&row,Charges&charge){
 Signature result{};int position=0;for(int pivot=0;pivot<FEATURE_DIM;++pivot)if(basis.used[pivot])result[position++]=dot_feature(basis.rows[pivot],row,charge);return result;
}

std::string basis_digest(const Basis&basis,u64&hash_charge){Sha256 hash;hash_u64(hash,basis.rank,hash_charge);for(int pivot=0;pivot<FEATURE_DIM;++pivot){hash_byte(hash,basis.used[pivot],hash_charge);for(auto value:basis.rows[pivot])hash_byte(hash,value,hash_charge);}return hash.finish();}

struct MultiplicitySummary{u64 distinct=0,mass=0,minimum=0,maximum=0,collision_pairs=0;std::string digest;};
MultiplicitySummary summarize(std::vector<Signature>&values,int rank,u64&hash_charge){
 std::sort(values.begin(),values.end());Sha256 hash;hash_u64(hash,rank,hash_charge);MultiplicitySummary out;out.mass=values.size();
 for(std::size_t start=0;start<values.size();){std::size_t end=start+1;while(end<values.size()&&values[end]==values[start])++end;u64 count=end-start;++out.distinct;out.minimum=out.minimum?std::min(out.minimum,count):count;out.maximum=std::max(out.maximum,count);out.collision_pairs+=count*(count-1)/2;for(auto value:values[start])hash_byte(hash,value,hash_charge);hash_u64(hash,count,hash_charge);start=end;}out.digest=hash.finish();return out;
}

u64 checked_product(u64 first,u64 second){__uint128_t value=__uint128_t(first)*second;if(value>std::numeric_limits<u64>::max())std::exit(83);return u64(value);}
u64 sort_bound(u64 count){u64 levels=0,power=1;while(power<count){power*=2;++levels;}return checked_product(checked_product(count,levels),FEATURE_DIM);}

int main(){
 struct rlimit limit{};getrlimit(RLIMIT_AS,&limit);if(limit.rlim_cur!=67108864ULL||limit.rlim_max!=67108864ULL)return 70;
 for(int value=0;value<P;++value){POW[value][0]=1;for(int exponent=1;exponent<=25;++exponent)POW[value][exponent]=POW[value][exponent-1]*value%P;}for(int character=0;character<N;++character)for(int index=0;index<N;++index)COEFF[character][index]=coeff(character,index);
 bool row5_guard=CH[5].first==2&&CH[5].second==15;std::array<std::array<int,3>,3>expected{{{{1,4,20}},{{2,13,10}},{{4,6,15}}}};int pattern=0;for(auto pivot:PIV)if(pivot[0]==2){if(pattern>=3||std::array<int,3>{{pivot[1],pivot[2],pivot[3]}}!=expected[pattern])row5_guard=false;++pattern;}row5_guard=row5_guard&&pattern==3;
 u64 hash_charge=0,state_iterations=0;Sha256 definition_hash;hash_u64(definition_hash,P,hash_charge);hash_u64(definition_hash,FEATURE_DIM,hash_charge);for(int h=0;h<3;++h)for(int q=0;q<5;++q){hash_u64(definition_hash,h,hash_charge);hash_u64(definition_hash,q,hash_charge);for(int exponent:expected[h])hash_u64(definition_hash,exponent,hash_charge);for(int residue:std::array<int,3>{{0,1,3}}){hash_u64(definition_hash,COEFF[2][residue+5*q],hash_charge);hash_u64(definition_hash,(residue+2)/5,hash_charge);}}auto feature_definition_sha=definition_hash.finish();
 Charges search;std::vector<std::uint32_t>zero;zero.reserve(ZERO_COUNT);Sha256 zero_hash;for(std::uint32_t code=0;code<390625;++code){auto shape=decode_shape(code);if(!cyclic_bucket(shape,search)){zero.push_back(code);hash_u64(zero_hash,code,hash_charge);}++state_iterations;}if(zero.size()!=ZERO_COUNT)return 71;auto zero_sha=zero_hash.finish();
 Charges feature_charge;u64 rank_charge=0;Basis b_basis,c_basis;Sha256 b_rows,c_rows;
 u64 state=0;for(int jb=0;jb<5;++jb)for(int phase_position=0;phase_position<5;++phase_position)for(auto code:zero){auto shape=decode_shape(code);Block B{1,pw(GEN,4*jb+20*PHASE_ORDER[phase_position]),&shape};auto row=phi_feature(1,B,feature_charge);insert_basis(row,b_basis,rank_charge);hash_feature(b_rows,state++,row,hash_charge);++state_iterations;}if(state!=NB)return 72;canonicalize_basis(b_basis,rank_charge);auto b_rows_sha=b_rows.finish();
 state=0;for(int jc=0;jc<5;++jc)for(auto code:zero){auto shape=decode_shape(code);Block C{2,pw(GEN,4*jc),&shape};auto row=psi_feature(0,C,feature_charge);insert_basis(row,c_basis,rank_charge);hash_feature(c_rows,state++,row,hash_charge);++state_iterations;}if(state!=NC)return 73;canonicalize_basis(c_basis,rank_charge);auto c_rows_sha=c_rows.finish();
 auto b_basis_sha=basis_digest(b_basis,hash_charge),c_basis_sha=basis_digest(c_basis,hash_charge);
 Charges signature_charge;std::vector<Signature>d_signatures;d_signatures.reserve(ND);Sha256 d_rows;state=0;for(int jd=0;jd<5;++jd)for(auto code:zero){auto shape=decode_shape(code);Block D{3,pw(GEN,4*jd),&shape};auto row=psi_feature(1,D,feature_charge);hash_feature(d_rows,state++,row,hash_charge);d_signatures.push_back(signature(b_basis,row,signature_charge));++state_iterations;}if(state!=ND)return 74;auto d_rows_sha=d_rows.finish();
 std::vector<Signature>a_signatures;a_signatures.reserve(NA);Sha256 a_rows;state=0;for(auto code:zero){auto shape=decode_shape(code);Block A{0,1,&shape};auto row=phi_feature(0,A,feature_charge);hash_feature(a_rows,state++,row,hash_charge);a_signatures.push_back(signature(c_basis,row,signature_charge));++state_iterations;}if(state!=NA)return 75;auto a_rows_sha=a_rows.finish();
 u64 sort_charge=sort_bound(d_signatures.size())+sort_bound(a_signatures.size());auto d_summary=summarize(d_signatures,b_basis.rank,hash_charge);auto a_summary=summarize(a_signatures,c_basis.rank,hash_charge);
 Charges identity_charge;u64 identity_failures=0;Sha256 identity_hash;for(u64 sample=0;sample<1000;++sample){int kind=sample%3;u64 first_id=0,second_id=0;Feature first{},second{};int direct=0;if(kind==0){first_id=(sample*37)%NB;second_id=(sample*101+17)%ND;int work=first_id/ZERO_COUNT,bpos=first_id%ZERO_COUNT,phase_position=work%5,jb=work/5,jd=second_id/ZERO_COUNT,dpos=second_id%ZERO_COUNT;auto bs=decode_shape(zero[bpos]),ds=decode_shape(zero[dpos]);Block B{1,pw(GEN,4*jb+20*PHASE_ORDER[phase_position]),&bs},D{3,pw(GEN,4*jd),&ds};first=phi_feature(1,B,identity_charge);second=psi_feature(1,D,identity_charge);direct=pivot_pair_value(5,B,D,identity_charge);}else if(kind==1){first_id=(sample*43)%NA;second_id=(sample*107+19)%NC;int jc=second_id/ZERO_COUNT,cpos=second_id%ZERO_COUNT;auto as=decode_shape(zero[first_id]),cs=decode_shape(zero[cpos]);Block A{0,1,&as},C{2,pw(GEN,4*jc),&cs};first=phi_feature(0,A,identity_charge);second=psi_feature(0,C,identity_charge);direct=pivot_pair_value(5,A,C,identity_charge);}else{first_id=(sample*47)%ND;second_id=(sample*109+23)%NA;int jd=first_id/ZERO_COUNT,dpos=first_id%ZERO_COUNT;auto ds=decode_shape(zero[dpos]),as=decode_shape(zero[second_id]);Block D{3,pw(GEN,4*jd),&ds},A{0,1,&as};first=phi_feature(3,D,identity_charge);second=psi_feature(3,A,identity_charge);direct=pivot_pair_value(5,D,A,identity_charge);}int dotted=dot_feature(first,second,identity_charge);if(direct!=dotted)++identity_failures;hash_u64(identity_hash,kind,hash_charge);hash_u64(identity_hash,first_id,hash_charge);hash_u64(identity_hash,second_id,hash_charge);hash_u64(identity_hash,direct,hash_charge);hash_u64(identity_hash,dotted,hash_charge);++state_iterations;}
 auto calibration_shape=decode_shape(zero[0]);Block calibration_a{0,1,&calibration_shape},calibration_d{3,1,&calibration_shape};int calibration_direct=pivot_pair_value(5,calibration_d,calibration_a,identity_charge),calibration_dot=dot_feature(phi_feature(3,calibration_d,identity_charge),psi_feature(3,calibration_a,identity_charge),identity_charge);bool calibration=calibration_direct==calibration_dot;hash_u64(identity_hash,3,hash_charge);hash_u64(identity_hash,0,hash_charge);hash_u64(identity_hash,0,hash_charge);hash_u64(identity_hash,calibration_direct,hash_charge);hash_u64(identity_hash,calibration_dot,hash_charge);auto identity_sha=identity_hash.finish();
 u64 profile_visits=checked_product(d_summary.distinct,NB)+checked_product(a_summary.distinct,NC);u64 profile_value_ops=2*checked_product(checked_product(b_basis.rank,d_summary.distinct),NB)+2*checked_product(checked_product(c_basis.rank,a_summary.distinct),NC);u64 profile_lower_combined=2*checked_product(1,profile_visits+profile_value_ops);u64 dense_profile_bytes=8*DENSE_CELLS,dense_pair_bytes=2*dense_profile_bytes,dense_all_bytes=checked_product(dense_profile_bytes,d_summary.distinct+a_summary.distinct),sparse_pair_bytes=16ULL*(NB+NC);
 u64 feature_total=feature_charge.total(),signature_total=signature_charge.total(),identity_total=identity_charge.total();u64 engine_preflight=search.total()+feature_total+rank_charge+signature_total+identity_total+hash_charge+state_iterations+sort_charge;u64 projected_combined=2*engine_preflight;bool source_verified=row5_guard&&!identity_failures&&calibration;std::string status=source_verified&&projected_combined<DIAGNOSTIC_CAP?"SIGNATURE_CENSUS_COMPLETE":"CAP_ABORT";std::string next_gate=status=="SIGNATURE_CENSUS_COMPLETE"&&projected_combined+profile_lower_combined<DIAGNOSTIC_CAP?"SPARSE_PROFILE_NEXT_GATE":"CHARACTER_CONTRACTION_REQUIRED";
 std::cout<<"REASONING_AD5_COORDINATE3_FUNCTIONAL_SIGNATURE_V1\nstatus="<<status<<"\nnext_gate="<<next_gate<<"\nfield_prime="<<P<<"\nfeature_dimension="<<FEATURE_DIM<<"\npivot_pattern_count="<<pattern<<"\nzero_bucket_count="<<zero.size()<<"\na_state_count="<<NA<<"\nd_state_count="<<ND<<"\nb_state_count="<<NB<<"\nc_state_count="<<NC<<"\nb_feature_rank="<<b_basis.rank<<"\nc_feature_rank="<<c_basis.rank<<"\nd_signature_count="<<d_summary.distinct<<"\na_signature_count="<<a_summary.distinct<<"\nd_signature_multiplicity_mass="<<d_summary.mass<<"\na_signature_multiplicity_mass="<<a_summary.mass<<"\nd_signature_minimum_multiplicity="<<d_summary.minimum<<"\nd_signature_maximum_multiplicity="<<d_summary.maximum<<"\na_signature_minimum_multiplicity="<<a_summary.minimum<<"\na_signature_maximum_multiplicity="<<a_summary.maximum<<"\nd_signature_collision_pair_count="<<d_summary.collision_pairs<<"\na_signature_collision_pair_count="<<a_summary.collision_pairs<<"\nfuture_profile_visit_count_per_engine="<<profile_visits<<"\nfuture_profile_value_operation_count_per_engine="<<profile_value_ops<<"\nfuture_profile_lower_bound_combined="<<profile_lower_combined<<"\ndense_cell_count="<<DENSE_CELLS<<"\ndense_profile_bytes="<<dense_profile_bytes<<"\ndense_pair_bytes="<<dense_pair_bytes<<"\ndense_all_signature_profile_bytes="<<dense_all_bytes<<"\nsparse_pair_payload_worst_case_bytes="<<sparse_pair_bytes<<"\nidentity_sample_count=1000\nidentity_sample_failure_count="<<identity_failures<<"\ncalibration_identity_verified="<<calibration<<"\nrow5_source_guard="<<row5_guard<<"\nsource_decomposition_verified="<<source_verified<<"\nshape_search_charge_total="<<search.total()<<"\nfeature_charge_total="<<feature_total<<"\nrank_charge_total="<<rank_charge<<"\nsignature_charge_total="<<signature_total<<"\nidentity_charge_total="<<identity_total<<"\nhash_byte_charge_total="<<hash_charge<<"\nstate_iteration_charge_total="<<state_iterations<<"\nsort_charge_bound="<<sort_charge<<"\nengine_preflight_charge="<<engine_preflight<<"\nprojected_combined_diagnostic_charge="<<projected_combined<<"\ncombined_charge_cap="<<DIAGNOSTIC_CAP<<"\nfeature_definition_sha256="<<feature_definition_sha<<"\nzero_code_sha256="<<zero_sha<<"\nb_feature_rows_sha256="<<b_rows_sha<<"\nc_feature_rows_sha256="<<c_rows_sha<<"\nd_feature_rows_sha256="<<d_rows_sha<<"\na_feature_rows_sha256="<<a_rows_sha<<"\nb_basis_sha256="<<b_basis_sha<<"\nc_basis_sha256="<<c_basis_sha<<"\nd_signature_multiplicity_sha256="<<d_summary.digest<<"\na_signature_multiplicity_sha256="<<a_summary.digest<<"\nidentity_samples_sha256="<<identity_sha<<"\nrlimit_as_soft="<<limit.rlim_cur<<"\nrlimit_as_hard="<<limit.rlim_max<<"\nREASONING_END\n";
}
"""
)

_INTEGER_FIELDS = {
    "field_prime",
    "feature_dimension",
    "pivot_pattern_count",
    "zero_bucket_count",
    "a_state_count",
    "d_state_count",
    "b_state_count",
    "c_state_count",
    "b_feature_rank",
    "c_feature_rank",
    "d_signature_count",
    "a_signature_count",
    "d_signature_multiplicity_mass",
    "a_signature_multiplicity_mass",
    "d_signature_minimum_multiplicity",
    "d_signature_maximum_multiplicity",
    "a_signature_minimum_multiplicity",
    "a_signature_maximum_multiplicity",
    "d_signature_collision_pair_count",
    "a_signature_collision_pair_count",
    "future_profile_visit_count_per_engine",
    "future_profile_value_operation_count_per_engine",
    "future_profile_lower_bound_combined",
    "dense_cell_count",
    "dense_profile_bytes",
    "dense_pair_bytes",
    "dense_all_signature_profile_bytes",
    "sparse_pair_payload_worst_case_bytes",
    "identity_sample_count",
    "identity_sample_failure_count",
    "calibration_identity_verified",
    "row5_source_guard",
    "source_decomposition_verified",
    "shape_search_charge_total",
    "feature_charge_total",
    "rank_charge_total",
    "signature_charge_total",
    "identity_charge_total",
    "hash_byte_charge_total",
    "state_iteration_charge_total",
    "sort_charge_bound",
    "engine_preflight_charge",
    "projected_combined_diagnostic_charge",
    "combined_charge_cap",
    "rlimit_as_soft",
    "rlimit_as_hard",
}
_HASH_FIELDS = {
    "feature_definition_sha256",
    "zero_code_sha256",
    "b_feature_rows_sha256",
    "c_feature_rows_sha256",
    "d_feature_rows_sha256",
    "a_feature_rows_sha256",
    "b_basis_sha256",
    "c_basis_sha256",
    "d_signature_multiplicity_sha256",
    "a_signature_multiplicity_sha256",
    "identity_samples_sha256",
}
_STRING_FIELDS = {"status", "next_gate"}


def _parse_transcript(stdout: str) -> dict[str, Any]:
    lines = stdout.strip().splitlines()
    frame = f"REASONING_AD5_COORDINATE3_FUNCTIONAL_SIGNATURE_V{_PROTOCOL_VERSION}"
    if len(lines) < 3 or lines[0] != frame or lines[-1] != "REASONING_END":
        raise ScientificToolError("AD5 coordinate-three signature transcript has invalid framing")
    fields: dict[str, str] = {}
    for line in lines[1:-1]:
        if "=" not in line:
            raise ScientificToolError("AD5 coordinate-three signature transcript has an invalid field")
        name, value = line.split("=", 1)
        if name in fields:
            raise ScientificToolError("AD5 coordinate-three signature transcript has duplicate fields")
        fields[name] = value
    if set(fields) != _INTEGER_FIELDS | _HASH_FIELDS | _STRING_FIELDS:
        raise ScientificToolError("AD5 coordinate-three signature transcript is incomplete")
    try:
        parsed: dict[str, Any] = {name: int(fields[name]) for name in _INTEGER_FIELDS}
    except ValueError as error:
        raise ScientificToolError("AD5 coordinate-three signature transcript has a nonintegral field") from error
    for name in _HASH_FIELDS:
        value = fields[name]
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ScientificToolError("AD5 coordinate-three signature transcript has an invalid digest")
        parsed[name] = value
    for name in _STRING_FIELDS:
        parsed[name] = fields[name]
    return parsed


def _run_program() -> dict[str, Any]:
    computation = _run_source_hashed_program(
        _PROGRAM,
        label="ad5-coordinate3-functional-signature",
        timeout_seconds=900,
        runtime_address_space_bytes=_RESIDENT_BYTE_CAP,
        preserve_exact_transcript=True,
    )
    return {**computation, **_parse_transcript(computation["transcript"])}


def _run_replay() -> dict[str, Any]:
    replay_path = Path(__file__).with_name("heisenberg_level25_four_block_ad5_signature_replay.py")
    try:
        program = replay_path.read_bytes()
    except OSError as error:
        raise ScientificToolError(f"independent AD5 signature replay is unavailable: {error}") from error
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
        raise ScientificToolError("independent AD5 signature replay exceeded 900 seconds") from error
    except OSError as error:
        raise ScientificToolError(f"independent AD5 signature replay could not start: {error}") from error
    if completed.returncode or completed.stderr.strip():
        detail = (completed.stderr.strip() or completed.stdout.strip() or "unknown replay failure")[:2000]
        raise ScientificToolError(f"independent AD5 signature replay failed: {detail}")
    try:
        replay = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise ScientificToolError("independent AD5 signature replay returned invalid JSON") from error
    if not isinstance(replay, dict):
        raise ScientificToolError("independent AD5 signature replay returned a non-object")
    return {
        **replay,
        "program_sha256": hashlib.sha256(program).hexdigest(),
        "transcript_sha256": hashlib.sha256(completed.stdout.encode()).hexdigest(),
        "transcript": completed.stdout,
        "runtime_address_space_limit_bytes": _RESIDENT_BYTE_CAP,
        "interpreter": str(Path(replay_python).resolve()),
    }


def level25_heisenberg_augmented_four_block_ad5_coordinate3_functional_signature_profile() -> dict[str, Any]:
    """Measure exact row-5 feature ranks and functional signature reuse."""

    fourier = level25_heisenberg_augmented_four_block_fourier_profile()
    fourier_sha256 = hashlib.sha256(canonical_json(fourier).encode()).hexdigest()
    phase = level25_heisenberg_augmented_four_block_phase_quotient_profile()
    computation = _run_program()
    replay = _run_replay()
    source_manifest = _source_manifest()
    compared_fields = tuple(
        sorted((_INTEGER_FIELDS - {"rlimit_as_soft", "rlimit_as_hard"}) | _HASH_FIELDS | _STRING_FIELDS)
    )
    replay_agreement = all(replay.get(name) == computation[name] for name in compared_fields)
    actual_combined_charge = computation["engine_preflight_charge"] + replay["engine_preflight_charge"]
    expected_next_gate = (
        "SPARSE_PROFILE_NEXT_GATE"
        if computation["projected_combined_diagnostic_charge"]
        + computation["future_profile_lower_bound_combined"]
        < _COMBINED_CHARGE_CAP
        else "CHARACTER_CONTRACTION_REQUIRED"
    )
    certificate = {
        "corrected_fourier_domain_is_the_certified_prerequisite": reviewed_profile_matches(fourier, _FOURIER_EVIDENCE_SHA256),
        "phase_quotient_is_the_certified_prerequisite": reviewed_profile_matches(phase, _PHASE_EVIDENCE_SHA256),
        "source_manifest_matches_executable_source": source_manifest["raw_record_count"] == 725,
        "primary_and_independent_replay_agree": replay_agreement,
        "source_derived_feature_decomposition_is_exact": computation["feature_dimension"] == _FEATURE_DIMENSION
        and computation["pivot_pattern_count"] == 3
        and computation["row5_source_guard"] == 1
        and computation["source_decomposition_verified"] == 1
        and computation["identity_sample_failure_count"] == 0
        and computation["calibration_identity_verified"] == 1,
        "domain_and_signature_multiplicity_masses_are_exact": computation["zero_bucket_count"] == 4_420
        and computation["a_state_count"] == computation["a_signature_multiplicity_mass"] == 4_420
        and computation["d_state_count"] == computation["d_signature_multiplicity_mass"] == 22_100
        and computation["b_state_count"] == 110_500
        and computation["c_state_count"] == 22_100,
        "decision_follows_the_exact_measured_lower_bound": computation["next_gate"] == expected_next_gate,
        "diagnostic_status_is_complete": computation["status"] == "SIGNATURE_CENSUS_COMPLETE",
        "combined_execution_respects_the_hard_cap": actual_combined_charge < _COMBINED_CHARGE_CAP,
        "both_engines_respect_the_hard_address_space_limit": computation["rlimit_as_soft"]
        == computation["rlimit_as_hard"]
        == replay["runtime_address_space_limit_bytes"]
        == _RESIDENT_BYTE_CAP,
        "programs_and_transcripts_match_reviewed_executions": exact_execution_matches(
            computation,
            parser=_parse_transcript,
            expected_program=_REVIEWED_PROGRAM_SHA256,
            expected_transcript=_REVIEWED_TRANSCRIPT_SHA256,
        )
        and independent_replay_matches(
            replay,
            expected_program=_REVIEWED_REPLAY_PROGRAM_SHA256,
            expected_transcript=_REVIEWED_REPLAY_TRANSCRIPT_SHA256,
        ),
    }
    request: dict[str, Any] = {}
    return seal_profile_evidence({
        "schema": "reasoning.level25-heisenberg-ad5-coordinate3-functional-signature-diagnostic.v3",
        "request": request,
        "request_sha256": hashlib.sha256(canonical_json(request).encode()).hexdigest(),
        "fourier_receipt_sha256": fourier_sha256,
        "fourier_evidence_sha256": fourier["evidence_sha256"],
        "phase_receipt_sha256": hashlib.sha256(canonical_json(phase).encode()).hexdigest(),
        "phase_evidence_sha256": phase["evidence_sha256"],
        "source_manifest": source_manifest,
        "feature_definition": {
            "field_prime": 101,
            "canonical_coordinate_order": "pivot source order, then q=0,1,2,3,4",
            "row": 5,
            "character": 2,
            "shift": 15,
            "pivot_exponents": [[1, 4, 20], [2, 13, 10], [4, 6, 15]],
            "phi": "COEFF[2][r+5q]*(scale_X*u_X[q])^alpha*(scale_X*u_X[q+1])^beta",
            "psi": "(scale_Y*u_Y[q+floor((r+2)/5)])^gamma, with the quotient index reduced modulo 5",
            "oriented_pairs": ["B->D", "A->C", "D->A"],
        },
        "computation": computation,
        "independent_replay": replay,
        "actual_combined_charge": actual_combined_charge,
        "certificate": certificate,
        "certificate_verified": all(certificate.values()),
        "decision": {
            "status": computation["status"],
            "next_gate": computation["next_gate"],
            "b_feature_rank": computation["b_feature_rank"],
            "c_feature_rank": computation["c_feature_rank"],
            "d_signature_count": computation["d_signature_count"],
            "a_signature_count": computation["a_signature_count"],
            "future_profile_visit_count_per_engine": computation["future_profile_visit_count_per_engine"],
            "future_profile_lower_bound_combined": computation["future_profile_lower_bound_combined"],
            "coordinate3_census_completed": False,
            "full_key_match_or_common_zero_found": False,
        },
        "interpretation": (
            "The diagnostic computes the exact quotient-dual functional signatures "
            "for the row-5 B-D and A-C pivot terms. It prices the literal reusable-"
            "profile state visits while retaining the requirement that any later "
            "count use joint F_101^3 prefix/row-value profiles, central shift, K_AD, "
            "and central multiplicities."
        ),
        "not_certified": [
            "construction of any joint F_101^3 B or C profile",
            "a prefix-conditioned coordinate-three convolution or solution count",
            "a full match in AD5 key coordinates four and five",
            "a seven-equation survivor or common zero",
            "any other Fourier fiber or support pattern",
            "geometric points over extensions of F_101 or a characteristic-zero lift",
            "global smoothness, a Brauer class, period, index, or period-index counterexample",
        ],
        "next_gate": {
            "feature": (
                "level25_heisenberg_augmented_four_block_ad5_coordinate3_sparse_profile"
                if computation["next_gate"] == "SPARSE_PROFILE_NEXT_GATE"
                else "level25_heisenberg_augmented_four_block_ad5_coordinate3_character_contraction"
            ),
            "requirement": (
                "Use the measured ranks and signature multiplicities to design an "
                "exact prefix-conditioned F_101^3 contraction retaining s(A,D), "
                "K_AD, central multiplicities, reflection signs, and independent "
                "replay; do not allocate persistent dense profile families."
            ),
        },
    })
