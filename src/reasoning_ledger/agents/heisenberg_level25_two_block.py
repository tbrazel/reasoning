# ruff: noqa: E501
"""Exact F_101 two-block search for the balanced five-pivot tuple."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .. import sandbox
from ..canonical import canonical_json
from .heisenberg_level25 import (
    _LEVEL25_HEISENBERG_NONADJACENT_PIVOTS,
    FINITE_FIELD_PRIME,
    LEVEL25_HEISENBERG_BALANCED_CHARACTERS,
    MODULUS,
    PRIME,
    ROOT_OF_UNITY,
)
from .heisenberg_level25_alphabet import (
    _PURE_POWER_EVIDENCE_SHA256,
    level25_heisenberg_balanced_five_pivot_pure_power_alphabet_profile,
)
from .science_evidence import exact_execution_matches, reviewed_profile_matches, seal_profile_evidence
from .scientific import ScientificToolError

_PROTOCOL_VERSION = 1
_TIMEOUT_SECONDS = 30
_MAX_OUTPUT_BYTES = 32 * 1024
_REVIEWED_PROGRAM_SHA256 = "f0d3b52946f945b872c23089e6e9f64f17ee9f6d2971f4f6c892d2c877e557eb"
_REVIEWED_TRANSCRIPT_SHA256 = "a03907ade8fa86d3d9b12667471cd7e882b9b4e7c8b36efa797e6aaca863b5d0"
_TWO_BLOCK_EVIDENCE_SHA256 = "2000fce3966e4592c0e35f975ecf00282fa2162ce6f769b9f115ffa4a9f1d9ae"

_PROGRAM = r"""#include <algorithm>
#include <array>
#include <cstdint>
#include <iostream>
#include <utility>
#include <vector>

constexpr int P=101,N=25,ZETA=5,GEN=2;
constexpr std::array<std::pair<int,int>,22> CH{{
 {5,20},{10,20},{15,20},{20,20},{1,0},{2,15},{3,20},{4,15},{6,5},
 {7,0},{8,0},{9,0},{11,0},{12,0},{13,0},{14,0},{16,0},{17,0},
 {18,0},{19,0},{21,0},{22,0}}};
constexpr std::array<int,8> RES{0,1,2,3,5,6,7,8};
constexpr std::array<std::array<int,4>,4> PIV{{
 {2,1,4,20},{2,2,13,10},{2,4,6,15},{4,1,4,20}}};

int pw(int b,int e){int r=1;while(e){if(e&1)r=r*b%P;b=b*b%P;e>>=1;}return r;}
int coeff(int a,int i){int e=(-a*i)%N;if(e<0)e+=N;return pw(ZETA,e);}

int form_value(int row,const std::array<int,N>&x){
 auto [a,b]=CH[row]; int value=0;
 for(int i=0;i<N;++i)value=(value+coeff(a,i)*pw(x[i],24)%P*x[(i-b+N)%N])%P;
 if(row==0)for(int i=0;i<N;++i)value=(value+coeff(5,i)*pw(x[i],20)%P*pw(x[(i+1)%N],5))%P;
 for(auto p:PIV)if(a==p[0]&&b==15)for(int i=0;i<N;++i){
   int term=coeff(a,i);term=term*pw(x[i],p[1])%P;term=term*pw(x[(i+5)%N],p[2])%P;term=term*pw(x[(i+2)%N],p[3])%P;value=(value+term)%P;
 }
 return value;
}

std::array<int,8> residual(const std::array<int,N>&x){
 std::array<int,8> v{};for(int j=0;j<8;++j)v[j]=form_value(RES[j],x);return v;
}

void add_grad(int row,const std::array<int,N>&x,std::array<int,N>&g){
 auto [a,b]=CH[row];
 for(int i=0;i<N;++i){int j=(i-b+N)%N,c=coeff(a,i);
   g[i]=(g[i]+24*c%P*pw(x[i],23)%P*x[j])%P;
   g[j]=(g[j]+c*pw(x[i],24))%P;
 }
 if(row==0)for(int i=0;i<N;++i){int j=(i+1)%N,c=coeff(5,i);
   g[i]=(g[i]+20*c%P*pw(x[i],19)%P*pw(x[j],5))%P;
   g[j]=(g[j]+5*c%P*pw(x[i],20)%P*pw(x[j],4))%P;
 }
 for(auto p:PIV)if(a==p[0]&&b==15)for(int i=0;i<N;++i){
   std::array<int,3> ix{i,(i+5)%N,(i+2)%N};std::array<int,3> ex{p[1],p[2],p[3]};int c=coeff(a,i);
   for(int s=0;s<3;++s){int d=c*ex[s]%P;for(int t=0;t<3;++t)d=d*pw(x[ix[t]],ex[t]-(s==t))%P;g[ix[s]]=(g[ix[s]]+d)%P;}
 }
}

