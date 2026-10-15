# ruff: noqa: E501
"""Exact two-coordinate AD5 prefix census across one complete central fiber."""

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
from .heisenberg_level25_four_block_ad5 import (
    _FOURIER_EVIDENCE_SHA256,
    _PHASE_EVIDENCE_SHA256,
    _RESIDENT_BYTE_CAP,
    _source_manifest,
)
from .heisenberg_level25_four_block_ad5 import (
    _PROGRAM as _FIRST_CONTEXT_PROGRAM,
)
from .heisenberg_level25_three_block import (
    _address_space_limited_command,
    _run_source_hashed_program,
)
from .heisenberg_level25_three_block_coverage import (
    level25_heisenberg_augmented_four_block_fourier_profile,
    level25_heisenberg_augmented_four_block_phase_quotient_profile,
)
from .science_evidence import (
    exact_execution_matches,
    independent_replay_matches,
    reviewed_profile_matches,
    seal_profile_evidence,
)
from .scientific import ScientificToolError

_PROTOCOL_VERSION = 1
_CELL_COUNT = 101 * 101
_COMBINED_CHARGE_CAP = 300_000_000
_REVIEWED_PROGRAM_SHA256 = "31314f37c274d8c008bc4f017e949a9bbbf9ed1eb07a65c416dc0ff2c6eda99f"
_REVIEWED_TRANSCRIPT_SHA256 = "f2b359b87b2363bb1b57b49d2f120a74afe3c5836bfc2031f2be38869c85e4bf"
_REVIEWED_REPLAY_PROGRAM_SHA256 = "01ac41b766ae99f798eacf7dcae1c1878e19af09fa3ec2f18be00f8044f17e36"
_REVIEWED_REPLAY_TRANSCRIPT_SHA256 = "f48149a15e12bdcaffef6102eee53e27ba51526a0a7cd0b5cb1d8850767c064d"

_SUPPORT, _MAIN_SEPARATOR, _OLD_MAIN = _FIRST_CONTEXT_PROGRAM.partition(
    "\nint main(){"
)
if not _MAIN_SEPARATOR:
    raise RuntimeError("bounded AD5 support program no longer has the expected main")

