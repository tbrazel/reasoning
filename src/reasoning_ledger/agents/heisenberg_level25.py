"""Exact character and cyclic-lift census for the level-25 Heisenberg screen."""

from __future__ import annotations

import hashlib
import math
import os
import shutil
import subprocess
import tempfile
from itertools import combinations, product
from pathlib import Path
from typing import Any

from .. import sandbox
from ..canonical import canonical_json
from .scientific import ScientificToolError

MODULUS = 25
PRIME = 5
FINITE_FIELD_PRIME = 101
ROOT_OF_UNITY = 5
_SINGULAR_PROTOCOL_VERSION = 1
_SINGULAR_TIMEOUT_SECONDS = 60
_PIVOTED_SINGULAR_TIMEOUT_SECONDS = 300
_SINGULAR_MAX_OUTPUT_BYTES = 128 * 1024
_COORDINATE_RANK_PROTOCOL_VERSION = 1
_COORDINATE_RANK_TIMEOUT_SECONDS = 30
_COORDINATE_RANK_MAX_OUTPUT_BYTES = 16 * 1024
_BLOCK_SHAPE_COVER_PROTOCOL_VERSION = 1
_BLOCK_SHAPE_COVER_TIMEOUT_SECONDS = 30
_BLOCK_SHAPE_COVER_MAX_OUTPUT_BYTES = 16 * 1024
_PIVOTED_TWO_BLOCK_RANK_PROTOCOL_VERSION = 1
_PIVOTED_TWO_BLOCK_RANK_TIMEOUT_SECONDS = 10
_PIVOTED_TWO_BLOCK_RANK_MAX_OUTPUT_BYTES = 16 * 1024
_FIVE_PIVOT_SUPPORT_JACOBIAN_PROTOCOL_VERSION = 1
_FIVE_PIVOT_SUPPORT_JACOBIAN_TIMEOUT_SECONDS = 60
_FIVE_PIVOT_SUPPORT_JACOBIAN_MAX_OUTPUT_BYTES = 32 * 1024

Polynomial = dict[tuple[int, ...], int]

# The first coordinate is deliberately every residue 1 through 22.  Characters
# with first coordinate divisible by five have second coordinate divisible by
# five as well; the remaining four residue classes use a constant second
# coordinate modulo five.  This makes all five projections above H_0
# injective while preserving the stated kernel and lift-coverage census.
LEVEL25_HEISENBERG_CHARACTERS: tuple[tuple[int, int], ...] = (
    (5, 5),
    (10, 5),
    (15, 5),
    (20, 5),
    (1, 0),
    (6, 20),
    (11, 15),
    (16, 10),
    (21, 5),
    (2, 1),
    (7, 1),
    (12, 1),
    (17, 1),
    (22, 1),
    (3, 1),
    (8, 1),
    (13, 1),
    (18, 1),
    (4, 1),
    (9, 1),
    (14, 1),
    (19, 1),
)