int jac_rank(const std::array<int,N>&x){
 int a[22][25]{};for(int r=0;r<22;++r){std::array<int,N>g{};add_grad(r,x,g);for(int c=0;c<N;++c)a[r][c]=g[c];}
 int rank=0;for(int c=0;c<N&&rank<22;++c){int p=rank;while(p<22&&!a[p][c])++p;if(p==22)continue;for(int j=c;j<N;++j)std::swap(a[p][j],a[rank][j]);int inv=pw(a[rank][c],P-2);for(int j=c;j<N;++j)a[rank][j]=a[rank][j]*inv%P;for(int r=0;r<22;++r)if(r!=rank&&a[r][c]){int f=a[r][c];for(int j=c;j<N;++j){a[r][j]=(a[r][j]-f*a[rank][j])%P;if(a[r][j]<0)a[r][j]+=P;}}++rank;}return rank;
}

void shape_point(std::uint32_t code,int residue,int scale,std::array<int,N>&x){
 x[residue]=scale;for(int q=1;q<5;++q){int e=code%25;code/=25;x[residue+5*q]=scale*pw(ZETA,e)%P;}
}

std::uint64_t pack(const std::array<int,8>&v,const std::vector<int>&positions,bool negate=false,int scale=1){
 std::uint64_t key=0,mul=1;for(int p:positions){int z=v[p]*scale%P;if(negate&&z)z=P-z;key+=mul*z;mul*=P;}return key;
}

struct Entry{std::uint64_t key;std::uint32_t code;bool operator<(const Entry&o)const{return key<o.key||(key==o.key&&code<o.code);}};

void mix(std::uint64_t& hash,std::uint64_t value){for(int byte=0;byte<8;++byte){hash^=(value>>(8*byte))&255;hash*=1099511628211ULL;}}

void search_kind(int distance){
 std::vector<int> unaffected = distance==1?std::vector<int>{1,2,3,4,5,6,7}:std::vector<int>{0,1,2,3,5,7};
 std::vector<Entry> first;first.reserve(390625);
 for(std::uint32_t code=0;code<390625;++code){std::array<int,N>x{};shape_point(code,0,1,x);first.push_back({pack(residual(x),unaffected,true),code});}
 std::sort(first.begin(),first.end());
 std::uint64_t join_hits=0,coupled_tests=0,zeros=0,singular=0,join_checksum=1469598103934665603ULL,coupled_checksum=1469598103934665603ULL,zero_checksum=1469598103934665603ULL;int minrank=23;std::array<int,N>witness{};std::uint32_t wa=0,wb=0;int wm=0;
 for(std::uint32_t code=0;code<390625;++code){
  std::array<int,N>base{};shape_point(code,distance,1,base);auto bv=residual(base);
  for(int residue=0;residue<4;++residue){int c=pw(GEN,25*residue);auto key=pack(bv,unaffected,false,c);Entry lo{key,0},hi{key,390625};auto begin=std::lower_bound(first.begin(),first.end(),lo);auto end=std::upper_bound(first.begin(),first.end(),hi);
   join_hits+=end-begin;
   for(auto it=begin;it!=end;++it){mix(join_checksum,distance);mix(join_checksum,it->code);mix(join_checksum,code);mix(join_checksum,residue);for(int m=residue;m<20;m+=4){++coupled_tests;mix(coupled_checksum,distance);mix(coupled_checksum,it->code);mix(coupled_checksum,code);mix(coupled_checksum,m);std::array<int,N>x{};shape_point(it->code,0,1,x);shape_point(code,distance,pw(GEN,m),x);bool ok=true;for(int row=0;row<22;++row)if(form_value(row,x)){ok=false;break;}if(!ok)continue;++zeros;mix(zero_checksum,distance);mix(zero_checksum,it->code);mix(zero_checksum,code);mix(zero_checksum,m);int rank=jac_rank(x);if(rank<minrank){minrank=rank;witness=x;wa=it->code;wb=code;wm=m;}if(rank<22)++singular;}}
  }
 }
 std::cout<<"distance_"<<distance<<"_join_hits="<<join_hits<<"\n";
 std::cout<<"distance_"<<distance<<"_join_checksum="<<join_checksum<<"\n";
 std::cout<<"distance_"<<distance<<"_coupled_tests="<<coupled_tests<<"\n";
 std::cout<<"distance_"<<distance<<"_coupled_checksum="<<coupled_checksum<<"\n";
 std::cout<<"distance_"<<distance<<"_orbit_zero_count="<<zeros<<"\n";
 std::cout<<"distance_"<<distance<<"_zero_checksum="<<zero_checksum<<"\n";
 std::cout<<"distance_"<<distance<<"_singular_orbit_count="<<singular<<"\n";
 std::cout<<"distance_"<<distance<<"_minimum_rank="<<(zeros?minrank:-1)<<"\n";
 if(zeros){std::cout<<"distance_"<<distance<<"_witness_codes="<<wa<<","<<wb<<","<<wm<<"\n";std::cout<<"distance_"<<distance<<"_witness=";for(int i=0;i<N;++i){if(i)std::cout<<",";std::cout<<witness[i];}std::cout<<"\n";}
}

