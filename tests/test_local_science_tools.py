import copy as copy_module
import hashlib
import json
import os
import shutil

import pytest
from jsonschema import ValidationError

from reasoning_ledger import LedgerStore
from reasoning_ledger.agents import ToolGrant, ToolRegistryError, build_project_tool_registry
from reasoning_ledger.agents.heisenberg_level25 import (
    level25_heisenberg_balanced_coefficient_pivot_profile,
    level25_heisenberg_balanced_coordinate_rank_profile,
    level25_heisenberg_balanced_five_pivot_support_jacobian_profile,
    level25_heisenberg_balanced_nonadjacent_coefficient_pivot_profile,
    level25_heisenberg_balanced_pivoted_two_block_obstruction_profile,
    level25_heisenberg_balanced_restricted_system_profile,
    level25_heisenberg_balanced_two_block_obstruction_profile,
    level25_heisenberg_balanced_two_pivot_two_block_obstruction_profile,
    level25_heisenberg_character_capacity_search_profile,
    level25_heisenberg_nonadjacent_block_shape_cover_profile,
    level25_heisenberg_surface_character_lift_profile,
    level25_heisenberg_surface_order5_jacobian_capacity_profile,
    level25_heisenberg_surface_restricted_system_profile,
    level25_heisenberg_surface_sparse_global_obstruction_profile,
)
from reasoning_ledger.agents.heisenberg_level25_alphabet import level25_heisenberg_balanced_five_pivot_pure_power_alphabet_profile
from reasoning_ledger.agents.heisenberg_level25_four_block import level25_heisenberg_augmented_four_block_unary_convolution_profile
from reasoning_ledger.agents.heisenberg_level25_four_block_ad5 import level25_heisenberg_augmented_four_block_bounded_ad5_profile
from reasoning_ledger.agents.heisenberg_level25_four_block_ad5_batch import (
    level25_heisenberg_augmented_four_block_bounded_ad5_context_batch_profile,
)
from reasoning_ledger.agents.heisenberg_level25_four_block_ad5_central_key import (
    level25_heisenberg_augmented_four_block_ad5_coordinate3_central_key_profile,
)
from reasoning_ledger.agents.heisenberg_level25_four_block_ad5_join import (
    level25_heisenberg_augmented_four_block_ad5_coordinate3_join_partition_covariance_profile,
)
from reasoning_ledger.agents.heisenberg_level25_four_block_ad5_orbit import (
    level25_heisenberg_augmented_four_block_ad5_coordinate3_tensor_orbit_profile,
)
from reasoning_ledger.agents.heisenberg_level25_four_block_ad5_prefix import (
    level25_heisenberg_augmented_four_block_ad5_prefix_shift_census_profile,
)
from reasoning_ledger.agents.heisenberg_level25_four_block_ad5_representative_batch import (
    level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_batch_profile,
)
from reasoning_ledger.agents.heisenberg_level25_four_block_ad5_representative_batch_9_12 import (
    level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_batch_9_12_profile,
)
from reasoning_ledger.agents.heisenberg_level25_four_block_ad5_representative_sweep import (
    _pair_receipt,
    _profile_from_execution,
    _seal,
    build_ad5_representative_macroreceipt,
    level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_batch_13_16_profile,
    validate_representative_bundle,
)
from reasoning_ledger.agents.heisenberg_level25_four_block_ad5_signature import (
    level25_heisenberg_augmented_four_block_ad5_coordinate3_functional_signature_profile,
)
from reasoning_ledger.agents.heisenberg_level25_four_block_ad5_slice import (
    level25_heisenberg_augmented_four_block_ad5_coordinate3_central_orbit_representative_preflight_profile,
)
from reasoning_ledger.agents.heisenberg_level25_four_block_row0 import level25_heisenberg_augmented_four_block_row0_feasibility_profile
from reasoning_ledger.agents.heisenberg_level25_four_block_row0_edge_indicator import (
    level25_heisenberg_augmented_four_block_row0_edge_indicator_rank_profile,
)
from reasoning_ledger.agents.heisenberg_level25_four_block_row0_kernel import (
    level25_heisenberg_augmented_four_block_row0_kernel_rank_profile,
)
from reasoning_ledger.agents.heisenberg_level25_four_block_row0_pair import (
    level25_heisenberg_augmented_four_block_row0_fixed_fiber_pair_calibration_profile,
)
from reasoning_ledger.agents.heisenberg_level25_four_block_row0_pair_reuse import (
    level25_heisenberg_augmented_four_block_row0_pair_reuse_profile,
)
from reasoning_ledger.agents.heisenberg_level25_four_block_row0_rank import (
    level25_heisenberg_augmented_four_block_row0_label_unary_rank_profile,
)
from reasoning_ledger.agents.heisenberg_level25_singleton_orbits import level25_heisenberg_fixed_stratum_singleton_orbit_profile
from reasoning_ledger.agents.heisenberg_level25_three_block import (
    level25_heisenberg_balanced_five_pivot_nonconsecutive_three_block_rational_profile,
)
from reasoning_ledger.agents.heisenberg_level25_three_block_consecutive import (
    level25_heisenberg_balanced_five_pivot_consecutive_three_block_rational_profile,
)
from reasoning_ledger.agents.heisenberg_level25_three_block_coverage import (
    level25_heisenberg_augmented_fixed_and_cyclic_local_profile,
    level25_heisenberg_augmented_four_block_alphabet_profile,
    level25_heisenberg_augmented_four_block_fourier_profile,
    level25_heisenberg_augmented_four_block_phase_quotient_profile,
    level25_heisenberg_augmented_three_block_rational_profile,
    level25_heisenberg_singleton_orbit_three_block_coverage_profile,
)
from reasoning_ledger.agents.heisenberg_level25_two_block import level25_heisenberg_balanced_five_pivot_two_block_rational_profile
from reasoning_ledger.agents.heisenberg_surface import (
    heisenberg_surface_invariant_h2_descent_profile,
    heisenberg_surface_witness_profile,
    singular_smoothness_profile,
)
from reasoning_ledger.canonical import canonical_json


def _zero(rows, columns):
    return [[0] * columns for _ in range(rows)]


def _identity(size):
    return [[int(row == column) for column in range(size)] for row in range(size)]


def _torus_translation_cochains(level):
    cells = level * level

    def cell(x, y):
        return (y % level) * level + (x % level)

    d0 = _zero(2 * cells, cells)
    d1 = _zero(cells, 2 * cells)
    for y in range(level):
        for x in range(level):
            vertex = cell(x, y)
            horizontal = cell(x, y)
            vertical = cells + cell(x, y)
            d0[horizontal][vertex] = -1
            d0[horizontal][cell(x + 1, y)] = 1
            d0[vertical][vertex] = -1
            d0[vertical][cell(x, y + 1)] = 1
            d1[cell(x, y)][horizontal] = 1
            d1[cell(x, y)][cells + cell(x + 1, y)] = 1
            d1[cell(x, y)][cell(x, y + 1)] = -1
            d1[cell(x, y)][vertical] = -1

    actions = []
    for dx, dy in ((1, 0), (0, 1)):
        degree_actions = []
        for copies in (1, 2, 1):
            rank = copies * cells
            action = _zero(rank, rank)
            for copy in range(copies):
                for y in range(level):
                    for x in range(level):
                        source = copy * cells + cell(x, y)
                        target = copy * cells + cell(x + dx, y + dy)
                        action[target][source] = 1
            degree_actions.append(action)
        degree_actions.append([])
        actions.append(degree_actions)

    h2 = [[int(index == 0)] for index in range(cells)]
    return {
        "level": level,
        "cochain_ranks": [cells, 2 * cells, cells, 0],
        "differentials": [d0, d1, []],
        "generator_actions": actions,
        "h2_cocycle_basis": h2,
    }


def test_post_s25_diagonal_profile_rejects_both_invalid_surface_families(tmp_path):
    registry = _registry(tmp_path)

    result = registry.invoke("post_s25_diagonal_candidate_profile", {})

    decic = result["candidate_a"]["profile"]
    assert decic["ambient_monomial_count"] == 286
    assert decic["character_block_count"] == 121
    assert decic["unobstructed_character_count"] == 0
    decic_zero = next(block for block in decic["character_blocks"] if block["character_modulus"] == [0, 0])
    assert decic_zero["monomial_count"] == 6
    assert decic_zero["common_monomial_factor"] == [0, 0, 0, 0]
    assert decic_zero["binary_pair_product_degree"] == 5
    assert decic_zero["smooth_irreducible_hypersurface_obstructed"] is True

    quadric = result["candidate_b"]["quadric_profile"]
    assert quadric["ambient_monomial_count"] == 15
    assert quadric["character_block_count"] == 13
    assert quadric["unobstructed_characters"] == [[0, 0]]
    quadric_zero = next(block for block in quadric["character_blocks"] if block["character_modulus"] == [0, 0])
    assert quadric_zero["monomial_exponents"] == [
        [0, 0, 0, 0, 2],
        [0, 0, 1, 1, 0],
        [1, 1, 0, 0, 0],
    ]

    quintic = result["candidate_b"]["quintic_profile"]
    assert quintic["ambient_monomial_count"] == 126
    assert quintic["character_block_count"] == 61
    assert quintic["every_character_obstructed"] is True
    quintic_zero = next(block for block in quintic["character_blocks"] if block["character_modulus"] == [0, 0])
    assert quintic_zero["common_monomial_factor"] == [0, 0, 0, 0, 1]

    assert result["schema"] == "reasoning.post-s25-diagonal-candidate-profile.v2"
    assert result["certificate"] == {
        "decic_census_verified": True,
        "quadric_census_verified": True,
        "quintic_census_verified": True,
        "only_zero_character_can_define_an_integral_quadric": True,
        "smooth_zero_character_quadric_witness_verified": True,
        "every_quintic_character_has_a_fixed_factor": True,
        "finite_group_eigenspace_reduction_in_characteristic_zero": True,
        "reducible_quadric_contradicts_primality_of_the_complete_intersection": True,
        "forced_quintic_factor_contradicts_primality_over_an_integral_quadric": True,
    }
    assert result["candidate_verdict"] == {
        "candidate_a_rejected": True,
        "candidate_b_rejected": True,
        "invariant_lattice_computation_warranted": False,
        "period_index_conjecture_disproved": False,
        "next_gate": (
            "Screen general four-coordinate diagonal representations, then five-"
            "coordinate complete intersections and abelian covers; the level-25 "
            "translation candidate is already separately scoped rejected."
        ),
    }
    assert result["certificate_verified"] is True


def test_diagonal_pair_profile_requires_unique_signed_character_lifts(tmp_path):
    registry = _registry(tmp_path)

    with pytest.raises(ValueError, match="exceed twice the degree"):
        registry.invoke(
            "diagonal_pair_action_linear_system_profile",
            {"modulus": 20, "degree": 10, "zero_weight_coordinates": 0},
        )


def test_level25_diagonal_p3_filter_exhausts_all_liftable_patterns(tmp_path):
    registry = _registry(tmp_path)

    result = registry.invoke(
        "level25_diagonal_p3_hypersurface_filter_profile",
        {},
    )

    assert result["schema"] == ("reasoning.level25-diagonal-p3-hypersurface-filter-profile.v1")
    assert result["degree_residues_mod_25"] == [5, 10, 15, 20]
    assert result["relation_pattern_count"] == 256
    assert result["relation_pattern_census_sha256"] == ("939ee023fe301ec707f2ee5167a65450b6612ef66a65ae0a3f710f05fb3aaf52")
    assert result["degree_profiles"] == [
        {
            "degree_residue_mod_25": degree,
            "liftable_residue_solution_count": 1_280,
            "liftable_solution_count_distribution": {"5": 256},
            "violating_pattern_count": 0,
        }
        for degree in [5, 10, 15, 20]
    ]
    assert result["counterexamples"] == []
    assert result["all_coordinate_weight_differences_divisible_by_5"] is True
    assert result["projective_action_consequence"] == {
        "kernel_contains": "5*(Z/25)^2",
        "kernel_order_lower_bound": 25,
        "image_exponent_upper_bound": 5,
        "faithful_action_obstructed": True,
    }
    assert result["certificate_verified"] is True


def test_level25_diagonal_p3_filter_report_writes_canonical_receipt(tmp_path):
    registry = _registry(tmp_path)

    result = registry.invoke(
        "level25_diagonal_p3_hypersurface_filter_report",
        {
            "path": "research/level25-diagonal-p3-filter.md",
            "terminal_marker": "LEVEL25_DIAGONAL_P3_FILTER_COMPLETE",
        },
    )

    artifact = tmp_path / "artifacts/research/level25-diagonal-p3-filter.md"
    text = artifact.read_text(encoding="utf-8")
    assert result["receipt"]["all_coordinate_weight_differences_divisible_by_5"] is True
    assert result["receipt_sha256"] in text
    assert result["certificate_verified"] is True
    assert text.endswith("LEVEL25_DIAGONAL_P3_FILTER_COMPLETE\n")


def test_level25_diagonal_2_5_filter_exhausts_quadric_ranks(tmp_path):
    registry = _registry(tmp_path)

    result = registry.invoke(
        "level25_diagonal_2_5_complete_intersection_filter_profile",
        {},
    )

    assert result["schema"] == ("reasoning.level25-diagonal-2-5-complete-intersection-filter-profile.v1")
    assert result["rank_at_most_three"]["obstructed"] is True
    assert result["rank_four"] == {
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
        "vertex_choice_count": 256,
        "relation_matrix_kernel_mod_5_count_distribution": {
            "1": 97,
            "5": 124,
            "25": 34,
            "125": 1,
        },
        "exact_mod_25_solution_count_distribution": {
            "1": 97,
            "5": 124,
            "25": 34,
            "125": 1,
        },
        "liftable_mod_5_residue_count_distribution": {"1": 256},
        "liftable_mod_5_residue_span_rank_distribution": {"0": 256},
        "pattern_census_sha256": ("e3c286d52442decdf65fad3d3de690392394dcf9b9fec7b5eac6823241f69ea4"),
        "faithful_pattern_count": 0,
        "faithful_patterns": [],
        "obstructed": True,
    }
    assert result["rank_five"]["paired_candidate_sha256"] == ("b8af94d026b548ab07ffcfafb80b28531cc03eedfada984f06533738f9681de5")
    paired = registry.invoke("post_s25_diagonal_candidate_profile", {})
    assert result["rank_five"]["paired_candidate_schema"] == paired["schema"]
    assert result["rank_five"]["paired_candidate_sha256"] == hashlib.sha256(canonical_json(paired["candidate_b"]).encode()).hexdigest()
    assert result["rank_five"]["obstructed"] is True
    assert result["faithful_surface_witness_count"] == 0
    assert result["faithful_diagonal_action_obstructed"] is True
    assert result["period_index_conjecture_disproved"] is False
    assert result["certificate_verified"] is True


def test_level25_diagonal_2_5_filter_report_writes_canonical_receipt(tmp_path):
    registry = _registry(tmp_path)

    result = registry.invoke(
        "level25_diagonal_2_5_complete_intersection_filter_report",
        {
            "path": "research/level25-diagonal-2-5-filter.md",
            "terminal_marker": "LEVEL25_DIAGONAL_2_5_FILTER_COMPLETE",
        },
    )

    artifact = tmp_path / "artifacts/research/level25-diagonal-2-5-filter.md"
    text = artifact.read_text(encoding="utf-8")
    assert result["receipt"]["faithful_diagonal_action_obstructed"] is True
    assert result["receipt_sha256"] in text
    assert result["certificate_verified"] is True
    assert text.endswith("LEVEL25_DIAGONAL_2_5_FILTER_COMPLETE\n")


def test_level25_heisenberg_character_lift_profile_checks_all_thirty_lifts():
    result = level25_heisenberg_surface_character_lift_profile()

    assert result["schema"] == ("reasoning.level25-heisenberg-surface-character-lift-profile.v1")
    assert result["zero_reduction_character_count"] == 4
    assert result["effective_count_sum"] == 42
    assert result["effective_count_sum_formula"] == 42
    assert [row["effective_character_count"] for row in result["order_five_subgroups"]] == [4, 8, 8, 9, 4, 9]
    assert sum(len(row["cyclic_lifts"]) for row in result["order_five_subgroups"]) == 30
    h0 = result["order_five_subgroups"][0]
    assert all(row["forced_eigenpoints"] for row in h0["cyclic_lifts"])
    assert all(row["character_jacobian_rank_permitted_if_forced"] for row in h0["cyclic_lifts"])
    assert all(not lift["forced_eigenpoints"] for subgroup in result["order_five_subgroups"][1:] for lift in subgroup["cyclic_lifts"])
    assert result["native_z2_census_rejection"]["rejected"] is True
    assert result["conditional_z3_h0_forced_lift_obstruction"]["uncovered_lifts_with_weight_collision"] == 3
    assert result["certificate_verified"] is True


def test_level25_heisenberg_character_lift_report_is_canonical(tmp_path):
    registry = _registry(tmp_path)
    result = registry.invoke(
        "level25_heisenberg_surface_character_lift_report",
        {
            "path": "research/level25-heisenberg-character-lifts.md",
            "terminal_marker": "LEVEL25_HEISENBERG_CHARACTER_LIFTS_COMPLETE",
        },
    )

    artifact = tmp_path / "artifacts/research/level25-heisenberg-character-lifts.md"
    text = artifact.read_text(encoding="utf-8")
    assert result["receipt"]["certificate_verified"] is True
    assert result["receipt_sha256"] in text
    assert text.endswith("LEVEL25_HEISENBERG_CHARACTER_LIFTS_COMPLETE\n")


def test_level25_heisenberg_restricted_system_profile_checks_fixed_strata():
    result = level25_heisenberg_surface_restricted_system_profile()

    assert result["schema"] == ("reasoning.level25-heisenberg-surface-restricted-system-profile.v1")
    assert result["form_definition"]["ambient_form_count"] == 22
    assert result["form_definition"]["ambient_sparse_term_count"] == 550
    assert len(result["fixed_p4_systems"]) == 30
    assert result["dimension_census_by_subgroup"] == [
        [1, 1, 1, 1, 1],
        [0, 0, 0, 0, 0],
        [0, 0, 0, 0, 0],
        [0, 0, 0, 0, 0],
        [1, 1, 1, 1, 1],
        [0, 0, 0, 0, 0],
    ]
    assert all(
        row["projective_singular_locus"] == "empty" and row["projective_degree"] == 25**4
        for row in result["fixed_p4_systems"]
        if row["restricted_form_count"] == 4
    )
    assert all(
        row["nonzero_equation_value_count"] == 0
        and row["jacobian_rank_distribution"] == {"22": 25}
        and row["projective_stabilizer_order_distribution"] == {"25": 25}
        and row["unexpected_projective_stabilizer_count"] == 0
        for row in result["forced_h0_eigenpoints"]
    )
    assert len(result["all_cyclic_eigenpoint_evaluations"]) == 30
    assert sum(row["common_zero_count"] for row in result["all_cyclic_eigenpoint_evaluations"]) == 125
    assert [row["marked_generator_tangent_weights"] for row in result["forced_h0_eigenpoints"]] == [
        [23, 24],
        [3, 4],
        [8, 9],
        [13, 14],
        [18, 19],
    ]
    assert [row["unmarked_cyclic_quotient_type"] for row in result["forced_h0_eigenpoints"]] == [
        "1/25(1,2)",
        "1/25(1,7)",
        "1/25(1,12)",
        "1/25(1,3)",
        "1/25(1,8)",
    ]
    assert result["singular"]["singular_version_code"] >= 1
    assert result["certificate_verified"] is True
    assert any("global" in item for item in result["not_certified"])


def test_level25_heisenberg_restricted_system_report_is_canonical(tmp_path):
    registry = _registry(tmp_path)
    result = registry.invoke(
        "level25_heisenberg_surface_restricted_system_report",
        {
            "path": "research/level25-heisenberg-restricted-systems.md",
            "terminal_marker": "LEVEL25_HEISENBERG_RESTRICTED_SYSTEMS_COMPLETE",
        },
    )

    artifact = tmp_path / "artifacts/research/level25-heisenberg-restricted-systems.md"
    text = artifact.read_text(encoding="utf-8")
    assert result["receipt"]["certificate_verified"] is True
    assert result["receipt_sha256"] in text
    assert text.endswith("LEVEL25_HEISENBERG_RESTRICTED_SYSTEMS_COMPLETE\n")


def test_level25_heisenberg_sparse_global_obstruction_finds_coordinate_p4():
    result = level25_heisenberg_surface_sparse_global_obstruction_profile()

    assert result["schema"] == ("reasoning.level25-heisenberg-surface-sparse-global-obstruction-profile.v1")
    witness = result["coordinate_subspace_witness"]
    assert witness["support"] == [0, 2, 4, 6, 8]
    assert witness["nonzero_restricted_equation_count"] == 1
    assert witness["common_zero_projective_dimension"] == 3
    assert [row["character"] for row in witness["restricted_system"] if not row["restriction_is_zero"]] == [[1, 0]]
    assert result["admissible_support_census"]["count"] == 1025
    assert result["certificate_verified"] is True
    assert result["verdict"] == "explicit sparse form family rejected"
    assert any("generic" in item for item in result["not_implied"])


def test_level25_heisenberg_sparse_global_obstruction_report_is_canonical(
    tmp_path,
):
    registry = _registry(tmp_path)
    result = registry.invoke(
        "level25_heisenberg_surface_sparse_global_obstruction_report",
        {
            "path": "research/level25-heisenberg-sparse-obstruction.md",
            "terminal_marker": "LEVEL25_HEISENBERG_SPARSE_OBSTRUCTION_COMPLETE",
        },
    )

    artifact = tmp_path / "artifacts/research/level25-heisenberg-sparse-obstruction.md"
    text = artifact.read_text(encoding="utf-8")
    assert result["receipt"]["certificate_verified"] is True
    assert result["receipt_sha256"] in text
    assert text.endswith("LEVEL25_HEISENBERG_SPARSE_OBSTRUCTION_COMPLETE\n")


def test_level25_heisenberg_order5_jacobian_capacity_rejects_current_census():
    result = level25_heisenberg_surface_order5_jacobian_capacity_profile()

    assert result["schema"] == ("reasoning.level25-heisenberg-surface-order5-jacobian-capacity-profile.v1")
    rows = result["order_five_subgroups"]
    assert [row["equation_restriction_weight_counts"] for row in rows] == [
        [4, 5, 5, 4, 4],
        [8, 5, 0, 5, 4],
        [8, 9, 0, 0, 5],
        [9, 9, 4, 0, 0],
        [4, 10, 4, 4, 0],
        [9, 13, 0, 0, 0],
    ]
    assert rows[0]["maximum_jacobian_rank_at_a_common_zero"] == 22
    assert rows[4]["effective_restricted_equation_count"] == 4
    assert rows[4]["common_projective_zero_forced_by_height"] is True
    assert rows[4]["maximum_jacobian_rank_at_a_common_zero"] == 17
    assert [row["subgroup_label"] for row in rows if row["unavoidable_rank_deficient_common_zero"]] == ["H_4"]
    assert result["certificate_verified"] is True
    assert "cannot support" in result["verdict"]
    assert any("alternative" in item for item in result["not_implied"])


def test_level25_heisenberg_order5_jacobian_capacity_report_is_canonical(
    tmp_path,
):
    registry = _registry(tmp_path)
    result = registry.invoke(
        "level25_heisenberg_surface_order5_jacobian_capacity_report",
        {
            "path": "research/level25-heisenberg-order5-capacity.md",
            "terminal_marker": "LEVEL25_HEISENBERG_ORDER5_CAPACITY_COMPLETE",
        },
    )

    artifact = tmp_path / "artifacts/research/level25-heisenberg-order5-capacity.md"
    text = artifact.read_text(encoding="utf-8")
    assert result["receipt"]["certificate_verified"] is True
    assert result["receipt_sha256"] in text
    assert text.endswith("LEVEL25_HEISENBERG_ORDER5_CAPACITY_COMPLETE\n")


def test_level25_heisenberg_character_capacity_search_finds_balanced_witness():
    result = level25_heisenberg_character_capacity_search_profile()

    assert result["schema"] == ("reasoning.level25-heisenberg-character-capacity-search-profile.v1")
    assert len(result["normalization"]["admissible_reduction_patterns"]) == 5
    assert len(result["characters"]) == 22
    assert [row["effective_restricted_equation_count"] for row in result["order_five_subgroups"]] == [4, 4, 4, 4, 4, 22]
    assert all(row["maximum_jacobian_rank_at_a_common_zero"] == 22 for row in result["order_five_subgroups"][:5])
    assert [row["subgroup_label"] for row in result["uncovered_cyclic_lifts"]] == ["H_0"] * 5
    assert all(row["distinct_nonzero_if_uncovered"] for row in result["uncovered_cyclic_lifts"])
    assert result["certificate_verified"] is True
    assert "finite local gates" in result["status"]


def test_level25_heisenberg_character_capacity_search_report_is_canonical(
    tmp_path,
):
    registry = _registry(tmp_path)
    result = registry.invoke(
        "level25_heisenberg_character_capacity_search_report",
        {
            "path": "research/level25-heisenberg-capacity-search.md",
            "terminal_marker": "LEVEL25_HEISENBERG_CAPACITY_SEARCH_COMPLETE",
        },
    )

    artifact = tmp_path / "artifacts/research/level25-heisenberg-capacity-search.md"
    text = artifact.read_text(encoding="utf-8")
    assert result["receipt"]["certificate_verified"] is True
    assert result["receipt_sha256"] in text
    assert text.endswith("LEVEL25_HEISENBERG_CAPACITY_SEARCH_COMPLETE\n")


