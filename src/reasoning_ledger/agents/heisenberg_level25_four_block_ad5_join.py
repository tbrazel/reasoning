# ruff: noqa: E501
"""Exact AD5 coordinate-three five-key join covariance certificate."""

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
    level25_heisenberg_augmented_four_block_ad5_coordinate3_tensor_orbit_profile,
)
from .heisenberg_level25_four_block_ad5_orbit import _PROGRAM as _ORBIT_PROGRAM
from .heisenberg_level25_four_block_ad5_slice import (
    _SLICE_EVIDENCE_SHA256,
    level25_heisenberg_augmented_four_block_ad5_coordinate3_central_orbit_representative_preflight_profile,
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
_ENGINE_PREFLIGHT_CAP = 5_000_000
_COMBINED_CHARGE_CAP = 10_000_000
_JOIN_EVIDENCE_SHA256 = "c9b5beeb78f48c3f05ef7bab2876c1c7ed5d88555c0084110d0299457cf58fb8"
_ORBIT_ARTIFACT_SHA256 = "6e82e5e14d114990c8fa424ece26c9643dcb902d07d8a4d132c14ee81eef2c11"
_ORBIT_SOURCE_RECORD_SHA256 = "fe272b5f7dddd1fc039d83614060520b8eba364ba13a422d48fa39ef6e9a925e"
_ORBIT_ZERO_SHAPE_SHA256 = "23acfac609eaa758dc9bede4f47fd60acf8cc7333e5580765f113c1b649175bd"
_ORBIT_SHAPE_ACTION_SHA256 = "3a53cc518e2628cd5c7804e78056fd5f93065dffeba7fcd52c0bd8dcd5df73a8"
_ORBIT_RECEIPT_SHA256 = "0ae2c3a469647b3824dd3b18c035603097a1f2b2208a1d3de3d5e8b2f285b41f"
_SLICE_ARTIFACT_SHA256 = "fd24e7cddf5f88f49b34df34a7de697d41a12eafdd595d712aa2bb89bc2c9560"
_SLICE_SHAPE_SHA256 = "f160568c8456b21812d2a67a380f9224bf03f8be39070d5aaf1278fd195c5e15"
_SLICE_ACTION_SHA256 = "593a9f252c776d059768f1754577998ea7fb0715b54d92a032a278cce32f2504"
_SLICE_REPRESENTATIVE_SHA256 = "a9d128cc5614b2dbffad31c2fa04aa372f5bbacb4397143e4f74e52f23704dc2"
_SLICE_RECEIPT_SHA256 = "2a13bda6bded0d2195b84ac3d7c5b5ad6bf16e5d7f7e9dd14096cbfdabe3f41d"
_NATIVE_DESIGN_ARTIFACT_PATH = "research/four-block/iteration-135/ad5-coordinate3-join-partition-covariance-design-native.md"
_NATIVE_DESIGN_SHA256 = "aae0d42c40889542735feecf702fbc09fd199fdcefe5f627079e349b9f8bfbf6"
_CODEX_AUDIT_ARTIFACT_PATH = "research/four-block/iteration-135/ad5-coordinate3-join-partition-covariance-design-audit-codex.md"
_CODEX_AUDIT_SHA256 = "e106d0a6024ea148b5b2adcb560b3b560ae50916dfc3b59a6a6d429c52928c9e"
_REVIEWED_PROGRAM_SHA256 = "57616d00c957d292d9403fb5646db843d3835f229f48b0f3d662f7998ff720fe"
_REVIEWED_MATHEMATICAL_TRANSCRIPT_SHA256 = "58e6d8156bb4f2753dcd5615fb5d2146a88e6921429e8a581d9205ce4d667430"
_REVIEWED_REPLAY_PROGRAM_SHA256 = "e4678cbc4a45254f317e5b1b88d47afd2119739159d8fe0c05d068d3566a2986"
_REVIEWED_REPLAY_TRANSCRIPT_SHA256 = "fcd4fe10127357daa8087f31a38023d7e15715e9f4c85bfce9b0ad93a97fcb5f"

_SUPPORT, _MAIN_SEPARATOR, _OLD_MAIN = _ORBIT_PROGRAM.partition("\nint main(){")
if not _MAIN_SEPARATOR:
    raise RuntimeError("AD5 orbit support program no longer has the expected main")

_PROGRAM = (
    _SUPPORT
    + r"""
#include <bit>
#include <limits>

constexpr u64 JOIN_ENGINE_CAP=5000000ULL;
constexpr u64 JOIN_COMBINED_CAP=10000000ULL;
constexpr u64 JOIN_SHAPES=4420ULL,JOIN_B_STATES=110500ULL,JOIN_C_STATES=22100ULL;
constexpr std::array<int,5> JOIN_KEY_ROWS{{6,8,5,7,11}};
constexpr std::array<int,5> JOIN_PHASE_ORDER{{0,3,1,4,2}};

struct JoinHash{
 Sha256 hash;u64 bytes=0;
 explicit JoinHash(const std::string&tag){raw(tag);byte(0);}
 void byte(int value){hash.byte(std::uint8_t(value));++bytes;}
 void raw(const std::string&value){for(unsigned char item:value)byte(item);}
 void raw_hex(const std::string&value){for(std::size_t offset=0;offset<value.size();offset+=2)byte(std::stoi(value.substr(offset,2),nullptr,16));}
 void u16le(std::uint16_t value){byte(value&255);byte((value>>8)&255);}
 void u32le(std::uint32_t value){for(int shift=0;shift<=24;shift+=8)byte((value>>shift)&255);}
 void u64le(u64 value){for(int shift=0;shift<=56;shift+=8)byte((value>>shift)&255);}
 std::string finish(){return hash.finish();}
};

int join_support_mask(const Record&record){int mask=0;for(int i=0;i<3&&i<record.arity;++i)mask|=1<<(record.indices[i]%5);return mask;}
bool join_key_row(int row){return std::find(JOIN_KEY_ROWS.begin(),JOIN_KEY_ROWS.end(),row)!=JOIN_KEY_ROWS.end();}
bool join_covered_row(int row){return join_key_row(row)||row==0||row==9;}
int join_classify(const Record&record){
 int row=record.row,mask=join_support_mask(record);if((mask&16)||!join_covered_row(row))return 0;
 if(row==6||row==8)return mask==2?1:(mask==1||mask==4||mask==8?2:0);
 if(row==5||row==7)return (mask==2||mask==10)?1:(mask==1||mask==4||mask==8||mask==5||mask==9?2:0);
 if(row==11){if(record.arity==2)return 4;return mask==11?1:(mask==13?2:0);}
 if(row==0)return 3;
 if(row==9){if(record.arity==2)return 4;return (mask==7||mask==14)?3:0;}
 return 0;
}
std::array<int,4>join_degrees(const Record&record){std::array<int,4>out{};for(int i=0;i<3&&i<record.arity;++i){int residue=record.indices[i]%5;if(residue<4)out[residue]=(out[residue]+record.exponents[i])%5;}return out;}
int join_weight(const Record&record){int value=0;for(int i=0;i<3&&i<record.arity;++i)value=(value+record.indices[i]*record.exponents[i])%25;return value;}
u64 join_pack_key(const std::array<std::uint8_t,5>&key){u64 packed=0;for(auto value:key)packed=P*packed+value;return packed;}
u64 join_splitmix(u64 value){value+=0x9e3779b97f4a7c15ULL;value=(value^(value>>30))*0xbf58476d1ce4e5b9ULL;value=(value^(value>>27))*0x94d049bb133111ebULL;return value^(value>>31);}
u64 join_fnv(const std::array<std::uint8_t,5>&key){u64 value=14695981039346656037ULL;for(auto byte:key){value^=byte;value*=1099511628211ULL;}return value;}

int main(){
 struct rlimit limit{};getrlimit(RLIMIT_AS,&limit);if(limit.rlim_cur!=67108864ULL||limit.rlim_max!=67108864ULL)return 70;
 const std::string orbit_artifact="6e82e5e14d114990c8fa424ece26c9643dcb902d07d8a4d132c14ee81eef2c11",orbit_source="fe272b5f7dddd1fc039d83614060520b8eba364ba13a422d48fa39ef6e9a925e",orbit_zero_shape="23acfac609eaa758dc9bede4f47fd60acf8cc7333e5580765f113c1b649175bd",orbit_action="3a53cc518e2628cd5c7804e78056fd5f93065dffeba7fcd52c0bd8dcd5df73a8",orbit_receipt="0ae2c3a469647b3824dd3b18c035603097a1f2b2208a1d3de3d5e8b2f285b41f",slice_artifact="fd24e7cddf5f88f49b34df34a7de697d41a12eafdd595d712aa2bb89bc2c9560",slice_shape="f160568c8456b21812d2a67a380f9224bf03f8be39070d5aaf1278fd195c5e15",slice_action="593a9f252c776d059768f1754577998ea7fb0715b54d92a032a278cce32f2504",slice_representative="a9d128cc5614b2dbffad31c2fa04aa372f5bbacb4397143e4f74e52f23704dc2",slice_receipt="2a13bda6bded0d2195b84ac3d7c5b5ad6bf16e5d7f7e9dd14096cbfdabe3f41d",native_design="aae0d42c40889542735feecf702fbc09fd199fdcefe5f627079e349b9f8bfbf6",codex_audit="e106d0a6024ea148b5b2adcb560b3b560ae50916dfc3b59a6a6d429c52928c9e";
 auto records=source_records();std::set<Record>record_set(records.begin(),records.end());std::map<Record,std::size_t>record_ordinal;for(std::size_t ordinal=0;ordinal<records.size();++ordinal)record_ordinal[records[ordinal]]=ordinal;
 u64 restricted=0,key_restricted=0,residual_restricted=0,vanishing=0,partition_failures=0,translation_checks=0,restricted_translation_checks=0,effective_translation_checks=0,translation_failures=0,inverse_translation_failures=0,total_degree_checks=0,total_degree_failures=0,block_phase_checks=0,block_phase_failures=0,source_charge=records.size();
 std::array<u64,22>key_counts{},b_counts{},c_counts{},raw_by_row{},supported_by_row{},b_by_row{},c_by_row{},residual_by_row{},vanishing_by_row{};std::set<std::array<int,4>>key_b_degrees,key_c_degrees,residual_degrees;
 const std::array<int,22>expected_weights{{5,0,0,0,0,10,5,10,20,0,0,0,0,0,0,0,0,0,0,0,0,0}};
 JoinHash partition_hash("AD5C3-SOURCE-PARTITION-v1"),source_record_hash("AD5C3-SOURCE-PARTITION-v3"),translation_hash("AD5C3-TRANSLATION-CLOSURE-v3");
 for(std::size_t ordinal=0;ordinal<records.size();++ordinal){const auto&record=records[ordinal];int side=join_classify(record);bool supported=!(join_support_mask(record)&16);++raw_by_row[record.row];supported_by_row[record.row]+=supported;restricted+=supported;if(side==1)++b_by_row[record.row];else if(side==2)++c_by_row[record.row];else if(side==3)++residual_by_row[record.row];else if(side==4)++vanishing_by_row[record.row];int sign=(side==1||side==3)?1:(side==2?100:0);auto degree=join_degrees(record);std::array<int,25>exponent_vector{};for(int i=0;i<3&&i<record.arity;++i)exponent_vector[record.indices[i]]+=record.exponents[i];int source_side=side==1?1:(side==2?2:(side==4?3:0)),source_sign=side==2?100:1;source_record_hash.u16le(ordinal);for(int value:std::array<int,6>{{record.row,source_side,source_sign,coeff(record.character,record.start),record.character,supported}})source_record_hash.byte(value);for(auto value:exponent_vector)source_record_hash.byte(value);
  partition_hash.u32le(ordinal);partition_hash.byte(ordinal/25);partition_hash.byte(ordinal%25);partition_hash.byte(supported);partition_hash.byte(record.row);partition_hash.byte(side);partition_hash.byte(sign);partition_hash.byte(coeff(record.character,record.start));partition_hash.byte(record.arity);for(int i=0;i<3&&i<record.arity;++i){partition_hash.byte(record.indices[i]);partition_hash.byte(record.exponents[i]);}partition_hash.byte(side==4?record.start%5:255);
  for(int a=0;a<5;++a){Record translated=record;translated.start=(record.start+5*a)%25;for(int i=0;i<3&&i<record.arity;++i)translated.indices[i]=(record.indices[i]+5*a)%25;Record inverse=translated;int inverse_start=int(translated.start)-5*a;inverse_start%=25;if(inverse_start<0)inverse_start+=25;inverse.start=inverse_start;for(int i=0;i<3&&i<record.arity;++i){int value=int(translated.indices[i])-5*a;value%=25;if(value<0)value+=25;inverse.indices[i]=value;}int translated_coefficient=coeff(record.character,translated.start),expected_factor=pw(ZETA,((-5*record.character*a)%25+25)%25),expected_coefficient=coeff(record.character,record.start)*expected_factor%P,translated_weight=join_weight(translated);partition_hash.byte(translated_coefficient);partition_hash.byte(translated_weight);++translation_checks;restricted_translation_checks+=supported;effective_translation_checks+=side==1||side==2||side==3;translation_failures+=!record_set.count(translated)||(!(join_support_mask(translated)&16))!=supported||join_classify(translated)!=side||translated_coefficient!=expected_coefficient||translated_weight!=join_weight(record);inverse_translation_failures+=!(inverse==record);if(supported){translation_hash.u16le(ordinal);translation_hash.byte(a);auto found=record_ordinal.find(translated);translation_hash.u16le(found==record_ordinal.end()?65535:found->second);translation_hash.byte(expected_factor);}++source_charge;}
  ++total_degree_checks;int total_degree=0;for(int i=0;i<3&&i<record.arity;++i)total_degree+=record.exponents[i];total_degree_failures+=total_degree!=25;
  if(!supported||!join_covered_row(record.row))continue;if(!side){++partition_failures;continue;}
  if(join_key_row(record.row)){++key_restricted;++key_counts[record.row];if(side==1)++b_counts[record.row];else if(side==2)++c_counts[record.row];}else ++residual_restricted;
  if(side==4)++vanishing;
  ++source_charge;if(side==1||side==2||side==3)partition_failures+=join_weight(record)!=expected_weights[record.row];
  if(((side==1||side==2)&&record.row!=11)||(side==3&&record.row==0))for(auto value:degree){++block_phase_checks;block_phase_failures+=value!=0;++source_charge;}
  if(record.row==11&&side==1)key_b_degrees.insert(degree);if(record.row==11&&side==2)key_c_degrees.insert(degree);if(record.row==9&&side==3)residual_degrees.insert(degree);
 }
 std::ostringstream row_table_stream;JoinHash row_table_hash("AD5C3-ROW-COUNT-TABLE-v1");for(int row=0;row<22;++row){u64 discarded=raw_by_row[row]-b_by_row[row]-c_by_row[row]-residual_by_row[row]-vanishing_by_row[row];if(row)row_table_stream<<';';row_table_stream<<row<<':'<<raw_by_row[row]<<','<<supported_by_row[row]<<','<<b_by_row[row]<<','<<c_by_row[row]<<','<<residual_by_row[row]<<','<<vanishing_by_row[row]<<','<<discarded;row_table_hash.byte(row);for(u64 value:std::array<u64,7>{{raw_by_row[row],supported_by_row[row],b_by_row[row],c_by_row[row],residual_by_row[row],vanishing_by_row[row],discarded}})row_table_hash.u32le(value);}std::string row_count_table=row_table_stream.str(),row_count_table_sha=row_table_hash.finish();
 u64 unary_sum_checks=0,unary_sum_terms=0,unary_sum_failures=0;for(int row:std::array<int,2>{{11,9}}){auto [character,shift]=CH[row];partition_failures+=shift!=0||character%5==0;for(int residue=0;residue<4;++residue){int value=0;for(int quotient=0;quotient<5;++quotient){value=(value+coeff(character,residue+5*quotient))%P;++unary_sum_terms;++source_charge;}++unary_sum_checks;unary_sum_failures+=value!=0;partition_hash.byte(row);partition_hash.byte(residue);partition_hash.byte(value);}}
 const std::array<u64,22>expected_key_counts{{0,0,0,0,0,65,20,35,20,0,0,30}},expected_b_counts{{0,0,0,0,0,20,5,10,5,0,0,5}},expected_c_counts{{0,0,0,0,0,45,15,25,15,0,0,5}};
 partition_failures+=key_counts!=expected_key_counts;partition_failures+=b_counts!=expected_b_counts;partition_failures+=c_counts!=expected_c_counts;partition_failures+=residual_restricted!=65||vanishing!=40;
 const std::string expected_row_count_table="0:50,35,0,0,35,0,15;1:25,20,0,0,0,0,25;2:25,20,0,0,0,0,25;3:25,20,0,0,0,0,25;4:25,20,0,0,0,0,25;5:100,65,20,45,0,0,35;6:25,20,5,15,0,0,5;7:50,35,10,25,0,0,15;8:25,20,5,15,0,0,5;9:50,30,0,0,10,20,20;10:25,20,0,0,0,0,25;11:50,30,5,5,0,20,20;12:25,20,0,0,0,0,25;13:25,20,0,0,0,0,25;14:25,20,0,0,0,0,25;15:25,20,0,0,0,0,25;16:25,20,0,0,0,0,25;17:25,20,0,0,0,0,25;18:25,20,0,0,0,0,25;19:25,20,0,0,0,0,25;20:25,20,0,0,0,0,25;21:25,20,0,0,0,0,25";partition_failures+=row_count_table!=expected_row_count_table;
 partition_failures+=key_b_degrees!=std::set<std::array<int,4>>{{{{1,1,0,3}}}};partition_failures+=key_c_degrees!=std::set<std::array<int,4>>{{{{3,0,1,1}}}};partition_failures+=residual_degrees!=std::set<std::array<int,4>>{{{{0,1,3,1}},{{1,3,1,0}}}};
 std::string source_partition_sha=partition_hash.finish(),source_record_schema_sha=source_record_hash.finish(),translation_closure_sha=translation_hash.finish();

 JoinHash predicate_hash("AD5C3-FIVE-KEY-PREDICATE-v1");predicate_hash.raw("rows=6,8,5,7,11;KB=U6(B)|U8(B)|U5(B)+P5(B,D)|U7(B)+P7(B,D)|Q9(B,D,A);KC=-(U6(A)+U6(C)+U6(D)|U8(A)+U8(C)+U8(D)|U5(A)+U5(C)+U5(D)+P5(A,C)+P5(D,A)|U7(A)+U7(C)+U7(D)+P7(A,C)+P7(D,A)|Q9(D,A,C));residual=rows0,9;record=key[5]:u8|state_id:u32le");std::string predicate_schema_sha=predicate_hash.finish();

 const std::array<int,4>kernel{{1,2,3,4}},key_b=*key_b_degrees.begin(),key_c=*key_c_degrees.begin();std::vector<std::array<int,4>>residual_degree_list(residual_degrees.begin(),residual_degrees.end());JoinHash phase_hash("AD5C3-PHASE-KERNEL-v3");u64 phase_tuple_classifications=0,kernel_tuples=0,key_phase_checks=0,residual_phase_checks=0,phase_failures=0,phase_charge=0;
 for(int l0=0;l0<5;++l0)for(int l1=0;l1<5;++l1)for(int l2=0;l2<5;++l2)for(int l3=0;l3<5;++l3){std::array<int,4>phase{{l0,l1,l2,l3}};int kernel_dot=0;for(int i=0;i<4;++i)kernel_dot+=kernel[i]*phase[i];bool in_kernel=kernel_dot%5==0;++phase_tuple_classifications;for(auto value:phase)phase_hash.byte(value);phase_hash.byte(in_kernel);if(!in_kernel)continue;++kernel_tuples;int b_factor=0,c_factor=0,r0_factor=0,r1_factor=0;for(int i=0;i<4;++i){b_factor+=key_b[i]*phase[i];c_factor+=key_c[i]*phase[i];r0_factor+=residual_degree_list[0][i]*phase[i];r1_factor+=residual_degree_list[1][i]*phase[i];}b_factor%=5;c_factor%=5;r0_factor%=5;r1_factor%=5;++key_phase_checks;++residual_phase_checks;phase_failures+=b_factor!=c_factor;phase_failures+=r0_factor!=r1_factor;phase_hash.byte(b_factor);phase_hash.byte(c_factor);phase_hash.byte(r0_factor);phase_hash.byte(r1_factor);phase_charge+=2;}
 std::string phase_kernel_sha=phase_hash.finish();

 JoinHash decompose_hash("AD5C3-DECOMPOSE-SCALE-TABLE-v1");u64 decompose_table_checks=0,decompose_table_failures=0;for(int raw=0;raw<25;++raw){auto [residue,ell]=decompose_scale(raw);int reconstructed=(21*residue+5*ell)%25;bool scale_matches=pw(ZETA,raw)==pw(ZETA,21*residue+5*ell);decompose_table_failures+=reconstructed!=raw||!scale_matches;for(int value:std::array<int,5>{{raw,residue,ell,reconstructed,scale_matches}})decompose_hash.byte(value);++decompose_table_checks;}for(int h=0;h<5;++h){int phase_position=JOIN_PHASE_ORDER[h],phase_exponent=20*phase_position%100,canonical_exponent=15*h%25;bool scale_matches=pw(GEN,phase_exponent)==pw(ZETA,canonical_exponent);decompose_table_failures+=!scale_matches;for(int value:std::array<int,5>{{h,phase_position,phase_exponent,canonical_exponent,scale_matches}})decompose_hash.byte(value);++decompose_table_checks;}std::string decompose_table_sha=decompose_hash.finish();
 JoinHash scale_hash("AD5C3-TRIANGULAR-SCALE-v1"),c_scale_hash("AD5C3-SCALE-BIJECTION-C-v3"),b_scale_hash("AD5C3-SCALE-BIJECTION-B-v3");u64 c_fibres=0,c_checks=0,c_failures=0;for(int delta_c=0;delta_c<25;++delta_c){std::array<int,5>seen{};for(int jc=0;jc<5;++jc){auto [jprime,ell]=decompose_scale(21*jc+delta_c);++seen[jprime];for(int value:std::array<int,4>{{delta_c,jc,jprime,ell}}){scale_hash.byte(value);c_scale_hash.byte(value);}++c_checks;}++c_fibres;c_failures+=seen!=std::array<int,5>{{1,1,1,1,1}};}
 u64 b_fibres=0,b_checks=0,b_failures=0;for(int delta_b=0;delta_b<25;++delta_b)for(int ell_c=0;ell_c<5;++ell_c)for(int ell_d=0;ell_d<5;++ell_d){std::array<int,25>seen{};for(int jb=0;jb<5;++jb)for(int h=0;h<5;++h){auto [jprime,ell_b]=decompose_scale(21*jb+15*h+delta_b);int hprime=(2*ell_b+3*ell_c+4*ell_d)%5;++seen[5*jprime+hprime];for(int value:std::array<int,7>{{delta_b,ell_c,ell_d,jb,h,jprime,hprime}}){scale_hash.byte(value);b_scale_hash.byte(value);}++b_checks;}++b_fibres;b_failures+=seen!=std::array<int,25>{{1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1}};}
 std::string scale_bijection_sha=scale_hash.finish(),c_scale_bijection_sha=c_scale_hash.finish(),b_scale_bijection_sha=b_scale_hash.finish();u64 scale_charge=decompose_table_checks+c_checks+b_checks;

 JoinHash schema_hash("AD5C3-LOSSLESS-RECORD-SCHEMA-v1");schema_hash.raw("key[5]:u8;state_id:u32le");u64 key_byte_checks=0,key_byte_failures=0,schema_charge=0;for(int position=0;position<5;++position)for(int value=0;value<P;++value){std::array<std::uint8_t,5>key{};key[position]=value;key_byte_failures+=key[position]!=value;schema_hash.byte(position);for(auto item:key)schema_hash.byte(item);++key_byte_checks;++schema_charge;}
 u64 state_id_checks=0,state_id_failures=0,state_id_coverage_failures=0;std::vector<std::uint8_t>b_seen((JOIN_B_STATES+7)/8,0),c_seen((JOIN_C_STATES+7)/8,0);for(int jb=0;jb<5;++jb)for(int h=0;h<5;++h){int kb=(21*jb+15*h)%25,evaluator_scale=pw(GEN,(4*jb+20*JOIN_PHASE_ORDER[h])%100),canonical_scale=pw(ZETA,kb);for(std::uint32_t shape=0;shape<JOIN_SHAPES;++shape){std::uint32_t state_id=(5*jb+h)*JOIN_SHAPES+shape,work=state_id/JOIN_SHAPES;state_id_failures+=state_id%JOIN_SHAPES!=shape||int(work/5)!=jb||int(work%5)!=h;state_id_failures+=evaluator_scale!=canonical_scale;std::array<std::uint8_t,5>key{};for(int position=0;position<5;++position)key[position]=(state_id*(position+1)+17*position+1)%P;state_id_coverage_failures+=(b_seen[state_id>>3]>>(state_id&7))&1;b_seen[state_id>>3]|=1u<<(state_id&7);for(auto value:key)schema_hash.byte(value);schema_hash.u32le(state_id);schema_hash.u32le(shape);schema_hash.byte(jb);schema_hash.byte(h);schema_hash.byte(kb);++state_id_checks;++schema_charge;}}
 for(int jc=0;jc<5;++jc){int kc=21*jc%25,evaluator_scale=pw(GEN,4*jc),canonical_scale=pw(ZETA,kc);for(std::uint32_t shape=0;shape<JOIN_SHAPES;++shape){std::uint32_t state_id=jc*JOIN_SHAPES+shape;state_id_failures+=state_id%JOIN_SHAPES!=shape||int(state_id/JOIN_SHAPES)!=jc;state_id_failures+=evaluator_scale!=canonical_scale;std::array<std::uint8_t,5>key{};for(int position=0;position<5;++position)key[position]=(state_id*(position+1)+17*position+2)%P;state_id_coverage_failures+=(c_seen[state_id>>3]>>(state_id&7))&1;c_seen[state_id>>3]|=1u<<(state_id&7);for(auto value:key)schema_hash.byte(value);schema_hash.u32le(state_id);schema_hash.u32le(shape);schema_hash.byte(jc);schema_hash.byte(kc);++state_id_checks;++schema_charge;}}
 u64 b_seen_count=0,c_seen_count=0;for(auto value:b_seen)b_seen_count+=std::popcount(value);for(auto value:c_seen)c_seen_count+=std::popcount(value);state_id_coverage_failures+=b_seen_count!=JOIN_B_STATES;state_id_coverage_failures+=c_seen_count!=JOIN_C_STATES;for(auto value:b_seen)schema_hash.byte(value);for(auto value:c_seen)schema_hash.byte(value);
 std::string state_schema_sha=schema_hash.finish();

 JoinHash sentinel_hash("AD5C3-LINKED-JOIN-SENTINEL-v1");using Key=std::array<std::uint8_t,5>;const Key key17{{17,1,2,3,4}},key42{{42,9,8,7,6}},key99{{99,0,100,50,25}},collision_key{{1,100,99,98,97}};const std::array<Key,8>b_keys{{key17,key17,collision_key,key17,key42,key99,key42,Key{{7,6,5,4,3}}}};const std::array<Key,5>c_keys{{key17,key42,key42,Key{{8,6,5,4,3}},key99}};std::array<int,8>heads;heads.fill(-1);std::array<int,8>next_record;next_record.fill(-1);u64 sentinel_failures=0;
 for(std::size_t state_id=0;state_id<b_keys.size();++state_id){std::size_t bucket=join_fnv(b_keys[state_id])&7;next_record[state_id]=heads[bucket];heads[bucket]=state_id;}
 u64 pair_count=0,collision_filter_count=0;std::map<u64,u64>key_pairs;for(std::size_t c_id=0;c_id<c_keys.size();++c_id){u64 packed=join_pack_key(c_keys[c_id]);std::size_t bucket=join_fnv(c_keys[c_id])&7;int b_id=heads[bucket];while(b_id>=0){sentinel_hash.u32le(c_id);sentinel_hash.u32le(b_id);for(auto value:c_keys[c_id])sentinel_hash.byte(value);bool equal=b_keys[b_id]==c_keys[c_id];sentinel_hash.byte(equal);collision_filter_count+=!equal;if(equal){++pair_count;++key_pairs[packed];}b_id=next_record[b_id];}}
 sentinel_failures+=pair_count!=8;sentinel_failures+=collision_filter_count<1;sentinel_failures+=key_pairs!=std::map<u64,u64>{{join_pack_key(key17),3},{join_pack_key(key42),4},{join_pack_key(key99),1}};std::string join_sentinel_sha=sentinel_hash.finish();u64 sentinel_charge=b_keys.size()+c_keys.size()+pair_count+collision_filter_count;

 JoinHash manifest_hash("AD5C3-COVARIANCE-INPUT-MANIFEST-v1");for(const auto&digest:std::array<std::string,12>{{orbit_artifact,orbit_source,orbit_zero_shape,orbit_action,orbit_receipt,slice_artifact,slice_shape,slice_action,slice_representative,slice_receipt,native_design,codex_audit}})manifest_hash.raw_hex(digest);std::string input_manifest_sha=manifest_hash.finish();
 u64 hash_charge=partition_hash.bytes+source_record_hash.bytes+translation_hash.bytes+predicate_hash.bytes+phase_hash.bytes+decompose_hash.bytes+scale_hash.bytes+c_scale_hash.bytes+b_scale_hash.bytes+schema_hash.bytes+sentinel_hash.bytes+manifest_hash.bytes+row_table_hash.bytes,engine_charge=source_charge+phase_charge+scale_charge+schema_charge+sentinel_charge+hash_charge;JoinHash charge_hash("AD5C3-COVARIANCE-CHARGES-v1");for(u64 value:std::array<u64,7>{{source_charge,phase_charge,scale_charge,schema_charge,sentinel_charge,hash_charge,engine_charge}})charge_hash.u64le(value);std::string charge_sha=charge_hash.finish();
 u64 key_effective=0,b_count=0,c_count=0;for(auto value:b_counts)b_count+=value;for(auto value:c_counts)c_count+=value;key_effective=b_count+c_count;u64 residual_effective=residual_restricted-20,effective=key_effective+residual_effective;
 bool success=records.size()==725&&restricted==535&&key_restricted==170&&residual_restricted==65&&vanishing==40&&key_effective==150&&residual_effective==45&&effective==195&&b_count==45&&c_count==105&&!partition_failures&&translation_checks==3625&&restricted_translation_checks==2675&&effective_translation_checks==975&&!translation_failures&&!inverse_translation_failures&&total_degree_checks==725&&!total_degree_failures&&block_phase_checks==700&&!block_phase_failures&&unary_sum_checks==8&&unary_sum_terms==40&&!unary_sum_failures&&phase_tuple_classifications==625&&kernel_tuples==125&&key_phase_checks==125&&residual_phase_checks==125&&!phase_failures&&decompose_table_checks==30&&!decompose_table_failures&&c_fibres==25&&c_checks==125&&!c_failures&&b_fibres==625&&b_checks==15625&&!b_failures&&state_id_checks==JOIN_B_STATES+JOIN_C_STATES&&key_byte_checks==505&&!key_byte_failures&&!state_id_failures&&!state_id_coverage_failures&&!sentinel_failures&&engine_charge<JOIN_ENGINE_CAP;
 std::string status=success?"T3_FIVE_KEY_PARTITION_AND_TRIANGULAR_COVARIANCE_CERTIFIED":"JOIN_COVARIANCE_CERTIFICATE_MISMATCH";
 std::cout<<"REASONING_AD5_COORDINATE3_JOIN_COVARIANCE_V1\nstatus="<<status
 <<"\nsource_record_count="<<records.size()<<"\nrestricted_source_record_count="<<restricted<<"\ncovered_source_record_count="<<key_restricted+residual_restricted
 <<"\nkey_row_count="<<JOIN_KEY_ROWS.size()<<"\nkey_restricted_record_count="<<key_restricted<<"\nresidual_restricted_record_count="<<residual_restricted<<"\nvanishing_unary_record_count="<<vanishing
 <<"\neffective_partition_record_count="<<effective<<"\nkey_effective_record_count="<<key_effective<<"\nresidual_effective_record_count="<<residual_effective<<"\nb_partition_record_count="<<b_count<<"\nc_partition_record_count="<<c_count
 <<"\nsource_partition_failure_count="<<partition_failures<<"\ntranslation_check_count="<<translation_checks<<"\nrestricted_translation_check_count="<<restricted_translation_checks<<"\neffective_translation_check_count="<<effective_translation_checks<<"\ntranslation_failure_count="<<translation_failures<<"\ninverse_translation_failure_count="<<inverse_translation_failures
 <<"\ntotal_degree_check_count="<<total_degree_checks<<"\ntotal_degree_failure_count="<<total_degree_failures<<"\nblock_phase_check_count="<<block_phase_checks<<"\nblock_phase_failure_count="<<block_phase_failures
 <<"\nunary_sum_check_count="<<unary_sum_checks<<"\nunary_sum_term_count="<<unary_sum_terms<<"\nunary_sum_failure_count="<<unary_sum_failures<<"\nphase_tuple_classification_count="<<phase_tuple_classifications<<"\nphase_kernel_tuple_count="<<kernel_tuples<<"\nkey_phase_check_count="<<key_phase_checks<<"\nresidual_phase_check_count="<<residual_phase_checks<<"\nphase_kernel_failure_count="<<phase_failures
 <<"\ndecompose_table_check_count="<<decompose_table_checks<<"\ndecompose_table_failure_count="<<decompose_table_failures
 <<"\nc_scale_bijection_fibre_count="<<c_fibres<<"\nc_scale_bijection_check_count="<<c_checks<<"\nc_scale_bijection_failure_count="<<c_failures<<"\nb_scale_bijection_fibre_count="<<b_fibres<<"\nb_scale_bijection_check_count="<<b_checks<<"\nb_scale_bijection_failure_count="<<b_failures
 <<"\nb_state_count="<<JOIN_B_STATES<<"\nc_state_count="<<JOIN_C_STATES<<"\nstate_id_check_count="<<state_id_checks<<"\nstate_id_failure_count="<<state_id_failures<<"\nstate_id_coverage_failure_count="<<state_id_coverage_failures<<"\nkey_byte_roundtrip_check_count="<<key_byte_checks<<"\nkey_byte_roundtrip_failure_count="<<key_byte_failures
 <<"\nrecord_width_bytes=9\nb_record_stream_bytes="<<JOIN_B_STATES*9<<"\nc_record_stream_bytes="<<JOIN_C_STATES*9<<"\njoin_sentinel_b_record_count="<<b_keys.size()<<"\njoin_sentinel_c_record_count="<<c_keys.size()<<"\njoin_sentinel_pair_count="<<pair_count<<"\njoin_sentinel_collision_filter_count="<<collision_filter_count<<"\njoin_sentinel_failure_count="<<sentinel_failures
 <<"\njoin_hash_slot_count=262144\njoin_materialized_array_bytes=3051096\njoin_charged_array_cap_bytes=4099672\njoin_two_engine_array_cap_bytes=8199344\njoin_table_bookkeeping_charge=2316680"
 <<"\nrepresentative_count=3907280\njoin_predicate_equivariance_certified="<<success<<"\nrepresentative_outer_domain_authorized="<<success<<"\njoin_execution_completed=0\ncontraction_authorized=0\ncommon_zero_established=0"
 <<"\nsource_charge_total="<<source_charge<<"\nphase_charge_total="<<phase_charge<<"\nscale_bijection_charge_total="<<scale_charge<<"\nschema_charge_total="<<schema_charge<<"\njoin_sentinel_charge_total="<<sentinel_charge<<"\nhash_charge_total="<<hash_charge<<"\nengine_preflight_charge="<<engine_charge<<"\nengine_preflight_cap="<<JOIN_ENGINE_CAP
 <<"\nrlimit_as_soft="<<limit.rlim_cur<<"\nrlimit_as_hard="<<limit.rlim_max<<"\nvm_peak_kib="<<vm_peak_kib()
 <<"\norbit_artifact_sha256="<<orbit_artifact<<"\norbit_source_record_sha256="<<orbit_source<<"\norbit_zero_shape_sha256="<<orbit_zero_shape<<"\norbit_shape_action_sha256="<<orbit_action<<"\norbit_receipt_sha256="<<orbit_receipt
 <<"\nslice_artifact_sha256="<<slice_artifact<<"\nslice_shape_sha256="<<slice_shape<<"\nslice_action_sha256="<<slice_action<<"\nslice_representative_sha256="<<slice_representative<<"\nslice_receipt_sha256="<<slice_receipt
 <<"\nnative_design_sha256="<<native_design<<"\ncodex_audit_sha256="<<codex_audit<<"\ninput_manifest_sha256="<<input_manifest_sha<<"\nsource_partition_sha256="<<source_partition_sha<<"\nsource_record_schema_sha256="<<source_record_schema_sha<<"\ntranslation_closure_sha256="<<translation_closure_sha<<"\nrow_count_table_sha256="<<row_count_table_sha
 <<"\npredicate_schema_sha256="<<predicate_schema_sha<<"\nphase_kernel_sha256="<<phase_kernel_sha<<"\ndecompose_table_sha256="<<decompose_table_sha<<"\nscale_bijection_sha256="<<scale_bijection_sha<<"\nc_scale_bijection_sha256="<<c_scale_bijection_sha<<"\nb_scale_bijection_sha256="<<b_scale_bijection_sha<<"\nstate_schema_sha256="<<state_schema_sha<<"\njoin_sentinel_sha256="<<join_sentinel_sha<<"\ncharge_transcript_sha256="<<charge_sha
 <<"\njoin_predicate_equivariance="<<(success?"CERTIFIED":"UNPROVED")<<"\nrow_count_table="<<row_count_table<<"\nREASONING_END\n";
}
"""
)

_INTEGER_FIELDS = {
    "source_record_count",
    "restricted_source_record_count",
    "covered_source_record_count",
    "key_row_count",
    "key_restricted_record_count",
    "residual_restricted_record_count",
    "vanishing_unary_record_count",
    "effective_partition_record_count",
    "key_effective_record_count",
    "residual_effective_record_count",
    "b_partition_record_count",
    "c_partition_record_count",
    "source_partition_failure_count",
    "translation_check_count",
    "restricted_translation_check_count",
    "effective_translation_check_count",
    "translation_failure_count",
    "inverse_translation_failure_count",
    "total_degree_check_count",
    "total_degree_failure_count",
    "block_phase_check_count",
    "block_phase_failure_count",
    "unary_sum_check_count",
    "unary_sum_term_count",
    "unary_sum_failure_count",
    "phase_kernel_tuple_count",
    "phase_tuple_classification_count",
    "key_phase_check_count",
    "residual_phase_check_count",
    "phase_kernel_failure_count",
    "decompose_table_check_count",
    "decompose_table_failure_count",
    "c_scale_bijection_fibre_count",
    "c_scale_bijection_check_count",
    "c_scale_bijection_failure_count",
    "b_scale_bijection_fibre_count",
    "b_scale_bijection_check_count",
    "b_scale_bijection_failure_count",
    "b_state_count",
    "c_state_count",
    "state_id_check_count",
    "state_id_failure_count",
    "state_id_coverage_failure_count",
    "key_byte_roundtrip_check_count",
    "key_byte_roundtrip_failure_count",
    "record_width_bytes",
    "b_record_stream_bytes",
    "c_record_stream_bytes",
    "join_sentinel_b_record_count",
    "join_sentinel_c_record_count",
    "join_sentinel_pair_count",
    "join_sentinel_collision_filter_count",
    "join_sentinel_failure_count",
    "join_hash_slot_count",
    "join_materialized_array_bytes",
    "join_charged_array_cap_bytes",
    "join_two_engine_array_cap_bytes",
    "join_table_bookkeeping_charge",
    "representative_count",
    "join_predicate_equivariance_certified",
    "representative_outer_domain_authorized",
    "join_execution_completed",
    "contraction_authorized",
    "common_zero_established",
    "source_charge_total",
    "phase_charge_total",
    "scale_bijection_charge_total",
    "schema_charge_total",
    "join_sentinel_charge_total",
    "hash_charge_total",
    "engine_preflight_charge",
    "engine_preflight_cap",
    "rlimit_as_soft",
    "rlimit_as_hard",
    "vm_peak_kib",
}
_HASH_FIELDS = {
    "orbit_artifact_sha256",
    "orbit_source_record_sha256",
    "orbit_zero_shape_sha256",
    "orbit_shape_action_sha256",
    "orbit_receipt_sha256",
    "slice_artifact_sha256",
    "slice_shape_sha256",
    "slice_action_sha256",
    "slice_representative_sha256",
    "slice_receipt_sha256",
    "native_design_sha256",
    "codex_audit_sha256",
    "input_manifest_sha256",
    "source_partition_sha256",
    "source_record_schema_sha256",
    "translation_closure_sha256",
    "row_count_table_sha256",
    "predicate_schema_sha256",
    "phase_kernel_sha256",
    "decompose_table_sha256",
    "scale_bijection_sha256",
    "c_scale_bijection_sha256",
    "b_scale_bijection_sha256",
    "state_schema_sha256",
    "join_sentinel_sha256",
    "charge_transcript_sha256",
}
_STRING_FIELDS = {"status", "join_predicate_equivariance", "row_count_table"}


def _parse_transcript(stdout: str) -> dict[str, Any]:
    lines = stdout.strip().splitlines()
    frame = f"REASONING_AD5_COORDINATE3_JOIN_COVARIANCE_V{_PROTOCOL_VERSION}"
    if len(lines) < 3 or lines[0] != frame or lines[-1] != "REASONING_END":
        raise ScientificToolError("AD5 join-covariance transcript has invalid framing")
    fields: dict[str, str] = {}
    for line in lines[1:-1]:
        if "=" not in line:
            raise ScientificToolError("AD5 join-covariance transcript has an invalid field")
        name, value = line.split("=", 1)
        if name in fields:
            raise ScientificToolError("AD5 join-covariance transcript has duplicate fields")
        fields[name] = value
    if set(fields) != _INTEGER_FIELDS | _HASH_FIELDS | _STRING_FIELDS:
        raise ScientificToolError("AD5 join-covariance transcript is incomplete")
    try:
        parsed: dict[str, Any] = {name: int(fields[name]) for name in _INTEGER_FIELDS}
    except ValueError as error:
        raise ScientificToolError("AD5 join-covariance transcript has a nonintegral field") from error
    for name in _HASH_FIELDS:
        value = fields[name]
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ScientificToolError("AD5 join-covariance transcript has an invalid digest")
        parsed[name] = value
    for name in _STRING_FIELDS:
        parsed[name] = fields[name]
    return parsed


def _run_program() -> dict[str, Any]:
    computation = _run_source_hashed_program(
        _PROGRAM,
        label="ad5-coordinate3-join-covariance",
        timeout_seconds=180,
        runtime_address_space_bytes=_RESIDENT_BYTE_CAP,
        preserve_exact_transcript=True,
    )
    return {
        **computation,
        **_parse_transcript(computation["transcript"]),
        "mathematical_transcript_sha256": bounded_transcript_sha256(computation["transcript"], parser=_parse_transcript),
    }


def _run_replay() -> dict[str, Any]:
    replay_path = Path(__file__).with_name("heisenberg_level25_four_block_ad5_join_replay.py")
    try:
        program = replay_path.read_bytes()
    except OSError as error:
        raise ScientificToolError(f"independent AD5 join-covariance replay is unavailable: {error}") from error
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
            timeout=180,
            env=environment,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise ScientificToolError("independent AD5 join-covariance replay exceeded 180 seconds") from error
    except OSError as error:
        raise ScientificToolError(f"independent AD5 join-covariance replay could not start: {error}") from error
    if completed.returncode or completed.stderr.strip():
        detail = (completed.stderr.strip() or completed.stdout.strip() or "unknown replay failure")[:2000]
        raise ScientificToolError(f"independent AD5 join-covariance replay failed: {detail}")
    try:
        replay = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise ScientificToolError("independent AD5 join-covariance replay returned invalid JSON") from error
    if not isinstance(replay, dict):
        raise ScientificToolError("independent AD5 join-covariance replay returned a non-object")
    return {
        **replay,
        "program_sha256": hashlib.sha256(program).hexdigest(),
        "transcript_sha256": hashlib.sha256(completed.stdout.encode()).hexdigest(),
        "transcript": completed.stdout,
        "runtime_address_space_limit_bytes": _RESIDENT_BYTE_CAP,
        "interpreter": str(Path(replay_python).resolve()),
    }


def level25_heisenberg_augmented_four_block_ad5_coordinate3_join_partition_covariance_profile() -> dict[str, Any]:
    """Certify the exact five-key partition and triangular action covariance."""

    orbit = level25_heisenberg_augmented_four_block_ad5_coordinate3_tensor_orbit_profile()
    slice_profile = level25_heisenberg_augmented_four_block_ad5_coordinate3_central_orbit_representative_preflight_profile()
    computation = _run_program()
    replay = _run_replay()
    compared = (
        "status",
        "join_predicate_equivariance",
        *(_INTEGER_FIELDS - {"rlimit_as_soft", "rlimit_as_hard", "vm_peak_kib"}),
        *_HASH_FIELDS,
    )
    replay_agreement = all(replay.get(name) == computation[name] for name in compared)
    actual_combined_charge = computation["engine_preflight_charge"] + replay["engine_preflight_charge"]
    expected_status = "T3_FIVE_KEY_PARTITION_AND_TRIANGULAR_COVARIANCE_CERTIFIED"
    source_manifest = _source_manifest()
    certificate = {
        "declared_review_references_match_executable_input": computation["orbit_artifact_sha256"] == _ORBIT_ARTIFACT_SHA256
        and computation["orbit_source_record_sha256"] == _ORBIT_SOURCE_RECORD_SHA256
        and computation["orbit_zero_shape_sha256"] == _ORBIT_ZERO_SHAPE_SHA256
        and computation["orbit_shape_action_sha256"] == _ORBIT_SHAPE_ACTION_SHA256
        and computation["orbit_receipt_sha256"] == _ORBIT_RECEIPT_SHA256
        and computation["slice_artifact_sha256"] == _SLICE_ARTIFACT_SHA256
        and computation["slice_shape_sha256"] == _SLICE_SHAPE_SHA256
        and computation["slice_action_sha256"] == _SLICE_ACTION_SHA256
        and computation["slice_representative_sha256"] == _SLICE_REPRESENTATIVE_SHA256
        and computation["slice_receipt_sha256"] == _SLICE_RECEIPT_SHA256
        and computation["native_design_sha256"] == _NATIVE_DESIGN_SHA256
        and computation["codex_audit_sha256"] == _CODEX_AUDIT_SHA256,
        "orbit_is_the_fresh_reviewed_prerequisite": reviewed_profile_matches(orbit, _ORBIT_EVIDENCE_SHA256),
        "slice_is_the_fresh_reviewed_prerequisite": reviewed_profile_matches(slice_profile, _SLICE_EVIDENCE_SHA256),
        "prior_mathematical_streams_match_fresh_certified_evidence": computation["orbit_source_record_sha256"] == orbit["computation"]["source_record_stream_sha256"]
        and computation["orbit_zero_shape_sha256"] == orbit["computation"]["zero_shape_stream_sha256"]
        and computation["orbit_shape_action_sha256"] == orbit["computation"]["shape_action_table_sha256"]
        and computation["slice_shape_sha256"] == slice_profile["computation"]["shape_stream_sha256"]
        and computation["slice_action_sha256"] == slice_profile["computation"]["action_table_sha256"]
        and computation["slice_representative_sha256"] == slice_profile["computation"]["representative_stream_sha256"]
        and computation["representative_count"] == orbit["computation"]["central_orbit_count"] == slice_profile["computation"]["representative_count"],
        "executable_source_is_partitioned_record_by_record": source_manifest["raw_record_count"] == computation["source_record_count"] == 725
        and source_manifest["four_block_restricted_record_count"] == computation["restricted_source_record_count"] == 535
        and computation["covered_source_record_count"] == 235
        and computation["effective_partition_record_count"] == 195
        and computation["b_partition_record_count"] == 45
        and computation["c_partition_record_count"] == 105
        and computation["row_count_table"]
        == "0:50,35,0,0,35,0,15;1:25,20,0,0,0,0,25;2:25,20,0,0,0,0,25;3:25,20,0,0,0,0,25;4:25,20,0,0,0,0,25;5:100,65,20,45,0,0,35;6:25,20,5,15,0,0,5;7:50,35,10,25,0,0,15;8:25,20,5,15,0,0,5;9:50,30,0,0,10,20,20;10:25,20,0,0,0,0,25;11:50,30,5,5,0,20,20;12:25,20,0,0,0,0,25;13:25,20,0,0,0,0,25;14:25,20,0,0,0,0,25;15:25,20,0,0,0,0,25;16:25,20,0,0,0,0,25;17:25,20,0,0,0,0,25;18:25,20,0,0,0,0,25;19:25,20,0,0,0,0,25;20:25,20,0,0,0,0,25;21:25,20,0,0,0,0,25"
        and computation["source_partition_failure_count"] == 0,
        "all_literal_records_are_closed_under_translation_and_inverse": computation["translation_check_count"] == 3_625
        and computation["restricted_translation_check_count"] == 2_675
        and computation["effective_translation_check_count"] == 975
        and computation["translation_failure_count"] == 0
        and computation["inverse_translation_failure_count"] == 0
        and computation["total_degree_check_count"] == 725
        and computation["total_degree_failure_count"] == 0,
        "phase_kernel_preserves_key_and_residual_zero_predicates": computation["block_phase_check_count"] == 700
        and computation["block_phase_failure_count"] == 0
        and computation["unary_sum_check_count"] == 8
        and computation["unary_sum_term_count"] == 40
        and computation["unary_sum_failure_count"] == 0
        and computation["phase_tuple_classification_count"] == 625
        and computation["phase_kernel_tuple_count"] == 125
        and computation["phase_kernel_failure_count"] == 0,
        "evaluator_normalization_and_scale_action_are_exactly_bijective": computation["decompose_table_check_count"] == 30
        and computation["decompose_table_failure_count"] == 0
        and computation["c_scale_bijection_check_count"] == 125
        and computation["c_scale_bijection_failure_count"] == 0
        and computation["b_scale_bijection_check_count"] == 15_625
        and computation["b_scale_bijection_failure_count"] == 0,
        "record_schema_is_lossless_and_preserves_duplicate_multiplicity": computation["record_width_bytes"] == 9
        and computation["state_id_check_count"] == 132_600
        and computation["state_id_failure_count"] == 0
        and computation["state_id_coverage_failure_count"] == 0
        and computation["key_byte_roundtrip_check_count"] == 505
        and computation["key_byte_roundtrip_failure_count"] == 0
        and computation["join_sentinel_pair_count"] == 8
        and computation["join_sentinel_collision_filter_count"] >= 1
        and computation["join_sentinel_failure_count"] == 0
        and computation["join_hash_slot_count"] == 262_144
        and computation["join_materialized_array_bytes"] == 3_051_096
        and computation["join_charged_array_cap_bytes"] == 4_099_672
        and computation["join_two_engine_array_cap_bytes"] == 8_199_344
        and computation["join_table_bookkeeping_charge"] == 2_316_680,
        "primary_and_independent_replay_agree": replay_agreement,
        "combined_execution_respects_hard_resource_caps": actual_combined_charge < _COMBINED_CHARGE_CAP
        and computation["engine_preflight_charge"] < _ENGINE_PREFLIGHT_CAP
        and replay["engine_preflight_charge"] < _ENGINE_PREFLIGHT_CAP
        and computation["rlimit_as_soft"] == computation["rlimit_as_hard"] == replay["runtime_address_space_limit_bytes"] == _RESIDENT_BYTE_CAP,
        "resident_telemetry_respects_the_address_space_cap": 0 < computation["vm_peak_kib"] * 1024 <= _RESIDENT_BYTE_CAP,
        "programs_and_transcripts_match_reviewed_executions": bounded_execution_matches(
            computation, parser=_parse_transcript, expected_program=_REVIEWED_PROGRAM_SHA256,
            expected_mathematics=_REVIEWED_MATHEMATICAL_TRANSCRIPT_SHA256,
        ) and independent_replay_matches(
            replay, expected_program=_REVIEWED_REPLAY_PROGRAM_SHA256, expected_transcript=_REVIEWED_REPLAY_TRANSCRIPT_SHA256,
        ),
        "terminal_status_authorizes_only_the_predicate_and_outer_domain": computation["status"] == expected_status
        and computation["join_predicate_equivariance"] == "CERTIFIED"
        and computation["join_predicate_equivariance_certified"] == 1
        and computation["representative_outer_domain_authorized"] == 1
        and computation["join_execution_completed"] == 0
        and computation["contraction_authorized"] == 0
        and computation["common_zero_established"] == 0,
    }
    verified = all(certificate.values())
    return seal_profile_evidence({
        "schema": "reasoning.level25-heisenberg-ad5-coordinate3-join-partition-covariance.v2",
        "request": {},
        "request_sha256": hashlib.sha256(canonical_json({}).encode()).hexdigest(),
        "source_manifest": source_manifest,
        "orbit_receipt_sha256": hashlib.sha256(canonical_json(orbit).encode()).hexdigest(),
        "orbit_evidence_sha256": orbit["evidence_sha256"],
        "slice_receipt_sha256": hashlib.sha256(canonical_json(slice_profile).encode()).hexdigest(),
        "slice_evidence_sha256": slice_profile["evidence_sha256"],
        "charge_scope": "named primary and independent replay only; prerequisite executions are separate",
        "review_references": {
            "kind": "declared_source_literals",
            "external_contents_verified": False,
            "native_design": {"path": _NATIVE_DESIGN_ARTIFACT_PATH, "sha256": _NATIVE_DESIGN_SHA256},
            "codex_audit": {"path": _CODEX_AUDIT_ARTIFACT_PATH, "sha256": _CODEX_AUDIT_SHA256},
        },
        "computation": computation,
        "independent_replay": replay,
        "actual_combined_charge": actual_combined_charge,
        "decision": {
            "status": computation["status"],
            "five_key_join_predicate_equivariant": verified and computation["status"] == expected_status,
            "direct_residual_predicate_equivariant": verified and computation["status"] == expected_status,
            "representative_count": computation["representative_count"],
            "representative_outer_domain_authorized": verified and bool(computation["representative_outer_domain_authorized"]),
            "join_execution_completed": bool(computation["join_execution_completed"]),
            "full_contraction_authorized": bool(computation["contraction_authorized"]),
            "common_zero_found": bool(computation["common_zero_established"]),
        },
        "certificate": certificate,
        "certificate_verified": verified,
        "interpretation": (
            "The literal source partition, phase-kernel calculation, and triangular scale bijections prove that the exact five-key equality and direct rows-0/9 zero tests are invariant under the already certified central action. The 3,907,280 canonical representatives may therefore index a future join, but this diagnostic does not execute or price that join."
        ),
        "not_certified": [
            "the Native design's 663,000-record all-context census or its state-id formula",
            "materialization of B or C key streams for any central context",
            "execution, completeness, or resource price of the 3,907,280-context join",
            "a Fourier transform, convolution, or full tensor contraction",
            "a coordinate-three solution count or seven-equation common zero",
            "geometric points over extensions of F_101 or a characteristic-zero lift",
            "global smoothness, a Brauer class, period, index, or period-index counterexample",
        ],
        "next_gate": {
            "feature": "level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_batch_profile",
            "requirement": (
                "Implement a bounded batch over canonical central representatives that materializes the exact 9-byte B/C streams, performs the duplicate-complete five-key join, evaluates rows 0 and 9 on every match, and emits resumable aggregate receipts before any full contraction claim."
            ),
        },
    })
