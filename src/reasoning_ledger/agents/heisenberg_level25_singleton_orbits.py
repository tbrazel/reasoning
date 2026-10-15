# ruff: noqa: E501
"""Exact fixed-stratum screen for three-residue singleton augmentation orbits."""

from __future__ import annotations

import hashlib
from collections import Counter
from typing import Any

from ..canonical import canonical_json
from .heisenberg_level25 import (
    FINITE_FIELD_PRIME,
    LEVEL25_HEISENBERG_BALANCED_CHARACTERS,
    MODULUS,
    PRIME,
    _add_term,
    _coordinate_restriction,
    _finite_field_power,
)
from .heisenberg_level25_three_block import _run_source_hashed_program
from .science_evidence import exact_execution_matches, seal_profile_evidence
from .scientific import ScientificToolError

_PROTOCOL_VERSION = 1
_REVIEWED_PROGRAM_SHA256 = "baa58ccad9e07cf202209f4dbafec5e5235642c10c7c4cb2df5123c125b0aab0"
_REVIEWED_TRANSCRIPT_SHA256 = "82a50ce5b7eb732ca6a14f5ee6ce23391954a246fd36fe522371b0be80a7b759"
_SINGLETON_EVIDENCE_SHA256 = "c30c8ec6ea77e72838fe365ebacec7df3b8226753ce6b557123c548feb56cf93"
_REVIEWED_SURVIVOR_CHECKSUM = 1659729786632400067

_PROGRAM = r"""#include <algorithm>
#include <array>
#include <cstdint>
#include <iostream>
#include <map>
#include <utility>

constexpr int P=101,N=25,ZETA=5;
constexpr std::array<std::pair<int,int>,22> CH{{
 {5,20},{10,20},{15,20},{20,20},{1,0},{2,15},{3,20},{4,15},{6,5},
 {7,0},{8,0},{9,0},{11,0},{12,0},{13,0},{14,0},{16,0},{17,0},
 {18,0},{19,0},{21,0},{22,0}}};

int pw(int b,int e){int r=1;while(e){if(e&1)r=r*b%P;b=b*b%P;e>>=1;}return r;}
int zpow(int e){e%=N;if(e<0)e+=N;return pw(ZETA,e);}
void mix(std::uint64_t& hash,std::uint64_t value){for(int byte=0;byte<8;++byte){hash^=(value>>(8*byte))&255;hash*=1099511628211ULL;}}

struct Coordinate {int variable,coefficient;bool active;};
Coordinate coordinate(int subgroup,int eigenspace,int index){
 if(subgroup==5){int residue=index%5;if(residue!=eigenspace)return {0,0,false};return {(index-residue)/5,1,true};}
 int residue=index%5,orbit=index/5;
 int ratio=zpow(5*eigenspace)*zpow(5*subgroup*residue)%P;
 return {residue,pw(ratio,orbit),true};
}

bool restriction_is_zero(int a,int d,int e,int beta,int subgroup,int eigenspace){
 std::map<std::array<int,5>,int> polynomial;
 for(int i=0;i<N;++i){
  std::array<Coordinate,3> entries{{coordinate(subgroup,eigenspace,i),coordinate(subgroup,eigenspace,(i+d)%N),coordinate(subgroup,eigenspace,(i+e)%N)}};
  if(!entries[0].active||!entries[1].active||!entries[2].active)continue;
  std::array<int,5> exponents{};std::array<int,3> powers{{1,beta,24-beta}};
  int coefficient=zpow(-a*i);
  for(int j=0;j<3;++j){exponents[entries[j].variable]+=powers[j];coefficient=coefficient*pw(entries[j].coefficient,powers[j])%P;}
  int value=(polynomial[exponents]+coefficient)%P;
  if(value)polynomial[exponents]=value;else polynomial.erase(exponents);
 }
 return polynomial.empty();
}

int pattern_index(int r,int s){if(r>s)std::swap(r,s);int index=0;for(int first=1;first<=4;++first)for(int second=first+1;second<=4;++second){if(first==r&&second==s)return index;++index;}return -1;}

int main(){
 std::array<std::uint64_t,22> row_counts{};std::array<std::uint64_t,6> pattern_counts{};
 std::array<std::array<int,3>,22> representatives{};std::array<bool,22> represented{};
 std::uint64_t raw=0,tests=0,survivors=0,checksum=1469598103934665603ULL;
 for(int d=1;d<N;++d)for(int e=1;e<N;++e){if(d%5==0||e%5==0||d%5==e%5)continue;for(int beta=1;beta<24;++beta){
  int weight=(beta*d+(24-beta)*e)%N,b=(N-weight)%N;
  for(int row=0;row<22;++row){auto [a,target_b]=CH[row];if(target_b!=b)continue;++raw;bool zero=true;
   for(int subgroup=0;subgroup<6;++subgroup)for(int eigenspace=0;eigenspace<5;++eigenspace){++tests;if(!restriction_is_zero(a,d,e,beta,subgroup,eigenspace))zero=false;}
   if(!zero)continue;++survivors;++row_counts[row];++pattern_counts[pattern_index(d%5,e%5)];
   if(!represented[row]){represented[row]=true;representatives[row]={d,e,beta};}
   mix(checksum,a);mix(checksum,b);mix(checksum,d);mix(checksum,e);mix(checksum,beta);
  }
 }}
 std::cout<<"REASONING_SINGLETON_ORBIT_FIXED_STRATUM_V1\n";
 std::cout<<"raw_candidate_count="<<raw<<"\nrestriction_test_count="<<tests<<"\nsurvivor_count="<<survivors<<"\nsurvivor_checksum="<<checksum<<"\n";
 std::cout<<"row_histogram=";bool first=true;for(int row=0;row<22;++row)if(row_counts[row]){if(!first)std::cout<<";";first=false;std::cout<<CH[row].first<<","<<CH[row].second<<","<<row_counts[row];}std::cout<<"\n";
 std::cout<<"residue_pattern_histogram=";first=true;int pattern=0;for(int r=1;r<=4;++r)for(int s=r+1;s<=4;++s){if(!first)std::cout<<";";first=false;std::cout<<r<<","<<s<<","<<pattern_counts[pattern++];}std::cout<<"\n";
 std::cout<<"row_representatives=";first=true;for(int row=0;row<22;++row)if(represented[row]){if(!first)std::cout<<";";first=false;std::cout<<CH[row].first<<","<<CH[row].second<<","<<representatives[row][0]<<","<<representatives[row][1]<<","<<representatives[row][2];}std::cout<<"\nREASONING_END\n";
}
"""