def test_level25_heisenberg_balanced_restricted_system_profile_passes_local_gates():
    result = level25_heisenberg_balanced_restricted_system_profile()

    assert result["schema"] == ("reasoning.level25-heisenberg-balanced-restricted-system-profile.v1")
    assert result["dimension_census_by_subgroup"] == [
        [1, 1, 1, 1, 1],
        [1, 1, 1, 1, 1],
        [1, 1, 1, 1, 1],
        [1, 1, 1, 1, 1],
        [1, 1, 1, 1, 1],
        [0, 0, 0, 0, 0],
    ]
    assert all(
        row["projective_singular_locus"] == "empty" and row["projective_degree"] == 25**4
        for row in result["fixed_p4_systems"]
        if row["restricted_form_count"] == 4
    )
    assert sum(row["common_zero_count"] for row in result["all_cyclic_eigenpoint_evaluations"]) == 125
    assert all(
        row["jacobian_rank_distribution"] == {"22": 25} and row["projective_stabilizer_order_distribution"] == {"25": 25}
        for row in result["forced_h0_eigenpoints"]
    )
    coordinate_screen = result["balanced_coordinate_support_screen"]
    assert coordinate_screen["support_count"] == 3125
    assert coordinate_screen["rank_histogram"] == {"5": 3125}
    assert coordinate_screen["rank_deficient_support_count"] == 0
    assert result["certificate_verified"] is True
    assert "global dimension" in result["not_certified"][0]


def test_level25_heisenberg_balanced_restricted_system_report_is_canonical(
    tmp_path,
):
    registry = _registry(tmp_path)
    result = registry.invoke(
        "level25_heisenberg_balanced_restricted_system_report",
        {
            "path": "research/level25-heisenberg-balanced-restrictions.md",
            "terminal_marker": "LEVEL25_HEISENBERG_BALANCED_RESTRICTIONS_COMPLETE",
        },
    )

    artifact = tmp_path / "artifacts/research/level25-heisenberg-balanced-restrictions.md"
    text = artifact.read_text(encoding="utf-8")
    assert result["receipt"]["certificate_verified"] is True
    assert result["receipt_sha256"] in text
    assert text.endswith("LEVEL25_HEISENBERG_BALANCED_RESTRICTIONS_COMPLETE\n")


def test_level25_heisenberg_balanced_pivoted_report_passes_local_gates(
    tmp_path,
):
    if shutil.which("Singular") is None:
        pytest.skip("Singular is unavailable")

    registry = _registry(tmp_path)
    result = registry.invoke(
        "level25_heisenberg_balanced_pivoted_restricted_system_report",
        {
            "path": "research/level25-heisenberg-balanced-pivoted.md",
            "terminal_marker": "LEVEL25_BALANCED_PIVOTED_COMPLETE",
        },
    )

    receipt = result["receipt"]
    assert receipt["schema"] == ("reasoning.level25-heisenberg-balanced-pivoted-restricted-system-profile.v1")
    assert receipt["form_definition"]["ambient_term_count"] == 575
    assert receipt["dimension_census_by_subgroup"] == [
        [1, 1, 1, 1, 1],
        [1, 1, 1, 1, 1],
        [1, 1, 1, 1, 1],
        [1, 1, 1, 1, 1],
        [1, 1, 1, 1, 1],
        [0, 0, 0, 0, 0],
    ]
    assert receipt["singular"]["program_sha256"] == ("3cf0ffc36b3046ba75c6845fe8baef6fb0edcbcd56bfa5a32a7c6b49ef258592")
    assert receipt["singular"]["transcript_sha256"] == ("327c575f1f300e94b1f566131736eaa1850d361f9b3cd3ddf76425f2041a10f8")
    assert sum(row["common_zero_count"] for row in receipt["all_cyclic_eigenpoint_evaluations"]) == 125
    assert all(row["jacobian_rank_distribution"] == {"22": 25} for row in receipt["forced_h0_eigenpoints"])
    assert receipt["certificate_verified"] is True
    artifact = tmp_path / "artifacts/research/level25-heisenberg-balanced-pivoted.md"
    text = artifact.read_text(encoding="utf-8")
    assert result["receipt_sha256"] in text
    assert text.endswith("LEVEL25_BALANCED_PIVOTED_COMPLETE\n")


def test_level25_heisenberg_balanced_two_pivot_report_passes_local_gates(
    tmp_path,
):
    if shutil.which("Singular") is None:
        pytest.skip("Singular is unavailable")

    registry = _registry(tmp_path)
    result = registry.invoke(
        "level25_heisenberg_balanced_two_pivot_restricted_system_report",
        {
            "path": "research/level25-heisenberg-balanced-two-pivot.md",
            "terminal_marker": "LEVEL25_BALANCED_TWO_PIVOT_COMPLETE",
        },
    )

    receipt = result["receipt"]
    assert receipt["schema"] == ("reasoning.level25-heisenberg-balanced-two-pivot-restricted-system-profile.v1")
    assert receipt["form_definition"]["ambient_term_count"] == 600
    assert receipt["dimension_census_by_subgroup"] == [
        [1, 1, 1, 1, 1],
        [1, 1, 1, 1, 1],
        [1, 1, 1, 1, 1],
        [1, 1, 1, 1, 1],
        [1, 1, 1, 1, 1],
        [0, 0, 0, 0, 0],
    ]
    assert receipt["singular"]["program_sha256"] == ("6b233fa8350393bca279aaaf19d72b188117095b5e51b78be7897b0731fcddca")
    assert receipt["singular"]["transcript_sha256"] == ("a15080700b1d5101c64d17c92e5cb5159aa43e13238af6ba255a41b597c2e57a")
    assert sum(row["common_zero_count"] for row in receipt["all_cyclic_eigenpoint_evaluations"]) == 125
    assert all(row["jacobian_rank_distribution"] == {"22": 25} for row in receipt["forced_h0_eigenpoints"])
    assert receipt["certificate"]["second_pivot_restricts_to_zero_on_all_fixed_p4s"] is True
    assert receipt["certificate_verified"] is True
    assert result["receipt_sha256"] == ("cd0f1d902bfd58d761b43c6e7e2821245083060c998177a53fb75bdf73f003e0")
    artifact = tmp_path / "artifacts/research/level25-heisenberg-balanced-two-pivot.md"
    text = artifact.read_text(encoding="utf-8")
    assert result["receipt_sha256"] in text
    assert text.endswith("LEVEL25_BALANCED_TWO_PIVOT_COMPLETE\n")


def test_level25_heisenberg_balanced_five_pivot_report_passes_local_gates(
    tmp_path,
):
    if shutil.which("Singular") is None:
        pytest.skip("Singular is unavailable")

    registry = _registry(tmp_path)
    result = registry.invoke(
        "level25_heisenberg_balanced_five_pivot_restricted_system_report",
        {
            "path": "research/level25-heisenberg-balanced-five-pivot.md",
            "terminal_marker": "LEVEL25_BALANCED_FIVE_PIVOT_COMPLETE",
        },
    )

    receipt = result["receipt"]
    assert receipt["schema"] == ("reasoning.level25-heisenberg-balanced-five-pivot-restricted-system-profile.v1")
    assert receipt["form_definition"]["ambient_term_count"] == 675
    assert receipt["dimension_census_by_subgroup"] == [
        [1, 1, 1, 1, 1],
        [1, 1, 1, 1, 1],
        [1, 1, 1, 1, 1],
        [1, 1, 1, 1, 1],
        [1, 1, 1, 1, 1],
        [0, 0, 0, 0, 0],
    ]
    assert receipt["singular"]["program_sha256"] == ("38c786a90421034b93e01178c2987035e001c0c0e280fe3531098af6dde5bdba")
    assert receipt["singular"]["transcript_sha256"] == ("035b16ad9adb6ea8a7d564ea9dca4a3788762417872c7362d02b19f55a6463fe")
    assert sum(row["common_zero_count"] for row in receipt["all_cyclic_eigenpoint_evaluations"]) == 125
    assert all(row["jacobian_rank_distribution"] == {"22": 25} for row in receipt["forced_h0_eigenpoints"])
    assert receipt["certificate"]["four_nonadjacent_pivots_restrict_to_zero_on_all_fixed_p4s"] is True
    assert receipt["certificate_verified"] is True
    assert result["receipt_sha256"] == ("add52c40247f323f73c84d991acf493f98bb8fb430a9bda42bb138bb830a6ba8")
    artifact = tmp_path / "artifacts/research/level25-heisenberg-balanced-five-pivot.md"
    text = artifact.read_text(encoding="utf-8")
    assert result["receipt_sha256"] in text
    assert text.endswith("LEVEL25_BALANCED_FIVE_PIVOT_COMPLETE\n")


def test_level25_heisenberg_balanced_five_pivot_support_jacobian_profile():
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    result = level25_heisenberg_balanced_five_pivot_support_jacobian_profile()

    assert result["schema"] == ("reasoning.level25-heisenberg-balanced-five-pivot-support-jacobian-profile.v1")
    assert result["computation"]["support_count"] == 2**25
    assert result["computation"]["program_sha256"] == ("27f6acd4fd55511d98c6d3dac37f7407dd76e7476c107895deff6fec889ab776")
    assert result["computation"]["transcript_sha256"] == ("3238a797fbd54ffe8f7f75260a5c3dd3148b3d7f21c7572f82943b250ff903d3")
    assert result["computation"]["pure_rank_histograms"][5] == {
        4: 5,
        5: 53_125,
    }
    assert result["forced_nonempty_supports_by_size"] == {
        "23": 300,
        "24": 25,
        "25": 1,
    }
    assert result["computation"]["forced_matching_histogram"] == {22: 326}
    assert result["computation"]["forced_rank_deficient_count"] == 0
    assert result["certificate_verified"] is True
    assert "does not prove" in result["interpretation"]


def test_level25_heisenberg_balanced_five_pivot_support_jacobian_report(
    tmp_path,
):
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    registry = _registry(tmp_path)
    result = registry.invoke(
        "level25_heisenberg_balanced_five_pivot_support_jacobian_report",
        {
            "path": "research/level25-five-pivot-support-jacobian.md",
            "terminal_marker": "LEVEL25_FIVE_PIVOT_SUPPORT_JACOBIAN_COMPLETE",
        },
    )

    assert result["receipt_sha256"] == ("5812652d11901371d2713cab9262dc118c71bc48171330fd2a1bc8ec50fd9b03")
    artifact = tmp_path / "artifacts/research/level25-five-pivot-support-jacobian.md"
    text = artifact.read_text(encoding="utf-8")
    assert result["receipt_sha256"] in text
    assert text.endswith("LEVEL25_FIVE_PIVOT_SUPPORT_JACOBIAN_COMPLETE\n")


def test_level25_heisenberg_balanced_five_pivot_pure_power_alphabet_profile():
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    result = level25_heisenberg_balanced_five_pivot_pure_power_alphabet_profile()

    assert result["schema"] == ("reasoning.level25-heisenberg-balanced-five-pivot-pure-power-alphabet-profile.v2")
    assert result["computation"]["program_sha256"] == ("0cd65236f0308ff7321215e3f08eb21065eef9708a432060a4a16e249dd0ef43")
    assert result["computation"]["transcript_sha256"] == ("438271f4ca15ff5dc0b41c43dac209e72928ebb317903b7dac3b39f383fabfe8")
    assert result["evidence_sha256"] == "6c5203617f2123454fdf41c6f50ea658d4c0edb8da4a6da4891f733ddbda3de0"
    assert result["computation"]["free_assignment_count"] == 5**11
    assert result["computation"]["solution_count"] == 5**5
    assert result["support_block_histogram"] == {
        "0": 1,
        "1": 20,
        "2": 160,
        "3": 640,
        "4": 1280,
        "5": 1024,
    }
    assert result["certificate_verified"] is True
    assert "algebraic closure" in result["not_certified"][0]


def test_level25_heisenberg_balanced_five_pivot_pure_power_alphabet_report(
    tmp_path,
):
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    registry = _registry(tmp_path)
    result = registry.invoke(
        ("level25_heisenberg_balanced_five_pivot_pure_power_alphabet_report"),
        {
            "path": "research/level25-five-pivot-pure-power-alphabet.md",
            "terminal_marker": "LEVEL25_PURE_POWER_ALPHABET_COMPLETE",
        },
    )

    assert result["evidence_sha256"] == "6c5203617f2123454fdf41c6f50ea658d4c0edb8da4a6da4891f733ddbda3de0"
    artifact = tmp_path / "artifacts/research/level25-five-pivot-pure-power-alphabet.md"
    text = artifact.read_text(encoding="utf-8")
    assert result["receipt_sha256"] in text
    assert text.endswith("LEVEL25_PURE_POWER_ALPHABET_COMPLETE\n")


def test_level25_heisenberg_balanced_five_pivot_two_block_rational_profile():
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    result = level25_heisenberg_balanced_five_pivot_two_block_rational_profile()

    assert result["schema"] == ("reasoning.level25-heisenberg-balanced-five-pivot-two-block-rational-profile.v2")
    assert result["pure_power_prerequisite"]["evidence_sha256"] == "6c5203617f2123454fdf41c6f50ea658d4c0edb8da4a6da4891f733ddbda3de0"
    assert len(result["pure_power_prerequisite"]["receipt_sha256"]) == 64
    computation = result["computation"]
    assert computation["program_sha256"] == ("f0d3b52946f945b872c23089e6e9f64f17ee9f6d2971f4f6c892d2c877e557eb")
    assert computation["transcript_sha256"] == ("a03907ade8fa86d3d9b12667471cd7e882b9b4e7c8b36efa797e6aaca863b5d0")
    assert result["evidence_sha256"] == "2000fce3966e4592c0e35f975ecf00282fa2162ce6f769b9f115ffa4a9f1d9ae"
    assert computation["shape_count"] == 25**4
    assert computation["projective_mu5_orbits_per_support"] == 25**8 * 20
    assert computation["support_classes"][1] == {
        "join_hits": 0,
        "join_checksum": 1469598103934665603,
        "coupled_tests": 0,
        "coupled_checksum": 1469598103934665603,
        "orbit_zero_count": 0,
        "zero_checksum": 1469598103934665603,
        "singular_orbit_count": 0,
        "minimum_rank": -1,
    }
    assert computation["support_classes"][2] == {
        "join_hits": 8375,
        "join_checksum": 4554850547829100655,
        "coupled_tests": 41875,
        "coupled_checksum": 13462476352807999215,
        "orbit_zero_count": 0,
        "zero_checksum": 1469598103934665603,
        "singular_orbit_count": 0,
        "minimum_rank": -1,
    }
    assert result["certificate_verified"] is True
    assert "algebraic closure" in result["not_certified"][1]
    assert "two cyclic support types" in result["next_gate"]["requirement"]


def test_level25_heisenberg_balanced_five_pivot_two_block_rational_report(
    tmp_path,
):
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    registry = _registry(tmp_path)
    result = registry.invoke(
        ("level25_heisenberg_balanced_five_pivot_two_block_rational_report"),
        {
            "path": "research/level25-five-pivot-two-block-rational.md",
            "terminal_marker": "LEVEL25_TWO_BLOCK_RATIONAL_COMPLETE",
        },
    )

    assert result["evidence_sha256"] == "2000fce3966e4592c0e35f975ecf00282fa2162ce6f769b9f115ffa4a9f1d9ae"
    assert result["certificate_verified"] is True
    artifact = tmp_path / "artifacts/research/level25-five-pivot-two-block-rational.md"
    text = artifact.read_text(encoding="utf-8")
    assert result["receipt_sha256"] in text
    assert text.endswith("LEVEL25_TWO_BLOCK_RATIONAL_COMPLETE\n")


def test_level25_heisenberg_balanced_five_pivot_nonconsecutive_three_block_rational_profile():
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    result = level25_heisenberg_balanced_five_pivot_nonconsecutive_three_block_rational_profile()

    assert result["schema"] == ("reasoning.level25-heisenberg-balanced-five-pivot-nonconsecutive-three-block-rational-profile.v2")
    computation = result["computation"]
    assert computation["program_sha256"] == ("bc10d0ccac82ee98783bcc774f9a9f159843a231612346d0d8c2e9fcfb75bb1a")
    assert computation["transcript_sha256"] == ("e5ee6516b728189ec19ad377b9d5b5b815d6966d9e053a2583720425233ef07a")
    assert result["evidence_sha256"] == "ad3630016b6b3d2e23da038f971d6c53d020955536035d2ad6f2eb744dde5452"
    assert computation["normalized_shape_count"] == 4420
    assert computation["cyclotomic_shape_count"] == 120
    assert computation["cyclotomic_adjacent_compatible_pair_count"] == 0
    assert computation["compatible_ordered_shape_pair_count"] == 721750
    assert computation["shape_scale_class_triple_count"] == 5035625
    assert computation["full_scale_test_count"] == 125890625
    assert computation["orbit_zero_count"] == 12400
    assert computation["zero_checksum"] == 4071796996668289174
    assert computation["rank_14_count"] == 12400
    assert computation["minimum_rank"] == computation["maximum_rank"] == 14
    assert computation["first_witness_codes"] == [136, 200869, 265558]
    assert computation["first_witness_scale_exponents"] == [13, 11]
    assert result["first_witness_replay"]["nonzero_form_count"] == 0
    assert result["first_witness_replay"]["jacobian_rank"] == 14
    assert result["certificate_verified"] is True
    assert "characteristic-zero" in result["not_certified"][0]


def test_level25_heisenberg_balanced_five_pivot_nonconsecutive_three_block_rational_report(
    tmp_path,
):
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    registry = _registry(tmp_path)
    result = registry.invoke(
        ("level25_heisenberg_balanced_five_pivot_nonconsecutive_three_block_rational_report"),
        {
            "path": "research/level25-five-pivot-three-block-rational.md",
            "terminal_marker": "LEVEL25_THREE_BLOCK_RATIONAL_COMPLETE",
        },
    )

    assert result["evidence_sha256"] == "ad3630016b6b3d2e23da038f971d6c53d020955536035d2ad6f2eb744dde5452"
    assert result["certificate_verified"] is True
    artifact = tmp_path / "artifacts/research/level25-five-pivot-three-block-rational.md"
    text = artifact.read_text(encoding="utf-8")
    assert result["receipt_sha256"] in text
    assert text.endswith("LEVEL25_THREE_BLOCK_RATIONAL_COMPLETE\n")


def test_level25_heisenberg_balanced_five_pivot_consecutive_three_block_rational_profile():
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    result = level25_heisenberg_balanced_five_pivot_consecutive_three_block_rational_profile()

    assert result["schema"] == ("reasoning.level25-heisenberg-balanced-five-pivot-consecutive-three-block-rational-profile.v2")
    computation = result["computation"]
    assert computation["program_sha256"] == ("437ecc8e77a7cd1557deecc8ba744970ac88eb6775d0bbcd6016fd8a15111987")
    assert computation["transcript_sha256"] == ("65b939e8060854397a7f37ffd3b5054ce7380cd979b3f96f6405e873c23201bc")
    assert result["evidence_sha256"] == "bad2c7f1acb71276f0c807fefa5b8a72909127da123bdf210d5a6f74d9352b69"
    assert computation["normalized_shape_count"] == 4420
    assert computation["cyclotomic_shape_count"] == 120
    assert computation["additive_shape_scale_class_triple_count"] == 135545985
    assert computation["adjacent_scale_solution_count"] == 40956925
    assert computation["full_scale_test_count"] == 40956925
    assert computation["orbit_zero_count"] == 4100
    assert computation["cyclotomic_orbit_zero_count"] == 0
    assert computation["zero_checksum"] == 8483181085099946785
    assert computation["rank_14_count"] == 4100
    assert computation["minimum_rank"] == computation["maximum_rank"] == 14
    assert computation["first_witness_codes"] == [1826, 136, 146505]
    assert computation["first_witness_scale_exponents"] == [17, 19]
    assert result["first_witness_replay"]["nonzero_form_count"] == 0
    assert result["first_witness_replay"]["jacobian_rank"] == 14
    assert result["certificate_verified"] is True
    assert "characteristic-zero" in result["not_certified"][0]


def test_level25_heisenberg_balanced_five_pivot_consecutive_three_block_rational_report(
    tmp_path,
):
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    registry = _registry(tmp_path)
    result = registry.invoke(
        ("level25_heisenberg_balanced_five_pivot_consecutive_three_block_rational_report"),
        {
            "path": "research/level25-five-pivot-consecutive-three-block.md",
            "terminal_marker": "LEVEL25_CONSECUTIVE_THREE_BLOCK_COMPLETE",
        },
    )

    assert result["evidence_sha256"] == "bad2c7f1acb71276f0c807fefa5b8a72909127da123bdf210d5a6f74d9352b69"
    assert result["certificate_verified"] is True
    artifact = tmp_path / "artifacts/research/level25-five-pivot-consecutive-three-block.md"
    text = artifact.read_text(encoding="utf-8")
    assert result["receipt_sha256"] in text
    assert text.endswith("LEVEL25_CONSECUTIVE_THREE_BLOCK_COMPLETE\n")


def test_level25_heisenberg_fixed_stratum_singleton_orbit_profile():
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    result = level25_heisenberg_fixed_stratum_singleton_orbit_profile()

    assert result["schema"] == ("reasoning.level25-heisenberg-fixed-stratum-singleton-orbit-profile.v2")
    computation = result["computation"]
    assert computation["program_sha256"] == ("baa58ccad9e07cf202209f4dbafec5e5235642c10c7c4cb2df5123c125b0aab0")
    assert computation["transcript_sha256"] == ("82a50ce5b7eb732ca6a14f5ee6ce23391954a246fd36fe522371b0be80a7b759")
    assert computation["raw_candidate_count"] == 6600
    assert computation["restriction_test_count"] == 198000
    assert computation["survivor_count"] == 5400
    assert computation["survivor_checksum"] == 1659729786632400067
    assert len(computation["row_histogram"]) == 18
    assert {row[2] for row in computation["row_histogram"]} == {300}
    assert len(computation["residue_pattern_histogram"]) == 6
    assert {row[2] for row in computation["residue_pattern_histogram"]} == {900}
    assert result["representative_candidates"][0]["exponents"] == [1, 23, 1]
    assert result["certificate_verified"] is True
    assert "raises its Jacobian rank" in result["not_certified"][0]


def test_level25_heisenberg_fixed_stratum_singleton_orbit_report(tmp_path):
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    registry = _registry(tmp_path)
    result = registry.invoke(
        "level25_heisenberg_fixed_stratum_singleton_orbit_report",
        {
            "path": "research/level25-fixed-stratum-singleton-orbits.md",
            "terminal_marker": "LEVEL25_SINGLETON_ORBITS_COMPLETE",
        },
    )

    assert result["evidence_sha256"] == ("c30c8ec6ea77e72838fe365ebacec7df3b8226753ce6b557123c548feb56cf93")
    assert result["certificate_verified"] is True
    artifact = tmp_path / "artifacts/research/level25-fixed-stratum-singleton-orbits.md"
    text = artifact.read_text(encoding="utf-8")
    assert result["evidence_sha256"] in text
    assert result["receipt_sha256"] in text
    assert text.endswith("LEVEL25_SINGLETON_ORBITS_COMPLETE\n")


def test_level25_heisenberg_singleton_orbit_three_block_coverage_profile():
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    result = level25_heisenberg_singleton_orbit_three_block_coverage_profile()

    assert result["schema"] == ("reasoning.level25-heisenberg-singleton-orbit-three-block-coverage-profile.v2")
    computation = result["computation"]
    assert computation["program_sha256"] == ("576ca653dfd0989b70d2d1e11564f2dd558d45d0e32b75510142d5985a26dc43")
    assert computation["transcript_sha256"] == ("e02e92e806098ba8afd85a65bb69ee36e60bab9a8c262641313d97036d663e2c")
    assert computation["nonconsecutive_zero_count"] == 12400
    assert computation["nonconsecutive_zero_checksum"] == 4071796996668289174
    assert computation["consecutive_zero_count"] == 4100
    assert computation["consecutive_zero_checksum"] == 8483181085099946785
    assert computation["b_zero_candidate_count"] == 4200
    assert computation["candidate_point_evaluation_count"] == 34650000
    assert computation["selected_candidates"] == [
        [9, 0, 2, 19, 18, 0],
        [7, 0, 1, 2, 23, 1],
    ]
    assert computation["remaining_counts"] == [4100, 0]
    assert computation["final_uncovered_count"] == 0
    assert computation["selection_checksum"] == 14663480442488609147
    assert [row["selected_candidate_values"] for row in result["first_witness_replays"]] == [[70], [67]]
    assert result["certificate_verified"] is True
    assert "new F_101-rational" in result["not_certified"][0]


def test_level25_heisenberg_singleton_orbit_three_block_coverage_report(tmp_path):
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    registry = _registry(tmp_path)
    result = registry.invoke(
        "level25_heisenberg_singleton_orbit_three_block_coverage_report",
        {
            "path": "research/level25-singleton-three-block-coverage.md",
            "terminal_marker": "LEVEL25_THREE_BLOCK_COVERAGE_COMPLETE",
        },
    )

    assert result["evidence_sha256"] == ("438fb3a52ef40fa7e41aace29658a66b4fc3c642388054a97e02bd3941e61ee1")
    assert result["certificate_verified"] is True
    artifact = tmp_path / "artifacts/research/level25-singleton-three-block-coverage.md"
    text = artifact.read_text(encoding="utf-8")
    assert result["evidence_sha256"] in text
    assert result["receipt_sha256"] in text
    assert text.endswith("LEVEL25_THREE_BLOCK_COVERAGE_COMPLETE\n")


