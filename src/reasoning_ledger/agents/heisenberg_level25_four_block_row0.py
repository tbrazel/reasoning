# ruff: noqa: E501
"""Bounded exact feasibility profile for the augmented four-block row-0 join."""

from __future__ import annotations

import hashlib
from array import array
from typing import Any

from ..canonical import canonical_json
from .heisenberg_level25 import FINITE_FIELD_PRIME, MODULUS, PRIME, ROOT_OF_UNITY, _finite_field_power
from .heisenberg_level25_four_block import _UNARY_EVIDENCE_SHA256, level25_heisenberg_augmented_four_block_unary_convolution_profile
from .heisenberg_level25_three_block import _PROGRAM_PREFIX, _run_source_hashed_program
from .science_evidence import exact_execution_matches, reviewed_profile_matches, seal_profile_evidence
from .scientific import ScientificToolError

_PROTOCOL_VERSION = 1
_REVIEWED_PROGRAM_SHA256 = "12550321269b7111d9567b190637c89e8cf2c788030d0b11f5f80068b3738d71"
_REVIEWED_TRANSCRIPT_SHA256 = "f9119e90096afaa08760714f04bf74a2b93c40f6bdaf2d1bcaa77ba72a0d12b6"
_ROW0_FEASIBILITY_EVIDENCE_SHA256 = "e904ad668cb66292c867f01d7a018a0a37af44ee3d9a572349bb652b4e4a87d7"
_CALIBRATION_FIBERS = (
    (0, 0, 0, 0),
    (0, 1, 2, 3),
    (1, 0, 0, 0),
    (22, 1, 2, 3),
    (100, 3, 2, 1),
)
_SLICE_LABELS = (0, 1, 17, 100, 624)

