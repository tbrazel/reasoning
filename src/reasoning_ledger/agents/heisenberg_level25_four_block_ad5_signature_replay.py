"""Independent scalar replay for the AD5 row-5 functional signatures.

This file imports no Reasoning evaluator code. It runs in a separate process
under a 64 MiB address-space limit.
"""

from __future__ import annotations

import hashlib
import json

P = 101
ZETA = 5
GEN = 2
FEATURE_DIM = 15
ZERO_COUNT = 4_420
NA = 4_420
ND = 22_100
NB = 110_500
NC = 22_100
DENSE_CELLS = 101**3
DIAGNOSTIC_CAP = 300_000_000
PHASE_ORDER = (0, 3, 1, 4, 2)
PATTERNS = ((1, 4, 20), (2, 13, 10), (4, 6, 15))


class CountHash:
    def __init__(self) -> None:
        self._hash = hashlib.sha256()
        self.charge = 0

    def byte(self, value: int) -> None:
        self._hash.update(bytes((value,)))
        self.charge += 1

    def u64(self, value: int) -> None:
        self._hash.update(value.to_bytes(8, "little"))
        self.charge += 8

    def feature(self, state: int, row: tuple[int, ...]) -> None:
        self.u64(state)
        self._hash.update(bytes(row))
        self.charge += len(row)

    def finish(self) -> str:
        return self._hash.hexdigest()


def decode_shape(code: int) -> tuple[int, ...]:
    exponents = [0] * 4
    for position in range(3, -1, -1):
        exponents[position] = code % 25
        code //= 25
    return (1, *(pow(ZETA, exponent, P) for exponent in exponents))


def coefficient(character: int, index: int) -> int:
    return pow(ZETA, (-character * index) % 25, P)


def cyclic_bucket(shape: tuple[int, ...], charge: dict[str, int]) -> int:
    value = 0
    for quotient in range(5):
        value = (value + pow(shape[quotient], 24, P) * shape[(quotient + 1) % 5]) % P
        charge["inverse_lookup"] += 1
        charge["multiply"] += 1
        charge["add"] += 1
    return value


def phi(residue: int, scale: int, shape: tuple[int, ...], charge: dict[str, int]) -> tuple[int, ...]:
    values = tuple(scale * value % P for value in shape)
    charge["multiply"] += 5
    row = []
    for alpha, beta, _gamma in PATTERNS:
        for quotient in range(5):
            term = coefficient(2, residue + 5 * quotient)
            term = term * pow(values[quotient], alpha, P) % P
            term = term * pow(values[(quotient + 1) % 5], beta, P) % P
            charge["power_lookup"] += 2
            charge["multiply"] += 2
            row.append(term)
    if len(row) != FEATURE_DIM:
        raise SystemExit(81)
    return tuple(row)


def psi(
    first_residue: int,
    scale: int,
    shape: tuple[int, ...],
    charge: dict[str, int],
) -> tuple[int, ...]:
    values = tuple(scale * value % P for value in shape)
    charge["multiply"] += 5
    carry = (first_residue + 2) // 5
    row = []
    for _alpha, _beta, gamma in PATTERNS:
        for quotient in range(5):
            row.append(pow(values[(quotient + carry) % 5], gamma, P))
            charge["power_lookup"] += 1
    if len(row) != FEATURE_DIM:
        raise SystemExit(82)
    return tuple(row)


def insert_basis(row_value: tuple[int, ...], basis: list[list[int] | None], charge: list[int]) -> None:
    row = list(row_value)
    for column in range(FEATURE_DIM):
        if not row[column]:
            continue
        existing = basis[column]
        if existing is not None:
            factor = row[column]
            for offset in range(column, FEATURE_DIM):
                charge[0] += 2
                row[offset] = (row[offset] - factor * existing[offset]) % P
            continue
        charge[0] += 1
        inverse = pow(row[column], P - 2, P)
        for offset in range(column, FEATURE_DIM):
            charge[0] += 1
            row[offset] = row[offset] * inverse % P
        for old in basis:
            if old is None or not old[column]:
                continue
            factor = old[column]
            for offset in range(column, FEATURE_DIM):
                charge[0] += 2
                old[offset] = (old[offset] - factor * row[offset]) % P
        basis[column] = row
        return


