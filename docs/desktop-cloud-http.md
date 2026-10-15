# Desktop Cloud private HTTP surface

`reasoning_ledger.desktop_cloud_http` is the sole HTTP adapter between an
authenticated Desktop process and the Cloud-owned control plane. This is a
private service interface, not a new public contract. Request and response
records remain exact bytes governed by `cloud-client@1.0.0-alpha.1`,
`cloud-product-policy@1.0.0-alpha.1`, or the referenced immutable platform
bundle.

The configured endpoint is one HTTPS origin with no path, query, fragment,
userinfo, loopback, private literal address, or reserved hostname. Every route
below is relative to that origin. Every request carries `Authorization: Bearer
<access-token>`; the token is forbidden from URLs, bodies, public records,
errors, and Desktop state. Redirects, cookies, content encodings other than
identity, and environment proxy settings are rejected. JSON responses use
`application/json`; artifact content uses `application/octet-stream`.

| Method | Path | Request | Successful response |
| --- | --- | --- | --- |
| `GET` | `/desktop/v1/session` | none | exact Cloud-client session bytes |
| `POST` | `/desktop/v1/session/connect` | `{}` | exact connected session bytes |
| `POST` | `/desktop/v1/session/disconnect` | `{}` | exact disconnected session bytes |
| `GET` | `/desktop/v1/capabilities` | none | exact capabilities bytes |
| `POST` | `/desktop/v1/contracts/resolve` | canonical JSON `ContractRecordReference` | exact referenced platform record bytes |
| `GET` | `/desktop/v1/projects` | none | `{"records":["<base64 exact project bytes>"]}` |
| `PUT` | `/desktop/v1/projects/{cloud_project_id}` | base64 exact project bytes and sorted `{path,bytes_base64}` contents | exact project-bundle bytes |
| `POST` | `/desktop/v1/runs` | base64 exact run and project bytes | exact run bytes |
| `GET` | `/desktop/v1/runs/{operation_id}` | none | exact run bytes |
| `GET` | `/desktop/v1/runs/{operation_id}/events` | optional `after` cursor | exact run-event-page bytes |
| `POST` | `/desktop/v1/runs/{operation_id}/cancel` | `{}` | exact run bytes |
| `GET` | `/desktop/v1/artifacts/{artifact_id}/metadata` | none | exact Executor artifact metadata JSON |
| `GET` | `/desktop/v1/artifacts/{artifact_id}/content` | none | exact artifact bytes |
| `GET` | `/desktop/v1/account-policy` | none | exact account-policy bytes |
| `POST` | `/desktop/v1/account-policy/optional-use-changes` | exact optional-use-change bytes | exact account-policy bytes |
| `GET` | `/desktop/v1/collaboration/bootstrap` | none | canonical organization/workspace bootstrap |
| `GET` | `/desktop/v1/organizations/{organization_id}/members` | none | active organization members and roles |
| `POST` | `/desktop/v1/organizations/{organization_id}/members` | email, role, and idempotency key | invited organization member |
| `GET` | `/desktop/v1/organizations/{organization_id}/workspaces/{workspace_id}/topics` | none | canonical shared topics |
| `POST` | `/desktop/v1/organizations/{organization_id}/workspaces/{workspace_id}/topics` | topic title plus idempotency key | canonical shared topic |
| `GET` | `/desktop/v1/organizations/{organization_id}/workspaces/{workspace_id}/topics/{topic_id}/messages` | optional `after` cursor | canonical ordered message page |
| `POST` | `/desktop/v1/organizations/{organization_id}/workspaces/{workspace_id}/topics/{topic_id}/messages` | message content plus idempotency key | canonical shared message |
| `POST` | `/desktop/v1/organizations/{organization_id}/workspaces/{workspace_id}/topics/{topic_id}/inference-requests` | credential-free inference request plus idempotency key | queued operation identity |
| `GET` | `/desktop/v1/organizations/{organization_id}/workspaces/{workspace_id}/topics/{topic_id}/operations` | none | canonical visible prompt activity |
| `POST` | `/desktop/v1/organizations/{organization_id}/workspaces/{workspace_id}/topics/{topic_id}/operations/{operation_id}/cancel` | `{}` | terminal cancellation receipt |
| `PUT` | `/desktop/v1/organizations/{organization_id}/runners/{runner_id}` | canonical runner registration | canonical runner registration |
| `POST` | `/desktop/v1/organizations/{organization_id}/runners/{runner_id}/leases/claim` | none | canonical runner lease or no-content response |
| `POST` | `/desktop/v1/organizations/{organization_id}/runners/{runner_id}/leases/{lease_id}/start` | `{}` | running lease receipt |
| `POST` | `/desktop/v1/organizations/{organization_id}/runners/{runner_id}/leases/{lease_id}/heartbeat` | visible progress | current lease receipt |
| `POST` | `/desktop/v1/organizations/{organization_id}/runners/{runner_id}/leases/{lease_id}/finish` | terminal result and optional provider usage | terminal lease receipt |

Project put, run submit, run cancel, and optional-use change carry the raw
bounded idempotency key in `Idempotency-Key`. Public records retain only its
canonical digest. IDs and cursors are bounded safe identifiers. Project upload
is limited to the canonical 64 MiB content cap and a 96 MiB encoded request;
individual JSON records are limited to 1 MiB, collections to 8 MiB, and
artifact content to 64 MiB.

The Cloud service must scope every route from the validated bearer, preserve
provider-owned operations across Desktop disconnect and process restart, and
enforce idempotency before mutation. The Desktop's local session wrapper returns
a disconnected session until the user explicitly connects, even when the
service can already project an authenticated session. The Desktop independently revalidates
session, capability, actor/tenant, descriptor, project, run, event, policy, and
artifact bindings before exposing a result.

Collaboration is available after browser authentication and does not depend on
connecting the legacy hosted-execution session. Bootstrap returns every active
organization membership, its workspaces and permissions, and stable default
personal organization and workspace identities. The renderer remembers the
last selected organization and workspace locally but falls back to those
server-owned personal defaults.

Runner registration and polling are outbound-only. A user explicitly opts a
local Codex installation into selected organizations and workspaces; the
Desktop advertises bounded capabilities and policy, claims only compatible
queued work, and executes it with network denied and read-only sandboxing.
Codex may use shell tools only for bounded read-only calculation and is
instructed not to inspect local files. Every claimed prompt and its progress are
shown on the contributing Desktop and in shared workspace activity; the Codex
thread is persistent and tagged for Reasoning shared-workspace history.

The separately selected metered route uses the assigned Desktop's existing
OpenRouter configuration. API keys remain local. Provider-reported tokens and
cost are copied into the shared operation and the Desktop's prompt-free usage
ledger. A Desktop advertises this route only while OpenRouter is configured.
Queued work is capped at 20 active requests per account and lease heartbeats,
cancellation, expiry, and `outcome_unknown` make interrupted work explicit.