int main(){std::cout<<"REASONING_TWO_BLOCK_RATIONAL_V1\n";std::cout<<"shape_count=390625\nrelative_scale_class_count=20\nprojective_mu5_orbits_per_support=3051757812500\nsupport_pair_orbit_count=2\n";search_kind(1);search_kind(2);std::cout<<"REASONING_END\n";}
"""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _parse_transcript(stdout: str) -> dict[str, Any]:
    lines = stdout.strip().splitlines()
    frame = f"REASONING_TWO_BLOCK_RATIONAL_V{_PROTOCOL_VERSION}"
    if len(lines) < 3 or lines[0] != frame or lines[-1] != "REASONING_END":
        raise ScientificToolError("two-block rational transcript has invalid framing")
    fields: dict[str, str] = {}
    for line in lines[1:-1]:
        if "=" not in line:
            raise ScientificToolError("two-block rational transcript has an invalid field")
        name, value = line.split("=", 1)
        if name in fields:
            raise ScientificToolError("two-block rational transcript has duplicate fields")
        fields[name] = value
    global_names = {
        "shape_count",
        "relative_scale_class_count",
        "projective_mu5_orbits_per_support",
        "support_pair_orbit_count",
    }
    distance_suffixes = {
        "join_hits",
        "join_checksum",
        "coupled_tests",
        "coupled_checksum",
        "orbit_zero_count",
        "zero_checksum",
        "singular_orbit_count",
        "minimum_rank",
    }
    expected = global_names | {f"distance_{distance}_{suffix}" for distance in (1, 2) for suffix in distance_suffixes}
    if set(fields) != expected:
        raise ScientificToolError("two-block rational transcript is incomplete")
    try:
        values = {name: int(value) for name, value in fields.items()}
    except ValueError as error:
        raise ScientificToolError("two-block rational transcript has a nonintegral field") from error
    if any(value < 0 for name, value in values.items() if not name.endswith("minimum_rank")):
        raise ScientificToolError("two-block rational transcript has a negative field")
    return {
        **{name: values[name] for name in global_names},
        "support_classes": {
            distance: {suffix: values[f"distance_{distance}_{suffix}"] for suffix in distance_suffixes} for distance in (1, 2)
        },
    }


def _run_program() -> dict[str, Any]:
    compiler_value = shutil.which("g++")
    if compiler_value is None:
        raise ScientificToolError("g++ is unavailable on the Reasoning service PATH")
    compiler = Path(compiler_value).resolve()
    environment = {
        "HOME": "",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
    }
    try:
        with tempfile.TemporaryDirectory(prefix="reasoning-level25-two-block-rational-") as temporary:
            workdir = Path(temporary)
            environment["HOME"] = temporary
            binary = workdir / "two-block-rational"
            compile_command = sandbox.wrap(
                [
                    str(compiler),
                    "-O3",
                    "-std=c++20",
                    "-x",
                    "c++",
                    "-",
                    "-o",
                    str(binary),
                ],
                rw=[workdir],
                chdir=workdir,
                network=False,
            )
            compiled = subprocess.run(
                compile_command,
                input=_PROGRAM,
                text=True,
                capture_output=True,
                timeout=_TIMEOUT_SECONDS,
                env=environment,
                check=False,
            )
            if compiled.returncode or compiled.stderr.strip():
                detail = (compiled.stderr.strip() or compiled.stdout.strip() or "unknown compiler failure")[:2000]
                raise ScientificToolError(f"two-block rational program failed to compile: {detail}")
            binary_sha256 = _sha256_file(binary)
            run_command = sandbox.wrap(
                [str(binary)],
                rw=[workdir],
                chdir=workdir,
                network=False,
            )
            completed = subprocess.run(
                run_command,
                text=True,
                capture_output=True,
                timeout=_TIMEOUT_SECONDS,
                env=environment,
                check=False,
            )
    except subprocess.TimeoutExpired as error:
        raise ScientificToolError(f"two-block rational program exceeded the {_TIMEOUT_SECONDS}-second wall timeout") from error
    except OSError as error:
        raise ScientificToolError(f"two-block rational program could not start: {error}") from error
    stdout = completed.stdout
    stderr = completed.stderr
    if len(stdout.encode()) > _MAX_OUTPUT_BYTES or len(stderr.encode()) > _MAX_OUTPUT_BYTES:
        raise ScientificToolError("two-block rational output exceeds the safe bound")
    if completed.returncode or stderr.strip():
        detail = (stderr.strip() or stdout.strip() or "unknown program failure")[:2000]
        raise ScientificToolError(f"two-block rational program failed: {detail}")
    parsed = _parse_transcript(stdout)
    try:
        compiler_version = subprocess.run(
            [str(compiler), "--version"],
            text=True,
            capture_output=True,
            timeout=5,
            env=environment,
            check=True,
        ).stdout.splitlines()[0]
    except (OSError, subprocess.SubprocessError, IndexError) as error:
        raise ScientificToolError(f"two-block rational compiler version check failed: {error}") from error
    return {
        "protocol_version": _PROTOCOL_VERSION,
        "program_sha256": hashlib.sha256(_PROGRAM.encode()).hexdigest(),
        "transcript_sha256": hashlib.sha256(stdout.encode()).hexdigest(),
        "transcript": stdout,
        "compiler": str(compiler),
        "compiler_sha256": _sha256_file(compiler),
        "compiler_version": compiler_version,
        "binary_sha256": binary_sha256,
        **parsed,
    }


def level25_heisenberg_balanced_five_pivot_two_block_rational_profile() -> dict[str, Any]:
    """Exclude F_101-rational full common zeros on two-block supports."""

    prerequisite = level25_heisenberg_balanced_five_pivot_pure_power_alphabet_profile()
    prerequisite_sha256 = hashlib.sha256(canonical_json(prerequisite).encode()).hexdigest()
    computation = _run_program()
    adjacent = computation["support_classes"][1]
    nonadjacent = computation["support_classes"][2]
    expected_characters = (
        (5, 20),
        (10, 20),
        (15, 20),
        (20, 20),
        (1, 0),
        (2, 15),
        (3, 20),
        (4, 15),
        (6, 5),
        (7, 0),
        (8, 0),
        (9, 0),
        (11, 0),
        (12, 0),
        (13, 0),
        (14, 0),
        (16, 0),
        (17, 0),
        (18, 0),
        (19, 0),
        (21, 0),
        (22, 0),
    )
    expected_pivots = (
        (2, 1, 4, 20),
        (2, 2, 13, 10),
        (2, 4, 6, 15),
        (4, 1, 4, 20),
    )
    certificate = {
        "complete_native_output_matches_reviewed_computation": exact_execution_matches(
            computation,
            parser=_parse_transcript,
            expected_program=_REVIEWED_PROGRAM_SHA256,
            expected_transcript=_REVIEWED_TRANSCRIPT_SHA256,
        ),
        "pure_power_prerequisite_verified": reviewed_profile_matches(prerequisite, _PURE_POWER_EVIDENCE_SHA256),
        "compiled_recipe_matches_the_five_pivot_tuple": (
            expected_characters == LEVEL25_HEISENBERG_BALANCED_CHARACTERS and expected_pivots == _LEVEL25_HEISENBERG_NONADJACENT_PIVOTS
        ),
        "two_is_a_primitive_generator_of_f101_units": (
            pow(2, 100, FINITE_FIELD_PRIME) == 1 and pow(2, 50, FINITE_FIELD_PRIME) != 1 and pow(2, 20, FINITE_FIELD_PRIME) != 1
        ),
        "all_normalized_block_shapes_exhausted": (computation["shape_count"] == MODULUS ** (PRIME - 1)),
        "relative_block_scale_is_exhausted_modulo_mu5": (
            computation["relative_scale_class_count"] == (FINITE_FIELD_PRIME - 1) // PRIME
            and computation["projective_mu5_orbits_per_support"] == MODULUS**8 * ((FINITE_FIELD_PRIME - 1) // PRIME)
        ),
        "cyclic_symmetry_leaves_two_pair_support_classes": (computation["support_pair_orbit_count"] == 2),
        "adjacent_pairs_are_excluded_by_seven_uncoupled_rows": (
            adjacent["join_hits"] == 0 and adjacent["coupled_tests"] == 0 and adjacent["orbit_zero_count"] == 0
        ),
        "every_nonadjacent_join_candidate_is_tested_at_all_five_scales": (
            nonadjacent["join_hits"] == 8375 and nonadjacent["coupled_tests"] == PRIME * nonadjacent["join_hits"]
        ),
        "nonadjacent_pairs_have_no_full_common_zero": (nonadjacent["orbit_zero_count"] == 0 and nonadjacent["singular_orbit_count"] == 0),
    }
    request: dict[str, Any] = {}
    verified = all(certificate.values())
    return seal_profile_evidence({
        "schema": ("reasoning.level25-heisenberg-balanced-five-pivot-two-block-rational-profile.v2"),
        "request": request,
        "request_sha256": hashlib.sha256(canonical_json(request).encode()).hexdigest(),
        "finite_field": {
            "prime": FINITE_FIELD_PRIME,
            "primitive_twenty_fifth_root": ROOT_OF_UNITY,
            "primitive_unit_generator": 2,
        },
        "pure_power_prerequisite": {
            "receipt_sha256": prerequisite_sha256,
            "evidence_sha256": prerequisite["evidence_sha256"],
            "program_sha256": prerequisite["computation"]["program_sha256"],
            "transcript_sha256": prerequisite["computation"]["transcript_sha256"],
            "certificate_verified": prerequisite["certificate_verified"],
        },
        "parameterization": (
            "After cyclically moving a two-block support to {0,d}, d=1 or 2, "
            "set x_(5q)=u_q and x_(d+5q)=lambda*v_q with u_0=v_0=1 and "
            "u_q,v_q in mu_25. Projective scaling fixes x_0=1. Independent "
            "mu_5 block scaling reduces lambda to twenty representatives."
        ),
        "search_reduction": {
            "adjacent_support": (
                "Hash-join all shape pairs and four lambda^25 values on the "
                "seven residual rows untouched by the adjacent pivot, then "
                "test the coupled (5,20) row at all five lambda classes."
            ),
            "nonadjacent_support": (
                "Hash-join on the six residual rows untouched by the four "
                "nonadjacent pivots, then evaluate all twenty-two forms at "
                "every one of the five lambda classes above each join."
            ),
            "represented_projective_mu5_orbits_per_support": computation["projective_mu5_orbits_per_support"],
        },
        "computation": computation,
        "execution_limits": {
            "primary_wall_seconds": _TIMEOUT_SECONDS,
            "primary_hard_address_space_bytes": None,
        },
        "certificate": certificate,
        "certificate_verified": verified,
        "decision": {"status": "F101_TWO_BLOCK_COMMON_ZEROS_EXCLUDED" if verified else "PROFILE_INVALID"},
        "interpretation": (
            "The explicit five-pivot tuple has no F_101-rational full common "
            "zero whose support is the union of exactly two residue blocks. "
            "Together with the already-certified empty one-block fixed P4 "
            "strata, any F_101-rational common zero must use at least three "
            "complete residue blocks."
        ),
        "not_certified": [
            "absence of common zeros on supports with three, four, or five residue blocks",
            "absence of two-block common zeros over the algebraic closure of F_101 or in characteristic zero",
            "global dimension, regular-sequence status, smoothness, or integrality",
            "a Brauer class, period, index bound, or counterexample",
        ],
        "next_gate": {
            "feature": ("level25_heisenberg_balanced_five_pivot_three_block_rational_search"),
            "requirement": (
                "Use the two cyclic support types and precomputed one-block "
                "signatures to solve the remaining eight equations, retaining "
                "all twenty-five Jacobian columns at every common zero."
            ),
        },
    })
