import hashlib
import json
import math
import os
import signal
import subprocess
import sys
import types
from pathlib import Path

import pytest

from reasoning_ledger import LedgerStore
from reasoning_ledger.agents import NoetherRuntime, NoetherRuntimeError, noether_worker
from reasoning_ledger.agents.project_tools import (
    build_project_tool_registry,
    project_mcp_config,
)


def _manifest(tmp_path: Path) -> Path:
    installation = tmp_path / "noether"
    runtime = installation / "runtime" / "bin"
    site_packages = installation / "runtime" / "lib" / "python3.12" / "site-packages"
    wheelhouse = installation / "wheelhouse"
    runtime.mkdir(parents=True)
    site_packages.mkdir(parents=True)
    wheelhouse.mkdir()
    python = runtime / "python"
    python.symlink_to(sys.executable)
    wheel = wheelhouse / "reasoning_noether-0.1.0.whl"
    wheel.write_bytes(b"fixed wheel fixture")
    value = {
        "schema_version": 1,
        "installation_id": "test-noether",
        "installation_root": str(installation),
        "package": {"distribution": "reasoning-noether", "version": "0.1.0"},
        "source": {"commit": "a" * 40},
        "wheel": {
            "path": str(wheel),
            "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        },
        "runtime": {
            "python": str(python),
            "python_runtime_root": sys.base_prefix,
        },
        "verification": {"wheel_gate": "passed"},
    }
    path = installation / "installation.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_noether_manifest_pins_and_rechecks_the_wheel(tmp_path):
    manifest = _manifest(tmp_path)
    runtime = NoetherRuntime.load(manifest)

    assert runtime.installation_id == "test-noether"
    assert runtime.version == "0.1.0"
    assert runtime.wheel_sha256 == hashlib.sha256(b"fixed wheel fixture").hexdigest()

    runtime.wheel.write_bytes(b"tampered")
    with pytest.raises(NoetherRuntimeError, match="SHA-256"):
        NoetherRuntime.load(manifest)


@pytest.mark.parametrize(
    "returncode",
    [-signal.SIGXCPU, 128 + signal.SIGXCPU],
)
def test_noether_runtime_reports_cpu_limit_signal(tmp_path, monkeypatch, returncode):
    runtime = NoetherRuntime.load(_manifest(tmp_path))

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command,
            returncode,
            "",
            "",
        ),
    )
    monkeypatch.setattr(
        "reasoning_ledger.agents.noether.sandbox.wrap",
        lambda command, **options: command,
    )
    monkeypatch.setattr("reasoning_ledger.agents.noether.sandbox.enabled", lambda: False)

    with pytest.raises(
        NoetherRuntimeError,
        match=r"exceeded the 17-second CPU limit \(SIGXCPU\)",
    ):
        runtime.invoke("factor_integer", {"value": 10}, timeout_seconds=17)


