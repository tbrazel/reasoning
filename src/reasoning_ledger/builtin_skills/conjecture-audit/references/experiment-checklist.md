# Computational experiment checklist

## Before computation

- Exact conjecture, domain, quantifiers, and equivalence conventions
- Known results and examples used to form the conjecture
- Falsification criterion and expected failure modes
- Sampling plan, bounds, random seed, and stopping rule
- Exact arithmetic, interval arithmetic, or numerical tolerance decision

## During computation

- Record input generators and canonicalization rules
- Keep exact expressions or source code, software and package versions
- Record time, memory, and tool safety limits
- Separate source data from normalized or derived data
- Retain the first counterexample and enough context to reproduce it

## After computation

- Report all tested ranges, not only successful subranges
- Recheck surprising results with an independent implementation when feasible
- Explain gaps caused by timeouts, unsupported inputs, or missing data
- Distinguish a database lookup, exact local computation, approximate result,
  heuristic, conditional statement, and proof
- State the strongest justified conclusion and a concrete next test or lemma

For physical constants, cite the 2022 CODATA source and propagate standard
uncertainty. The pinned Reasoning catalog is intentionally small; consult
https://physics.nist.gov/constants for correlations and additional values.
