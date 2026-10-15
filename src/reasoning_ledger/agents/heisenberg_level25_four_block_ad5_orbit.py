# ruff: noqa: E501
"""Exact AD5 coordinate-three Heisenberg orbit diagnostic."""

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
from .heisenberg_level25_four_block_ad5_central_key import (
    _CENTRAL_KEY_EVIDENCE_SHA256,
    level25_heisenberg_augmented_four_block_ad5_coordinate3_central_key_profile,
)
from .heisenberg_level25_four_block_ad5_signature import _PROGRAM as _SIGNATURE_PROGRAM
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
_ENGINE_PREFLIGHT_CAP = 60_000_000
_NATIVE_INPUT_SHA256 = "f7fff26b9493e82ccb073b2ecfa1236bc83296a3ee44dacbeacf2e4d3b4fd969"
_CENTRAL_KEY_ARTIFACT_PATH = "research/four-block/iteration-125/ad5-coordinate3-central-key-native.md"
_CENTRAL_KEY_ARTIFACT_SHA256 = "65658d6de29e771312c63d2f2a89a7dd53df97d96b2e521f0fb0fb8b419ce22d"
_CENTRAL_KEY_RECEIPT_SHA256 = "70430e5a35233c5bb6ec2bade5a1511e7e8891045bfc40f2b7956bca008c7e63"
_REVIEWED_PROGRAM_SHA256 = "81d0acaf96f44f4a56b03c22d99421d58d5bdcacad8c97f29f8a07aedad5ee48"
_REVIEWED_MATHEMATICAL_TRANSCRIPT_SHA256 = "cf2eda13073eef480a70bfdd25033634fc19c4c60731df244de72825df8139f0"
_ORBIT_EVIDENCE_SHA256 = "8c526e2208aaa8007b63741cbd9f5c4d9f05c3d412033050bfd921df2f84f32e"
_REVIEWED_REPLAY_PROGRAM_SHA256 = "14edb9a8bbea038a58be8aa3f5550e82eb1c9ad3119310bd48dec454f9e275f5"
_REVIEWED_REPLAY_TRANSCRIPT_SHA256 = "e9e1512304bb89a6956c0d5147d31ec2a84458bcb98662eefb76cc6e4f2b3408"

_SUPPORT, _MAIN_SEPARATOR, _OLD_MAIN = _SIGNATURE_PROGRAM.partition("\nint main(){")
if not _MAIN_SEPARATOR:
    raise RuntimeError("AD5 signature support program no longer has the expected main")

