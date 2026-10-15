# ruff: noqa: E501
"""Exact Fourier ranks of the lossless row-0 edge-value indicator kernels."""

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
from .heisenberg_level25_four_block_row0_pair_reuse import (
    _ROW0_PAIR_REUSE_EVIDENCE_SHA256,
    level25_heisenberg_augmented_four_block_row0_pair_reuse_profile,
)
from .heisenberg_level25_three_block import (
    _address_space_limited_command,
    _run_source_hashed_program,
)
from .science_evidence import exact_execution_matches, independent_replay_matches, reviewed_profile_matches, seal_profile_evidence
from .scientific import ScientificToolError

_PROTOCOL_VERSION = 1
_REVIEWED_PROGRAM_SHA256 = "015b05c0b80857efb1d18cd0bc8fdd27927f6c45c9a33c2162316bc473697009"
_REVIEWED_TRANSCRIPT_SHA256 = "c51f022b6a319ee10cce579ee36740a1a6e4630204b3a9379c7a6d4076e73436"
_ROW0_EDGE_INDICATOR_EVIDENCE_SHA256 = "75dc83534947f323ec2c5ef82e3b8fd0a30f3aedbc25b275c6fb595ff2d3b427"
_REVIEWED_REPLAY_PROGRAM_SHA256 = "6e5d8af634eb349175937a354203dd79bd8c8f1288f1f958796b7ae22b712724"
_REVIEWED_REPLAY_TRANSCRIPT_SHA256 = "cf417fb52cce00b3f91c3767374f3097ceed2f1ced01b7ab6240131ff1d1334e"
_EDGE_VALUE_COUNT = 65
_LABEL_COUNT = 625
_RESIDENT_BYTE_CAP = 64 * 1024**2
_DIAGNOSTIC_CHARGE_BOUND = 10_000_000
_OPERATION_CAP = 2**31

