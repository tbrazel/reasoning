# ruff: noqa: E501
"""Exact coverage search for fixed-stratum-preserving three-block augmentations."""

from __future__ import annotations

import hashlib
from itertools import product
from typing import Any

from ..canonical import canonical_json
from .heisenberg_level25 import (
    FINITE_FIELD_PRIME,
    LEVEL25_HEISENBERG_BALANCED_CHARACTERS,
    MODULUS,
    PRIME,
    _coordinate_restriction,
    _cyclic_eigenpoint,
    _cyclic_eigenpoint_equation_holds,
    _finite_field_power,
    _five_pivoted_form_value_and_gradient,
    _modular_rank,
    _order_five_subgroups,
)
from .heisenberg_level25_singleton_orbits import (
    _SINGLETON_EVIDENCE_SHA256,
    _restricted_orbit,
    level25_heisenberg_fixed_stratum_singleton_orbit_profile,
)
from .heisenberg_level25_three_block import (
    _PROGRAM_PREFIX,
    _run_source_hashed_program,
)
from .science_evidence import exact_execution_matches, reviewed_profile_matches, seal_profile_evidence
from .scientific import ScientificToolError

_PROTOCOL_VERSION = 1
_REVIEWED_PROGRAM_SHA256 = "576ca653dfd0989b70d2d1e11564f2dd558d45d0e32b75510142d5985a26dc43"
_REVIEWED_TRANSCRIPT_SHA256 = "e02e92e806098ba8afd85a65bb69ee36e60bab9a8c262641313d97036d663e2c"
_COVERAGE_EVIDENCE_SHA256 = "438fb3a52ef40fa7e41aace29658a66b4fc3c642388054a97e02bd3941e61ee1"
_LOCAL_EVIDENCE_SHA256 = "43e14323b47fbb532c19f58d1b7de175d0a6e855fc18ed63efd9c6a8c916f795"
_THREE_BLOCK_EVIDENCE_SHA256 = "1f6117123f053dcb07180e2cde3896b2dd0e1b2d5231bc1eb7f7a0d80f1250fe"
_REVIEWED_SELECTION_CHECKSUM = 14663480442488609147
_SELECTED_AUGMENTATION: tuple[tuple[int, int, int, int, int, int], ...] = (
    (9, 0, 2, 19, 18, 0),
    (7, 0, 1, 2, 23, 1),
)
_TWELVE_ROW_REVIEWED_PROGRAM_SHA256 = "a41ceb7d993f9df17c8d52a99a4a3137375a7c81e9a3cb3187debe737790798f"
_TWELVE_ROW_REVIEWED_TRANSCRIPT_SHA256 = "ac592937657e73cb49c7f508b1d533fcb595f1ab7d687cb53ce4e80f1e60a22d"
_FOUR_BLOCK_REVIEWED_PROGRAM_SHA256 = "099302d1f7e8e72367e2347dd8f0a95fe3bf0dffdad2fabb190bd1949f3ba17d"
_FOUR_BLOCK_REVIEWED_TRANSCRIPT_SHA256 = "f1bd72d0126ea16e3dcfeb5448fe4da014347adbd4e94d93238b3f94beb70251"
_FOUR_BLOCK_PHASE_REVIEWED_PROGRAM_SHA256 = "7b460251351db3bc3c7da7fecca7e138f1f1f4fc5ddf7bb07abefa85ebb606fd"
_FOUR_BLOCK_PHASE_REVIEWED_TRANSCRIPT_SHA256 = "d0d42f8385b04fe23297b02eabb0464c75fc38052b61200820b5886afc02291a"
_FOUR_BLOCK_FOURIER_REVIEWED_PROGRAM_SHA256 = "7421d66130f7c0bf913de6ac981441d2da1979fe22cda6e1d834ab3fa1fa3900"
_FOUR_BLOCK_FOURIER_REVIEWED_TRANSCRIPT_SHA256 = "36352eed7bc8dfdee52f8c7f498c3918d787a53ebf892974c4821a0bd09b1445"
_FOUR_BLOCK_ALPHABET_EVIDENCE_SHA256 = "bf392cc8d623d78ed3eef029132335fa1d974cbfa42c1294bedd2e899aade9b2"

_TWELVE_ROW_PROGRAM = r"""#include <array>
#include <cstdint>
#include <iostream>
#include <vector>

constexpr int P=101,N=25,ZETA=5;
constexpr std::array<int,12> CH{{1,8,11,12,13,14,16,17,18,19,21,22}};
constexpr std::array<int,4> ALPH{{1,10,100,91}};
int pw(int b,int e){int r=1;while(e){if(e&1)r=r*b%P;b=b*b%P;e>>=1;}return r;}
int coeff(int a,int i){int e=(-a*i)%N;if(e<0)e+=N;return pw(ZETA,e);}
void mix(std::uint64_t&hash,std::uint64_t value){for(int byte=0;byte<8;++byte){hash^=(value>>(8*byte))&255;hash*=1099511628211ULL;}}

void census(const std::array<int,3>&support,const char*label){
 int matrix[12][15]{};for(int row=0;row<12;++row)for(int block=0;block<3;++block)for(int q=0;q<5;++q)matrix[row][5*block+q]=coeff(CH[row],support[block]+5*q);
 std::vector<int>pivots;int rank=0;for(int column=0;column<15&&rank<12;++column){int pivot=rank;while(pivot<12&&!matrix[pivot][column])++pivot;if(pivot==12)continue;for(int c=0;c<15;++c)std::swap(matrix[pivot][c],matrix[rank][c]);int inverse=pw(matrix[rank][column],P-2);for(int c=0;c<15;++c)matrix[rank][c]=matrix[rank][c]*inverse%P;for(int row=0;row<12;++row)if(row!=rank&&matrix[row][column]){int factor=matrix[row][column];for(int c=0;c<15;++c){matrix[row][c]=(matrix[row][c]-factor*matrix[rank][c])%P;if(matrix[row][c]<0)matrix[row][c]+=P;}}pivots.push_back(column);++rank;}
 std::vector<int>free_columns;for(int column=0;column<15;++column){bool pivot=false;for(int value:pivots)if(value==column)pivot=true;if(!pivot)free_columns.push_back(column);}
 int assignments=1;for(int ignored:free_columns){(void)ignored;assignments*=4;}int solutions=0,block_constant=0;std::uint64_t checksum=1469598103934665603ULL;
 for(int code=0;code<assignments;++code){int work=code;std::array<int,15>x{};for(int column:free_columns){x[column]=ALPH[work%4];work/=4;}for(int row=0;row<rank;++row){int value=0;for(int column:free_columns)value=(value+matrix[row][column]*x[column])%P;x[pivots[row]]=(P-value)%P;}bool in_alphabet=true;for(int value:x){bool found=false;for(int allowed:ALPH)if(value==allowed)found=true;if(!found)in_alphabet=false;}if(!in_alphabet)continue;++solutions;bool constant=true;for(int block=0;block<3;++block)for(int q=1;q<5;++q)if(x[5*block+q]!=x[5*block])constant=false;if(constant)++block_constant;for(int value:x)mix(checksum,value);}
 std::cout<<label<<"_rank="<<rank<<"\n"<<label<<"_free_column_count="<<free_columns.size()<<"\n"<<label<<"_alphabet_assignment_count="<<assignments<<"\n"<<label<<"_solution_count="<<solutions<<"\n"<<label<<"_block_constant_solution_count="<<block_constant<<"\n"<<label<<"_solution_checksum="<<checksum<<"\n";
}

int main(){std::cout<<"REASONING_AUGMENTED_THREE_BLOCK_PURE_POWER_V1\n";census({0,1,3},"nonconsecutive");census({0,1,2},"consecutive");std::cout<<"REASONING_END\n";}
"""

_FOUR_BLOCK_PROGRAM = r"""#include <array>
#include <cstdint>
#include <iostream>
#include <vector>

constexpr int P=101,N=25,ZETA=5;
constexpr std::array<int,12> CH{{1,8,11,12,13,14,16,17,18,19,21,22}};
constexpr std::array<int,4> ALPH{{1,10,100,91}};
constexpr std::array<int,4> SUPPORT{{0,1,2,3}};
int pw(int b,int e){int r=1;while(e){if(e&1)r=r*b%P;b=b*b%P;e>>=1;}return r;}
int coeff(int a,int i){int e=(-a*i)%N;if(e<0)e+=N;return pw(ZETA,e);}
void mix(std::uint64_t&hash,std::uint64_t value){for(int byte=0;byte<8;++byte){hash^=(value>>(8*byte))&255;hash*=1099511628211ULL;}}

int main(){
 int matrix[12][20]{};for(int row=0;row<12;++row)for(int block=0;block<4;++block)for(int q=0;q<5;++q)matrix[row][5*block+q]=coeff(CH[row],SUPPORT[block]+5*q);
 std::vector<int>pivots;int rank=0;for(int column=0;column<20&&rank<12;++column){int pivot=rank;while(pivot<12&&!matrix[pivot][column])++pivot;if(pivot==12)continue;for(int c=0;c<20;++c)std::swap(matrix[pivot][c],matrix[rank][c]);int inverse=pw(matrix[rank][column],P-2);for(int c=0;c<20;++c)matrix[rank][c]=matrix[rank][c]*inverse%P;for(int row=0;row<12;++row)if(row!=rank&&matrix[row][column]){int factor=matrix[row][column];for(int c=0;c<20;++c){matrix[row][c]=(matrix[row][c]-factor*matrix[rank][c])%P;if(matrix[row][c]<0)matrix[row][c]+=P;}}pivots.push_back(column);++rank;}
 std::vector<int>free_columns;for(int column=0;column<20;++column){bool pivot=false;for(int value:pivots)if(value==column)pivot=true;if(!pivot)free_columns.push_back(column);}
 int assignments=1;for(int ignored:free_columns){(void)ignored;assignments*=4;}int solutions=0,block_constant=0;std::uint64_t checksum=1469598103934665603ULL;
 for(int code=0;code<assignments;++code){int work=code;std::array<int,20>x{};for(int column:free_columns){x[column]=ALPH[work%4];work/=4;}for(int row=0;row<rank;++row){int value=0;for(int column:free_columns)value=(value+matrix[row][column]*x[column])%P;x[pivots[row]]=(P-value)%P;}bool in_alphabet=true;for(int value:x){bool found=false;for(int allowed:ALPH)if(value==allowed)found=true;if(!found)in_alphabet=false;}if(!in_alphabet)continue;++solutions;bool constant=true;for(int block=0;block<4;++block)for(int q=1;q<5;++q)if(x[5*block+q]!=x[5*block])constant=false;if(constant)++block_constant;for(int value:x)mix(checksum,value);}
 std::cout<<"REASONING_AUGMENTED_FOUR_BLOCK_PURE_POWER_V1\n"<<"support_residues=0,1,2,3\n"<<"rank="<<rank<<"\n"<<"free_column_count="<<free_columns.size()<<"\n"<<"alphabet_assignment_count="<<assignments<<"\n"<<"solution_count="<<solutions<<"\n"<<"block_constant_solution_count="<<block_constant<<"\n"<<"solution_checksum="<<checksum<<"\nREASONING_END\n";
}
"""

