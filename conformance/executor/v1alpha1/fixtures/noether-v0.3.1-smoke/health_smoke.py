from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from time import perf_counter

import noether
from noether import Q
from noether.commalg import PolynomialRing

CONSTRUCTION = {
    "coefficient_ring": "Q",
    "variables": ["x", "y"],
    "term_order": "lex",
    "generator_coefficients": [[1, 1], [1, -1]],
}
EXPECTED = {
    "basis_count": 2,
    "leading_exponents": [[1, 0], [0, 1]],
    "noether_version": "0.3.1",
    "sha256": "dbbdda3865b1b4dae69b029d69c993adb47b53b1195b0e2afdb7128065924c9c",
    "verified": True,
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    started = perf_counter()
    ring = PolynomialRing(Q, CONSTRUCTION["variables"], order=CONSTRUCTION["term_order"])
    x, y = ring.generators()
    generators = [x_coefficient * x + y_coefficient * y for x_coefficient, y_coefficient in CONSTRUCTION["generator_coefficients"]]
    ideal = ring.ideal(generators)
    ideal.verify()
    basis = ideal.groebner_basis()
    result = {
        "basis_count": len(basis),
        "elapsed_seconds": round(perf_counter() - started, 6),
        "leading_exponents": [polynomial.terms()[0][0] for polynomial in basis],
        "noether_version": noether.__version__,
        "sha256": hashlib.sha256(ideal.to_json().encode()).hexdigest(),
        "verified": True,
    }
    observed = {key: result[key] for key in EXPECTED}
    if observed != EXPECTED:
        raise RuntimeError(f"Noether v0.3.1 health invariant mismatch: {observed!r}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
