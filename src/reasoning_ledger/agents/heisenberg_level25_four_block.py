# ruff: noqa: E501
"""Exact unary-equation convolution on the augmented four-block domain."""

from __future__ import annotations

import hashlib
from array import array
from fractions import Fraction
from typing import Any

from ..canonical import canonical_json
from .heisenberg_level25 import (
    FINITE_FIELD_PRIME,
    MODULUS,
    PRIME,
    ROOT_OF_UNITY,
    _finite_field_power,
)
from .heisenberg_level25_four_block_ad5 import _FOURIER_EVIDENCE_SHA256
from .heisenberg_level25_three_block import (
    _PROGRAM_PREFIX,
    _run_source_hashed_program,
)
from .heisenberg_level25_three_block_coverage import (
    level25_heisenberg_augmented_four_block_fourier_profile,
)
from .science_evidence import exact_execution_matches, reviewed_profile_matches, seal_profile_evidence
from .scientific import ScientificToolError

_PROTOCOL_VERSION = 1
_UNARY_EVIDENCE_SHA256 = "f39b6bf089f400ef9677502a4b5caee5173071136795abb0c113ef747bf0f542"
_REVIEWED_PROGRAM_SHA256 = "7a4ed2e351722786fbc74dc144a5d9c0968497188abc342879161e825a25dc6b"
_REVIEWED_TRANSCRIPT_SHA256 = "a603309428767f5466448a9ea2db956d38f886cafb9c221568c74a6e7a0a4949"
_REVIEWED_HISTOGRAM_CHECKSUM = 6465411173863107267
_REVIEWED_FIBER_CHECKSUM = 1051450695276653208
_TRANSFORM_MODULI = (1449072215711168621, 1449072215711172257)
_CALIBRATION_FIBERS = (
    (0, 0, 0, 0),
    (0, 1, 2, 3),
    (1, 0, 0, 0),
    (22, 1, 2, 3),
    (100, 3, 2, 1),
)

