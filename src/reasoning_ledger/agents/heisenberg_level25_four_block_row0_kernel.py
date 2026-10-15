# ruff: noqa: E501
"""Exact Fourier-rank profile for the phase-averaged four-block row-0 kernel."""

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
from .heisenberg_level25_four_block_row0_rank import (
    _ROW0_LABEL_UNARY_RANK_EVIDENCE_SHA256,
    level25_heisenberg_augmented_four_block_row0_label_unary_rank_profile,
)
from .heisenberg_level25_three_block import (
    _address_space_limited_command,
    _run_source_hashed_program,
)
from .science_evidence import exact_execution_matches, independent_replay_matches, reviewed_profile_matches, seal_profile_evidence
from .scientific import ScientificToolError

_PROTOCOL_VERSION = 1
_REVIEWED_PROGRAM_SHA256 = "807824994a614ef193ce4718bbef0c83a612f95116fc1855e45222c7661ac592"
_REVIEWED_TRANSCRIPT_SHA256 = "0ab99cdfb5284274d209d538048033eec615a25630a2adc002c5899f1395406a"
_ROW0_KERNEL_RANK_EVIDENCE_SHA256 = "972baf6f162875276e315ffa92421714df8a10418acc8e62e819df787ca13f18"
_REVIEWED_REPLAY_PROGRAM_SHA256 = "2bd49404ad30858f81f723db992e4e265085591cf701374e8a798d595beebfa4"
_REVIEWED_REPLAY_TRANSCRIPT_SHA256 = "07b1484a526ddb14b7aa2126857fd95acb00bcfbfe45efd827d27c13fe50393a"
_FIELD_PRIME = 101
_OMEGA = 95
_LABEL_COUNT = 625
_ORBIT_REPRESENTATIVES = (0, 1, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12, 13, 18, 19, 22, 24, 25, 26, 38, 39)
_TRANSFORM_MODULI = (1449072215711168621, 1449072215711180741)
_ORDER_101_ROOTS = (1342006931089051155, 117958364458391118)
_ORDER_5_ROOTS = (1075073713960594284, 425568228032004829)
_DIAGNOSTIC_CHARGE_BOUND = 433796220
_OPERATION_CAP = 2**31
_RESIDENT_BYTE_CAP = 64 * 1024**2

