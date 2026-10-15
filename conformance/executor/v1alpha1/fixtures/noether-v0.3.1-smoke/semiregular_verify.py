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
SIZES = (6, 7)
CONSTRUCTION = {
    "family": "semiregular",
    "prime": 65521,
    "variable_prefix": "x",
    "term_order": "grevlex",
    "coefficient_generator": "64-bit LCG, seed 0x4D595DF4D0F33173, multiplier 6364136223846793005, increment 1442695040888963407",
    "coefficient_map": "state % (prime - 1) + 1",
    "term_order_of_generation": "quadratic terms x_i*x_j for i <= j, then linear terms, then the constant, per equation",
}
EXPECTED = {
    6: {
        "basis_count": 39,
        "term_count": 2028,
        "ideal_json_bytes": 447289,
        "sha256": "fd111468ad97a761ecbb57a5d2493bb63d87ca59c154f4be31f844db4977edfb",
    },
    7: {
        "basis_count": 67,
        "term_count": 6714,
        "ideal_json_bytes": 1482973,
        "sha256": "1247f57d6ef4d4d4c4de74ceb8966da70703a6ff15a29eb7c5c9edd662888081",
    },
}


def semiregular_terms(size: int, prime: int) -> list[list[tuple[list[int], int]]]:
    state = 0x4D595DF4D0F33173

    def coefficient() -> int:
        nonlocal state
        state = (state * 6_364_136_223_846_793_005 + 1_442_695_040_888_963_407) & ((1 << 64) - 1)
        return state % (prime - 1) + 1

    equations = []
    for _equation in range(size):
        terms: dict[tuple[int, ...], int] = {}
        for left in range(size):
            for right in range(left, size):
                exponent = [0] * size
                exponent[left] += 1
                exponent[right] += 1
                key = tuple(exponent)
                terms[key] = (terms.get(key, 0) + coefficient()) % prime
        for index in range(size):
            key = tuple(1 if position == index else 0 for position in range(size))
            terms[key] = (terms.get(key, 0) + coefficient()) % prime
        key = tuple([0] * size)
        terms[key] = (terms.get(key, 0) + coefficient()) % prime
        equations.append([(list(exponent), value) for exponent, value in sorted(terms.items()) if value])
    return equations


def semiregular_ideal(size: int):
    prime = CONSTRUCTION["prime"]
    ring = PolynomialRing(
        FiniteField.prime(prime),
        [f"{CONSTRUCTION['variable_prefix']}{index}" for index in range(size)],
        order=CONSTRUCTION["term_order"],
    )
    return ring.ideal([ring.from_terms(terms) for terms in semiregular_terms(size, prime)])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("size", type=int, choices=SIZES)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    started = perf_counter()
    ideal = semiregular_ideal(args.size)
    compute_seconds = perf_counter() - started
    started = perf_counter()
    ideal.verify()
    verify_seconds = perf_counter() - started
    basis = ideal.groebner_basis()
    canonical = ideal.to_json().encode("utf-8")
    result = {
        "basis_count": len(basis),
        "compute_seconds": round(compute_seconds, 6),
        "ideal_json_bytes": len(canonical),
        "noether_version": noether.__version__,
        "sha256": hashlib.sha256(canonical).hexdigest(),
        "size": args.size,
        "term_count": sum(polynomial.nonzero_term_count for polynomial in basis),
        "verified": True,
        "verify_seconds": round(verify_seconds, 6),
    }
    invariant_keys = ("basis_count", "term_count", "ideal_json_bytes", "sha256")
    observed = {key: result[key] for key in invariant_keys}
    if noether.__version__ != NOETHER_VERSION or observed != EXPECTED[args.size]:
        raise RuntimeError(f"Noether v0.3.1 semiregular-{args.size} invariant mismatch: {observed!r}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
