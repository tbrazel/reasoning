# ruff: noqa: E501
"""Independent scalar replay for the AD5 central-slice orbit preflight."""

from __future__ import annotations

import hashlib
import json
import struct
from array import array

P = 101
ZETA = 5
SHAPE_COUNT = 4_420
SLICE_COUNT = SHAPE_COUNT**2
REPRESENTATIVE_COUNT = SLICE_COUNT // 5
ACTION_TABLE_ENTRIES = SHAPE_COUNT * 5
ACTION_TABLE_BYTES = SHAPE_COUNT * 11
BITMAP_BYTES = SLICE_COUNT // 8
REPRESENTATIVE_BYTES = REPRESENTATIVE_COUNT * 4
ENUMERATOR_CHARGE = 136_776_900
SHAPE_GENERATION_CHARGE = 5_859_375
GROUP_LAW_CHECKS = SHAPE_COUNT * 25 * 25
COCYCLE_CHECKS = GROUP_LAW_CHECKS
UNIQUE_B_CHECKS = 5**4
SHAPE_ENCODER_CHECKS = SHAPE_COUNT
SENTINEL_CHARGE = GROUP_LAW_CHECKS + COCYCLE_CHECKS + UNIQUE_B_CHECKS + SHAPE_ENCODER_CHECKS
ENGINE_CHARGE = ENUMERATOR_CHARGE + SHAPE_GENERATION_CHARGE + SENTINEL_CHARGE
ENGINE_CHARGE_CAP = 150_000_000
COMBINED_CHARGE_CAP = 300_000_000
DECLARED_RESIDENT_BYTES = 18_141_890
FIXED_BUFFER_CAP = 1_048_576
PEAK_ALLOCATION_CAP = DECLARED_RESIDENT_BYTES + FIXED_BUFFER_CAP

ORBIT_ARTIFACT_PATH = "research/four-block/iteration-128/ad5-coordinate3-tensor-orbit-native.md"
ORBIT_ARTIFACT_SHA256 = "6e82e5e14d114990c8fa424ece26c9643dcb902d07d8a4d132c14ee81eef2c11"
ORBIT_RECEIPT_SHA256 = "0ae2c3a469647b3824dd3b18c035603097a1f2b2208a1d3de3d5e8b2f285b41f"
NATIVE_DESIGN_SHA256 = "ca088f7b4fbc32696fff972e92023eda169519343992246a39c1d3b5b33f4a38"
CODEX_CORRECTION_SHA256 = "efeaeaeeeb88c986b72e4c0324300579d86215cc1dd7bd17db94ab31b3065901"

_U32 = struct.Struct("<I")
_EDGE = struct.Struct("<IBI")
_GROUP_SENTINEL = struct.Struct("<IBBBBIIbb")


class DomainHash:
    def __init__(self, tag: str) -> None:
        self._hash = hashlib.sha256()
        self.bytes = 0
        self.raw(tag.encode("ascii"))
        self.byte(0)

    def raw(self, value: bytes | bytearray) -> None:
        self._hash.update(value)
        self.bytes += len(value)

    def byte(self, value: int) -> None:
        self.raw(bytes((value,)))

    def u16(self, value: int) -> None:
        self.raw(value.to_bytes(2, "little"))

    def u32(self, value: int) -> None:
        self.raw(value.to_bytes(4, "little"))

    def u64(self, value: int) -> None:
        self.raw(value.to_bytes(8, "little"))

    def finish(self) -> str:
        return self._hash.hexdigest()


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


def table_target(table: bytearray, shape: int, b: int) -> int:
    offset = shape * 11 + 1 + 2 * b
    return table[offset] | table[offset + 1] << 8


def bitmap_test(bitmap: bytearray, index: int) -> bool:
    return bool(bitmap[index >> 3] & (1 << (index & 7)))


def bitmap_set(bitmap: bytearray, index: int) -> None:
    bitmap[index >> 3] |= 1 << (index & 7)


def flush_buffer(digest: DomainHash, buffer: bytearray) -> None:
    if buffer:
        digest.raw(buffer)
        buffer.clear()


