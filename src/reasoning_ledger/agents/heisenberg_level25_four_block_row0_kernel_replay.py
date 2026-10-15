"""Standalone independent replay for the row-zero phase-kernel rank profile."""

from __future__ import annotations

import itertools
import json

FIELD_PRIME = 101
OMEGA = 95
LABEL_COUNT = 625
ORBIT_REPRESENTATIVES = (
    0,
    1,
    2,
    3,
    4,
    5,
    6,
    8,
    9,
    10,
    11,
    12,
    13,
    18,
    19,
    22,
    24,
    25,
    26,
    38,
    39,
)
TRANSFORM_MODULI = (1449072215711168621, 1449072215711180741)
ORDER_101_ROOTS = (1342006931089051155, 117958364458391118)
ORDER_5_ROOTS = (1075073713960594284, 425568228032004829)
FNV_OFFSET = 14695981039346656037
FNV_PRIME = 1099511628211
UINT64_MASK = (1 << 64) - 1


def prime64(value: int) -> bool:
    """Apply the complete deterministic Miller--Rabin test for uint64 values."""

    if value < 2:
        return False
    for prime in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if value % prime == 0:
            return value == prime
    odd = value - 1
    shifts = 0
    while odd % 2 == 0:
        odd //= 2
        shifts += 1
    for base in (2, 325, 9375, 28178, 450775, 9780504, 1795265022):
        if base % value == 0:
            continue
        witness = pow(base, odd, value)
        if witness in (1, value - 1):
            continue
        for _ in range(1, shifts):
            witness = witness * witness % value
            if witness == value - 1:
                break
        else:
            return False
    return True


def mix(checksum: int, value: int) -> int:
    for byte_index in range(8):
        checksum ^= value >> (8 * byte_index) & 255
        checksum = checksum * FNV_PRIME & UINT64_MASK
    return checksum


def mix_record(checksum: int, value: int, index: int) -> int:
    return mix(mix(checksum, value), index)


