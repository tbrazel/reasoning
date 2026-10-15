---
name: arithmetic-geometry-research
description: Investigate elliptic curves over the rationals and number fields with LMFDB while keeping models, labels, conjectural invariants, completeness limits, and provenance explicit. Use for identifying curves or fields, comparing arithmetic invariants, locating examples, and turning database observations into carefully qualified mathematical questions.
---

# Arithmetic Geometry Research

Use LMFDB to locate objects and evidence. Do not turn a database record into a theorem without checking the mathematical status of each field.

## Method

1. State the object and normalization precisely. For an elliptic curve, record the base field and Weierstrass coefficients; for a number field, record a defining polynomial or exact LMFDB label.
2. Search exact labels first with `lmfdb_elliptic_curves_q` or `lmfdb_number_fields`. For broader searches, use the smallest set of invariant filters that expresses the question and keep the result bound low.
3. Check whether the returned model is the model intended by the question. Curves in one isogeny class can have different coefficients and integral-point data; isomorphic number fields can have different defining polynomials.
4. Separate algebraic, analytic, and conditional fields. In particular, do not silently identify algebraic rank with analytic rank, and preserve `used_grh` when reporting number-field data.
5. Follow the returned object URL and primary references when a claim depends on completeness, computation methodology, or a subtle invariant.
6. Independently reproduce tractable invariants with local exact computation when possible. Record software, version, assumptions, and commands in the artifact.
7. Report the exact query URL, retrieval date, LMFDB labels, fields compared, mismatches, and the status of every conclusion: database observation, independently checked computation, conjectural inference, or proof.

## Guardrails

- LMFDB searches are bounded and the database has collection-specific completeness limits; absence from a result is not nonexistence.
- Never infer equality of curves, fields, or representations from a few shared invariants.
- Treat ranks, Tate–Shafarevich group orders, and GRH-dependent values according to the status recorded by the source and its supporting page.
- Retain LMFDB attribution and CC-BY-SA licensing with saved data.
- If network tools are unavailable, state that limitation rather than fabricating labels or records.

Read [references/lmfdb-method.md](references/lmfdb-method.md) before interpreting labels, pagination, completeness, or conditional fields.