def test_registry_exposes_fixed_protocol_tools_and_provenance(tmp_path, monkeypatch):
    manifest = _manifest(tmp_path)
    calls = []

    def fake_run(command, **kwargs):
        if command[0:2] != ["/usr/bin/env", "-i"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        calls.append((command, kwargs))
        request = json.loads(kwargs["input"])
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps({"ok": True, "result": {"operation": request["operation"]}}),
            "",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(
        "reasoning_ledger.agents.noether.sandbox.wrap",
        lambda command, **options: command,
    )
    monkeypatch.setattr("reasoning_ledger.agents.noether.sandbox.enabled", lambda: False)
    registry = build_project_tool_registry(
        LedgerStore(tmp_path / "ledger"),
        tmp_path / "artifacts",
        noether_manifest=manifest,
    )
    compute_schema = registry.get("noether_compute").input_schema
    assert compute_schema["required"] == ["operation", "payload"]
    assert "oneOf" not in compute_schema
    assert "lattice_shortest_vectors" in compute_schema["properties"]["operation"]["enum"]
    assert "lattice_orthogonal_complements" in compute_schema["properties"]["operation"]["enum"]
    assert "integer_kernel_projection" in compute_schema["properties"]["operation"]["enum"]
    assert "perfect_matching_polynomial" in compute_schema["properties"]["operation"]["enum"]
    assert "swarm_topology" in compute_schema["properties"]["operation"]["enum"]
    assert "multiplicities: [{label, count}]" in compute_schema["properties"]["payload"]["description"]
    assert "pairings?: [{left, right, value}]" in compute_schema["properties"]["payload"]["description"]

    result = registry.invoke(
        "noether_compute",
        {
            "operation": "matrix_invariants",
            "payload": {"ring": "Z", "rows": [[2, 4], [6, 8]]},
            "timeout_seconds": 17,
        },
    )

    assert result["result"] == {"operation": "matrix_invariants"}
    assert result["request"] == {
        "operation": "matrix_invariants",
        "payload": {"ring": "Z", "rows": [[2, 4], [6, 8]]},
    }
    assert (
        result["request_sha256"]
        == hashlib.sha256(b'{"operation":"matrix_invariants","payload":{"ring":"Z","rows":[[2,4],[6,8]]}}\n').hexdigest()
    )
    assert result["provenance"]["installation_id"] == "test-noether"
    command, options = calls[0]
    assert command[0:2] == ["/usr/bin/env", "-i"]
    assert str(manifest.parent / "runtime" / "bin" / "python") in command
    assert "-I" in command
    assert command[-1].endswith("/agents/noether_worker.py")
    assert "PATH=" in command
    assert options["env"]["PATH"] == os.defpath
    assert options["env"]["REASONING_NOETHER_CPU_SECONDS"] == "17"
    assert options["start_new_session"] is True
    assert Path(options["cwd"]).name.startswith("reasoning-noether-")


def test_project_mcp_config_carries_the_manifest(tmp_path):
    manifest = _manifest(tmp_path)

    config = project_mcp_config(
        tmp_path / "ledger",
        tmp_path / "artifacts",
        noether_manifest=manifest,
    )

    index = config["args"].index("--noether-manifest")
    assert config["args"][index + 1] == str(manifest)


def test_wrapper_descriptions_match_the_fixed_compute_protocol():
    factor = noether_worker._describe({"operation": "factor_integer"})
    binomial = noether_worker._describe({"operation": "binomial_invariants"})
    multinomial = noether_worker._describe({"operation": "multinomial_invariants"})
    matching = noether_worker._describe({"operation": "perfect_matching_polynomial"})
    matrix = noether_worker._describe({"operation": "matrix_invariants"})
    shortest = noether_worker._describe({"operation": "lattice_shortest_vectors"})
    orthogonals = noether_worker._describe({"operation": "lattice_orthogonal_complements"})
    membership = noether_worker._describe({"operation": "integer_lattice_membership"})
    projection = noether_worker._describe({"operation": "integer_kernel_projection"})
    ideal = noether_worker._describe({"operation": "ideal_invariants"})
    homology = noether_worker._describe({"operation": "simplicial_homology"})
    swarm = noether_worker._describe({"operation": "swarm_topology"})

    assert factor["input_schemas"][0]["required"] == ["value"]
    assert binomial["input_schemas"][0]["required"] == ["n", "k"]
    assert binomial["example"] == {
        "operation": "binomial_invariants",
        "payload": {"n": 3125, "k": 5},
    }
    assert multinomial["input_schemas"][0]["required"] == ["power", "parts"]
    assert multinomial["example"] == {
        "operation": "multinomial_invariants",
        "payload": {"power": 4, "parts": [1, 2, 1], "modulus": 5},
    }
    assert matching["input_schemas"][0]["required"] == ["multiplicities"]
    assert matching["algorithms"] == [
        "distinguished-vertex perfect-matching recurrence",
        "closed multiset orbit-size formula",
        "numthy.factor",
    ]
    assert matching["example"] == {
        "operation": "perfect_matching_polynomial",
        "payload": {
            "multiplicities": [
                {"label": "v", "count": 4},
                {"label": "x", "count": 1},
                {"label": "h", "count": 1},
            ],
            "pairings": [
                {"left": "v", "right": "v", "value": 2},
                {"left": "v", "right": "x", "value": 1},
                {"left": "v", "right": "h", "value": 26},
                {"left": "x", "right": "h", "value": 25},
            ],
            "modulus": 5,
        },
    }
    assert matrix["input_schemas"][0]["properties"]["ring"]["enum"] == ["Z", "Q"]
    assert shortest["input_schemas"][0]["required"] == ["gram"]
    assert shortest["algorithms"] == ["Lattice.shortest_vectors"]
    assert shortest["example"] == {
        "operation": "lattice_shortest_vectors",
        "payload": {"gram": [[2, -1], [-1, 2]], "bound": 2},
    }
    assert orthogonals["input_schemas"][0]["required"] == ["gram", "vectors"]
    assert orthogonals["algorithms"] == [
        "Matrix.smith_normal_form_with_transforms",
        "Matrix.smith_normal_form",
    ]
    assert orthogonals["example"] == {
        "operation": "lattice_orthogonal_complements",
        "payload": {
            "gram": [[2, -1], [-1, 2]],
            "vectors": [[1, 0], [1, 1]],
        },
    }
    assert membership["input_schemas"][0]["required"] == ["matrix", "target"]
    assert membership["algorithms"] == ["Matrix.smith_normal_form_with_transforms"]
    assert projection["input_schemas"][0]["required"] == [
        "matrix",
        "selected_columns",
    ]
    assert projection["algorithms"] == [
        "Matrix.smith_normal_form_with_transforms",
        "Matrix.hermite_normal_form",
    ]
    assert projection["example"] == {
        "operation": "integer_kernel_projection",
        "payload": {"matrix": [[1, 0, -2]], "selected_columns": [0]},
    }
    assert ideal["example"]["payload"]["field"] == {"kind": "Fp", "prime": 5}
    assert ideal["example"]["payload"]["generators"][0][0] == {
        "coefficient": 1,
        "exponents": [5, 0],
    }
    assert homology["example"]["payload"]["facets"] == [
        [0, 1],
        [1, 2],
        [0, 2],
    ]
    assert swarm["input_schemas"][0]["required"] == ["facets", "edges", "directed"]
    assert swarm["algorithms"] == [
        "SimplicialComplex.canonical_form",
        "SimplicialComplex.graded_homology",
        "Graph.canonical_label",
    ]
    assert swarm["example"]["payload"]["directed"] is False


def test_perfect_matching_recurrence_compresses_the_campaign_multiset():
    pair_types, terms = noether_worker._matching_polynomial_terms((4, 1, 7), max_terms=100)

    assert pair_types == [(0, 0), (0, 1), (0, 2), (1, 1), (1, 2), (2, 2)]
    assert terms == [
        ((0, 0, 4, 0, 1, 1), 2520),
        ((0, 1, 3, 0, 0, 2), 2520),
        ((1, 0, 2, 0, 1, 2), 3780),
        ((1, 1, 1, 0, 0, 3), 1260),
        ((2, 0, 0, 0, 1, 3), 315),
    ]
    assert sum(coefficient for _, coefficient in terms) == 10_395


def test_perfect_matching_recurrence_distinguishes_seven_divisor_labels():
    pair_types, terms = noether_worker._matching_polynomial_terms((4, 1, 1, 1, 1, 1, 1, 1, 1), max_terms=1_000)

    assert len(pair_types) == 45
    assert len(terms) == 735
    assert {coefficient for _, coefficient in terms} == {3, 12, 24}
    assert math.gcd(*(coefficient for _, coefficient in terms)) == 3
    assert sum(coefficient for _, coefficient in terms) == 10_395
    assert all(coefficient % 5 for _, coefficient in terms)


def test_perfect_matching_polynomial_evaluates_pairings_exactly(monkeypatch):
    numthy = types.ModuleType("noether.numthy")
    numthy.factor = lambda _value: types.SimpleNamespace(unit=1, factors=[], verify=lambda _n: True)
    monkeypatch.setitem(sys.modules, "noether.numthy", numthy)

    result = noether_worker._perfect_matching_polynomial(
        {
            "multiplicities": [
                {"label": "v", "count": 4},
                {"label": "x", "count": 1},
                {"label": "h", "count": 1},
            ],
            "pairings": [
                {"left": "v", "right": "v", "value": 2},
                {"left": "x", "right": "v", "value": 1},
                {"left": "h", "right": "v", "value": 26},
                {"left": "x", "right": "h", "value": 25},
            ],
            "modulus": 5,
        }
    )

    assert result["evaluated_value"] == 924
    assert result["evaluated_remainder"] == 4
    assert result["evaluated_divisible_by_modulus"] is False
    assert sorted(term["evaluated_value"] for term in result["terms"]) == [300, 624]
    assert result["certificate"]["pairing_evaluation_from_verified_terms"] is True
    assert result["certificate_verified"] is True


def test_perfect_matching_polynomial_requires_every_active_pairing():
    with pytest.raises(ValueError, match="missing active pairing values: x:h"):
        noether_worker._perfect_matching_polynomial(
            {
                "multiplicities": [
                    {"label": "v", "count": 4},
                    {"label": "x", "count": 1},
                    {"label": "h", "count": 1},
                ],
                "pairings": [
                    {"left": "v", "right": "v", "value": 2},
                    {"left": "v", "right": "x", "value": 1},
                    {"left": "v", "right": "h", "value": 26},
                ],
            }
        )


def test_perfect_matching_protocol_rejects_an_odd_multiset_before_runtime_imports():
    with pytest.raises(ValueError, match="total multiplicity must be even"):
        noether_worker._perfect_matching_polynomial({"multiplicities": [{"label": "a", "count": 2}, {"label": "b", "count": 1}]})


def test_integer_kernel_projection_rejects_invalid_selected_columns_before_runtime_imports():
    with pytest.raises(ValueError, match="distinct in-range"):
        noether_worker._integer_kernel_projection(
            {"matrix": [[1, 0]], "selected_columns": [0, 0]}
        )


@pytest.mark.skipif(
    not os.environ.get("REASONING_TEST_NOETHER_MANIFEST"),
    reason="set REASONING_TEST_NOETHER_MANIFEST for the installed-RC integration gate",
)
def test_installed_noether_rc_fixed_protocol_operations():
    runtime = NoetherRuntime.load(os.environ["REASONING_TEST_NOETHER_MANIFEST"])

    capabilities = runtime.invoke("capabilities", {})["result"]
    factor = runtime.invoke("factor_integer", {"value": -360})["result"]
    binomial = runtime.invoke("binomial_invariants", {"n": 3125, "k": 5})["result"]
    multinomial = runtime.invoke(
        "multinomial_invariants",
        {"power": 4, "parts": [1, 2, 1], "modulus": 5},
    )["result"]
    matching = runtime.invoke(
        "perfect_matching_polynomial",
        {
            "multiplicities": [
                {"label": "a", "count": 4},
                {"label": "b", "count": 1},
                {"label": "h", "count": 7},
            ],
            "modulus": 5,
        },
    )["result"]
    matching_evaluation = runtime.invoke(
        "perfect_matching_polynomial",
        {
            "multiplicities": [
                {"label": "v", "count": 4},
                {"label": "x", "count": 1},
                {"label": "h", "count": 1},
            ],
            "pairings": [
                {"left": "v", "right": "v", "value": 2},
                {"left": "v", "right": "x", "value": 1},
                {"left": "v", "right": "h", "value": 26},
                {"left": "x", "right": "h", "value": 25},
            ],
            "modulus": 5,
        },
    )["result"]
    matrix = runtime.invoke(
        "matrix_invariants",
        {"ring": "Z", "rows": [[2, 4], [6, 8]]},
    )["result"]
    shortest = runtime.invoke(
        "lattice_shortest_vectors",
        {"gram": [[2, -1], [-1, 2]], "bound": 2},
    )["result"]
    orthogonals = runtime.invoke(
        "lattice_orthogonal_complements",
        {
            "gram": [[2, -1], [-1, 2]],
            "vectors": [[1, 0], [1, 1]],
        },
    )["result"]
    campaign_orthogonals = runtime.invoke(
        "lattice_orthogonal_complements",
        {
            "gram": [
                [4, 1, 1, -1],
                [1, 4, -1, 1],
                [1, -1, 4, 1],
                [-1, 1, 1, 4],
            ],
            "vectors": [
                [0, 0, 0, 1],
                [0, 0, 1, 0],
                [0, 1, 0, 0],
                [1, -1, -1, 1],
                [1, 0, 0, 0],
            ],
        },
    )["result"]
    feasible_matrix = [
        [-1, 1, 0, 0, 0, -5, 0, 0, 0],
        [1, -2, 1, 0, 0, 0, -25, 0, 0],
        [-1, 3, -3, 1, 0, 0, 0, -125, 0],
        [1, -4, 6, -4, 1, 0, 0, 0, -625],
    ]
    feasible_membership = runtime.invoke(
        "integer_lattice_membership",
        {"matrix": feasible_matrix, "target": [0, 0, 0, -125]},
    )["result"]
    infeasible_membership = runtime.invoke(
        "integer_lattice_membership",
        {"matrix": [[-5, 0], [0, -625]], "target": [0, -125]},
    )["result"]
    exact_obstruction = runtime.invoke(
        "integer_lattice_membership",
        {"matrix": [[1], [0]], "target": [0, 1]},
    )["result"]
    kernel_projection = runtime.invoke(
        "integer_kernel_projection",
        {"matrix": [[1, 0, -2]], "selected_columns": [0]},
    )["result"]
    rank_two_projection = runtime.invoke(
        "integer_kernel_projection",
        {
            "matrix": [[1, 0, -2, -1], [0, 1, -1, -3]],
            "selected_columns": [0, 1],
            "include_kernel_basis": False,
        },
    )["result"]
    zero_projection = runtime.invoke(
        "integer_kernel_projection",
        {"matrix": [[1, 0]], "selected_columns": [0]},
    )["result"]
    ideal = runtime.invoke(
        "ideal_invariants",
        {
            "field": {"kind": "Q"},
            "ambient": "projective",
            "variables": ["x", "y", "z", "w"],
            "generators": [
                [
                    {"coefficient": 1, "exponents": [1, 0, 1, 0]},
                    {"coefficient": -1, "exponents": [0, 2, 0, 0]},
                ],
                [
                    {"coefficient": 1, "exponents": [1, 0, 0, 1]},
                    {"coefficient": -1, "exponents": [0, 1, 1, 0]},
                ],
                [
                    {"coefficient": 1, "exponents": [0, 1, 0, 1]},
                    {"coefficient": -1, "exponents": [0, 0, 2, 0]},
                ],
            ],
        },
    )["result"]
    homology = runtime.invoke(
        "simplicial_homology",
        {"facets": [[0, 1], [1, 2], [0, 2]]},
    )["result"]

    assert factor["factors"] == [[2, 3], [3, 2], [5, 1]]
    assert binomial == {
        "n": 3125,
        "k": 5,
        "value": 2475588476563125,
        "unit": 1,
        "factors": [
            [3, 1],
            [5, 4],
            [7, 1],
            [11, 1],
            [71, 1],
            [223, 1],
            [347, 1],
            [3121, 1],
        ],
        "certificate_verified": True,
    }
    assert multinomial == {
        "power": 4,
        "parts": [1, 2, 1],
        "value": 12,
        "unit": 1,
        "factors": [[2, 2], [3, 1]],
        "certificate_verified": True,
        "modulus": 5,
        "remainder": 2,
        "divisible_by_modulus": False,
    }
    assert matching["pair_types"] == [
        ["a", "a"],
        ["a", "b"],
        ["a", "h"],
        ["b", "b"],
        ["b", "h"],
        ["h", "h"],
    ]
    assert matching["term_count"] == 5
    assert [term["coefficient"] for term in matching["terms"]] == [2520, 2520, 3780, 1260, 315]
    assert matching["total_matching_count"] == 10_395
    assert matching["coefficient_gcd"] == 315
    assert matching["coefficient_gcd_factors"] == [[3, 2], [5, 1], [7, 1]]
    assert matching["nonzero_term_count_modulus"] == 0
    assert matching["all_coefficients_divisible_by_modulus"] is True
    assert matching["certificate"] == {
        "multiplicity_balances_verified": True,
        "closed_coefficient_formula_verified": True,
        "total_matching_count_verified": True,
        "coefficient_gcd_factorization_verified": True,
    }
    assert matching["certificate_verified"] is True
    assert matching_evaluation["evaluated_value"] == 924
    assert matching_evaluation["evaluated_remainder"] == 4
    assert matching_evaluation["certificate"]["pairing_evaluation_from_verified_terms"] is True
    assert capabilities == {
        "version": "0.1.0",
        "domains": [
            "combin",
            "commalg",
            "geometry",
            "groups",
            "homalg",
            "linalg",
            "numthy",
            "topology",
        ],
        "operations": 1078,
    }
    assert factor["certificate_verified"] is True
    assert matrix["invariant_factors"] == [2, 4]
    assert matrix["certificate_verified"] is True
    assert shortest == {
        "rank": 2,
        "bound": 2,
        "max_pairs": 10_000,
        "vectors": [[0, 1], [1, 0], [1, 1]],
        "norms": [2, 2, 2],
        "pair_count": 3,
        "signed_count": 6,
        "max_norm": 2,
        "certificate_verified": True,
    }
    assert orthogonals["rank"] == 2
    assert orthogonals["ambient_absolute_determinant"] == 3
    assert orthogonals["vectors_checked"] == 2
    assert orthogonals["certificate_verified"] is True
    assert [item["vector"] for item in orthogonals["orthogonals"]] == [
        [1, 0],
        [1, 1],
    ]
    for item in orthogonals["orthogonals"]:
        assert item["primitive"] is True
        assert item["vector_content"] == 1
        assert item["norm"] == 2
        assert item["divisibility"] == 1
        assert item["pairing_residuals"] == [0]
        assert item["basis_saturated"] is True
        assert item["orthogonal_gram"] == [[6]]
        assert item["entry_gcd"] == 6
        assert item["invariant_factors"] == [6]
        assert item["absolute_determinant"] == 6
        assert item["determinant_identity_verified"] is True
        assert item["certificate_verified"] is True
    assert campaign_orthogonals["ambient_absolute_determinant"] == 125
    assert campaign_orthogonals["vectors_checked"] == 5
    assert [item["entry_gcd"] for item in campaign_orthogonals["orthogonals"]] == [
        5,
        5,
        5,
        5,
        5,
    ]
    assert [item["invariant_factors"] for item in campaign_orthogonals["orthogonals"]] == [[5, 5, 20]] * 5
    assert [item["absolute_determinant"] for item in campaign_orthogonals["orthogonals"]] == [500] * 5
    assert all(item["determinant_identity_verified"] for item in campaign_orthogonals["orthogonals"])
    assert feasible_membership["status"] == "member"
    assert feasible_membership["residual"] == [0, 0, 0, 0]
    assert feasible_membership["certificate_verified"] is True
    assert [sum(row[index] * feasible_membership["solution"][index] for index in range(len(row))) for row in feasible_matrix] == [
        0,
        0,
        0,
        -125,
    ]
    assert infeasible_membership["status"] == "not_member"
    assert infeasible_membership["obstruction"]["kind"] == "modular_functional"
    assert infeasible_membership["certificate_verified"] is True
    modulus = infeasible_membership["obstruction"]["modulus"]
    assert all(value % modulus == 0 for value in infeasible_membership["obstruction"]["matrix_image"])
    assert infeasible_membership["obstruction"]["target_value"] % modulus != 0
    assert exact_obstruction["status"] == "not_member"
    assert exact_obstruction["obstruction"]["kind"] == "exact_functional"
    assert exact_obstruction["obstruction"]["matrix_image"] == [0]
    assert exact_obstruction["obstruction"]["target_value"] != 0
    assert kernel_projection["rank"] == 1
    assert kernel_projection["nullity"] == 2
    assert kernel_projection["kernel_basis"] == [[0, 1, 0], [2, 0, 1]]
    assert kernel_projection["projected_rank"] == 1
    assert kernel_projection["projected_column_hnf_basis"] == [[2]]
    assert kernel_projection["projected_basis_full_lifts"] == [[2, 0, 1]]
    assert kernel_projection["raw_projection_in_hnf_basis"] == [[0, 1]]
    assert kernel_projection["certificate_verified"] is True
    assert rank_two_projection["projected_invariant_factors"] == [1, 5]
    assert rank_two_projection["projected_column_hnf_basis"] == [[1, 3], [0, 5]]
    assert rank_two_projection["projected_basis_full_lifts"] == [
        [1, 3, 0, 1],
        [0, 5, -1, 2],
    ]
    assert rank_two_projection["kernel_basis_included"] is False
    assert "kernel_basis" not in rank_two_projection
    assert rank_two_projection["certificate_verified"] is True
    assert zero_projection["projected_rank"] == 0
    assert zero_projection["projected_column_hnf_basis"] == []
    assert zero_projection["certificate_verified"] is True
    assert ideal["degree"] == 3
    assert ideal["scheme_dimension"] == 1
    assert ideal["certificate_verified"] is True
    assert homology["groups"] == [
        {"degree": 0, "free_rank": 1, "torsion_coefficients": []},
        {"degree": 1, "free_rank": 1, "torsion_coefficients": []},
    ]
    assert homology["certificate_verified"] is True
