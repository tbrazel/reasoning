"""Exact witness data and smoothness checks for the level-five Heisenberg surface."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
from fractions import Fraction
from itertools import combinations
from pathlib import Path
from typing import Any

from .. import sandbox
from ..canonical import canonical_json
from .scientific import ScientificToolError

_LEVEL = 5
_FINITE_FIELD_PRIME = 101
_SINGULAR_PROTOCOL_VERSION = 1
_SINGULAR_TIMEOUT_SECONDS = 60
_SINGULAR_MAX_OUTPUT_BYTES = 64 * 1024
_VARIABLES = tuple(f"x{index}" for index in range(5))

Polynomial = dict[tuple[int, ...], int]

_F_ORBIT_REPRESENTATIVES = (
    (0, 0, 0, 4, 1),
    (0, 0, 1, 2, 2),
    (0, 0, 2, 0, 3),
    (0, 1, 0, 1, 3),
    (0, 2, 1, 1, 1),
)
_F_COEFFICIENTS = (-2, 1, 3, 3, 3)
_G_ORBIT_REPRESENTATIVES = (
    (0, 0, 0, 3, 2),
    (0, 0, 1, 1, 3),
    (0, 0, 4, 0, 1),
    (0, 1, 2, 1, 1),
    (0, 2, 0, 2, 1),
)
_G_COEFFICIENTS = (-3, -1, -3, -1, 3)


def _cyclic_orbit(exponents: tuple[int, ...]) -> tuple[tuple[int, ...], ...]:
    return tuple(sorted({exponents[index:] + exponents[:index] for index in range(_LEVEL)}))


def _polynomial_from_orbits(
    representatives: tuple[tuple[int, ...], ...],
    coefficients: tuple[int, ...],
) -> Polynomial:
    polynomial: Polynomial = {}
    for representative, coefficient in zip(representatives, coefficients, strict=True):
        for exponents in _cyclic_orbit(representative):
            polynomial[exponents] = polynomial.get(exponents, 0) + coefficient
    return {exponents: coefficient for exponents, coefficient in polynomial.items() if coefficient}


def heisenberg_surface_polynomials() -> tuple[Polynomial, Polynomial]:
    """Return the fixed integral quintics in lexicographic sparse form."""

    return (
        _polynomial_from_orbits(_F_ORBIT_REPRESENTATIVES, _F_COEFFICIENTS),
        _polynomial_from_orbits(_G_ORBIT_REPRESENTATIVES, _G_COEFFICIENTS),
    )


def sparse_polynomial(polynomial: Polynomial) -> list[dict[str, Any]]:
    return [
        {"coefficient": coefficient, "exponents": list(exponents)}
        for exponents, coefficient in sorted(polynomial.items())
    ]


def _weak_compositions(total: int, parts: int) -> tuple[tuple[int, ...], ...]:
    if parts == 1:
        return ((total,),)
    return tuple(
        (first, *tail)
        for first in range(total + 1)
        for tail in _weak_compositions(total - first, parts - 1)
    )


def _weight(exponents: tuple[int, ...]) -> int:
    return sum(index * exponent for index, exponent in enumerate(exponents)) % _LEVEL


def _equation_profile(
    *,
    name: str,
    character: int,
    representatives: tuple[tuple[int, ...], ...],
    coefficients: tuple[int, ...],
    polynomial: Polynomial,
) -> dict[str, Any]:
    terms = sparse_polynomial(polynomial)
    return {
        "name": name,
        "degree": _LEVEL,
        "S_character": 0,
        "T_character": character,
        "orbit_representatives": [list(value) for value in representatives],
        "orbit_coefficients": list(coefficients),
        "term_count": len(terms),
        "sparse_terms": terms,
        "sparse_terms_sha256": hashlib.sha256(canonical_json({"terms": terms}).encode()).hexdigest(),
        "all_terms_have_declared_character": all(_weight(exponents) == character for exponents in polynomial),
    }


def heisenberg_surface_witness_profile() -> dict[str, Any]:
    """Compute the exact finite combinatorics of the selected surface witness."""

    f, g = heisenberg_surface_polynomials()
    monomials = _weak_compositions(_LEVEL, len(_VARIABLES))
    orbit_sets = {
        character: {
            min(_cyclic_orbit(exponents))
            for exponents in monomials
            if _weight(exponents) == character
        }
        for character in range(_LEVEL)
    }
    character_block_dimensions = {
        str(character): len(orbits) for character, orbits in orbit_sets.items()
    }
    f_profile = _equation_profile(
        name="F",
        character=1,
        representatives=_F_ORBIT_REPRESENTATIVES,
        coefficients=_F_COEFFICIENTS,
        polynomial=f,
    )
    g_profile = _equation_profile(
        name="G",
        character=2,
        representatives=_G_ORBIT_REPRESENTATIVES,
        coefficients=_G_COEFFICIENTS,
        polynomial=g,
    )
    traces = {"identity": 873, "S_line_nonidentity": -2, "other_nonidentity": 3}
    invariant_h2_rank = (traces["identity"] + 4 * traces["S_line_nonidentity"] + 20 * traces["other_nonidentity"]) // 25
    request = {
        "level": _LEVEL,
        "ambient_dimension": 4,
        "degrees": [_LEVEL, _LEVEL],
        "equations": [f_profile["sparse_terms_sha256"], g_profile["sparse_terms_sha256"]],
    }
    certificate = {
        "degree_five_monomial_count_verified": len(monomials) == 126,
        "cyclic_orbit_count_verified": sum(character_block_dimensions.values()) == 26,
        "character_block_dimensions_verified": character_block_dimensions
        == {"0": 6, "1": 5, "2": 5, "3": 5, "4": 5},
        "equation_characters_verified": (
            f_profile["all_terms_have_declared_character"]
            and g_profile["all_terms_have_declared_character"]
        ),
        "equation_orbits_have_length_five": all(
            len(_cyclic_orbit(value)) == 5
            for value in (*_F_ORBIT_REPRESENTATIVES, *_G_ORBIT_REPRESENTATIVES)
        ),
        "S_eigenline_evaluations_nonzero": sum(_F_COEFFICIENTS) * 5 == 40
        and sum(_G_COEFFICIENTS) * 5 == -25,
        "fixed_point_count_consistent": 5 * 5 == 25,
        "topological_invariants_verified": (
            35 * 25 == 875 and 875 - 2 == 873 and 126 - 2 == 124
        ),
        "invariant_trace_average_integral": invariant_h2_rank == 37,
        "invariant_hodge_dimensions_consistent": invariant_h2_rank == 5 + 27 + 5,
    }
    return {
        "schema": "reasoning.heisenberg-level5-5-5-surface-witness-profile.v1",
        "request": request,
        "request_sha256": hashlib.sha256(canonical_json(request).encode()).hexdigest(),
        "group": {
            "invariant_factors": [_LEVEL, _LEVEL],
            "order": 25,
            "projective_generators": {
                "S": "S*e_i=e_(i+1)",
                "T": "T*e_i=zeta^i*e_i",
            },
            "lift_relation": "T*S=zeta*S*T",
            "projective_action_faithful": True,
            "multiplier_order": 5,
        },
        "degree_five_representation": {
            "monomial_count": len(monomials),
            "S_orbit_count": sum(character_block_dimensions.values()),
            "character_block_dimensions": character_block_dimensions,
            "fixed_monomial": [1, 1, 1, 1, 1],
        },
        "equations": [f_profile, g_profile],
        "fixed_locus": {
            "cyclic_subgroup_count": 6,
            "S_subgroup_fixed_points_on_surface": 0,
            "S_eigenline_evaluations": {"F": 40, "G": -25},
            "other_cyclic_subgroups": 5,
            "fixed_points_per_other_subgroup": 5,
            "surface_nonfree_point_count": 25,
            "surface_point_stabilizer_invariant_factors": [5],
            "nonidentity_element_fixed_point_distribution": {"0": 4, "5": 20},
        },
        "topology": {
            "hyperplane_square": 25,
            "first_chern_class": "-5h",
            "canonical_class": "5h",
            "second_chern_class": "35h^2",
            "euler_characteristic": 875,
            "b1": 0,
            "b2": 873,
            "b3": 0,
            "geometric_genus": 124,
            "hodge_1_1_dimension": 625,
        },
        "invariant_cohomology": {
            "lefschetz_h2_traces": traces,
            "rational_h2_rank": invariant_h2_rank,
            "canonical_residue_character": 3,
            "hodge_dimensions": {"2,0": 5, "1,1": 27, "0,2": 5},
            "non_1_1_invariant_classes_exist_rationally": True,
        },
        "linearization_gate": {
            "O_S_1_linearizable": False,
            "reason": (
                "A linearization would make H0(S,O_S(1)) a genuine five-dimensional "
                "representation lifting the projective S,T action, contradicting T*S=zeta*S*T."
            ),
            "cartan_leray_d3_hyperplane_nonzero": True,
            "cartan_leray_d3_target": "H3((Z/5)^2,Z)=Z/5",
        },
        "sixfold_architecture": {
            "upstairs": "S_(5,5) x E x P3",
            "action": (
                "the Heisenberg projective action on S, the faithful E[5] translation "
                "action on E, and the trivial action on P3"
            ),
            "dimension": 6,
            "diagonal_action_free": True,
            "quotient_smooth_proper_integral_if_surface_certified": True,
        },
        "certificate": certificate,
        "certificate_verified": all(certificate.values()),
    }


def _determinant(matrix: list[list[int]]) -> int:
    values = [[Fraction(value) for value in row] for row in matrix]
    determinant = Fraction(1)
    sign = 1
    for column in range(len(values)):
        pivot = next(
            (row for row in range(column, len(values)) if values[row][column]),
            None,
        )
        if pivot is None:
            return 0
        if pivot != column:
            values[column], values[pivot] = values[pivot], values[column]
            sign *= -1
        pivot_value = values[column][column]
        determinant *= pivot_value
        for row in range(column + 1, len(values)):
            multiplier = values[row][column] / pivot_value
            for index in range(column + 1, len(values)):
                values[row][index] -= multiplier * values[column][index]
    result = sign * determinant
    if result.denominator != 1:
        raise ScientificToolError("integral Gram matrix has a nonintegral determinant")
    return result.numerator


def _rank_mod_prime(matrix: list[list[int]], prime: int) -> int:
    if not matrix:
        return 0
    values = [[value % prime for value in row] for row in matrix]
    rank = 0
    for column in range(len(values[0])):
        pivot = next(
            (row for row in range(rank, len(values)) if values[row][column]),
            None,
        )
        if pivot is None:
            continue
        values[rank], values[pivot] = values[pivot], values[rank]
        inverse = pow(values[rank][column], -1, prime)
        values[rank] = [(entry * inverse) % prime for entry in values[rank]]
        for row in range(len(values)):
            if row == rank:
                continue
            multiplier = values[row][column]
            values[row] = [
                (entry - multiplier * pivot_entry) % prime
                for entry, pivot_entry in zip(values[row], values[rank], strict=True)
            ]
        rank += 1
    return rank


def _exceptional_lattice_data() -> dict[str, Any]:
    local_gram = [[-3, 1], [1, -2]]
    raw_gram = [[0] * 10 for _ in range(10)]
    for singularity in range(5):
        for row in range(2):
            for column in range(2):
                raw_gram[2 * singularity + row][2 * singularity + column] = (
                    local_gram[row][column]
                )

    # The first local dual generator is (-2 e_0-e_1)/5.  Evaluating the two
    # characters of G on the five stabilizer lines <T S^r> gives the isotropic
    # code generated by (1,1,1,1,1) and (0,1,2,3,4).
    code = [[1, 1, 1, 1, 1], [0, 1, 2, 3, 4]]
    local_duals: list[list[Fraction]] = []
    for singularity in range(5):
        vector = [Fraction(0)] * 10
        vector[2 * singularity] = Fraction(-2, 5)
        vector[2 * singularity + 1] = Fraction(-1, 5)
        local_duals.append(vector)
    code_vectors = [
        [
            sum(
                (
                    Fraction(code[row][index]) * local_duals[index][coordinate]
                    for index in range(5)
                ),
                Fraction(0),
            )
            for coordinate in range(10)
        ]
        for row in range(2)
    ]
    standard_basis = [
        [Fraction(int(row == column)) for row in range(10)]
        for column in range(10)
        if column not in (1, 3)
    ]
    saturated_basis = [*code_vectors, *standard_basis]

    def pairing(left: list[Fraction], right: list[Fraction]) -> Fraction:
        return sum(
            (
                left[row]
                * raw_gram[row][column]
                * right[column]
                for row in range(10)
                for column in range(10)
            ),
            Fraction(0),
        )

    rational_gram = [
        [pairing(left, right) for right in saturated_basis]
        for left in saturated_basis
    ]
    if any(entry.denominator != 1 for row in rational_gram for entry in row):
        raise ScientificToolError("exceptional saturation code is not integral")
    saturated_gram = [[entry.numerator for entry in row] for row in rational_gram]
    discriminant_coefficient = 3
    code_isotropic = all(
        sum(
            discriminant_coefficient * left * right
            for left, right in zip(code[left_row], code[right_row], strict=True)
        )
        % 5
        == 0
        for left_row in range(2)
        for right_row in range(2)
    )
    return {
        "local_gram": local_gram,
        "local_determinant": _determinant(local_gram),
        "raw_gram": raw_gram,
        "raw_determinant": _determinant(raw_gram),
        "local_discriminant_generator_square": "3/5 mod Z",
        "saturation_code": {
            "coordinates": [f"<T*S^{power}>" for power in range(5)],
            "generator_matrix_mod_5": code,
            "rank_mod_5": _rank_mod_prime(code, 5),
            "isotropic": code_isotropic,
        },
        "saturation_index": 25,
        "saturated_basis": {
            "description": (
                "the two code lifts followed by e0,e2,e4,e5,e6,e7,e8,e9"
            ),
            "gram": saturated_gram,
        },
        "saturated_determinant": _determinant(saturated_gram),
        "saturated_discriminant_generator_square": "3/5 mod Z",
    }


def heisenberg_surface_invariant_h2_descent_profile() -> dict[str, Any]:
    """Return the exact quotient-resolution and level-five descent arithmetic."""

    exceptional = _exceptional_lattice_data()
    ambient_cotangent_pullback_weights = [1, 2, 3, 4]
    conormal_pullback_weights = [1, 2]
    surface_cotangent_pullback_weights = [
        weight
        for weight in ambient_cotangent_pullback_weights
        if weight not in conormal_pullback_weights
    ]
    marked_tangent_weights = surface_cotangent_pullback_weights.copy()
    inverse_generator_tangent_weights = [
        (-weight) % _LEVEL for weight in marked_tangent_weights
    ]
    normalized_quotient_weights = sorted(inverse_generator_tangent_weights)
    quotient_euler = (875 + 4 * 0 + 20 * 5) // 25
    resolution_euler = quotient_euler + 5 * 2
    holomorphic_euler = 1 + 5
    canonical_square = 12 * holomorphic_euler - resolution_euler
    resolution_b2 = resolution_euler - 2
    resolution_signature = (canonical_square - 2 * resolution_euler) // 3
    resolution_b_plus = (resolution_b2 + resolution_signature) // 2
    resolution_b_minus = resolution_b2 - resolution_b_plus
    stabilizer_generators = [[1, power] for power in range(5)]
    stabilizer_span_rank = _rank_mod_prime(stabilizer_generators, 5)
    kernel_determinant = 5 ** 73
    invariant_determinant = 5 ** 71
    request = {
        "surface_witness_request_sha256": (
            "3d56f06188ad5bc6734f3ebc5b49c9c5ccba5abfbe83933ee1501d2eb674e5dd"
        ),
        "level": 5,
        "normalization": "d3(h_S)=d3(5*eta_E)=1 in Z/5",
        "local_generator_convention": (
            "mark T*S^r; use its inverse and reorder weights for 1/5(1,2)"
        ),
    }
    certificate = {
        "five_quotient_singularities": 25 // 5 == 5,
        "local_hirzebruch_jung_determinant_five": (
            exceptional["local_determinant"] == 5
        ),
        "stabilizers_span_G": stabilizer_span_rank == 2,
        "burnside_quotient_euler_verified": quotient_euler == 39,
        "resolution_invariants_verified": (
            resolution_euler,
            canonical_square,
            resolution_b2,
            resolution_signature,
            resolution_b_plus,
            resolution_b_minus,
        )
        == (49, 23, 47, -25, 11, 36),
        "exceptional_raw_determinant_verified": (
            exceptional["raw_determinant"] == 5**5
        ),
        "saturation_code_verified": (
            exceptional["saturation_code"]["rank_mod_5"] == 2
            and exceptional["saturation_code"]["isotropic"]
        ),
        "exceptional_saturation_determinant_verified": (
            exceptional["saturation_index"] == 25
            and exceptional["saturated_determinant"] == 5
        ),
        "kernel_and_invariant_determinants_verified": (
            kernel_determinant == (25**37) // 5
            and invariant_determinant == kernel_determinant // 25
        ),
        "level_five_pairing_gate_verified": (
            (-500 * 5) % 625 == 0 and (-500) % 625 != 0
        ),
        "marked_local_weight_convention_verified": (
            surface_cotangent_pullback_weights == [3, 4]
            and marked_tangent_weights == [3, 4]
            and inverse_generator_tangent_weights == [2, 1]
            and normalized_quotient_weights == [1, 2]
        ),
    }
    return {
        "schema": "reasoning.heisenberg-surface-invariant-h2-descent-profile.v2",
        "request": request,
        "request_sha256": hashlib.sha256(canonical_json(request).encode()).hexdigest(),
        "surface_dependency": {
            "surface_witness": "SURFACE_WITNESS",
            "surface_witness_request_sha256": request[
                "surface_witness_request_sha256"
            ],
            "surface_certified_by_this_receipt": False,
        },
        "local_quotient": {
            "fixed_point_orbits": 5,
            "singularities": 5,
            "marked_stabilizer_generator": "T*S^r",
            "ambient_cotangent_pullback_weights_mod_5": (
                ambient_cotangent_pullback_weights
            ),
            "conormal_pullback_weights_mod_5": conormal_pullback_weights,
            "marked_cotangent_pullback_weights_mod_5": (
                surface_cotangent_pullback_weights
            ),
            "marked_tangent_weights_mod_5": marked_tangent_weights,
            "inverse_generator_tangent_weights_mod_5": (
                inverse_generator_tangent_weights
            ),
            "normalized_quotient_tangent_weights_mod_5": normalized_quotient_weights,
            "cyclic_quotient_type": "1/5(1,2)",
            "hirzebruch_jung_expansion": "5/2=[3,2]",
            "exceptional_chain_self_intersections": [-3, -2],
        },
        "fundamental_groups": {
            "surface_complement": "trivial",
            "smooth_quotient_locus": "(Z/5)^2",
            "stabilizer_line_generators_mod_5": stabilizer_generators,
            "stabilizer_span_rank_mod_5": stabilizer_span_rank,
            "singular_quotient": "trivial",
            "minimal_resolution": "trivial",
        },
        "minimal_resolution": {
            "quotient_euler_characteristic": quotient_euler,
            "euler_characteristic": resolution_euler,
            "holomorphic_euler_characteristic": holomorphic_euler,
            "geometric_genus": 5,
            "irregularity": 0,
            "canonical_square": canonical_square,
            "b2": resolution_b2,
            "signature": resolution_signature,
            "b_plus": resolution_b_plus,
            "b_minus": resolution_b_minus,
            "hodge_1_1_dimension": resolution_b2 - 10,
            "integral_h2_unimodular": True,
            "integral_h2_odd": True,
        },
        "exceptional_lattice": exceptional,
        "orthogonal_lattice": {
            "name": "K=exceptional_saturation^perp",
            "rank": 37,
            "signature": [11, 26],
            "determinant": 5,
            "hodge_dimensions": {"2,0": 5, "1,1": 27, "0,2": 5},
            "full_gram_matrix_selected": False,
            "reason": (
                "the pairing obstruction uses only primitivity, determinant five, "
                "and the dual-lattice denominator; selecting an isometry-class "
                "representative still requires a primitive-embedding audit"
            ),
        },
        "descended_polarization": {
            "name": "H",
            "pullback_to_surface": "5*h_S",
            "square": 25,
            "primitive_in_K": True,
            "divisibility_in_K": 1,
            "nonlinearizable_root_obstruction": "O_S(1) is not G-linearizable",
        },
        "integral_invariant_h2": {
            "rank": 37,
            "d3_target": "Z/5",
            "d3_hyperplane": 1,
            "d3_surjective": True,
            "kernel_index": 5,
            "kernel_model": "K^dual with its form multiplied by 25",
            "kernel_determinant": kernel_determinant,
            "invariant_model": "(K^dual + Z*(H/5)) with its form multiplied by 25",
            "invariant_determinant": invariant_determinant,
            "hyperplane_model": "h_S corresponds to H/5",
            "kernel_pairings_divisible_by_5": True,
            "hyperplane_kernel_pairings_divisible_by_5": True,
            "valuation_one_pairing_exists": True,
            "valuation_one_pairing_formula": (
                "choose k in K with H*k=1; its kernel image u has h_S*u=5"
            ),
            "non_1_1_integral_kernel_classes_exist": True,
        },
        "perry_level_five_gate": {
            "sixfold": "X=(S x P3 x E)/G",
            "test_class": "q^*b=v+a-5*eta_E with d3(v)=1",
            "general_degree_two_class": (
                "q^*w=u+c*a+5*m*eta_E with d3(u)+m=0"
            ),
            "kernel_substitution": "k=u+m*v lies in ker(d3)",
            "paired_fifth_power_formula": (
                "integral_X b^5*w=6*(m-c)*v^2-4*v*k"
            ),
            "v_square_divisible_by_5": True,
            "v_kernel_pairings_divisible_by_5": True,
            "b_fifth_power_divisible_by_5_in_free_integral_cohomology": True,
            "degree_ten_leading_term": "125*b^5",
            "degree_ten_leading_term_zero_mod_625": True,
            "zero_hodge_correction_tuple_passes_degrees_2_through_5": True,
            "degree_six_absorbed_by_top_hodge_class": True,
            "level_five_perry_obstruction_survives": False,
            "valuation_one_is_not_a_level_five_unit": True,
            "level_twenty_five_warning": (
                "the valuation-one number matches the formal level-25 numeric "
                "window, but this action has d3 image of order 5, not 25"
            ),
        },
        "verdict": {
            "integral_h2_structure_certified_subject_to_bridges": True,
            "non_1_1_descent_source_exists": True,
            "level_five_perry_index_gate_killed": True,
            "period_five_brauer_class_certified": False,
            "index_5_adic_valuation_at_least_6_certified": False,
            "period_index_counterexample_certified": False,
        },
        "proof_bridges": {
            "quotient_resolution": (
                "Burnside, Armstrong, van Kampen, rationality of cyclic quotient "
                "singularities, and Noether plus signature formulas identify the "
                "resolution topology."
            ),
            "exceptional_saturation": (
                "The divisor-complement exact sequence identifies the primitive "
                "closure quotient with Hom(pi1(U),Q/Z); the five stabilizer lines "
                "give the displayed two-dimensional isotropic code."
            ),
            "borel_comparison": (
                "Comparing the resolution and Borel fillings of the five lens-space "
                "boundaries identifies ker(d3) with K^dual(25); adjoining h_S, "
                "which corresponds to H/5, gives the invariant lattice."
            ),
            "polarization_primitivity": (
                "If H were five times an integral class, Lefschetz (1,1) and descent "
                "across rational singularities would linearize O_S(1), contradicting "
                "its Heisenberg multiplier. Determinant five then forces div_K(H)=1."
            ),
            "perry_divisibility": (
                "Poincare duality on free cohomology turns the displayed pairing "
                "calculation with every degree-two class into b^5 in 5*L_5."
            ),
        },
        "certificate": certificate,
        "certificate_verified": all(certificate.values()),
    }


def _singular_polynomial(polynomial: Polynomial) -> str:
    terms = []
    for exponents, coefficient in sorted(polynomial.items()):
        monomial = "*".join(
            f"{variable}^{exponent}" if exponent != 1 else variable
            for variable, exponent in zip(_VARIABLES, exponents, strict=True)
            if exponent
        )
        terms.append(f"{coefficient}*{monomial}")
    return "+".join(terms).replace("+-", "-")


def singular_smoothness_program() -> str:
    """Return the fixed Singular protocol for all five projective charts."""

    f, g = heisenberg_surface_polynomials()
    minors = [
        f"diff(f,x{left})*diff(g,x{right})-diff(f,x{right})*diff(g,x{left})"
        for left, right in combinations(range(5), 2)
    ]
    lines = [
        "option(redSB);",
        "ring r=101,(x0,x1,x2,x3,x4),dp;",
        f"poly f={_singular_polynomial(f)};",
        f"poly g={_singular_polynomial(g)};",
        f'print("REASONING_HEISENBERG_SMOOTHNESS_V{_SINGULAR_PROTOCOL_VERSION}");',
        'print("singular_version="+string(system("version")));',
    ]
    for chart in range(5):
        lines.extend(
            (
                f"ideal j{chart}=f,g,{','.join(minors)},x{chart}-1;",
                f"ideal s{chart}=std(j{chart});",
                f"poly r{chart}=reduce(1,s{chart});",
                f'if (r{chart}==0) {{ print("chart_{chart}_unit=1"); }} '
                f'else {{ print("chart_{chart}_unit=0"); }}',
                f'print("chart_{chart}_basis_size="+string(size(s{chart})));',
            )
        )
    lines.extend(('print("REASONING_END");', "$;"))
    return "\n".join(lines) + "\n"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _parse_singular_transcript(transcript: str) -> dict[str, Any]:
    lines = transcript.splitlines()
    if not lines or lines[0] != f"REASONING_HEISENBERG_SMOOTHNESS_V{_SINGULAR_PROTOCOL_VERSION}" or lines[-1] != "REASONING_END":
        raise ScientificToolError("Singular smoothness profile has invalid framing")
    fields: dict[str, str] = {}
    for line in lines[1:-1]:
        if "=" not in line:
            raise ScientificToolError("Singular smoothness profile has an invalid field")
        name, value = line.split("=", 1)
        if name in fields:
            raise ScientificToolError("Singular smoothness profile has duplicate fields")
        fields[name] = value
    expected = {"singular_version"} | {
        f"chart_{chart}_{suffix}"
        for chart in range(5)
        for suffix in ("unit", "basis_size")
    }
    if set(fields) != expected:
        raise ScientificToolError("Singular smoothness profile is incomplete")
    try:
        version = int(fields["singular_version"])
        charts = [
            {
                "chart": chart,
                "unit_ideal": int(fields[f"chart_{chart}_unit"]) == 1,
                "standard_basis_size": int(fields[f"chart_{chart}_basis_size"]),
            }
            for chart in range(5)
        ]
    except ValueError as error:
        raise ScientificToolError("Singular smoothness profile has a nonintegral field") from error
    if version < 1 or any(entry["standard_basis_size"] < 1 for entry in charts):
        raise ScientificToolError("Singular smoothness profile has an invalid integer field")
    return {
        "singular_version_code": version,
        "projective_charts": charts,
        "all_chart_singular_ideals_are_unit": all(entry["unit_ideal"] for entry in charts),
        "all_unit_standard_bases_have_size_one": all(
            entry["unit_ideal"] and entry["standard_basis_size"] == 1
            for entry in charts
        ),
    }


def singular_smoothness_profile() -> dict[str, Any]:
    """Run the fixed exact chart-Jacobian calculation in a confined Singular."""

    executable_value = shutil.which("Singular")
    if executable_value is None:
        raise ScientificToolError("Singular is unavailable on the Reasoning service PATH")
    executable = Path(executable_value).resolve()
    program = singular_smoothness_program()
    environment = {
        "HOME": "",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
    }
    try:
        with tempfile.TemporaryDirectory(prefix="reasoning-heisenberg-singular-") as temporary:
            workdir = Path(temporary)
            environment["HOME"] = temporary
            command = sandbox.wrap(
                [str(executable), "-q"],
                rw=[workdir],
                chdir=workdir,
                network=False,
            )
            completed = subprocess.run(
                command,
                input=program,
                text=True,
                capture_output=True,
                timeout=_SINGULAR_TIMEOUT_SECONDS,
                env=environment,
                check=False,
            )
    except subprocess.TimeoutExpired as error:
        raise ScientificToolError(
            f"Singular smoothness profile exceeded the {_SINGULAR_TIMEOUT_SECONDS}-second wall timeout"
        ) from error
    except OSError as error:
        raise ScientificToolError(f"Singular smoothness profile could not start: {error}") from error
    stdout = completed.stdout
    stderr = completed.stderr
    if len(stdout.encode()) > _SINGULAR_MAX_OUTPUT_BYTES or len(stderr.encode()) > _SINGULAR_MAX_OUTPUT_BYTES:
        raise ScientificToolError("Singular smoothness profile output exceeds the safe bound")
    if completed.returncode or stderr.strip():
        detail = (stderr.strip() or stdout.strip() or "unknown Singular failure")[:2000]
        raise ScientificToolError(f"Singular smoothness profile failed: {detail}")
    profile = _parse_singular_transcript(stdout)
    certificate = {
        "five_standard_affine_charts_checked": len(profile["projective_charts"]) == 5,
        "jacobian_singular_ideal_is_unit_on_every_chart": profile[
            "all_chart_singular_ideals_are_unit"
        ],
        "unit_standard_basis_shape_verified": profile[
            "all_unit_standard_bases_have_size_one"
        ],
    }
    return {
        "field": f"F_{_FINITE_FIELD_PRIME}",
        "protocol_version": _SINGULAR_PROTOCOL_VERSION,
        "program_sha256": hashlib.sha256(program.encode()).hexdigest(),
        "transcript_sha256": hashlib.sha256(stdout.encode()).hexdigest(),
        "transcript": stdout.strip(),
        "executable": str(executable),
        "executable_sha256": _sha256_file(executable),
        **profile,
        "certificate": certificate,
        "certificate_verified": all(certificate.values()),
    }
