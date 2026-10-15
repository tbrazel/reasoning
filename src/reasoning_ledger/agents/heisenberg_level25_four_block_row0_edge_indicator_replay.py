"""Independent exact replay for the lossless edge-indicator Fourier ranks."""

import json

P = 101
OMEGA = 95
LABELS = 625
MODULI = (1449072215711168621, 1449072215711180741)
ROOTS = (1075073713960594284, 425568228032004829)
FNV_OFFSET = 1469598103934665603
FNV_PRIME = 1099511628211
MASK64 = 2**64 - 1


def mix(checksum, value):
    value &= MASK64
    for byte in range(8):
        checksum ^= (value >> (8 * byte)) & 255
        checksum = checksum * FNV_PRIME & MASK64
    return checksum


def prime64(value):
    if value < 2:
        return False
    for small in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if value % small == 0:
            return value == small
    odd = value - 1
    shifts = 0
    while odd % 2 == 0:
        odd //= 2
        shifts += 1
    for base in (2, 325, 9375, 28178, 450775, 9780504, 1795265022):
        if base % value == 0:
            continue
        current = pow(base % value, odd, value)
        if current in (1, value - 1):
            continue
        for _ in range(1, shifts):
            current = current * current % value
            if current == value - 1:
                break
        else:
            return False
    return True


digits = []
edge_by_delta = []
for code in range(LABELS):
    work = code
    current = []
    for _ in range(4):
        current.append(work % 5)
        work //= 5
    digits.append(current)
    edge_by_delta.append((1 + sum(pow(OMEGA, value, P) for value in current)) % P)

edge_values = sorted(set(edge_by_delta))
edge_index = {value: index for index, value in enumerate(edge_values)}
levels = [0] * len(edge_values)
for value in edge_by_delta:
    levels[edge_index[value]] += 1

supports = [0] * len(edge_values)
zeros = [0] * len(edge_values)
mod_support = [[0] * len(edge_values) for _ in MODULI]
exact_checksum = FNV_OFFSET
mod_checksums = [FNV_OFFSET, FNV_OFFSET]
mismatches = 0
for frequency, frequency_digits in enumerate(digits):
    bins = [[[0] for _ in range(5)] for _ in edge_values]
    for delta, delta_digits in enumerate(digits):
        exponent = -sum(
            first * second
            for first, second in zip(frequency_digits, delta_digits, strict=True)
        ) % 5
        bins[edge_index[edge_by_delta[delta]]][exponent][0] += 1
    for value_index, wrapped in enumerate(bins):
        counts = [entry[0] for entry in wrapped]
        is_zero = len(set(counts)) == 1
        if is_zero:
            zeros[value_index] += 1
        else:
            supports[value_index] += 1
        exact_checksum = mix(exact_checksum, value_index)
        exact_checksum = mix(exact_checksum, frequency)
        for exponent in range(4):
            exact_checksum = mix(exact_checksum, counts[exponent] - counts[4])
        for modulus_index, (modulus, root) in enumerate(zip(MODULI, ROOTS, strict=True)):
            transform = sum(counts[exponent] * pow(root, exponent, modulus) for exponent in range(5)) % modulus
            if transform:
                mod_support[modulus_index][value_index] += 1
            if (transform == 0) != is_zero:
                mismatches += 1
            mod_checksums[modulus_index] = mix(mod_checksums[modulus_index], value_index)
            mod_checksums[modulus_index] = mix(mod_checksums[modulus_index], frequency)
            mod_checksums[modulus_index] = mix(mod_checksums[modulus_index], transform)

defective = [
    value
    for value, support in zip(edge_values, supports, strict=True)
    if support != LABELS
]
result = {
    "field_prime": P,
    "omega": OMEGA,
    "label_count": LABELS,
    "edge_value_count": len(edge_values),
    "edge_values": edge_values,
    "edge_level_set_sizes": levels,
    "exact_fourier_support_counts": supports,
    "exact_fourier_zero_counts": zeros,
    "modulus_1_support_counts": mod_support[0],
    "modulus_2_support_counts": mod_support[1],
    "transform_moduli": list(MODULI),
    "order_5_roots": list(ROOTS),
    "prime_checks": [int(prime64(modulus)) for modulus in MODULI],
    "order_5_root_checks": [
        int(root != 1 and pow(root, 5, modulus) == 1)
        for modulus, root in zip(MODULI, ROOTS, strict=True)
    ],
    "exact_coefficient_checksum": exact_checksum,
    "modular_transform_checksums": mod_checksums,
    "exact_modular_zero_mismatch_count": mismatches,
    "full_rank_indicator_count": sum(support == LABELS for support in supports),
    "defective_indicator_count": len(defective),
    "defective_indicator_values": defective,
    "minimum_indicator_rank": min(supports),
    "maximum_indicator_rank": max(supports),
    "total_indicator_fourier_support": sum(supports),
    "maximum_total_indicator_fourier_support": len(edge_values) * LABELS,
    "fourier_term_count": LABELS * LABELS,
}
print(json.dumps(result, sort_keys=True, separators=(",", ":")))