def test_level25_heisenberg_augmented_fixed_and_cyclic_local_profile():
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    result = level25_heisenberg_augmented_fixed_and_cyclic_local_profile()

    assert result["schema"] == ("reasoning.level25-heisenberg-augmented-fixed-and-cyclic-local-profile.v2")
    assert result["coverage_evidence_sha256"] == ("438fb3a52ef40fa7e41aace29658a66b4fc3c642388054a97e02bd3941e61ee1")
    assert result["selected_augmentation"] == [
        [9, 0, 2, 19, 18, 0],
        [7, 0, 1, 2, 23, 1],
    ]
    assert all(row["nonzero_fixed_restriction_count"] == 0 for row in result["selected_fixed_restriction_replay"])
    cyclic = result["cyclic_eigenpoint_replay"]
    assert cyclic["eigenpoint_count"] == 750
    assert cyclic["invalid_eigenpoint_count"] == 0
    assert cyclic["common_zero_membership_change_count"] == 0
    assert cyclic["augmented_common_zero_count"] == 125
    assert cyclic["augmented_rank_22_count"] == 125
    assert result["certificate_verified"] is True
    assert "global Jacobian rank" in result["not_certified"][0]


def test_level25_heisenberg_augmented_fixed_and_cyclic_local_report(tmp_path):
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    registry = _registry(tmp_path)
    result = registry.invoke(
        "level25_heisenberg_augmented_fixed_and_cyclic_local_report",
        {
            "path": "research/level25-augmented-local-replay.md",
            "terminal_marker": "LEVEL25_AUGMENTED_LOCAL_COMPLETE",
        },
    )

    assert result["evidence_sha256"] == ("43e14323b47fbb532c19f58d1b7de175d0a6e855fc18ed63efd9c6a8c916f795")
    assert result["certificate_verified"] is True
    artifact = tmp_path / "artifacts/research/level25-augmented-local-replay.md"
    text = artifact.read_text(encoding="utf-8")
    assert result["evidence_sha256"] in text
    assert result["receipt_sha256"] in text
    assert text.endswith("LEVEL25_AUGMENTED_LOCAL_COMPLETE\n")


def test_level25_heisenberg_augmented_three_block_rational_profile():
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    result = level25_heisenberg_augmented_three_block_rational_profile()

    assert result["schema"] == ("reasoning.level25-heisenberg-augmented-three-block-rational-profile.v2")
    assert result["coverage_evidence_sha256"] == ("438fb3a52ef40fa7e41aace29658a66b4fc3c642388054a97e02bd3941e61ee1")
    computation = result["computation"]
    assert computation["program_sha256"] == ("a41ceb7d993f9df17c8d52a99a4a3137375a7c81e9a3cb3187debe737790798f")
    assert computation["transcript_sha256"] == ("ac592937657e73cb49c7f508b1d533fcb595f1ab7d687cb53ce4e80f1e60a22d")
    for kind in ("nonconsecutive", "consecutive"):
        assert computation[f"{kind}_rank"] == 11
        assert computation[f"{kind}_free_column_count"] == 4
        assert computation[f"{kind}_alphabet_assignment_count"] == 256
        assert computation[f"{kind}_solution_count"] == 64
        assert computation[f"{kind}_block_constant_solution_count"] == 64
        assert computation[f"{kind}_solution_checksum"] == 6819550470155986435
    assert result["certificate_verified"] is True
    assert "algebraic closure" in result["not_certified"][0]


def test_level25_heisenberg_augmented_three_block_rational_report(tmp_path):
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    registry = _registry(tmp_path)
    result = registry.invoke(
        "level25_heisenberg_augmented_three_block_rational_report",
        {
            "path": "research/level25-augmented-three-block-rational.md",
            "terminal_marker": "LEVEL25_AUGMENTED_THREE_BLOCK_COMPLETE",
        },
    )

    assert result["evidence_sha256"] == ("1f6117123f053dcb07180e2cde3896b2dd0e1b2d5231bc1eb7f7a0d80f1250fe")
    assert result["certificate_verified"] is True
    artifact = tmp_path / "artifacts/research/level25-augmented-three-block-rational.md"
    text = artifact.read_text(encoding="utf-8")
    assert result["evidence_sha256"] in text
    assert result["receipt_sha256"] in text
    assert text.endswith("LEVEL25_AUGMENTED_THREE_BLOCK_COMPLETE\n")


def test_level25_heisenberg_augmented_four_block_alphabet_profile():
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    result = level25_heisenberg_augmented_four_block_alphabet_profile()

    assert result["schema"] == ("reasoning.level25-heisenberg-augmented-four-block-alphabet-profile.v2")
    assert result["support_residues"] == [0, 1, 2, 3]
    assert len(result["cyclic_support_orbit"]) == 5
    computation = result["computation"]
    assert computation["program_sha256"] == ("099302d1f7e8e72367e2347dd8f0a95fe3bf0dffdad2fabb190bd1949f3ba17d")
    assert computation["transcript_sha256"] == ("f1bd72d0126ea16e3dcfeb5448fe4da014347adbd4e94d93238b3f94beb70251")
    assert computation["rank"] == 12
    assert computation["free_column_count"] == 8
    assert computation["alphabet_assignment_count"] == 65536
    assert computation["solution_count"] == 256
    assert computation["block_constant_solution_count"] == 256
    assert computation["solution_checksum"] == 2955430217838455939
    assert result["independent_python_census"] == {
        "rank": 12,
        "free_column_count": 8,
        "alphabet_assignment_count": 65536,
        "solution_count": 256,
        "block_constant_solution_count": 256,
        "solution_checksum": 2955430217838455939,
    }
    assert result["evidence_sha256"] == ("bf392cc8d623d78ed3eef029132335fa1d974cbfa42c1294bedd2e899aade9b2")
    assert result["certificate_verified"] is True
    assert "does not exclude four-block zeros" in result["interpretation"]


def test_level25_heisenberg_augmented_four_block_alphabet_report(tmp_path):
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    registry = _registry(tmp_path)
    result = registry.invoke(
        "level25_heisenberg_augmented_four_block_alphabet_report",
        {
            "path": "research/level25-augmented-four-block-alphabet.md",
            "terminal_marker": "LEVEL25_AUGMENTED_FOUR_BLOCK_ALPHABET_COMPLETE",
        },
    )

    assert result["evidence_sha256"] == ("bf392cc8d623d78ed3eef029132335fa1d974cbfa42c1294bedd2e899aade9b2")
    assert result["certificate_verified"] is True
    artifact = tmp_path / "artifacts/research/level25-augmented-four-block-alphabet.md"
    text = artifact.read_text(encoding="utf-8")
    receipt = json.loads(text.split("```json\n", 1)[1].split("\n```", 1)[0])
    assert result["receipt_sha256"] == hashlib.sha256(canonical_json(receipt).encode()).hexdigest()
    assert result["receipt_sha256"] in text
    assert text.endswith("LEVEL25_AUGMENTED_FOUR_BLOCK_ALPHABET_COMPLETE\n")


def test_level25_heisenberg_augmented_four_block_phase_quotient_profile():
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    result = level25_heisenberg_augmented_four_block_phase_quotient_profile()

    assert result["schema"] == ("reasoning.level25-heisenberg-augmented-four-block-phase-quotient-profile.v2")
    computation = result["computation"]
    assert computation["program_sha256"] == ("7b460251351db3bc3c7da7fecca7e138f1f1f4fc5ddf7bb07abefa85ebb606fd")
    assert computation["transcript_sha256"] == ("d0d42f8385b04fe23297b02eabb0464c75fc38052b61200820b5886afc02291a")
    assert computation["q7_difference"] == [1, 2, 3, 4]
    assert computation["q9_difference"] == [3, 1, 4, 2]
    assert computation["difference_multiplier"] == 3
    assert computation["phase_tuple_count"] == 625
    assert computation["common_kernel_count"] == 125
    assert computation["diagonal_count"] == 5
    assert computation["relative_kernel_count"] == 25
    assert computation["normalized_phase_tuple_count"] == 125
    assert computation["normalized_orbit_count"] == 5
    assert computation["invariant_histogram"] == [25] * 5
    assert computation["canonical_representatives"] == [
        [0, 0, 0, 0],
        [0, 3, 0, 0],
        [0, 1, 0, 0],
        [0, 4, 0, 0],
        [0, 2, 0, 0],
    ]
    assert result["independent_python_replay"]["q7_term_vectors"] == [
        [1, 3, 1, 0],
        [0, 1, 3, 1],
    ]
    assert result["independent_python_replay"]["q9_term_vectors"] == [
        [1, 1, 0, 3],
        [3, 0, 1, 1],
    ]
    assert result["evidence_sha256"] == ("ddff1f79ec58bd2a093038a70d01483341988cc8d5c829e6fd8939fd6fecc95c")
    assert result["certificate_verified"] is True
    assert "five orbits" in result["interpretation"]


def test_level25_heisenberg_augmented_four_block_phase_quotient_report(tmp_path):
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    registry = _registry(tmp_path)
    result = registry.invoke(
        "level25_heisenberg_augmented_four_block_phase_quotient_report",
        {
            "path": "research/level25-augmented-four-block-phase-quotient.md",
            "terminal_marker": "LEVEL25_AUGMENTED_FOUR_BLOCK_PHASE_COMPLETE",
        },
    )

    assert result["evidence_sha256"] == ("ddff1f79ec58bd2a093038a70d01483341988cc8d5c829e6fd8939fd6fecc95c")
    assert result["certificate_verified"] is True
    artifact = tmp_path / "artifacts/research/level25-augmented-four-block-phase-quotient.md"
    text = artifact.read_text(encoding="utf-8")
    receipt = json.loads(text.split("```json\n", 1)[1].split("\n```", 1)[0])
    assert result["receipt_sha256"] == hashlib.sha256(canonical_json(receipt).encode()).hexdigest()
    assert result["receipt_sha256"] in text
    assert text.endswith("LEVEL25_AUGMENTED_FOUR_BLOCK_PHASE_COMPLETE\n")


def test_level25_heisenberg_augmented_four_block_fourier_profile():
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    result = level25_heisenberg_augmented_four_block_fourier_profile()

    assert result["schema"] == ("reasoning.level25-heisenberg-augmented-four-block-fourier-profile.v2")
    assert result["alphabet_evidence_sha256"] == ("bf392cc8d623d78ed3eef029132335fa1d974cbfa42c1294bedd2e899aade9b2")
    computation = result["computation"]
    assert computation["program_sha256"] == ("7421d66130f7c0bf913de6ac981441d2da1979fe22cda6e1d834ab3fa1fa3900")
    assert computation["transcript_sha256"] == ("36352eed7bc8dfdee52f8c7f498c3918d787a53ebf892974c4821a0bd09b1445")
    assert computation["shape_count"] == 390625
    assert computation["zero_cyclic_sum_shape_count"] == 4420
    assert computation["nonzero_bucket_minimum"] == 3230
    assert computation["nonzero_bucket_maximum"] == 4300
    assert computation["histogram_checksum"] == 5104993727717593996
    assert computation["fourier_rank"] == 3
    assert computation["kernel_vector"] == [1, 96, 31, 17]
    assert computation["scale_twenty_fifth_power_classes"] == [1, 10, 100, 91]
    assert computation["admissible_shape_class_quadruple_count"] == 1449072215711161125
    assert computation["zero_line_shape_class_quadruple_count"] == 24426939197440000
    assert computation["nonzero_line_shape_class_quadruple_count"] == 1424645276513721125
    assert len(computation["cyclic_sum_histogram"]) == 101
    assert computation["cyclic_sum_histogram"] == result["independent_python_replay"]["cyclic_sum_histogram"]
    assert result["evidence_sha256"] == ("8da803c2ceffb48c1cf5590e9c6334ed37d2760383231ee71b7720db99c46317")
    assert result["certificate_verified"] is True
    assert "do not force each block cyclic sum to vanish" in result["interpretation"]


def test_level25_heisenberg_augmented_four_block_fourier_report(tmp_path):
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    registry = _registry(tmp_path)
    result = registry.invoke(
        "level25_heisenberg_augmented_four_block_fourier_report",
        {
            "path": "research/level25-augmented-four-block-fourier.md",
            "terminal_marker": "LEVEL25_AUGMENTED_FOUR_BLOCK_FOURIER_COMPLETE",
        },
    )

    assert result["evidence_sha256"] == ("8da803c2ceffb48c1cf5590e9c6334ed37d2760383231ee71b7720db99c46317")
    assert result["certificate_verified"] is True
    artifact = tmp_path / "artifacts/research/level25-augmented-four-block-fourier.md"
    text = artifact.read_text(encoding="utf-8")
    receipt = json.loads(text.split("```json\n", 1)[1].split("\n```", 1)[0])
    assert result["receipt_sha256"] == hashlib.sha256(canonical_json(receipt).encode()).hexdigest()
    assert result["receipt_sha256"] in text
    assert text.endswith("LEVEL25_AUGMENTED_FOUR_BLOCK_FOURIER_COMPLETE\n")


def test_level25_heisenberg_augmented_four_block_unary_convolution_profile():
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    result = level25_heisenberg_augmented_four_block_unary_convolution_profile()

    assert result["schema"] == ("reasoning.level25-heisenberg-augmented-four-block-unary-convolution-profile.v2")
    assert result["fourier_evidence_sha256"] == ("8da803c2ceffb48c1cf5590e9c6334ed37d2760383231ee71b7720db99c46317")
    computation = result["computation"]
    assert computation["program_sha256"] == ("7a4ed2e351722786fbc74dc144a5d9c0968497188abc342879161e825a25dc6b")
    assert computation["transcript_sha256"] == ("a603309428767f5466448a9ea2db956d38f886cafb9c221568c74a6e7a0a4949")
    assert computation["transform_moduli"] == [
        1449072215711168621,
        1449072215711172257,
    ]
    assert computation["histogram_checksum"] == 6465411173863107267
    assert computation["support_checksum"] == 17561451370375839639
    assert computation["histogram_support_minimum"] == 2775
    assert computation["histogram_support_maximum"] == 3546
    assert computation["shape_class_survivor_count"] == 142052065500490
    assert computation["zero_line_shape_class_survivor_count"] == 2394564235515
    assert computation["nonzero_line_shape_class_survivor_count"] == 139657501264975
    assert computation["scale_phase_setting_count"] == 88782540937806250
    assert computation["minimum_fiber_survivor_count"] == 12816504840
    assert computation["maximum_fiber_survivor_count"] == 37418685840
    assert computation["fiber_checksum"] == 1051450695276653208
    assert computation["calibration_fiber_counts"] == [
        37417315695,
        37418685840,
        17489015025,
        22348045035,
        22863914780,
    ]
    assert computation["row0_unary_kernel_factor"] == 62
    assert max(abs(value["relative_deviation_decimal"]) for value in result["survivor_ratios"].values()) < 1e-6
    assert result["evidence_sha256"] == ("f39b6bf089f400ef9677502a4b5caee5173071136795abb0c113ef747bf0f542")
    assert result["certificate_verified"] is True
    assert "remaining coupled join" in result["interpretation"]


def test_level25_heisenberg_augmented_four_block_unary_convolution_report(tmp_path):
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    registry = _registry(tmp_path)
    result = registry.invoke(
        "level25_heisenberg_augmented_four_block_unary_convolution_report",
        {
            "path": "research/level25-augmented-four-block-unary-convolution.md",
            "terminal_marker": "LEVEL25_AUGMENTED_FOUR_BLOCK_UNARY_CONVOLUTION_COMPLETE",
        },
    )

    assert result["evidence_sha256"] == ("f39b6bf089f400ef9677502a4b5caee5173071136795abb0c113ef747bf0f542")
    assert result["certificate_verified"] is True
    artifact = tmp_path / "artifacts/research/level25-augmented-four-block-unary-convolution.md"
    text = artifact.read_text(encoding="utf-8")
    receipt = json.loads(text.split("```json\n", 1)[1].split("\n```", 1)[0])
    assert result["receipt_sha256"] == hashlib.sha256(canonical_json(receipt).encode()).hexdigest()
    assert result["receipt_sha256"] in text
    assert text.endswith("LEVEL25_AUGMENTED_FOUR_BLOCK_UNARY_CONVOLUTION_COMPLETE\n")


def test_level25_heisenberg_augmented_four_block_bounded_ad5_profile():
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    result = level25_heisenberg_augmented_four_block_bounded_ad5_profile()

    assert result["schema"] == ("reasoning.level25-heisenberg-augmented-four-block-bounded-ad5-profile.v2")
    assert result["source_manifest"]["raw_record_count"] == 725
    assert result["source_manifest"]["audit_claimed_raw_record_count"] == 675
    assert result["source_manifest"]["audit_count_matches_executable_source"] is False
    computation = result["computation"]
    assert computation["program_sha256"] == ("5650b2aef36ca801bda89048e4c0b4d2ddc52bd607eb023e850903d66bfeb3d1")
    assert computation["mathematical_transcript_sha256"] == ("53dfd0577aa35249cb20ace289f02c798e6a919d7e4e3ed9ba8e24f0fdbcd63b")
    assert computation["transcript"].endswith("REASONING_END\n")
    assert hashlib.sha256(computation["transcript"].encode()).hexdigest() == computation["transcript_sha256"]
    assert computation["histogram_checksum"] == 5104993727717593996
    assert computation["zero_bucket_count"] == 4420
    assert computation["first_zero_shape_code"] == 280
    assert computation["b_state_count"] == 110500
    assert computation["c_state_count"] == 22100
    assert computation["logical_cartesian_pair_count"] == 2442050000
    assert computation["exact_key_match_count"] == 1
    assert computation["residual_tests_completed"] == 1
    assert computation["residual_survivor_count"] == 0
    assert computation["decomposition_failure_count"] == 0
    assert computation["decomposition_sample_count"] == 64
    assert computation["decomposition_sample_failure_count"] == 0
    assert computation["search_charge_total"] == 54327397
    assert computation["vm_peak_kib"] < 64 * 1024
    independent = result["independent_replay"]
    assert independent["program_sha256"] == ("155659e7f4f8bf52cad37a4756572f8a1d327e3e689f96550f5080c859535043")
    assert independent["charged_operations"] == 5863997
    assert result["decision"]["status"] == "FIRST_AD5_CONTEXT_EMPTY"
    assert result["decision"]["context_exhausted"] is True
    assert result["evidence_sha256"] == ("f425e93832a5b366a0c8e6e4b1618c184a9ec8b1f6aa874e98a9b0d87326bcb8")
    assert result["certificate_verified"] is True


def test_level25_heisenberg_augmented_four_block_bounded_ad5_report(tmp_path):
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    registry = _registry(tmp_path)
    result = registry.invoke(
        "level25_heisenberg_augmented_four_block_bounded_ad5_report",
        {
            "path": "research/level25-augmented-four-block-bounded-ad5.md",
            "terminal_marker": "LEVEL25_AUGMENTED_FOUR_BLOCK_BOUNDED_AD5_COMPLETE",
        },
    )

    assert result["evidence_sha256"] == ("f425e93832a5b366a0c8e6e4b1618c184a9ec8b1f6aa874e98a9b0d87326bcb8")
    assert result["certificate_verified"] is True
    assert result["decision"]["status"] == "FIRST_AD5_CONTEXT_EMPTY"
    assert result["exact_key_match_count"] == 1
    assert result["residual_tests_completed"] == 1
    assert result["source_record_count"] == 725
    assert result["audit_claimed_source_record_count"] == 675
    assert result["audit_count_matches_executable_source"] is False
    assert result["source_record_count_correction"] == (
        "The executable tuple has 550 base records, 25 adjacent row-zero "
        "records, 100 nonadjacent pivot records, and 50 selected-orbit "
        "records. The model-authored audit omitted two 25-record sums."
    )
    artifact = tmp_path / "artifacts/research/level25-augmented-four-block-bounded-ad5.md"
    text = artifact.read_text(encoding="utf-8")
    receipt = json.loads(text.split("```json\n", 1)[1].split("\n```", 1)[0])
    assert result["receipt_sha256"] == hashlib.sha256(canonical_json(receipt).encode()).hexdigest()
    assert result["receipt_sha256"] in text
    assert text.endswith("LEVEL25_AUGMENTED_FOUR_BLOCK_BOUNDED_AD5_COMPLETE\n")


def test_level25_heisenberg_augmented_four_block_bounded_ad5_context_batch_profile():
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    result = level25_heisenberg_augmented_four_block_bounded_ad5_context_batch_profile()

    assert result["schema"] == ("reasoning.level25-heisenberg-augmented-four-block-bounded-ad5-context-batch-profile.v2")
    computation = result["computation"]
    assert computation["program_sha256"] == ("b413cc1f9f12921e4a93cf05cdcd71394844cde765d15feb7abb3b06fa414b63")
    assert computation["mathematical_transcript_sha256"] == ("1cc298dc0dfcf393d87242d13b266acacabfb660ef3808e0f8578d8e217cfd66")
    assert computation["transcript"].endswith("REASONING_END\n")
    assert hashlib.sha256(computation["transcript"].encode()).hexdigest() == (computation["transcript_sha256"])
    assert computation["zero_bucket_prefix_codes"] == [280, 281, 480, 489, 506]
    assert computation["fixed_charge_total"] == 5863997
    assert computation["aggregate_charge_total"] == 199704165
    assert computation["reserved_upper_bound"] == 223173585
    assert computation["vm_peak_kib"] < 64 * 1024
    assert [context["d_code"] for context in computation["contexts"]] == [
        281,
        480,
        489,
        506,
    ]
    assert [context["status"] for context in computation["contexts"]] == ["CONTEXT_EMPTY"] * 4
    assert [context["exact_key_match_count"] for context in computation["contexts"]] == [0, 0, 0, 0]
    assert [context["residual_tests_completed"] for context in computation["contexts"]] == [0, 0, 0, 0]
    assert [context["charge_total"] for context in computation["contexts"]] == [
        48461360,
        48458326,
        48459500,
        48460982,
    ]
    assert result["independent_domain_replay"]["program_sha256"] == ("8df30281ce9d058c1d5afd4de2bea2de67f455b4b79c3d2d36b1a635dbb26aa7")
    assert result["independent_domain_replay"]["transcript_sha256"] == ("2c72e3e1f1c48ed99fb67412009f5a1df55d9d9310137b2d1175441a934671ee")
    assert result["independent_domain_replay"]["charged_operations"] == 5863997
    assert result["decision"] == {
        "status": "NEXT_FOUR_AD5_CONTEXTS_EMPTY",
        "smoothness_candidate_rejected": False,
        "contexts_exhausted": [0, 1, 2, 3],
        "complete_target_batch_exhausted": True,
    }
    assert result["evidence_sha256"] == ("49835bb6dc187a1cabfae48e4a9aa2c7afbe3f9334799d518e5a94a7a0f2d873")
    assert result["certificate_verified"] is True


def test_level25_heisenberg_augmented_four_block_bounded_ad5_context_batch_report(
    tmp_path,
):
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    registry = _registry(tmp_path)
    result = registry.invoke(
        "level25_heisenberg_augmented_four_block_bounded_ad5_context_batch_report",
        {
            "path": "research/level25-augmented-four-block-bounded-ad5-batch.md",
            "terminal_marker": "LEVEL25_AUGMENTED_FOUR_BLOCK_BOUNDED_AD5_BATCH_COMPLETE",
        },
    )

    assert result["evidence_sha256"] == ("49835bb6dc187a1cabfae48e4a9aa2c7afbe3f9334799d518e5a94a7a0f2d873")
    assert result["certificate_verified"] is True
    assert result["decision"]["status"] == "NEXT_FOUR_AD5_CONTEXTS_EMPTY"
    assert [context["shape_d"] for context in result["context_summaries"]] == [
        281,
        480,
        489,
        506,
    ]
    assert [context["exact_key_match_count"] for context in result["context_summaries"]] == [0, 0, 0, 0]
    assert result["fixed_search_charge_total"] == 5863997
    assert result["aggregate_search_charge_total"] == 199704165
    assert result["reserved_upper_bound"] == 223173585
    artifact = tmp_path / "artifacts/research/level25-augmented-four-block-bounded-ad5-batch.md"
    text = artifact.read_text(encoding="utf-8")
    receipt = json.loads(text.split("```json\n", 1)[1].split("\n```", 1)[0])
    assert result["receipt_sha256"] == hashlib.sha256(canonical_json(receipt).encode()).hexdigest()
    assert result["receipt_sha256"] in text
    assert text.endswith("LEVEL25_AUGMENTED_FOUR_BLOCK_BOUNDED_AD5_BATCH_COMPLETE\n")


def test_level25_heisenberg_augmented_four_block_ad5_prefix_shift_census_profile():
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    result = level25_heisenberg_augmented_four_block_ad5_prefix_shift_census_profile()

    assert result["schema"] == ("reasoning.level25-heisenberg-augmented-four-block-ad5-prefix-shift-census-profile.v2")
    assert result["evidence_sha256"] == "619ea2102daed880ac662d6b119e8b324e5cafffb06ec9793213230696e48b24"
    computation = result["computation"]
    assert computation["program_sha256"] == ("31314f37c274d8c008bc4f017e949a9bbbf9ed1eb07a65c416dc0ff2c6eda99f")
    assert computation["transcript_sha256"] == ("f2b359b87b2363bb1b57b49d2f120a74afe3c5836bfc2031f2be38869c85e4bf")
    assert result["independent_replay"]["program_sha256"] == ("01ac41b766ae99f798eacf7dcae1c1878e19af09fa3ec2f18be00f8044f17e36")
    assert result["independent_replay"]["transcript_sha256"] == ("f48149a15e12bdcaffef6102eee53e27ba51526a0a7cd0b5cb1d8850767c064d")
    assert [computation[f"{name}_histogram_support"] for name in ("a", "d", "b", "c")] == [3546, 3546, 3546, 3546]
    assert computation["n_table_sha256"] == ("8aa5bd07dd0b64289fc4aae85dd3ac7c339754b92641f72d8fd6021c032d6069")
    assert computation["m_table_sha256"] == ("847f723787bf1da1dbd945b0a8189f19096566cda939182db0033864f6534c0b")
    assert computation["n_table_mass"] == 97_682_000
    assert computation["m_table_mass"] == 2_442_050_000
    assert computation["n_table_support"] == computation["m_table_support"] == 10_201
    assert computation["minimum_prefix_pair_count_on_realized_shift"] == 216_875
    assert computation["maximum_prefix_pair_count_on_realized_shift"] == 268_875
    assert computation["prefix_pair_context_count"] == 23_385_822_309_375
    assert computation["excluded_context_count"] == 0
    assert computation["open_context_count"] == 97_682_000
    assert result["actual_combined_charge"] == 78_437_038
    assert result["decision"] == {
        "status": "NONPRUNING_CENSUS",
        "complete_fixed_fiber_prefix_census": True,
        "contexts_prefix_excluded": 0,
        "contexts_not_excluded_by_prefix": 97_682_000,
        "full_key_match_or_common_zero_found": False,
    }
    assert result["certificate_verified"] is True


