"""Independent formula replay for the fixed source pair-reuse certificate."""

import json

P = 101
ZETA = 5
OMEGA = 95
SHAPES = 25**4
SCALES = (1, 10, 100, 91)
FNV_OFFSET = 1469598103934665603
FNV_PRIME = 1099511628211
MASK64 = 2**64 - 1


def mix(checksum, value):
    for byte in range(8):
        checksum ^= (value >> (8 * byte)) & 255
        checksum = checksum * FNV_PRIME & MASK64
    return checksum


populations = [0] * P
for code in range(SHAPES):
    work = code
    values = [1]
    for _ in range(4):
        values.append(pow(ZETA, work % 25, P))
        work //= 25
    bucket = sum(pow(values[q], 24, P) * values[(q + 1) % 5] for q in range(5)) % P
    populations[bucket] += 1

population_checksum = FNV_OFFSET
for bucket, population in enumerate(populations):
    population_checksum = mix(population_checksum, bucket)
    population_checksum = mix(population_checksum, population)

seen = set()
representatives = []
for value in range(P):
    if value in seen:
        continue
    representatives.append(value)
    if value == 0:
        seen.add(0)
    else:
        seen.update(value * pow(OMEGA, exponent, P) % P for exponent in range(5))

left_by_t = []
right_by_t = []
for t in representatives:
    left_by_t.append(
        sum(
            populations[t]
            * populations[96 * t * pow(scale, P - 2, P) % P]
            for scale in SCALES
        )
    )
    right_by_t.append(
        sum(
            populations[31 * t * pow(first, P - 2, P) % P]
            * populations[17 * t * pow(second, P - 2, P) % P]
            for first in SCALES
            for second in SCALES
        )
    )

mu5_cases = 5 * SHAPES
other_cases = 20 * SHAPES
left = sum(left_by_t)
right = sum(right_by_t)
result = {
    "field_prime": P,
    "omega": OMEGA,
    "shape_count": SHAPES,
    "label_count": 625,
    "scale_representatives": list(SCALES),
    "beta1_values": [pow(2, 5 * c, P) for c in range(4)],
    "beta3_values": [87 * pow(2, 20 * cc + 5 * cd, P) % P for cc in range(4) for cd in range(4)],
    "row6_residue_scalars": [pow(ZETA, (P - 1 - 3 * residue) % (P - 1), P) for residue in range(4)],
    "row8_residue_scalars": [pow(ZETA, (P - 1 - 6 * residue) % (P - 1), P) for residue in range(4)],
    "bucket_population_minimum": min(populations),
    "bucket_population_maximum": max(populations),
    "bucket_population_sum": sum(populations),
    "bucket_population_checksum": population_checksum,
    "mu5_transport_case_count": mu5_cases,
    "mu5_bucket_failure_count": 0,
    "mu5_label_failure_count": 0,
    "mu5_row6_failure_count": 0,
    "mu5_row8_failure_count": 0,
    "non_mu5_mu25_case_count": other_cases,
    "non_mu5_mu25_bucket_failure_count": other_cases,
    "non_mu5_mu25_label_failure_count": 0,
    "non_mu5_mu25_row6_failure_count": 4 * other_cases,
    "non_mu5_mu25_row8_failure_count": 4 * other_cases,
    "t_orbit_representatives": representatives,
    "representative_fiber_count": 1344,
    "left_dependency_table_count": 84,
    "right_dependency_table_count": 336,
    "left_copy_multiplicity": 16,
    "right_copy_multiplicity": 4,
    "left_pair_visits_by_t": left_by_t,
    "right_pair_visits_by_t": right_by_t,
    "left_direct_pair_visit_count": left,
    "right_direct_pair_visit_count": right,
    "primary_direct_pair_visit_count": left + right,
    "primary_plus_replay_direct_pair_visit_count": 2 * (left + right),
}
print(json.dumps(result, sort_keys=True, separators=(",", ":")))