_PROGRAM = (
    _SUPPORT
    + r"""
#include <map>
#include <set>
#include <tuple>

constexpr int ORBIT_GROUP_ORDER=25,ORBIT_ENGINE_CAP=60000000;
constexpr u64 CENTRAL_STATE_COUNT=97682000ULL,CENTRAL_ORBIT_COUNT=3907280ULL;

struct OrbitHash{
 Sha256 hash;u64 bytes=0;
 explicit OrbitHash(const std::string&tag){u64be(tag.size());raw(tag);}
 void byte(int value){hash.byte(std::uint8_t(value));++bytes;}
 void raw(const std::string&value){for(unsigned char item:value)byte(item);}
 void raw_hex(const std::string&value){for(std::size_t offset=0;offset<value.size();offset+=2)byte(std::stoi(value.substr(offset,2),nullptr,16));}
 void u32be(std::uint32_t value){for(int shift=24;shift>=0;shift-=8)byte((value>>shift)&255);}
 void u64be(u64 value){for(int shift=56;shift>=0;shift-=8)byte((value>>shift)&255);}
 std::pair<std::string,u64>finish(){return {hash.finish(),((bytes+63)/64)*1000};}
};

u64 fnv_mix_be(u64 checksum,u64 value){for(int shift=56;shift>=0;shift-=8){checksum^=(value>>shift)&255;checksum*=1099511628211ULL;}return checksum;}

using Exp=std::array<std::uint8_t,5>;
Exp decode_exp(std::uint32_t code){Exp out{};for(int q=4;q>=1;--q){out[q]=code%25;code/=25;}return out;}
std::uint32_t encode_exp(const Exp&value){std::uint32_t code=0;for(int q=1;q<5;++q)code=25*code+value[q];return code;}
Exp transform_exp(const Exp&value,int a,int b){Exp out{};for(int q=0;q<5;++q){int item=int(value[(q+a)%5])-value[a]+5*b*q;item%=25;if(item<0)item+=25;out[q]=item;}return out;}

struct Record{
 std::uint8_t row=0,character=0,start=0,arity=0;
 std::array<std::uint8_t,3>indices{},exponents{};
 bool operator<(const Record&other)const{return std::tie(row,character,start,arity,indices,exponents)<std::tie(other.row,other.character,other.start,other.arity,other.indices,other.exponents);}
 bool operator==(const Record&other)const{return row==other.row&&character==other.character&&start==other.start&&arity==other.arity&&indices==other.indices&&exponents==other.exponents;}
};

std::vector<Record>source_records(){
 std::vector<Record>out;out.reserve(725);
 for(int row=0;row<22;++row)for(int i=0;i<25;++i){Record record;record.row=row;record.character=CH[row].first;record.start=i;record.arity=2;record.indices={{std::uint8_t(i),std::uint8_t((i-CH[row].second+25)%25),0}};record.exponents={{24,1,0}};out.push_back(record);}
 for(int i=0;i<25;++i){Record record;record.row=0;record.character=5;record.start=i;record.arity=2;record.indices={{std::uint8_t(i),std::uint8_t((i+1)%25),0}};record.exponents={{20,5,0}};out.push_back(record);}
 const std::array<std::array<int,4>,4>pivots{{{{2,1,4,20}},{{2,2,13,10}},{{2,4,6,15}},{{4,1,4,20}}}};
 for(auto pivot:pivots){int row=0;while(row<22&&CH[row].first!=pivot[0])++row;for(int i=0;i<25;++i){Record record;record.row=row;record.character=pivot[0];record.start=i;record.arity=3;record.indices={{std::uint8_t(i),std::uint8_t((i+5)%25),std::uint8_t((i+2)%25)}};record.exponents={{std::uint8_t(pivot[1]),std::uint8_t(pivot[2]),std::uint8_t(pivot[3])}};out.push_back(record);}}
 const std::array<std::array<int,4>,2>selected{{{{9,2,19,18}},{{7,1,2,23}}}};
 for(auto item:selected){int row=0;while(row<22&&CH[row].first!=item[0])++row;for(int i=0;i<25;++i){Record record;record.row=row;record.character=item[0];record.start=i;record.arity=3;record.indices={{std::uint8_t(i),std::uint8_t((i+item[1])%25),std::uint8_t((i+item[2])%25)}};record.exponents={{1,std::uint8_t(item[3]),std::uint8_t(24-item[3])}};out.push_back(record);}}
 return out;
}

std::pair<int,int>decompose_scale(int raw){raw%=25;if(raw<0)raw+=25;int residue=raw%5,delta=(raw-21*residue)%25;if(delta<0)delta+=25;return {residue,delta/5};}
std::array<int,4>canonical_scales(int rb,int rc,int rd){auto [jb,lb]=decompose_scale(rb);auto [jc,lc]=decompose_scale(rc);auto [jd,ld]=decompose_scale(rd);return {{jb,(2*lb+3*lc+4*ld)%5,jc,jd}};}
std::size_t action_offset(std::size_t shape,int a,int b){return shape*25+a*5+b;}

struct State{
 std::array<std::uint32_t,4>shape{};std::array<std::uint8_t,4>scale{};
 bool operator==(const State&other)const{return shape==other.shape&&scale==other.scale;}
};

State act_state(const State&state,int a,int b,const std::vector<Exp>&shapes,const std::vector<std::uint32_t>&action){
 State out;for(int role=0;role<4;++role)out.shape[role]=action[action_offset(state.shape[role],a,b)];int ea=shapes[state.shape[0]][a];std::array<int,4>scale{{0,(21*state.scale[0]+15*state.scale[1])%25,(21*state.scale[2])%25,(21*state.scale[3])%25}};std::array<int,3>raw{};for(int role=1;role<4;++role){raw[role-1]=(scale[role]+b*role+int(shapes[state.shape[role]][a])-ea)%25;if(raw[role-1]<0)raw[role-1]+=25;}auto canonical=canonical_scales(raw[0],raw[1],raw[2]);for(int i=0;i<4;++i)out.scale[i]=canonical[i];return out;
}

int main(){
 struct rlimit limit{};getrlimit(RLIMIT_AS,&limit);if(limit.rlim_cur!=67108864ULL||limit.rlim_max!=67108864ULL)return 70;
 for(int value=0;value<P;++value){POW[value][0]=1;for(int exponent=1;exponent<=25;++exponent)POW[value][exponent]=POW[value][exponent-1]*value%P;}for(int character=0;character<N;++character)for(int index=0;index<N;++index)COEFF[character][index]=coeff(character,index);
 const std::string native_input="f7fff26b9493e82ccb073b2ecfa1236bc83296a3ee44dacbeacf2e4d3b4fd969",central_artifact="65658d6de29e771312c63d2f2a89a7dd53df97d96b2e521f0fb0fb8b419ce22d",central_receipt="70430e5a35233c5bb6ec2bade5a1511e7e8891045bfc40f2b7956bca008c7e63";
 u64 hash_charge=0,source_charge=0,action_charge=0,group_law_charge=0,recanonicalization_charge=0,orbit_charge=0,central_charge=0;
 auto records=source_records();for(const auto&record:records)source_charge+=4+2*record.arity;
 OrbitHash record_hash("ad5-orbit/source-records/v1");record_hash.u64be(records.size());for(const auto&record:records){record_hash.byte(record.row);record_hash.byte(record.character);record_hash.byte(record.start);record_hash.byte(record.arity);for(int i=0;i<3;++i)if(i<record.arity){record_hash.byte(record.indices[i]);record_hash.byte(record.exponents[i]);}}auto [record_sha,charged_record]=record_hash.finish();hash_charge+=charged_record;
 std::set<Record>record_set(records.begin(),records.end());std::array<u64,22>row_counts{};std::array<std::set<int>,22>row_weights;u64 translation_failures=0,residue_piece_failures=0;
 for(const auto&record:records){++row_counts[record.row];int weight=0;for(int i=0;i<3;++i)if(i<record.arity)weight=(weight+record.indices[i]*record.exponents[i])%25;row_weights[record.row].insert(weight);for(int a=0;a<5;++a){Record translated=record;translated.start=(record.start+5*a)%25;for(int i=0;i<3;++i)if(i<record.arity)translated.indices[i]=(record.indices[i]+5*a)%25;if(!record_set.count(translated))++translation_failures;source_charge+=1+record.arity;}if(record.row==5||record.row==6||record.row==8){std::array<int,5>degrees{};for(int i=0;i<3;++i)if(i<record.arity)degrees[record.indices[i]%5]+=record.exponents[i];for(int value:degrees)if(value%5){++residue_piece_failures;break;}source_charge+=record.arity;}}
 const std::array<int,22>expected_counts{{50,25,25,25,25,100,25,50,25,50,25,50,25,25,25,25,25,25,25,25,25,25}},expected_weights{{5,5,5,5,0,10,5,10,20,0,0,0,0,0,0,0,0,0,0,0,0,0}};u64 covariance_failures=translation_failures;for(int row=0;row<22;++row){covariance_failures+=row_counts[row]!=u64(expected_counts[row]);covariance_failures+=row_weights[row]!=std::set<int>{expected_weights[row]};for(const auto&record:records)if(record.row==row&&record.character!=CH[row].first)++covariance_failures;}
 OrbitHash covariance_hash("ad5-orbit/source-covariance/v1");covariance_hash.u64be(22);for(int row=0;row<22;++row){covariance_hash.byte(row);covariance_hash.byte(CH[row].first);covariance_hash.u64be(row_counts[row]);covariance_hash.byte(row_weights[row].empty()?255:*row_weights[row].begin());}covariance_hash.u64be(translation_failures);covariance_hash.u64be(residue_piece_failures);auto [covariance_sha,charged_covariance]=covariance_hash.finish();hash_charge+=charged_covariance;
 OrbitHash manifest_hash("ad5-orbit/source-manifest/v1");manifest_hash.raw_hex(native_input);manifest_hash.raw_hex(central_artifact);manifest_hash.raw_hex(central_receipt);manifest_hash.raw_hex(record_sha);manifest_hash.u64be(records.size());auto [manifest_sha,charged_manifest]=manifest_hash.finish();hash_charge+=charged_manifest;
 Charges search;std::vector<Exp>shapes;std::vector<std::uint32_t>codes;std::vector<std::int32_t>index_by_code(390625,-1);for(std::uint32_t code=0;code<390625;++code){auto value=decode_shape(code);if(!cyclic_bucket(value,search)){index_by_code[code]=shapes.size();shapes.push_back(decode_exp(code));codes.push_back(code);}}
 OrbitHash zero_hash("ad5-orbit/zero-shapes/v1");zero_hash.u64be(shapes.size());for(std::size_t i=0;i<shapes.size();++i){zero_hash.u32be(codes[i]);for(auto value:shapes[i])zero_hash.byte(value);}auto [zero_sha,charged_zero]=zero_hash.finish();hash_charge+=charged_zero;
 std::vector<std::uint32_t>action(shapes.size()*25);u64 closure_failures=0;OrbitHash action_hash("ad5-orbit/shape-action/v1");action_hash.u64be(action.size());for(std::size_t shape=0;shape<shapes.size();++shape)for(int a=0;a<5;++a)for(int b=0;b<5;++b){auto target_code=encode_exp(transform_exp(shapes[shape],a,b));int target=index_by_code[target_code];if(target<0){++closure_failures;target=0;}action[action_offset(shape,a,b)]=target;action_hash.u32be(shape);action_hash.byte(a);action_hash.byte(b);action_hash.u32be(target);action_charge+=26;}auto [action_sha,charged_action]=action_hash.finish();hash_charge+=charged_action;
 u64 group_checks=0,group_failures=0,group_checksum=14695981039346656037ULL;for(std::size_t shape=0;shape<shapes.size();++shape)for(int a=0;a<5;++a)for(int b=0;b<5;++b){auto first=action[action_offset(shape,a,b)];for(int c=0;c<5;++c)for(int d=0;d<5;++d){auto composed=action[action_offset(first,c,d)],direct=action[action_offset(shape,(a+c)%5,(b+d)%5)];group_failures+=composed!=direct;++group_checks;group_checksum=fnv_mix_be(group_checksum,direct);group_law_charge+=4;}}
 OrbitHash law_hash("ad5-orbit/shape-group-law/v1");law_hash.u64be(group_checks);law_hash.u64be(group_failures);law_hash.u64be(group_checksum);auto [law_sha,charged_law]=law_hash.finish();hash_charge+=charged_law;
 std::vector<std::pair<std::uint32_t,int>>assignments;std::vector<std::uint32_t>representatives;std::map<int,u64>orbit_sizes;assignments.reserve(shapes.size());for(std::size_t shape=0;shape<shapes.size();++shape){std::set<std::uint32_t>members;for(int a=0;a<5;++a)for(int b=0;b<5;++b)members.insert(action[action_offset(shape,a,b)]);auto representative=*members.begin();assignments.push_back({representative,members.size()});if(representative==shape){representatives.push_back(shape);++orbit_sizes[members.size()];}orbit_charge+=25;}
 OrbitHash assignment_hash("ad5-orbit/shape-orbit-assignment/v1");assignment_hash.u64be(assignments.size());for(std::size_t shape=0;shape<assignments.size();++shape){assignment_hash.u32be(shape);assignment_hash.u32be(assignments[shape].first);assignment_hash.byte(assignments[shape].second);}auto [assignment_sha,charged_assignment]=assignment_hash.finish();hash_charge+=charged_assignment;
 OrbitHash histogram_hash("ad5-orbit/shape-orbit-histogram/v1");histogram_hash.u64be(orbit_sizes.size());for(auto [size,count]:orbit_sizes){histogram_hash.u64be(size);histogram_hash.u64be(count);}auto [histogram_sha,charged_histogram]=histogram_hash.finish();hash_charge+=charged_histogram;
 OrbitHash phase_hash("ad5-orbit/phase-recanonicalization/v1");phase_hash.u64be(15625);u64 phase_failures=0;for(int rb=0;rb<25;++rb)for(int rc=0;rc<25;++rc)for(int rd=0;rd<25;++rd){auto canonical=canonical_scales(rb,rc,rd);std::array<int,3>bases{{(21*canonical[0]+15*canonical[1])%25,(21*canonical[2])%25,(21*canonical[3])%25}},raw{{rb,rc,rd}},phases{};for(int i=0;i<3;++i){int delta=(raw[i]-bases[i])%25;if(delta<0)delta+=25;phases[i]=delta/5;}phase_failures+=(2*phases[0]+3*phases[1]+4*phases[2])%5!=0;for(int value:std::array<int,10>{{rb,rc,rd,canonical[0],canonical[1],canonical[2],canonical[3],bases[0],bases[1],bases[2]}})phase_hash.byte(value);recanonicalization_charge+=20;}
 std::vector<State>corpus;std::uint32_t first=0,last=shapes.size()-1;for(std::size_t position=0;position<representatives.size();++position){State state;state.shape={{first,first,last,last}};state.shape[position%4]=representatives[position];int work=position%625;for(int i=0;i<4;++i){state.scale[i]=work%5;work/=5;}corpus.push_back(state);}std::vector<std::uint32_t>size_five;for(auto representative:representatives)if(assignments[representative].second==5)size_five.push_back(representative);for(auto representative:size_five)for(int role=0;role<4;++role){State state;state.shape={{first,first,last,last}};state.shape[role]=representative;corpus.push_back(state);}for(int role=0;role<4;++role)for(int value=0;value<25;++value){std::uint32_t chosen=0;for(;chosen<shapes.size();++chosen)if(std::find(shapes[chosen].begin(),shapes[chosen].end(),value)!=shapes[chosen].end())break;State state;state.shape={{first,first,last,last}};state.shape[role]=chosen;state.scale={{std::uint8_t(value%5),std::uint8_t((value/5)%5),std::uint8_t(role),std::uint8_t(value%5)}};corpus.push_back(state);}for(int packed=0;packed<625;++packed){State state;state.shape={{representatives[packed%representatives.size()],std::uint32_t((packed*17+1)%shapes.size()),std::uint32_t((packed*37+2)%shapes.size()),std::uint32_t((packed*73+3)%shapes.size())}};int work=packed;for(int i=0;i<4;++i){state.scale[i]=work%5;work/=5;}corpus.push_back(state);}
 u64 quotient_checks=0,quotient_failures=0,quotient_checksum=14695981039346656037ULL;for(const auto&state:corpus)for(int a=0;a<5;++a)for(int b=0;b<5;++b){auto first_state=act_state(state,a,b,shapes,action);for(int c=0;c<5;++c)for(int d=0;d<5;++d){auto composed=act_state(first_state,c,d,shapes,action),direct=act_state(state,(a+c)%5,(b+d)%5,shapes,action);quotient_failures+=!(composed==direct);++quotient_checks;for(auto value:direct.shape)quotient_checksum=fnv_mix_be(quotient_checksum,value);for(auto value:direct.scale)quotient_checksum=fnv_mix_be(quotient_checksum,value);recanonicalization_charge+=4;}}
 phase_hash.u64be(corpus.size());phase_hash.u64be(quotient_checks);phase_hash.u64be(phase_failures+quotient_failures);phase_hash.u64be(quotient_checksum);auto [phase_sha,charged_phase]=phase_hash.finish();hash_charge+=charged_phase;
 std::vector<u64>fixed_shape_counts,central_fixed_counts;OrbitHash fixed_hash("ad5-orbit/central-fixed-counts/v1");fixed_hash.u64be(25);for(int a=0;a<5;++a)for(int b=0;b<5;++b){std::vector<std::uint32_t>fixed;for(std::uint32_t shape=0;shape<shapes.size();++shape)if(action[action_offset(shape,a,b)]==shape)fixed.push_back(shape);fixed_shape_counts.push_back(fixed.size());u64 central_fixed=0;if(a==0&&b==0)central_fixed=CENTRAL_STATE_COUNT;else for(auto ai:fixed)for(auto di:fixed)for(int jd=0;jd<5;++jd){int new_jd=(jd+3*b+int(shapes[di][a])-shapes[ai][a])%5;if(new_jd<0)new_jd+=5;central_fixed+=new_jd==jd;central_charge+=6;}central_fixed_counts.push_back(central_fixed);fixed_hash.byte(a);fixed_hash.byte(b);fixed_hash.u64be(fixed.size());fixed_hash.u64be(central_fixed);}auto [fixed_sha,charged_fixed]=fixed_hash.finish();hash_charge+=charged_fixed;
 u64 burnside_sum=0;for(auto value:central_fixed_counts)burnside_sum+=value;u64 central_orbits=burnside_sum/25;bool central_free=central_fixed_counts.size()==25&&central_fixed_counts[0]==CENTRAL_STATE_COUNT;for(std::size_t i=1;i<central_fixed_counts.size();++i)central_free=central_free&&!central_fixed_counts[i];OrbitHash burnside_hash("ad5-orbit/central-burnside/v1");burnside_hash.u64be(CENTRAL_STATE_COUNT);burnside_hash.u64be(burnside_sum);burnside_hash.u64be(central_orbits);burnside_hash.u64be(central_free);auto [burnside_sha,charged_burnside]=burnside_hash.finish();hash_charge+=charged_burnside;
 u64 shape_charge=search.total();OrbitHash charge_hash("ad5-orbit/charges/v1");for(u64 value:std::array<u64,8>{{source_charge,shape_charge,action_charge,group_law_charge,recanonicalization_charge,orbit_charge,central_charge,hash_charge}})charge_hash.u64be(value);auto [charge_sha,charged_charges]=charge_hash.finish();hash_charge+=charged_charges;u64 engine_charge=source_charge+shape_charge+action_charge+group_law_charge+recanonicalization_charge+orbit_charge+central_charge+hash_charge;
 std::vector<u64>expected_fixed{4420};for(int a=0;a<5;++a)for(int b=0;b<5;++b)if(a||b)expected_fixed.push_back(a&&b?5:0);bool success=records.size()==725&&!covariance_failures&&!residue_piece_failures&&shapes.size()==4420&&!closure_failures&&!group_failures&&!phase_failures&&!quotient_failures&&orbit_sizes==std::map<int,u64>{{5,4},{25,176}}&&fixed_shape_counts==expected_fixed&&central_free&&central_orbits==CENTRAL_ORBIT_COUNT&&engine_charge<ORBIT_ENGINE_CAP;std::string status=success?"T1_C5SQUARED_COVARIANCE_AND_FREE_CENTRAL_ACTION_CERTIFIED":"ORBIT_CERTIFICATE_MISMATCH";
 u64 nonidentity_fixed=0;for(std::size_t i=1;i<central_fixed_counts.size();++i)nonidentity_fixed+=central_fixed_counts[i];
 std::cout<<"REASONING_AD5_COORDINATE3_ORBIT_V1\nstatus="<<status<<"\nfield_prime=101\nshape_count=390625\nzero_shape_count="<<shapes.size()<<"\ngroup_order=25\nsource_row_count=22\nsource_record_count="<<records.size()<<"\nsource_covariance_failure_count="<<covariance_failures<<"\nresidue_piece_failure_count="<<residue_piece_failures<<"\nshape_closure_failure_count="<<closure_failures<<"\nshape_group_law_check_count="<<group_checks<<"\nshape_group_law_failure_count="<<group_failures<<"\nquotient_recanonicalization_state_count="<<corpus.size()<<"\nquotient_recanonicalization_check_count="<<quotient_checks<<"\nquotient_recanonicalization_failure_count="<<phase_failures+quotient_failures<<"\nshape_orbit_count="<<representatives.size()<<"\nshape_orbit_size_5_count="<<orbit_sizes[5]<<"\nshape_orbit_size_25_count="<<orbit_sizes[25]<<"\ncentral_state_count="<<CENTRAL_STATE_COUNT<<"\ncentral_nonidentity_fixed_count="<<nonidentity_fixed<<"\ncentral_action_free="<<central_free<<"\ncentral_orbit_count="<<central_orbits<<"\nauthorized_central_reduction_factor="<<(central_free?25:1)<<"\nsource_charge_total="<<source_charge<<"\nshape_charge_total="<<shape_charge<<"\naction_charge_total="<<action_charge<<"\ngroup_law_charge_total="<<group_law_charge<<"\nrecanonicalization_charge_total="<<recanonicalization_charge<<"\norbit_charge_total="<<orbit_charge<<"\ncentral_charge_total="<<central_charge<<"\nhash_charge_total="<<hash_charge<<"\nengine_preflight_charge="<<engine_charge<<"\nengine_preflight_cap="<<ORBIT_ENGINE_CAP<<"\nnative_input_sha256="<<native_input<<"\ncentral_key_artifact_path=research/four-block/iteration-125/ad5-coordinate3-central-key-native.md\ncentral_key_artifact_sha256="<<central_artifact<<"\ncentral_key_receipt_sha256="<<central_receipt<<"\nsource_manifest_sha256="<<manifest_sha<<"\nsource_record_stream_sha256="<<record_sha<<"\nsource_row_covariance_stream_sha256="<<covariance_sha<<"\nzero_shape_stream_sha256="<<zero_sha<<"\nshape_action_table_sha256="<<action_sha<<"\nshape_group_law_transcript_sha256="<<law_sha<<"\nquotient_recanonicalization_sentinel_sha256="<<phase_sha<<"\nshape_orbit_assignment_sha256="<<assignment_sha<<"\nshape_orbit_histogram_sha256="<<histogram_sha<<"\ncentral_fixed_count_vector_sha256="<<fixed_sha<<"\ncentral_burnside_receipt_sha256="<<burnside_sha<<"\ncharge_transcript_sha256="<<charge_sha<<"\nrlimit_as_soft="<<limit.rlim_cur<<"\nrlimit_as_hard="<<limit.rlim_max<<"\nvm_peak_kib="<<vm_peak_kib()<<"\nREASONING_END\n";
}
"""
)