def test_level25_heisenberg_augmented_four_block_ad5_prefix_shift_census_report(
    tmp_path,
):
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    registry = _registry(tmp_path)
    result = registry.invoke(
        "level25_heisenberg_augmented_four_block_ad5_prefix_shift_census_report",
        {
            "path": "research/level25-ad5-prefix-shift-census.md",
            "terminal_marker": "LEVEL25_AD5_PREFIX_SHIFT_CENSUS_COMPLETE",
        },
    )

    assert result["evidence_sha256"] == "619ea2102daed880ac662d6b119e8b324e5cafffb06ec9793213230696e48b24"
    assert result["certificate_verified"] is True
    assert result["decision"]["status"] == "NONPRUNING_CENSUS"
    assert result["histogram_supports"] == {
        "a": 3546,
        "d": 3546,
        "b": 3546,
        "c": 3546,
    }
    assert result["realized_shift_count"] == 10_201
    assert result["excluded_shift_count"] == 0
    assert result["n_table_mass"] == 97_682_000
    assert result["m_table_mass"] == 2_442_050_000
    assert result["n_table_support"] == result["m_table_support"] == 10_201
    assert result["excluded_context_count"] == 0
    assert result["open_context_count"] == 97_682_000
    assert result["actual_combined_charge"] == 78_437_038
    assert result["next_gate"]["feature"] == ("level25_heisenberg_augmented_four_block_ad5_deeper_shift_census")
    artifact = tmp_path / "artifacts/research/level25-ad5-prefix-shift-census.md"
    text = artifact.read_text(encoding="utf-8")
    rendered_receipt = text.split("```json\n", 1)[1].split("\n```", 1)[0]
    assert result["receipt_sha256"] == hashlib.sha256(rendered_receipt.encode()).hexdigest()
    assert result["receipt_sha256"] in text
    assert text.endswith("LEVEL25_AD5_PREFIX_SHIFT_CENSUS_COMPLETE\n")


def test_level25_heisenberg_augmented_four_block_ad5_coordinate3_functional_signature_profile():
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    result = level25_heisenberg_augmented_four_block_ad5_coordinate3_functional_signature_profile()

    assert result["schema"] == ("reasoning.level25-heisenberg-ad5-coordinate3-functional-signature-diagnostic.v3")
    assert result["evidence_sha256"] == "4b4eb7eac2fd0f7d4b19e91e424492e9f4e405b0c5872df1217f1c9574f871d8"
    computation = result["computation"]
    assert computation["program_sha256"] == ("d3c8ee2585062a02b155269153b2754d73d82c6c446671dd7f072a59a76c08e6")
    assert computation["transcript_sha256"] == ("1e1fc5c2f117e06f181d5af89dcc428981550be32b2bb9277f55cd1cda2a3d5c")
    assert result["independent_replay"]["program_sha256"] == ("a054e0494e94cde4b0f16c9bd6725723ad1962ad6fe15a8ae5fc2d9f60dde116")
    assert result["independent_replay"]["transcript_sha256"] == ("8327cf3bdcf5404c8c7cc9f19e3a6539c3966ca7303a25919002e4878a63bdce")
    assert computation["feature_definition_sha256"] == ("4c62831c34be7b6f88bcc003e5aea41934fb5628ea4d325f743ef94b349ab4c3")
    assert computation["b_feature_rank"] == computation["c_feature_rank"] == 15
    assert computation["d_signature_count"] == 1_755
    assert computation["a_signature_count"] == 4_420
    assert computation["d_signature_multiplicity_mass"] == 22_100
    assert computation["a_signature_multiplicity_mass"] == 4_420
    assert computation["d_signature_minimum_multiplicity"] == 5
    assert computation["d_signature_maximum_multiplicity"] == 120
    assert computation["a_signature_minimum_multiplicity"] == 1
    assert computation["a_signature_maximum_multiplicity"] == 1
    assert computation["identity_sample_failure_count"] == 0
    assert computation["source_decomposition_verified"] == 1
    assert computation["future_profile_visit_count_per_engine"] == 291_609_500
    assert computation["future_profile_value_operation_count_per_engine"] == 8_748_285_000
    assert computation["future_profile_lower_bound_combined"] == 18_079_789_000
    assert computation["dense_profile_bytes"] == 8_242_408
    assert computation["dense_all_signature_profile_bytes"] == 50_896_869_400
    assert result["actual_combined_charge"] == 135_057_826
    assert result["decision"]["next_gate"] == "CHARACTER_CONTRACTION_REQUIRED"
    assert result["decision"]["coordinate3_census_completed"] is False
    assert result["certificate_verified"] is True


def test_level25_heisenberg_augmented_four_block_ad5_coordinate3_functional_signature_report(
    tmp_path,
):
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    registry = _registry(tmp_path)
    result = registry.invoke(
        "level25_heisenberg_augmented_four_block_ad5_coordinate3_functional_signature_report",
        {
            "path": "research/level25-ad5-coordinate3-functional-signatures.md",
            "terminal_marker": "LEVEL25_AD5_COORDINATE3_FUNCTIONAL_SIGNATURES_COMPLETE",
        },
    )

    assert result["evidence_sha256"] == "4b4eb7eac2fd0f7d4b19e91e424492e9f4e405b0c5872df1217f1c9574f871d8"
    assert result["certificate_verified"] is True
    assert result["decision"]["next_gate"] == "CHARACTER_CONTRACTION_REQUIRED"
    assert result["b_feature_rank"] == result["c_feature_rank"] == 15
    assert result["d_signature_count"] == 1_755
    assert result["a_signature_count"] == 4_420
    assert result["future_profile_visit_count_per_engine"] == 291_609_500
    assert result["future_profile_lower_bound_combined"] == 18_079_789_000
    assert result["actual_combined_diagnostic_charge"] == 135_057_826
    assert result["next_gate"]["feature"] == ("level25_heisenberg_augmented_four_block_ad5_coordinate3_character_contraction")
    artifact = tmp_path / "artifacts/research/level25-ad5-coordinate3-functional-signatures.md"
    text = artifact.read_text(encoding="utf-8")
    rendered_receipt = text.split("```json\n", 1)[1].split("\n```", 1)[0]
    assert result["receipt_sha256"] == hashlib.sha256(rendered_receipt.encode()).hexdigest()
    assert result["receipt_sha256"] in text
    assert text.endswith("LEVEL25_AD5_COORDINATE3_FUNCTIONAL_SIGNATURES_COMPLETE\n")


def test_level25_heisenberg_augmented_four_block_ad5_coordinate3_central_key_profile():
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    result = level25_heisenberg_augmented_four_block_ad5_coordinate3_central_key_profile()

    assert result["schema"] == ("reasoning.level25-heisenberg-ad5-coordinate3-central-key-diagnostic.v2")
    assert result["evidence_sha256"] == "7a3896a48b8e0c1de959856c7f07740df956d63cc568321f4a2052e28c27f403"
    computation = result["computation"]
    assert computation["program_sha256"] == ("18215d957f43c747650885721729482a354f600e028281452c03cef17ad91db7")
    assert computation["mathematical_transcript_sha256"] == ("ba6cb15d0aea5a3863e2e5538bfd31fe2a9a94e51ea2f802d3422c791928c02e")
    assert computation["transcript_sha256"] == hashlib.sha256(computation["transcript"].encode()).hexdigest()
    assert result["independent_replay"]["program_sha256"] == ("e75117f7df3530a928806f903df51ab9b4de8e6c7a7fde34e4a934d7a67d8b29")
    assert result["independent_replay"]["transcript_sha256"] == ("418d00ebd30bfc27cbe81e56f85e4a4f5bfb017da0a608b23ab0c3b276168030")
    assert computation["w_a3_rank"] == 13
    assert computation["d_signature_count"] == 1_755
    assert computation["distinct_sigd_central_key_count"] == 22_100
    assert computation["nonconstant_sigd_fibre_count"] == 1_755
    assert computation["maximum_central_key_multiplicity"] == 1
    assert computation["coordinate_reconstruction_failure_count"] == 0
    assert computation["orientation_sample_failure_count"] == 0
    assert computation["fourier_omega"] == 7
    assert computation["fourier_first_direct"] == computation["fourier_first_character"] == 1
    assert computation["fourier_second_direct"] == computation["fourier_second_character"] == 0
    assert result["actual_combined_charge"] == 211_353_848
    assert result["decision"]["status"] == "D1_D_CENTRAL_KEY_NOT_DETERMINED"
    assert result["decision"]["next_gate"] == ("COORDINATE3_TENSOR_ORBIT_DIAGNOSTIC_REQUIRED")
    assert result["decision"]["full_contraction_authorized"] is False
    assert result["certificate_verified"] is True


def test_level25_heisenberg_augmented_four_block_ad5_coordinate3_central_key_report(
    tmp_path,
):
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    registry = _registry(tmp_path)
    result = registry.invoke(
        "level25_heisenberg_augmented_four_block_ad5_coordinate3_central_key_report",
        {
            "path": "research/level25-ad5-coordinate3-central-key.md",
            "terminal_marker": "LEVEL25_AD5_COORDINATE3_CENTRAL_KEY_COMPLETE",
        },
    )

    assert result["evidence_sha256"] == "7a3896a48b8e0c1de959856c7f07740df956d63cc568321f4a2052e28c27f403"
    assert result["certificate_verified"] is True
    assert result["decision"]["status"] == "D1_D_CENTRAL_KEY_NOT_DETERMINED"
    assert result["w_a3_rank"] == 13
    assert result["d_signature_count"] == 1_755
    assert result["distinct_sigd_central_key_count"] == 22_100
    assert result["nonconstant_sigd_fibre_count"] == 1_755
    assert result["maximum_central_key_multiplicity"] == 1
    assert result["fourier_sign_sentinel_verified"] is True
    assert result["actual_combined_diagnostic_charge"] == 211_353_848
    assert result["next_gate"]["feature"] == ("level25_heisenberg_augmented_four_block_ad5_coordinate3_tensor_orbit_diagnostic")
    artifact = tmp_path / "artifacts/research/level25-ad5-coordinate3-central-key.md"
    text = artifact.read_text(encoding="utf-8")
    rendered_receipt = text.split("```json\n", 1)[1].split("\n```", 1)[0]
    assert result["receipt_sha256"] == hashlib.sha256(rendered_receipt.encode()).hexdigest()
    assert result["receipt_sha256"] in text
    assert text.endswith("LEVEL25_AD5_COORDINATE3_CENTRAL_KEY_COMPLETE\n")


def test_level25_heisenberg_augmented_four_block_ad5_coordinate3_tensor_orbit_profile():
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    result = level25_heisenberg_augmented_four_block_ad5_coordinate3_tensor_orbit_profile()

    assert result["schema"] == ("reasoning.level25-heisenberg-ad5-coordinate3-tensor-orbit-diagnostic.v2")
    assert result["evidence_sha256"] == "8c526e2208aaa8007b63741cbd9f5c4d9f05c3d412033050bfd921df2f84f32e"
    computation = result["computation"]
    assert computation["program_sha256"] == ("81d0acaf96f44f4a56b03c22d99421d58d5bdcacad8c97f29f8a07aedad5ee48")
    assert computation["mathematical_transcript_sha256"] == ("cf2eda13073eef480a70bfdd25033634fc19c4c60731df244de72825df8139f0")
    assert computation["transcript_sha256"] == hashlib.sha256(computation["transcript"].encode()).hexdigest()
    assert result["independent_replay"]["program_sha256"] == ("14edb9a8bbea038a58be8aa3f5550e82eb1c9ad3119310bd48dec454f9e275f5")
    assert result["independent_replay"]["transcript_sha256"] == ("e9e1512304bb89a6956c0d5147d31ec2a84458bcb98662eefb76cc6e4f2b3408")
    assert computation["source_record_count"] == 725
    assert computation["source_covariance_failure_count"] == 0
    assert computation["shape_group_law_check_count"] == 2_762_500
    assert computation["shape_group_law_failure_count"] == 0
    assert computation["quotient_recanonicalization_check_count"] == 575_625
    assert computation["quotient_recanonicalization_failure_count"] == 0
    assert computation["shape_orbit_count"] == 180
    assert computation["shape_orbit_size_5_count"] == 4
    assert computation["shape_orbit_size_25_count"] == 176
    assert computation["central_state_count"] == 97_682_000
    assert computation["central_nonidentity_fixed_count"] == 0
    assert computation["central_action_free"] == 1
    assert computation["central_orbit_count"] == 3_907_280
    assert computation["authorized_central_reduction_factor"] == 25
    assert result["actual_combined_charge"] == 87_225_950
    assert result["decision"]["full_contraction_authorized"] is False
    assert result["certificate_verified"] is True


def test_level25_heisenberg_augmented_four_block_ad5_coordinate3_tensor_orbit_report(
    tmp_path,
):
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    registry = _registry(tmp_path)
    result = registry.invoke(
        "level25_heisenberg_augmented_four_block_ad5_coordinate3_tensor_orbit_report",
        {
            "path": "research/level25-ad5-coordinate3-tensor-orbit.md",
            "terminal_marker": "LEVEL25_AD5_COORDINATE3_TENSOR_ORBIT_COMPLETE",
        },
    )

    assert result["evidence_sha256"] == "8c526e2208aaa8007b63741cbd9f5c4d9f05c3d412033050bfd921df2f84f32e"
    assert result["certificate_verified"] is True
    assert result["decision"]["simultaneous_c5_squared_action_certified"] is True
    assert result["shape_orbit_count"] == 180
    assert result["central_state_count"] == 97_682_000
    assert result["central_orbit_count"] == 3_907_280
    assert result["authorized_central_reduction_factor"] == 25
    assert result["actual_combined_diagnostic_charge"] == 87_225_950
    assert result["next_gate"]["feature"] == (
        "level25_heisenberg_augmented_four_block_ad5_coordinate3_central_orbit_representative_preflight"
    )
    artifact = tmp_path / "artifacts/research/level25-ad5-coordinate3-tensor-orbit.md"
    text = artifact.read_text(encoding="utf-8")
    rendered_receipt = text.split("```json\n", 1)[1].split("\n```", 1)[0]
    assert result["receipt_sha256"] == hashlib.sha256(rendered_receipt.encode()).hexdigest()
    assert result["receipt_sha256"] in text
    assert text.endswith("LEVEL25_AD5_COORDINATE3_TENSOR_ORBIT_COMPLETE\n")


def test_level25_heisenberg_augmented_four_block_ad5_coordinate3_central_orbit_representative_preflight_profile():
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    result = level25_heisenberg_augmented_four_block_ad5_coordinate3_central_orbit_representative_preflight_profile()

    assert result["schema"] == ("reasoning.level25-heisenberg-ad5-coordinate3-central-slice-preflight.v2")
    assert result["evidence_sha256"] == "32b669dd063b82e62fd3d11a62ea3543d2b7d0683666f0c2601f688576912318"
    computation = result["computation"]
    assert computation["program_sha256"] == ("86ecfe0d0a7c6f67c8959df3c1183eeb1b46dc75b0099a8fda6b37654241c9d4")
    assert computation["mathematical_transcript_sha256"] == ("e25dee91c21964e7b13068fd199408782939c52cc615b7856e1ad26796570081")
    assert computation["transcript_sha256"] == hashlib.sha256(computation["transcript"].encode()).hexdigest()
    assert result["independent_replay"]["program_sha256"] == ("b44d46fe5a50e5e24f77e71e00c99fee38328327175523ce6f630819e672cbdf")
    assert result["independent_replay"]["transcript_sha256"] == ("930f78193f5484b57a6fe32d5a5f752ac82f17482ea12bf2db7d0c103494e00d")
    assert computation["shape_cardinality"] == 4_420
    assert computation["action_table_entries"] == 22_100
    assert computation["group_law_check_count"] == 2_762_500
    assert computation["group_law_failure_count"] == 0
    assert computation["cocycle_check_count"] == 2_762_500
    assert computation["cocycle_failure_count"] == 0
    assert computation["unique_b_failure_count"] == 0
    assert computation["r1_order"] == 5
    assert computation["r1_free"] == 1
    assert computation["visited_bits"] == 19_536_400
    assert computation["representative_count"] == 3_907_280
    assert computation["image_visit_count"] == 19_536_400
    assert computation["join_equivariance"] == "UNPROVED"
    assert result["actual_combined_charge"] == 296_332_640
    assert result["decision"]["full_contraction_authorized"] is False
    assert result["certificate_verified"] is True


def test_level25_heisenberg_augmented_four_block_ad5_coordinate3_central_orbit_representative_preflight_report(
    tmp_path,
):
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    registry = _registry(tmp_path)
    result = registry.invoke(
        "level25_heisenberg_augmented_four_block_ad5_coordinate3_central_orbit_representative_preflight_report",
        {
            "path": "research/level25-ad5-coordinate3-central-slice.md",
            "terminal_marker": "LEVEL25_AD5_COORDINATE3_CENTRAL_SLICE_PREFLIGHT_COMPLETE",
        },
    )

    assert result["evidence_sha256"] == "32b669dd063b82e62fd3d11a62ea3543d2b7d0683666f0c2601f688576912318"
    assert result["certificate_verified"] is True
    assert result["decision"]["central_slice_enumerator_certified"] is True
    assert result["decision"]["join_equivariance"] == "UNPROVED"
    assert result["decision"]["full_contraction_authorized"] is False
    assert result["slice_cardinality"] == 19_536_400
    assert result["representative_count"] == 3_907_280
    assert result["actual_combined_diagnostic_charge"] == 296_332_640
    assert result["next_gate"]["feature"] == ("level25_heisenberg_augmented_four_block_ad5_coordinate3_join_record_commutation_preflight")
    artifact = tmp_path / "artifacts/research/level25-ad5-coordinate3-central-slice.md"
    text = artifact.read_text(encoding="utf-8")
    rendered_receipt = text.split("```json\n", 1)[1].split("\n```", 1)[0]
    assert result["receipt_sha256"] == hashlib.sha256(rendered_receipt.encode()).hexdigest()
    assert result["receipt_sha256"] in text
    assert text.endswith("LEVEL25_AD5_COORDINATE3_CENTRAL_SLICE_PREFLIGHT_COMPLETE\n")


def test_level25_heisenberg_augmented_four_block_ad5_coordinate3_join_partition_covariance_profile():
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    result = level25_heisenberg_augmented_four_block_ad5_coordinate3_join_partition_covariance_profile()

    assert result["schema"] == ("reasoning.level25-heisenberg-ad5-coordinate3-join-partition-covariance.v2")
    assert result["evidence_sha256"] == "c9b5beeb78f48c3f05ef7bab2876c1c7ed5d88555c0084110d0299457cf58fb8"
    computation = result["computation"]
    assert computation["program_sha256"] == ("57616d00c957d292d9403fb5646db843d3835f229f48b0f3d662f7998ff720fe")
    assert computation["mathematical_transcript_sha256"] == ("58e6d8156bb4f2753dcd5615fb5d2146a88e6921429e8a581d9205ce4d667430")
    assert computation["transcript_sha256"] == hashlib.sha256(computation["transcript"].encode()).hexdigest()
    assert result["independent_replay"]["program_sha256"] == ("e4678cbc4a45254f317e5b1b88d47afd2119739159d8fe0c05d068d3566a2986")
    assert result["independent_replay"]["transcript_sha256"] == ("fcd4fe10127357daa8087f31a38023d7e15715e9f4c85bfce9b0ad93a97fcb5f")
    assert computation["source_record_count"] == 725
    assert computation["restricted_source_record_count"] == 535
    assert computation["effective_partition_record_count"] == 195
    assert computation["translation_check_count"] == 3_625
    assert computation["restricted_translation_check_count"] == 2_675
    assert computation["phase_tuple_classification_count"] == 625
    assert computation["phase_kernel_tuple_count"] == 125
    assert computation["c_scale_bijection_check_count"] == 125
    assert computation["b_scale_bijection_check_count"] == 15_625
    assert computation["state_id_check_count"] == 132_600
    assert computation["join_sentinel_collision_filter_count"] == 1
    assert computation["source_partition_failure_count"] == 0
    assert computation["translation_failure_count"] == 0
    assert computation["phase_kernel_failure_count"] == 0
    assert computation["b_scale_bijection_failure_count"] == 0
    assert computation["state_id_failure_count"] == 0
    assert computation["join_sentinel_failure_count"] == 0
    assert result["actual_combined_charge"] == 5_117_520
    assert result["decision"]["representative_count"] == 3_907_280
    assert result["decision"]["representative_outer_domain_authorized"] is True
    assert result["decision"]["join_execution_completed"] is False
    assert result["decision"]["common_zero_found"] is False
    assert result["certificate_verified"] is True


def test_level25_heisenberg_augmented_four_block_ad5_coordinate3_join_partition_covariance_report(
    tmp_path,
):
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    registry = _registry(tmp_path)
    result = registry.invoke(
        "level25_heisenberg_augmented_four_block_ad5_coordinate3_join_partition_covariance_report",
        {
            "path": "research/level25-ad5-coordinate3-join-covariance.md",
            "terminal_marker": "LEVEL25_AD5_COORDINATE3_JOIN_COVARIANCE_COMPLETE",
        },
    )

    assert result["evidence_sha256"] == "c9b5beeb78f48c3f05ef7bab2876c1c7ed5d88555c0084110d0299457cf58fb8"
    assert result["certificate_verified"] is True
    assert result["decision"]["five_key_join_predicate_equivariant"] is True
    assert result["decision"]["direct_residual_predicate_equivariant"] is True
    assert result["decision"]["join_execution_completed"] is False
    assert result["representative_count"] == 3_907_280
    assert result["actual_combined_diagnostic_charge"] == 5_117_520
    assert result["next_gate"]["feature"] == ("level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_batch_profile")
    artifact = tmp_path / "artifacts/research/level25-ad5-coordinate3-join-covariance.md"
    text = artifact.read_text(encoding="utf-8")
    rendered_receipt = text.split("```json\n", 1)[1].split("\n```", 1)[0]
    assert result["receipt_sha256"] == hashlib.sha256(rendered_receipt.encode()).hexdigest()
    assert result["receipt_sha256"] in text
    assert text.endswith("LEVEL25_AD5_COORDINATE3_JOIN_COVARIANCE_COMPLETE\n")


def test_level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_batch_profile():
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    result = level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_batch_profile()

    assert result["schema"] == ("reasoning.level25-heisenberg-ad5-coordinate3-representative-join-batch.v2")
    assert result["evidence_sha256"] == "2714b93ca2b26587ff4dc1b08d38728892166bbc8f5c2864f75194eb6649c9f9"
    computation = result["computation"]
    replay = result["independent_sorted_join_replay"]
    assert computation["program_sha256"] == "d67863f733b843e71fb7ebd5325263e144c94a1805897900df4c4ddf6009aa2f"
    assert computation["transcript_sha256"] == hashlib.sha256(computation["transcript"].encode()).hexdigest()
    assert computation["mathematical_transcript_sha256"] == "f1f2b101e36a4bb764458d7c4d5305f83adc84bfeef8903de2b17c95915e4077"
    assert replay["program_sha256"] == "2c27e0bcc2428ddb3b8598de98fd9fa9c46c3c227eb2590601d91d4efa0d299e"
    assert replay["transcript_sha256"] == hashlib.sha256(replay["transcript"].encode()).hexdigest()
    assert replay["mathematical_transcript_sha256"] == "8e83caa5c96e30f25ec55638fee611d162e7768d8e53c3eaa06c18986ce90882"
    assert result["input_manifest_sha256"] == ("bfb8d879f52de20deaed7f2caed917486aaff0d3c3bacced2802492597b3a3a7")
    assert result["representative_prefix_sha256"] == ("41110fdb035bd1429a43bb99379e7e59b86861c11c4d1ce98ee78acdd59f9586")
    assert result["actual_combined_charge"] == 409_501_621
    assert result["decision"]["packed_representatives_exhausted"] == [5, 6, 7, 8]
    assert result["decision"]["public_next_unaccepted_packed"] == 9
    assert result["decision"]["exact_key_match_counts"] == [0, 0, 1, 0]
    assert result["decision"]["residual_survivor_count"] == 0
    assert result["decision"]["common_zero_found"] is False
    assert result["certificate_verified"] is True


def test_level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_batch_report(
    tmp_path,
):
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    registry = _registry(tmp_path)
    result = registry.invoke(
        "level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_batch_report",
        {
            "path": "research/level25-ad5-coordinate3-representative-join-batch-5-8.md",
            "terminal_marker": "LEVEL25_AD5_REPRESENTATIVE_JOIN_BATCH_5_8_COMPLETE",
        },
    )

    assert result["evidence_sha256"] == "2714b93ca2b26587ff4dc1b08d38728892166bbc8f5c2864f75194eb6649c9f9"
    assert result["certificate_verified"] is True
    assert result["decision"]["public_cursor_start"] == 5
    assert result["decision"]["public_next_unaccepted_packed"] == 9
    assert result["decision"]["exact_key_match_counts"] == [0, 0, 1, 0]
    assert result["primary_charged_operations"] == 199_973_829
    assert result["replay_charged_operations"] == 209_527_792
    assert result["actual_combined_charge"] == 409_501_621
    assert result["next_gate"]["feature"].endswith("representative_join_batch_9_12_profile")
    artifact = tmp_path / "artifacts/research/level25-ad5-coordinate3-representative-join-batch-5-8.md"
    text = artifact.read_text(encoding="utf-8")
    receipt = json.loads(text.split("```json\n", 1)[1].split("\n```", 1)[0])
    assert result["receipt_sha256"] == hashlib.sha256(canonical_json(receipt).encode()).hexdigest()
    assert result["receipt_sha256"] in text
    assert text.endswith("LEVEL25_AD5_REPRESENTATIVE_JOIN_BATCH_5_8_COMPLETE\n")


