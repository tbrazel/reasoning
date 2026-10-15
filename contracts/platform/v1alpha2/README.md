# Pre-dispatch pricing release contracts

This directory is the canonical `1.0.0-alpha.2` wire contract for `pricing`
and `route-plan`. It defines a truthful terminal representation for a reservation
released before inference or compute dispatch.

The unchanged platform descriptors and execution contracts remain exact
`1.0.0-alpha.1` dependencies. `compatibility.json` lists each dependency.
Consumers negotiate `pricing@1.0.0-alpha.2` and
`route-plan@1.0.0-alpha.2` together; a route plan declares
`pricing_contract_version` explicitly.

## Release states

| Terminal state | Dispatches | Usage | Required evidence |
| --- | ---: | ---: | --- |
| `released` / `pre_dispatch` | empty | empty | reason, release time, full reservation release |
| `released` / `post_dispatch` | nonempty | nonempty | zero quantities, dispatch IDs, actual provenance |
| `settled` | nonempty | nonempty | dispatch IDs, actual usage, actual provenance |
| `outcome_unknown` | nonempty | nonempty | dispatch IDs, explicit unknown usage, retained reservation |

A pre-dispatch release has sequence `3`, after quote sequence `1` and
reservation sequence `2`. Its reason is either `quote_expired`, for a release
at or after quote expiry, or `cancelled_before_dispatch`, for a release before
expiry. It releases the complete reservation with zero actual amount. Its
closed settlement shape has no usage digest or actual-provenance field, and
the enclosing record requires empty `dispatches` and `usage` arrays with no
dispatch or usage entries.

The conformance semantics reject any request ID, job ID, dispatch time,
provider/model execution evidence, Executor job or image evidence, artifact
claim, actual usage, or actual provenance in a pre-dispatch pricing lifecycle.
Selected routes and targets remain in the paired route plan; they are planned
capabilities, not claims that either service ran.

## Migration

An alpha.1 pricing record is valid only under the alpha.1 schema and semantic
suite. Do not delete a synthetic alpha.1 dispatch and relabel the record.
Producers that need a truthful pre-dispatch release must negotiate alpha.2,
emit a paired alpha.2 route plan, and create a new alpha.2 lifecycle from the
durable quote, reservation, release time, and release reason.

Consumers that support only alpha.1 must reject alpha.2. Consumers that support
both versions validate each against its own immutable bundle. Post-dispatch
release and `outcome_unknown` retain their dispatch, usage, and provenance
requirements in alpha.2.

`bundle-manifest.json` lists the byte digest of every schema, compatibility
record, conformance suite, and fixture. Its `bundle_digest` is SHA-256 over RFC
8785/JCS UTF-8 encoding of the manifest object with `bundle_digest` omitted.

Run both immutable bundles with:

```bash
pytest -q tests/test_platform_contracts.py tests/test_platform_contracts_alpha2.py
```
