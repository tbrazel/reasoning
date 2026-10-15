# Harness architecture

Reasoning owns the durable control plane for projects, chats, workflows,
benchmarks, tools, permissions, budgets, provenance, and rendered events. An
agent harness is a replaceable execution engine beneath that control plane.
No project object assumes that a particular harness or inference provider is
installed.

## Three independent compatibility axes

Reasoning evaluates a run across three separate axes:

1. The **harness** owns the agent loop, prompt mediation, session lifecycle,
   streaming, tool dispatch, approvals, steering, interruption, compaction,
   and checkpoint behavior.
2. The **execution target** owns workspace access, process execution,
   isolation, network enforcement, and workspace durability. A target may be
   the host checkout, a local OS sandbox, a remote container or VM, or a
   stateless service.
3. The **inference route** owns the provider, model, and model-specific
   features and prices.

The same harness can use different inference routes and execution targets. A
cloud-hosted workbench can therefore expose a remote Reasoning harness backed by
sandbox targets without introducing a different chat or workflow data model.

## Desktop hybrid planning reference

`DesktopHybridPlanner` is the OSS, dependency-light reference for consuming the
accepted platform records on the Desktop side. It keeps harness, inference, and
compute location independent, resolves all eight local/hosted combinations,
validates every record against its exact repository-owned schema, checks
descriptor digests and cross-record capability bindings, and emits only a
credential-free route display. Route-plan and pricing records are negotiated as
an inseparable pair: alpha.1 remains supported, while alpha.2 is accepted only
with alpha.2 pricing. Harness, inference, compute-target, project-bundle,
local-connector, and Executor dependencies remain alpha.1. Unknown versions,
mixed pairs, pricing identity substitutions, and attempts to give alpha.1
records alpha.2 meaning fail closed. The route display identifies both negotiated
contracts and pricing disposition without exposing credentials.

An alpha.2 release before dispatch is descriptive evidence, not execution
authority. The planner verifies empty dispatch and usage evidence, exact
quote/reservation/release chronology, a reason coherent with quote expiry,
zero actual amount, and complete atomic reservation release. Such a route is
marked non-dispatchable, requires neither transfer nor connector, is not retained
as a connector authorization, and cannot be used to approve a project transfer.
Matrix resolution validates every route/pricing pair before retaining any route
authorization, so a rejected matrix cannot leave a partially authorized subset.

The reference reads the canonical contract assets from the Reasoning source
tree during source development. The frozen Desktop backend embeds the exact
alpha.1 and alpha.2 schema, compatibility, and bundle-manifest bytes directly
from those same canonical directories; the general Python package does not
contain another copy. Desktop startup verifies the accepted bundle digests,
every packaged member digest, schema identity, and exact version before health
can report the planner ready. `DesktopHybridPlannerFactory` is registered only
in local application state and creates planners from exact records supplied by
a later integration. This runtime does not discover providers, invoke a
harness, perform account login, transfer a project, or add a planning endpoint
or Desktop UI.

An account connection leaves a local project local and creates no transfer.
`approve_project_transfer` accepts an explicit approval bound to the exact
route-plan and pricing pair, project-bundle bytes, actor, project revision,
selected input metadata, retention disclosure, and one-step transfer identifier.
It limits a transfer to 32 inputs and 64 MiB, omits local paths, harness state,
and authentication material, and returns a durable local authorization
containing the exact explicit approval. Attachment parses that approval again and
re-derives the manifest from the exact route-plan and project-bundle bytes. It
keeps the extra route anti-transplant receipt in Desktop state instead of
extending the public wire schema.
`DesktopTransferReceiptStore` persists that immutable receipt in local SQLite,
keyed by the canonical attached bundle, transfer, and exact route-plan digest.
Hosted use fails closed when the receipt is missing, corrupt, ambiguous, or does
not bind the manifest, actor, tenant, operation, project revision, and project
contents presented after restart. The store requires an existing canonical
private parent, holds stable directory/database identities, and opens SQLite
only in memory. Every authoritative snapshot is loaded from and saved to the
already-bound database descriptor under a file lock, so a mutable pathname
never selects receipt state. The store refuses sidecars and rechecks path,
owner, mode, link count, inode, and size bindings around every operation.

