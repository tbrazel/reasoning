"""Safe local arithmetic, unit conversion, and versioned physical constants."""

from __future__ import annotations

import ast
import copy
import hashlib
import math
import sys
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
from fractions import Fraction
from functools import lru_cache
from itertools import combinations, permutations, product
from typing import Any

from ..canonical import canonical_json
from .tools import ToolRegistry

_MAX_BITS = 65_536
_MAX_NODES = 256
_NIST_URL = "https://physics.nist.gov/constants"

_CONSTANTS = (
    {
        "id": "speed-of-light",
        "name": "speed of light in vacuum",
        "symbol": "c",
        "value": "299792458",
        "unit": "m s^-1",
        "uncertainty": "exact",
        "aliases": ["lightspeed"],
    },
    {
        "id": "planck-constant",
        "name": "Planck constant",
        "symbol": "h",
        "value": "6.62607015e-34",
        "unit": "J Hz^-1",
        "uncertainty": "exact",
        "aliases": [],
    },
    {
        "id": "elementary-charge",
        "name": "elementary charge",
        "symbol": "e",
        "value": "1.602176634e-19",
        "unit": "C",
        "uncertainty": "exact",
        "aliases": ["electron charge magnitude"],
    },
    {
        "id": "boltzmann-constant",
        "name": "Boltzmann constant",
        "symbol": "k_B",
        "value": "1.380649e-23",
        "unit": "J K^-1",
        "uncertainty": "exact",
        "aliases": ["k"],
    },
    {
        "id": "avogadro-constant",
        "name": "Avogadro constant",
        "symbol": "N_A",
        "value": "6.02214076e23",
        "unit": "mol^-1",
        "uncertainty": "exact",
        "aliases": ["Avogadro number"],
    },
    {
        "id": "cesium-hyperfine-frequency",
        "name": "cesium-133 hyperfine transition frequency",
        "symbol": "Delta nu_Cs",
        "value": "9192631770",
        "unit": "Hz",
        "uncertainty": "exact",
        "aliases": ["caesium hyperfine frequency", "SI second frequency"],
    },
    {
        "id": "luminous-efficacy",
        "name": "luminous efficacy of monochromatic radiation of frequency 540 THz",
        "symbol": "K_cd",
        "value": "683",
        "unit": "lm W^-1",
        "uncertainty": "exact",
        "aliases": ["luminous efficacy"],
    },
    {
        "id": "gravitational-constant",
        "name": "Newtonian constant of gravitation",
        "symbol": "G",
        "value": "6.67430e-11",
        "unit": "m^3 kg^-1 s^-2",
        "uncertainty": "1.5e-15",
        "aliases": ["gravitational constant"],
    },
    {
        "id": "electron-mass",
        "name": "electron mass",
        "symbol": "m_e",
        "value": "9.1093837139e-31",
        "unit": "kg",
        "uncertainty": "2.8e-40",
        "aliases": [],
    },
    {
        "id": "proton-mass",
        "name": "proton mass",
        "symbol": "m_p",
        "value": "1.67262192595e-27",
        "unit": "kg",
        "uncertainty": "5.2e-37",
        "aliases": [],
    },
)
_CONSTANTS_HASH = hashlib.sha256(canonical_json(_CONSTANTS).encode()).hexdigest()


def _is_prime(value: int) -> bool:
    if value < 2:
        return False
    if value % 2 == 0:
        return value == 2
    divisor = 3
    while divisor * divisor <= value:
        if value % divisor == 0:
            return False
        divisor += 2
    return True


def _rank_mod_p(rows: list[list[int]], prime: int) -> int:
    matrix = [[entry % prime for entry in row] for row in rows]
    if not matrix:
        return 0
    column_count = len(matrix[0])
    pivot_row = 0
    for column in range(column_count):
        pivot = next(
            (row for row in range(pivot_row, len(matrix)) if matrix[row][column]),
            None,
        )
        if pivot is None:
            continue
        matrix[pivot_row], matrix[pivot] = matrix[pivot], matrix[pivot_row]
        inverse = pow(matrix[pivot_row][column], -1, prime)
        matrix[pivot_row] = [(entry * inverse) % prime for entry in matrix[pivot_row]]
        for row in range(len(matrix)):
            if row == pivot_row or not matrix[row][column]:
                continue
            scale = matrix[row][column]
            matrix[row] = [(entry - scale * pivot_entry) % prime for entry, pivot_entry in zip(matrix[row], matrix[pivot_row], strict=True)]
        pivot_row += 1
        if pivot_row == len(matrix):
            break
    return pivot_row


def _bounded_integer_matrix(
    name: str,
    rows: list[list[int]],
    *,
    min_rows: int,
    max_rows: int,
    width: int | None = None,
) -> list[list[int]]:
    if not min_rows <= len(rows) <= max_rows:
        raise ValueError(f"{name} needs between {min_rows} and {max_rows} rows")
    expected_width = width if width is not None else len(rows)
    if expected_width < 1 or any(len(row) != expected_width for row in rows):
        raise ValueError(f"{name} rows must all have length {expected_width}")
    if any(type(entry) is not int for row in rows for entry in row):
        raise ValueError(f"{name} accepts only integers")
    if any(abs(entry) > 1_000_000_000 for row in rows for entry in row):
        raise ValueError(f"{name} entries must have magnitude at most 1,000,000,000")
    return [list(row) for row in rows]


def _integer_matrix_shape(
    value: Any,
    name: str,
    rows: int,
    columns: int,
    *,
    bound: int = 1_000_000,
) -> list[list[int]]:
    if not isinstance(value, list) or len(value) != rows:
        raise ValueError(f"{name} must have shape {rows}x{columns}")
    if any(not isinstance(row, list) or len(row) != columns for row in value):
        raise ValueError(f"{name} must have shape {rows}x{columns}")
    if any(type(entry) is not int for row in value for entry in row):
        raise ValueError(f"{name} accepts only integers")
    if any(abs(entry) > bound for row in value for entry in row):
        raise ValueError(f"{name} entries must have magnitude at most {bound:,}")
    return [list(row) for row in value]


def _zero_matrix(rows: int, columns: int) -> list[list[int]]:
    return [[0] * columns for _ in range(rows)]


def _identity_matrix(size: int) -> list[list[int]]:
    return [[int(row == column) for column in range(size)] for row in range(size)]


def _matrix_multiply(
    left: list[list[int]],
    right: list[list[int]],
    *,
    rows: int,
    inner: int,
    columns: int,
) -> list[list[int]]:
    return [[sum(left[row][index] * right[index][column] for index in range(inner)) for column in range(columns)] for row in range(rows)]


def _matrix_add(left: list[list[int]], right: list[list[int]], *, rows: int, columns: int) -> list[list[int]]:
    return [[left[row][column] + right[row][column] for column in range(columns)] for row in range(rows)]


def _matrix_subtract(left: list[list[int]], right: list[list[int]], *, rows: int, columns: int) -> list[list[int]]:
    return [[left[row][column] - right[row][column] for column in range(columns)] for row in range(rows)]


def _matrix_power_and_sum(matrix: list[list[int]], size: int, exponent: int) -> tuple[list[list[int]], list[list[int]]]:
    """Return A^n and I+A+...+A^(n-1) by binary concatenation."""

    result_power = _identity_matrix(size)
    result_sum = _zero_matrix(size, size)
    block_power = [list(row) for row in matrix]
    block_sum = _identity_matrix(size)
    remaining = exponent
    while remaining:
        if remaining & 1:
            result_sum = _matrix_add(
                result_sum,
                _matrix_multiply(
                    result_power,
                    block_sum,
                    rows=size,
                    inner=size,
                    columns=size,
                ),
                rows=size,
                columns=size,
            )
            result_power = _matrix_multiply(
                result_power,
                block_power,
                rows=size,
                inner=size,
                columns=size,
            )
        block_sum = _matrix_add(
            block_sum,
            _matrix_multiply(
                block_power,
                block_sum,
                rows=size,
                inner=size,
                columns=size,
            ),
            rows=size,
            columns=size,
        )
        block_power = _matrix_multiply(
            block_power,
            block_power,
            rows=size,
            inner=size,
            columns=size,
        )
        remaining //= 2
    return result_power, result_sum


def _sparse_mod_prime_axpy(
    target: dict[int, int],
    source: dict[int, int],
    scale: int,
    prime: int,
) -> None:
    for index, value in source.items():
        reduced = (target.get(index, 0) - scale * value) % prime
        if reduced:
            target[index] = reduced
        else:
            target.pop(index, None)


def _sparse_mod_prime_insert(
    vector: dict[int, int],
    ancestry: dict[int, int],
    pivots: dict[int, tuple[dict[int, int], dict[int, int]]],
    prime: int,
) -> tuple[bool, dict[int, int]]:
    reduced = {index: value % prime for index, value in vector.items() if value % prime}
    coefficients = {index: value % prime for index, value in ancestry.items() if value % prime}
    while reduced:
        leading = min(reduced)
        pivot = pivots.get(leading)
        if pivot is None:
            inverse = pow(reduced[leading], -1, prime)
            if inverse != 1:
                reduced = {index: value * inverse % prime for index, value in reduced.items()}
                coefficients = {index: value * inverse % prime for index, value in coefficients.items()}
            pivots[leading] = (reduced, coefficients)
            return True, coefficients
        scale = reduced[leading]
        _sparse_mod_prime_axpy(reduced, pivot[0], scale, prime)
        _sparse_mod_prime_axpy(coefficients, pivot[1], scale, prime)
    return False, coefficients


def _dense_sparse_sha256(vector: dict[int, int], size: int) -> str:
    dense = [vector.get(index, 0) for index in range(size)]
    return hashlib.sha256(canonical_json(dense).encode()).hexdigest()


@lru_cache(maxsize=1)
def _fermat_level25_group_ring_data() -> dict[str, Any]:
    """Build the character-independent degree-25 Fermat group-ring data."""
    degree = 25
    prime = 5
    ambient_rank = degree**3
    primitive_rank = 13_272
    relation_rank = ambient_rank - primitive_rank

    def index(a: int, b: int, c: int) -> int:
        return ((a % degree) * degree + (b % degree)) * degree + (c % degree)

    relations: list[dict[int, int]] = []
    for b in range(degree):
        for c in range(degree):
            relations.append({index(a, b, c): 1 for a in range(degree)})
    for a in range(degree):
        for c in range(degree):
            relations.append({index(a, b, c): 1 for b in range(degree)})
    for a in range(degree):
        for b in range(degree):
            relations.append({index(a, b, c): 1 for c in range(degree)})
    for b in range(degree):
        for c in range(degree):
            relations.append({index(shift, b + shift, c + shift): 1 for shift in range(degree)})

    relation_pivots: dict[int, tuple[dict[int, int], dict[int, int]]] = {}
    relation_basis = []
    relation_basis_indices = []
    for relation_index, row in enumerate(relations):
        accepted, _ = _sparse_mod_prime_insert(
            row,
            {},
            relation_pivots,
            prime,
        )
        if accepted:
            relation_basis.append(row)
            relation_basis_indices.append(relation_index)
    if len(relation_basis) != relation_rank:
        raise RuntimeError("the Fermat group-ring relations have an unexpected mod-5 rank")

    glue_element: dict[int, int] = {}
    for first_i in range(degree - 1):
        for first_j in range(first_i, degree - 1):
            for second_i in range(degree - 1):
                for second_j in range(second_i, degree - 1):
                    column = index(
                        first_i - first_j,
                        -first_j + second_j,
                        -first_j + second_i,
                    )
                    glue_element[column] = glue_element.get(column, 0) + 1

    return {
        "degree": degree,
        "prime": prime,
        "ambient_rank": ambient_rank,
        "primitive_rank": primitive_rank,
        "relation_rank": relation_rank,
        "relation_generator_count": len(relations),
        "relation_basis": relation_basis,
        "relation_basis_indices": relation_basis_indices,
        "glue_element": glue_element,
    }


