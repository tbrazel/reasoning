# Formal proof audit checklist

## Statement fidelity

- Every informal quantifier and domain appears in the Lean declaration.
- Hypotheses have not been strengthened and the conclusion has not been weakened.
- Definitions, coercions, equality notions, finiteness conditions, and edge cases match the intended mathematics.
- The formalized type is inhabited where the informal claim presumes examples.

## Environment provenance

- Exact Lean version and, when applicable, Lake/mathlib workspace revision.
- Complete import list and any local modules on which elaboration depends.
- SHA-256 of the exact checked source, including appended axiom-audit commands.
- No package update, dependency resolution, or cache download during the check.

## Kernel and assumptions

- Exit status and complete bounded diagnostics preserved.
- `kernel_accepted` and `complete_without_sorry` reported separately.
- `#print axioms` output recorded for every central theorem.
- `sorryAx`, custom axioms, quotient soundness, propositional extensionality, and classical choice identified rather than collapsed into one “verified” label.

## Handoff status

Use exactly one of these outcomes:

- **Rejected by Lean** — diagnostics identify a syntax, elaboration, type, or proof failure.
- **Check incomplete** — toolchain unavailable, dependency missing, output truncated materially, or timeout reached.
- **Kernel accepted with placeholders or unaudited assumptions** — useful intermediate state, not a completed formal proof.
- **Kernel accepted and assumption-audited** — the exact formal declaration checked without reported `sorry`; statement fidelity remains a separate review.
- **Formally verified correspondence** — kernel acceptance, assumption audit, and an explicit informal-to-formal fidelity review all succeeded.

Official references:

- Lean: Validating a Lean Proof: https://lean-lang.org/doc/reference/latest/Validating-a-Lean-Proof/
- Lean: Axioms and `#print axioms`: https://lean-lang.org/doc/reference/latest/Axioms/
- Lake build system: https://lean-lang.org/doc/reference/latest/Build-Tools-and-Distribution/Lake/
- mathlib documentation: https://leanprover-community.github.io/documentation.html