def main() -> None:
    shapes: list[tuple[int, int, int, int, int]] = []
    codes = array("I")
    index_by_code = array("i", [-1]) * (25**4)
    encoder_failures = 0
    shapes_hash = DomainHash("COORD3-SHAPES-MOD25-v2")
    sentinel_hash = DomainHash("COORD3-SENTINELS-v2")
    sentinel_buffer = bytearray()
    for code in range(25**4):
        exponents = decode_exponents(code)
        if is_zero_shape(exponents):
            shape = len(shapes)
            index_by_code[code] = shape
            shapes.append(exponents)
            codes.append(code)
            shapes_hash.raw(bytes(exponents))
            encoded = encode_exponents(exponents)
            encoder_failures += encoded != code or decode_exponents(encoded) != exponents
            sentinel_buffer.extend(_U32.pack(shape))
            sentinel_buffer.extend(_U32.pack(code))
            sentinel_buffer.append(int(encoded == code))

    table = bytearray(ACTION_TABLE_BYTES)
    closure_failures = 0
    ramp_failures = 0
    action_hash = DomainHash("COORD3-A1-ALLB-ACTION-v2")
    for shape, exponents in enumerate(shapes):
        offset = shape * 11
        table[offset] = exponents[1]
        for b in range(5):
            target_word = transform_shape(exponents, 1, b)
            target = index_by_code[encode_exponents(target_word)]
            closure_failures += target < 0
            if target < 0:
                target = 0
            ramp_failures += shapes[target] != target_word
            table[offset + 1 + 2 * b] = target & 255
            table[offset + 2 + 2 * b] = target >> 8
        action_hash.raw(table[offset : offset + 11])

    group_law_failures = 0
    cocycle_failures = 0
    group_law_checks = 0
    cocycle_checks = 0
    for shape, exponents in enumerate(shapes):
        for a in range(5):
            for b in range(5):
                first = transform_shape(exponents, a, b)
                for c in range(5):
                    for d in range(5):
                        composed = transform_shape(first, c, d)
                        direct = transform_shape(exponents, (a + c) % 5, (b + d) % 5)
                        group_law_failures += composed != direct
                        group_law_checks += 1
                        lhs = exponents[(a + c) % 5]
                        rhs = (exponents[a] + first[c] - 5 * b * c) % 25
                        cocycle_failures += lhs != rhs
                        cocycle_checks += 1
                        sentinel_buffer.extend(
                            _GROUP_SENTINEL.pack(
                                shape,
                                a,
                                b,
                                c,
                                d,
                                encode_exponents(composed),
                                encode_exponents(direct),
                                lhs,
                                rhs,
                            )
                        )
                        if len(sentinel_buffer) >= FIXED_BUFFER_CAP:
                            flush_buffer(sentinel_hash, sentinel_buffer)

    unique_b_failures = 0
    unique_b_checks = 0
    for jd in range(5):
        for ea in range(5):
            for ed in range(5):
                chosen = 2 * (-jd - ed + ea) % 5
                solutions = 0
                for candidate in range(5):
                    solutions += (jd + ed - ea + 3 * candidate) % 5 == 0
                    unique_b_checks += 1
                unique_b_failures += solutions != 1 or (jd + ed - ea + 3 * chosen) % 5 != 0
                sentinel_buffer.extend(bytes((jd, ea, ed, chosen, solutions)))
    flush_buffer(sentinel_hash, sentinel_buffer)

    del index_by_code

    bitmap = bytearray(BITMAP_BYTES)
    representatives = array("I")
    edges_hash = DomainHash("COORD3-R1-EDGES-v2")
    edge_buffer = bytearray()
    visited_bits = 0
    image_visits = 0
    collision_failures = 0
    early_return_failures = 0
    power5_failures = 0
    for packed in range(SLICE_COUNT):
        if bitmap_test(bitmap, packed):
            continue
        representatives.append(packed)
        current = packed
        for step in range(5):
            if bitmap_test(bitmap, current):
                collision_failures += 1
            else:
                bitmap_set(bitmap, current)
                visited_bits += 1
            a_shape, d_shape = divmod(current, SHAPE_COUNT)
            beta = 2 * ((table[a_shape * 11] % 5) - (table[d_shape * 11] % 5)) % 5
            next_a = table_target(table, a_shape, beta)
            next_d = table_target(table, d_shape, beta)
            following = next_a * SHAPE_COUNT + next_d
            edge_buffer.extend(_EDGE.pack(current, beta, following))
            if len(edge_buffer) >= FIXED_BUFFER_CAP:
                flush_buffer(edges_hash, edge_buffer)
            image_visits += 1
            if step < 4:
                early_return_failures += following == packed
            else:
                power5_failures += following != packed
            current = following
    flush_buffer(edges_hash, edge_buffer)

    bitmap_hash = DomainHash("COORD3-SLICE-BITMAP-v2")
    bitmap_hash.raw(bitmap)
    representatives_hash = DomainHash("COORD3-REPS-v2")
    representative_buffer = bytearray()
    for representative in representatives:
        representative_buffer.extend(_U32.pack(representative))
        if len(representative_buffer) >= FIXED_BUFFER_CAP:
            flush_buffer(representatives_hash, representative_buffer)
    flush_buffer(representatives_hash, representative_buffer)

    manifest_hash = DomainHash("COORD3-SLICE-INPUT-MANIFEST-v2")
    for value in (
        ORBIT_ARTIFACT_SHA256,
        ORBIT_RECEIPT_SHA256,
        NATIVE_DESIGN_SHA256,
        CODEX_CORRECTION_SHA256,
    ):
        manifest_hash.raw(bytes.fromhex(value))
    manifest_hash.raw(b"e'[q]=e[q+a]-e[a]+5*b*q(mod25);jD'=jD+eD[a]-eA[a]+3*b(mod5)")

    charge_hash = DomainHash("COORD3-SLICE-CHARGES-v2")
    for value in (
        ENUMERATOR_CHARGE,
        SHAPE_GENERATION_CHARGE,
        GROUP_LAW_CHECKS,
        COCYCLE_CHECKS,
        UNIQUE_B_CHECKS,
        SHAPE_ENCODER_CHECKS,
        SENTINEL_CHARGE,
        ENGINE_CHARGE,
    ):
        charge_hash.u64(value)

    success = (
        len(shapes) == SHAPE_COUNT
        and not encoder_failures
        and not closure_failures
        and not ramp_failures
        and group_law_checks == GROUP_LAW_CHECKS
        and not group_law_failures
        and cocycle_checks == COCYCLE_CHECKS
        and not cocycle_failures
        and unique_b_checks == UNIQUE_B_CHECKS
        and not unique_b_failures
        and not collision_failures
        and not early_return_failures
        and not power5_failures
        and visited_bits == SLICE_COUNT
        and len(representatives) == REPRESENTATIVE_COUNT
        and image_visits == SLICE_COUNT
        and ENGINE_CHARGE < ENGINE_CHARGE_CAP
        and 2 * ENGINE_CHARGE < COMBINED_CHARGE_CAP
        and PEAK_ALLOCATION_CAP < 64 * 1024 * 1024
    )
    result = {
        "status": "T2_STATE_DEPENDENT_C5_SLICE_ENUMERATOR_CERTIFIED" if success else "SLICE_ENUMERATOR_CERTIFICATE_MISMATCH",
        "shape_cardinality": len(shapes),
        "slice_cardinality": SLICE_COUNT,
        "action_table_entries": ACTION_TABLE_ENTRIES,
        "action_table_bytes": len(table),
        "shape_encoder_check_count": SHAPE_ENCODER_CHECKS,
        "shape_encoder_failure_count": encoder_failures,
        "action_closure_failure_count": closure_failures,
        "ramp_failure_count": ramp_failures,
        "group_law_check_count": group_law_checks,
        "group_law_failure_count": group_law_failures,
        "cocycle_check_count": cocycle_checks,
        "cocycle_failure_count": cocycle_failures,
        "unique_b_check_count": unique_b_checks,
        "unique_b_failure_count": unique_b_failures,
        "collision_failure_count": collision_failures,
        "early_return_failure_count": early_return_failures,
        "power5_failure_count": power5_failures,
        "r1_power5_identity": int(not power5_failures),
        "r1_order": 5 if not early_return_failures and not power5_failures else 0,
        "r1_free": int(not collision_failures and not early_return_failures and not power5_failures),
        "visited_bits": visited_bits,
        "representative_count": len(representatives),
        "image_visit_count": image_visits,
        "enumerator_charge": ENUMERATOR_CHARGE,
        "shape_generation_charge": SHAPE_GENERATION_CHARGE,
        "sentinel_charge": SENTINEL_CHARGE,
        "engine_preflight_charge": ENGINE_CHARGE,
        "engine_preflight_cap": ENGINE_CHARGE_CAP,
        "declared_resident_bytes": DECLARED_RESIDENT_BYTES,
        "fixed_buffer_cap": FIXED_BUFFER_CAP,
        "peak_allocation_cap": PEAK_ALLOCATION_CAP,
        "orbit_artifact_path": ORBIT_ARTIFACT_PATH,
        "orbit_artifact_sha256": ORBIT_ARTIFACT_SHA256,
        "orbit_receipt_sha256": ORBIT_RECEIPT_SHA256,
        "native_design_sha256": NATIVE_DESIGN_SHA256,
        "codex_correction_sha256": CODEX_CORRECTION_SHA256,
        "input_manifest_sha256": manifest_hash.finish(),
        "shape_stream_sha256": shapes_hash.finish(),
        "action_table_sha256": action_hash.finish(),
        "sentinel_transcript_sha256": sentinel_hash.finish(),
        "residual_edge_stream_sha256": edges_hash.finish(),
        "slice_bitmap_sha256": bitmap_hash.finish(),
        "representative_stream_sha256": representatives_hash.finish(),
        "charge_transcript_sha256": charge_hash.finish(),
        "join_equivariance": "UNPROVED",
        "contraction_authorized": False,
        "common_zero_established": False,
    }
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
