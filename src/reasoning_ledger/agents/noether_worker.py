"""Fixed-protocol worker for an isolated Reasoning Noether installation.

This file is executed by the Noether runtime, not imported by the workbench.
It deliberately accepts declarative JSON operations instead of Python or shell
source, so granting the corresponding agent tool is not a general code-exec
capability.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import os
import re
import resource
import sys
from fractions import Fraction
from typing import Any

_VARIABLE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,31}$")

_SITE_PACKAGES = os.environ.pop("REASONING_NOETHER_SITE_PACKAGES", None)
if _SITE_PACKAGES:
    sys.path.insert(0, _SITE_PACKAGES)


def _limit_process() -> None:
    cpu = int(os.environ.get("REASONING_NOETHER_CPU_SECONDS", "120"))
    memory = int(os.environ.get("REASONING_NOETHER_MEMORY_BYTES", str(4 * 1024**3)))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 1))

    # Darwin may reject replacing an inherited unlimited hard address-space
    # limit with a finite hard limit even though lowering the soft limit is
    # supported. Keep the inherited hard ceiling in that case; the worker is
    # still bounded by the requested soft limit and the outer sandbox.
    try:
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    except ValueError:
        _, hard = resource.getrlimit(resource.RLIMIT_AS)
        bounded = memory if hard == resource.RLIM_INFINITY else min(memory, hard)
        try:
            resource.setrlimit(resource.RLIMIT_AS, (bounded, hard))
        except ValueError:
            if sys.platform != "darwin":
                raise
    resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024**2, 64 * 1024**2))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    os.umask(0o077)


def _object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _keys(value: dict[str, Any], *, allowed: set[str], required: set[str]) -> None:
    missing = required - value.keys()
    unexpected = value.keys() - allowed
    if missing:
        raise ValueError(f"missing fields: {', '.join(sorted(missing))}")
    if unexpected:
        raise ValueError(f"unexpected fields: {', '.join(sorted(unexpected))}")


def _bounded_int(value: Any, name: str, *, bits: int = 65_536) -> int:
    if type(value) is not int:
        raise ValueError(f"{name} must be an integer")
    if value.bit_length() > bits:
        raise ValueError(f"{name} exceeds the {bits:,}-bit limit")
    return value


def _fraction(value: Any, name: str) -> Fraction:
    if type(value) is int:
        result = Fraction(value)
    elif isinstance(value, str) and len(value) <= 20_000:
        try:
            result = Fraction(value)
        except (ValueError, ZeroDivisionError) as error:
            raise ValueError(f"{name} is not a rational number") from error
    else:
        raise ValueError(f"{name} must be an integer or rational string")
    if result.numerator.bit_length() > 65_536 or result.denominator.bit_length() > 65_536:
        raise ValueError(f"{name} exceeds the 65,536-bit limit")
    return result


def _matrix(payload: dict[str, Any]) -> dict[str, Any]:
    _keys(payload, allowed={"ring", "rows"}, required={"ring", "rows"})
    ring_name = payload["ring"]
    rows = payload["rows"]
    if ring_name not in {"Z", "Q"}:
        raise ValueError("matrix ring must be Z or Q")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 512:
        raise ValueError("matrix rows must contain between 1 and 512 rows")
    if not all(isinstance(row, list) for row in rows):
        raise ValueError("every matrix row must be an array")
    columns = len(rows[0])
    if not 1 <= columns <= 512 or any(len(row) != columns for row in rows):
        raise ValueError("matrix rows must have one common width between 1 and 512")
    if len(rows) * columns > 65_536:
        raise ValueError("matrix exceeds the 65,536-entry limit")

    from noether import Q, Z
    from noether.linalg import Matrix

    if ring_name == "Z":
        converted = [[_bounded_int(item, f"rows[{i}][{j}]") for j, item in enumerate(row)] for i, row in enumerate(rows)]
        matrix = Matrix.from_rows(Z, converted)
        smith = matrix.smith_normal_form()
        smith.verify()
        result: dict[str, Any] = {
            "ring": "Z",
            "rows": len(rows),
            "columns": columns,
            "rank": smith.rank,
            "invariant_factors": [int(value) for value in smith.invariant_factors],
            "certificate_verified": True,
        }
        if len(rows) == columns:
            absolute_determinant = 1
            for value in smith.invariant_factors:
                absolute_determinant *= int(value)
            result["absolute_determinant"] = absolute_determinant
    else:
        converted = [[_fraction(item, f"rows[{i}][{j}]") for j, item in enumerate(row)] for i, row in enumerate(rows)]
        matrix = Matrix.from_rows(Q, converted)
        result = {
            "ring": "Q",
            "rows": len(rows),
            "columns": columns,
            "rank": matrix.rank(),
        }
    if ring_name == "Q" and len(rows) == columns:
        result["determinant"] = str(matrix.det())
    return result


def _integral_gram(value: Any) -> list[list[int]]:
    if not isinstance(value, list) or not 1 <= len(value) <= 64:
        raise ValueError("gram must contain between 1 and 64 rows")
    if not all(isinstance(row, list) and len(row) == len(value) for row in value):
        raise ValueError("gram must be a square matrix")
    gram = [[_bounded_int(item, f"gram[{i}][{j}]", bits=4096) for j, item in enumerate(row)] for i, row in enumerate(value)]
    if any(gram[i][j] != gram[j][i] for i in range(len(gram)) for j in range(i)):
        raise ValueError("gram must be symmetric")
    return gram


def _lattice_shortest_vectors(payload: dict[str, Any]) -> dict[str, Any]:
    """Enumerate and verify shortest or bounded vectors of an integral lattice."""

    _keys(
        payload,
        allowed={"gram", "bound", "max_pairs"},
        required={"gram"},
    )
    gram = _integral_gram(payload["gram"])

    raw_bound = payload.get("bound")
    bound = None
    if raw_bound is not None:
        bound = _bounded_int(raw_bound, "bound", bits=4096)
        if bound <= 0:
            raise ValueError("bound must be positive")
    raw_max_pairs = payload.get("max_pairs", 10_000)
    max_pairs = _bounded_int(raw_max_pairs, "max_pairs", bits=32)
    if not 1 <= max_pairs <= 10_000:
        raise ValueError("max_pairs must be between 1 and 10,000")

    from noether import Z
    from noether.linalg import Lattice, Matrix

    lattice = Lattice(Matrix.from_rows(Z, gram))
    enumeration = lattice.shortest_vectors(bound=bound, max_pairs=max_pairs)
    enumeration.verify(lattice)
    vectors = [[int(coordinate) for coordinate in vector] for vector in enumeration.vectors]
    norms = [sum(vector[i] * gram[i][j] * vector[j] for i in range(len(gram)) for j in range(len(gram))) for vector in vectors]
    if enumeration.pair_count != len(vectors):
        raise RuntimeError("verified Noether enumeration has inconsistent pair count")
    if enumeration.signed_count != 2 * len(vectors):
        raise RuntimeError("verified Noether enumeration has inconsistent signed count")
    if vectors and enumeration.max_norm != max(norms):
        raise RuntimeError("verified Noether enumeration has inconsistent maximum norm")
    return {
        "rank": len(gram),
        "bound": bound,
        "max_pairs": max_pairs,
        "vectors": vectors,
        "norms": norms,
        "pair_count": enumeration.pair_count,
        "signed_count": enumeration.signed_count,
        "max_norm": enumeration.max_norm,
        "certificate_verified": True,
    }


def _lattice_orthogonal_complements(payload: dict[str, Any]) -> dict[str, Any]:
    """Compute saturated vector orthogonals and their exact lattice invariants."""

    _keys(payload, allowed={"gram", "vectors"}, required={"gram", "vectors"})
    gram = _integral_gram(payload["gram"])
    if len(gram) < 2:
        raise ValueError("orthogonal complements require gram rank at least 2")
    raw_vectors = payload["vectors"]
    if not isinstance(raw_vectors, list) or not 1 <= len(raw_vectors) <= 128:
        raise ValueError("vectors must contain between 1 and 128 vectors")
    if not all(isinstance(vector, list) and len(vector) == len(gram) for vector in raw_vectors):
        raise ValueError("every vector length must equal the gram rank")
    vectors = [
        [_bounded_int(coordinate, f"vectors[{i}][{j}]", bits=4096) for j, coordinate in enumerate(vector)]
        for i, vector in enumerate(raw_vectors)
    ]
    if any(not any(vector) for vector in vectors):
        raise ValueError("orthogonal-complement vectors must be nonzero")

    from noether import Z
    from noether.linalg import Lattice, Matrix

    gram_matrix = Matrix.from_rows(Z, gram)
    Lattice(gram_matrix)
    gram_smith = gram_matrix.smith_normal_form()
    gram_smith.verify()
    ambient_determinant = math.prod(int(value) for value in gram_smith.invariant_factors)
    orthogonals = []
    for vector in vectors:
        pairings = [sum(vector[i] * gram[i][j] for i in range(len(gram))) for j in range(len(gram))]
        pairing_matrix = Matrix.from_rows(Z, [pairings])
        pairing_smith = pairing_matrix.smith_normal_form_with_transforms()
        pairing_smith.verify()
        if pairing_smith.rank != 1:
            raise RuntimeError("nonzero vector has zero pairing row in a positive-definite lattice")
        right = [[int(value) for value in row] for row in pairing_smith.right.to_rows()]
        basis_matrix = [[right[i][j] for j in range(pairing_smith.rank, len(gram))] for i in range(len(gram))]
        residuals = [sum(pairings[i] * basis_matrix[i][j] for i in range(len(gram))) for j in range(len(gram) - 1)]
        if any(residuals):
            raise RuntimeError("verified Smith kernel basis has a nonzero pairing residual")
        orthogonal_gram = [
            [
                sum(basis_matrix[i][a] * gram[i][j] * basis_matrix[j][b] for i in range(len(gram)) for j in range(len(gram)))
                for b in range(len(gram) - 1)
            ]
            for a in range(len(gram) - 1)
        ]
        orthogonal_matrix = Matrix.from_rows(Z, orthogonal_gram)
        orthogonal_smith = orthogonal_matrix.smith_normal_form()
        orthogonal_smith.verify()
        if orthogonal_smith.rank != len(gram) - 1:
            raise RuntimeError("positive-definite orthogonal complement lost rank")
        orthogonal_determinant = math.prod(int(value) for value in orthogonal_smith.invariant_factors)
        norm = sum(vector[i] * gram[i][j] * vector[j] for i in range(len(gram)) for j in range(len(gram)))
        divisibility = math.gcd(*(abs(value) for value in pairings))
        if orthogonal_determinant * divisibility**2 != ambient_determinant * norm:
            raise RuntimeError("orthogonal-complement determinant identity failed")
        orthogonals.append(
            {
                "vector": vector,
                "vector_content": math.gcd(*(abs(value) for value in vector)),
                "primitive": math.gcd(*(abs(value) for value in vector)) == 1,
                "norm": norm,
                "pairings": pairings,
                "divisibility": divisibility,
                "basis": [[basis_matrix[i][j] for i in range(len(gram))] for j in range(len(gram) - 1)],
                "pairing_residuals": residuals,
                "basis_saturated": True,
                "orthogonal_gram": orthogonal_gram,
                "entry_gcd": math.gcd(*(abs(value) for row in orthogonal_gram for value in row)),
                "invariant_factors": [int(value) for value in orthogonal_smith.invariant_factors],
                "absolute_determinant": orthogonal_determinant,
                "determinant_identity_verified": True,
                "certificate_verified": True,
            }
        )
    return {
        "rank": len(gram),
        "ambient_absolute_determinant": ambient_determinant,
        "vectors_checked": len(vectors),
        "orthogonals": orthogonals,
        "certificate_verified": True,
    }


def _integer_lattice_membership(payload: dict[str, Any]) -> dict[str, Any]:
    """Decide whether a target belongs to an integer matrix's column lattice."""

    _keys(payload, allowed={"matrix", "target"}, required={"matrix", "target"})
    rows = payload["matrix"]
    target = payload["target"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= 128:
        raise ValueError("matrix must contain between 1 and 128 rows")
    if not all(isinstance(row, list) for row in rows):
        raise ValueError("every matrix row must be an array")
    columns = len(rows[0])
    if not 1 <= columns <= 256 or any(len(row) != columns for row in rows):
        raise ValueError("matrix rows must have one common width between 1 and 256")
    if len(rows) * columns > 16_384:
        raise ValueError("matrix exceeds the 16,384-entry limit")
    if not isinstance(target, list) or len(target) != len(rows):
        raise ValueError("target length must equal the number of matrix rows")
    converted = [[_bounded_int(item, f"matrix[{i}][{j}]", bits=4096) for j, item in enumerate(row)] for i, row in enumerate(rows)]
    checked_target = [_bounded_int(item, f"target[{i}]", bits=4096) for i, item in enumerate(target)]

    from noether import Z
    from noether.linalg import Matrix

    matrix = Matrix.from_rows(Z, converted)
    smith = matrix.smith_normal_form_with_transforms()
    smith.verify()
    form = [[int(item) for item in row] for row in smith.form.to_rows()]
    left = [[int(item) for item in row] for row in smith.left.to_rows()]
    right = [[int(item) for item in row] for row in smith.right.to_rows()]
    transformed_target = [sum(left[i][j] * checked_target[j] for j in range(len(rows))) for i in range(len(rows))]
    rank = smith.rank
    offending_index = None
    for index in range(rank):
        diagonal = form[index][index]
        if diagonal == 0:
            raise RuntimeError("verified Smith form has a zero entry inside its rank")
        if transformed_target[index] % abs(diagonal):
            offending_index = index
            break
    if offending_index is None:
        offending_index = next(
            (index for index in range(rank, len(rows)) if transformed_target[index] != 0),
            None,
        )

    matrix_canonical = (
        json.dumps(
            {"matrix": converted},
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n"
    )
    problem_canonical = (
        json.dumps(
            {"matrix": converted, "target": checked_target},
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n"
    )
    common = {
        "matrix_sha256": hashlib.sha256(matrix_canonical.encode("utf-8")).hexdigest(),
        "problem_sha256": hashlib.sha256(problem_canonical.encode("utf-8")).hexdigest(),
        "rows": len(rows),
        "columns": columns,
        "rank": rank,
        "invariant_factors": [int(item) for item in smith.invariant_factors],
    }

    if offending_index is None:
        smith_coordinates = [0] * columns
        for index in range(rank):
            smith_coordinates[index] = transformed_target[index] // form[index][index]
        solution = [sum(right[i][j] * smith_coordinates[j] for j in range(columns)) for i in range(columns)]
        residual = [sum(converted[i][j] * solution[j] for j in range(columns)) - checked_target[i] for i in range(len(rows))]
        if any(residual):
            raise RuntimeError("Smith solution failed direct residual verification")
        return {
            **common,
            "status": "member",
            "solution": solution,
            "residual": residual,
            "certificate_verified": True,
        }

    functional = left[offending_index]
    functional_matrix = [sum(functional[i] * converted[i][j] for i in range(len(rows))) for j in range(columns)]
    target_value = transformed_target[offending_index]
    if offending_index < rank:
        modulus = abs(form[offending_index][offending_index])
        if any(value % modulus for value in functional_matrix) or target_value % modulus == 0:
            raise RuntimeError("Smith modular obstruction failed direct verification")
        obstruction = {
            "kind": "modular_functional",
            "functional": functional,
            "modulus": modulus,
            "matrix_image": functional_matrix,
            "target_value": target_value,
            "target_remainder": target_value % modulus,
        }
    else:
        if any(functional_matrix) or target_value == 0:
            raise RuntimeError("Smith exact obstruction failed direct verification")
        obstruction = {
            "kind": "exact_functional",
            "functional": functional,
            "matrix_image": functional_matrix,
            "target_value": target_value,
        }
    return {
        **common,
        "status": "not_member",
        "obstruction": obstruction,
        "certificate_verified": True,
    }


def _integer_kernel_projection(payload: dict[str, Any]) -> dict[str, Any]:
    """Project an integral kernel and retain exact lifts of a column-HNF basis."""

    _keys(
        payload,
        allowed={"matrix", "selected_columns", "include_kernel_basis"},
        required={"matrix", "selected_columns"},
    )
    rows = payload["matrix"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= 512:
        raise ValueError("matrix must contain between 1 and 512 rows")
    if not all(isinstance(row, list) for row in rows):
        raise ValueError("every matrix row must be an array")
    columns = len(rows[0])
    if not 1 <= columns <= 512 or any(len(row) != columns for row in rows):
        raise ValueError("matrix rows must have one common width between 1 and 512")
    if len(rows) * columns > 262_144:
        raise ValueError("matrix exceeds the 262,144-entry limit")
    converted = [
        [
            _bounded_int(item, f"matrix[{i}][{j}]", bits=4096)
            for j, item in enumerate(row)
        ]
        for i, row in enumerate(rows)
    ]
    selected = payload["selected_columns"]
    if (
        not isinstance(selected, list)
        or not 1 <= len(selected) <= min(columns, 128)
        or any(type(index) is not int or not 0 <= index < columns for index in selected)
        or len(set(selected)) != len(selected)
    ):
        raise ValueError(
            "selected_columns must contain distinct in-range integer indices"
        )
    include_kernel_basis = payload.get("include_kernel_basis", True)
    if type(include_kernel_basis) is not bool:
        raise ValueError("include_kernel_basis must be a boolean")

    from noether import Z
    from noether.linalg import Matrix

    def matrix_rows(matrix) -> list[list[int]]:
        return [[int(item) for item in row] for row in matrix.to_rows()]

    def columns_from_rows(matrix_rows: list[list[int]]) -> list[list[int]]:
        if not matrix_rows:
            return []
        return [
            [matrix_rows[row][column] for row in range(len(matrix_rows))]
            for column in range(len(matrix_rows[0]))
        ]

    def multiply(
        left: list[list[int]], right: list[list[int]]
    ) -> list[list[int]]:
        if not left:
            return []
        inner = len(left[0])
        if not right:
            return [[] for _ in left]
        if len(right) != inner:
            raise RuntimeError("internal matrix multiplication shape mismatch")
        width = len(right[0])
        return [
            [
                sum(left[i][k] * right[k][j] for k in range(inner))
                for j in range(width)
            ]
            for i in range(len(left))
        ]

    def solve_columns(
        source: list[list[int]], targets: list[list[int]]
    ) -> list[list[int]]:
        """Return X with source*X=targets, failing unless every lift is integral."""

        if not targets:
            return [[] for _ in range(len(source[0]))]
        smith = Matrix.from_rows(Z, source).smith_normal_form_with_transforms()
        smith.verify()
        form = matrix_rows(smith.form)
        left = matrix_rows(smith.left)
        right = matrix_rows(smith.right)
        target_columns = columns_from_rows(targets)
        solutions = []
        for target_index, target in enumerate(target_columns):
            transformed = [
                sum(left[i][j] * target[j] for j in range(len(source)))
                for i in range(len(source))
            ]
            coordinates = [0] * len(source[0])
            for index in range(smith.rank):
                diagonal = form[index][index]
                if diagonal == 0 or transformed[index] % diagonal:
                    raise RuntimeError(
                        f"projected HNF column {target_index} has no integral lift"
                    )
                coordinates[index] = transformed[index] // diagonal
            if any(transformed[index] for index in range(smith.rank, len(source))):
                raise RuntimeError(
                    f"projected HNF column {target_index} escapes the projection span"
                )
            solutions.append(
                [
                    sum(right[i][j] * coordinates[j] for j in range(len(coordinates)))
                    for i in range(len(coordinates))
                ]
            )
        solution_rows = [
            [solutions[column][row] for column in range(len(solutions))]
            for row in range(len(source[0]))
        ]
        if multiply(source, solution_rows) != targets:
            raise RuntimeError("integral projection lifts failed direct verification")
        return solution_rows

    matrix = Matrix.from_rows(Z, converted)
    smith = matrix.smith_normal_form_with_transforms()
    smith.verify()
    right = matrix_rows(smith.right)
    rank = smith.rank
    nullity = columns - rank
    kernel_rows = [row[rank:] for row in right]
    zero_kernel_residual = [[0] * nullity for _ in converted]
    if multiply(converted, kernel_rows) != zero_kernel_residual:
        raise RuntimeError("verified Smith kernel basis has a nonzero residual")

    projected_rows = [kernel_rows[index] for index in selected]
    if nullity == 0 or not any(item for row in projected_rows for item in row):
        projected_rank = 0
        projected_basis_rows = [[] for _ in selected]
        kernel_coefficients = [[] for _ in range(nullity)]
        full_lift_rows = [[] for _ in range(columns)]
        raw_projection_in_basis = [[] for _ in range(0)]
        projection_invariant_factors: list[int] = []
    else:
        projected_matrix = Matrix.from_rows(Z, projected_rows)
        projected_smith = projected_matrix.smith_normal_form_with_transforms()
        projected_smith.verify()
        projected_rank = projected_smith.rank
        projection_invariant_factors = [
            int(item) for item in projected_smith.invariant_factors
        ]

        hnf_full = projected_matrix.transpose().hermite_normal_form().transpose()
        hnf_rows = matrix_rows(hnf_full)
        nonzero_columns = [
            index
            for index in range(len(hnf_rows[0]))
            if any(hnf_rows[row][index] for row in range(len(hnf_rows)))
        ]
        if len(nonzero_columns) != projected_rank:
            raise RuntimeError("column HNF rank disagrees with verified Smith rank")
        projected_basis_rows = [
            [row[index] for index in nonzero_columns] for row in hnf_rows
        ]
        basis_matrix = Matrix.from_rows(Z, projected_basis_rows)
        canonical_check = (
            basis_matrix.transpose().hermite_normal_form().transpose()
        )
        if matrix_rows(canonical_check) != projected_basis_rows:
            raise RuntimeError("projected basis is not in canonical column HNF")

        kernel_coefficients = solve_columns(projected_rows, projected_basis_rows)
        full_lift_rows = multiply(kernel_rows, kernel_coefficients)
        if [full_lift_rows[index] for index in selected] != projected_basis_rows:
            raise RuntimeError("full kernel lifts do not project to the HNF basis")
        if multiply(converted, full_lift_rows) != [
            [0] * projected_rank for _ in converted
        ]:
            raise RuntimeError("full projected-basis lifts leave the kernel")
        raw_projection_in_basis = solve_columns(
            projected_basis_rows, projected_rows
        )

    normalized_request = {
        "matrix": converted,
        "selected_columns": selected,
        "include_kernel_basis": include_kernel_basis,
    }
    request_canonical = json.dumps(
        normalized_request,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ) + "\n"
    kernel_basis = columns_from_rows(kernel_rows)
    projected_basis = columns_from_rows(projected_basis_rows)
    full_lifts = columns_from_rows(full_lift_rows)
    coefficient_columns = columns_from_rows(kernel_coefficients)
    result = {
        "problem_sha256": hashlib.sha256(request_canonical.encode("utf-8")).hexdigest(),
        "rows": len(converted),
        "columns": columns,
        "rank": rank,
        "nullity": nullity,
        "selected_columns": selected,
        "kernel_basis_included": include_kernel_basis,
        "kernel_basis_sha256": hashlib.sha256(
            (
                json.dumps(
                    kernel_basis,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n"
            ).encode("utf-8")
        ).hexdigest(),
        "projected_rank": projected_rank,
        "projected_invariant_factors": projection_invariant_factors,
        "projected_column_hnf_basis": projected_basis,
        "projected_basis_kernel_coefficients": coefficient_columns,
        "projected_basis_full_lifts": full_lifts,
        "raw_projection_in_hnf_basis": raw_projection_in_basis,
        "certificate": {
            "smith_kernel_verified": True,
            "kernel_basis_saturated": True,
            "column_hnf_canonical": True,
            "hnf_generators_have_integral_kernel_lifts": True,
            "raw_and_hnf_projection_lattices_equal": True,
        },
        "certificate_verified": True,
    }
    if include_kernel_basis:
        result["kernel_basis"] = kernel_basis
    return result


def _coefficient_field(value: Any):
    specification = _object(value, "field")
    _keys(specification, allowed={"kind", "prime"}, required={"kind"})
    kind = specification["kind"]
    if kind == "Q":
        if "prime" in specification:
            raise ValueError("field prime is only valid for Fp")
        from noether import Q

        return Q, "Q"
    if kind == "Fp":
        prime = _bounded_int(specification.get("prime"), "field.prime", bits=31)
        if not 2 <= prime <= 2_147_483_647:
            raise ValueError("field.prime must be between 2 and 2,147,483,647")
        from noether.numthy import FiniteField

        return FiniteField.prime(prime), f"F_{prime}"
    raise ValueError("field kind must be Q or Fp")


def _polynomial(ring, field_name: str, value: Any, index: int):
    if not isinstance(value, list) or not 1 <= len(value) <= 4096:
        raise ValueError(f"generators[{index}] must contain between 1 and 4,096 terms")
    terms = []
    for term_index, raw_term in enumerate(value):
        term = _object(raw_term, f"generators[{index}][{term_index}]")
        _keys(term, allowed={"coefficient", "exponents"}, required={"coefficient", "exponents"})
        exponents = term["exponents"]
        if not isinstance(exponents, list) or len(exponents) != len(ring.variables):
            raise ValueError(f"generators[{index}][{term_index}].exponents must have {len(ring.variables)} entries")
        checked_exponents = []
        for exponent in exponents:
            exponent = _bounded_int(exponent, "polynomial exponent", bits=31)
            if not 0 <= exponent <= 10_000:
                raise ValueError("polynomial exponents must be between 0 and 10,000")
            checked_exponents.append(exponent)
        coefficient = (
            _fraction(term["coefficient"], "polynomial coefficient")
            if field_name == "Q"
            else _bounded_int(term["coefficient"], "polynomial coefficient")
        )
        terms.append((checked_exponents, coefficient))
    return ring.from_terms(terms)


def _ideal(payload: dict[str, Any]) -> dict[str, Any]:
    _keys(
        payload,
        allowed={"field", "ambient", "variables", "generators", "check_smooth"},
        required={"field", "ambient", "variables", "generators"},
    )
    field, field_name = _coefficient_field(payload["field"])
    variables = payload["variables"]
    if (
        not isinstance(variables, list)
        or not 1 <= len(variables) <= 16
        or not all(isinstance(value, str) and _VARIABLE.fullmatch(value) for value in variables)
        or len(set(variables)) != len(variables)
    ):
        raise ValueError("variables must be 1 to 16 distinct identifier strings")
    generators = payload["generators"]
    if not isinstance(generators, list) or len(generators) > 64:
        raise ValueError("generators must be an array of at most 64 polynomials")
    ambient_kind = payload["ambient"]
    if ambient_kind not in {"ideal", "affine", "projective"}:
        raise ValueError("ambient must be ideal, affine, or projective")
    if type(payload.get("check_smooth", False)) is not bool:
        raise ValueError("check_smooth must be a boolean")

    from noether.commalg import AffineSpace, PolynomialRing, ProjectiveSpace

    ambient = None
    if ambient_kind == "affine":
        ambient = AffineSpace(field, len(variables), names=variables)
        ring = ambient.coordinate_ring
    elif ambient_kind == "projective":
        if len(variables) < 2:
            raise ValueError("projective ambient space needs at least two variables")
        ambient = ProjectiveSpace(field, len(variables) - 1, names=variables)
        ring = ambient.coordinate_ring
    else:
        ring = PolynomialRing(field, variables)
    polynomials = [_polynomial(ring, field_name, value, index) for index, value in enumerate(generators)]
    ideal = ring.ideal(polynomials)
    ideal.verify()
    hilbert = ideal.hilbert_polynomial()
    result: dict[str, Any] = {
        "field": field_name,
        "ambient": ambient_kind,
        "variables": variables,
        "groebner_basis": [str(value) for value in ideal.groebner_basis()],
        "ideal_json": json.loads(ideal.to_json()),
        "krull_dimension": ideal.dimension(),
        "degree": ideal.degree(),
        "hilbert_numerator": ideal.hilbert_numerator(),
        "hilbert_polynomial": str(hilbert),
        "certificate_verified": True,
    }
    if ambient is not None:
        scheme = ambient.closed_subscheme(ideal)
        result["scheme_dimension"] = scheme.dimension()
        if payload.get("check_smooth", False):
            try:
                result["smoothness"] = {
                    "supported": True,
                    "is_smooth": scheme.is_smooth(),
                    "singular_locus_dimension": scheme.jacobian_subscheme().dimension(),
                }
            except Exception as error:
                result["smoothness"] = {
                    "supported": False,
                    "error_type": type(error).__name__,
                    "detail": str(error),
                }
    return result


def _factor(payload: dict[str, Any]) -> dict[str, Any]:
    _keys(payload, allowed={"value"}, required={"value"})
    value = _bounded_int(payload["value"], "value")
    from noether.numthy import factor

    result = factor(value)
    verified = bool(result.verify(value))
    return {
        "value": value,
        "unit": int(result.unit),
        "factors": [[int(prime), int(exponent)] for prime, exponent in result.factors],
        "certificate_verified": verified,
    }


def _binomial(payload: dict[str, Any]) -> dict[str, Any]:
    _keys(payload, allowed={"n", "k"}, required={"n", "k"})
    n = _bounded_int(payload["n"], "n", bits=31)
    k = _bounded_int(payload["k"], "k", bits=31)
    if not 0 <= n <= 1_000_000:
        raise ValueError("n must be between 0 and 1,000,000")
    if not 0 <= k <= n:
        raise ValueError("k must be between 0 and n")
    if min(k, n - k) > 128:
        raise ValueError("min(k, n-k) exceeds the 128-factor limit")
    value = math.comb(n, k)
    if value.bit_length() > 4_096:
        raise ValueError("binomial coefficient exceeds the 4,096-bit limit")

    from noether.numthy import factor

    factorization = factor(value)
    verified = bool(factorization.verify(value))
    factors = [[int(prime), int(exponent)] for prime, exponent in factorization.factors]
    return {
        "n": n,
        "k": k,
        "value": value,
        "unit": int(factorization.unit),
        "factors": factors,
        "certificate_verified": verified,
    }


def _multinomial(payload: dict[str, Any]) -> dict[str, Any]:
    """Compute a coefficient in a powered sum, excluding fixed outside factors."""

    _keys(payload, allowed={"power", "parts", "modulus"}, required={"power", "parts"})
    power = _bounded_int(payload["power"], "power", bits=31)
    parts = payload["parts"]
    if not 0 <= power <= 1_000_000:
        raise ValueError("power must be between 0 and 1,000,000")
    if not isinstance(parts, list) or not 1 <= len(parts) <= 128:
        raise ValueError("parts must contain between 1 and 128 exponents")
    checked_parts = [_bounded_int(value, f"parts[{index}]", bits=31) for index, value in enumerate(parts)]
    if any(value < 0 for value in checked_parts):
        raise ValueError("parts must be nonnegative")
    if sum(checked_parts) != power:
        raise ValueError("parts must sum exactly to power; do not include fixed factors outside the powered sum")

    value = 1
    remaining = power
    for part in checked_parts:
        value *= math.comb(remaining, part)
        remaining -= part
        if value.bit_length() > 4_096:
            raise ValueError("multinomial coefficient exceeds the 4,096-bit limit")

    from noether.numthy import factor

    factorization = factor(value)
    verified = bool(factorization.verify(value))
    result = {
        "power": power,
        "parts": checked_parts,
        "value": value,
        "unit": int(factorization.unit),
        "factors": [[int(prime), int(exponent)] for prime, exponent in factorization.factors],
        "certificate_verified": verified,
    }
    if "modulus" in payload:
        modulus = _bounded_int(payload["modulus"], "modulus", bits=4096)
        if modulus < 2:
            raise ValueError("modulus must be at least 2")
        result.update(
            {
                "modulus": modulus,
                "remainder": value % modulus,
                "divisible_by_modulus": value % modulus == 0,
            }
        )
    return result


def _matching_polynomial_terms(
    counts: tuple[int, ...],
    *,
    max_terms: int,
) -> tuple[list[tuple[int, int]], list[tuple[tuple[int, ...], int]]]:
    """Compress labeled perfect matchings by their multiset pair monomial."""

    from functools import cache

    pair_types = [(left, right) for left in range(len(counts)) for right in range(left, len(counts))]
    pair_index = {pair: index for index, pair in enumerate(pair_types)}
    zero = (0,) * len(pair_types)

    @cache
    def recurse(state: tuple[int, ...]) -> dict[tuple[int, ...], int]:
        if not any(state):
            return {zero: 1}
        left = next(index for index, count in enumerate(state) if count)
        after_left = list(state)
        after_left[left] -= 1
        result: dict[tuple[int, ...], int] = {}
        for right in range(left, len(state)):
            partner_choices = after_left[right]
            if not partner_choices:
                continue
            remainder = after_left.copy()
            remainder[right] -= 1
            pair_position = pair_index[(left, right)]
            for exponents, coefficient in recurse(tuple(remainder)).items():
                updated = list(exponents)
                updated[pair_position] += 1
                key = tuple(updated)
                result[key] = result.get(key, 0) + partner_choices * coefficient
            if len(result) > max_terms:
                raise ValueError(f"perfect-matching polynomial exceeds max_terms={max_terms}")
        return result

    terms = sorted(recurse(counts).items())
    return pair_types, terms


def _perfect_matching_polynomial(payload: dict[str, Any]) -> dict[str, Any]:
    """Compute and independently verify an orbit-compressed pairing sum."""

    _keys(
        payload,
        allowed={"multiplicities", "pairings", "modulus", "max_terms"},
        required={"multiplicities"},
    )
    raw_multiplicities = payload["multiplicities"]
    if not isinstance(raw_multiplicities, list) or not 1 <= len(raw_multiplicities) <= 12:
        raise ValueError("multiplicities must contain between 1 and 12 labeled entries")
    multiplicities = []
    labels: list[str] = []
    counts: list[int] = []
    for index, item in enumerate(raw_multiplicities):
        item = _object(item, f"multiplicities[{index}]")
        _keys(item, allowed={"label", "count"}, required={"label", "count"})
        label = item["label"]
        if not isinstance(label, str) or not _VARIABLE.fullmatch(label):
            raise ValueError(f"multiplicities[{index}].label must be a simple variable name")
        if label in labels:
            raise ValueError(f"duplicate multiplicity label: {label}")
        count = _bounded_int(item["count"], f"multiplicities[{index}].count", bits=31)
        if not 1 <= count <= 12:
            raise ValueError("every multiplicity count must be between 1 and 12")
        labels.append(label)
        counts.append(count)
        multiplicities.append({"label": label, "count": count})

    degree = sum(counts)
    if not 2 <= degree <= 12 or degree % 2:
        raise ValueError("the total multiplicity must be even and between 2 and 12")
    max_terms = _bounded_int(payload.get("max_terms", 5_000), "max_terms", bits=31)
    if not 1 <= max_terms <= 10_000:
        raise ValueError("max_terms must be between 1 and 10,000")
    modulus = None
    if "modulus" in payload:
        modulus = _bounded_int(payload["modulus"], "modulus", bits=4096)
        if modulus < 2:
            raise ValueError("modulus must be at least 2")

    pair_indices, compressed_terms = _matching_polynomial_terms(tuple(counts), max_terms=max_terms)
    pair_types = [[labels[left], labels[right]] for left, right in pair_indices]
    pairing_assignments: dict[tuple[int, int], int] | None = None
    pairing_values: list[dict[str, Any]] | None = None
    if "pairings" in payload:
        raw_pairings = payload["pairings"]
        if not isinstance(raw_pairings, list) or not 1 <= len(raw_pairings) <= 78:
            raise ValueError("pairings must contain between 1 and 78 labeled values")
        label_indices = {label: index for index, label in enumerate(labels)}
        pairing_assignments = {}
        for index, item in enumerate(raw_pairings):
            item = _object(item, f"pairings[{index}]")
            _keys(item, allowed={"left", "right", "value"}, required={"left", "right", "value"})
            left_label = item["left"]
            right_label = item["right"]
            if left_label not in label_indices or right_label not in label_indices:
                raise ValueError(f"pairings[{index}] references an unknown multiplicity label")
            left, right = sorted((label_indices[left_label], label_indices[right_label]))
            pair = (left, right)
            if pair in pairing_assignments:
                raise ValueError(f"duplicate pairing value: {labels[left]}:{labels[right]}")
            pairing_assignments[pair] = _bounded_int(
                item["value"],
                f"pairings[{index}].value",
                bits=4_096,
            )

        active_pairs = {
            pair
            for exponents, _coefficient in compressed_terms
            for pair, exponent in zip(pair_indices, exponents, strict=True)
            if exponent
        }
        missing_pairs = active_pairs - pairing_assignments.keys()
        unexpected_pairs = pairing_assignments.keys() - active_pairs
        if missing_pairs:
            names = ", ".join(f"{labels[left]}:{labels[right]}" for left, right in sorted(missing_pairs))
            raise ValueError(f"missing active pairing values: {names}")
        if unexpected_pairs:
            names = ", ".join(
                f"{labels[left]}:{labels[right]}" for left, right in sorted(unexpected_pairs)
            )
            raise ValueError(f"inactive pairing values are not accepted: {names}")
        pairing_values = [
            {"left": labels[left], "right": labels[right], "value": pairing_assignments[(left, right)]}
            for left, right in sorted(active_pairs)
        ]

    numerator = math.prod(math.factorial(count) for count in counts)
    verified_terms = []
    coefficients = []
    evaluated_values: list[int] = []
    for exponents, coefficient in compressed_terms:
        balances = [0] * len(labels)
        denominator = 1
        for exponent, (left, right) in zip(exponents, pair_indices, strict=True):
            if left == right:
                balances[left] += 2 * exponent
                denominator *= 2**exponent * math.factorial(exponent)
            else:
                balances[left] += exponent
                balances[right] += exponent
                denominator *= math.factorial(exponent)
        if balances != counts:
            raise RuntimeError("perfect-matching term violates its multiplicity balance")
        expected_coefficient, remainder = divmod(numerator, denominator)
        if remainder or expected_coefficient != coefficient:
            raise RuntimeError("perfect-matching recurrence disagrees with the closed coefficient formula")
        term: dict[str, Any] = {
            "exponents": list(exponents),
            "coefficient": coefficient,
        }
        if modulus is not None:
            term.update(
                {
                    "remainder": coefficient % modulus,
                    "divisible_by_modulus": coefficient % modulus == 0,
                }
            )
        if pairing_assignments is not None:
            evaluated_value = coefficient
            for exponent, pair in zip(exponents, pair_indices, strict=True):
                if exponent:
                    evaluated_value *= pairing_assignments[pair] ** exponent
                    if evaluated_value.bit_length() > 65_536:
                        raise ValueError("evaluated perfect-matching polynomial exceeds the 65,536-bit limit")
            term["evaluated_value"] = evaluated_value
            if modulus is not None:
                term["evaluated_remainder"] = evaluated_value % modulus
            evaluated_values.append(evaluated_value)
        verified_terms.append(term)
        coefficients.append(coefficient)

    total_matching_count = sum(coefficients)
    expected_total = math.prod(range(degree - 1, 0, -2))
    if total_matching_count != expected_total:
        raise RuntimeError("compressed coefficients do not sum to the number of labeled perfect matchings")
    coefficient_gcd = math.gcd(*coefficients)
    from noether.numthy import factor

    factorization = factor(coefficient_gcd)
    factorization_verified = bool(factorization.verify(coefficient_gcd))
    certificate = {
        "multiplicity_balances_verified": True,
        "closed_coefficient_formula_verified": True,
        "total_matching_count_verified": True,
        "coefficient_gcd_factorization_verified": factorization_verified,
    }
    if pairing_assignments is not None:
        certificate["pairing_evaluation_from_verified_terms"] = True
    result: dict[str, Any] = {
        "multiplicities": multiplicities,
        "degree": degree,
        "matching_size": degree // 2,
        "pair_types": pair_types,
        "term_count": len(verified_terms),
        "terms": verified_terms,
        "total_matching_count": total_matching_count,
        "coefficient_gcd": coefficient_gcd,
        "coefficient_gcd_unit": int(factorization.unit),
        "coefficient_gcd_factors": [
            [int(prime), int(exponent)] for prime, exponent in factorization.factors
        ],
        "certificate": certificate,
        "certificate_verified": all(certificate.values()),
    }
    if modulus is not None:
        nonzero_terms = sum(coefficient % modulus != 0 for coefficient in coefficients)
        result.update(
            {
                "modulus": modulus,
                "coefficient_gcd_remainder": coefficient_gcd % modulus,
                "nonzero_term_count_modulus": nonzero_terms,
                "all_coefficients_divisible_by_modulus": nonzero_terms == 0,
            }
        )
    if pairing_assignments is not None:
        evaluated_value = sum(evaluated_values)
        result.update(
            {
                "pairing_values": pairing_values,
                "evaluated_value": evaluated_value,
            }
        )
        if modulus is not None:
            result.update(
                {
                    "evaluated_remainder": evaluated_value % modulus,
                    "evaluated_divisible_by_modulus": evaluated_value % modulus == 0,
                }
            )
    return result


def _simplicial_homology(payload: dict[str, Any]) -> dict[str, Any]:
    _keys(payload, allowed={"facets"}, required={"facets"})
    facets = payload["facets"]
    if not isinstance(facets, list) or not 1 <= len(facets) <= 4096:
        raise ValueError("facets must contain between 1 and 4,096 facets")
    checked = []
    entries = 0
    for index, facet in enumerate(facets):
        if not isinstance(facet, list) or not facet:
            raise ValueError(f"facets[{index}] must be a nonempty vertex array")
        vertices = [_bounded_int(value, "vertex", bits=31) for value in facet]
        if any(value < 0 for value in vertices) or len(set(vertices)) != len(vertices):
            raise ValueError("facet vertices must be distinct nonnegative integers")
        entries += len(vertices)
        checked.append(vertices)
    if entries > 65_536:
        raise ValueError("facets exceed the 65,536-vertex-entry limit")
    from noether.topology import SimplicialComplex

    homology = SimplicialComplex(checked).graded_homology()
    homology.verify()
    return {
        "groups": [
            {
                "degree": group.degree,
                "free_rank": group.free_rank,
                "torsion_coefficients": [int(value) for value in group.torsion_coefficients],
            }
            for group in homology.groups
        ],
        "certificate_verified": True,
    }


def _swarm_topology(payload: dict[str, Any]) -> dict[str, Any]:
    """Canonicalize and verify one swarm interaction substrate in Noether."""

    _keys(payload, allowed={"facets", "edges", "directed"}, required={"facets", "edges", "directed"})
    facets = payload["facets"]
    edges = payload["edges"]
    directed = payload["directed"]
    if type(directed) is not bool:
        raise ValueError("directed must be a boolean")
    if not isinstance(facets, list) or not 1 <= len(facets) <= 4096:
        raise ValueError("facets must contain between 1 and 4,096 facets")
    checked_facets: list[list[int]] = []
    entries = 0
    for index, facet in enumerate(facets):
        if not isinstance(facet, list) or not facet:
            raise ValueError(f"facets[{index}] must be a nonempty vertex array")
        vertices = [_bounded_int(value, f"facets[{index}] vertex", bits=31) for value in facet]
        if any(value < 0 for value in vertices) or len(set(vertices)) != len(vertices):
            raise ValueError("facet vertices must be distinct nonnegative integers")
        entries += len(vertices)
        checked_facets.append(vertices)
    if entries > 65_536:
        raise ValueError("facets exceed the 65,536-vertex-entry limit")
    if not isinstance(edges, list) or len(edges) > 32_768:
        raise ValueError("edges must contain at most 32,768 pairs")
    checked_edges: list[tuple[int, int]] = []
    for index, edge in enumerate(edges):
        if not isinstance(edge, list) or len(edge) != 2:
            raise ValueError(f"edges[{index}] must be a vertex pair")
        left = _bounded_int(edge[0], f"edges[{index}][0]", bits=31)
        right = _bounded_int(edge[1], f"edges[{index}][1]", bits=31)
        if left < 0 or right < 0 or left == right:
            raise ValueError("edge endpoints must be distinct nonnegative integers")
        checked_edges.append((left, right))

    from noether.combin import Graph
    from noether.topology import SimplicialComplex

    complex_value = SimplicialComplex(checked_facets)
    complex_value.verify()
    canonical = complex_value.canonical_form()
    canonical.verify()
    homology = complex_value.graded_homology()
    homology.verify()
    vertex_count = complex_value.count(0)
    maximum_vertex = max((vertex for facet in checked_facets for vertex in facet), default=-1)
    if vertex_count != maximum_vertex + 1:
        raise ValueError("swarm substrate vertex labels must be contiguous from zero")

    graph = Graph.from_unweighted(vertex_count, checked_edges, directed)
    canonical_graph = graph.canonical_label()
    if not canonical_graph.verify(graph):
        raise RuntimeError("Noether graph canonical-label certificate failed")
    graph_facts: dict[str, Any] = {
        "directed": graph.is_directed,
        "vertices": graph.order,
        "edges": graph.edge_count,
        "degree_sequence": graph.degree_sequence(),
        "canonical_json": json.loads(canonical_graph.graph.to_json()),
        "canonical_label_verified": True,
    }
    if not directed:
        graph_facts["laplacian_spectrum"] = graph.laplacian_spectrum()

    return {
        "dimension": complex_value.dimension,
        "counts": [complex_value.count(dimension) for dimension in range(complex_value.dimension + 1)],
        "euler_characteristic": complex_value.euler_characteristic(),
        "facets": complex_value.facets,
        "simplices": [
            complex_value.simplices(dimension)
            for dimension in range(complex_value.dimension + 1)
        ],
        "canonical_complex_json": json.loads(canonical.to_json()),
        "homology": [
            {
                "degree": group.degree,
                "free_rank": group.free_rank,
                "torsion_coefficients": [int(value) for value in group.torsion_coefficients],
            }
            for group in homology.groups
        ],
        "graph": graph_facts,
        "certificate_verified": True,
    }


_WRAPPER_DESCRIPTIONS = {
    "factor_integer": {
        "name": "factor_integer",
        "domain": "numthy",
        "kind": "reasoning-wrapper",
        "summary": "Factor one bounded integer and verify the factorization certificate.",
        "signatures": ["payload: {value: int} -> verified integer factorization"],
        "input_schemas": [
            {
                "type": "object",
                "properties": {"value": {"type": "integer"}},
                "required": ["value"],
                "additionalProperties": False,
            }
        ],
        "result_schemas": [
            {
                "type": "object",
                "required": ["value", "unit", "factors", "certificate_verified"],
            }
        ],
        "determinism": "deterministic",
        "supported_rings": ["Z"],
        "locations": ["local", "reasoning-sandbox"],
        "algorithms": ["numthy.factor"],
        "backends": ["flint"],
        "validity": ["unconditional"],
        "evidence": ["IntegerFactorization.verify"],
        "example": {"operation": "factor_integer", "payload": {"value": -360}},
        "errors": ["non-integer or oversized value"],
    },
    "binomial_invariants": {
        "name": "binomial_invariants",
        "domain": "combin",
        "kind": "reasoning-wrapper",
        "summary": (
            "Compute one bounded exact binomial coefficient from n and k, then factor it and verify the Noether factorization certificate."
        ),
        "signatures": ["payload: {n: int, k: int} -> exact coefficient and verified factorization"],
        "input_schemas": [
            {
                "type": "object",
                "properties": {
                    "n": {"type": "integer", "minimum": 0, "maximum": 1_000_000},
                    "k": {"type": "integer", "minimum": 0},
                },
                "required": ["n", "k"],
                "additionalProperties": False,
            }
        ],
        "result_schemas": [
            {
                "type": "object",
                "required": [
                    "n",
                    "k",
                    "value",
                    "unit",
                    "factors",
                    "certificate_verified",
                ],
            }
        ],
        "determinism": "deterministic",
        "supported_rings": ["Z"],
        "locations": ["local", "reasoning-sandbox"],
        "algorithms": ["math.comb", "numthy.factor"],
        "backends": ["python-integer", "flint"],
        "validity": ["unconditional"],
        "evidence": ["IntegerFactorization.verify"],
        "example": {"operation": "binomial_invariants", "payload": {"n": 3125, "k": 5}},
        "errors": [
            "non-integer or out-of-range n or k",
            "more than 128 multiplicative factors",
            "coefficient larger than 4,096 bits",
        ],
    },
    "multinomial_invariants": {
        "name": "multinomial_invariants",
        "domain": "combin",
        "kind": "reasoning-wrapper",
        "summary": (
            "Compute and factor the exact coefficient of a monomial in a powered sum. "
            "The required power rejects accidental inclusion of fixed outside factors."
        ),
        "signatures": ["payload: {power: int, parts: list[int], modulus?: int} -> exact coefficient, factorization, and residue"],
        "input_schemas": [
            {
                "type": "object",
                "properties": {
                    "power": {"type": "integer", "minimum": 0, "maximum": 1_000_000},
                    "parts": {
                        "type": "array",
                        "items": {"type": "integer", "minimum": 0},
                        "minItems": 1,
                        "maxItems": 128,
                    },
                    "modulus": {"type": "integer", "minimum": 2},
                },
                "required": ["power", "parts"],
                "additionalProperties": False,
            }
        ],
        "result_schemas": [
            {
                "type": "object",
                "required": [
                    "power",
                    "parts",
                    "value",
                    "unit",
                    "factors",
                    "certificate_verified",
                ],
            }
        ],
        "determinism": "deterministic",
        "supported_rings": ["Z"],
        "locations": ["local", "reasoning-sandbox"],
        "algorithms": ["iterated math.comb", "numthy.factor"],
        "backends": ["python-integer", "flint"],
        "validity": ["parts sum exactly to the displayed power"],
        "evidence": ["IntegerFactorization.verify", "exact modular reduction"],
        "example": {
            "operation": "multinomial_invariants",
            "payload": {"power": 4, "parts": [1, 2, 1], "modulus": 5},
        },
        "errors": [
            "parts do not sum to power",
            "negative or oversized parts",
            "coefficient larger than 4,096 bits",
        ],
    },
    "perfect_matching_polynomial": {
        "name": "perfect_matching_polynomial",
        "domain": "combin",
        "kind": "reasoning-wrapper",
        "summary": (
            "Compute the orbit-compressed polynomial obtained by summing one "
            "pairing monomial over every perfect matching of a labeled multiset."
        ),
        "signatures": [
            "payload: {multiplicities: [{label, count}], pairings?: [{left, right, value}], "
            "modulus?: int, max_terms?: int} -> verified perfect-matching polynomial and optional exact evaluation"
        ],
        "input_schemas": [
            {
                "type": "object",
                "properties": {
                    "multiplicities": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string"},
                                "count": {"type": "integer", "minimum": 1, "maximum": 12},
                            },
                            "required": ["label", "count"],
                            "additionalProperties": False,
                        },
                        "minItems": 1,
                        "maxItems": 12,
                    },
                    "pairings": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "left": {"type": "string"},
                                "right": {"type": "string"},
                                "value": {"type": "integer"},
                            },
                            "required": ["left", "right", "value"],
                            "additionalProperties": False,
                        },
                        "minItems": 1,
                        "maxItems": 78,
                    },
                    "modulus": {"type": "integer", "minimum": 2},
                    "max_terms": {"type": "integer", "minimum": 1, "maximum": 10_000},
                },
                "required": ["multiplicities"],
                "additionalProperties": False,
            }
        ],
        "result_schemas": [
            {
                "type": "object",
                "required": [
                    "multiplicities",
                    "degree",
                    "matching_size",
                    "pair_types",
                    "term_count",
                    "terms",
                    "total_matching_count",
                    "coefficient_gcd",
                    "certificate",
                    "certificate_verified",
                ],
            }
        ],
        "determinism": "deterministic",
        "supported_rings": ["Z"],
        "locations": ["local", "reasoning-sandbox"],
        "algorithms": [
            "distinguished-vertex perfect-matching recurrence",
            "closed multiset orbit-size formula",
            "numthy.factor",
        ],
        "backends": ["python-integer", "flint"],
        "validity": [
            "even total multiplicity between 2 and 12",
            "optional pairing assignment covers every active pair type exactly once",
        ],
        "evidence": [
            "independent multiplicity-balance checks",
            "closed coefficient-formula checks",
            "total labeled-matching count check",
            "IntegerFactorization.verify for the coefficient gcd",
            "exact substitution into every independently verified compressed term",
        ],
        "example": {
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
        },
        "errors": [
            "duplicate or invalid labels",
            "odd or out-of-range total multiplicity",
            "compressed polynomial exceeds max_terms",
            "missing, duplicate, unknown, or inactive pairing value",
        ],
    },
    "matrix_invariants": {
        "name": "matrix_invariants",
        "domain": "linalg",
        "kind": "reasoning-wrapper",
        "summary": "Compute exact rank and square-matrix determinant data; over Z also compute and verify Smith invariants.",
        "signatures": ["payload: {ring: 'Z'|'Q', rows: matrix} -> exact invariants"],
        "input_schemas": [
            {
                "type": "object",
                "properties": {
                    "ring": {"enum": ["Z", "Q"]},
                    "rows": {
                        "type": "array",
                        "items": {
                            "type": "array",
                            "items": {"type": ["integer", "string"]},
                        },
                    },
                },
                "required": ["ring", "rows"],
                "additionalProperties": False,
            }
        ],
        "result_schemas": [
            {
                "type": "object",
                "required": ["ring", "rows", "columns", "rank"],
            }
        ],
        "determinism": "deterministic",
        "supported_rings": ["Z", "Q"],
        "locations": ["local", "reasoning-sandbox"],
        "algorithms": ["Matrix.rank", "Matrix.det", "Matrix.smith_normal_form"],
        "backends": ["flint", "native"],
        "validity": ["unconditional"],
        "evidence": ["SmithVerification for Z"],
        "example": {
            "operation": "matrix_invariants",
            "payload": {"ring": "Z", "rows": [[2, 4], [6, 8]]},
        },
        "errors": ["ring other than Z or Q", "ragged or oversized matrix"],
    },
    "lattice_shortest_vectors": {
        "name": "lattice_shortest_vectors",
        "domain": "linalg",
        "kind": "reasoning-wrapper",
        "summary": (
            "Enumerate one representative of each shortest-vector pair in a "
            "positive-definite integral lattice, or all nonzero pairs through an "
            "optional norm bound, and verify Noether's enumeration certificate."
        ),
        "signatures": ["payload: {gram: integer matrix, bound?: positive int, max_pairs?: int} -> verified short-vector pairs"],
        "input_schemas": [
            {
                "type": "object",
                "properties": {
                    "gram": {
                        "type": "array",
                        "items": {"type": "array", "items": {"type": "integer"}},
                    },
                    "bound": {"type": "integer", "minimum": 1},
                    "max_pairs": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10_000,
                    },
                },
                "required": ["gram"],
                "additionalProperties": False,
            }
        ],
        "result_schemas": [
            {
                "type": "object",
                "required": [
                    "rank",
                    "bound",
                    "vectors",
                    "norms",
                    "pair_count",
                    "signed_count",
                    "max_norm",
                    "certificate_verified",
                ],
            }
        ],
        "determinism": "deterministic",
        "supported_rings": ["Z"],
        "locations": ["local", "reasoning-sandbox"],
        "algorithms": ["Lattice.shortest_vectors"],
        "backends": ["lattice-enum"],
        "validity": ["positive-definite integral Gram matrices"],
        "evidence": ["LatticeEnumeration.verify", "direct exact norm checks"],
        "example": {
            "operation": "lattice_shortest_vectors",
            "payload": {"gram": [[2, -1], [-1, 2]], "bound": 2},
        },
        "errors": [
            "nonsymmetric, non-positive-definite, or oversized Gram matrix",
            "nonpositive norm bound",
            "enumeration exceeds max_pairs",
        ],
    },
    "lattice_orthogonal_complements": {
        "name": "lattice_orthogonal_complements",
        "domain": "linalg",
        "kind": "reasoning-wrapper",
        "summary": (
            "Compute the full saturated integral orthogonal complement of each "
            "supplied vector in one positive-definite lattice, then certify its "
            "basis, induced Gram matrix, Smith invariants, and determinant identity."
        ),
        "signatures": [
            "payload: {gram: integer matrix, vectors: list[integer vector]} -> verified saturated orthogonal-complement invariants"
        ],
        "input_schemas": [
            {
                "type": "object",
                "properties": {
                    "gram": {
                        "type": "array",
                        "items": {"type": "array", "items": {"type": "integer"}},
                    },
                    "vectors": {
                        "type": "array",
                        "items": {"type": "array", "items": {"type": "integer"}},
                        "minItems": 1,
                        "maxItems": 128,
                    },
                },
                "required": ["gram", "vectors"],
                "additionalProperties": False,
            }
        ],
        "result_schemas": [
            {
                "type": "object",
                "required": [
                    "rank",
                    "ambient_absolute_determinant",
                    "vectors_checked",
                    "orthogonals",
                    "certificate_verified",
                ],
            }
        ],
        "determinism": "deterministic",
        "supported_rings": ["Z"],
        "locations": ["local", "reasoning-sandbox"],
        "algorithms": [
            "Matrix.smith_normal_form_with_transforms",
            "Matrix.smith_normal_form",
        ],
        "backends": ["flint"],
        "validity": ["positive-definite integral Gram matrices"],
        "evidence": [
            "verified Smith right-transform kernel basis",
            "direct zero pairing residuals",
            "verified Smith invariants of every induced Gram matrix",
            "exact orthogonal-complement determinant identity",
        ],
        "example": {
            "operation": "lattice_orthogonal_complements",
            "payload": {
                "gram": [[2, -1], [-1, 2]],
                "vectors": [[1, 0], [1, 1]],
            },
        },
        "errors": [
            "nonsymmetric, non-positive-definite, or oversized Gram matrix",
            "zero, dimension-mismatched, or oversized vector list",
        ],
    },
    "integer_lattice_membership": {
        "name": "integer_lattice_membership",
        "domain": "linalg",
        "kind": "reasoning-wrapper",
        "summary": (
            "Decide exact membership in an integer matrix's column lattice and "
            "return either a checked integral solution or a checked separating functional."
        ),
        "signatures": ["payload: {matrix: integer matrix, target: integer vector} -> certified column-lattice membership"],
        "input_schemas": [
            {
                "type": "object",
                "properties": {
                    "matrix": {
                        "type": "array",
                        "items": {"type": "array", "items": {"type": "integer"}},
                    },
                    "target": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["matrix", "target"],
                "additionalProperties": False,
            }
        ],
        "result_schemas": [
            {
                "type": "object",
                "required": [
                    "status",
                    "matrix_sha256",
                    "problem_sha256",
                    "rank",
                    "invariant_factors",
                    "certificate_verified",
                ],
            }
        ],
        "determinism": "deterministic",
        "supported_rings": ["Z"],
        "locations": ["local", "reasoning-sandbox"],
        "algorithms": ["Matrix.smith_normal_form_with_transforms"],
        "backends": ["flint"],
        "validity": ["unconditional"],
        "evidence": [
            "SmithNormalFormWithTransforms.verify",
            "direct solution residual or separating-functional check",
        ],
        "example": {
            "operation": "integer_lattice_membership",
            "payload": {"matrix": [[2, 0], [0, 6]], "target": [4, 3]},
        },
        "errors": ["ragged or oversized matrix", "target dimension mismatch"],
    },
    "integer_kernel_projection": {
        "name": "integer_kernel_projection",
        "domain": "linalg",
        "kind": "reasoning-wrapper",
        "summary": (
            "Compute a saturated integral kernel, project it to selected domain "
            "coordinates, and return a canonical column-HNF basis with exact full lifts."
        ),
        "signatures": [
            "payload: {matrix: integer matrix, selected_columns: list[int]} -> certified projected integral kernel"
        ],
        "input_schemas": [
            {
                "type": "object",
                "properties": {
                    "matrix": {
                        "type": "array",
                        "items": {"type": "array", "items": {"type": "integer"}},
                    },
                    "selected_columns": {
                        "type": "array",
                        "items": {"type": "integer", "minimum": 0},
                        "minItems": 1,
                        "maxItems": 128,
                    },
                    "include_kernel_basis": {"type": "boolean"},
                },
                "required": ["matrix", "selected_columns"],
                "additionalProperties": False,
            }
        ],
        "result_schemas": [
            {
                "type": "object",
                "required": [
                    "problem_sha256",
                    "rank",
                    "nullity",
                    "kernel_basis_sha256",
                    "projected_rank",
                    "projected_column_hnf_basis",
                    "projected_basis_full_lifts",
                    "certificate_verified",
                ],
            }
        ],
        "determinism": "deterministic",
        "supported_rings": ["Z"],
        "locations": ["local", "reasoning-sandbox"],
        "algorithms": [
            "Matrix.smith_normal_form_with_transforms",
            "Matrix.hermite_normal_form",
        ],
        "backends": ["flint"],
        "validity": ["unconditional"],
        "evidence": [
            "SmithNormalFormWithTransforms.verify",
            "direct kernel residual checks",
            "two integral change-of-generator witnesses for projected lattices",
            "direct projection checks for full HNF lifts",
        ],
        "example": {
            "operation": "integer_kernel_projection",
            "payload": {
                "matrix": [[1, 0, -2]],
                "selected_columns": [0],
            },
        },
        "errors": [
            "ragged or oversized matrix",
            "empty, repeated, or out-of-range selected columns",
        ],
    },
    "ideal_invariants": {
        "name": "ideal_invariants",
        "domain": "commalg",
        "kind": "reasoning-wrapper",
        "summary": (
            "Build a bounded commutative polynomial ideal from sparse terms and "
            "return verified Groebner, dimension, degree, and optional smoothness data."
        ),
        "signatures": ["payload: {field, ambient, variables, generators, check_smooth?} -> ideal invariants"],
        "input_schemas": [
            {
                "type": "object",
                "properties": {
                    "field": {
                        "oneOf": [
                            {"type": "object", "properties": {"kind": {"const": "Q"}}, "required": ["kind"]},
                            {
                                "type": "object",
                                "properties": {
                                    "kind": {"const": "Fp"},
                                    "prime": {"type": "integer", "minimum": 2},
                                },
                                "required": ["kind", "prime"],
                            },
                        ]
                    },
                    "ambient": {"enum": ["ideal", "affine", "projective"]},
                    "variables": {"type": "array", "items": {"type": "string"}},
                    "generators": {
                        "type": "array",
                        "description": (
                            "Each polynomial is an array of {coefficient, exponents} sparse terms; exponents follow variables order."
                        ),
                    },
                    "check_smooth": {"type": "boolean"},
                },
                "required": ["field", "ambient", "variables", "generators"],
                "additionalProperties": False,
            }
        ],
        "result_schemas": [
            {
                "type": "object",
                "required": ["groebner_basis", "krull_dimension", "degree", "certificate_verified"],
            }
        ],
        "determinism": "deterministic",
        "supported_rings": ["Q[x_1,...,x_n]", "F_p[x_1,...,x_n]"],
        "locations": ["local", "reasoning-sandbox"],
        "algorithms": ["Ideal.groebner_basis", "Ideal.verify", "Scheme.is_smooth"],
        "backends": ["native"],
        "validity": ["commutative polynomial ideals only"],
        "evidence": ["Ideal.verify", "Jacobian criterion when supported"],
        "example": {
            "operation": "ideal_invariants",
            "payload": {
                "field": {"kind": "Fp", "prime": 5},
                "ambient": "affine",
                "variables": ["w", "t"],
                "generators": [
                    [
                        {"coefficient": 1, "exponents": [5, 0]},
                        {"coefficient": -1, "exponents": [1, 0]},
                        {"coefficient": -1, "exponents": [0, 1]},
                    ]
                ],
                "check_smooth": True,
            },
        },
        "errors": ["non-prime coefficient characteristic", "malformed sparse polynomial", "unsupported smoothness check"],
    },
    "simplicial_homology": {
        "name": "simplicial_homology",
        "domain": "topology",
        "kind": "reasoning-wrapper",
        "summary": "Compute and verify integral homology of a bounded finite simplicial complex.",
        "signatures": ["payload: {facets: list[list[int]]} -> verified graded homology"],
        "input_schemas": [
            {
                "type": "object",
                "properties": {
                    "facets": {
                        "type": "array",
                        "items": {"type": "array", "items": {"type": "integer", "minimum": 0}},
                    }
                },
                "required": ["facets"],
                "additionalProperties": False,
            }
        ],
        "result_schemas": [{"type": "object", "required": ["groups", "certificate_verified"]}],
        "determinism": "deterministic",
        "supported_rings": ["Z"],
        "locations": ["local", "reasoning-sandbox"],
        "algorithms": ["SimplicialComplex.graded_homology"],
        "backends": ["native"],
        "validity": ["finite simplicial complexes"],
        "evidence": ["GradedHomology.verify"],
        "example": {
            "operation": "simplicial_homology",
            "payload": {"facets": [[0, 1], [1, 2], [0, 2]]},
        },
        "errors": ["empty, repeated, negative, or oversized facets"],
    },
    "swarm_topology": {
        "name": "swarm_topology",
        "domain": "topology",
        "kind": "reasoning-wrapper",
        "summary": "Canonicalize and verify a bounded graph and its finite simplicial interaction complex.",
        "signatures": ["payload: {facets, edges, directed} -> canonical graph, complex, homology, and spectral facts"],
        "input_schemas": [
            {
                "type": "object",
                "properties": {
                    "facets": {"type": "array"},
                    "edges": {"type": "array"},
                    "directed": {"type": "boolean"},
                },
                "required": ["facets", "edges", "directed"],
                "additionalProperties": False,
            }
        ],
        "result_schemas": [
            {"type": "object", "required": ["canonical_complex_json", "homology", "graph", "certificate_verified"]}
        ],
        "determinism": "deterministic",
        "supported_rings": ["Z"],
        "locations": ["local", "reasoning-sandbox"],
        "algorithms": ["SimplicialComplex.canonical_form", "SimplicialComplex.graded_homology", "Graph.canonical_label"],
        "backends": ["native"],
        "validity": ["finite simplicial complexes and finite graphs"],
        "evidence": ["SimplicialComplex.verify", "GradedHomology.verify", "CanonicalLabel.verify"],
        "example": {
            "operation": "swarm_topology",
            "payload": {"facets": [[0, 1], [1, 2], [0, 2]], "edges": [[0, 1], [1, 2], [0, 2]], "directed": False},
        },
        "errors": ["noncontiguous labels", "malformed graph", "invalid simplicial complex"],
    },
}


