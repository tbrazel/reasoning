# Cloud product-policy contract 1.0.0-alpha.1

This provider-neutral public boundary presents one exact account policy to web
and Desktop clients. It has exactly two domain records:
`reasoning.cloud-product-policy.account-policy` and
`reasoning.cloud-product-policy.optional-use-change`.

The account snapshot keeps inference and compute offers independent. Every
offer references an exact descriptor from its connected Cloud-client
capability snapshot and states price, finite limits, data path, and required
telemetry without activating any service. The three optional purposes are
independent and off by default. Their bounded decision lineage retains complete
historical policy terms; a 65th transition is rejected rather than truncating
history.

Mutation processing validates strict bytes, the idempotency key, connected
session and separate account authority, and the canonical request digest. One
store lock then performs exact replay lookup before current-state CAS. A new
request checks the complete snapshot, capability, entitlement, product-policy,
and purpose-decision bindings before one atomic transition.

The dependency manifest digest is SHA-256 over the accepted Cloud-client
manifest bytes. Its separately named internal bundle digest is SHA-256 over the
manifest's RFC 8785 JCS basis. Neither digest substitutes for the other.

## Digest bases

All JCS rows use UTF-8 RFC 8785 JSON Canonicalization Scheme bytes and SHA-256.
An omitted field means only that named derived field is removed; every other
field participates, including null values and `product_policy.state`.

| Digest | Input domain | Exact basis | Excluded derived fields |
| --- | --- | --- | --- |
| Capability snapshot | JCS | Complete accepted `reasoning.cloud-client.capabilities` object | None |
| Entitlement projection | JCS | Closed object containing exactly `id` and `version` | `digest` |
| Catalogue definition | JCS | Closed object containing exactly `catalogue_id` and `catalogue_version` | `catalogue_digest` |
| Plan definition | JCS | Closed object containing exactly `plan_id` and `plan_version` | `plan_digest` |
| Product-policy definition | JCS | Complete `product_policy` object, including `state`, `effective_at`, and `expires_at` | `digest` |
| Optional-use policy terms | JCS | Complete closed policy-terms object | `digest` |
| Decision-lineage entry | JCS | Complete closed history entry, including its full historical policy or null | `digest` |
| Account-policy snapshot | JCS | Complete `reasoning.cloud-product-policy.account-policy` object | `snapshot_digest` |
| Optional-use request | JCS | Complete `reasoning.cloud-product-policy.optional-use-change` object, including purpose, action, key digest, full consent policy, and every CAS term | `request_digest` |
| Accepted-record wrapper | Exact bytes | Exact bounded UTF-8 input bytes retained at acceptance | None; no JCS transformation |
| Offer descriptor reference | Exact bytes | Exact accepted canonical descriptor bytes named by the T030 capability reference | None; no JCS transformation |

The capability snapshot itself therefore has a JCS digest, while each
descriptor reference inside it has the SHA-256 digest of exact accepted bytes.
The two domains are intentionally distinct.