_PROGRAM = (
    _PROGRAM_PREFIX
    + r"""
#include <atomic>
#include <string>
#include <thread>

using u64=std::uint64_t;
using u128=unsigned __int128;
constexpr int PAIRS=P*P,TRANSFORM_COUNT=4*P,FIBER_COUNT=P*4*4*4,WORKERS=32;
constexpr std::array<int,4> KERNEL{{1,96,31,17}};
constexpr std::array<int,4> SCALE25{{1,10,100,91}};
constexpr std::array<u64,2> MODQ{{1449072215711168621ULL,1449072215711172257ULL}};
constexpr std::array<std::array<int,4>,5> CALIBRATION{{{{0,0,0,0}},{{0,1,2,3}},{{1,0,0,0}},{{22,1,2,3}},{{100,3,2,1}}}};

u64 mulq(u64 a,u64 b,u64 q){return (u128)a*b%q;}
u64 powq(u64 a,u64 e,u64 q){u64 result=1;while(e){if(e&1)result=mulq(result,a,q);a=mulq(a,a,q);e>>=1;}return result;}
bool prime64(u64 n){
 if(n<2)return false;
 for(u64 p:std::array<u64,12>{{2,3,5,7,11,13,17,19,23,29,31,37}}){if(n%p==0)return n==p;}
 u64 d=n-1,s=0;while(!(d&1)){d>>=1;++s;}
 for(u64 a:std::array<u64,7>{{2,325,9375,28178,450775,9780504,1795265022}}){
  if(a%n==0)continue;u64 x=powq(a%n,d,n);if(x==1||x==n-1)continue;bool witness=true;
  for(u64 r=1;r<s;++r){x=mulq(x,x,n);if(x==n-1){witness=false;break;}}
  if(witness)return false;
 }
 return true;
}
u64 addq(u64 a,u64 b,u64 q){a+=b;if(a>=q)a-=q;return a;}
std::string decimal128(u128 value){if(!value)return "0";std::string out;while(value){out.push_back(char('0'+value%10));value/=10;}std::reverse(out.begin(),out.end());return out;}
std::size_t hist_index(int residue,int bucket,int first,int second){return ((std::size_t(residue)*P+bucket)*PAIRS+first+P*second);}
std::size_t transform_index(int residue,int bucket,int first,int second){return hist_index(residue,bucket,first,second);}

std::vector<std::uint32_t> build_histograms(std::array<u64,P>&bucket_counts){
 std::vector<std::uint32_t> hist(std::size_t(TRANSFORM_COUNT)*PAIRS);
 for(std::uint32_t code=0;code<390625;++code){
  std::uint32_t work=code;std::array<int,5>u{{1}};for(int quotient=1;quotient<5;++quotient){u[quotient]=pw(ZETA,work%25);work/=25;}
  int cyclic=0;for(int quotient=0;quotient<5;++quotient)cyclic=(cyclic+pw(u[quotient],24)*u[(quotient+1)%5])%P;
  ++bucket_counts[cyclic];
  for(int residue=0;residue<4;++residue){
   int row6=0,row8=0;
   for(int quotient=0;quotient<5;++quotient){
    int coordinate=residue+5*quotient,inverse=pw(u[quotient],24);
    row6=(row6+coeff(3,coordinate)*inverse%P*u[(quotient+1)%5])%P;
    row8=(row8+coeff(6,coordinate)*inverse%P*u[(quotient+4)%5])%P;
   }
   ++hist[hist_index(residue,cyclic,row6,row8)];
  }
 }
 return hist;
}

struct TransformResult{u64 total=0,zero=0,minimum=0,maximum=0,fiber_checksum=1469598103934665603ULL,transform_checksum=1469598103934665603ULL;std::array<u64,5>calibration{};};

TransformResult count_modulus(const std::vector<std::uint32_t>&hist,u64 modulus){
 u64 root=powq(2,(modulus-1)/P,modulus),inverse_pairs=powq(PAIRS,modulus-2,modulus);
 std::array<u64,PAIRS>weight{};for(int frequency=0;frequency<P;++frequency)for(int value=0;value<P;++value)weight[frequency*P+value]=powq(root,frequency*value,modulus);
 std::vector<u64> transformed(std::size_t(TRANSFORM_COUNT)*PAIRS);
 std::atomic<int> next{0};std::vector<std::thread>workers;
 for(int worker=0;worker<WORKERS;++worker)workers.emplace_back([&]{
  std::array<u64,PAIRS>temporary{};
  while(true){int transform=next.fetch_add(1);if(transform>=TRANSFORM_COUNT)break;const std::uint32_t*source=&hist[std::size_t(transform)*PAIRS];u64*target=&transformed[std::size_t(transform)*PAIRS];
   for(int first=0;first<P;++first)for(int second_frequency=0;second_frequency<P;++second_frequency){u64 sum=0;for(int second=0;second<P;++second)sum=addq(sum,mulq(source[first+P*second],weight[second_frequency*P+second],modulus),modulus);temporary[first+P*second_frequency]=sum;}
   for(int first_frequency=0;first_frequency<P;++first_frequency)for(int second_frequency=0;second_frequency<P;++second_frequency){u64 sum=0;for(int first=0;first<P;++first)sum=addq(sum,mulq(temporary[first+P*second_frequency],weight[first_frequency*P+first],modulus),modulus);target[first_frequency+P*second_frequency]=sum;}
  }
 });
 for(auto&worker:workers)worker.join();
 TransformResult result;for(u64 value:transformed)mix(result.transform_checksum,value);
 result.minimum=modulus;
 for(int bucket=0;bucket<P;++bucket)for(int c1=0;c1<4;++c1)for(int c2=0;c2<4;++c2)for(int c3=0;c3<4;++c3){
  std::array<int,4>classes{{0,c1,c2,c3}},targets{{bucket,0,0,0}};for(int residue=1;residue<4;++residue)targets[residue]=bucket*KERNEL[residue]%P*pw(SCALE25[classes[residue]],P-2)%P;
  u64 frequency_sum=0;
  for(int first_frequency=0;first_frequency<P;++first_frequency)for(int second_frequency=0;second_frequency<P;++second_frequency){
   u64 product=transformed[transform_index(0,targets[0],first_frequency,second_frequency)];
   for(int residue=1;residue<4;++residue){int scaled_first=first_frequency*SCALE25[classes[residue]]%P,scaled_second=second_frequency*SCALE25[classes[residue]]%P;product=mulq(product,transformed[transform_index(residue,targets[residue],scaled_first,scaled_second)],modulus);}
   frequency_sum=addq(frequency_sum,product,modulus);
  }
  u64 count=mulq(frequency_sum,inverse_pairs,modulus);result.total+=count;if(bucket==0)result.zero+=count;result.minimum=std::min(result.minimum,count);result.maximum=std::max(result.maximum,count);mix(result.fiber_checksum,bucket);mix(result.fiber_checksum,c1);mix(result.fiber_checksum,c2);mix(result.fiber_checksum,c3);mix(result.fiber_checksum,count);
  for(int index=0;index<(int)CALIBRATION.size();++index)if(CALIBRATION[index]==std::array<int,4>{{bucket,c1,c2,c3}})result.calibration[index]=count;
 }
 return result;
}

int main(){
 std::array<u64,P>bucket_counts{};auto hist=build_histograms(bucket_counts);u64 histogram_checksum=1469598103934665603ULL,support_checksum=1469598103934665603ULL;int support_minimum=PAIRS,support_maximum=0;
 for(int residue=0;residue<4;++residue)for(int bucket=0;bucket<P;++bucket){u64 total=0;int support=0;for(int first=0;first<P;++first)for(int second=0;second<P;++second){u64 count=hist[hist_index(residue,bucket,first,second)];mix(histogram_checksum,residue);mix(histogram_checksum,bucket);mix(histogram_checksum,first);mix(histogram_checksum,second);mix(histogram_checksum,count);total+=count;if(count)++support;}if(total!=bucket_counts[bucket])return 21;support_minimum=std::min(support_minimum,support);support_maximum=std::max(support_maximum,support);mix(support_checksum,residue);mix(support_checksum,bucket);mix(support_checksum,support);}
 bool prime_checks=true,root_checks=true;std::array<u64,2>roots{};for(int i=0;i<2;++i){prime_checks=prime_checks&&prime64(MODQ[i]);roots[i]=powq(2,(MODQ[i]-1)/P,MODQ[i]);root_checks=root_checks&&roots[i]!=1&&powq(roots[i],P,MODQ[i])==1;}
 if(!prime_checks||!root_checks)return 22;auto first=count_modulus(hist,MODQ[0]);auto second=count_modulus(hist,MODQ[1]);if(first.total!=second.total||first.zero!=second.zero||first.minimum!=second.minimum||first.maximum!=second.maximum||first.fiber_checksum!=second.fiber_checksum||first.calibration!=second.calibration)return 23;
 u128 setting_count=u128(first.total)*625;int row0_factor=0;for(int residue=0;residue<4;++residue)row0_factor=(row0_factor+coeff(5,residue)*KERNEL[residue])%P;
 std::cout<<"REASONING_AUGMENTED_FOUR_BLOCK_UNARY_CONVOLUTION_V1\n";
 std::cout<<"shape_count=";u64 shape_count=0;for(u64 value:bucket_counts)shape_count+=value;std::cout<<shape_count<<"\n";
 std::cout<<"fiber_count="<<FIBER_COUNT<<"\nworker_count="<<WORKERS<<"\ntransform_moduli="<<MODQ[0]<<","<<MODQ[1]<<"\ntransform_roots="<<roots[0]<<","<<roots[1]<<"\nmoduli_prime="<<prime_checks<<","<<prime_checks<<"\nroots_have_order_101="<<root_checks<<","<<root_checks<<"\nmodulus_fiber_agreement=1\n";
 std::cout<<"histogram_checksum="<<histogram_checksum<<"\nsupport_checksum="<<support_checksum<<"\nhistogram_support_minimum="<<support_minimum<<"\nhistogram_support_maximum="<<support_maximum<<"\ntransform_checksums="<<first.transform_checksum<<","<<second.transform_checksum<<"\n";
 std::cout<<"shape_class_survivor_count="<<first.total<<"\nzero_line_shape_class_survivor_count="<<first.zero<<"\nnonzero_line_shape_class_survivor_count="<<first.total-first.zero<<"\nscale_phase_setting_count="<<decimal128(setting_count)<<"\nminimum_fiber_survivor_count="<<first.minimum<<"\nmaximum_fiber_survivor_count="<<first.maximum<<"\nfiber_checksum="<<first.fiber_checksum<<"\ncalibration_fiber_counts=";for(int i=0;i<(int)first.calibration.size();++i){if(i)std::cout<<",";std::cout<<first.calibration[i];}std::cout<<"\nrow0_unary_kernel_factor="<<row0_factor<<"\nREASONING_END\n";
}
"""
)