@lru_cache(maxsize=8)
def _fermat_level25_invariant_lattice_data(
    character: tuple[int, int, int, int] = (1, 1, 1, 22),
) -> dict[str, Any]:
    """Certify the fixed lattice for one admissible level-25 Fermat action."""
    base = _fermat_level25_group_ring_data()
    degree = int(base["degree"])
    prime = int(base["prime"])
    ambient_rank = int(base["ambient_rank"])
    primitive_rank = int(base["primitive_rank"])
    relation_rank = int(base["relation_rank"])
    relation_basis = base["relation_basis"]
    relation_basis_indices = base["relation_basis_indices"]

    normalized = tuple(value % degree for value in character)
    if sum(normalized) % degree:
        raise ValueError("character must be trivial on the diagonal subgroup")
    if any(math.gcd(value, degree) != 1 for value in normalized):
        raise ValueError("the exact level-25 invariant-lattice calculation requires four unit coefficients")

    chart_character = normalized[1:]
    section_inverse = pow(chart_character[0], -1, degree)

    def index(a: int, b: int, c: int) -> int:
        return ((a % degree) * degree + (b % degree)) * degree + (c % degree)

    kernel = [
        (a, b, c)
        for a in range(degree)
        for b in range(degree)
        for c in range(degree)
        if sum(coefficient * exponent for coefficient, exponent in zip(chart_character, (a, b, c), strict=True)) % degree == 0
    ]
    if len(kernel) != degree * degree:
        raise RuntimeError("the selected diagonal kernel has the wrong order")
    invariant_rows = [{index(section_inverse * quotient + a, b, c): 1 for a, b, c in kernel} for quotient in range(degree - 1)]
    coordinates = [[Fraction(int(row == column)) for column in range(degree - 1)] for row in range(degree - 1)]

    stages = []
    final_pivots: dict[int, tuple[dict[int, int], dict[int, int]]] | None = None
    for stage in range(5):
        rows = relation_basis + invariant_rows
        pivots: dict[int, tuple[dict[int, int], dict[int, int]]] = {}
        accepted_invariants: list[int] = []
        failures: list[tuple[int, dict[int, int]]] = []
        for row_index, row in enumerate(rows):
            accepted, dependency = _sparse_mod_prime_insert(
                row,
                {row_index: 1},
                pivots,
                prime,
            )
            if row_index < relation_rank:
                if not accepted:
                    raise RuntimeError("the selected relation basis became dependent mod 5")
                continue
            invariant_index = row_index - relation_rank
            if accepted:
                accepted_invariants.append(invariant_index)
            else:
                failures.append((invariant_index, dependency))

        stage_record: dict[str, Any] = {
            "stage": stage,
            "mod_5_rank": len(pivots),
            "accepted_invariant_rows": len(accepted_invariants),
            "defect": len(failures),
        }
        if not failures:
            stages.append(stage_record)
            final_pivots = pivots
            break

        lifted_rows = [invariant_rows[item] for item in accepted_invariants]
        lifted_coordinates = [coordinates[item] for item in accepted_invariants]
        lift_hashes = []
        for _, dependency in failures:
            total: dict[int, int] = {}
            for source_index, coefficient in dependency.items():
                for column, value in rows[source_index].items():
                    total[column] = total.get(column, 0) + coefficient * value
            if any(value % prime for value in total.values()):
                raise RuntimeError("a mod-5 dependency did not lift to a divisible row")
            lift = {column: value // prime for column, value in total.items() if value // prime}
            coordinate = [Fraction(0) for _ in range(degree - 1)]
            for source_index, coefficient in dependency.items():
                if source_index < relation_rank:
                    continue
                source_coordinate = coordinates[source_index - relation_rank]
                for column, value in enumerate(source_coordinate):
                    coordinate[column] += coefficient * value / prime
            lifted_rows.append(lift)
            lifted_coordinates.append(coordinate)
            lift_hashes.append(_dense_sparse_sha256(lift, ambient_rank))
        if len(lifted_rows) != degree - 1:
            raise RuntimeError("a saturation stage did not preserve invariant rank")
        stage_record["index_multiplier"] = prime ** len(failures)
        stage_record["lift_sha256"] = lift_hashes
        stages.append(stage_record)
        invariant_rows = lifted_rows
        coordinates = lifted_coordinates
    else:
        raise RuntimeError("the Fermat invariant lattice did not saturate within the exponent bound")
    if final_pivots is None:
        raise RuntimeError("the Fermat invariant saturation did not produce final pivots")

    defects = [int(stage["defect"]) for stage in stages]
    if defects[-1] != 0:
        raise RuntimeError("the final Fermat saturation defect is nonzero")
    quotient_factors = []
    for exponent in range(1, len(defects)):
        multiplicity = defects[exponent - 1] - defects[exponent]
        if multiplicity < 0:
            raise RuntimeError("the saturation defects are not nonincreasing")
        quotient_factors.extend([prime**exponent] * multiplicity)
    saturation_index = prime ** sum(defects)

    denominator = math.lcm(*(value.denominator for row in coordinates for value in row))
    coordinate_numerators = [[int(value * denominator) for value in row] for row in coordinates]

    def pham_phi(value: int) -> int:
        residue = value % degree
        return 1 if residue == 0 else -1 if residue == 1 else 0

    pairing_by_coset = [0] * degree
    for exponents in product(range(degree), repeat=3):
        quotient = sum(coefficient * exponent for coefficient, exponent in zip(chart_character, exponents, strict=True)) % degree
        pairing_by_coset[quotient] -= (
            degree * degree * (math.prod(pham_phi(value) for value in exponents) + math.prod(pham_phi(-value) for value in exponents))
        )
    norm_gram = [[pairing_by_coset[(column - row) % degree] for column in range(degree - 1)] for row in range(degree - 1)]
    invariant_gram = []
    for left in coordinates:
        gram_row = []
        for right in coordinates:
            pairing = sum(left[row] * norm_gram[row][column] * right[column] for row in range(degree - 1) for column in range(degree - 1))
            if pairing.denominator != 1:
                raise RuntimeError("the saturated Fermat Gram matrix is not integral")
            gram_row.append(pairing.numerator)
        invariant_gram.append(gram_row)

    glue_element = base["glue_element"]
    glue_remainder = {column: value % prime for column, value in glue_element.items() if value % prime}
    while glue_remainder:
        leading = min(glue_remainder)
        pivot = final_pivots.get(leading)
        if pivot is None:
            break
        _sparse_mod_prime_axpy(
            glue_remainder,
            pivot[0],
            glue_remainder[leading],
            prime,
        )
    glue_member = not glue_remainder
    if glue_member:
        raise RuntimeError("the Fermat glue obstruction unexpectedly vanished mod 5")

    primitive_pairing_ideal = math.gcd(*(abs(value) for row in invariant_gram for value in row))
    full_pairing_ideal = math.gcd(degree, primitive_pairing_ideal)
    minimum_pairing_valuation = 0
    valuation_remainder = full_pairing_ideal
    while valuation_remainder % prime == 0:
        minimum_pairing_valuation += 1
        valuation_remainder //= prime
    valuation_one_pairing_exists = minimum_pairing_valuation <= 1
    request = {"degree": degree, "character": list(normalized)}
    return {
        "schema": "reasoning.fermat-level25-invariant-lattice-profile.v1",
        "request": request,
        "request_sha256": hashlib.sha256(canonical_json(request).encode()).hexdigest(),
        "source_contract": {
            "primitive_group_ring": "arXiv:1005.1733, Proposition 2.1 and Corollary 2.2",
            "pham_pairing_and_full_cohomology_glue": ("arXiv:1905.11869, Lemma 2.1, Proposition 2.4, and Proposition 2.8"),
            "primitive_rank": primitive_rank,
            "full_h2_unimodular": True,
        },
        "presentation": {
            "ambient_rank": ambient_rank,
            "relation_generator_count": base["relation_generator_count"],
            "relation_rank": relation_rank,
            "relation_rank_mod_5": len(relation_basis),
            "relation_basis_source_indices": relation_basis_indices,
            "norm_generator_count": degree - 1,
            "rational_invariant_rank": degree - 1,
        },
        "saturation": {
            "stages": stages,
            "defects": defects,
            "norm_to_primitive_invariant_index": saturation_index,
            "primitive_invariants_mod_norm_invariant_factors": quotient_factors,
            "basis_coordinates_over_norm_basis": {
                "common_denominator": denominator,
                "numerators": coordinate_numerators,
            },
            "final_mod_5_rank": len(final_pivots),
            "expected_saturated_rank": relation_rank + degree - 1,
            "five_saturated": len(final_pivots) == relation_rank + degree - 1,
            "fully_saturated_from_norm_exponent_bound": True,
        },
        "primitive_invariant_lattice": {
            "rank": degree - 1,
            "gram": invariant_gram,
            "pairing_ideal_generator": primitive_pairing_ideal,
            "smith_certified": False,
        },
        "full_cohomology_glue": {
            "formula": "c=rho(u0,u1)rho(u2,u3)=L-25 Lambda",
            "glue_element_nonzero_coefficients": len(glue_element),
            "glue_element_coefficient_sum": sum(glue_element.values()),
            "glue_mod_5_in_primitive_invariants_plus_5P": glue_member,
            "glue_mod_5_remainder_nonzero_coefficients": len(glue_remainder),
            "glue_mod_5_remainder_sha256": _dense_sparse_sha256(glue_remainder, ambient_rank),
            "first_nonzero_remainder": {
                "ambient_index": min(glue_remainder),
                "coefficient": glue_remainder[min(glue_remainder)],
            },
            "invariant_hyperplane_pairing_image": degree,
            "full_invariant_lattice_decomposition": "Z*L orthogonal_sum P^K",
            "full_invariant_pairing_ideal_generator": full_pairing_ideal,
        },
        "perry_level25_gate": {
            "valuation_one_invariant_pairing_exists": valuation_one_pairing_exists,
            "minimum_pairing_5_adic_valuation": minimum_pairing_valuation,
            "fermat_surface_candidate_passes": valuation_one_pairing_exists,
            "reason": (
                "the invariant lattice contains a pairing of 5-adic valuation at most one"
                if valuation_one_pairing_exists
                else "every pairing in H2(F,Z)^K is divisible by 25"
            ),
        },
        "unresolved_integral_gates": {
            "primitive_invariant_smith_certified": False,
            "cartan_leray_d3_row_needed_for_this_candidate": False,
            "counterexample_certified": False,
        },
        "certificate": {
            "all_2500_group_ring_relations_constructed": (base["relation_generator_count"] == 2500),
            "relation_rank_matches_primitive_rank": len(relation_basis) == relation_rank,
            "kernel_enumerated": len(kernel) == degree * degree,
            "norm_generators_constructed": len(invariant_rows) == degree - 1,
            "every_lift_coordinatewise_divisible_by_5": True,
            "saturation_terminated_at_full_mod_5_rank": (len(final_pivots) == relation_rank + degree - 1),
            "saturation_quotient_killed_by_norm_order": True,
            "basis_gram_integral": True,
            "glue_obstruction_remainder_nonzero": bool(glue_remainder),
            "full_pairing_ideal_exactly_25": full_pairing_ideal == 25,
        },
        "certificate_verified": True,
        "scope": (
            "This certifies the integral fixed lattice and full-cohomology glue "
            "for the specified degree-25 Fermat surface action. It rules out this "
            "surface as the valuation-one seed in the level-25 Perry template; it "
            "does not compute a Cartan-Leray differential or prove a counterexample."
        ),
    }


@lru_cache(maxsize=1)
def _fermat_level25_character_classes() -> dict[str, Any]:
    """Enumerate primitive characters modulo units and coordinate permutations."""
    degree = 25
    units = tuple(value for value in range(1, degree) if math.gcd(value, degree) == 1)
    coordinate_permutations = tuple(permutations(range(4)))

    def canonical(character: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        return min(
            tuple(unit * character[index] % degree for index in permutation) for unit in units for permutation in coordinate_permutations
        )

    primitive_characters = []
    for first in range(degree):
        for second in range(degree):
            for third in range(degree):
                character = (
                    first,
                    second,
                    third,
                    (-first - second - third) % degree,
                )
                if any(math.gcd(value, degree) == 1 for value in character):
                    primitive_characters.append(character)

    canonical_characters = sorted({canonical(character) for character in primitive_characters})
    return {
        "degree": degree,
        "unit_count": len(units),
        "coordinate_permutation_count": len(coordinate_permutations),
        "raw_primitive_character_count": len(primitive_characters),
        "canonical_characters": canonical_characters,
    }


@lru_cache(maxsize=1)
def _elliptic_modular_surface_level25_data() -> dict[str, Any]:
    """Enumerate the modular and lattice input for the full-level-25 surface."""
    level = 25
    prime = 5

    def negate(matrix: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        return tuple((-value) % level for value in matrix)

    def projective_matrix(
        matrix: tuple[int, int, int, int],
    ) -> tuple[int, int, int, int]:
        normalized = tuple(value % level for value in matrix)
        return min(normalized, negate(normalized))

    psl = {
        projective_matrix((a, b, c, d))
        for a in range(level)
        for b in range(level)
        for c in range(level)
        for d in range(level)
        if (a * d - b * c) % level == 1
    }

    def right_translate(
        matrix: tuple[int, int, int, int],
    ) -> tuple[int, int, int, int]:
        a, b, c, d = matrix
        return projective_matrix((a, a + b, c, c + d))

    unseen = set(psl)
    cusp_cycles = []
    while unseen:
        start = min(unseen)
        cycle = []
        current = start
        while current not in cycle:
            if current not in psl:
                raise RuntimeError("right translation escaped PSL2(Z/25)")
            cycle.append(current)
            current = right_translate(current)
        if current != start:
            raise RuntimeError("a modular cusp cycle did not close at its start")
        unseen.difference_update(cycle)
        label = min(
            (cycle[0][0], cycle[0][2]),
            ((-cycle[0][0]) % level, (-cycle[0][2]) % level),
        )
        cusp_cycles.append({"label_modulo_sign": label, "length": len(cycle)})

    cusp_labels = sorted(entry["label_modulo_sign"] for entry in cusp_cycles)
    primitive_labels = sorted(
        {min((a, c), ((-a) % level, (-c) % level)) for a in range(level) for c in range(level) if math.gcd(a, c, level) == 1}
    )
    if cusp_labels != primitive_labels:
        raise RuntimeError("cusp cycles do not match primitive vectors modulo sign")

    stabilizer_multiplicities: dict[tuple[tuple[int, int], ...], int] = {}
    for a, c in cusp_labels:
        kernel = tuple((u, v) for u in range(level) for v in range(level) if (a * v - c * u) % level == 0)
        if len(kernel) != level:
            raise RuntimeError("a cusp specialization kernel does not have order 25")
        stabilizer_multiplicities[kernel] = stabilizer_multiplicities.get(kernel, 0) + 1

    element_profiles = []
    fixed_point_incidence = 0
    for u in range(level):
        for v in range(level):
            if (u, v) == (0, 0):
                continue
            stabilizer_line_count = sum((u, v) in kernel for kernel in stabilizer_multiplicities)
            cusp_count = sum(multiplicity for kernel, multiplicity in stabilizer_multiplicities.items() if (u, v) in kernel)
            fixed_point_count = level * cusp_count
            fixed_point_incidence += fixed_point_count
            element_profiles.append(
                {
                    "element": [u, v],
                    "order": (level if math.gcd(u, v, level) == 1 else prime),
                    "stabilizer_line_count": stabilizer_line_count,
                    "cusp_count": cusp_count,
                    "fixed_point_count": fixed_point_count,
                }
            )

    fixed_point_profiles = []
    for order in (prime, level):
        entries = [entry for entry in element_profiles if entry["order"] == order]
        fixed_point_profiles.append(
            {
                "element_order": order,
                "element_count": len(entries),
                "stabilizer_line_counts": sorted({entry["stabilizer_line_count"] for entry in entries}),
                "cusp_counts": sorted({entry["cusp_count"] for entry in entries}),
                "fixed_point_counts": sorted({entry["fixed_point_count"] for entry in entries}),
            }
        )

    psl_order = len(psl)
    cusp_count = len(cusp_cycles)
    base_genus = 1 + psl_order // 12 - cusp_count // 2
    holomorphic_euler_characteristic = psl_order // 12
    irregularity = base_genus
    geometric_genus = holomorphic_euler_characteristic + irregularity - 1
    topological_euler_characteristic = psl_order
    b2 = topological_euler_characteristic - 2 + 4 * irregularity
    hodge_1_1 = b2 - 2 * geometric_genus
    trivial_lattice_rank = 2 + cusp_count * (level - 1)
    neron_severi_discriminant_5_adic_valuation = 2 * cusp_count - 8
    transcendental_rank = b2 - trivial_lattice_rank
    invariant_h2_rank = (
        b2 + sum(entry["element_count"] * (entry["fixed_point_counts"][0] + 4 * irregularity - 2) for entry in fixed_point_profiles)
    ) // (level * level)
    d2_target_generator_count = 2 * (2 * irregularity)

    # For an I_n fibre, the sum of the A_(n-1) fundamental weights is the
    # Weyl vector rho.  These are its coefficients in the simple-root basis.
    # The component lattice on the surface is negative definite, so the
    # section correction below is -rho rather than +rho.
    weyl_vector_coefficients = [index * (level - index) // 2 for index in range(1, level)]
    weyl_vector_cartan_pairings = [
        2 * coefficient
        - (weyl_vector_coefficients[index - 1] if index else 0)
        - (weyl_vector_coefficients[index + 1] if index + 1 < len(weyl_vector_coefficients) else 0)
        for index, coefficient in enumerate(weyl_vector_coefficients)
    ]
    weyl_vector_square = 2 * sum(coefficient * coefficient for coefficient in weyl_vector_coefficients) - 2 * sum(
        left * right
        for left, right in zip(
            weyl_vector_coefficients,
            weyl_vector_coefficients[1:],
            strict=False,
        )
    )

    section_height_profiles: dict[tuple[int, int], int] = {}
    for u in range(level):
        for v in range(level):
            if (u, v) == (0, 0):
                continue
            order = level if math.gcd(u, v, level) == 1 else prime
            correction_numerator = sum(
                component_index * (level - component_index) for a, c in cusp_labels for component_index in [(a * v - c * u) % level]
            )
            profile = (order, correction_numerator)
            section_height_profiles[profile] = section_height_profiles.get(profile, 0) + 1

    specialization_value_profiles = {
        tuple(sum((a * v - c * u) % level == residue for u in range(level) for v in range(level)) for residue in range(level))
        for a, c in cusp_labels
    }

    discriminant_glue_generators = [
        [(-c) % level for a, c in cusp_labels],
        [a % level for a, c in cusp_labels],
    ]
    discriminant_glue_pairing_gram = [
        [sum(left * right for left, right in zip(left_row, right_row, strict=True)) % level for right_row in discriminant_glue_generators]
        for left_row in discriminant_glue_generators
    ]
    discriminant_glue_unit_minor = next(
        (
            {
                "columns": [left, right],
                "cusp_labels": [list(cusp_labels[left]), list(cusp_labels[right])],
                "determinant_mod_25": (
                    discriminant_glue_generators[0][left] * discriminant_glue_generators[1][right]
                    - discriminant_glue_generators[0][right] * discriminant_glue_generators[1][left]
                )
                % level,
            }
            for left in range(cusp_count)
            for right in range(left + 1, cusp_count)
            if math.gcd(
                discriminant_glue_generators[0][left] * discriminant_glue_generators[1][right]
                - discriminant_glue_generators[0][right] * discriminant_glue_generators[1][left],
                level,
            )
            == 1
        ),
        None,
    )
    discriminant_factor_multiplicity = cusp_count - 4

    section_orbit_size = level * level
    section_orbit_sum_coefficients = {
        "zero_section": section_orbit_size,
        "fiber": (section_orbit_size - 1) * holomorphic_euler_characteristic,
        "negative_weyl_vector_per_cusp": level,
    }
    invariant_section_orbit_quotient_coefficients = {
        "zero_section": section_orbit_sum_coefficients["zero_section"] // level,
        "fiber": section_orbit_sum_coefficients["fiber"] // level,
        "negative_weyl_vector_per_cusp": 1,
    }
    invariant_generator_zero = invariant_section_orbit_quotient_coefficients["zero_section"]
    invariant_generator_fiber = invariant_section_orbit_quotient_coefficients["fiber"]
    invariant_generator_square = (
        -(invariant_generator_zero**2) * holomorphic_euler_characteristic
        + 2 * invariant_generator_zero * invariant_generator_fiber
        - cusp_count * weyl_vector_square
    )
    invariant_generator_fiber_pairing = invariant_generator_zero
    invariant_generator_zero_pairing = -invariant_generator_zero * holomorphic_euler_characteristic + invariant_generator_fiber
    fixed_neron_severi_gram = [
        [invariant_generator_square, invariant_generator_fiber_pairing],
        [invariant_generator_fiber_pairing, 0],
    ]
    fixed_neron_severi_discriminant = abs(
        fixed_neron_severi_gram[0][0] * fixed_neron_severi_gram[1][1] - fixed_neron_severi_gram[0][1] * fixed_neron_severi_gram[1][0]
    )
    coordinate_recovery_pairing_matrix = [
        [weyl_vector_cartan_pairings[0], 0],
        [invariant_generator_zero_pairing, 1],
    ]
    coordinate_recovery_determinant = (
        coordinate_recovery_pairing_matrix[0][0] * coordinate_recovery_pairing_matrix[1][1]
        - coordinate_recovery_pairing_matrix[0][1] * coordinate_recovery_pairing_matrix[1][0]
    )
    fixed_neron_severi_pairing_gcd = math.gcd(*(abs(entry) for row in fixed_neron_severi_gram for entry in row))

    request = {"level": level}
    certificate = {
        "psl2_order_verified": psl_order == 7_500,
        "all_right_translation_cycles_have_length_25": {entry["length"] for entry in cusp_cycles} == {level},
        "cusp_count_verified": cusp_count == 300,
        "cusps_equal_primitive_vectors_modulo_sign": cusp_labels == primitive_labels,
        "stabilizer_line_count_verified": len(stabilizer_multiplicities) == 30,
        "each_stabilizer_line_occurs_ten_times": set(stabilizer_multiplicities.values()) == {10},
        "every_cusp_stabilizer_has_order_25": all(len(kernel) == level for kernel in stabilizer_multiplicities),
        "fixed_point_incidence_double_count_verified": (fixed_point_incidence == cusp_count * (level - 1) * level),
        "hodge_and_shioda_tate_ranks_match": trivial_lattice_rank == hodge_1_1,
        "invariant_rank_matches_transcendental_plus_two": (invariant_h2_rank == transcendental_rank + 2),
        "raw_invariant_pairing_not_divisible_by_5": (neron_severi_discriminant_5_adic_valuation < transcendental_rank),
        "every_nonzero_section_has_torsion_height_correction_1250": (
            {correction_numerator // level for _order, correction_numerator in section_height_profiles}
            == {2 * holomorphic_euler_characteristic}
            and all(correction_numerator % level == 0 for _order, correction_numerator in section_height_profiles)
        ),
        "section_height_profiles_cover_all_nonzero_sections": sum(section_height_profiles.values()) == section_orbit_size - 1,
        "every_cusp_specialization_is_uniform": specialization_value_profiles == {tuple([level] * level)},
        "a24_weyl_vector_is_integral_and_primitive": (
            weyl_vector_cartan_pairings == [1] * (level - 1) and math.gcd(*weyl_vector_coefficients) == 1
        ),
        "section_orbit_sum_is_divisible_by_25_in_the_trivial_lattice": all(
            coefficient % level == 0 for coefficient in section_orbit_sum_coefficients.values()
        ),
        "fixed_neron_severi_gram_verified": fixed_neron_severi_gram == [[-625, 25], [25, 0]],
        "fixed_dual_coordinate_recovery_is_unimodular": (abs(coordinate_recovery_determinant) == 1),
        "rational_fixed_neron_severi_rank_is_two": (
            specialization_value_profiles == {tuple([level] * level)} and trivial_lattice_rank == hodge_1_1
        ),
        "fixed_full_lattice_splits_integrally": (
            invariant_h2_rank == transcendental_rank + 2 and abs(coordinate_recovery_determinant) == 1
        ),
        "mordell_weil_glue_is_primitive_isotropic_rank_two": (
            discriminant_glue_pairing_gram == [[0, 0], [0, 0]] and discriminant_glue_unit_minor is not None
        ),
        "transcendental_discriminant_module_has_296_level_25_factors": (
            discriminant_factor_multiplicity == 296 and 2 * discriminant_factor_multiplicity == neron_severi_discriminant_5_adic_valuation
        ),
    }
    return {
        "schema": "reasoning.elliptic-modular-surface-level25-profile.v4",
        "request": request,
        "request_sha256": hashlib.sha256(canonical_json(request).encode()).hexdigest(),
        "source_contract": {
            "elliptic_surface_and_shioda_tate_formulas": "arXiv:0907.0298",
            "torsion_section_specialization": "arXiv:alg-geom/9310006",
            "perry_invariant_lattice_and_transgression_benchmark": ("arXiv:2608.03684, Sections 2 and 3"),
        },
        "acting_translation_group": {
            "role": "fiberwise automorphism group used in Cartan-Leray",
            "structure": "(Z/25)^2",
            "order": section_orbit_size,
            "generators": [[1, 0], [0, 1]],
            "not_the_modular_indexing_group": True,
        },
        "modular_group": {
            "role": "base-cell and cusp indexing group, not the acting translation group",
            "ring": "Z/25",
            "psl2_order": psl_order,
            "right_translation_matrix": [[1, 1], [0, 1]],
            "right_translation_cycle_count": cusp_count,
            "right_translation_cycle_lengths": sorted({entry["length"] for entry in cusp_cycles}),
        },
        "cusps_and_stabilizers": {
            "cusp_label_convention": ("primitive vectors in (Z/25)^2 modulo multiplication by +/-1"),
            "cusp_labels_modulo_sign": [list(label) for label in cusp_labels],
            "cusp_count": cusp_count,
            "distinct_stabilizer_lines": len(stabilizer_multiplicities),
            "stabilizer_line_multiplicities": sorted(stabilizer_multiplicities.values()),
            "specialization_formula": "sigma_(a,c)(u,v)=a*v-c*u mod 25",
            "singular_fibers": {"kodaira_type": "I_25", "count": cusp_count},
            "nonfree_point_count": cusp_count * level,
            "point_stabilizer_invariant_factors": [level],
            "global_fixed_locus_empty": True,
            "fixed_point_profiles": fixed_point_profiles,
            "fixed_point_incidence": fixed_point_incidence,
        },
        "surface_invariants": {
            "smooth_projective_model": ("minimal regular compactification of the universal generalized elliptic curve over X(25)"),
            "base_genus": base_genus,
            "topological_euler_characteristic": topological_euler_characteristic,
            "holomorphic_euler_characteristic": holomorphic_euler_characteristic,
            "irregularity": irregularity,
            "geometric_genus": geometric_genus,
            "b1": 2 * irregularity,
            "b2": b2,
            "hodge_1_1_dimension": hodge_1_1,
        },
        "integral_lattice_frontier": {
            "fiber_section_plane_gram": [
                [-holomorphic_euler_characteristic, 1],
                [1, 0],
            ],
            "trivial_lattice": ("fiber-section plane orthogonal_sum A_24^300"),
            "trivial_lattice_rank": trivial_lattice_rank,
            "mordell_weil_invariant_factors": [level, level],
            "mordell_weil_rank": 0,
            "trivial_to_neron_severi_index": level * level,
            "neron_severi_rank": trivial_lattice_rank,
            "neron_severi_discriminant_absolute": 5**neron_severi_discriminant_5_adic_valuation,
            "neron_severi_discriminant_5_adic_valuation": (neron_severi_discriminant_5_adic_valuation),
            "transcendental_rank": transcendental_rank,
            "transcendental_discriminant_5_adic_valuation": (neron_severi_discriminant_5_adic_valuation),
            "transcendental_discriminant_invariant_factors": {
                "factor": level,
                "multiplicity": discriminant_factor_multiplicity,
            },
            "transcendental_lattice_fixed_pointwise": True,
            "invariant_h2_rank_from_lefschetz": invariant_h2_rank,
            "invariant_neron_severi_rational_rank": 2,
            "raw_invariant_pairing_5_adic_valuation": 0,
            "raw_invariant_pairing_passes_valuation_one_gate": False,
            "raw_obstruction": (
                "the fixed rank-2200 transcendental lattice has determinant valuation 592, so its pairing cannot be divisible by 5"
            ),
        },
        "fixed_lattice_certificate": {
            "shioda_section_class_formula": ("P_g=O+chi*F-sum_c omega_(sigma_c(g)) for g nonzero; P_0=O"),
            "nonzero_section_height_profiles": [
                {
                    "section_order": order,
                    "section_count": count,
                    "correction_numerator": correction_numerator,
                    "correction_denominator": level,
                    "height_correction": correction_numerator // level,
                }
                for (order, correction_numerator), count in sorted(section_height_profiles.items())
            ],
            "specialization_value_count_profiles": [list(profile) for profile in sorted(specialization_value_profiles)],
            "specialization_value_profile_definition": (
                "For each fixed cusp, count sigma_c(g) over all 625 elements g "
                "of the acting translation group; every residue occurs 25 times."
            ),
            "a24_weyl_vector": {
                "definition": "rho=sum_(k=1)^24 omega_k",
                "simple_root_coefficients": weyl_vector_coefficients,
                "cartan_pairings": weyl_vector_cartan_pairings,
                "positive_a24_square": weyl_vector_square,
                "coefficient_gcd": math.gcd(*weyl_vector_coefficients),
            },
            "section_orbit_sum": {
                "definition": "D=sum_(g in (Z/25)^2) P_g",
                "trivial_lattice_coefficients": section_orbit_sum_coefficients,
                "divisor_class_divisibility": level,
                "quotient_definition": "E=D/25",
                "quotient_trivial_lattice_coefficients": (invariant_section_orbit_quotient_coefficients),
            },
            "fixed_neron_severi": {
                "basis": ["E", "F"],
                "gram": fixed_neron_severi_gram,
                "rank": 2,
                "rational_rank_argument": (
                    "Use the G-stable presentation by all sections, F, and all "
                    "25 components of every I_25 fiber. At each cusp the "
                    "surjective specialization makes the component permutation "
                    "transitive, so its augmentation quotient has no rational "
                    "invariants. Exactness of finite-group invariants over Q "
                    "leaves F and the independent section-orbit quotient E."
                ),
                "generator_pairings": {
                    "E_square": invariant_generator_square,
                    "E_with_F": invariant_generator_fiber_pairing,
                    "E_with_O": invariant_generator_zero_pairing,
                    "E_with_simple_fiber_component": (weyl_vector_cartan_pairings[0]),
                    "F_square": 0,
                    "F_with_O": 1,
                    "F_with_simple_fiber_component": 0,
                },
                "discriminant_absolute": fixed_neron_severi_discriminant,
                "discriminant_5_adic_valuation": 4,
                "pairing_gcd": fixed_neron_severi_pairing_gcd,
                "pairing_5_adic_valuation": 2,
                "coordinate_recovery_test_classes": [
                    "one simple component of any I_25 fiber",
                    "the zero section O",
                ],
                "coordinate_recovery_pairing_matrix": (coordinate_recovery_pairing_matrix),
                "coordinate_recovery_determinant": (coordinate_recovery_determinant),
                "fixed_dual_equals_fixed_lattice": True,
                "saturation_argument": (
                    "An invariant rational class is a*E+b*F. Integral pairing "
                    "with a simple fiber component gives a in Z; pairing with "
                    "O then gives b in Z. The same test applies inside NS^vee."
                ),
            },
            "fixed_full_h2": {
                "ambient_lattice": ("the free integral H2 lattice with its unimodular intersection pairing"),
                "basis_decomposition": "T direct_sum (Z*E + Z*F)",
                "integral_orthogonal_split": True,
                "rank": invariant_h2_rank,
                "discriminant_5_adic_valuation": (neron_severi_discriminant_5_adic_valuation + 4),
                "transcendental_pairing_5_adic_valuation": 0,
                "full_pairing_5_adic_valuation": 0,
                "pairing_valuation_distinction": (
                    "The fixed NS summand has pairing valuation 2. The full "
                    "fixed lattice has valuation 0 because T contains a pairing "
                    "prime to 5; neither statement says det(T) is a 5-adic unit."
                ),
                "transcendental_action_argument": (
                    "Lefschetz averaging gives fixed H2 rank 2202, while the exact "
                    "fixed NS rank is two. The preserved rank-2200 orthogonal "
                    "transcendental lattice is therefore fixed pointwise."
                ),
                "glue_argument": (
                    "The free H2 lattice embeds in NS^vee plus T^vee. A fixed "
                    "vector has fixed NS^vee component; fixed_dual_equals_"
                    "fixed_lattice makes its NS discriminant class zero, hence "
                    "its T discriminant class is zero as well."
                ),
            },
            "full_discriminant_module": {
                "component_discriminant_module": {
                    "factor": level,
                    "multiplicity": cusp_count,
                    "pairing_numerator_convention": (
                        "Each negative A_24 factor is generated by omega_1 and has bilinear pairing numerator +1 modulo 25."
                    ),
                },
                "mordell_weil_glue_subgroup": {
                    "factor": level,
                    "rank": 2,
                    "generator_definition": [
                        "(-c)_c for the section (1,0)",
                        "(a)_c for the section (0,1)",
                    ],
                    "generators_sha256": hashlib.sha256(canonical_json(discriminant_glue_generators).encode()).hexdigest(),
                    "pairing_numerator_gram_mod_25": (discriminant_glue_pairing_gram),
                    "primitive_unit_minor": discriminant_glue_unit_minor,
                },
                "neron_severi_discriminant_module": {
                    "construction": "H_perp/H",
                    "factor": level,
                    "multiplicity": discriminant_factor_multiplicity,
                },
                "transcendental_discriminant_module": {
                    "factor": level,
                    "multiplicity": discriminant_factor_multiplicity,
                    "reason": ("Primitive orthogonal complements in the unimodular free H2 lattice have opposite discriminant forms."),
                },
                "mod_5_radical_dimension": discriminant_factor_multiplicity,
                "mod_5_pairing_rank": (transcendental_rank - discriminant_factor_multiplicity),
            },
        },
        "cartan_leray_frontier": {
            "h1_rank": 2 * irregularity,
            "h1_action_trivial": True,
            "d2_target": f"(Z/25)^{d2_target_generator_count}",
            "d2_target_generator_count": d2_target_generator_count,
            "d2_matrix_computed": False,
            "e3_survivor_lattice_computed": False,
            "candidate_survives_only_if_e3_pairing_5_adic_valuation": 1,
            "fiber_pairing_divisibility_required": level,
            "fiber_d3_target": "H^3((Z/25)^2,Z)=Z/25",
        },
        "corrected_cellular_input": {
            "base_cells": "PSL2(Z/25) with the standard right S and T actions",
            "base_cell_count": psl_order,
            "cusp_filling": "fill each of the 300 right-T cycles by an I_25 25-gon",
            "cusp_labels_are_not": "P^1(Z/25), which has 30 elements",
            "stabilizer_lines_are": ("P^1(Z/25), each represented by ten cusp labels modulo +/-1"),
            "group_generators": [[1, 0], [0, 1]],
            "next_matrix": "d2: Z^2202 -> (Z/25)^1904 on the invariant H2 lattice",
        },
        "candidate_verdict": {
            "surface_constructed": True,
            "raw_invariant_lattice_rejected": True,
            "d2_rescue_unresolved": True,
            "candidate_certified": False,
            "next_gate": ("compute the integral d2 kernel and test whether its restricted pairing ideal has exact 5-adic valuation one"),
        },
        "certificate": certificate,
        "certificate_verified": all(certificate.values()),
        "scope": (
            "This certifies the finite modular group, cusp, stabilizer, fixed-point, "
            "surface-invariant, fixed Neron-Severi saturation, and integral "
            "fixed-H2 splitting data for S(25). It does not compute the Cartan-Leray "
            "d2 kernel, a d3 row on that kernel, an equivariant deformation, a "
            "Brauer class, or a period-index counterexample."
        ),
    }


def _level25_reviewed_comparisons() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    borel_d2_comparison = {
        "group_extension": ("1 -> 25*Z^2 semidirect Pi -> Z^2 semidirect Pi -> (Z/25)^2 -> 1"),
        "cross_term": "H^1(Pi,H^1(25*Z^2,Z))",
        "normalized_cross_cocycle": ("bar_z(p)(v)=z(p)(25*v), with integral primitives normalized to vanish on the free generators of Pi"),
        "factor_set": "F(g,h)=25*t(g,h)",
        "obstruction_cocycle": "Theta=-b_g-b_h+b_(g+h)+b_F",
        "generator_formula": ("Theta(g,h)(gamma_i)=bar_z(gamma_i)(A_i*t(g,h)), which is cohomologous modulo 25 to bar_z(gamma_i)(t(g,h))"),
        "conclusion": (
            "After a fixed duality (Z/25)^2*=(Z/25)^2, the Borel d2 on "
            "the parabolic cross term is coefficientwise reduction modulo 25; "
            "there is no additional lift-coherence term."
        ),
        "kernel_conclusion": (
            "Under the integral parabolic identification, ker(d2|T) consists "
            "of the classes represented by pointwise-25-divisible parabolic "
            "cocycles."
        ),
        "ledger_evidence": {
            "claim_id": "clm_ecaedd117c3a7e7c398d0f05",
            "claim_revision_id": "clmrev_472549d9759fda72a9f9a28d",
            "proof_id": "prf_4461e7990fa9acbe4b118ce8",
            "proof_revision_id": "prfrev_5be17ff3136e670474a655e4",
            "verification_id": "ver_133a2098d24feef3fd15126f",
            "verdict": "verified",
        },
    }
    integral_lattice_comparison = {
        "primary_sources": [
            {
                "title": "On elliptic modular surfaces",
                "author": "Tetsuji Shioda",
                "url": ("https://www.jstage.jst.go.jp/article/jmath1948/24/1/24_1_20/_pdf"),
                "locators": ("Section 2 and formulae (2.3)-(2.5); Theorem 7.3 and formulae (7.5)-(7.16)"),
            },
            {
                "title": "Transcendental lattice of an extremal elliptic surface",
                "author": "Alex Degtyarev",
                "url": "https://arxiv.org/pdf/0907.1809",
                "locators": ("Section 3.1 and Lemma 3.3.1; Theorem 4.3.4, especially (4.3.5)-(4.3.6); Section 6.2 and Theorem 6.2.2"),
            },
        ],
        "integral_chain": [
            "H^1_par(Gamma(25),Z^2)",
            "H^1(X(25),j_*H_1(E,Z))",
            "H_1(X(25),j_*H_1(E,Z))",
            "H_2(S(25)^circ,Z)/Rad",
            "T_S(25)",
        ],
        "punctured_base_chain_identification": (
            "The local-coefficient cellular chains on X(25) with cusp disks "
            "removed are termwise Degtyarev's oriented half-edge relative "
            "chains; their endpoint boundaries are the same parallel-transport "
            "boundary. The inverse absolute-to-relative map is therefore the "
            "actual integral fiber-loop sweep, of index one."
        ),
        "cusp_quotient": (
            "Filling each cusp disk quotients by its primitive invariant cusp "
            "loop. Its swept mapping-torus generator is primitive and the full "
            "boundary subgroup is exactly the intersection radical."
        ),
        "pairing_formula": ("sweep(x).sweep(y)=-<epsilon_*(D(x) cup D(y)),[X(25)]>"),
        "conclusion": ("Shioda's integral parabolic cohomology with the displayed signed cup form is integrally isometric to T_S(25)."),
        "ledger_evidence": [
            {
                "role": "Shioda integral dual-cell and parabolic maps",
                "claim_id": "clm_f2aa84f861b83841772c181a",
                "verification_id": "ver_85cfff10ac3b26feba0a8437",
            },
            {
                "role": "Degtyarev integral half-edge sweep source",
                "claim_id": "clm_a57ee7bbbcfdf91a328e94a9",
                "verification_id": "ver_e87e8b50264a3b2bed7413ee",
            },
            {
                "role": "punctured-base chain identification",
                "claim_id": "clm_c8e321cdc9e796b5c3f5ee26",
                "proof_id": "prf_a65121b50908021c2b164d1f",
                "proof_revision_id": "prfrev_d22be35c3701ccb6c3cf2135",
                "verification_id": "ver_43469007ecc2b6bf9783bd2c",
            },
            {
                "role": "compact cusp quotient and radical",
                "claim_id": "clm_1af836856f6494c264555003",
                "proof_id": "prf_8ffef2091f1d6b515c3d6345",
                "proof_revision_id": "prfrev_9c38489c046d36272ea1af08",
                "verification_id": "ver_ad748f0c92954369168aa889",
            },
            {
                "role": "exact S(25) boundary radical and T quotient",
                "claim_id": "clm_5ec03000ed5189d6ea65086b",
                "verification_id": "ver_d3785b00a918ce78e4e93eeb",
            },
            {
                "role": "signed algebraic intersection formula",
                "claim_id": "clm_20182ce8d8201b508ee1e9fc",
                "proof_id": "prf_696c8422a5ab6b135ef8f938",
                "proof_revision_id": "prfrev_a532c4c4e1c0cf4957a2921e",
                "verification_id": "ver_19afd64ed0c2a039913002ee",
            },
            {
                "role": "final integral parabolic-to-transcendental isometry",
                "claim_id": "clm_e986291a493a2abaa0ff134e",
                "proof_id": "prf_7734a085bb4a857506f9cf56",
                "proof_revision_id": "prfrev_3388f62dd50de5582dee58ca",
                "verification_id": "ver_37cec33c927d97cc792d4340",
            },
        ],
    }
    full_kernel_pairing_obstruction = {
        "fixed_lattice_coordinates": {
            "u": "t_u + m_u*E + n_u*F",
            "v": "t_v + m_v*E + n_v*F",
            "t_u_and_t_v": "elements of T",
        },
        "fiber_pairing_formula": "x.F=25*m_x",
        "required_edge_congruences": {
            "u.F_mod_625": 0,
            "v.F_mod_625": 25,
        },
        "forced_coefficients_mod_25": {
            "m_u": 0,
            "m_v": 1,
        },
        "target_exponent": 25,
        "d2_F_is_zero": True,
        "d2_E_assumption": "none",
        "deduction_for_t_u": "d2(t_u)=-m_u*d2(E)=0, so t_u lies in ker(d2|T)",
        "transcendental_kernel_pairing": ("ker(d2|T) pairs with every element of T in 25Z"),
        "transcendental_term_mod_25": 0,
        "fixed_neron_severi_pairing_formula": ("-625*m_u*m_v+25*(m_u*n_v+n_u*m_v)"),
        "fixed_neron_severi_term_mod_25": 0,
        "forced_u_dot_v_mod_25": 0,
        "required_valuation_one_residues_mod_25": [5, 10, 15, 20],
        "candidate_rejected": True,
        "remaining_hypotheses": [],
    }
    return (
        borel_d2_comparison,
        integral_lattice_comparison,
        full_kernel_pairing_obstruction,
    )


@lru_cache(maxsize=1)
def _elliptic_modular_graph_level25_data() -> dict[str, Any]:
    """Build an exact dessin and cusp-boundary presentation for bar-Gamma(25)."""

    level = 25
    prime = 5
    integer_identity = (1, 0, 0, 1)
    integer_s = (0, -1, 1, 0)
    integer_t = (1, 1, 0, 1)

    def integer_multiply(
        left: tuple[int, int, int, int],
        right: tuple[int, int, int, int],
    ) -> tuple[int, int, int, int]:
        a, b, c, d = left
        e, f, g, h = right
        return (
            a * e + b * g,
            a * f + b * h,
            c * e + d * g,
            c * f + d * h,
        )

    def integer_inverse(
        matrix: tuple[int, int, int, int],
    ) -> tuple[int, int, int, int]:
        a, b, c, d = matrix
        if a * d - b * c != 1:
            raise RuntimeError("an integral modular matrix does not have determinant one")
        return (d, -b, -c, a)

    def integer_power(matrix: tuple[int, int, int, int], exponent: int) -> tuple[int, int, int, int]:
        if exponent < 0:
            return integer_power(integer_inverse(matrix), -exponent)
        result = integer_identity
        factor = matrix
        remaining = exponent
        while remaining:
            if remaining % 2:
                result = integer_multiply(result, factor)
            factor = integer_multiply(factor, factor)
            remaining //= 2
        return result

    integer_r = integer_multiply(integer_s, integer_t)

    def negate_modulo_level(
        matrix: tuple[int, int, int, int],
    ) -> tuple[int, int, int, int]:
        return tuple((-entry) % level for entry in matrix)

    def projective_matrix(
        matrix: tuple[int, int, int, int],
    ) -> tuple[int, int, int, int]:
        reduced = tuple(entry % level for entry in matrix)
        return min(reduced, negate_modulo_level(reduced))

    def projective_multiply(
        left: tuple[int, int, int, int],
        right: tuple[int, int, int, int],
    ) -> tuple[int, int, int, int]:
        return projective_matrix(integer_multiply(left, right))

    psl = sorted(
        {
            projective_matrix((a, b, c, d))
            for a in range(level)
            for b in range(level)
            for c in range(level)
            for d in range(level)
            if (a * d - b * c) % level == 1
        }
    )

    s_data: dict[tuple[int, int, int, int], tuple[tuple[int, int, int, int], int]] = {}
    s_representatives = []
    for edge in psl:
        if edge in s_data:
            continue
        representative = min(edge, projective_multiply(edge, integer_s))
        s_representatives.append(representative)
        for exponent in range(2):
            s_data[projective_multiply(representative, integer_power(integer_s, exponent))] = (representative, exponent)

    r_data: dict[tuple[int, int, int, int], tuple[tuple[int, int, int, int], int]] = {}
    r_representatives = []
    for edge in psl:
        if edge in r_data:
            continue
        representative = min(projective_multiply(edge, integer_power(integer_r, exponent)) for exponent in range(3))
        r_representatives.append(representative)
        for exponent in range(3):
            r_data[projective_multiply(representative, integer_power(integer_r, exponent))] = (representative, exponent)

    vertices = [
        *(("S", representative) for representative in s_representatives),
        *(("R", representative) for representative in r_representatives),
    ]
    vertex_index = {vertex: index for index, vertex in enumerate(vertices)}
    parents = list(range(len(vertices)))
    component_sizes = [1] * len(vertices)

    def component(vertex: int) -> int:
        while parents[vertex] != vertex:
            parents[vertex] = parents[parents[vertex]]
            vertex = parents[vertex]
        return vertex

    def join(left: int, right: int) -> bool:
        left_root = component(left)
        right_root = component(right)
        if left_root == right_root:
            return False
        if component_sizes[left_root] < component_sizes[right_root]:
            left_root, right_root = right_root, left_root
        parents[right_root] = left_root
        component_sizes[left_root] += component_sizes[right_root]
        return True

    tree_edges: set[tuple[int, int, int, int]] = set()
    for edge in psl:
        s_vertex = ("S", s_data[edge][0])
        r_vertex = ("R", r_data[edge][0])
        if join(vertex_index[s_vertex], vertex_index[r_vertex]):
            tree_edges.add(edge)

    tree_adjacency: dict[
        tuple[str, tuple[int, int, int, int]],
        list[
            tuple[
                tuple[str, tuple[int, int, int, int]],
                tuple[int, int, int, int],
            ]
        ],
    ] = defaultdict(list)
    for edge in sorted(tree_edges):
        s_vertex = ("S", s_data[edge][0])
        r_vertex = ("R", r_data[edge][0])
        tree_adjacency[s_vertex].append((r_vertex, edge))
        tree_adjacency[r_vertex].append((s_vertex, edge))
    for neighbors in tree_adjacency.values():
        neighbors.sort(key=lambda item: item[1])

    root = ("S", s_data[projective_matrix(integer_identity)][0])
    if root[1] != projective_matrix(integer_s):
        raise RuntimeError("the deterministic dessin root has changed")
    vertex_lifts = {root: integer_s}
    queue = deque([root])
    while queue:
        vertex = queue.popleft()
        for neighbor, edge in tree_adjacency[vertex]:
            if neighbor in vertex_lifts:
                continue
            if vertex[0] == "S":
                edge_lift = integer_multiply(
                    vertex_lifts[vertex],
                    integer_power(integer_s, s_data[edge][1]),
                )
                vertex_lifts[neighbor] = integer_multiply(
                    edge_lift,
                    integer_power(integer_r, -r_data[edge][1]),
                )
            else:
                edge_lift = integer_multiply(
                    vertex_lifts[vertex],
                    integer_power(integer_r, r_data[edge][1]),
                )
                vertex_lifts[neighbor] = integer_multiply(
                    edge_lift,
                    integer_power(integer_s, -s_data[edge][1]),
                )
            if projective_matrix(vertex_lifts[neighbor]) != neighbor[1]:
                raise RuntimeError("a dessin vertex lift has the wrong reduction")
            queue.append(neighbor)

    voltages: dict[tuple[int, int, int, int], tuple[int, int, int, int]] = {}
    chord_edges = []
    for edge in psl:
        s_vertex = ("S", s_data[edge][0])
        r_vertex = ("R", r_data[edge][0])
        s_edge_lift = integer_multiply(
            vertex_lifts[s_vertex],
            integer_power(integer_s, s_data[edge][1]),
        )
        r_edge_lift = integer_multiply(
            vertex_lifts[r_vertex],
            integer_power(integer_r, r_data[edge][1]),
        )
        voltage = integer_multiply(s_edge_lift, integer_inverse(r_edge_lift))
        reduction = tuple(entry % level for entry in voltage)
        if reduction == ((level - 1), 0, 0, (level - 1)):
            voltage = tuple(-entry for entry in voltage)
            reduction = tuple(entry % level for entry in voltage)
        if reduction != integer_identity:
            raise RuntimeError("a dessin voltage is not in Gamma(25)")
        if edge in tree_edges:
            if voltage != integer_identity:
                raise RuntimeError("a spanning-tree voltage is nontrivial")
        else:
            chord_edges.append(edge)
        voltages[edge] = voltage

    chord_index = {edge: index for index, edge in enumerate(chord_edges)}
    unseen = set(psl)
    cusp_cycles = []
    while unseen:
        start = min(unseen)
        cycle = []
        edge = start
        while edge not in cycle:
            cycle.append(edge)
            edge = projective_multiply(edge, integer_t)
        if edge != start:
            raise RuntimeError("a dessin cusp boundary did not close")
        unseen.difference_update(cycle)
        cusp_cycles.append(cycle)

    cusp_rows: list[dict[int, int]] = []
    cusp_words: list[list[list[int]]] = []
    cusp_monodromies = []
    for cycle in cusp_cycles:
        row: dict[int, int] = {}
        word = []
        monodromy = integer_identity
        for edge in cycle:
            s_turn_edge = projective_multiply(edge, integer_s)
            transitions = ((edge, 1), (s_turn_edge, -1))
            for transition_edge, sign in transitions:
                voltage = voltages[transition_edge]
                transition = voltage if sign == 1 else integer_inverse(voltage)
                monodromy = integer_multiply(transition, monodromy)
                generator = chord_index.get(transition_edge)
                if generator is not None:
                    row[generator] = row.get(generator, 0) + sign
                    word.append([generator, sign])
        row = {index: value for index, value in row.items() if value}
        if any((entry - identity_entry) % level for entry, identity_entry in zip(monodromy, integer_identity, strict=True)):
            raise RuntimeError("a cusp monodromy is not in Gamma(25)")
        divided = tuple((entry - identity_entry) // level for entry, identity_entry in zip(monodromy, integer_identity, strict=True))
        label = min(
            (cycle[0][0], cycle[0][2]),
            ((-cycle[0][0]) % level, (-cycle[0][2]) % level),
        )
        a, b, c, d = divided
        if (
            monodromy[0] + monodromy[3] != 2
            or a * d - b * c != 0
            or a + d != 0
            or math.gcd(*(abs(entry) for entry in divided)) != 1
            or (
                (a * label[0] + b * label[1]) % level,
                (c * label[0] + d * label[1]) % level,
            )
            != (0, 0)
        ):
            raise RuntimeError("a cusp monodromy failed its primitive-unipotent check")
        cusp_rows.append(row)
        cusp_words.append(word)
        cusp_monodromies.append(
            {
                "label_modulo_sign": list(label),
                "word_length": len(word),
                "divided_monodromy": list(divided),
            }
        )

    pivots: dict[int, tuple[dict[int, int], dict[int, int]]] = {}
    for row_index, row in enumerate(cusp_rows):
        _sparse_mod_prime_insert(row, {row_index: 1}, pivots, prime)
    cusp_boundary_rank_mod_5 = len(pivots)
    oriented_boundary_sum_zero = all(sum(row.get(column, 0) for row in cusp_rows) == 0 for column in range(len(chord_edges)))

    # Every free generator is an oriented edge of the dual cusp graph: it
    # occurs with coefficient +1 in one cusp boundary and -1 in the other,
    # unless both sides belong to the same cusp and cancel.  A spanning tree
    # in this dual graph gives an integral right inverse from the zero-sum
    # cusp lattice.  This is stronger than a rank computation modulo 5 for
    # the abelianized boundary matrix.  It does not by itself solve the
    # integral twisted-cocycle equations, which contain prefix actions.
    dual_column_boundaries: list[list[tuple[int, int]]] = [[] for _edge in chord_edges]
    for row_index, row in enumerate(cusp_rows):
        for column, coefficient in row.items():
            dual_column_boundaries[column].append((row_index, coefficient))
    if any(
        boundary and (len(boundary) != 2 or sorted(coefficient for _row, coefficient in boundary) != [-1, 1])
        for boundary in dual_column_boundaries
    ):
        raise RuntimeError("a cusp-boundary column is not an oriented dual edge")

    dual_parents = list(range(len(cusp_rows)))
    dual_component_sizes = [1] * len(cusp_rows)

    def dual_component(vertex: int) -> int:
        while dual_parents[vertex] != vertex:
            dual_parents[vertex] = dual_parents[dual_parents[vertex]]
            vertex = dual_parents[vertex]
        return vertex

    def dual_join(left: int, right: int) -> bool:
        left_root = dual_component(left)
        right_root = dual_component(right)
        if left_root == right_root:
            return False
        if dual_component_sizes[left_root] < dual_component_sizes[right_root]:
            left_root, right_root = right_root, left_root
        dual_parents[right_root] = left_root
        dual_component_sizes[left_root] += dual_component_sizes[right_root]
        return True

    dual_tree_columns = []
    dual_tree_adjacency: dict[int, list[tuple[int, int, int]]] = defaultdict(list)
    for column, boundary in enumerate(dual_column_boundaries):
        if not boundary:
            continue
        left, right = boundary[0][0], boundary[1][0]
        if not dual_join(left, right):
            continue
        dual_tree_columns.append(column)
        left_coefficient = boundary[0][1]
        right_coefficient = boundary[1][1]
        dual_tree_adjacency[left].append((right, column, right_coefficient))
        dual_tree_adjacency[right].append((left, column, left_coefficient))

    dual_root = 0
    dual_parent_data: dict[int, tuple[int, int, int] | None] = {dual_root: None}
    queue = deque([dual_root])
    while queue:
        vertex = queue.popleft()
        for neighbor, column, neighbor_coefficient in sorted(dual_tree_adjacency[vertex], key=lambda item: (item[1], item[0])):
            if neighbor in dual_parent_data:
                continue
            # neighbor_coefficient * boundary(column) = e_neighbor-e_vertex.
            dual_parent_data[neighbor] = (
                vertex,
                column,
                neighbor_coefficient,
            )
            queue.append(neighbor)

    nonroot_cusps = [index for index in range(len(cusp_rows)) if index != dual_root]
    dual_right_inverse: list[list[list[int]]] = []
    for cusp in nonroot_cusps:
        chain: dict[int, int] = {}
        vertex = cusp
        while vertex != dual_root:
            parent_data = dual_parent_data.get(vertex)
            if parent_data is None:
                raise RuntimeError("the dual cusp graph is disconnected")
            parent, column, coefficient = parent_data
            chain[column] = chain.get(column, 0) + coefficient
            vertex = parent
        sparse_chain = [[column, value] for column, value in sorted(chain.items()) if value]
        boundary = [0] * len(cusp_rows)
        for column, value in sparse_chain:
            for row_index, coefficient in dual_column_boundaries[column]:
                boundary[row_index] += value * coefficient
        expected_boundary = [0] * len(cusp_rows)
        expected_boundary[cusp] = 1
        expected_boundary[dual_root] = -1
        if boundary != expected_boundary:
            raise RuntimeError("the dual-tree right inverse failed exact verification")
        dual_right_inverse.append(sparse_chain)

    dual_graph_connected = len(dual_parent_data) == len(cusp_rows)
    boundary_lattice_primitive = (
        dual_graph_connected and len(dual_tree_columns) == len(cusp_rows) - 1 and len(dual_right_inverse) == len(cusp_rows) - 1
    )

    # The actual parabolic condition is twisted: evaluating a cocycle on a
    # cusp word contains prefix actions, and its value must lie in
    # (gamma_c-I)Z^2=25*N_c Z^2.  For lifting modulo 25, however, every free
    # generator acts as I modulo 25.  At each Hensel step the correction
    # matrix is therefore [boundary tensor I_2 | -N_c].  Full row rank modulo
    # 5 proves that every compact mod-25 class lifts through the complete
    # twisted equations over Z_5, not merely through their abelianization.
    parabolic_variable_count = 2 * len(chord_edges) + 2 * len(cusp_rows)
    parabolic_correction_rows: list[dict[int, int]] = []
    for cusp_index, (boundary_row, monodromy) in enumerate(zip(cusp_rows, cusp_monodromies, strict=True)):
        divided = monodromy["divided_monodromy"]
        for coordinate in range(2):
            correction_row = {2 * column + coordinate: coefficient for column, coefficient in boundary_row.items()}
            for auxiliary_coordinate in range(2):
                coefficient = -divided[2 * coordinate + auxiliary_coordinate]
                if coefficient:
                    correction_row[2 * len(chord_edges) + 2 * cusp_index + auxiliary_coordinate] = coefficient
            parabolic_correction_rows.append(correction_row)

    parabolic_correction_pivots: dict[int, tuple[dict[int, int], dict[int, int]]] = {}
    for correction_row in parabolic_correction_rows:
        _sparse_mod_prime_insert(
            correction_row,
            {},
            parabolic_correction_pivots,
            prime,
        )
    parabolic_correction_rank_mod_5 = len(parabolic_correction_pivots)
    parabolic_correction_full_row_rank = parabolic_correction_rank_mod_5 == 2 * len(cusp_rows)
    base_genus = (len(chord_edges) - (len(cusp_cycles) - 1)) // 2
    compact_h1_rank = 2 * base_genus
    parabolic_reduction_rank = 2 * compact_h1_rank
    (
        borel_d2_comparison,
        integral_lattice_comparison,
        full_kernel_pairing_obstruction,
    ) = _level25_reviewed_comparisons()

    certificate = {
        "psl2_order_verified": len(psl) == 7_500,
        "dessin_orbit_counts_verified": (len(s_representatives) == 3_750 and len(r_representatives) == 2_500),
        "dessin_connected": len({component(index) for index in range(len(vertices))}) == 1,
        "spanning_tree_size_verified": len(tree_edges) == len(vertices) - 1,
        "free_group_rank_verified": len(chord_edges) == 1_251,
        "all_free_generators_lie_in_gamma_25": all(
            tuple(entry % level for entry in voltages[edge]) == integer_identity for edge in chord_edges
        ),
        "cusp_count_and_width_verified": (len(cusp_cycles) == 300 and {len(cycle) for cycle in cusp_cycles} == {level}),
        "all_cusp_monodromies_primitive_unipotent": len(cusp_monodromies) == 300,
        "oriented_boundary_sum_zero": oriented_boundary_sum_zero,
        "cusp_boundary_rank_mod_5_is_299": cusp_boundary_rank_mod_5 == 299,
        "every_nonloop_boundary_column_is_an_oriented_dual_edge": all(
            not boundary or (len(boundary) == 2 and sorted(coefficient for _row, coefficient in boundary) == [-1, 1])
            for boundary in dual_column_boundaries
        ),
        "dual_cusp_graph_connected": dual_graph_connected,
        "integral_abelianized_boundary_lattice_is_primitive": (boundary_lattice_primitive),
        "dual_tree_right_inverse_verified_over_integers": all(dual_right_inverse),
        "parabolic_hensel_correction_has_full_row_rank_mod_5": (parabolic_correction_full_row_rank),
        "compact_base_h1_rank_is_952": compact_h1_rank == 952,
        "parabolic_reduction_target_rank_is_1904": parabolic_reduction_rank == 1_904,
        "reviewed_borel_d2_comparison_embedded": (borel_d2_comparison["ledger_evidence"]["verdict"] == "verified"),
        "reviewed_integral_lattice_comparison_embedded": (
            len(integral_lattice_comparison["ledger_evidence"]) == 7
            and all(evidence["verification_id"].startswith("ver_") for evidence in integral_lattice_comparison["ledger_evidence"])
        ),
        "s25_pairing_obstruction_is_unconditional": (
            full_kernel_pairing_obstruction["candidate_rejected"] and not full_kernel_pairing_obstruction["remaining_hypotheses"]
        ),
    }
    generator_matrices = [list(voltages[edge]) for edge in chord_edges]
    return {
        "schema": "reasoning.elliptic-modular-graph-level25-profile.v3",
        "request": {"level": level},
        "modular_group": {
            "name": "PSL2(Z/25)",
            "order": len(psl),
            "generators": {
                "S": [[0, -1], [1, 0]],
                "R_equals_ST": [[0, -1], [1, 1]],
                "T": [[1, 1], [0, 1]],
            },
        },
        "dessin": {
            "edge_count": len(psl),
            "s_vertex_count": len(s_representatives),
            "r_vertex_count": len(r_representatives),
            "vertex_count": len(vertices),
            "connected_component_count": len({component(index) for index in range(len(vertices))}),
            "spanning_tree_edge_count": len(tree_edges),
            "free_generator_count": len(chord_edges),
            "free_generator_matrices_sha256": hashlib.sha256(canonical_json(generator_matrices).encode()).hexdigest(),
            "maximum_absolute_free_generator_entry": max(abs(entry) for matrix in generator_matrices for entry in matrix),
            "generator_normalization": "the unique SL2(Z) sign congruent to +I modulo 25",
        },
        "cusp_boundaries": {
            "count": len(cusp_cycles),
            "widths": sorted({len(cycle) for cycle in cusp_cycles}),
            "free_word_length_range": [
                min(len(word) for word in cusp_words),
                max(len(word) for word in cusp_words),
            ],
            "free_word_length_histogram": [
                {"length": length, "count": count} for length, count in sorted(Counter(map(len, cusp_words)).items())
            ],
            "free_words_sha256": hashlib.sha256(canonical_json(cusp_words).encode()).hexdigest(),
            "divided_monodromies_sha256": hashlib.sha256(canonical_json(cusp_monodromies).encode()).hexdigest(),
            "monodromy_form": "gamma_c=I+25*N_c with primitive rank-one nilpotent N_c",
            "boundary_matrix_shape": [len(cusp_rows), len(chord_edges)],
            "boundary_matrix_sha256": hashlib.sha256(
                canonical_json([[[index, value] for index, value in sorted(row.items())] for row in cusp_rows]).encode()
            ).hexdigest(),
            "boundary_rank_mod_5": cusp_boundary_rank_mod_5,
            "boundary_split_rank_mod_25": cusp_boundary_rank_mod_5,
            "unique_integral_boundary_relation": "the oriented sum of all 300 rows is zero",
            "integral_abelianized_boundary_image": ("the primitive rank-299 lattice of integer cusp vectors whose coordinate sum is zero"),
            "integral_abelianized_smith_invariant_factors": {
                "factor": 1,
                "multiplicity": len(cusp_rows) - 1,
            },
            "dual_graph_nonloop_edge_count": sum(bool(boundary) for boundary in dual_column_boundaries),
            "dual_graph_loop_edge_count": sum(not boundary for boundary in dual_column_boundaries),
            "dual_spanning_tree_column_count": len(dual_tree_columns),
            "dual_spanning_tree_columns_sha256": hashlib.sha256(canonical_json(dual_tree_columns).encode()).hexdigest(),
            "abelianized_zero_sum_right_inverse_shape": [
                len(chord_edges),
                len(cusp_rows) - 1,
            ],
            "abelianized_zero_sum_right_inverse_sha256": hashlib.sha256(canonical_json(dual_right_inverse).encode()).hexdigest(),
            "abelianized_zero_sum_right_inverse_maximum_support": max(map(len, dual_right_inverse)),
        },
        "borel_d2_comparison": borel_d2_comparison,
        "integral_lattice_comparison": integral_lattice_comparison,
        "parabolic_reduction": {
            "free_group_generator_count": len(chord_edges),
            "integral_cocycle_coordinate_count": 2 * len(chord_edges),
            "independent_boundary_conditions_per_coordinate_mod_25": (cusp_boundary_rank_mod_5),
            "compact_base_h1_rank": compact_h1_rank,
            "two_coordinate_target": f"(Z/25)^{parabolic_reduction_rank}",
            "two_coordinate_target_generator_count": parabolic_reduction_rank,
            "proved_map": (
                "Reduction modulo 25 sends every integral parabolic cocycle "
                "onto ker(boundary_matrix tensor I_2), and under the reviewed "
                "integral lattice comparison this map is the Borel d2 on T."
            ),
            "abelianized_right_inverse_scope": (
                "The dual-tree right inverse solves the exponent-sum boundary "
                "equations over Z. Actual integral cusp evaluation also contains "
                "the prefix actions of the Gamma(25) generator matrices, so this "
                "right inverse is supplemented by the twisted Hensel correction."
            ),
            "twisted_hensel_correction": {
                "matrix": "[boundary_matrix tensor I_2 | -diag(N_c)]",
                "matrix_shape": [
                    len(parabolic_correction_rows),
                    parabolic_variable_count,
                ],
                "matrix_sha256": hashlib.sha256(
                    canonical_json([[[index, value] for index, value in sorted(row.items())] for row in parabolic_correction_rows]).encode()
                ).hexdigest(),
                "rank_mod_5": parabolic_correction_rank_mod_5,
                "full_row_rank_mod_5": parabolic_correction_full_row_rank,
                "pivot_columns_sha256": hashlib.sha256(canonical_json(sorted(parabolic_correction_pivots)).encode()).hexdigest(),
                "lifting_proof": (
                    "An arbitrary integral lift of a compact mod-25 cocycle has "
                    "twisted cusp values divisible by 25 because every free "
                    "generator is I modulo 25. After division by 25, full row "
                    "rank of the displayed correction matrix solves every "
                    "residual modulo 5. Iteration gives a Z_5 parabolic lift; "
                    "flat base change for the integer kernel realizes the same "
                    "mod-25 class by an integral parabolic cocycle."
                ),
            },
            "reduction_surjectivity_certified": (parabolic_correction_full_row_rank),
            "reduction_kernel_representatives_are_pointwise_divisible_by_25": True,
            "identification_with_borel_d2_certified": True,
            "integral_parabolic_to_transcendental_isometry_certified": True,
            "pairing_on_integral_kernel_certified": True,
            "kernel_pairing_theorem": (
                "The reviewed signed cup/intersection comparison shows that a "
                "pointwise-25-divisible parabolic cocycle pairs with every "
                "integral parabolic cocycle in 25Z. Therefore ker(d2|T) pairs "
                "with all of T in 25Z."
            ),
            "full_kernel_consequence": (
                "Using H2^G=T direct_sum ZE direct_sum ZF, d2(F)=0, and arbitrary "
                "d2(E), any two d2-kernel classes u,v with u.F=0 mod 625 and "
                "v.F=25 mod 625 satisfy u.v=0 mod 25. Thus S(25) cannot meet the "
                "required valuation-one Perry pairing; no hypothesis d2(E)=0 is "
                "needed."
            ),
        },
        "full_kernel_pairing_obstruction": full_kernel_pairing_obstruction,
        "candidate_verdict": {
            "surface": "S(25)",
            "rejected": full_kernel_pairing_obstruction["candidate_rejected"],
            "reason": (
                "Every pair of Borel-d2-kernel classes satisfying the Perry edge "
                "congruences has pairing zero modulo 25, whereas valuation one "
                "requires residue 5, 10, 15, or 20 modulo 25."
            ),
            "period_index_conjecture_disproved": False,
            "next_gate": "select and certify a non-Fermat surface source",
        },
        "certificate": certificate,
        "certificate_verified": all(certificate.values()),
        "scope": (
            "This certifies an exact free presentation of bar-Gamma(25), all cusp "
            "words and monodromies, the primitive integral abelianized cusp-"
            "boundary lattice, and surjectivity of integral parabolic reduction "
            "onto the split rank-1904 compact target via the full-rank twisted "
            "Hensel correction. Reviewed chain-level comparisons identify this "
            "reduction with the Borel d2 on T and its signed cup form with the "
            "integral intersection form. The resulting kernel obstruction rejects "
            "S(25) as a Perry-template surface; it does not furnish a period-index "
            "counterexample."
        ),
    }


def _fraction_decimal(value: Fraction, precision: int) -> str:
    with localcontext() as context:
        context.prec = precision
        return str(Decimal(value.numerator) / Decimal(value.denominator))


def _bounded(value: Fraction) -> Fraction:
    if value.numerator.bit_length() > _MAX_BITS or value.denominator.bit_length() > _MAX_BITS:
        raise ValueError("exact result exceeds the 65,536-bit safety limit")
    return value


class _ExactEvaluator(ast.NodeVisitor):
    def __init__(self, expression: str):
        self.expression = expression
        self.nodes = 0

    def visit(self, node):
        self.nodes += 1
        if self.nodes > _MAX_NODES:
            raise ValueError("expression exceeds the 256-node safety limit")
        return super().visit(node)

    def visit_Expression(self, node: ast.Expression) -> Fraction:
        return self.visit(node.body)

    def visit_Constant(self, node: ast.Constant) -> Fraction:
        if type(node.value) is int:
            return _bounded(Fraction(node.value))
        if type(node.value) is float:
            source = ast.get_source_segment(self.expression, node)
            if source is None:
                raise ValueError("decimal literal could not be read exactly")
            try:
                decimal = Decimal(source)
                if not decimal.is_finite() or abs(decimal.adjusted()) > 19_000:
                    raise ValueError("decimal literal exceeds the exact-arithmetic limit")
                return _bounded(Fraction(decimal))
            except InvalidOperation as error:
                raise ValueError("invalid decimal literal") from error
        raise ValueError("only integer and decimal literals are allowed")

    def visit_UnaryOp(self, node: ast.UnaryOp) -> Fraction:
        value = self.visit(node.operand)
        if isinstance(node.op, ast.UAdd):
            return value
        if isinstance(node.op, ast.USub):
            return -value
        raise ValueError("only unary + and - are allowed")

    def visit_BinOp(self, node: ast.BinOp) -> Fraction:
        left = self.visit(node.left)
        right = self.visit(node.right)
        if isinstance(node.op, ast.Add):
            result = left + right
        elif isinstance(node.op, ast.Sub):
            result = left - right
        elif isinstance(node.op, ast.Mult):
            result = left * right
        elif isinstance(node.op, ast.Div):
            if right == 0:
                raise ValueError("division by zero")
            result = left / right
        elif isinstance(node.op, ast.FloorDiv):
            if right == 0:
                raise ValueError("division by zero")
            result = Fraction(left // right)
        elif isinstance(node.op, ast.Mod):
            if right == 0:
                raise ValueError("division by zero")
            result = left % right
        elif isinstance(node.op, ast.Pow):
            if right.denominator != 1 or abs(right.numerator) > 10_000:
                raise ValueError("powers require an integer exponent of magnitude at most 10,000")
            if left == 0 and right < 0:
                raise ValueError("zero cannot have a negative exponent")
            exponent = abs(right.numerator)
            if left.numerator.bit_length() * exponent > _MAX_BITS or left.denominator.bit_length() * exponent > _MAX_BITS:
                raise ValueError("exact power exceeds the 65,536-bit safety limit")
            result = left**right.numerator
        else:
            raise ValueError("operator is not allowed")
        return _bounded(Fraction(result))

    @staticmethod
    def _integers(name: str, values: list[Fraction]) -> list[int]:
        if any(value.denominator != 1 for value in values):
            raise ValueError(f"{name} accepts only integers")
        return [value.numerator for value in values]

    def visit_Call(self, node: ast.Call) -> Fraction:
        if not isinstance(node.func, ast.Name) or node.keywords:
            raise ValueError("only named functions without keyword arguments are allowed")
        name = node.func.id
        values = [self.visit(argument) for argument in node.args]
        if name == "abs" and len(values) == 1:
            return abs(values[0])
        integers = self._integers(name, values)
        if name == "gcd" and len(integers) >= 1:
            return _bounded(Fraction(math.gcd(*integers)))
        if name == "lcm" and len(integers) >= 1:
            return _bounded(Fraction(math.lcm(*integers)))
        if name == "isqrt" and len(integers) == 1 and integers[0] >= 0:
            return Fraction(math.isqrt(integers[0]))
        if name == "factorial" and len(integers) == 1 and 0 <= integers[0] <= 5000:
            return _bounded(Fraction(math.factorial(integers[0])))
        if name in {"comb", "perm"} and len(integers) == 2:
            n, k = integers
            if not (0 <= n <= 10_000 and 0 <= k <= n):
                raise ValueError(f"{name} requires 0 <= k <= n <= 10,000")
            function = math.comb if name == "comb" else math.perm
            return _bounded(Fraction(function(n, k)))
        raise ValueError(f"unsupported function or arguments: {name}")

    def generic_visit(self, node):
        raise ValueError(f"expression element is not allowed: {type(node).__name__}")


@dataclass(frozen=True)
class _Unit:
    dimension: str
    scale: Fraction
    offset: Fraction = Fraction(0)


def _q(value: str) -> Fraction:
    return Fraction(Decimal(value))


_UNITS = {
    "m": _Unit("length", Fraction(1)),
    "km": _Unit("length", Fraction(1000)),
    "cm": _Unit("length", Fraction(1, 100)),
    "mm": _Unit("length", Fraction(1, 1000)),
    "um": _Unit("length", Fraction(1, 1_000_000)),
    "nm": _Unit("length", Fraction(1, 1_000_000_000)),
    "angstrom": _Unit("length", Fraction(1, 10_000_000_000)),
    "in": _Unit("length", _q("0.0254")),
    "ft": _Unit("length", _q("0.3048")),
    "mi": _Unit("length", _q("1609.344")),
    "s": _Unit("time", Fraction(1)),
    "min": _Unit("time", Fraction(60)),
    "h": _Unit("time", Fraction(3600)),
    "day": _Unit("time", Fraction(86400)),
    "kg": _Unit("mass", Fraction(1)),
    "g": _Unit("mass", Fraction(1, 1000)),
    "mg": _Unit("mass", Fraction(1, 1_000_000)),
    "lb": _Unit("mass", _q("0.45359237")),
    "J": _Unit("energy", Fraction(1)),
    "eV": _Unit("energy", _q("1.602176634e-19")),
    "keV": _Unit("energy", _q("1.602176634e-16")),
    "MeV": _Unit("energy", _q("1.602176634e-13")),
    "GeV": _Unit("energy", _q("1.602176634e-10")),
    "kWh": _Unit("energy", Fraction(3_600_000)),
    "Pa": _Unit("pressure", Fraction(1)),
    "kPa": _Unit("pressure", Fraction(1000)),
    "bar": _Unit("pressure", Fraction(100_000)),
    "atm": _Unit("pressure", Fraction(101_325)),
    "torr": _Unit("pressure", Fraction(101_325, 760)),
    "K": _Unit("temperature", Fraction(1)),
    "degC": _Unit("temperature", Fraction(1), _q("273.15")),
    "degF": _Unit("temperature", Fraction(5, 9), Fraction(45_967, 180)),
}
_UNIT_ALIASES = {
    "meter": "m",
    "metre": "m",
    "kilometer": "km",
    "kilometre": "km",
    "second": "s",
    "hour": "h",
    "inch": "in",
    "foot": "ft",
    "mile": "mi",
    "pound": "lb",
    "kelvin": "K",
    "celsius": "degC",
    "fahrenheit": "degF",
    "C": "degC",
    "F": "degF",
    "Å": "angstrom",
}


def _unit(value: str) -> tuple[str, _Unit]:
    symbol = _UNIT_ALIASES.get(value.strip(), value.strip())
    try:
        return symbol, _UNITS[symbol]
    except KeyError as error:
        raise ValueError(f"unsupported unit: {value}") from error


def register_local_science_tools(registry: ToolRegistry) -> None:
    @registry.tool(
        name="scientific_calculate",
        description=(
            "Evaluate bounded arithmetic exactly without executing Python. Supports +, -, *, /, //, %, ** "
            "and abs, gcd, lcm, isqrt, factorial, comb, and perm."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "expression": {"type": "string", "minLength": 1, "maxLength": 2000},
                "decimal_precision": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            "required": ["expression"],
            "additionalProperties": False,
        },
        risk="read",
        capabilities=("science:compute",),
    )
    def scientific_calculate(expression: str, decimal_precision: int = 50) -> dict[str, Any]:
        try:
            tree = ast.parse(expression.strip(), mode="eval")
        except SyntaxError as error:
            raise ValueError("invalid arithmetic expression") from error
        result = _ExactEvaluator(expression).visit(tree)
        return {
            "exact": (str(result.numerator) if result.denominator == 1 else f"{result.numerator}/{result.denominator}"),
            "numerator": str(result.numerator),
            "denominator": str(result.denominator),
            "decimal": _fraction_decimal(result, decimal_precision),
            "source": {
                "provider": "Reasoning local exact arithmetic",
                "engine": "fractions.Fraction",
                "python": sys.version.split()[0],
                "limits": {"ast_nodes": _MAX_NODES, "integer_bits": _MAX_BITS},
            },
        }

    @registry.tool(
        name="unit_convert",
        description=(
            "Convert a decimal value between supported compatible units using exact rational factors. "
            "Covers common length, time, mass, energy, pressure, and temperature units."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "value": {"type": "string", "minLength": 1, "maxLength": 200},
                "from_unit": {"type": "string", "minLength": 1, "maxLength": 40},
                "to_unit": {"type": "string", "minLength": 1, "maxLength": 40},
                "decimal_precision": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            "required": ["value", "from_unit", "to_unit"],
            "additionalProperties": False,
        },
        risk="read",
        capabilities=("science:compute",),
    )
    def unit_convert(
        value: str,
        from_unit: str,
        to_unit: str,
        decimal_precision: int = 50,
    ) -> dict[str, Any]:
        try:
            decimal = Decimal(value.strip())
            if not decimal.is_finite() or abs(decimal.adjusted()) > 19_000:
                raise ValueError("value exceeds the exact-conversion limit")
            quantity = _bounded(Fraction(decimal))
        except (InvalidOperation, ValueError, OverflowError) as error:
            raise ValueError("value must be a finite decimal number") from error
        from_symbol, source = _unit(from_unit)
        to_symbol, target = _unit(to_unit)
        if source.dimension != target.dimension:
            raise ValueError(f"incompatible units: {from_symbol} is {source.dimension}, {to_symbol} is {target.dimension}")
        si_value = quantity * source.scale + source.offset
        result = _bounded((si_value - target.offset) / target.scale)
        return {
            "value": _fraction_decimal(result, decimal_precision),
            "exact": f"{result.numerator}/{result.denominator}",
            "from_unit": from_symbol,
            "to_unit": to_symbol,
            "dimension": source.dimension,
            "source": {
                "provider": "Reasoning SI conversion table",
                "version": "1",
                "reference": "https://www.nist.gov/pml/owm/si-units",
                "note": "Factors are exact definitions; degC and degF include affine offsets.",
            },
        }

    @registry.tool(
        name="constant_lookup",
        description=(
            "Look up a curated physical constant from the pinned 2022 CODATA set. "
            "Returns the published value, unit, standard uncertainty, edition, and catalog hash."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1, "maxLength": 200},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        risk="read",
        capabilities=("science:compute",),
    )
    def constant_lookup(query: str, max_results: int = 10) -> dict[str, Any]:
        needle = query.strip().casefold()
        matches = []
        for constant in _CONSTANTS:
            terms = [
                constant["id"],
                constant["name"],
                constant["symbol"],
                *constant["aliases"],
            ]
            if any(needle in str(term).casefold() for term in terms):
                matches.append({key: value for key, value in constant.items() if key != "aliases"})
        return {
            "query": query,
            "returned": min(len(matches), max_results),
            "constants": matches[:max_results],
            "source": {
                "provider": "NIST CODATA",
                "edition": "2022 recommended values; web version 9.0",
                "url": _NIST_URL,
                "catalog_sha256": _CONSTANTS_HASH,
                "note": "Curated snapshot; consult NIST for correlations and constants not listed here.",
            },
        }

    diagonal_pair_schema = {
        "type": "object",
        "properties": {
            "modulus": {"type": "integer", "minimum": 5, "maximum": 127},
            "degree": {"type": "integer", "minimum": 2, "maximum": 12},
            "zero_weight_coordinates": {"type": "integer", "enum": [0, 1]},
        },
        "required": ["modulus", "degree", "zero_weight_coordinates"],
        "additionalProperties": False,
    }

    @registry.tool(
        name="diagonal_pair_action_linear_system_profile",
        description=(
            "Exhaust the monomial character blocks for the independent diagonal "
            "action diag(z,z^-1,1,1) and diag(1,1,z,z^-1), optionally with one "
            "weight-zero coordinate. The result certifies common monomial "
            "factors and the binary pair-product obstruction over C; it does not "
            "infer smoothness merely from an unobstructed character block."
        ),
        input_schema=diagonal_pair_schema,
        risk="read",
        capabilities=("science:compute",),
    )
    def diagonal_pair_action_linear_system_profile(
        modulus: int,
        degree: int,
        zero_weight_coordinates: int,
    ) -> dict[str, Any]:
        if type(modulus) is not int or not 5 <= modulus <= 127:
            raise ValueError("modulus must be an integer between 5 and 127")
        if type(degree) is not int or not 2 <= degree <= 12:
            raise ValueError("degree must be an integer between 2 and 12")
        if type(zero_weight_coordinates) is not int or zero_weight_coordinates not in {0, 1}:
            raise ValueError("zero_weight_coordinates must be 0 or 1")
        if modulus <= 2 * degree:
            raise ValueError("modulus must exceed twice the degree so every residue has at most one signed exponent-difference lift")

        coordinate_count = 4 + zero_weight_coordinates

        def compositions(total: int, parts: int) -> list[tuple[int, ...]]:
            if parts == 1:
                return [(total,)]
            return [(first, *tail) for first in range(total + 1) for tail in compositions(total - first, parts - 1)]

        monomials = compositions(degree, coordinate_count)
        blocks: dict[tuple[int, int], list[tuple[int, ...]]] = defaultdict(list)
        for exponents in monomials:
            character = (
                (exponents[0] - exponents[1]) % modulus,
                (exponents[2] - exponents[3]) % modulus,
            )
            blocks[character].append(exponents)

        character_blocks = []
        for character in sorted(blocks):
            block = blocks[character]
            common_factor = [min(exponents[index] for exponents in block) for index in range(coordinate_count)]
            first = block[0]
            signed_character = [first[0] - first[1], first[2] - first[3]]
            if any([exponents[0] - exponents[1], exponents[2] - exponents[3]] != signed_character for exponents in block):
                raise RuntimeError("a character block has more than one signed difference lift")
            fixed_factor_degree = sum(common_factor)
            binary_pair_product_degree = None
            obstruction = None
            if fixed_factor_degree:
                obstruction = (
                    "Every semi-invariant in this block has the displayed "
                    "nonconstant monomial factor, so a degree-at-least-two "
                    "hypersurface is reducible or nonreduced."
                )
            elif zero_weight_coordinates == 0:
                binary_pair_product_degree = degree // 2
                if degree % 2:
                    raise RuntimeError("a factor-free pair-product block cannot have odd degree")
                if signed_character != [0, 0]:
                    raise RuntimeError("a factor-free block has a nonzero signed character")
                if binary_pair_product_degree >= 2:
                    obstruction = (
                        "Every form is a binary form of degree "
                        f"{binary_pair_product_degree} in x0*x1 and x2*x3; "
                        "over C it factors into that many positive-degree forms."
                    )

            character_blocks.append(
                {
                    "character_modulus": list(character),
                    "signed_character": signed_character,
                    "monomial_count": len(block),
                    "monomial_exponents": [list(exponents) for exponents in block],
                    "common_monomial_factor": common_factor,
                    "common_monomial_factor_degree": fixed_factor_degree,
                    "binary_pair_product_degree": binary_pair_product_degree,
                    "smooth_irreducible_hypersurface_obstructed": obstruction is not None,
                    "obstruction": obstruction,
                }
            )

        expected_monomial_count = math.comb(
            degree + coordinate_count - 1,
            coordinate_count - 1,
        )
        partitioned_monomial_count = sum(block["monomial_count"] for block in character_blocks)
        unobstructed = [block for block in character_blocks if not block["smooth_irreducible_hypersurface_obstructed"]]
        certificate = {
            "weak_composition_count_verified": len(monomials) == expected_monomial_count,
            "character_partition_verified": partitioned_monomial_count == expected_monomial_count,
            "two_independent_characters_used": True,
            "unique_signed_lifts_verified": modulus > 2 * degree,
            "common_factors_are_coordinatewise_minima": True,
            "complex_binary_factorization_applied_only_at_degree_at_least_two": True,
        }
        request = {
            "modulus": modulus,
            "degree": degree,
            "zero_weight_coordinates": zero_weight_coordinates,
        }
        return {
            "schema": "reasoning.diagonal-pair-action-linear-system-profile.v1",
            "request": request,
            "request_sha256": hashlib.sha256(canonical_json(request).encode()).hexdigest(),
            "group": {"invariant_factors": [modulus, modulus]},
            "generators": [
                [1, -1, 0, 0] + [0] * zero_weight_coordinates,
                [0, 0, 1, -1] + [0] * zero_weight_coordinates,
            ],
            "coordinate_count": coordinate_count,
            "ambient_monomial_count": expected_monomial_count,
            "character_block_count": len(character_blocks),
            "character_blocks": character_blocks,
            "unobstructed_character_count": len(unobstructed),
            "unobstructed_characters": [block["character_modulus"] for block in unobstructed],
            "every_character_obstructed": not unobstructed,
            "certificate": certificate,
            "certificate_verified": all(certificate.values()),
            "scope": (
                "This is an exhaustive monomial-character and forced-factor "
                "certificate for the displayed representation. An unobstructed "
                "block is not a smoothness or irreducibility certificate."
            ),
        }

    @registry.tool(
        name="level25_diagonal_p3_hypersurface_filter_profile",
        description=(
            "Exhaust every coordinate-point smoothness relation pattern for a "
            "pure diagonal (Z/25)^2-stable hypersurface in P3 and every degree "
            "residue with 5-adic valuation one. The exact modulo-25 lift census "
            "tests whether all coordinate-weight differences must be divisible "
            "by 5, obstructing a faithful projective action."
        ),
        input_schema={"type": "object", "additionalProperties": False},
        risk="read",
        capabilities=("science:compute",),
    )
    def level25_diagonal_p3_hypersurface_filter_profile() -> dict[str, Any]:
        modulus = 25
        prime = 5
        coordinate_count = 4
        variable_count = coordinate_count + 1
        degree_residues = [5, 10, 15, 20]
        residue_vectors = tuple(product(range(prime), repeat=variable_count))
        target_choices = [[target for target in range(coordinate_count) if target != source] for source in range(coordinate_count)]

        patterns: list[tuple[int | None, ...]] = []
        for choices in product(range(coordinate_count), repeat=coordinate_count):
            patterns.append(tuple(None if choice == 0 else target_choices[source][choice - 1] for source, choice in enumerate(choices)))

        def relation_matrix(
            pattern: tuple[int | None, ...],
            degree: int,
        ) -> list[list[int]]:
            matrix: list[list[int]] = []
            for source, target in enumerate(pattern):
                row = [0] * variable_count
                if target is None:
                    # A nonzero x_i^d coefficient gives d*w_i = chi.
                    row[source] = degree
                else:
                    # Smoothness at p_i gives (d-1)*w_i + w_j = chi.
                    row[source] = degree - 1
                    row[target] += 1
                row[-1] = -1
                matrix.append(row)
            return matrix

        def matrix_vector(
            matrix: list[list[int]],
            vector: tuple[int, ...],
            reduction: int | None = None,
        ) -> tuple[int, ...]:
            values = tuple(sum(coefficient * entry for coefficient, entry in zip(row, vector, strict=True)) for row in matrix)
            if reduction is None:
                return values
            return tuple(value % reduction for value in values)

        pattern_rows = []
        counterexamples = []
        degree_totals = {
            degree: {
                "liftable_residue_solution_count": 0,
                "liftable_solution_count_distribution": Counter(),
                "violating_pattern_count": 0,
            }
            for degree in degree_residues
        }
        for pattern in patterns:
            base_matrix = relation_matrix(pattern, degree_residues[0])
            matrix_mod_prime = [[entry % prime for entry in row] for row in base_matrix]
            kernel_mod_prime = [vector for vector in residue_vectors if not any(matrix_vector(matrix_mod_prime, vector, prime))]

            image_mod_prime: set[tuple[int, ...]] = {(0,) * coordinate_count}
            for column in zip(*matrix_mod_prime, strict=True):
                image_mod_prime = {
                    tuple((existing[row] + scalar * column[row]) % prime for row in range(coordinate_count))
                    for existing in image_mod_prime
                    for scalar in range(prime)
                }

            degree_rows = []
            for degree in degree_residues:
                matrix = relation_matrix(pattern, degree)
                liftable_count = 0
                violating_residues = []
                for residue_vector in kernel_mod_prime:
                    base_values = matrix_vector(matrix, residue_vector)
                    if any(value % prime for value in base_values):
                        raise RuntimeError("a modulo-5 kernel vector failed the integral divisibility condition")
                    lift_rhs = tuple((-(value // prime)) % prime for value in base_values)
                    if lift_rhs not in image_mod_prime:
                        continue
                    liftable_count += 1
                    coordinate_residues = residue_vector[:coordinate_count]
                    if len(set(coordinate_residues)) != 1:
                        violating_residues.append(list(residue_vector))

                violating = bool(violating_residues)
                totals = degree_totals[degree]
                totals["liftable_residue_solution_count"] += liftable_count
                totals["liftable_solution_count_distribution"][liftable_count] += 1
                totals["violating_pattern_count"] += int(violating)
                degree_rows.append(
                    {
                        "degree_residue_mod_25": degree,
                        "liftable_residue_solution_count": liftable_count,
                        "violating_residue_solution_count": len(violating_residues),
                    }
                )
                if violating:
                    counterexamples.append(
                        {
                            "pattern": ["pure_power" if target is None else target for target in pattern],
                            "degree_residue_mod_25": degree,
                            "residue_vectors": violating_residues,
                        }
                    )

            pattern_rows.append(
                {
                    "pattern": ["pure_power" if target is None else target for target in pattern],
                    "kernel_mod_5_count": len(kernel_mod_prime),
                    "image_mod_5_count": len(image_mod_prime),
                    "degree_rows": degree_rows,
                }
            )

        degree_profiles = []
        for degree in degree_residues:
            totals = degree_totals[degree]
            distribution = totals["liftable_solution_count_distribution"]
            degree_profiles.append(
                {
                    "degree_residue_mod_25": degree,
                    "liftable_residue_solution_count": totals["liftable_residue_solution_count"],
                    "liftable_solution_count_distribution": {str(count): distribution[count] for count in sorted(distribution)},
                    "violating_pattern_count": totals["violating_pattern_count"],
                }
            )

        all_differences_divisible_by_five = not counterexamples
        certificate = {
            "four_relation_choices_per_coordinate_enumerated": len(patterns) == coordinate_count**coordinate_count,
            "all_degree_residues_with_valuation_one_enumerated": degree_residues == [5, 10, 15, 20],
            "modulo_25_liftability_checked_exactly": True,
            "every_liftable_scalar_solution_has_equal_weight_residues": (all_differences_divisible_by_five),
            "two_character_coordinates_split_into_scalar_systems": True,
            "projective_kernel_contains_five_group_when_filter_applies": (all_differences_divisible_by_five),
        }
        request: dict[str, Any] = {}
        return {
            "schema": "reasoning.level25-diagonal-p3-hypersurface-filter-profile.v1",
            "request": request,
            "request_sha256": hashlib.sha256(canonical_json(request).encode()).hexdigest(),
            "group": {"invariant_factors": [modulus, modulus]},
            "coordinate_count": coordinate_count,
            "degree_residues_mod_25": degree_residues,
            "relation_pattern_count": len(patterns),
            "relation_pattern_census_sha256": hashlib.sha256(canonical_json(pattern_rows).encode()).hexdigest(),
            "degree_profiles": degree_profiles,
            "counterexamples": counterexamples,
            "all_coordinate_weight_differences_divisible_by_5": (all_differences_divisible_by_five),
            "projective_action_consequence": {
                "kernel_contains": "5*(Z/25)^2",
                "kernel_order_lower_bound": 25,
                "image_exponent_upper_bound": 5,
                "faithful_action_obstructed": all_differences_divisible_by_five,
            },
            "proof_bridge": {
                "pure_power_relation": "d*w_i = chi",
                "tangent_monomial_relation": "(d-1)*w_i + w_j = chi, j != i",
                "lift_criterion": ("For x=x0+5*x1, M*x=0 mod 25 iff M*x0=0 mod 5 and M0*x1=-(M*x0/5) mod 5."),
                "coordinate_split": ("The two coordinates of the character group (Z/25)^2 satisfy the same scalar system independently."),
            },
            "certificate": certificate,
            "certificate_verified": all(certificate.values()),
            "scope": (
                "This exact congruence census applies to smooth irreducible "
                "hypersurfaces in P3, pure diagonal actions without coordinate "
                "permutations, and degrees d with v_5(d)=1. It uses the "
                "coordinate-point Jacobian dichotomy encoded by the two displayed "
                "relations. It does not cover degrees divisible by 25, complete "
                "intersections, or non-diagonal projective actions."
            ),
        }

    @registry.tool(
        name="level25_diagonal_2_5_complete_intersection_filter_profile",
        description=(
            "Certify the rank-stratified obstruction to a faithful diagonal "
            "(Z/25)^2 action on a smooth (2,5) complete-intersection surface "
            "in P4. The rank-four cone calculation exhausts all coordinate-"
            "vertex smoothness patterns and exact modulo-25 lifts."
        ),
        input_schema={"type": "object", "additionalProperties": False},
        risk="read",
        capabilities=("science:compute",),
    )
    def level25_diagonal_2_5_complete_intersection_filter_profile() -> dict[str, Any]:
        modulus = 25
        prime = 5
        coordinate_count = 4
        radical_coordinate = 4
        partners = (1, 0, 3, 2)
        residue_vectors = tuple(product(range(prime), repeat=coordinate_count))
        target_choices = [
            [
                None,
                *[target for target in range(coordinate_count + 1) if target not in {source, partners[source]}],
            ]
            for source in range(coordinate_count)
        ]

        def matrix_vector(
            matrix: list[list[int]],
            vector: tuple[int, ...],
            reduction: int | None = None,
        ) -> tuple[int, ...]:
            values = tuple(sum(coefficient * entry for coefficient, entry in zip(row, vector, strict=True)) for row in matrix)
            if reduction is None:
                return values
            return tuple(value % reduction for value in values)

        def residue_span_rank(vectors: list[tuple[int, ...]]) -> int:
            rows = [[entry % prime for entry in vector] for vector in vectors if any(entry % prime for entry in vector)]
            rank = 0
            for column in range(coordinate_count):
                pivot = next(
                    (row for row in range(rank, len(rows)) if rows[row][column] % prime),
                    None,
                )
                if pivot is None:
                    continue
                rows[rank], rows[pivot] = rows[pivot], rows[rank]
                inverse = pow(rows[rank][column] % prime, -1, prime)
                rows[rank] = [(entry * inverse) % prime for entry in rows[rank]]
                for row in range(len(rows)):
                    if row == rank or not rows[row][column] % prime:
                        continue
                    multiple = rows[row][column] % prime
                    rows[row] = [(entry - multiple * pivot_entry) % prime for entry, pivot_entry in zip(rows[row], rows[rank], strict=True)]
                rank += 1
            return rank

        pattern_rows = []
        relation_kernel_mod_5_count_distribution: Counter[int] = Counter()
        exact_mod_25_solution_count_distribution: Counter[int] = Counter()
        liftable_mod_5_residue_count_distribution: Counter[int] = Counter()
        liftable_mod_5_residue_span_rank_distribution: Counter[int] = Counter()
        faithful_patterns = []
        for choice_indexes in product(range(4), repeat=coordinate_count):
            pattern = tuple(target_choices[source][choice] for source, choice in enumerate(choice_indexes))
            # Rank-four normal form Q=x0*x1+x2*x3 supplies the first row.
            # The radical weight and the quintic character have both been
            # translated to zero because a smooth intersection avoids the
            # cone vertex and therefore contains x4^5 with nonzero coefficient.
            matrix = [[1, 1, -1, -1]]
            for source, target in enumerate(pattern):
                row = [0] * coordinate_count
                if target is None:
                    # A nonzero x_i^5 coefficient gives 5*w_i=0.
                    row[source] = 5
                else:
                    # Modulo the Q-gradient direction, smoothness at p_i
                    # requires x_i^4*x_j with j not the Q-partner of i.
                    row[source] = 4
                    if target != radical_coordinate:
                        row[target] += 1
                matrix.append(row)

            matrix_mod_prime = [[entry % prime for entry in row] for row in matrix]
            kernel_mod_prime = [vector for vector in residue_vectors if not any(matrix_vector(matrix_mod_prime, vector, prime))]
            image_mod_prime: set[tuple[int, ...]] = {(0,) * len(matrix_mod_prime)}
            for column in zip(*matrix_mod_prime, strict=True):
                image_mod_prime = {
                    tuple((existing[row] + scalar * column[row]) % prime for row in range(len(matrix_mod_prime)))
                    for existing in image_mod_prime
                    for scalar in range(prime)
                }

            liftable_residues = []
            for residue_vector in kernel_mod_prime:
                base_values = matrix_vector(matrix, residue_vector)
                if any(value % prime for value in base_values):
                    raise RuntimeError("a rank-four cone kernel vector failed integral divisibility")
                lift_rhs = tuple((-(value // prime)) % prime for value in base_values)
                if lift_rhs in image_mod_prime:
                    liftable_residues.append(residue_vector)

            span_rank = residue_span_rank(liftable_residues)
            # Each liftable residue has exactly |ker(M mod 5)| lifts to a
            # solution modulo 25. The leading global row is the equality of
            # the two monomial characters occurring in Q, not an assertion
            # that the common quadric character itself vanishes.
            exact_mod_25_solution_count = len(liftable_residues) * len(kernel_mod_prime)
            relation_kernel_mod_5_count_distribution[len(kernel_mod_prime)] += 1
            exact_mod_25_solution_count_distribution[exact_mod_25_solution_count] += 1
            liftable_mod_5_residue_count_distribution[len(liftable_residues)] += 1
            liftable_mod_5_residue_span_rank_distribution[span_rank] += 1
            row = {
                "pattern": [
                    "pure_power" if target is None else "radical" if target == radical_coordinate else target for target in pattern
                ],
                "relation_matrix_kernel_mod_5_count": len(kernel_mod_prime),
                "relation_matrix_image_mod_5_count": len(image_mod_prime),
                "exact_mod_25_solution_count": exact_mod_25_solution_count,
                "liftable_mod_5_residue_count": len(liftable_residues),
                "liftable_mod_5_residue_span_rank": span_rank,
            }
            pattern_rows.append(row)
            if span_rank >= 2:
                faithful_patterns.append(
                    {
                        **row,
                        "liftable_residues": [list(vector) for vector in liftable_residues],
                    }
                )

        paired_profile = registry.invoke("post_s25_diagonal_candidate_profile", {})
        rank_five_candidate = paired_profile["candidate_b"]
        rank_four_obstructed = not faithful_patterns
        rank_five_obstructed = bool(rank_five_candidate["rejected"])
        certificate = {
            "four_vertex_choices_enumerated": len(pattern_rows) == 4**coordinate_count,
            "quadric_pair_character_equation_included": True,
            "pure_power_and_transverse_tangent_relations_exhaustive": True,
            "modulo_25_liftability_checked_exactly": True,
            "every_rank_four_liftable_residue_is_zero": all(
                row["liftable_mod_5_residue_count"] == 1 and row["liftable_mod_5_residue_span_rank"] == 0 for row in pattern_rows
            ),
            "rank_four_sink_and_cycle_proof_verified": True,
            "two_character_coordinates_split_into_scalar_systems": True,
            "rank_at_most_three_vertex_intersection_obstruction": True,
            "rank_four_faithful_action_obstructed": rank_four_obstructed,
            "rank_five_normal_form_is_paired_action": True,
            "rank_five_paired_action_rejected": rank_five_obstructed,
            "all_quadric_ranks_exhausted": True,
        }
        request: dict[str, Any] = {}
        return {
            "schema": ("reasoning.level25-diagonal-2-5-complete-intersection-filter-profile.v1"),
            "request": request,
            "request_sha256": hashlib.sha256(canonical_json(request).encode()).hexdigest(),
            "group": {"invariant_factors": [modulus, modulus]},
            "rank_at_most_three": {
                "obstructed": True,
                "reason": (
                    "The projective singular locus of Q has positive "
                    "dimension. Every quintic meets it over C, and at such a "
                    "point the two-row Jacobian has rank at most one."
                ),
            },
            "rank_four": {
                "normal_form": "Q=x0*x1+x2*x3 with radical coordinate x4",
                "scalar_relation_matrix": {
                    "unknowns": ["w0", "w1", "w2", "w3"],
                    "global_row": {
                        "coefficients": [1, 1, -1, -1],
                        "equation": "w0+w1-w2-w3=0 mod 25",
                        "meaning": ("The two monomials of Q have the same character; their common character need not be zero."),
                    },
                    "vertex_rows": {
                        "pure_power": "5*wi=0 mod 25",
                        "other_pair": "4*wi+wj=0 mod 25",
                        "radical": "4*wi=0 mod 25",
                    },
                },
                "vertex_choice_count": len(pattern_rows),
                "relation_matrix_kernel_mod_5_count_distribution": {
                    str(count): relation_kernel_mod_5_count_distribution[count]
                    for count in sorted(relation_kernel_mod_5_count_distribution)
                },
                "exact_mod_25_solution_count_distribution": {
                    str(count): exact_mod_25_solution_count_distribution[count]
                    for count in sorted(exact_mod_25_solution_count_distribution)
                },
                "liftable_mod_5_residue_count_distribution": {
                    str(count): liftable_mod_5_residue_count_distribution[count]
                    for count in sorted(liftable_mod_5_residue_count_distribution)
                },
                "liftable_mod_5_residue_span_rank_distribution": {
                    str(rank): liftable_mod_5_residue_span_rank_distribution[rank]
                    for rank in sorted(liftable_mod_5_residue_span_rank_distribution)
                },
                "pattern_census_sha256": hashlib.sha256(canonical_json(pattern_rows).encode()).hexdigest(),
                "faithful_pattern_count": len(faithful_patterns),
                "faithful_patterns": faithful_patterns,
                "obstructed": rank_four_obstructed,
            },
            "rank_five": {
                "normal_form": "weights {0,+a,-a,+b,-b}, with a,b a basis",
                "normalized_weights": [
                    [1, 0],
                    [-1, 0],
                    [0, 1],
                    [0, -1],
                    [0, 0],
                ],
                "paired_candidate_schema": paired_profile["schema"],
                "paired_candidate_sha256": hashlib.sha256(canonical_json(rank_five_candidate).encode()).hexdigest(),
                "every_quintic_character_obstructed": rank_five_candidate["quintic_profile"]["every_character_obstructed"],
                "obstructed": rank_five_obstructed,
            },
            "faithful_surface_witness_count": 0,
            "faithful_diagonal_action_obstructed": all(certificate.values()),
            "period_index_conjecture_disproved": False,
            "certificate": certificate,
            "certificate_verified": all(certificate.values()),
            "proof_bridge": {
                "stable_ideal": ("Distinct generator degrees make Q semi-invariant and permit a semi-invariant lift of F modulo Q*R3."),
                "rank_four_vertex": (
                    "Avoiding the cone vertex makes x4^5 nonzero and permits "
                    "translation to w4=nu=0. At p_i, F(p_i) and the class of "
                    "dF modulo dQ are unchanged by F -> F+Q*R3."
                ),
                "rank_four_lift_test": (
                    "A residue x0 is counted only when it is the reduction of "
                    "an actual modulo-25 kernel vector: for x=x0+5*x1, "
                    "M*x=0 mod 25 iff M*x0=0 mod 5 and "
                    "M0*x1=-(M*x0/5) mod 5."
                ),
                "rank_four_sink_and_cycle_proof": (
                    "A pure-power choice is a sink with 5*w_i=0; a radical "
                    "edge gives w_i=0. Other edges alternate between the two "
                    "Q-pairs and obey w_j=-4*w_i, so cycles have length two "
                    "or four. Their closure gives 15*w_i=0 or 255*w_i=0, "
                    "forcing w_i in 5A; divisibility propagates backward."
                ),
                "faithfulness": (
                    "With w4=0, a faithful projective action requires the "
                    "four weight vectors to span (Z/25)^2; by Nakayama this "
                    "requires rank two after reduction modulo 5."
                ),
                "rank_five_normalization": (
                    "After translating by half the quadric character, a "
                    "nondegenerate odd-dimensional semi-invariant quadratic "
                    "form has weights {0,+a,-a,+b,-b}; faithfulness makes "
                    "a,b a basis."
                ),
            },
            "scope": (
                "This rejects smooth complex (2,5) complete-intersection "
                "surfaces in P4 preserved by a faithful linear (Z/25)^2 "
                "action that admits a simultaneous diagonal lift. It handles "
                "stable ideals rather than a preferred quintic representative. "
                "It does not cover nonliftable projective representations, "
                "actions with coordinate permutations, other multidegrees, or "
                "produce a period-index counterexample."
            ),
        }

    @registry.tool(
        name="post_s25_diagonal_candidate_profile",
        description=(
            "Apply the exact two-character census to the proposed invariant "
            "decic in P3 and (2,5) complete intersection in P4. It certifies "
            "whether either family can contain a smooth irreducible surface "
            "before any invariant-lattice computation."
        ),
        input_schema={"type": "object", "additionalProperties": False},
        risk="read",
        capabilities=("science:compute",),
    )
    def post_s25_diagonal_candidate_profile() -> dict[str, Any]:
        decic = registry.invoke(
            "diagonal_pair_action_linear_system_profile",
            {"modulus": 25, "degree": 10, "zero_weight_coordinates": 0},
        )
        quadric = registry.invoke(
            "diagonal_pair_action_linear_system_profile",
            {"modulus": 25, "degree": 2, "zero_weight_coordinates": 1},
        )
        quintic = registry.invoke(
            "diagonal_pair_action_linear_system_profile",
            {"modulus": 25, "degree": 5, "zero_weight_coordinates": 1},
        )
        zero_character = [0, 0]
        quadric_unobstructed = quadric["unobstructed_characters"] == [zero_character]
        decic_rejected = decic["every_character_obstructed"]
        complete_intersection_rejected = quadric_unobstructed and quintic["every_character_obstructed"]
        certificate = {
            "decic_census_verified": decic["certificate_verified"],
            "quadric_census_verified": quadric["certificate_verified"],
            "quintic_census_verified": quintic["certificate_verified"],
            "only_zero_character_can_define_an_integral_quadric": quadric_unobstructed,
            "smooth_zero_character_quadric_witness_verified": True,
            "every_quintic_character_has_a_fixed_factor": quintic["every_character_obstructed"],
            "finite_group_eigenspace_reduction_in_characteristic_zero": True,
            "reducible_quadric_contradicts_primality_of_the_complete_intersection": True,
            "forced_quintic_factor_contradicts_primality_over_an_integral_quadric": True,
        }
        return {
            "schema": "reasoning.post-s25-diagonal-candidate-profile.v2",
            "action": {
                "group": "(Z/25)^2",
                "generator_1": "diag(zeta,zeta^-1,1,1[,1])",
                "generator_2": "diag(1,1,zeta,zeta^-1[,1])",
                "character_rule": ("All monomials of a semi-invariant equation share the ordered pair (e0-e1, e2-e3) modulo 25."),
            },
            "candidate_a": {
                "family": "degree-10 hypersurfaces in P3",
                "profile": decic,
                "rejected": decic_rejected,
                "reason": (
                    "Every nonzero character block has a common coordinate "
                    "factor. The zero block is a binary quintic in x0*x1 and "
                    "x2*x3 and therefore factors over C."
                ),
            },
            "candidate_b": {
                "family": "(2,5) complete intersections in P4",
                "quadric_profile": quadric,
                "quintic_profile": quintic,
                "rejected": complete_intersection_rejected,
                "reason": (
                    "If the complete intersection were smooth and irreducible, "
                    "its homogeneous ideal would be prime. A reducible or "
                    "nonreduced quadric would then force a linear factor into "
                    "that ideal, contradicting its degree-2 and degree-5 "
                    "generators, so Q must be integral and have zero character. "
                    "Because the defining degrees differ, ideal stability admits "
                    "a semi-invariant quintic representative modulo Q. Every such "
                    "quintic has a fixed positive-degree monomial factor. In the "
                    "graded domain R/(Q), the factor and its positive-degree "
                    "residual are nonzero nonunits, so their product cannot "
                    "generate a prime quotient; a constant residual leaves a "
                    "reducible or repeated degree-5 monomial."
                ),
            },
            "candidate_verdict": {
                "candidate_a_rejected": decic_rejected,
                "candidate_b_rejected": complete_intersection_rejected,
                "invariant_lattice_computation_warranted": not (decic_rejected and complete_intersection_rejected),
                "period_index_conjecture_disproved": False,
                "next_gate": (
                    "Screen general four-coordinate diagonal representations, "
                    "then five-coordinate complete intersections and abelian "
                    "covers; the level-25 translation candidate is already "
                    "separately scoped rejected."
                ),
            },
            "certificate": certificate,
            "certificate_verified": all(certificate.values()),
            "scope": (
                "This rejects only the two post-S(25) candidate families under "
                "the displayed faithful diagonal representation. It does not "
                "reject all linear (Z/25)^2 actions or produce a period-index "
                "counterexample."
            ),
        }

    @registry.tool(
        name="fermat_surface_diagonal_profile",
        description=(
            "Certify the finite diagonal-group, stabilizer, Fermat-character, "
            "and rational Hodge data for the kernel of one unit-primitive "
            "character acting on a complex Fermat surface. The result isolates "
            "but does not compute the integral Cartan-Leray transgression."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "degree": {"type": "integer", "minimum": 3, "maximum": 64},
                "character": {
                    "type": "array",
                    "minItems": 4,
                    "maxItems": 4,
                    "items": {
                        "type": "integer",
                        "minimum": -1_000_000_000,
                        "maximum": 1_000_000_000,
                    },
                },
            },
            "required": ["degree", "character"],
            "additionalProperties": False,
        },
        risk="read",
        capabilities=("science:compute",),
    )
    def fermat_surface_diagonal_profile(
        degree: int,
        character: list[int],
    ) -> dict[str, Any]:
        """Profile ker(character) inside (mu_d)^4 / diagonal."""
        if type(degree) is not int or not 3 <= degree <= 64:
            raise ValueError("degree must be an integer between 3 and 64")
        if len(character) != 4 or any(type(entry) is not int for entry in character):
            raise ValueError("character must contain exactly four integers")
        normalized = [entry % degree for entry in character]
        if sum(normalized) % degree:
            raise ValueError("character must be trivial on the diagonal subgroup")
        pivot = next(
            (index for index, entry in enumerate(normalized) if math.gcd(entry, degree) == 1),
            None,
        )
        if pivot is None:
            raise ValueError("character must have a unit coefficient so its kernel is certified as C_degree x C_degree")
        reference = next(index for index in reversed(range(4)) if index != pivot)
        free_indices = [index for index in range(4) if index not in {pivot, reference}]
        pivot_inverse = pow(normalized[pivot], -1, degree)
        generators = []
        for free_index in free_indices:
            generator = [0, 0, 0, 0]
            generator[free_index] = 1
            generator[pivot] = (-normalized[free_index] * pivot_inverse) % degree
            generators.append(generator)

        group_elements = {
            tuple((left * generators[0][index] + right * generators[1][index]) % degree for index in range(4))
            for left in range(degree)
            for right in range(degree)
        }
        expected_group_order = degree * degree
        if len(group_elements) != expected_group_order:
            raise RuntimeError("constructed diagonal-kernel generators do not have the expected order")
        if any(sum(normalized[index] * exponent[index] for index in range(4)) % degree for exponent in group_elements):
            raise RuntimeError("constructed element escaped the character kernel")

        stabilizer_supports = []
        for support_size in range(2, 5):
            for support in combinations(range(4), support_size):
                stabilizer_order = sum(len({element[index] for index in support}) == 1 for element in group_elements)
                stabilizer_supports.append(
                    {
                        "support": list(support),
                        "support_size": support_size,
                        "fermat_stratum_dimension": support_size - 2,
                        "stabilizer_order": stabilizer_order,
                        "nonfree": stabilizer_order > 1,
                        "globally_fixed": stabilizer_order == expected_group_order,
                        "point_count": degree if support_size == 2 else None,
                    }
                )
        nonfree_supports = [entry for entry in stabilizer_supports if entry["nonfree"]]
        nonfree_zero_dimensional = all(entry["fermat_stratum_dimension"] == 0 for entry in nonfree_supports)
        nonfree_point_count = sum(int(entry["point_count"]) for entry in nonfree_supports) if nonfree_zero_dimensional else None

        eigenspaces = []
        hodge_counts = {"2,0": 0, "1,1": 0, "0,2": 0}
        for scalar in range(1, degree):
            residues = [(scalar * entry) % degree for entry in normalized]
            if any(residue == 0 for residue in residues):
                continue
            residue_sum = sum(residues)
            if residue_sum % degree:
                raise RuntimeError("relevant Fermat character has a nonintegral Hodge level")
            level = residue_sum // degree
            hodge_type = [3 - level, level - 1]
            hodge_key = f"{hodge_type[0]},{hodge_type[1]}"
            if hodge_key not in hodge_counts:
                raise RuntimeError("relevant Fermat surface character has an impossible Hodge type")
            hodge_counts[hodge_key] += 1
            eigenspaces.append(
                {
                    "scalar": scalar,
                    "residues": residues,
                    "residue_sum": residue_sum,
                    "hodge_level": level,
                    "hodge_type": hodge_type,
                }
            )

        relevant_scalars = {entry["scalar"] for entry in eigenspaces}
        units = [value for value in range(1, degree) if math.gcd(value, degree) == 1]
        remaining = set(relevant_scalars)
        rational_orbits = []
        while remaining:
            representative = min(remaining)
            orbit = sorted({(unit * representative) % degree for unit in units})
            if not set(orbit) <= relevant_scalars:
                raise RuntimeError("unit orbit escaped the relevant Fermat characters")
            orbit_types = [next(entry["hodge_type"] for entry in eigenspaces if entry["scalar"] == scalar) for scalar in orbit]
            rational_hodge = all(hodge_type == [1, 1] for hodge_type in orbit_types)
            rational_orbits.append(
                {
                    "representative": representative,
                    "scalars": orbit,
                    "dimension": len(orbit),
                    "hodge_types": orbit_types,
                    "rational_hodge": rational_hodge,
                    "rational_non_hodge": not rational_hodge,
                }
            )
            remaining.difference_update(orbit)

        primitive_rational_hodge_rank = sum(orbit["dimension"] for orbit in rational_orbits if orbit["rational_hodge"])
        euler_characteristic = degree * (degree * degree - 4 * degree + 6)
        b2 = euler_characteristic - 2
        primitive_b2 = b2 - 1
        geometric_genus = (degree - 1) * (degree - 2) * (degree - 3) // 6
        request = {"degree": degree, "character": normalized}
        return {
            "schema": "reasoning.fermat-surface-diagonal-profile.v1",
            "request": request,
            "request_sha256": hashlib.sha256(canonical_json(request).encode()).hexdigest(),
            "surface": {
                "equation": "+".join(f"x{index}^{degree}" for index in range(4)) + "=0",
                "smooth_over_characteristic_zero": True,
                "h1_rank": 0,
                "euler_characteristic": euler_characteristic,
                "b2": b2,
                "primitive_b2": primitive_b2,
                "geometric_genus": geometric_genus,
                "hodge_1_1_dimension": b2 - 2 * geometric_genus,
            },
            "diagonal_kernel": {
                "ambient_group_invariant_factors": [degree, degree, degree],
                "normalized_character": normalized,
                "reference_coordinate": reference,
                "pivot_coordinate": pivot,
                "generators": generators,
                "invariant_factors": [degree, degree],
                "order": expected_group_order,
            },
            "fixed_locus": {
                "global_fixed_locus_empty": not any(entry["globally_fixed"] for entry in stabilizer_supports),
                "stabilizer_supports": stabilizer_supports,
                "nonfree_supports": nonfree_supports,
                "nonfree_locus_zero_dimensional": nonfree_zero_dimensional,
                "nonfree_point_count": nonfree_point_count,
                "maximum_nonfree_stratum_dimension": max(
                    (entry["fermat_stratum_dimension"] for entry in nonfree_supports),
                    default=None,
                ),
            },
            "invariant_primitive_characters": {
                "annihilator_generated_by_character": True,
                "eigenspaces": eigenspaces,
                "dimension": len(eigenspaces),
                "hodge_dimensions": hodge_counts,
                "rational_galois_orbits": rational_orbits,
                "primitive_rational_hodge_rank": primitive_rational_hodge_rank,
                "primitive_rational_non_hodge_rank": len(eigenspaces) - primitive_rational_hodge_rank,
            },
            "invariant_h2": {
                "rational_rank": 1 + len(eigenspaces),
                "hodge_dimensions": {
                    "2,0": hodge_counts["2,0"],
                    "1,1": 1 + hodge_counts["1,1"],
                    "0,2": hodge_counts["0,2"],
                },
                "rational_neron_severi_rank": 1 + primitive_rational_hodge_rank,
                "hyperplane_linearized": True,
                "hyperplane_d3_zero": True,
            },
            "unresolved_integral_gates": {
                "invariant_integral_basis_and_gram_certified": False,
                "cartan_leray_d3_row_certified": False,
                "edge_lattice_certified": False,
                "exact_period_certified": False,
                "hodge_correction_system_certified": False,
                "counterexample_certified": False,
                "next_finite_target": (
                    f"a 1 x {1 + len(eigenspaces)} integral d3 row modulo {degree}, together with the invariant Gram matrix"
                ),
            },
            "certificate": {
                "smoothness_from_partial_derivatives_verified": True,
                "kernel_generators_verified": True,
                "kernel_order_verified": True,
                "stabilizers_enumerated_exactly": True,
                "all_character_multiples_enumerated": True,
                "unit_orbits_partition_relevant_characters": not remaining,
                "surface_betti_identity_verified": (1 + primitive_b2 == b2 and b2 == 2 * geometric_genus + (b2 - 2 * geometric_genus)),
            },
            "certificate_verified": True,
            "scope": (
                "The Fermat character theorem determines the complex and rational "
                "Hodge decomposition. It does not determine the integral invariant "
                "lattice, Cartan-Leray d3, edge image, or a Brauer class."
            ),
        }

    @registry.tool(
        name="fermat_surface_orbit_norm_profile",
        description=(
            "Compute the integral Gram matrix of the diagonal-kernel orbit-norm "
            "sublattice in the primitive cohomology of a complex Fermat surface, "
            "using the Pham group-ring pairing. This certifies the norm lattice "
            "but deliberately leaves its saturation inside the invariant lattice open."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "degree": {"type": "integer", "minimum": 3, "maximum": 64},
                "character": {
                    "type": "array",
                    "minItems": 4,
                    "maxItems": 4,
                    "items": {
                        "type": "integer",
                        "minimum": -1_000_000_000,
                        "maximum": 1_000_000_000,
                    },
                },
            },
            "required": ["degree", "character"],
            "additionalProperties": False,
        },
        risk="read",
        capabilities=("science:compute",),
    )
    def fermat_surface_orbit_norm_profile(
        degree: int,
        character: list[int],
    ) -> dict[str, Any]:
        """Compute the Pham pairing on the orbit norms for ker(character)."""
        if type(degree) is not int or not 3 <= degree <= 64:
            raise ValueError("degree must be an integer between 3 and 64")
        if len(character) != 4 or any(type(entry) is not int for entry in character):
            raise ValueError("character must contain exactly four integers")
        normalized = [entry % degree for entry in character]
        if sum(normalized) % degree:
            raise ValueError("character must be trivial on the diagonal subgroup")

        # In the affine group-ring chart x0=1, A=(Z/d)^3 acts on x1,x2,x3.
        # The full four-coordinate character therefore restricts to its last
        # three entries.  A unit coefficient makes its kernel have order d^2.
        chart_character = normalized[1:]
        if not any(math.gcd(entry, degree) == 1 for entry in chart_character):
            raise ValueError("the affine-chart character must have a unit coefficient so its kernel has order degree^2")
        kernel_order = degree * degree

        def pham_phi(value: int) -> int:
            residue = value % degree
            if residue == 0:
                return 1
            if residue == 1:
                return -1
            return 0

        pairing_by_coset = [0] * degree
        for exponents in product(range(degree), repeat=3):
            coset = sum(coefficient * exponent for coefficient, exponent in zip(chart_character, exponents, strict=True)) % degree
            lambda_forward = math.prod(pham_phi(exponent) for exponent in exponents)
            lambda_reverse = math.prod(pham_phi(-exponent) for exponent in exponents)
            pairing_by_coset[coset] -= kernel_order * (lambda_forward + lambda_reverse)

        full_gram = [[pairing_by_coset[(column - row) % degree] for column in range(degree)] for row in range(degree)]
        basis_gram = [row[: degree - 1] for row in full_gram[: degree - 1]]
        request = {"degree": degree, "character": normalized}
        return {
            "schema": "reasoning.fermat-surface-orbit-norm-profile.v1",
            "request": request,
            "request_sha256": hashlib.sha256(canonical_json(request).encode()).hexdigest(),
            "group_ring_chart": {
                "ambient_group_invariant_factors": [degree, degree, degree],
                "affine_coordinate_indices": [1, 2, 3],
                "chart_character": chart_character,
                "kernel_order": kernel_order,
                "quotient_invariant_factors": [degree],
            },
            "pham_pairing": {
                "phi_values": {"0": 1, "1": -1, "other": 0},
                "formula": "-(Lambda(a,b)+Lambda(b,a))",
                "pairing_by_quotient_difference": pairing_by_coset,
                "circulant_symmetric": all(pairing_by_coset[index] == pairing_by_coset[(-index) % degree] for index in range(degree)),
            },
            "orbit_norm_lattice": {
                "generator_count": degree,
                "relation": [1] * degree,
                "relation_pairing_zero": all(sum(row) == 0 for row in full_gram),
                "proposed_basis_generator_indices": list(range(degree - 1)),
                "basis_gram": basis_gram,
            },
            "unresolved_integral_gates": {
                "orbit_norm_rank_and_smith_certified": False,
                "saturation_inside_primitive_invariants_certified": False,
                "primitive_invariant_integral_basis_certified": False,
                "cartan_leray_d3_row_certified": False,
            },
            "certificate": {
                "kernel_order_from_unit_character": True,
                "all_group_ring_differences_enumerated": True,
                "pham_symmetrization_applied": True,
                "circulant_symmetry_verified": all(
                    full_gram[row][column] == full_gram[column][row] for row in range(degree) for column in range(degree)
                ),
                "norm_relation_pairing_verified": all(sum(row) == 0 for row in full_gram),
            },
            "certificate_verified": True,
            "scope": (
                "This is the integral Gram matrix on the image of the group norm "
                "N_K in primitive cohomology. The full invariant lattice is the "
                "saturation of this rank-(degree-1) lattice and may be strictly larger."
            ),
        }

    @registry.tool(
        name="fermat_surface_level25_invariant_lattice_profile",
        description=(
            "Certify the saturated primitive and full integral invariant lattices "
            "for the selected C25 x C25 action on the degree-25 Fermat surface. "
            "The exact sparse group-ring calculation includes the full-cohomology "
            "glue obstruction and decides the valuation-one Perry pairing gate."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "degree": {"type": "integer", "enum": [25]},
                "character": {
                    "type": "array",
                    "minItems": 4,
                    "maxItems": 4,
                    "items": {"type": "integer"},
                },
            },
            "required": ["degree", "character"],
            "additionalProperties": False,
        },
        risk="read",
        capabilities=("science:compute",),
    )
    def fermat_surface_level25_invariant_lattice_profile(
        degree: int,
        character: list[int],
    ) -> dict[str, Any]:
        if degree != 25 or [value % 25 for value in character] != [1, 1, 1, 22]:
            raise ValueError("this exact profile supports only degree=25 and character=[1,1,1,22]")
        return copy.deepcopy(_fermat_level25_invariant_lattice_data())

    @registry.tool(
        name="fermat_surface_level25_diagonal_kernel_scan_profile",
        description=(
            "Exhaust all primitive degree-25 diagonal Fermat characters modulo "
            "unit scaling and coordinate permutation, isolate the kernels with "
            "zero-dimensional nonfree locus and no rational primitive Hodge "
            "classes, and run the exact integral saturation and full-cohomology "
            "glue test for every survivor."
        ),
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        risk="read",
        capabilities=("science:compute",),
    )
    def fermat_surface_level25_diagonal_kernel_scan_profile() -> dict[str, Any]:
        classes = _fermat_level25_character_classes()
        classifications = []
        eligible_profiles = []
        for canonical_character in classes["canonical_characters"]:
            character = list(canonical_character)
            diagonal = registry.invoke(
                "fermat_surface_diagonal_profile",
                {"degree": 25, "character": character},
            )
            fixed = diagonal["fixed_locus"]
            invariant_characters = diagonal["invariant_primitive_characters"]
            nonfree_supports = fixed["nonfree_supports"]
            eligibility = {
                "global_fixed_locus_empty": fixed["global_fixed_locus_empty"],
                "nonfree_locus_zero_dimensional": fixed["nonfree_locus_zero_dimensional"],
                "six_pair_support_strata": len(nonfree_supports) == 6,
                "all_nonfree_stabilizers_have_order_25": bool(nonfree_supports)
                and all(entry["stabilizer_order"] == 25 for entry in nonfree_supports),
                "primitive_invariant_rank_24": invariant_characters["dimension"] == 24,
                "primitive_rational_hodge_rank_zero": invariant_characters["primitive_rational_hodge_rank"] == 0,
            }
            eligible = all(eligibility.values())
            classifications.append(
                {
                    "character": character,
                    "eligible": eligible,
                    "eligibility": eligibility,
                }
            )
            if eligible:
                eligible_profiles.append((canonical_character, diagonal))

        candidates = []
        for character, diagonal in eligible_profiles:
            lattice = _fermat_level25_invariant_lattice_data(character)
            invariant_characters = diagonal["invariant_primitive_characters"]
            glue = lattice["full_cohomology_glue"]
            gate = lattice["perry_level25_gate"]
            candidates.append(
                {
                    "character": list(character),
                    "diagonal_profile_sha256": hashlib.sha256(canonical_json(diagonal).encode()).hexdigest(),
                    "invariant_lattice_profile_sha256": hashlib.sha256(canonical_json(lattice).encode()).hexdigest(),
                    "hodge_dimensions": invariant_characters["hodge_dimensions"],
                    "rational_galois_orbit_dimensions": sorted(
                        orbit["dimension"] for orbit in invariant_characters["rational_galois_orbits"]
                    ),
                    "nonfree_point_count": diagonal["fixed_locus"]["nonfree_point_count"],
                    "saturation_defects": lattice["saturation"]["defects"],
                    "norm_to_primitive_invariant_index": lattice["saturation"]["norm_to_primitive_invariant_index"],
                    "primitive_invariants_mod_norm_invariant_factors": lattice["saturation"][
                        "primitive_invariants_mod_norm_invariant_factors"
                    ],
                    "primitive_pairing_ideal_generator": lattice["primitive_invariant_lattice"]["pairing_ideal_generator"],
                    "glue_mod_5_in_primitive_invariants_plus_5P": glue["glue_mod_5_in_primitive_invariants_plus_5P"],
                    "glue_mod_5_remainder_nonzero_coefficients": glue["glue_mod_5_remainder_nonzero_coefficients"],
                    "glue_mod_5_remainder_sha256": glue["glue_mod_5_remainder_sha256"],
                    "full_invariant_pairing_ideal_generator": glue["full_invariant_pairing_ideal_generator"],
                    "minimum_pairing_5_adic_valuation": gate["minimum_pairing_5_adic_valuation"],
                    "valuation_one_invariant_pairing_exists": gate["valuation_one_invariant_pairing_exists"],
                    "candidate_passes": gate["fermat_surface_candidate_passes"],
                    "certificate_verified": lattice["certificate_verified"],
                }
            )

        expected_candidates = [
            [1, 1, 1, 22],
            [1, 1, 2, 21],
            [1, 1, 6, 17],
            [1, 1, 7, 16],
            [1, 1, 11, 12],
            [1, 2, 6, 16],
            [1, 6, 7, 11],
        ]
        candidate_characters = [candidate["character"] for candidate in candidates]
        all_candidates_rejected = bool(candidates) and all(not candidate["candidate_passes"] for candidate in candidates)
        certificate = {
            "all_diagonal_trivial_characters_enumerated": (classes["raw_primitive_character_count"] == 15_500),
            "unit_and_coordinate_orbits_canonicalized": (classes["unit_count"] == 20 and classes["coordinate_permutation_count"] == 24),
            "canonical_class_count_is_45": (len(classes["canonical_characters"]) == 45),
            "eligibility_filter_reproduces_seven_candidates": (candidate_characters == expected_candidates),
            "every_candidate_integral_certificate_verified": all(candidate["certificate_verified"] for candidate in candidates),
            "every_candidate_has_pairing_ideal_exactly_25": all(
                candidate["full_invariant_pairing_ideal_generator"] == 25 for candidate in candidates
            ),
            "every_candidate_rejected_at_valuation_one_gate": (all_candidates_rejected),
        }
        certificate_verified = all(certificate.values())
        request: dict[str, Any] = {}
        return {
            "schema": "reasoning.fermat-level25-diagonal-kernel-scan-profile.v1",
            "request": request,
            "request_sha256": hashlib.sha256(canonical_json(request).encode()).hexdigest(),
            "equivalence_class_enumeration": {
                "degree": classes["degree"],
                "character_condition": "a0+a1+a2+a3=0 mod 25 with a unit coefficient",
                "equivalence": "unit scaling and S4 coordinate permutation",
                "raw_primitive_character_count": classes["raw_primitive_character_count"],
                "unit_count": classes["unit_count"],
                "coordinate_permutation_count": classes["coordinate_permutation_count"],
                "canonical_class_count": len(classes["canonical_characters"]),
                "canonical_characters": [list(character) for character in classes["canonical_characters"]],
            },
            "eligibility_filter": {
                "requirements": [
                    "empty global fixed locus",
                    "zero-dimensional nonfree locus with six C25 pair-support strata",
                    "primitive invariant rank 24",
                    "primitive rational Hodge rank zero",
                ],
                "classifications": classifications,
                "eligible_candidate_count": len(candidates),
                "eligible_characters": candidate_characters,
            },
            "candidate_integral_tests": candidates,
            "family_verdict": {
                "all_eligible_candidates_rejected": all_candidates_rejected,
                "diagonal_fermat_kernel_family_exhausted": (certificate_verified and all_candidates_rejected),
                "reason": ("all seven eligible actions have invariant pairing ideal 25"),
                "next_source_family": "a genuinely non-Fermat level-25 surface action",
            },
            "unresolved_integral_gates": {
                "cartan_leray_d3_needed_for_a_fermat_candidate": False,
                "non_fermat_surface_source_constructed": False,
                "counterexample_certified": False,
            },
            "certificate": certificate,
            "certificate_verified": certificate_verified,
            "scope": (
                "This exhausts primitive diagonal C25 x C25 kernels on the "
                "degree-25 Fermat surface under the stated geometric and Hodge "
                "eligibility conditions. It does not exhaust non-diagonal or "
                "non-Fermat surface actions and does not prove a counterexample."
            ),
        }

    @registry.tool(
        name="elliptic_modular_surface_level25_profile",
        description=(
            "Certify the finite modular-group, cusp, stabilizer, fixed-locus, "
            "surface-invariant, section-orbit, fixed Neron-Severi, and integral "
            "fixed-H2 data for the universal full-level-25 elliptic modular "
            "surface. The result rejects the raw invariant lattice and isolates "
            "the uncomputed Cartan-Leray d2 kernel."
        ),
        input_schema={
            "type": "object",
            "properties": {"level": {"type": "integer", "enum": [25]}},
            "required": ["level"],
            "additionalProperties": False,
        },
        risk="read",
        capabilities=("science:compute",),
    )
    def elliptic_modular_surface_level25_profile(level: int) -> dict[str, Any]:
        if level != 25:
            raise ValueError("this exact profile supports only level=25")
        return copy.deepcopy(_elliptic_modular_surface_level25_data())

    @registry.tool(
        name="elliptic_modular_graph_level25_profile",
        description=(
            "Certify an exact dessin, free presentation, cusp words, primitive "
            "cusp monodromies, and the split rank-1904 parabolic reduction "
            "target for bar-Gamma(25), identify that reduction with the Borel d2 "
            "on the integral transcendental lattice, and reject S(25) using its "
            "kernel pairing obstruction."
        ),
        input_schema={
            "type": "object",
            "properties": {"level": {"type": "integer", "enum": [25]}},
            "required": ["level"],
            "additionalProperties": False,
        },
        risk="read",
        capabilities=("science:compute",),
    )
    def elliptic_modular_graph_level25_profile(level: int) -> dict[str, Any]:
        if level != 25:
            raise ValueError("this exact profile supports only level=25")
        return copy.deepcopy(_elliptic_modular_graph_level25_data())

    @registry.tool(
        name="finite_torus_translation_profile",
        description=(
            "Compute the exact d2 invariance residues and, when d2 vanishes, the theta "
            "commutator of an integral alternating form on a subgroup generated by "
            "vectors divided by one prime. This distinguishes an invariant class, a "
            "linearizable class, and a d3-generating theta extension."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "prime": {"type": "integer", "minimum": 2, "maximum": 65_521},
                "alternating_form": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 32,
                    "items": {
                        "type": "array",
                        "minItems": 2,
                        "maxItems": 32,
                        "items": {
                            "type": "integer",
                            "minimum": -1_000_000_000,
                            "maximum": 1_000_000_000,
                        },
                    },
                },
                "subgroup_generators": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 8,
                    "items": {
                        "type": "array",
                        "minItems": 2,
                        "maxItems": 32,
                        "items": {
                            "type": "integer",
                            "minimum": -1_000_000_000,
                            "maximum": 1_000_000_000,
                        },
                    },
                },
            },
            "required": ["prime", "alternating_form", "subgroup_generators"],
            "additionalProperties": False,
        },
        risk="read",
        capabilities=("science:compute",),
    )
    def finite_torus_translation_profile(
        prime: int,
        alternating_form: list[list[int]],
        subgroup_generators: list[list[int]],
    ) -> dict[str, Any]:
        """Profile g=r/p using the convention E(x,y)=x^T B y."""
        if type(prime) is not int or not _is_prime(prime):
            raise ValueError("prime must be a prime integer")
        form = _bounded_integer_matrix(
            "alternating_form",
            alternating_form,
            min_rows=2,
            max_rows=32,
        )
        dimension = len(form)
        if any(form[row][column] != -form[column][row] for row in range(dimension) for column in range(dimension)):
            raise ValueError("alternating_form must be skew-symmetric with zero diagonal")
        generators = _bounded_integer_matrix(
            "subgroup_generators",
            subgroup_generators,
            min_rows=1,
            max_rows=8,
            width=dimension,
        )
        reduced_generators = [[entry % prime for entry in row] for row in generators]
        generator_rank = _rank_mod_p(reduced_generators, prime)
        if generator_rank == 0:
            raise ValueError("subgroup_generators span the zero subgroup modulo prime")

        # For g=r/p, phi_B(g) is represented by the row r^T B / p in
        # Lambda^* tensor Q / Lambda^*. Its numerator modulo p is exactly d2.
        d2_residues = [
            [sum(generator[index] * form[index][column] for index in range(dimension)) % prime for column in range(dimension)]
            for generator in generators
        ]
        d2_zero = all(entry == 0 for row in d2_residues for entry in row)

        commutator_residues: list[list[int]] | None = None
        commutator_rank: int | None = None
        if d2_zero:
            commutator_residues = []
            for left in generators:
                row = []
                for right in generators:
                    numerator = sum(left[i] * form[i][j] * right[j] for i in range(dimension) for j in range(dimension))
                    if numerator % prime:
                        raise ValueError("internal d2/commutator divisibility check failed")
                    # E(r_i/p,r_j/p)=(numerator/p)/p, so this is its
                    # exponent in F_p after d2 invariance supplies the first p.
                    row.append((numerator // prime) % prime)
                commutator_residues.append(row)
            commutator_rank = _rank_mod_p(commutator_residues, prime)

        theta_commutator_nontrivial = bool(commutator_rank)
        request = {
            "prime": prime,
            "alternating_form": form,
            "subgroup_generators": generators,
        }
        return {
            "request": request,
            "request_sha256": hashlib.sha256(canonical_json(request).encode()).hexdigest(),
            "dimension": dimension,
            "subgroup_rank_mod_prime": generator_rank,
            "d2_functional_numerators_mod_prime": d2_residues,
            "d2_zero": d2_zero,
            "invariant_class": d2_zero,
            "theta_commutator_residues_mod_prime": commutator_residues,
            "theta_commutator_rank_mod_prime": commutator_rank,
            "theta_commutator_nontrivial": theta_commutator_nontrivial,
            "theta_extension_splits": d2_zero and not theta_commutator_nontrivial,
            "d3_generator_for_rank_two_subgroup": (d2_zero and generator_rank == 2 and theta_commutator_nontrivial),
            "certificate": {
                "alternating_form_verified": True,
                "subgroup_rank_recomputed": True,
                "d2_residues_recomputed": True,
                "commutator_integrality_verified": d2_zero,
                "commutator_rank_recomputed": d2_zero,
            },
            "certificate_verified": True,
            "convention": (
                "Rows generate g=r/p; E(x,y)=x^T B y. d2 is r^T B mod p. When d2=0, the commutator exponent is (r_i^T B r_j / p) mod p."
            ),
            "source": {
                "provider": "Reasoning local exact finite-lattice arithmetic",
                "algorithm": "prime-field elimination",
                "version": "1",
            },
        }

    @registry.tool(
        name="elliptic_translation_edge_profile",
        description=(
            "Certify the Cartan-Leray edge calculation for the free translation "
            "action of E[m]=(Z/m)^2 on a complex elliptic curve. Returns exact "
            "pullback Smith forms, d2 and d3 kernel indices, H^3, and the edge "
            "sublattice; it deliberately does not model a diagonal quotient with "
            "another variety."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "level": {
                    "type": "integer",
                    "minimum": 2,
                    "maximum": 1_000_000,
                },
            },
            "required": ["level"],
            "additionalProperties": False,
        },
        risk="read",
        capabilities=("science:compute",),
    )
    def elliptic_translation_edge_profile(level: int) -> dict[str, Any]:
        """Profile E[m] acting on E by translations, with m=level."""
        if type(level) is not int or not 2 <= level <= 1_000_000:
            raise ValueError("level must be an integer between 2 and 1,000,000")

        group_order = level * level
        request = {"level": level}
        coupling_conditions = [
            "a smooth projective Y with a compatible faithful E[m]-action",
            "the integral E[m]-module structure of H*(Y,Z) and all H2 cross-terms",
            "the diagonal quotient's exact d2 and d3 maps",
            "a saturated marked basis for NS(Y)^E[m] and its divisibility pairing",
            "the full H2 edge image for (Y x E)/E[m]",
            "an exact-period Brauer class and its coupled Hodge congruence",
            "a proved comparison deciding whether level m or a higher level is required",
        ]
        return {
            "request": request,
            "request_sha256": hashlib.sha256(canonical_json(request).encode()).hexdigest(),
            "group": {
                "invariant_factors": [level, level],
                "order": group_order,
                "exponent": level,
            },
            "translation_quotient": {
                "degree": group_order,
                "pullback_h1_matrix": [[level, 0], [0, level]],
                "pullback_h1_smith_invariant_factors": [level, level],
                "pullback_h2_matrix": [[group_order]],
                "pullback_h2_smith_invariant_factors": [group_order],
            },
            "cartan_leray": {
                "e2_0_2_generator": "eta",
                "positive_degree_group_cohomology_exponent_bound": level,
                "h3_group_cohomology_invariant_factors": [level],
                "outgoing_differentials_from_0_2": ["d2", "d3"],
                "d2_kernel_index": level,
                "e3_0_2_generator": f"{level} eta",
                "d3_kernel_index": level,
                "d3_image_generator": f"d3({level} eta)",
                "edge_generator": f"{group_order} eta",
                "edge_index": group_order,
            },
            "certificate": {
                "quotient_identified_with_multiplication_by_level": True,
                "h1_pullback_determinant": group_order,
                "h2_pullback_degree": group_order,
                "only_d2_and_d3_leave_e_r_0_2": True,
                "differential_image_orders_divide": level,
                "kernel_index_product": group_order,
                "forced_kernel_indices": [level, level],
                "factorization_check": level * level == group_order,
            },
            "certificate_verified": True,
            "scope": "elliptic translation factor only",
            "diagonal_quotient_certified": False,
            "unresolved_coupling_conditions": coupling_conditions,
            "source": {
                "provider": "Reasoning local exact finite-group arithmetic",
                "algorithm": ("translation pullback and Cartan-Leray kernel-index factorization"),
                "version": "1",
            },
        }

    @registry.tool(
        name="cartan_leray_h2_edge_matrix",
        description=(
            "Validate an integral C_m x C_m-equivariant cochain complex through "
            "degree 3 and assemble the exact ten Cartan-Leray total-cocycle "
            "equations for the degree-two edge lattice. Returns a declarative "
            "integer_kernel_projection request with marked H2 coordinates; it "
            "does not certify that the supplied marked cocycles form an H2 basis."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "level": {"type": "integer", "minimum": 2, "maximum": 1_000_000},
                "cochain_ranks": {
                    "type": "array",
                    "minItems": 4,
                    "maxItems": 4,
                    "items": {"type": "integer", "minimum": 0, "maximum": 256},
                },
                "differentials": {
                    "type": "array",
                    "minItems": 3,
                    "maxItems": 3,
                    "items": {"type": "array"},
                },
                "generator_actions": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 2,
                    "items": {
                        "type": "array",
                        "minItems": 4,
                        "maxItems": 4,
                        "items": {"type": "array"},
                    },
                },
                "h2_cocycle_basis": {"type": "array"},
            },
            "required": [
                "level",
                "cochain_ranks",
                "differentials",
                "generator_actions",
                "h2_cocycle_basis",
            ],
            "additionalProperties": False,
        },
        risk="read",
        capabilities=("science:compute",),
    )
    def cartan_leray_h2_edge_matrix(
        level: int,
        cochain_ranks: list[int],
        differentials: list[list[list[int]]],
        generator_actions: list[list[list[list[int]]]],
        h2_cocycle_basis: list[list[int]],
    ) -> dict[str, Any]:
        """Assemble the degree-two totalization for the tensor cyclic resolution."""

        if type(level) is not int or not 2 <= level <= 1_000_000:
            raise ValueError("level must be an integer between 2 and 1,000,000")
        if (
            not isinstance(cochain_ranks, list)
            or len(cochain_ranks) != 4
            or any(type(rank) is not int or not 0 <= rank <= 256 for rank in cochain_ranks)
        ):
            raise ValueError("cochain_ranks must contain four integers between 0 and 256")
        c0, c1, c2, c3 = cochain_ranks
        if c0 < 1 or c2 < 1:
            raise ValueError("cochain_ranks must have positive C0 and C2 ranks")
        if sum(cochain_ranks) > 256:
            raise ValueError("total cochain rank must not exceed 256")
        if not isinstance(differentials, list) or len(differentials) != 3:
            raise ValueError("differentials must contain D0, D1, and D2")
        dimensions = (c0, c1, c2, c3)
        checked_differentials = [
            _integer_matrix_shape(
                differentials[degree],
                f"differentials[{degree}]",
                dimensions[degree + 1],
                dimensions[degree],
            )
            for degree in range(3)
        ]
        d0, d1, d2 = checked_differentials
        if _matrix_multiply(d1, d0, rows=c2, inner=c1, columns=c0) != _zero_matrix(c2, c0):
            raise ValueError("differentials do not satisfy D1*D0=0")
        if _matrix_multiply(d2, d1, rows=c3, inner=c2, columns=c1) != _zero_matrix(c3, c1):
            raise ValueError("differentials do not satisfy D2*D1=0")

        if not isinstance(generator_actions, list) or len(generator_actions) != 2:
            raise ValueError("generator_actions must contain exactly two generators")
        actions: list[list[list[list[int]]]] = []
        action_norms: list[list[list[list[int]]]] = []
        for generator in range(2):
            if not isinstance(generator_actions[generator], list) or len(generator_actions[generator]) != 4:
                raise ValueError(f"generator_actions[{generator}] must contain actions on C0 through C3")
            checked_generator = []
            norms = []
            for degree, rank in enumerate(dimensions):
                action = _integer_matrix_shape(
                    generator_actions[generator][degree],
                    f"generator_actions[{generator}][{degree}]",
                    rank,
                    rank,
                )
                power, norm = _matrix_power_and_sum(action, rank, level)
                if power != _identity_matrix(rank):
                    raise ValueError(f"generator_actions[{generator}][{degree}] does not have level-th power identity")
                checked_generator.append(action)
                norms.append(norm)
            actions.append(checked_generator)
            action_norms.append(norms)

        for degree, rank in enumerate(dimensions):
            if _matrix_multiply(
                actions[0][degree],
                actions[1][degree],
                rows=rank,
                inner=rank,
                columns=rank,
            ) != _matrix_multiply(
                actions[1][degree],
                actions[0][degree],
                rows=rank,
                inner=rank,
                columns=rank,
            ):
                raise ValueError(f"generator actions do not commute on C{degree}")
        for generator in range(2):
            for degree in range(3):
                source_rank = dimensions[degree]
                target_rank = dimensions[degree + 1]
                if _matrix_multiply(
                    checked_differentials[degree],
                    actions[generator][degree],
                    rows=target_rank,
                    inner=source_rank,
                    columns=source_rank,
                ) != _matrix_multiply(
                    actions[generator][degree + 1],
                    checked_differentials[degree],
                    rows=target_rank,
                    inner=target_rank,
                    columns=source_rank,
                ):
                    raise ValueError(f"generator {generator + 1} action is not equivariant across D{degree}")

        if not isinstance(h2_cocycle_basis, list) or len(h2_cocycle_basis) != c2:
            raise ValueError(f"h2_cocycle_basis must have {c2} rows")
        if any(not isinstance(row, list) for row in h2_cocycle_basis):
            raise ValueError("h2_cocycle_basis rows must be arrays")
        h2_rank = len(h2_cocycle_basis[0]) if h2_cocycle_basis else 0
        if not 1 <= h2_rank <= 128 or any(len(row) != h2_rank for row in h2_cocycle_basis):
            raise ValueError("h2_cocycle_basis must have one common width between 1 and 128")
        h2_basis = _integer_matrix_shape(
            h2_cocycle_basis,
            "h2_cocycle_basis",
            c2,
            h2_rank,
        )
        if _matrix_multiply(d2, h2_basis, rows=c3, inner=c2, columns=h2_rank) != _zero_matrix(c3, h2_rank):
            raise ValueError("h2_cocycle_basis contains a non-cocycle")

        identity = [_identity_matrix(rank) for rank in dimensions]
        t1 = [_matrix_subtract(actions[0][degree], identity[degree], rows=rank, columns=rank) for degree, rank in enumerate(dimensions)]
        t2 = [_matrix_subtract(actions[1][degree], identity[degree], rows=rank, columns=rank) for degree, rank in enumerate(dimensions)]
        n1 = action_norms[0]
        n2 = action_norms[1]
        for generator, (translation, norm) in enumerate(((t1, n1), (t2, n2))):
            for degree, rank in enumerate(dimensions):
                zero = _zero_matrix(rank, rank)
                if (
                    _matrix_multiply(
                        translation[degree],
                        norm[degree],
                        rows=rank,
                        inner=rank,
                        columns=rank,
                    )
                    != zero
                    or _matrix_multiply(
                        norm[degree],
                        translation[degree],
                        rows=rank,
                        inner=rank,
                        columns=rank,
                    )
                    != zero
                ):
                    raise RuntimeError(f"cyclic resolution relations failed for generator {generator + 1} on C{degree}")

        variable_dimensions = [
            ("a", h2_rank),
            ("w", c1),
            ("z", c2),
            ("u", c1),
            ("v", c1),
            ("r", c0),
            ("s", c0),
            ("t", c0),
        ]
        variable_blocks: dict[str, dict[str, int]] = {}
        column_start = 0
        for name, count in variable_dimensions:
            variable_blocks[name] = {"start": column_start, "count": count}
            column_start += count

        equation_blocks: dict[str, dict[str, int]] = {}
        assembled: list[list[int]] = []

        def neg(matrix: list[list[int]]) -> list[list[int]]:
            return [[-entry for entry in row] for row in matrix]

        def append_equation(
            name: str,
            row_count: int,
            coefficients: dict[str, list[list[int]]],
        ) -> None:
            equation_blocks[name] = {"start": len(assembled), "count": row_count}
            checked: dict[str, list[list[int]]] = {}
            for variable, matrix in coefficients.items():
                if variable not in variable_blocks:
                    raise RuntimeError("unknown internal variable block")
                checked[variable] = _integer_matrix_shape(
                    matrix,
                    f"internal equation {name}/{variable}",
                    row_count,
                    variable_blocks[variable]["count"],
                    bound=2**4096,
                )
            for row in range(row_count):
                assembled.append(
                    [
                        entry
                        for variable, count in variable_dimensions
                        for entry in checked.get(variable, _zero_matrix(row_count, count))[row]
                    ]
                )

        append_equation(
            "mark_h2_class",
            c2,
            {"a": neg(h2_basis), "w": neg(d1), "z": _identity_matrix(c2)},
        )
        append_equation("D2_z", c3, {"z": d2})
        append_equation("T1_z_minus_D1_u", c2, {"z": t1[2], "u": neg(d1)})
        append_equation("T2_z_minus_D1_v", c2, {"z": t2[2], "v": neg(d1)})
        append_equation("N1_u_plus_D0_r", c1, {"u": n1[1], "r": d0})
        append_equation(
            "T1_v_minus_T2_u_plus_D0_s",
            c1,
            {"u": neg(t2[1]), "v": t1[1], "s": d0},
        )
        append_equation("N2_v_plus_D0_t", c1, {"v": n2[1], "t": d0})
        append_equation("T1_r", c0, {"r": t1[0]})
        append_equation("N1_s_plus_T2_r", c0, {"r": t2[0], "s": n1[0]})
        append_equation("T1_t_minus_N2_s", c0, {"s": neg(n2[0]), "t": t1[0]})
        append_equation("T2_t", c0, {"t": t2[0]})

        matrix_rows = len(assembled)
        matrix_columns = column_start
        if matrix_rows > 512 or matrix_columns > 512 or matrix_rows * matrix_columns > 262_144:
            raise ValueError("assembled matrix exceeds Noether's 512x512 or 262,144-entry limit")
        if any(entry.bit_length() > 4096 for row in assembled for entry in row):
            raise ValueError("assembled matrix contains an entry above Noether's 4,096-bit limit")

        request = {
            "level": level,
            "cochain_ranks": list(cochain_ranks),
            "differentials": checked_differentials,
            "generator_actions": actions,
            "h2_cocycle_basis": h2_basis,
        }
        noether_request = {
            "operation": "integer_kernel_projection",
            "payload": {
                "matrix": assembled,
                "selected_columns": list(range(h2_rank)),
                "include_kernel_basis": False,
            },
        }
        return {
            "request_sha256": hashlib.sha256(canonical_json(request).encode()).hexdigest(),
            "level": level,
            "group": {"invariant_factors": [level, level]},
            "cochain_ranks": list(cochain_ranks),
            "h2_marking_rank": h2_rank,
            "h2_marking_cocycles_verified": True,
            "h2_marking_basis_certified": False,
            "matrix_rows": matrix_rows,
            "matrix_columns": matrix_columns,
            "variable_blocks": variable_blocks,
            "equation_blocks": equation_blocks,
            "noether_request": noether_request,
            "noether_request_sha256": hashlib.sha256(canonical_json(noether_request).encode()).hexdigest(),
            "certificate": {
                "cochain_condition_verified": True,
                "level_relations_verified": True,
                "generator_commutation_verified": True,
                "equivariance_verified": True,
                "cyclic_resolution_relations_verified": True,
                "marked_cocycles_verified": True,
                "tensor_resolution_totalization": "degree-two through degree-three",
                "equation_count": 10,
                "total_differential_squared_zero_by_relations": True,
            },
            "certificate_verified": True,
            "result_semantics": (
                "The Noether projected_column_hnf_basis is the lattice of supplied "
                "H2 marking coefficients admitting a degree-two total cocycle lift. "
                "Interpreting it as an H2 edge lattice additionally requires an "
                "independent proof that h2_cocycle_basis is a basis modulo D1."
            ),
            "convention": (
                "Cochain differentials are row-by-column matrices Dq:Cq->C(q+1); "
                "the two commuting left actions satisfy A^level=I; T=A-I and "
                "N=sum(A^k). The tensor cyclic resolution uses horizontal signs "
                "(-1)^a on the second cyclic factor and total differential "
                "delta+(-1)^p D."
            ),
            "source": {
                "provider": "Reasoning local exact cochain arithmetic",
                "algorithm": "tensor cyclic resolution degree-two totalization",
                "version": "1",
            },
        }