_PROGRAM = (
    _PROGRAM_PREFIX
    + r"""
#include <bit>
#include <string>

using u64=std::uint64_t;
constexpr int LABELS=625,ORBITS=21,ROW_KEYS=P*P;
constexpr std::size_t HALF_BITS=std::size_t(LABELS)*ROW_KEYS*ORBITS;
constexpr std::array<int,4>KERNEL{{1,96,31,17}};
constexpr std::array<int,4>SCALE25{{1,10,100,91}};
constexpr std::array<std::array<int,4>,5>FIBERS{{{{0,0,0,0}},{{0,1,2,3}},{{1,0,0,0}},{{22,1,2,3}},{{100,3,2,1}}}};
constexpr std::array<int,5>SLICE_LABELS{{0,1,17,100,624}};

struct ShapeR{std::uint8_t bucket;std::uint16_t label;std::array<std::uint8_t,4>b6,b8;};
struct HalfSummary{u64 support=0,checksum=1469598103934665603ULL,maximum_label_support=0;std::array<u64,LABELS*ORBITS>by_label_orbit{};std::array<u64,5>slice_support{},slice_checksum{};};

std::array<int,5>decode_r(std::uint32_t code,std::uint16_t&label){std::array<int,5>u{{1}};int place=1;label=0;for(int q=1;q<5;++q){int exponent=code%25;code/=25;u[q]=pw(ZETA,exponent);label+=(exponent%5)*place;place*=5;}return u;}
std::size_t half_index(int label,int first,int second,int orbit){return ((std::size_t(label)*P+first)*P+second)*ORBITS+orbit;}

HalfSummary summarize(const std::vector<u64>&bits){
 HalfSummary result;for(auto&value:result.slice_checksum)value=1469598103934665603ULL;
 for(std::size_t word_index=0;word_index<bits.size();++word_index){u64 word=bits[word_index];if(word){mix(result.checksum,word_index);mix(result.checksum,word);}while(word){int offset=std::countr_zero(word);std::size_t index=word_index*64+offset;word&=word-1;if(index>=HALF_BITS)continue;++result.support;int orbit=index%ORBITS;std::size_t quotient=index/ORBITS;int label=quotient/ROW_KEYS;int row_key=quotient%ROW_KEYS;++result.by_label_orbit[label*ORBITS+orbit];for(int slice=0;slice<5;++slice)if(label==SLICE_LABELS[slice]){++result.slice_support[slice];mix(result.slice_checksum[slice],row_key*ORBITS+orbit);}}
 }
 for(int label=0;label<LABELS;++label){u64 count=0;for(int orbit=0;orbit<ORBITS;++orbit)count+=result.by_label_orbit[label*ORBITS+orbit];result.maximum_label_support=std::max(result.maximum_label_support,count);}
 return result;
}

int main(){
 std::array<int,5>mu5{};for(int exponent=0;exponent<5;++exponent)mu5[exponent]=pw(pw(ZETA,5),exponent);
 std::array<int,P>orbit{};orbit.fill(-1);orbit[0]=0;int orbit_count=1;for(int value=1;value<P;++value)if(orbit[value]<0){for(int phase=0;phase<5;++phase)orbit[value*mu5[phase]%P]=orbit_count;++orbit_count;}
 if(orbit_count!=ORBITS)return 31;
 std::array<std::array<std::uint8_t,5>,LABELS>label_values{};for(int label=0;label<LABELS;++label){int work=label;label_values[label][0]=1;for(int q=1;q<5;++q){label_values[label][q]=mu5[work%5];work/=5;}}
 std::vector<std::uint8_t>edge(LABELS*LABELS);std::array<bool,P>edge_support{};u64 edge_checksum=1469598103934665603ULL;for(int first=0;first<LABELS;++first)for(int second=0;second<LABELS;++second){int value=0;for(int q=0;q<5;++q)value=(value+pw(label_values[first][q],P-2)*label_values[second][q])%P;edge[first*LABELS+second]=value;edge_support[value]=true;mix(edge_checksum,first);mix(edge_checksum,second);mix(edge_checksum,value);}
 int edge_value_support=0;for(bool present:edge_support)edge_value_support+=present;
 std::array<bool,125>phase_seen{};for(int j1=0;j1<5;++j1)for(int j2=0;j2<5;++j2)for(int j3=0;j3<5;++j3){int n1=j1,n2=(4*j1+j2)%5,n3=(4*j2+j3)%5;phase_seen[n1+5*n2+25*n3]=true;}bool phase_bijection=std::all_of(phase_seen.begin(),phase_seen.end(),[](bool value){return value;});
 std::vector<ShapeR>shapes(390625);std::array<std::vector<std::uint32_t>,P>buckets;std::array<u64,LABELS>label_counts{};for(std::uint32_t code=0;code<shapes.size();++code){std::uint16_t label=0;auto u=decode_r(code,label);std::array<int,5>inverse{};for(int q=0;q<5;++q)inverse[q]=pw(u[q],24);int bucket=0;for(int q=0;q<5;++q)bucket=(bucket+inverse[q]*u[(q+1)%5])%P;ShapeR shape{};shape.bucket=bucket;shape.label=label;for(int residue=0;residue<4;++residue){int first=0,second=0;for(int q=0;q<5;++q){int coordinate=residue+5*q;first=(first+coeff(3,coordinate)*inverse[q]%P*u[(q+1)%5])%P;second=(second+coeff(6,coordinate)*inverse[q]%P*u[(q+4)%5])%P;}shape.b6[residue]=first;shape.b8[residue]=second;}shapes[code]=shape;buckets[bucket].push_back(code);++label_counts[label];}
 for(u64 count:label_counts)if(count!=625)return 32;
 std::vector<u64>bits((HALF_BITS+63)/64);std::array<u64,5>left_loops{},right_loops{},left_supports{},right_supports{},left_checksums{},right_checksums{},left_maxima{},right_maxima{},probe_counts{};std::array<std::array<u64,5>,5>left_slice_support{},right_slice_support{},left_slice_checksum{},right_slice_checksum{};std::array<std::array<int,4>,5>populations{};u64 selected_visits=0;
 for(int fiber_index=0;fiber_index<5;++fiber_index){auto fiber=FIBERS[fiber_index];int t=fiber[0];std::array<int,4>classes{{0,fiber[1],fiber[2],fiber[3]}},targets{{t,0,0,0}};for(int residue=1;residue<4;++residue)targets[residue]=t*KERNEL[residue]%P*pw(SCALE25[classes[residue]],P-2)%P;for(int residue=0;residue<4;++residue){populations[fiber_index][residue]=buckets[targets[residue]].size();selected_visits+=populations[fiber_index][residue];}
  int wB=SCALE25[classes[1]],wC=SCALE25[classes[2]],wD=SCALE25[classes[3]];left_loops[fiber_index]=u64(populations[fiber_index][0])*populations[fiber_index][1];right_loops[fiber_index]=u64(populations[fiber_index][2])*populations[fiber_index][3];
  std::fill(bits.begin(),bits.end(),0);for(std::uint32_t ia:buckets[targets[0]])for(std::uint32_t ib:buckets[targets[1]]){const auto&a=shapes[ia];const auto&b=shapes[ib];int first=(a.b6[0]+wB*b.b6[1])%P,second=(a.b8[0]+wB*b.b8[1])%P,edge_orbit=orbit[edge[a.label*LABELS+b.label]];std::size_t index=half_index(b.label,first,second,edge_orbit);bits[index>>6]|=u64(1)<<(index&63);}
  HalfSummary left=summarize(bits);left_supports[fiber_index]=left.support;left_checksums[fiber_index]=left.checksum;left_maxima[fiber_index]=left.maximum_label_support;left_slice_support[fiber_index]=left.slice_support;left_slice_checksum[fiber_index]=left.slice_checksum;
  u64 probes=0;for(int xB=0;xB<LABELS;++xB)for(int xC=0;xC<LABELS;++xC){int middle_orbit=orbit[edge[xB*LABELS+xC]],middle_values=middle_orbit?5:1;for(int edge_orbit=0;edge_orbit<ORBITS;++edge_orbit){int left_values=edge_orbit?5:1;probes+=left.by_label_orbit[xB*ORBITS+edge_orbit]*left_values*middle_values;}}probe_counts[fiber_index]=probes;
  std::fill(bits.begin(),bits.end(),0);for(std::uint32_t ic:buckets[targets[2]])for(std::uint32_t id:buckets[targets[3]]){const auto&c=shapes[ic];const auto&d=shapes[id];int first=(wC*c.b6[2]+wD*d.b6[3])%P,second=(wC*c.b8[2]+wD*d.b8[3])%P,edge_orbit=orbit[edge[c.label*LABELS+d.label]];std::size_t index=half_index(c.label,first,second,edge_orbit);bits[index>>6]|=u64(1)<<(index&63);}
  HalfSummary right=summarize(bits);right_supports[fiber_index]=right.support;right_checksums[fiber_index]=right.checksum;right_maxima[fiber_index]=right.maximum_label_support;right_slice_support[fiber_index]=right.slice_support;right_slice_checksum[fiber_index]=right.slice_checksum;
 }
 int row0_factor=0;for(int residue=0;residue<4;++residue)row0_factor=(row0_factor+coeff(5,residue)*KERNEL[residue])%P;
 auto print5=[](const auto&values){for(int index=0;index<5;++index){if(index)std::cout<<",";std::cout<<values[index];}std::cout<<"\n";};auto print25=[&](const auto&values){bool first=true;for(const auto&row:values)for(auto value:row){if(!first)std::cout<<",";first=false;std::cout<<value;}std::cout<<"\n";};
 std::cout<<"REASONING_AUGMENTED_FOUR_BLOCK_ROW0_FEASIBILITY_V1\nshape_count="<<shapes.size()<<"\nlabel_count="<<LABELS<<"\nlabel_multiplicity=625\nfield_orbit_count="<<orbit_count<<"\nedge_value_support="<<edge_value_support<<"\nedge_table_term_count="<<LABELS*LABELS*5<<"\nedge_checksum="<<edge_checksum<<"\nphase_map_bijection="<<phase_bijection<<"\nvisible_scale_choice_count=125\ninvisible_phase_factor=5\nrow0_unary_kernel_factor="<<row0_factor<<"\nbitset_word_count="<<bits.size()<<"\n";
 std::cout<<"bucket_populations=";bool first=true;for(const auto&row:populations)for(int value:row){if(!first)std::cout<<",";first=false;std::cout<<value;}std::cout<<"\nselected_shape_visit_count="<<selected_visits<<"\nleft_pair_loops=";print5(left_loops);std::cout<<"right_pair_loops=";print5(right_loops);u64 records=0;for(int index=0;index<5;++index)records+=left_loops[index]+right_loops[index];std::cout<<"half_pair_record_generation_count="<<records<<"\nsupport_summary_cell_count="<<u64(5)*LABELS*LABELS*ORBITS<<"\nleft_support_counts=";print5(left_supports);std::cout<<"right_support_counts=";print5(right_supports);std::cout<<"left_maximum_fixed_label_supports=";print5(left_maxima);std::cout<<"right_maximum_fixed_label_supports=";print5(right_maxima);std::cout<<"prospective_probe_counts=";print5(probe_counts);std::cout<<"left_support_checksums=";print5(left_checksums);std::cout<<"right_support_checksums=";print5(right_checksums);std::cout<<"left_slice_support_counts=";print25(left_slice_support);std::cout<<"right_slice_support_counts=";print25(right_slice_support);std::cout<<"left_slice_checksums=";print25(left_slice_checksum);std::cout<<"right_slice_checksums=";print25(right_slice_checksum);std::cout<<"REASONING_END\n";
}
"""
)


