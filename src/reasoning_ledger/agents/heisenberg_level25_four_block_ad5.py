# ruff: noqa: E501
"""Bounded exact AD5 calibration on the first augmented four-block context."""

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
from .heisenberg_level25 import LEVEL25_HEISENBERG_BALANCED_CHARACTERS
from .heisenberg_level25_three_block import (
    _PROGRAM_PREFIX,
    _address_space_limited_command,
    _run_source_hashed_program,
)
from .heisenberg_level25_three_block_coverage import (
    _SELECTED_AUGMENTATION,
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
_RESIDENT_BYTE_CAP = 64 * 1024**2
_SEARCH_CHARGE_CAP = 270_000_000
_PRIMARY_REPLAY_CHARGE_CAP = 10_000_000
_INDEPENDENT_REPLAY_CHARGE_CAP = 20_000_000
_FOURIER_EVIDENCE_SHA256 = "8da803c2ceffb48c1cf5590e9c6334ed37d2760383231ee71b7720db99c46317"
_PHASE_EVIDENCE_SHA256 = "ddff1f79ec58bd2a093038a70d01483341988cc8d5c829e6fd8939fd6fecc95c"
_AD5_EVIDENCE_SHA256 = "f425e93832a5b366a0c8e6e4b1618c184a9ec8b1f6aa874e98a9b0d87326bcb8"
_REVIEWED_PROGRAM_SHA256 = "5650b2aef36ca801bda89048e4c0b4d2ddc52bd607eb023e850903d66bfeb3d1"
_REVIEWED_MATHEMATICAL_TRANSCRIPT_SHA256 = "53dfd0577aa35249cb20ace289f02c798e6a919d7e4e3ed9ba8e24f0fdbcd63b"
_REVIEWED_REPLAY_PROGRAM_SHA256 = "155659e7f4f8bf52cad37a4756572f8a1d327e3e689f96550f5080c859535043"
_REVIEWED_REPLAY_TRANSCRIPT_SHA256 = "508e2dd25fda738cbc3230a52aecfc1fc845c2aaac3d3037358e6365318b8c53"


def _source_records() -> list[tuple[int, int, int, tuple[int, ...], tuple[int, ...]]]:
    """Expand the executable augmented tuple into canonical raw orbit records."""

    records: list[tuple[int, int, int, tuple[int, ...], tuple[int, ...]]] = []
    for row, (character, shift) in enumerate(LEVEL25_HEISENBERG_BALANCED_CHARACTERS):
        for index in range(25):
            records.append((row, character, index, (index, (index - shift) % 25), (24, 1)))
    for index in range(25):
        records.append((0, 5, index, (index, (index + 1) % 25), (20, 5)))
    pivots = ((2, 1, 4, 20), (2, 2, 13, 10), (2, 4, 6, 15), (4, 1, 4, 20))
    row_by_character = {character: row for row, character in enumerate(LEVEL25_HEISENBERG_BALANCED_CHARACTERS)}
    for character, first, second, third in pivots:
        row = row_by_character[(character, 15)]
        for index in range(25):
            records.append((row, character, index, (index, (index + 5) % 25, (index + 2) % 25), (first, second, third)))
    for character, shift, first_offset, second_offset, middle_exponent, _kind in _SELECTED_AUGMENTATION:
        if shift != 0:
            raise RuntimeError("selected level-25 augmentation no longer has shift zero")
        row = row_by_character[(character, shift)]
        for index in range(25):
            records.append(
                (
                    row,
                    character,
                    index,
                    (index, (index + first_offset) % 25, (index + second_offset) % 25),
                    (1, middle_exponent, 24 - middle_exponent),
                )
            )
    return records


def _source_manifest() -> dict[str, Any]:
    records = _source_records()
    serialized = [
        {
            "row": row,
            "character": character,
            "cyclic_index": index,
            "coordinate_indices": list(indices),
            "coordinate_exponents": list(exponents),
            "residue_support": sorted({coordinate % 5 for coordinate in indices}),
        }
        for row, character, index, indices, exponents in records
    ]
    restricted = [record for record in serialized if 4 not in record["residue_support"]]
    return {
        "raw_record_count": len(serialized),
        "audit_claimed_raw_record_count": 675,
        "audit_count_matches_executable_source": len(serialized) == 675,
        "corrected_count_explanation": (
            "The executable tuple has 550 base records, 25 adjacent row-zero "
            "records, 100 nonadjacent pivot records, and 50 selected-orbit "
            "records. The model-authored audit omitted two 25-record sums."
        ),
        "records_sha256": hashlib.sha256(canonical_json(serialized).encode()).hexdigest(),
        "four_block_restricted_record_count": len(restricted),
        "four_block_restricted_records_sha256": hashlib.sha256(canonical_json(restricted).encode()).hexdigest(),
    }


_PROGRAM = (
    _PROGRAM_PREFIX
    + r"""
#include <fstream>
#include <limits>
#include <sstream>
#include <string>
#include <sys/resource.h>

using u64=std::uint64_t;
constexpr u64 EMPTY=std::numeric_limits<u64>::max();
constexpr std::size_t SLOTS=1U<<18;
constexpr u64 SPLITMIX_SEED=0x6a09e667f3bcc909ULL;
constexpr u64 SEARCH_CAP=270000000ULL;
constexpr int ZERO_COUNT=4420,NB=110500,NC=22100;
constexpr std::array<int,5> PHASE_ORDER{{0,3,1,4,2}};
constexpr std::array<std::array<int,4>,2> SELECTED{{{{9,2,19,18}},{{7,1,2,23}}}};

struct Charges{
 u64 add=0,sub=0,neg=0,mul=0,inverse_lookup=0,power_lookup=0,source_record=0,hash_slot=0,key_compare=0,duplicate_link=0,checksum_word=0;
 u64 total()const{return add+sub+neg+mul+inverse_lookup+power_lookup+source_record+hash_slot+key_compare+duplicate_link+checksum_word;}
};

std::array<std::array<int,26>,P> POW{};
std::array<std::array<int,N>,N> COEFF{};

void mixc(u64&checksum,u64 value,Charges&charge){++charge.checksum_word;for(int byte=0;byte<8;++byte){checksum^=(value>>(8*byte))&255;checksum*=1099511628211ULL;}}
int plusc(int first,int second,Charges&charge){++charge.add;int value=first+second;return value>=P?value-P:value;}
int subc(int first,int second,Charges&charge){++charge.sub;int value=first-second;return value<0?value+P:value;}
int negc(int value,Charges&charge){++charge.neg;return value?P-value:0;}
int mulc(int first,int second,Charges&charge){++charge.mul;return first*second%P;}
int powc(int value,int exponent,Charges&charge){++charge.power_lookup;return POW[value][exponent];}

std::array<int,5> decode_shape(std::uint32_t code,std::array<int,4>*exponents=nullptr){
 std::array<int,4> e{};std::uint32_t work=code;for(int q=3;q>=0;--q){e[q]=work%25;work/=25;}
 std::array<int,5>u{{1}};for(int q=1;q<5;++q)u[q]=POW[ZETA][e[q-1]];if(exponents)*exponents=e;return u;
}

int cyclic_bucket(const std::array<int,5>&u,Charges&charge){int value=0;for(int q=0;q<5;++q){++charge.inverse_lookup;int term=mulc(POW[u[q]][24],u[(q+1)%5],charge);value=plusc(value,term,charge);}return value;}

struct Block{int residue,scale;const std::array<int,5>*u;};
void put_block(const Block&block,std::array<int,N>&x){for(int q=0;q<5;++q)x[block.residue+5*q]=block.scale*(*block.u)[q]%P;}

int unary_value(int row,const Block&block,Charges&charge){
 auto [character,shift]=CH[row];if(shift%5)return 0;int value=0;
 for(int q=0;q<5;++q){++charge.source_record;int i=block.residue+5*q,j=(i-shift+N)%N;int jq=(j-block.residue)/5;int xi=block.scale*(*block.u)[q]%P,xj=block.scale*(*block.u)[jq]%P;int term=COEFF[character][i];term=mulc(term,powc(xi,24,charge),charge);term=mulc(term,xj,charge);value=plusc(value,term,charge);}return value;
}

int pivot_pair_value(int row,const Block&first,const Block&second,Charges&charge){
 std::array<int,N>x{};put_block(first,x);put_block(second,x);auto [character,shift]=CH[row];int value=0;
 for(auto pivot:PIV)if(character==pivot[0]&&shift==15)for(int i=0;i<N;++i){int j=(i+5)%N,k=(i+2)%N;if(!x[i]||!x[j]||!x[k])continue;++charge.source_record;int term=COEFF[character][i];term=mulc(term,powc(x[i],pivot[1],charge),charge);term=mulc(term,powc(x[j],pivot[2],charge),charge);term=mulc(term,powc(x[k],pivot[3],charge),charge);value=plusc(value,term,charge);}return value;
}

int row0_pair_value(const Block&first,const Block&second,Charges&charge){
 std::array<int,N>x{};put_block(first,x);put_block(second,x);int value=0;
 for(int i=0;i<N;++i){int j=(i+1)%N;if(!x[i]||!x[j])continue;++charge.source_record;int term=COEFF[5][i];term=mulc(term,powc(x[i],20,charge),charge);term=mulc(term,powc(x[j],5,charge),charge);value=plusc(value,term,charge);}return value;
}

int selected_value(int which,const Block&first,const Block&second,const Block&third,Charges&charge){
 std::array<int,N>x{};put_block(first,x);put_block(second,x);put_block(third,x);auto candidate=SELECTED[which];int character=candidate[0],value=0;
 for(int i=0;i<N;++i){int j=(i+candidate[1])%N,k=(i+candidate[2])%N;if(!x[i]||!x[j]||!x[k])continue;++charge.source_record;int term=COEFF[character][i];term=mulc(term,x[i],charge);term=mulc(term,powc(x[j],candidate[3],charge),charge);term=mulc(term,powc(x[k],24-candidate[3],charge),charge);value=plusc(value,term,charge);}return value;
}

int augmented_value(int row,const std::array<int,N>&x,Charges&charge){
 auto [character,shift]=CH[row];int value=0;
 for(int i=0;i<N;++i){++charge.source_record;int term=COEFF[character][i];term=mulc(term,powc(x[i],24,charge),charge);term=mulc(term,x[(i-shift+N)%N],charge);value=plusc(value,term,charge);}
 if(row==0)for(int i=0;i<N;++i){++charge.source_record;int term=COEFF[5][i];term=mulc(term,powc(x[i],20,charge),charge);term=mulc(term,powc(x[(i+1)%N],5,charge),charge);value=plusc(value,term,charge);}
 for(auto pivot:PIV)if(character==pivot[0]&&shift==15)for(int i=0;i<N;++i){++charge.source_record;int term=COEFF[character][i];term=mulc(term,powc(x[i],pivot[1],charge),charge);term=mulc(term,powc(x[(i+5)%N],pivot[2],charge),charge);term=mulc(term,powc(x[(i+2)%N],pivot[3],charge),charge);value=plusc(value,term,charge);}
 for(auto candidate:SELECTED)if(character==candidate[0]&&shift==0)for(int i=0;i<N;++i){++charge.source_record;int term=COEFF[character][i];term=mulc(term,x[i],charge);term=mulc(term,powc(x[(i+candidate[1])%N],candidate[3],charge),charge);term=mulc(term,powc(x[(i+candidate[2])%N],24-candidate[3],charge),charge);value=plusc(value,term,charge);}
 return value;
}

void selected_add_grad(int row,const std::array<int,N>&x,std::array<int,N>&gradient){
 auto [character,shift]=CH[row];for(auto candidate:SELECTED)if(character==candidate[0]&&shift==0)for(int i=0;i<N;++i){std::array<int,3>indices{{i,(i+candidate[1])%N,(i+candidate[2])%N}};std::array<int,3>exponents{{1,candidate[3],24-candidate[3]}};int coefficient=coeff(character,i);for(int factor=0;factor<3;++factor){int derivative=coefficient*exponents[factor]%P;for(int other=0;other<3;++other)derivative=derivative*pw(x[indices[other]],exponents[other]-(factor==other))%P;gradient[indices[factor]]=(gradient[indices[factor]]+derivative)%P;}}
}

std::array<std::array<int,N>,22> augmented_jacobian(const std::array<int,N>&x){std::array<std::array<int,N>,22>matrix{};for(int row=0;row<22;++row){add_grad(row,x,matrix[row]);selected_add_grad(row,x,matrix[row]);}return matrix;}
int matrix_rank(std::array<std::array<int,N>,22>matrix){int rank=0;for(int column=0;column<N&&rank<22;++column){int pivot=rank;while(pivot<22&&!matrix[pivot][column])++pivot;if(pivot==22)continue;std::swap(matrix[pivot],matrix[rank]);int inverse=pw(matrix[rank][column],P-2);for(int c=column;c<N;++c)matrix[rank][c]=matrix[rank][c]*inverse%P;for(int row=0;row<22;++row)if(row!=rank&&matrix[row][column]){int factor=matrix[row][column];for(int c=column;c<N;++c){matrix[row][c]=(matrix[row][c]-factor*matrix[rank][c])%P;if(matrix[row][c]<0)matrix[row][c]+=P;}}++rank;}return rank;}

u64 pack5(const std::array<int,5>&key){u64 packed=0;for(int value:key)packed=packed*P+value;return packed;}
u64 splitmix(u64 value){value+=0x9e3779b97f4a7c15ULL;value=(value^(value>>30))*0xbf58476d1ce4e5b9ULL;value=(value^(value>>27))*0x94d049bb133111ebULL;return value^(value>>31);}

std::array<int,5> b_key(const Block&B,const Block&D,const Block&A,Charges&charge){return {{unary_value(6,B,charge),unary_value(8,B,charge),plusc(unary_value(5,B,charge),pivot_pair_value(5,B,D,charge),charge),plusc(unary_value(7,B,charge),pivot_pair_value(7,B,D,charge),charge),selected_value(0,B,D,A,charge)}};}
std::array<int,5> c_key(const Block&C,const Block&D,const Block&A,Charges&charge){
 std::array<int,5> key{};key[0]=negc(plusc(plusc(unary_value(6,A,charge),unary_value(6,C,charge),charge),unary_value(6,D,charge),charge),charge);key[1]=negc(plusc(plusc(unary_value(8,A,charge),unary_value(8,C,charge),charge),unary_value(8,D,charge),charge),charge);
 int row5=plusc(plusc(unary_value(5,A,charge),unary_value(5,C,charge),charge),unary_value(5,D,charge),charge);row5=plusc(row5,pivot_pair_value(5,A,C,charge),charge);row5=plusc(row5,pivot_pair_value(5,D,A,charge),charge);key[2]=negc(row5,charge);
 int row7=plusc(plusc(unary_value(7,A,charge),unary_value(7,C,charge),charge),unary_value(7,D,charge),charge);row7=plusc(row7,pivot_pair_value(7,A,C,charge),charge);row7=plusc(row7,pivot_pair_value(7,D,A,charge),charge);key[3]=negc(row7,charge);key[4]=negc(selected_value(0,D,A,C,charge),charge);return key;
}

u64 vm_peak_kib(){std::ifstream input("/proc/self/status");std::string line;while(std::getline(input,line))if(line.rfind("VmPeak:",0)==0){std::istringstream stream(line.substr(7));u64 value=0;stream>>value;return value;}return 0;}

template<class T>void print_list(const T&values){bool first=true;for(auto value:values){if(!first)std::cout<<",";first=false;std::cout<<value;}std::cout<<"\n";}

int main(){
 struct rlimit limit{};getrlimit(RLIMIT_AS,&limit);if(limit.rlim_cur!=67108864ULL||limit.rlim_max!=67108864ULL)return 70;
 for(int value=0;value<P;++value){POW[value][0]=1;for(int exponent=1;exponent<=25;++exponent)POW[value][exponent]=POW[value][exponent-1]*value%P;}for(int character=0;character<N;++character)for(int index=0;index<N;++index)COEFF[character][index]=coeff(character,index);
 Charges search;std::array<u64,P>histogram{};std::vector<std::uint32_t>zero;zero.reserve(ZERO_COUNT);u64 histogram_checksum=1469598103934665603ULL,zero_checksum=1469598103934665603ULL;
 for(std::uint32_t code=0;code<390625;++code){auto shape=decode_shape(code);int bucket=cyclic_bucket(shape,search);++histogram[bucket];if(!bucket)zero.push_back(code);}
 for(int bucket=0;bucket<P;++bucket){mixc(histogram_checksum,bucket,search);mixc(histogram_checksum,histogram[bucket],search);}for(auto code:zero)mixc(zero_checksum,code,search);
 std::array<int,4>first_exponents{};auto first_shape=decode_shape(zero.empty()?0:zero[0],&first_exponents);bool shape_guard=zero.size()==ZERO_COUNT&&zero[0]==280&&first_exponents==std::array<int,4>{{0,0,11,5}}&&first_shape==std::array<int,5>{{1,1,1,79,95}};
 if(!shape_guard)return 71;
 const auto&fixed_shape=first_shape;Block A{0,1,&fixed_shape},D{3,1,&fixed_shape};u64 decomposition_sample_count=0,decomposition_sample_failure_count=0;
 for(int sample=0;sample<64;++sample){int jb=sample%5,phase_position=(sample/5)%5,jc=(sample/25)%5;auto bshape=decode_shape(zero[(sample*37)%zero.size()]),cshape=decode_shape(zero[(sample*101+17)%zero.size()]);Block B{1,pw(GEN,4*jb+20*PHASE_ORDER[phase_position]),&bshape},C{2,pw(GEN,4*jc),&cshape};auto bk=b_key(B,D,A,search),ck=c_key(C,D,A,search);std::array<int,N>x{};put_block(A,x);put_block(B,x);put_block(C,x);put_block(D,x);std::array<int,5>rows{{6,8,5,7,11}};bool valid=true;for(int key_index=0;key_index<5;++key_index)if(augmented_value(rows[key_index],x,search)!=(bk[key_index]-ck[key_index]+P)%P)valid=false;int e0=unary_value(0,A,search);e0=plusc(e0,unary_value(0,B,search),search);e0=plusc(e0,unary_value(0,C,search),search);e0=plusc(e0,unary_value(0,D,search),search);e0=plusc(e0,row0_pair_value(A,B,search),search);e0=plusc(e0,row0_pair_value(B,C,search),search);e0=plusc(e0,row0_pair_value(C,D,search),search);if(augmented_value(0,x,search)!=e0)valid=false;int q7=unary_value(9,A,search);q7=plusc(q7,unary_value(9,B,search),search);q7=plusc(q7,unary_value(9,C,search),search);q7=plusc(q7,unary_value(9,D,search),search);q7=plusc(q7,selected_value(1,A,B,C,search),search);q7=plusc(q7,selected_value(1,B,C,D,search),search);if(augmented_value(9,x,search)!=q7)valid=false;++decomposition_sample_count;if(!valid)++decomposition_sample_failure_count;}
 if(decomposition_sample_failure_count)return 76;
 std::vector<u64>keys(SLOTS,EMPTY);std::vector<std::uint32_t>heads(SLOTS,std::numeric_limits<std::uint32_t>::max()),counts(SLOTS),next(NB,std::numeric_limits<std::uint32_t>::max());u64 b_checksum=1469598103934665603ULL,c_checksum=1469598103934665603ULL,match_checksum=1469598103934665603ULL;u64 occupied=0,max_insert_probe=0,max_lookup_probe=0;
 std::uint32_t state=0;for(int jb=0;jb<5;++jb)for(int phase_position=0;phase_position<5;++phase_position){int scale=pw(GEN,4*jb+20*PHASE_ORDER[phase_position]);for(std::uint32_t shape_position=0;shape_position<zero.size();++shape_position,++state){auto shape=decode_shape(zero[shape_position]);Block B{1,scale,&shape};u64 key=pack5(b_key(B,D,A,search));mixc(b_checksum,state,search);mixc(b_checksum,key,search);std::size_t slot=splitmix(key^SPLITMIX_SEED)&(SLOTS-1);u64 probes=0;while(true){++search.hash_slot;++probes;if(keys[slot]==EMPTY){keys[slot]=key;heads[slot]=state;counts[slot]=1;++occupied;break;}++search.key_compare;if(keys[slot]==key){next[state]=heads[slot];heads[slot]=state;++counts[slot];break;}slot=(slot+1)&(SLOTS-1);}max_insert_probe=std::max(max_insert_probe,probes);if(search.total()>SEARCH_CAP)return 72;}}
 if(state!=NB)return 73;
 std::vector<u64>ckeys(NC);std::vector<std::uint32_t>cslots(NC);u64 matches=0;state=0;for(int jc=0;jc<5;++jc){int scale=pw(GEN,4*jc);for(std::uint32_t shape_position=0;shape_position<zero.size();++shape_position,++state){auto shape=decode_shape(zero[shape_position]);Block C{2,scale,&shape};u64 key=pack5(c_key(C,D,A,search));std::size_t slot=splitmix(key^SPLITMIX_SEED)&(SLOTS-1);u64 probes=0,matched=0;while(true){++search.hash_slot;++probes;if(keys[slot]==EMPTY)break;++search.key_compare;if(keys[slot]==key){matched=counts[slot];break;}slot=(slot+1)&(SLOTS-1);}ckeys[state]=key;cslots[state]=slot;matches+=matched;mixc(c_checksum,state,search);mixc(c_checksum,key,search);mixc(c_checksum,probes,search);mixc(c_checksum,matched,search);max_lookup_probe=std::max(max_lookup_probe,probes);if(search.total()>SEARCH_CAP)return 74;}}
 if(state!=NC)return 75;
 Charges one_residual;std::array<int,N>dummy{};for(int row:std::array<int,7>{{0,5,6,7,8,9,11}})(void)augmented_value(row,dummy,one_residual);u64 residual_per_match=one_residual.total()+3;bool fanout=matches&&((SEARCH_CAP-search.total())/residual_per_match<matches);u64 residual_tests=0,survivors=0,decomposition_failures=0;std::array<int,N>witness{};std::array<int,22>witness_values{};std::array<std::array<int,N>,22>witness_jacobian{};int witness_rank=-1;u64 primary_replay_charge=0;
 if(!fanout){for(std::uint32_t cid=0;cid<NC&&!survivors;++cid){std::size_t slot=cslots[cid];if(keys[slot]==EMPTY||keys[slot]!=ckeys[cid])continue;int jc=cid/zero.size(),shape_position=cid%zero.size(),cscale=pw(GEN,4*jc);auto cshape=decode_shape(zero[shape_position]);Block C{2,cscale,&cshape};for(std::uint32_t bid=heads[slot];bid!=std::numeric_limits<std::uint32_t>::max();bid=next[bid]){++search.duplicate_link;++residual_tests;mixc(match_checksum,cid,search);mixc(match_checksum,bid,search);int work=bid/zero.size(),bshape_position=bid%zero.size(),phase_position=work%5,jb=work/5,bscale=pw(GEN,4*jb+20*PHASE_ORDER[phase_position]);auto bshape=decode_shape(zero[bshape_position]);Block B{1,bscale,&bshape};std::array<int,N>x{};put_block(A,x);put_block(B,x);put_block(C,x);put_block(D,x);std::array<int,7>values{};int vi=0;for(int row:std::array<int,7>{{0,5,6,7,8,9,11}})values[vi++]=augmented_value(row,x,search);if(values[1]||values[2]||values[3]||values[4]||values[6]){++decomposition_failures;break;}if(values[0]||values[5])continue;++survivors;witness=x;Charges replay;for(int row=0;row<22;++row)witness_values[row]=augmented_value(row,x,replay);witness_jacobian=augmented_jacobian(x);witness_rank=matrix_rank(witness_jacobian);primary_replay_charge=replay.total()+250000;break;}}
 }
 std::string status=fanout?"FIRST_AD5_CONTEXT_FANOUT_PROFILED":survivors?(witness_rank<22?"PRIMARY_SINGULAR_WITNESS_PENDING_INDEPENDENT_REPLAY":"PRIMARY_COMMON_ZERO_RANK_22_PENDING_INDEPENDENT_REPLAY"):decomposition_failures?"DECOMPOSITION_MISMATCH":"FIRST_AD5_CONTEXT_EMPTY";
 std::cout<<"REASONING_BOUNDED_AD5_FIRST_CONTEXT_V1\nstatus="<<status<<"\nfield_prime=101\nsource_record_count=725\naudit_claimed_source_record_count=675\naudit_count_matches_source=0\nshape_count=390625\nzero_bucket_count="<<zero.size()<<"\nhistogram_checksum="<<histogram_checksum<<"\nzero_bucket_checksum="<<zero_checksum<<"\nfirst_zero_shape_code="<<zero[0]<<"\nfirst_zero_shape_exponents=";print_list(first_exponents);std::cout<<"first_zero_shape_values=";print_list(first_shape);std::cout<<"decomposition_sample_count="<<decomposition_sample_count<<"\ndecomposition_sample_failure_count="<<decomposition_sample_failure_count<<"\nb_state_count="<<NB<<"\nc_state_count="<<NC<<"\nlogical_cartesian_pair_count="<<u64(NB)*NC<<"\nhash_slot_count="<<SLOTS<<"\nhash_occupied_count="<<occupied<<"\nmaximum_insert_probe="<<max_insert_probe<<"\nmaximum_lookup_probe="<<max_lookup_probe<<"\nexact_key_match_count="<<matches<<"\nresidual_charge_per_match="<<residual_per_match<<"\nresidual_tests_completed="<<residual_tests<<"\nresidual_survivor_count="<<survivors<<"\ndecomposition_failure_count="<<decomposition_failures<<"\nb_record_checksum="<<b_checksum<<"\nc_record_checksum="<<c_checksum<<"\nmatch_stream_checksum="<<match_checksum<<"\nsearch_add_charges="<<search.add<<"\nsearch_subtract_charges="<<search.sub<<"\nsearch_negate_charges="<<search.neg<<"\nsearch_multiply_charges="<<search.mul<<"\nsearch_inverse_lookup_charges="<<search.inverse_lookup<<"\nsearch_power_lookup_charges="<<search.power_lookup<<"\nsearch_source_record_charges="<<search.source_record<<"\nsearch_hash_slot_charges="<<search.hash_slot<<"\nsearch_key_compare_charges="<<search.key_compare<<"\nsearch_duplicate_link_charges="<<search.duplicate_link<<"\nsearch_checksum_word_charges="<<search.checksum_word<<"\nsearch_charge_total="<<search.total()<<"\nsearch_charge_cap="<<SEARCH_CAP<<"\nprimary_replay_charge_total="<<primary_replay_charge<<"\nprimary_replay_charge_cap=10000000\nindependent_replay_charge_cap=20000000\nrlimit_as_soft="<<limit.rlim_cur<<"\nrlimit_as_hard="<<limit.rlim_max<<"\nvm_peak_kib="<<vm_peak_kib()<<"\nwitness_rank="<<witness_rank<<"\nwitness_coordinates=";if(survivors)print_list(witness);else std::cout<<"\n";std::cout<<"witness_equation_values=";if(survivors)print_list(witness_values);else std::cout<<"\n";std::cout<<"witness_jacobian=";if(survivors){bool first=true;for(auto&row:witness_jacobian)for(int value:row){if(!first)std::cout<<",";first=false;std::cout<<value;}std::cout<<"\n";}else std::cout<<"\n";std::cout<<"REASONING_END\n";
}
"""
)


_INTEGER_FIELDS = {
    "field_prime",
    "source_record_count",
    "audit_claimed_source_record_count",
    "audit_count_matches_source",
    "shape_count",
    "zero_bucket_count",
    "histogram_checksum",
    "zero_bucket_checksum",
    "first_zero_shape_code",
    "decomposition_sample_count",
    "decomposition_sample_failure_count",
    "b_state_count",
    "c_state_count",
    "logical_cartesian_pair_count",
    "hash_slot_count",
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
    "search_add_charges",
    "search_subtract_charges",
    "search_negate_charges",
    "search_multiply_charges",
    "search_inverse_lookup_charges",
    "search_power_lookup_charges",
    "search_source_record_charges",
    "search_hash_slot_charges",
    "search_key_compare_charges",
    "search_duplicate_link_charges",
    "search_checksum_word_charges",
    "search_charge_total",
    "search_charge_cap",
    "primary_replay_charge_total",
    "primary_replay_charge_cap",
    "independent_replay_charge_cap",
    "rlimit_as_soft",
    "rlimit_as_hard",
    "vm_peak_kib",
    "witness_rank",
}


def _csv_ints(value: str, *, count: int | tuple[int, ...], label: str) -> list[int]:
    if not value:
        parsed: list[int] = []
    else:
        try:
            parsed = [int(item) for item in value.split(",")]
        except ValueError as error:
            raise ScientificToolError(f"bounded AD5 transcript has a nonintegral {label}") from error
    allowed = (count,) if isinstance(count, int) else count
    if len(parsed) not in allowed:
        raise ScientificToolError(f"bounded AD5 transcript has a malformed {label}")
    return parsed


def _parse_transcript(stdout: str) -> dict[str, Any]:
    lines = stdout.strip().splitlines()
    frame = f"REASONING_BOUNDED_AD5_FIRST_CONTEXT_V{_PROTOCOL_VERSION}"
    if len(lines) < 3 or lines[0] != frame or lines[-1] != "REASONING_END":
        raise ScientificToolError("bounded AD5 transcript has invalid framing")
    fields: dict[str, str] = {}
    for line in lines[1:-1]:
        if "=" not in line:
            raise ScientificToolError("bounded AD5 transcript has an invalid field")
        name, value = line.split("=", 1)
        if name in fields:
            raise ScientificToolError("bounded AD5 transcript has duplicate fields")
        fields[name] = value
    list_fields = {
        "first_zero_shape_exponents",
        "first_zero_shape_values",
        "witness_coordinates",
        "witness_equation_values",
        "witness_jacobian",
    }
    if set(fields) != _INTEGER_FIELDS | list_fields | {"status"}:
        raise ScientificToolError("bounded AD5 transcript is incomplete")
    try:
        parsed: dict[str, Any] = {name: int(fields[name]) for name in _INTEGER_FIELDS}
    except ValueError as error:
        raise ScientificToolError("bounded AD5 transcript has a nonintegral field") from error
    parsed.update(
        {
            "status": fields["status"],
            "first_zero_shape_exponents": _csv_ints(fields["first_zero_shape_exponents"], count=4, label="first shape exponents"),
            "first_zero_shape_values": _csv_ints(fields["first_zero_shape_values"], count=5, label="first shape values"),
            "witness_coordinates": _csv_ints(fields["witness_coordinates"], count=(0, 25), label="witness"),
            "witness_equation_values": _csv_ints(fields["witness_equation_values"], count=(0, 22), label="witness equations"),
            "witness_jacobian": _csv_ints(fields["witness_jacobian"], count=(0, 22 * 25), label="witness Jacobian"),
        }
    )
    return parsed


def _run_program() -> dict[str, Any]:
    computation = _run_source_hashed_program(
        _PROGRAM,
        label="bounded-ad5-first-context",
        timeout_seconds=900,
        runtime_address_space_bytes=_RESIDENT_BYTE_CAP,
        preserve_exact_transcript=True,
    )
    return {
        **computation,
        **_parse_transcript(computation["transcript"]),
        "mathematical_transcript_sha256": _mathematical_transcript_sha256(computation["transcript"]),
    }


def _mathematical_transcript_sha256(stdout: str) -> str:
    """Bind exact framed output except the one separately cap-checked memory line."""
    return bounded_transcript_sha256(stdout, parser=_parse_transcript)


def _reviewed_execution_matches(computation: dict[str, Any], independent: dict[str, Any]) -> bool:
    return bounded_execution_matches(
        computation,
        parser=_parse_transcript,
        expected_program=_REVIEWED_PROGRAM_SHA256,
        expected_mathematics=_REVIEWED_MATHEMATICAL_TRANSCRIPT_SHA256,
    ) and independent_replay_matches(
        independent,
        expected_program=_REVIEWED_REPLAY_PROGRAM_SHA256,
        expected_transcript=_REVIEWED_REPLAY_TRANSCRIPT_SHA256,
    )


def _run_independent_replay(witness: list[int]) -> dict[str, Any]:
    replay_path = Path(__file__).with_name("heisenberg_level25_four_block_ad5_replay.py")
    try:
        program = replay_path.read_bytes()
    except OSError as error:
        raise ScientificToolError(f"independent bounded AD5 replay is unavailable: {error}") from error
    replay_python = shutil.which("python3", path="/usr/local/bin:/usr/bin:/bin") or sys.executable
    command = _address_space_limited_command(
        [replay_python, "-I", "-S", str(replay_path), ",".join(map(str, witness)) if witness else "-"],
        _RESIDENT_BYTE_CAP,
    )
    environment = {"HOME": "", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")}
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
        raise ScientificToolError("independent bounded AD5 replay exceeded 300 seconds") from error
    except OSError as error:
        raise ScientificToolError(f"independent bounded AD5 replay could not start: {error}") from error
    if completed.returncode or completed.stderr.strip():
        detail = (completed.stderr.strip() or completed.stdout.strip() or "unknown replay failure")[:2000]
        raise ScientificToolError(f"independent bounded AD5 replay failed: {detail}")
    try:
        replay = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise ScientificToolError("independent bounded AD5 replay returned invalid JSON") from error
    if not isinstance(replay, dict):
        raise ScientificToolError("independent bounded AD5 replay returned a non-object")
    return {
        **replay,
        "program_sha256": hashlib.sha256(program).hexdigest(),
        "transcript_sha256": hashlib.sha256(completed.stdout.encode()).hexdigest(),
        "transcript": completed.stdout,
        "runtime_address_space_limit_bytes": _RESIDENT_BYTE_CAP,
        "interpreter": str(Path(replay_python).resolve()),
    }


def level25_heisenberg_augmented_four_block_bounded_ad5_profile() -> dict[str, Any]:
    """Profile or exhaust the first deterministic AD5 central context."""

    fourier = level25_heisenberg_augmented_four_block_fourier_profile()
    fourier_sha256 = hashlib.sha256(canonical_json(fourier).encode()).hexdigest()
    phase = level25_heisenberg_augmented_four_block_phase_quotient_profile()
    phase_sha256 = hashlib.sha256(canonical_json(phase).encode()).hexdigest()
    computation = _run_program()
    source_manifest = _source_manifest()
    independent = _run_independent_replay(computation["witness_coordinates"])
    witness = computation["witness_coordinates"]
    replay_agreement = not witness or (
        independent.get("coordinates") == witness
        and independent.get("equation_values") == computation["witness_equation_values"]
        and independent.get("jacobian") == computation["witness_jacobian"]
        and independent.get("rank") == computation["witness_rank"]
    )
    independent_domain_agreement = all(
        independent[name] == computation[name]
        for name in (
            "source_record_count",
            "shape_count",
            "zero_bucket_count",
            "histogram_checksum",
            "zero_bucket_checksum",
            "first_zero_shape_code",
            "first_zero_shape_exponents",
            "first_zero_shape_values",
        )
    )
    status = computation["status"]
    if status == "PRIMARY_SINGULAR_WITNESS_PENDING_INDEPENDENT_REPLAY" and replay_agreement:
        status = "REPLAYED_SINGULAR_WITNESS_FOUND"
    elif status == "PRIMARY_COMMON_ZERO_RANK_22_PENDING_INDEPENDENT_REPLAY" and replay_agreement:
        status = "REPLAYED_COMMON_ZERO_RANK_22"
    certificate = {
        "corrected_fourier_domain_is_the_certified_prerequisite": reviewed_profile_matches(fourier, _FOURIER_EVIDENCE_SHA256),
        "phase_quotient_is_the_certified_prerequisite": reviewed_profile_matches(phase, _PHASE_EVIDENCE_SHA256)
        and phase["computation"]["canonical_representatives"] == [[0, 0, 0, 0], [0, 3, 0, 0], [0, 1, 0, 0], [0, 4, 0, 0], [0, 2, 0, 0]],
        "source_manifest_corrects_the_audit_record_count": source_manifest["raw_record_count"] == computation["source_record_count"] == 725
        and not source_manifest["audit_count_matches_executable_source"]
        and computation["audit_count_matches_source"] == 0,
        "first_context_domain_is_recomputed": computation["shape_count"] == 390625
        and computation["zero_bucket_count"] == 4420
        and computation["first_zero_shape_code"] == 280
        and computation["first_zero_shape_exponents"] == [0, 0, 11, 5]
        and computation["first_zero_shape_values"] == [1, 1, 1, 79, 95],
        "independent_scalar_domain_replay_agrees": independent_domain_agreement,
        "opposite_state_domains_are_exact": computation["b_state_count"] == 110500
        and computation["c_state_count"] == 22100
        and computation["logical_cartesian_pair_count"] == 2_442_050_000,
        "hash_table_has_the_declared_collision_free_capacity": computation["hash_slot_count"] == 2**18
        and computation["hash_occupied_count"] <= computation["b_state_count"] < computation["hash_slot_count"],
        "reduced_decomposition_matches_direct_source_evaluation": computation["decomposition_sample_count"] == 64
        and computation["decomposition_sample_failure_count"] == 0,
        "search_and_replay_respect_hard_caps": computation["search_charge_total"] <= _SEARCH_CHARGE_CAP == computation["search_charge_cap"]
        and computation["primary_replay_charge_total"] <= _PRIMARY_REPLAY_CHARGE_CAP == computation["primary_replay_charge_cap"]
        and independent["charged_operations"] <= _INDEPENDENT_REPLAY_CHARGE_CAP == computation["independent_replay_charge_cap"]
        and computation["rlimit_as_soft"]
        == computation["rlimit_as_hard"]
        == independent["runtime_address_space_limit_bytes"]
        == _RESIDENT_BYTE_CAP,
        "atomic_context_semantics_are_respected": (
            status == "FIRST_AD5_CONTEXT_FANOUT_PROFILED" and computation["residual_tests_completed"] == 0
        )
        or (
            status != "FIRST_AD5_CONTEXT_FANOUT_PROFILED"
            and computation["residual_tests_completed"] == computation["exact_key_match_count"]
        ),
        "key_decomposition_has_no_direct_replay_failure": computation["decomposition_failure_count"] == 0,
        "independent_replay_agrees_when_a_witness_exists": replay_agreement,
        "resident_telemetry_respects_the_address_space_cap": 0 < computation["vm_peak_kib"] * 1024 <= _RESIDENT_BYTE_CAP,
        "program_and_mathematical_transcript_match_reviewed_execution": _reviewed_execution_matches(computation, independent),
    }
    request: dict[str, Any] = {}
    profile = {
        "schema": "reasoning.level25-heisenberg-augmented-four-block-bounded-ad5-profile.v2",
        "request": request,
        "request_sha256": hashlib.sha256(canonical_json(request).encode()).hexdigest(),
        "context": {"t": 0, "c_b": 0, "c_c": 0, "c_d": 0, "j_d": 0, "shape_a": 280, "shape_d": 280, "scale_a": 1, "scale_d": 1},
        "fourier_receipt_sha256": fourier_sha256,
        "fourier_evidence_sha256": fourier["evidence_sha256"],
        "phase_receipt_sha256": phase_sha256,
        "phase_evidence_sha256": phase["evidence_sha256"],
        "source_manifest": source_manifest,
        "computation": computation,
        "independent_replay": independent,
        "certificate": certificate,
        "certificate_verified": all(certificate.values()),
        "decision": {
            "status": status,
            "smoothness_candidate_rejected": status == "REPLAYED_SINGULAR_WITNESS_FOUND",
            "context_exhausted": status == "FIRST_AD5_CONTEXT_EMPTY",
        },
        "interpretation": (
            "This collision-free five-coordinate AD5 join covers exactly the first deterministic central context. "
            "It never materializes the 2,442,050,000-element B-C Cartesian product, applies an atomic fanout gate, "
            "directly reevaluates all seven residual equations at every tested match, and independently replays any first common zero."
        ),
        "not_certified": [
            "any other AD5 central context or Fourier fiber",
            "absence of F_101-rational or geometric four-block common zeros outside this context",
            "the full-support rational stratum or a characteristic-zero lift",
            "global smoothness, integrality, a Brauer class, period, index bound, or a period-index counterexample",
        ],
        "next_gate": {
            "feature": "level25_heisenberg_augmented_four_block_bounded_ad5_context_batch",
            "requirement": "Use the measured match fanout and charge profile to select the next deterministic batch of central contexts without weakening exact replay or route constraints.",
        },
    }
    return seal_profile_evidence(profile)
