---
name: conjecture-audit
description: Design, run, and critique computational experiments around mathematical conjectures while separating exact computation, numerical evidence, counterexample search, heuristic inference, and proof. Use when testing patterns, stress-testing a proposed theorem, checking finite cases, or turning agent-generated observations into reproducible research artifacts.
---

# Conjecture Audit

Treat computation as an experiment with a declared domain, method, and failure model.

## Method

1. Restate the conjecture with explicit quantifiers, domain, conventions, and exceptional cases. Identify which part is actually being tested.
2. Write down cheap invariants and boundary cases before running a broad search. Include zero, one, smallest admissible objects, degenerate inputs, and transitions between regimes.
3. Use `scientific_calculate` for bounded exact arithmetic. Prefer exact rational outputs over decimal approximations; record the full expression and tool limits.
4. When physical quantities occur, use `unit_convert` and `constant_lookup`. Carry dimensions, CODATA edition, uncertainty, and exact-versus-measured status through the computation.
5. Search for counterexamples adversarially: vary parameters independently, concentrate near singular or extremal cases, and test equivalent formulations whose numerical stability differs.
6. Save a reproducible artifact containing the hypothesis, sampling strategy, code or expressions, software versions, seeds, bounds, successes, failures, and raw counterexamples.
7. Classify the outcome precisely: falsified; survived the tested range; supported by a heuristic; reduced to another claim; or proved. Only the last classification is proof.
8. Before accepting any verdict controlled by a displayed formula, make a substitution table containing the formula, every exact input, the normalized inputs required by the formula's conventions, the substituted expression, its raw result, and the prose conclusion. Audit every load-bearing side condition or exception in a separate column with its source or independent derivation; a condition does not become valid merely because the proposer attached it to the formula. Recompute the table independently of the proposer. If a side condition is unsupported, or the raw result and prose disagree, retract or block the verdict and preserve the contradiction as evidence.
9. Run a final contradiction scan across formulas, tables, prose conclusions, outcome labels, and cited hypotheses. Repeated agreement by roles that read one another is one dependency, not independent corroboration.

## Guardrails

- Never replace universal quantifiers with a finite test without saying so.
- Do not use floating-point agreement as equality; state tolerance and conditioning when approximation is unavoidable.
- A failed counterexample search does not increase logical certainty by itself.
- Check for selection bias when a model proposes both the conjecture and the experiments.
- Preserve negative results and mismatches rather than optimizing the artifact for a clean narrative.
- Never inherit an extra precondition from the proposer without deriving or sourcing it. Apply the formula to the exact inputs first, including unit parts, signs, boundary values, and neutral elements. For a discrete-valuation formula stated in terms of unit parts, explicitly write each input as a uniformizer power times a unit before reduction; never reduce the nonunit itself to zero and call the unit-part expression ill-defined.

Read [references/experiment-checklist.md](references/experiment-checklist.md) when planning or reviewing a computational experiment.