def test_level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_batch_9_12_profile():
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    result = level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_batch_9_12_profile()

    assert result["schema"] == ("reasoning.level25-heisenberg-ad5-coordinate3-representative-join-batch-9-12.v2")
    assert result["evidence_sha256"] == "bd0bfbe69db2eeb922529526e3c4c64742e7e62b272cdf3e910123e58864f944"
    computation = result["computation"]
    replay = result["independent_sorted_join_replay"]
    assert computation["program_sha256"] == "03e6337fda02411e8c7b4fdc28a96be45140bb65646bbfc1cd01834fa3f24212"
    assert computation["transcript_sha256"] == hashlib.sha256(computation["transcript"].encode()).hexdigest()
    assert computation["mathematical_transcript_sha256"] == "6f542b95f7d65b2b896a627ed01b3c4ca2ab17bb9265d924dfe4ef393fae65b7"
    assert replay["program_sha256"] == "4b150a79c49d8ff026eb466eab50c78cef9cdd46fa7e327d567d9fa0cffe5e92"
    assert replay["transcript_sha256"] == hashlib.sha256(replay["transcript"].encode()).hexdigest()
    assert replay["mathematical_transcript_sha256"] == "367c3d755b00534d6963c3604e9a9d5e6d76e4ec4c8bde02709555895b249773"
    assert result["input_manifest_sha256"] == ("08d25cba110432f8879cf2d80fcd75a5e6ad510c8bd23037d86ce4cc45624e49")
    assert result["representative_prefix_sha256"] == ("ab05187e3e6c62cafd393d94d16756c92baa7b987c6e51c67b14a98e25b12da8")
    assert result["actual_combined_charge"] == 409_483_494
    assert result["decision"]["packed_representatives_exhausted"] == [9, 10, 11, 12]
    assert result["decision"]["public_next_unaccepted_packed"] == 13
    assert result["decision"]["exact_key_match_counts"] == [0, 1, 1, 0]
    assert result["decision"]["residual_survivor_count"] == 0
    assert result["decision"]["common_zero_found"] is False
    assert result["certificate_verified"] is True


def test_level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_batch_9_12_report(
    tmp_path,
):
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    registry = _registry(tmp_path)
    result = registry.invoke(
        "level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_batch_9_12_report",
        {
            "path": "research/level25-ad5-coordinate3-representative-join-batch-9-12.md",
            "terminal_marker": "LEVEL25_AD5_REPRESENTATIVE_JOIN_BATCH_9_12_COMPLETE",
        },
    )

    assert result["evidence_sha256"] == "bd0bfbe69db2eeb922529526e3c4c64742e7e62b272cdf3e910123e58864f944"
    assert result["certificate_verified"] is True
    assert result["decision"]["public_cursor_start"] == 9
    assert result["decision"]["public_next_unaccepted_packed"] == 13
    assert result["decision"]["exact_key_match_counts"] == [0, 1, 1, 0]
    assert result["primary_charged_operations"] == 199_976_430
    assert result["replay_charged_operations"] == 209_507_064
    assert result["actual_combined_charge"] == 409_483_494
    assert result["next_gate"]["feature"].endswith("representative_join_batch_13_16_profile")
    artifact = tmp_path / "artifacts/research/level25-ad5-coordinate3-representative-join-batch-9-12.md"
    text = artifact.read_text(encoding="utf-8")
    receipt = json.loads(text.split("```json\n", 1)[1].split("\n```", 1)[0])
    assert result["receipt_sha256"] == hashlib.sha256(canonical_json(receipt).encode()).hexdigest()
    assert result["receipt_sha256"] in text
    assert text.endswith("LEVEL25_AD5_REPRESENTATIVE_JOIN_BATCH_9_12_COMPLETE\n")


@pytest.fixture(scope="module")
def ad5_sweep_profile():
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")
    return level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_batch_13_16_profile()


def test_level25_ad5_representative_join_batch_13_16_sweep_profile(ad5_sweep_profile):
    result = ad5_sweep_profile
    bundle = result["bundle"]
    primary = bundle["primary_engine_receipt"]
    replay = bundle["sorted_engine_receipt"]
    pair = bundle["paired_subreceipt"]

    validate_representative_bundle(bundle)
    assert result["schema"] == ("reasoning.level25-heisenberg-ad5-representative-join-batch-13-16.v2.2")
    assert result["evidence_sha256"] == "af8541f6d8b8d7ba054af7c80a683048b2f85d59c14179c99e839ce1ed4ba525"
    assert result["computation"]["program_sha256"] == "0a4d01d419d9e93330b5d1e1d9e75e5b01754d55b355091608a2c28bdbbd4233"
    assert result["computation"]["transcript_sha256"] == hashlib.sha256(result["computation"]["transcript"].encode()).hexdigest()
    assert result["computation"]["mathematical_transcript_sha256"] == "803a6e042658172dfe3a5236b134ab12ee7519c0ba4622fc4013e1cab4fef5f4"
    assert result["independent_sorted_join_replay"]["program_sha256"] == (
        "ebb5488f30aa949e7695d2f798d041a40fcf29d473ad699192e7be27d26aff14"
    )
    assert result["independent_sorted_join_replay"]["transcript_sha256"] == hashlib.sha256(
        result["independent_sorted_join_replay"]["transcript"].encode()
    ).hexdigest()
    assert result["independent_sorted_join_replay"]["mathematical_transcript_sha256"] == (
        "47aaa96db389e010afa3f412ffc3ff316f562a325144f7d88fe8b125b2bcfc1a"
    )
    assert primary["engine"]["engine_source_sha256"] != replay["engine"]["engine_source_sha256"]
    assert primary["engine"]["engine_executable_sha256"] != replay["engine"]["engine_executable_sha256"]
    assert [context["ordinal"] for context in primary["contexts"]] == [13, 14, 15, 16]
    assert [context["d_code"] for context in primary["contexts"]] == [2549, 2553, 2574, 2627]
    assert [context["exact_key_match_count"] for context in primary["contexts"]] == [0, 0, 0, 1]
    assert [context["residual_tests_completed"] for context in primary["contexts"]] == [0, 0, 0, 1]
    assert len({context["b_keyed_records_sha256"] for context in primary["contexts"]}) == 4
    assert len({context["c_keyed_records_sha256"] for context in primary["contexts"]}) == 4
    for left, right in zip(primary["contexts"], replay["contexts"], strict=True):
        assert left["b_keyed_records_sha256"] == right["b_keyed_records_sha256"]
        assert left["c_keyed_records_sha256"] == right["c_keyed_records_sha256"]
        assert left["equal_key_pairs_sha256"] == right["equal_key_pairs_sha256"]
        assert left["match_stream_sha256"] == right["match_stream_sha256"]
        assert left["residual_survivors_sha256"] == right["residual_survivors_sha256"]
    assert pair["terminal_status"] == "ACCEPTED_EMPTY"
    assert pair["aggregate"]["primary_charge_total"] == 212_702_345
    assert pair["aggregate"]["sorted_charge_total"] == 222_239_807
    assert pair["aggregate"]["combined_charge_total"] == 434_942_152
    assert result["decision"]["public_next_unaccepted_packed"] == 13
    assert result["decision"]["next_ordinal_if_committed"] == 17
    assert result["certificate_verified"] is True


def test_level25_ad5_sweep_hash_cursor_resource_and_witness_faults(ad5_sweep_profile):
    original = ad5_sweep_profile["bundle"]

    truncated = copy_module.deepcopy(original)
    truncated["source_manifest"]["source_records_sha256"] = "6c1968b4"
    with pytest.raises(ValueError, match="complete lowercase SHA-256"):
        validate_representative_bundle(truncated)

    permuted = copy_module.deepcopy(original)
    permuted["paired_subreceipt"]["cursor"] = {
        "start_ordinal": 13,
        "end_ordinal_exclusive": 18,
        "context_count": 4,
    }
    permuted["paired_subreceipt"] = _seal(permuted["paired_subreceipt"])
    with pytest.raises(ValueError, match="gap, overlap, or invalid size"):
        validate_representative_bundle(permuted)

    for status, expected in (
        ("ENGINE_RESOURCE_BLOCKED", "RESOURCE_BLOCKED"),
        ("ENGINE_SURVIVOR_FOUND", "WITNESS_PENDING"),
    ):
        fault = copy_module.deepcopy(original)
        fault["primary_engine_receipt"]["terminal_status"] = status
        fault["primary_engine_receipt"] = _seal(fault["primary_engine_receipt"])
        fault["paired_subreceipt"] = _pair_receipt(
            fault["primary_engine_receipt"],
            fault["sorted_engine_receipt"],
            fault["paired_subreceipt"]["source_manifest_sha256"],
            prior_profile=fault["prior_profile"],
        )
        validate_representative_bundle(fault)
        assert fault["paired_subreceipt"]["terminal_status"] == expected
        assert fault["paired_subreceipt"]["next_ordinal_if_accepted"] == 13


def test_level25_ad5_sweep_macroreceipt_checks_sixty_four_context_arithmetic(ad5_sweep_profile):
    first = copy_module.deepcopy(ad5_sweep_profile["bundle"]["paired_subreceipt"])
    pairs = [first]
    for _ in range(15):
        prior = pairs[-1]
        current = copy_module.deepcopy(first)
        start = prior["next_ordinal_if_accepted"]
        current["prior"] = {
            "accepted_receipt_sha256": prior["canonical_payload_sha256"],
            "accepted_receipt_artifact_sha256": "0" * 64,
            "next_ordinal": start,
        }
        current["cursor"] = {
            "start_ordinal": start,
            "end_ordinal_exclusive": start + 4,
            "context_count": 4,
        }
        current["next_ordinal_if_accepted"] = start + 4
        pairs.append(_seal(current))

    macro = build_ad5_representative_macroreceipt(pairs)
    assert macro["cursor"] == {
        "start_ordinal": 13,
        "end_ordinal_exclusive": 77,
        "context_count": 64,
    }
    assert macro["aggregate"]["primary_b_states_completed"] == 64 * 110_500
    assert macro["aggregate"]["sorted_b_states_completed"] == 64 * 110_500
    assert macro["aggregate"]["primary_c_states_completed"] == 64 * 22_100
    assert macro["aggregate"]["sorted_c_states_completed"] == 64 * 22_100
    assert macro["cursor_advancement_owned_by_macro"] is False
    assert macro["mathematical_coverage_verified"] is False
    with pytest.raises(ValueError, match="not a consecutive receipt chain|repeats"):
        build_ad5_representative_macroreceipt([pairs[0], pairs[0]])
    with pytest.raises(ValueError, match="not a consecutive receipt chain"):
        build_ad5_representative_macroreceipt([pairs[1], pairs[0]])


def test_level25_ad5_sweep_report_and_exactly_once_chain_commit(
    tmp_path,
    monkeypatch,
    ad5_sweep_profile,
):
    import reasoning_ledger.agents.project_tools as project_tools

    monkeypatch.setattr(
        project_tools,
        "level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_batch_13_16_profile",
        lambda: ad5_sweep_profile,
    )
    registry = _registry(tmp_path)
    receipt_path = "research/ad5-v21-13-16.md"
    chain_tip_path = "research/ad5-v21-chain-tip.json"

    report = registry.invoke(
        "level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_batch_13_16_report",
        {
            "path": receipt_path,
            "terminal_marker": "LEVEL25_AD5_V21_PAIR_13_16_COMPLETE",
        },
    )
    assert report["certificate_verified"] is True
    assert report["chain_committed"] is False
    assert not (tmp_path / "artifacts" / chain_tip_path).exists()

    summary = registry.invoke(
        "level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_receipt_summary",
        {"path": receipt_path},
    )
    assert summary["bundle_validated"] is True
    assert summary["pair"]["receipt_sha256"] == report["paired_receipt_sha256"]
    assert summary["pair"]["terminal_status"] == "ACCEPTED_EMPTY"
    assert summary["pair"]["aggregate"]["combined_charge_total"] == 434_942_152
    assert [context["exact_key_match_count"] for context in summary["primary_engine"]["contexts"]] == [0, 0, 0, 1]
    assert summary["artifact"]["bytes"] < 100_000

    committed = registry.invoke(
        "level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_batch_13_16_commit",
        {"receipt_path": receipt_path, "chain_tip_path": chain_tip_path},
    )
    assert committed["status"] == "COMMITTED"
    assert committed["chain_tip"]["next_ordinal"] == 17
    tip_sha256 = committed["chain_tip_artifact"]["sha256"]

    retried = registry.invoke(
        "level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_batch_13_16_commit",
        {"receipt_path": receipt_path, "chain_tip_path": chain_tip_path},
    )
    assert retried["status"] == "ALREADY_COMMITTED"
    assert retried["chain_tip_artifact"]["sha256"] == tip_sha256

    duplicate = copy_module.deepcopy(ad5_sweep_profile)
    duplicate["computation"]["wall_seconds"] += 1
    duplicate = _profile_from_execution(
        duplicate["bundle"]["prior_profile"], duplicate["computation"], duplicate["independent_sorted_join_replay"],
    )
    duplicate_json = canonical_json(duplicate)
    registry.invoke(
        "artifact_write",
        {
            "path": "research/ad5-v21-duplicate.md",
            "text": f"```json\n{duplicate_json}\n```\n",
        },
    )
    with pytest.raises(ValueError, match="prior does not match|quarantined"):
        registry.invoke(
            "level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_batch_13_16_commit",
            {
                "receipt_path": "research/ad5-v21-duplicate.md",
                "chain_tip_path": chain_tip_path,
                "expected_chain_tip_sha256": tip_sha256,
            },
        )
    assert hashlib.sha256((tmp_path / "artifacts" / chain_tip_path).read_bytes()).hexdigest() == tip_sha256

    macro = registry.invoke(
        "level25_heisenberg_augmented_four_block_ad5_coordinate3_representative_join_macro_report",
        {
            "subreceipt_paths": [receipt_path],
            "path": "research/ad5-v21-macro.md",
            "terminal_marker": "LEVEL25_AD5_V21_MACRO_COMPLETE",
        },
    )
    assert macro["cursor"]["context_count"] == 4
    assert macro["cursor_advancement_owned_by_macro"] is False


def test_level25_heisenberg_augmented_four_block_row0_feasibility_profile():
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    result = level25_heisenberg_augmented_four_block_row0_feasibility_profile()

    assert result["schema"] == ("reasoning.level25-heisenberg-augmented-four-block-row0-feasibility-profile.v2")
    assert result["unary_evidence_sha256"] == ("f39b6bf089f400ef9677502a4b5caee5173071136795abb0c113ef747bf0f542")
    computation = result["computation"]
    assert computation["program_sha256"] == ("12550321269b7111d9567b190637c89e8cf2c788030d0b11f5f80068b3738d71")
    assert computation["transcript_sha256"] == ("f9119e90096afaa08760714f04bf74a2b93c40f6bdaf2d1bcaa77ba72a0d12b6")
    assert computation["row0_unary_kernel_factor"] == 62
    assert computation["field_orbit_count"] == 21
    assert computation["edge_value_support"] == 65
    assert computation["half_pair_record_generation_count"] == 165865150
    assert computation["prospective_probe_counts"] == [
        225214888665,
        224988410335,
        165050497755,
        181415609245,
        195931379930,
    ]
    assert result["decision"]["status"] == "ROW0_HASH_JOIN_REJECTED"
    assert result["certificate_verified"] is True


def test_level25_heisenberg_augmented_four_block_row0_feasibility_report(tmp_path):
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    registry = _registry(tmp_path)
    result = registry.invoke(
        "level25_heisenberg_augmented_four_block_row0_feasibility_report",
        {
            "path": "research/level25-augmented-four-block-row0-feasibility.md",
            "terminal_marker": "LEVEL25_AUGMENTED_FOUR_BLOCK_ROW0_FEASIBILITY_COMPLETE",
        },
    )

    assert result["evidence_sha256"] == ("e904ad668cb66292c867f01d7a018a0a37af44ee3d9a572349bb652b4e4a87d7")
    assert result["certificate_verified"] is True
    artifact = tmp_path / "artifacts/research/level25-augmented-four-block-row0-feasibility.md"
    text = artifact.read_text(encoding="utf-8")
    assert result["receipt_sha256"] in text
    assert text.endswith("LEVEL25_AUGMENTED_FOUR_BLOCK_ROW0_FEASIBILITY_COMPLETE\n")


def test_level25_heisenberg_augmented_four_block_row0_label_unary_rank_profile():
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    result = level25_heisenberg_augmented_four_block_row0_label_unary_rank_profile()

    assert result["schema"] == ("reasoning.level25-heisenberg-augmented-four-block-row0-label-unary-rank-profile.v2")
    assert result["row0_feasibility_evidence_sha256"] == ("e904ad668cb66292c867f01d7a018a0a37af44ee3d9a572349bb652b4e4a87d7")
    computation = result["computation"]
    assert computation["program_sha256"] == ("3690222f6379b9927e62492b30742ecf04cd760c989d4ed92e78f355696127e6")
    assert computation["transcript_sha256"] == ("c6bd22105af9263e111c8d6f182e65b73453dc50ab72ca94e67083e675139aaa")
    assert computation["orbit_representatives"] == [
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
    ]
    assert computation["populated_label_counts"] == [
        351,
        622,
        601,
        620,
        602,
        572,
        562,
        601,
        540,
        541,
        584,
        602,
        620,
        561,
        571,
        551,
        621,
        551,
        622,
        540,
        601,
    ]
    assert computation["parity_ranks"] == [
        346,
        622,
        601,
        620,
        602,
        572,
        562,
        601,
        540,
        541,
        584,
        602,
        620,
        561,
        571,
        551,
        621,
        551,
        622,
        540,
        601,
    ]
    assert result["engineering_caps"]["conservative_rank_word_operation_bound"] == 793288125
    assert result["rational_rank_conclusions"]["nonzero_bucket_orbit_ranks_are_exact"] is True
    assert result["decision"]["status"] == "ROW0_LABEL_UNARY_LOW_RANK_REJECTED"
    assert result["certificate_verified"] is True


def test_level25_heisenberg_augmented_four_block_row0_label_unary_rank_report(tmp_path):
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    registry = _registry(tmp_path)
    result = registry.invoke(
        "level25_heisenberg_augmented_four_block_row0_label_unary_rank_report",
        {
            "path": "research/level25-augmented-four-block-row0-label-unary-rank.md",
            "terminal_marker": "LEVEL25_AUGMENTED_FOUR_BLOCK_ROW0_LABEL_UNARY_RANK_COMPLETE",
        },
    )

    assert result["evidence_sha256"] == ("5e6264db7b66415510212f1c4468b3e85a5860c4b78642825f7e4b2dac29a100")
    assert result["certificate_verified"] is True
    artifact = tmp_path / "artifacts/research/level25-augmented-four-block-row0-label-unary-rank.md"
    text = artifact.read_text(encoding="utf-8")
    assert result["receipt_sha256"] in text
    assert text.endswith("LEVEL25_AUGMENTED_FOUR_BLOCK_ROW0_LABEL_UNARY_RANK_COMPLETE\n")


def test_level25_heisenberg_augmented_four_block_row0_kernel_rank_profile():
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    result = level25_heisenberg_augmented_four_block_row0_kernel_rank_profile()

    assert result["schema"] == ("reasoning.level25-heisenberg-augmented-four-block-row0-kernel-rank-profile.v2")
    assert result["label_unary_rank_evidence_sha256"] == ("5e6264db7b66415510212f1c4468b3e85a5860c4b78642825f7e4b2dac29a100")
    computation = result["computation"]
    assert computation["program_sha256"] == ("807824994a614ef193ce4718bbef0c83a612f95116fc1855e45222c7661ac592")
    assert computation["transcript_sha256"] == ("0ab99cdfb5284274d209d538048033eec615a25630a2adc002c5899f1395406a")
    assert computation["transform_moduli"] == [
        1449072215711168621,
        1449072215711180741,
    ]
    assert computation["support_counts_modulus_1"] == [1] + [625] * 20
    assert computation["support_counts_modulus_2"] == [1] + [625] * 20
    assert computation["formula_failure_count"] == 0
    assert computation["kernel_transport_failure_count"] == 0
    assert computation["runtime_address_space_limit_bytes"] == 64 * 1024**2
    independent = result["independent_python_replay"]
    assert independent["program_sha256"] == ("2bd49404ad30858f81f723db992e4e265085591cf701374e8a798d595beebfa4")
    assert independent["transcript_sha256"] == ("07b1484a526ddb14b7aa2126857fd95acb00bcfbfe45efd827d27c13fe50393a")
    assert independent["runtime_address_space_limit_bytes"] == 64 * 1024**2
    assert result["engineering_caps"]["conservative_named_diagnostic_charge_bound"] == 433796220
    assert result["fiber_transport"]["representative_fiber_count"] == 1344
    assert result["decision"]["status"] == "ROW0_PHASE_KERNEL_FOURIER_LOW_RANK_REJECTED"
    assert result["certificate_verified"] is True


def test_level25_heisenberg_augmented_four_block_row0_kernel_rank_report(tmp_path):
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    registry = _registry(tmp_path)
    result = registry.invoke(
        "level25_heisenberg_augmented_four_block_row0_kernel_rank_report",
        {
            "path": "research/level25-augmented-four-block-row0-kernel-rank.md",
            "terminal_marker": "LEVEL25_AUGMENTED_FOUR_BLOCK_ROW0_KERNEL_RANK_COMPLETE",
        },
    )

    assert result["evidence_sha256"] == ("972baf6f162875276e315ffa92421714df8a10418acc8e62e819df787ca13f18")
    assert result["certificate_verified"] is True
    assert "receipt" not in result
    artifact = tmp_path / "artifacts/research/level25-augmented-four-block-row0-kernel-rank.md"
    text = artifact.read_text(encoding="utf-8")
    assert result["receipt_sha256"] in text
    assert text.endswith("LEVEL25_AUGMENTED_FOUR_BLOCK_ROW0_KERNEL_RANK_COMPLETE\n")


def test_level25_heisenberg_augmented_four_block_row0_fixed_fiber_pair_calibration_profile():
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    result = level25_heisenberg_augmented_four_block_row0_fixed_fiber_pair_calibration_profile()

    assert result["schema"] == ("reasoning.level25-heisenberg-augmented-four-block-row0-fixed-fiber-pair-calibration-profile.v2")
    assert result["kernel_rank_evidence_sha256"] == ("972baf6f162875276e315ffa92421714df8a10418acc8e62e819df787ca13f18")
    computation = result["computation"]
    assert computation["program_sha256"] == ("69d2ae72c2056f6aead3f0f72fc8051cb6bf68e49f7d9667112a6ea0823def9d")
    assert computation["transcript_sha256"] == ("0bbfd2df77e9d8089b55b4af6e7cad18108bbf89ce22eb1bae1ace8de1217c0c")
    assert computation["zero_bucket_population"] == 4420
    assert computation["pair_visits"] == [19_536_400, 19_536_400]
    assert computation["raw_masses"] == [19_536_400, 19_536_400]
    assert computation["phase_masses"] == [97_682_000, 97_682_000]
    assert computation["raw_support_counts"] == [17_882_489, 17_872_191]
    assert computation["phase_support_counts"] == [74_352_885, 74_303_350]
    assert computation["phase_collision_merge_counts"] == [15_059_560, 15_057_605]
    assert computation["raw_transport_failure_counts"] == [0, 0]
    replay = result["independent_compiled_replay"]
    assert replay["program_sha256"] == ("b228f6087eb271c512f63451ce9d6936258476cd0c70aa98b06e0c6a20a8775b")
    assert replay["transcript_sha256"] == computation["transcript_sha256"]
    assert result["engineering_caps"]["combined_conservative_record_operation_bound"] == 1_289_392_100
    assert result["decision"]["status"] == "FIXED_FIBER_PAIR_CALIBRATION_VALID"
    assert result["certificate_verified"] is True


def test_level25_heisenberg_augmented_four_block_row0_fixed_fiber_pair_calibration_report(tmp_path):
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    registry = _registry(tmp_path)
    result = registry.invoke(
        "level25_heisenberg_augmented_four_block_row0_fixed_fiber_pair_calibration_report",
        {
            "path": "research/level25-augmented-four-block-row0-fixed-fiber-pair-calibration.md",
            "terminal_marker": "LEVEL25_AUGMENTED_FOUR_BLOCK_ROW0_FIXED_FIBER_PAIR_CALIBRATION_COMPLETE",
        },
    )

    assert result["evidence_sha256"] == ("cb473fbb7a70dacabd981444bc7e582e3cf0523d3fc3ba8e504f6253ea3e15c5")
    assert result["certificate_verified"] is True
    assert "receipt" not in result
    assert result["summary"]["raw_support_counts"] == [17_882_489, 17_872_191]
    assert result["summary"]["phase_support_counts"] == [74_352_885, 74_303_350]
    assert result["summary"]["phase_collision_merge_counts"] == [15_059_560, 15_057_605]
    assert "a crossing B-C join or a row-zero survivor count" in result["summary"]["not_certified"]
    artifact = tmp_path / "artifacts/research/level25-augmented-four-block-row0-fixed-fiber-pair-calibration.md"
    text = artifact.read_text(encoding="utf-8")
    assert result["receipt_sha256"] in text
    assert text.endswith("LEVEL25_AUGMENTED_FOUR_BLOCK_ROW0_FIXED_FIBER_PAIR_CALIBRATION_COMPLETE\n")