_PROGRAM = (
    _SUPPORT
    + r"""
#include <iomanip>

constexpr int CELL_COUNT=P*P;
constexpr u64 COMBINED_CAP=300000000ULL;

struct Sha256{
 std::array<std::uint32_t,8>h{{0x6a09e667U,0xbb67ae85U,0x3c6ef372U,0xa54ff53aU,0x510e527fU,0x9b05688cU,0x1f83d9abU,0x5be0cd19U}};
 std::array<std::uint8_t,64>data{};std::size_t used=0;u64 bits=0;
 static constexpr std::array<std::uint32_t,64>k{{
  0x428a2f98U,0x71374491U,0xb5c0fbcfU,0xe9b5dba5U,0x3956c25bU,0x59f111f1U,0x923f82a4U,0xab1c5ed5U,
  0xd807aa98U,0x12835b01U,0x243185beU,0x550c7dc3U,0x72be5d74U,0x80deb1feU,0x9bdc06a7U,0xc19bf174U,
  0xe49b69c1U,0xefbe4786U,0x0fc19dc6U,0x240ca1ccU,0x2de92c6fU,0x4a7484aaU,0x5cb0a9dcU,0x76f988daU,
  0x983e5152U,0xa831c66dU,0xb00327c8U,0xbf597fc7U,0xc6e00bf3U,0xd5a79147U,0x06ca6351U,0x14292967U,
  0x27b70a85U,0x2e1b2138U,0x4d2c6dfcU,0x53380d13U,0x650a7354U,0x766a0abbU,0x81c2c92eU,0x92722c85U,
  0xa2bfe8a1U,0xa81a664bU,0xc24b8b70U,0xc76c51a3U,0xd192e819U,0xd6990624U,0xf40e3585U,0x106aa070U,
  0x19a4c116U,0x1e376c08U,0x2748774cU,0x34b0bcb5U,0x391c0cb3U,0x4ed8aa4aU,0x5b9cca4fU,0x682e6ff3U,
  0x748f82eeU,0x78a5636fU,0x84c87814U,0x8cc70208U,0x90befffaU,0xa4506cebU,0xbef9a3f7U,0xc67178f2U}};
 static std::uint32_t rotr(std::uint32_t x,int n){return (x>>n)|(x<<(32-n));}
 void transform(){
  std::array<std::uint32_t,64>w{};for(int i=0;i<16;++i)w[i]=(std::uint32_t(data[4*i])<<24)|(std::uint32_t(data[4*i+1])<<16)|(std::uint32_t(data[4*i+2])<<8)|data[4*i+3];
  for(int i=16;i<64;++i){auto s0=rotr(w[i-15],7)^rotr(w[i-15],18)^(w[i-15]>>3);auto s1=rotr(w[i-2],17)^rotr(w[i-2],19)^(w[i-2]>>10);w[i]=w[i-16]+s0+w[i-7]+s1;}
  auto a=h[0],b=h[1],c=h[2],d=h[3],e=h[4],f=h[5],g=h[6],hh=h[7];
  for(int i=0;i<64;++i){auto s1=rotr(e,6)^rotr(e,11)^rotr(e,25);auto ch=(e&f)^((~e)&g);auto t1=hh+s1+ch+k[i]+w[i];auto s0=rotr(a,2)^rotr(a,13)^rotr(a,22);auto maj=(a&b)^(a&c)^(b&c);auto t2=s0+maj;hh=g;g=f;f=e;e=d+t1;d=c;c=b;b=a;a=t1+t2;}
  h[0]+=a;h[1]+=b;h[2]+=c;h[3]+=d;h[4]+=e;h[5]+=f;h[6]+=g;h[7]+=hh;
 }
 void byte(std::uint8_t value){data[used++]=value;if(used==64){transform();bits+=512;used=0;}}
 std::string finish(){
  bits+=u64(used)*8;data[used++]=0x80;if(used>56){while(used<64)data[used++]=0;transform();used=0;}while(used<56)data[used++]=0;for(int i=7;i>=0;--i)data[used++]=(bits>>(8*i))&255;transform();
  std::ostringstream out;out<<std::hex<<std::setfill('0');for(auto value:h)out<<std::setw(8)<<value;return out.str();
 }
};

template<std::size_t Size>std::string dense_digest(const std::array<u64,Size>&values){Sha256 hash;for(auto value:values)for(int byte=0;byte<8;++byte)hash.byte((value>>(8*byte))&255);return hash.finish();}
template<std::size_t Size>u64 mass(const std::array<u64,Size>&values){u64 total=0;for(auto value:values)total+=value;return total;}
template<std::size_t Size>u64 support_size(const std::array<u64,Size>&values){u64 count=0;for(auto value:values)if(value)++count;return count;}

void add_prefix(std::array<u64,CELL_COUNT>&histogram,const Block&block,Charges&charge,u64&increments){int first=unary_value(6,block,charge),second=unary_value(8,block,charge);++histogram[first*P+second];++increments;}
void print_charges(const Charges&charge){std::cout<<"evaluation_add_charges="<<charge.add<<"\nevaluation_subtract_charges="<<charge.sub<<"\nevaluation_negate_charges="<<charge.neg<<"\nevaluation_multiply_charges="<<charge.mul<<"\nevaluation_inverse_lookup_charges="<<charge.inverse_lookup<<"\nevaluation_power_lookup_charges="<<charge.power_lookup<<"\nevaluation_source_record_charges="<<charge.source_record<<"\nevaluation_hash_slot_charges="<<charge.hash_slot<<"\nevaluation_key_compare_charges="<<charge.key_compare<<"\nevaluation_duplicate_link_charges="<<charge.duplicate_link<<"\nevaluation_checksum_word_charges="<<charge.checksum_word<<"\nevaluation_charge_total="<<charge.total()<<"\n";}

int main(){
 struct rlimit limit{};getrlimit(RLIMIT_AS,&limit);if(limit.rlim_cur!=67108864ULL||limit.rlim_max!=67108864ULL)return 70;
 for(int value=0;value<P;++value){POW[value][0]=1;for(int exponent=1;exponent<=25;++exponent)POW[value][exponent]=POW[value][exponent-1]*value%P;}for(int character=0;character<N;++character)for(int index=0;index<N;++index)COEFF[character][index]=coeff(character,index);
 Charges evaluation;std::vector<std::uint32_t>zero;zero.reserve(ZERO_COUNT);for(std::uint32_t code=0;code<390625;++code){auto shape=decode_shape(code);if(!cyclic_bucket(shape,evaluation))zero.push_back(code);}if(zero.size()!=ZERO_COUNT)return 71;
 std::array<u64,CELL_COUNT>ha{},hd{},hb{},hc{};u64 histogram_increments=0;
 for(auto code:zero){auto shape=decode_shape(code);Block A{0,1,&shape};add_prefix(ha,A,evaluation,histogram_increments);}
 for(int jd=0;jd<5;++jd)for(auto code:zero){auto shape=decode_shape(code);Block D{3,pw(GEN,4*jd),&shape};add_prefix(hd,D,evaluation,histogram_increments);}
 for(int jb=0;jb<5;++jb)for(int phase_position=0;phase_position<5;++phase_position)for(auto code:zero){auto shape=decode_shape(code);Block B{1,pw(GEN,4*jb+20*PHASE_ORDER[phase_position]),&shape};add_prefix(hb,B,evaluation,histogram_increments);}
 for(int jc=0;jc<5;++jc)for(auto code:zero){auto shape=decode_shape(code);Block C{2,pw(GEN,4*jc),&shape};add_prefix(hc,C,evaluation,histogram_increments);}
 auto sha_ha=dense_digest(ha),sha_hd=dense_digest(hd),sha_hb=dense_digest(hb),sha_hc=dense_digest(hc);u64 support_ha=support_size(ha),support_hd=support_size(hd),support_hb=support_size(hb),support_hc=support_size(hc),support_sum=support_ha+support_hd+support_hb+support_hc;
 u64 n_pairs=support_ha*support_hd,m_pairs=support_hb*support_hc,convolution_pairs=n_pairs+m_pairs,dense_actions=7ULL*CELL_COUNT,engine_preflight=evaluation.total()+histogram_increments+convolution_pairs+dense_actions+support_sum,combined_preflight=2*engine_preflight+2ULL*CELL_COUNT;
 std::array<u64,CELL_COUNT>n{},m{};std::string status="CAP_ABORT";
 if(combined_preflight<COMBINED_CAP){
  for(int ai=0;ai<CELL_COUNT;++ai)if(ha[ai])for(int di=0;di<CELL_COUNT;++di)if(hd[di]){int first=(ai/P+di/P)%P,second=(ai%P+di%P)%P;n[first*P+second]+=ha[ai]*hd[di];}
  for(int bi=0;bi<CELL_COUNT;++bi)if(hb[bi])for(int ci=0;ci<CELL_COUNT;++ci)if(hc[ci]){int first=(P-(bi/P+ci/P)%P)%P,second=(P-(bi%P+ci%P)%P)%P;m[first*P+second]+=hb[bi]*hc[ci];}
  status="NONPRUNING_CENSUS";
 }
 u64 excluded=0,open=0,realized_shifts=0,excluded_shifts=0,m_compatible_shifts=0,prefix_pair_contexts=0,minimum_m=std::numeric_limits<u64>::max(),maximum_m=0;
 if(status!="CAP_ABORT")for(int cell=0;cell<CELL_COUNT;++cell){if(m[cell])++m_compatible_shifts;if(!n[cell])continue;++realized_shifts;minimum_m=std::min(minimum_m,m[cell]);maximum_m=std::max(maximum_m,m[cell]);prefix_pair_contexts+=n[cell]*m[cell];if(m[cell])open+=n[cell];else{excluded+=n[cell];++excluded_shifts;}}
 if(excluded){status="CERTIFIED_PREFIX_EXCLUSION";}if(!realized_shifts)minimum_m=0;
 std::cout<<"REASONING_AD5_PREFIX_SHIFT_CENSUS_V1\nstatus="<<status<<"\nfield_prime=101\ncell_count="<<CELL_COUNT<<"\nshape_count=390625\nzero_bucket_count="<<zero.size()<<"\na_domain_mass="<<mass(ha)<<"\nd_domain_mass="<<mass(hd)<<"\nb_domain_mass="<<mass(hb)<<"\nc_domain_mass="<<mass(hc)<<"\na_histogram_support="<<support_ha<<"\nd_histogram_support="<<support_hd<<"\nb_histogram_support="<<support_hb<<"\nc_histogram_support="<<support_hc<<"\na_histogram_sha256="<<sha_ha<<"\nd_histogram_sha256="<<sha_hd<<"\nb_histogram_sha256="<<sha_hb<<"\nc_histogram_sha256="<<sha_hc<<"\nn_table_sha256="<<dense_digest(n)<<"\nm_table_sha256="<<dense_digest(m)<<"\nn_table_mass="<<mass(n)<<"\nm_table_mass="<<mass(m)<<"\nn_table_support="<<support_size(n)<<"\nm_table_support="<<support_size(m)<<"\nrealized_shift_count="<<realized_shifts<<"\nexcluded_shift_count="<<excluded_shifts<<"\nbc_compatible_shift_count="<<m_compatible_shifts<<"\nminimum_prefix_pair_count_on_realized_shift="<<minimum_m<<"\nmaximum_prefix_pair_count_on_realized_shift="<<maximum_m<<"\nexcluded_context_count="<<excluded<<"\nopen_context_count="<<open<<"\nprefix_pair_context_count="<<prefix_pair_contexts<<"\nhistogram_increment_count="<<histogram_increments<<"\nn_convolution_pair_count="<<n_pairs<<"\nm_convolution_pair_count="<<m_pairs<<"\nconvolution_pair_count="<<convolution_pairs<<"\ndense_action_count="<<dense_actions<<"\nsupport_action_count="<<support_sum<<"\nengine_preflight_charge="<<engine_preflight<<"\ncombined_preflight_charge="<<combined_preflight<<"\ncombined_charge_cap="<<COMBINED_CAP<<"\n";print_charges(evaluation);std::cout<<"rlimit_as_soft="<<limit.rlim_cur<<"\nrlimit_as_hard="<<limit.rlim_max<<"\nREASONING_END\n";
}
"""
)