`ScopedComputeConnector` is the matching provider-neutral reference for a
local harness using hosted compute. An injected transport owns authentication
and HTTP. The connector enforces the accepted compute-only grant's exact HTTPS
audience, route, actor, tenant, project, target, image, expiry, revocation,
fresh nonce, one-job binding, and approved compute inputs. Exclusive opaque
event cursors are committed only after a complete page validates and a
whole-projection version check covers state, terminal outcome, cursor, events,
and artifacts. Event state cannot regress or continue after a terminal event.
The connector accepts only a route authorization known to the originating
planner. Before using any image or connector binding, it makes that planner
re-resolve the retained accepted route-plan, pricing, and project records and
requires every route and pricing identity, version, digest, and disposition
claim to match exactly. A pre-dispatch release is rejected before connector
state or transport is touched. An interrupted or stale transport leaves the previous operation
projection intact. Artifact bytes must match both the committed digest and
size. The in-memory replay, revocation, and grant-use stores are deterministic
fixture references and must be replaced by
shared durable implementations in a serving deployment.

## Contract boundary

`reasoning_ledger.harnesses` is the dependency-light contract package. Its
public types contain no subprocess, HTTP, Electron, FastAPI, or provider SDK
implementation details.

- `HarnessCapabilities` describes the agent loop.
- `ExecutionTargetCapabilities` describes the security and persistence
  boundary where tools run.
- `HarnessRequirements` and `preflight_harness` perform explicit compatibility
  checks. Features are never inferred from a model or harness name.
- `HarnessSessionSpec`, `HarnessTurnRequest`, `HarnessEvent`, and
  `HarnessTurnResult` are the control-plane data plane.
- `HarnessAdapter` is the synchronous session protocol. Adapters may perform
  asynchronous or remote work internally while delivering ordered events to
  the supplied sink.
- `InteractiveHarnessAdapter` adds typed steering, context injection,
  approvals, and interruption for persistent chat harnesses.

Reasoning visibility means only model- or harness-supplied summaries. Reasoning
does not represent private chain-of-thought as a portable capability.

## Product selection and live preflight

`HarnessSelectionCatalog` is the product-facing compatibility layer. It keeps
four controls independent: harness, inference route, execution target, and
reasoning effort. `InferenceRoute` describes a credential-free provider/model
route, while `SelectedRunConfiguration` records the exact user choice. The
catalog resolves those identifiers and combines route checks with
`preflight_harness`; the browser receives the resulting issues and never
infers support from names.

Route readiness is transient deployment state. It can block live preflight,
but credentials and readiness diagnostics do not enter workflow revisions or
harness checkpoints. A `SessionBinding` exposes the harness, provider, target,
and model already pinned by a persistent session. Live preflight rejects a
change to any of those values and tells the user to start a new chat; reasoning
effort remains a turn-level choice when the selected route and harness support
it.

Each chat exposes the catalog and its default selection through
`execution-options`, accepts arbitrary selections through
`execution-preflight`, and repeats preflight when the message is submitted.
Research chats default to Reasoning Native, OpenRouter, and mediated project
tools. Development chats require a harness and target that satisfy the complete
development capability profile; the built-in default is Codex with the
registered local read-write workspace. The React composer renders all four
controls, shows concrete incompatibility details, and disables send while
compatibility is unknown or false.

Serving deployments extend this built-in catalog through
`HarnessDeploymentRegistry`. Harness adapters, inference routes, and execution
targets are registered independently and filtered by research or development
chat mode. Adapter factories receive only stable project, chat, and Reasoning
session identities; endpoint configuration, clients, and credentials remain
deployment-owned memory. A factory must return the exact capability snapshot
that was advertised, or the turn is rejected.

On the first deployed-harness turn, the chat atomically records its stable
session identity, route, model, provider, and exact harness and target
capability snapshots. Later turns resume that binding. A missing deployment or
changed snapshot fails closed instead of silently switching harness, route, or
security boundary. Operations independently record the exact harness, target,
route, model, and provider used for attribution.

## Explainable automatic routing

