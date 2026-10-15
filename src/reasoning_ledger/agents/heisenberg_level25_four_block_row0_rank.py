# ruff: noqa: E501
"""Exact label/unary rank profile for the augmented four-block row-0 stage."""

from __future__ import annotations

import hashlib
from array import array
from typing import Any

from ..canonical import canonical_json
from .heisenberg_level25 import FINITE_FIELD_PRIME, MODULUS, PRIME, ROOT_OF_UNITY, _finite_field_power
from .heisenberg_level25_four_block_row0 import (
    _ROW0_FEASIBILITY_EVIDENCE_SHA256,
    level25_heisenberg_augmented_four_block_row0_feasibility_profile,
)
from .heisenberg_level25_three_block import _PROGRAM_PREFIX, _run_source_hashed_program
from .science_evidence import exact_execution_matches, reviewed_profile_matches, seal_profile_evidence
from .scientific import ScientificToolError

_PROTOCOL_VERSION = 1
_REVIEWED_PROGRAM_SHA256 = "3690222f6379b9927e62492b30742ecf04cd760c989d4ed92e78f355696127e6"
_REVIEWED_TRANSCRIPT_SHA256 = "c6bd22105af9263e111c8d6f182e65b73453dc50ab72ca94e67083e675139aaa"
_ROW0_LABEL_UNARY_RANK_EVIDENCE_SHA256 = "5e6264db7b66415510212f1c4468b3e85a5860c4b78642825f7e4b2dac29a100"
_FIELD_ORBIT_COUNT = 21
_LABEL_COUNT = 625
_UNARY_KEY_COUNT = FINITE_FIELD_PRIME**2
_BITSET_WORD_COUNT = (_UNARY_KEY_COUNT + 63) // 64
_COVERED_MATRIX_COUNT = 4 * FINITE_FIELD_PRIME
_RANK_WORD_OPERATION_CAP = 2**31

