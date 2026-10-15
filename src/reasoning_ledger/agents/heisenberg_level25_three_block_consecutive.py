# ruff: noqa: E501
"""Exact F_101 consecutive three-block search for the five-pivot tuple."""

from __future__ import annotations

import hashlib
from typing import Any

from ..canonical import canonical_json
from .heisenberg_level25 import FINITE_FIELD_PRIME, ROOT_OF_UNITY
from .heisenberg_level25_alphabet import (
    _PURE_POWER_EVIDENCE_SHA256,
    level25_heisenberg_balanced_five_pivot_pure_power_alphabet_profile,
)
from .heisenberg_level25_three_block import (
    _PROGRAM_PREFIX,
    _TIMEOUT_SECONDS,
    _replay_witness,
    _run_source_hashed_program,
)
from .science_evidence import exact_execution_matches, reviewed_profile_matches, seal_profile_evidence
from .scientific import ScientificToolError

_PROTOCOL_VERSION = 1
_REVIEWED_PROGRAM_SHA256 = "437ecc8e77a7cd1557deecc8ba744970ac88eb6775d0bbcd6016fd8a15111987"
_REVIEWED_TRANSCRIPT_SHA256 = "65b939e8060854397a7f37ffd3b5054ce7380cd979b3f96f6405e873c23201bc"
_CONSECUTIVE_EVIDENCE_SHA256 = "bad2c7f1acb71276f0c807fefa5b8a72909127da123bdf210d5a6f74d9352b69"