_FOUR_BLOCK_PHASE_PROGRAM = r"""#include <array>
#include <iostream>
#include <vector>

constexpr int P=5;
constexpr std::array<bool,5> SUPPORT{{true,true,true,true,false}};
struct Candidate{int d,e,beta;};

std::vector<std::array<int,4>> term_vectors(Candidate candidate){
 std::vector<std::array<int,4>> rows;
 for(int start=0;start<5;++start){int middle=(start+candidate.d)%5,last=(start+candidate.e)%5;if(!SUPPORT[start]||!SUPPORT[middle]||!SUPPORT[last])continue;std::array<int,4> row{};row[start]=(row[start]+1)%P;row[middle]=(row[middle]+candidate.beta)%P;row[last]=(row[last]+24-candidate.beta)%P;rows.push_back(row);}
 return rows;
}

std::array<int,4> difference(const std::vector<std::array<int,4>>&rows){std::array<int,4> value{};for(int i=0;i<4;++i)value[i]=(rows[0][i]-rows[1][i]+P)%P;return value;}
int dot(const std::array<int,4>&a,const std::array<int,4>&b){int value=0;for(int i=0;i<4;++i)value=(value+a[i]*b[i])%P;return value;}

int main(){
 constexpr std::array<std::array<int,2>,6> old_degrees{{{{25,0}},{{20,5}},{{5,20}},{{15,10}},{{10,15}},{{5,20}}}};
 bool old_invariant=true;for(auto row:old_degrees)for(int value:row)if(value%P)old_invariant=false;
 auto q7=term_vectors({1,2,23}),q9=term_vectors({2,19,18});auto d7=difference(q7),d9=difference(q9);int multiplier=-1;for(int m=0;m<P;++m){bool equal=true;for(int i=0;i<4;++i)if(d9[i]!=(m*d7[i])%P)equal=false;if(equal)multiplier=m;}
 int phase_count=0,kernel_count=0,diagonal_count=0,normalized_count=0;std::array<int,5>hist{};for(int l0=0;l0<P;++l0)for(int l1=0;l1<P;++l1)for(int l2=0;l2<P;++l2)for(int l3=0;l3<P;++l3){std::array<int,4>phase{{l0,l1,l2,l3}};++phase_count;int h=dot(d7,phase);if(!h&&dot(d9,phase)==0)++kernel_count;if(l0==l1&&l1==l2&&l2==l3)++diagonal_count;if(!l0){++normalized_count;++hist[h];}}
 std::cout<<"REASONING_AUGMENTED_FOUR_BLOCK_PHASE_QUOTIENT_V1\n";
 std::cout<<"old_block_degree_pattern_count="<<old_degrees.size()<<"\nold_equations_block_mu5_invariant="<<old_invariant<<"\nselected_term_counts="<<q7.size()<<","<<q9.size()<<"\nq7_difference=";for(int i=0;i<4;++i){if(i)std::cout<<",";std::cout<<d7[i];}std::cout<<"\nq9_difference=";for(int i=0;i<4;++i){if(i)std::cout<<",";std::cout<<d9[i];}std::cout<<"\ndifference_multiplier="<<multiplier<<"\nphase_tuple_count="<<phase_count<<"\ncommon_kernel_count="<<kernel_count<<"\ndiagonal_count="<<diagonal_count<<"\nrelative_kernel_count="<<kernel_count/diagonal_count<<"\nnormalized_phase_tuple_count="<<normalized_count<<"\nnormalized_orbit_count="<<normalized_count/(kernel_count/diagonal_count)<<"\ninvariant_histogram=";for(int i=0;i<P;++i){if(i)std::cout<<",";std::cout<<hist[i];}std::cout<<"\ncanonical_representatives=";for(int h=0;h<P;++h){if(h)std::cout<<";";std::cout<<"0,"<<(3*h)%P<<",0,0";}std::cout<<"\nREASONING_END\n";
}
"""

_FOUR_BLOCK_FOURIER_PROGRAM = r"""#include <algorithm>
#include <array>
#include <cstdint>
#include <iostream>
#include <utility>

constexpr int P=101,N=25,ZETA=5,GEN=2;
int pw(int b,int e){int r=1;while(e){if(e&1)r=r*b%P;b=b*b%P;e>>=1;}return r;}
int coeff(int a,int i){int e=(-a*i)%N;if(e<0)e+=N;return pw(ZETA,e);}
void mix(std::uint64_t&hash,std::uint64_t value){for(int byte=0;byte<8;++byte){hash^=(value>>(8*byte))&255;hash*=1099511628211ULL;}}

int main(){
 std::array<std::uint64_t,P>hist{};for(std::uint32_t code=0;code<390625;++code){std::uint32_t work=code;std::array<int,5>u{{1}};for(int q=1;q<5;++q){u[q]=pw(ZETA,work%25);work/=25;}int value=0;for(int q=0;q<5;++q)value=(value+pw(u[q],24)*u[(q+1)%5])%P;++hist[value];}
 int matrix[3][4]{};for(int row=0;row<3;++row)for(int residue=0;residue<4;++residue)matrix[row][residue]=coeff(5*(row+2),residue);std::array<int,3>pivots{};int rank=0;for(int column=0;column<4&&rank<3;++column){int pivot=rank;while(pivot<3&&!matrix[pivot][column])++pivot;if(pivot==3)continue;for(int c=0;c<4;++c)std::swap(matrix[pivot][c],matrix[rank][c]);int inverse=pw(matrix[rank][column],P-2);for(int c=0;c<4;++c)matrix[rank][c]=matrix[rank][c]*inverse%P;for(int row=0;row<3;++row)if(row!=rank&&matrix[row][column]){int factor=matrix[row][column];for(int c=0;c<4;++c){matrix[row][c]=(matrix[row][c]-factor*matrix[rank][c])%P;if(matrix[row][c]<0)matrix[row][c]+=P;}}pivots[rank++]=column;}
 int free_column=0;while(free_column<4&&(free_column==pivots[0]||free_column==pivots[1]||free_column==pivots[2]))++free_column;std::array<int,4>kernel{};kernel[free_column]=1;for(int row=0;row<rank;++row)kernel[pivots[row]]=(P-matrix[row][free_column])%P;int normalize=pw(kernel[0],P-2);for(int&value:kernel)value=value*normalize%P;
 std::array<int,4>scale25{};for(int c=0;c<4;++c)scale25[c]=pw(GEN,25*c);std::uint64_t admissible=0,zero_line=0;for(int first=0;first<P;++first)for(int c1=0;c1<4;++c1)for(int c2=0;c2<4;++c2)for(int c3=0;c3<4;++c3){std::array<int,4>classes{{0,c1,c2,c3}};std::uint64_t count=hist[first];for(int residue=1;residue<4;++residue){int target=first*kernel[residue]%P*pw(scale25[classes[residue]],P-2)%P;count*=hist[target];}admissible+=count;if(!first)zero_line+=count;}
 std::uint64_t checksum=1469598103934665603ULL,total=0;std::uint64_t minimum=390625,maximum=0;for(int value=0;value<P;++value){mix(checksum,value);mix(checksum,hist[value]);total+=hist[value];if(value){minimum=std::min(minimum,hist[value]);maximum=std::max(maximum,hist[value]);}}
 std::cout<<"REASONING_AUGMENTED_FOUR_BLOCK_FOURIER_V1\nshape_count="<<total<<"\nzero_cyclic_sum_shape_count="<<hist[0]<<"\nnonzero_bucket_minimum="<<minimum<<"\nnonzero_bucket_maximum="<<maximum<<"\nhistogram_checksum="<<checksum<<"\nfourier_rank="<<rank<<"\nkernel_vector=";for(int i=0;i<4;++i){if(i)std::cout<<",";std::cout<<kernel[i];}std::cout<<"\nscale_twenty_fifth_power_classes=";for(int i=0;i<4;++i){if(i)std::cout<<",";std::cout<<scale25[i];}std::cout<<"\nadmissible_shape_class_quadruple_count="<<admissible<<"\nzero_line_shape_class_quadruple_count="<<zero_line<<"\nnonzero_line_shape_class_quadruple_count="<<admissible-zero_line<<"\ncyclic_sum_histogram=";for(int i=0;i<P;++i){if(i)std::cout<<",";std::cout<<hist[i];}std::cout<<"\nREASONING_END\n";
}
"""