def _parse_int_list(value: str, *, count: int, label: str) -> list[int]:
    try:
        parsed = [int(item) for item in value.split(",")]
    except ValueError as error:
        raise ScientificToolError(f"row-0 feasibility transcript has a nonintegral {label}") from error
    if len(parsed) != count:
        raise ScientificToolError(f"row-0 feasibility transcript has a malformed {label}")
    return parsed


def _parse_transcript(stdout: str) -> dict[str, Any]:
    lines = stdout.strip().splitlines()
    frame = f"REASONING_AUGMENTED_FOUR_BLOCK_ROW0_FEASIBILITY_V{_PROTOCOL_VERSION}"
    if len(lines) < 3 or lines[0] != frame or lines[-1] != "REASONING_END":
        raise ScientificToolError("row-0 feasibility transcript has invalid framing")
    fields: dict[str, str] = {}
    for line in lines[1:-1]:
        if "=" not in line:
            raise ScientificToolError("row-0 feasibility transcript has an invalid field")
        name, value = line.split("=", 1)
        if name in fields:
            raise ScientificToolError("row-0 feasibility transcript has duplicate fields")
        fields[name] = value
    list_fields = {
        "bucket_populations": 20,
        "left_pair_loops": 5,
        "right_pair_loops": 5,
        "left_support_counts": 5,
        "right_support_counts": 5,
        "left_maximum_fixed_label_supports": 5,
        "right_maximum_fixed_label_supports": 5,
        "prospective_probe_counts": 5,
        "left_support_checksums": 5,
        "right_support_checksums": 5,
        "left_slice_support_counts": 25,
        "right_slice_support_counts": 25,
        "left_slice_checksums": 25,
        "right_slice_checksums": 25,
    }
    integer_fields = {
        "shape_count",
        "label_count",
        "label_multiplicity",
        "field_orbit_count",
        "edge_value_support",
        "edge_table_term_count",
        "edge_checksum",
        "phase_map_bijection",
        "visible_scale_choice_count",
        "invisible_phase_factor",
        "row0_unary_kernel_factor",
        "bitset_word_count",
        "selected_shape_visit_count",
        "half_pair_record_generation_count",
        "support_summary_cell_count",
    }
    if set(fields) != integer_fields | set(list_fields):
        raise ScientificToolError("row-0 feasibility transcript is incomplete")
    try:
        parsed: dict[str, Any] = {name: int(fields[name]) for name in integer_fields}
    except ValueError as error:
        raise ScientificToolError("row-0 feasibility transcript has a nonintegral field") from error
    parsed.update({name: _parse_int_list(fields[name], count=count, label=name) for name, count in list_fields.items()})
    return parsed


