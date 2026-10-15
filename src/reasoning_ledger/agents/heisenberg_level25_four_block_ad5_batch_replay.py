"""Independent domain replay for the next bounded level-25 AD5 contexts.

This file deliberately imports no Reasoning evaluator code. It is executed in a
separate process with a 64 MiB address-space limit.
"""

from __future__ import annotations

import json

P = 101
ZETA = 5
N = 25
CH_COUNT = 22
PIVOT_COUNT = 4
SELECTED_COUNT = 2

charged_operations = 0


def charge(count: int = 1) -> None:
    global charged_operations
    charged_operations += count


def decode_shape(code: int) -> tuple[list[int], list[int]]:
    exponents = [0] * 4
    for position in range(3, -1, -1):
        exponents[position] = code % 25
        code //= 25
    return [1, *(pow(ZETA, exponent, P) for exponent in exponents)], exponents


def cyclic_bucket(shape: list[int]) -> int:
    value = 0
    for quotient in range(5):
        value = (
            value
            + pow(shape[quotient], 24, P) * shape[(quotient + 1) % 5]
        ) % P
        charge(3)
    return value


def mix(checksum: int, value: int) -> int:
    charge()
    for byte in range(8):
        checksum ^= (value >> (8 * byte)) & 255
        checksum = checksum * 1099511628211 & ((1 << 64) - 1)
    return checksum


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

    prefix = []
    for position, code in enumerate(zero_codes[:5]):
        values, exponents = decode_shape(code)
        prefix.append(
            {
                "position": position,
                "code": code,
                "exponents": exponents,
                "values": values,
            }
        )

    result = {
        "source_record_count": (
            CH_COUNT * N + N + PIVOT_COUNT * N + SELECTED_COUNT * N
        ),
        "shape_count": sum(histogram),
        "zero_bucket_count": len(zero_codes),
        "histogram_checksum": histogram_checksum,
        "zero_bucket_checksum": zero_checksum,
        "zero_bucket_prefix": prefix,
        "charged_operations": charged_operations,
    }
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