_PROGRAM = r"""
#include <algorithm>
#include <array>
#include <cstdint>
#include <iostream>

using u64=std::uint64_t;using i64=std::int64_t;using u128=unsigned __int128;
constexpr int P=101,OMEGA=95,LABELS=625,EDGE_VALUES=65;
constexpr u64 FNV_OFFSET=1469598103934665603ULL;
constexpr std::array<u64,2>MODQ{{1449072215711168621ULL,1449072215711180741ULL}};
constexpr std::array<u64,2>ROOT5{{1075073713960594284ULL,425568228032004829ULL}};

int fpow(int b,int e){int r=1;while(e){if(e&1)r=r*b%P;b=b*b%P;e>>=1;}return r;}
u64 mulq(u64 a,u64 b,u64 q){return(u128)a*b%q;}u64 powq(u64 a,u64 e,u64 q){u64 r=1;while(e){if(e&1)r=mulq(r,a,q);a=mulq(a,a,q);e>>=1;}return r;}
bool prime64(u64 n){if(n<2)return false;for(u64 p:std::array<u64,12>{{2,3,5,7,11,13,17,19,23,29,31,37}}){if(n%p==0)return n==p;}u64 d=n-1,s=0;while(!(d&1)){d>>=1;++s;}for(u64 a:std::array<u64,7>{{2,325,9375,28178,450775,9780504,1795265022}}){if(a%n==0)continue;u64 x=powq(a%n,d,n);if(x==1||x==n-1)continue;bool witness=true;for(u64 r=1;r<s;++r){x=mulq(x,x,n);if(x==n-1){witness=false;break;}}if(witness)return false;}return true;}
void mix(u64&h,u64 v){for(int b=0;b<8;++b){h^=(v>>(8*b))&255;h*=1099511628211ULL;}}
std::array<int,4>digits(int code){std::array<int,4>d{};for(int q=0;q<4;++q){d[q]=code%5;code/=5;}return d;}
template<std::size_t S>void print_ints(const std::array<int,S>&v){for(std::size_t i=0;i<S;++i){if(i)std::cout<<",";std::cout<<v[i];}std::cout<<"\n";}
template<std::size_t S>void print_u64(const std::array<u64,S>&v){for(std::size_t i=0;i<S;++i){if(i)std::cout<<",";std::cout<<v[i];}std::cout<<"\n";}

int main(){
 std::array<std::array<int,4>,LABELS>d{};std::array<int,LABELS>wvalue{};std::array<bool,P>seen{};std::array<int,P>level{};
 for(int code=0;code<LABELS;++code){d[code]=digits(code);int y=1;for(int q=0;q<4;++q)y=(y+fpow(OMEGA,d[code][q]))%P;wvalue[code]=y;seen[y]=true;++level[y];}
 std::array<int,EDGE_VALUES>values{},levels{},supports{},zeros{};int count=0;for(int y=0;y<P;++y)if(seen[y]){values[count]=y;levels[count]=level[y];++count;}if(count!=EDGE_VALUES)return 91;
 std::array<int,P>edge_index{};edge_index.fill(-1);for(int i=0;i<EDGE_VALUES;++i)edge_index[values[i]]=i;
 std::array<int,2>prime_checks{},root_checks{};for(int m=0;m<2;++m){prime_checks[m]=prime64(MODQ[m]);root_checks[m]=ROOT5[m]!=1&&powq(ROOT5[m],5,MODQ[m])==1;if(!prime_checks[m]||!root_checks[m])return 92;}
 std::array<std::array<int,EDGE_VALUES>,2>mod_support{};std::array<u64,2>mod_checksums{{FNV_OFFSET,FNV_OFFSET}};u64 exact_checksum=FNV_OFFSET;u64 exact_modular_zero_mismatches=0;u64 term_count=0;
 for(int k=0;k<LABELS;++k){std::array<std::array<int,5>,EDGE_VALUES>bins{};for(int delta=0;delta<LABELS;++delta){int dot=0;for(int q=0;q<4;++q)dot=(dot+d[k][q]*d[delta][q])%5;++bins[edge_index[wvalue[delta]]][(5-dot)%5];++term_count;}
  for(int yi=0;yi<EDGE_VALUES;++yi){bool zero=true;for(int e=1;e<5;++e)if(bins[yi][e]!=bins[yi][0])zero=false;if(zero)++zeros[yi];else ++supports[yi];mix(exact_checksum,yi);mix(exact_checksum,k);for(int e=0;e<4;++e)mix(exact_checksum,u64(i64(bins[yi][e]-bins[yi][4])));
   for(int m=0;m<2;++m){u64 value=0,power=1;for(int e=0;e<5;++e){value=(value+mulq(u64(bins[yi][e]),power,MODQ[m]))%MODQ[m];power=mulq(power,ROOT5[m],MODQ[m]);}if(value)++mod_support[m][yi];if((value==0)!=zero)++exact_modular_zero_mismatches;mix(mod_checksums[m],yi);mix(mod_checksums[m],k);mix(mod_checksums[m],value);}
  }
 }
 std::array<int,EDGE_VALUES>defective_values{};int defective_count=0,full_count=0,min_support=LABELS,max_support=0;u64 total_support=0;for(int i=0;i<EDGE_VALUES;++i){min_support=std::min(min_support,supports[i]);max_support=std::max(max_support,supports[i]);total_support+=supports[i];if(supports[i]==LABELS)++full_count;else defective_values[defective_count++]=values[i];}
 std::cout<<"REASONING_AUGMENTED_FOUR_BLOCK_ROW0_EDGE_INDICATOR_FOURIER_RANK_V1\nfield_prime="<<P<<"\nomega="<<OMEGA<<"\nlabel_count="<<LABELS<<"\nedge_value_count="<<count<<"\nedge_values=";print_ints(values);std::cout<<"edge_level_set_sizes=";print_ints(levels);std::cout<<"exact_fourier_support_counts=";print_ints(supports);std::cout<<"exact_fourier_zero_counts=";print_ints(zeros);std::cout<<"modulus_1_support_counts=";print_ints(mod_support[0]);std::cout<<"modulus_2_support_counts=";print_ints(mod_support[1]);
 std::cout<<"transform_moduli=";print_u64(MODQ);std::cout<<"order_5_roots=";print_u64(ROOT5);std::cout<<"prime_checks="<<prime_checks[0]<<","<<prime_checks[1]<<"\norder_5_root_checks="<<root_checks[0]<<","<<root_checks[1]<<"\nexact_coefficient_checksum="<<exact_checksum<<"\nmodular_transform_checksums="<<mod_checksums[0]<<","<<mod_checksums[1]<<"\nexact_modular_zero_mismatch_count="<<exact_modular_zero_mismatches<<"\nfull_rank_indicator_count="<<full_count<<"\ndefective_indicator_count="<<defective_count<<"\ndefective_indicator_values=";for(int i=0;i<defective_count;++i){if(i)std::cout<<",";std::cout<<defective_values[i];}std::cout<<"\nminimum_indicator_rank="<<min_support<<"\nmaximum_indicator_rank="<<max_support<<"\ntotal_indicator_fourier_support="<<total_support<<"\nmaximum_total_indicator_fourier_support="<<EDGE_VALUES*LABELS<<"\nfourier_term_count="<<term_count<<"\nREASONING_END\n";
}
"""


def _parse_list(value: str, *, count: int, label: str) -> list[int]:
    try:
        parsed = [int(item) for item in value.split(",")]
    except ValueError as error:
        raise ScientificToolError(f"edge-indicator transcript has a nonintegral {label}") from error
    if len(parsed) != count:
        raise ScientificToolError(f"edge-indicator transcript has a malformed {label}")
    return parsed


