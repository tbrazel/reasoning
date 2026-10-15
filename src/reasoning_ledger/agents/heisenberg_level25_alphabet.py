"""Exact F_101 pure-power census for the balanced level-25 candidate."""

from __future__ import annotations

import hashlib
import math
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .. import sandbox
from ..canonical import canonical_json
from .heisenberg_level25 import (
    FINITE_FIELD_PRIME,
    LEVEL25_HEISENBERG_BALANCED_CHARACTERS,
    MODULUS,
    PRIME,
    ROOT_OF_UNITY,
)
from .science_evidence import exact_execution_matches, seal_profile_evidence
from .scientific import ScientificToolError

_PROTOCOL_VERSION = 1
_TIMEOUT_SECONDS = 10
_MAX_OUTPUT_BYTES = 16 * 1024
_REVIEWED_PROGRAM_SHA256 = "0cd65236f0308ff7321215e3f08eb21065eef9708a432060a4a16e249dd0ef43"
_REVIEWED_TRANSCRIPT_SHA256 = "438271f4ca15ff5dc0b41c43dac209e72928ebb317903b7dac3b39f383fabfe8"
_PURE_POWER_EVIDENCE_SHA256 = "6c5203617f2123454fdf41c6f50ea658d4c0edb8da4a6da4891f733ddbda3de0"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _program() -> str:
    zero_shift_characters = [first for first, second in LEVEL25_HEISENBERG_BALANCED_CHARACTERS if second == 0]
    characters = ", ".join(str(value) for value in zero_shift_characters)
    return r"""#include <array>
#include <cstddef>
#include <cstdint>
#include <iostream>
#include <utility>
#include <vector>

constexpr int P = 101;
constexpr int ZETA = 5;
constexpr int N = 25;
constexpr int ROWS = 14;
constexpr int FREE = 11;
constexpr std::array<int, ROWS> A0{@A0@};
constexpr std::array<int, 5> ALPHABET{0, 1, 10, 100, 91};

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

bool allowed(int value) {
    for (int candidate : ALPHABET) {
        if (value == candidate) return true;
    }
    return false;
}

template <std::size_t Size>
struct Part {
    std::array<std::uint8_t, ROWS> sum{};
    std::array<std::uint8_t, Size> values{};
};

template <std::size_t Size>
std::vector<Part<Size>> parts(
    const std::array<std::array<int, FREE>, ROWS>& map,
    int offset
) {
    std::uint64_t total = 1;
    for (std::size_t index = 0; index < Size; ++index) total *= 5;
    std::vector<Part<Size>> result;
    result.reserve(total);
    for (std::uint64_t code = 0; code < total; ++code) {
        std::uint64_t remaining = code;
        Part<Size> part;
        for (std::size_t column = 0; column < Size; ++column) {
            int value = ALPHABET[remaining % 5];
            remaining /= 5;
            part.values[column] = static_cast<std::uint8_t>(value);
            for (int row = 0; row < ROWS; ++row) {
                part.sum[row] = static_cast<std::uint8_t>(
                    (part.sum[row] + map[row][offset + column] * value) % P
                );
            }
        }
        result.push_back(part);
    }
    return result;
}

bool equations_vanish(const std::array<int, N>& values) {
    for (int row = 0; row < ROWS; ++row) {
        int sum = 0;
        for (int index = 0; index < N; ++index) {
            sum = (sum + coefficient(A0[row], index) * values[index]) % P;
        }
        if (sum != 0) return false;
    }
    return true;
}

int main() {
    std::array<std::array<int, ROWS + FREE>, ROWS> augmented{};
    for (int row = 0; row < ROWS; ++row) {
        for (int column = 0; column < N; ++column) {
            augmented[row][column] = coefficient(A0[row], column);
        }
    }
    int pivot_rank = 0;
    for (int column = 0; column < ROWS; ++column) {
        int pivot = pivot_rank;
        while (pivot < ROWS && augmented[pivot][column] == 0) ++pivot;
        if (pivot == ROWS) continue;
        std::swap(augmented[pivot_rank], augmented[pivot]);
        int inverse = mod_pow(augmented[pivot_rank][column], P - 2);
        for (int entry = column; entry < N; ++entry) {
            augmented[pivot_rank][entry] =
                augmented[pivot_rank][entry] * inverse % P;
        }
        for (int row = 0; row < ROWS; ++row) {
            if (row == pivot_rank || augmented[row][column] == 0) continue;
            int factor = augmented[row][column];
            for (int entry = column; entry < N; ++entry) {
                int value = augmented[row][entry]
                    - factor * augmented[pivot_rank][entry];
                value %= P;
                if (value < 0) value += P;
                augmented[row][entry] = value;
            }
        }
        ++pivot_rank;
    }
    if (pivot_rank != ROWS) return 2;

    std::array<std::array<int, FREE>, ROWS> map{};
    for (int row = 0; row < ROWS; ++row) {
        for (int column = 0; column < FREE; ++column) {
            map[row][column] = (P - augmented[row][ROWS + column]) % P;
        }
    }
    auto left = parts<5>(map, 0);
    auto right = parts<6>(map, 5);
    std::uint64_t solution_count = 0;
    std::uint64_t residual_failure_count = 0;
    std::uint64_t non_block_constant_count = 0;
    std::array<std::uint64_t, 6> support_block_histogram{};
    for (const auto& first : left) {
        for (const auto& second : right) {
            std::array<int, N> values{};
            bool admissible = true;
            for (int row = 0; row < ROWS; ++row) {
                values[row] = (first.sum[row] + second.sum[row]) % P;
                if (!allowed(values[row])) {
                    admissible = false;
                    break;
                }
            }
            if (!admissible) continue;
            for (int index = 0; index < 5; ++index) {
                values[ROWS + index] = first.values[index];
            }
            for (int index = 0; index < 6; ++index) {
                values[ROWS + 5 + index] = second.values[index];
            }
            ++solution_count;
            if (!equations_vanish(values)) ++residual_failure_count;
            bool block_constant = true;
            int active_blocks = 0;
            for (int residue = 0; residue < 5; ++residue) {
                int value = values[residue];
                if (value != 0) ++active_blocks;
                for (int quotient = 1; quotient < 5; ++quotient) {
                    if (values[residue + 5 * quotient] != value) {
                        block_constant = false;
                    }
                }
            }
            if (block_constant) {
                ++support_block_histogram[active_blocks];
            } else {
                ++non_block_constant_count;
            }
        }
    }

    std::uint64_t expected_block_count = 0;
    std::uint64_t expected_block_failure_count = 0;
    for (std::uint64_t code = 0; code < 3125; ++code) {
        std::uint64_t remaining = code;
        std::array<int, N> values{};
        for (int residue = 0; residue < 5; ++residue) {
            int value = ALPHABET[remaining % 5];
            remaining /= 5;
            for (int quotient = 0; quotient < 5; ++quotient) {
                values[residue + 5 * quotient] = value;
            }
        }
        ++expected_block_count;
        if (!equations_vanish(values)) ++expected_block_failure_count;
    }

    std::array<std::uint64_t, 5> power_image_counts{};
    std::uint64_t power_image_outside_count = 0;
    for (int value = 0; value < P; ++value) {
        int power_value = mod_pow(value, 25);
        bool found = false;
        for (std::size_t index = 0; index < ALPHABET.size(); ++index) {
            if (power_value == ALPHABET[index]) {
                ++power_image_counts[index];
                found = true;
            }
        }
        if (!found) ++power_image_outside_count;
    }

    std::cout << "REASONING_LEVEL25_PURE_POWER_ALPHABET_V1\n";
    std::cout << "pivot_rank=" << pivot_rank << "\n";
    std::cout << "free_assignment_count="
              << left.size() * right.size() << "\n";
    std::cout << "solution_count=" << solution_count << "\n";
    std::cout << "nonzero_solution_count=" << solution_count - 1 << "\n";
    std::cout << "equation_residual_failure_count="
              << residual_failure_count << "\n";
    std::cout << "non_block_constant_solution_count="
              << non_block_constant_count << "\n";
    std::cout << "expected_block_constant_count="
              << expected_block_count << "\n";
    std::cout << "expected_block_constant_failure_count="
              << expected_block_failure_count << "\n";
    std::cout << "support_block_histogram=";
    for (std::size_t index = 0; index < support_block_histogram.size(); ++index) {
        if (index) std::cout << ",";
        std::cout << index << ":" << support_block_histogram[index];
    }
    std::cout << "\n";
    std::cout << "power_image_counts=";
    for (std::size_t index = 0; index < ALPHABET.size(); ++index) {
        if (index) std::cout << ",";
        std::cout << ALPHABET[index] << ":" << power_image_counts[index];
    }
    std::cout << "\n";
    std::cout << "power_image_outside_count="
              << power_image_outside_count << "\n";
    std::cout << "REASONING_END\n";
}
""".replace("@A0@", characters)


