# ruff: noqa: E501
"""Exact dependency and transport certificate for row-0 pair-table reuse."""

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
from .heisenberg_level25_four_block_row0_pair import (
    _ROW0_PAIR_EVIDENCE_SHA256,
    level25_heisenberg_augmented_four_block_row0_fixed_fiber_pair_calibration_profile,
)
from .heisenberg_level25_three_block import (
    _address_space_limited_command,
    _run_source_hashed_program,
)
from .science_evidence import exact_execution_matches, independent_replay_matches, reviewed_profile_matches, seal_profile_evidence
from .scientific import ScientificToolError

_PROTOCOL_VERSION = 1
_REVIEWED_PROGRAM_SHA256 = "9c25091f66fd470347deb0006c3a59b406dae61074d8d9b8b305201ba414f617"
_REVIEWED_TRANSCRIPT_SHA256 = "1a5fa047915b4de20a9cce7ecf06252f2d1c08c3a2b833bc010b4f8f8852d51a"
_ROW0_PAIR_REUSE_EVIDENCE_SHA256 = "f5cf3ac39d594a70d0f66e2449cfa4db541dc62a2093a29be1e4168ef75dac4a"
_REVIEWED_REPLAY_PROGRAM_SHA256 = "fb82448430c499fbe792e7109468f18b019e9e1cc6739f0ccced97030d28ce77"
_REVIEWED_REPLAY_TRANSCRIPT_SHA256 = "90cdd00bd0d1462ee6995e2351773169ceeb31fee07dd2775e4c63e28aadb883"
_RESIDENT_BYTE_CAP = 64 * 1024**2
_DIAGNOSTIC_CHARGE_BOUND = 300_000_000
_OPERATION_CAP = 2**31
_REPRESENTATIVE_COUNT = 21