_PROGRAM = (
    _PROGRAM_PREFIX
    + r"""
#include <bit>
#include <string>

using u64=std::uint64_t;
constexpr int LABELS=625,ORBITS=21,ROW_KEYS=P*P,WORDS=(ROW_KEYS+63)/64;
constexpr u64 PIVOT_BIT_TEST_CAP=u64(ORBITS)*ROW_KEYS*LABELS;
constexpr u64 ELIMINATION_XOR_WORD_CAP=u64(ORBITS)*LABELS*(LABELS-1)/2*WORDS;
constexpr u64 ROW_SWAP_WORD_CAP=u64(ORBITS)*LABELS*WORDS;
constexpr u64 CHECKSUM_WORD_SCAN_COUNT=u64(ORBITS)*LABELS*WORDS;

struct ShapeRankData{std::uint8_t bucket,row6,row8;std::uint16_t label;};

std::size_t matrix_index(int orbit,int label,int word){return(std::size_t(orbit)*LABELS+label)*WORDS+word;}

struct RankResult{int rank=0;u64 pivot_checksum=1469598103934665603ULL;};

RankResult parity_rank(std::vector<u64>&matrices,int orbit){
 RankResult result;
 for(int column=0;column<ROW_KEYS&&result.rank<LABELS;++column){
  int word=column>>6,bit=column&63,pivot=result.rank;
  while(pivot<LABELS&&!((matrices[matrix_index(orbit,pivot,word)]>>bit)&1))++pivot;
  if(pivot==LABELS)continue;
  if(pivot!=result.rank)for(int current=0;current<WORDS;++current)std::swap(matrices[matrix_index(orbit,pivot,current)],matrices[matrix_index(orbit,result.rank,current)]);
  for(int row=result.rank+1;row<LABELS;++row)if((matrices[matrix_index(orbit,row,word)]>>bit)&1)
   for(int current=word;current<WORDS;++current)matrices[matrix_index(orbit,row,current)]^=matrices[matrix_index(orbit,result.rank,current)];
  mix(result.pivot_checksum,column);++result.rank;
 }
 return result;
}

int main(){
 std::array<int,5>mu5{};for(int exponent=0;exponent<5;++exponent)mu5[exponent]=pw(pw(ZETA,5),exponent);
 std::array<int,P>orbit{},representative{};orbit.fill(-1);int orbit_count=0;
 for(int value=0;value<P;++value)if(orbit[value]<0){representative[orbit_count]=value;if(!value)orbit[value]=orbit_count;else for(int scalar:mu5)orbit[value*scalar%P]=orbit_count;++orbit_count;}
 if(orbit_count!=ORBITS)return 41;
 std::vector<ShapeRankData>shapes(390625);std::array<u64,P>bucket_populations{};u64 residue_scaling_failures=0;
 for(std::uint32_t code=0;code<shapes.size();++code){
  std::uint32_t work=code;std::array<int,5>u{{1}};int label=0,place=1;
  for(int quotient=1;quotient<5;++quotient){int exponent=work%25;work/=25;u[quotient]=pw(ZETA,exponent);label+=(exponent%5)*place;place*=5;}
  std::array<int,5>inverse{};for(int quotient=0;quotient<5;++quotient)inverse[quotient]=pw(u[quotient],24);
  int bucket=0;for(int quotient=0;quotient<5;++quotient)bucket=(bucket+inverse[quotient]*u[(quotient+1)%5])%P;
  std::array<int,4>row6{},row8{};
  for(int residue=0;residue<4;++residue)for(int quotient=0;quotient<5;++quotient){int coordinate=residue+5*quotient;row6[residue]=(row6[residue]+coeff(3,coordinate)*inverse[quotient]%P*u[(quotient+1)%5])%P;row8[residue]=(row8[residue]+coeff(6,coordinate)*inverse[quotient]%P*u[(quotient+4)%5])%P;}
  for(int residue=0;residue<4;++residue)if(row6[residue]!=pw(ZETA,P-1-3*residue)*row6[0]%P||row8[residue]!=pw(ZETA,P-1-6*residue)*row8[0]%P)++residue_scaling_failures;
  shapes[code]=ShapeRankData{std::uint8_t(bucket),std::uint8_t(row6[0]),std::uint8_t(row8[0]),std::uint16_t(label)};++bucket_populations[bucket];
 }
 u64 transport_check_count=0,transport_failures=0;
 for(std::uint32_t code=0;code<shapes.size();++code){
  std::array<int,5>exponents{};std::uint32_t work=code;for(int quotient=1;quotient<5;++quotient){exponents[quotient]=work%25;work/=25;}
  for(int scalar_exponent=0;scalar_exponent<5;++scalar_exponent){
   std::uint32_t transformed_code=0,place=1;for(int quotient=1;quotient<5;++quotient){int exponent=(exponents[quotient]+5*scalar_exponent*quotient)%25;transformed_code+=place*exponent;place*=25;}
   const auto&source=shapes[code];const auto&target=shapes[transformed_code];int scalar=mu5[scalar_exponent];++transport_check_count;
   if(target.bucket!=scalar*source.bucket%P||target.label!=source.label||target.row6!=scalar*source.row6%P||target.row8!=pw(scalar,P-2)*source.row8%P)++transport_failures;
  }
 }
 std::vector<u64>matrices(std::size_t(ORBITS)*LABELS*WORDS);std::array<u64,ORBITS>representative_populations{};std::array<bool,ORBITS*LABELS>populated_labels{};u64 selected_shape_visits=0;
 for(const auto&shape:shapes){int current_orbit=orbit[shape.bucket];if(shape.bucket!=representative[current_orbit])continue;int column=shape.row6+P*shape.row8;matrices[matrix_index(current_orbit,shape.label,column>>6)]^=u64(1)<<(column&63);++representative_populations[current_orbit];populated_labels[current_orbit*LABELS+shape.label]=true;++selected_shape_visits;}
 std::array<int,ORBITS>support_counts{},unary_support_counts{},parity_nonzero_cell_counts{},ranks{};std::array<u64,ORBITS>matrix_checksums{},pivot_checksums{};
 for(int current_orbit=0;current_orbit<ORBITS;++current_orbit){u64 checksum=1469598103934665603ULL;std::array<u64,WORDS>unary_support{};for(int label=0;label<LABELS;++label){support_counts[current_orbit]+=populated_labels[current_orbit*LABELS+label];for(int word=0;word<WORDS;++word){u64 value=matrices[matrix_index(current_orbit,label,word)];unary_support[word]|=value;parity_nonzero_cell_counts[current_orbit]+=std::popcount(value);if(value){mix(checksum,label);mix(checksum,word);mix(checksum,value);}}}for(u64 value:unary_support)unary_support_counts[current_orbit]+=std::popcount(value);matrix_checksums[current_orbit]=checksum;auto rank_result=parity_rank(matrices,current_orbit);ranks[current_orbit]=rank_result.rank;pivot_checksums[current_orbit]=rank_result.pivot_checksum;}
 auto print21=[](const auto&values){for(int index=0;index<ORBITS;++index){if(index)std::cout<<",";std::cout<<values[index];}std::cout<<"\n";};
 std::cout<<"REASONING_AUGMENTED_FOUR_BLOCK_ROW0_LABEL_UNARY_RANK_V1\nshape_count="<<shapes.size()<<"\nlabel_count="<<LABELS<<"\nunary_key_count="<<ROW_KEYS<<"\nfield_orbit_count="<<orbit_count<<"\nprofiled_matrix_count="<<ORBITS<<"\ncovered_residue_bucket_matrix_count="<<4*P<<"\nbitset_word_count_per_row="<<WORDS<<"\nmatrix_storage_bytes="<<u64(ORBITS)*LABELS*WORDS*sizeof(u64)<<"\n";
 std::cout<<"mu5_values=";for(int index=0;index<5;++index){if(index)std::cout<<",";std::cout<<mu5[index];}std::cout<<"\nresidue_row6_scalars=";for(int residue=0;residue<4;++residue){if(residue)std::cout<<",";std::cout<<pw(ZETA,P-1-3*residue);}std::cout<<"\nresidue_row8_scalars=";for(int residue=0;residue<4;++residue){if(residue)std::cout<<",";std::cout<<pw(ZETA,P-1-6*residue);}std::cout<<"\n";
 std::cout<<"orbit_representatives=";print21(representative);std::cout<<"representative_populations=";print21(representative_populations);std::cout<<"populated_label_counts=";print21(support_counts);std::cout<<"populated_unary_key_counts=";print21(unary_support_counts);std::cout<<"parity_nonzero_cell_counts=";print21(parity_nonzero_cell_counts);std::cout<<"parity_ranks=";print21(ranks);std::cout<<"matrix_checksums=";print21(matrix_checksums);std::cout<<"pivot_checksums=";print21(pivot_checksums);
 std::cout<<"selected_shape_visit_count="<<selected_shape_visits<<"\ntransport_check_count="<<transport_check_count<<"\ntransport_failure_count="<<transport_failures<<"\nresidue_scaling_check_count="<<u64(shapes.size())*4<<"\nresidue_scaling_failure_count="<<residue_scaling_failures<<"\npivot_bit_test_cap="<<PIVOT_BIT_TEST_CAP<<"\nelimination_xor_word_cap="<<ELIMINATION_XOR_WORD_CAP<<"\nrow_swap_word_cap="<<ROW_SWAP_WORD_CAP<<"\nchecksum_word_scan_count="<<CHECKSUM_WORD_SCAN_COUNT<<"\nREASONING_END\n";
}
"""
)


