# ruff: noqa: E501
"""Exact F_101 nonconsecutive three-block search for the five-pivot tuple."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from .. import sandbox
from ..canonical import canonical_json
from .heisenberg_level25 import (
    FINITE_FIELD_PRIME,
    LEVEL25_HEISENBERG_BALANCED_CHARACTERS,
    MODULUS,
    PRIME,
    ROOT_OF_UNITY,
    _five_pivoted_form_value_and_gradient,
    _modular_rank,
)
from .heisenberg_level25_alphabet import (
    _PURE_POWER_EVIDENCE_SHA256,
    level25_heisenberg_balanced_five_pivot_pure_power_alphabet_profile,
)
from .heisenberg_level25_two_block import _PROGRAM as _TWO_BLOCK_PROGRAM
from .science_evidence import exact_execution_matches, reviewed_profile_matches, seal_profile_evidence
from .scientific import ScientificToolError

_PROTOCOL_VERSION = 1
_TIMEOUT_SECONDS = 60
_MAX_OUTPUT_BYTES = 32 * 1024
_REVIEWED_PROGRAM_SHA256 = "bc10d0ccac82ee98783bcc774f9a9f159843a231612346d0d8c2e9fcfb75bb1a"
_REVIEWED_TRANSCRIPT_SHA256 = "e5ee6516b728189ec19ad377b9d5b5b815d6966d9e053a2583720425233ef07a"
_NONCONSECUTIVE_EVIDENCE_SHA256 = "ad3630016b6b3d2e23da038f971d6c53d020955536035d2ad6f2eb744dde5452"

_PROGRAM_PREFIX, _MAIN_SEPARATOR, _OLD_MAIN = _TWO_BLOCK_PROGRAM.rpartition("int main()")
if not _MAIN_SEPARATOR:  # pragma: no cover - import-time source invariant
    raise RuntimeError("two-block search source has no main function")

_PROGRAM = (
    _PROGRAM_PREFIX
    + r"""
struct Shape3 {
 std::uint32_t code;
 std::array<int,5> u,p5,p20;
 std::array<int,3> b6,b8,b5,b7;
 bool cyclotomic;
};
struct Entry3 {int key,index;bool operator<(const Entry3&o)const{return key<o.key||(key==o.key&&index<o.index);}};

std::array<int,5> decode_shape(std::uint32_t code){std::array<int,5>u{1};for(int q=1;q<5;++q){u[q]=pw(ZETA,code%25);code/=25;}return u;}
std::array<int,5> decode_exponents(std::uint32_t code){std::array<int,5>e{};for(int q=1;q<5;++q){e[q]=code%25;code/=25;}return e;}
bool cyclotomic_shape(std::uint32_t code){auto e=decode_exponents(code);std::array<int,25>count{};for(int q=0;q<5;++q)++count[(e[(q+1)%5]-e[q]+25)%25];for(int k=0;k<25;++k)if(count[k]!=(k%5==0))return false;return true;}
int block_base(int row,int residue,const std::array<int,5>&u){std::array<int,N>x{};for(int q=0;q<5;++q)x[residue+5*q]=u[q];return form_value(row,x);}
int adjacent_invariant(const Shape3&a,const Shape3&b){int value=0;for(int q=0;q<5;++q)value=(value+a.p20[q]*b.p5[q])%P;return value;}
int nonadjacent_invariant(const Shape3&a,const Shape3&b,int residue,int character,int e1,int e5,int e2){int value=0,carry=(residue+2)/5;for(int q=0;q<5;++q){int term=coeff(character,residue+5*q);term=term*pw(a.u[q],e1)%P;term=term*pw(a.u[(q+1)%5],e5)%P;term=term*pw(b.u[(q+carry)%5],e2)%P;value=(value+term)%P;}return value;}