`route_automatically` ranks a finite set of exact
`SelectedRunConfiguration` candidates. Every candidate first passes the same
live catalog preflight as an explicit user selection. Operator policy can then
restrict harnesses, providers, targets, observed reliability, cost, latency,
benchmark coverage, and the required kind of evidence.

The router optimizes a declared quality, reliability, cost, latency, or
balanced objective. Its result includes every rejected issue and every scored
component, its weight, contribution, and evidence source. Task observations
are considered only when their fingerprint exactly matches the requested
task. Catalog cost estimates are labeled as estimates and never treated as
quality evidence. Equal scores use a stable selection-identity tie-break, so
the same catalog, requirements, policy, and evidence produce the same result.

The chat composer supplies a finite candidate set from the live execution
catalog and OpenRouter's current account-visible text-model catalog. The user
chooses an objective and asks Auto to select; Auto applies one concrete
harness, provider route, model, target, and reasoning effort to the ordinary
controls without starting the turn. Its disclosure shows the selected
configuration, compatible-candidate count, score components, evidence source,
and aggregate rejection reasons. The prompt itself does not enter an operation
record; a SHA-256 fingerprint of normalized whitespace associates later
observations with an exact repeated task.

Completed chat operations record that task fingerprint, exact run identity,
reasoning effort, duration, usage, and provider-reported cost when available.
Balanced chat routing requires at least observed or catalog cost evidence;
quality, reliability, and latency objectives require their corresponding
task-specific observations and fail closed when those observations do not
exist. Catalog prices remain visibly labeled estimates.

Workflow Auto uses rankings derived only from durable runs whose immutable
workflow revisions have the same task fingerprint. It never substitutes live
catalog estimates for benchmark results or realized workflow cost. The node
inspector exposes this workflow-level selection only when the graph has one
agent role; heterogeneous graphs need role-specific evidence before per-node
Auto routing can be presented honestly. The chosen exact configuration is
applied to the editable draft and is not executed until the user starts the
workflow.

The routing engine itself remains a control-plane primitive. A product surface
must supply its finite candidate set and task-scoped evidence; it cannot ask
the router to discover models or infer capabilities from names.

`HarnessChatEventMapper` is the provider-neutral rendering boundary for
non-Codex persistent chats. It translates canonical message, reasoning, tool,
progress, usage, and terminal events into the durable transcript and browser
event vocabulary. Tool activity is intentionally sanitized: name, call ID,
status, and attribution are visible; arguments and results are not copied into
chat activity records.

## Workflow event transport

Workflow runs retain their orchestration events, such as node and work-item
start and completion, because those records describe the graph scheduler rather
than an agent harness. Each executor event is also projected into a canonical
`harness.event` record. The parent record carries workflow scope (node,
iteration, case, and variant), the agent-run identifier, and a nested
`HarnessEvent` wire record whose session is the workflow run and whose turn is
the agent run.

Message and reasoning deltas are coalesced while they continue to arrive. A
subsequent delta flushes a batch after 250 milliseconds or 2,048 characters;
the next non-delta event and agent completion also flush pending text. This
bounds durable-write churn without changing executor order. A merged record
retains the newest sequence plus the first source sequence. The complete
executor-native stream remains in the agent run log.

Only presentation-safe compatibility fields enter the parent workflow record:
message or provider-supplied reasoning text, tool name/call ID/outcome, bounded
usage values, artifact attribution, checkpoint ID, and terminal error text.
Tool arguments and results never cross this projection boundary. Generic run
timelines consume the canonical lifecycle and activity vocabulary, while
Compare Models can show bounded provider reasoning when an executor emits it.

## Codex app-server adapter

`CodexAppServerHarnessAdapter` is the first persistent implementation of the
contract. It owns the Codex subprocess, initialization handshake, thread
creation and resume, provider affinity, turn transport, approvals, steering,
interruption, context injection, compaction notifications, subagent activity,
and usage aggregation. Raw app-server notifications become ordered canonical
`HarnessEvent` records before they reach the control plane.

`ChatSession` owns the Reasoning side of the boundary: durable transcripts,
browser subscribers, operation records, tasks, goals, reviews, writers, and
ledger-observation invalidation. Persistent harness turns enter through its
common session/turn worker. It does not own a harness client or interpret a
provider's notifications.

