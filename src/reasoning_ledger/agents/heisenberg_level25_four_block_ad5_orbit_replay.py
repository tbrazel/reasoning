"""Independent scalar replay for the AD5 coordinate-three orbit gate.

This file imports no Reasoning evaluator code. It runs in a separate process
under a 64 MiB address-space limit.
"""

from __future__ import annotations

import hashlib
import json
from array import array

P = 101
ZETA = 5
ZERO_COUNT = 4_420
GROUP_ORDER = 25
CENTRAL_STATE_COUNT = 97_682_000
CENTRAL_ORBIT_COUNT = 3_907_280
ENGINE_PREFLIGHT_CAP = 60_000_000
NATIVE_INPUT_SHA256 = "f7fff26b9493e82ccb073b2ecfa1236bc83296a3ee44dacbeacf2e4d3b4fd969"
CENTRAL_KEY_ARTIFACT_PATH = "research/four-block/iteration-125/ad5-coordinate3-central-key-native.md"
CENTRAL_KEY_ARTIFACT_SHA256 = "65658d6de29e771312c63d2f2a89a7dd53df97d96b2e521f0fb0fb8b419ce22d"
CENTRAL_KEY_RECEIPT_SHA256 = "70430e5a35233c5bb6ec2bade5a1511e7e8891045bfc40f2b7956bca008c7e63"
CH = (
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
EXPECTED_ROW_COUNTS = (
    50,
    25,
    25,
    25,
    25,
    100,
    25,
    50,
    25,
    50,
    25,
    50,
    25,
    25,
    25,
    25,
    25,
    25,
    25,
    25,
    25,
    25,
)
EXPECTED_ROW_WEIGHTS = (
    5,
    5,
    5,
    5,
    0,
    10,
    5,
    10,
    20,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
)


class DomainHash:
    def __init__(self, tag: str) -> None:
        self._hash = hashlib.sha256()
        self.bytes = 0
        encoded = tag.encode("ascii")
        self.u64(len(encoded))
        self.raw(encoded)

    def raw(self, value: bytes) -> None:
        self._hash.update(value)
        self.bytes += len(value)

    def byte(self, value: int) -> None:
        self.raw(bytes((value,)))

    def u32(self, value: int) -> None:
        self.raw(value.to_bytes(4, "big"))

    def u64(self, value: int) -> None:
        self.raw(value.to_bytes(8, "big"))

    def finish(self) -> tuple[str, int]:
        return self._hash.hexdigest(), ((self.bytes + 63) // 64) * 1_000


def fnv_mix(checksum: int, value: int) -> int:
    for byte in value.to_bytes(8, "big"):
        checksum ^= byte
        checksum = checksum * 1_099_511_628_211 & ((1 << 64) - 1)
    return checksum


def decode_exponents(code: int) -> tuple[int, int, int, int, int]:
    values = [0] * 5
    for position in range(4, 0, -1):
        values[position] = code % 25
        code //= 25
    return tuple(values)  # type: ignore[return-value]


def encode_exponents(values: tuple[int, ...]) -> int:
    code = 0
    for value in values[1:]:
        code = 25 * code + value
    return code


def is_zero_shape(exponents: tuple[int, ...]) -> bool:
    return (
        sum(
            pow(ZETA, (exponents[(quotient + 1) % 5] - exponents[quotient]) % 25, P)
            for quotient in range(5)
        )
        % P
        == 0
    )


def transform_shape(exponents: tuple[int, ...], a: int, b: int) -> tuple[int, ...]:
    return tuple(
        (exponents[(quotient + a) % 5] - exponents[a] + 5 * b * quotient) % 25
        for quotient in range(5)
    )


def source_records() -> list[tuple[int, int, int, tuple[int, ...], tuple[int, ...]]]:
    records = []
    for row, (character, shift) in enumerate(CH):
        for index in range(25):
            records.append((row, character, index, (index, (index - shift) % 25), (24, 1)))
    for index in range(25):
        records.append((0, 5, index, (index, (index + 1) % 25), (20, 5)))
    row_by_character = {character: row for row, (character, _shift) in enumerate(CH)}
    for character, alpha, beta, gamma in (
        (2, 1, 4, 20),
        (2, 2, 13, 10),
        (2, 4, 6, 15),
        (4, 1, 4, 20),
    ):
        row = row_by_character[character]
        for index in range(25):
            records.append(
                (
                    row,
                    character,
                    index,
                    (index, (index + 5) % 25, (index + 2) % 25),
                    (alpha, beta, gamma),
                )
            )
    for character, first_offset, second_offset, middle_exponent in (
        (9, 2, 19, 18),
        (7, 1, 2, 23),
    ):
        row = row_by_character[character]
        for index in range(25):
            records.append(
                (
                    row,
                    character,
                    index,
                    (index, (index + first_offset) % 25, (index + second_offset) % 25),
                    (1, middle_exponent, 24 - middle_exponent),
                )
            )
    return records


def decompose_scale(raw: int) -> tuple[int, int]:
    residue = raw % 5
    phase = ((raw - 21 * residue) % 25) // 5
    return residue, phase


def canonicalize_scales(raw_b: int, raw_c: int, raw_d: int) -> tuple[int, int, int, int]:
    jb, lb = decompose_scale(raw_b)
    jc, lc = decompose_scale(raw_c)
    jd, ld = decompose_scale(raw_d)
    h = (2 * lb + 3 * lc + 4 * ld) % 5
    return jb, h, jc, jd


def action_offset(shape_index: int, a: int, b: int) -> int:
    return shape_index * GROUP_ORDER + a * 5 + b


def act_state(
    state: tuple[int, int, int, int, int, int, int, int],
    a: int,
    b: int,
    shapes: list[tuple[int, ...]],
    action: array,
) -> tuple[int, int, int, int, int, int, int, int]:
    ai, bi, ci, di, jb, h, jc, jd = state
    indices = (ai, bi, ci, di)
    transformed = tuple(action[action_offset(index, a, b)] for index in indices)
    ea = shapes[ai][a]
    scales = (0, (21 * jb + 15 * h) % 25, (21 * jc) % 25, (21 * jd) % 25)
    raw = tuple(
        (scales[role] + b * role + shapes[indices[role]][a] - ea) % 25
        for role in range(1, 4)
    )
    new_jb, new_h, new_jc, new_jd = canonicalize_scales(*raw)
    return (*transformed, new_jb, new_h, new_jc, new_jd)


def main() -> None:
    hash_charge = 0
    source_charge = 0
    shape_charge = 0
    action_charge = 0
    group_law_charge = 0
    recanonicalization_charge = 0
    orbit_charge = 0
    central_charge = 0

    records = source_records()
    source_charge += sum(4 + 2 * len(record[3]) for record in records)
    record_hash = DomainHash("ad5-orbit/source-records/v1")
    record_hash.u64(len(records))
    for row, character, index, indices, exponents in records:
        for value in (row, character, index, len(indices)):
            record_hash.byte(value)
        for coordinate, exponent in zip(indices, exponents, strict=True):
            record_hash.byte(coordinate)
            record_hash.byte(exponent)
    source_record_stream_sha256, charged = record_hash.finish()
    hash_charge += charged

    record_set = set(records)
    row_counts = [0] * 22
    row_weights: list[set[int]] = [set() for _ in range(22)]
    translation_failures = 0
    residue_piece_failures = 0
    for record in records:
        row, character, index, indices, exponents = record
        row_counts[row] += 1
        row_weights[row].add(sum(value * exponent for value, exponent in zip(indices, exponents, strict=True)) % 25)
        for a in range(5):
            translated = (
                row,
                character,
                (index + 5 * a) % 25,
                tuple((value + 5 * a) % 25 for value in indices),
                exponents,
            )
            translation_failures += translated not in record_set
            source_charge += 1 + len(indices)
        if row in (5, 6, 8):
            degree_by_residue = [0] * 5
            for coordinate, exponent in zip(indices, exponents, strict=True):
                degree_by_residue[coordinate % 5] += exponent
            residue_piece_failures += any(value % 5 for value in degree_by_residue)
            source_charge += len(indices)
    covariance_failures = translation_failures
    for row in range(22):
        covariance_failures += row_counts[row] != EXPECTED_ROW_COUNTS[row]
        covariance_failures += row_weights[row] != {EXPECTED_ROW_WEIGHTS[row]}
        covariance_failures += any(record[1] != CH[row][0] for record in records if record[0] == row)
    covariance_hash = DomainHash("ad5-orbit/source-covariance/v1")
    covariance_hash.u64(22)
    for row in range(22):
        covariance_hash.byte(row)
        covariance_hash.byte(CH[row][0])
        covariance_hash.u64(row_counts[row])
        covariance_hash.byte(next(iter(row_weights[row]), 255))
    covariance_hash.u64(translation_failures)
    covariance_hash.u64(residue_piece_failures)
    source_row_covariance_stream_sha256, charged = covariance_hash.finish()
    hash_charge += charged

    manifest_hash = DomainHash("ad5-orbit/source-manifest/v1")
    manifest_hash.raw(bytes.fromhex(NATIVE_INPUT_SHA256))
    manifest_hash.raw(bytes.fromhex(CENTRAL_KEY_ARTIFACT_SHA256))
    manifest_hash.raw(bytes.fromhex(CENTRAL_KEY_RECEIPT_SHA256))
    manifest_hash.raw(bytes.fromhex(source_record_stream_sha256))
    manifest_hash.u64(len(records))
    source_manifest_sha256, charged = manifest_hash.finish()
    hash_charge += charged

    shapes = []
    codes = []
    index_by_code = array("i", [-1]) * (25**4)
    for code in range(25**4):
        exponents = decode_exponents(code)
        shape_charge += 15
        if is_zero_shape(exponents):
            index_by_code[code] = len(shapes)
            shapes.append(exponents)
            codes.append(code)
    zero_hash = DomainHash("ad5-orbit/zero-shapes/v1")
    zero_hash.u64(len(shapes))
    for code, exponents in zip(codes, shapes, strict=True):
        zero_hash.u32(code)
        zero_hash.raw(bytes(exponents))
    zero_shape_stream_sha256, charged = zero_hash.finish()
    hash_charge += charged

    action = array("I", [0]) * (len(shapes) * GROUP_ORDER)
    closure_failures = 0
    action_hash = DomainHash("ad5-orbit/shape-action/v1")
    action_hash.u64(len(action))
    for shape_index, exponents in enumerate(shapes):
        for a in range(5):
            for b in range(5):
                target_code = encode_exponents(transform_shape(exponents, a, b))
                target = index_by_code[target_code]
                closure_failures += target < 0
                if target < 0:
                    target = 0
                action[action_offset(shape_index, a, b)] = target
                action_hash.u32(shape_index)
                action_hash.byte(a)
                action_hash.byte(b)
                action_hash.u32(target)
                action_charge += 26
    shape_action_table_sha256, charged = action_hash.finish()
    hash_charge += charged

    group_law_checks = 0
    group_law_failures = 0
    group_law_checksum = 14_695_981_039_346_656_037
    for shape_index in range(len(shapes)):
        for a in range(5):
            for b in range(5):
                first = action[action_offset(shape_index, a, b)]
                for c in range(5):
                    for d in range(5):
                        composed = action[action_offset(first, c, d)]
                        direct = action[action_offset(shape_index, (a + c) % 5, (b + d) % 5)]
                        group_law_failures += composed != direct
                        group_law_checks += 1
                        group_law_checksum = fnv_mix(group_law_checksum, direct)
                        group_law_charge += 4
    law_hash = DomainHash("ad5-orbit/shape-group-law/v1")
    for value in (group_law_checks, group_law_failures, group_law_checksum):
        law_hash.u64(value)
    shape_group_law_transcript_sha256, charged = law_hash.finish()
    hash_charge += charged

    orbit_representatives = []
    orbit_sizes: dict[int, int] = {}
    assignments = []
    for shape_index in range(len(shapes)):
        members = {action[action_offset(shape_index, a, b)] for a in range(5) for b in range(5)}
        representative = min(members)
        assignments.append((representative, len(members)))
        if representative == shape_index:
            orbit_representatives.append(shape_index)
            orbit_sizes[len(members)] = orbit_sizes.get(len(members), 0) + 1
        orbit_charge += GROUP_ORDER
    assignment_hash = DomainHash("ad5-orbit/shape-orbit-assignment/v1")
    assignment_hash.u64(len(assignments))
    for shape_index, (representative, size) in enumerate(assignments):
        assignment_hash.u32(shape_index)
        assignment_hash.u32(representative)
        assignment_hash.byte(size)
    shape_orbit_assignment_sha256, charged = assignment_hash.finish()
    hash_charge += charged
    histogram_hash = DomainHash("ad5-orbit/shape-orbit-histogram/v1")
    histogram_hash.u64(len(orbit_sizes))
    for size, count in sorted(orbit_sizes.items()):
        histogram_hash.u64(size)
        histogram_hash.u64(count)
    shape_orbit_histogram_sha256, charged = histogram_hash.finish()
    hash_charge += charged

    phase_hash = DomainHash("ad5-orbit/phase-recanonicalization/v1")
    phase_hash.u64(25**3)
    phase_failures = 0
    for raw_b in range(25):
        for raw_c in range(25):
            for raw_d in range(25):
                jb, h, jc, jd = canonicalize_scales(raw_b, raw_c, raw_d)
                canonical = ((21 * jb + 15 * h) % 25, (21 * jc) % 25, (21 * jd) % 25)
                phases = tuple(((raw - base) % 25) // 5 for raw, base in zip((raw_b, raw_c, raw_d), canonical, strict=True))
                phase_failures += (2 * phases[0] + 3 * phases[1] + 4 * phases[2]) % 5 != 0
                phase_hash.raw(bytes((raw_b, raw_c, raw_d, jb, h, jc, jd, *canonical)))
                recanonicalization_charge += 20

    first = 0
    last = len(shapes) - 1
    corpus = []
    for position, representative in enumerate(orbit_representatives):
        indices = [first, first, last, last]
        indices[position % 4] = representative
        work = position % 625
        scales = []
        for _ in range(4):
            scales.append(work % 5)
            work //= 5
        corpus.append((*indices, *scales))
    size_five_representatives = [
        representative
        for representative in orbit_representatives
        if assignments[representative][1] == 5
    ]
    for representative in size_five_representatives:
        for role in range(4):
            indices = [first, first, last, last]
            indices[role] = representative
            corpus.append((*indices, 0, 0, 0, 0))
    for role in range(4):
        for value in range(25):
            chosen = next(index for index, shape in enumerate(shapes) if value in shape)
            indices = [first, first, last, last]
            indices[role] = chosen
            corpus.append((*indices, value % 5, (value // 5) % 5, role, value % 5))
    for packed in range(625):
        work = packed
        scales = []
        for _ in range(4):
            scales.append(work % 5)
            work //= 5
        indices = (
            orbit_representatives[packed % len(orbit_representatives)],
            (packed * 17 + 1) % len(shapes),
            (packed * 37 + 2) % len(shapes),
            (packed * 73 + 3) % len(shapes),
        )
        corpus.append((*indices, *scales))

    quotient_checks = 0
    quotient_failures = 0
    quotient_checksum = 14_695_981_039_346_656_037
    for state in corpus:
        for a in range(5):
            for b in range(5):
                first_state = act_state(state, a, b, shapes, action)
                for c in range(5):
                    for d in range(5):
                        composed = act_state(first_state, c, d, shapes, action)
                        direct = act_state(state, (a + c) % 5, (b + d) % 5, shapes, action)
                        quotient_failures += composed != direct
                        quotient_checks += 1
                        for value in direct:
                            quotient_checksum = fnv_mix(quotient_checksum, value)
                        recanonicalization_charge += 4
    phase_hash.u64(len(corpus))
    phase_hash.u64(quotient_checks)
    phase_hash.u64(phase_failures + quotient_failures)
    phase_hash.u64(quotient_checksum)
    quotient_recanonicalization_sentinel_sha256, charged = phase_hash.finish()
    hash_charge += charged

    fixed_shape_counts = []
    central_fixed_counts = []
    fixed_hash = DomainHash("ad5-orbit/central-fixed-counts/v1")
    fixed_hash.u64(GROUP_ORDER)
    for a in range(5):
        for b in range(5):
            fixed = [index for index in range(len(shapes)) if action[action_offset(index, a, b)] == index]
            fixed_shape_counts.append(len(fixed))
            if a == 0 and b == 0:
                central_fixed = CENTRAL_STATE_COUNT
            else:
                central_fixed = 0
                for ai in fixed:
                    for di in fixed:
                        for jd in range(5):
                            new_jd = (jd + 3 * b + shapes[di][a] - shapes[ai][a]) % 5
                            central_fixed += new_jd == jd
                            central_charge += 6
            central_fixed_counts.append(central_fixed)
            fixed_hash.byte(a)
            fixed_hash.byte(b)
            fixed_hash.u64(len(fixed))
            fixed_hash.u64(central_fixed)
    central_fixed_count_vector_sha256, charged = fixed_hash.finish()
    hash_charge += charged
    burnside_sum = sum(central_fixed_counts)
    central_orbits = burnside_sum // GROUP_ORDER
    central_free = central_fixed_counts == [CENTRAL_STATE_COUNT, *([0] * 24)]
    burnside_hash = DomainHash("ad5-orbit/central-burnside/v1")
    for value in (CENTRAL_STATE_COUNT, burnside_sum, central_orbits, int(central_free)):
        burnside_hash.u64(value)
    central_burnside_receipt_sha256, charged = burnside_hash.finish()
    hash_charge += charged

    charge_hash = DomainHash("ad5-orbit/charges/v1")
    categories = (
        source_charge,
        shape_charge,
        action_charge,
        group_law_charge,
        recanonicalization_charge,
        orbit_charge,
        central_charge,
        hash_charge,
    )
    for value in categories:
        charge_hash.u64(value)
    charge_transcript_sha256, charged = charge_hash.finish()
    hash_charge += charged
    engine_charge = sum(categories) + charged

    expected_fixed_shapes = [4_420]
    for a in range(5):
        for b in range(5):
            if a == b == 0:
                continue
            expected_fixed_shapes.append(5 if a and b else 0)
    success = (
        len(records) == 725
        and not covariance_failures
        and not residue_piece_failures
        and len(shapes) == ZERO_COUNT
        and not closure_failures
        and not group_law_failures
        and not phase_failures
        and not quotient_failures
        and orbit_sizes == {5: 4, 25: 176}
        and fixed_shape_counts == expected_fixed_shapes
        and central_free
        and central_orbits == CENTRAL_ORBIT_COUNT
        and engine_charge < ENGINE_PREFLIGHT_CAP
    )
    status = (
        "T1_C5SQUARED_COVARIANCE_AND_FREE_CENTRAL_ACTION_CERTIFIED"
        if success
        else "ORBIT_CERTIFICATE_MISMATCH"
    )
    result = {
        "status": status,
        "field_prime": P,
        "shape_count": 25**4,
        "zero_shape_count": len(shapes),
        "group_order": GROUP_ORDER,
        "source_row_count": len(CH),
        "source_record_count": len(records),
        "source_covariance_failure_count": covariance_failures,
        "residue_piece_failure_count": residue_piece_failures,
        "shape_closure_failure_count": closure_failures,
        "shape_group_law_check_count": group_law_checks,
        "shape_group_law_failure_count": group_law_failures,
        "quotient_recanonicalization_state_count": len(corpus),
        "quotient_recanonicalization_check_count": quotient_checks,
        "quotient_recanonicalization_failure_count": phase_failures + quotient_failures,
        "shape_orbit_count": len(orbit_representatives),
        "shape_orbit_size_5_count": orbit_sizes.get(5, 0),
        "shape_orbit_size_25_count": orbit_sizes.get(25, 0),
        "central_state_count": CENTRAL_STATE_COUNT,
        "central_nonidentity_fixed_count": sum(central_fixed_counts[1:]),
        "central_action_free": int(central_free),
        "central_orbit_count": central_orbits,
        "authorized_central_reduction_factor": 25 if central_free else 1,
        "source_charge_total": source_charge,
        "shape_charge_total": shape_charge,
        "action_charge_total": action_charge,
        "group_law_charge_total": group_law_charge,
        "recanonicalization_charge_total": recanonicalization_charge,
        "orbit_charge_total": orbit_charge,
        "central_charge_total": central_charge,
        "hash_charge_total": hash_charge,
        "engine_preflight_charge": engine_charge,
        "engine_preflight_cap": ENGINE_PREFLIGHT_CAP,
        "native_input_sha256": NATIVE_INPUT_SHA256,
        "central_key_artifact_path": CENTRAL_KEY_ARTIFACT_PATH,
        "central_key_artifact_sha256": CENTRAL_KEY_ARTIFACT_SHA256,
        "central_key_receipt_sha256": CENTRAL_KEY_RECEIPT_SHA256,
        "source_manifest_sha256": source_manifest_sha256,
        "source_record_stream_sha256": source_record_stream_sha256,
        "source_row_covariance_stream_sha256": source_row_covariance_stream_sha256,
        "zero_shape_stream_sha256": zero_shape_stream_sha256,
        "shape_action_table_sha256": shape_action_table_sha256,
        "shape_group_law_transcript_sha256": shape_group_law_transcript_sha256,
        "quotient_recanonicalization_sentinel_sha256": quotient_recanonicalization_sentinel_sha256,
        "shape_orbit_assignment_sha256": shape_orbit_assignment_sha256,
        "shape_orbit_histogram_sha256": shape_orbit_histogram_sha256,
        "central_fixed_count_vector_sha256": central_fixed_count_vector_sha256,
        "central_burnside_receipt_sha256": central_burnside_receipt_sha256,
        "charge_transcript_sha256": charge_transcript_sha256,
    }
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