def _run_program() -> dict[str, Any]:
    computation = _run_source_hashed_program(_PROGRAM, label="augmented-four-block-row0-feasibility", timeout_seconds=600, preserve_exact_transcript=True)
    return {**computation, **_parse_transcript(computation["transcript"])}


def _mix(checksum: int, value: int) -> int:
    for byte in range(8):
        checksum ^= (value >> (8 * byte)) & 255
        checksum = checksum * 1099511628211 & ((1 << 64) - 1)
    return checksum


def _python_replay() -> dict[str, Any]:
    prime = FINITE_FIELD_PRIME
    roots = [pow(ROOT_OF_UNITY, exponent, prime) for exponent in range(MODULUS)]
    mu5 = [pow(pow(ROOT_OF_UNITY, 5, prime), exponent, prime) for exponent in range(5)]
    orbit = [-1] * prime
    orbit[0] = 0
    orbit_count = 1
    for value in range(1, prime):
        if orbit[value] >= 0:
            continue
        for phase in mu5:
            orbit[value * phase % prime] = orbit_count
        orbit_count += 1
    label_values = []
    for label in range(625):
        work = label
        values = [1]
        for _ in range(4):
            values.append(mu5[work % 5])
            work //= 5
        label_values.append(values)
    edge = array("B")
    edge_checksum = 1469598103934665603
    edge_support = set()
    for first, first_values in enumerate(label_values):
        for second, second_values in enumerate(label_values):
            value = sum(pow(first_values[q], prime - 2, prime) * second_values[q] for q in range(5)) % prime
            edge.append(value)
            edge_support.add(value)
            for item in (first, second, value):
                edge_checksum = _mix(edge_checksum, item)
    buckets: list[list[int]] = [[] for _ in range(prime)]
    labels = array("H", [0]) * (MODULUS ** (PRIME - 1))
    row6 = [array("B", [0]) * len(labels) for _ in range(4)]
    row8 = [array("B", [0]) * len(labels) for _ in range(4)]
    for code in range(len(labels)):
        work = code
        shape = [1]
        label = 0
        place = 1
        for _ in range(4):
            exponent = work % MODULUS
            work //= MODULUS
            shape.append(roots[exponent])
            label += exponent % 5 * place
            place *= 5
        labels[code] = label
        inverses = [pow(value, MODULUS - 1, prime) for value in shape]
        bucket = sum(inverses[q] * shape[(q + 1) % 5] for q in range(5)) % prime
        buckets[bucket].append(code)
        for residue in range(4):
            row6[residue][code] = sum(
                _finite_field_power(-3 * (residue + 5 * q)) * inverses[q] * shape[(q + 1) % 5] for q in range(5)
            ) % prime
            row8[residue][code] = sum(
                _finite_field_power(-6 * (residue + 5 * q)) * inverses[q] * shape[(q - 1) % 5] for q in range(5)
            ) % prime
    scale25 = (1, 10, 100, 91)
    kernel = (1, 96, 31, 17)
    left_counts: list[int] = []
    right_counts: list[int] = []
    left_checksums: list[int] = []
    right_checksums: list[int] = []
    populations: list[int] = []
    for t, c1, c2, c3 in _CALIBRATION_FIBERS:
        classes = (0, c1, c2, c3)
        targets = [t] + [t * kernel[residue] * pow(scale25[classes[residue]], prime - 2, prime) % prime for residue in range(1, 4)]
        populations.extend(len(buckets[target]) for target in targets)
        for central_label in _SLICE_LABELS:
            keys: set[int] = set()
            central = [code for code in buckets[targets[1]] if labels[code] == central_label]
            for first_code in buckets[targets[0]]:
                first_label = labels[first_code]
                for central_code in central:
                    first = (row6[0][first_code] + scale25[c1] * row6[1][central_code]) % prime
                    second = (row8[0][first_code] + scale25[c1] * row8[1][central_code]) % prime
                    edge_orbit = orbit[edge[first_label * 625 + central_label]]
                    keys.add((first * prime + second) * 21 + edge_orbit)
            checksum = 1469598103934665603
            for key in sorted(keys):
                checksum = _mix(checksum, key)
            left_counts.append(len(keys))
            left_checksums.append(checksum)
            keys = set()
            central = [code for code in buckets[targets[2]] if labels[code] == central_label]
            for central_code in central:
                for last_code in buckets[targets[3]]:
                    first = (scale25[c2] * row6[2][central_code] + scale25[c3] * row6[3][last_code]) % prime
                    second = (scale25[c2] * row8[2][central_code] + scale25[c3] * row8[3][last_code]) % prime
                    edge_orbit = orbit[edge[central_label * 625 + labels[last_code]]]
                    keys.add((first * prime + second) * 21 + edge_orbit)
            checksum = 1469598103934665603
            for key in sorted(keys):
                checksum = _mix(checksum, key)
            right_counts.append(len(keys))
            right_checksums.append(checksum)
    return {
        "field_orbit_count": orbit_count,
        "edge_value_support": len(edge_support),
        "edge_checksum": edge_checksum,
        "bucket_populations": populations,
        "left_slice_support_counts": left_counts,
        "right_slice_support_counts": right_counts,
        "left_slice_checksums": left_checksums,
        "right_slice_checksums": right_checksums,
    }