_INTEGER_FIELDS = {
    "field_prime",
    "shape_count",
    "zero_shape_count",
    "group_order",
    "source_row_count",
    "source_record_count",
    "source_covariance_failure_count",
    "residue_piece_failure_count",
    "shape_closure_failure_count",
    "shape_group_law_check_count",
    "shape_group_law_failure_count",
    "quotient_recanonicalization_state_count",
    "quotient_recanonicalization_check_count",
    "quotient_recanonicalization_failure_count",
    "shape_orbit_count",
    "shape_orbit_size_5_count",
    "shape_orbit_size_25_count",
    "central_state_count",
    "central_nonidentity_fixed_count",
    "central_action_free",
    "central_orbit_count",
    "authorized_central_reduction_factor",
    "source_charge_total",
    "shape_charge_total",
    "action_charge_total",
    "group_law_charge_total",
    "recanonicalization_charge_total",
    "orbit_charge_total",
    "central_charge_total",
    "hash_charge_total",
    "engine_preflight_charge",
    "engine_preflight_cap",
    "rlimit_as_soft",
    "rlimit_as_hard",
    "vm_peak_kib",
}
_HASH_FIELDS = {
    "native_input_sha256",
    "central_key_artifact_sha256",
    "central_key_receipt_sha256",
    "source_manifest_sha256",
    "source_record_stream_sha256",
    "source_row_covariance_stream_sha256",
    "zero_shape_stream_sha256",
    "shape_action_table_sha256",
    "shape_group_law_transcript_sha256",
    "quotient_recanonicalization_sentinel_sha256",
    "shape_orbit_assignment_sha256",
    "shape_orbit_histogram_sha256",
    "central_fixed_count_vector_sha256",
    "central_burnside_receipt_sha256",
    "charge_transcript_sha256",
}
_STRING_FIELDS = {"status", "central_key_artifact_path"}