_PROGRAM = r"""
#include <algorithm>
#include <array>
#include <cstdint>
#include <iostream>
#include <vector>

using u64=std::uint64_t;
using u128=unsigned __int128;
constexpr int P=101,OMEGA=95,LABELS=625,ORBITS=21;
constexpr std::array<int,21> REPS{{0,1,2,3,4,5,6,8,9,10,11,12,13,18,19,22,24,25,26,38,39}};
constexpr std::array<u64,2> MODQ{{1449072215711168621ULL,1449072215711180741ULL}};
constexpr std::array<u64,2> ROOT101{{1342006931089051155ULL,117958364458391118ULL}};
constexpr std::array<u64,2> ROOT5{{1075073713960594284ULL,425568228032004829ULL}};

u64 mulq(u64 a,u64 b,u64 q){return (u128)a*b%q;}
u64 addq(u64 a,u64 b,u64 q){a+=b;if(a>=q)a-=q;return a;}
u64 powq(u64 a,u64 e,u64 q){u64 out=1;while(e){if(e&1)out=mulq(out,a,q);a=mulq(a,a,q);e>>=1;}return out;}
bool prime64(u64 n){
 if(n<2)return false;
 for(u64 p:std::array<u64,12>{{2,3,5,7,11,13,17,19,23,29,31,37}}){if(n%p==0)return n==p;}
 u64 d=n-1,s=0;while(!(d&1)){d>>=1;++s;}
 for(u64 a:std::array<u64,7>{{2,325,9375,28178,450775,9780504,1795265022}}){
  if(a%n==0)continue;u64 x=powq(a%n,d,n);if(x==1||x==n-1)continue;bool composite=true;
  for(u64 r=1;r<s;++r){x=mulq(x,x,n);if(x==n-1){composite=false;break;}}
  if(composite)return false;
 }
 return true;
}
void mix(u64&checksum,u64 value){for(int byte=0;byte<8;++byte){checksum^=(value>>(8*byte))&255;checksum*=1099511628211ULL;}}
void mix_record(u64&checksum,u64 value,u64 index){mix(checksum,value);mix(checksum,index);}
std::array<int,4> unpack(int code){std::array<int,4>d{};for(int q=3;q>=0;--q){d[q]=code%5;code/=5;}return d;}
int fpow(int base,int exponent){int out=1;while(exponent){if(exponent&1)out=out*base%P;base=base*base%P;exponent>>=1;}return out;}

int main(){
 std::array<std::array<int,4>,LABELS>digits{};std::array<int,LABELS>wvalue{};
 std::array<int,5>omega{};for(int n=0;n<5;++n)omega[n]=fpow(OMEGA,n);
 for(int code=0;code<LABELS;++code){digits[code]=unpack(code);int value=1;for(int q=0;q<4;++q)value=(value+omega[digits[code][q]])%P;wvalue[code]=value;}
 std::array<int,P>orbit{};orbit.fill(-1);for(int oi=0;oi<ORBITS;++oi){int c=REPS[oi];if(!c)orbit[0]=oi;else for(int a=0;a<5;++a)orbit[c*omega[a]%P]=oi;}
 int orbit_failures=0;for(int c=0;c<P;++c)if(orbit[c]<0)++orbit_failures;
 std::array<u64,2>s_checksums{},factor_checksums{},direct_checksums{},diagnostic_checksums{};
 std::array<std::array<int,ORBITS>,2>support{};
 std::array<int,2>prime_checks{},congruence_checks{},root101_checks{},root5_checks{};
 u64 formula_failures=0,c0_failures=0,transport_failures=0,transport_checks=0;
 for(int mi=0;mi<2;++mi){
  u64 modulus=MODQ[mi],r101=ROOT101[mi],r5=ROOT5[mi];
  prime_checks[mi]=prime64(modulus);congruence_checks[mi]=modulus%505==1;
  root101_checks[mi]=r101!=1&&powq(r101,101,modulus)==1&&r101==powq(2,(modulus-1)/101,modulus);
  root5_checks[mi]=r5!=1&&powq(r5,5,modulus)==1&&r5==powq(2,(modulus-1)/5,modulus);
  if(!prime_checks[mi]||!congruence_checks[mi]||!root101_checks[mi]||!root5_checks[mi])return 51;
  std::array<u64,P>rpow{};std::array<u64,5>epow{};for(int j=0;j<P;++j)rpow[j]=powq(r101,j,modulus);for(int j=0;j<5;++j)epow[j]=powq(r5,j,modulus);
  std::vector<u64>characters(std::size_t(LABELS)*LABELS);
  for(int k=0;k<LABELS;++k)for(int delta=0;delta<LABELS;++delta){int dot=0;for(int q=0;q<4;++q)dot=(dot+digits[k][q]*digits[delta][q])%5;characters[std::size_t(k)*LABELS+delta]=epow[(5-dot)%5];}
  u64 s_checksum=14695981039346656037ULL,factor_checksum=14695981039346656037ULL,direct_checksum=14695981039346656037ULL,diagnostic_checksum=14695981039346656037ULL;
  std::array<std::array<u64,5>,ORBITS>svalues{};
  std::vector<u64>kernel(LABELS),factor(LABELS),direct(LABELS);
  std::vector<std::uint8_t>equalities(std::size_t(ORBITS)*LABELS);
  for(int oi=0;oi<ORBITS;++oi){
   int c=REPS[oi];
   for(int m=0;m<5;++m){u64 total=0;for(int r=0;r<5;++r)total=addq(total,mulq(rpow[c*omega[r]%P],epow[(5-(m*r)%5)%5],modulus),modulus);svalues[oi][m]=total;mix_record(s_checksum,total,oi*5+m);}
   for(int delta=0;delta<LABELS;++delta){u64 total=0;for(int n=0;n<5;++n)total=addq(total,rpow[c*omega[n]%P*wvalue[delta]%P],modulus);kernel[delta]=total;}
   for(int a=0;a<5;++a)for(int delta=0;delta<LABELS;++delta){u64 total=0;int moved=c*omega[a]%P;for(int n=0;n<5;++n)total=addq(total,rpow[moved*omega[n]%P*wvalue[delta]%P],modulus);++transport_checks;if(total!=kernel[delta])++transport_failures;}
   int current_support=0;
   for(int k=0;k<LABELS;++k){
    int sum=0;for(int q=0;q<4;++q)sum=(sum+digits[k][q])%5;
    u64 value=svalues[oi][(5-sum)%5];for(int q=0;q<4;++q)value=mulq(value,svalues[oi][digits[k][q]],modulus);factor[k]=value;if(value)++current_support;mix_record(factor_checksum,value,oi*LABELS+k);
    u64 total=0;const u64*row=&characters[std::size_t(k)*LABELS];for(int delta=0;delta<LABELS;++delta)total=addq(total,mulq(row[delta],kernel[delta],modulus),modulus);direct[k]=total;mix_record(direct_checksum,total,oi*LABELS+k);
    bool equal=value==total;equalities[oi*LABELS+k]=equal;if(!equal)++formula_failures;
    if(oi==0){u64 expected=k==0?3125%modulus:0;if(value!=expected||total!=expected)++c0_failures;}
   }
   support[mi][oi]=current_support;mix_record(diagnostic_checksum,current_support,oi);
  }
  for(std::size_t index=0;index<equalities.size();++index)mix_record(diagnostic_checksum,equalities[index],ORBITS+index);
  s_checksums[mi]=s_checksum;factor_checksums[mi]=factor_checksum;direct_checksums[mi]=direct_checksum;diagnostic_checksums[mi]=diagnostic_checksum;
 }
 auto print2=[](const auto&values){std::cout<<values[0]<<","<<values[1]<<"\n";};
 auto print21=[](const auto&values){for(int i=0;i<ORBITS;++i){if(i)std::cout<<",";std::cout<<values[i];}std::cout<<"\n";};
 std::cout<<"REASONING_AUGMENTED_FOUR_BLOCK_ROW0_KERNEL_RANK_V1\nfield_prime="<<P<<"\nomega="<<OMEGA<<"\nlabel_count="<<LABELS<<"\nfield_orbit_count="<<ORBITS<<"\n";
 std::cout<<"orbit_representatives=";print21(REPS);std::cout<<"transform_moduli=";print2(MODQ);std::cout<<"order_101_roots=";print2(ROOT101);std::cout<<"order_5_roots=";print2(ROOT5);
 std::cout<<"prime_checks=";print2(prime_checks);std::cout<<"congruence_checks=";print2(congruence_checks);std::cout<<"order_101_root_checks=";print2(root101_checks);std::cout<<"order_5_root_checks=";print2(root5_checks);
 std::cout<<"s_checksums=";print2(s_checksums);std::cout<<"factor_checksums=";print2(factor_checksums);std::cout<<"direct_checksums=";print2(direct_checksums);std::cout<<"diagnostic_checksums=";print2(diagnostic_checksums);
 std::cout<<"support_counts_modulus_1=";print21(support[0]);std::cout<<"support_counts_modulus_2=";print21(support[1]);
 std::cout<<"formula_match_count="<<u64(2)*ORBITS*LABELS-formula_failures<<"\nformula_failure_count="<<formula_failures<<"\nc0_failure_count="<<c0_failures<<"\norbit_partition_failure_count="<<orbit_failures<<"\n";
 std::cout<<"kernel_transport_check_count="<<transport_checks<<"\nkernel_transport_failure_count="<<transport_failures<<"\ns_term_count="<<u64(2)*ORBITS*5*5<<"\nkernel_term_count="<<u64(2)*ORBITS*LABELS*5<<"\nfactor_multiply_count="<<u64(2)*ORBITS*LABELS*4<<"\ndirect_transform_term_count="<<u64(2)*ORBITS*LABELS*LABELS<<"\ncharacter_matrix_payload_bytes="<<u64(LABELS)*LABELS*sizeof(u64)<<"\nREASONING_END\n";
}
"""