_PROGRAM = r"""
#include <algorithm>
#include <array>
#include <cstdint>
#include <iostream>
#include <numeric>
#include <vector>

using u32=std::uint32_t;using u64=std::uint64_t;
constexpr int P=101,ZETA=5,OMEGA=95,LABELS=625;
constexpr u64 FNV_OFFSET=1469598103934665603ULL;
constexpr std::array<int,4>SCALE25{{1,10,100,91}};

int pw(int b,int e){int r=1;while(e){if(e&1)r=r*b%P;b=b*b%P;e>>=1;}return r;}
int coeff(int a,int i){int e=(-a*i)%25;if(e<0)e+=25;return pw(ZETA,e);}
void mix(u64&h,u64 v){for(int b=0;b<8;++b){h^=(v>>(8*b))&255;h*=1099511628211ULL;}}
struct Shape{std::uint8_t bucket;std::uint16_t label;std::array<std::uint8_t,4>b6,b8;};

std::array<int,4> exponents(u32 code){std::array<int,4>e{};for(int q=0;q<4;++q){e[q]=code%25;code/=25;}return e;}
u32 code_from(const std::array<int,4>&e){u32 code=0,place=1;for(int q=0;q<4;++q){code+=place*e[q];place*=25;}return code;}
int translated_label(int label,int a){int out=0,place=1;for(int q=1;q<5;++q){int digit=label%5;label/=5;out+=place*((digit+a*q)%5);place*=5;}return out;}

template<std::size_t S>void print_array(const std::array<u64,S>&v){for(std::size_t i=0;i<S;++i){if(i)std::cout<<",";std::cout<<v[i];}std::cout<<"\n";}
template<std::size_t S>void print_int_array(const std::array<int,S>&v){for(std::size_t i=0;i<S;++i){if(i)std::cout<<",";std::cout<<v[i];}std::cout<<"\n";}

int main(){
 std::vector<Shape>shapes(390625);std::array<u64,P>pop{};
 for(u32 code=0;code<shapes.size();++code){auto e=exponents(code);std::array<int,5>u{{1}},inv{};int label=0,place=1;for(int q=1;q<5;++q){u[q]=pw(ZETA,e[q-1]);label+=place*(e[q-1]%5);place*=5;}for(int q=0;q<5;++q)inv[q]=pw(u[q],24);int bucket=0;for(int q=0;q<5;++q)bucket=(bucket+inv[q]*u[(q+1)%5])%P;Shape s{};s.bucket=bucket;s.label=label;for(int r=0;r<4;++r)for(int q=0;q<5;++q){int i=r+5*q;s.b6[r]=(s.b6[r]+coeff(3,i)*inv[q]%P*u[(q+1)%5])%P;s.b8[r]=(s.b8[r]+coeff(6,i)*inv[q]%P*u[(q+4)%5])%P;}shapes[code]=s;++pop[bucket];
 }
 u64 population_checksum=FNV_OFFSET;for(int s=0;s<P;++s){mix(population_checksum,s);mix(population_checksum,pop[s]);}
 u64 mu_cases=0,mu_bucket_fail=0,mu_label_fail=0,mu_b6_fail=0,mu_b8_fail=0,other_cases=0,other_bucket_fail=0,other_label_fail=0,other_b6_fail=0,other_b8_fail=0;
 for(u32 code=0;code<shapes.size();++code){auto e=exponents(code);const auto&source=shapes[code];for(int a=0;a<25;++a){std::array<int,4>moved_e{};for(int q=1;q<5;++q)moved_e[q-1]=(e[q-1]+a*q)%25;const auto&target=shapes[code_from(moved_e)];int scalar=pw(ZETA,a),inverse=pw(scalar,P-2);bool subgroup=a%5==0;if(subgroup)++mu_cases;else ++other_cases;
   u64&bucket_fail=subgroup?mu_bucket_fail:other_bucket_fail;u64&label_fail=subgroup?mu_label_fail:other_label_fail;u64&b6_fail=subgroup?mu_b6_fail:other_b6_fail;u64&b8_fail=subgroup?mu_b8_fail:other_b8_fail;
   if(target.bucket!=scalar*source.bucket%P)++bucket_fail;if(target.label!=translated_label(source.label,a))++label_fail;for(int r=0;r<4;++r){if(target.b6[r]!=scalar*source.b6[r]%P)++b6_fail;if(target.b8[r]!=inverse*source.b8[r]%P)++b8_fail;}
  }}
 std::array<int,21>reps{};std::array<bool,P>seen{};int rep_count=0;for(int t=0;t<P;++t)if(!seen[t]){reps[rep_count++]=t;if(!t)seen[0]=true;else for(int a=0;a<5;++a)seen[t*pw(OMEGA,a)%P]=true;}if(rep_count!=21)return 81;
 std::array<u64,21>left_by_t{},right_by_t{};u64 left=0,right=0;
 for(int ri=0;ri<21;++ri){int t=reps[ri];for(int c=0;c<4;++c){int sb=96*t%P*pw(SCALE25[c],P-2)%P;left_by_t[ri]+=pop[t]*pop[sb];}for(int cc=0;cc<4;++cc)for(int cd=0;cd<4;++cd){int sc=31*t%P*pw(SCALE25[cc],P-2)%P,sd=17*t%P*pw(SCALE25[cd],P-2)%P;right_by_t[ri]+=pop[sc]*pop[sd];}left+=left_by_t[ri];right+=right_by_t[ri];}
 std::array<int,4>beta1{};std::array<int,16>beta3{};for(int c=0;c<4;++c)beta1[c]=pw(2,5*c);for(int cc=0;cc<4;++cc)for(int cd=0;cd<4;++cd)beta3[cc*4+cd]=87*pw(2,20*cc+5*cd)%P;
 std::array<int,4>row6_scalars{},row8_scalars{};for(int r=0;r<4;++r){row6_scalars[r]=pw(ZETA,(P-1-3*r)%(P-1));row8_scalars[r]=pw(ZETA,(P-1-6*r)%(P-1));}
 std::cout<<"REASONING_AUGMENTED_FOUR_BLOCK_ROW0_PAIR_REUSE_V1\nfield_prime="<<P<<"\nomega="<<OMEGA<<"\nshape_count="<<shapes.size()<<"\nlabel_count="<<LABELS<<"\nscale_representatives=";print_int_array(SCALE25);std::cout<<"beta1_values=";print_int_array(beta1);std::cout<<"beta3_values=";print_int_array(beta3);std::cout<<"row6_residue_scalars=";print_int_array(row6_scalars);std::cout<<"row8_residue_scalars=";print_int_array(row8_scalars);
 std::cout<<"bucket_population_minimum="<<*std::min_element(pop.begin(),pop.end())<<"\nbucket_population_maximum="<<*std::max_element(pop.begin(),pop.end())<<"\nbucket_population_sum="<<std::accumulate(pop.begin(),pop.end(),u64(0))<<"\nbucket_population_checksum="<<population_checksum<<"\nmu5_transport_case_count="<<mu_cases<<"\nmu5_bucket_failure_count="<<mu_bucket_fail<<"\nmu5_label_failure_count="<<mu_label_fail<<"\nmu5_row6_failure_count="<<mu_b6_fail<<"\nmu5_row8_failure_count="<<mu_b8_fail<<"\nnon_mu5_mu25_case_count="<<other_cases<<"\nnon_mu5_mu25_bucket_failure_count="<<other_bucket_fail<<"\nnon_mu5_mu25_label_failure_count="<<other_label_fail<<"\nnon_mu5_mu25_row6_failure_count="<<other_b6_fail<<"\nnon_mu5_mu25_row8_failure_count="<<other_b8_fail<<"\n";
 std::cout<<"t_orbit_representatives=";print_int_array(reps);std::cout<<"representative_fiber_count=1344\nleft_dependency_table_count=84\nright_dependency_table_count=336\nleft_copy_multiplicity=16\nright_copy_multiplicity=4\nleft_pair_visits_by_t=";print_array(left_by_t);std::cout<<"right_pair_visits_by_t=";print_array(right_by_t);std::cout<<"left_direct_pair_visit_count="<<left<<"\nright_direct_pair_visit_count="<<right<<"\nprimary_direct_pair_visit_count="<<left+right<<"\nprimary_plus_replay_direct_pair_visit_count="<<2*(left+right)<<"\nREASONING_END\n";
}
"""