def _parse_transcript(stdout: str) -> dict[str, Any]:
    lines = stdout.strip().splitlines()
    frame = f"REASONING_AUGMENTED_FOUR_BLOCK_ROW0_EDGE_INDICATOR_FOURIER_RANK_V{_PROTOCOL_VERSION}"
    if len(lines) < 3 or lines[0] != frame or lines[-1] != "REASONING_END":
        raise ScientificToolError("edge-indicator transcript has invalid framing")
    fields: dict[str, str] = {}
    for line in lines[1:-1]:
        if "=" not in line:
            raise ScientificToolError("edge-indicator transcript has an invalid field")
        name, value = line.split("=", 1)
        if name in fields:
            raise ScientificToolError("edge-indicator transcript has duplicate fields")
        fields[name] = value
    list_fields = {
        "edge_values": _EDGE_VALUE_COUNT,
        "edge_level_set_sizes": _EDGE_VALUE_COUNT,
        "exact_fourier_support_counts": _EDGE_VALUE_COUNT,
        "exact_fourier_zero_counts": _EDGE_VALUE_COUNT,
        "modulus_1_support_counts": _EDGE_VALUE_COUNT,
        "modulus_2_support_counts": _EDGE_VALUE_COUNT,
        "transform_moduli": 2,
        "order_5_roots": 2,
        "prime_checks": 2,
        "order_5_root_checks": 2,
        "modular_transform_checksums": 2,
        "defective_indicator_values": 5,
    }
    integer_fields = {"field_prime", "omega", "label_count", "edge_value_count", "exact_coefficient_checksum", "exact_modular_zero_mismatch_count", "full_rank_indicator_count", "defective_indicator_count", "minimum_indicator_rank", "maximum_indicator_rank", "total_indicator_fourier_support", "maximum_total_indicator_fourier_support", "fourier_term_count"}
    if set(fields) != integer_fields | set(list_fields):
        raise ScientificToolError("edge-indicator transcript is incomplete")
    try:
        parsed: dict[str, Any] = {name: int(fields[name]) for name in integer_fields}
    except ValueError as error:
        raise ScientificToolError("edge-indicator transcript has a nonintegral field") from error
    parsed.update({name: _parse_list(fields[name], count=count, label=name) for name, count in list_fields.items()})
    return parsed


def _run_program() -> dict[str, Any]:
    computation = _run_source_hashed_program(_PROGRAM, label="augmented-four-block-row0-edge-indicator-rank", timeout_seconds=120, runtime_address_space_bytes=_RESIDENT_BYTE_CAP, preserve_exact_transcript=True)
    return {**computation, **_parse_transcript(computation["transcript"])}