def test_level25_heisenberg_augmented_four_block_row0_pair_reuse_profile():
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    result = level25_heisenberg_augmented_four_block_row0_pair_reuse_profile()

    assert result["schema"] == ("reasoning.level25-heisenberg-augmented-four-block-row0-pair-reuse-profile.v2")
    assert result["pair_calibration_evidence_sha256"] == ("cb473fbb7a70dacabd981444bc7e582e3cf0523d3fc3ba8e504f6253ea3e15c5")
    computation = result["computation"]
    assert computation["program_sha256"] == ("9c25091f66fd470347deb0006c3a59b406dae61074d8d9b8b305201ba414f617")
    assert computation["transcript_sha256"] == ("1a5fa047915b4de20a9cce7ecf06252f2d1c08c3a2b833bc010b4f8f8852d51a")
    assert computation["scale_representatives"] == [1, 10, 100, 91]
    assert computation["mu5_bucket_failure_count"] == 0
    assert computation["mu5_row6_failure_count"] == 0
    assert computation["mu5_row8_failure_count"] == 0
    assert computation["non_mu5_mu25_bucket_failure_count"] == 7_812_500
    assert computation["non_mu5_mu25_row6_failure_count"] == 31_250_000
    assert computation["non_mu5_mu25_row8_failure_count"] == 31_250_000
    assert computation["left_dependency_table_count"] == 84
    assert computation["right_dependency_table_count"] == 336
    assert computation["left_direct_pair_visit_count"] == 1_271_302_365
    assert computation["right_direct_pair_visit_count"] == 5_085_209_460
    assert computation["primary_plus_replay_direct_pair_visit_count"] == 12_713_023_650
    replay = result["independent_python_replay"]
    assert replay["program_sha256"] == ("fb82448430c499fbe792e7109468f18b019e9e1cc6739f0ccced97030d28ce77")
    assert replay["transcript_sha256"] == ("90cdd00bd0d1462ee6995e2351773169ceeb31fee07dd2775e4c63e28aadb883")
    assert result["decision"]["status"] == ("PAIR_DEPENDENCY_REUSE_CERTIFIED_DIRECT_SCHEDULE_OVER_CAP")
    assert result["certificate_verified"] is True


def test_level25_heisenberg_augmented_four_block_row0_pair_reuse_report(tmp_path):
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    registry = _registry(tmp_path)
    result = registry.invoke(
        "level25_heisenberg_augmented_four_block_row0_pair_reuse_report",
        {
            "path": "research/level25-augmented-four-block-row0-pair-reuse.md",
            "terminal_marker": "LEVEL25_AUGMENTED_FOUR_BLOCK_ROW0_PAIR_REUSE_COMPLETE",
        },
    )

    assert result["evidence_sha256"] == ("f5cf3ac39d594a70d0f66e2449cfa4db541dc62a2093a29be1e4168ef75dac4a")
    assert result["certificate_verified"] is True
    assert result["summary"]["left_dependency_table_count"] == 84
    assert result["summary"]["right_dependency_table_count"] == 336
    assert result["summary"]["primary_direct_pair_visit_count"] == 6_356_511_825
    assert result["summary"]["primary_plus_replay_direct_pair_visit_count"] == 12_713_023_650
    assert "receipt" not in result
    artifact = tmp_path / "artifacts/research/level25-augmented-four-block-row0-pair-reuse.md"
    text = artifact.read_text(encoding="utf-8")
    assert result["receipt_sha256"] in text
    assert text.endswith("LEVEL25_AUGMENTED_FOUR_BLOCK_ROW0_PAIR_REUSE_COMPLETE\n")


def test_level25_heisenberg_augmented_four_block_row0_edge_indicator_rank_profile():
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    result = level25_heisenberg_augmented_four_block_row0_edge_indicator_rank_profile()

    assert result["schema"] == ("reasoning.level25-heisenberg-augmented-four-block-row0-edge-indicator-rank-profile.v2")
    assert result["pair_reuse_evidence_sha256"] == ("f5cf3ac39d594a70d0f66e2449cfa4db541dc62a2093a29be1e4168ef75dac4a")
    computation = result["computation"]
    assert computation["program_sha256"] == ("015b05c0b80857efb1d18cd0bc8fdd27927f6c45c9a33c2162316bc473697009")
    assert computation["transcript_sha256"] == ("c51f022b6a319ee10cce579ee36740a1a6e4630204b3a9379c7a6d4076e73436")
    assert computation["full_rank_indicator_count"] == 60
    assert computation["defective_indicator_values"] == [40, 45, 61, 70, 92]
    assert computation["minimum_indicator_rank"] == 601
    assert computation["maximum_indicator_rank"] == 625
    assert computation["total_indicator_fourier_support"] == 40_505
    assert computation["maximum_total_indicator_fourier_support"] == 40_625
    replay = result["independent_python_replay"]
    assert replay["program_sha256"] == ("6e5d8af634eb349175937a354203dd79bd8c8f1288f1f958796b7ae22b712724")
    assert replay["transcript_sha256"] == ("cf417fb52cce00b3f91c3767374f3097ceed2f1ced01b7ab6240131ff1d1334e")
    assert result["decision"]["status"] == ("EDGE_INDICATOR_FOURIER_COMPRESSION_REJECTED")
    assert result["certificate_verified"] is True


def test_level25_heisenberg_augmented_four_block_row0_edge_indicator_rank_report(
    tmp_path,
):
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    registry = _registry(tmp_path)
    result = registry.invoke(
        "level25_heisenberg_augmented_four_block_row0_edge_indicator_rank_report",
        {
            "path": "research/level25-augmented-four-block-row0-edge-indicator-rank.md",
            "terminal_marker": "LEVEL25_AUGMENTED_FOUR_BLOCK_ROW0_EDGE_INDICATOR_RANK_COMPLETE",
        },
    )

    assert result["evidence_sha256"] == ("75dc83534947f323ec2c5ef82e3b8fd0a30f3aedbc25b275c6fb595ff2d3b427")
    assert result["certificate_verified"] is True
    assert result["summary"]["full_rank_indicator_count"] == 60
    assert result["summary"]["defective_indicator_values"] == [40, 45, 61, 70, 92]
    assert result["summary"]["minimum_indicator_rank"] == 601
    assert result["summary"]["total_indicator_fourier_support"] == 40_505
    assert result["summary"]["maximum_total_indicator_fourier_support"] == 40_625
    assert "receipt" not in result
    artifact = tmp_path / "artifacts/research/level25-augmented-four-block-row0-edge-indicator-rank.md"
    text = artifact.read_text(encoding="utf-8")
    assert result["receipt_sha256"] in text
    assert text.endswith("LEVEL25_AUGMENTED_FOUR_BLOCK_ROW0_EDGE_INDICATOR_RANK_COMPLETE\n")


def test_level25_heisenberg_balanced_coordinate_rank_profile_exhausts_supports():
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    result = level25_heisenberg_balanced_coordinate_rank_profile()

    assert result["schema"] == ("reasoning.level25-heisenberg-balanced-coordinate-rank-profile.v1")
    assert result["coordinate_support_count"] == 2**25
    assert result["full_support_shift_ranks"] == {
        "0": 14,
        "20": 5,
        "15": 2,
        "5": 1,
    }
    assert result["maximum_certified_affine_rank_defect"] == 3
    assert result["computation"]["pruned_supports"] == 2**25
    assert result["computation"]["violations"] == 0
    assert result["certificate_verified"] is True
    assert "global dimension" in result["not_certified"][1]


def test_level25_heisenberg_balanced_coordinate_rank_report_is_canonical(
    tmp_path,
):
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")
    registry = _registry(tmp_path)
    result = registry.invoke(
        "level25_heisenberg_balanced_coordinate_rank_report",
        {
            "path": "research/level25-heisenberg-balanced-coordinate-rank.md",
            "terminal_marker": "LEVEL25_BALANCED_COORDINATE_RANK_COMPLETE",
        },
    )

    artifact = tmp_path / "artifacts/research/level25-heisenberg-balanced-coordinate-rank.md"
    text = artifact.read_text(encoding="utf-8")
    assert result["receipt"]["certificate_verified"] is True
    assert result["receipt_sha256"] in text
    assert text.endswith("LEVEL25_BALANCED_COORDINATE_RANK_COMPLETE\n")


def test_level25_heisenberg_balanced_two_block_obstruction_is_exact():
    result = level25_heisenberg_balanced_two_block_obstruction_profile()

    assert result["schema"] == ("reasoning.level25-heisenberg-balanced-two-block-obstruction-profile.v2")
    assert result["cyclotomic_witness"]["block_shape_exponents"] == [
        0,
        0,
        20,
        10,
        20,
    ]
    assert result["cyclotomic_witness"]["second_scale_equation"] == ("lambda^25=-zeta^6")
    assert result["finite_field_specialization"] == {
        "field": "algebraic closure of F_101",
        "active_residue_blocks": [0, 1],
        "block_shape": [1, 1, 84, 36, 84],
        "block_scale_twenty_fifth_powers": [1, 30],
        "second_scale_equation": "lambda^25=30",
        "coordinate_rule": ("x_(5q)=u_q, x_(1+5q)=lambda*u_q, and all other coordinates vanish"),
    }
    assert [
        {key: row[key] for key in ("first_residue", "shift_quotient", "value")} for row in result["block_correlations"] if row["value"]
    ] == [{"first_residue": 1, "shift_quotient": 1, "value": 16}]
    assert all(row["value"] == 0 for row in result["equation_evaluations"])
    assert all(not any(row["remainder"]) for row in result["cyclotomic_equation_remainders"])
    assert result["normalized_active_jacobian_rank"] == 9
    assert result["normalized_affine_jacobian_rank"] == 9
    assert result["inactive_jacobian_column_count"] == 15
    assert result["certificate_verified"] is True
    assert "richer coefficient choices" in result["not_certified"][2]


def test_level25_heisenberg_balanced_two_block_obstruction_report_is_canonical(
    tmp_path,
):
    registry = _registry(tmp_path)
    result = registry.invoke(
        "level25_heisenberg_balanced_two_block_obstruction_report",
        {
            "path": "research/level25-heisenberg-balanced-two-block.md",
            "terminal_marker": "LEVEL25_BALANCED_TWO_BLOCK_COMPLETE",
        },
    )

    artifact = tmp_path / "artifacts/research/level25-heisenberg-balanced-two-block.md"
    text = artifact.read_text(encoding="utf-8")
    assert result["receipt"]["normalized_active_jacobian_rank"] == 9
    assert result["receipt_sha256"] in text
    assert text.endswith("LEVEL25_BALANCED_TWO_BLOCK_COMPLETE\n")


def test_level25_heisenberg_balanced_coefficient_pivot_is_exact():
    result = level25_heisenberg_balanced_coefficient_pivot_profile()

    assert result["schema"] == ("reasoning.level25-heisenberg-balanced-coefficient-pivot-profile.v1")
    assert result["target_character"] == [5, 20]
    assert result["pivot_orbit"] == {
        "formula": ("P_(5,20)=sum_(i=0)^24 zeta^(-5i) x_i^20*x_(i+1)^5"),
        "base_monomial": "x_0^20*x_1^5",
        "degree": 25,
        "clock_weight": 5,
        "orbit_clock_weights": [5] * 25,
        "required_clock_weight": 5,
        "fourier_first_character": 5,
        "distinct_from_original_sparse_orbit": True,
    }
    witness = result["cyclotomic_witness"]
    assert witness["pivot_orbit_value"] == "5*lambda^5"
    assert witness["modified_generator_value"] == "5*c*lambda^5"
    assert [row["index"] for row in witness["surviving_pivot_terms"]] == [
        0,
        5,
        10,
        15,
        20,
    ]
    assert result["finite_field_specialization"]["pivot_orbit_value_relative_to_lambda_fifth"] == 5
    assert result["certificate_verified"] is True


def test_level25_heisenberg_balanced_coefficient_pivot_report_is_canonical(
    tmp_path,
):
    registry = _registry(tmp_path)
    result = registry.invoke(
        "level25_heisenberg_balanced_coefficient_pivot_report",
        {
            "path": "research/level25-heisenberg-balanced-coefficient-pivot.md",
            "terminal_marker": "LEVEL25_BALANCED_COEFFICIENT_PIVOT_COMPLETE",
        },
    )

    artifact = tmp_path / "artifacts/research/level25-heisenberg-balanced-coefficient-pivot.md"
    text = artifact.read_text(encoding="utf-8")
    assert result["receipt"]["certificate_verified"] is True
    assert result["receipt_sha256"] in text
    assert text.endswith("LEVEL25_BALANCED_COEFFICIENT_PIVOT_COMPLETE\n")


def test_level25_heisenberg_balanced_pivoted_two_block_obstruction_is_exact():
    if shutil.which("gp") is None:
        pytest.skip("PARI/GP is unavailable")

    result = level25_heisenberg_balanced_pivoted_two_block_obstruction_profile()

    assert result["schema"] == ("reasoning.level25-heisenberg-balanced-pivoted-two-block-obstruction-profile.v1")
    assert result["witness"]["active_residue_blocks"] == [0, 2]
    assert all(not any(row["cyclotomic_remainder"]) and row["finite_field_value"] == 0 for row in result["equation_evaluations"])
    assert result["pivot_vanishing"]["value"] == "0"
    assert result["pivot_vanishing"]["gradient"] == ["0"] * 25
    assert result["exact_cyclotomic_rank"]["program_sha256"] == ("732a2acb15d36cd500c8af23a39fd6366d5b0e5d7707451e58d4ac84552fde4c")
    assert result["exact_cyclotomic_rank"]["transcript_sha256"] == ("4ddd2d64878a1ffea7977911d087684ca87cc7e46fe122aa8eea9b2708152c32")
    assert result["exact_cyclotomic_rank"]["rank"] == 9
    assert result["exact_cyclotomic_rank"]["affine_rank"] == 9
    assert result["finite_field_jacobian_rank"] == 9
    assert result["projective_stabilizer_proof"]["projective_stabilizer"] == [[0, 0]]
    assert result["zero_shift_dimension_bound"] == {
        "zero_shift_character_count": 14,
        "substitution": "y_i=x_i^25",
        "fourier_linear_forms_are_independent": True,
        "finite_free_extension": ("Q(zeta)[y_0,...,y_24] -> Q(zeta)[x_0,...,x_24]"),
        "affine_zero_shift_scheme_dimension": 11,
        "projective_zero_shift_scheme_dimension": 10,
        "pivoted_scheme_local_dimension_upper_bound_at_witness": 10,
        "affine_chart_tangent_dimension_at_witness": 15,
    }
    assert result["certificate_verified"] is True


def test_level25_heisenberg_balanced_pivoted_two_block_report_is_canonical(
    tmp_path,
):
    if shutil.which("gp") is None:
        pytest.skip("PARI/GP is unavailable")

    registry = _registry(tmp_path)
    result = registry.invoke(
        "level25_heisenberg_balanced_pivoted_two_block_obstruction_report",
        {
            "path": "research/level25-heisenberg-pivoted-two-block.md",
            "terminal_marker": "LEVEL25_PIVOTED_TWO_BLOCK_COMPLETE",
        },
    )

    artifact = tmp_path / "artifacts/research/level25-heisenberg-pivoted-two-block.md"
    text = artifact.read_text(encoding="utf-8")
    assert result["receipt_sha256"] == ("fda6f1b95b7803bae96e6f9f7fb112422907d2a2e9dfc45e85a7748c61204fca")
    assert result["receipt_sha256"] in text
    assert text.endswith("LEVEL25_PIVOTED_TWO_BLOCK_COMPLETE\n")


def test_level25_heisenberg_balanced_nonadjacent_coefficient_pivot_is_exact():
    if shutil.which("gp") is None:
        pytest.skip("PARI/GP is unavailable")

    result = level25_heisenberg_balanced_nonadjacent_coefficient_pivot_profile()

    assert result["schema"] == ("reasoning.level25-heisenberg-balanced-nonadjacent-coefficient-pivot-profile.v1")
    assert result["target_character"] == [2, 15]
    assert result["second_pivot_orbit"]["base_monomial"] == ("x_0^2*x_5^13*x_2^10")
    assert result["second_pivot_orbit"]["degree"] == 25
    assert result["second_pivot_orbit"]["clock_weight"] == 10
    assert result["surviving_obstruction_evaluation"]["cyclotomic_exponents_relative_to_lambda_tenth"] == [0] * 5
    assert result["surviving_obstruction_evaluation"]["pivot_orbit_value"] == ("5*lambda^10")
    assert result["certificate_verified"] is True


def test_level25_heisenberg_balanced_nonadjacent_pivot_report_is_canonical(
    tmp_path,
):
    if shutil.which("gp") is None:
        pytest.skip("PARI/GP is unavailable")

    registry = _registry(tmp_path)
    result = registry.invoke(
        "level25_heisenberg_balanced_nonadjacent_coefficient_pivot_report",
        {
            "path": "research/level25-heisenberg-nonadjacent-pivot.md",
            "terminal_marker": "LEVEL25_NONADJACENT_PIVOT_COMPLETE",
        },
    )

    artifact = tmp_path / "artifacts/research/level25-heisenberg-nonadjacent-pivot.md"
    text = artifact.read_text(encoding="utf-8")
    assert result["receipt"]["certificate_verified"] is True
    assert result["receipt_sha256"] in text
    assert text.endswith("LEVEL25_NONADJACENT_PIVOT_COMPLETE\n")


def test_level25_heisenberg_balanced_two_pivot_obstruction_is_exact():
    result = level25_heisenberg_balanced_two_pivot_two_block_obstruction_profile()

    assert result["schema"] == ("reasoning.level25-heisenberg-balanced-two-pivot-two-block-obstruction-profile.v1")
    assert result["witness"]["block_shape_exponents"] == [0, 0, 5, 15, 5]
    assert result["witness"]["scale_equation"] == "lambda^25=-zeta^4"
    assert result["second_pivot_vanishing"]["cyclotomic_exponents_relative_to_lambda_tenth"] == [0, 5, 10, 15, 20]
    assert all(not any(row["cyclotomic_remainder"]) and row["finite_field_value"] == 0 for row in result["sparse_equation_evaluations"])
    assert result["jacobian_rank_bound"]["affine_rank_upper_bound"] == 9
    assert result["jacobian_rank_bound"]["affine_tangent_dimension_lower_bound"] == 15
    assert result["zero_shift_dimension_bound"]["two_pivot_scheme_local_dimension_upper_bound_at_witness"] == 10
    assert result["projective_stabilizer_proof"]["projective_stabilizer"] == [[0, 0]]
    assert result["certificate_verified"] is True


def test_level25_heisenberg_balanced_two_pivot_obstruction_report_is_canonical(
    tmp_path,
):
    registry = _registry(tmp_path)
    result = registry.invoke(
        "level25_heisenberg_balanced_two_pivot_two_block_obstruction_report",
        {
            "path": "research/level25-heisenberg-two-pivot-obstruction.md",
            "terminal_marker": "LEVEL25_TWO_PIVOT_OBSTRUCTION_COMPLETE",
        },
    )

    artifact = tmp_path / "artifacts/research/level25-heisenberg-two-pivot-obstruction.md"
    text = artifact.read_text(encoding="utf-8")
    assert result["receipt_sha256"] == ("f53c46e134d166cda8e7d40a1a0a4eacf36af410d32c75831da3a1e5ee8d8b19")
    receipt = json.loads(text.split("```json\n", 1)[1].split("\n```", 1)[0])
    assert receipt == result["receipt"] == level25_heisenberg_balanced_two_pivot_two_block_obstruction_profile()
    assert result["receipt_sha256"] == hashlib.sha256(canonical_json(receipt).encode()).hexdigest()
    assert result["receipt_sha256"] in text
    assert text.endswith("LEVEL25_TWO_PIVOT_OBSTRUCTION_COMPLETE\n")


def test_level25_heisenberg_nonadjacent_block_shape_cover_is_exact():
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    result = level25_heisenberg_nonadjacent_block_shape_cover_profile()

    assert result["schema"] == ("reasoning.level25-heisenberg-nonadjacent-block-shape-cover-profile.v1")
    assert result["ansatz"]["normalized_shape_count"] == 25**4
    assert result["admissible_shape_count"] == 20
    assert result["candidate_library"]["orbit_count"] == 18
    assert result["candidate_library"]["nonzero_on_census_count"] == 12
    assert result["candidate_library"]["identically_zero_on_census_count"] == 6
    assert len(result["candidate_library"]["distinct_nonempty_coverage_masks"]) == 4
    assert [row["exponent_triple"] for row in result["selected_coefficient_one_orbits"]] == [
        [1, 4, 20],
        [2, 13, 10],
        [4, 6, 15],
        [1, 4, 20],
    ]
    assert [row["character"] for row in result["selected_coefficient_one_orbits"]] == [[2, 15], [2, 15], [2, 15], [4, 15]]
    assert result["cover"] == {
        "covered_shape_count": 20,
        "uncovered_shape_indexes": [],
        "pairwise_overlap_count": 0,
        "minimum_candidate_library_cover_size": 4,
    }
    assert result["computation"]["program_sha256"] == ("92af9706fb863efd8f8764485321d30aacbae69efe1e86057f226c72b035300f")
    assert result["computation"]["transcript_sha256"] == ("7c747ea1423784d15413e33872496c866dcf7806902b2aed68be19c018ed8de6")
    assert result["computation"]["independent_python_recomputation_agrees"] is True
    assert result["certificate_verified"] is True
    assert "unequal or non-torsion" in result["not_certified"][0]


def test_level25_heisenberg_nonadjacent_block_shape_cover_report_is_canonical(
    tmp_path,
):
    if shutil.which("g++") is None:
        pytest.skip("g++ is unavailable")

    registry = _registry(tmp_path)
    result = registry.invoke(
        "level25_heisenberg_nonadjacent_block_shape_cover_report",
        {
            "path": "research/level25-heisenberg-block-shape-cover.md",
            "terminal_marker": "LEVEL25_BLOCK_SHAPE_COVER_COMPLETE",
        },
    )

    artifact = tmp_path / "artifacts/research/level25-heisenberg-block-shape-cover.md"
    text = artifact.read_text(encoding="utf-8")
    assert result["receipt"]["certificate_verified"] is True
    assert result["receipt_sha256"] == ("b569dce56c50540ec987820ae0089208ab1511f231d35b3b98321e9d04881d92")
    assert result["receipt_sha256"] in text
    assert text.endswith("LEVEL25_BLOCK_SHAPE_COVER_COMPLETE\n")


def test_heisenberg_surface_witness_has_exact_character_and_fixed_locus_census():
    result = heisenberg_surface_witness_profile()

    assert result["schema"] == ("reasoning.heisenberg-level5-5-5-surface-witness-profile.v1")
    assert result["request_sha256"] == ("3d56f06188ad5bc6734f3ebc5b49c9c5ccba5abfbe83933ee1501d2eb674e5dd")
    assert result["degree_five_representation"] == {
        "monomial_count": 126,
        "S_orbit_count": 26,
        "character_block_dimensions": {
            "0": 6,
            "1": 5,
            "2": 5,
            "3": 5,
            "4": 5,
        },
        "fixed_monomial": [1, 1, 1, 1, 1],
    }
    assert [equation["T_character"] for equation in result["equations"]] == [
        1,
        2,
    ]
    assert [equation["term_count"] for equation in result["equations"]] == [
        25,
        25,
    ]
    assert [equation["sparse_terms_sha256"] for equation in result["equations"]] == [
        "209a705128998781c77f669fa86f0f0427f06be924450d05dfa590e79bba266f",
        "6a96b33da6dbd8348a8572a26c49f418790455726a49a41dc54bdd78f364b56d",
    ]
    assert result["fixed_locus"] == {
        "cyclic_subgroup_count": 6,
        "S_subgroup_fixed_points_on_surface": 0,
        "S_eigenline_evaluations": {"F": 40, "G": -25},
        "other_cyclic_subgroups": 5,
        "fixed_points_per_other_subgroup": 5,
        "surface_nonfree_point_count": 25,
        "surface_point_stabilizer_invariant_factors": [5],
        "nonidentity_element_fixed_point_distribution": {"0": 4, "5": 20},
    }
    assert result["invariant_cohomology"] == {
        "lefschetz_h2_traces": {
            "identity": 873,
            "S_line_nonidentity": -2,
            "other_nonidentity": 3,
        },
        "rational_h2_rank": 37,
        "canonical_residue_character": 3,
        "hodge_dimensions": {"2,0": 5, "1,1": 27, "0,2": 5},
        "non_1_1_invariant_classes_exist_rationally": True,
    }
    assert result["certificate_verified"] is True