def canonicalize_basis(basis: list[list[int] | None], charge: list[int]) -> None:
    matrix = [item[:] for item in basis if item is not None]
    pivot_columns = []
    rank = 0
    for column in range(FEATURE_DIM):
        pivot = next((row for row in range(rank, len(matrix)) if matrix[row][column]), None)
        if pivot is None:
            continue
        matrix[rank], matrix[pivot] = matrix[pivot], matrix[rank]
        charge[0] += 1
        inverse = pow(matrix[rank][column], P - 2, P)
        for offset in range(FEATURE_DIM):
            charge[0] += 1
            matrix[rank][offset] = matrix[rank][offset] * inverse % P
        for row in range(len(matrix)):
            if row == rank or not matrix[row][column]:
                continue
            factor = matrix[row][column]
            for offset in range(FEATURE_DIM):
                charge[0] += 2
                matrix[row][offset] = (matrix[row][offset] - factor * matrix[rank][offset]) % P
        pivot_columns.append(column)
        rank += 1
        if rank == len(matrix):
            break
    basis[:] = [None] * FEATURE_DIM
    for column, row in zip(pivot_columns, matrix, strict=True):
        basis[column] = row


def dot(first: tuple[int, ...] | list[int], second: tuple[int, ...], charge: dict[str, int]) -> int:
    value = first[0] * second[0] % P
    charge["multiply"] += 1
    for position in range(1, FEATURE_DIM):
        value = (value + first[position] * second[position]) % P
        charge["multiply"] += 1
        charge["add"] += 1
    return value


def signature(basis: list[list[int] | None], row: tuple[int, ...], charge: dict[str, int]) -> bytes:
    values = [dot(item, row, charge) for item in basis if item is not None]
    values.extend([0] * (FEATURE_DIM - len(values)))
    return bytes(values)


def basis_digest(basis: list[list[int] | None]) -> tuple[str, int]:
    receipt = CountHash()
    receipt.u64(sum(item is not None for item in basis))
    for item in basis:
        receipt.byte(item is not None)
        for value in item or [0] * FEATURE_DIM:
            receipt.byte(value)
    return receipt.finish(), receipt.charge


def summarize(values: list[bytes], rank: int) -> dict[str, int | str]:
    values.sort()
    receipt = CountHash()
    receipt.u64(rank)
    distinct = 0
    minimum = 0
    maximum = 0
    collisions = 0
    start = 0
    while start < len(values):
        end = start + 1
        while end < len(values) and values[end] == values[start]:
            end += 1
        count = end - start
        distinct += 1
        minimum = min(minimum, count) if minimum else count
        maximum = max(maximum, count)
        collisions += count * (count - 1) // 2
        for value in values[start]:
            receipt.byte(value)
        receipt.u64(count)
        start = end
    return {
        "distinct": distinct,
        "mass": len(values),
        "minimum": minimum,
        "maximum": maximum,
        "collisions": collisions,
        "digest": receipt.finish(),
        "hash_charge": receipt.charge,
    }


def direct_pair(
    residue: int,
    first_scale: int,
    first_shape: tuple[int, ...],
    second_scale: int,
    second_shape: tuple[int, ...],
    charge: dict[str, int],
) -> int:
    first = tuple(first_scale * value % P for value in first_shape)
    second = tuple(second_scale * value % P for value in second_shape)
    value = 0
    carry = (residue + 2) // 5
    for alpha, beta, gamma in PATTERNS:
        for quotient in range(5):
            term = coefficient(2, residue + 5 * quotient)
            term = term * pow(first[quotient], alpha, P) % P
            term = term * pow(first[(quotient + 1) % 5], beta, P) % P
            term = term * pow(second[(quotient + carry) % 5], gamma, P) % P
            charge["source_record"] += 1
            charge["power_lookup"] += 3
            charge["multiply"] += 3
            charge["add"] += 1
            value = (value + term) % P
    return value