def _parse_int_list(value: str, *, count: int, label: str) -> list[int]:
    try:
        parsed = [int(item) for item in value.split(",")]
    except ValueError as error:
        raise ScientificToolError(f"four-block unary transcript has a nonintegral {label}") from error
    if len(parsed) != count:
        raise ScientificToolError(f"four-block unary transcript has a malformed {label}")
    return parsed


def _parse_transcript(stdout: str) -> dict[str, Any]:
    lines = stdout.strip().splitlines()
    frame = f"REASONING_AUGMENTED_FOUR_BLOCK_UNARY_CONVOLUTION_V{_PROTOCOL_VERSION}"
    if len(lines) < 3 or lines[0] != frame or lines[-1] != "REASONING_END":
        raise ScientificToolError("four-block unary transcript has invalid framing")
    fields: dict[str, str] = {}
    for line in lines[1:-1]:
        if "=" not in line:
            raise ScientificToolError("four-block unary transcript has an invalid field")
        name, value = line.split("=", 1)
        if name in fields:
            raise ScientificToolError("four-block unary transcript has duplicate fields")
        fields[name] = value
    list_fields = {
        "transform_moduli": 2,
        "transform_roots": 2,
        "moduli_prime": 2,
        "roots_have_order_101": 2,
        "transform_checksums": 2,
        "calibration_fiber_counts": len(_CALIBRATION_FIBERS),
    }
    integer_fields = {
        "shape_count",
        "fiber_count",
        "worker_count",
        "modulus_fiber_agreement",
        "histogram_checksum",
        "support_checksum",
        "histogram_support_minimum",
        "histogram_support_maximum",
        "shape_class_survivor_count",
        "zero_line_shape_class_survivor_count",
        "nonzero_line_shape_class_survivor_count",
        "scale_phase_setting_count",
        "minimum_fiber_survivor_count",
        "maximum_fiber_survivor_count",
        "fiber_checksum",
        "row0_unary_kernel_factor",
    }
    if set(fields) != integer_fields | set(list_fields):
        raise ScientificToolError("four-block unary transcript is incomplete")
    try:
        parsed: dict[str, Any] = {name: int(fields[name]) for name in integer_fields}
    except ValueError as error:
        raise ScientificToolError("four-block unary transcript has a nonintegral field") from error
    parsed.update({name: _parse_int_list(fields[name], count=count, label=name) for name, count in list_fields.items()})
    return parsed