def _parse_int_list(value: str, *, count: int, label: str) -> list[int]:
    try:
        parsed = [int(item) for item in value.split(",")]
    except ValueError as error:
        raise ScientificToolError(f"row-0 label/unary rank transcript has a nonintegral {label}") from error
    if len(parsed) != count:
        raise ScientificToolError(f"row-0 label/unary rank transcript has a malformed {label}")
    return parsed


def _parse_transcript(stdout: str) -> dict[str, Any]:
    lines = stdout.strip().splitlines()
    frame = f"REASONING_AUGMENTED_FOUR_BLOCK_ROW0_LABEL_UNARY_RANK_V{_PROTOCOL_VERSION}"
    if len(lines) < 3 or lines[0] != frame or lines[-1] != "REASONING_END":
        raise ScientificToolError("row-0 label/unary rank transcript has invalid framing")
    fields: dict[str, str] = {}
    for line in lines[1:-1]:
        if "=" not in line:
            raise ScientificToolError("row-0 label/unary rank transcript has an invalid field")
        name, value = line.split("=", 1)
        if name in fields:
            raise ScientificToolError("row-0 label/unary rank transcript has duplicate fields")
        fields[name] = value
    list_fields = {
        "mu5_values": 5,
        "residue_row6_scalars": 4,
        "residue_row8_scalars": 4,
        "orbit_representatives": _FIELD_ORBIT_COUNT,
        "representative_populations": _FIELD_ORBIT_COUNT,
        "populated_label_counts": _FIELD_ORBIT_COUNT,
        "populated_unary_key_counts": _FIELD_ORBIT_COUNT,
        "parity_nonzero_cell_counts": _FIELD_ORBIT_COUNT,
        "parity_ranks": _FIELD_ORBIT_COUNT,
        "matrix_checksums": _FIELD_ORBIT_COUNT,
        "pivot_checksums": _FIELD_ORBIT_COUNT,
    }
    integer_fields = {
        "shape_count",
        "label_count",
        "unary_key_count",
        "field_orbit_count",
        "profiled_matrix_count",
        "covered_residue_bucket_matrix_count",
        "bitset_word_count_per_row",
        "matrix_storage_bytes",
        "selected_shape_visit_count",
        "transport_check_count",
        "transport_failure_count",
        "residue_scaling_check_count",
        "residue_scaling_failure_count",
        "pivot_bit_test_cap",
        "elimination_xor_word_cap",
        "row_swap_word_cap",
        "checksum_word_scan_count",
    }
    if set(fields) != integer_fields | set(list_fields):
        raise ScientificToolError("row-0 label/unary rank transcript is incomplete")
    try:
        parsed: dict[str, Any] = {name: int(fields[name]) for name in integer_fields}
    except ValueError as error:
        raise ScientificToolError("row-0 label/unary rank transcript has a nonintegral field") from error
    parsed.update({name: _parse_int_list(fields[name], count=count, label=name) for name, count in list_fields.items()})
    return parsed


