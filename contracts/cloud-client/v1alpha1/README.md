# Public Cloud client contracts

This directory is the canonical `1.0.0-alpha.1` domain boundary between a
Reasoning client and Cloud-backed projects and runs. It does not define a network
transport or an authentication mechanism.

A session is a credential-free post-authentication view. A disconnected view
contains no identity. A connected view contains only the canonical actor,
tenant scope, and digest of the exact capability snapshot accepted for that
connection. Provider selection, browser redirects, tokens, cookies, refresh,
credential storage, and endpoint configuration remain outside every record.
Durable client resources are scoped by actor type, actor ID, and tenant ID.

Capabilities refer to exact canonical descriptor bytes by schema identity,
version, record identity, and byte digest. The session's capability snapshot
digest covers the complete capabilities record. Consumers resolve and validate
those accepted records before planning. Projects remain canonical platform
project bundles.
Runs embed the exact canonical route plan and, once terminal, its exact pricing
lifecycle. Event pages resume strictly after an opaque cursor, permit
at-least-once redelivery, and deduplicate by event identity. Artifact events
reuse the canonical Executor artifact record; content is exposed only after its
size and SHA-256 digest validate.

The compatibility record admits exact alpha.1 project, descriptor, connector,
Executor, Cloud-execution, and capability-manifest dependencies. Route plan and
pricing negotiate only as the exact pairs alpha.1/alpha.1 or alpha.2/alpha.2.
No dependency schema is copied into this family.

Connecting or disconnecting an account does not link or transfer a project.
Disconnect and client restart do not request run cancellation. Mutations are
idempotent under a caller-held key, while Cloud-client-owned fields retain only
its SHA-256 digest. A run request digest covers the JCS object
`{"mutation":"submit_run","run":...}` after omitting the top-level
`idempotency_key_digest`, `request_digest`, `route_plan_digest`, and
`pricing_digest` fields plus the embedded pricing operation's
`request_digest`; those derived bindings validate separately. Embedded
Platform records remain byte-compatible with their own accepted schemas.

`bundle-manifest.json` lists the byte digest of every schema, compatibility
record, conformance suite, and fixture. It also pins every external dependency
file by byte digest. Its `bundle_digest` is SHA-256 over RFC 8785/JCS UTF-8
encoding of the manifest object with `bundle_digest` omitted.

Run the executable checks with:

```bash
pytest -q tests/test_cloud_client_contracts.py tests/test_cloud_client.py
```