def _run_replay() -> dict[str, Any]:
    replay_path = Path(__file__).with_name("heisenberg_level25_four_block_row0_edge_indicator_replay.py")
    try:
        program = replay_path.read_bytes()
    except OSError as error:
        raise ScientificToolError(f"independent edge-indicator replay is unavailable: {error}") from error
    environment = {"HOME": "", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")}
    replay_python = shutil.which("python3", path="/usr/local/bin:/usr/bin:/bin") or sys.executable
    command = _address_space_limited_command([replay_python, "-I", "-S", str(replay_path)], _RESIDENT_BYTE_CAP)
    try:
        completed = subprocess.run(sandbox.wrap(command, ro=[replay_path], network=False), text=True, capture_output=True, timeout=120, env=environment, check=False)
    except subprocess.TimeoutExpired as error:
        raise ScientificToolError("independent edge-indicator replay exceeded the wall timeout") from error
    except OSError as error:
        raise ScientificToolError(f"independent edge-indicator replay could not start: {error}") from error
    if completed.returncode or completed.stderr.strip():
        detail = (completed.stderr.strip() or completed.stdout.strip() or "unknown replay failure")[:2000]
        raise ScientificToolError(f"independent edge-indicator replay failed: {detail}")
    if len(completed.stdout.encode()) > 2 * 1024**2:
        raise ScientificToolError("independent edge-indicator replay output exceeds 2 MiB")
    try:
        replay = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise ScientificToolError("independent edge-indicator replay returned invalid JSON") from error
    if not isinstance(replay, dict):
        raise ScientificToolError("independent edge-indicator replay returned a non-object")
    return {**replay, "program_sha256": hashlib.sha256(program).hexdigest(), "transcript_sha256": hashlib.sha256(completed.stdout.encode()).hexdigest(), "transcript": completed.stdout, "runtime_address_space_limit_bytes": _RESIDENT_BYTE_CAP, "interpreter": str(Path(replay_python).resolve())}


def level25_heisenberg_augmented_four_block_row0_edge_indicator_rank_profile() -> dict[str, Any]:
    """Measure every lossless edge-value indicator convolution rank exactly."""

    prerequisite = level25_heisenberg_augmented_four_block_row0_pair_reuse_profile()
    prerequisite_verified = reviewed_profile_matches(prerequisite, _ROW0_PAIR_REUSE_EVIDENCE_SHA256)
    prerequisite_evidence_sha256 = prerequisite["evidence_sha256"]
    prerequisite_sha256 = hashlib.sha256(canonical_json(prerequisite).encode()).hexdigest()
    del prerequisite
    computation = _run_program()
    replay = _run_replay()
    comparable = tuple(name for name in replay if name not in {"program_sha256", "transcript_sha256", "transcript", "runtime_address_space_limit_bytes", "interpreter"})
    expected_supports = [601 if value in {40, 45, 61, 70, 92} else 625 for value in computation["edge_values"]]
    certificate = {
        "pair_reuse_receipt_is_the_certified_prerequisite": prerequisite_verified,
        "program_and_transcript_match_reviewed_computation": exact_execution_matches(computation, parser=_parse_transcript, expected_program=_REVIEWED_PROGRAM_SHA256, expected_transcript=_REVIEWED_TRANSCRIPT_SHA256),
        "independent_program_and_transcript_match_reviewed_replay": independent_replay_matches(replay, expected_program=_REVIEWED_REPLAY_PROGRAM_SHA256, expected_transcript=_REVIEWED_REPLAY_TRANSCRIPT_SHA256),
        "compiled_and_independent_replays_agree": all(computation[name] == replay[name] for name in comparable),
        "edge_partition_is_complete": computation["edge_value_count"] == 65 and sum(computation["edge_level_set_sizes"]) == 625,
        "cyclotomic_zero_test_is_exact": computation["exact_fourier_support_counts"] == expected_supports and computation["exact_modular_zero_mismatch_count"] == 0,
        "two_modular_transforms_match_exact_support": computation["modulus_1_support_counts"] == expected_supports and computation["modulus_2_support_counts"] == expected_supports and computation["prime_checks"] == [1, 1] and computation["order_5_root_checks"] == [1, 1],
        "named_diagnostic_fits_hard_caps": _DIAGNOSTIC_CHARGE_BOUND < _OPERATION_CAP and computation["runtime_address_space_limit_bytes"] == _RESIDENT_BYTE_CAP and replay["runtime_address_space_limit_bytes"] == _RESIDENT_BYTE_CAP,
    }
    request: dict[str, Any] = {}
    return seal_profile_evidence({
        "schema": "reasoning.level25-heisenberg-augmented-four-block-row0-edge-indicator-rank-profile.v2",
        "request": request,
        "request_sha256": hashlib.sha256(canonical_json(request).encode()).hexdigest(),
        "pair_reuse_receipt_sha256": prerequisite_sha256,
        "pair_reuse_evidence_sha256": prerequisite_evidence_sha256,
        "kernel_definition": {"group": "(Z/5)^4", "edge": "W(delta)=1+sum_q 95^delta_q in F_101", "indicator": "I_y(delta)=1 exactly when W(delta)=y", "exact_zero_test": "If the five eta-exponent counts are n_0,...,n_4, their sum in Z[eta_5] vanishes exactly when all five counts agree."},
        "computation": computation,
        "independent_python_replay": replay,
        "engineering_caps": {"charged_operations": _OPERATION_CAP, "conservative_diagnostic_charge_bound": _DIAGNOSTIC_CHARGE_BOUND, "resident_bytes_per_implementation": _RESIDENT_BYTE_CAP, "hard_address_space_bytes_per_implementation": _RESIDENT_BYTE_CAP},
        "certificate": certificate,
        "certificate_verified": all(certificate.values()),
        "decision": {"status": "EDGE_INDICATOR_FOURIER_COMPRESSION_REJECTED" if all(certificate.values()) else "PROFILE_INVALID", "reason": "Sixty indicator kernels have exact rank 625 and the remaining mu5-orbit of five has rank 601, leaving 40,505 of 40,625 possible Fourier modes nonzero."},
        "interpretation": "The exact lossless y-slice kernels are essentially full rank. Label Fourier convolution remains algebraically valid but provides only 120 missing modes across all 65 indicators, so this named spectral-support shortcut cannot rescue the over-cap payload contraction.",
        "not_certified": ["an operation lower bound for every grouped or fused contraction", "a crossing B-C join or row-zero survivor count", "rows 5 and 7 or the selected Q7 and Q9 couplings", "a common zero, geometric lift, Brauer class, index bound, or period-index counterexample"],
        "next_gate": {"feature": "level25_heisenberg_augmented_four_block_row0_fused_crossing_design", "requirement": "Design a fused crossing contraction that never emits both pair tables and exploit coefficient or bucket reuse rather than label-kernel Fourier sparsity."},
    })
