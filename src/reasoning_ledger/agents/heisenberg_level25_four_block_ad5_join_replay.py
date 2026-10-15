#!/usr/bin/env python3
# ruff: noqa: E501
"""Independent scalar replay of the AD5 five-key covariance certificate."""

from __future__ import annotations

import hashlib
import json

P = 101
N = 25
ZETA = 5
GEN = 2
SHAPES = 4_420
B_STATES = 110_500
C_STATES = 22_100
ENGINE_CAP = 5_000_000

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
KEY_ROWS = (6, 8, 5, 7, 11)
COVERED_ROWS = frozenset((*KEY_ROWS, 0, 9))
EXPECTED_WEIGHTS = {0: 5, 5: 10, 6: 5, 7: 10, 8: 20, 9: 0, 11: 0}
PHASE_ORDER = (0, 3, 1, 4, 2)

ORBIT_RECORD_SHA256 = "fe272b5f7dddd1fc039d83614060520b8eba364ba13a422d48fa39ef6e9a925e"
ORBIT_ARTIFACT_SHA256 = "6e82e5e14d114990c8fa424ece26c9643dcb902d07d8a4d132c14ee81eef2c11"
ORBIT_ZERO_SHAPE_SHA256 = "23acfac609eaa758dc9bede4f47fd60acf8cc7333e5580765f113c1b649175bd"
ORBIT_ACTION_SHA256 = "3a53cc518e2628cd5c7804e78056fd5f93065dffeba7fcd52c0bd8dcd5df73a8"
ORBIT_RECEIPT_SHA256 = "0ae2c3a469647b3824dd3b18c035603097a1f2b2208a1d3de3d5e8b2f285b41f"
SLICE_ARTIFACT_SHA256 = "fd24e7cddf5f88f49b34df34a7de697d41a12eafdd595d712aa2bb89bc2c9560"
SLICE_SHAPE_SHA256 = "f160568c8456b21812d2a67a380f9224bf03f8be39070d5aaf1278fd195c5e15"
SLICE_ACTION_SHA256 = "593a9f252c776d059768f1754577998ea7fb0715b54d92a032a278cce32f2504"
SLICE_REPRESENTATIVE_SHA256 = "a9d128cc5614b2dbffad31c2fa04aa372f5bbacb4397143e4f74e52f23704dc2"
SLICE_RECEIPT_SHA256 = "2a13bda6bded0d2195b84ac3d7c5b5ad6bf16e5d7f7e9dd14096cbfdabe3f41d"
NATIVE_DESIGN_SHA256 = "aae0d42c40889542735feecf702fbc09fd199fdcefe5f627079e349b9f8bfbf6"
CODEX_AUDIT_SHA256 = "e106d0a6024ea148b5b2adcb560b3b560ae50916dfc3b59a6a6d429c52928c9e"


class TaggedHash:
    def __init__(self, tag: str):
        self._hash = hashlib.sha256()
        self.bytes = 0
        self.raw(tag.encode())
        self.byte(0)

    def byte(self, value: int) -> None:
        self._hash.update(bytes((value & 0xFF,)))
        self.bytes += 1

    def raw(self, value: bytes) -> None:
        self._hash.update(value)
        self.bytes += len(value)

    def u32le(self, value: int) -> None:
        self.raw(value.to_bytes(4, "little"))

    def u16le(self, value: int) -> None:
        self.raw(value.to_bytes(2, "little"))

    def u64le(self, value: int) -> None:
        self.raw(value.to_bytes(8, "little"))

    def finish(self) -> str:
        return self._hash.hexdigest()


def source_records() -> list[tuple[int, int, int, tuple[int, ...], tuple[int, ...]]]:
    records: list[tuple[int, int, int, tuple[int, ...], tuple[int, ...]]] = []
    for row, (character, shift) in enumerate(CH):
        for index in range(N):
            records.append(
                (row, character, index, (index, (index - shift) % N), (24, 1))
            )
    for index in range(N):
        records.append((0, 5, index, (index, (index + 1) % N), (20, 5)))
    row_by_character = {character: row for row, character in enumerate(CH)}
    for character, first, second, third in (
        (2, 1, 4, 20),
        (2, 2, 13, 10),
        (2, 4, 6, 15),
        (4, 1, 4, 20),
    ):
        row = row_by_character[(character, 15)]
        for index in range(N):
            records.append(
                (
                    row,
                    character,
                    index,
                    (index, (index + 5) % N, (index + 2) % N),
                    (first, second, third),
                )
            )
    for character, first_offset, second_offset, middle_exponent in (
        (9, 2, 19, 18),
        (7, 1, 2, 23),
    ):
        row = row_by_character[(character, 0)]
        for index in range(N):
            records.append(
                (
                    row,
                    character,
                    index,
                    (index, (index + first_offset) % N, (index + second_offset) % N),
                    (1, middle_exponent, 24 - middle_exponent),
                )
            )
    return records