def test_heisenberg_surface_h2_descent_profile_certifies_level_five_pairing_kill():
    result = heisenberg_surface_invariant_h2_descent_profile()

    assert result["schema"] == ("reasoning.heisenberg-surface-invariant-h2-descent-profile.v2")
    assert result["request_sha256"] == ("de2af424b1c08a430d8a658bd9f2a778b6c8df1a1969637e59b6c4366273dc7d")
    assert result["local_quotient"] == {
        "fixed_point_orbits": 5,
        "singularities": 5,
        "marked_stabilizer_generator": "T*S^r",
        "ambient_cotangent_pullback_weights_mod_5": [1, 2, 3, 4],
        "conormal_pullback_weights_mod_5": [1, 2],
        "marked_cotangent_pullback_weights_mod_5": [3, 4],
        "marked_tangent_weights_mod_5": [3, 4],
        "inverse_generator_tangent_weights_mod_5": [2, 1],
        "normalized_quotient_tangent_weights_mod_5": [1, 2],
        "cyclic_quotient_type": "1/5(1,2)",
        "hirzebruch_jung_expansion": "5/2=[3,2]",
        "exceptional_chain_self_intersections": [-3, -2],
    }
    assert result["minimal_resolution"] == {
        "quotient_euler_characteristic": 39,
        "euler_characteristic": 49,
        "holomorphic_euler_characteristic": 6,
        "geometric_genus": 5,
        "irregularity": 0,
        "canonical_square": 23,
        "b2": 47,
        "signature": -25,
        "b_plus": 11,
        "b_minus": 36,
        "hodge_1_1_dimension": 37,
        "integral_h2_unimodular": True,
        "integral_h2_odd": True,
    }
    exceptional = result["exceptional_lattice"]
    assert exceptional["raw_determinant"] == 5**5
    assert exceptional["saturation_code"] == {
        "coordinates": [f"<T*S^{power}>" for power in range(5)],
        "generator_matrix_mod_5": [[1, 1, 1, 1, 1], [0, 1, 2, 3, 4]],
        "rank_mod_5": 2,
        "isotropic": True,
    }
    assert exceptional["saturation_index"] == 25
    assert exceptional["saturated_determinant"] == 5
    assert result["orthogonal_lattice"]["determinant"] == 5
    invariant = result["integral_invariant_h2"]
    assert invariant["kernel_determinant"] == 5**73
    assert invariant["invariant_determinant"] == 5**71
    assert invariant["hyperplane_kernel_pairings_divisible_by_5"] is True
    assert invariant["valuation_one_pairing_exists"] is True
    assert result["perry_level_five_gate"]["b_fifth_power_divisible_by_5_in_free_integral_cohomology"] is True
    assert result["perry_level_five_gate"]["level_five_perry_obstruction_survives"] is False
    assert result["verdict"] == {
        "integral_h2_structure_certified_subject_to_bridges": True,
        "non_1_1_descent_source_exists": True,
        "level_five_perry_index_gate_killed": True,
        "period_five_brauer_class_certified": False,
        "index_5_adic_valuation_at_least_6_certified": False,
        "period_index_counterexample_certified": False,
    }
    assert result["certificate_verified"] is True


def test_heisenberg_surface_h2_descent_report_writes_canonical_receipt(tmp_path):
    registry = _registry(tmp_path)

    result = registry.invoke(
        "heisenberg_surface_invariant_h2_descent_report",
        {
            "path": "research/heisenberg-surface-h2-descent.md",
            "terminal_marker": "HEISENBERG_SURFACE_H2_DESCENT_COMPLETE",
        },
    )

    artifact = tmp_path / "artifacts/research/heisenberg-surface-h2-descent.md"
    text = artifact.read_text(encoding="utf-8")
    assert result["receipt"]["verdict"]["level_five_perry_index_gate_killed"] is True
    assert result["receipt_sha256"] in text
    assert result["certificate_verified"] is True
    assert text.endswith("HEISENBERG_SURFACE_H2_DESCENT_COMPLETE\n")


@pytest.mark.skipif(
    shutil.which("Singular") is None,
    reason="Singular is required for the exact five-chart integration gate",
)
def test_heisenberg_surface_singular_protocol_certifies_all_five_charts():
    result = singular_smoothness_profile()

    assert result["field"] == "F_101"
    assert result["program_sha256"] == ("c8c00bb5f60ed8027937885bff50468b7110b5b87d08ad086ad8ddfe1bad3f80")
    assert result["all_chart_singular_ideals_are_unit"] is True
    assert result["all_unit_standard_bases_have_size_one"] is True
    assert result["projective_charts"] == [{"chart": chart, "unit_ideal": True, "standard_basis_size": 1} for chart in range(5)]
    assert result["certificate_verified"] is True


@pytest.mark.skipif(
    not os.environ.get("REASONING_TEST_NOETHER_MANIFEST") or shutil.which("Singular") is None,
    reason=("set REASONING_TEST_NOETHER_MANIFEST and install Singular for the Heisenberg surface integration gate"),
)
def test_heisenberg_surface_report_certifies_explicit_surface(tmp_path):
    registry = _registry(
        tmp_path,
        noether_manifest=os.environ["REASONING_TEST_NOETHER_MANIFEST"],
    )

    result = registry.invoke(
        "heisenberg_complete_intersection_surface_report",
        {
            "level": 5,
            "ambient_dimension": 4,
            "degrees": [5, 5],
            "path": "research/heisenberg-5-5-surface.md",
            "terminal_marker": "HEISENBERG_5_5_SURFACE_COMPLETE",
        },
    )

    artifact = tmp_path / "artifacts/research/heisenberg-5-5-surface.md"
    text = artifact.read_text(encoding="utf-8")
    receipt_text = text.split("```json\n", 1)[1].split("```\n", 1)[0]
    receipt = json.loads(receipt_text)
    assert (
        receipt["finite_field_certificate"]["noether"]["request_sha256"]
        == "6a2db15d44381bf81a26947345086acde9b419f5f8c50de1ed5714b70aa3abc1"
    )
    assert receipt["finite_field_certificate"]["noether"]["degree"] == 25
    assert receipt["finite_field_certificate"]["singular"]["all_chart_singular_ideals_are_unit"] is True
    assert receipt["verdict"] == {
        "surface_witness": "SURFACE_WITNESS",
        "surface_witness_certified": True,
        "sixfold_geometry_certified": True,
        "period_five_brauer_class_certified": False,
        "index_5_adic_valuation_at_least_6_certified": False,
        "period_index_counterexample_certified": False,
    }
    assert result["certificate_verified"] is True
    assert result["receipt_sha256"] in text
    assert text.endswith("HEISENBERG_5_5_SURFACE_COMPLETE\n")


def test_post_s25_diagonal_report_writes_the_machine_receipt(tmp_path):
    registry = _registry(tmp_path)

    result = registry.invoke(
        "post_s25_diagonal_candidate_report",
        {
            "path": "research/post-s25-diagonal-screen.md",
            "terminal_marker": "POST_S25_DIAGONAL_SCREEN_COMPLETE",
        },
    )

    artifact = tmp_path / "artifacts/research/post-s25-diagonal-screen.md"
    text = artifact.read_text(encoding="utf-8")
    assert result["summary"]["candidate_a_rejected"] is True
    assert result["summary"]["candidate_b_rejected"] is True
    assert result["summary"]["candidate_a"]["character_block_count"] == 121
    assert result["summary"]["candidate_b_quadric"]["unobstructed_characters"] == [[0, 0]]
    assert result["summary"]["candidate_b_quintic"]["every_character_obstructed"] is True
    assert result["certificate_verified"] is True
    assert result["receipt_sha256"] in text
    assert text.endswith("POST_S25_DIAGONAL_SCREEN_COMPLETE\n")


def test_fermat_surface_profile_certifies_the_level_25_candidate(tmp_path):
    registry = _registry(tmp_path)

    result = registry.invoke(
        "fermat_surface_diagonal_profile",
        {"degree": 25, "character": [1, 1, 1, 22]},
    )

    assert result["surface"] == {
        "equation": "x0^25+x1^25+x2^25+x3^25=0",
        "smooth_over_characteristic_zero": True,
        "h1_rank": 0,
        "euler_characteristic": 13_275,
        "b2": 13_273,
        "primitive_b2": 13_272,
        "geometric_genus": 2_024,
        "hodge_1_1_dimension": 9_225,
    }
    assert result["diagonal_kernel"]["invariant_factors"] == [25, 25]
    assert result["diagonal_kernel"]["order"] == 625
    assert result["fixed_locus"]["global_fixed_locus_empty"] is True
    assert result["fixed_locus"]["nonfree_locus_zero_dimensional"] is True
    assert result["fixed_locus"]["nonfree_point_count"] == 150
    assert len(result["fixed_locus"]["nonfree_supports"]) == 6
    assert {entry["stabilizer_order"] for entry in result["fixed_locus"]["nonfree_supports"]} == {25}
    characters = result["invariant_primitive_characters"]
    assert characters["dimension"] == 24
    assert characters["hodge_dimensions"] == {"2,0": 8, "1,1": 8, "0,2": 8}
    assert sorted(orbit["dimension"] for orbit in characters["rational_galois_orbits"]) == [4, 20]
    assert characters["primitive_rational_hodge_rank"] == 0
    assert result["invariant_h2"]["rational_rank"] == 25
    assert result["invariant_h2"]["rational_neron_severi_rank"] == 1
    assert result["unresolved_integral_gates"]["cartan_leray_d3_row_certified"] is False
    assert result["unresolved_integral_gates"]["counterexample_certified"] is False
    assert result["certificate_verified"] is True
    assert len(result["request_sha256"]) == 64


def test_fermat_surface_report_writes_the_machine_receipt(tmp_path):
    registry = _registry(tmp_path)
    result = registry.invoke(
        "fermat_surface_diagonal_report",
        {
            "degree": 25,
            "character": [1, 1, 1, 22],
            "path": "research/fermat/profile.md",
            "terminal_marker": "FERMAT_PROFILE_COMPLETE",
        },
    )

    text = (tmp_path / "artifacts/research/fermat/profile.md").read_text()
    assert result["artifact"]["bytes"] == len(text.encode())
    assert result["receipt"]["fixed_locus"]["nonfree_point_count"] == 150
    assert result["certificate_verified"] is True
    assert f"Receipt SHA-256: `{result['receipt_sha256']}`" in text
    assert text.endswith("FERMAT_PROFILE_COMPLETE\n")


def test_fermat_surface_orbit_norm_profile_computes_the_pham_gram(tmp_path):
    result = _registry(tmp_path).invoke(
        "fermat_surface_orbit_norm_profile",
        {"degree": 25, "character": [1, 1, 1, 22]},
    )

    assert result["group_ring_chart"]["chart_character"] == [1, 1, 22]
    assert result["group_ring_chart"]["kernel_order"] == 625
    assert result["pham_pairing"]["pairing_by_quotient_difference"] == [
        -1250,
        1875,
        -1875,
        625,
        *([0] * 18),
        625,
        -1875,
        1875,
    ]
    lattice = result["orbit_norm_lattice"]
    assert len(lattice["basis_gram"]) == 24
    assert all(len(row) == 24 for row in lattice["basis_gram"])
    assert lattice["relation"] == [1] * 25
    assert lattice["relation_pairing_zero"] is True
    assert result["unresolved_integral_gates"]["saturation_inside_primitive_invariants_certified"] is False
    assert result["certificate_verified"] is True


@pytest.mark.skipif(
    not os.environ.get("REASONING_TEST_NOETHER_MANIFEST"),
    reason="set REASONING_TEST_NOETHER_MANIFEST for the installed-RC integration gate",
)
def test_fermat_surface_orbit_norm_report_certifies_smith_data(tmp_path):
    registry = _registry(
        tmp_path,
        noether_manifest=os.environ["REASONING_TEST_NOETHER_MANIFEST"],
    )
    result = registry.invoke(
        "fermat_surface_orbit_norm_report",
        {
            "degree": 25,
            "character": [1, 1, 1, 22],
            "path": "research/fermat/orbit-norm.md",
            "terminal_marker": "FERMAT_ORBIT_NORM_COMPLETE",
        },
    )

    lattice = result["receipt"]["orbit_norm_lattice"]
    assert lattice["rank"] == 24
    assert lattice["smith_invariant_factors"] == [625] * 21 + [15_625] * 3
    assert lattice["absolute_discriminant"] == 5**102
    assert result["receipt"]["unresolved_integral_gates"]["orbit_norm_rank_and_smith_certified"] is True
    assert result["receipt"]["unresolved_integral_gates"]["saturation_inside_primitive_invariants_certified"] is False
    text = (tmp_path / "artifacts/research/fermat/orbit-norm.md").read_text()
    assert f"Receipt SHA-256: `{result['receipt_sha256']}`" in text
    assert text.endswith("FERMAT_ORBIT_NORM_COMPLETE\n")
    assert result["certificate_verified"] is True


def test_fermat_surface_level25_profile_certifies_saturation_and_glue(tmp_path):
    result = _registry(tmp_path).invoke(
        "fermat_surface_level25_invariant_lattice_profile",
        {"degree": 25, "character": [1, 1, 1, 22]},
    )

    assert result["presentation"]["ambient_rank"] == 15_625
    assert result["presentation"]["relation_generator_count"] == 2_500
    assert result["presentation"]["relation_rank"] == 2_353
    assert result["saturation"]["defects"] == [3, 3, 0]
    assert result["saturation"]["norm_to_primitive_invariant_index"] == 5**6
    assert result["saturation"]["primitive_invariants_mod_norm_invariant_factors"] == [25, 25, 25]
    assert result["saturation"]["five_saturated"] is True
    primitive = result["primitive_invariant_lattice"]
    assert primitive["rank"] == 24
    assert primitive["pairing_ideal_generator"] == 25
    assert len(primitive["gram"]) == 24
    glue = result["full_cohomology_glue"]
    assert glue["glue_mod_5_in_primitive_invariants_plus_5P"] is False
    assert glue["invariant_hyperplane_pairing_image"] == 25
    assert glue["full_invariant_pairing_ideal_generator"] == 25
    gate = result["perry_level25_gate"]
    assert gate["valuation_one_invariant_pairing_exists"] is False
    assert gate["fermat_surface_candidate_passes"] is False
    assert result["certificate_verified"] is True


def test_fermat_surface_level25_scan_exhausts_eligible_diagonal_kernels(tmp_path):
    result = _registry(tmp_path).invoke(
        "fermat_surface_level25_diagonal_kernel_scan_profile",
        {},
    )

    enumeration = result["equivalence_class_enumeration"]
    assert enumeration["raw_primitive_character_count"] == 15_500
    assert enumeration["canonical_class_count"] == 45
    assert result["eligibility_filter"]["eligible_characters"] == [
        [1, 1, 1, 22],
        [1, 1, 2, 21],
        [1, 1, 6, 17],
        [1, 1, 7, 16],
        [1, 1, 11, 12],
        [1, 2, 6, 16],
        [1, 6, 7, 11],
    ]
    candidates = result["candidate_integral_tests"]
    assert len(candidates) == 7
    assert {tuple(candidate["saturation_defects"]) for candidate in candidates} == {(3, 3, 0)}
    assert {candidate["norm_to_primitive_invariant_index"] for candidate in candidates} == {5**6}
    assert {candidate["full_invariant_pairing_ideal_generator"] for candidate in candidates} == {25}
    assert all(not candidate["candidate_passes"] for candidate in candidates)
    assert result["family_verdict"]["diagonal_fermat_kernel_family_exhausted"] is True
    assert result["unresolved_integral_gates"]["non_fermat_surface_source_constructed"] is False
    assert result["certificate_verified"] is True


def test_fermat_surface_level25_scan_report_writes_machine_receipt(tmp_path):
    result = _registry(tmp_path).invoke(
        "fermat_surface_level25_diagonal_kernel_scan_report",
        {
            "path": "research/fermat/diagonal-kernel-scan.md",
            "terminal_marker": "FERMAT_DIAGONAL_KERNEL_SCAN_COMPLETE",
        },
    )

    text = (tmp_path / "artifacts/research/fermat/diagonal-kernel-scan.md").read_text()
    assert result["receipt"]["eligibility_filter"]["eligible_candidate_count"] == 7
    assert f"Receipt SHA-256: `{result['receipt_sha256']}`" in text
    assert text.endswith("FERMAT_DIAGONAL_KERNEL_SCAN_COMPLETE\n")
    assert result["certificate_verified"] is True


def test_elliptic_modular_surface_level25_profile_certifies_frontier(tmp_path):
    result = _registry(tmp_path).invoke(
        "elliptic_modular_surface_level25_profile",
        {"level": 25},
    )

    modular_group = result["modular_group"]
    assert modular_group["psl2_order"] == 7_500
    assert modular_group["right_translation_cycle_count"] == 300
    assert modular_group["right_translation_cycle_lengths"] == [25]

    cusps = result["cusps_and_stabilizers"]
    assert cusps["cusp_count"] == 300
    assert cusps["distinct_stabilizer_lines"] == 30
    assert set(cusps["stabilizer_line_multiplicities"]) == {10}
    assert cusps["nonfree_point_count"] == 7_500
    assert cusps["fixed_point_incidence"] == 180_000
    assert cusps["fixed_point_profiles"] == [
        {
            "element_order": 5,
            "element_count": 24,
            "stabilizer_line_counts": [5],
            "cusp_counts": [50],
            "fixed_point_counts": [1_250],
        },
        {
            "element_order": 25,
            "element_count": 600,
            "stabilizer_line_counts": [1],
            "cusp_counts": [10],
            "fixed_point_counts": [250],
        },
    ]

    assert result["surface_invariants"] == {
        "smooth_projective_model": ("minimal regular compactification of the universal generalized elliptic curve over X(25)"),
        "base_genus": 476,
        "topological_euler_characteristic": 7_500,
        "holomorphic_euler_characteristic": 625,
        "irregularity": 476,
        "geometric_genus": 1_100,
        "b1": 952,
        "b2": 9_402,
        "hodge_1_1_dimension": 7_202,
    }
    assert result["acting_translation_group"] == {
        "role": "fiberwise automorphism group used in Cartan-Leray",
        "structure": "(Z/25)^2",
        "order": 625,
        "generators": [[1, 0], [0, 1]],
        "not_the_modular_indexing_group": True,
    }
    assert result["modular_group"]["role"] == ("base-cell and cusp indexing group, not the acting translation group")
    lattice = result["integral_lattice_frontier"]
    assert lattice["fiber_section_plane_gram"] == [[-625, 1], [1, 0]]
    assert lattice["neron_severi_rank"] == 7_202
    assert lattice["neron_severi_discriminant_5_adic_valuation"] == 592
    assert lattice["transcendental_discriminant_invariant_factors"] == {
        "factor": 25,
        "multiplicity": 296,
    }
    assert lattice["transcendental_rank"] == 2_200
    assert lattice["invariant_h2_rank_from_lefschetz"] == 2_202
    assert lattice["raw_invariant_pairing_5_adic_valuation"] == 0
    assert lattice["raw_invariant_pairing_passes_valuation_one_gate"] is False
    fixed = result["fixed_lattice_certificate"]
    assert fixed["nonzero_section_height_profiles"] == [
        {
            "section_order": 5,
            "section_count": 24,
            "correction_numerator": 31_250,
            "correction_denominator": 25,
            "height_correction": 1_250,
        },
        {
            "section_order": 25,
            "section_count": 600,
            "correction_numerator": 31_250,
            "correction_denominator": 25,
            "height_correction": 1_250,
        },
    ]
    assert fixed["specialization_value_count_profiles"] == [[25] * 25]
    assert fixed["specialization_value_profile_definition"].startswith("For each fixed cusp")
    assert fixed["a24_weyl_vector"] == {
        "definition": "rho=sum_(k=1)^24 omega_k",
        "simple_root_coefficients": [
            12,
            23,
            33,
            42,
            50,
            57,
            63,
            68,
            72,
            75,
            77,
            78,
            78,
            77,
            75,
            72,
            68,
            63,
            57,
            50,
            42,
            33,
            23,
            12,
        ],
        "cartan_pairings": [1] * 24,
        "positive_a24_square": 1_300,
        "coefficient_gcd": 1,
    }
    assert fixed["section_orbit_sum"] == {
        "definition": "D=sum_(g in (Z/25)^2) P_g",
        "trivial_lattice_coefficients": {
            "zero_section": 625,
            "fiber": 390_000,
            "negative_weyl_vector_per_cusp": 25,
        },
        "divisor_class_divisibility": 25,
        "quotient_definition": "E=D/25",
        "quotient_trivial_lattice_coefficients": {
            "zero_section": 25,
            "fiber": 15_600,
            "negative_weyl_vector_per_cusp": 1,
        },
    }
    fixed_ns = fixed["fixed_neron_severi"]
    assert fixed_ns["gram"] == [[-625, 25], [25, 0]]
    assert fixed_ns["discriminant_absolute"] == 625
    assert fixed_ns["pairing_gcd"] == 25
    assert fixed_ns["pairing_5_adic_valuation"] == 2
    assert fixed_ns["generator_pairings"] == {
        "E_square": -625,
        "E_with_F": 25,
        "E_with_O": -25,
        "E_with_simple_fiber_component": 1,
        "F_square": 0,
        "F_with_O": 1,
        "F_with_simple_fiber_component": 0,
    }
    assert fixed_ns["coordinate_recovery_pairing_matrix"] == [[1, 0], [-25, 1]]
    assert fixed_ns["coordinate_recovery_determinant"] == 1
    assert fixed_ns["fixed_dual_equals_fixed_lattice"] is True
    fixed_h2 = fixed["fixed_full_h2"]
    assert fixed_h2["basis_decomposition"] == "T direct_sum (Z*E + Z*F)"
    assert fixed_h2["integral_orthogonal_split"] is True
    assert fixed_h2["rank"] == 2_202
    assert fixed_h2["discriminant_5_adic_valuation"] == 596
    assert fixed_h2["full_pairing_5_adic_valuation"] == 0
    discriminant = fixed["full_discriminant_module"]
    assert discriminant["component_discriminant_module"] == {
        "factor": 25,
        "multiplicity": 300,
        "pairing_numerator_convention": (
            "Each negative A_24 factor is generated by omega_1 and has bilinear pairing numerator +1 modulo 25."
        ),
    }
    assert discriminant["mordell_weil_glue_subgroup"]["pairing_numerator_gram_mod_25"] == [[0, 0], [0, 0]]
    assert discriminant["mordell_weil_glue_subgroup"]["primitive_unit_minor"] == {
        "columns": [0, 10],
        "cusp_labels": [[0, 1], [1, 0]],
        "determinant_mod_25": 24,
    }
    assert discriminant["neron_severi_discriminant_module"] == {
        "construction": "H_perp/H",
        "factor": 25,
        "multiplicity": 296,
    }
    assert discriminant["transcendental_discriminant_module"]["factor"] == 25
    assert discriminant["transcendental_discriminant_module"]["multiplicity"] == 296
    assert discriminant["mod_5_radical_dimension"] == 296
    assert discriminant["mod_5_pairing_rank"] == 1_904
    frontier = result["cartan_leray_frontier"]
    assert frontier["d2_target_generator_count"] == 1_904
    assert frontier["d2_matrix_computed"] is False
    assert result["candidate_verdict"]["d2_rescue_unresolved"] is True
    assert result["schema"] == "reasoning.elliptic-modular-surface-level25-profile.v4"
    assert result["certificate_verified"] is True