def _parse_int_list(value: str, *, count: int, label: str) -> list[int]:
    try:
        parsed = [int(item) for item in value.split(",")]
    except ValueError as error:
        raise ScientificToolError(f"row-0 kernel transcript has a nonintegral {label}") from error
    if len(parsed) != count:
        raise ScientificToolError(f"row-0 kernel transcript has a malformed {label}")
    return parsed


def _parse_transcript(stdout: str) -> dict[str, Any]:
    lines = stdout.strip().splitlines()
    frame = f"REASONING_AUGMENTED_FOUR_BLOCK_ROW0_KERNEL_RANK_V{_PROTOCOL_VERSION}"
    if len(lines) < 3 or lines[0] != frame or lines[-1] != "REASONING_END":
        raise ScientificToolError("row-0 kernel transcript has invalid framing")
    fields: dict[str, str] = {}
    for line in lines[1:-1]:
        if "=" not in line:
            raise ScientificToolError("row-0 kernel transcript has an invalid field")
        name, value = line.split("=", 1)
        if name in fields:
            raise ScientificToolError("row-0 kernel transcript has duplicate fields")
        fields[name] = value
    list_fields = {
        "orbit_representatives": len(_ORBIT_REPRESENTATIVES),
        "transform_moduli": 2,
        "order_101_roots": 2,
        "order_5_roots": 2,
        "prime_checks": 2,
        "congruence_checks": 2,
        "order_101_root_checks": 2,
        "order_5_root_checks": 2,
        "s_checksums": 2,
        "factor_checksums": 2,
        "direct_checksums": 2,
        "diagnostic_checksums": 2,
        "support_counts_modulus_1": len(_ORBIT_REPRESENTATIVES),
        "support_counts_modulus_2": len(_ORBIT_REPRESENTATIVES),
    }
    integer_fields = {
        "field_prime",
        "omega",
        "label_count",
        "field_orbit_count",
        "formula_match_count",
        "formula_failure_count",
        "c0_failure_count",
        "orbit_partition_failure_count",
        "kernel_transport_check_count",
        "kernel_transport_failure_count",
        "s_term_count",
        "kernel_term_count",
        "factor_multiply_count",
        "direct_transform_term_count",
        "character_matrix_payload_bytes",
    }
    if set(fields) != integer_fields | set(list_fields):
        raise ScientificToolError("row-0 kernel transcript is incomplete")
    try:
        parsed: dict[str, Any] = {name: int(fields[name]) for name in integer_fields}
    except ValueError as error:
        raise ScientificToolError("row-0 kernel transcript has a nonintegral field") from error
    parsed.update({name: _parse_int_list(fields[name], count=count, label=name) for name, count in list_fields.items()})
    return parsed