def _parse_histogram(value: str, *, field: str) -> dict[int, int]:
    histogram: dict[int, int] = {}
    for entry in value.split(","):
        if ":" not in entry:
            raise ScientificToolError(f"pure-power alphabet transcript has an invalid {field}")
        key_text, count_text = entry.split(":", 1)
        try:
            key = int(key_text)
            count = int(count_text)
        except ValueError as error:
            raise ScientificToolError(f"pure-power alphabet transcript has a nonintegral {field}") from error
        if key in histogram or count < 0:
            raise ScientificToolError(f"pure-power alphabet transcript has an invalid {field}")
        histogram[key] = count
    return histogram


def _parse_transcript(stdout: str) -> dict[str, Any]:
    lines = stdout.strip().splitlines()
    frame = f"REASONING_LEVEL25_PURE_POWER_ALPHABET_V{_PROTOCOL_VERSION}"
    if len(lines) < 3 or lines[0] != frame or lines[-1] != "REASONING_END":
        raise ScientificToolError("pure-power alphabet transcript has invalid framing")
    fields: dict[str, str] = {}
    for line in lines[1:-1]:
        if "=" not in line:
            raise ScientificToolError("pure-power alphabet transcript has an invalid field")
        name, value = line.split("=", 1)
        if name in fields:
            raise ScientificToolError("pure-power alphabet transcript has duplicate fields")
        fields[name] = value
    scalar_names = {
        "pivot_rank",
        "free_assignment_count",
        "solution_count",
        "nonzero_solution_count",
        "equation_residual_failure_count",
        "non_block_constant_solution_count",
        "expected_block_constant_count",
        "expected_block_constant_failure_count",
        "power_image_outside_count",
    }
    expected = scalar_names | {
        "support_block_histogram",
        "power_image_counts",
    }
    if set(fields) != expected:
        raise ScientificToolError("pure-power alphabet transcript is incomplete")
    try:
        scalars = {name: int(fields[name]) for name in scalar_names}
    except ValueError as error:
        raise ScientificToolError("pure-power alphabet transcript has a nonintegral field") from error
    if any(value < 0 for value in scalars.values()):
        raise ScientificToolError("pure-power alphabet transcript has a negative field")
    return {
        **scalars,
        "support_block_histogram": _parse_histogram(
            fields["support_block_histogram"],
            field="support-block histogram",
        ),
        "power_image_counts": _parse_histogram(
            fields["power_image_counts"],
            field="power-image histogram",
        ),
    }