def _parse_transcript(stdout: str) -> dict[str, Any]:
    lines = stdout.strip().splitlines()
    frame = f"REASONING_AD5_COORDINATE3_ORBIT_V{_PROTOCOL_VERSION}"
    if len(lines) < 3 or lines[0] != frame or lines[-1] != "REASONING_END":
        raise ScientificToolError("AD5 coordinate-three orbit transcript has invalid framing")
    fields: dict[str, str] = {}
    for line in lines[1:-1]:
        if "=" not in line:
            raise ScientificToolError("AD5 orbit transcript has an invalid field")
        name, value = line.split("=", 1)
        if name in fields:
            raise ScientificToolError("AD5 orbit transcript has duplicate fields")
        fields[name] = value
    if set(fields) != _INTEGER_FIELDS | _HASH_FIELDS | _STRING_FIELDS:
        raise ScientificToolError("AD5 orbit transcript is incomplete")
    try:
        parsed: dict[str, Any] = {name: int(fields[name]) for name in _INTEGER_FIELDS}
    except ValueError as error:
        raise ScientificToolError("AD5 orbit transcript has a nonintegral field") from error
    for name in _HASH_FIELDS:
        value = fields[name]
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ScientificToolError("AD5 orbit transcript has an invalid digest")
        parsed[name] = value
    for name in _STRING_FIELDS:
        parsed[name] = fields[name]
    return parsed


