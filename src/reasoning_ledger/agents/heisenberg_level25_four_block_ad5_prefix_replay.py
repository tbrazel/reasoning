"""Independent scalar replay for the level-25 AD5 prefix-shift census.

This file imports no Reasoning evaluator code. It runs in a separate process
under a 64 MiB address-space limit.
"""

from __future__ import annotations

import hashlib
import json

P = 101
ZETA = 5
GEN = 2
CELL_COUNT = P * P
ZERO_COUNT = 4_420
COMBINED_CAP = 300_000_000
PHASE_ORDER = (0, 3, 1, 4, 2)
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

evaluation_charges = {
    "add": 0,
    "subtract": 0,
    "negate": 0,
    "multiply": 0,
    "inverse_lookup": 0,
    "power_lookup": 0,
    "source_record": 0,
    "hash_slot": 0,
    "key_compare": 0,
    "duplicate_link": 0,
    "checksum_word": 0,
}


def charge(name: str, count: int = 1) -> None:
    evaluation_charges[name] += count


def decode_shape(code: int) -> list[int]:
    exponents = [0] * 4
    for position in range(3, -1, -1):
        exponents[position] = code % 25
        code //= 25
    return [1, *(pow(ZETA, exponent, P) for exponent in exponents)]


def cyclic_bucket(shape: list[int]) -> int:
    value = 0
    for quotient in range(5):
        value = (
            value
            + pow(shape[quotient], 24, P) * shape[(quotient + 1) % 5]
        ) % P
        charge("inverse_lookup")
        charge("multiply")
        charge("add")
    return value


def coefficient(character: int, index: int) -> int:
    return pow(ZETA, (-character * index) % 25, P)


def unary(row: int, residue: int, scale: int, shape: list[int]) -> int:
    character, shift = CH[row]
    if shift % 5:
        return 0
    value = 0
    for quotient in range(5):
        index = residue + 5 * quotient
        other_index = (index - shift + 25) % 25
        other_quotient = (other_index - residue) // 5
        first = scale * shape[quotient] % P
        second = scale * shape[other_quotient] % P
        value = (
            value
            + coefficient(character, index) * pow(first, 24, P) * second
        ) % P
        charge("source_record")
        charge("power_lookup")
        charge("multiply", 2)
        charge("add")
    return value


def add_prefix(histogram: list[int], residue: int, scale: int, shape: list[int]) -> None:
    histogram[unary(6, residue, scale, shape) * P + unary(8, residue, scale, shape)] += 1


def digest(values: list[int]) -> str:
    payload = b"".join(value.to_bytes(8, "little") for value in values)
    return hashlib.sha256(payload).hexdigest()


def occupied(histogram: list[int]) -> list[tuple[int, int]]:
    return [(cell, value) for cell, value in enumerate(histogram) if value]