def _run_program() -> dict[str, Any]:
    computation = _run_source_hashed_program(
        _PROGRAM,
        label="augmented-four-block-unary-convolution",
        timeout_seconds=180,
        preserve_exact_transcript=True,
    )
    return {**computation, **_parse_transcript(computation["transcript"])}


def _shape_histograms() -> tuple[array, list[int], dict[str, int]]:
    pair_count = FINITE_FIELD_PRIME**2
    histograms = array("I", [0]) * (4 * FINITE_FIELD_PRIME * pair_count)
    bucket_counts = [0] * FINITE_FIELD_PRIME
    roots = [pow(ROOT_OF_UNITY, exponent, FINITE_FIELD_PRIME) for exponent in range(MODULUS)]

    def index(residue: int, bucket: int, first: int, second: int) -> int:
        return (residue * FINITE_FIELD_PRIME + bucket) * pair_count + first + FINITE_FIELD_PRIME * second

    for code in range(MODULUS ** (PRIME - 1)):
        work = code
        shape = [1]
        for _quotient in range(1, PRIME):
            shape.append(roots[work % MODULUS])
            work //= MODULUS
        inverses = [pow(value, MODULUS - 1, FINITE_FIELD_PRIME) for value in shape]
        bucket = sum(inverses[q] * shape[(q + 1) % PRIME] for q in range(PRIME)) % FINITE_FIELD_PRIME
        bucket_counts[bucket] += 1
        for residue in range(4):
            row6 = (
                sum(_finite_field_power(-3 * (residue + PRIME * q)) * inverses[q] * shape[(q + 1) % PRIME] for q in range(PRIME))
                % FINITE_FIELD_PRIME
            )
            row8 = (
                sum(_finite_field_power(-6 * (residue + PRIME * q)) * inverses[q] * shape[(q - 1) % PRIME] for q in range(PRIME))
                % FINITE_FIELD_PRIME
            )
            histograms[index(residue, bucket, row6, row8)] += 1
    checksum = 1469598103934665603
    support_checksum = 1469598103934665603
    support_sizes = []
    for residue in range(4):
        for bucket in range(FINITE_FIELD_PRIME):
            total = 0
            support = 0
            for first in range(FINITE_FIELD_PRIME):
                for second in range(FINITE_FIELD_PRIME):
                    count = histograms[index(residue, bucket, first, second)]
                    for value in (residue, bucket, first, second, count):
                        for byte in range(8):
                            checksum ^= (value >> (8 * byte)) & 255
                            checksum = checksum * 1099511628211 & ((1 << 64) - 1)
                    total += count
                    support += count != 0
            if total != bucket_counts[bucket]:
                raise ScientificToolError("Python unary histogram has an inconsistent bucket total")
            support_sizes.append(support)
            for value in (residue, bucket, support):
                for byte in range(8):
                    support_checksum ^= (value >> (8 * byte)) & 255
                    support_checksum = support_checksum * 1099511628211 & ((1 << 64) - 1)
    return (
        histograms,
        bucket_counts,
        {
            "histogram_checksum": checksum,
            "support_checksum": support_checksum,
            "histogram_support_minimum": min(support_sizes),
            "histogram_support_maximum": max(support_sizes),
        },
    )