int main(){
 std::vector<Shape3>shapes;
 for(std::uint32_t code=0;code<390625;++code){auto u=decode_shape(code);int cyclic_sum=0;for(int q=0;q<5;++q)cyclic_sum=(cyclic_sum+pw(u[q],24)*u[(q+1)%5])%P;if(cyclic_sum)continue;Shape3 shape{};shape.code=code;shape.u=u;shape.cyclotomic=cyclotomic_shape(code);for(int q=0;q<5;++q){shape.p5[q]=pw(u[q],5);shape.p20[q]=pw(u[q],20);}for(int j=0;j<3;++j){int residue=std::array<int,3>{0,1,3}[j];shape.b6[j]=block_base(6,residue,u);shape.b8[j]=block_base(8,residue,u);shape.b5[j]=block_base(5,residue,u);shape.b7[j]=block_base(7,residue,u);}shapes.push_back(shape);}
 std::uint64_t cyclotomic_count=0,cyclotomic_compatible=0;for(auto&a:shapes)if(a.cyclotomic)++cyclotomic_count;for(auto&a:shapes)if(a.cyclotomic)for(auto&b:shapes)if(b.cyclotomic&&!adjacent_invariant(a,b))++cyclotomic_compatible;
 std::array<std::vector<Entry3>,4> third;for(int c=0;c<4;++c){int scale=pw(GEN,25*c);for(int i=0;i<(int)shapes.size();++i)third[c].push_back({shapes[i].b6[2]*scale%P+P*(shapes[i].b8[2]*scale%P),i});std::sort(third[c].begin(),third[c].end());}
 std::uint64_t pairs=0,triples=0,tests=0,zeros=0,replay_failures=0,rank14=0,zero_checksum=1469598103934665603ULL;int minimum_rank=23,maximum_rank=-1;std::uint32_t first_a=0,first_b=0,first_d=0;int first_m1=-1,first_m3=-1;
 for(int ia=0;ia<(int)shapes.size();++ia)for(int ib=0;ib<(int)shapes.size();++ib){if(adjacent_invariant(shapes[ia],shapes[ib]))continue;++pairs;for(int c1=0;c1<4;++c1){int scale1_25=pw(GEN,25*c1);int v6=(shapes[ia].b6[0]+scale1_25*shapes[ib].b6[1])%P,v8=(shapes[ia].b8[0]+scale1_25*shapes[ib].b8[1])%P;for(int c3=0;c3<4;++c3){int key=((P-v6)%P)+P*((P-v8)%P);auto begin=std::lower_bound(third[c3].begin(),third[c3].end(),Entry3{key,-1});auto end=std::upper_bound(third[c3].begin(),third[c3].end(),Entry3{key,999999});for(auto it=begin;it!=end;++it){++triples;auto&a=shapes[ia];auto&b=shapes[ib];auto&d=shapes[it->index];int scale3_25=pw(GEN,25*c3);int base5=(a.b5[0]+scale1_25*b.b5[1]+scale3_25*d.b5[2])%P,base7=(a.b7[0]+scale1_25*b.b7[1]+scale3_25*d.b7[2])%P;int p13[4]{nonadjacent_invariant(b,d,1,2,1,4,20),nonadjacent_invariant(b,d,1,2,2,13,10),nonadjacent_invariant(b,d,1,2,4,6,15),nonadjacent_invariant(b,d,1,4,1,4,20)};int p30[4]{nonadjacent_invariant(d,a,3,2,1,4,20),nonadjacent_invariant(d,a,3,2,2,13,10),nonadjacent_invariant(d,a,3,2,4,6,15),nonadjacent_invariant(d,a,3,4,1,4,20)};for(int m1=c1;m1<20;m1+=4)for(int m3=c3;m3<20;m3+=4){++tests;int row5=base5,row7=base7,s1=pw(GEN,m1),s3=pw(GEN,m3);int source1[3]{5,15,10},target3[3]{20,10,15},source3[3]{5,15,10};for(int k=0;k<3;++k)row5=(row5+p13[k]*pw(s1,source1[k])%P*pw(s3,target3[k])+p30[k]*pw(s3,source3[k]))%P;row7=(row7+p13[3]*pw(s1,5)%P*pw(s3,20)+p30[3]*pw(s3,5))%P;if(row5||row7)continue;std::array<int,N>x{};for(int q=0;q<5;++q){x[5*q]=a.u[q];x[1+5*q]=s1*b.u[q]%P;x[3+5*q]=s3*d.u[q]%P;}bool replay=true;for(int row=0;row<22;++row)if(form_value(row,x)){replay=false;break;}if(!replay){++replay_failures;continue;}int rank=jac_rank(x);if(!zeros){first_a=a.code;first_b=b.code;first_d=d.code;first_m1=m1;first_m3=m3;}++zeros;if(rank==14)++rank14;minimum_rank=std::min(minimum_rank,rank);maximum_rank=std::max(maximum_rank,rank);mix(zero_checksum,a.code);mix(zero_checksum,b.code);mix(zero_checksum,d.code);mix(zero_checksum,m1);mix(zero_checksum,m3);mix(zero_checksum,rank);}}}}}
 std::cout<<"REASONING_THREE_BLOCK_NONCONSECUTIVE_RATIONAL_V1\n";
 std::cout<<"support_residues=0,1,3\nnormalized_shape_count="<<shapes.size()<<"\ncyclotomic_shape_count="<<cyclotomic_count<<"\ncyclotomic_adjacent_compatible_pair_count="<<cyclotomic_compatible<<"\ncompatible_ordered_shape_pair_count="<<pairs<<"\nshape_scale_class_triple_count="<<triples<<"\nfull_scale_test_count="<<tests<<"\norbit_zero_count="<<zeros<<"\nzero_checksum="<<zero_checksum<<"\nfull_replay_failure_count="<<replay_failures<<"\nrank_14_count="<<rank14<<"\nminimum_rank="<<(zeros?minimum_rank:-1)<<"\nmaximum_rank="<<maximum_rank<<"\nfirst_witness_codes="<<first_a<<","<<first_b<<","<<first_d<<"\nfirst_witness_scale_exponents="<<first_m1<<","<<first_m3<<"\nREASONING_END\n";
}
"""
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _parse_csv_ints(value: str, count: int) -> list[int]:
    try:
        parsed = [int(item) for item in value.split(",")]
    except ValueError as error:
        raise ScientificToolError("three-block transcript has a nonintegral list") from error
    if len(parsed) != count:
        raise ScientificToolError("three-block transcript has a malformed list")
    return parsed


def _parse_transcript(stdout: str) -> dict[str, Any]:
    lines = stdout.strip().splitlines()
    frame = f"REASONING_THREE_BLOCK_NONCONSECUTIVE_RATIONAL_V{_PROTOCOL_VERSION}"
    if len(lines) < 3 or lines[0] != frame or lines[-1] != "REASONING_END":
        raise ScientificToolError("three-block transcript has invalid framing")
    fields: dict[str, str] = {}
    for line in lines[1:-1]:
        if "=" not in line:
            raise ScientificToolError("three-block transcript has an invalid field")
        name, value = line.split("=", 1)
        if name in fields:
            raise ScientificToolError("three-block transcript has duplicate fields")
        fields[name] = value
    integer_names = {
        "normalized_shape_count",
        "cyclotomic_shape_count",
        "cyclotomic_adjacent_compatible_pair_count",
        "compatible_ordered_shape_pair_count",
        "shape_scale_class_triple_count",
        "full_scale_test_count",
        "orbit_zero_count",
        "zero_checksum",
        "full_replay_failure_count",
        "rank_14_count",
        "minimum_rank",
        "maximum_rank",
    }
    expected = integer_names | {
        "support_residues",
        "first_witness_codes",
        "first_witness_scale_exponents",
    }
    if set(fields) != expected:
        raise ScientificToolError("three-block transcript is incomplete")
    try:
        integers = {name: int(fields[name]) for name in integer_names}
    except ValueError as error:
        raise ScientificToolError("three-block transcript has a nonintegral field") from error
    return {
        **integers,
        "support_residues": _parse_csv_ints(fields["support_residues"], 3),
        "first_witness_codes": _parse_csv_ints(fields["first_witness_codes"], 3),
        "first_witness_scale_exponents": _parse_csv_ints(fields["first_witness_scale_exponents"], 2),
    }


def _run_source_hashed_program(
    program: str,
    *,
    label: str,
    timeout_seconds: int = _TIMEOUT_SECONDS,
    runtime_address_space_bytes: int | None = None,
    preserve_exact_transcript: bool = False,
) -> dict[str, Any]:
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
        with tempfile.TemporaryDirectory(prefix=f"reasoning-level25-{label}-") as temporary:
            workdir = Path(temporary)
            environment["HOME"] = temporary
            binary = workdir / label
            compiled = subprocess.run(
                sandbox.wrap(
                    [str(compiler), "-O3", "-std=c++20", "-x", "c++", "-", "-o", str(binary)],
                    rw=[workdir],
                    chdir=workdir,
                    network=False,
                ),
                input=program,
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                env=environment,
                check=False,
            )
            if compiled.returncode or compiled.stderr.strip():
                detail = (compiled.stderr.strip() or compiled.stdout.strip() or "unknown compiler failure")[:2000]
                raise ScientificToolError(f"{label} program failed to compile: {detail}")
            binary_sha256 = _sha256_file(binary)
            runtime_command = [str(binary)]
            if runtime_address_space_bytes is not None:
                runtime_command = _address_space_limited_command(
                    runtime_command,
                    runtime_address_space_bytes,
                )
            completed = subprocess.run(
                sandbox.wrap(runtime_command, rw=[workdir], chdir=workdir, network=False),
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                env=environment,
                check=False,
            )
    except subprocess.TimeoutExpired as error:
        raise ScientificToolError(f"{label} program exceeded the {timeout_seconds}-second wall timeout") from error
    except OSError as error:
        raise ScientificToolError(f"{label} program could not start: {error}") from error
    if len(completed.stdout.encode()) > _MAX_OUTPUT_BYTES or len(completed.stderr.encode()) > _MAX_OUTPUT_BYTES:
        raise ScientificToolError(f"{label} program output exceeds the safe bound")
    if completed.returncode or completed.stderr.strip():
        detail = (completed.stderr.strip() or completed.stdout.strip() or "unknown program failure")[:2000]
        raise ScientificToolError(f"{label} program failed: {detail}")
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
        raise ScientificToolError(f"{label} compiler version check failed: {error}") from error
    result = {
        "protocol_version": _PROTOCOL_VERSION,
        "program_sha256": hashlib.sha256(program.encode()).hexdigest(),
        "transcript_sha256": hashlib.sha256(completed.stdout.encode()).hexdigest(),
        "transcript": (
            completed.stdout
            if preserve_exact_transcript
            else completed.stdout.strip()
        ),
        "compiler": str(compiler),
        "compiler_sha256": _sha256_file(compiler),
        "compiler_version": compiler_version,
        "binary_sha256": binary_sha256,
    }
    if runtime_address_space_bytes is not None:
        result["runtime_address_space_limit_bytes"] = runtime_address_space_bytes
    return result


def _address_space_limited_command(command: list[str], limit_bytes: int) -> list[str]:
    """Exec a command after applying a process-inherited address-space limit."""

    if limit_bytes <= 0:
        raise ValueError("address-space limit must be positive")
    launcher = (
        "import os,resource,sys;"
        "n=int(sys.argv[1]);"
        "resource.setrlimit(resource.RLIMIT_AS,(n,n));"
        "os.execv(sys.argv[2],sys.argv[2:])"
    )
    launcher_python = shutil.which(
        "python3",
        path="/usr/local/bin:/usr/bin:/bin",
    ) or sys.executable
    return [
        launcher_python,
        "-I",
        "-S",
        "-c",
        launcher,
        str(limit_bytes),
        *command,
    ]


def _run_program() -> dict[str, Any]:
    computation = _run_source_hashed_program(
        _PROGRAM,
        label="three-block-nonconsecutive",
        preserve_exact_transcript=True,
    )
    return {**computation, **_parse_transcript(computation["transcript"])}


def _decode_shape(code: int) -> list[int]:
    shape = [1]
    for _ in range(PRIME - 1):
        shape.append(pow(ROOT_OF_UNITY, code % MODULUS, FINITE_FIELD_PRIME))
        code //= MODULUS
    return shape


def _replay_witness(computation: dict[str, Any]) -> dict[str, Any]:
    point = [0] * MODULUS
    residues = computation["support_residues"]
    codes = computation["first_witness_codes"]
    scales = [1] + [pow(2, exponent, FINITE_FIELD_PRIME) for exponent in computation["first_witness_scale_exponents"]]
    for residue, code, scale in zip(residues, codes, scales, strict=True):
        for quotient, value in enumerate(_decode_shape(code)):
            point[residue + PRIME * quotient] = scale * value % FINITE_FIELD_PRIME
    rows = [_five_pivoted_form_value_and_gradient(character, point) for character in LEVEL25_HEISENBERG_BALANCED_CHARACTERS]
    values = [value for value, _ in rows]
    rank = _modular_rank([gradient for _, gradient in rows])
    return {
        "point": point,
        "nonzero_form_count": sum(value != 0 for value in values),
        "jacobian_rank": rank,
        "projective_tangent_dimension_lower_bound": MODULUS - 1 - rank,
    }


def level25_heisenberg_balanced_five_pivot_nonconsecutive_three_block_rational_profile() -> dict[str, Any]:
    """Enumerate F_101 zeros on the {0,1,3} three-block support class."""

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
            and computation["zero_checksum"] == 4071796996668289174
        ),
        "pure_power_prerequisite_verified": reviewed_profile_matches(prerequisite, _PURE_POWER_EVIDENCE_SHA256),
        "support_is_the_nonconsecutive_three_block_orbit": computation["support_residues"] == [0, 1, 3],
        "all_reduced_shapes_and_relative_scales_are_exhausted": (
            computation["normalized_shape_count"] == 4420
            and computation["compatible_ordered_shape_pair_count"] == 721750
            and computation["shape_scale_class_triple_count"] == 5035625
            and computation["full_scale_test_count"] == 125890625
        ),
        "every_compiled_survivor_passes_full_equation_replay": computation["full_replay_failure_count"] == 0,
        "all_survivors_have_rank_fourteen": (
            computation["orbit_zero_count"] == 12400
            and computation["rank_14_count"] == computation["orbit_zero_count"]
            and computation["minimum_rank"] == computation["maximum_rank"] == 14
        ),
        "python_replays_the_first_witness": witness["nonzero_form_count"] == 0 and witness["jacobian_rank"] == 14,
        "cyclotomic_torsion_substratum_has_no_adjacent_pair": (
            computation["cyclotomic_shape_count"] == 120 and computation["cyclotomic_adjacent_compatible_pair_count"] == 0
        ),
    }
    request: dict[str, Any] = {}
    verified = all(certificate.values())
    return seal_profile_evidence({
        "schema": "reasoning.level25-heisenberg-balanced-five-pivot-nonconsecutive-three-block-rational-profile.v2",
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
        "decision": {"status": "F101_NONCONSECUTIVE_THREE_BLOCK_ZEROS_CLASSIFIED" if verified else "PROFILE_INVALID"},
        "interpretation": (
            "On the nonconsecutive three-residue-block support orbit, the explicit five-pivot tuple has 12,400 F_101-rational representatives modulo the stated projective and independent mu_5 normalizations. Every one has Jacobian rank 14, so the special fiber is not a smooth two-dimensional complete intersection at these points."
        ),
        "cyclotomic_lift_check": (
            "Only 120 of the 4,420 modular block shapes make the cyclic shape sum vanish as a cyclotomic identity, and no ordered pair of those 120 satisfies the necessary adjacent-pivot equation even after reduction modulo 101. Thus none of these modular witnesses lifts inside the same mu_25-shape, mu_100-scale torsion parameterization."
        ),
        "not_certified": [
            "a characteristic-zero singularity or common zero",
            "absence or presence of points with non-block-constant 25th powers over the algebraic closure",
            "the consecutive three-block, four-block, or five-block support strata",
            "a Brauer class, period, index bound, or period-index counterexample",
        ],
        "next_gate": {
            "feature": "level25_heisenberg_balanced_five_pivot_consecutive_three_block_rational_profile",
            "requirement": "Exhaust support {0,1,2}, including both directed adjacent edges and the carried nonadjacent edge, with full equation and all-column Jacobian replay.",
        },
    })