def _run_program() -> dict[str, Any]:
    compiler_value = shutil.which("g++")
    if compiler_value is None:
        raise ScientificToolError("g++ is unavailable on the Reasoning service PATH")
    compiler = Path(compiler_value).resolve()
    program = _program()
    environment = {
        "HOME": "",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
    }
    try:
        with tempfile.TemporaryDirectory(prefix="reasoning-level25-pure-power-alphabet-") as temporary:
            workdir = Path(temporary)
            environment["HOME"] = temporary
            binary = workdir / "pure-power-alphabet"
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
                timeout=_TIMEOUT_SECONDS,
                env=environment,
                check=False,
            )
            if compiled.returncode or compiled.stderr.strip():
                detail = (compiled.stderr.strip() or compiled.stdout.strip() or "unknown compiler failure")[:2000]
                raise ScientificToolError(f"pure-power alphabet program failed to compile: {detail}")
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
                timeout=_TIMEOUT_SECONDS,
                env=environment,
                check=False,
            )
    except subprocess.TimeoutExpired as error:
        raise ScientificToolError(f"pure-power alphabet program exceeded the {_TIMEOUT_SECONDS}-second wall timeout") from error
    except OSError as error:
        raise ScientificToolError(f"pure-power alphabet program could not start: {error}") from error
    stdout = completed.stdout
    stderr = completed.stderr
    if len(stdout.encode()) > _MAX_OUTPUT_BYTES or len(stderr.encode()) > _MAX_OUTPUT_BYTES:
        raise ScientificToolError("pure-power alphabet output exceeds the safe bound")
    if completed.returncode or stderr.strip():
        detail = (stderr.strip() or stdout.strip() or "unknown program failure")[:2000]
        raise ScientificToolError(f"pure-power alphabet program failed: {detail}")
    parsed = _parse_transcript(stdout)
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
        raise ScientificToolError(f"pure-power alphabet compiler version check failed: {error}") from error
    return {
        "protocol_version": _PROTOCOL_VERSION,
        "program_sha256": hashlib.sha256(program.encode()).hexdigest(),
        "transcript_sha256": hashlib.sha256(stdout.encode()).hexdigest(),
        "transcript": stdout,
        "compiler": str(compiler),
        "compiler_sha256": _sha256_file(compiler),
        "compiler_version": compiler_version,
        "binary_sha256": binary_sha256,
        **parsed,
    }


