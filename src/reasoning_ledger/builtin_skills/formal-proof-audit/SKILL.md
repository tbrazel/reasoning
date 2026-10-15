---
name: formal-proof-audit
description: Audit mathematical claims and Lean 4 formalizations using an installed core or mathlib toolchain while tracking statement fidelity, toolchain provenance, axioms, sorry placeholders, and the difference between elaboration, kernel acceptance, and an informal proof. Use when checking generated Lean, translating an argument into Lean, reviewing a claimed formal proof, or preserving a reproducible proof-check artifact.
metadata:
  reasoning:
    tools: ["lean_environment", "lean_check", "artifact_write"]
    capabilities: ["proof:inspect", "proof:check", "artifacts:write"]
---

# Formal Proof Audit

Treat a formalization as a correspondence between an informal claim, a precise Lean statement, and a kernel-checked term in a pinned environment. Audit all three.

## Method

1. Restate the informal claim with its quantifiers, domains, hypotheses, conventions, and intended conclusion. Identify ambiguity before writing code.
2. Use `lean_environment` to record whether core Lean and the configured local Lake/mathlib workspace are available. Do not install or update dependencies during an audit.
3. Write the smallest self-contained Lean source that expresses the claim. Pin imports deliberately; do not widen imports merely to make tactic search succeed.
4. Call `lean_check` with the exact source and environment. Supply every central declaration name in `declarations` so Reasoning appends `#print axioms` audits.
5. If Lean rejects the source, report the diagnostics as a failed check. Iterate on syntax or proof only after confirming the formal statement has not drifted.
6. If Lean accepts it, require `complete_without_sorry: true`. Review the axiom report and distinguish ordinary foundational or classical assumptions from custom axioms, `sorryAx`, or unexpected dependencies.
7. Compare the accepted Lean declaration back to the informal claim line by line. Check that no hypothesis was strengthened, conclusion weakened, type made empty, or edge case silently excluded.
8. Save a proof artifact with the informal statement, exact Lean source, source SHA-256, imports, audited declarations, toolchain version, environment, diagnostics, axiom report, and fidelity assessment.

## Interpretation

- `kernel_accepted: true` means the source elaborated and its declarations passed the selected Lean kernel.
- `complete_without_sorry: true` additionally means the emitted diagnostics did not report `sorryAx`; inspect named declarations to catch transitive placeholders.
- Neither field proves that the Lean proposition faithfully represents the user's mathematical sentence.
- Axiom use is not automatically an error. It is an explicit assumption ledger whose acceptability depends on the claim and intended foundation.
- A timeout, unavailable toolchain, or missing dependency is inconclusive, not a mathematical counterexample.

## Guardrails

- Never report “proved” when Lean rejected, timed out, used `sorry`, or the relevant declaration was not audited.
- Do not run `lake update`, add dependencies, or download caches as part of a check. Environment changes require separate user intent and a new audit.
- Do not hide warnings, truncate a failing goal in the written artifact, or omit the exact toolchain.
- Treat generated tactics and imported macros as executable local code. Run checks only in a write-capable node explicitly granted `proof:check`.
- Keep the informal-fidelity review independent from the agent that produced the formalization when the result matters.

Read [references/audit-checklist.md](references/audit-checklist.md) before making a final proof-status claim.