def _parse_records(value: str, width: int, label: str) -> list[list[int]]:
    if not value:
        return []
    try:
        records = [[int(item) for item in record.split(",")] for record in value.split(";")]
    except ValueError as error:
        raise ScientificToolError(f"singleton-orbit transcript has a nonintegral {label}") from error
    if any(len(record) != width for record in records):
        raise ScientificToolError(f"singleton-orbit transcript has a malformed {label}")
    return records


def _parse_transcript(stdout: str) -> dict[str, Any]:
    lines = stdout.strip().splitlines()
    if len(lines) < 3 or lines[0] != f"REASONING_SINGLETON_ORBIT_FIXED_STRATUM_V{_PROTOCOL_VERSION}" or lines[-1] != "REASONING_END":
        raise ScientificToolError("singleton-orbit transcript has invalid framing")
    fields: dict[str, str] = {}
    for line in lines[1:-1]:
        if "=" not in line:
            raise ScientificToolError("singleton-orbit transcript has an invalid field")
        name, value = line.split("=", 1)
        if name in fields:
            raise ScientificToolError("singleton-orbit transcript has duplicate fields")
        fields[name] = value
    expected = {"raw_candidate_count", "restriction_test_count", "survivor_count", "survivor_checksum", "row_histogram", "residue_pattern_histogram", "row_representatives"}
    if set(fields) != expected:
        raise ScientificToolError("singleton-orbit transcript is incomplete")
    try:
        integers = {name: int(fields[name]) for name in ("raw_candidate_count", "restriction_test_count", "survivor_count", "survivor_checksum")}
    except ValueError as error:
        raise ScientificToolError("singleton-orbit transcript has a nonintegral count") from error
    return {
        **integers,
        "row_histogram": _parse_records(fields["row_histogram"], 3, "row histogram"),
        "residue_pattern_histogram": _parse_records(fields["residue_pattern_histogram"], 3, "residue-pattern histogram"),
        "row_representatives": _parse_records(fields["row_representatives"], 5, "row representative"),
    }