def _run_program() -> dict[str, Any]:
    computation = _run_source_hashed_program(_PROGRAM, label="augmented-four-block-row0-label-unary-rank", timeout_seconds=180, preserve_exact_transcript=True)
    return {**computation, **_parse_transcript(computation["transcript"])}


def _mix(checksum: int, value: int) -> int:
    for byte in range(8):
        checksum ^= (value >> (8 * byte)) & 255
        checksum = checksum * 1099511628211 & ((1 << 64) - 1)
    return checksum


def _parity_rank(rows: list[int]) -> tuple[int, int]:
    basis: dict[int, int] = {}
    for row in rows:
        while row:
            pivot = (row & -row).bit_length() - 1
            previous = basis.get(pivot)
            if previous is None:
                basis[pivot] = row
                break
            row ^= previous
    checksum = 1469598103934665603
    for pivot in sorted(basis):
        checksum = _mix(checksum, pivot)
    return len(basis), checksum


def _python_replay() -> dict[str, Any]:
    prime = FINITE_FIELD_PRIME
    roots = [pow(ROOT_OF_UNITY, exponent, prime) for exponent in range(MODULUS)]
    inverses = [pow(value, MODULUS - 1, prime) for value in roots]
    mu5 = [pow(pow(ROOT_OF_UNITY, 5, prime), exponent, prime) for exponent in range(5)]
    orbit = [-1] * prime
    representatives = []
    for value in range(prime):
        if orbit[value] >= 0:
            continue
        current_orbit = len(representatives)
        representatives.append(value)
        if value == 0:
            orbit[value] = current_orbit
        else:
            for scalar in mu5:
                orbit[value * scalar % prime] = current_orbit
    representative_index = {value: index for index, value in enumerate(representatives)}
    row6_coefficients = [_finite_field_power(-3 * PRIME * quotient) for quotient in range(PRIME)]
    row8_coefficients = [_finite_field_power(-6 * PRIME * quotient) for quotient in range(PRIME)]
    matrices = [[0] * _LABEL_COUNT for _ in representatives]
    populations = [0] * len(representatives)
    populated = [array("B", [0]) * _LABEL_COUNT for _ in representatives]
    for code in range(MODULUS ** (PRIME - 1)):
        work = code
        shape = [1]
        shape_inverses = [1]
        label = 0
        place = 1
        for _quotient in range(1, PRIME):
            exponent = work % MODULUS
            work //= MODULUS
            shape.append(roots[exponent])
            shape_inverses.append(inverses[exponent])
            label += exponent % PRIME * place
            place *= PRIME
        bucket = sum(shape_inverses[q] * shape[(q + 1) % PRIME] for q in range(PRIME)) % prime
        current_orbit = representative_index.get(bucket)
        if current_orbit is None:
            continue
        row6 = sum(row6_coefficients[q] * shape_inverses[q] * shape[(q + 1) % PRIME] for q in range(PRIME)) % prime
        row8 = sum(row8_coefficients[q] * shape_inverses[q] * shape[(q - 1) % PRIME] for q in range(PRIME)) % prime
        matrices[current_orbit][label] ^= 1 << (row6 + prime * row8)
        populations[current_orbit] += 1
        populated[current_orbit][label] = 1
    checksums = []
    unary_support_counts = []
    parity_nonzero_cell_counts = []
    for matrix in matrices:
        checksum = 1469598103934665603
        unary_support = 0
        nonzero_cells = 0
        for label, bits in enumerate(matrix):
            unary_support |= bits
            nonzero_cells += bits.bit_count()
            word = 0
            while bits:
                value = bits & ((1 << 64) - 1)
                if value:
                    checksum = _mix(checksum, label)
                    checksum = _mix(checksum, word)
                    checksum = _mix(checksum, value)
                bits >>= 64
                word += 1
        checksums.append(checksum)
        unary_support_counts.append(unary_support.bit_count())
        parity_nonzero_cell_counts.append(nonzero_cells)
    rank_results = [_parity_rank(matrix) for matrix in matrices]
    row6_scalars = [pow(ROOT_OF_UNITY, prime - 1 - 3 * residue, prime) for residue in range(4)]
    row8_scalars = [pow(ROOT_OF_UNITY, prime - 1 - 6 * residue, prime) for residue in range(4)]
    transport_factors_verified = all(
        pow(scalar, PRIME, prime) == 1
        and all(pow(scalar, quotient + 1, prime) * pow(scalar, prime - 2, prime) ** quotient % prime == scalar for quotient in range(PRIME - 1))
        and pow(scalar, -(PRIME - 1), prime) == scalar
        for scalar in mu5
    )
    return {
        "mu5_values": mu5,
        "residue_row6_scalars": row6_scalars,
        "residue_row8_scalars": row8_scalars,
        "orbit_representatives": representatives,
        "representative_populations": populations,
        "populated_label_counts": [sum(values) for values in populated],
        "populated_unary_key_counts": unary_support_counts,
        "parity_nonzero_cell_counts": parity_nonzero_cell_counts,
        "parity_ranks": [rank for rank, _checksum in rank_results],
        "matrix_checksums": checksums,
        "pivot_checksums": [checksum for _rank, checksum in rank_results],
        "selected_shape_visit_count": sum(populations),
        "transport_factors_verified": transport_factors_verified,
    }