def _direct_fiber_count(histograms: array, fiber: tuple[int, int, int, int]) -> int:
    prime = FINITE_FIELD_PRIME
    pair_count = prime**2
    kernel = (1, 96, 31, 17)
    scale_classes = (1, 10, 100, 91)
    bucket, c1, c2, c3 = fiber
    classes = (0, c1, c2, c3)

    def support(residue: int) -> list[tuple[int, int, int]]:
        scale = scale_classes[classes[residue]]
        target = bucket if residue == 0 else bucket * kernel[residue] * pow(scale, prime - 2, prime) % prime
        start = (residue * prime + target) * pair_count
        values = []
        for second in range(prime):
            for first in range(prime):
                count = histograms[start + first + prime * second]
                if count:
                    values.append((first * scale % prime, second * scale % prime, count))
        return values

    blocks = [support(residue) for residue in range(4)]
    pair_sums = [0] * pair_count
    for first_a, second_a, count_a in blocks[0]:
        for first_b, second_b, count_b in blocks[1]:
            key = (first_a + first_b) % prime + prime * ((second_a + second_b) % prime)
            pair_sums[key] += count_a * count_b
    total = 0
    for first_c, second_c, count_c in blocks[2]:
        for first_d, second_d, count_d in blocks[3]:
            key = (-(first_c + first_d)) % prime + prime * (-(second_c + second_d) % prime)
            total += pair_sums[key] * count_c * count_d
    return total