def level25_heisenberg_augmented_four_block_row0_feasibility_profile() -> dict[str, Any]:
    """Measure exact half-key supports for the bounded row-0 calibration fibers."""

    prerequisite = level25_heisenberg_augmented_four_block_unary_convolution_profile()
    prerequisite_sha256 = hashlib.sha256(canonical_json(prerequisite).encode()).hexdigest()
    computation = _run_program()
    independent = _python_replay()
    populations = computation["bucket_populations"]
    expected_left = [populations[index] * populations[index + 1] for index in range(0, len(populations), 4)]
    expected_right = [populations[index + 2] * populations[index + 3] for index in range(0, len(populations), 4)]
    prospective_probe_cap = 2**31
    certificate = {
        "certified_unary_filter_is_the_prerequisite": reviewed_profile_matches(prerequisite, _UNARY_EVIDENCE_SHA256),
        "program_and_transcript_match_reviewed_computation": exact_execution_matches(computation, parser=_parse_transcript, expected_program=_REVIEWED_PROGRAM_SHA256, expected_transcript=_REVIEWED_TRANSCRIPT_SHA256),
        "complete_row0_unary_term_is_retained": computation["row0_unary_kernel_factor"] == 62,
        "reduced_scale_map_is_bijective_and_old_phase_is_invisible": computation["phase_map_bijection"] == 1 and computation["visible_scale_choice_count"] == 125 and computation["invisible_phase_factor"] == 5,
        "mu5_label_and_edge_orbit_domains_are_exhaustive": computation["label_count"] == computation["label_multiplicity"] == 625 and computation["field_orbit_count"] == 21 and computation["edge_value_support"] == 65,
        "five_fibers_have_the_predeclared_bounded_loop_count": computation["left_pair_loops"] == expected_left and computation["right_pair_loops"] == expected_right and computation["half_pair_record_generation_count"] == sum(expected_left) + sum(expected_right) == 165865150,
        "support_keys_fit_the_proved_fixed_label_bound": max(computation["left_maximum_fixed_label_supports"] + computation["right_maximum_fixed_label_supports"]) <= FINITE_FIELD_PRIME**2 * 21,
        "independent_python_inputs_and_slice_replay_agree": all(computation[name] == independent[name] for name in independent),
        "every_calibration_fiber_exceeds_the_predeclared_probe_cap": all(count > prospective_probe_cap for count in computation["prospective_probe_counts"]),
    }
    request: dict[str, Any] = {}
    verified = all(certificate.values())
    return seal_profile_evidence({
        "schema": "reasoning.level25-heisenberg-augmented-four-block-row0-feasibility-profile.v2",
        "request": request,
        "request_sha256": hashlib.sha256(canonical_json(request).encode()).hexdigest(),
        "unary_receipt_sha256": prerequisite_sha256,
        "unary_evidence_sha256": prerequisite["evidence_sha256"],
        "calibration_fibers": [list(fiber) for fiber in _CALIBRATION_FIBERS],
        "slice_labels": list(_SLICE_LABELS),
        "computation": computation,
        "independent_python_replay": independent,
        "certificate": certificate,
        "certificate_verified": verified,
        "execution_limits": {
            "primary_wall_seconds": 600,
            "primary_hard_address_space_bytes": None,
            "independent_python_replay": "in_process_without_a_separate_hard_memory_or_wall_limit",
        },
        "engineering_caps": {
            "scope": "prospective_all_fiber_hash_join; not hard limits of the calibration or in-process Python replay",
            "generated_half_records": 2**31,
            "prospective_dictionary_probes": prospective_probe_cap,
            "resident_bytes": 96 * 1024**3,
            "wall_seconds": 24 * 60 * 60,
        },
        "decision": {
            "status": "ROW0_HASH_JOIN_REJECTED" if verified else "PROFILE_INVALID",
            "reason": (
                "Every predeclared calibration fiber requires between 165,050,497,755 and 225,214,888,665 prospective dictionary probes after exact mu_5 orbit compression, exceeding the 2^31 cap before an all-fiber support census begins."
            ) if verified else "The mathematical certificate failed; no hash-join conclusion is authorized.",
        },
        "interpretation": (
            "The five predeclared corrected Fourier fibers measure the exact support and prospective lookup work of the complete row-0 half-key join after rows 6 and 8. Reduced scales are compressed through 21 exact mu_5 edge-value orbits, while the old five-element phase is proved invisible to row 0. All five fibers exceed the prospective-probe cap, so this profile rejects the hash join without executing or authorizing the all-fiber count."
        ),
        "not_certified": [
            "feasibility or completion of the all-fiber row-0 support census or counting join",
            "satisfaction of rows 5 and 7 or the selected Q7 and Q9 couplings",
            "a common-zero count or Jacobian-rank census on exact four-block support",
            "absence of F_101-rational or geometric four-block common zeros",
            "the full-support rational stratum or any characteristic-zero lift",
            "a Brauer class, period, index bound, or period-index counterexample",
        ],
        "next_gate": {
            "feature": "level25_heisenberg_augmented_four_block_row0_algebraic_projection",
            "requirement": (
                "Replace the rejected central-label hash join by a different exact algebraic projection, such as a character-sum or low-rank transfer contraction, whose predeclared bound does not enumerate survivor quadruples or exceed 2^31 core operations."
            ),
        },
    })