def replay() -> dict[str, object]:
    labels = list(itertools.product(range(5), repeat=4))
    omega_powers = [pow(OMEGA, exponent, FIELD_PRIME) for exponent in range(5)]
    edge_values = [
        (1 + sum(omega_powers[digit] for digit in delta)) % FIELD_PRIME
        for delta in labels
    ]
    result: dict[str, object] = {
        "prime_checks": [],
        "congruence_checks": [],
        "order_101_root_checks": [],
        "order_5_root_checks": [],
        "s_checksums": [],
        "factor_checksums": [],
        "direct_checksums": [],
        "diagnostic_checksums": [],
        "support_counts_modulus_1": [],
        "support_counts_modulus_2": [],
        "factor_eigenvalues": [],
        "s_values": [],
        "equality_bits": [],
    }
    total_formula_failures = 0
    total_c0_failures = 0
    total_transport_failures = 0
    total_transport_checks = 0
    moduli = zip(
        TRANSFORM_MODULI,
        ORDER_101_ROOTS,
        ORDER_5_ROOTS,
        strict=True,
    )
    for modulus_index, (modulus, root101, root5) in enumerate(moduli):
        result["prime_checks"].append(int(prime64(modulus)))
        result["congruence_checks"].append(int(modulus % 505 == 1))
        result["order_101_root_checks"].append(
            int(
                root101 != 1
                and pow(root101, 101, modulus) == 1
                and root101 == pow(2, (modulus - 1) // 101, modulus)
            )
        )
        result["order_5_root_checks"].append(
            int(
                root5 != 1
                and pow(root5, 5, modulus) == 1
                and root5 == pow(2, (modulus - 1) // 5, modulus)
            )
        )
        root101_powers = [
            pow(root101, exponent, modulus) for exponent in range(FIELD_PRIME)
        ]
        root5_powers = [pow(root5, exponent, modulus) for exponent in range(5)]
        s_checksum = FNV_OFFSET
        factor_checksum = FNV_OFFSET
        direct_checksum = FNV_OFFSET
        diagnostic_checksum = FNV_OFFSET
        support_counts = []
        factor_values_for_modulus = []
        s_values_for_modulus = []
        equality_bits_for_modulus = []
        for orbit_index, representative in enumerate(ORBIT_REPRESENTATIVES):
            s_values = []
            for frequency in range(5):
                value = (
                    sum(
                        root101_powers[
                            representative * omega_powers[exponent] % FIELD_PRIME
                        ]
                        * root5_powers[-frequency * exponent % 5]
                        for exponent in range(5)
                    )
                    % modulus
                )
                s_values.append(value)
                s_values_for_modulus.append(value)
                s_checksum = mix_record(
                    s_checksum,
                    value,
                    orbit_index * 5 + frequency,
                )
            kernel = [
                sum(
                    root101_powers[
                        representative
                        * omega_powers[phase]
                        % FIELD_PRIME
                        * edge_value
                        % FIELD_PRIME
                    ]
                    for phase in range(5)
                )
                % modulus
                for edge_value in edge_values
            ]
            for scalar in omega_powers:
                moved = representative * scalar % FIELD_PRIME
                for edge_value, expected in zip(edge_values, kernel, strict=True):
                    transported = (
                        sum(
                            root101_powers[
                                moved
                                * omega_powers[phase]
                                % FIELD_PRIME
                                * edge_value
                                % FIELD_PRIME
                            ]
                            for phase in range(5)
                        )
                        % modulus
                    )
                    total_transport_checks += 1
                    total_transport_failures += transported != expected
            current_support = 0
            for label_index, frequency in enumerate(labels):
                factored = s_values[-sum(frequency) % 5]
                for coordinate in frequency:
                    factored = factored * s_values[coordinate] % modulus
                direct = 0
                for delta, kernel_value in zip(labels, kernel, strict=True):
                    coefficient = root5_powers[
                        -sum(
                            left * right
                            for left, right in zip(frequency, delta, strict=True)
                        )
                        % 5
                    ]
                    direct = (direct + coefficient * kernel_value) % modulus
                flat_index = orbit_index * LABEL_COUNT + label_index
                factor_values_for_modulus.append(factored)
                factor_checksum = mix_record(
                    factor_checksum,
                    factored,
                    flat_index,
                )
                direct_checksum = mix_record(direct_checksum, direct, flat_index)
                equality = int(factored == direct)
                equality_bits_for_modulus.append(equality)
                total_formula_failures += not equality
                current_support += factored != 0
                if orbit_index == 0:
                    expected = 3125 % modulus if label_index == 0 else 0
                    total_c0_failures += factored != expected or direct != expected
            support_counts.append(current_support)
            diagnostic_checksum = mix_record(
                diagnostic_checksum,
                current_support,
                orbit_index,
            )
        for equality_index, equality in enumerate(equality_bits_for_modulus):
            diagnostic_checksum = mix_record(
                diagnostic_checksum,
                equality,
                len(ORBIT_REPRESENTATIVES) + equality_index,
            )
        result["s_checksums"].append(s_checksum)
        result["factor_checksums"].append(factor_checksum)
        result["direct_checksums"].append(direct_checksum)
        result["diagnostic_checksums"].append(diagnostic_checksum)
        result[f"support_counts_modulus_{modulus_index + 1}"] = support_counts
        result["factor_eigenvalues"].append(factor_values_for_modulus)
        result["s_values"].append(s_values_for_modulus)
        result["equality_bits"].append(equality_bits_for_modulus)
    result.update(
        {
            "formula_match_count": (
                2 * len(ORBIT_REPRESENTATIVES) * LABEL_COUNT
                - total_formula_failures
            ),
            "formula_failure_count": total_formula_failures,
            "c0_failure_count": total_c0_failures,
            "kernel_transport_check_count": total_transport_checks,
            "kernel_transport_failure_count": total_transport_failures,
        }
    )
    return result


if __name__ == "__main__":
    print(json.dumps(replay(), sort_keys=True, separators=(",", ":")))