def _parse_int_list(value: str, *, count: int, label: str) -> list[int]:
    try:
        parsed = [int(item) for item in value.split(",")]
    except ValueError as error:
        raise ScientificToolError(f"pair-reuse transcript has a nonintegral {label}") from error
    if len(parsed) != count:
        raise ScientificToolError(f"pair-reuse transcript has a malformed {label}")
    return parsed


def _parse_transcript(stdout: str) -> dict[str, Any]:
    lines = stdout.strip().splitlines()
    frame = f"REASONING_AUGMENTED_FOUR_BLOCK_ROW0_PAIR_REUSE_V{_PROTOCOL_VERSION}"
    if len(lines) < 3 or lines[0] != frame or lines[-1] != "REASONING_END":
        raise ScientificToolError("pair-reuse transcript has invalid framing")
    fields: dict[str, str] = {}
    for line in lines[1:-1]:
        if "=" not in line:
            raise ScientificToolError("pair-reuse transcript has an invalid field")
        name, value = line.split("=", 1)
        if name in fields:
            raise ScientificToolError("pair-reuse transcript has duplicate fields")
        fields[name] = value
    list_fields = {
        "scale_representatives": 4,
        "beta1_values": 4,
        "beta3_values": 16,
        "row6_residue_scalars": 4,
        "row8_residue_scalars": 4,
        "t_orbit_representatives": _REPRESENTATIVE_COUNT,
        "left_pair_visits_by_t": _REPRESENTATIVE_COUNT,
        "right_pair_visits_by_t": _REPRESENTATIVE_COUNT,
    }
    integer_fields = {
        "field_prime", "omega", "shape_count", "label_count",
        "bucket_population_minimum", "bucket_population_maximum", "bucket_population_sum", "bucket_population_checksum",
        "mu5_transport_case_count", "mu5_bucket_failure_count", "mu5_label_failure_count", "mu5_row6_failure_count", "mu5_row8_failure_count",
        "non_mu5_mu25_case_count", "non_mu5_mu25_bucket_failure_count", "non_mu5_mu25_label_failure_count", "non_mu5_mu25_row6_failure_count", "non_mu5_mu25_row8_failure_count",
        "representative_fiber_count", "left_dependency_table_count", "right_dependency_table_count", "left_copy_multiplicity", "right_copy_multiplicity",
        "left_direct_pair_visit_count", "right_direct_pair_visit_count", "primary_direct_pair_visit_count", "primary_plus_replay_direct_pair_visit_count",
    }
    if set(fields) != integer_fields | set(list_fields):
        raise ScientificToolError("pair-reuse transcript is incomplete")
    try:
        parsed: dict[str, Any] = {name: int(fields[name]) for name in integer_fields}
    except ValueError as error:
        raise ScientificToolError("pair-reuse transcript has a nonintegral field") from error
    parsed.update({name: _parse_int_list(fields[name], count=count, label=name) for name, count in list_fields.items()})
    return parsed