def support_mask(record: tuple[int, int, int, tuple[int, ...], tuple[int, ...]]) -> int:
    return sum(1 << residue for residue in {index % 5 for index in record[3]})


def classify(record: tuple[int, int, int, tuple[int, ...], tuple[int, ...]]) -> int:
    """Return 0 ignored, 1 B, 2 C, 3 residual, or 4 vanishing unary."""

    row, _character, _start, indices, _exponents = record
    mask = support_mask(record)
    if mask & 16 or row not in COVERED_ROWS:
        return 0
    if row in (6, 8):
        return 1 if mask == 2 else 2 if mask in (1, 4, 8) else 0
    if row in (5, 7):
        return 1 if mask in (2, 10) else 2 if mask in (1, 4, 8, 5, 9) else 0
    if row == 11:
        if len(indices) == 2:
            return 4
        return 1 if mask == 11 else 2 if mask == 13 else 0
    if row == 0:
        return 3
    if row == 9:
        return 4 if len(indices) == 2 else 3 if mask in (7, 14) else 0
    return 0


def degrees(record: tuple[int, int, int, tuple[int, ...], tuple[int, ...]]) -> tuple[int, ...]:
    return tuple(
        sum(exponent for index, exponent in zip(record[3], record[4], strict=True) if index % 5 == residue)
        % 5
        for residue in range(4)
    )


def weight(record: tuple[int, int, int, tuple[int, ...], tuple[int, ...]]) -> int:
    return sum(index * exponent for index, exponent in zip(record[3], record[4], strict=True)) % 25


def coefficient(character: int, index: int) -> int:
    return pow(ZETA, (-character * index) % 25, P)


def decompose_scale(raw: int) -> tuple[int, int]:
    raw %= 25
    residue = raw % 5
    return residue, ((raw - 21 * residue) % 25) // 5


def splitmix(value: int) -> int:
    mask = (1 << 64) - 1
    value = (value + 0x9E3779B97F4A7C15) & mask
    value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & mask
    value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & mask
    return value ^ (value >> 31)


def fnv1a(key: tuple[int, ...]) -> int:
    value = 14_695_981_039_346_656_037
    for byte in key:
        value ^= byte
        value = value * 1_099_511_628_211 & ((1 << 64) - 1)
    return value


def pack_key(key: tuple[int, ...]) -> int:
    packed = 0
    for value in key:
        packed = P * packed + value
    return packed


def record_bytes(key: tuple[int, ...], state_id: int) -> bytes:
    return bytes(key) + state_id.to_bytes(4, "little")


