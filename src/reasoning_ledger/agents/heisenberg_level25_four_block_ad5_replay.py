"""Independent scalar replay for the bounded level-25 AD5 calibration.

This file deliberately imports no Reasoning evaluator code.  It is executed in a
separate process with a 64 MiB address-space limit.
"""

from __future__ import annotations

import json
import sys

P = 101
N = 25
ZETA = 5
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
PIVOTS = ((2, 1, 4, 20), (2, 2, 13, 10), (2, 4, 6, 15), (4, 1, 4, 20))
SELECTED = ((9, 2, 19, 18), (7, 1, 2, 23))

charged_operations = 0


def charge(count: int = 1) -> None:
    global charged_operations
    charged_operations += count


def coefficient(character: int, index: int) -> int:
    return pow(ZETA, (-character * index) % N, P)


def decode_shape(code: int) -> tuple[list[int], list[int]]:
    exponents = [0] * 4
    for position in range(3, -1, -1):
        exponents[position] = code % 25
        code //= 25
    return [1, *(pow(ZETA, exponent, P) for exponent in exponents)], exponents


def cyclic_bucket(shape: list[int]) -> int:
    value = 0
    for quotient in range(5):
        value = (value + pow(shape[quotient], 24, P) * shape[(quotient + 1) % 5]) % P
        charge(3)
    return value


def mix(checksum: int, value: int) -> int:
    charge()
    for byte in range(8):
        checksum ^= (value >> (8 * byte)) & 255
        checksum = checksum * 1099511628211 & ((1 << 64) - 1)
    return checksum


def source_record_count() -> int:
    base = len(CH) * N
    adjacent = N
    nonadjacent = len(PIVOTS) * N
    selected = len(SELECTED) * N
    return base + adjacent + nonadjacent + selected


def value_and_gradient(row: int, point: list[int]) -> tuple[int, list[int]]:
    character, shift = CH[row]
    value = 0
    gradient = [0] * N
    for index in range(N):
        target = (index - shift) % N
        coeff = coefficient(character, index)
        term = coeff * pow(point[index], 24, P) * point[target] % P
        value = (value + term) % P
        gradient[index] = (gradient[index] + 24 * coeff * pow(point[index], 23, P) * point[target]) % P
        gradient[target] = (gradient[target] + coeff * pow(point[index], 24, P)) % P
        charge(11)
    if row == 0:
        for index in range(N):
            target = (index + 1) % N
            coeff = coefficient(5, index)
            term = coeff * pow(point[index], 20, P) * pow(point[target], 5, P) % P
            value = (value + term) % P
            gradient[index] = (gradient[index] + 20 * coeff * pow(point[index], 19, P) * pow(point[target], 5, P)) % P
            gradient[target] = (gradient[target] + 5 * coeff * pow(point[index], 20, P) * pow(point[target], 4, P)) % P
            charge(16)
    for pivot_character, first, second, third in PIVOTS:
        if (character, shift) != (pivot_character, 15):
            continue
        exponents = (first, second, third)
        for index in range(N):
            indices = (index, (index + 5) % N, (index + 2) % N)
            coeff = coefficient(character, index)
            term = coeff
            for coordinate, exponent in zip(indices, exponents, strict=True):
                term = term * pow(point[coordinate], exponent, P) % P
            value = (value + term) % P
            for factor, (coordinate, exponent) in enumerate(zip(indices, exponents, strict=True)):
                derivative = coeff * exponent % P
                for other, (other_coordinate, other_exponent) in enumerate(zip(indices, exponents, strict=True)):
                    derivative = derivative * pow(point[other_coordinate], other_exponent - int(factor == other), P) % P
                gradient[coordinate] = (gradient[coordinate] + derivative) % P
            charge(35)
    for selected_character, first_offset, second_offset, middle_exponent in SELECTED:
        if (character, shift) != (selected_character, 0):
            continue
        indices_exponents = (0, middle_exponent, 24 - middle_exponent)
        for index in range(N):
            indices = (index, (index + first_offset) % N, (index + second_offset) % N)
            coeff = coefficient(character, index)
            term = coeff
            for coordinate, exponent in zip(indices, indices_exponents, strict=True):
                term = term * pow(point[coordinate], exponent, P) % P
            value = (value + term) % P
            for factor, (coordinate, exponent) in enumerate(zip(indices, indices_exponents, strict=True)):
                derivative = coeff * exponent % P
                for other, (other_coordinate, other_exponent) in enumerate(zip(indices, indices_exponents, strict=True)):
                    derivative = derivative * pow(point[other_coordinate], other_exponent - int(factor == other), P) % P
                gradient[coordinate] = (gradient[coordinate] + derivative) % P
            charge(35)
    return value, gradient


def rank(matrix: list[list[int]]) -> int:
    work = [row[:] for row in matrix]
    pivot_row = 0
    for column in range(N):
        pivot = next((row for row in range(pivot_row, len(work)) if work[row][column]), None)
        if pivot is None:
            continue
        work[pivot_row], work[pivot] = work[pivot], work[pivot_row]
        inverse = pow(work[pivot_row][column], P - 2, P)
        charge()
        work[pivot_row] = [value * inverse % P for value in work[pivot_row]]
        charge(N)
        for row in range(len(work)):
            if row == pivot_row or not work[row][column]:
                continue
            factor = work[row][column]
            work[row] = [(value - factor * pivot_value) % P for value, pivot_value in zip(work[row], work[pivot_row], strict=True)]
            charge(2 * N)
        pivot_row += 1
        if pivot_row == len(work):
            break
    return pivot_row


def main() -> None:
    histogram = [0] * P
    zero_codes: list[int] = []
    for code in range(25**4):
        shape, _exponents = decode_shape(code)
        bucket = cyclic_bucket(shape)
        histogram[bucket] += 1
        if bucket == 0:
            zero_codes.append(code)
    histogram_checksum = 1469598103934665603
    zero_checksum = 1469598103934665603
    for bucket, count in enumerate(histogram):
        histogram_checksum = mix(histogram_checksum, bucket)
        histogram_checksum = mix(histogram_checksum, count)
    for code in zero_codes:
        zero_checksum = mix(zero_checksum, code)
    first_shape, first_exponents = decode_shape(zero_codes[0])
    result: dict[str, object] = {
        "source_record_count": source_record_count(),
        "shape_count": sum(histogram),
        "zero_bucket_count": len(zero_codes),
        "histogram_checksum": histogram_checksum,
        "zero_bucket_checksum": zero_checksum,
        "first_zero_shape_code": zero_codes[0],
        "first_zero_shape_exponents": first_exponents,
        "first_zero_shape_values": first_shape,
        "coordinates": [],
        "equation_values": [],
        "jacobian": [],
        "rank": -1,
    }
    if len(sys.argv) != 2:
        raise SystemExit("usage: replay.py COORDINATES_OR_DASH")
    if sys.argv[1] != "-":
        coordinates = [int(item) for item in sys.argv[1].split(",")]
        if len(coordinates) != N or any(value < 0 or value >= P for value in coordinates):
            raise SystemExit("invalid witness coordinates")
        rows = [value_and_gradient(row, coordinates) for row in range(len(CH))]
        matrix = [gradient for _value, gradient in rows]
        result.update(
            {
                "coordinates": coordinates,
                "equation_values": [value for value, _gradient in rows],
                "jacobian": [entry for row in matrix for entry in row],
                "rank": rank(matrix),
            }
        )
    result["charged_operations"] = charged_operations
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