def _run_program() -> dict[str, Any]:
    computation = _run_source_hashed_program(
        _PROGRAM,
        label="ad5-coordinate3-orbit",
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
    replay_path = Path(__file__).with_name("heisenberg_level25_four_block_ad5_orbit_replay.py")
    try:
        program = replay_path.read_bytes()
    except OSError as error:
        raise ScientificToolError(f"independent AD5 orbit replay is unavailable: {error}") from error
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
        raise ScientificToolError("independent AD5 orbit replay exceeded 900 seconds") from error
    except OSError as error:
        raise ScientificToolError(f"independent AD5 orbit replay could not start: {error}") from error
    if completed.returncode or completed.stderr.strip():
        detail = (completed.stderr.strip() or completed.stdout.strip() or "unknown replay failure")[:2000]
        raise ScientificToolError(f"independent AD5 orbit replay failed: {detail}")
    try:
        replay = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise ScientificToolError("independent AD5 orbit replay returned invalid JSON") from error
    if not isinstance(replay, dict):
        raise ScientificToolError("independent AD5 orbit replay returned a non-object")
    return {
        **replay,
        "program_sha256": hashlib.sha256(program).hexdigest(),
        "transcript_sha256": hashlib.sha256(completed.stdout.encode()).hexdigest(),
        "transcript": completed.stdout,
        "runtime_address_space_limit_bytes": _RESIDENT_BYTE_CAP,
        "interpreter": str(Path(replay_python).resolve()),
    }


def level25_heisenberg_augmented_four_block_ad5_coordinate3_tensor_orbit_profile() -> dict[str, Any]:
    """Certify the simultaneous C5-squared action on AD5 central contexts."""

    central_key = level25_heisenberg_augmented_four_block_ad5_coordinate3_central_key_profile()
    computation = _run_program()
    replay = _run_replay()
    compared = (
        "status",
        "central_key_artifact_path",
        *(_INTEGER_FIELDS - {"rlimit_as_soft", "rlimit_as_hard", "vm_peak_kib"}),
        *_HASH_FIELDS,
    )
    replay_agreement = all(replay.get(name) == computation[name] for name in compared)
    actual_combined_charge = computation["engine_preflight_charge"] + replay["engine_preflight_charge"]
    expected_status = "T1_C5SQUARED_COVARIANCE_AND_FREE_CENTRAL_ACTION_CERTIFIED"
    source_manifest = _source_manifest()
    certificate = {
        "native_design_input_is_bound": computation["native_input_sha256"] == _NATIVE_INPUT_SHA256,
        "declared_central_key_reference_matches_executable_input": computation["central_key_artifact_path"] == _CENTRAL_KEY_ARTIFACT_PATH
        and computation["central_key_artifact_sha256"] == _CENTRAL_KEY_ARTIFACT_SHA256
        and computation["central_key_receipt_sha256"] == _CENTRAL_KEY_RECEIPT_SHA256,
        "central_key_is_the_fresh_reviewed_prerequisite": reviewed_profile_matches(central_key, _CENTRAL_KEY_EVIDENCE_SHA256),
        "source_manifest_matches_the_725_literal_records": source_manifest["raw_record_count"] == computation["source_record_count"] == 725,
        "source_rows_are_covariant_and_coordinate_three_pieces_survive_recanonicalization": computation["source_row_count"] == 22
        and computation["source_covariance_failure_count"] == 0
        and computation["residue_piece_failure_count"] == 0,
        "zero_shapes_are_regenerated_and_closed_under_the_action": computation["shape_count"] == 25**4
        and computation["zero_shape_count"] == 4_420
        and computation["shape_closure_failure_count"] == 0,
        "shape_group_law_is_exhaustive": computation["shape_group_law_check_count"] == 4_420 * 25 * 25
        and computation["shape_group_law_failure_count"] == 0,
        "quotient_recanonicalization_sentinels_cover_the_action": computation["quotient_recanonicalization_state_count"] == 921
        and computation["quotient_recanonicalization_check_count"] == 921 * 25 * 25
        and computation["quotient_recanonicalization_failure_count"] == 0,
        "shape_orbit_histogram_is_exact": computation["shape_orbit_count"] == 180
        and computation["shape_orbit_size_5_count"] == 4
        and computation["shape_orbit_size_25_count"] == 176,
        "central_action_is_free_with_the_burnside_count": computation["central_state_count"] == 97_682_000
        and computation["central_nonidentity_fixed_count"] == 0
        and computation["central_action_free"] == 1
        and computation["central_orbit_count"] == 3_907_280
        and computation["authorized_central_reduction_factor"] == 25,
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
        "terminal_status_matches_the_exact_census": computation["status"] == expected_status,
    }
    verified = all(certificate.values())
    return seal_profile_evidence({
        "schema": "reasoning.level25-heisenberg-ad5-coordinate3-tensor-orbit-diagnostic.v2",
        "request": {},
        "request_sha256": hashlib.sha256(canonical_json({}).encode()).hexdigest(),
        "source_manifest": source_manifest,
        "central_key_receipt_sha256": hashlib.sha256(canonical_json(central_key).encode()).hexdigest(),
        "central_key_evidence_sha256": central_key["evidence_sha256"],
        "review_reference_policy": {"kind": "declared_source_literals", "external_contents_verified": False},
        "charge_scope": "named primary and independent replay only; prerequisite executions are separate",
        "computation": computation,
        "independent_replay": replay,
        "actual_combined_charge": actual_combined_charge,
        "decision": {
            "status": computation["status"],
            "simultaneous_c5_squared_action_certified": verified and computation["status"] == expected_status,
            "central_action_free": bool(computation["central_action_free"]),
            "central_state_count": computation["central_state_count"],
            "central_orbit_count": computation["central_orbit_count"],
            "authorized_central_reduction_factor": computation["authorized_central_reduction_factor"] if verified else 1,
            "full_contraction_authorized": False,
            "coordinate3_census_completed": False,
            "common_zero_found": False,
        },
        "certificate": certificate,
        "certificate_verified": verified,
        "interpretation": (
            "The source-covariant simultaneous C5-squared action is exact on canonical quotient states. "
            "Its projection to (A,D,j_D) is free, so a future equivariant existence search may use one "
            "representative of each of 3,907,280 central orbits instead of all 97,682,000 central states."
        ),
        "not_certified": [
            "the Native inversion or any reflection or dihedral quotient",
            "an independent action on any one block or a factor 25 per block",
            "a collapse of D signatures, refined central keys, kappa values, or tensor values",
            "construction of central-orbit representatives for a future full search",
            "construction of any joint F_101^3 B or C profile",
            "a Fourier transform, convolution, join, or full contraction",
            "a coordinate-three solution count or census",
            "a seven-equation survivor or common zero",
            "geometric points over extensions of F_101 or a characteristic-zero lift",
            "global smoothness, a Brauer class, period, index, or period-index counterexample",
        ],
        "next_gate": {
            "feature": "level25_heisenberg_augmented_four_block_ad5_coordinate3_central_orbit_representative_preflight",
            "requirement": (
                "Price and independently replay generation of one canonical representative for each free "
                "central orbit, then prove the actual B/C join predicate equivariant before using the factor-25 outer-loop reduction."
            ),
        },
    })