def main() -> None:
    records = source_records()
    record_set = set(records)
    record_ordinal = {record: ordinal for ordinal, record in enumerate(records)}
    restricted = 0
    key_counts = {row: 0 for row in KEY_ROWS}
    b_counts = {row: 0 for row in KEY_ROWS}
    c_counts = {row: 0 for row in KEY_ROWS}
    raw_by_row = [0] * len(CH)
    supported_by_row = [0] * len(CH)
    b_by_row = [0] * len(CH)
    c_by_row = [0] * len(CH)
    residual_by_row = [0] * len(CH)
    vanishing_by_row = [0] * len(CH)
    residual_restricted = 0
    vanishing = 0
    partition_failures = 0
    translation_checks = 0
    restricted_translation_checks = 0
    effective_translation_checks = 0
    translation_failures = 0
    inverse_translation_failures = 0
    total_degree_checks = 0
    total_degree_failures = 0
    block_phase_checks = 0
    block_phase_failures = 0
    source_charge = len(records)
    partition_hash = TaggedHash("AD5C3-SOURCE-PARTITION-v1")
    source_record_hash = TaggedHash("AD5C3-SOURCE-PARTITION-v3")
    translation_hash = TaggedHash("AD5C3-TRANSLATION-CLOSURE-v3")
    key_degrees: dict[int, set[tuple[int, ...]]] = {1: set(), 2: set()}
    residual_degrees: set[tuple[int, ...]] = set()

    for ordinal, record in enumerate(records):
        row, character, start, indices, exponents = record
        side = classify(record)
        supported = not (support_mask(record) & 16)
        raw_by_row[row] += 1
        supported_by_row[row] += supported
        restricted += supported
        if side == 1:
            b_by_row[row] += 1
        elif side == 2:
            c_by_row[row] += 1
        elif side == 3:
            residual_by_row[row] += 1
        elif side == 4:
            vanishing_by_row[row] += 1
        sign = 1 if side in (1, 3) else 100 if side == 2 else 0
        degree = degrees(record)
        exponent_vector = [0] * 25
        for index, exponent in zip(indices, exponents, strict=True):
            exponent_vector[index] = (exponent_vector[index] + exponent) % 256
        source_side = 1 if side == 1 else 2 if side == 2 else 3 if side == 4 else 0
        source_sign = 100 if side == 2 else 1
        source_record_hash.u16le(ordinal)
        for item in (row, source_side, source_sign, coefficient(character, start), character, int(supported)):
            source_record_hash.byte(item)
        for item in exponent_vector:
            source_record_hash.byte(item)
        partition_hash.u32le(ordinal)
        partition_hash.byte(ordinal // 25)
        partition_hash.byte(ordinal % 25)
        partition_hash.byte(int(supported))
        partition_hash.byte(row)
        partition_hash.byte(side)
        partition_hash.byte(sign)
        partition_hash.byte(coefficient(character, start))
        partition_hash.byte(len(indices))
        for index, exponent in zip(indices, exponents, strict=True):
            partition_hash.byte(index)
            partition_hash.byte(exponent)
        partition_hash.byte(start % 5 if side == 4 else 255)
        for action_a in range(5):
            translated = (
                row,
                character,
                (start + 5 * action_a) % 25,
                tuple((index + 5 * action_a) % 25 for index in indices),
                exponents,
            )
            inverse = (
                row,
                character,
                (translated[2] - 5 * action_a) % 25,
                tuple((index - 5 * action_a) % 25 for index in translated[3]),
                exponents,
            )
            translated_coefficient = coefficient(character, translated[2])
            expected_coefficient = coefficient(character, start) * pow(
                ZETA, (-5 * character * action_a) % 25, P
            ) % P
            translated_weight = weight(translated)
            partition_hash.byte(translated_coefficient)
            partition_hash.byte(translated_weight)
            translation_checks += 1
            restricted_translation_checks += supported
            effective_translation_checks += side in (1, 2, 3)
            translation_failures += (
                translated not in record_set
                or bool(support_mask(translated) & 16) == supported
                or classify(translated) != side
                or translated_coefficient != expected_coefficient
                or translated_weight != weight(record)
            )
            inverse_translation_failures += inverse != record
            if supported:
                translation_hash.u16le(ordinal)
                translation_hash.byte(action_a)
                translation_hash.u16le(record_ordinal.get(translated, 65_535))
                translation_hash.byte(pow(ZETA, (-5 * character * action_a) % 25, P))
            source_charge += 1
        total_degree_checks += 1
        total_degree_failures += sum(exponents) != 25
        if not supported or row not in COVERED_ROWS:
            continue
        if not side:
            partition_failures += 1
            continue
        if row in KEY_ROWS:
            key_counts[row] += 1
            if side == 1:
                b_counts[row] += 1
            elif side == 2:
                c_counts[row] += 1
        else:
            residual_restricted += 1
        if side == 4:
            vanishing += 1
        source_charge += 1
        if side in (1, 2, 3):
            partition_failures += weight(record) != EXPECTED_WEIGHTS[row]
        if (side in (1, 2) and row != 11) or (side == 3 and row == 0):
            for item in degree:
                block_phase_checks += 1
                block_phase_failures += item != 0
                source_charge += 1
        if row == 11 and side in (1, 2):
            key_degrees[side].add(degree)
        if row == 9 and side == 3:
            residual_degrees.add(degree)

    row_count_table = ";".join(
        f"{row}:{raw_by_row[row]},{supported_by_row[row]},{b_by_row[row]},{c_by_row[row]},"
        f"{residual_by_row[row]},{vanishing_by_row[row]},"
        f"{raw_by_row[row]-b_by_row[row]-c_by_row[row]-residual_by_row[row]-vanishing_by_row[row]}"
        for row in range(len(CH))
    )
    row_table_hash = TaggedHash("AD5C3-ROW-COUNT-TABLE-v1")
    for row in range(len(CH)):
        row_table_hash.byte(row)
        for value in (
            raw_by_row[row],
            supported_by_row[row],
            b_by_row[row],
            c_by_row[row],
            residual_by_row[row],
            vanishing_by_row[row],
            raw_by_row[row] - b_by_row[row] - c_by_row[row] - residual_by_row[row] - vanishing_by_row[row],
        ):
            row_table_hash.u32le(value)
    row_count_table_sha256 = row_table_hash.finish()

    unary_sum_checks = 0
    unary_sum_terms = 0
    unary_sum_failures = 0
    for row in (11, 9):
        character, shift = CH[row]
        partition_failures += shift != 0 or character % 5 == 0
        for residue in range(4):
            value = 0
            for quotient in range(5):
                value = (value + coefficient(character, residue + 5 * quotient)) % P
                unary_sum_terms += 1
                source_charge += 1
            unary_sum_checks += 1
            unary_sum_failures += value != 0
            partition_hash.byte(row)
            partition_hash.byte(residue)
            partition_hash.byte(value)

    expected_key_counts = {6: 20, 8: 20, 5: 65, 7: 35, 11: 30}
    expected_b_counts = {6: 5, 8: 5, 5: 20, 7: 10, 11: 5}
    expected_c_counts = {6: 15, 8: 15, 5: 45, 7: 25, 11: 5}
    partition_failures += key_counts != expected_key_counts
    partition_failures += b_counts != expected_b_counts
    partition_failures += c_counts != expected_c_counts
    partition_failures += residual_restricted != 65 or vanishing != 40
    partition_failures += row_count_table != (
        "0:50,35,0,0,35,0,15;1:25,20,0,0,0,0,25;2:25,20,0,0,0,0,25;"
        "3:25,20,0,0,0,0,25;4:25,20,0,0,0,0,25;5:100,65,20,45,0,0,35;"
        "6:25,20,5,15,0,0,5;7:50,35,10,25,0,0,15;8:25,20,5,15,0,0,5;"
        "9:50,30,0,0,10,20,20;10:25,20,0,0,0,0,25;11:50,30,5,5,0,20,20;"
        "12:25,20,0,0,0,0,25;13:25,20,0,0,0,0,25;14:25,20,0,0,0,0,25;"
        "15:25,20,0,0,0,0,25;16:25,20,0,0,0,0,25;17:25,20,0,0,0,0,25;"
        "18:25,20,0,0,0,0,25;19:25,20,0,0,0,0,25;20:25,20,0,0,0,0,25;"
        "21:25,20,0,0,0,0,25"
    )
    partition_failures += key_degrees != {1: {(1, 1, 0, 3)}, 2: {(3, 0, 1, 1)}}
    partition_failures += residual_degrees != {(1, 3, 1, 0), (0, 1, 3, 1)}
    source_partition_sha256 = partition_hash.finish()
    source_record_schema_sha256 = source_record_hash.finish()
    translation_closure_sha256 = translation_hash.finish()

    predicate_hash = TaggedHash("AD5C3-FIVE-KEY-PREDICATE-v1")
    predicate_hash.raw(
        b"rows=6,8,5,7,11;"
        b"KB=U6(B)|U8(B)|U5(B)+P5(B,D)|U7(B)+P7(B,D)|Q9(B,D,A);"
        b"KC=-(U6(A)+U6(C)+U6(D)|U8(A)+U8(C)+U8(D)|"
        b"U5(A)+U5(C)+U5(D)+P5(A,C)+P5(D,A)|"
        b"U7(A)+U7(C)+U7(D)+P7(A,C)+P7(D,A)|Q9(D,A,C));"
        b"residual=rows0,9;record=key[5]:u8|state_id:u32le"
    )
    predicate_schema_sha256 = predicate_hash.finish()

    kernel_vector = (1, 2, 3, 4)
    key_b_degree = next(iter(key_degrees[1]))
    key_c_degree = next(iter(key_degrees[2]))
    residual_degree_list = sorted(residual_degrees)
    phase_hash = TaggedHash("AD5C3-PHASE-KERNEL-v3")
    phase_tuple_classifications = 0
    kernel_tuples = 0
    key_phase_checks = 0
    residual_phase_checks = 0
    phase_failures = 0
    phase_charge = 0
    for l0 in range(5):
        for l1 in range(5):
            for l2 in range(5):
                for l3 in range(5):
                    phase = (l0, l1, l2, l3)
                    in_kernel = not sum(
                        first * second for first, second in zip(kernel_vector, phase, strict=True)
                    ) % 5
                    phase_tuple_classifications += 1
                    for item in (*phase, int(in_kernel)):
                        phase_hash.byte(item)
                    if not in_kernel:
                        continue
                    kernel_tuples += 1
                    key_b_factor = sum(first * second for first, second in zip(key_b_degree, phase, strict=True)) % 5
                    key_c_factor = sum(first * second for first, second in zip(key_c_degree, phase, strict=True)) % 5
                    residual_factors = [
                        sum(first * second for first, second in zip(degree, phase, strict=True)) % 5
                        for degree in residual_degree_list
                    ]
                    key_phase_checks += 1
                    residual_phase_checks += 1
                    phase_failures += key_b_factor != key_c_factor
                    phase_failures += residual_factors[0] != residual_factors[1]
                    for item in (key_b_factor, key_c_factor, *residual_factors):
                        phase_hash.byte(item)
                    phase_charge += 2
    phase_kernel_sha256 = phase_hash.finish()

    decompose_hash = TaggedHash("AD5C3-DECOMPOSE-SCALE-TABLE-v1")
    decompose_table_checks = 0
    decompose_table_failures = 0
    for raw in range(25):
        residue, ell = decompose_scale(raw)
        reconstructed = (21 * residue + 5 * ell) % 25
        scale_matches = pow(ZETA, raw, P) == pow(ZETA, 21 * residue + 5 * ell, P)
        decompose_table_failures += reconstructed != raw or not scale_matches
        for item in (raw, residue, ell, reconstructed, int(scale_matches)):
            decompose_hash.byte(item)
        decompose_table_checks += 1
    for h, phase_position in enumerate(PHASE_ORDER):
        phase_exponent = (20 * phase_position) % 100
        canonical_exponent = 15 * h % 25
        scale_matches = pow(GEN, phase_exponent, P) == pow(ZETA, canonical_exponent, P)
        decompose_table_failures += not scale_matches
        for item in (h, phase_position, phase_exponent, canonical_exponent, int(scale_matches)):
            decompose_hash.byte(item)
        decompose_table_checks += 1
    decompose_table_sha256 = decompose_hash.finish()

    scale_hash = TaggedHash("AD5C3-TRIANGULAR-SCALE-v1")
    c_scale_hash = TaggedHash("AD5C3-SCALE-BIJECTION-C-v3")
    b_scale_hash = TaggedHash("AD5C3-SCALE-BIJECTION-B-v3")
    c_fibres = 0
    c_checks = 0
    c_failures = 0
    for delta_c in range(25):
        seen = [0] * 5
        for j_c in range(5):
            j_prime, ell = decompose_scale(21 * j_c + delta_c)
            seen[j_prime] += 1
            scale_hash.byte(delta_c)
            scale_hash.byte(j_c)
            scale_hash.byte(j_prime)
            scale_hash.byte(ell)
            c_scale_hash.byte(delta_c)
            c_scale_hash.byte(j_c)
            c_scale_hash.byte(j_prime)
            c_scale_hash.byte(ell)
            c_checks += 1
        c_fibres += 1
        c_failures += seen != [1] * 5
    b_fibres = 0
    b_checks = 0
    b_failures = 0
    for delta_b in range(25):
        for ell_c in range(5):
            for ell_d in range(5):
                seen = [0] * 25
                for j_b in range(5):
                    for h in range(5):
                        j_prime, ell_b = decompose_scale(21 * j_b + 15 * h + delta_b)
                        h_prime = (2 * ell_b + 3 * ell_c + 4 * ell_d) % 5
                        seen[5 * j_prime + h_prime] += 1
                        for item in (delta_b, ell_c, ell_d, j_b, h, j_prime, h_prime):
                            scale_hash.byte(item)
                            b_scale_hash.byte(item)
                        b_checks += 1
                b_fibres += 1
                b_failures += seen != [1] * 25
    scale_bijection_sha256 = scale_hash.finish()
    c_scale_bijection_sha256 = c_scale_hash.finish()
    b_scale_bijection_sha256 = b_scale_hash.finish()
    scale_charge = decompose_table_checks + c_checks + b_checks

    schema_hash = TaggedHash("AD5C3-LOSSLESS-RECORD-SCHEMA-v1")
    schema_hash.raw(b"key[5]:u8;state_id:u32le")
    key_byte_roundtrip_checks = 0
    key_byte_roundtrip_failures = 0
    schema_charge = 0
    for position in range(5):
        for value in range(P):
            key = [0] * 5
            key[position] = value
            encoded = bytes(key)
            key_byte_roundtrip_failures += encoded[position] != value
            schema_hash.byte(position)
            schema_hash.raw(encoded)
            key_byte_roundtrip_checks += 1
            schema_charge += 1
    state_id_checks = 0
    state_id_failures = 0
    state_id_coverage_failures = 0
    b_seen = bytearray((B_STATES + 7) // 8)
    c_seen = bytearray((C_STATES + 7) // 8)
    for j_b in range(5):
        for h in range(5):
            k_b = (21 * j_b + 15 * h) % 25
            evaluator_scale = pow(GEN, (4 * j_b + 20 * PHASE_ORDER[h]) % 100, P)
            canonical_scale = pow(ZETA, k_b, P)
            for shape in range(SHAPES):
                state_id = (5 * j_b + h) * SHAPES + shape
                work = state_id // SHAPES
                decoded = (state_id % SHAPES, work // 5, work % 5)
                state_id_failures += decoded != (shape, j_b, h)
                state_id_failures += evaluator_scale != canonical_scale
                key = tuple((state_id * (position + 1) + 17 * position + 1) % P for position in range(5))
                encoded = record_bytes(key, state_id)
                state_id_failures += len(encoded) != 9 or tuple(encoded[:5]) != key
                state_id_failures += int.from_bytes(encoded[5:], "little") != state_id
                state_id_coverage_failures += bool(b_seen[state_id >> 3] & (1 << (state_id & 7)))
                b_seen[state_id >> 3] |= 1 << (state_id & 7)
                schema_hash.raw(encoded)
                schema_hash.u32le(shape)
                schema_hash.byte(j_b)
                schema_hash.byte(h)
                schema_hash.byte(k_b)
                state_id_checks += 1
                schema_charge += 1
    for j_c in range(5):
        k_c = 21 * j_c % 25
        evaluator_scale = pow(GEN, 4 * j_c, P)
        canonical_scale = pow(ZETA, k_c, P)
        for shape in range(SHAPES):
            state_id = j_c * SHAPES + shape
            decoded = (state_id % SHAPES, state_id // SHAPES)
            state_id_failures += decoded != (shape, j_c)
            state_id_failures += evaluator_scale != canonical_scale
            key = tuple((state_id * (position + 1) + 17 * position + 2) % P for position in range(5))
            encoded = record_bytes(key, state_id)
            state_id_failures += len(encoded) != 9 or tuple(encoded[:5]) != key
            state_id_failures += int.from_bytes(encoded[5:], "little") != state_id
            state_id_coverage_failures += bool(c_seen[state_id >> 3] & (1 << (state_id & 7)))
            c_seen[state_id >> 3] |= 1 << (state_id & 7)
            schema_hash.raw(encoded)
            schema_hash.u32le(shape)
            schema_hash.byte(j_c)
            schema_hash.byte(k_c)
            state_id_checks += 1
            schema_charge += 1
    state_id_coverage_failures += sum(byte.bit_count() for byte in b_seen) != B_STATES
    state_id_coverage_failures += sum(byte.bit_count() for byte in c_seen) != C_STATES
    schema_hash.raw(b_seen)
    schema_hash.raw(c_seen)
    state_schema_sha256 = schema_hash.finish()

    sentinel_hash = TaggedHash("AD5C3-LINKED-JOIN-SENTINEL-v1")
    key17 = (17, 1, 2, 3, 4)
    key42 = (42, 9, 8, 7, 6)
    key99 = (99, 0, 100, 50, 25)
    collision_key = (1, 100, 99, 98, 97)
    b_keys = (key17, key17, collision_key, key17, key42, key99, key42, (7, 6, 5, 4, 3))
    c_keys = (key17, key42, key42, (8, 6, 5, 4, 3), key99)
    heads = [-1] * 8
    next_record = [-1] * len(b_keys)
    sentinel_failures = 0
    for state_id, key in enumerate(b_keys):
        bucket = fnv1a(key) & 7
        next_record[state_id] = heads[bucket]
        heads[bucket] = state_id
    pair_count = 0
    collision_filter_count = 0
    key_pairs: dict[int, int] = {}
    for c_id, key in enumerate(c_keys):
        packed = pack_key(key)
        bucket = fnv1a(key) & 7
        b_id = heads[bucket]
        while b_id >= 0:
            sentinel_hash.u32le(c_id)
            sentinel_hash.u32le(b_id)
            for value in key:
                sentinel_hash.byte(value)
            equal = b_keys[b_id] == key
            sentinel_hash.byte(int(equal))
            collision_filter_count += not equal
            if equal:
                pair_count += 1
                key_pairs[packed] = key_pairs.get(packed, 0) + 1
            b_id = next_record[b_id]
    sentinel_failures += pair_count != 8
    sentinel_failures += collision_filter_count < 1
    sentinel_failures += key_pairs != {pack_key(key17): 3, pack_key(key42): 4, pack_key(key99): 1}
    join_sentinel_sha256 = sentinel_hash.finish()
    sentinel_charge = len(b_keys) + len(c_keys) + pair_count + collision_filter_count

    manifest_hash = TaggedHash("AD5C3-COVARIANCE-INPUT-MANIFEST-v1")
    for digest in (
        ORBIT_ARTIFACT_SHA256,
        ORBIT_RECORD_SHA256,
        ORBIT_ZERO_SHAPE_SHA256,
        ORBIT_ACTION_SHA256,
        ORBIT_RECEIPT_SHA256,
        SLICE_ARTIFACT_SHA256,
        SLICE_SHAPE_SHA256,
        SLICE_ACTION_SHA256,
        SLICE_REPRESENTATIVE_SHA256,
        SLICE_RECEIPT_SHA256,
        NATIVE_DESIGN_SHA256,
        CODEX_AUDIT_SHA256,
    ):
        manifest_hash.raw(digest.encode() if digest == "TO_BE_REPLACED" else bytes.fromhex(digest))
    input_manifest_sha256 = manifest_hash.finish()

    hash_charge = sum(
        item.bytes
        for item in (
            partition_hash,
            source_record_hash,
            translation_hash,
            predicate_hash,
            phase_hash,
            decompose_hash,
            scale_hash,
            c_scale_hash,
            b_scale_hash,
            schema_hash,
            sentinel_hash,
            manifest_hash,
            row_table_hash,
        )
    )
    engine_charge = source_charge + phase_charge + scale_charge + schema_charge + sentinel_charge + hash_charge
    charge_hash = TaggedHash("AD5C3-COVARIANCE-CHARGES-v1")
    for value in (source_charge, phase_charge, scale_charge, schema_charge, sentinel_charge, hash_charge, engine_charge):
        charge_hash.u64le(value)
    charge_transcript_sha256 = charge_hash.finish()

    key_restricted = sum(key_counts.values())
    key_effective = sum(b_counts.values()) + sum(c_counts.values())
    residual_effective = residual_restricted - 20
    effective = key_effective + residual_effective
    b_count = sum(b_counts.values())
    c_count = sum(c_counts.values())
    success = (
        len(records) == 725
        and restricted == 535
        and key_restricted == 170
        and residual_restricted == 65
        and vanishing == 40
        and key_effective == 150
        and residual_effective == 45
        and effective == 195
        and b_count == 45
        and c_count == 105
        and not partition_failures
        and translation_checks == 3_625
        and restricted_translation_checks == 2_675
        and effective_translation_checks == 975
        and not translation_failures
        and not inverse_translation_failures
        and total_degree_checks == 725
        and not total_degree_failures
        and block_phase_checks == 700
        and not block_phase_failures
        and unary_sum_checks == 8
        and unary_sum_terms == 40
        and not unary_sum_failures
        and kernel_tuples == 125
        and phase_tuple_classifications == 625
        and key_phase_checks == residual_phase_checks == 125
        and not phase_failures
        and decompose_table_checks == 30
        and not decompose_table_failures
        and c_fibres == 25
        and c_checks == 125
        and not c_failures
        and b_fibres == 625
        and b_checks == 15_625
        and not b_failures
        and state_id_checks == B_STATES + C_STATES
        and key_byte_roundtrip_checks == 505
        and not key_byte_roundtrip_failures
        and not state_id_failures
        and not state_id_coverage_failures
        and not sentinel_failures
        and engine_charge < ENGINE_CAP
    )
    status = (
        "T3_FIVE_KEY_PARTITION_AND_TRIANGULAR_COVARIANCE_CERTIFIED"
        if success
        else "JOIN_COVARIANCE_CERTIFICATE_MISMATCH"
    )
    result = {
        "status": status,
        "source_record_count": len(records),
        "restricted_source_record_count": restricted,
        "covered_source_record_count": key_restricted + residual_restricted,
        "key_row_count": len(KEY_ROWS),
        "key_restricted_record_count": key_restricted,
        "residual_restricted_record_count": residual_restricted,
        "vanishing_unary_record_count": vanishing,
        "effective_partition_record_count": effective,
        "key_effective_record_count": key_effective,
        "residual_effective_record_count": residual_effective,
        "b_partition_record_count": b_count,
        "c_partition_record_count": c_count,
        "source_partition_failure_count": partition_failures,
        "translation_check_count": translation_checks,
        "restricted_translation_check_count": restricted_translation_checks,
        "effective_translation_check_count": effective_translation_checks,
        "translation_failure_count": translation_failures,
        "inverse_translation_failure_count": inverse_translation_failures,
        "total_degree_check_count": total_degree_checks,
        "total_degree_failure_count": total_degree_failures,
        "block_phase_check_count": block_phase_checks,
        "block_phase_failure_count": block_phase_failures,
        "unary_sum_check_count": unary_sum_checks,
        "unary_sum_term_count": unary_sum_terms,
        "unary_sum_failure_count": unary_sum_failures,
        "phase_kernel_tuple_count": kernel_tuples,
        "phase_tuple_classification_count": phase_tuple_classifications,
        "key_phase_check_count": key_phase_checks,
        "residual_phase_check_count": residual_phase_checks,
        "phase_kernel_failure_count": phase_failures,
        "decompose_table_check_count": decompose_table_checks,
        "decompose_table_failure_count": decompose_table_failures,
        "c_scale_bijection_fibre_count": c_fibres,
        "c_scale_bijection_check_count": c_checks,
        "c_scale_bijection_failure_count": c_failures,
        "b_scale_bijection_fibre_count": b_fibres,
        "b_scale_bijection_check_count": b_checks,
        "b_scale_bijection_failure_count": b_failures,
        "b_state_count": B_STATES,
        "c_state_count": C_STATES,
        "state_id_check_count": state_id_checks,
        "state_id_failure_count": state_id_failures,
        "state_id_coverage_failure_count": state_id_coverage_failures,
        "key_byte_roundtrip_check_count": key_byte_roundtrip_checks,
        "key_byte_roundtrip_failure_count": key_byte_roundtrip_failures,
        "record_width_bytes": 9,
        "b_record_stream_bytes": B_STATES * 9,
        "c_record_stream_bytes": C_STATES * 9,
        "join_sentinel_b_record_count": len(b_keys),
        "join_sentinel_c_record_count": len(c_keys),
        "join_sentinel_pair_count": pair_count,
        "join_sentinel_collision_filter_count": collision_filter_count,
        "join_sentinel_failure_count": sentinel_failures,
        "representative_count": 3_907_280,
        "join_predicate_equivariance_certified": int(success),
        "representative_outer_domain_authorized": int(success),
        "join_execution_completed": 0,
        "contraction_authorized": 0,
        "common_zero_established": 0,
        "source_charge_total": source_charge,
        "phase_charge_total": phase_charge,
        "scale_bijection_charge_total": scale_charge,
        "schema_charge_total": schema_charge,
        "join_sentinel_charge_total": sentinel_charge,
        "hash_charge_total": hash_charge,
        "engine_preflight_charge": engine_charge,
        "engine_preflight_cap": ENGINE_CAP,
        "join_hash_slot_count": 262_144,
        "join_materialized_array_bytes": 3_051_096,
        "join_charged_array_cap_bytes": 4_099_672,
        "join_two_engine_array_cap_bytes": 8_199_344,
        "join_table_bookkeeping_charge": 2_316_680,
        "orbit_artifact_sha256": ORBIT_ARTIFACT_SHA256,
        "orbit_source_record_sha256": ORBIT_RECORD_SHA256,
        "orbit_zero_shape_sha256": ORBIT_ZERO_SHAPE_SHA256,
        "orbit_shape_action_sha256": ORBIT_ACTION_SHA256,
        "orbit_receipt_sha256": ORBIT_RECEIPT_SHA256,
        "slice_artifact_sha256": SLICE_ARTIFACT_SHA256,
        "slice_shape_sha256": SLICE_SHAPE_SHA256,
        "slice_action_sha256": SLICE_ACTION_SHA256,
        "slice_representative_sha256": SLICE_REPRESENTATIVE_SHA256,
        "slice_receipt_sha256": SLICE_RECEIPT_SHA256,
        "native_design_sha256": NATIVE_DESIGN_SHA256,
        "codex_audit_sha256": CODEX_AUDIT_SHA256,
        "input_manifest_sha256": input_manifest_sha256,
        "source_partition_sha256": source_partition_sha256,
        "source_record_schema_sha256": source_record_schema_sha256,
        "translation_closure_sha256": translation_closure_sha256,
        "row_count_table_sha256": row_count_table_sha256,
        "predicate_schema_sha256": predicate_schema_sha256,
        "phase_kernel_sha256": phase_kernel_sha256,
        "decompose_table_sha256": decompose_table_sha256,
        "scale_bijection_sha256": scale_bijection_sha256,
        "c_scale_bijection_sha256": c_scale_bijection_sha256,
        "b_scale_bijection_sha256": b_scale_bijection_sha256,
        "state_schema_sha256": state_schema_sha256,
        "join_sentinel_sha256": join_sentinel_sha256,
        "charge_transcript_sha256": charge_transcript_sha256,
        "join_predicate_equivariance": "CERTIFIED" if success else "UNPROVED",
        "row_count_table": row_count_table,
    }
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