def _run_program() -> dict[str, Any]:
    computation = _run_source_hashed_program(_PROGRAM, label="augmented-four-block-row0-pair-reuse", timeout_seconds=180, runtime_address_space_bytes=_RESIDENT_BYTE_CAP, preserve_exact_transcript=True)
    return {**computation, **_parse_transcript(computation["transcript"])}


def _run_replay() -> dict[str, Any]:
    replay_path = Path(__file__).with_name("heisenberg_level25_four_block_row0_pair_reuse_replay.py")
    try:
        program = replay_path.read_bytes()
    except OSError as error:
        raise ScientificToolError(f"independent pair-reuse replay is unavailable: {error}") from error
    environment = {"HOME": "", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")}
    replay_python = shutil.which("python3", path="/usr/local/bin:/usr/bin:/bin") or sys.executable
    command = _address_space_limited_command([replay_python, "-I", "-S", str(replay_path)], _RESIDENT_BYTE_CAP)
    try:
        completed = subprocess.run(sandbox.wrap(command, ro=[replay_path], network=False), text=True, capture_output=True, timeout=180, env=environment, check=False)
    except subprocess.TimeoutExpired as error:
        raise ScientificToolError("independent pair-reuse replay exceeded the wall timeout") from error
    except OSError as error:
        raise ScientificToolError(f"independent pair-reuse replay could not start: {error}") from error
    if completed.returncode or completed.stderr.strip():
        detail = (completed.stderr.strip() or completed.stdout.strip() or "unknown replay failure")[:2000]
        raise ScientificToolError(f"independent pair-reuse replay failed: {detail}")
    if len(completed.stdout.encode()) > 2 * 1024**2:
        raise ScientificToolError("independent pair-reuse replay output exceeds 2 MiB")
    try:
        replay = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise ScientificToolError("independent pair-reuse replay returned invalid JSON") from error
    if not isinstance(replay, dict):
        raise ScientificToolError("independent pair-reuse replay returned a non-object")
    return {**replay, "program_sha256": hashlib.sha256(program).hexdigest(), "transcript_sha256": hashlib.sha256(completed.stdout.encode()).hexdigest(), "transcript": completed.stdout, "runtime_address_space_limit_bytes": _RESIDENT_BYTE_CAP, "interpreter": str(Path(replay_python).resolve())}


def level25_heisenberg_augmented_four_block_row0_pair_reuse_profile() -> dict[str, Any]:
    """Certify syntactic pair reuse and reject the resulting direct schedule."""

    prerequisite = level25_heisenberg_augmented_four_block_row0_fixed_fiber_pair_calibration_profile()
    prerequisite_verified = reviewed_profile_matches(prerequisite, _ROW0_PAIR_EVIDENCE_SHA256)
    prerequisite_evidence_sha256 = prerequisite["evidence_sha256"]
    prerequisite_sha256 = hashlib.sha256(canonical_json(prerequisite).encode()).hexdigest()
    del prerequisite
    computation = _run_program()
    replay = _run_replay()
    comparable = tuple(name for name in replay if name not in {"program_sha256", "transcript_sha256", "transcript", "runtime_address_space_limit_bytes", "interpreter"})
    certificate = {
        "pair_calibration_receipt_is_the_certified_prerequisite": prerequisite_verified,
        "program_and_transcript_match_reviewed_computation": exact_execution_matches(computation, parser=_parse_transcript, expected_program=_REVIEWED_PROGRAM_SHA256, expected_transcript=_REVIEWED_TRANSCRIPT_SHA256),
        "independent_program_and_transcript_match_reviewed_replay": independent_replay_matches(replay, expected_program=_REVIEWED_REPLAY_PROGRAM_SHA256, expected_transcript=_REVIEWED_REPLAY_TRANSCRIPT_SHA256),
        "compiled_and_independent_replays_agree": all(computation[name] == replay[name] for name in comparable),
        "source_scale_and_phase_coefficients_are_exact": computation["scale_representatives"] == [1, 10, 100, 91] and computation["beta1_values"] == [1, 32, 14, 44],
        "mu5_transport_is_exhaustive_and_exact": computation["mu5_transport_case_count"] == 5 * 390625 and all(computation[name] == 0 for name in ("mu5_bucket_failure_count", "mu5_label_failure_count", "mu5_row6_failure_count", "mu5_row8_failure_count")),
        "cyclic_wrap_rejects_every_other_mu25_scalar": computation["non_mu5_mu25_case_count"] == 20 * 390625 and computation["non_mu5_mu25_bucket_failure_count"] == 20 * 390625 and computation["non_mu5_mu25_label_failure_count"] == 0,
        "dependency_census_and_direct_price_are_exact": computation["left_dependency_table_count"] == 84 and computation["right_dependency_table_count"] == 336 and computation["primary_plus_replay_direct_pair_visit_count"] == 12_713_023_650,
        "diagnostic_fits_hard_caps_while_direct_schedule_does_not": _DIAGNOSTIC_CHARGE_BOUND < _OPERATION_CAP < computation["primary_direct_pair_visit_count"] and computation["runtime_address_space_limit_bytes"] == _RESIDENT_BYTE_CAP and replay["runtime_address_space_limit_bytes"] == _RESIDENT_BYTE_CAP,
    }
    request: dict[str, Any] = {}
    return seal_profile_evidence({
        "schema": "reasoning.level25-heisenberg-augmented-four-block-row0-pair-reuse-profile.v2",
        "request": request,
        "request_sha256": hashlib.sha256(canonical_json(request).encode()).hexdigest(),
        "pair_calibration_receipt_sha256": prerequisite_sha256,
        "pair_calibration_evidence_sha256": prerequisite_evidence_sha256,
        "dependency_theorem": {"left_key": ["t_orbit_representative", "c_B"], "right_key": ["t_orbit_representative", "c_C", "c_D"], "left_table_count": 84, "right_table_count": 336, "scope": "exact equality only for identical dependency keys; mu5 supplies key permutations within each represented t orbit"},
        "cyclic_wrap_obstruction": "For u'_q=lambda^q*u_q, the q=4 to q=0 forward term scales by lambda^-4 rather than lambda. Thus common shape transport preserves the required bucket and row-key scalings exactly when lambda^5=1, not for the other twenty elements of mu25.",
        "computation": computation,
        "independent_python_replay": replay,
        "engineering_caps": {"charged_operations": _OPERATION_CAP, "conservative_diagnostic_charge_bound": _DIAGNOSTIC_CHARGE_BOUND, "resident_bytes_per_implementation": _RESIDENT_BYTE_CAP, "hard_address_space_bytes_per_implementation": _RESIDENT_BYTE_CAP},
        "certificate": certificate,
        "certificate_verified": all(certificate.values()),
        "decision": {"status": "PAIR_DEPENDENCY_REUSE_CERTIFIED_DIRECT_SCHEDULE_OVER_CAP" if all(certificate.values()) else "PROFILE_INVALID", "reason": "The exact 84-left/336-right dependency cache reduces duplicate fiber tables, but even one direct primary construction requires 6,356,511,825 ordered pair visits before phase closure or a replay."},
        "interpretation": "The available source-level equality and mu5 permutation reuse are now certified, and the stronger common-mu25 transport is exhaustively rejected at the cyclic wrap. A different construction rather than further direct pair enumeration is required.",
        "not_certified": ["any accidental equality between different dependency keys", "a crossing B-C join or row-zero survivor count", "an algorithm-independent lower bound against all contractions", "rows 5 and 7 or the selected Q7 and Q9 couplings", "a common zero, geometric lift, Brauer class, index bound, or period-index counterexample"],
        "next_gate": {"feature": "level25_heisenberg_augmented_four_block_row0_pair_convolution_design", "requirement": "Design a transform or grouped-convolution construction of the lossless pair contribution that avoids one visit per shape pair and retains the boundary label, both unary sums, and edge coordinate through the crossing join."},
    })