def level25_heisenberg_augmented_four_block_row0_label_unary_rank_profile() -> dict[str, Any]:
    """Measure exact modular ranks coupling normalized labels to both unary keys."""

    prerequisite = level25_heisenberg_augmented_four_block_row0_feasibility_profile()
    prerequisite_sha256 = hashlib.sha256(canonical_json(prerequisite).encode()).hexdigest()
    computation = _run_program()
    independent = _python_replay()
    word_operation_bound = (
        computation["pivot_bit_test_cap"]
        + computation["elimination_xor_word_cap"]
        + computation["row_swap_word_cap"]
        + computation["checksum_word_scan_count"]
    )
    nonzero_full_rational_ranks = computation["parity_ranks"][1:] == computation["populated_label_counts"][1:]
    certificate = {
        "certified_row0_feasibility_profile_is_the_prerequisite": reviewed_profile_matches(prerequisite, _ROW0_FEASIBILITY_EVIDENCE_SHA256),
        "program_and_transcript_match_reviewed_computation": exact_execution_matches(computation, parser=_parse_transcript, expected_program=_REVIEWED_PROGRAM_SHA256, expected_transcript=_REVIEWED_TRANSCRIPT_SHA256),
        "mu5_transport_is_exhaustive": computation["field_orbit_count"] == _FIELD_ORBIT_COUNT and computation["transport_check_count"] == MODULUS ** (PRIME - 1) * PRIME and computation["transport_failure_count"] == 0 and independent["transport_factors_verified"],
        "residue_changes_only_permute_unary_columns": computation["residue_scaling_check_count"] == 4 * MODULUS ** (PRIME - 1) and computation["residue_scaling_failure_count"] == 0,
        "twenty_one_profiles_cover_every_residue_bucket_matrix": computation["profiled_matrix_count"] == _FIELD_ORBIT_COUNT and computation["covered_residue_bucket_matrix_count"] == _COVERED_MATRIX_COUNT,
        "compiled_and_python_parity_matrices_agree": all(computation[name] == independent[name] for name in independent if name != "transport_factors_verified"),
        "all_nonzero_bucket_orbits_have_full_rational_rank_on_populated_labels": nonzero_full_rational_ranks,
        "zero_bucket_rational_rank_has_the_certified_interval": computation["populated_label_counts"][0] == 351 and computation["parity_ranks"][0] == 346,
        "rank_word_work_fits_the_predeclared_cap": word_operation_bound < _RANK_WORD_OPERATION_CAP,
        "matrix_payload_fits_the_declared_storage_budget": computation["matrix_storage_bytes"] == _FIELD_ORBIT_COUNT * _LABEL_COUNT * _BITSET_WORD_COUNT * 8 < 32 * 1024**2,
    }
    request: dict[str, Any] = {}
    verified = all(certificate.values())
    return seal_profile_evidence({
        "schema": "reasoning.level25-heisenberg-augmented-four-block-row0-label-unary-rank-profile.v2",
        "request": request,
        "request_sha256": hashlib.sha256(canonical_json(request).encode()).hexdigest(),
        "row0_feasibility_receipt_sha256": prerequisite_sha256,
        "row0_feasibility_evidence_sha256": prerequisite["evidence_sha256"],
        "matrix_definition": "H[residue,bucket][normalized_mu5_label,(row6,row8)] reduced modulo 2",
        "computation": computation,
        "independent_python_replay": independent,
        "rational_rank_conclusions": {
            "zero_bucket_lower_bound": computation["parity_ranks"][0] if verified else None,
            "zero_bucket_upper_bound": computation["populated_label_counts"][0] if verified else None,
            "nonzero_bucket_orbit_ranks": computation["parity_ranks"][1:] if verified else None,
            "nonzero_bucket_orbit_ranks_are_exact": verified and nonzero_full_rational_ranks,
            "justification": (
                "Reduction modulo 2 cannot increase rational rank. Equality between modular rank and the number of populated rows therefore certifies exact full rational row rank for every nonzero bucket orbit. The deficient zero-bucket modular rank supplies only the stated lower bound, not an exact rational rank."
            ),
        },
        "engineering_caps": {
            "scope": "conservative modular-elimination word bound and C++ matrix payload; not process peak memory or Python replay limits",
            "rank_word_operations": _RANK_WORD_OPERATION_CAP,
            "conservative_rank_word_operation_bound": word_operation_bound,
            "resident_bytes": 32 * 1024**2,
        },
        "certificate": certificate,
        "certificate_verified": verified,
        "execution_limits": {
            "primary_wall_seconds": 180,
            "primary_hard_address_space_bytes": None,
            "independent_python_replay": "in_process_without_a_separate_hard_memory_or_wall_limit",
        },
        "decision": {
            "status": "ROW0_LABEL_UNARY_LOW_RANK_REJECTED" if verified else "PROFILE_INVALID",
            "reason": (
                "Every nonzero mu_5 bucket orbit has exact rational row rank equal to all 540--622 populated labels, while the zero bucket has rational rank at least 346 of 351 populated labels. Per-block linear separation of labels from the two unary keys therefore supplies no substantial compression on the exact histograms."
            ) if verified else "The mathematical certificate failed; no rational-rank or compression conclusion is authorized.",
        },
        "interpretation": (
            "Exact mu_5 transport and residue-wise unary scalings reduce all 404 block histogram matrices to 21 representatives. Independent C++ and Python parity elimination agrees on every representative. The result rejects only per-block low-rank compression between normalized labels and the joint rows-6/8 key; it does not reject translation-invariant edge convolution or another multi-block contraction."
        ),
        "not_certified": [
            "the exact rational rank of the zero-bucket label/unary matrix",
            "an all-fiber row-0 support census or common-zero count",
            "a lower bound for every possible multi-block or nonlinear contraction",
            "satisfaction of rows 5 and 7 or the selected Q7 and Q9 couplings",
            "absence of F_101-rational or geometric four-block common zeros",
            "the full-support rational stratum or any characteristic-zero lift",
            "a Brauer class, period, index bound, or period-index counterexample",
        ],
        "next_gate": {
            "feature": "level25_heisenberg_augmented_four_block_row0_group_convolution_design",
            "requirement": (
                "Price an exact multi-block contraction that retains the full label/unary histograms and exploits translation invariance of W(g,h) on (Z/5)^4, the independent phase averages, and the certified mu_5 bucket transport; reject it unless its complete all-fiber bound is below 2^31."
            ),
        },
    })