_INTEGER_FIELDS = {
    "field_prime",
    "cell_count",
    "shape_count",
    "zero_bucket_count",
    "a_domain_mass",
    "d_domain_mass",
    "b_domain_mass",
    "c_domain_mass",
    "a_histogram_support",
    "d_histogram_support",
    "b_histogram_support",
    "c_histogram_support",
    "n_table_mass",
    "m_table_mass",
    "n_table_support",
    "m_table_support",
    "realized_shift_count",
    "excluded_shift_count",
    "bc_compatible_shift_count",
    "minimum_prefix_pair_count_on_realized_shift",
    "maximum_prefix_pair_count_on_realized_shift",
    "excluded_context_count",
    "open_context_count",
    "prefix_pair_context_count",
    "histogram_increment_count",
    "n_convolution_pair_count",
    "m_convolution_pair_count",
    "convolution_pair_count",
    "dense_action_count",
    "support_action_count",
    "engine_preflight_charge",
    "combined_preflight_charge",
    "combined_charge_cap",
    "evaluation_add_charges",
    "evaluation_subtract_charges",
    "evaluation_negate_charges",
    "evaluation_multiply_charges",
    "evaluation_inverse_lookup_charges",
    "evaluation_power_lookup_charges",
    "evaluation_source_record_charges",
    "evaluation_hash_slot_charges",
    "evaluation_key_compare_charges",
    "evaluation_duplicate_link_charges",
    "evaluation_checksum_word_charges",
    "evaluation_charge_total",
    "rlimit_as_soft",
    "rlimit_as_hard",
}
_HASH_FIELDS = {
    "a_histogram_sha256",
    "d_histogram_sha256",
    "b_histogram_sha256",
    "c_histogram_sha256",
    "n_table_sha256",
    "m_table_sha256",
}