def _restricted_orbit(
    first_character: int,
    offsets: tuple[int, int, int],
    exponents: tuple[int, int, int],
    coordinate_map: tuple[tuple[int, int] | None, ...],
) -> dict[tuple[int, ...], int]:
    polynomial: dict[tuple[int, ...], int] = {}
    for index in range(MODULUS):
        entries = [coordinate_map[(index + offset) % MODULUS] for offset in offsets]
        if any(entry is None for entry in entries):
            continue
        monomial_exponents = [0] * PRIME
        coefficient = _finite_field_power(-first_character * index)
        for entry, exponent in zip(entries, exponents, strict=True):
            assert entry is not None
            variable, restricted_coefficient = entry
            monomial_exponents[variable] += exponent
            coefficient = coefficient * pow(restricted_coefficient, exponent, FINITE_FIELD_PRIME) % FINITE_FIELD_PRIME
        _add_term(polynomial, tuple(monomial_exponents), coefficient)
    return polynomial


def _python_census() -> dict[str, Any]:
    coordinate_maps = [_coordinate_restriction(subgroup, eigenspace) for subgroup in range(PRIME + 1) for eigenspace in range(PRIME)]
    rows: Counter[tuple[int, int]] = Counter()
    patterns: Counter[tuple[int, int]] = Counter()
    representatives: dict[tuple[int, int], tuple[int, int, int]] = {}
    raw = 0
    survivors = 0
    checksum = 1469598103934665603

    def mix(value: int) -> None:
        nonlocal checksum
        for byte in range(8):
            checksum ^= (value >> (8 * byte)) & 255
            checksum = checksum * 1099511628211 & ((1 << 64) - 1)

    for first_offset in range(1, MODULUS):
        for second_offset in range(1, MODULUS):
            residues = first_offset % PRIME, second_offset % PRIME
            if 0 in residues or residues[0] == residues[1]:
                continue
            for middle_exponent in range(1, MODULUS - 1):
                clock_weight = (middle_exponent * first_offset + (MODULUS - 1 - middle_exponent) * second_offset) % MODULUS
                second_character = (-clock_weight) % MODULUS
                for character in LEVEL25_HEISENBERG_BALANCED_CHARACTERS:
                    if character[1] != second_character:
                        continue
                    raw += 1
                    offsets = (0, first_offset, second_offset)
                    exponents = (1, middle_exponent, MODULUS - 1 - middle_exponent)
                    if any(_restricted_orbit(character[0], offsets, exponents, coordinate_map) for coordinate_map in coordinate_maps):
                        continue
                    survivors += 1
                    rows[character] += 1
                    patterns[tuple(sorted(residues))] += 1
                    representatives.setdefault(character, (first_offset, second_offset, middle_exponent))
                    for value in (*character, first_offset, second_offset, middle_exponent):
                        mix(value)
    return {
        "raw_candidate_count": raw,
        "restriction_test_count": raw * len(coordinate_maps),
        "survivor_count": survivors,
        "survivor_checksum": checksum,
        "row_histogram": [[*character, rows[character]] for character in LEVEL25_HEISENBERG_BALANCED_CHARACTERS if rows[character]],
        "residue_pattern_histogram": [[*pattern, count] for pattern, count in sorted(patterns.items())],
        "row_representatives": [[*character, *representatives[character]] for character in LEVEL25_HEISENBERG_BALANCED_CHARACTERS if character in representatives],
    }


def _run_program() -> dict[str, Any]:
    computation = _run_source_hashed_program(_PROGRAM, label="singleton-orbit-fixed-stratum", preserve_exact_transcript=True)
    return {**computation, **_parse_transcript(computation["transcript"])}


def _candidate_from_record(record: list[int]) -> dict[str, Any]:
    first_character, second_character, first_offset, second_offset, middle_exponent = record
    return {
        "target_character": [first_character, second_character],
        "offsets": [0, first_offset, second_offset],
        "exponents": [1, middle_exponent, MODULUS - 1 - middle_exponent],
        "clock_weight_mod_25": (-second_character) % MODULUS,
        "clock_weight_identity": (
            f"{middle_exponent}*{first_offset} + "
            f"{MODULUS - 1 - middle_exponent}*{second_offset} = "
            f"{-second_character % MODULUS} mod 25"
        ),
    }