def level25_heisenberg_balanced_five_pivot_pure_power_alphabet_profile() -> dict[str, Any]:
    """Classify F_101-rational 25th powers on the fourteen pure equations."""

    computation = _run_program()
    alphabet = [0, 1, 10, 100, 91]
    zero_shift_characters = [first for first, second in LEVEL25_HEISENBERG_BALANCED_CHARACTERS if second == 0]
    expected_support_histogram = {
        active_blocks: math.comb(PRIME, active_blocks) * (PRIME - 1) ** active_blocks for active_blocks in range(PRIME + 1)
    }
    power_image = {pow(value, MODULUS, FINITE_FIELD_PRIME) for value in range(FINITE_FIELD_PRIME)}
    certificate = {
        "complete_native_output_matches_reviewed_computation": exact_execution_matches(
            computation,
            parser=_parse_transcript,
            expected_program=_REVIEWED_PROGRAM_SHA256,
            expected_transcript=_REVIEWED_TRANSCRIPT_SHA256,
        ),
        "root_has_exact_order_twenty_five": (
            pow(ROOT_OF_UNITY, MODULUS, FINITE_FIELD_PRIME) == 1 and pow(ROOT_OF_UNITY, PRIME, FINITE_FIELD_PRIME) != 1
        ),
        "fourteen_zero_shift_characters_are_distinct": (len(zero_shift_characters) == 14 and len(set(zero_shift_characters)) == 14),
        "twenty_fifth_power_image_is_zero_union_mu_four": (
            power_image == set(alphabet)
            and computation["power_image_counts"] == {0: 1, 1: 25, 10: 25, 100: 25, 91: 25}
            and computation["power_image_outside_count"] == 0
        ),
        "first_fourteen_fourier_columns_are_invertible": (computation["pivot_rank"] == 14),
        "all_five_to_the_eleventh_free_assignments_exhausted": (
            computation["free_assignment_count"] == 5**11 and computation["equation_residual_failure_count"] == 0
        ),
        "every_rational_solution_is_residue_block_constant": (
            computation["solution_count"] == 5**5 and computation["non_block_constant_solution_count"] == 0
        ),
        "every_residue_block_constant_pattern_is_a_solution": (
            computation["expected_block_constant_count"] == 5**5 and computation["expected_block_constant_failure_count"] == 0
        ),
        "support_histogram_matches_five_independent_block_values": (
            computation["support_block_histogram"] == expected_support_histogram and computation["nonzero_solution_count"] == 5**5 - 1
        ),
    }
    request: dict[str, Any] = {}
    verified = all(certificate.values())
    return seal_profile_evidence({
        "schema": ("reasoning.level25-heisenberg-balanced-five-pivot-pure-power-alphabet-profile.v2"),
        "request": request,
        "request_sha256": hashlib.sha256(canonical_json(request).encode()).hexdigest(),
        "finite_field": {
            "prime": FINITE_FIELD_PRIME,
            "primitive_twenty_fifth_root": ROOT_OF_UNITY,
            "twenty_fifth_power_image": alphabet,
        },
        "zero_shift_characters": zero_shift_characters,
        "equations": ("For every listed character a, sum_i zeta^(-a*i)*y_i=0, where y_i=x_i^25 and zeta=5 in F_101."),
        "classification": (
            "Every F_101-rational solution has y_(r+5*q)=c_r for five "
            "independent values c_r in {0,1,10,100,91}. Thus its coordinate "
            "support is a union of complete five-coordinate residue blocks."
        ),
        "support_block_histogram": {str(active_blocks): count for active_blocks, count in computation["support_block_histogram"].items()},
        "computation": computation,
        "execution_limits": {
            "primary_wall_seconds": _TIMEOUT_SECONDS,
            "primary_hard_address_space_bytes": None,
            "python_checks": "in_process_without_a_separate_hard_memory_or_wall_limit",
        },
        "certificate": certificate,
        "certificate_verified": verified,
        "decision": {"status": "F101_PURE_POWER_ALPHABET_CLASSIFIED" if verified else "PROFILE_INVALID"},
        "interpretation": (
            "This is an exact classification of F_101-rational points on the "
            "fourteen pure-power equations. It reduces a rational-point search "
            "for the full five-pivot common-zero or singular locus to unions "
            "of the five residue blocks."
        ),
        "not_certified": [
            "the same classification over the algebraic closure of F_101 or in characteristic zero",
            "a common zero of the remaining eight five-pivot equations",
            "an evaluated Jacobian rank drop at any full-system common zero",
            "global dimension, smoothness, integrality, or characteristic-zero lifting",
            "a Brauer class, period, index bound, or counterexample",
        ],
        "next_gate": {
            "feature": ("level25_heisenberg_balanced_five_pivot_residual_block_search"),
            "requirement": (
                "Parameterize x on each active residue block by one scale and "
                "four mu_25 phases, then search the remaining eight equations "
                "and the full twenty-five-column Jacobian over F_101."
            ),
        },
    })