_PROGRAM = (
    _PROGRAM_PREFIX
    + r"""
struct ShapeV {std::uint32_t code;std::array<int,5>u,p5,p20;std::array<int,3>b6,b8,b5,b7;};
struct PointRecord {std::array<int,N>x;std::uint32_t a,b,d;int m1,m2;};
struct EntryV {int key,index;bool operator<(const EntryV&o)const{return key<o.key||(key==o.key&&index<o.index);}};
struct Candidate {int row,a,d,e,beta,kind,start;};

std::array<int,5> decode_v(std::uint32_t code){std::array<int,5>u{1};for(int q=1;q<5;++q){u[q]=pw(ZETA,code%25);code/=25;}return u;}
int block_base_v(int row,int residue,const std::array<int,5>&u){std::array<int,N>x{};for(int q=0;q<5;++q)x[residue+5*q]=u[q];return form_value(row,x);}
int adjacent_v(const ShapeV&a,const ShapeV&b){int value=0;for(int q=0;q<5;++q)value=(value+a.p20[q]*b.p5[q])%P;return value;}
int nonadjacent_v(const ShapeV&a,const ShapeV&b,int residue,int character,int e1,int e5,int e2){int value=0,carry=(residue+2)/5;for(int q=0;q<5;++q){int term=coeff(character,residue+5*q);term=term*pw(a.u[q],e1)%P;term=term*pw(a.u[(q+1)%5],e5)%P;term=term*pw(b.u[(q+carry)%5],e2)%P;value=(value+term)%P;}return value;}
int nonadjacent_consecutive_v(const ShapeV&a,const ShapeV&b,int character,int e1,int e5,int e2){int value=0;for(int q=0;q<5;++q){int term=coeff(character,5*q);term=term*pw(a.u[q],e1)%P;term=term*pw(a.u[(q+1)%5],e5)%P;term=term*pw(b.u[q],e2)%P;value=(value+term)%P;}return value;}

std::vector<ShapeV> shapes_v(){
 std::vector<ShapeV>shapes;
 for(std::uint32_t code=0;code<390625;++code){auto u=decode_v(code);int cyclic_sum=0;for(int q=0;q<5;++q)cyclic_sum=(cyclic_sum+pw(u[q],24)*u[(q+1)%5])%P;if(cyclic_sum)continue;ShapeV shape{};shape.code=code;shape.u=u;for(int q=0;q<5;++q){shape.p5[q]=pw(u[q],5);shape.p20[q]=pw(u[q],20);}shapes.push_back(shape);}
 return shapes;
}

std::vector<PointRecord> collect_nonconsecutive(std::vector<ShapeV>&shapes,std::uint64_t&checksum){
 for(auto&shape:shapes)for(int j=0;j<3;++j){int residue=std::array<int,3>{0,1,3}[j];shape.b6[j]=block_base_v(6,residue,shape.u);shape.b8[j]=block_base_v(8,residue,shape.u);shape.b5[j]=block_base_v(5,residue,shape.u);shape.b7[j]=block_base_v(7,residue,shape.u);}
 std::array<std::vector<EntryV>,4> third;for(int c=0;c<4;++c){int scale=pw(GEN,25*c);for(int i=0;i<(int)shapes.size();++i)third[c].push_back({shapes[i].b6[2]*scale%P+P*(shapes[i].b8[2]*scale%P),i});std::sort(third[c].begin(),third[c].end());}
 std::vector<PointRecord>zeros;checksum=1469598103934665603ULL;
 for(int ia=0;ia<(int)shapes.size();++ia)for(int ib=0;ib<(int)shapes.size();++ib){if(adjacent_v(shapes[ia],shapes[ib]))continue;for(int c1=0;c1<4;++c1){int scale1_25=pw(GEN,25*c1);int v6=(shapes[ia].b6[0]+scale1_25*shapes[ib].b6[1])%P,v8=(shapes[ia].b8[0]+scale1_25*shapes[ib].b8[1])%P;for(int c3=0;c3<4;++c3){int key=((P-v6)%P)+P*((P-v8)%P);auto begin=std::lower_bound(third[c3].begin(),third[c3].end(),EntryV{key,-1});auto end=std::upper_bound(third[c3].begin(),third[c3].end(),EntryV{key,999999});for(auto it=begin;it!=end;++it){auto&a=shapes[ia];auto&b=shapes[ib];auto&d=shapes[it->index];int scale3_25=pw(GEN,25*c3);int base5=(a.b5[0]+scale1_25*b.b5[1]+scale3_25*d.b5[2])%P,base7=(a.b7[0]+scale1_25*b.b7[1]+scale3_25*d.b7[2])%P;int p13[4]{nonadjacent_v(b,d,1,2,1,4,20),nonadjacent_v(b,d,1,2,2,13,10),nonadjacent_v(b,d,1,2,4,6,15),nonadjacent_v(b,d,1,4,1,4,20)};int p30[4]{nonadjacent_v(d,a,3,2,1,4,20),nonadjacent_v(d,a,3,2,2,13,10),nonadjacent_v(d,a,3,2,4,6,15),nonadjacent_v(d,a,3,4,1,4,20)};for(int m1=c1;m1<20;m1+=4)for(int m3=c3;m3<20;m3+=4){int row5=base5,row7=base7,s1=pw(GEN,m1),s3=pw(GEN,m3);int source1[3]{5,15,10},target3[3]{20,10,15},source3[3]{5,15,10};for(int k=0;k<3;++k)row5=(row5+p13[k]*pw(s1,source1[k])%P*pw(s3,target3[k])+p30[k]*pw(s3,source3[k]))%P;row7=(row7+p13[3]*pw(s1,5)%P*pw(s3,20)+p30[3]*pw(s3,5))%P;if(row5||row7)continue;std::array<int,N>x{};for(int q=0;q<5;++q){x[5*q]=a.u[q];x[1+5*q]=s1*b.u[q]%P;x[3+5*q]=s3*d.u[q]%P;}bool replay=true;for(int row=0;row<22;++row)if(form_value(row,x)){replay=false;break;}if(!replay)continue;int rank=jac_rank(x);zeros.push_back({x,a.code,b.code,d.code,m1,m3});mix(checksum,a.code);mix(checksum,b.code);mix(checksum,d.code);mix(checksum,m1);mix(checksum,m3);mix(checksum,rank);}}}}}
 return zeros;
}

static std::uint32_t coverage_scale_masks[4][4][101][101];
std::vector<PointRecord> collect_consecutive(std::vector<ShapeV>&shapes,std::uint64_t&checksum){
 for(auto&shape:shapes)for(int residue=0;residue<3;++residue){shape.b6[residue]=block_base_v(6,residue,shape.u);shape.b8[residue]=block_base_v(8,residue,shape.u);shape.b5[residue]=block_base_v(5,residue,shape.u);shape.b7[residue]=block_base_v(7,residue,shape.u);}
 int second_edge_coefficient=coeff(5,1);for(int c1=0;c1<4;++c1)for(int c2=0;c2<4;++c2)for(int first=0;first<P;++first)for(int second=0;second<P;++second){std::uint32_t mask=0;int bit=0;for(int m1=c1;m1<20;m1+=4)for(int m2=c2;m2<20;m2+=4,++bit){int s1=pw(GEN,m1),s2=pw(GEN,m2);if((pw(s1,5)*first+second_edge_coefficient*pw(s1,20)%P*pw(s2,5)%P*second)%P==0)mask|=1U<<bit;}coverage_scale_masks[c1][c2][first][second]=mask;}
 std::array<int,10201>head{};std::vector<int>next(shapes.size());std::vector<PointRecord>zeros;checksum=1469598103934665603ULL;
 for(int ib=0;ib<(int)shapes.size();++ib)for(int c1=0;c1<4;++c1){head.fill(-1);int scale1_25=pw(GEN,25*c1);for(int ia=0;ia<(int)shapes.size();++ia){int key=(shapes[ia].b6[0]+scale1_25*shapes[ib].b6[1])%P+P*((shapes[ia].b8[0]+scale1_25*shapes[ib].b8[1])%P);next[ia]=head[key];head[key]=ia;}for(int c2=0;c2<4;++c2){int scale2_25=pw(GEN,25*c2);for(int id=0;id<(int)shapes.size();++id){int key=(P-scale2_25*shapes[id].b6[2]%P)%P+P*((P-scale2_25*shapes[id].b8[2]%P)%P);for(int ia=head[key];ia>=0;ia=next[ia]){int first_adjacent=adjacent_v(shapes[ia],shapes[ib]),second_adjacent=adjacent_v(shapes[ib],shapes[id]);std::uint32_t mask=coverage_scale_masks[c1][c2][first_adjacent][second_adjacent];if(!mask)continue;int base5=(shapes[ia].b5[0]+scale1_25*shapes[ib].b5[1]+scale2_25*shapes[id].b5[2])%P,base7=(shapes[ia].b7[0]+scale1_25*shapes[ib].b7[1]+scale2_25*shapes[id].b7[2])%P;int pivot[4]{nonadjacent_consecutive_v(shapes[ia],shapes[id],2,1,4,20),nonadjacent_consecutive_v(shapes[ia],shapes[id],2,2,13,10),nonadjacent_consecutive_v(shapes[ia],shapes[id],2,4,6,15),nonadjacent_consecutive_v(shapes[ia],shapes[id],4,1,4,20)};int bit=0;for(int m1=c1;m1<20;m1+=4)for(int m2=c2;m2<20;m2+=4,++bit){if(!(mask&(1U<<bit)))continue;int s2=pw(GEN,m2);int row5=(base5+pivot[0]*pw(s2,20)+pivot[1]*pw(s2,10)+pivot[2]*pw(s2,15))%P,row7=(base7+pivot[3]*pw(s2,20))%P;if(row5||row7)continue;std::array<int,N>x{};int s1=pw(GEN,m1);for(int q=0;q<5;++q){x[5*q]=shapes[ia].u[q];x[1+5*q]=s1*shapes[ib].u[q]%P;x[2+5*q]=s2*shapes[id].u[q]%P;}bool replay=true;for(int row=0;row<22;++row)if(form_value(row,x)){replay=false;break;}if(!replay)continue;int rank=jac_rank(x);zeros.push_back({x,shapes[ia].code,shapes[ib].code,shapes[id].code,m1,m2});mix(checksum,shapes[ia].code);mix(checksum,shapes[ib].code);mix(checksum,shapes[id].code);mix(checksum,m1);mix(checksum,m2);mix(checksum,rank);}}}}}
 return zeros;
}

int matching_start(int d,int e,const std::array<int,3>&support){for(int start=0;start<5;++start){std::array<bool,5>have{};have[start]=true;have[(start+d)%5]=true;have[(start+e)%5]=true;bool same=true;for(int r=0;r<5;++r){bool wanted=std::find(support.begin(),support.end(),r)!=support.end();if(have[r]!=wanted)same=false;}if(same)return start;}return -1;}
int orbit_value(const Candidate&candidate,const std::array<int,N>&x){int value=0;for(int q=0;q<5;++q){int i=candidate.start+5*q,j=(i+candidate.d)%N,k=(i+candidate.e)%N;int term=coeff(candidate.a,i);term=term*x[i]%P;term=term*pw(x[j],candidate.beta)%P;term=term*pw(x[k],24-candidate.beta)%P;value=(value+term)%P;}return value;}

int main(){
 auto shapes=shapes_v();std::uint64_t checksum_nonconsecutive=0,checksum_consecutive=0;auto nonconsecutive=collect_nonconsecutive(shapes,checksum_nonconsecutive);auto consecutive=collect_consecutive(shapes,checksum_consecutive);
 std::vector<Candidate>candidates;std::array<int,3>support_n{{0,1,3}},support_c{{0,1,2}};int candidate_n=0,candidate_c=0;
 for(int d=1;d<N;++d)for(int e=1;e<N;++e){if(d%5==0||e%5==0||d%5==e%5)continue;for(int beta=1;beta<24;++beta){int weight=(beta*d+(24-beta)*e)%N,b=(N-weight)%N;if(b)continue;for(int row=0;row<22;++row){auto[a,target_b]=CH[row];if(target_b||a%5==0)continue;int start_n=matching_start(d,e,support_n),start_c=matching_start(d,e,support_c);if(start_n>=0){candidates.push_back({row,a,d,e,beta,0,start_n});++candidate_n;}if(start_c>=0){candidates.push_back({row,a,d,e,beta,1,start_c});++candidate_c;}}}}
 int total=nonconsecutive.size()+consecutive.size(),words=(total+63)/64;std::vector<std::vector<std::uint64_t>>coverage(candidates.size(),std::vector<std::uint64_t>(words));std::uint64_t evaluations=0;
 for(int ci=0;ci<(int)candidates.size();++ci){auto&candidate=candidates[ci];if(candidate.kind==0){for(int p=0;p<(int)nonconsecutive.size();++p){++evaluations;if(orbit_value(candidate,nonconsecutive[p].x))coverage[ci][p/64]|=1ULL<<(p%64);}}else{int offset=nonconsecutive.size();for(int p=0;p<(int)consecutive.size();++p){++evaluations;if(orbit_value(candidate,consecutive[p].x))coverage[ci][(offset+p)/64]|=1ULL<<((offset+p)%64);}}}
 std::vector<std::uint64_t>uncovered(words,~0ULL);if(total%64)uncovered.back()=(1ULL<<(total%64))-1;std::array<bool,22>used_rows{};std::vector<int>selected;std::vector<int>remaining;std::uint64_t selection_checksum=1469598103934665603ULL;
 for(int step=0;step<14;++step){int best=-1,gain=-1;for(int ci=0;ci<(int)candidates.size();++ci){if(used_rows[candidates[ci].row])continue;int current=0;for(int word=0;word<words;++word)current+=__builtin_popcountll(coverage[ci][word]&uncovered[word]);if(current>gain){gain=current;best=ci;}}if(best<0||gain==0)break;selected.push_back(best);used_rows[candidates[best].row]=true;for(int word=0;word<words;++word)uncovered[word]&=~coverage[best][word];int left=0;for(auto word:uncovered)left+=__builtin_popcountll(word);remaining.push_back(left);auto&c=candidates[best];mix(selection_checksum,c.a);mix(selection_checksum,c.d);mix(selection_checksum,c.e);mix(selection_checksum,c.beta);mix(selection_checksum,c.kind);mix(selection_checksum,gain);mix(selection_checksum,left);if(!left)break;}
 int final_uncovered=remaining.empty()?total:remaining.back();
 std::cout<<"REASONING_THREE_BLOCK_SINGLETON_COVERAGE_V1\n";
 std::cout<<"normalized_shape_count="<<shapes.size()<<"\nnonconsecutive_zero_count="<<nonconsecutive.size()<<"\nnonconsecutive_zero_checksum="<<checksum_nonconsecutive<<"\nconsecutive_zero_count="<<consecutive.size()<<"\nconsecutive_zero_checksum="<<checksum_consecutive<<"\nb_zero_candidate_count="<<candidates.size()<<"\nnonconsecutive_candidate_count="<<candidate_n<<"\nconsecutive_candidate_count="<<candidate_c<<"\ncandidate_point_evaluation_count="<<evaluations<<"\nselected_candidate_count="<<selected.size()<<"\nselection_checksum="<<selection_checksum<<"\nfinal_uncovered_count="<<final_uncovered<<"\n";
 std::cout<<"selected_candidates=";for(int i=0;i<(int)selected.size();++i){if(i)std::cout<<";";auto&c=candidates[selected[i]];std::cout<<c.a<<",0,"<<c.d<<","<<c.e<<","<<c.beta<<","<<c.kind;}std::cout<<"\nremaining_counts=";for(int i=0;i<(int)remaining.size();++i){if(i)std::cout<<",";std::cout<<remaining[i];}std::cout<<"\nREASONING_END\n";
}
"""
)