def main() -> None:
    zero_shapes = []
    for code in range(25**4):
        shape = decode_shape(code)
        if cyclic_bucket(shape) == 0:
            zero_shapes.append(shape)
    if len(zero_shapes) != ZERO_COUNT:
        raise SystemExit(71)

    ha = [0] * CELL_COUNT
    hd = [0] * CELL_COUNT
    hb = [0] * CELL_COUNT
    hc = [0] * CELL_COUNT
    histogram_increments = 0

    for shape in zero_shapes:
        add_prefix(ha, 0, 1, shape)
        histogram_increments += 1
    for jd in range(5):
        scale = pow(GEN, 4 * jd, P)
        for shape in zero_shapes:
            add_prefix(hd, 3, scale, shape)
            histogram_increments += 1
    for jb in range(5):
        for phase in PHASE_ORDER:
            scale = pow(GEN, 4 * jb + 20 * phase, P)
            for shape in zero_shapes:
                add_prefix(hb, 1, scale, shape)
                histogram_increments += 1
    for jc in range(5):
        scale = pow(GEN, 4 * jc, P)
        for shape in zero_shapes:
            add_prefix(hc, 2, scale, shape)
            histogram_increments += 1

    occupied_ha = occupied(ha)
    occupied_hd = occupied(hd)
    occupied_hb = occupied(hb)
    occupied_hc = occupied(hc)
    support_sum = sum(
        len(values)
        for values in (occupied_ha, occupied_hd, occupied_hb, occupied_hc)
    )
    n_pairs = len(occupied_ha) * len(occupied_hd)
    m_pairs = len(occupied_hb) * len(occupied_hc)
    convolution_pairs = n_pairs + m_pairs
    dense_actions = 7 * CELL_COUNT
    engine_preflight = (
        sum(evaluation_charges.values())
        + histogram_increments
        + convolution_pairs
        + dense_actions
        + support_sum
    )
    combined_preflight = 2 * engine_preflight + 2 * CELL_COUNT

    n_table = [0] * CELL_COUNT
    m_table = [0] * CELL_COUNT
    status = "CAP_ABORT"
    if combined_preflight < COMBINED_CAP:
        for a_cell, a_count in occupied_ha:
            for d_cell, d_count in occupied_hd:
                first = (a_cell // P + d_cell // P) % P
                second = (a_cell % P + d_cell % P) % P
                n_table[first * P + second] += a_count * d_count
        for b_cell, b_count in occupied_hb:
            for c_cell, c_count in occupied_hc:
                first = (-(b_cell // P + c_cell // P)) % P
                second = (-(b_cell % P + c_cell % P)) % P
                m_table[first * P + second] += b_count * c_count
        status = "NONPRUNING_CENSUS"

    excluded = 0
    open_contexts = 0
    realized_shifts = 0
    excluded_shifts = 0
    compatible_shifts = sum(value > 0 for value in m_table)
    prefix_pair_contexts = 0
    realized_m = []
    if status != "CAP_ABORT":
        for n_count, m_count in zip(n_table, m_table, strict=True):
            if not n_count:
                continue
            realized_shifts += 1
            realized_m.append(m_count)
            prefix_pair_contexts += n_count * m_count
            if m_count:
                open_contexts += n_count
            else:
                excluded += n_count
                excluded_shifts += 1
    if excluded:
        status = "CERTIFIED_PREFIX_EXCLUSION"

    result = {
        "status": status,
        "field_prime": P,
        "cell_count": CELL_COUNT,
        "shape_count": 25**4,
        "zero_bucket_count": len(zero_shapes),
        "a_domain_mass": sum(ha),
        "d_domain_mass": sum(hd),
        "b_domain_mass": sum(hb),
        "c_domain_mass": sum(hc),
        "a_histogram_support": len(occupied_ha),
        "d_histogram_support": len(occupied_hd),
        "b_histogram_support": len(occupied_hb),
        "c_histogram_support": len(occupied_hc),
        "a_histogram_sha256": digest(ha),
        "d_histogram_sha256": digest(hd),
        "b_histogram_sha256": digest(hb),
        "c_histogram_sha256": digest(hc),
        "n_table_sha256": digest(n_table),
        "m_table_sha256": digest(m_table),
        "n_table_mass": sum(n_table),
        "m_table_mass": sum(m_table),
        "n_table_support": sum(value > 0 for value in n_table),
        "m_table_support": sum(value > 0 for value in m_table),
        "realized_shift_count": realized_shifts,
        "excluded_shift_count": excluded_shifts,
        "bc_compatible_shift_count": compatible_shifts,
        "minimum_prefix_pair_count_on_realized_shift": min(realized_m, default=0),
        "maximum_prefix_pair_count_on_realized_shift": max(realized_m, default=0),
        "excluded_context_count": excluded,
        "open_context_count": open_contexts,
        "prefix_pair_context_count": prefix_pair_contexts,
        "histogram_increment_count": histogram_increments,
        "n_convolution_pair_count": n_pairs,
        "m_convolution_pair_count": m_pairs,
        "convolution_pair_count": convolution_pairs,
        "dense_action_count": dense_actions,
        "support_action_count": support_sum,
        "engine_preflight_charge": engine_preflight,
        "combined_preflight_charge": combined_preflight,
        "combined_charge_cap": COMBINED_CAP,
        "evaluation_add_charges": evaluation_charges["add"],
        "evaluation_subtract_charges": evaluation_charges["subtract"],
        "evaluation_negate_charges": evaluation_charges["negate"],
        "evaluation_multiply_charges": evaluation_charges["multiply"],
        "evaluation_inverse_lookup_charges": evaluation_charges["inverse_lookup"],
        "evaluation_power_lookup_charges": evaluation_charges["power_lookup"],
        "evaluation_source_record_charges": evaluation_charges["source_record"],
        "evaluation_hash_slot_charges": evaluation_charges["hash_slot"],
        "evaluation_key_compare_charges": evaluation_charges["key_compare"],
        "evaluation_duplicate_link_charges": evaluation_charges["duplicate_link"],
        "evaluation_checksum_word_charges": evaluation_charges["checksum_word"],
        "evaluation_charge_total": sum(evaluation_charges.values()),
    }
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
