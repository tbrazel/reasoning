"""Independent scalar replay for the AD5 coordinate-three central-key gate.

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
CAP = 300_000_000
FOURIER_MODULUS = 607
PHASE_ORDER = (0, 3, 1, 4, 2)
PATTERNS = ((1, 4, 20), (2, 13, 10), (4, 6, 15))
CHARACTER_SHIFT = ((2, 15), (3, 20), (6, 5))


class CountHash:
    def __init__(self) -> None:
        self._hash = hashlib.sha256()
        self.charge = 0

    def byte(self, value: int) -> None:
        self._hash.update(bytes((value,)))
        self.charge += 1

    def u64_be(self, value: int) -> None:
        self._hash.update(value.to_bytes(8, "big"))
        self.charge += 8

    def u64_le(self, value: int) -> None:
        self._hash.update(value.to_bytes(8, "little"))
        self.charge += 8

    def raw(self, value: bytes) -> None:
        self._hash.update(value)
        self.charge += len(value)

    def finish(self) -> str:
        return self._hash.hexdigest()


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


def total(charge: dict[str, int]) -> int:
    return sum(charge.values())


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
    return tuple(row)


def psi(first_residue: int, scale: int, shape: tuple[int, ...], charge: dict[str, int]) -> tuple[int, ...]:
    values = tuple(scale * value % P for value in shape)
    charge["multiply"] += 5
    carry = (first_residue + 2) // 5
    row = []
    for _alpha, _beta, gamma in PATTERNS:
        for quotient in range(5):
            row.append(pow(values[(quotient + carry) % 5], gamma, P))
            charge["power_lookup"] += 1
    return tuple(row)


def unary(row: int, residue: int, scale: int, shape: tuple[int, ...], charge: dict[str, int]) -> int:
    character, shift = CHARACTER_SHIFT[{5: 0, 6: 1, 8: 2}[row]]
    value = 0
    for quotient in range(5):
        index = residue + 5 * quotient
        target = (index - shift + 25) % 25
        target_quotient = (target - residue) // 5
        first = scale * shape[quotient] % P
        second = scale * shape[target_quotient] % P
        term = coefficient(character, index)
        term = term * pow(first, 24, P) % P
        term = term * second % P
        value = (value + term) % P
        charge["source_record"] += 1
        charge["power_lookup"] += 1
        charge["multiply"] += 2
        charge["add"] += 1
    return value


def insert_basis(row_value: tuple[int, ...], basis: list[list[int] | None], charge: list[int]) -> None:
    row = list(row_value)
    for column in range(FEATURE_DIM):
        if not row[column]:
            continue
        old = basis[column]
        if old is not None:
            factor = row[column]
            for offset in range(column, FEATURE_DIM):
                charge[0] += 2
                row[offset] = (row[offset] - factor * old[offset]) % P
            continue
        charge[0] += 1
        inverse = pow(row[column], P - 2, P)
        for offset in range(column, FEATURE_DIM):
            charge[0] += 1
            row[offset] = row[offset] * inverse % P
        for existing in basis:
            if existing is None or not existing[column]:
                continue
            factor = existing[column]
            for offset in range(column, FEATURE_DIM):
                charge[0] += 2
                existing[offset] = (existing[offset] - factor * row[offset]) % P
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


def dot(first: list[int] | tuple[int, ...], second: tuple[int, ...], charge: dict[str, int]) -> int:
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


def coordinates(
    basis: list[list[int] | None],
    row: tuple[int, ...],
    charge: dict[str, int],
) -> tuple[bytes, bool]:
    result = []
    reconstructed = [0] * FEATURE_DIM
    for pivot, item in enumerate(basis):
        if item is None:
            continue
        coefficient_value = row[pivot]
        result.append(coefficient_value)
        for column in range(FEATURE_DIM):
            reconstructed[column] = (reconstructed[column] + coefficient_value * item[column]) % P
            charge["multiply"] += 1
            charge["add"] += 1
    result.extend([0] * (FEATURE_DIM - len(result)))
    return bytes(result), tuple(reconstructed) == row


def basis_digest(basis: list[list[int] | None]) -> tuple[str, int]:
    receipt = CountHash()
    receipt.u64_le(sum(item is not None for item in basis))
    for item in basis:
        receipt.byte(item is not None)
        receipt.raw(bytes(item or [0] * FEATURE_DIM))
    return receipt.finish(), receipt.charge


def summarize(values: list[bytes], rank: int) -> dict[str, int | str]:
    values.sort()
    receipt = CountHash()
    receipt.u64_le(rank)
    distinct = minimum = maximum = collisions = 0
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
        receipt.raw(values[start])
        receipt.u64_le(count)
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
    carry = (residue + 2) // 5
    value = 0
    for alpha, beta, gamma in PATTERNS:
        for quotient in range(5):
            term = coefficient(2, residue + 5 * quotient)
            term = term * pow(first[quotient], alpha, P) % P
            term = term * pow(first[(quotient + 1) % 5], beta, P) % P
            term = term * pow(second[(quotient + carry) % 5], gamma, P) % P
            value = (value + term) % P
            charge["source_record"] += 1
            charge["power_lookup"] += 3
            charge["multiply"] += 3
            charge["add"] += 1
    return value


def sort_bound(count: int, width: int) -> int:
    levels = 0
    power = 1
    while power < count:
        power *= 2
        levels += 1
    return count * levels * width


def mod_power(base: int, exponent: int, charge: list[int]) -> int:
    value = 1
    while exponent:
        if exponent & 1:
            value = value * base % FOURIER_MODULUS
            charge[0] += 1
        exponent >>= 1
        if exponent:
            base = base * base % FOURIER_MODULUS
            charge[0] += 1
    return value


def fourier_sentinel() -> tuple[dict[str, int | str], int]:
    charge = [0]
    omega = next(candidate for candidate in range(2, FOURIER_MODULUS) if mod_power(candidate, 101, charge) == 1)
    powers = [1]
    for _exponent in range(1, 101):
        powers.append(powers[-1] * omega % FOURIER_MODULUS)
        charge[0] += 1
    inverse_m = mod_power(DENSE_CELLS % FOURIER_MODULUS, FOURIER_MODULUS - 2, charge)
    first = (1, 2, 3)
    second = (4, 5, 6)
    central = ((96, 94, 92), (96, 94, 93))
    character_results = []
    for center in central:
        value = 0
        for t0 in range(101):
            for t1 in range(101):
                for t2 in range(101):
                    frequency = (t0, t1, t2)
                    exponent = 0
                    for coordinate in range(3):
                        exponent = (
                            exponent
                            + frequency[coordinate]
                            * (first[coordinate] + second[coordinate] + center[coordinate])
                        ) % 101
                        charge[0] += 2
                    value += powers[(101 - exponent) % 101]
                    charge[0] += 1
                    if value >= 1 << 61:
                        value %= FOURIER_MODULUS
        character_results.append(value % FOURIER_MODULUS * inverse_m % FOURIER_MODULUS)
        charge[0] += 2
    direct = [
        int(all((first[index] + second[index] + center[index]) % 101 == 0 for index in range(3)))
        for center in central
    ]
    receipt = CountHash()
    for value in (omega, inverse_m, direct[0], character_results[0], direct[1], character_results[1]):
        receipt.u64_be(value)
    return {
        "omega": omega,
        "inverse_m": inverse_m,
        "first_direct": direct[0],
        "first_character": character_results[0],
        "second_direct": direct[1],
        "second_character": character_results[1],
        "digest": receipt.finish(),
        "hash_charge": receipt.charge,
    }, charge[0]


def main() -> None:
    hash_charge = 0
    state_iterations = 0
    definition = CountHash()
    definition.u64_be(P)
    definition.u64_be(FEATURE_DIM)
    for row in (5, 6, 8, 3):
        definition.u64_be(row)
    for pattern_index, exponents in enumerate(PATTERNS):
        for quotient in range(5):
            definition.u64_be(pattern_index)
            definition.u64_be(quotient)
            for exponent in exponents:
                definition.u64_be(exponent)
            for residue in (0, 1, 3):
                definition.u64_be(coefficient(2, residue + 5 * quotient))
                definition.u64_be((residue + 2) // 5)
    central_key_definition_sha256 = definition.finish()
    hash_charge += definition.charge

    search_charge = empty_charge()
    zero_codes = []
    for code in range(25**4):
        if cyclic_bucket(decode_shape(code), search_charge) == 0:
            zero_codes.append(code)
        state_iterations += 1
    if len(zero_codes) != ZERO_COUNT:
        raise SystemExit(71)

    feature_charge = empty_charge()
    rank_charge = [0]
    b_basis: list[list[int] | None] = [None] * FEATURE_DIM
    c_basis: list[list[int] | None] = [None] * FEATURE_DIM
    w_a3_basis: list[list[int] | None] = [None] * FEATURE_DIM
    for jb in range(5):
        for phase in PHASE_ORDER:
            scale = pow(GEN, 4 * jb + 20 * phase, P)
            for code in zero_codes:
                insert_basis(phi(1, scale, decode_shape(code), feature_charge), b_basis, rank_charge)
                state_iterations += 1
    canonicalize_basis(b_basis, rank_charge)
    for jc in range(5):
        scale = pow(GEN, 4 * jc, P)
        for code in zero_codes:
            insert_basis(psi(0, scale, decode_shape(code), feature_charge), c_basis, rank_charge)
            state_iterations += 1
    canonicalize_basis(c_basis, rank_charge)
    for code in zero_codes:
        insert_basis(psi(3, 1, decode_shape(code), feature_charge), w_a3_basis, rank_charge)
        state_iterations += 1
    canonicalize_basis(w_a3_basis, rank_charge)

    b_basis_sha256, charged = basis_digest(b_basis)
    hash_charge += charged
    c_basis_sha256, charged = basis_digest(c_basis)
    hash_charge += charged
    w_a3_rref_sha256, charged = basis_digest(w_a3_basis)
    hash_charge += charged

    signature_charge = empty_charge()
    coordinate_charge = empty_charge()
    central_charge = empty_charge()
    identity_charge = empty_charge()
    a_states = CountHash()
    d_states = CountHash()
    a_map = CountHash()
    d_map = CountHash()
    a_coordinates = CountHash()
    d_central_map = CountHash()
    orientation = CountHash()
    a_signatures: list[bytes] = []
    d_signatures: list[bytes] = []
    d_records: list[tuple[bytes, bytes, int]] = []
    coordinate_failures = 0

    for state, code in enumerate(zero_codes):
        shape = decode_shape(code)
        a_signature = signature(c_basis, phi(0, 1, shape, feature_charge), signature_charge)
        rho, reconstructed = coordinates(w_a3_basis, psi(3, 1, shape, feature_charge), coordinate_charge)
        coordinate_failures += not reconstructed
        a_signatures.append(a_signature)
        a_states.u64_be(state)
        a_states.u64_be(code)
        a_states.byte(1)
        a_map.u64_be(state)
        a_map.raw(a_signature)
        a_coordinates.u64_be(state)
        a_coordinates.raw(rho)
        state_iterations += 1

    state = 0
    for jd in range(5):
        scale = pow(GEN, 4 * jd, P)
        for code in zero_codes:
            shape = decode_shape(code)
            d_signature = signature(b_basis, psi(1, scale, shape, feature_charge), signature_charge)
            kappa = signature(w_a3_basis, phi(3, scale, shape, feature_charge), central_charge)
            key = bytes(
                (
                    unary(6, 3, scale, shape, central_charge),
                    unary(8, 3, scale, shape, central_charge),
                    unary(5, 3, scale, shape, central_charge),
                    *kappa,
                )
            )
            d_signatures.append(d_signature)
            d_records.append((d_signature, key, state))
            d_states.u64_be(state)
            d_states.u64_be(code)
            d_states.byte(scale)
            d_map.u64_be(state)
            d_map.raw(d_signature)
            state += 1
            state_iterations += 1
    if state != ND:
        raise SystemExit(73)

    identity_failures = 0
    for sample in range(256):
        did = (sample * 97 + 11) % ND
        aid = (sample * 53 + 7) % NA
        jd, d_position = divmod(did, ZERO_COUNT)
        d_scale = pow(GEN, 4 * jd, P)
        d_shape = decode_shape(zero_codes[d_position])
        a_shape = decode_shape(zero_codes[aid])
        direct = direct_pair(3, d_scale, d_shape, 1, a_shape, identity_charge)
        dotted = dot(phi(3, d_scale, d_shape, identity_charge), psi(3, 1, a_shape, identity_charge), identity_charge)
        identity_failures += direct != dotted
        for value in (did, aid, direct, dotted):
            orientation.u64_be(value)
        state_iterations += 1

    sort_charge = sort_bound(len(a_signatures), FEATURE_DIM) + sort_bound(len(d_signatures), FEATURE_DIM) + sort_bound(len(d_records), 34)
    a_summary = summarize(a_signatures, sum(item is not None for item in c_basis))
    d_summary = summarize(d_signatures, sum(item is not None for item in b_basis))
    hash_charge += int(a_summary["hash_charge"]) + int(d_summary["hash_charge"])
    d_records.sort()

    distinct_pairs = 0
    nonconstant_fibres = 0
    maximum_key_multiplicity = 0
    first_state = (1 << 64) - 1
    second_state = (1 << 64) - 1
    first_signature = bytes(FEATURE_DIM)
    first_key = bytes(18)
    second_key = bytes(18)
    begin = 0
    while begin < len(d_records):
        end = begin + 1
        while end < len(d_records) and d_records[end][0] == d_records[begin][0]:
            end += 1
        key_count = 0
        cursor = begin
        while cursor < end:
            next_cursor = cursor + 1
            while next_cursor < end and d_records[next_cursor][1] == d_records[cursor][1]:
                next_cursor += 1
            distinct_pairs += 1
            key_count += 1
            maximum_key_multiplicity = max(maximum_key_multiplicity, next_cursor - cursor)
            if key_count == 2 and first_state == (1 << 64) - 1:
                first_signature = d_records[begin][0]
                first_key = d_records[begin][1]
                second_key = d_records[cursor][1]
                first_state = d_records[begin][2]
                second_state = d_records[cursor][2]
            cursor = next_cursor
        nonconstant_fibres += key_count > 1
        begin = end

    for d_signature, key, state_id in d_records:
        d_central_map.raw(d_signature)
        d_central_map.raw(key)
        d_central_map.u64_be(state_id)

    sentinel, fourier_charge = fourier_sentinel()
    hash_charge += int(sentinel["hash_charge"])
    sentinel_verified = (
        int(sentinel["omega"]) != 0
        and int(sentinel["first_direct"]) == int(sentinel["first_character"]) == 1
        and int(sentinel["second_direct"]) == int(sentinel["second_character"]) == 0
    )

    hashes = {
        "a_state_stream_sha256": a_states.finish(),
        "d_state_stream_sha256": d_states.finish(),
        "a_signature_map_sha256": a_map.finish(),
        "d_signature_map_sha256": d_map.finish(),
        "a_coordinate_map_sha256": a_coordinates.finish(),
        "d_central_key_map_sha256": d_central_map.finish(),
        "orientation_samples_sha256": orientation.finish(),
    }
    hash_charge += sum(item.charge for item in (a_states, d_states, a_map, d_map, a_coordinates, d_central_map, orientation))
    source_guard = len(PATTERNS) == 3
    engine_charge = (
        total(search_charge)
        + total(feature_charge)
        + rank_charge[0]
        + total(signature_charge)
        + total(coordinate_charge)
        + total(central_charge)
        + total(identity_charge)
        + fourier_charge
        + hash_charge
        + state_iterations
        + sort_charge
    )
    projected_combined = 2 * engine_charge
    status = "CAP_ABORT"
    if source_guard and identity_failures == 0 and coordinate_failures == 0 and sentinel_verified and projected_combined < CAP:
        status = "D1_D_CENTRAL_KEY_NOT_DETERMINED" if nonconstant_fibres else "D1_PASS"
    next_gate = "ROUTE_REJECTED"
    if status == "D1_PASS":
        next_gate = "D2_PREFLIGHT_REQUIRED"
    elif status == "D1_D_CENTRAL_KEY_NOT_DETERMINED":
        next_gate = (
            "COORDINATE3_TENSOR_ORBIT_DIAGNOSTIC_REQUIRED"
            if distinct_pairs == ND and maximum_key_multiplicity == 1
            else "CENTRAL_KEY_REFINEMENT_REQUIRED"
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
        "b_feature_rank": sum(item is not None for item in b_basis),
        "c_feature_rank": sum(item is not None for item in c_basis),
        "w_a3_rank": sum(item is not None for item in w_a3_basis),
        "a_signature_count": int(a_summary["distinct"]),
        "a_signature_minimum_multiplicity": int(a_summary["minimum"]),
        "a_signature_maximum_multiplicity": int(a_summary["maximum"]),
        "d_signature_count": int(d_summary["distinct"]),
        "d_signature_multiplicity_mass": int(d_summary["mass"]),
        "d_signature_minimum_multiplicity": int(d_summary["minimum"]),
        "d_signature_maximum_multiplicity": int(d_summary["maximum"]),
        "d_signature_collision_pair_count": int(d_summary["collisions"]),
        "distinct_sigd_central_key_count": distinct_pairs,
        "nonconstant_sigd_fibre_count": nonconstant_fibres,
        "maximum_central_key_multiplicity": maximum_key_multiplicity,
        "first_conflict_first_state": first_state,
        "first_conflict_second_state": second_state,
        "coordinate_reconstruction_failure_count": coordinate_failures,
        "orientation_sample_count": 256,
        "orientation_sample_failure_count": identity_failures,
        "fourier_modulus": FOURIER_MODULUS,
        "fourier_omega": int(sentinel["omega"]),
        "fourier_inverse_m": int(sentinel["inverse_m"]),
        "fourier_first_direct": int(sentinel["first_direct"]),
        "fourier_first_character": int(sentinel["first_character"]),
        "fourier_second_direct": int(sentinel["second_direct"]),
        "fourier_second_character": int(sentinel["second_character"]),
        "fourier_sign_sentinel_verified": int(sentinel_verified),
        "shape_search_charge_total": total(search_charge),
        "feature_charge_total": total(feature_charge),
        "rank_charge_total": rank_charge[0],
        "signature_charge_total": total(signature_charge),
        "coordinate_charge_total": total(coordinate_charge),
        "central_key_charge_total": total(central_charge),
        "identity_charge_total": total(identity_charge),
        "fourier_charge_total": fourier_charge,
        "hash_byte_charge_total": hash_charge,
        "state_iteration_charge_total": state_iterations,
        "sort_charge_bound": sort_charge,
        "engine_preflight_charge": engine_charge,
        "projected_combined_diagnostic_charge": projected_combined,
        "combined_charge_cap": CAP,
        "source_guard": int(source_guard),
        "central_key_definition_sha256": central_key_definition_sha256,
        **hashes,
        "b_basis_sha256": b_basis_sha256,
        "c_basis_sha256": c_basis_sha256,
        "w_a3_rref_sha256": w_a3_rref_sha256,
        "a_signature_multiplicity_sha256": str(a_summary["digest"]),
        "d_signature_multiplicity_sha256": str(d_summary["digest"]),
        "fourier_sentinel_sha256": str(sentinel["digest"]),
        "first_conflict_signature_sha256": hashlib.sha256(first_signature).hexdigest(),
        "first_conflict_first_key_sha256": hashlib.sha256(first_key).hexdigest(),
        "first_conflict_second_key_sha256": hashlib.sha256(second_key).hexdigest(),
    }
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