def _run_program() -> dict[str, Any]:
    computation = _run_source_hashed_program(
        _PROGRAM,
        label="augmented-four-block-row0-kernel-rank",
        timeout_seconds=180,
        runtime_address_space_bytes=_RESIDENT_BYTE_CAP,
        preserve_exact_transcript=True,
    )
    return {**computation, **_parse_transcript(computation["transcript"])}


def _run_limited_python_replay() -> dict[str, Any]:
    replay_path = Path(__file__).with_name(
        "heisenberg_level25_four_block_row0_kernel_replay.py"
    )
    try:
        program = replay_path.read_bytes()
    except OSError as error:
        raise ScientificToolError(f"independent row-0 kernel replay is unavailable: {error}") from error
    environment = {
        "HOME": "",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
    }
    replay_python = shutil.which(
        "python3",
        path="/usr/local/bin:/usr/bin:/bin",
    ) or sys.executable
    command = _address_space_limited_command(
        [replay_python, "-I", "-S", str(replay_path)],
        _RESIDENT_BYTE_CAP,
    )
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
        raise ScientificToolError(
            "independent row-0 kernel replay exceeded the 180-second wall timeout"
        ) from error
    except OSError as error:
        raise ScientificToolError(
            f"independent row-0 kernel replay could not start: {error}"
        ) from error
    if completed.returncode or completed.stderr.strip():
        detail = (
            completed.stderr.strip()
            or completed.stdout.strip()
            or "unknown replay failure"
        )[:2000]
        raise ScientificToolError(f"independent row-0 kernel replay failed: {detail}")
    if len(completed.stdout.encode()) > 2 * 1024**2:
        raise ScientificToolError("independent row-0 kernel replay output exceeds 2 MiB")
    try:
        replay = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise ScientificToolError(
            "independent row-0 kernel replay returned invalid JSON"
        ) from error
    if not isinstance(replay, dict):
        raise ScientificToolError("independent row-0 kernel replay returned a non-object")
    return {
        **replay,
        "program_sha256": hashlib.sha256(program).hexdigest(),
        "transcript_sha256": hashlib.sha256(completed.stdout.encode()).hexdigest(),
        "transcript": completed.stdout,
        "runtime_address_space_limit_bytes": _RESIDENT_BYTE_CAP,
        "interpreter": str(Path(replay_python).resolve()),
    }