def _parse_transcript(stdout: str) -> dict[str, Any]:
    lines = stdout.strip().splitlines()
    frame = f"REASONING_AD5_PREFIX_SHIFT_CENSUS_V{_PROTOCOL_VERSION}"
    if len(lines) < 3 or lines[0] != frame or lines[-1] != "REASONING_END":
        raise ScientificToolError("AD5 prefix census transcript has invalid framing")
    fields: dict[str, str] = {}
    for line in lines[1:-1]:
        if "=" not in line:
            raise ScientificToolError("AD5 prefix census transcript has an invalid field")
        name, value = line.split("=", 1)
        if name in fields:
            raise ScientificToolError("AD5 prefix census transcript has duplicate fields")
        fields[name] = value
    if set(fields) != {"status", *_INTEGER_FIELDS, *_HASH_FIELDS}:
        raise ScientificToolError("AD5 prefix census transcript is incomplete")
    try:
        parsed: dict[str, Any] = {name: int(fields[name]) for name in _INTEGER_FIELDS}
    except ValueError as error:
        raise ScientificToolError("AD5 prefix census transcript has a nonintegral field") from error
    for name in _HASH_FIELDS:
        value = fields[name]
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ScientificToolError("AD5 prefix census transcript has an invalid digest")
        parsed[name] = value
    parsed["status"] = fields["status"]
    return parsed


