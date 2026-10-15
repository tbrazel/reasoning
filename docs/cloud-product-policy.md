# Cloud product policy

`cloud-product-policy@1.0.0-alpha.1` is the canonical public account-policy
boundary shared by the web product and Desktop. It is a presentation and
mutation contract, not an authentication, payment, provider, collection, or
activation API.

The immutable bundle lives in `contracts/cloud-product-policy/v1alpha1` with
its conformance suite under `conformance/cloud-product-policy/v1alpha1`.
`CloudProductPolicyClient` validates the exact bundle and provides a
provider-neutral interface. The deterministic in-memory implementation models
two clients over one durable store for offline conformance testing.

An account-policy snapshot binds a separately supplied account authority to the
complete actor and tenant of one connected Cloud-client session. It then pins
the exact capability snapshot, entitlement projection, catalogue/plan,
product-policy definition, offer descriptors, and optional-use decision heads.
Inference and compute stay independent.

Price states are explicit: only `zero` with exact zero bounds means no charge.
An estimate has finite ordered bounds; `unknown` and `unavailable` carry no
amounts. Every offer has finite operation, concurrency, and spend limits.
Hosted availability requires a verified hosted data path and configured
required telemetry. Local availability requires a verified device-local path,
no subprocessors, no provider training, and no hosted telemetry processing.

Optional product analytics, research/evaluation, and model training are all off
by default. One grant or withdrawal changes only one purpose and never changes
required telemetry or starts a data operation. Full historical grant terms are
retained in a bounded public lineage so a reconstructed client can withdraw a
grant after the offered policy is superseded or unavailable. The store refuses
a transition when that lineage is full.

Exact valid replay returns the originally accepted snapshot bytes before
current-state CAS is consulted. Reusing a scoped idempotency key for changed
content conflicts; using a fresh key with stale bindings also conflicts.

## Runtime boundary

`CloudProductPolicyValidator` verifies the exact product-policy bundle, its
exact Cloud-client dependency, and strict accepted record bytes.
`AccountPolicyAuthority.from_connected` is the only supported authority
constructor: it binds a separately supplied account to the complete actor and
tenant of a semantically validated T030 session and capability snapshot, after
resolving every advertised descriptor by exact accepted-byte digest.

`CloudProductPolicyClient` exposes `account_policy()` and
`change_optional_use(change, *, idempotency_key=...)`. The raw key is validated
and compared to its public one-way digest but is never placed in a public
record. `InMemoryCloudProductPolicyStore` provides deterministic shared-client,
restart, concurrency, replay, and transition conformance without starting any
collection or external operation.

## Normative digest bases

JCS digests use SHA-256 over UTF-8 RFC 8785 JSON Canonicalization Scheme bytes.
Removing one derived field never removes any other field: nulls, timestamps,
purpose, action, every CAS term, and `product_policy.state` all participate.

| Digest | Domain | Exact canonical input | Exclusion |
| --- | --- | --- | --- |
| Capability snapshot | JCS | Complete accepted T030 capability object | None |
| Entitlement projection | JCS | Exactly `id` and `version` | `digest` |
| Catalogue definition | JCS | Exactly `catalogue_id` and `catalogue_version` | `catalogue_digest` |
| Plan definition | JCS | Exactly `plan_id` and `plan_version` | `plan_digest` |
| Product-policy definition | JCS | Complete product-policy object, including state, effective time, and expiry | `digest` |
| Optional-use policy terms | JCS | Complete closed policy-terms object | `digest` |
| Decision-lineage entry | JCS | Complete history entry, including full historical policy terms or null | `digest` |
| Policy snapshot | JCS | Complete account-policy record | `snapshot_digest` |
| Optional-use request | JCS | Complete change record, including purpose/action, idempotency-key digest, consent terms, and all snapshot/capability/entitlement/product-policy/decision CAS fields | `request_digest` |
| Accepted record | Exact bytes | Exact bounded accepted UTF-8 bytes | None; not JCS |
| Offer descriptor | Exact bytes | Exact canonical descriptor bytes pinned by the T030 capability reference | None; not JCS |

The raw T030 bundle-manifest dependency digest, accepted-record digest, and
offer-descriptor digest are byte domains. The T030 internal bundle digest and
the policy definition/projection/state digests are JCS domains. Implementations
must not substitute one for the other even when parsing yields the same object.