def _parse_records(value: str, width: int, label: str) -> list[list[int]]:
    if not value:
        return []
    try:
        records = [[int(item) for item in record.split(",")] for record in value.split(";")]
    except ValueError as error:
        raise ScientificToolError(f"three-block coverage transcript has a nonintegral {label}") from error
    if any(len(record) != width for record in records):
        raise ScientificToolError(f"three-block coverage transcript has a malformed {label}")
    return records


def _parse_transcript(stdout: str) -> dict[str, Any]:
    lines = stdout.strip().splitlines()
    if len(lines) < 3 or lines[0] != f"REASONING_THREE_BLOCK_SINGLETON_COVERAGE_V{_PROTOCOL_VERSION}" or lines[-1] != "REASONING_END":
        raise ScientificToolError("three-block coverage transcript has invalid framing")
    fields: dict[str, str] = {}
    for line in lines[1:-1]:
        if "=" not in line:
            raise ScientificToolError("three-block coverage transcript has an invalid field")
        name, value = line.split("=", 1)
        if name in fields:
            raise ScientificToolError("three-block coverage transcript has duplicate fields")
        fields[name] = value
    integer_names = {
        "normalized_shape_count",
        "nonconsecutive_zero_count",
        "nonconsecutive_zero_checksum",
        "consecutive_zero_count",
        "consecutive_zero_checksum",
        "b_zero_candidate_count",
        "nonconsecutive_candidate_count",
        "consecutive_candidate_count",
        "candidate_point_evaluation_count",
        "selected_candidate_count",
        "selection_checksum",
        "final_uncovered_count",
    }
    expected = integer_names | {"selected_candidates", "remaining_counts"}
    if set(fields) != expected:
        raise ScientificToolError("three-block coverage transcript is incomplete")
    try:
        integers = {name: int(fields[name]) for name in integer_names}
        remaining = [int(item) for item in fields["remaining_counts"].split(",") if item]
    except ValueError as error:
        raise ScientificToolError("three-block coverage transcript has a nonintegral count") from error
    return {**integers, "selected_candidates": _parse_records(fields["selected_candidates"], 6, "candidate"), "remaining_counts": remaining}


def _run_program() -> dict[str, Any]:
    computation = _run_source_hashed_program(
        _PROGRAM,
        label="three-block-singleton-coverage",
        timeout_seconds=300,
        preserve_exact_transcript=True,
    )
    return {**computation, **_parse_transcript(computation["transcript"])}


def _orbit_value_and_gradient(candidate: list[int], point: list[int]) -> tuple[int, list[int]]:
    first_character, second_character, first_offset, second_offset, middle_exponent, _kind = candidate
    assert second_character == 0
    offsets = (0, first_offset, second_offset)
    exponents = (1, middle_exponent, MODULUS - 1 - middle_exponent)
    value = 0
    gradient = [0] * MODULUS
    for index in range(MODULUS):
        indices = [(index + offset) % MODULUS for offset in offsets]
        coefficient = _finite_field_power(-first_character * index)
        term = coefficient
        for coordinate, exponent in zip(indices, exponents, strict=True):
            term = term * pow(point[coordinate], exponent, FINITE_FIELD_PRIME) % FINITE_FIELD_PRIME
        value = (value + term) % FINITE_FIELD_PRIME
        for factor, (coordinate, exponent) in enumerate(zip(indices, exponents, strict=True)):
            derivative = coefficient * exponent % FINITE_FIELD_PRIME
            for other, (other_coordinate, other_exponent) in enumerate(zip(indices, exponents, strict=True)):
                derivative = (
                    derivative
                    * pow(point[other_coordinate], other_exponent - int(factor == other), FINITE_FIELD_PRIME)
                    % FINITE_FIELD_PRIME
                )
            gradient[coordinate] = (gradient[coordinate] + derivative) % FINITE_FIELD_PRIME
    return value, gradient


def _replay_selected_on_first_witnesses(computation: dict[str, Any]) -> list[dict[str, Any]]:
    points = [
        [1, 11, 0, 28, 0, 79, 3, 0, 8, 0, 95, 59, 0, 18, 0, 1, 75, 0, 46, 0, 1, 2, 0, 60, 0],
        [1, 75, 98, 0, 0, 5, 67, 18, 0, 0, 97, 55, 94, 0, 0, 25, 75, 39, 0, 0, 1, 75, 39, 0, 0],
    ]
    rows = []
    for kind, point in enumerate(points):
        original = [_five_pivoted_form_value_and_gradient(character, point) for character in LEVEL25_HEISENBERG_BALANCED_CHARACTERS]
        selected = [candidate for candidate in computation["selected_candidates"] if candidate[5] == kind]
        candidate_values = []
        for candidate in selected:
            value, _gradient = _orbit_value_and_gradient(candidate, point)
            candidate_values.append(value)
        rows.append(
            {
                "support_kind": "nonconsecutive" if kind == 0 else "consecutive",
                "original_nonzero_form_count": sum(value != 0 for value, _gradient in original),
                "original_jacobian_rank": _modular_rank([gradient for _value, gradient in original]),
                "selected_candidate_values": candidate_values,
                "nonzero_selected_candidate_value_count": sum(value != 0 for value in candidate_values),
            }
        )
    return rows


def level25_heisenberg_singleton_orbit_three_block_coverage_profile() -> dict[str, Any]:
    """Select fixed-stratum-preserving b=0 orbits covering all known zeros."""

    fixed_screen = level25_heisenberg_fixed_stratum_singleton_orbit_profile()
    fixed_screen_sha256 = hashlib.sha256(canonical_json(fixed_screen).encode()).hexdigest()
    computation = _run_program()
    witness_replays = _replay_selected_on_first_witnesses(computation)
    certificate = {
        "program_transcript_and_selection_checksum_match_reviewed_computation": (
            exact_execution_matches(computation, parser=_parse_transcript, expected_program=_REVIEWED_PROGRAM_SHA256, expected_transcript=_REVIEWED_TRANSCRIPT_SHA256)
            and computation["selection_checksum"] == _REVIEWED_SELECTION_CHECKSUM
        ),
        "fixed_stratum_candidate_screen_is_the_certified_prerequisite": reviewed_profile_matches(fixed_screen, _SINGLETON_EVIDENCE_SHA256),
        "both_original_three_block_zero_censuses_are_reproduced": (
            computation["nonconsecutive_zero_count"] == 12400
            and computation["nonconsecutive_zero_checksum"] == 4071796996668289174
            and computation["consecutive_zero_count"] == 4100
            and computation["consecutive_zero_checksum"] == 8483181085099946785
        ),
        "all_b_zero_fixed_preserving_candidates_are_screened": (
            computation["b_zero_candidate_count"] == 4200
            and computation["nonconsecutive_candidate_count"] == computation["consecutive_candidate_count"] == 2100
        ),
        "greedy_selection_uses_distinct_balanced_rows": len({candidate[0] for candidate in computation["selected_candidates"]})
        == computation["selected_candidate_count"],
        "selected_candidates_cover_every_original_three_block_zero": computation["final_uncovered_count"] == 0
        and computation["remaining_counts"][-1] == 0,
        "first_witnesses_are_independently_removed": len(witness_replays) == 2 and all(
            row["support_kind"] == ("nonconsecutive" if index == 0 else "consecutive")
            and len(row["selected_candidate_values"]) == sum(candidate[5] == index for candidate in computation["selected_candidates"])
            and row["nonzero_selected_candidate_value_count"] == sum(value != 0 for value in row["selected_candidate_values"])
            and all(0 <= value < FINITE_FIELD_PRIME for value in row["selected_candidate_values"])
            and row["original_nonzero_form_count"] == 0
            and row["original_jacobian_rank"] == 14
            and row["nonzero_selected_candidate_value_count"] > 0
            for index, row in enumerate(witness_replays)
        ),
    }
    request: dict[str, Any] = {}
    verified = all(certificate.values())
    return seal_profile_evidence({
        "schema": "reasoning.level25-heisenberg-singleton-orbit-three-block-coverage-profile.v2",
        "request": request,
        "request_sha256": hashlib.sha256(canonical_json(request).encode()).hexdigest(),
        "fixed_stratum_screen_receipt_sha256": fixed_screen_sha256,
        "fixed_stratum_screen_evidence_sha256": fixed_screen["evidence_sha256"],
        "computation": computation,
        "first_witness_replays": witness_replays,
        "certificate": certificate,
        "certificate_verified": verified,
        "decision": {"status": "ORIGINAL_THREE_BLOCK_ZEROS_COVERED" if verified else "PROFILE_INVALID"},
        "execution_limits": {
            "primary_wall_seconds": 300,
            "primary_hard_address_space_bytes": None,
            "independent_first_witness_replay": "in_process_without_a_separate_hard_memory_or_wall_limit",
        },
        "interpretation": (
            "A deterministic distinct-row greedy selection from the certified b=0 singleton-orbit library has a nonzero value at every one of the 16,500 original F_101 three-block zeros, so none of those old points survives the selected augmentation. This does not exclude new augmented-system zeros: two of the fourteen pure-power equations have changed, and their old values can cancel against the added orbit values outside the old block-constant parameterization."
        ),
        "not_certified": [
            "absence of new F_101-rational three-block common zeros after augmentation",
            "absence of geometric three-block points over the algebraic closure of F_101",
            "the four-block or five-block rational support strata",
            "the cyclic-eigenpoint Jacobian ranks after inserting the selected terms",
            "global dimension, regular-sequence status, smoothness, integrality, or a characteristic-zero lift",
            "a Brauer class, period, index bound, or period-index counterexample",
        ],
        "next_gate": {
            "feature": "level25_heisenberg_augmented_three_block_rational_profile",
            "requirement": (
                "Reclassify the twelve unchanged pure-power equations together with the two augmented b=0 equations, then exhaust both three-block support orbits for the new system before treating larger supports."
            ),
        },
    })