def _run_program() -> dict[str, Any]:
    computation = _run_source_hashed_program(
        _PROGRAM,
        label="ad5-prefix-shift-census",
        timeout_seconds=900,
        runtime_address_space_bytes=_RESIDENT_BYTE_CAP,
        preserve_exact_transcript=True,
    )
    return {**computation, **_parse_transcript(computation["transcript"])}


def _run_replay() -> dict[str, Any]:
    replay_path = Path(__file__).with_name(
        "heisenberg_level25_four_block_ad5_prefix_replay.py"
    )
    try:
        program = replay_path.read_bytes()
    except OSError as error:
        raise ScientificToolError(f"independent AD5 prefix replay is unavailable: {error}") from error
    replay_python = shutil.which("python3", path="/usr/local/bin:/usr/bin:/bin") or sys.executable
    command = _address_space_limited_command(
        [replay_python, "-I", "-S", str(replay_path)],
        _RESIDENT_BYTE_CAP,
    )
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
        raise ScientificToolError("independent AD5 prefix replay exceeded 900 seconds") from error
    except OSError as error:
        raise ScientificToolError(f"independent AD5 prefix replay could not start: {error}") from error
    if completed.returncode or completed.stderr.strip():
        detail = (completed.stderr.strip() or completed.stdout.strip() or "unknown replay failure")[:2000]
        raise ScientificToolError(f"independent AD5 prefix replay failed: {detail}")
    try:
        replay = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise ScientificToolError("independent AD5 prefix replay returned invalid JSON") from error
    if not isinstance(replay, dict):
        raise ScientificToolError("independent AD5 prefix replay returned a non-object")
    return {
        **replay,
        "program_sha256": hashlib.sha256(program).hexdigest(),
        "transcript_sha256": hashlib.sha256(completed.stdout.encode()).hexdigest(),
        "transcript": completed.stdout,
        "runtime_address_space_limit_bytes": _RESIDENT_BYTE_CAP,
        "interpreter": str(Path(replay_python).resolve()),
    }