The current browser still consumes its established event shapes. During this
compatibility period the Codex adapter keeps a detailed rendering event only
until the matching canonical event is consumed. This ephemeral rendering path
is not a durable harness record. Canonical tool events retain attribution and
status but omit raw arguments and results, which may contain credentials or
large provider-specific payloads.

## Existing executor bridge

Workflow executors continue to implement the stable one-shot `AgentExecutor`
contract in `reasoning_ledger.agents`. `legacy_executor_capabilities` projects
their existing declarations into the harness capability vocabulary so the
control plane can migrate one adapter at a time.

## Remote service adapter

`RemoteHarnessAdapter` runs the common synchronous adapter contract over a
deployment-supplied `RemoteHarnessClient`. It rechecks the advertised harness
capability snapshot before opening a session, rejects target or inference-route
substitution, and uses the stable Reasoning session and turn identifiers as
idempotency keys. A lost create or submit acknowledgement can therefore be
retried without creating a second session or turn.

Event streaming resumes strictly after the last accepted positive sequence.
Replayed events are deduplicated, identity is checked before publication, and
the complete terminal trace is checked by `validate_harness_trace` before its
result is accepted. A stream that ends without a terminal event reconnects a
bounded number of times. Reasoning does not move the turn to another harness.
Steering, context injection, approvals, and interruption are forwarded only
while the matching session and turn are active.

`HttpRemoteHarnessClient` supplies the versioned reference wire protocol:

- `GET /v1/harness` describes the live capability snapshot.
- `POST /v1/sessions` idempotently creates or resumes a session.
- `PUT /v1/sessions/{session_ref}/turns/{turn_id}` idempotently submits a
  turn.
- `GET .../events?after_sequence=N` streams newline-delimited canonical event
  objects, and `GET .../result` returns the terminal result.
- Turn controls use `POST .../interrupt`, `POST .../steer`, and
  `POST .../context`; session approvals use `GET .../approvals` and
  `POST .../approvals/{request_id}`.
- `DELETE /v1/sessions/{session_ref}` releases the serving lease. The remote
  service retains whatever stable session state it needs for a later
  idempotent resume.

Opaque references are path-encoded. Non-local services require HTTPS unless a
deployment explicitly permits insecure transport. Authentication headers come
from an in-memory callback on every request and never enter a session, request,
event, result, exception, browser payload, or project file. Event and JSON
response sizes are bounded before decoding, malformed remote data produces a
generic error that does not reflect attacker-controlled values, and transport
errors expose retryability without endpoint or credential details. The
deployment owns HTTP client lifetime and certificate policy.

## Reasoning reference harness

`ReasoningNativeHarnessAdapter` is the compact Reasoning-owned persistent harness.
It delegates each bounded turn to the existing `ReasoningNativeExecutor`, while
the adapter owns fresh and resumed sessions, canonical event translation,
cooperative interruption, transcript compaction, and atomic checkpoints.

Ordinary Reasoning Native research chats use this adapter directly. Their fixed
per-chat checkpoint resumes normalized conversation history across turns,
while current project and ledger surroundings are injected as system context
for the turn rather than persisted as user messages. The Stop control requests
cooperative interruption through the adapter.

The adapter resolves its inference client through an injected
`CompletionClientResolver`. Provider and model remain pinned session data, but
no provider SDK or endpoint is part of the session contract. It likewise
receives only the `ToolInvoker` granted for the selected execution target; it
has no direct workspace or process API. The built-in capability profile
therefore advertises no workspace access and no MCP support.

The JSON checkpoint pins the harness capability/version snapshot, model,
provider, execution-target capability snapshot, sandbox policy, normalized
conversation history, resolved prompt hashes, turn budgets, usage and cost,
compaction audit records, original source messages, and a digest of the semantic
state. Resume fails closed if the harness, route, target, policy, or digest changes.
Token pressure triggers mathematical working-memory summaries using the same
cited definitions, hypotheses, notation, claims, open questions, decisions,
references, and artifact contract as organization beta agents. A bounded recent
tail accompanies this memory. Original messages remain in the session's source
archive, and `research_source_read` retrieves exact slices by cited source ID.
`inspect_memory` and `read_source` expose the same evidence to local callers.