def _augmented_form_value_and_gradient(
    character: tuple[int, int],
    point: list[int],
) -> tuple[int, list[int]]:
    value, gradient = _five_pivoted_form_value_and_gradient(character, point)
    for candidate_tuple in _SELECTED_AUGMENTATION:
        candidate = list(candidate_tuple)
        if character != tuple(candidate[:2]):
            continue
        orbit_value, orbit_gradient = _orbit_value_and_gradient(candidate, point)
        value = (value + orbit_value) % FINITE_FIELD_PRIME
        gradient = [(entry + addition) % FINITE_FIELD_PRIME for entry, addition in zip(gradient, orbit_gradient, strict=True)]
    return value, gradient


def _augmented_cyclic_eigenpoint_census() -> dict[str, Any]:
    rows = []
    invalid_eigenpoints = 0
    common_membership_changes = 0
    augmented_common_zeros = 0
    augmented_rank_22 = 0
    for subgroup in _order_five_subgroups():
        for lift_index, generator in enumerate(subgroup["cyclic_lifts"]):
            common_zero_count = 0
            rank_distribution: dict[int, int] = {}
            for eigenvalue_index in range(MODULUS):
                point = _cyclic_eigenpoint(generator, eigenvalue_index)
                invalid_eigenpoints += int(
                    not _cyclic_eigenpoint_equation_holds(
                        generator,
                        eigenvalue_index,
                        point,
                    )
                )
                original = [_five_pivoted_form_value_and_gradient(character, point) for character in LEVEL25_HEISENBERG_BALANCED_CHARACTERS]
                augmented = [_augmented_form_value_and_gradient(character, point) for character in LEVEL25_HEISENBERG_BALANCED_CHARACTERS]
                original_common = all(value == 0 for value, _gradient in original)
                augmented_common = all(value == 0 for value, _gradient in augmented)
                common_membership_changes += int(original_common != augmented_common)
                if not augmented_common:
                    continue
                common_zero_count += 1
                augmented_common_zeros += 1
                rank = _modular_rank([gradient for _value, gradient in augmented])
                rank_distribution[rank] = rank_distribution.get(rank, 0) + 1
                augmented_rank_22 += int(rank == 22)
            rows.append(
                {
                    "subgroup_label": subgroup["label"],
                    "lift_index": lift_index,
                    "generator": list(generator),
                    "eigenpoint_count": MODULUS,
                    "augmented_common_zero_count": common_zero_count,
                    "augmented_common_zero_rank_distribution": {str(rank): count for rank, count in sorted(rank_distribution.items())},
                }
            )
    return {
        "lift_count": len(rows),
        "eigenpoint_count": len(rows) * MODULUS,
        "invalid_eigenpoint_count": invalid_eigenpoints,
        "common_zero_membership_change_count": common_membership_changes,
        "augmented_common_zero_count": augmented_common_zeros,
        "augmented_rank_22_count": augmented_rank_22,
        "lifts": rows,
    }


def level25_heisenberg_augmented_fixed_and_cyclic_local_profile() -> dict[str, Any]:
    """Replay fixed-restriction and cyclic-eigenpoint gates after augmentation."""

    coverage = level25_heisenberg_singleton_orbit_three_block_coverage_profile()
    coverage_sha256 = hashlib.sha256(canonical_json(coverage).encode()).hexdigest()
    coordinate_maps = [_coordinate_restriction(subgroup, eigenspace) for subgroup in range(PRIME + 1) for eigenspace in range(PRIME)]
    selected_restrictions = []
    for candidate_tuple in _SELECTED_AUGMENTATION:
        first_character, second_character, first_offset, second_offset, middle_exponent, support_kind = candidate_tuple
        restrictions = [
            _restricted_orbit(
                first_character,
                (0, first_offset, second_offset),
                (1, middle_exponent, MODULUS - 1 - middle_exponent),
                coordinate_map,
            )
            for coordinate_map in coordinate_maps
        ]
        selected_restrictions.append(
            {
                "target_character": [first_character, second_character],
                "offsets": [0, first_offset, second_offset],
                "exponents": [1, middle_exponent, MODULUS - 1 - middle_exponent],
                "support_kind": "nonconsecutive" if support_kind == 0 else "consecutive",
                "nonzero_fixed_restriction_count": sum(bool(restriction) for restriction in restrictions),
            }
        )
    cyclic = _augmented_cyclic_eigenpoint_census()
    expected_lifts = [
        (subgroup["label"], lift_index, list(generator))
        for subgroup in _order_five_subgroups()
        for lift_index, generator in enumerate(subgroup["cyclic_lifts"])
    ]
    certificate = {
        "three_block_coverage_is_the_certified_prerequisite": reviewed_profile_matches(coverage, _COVERAGE_EVIDENCE_SHA256),
        "selected_orbits_are_exactly_the_coverage_selection": [list(candidate) for candidate in _SELECTED_AUGMENTATION]
        == coverage["computation"]["selected_candidates"],
        "both_selected_orbits_vanish_on_all_thirty_fixed_strata": all(
            row["nonzero_fixed_restriction_count"] == 0 for row in selected_restrictions
        ),
        "all_seven_hundred_fifty_cyclic_eigenpoints_are_valid": cyclic["lift_count"] == 30
        and len(cyclic["lifts"]) == 30
        and [(row["subgroup_label"], row["lift_index"], row["generator"]) for row in cyclic["lifts"]] == expected_lifts
        and all(row["eigenpoint_count"] == MODULUS for row in cyclic["lifts"])
        and cyclic["eigenpoint_count"] == 750
        and cyclic["invalid_eigenpoint_count"] == 0,
        "augmentation_does_not_change_cyclic_common_zero_membership": cyclic["common_zero_membership_change_count"] == 0
        and cyclic["augmented_common_zero_count"] == 125
        and sum(row["augmented_common_zero_count"] for row in cyclic["lifts"]) == 125,
        "all_augmented_cyclic_common_zeros_retain_rank_twenty_two": cyclic["augmented_rank_22_count"] == 125
        and all(
            row["augmented_common_zero_rank_distribution"] in ({}, {"22": 25})
            and sum(row["augmented_common_zero_rank_distribution"].values()) == row["augmented_common_zero_count"]
            for row in cyclic["lifts"]
        ),
    }
    request: dict[str, Any] = {}
    verified = all(certificate.values())
    return seal_profile_evidence({
        "schema": "reasoning.level25-heisenberg-augmented-fixed-and-cyclic-local-profile.v2",
        "request": request,
        "request_sha256": hashlib.sha256(canonical_json(request).encode()).hexdigest(),
        "coverage_receipt_sha256": coverage_sha256,
        "coverage_evidence_sha256": coverage["evidence_sha256"],
        "selected_augmentation": [list(candidate) for candidate in _SELECTED_AUGMENTATION],
        "selected_fixed_restriction_replay": selected_restrictions,
        "cyclic_eigenpoint_replay": cyclic,
        "certificate": certificate,
        "certificate_verified": verified,
        "decision": {"status": "FIXED_AND_CYCLIC_LOCAL_GATES_CERTIFIED" if verified else "PROFILE_INVALID"},
        "execution_limits": {"mode": "in_process_without_a_separate_hard_memory_or_wall_limit"},
        "interpretation": (
            "The two selected b=0 singleton orbits leave all thirty order-five fixed-P4 restriction polynomials unchanged. Across all 750 cyclic eigenpoints, they preserve the exact set of 125 common zeros and every such zero still has all-column Jacobian rank 22."
        ),
        "not_certified": [
            "global Jacobian rank away from the cyclic eigenpoints",
            "absence of new F_101-rational three-block common zeros after augmentation",
            "absence of geometric three-block points over the algebraic closure of F_101",
            "the four-block or five-block rational support strata",
            "global dimension, regular-sequence status, smoothness, integrality, or a characteristic-zero lift",
            "a Brauer class, period, index bound, or period-index counterexample",
        ],
        "next_gate": {
            "feature": "level25_heisenberg_augmented_three_block_rational_profile",
            "requirement": (
                "Reclassify the twelve unchanged pure-power equations together with the two explicit b=0 couplings and exhaust both augmented three-block F_101-rational support orbits with all-column Jacobian replay."
            ),
        },
    })