def total(charge: dict[str, int]) -> int:
    return sum(charge.values())


def empty_charge() -> dict[str, int]:
    return {
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


def sort_bound(count: int) -> int:
    levels = 0
    power = 1
    while power < count:
        power *= 2
        levels += 1
    return count * levels * FEATURE_DIM


def main() -> None:
    hash_charge = 0
    state_iterations = 0
    definition = CountHash()
    definition.u64(P)
    definition.u64(FEATURE_DIM)
    for pattern_index, exponents in enumerate(PATTERNS):
        for quotient in range(5):
            definition.u64(pattern_index)
            definition.u64(quotient)
            for exponent in exponents:
                definition.u64(exponent)
            for residue in (0, 1, 3):
                definition.u64(coefficient(2, residue + 5 * quotient))
                definition.u64((residue + 2) // 5)
    feature_definition_sha256 = definition.finish()
    hash_charge += definition.charge

    search_charge = empty_charge()
    zero_codes = []
    zero_receipt = CountHash()
    for code in range(25**4):
        if cyclic_bucket(decode_shape(code), search_charge) == 0:
            zero_codes.append(code)
            zero_receipt.u64(code)
        state_iterations += 1
    if len(zero_codes) != ZERO_COUNT:
        raise SystemExit(71)
    zero_code_sha256 = zero_receipt.finish()
    hash_charge += zero_receipt.charge

    feature_charge = empty_charge()
    rank_charge = [0]
    b_basis: list[list[int] | None] = [None] * FEATURE_DIM
    c_basis: list[list[int] | None] = [None] * FEATURE_DIM
    b_rows = CountHash()
    state = 0
    for jb in range(5):
        for phase in PHASE_ORDER:
            scale = pow(GEN, 4 * jb + 20 * phase, P)
            for code in zero_codes:
                row = phi(1, scale, decode_shape(code), feature_charge)
                insert_basis(row, b_basis, rank_charge)
                b_rows.feature(state, row)
                state += 1
                state_iterations += 1
    if state != NB:
        raise SystemExit(72)
    canonicalize_basis(b_basis, rank_charge)
    b_feature_rows_sha256 = b_rows.finish()
    hash_charge += b_rows.charge

    c_rows = CountHash()
    state = 0
    for jc in range(5):
        scale = pow(GEN, 4 * jc, P)
        for code in zero_codes:
            row = psi(0, scale, decode_shape(code), feature_charge)
            insert_basis(row, c_basis, rank_charge)
            c_rows.feature(state, row)
            state += 1
            state_iterations += 1
    if state != NC:
        raise SystemExit(73)
    canonicalize_basis(c_basis, rank_charge)
    c_feature_rows_sha256 = c_rows.finish()
    hash_charge += c_rows.charge

    b_basis_sha256, basis_hash_charge = basis_digest(b_basis)
    hash_charge += basis_hash_charge
    c_basis_sha256, basis_hash_charge = basis_digest(c_basis)
    hash_charge += basis_hash_charge
    b_rank = sum(item is not None for item in b_basis)
    c_rank = sum(item is not None for item in c_basis)

    signature_charge = empty_charge()
    d_rows = CountHash()
    d_signatures = []
    state = 0
    for jd in range(5):
        scale = pow(GEN, 4 * jd, P)
        for code in zero_codes:
            row = psi(1, scale, decode_shape(code), feature_charge)
            d_rows.feature(state, row)
            d_signatures.append(signature(b_basis, row, signature_charge))
            state += 1
            state_iterations += 1
    if state != ND:
        raise SystemExit(74)
    d_feature_rows_sha256 = d_rows.finish()
    hash_charge += d_rows.charge

    a_rows = CountHash()
    a_signatures = []
    state = 0
    for code in zero_codes:
        row = phi(0, 1, decode_shape(code), feature_charge)
        a_rows.feature(state, row)
        a_signatures.append(signature(c_basis, row, signature_charge))
        state += 1
        state_iterations += 1
    if state != NA:
        raise SystemExit(75)
    a_feature_rows_sha256 = a_rows.finish()
    hash_charge += a_rows.charge

    sort_charge = sort_bound(len(d_signatures)) + sort_bound(len(a_signatures))
    d_summary = summarize(d_signatures, b_rank)
    a_summary = summarize(a_signatures, c_rank)
    hash_charge += int(d_summary["hash_charge"]) + int(a_summary["hash_charge"])

    identity_charge = empty_charge()
    identity_receipt = CountHash()
    identity_failures = 0
    for sample in range(1000):
        kind = sample % 3
        if kind == 0:
            first_id = sample * 37 % NB
            second_id = (sample * 101 + 17) % ND
            work, b_position = divmod(first_id, ZERO_COUNT)
            jb, phase_position = divmod(work, 5)
            jd, d_position = divmod(second_id, ZERO_COUNT)
            b_scale = pow(GEN, 4 * jb + 20 * PHASE_ORDER[phase_position], P)
            d_scale = pow(GEN, 4 * jd, P)
            b_shape = decode_shape(zero_codes[b_position])
            d_shape = decode_shape(zero_codes[d_position])
            first = phi(1, b_scale, b_shape, identity_charge)
            second = psi(1, d_scale, d_shape, identity_charge)
            direct = direct_pair(1, b_scale, b_shape, d_scale, d_shape, identity_charge)
        elif kind == 1:
            first_id = sample * 43 % NA
            second_id = (sample * 107 + 19) % NC
            jc, c_position = divmod(second_id, ZERO_COUNT)
            a_shape = decode_shape(zero_codes[first_id])
            c_shape = decode_shape(zero_codes[c_position])
            c_scale = pow(GEN, 4 * jc, P)
            first = phi(0, 1, a_shape, identity_charge)
            second = psi(0, c_scale, c_shape, identity_charge)
            direct = direct_pair(0, 1, a_shape, c_scale, c_shape, identity_charge)
        else:
            first_id = sample * 47 % ND
            second_id = (sample * 109 + 23) % NA
            jd, d_position = divmod(first_id, ZERO_COUNT)
            d_scale = pow(GEN, 4 * jd, P)
            d_shape = decode_shape(zero_codes[d_position])
            a_shape = decode_shape(zero_codes[second_id])
            first = phi(3, d_scale, d_shape, identity_charge)
            second = psi(3, 1, a_shape, identity_charge)
            direct = direct_pair(3, d_scale, d_shape, 1, a_shape, identity_charge)
        dotted = dot(first, second, identity_charge)
        identity_failures += direct != dotted
        for value in (kind, first_id, second_id, direct, dotted):
            identity_receipt.u64(value)
        state_iterations += 1

    calibration_shape = decode_shape(zero_codes[0])
    calibration_direct = direct_pair(3, 1, calibration_shape, 1, calibration_shape, identity_charge)
    calibration_dot = dot(
        phi(3, 1, calibration_shape, identity_charge),
        psi(3, 1, calibration_shape, identity_charge),
        identity_charge,
    )
    calibration_verified = calibration_direct == calibration_dot
    for value in (3, 0, 0, calibration_direct, calibration_dot):
        identity_receipt.u64(value)
    identity_samples_sha256 = identity_receipt.finish()
    hash_charge += identity_receipt.charge

    d_count = int(d_summary["distinct"])
    a_count = int(a_summary["distinct"])
    profile_visits = d_count * NB + a_count * NC
    profile_value_operations = 2 * b_rank * d_count * NB + 2 * c_rank * a_count * NC
    profile_lower_bound_combined = 2 * (profile_visits + profile_value_operations)
    dense_profile_bytes = 8 * DENSE_CELLS
    dense_pair_bytes = 2 * dense_profile_bytes
    dense_all_bytes = dense_profile_bytes * (d_count + a_count)
    sparse_pair_bytes = 16 * (NB + NC)
    shape_search_total = total(search_charge)
    feature_total = total(feature_charge)
    signature_total = total(signature_charge)
    identity_total = total(identity_charge)
    engine_preflight = (
        shape_search_total
        + feature_total
        + rank_charge[0]
        + signature_total
        + identity_total
        + hash_charge
        + state_iterations
        + sort_charge
    )
    projected_combined = 2 * engine_preflight
    source_guard = len(PATTERNS) == 3
    source_verified = source_guard and identity_failures == 0 and calibration_verified
    status = (
        "SIGNATURE_CENSUS_COMPLETE"
        if source_verified and projected_combined < DIAGNOSTIC_CAP
        else "CAP_ABORT"
    )
    next_gate = (
        "SPARSE_PROFILE_NEXT_GATE"
        if status == "SIGNATURE_CENSUS_COMPLETE"
        and projected_combined + profile_lower_bound_combined < DIAGNOSTIC_CAP
        else "CHARACTER_CONTRACTION_REQUIRED"
    )
    result = {
        "status": status,
        "next_gate": next_gate,
        "field_prime": P,
        "feature_dimension": FEATURE_DIM,
        "pivot_pattern_count": len(PATTERNS),
        "zero_bucket_count": len(zero_codes),
        "a_state_count": NA,
        "d_state_count": ND,
        "b_state_count": NB,
        "c_state_count": NC,
        "b_feature_rank": b_rank,
        "c_feature_rank": c_rank,
        "d_signature_count": d_count,
        "a_signature_count": a_count,
        "d_signature_multiplicity_mass": int(d_summary["mass"]),
        "a_signature_multiplicity_mass": int(a_summary["mass"]),
        "d_signature_minimum_multiplicity": int(d_summary["minimum"]),
        "d_signature_maximum_multiplicity": int(d_summary["maximum"]),
        "a_signature_minimum_multiplicity": int(a_summary["minimum"]),
        "a_signature_maximum_multiplicity": int(a_summary["maximum"]),
        "d_signature_collision_pair_count": int(d_summary["collisions"]),
        "a_signature_collision_pair_count": int(a_summary["collisions"]),
        "future_profile_visit_count_per_engine": profile_visits,
        "future_profile_value_operation_count_per_engine": profile_value_operations,
        "future_profile_lower_bound_combined": profile_lower_bound_combined,
        "dense_cell_count": DENSE_CELLS,
        "dense_profile_bytes": dense_profile_bytes,
        "dense_pair_bytes": dense_pair_bytes,
        "dense_all_signature_profile_bytes": dense_all_bytes,
        "sparse_pair_payload_worst_case_bytes": sparse_pair_bytes,
        "identity_sample_count": 1000,
        "identity_sample_failure_count": identity_failures,
        "calibration_identity_verified": int(calibration_verified),
        "row5_source_guard": int(source_guard),
        "source_decomposition_verified": int(source_verified),
        "shape_search_charge_total": shape_search_total,
        "feature_charge_total": feature_total,
        "rank_charge_total": rank_charge[0],
        "signature_charge_total": signature_total,
        "identity_charge_total": identity_total,
        "hash_byte_charge_total": hash_charge,
        "state_iteration_charge_total": state_iterations,
        "sort_charge_bound": sort_charge,
        "engine_preflight_charge": engine_preflight,
        "projected_combined_diagnostic_charge": projected_combined,
        "combined_charge_cap": DIAGNOSTIC_CAP,
        "feature_definition_sha256": feature_definition_sha256,
        "zero_code_sha256": zero_code_sha256,
        "b_feature_rows_sha256": b_feature_rows_sha256,
        "c_feature_rows_sha256": c_feature_rows_sha256,
        "d_feature_rows_sha256": d_feature_rows_sha256,
        "a_feature_rows_sha256": a_feature_rows_sha256,
        "b_basis_sha256": b_basis_sha256,
        "c_basis_sha256": c_basis_sha256,
        "d_signature_multiplicity_sha256": str(d_summary["digest"]),
        "a_signature_multiplicity_sha256": str(a_summary["digest"]),
        "identity_samples_sha256": identity_samples_sha256,
    }
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