def level25_heisenberg_augmented_four_block_ad5_prefix_shift_census_profile() -> dict[
    str, Any
]:
    """Census the exact first-two-key shift classes in one complete fiber."""

    fourier = level25_heisenberg_augmented_four_block_fourier_profile()
    fourier_sha256 = hashlib.sha256(canonical_json(fourier).encode()).hexdigest()
    phase = level25_heisenberg_augmented_four_block_phase_quotient_profile()
    computation = _run_program()
    replay = _run_replay()
    source_manifest = _source_manifest()
    compared_fields = (
        "status",
        *_INTEGER_FIELDS - {"rlimit_as_soft", "rlimit_as_hard"},
        *_HASH_FIELDS,
    )
    replay_agreement = all(replay.get(name) == computation[name] for name in compared_fields)
    actual_combined_charge = (
        computation["engine_preflight_charge"]
        + replay["engine_preflight_charge"]
        + 2 * _CELL_COUNT
    )
    expected_status = (
        "CERTIFIED_PREFIX_EXCLUSION"
        if computation["excluded_context_count"] > 0
        else "NONPRUNING_CENSUS"
    )
    certificate = {
        "corrected_fourier_domain_is_the_certified_prerequisite": reviewed_profile_matches(fourier, _FOURIER_EVIDENCE_SHA256),
        "phase_quotient_is_the_certified_prerequisite": reviewed_profile_matches(phase, _PHASE_EVIDENCE_SHA256),
        "source_manifest_matches_executable_source": source_manifest[
            "raw_record_count"
        ]
        == 725,
        "primary_and_independent_replay_agree": replay_agreement,
        "all_histogram_and_table_masses_are_exact": computation[
            "a_domain_mass"
        ]
        == 4_420
        and computation["d_domain_mass"] == 22_100
        and computation["b_domain_mass"] == 110_500
        and computation["c_domain_mass"] == 22_100
        and computation["n_table_mass"] == 97_682_000
        and computation["m_table_mass"] == 2_442_050_000
        and computation["excluded_context_count"]
        + computation["open_context_count"]
        == 97_682_000,
        "status_matches_the_complete_exclusion_count": computation["status"]
        == expected_status,
        "combined_preflight_and_execution_respect_the_hard_cap": computation[
            "combined_preflight_charge"
        ]
        == actual_combined_charge
        < _COMBINED_CHARGE_CAP,
        "both_engines_respect_the_hard_address_space_limit": computation[
            "rlimit_as_soft"
        ]
        == computation["rlimit_as_hard"]
        == replay["runtime_address_space_limit_bytes"]
        == _RESIDENT_BYTE_CAP,
        "programs_and_transcripts_match_reviewed_executions": exact_execution_matches(
            computation,
            parser=_parse_transcript,
            expected_program=_REVIEWED_PROGRAM_SHA256,
            expected_transcript=_REVIEWED_TRANSCRIPT_SHA256,
        )
        and independent_replay_matches(
            replay,
            expected_program=_REVIEWED_REPLAY_PROGRAM_SHA256,
            expected_transcript=_REVIEWED_REPLAY_TRANSCRIPT_SHA256,
        ),
    }
    request: dict[str, Any] = {}
    return seal_profile_evidence({
        "schema": "reasoning.level25-heisenberg-augmented-four-block-ad5-prefix-shift-census-profile.v2",
        "request": request,
        "request_sha256": hashlib.sha256(canonical_json(request).encode()).hexdigest(),
        "fourier_receipt_sha256": fourier_sha256,
        "fourier_evidence_sha256": fourier["evidence_sha256"],
        "phase_receipt_sha256": hashlib.sha256(canonical_json(phase).encode()).hexdigest(),
        "phase_evidence_sha256": phase["evidence_sha256"],
        "source_manifest": source_manifest,
        "computation": computation,
        "independent_replay": replay,
        "actual_combined_charge": actual_combined_charge,
        "certificate": certificate,
        "certificate_verified": all(certificate.values()),
        "decision": {
            "status": computation["status"],
            "complete_fixed_fiber_prefix_census": replay_agreement
            and computation["status"] == expected_status,
            "contexts_prefix_excluded": computation["excluded_context_count"],
            "contexts_not_excluded_by_prefix": computation["open_context_count"],
            "full_key_match_or_common_zero_found": False,
        },
        "interpretation": (
            "The first two AD5 key coordinates collapse all 97,682,000 central "
            "A/D contexts in the fixed (0,0,0,0) fiber into exact F_101^2 shift "
            "classes. A shift with zero B/C prefix-pair count excludes every "
            "context in that class; nonzero counts are only necessary-condition "
            "diagnostics."
        ),
        "not_certified": [
            "a full match in AD5 key coordinates three through five",
            "a seven-equation survivor or common zero",
            "any other Fourier fiber or support pattern",
            "geometric points over extensions of F_101 or a characteristic-zero lift",
            "global smoothness, a Brauer class, period, index, or period-index counterexample",
        ],
        "next_gate": {
            "feature": "level25_heisenberg_augmented_four_block_ad5_deeper_shift_census",
            "requirement": (
                "If prefix exclusions occur, restrict full-key work to the open "
                "shift classes; otherwise derive a source-faithful reusable "
                "dependency for key coordinate three without discarding its B-D, "
                "A-C, or D-A pair terms."
            ),
        },
    })