def _python_replay() -> dict[str, Any]:
    histograms, bucket_counts, summary = _shape_histograms()
    calibration = [_direct_fiber_count(histograms, fiber) for fiber in _CALIBRATION_FIBERS]
    row0_factor = (
        sum(_finite_field_power(-5 * residue) * kernel_value for residue, kernel_value in enumerate((1, 96, 31, 17))) % FINITE_FIELD_PRIME
    )
    return {
        "shape_count": sum(bucket_counts),
        "bucket_counts": bucket_counts,
        **summary,
        "calibration_fibers": [list(fiber) for fiber in _CALIBRATION_FIBERS],
        "calibration_fiber_counts": calibration,
        "row0_unary_kernel_factor": row0_factor,
    }


def _is_prime_64(value: int) -> bool:
    if value < 2:
        return False
    for prime in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if value % prime == 0:
            return value == prime
    odd_part = value - 1
    twos = 0
    while odd_part % 2 == 0:
        odd_part //= 2
        twos += 1
    for base in (2, 325, 9375, 28178, 450775, 9780504, 1795265022):
        witness = pow(base % value, odd_part, value)
        if witness in (1, value - 1):
            continue
        for _ in range(twos - 1):
            witness = witness * witness % value
            if witness == value - 1:
                break
        else:
            return False
    return True


