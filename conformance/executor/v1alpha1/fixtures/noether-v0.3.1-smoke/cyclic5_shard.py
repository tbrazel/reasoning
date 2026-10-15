from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from time import perf_counter

import noether
from noether.commalg import PolynomialRing
from noether.numthy import FiniteField

NOETHER_VERSION = "0.3.1"
PRIMES = (101, 103, 107, 109)
CONSTRUCTION = {
    "family": "cyclic-5",
    "variable_count": 5,
    "variable_prefix": "x",
    "term_order": "grevlex",
    "equation_widths": [1, 2, 3, 4],
    "product_constant": -1,
}
EXPECTED = {
    101: {
        "basis_count": 20,
        "term_count": 232,
        "sha256": "2d0644dff58035b763b878c5cca0f80275457cf8f315cfe5484dc620bc6fac48",
    },
    103: {
        "basis_count": 20,
        "term_count": 232,
        "sha256": "6a384a7ad8b3ab73490e6f40b4393fdd5e149a422a39f674c4417f1b5bcc9517",
    },
    107: {
        "basis_count": 20,
        "term_count": 232,
        "sha256": "9ff7f9e580d3297a4f06784195643a275c28abd85ef01667a7b1ff18f20f068f",
    },
    109: {
        "basis_count": 20,
        "term_count": 231,
        "sha256": "4a51b01deac2c66a9e8dcc934fedb600795501c3ecfb7bd796681f2bf6aaf2c9",
    },
}

EXPECTED_LEADING_EXPONENTS = [
    [0, 0, 0, 2, 6],
    [0, 0, 1, 0, 7],
    [0, 0, 0, 1, 7],
    [0, 0, 0, 0, 8],
    [0, 0, 0, 3, 4],
    [0, 0, 2, 0, 5],
    [0, 0, 1, 1, 5],
    [0, 1, 0, 2, 3],
    [0, 0, 1, 2, 3],
    [0, 1, 0, 0, 5],
    [0, 1, 1, 1, 2],
    [0, 1, 1, 2, 0],
    [0, 0, 2, 2, 0],
    [0, 1, 0, 3, 0],
    [0, 0, 1, 3, 0],
    [0, 0, 0, 4, 0],
    [0, 1, 2, 0, 0],
    [0, 0, 3, 0, 0],
    [0, 2, 0, 0, 0],
    [1, 0, 0, 0, 0],
]
EXPECTED_LEADING_EXPONENTS_SHA256 = "e281cfc799942f167255604f4a416bf66c624a92f91eac12598a921bf225919a"


def cyclic_ideal(prime: int):
    variable_count = CONSTRUCTION["variable_count"]
    ring = PolynomialRing(
        FiniteField.prime(prime),
        [f"{CONSTRUCTION['variable_prefix']}{index}" for index in range(variable_count)],
        order=CONSTRUCTION["term_order"],
    )
    variables = ring.generators()
    equations = []
    for width in CONSTRUCTION["equation_widths"]:
        total = ring.zero()
        for start in range(variable_count):
            term = ring.one()
            for offset in range(width):
                term = term * variables[(start + offset) % variable_count]
            total = total + term
        equations.append(total)
    product = ring.one()
    for variable in variables:
        product = product * variable
    equations.append(product + CONSTRUCTION["product_constant"])
    return ring.ideal(equations)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("prime", type=int, choices=PRIMES)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    started = perf_counter()
    ideal = cyclic_ideal(args.prime)
    compute_seconds = perf_counter() - started
    ideal.verify()
    basis = ideal.groebner_basis()
    leading_exponents = [polynomial.terms()[0][0] for polynomial in basis]
    leading_exponents_sha256 = hashlib.sha256(json.dumps(leading_exponents, separators=(",", ":")).encode()).hexdigest()
    result = {
        "basis_count": len(basis),
        "leading_exponents": leading_exponents,
        "leading_exponents_sha256": leading_exponents_sha256,
        "noether_version": noether.__version__,
        "prime": args.prime,
        "seconds": round(compute_seconds, 6),
        "sha256": hashlib.sha256(ideal.to_json().encode()).hexdigest(),
        "term_count": sum(polynomial.nonzero_term_count for polynomial in basis),
        "verified": True,
    }
    invariant_keys = ("basis_count", "term_count", "sha256")
    observed = {key: result[key] for key in invariant_keys}
    if (
        noether.__version__ != NOETHER_VERSION
        or observed != EXPECTED[args.prime]
        or leading_exponents != EXPECTED_LEADING_EXPONENTS
        or leading_exponents_sha256 != EXPECTED_LEADING_EXPONENTS_SHA256
    ):
        raise RuntimeError(f"Noether v0.3.1 cyclic-5 invariant mismatch for F_{args.prime}: {observed!r}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