def _parse_twelve_row_transcript(stdout: str) -> dict[str, Any]:
    lines = stdout.strip().splitlines()
    if len(lines) < 3 or lines[0] != "REASONING_AUGMENTED_THREE_BLOCK_PURE_POWER_V1" or lines[-1] != "REASONING_END":
        raise ScientificToolError("augmented three-block pure-power transcript has invalid framing")
    fields: dict[str, int] = {}
    for line in lines[1:-1]:
        if "=" not in line:
            raise ScientificToolError("augmented three-block pure-power transcript has an invalid field")
        name, value = line.split("=", 1)
        if name in fields:
            raise ScientificToolError("augmented three-block pure-power transcript has duplicate fields")
        try:
            fields[name] = int(value)
        except ValueError as error:
            raise ScientificToolError("augmented three-block pure-power transcript has a nonintegral field") from error
    suffixes = {
        "rank",
        "free_column_count",
        "alphabet_assignment_count",
        "solution_count",
        "block_constant_solution_count",
        "solution_checksum",
    }
    expected = {f"{kind}_{suffix}" for kind in ("nonconsecutive", "consecutive") for suffix in suffixes}
    if set(fields) != expected:
        raise ScientificToolError("augmented three-block pure-power transcript is incomplete")
    return fields


def _run_twelve_row_program() -> dict[str, Any]:
    computation = _run_source_hashed_program(
        _TWELVE_ROW_PROGRAM,
        label="augmented-three-block-pure-power",
        preserve_exact_transcript=True,
    )
    return {**computation, **_parse_twelve_row_transcript(computation["transcript"])}


def _modular_rref(matrix: list[list[int]]) -> tuple[list[list[int]], list[int]]:
    reduced = [row[:] for row in matrix]
    pivots = []
    rank = 0
    for column in range(len(reduced[0])):
        pivot = next((row for row in range(rank, len(reduced)) if reduced[row][column]), None)
        if pivot is None:
            continue
        reduced[rank], reduced[pivot] = reduced[pivot], reduced[rank]
        inverse = pow(reduced[rank][column], FINITE_FIELD_PRIME - 2, FINITE_FIELD_PRIME)
        reduced[rank] = [entry * inverse % FINITE_FIELD_PRIME for entry in reduced[rank]]
        for row in range(len(reduced)):
            if row == rank or not reduced[row][column]:
                continue
            factor = reduced[row][column]
            reduced[row] = [
                (entry - factor * pivot_entry) % FINITE_FIELD_PRIME for entry, pivot_entry in zip(reduced[row], reduced[rank], strict=True)
            ]
        pivots.append(column)
        rank += 1
        if rank == len(reduced):
            break
    return reduced, pivots


def _python_twelve_row_census(support: tuple[int, ...]) -> dict[str, int]:
    remaining_characters = (1, 8, 11, 12, 13, 14, 16, 17, 18, 19, 21, 22)
    coordinates = [residue + PRIME * quotient for residue in support for quotient in range(PRIME)]
    matrix = [[_finite_field_power(-character * coordinate) for coordinate in coordinates] for character in remaining_characters]
    reduced, pivots = _modular_rref(matrix)
    free_columns = [column for column in range(len(coordinates)) if column not in pivots]
    alphabet = (1, 10, 100, 91)
    solutions = 0
    block_constant = 0
    checksum = 1469598103934665603

    def mix(value: int) -> None:
        nonlocal checksum
        for byte in range(8):
            checksum ^= (value >> (8 * byte)) & 255
            checksum = checksum * 1099511628211 & ((1 << 64) - 1)

    for code in range(len(alphabet) ** len(free_columns)):
        work = code
        values = [0] * len(coordinates)
        for column in free_columns:
            values[column] = alphabet[work % len(alphabet)]
            work //= len(alphabet)
        for row, pivot in enumerate(pivots):
            values[pivot] = -sum(reduced[row][column] * values[column] for column in free_columns) % FINITE_FIELD_PRIME
        if any(value not in alphabet for value in values):
            continue
        solutions += 1
        is_block_constant = all(len(set(values[PRIME * block : PRIME * (block + 1)])) == 1 for block in range(len(support)))
        block_constant += int(is_block_constant)
        for value in values:
            mix(value)
    return {
        "rank": len(pivots),
        "free_column_count": len(free_columns),
        "alphabet_assignment_count": len(alphabet) ** len(free_columns),
        "solution_count": solutions,
        "block_constant_solution_count": block_constant,
        "solution_checksum": checksum,
    }


def level25_heisenberg_augmented_three_block_rational_profile() -> dict[str, Any]:
    """Exclude fresh augmented-system rational zeros on exact three-block support."""

    coverage = level25_heisenberg_singleton_orbit_three_block_coverage_profile()
    coverage_sha256 = hashlib.sha256(canonical_json(coverage).encode()).hexdigest()
    computation = _run_twelve_row_program()
    python_censuses = {
        "nonconsecutive": _python_twelve_row_census((0, 1, 3)),
        "consecutive": _python_twelve_row_census((0, 1, 2)),
    }
    compiled_censuses = {
        kind: {
            suffix: computation[f"{kind}_{suffix}"]
            for suffix in (
                "rank",
                "free_column_count",
                "alphabet_assignment_count",
                "solution_count",
                "block_constant_solution_count",
                "solution_checksum",
            )
        }
        for kind in python_censuses
    }
    certificate = {
        "program_and_transcript_match_reviewed_computation": exact_execution_matches(computation, parser=_parse_twelve_row_transcript, expected_program=_TWELVE_ROW_REVIEWED_PROGRAM_SHA256, expected_transcript=_TWELVE_ROW_REVIEWED_TRANSCRIPT_SHA256),
        "compiled_and_python_censuses_agree": compiled_censuses == python_censuses,
        "old_zero_coverage_is_the_corrected_certified_prerequisite": reviewed_profile_matches(coverage, _COVERAGE_EVIDENCE_SHA256),
        "twelve_unchanged_rows_force_block_constant_nonzero_powers": all(
            row["rank"] == 11
            and row["free_column_count"] == 4
            and row["alphabet_assignment_count"] == 256
            and row["solution_count"] == row["block_constant_solution_count"] == 64
            for row in python_censuses.values()
        ),
        "both_selected_orbits_cover_every_resulting_original_zero": coverage["computation"]["final_uncovered_count"] == 0
        and coverage["computation"]["remaining_counts"] == [4100, 0],
    }
    request: dict[str, Any] = {}
    verified = all(certificate.values())
    return seal_profile_evidence({
        "schema": "reasoning.level25-heisenberg-augmented-three-block-rational-profile.v2",
        "request": request,
        "request_sha256": hashlib.sha256(canonical_json(request).encode()).hexdigest(),
        "coverage_receipt_sha256": coverage_sha256,
        "coverage_evidence_sha256": coverage["evidence_sha256"],
        "remaining_pure_power_characters": [1, 8, 11, 12, 13, 14, 16, 17, 18, 19, 21, 22],
        "removed_pure_power_characters": [7, 9],
        "twenty_fifth_power_alphabet_nonzero": [1, 10, 100, 91],
        "computation": computation,
        "independent_python_censuses": python_censuses,
        "certificate": certificate,
        "certificate_verified": verified,
        "decision": {"status": "NO_RATIONAL_THREE_BLOCK_COMMON_ZEROS" if verified else "PROFILE_INVALID"},
        "execution_limits": {
            "primary_wall_seconds": 60,
            "primary_hard_address_space_bytes": None,
            "independent_python_censuses": "in_process_without_a_separate_hard_memory_or_wall_limit",
        },
        "interpretation": (
            "On either exact three-block support orbit over F_101, the twelve unchanged b=0 equations already force every nonzero 25th power to be constant on each active residue block. The old (7,0) and (9,0) pure-power rows then vanish automatically, so any augmented common zero would be one of the 16,500 original zeros and would require both selected orbit values to vanish. The certified coverage computation excludes that possibility; the augmented tuple therefore has no F_101-rational common zero on exact three-block support."
        ),
        "not_certified": [
            "absence of geometric three-block points over the algebraic closure of F_101",
            "the four-block or five-block rational support strata",
            "global dimension, regular-sequence status, smoothness, integrality, or a characteristic-zero lift",
            "a Brauer class, period, index bound, or period-index counterexample",
        ],
        "next_gate": {
            "feature": "level25_heisenberg_augmented_four_block_rational_profile",
            "requirement": (
                "Classify the twelve-row 25th-power alphabet on the unique exact four-block support orbit, incorporate both selected couplings, and replay all equations and Jacobian columns."
            ),
        },
    })


def _parse_four_block_transcript(stdout: str) -> dict[str, Any]:
    lines = stdout.strip().splitlines()
    if len(lines) != 9 or lines[0] != "REASONING_AUGMENTED_FOUR_BLOCK_PURE_POWER_V1" or lines[-1] != "REASONING_END":
        raise ScientificToolError("augmented four-block pure-power transcript has invalid framing")
    fields: dict[str, str] = {}
    for line in lines[1:-1]:
        if "=" not in line:
            raise ScientificToolError("augmented four-block pure-power transcript has an invalid field")
        name, value = line.split("=", 1)
        fields[name] = value
    integer_names = {
        "rank",
        "free_column_count",
        "alphabet_assignment_count",
        "solution_count",
        "block_constant_solution_count",
        "solution_checksum",
    }
    if set(fields) != integer_names | {"support_residues"}:
        raise ScientificToolError("augmented four-block pure-power transcript is incomplete")
    try:
        support = [int(item) for item in fields["support_residues"].split(",")]
        integers = {name: int(fields[name]) for name in integer_names}
    except ValueError as error:
        raise ScientificToolError("augmented four-block pure-power transcript has a nonintegral field") from error
    if support != [0, 1, 2, 3]:
        raise ScientificToolError("augmented four-block pure-power transcript has the wrong support")
    return {"support_residues": support, **integers}


def _run_four_block_program() -> dict[str, Any]:
    computation = _run_source_hashed_program(
        _FOUR_BLOCK_PROGRAM,
        label="augmented-four-block-pure-power",
        preserve_exact_transcript=True,
    )
    return {**computation, **_parse_four_block_transcript(computation["transcript"])}