def level25_heisenberg_augmented_four_block_row0_kernel_rank_profile() -> dict[str, Any]:
    """Measure every phase-averaged edge-kernel Fourier rank over two primes."""

    prerequisite = level25_heisenberg_augmented_four_block_row0_label_unary_rank_profile()
    prerequisite_verified = reviewed_profile_matches(prerequisite, _ROW0_LABEL_UNARY_RANK_EVIDENCE_SHA256)
    prerequisite_evidence_sha256 = prerequisite["evidence_sha256"]
    prerequisite_sha256 = hashlib.sha256(canonical_json(prerequisite).encode()).hexdigest()
    del prerequisite
    computation = _run_program()
    independent = _run_limited_python_replay()
    comparable_fields = (
        "prime_checks",
        "congruence_checks",
        "order_101_root_checks",
        "order_5_root_checks",
        "s_checksums",
        "factor_checksums",
        "direct_checksums",
        "diagnostic_checksums",
        "support_counts_modulus_1",
        "support_counts_modulus_2",
        "formula_match_count",
        "formula_failure_count",
        "c0_failure_count",
        "kernel_transport_check_count",
        "kernel_transport_failure_count",
    )
    expected_support = [1] + [625] * 20
    full_nonzero_rank = computation["support_counts_modulus_1"] == expected_support and computation["support_counts_modulus_2"] == expected_support
    certificate = {
        "label_unary_rank_receipt_is_the_certified_prerequisite": prerequisite_verified,
        "program_and_transcript_match_reviewed_computation": exact_execution_matches(computation, parser=_parse_transcript, expected_program=_REVIEWED_PROGRAM_SHA256, expected_transcript=_REVIEWED_TRANSCRIPT_SHA256),
        "independent_program_and_transcript_match_reviewed_replay": independent_replay_matches(independent, expected_program=_REVIEWED_REPLAY_PROGRAM_SHA256, expected_transcript=_REVIEWED_REPLAY_TRANSCRIPT_SHA256),
        "two_distinct_primes_and_exact_order_roots_are_verified": computation["transform_moduli"] == list(_TRANSFORM_MODULI) and computation["order_101_roots"] == list(_ORDER_101_ROOTS) and computation["order_5_roots"] == list(_ORDER_5_ROOTS) and all(computation[name] == [1, 1] for name in ("prime_checks", "congruence_checks", "order_101_root_checks", "order_5_root_checks")),
        "twenty_one_orbits_cover_every_phase_kernel": computation["orbit_representatives"] == list(_ORBIT_REPRESENTATIVES) and computation["orbit_partition_failure_count"] == 0 and computation["kernel_transport_check_count"] == 2 * 21 * 5 * 625 and computation["kernel_transport_failure_count"] == 0,
        "factorized_and_literal_fourier_transforms_agree": computation["formula_match_count"] == 2 * 21 * 625 and computation["formula_failure_count"] == 0 and computation["c0_failure_count"] == 0,
        "compiled_and_independent_python_replays_agree": all(computation[name] == independent[name] for name in comparable_fields),
        "all_nonzero_phase_kernel_orbits_have_full_modular_rank": full_nonzero_rank,
        "named_diagnostic_fits_the_predeclared_caps": _DIAGNOSTIC_CHARGE_BOUND < _OPERATION_CAP and computation["character_matrix_payload_bytes"] < _RESIDENT_BYTE_CAP and computation["runtime_address_space_limit_bytes"] == _RESIDENT_BYTE_CAP and independent["runtime_address_space_limit_bytes"] == _RESIDENT_BYTE_CAP,
    }
    request: dict[str, Any] = {}
    verified = all(certificate.values())
    return seal_profile_evidence({
        "schema": "reasoning.level25-heisenberg-augmented-four-block-row0-kernel-rank-profile.v2",
        "request": request,
        "request_sha256": hashlib.sha256(canonical_json(request).encode()).hexdigest(),
        "label_unary_rank_receipt_sha256": prerequisite_sha256,
        "label_unary_rank_evidence_sha256": prerequisite_evidence_sha256,
        "kernel_definition": {
            "group": "(Z/5)^4",
            "edge": "W(delta)=1+sum_{q=1}^4 95^delta_q in F_101",
            "phase_average": "K_c(delta)=sum_{n=0}^4 xi(c*95^n*W(delta))",
            "factorization": "hat(K_c)(k)=S_{-sum(k)}(c)*product_q S_{k_q}(c)",
            "one_dimensional_factor": "S_m(c)=sum_{r=0}^4 xi(c*95^r)*eta^(-m*r)",
        },
        "fiber_transport": {
            "representative_fiber_count": 64 * 21,
            "original_fiber_count": 64 * 101,
            "zero_t_representative_count": 64,
            "nonzero_t_representative_count": 64 * 20,
            "zero_t_multiplicity": 1,
            "nonzero_t_multiplicity": 5,
            "justification": (
                "The certified shape transport multiplies every bucket and row-6 key by s, every row-8 key by s^-1, and fixes normalized labels. Shifting all three independent edge phases by the exponent of s multiplies the complete row-0 expression, including 62*t, by s. Thus phase-summed counts are constant on each t*mu_5 orbit while the full weighted domain still has 6,464 fibers."
            ),
        },
        "computation": computation,
        "independent_python_replay": independent,
        "characteristic_zero_rank_conclusions": {
            "zero_character_rank": 1 if verified else None,
            "nonzero_character_orbit_ranks": computation["support_counts_modulus_1"][1:] if verified else None,
            "nonzero_character_orbit_ranks_are_exact": verified and full_nonzero_rank,
            "justification": (
                "The order-5 Fourier matrix is invertible modulo each transform prime, so modular convolution rank equals modular Fourier support. A full-rank modular reduction supplies a nonzero determinant minor and therefore certifies full characteristic-zero rank. Modular vanishing alone would not certify characteristic-zero vanishing."
            ),
        },
        "engineering_caps": {
            "scope": "named primary and independent replay, excluding prerequisite execution",
            "charged_operations": _OPERATION_CAP,
            "conservative_named_diagnostic_charge_bound": _DIAGNOSTIC_CHARGE_BOUND,
            "resident_bytes_per_implementation": _RESIDENT_BYTE_CAP,
            "hard_address_space_bytes_per_implementation": _RESIDENT_BYTE_CAP,
        },
        "certificate": certificate,
        "certificate_verified": verified,
        "decision": {
            "status": "ROW0_PHASE_KERNEL_FOURIER_LOW_RANK_REJECTED" if verified else "PROFILE_INVALID",
            "reason": (
                "The phase-averaged kernel has rank one at c=0 and exact full rank 625 for every nonzero c orbit. Translation invariance permits Fourier convolution but supplies no nonzero-character rank or support compression."
            ) if verified else "The mathematical certificate failed; no characteristic-zero rank or compression conclusion is authorized.",
        },
        "interpretation": (
            "The direct 625-by-625 transforms and the proved five-factor formula agree over two independently suitable primes, and an independent Python implementation reproduces every checksum and support count. Together with the earlier full populated label/unary ranks, this closes the two proposed linear low-rank shortcuts without ruling out a different nonlinear or multi-block algorithm."
        ),
        "not_certified": [
            "an all-fiber row-0 survivor census or common-zero count",
            "an algorithm-independent lower bound against every exact contraction",
            "satisfaction of rows 5 and 7 or the selected Q7 and Q9 couplings",
            "absence of F_101-rational or geometric four-block common zeros",
            "the full-support rational stratum or any characteristic-zero lift",
            "a Brauer class, period, index bound, or period-index counterexample",
        ],
        "next_gate": {
            "feature": "level25_heisenberg_augmented_four_block_row0_nonlinear_contraction_design",
            "requirement": (
                "Design and price a genuinely multi-block or nonlinear exact contraction that retains the full label/unary tensor and weighted 1,344-fiber quotient; do not reuse per-block label/unary rank or phase-kernel Fourier support as a compression."
            ),
        },
    })