Summary calls are recorded with purpose `compaction` and contribute to the turn's
usage and cost. They consume its remaining output-token and time limits. For a
finite cost limit, the adapter requires known prior cost and an injected
`compaction_cost_bound` before making a summary call. An exhausted or unpriced
budget, invalid citations, or failed summary leaves the complete history intact
and records a deferred compaction. Context preparation retries before the next
answer using its new turn budget, deducting summary usage before starting the
agent loop. If preparation cannot safely fit, the answer is refused before
provider submission. The adapter never substitutes an omission marker.

Interruption is cooperative. It is checked before and after provider calls and
tool dispatch and while waiting to retry a rate-limited request. An arbitrary
blocking completion client cannot be stopped inside its own call unless that
client provides a cancellation mechanism; the turn settles as interrupted at
the next executor boundary.

Native session state remains opaque to other harnesses. Cross-harness
continuation uses the portable semantic checkpoint described below and never
claims that one harness can resume another harness's private session.

## Portable semantic checkpoints

A completed provider-neutral chat operation can export an immutable,
content-addressed `PortableCheckpoint`. It contains normalized visible user
and assistant messages, credential-free source attribution, optional relative
content-addressed artifact references, and the canonical event stream's
side-effect assessment. Reasoning traces, raw tool activity, authorization
values, harness session handles, and provider-private continuation state are
excluded. Import verifies the exact SHA-256 content digest, rejects malformed
paths and credential-shaped fields recursively, and preserves message roles.

`TurnSafetyTracker` treats tool activity, file changes, accepted approvals, and
artifact progress as potential effects. An incomplete event stream is unknown.
A harness-emitted checkpoint event is recorded but does not establish safety.
Automatic continuation is eligible only when a complete stream contains no
observed potential effect, or when a user explicitly promotes an immutable
checkpoint boundary after reviewing the resulting state. Promotion creates a
new checkpoint and leaves the unpromoted record intact.

Explicit handoff creates a fresh destination chat. The imported messages are
immediately visible in its exact transcript and the first compatible harness
turn receives them as untrusted semantic context. Contract-backed harnesses
receive the original roles directly. Codex receives an explicit untrusted-data
boundary and role-labeled text because its app-server turn input does not
accept historical roles. The destination operation records the imported
checkpoint identifier. Reasoning never silently retries or moves an in-flight
turn after a possible effect.

The chat transcript offers this action on the final assistant message of each
completed operation. A checkpoint with no observed effects continues directly
after the user's click. A checkpoint with possible or unknown effects exposes
the server's fail-closed reason and requires a written user attestation before
promotion and handoff. Imported transcript messages display their handoff
provenance, and a development handoff keeps the source workspace attachment
unless the caller explicitly selects another target.

## Audit rules

- Capability declarations are explicit immutable data. The built-in Codex and
  Reasoning Native profiles and the legacy-executor projection are covered by
  contract tests.
- `preflight_harness` reports every harness or target mismatch and treats any
  reported issue as incompatible.
- Session specifications pin a complete execution-target capability snapshot;
  a harness cannot substitute a weaker target after preflight.
- Reasoning reference checkpoints are written atomically and verify their
  semantic-state digest before resume. Fresh sessions refuse to overwrite an
  existing state path.
- Session and turn metadata, event data, usage, and cost records reject
  credential-like keys recursively. Turn checkpoints receive the same check.
  MCP access crosses the contract as stable connection IDs, not endpoint
  configuration or authorization values.
- `validate_harness_trace` checks session and turn identity, monotone event
  order, one matching terminal event, and consistency between emitted events
  and advertised capabilities.
- Remote session and turn creation is idempotent; reconnect cursors are exact,
  replay is deduplicated, terminal traces are revalidated, and arbitrary
  client and decode failures cannot reflect their contents into Reasoning errors.
- Auto routing is deterministic, task-scoped, preflighted, and explainable;
  policy incompatibilities are hard failures rather than score penalties.
- Portable checkpoints are immutable and digest-verified. Automatic
  continuation eligibility fails closed for incomplete streams and possible
  effects without an explicit promoted boundary.