_PROGRAM = (
    _PROGRAM_PREFIX
    + r"""
struct ShapeC {std::uint32_t code;std::array<int,5>u,p5,p20;std::array<int,3>b6,b8,b5,b7;bool cyclotomic;};
std::array<int,5> decode_c(std::uint32_t code){std::array<int,5>u{1};for(int q=1;q<5;++q){u[q]=pw(ZETA,code%25);code/=25;}return u;}
std::array<int,5> decode_c_exponents(std::uint32_t code){std::array<int,5>e{};for(int q=1;q<5;++q){e[q]=code%25;code/=25;}return e;}
bool cyclotomic_c(std::uint32_t code){auto e=decode_c_exponents(code);std::array<int,25>count{};for(int q=0;q<5;++q)++count[(e[(q+1)%5]-e[q]+25)%25];for(int k=0;k<25;++k)if(count[k]!=(k%5==0))return false;return true;}
int block_base_c(int row,int residue,const std::array<int,5>&u){std::array<int,N>x{};for(int q=0;q<5;++q)x[residue+5*q]=u[q];return form_value(row,x);}
int adjacent_c(const ShapeC&a,const ShapeC&b){int value=0;for(int q=0;q<5;++q)value=(value+a.p20[q]*b.p5[q])%P;return value;}
int nonadjacent_c(const ShapeC&a,const ShapeC&b,int character,int e1,int e5,int e2){int value=0;for(int q=0;q<5;++q){int term=coeff(character,5*q);term=term*pw(a.u[q],e1)%P;term=term*pw(a.u[(q+1)%5],e5)%P;term=term*pw(b.u[q],e2)%P;value=(value+term)%P;}return value;}
static std::uint32_t scale_masks[4][4][101][101];

int main(){
 std::vector<ShapeC>shapes;std::uint64_t cyclotomic_shape_count=0;
 for(std::uint32_t code=0;code<390625;++code){auto u=decode_c(code);int cyclic_sum=0;for(int q=0;q<5;++q)cyclic_sum=(cyclic_sum+pw(u[q],24)*u[(q+1)%5])%P;if(cyclic_sum)continue;ShapeC shape{};shape.code=code;shape.u=u;shape.cyclotomic=cyclotomic_c(code);if(shape.cyclotomic)++cyclotomic_shape_count;for(int q=0;q<5;++q){shape.p5[q]=pw(u[q],5);shape.p20[q]=pw(u[q],20);}for(int residue=0;residue<3;++residue){shape.b6[residue]=block_base_c(6,residue,u);shape.b8[residue]=block_base_c(8,residue,u);shape.b5[residue]=block_base_c(5,residue,u);shape.b7[residue]=block_base_c(7,residue,u);}shapes.push_back(shape);}
 int second_edge_coefficient=coeff(5,1);for(int c1=0;c1<4;++c1)for(int c2=0;c2<4;++c2)for(int first=0;first<P;++first)for(int second=0;second<P;++second){std::uint32_t mask=0;int bit=0;for(int m1=c1;m1<20;m1+=4)for(int m2=c2;m2<20;m2+=4,++bit){int s1=pw(GEN,m1),s2=pw(GEN,m2);if((pw(s1,5)*first+second_edge_coefficient*pw(s1,20)%P*pw(s2,5)%P*second)%P==0)mask|=1U<<bit;}scale_masks[c1][c2][first][second]=mask;}
 std::array<int,10201>head{};std::vector<int>next(shapes.size());std::uint64_t triples=0,scale_tests=0,zeros=0,cyclotomic_zeros=0,replay_failures=0,rank14=0,zero_checksum=1469598103934665603ULL;int minimum_rank=23,maximum_rank=-1;std::uint32_t first_a=0,first_b=0,first_d=0;int first_m1=-1,first_m2=-1;
 for(int ib=0;ib<(int)shapes.size();++ib)for(int c1=0;c1<4;++c1){head.fill(-1);int scale1_25=pw(GEN,25*c1);for(int ia=0;ia<(int)shapes.size();++ia){int key=(shapes[ia].b6[0]+scale1_25*shapes[ib].b6[1])%P+P*((shapes[ia].b8[0]+scale1_25*shapes[ib].b8[1])%P);next[ia]=head[key];head[key]=ia;}for(int c2=0;c2<4;++c2){int scale2_25=pw(GEN,25*c2);for(int id=0;id<(int)shapes.size();++id){int key=(P-scale2_25*shapes[id].b6[2]%P)%P+P*((P-scale2_25*shapes[id].b8[2]%P)%P);for(int ia=head[key];ia>=0;ia=next[ia]){++triples;int first_adjacent=adjacent_c(shapes[ia],shapes[ib]),second_adjacent=adjacent_c(shapes[ib],shapes[id]);std::uint32_t mask=scale_masks[c1][c2][first_adjacent][second_adjacent];if(!mask)continue;int base5=(shapes[ia].b5[0]+scale1_25*shapes[ib].b5[1]+scale2_25*shapes[id].b5[2])%P,base7=(shapes[ia].b7[0]+scale1_25*shapes[ib].b7[1]+scale2_25*shapes[id].b7[2])%P;int pivot[4]{nonadjacent_c(shapes[ia],shapes[id],2,1,4,20),nonadjacent_c(shapes[ia],shapes[id],2,2,13,10),nonadjacent_c(shapes[ia],shapes[id],2,4,6,15),nonadjacent_c(shapes[ia],shapes[id],4,1,4,20)};int bit=0;for(int m1=c1;m1<20;m1+=4)for(int m2=c2;m2<20;m2+=4,++bit){if(!(mask&(1U<<bit)))continue;++scale_tests;int s2=pw(GEN,m2);int row5=(base5+pivot[0]*pw(s2,20)+pivot[1]*pw(s2,10)+pivot[2]*pw(s2,15))%P,row7=(base7+pivot[3]*pw(s2,20))%P;if(row5||row7)continue;std::array<int,N>x{};int s1=pw(GEN,m1);for(int q=0;q<5;++q){x[5*q]=shapes[ia].u[q];x[1+5*q]=s1*shapes[ib].u[q]%P;x[2+5*q]=s2*shapes[id].u[q]%P;}bool replay=true;for(int row=0;row<22;++row)if(form_value(row,x)){replay=false;break;}if(!replay){++replay_failures;continue;}int rank=jac_rank(x);if(!zeros){first_a=shapes[ia].code;first_b=shapes[ib].code;first_d=shapes[id].code;first_m1=m1;first_m2=m2;}++zeros;if(shapes[ia].cyclotomic&&shapes[ib].cyclotomic&&shapes[id].cyclotomic)++cyclotomic_zeros;if(rank==14)++rank14;minimum_rank=std::min(minimum_rank,rank);maximum_rank=std::max(maximum_rank,rank);mix(zero_checksum,shapes[ia].code);mix(zero_checksum,shapes[ib].code);mix(zero_checksum,shapes[id].code);mix(zero_checksum,m1);mix(zero_checksum,m2);mix(zero_checksum,rank);}}}}}
 std::cout<<"REASONING_THREE_BLOCK_CONSECUTIVE_RATIONAL_V1\n";
 std::cout<<"support_residues=0,1,2\nnormalized_shape_count="<<shapes.size()<<"\ncyclotomic_shape_count="<<cyclotomic_shape_count<<"\nadditive_shape_scale_class_triple_count="<<triples<<"\nadjacent_scale_solution_count="<<scale_tests<<"\nfull_scale_test_count="<<scale_tests<<"\norbit_zero_count="<<zeros<<"\ncyclotomic_orbit_zero_count="<<cyclotomic_zeros<<"\nzero_checksum="<<zero_checksum<<"\nfull_replay_failure_count="<<replay_failures<<"\nrank_14_count="<<rank14<<"\nminimum_rank="<<(zeros?minimum_rank:-1)<<"\nmaximum_rank="<<maximum_rank<<"\nfirst_witness_codes="<<first_a<<","<<first_b<<","<<first_d<<"\nfirst_witness_scale_exponents="<<first_m1<<","<<first_m2<<"\nREASONING_END\n";
}
"""
)