def level25_heisenberg_augmented_four_block_unary_convolution_profile() -> dict[str, Any]:
    """Count exact Fourier-domain four-block tuples surviving unary rows 6 and 8."""

    prerequisite = level25_heisenberg_augmented_four_block_fourier_profile()
    prerequisite_sha256 = hashlib.sha256(canonical_json(prerequisite).encode()).hexdigest()
    computation = _run_program()
    independent = _python_replay()
    domain_count = prerequisite["computation"]["admissible_shape_class_quadruple_count"]
    line_domains = {
        "all": domain_count,
        "zero": prerequisite["computation"]["zero_line_shape_class_quadruple_count"],
        "nonzero": prerequisite["computation"]["nonzero_line_shape_class_quadruple_count"],
    }
    line_survivors = {
        "all": computation["shape_class_survivor_count"],
        "zero": computation["zero_line_shape_class_survivor_count"],
        "nonzero": computation["nonzero_line_shape_class_survivor_count"],
    }
    independence_baseline = Fraction(1, FINITE_FIELD_PRIME**2)
    survivor_ratios = {}
    for line in ("all", "zero", "nonzero"):
        ratio = Fraction(line_survivors[line], line_domains[line])
        relative_deviation = ratio / independence_baseline - 1
        survivor_ratios[line] = {
            "survivors": line_survivors[line],
            "domain": line_domains[line],
            "ratio_numerator": ratio.numerator,
            "ratio_denominator": ratio.denominator,
            "ratio_decimal": float(ratio),
            "independence_baseline_numerator": 1,
            "independence_baseline_denominator": FINITE_FIELD_PRIME**2,
            "relative_deviation_numerator": relative_deviation.numerator,
            "relative_deviation_denominator": relative_deviation.denominator,
            "relative_deviation_decimal": float(relative_deviation),
        }
    first_candidate = domain_count + 1
    first_candidate += (1 - first_candidate) % FINITE_FIELD_PRIME
    smaller_transform_candidates = range(
        first_candidate,
        _TRANSFORM_MODULI[0],
        FINITE_FIELD_PRIME,
    )
    certificate = {
        "corrected_fourier_domain_is_the_certified_prerequisite": reviewed_profile_matches(prerequisite, _FOURIER_EVIDENCE_SHA256),
        "program_and_transcript_match_reviewed_computation": exact_execution_matches(
            computation, parser=_parse_transcript, expected_program=_REVIEWED_PROGRAM_SHA256,
            expected_transcript=_REVIEWED_TRANSCRIPT_SHA256,
        ),
        "two_large_transform_moduli_are_prime_and_have_exact_order_101_roots": computation["transform_moduli"] == list(_TRANSFORM_MODULI)
        and computation["moduli_prime"] == [1, 1]
        and computation["roots_have_order_101"] == [1, 1],
        "primary_modulus_is_the_smallest_admissible_transform_prime": _TRANSFORM_MODULI[0] > domain_count
        and _TRANSFORM_MODULI[0] % FINITE_FIELD_PRIME == 1
        and not any(_is_prime_64(candidate) for candidate in smaller_transform_candidates),
        "compiled_and_python_histograms_agree": computation["shape_count"] == independent["shape_count"] == MODULUS ** (PRIME - 1)
        and computation["histogram_checksum"] == independent["histogram_checksum"]
        and computation["support_checksum"] == independent["support_checksum"]
        and computation["histogram_support_minimum"] == independent["histogram_support_minimum"]
        and computation["histogram_support_maximum"] == independent["histogram_support_maximum"],
        "both_moduli_agree_on_every_fiber": computation["fiber_count"] == FINITE_FIELD_PRIME * 4**3
        and computation["modulus_fiber_agreement"] == 1,
        "direct_python_convolutions_match_selected_fibers": computation["calibration_fiber_counts"]
        == independent["calibration_fiber_counts"],
        "survivor_partition_and_scale_phase_extension_are_exact": computation["shape_class_survivor_count"]
        == computation["zero_line_shape_class_survivor_count"] + computation["nonzero_line_shape_class_survivor_count"]
        and computation["scale_phase_setting_count"] == 625 * computation["shape_class_survivor_count"],
        "row0_unary_term_is_retained_for_the_next_join": computation["row0_unary_kernel_factor"]
        == independent["row0_unary_kernel_factor"]
        == 62,
        "reviewed_checksums_match": computation["histogram_checksum"] == _REVIEWED_HISTOGRAM_CHECKSUM
        and computation["fiber_checksum"] == _REVIEWED_FIBER_CHECKSUM,
        "both_fourier_lines_match_the_predeclared_independence_band": all(
            abs(value["relative_deviation_decimal"]) <= 0.01 for value in survivor_ratios.values()
        ),
    }
    request: dict[str, Any] = {}
    profile = {
        "schema": "reasoning.level25-heisenberg-augmented-four-block-unary-convolution-profile.v2",
        "request": request,
        "request_sha256": hashlib.sha256(canonical_json(request).encode()).hexdigest(),
        "fourier_receipt_sha256": prerequisite_sha256,
        "fourier_evidence_sha256": prerequisite["evidence_sha256"],
        "support_residues": [0, 1, 2, 3],
        "unary_rows": [[3, 20], [6, 5]],
        "computation": computation,
        "independent_python_replay": independent,
        "survivor_ratios": survivor_ratios,
        "certificate": certificate,
        "certificate_verified": all(certificate.values()),
        "interpretation": (
            "Across all 6,464 corrected Fourier fibers, the exact two-dimensional additive convolution counts the normalized shape-class quadruples satisfying the unchanged unary rows 6 and 8. Each survivor extends to exactly 625 old-scale and certified phase settings. This is a domain profile for the remaining coupled join, not a common-zero count."
        ),
        "not_certified": [
            "satisfaction of row 0, rows 5 and 7, or the selected Q7 and Q9 couplings",
            "a common-zero count or Jacobian-rank census on exact four-block support",
            "absence of F_101-rational or geometric four-block common zeros",
            "the full-support rational stratum or any characteristic-zero lift",
            "a Brauer class, period, index bound, or period-index counterexample",
        ],
        "next_gate": {
            "feature": "level25_heisenberg_augmented_four_block_row0_join_profile",
            "requirement": (
                "Join the certified unary survivors against the complete row-0 value, including its nonzero 62*t unary contribution on the Fourier kernel, before adding rows 5, 7, Q7, and Q9 and replaying every survivor."
            ),
        },
    }
    return seal_profile_evidence(profile)