def test_elliptic_modular_graph_level25_profile_certifies_parabolic_target(
    tmp_path,
):
    result = _registry(tmp_path).invoke(
        "elliptic_modular_graph_level25_profile",
        {"level": 25},
    )

    assert result["schema"] == "reasoning.elliptic-modular-graph-level25-profile.v3"
    assert result["modular_group"]["order"] == 7_500
    dessin = result["dessin"]
    assert dessin["edge_count"] == 7_500
    assert dessin["s_vertex_count"] == 3_750
    assert dessin["r_vertex_count"] == 2_500
    assert dessin["vertex_count"] == 6_250
    assert dessin["connected_component_count"] == 1
    assert dessin["spanning_tree_edge_count"] == 6_249
    assert dessin["free_generator_count"] == 1_251
    assert dessin["maximum_absolute_free_generator_entry"] == 165_885_144_100
    assert dessin["free_generator_matrices_sha256"] == ("949410478fd299a318d57cf0ef708f2036c6522979c31080b4481b494f750019")
    cusps = result["cusp_boundaries"]
    assert cusps["count"] == 300
    assert cusps["widths"] == [25]
    assert cusps["free_word_length_range"] == [1, 24]
    assert sum(item["count"] for item in cusps["free_word_length_histogram"]) == 300
    assert cusps["boundary_matrix_shape"] == [300, 1_251]
    assert cusps["boundary_rank_mod_5"] == 299
    assert cusps["boundary_split_rank_mod_25"] == 299
    assert cusps["free_words_sha256"] == ("c932977f649db879c2f79296b02510495dda796b8b949d8f44fd91c3ff8618d4")
    assert cusps["divided_monodromies_sha256"] == ("161f2677d66e005beb6a5ad38a8721fd5e2df576e40245ff8aa16f3765a35bac")
    assert cusps["boundary_matrix_sha256"] == ("4239d1aa8acd4ef0f7276b959344379a4caada23c726271fcf1797dc79461a62")
    assert cusps["integral_abelianized_boundary_image"].startswith("the primitive rank-299 lattice")
    assert cusps["integral_abelianized_smith_invariant_factors"] == {
        "factor": 1,
        "multiplicity": 299,
    }
    assert cusps["dual_graph_nonloop_edge_count"] == 1_251
    assert cusps["dual_graph_loop_edge_count"] == 0
    assert cusps["dual_spanning_tree_column_count"] == 299
    assert cusps["dual_spanning_tree_columns_sha256"] == ("611409d27e13f2f34aadba5a3e55637e4120a34f8bffacc76bc0fd46a48c1a79")
    assert cusps["abelianized_zero_sum_right_inverse_shape"] == [1_251, 299]
    assert cusps["abelianized_zero_sum_right_inverse_sha256"] == ("acd40f60f04a3937ed253cee6ca42de1953e56ded566e84b5897f44bef5cc5c6")
    assert cusps["abelianized_zero_sum_right_inverse_maximum_support"] == 40
    reduction = result["parabolic_reduction"]
    assert reduction["integral_cocycle_coordinate_count"] == 2_502
    assert reduction["compact_base_h1_rank"] == 952
    assert reduction["two_coordinate_target"] == "(Z/25)^1904"
    assert reduction["reduction_surjectivity_certified"] is True
    assert "prefix actions" in reduction["abelianized_right_inverse_scope"]
    hensel = reduction["twisted_hensel_correction"]
    assert hensel["matrix"] == "[boundary_matrix tensor I_2 | -diag(N_c)]"
    assert hensel["matrix_shape"] == [600, 3_102]
    assert hensel["rank_mod_5"] == 600
    assert hensel["full_row_rank_mod_5"] is True
    assert hensel["matrix_sha256"] == ("f710ee1baf024fd19ae87f0f395f634bf30abcd33510b75349151d8805622b82")
    assert hensel["pivot_columns_sha256"] == ("bb3014be3a98cb0ff0187faa11a409b6cba7962c621fd2e35f5bace5e71b1bb6")
    assert "flat base change" in hensel["lifting_proof"]
    assert reduction["reduction_kernel_representatives_are_pointwise_divisible_by_25"] is True
    assert reduction["identification_with_borel_d2_certified"] is True
    assert reduction["integral_parabolic_to_transcendental_isometry_certified"] is True
    assert reduction["pairing_on_integral_kernel_certified"] is True
    assert "ker(d2|T)" in reduction["kernel_pairing_theorem"]
    assert "arbitrary d2(E)" in reduction["full_kernel_consequence"]
    assert "u.v=0 mod 25" in reduction["full_kernel_consequence"]
    borel = result["borel_d2_comparison"]
    assert "coefficientwise reduction modulo 25" in borel["conclusion"]
    assert borel["ledger_evidence"] == {
        "claim_id": "clm_ecaedd117c3a7e7c398d0f05",
        "claim_revision_id": "clmrev_472549d9759fda72a9f9a28d",
        "proof_id": "prf_4461e7990fa9acbe4b118ce8",
        "proof_revision_id": "prfrev_5be17ff3136e670474a655e4",
        "verification_id": "ver_133a2098d24feef3fd15126f",
        "verdict": "verified",
    }
    comparison = result["integral_lattice_comparison"]
    assert comparison["integral_chain"] == [
        "H^1_par(Gamma(25),Z^2)",
        "H^1(X(25),j_*H_1(E,Z))",
        "H_1(X(25),j_*H_1(E,Z))",
        "H_2(S(25)^circ,Z)/Rad",
        "T_S(25)",
    ]
    assert "signed" in comparison["conclusion"]
    assert [item["verification_id"] for item in comparison["ledger_evidence"]] == [
        "ver_85cfff10ac3b26feba0a8437",
        "ver_e87e8b50264a3b2bed7413ee",
        "ver_43469007ecc2b6bf9783bd2c",
        "ver_ad748f0c92954369168aa889",
        "ver_d3785b00a918ce78e4e93eeb",
        "ver_19afd64ed0c2a039913002ee",
        "ver_37cec33c927d97cc792d4340",
    ]
    obstruction = result["full_kernel_pairing_obstruction"]
    assert obstruction["forced_coefficients_mod_25"] == {"m_u": 0, "m_v": 1}
    assert obstruction["d2_E_assumption"] == "none"
    assert obstruction["transcendental_term_mod_25"] == 0
    assert obstruction["fixed_neron_severi_term_mod_25"] == 0
    assert obstruction["forced_u_dot_v_mod_25"] == 0
    assert obstruction["required_valuation_one_residues_mod_25"] == [5, 10, 15, 20]
    assert obstruction["candidate_rejected"] is True
    assert obstruction["remaining_hypotheses"] == []
    assert result["candidate_verdict"] == {
        "surface": "S(25)",
        "rejected": True,
        "reason": (
            "Every pair of Borel-d2-kernel classes satisfying the Perry edge "
            "congruences has pairing zero modulo 25, whereas valuation one "
            "requires residue 5, 10, 15, or 20 modulo 25."
        ),
        "period_index_conjecture_disproved": False,
        "next_gate": "select and certify a non-Fermat surface source",
    }
    assert result["certificate"]["integral_abelianized_boundary_lattice_is_primitive"] is True
    assert result["certificate"]["dual_tree_right_inverse_verified_over_integers"] is True
    assert result["certificate"]["parabolic_hensel_correction_has_full_row_rank_mod_5"] is True
    assert result["certificate"]["reviewed_borel_d2_comparison_embedded"] is True
    assert result["certificate"]["reviewed_integral_lattice_comparison_embedded"] is True
    assert result["certificate"]["s25_pairing_obstruction_is_unconditional"] is True
    assert result["certificate_verified"] is True


def test_elliptic_modular_surface_level25_report_writes_machine_receipt(tmp_path):
    result = _registry(tmp_path).invoke(
        "elliptic_modular_surface_level25_report",
        {
            "level": 25,
            "path": "research/modular-surface/level25-profile.md",
            "terminal_marker": "ELLIPTIC_MODULAR_SURFACE_LEVEL25_COMPLETE",
        },
    )

    text = (tmp_path / "artifacts/research/modular-surface/level25-profile.md").read_text()
    assert result["receipt"]["modular_group"]["psl2_order"] == 7_500
    assert result["receipt"]["fixed_lattice_certificate"]["fixed_neron_severi"]["gram"] == [[-625, 25], [25, 0]]
    assert result["receipt"]["fixed_lattice_certificate"]["fixed_full_h2"]["integral_orthogonal_split"] is True
    assert f"Receipt SHA-256: `{result['receipt_sha256']}`" in text
    assert text.endswith("ELLIPTIC_MODULAR_SURFACE_LEVEL25_COMPLETE\n")
    assert result["certificate_verified"] is True


@pytest.mark.skipif(
    not os.environ.get("REASONING_TEST_NOETHER_MANIFEST"),
    reason="set REASONING_TEST_NOETHER_MANIFEST for the installed-RC integration gate",
)
def test_fermat_surface_level25_report_certifies_saturated_gram(tmp_path):
    registry = _registry(
        tmp_path,
        noether_manifest=os.environ["REASONING_TEST_NOETHER_MANIFEST"],
    )
    result = registry.invoke(
        "fermat_surface_level25_invariant_lattice_report",
        {
            "degree": 25,
            "character": [1, 1, 1, 22],
            "path": "research/fermat/invariant-lattice.md",
            "terminal_marker": "FERMAT_INVARIANT_LATTICE_COMPLETE",
        },
    )

    primitive = result["receipt"]["primitive_invariant_lattice"]
    assert primitive["smith_invariant_factors"] == [25] * 3 + [625] * 21
    assert primitive["absolute_discriminant"] == 5**90
    assert primitive["smith_certified"] is True
    assert result["receipt"]["certificate"]["norm_saturation_discriminant_identity_verified"] is True
    text = (tmp_path / "artifacts/research/fermat/invariant-lattice.md").read_text()
    assert f"Receipt SHA-256: `{result['receipt_sha256']}`" in text
    assert text.endswith("FERMAT_INVARIANT_LATTICE_COMPLETE\n")
    assert result["certificate_verified"] is True


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"degree": 25, "character": [1, 1, 1, 21]}, "diagonal subgroup"),
        ({"degree": 6, "character": [2, 2, 2, 0]}, "unit coefficient"),
    ],
)
def test_fermat_surface_profile_rejects_unsupported_characters(tmp_path, payload, message):
    with pytest.raises(ValueError, match=message):
        _registry(tmp_path).invoke("fermat_surface_diagonal_profile", payload)


def test_fermat_surface_level25_profile_rejects_another_character(tmp_path):
    with pytest.raises(ValueError, match="supports only"):
        _registry(tmp_path).invoke(
            "fermat_surface_level25_invariant_lattice_profile",
            {"degree": 25, "character": [1, 1, 2, 21]},
        )


def test_finite_torus_profile_separates_d2_and_theta_obstructions(tmp_path):
    registry = _registry(tmp_path)
    generators = [[1, 0, 0, 0], [0, 1, 0, 0]]

    primitive = registry.invoke(
        "finite_torus_translation_profile",
        {
            "prime": 5,
            "alternating_form": [
                [0, 1, 0, 0],
                [-1, 0, 0, 0],
                [0, 0, 0, 0],
                [0, 0, 0, 0],
            ],
            "subgroup_generators": generators,
        },
    )
    assert primitive["d2_functional_numerators_mod_prime"] == [
        [0, 1, 0, 0],
        [4, 0, 0, 0],
    ]
    assert primitive["d2_zero"] is False
    assert primitive["theta_commutator_residues_mod_prime"] is None

    polarization = registry.invoke(
        "finite_torus_translation_profile",
        {
            "prime": 5,
            "alternating_form": [
                [0, 25, 0, 0],
                [-25, 0, 0, 0],
                [0, 0, 0, 1],
                [0, 0, -1, 0],
            ],
            "subgroup_generators": generators,
        },
    )
    assert polarization["d2_zero"] is True
    assert polarization["theta_commutator_residues_mod_prime"] == [[0, 0], [0, 0]]
    assert polarization["theta_extension_splits"] is True

    generator = registry.invoke(
        "finite_torus_translation_profile",
        {
            "prime": 5,
            "alternating_form": [
                [0, 5, 0, 0],
                [-5, 0, 0, 0],
                [0, 0, 0, 1],
                [0, 0, -1, 0],
            ],
            "subgroup_generators": generators,
        },
    )
    assert generator["d2_zero"] is True
    assert generator["theta_commutator_residues_mod_prime"] == [[0, 1], [4, 0]]
    assert generator["theta_commutator_rank_mod_prime"] == 2
    assert generator["d3_generator_for_rank_two_subgroup"] is True
    assert generator["certificate_verified"] is True
    assert len(generator["request_sha256"]) == 64


@pytest.mark.parametrize(
    "payload, message",
    [
        (
            {
                "prime": 4,
                "alternating_form": [[0, 1], [-1, 0]],
                "subgroup_generators": [[1, 0]],
            },
            "prime integer",
        ),
        (
            {
                "prime": 5,
                "alternating_form": [[0, 1], [0, 0]],
                "subgroup_generators": [[1, 0]],
            },
            "skew-symmetric",
        ),
        (
            {
                "prime": 5,
                "alternating_form": [[0, 1], [-1, 0]],
                "subgroup_generators": [[1, 0, 0]],
            },
            "length 2",
        ),
    ],
)
def test_finite_torus_profile_rejects_invalid_lattice_data(tmp_path, payload, message):
    with pytest.raises(ValueError, match=message):
        _registry(tmp_path).invoke("finite_torus_translation_profile", payload)


@pytest.mark.parametrize(
    ("level", "edge_index"),
    [(4, 16), (5, 25), (25, 625)],
)
def test_elliptic_translation_edge_profile_certifies_both_differentials(tmp_path, level, edge_index):
    result = _registry(tmp_path).invoke("elliptic_translation_edge_profile", {"level": level})

    assert result["group"] == {
        "invariant_factors": [level, level],
        "order": edge_index,
        "exponent": level,
    }
    assert result["translation_quotient"]["pullback_h1_matrix"] == [
        [level, 0],
        [0, level],
    ]
    assert result["translation_quotient"]["pullback_h2_matrix"] == [[edge_index]]
    assert result["cartan_leray"]["h3_group_cohomology_invariant_factors"] == [level]
    assert result["cartan_leray"]["d2_kernel_index"] == level
    assert result["cartan_leray"]["d3_kernel_index"] == level
    assert result["cartan_leray"]["edge_index"] == edge_index
    assert result["certificate"]["forced_kernel_indices"] == [level, level]
    assert result["certificate_verified"] is True
    assert result["diagonal_quotient_certified"] is False
    assert len(result["unresolved_coupling_conditions"]) == 7
    assert len(result["request_sha256"]) == 64


@pytest.mark.parametrize("level", [True, 1, 1_000_001])
def test_elliptic_translation_edge_profile_rejects_invalid_levels(tmp_path, level):
    with pytest.raises(ValidationError):
        _registry(tmp_path).invoke("elliptic_translation_edge_profile", {"level": level})


def test_cartan_leray_h2_edge_matrix_assembles_the_ten_equations(tmp_path):
    result = _registry(tmp_path).invoke(
        "cartan_leray_h2_edge_matrix",
        {
            "level": 5,
            "cochain_ranks": [1, 1, 1, 1],
            "differentials": [[[0]], [[0]], [[0]]],
            "generator_actions": [
                [[[1]], [[1]], [[1]], [[1]]],
                [[[1]], [[1]], [[1]], [[1]]],
            ],
            "h2_cocycle_basis": [[1]],
        },
    )

    assert result["group"] == {"invariant_factors": [5, 5]}
    assert result["matrix_rows"] == 11
    assert result["matrix_columns"] == 8
    assert result["variable_blocks"]["a"] == {"start": 0, "count": 1}
    assert result["equation_blocks"]["mark_h2_class"] == {
        "start": 0,
        "count": 1,
    }
    assert result["equation_blocks"]["T2_t"] == {"start": 10, "count": 1}
    assert result["certificate"]["equation_count"] == 10
    assert result["certificate"]["cyclic_resolution_relations_verified"] is True
    assert result["h2_marking_basis_certified"] is False
    assert result["noether_request"]["operation"] == "integer_kernel_projection"
    assert result["noether_request"]["payload"]["selected_columns"] == [0]
    assert len(result["request_sha256"]) == 64
    assert result["certificate_verified"] is True


def test_cartan_leray_h2_edge_matrix_validates_a_cellular_torus(tmp_path):
    result = _registry(tmp_path).invoke("cartan_leray_h2_edge_matrix", _torus_translation_cochains(2))

    assert result["cochain_ranks"] == [4, 8, 4, 0]
    assert result["matrix_rows"] == 52
    assert result["matrix_columns"] == 41
    assert result["certificate"]["equivariance_verified"] is True


def test_cartan_leray_h2_edge_matrix_rejects_a_noncomplex(tmp_path):
    with pytest.raises(ValueError, match=r"D1\*D0=0"):
        _registry(tmp_path).invoke(
            "cartan_leray_h2_edge_matrix",
            {
                "level": 5,
                "cochain_ranks": [1, 1, 1, 1],
                "differentials": [[[1]], [[1]], [[0]]],
                "generator_actions": [
                    [[[1]], [[1]], [[1]], [[1]]],
                    [[[1]], [[1]], [[1]], [[1]]],
                ],
                "h2_cocycle_basis": [[1]],
            },
        )


@pytest.mark.skipif(
    not os.environ.get("REASONING_TEST_NOETHER_MANIFEST"),
    reason="set REASONING_TEST_NOETHER_MANIFEST for the installed-RC integration gate",
)
@pytest.mark.parametrize("level", [2, 5])
def test_cartan_leray_torus_edge_pipeline_returns_the_covering_degree(tmp_path, level):
    result = _registry(
        tmp_path,
        noether_manifest=os.environ["REASONING_TEST_NOETHER_MANIFEST"],
    ).invoke("cartan_leray_h2_edge_lattice", _torus_translation_cochains(level))
    projected = result["projection"]

    assert result["edge_coefficient_column_hnf_basis"] == [[level * level]]
    assert result["assembly"]["h2_marking_basis_certified"] is False
    assert "noether_request" not in result["assembly"]
    assert projected["kernel_basis_included"] is False
    assert "kernel_basis" not in projected
    assert projected["projected_rank"] == 1
    assert result["certificate_verified"] is True


@pytest.mark.skipif(
    not os.environ.get("REASONING_TEST_NOETHER_MANIFEST"),
    reason="set REASONING_TEST_NOETHER_MANIFEST for the installed-RC integration gate",
)
def test_cartan_leray_edge_report_writes_machine_rendered_fields(tmp_path):
    registry = _registry(
        tmp_path,
        noether_manifest=os.environ["REASONING_TEST_NOETHER_MANIFEST"],
    )
    path = "receipts/edge.md"
    result = registry.invoke(
        "cartan_leray_h2_edge_report",
        {
            **_torus_translation_cochains(2),
            "path": path,
            "terminal_marker": "EDGE_RECEIPT_COMPLETE",
        },
    )

    text = (tmp_path / "artifacts" / path).read_text(encoding="utf-8")
    receipt_text = text.split("```json\n", 1)[1].split("\n```", 1)[0]
    receipt = json.loads(receipt_text)
    assert receipt == result["receipt"]
    assert receipt["edge_coefficient_column_hnf_basis"] == [[4]]
    assert receipt["h2_marking_basis_certified"] is False
    assert receipt["hashes"]["assembled_noether_request_sha256"] == receipt["hashes"]["executed_noether_request_sha256"]
    assert receipt["certificates"]["certificate_verified"] is True
    assert text.endswith("EDGE_RECEIPT_COMPLETE\n")
    assert result["artifact"]["bytes"] == len(text.encode())
    assert len(result["receipt_sha256"]) == 64


@pytest.mark.skipif(
    not os.environ.get("REASONING_TEST_NOETHER_MANIFEST"),
    reason="set REASONING_TEST_NOETHER_MANIFEST for the installed-RC integration gate",
)
def test_abelian_surface_translation_profile_certifies_the_split_edge_lattice(
    tmp_path,
):
    registry = _registry(
        tmp_path,
        noether_manifest=os.environ["REASONING_TEST_NOETHER_MANIFEST"],
    )
    path = "receipts/abelian-surface-edge.md"
    result = registry.invoke(
        "abelian_surface_translation_edge_report",
        {
            "level": 5,
            "path": path,
            "terminal_marker": "ABELIAN_SURFACE_EDGE_COMPLETE",
        },
    )
    receipt = result["receipt"]

    assert receipt["edge_lattice"]["diagonal_factors_by_marked_basis"] == [
        25,
        5,
        5,
        5,
        5,
        1,
    ]
    assert receipt["edge_lattice"]["smith_invariant_factors"] == [
        1,
        5,
        5,
        5,
        5,
        25,
    ]
    assert receipt["edge_lattice"]["index"] == 5**6
    assert receipt["d2_kernel_lattice"]["diagonal_factors_by_marked_basis"] == [
        5,
        5,
        5,
        5,
        5,
        1,
    ]
    assert receipt["d2_kernel_lattice"]["smith_invariant_factors"] == [
        1,
        5,
        5,
        5,
        5,
        5,
    ]
    assert receipt["d2_kernel_lattice"]["quotient_by_edge_invariant_factors"] == [5]
    assert receipt["d2_kernel_lattice"]["basis_residues_zero_verified"] is True
    assert receipt["d2_kernel_lattice"]["nontrivial_factors_minimal_verified"] is True
    assert receipt["transgressing_class"]["coefficients"] == [5, 0, 0, 0, 0, 1]
    assert receipt["transgressing_class"]["d3_generator"] is True
    assert receipt["transgressing_class"]["in_edge_lattice"] is False
    assert receipt["descended_comparison_class"]["in_edge_lattice"] is True
    assert receipt["diagonal_elliptic_cancellation"]["exact_period_gate_under_generic_ns_marking"] is True
    assert receipt["counterexample_scope"]["counterexample_certified"] is False
    assert receipt["certificate_verified"] is True

    text = (tmp_path / "artifacts" / path).read_text(encoding="utf-8")
    assert text.endswith("ABELIAN_SURFACE_EDGE_COMPLETE\n")
    assert result["artifact"]["bytes"] == len(text.encode())


@pytest.mark.skipif(
    not os.environ.get("REASONING_TEST_NOETHER_MANIFEST"),
    reason="set REASONING_TEST_NOETHER_MANIFEST for the installed-RC integration gate",
)
def test_abelian_surface_level25_profile_certifies_the_product_cyclic_gate(
    tmp_path,
):
    registry = _registry(
        tmp_path,
        noether_manifest=os.environ["REASONING_TEST_NOETHER_MANIFEST"],
    )
    receipt = registry.invoke(
        "abelian_surface_translation_edge_profile",
        {"level": 25},
    )

    assert receipt["schema"] == "reasoning.abelian-surface-translation-edge-profile.v3"
    assert receipt["marking"]["pullback_h1_smith_invariant_factors"] == [
        1,
        1,
        25,
        25,
    ]
    assert receipt["edge_lattice"]["smith_invariant_factors"] == [
        1,
        25,
        25,
        25,
        25,
        625,
    ]
    assert receipt["edge_lattice"]["raw_bilinear_pairing_ideal_generator"] == 625
    assert receipt["edge_lattice"]["quotient_normalized_pairing_ideal_generator"] == 1
    active_active = receipt["edge_lattice"]["component_receipts"]["active_active"]
    assert active_active["factor_edge_coefficients"] == [25, 25]
    assert active_active["kunneth_coefficient_product_verified"] is True
    kernel = receipt["d2_kernel_lattice"]
    assert kernel["diagonal_factors_by_marked_basis"] == [25, 25, 25, 25, 25, 1]
    assert kernel["smith_invariant_factors"] == [1, 25, 25, 25, 25, 25]
    assert kernel["quotient_by_edge_invariant_factors"] == [25]
    assert kernel["bilinear_pairing_ideal_generator"] == 25
    assert kernel["kernel_is_saturated_in_h2"] is False
    assert receipt["transgressing_class"]["coefficients"] == [25, 0, 0, 0, 0, 1]
    assert receipt["transgressing_class"]["theta_commutator_residues_mod_level"] == [
        [0, 1],
        [24, 0],
    ]
    assert receipt["transgressing_class"]["d3_generator"] is True
    assert receipt["descended_comparison_class"]["coefficients"] == [
        625,
        0,
        0,
        0,
        0,
        1,
    ]
    assert receipt["level25_pairing_gate"] == {
        "applicable": True,
        "raw_d2_kernel_pairing_ideal_generator": 25,
        "minimum_pairing_5_adic_valuation": 2,
        "valuation_one_pairing_exists_in_raw_kernel": False,
        "raw_kernel_rules_out_valuation_one": True,
        "candidate_verdict": (
            "rejected for the level-25 abelian-surface Perry product template; no period-index counterexample is certified"
        ),
    }
    assert receipt["diagonal_elliptic_cancellation"]["elliptic_d2_zero"] is True
    assert receipt["perry_product_template_comparison"] == {
        "applicable": True,
        "status": "rejected",
        "source_lattice": "raw d2 kernel in H2(A,Z)",
        "source_pairing": "ordinary upstairs cup-product intersection",
        "elliptic_correction_is_d2_zero": True,
        "surface_v_must_be_d2_zero": True,
        "surface_u_must_be_d2_zero": True,
        "surface_v_must_survive_d3": False,
        "surface_u_must_survive_d3": True,
        "both_surface_classes_must_lie_in_stable_edge_lattice": False,
        "d3_survivors_restrict_raw_d2_kernel": True,
        "quotient_normalized_edge_form_used_by_gate": False,
        "cover_degree_normalization_before_valuation_gate": False,
        "total_cover_degree": 625,
        "cover_degree_divisions_in_characteristic_number": 1,
        "characteristic_number_formula": "-100(v.u)",
        "valuation_one_gate_passes": False,
    }
    assert receipt["certificate_verified"] is True


def _registry(tmp_path):
    return build_project_tool_registry(LedgerStore(tmp_path / "ledger"), tmp_path / "artifacts")


def test_exact_calculator_preserves_rationals_and_supports_bounded_functions(tmp_path):
    registry = _registry(tmp_path)

    result = registry.invoke(
        "scientific_calculate",
        {"expression": "1/3 + 0.1 + comb(10, 3) - abs(-1/2)"},
    )

    assert result["exact"] == "1799/15"
    assert result["numerator"] == "1799"
    assert result["denominator"] == "15"
    assert result["decimal"].startswith("119.933333")
    assert result["source"]["engine"] == "fractions.Fraction"
    assert registry.invoke("scientific_calculate", {"expression": "gcd(84, 30)"})["exact"] == "6"


@pytest.mark.parametrize(
    "expression, message",
    [
        ("__import__('os').system('true')", "only named functions"),
        ("[1, 2, 3]", "List"),
        ("1 / 0", "division by zero"),
        ("factorial(5001)", "unsupported function"),
        ("65537 ** 5000", "safety limit"),
        ("1e999999", "exact-arithmetic limit"),
    ],
)
def test_exact_calculator_rejects_code_and_resource_abuse(tmp_path, expression, message):
    with pytest.raises(ValueError, match=message):
        _registry(tmp_path).invoke("scientific_calculate", {"expression": expression})


def test_unit_conversion_uses_exact_factors_and_affine_temperatures(tmp_path):
    registry = _registry(tmp_path)

    freezing = registry.invoke("unit_convert", {"value": "0", "from_unit": "degC", "to_unit": "degF"})
    assert freezing["exact"] == "32/1"
    assert freezing["value"] == "32"
    electron_volt = registry.invoke("unit_convert", {"value": "1", "from_unit": "eV", "to_unit": "J"})
    assert electron_volt["value"] == "1.602176634E-19"
    assert electron_volt["source"]["version"] == "1"
    with pytest.raises(ValueError, match="incompatible units"):
        registry.invoke("unit_convert", {"value": "1", "from_unit": "kg", "to_unit": "m"})


def test_constant_catalog_is_versioned_hashed_and_keeps_uncertainty(tmp_path):
    registry = _registry(tmp_path)

    gravity = registry.invoke("constant_lookup", {"query": "gravitation"})
    assert gravity["constants"] == [
        {
            "id": "gravitational-constant",
            "name": "Newtonian constant of gravitation",
            "symbol": "G",
            "value": "6.67430e-11",
            "unit": "m^3 kg^-1 s^-2",
            "uncertainty": "1.5e-15",
        }
    ]
    assert gravity["source"]["edition"].startswith("2022")
    assert len(gravity["source"]["catalog_sha256"]) == 64
    charge = registry.invoke("constant_lookup", {"query": "elementary charge"})
    assert charge["constants"][0]["uncertainty"] == "exact"


def test_local_science_tools_require_the_compute_capability_but_not_network(tmp_path):
    registry = _registry(tmp_path)

    with pytest.raises(ToolRegistryError, match="science:compute"):
        registry.select(ToolGrant(("scientific_calculate",)))
    selected = registry.select(ToolGrant(("scientific_calculate",), capabilities=("science:compute",)))
    assert [tool.name for tool in selected] == ["scientific_calculate"]