def _parse_csv_ints(value: str, count: int) -> list[int]:
    try:
        parsed = [int(item) for item in value.split(",")]
    except ValueError as error:
        raise ScientificToolError("consecutive three-block transcript has a nonintegral list") from error
    if len(parsed) != count:
        raise ScientificToolError("consecutive three-block transcript has a malformed list")
    return parsed


def _parse_transcript(stdout: str) -> dict[str, Any]:
    lines = stdout.strip().splitlines()
    frame = f"REASONING_THREE_BLOCK_CONSECUTIVE_RATIONAL_V{_PROTOCOL_VERSION}"
    if len(lines) < 3 or lines[0] != frame or lines[-1] != "REASONING_END":
        raise ScientificToolError("consecutive three-block transcript has invalid framing")
    fields: dict[str, str] = {}
    for line in lines[1:-1]:
        if "=" not in line:
            raise ScientificToolError("consecutive three-block transcript has an invalid field")
        name, value = line.split("=", 1)
        if name in fields:
            raise ScientificToolError("consecutive three-block transcript has duplicate fields")
        fields[name] = value
    integer_names = {
        "normalized_shape_count",
        "cyclotomic_shape_count",
        "additive_shape_scale_class_triple_count",
        "adjacent_scale_solution_count",
        "full_scale_test_count",
        "orbit_zero_count",
        "cyclotomic_orbit_zero_count",
        "zero_checksum",
        "full_replay_failure_count",
        "rank_14_count",
        "minimum_rank",
        "maximum_rank",
    }
    expected = integer_names | {"support_residues", "first_witness_codes", "first_witness_scale_exponents"}
    if set(fields) != expected:
        raise ScientificToolError("consecutive three-block transcript is incomplete")
    try:
        integers = {name: int(fields[name]) for name in integer_names}
    except ValueError as error:
        raise ScientificToolError("consecutive three-block transcript has a nonintegral field") from error
    return {
        **integers,
        "support_residues": _parse_csv_ints(fields["support_residues"], 3),
        "first_witness_codes": _parse_csv_ints(fields["first_witness_codes"], 3),
        "first_witness_scale_exponents": _parse_csv_ints(fields["first_witness_scale_exponents"], 2),
    }


def _run_program() -> dict[str, Any]:
    computation = _run_source_hashed_program(_PROGRAM, label="three-block-consecutive", preserve_exact_transcript=True)
    return {**computation, **_parse_transcript(computation["transcript"])}


