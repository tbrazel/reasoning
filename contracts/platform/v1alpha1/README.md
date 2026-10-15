# Portable hybrid platform contracts

This directory is the canonical `1.0.0-alpha.1` wire contract for moving a
Reasoning project and selecting its runtime services without collapsing them
into an opaque backend.

`HarnessProvider` is the serialized capability/session view of the existing
`HarnessCapabilities` and `HarnessAdapter` boundary. `inference-route` uses the
same stable identity and provider/model meaning as `InferenceRoute`.
`compute-target` projects `ExecutionTargetCapabilities` plus its accepted
Executor adapter. A `route-plan` pins those three selections independently,
along with the project persistence location and content digests; a preset is
only a decision source and never replaces the three records.

Local projects remain `unlinked` unless a user-approved project-link record is
present. Its approval binds the JCS digest of the exact tenant, Cloud project,
authorized input classes, retained output classes, and revocable future-transfer
policy. Any hosted participation in a route plan over local persistence names
an exact transfer manifest. That manifest lists every disclosed input and each
class of output, log, usage, metadata, or provenance retained by Cloud, with a
separate user-approved disclosure digest. Its actor, tenant, project, revision,
and operation binding must equal the route plan that consumes it. Route plans
name the exact project bundle as well as the transfer. Project exports accept
only declared fields and omit authentication material and server-only policy.

A local harness reaches hosted computation through the connector verifier
record. The record is audience-, actor-, tenant-, project-, operation-, plan-,
harness-, target-, expiry-, and budget-bound. It grants only one compute-job
lifecycle and explicitly denies account, billing, inference, and project-export
access. It contains no bearer value, refresh material, or reusable account
credential; authentication wire format and secure storage remain deployment
concerns. The user's approval binds the JCS digest of the connector audience,
expiry, complete route/operation binding, capability set, budget, replay policy,
and presentation restrictions.

`pricing` is one quote/reserve/dispatch/usage/settle envelope for inference,
compute, and hybrid operations. Potentially paid dispatches name a prior durable
reservation. Settlement binds observed inference-route or Executor image
provenance, atomically charges actual usage, and releases the remainder. An
ambiguous outcome keeps the reservation visible for reconciliation. Zero-priced
dimensions and free-quota reservations use the same record shape.
Every quoted component's maximum is its maximum quantity times unit price. A
`released` settlement permits only zero actual usage and releases the complete
reservation; an `outcome_unknown` settlement carries an explicit unknown usage
record, releases nothing, and retains the complete reservation for later
reconciliation.

Consumers negotiate prerelease contracts by exact version. The `$id` values under
`https://schemas.reasoning.invalid/` are local registry identifiers, not network
services; schemas are loaded from the bundled files. The normative compatibility
rules are `compatibility.json`. `bundle-manifest.json` lists the
byte digest of every schema, compatibility record, conformance suite, and
fixture. Its `bundle_digest` is SHA-256 over RFC 8785/JCS UTF-8 encoding of the
manifest object with `bundle_digest` omitted. The manifest itself is therefore
not a member and no circular digest is introduced.

JSON Schema enforces each record's closed shape. The conformance suite is also
normative for cross-record digests, identifier binding, time order, expiry,
money conservation, and approval binding that JSON Schema cannot compare.

Run the executable contract checks with:

```bash
pytest -q tests/test_platform_contracts.py
```