def level25_heisenberg_augmented_four_block_alphabet_profile() -> dict[str, Any]:
    """Classify the unchanged-row alphabet on exact four-block support."""

    support = (0, 1, 2, 3)
    computation = _run_four_block_program()
    python_census = _python_twelve_row_census(support)
    compiled_census = {
        name: computation[name]
        for name in (
            "rank",
            "free_column_count",
            "alphabet_assignment_count",
            "solution_count",
            "block_constant_solution_count",
            "solution_checksum",
        )
    }
    cyclic_supports = {tuple(sorted((residue + shift) % PRIME for residue in support)) for shift in range(PRIME)}
    certificate = {
        "program_and_transcript_match_reviewed_computation": exact_execution_matches(
            computation, parser=_parse_four_block_transcript, expected_program=_FOUR_BLOCK_REVIEWED_PROGRAM_SHA256,
            expected_transcript=_FOUR_BLOCK_REVIEWED_TRANSCRIPT_SHA256,
        ),
        "compiled_and_python_censuses_agree": compiled_census == python_census,
        "support_represents_the_unique_cyclic_four_block_orbit": len(cyclic_supports) == PRIME
        and cyclic_supports == {tuple(residue for residue in range(PRIME) if residue != missing) for missing in range(PRIME)},
        "selected_augmentation_replaces_exactly_the_two_omitted_rows": {candidate[0] for candidate in _SELECTED_AUGMENTATION} == {7, 9},
        "twelve_unchanged_rows_force_block_constant_nonzero_powers": python_census["rank"] == 12
        and python_census["free_column_count"] == 8
        and python_census["alphabet_assignment_count"] == 65536
        and python_census["solution_count"] == python_census["block_constant_solution_count"] == 256,
    }
    request: dict[str, Any] = {}
    profile = {
        "schema": "reasoning.level25-heisenberg-augmented-four-block-alphabet-profile.v2",
        "request": request,
        "request_sha256": hashlib.sha256(canonical_json(request).encode()).hexdigest(),
        "support_residues": list(support),
        "cyclic_support_orbit": [list(row) for row in sorted(cyclic_supports)],
        "selected_augmentation": [list(candidate) for candidate in _SELECTED_AUGMENTATION],
        "remaining_pure_power_characters": [1, 8, 11, 12, 13, 14, 16, 17, 18, 19, 21, 22],
        "twenty_fifth_power_alphabet_nonzero": [1, 10, 100, 91],
        "computation": computation,
        "independent_python_census": python_census,
        "certificate": certificate,
        "certificate_verified": all(certificate.values()),
        "interpretation": (
            "On the unique exact four-block support orbit over F_101, the twelve unchanged b=0 equations force every nonzero 25th power to be constant on each active residue block. Thus any augmented common zero on this stratum lies in the block-constant 25th-power parameterization, but this receipt does not test the remaining eight pivot equations or the two selected b=0 couplings and does not exclude four-block zeros."
        ),
        "not_certified": [
            "absence of F_101-rational common zeros on exact four-block support",
            "absence of geometric four-block points over the algebraic closure of F_101",
            "the full-support rational stratum",
            "global dimension, regular-sequence status, smoothness, integrality, or a characteristic-zero lift",
            "a Brauer class, period, index bound, or period-index counterexample",
        ],
        "next_gate": {
            "feature": "level25_heisenberg_augmented_four_block_rational_profile",
            "requirement": (
                "Exhaust the block-constant four-block parameterization against the remaining eight pivot equations and both selected couplings, then replay all twenty-two equations and all twenty-five Jacobian columns at every survivor."
            ),
        },
    }

    return seal_profile_evidence(profile)


def _parse_phase_list(value: str, *, count: int, label: str) -> list[int]:
    try:
        parsed = [int(item) for item in value.split(",")]
    except ValueError as error:
        raise ScientificToolError(f"augmented four-block phase transcript has a nonintegral {label}") from error
    if len(parsed) != count:
        raise ScientificToolError(f"augmented four-block phase transcript has a malformed {label}")
    return parsed


def _parse_four_block_phase_transcript(stdout: str) -> dict[str, Any]:
    lines = stdout.strip().splitlines()
    if len(lines) != 16 or lines[0] != "REASONING_AUGMENTED_FOUR_BLOCK_PHASE_QUOTIENT_V1" or lines[-1] != "REASONING_END":
        raise ScientificToolError("augmented four-block phase transcript has invalid framing")
    fields: dict[str, str] = {}
    for line in lines[1:-1]:
        if "=" not in line:
            raise ScientificToolError("augmented four-block phase transcript has an invalid field")
        name, value = line.split("=", 1)
        fields[name] = value
    integer_names = {
        "old_block_degree_pattern_count",
        "old_equations_block_mu5_invariant",
        "difference_multiplier",
        "phase_tuple_count",
        "common_kernel_count",
        "diagonal_count",
        "relative_kernel_count",
        "normalized_phase_tuple_count",
        "normalized_orbit_count",
    }
    list_names = {
        "selected_term_counts",
        "q7_difference",
        "q9_difference",
        "invariant_histogram",
        "canonical_representatives",
    }
    if set(fields) != integer_names | list_names:
        raise ScientificToolError("augmented four-block phase transcript is incomplete")
    try:
        integers = {name: int(fields[name]) for name in integer_names}
    except ValueError as error:
        raise ScientificToolError("augmented four-block phase transcript has a nonintegral field") from error
    representatives = [
        _parse_phase_list(record, count=4, label="representative") for record in fields["canonical_representatives"].split(";")
    ]
    return {
        **integers,
        "selected_term_counts": _parse_phase_list(fields["selected_term_counts"], count=2, label="term count"),
        "q7_difference": _parse_phase_list(fields["q7_difference"], count=4, label="q7 difference"),
        "q9_difference": _parse_phase_list(fields["q9_difference"], count=4, label="q9 difference"),
        "invariant_histogram": _parse_phase_list(fields["invariant_histogram"], count=5, label="histogram"),
        "canonical_representatives": representatives,
    }


def _run_four_block_phase_program() -> dict[str, Any]:
    computation = _run_source_hashed_program(
        _FOUR_BLOCK_PHASE_PROGRAM,
        label="augmented-four-block-phase-quotient",
        preserve_exact_transcript=True,
    )
    return {**computation, **_parse_four_block_phase_transcript(computation["transcript"])}


def _selected_phase_term_vectors(candidate: tuple[int, int, int, int, int, int]) -> list[list[int]]:
    _first_character, second_character, first_offset, second_offset, middle_exponent, _kind = candidate
    assert second_character == 0
    support = {0, 1, 2, 3}
    vectors = []
    for start in range(PRIME):
        residues = (start, (start + first_offset) % PRIME, (start + second_offset) % PRIME)
        if not set(residues) <= support:
            continue
        vector = [0] * 4
        for residue, exponent in zip(residues, (1, middle_exponent, MODULUS - 1 - middle_exponent), strict=True):
            vector[residue] = (vector[residue] + exponent) % PRIME
        vectors.append(vector)
    return vectors


def level25_heisenberg_augmented_four_block_phase_quotient_profile() -> dict[str, Any]:
    """Certify the safe block-phase quotient for the four-block search."""

    computation = _run_four_block_phase_program()
    q9_terms = _selected_phase_term_vectors(_SELECTED_AUGMENTATION[0])
    q7_terms = _selected_phase_term_vectors(_SELECTED_AUGMENTATION[1])

    def difference(rows: list[list[int]]) -> list[int]:
        return [(first - second) % PRIME for first, second in zip(rows[0], rows[1], strict=True)]

    q7_difference = difference(q7_terms)
    q9_difference = difference(q9_terms)
    phases = list(product(range(PRIME), repeat=4))
    invariant_values = [sum(weight * phase for weight, phase in zip(q7_difference, row, strict=True)) % PRIME for row in phases]
    common_kernel = [
        row
        for row, value in zip(phases, invariant_values, strict=True)
        if value == 0 and sum(weight * phase for weight, phase in zip(q9_difference, row, strict=True)) % PRIME == 0
    ]
    normalized = [row for row in phases if row[0] == 0]
    histogram = [
        sum(sum(weight * phase for weight, phase in zip(q7_difference, row, strict=True)) % PRIME == value for row in normalized)
        for value in range(PRIME)
    ]
    representatives = [[0, 3 * value % PRIME, 0, 0] for value in range(PRIME)]
    independent = {
        "old_block_degree_patterns": [[25, 0], [20, 5], [5, 20], [15, 10], [10, 15], [5, 20]],
        "q7_term_vectors": q7_terms,
        "q9_term_vectors": q9_terms,
        "q7_difference": q7_difference,
        "q9_difference": q9_difference,
        "difference_multiplier": next(
            multiplier for multiplier in range(PRIME) if q9_difference == [multiplier * value % PRIME for value in q7_difference]
        ),
        "phase_tuple_count": len(phases),
        "common_kernel_count": len(common_kernel),
        "diagonal_count": sum(len(set(row)) == 1 for row in phases),
        "relative_kernel_count": len(common_kernel) // PRIME,
        "normalized_phase_tuple_count": len(normalized),
        "normalized_orbit_count": len(normalized) // (len(common_kernel) // PRIME),
        "invariant_histogram": histogram,
        "canonical_representatives": representatives,
    }
    certificate = {
        "program_and_transcript_match_reviewed_computation": exact_execution_matches(
            computation, parser=_parse_four_block_phase_transcript, expected_program=_FOUR_BLOCK_PHASE_REVIEWED_PROGRAM_SHA256,
            expected_transcript=_FOUR_BLOCK_PHASE_REVIEWED_TRANSCRIPT_SHA256,
        ),
        "old_equations_are_invariant_under_independent_block_mu5_scaling": computation["old_equations_block_mu5_invariant"] == 1
        and all(all(value % PRIME == 0 for value in row) for row in independent["old_block_degree_patterns"]),
        "both_selected_couplings_have_exactly_two_surviving_block_triples": computation["selected_term_counts"] == [2, 2]
        and len(q7_terms) == len(q9_terms) == 2,
        "selected_relative_phase_characters_are_proportional": q7_difference == [1, 2, 3, 4]
        and q9_difference == [3, 1, 4, 2]
        and independent["difference_multiplier"] == computation["difference_multiplier"] == 3,
        "compiled_and_python_phase_censuses_agree": computation["phase_tuple_count"] == independent["phase_tuple_count"]
        and computation["common_kernel_count"] == independent["common_kernel_count"]
        and computation["diagonal_count"] == independent["diagonal_count"]
        and computation["relative_kernel_count"] == independent["relative_kernel_count"]
        and computation["normalized_phase_tuple_count"] == independent["normalized_phase_tuple_count"]
        and computation["normalized_orbit_count"] == independent["normalized_orbit_count"]
        and computation["invariant_histogram"] == independent["invariant_histogram"]
        and computation["canonical_representatives"] == independent["canonical_representatives"],
        "five_representatives_exhaust_the_safe_phase_quotient": independent["relative_kernel_count"] == 25
        and independent["normalized_orbit_count"] == 5
        and histogram == [25] * PRIME
        and all(
            sum(weight * phase for weight, phase in zip(q7_difference, representative, strict=True)) % PRIME == value
            for value, representative in enumerate(representatives)
        ),
    }
    request: dict[str, Any] = {}
    profile = {
        "schema": "reasoning.level25-heisenberg-augmented-four-block-phase-quotient-profile.v2",
        "request": request,
        "request_sha256": hashlib.sha256(canonical_json(request).encode()).hexdigest(),
        "support_residues": [0, 1, 2, 3],
        "selected_augmentation": [list(candidate) for candidate in _SELECTED_AUGMENTATION],
        "computation": computation,
        "independent_python_replay": independent,
        "certificate": certificate,
        "certificate_verified": all(certificate.values()),
        "interpretation": (
            "After projectively normalizing the first block scale, the 125 relative blockwise mu_5 phases split into five orbits under a certified 25-element stabilizer of the augmented four-block zero equations. The five canonical phase representatives are exhaustive. For each old reduced scale triple m_1,m_2,m_3 in {0,...,19}, a complete augmented search may therefore use s_0=1, s_1=2^(m_1+20*l_1), s_2=2^m_2, s_3=2^m_3 for the five listed l_1 values, rather than all 125 phase triples."
        ),
        "not_certified": [
            "a common-zero count or Jacobian-rank census on exact four-block support",
            "absence of F_101-rational or geometric four-block common zeros",
            "the full-support rational stratum or any characteristic-zero lift",
            "a Brauer class, period, index bound, or period-index counterexample",
        ],
        "next_gate": {
            "feature": "level25_heisenberg_augmented_four_block_rational_profile",
            "requirement": (
                "Use exactly the five certified phase representatives in an exhaustive join for the remaining equations, then replay all equations and Jacobian columns."
            ),
        },
    }

    return seal_profile_evidence(profile)