def level25_heisenberg_balanced_five_pivot_consecutive_three_block_rational_profile() -> dict[str, Any]:
    """Enumerate F_101 zeros on the {0,1,2} three-block support class."""

    prerequisite = level25_heisenberg_balanced_five_pivot_pure_power_alphabet_profile()
    prerequisite_sha256 = hashlib.sha256(canonical_json(prerequisite).encode()).hexdigest()
    computation = _run_program()
    witness = _replay_witness(computation)
    certificate = {
        "program_transcript_and_zero_checksum_match_reviewed_computation": (
            exact_execution_matches(
                computation,
                parser=_parse_transcript,
                expected_program=_REVIEWED_PROGRAM_SHA256,
                expected_transcript=_REVIEWED_TRANSCRIPT_SHA256,
            )
            and computation["zero_checksum"] == 8483181085099946785
        ),
        "pure_power_prerequisite_verified": reviewed_profile_matches(prerequisite, _PURE_POWER_EVIDENCE_SHA256),
        "support_is_the_consecutive_three_block_orbit": computation["support_residues"] == [0, 1, 2],
        "all_reduced_shapes_and_relative_scales_are_exhausted": (
            computation["normalized_shape_count"] == 4420
            and computation["additive_shape_scale_class_triple_count"] == 135545985
            and computation["adjacent_scale_solution_count"] == computation["full_scale_test_count"] == 40956925
        ),
        "every_compiled_survivor_passes_full_equation_replay": computation["full_replay_failure_count"] == 0,
        "all_survivors_have_rank_fourteen": (
            computation["orbit_zero_count"] == 4100
            and computation["rank_14_count"] == computation["orbit_zero_count"]
            and computation["minimum_rank"] == computation["maximum_rank"] == 14
        ),
        "python_replays_the_first_witness": witness["nonzero_form_count"] == 0 and witness["jacobian_rank"] == 14,
        "no_survivor_uses_three_cyclotomic_block_shapes": (
            computation["cyclotomic_shape_count"] == 120 and computation["cyclotomic_orbit_zero_count"] == 0
        ),
    }
    request: dict[str, Any] = {}
    verified = all(certificate.values())
    return seal_profile_evidence({
        "schema": "reasoning.level25-heisenberg-balanced-five-pivot-consecutive-three-block-rational-profile.v2",
        "request": request,
        "request_sha256": hashlib.sha256(canonical_json(request).encode()).hexdigest(),
        "finite_field": {"prime": FINITE_FIELD_PRIME, "primitive_twenty_fifth_root": ROOT_OF_UNITY},
        "pure_power_prerequisite_receipt_sha256": prerequisite_sha256,
        "pure_power_prerequisite_evidence_sha256": prerequisite["evidence_sha256"],
        "computation": computation,
        "first_witness_replay": witness,
        "execution_limits": {
            "primary_wall_seconds": _TIMEOUT_SECONDS,
            "primary_hard_address_space_bytes": None,
            "python_witness_replay": "in_process_without_a_separate_hard_memory_or_wall_limit",
        },
        "certificate": certificate,
        "certificate_verified": verified,
        "decision": {"status": "F101_CONSECUTIVE_THREE_BLOCK_ZEROS_CLASSIFIED" if verified else "PROFILE_INVALID"},
        "interpretation": (
            "On the consecutive three-residue-block support orbit, the explicit five-pivot tuple has 4,100 F_101-rational representatives modulo the stated projective and independent mu_5 normalizations. Every one has Jacobian rank 14, so the special fiber is not a smooth two-dimensional complete intersection at these points."
        ),
        "cyclotomic_lift_check": (
            "None of the modular common zeros uses three of the 120 block shapes whose cyclic shape sum vanishes as a cyclotomic identity. Thus none lifts inside the same mu_25-shape, mu_100-scale torsion parameterization."
        ),
        "not_certified": [
            "a characteristic-zero singularity or common zero",
            "absence or presence of points with non-block-constant 25th powers over the algebraic closure",
            "the four-block or five-block support strata",
            "a Brauer class, period, index bound, or period-index counterexample",
        ],
        "next_gate": {
            "feature": "level25_heisenberg_balanced_five_pivot_four_block_rational_profile",
            "requirement": "Exhaust the unique cyclic four-block support class with full equation and all-column Jacobian replay, then treat full support.",
        },
    })