def level25_heisenberg_fixed_stratum_singleton_orbit_profile() -> dict[str, Any]:
    """Certify augmentation orbits vanishing on all thirty fixed P4 strata."""

    computation = _run_program()
    python_census = _python_census()
    compiled_census = {key: computation[key] for key in python_census}
    target_rows = [record[:2] for record in computation["row_histogram"]]
    certificate = {
        "program_transcript_and_checksum_match_reviewed_computation": (
            exact_execution_matches(computation, parser=_parse_transcript, expected_program=_REVIEWED_PROGRAM_SHA256, expected_transcript=_REVIEWED_TRANSCRIPT_SHA256)
            and computation["survivor_checksum"] == _REVIEWED_SURVIVOR_CHECKSUM
        ),
        "compiled_and_python_censuses_agree": compiled_census == python_census,
        "all_thirty_fixed_strata_are_tested_for_every_raw_candidate": computation["restriction_test_count"] == 30 * computation["raw_candidate_count"],
        "five_thousand_four_hundred_orbits_preserve_every_fixed_restriction": computation["survivor_count"] == 5400,
        "eighteen_balanced_target_rows_have_three_hundred_candidates_each": len(target_rows) == 18 and all(record[2] == 300 for record in computation["row_histogram"]),
        "all_six_three_residue_patterns_have_nine_hundred_candidates": len(computation["residue_pattern_histogram"]) == 6 and all(record[2] == 900 for record in computation["residue_pattern_histogram"]),
        "every_representative_has_a_singleton_first_factor": all(_candidate_from_record(record)["exponents"][0] == 1 for record in computation["row_representatives"]),
    }
    request: dict[str, Any] = {}
    verified = all(certificate.values())
    return seal_profile_evidence({
        "schema": "reasoning.level25-heisenberg-fixed-stratum-singleton-orbit-profile.v2",
        "request": request,
        "request_sha256": hashlib.sha256(canonical_json(request).encode()).hexdigest(),
        "finite_field": {"prime": FINITE_FIELD_PRIME, "primitive_twenty_fifth_root": 5},
        "orbit_recipe": (
            "sum_i zeta^(-a*i) x_i x_(i+d)^beta x_(i+e)^(24-beta), "
            "with d,e nonzero and distinct modulo 5 and 1 <= beta <= 23"
        ),
        "target_character_rule": "b = -(beta*d + (24-beta)*e) mod 25",
        "machine_checked_fixed_stratum_criterion": (
            "After substituting each of the thirty order-five fixed-P4 coordinate maps, collect all degree-25 monomials over F_101; the orbit is admitted exactly when every collected polynomial is empty."
        ),
        "computation": computation,
        "independent_python_census": python_census,
        "representative_candidates": [_candidate_from_record(record) for record in computation["row_representatives"]] if verified else [],
        "certificate": certificate,
        "certificate_verified": verified,
        "decision": {"status": "FIXED_STRATUM_CANDIDATES_CERTIFIED" if verified else "PROFILE_INVALID"},
        "execution_limits": {
            "primary_wall_seconds": 60,
            "primary_hard_address_space_bytes": None,
            "independent_python_census": "in_process_without_a_separate_hard_memory_or_wall_limit",
        },
        "interpretation": (
            "Exactly 5,400 bounded three-residue singleton monomial orbits can be added to the indicated balanced rows without changing any of the thirty certified order-five fixed-P4 restrictions over F_101. Their exponent-one factor can create a nonzero derivative in a residue block missing from a three-block point."
        ),
        "not_certified": [
            "that any candidate eliminates a known three-block common zero or raises its Jacobian rank",
            "that a simultaneous choice preserves cyclic-eigenpoint or global smoothness gates",
            "an algebraic-closure or characteristic-zero point classification",
            "a Brauer class, period, index bound, or period-index counterexample",
        ],
        "next_gate": {
            "feature": "level25_heisenberg_singleton_orbit_three_block_coverage_profile",
            "requirement": (
                "Evaluate the certified orbit library on all 16,500 known three-block zeros, solve a row-compatible coverage and inactive-gradient rank problem, then replay the fixed systems and global three-block searches for the selected augmentation."
            ),
        },
    })
