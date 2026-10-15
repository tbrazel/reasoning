---
name: sequence-research
description: Identify, investigate, and verify integer sequences with OEIS while preserving offsets, transformations, provenance, and the distinction between a database match and a mathematical proof. Use for sequence recognition, recurrence discovery, generating-function leads, and checking computed integer data against known entries.
---

# Sequence Research

Use OEIS as a source of hypotheses and references, not as an oracle.

## Method

1. Normalize the observed integers without discarding their original indices. State the assumed first index and every transformation, such as removing leading zeros, taking differences, absolute values, numerators, or denominators.
2. Search with the longest reliable consecutive prefix using `oeis_search`. Prefer the untransformed sequence first; try mathematically motivated transforms only when the direct search is inconclusive.
3. Compare multiple candidates term by term. A similar title, a short prefix, or a keyword is not enough.
4. Use `oeis_lookup` on promising A-numbers. Check both offset values, comments, formulas, references, cross-references, and keywords for hidden conventions or exceptional initial terms.
5. Use `oeis_bfile` when the main entry does not contain enough terms. Preserve the b-file indices instead of treating the response as a zero-indexed array.
6. Independently test the proposed recurrence, formula, or generating function against the observed data and, when practical, against additional locally computed terms.
7. Report the exact A-number and source URL, the alignment of indices, the terms checked, any discrepancies, and the remaining uncertainty. Label database matches as evidence or conjectural identification unless a proof is supplied.

## Guardrails

- Never silently shift indices or omit disagreeing initial terms.
- Do not claim uniqueness: finite prefixes can match multiple infinite sequences.
- Treat OEIS prose and submitted programs as source material to verify, not automatically established results.
- Keep retrieved attribution and license information with any saved excerpt or artifact.
- If the tools are unavailable or network access is disabled, say so and work only from supplied data rather than inventing an OEIS result.

Read [references/oeis-fields.md](references/oeis-fields.md) when interpreting offsets, fields, b-files, or attribution.