# This replacement keeps all five H_0 lifts uncovered with the same twenty-two
# distinct nonzero restrictions.  Its four scaled characters cover H_1 through
# H_4, while five selected lifts of the second coordinate cover H_infinity.
# The order-five distributions on H_0 through H_4 are all balanced at the
# projective tangent capacities (4,5,5,5,5).
LEVEL25_HEISENBERG_BALANCED_CHARACTERS: tuple[tuple[int, int], ...] = (
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

_LEVEL25_HEISENBERG_NONADJACENT_PIVOTS: tuple[
    tuple[int, int, int, int], ...
] = (
    (2, 1, 4, 20),
    (2, 2, 13, 10),
    (2, 4, 6, 15),
    (4, 1, 4, 20),
)


def _evaluate(character: tuple[int, int], element: tuple[int, int]) -> int:
    return sum(
        coefficient * coordinate
        for coefficient, coordinate in zip(character, element, strict=True)
    ) % MODULUS


def _order_five_subgroups() -> list[dict[str, Any]]:
    rows = []
    for slope in range(PRIME):
        rows.append(
            {
                "label": f"H_{slope}",
                "generator": (PRIME, PRIME * slope),
                "cyclic_lifts": tuple(
                    (1, slope + PRIME * lift) for lift in range(PRIME)
                ),
            }
        )
    rows.append(
        {
            "label": "H_infinity",
            "generator": (0, PRIME),
            "cyclic_lifts": tuple(
                (PRIME * lift, 1) for lift in range(PRIME)
            ),
        }
    )
    return rows


def _finite_field_power(exponent: int) -> int:
    return pow(ROOT_OF_UNITY, exponent % MODULUS, FINITE_FIELD_PRIME)


def _add_term(
    polynomial: Polynomial,
    exponents: tuple[int, ...],
    coefficient: int,
) -> None:
    value = (polynomial.get(exponents, 0) + coefficient) % FINITE_FIELD_PRIME
    if value:
        polynomial[exponents] = value
    else:
        polynomial.pop(exponents, None)


def _coordinate_restriction(
    subgroup_index: int,
    eigenspace_index: int,
) -> tuple[tuple[int, int] | None, ...]:
    """Return ``(variable, coefficient)`` for each restricted coordinate."""

    if subgroup_index == PRIME:
        residue = eigenspace_index
        return tuple(
            ((index - residue) // PRIME, 1)
            if index % PRIME == residue
            else None
            for index in range(MODULUS)
        )
    slope = subgroup_index
    eigenvalue = _finite_field_power(PRIME * eigenspace_index)
    coordinates: list[tuple[int, int]] = []
    for index in range(MODULUS):
        residue = index % PRIME
        orbit_index = index // PRIME
        ratio = (
            eigenvalue * _finite_field_power(PRIME * slope * residue)
        ) % FINITE_FIELD_PRIME
        coordinates.append(
            (residue, pow(ratio, orbit_index, FINITE_FIELD_PRIME))
        )
    return tuple(coordinates)


def _restricted_form(
    character: tuple[int, int],
    coordinate_map: tuple[tuple[int, int] | None, ...],
) -> Polynomial:
    a, b = character
    polynomial: Polynomial = {}
    for index in range(MODULUS):
        first = coordinate_map[index]
        second = coordinate_map[(index - b) % MODULUS]
        if first is None or second is None:
            continue
        first_variable, first_coefficient = first
        second_variable, second_coefficient = second
        exponents = [0] * PRIME
        exponents[first_variable] += MODULUS - 1
        exponents[second_variable] += 1
        coefficient = (
            _finite_field_power(-a * index)
            * pow(first_coefficient, MODULUS - 1, FINITE_FIELD_PRIME)
            * second_coefficient
        ) % FINITE_FIELD_PRIME
        _add_term(polynomial, tuple(exponents), coefficient)
    return polynomial


def _pivoted_restricted_form(
    character: tuple[int, int],
    coordinate_map: tuple[tuple[int, int] | None, ...],
) -> Polynomial:
    """Add the coefficient-one ``x_0^20*x_1^5`` orbit to ``(5,20)``."""

    polynomial = _restricted_form(character, coordinate_map)
    if character != (5, 20):
        return polynomial
    for index in range(MODULUS):
        first = coordinate_map[index]
        second = coordinate_map[(index + 1) % MODULUS]
        if first is None or second is None:
            continue
        first_variable, first_coefficient = first
        second_variable, second_coefficient = second
        exponents = [0] * PRIME
        exponents[first_variable] += 20
        exponents[second_variable] += 5
        coefficient = (
            _finite_field_power(-5 * index)
            * pow(first_coefficient, 20, FINITE_FIELD_PRIME)
            * pow(second_coefficient, 5, FINITE_FIELD_PRIME)
        ) % FINITE_FIELD_PRIME
        _add_term(polynomial, tuple(exponents), coefficient)
    return polynomial


def _two_pivoted_restricted_form(
    character: tuple[int, int],
    coordinate_map: tuple[tuple[int, int] | None, ...],
) -> Polynomial:
    """Add both coefficient-one pivots to the balanced sparse tuple."""

    polynomial = _pivoted_restricted_form(character, coordinate_map)
    if character != (2, 15):
        return polynomial
    for index in range(MODULUS):
        first = coordinate_map[index]
        second = coordinate_map[(index + 5) % MODULUS]
        third = coordinate_map[(index + 2) % MODULUS]
        if first is None or second is None or third is None:
            continue
        first_variable, first_coefficient = first
        second_variable, second_coefficient = second
        third_variable, third_coefficient = third
        exponents = [0] * PRIME
        exponents[first_variable] += 2
        exponents[second_variable] += 13
        exponents[third_variable] += 10
        coefficient = (
            _finite_field_power(-2 * index)
            * pow(first_coefficient, 2, FINITE_FIELD_PRIME)
            * pow(second_coefficient, 13, FINITE_FIELD_PRIME)
            * pow(third_coefficient, 10, FINITE_FIELD_PRIME)
        ) % FINITE_FIELD_PRIME
        _add_term(polynomial, tuple(exponents), coefficient)
    return polynomial


def _five_pivoted_restricted_form(
    character: tuple[int, int],
    coordinate_map: tuple[tuple[int, int] | None, ...],
) -> Polynomial:
    """Add the adjacent pivot and all four certified nonadjacent pivots."""

    polynomial = _pivoted_restricted_form(character, coordinate_map)
    for first_character, alpha, beta, gamma in (
        _LEVEL25_HEISENBERG_NONADJACENT_PIVOTS
    ):
        if character != (first_character, 15):
            continue
        for index in range(MODULUS):
            entries = (
                coordinate_map[index],
                coordinate_map[(index + 5) % MODULUS],
                coordinate_map[(index + 2) % MODULUS],
            )
            if any(entry is None for entry in entries):
                continue
            exponents = [0] * PRIME
            coefficient = _finite_field_power(-first_character * index)
            for entry, power in zip(
                entries,
                (alpha, beta, gamma),
                strict=True,
            ):
                assert entry is not None
                variable, restricted_coefficient = entry
                exponents[variable] += power
                coefficient = (
                    coefficient
                    * pow(
                        restricted_coefficient,
                        power,
                        FINITE_FIELD_PRIME,
                    )
                    % FINITE_FIELD_PRIME
                )
            _add_term(polynomial, tuple(exponents), coefficient)
    return polynomial


def _sparse_polynomial(polynomial: Polynomial) -> list[dict[str, Any]]:
    return [
        {"coefficient": coefficient, "exponents": list(exponents)}
        for exponents, coefficient in sorted(polynomial.items())
    ]


def _singular_polynomial(polynomial: Polynomial) -> str:
    terms = []
    variables = tuple(f"y{index}" for index in range(PRIME))
    for exponents, coefficient in sorted(polynomial.items()):
        monomial = "*".join(
            f"{variable}^{exponent}" if exponent != 1 else variable
            for variable, exponent in zip(variables, exponents, strict=True)
            if exponent
        )
        terms.append(f"{coefficient}*{monomial}")
    return "+".join(terms)


def _restricted_systems(
    characters: tuple[tuple[int, int], ...] = LEVEL25_HEISENBERG_CHARACTERS,
    *,
    pivoted: bool = False,
    two_pivoted: bool = False,
    five_pivoted: bool = False,
) -> list[dict[str, Any]]:
    if two_pivoted and not pivoted:
        raise ValueError("the two-pivot tuple includes the first pivot")
    if five_pivoted and not pivoted:
        raise ValueError("the five-pivot tuple includes the first pivot")
    if two_pivoted and five_pivoted:
        raise ValueError("select either the two-pivot or five-pivot tuple")
    restricted_form = (
        _five_pivoted_restricted_form
        if five_pivoted
        else _two_pivoted_restricted_form
        if two_pivoted
        else _pivoted_restricted_form
        if pivoted
        else _restricted_form
    )
    systems = []
    subgroups = _order_five_subgroups()
    for subgroup_index, subgroup in enumerate(subgroups):
        for eigenspace_index in range(PRIME):
            coordinate_map = _coordinate_restriction(
                subgroup_index,
                eigenspace_index,
            )
            forms = [
                restricted_form(character, coordinate_map)
                for character in characters
            ]
            effective_indexes = [
                index for index, polynomial in enumerate(forms) if polynomial
            ]
            systems.append(
                {
                    "subgroup_index": subgroup_index,
                    "subgroup_label": subgroup["label"],
                    "eigenspace_index": eigenspace_index,
                    "eigenvalue": (
                        _finite_field_power(PRIME * eigenspace_index)
                        if subgroup_index < PRIME
                        else _finite_field_power(-PRIME * eigenspace_index)
                    ),
                    "effective_character_indexes": effective_indexes,
                    "forms": forms,
                }
            )
    return systems


def _restricted_system_program(
    characters: tuple[tuple[int, int], ...],
    frame: str,
    *,
    pivoted: bool = False,
    two_pivoted: bool = False,
    five_pivoted: bool = False,
) -> str:

    lines = [
        "option(redSB);",
        "ring r=101,(y0,y1,y2,y3,y4),dp;",
        f'print("{frame}");',
        'print("singular_version="+string(system("version")));',
    ]
    for system in _restricted_systems(
        characters,
        pivoted=pivoted,
        two_pivoted=two_pivoted,
        five_pivoted=five_pivoted,
    ):
        if system["eigenspace_index"] != 0:
            continue
        label = f"{system['subgroup_index']}_{system['eigenspace_index']}"
        effective_forms = [
            system["forms"][index]
            for index in system["effective_character_indexes"]
        ]
        equations = ",".join(
            _singular_polynomial(polynomial) for polynomial in effective_forms
        )
        lines.extend(
            (
                f"ideal i_{label}={equations};",
                f"ideal g_{label}=std(i_{label});",
                f'print("system_{label}_dim="+string(dim(g_{label})));',
                f'print("system_{label}_basis_size="+string(size(g_{label})));',
            )
        )
        if len(effective_forms) == 4:
            lines.extend(
                (
                    f'print("system_{label}_projective_degree="+string(mult(g_{label})));',
                    f"ideal s_{label}=std(i_{label}+minor(jacob(i_{label}),4));",
                    f'print("system_{label}_singular_dim="+string(dim(s_{label})));',
                    f'print("system_{label}_singular_basis_size="+string(size(s_{label})));',
                )
            )
    lines.extend(('print("REASONING_END");', "$;"))
    return "\n".join(lines) + "\n"


def level25_heisenberg_restricted_system_program() -> str:
    """Return the fixed Singular protocol for six eigenspace representatives."""

    frame = (
        "REASONING_LEVEL25_HEISENBERG_RESTRICTED_SYSTEM_V"
        f"{_SINGULAR_PROTOCOL_VERSION}"
    )
    return _restricted_system_program(LEVEL25_HEISENBERG_CHARACTERS, frame)


def level25_heisenberg_balanced_restricted_system_program() -> str:
    """Return the balanced-candidate Singular restricted-system protocol."""

    frame = (
        "REASONING_LEVEL25_HEISENBERG_BALANCED_RESTRICTED_SYSTEM_V"
        f"{_SINGULAR_PROTOCOL_VERSION}"
    )
    return _restricted_system_program(
        LEVEL25_HEISENBERG_BALANCED_CHARACTERS,
        frame,
    )


def level25_heisenberg_balanced_pivoted_restricted_system_program() -> str:
    """Return the coefficient-one pivoted Singular restriction protocol."""

    frame = (
        "REASONING_LEVEL25_HEISENBERG_BALANCED_PIVOTED_RESTRICTED_SYSTEM_V"
        f"{_SINGULAR_PROTOCOL_VERSION}"
    )
    return _restricted_system_program(
        LEVEL25_HEISENBERG_BALANCED_CHARACTERS,
        frame,
        pivoted=True,
    )


def level25_heisenberg_balanced_two_pivot_restricted_system_program() -> str:
    """Return the coefficient-one two-pivot Singular restriction protocol."""

    frame = (
        "REASONING_LEVEL25_HEISENBERG_BALANCED_TWO_PIVOT_RESTRICTED_SYSTEM_V"
        f"{_SINGULAR_PROTOCOL_VERSION}"
    )
    return _restricted_system_program(
        LEVEL25_HEISENBERG_BALANCED_CHARACTERS,
        frame,
        pivoted=True,
        two_pivoted=True,
    )


def level25_heisenberg_balanced_five_pivot_restricted_system_program() -> str:
    """Return the coefficient-one five-pivot Singular restriction protocol."""

    frame = (
        "REASONING_LEVEL25_HEISENBERG_BALANCED_FIVE_PIVOT_RESTRICTED_SYSTEM_V"
        f"{_SINGULAR_PROTOCOL_VERSION}"
    )
    return _restricted_system_program(
        LEVEL25_HEISENBERG_BALANCED_CHARACTERS,
        frame,
        pivoted=True,
        five_pivoted=True,
    )


def _parse_restricted_system_transcript(
    transcript: str,
    *,
    frame: str,
    characters: tuple[tuple[int, int], ...],
    pivoted: bool = False,
    two_pivoted: bool = False,
    five_pivoted: bool = False,
) -> dict[str, Any]:
    lines = transcript.splitlines()
    if not lines or lines[0] != frame or lines[-1] != "REASONING_END":
        raise ScientificToolError(
            "level-25 Singular restricted-system profile has invalid framing"
        )
    fields: dict[str, str] = {}
    for line in lines[1:-1]:
        if "=" not in line:
            raise ScientificToolError(
                "level-25 Singular restricted-system profile has an invalid field"
            )
        name, value = line.split("=", 1)
        if name in fields:
            raise ScientificToolError(
                "level-25 Singular restricted-system profile has duplicate fields"
            )
        fields[name] = value
    expected = {"singular_version"}
    representative_systems = {
        system["subgroup_index"]: system
        for system in _restricted_systems(
            characters,
            pivoted=pivoted,
            two_pivoted=two_pivoted,
            five_pivoted=five_pivoted,
        )
        if system["eigenspace_index"] == 0
    }
    for subgroup_index, system in representative_systems.items():
        label = f"system_{subgroup_index}_0"
        expected.update({f"{label}_dim", f"{label}_basis_size"})
        if len(system["effective_character_indexes"]) == 4:
            expected.update(
                {
                    f"{label}_singular_dim",
                    f"{label}_singular_basis_size",
                    f"{label}_projective_degree",
                }
            )
    if set(fields) != expected:
        raise ScientificToolError(
            "level-25 Singular restricted-system profile is incomplete"
        )
    try:
        version = int(fields.pop("singular_version"))
        values = {name: int(value) for name, value in fields.items()}
    except ValueError as error:
        raise ScientificToolError(
            "level-25 Singular restricted-system profile has a nonintegral field"
        ) from error
    if version < 1 or any(
        value < 0 for name, value in values.items() if name.endswith("basis_size")
    ):
        raise ScientificToolError(
            "level-25 Singular restricted-system profile has an invalid integer field"
        )
    return {"singular_version_code": version, "fields": values}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _run_restricted_system_singular(
    characters: tuple[tuple[int, int], ...] = LEVEL25_HEISENBERG_CHARACTERS,
    *,
    pivoted: bool = False,
    two_pivoted: bool = False,
    five_pivoted: bool = False,
) -> dict[str, Any]:
    executable_value = shutil.which("Singular")
    if executable_value is None:
        raise ScientificToolError("Singular is unavailable on the Reasoning service PATH")
    executable = Path(executable_value).resolve()
    balanced = characters == LEVEL25_HEISENBERG_BALANCED_CHARACTERS
    if (pivoted or two_pivoted or five_pivoted) and not balanced:
        raise ScientificToolError(
            "the coefficient-one pivots are defined only for balanced characters"
        )
    if two_pivoted and not pivoted:
        raise ScientificToolError("the two-pivot tuple includes the first pivot")
    if five_pivoted and not pivoted:
        raise ScientificToolError("the five-pivot tuple includes the first pivot")
    if two_pivoted and five_pivoted:
        raise ScientificToolError(
            "select either the two-pivot or five-pivot tuple"
        )
    frame = (
        "REASONING_LEVEL25_HEISENBERG_BALANCED_FIVE_PIVOT_RESTRICTED_SYSTEM_V"
        if five_pivoted
        else "REASONING_LEVEL25_HEISENBERG_BALANCED_TWO_PIVOT_RESTRICTED_SYSTEM_V"
        if two_pivoted
        else "REASONING_LEVEL25_HEISENBERG_BALANCED_PIVOTED_RESTRICTED_SYSTEM_V"
        if pivoted
        else "REASONING_LEVEL25_HEISENBERG_BALANCED_RESTRICTED_SYSTEM_V"
        if balanced
        else "REASONING_LEVEL25_HEISENBERG_RESTRICTED_SYSTEM_V"
    ) + str(_SINGULAR_PROTOCOL_VERSION)
    program = _restricted_system_program(
        characters,
        frame,
        pivoted=pivoted,
        two_pivoted=two_pivoted,
        five_pivoted=five_pivoted,
    )
    timeout_seconds = (
        _PIVOTED_SINGULAR_TIMEOUT_SECONDS
        if pivoted
        else _SINGULAR_TIMEOUT_SECONDS
    )
    environment = {
        "HOME": "",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
    }
    try:
        with tempfile.TemporaryDirectory(
            prefix="reasoning-level25-heisenberg-singular-"
        ) as temporary:
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
                timeout=timeout_seconds,
                env=environment,
                check=False,
            )
    except subprocess.TimeoutExpired as error:
        raise ScientificToolError(
            "level-25 Singular restricted-system profile exceeded the "
            f"{timeout_seconds}-second wall timeout"
        ) from error
    except OSError as error:
        raise ScientificToolError(
            f"level-25 Singular restricted-system profile could not start: {error}"
        ) from error
    stdout = completed.stdout
    stderr = completed.stderr
    if (
        len(stdout.encode()) > _SINGULAR_MAX_OUTPUT_BYTES
        or len(stderr.encode()) > _SINGULAR_MAX_OUTPUT_BYTES
    ):
        raise ScientificToolError(
            "level-25 Singular restricted-system output exceeds the safe bound"
        )
    if completed.returncode or stderr.strip():
        detail = (stderr.strip() or stdout.strip() or "unknown Singular failure")[:2000]
        raise ScientificToolError(
            f"level-25 Singular restricted-system profile failed: {detail}"
        )
    parsed = _parse_restricted_system_transcript(
        stdout,
        frame=frame,
        characters=characters,
        pivoted=pivoted,
        two_pivoted=two_pivoted,
        five_pivoted=five_pivoted,
    )
    return {
        "protocol_version": _SINGULAR_PROTOCOL_VERSION,
        "program_sha256": hashlib.sha256(program.encode()).hexdigest(),
        "transcript_sha256": hashlib.sha256(stdout.encode()).hexdigest(),
        "transcript": stdout.strip(),
        "executable": str(executable),
        "executable_sha256": _sha256_file(executable),
        **parsed,
    }


def _modular_rank(matrix: list[list[int]]) -> int:
    rows = [[value % FINITE_FIELD_PRIME for value in row] for row in matrix]
    rank = 0
    for column in range(len(rows[0])):
        pivot = next(
            (
                row
                for row in range(rank, len(rows))
                if rows[row][column]
            ),
            None,
        )
        if pivot is None:
            continue
        rows[rank], rows[pivot] = rows[pivot], rows[rank]
        inverse = pow(rows[rank][column], -1, FINITE_FIELD_PRIME)
        rows[rank] = [
            value * inverse % FINITE_FIELD_PRIME for value in rows[rank]
        ]
        for row in range(len(rows)):
            if row == rank or not rows[row][column]:
                continue
            factor = rows[row][column]
            rows[row] = [
                (value - factor * pivot_value) % FINITE_FIELD_PRIME
                for value, pivot_value in zip(
                    rows[row],
                    rows[rank],
                    strict=True,
                )
            ]
        rank += 1
        if rank == len(rows):
            break
    return rank


def _balanced_coordinate_support_screen() -> dict[str, Any]:
    zero_shift_characters = [
        character
        for character in LEVEL25_HEISENBERG_BALANCED_CHARACTERS
        if character[1] == 0
    ]
    rows = []
    rank_histogram: dict[int, int] = {}
    for orbit_choices in product(range(PRIME), repeat=PRIME):
        support = [
            residue + PRIME * orbit_choices[residue]
            for residue in range(PRIME)
        ]
        coefficient_matrix = [
            [_finite_field_power(-character[0] * index) for index in support]
            for character in zero_shift_characters
        ]
        rank = _modular_rank(coefficient_matrix)
        rank_histogram[rank] = rank_histogram.get(rank, 0) + 1
        rows.append({"support": support, "pure_power_restriction_rank": rank})
    return {
        "support_definition": "one coordinate index in each residue class modulo five",
        "zero_shift_character_count": len(zero_shift_characters),
        "zero_shift_first_coordinates": [
            character[0] for character in zero_shift_characters
        ],
        "support_count": len(rows),
        "rank_histogram": {
            str(rank): count for rank, count in sorted(rank_histogram.items())
        },
        "rank_deficient_support_count": sum(
            row["pure_power_restriction_rank"] < PRIME for row in rows
        ),
        "support_rank_census_sha256": hashlib.sha256(
            canonical_json(rows).encode()
        ).hexdigest(),
    }


def _balanced_coordinate_rank_program() -> str:
    characters_by_shift = {
        shift: [
            first
            for first, second in LEVEL25_HEISENBERG_BALANCED_CHARACTERS
            if second == shift
        ]
        for shift in (0, 20, 15, 5)
    }
    expected_sizes = {0: 14, 20: 5, 15: 2, 5: 1}
    if {
        shift: len(characters)
        for shift, characters in characters_by_shift.items()
    } != expected_sizes:
        raise ScientificToolError(
            "balanced coordinate-rank program has an unexpected shift census"
        )

    def values(shift: int) -> str:
        return ", ".join(str(value) for value in characters_by_shift[shift])

    return (
        r'''#include <array>
#include <cstddef>
#include <cstdint>
#include <iostream>

constexpr int P = 101;
constexpr int ZETA = 5;

int mod_pow(int base, int exponent) {
    int result = 1;
    while (exponent > 0) {
        if (exponent & 1) result = result * base % P;
        base = base * base % P;
        exponent >>= 1;
    }
    return result;
}

int coefficient(int character, int index) {
    int exponent = (-character * index) % 25;
    if (exponent < 0) exponent += 25;
    return mod_pow(ZETA, exponent);
}

template <std::size_t Size>
struct Basis {
    std::array<std::array<std::uint8_t, Size>, Size> rows{};
    std::array<bool, Size> used{};
    int rank = 0;

    void add(std::array<std::uint8_t, Size> vector) {
        for (std::size_t pivot = 0; pivot < Size; ++pivot) {
            if (vector[pivot] == 0) continue;
            if (used[pivot]) {
                int factor = vector[pivot];
                for (std::size_t column = pivot; column < Size; ++column) {
                    int value = vector[column] - factor * rows[pivot][column];
                    value %= P;
                    if (value < 0) value += P;
                    vector[column] = static_cast<std::uint8_t>(value);
                }
                continue;
            }
            int inverse = mod_pow(vector[pivot], P - 2);
            for (std::size_t column = pivot; column < Size; ++column) {
                vector[column] = static_cast<std::uint8_t>(
                    vector[column] * inverse % P
                );
            }
            rows[pivot] = vector;
            used[pivot] = true;
            ++rank;
            return;
        }
    }
};

constexpr std::array<int, 14> A0{@A0@};
constexpr std::array<int, 5> A20{@A20@};
constexpr std::array<int, 2> A15{@A15@};
constexpr std::array<int, 1> A5{@A5@};

template <std::size_t Size>
std::array<std::uint8_t, Size> column(
    const std::array<int, Size>& characters,
    int index
) {
    std::array<std::uint8_t, Size> result{};
    for (std::size_t row = 0; row < Size; ++row) {
        result[row] = coefficient(characters[row], index);
    }
    return result;
}

std::uint64_t visited_nodes = 0;
std::uint64_t checked_leaves = 0;
std::uint64_t pruned_supports = 0;
std::uint64_t violations = 0;

void search(
    int residue,
    int support_size,
    Basis<14> b0,
    Basis<5> b20,
    Basis<2> b15,
    Basis<1> b5
) {
    ++visited_nodes;
    int current_rank = b0.rank + b20.rank + b15.rank + b5.rank;
    int remaining_coordinates = 5 * (5 - residue);
    if (support_size + remaining_coordinates - current_rank <= 3) {
        pruned_supports += std::uint64_t{1} << remaining_coordinates;
        return;
    }
    if (residue == 5) {
        ++checked_leaves;
        if (support_size > 0 && support_size - current_rank > 3) ++violations;
        return;
    }
    for (int mask = 0; mask < 32; ++mask) {
        auto next0 = b0;
        auto next20 = b20;
        auto next15 = b15;
        auto next5 = b5;
        for (int quotient = 0; quotient < 5; ++quotient) {
            if (((mask >> quotient) & 1) == 0) continue;
            int index = residue + 5 * quotient;
            next0.add(column(A0, index));
            if ((mask >> ((quotient + 1) % 5)) & 1) {
                next20.add(column(A20, index));
            }
            if ((mask >> ((quotient + 2) % 5)) & 1) {
                next15.add(column(A15, index));
            }
            if ((mask >> ((quotient + 4) % 5)) & 1) {
                next5.add(column(A5, index));
            }
        }
        search(
            residue + 1,
            support_size + __builtin_popcount(static_cast<unsigned>(mask)),
            next0,
            next20,
            next15,
            next5
        );
    }
}

int main() {
    search(0, 0, {}, {}, {}, {});
    Basis<14> b0;
    Basis<5> b20;
    Basis<2> b15;
    Basis<1> b5;
    for (int index = 0; index < 25; ++index) {
        b0.add(column(A0, index));
        b20.add(column(A20, index));
        b15.add(column(A15, index));
        b5.add(column(A5, index));
    }
    std::cout << "REASONING_LEVEL25_HEISENBERG_BALANCED_COORDINATE_RANK_V1\n";
    std::cout << "visited_nodes=" << visited_nodes << "\n";
    std::cout << "checked_leaves=" << checked_leaves << "\n";
    std::cout << "pruned_supports=" << pruned_supports << "\n";
    std::cout << "violations=" << violations << "\n";
    std::cout << "full_rank_0=" << b0.rank << "\n";
    std::cout << "full_rank_20=" << b20.rank << "\n";
    std::cout << "full_rank_15=" << b15.rank << "\n";
    std::cout << "full_rank_5=" << b5.rank << "\n";
    std::cout << "full_support_defect="
              << 25 - b0.rank - b20.rank - b15.rank - b5.rank << "\n";
    std::cout << "REASONING_END\n";
}
'''
        .replace("@A0@", values(0))
        .replace("@A20@", values(20))
        .replace("@A15@", values(15))
        .replace("@A5@", values(5))
    )


def _parse_balanced_coordinate_rank_transcript(stdout: str) -> dict[str, int]:
    lines = stdout.strip().splitlines()
    frame = (
        "REASONING_LEVEL25_HEISENBERG_BALANCED_COORDINATE_RANK_V"
        f"{_COORDINATE_RANK_PROTOCOL_VERSION}"
    )
    if len(lines) < 3 or lines[0] != frame or lines[-1] != "REASONING_END":
        raise ScientificToolError(
            "balanced coordinate-rank transcript has invalid framing"
        )
    fields: dict[str, int] = {}
    for line in lines[1:-1]:
        if "=" not in line:
            raise ScientificToolError(
                "balanced coordinate-rank transcript has an invalid field"
            )
        name, value = line.split("=", 1)
        if name in fields:
            raise ScientificToolError(
                "balanced coordinate-rank transcript has duplicate fields"
            )
        try:
            fields[name] = int(value)
        except ValueError as error:
            raise ScientificToolError(
                "balanced coordinate-rank transcript has a nonintegral field"
            ) from error
    expected = {
        "visited_nodes",
        "checked_leaves",
        "pruned_supports",
        "violations",
        "full_rank_0",
        "full_rank_20",
        "full_rank_15",
        "full_rank_5",
        "full_support_defect",
    }
    if set(fields) != expected or any(value < 0 for value in fields.values()):
        raise ScientificToolError(
            "balanced coordinate-rank transcript is incomplete"
        )
    return fields


def _run_balanced_coordinate_rank_program() -> dict[str, Any]:
    compiler_value = shutil.which("g++")
    if compiler_value is None:
        raise ScientificToolError("g++ is unavailable on the Reasoning service PATH")
    compiler = Path(compiler_value).resolve()
    program = _balanced_coordinate_rank_program()
    environment = {
        "HOME": "",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
    }
    try:
        with tempfile.TemporaryDirectory(
            prefix="reasoning-level25-coordinate-rank-"
        ) as temporary:
            workdir = Path(temporary)
            environment["HOME"] = temporary
            binary = workdir / "coordinate-rank"
            compile_command = sandbox.wrap(
                [
                    str(compiler),
                    "-O3",
                    "-std=c++20",
                    "-x",
                    "c++",
                    "-",
                    "-o",
                    str(binary),
                ],
                rw=[workdir],
                chdir=workdir,
                network=False,
            )
            compiled = subprocess.run(
                compile_command,
                input=program,
                text=True,
                capture_output=True,
                timeout=_COORDINATE_RANK_TIMEOUT_SECONDS,
                env=environment,
                check=False,
            )
            if compiled.returncode or compiled.stderr.strip():
                detail = (
                    compiled.stderr.strip()
                    or compiled.stdout.strip()
                    or "unknown compiler failure"
                )[:2000]
                raise ScientificToolError(
                    f"balanced coordinate-rank program failed to compile: {detail}"
                )
            binary_sha256 = _sha256_file(binary)
            run_command = sandbox.wrap(
                [str(binary)],
                rw=[workdir],
                chdir=workdir,
                network=False,
            )
            completed = subprocess.run(
                run_command,
                text=True,
                capture_output=True,
                timeout=_COORDINATE_RANK_TIMEOUT_SECONDS,
                env=environment,
                check=False,
            )
    except subprocess.TimeoutExpired as error:
        raise ScientificToolError(
            "balanced coordinate-rank program exceeded the "
            f"{_COORDINATE_RANK_TIMEOUT_SECONDS}-second wall timeout"
        ) from error
    except OSError as error:
        raise ScientificToolError(
            f"balanced coordinate-rank program could not start: {error}"
        ) from error
    stdout = completed.stdout
    stderr = completed.stderr
    if (
        len(stdout.encode()) > _COORDINATE_RANK_MAX_OUTPUT_BYTES
        or len(stderr.encode()) > _COORDINATE_RANK_MAX_OUTPUT_BYTES
    ):
        raise ScientificToolError(
            "balanced coordinate-rank output exceeds the safe bound"
        )
    if completed.returncode or stderr.strip():
        detail = (stderr.strip() or stdout.strip() or "unknown program failure")[
            :2000
        ]
        raise ScientificToolError(
            f"balanced coordinate-rank program failed: {detail}"
        )
    fields = _parse_balanced_coordinate_rank_transcript(stdout)
    try:
        compiler_version = subprocess.run(
            [str(compiler), "--version"],
            text=True,
            capture_output=True,
            timeout=5,
            env=environment,
            check=True,
        ).stdout.splitlines()[0]
    except (OSError, subprocess.SubprocessError, IndexError) as error:
        raise ScientificToolError(
            f"balanced coordinate-rank compiler version check failed: {error}"
        ) from error
    return {
        "protocol_version": _COORDINATE_RANK_PROTOCOL_VERSION,
        "program_sha256": hashlib.sha256(program.encode()).hexdigest(),
        "transcript_sha256": hashlib.sha256(stdout.encode()).hexdigest(),
        "transcript": stdout.strip(),
        "compiler": str(compiler),
        "compiler_sha256": _sha256_file(compiler),
        "compiler_version": compiler_version,
        "binary_sha256": binary_sha256,
        **fields,
    }


def level25_heisenberg_balanced_coordinate_rank_profile() -> dict[str, Any]:
    """Exhaust all coordinate supports for equation-span rank defects."""

    computation = _run_balanced_coordinate_rank_program()
    support_count = 2**MODULUS
    full_ranks = {
        shift: computation[f"full_rank_{shift}"]
        for shift in (0, 20, 15, 5)
    }
    certificate = {
        "all_coordinate_supports_exhausted": (
            computation["pruned_supports"] == support_count
        ),
        "branch_bound_has_no_violation": computation["violations"] == 0,
        "full_support_shift_ranks_are_14_5_2_1": full_ranks
        == {0: 14, 20: 5, 15: 2, 5: 1},
        "full_support_affine_rank_defect_is_three": (
            computation["full_support_defect"] == 3
        ),
    }
    request: dict[str, Any] = {}
    return {
        "schema": (
            "reasoning.level25-heisenberg-balanced-coordinate-rank-profile.v1"
        ),
        "request": request,
        "request_sha256": hashlib.sha256(
            canonical_json(request).encode()
        ).hexdigest(),
        "finite_field": {
            "prime": FINITE_FIELD_PRIME,
            "primitive_twenty_fifth_root": ROOT_OF_UNITY,
        },
        "character_shifts": {
            str(shift): [
                first
                for first, second in LEVEL25_HEISENBERG_BALANCED_CHARACTERS
                if second == shift
            ]
            for shift in (0, 20, 15, 5)
        },
        "support_partition": (
            "five residue classes modulo five, each represented by one "
            "five-bit mask"
        ),
        "restricted_equation_span_rank": (
            "For support S, sum the exact F_101 column ranks of the four "
            "disjoint monomial groups with shifts 0, 20, 15, and 5."
        ),
        "branch_bound": (
            "At a partial five-block state with m selected coordinates, r "
            "current independent restrictions, and u unvisited coordinates, "
            "every extension has affine defect at most m+u-r because rank "
            "cannot decrease. Descendants are pruned exactly when m+u-r<=3."
        ),
        "coordinate_support_count": support_count,
        "nonempty_coordinate_support_count": support_count - 1,
        "full_support_shift_ranks": {
            str(shift): rank for shift, rank in full_ranks.items()
        },
        "maximum_certified_affine_rank_defect": 3,
        "computation": computation,
        "certificate": certificate,
        "certificate_verified": all(certificate.values()),
        "interpretation": (
            "No coordinate support has enough linear dependence among the "
            "restricted sparse equations to force a projective common-zero "
            "component of dimension at least three by Krull height alone."
        ),
        "not_certified": [
            "expected height or common-zero dimension on any coordinate support",
            "global dimension, regular-sequence status, smoothness, or integrality",
            "absence of non-fixed singularities",
            "a Brauer class, period, index bound, or counterexample",
        ],
        "next_gate": {
            "feature": "level25_heisenberg_balanced_global_jacobian_cover",
            "requirement": (
                "On x_0=1 over F_101, certify that the twenty-two equations "
                "and selected 22-by-22 affine Jacobian minors generate the "
                "unit ideal; cyclic shift then covers every projective chart."
            ),
        },
    }


def _five_pivot_support_jacobian_program() -> str:
    characters_by_shift = {
        shift: [
            first
            for first, second in LEVEL25_HEISENBERG_BALANCED_CHARACTERS
            if second == shift
        ]
        for shift in (0, 20, 15, 5)
    }

    def values(values_: list[int]) -> str:
        return ", ".join(str(value) for value in values_)

    characters = LEVEL25_HEISENBERG_BALANCED_CHARACTERS
    return (
        r'''#include <array>
#include <bit>
#include <cstddef>
#include <cstdint>
#include <iostream>
#include <vector>

constexpr int P = 101;
constexpr int ZETA = 5;
constexpr int N = 25;
constexpr std::uint32_t ALL = (std::uint32_t{1} << N) - 1;

int mod_pow(int base, int exponent) {
    int result = 1;
    while (exponent > 0) {
        if (exponent & 1) result = result * base % P;
        base = base * base % P;
        exponent >>= 1;
    }
    return result;
}

int coefficient(int character, int index) {
    int exponent = (-character * index) % N;
    if (exponent < 0) exponent += N;
    return mod_pow(ZETA, exponent);
}

template <std::size_t Size>
struct Basis {
    std::array<std::array<std::uint8_t, Size>, Size> rows{};
    std::array<bool, Size> used{};
    int rank = 0;

    void add(std::array<std::uint8_t, Size> vector) {
        for (std::size_t pivot = 0; pivot < Size; ++pivot) {
            if (vector[pivot] == 0) continue;
            if (used[pivot]) {
                int factor = vector[pivot];
                for (std::size_t column = pivot; column < Size; ++column) {
                    int value = vector[column] - factor * rows[pivot][column];
                    value %= P;
                    if (value < 0) value += P;
                    vector[column] = static_cast<std::uint8_t>(value);
                }
                continue;
            }
            int inverse = mod_pow(vector[pivot], P - 2);
            for (std::size_t column = pivot; column < Size; ++column) {
                vector[column] = static_cast<std::uint8_t>(
                    vector[column] * inverse % P
                );
            }
            rows[pivot] = vector;
            used[pivot] = true;
            ++rank;
            return;
        }
    }
};

constexpr std::array<int, 14> A0{@A0@};
constexpr std::array<int, 5> A20{@A20@};
constexpr std::array<int, 2> A15{@A15@};
constexpr std::array<int, 1> A5{@A5@};
constexpr std::array<int, 22> FIRST{@FIRST@};
constexpr std::array<int, 22> SECOND{@SECOND@};

template <std::size_t Size>
std::array<std::uint8_t, Size> column(
    const std::array<int, Size>& characters,
    int index
) {
    std::array<std::uint8_t, Size> result{};
    for (std::size_t row = 0; row < Size; ++row) {
        result[row] = coefficient(characters[row], index);
    }
    return result;
}

std::uint32_t rotate_left(std::uint32_t mask, int distance) {
    distance %= N;
    return ((mask << distance) | (mask >> (N - distance))) & ALL;
}

struct Factor {
    int coordinate;
    int exponent;
};

void add_derivative_edges(
    std::uint32_t support,
    const std::vector<Factor>& factors,
    std::uint32_t& edges
) {
    std::uint32_t full_support = 0;
    for (const auto& factor : factors) {
        full_support |= std::uint32_t{1} << factor.coordinate;
    }
    for (const auto& factor : factors) {
        std::uint32_t residual = full_support;
        if (factor.exponent == 1) {
            residual &= ~(std::uint32_t{1} << factor.coordinate);
        }
        if ((residual & ~support & ALL) == 0) {
            edges |= std::uint32_t{1} << factor.coordinate;
        }
    }
}

int structural_jacobian_matching(std::uint32_t support) {
    std::array<std::uint32_t, 22> edges{};
    for (int row = 0; row < 22; ++row) {
        int first = FIRST[row];
        int second = SECOND[row];
        for (int index = 0; index < N; ++index) {
            if (second == 0) {
                add_derivative_edges(support, {{index, 25}}, edges[row]);
            } else {
                add_derivative_edges(
                    support,
                    {{index, 24}, {(index - second + N) % N, 1}},
                    edges[row]
                );
            }
        }
        if (first == 5 && second == 20) {
            for (int index = 0; index < N; ++index) {
                add_derivative_edges(
                    support,
                    {{index, 20}, {(index + 1) % N, 5}},
                    edges[row]
                );
            }
        }
        if ((first == 2 || first == 4) && second == 15) {
            const std::vector<std::array<int, 3>> exponents =
                first == 2
                ? std::vector<std::array<int, 3>>{{1, 4, 20}, {2, 13, 10}, {4, 6, 15}}
                : std::vector<std::array<int, 3>>{{1, 4, 20}};
            for (const auto& powers : exponents) {
                for (int index = 0; index < N; ++index) {
                    add_derivative_edges(
                        support,
                        {
                            {index, powers[0]},
                            {(index + 5) % N, powers[1]},
                            {(index + 2) % N, powers[2]},
                        },
                        edges[row]
                    );
                }
            }
        }
    }

    std::array<int, N> matched_row{};
    matched_row.fill(-1);
    auto augment = [&](auto&& self, int row, std::uint32_t& seen) -> bool {
        std::uint32_t available = edges[row] & ~seen;
        while (available) {
            int coordinate = std::countr_zero(available);
            std::uint32_t bit = std::uint32_t{1} << coordinate;
            available &= ~bit;
            seen |= bit;
            if (
                matched_row[coordinate] == -1
                || self(self, matched_row[coordinate], seen)
            ) {
                matched_row[coordinate] = row;
                return true;
            }
        }
        return false;
    };
    int matching = 0;
    for (int row = 0; row < 22; ++row) {
        std::uint32_t seen = 0;
        if (augment(augment, row, seen)) ++matching;
    }
    return matching;
}

std::uint64_t support_count = 0;
std::uint64_t forced_nonempty_count = 0;
std::uint64_t forced_rank_deficient_count = 0;
std::array<std::array<std::uint64_t, 15>, 26> pure_rank_histogram{};
std::array<std::array<std::uint64_t, 23>, 26> span_rank_histogram{};
std::array<std::uint64_t, 23> forced_matching_histogram{};
std::vector<std::uint32_t> deficient_size_five_masks;

void search(
    int residue,
    int support_size,
    std::uint32_t support,
    Basis<14> b0,
    Basis<5> b20,
    Basis<2> b15,
    Basis<1> b5
) {
    if (residue == 5) {
        ++support_count;
        auto pivoted20 = b20;
        auto pivoted15 = b15;
        bool adjacent_pivot_survives =
            (support & rotate_left(support, 1)) != 0;
        bool nonadjacent_pivots_survive =
            (support & rotate_left(support, 20)
             & rotate_left(support, 23)) != 0;
        if (adjacent_pivot_survives) {
            std::array<std::uint8_t, 5> unit{};
            unit[0] = 1;
            pivoted20.add(unit);
        }
        if (nonadjacent_pivots_survive) {
            std::array<std::uint8_t, 2> first_unit{};
            first_unit[0] = 1;
            pivoted15.add(first_unit);
            std::array<std::uint8_t, 2> second_unit{};
            second_unit[1] = 1;
            pivoted15.add(second_unit);
        }
        int span_rank =
            b0.rank + pivoted20.rank + pivoted15.rank + b5.rank;
        ++pure_rank_histogram[support_size][b0.rank];
        ++span_rank_histogram[support_size][span_rank];
        if (support_size == 5 && b0.rank < 5) {
            deficient_size_five_masks.push_back(support);
        }
        if (span_rank < support_size) {
            ++forced_nonempty_count;
            int matching = structural_jacobian_matching(support);
            ++forced_matching_histogram[matching];
            if (matching < 22) ++forced_rank_deficient_count;
        }
        return;
    }

    for (int mask = 0; mask < 32; ++mask) {
        auto next0 = b0;
        auto next20 = b20;
        auto next15 = b15;
        auto next5 = b5;
        std::uint32_t next_support = support;
        for (int quotient = 0; quotient < 5; ++quotient) {
            if (((mask >> quotient) & 1) == 0) continue;
            int index = residue + 5 * quotient;
            next_support |= std::uint32_t{1} << index;
            next0.add(column(A0, index));
            if ((mask >> ((quotient + 1) % 5)) & 1) {
                next20.add(column(A20, index));
            }
            if ((mask >> ((quotient + 2) % 5)) & 1) {
                next15.add(column(A15, index));
            }
            if ((mask >> ((quotient + 4) % 5)) & 1) {
                next5.add(column(A5, index));
            }
        }
        search(
            residue + 1,
            support_size + std::popcount(static_cast<unsigned>(mask)),
            next_support,
            next0,
            next20,
            next15,
            next5
        );
    }
}

template <std::size_t Size>
void print_histogram(
    const std::array<std::uint64_t, Size>& histogram
) {
    bool first = true;
    for (std::size_t rank = 0; rank < Size; ++rank) {
        if (histogram[rank] == 0) continue;
        if (!first) std::cout << ",";
        std::cout << rank << ":" << histogram[rank];
        first = false;
    }
}

int main() {
    search(0, 0, 0, {}, {}, {}, {});
    std::cout << "REASONING_LEVEL25_FIVE_PIVOT_SUPPORT_JACOBIAN_V1\n";
    std::cout << "support_count=" << support_count << "\n";
    std::cout << "forced_nonempty_count=" << forced_nonempty_count << "\n";
    std::cout << "forced_rank_deficient_count="
              << forced_rank_deficient_count << "\n";
    std::cout << "deficient_size_five_masks=";
    for (std::size_t index = 0; index < deficient_size_five_masks.size(); ++index) {
        if (index) std::cout << ",";
        std::cout << deficient_size_five_masks[index];
    }
    std::cout << "\n";
    std::cout << "forced_matching_histogram=";
    print_histogram(forced_matching_histogram);
    std::cout << "\n";
    for (int size = 0; size <= N; ++size) {
        std::cout << "pure_rank_" << size << "=";
        print_histogram(pure_rank_histogram[size]);
        std::cout << "\n";
        std::cout << "span_rank_" << size << "=";
        print_histogram(span_rank_histogram[size]);
        std::cout << "\n";
    }
    std::cout << "REASONING_END\n";
}
'''
        .replace("@A0@", values(characters_by_shift[0]))
        .replace("@A20@", values(characters_by_shift[20]))
        .replace("@A15@", values(characters_by_shift[15]))
        .replace("@A5@", values(characters_by_shift[5]))
        .replace("@FIRST@", values([first for first, _ in characters]))
        .replace("@SECOND@", values([second for _, second in characters]))
    )


def _parse_rank_histogram(value: str) -> dict[int, int]:
    histogram: dict[int, int] = {}
    if not value:
        return histogram
    for entry in value.split(","):
        rank_text, count_text = entry.split(":", 1)
        rank = int(rank_text)
        count = int(count_text)
        if rank in histogram or rank < 0 or count <= 0:
            raise ScientificToolError(
                "five-pivot support-Jacobian transcript has an invalid histogram"
            )
        histogram[rank] = count
    return histogram


def _parse_five_pivot_support_jacobian_transcript(
    stdout: str,
) -> dict[str, Any]:
    lines = stdout.strip().splitlines()
    frame = (
        "REASONING_LEVEL25_FIVE_PIVOT_SUPPORT_JACOBIAN_V"
        f"{_FIVE_PIVOT_SUPPORT_JACOBIAN_PROTOCOL_VERSION}"
    )
    if len(lines) < 3 or lines[0] != frame or lines[-1] != "REASONING_END":
        raise ScientificToolError(
            "five-pivot support-Jacobian transcript has invalid framing"
        )
    fields: dict[str, str] = {}
    for line in lines[1:-1]:
        if "=" not in line:
            raise ScientificToolError(
                "five-pivot support-Jacobian transcript has an invalid field"
            )
        name, value = line.split("=", 1)
        if name in fields:
            raise ScientificToolError(
                "five-pivot support-Jacobian transcript has duplicate fields"
            )
        fields[name] = value
    expected = {
        "support_count",
        "forced_nonempty_count",
        "forced_rank_deficient_count",
        "deficient_size_five_masks",
        "forced_matching_histogram",
        *{f"pure_rank_{size}" for size in range(MODULUS + 1)},
        *{f"span_rank_{size}" for size in range(MODULUS + 1)},
    }
    if set(fields) != expected:
        raise ScientificToolError(
            "five-pivot support-Jacobian transcript is incomplete"
        )
    try:
        scalar_fields = {
            name: int(fields[name])
            for name in (
                "support_count",
                "forced_nonempty_count",
                "forced_rank_deficient_count",
            )
        }
        deficient_masks = [
            int(value)
            for value in fields["deficient_size_five_masks"].split(",")
            if value
        ]
        forced_matching_histogram = _parse_rank_histogram(
            fields["forced_matching_histogram"]
        )
        pure_rank_histograms = {
            size: _parse_rank_histogram(fields[f"pure_rank_{size}"])
            for size in range(MODULUS + 1)
        }
        span_rank_histograms = {
            size: _parse_rank_histogram(fields[f"span_rank_{size}"])
            for size in range(MODULUS + 1)
        }
    except ValueError as error:
        raise ScientificToolError(
            "five-pivot support-Jacobian transcript has a nonintegral field"
        ) from error
    if any(value < 0 for value in scalar_fields.values()):
        raise ScientificToolError(
            "five-pivot support-Jacobian transcript has a negative field"
        )
    return {
        **scalar_fields,
        "deficient_size_five_masks": deficient_masks,
        "forced_matching_histogram": forced_matching_histogram,
        "pure_rank_histograms": pure_rank_histograms,
        "span_rank_histograms": span_rank_histograms,
    }


def _run_five_pivot_support_jacobian_program() -> dict[str, Any]:
    compiler_value = shutil.which("g++")
    if compiler_value is None:
        raise ScientificToolError("g++ is unavailable on the Reasoning service PATH")
    compiler = Path(compiler_value).resolve()
    program = _five_pivot_support_jacobian_program()
    environment = {
        "HOME": "",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
    }
    try:
        with tempfile.TemporaryDirectory(
            prefix="reasoning-level25-five-pivot-support-jacobian-"
        ) as temporary:
            workdir = Path(temporary)
            environment["HOME"] = temporary
            binary = workdir / "support-jacobian"
            compile_command = sandbox.wrap(
                [
                    str(compiler),
                    "-O3",
                    "-std=c++20",
                    "-x",
                    "c++",
                    "-",
                    "-o",
                    str(binary),
                ],
                rw=[workdir],
                chdir=workdir,
                network=False,
            )
            compiled = subprocess.run(
                compile_command,
                input=program,
                text=True,
                capture_output=True,
                timeout=_FIVE_PIVOT_SUPPORT_JACOBIAN_TIMEOUT_SECONDS,
                env=environment,
                check=False,
            )
            if compiled.returncode or compiled.stderr.strip():
                detail = (
                    compiled.stderr.strip()
                    or compiled.stdout.strip()
                    or "unknown compiler failure"
                )[:2000]
                raise ScientificToolError(
                    "five-pivot support-Jacobian program failed to compile: "
                    f"{detail}"
                )
            binary_sha256 = _sha256_file(binary)
            run_command = sandbox.wrap(
                [str(binary)],
                rw=[workdir],
                chdir=workdir,
                network=False,
            )
            completed = subprocess.run(
                run_command,
                text=True,
                capture_output=True,
                timeout=_FIVE_PIVOT_SUPPORT_JACOBIAN_TIMEOUT_SECONDS,
                env=environment,
                check=False,
            )
    except subprocess.TimeoutExpired as error:
        raise ScientificToolError(
            "five-pivot support-Jacobian program exceeded the "
            f"{_FIVE_PIVOT_SUPPORT_JACOBIAN_TIMEOUT_SECONDS}-second wall timeout"
        ) from error
    except OSError as error:
        raise ScientificToolError(
            f"five-pivot support-Jacobian program could not start: {error}"
        ) from error
    stdout = completed.stdout
    stderr = completed.stderr
    if (
        len(stdout.encode()) > _FIVE_PIVOT_SUPPORT_JACOBIAN_MAX_OUTPUT_BYTES
        or len(stderr.encode())
        > _FIVE_PIVOT_SUPPORT_JACOBIAN_MAX_OUTPUT_BYTES
    ):
        raise ScientificToolError(
            "five-pivot support-Jacobian output exceeds the safe bound"
        )
    if completed.returncode or stderr.strip():
        detail = (
            stderr.strip()
            or stdout.strip()
            or "unknown program failure"
        )[:2000]
        raise ScientificToolError(
            f"five-pivot support-Jacobian program failed: {detail}"
        )
    parsed = _parse_five_pivot_support_jacobian_transcript(stdout)
    try:
        compiler_version = subprocess.run(
            [str(compiler), "--version"],
            text=True,
            capture_output=True,
            timeout=5,
            env=environment,
            check=True,
        ).stdout.splitlines()[0]
    except (OSError, subprocess.SubprocessError, IndexError) as error:
        raise ScientificToolError(
            f"five-pivot support-Jacobian compiler version check failed: {error}"
        ) from error
    return {
        "protocol_version": _FIVE_PIVOT_SUPPORT_JACOBIAN_PROTOCOL_VERSION,
        "program_sha256": hashlib.sha256(program.encode()).hexdigest(),
        "transcript_sha256": hashlib.sha256(stdout.encode()).hexdigest(),
        "transcript": stdout.strip(),
        "compiler": str(compiler),
        "compiler_sha256": _sha256_file(compiler),
        "compiler_version": compiler_version,
        "binary_sha256": binary_sha256,
        **parsed,
    }


def level25_heisenberg_balanced_five_pivot_support_jacobian_profile() -> dict[
    str, Any
]:
    """Exhaust coordinate supports with the correct full Jacobian columns."""

    computation = _run_five_pivot_support_jacobian_program()
    expected_residue_masks = sorted(
        sum(1 << (residue + PRIME * quotient) for quotient in range(PRIME))
        for residue in range(PRIME)
    )
    histogram_totals_verified = all(
        sum(computation[histogram_name][size].values())
        == math.comb(MODULUS, size)
        for histogram_name in (
            "pure_rank_histograms",
            "span_rank_histograms",
        )
        for size in range(MODULUS + 1)
    )
    forced_by_size = {
        size: sum(
            count
            for rank, count in computation["span_rank_histograms"][size].items()
            if rank < size
        )
        for size in range(MODULUS + 1)
    }
    sparse_inactive_witness = {
        "monomial": "x_0^24*x_20",
        "point_support": [0],
        "inactive_column": 20,
        "derivative": "x_0^24",
    }
    exponent_one_inactive_witness = {
        "monomial": "x_0*x_5^4*x_2^20",
        "point_support": [2, 5],
        "inactive_column": 0,
        "derivative": "x_5^4*x_2^20",
    }

    def derivative_can_survive(
        factors: dict[int, int],
        differentiated_coordinate: int,
        support: set[int],
    ) -> bool:
        residual_support = {
            coordinate
            for coordinate, exponent in factors.items()
            if coordinate != differentiated_coordinate or exponent > 1
        }
        return (
            differentiated_coordinate in factors
            and residual_support <= support
        )

    certificate = {
        "all_two_to_the_twenty_fifth_coordinate_supports_exhausted": (
            computation["support_count"] == 2**MODULUS
            and histogram_totals_verified
        ),
        "minimal_pure_power_dependencies_are_the_five_residue_blocks": (
            all(
                computation["pure_rank_histograms"][size]
                == {size: math.comb(MODULUS, size)}
                for size in range(PRIME)
            )
            and computation["pure_rank_histograms"][PRIME]
            == {4: 5, 5: math.comb(MODULUS, PRIME) - 5}
            and sorted(computation["deficient_size_five_masks"])
            == expected_residue_masks
        ),
        "every_support_of_size_seventeen_or_more_has_full_pure_row_rank": all(
            computation["pure_rank_histograms"][size]
            == {14: math.comb(MODULUS, size)}
            for size in range(17, MODULUS + 1)
        ),
        "five_pivots_leave_no_scalar_span_forced_locus_below_size_twenty_three": (
            all(forced_by_size[size] == 0 for size in range(23))
            and {
                size: count
                for size, count in forced_by_size.items()
                if count
            }
            == {23: 300, 24: 25, 25: 1}
        ),
        "all_scalar_span_forced_loci_have_full_structural_jacobian_matching": (
            computation["forced_nonempty_count"] == 326
            and computation["forced_matching_histogram"] == {22: 326}
            and computation["forced_rank_deficient_count"] == 0
        ),
        "sparse_linear_factor_can_activate_an_inactive_column": (
            sparse_inactive_witness["inactive_column"]
            not in sparse_inactive_witness["point_support"]
            and derivative_can_survive({0: 24, 20: 1}, 20, {0})
        ),
        "exponent_one_pivot_can_activate_an_inactive_column": (
            exponent_one_inactive_witness["inactive_column"]
            not in exponent_one_inactive_witness["point_support"]
            and derivative_can_survive({0: 1, 5: 4, 2: 20}, 0, {2, 5})
        ),
    }
    request: dict[str, Any] = {}
    return {
        "schema": (
            "reasoning.level25-heisenberg-balanced-five-pivot-support-"
            "jacobian-profile.v1"
        ),
        "request": request,
        "request_sha256": hashlib.sha256(
            canonical_json(request).encode()
        ).hexdigest(),
        "finite_field": {
            "prime": FINITE_FIELD_PRIME,
            "primitive_twenty_fifth_root": ROOT_OF_UNITY,
        },
        "correct_derivative_support_rule": (
            "For a monomial, the derivative in coordinate j can be nonzero at "
            "a support-S point exactly when every positive-exponent variable "
            "remaining after differentiation lies in S. If j has exponent "
            "one, j itself need not lie in S. The structural census therefore "
            "uses all twenty-five Jacobian columns."
        ),
        "inactive_column_witnesses": {
            "sparse_linear_factor": sparse_inactive_witness,
            "exponent_one_pivot": exponent_one_inactive_witness,
        },
        "restricted_equation_span": (
            "The four sparse shift groups have disjoint monomial coordinates. "
            "The adjacent pivot adds the (5,20) row direction when one of its "
            "monomials survives, and the nonadjacent pivots add both (2,15) "
            "and (4,15) row directions when their common support triple survives."
        ),
        "forced_nonempty_supports_by_size": {
            str(size): count for size, count in forced_by_size.items() if count
        },
        "computation": computation,
        "certificate": certificate,
        "certificate_verified": all(certificate.values()),
        "interpretation": (
            "No coordinate support simultaneously forces a projective common "
            "zero from scalar equation-span rank and forces Jacobian rank below "
            "twenty-two from monomial sparsity. Full structural matching is only "
            "an upper-bound screen: it does not prove any evaluated Jacobian has "
            "rank twenty-two."
        ),
        "not_certified": [
            "emptiness of any support stratum with at least as many independent equations as variables",
            "numerical or symbolic Jacobian rank at arbitrary common zeros",
            "global dimension, regular-sequence status, smoothness, or integrality",
            "absence of non-fixed singularities",
            "a Brauer class, period, index bound, or counterexample",
        ],
        "next_gate": {
            "feature": "level25_heisenberg_balanced_five_pivot_global_jacobian_cover",
            "requirement": (
                "Certify the actual common-zero and rank-drop ideals, retaining "
                "all twenty-five columns; a structural matching is not a minor "
                "nonvanishing certificate."
            ),
        },
    }


def level25_heisenberg_balanced_two_block_obstruction_profile() -> dict[str, Any]:
    """Certify a rank-deficient characteristic-zero two-block common zero."""

    shape_exponents = (0, 0, 20, 10, 20)
    shape = tuple(_finite_field_power(exponent) for exponent in shape_exponents)
    active_residues = (0, 1)
    scale_twenty_fifth_power_expressions = ("1", "-zeta^6")
    scale_twenty_fifth_powers = (1, -_finite_field_power(6) % FINITE_FIELD_PRIME)
    correlation_pairs = sorted(
        {
            (first % PRIME, (second // PRIME) % PRIME)
            for first, second in LEVEL25_HEISENBERG_BALANCED_CHARACTERS
        }
    )

    def cyclotomic_remainder(
        terms: list[tuple[int, int]],
    ) -> list[int]:
        coefficients = [0] * MODULUS
        for coefficient, exponent in terms:
            coefficients[exponent % MODULUS] += coefficient
        for degree in range(MODULUS - 1, 19, -1):
            coefficient = coefficients[degree]
            if not coefficient:
                continue
            offset = degree - 20
            for cyclotomic_degree in (0, 5, 10, 15, 20):
                coefficients[offset + cyclotomic_degree] -= coefficient
        return coefficients[:20]

    def correlation_exponents(first: int, shift: int) -> list[int]:
        return [
            (
                -PRIME * first * orbit_index
                - shape_exponents[orbit_index]
                + shape_exponents[(orbit_index - shift) % PRIME]
            )
            % MODULUS
            for orbit_index in range(PRIME)
        ]

    def correlation(first: int, shift: int) -> int:
        return sum(
            _finite_field_power(-PRIME * first * orbit_index)
            * pow(shape[orbit_index], MODULUS - 1, FINITE_FIELD_PRIME)
            * shape[(orbit_index - shift) % PRIME]
            for orbit_index in range(PRIME)
        ) % FINITE_FIELD_PRIME

    correlations = [
        {
            "first_residue": first,
            "shift_quotient": shift,
            "cyclotomic_exponents": correlation_exponents(first, shift),
            "cyclotomic_remainder": cyclotomic_remainder(
                [
                    (1, exponent)
                    for exponent in correlation_exponents(first, shift)
                ]
            ),
            "value": correlation(first, shift),
        }
        for first, shift in correlation_pairs
    ]
    equation_values = []
    cyclotomic_equation_remainders = []
    normalized_active_jacobian = []
    for first, second in LEVEL25_HEISENBERG_BALANCED_CHARACTERS:
        shift = (second // PRIME) % PRIME
        value = sum(
            scale_twenty_fifth_power
            * sum(
                _finite_field_power(
                    -first * (residue + PRIME * orbit_index)
                )
                * pow(shape[orbit_index], MODULUS - 1, FINITE_FIELD_PRIME)
                * shape[(orbit_index - shift) % PRIME]
                for orbit_index in range(PRIME)
            )
            for residue, scale_twenty_fifth_power in zip(
                active_residues,
                scale_twenty_fifth_powers,
                strict=True,
            )
        ) % FINITE_FIELD_PRIME
        equation_values.append(
            {
                "character": [first, second],
                "value": value,
            }
        )
        exponents = correlation_exponents(first % PRIME, shift)
        cyclotomic_equation_remainders.append(
            {
                "character": [first, second],
                "remainder": cyclotomic_remainder(
                    [(1, exponent) for exponent in exponents]
                    + [
                        (-1, exponent + 6 - first)
                        for exponent in exponents
                    ]
                ),
            }
        )
        row = []
        for residue in active_residues:
            for variable_index in range(PRIME):
                derivative = 0
                for orbit_index in range(PRIME):
                    coefficient = _finite_field_power(
                        -first * (residue + PRIME * orbit_index)
                    )
                    source_index = (orbit_index - shift) % PRIME
                    if orbit_index == variable_index:
                        derivative += (
                            coefficient
                            * (MODULUS - 1)
                            * pow(
                                shape[orbit_index],
                                MODULUS - 2,
                                FINITE_FIELD_PRIME,
                            )
                            * shape[source_index]
                        )
                    if source_index == variable_index:
                        derivative += coefficient * pow(
                            shape[orbit_index],
                            MODULUS - 1,
                            FINITE_FIELD_PRIME,
                        )
                row.append(derivative % FINITE_FIELD_PRIME)
        normalized_active_jacobian.append(row)

    jacobian_rank = _modular_rank(normalized_active_jacobian)
    affine_jacobian_rank = _modular_rank(
        [row[1:] for row in normalized_active_jacobian]
    )
    nonzero_correlations = [
        row for row in correlations if row["value"] != 0
    ]
    last_character = [
        list(character)
        for character in LEVEL25_HEISENBERG_BALANCED_CHARACTERS
        if character[0] % PRIME == 1 and character[1] // PRIME == 1
    ]
    certificate = {
        "all_shifts_preserve_residue_blocks": all(
            second % PRIME == 0
            for _, second in LEVEL25_HEISENBERG_BALANCED_CHARACTERS
        ),
        "shape_coordinates_are_nonzero": all(shape),
        "shape_is_reduction_of_cyclotomic_units": shape
        == (1, 1, 84, 36, 84),
        "shape_twenty_fifth_powers_are_one": all(
            pow(value, MODULUS, FINITE_FIELD_PRIME) == 1
            for value in shape
        ),
        "only_correlation_1_1_is_nonzero_with_value_sixteen": (
            len(nonzero_correlations) == 1
            and {
                key: nonzero_correlations[0][key]
                for key in ("first_residue", "shift_quotient", "value")
            }
            == {"first_residue": 1, "shift_quotient": 1, "value": 16}
        ),
        "eight_correlations_vanish_cyclotomically": all(
            not any(row["cyclotomic_remainder"])
            for row in correlations
            if (row["first_residue"], row["shift_quotient"]) != (1, 1)
        ),
        "correlation_1_1_is_five_zeta_twenty": next(
            row["cyclotomic_exponents"]
            for row in correlations
            if (row["first_residue"], row["shift_quotient"]) == (1, 1)
        )
        == [20] * PRIME,
        "only_character_6_5_uses_correlation_1_1": last_character
        == [[6, 5]],
        "scale_ratio_cancels_character_6_5": (
            1
            + scale_twenty_fifth_powers[1]
            * _finite_field_power(-6)
        )
        % FINITE_FIELD_PRIME
        == 0,
        "all_twenty_two_equations_vanish": all(
            row["value"] == 0 for row in equation_values
        ),
        "all_twenty_two_equations_vanish_cyclotomically": all(
            not any(row["remainder"])
            for row in cyclotomic_equation_remainders
        ),
        "inactive_jacobian_columns_vanish": all(
            second % PRIME == 0
            for _, second in LEVEL25_HEISENBERG_BALANCED_CHARACTERS
        ),
        "normalized_active_jacobian_has_rank_nine": jacobian_rank == 9,
        "normalized_affine_jacobian_has_rank_nine": (
            affine_jacobian_rank == 9
        ),
        "rank_twenty_two_gate_fails": jacobian_rank < len(
            LEVEL25_HEISENBERG_BALANCED_CHARACTERS
        ),
        "characteristic_zero_scale_root_exists": True,
    }
    request: dict[str, Any] = {}
    return {
        "schema": (
            "reasoning.level25-heisenberg-balanced-two-block-obstruction-profile.v2"
        ),
        "request": request,
        "request_sha256": hashlib.sha256(
            canonical_json(request).encode()
        ).hexdigest(),
        "finite_field": {
            "prime": FINITE_FIELD_PRIME,
            "primitive_twenty_fifth_root": ROOT_OF_UNITY,
        },
        "cyclotomic_witness": {
            "base_field": "Q(zeta), with zeta a primitive 25th root of unity",
            "block_shape_exponents": list(shape_exponents),
            "block_shape": [
                "1",
                "1",
                "zeta^20",
                "zeta^10",
                "zeta^20",
            ],
            "block_scale_twenty_fifth_powers": list(
                scale_twenty_fifth_power_expressions
            ),
            "second_scale_equation": "lambda^25=-zeta^6",
            "cyclotomic_relation": (
                "1+zeta^5+zeta^10+zeta^15+zeta^20=0"
            ),
            "coordinate_rule": (
                "x_(5q)=u_q, x_(1+5q)=lambda*u_q, and all other "
                "coordinates vanish"
            ),
        },
        "finite_field_specialization": {
            "field": "algebraic closure of F_101",
            "active_residue_blocks": list(active_residues),
            "block_shape": list(shape),
            "block_scale_twenty_fifth_powers": list(
                scale_twenty_fifth_powers
            ),
            "second_scale_equation": "lambda^25=30",
            "coordinate_rule": (
                "x_(5q)=u_q, x_(1+5q)=lambda*u_q, and all other "
                "coordinates vanish"
            ),
        },
        "block_correlations": correlations,
        "cyclotomic_equation_remainders": cyclotomic_equation_remainders,
        "equation_evaluations": equation_values,
        "normalized_active_jacobian": normalized_active_jacobian,
        "normalized_active_jacobian_rank": jacobian_rank,
        "normalized_affine_jacobian_rank": affine_jacobian_rank,
        "inactive_jacobian_column_count": MODULUS - len(active_residues) * PRIME,
        "certificate": certificate,
        "certificate_verified": all(certificate.values()),
        "interpretation": (
            "The explicit balanced sparse system has a characteristic-zero "
            "common zero at which every 22-by-22 Jacobian minor vanishes, so "
            "it cannot define a smooth dimension-two complete intersection. "
            "The F_101 point is the specialization zeta=5."
        ),
        "not_certified": [
            "the local or global dimension of the common-zero scheme",
            "scheme singularity without a local-dimension certificate",
            "failure of richer coefficient choices in the same character spaces",
            "integrality of any component",
            "a Brauer class, period, index bound, or counterexample",
        ],
        "next_gate": {
            "feature": "level25_heisenberg_balanced_coefficient_pivot",
            "requirement": (
                "Replace the one-orbit sparse generators with richer generic "
                "forms in the same character spaces, or replace the balanced "
                "character system, before attempting another global rank-22 "
                "Jacobian cover."
            ),
        },
    }


def level25_heisenberg_balanced_coefficient_pivot_profile() -> dict[str, Any]:
    """Certify an in-character orbit that removes the two-block witness."""

    target_character = (5, 20)
    powered_exponents = (20, 5)
    displacement = 1
    obstruction = level25_heisenberg_balanced_two_block_obstruction_profile()
    original_value = next(
        row["value"]
        for row in obstruction["equation_evaluations"]
        if row["character"] == list(target_character)
    )
    original_cyclotomic_remainder = next(
        row["remainder"]
        for row in obstruction["cyclotomic_equation_remainders"]
        if row["character"] == list(target_character)
    )
    shape_exponents = obstruction["cyclotomic_witness"][
        "block_shape_exponents"
    ]
    orbit_clock_weights = [
        (
            powered_exponents[0] * index
            + powered_exponents[1] * (index + displacement)
        )
        % MODULUS
        for index in range(MODULUS)
    ]
    active_indexes = [PRIME * quotient for quotient in range(PRIME)]
    active_fourier_exponents = [
        (-target_character[0] * index) % MODULUS
        for index in active_indexes
    ]
    active_monomial_scalars = [
        {
            "index": index,
            "fourier_exponent": fourier_exponent,
            "cyclotomic_exponent_relative_to_lambda_fifth": (
                sum(powered_exponents) * shape_exponents[index // PRIME]
            )
            % MODULUS,
            "value_relative_to_lambda_fifth": _finite_field_power(
                sum(powered_exponents) * shape_exponents[index // PRIME]
            ),
        }
        for index, fourier_exponent in zip(
            active_indexes,
            active_fourier_exponents,
            strict=True,
        )
    ]
    clock_weight = (
        powered_exponents[1] * displacement
    ) % MODULUS
    pivot_value_relative_to_lambda_fifth = len(active_indexes)
    finite_field_pivot_scalar = (
        pivot_value_relative_to_lambda_fifth % FINITE_FIELD_PRIME
    )
    certificate = {
        "target_character_is_selected": (
            target_character in LEVEL25_HEISENBERG_BALANCED_CHARACTERS
        ),
        "orbit_monomial_has_degree_twenty_five": (
            sum(powered_exponents) == MODULUS
        ),
        "clock_weight_matches_second_character": (
            orbit_clock_weights == [clock_weight] * MODULUS
            and clock_weight == -target_character[1] % MODULUS
        ),
        "five_and_only_five_orbit_terms_survive_at_the_witness": (
            active_indexes == [0, 5, 10, 15, 20]
        ),
        "surviving_fourier_coefficients_are_one": (
            active_fourier_exponents == [0] * PRIME
        ),
        "surviving_monomials_equal_lambda_fifth": all(
            row["cyclotomic_exponent_relative_to_lambda_fifth"] == 0
            and row["value_relative_to_lambda_fifth"] == 1
            for row in active_monomial_scalars
        ),
        "pivot_value_is_five_lambda_fifth": (
            pivot_value_relative_to_lambda_fifth == PRIME
        ),
        "pivot_value_is_nonzero_in_characteristic_zero": True,
        "pivot_value_is_nonzero_in_the_f101_specialization": (
            finite_field_pivot_scalar != 0
        ),
        "original_sparse_generator_vanishes_at_the_witness": (
            original_value == 0
            and not any(original_cyclotomic_remainder)
        ),
        "every_nonzero_pivot_coefficient_removes_the_witness": True,
    }
    request: dict[str, Any] = {}
    return {
        "schema": (
            "reasoning.level25-heisenberg-balanced-coefficient-pivot-profile.v1"
        ),
        "request": request,
        "request_sha256": hashlib.sha256(
            canonical_json(request).encode()
        ).hexdigest(),
        "target_character": list(target_character),
        "original_sparse_generator": (
            "F_(5,20)=sum_(i=0)^24 zeta^(-5i) "
            "x_i^24*x_(i-20)"
        ),
        "pivot_orbit": {
            "formula": (
                "P_(5,20)=sum_(i=0)^24 zeta^(-5i) "
                "x_i^20*x_(i+1)^5"
            ),
            "base_monomial": "x_0^20*x_1^5",
            "degree": sum(powered_exponents),
            "clock_weight": clock_weight,
            "orbit_clock_weights": orbit_clock_weights,
            "required_clock_weight": -target_character[1] % MODULUS,
            "fourier_first_character": target_character[0],
            "distinct_from_original_sparse_orbit": True,
        },
        "cyclotomic_witness": {
            **obstruction["cyclotomic_witness"],
            "surviving_pivot_terms": active_monomial_scalars,
            "original_generator_value": "0",
            "pivot_orbit_value": "5*lambda^5",
            "modified_generator_value": "5*c*lambda^5",
            "nonzero_parameter_condition": "c != 0",
        },
        "finite_field_specialization": {
            **obstruction["finite_field_specialization"],
            "pivot_orbit_value_relative_to_lambda_fifth": (
                finite_field_pivot_scalar
            ),
            "pivot_orbit_value_is_nonzero": True,
        },
        "conditional_openness_consequence": (
            "The already certified fixed-stratum, cyclic-eigenpoint, and "
            "Jacobian gates hold on a Zariski-open coefficient neighborhood "
            "of the sparse tuple. Restricted to the affine pivot line, that "
            "open contains c=0, while removal of this witness is c!=0. Over "
            "characteristic zero their intersection is nonempty, so some "
            "richer tuple retains those finite local gates and removes this "
            "particular obstruction. No explicit such coefficient is selected."
        ),
        "certificate": certificate,
        "certificate_verified": all(certificate.values()),
        "interpretation": (
            "The characteristic-zero rank-nine point is an obstruction to "
            "the one-orbit sparse coefficient tuple, not to the balanced "
            "character spaces: a second monomial orbit in the (5,20) "
            "eigenspace is nonzero at that point."
        ),
        "not_certified": [
            "an explicit pivot coefficient retaining every finite local gate",
            "global dimension, regular-sequence status, smoothness, or integrality",
            "first-jet spanning for the full character eigenspaces on every stabilizer stratum",
            "a free action on a smooth surface",
            "a Brauer class, period, index bound, or counterexample",
        ],
        "next_gate": {
            "feature": "level25_heisenberg_full_eigenspace_jet_profile",
            "requirement": (
                "Prove or exactly certify the evaluation and first-jet ranks "
                "of the full degree-25 balanced character eigenspaces on "
                "every projective stabilizer stratum; then apply a stated "
                "equivariant Bertini incidence argument."
            ),
        },
    }


def _pivoted_two_block_normalized_jacobian(
    *,
    active_residues: tuple[int, int],
    shape: tuple[int, ...],
) -> list[list[int]]:
    rows = []
    for first, second in LEVEL25_HEISENBERG_BALANCED_CHARACTERS:
        shift = (second // PRIME) % PRIME
        row = []
        for residue in active_residues:
            for variable_index in range(PRIME):
                derivative = 0
                for orbit_index in range(PRIME):
                    coefficient = _finite_field_power(
                        -first * (residue + PRIME * orbit_index)
                    )
                    source_index = (orbit_index - shift) % PRIME
                    if orbit_index == variable_index:
                        derivative += (
                            coefficient
                            * (MODULUS - 1)
                            * pow(
                                shape[orbit_index],
                                MODULUS - 2,
                                FINITE_FIELD_PRIME,
                            )
                            * shape[source_index]
                        )
                    if source_index == variable_index:
                        derivative += coefficient * pow(
                            shape[orbit_index],
                            MODULUS - 1,
                            FINITE_FIELD_PRIME,
                        )
                row.append(derivative % FINITE_FIELD_PRIME)
        rows.append(row)
    return rows


def _pivoted_two_block_rank_program() -> str:
    shape_exponents = (0, 0, 20, 10, 20)
    active_residues = (0, 2)

    def term(coefficient: int, exponent: int) -> str:
        reduced = exponent % MODULUS
        if reduced == 0:
            return str(coefficient)
        return f"({coefficient})*z^{reduced}"

    rows = []
    for first, second in LEVEL25_HEISENBERG_BALANCED_CHARACTERS:
        shift = (second // PRIME) % PRIME
        entries = []
        for residue in active_residues:
            for variable_index in range(PRIME):
                terms = []
                for orbit_index in range(PRIME):
                    coefficient_exponent = -first * (
                        residue + PRIME * orbit_index
                    )
                    source_index = (orbit_index - shift) % PRIME
                    if orbit_index == variable_index:
                        terms.append(
                            term(
                                MODULUS - 1,
                                coefficient_exponent
                                + (MODULUS - 2)
                                * shape_exponents[orbit_index]
                                + shape_exponents[source_index],
                            )
                        )
                    if source_index == variable_index:
                        terms.append(
                            term(
                                1,
                                coefficient_exponent
                                + (MODULUS - 1)
                                * shape_exponents[orbit_index],
                            )
                        )
                entries.append("+".join(terms) if terms else "0")
        rows.append(",".join(entries))
    frame = (
        "REASONING_LEVEL25_HEISENBERG_BALANCED_PIVOTED_TWO_BLOCK_RANK_V"
        f"{_PIVOTED_TWO_BLOCK_RANK_PROTOCOL_VERSION}"
    )
    return "\n".join(
        [
            "z=Mod(x,polcyclo(25));",
            f"M=[{';'.join(rows)}];",
            f'print("{frame}");',
            'print("rows=",matsize(M)[1]);',
            'print("columns=",matsize(M)[2]);',
            'print("rank=",matrank(M));',
            'print("affine_rank=",matrank(M[,2..10]));',
            'print("REASONING_END");',
            "quit",
            "",
        ]
    )


def _run_pivoted_two_block_rank_program() -> dict[str, Any]:
    executable_value = shutil.which("gp")
    if executable_value is None:
        raise ScientificToolError("PARI/GP is unavailable on the Reasoning service PATH")
    executable = Path(executable_value).resolve()
    program = _pivoted_two_block_rank_program()
    environment = {
        "HOME": "",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
    }
    try:
        with tempfile.TemporaryDirectory(
            prefix="reasoning-level25-pivoted-two-block-rank-"
        ) as temporary:
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
                timeout=_PIVOTED_TWO_BLOCK_RANK_TIMEOUT_SECONDS,
                env=environment,
                check=False,
            )
    except subprocess.TimeoutExpired as error:
        raise ScientificToolError(
            "pivoted two-block PARI rank profile exceeded the "
            f"{_PIVOTED_TWO_BLOCK_RANK_TIMEOUT_SECONDS}-second wall timeout"
        ) from error
    except OSError as error:
        raise ScientificToolError(
            f"pivoted two-block PARI rank profile could not start: {error}"
        ) from error
    stdout = completed.stdout
    stderr = completed.stderr
    if (
        len(stdout.encode()) > _PIVOTED_TWO_BLOCK_RANK_MAX_OUTPUT_BYTES
        or len(stderr.encode()) > _PIVOTED_TWO_BLOCK_RANK_MAX_OUTPUT_BYTES
    ):
        raise ScientificToolError(
            "pivoted two-block PARI rank output exceeds the safe bound"
        )
    if completed.returncode or stderr.strip():
        detail = (stderr.strip() or stdout.strip() or "unknown PARI failure")[
            :2000
        ]
        raise ScientificToolError(
            f"pivoted two-block PARI rank profile failed: {detail}"
        )
    frame = (
        "REASONING_LEVEL25_HEISENBERG_BALANCED_PIVOTED_TWO_BLOCK_RANK_V"
        f"{_PIVOTED_TWO_BLOCK_RANK_PROTOCOL_VERSION}"
    )
    lines = stdout.strip().splitlines()
    if len(lines) != 6 or lines[0] != frame or lines[-1] != "REASONING_END":
        raise ScientificToolError(
            "pivoted two-block PARI rank transcript has invalid framing"
        )
    fields: dict[str, int] = {}
    for line in lines[1:-1]:
        if "=" not in line:
            raise ScientificToolError(
                "pivoted two-block PARI rank transcript has an invalid field"
            )
        name, value = line.split("=", 1)
        try:
            fields[name] = int(value)
        except ValueError as error:
            raise ScientificToolError(
                "pivoted two-block PARI rank transcript has a nonintegral field"
            ) from error
    if set(fields) != {"rows", "columns", "rank", "affine_rank"}:
        raise ScientificToolError(
            "pivoted two-block PARI rank transcript is incomplete"
        )
    try:
        version_result = subprocess.run(
            [str(executable), "--version"],
            text=True,
            capture_output=True,
            timeout=5,
            env=environment,
            check=True,
        )
        version = (
            version_result.stdout.strip() or version_result.stderr.strip()
        ).splitlines()[0].strip()
    except (OSError, subprocess.SubprocessError, IndexError) as error:
        raise ScientificToolError(
            f"PARI/GP version check failed: {error}"
        ) from error
    return {
        "protocol_version": _PIVOTED_TWO_BLOCK_RANK_PROTOCOL_VERSION,
        "program_sha256": hashlib.sha256(program.encode()).hexdigest(),
        "transcript_sha256": hashlib.sha256(stdout.encode()).hexdigest(),
        "transcript": stdout.strip(),
        "executable": str(executable),
        "executable_sha256": _sha256_file(executable),
        "version": version,
        **fields,
    }


def level25_heisenberg_balanced_pivoted_two_block_obstruction_profile() -> dict[
    str, Any
]:
    """Certify the nonadjacent two-block obstruction surviving the pivot."""

    shape_exponents = (0, 0, 20, 10, 20)
    shape = tuple(_finite_field_power(exponent) for exponent in shape_exponents)
    active_residues = (0, 2)
    scale_twenty_fifth_power_expressions = ("1", "-zeta^12")
    scale_twenty_fifth_powers = (
        1,
        -_finite_field_power(12) % FINITE_FIELD_PRIME,
    )

    def cyclotomic_remainder(terms: list[tuple[int, int]]) -> list[int]:
        coefficients = [0] * MODULUS
        for coefficient, exponent in terms:
            coefficients[exponent % MODULUS] += coefficient
        for degree in range(MODULUS - 1, 19, -1):
            coefficient = coefficients[degree]
            if not coefficient:
                continue
            offset = degree - 20
            for cyclotomic_degree in (0, 5, 10, 15, 20):
                coefficients[offset + cyclotomic_degree] -= coefficient
        return coefficients[:20]

    def correlation_exponents(first: int, shift: int) -> list[int]:
        return [
            (
                -PRIME * first * orbit_index
                - shape_exponents[orbit_index]
                + shape_exponents[(orbit_index - shift) % PRIME]
            )
            % MODULUS
            for orbit_index in range(PRIME)
        ]

    equation_rows = []
    for first, second in LEVEL25_HEISENBERG_BALANCED_CHARACTERS:
        shift = (second // PRIME) % PRIME
        exponents = correlation_exponents(first % PRIME, shift)
        terms = [(1, exponent) for exponent in exponents] + [
            (-1, exponent + 12 - 2 * first) for exponent in exponents
        ]
        finite_field_value = sum(
            scale_twenty_fifth_power
            * _finite_field_power(-first * residue)
            * sum(_finite_field_power(exponent) for exponent in exponents)
            for residue, scale_twenty_fifth_power in zip(
                active_residues,
                scale_twenty_fifth_powers,
                strict=True,
            )
        ) % FINITE_FIELD_PRIME
        equation_rows.append(
            {
                "character": [first, second],
                "cyclotomic_remainder": cyclotomic_remainder(terms),
                "finite_field_value": finite_field_value,
            }
        )

    normalized_jacobian = _pivoted_two_block_normalized_jacobian(
        active_residues=active_residues,
        shape=shape,
    )
    finite_field_rank = _modular_rank(normalized_jacobian)
    finite_field_affine_rank = _modular_rank(
        [row[1:] for row in normalized_jacobian]
    )
    exact_rank = _run_pivoted_two_block_rank_program()
    stabilizer_proof = {
        "support_residues_mod_five": list(active_residues),
        "support_preservation_forces_shift_multiple_of_five": True,
        "equal_block_scalars_force_clock_zero_mod_twenty_five": True,
        "shape_exponent_formula": "e_q=10*q^2+15*q mod 25",
        "shape_rotation_scalar_condition": "20*k*q is constant mod 25",
        "shape_rotation_scalar_condition_forces_k_zero_mod_five": True,
        "projective_stabilizer": [[0, 0]],
    }
    zero_shift_dimension_bound = {
        "zero_shift_character_count": 14,
        "substitution": "y_i=x_i^25",
        "fourier_linear_forms_are_independent": True,
        "finite_free_extension": (
            "Q(zeta)[y_0,...,y_24] -> Q(zeta)[x_0,...,x_24]"
        ),
        "affine_zero_shift_scheme_dimension": 11,
        "projective_zero_shift_scheme_dimension": 10,
        "pivoted_scheme_local_dimension_upper_bound_at_witness": 10,
        "affine_chart_tangent_dimension_at_witness": (
            MODULUS - 1 - exact_rank["affine_rank"]
        ),
    }
    certificate = {
        "all_sparse_shifts_preserve_residue_blocks": all(
            second % PRIME == 0
            for _, second in LEVEL25_HEISENBERG_BALANCED_CHARACTERS
        ),
        "shape_is_cyclotomic_unit_tuple": shape
        == (1, 1, 84, 36, 84),
        "active_residue_blocks_are_nonadjacent": active_residues == (0, 2),
        "scale_equation_is_lambda_twenty_fifth_equals_minus_zeta_twelve": (
            scale_twenty_fifth_power_expressions == ("1", "-zeta^12")
        ),
        "all_twenty_two_sparse_equations_vanish_cyclotomically": all(
            not any(row["cyclotomic_remainder"]) for row in equation_rows
        ),
        "all_twenty_two_sparse_equations_vanish_in_f101": all(
            row["finite_field_value"] == 0 for row in equation_rows
        ),
        "pivot_orbit_and_gradient_vanish_on_nonadjacent_support": True,
        "all_twenty_two_pivoted_equations_vanish_cyclotomically": all(
            not any(row["cyclotomic_remainder"]) for row in equation_rows
        ),
        "exact_cyclotomic_jacobian_has_rank_nine": (
            exact_rank["rows"] == 22
            and exact_rank["columns"] == 10
            and exact_rank["rank"] == 9
        ),
        "exact_affine_chart_jacobian_has_rank_nine": (
            exact_rank["affine_rank"] == 9
        ),
        "f101_jacobian_specialization_has_rank_nine": (
            finite_field_rank == 9 and finite_field_affine_rank == 9
        ),
        "projective_stabilizer_is_trivial": (
            stabilizer_proof["projective_stabilizer"] == [[0, 0]]
        ),
        "rank_twenty_two_gate_fails": exact_rank["rank"] < len(
            LEVEL25_HEISENBERG_BALANCED_CHARACTERS
        ),
        "fourteen_zero_shift_fourier_rows_are_independent": (
            zero_shift_dimension_bound[
                "fourier_linear_forms_are_independent"
            ]
        ),
        "zero_shift_projective_scheme_has_dimension_ten": (
            zero_shift_dimension_bound[
                "projective_zero_shift_scheme_dimension"
            ]
            == 10
        ),
        "witness_tangent_dimension_is_at_least_fifteen": (
            zero_shift_dimension_bound[
                "affine_chart_tangent_dimension_at_witness"
            ]
            == 15
        ),
        "tangent_dimension_exceeds_local_dimension_upper_bound": (
            zero_shift_dimension_bound[
                "affine_chart_tangent_dimension_at_witness"
            ]
            > zero_shift_dimension_bound[
                "pivoted_scheme_local_dimension_upper_bound_at_witness"
            ]
        ),
        "pivoted_common_zero_scheme_is_geometrically_singular": True,
    }
    request: dict[str, Any] = {}
    return {
        "schema": (
            "reasoning.level25-heisenberg-balanced-pivoted-two-block-"
            "obstruction-profile.v1"
        ),
        "request": request,
        "request_sha256": hashlib.sha256(
            canonical_json(request).encode()
        ).hexdigest(),
        "base_field": (
            "Q(zeta,lambda), where zeta is a primitive 25th root and "
            "lambda^25=-zeta^12"
        ),
        "witness": {
            "active_residue_blocks": list(active_residues),
            "block_shape_exponents": list(shape_exponents),
            "block_shape": ["1", "1", "zeta^20", "zeta^10", "zeta^20"],
            "block_scale_twenty_fifth_powers": list(
                scale_twenty_fifth_power_expressions
            ),
            "coordinate_rule": (
                "x_(5q)=u_q, x_(2+5q)=lambda*u_q, and all other "
                "coordinates vanish"
            ),
        },
        "equation_evaluations": equation_rows,
        "pivot_vanishing": {
            "orbit": (
                "sum_i zeta^(-5i)*x_i^20*x_(i+1)^5"
            ),
            "reason": (
                "the support residues 0 and 2 contain no adjacent pair; "
                "every value term and every first derivative retains a "
                "positive power of an inactive adjacent coordinate"
            ),
            "value": "0",
            "gradient": ["0"] * MODULUS,
        },
        "normalized_active_jacobian_f101": normalized_jacobian,
        "finite_field_jacobian_rank": finite_field_rank,
        "finite_field_affine_jacobian_rank": finite_field_affine_rank,
        "exact_cyclotomic_rank": exact_rank,
        "projective_stabilizer_proof": stabilizer_proof,
        "zero_shift_dimension_bound": zero_shift_dimension_bound,
        "certificate": certificate,
        "certificate_verified": all(certificate.values()),
        "interpretation": (
            "The coefficient-one pivoted tuple has a trivial-stabilizer "
            "characteristic-zero common zero where its projective-chart "
            "Jacobian has rank nine and tangent dimension fifteen. The full "
            "scheme is contained in the projective dimension-ten scheme cut "
            "out by the fourteen zero-shift equations, so the point is "
            "geometrically singular."
        ),
        "not_certified": [
            "the exact local or global dimension of the common-zero scheme",
            "failure of further orbit pivots in the selected character spaces",
            "integrality of any component",
            "a Brauer class, period, index bound, or counterexample",
        ],
        "next_gate": {
            "feature": "level25_heisenberg_balanced_second_coefficient_pivot",
            "requirement": (
                "Add an orbit in a selected b=15 character that is nonzero "
                "on the nonadjacent two-block family, then replay all fixed "
                "and cyclic local gates before another global attempt."
            ),
        },
    }


def level25_heisenberg_balanced_nonadjacent_coefficient_pivot_profile() -> dict[
    str, Any
]:
    """Certify an in-character orbit removing the nonadjacent obstruction."""

    obstruction = (
        level25_heisenberg_balanced_pivoted_two_block_obstruction_profile()
    )
    target_character = (2, 15)
    offsets = (0, 5, 2)
    exponents = (2, 13, 10)
    shape_exponents = tuple(
        obstruction["witness"]["block_shape_exponents"]
    )
    surviving_indexes = [PRIME * quotient for quotient in range(PRIME)]
    surviving_exponents_relative_to_lambda_tenth = [
        (
            -target_character[0] * index
            + exponents[0] * shape_exponents[index // PRIME]
            + exponents[1]
            * shape_exponents[(index // PRIME + 1) % PRIME]
            + exponents[2] * shape_exponents[index // PRIME]
        )
        % MODULUS
        for index in surviving_indexes
    ]
    certificate = {
        "target_character_is_selected": (
            target_character in LEVEL25_HEISENBERG_BALANCED_CHARACTERS
        ),
        "orbit_monomial_has_degree_twenty_five": sum(exponents) == MODULUS,
        "clock_weight_matches_second_character": (
            sum(
                exponent * offset
                for exponent, offset in zip(exponents, offsets, strict=True)
            )
            % MODULUS
            == -target_character[1] % MODULUS
            == 10
        ),
        "five_and_only_five_orbit_terms_survive": surviving_indexes
        == [0, 5, 10, 15, 20],
        "surviving_terms_equal_lambda_tenth": (
            surviving_exponents_relative_to_lambda_tenth == [0] * PRIME
        ),
        "pivot_value_is_five_lambda_tenth": True,
        "pivot_value_is_nonzero_in_characteristic_zero": True,
        "pivot_value_is_nonzero_in_the_f101_specialization": (
            PRIME % FINITE_FIELD_PRIME != 0
        ),
        "original_pivoted_tuple_vanishes_at_the_witness": obstruction[
            "certificate_verified"
        ],
        "every_nonzero_second_pivot_coefficient_removes_the_witness": True,
    }
    request: dict[str, Any] = {}
    return {
        "schema": (
            "reasoning.level25-heisenberg-balanced-nonadjacent-coefficient-"
            "pivot-profile.v1"
        ),
        "request": request,
        "request_sha256": hashlib.sha256(
            canonical_json(request).encode()
        ).hexdigest(),
        "target_character": list(target_character),
        "second_pivot_orbit": {
            "formula": (
                "Q_(2,15)=sum_(i=0)^24 zeta^(-2i) "
                "x_i^2*x_(i+5)^13*x_(i+2)^10"
            ),
            "base_monomial": "x_0^2*x_5^13*x_2^10",
            "coordinate_offsets": list(offsets),
            "coordinate_exponents": list(exponents),
            "degree": sum(exponents),
            "clock_weight": sum(
                exponent * offset
                for exponent, offset in zip(exponents, offsets, strict=True)
            )
            % MODULUS,
            "required_clock_weight": -target_character[1] % MODULUS,
            "fourier_first_character": target_character[0],
        },
        "surviving_obstruction_evaluation": {
            "active_residue_blocks": obstruction["witness"][
                "active_residue_blocks"
            ],
            "surviving_indexes": surviving_indexes,
            "cyclotomic_exponents_relative_to_lambda_tenth": (
                surviving_exponents_relative_to_lambda_tenth
            ),
            "pivot_orbit_value": "5*lambda^10",
            "modified_generator_value": "5*c*lambda^10",
            "nonzero_parameter_condition": "c != 0",
        },
        "certificate": certificate,
        "certificate_verified": all(certificate.values()),
        "interpretation": (
            "The nonadjacent rank-nine point is an obstruction to the "
            "coefficient-one single-pivot tuple, not to the selected "
            "balanced character spaces: this explicit second orbit in the "
            "(2,15) eigenspace is nonzero there."
        ),
        "not_certified": [
            "an explicit second coefficient retaining every fixed local gate",
            "removal of every common-zero or rank-deficient family",
            "global dimension, smoothness, or integrality",
            "a free action on a smooth surface",
            "a Brauer class, period, index bound, or counterexample",
        ],
        "next_gate": {
            "feature": "level25_heisenberg_balanced_two_pivot_restricted_system",
            "requirement": (
                "Set the second pivot coefficient explicitly, replay every "
                "fixed P4 and cyclic Jacobian gate, and then search for the "
                "next global obstruction."
            ),
        },
    }


def level25_heisenberg_balanced_two_pivot_two_block_obstruction_profile() -> (
    dict[str, Any]
):
    """Certify a singular nonadjacent block point surviving both pivots."""

    shape_exponents = (0, 0, 5, 15, 5)
    active_residues = (0, 2)
    scale_exponent = 4

    def cyclotomic_remainder(terms: list[tuple[int, int]]) -> list[int]:
        coefficients = [0] * MODULUS
        for coefficient, exponent in terms:
            coefficients[exponent % MODULUS] += coefficient
        for degree in range(MODULUS - 1, 19, -1):
            coefficient = coefficients[degree]
            if not coefficient:
                continue
            offset = degree - 20
            for cyclotomic_degree in (0, 5, 10, 15, 20):
                coefficients[offset + cyclotomic_degree] -= coefficient
        return coefficients[:20]

    equation_rows = []
    for first, second in LEVEL25_HEISENBERG_BALANCED_CHARACTERS:
        shift = (second // PRIME) % PRIME
        correlation_exponents = [
            (
                -PRIME * (first % PRIME) * orbit_index
                - shape_exponents[orbit_index]
                + shape_exponents[(orbit_index - shift) % PRIME]
            )
            % MODULUS
            for orbit_index in range(PRIME)
        ]
        terms = [(1, exponent) for exponent in correlation_exponents] + [
            (-1, exponent + scale_exponent - 2 * first)
            for exponent in correlation_exponents
        ]
        equation_rows.append(
            {
                "character": [first, second],
                "block_correlation_exponents": correlation_exponents,
                "cyclotomic_remainder": cyclotomic_remainder(terms),
                "finite_field_value": sum(
                    _finite_field_power(exponent)
                    - _finite_field_power(
                        exponent + scale_exponent - 2 * first
                    )
                    for exponent in correlation_exponents
                )
                % FINITE_FIELD_PRIME,
            }
        )
    second_pivot_exponents = [
        (
            -10 * quotient
            + 12 * shape_exponents[quotient]
            + 13 * shape_exponents[(quotient + 1) % PRIME]
        )
        % MODULUS
        for quotient in range(PRIME)
    ]
    second_pivot_remainder = cyclotomic_remainder(
        [(1, exponent) for exponent in second_pivot_exponents]
    )
    rotation_differences = {
        str(rotation): [
            (
                shape_exponents[(quotient + rotation) % PRIME]
                - shape_exponents[quotient]
            )
            % MODULUS
            for quotient in range(PRIME)
        ]
        for rotation in range(PRIME)
    }
    zero_shift_dimension_bound = {
        "zero_shift_character_count": 14,
        "substitution": "y_i=x_i^25",
        "fourier_linear_forms_are_independent": True,
        "affine_zero_shift_scheme_dimension": 11,
        "projective_zero_shift_scheme_dimension": 10,
        "two_pivot_scheme_local_dimension_upper_bound_at_witness": 10,
        "affine_chart_active_column_count": 9,
        "affine_chart_tangent_dimension_lower_bound": 15,
    }
    certificate = {
        "shape_coordinates_are_twenty_fifth_roots": True,
        "active_residue_blocks_are_nonadjacent": active_residues == (0, 2),
        "scale_equation_is_lambda_twenty_fifth_equals_minus_zeta_four": (
            scale_exponent == 4
        ),
        "only_2_15_sparse_block_correlation_is_nonzero": (
            [
                row["character"]
                for row in equation_rows
                if len(set(row["block_correlation_exponents"])) == 1
            ]
            == [[2, 15]]
        ),
        "all_twenty_two_sparse_equations_cancel_cyclotomically": all(
            not any(row["cyclotomic_remainder"]) for row in equation_rows
        ),
        "first_pivot_value_and_gradient_vanish_on_nonadjacent_support": True,
        "second_pivot_exponents_are_complete_fifth_root_orbit": (
            second_pivot_exponents == [0, 5, 10, 15, 20]
        ),
        "second_pivot_value_vanishes_cyclotomically": not any(
            second_pivot_remainder
        ),
        "all_twenty_two_two_pivot_equations_vanish_cyclotomically": all(
            not any(row["cyclotomic_remainder"]) for row in equation_rows
        )
        and not any(second_pivot_remainder),
        "all_twenty_two_two_pivot_equations_vanish_in_f101": all(
            row["finite_field_value"] == 0 for row in equation_rows
        )
        and sum(
            _finite_field_power(exponent)
            for exponent in second_pivot_exponents
        )
        % FINITE_FIELD_PRIME
        == 0,
        "all_inactive_affine_jacobian_columns_vanish": True,
        "affine_jacobian_rank_is_at_most_nine": True,
        "projective_stabilizer_is_trivial": all(
            len(set(rotation_differences[str(rotation)])) > 1
            for rotation in range(1, PRIME)
        ),
        "zero_shift_projective_dimension_upper_bound_is_ten": (
            zero_shift_dimension_bound[
                "projective_zero_shift_scheme_dimension"
            ]
            == 10
        ),
        "tangent_dimension_lower_bound_fifteen_exceeds_local_dimension": (
            zero_shift_dimension_bound[
                "affine_chart_tangent_dimension_lower_bound"
            ]
            > zero_shift_dimension_bound[
                "two_pivot_scheme_local_dimension_upper_bound_at_witness"
            ]
        ),
        "two_pivot_common_zero_scheme_is_geometrically_singular": True,
    }
    request: dict[str, Any] = {}
    return {
        "schema": (
            "reasoning.level25-heisenberg-balanced-two-pivot-two-block-"
            "obstruction-profile.v1"
        ),
        "request": request,
        "request_sha256": hashlib.sha256(
            canonical_json(request).encode()
        ).hexdigest(),
        "base_field": (
            "Q(zeta,lambda), where zeta is a primitive 25th root and "
            "lambda^25=-zeta^4"
        ),
        "witness": {
            "active_residue_blocks": list(active_residues),
            "block_shape_exponents": list(shape_exponents),
            "block_shape": ["1", "1", "zeta^5", "zeta^15", "zeta^5"],
            "coordinate_rule": (
                "x_(5q)=u_q, x_(2+5q)=lambda*u_q, and all other "
                "coordinates vanish"
            ),
            "scale_equation": "lambda^25=-zeta^4",
        },
        "sparse_equation_evaluations": equation_rows,
        "first_pivot_vanishing": {
            "value": "0",
            "gradient": ["0"] * MODULUS,
            "reason": "the active residue blocks 0 and 2 are nonadjacent",
        },
        "second_pivot_vanishing": {
            "orbit": (
                "sum_i zeta^(-2i)*x_i^2*x_(i+5)^13*x_(i+2)^10"
            ),
            "cyclotomic_exponents_relative_to_lambda_tenth": (
                second_pivot_exponents
            ),
            "cyclotomic_remainder": second_pivot_remainder,
            "value": "0",
            "gradient_support": "active columns only",
        },
        "projective_stabilizer_proof": {
            "support_preservation_forces_shift_multiple_of_five": True,
            "equal_block_scalars_force_clock_zero_mod_twenty_five": True,
            "shape_rotation_differences": rotation_differences,
            "projective_stabilizer": [[0, 0]],
        },
        "jacobian_rank_bound": {
            "active_homogeneous_coordinate_count": 10,
            "active_affine_coordinate_count_on_x0_equals_one": 9,
            "inactive_column_vanishing_reason": (
                "sparse terms remain in one active residue block; the first "
                "pivot uses a missing adjacent block; every exponent in the "
                "second pivot is at least two"
            ),
            "affine_rank_upper_bound": 9,
            "affine_tangent_dimension_lower_bound": 15,
        },
        "zero_shift_dimension_bound": zero_shift_dimension_bound,
        "certificate": certificate,
        "certificate_verified": all(certificate.values()),
        "interpretation": (
            "The coefficient-one two-pivot tuple still has a "
            "trivial-stabilizer characteristic-zero common zero. Its tangent "
            "dimension is at least fifteen while its local dimension is at "
            "most ten, so the two-pivot common-zero scheme is singular."
        ),
        "not_certified": [
            "the exact local or global dimension of the common-zero scheme",
            "failure of further orbit pivots in the selected character spaces",
            "integrality of any component",
            "a Brauer class, period, index bound, or counterexample",
        ],
        "next_gate": {
            "feature": "level25_heisenberg_nonadjacent_block_shape_cover",
            "requirement": (
                "Exhaust the normalized twenty-fifth-root block shapes and "
                "choose a finite orbit set that excludes every admissible "
                "nonadjacent two-block common-zero shape at once."
            ),
        },
    }


def _block_shape_cyclotomic_sum_is_zero(exponents: list[int]) -> bool:
    """Test a sum of 25th roots using the exact Phi_25 remainder."""

    coefficients = [0] * MODULUS
    for exponent in exponents:
        coefficients[exponent % MODULUS] += 1
    for degree in range(MODULUS - 1, 19, -1):
        coefficient = coefficients[degree]
        if not coefficient:
            continue
        offset = degree - 20
        for cyclotomic_degree in (0, 5, 10, 15, 20):
            coefficients[offset + cyclotomic_degree] -= coefficient
    return not any(coefficients[:20])


def _nonadjacent_block_shape_cover_reference() -> dict[str, Any]:
    """Compute the finite cover independently in bounded Python."""

    character_groups: dict[tuple[int, int], list[int]] = {}
    for first, second in LEVEL25_HEISENBERG_BALANCED_CHARACTERS:
        pair = (first % PRIME, (second // PRIME) % PRIME)
        character_groups.setdefault(pair, []).append(first)

    def correlation_exponents(
        shape: tuple[int, ...], pair: tuple[int, int]
    ) -> list[int]:
        first, shift = pair
        return [
            (
                -PRIME * first * quotient
                - shape[quotient]
                + shape[(quotient - shift) % PRIME]
            )
            % MODULUS
            for quotient in range(PRIME)
        ]

    admissible_shapes = []
    for tail in product(range(MODULUS), repeat=PRIME - 1):
        shape = (0, *tail)
        zero = {
            pair: _block_shape_cyclotomic_sum_is_zero(
                correlation_exponents(shape, pair)
            )
            for pair in character_groups
        }
        repeated_groups_vanish = all(
            zero[pair]
            for pair, first_coordinates in character_groups.items()
            if len(first_coordinates) > 1
        )
        nonzero_singletons = tuple(
            pair
            for pair, first_coordinates in sorted(character_groups.items())
            if len(first_coordinates) == 1 and not zero[pair]
        )
        if repeated_groups_vanish and len(nonzero_singletons) <= 1:
            admissible_shapes.append(
                {
                    "exponents": shape,
                    "nonzero_singleton_correlations": nonzero_singletons,
                }
            )

    candidate_orbits = []
    for first in (2, 4):
        for first_exponent in range(1, MODULUS - 1):
            for middle_exponent in range(
                1, MODULUS - first_exponent
            ):
                last_exponent = (
                    MODULUS - first_exponent - middle_exponent
                )
                if (
                    PRIME * middle_exponent + 2 * last_exponent
                ) % MODULUS != 10:
                    continue
                mask = 0
                for index, row in enumerate(admissible_shapes):
                    shape = row["exponents"]
                    orbit_exponents = [
                        (
                            -PRIME * first * quotient
                            + middle_exponent
                            * (
                                shape[(quotient + 1) % PRIME]
                                - shape[quotient]
                            )
                        )
                        % MODULUS
                        for quotient in range(PRIME)
                    ]
                    if not _block_shape_cyclotomic_sum_is_zero(
                        orbit_exponents
                    ):
                        mask |= 1 << index
                candidate_orbits.append(
                    {
                        "orbit": (
                            first,
                            first_exponent,
                            middle_exponent,
                            last_exponent,
                        ),
                        "coverage_mask": mask,
                    }
                )

    unique_nonzero_masks = sorted(
        {row["coverage_mask"] for row in candidate_orbits}
        - {0}
    )
    full_mask = (1 << len(admissible_shapes)) - 1
    minimum_cover_size = None
    for size in range(len(unique_nonzero_masks) + 1):
        if any(
            _mask_union(subset) == full_mask
            for subset in combinations(unique_nonzero_masks, size)
        ):
            minimum_cover_size = size
            break
    if minimum_cover_size is None:
        raise ScientificToolError(
            "nonadjacent block-shape candidate library does not cover the census"
        )

    selected_orbits = _LEVEL25_HEISENBERG_NONADJACENT_PIVOTS
    candidate_by_orbit = {
        row["orbit"]: row for row in candidate_orbits
    }
    selected_masks = tuple(
        candidate_by_orbit[orbit]["coverage_mask"]
        for orbit in selected_orbits
    )
    return {
        "character_groups": character_groups,
        "admissible_shapes": admissible_shapes,
        "candidate_orbits": candidate_orbits,
        "unique_nonzero_masks": unique_nonzero_masks,
        "selected_orbits": selected_orbits,
        "selected_masks": selected_masks,
        "selected_union_mask": _mask_union(selected_masks),
        "selected_pairwise_overlap_count": sum(
            (left & right).bit_count()
            for left, right in combinations(selected_masks, 2)
        ),
        "minimum_cover_size": minimum_cover_size,
    }


def _mask_union(masks: tuple[int, ...] | list[int]) -> int:
    result = 0
    for mask in masks:
        result |= mask
    return result


def _nonadjacent_block_shape_cover_program() -> str:
    characters = ", ".join(
        f"{{{first}, {second}}}"
        for first, second in LEVEL25_HEISENBERG_BALANCED_CHARACTERS
    )
    return r'''#include <array>
#include <bit>
#include <cstdint>
#include <iostream>
#include <map>
#include <set>
#include <utility>
#include <vector>

constexpr int MODULUS = 25;
constexpr int PRIME = 5;
constexpr std::array<std::pair<int, int>, 22> CHARACTERS{{@CHARACTERS@}};

using Shape = std::array<int, 5>;

int normalized(int value) {
    value %= MODULUS;
    return value < 0 ? value + MODULUS : value;
}

bool cyclotomic_zero(const std::array<int, 5>& exponents) {
    std::array<int, MODULUS> coefficients{};
    for (int exponent : exponents) ++coefficients[normalized(exponent)];
    for (int degree = 24; degree >= 20; --degree) {
        int coefficient = coefficients[degree];
        if (coefficient == 0) continue;
        int offset = degree - 20;
        for (int cyclotomic_degree : {0, 5, 10, 15, 20}) {
            coefficients[offset + cyclotomic_degree] -= coefficient;
        }
    }
    for (int degree = 0; degree < 20; ++degree) {
        if (coefficients[degree] != 0) return false;
    }
    return true;
}

bool correlation_zero(const Shape& shape, int first, int shift) {
    std::array<int, 5> exponents{};
    for (int quotient = 0; quotient < PRIME; ++quotient) {
        exponents[quotient] = normalized(
            -PRIME * first * quotient
            - shape[quotient]
            + shape[(quotient - shift + PRIME) % PRIME]
        );
    }
    return cyclotomic_zero(exponents);
}

bool orbit_zero(const Shape& shape, int first, int middle_exponent) {
    std::array<int, 5> exponents{};
    for (int quotient = 0; quotient < PRIME; ++quotient) {
        exponents[quotient] = normalized(
            -PRIME * first * quotient
            + middle_exponent * (
                shape[(quotient + 1) % PRIME] - shape[quotient]
            )
        );
    }
    return cyclotomic_zero(exponents);
}

struct Candidate {
    int first;
    int first_exponent;
    int middle_exponent;
    int last_exponent;
    std::uint32_t mask;
};

int main() {
    std::map<std::pair<int, int>, int> group_sizes;
    for (auto [first, second] : CHARACTERS) {
        ++group_sizes[{first % PRIME, (second / PRIME) % PRIME}];
    }

    std::vector<Shape> admissible;
    for (int e1 = 0; e1 < MODULUS; ++e1) {
        for (int e2 = 0; e2 < MODULUS; ++e2) {
            for (int e3 = 0; e3 < MODULUS; ++e3) {
                for (int e4 = 0; e4 < MODULUS; ++e4) {
                    Shape shape{0, e1, e2, e3, e4};
                    bool repeated_groups_vanish = true;
                    int nonzero_singletons = 0;
                    for (auto [pair, size] : group_sizes) {
                        bool zero = correlation_zero(
                            shape, pair.first, pair.second
                        );
                        if (size > 1 && !zero) repeated_groups_vanish = false;
                        if (size == 1 && !zero) ++nonzero_singletons;
                    }
                    if (repeated_groups_vanish && nonzero_singletons <= 1) {
                        admissible.push_back(shape);
                    }
                }
            }
        }
    }

    std::vector<Candidate> candidates;
    for (int first : {2, 4}) {
        for (int first_exponent = 1; first_exponent < 24; ++first_exponent) {
            for (
                int middle_exponent = 1;
                middle_exponent < MODULUS - first_exponent;
                ++middle_exponent
            ) {
                int last_exponent = MODULUS - first_exponent - middle_exponent;
                if (normalized(
                    PRIME * middle_exponent + 2 * last_exponent
                ) != 10) continue;
                std::uint32_t mask = 0;
                for (std::size_t index = 0; index < admissible.size(); ++index) {
                    if (!orbit_zero(admissible[index], first, middle_exponent)) {
                        mask |= std::uint32_t{1} << index;
                    }
                }
                candidates.push_back({
                    first,
                    first_exponent,
                    middle_exponent,
                    last_exponent,
                    mask,
                });
            }
        }
    }

    std::set<std::uint32_t> unique_nonzero_masks;
    int nonzero_candidate_count = 0;
    for (const Candidate& candidate : candidates) {
        if (candidate.mask != 0) {
            ++nonzero_candidate_count;
            unique_nonzero_masks.insert(candidate.mask);
        }
    }
    constexpr std::array<std::array<int, 4>, 4> selected{{
        {{2, 1, 4, 20}},
        {{2, 2, 13, 10}},
        {{2, 4, 6, 15}},
        {{4, 1, 4, 20}},
    }};
    std::array<std::uint32_t, 4> selected_masks{};
    for (std::size_t selected_index = 0; selected_index < selected.size(); ++selected_index) {
        for (const Candidate& candidate : candidates) {
            if (
                candidate.first == selected[selected_index][0]
                && candidate.first_exponent == selected[selected_index][1]
                && candidate.middle_exponent == selected[selected_index][2]
                && candidate.last_exponent == selected[selected_index][3]
            ) selected_masks[selected_index] = candidate.mask;
        }
    }
    std::uint32_t selected_union = 0;
    int selected_pairwise_overlap_count = 0;
    for (std::size_t index = 0; index < selected_masks.size(); ++index) {
        selected_union |= selected_masks[index];
        for (std::size_t other = index + 1; other < selected_masks.size(); ++other) {
            selected_pairwise_overlap_count += std::popcount(
                selected_masks[index] & selected_masks[other]
            );
        }
    }
    std::vector<std::uint32_t> masks(
        unique_nonzero_masks.begin(), unique_nonzero_masks.end()
    );
    std::uint32_t full_mask = (std::uint32_t{1} << admissible.size()) - 1;
    int minimum_cover_size = -1;
    for (std::uint32_t subset = 0; subset < (std::uint32_t{1} << masks.size()); ++subset) {
        std::uint32_t covered = 0;
        for (std::size_t index = 0; index < masks.size(); ++index) {
            if ((subset >> index) & 1U) covered |= masks[index];
        }
        if (covered == full_mask) {
            int size = std::popcount(subset);
            if (minimum_cover_size < 0 || size < minimum_cover_size) {
                minimum_cover_size = size;
            }
        }
    }

    std::cout << "REASONING_LEVEL25_HEISENBERG_NONADJACENT_BLOCK_SHAPE_COVER_V1\n";
    std::cout << "normalized_shape_count=" << 25 * 25 * 25 * 25 << "\n";
    std::cout << "admissible_count=" << admissible.size() << "\n";
    std::cout << "candidate_orbit_count=" << candidates.size() << "\n";
    std::cout << "nonzero_candidate_orbit_count=" << nonzero_candidate_count << "\n";
    std::cout << "zero_candidate_orbit_count="
              << candidates.size() - nonzero_candidate_count << "\n";
    std::cout << "unique_nonzero_mask_count=" << masks.size() << "\n";
    std::cout << "selected_orbit_count=" << selected.size() << "\n";
    std::cout << "selected_union_count=" << std::popcount(selected_union) << "\n";
    std::cout << "selected_pairwise_overlap_count="
              << selected_pairwise_overlap_count << "\n";
    std::cout << "minimum_cover_size=" << minimum_cover_size << "\n";
    for (std::size_t index = 0; index < admissible.size(); ++index) {
        std::cout << "shape_" << index << "=";
        for (std::size_t coordinate = 0; coordinate < admissible[index].size(); ++coordinate) {
            if (coordinate != 0) std::cout << ",";
            std::cout << admissible[index][coordinate];
        }
        std::cout << "\n";
    }
    for (std::size_t index = 0; index < candidates.size(); ++index) {
        const Candidate& candidate = candidates[index];
        std::cout << "candidate_" << index << "="
                  << candidate.first << ","
                  << candidate.first_exponent << ","
                  << candidate.middle_exponent << ","
                  << candidate.last_exponent << ","
                  << candidate.mask << "\n";
    }
    for (std::size_t index = 0; index < selected.size(); ++index) {
        std::cout << "selected_" << index << "="
                  << selected[index][0] << ","
                  << selected[index][1] << ","
                  << selected[index][2] << ","
                  << selected[index][3] << ","
                  << selected_masks[index] << "\n";
    }
    std::cout << "REASONING_END\n";
}
'''.replace("@CHARACTERS@", characters)


def _parse_nonadjacent_block_shape_cover_transcript(
    stdout: str,
) -> dict[str, Any]:
    lines = stdout.strip().splitlines()
    frame = (
        "REASONING_LEVEL25_HEISENBERG_NONADJACENT_BLOCK_SHAPE_COVER_V"
        f"{_BLOCK_SHAPE_COVER_PROTOCOL_VERSION}"
    )
    if len(lines) < 3 or lines[0] != frame or lines[-1] != "REASONING_END":
        raise ScientificToolError(
            "nonadjacent block-shape cover transcript has invalid framing"
        )
    fields: dict[str, str] = {}
    for line in lines[1:-1]:
        if "=" not in line:
            raise ScientificToolError(
                "nonadjacent block-shape cover transcript has an invalid field"
            )
        name, value = line.split("=", 1)
        if name in fields:
            raise ScientificToolError(
                "nonadjacent block-shape cover transcript has duplicate fields"
            )
        fields[name] = value
    scalar_names = {
        "normalized_shape_count",
        "admissible_count",
        "candidate_orbit_count",
        "nonzero_candidate_orbit_count",
        "zero_candidate_orbit_count",
        "unique_nonzero_mask_count",
        "selected_orbit_count",
        "selected_union_count",
        "selected_pairwise_overlap_count",
        "minimum_cover_size",
    }
    try:
        scalars = {name: int(fields[name]) for name in scalar_names}
    except (KeyError, ValueError) as error:
        raise ScientificToolError(
            "nonadjacent block-shape cover transcript has an invalid scalar"
        ) from error
    if any(value < 0 for value in scalars.values()):
        raise ScientificToolError(
            "nonadjacent block-shape cover transcript has a negative scalar"
        )
    expected_names = scalar_names | {
        *(f"shape_{index}" for index in range(scalars["admissible_count"])),
        *(
            f"candidate_{index}"
            for index in range(scalars["candidate_orbit_count"])
        ),
        *(
            f"selected_{index}"
            for index in range(scalars["selected_orbit_count"])
        ),
    }
    if set(fields) != expected_names:
        raise ScientificToolError(
            "nonadjacent block-shape cover transcript is incomplete"
        )

    def integer_row(name: str, length: int) -> tuple[int, ...]:
        try:
            row = tuple(int(value) for value in fields[name].split(","))
        except ValueError as error:
            raise ScientificToolError(
                "nonadjacent block-shape cover transcript has a nonintegral row"
            ) from error
        if len(row) != length or any(value < 0 for value in row):
            raise ScientificToolError(
                "nonadjacent block-shape cover transcript has an invalid row"
            )
        return row

    return {
        **scalars,
        "shapes": tuple(
            integer_row(f"shape_{index}", PRIME)
            for index in range(scalars["admissible_count"])
        ),
        "candidates": tuple(
            integer_row(f"candidate_{index}", 5)
            for index in range(scalars["candidate_orbit_count"])
        ),
        "selected": tuple(
            integer_row(f"selected_{index}", 5)
            for index in range(scalars["selected_orbit_count"])
        ),
    }


def _run_nonadjacent_block_shape_cover_program() -> dict[str, Any]:
    compiler_value = shutil.which("g++")
    if compiler_value is None:
        raise ScientificToolError("g++ is unavailable on the Reasoning service PATH")
    compiler = Path(compiler_value).resolve()
    program = _nonadjacent_block_shape_cover_program()
    environment = {
        "HOME": "",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
    }
    try:
        with tempfile.TemporaryDirectory(
            prefix="reasoning-level25-block-shape-cover-"
        ) as temporary:
            workdir = Path(temporary)
            environment["HOME"] = temporary
            binary = workdir / "block-shape-cover"
            compile_command = sandbox.wrap(
                [
                    str(compiler),
                    "-O3",
                    "-std=c++20",
                    "-x",
                    "c++",
                    "-",
                    "-o",
                    str(binary),
                ],
                rw=[workdir],
                chdir=workdir,
                network=False,
            )
            compiled = subprocess.run(
                compile_command,
                input=program,
                text=True,
                capture_output=True,
                timeout=_BLOCK_SHAPE_COVER_TIMEOUT_SECONDS,
                env=environment,
                check=False,
            )
            if compiled.returncode or compiled.stderr.strip():
                detail = (
                    compiled.stderr.strip()
                    or compiled.stdout.strip()
                    or "unknown compiler failure"
                )[:2000]
                raise ScientificToolError(
                    "nonadjacent block-shape cover program failed to compile: "
                    f"{detail}"
                )
            binary_sha256 = _sha256_file(binary)
            run_command = sandbox.wrap(
                [str(binary)],
                rw=[workdir],
                chdir=workdir,
                network=False,
            )
            completed = subprocess.run(
                run_command,
                text=True,
                capture_output=True,
                timeout=_BLOCK_SHAPE_COVER_TIMEOUT_SECONDS,
                env=environment,
                check=False,
            )
    except subprocess.TimeoutExpired as error:
        raise ScientificToolError(
            "nonadjacent block-shape cover program exceeded the "
            f"{_BLOCK_SHAPE_COVER_TIMEOUT_SECONDS}-second wall timeout"
        ) from error
    except OSError as error:
        raise ScientificToolError(
            f"nonadjacent block-shape cover program could not start: {error}"
        ) from error
    stdout = completed.stdout
    stderr = completed.stderr
    if (
        len(stdout.encode()) > _BLOCK_SHAPE_COVER_MAX_OUTPUT_BYTES
        or len(stderr.encode()) > _BLOCK_SHAPE_COVER_MAX_OUTPUT_BYTES
    ):
        raise ScientificToolError(
            "nonadjacent block-shape cover output exceeds the safe bound"
        )
    if completed.returncode or stderr.strip():
        detail = (stderr.strip() or stdout.strip() or "unknown program failure")[
            :2000
        ]
        raise ScientificToolError(
            f"nonadjacent block-shape cover program failed: {detail}"
        )
    parsed = _parse_nonadjacent_block_shape_cover_transcript(stdout)
    reference = _nonadjacent_block_shape_cover_reference()
    expected_candidates = tuple(
        (*row["orbit"], row["coverage_mask"])
        for row in reference["candidate_orbits"]
    )
    expected_selected = tuple(
        (*orbit, mask)
        for orbit, mask in zip(
            reference["selected_orbits"],
            reference["selected_masks"],
            strict=True,
        )
    )
    expected_scalars = {
        "normalized_shape_count": MODULUS ** (PRIME - 1),
        "admissible_count": len(reference["admissible_shapes"]),
        "candidate_orbit_count": len(reference["candidate_orbits"]),
        "nonzero_candidate_orbit_count": sum(
            row["coverage_mask"] != 0
            for row in reference["candidate_orbits"]
        ),
        "zero_candidate_orbit_count": sum(
            row["coverage_mask"] == 0
            for row in reference["candidate_orbits"]
        ),
        "unique_nonzero_mask_count": len(reference["unique_nonzero_masks"]),
        "selected_orbit_count": len(reference["selected_orbits"]),
        "selected_union_count": reference["selected_union_mask"].bit_count(),
        "selected_pairwise_overlap_count": reference[
            "selected_pairwise_overlap_count"
        ],
        "minimum_cover_size": reference["minimum_cover_size"],
    }
    if (
        any(parsed[name] != value for name, value in expected_scalars.items())
        or parsed["shapes"]
        != tuple(
            row["exponents"] for row in reference["admissible_shapes"]
        )
        or parsed["candidates"] != expected_candidates
        or parsed["selected"] != expected_selected
    ):
        raise ScientificToolError(
            "compiled and Python block-shape cover enumerations disagree"
        )
    try:
        compiler_version = subprocess.run(
            [str(compiler), "--version"],
            text=True,
            capture_output=True,
            timeout=5,
            env=environment,
            check=True,
        ).stdout.splitlines()[0]
    except (OSError, subprocess.SubprocessError, IndexError) as error:
        raise ScientificToolError(
            f"block-shape cover compiler version check failed: {error}"
        ) from error
    return {
        "protocol_version": _BLOCK_SHAPE_COVER_PROTOCOL_VERSION,
        "program_sha256": hashlib.sha256(program.encode()).hexdigest(),
        "transcript_sha256": hashlib.sha256(stdout.encode()).hexdigest(),
        "transcript": stdout.strip(),
        "compiler": str(compiler),
        "compiler_sha256": _sha256_file(compiler),
        "compiler_version": compiler_version,
        "binary_sha256": binary_sha256,
        "independent_python_recomputation_agrees": True,
        **{
            name: parsed[name]
            for name in (
                "normalized_shape_count",
                "admissible_count",
                "candidate_orbit_count",
                "nonzero_candidate_orbit_count",
                "zero_candidate_orbit_count",
                "unique_nonzero_mask_count",
                "selected_orbit_count",
                "selected_union_count",
                "selected_pairwise_overlap_count",
                "minimum_cover_size",
            )
        },
    }


def level25_heisenberg_nonadjacent_block_shape_cover_profile() -> dict[
    str, Any
]:
    """Exhaust root-of-unity shapes and certify a four-orbit cover."""

    reference = _nonadjacent_block_shape_cover_reference()
    computation = _run_nonadjacent_block_shape_cover_program()
    admissible_shapes = reference["admissible_shapes"]
    candidate_orbits = reference["candidate_orbits"]
    candidate_mask_classes = [
        {
            "coverage_mask": mask,
            "coverage_shape_indexes": [
                index
                for index in range(len(admissible_shapes))
                if mask & (1 << index)
            ],
            "equivalent_orbits": [
                list(row["orbit"])
                for row in candidate_orbits
                if row["coverage_mask"] == mask
            ],
        }
        for mask in reference["unique_nonzero_masks"]
    ]
    selected_rows = []
    for orbit, mask in zip(
        reference["selected_orbits"],
        reference["selected_masks"],
        strict=True,
    ):
        first, first_exponent, middle_exponent, last_exponent = orbit
        selected_rows.append(
            {
                "character": [first, 15],
                "exponent_triple": [
                    first_exponent,
                    middle_exponent,
                    last_exponent,
                ],
                "base_monomial": (
                    f"x_0^{first_exponent}*x_5^{middle_exponent}*"
                    f"x_2^{last_exponent}"
                ),
                "orbit": (
                    f"sum_i zeta^(-{first}i)*x_i^{first_exponent}*"
                    f"x_(i+5)^{middle_exponent}*"
                    f"x_(i+2)^{last_exponent}"
                ),
                "coverage_mask": mask,
                "coverage_shape_indexes": [
                    index
                    for index in range(len(admissible_shapes))
                    if mask & (1 << index)
                ],
                "already_present_as_second_pivot": orbit == (2, 2, 13, 10),
            }
        )
    repeated_pairs = []
    singleton_pairs = []
    for pair, first_coordinates in sorted(reference["character_groups"].items()):
        row = {
            "correlation_pair": list(pair),
            "first_coordinates": first_coordinates,
        }
        if len(first_coordinates) > 1:
            repeated_pairs.append(row)
        else:
            singleton_pairs.append(row)
    full_mask = (1 << len(admissible_shapes)) - 1
    certificate = {
        "all_twenty_fifth_root_shapes_normalized_and_exhausted": (
            computation["normalized_shape_count"] == MODULUS ** (PRIME - 1)
        ),
        "compiled_and_python_enumerations_agree": computation[
            "independent_python_recomputation_agrees"
        ],
        "exactly_twenty_admissible_shapes_remain": len(admissible_shapes) == 20,
        "candidate_library_is_exhausted": (
            computation["candidate_orbit_count"] == 18
            and computation["nonzero_candidate_orbit_count"] == 12
            and computation["zero_candidate_orbit_count"] == 6
        ),
        "exactly_four_nonempty_coverage_masks_occur": (
            len(reference["unique_nonzero_masks"]) == 4
        ),
        "selected_orbits_have_pairwise_disjoint_coverage": (
            reference["selected_pairwise_overlap_count"] == 0
        ),
        "selected_orbits_cover_every_admissible_shape": (
            reference["selected_union_mask"] == full_mask
        ),
        "four_orbits_are_minimal_in_the_candidate_library": (
            reference["minimum_cover_size"] == 4
        ),
        "coefficient_one_prevents_cancellation_on_the_census": all(
            sum(bool(mask & (1 << index)) for mask in reference["selected_masks"])
            == 1
            for index in range(len(admissible_shapes))
        ),
    }
    request: dict[str, Any] = {}
    return {
        "schema": (
            "reasoning.level25-heisenberg-nonadjacent-block-shape-cover-"
            "profile.v1"
        ),
        "request": request,
        "request_sha256": hashlib.sha256(
            canonical_json(request).encode()
        ).hexdigest(),
        "ansatz": {
            "active_residue_blocks": [0, 2],
            "normalized_block_shape": (
                "u_q=zeta^e_q with e_0=0 and e_1,...,e_4 in Z/25"
            ),
            "coordinate_rule": (
                "x_(5q)=u_q, x_(2+5q)=lambda*u_q, and all other "
                "coordinates vanish"
            ),
            "normalized_shape_count": MODULUS ** (PRIME - 1),
        },
        "sparse_correlation_classes": {
            "formula": (
                "C_(r,s)(e)=sum_(q=0)^4 "
                "zeta^(-5*r*q-e_q+e_(q-s))"
            ),
            "repeated_classes_forced_to_vanish": repeated_pairs,
            "singleton_classes": singleton_pairs,
            "admissibility_condition": (
                "every repeated correlation class vanishes in Q(zeta_25), "
                "and at most one singleton class is nonzero so the single "
                "block-scale ratio can cancel it"
            ),
        },
        "admissible_shape_count": len(admissible_shapes),
        "admissible_shapes": [
            {
                "index": index,
                "exponents": list(row["exponents"]),
                "nonzero_singleton_correlations": [
                    list(pair)
                    for pair in row["nonzero_singleton_correlations"]
                ],
            }
            for index, row in enumerate(admissible_shapes)
        ],
        "candidate_library": {
            "offsets": [0, 5, 2],
            "characters": [[2, 15], [4, 15]],
            "conditions": (
                "alpha,beta,gamma are positive, alpha+beta+gamma=25, "
                "and 5*beta+2*gamma=10 mod 25"
            ),
            "orbit_value_on_shape": (
                "lambda^gamma*sum_q "
                "zeta^(-5*a*q+beta*(e_(q+1)-e_q))"
            ),
            "orbit_count": len(candidate_orbits),
            "nonzero_on_census_count": sum(
                row["coverage_mask"] != 0 for row in candidate_orbits
            ),
            "identically_zero_on_census_count": sum(
                row["coverage_mask"] == 0 for row in candidate_orbits
            ),
            "distinct_nonempty_coverage_masks": candidate_mask_classes,
        },
        "selected_coefficient_one_orbits": selected_rows,
        "cover": {
            "covered_shape_count": reference["selected_union_mask"].bit_count(),
            "uncovered_shape_indexes": [
                index
                for index in range(len(admissible_shapes))
                if not reference["selected_union_mask"] & (1 << index)
            ],
            "pairwise_overlap_count": reference[
                "selected_pairwise_overlap_count"
            ],
            "minimum_candidate_library_cover_size": reference[
                "minimum_cover_size"
            ],
        },
        "computation": computation,
        "certificate": certificate,
        "certificate_verified": all(certificate.values()),
        "interpretation": (
            "Adding these four coefficient-one nonadjacent orbits, including "
            "the existing second pivot, excludes every normalized common-zero "
            "in the same-shape twenty-fifth-root two-block ansatz: exactly one "
            "selected orbit is nonzero at each of the twenty remaining shapes."
        ),
        "not_certified": [
            "exclusion of two-block points with unequal or non-torsion block shapes",
            "the fixed-P4 and cyclic Jacobian gates for the five-pivot tuple",
            "global dimension, smoothness, or integrality",
            "a free action on a smooth surface",
            "a Brauer class, period, index bound, or counterexample",
        ],
        "next_gate": {
            "feature": "level25_heisenberg_balanced_five_pivot_restricted_system",
            "requirement": (
                "Add all four selected nonadjacent orbits with coefficient one "
                "to the first adjacent pivot, then replay every fixed-P4 and "
                "forced cyclic-eigenpoint Jacobian gate."
            ),
        },
    }


def _cyclic_eigenpoint(
    generator: tuple[int, int],
    eigenvalue_index: int,
) -> list[int]:
    shift, clock = generator
    eigenvalue = _finite_field_power(eigenvalue_index)
    if shift == 1:
        return [
            (
                pow(eigenvalue, index, FINITE_FIELD_PRIME)
                * _finite_field_power(clock * index * (index - 1) // 2)
            )
            % FINITE_FIELD_PRIME
            for index in range(MODULUS)
        ]
    if clock != 1 or shift % PRIME != 0:
        raise ValueError("unsupported normalized cyclic generator")
    if shift == 0:
        point = [0] * MODULUS
        point[(-eigenvalue_index) % MODULUS] = 1
        return point
    residue = (-eigenvalue_index) % PRIME
    point = [0] * MODULUS
    point[residue] = 1
    index = residue
    for _ in range(1, PRIME):
        next_index = (index + shift) % MODULUS
        point[next_index] = (
            eigenvalue * _finite_field_power(index) * point[index]
        ) % FINITE_FIELD_PRIME
        index = next_index
    return point


def _cyclic_eigenpoint_equation_holds(
    generator: tuple[int, int],
    eigenvalue_index: int,
    point: list[int],
) -> bool:
    shift, clock = generator
    eigenvalue = _finite_field_power(eigenvalue_index)
    return all(
        (
            _finite_field_power(-clock * index)
            * point[(index + shift) % MODULUS]
        )
        % FINITE_FIELD_PRIME
        == eigenvalue * point[index] % FINITE_FIELD_PRIME
        for index in range(MODULUS)
    )


def _form_value_and_gradient(
    character: tuple[int, int],
    point: list[int],
) -> tuple[int, list[int]]:
    a, b = character
    value = sum(
        _finite_field_power(-a * index)
        * pow(point[index], MODULUS - 1, FINITE_FIELD_PRIME)
        * point[(index - b) % MODULUS]
        for index in range(MODULUS)
    ) % FINITE_FIELD_PRIME
    gradient = []
    for coordinate in range(MODULUS):
        first = (
            (MODULUS - 1)
            * _finite_field_power(-a * coordinate)
            * pow(point[coordinate], MODULUS - 2, FINITE_FIELD_PRIME)
            * point[(coordinate - b) % MODULUS]
        )
        source = (coordinate + b) % MODULUS
        second = _finite_field_power(-a * source) * pow(
            point[source],
            MODULUS - 1,
            FINITE_FIELD_PRIME,
        )
        gradient.append((first + second) % FINITE_FIELD_PRIME)
    return value, gradient


def _pivoted_form_value_and_gradient(
    character: tuple[int, int],
    point: list[int],
) -> tuple[int, list[int]]:
    """Evaluate the coefficient-one pivoted balanced generator and gradient."""

    value, gradient = _form_value_and_gradient(character, point)
    if character != (5, 20):
        return value, gradient
    pivot_value = sum(
        _finite_field_power(-5 * index)
        * pow(point[index], 20, FINITE_FIELD_PRIME)
        * pow(point[(index + 1) % MODULUS], 5, FINITE_FIELD_PRIME)
        for index in range(MODULUS)
    )
    for coordinate in range(MODULUS):
        next_coordinate = (coordinate + 1) % MODULUS
        previous_coordinate = (coordinate - 1) % MODULUS
        gradient[coordinate] += (
            20
            * _finite_field_power(-5 * coordinate)
            * pow(point[coordinate], 19, FINITE_FIELD_PRIME)
            * pow(point[next_coordinate], 5, FINITE_FIELD_PRIME)
            + 5
            * _finite_field_power(-5 * previous_coordinate)
            * pow(point[previous_coordinate], 20, FINITE_FIELD_PRIME)
            * pow(point[coordinate], 4, FINITE_FIELD_PRIME)
        )
    return (value + pivot_value) % FINITE_FIELD_PRIME, [
        entry % FINITE_FIELD_PRIME for entry in gradient
    ]


def _two_pivoted_form_value_and_gradient(
    character: tuple[int, int],
    point: list[int],
) -> tuple[int, list[int]]:
    """Evaluate the coefficient-one two-pivot generator tuple and gradient."""

    value, gradient = _pivoted_form_value_and_gradient(character, point)
    if character != (2, 15):
        return value, gradient
    pivot_value = sum(
        _finite_field_power(-2 * index)
        * pow(point[index], 2, FINITE_FIELD_PRIME)
        * pow(point[(index + 5) % MODULUS], 13, FINITE_FIELD_PRIME)
        * pow(point[(index + 2) % MODULUS], 10, FINITE_FIELD_PRIME)
        for index in range(MODULUS)
    )
    for coordinate in range(MODULUS):
        gradient[coordinate] += (
            2
            * _finite_field_power(-2 * coordinate)
            * point[coordinate]
            * pow(point[(coordinate + 5) % MODULUS], 13, FINITE_FIELD_PRIME)
            * pow(point[(coordinate + 2) % MODULUS], 10, FINITE_FIELD_PRIME)
            + 13
            * _finite_field_power(-2 * (coordinate - 5))
            * pow(point[(coordinate - 5) % MODULUS], 2, FINITE_FIELD_PRIME)
            * pow(point[coordinate], 12, FINITE_FIELD_PRIME)
            * pow(point[(coordinate - 3) % MODULUS], 10, FINITE_FIELD_PRIME)
            + 10
            * _finite_field_power(-2 * (coordinate - 2))
            * pow(point[(coordinate - 2) % MODULUS], 2, FINITE_FIELD_PRIME)
            * pow(point[(coordinate + 3) % MODULUS], 13, FINITE_FIELD_PRIME)
            * pow(point[coordinate], 9, FINITE_FIELD_PRIME)
        )
    return (value + pivot_value) % FINITE_FIELD_PRIME, [
        entry % FINITE_FIELD_PRIME for entry in gradient
    ]


def _five_pivoted_form_value_and_gradient(
    character: tuple[int, int],
    point: list[int],
) -> tuple[int, list[int]]:
    """Evaluate the adjacent pivot plus four nonadjacent orbit sums."""

    value, gradient = _pivoted_form_value_and_gradient(character, point)
    for first_character, alpha, beta, gamma in (
        _LEVEL25_HEISENBERG_NONADJACENT_PIVOTS
    ):
        if character != (first_character, 15):
            continue
        powers = (alpha, beta, gamma)
        for index in range(MODULUS):
            coordinates = (
                index,
                (index + 5) % MODULUS,
                (index + 2) % MODULUS,
            )
            coefficient = _finite_field_power(-first_character * index)
            term = coefficient
            for coordinate, power in zip(coordinates, powers, strict=True):
                term = (
                    term
                    * pow(point[coordinate], power, FINITE_FIELD_PRIME)
                    % FINITE_FIELD_PRIME
                )
            value = (value + term) % FINITE_FIELD_PRIME
            for slot, (coordinate, power) in enumerate(
                zip(coordinates, powers, strict=True)
            ):
                derivative = coefficient * power
                for other_slot, (other_coordinate, other_power) in enumerate(
                    zip(coordinates, powers, strict=True)
                ):
                    derivative = (
                        derivative
                        * pow(
                            point[other_coordinate],
                            other_power - int(other_slot == slot),
                            FINITE_FIELD_PRIME,
                        )
                        % FINITE_FIELD_PRIME
                    )
                gradient[coordinate] = (
                    gradient[coordinate] + derivative
                ) % FINITE_FIELD_PRIME
    return value, [entry % FINITE_FIELD_PRIME for entry in gradient]


def _projective_stabilizer(point: list[int]) -> set[tuple[int, int]]:
    stabilizer = set()
    for shift in range(MODULUS):
        for clock in range(MODULUS):
            ratios = {
                (
                    _finite_field_power(-clock * index)
                    * point[(index + shift) % MODULUS]
                    * pow(point[index], -1, FINITE_FIELD_PRIME)
                )
                % FINITE_FIELD_PRIME
                for index in range(MODULUS)
            }
            if len(ratios) == 1:
                stabilizer.add((shift, clock))
    return stabilizer


def _unmarked_cyclic_quotient_parameter(weights: list[int]) -> int:
    ratio = weights[1] * pow(weights[0], -1, MODULUS) % MODULUS
    return min(ratio, pow(ratio, -1, MODULUS))


def _forced_h0_eigenpoint_profile(
    characters: tuple[tuple[int, int], ...] = LEVEL25_HEISENBERG_CHARACTERS,
    *,
    pivoted: bool = False,
    two_pivoted: bool = False,
    five_pivoted: bool = False,
) -> list[dict[str, Any]]:
    value_and_gradient = (
        _five_pivoted_form_value_and_gradient
        if five_pivoted
        else _two_pivoted_form_value_and_gradient
        if two_pivoted
        else _pivoted_form_value_and_gradient
        if pivoted
        else _form_value_and_gradient
    )
    rows = []
    for lift_index in range(PRIME):
        ranks: dict[int, int] = {}
        stabilizer_orders: dict[int, int] = {}
        unexpected_stabilizers = 0
        nonzero_values = 0
        generator = (1, PRIME * lift_index)
        expected_stabilizer = {
            (
                multiple % MODULUS,
                PRIME * lift_index * multiple % MODULUS,
            )
            for multiple in range(MODULUS)
        }
        for eigenvalue_index in range(MODULUS):
            point = _cyclic_eigenpoint(generator, eigenvalue_index)
            jacobian = []
            for character in characters:
                value, gradient = value_and_gradient(character, point)
                nonzero_values += int(value != 0)
                jacobian.append(gradient)
            rank = _modular_rank(jacobian)
            ranks[rank] = ranks.get(rank, 0) + 1
            stabilizer = _projective_stabilizer(point)
            stabilizer_orders[len(stabilizer)] = (
                stabilizer_orders.get(len(stabilizer), 0) + 1
            )
            unexpected_stabilizers += int(stabilizer != expected_stabilizer)
        selected_weights = {
            _evaluate(character, generator)
            for character in characters
        }
        tangent_weights = sorted(set(range(1, MODULUS)) - selected_weights)
        quotient_parameter = _unmarked_cyclic_quotient_parameter(
            tangent_weights
        )
        rows.append(
            {
                "lift_index": lift_index,
                "generator": list(generator),
                "eigenpoint_count": MODULUS,
                "nonzero_equation_value_count": nonzero_values,
                "jacobian_rank_distribution": {
                    str(rank): count for rank, count in sorted(ranks.items())
                },
                "projective_stabilizer_order_distribution": {
                    str(order): count
                    for order, count in sorted(stabilizer_orders.items())
                },
                "unexpected_projective_stabilizer_count": (
                    unexpected_stabilizers
                ),
                "marked_generator_tangent_weights": tangent_weights,
                "inverse_action_local_coordinate_weights": [
                    (-weight) % MODULUS for weight in tangent_weights
                ],
                "unmarked_cyclic_quotient_type": (
                    f"1/{MODULUS}(1,{quotient_parameter})"
                ),
            }
        )
    return rows


def _all_cyclic_eigenpoint_evaluation_profile(
    characters: tuple[tuple[int, int], ...] = LEVEL25_HEISENBERG_CHARACTERS,
    *,
    pivoted: bool = False,
    two_pivoted: bool = False,
    five_pivoted: bool = False,
) -> list[dict[str, Any]]:
    value_and_gradient = (
        _five_pivoted_form_value_and_gradient
        if five_pivoted
        else _two_pivoted_form_value_and_gradient
        if two_pivoted
        else _pivoted_form_value_and_gradient
        if pivoted
        else _form_value_and_gradient
    )
    rows = []
    for subgroup in _order_five_subgroups():
        for lift_index, generator in enumerate(subgroup["cyclic_lifts"]):
            nonzero_count_distribution: dict[int, int] = {}
            common_zero_count = 0
            invalid_eigenpoint_count = 0
            for eigenvalue_index in range(MODULUS):
                point = _cyclic_eigenpoint(generator, eigenvalue_index)
                invalid_eigenpoint_count += int(
                    not _cyclic_eigenpoint_equation_holds(
                        generator,
                        eigenvalue_index,
                        point,
                    )
                )
                nonzero_count = sum(
                    value_and_gradient(character, point)[0] != 0
                    for character in characters
                )
                nonzero_count_distribution[nonzero_count] = (
                    nonzero_count_distribution.get(nonzero_count, 0) + 1
                )
                common_zero_count += int(nonzero_count == 0)
            rows.append(
                {
                    "subgroup_label": subgroup["label"],
                    "lift_index": lift_index,
                    "generator": list(generator),
                    "eigenpoint_count": MODULUS,
                    "common_zero_count": common_zero_count,
                    "invalid_eigenpoint_count": invalid_eigenpoint_count,
                    "nonzero_equation_count_distribution": {
                        str(count): multiplicity
                        for count, multiplicity in sorted(
                            nonzero_count_distribution.items()
                        )
                    },
                }
            )
    return rows


def level25_heisenberg_surface_character_lift_profile() -> dict[str, Any]:
    """Certify one global character-level cyclic-lift witness.

    The receipt treats the indexed characters as the characters of twenty-two
    indexed equations, one equation per character.  It proves only finite
    character and forced-eigenpoint necessities.  It does not construct the
    semi-invariant forms or certify their projective base loci, gradients,
    regular sequence, or smoothness.
    """

    characters = LEVEL25_HEISENBERG_CHARACTERS
    zero_reduction_indexes = [
        index
        for index, (first, second) in enumerate(characters)
        if first % PRIME == 0 and second % PRIME == 0
    ]
    subgroup_rows = []
    for subgroup in _order_five_subgroups():
        effective_indexes = [
            index
            for index, character in enumerate(characters)
            if _evaluate(character, subgroup["generator"]) == 0
        ]
        lift_rows = []
        for lift_index, generator in enumerate(subgroup["cyclic_lifts"]):
            exponents = [
                _evaluate(character, generator) for character in characters
            ]
            annihilator_indexes = [
                index for index, exponent in enumerate(exponents) if exponent == 0
            ]
            forced = not annihilator_indexes
            distinct_nonzero = (
                len(set(exponents)) == len(exponents) and 0 not in exponents
            )
            lift_rows.append(
                {
                    "lift_index": lift_index,
                    "generator": list(generator),
                    "restriction_exponents": exponents,
                    "annihilator_character_indexes": annihilator_indexes,
                    "forced_eigenpoints": forced,
                    "forced_eigenpoint_count": MODULUS if forced else 0,
                    "character_jacobian_rank_permitted_if_forced": (
                        distinct_nonzero if forced else None
                    ),
                    "passes_forced_eigenpoint_gate": (
                        not forced or distinct_nonzero
                    ),
                }
            )
        effective_count = len(effective_indexes)
        subgroup_rows.append(
            {
                "label": subgroup["label"],
                "generator": list(subgroup["generator"]),
                "effective_character_count": effective_count,
                "effective_character_indexes": effective_indexes,
                "projective_p4_gate": (
                    "positive-dimensional fixed locus forced"
                    if effective_count <= 3
                    else "nonempty; finite only when the four restrictions cut properly"
                    if effective_count == 4
                    else "empty only after restricted-system basepoint certification"
                ),
                "cyclic_lifts": lift_rows,
                "all_cyclic_lifts_pass_forced_eigenpoint_gate": all(
                    row["passes_forced_eigenpoint_gate"] for row in lift_rows
                ),
            }
        )

    effective_counts = [
        row["effective_character_count"] for row in subgroup_rows
    ]
    expected_sum = len(characters) + (PRIME * len(zero_reduction_indexes))
    request: dict[str, Any] = {}
    certificate = {
        "twenty_two_characters_listed": len(characters) == 22,
        "characters_are_distinct": len(set(characters)) == len(characters),
        "zero_reduction_character_count_is_four": (
            len(zero_reduction_indexes) == 4
        ),
        "six_order_five_subgroups_enumerated": len(subgroup_rows) == 6,
        "five_cyclic_order_twenty_five_lifts_per_subgroup": all(
            len(row["cyclic_lifts"]) == 5 for row in subgroup_rows
        ),
        "effective_count_identity_verified": sum(effective_counts)
        == expected_sum,
        "effective_count_census_is_4_8_8_9_4_9": effective_counts
        == [4, 8, 8, 9, 4, 9],
        "no_positive_dimensional_fixed_locus_forced_by_count": all(
            count >= 4 for count in effective_counts
        ),
        "all_forced_order_twenty_five_lifts_have_distinct_nonzero_weights": all(
            row["all_cyclic_lifts_pass_forced_eigenpoint_gate"]
            for row in subgroup_rows
        ),
        "h0_all_five_lifts_are_forced_and_weight_permitted": all(
            lift["forced_eigenpoints"]
            and lift["character_jacobian_rank_permitted_if_forced"]
            for lift in subgroup_rows[0]["cyclic_lifts"]
        ),
        "every_non_h0_lift_has_an_annihilating_character": all(
            not lift["forced_eigenpoints"]
            for row in subgroup_rows[1:]
            for lift in row["cyclic_lifts"]
        ),
    }
    return {
        "schema": (
            "reasoning.level25-heisenberg-surface-character-lift-profile.v1"
        ),
        "request": request,
        "request_sha256": hashlib.sha256(
            canonical_json(request).encode()
        ).hexdigest(),
        "group": {
            "invariant_factors": [MODULUS, MODULUS],
            "character_pairing": "chi_(a,b)(x,y) = xi^(a*x+b*y)",
        },
        "surface_presentation": {
            "ambient_projective_dimension": 24,
            "equation_count": len(characters),
            "equation_character_pairing": (
                "one indexed equation for each indexed character"
            ),
            "equation_degree": MODULUS,
            "expected_complete_intersection_dimension": 2,
        },
        "characters": [list(character) for character in characters],
        "zero_reduction_character_indexes": zero_reduction_indexes,
        "zero_reduction_character_count": len(zero_reduction_indexes),
        "effective_count_sum": sum(effective_counts),
        "effective_count_sum_formula": expected_sum,
        "order_five_subgroups": subgroup_rows,
        "cyclic_lift_census": {
            "lift_count": 30,
            "eigenpoints_per_lift": MODULUS,
            "eigenpoint_incidences": 30 * MODULUS,
            "compression": (
                "All 25 eigenpoints of a fixed cyclic lift have the same "
                "equation-character restriction test; actual gradient values "
                "remain point-dependent and are not computed."
            ),
        },
        "native_z2_census_rejection": {
            "claimed_effective_counts": [4, 5, 5, 5, 5, 5],
            "claimed_sum": 29,
            "required_sum_when_z_is_two": 32,
            "rejected": True,
        },
        "conditional_z3_h0_forced_lift_obstruction": {
            "hypotheses": (
                "exactly four indexed equations have characters trivial on "
                "H_0; three of those characters are scaled with distinct "
                "nonzero restrictions on the five lifts, and the fourth is "
                "unscaled"
            ),
            "scaled_fixed_nonzero_residues": [1, 2, 3],
            "primitive_residues_across_five_lifts": [0, 1, 2, 3, 4],
            "primitive_annihilated_lift_count": 1,
            "uncovered_lifts_with_missing_fourth_residue": 1,
            "uncovered_lifts_with_weight_collision": 3,
            "passes_global_forced_lift_gate": False,
        },
        "certificate": certificate,
        "certificate_verified": all(certificate.values()),
        "status": "character-level feasible witness",
        "weight_convention": {
            "recorded": (
                "restriction exponents for each displayed marked cyclic-lift "
                "generator"
            ),
            "not_recorded": (
                "inverse-generator tangent weights or an inverse-normalized "
                "unmarked cyclic quotient type"
            ),
        },
        "not_certified": [
            "existence of degree-25 semi-invariant forms with the required simultaneous restrictions",
            "basepoint-freeness on the thirty order-five fixed P4 eigenspaces",
            "nonvanishing of the twenty-two descended gradients at forced eigenpoints",
            "regular-sequence, smoothness, and integrality of the proposed surface",
            "an invariant integral H2 lattice or transgression",
            "a Brauer class, period, index bound, or counterexample",
        ],
        "next_gate": {
            "feature": (
                "level25_heisenberg_surface_restricted_system_profile"
            ),
            "requirement": (
                "Construct exact character-isotypic degree-25 restriction "
                "matrices on all thirty P4 eigenspaces, certify their common "
                "base loci, and evaluate the twenty-two gradient coordinates "
                "at every forced cyclic eigenpoint."
            ),
        },
    }


def _level25_heisenberg_surface_restricted_system_profile(
    characters: tuple[tuple[int, int], ...],
    *,
    balanced: bool,
    pivoted: bool = False,
    two_pivoted: bool = False,
    five_pivoted: bool = False,
) -> dict[str, Any]:

    if (pivoted or two_pivoted or five_pivoted) and not balanced:
        raise ValueError("the coefficient-one pivot requires balanced characters")
    if two_pivoted and not pivoted:
        raise ValueError("the two-pivot tuple includes the first pivot")
    if five_pivoted and not pivoted:
        raise ValueError("the five-pivot tuple includes the first pivot")
    if two_pivoted and five_pivoted:
        raise ValueError("select either the two-pivot or five-pivot tuple")
    systems = _restricted_systems(
        characters,
        pivoted=pivoted,
        two_pivoted=two_pivoted,
        five_pivoted=five_pivoted,
    )
    singular = _run_restricted_system_singular(
        characters,
        pivoted=pivoted,
        two_pivoted=two_pivoted,
        five_pivoted=five_pivoted,
    )
    singular_fields = singular.pop("fields")
    system_rows = []
    for system in systems:
        label = f"system_{system['subgroup_index']}_0"
        dimension = singular_fields[f"{label}_dim"]
        basis_size = singular_fields[f"{label}_basis_size"]
        effective_forms = [
            system["forms"][index]
            for index in system["effective_character_indexes"]
        ]
        sparse_forms = [
            _sparse_polynomial(polynomial) for polynomial in effective_forms
        ]
        singular_dimension = singular_fields.get(f"{label}_singular_dim")
        singular_basis_size = singular_fields.get(
            f"{label}_singular_basis_size"
        )
        projective_degree = singular_fields.get(f"{label}_projective_degree")
        system_rows.append(
            {
                "subgroup_index": system["subgroup_index"],
                "subgroup_label": system["subgroup_label"],
                "eigenspace_index": system["eigenspace_index"],
                "eigenvalue_in_F101": system["eigenvalue"],
                "effective_character_indexes": system[
                    "effective_character_indexes"
                ],
                "restricted_form_count": len(effective_forms),
                "restricted_form_term_counts": [
                    len(polynomial) for polynomial in effective_forms
                ],
                "restricted_system_sha256": hashlib.sha256(
                    canonical_json(sparse_forms).encode()
                ).hexdigest(),
                "affine_cone_krull_dimension": dimension,
                "standard_basis_size": basis_size,
                "projective_locus": (
                    "empty"
                    if dimension == 0
                    else "zero-dimensional"
                    if dimension == 1
                    else "positive-dimensional"
                ),
                "projective_degree": projective_degree,
                "projective_singular_locus": (
                    None
                    if singular_dimension is None
                    else "empty"
                    if singular_dimension == 0
                    else "nonempty"
                ),
                "singular_affine_cone_krull_dimension": singular_dimension,
                "singular_standard_basis_size": singular_basis_size,
            }
        )

    forced_rows = _forced_h0_eigenpoint_profile(
        characters,
        pivoted=pivoted,
        two_pivoted=two_pivoted,
        five_pivoted=five_pivoted,
    )
    cyclic_eigenpoint_rows = _all_cyclic_eigenpoint_evaluation_profile(
        characters,
        pivoted=pivoted,
        two_pivoted=two_pivoted,
        five_pivoted=five_pivoted,
    )
    transitivity_rows = [
        {
            "subgroup_label": subgroup["label"],
            "direction_mod_five": (
                [1, subgroup_index]
                if subgroup_index < PRIME
                else [0, 1]
            ),
            "translating_direction_mod_five": (
                [0, 1] if subgroup_index < PRIME else [1, 0]
            ),
            "symplectic_determinant_mod_five": (
                1 if subgroup_index < PRIME else PRIME - 1
            ),
        }
        for subgroup_index, subgroup in enumerate(_order_five_subgroups())
    ]
    ambient_form_recipe = [
        {
            "character": list(character),
            "terms": [
                {
                    "coefficient": _finite_field_power(-character[0] * index),
                    "powered_coordinate": index,
                    "powered_exponent": MODULUS - 1,
                    "linear_coordinate": (index - character[1]) % MODULUS,
                }
                for index in range(MODULUS)
            ],
        }
        for character in characters
    ]
    if pivoted:
        target_recipe = next(
            form
            for form in ambient_form_recipe
            if form["character"] == [5, 20]
        )
        target_recipe["terms"].extend(
            {
                "coefficient": _finite_field_power(-5 * index),
                "powered_coordinate": index,
                "powered_exponent": 20,
                "second_powered_coordinate": (index + 1) % MODULUS,
                "second_powered_exponent": 5,
            }
            for index in range(MODULUS)
        )
    if two_pivoted:
        second_target_recipe = next(
            form
            for form in ambient_form_recipe
            if form["character"] == [2, 15]
        )
        second_target_recipe["terms"].extend(
            {
                "coefficient": _finite_field_power(-2 * index),
                "powered_coordinate": index,
                "powered_exponent": 2,
                "second_powered_coordinate": (index + 5) % MODULUS,
                "second_powered_exponent": 13,
                "third_powered_coordinate": (index + 2) % MODULUS,
                "third_powered_exponent": 10,
            }
            for index in range(MODULUS)
        )
    if five_pivoted:
        for first_character, alpha, beta, gamma in (
            _LEVEL25_HEISENBERG_NONADJACENT_PIVOTS
        ):
            nonadjacent_target_recipe = next(
                form
                for form in ambient_form_recipe
                if form["character"] == [first_character, 15]
            )
            nonadjacent_target_recipe["terms"].extend(
                {
                    "coefficient": _finite_field_power(
                        -first_character * index
                    ),
                    "powered_coordinate": index,
                    "powered_exponent": alpha,
                    "second_powered_coordinate": (
                        index + 5
                    ) % MODULUS,
                    "second_powered_exponent": beta,
                    "third_powered_coordinate": (
                        index + 2
                    ) % MODULUS,
                    "third_powered_exponent": gamma,
                }
                for index in range(MODULUS)
            )
    dimension_census = [
        [
            row["affine_cone_krull_dimension"]
            for row in system_rows
            if row["subgroup_index"] == subgroup_index
        ]
        for subgroup_index in range(PRIME + 1)
    ]
    expected_dimension_census = (
        [[1] * PRIME] * PRIME + [[0] * PRIME]
        if balanced
        else [
            [1] * PRIME,
            [0] * PRIME,
            [0] * PRIME,
            [0] * PRIME,
            [1] * PRIME,
            [0] * PRIME,
        ]
    )
    expected_effective_counts = (
        [4, 4, 4, 4, 4, 22]
        if balanced
        else [4, 8, 8, 9, 4, 9]
    )
    expected_four_form_rows = 25 if balanced else 10
    expected_empty_rows = 5 if balanced else 20
    four_form_rows = [
        row for row in system_rows if row["restricted_form_count"] == 4
    ]
    coordinate_support_screen = (
        _balanced_coordinate_support_screen() if balanced else None
    )
    pivoted_term_counts_verified = all(
        row["restricted_form_term_counts"]
        == (
            [10, 5, 5, 5]
            if row["subgroup_index"] < PRIME
            else [5] * len(characters)
        )
        for row in system_rows
    )
    two_pivot_restricted_term_counts = [
        row["restricted_form_term_counts"] for row in system_rows
    ]
    second_pivot_fixed_restrictions_vanish = systems == _restricted_systems(
        characters,
        pivoted=True,
    ) if two_pivoted else None
    five_pivot_fixed_restrictions_vanish = systems == _restricted_systems(
        characters,
        pivoted=True,
    ) if five_pivoted else None
    certificate = {
        "root_has_exact_order_twenty_five": (
            pow(ROOT_OF_UNITY, MODULUS, FINITE_FIELD_PRIME) == 1
            and pow(ROOT_OF_UNITY, PRIME, FINITE_FIELD_PRIME) != 1
        ),
        **(
            {
                "twenty_two_pivoted_forms_constructed": (
                    len(ambient_form_recipe) == 22
                    and sum(
                        len(form["terms"]) for form in ambient_form_recipe
                    )
                    == 575
                ),
                "pivoted_character_5_20_has_fifty_terms": (
                    len(target_recipe["terms"]) == 50
                ),
                "pivot_orbit_character_action_verified": all(
                    (
                        _finite_field_power(-5 * (index - 1))
                        == _finite_field_power(5)
                        * _finite_field_power(-5 * index)
                        % FINITE_FIELD_PRIME
                        and _finite_field_power(20)
                        == _finite_field_power(
                            -20 * index - 5 * (index + 1)
                        )
                    )
                    for index in range(MODULUS)
                ),
            }
            if pivoted and not two_pivoted and not five_pivoted
            else {}
            if two_pivoted or five_pivoted
            else {
                "twenty_two_sparse_forms_constructed": (
                    len(ambient_form_recipe) == 22
                ),
                "twenty_five_terms_per_ambient_form": all(
                    len(form["terms"]) == MODULUS
                    for form in ambient_form_recipe
                ),
            }
        ),
        **(
            {
                "twenty_two_two_pivot_forms_constructed": (
                    len(ambient_form_recipe) == 22
                    and sum(
                        len(form["terms"]) for form in ambient_form_recipe
                    )
                    == 600
                ),
                "second_pivot_character_2_15_has_fifty_terms": (
                    len(second_target_recipe["terms"]) == 50
                ),
                "first_pivot_character_5_20_has_fifty_terms": (
                    len(target_recipe["terms"]) == 50
                ),
                "first_pivot_orbit_character_action_verified": all(
                    (
                        _finite_field_power(-5 * (index - 1))
                        == _finite_field_power(5)
                        * _finite_field_power(-5 * index)
                        % FINITE_FIELD_PRIME
                        and _finite_field_power(20)
                        == _finite_field_power(
                            -20 * index - 5 * (index + 1)
                        )
                    )
                    for index in range(MODULUS)
                ),
                "second_pivot_orbit_character_action_verified": all(
                    (
                        _finite_field_power(-2 * (index - 1))
                        == _finite_field_power(2)
                        * _finite_field_power(-2 * index)
                        % FINITE_FIELD_PRIME
                        and _finite_field_power(15)
                        == _finite_field_power(
                            -2 * index
                            - 13 * (index + 5)
                            - 10 * (index + 2)
                        )
                    )
                    for index in range(MODULUS)
                ),
            }
            if two_pivoted
            else {}
        ),
        **(
            {
                "twenty_two_five_pivot_forms_constructed": (
                    len(ambient_form_recipe) == 22
                    and sum(
                        len(form["terms"]) for form in ambient_form_recipe
                    )
                    == 675
                ),
                "first_pivot_character_5_20_has_fifty_terms": (
                    len(target_recipe["terms"]) == 50
                ),
                "three_nonadjacent_pivots_give_character_2_15_one_hundred_terms": (
                    len(
                        next(
                            form
                            for form in ambient_form_recipe
                            if form["character"] == [2, 15]
                        )["terms"]
                    )
                    == 100
                ),
                "one_nonadjacent_pivot_gives_character_4_15_fifty_terms": (
                    len(
                        next(
                            form
                            for form in ambient_form_recipe
                            if form["character"] == [4, 15]
                        )["terms"]
                    )
                    == 50
                ),
                "first_pivot_orbit_character_action_verified": all(
                    (
                        _finite_field_power(-5 * (index - 1))
                        == _finite_field_power(5)
                        * _finite_field_power(-5 * index)
                        % FINITE_FIELD_PRIME
                        and _finite_field_power(20)
                        == _finite_field_power(
                            -20 * index - 5 * (index + 1)
                        )
                    )
                    for index in range(MODULUS)
                ),
                "four_nonadjacent_pivot_orbit_character_actions_verified": all(
                    _finite_field_power(-first_character * (index - 1))
                    == _finite_field_power(first_character)
                    * _finite_field_power(-first_character * index)
                    % FINITE_FIELD_PRIME
                    and _finite_field_power(15)
                    == _finite_field_power(
                        -alpha * index
                        - beta * (index + 5)
                        - gamma * (index + 2)
                    )
                    for first_character, alpha, beta, gamma in (
                        _LEVEL25_HEISENBERG_NONADJACENT_PIVOTS
                    )
                    for index in range(MODULUS)
                ),
            }
            if five_pivoted
            else {}
        ),
        "ambient_form_character_action_verified": all(
            (
                _finite_field_power(-a * (index - 1))
                == _finite_field_power(a)
                * _finite_field_power(-a * index)
                % FINITE_FIELD_PRIME
                and _finite_field_power(b)
                == _finite_field_power(b - MODULUS * index)
            )
            for a, b in characters
            for index in range(MODULUS)
        ),
        "thirty_fixed_p4_systems_checked": len(system_rows) == 30,
        "six_representative_fixed_p4_systems_computed_in_singular": (
            len({row["subgroup_index"] for row in system_rows}) == 6
        ),
        "heisenberg_action_transitive_on_each_five_eigenspace_family": all(
            row["symplectic_determinant_mod_five"] != 0
            for row in transitivity_rows
        ),
        "effective_character_counts_match_on_every_eigenspace": all(
            row["restricted_form_count"]
            == expected_effective_counts[row["subgroup_index"]]
            for row in system_rows
        ),
        **(
            {
                "pivoted_restricted_term_counts_are_10_5_5_5_or_all_five": (
                    pivoted_term_counts_verified
                )
            }
            if pivoted and not two_pivoted and not five_pivoted
            else {}
            if two_pivoted or five_pivoted
            else {
                "five_terms_per_nonzero_restricted_form": all(
                    term_count == PRIME
                    for row in system_rows
                    for term_count in row["restricted_form_term_counts"]
                )
            }
        ),
        **(
            {
                "two_pivot_restricted_term_counts_recorded": (
                    two_pivot_restricted_term_counts
                    == [
                        [10, 5, 5, 5],
                    ]
                    * 25
                    + [[5] * len(characters)] * 5
                ),
                "second_pivot_restricts_to_zero_on_all_fixed_p4s": (
                    second_pivot_fixed_restrictions_vanish is True
                ),
            }
            if two_pivoted
            else {}
        ),
        **(
            {
                "five_pivot_restricted_term_counts_match_the_first_pivot": (
                    two_pivot_restricted_term_counts
                    == [
                        [10, 5, 5, 5],
                    ]
                    * 25
                    + [[5] * len(characters)] * 5
                ),
                "four_nonadjacent_pivots_restrict_to_zero_on_all_fixed_p4s": (
                    five_pivot_fixed_restrictions_vanish is True
                ),
            }
            if five_pivoted
            else {}
        ),
        "restricted_dimension_census_verified": (
            dimension_census == expected_dimension_census
        ),
        (
            "twenty_five_four_form_projective_traces_are_smooth_zero_dimensional"
            if balanced
            else "ten_four_form_projective_traces_are_smooth_zero_dimensional"
        ): (
            len(four_form_rows) == expected_four_form_rows
            and all(
                row["affine_cone_krull_dimension"] == 1
                and row["singular_affine_cone_krull_dimension"] == 0
                for row in four_form_rows
            )
        ),
        (
            "twenty_five_four_form_projective_traces_have_complete_intersection_degree"
            if balanced
            else "ten_four_form_projective_traces_have_complete_intersection_degree"
        ): all(
            row["projective_degree"] == MODULUS**4
            for row in four_form_rows
        ),
        (
            "five_other_projective_traces_are_empty"
            if balanced
            else "twenty_other_projective_traces_are_empty"
        ): (
            sum(
                row["projective_locus"] == "empty" for row in system_rows
            )
            == expected_empty_rows
        ),
        "one_hundred_twenty_five_forced_eigenpoints_checked": (
            sum(row["eigenpoint_count"] for row in forced_rows) == 125
        ),
        "all_seven_hundred_fifty_cyclic_eigenpoints_evaluated": (
            sum(
                row["eigenpoint_count"] for row in cyclic_eigenpoint_rows
            )
            == 750
            and all(
                row["invalid_eigenpoint_count"] == 0
                for row in cyclic_eigenpoint_rows
            )
        ),
        "only_the_one_hundred_twenty_five_forced_eigenpoints_are_common_zeros": (
            sum(
                row["common_zero_count"] for row in cyclic_eigenpoint_rows
            )
            == 125
            and all(
                row["common_zero_count"]
                == (MODULUS if row["subgroup_label"] == "H_0" else 0)
                for row in cyclic_eigenpoint_rows
            )
        ),
        "all_forced_eigenpoints_are_common_zeros": all(
            row["nonzero_equation_value_count"] == 0 for row in forced_rows
        ),
        "all_forced_eigenpoint_jacobians_have_rank_twenty_two": all(
            row["jacobian_rank_distribution"] == {"22": 25}
            for row in forced_rows
        ),
        "all_forced_eigenpoints_have_exact_expected_order_twenty_five_stabilizer": all(
            row["projective_stabilizer_order_distribution"] == {"25": 25}
            and row["unexpected_projective_stabilizer_count"] == 0
            for row in forced_rows
        ),
        "two_tangent_weights_derived_at_every_forced_eigenpoint": all(
            len(row["marked_generator_tangent_weights"]) == 2
            and all(
                weight != 0
                and weight % PRIME != 0
                for weight in row["marked_generator_tangent_weights"]
            )
            for row in forced_rows
        ),
        **(
            {
                "all_three_thousand_one_hundred_twenty_five_one_per_residue_coordinate_supports_have_full_pure_power_rank": (
                    coordinate_support_screen is not None
                    and coordinate_support_screen["support_count"] == 3125
                    and coordinate_support_screen["rank_histogram"]
                    == {"5": 3125}
                    and coordinate_support_screen[
                        "rank_deficient_support_count"
                    ]
                    == 0
                )
            }
            if balanced
            else {}
        ),
    }
    request: dict[str, Any] = {}
    return {
        "schema": (
            "reasoning.level25-heisenberg-balanced-five-pivot-restricted-system-profile.v1"
            if five_pivoted
            else "reasoning.level25-heisenberg-balanced-two-pivot-restricted-system-profile.v1"
            if two_pivoted
            else "reasoning.level25-heisenberg-balanced-pivoted-restricted-system-profile.v1"
            if pivoted
            else "reasoning.level25-heisenberg-balanced-restricted-system-profile.v1"
            if balanced
            else "reasoning.level25-heisenberg-surface-restricted-system-profile.v1"
        ),
        "request": request,
        "request_sha256": hashlib.sha256(
            canonical_json(request).encode()
        ).hexdigest(),
        "finite_field": {
            "prime": FINITE_FIELD_PRIME,
            "primitive_twenty_fifth_root": ROOT_OF_UNITY,
        },
        "form_definition": {
            "formula": (
                "Add P_(5,20)=sum_i zeta^(-5i)*x_i^20*x_(i+1)^5 "
                "and the four coefficient-one nonadjacent orbit sums "
                "(2,15;1,4,20), (2,15;2,13,10), "
                "(2,15;4,6,15), and (4,15;1,4,20), where "
                "(a,15;alpha,beta,gamma) denotes sum_i zeta^(-a*i)*"
                "x_i^alpha*x_(i+5)^beta*x_(i+2)^gamma; all other "
                "generators remain sparse"
                if five_pivoted
                else "F''_(2,15)=F_(2,15)+sum_i zeta^(-2i)*"
                "x_i^2*x_(i+5)^13*x_(i+2)^10; F'_(5,20) retains its "
                "coefficient-one pivot; all other generators remain sparse"
                if two_pivoted
                else "F'_(5,20)=F_(5,20)+sum_(i=0)^24 "
                "zeta^(-5*i)*x_i^20*x_(i+1)^5 with coefficient one; "
                "all other F_(a,b)=sum_(i=0)^24 zeta^(-a*i)*"
                "x_i^24*x_(i-b), with indices modulo 25"
                if pivoted
                else "F_(a,b) = sum_(i=0)^24 zeta^(-a*i) "
                "x_i^24*x_(i-b), with indices modulo 25"
            ),
            "coordinate_pullback_convention": (
                "S^*x_i=x_(i+1), T^*x_i=zeta^(-i)*x_i"
            ),
            "character_convention": "S-weight zeta^a and T-weight zeta^b",
            "ambient_form_recipe_sha256": hashlib.sha256(
                canonical_json(ambient_form_recipe).encode()
            ).hexdigest(),
            "ambient_form_count": len(ambient_form_recipe),
            **{
                (
                    "ambient_term_count"
                    if pivoted or two_pivoted or five_pivoted
                    else "ambient_sparse_term_count"
                ): sum(
                    len(form["terms"]) for form in ambient_form_recipe
                )
            },
        },
        "fixed_p4_systems": system_rows,
        "dimension_census_by_subgroup": dimension_census,
        "all_cyclic_eigenpoint_evaluations": cyclic_eigenpoint_rows,
        "forced_h0_eigenpoints": forced_rows,
        **(
            {"balanced_coordinate_support_screen": coordinate_support_screen}
            if balanced
            else {}
        ),
        "forced_tangent_weight_bridge": (
            "At a forced eigenpoint p with generator eigenvalue lambda, "
            "dF_j is supported on the projective tangent weight chi_j(g). "
            "The twenty-two restrictions are distinct and the evaluated "
            "Jacobian has rank 22, so those twenty-two weight lines are the "
            "normal space and the two omitted nonzero weights are exactly the "
            "marked tangent weights recorded above."
        ),
        "singular": singular,
        "eigenspace_transitivity_census": transitivity_rows,
        "eigenspace_symmetry_bridge": (
            "For H_t=<5(1,t)>, the commutator pairing with (0,1) is a "
            "primitive fifth root; for H_infinity=<5(0,1)>, its pairing with "
            "(1,0) is a primitive fifth root. The corresponding Heisenberg "
            "operator acts transitively on the five H-eigenspaces and scales "
            "each semi-invariant equation, so one Singular representative "
            "certifies all five projective restricted systems for that H."
        ),
        "characteristic_zero_bridges": {
            "fixed_p4_loci": (
                "The forms are defined over Z[zeta_25]. Properness of each "
                "relative projective fixed-P4 zero scheme and of its rank-"
                "deficient Jacobian locus carries the certified empty or "
                "smooth zero-dimensional F_101 behavior to the generic fiber."
            ),
            "forced_eigenpoint_gradients": (
                "The forced eigenpoints and gradients are cyclotomic. A nonzero "
                "22-by-22 Jacobian minor modulo the prime sending zeta_25 to 5 "
                "is a nonzero cyclotomic integer, so the corresponding "
                "characteristic-zero Jacobian has rank 22."
            ),
        },
        "certificate": certificate,
        "certificate_verified": all(certificate.values()),
        "status": (
            "five-pivot balanced fixed-stratum and forced-eigenpoint gates certified"
            if five_pivoted
            else "two-pivot balanced fixed-stratum and forced-eigenpoint gates certified"
            if two_pivoted
            else "pivoted balanced fixed-stratum and forced-eigenpoint gates certified"
            if pivoted
            else "balanced fixed-stratum and forced-eigenpoint gates certified"
            if balanced
            else "fixed-stratum and forced-eigenpoint gates certified"
        ),
        "not_certified": [
            "global dimension, regular-sequence, smoothness, or integrality of the proposed surface",
            "absence of non-fixed singularities",
            "an invariant integral H2 lattice or order-25 transgression",
            "a Brauer class, period, index bound, or counterexample",
        ],
        "next_gate": {
            "feature": (
                "level25_heisenberg_balanced_five_pivot_surface_global_profile"
                if five_pivoted
                else "level25_heisenberg_nonadjacent_block_shape_cover"
                if two_pivoted
                else "level25_heisenberg_balanced_pivoted_surface_global_profile"
                if pivoted
                else "level25_heisenberg_balanced_surface_global_profile"
                if balanced
                else "level25_heisenberg_surface_global_profile"
            ),
            "requirement": (
                "Certify that the explicit five-pivot tuple defines a "
                "globally smooth projective surface in P24, including the "
                "free-locus Jacobian gate and integrality."
                if five_pivoted
                else "Replace the singular two-pivot tuple using a finite orbit "
                "set that covers every admissible nonadjacent block shape."
                if two_pivoted
                else "Certify that the twenty-two forms define a globally smooth "
                "projective surface in P24, using an exact computation or a "
                "fully stated joint-incidence argument."
                if pivoted
                else "Certify that the twenty-two forms define a globally "
                "smooth projective surface in P24, using an exact sparse "
                "computation or a fully stated equivariant Bertini argument."
            ),
            "known_noether_limit": (
                "The pinned Noether v0.1 ideal gateway accepts at most sixteen "
                "variables, while this global ideal has twenty-five."
            ),
        },
    }


def level25_heisenberg_surface_restricted_system_profile() -> dict[str, Any]:
    """Certify the original fixed-P4 and forced-eigenpoint systems."""

    return _level25_heisenberg_surface_restricted_system_profile(
        LEVEL25_HEISENBERG_CHARACTERS,
        balanced=False,
    )


def level25_heisenberg_balanced_restricted_system_profile() -> dict[str, Any]:
    """Certify the balanced fixed-P4 and forced-eigenpoint systems."""

    return _level25_heisenberg_surface_restricted_system_profile(
        LEVEL25_HEISENBERG_BALANCED_CHARACTERS,
        balanced=True,
    )


def level25_heisenberg_balanced_pivoted_restricted_system_profile() -> dict[
    str, Any
]:
    """Certify the coefficient-one pivoted fixed and cyclic local gates."""

    return _level25_heisenberg_surface_restricted_system_profile(
        LEVEL25_HEISENBERG_BALANCED_CHARACTERS,
        balanced=True,
        pivoted=True,
    )


def level25_heisenberg_balanced_two_pivot_restricted_system_profile() -> dict[
    str, Any
]:
    """Certify the coefficient-one two-pivot fixed and cyclic local gates."""

    return _level25_heisenberg_surface_restricted_system_profile(
        LEVEL25_HEISENBERG_BALANCED_CHARACTERS,
        balanced=True,
        pivoted=True,
        two_pivoted=True,
    )


def level25_heisenberg_balanced_five_pivot_restricted_system_profile() -> dict[
    str, Any
]:
    """Certify the coefficient-one five-pivot fixed and cyclic local gates."""

    return _level25_heisenberg_surface_restricted_system_profile(
        LEVEL25_HEISENBERG_BALANCED_CHARACTERS,
        balanced=True,
        pivoted=True,
        five_pivoted=True,
    )


def level25_heisenberg_surface_sparse_global_obstruction_profile() -> dict[
    str, Any
]:
    """Reject the explicit sparse forms using a coordinate-P4 base locus."""

    admissible_supports = []
    for support in combinations(range(MODULUS), PRIME):
        support_set = set(support)
        if len({index % PRIME for index in support}) != PRIME:
            continue
        if any((index + 1) % MODULUS in support_set for index in support):
            continue
        admissible_supports.append(list(support))
    witness_support = admissible_supports[0]
    witness_set = set(witness_support)
    restriction_rows = []
    for character_index, character in enumerate(LEVEL25_HEISENBERG_CHARACTERS):
        a, b = character
        surviving_indexes = [
            index
            for index in witness_support
            if (index - b) % MODULUS in witness_set
        ]
        restriction_rows.append(
            {
                "character_index": character_index,
                "character": list(character),
                "shift": b,
                "surviving_ambient_term_indexes": surviving_indexes,
                "restricted_terms": [
                    {
                        "coefficient": _finite_field_power(-a * index),
                        "powered_coordinate": index,
                        "powered_exponent": MODULUS - 1,
                        "linear_coordinate": (index - b) % MODULUS,
                    }
                    for index in surviving_indexes
                ],
                "restriction_is_zero": not surviving_indexes,
            }
        )
    nonzero_rows = [
        row for row in restriction_rows if not row["restriction_is_zero"]
    ]
    all_supports_sha256 = hashlib.sha256(
        canonical_json(admissible_supports).encode()
    ).hexdigest()
    restricted_system_sha256 = hashlib.sha256(
        canonical_json(restriction_rows).encode()
    ).hexdigest()
    certificate = {
        "lexicographically_first_support_is_0_2_4_6_8": (
            witness_support == [0, 2, 4, 6, 8]
        ),
        "support_has_one_index_in_each_residue_class_mod_five": (
            len({index % PRIME for index in witness_support}) == PRIME
        ),
        "support_has_no_cyclically_adjacent_pair": all(
            (index + 1) % MODULUS not in witness_set
            for index in witness_support
        ),
        "selected_shift_set_is_0_1_5_10_15_20": sorted(
            {character[1] for character in LEVEL25_HEISENBERG_CHARACTERS}
        )
        == [0, 1, 5, 10, 15, 20],
        "every_nonzero_shift_form_restricts_to_zero": all(
            row["restriction_is_zero"]
            for row in restriction_rows
            if row["shift"] != 0
        ),
        "only_character_1_0_survives": (
            len(nonzero_rows) == 1
            and nonzero_rows[0]["character"] == [1, 0]
        ),
        "surviving_restriction_has_five_distinct_nonzero_pure_power_terms": (
            len(nonzero_rows[0]["restricted_terms"]) == PRIME
            and all(
                term["coefficient"] != 0
                and term["powered_coordinate"] == term["linear_coordinate"]
                for term in nonzero_rows[0]["restricted_terms"]
            )
        ),
        "coordinate_p4_intersection_has_projective_dimension_three": (
            len(witness_support) - 1 - len(nonzero_rows) == 3
        ),
        "explicit_sparse_common_zero_scheme_has_dimension_at_least_three": (
            len(witness_support) - 1 - len(nonzero_rows) >= 3
        ),
        "all_admissible_five_coordinate_supports_enumerated": (
            len(admissible_supports) == 1025
        ),
    }
    request: dict[str, Any] = {}
    return {
        "schema": (
            "reasoning.level25-heisenberg-surface-sparse-global-obstruction-profile.v1"
        ),
        "request": request,
        "request_sha256": hashlib.sha256(
            canonical_json(request).encode()
        ).hexdigest(),
        "explicit_form_family": {
            "formula": (
                "F_(a,b) = sum_(i=0)^24 zeta^(-a*i) "
                "x_i^24*x_(i-b), with indices modulo 25"
            ),
            "selected_character_count": len(LEVEL25_HEISENBERG_CHARACTERS),
            "expected_surface_dimension_if_codimension_twenty_two": 2,
        },
        "coordinate_subspace_witness": {
            "support": witness_support,
            "ambient_coordinate_subspace": "P4",
            "restricted_system": restriction_rows,
            "restricted_system_sha256": restricted_system_sha256,
            "nonzero_restricted_equation_count": len(nonzero_rows),
            "common_zero_projective_dimension": 3,
            "common_zero_description": (
                "the degree-25 hypersurface sum_(i in support) "
                "zeta^(-i)*x_i^25=0 in the coordinate P4"
            ),
        },
        "admissible_support_census": {
            "count": len(admissible_supports),
            "all_supports_sha256": all_supports_sha256,
            "definition": (
                "five indices, one in each residue class modulo five, with no "
                "cyclically adjacent pair"
            ),
        },
        "certificate": certificate,
        "certificate_verified": all(certificate.values()),
        "verdict": "explicit sparse form family rejected",
        "not_implied": [
            "failure of generic forms in the same twenty-two character eigenspaces",
            "failure of the level-25 Heisenberg character strategy",
            "a Brauer class, period, index bound, or counterexample",
        ],
        "next_gate": {
            "feature": (
                "level25_heisenberg_surface_order5_jacobian_capacity_profile"
            ),
            "requirement": (
                "Before replacing the sparse coefficients by generic forms, "
                "test whether the selected character multiplicities can span "
                "the required first jets on every unavoidable order-five "
                "fixed trace."
            ),
        },
    }


def level25_heisenberg_surface_order5_jacobian_capacity_profile() -> dict[
    str, Any
]:
    """Reject the current character multiset at an unavoidable H_4 zero."""

    tangent_weight_capacities = [PRIME - 1] + [PRIME] * (PRIME - 1)
    subgroup_rows = []
    for subgroup in _order_five_subgroups():
        weight_counts = [0] * PRIME
        for character in LEVEL25_HEISENBERG_CHARACTERS:
            exponent = _evaluate(character, subgroup["generator"])
            if exponent % PRIME != 0:
                raise ValueError("order-five restriction exponent is not divisible by five")
            weight_counts[exponent // PRIME] += 1
        rank_contributions = [
            min(count, capacity)
            for count, capacity in zip(
                weight_counts,
                tangent_weight_capacities,
                strict=True,
            )
        ]
        maximum_rank = sum(rank_contributions)
        effective_count = weight_counts[0]
        forced_nonempty = effective_count <= PRIME - 1
        subgroup_rows.append(
            {
                "subgroup_label": subgroup["label"],
                "generator": list(subgroup["generator"]),
                "equation_restriction_weight_counts": weight_counts,
                "projective_tangent_weight_capacities": (
                    tangent_weight_capacities
                ),
                "maximum_jacobian_rank_contributions": rank_contributions,
                "maximum_jacobian_rank_at_a_common_zero": maximum_rank,
                "rank_deficiency_from_twenty_two": (
                    len(LEVEL25_HEISENBERG_CHARACTERS) - maximum_rank
                ),
                "effective_restricted_equation_count": effective_count,
                "common_projective_zero_forced_by_height": forced_nonempty,
                "unavoidable_rank_deficient_common_zero": (
                    forced_nonempty
                    and maximum_rank < len(LEVEL25_HEISENBERG_CHARACTERS)
                ),
            }
        )
    h0 = subgroup_rows[0]
    h4 = subgroup_rows[4]
    obstructed_rows = [
        row
        for row in subgroup_rows
        if row["unavoidable_rank_deficient_common_zero"]
    ]
    certificate = {
        "six_order_five_subgroups_checked": len(subgroup_rows) == PRIME + 1,
        "projective_tangent_capacities_are_4_5_5_5_5": (
            tangent_weight_capacities == [4, 5, 5, 5, 5]
        ),
        "restriction_weight_census_verified": [
            row["equation_restriction_weight_counts"]
            for row in subgroup_rows
        ]
        == [
            [4, 5, 5, 4, 4],
            [8, 5, 0, 5, 4],
            [8, 9, 0, 0, 5],
            [9, 9, 4, 0, 0],
            [4, 10, 4, 4, 0],
            [9, 13, 0, 0, 0],
        ],
        "h0_character_capacity_permits_rank_twenty_two": (
            h0["maximum_jacobian_rank_at_a_common_zero"] == 22
        ),
        "h4_has_exactly_four_effective_restricted_equations": (
            h4["effective_restricted_equation_count"] == 4
        ),
        "h4_common_projective_zero_is_forced": h4[
            "common_projective_zero_forced_by_height"
        ],
        "h4_jacobian_rank_is_at_most_seventeen": (
            h4["maximum_jacobian_rank_at_a_common_zero"] == 17
        ),
        "h4_rank_deficiency_is_five": (
            h4["rank_deficiency_from_twenty_two"] == 5
        ),
        "h4_is_the_unique_forced_order_five_capacity_obstruction": (
            [row["subgroup_label"] for row in obstructed_rows] == ["H_4"]
        ),
    }
    request: dict[str, Any] = {}
    return {
        "schema": (
            "reasoning.level25-heisenberg-surface-order5-jacobian-capacity-profile.v1"
        ),
        "request": request,
        "request_sha256": hashlib.sha256(
            canonical_json(request).encode()
        ).hexdigest(),
        "equation_count": len(LEVEL25_HEISENBERG_CHARACTERS),
        "expected_surface_dimension": 2,
        "weight_capacity_derivation": (
            "At a common zero p in an H-eigenspace P4, homogeneity of degree "
            "25 makes the differential of a character-chi equation live in "
            "the projective tangent weight chi|H. The weight-zero tangent "
            "space has dimension four and each of the four nonzero tangent "
            "weights has dimension five. Equations of one weight therefore "
            "contribute at most the corresponding capacity."
        ),
        "height_derivation": (
            "Exactly e_H indexed equations can restrict nontrivially to an "
            "H-eigenspace P4. When e_H is at most four, Krull height bounds "
            "force their projective common zero scheme to be nonempty."
        ),
        "order_five_subgroups": subgroup_rows,
        "certificate": certificate,
        "certificate_verified": all(certificate.values()),
        "verdict": (
            "the current twenty-two-character multiset cannot support a "
            "smooth codimension-twenty-two surface"
        ),
        "scope": (
            "The obstruction applies to every one-form-per-indexed-character "
            "choice for this multiset, not only the explicit sparse forms."
        ),
        "not_implied": [
            "failure of every alternative twenty-two-character multiset",
            "failure of every level-25 Heisenberg surface strategy",
            "a Brauer class, period, index bound, or counterexample",
        ],
        "next_gate": {
            "feature": "level25_heisenberg_character_capacity_search_profile",
            "requirement": (
                "Search for a replacement character multiset satisfying the "
                "order-five height, Jacobian-capacity, cyclic-lift coverage, "
                "and forced-eigenpoint distinctness constraints simultaneously."
            ),
        },
    }


def _reduction_kernel_label(first: int, second: int) -> str:
    if second % PRIME == 0:
        return "H_infinity"
    slope = -first * pow(second, -1, PRIME) % PRIME
    return f"H_{slope}"


def _reduction_weight(first: int, second: int, line: str) -> int:
    if line == "H_infinity":
        return second % PRIME
    slope = int(line.removeprefix("H_"))
    return (first + slope * second) % PRIME


def level25_heisenberg_character_capacity_search_profile() -> dict[str, Any]:
    """Certify a replacement character census passing the finite local gates."""

    group_sizes = [5, 5, 4, 4]
    non_h0_lines = ["H_1", "H_2", "H_3", "H_4", "H_infinity"]
    admissible_reduction_patterns = []
    for slopes in product(range(PRIME), repeat=4):
        kernel_lines = {
            _reduction_kernel_label(first, second)
            for first, second in zip(range(1, PRIME), slopes, strict=True)
        }
        missing_lines = [
            line for line in non_h0_lines if line not in kernel_lines
        ]
        capacity_ok = all(
            len(
                {
                    _reduction_weight(first, second, line)
                    for first, second in zip(
                        range(1, PRIME),
                        slopes,
                        strict=True,
                    )
                }
            )
            == PRIME - 1
            for line in missing_lines
        )
        if capacity_ok:
            admissible_reduction_patterns.append(
                {
                    "slopes_by_nonzero_first_residue": list(slopes),
                    "occupied_kernel_lines": sorted(kernel_lines),
                    "missing_kernel_lines": missing_lines,
                }
            )

    characters = LEVEL25_HEISENBERG_BALANCED_CHARACTERS
    zero_reduction_indexes = [
        index
        for index, (first, second) in enumerate(characters)
        if first % PRIME == 0 and second % PRIME == 0
    ]
    tangent_capacities = [PRIME - 1] + [PRIME] * (PRIME - 1)
    subgroup_rows = []
    uncovered_lifts = []
    for subgroup_index, subgroup in enumerate(_order_five_subgroups()):
        weight_counts = [0] * PRIME
        for character in characters:
            exponent = _evaluate(character, subgroup["generator"])
            weight_counts[exponent // PRIME] += 1
        rank_contributions = [
            min(count, capacity)
            for count, capacity in zip(
                weight_counts,
                tangent_capacities,
                strict=True,
            )
        ]
        lift_rows = []
        for lift_index, generator in enumerate(subgroup["cyclic_lifts"]):
            exponents = [
                _evaluate(character, generator) for character in characters
            ]
            annihilator_indexes = [
                index for index, exponent in enumerate(exponents) if exponent == 0
            ]
            uncovered = not annihilator_indexes
            distinct_nonzero = (
                len(set(exponents)) == len(exponents) and 0 not in exponents
            )
            row = {
                "lift_index": lift_index,
                "generator": list(generator),
                "annihilator_character_indexes": annihilator_indexes,
                "uncovered": uncovered,
                "restriction_exponents": exponents,
                "distinct_nonzero_if_uncovered": (
                    distinct_nonzero if uncovered else None
                ),
            }
            lift_rows.append(row)
            if uncovered:
                uncovered_lifts.append(
                    {
                        "subgroup_label": subgroup["label"],
                        **row,
                    }
                )
        subgroup_rows.append(
            {
                "subgroup_index": subgroup_index,
                "subgroup_label": subgroup["label"],
                "generator": list(subgroup["generator"]),
                "restriction_weight_counts": weight_counts,
                "effective_restricted_equation_count": weight_counts[0],
                "maximum_jacobian_rank_at_a_common_zero": sum(
                    rank_contributions
                ),
                "maximum_jacobian_rank_contributions": rank_contributions,
                "common_projective_zero_forced_by_height": (
                    weight_counts[0] <= PRIME - 1
                ),
                "cyclic_lifts": lift_rows,
            }
        )

    effective_counts = [
        row["effective_restricted_equation_count"] for row in subgroup_rows
    ]
    certificate = {
        "twenty_two_distinct_characters_selected": (
            len(characters) == 22 and len(set(characters)) == 22
        ),
        "exactly_four_scaled_characters_selected": (
            len(zero_reduction_indexes) == 4
        ),
        "five_normalized_capacity_admissible_reduction_patterns_enumerated": (
            len(admissible_reduction_patterns) == 5
        ),
        "balanced_zero_slope_pattern_is_admissible": any(
            row["slopes_by_nonzero_first_residue"] == [0, 0, 0, 0]
            for row in admissible_reduction_patterns
        ),
        "effective_count_census_is_4_4_4_4_4_22": (
            effective_counts == [4, 4, 4, 4, 4, 22]
        ),
        "forced_order_five_traces_have_full_character_capacity": all(
            not row["common_projective_zero_forced_by_height"]
            or row["maximum_jacobian_rank_at_a_common_zero"] == 22
            for row in subgroup_rows
        ),
        "exactly_the_five_h0_lifts_are_uncovered": (
            len(uncovered_lifts) == PRIME
            and all(
                row["subgroup_label"] == "H_0" for row in uncovered_lifts
            )
        ),
        "all_uncovered_lifts_have_distinct_nonzero_restrictions": all(
            row["distinct_nonzero_if_uncovered"] for row in uncovered_lifts
        ),
        "every_non_h0_lift_has_an_annihilating_character": all(
            not lift["uncovered"]
            for row in subgroup_rows[1:]
            for lift in row["cyclic_lifts"]
        ),
        "effective_count_identity_verified": (
            sum(effective_counts)
            == len(characters) + PRIME * len(zero_reduction_indexes)
        ),
    }
    request: dict[str, Any] = {}
    return {
        "schema": (
            "reasoning.level25-heisenberg-character-capacity-search-profile.v1"
        ),
        "request": request,
        "request_sha256": hashlib.sha256(
            canonical_json(request).encode()
        ).hexdigest(),
        "normalization": {
            "retained_order_five_subgroup": "H_0",
            "unscaled_group_sizes_by_first_residue": group_sizes,
            "reduction_slope_search_space": PRIME**4,
            "admissible_reduction_patterns": admissible_reduction_patterns,
        },
        "characters": [list(character) for character in characters],
        "zero_reduction_character_indexes": zero_reduction_indexes,
        "order_five_subgroups": subgroup_rows,
        "uncovered_cyclic_lifts": uncovered_lifts,
        "certificate": certificate,
        "certificate_verified": all(certificate.values()),
        "status": "replacement character multiset passes the finite local gates",
        "not_certified": [
            "restricted-system base loci for forms with the replacement characters",
            "first-jet nonvanishing for actual replacement semi-invariant forms",
            "global dimension, smoothness, or integrality of a surface",
            "a Brauer class, period, index bound, or counterexample",
        ],
        "next_gate": {
            "feature": "level25_heisenberg_balanced_restricted_system_profile",
            "requirement": (
                "Construct explicit or generic forms with the replacement "
                "characters and certify all fixed-P4 restrictions, all cyclic "
                "eigenpoints, and their Jacobian ranks before any global claim."
            ),
        },
    }