def _describe(payload: dict[str, Any]) -> dict[str, Any]:
    _keys(payload, allowed={"operation"}, required={"operation"})
    operation = payload["operation"]
    if not isinstance(operation, str) or not 1 <= len(operation) <= 200:
        raise ValueError("operation must be a nonempty string of at most 200 characters")
    if operation in _WRAPPER_DESCRIPTIONS:
        return _WRAPPER_DESCRIPTIONS[operation]
    import noether

    description = noether.describe(operation)
    return dataclasses.asdict(description)


def _capabilities(payload: dict[str, Any]) -> dict[str, Any]:
    _keys(payload, allowed=set(), required=set())
    import noether

    capabilities = noether.capabilities()
    return {
        "version": noether.__version__,
        "domains": list(capabilities.domains),
        "operations": len(capabilities.operations),
    }


_OPERATIONS = {
    "capabilities": _capabilities,
    "describe": _describe,
    "factor_integer": _factor,
    "binomial_invariants": _binomial,
    "multinomial_invariants": _multinomial,
    "perfect_matching_polynomial": _perfect_matching_polynomial,
    "matrix_invariants": _matrix,
    "lattice_shortest_vectors": _lattice_shortest_vectors,
    "lattice_orthogonal_complements": _lattice_orthogonal_complements,
    "integer_lattice_membership": _integer_lattice_membership,
    "integer_kernel_projection": _integer_kernel_projection,
    "ideal_invariants": _ideal,
    "simplicial_homology": _simplicial_homology,
    "swarm_topology": _swarm_topology,
}


def execute(request: Any) -> dict[str, Any]:
    request = _object(request, "request")
    _keys(request, allowed={"operation", "payload"}, required={"operation", "payload"})
    operation = request["operation"]
    if operation not in _OPERATIONS:
        raise ValueError(f"unsupported Noether operation: {operation}")
    payload = _object(request["payload"], "payload")
    return _OPERATIONS[operation](payload)


def main() -> int:
    _limit_process()
    try:
        raw = sys.stdin.buffer.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("request exceeds the 2,000,000-byte limit")
        request = json.loads(raw)
        result = execute(request)
        response = {"ok": True, "result": result}
    except Exception as error:
        response = {
            "ok": False,
            "error": {"type": type(error).__name__, "detail": str(error)},
        }
    sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")))
    sys.stdout.write("\n")
    return 0 if response["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