def _parse_fourier_list(value: str, *, count: int, label: str) -> list[int]:
    try:
        parsed = [int(item) for item in value.split(",")]
    except ValueError as error:
        raise ScientificToolError(f"augmented four-block Fourier transcript has a nonintegral {label}") from error
    if len(parsed) != count:
        raise ScientificToolError(f"augmented four-block Fourier transcript has a malformed {label}")
    return parsed


def _parse_four_block_fourier_transcript(stdout: str) -> dict[str, Any]:
    lines = stdout.strip().splitlines()
    if len(lines) != 14 or lines[0] != "REASONING_AUGMENTED_FOUR_BLOCK_FOURIER_V1" or lines[-1] != "REASONING_END":
        raise ScientificToolError("augmented four-block Fourier transcript has invalid framing")
    fields: dict[str, str] = {}
    for line in lines[1:-1]:
        if "=" not in line:
            raise ScientificToolError("augmented four-block Fourier transcript has an invalid field")
        name, value = line.split("=", 1)
        fields[name] = value
    integer_names = {
        "shape_count",
        "zero_cyclic_sum_shape_count",
        "nonzero_bucket_minimum",
        "nonzero_bucket_maximum",
        "histogram_checksum",
        "fourier_rank",
        "admissible_shape_class_quadruple_count",
        "zero_line_shape_class_quadruple_count",
        "nonzero_line_shape_class_quadruple_count",
    }
    list_names = {
        "kernel_vector",
        "scale_twenty_fifth_power_classes",
        "cyclic_sum_histogram",
    }
    if set(fields) != integer_names | list_names:
        raise ScientificToolError("augmented four-block Fourier transcript is incomplete")
    try:
        integers = {name: int(fields[name]) for name in integer_names}
    except ValueError as error:
        raise ScientificToolError("augmented four-block Fourier transcript has a nonintegral field") from error
    return {
        **integers,
        "kernel_vector": _parse_fourier_list(fields["kernel_vector"], count=4, label="kernel vector"),
        "scale_twenty_fifth_power_classes": _parse_fourier_list(fields["scale_twenty_fifth_power_classes"], count=4, label="scale class"),
        "cyclic_sum_histogram": _parse_fourier_list(fields["cyclic_sum_histogram"], count=FINITE_FIELD_PRIME, label="histogram"),
    }


def _run_four_block_fourier_program() -> dict[str, Any]:
    computation = _run_source_hashed_program(
        _FOUR_BLOCK_FOURIER_PROGRAM,
        label="augmented-four-block-fourier",
        preserve_exact_transcript=True,
    )
    return {**computation, **_parse_four_block_fourier_transcript(computation["transcript"])}


def _python_four_block_fourier_census() -> dict[str, Any]:
    roots = [_finite_field_power(exponent) for exponent in range(MODULUS)]
    histogram = [0] * FINITE_FIELD_PRIME
    for code in range(MODULUS**4):
        work = code
        shape = [1]
        for _quotient in range(1, PRIME):
            shape.append(roots[work % MODULUS])
            work //= MODULUS
        cyclic_sum = (
            sum(pow(shape[quotient], MODULUS - 1, FINITE_FIELD_PRIME) * shape[(quotient + 1) % PRIME] for quotient in range(PRIME))
            % FINITE_FIELD_PRIME
        )
        histogram[cyclic_sum] += 1
    matrix = [[_finite_field_power(-PRIME * character_multiple * residue) for residue in range(4)] for character_multiple in (2, 3, 4)]
    reduced, pivots = _modular_rref(matrix)
    free_column = next(column for column in range(4) if column not in pivots)
    kernel = [0] * 4
    kernel[free_column] = 1
    for row, pivot in enumerate(pivots):
        kernel[pivot] = -reduced[row][free_column] % FINITE_FIELD_PRIME
    normalization = pow(kernel[0], FINITE_FIELD_PRIME - 2, FINITE_FIELD_PRIME)
    kernel = [value * normalization % FINITE_FIELD_PRIME for value in kernel]
    scale_classes = [pow(2, MODULUS * scale_class, FINITE_FIELD_PRIME) for scale_class in range(4)]
    admissible_count = 0
    zero_line_count = 0
    for first_value in range(FINITE_FIELD_PRIME):
        for classes in product(range(4), repeat=3):
            count = histogram[first_value]
            for residue, scale_class in enumerate(classes, start=1):
                target = (
                    first_value
                    * kernel[residue]
                    * pow(scale_classes[scale_class], FINITE_FIELD_PRIME - 2, FINITE_FIELD_PRIME)
                    % FINITE_FIELD_PRIME
                )
                count *= histogram[target]
            admissible_count += count
            if first_value == 0:
                zero_line_count += count
    checksum = 1469598103934665603
    for value, count in enumerate(histogram):
        for mixed in (value, count):
            for byte in range(8):
                checksum ^= (mixed >> (8 * byte)) & 255
                checksum = checksum * 1099511628211 & ((1 << 64) - 1)
    return {
        "shape_count": sum(histogram),
        "zero_cyclic_sum_shape_count": histogram[0],
        "nonzero_bucket_minimum": min(histogram[1:]),
        "nonzero_bucket_maximum": max(histogram[1:]),
        "histogram_checksum": checksum,
        "fourier_rank": len(pivots),
        "kernel_vector": kernel,
        "scale_twenty_fifth_power_classes": scale_classes,
        "admissible_shape_class_quadruple_count": admissible_count,
        "zero_line_shape_class_quadruple_count": zero_line_count,
        "nonzero_line_shape_class_quadruple_count": admissible_count - zero_line_count,
        "cyclic_sum_histogram": histogram,
    }


def level25_heisenberg_augmented_four_block_fourier_profile() -> dict[str, Any]:
    """Classify the coupled four-block Fourier rows over F_101."""

    alphabet = level25_heisenberg_augmented_four_block_alphabet_profile()
    alphabet_sha256 = hashlib.sha256(canonical_json(alphabet).encode()).hexdigest()
    computation = _run_four_block_fourier_program()
    independent = _python_four_block_fourier_census()
    compiled = {name: computation[name] for name in independent}
    certificate = {
        "four_block_alphabet_reduction_is_the_certified_prerequisite": reviewed_profile_matches(
            alphabet, _FOUR_BLOCK_ALPHABET_EVIDENCE_SHA256
        ),
        "program_and_transcript_match_reviewed_computation": exact_execution_matches(
            computation, parser=_parse_four_block_fourier_transcript, expected_program=_FOUR_BLOCK_FOURIER_REVIEWED_PROGRAM_SHA256,
            expected_transcript=_FOUR_BLOCK_FOURIER_REVIEWED_TRANSCRIPT_SHA256,
        ),
        "compiled_and_python_fourier_censuses_agree": compiled == independent,
        "all_normalized_mu25_shapes_are_bucketed": computation["shape_count"] == MODULUS**4
        and sum(computation["cyclic_sum_histogram"]) == MODULUS**4,
        "three_fourier_rows_have_a_one_dimensional_kernel": computation["fourier_rank"] == 3
        and computation["kernel_vector"][0] == 1
        and all(
            sum(_finite_field_power(-PRIME * character_multiple * residue) * computation["kernel_vector"][residue] for residue in range(4))
            % FINITE_FIELD_PRIME
            == 0
            for character_multiple in (2, 3, 4)
        ),
        "zero_and_nonzero_kernel_lines_are_both_retained": computation["zero_cyclic_sum_shape_count"] == 4420
        and computation["zero_line_shape_class_quadruple_count"] > 0
        and computation["nonzero_line_shape_class_quadruple_count"] > 0
        and computation["admissible_shape_class_quadruple_count"]
        == computation["zero_line_shape_class_quadruple_count"] + computation["nonzero_line_shape_class_quadruple_count"],
    }
    request: dict[str, Any] = {}
    profile = {
        "schema": "reasoning.level25-heisenberg-augmented-four-block-fourier-profile.v2",
        "request": request,
        "request_sha256": hashlib.sha256(canonical_json(request).encode()).hexdigest(),
        "alphabet_receipt_sha256": alphabet_sha256,
        "alphabet_evidence_sha256": alphabet["evidence_sha256"],
        "support_residues": [0, 1, 2, 3],
        "fourier_characters": [[10, 20], [15, 20], [20, 20]],
        "computation": computation,
        "independent_python_replay": independent,
        "certificate": certificate,
        "certificate_verified": all(certificate.values()),
        "interpretation": (
            "On exact four-block support over F_101, the character-10, -15, and -20 rows do not force each block cyclic sum to vanish. They force the four scale-weighted cyclic sums onto the displayed one-dimensional Fourier kernel. The zero line recovers the 4,420-shape bucket in every block, while the certified nonzero line contributes additional admissible shape-class quadruples that every exhaustive search must retain."
        ),
        "not_certified": [
            "a common-zero count or Jacobian-rank census after the other seven residual equations",
            "absence of F_101-rational or geometric four-block common zeros",
            "the full-support rational stratum or any characteristic-zero lift",
            "a Brauer class, period, index bound, or period-index counterexample",
        ],
        "next_gate": {
            "feature": "level25_heisenberg_augmented_four_block_bucket_join_profile",
            "requirement": (
                "Partition every block by the certified cyclic-sum target and scale class, retain both kernel-line strata, and measure an exact join for rows 0,5,6,7,8,Q7,Q9 across all five phase representatives."
            ),
        },
    }
    return seal_profile_evidence(profile)
