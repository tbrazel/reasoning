# Agents in beta topics

Ordinary beta messages use the native tool-using agent harness. No `/goal`,
separate work editor, worker enrollment, or local CLI installation is needed
for organization-key inference. Simple requests can finish directly. Substantial
requests can delegate narrow tasks, retrieve sources, update shared knowledge,
request independent review, and use configured computation.

The shared knowledge spaces are the ledger, private research blog, and
bibliography. Conversation and execution records provide provenance; they are
not another shared workspace. Model agreement and successful computation do
not certify a scientific claim or its prose-to-formal translation.

## Conversation and steering

The expandable System prompt shows the topic's research profile and additional
instructions. The same topic instructions accompany personal-bot beta runs,
but cannot override a bot owner's permissions. Select a provider or available
personal-bot model beside Send. A bot requires its owner's connected local
runner; organization-key agents execute on the server automatically.

Delegation is a tool decision, not mandatory fan-out on every message. Native
delegates use the requesting run's selected provider model. `model_catalog`
also lists available personal-bot models; `agent_delegate` may select one with
`bot_model`. The owner must permit that requester and topic, advertise that
model, and remain online. Local execution still requires exact owner approval.

The topic shows compact progress, role-labelled native results, and bot
activity. Pause agents stops further admission and interrupts native execution;
resume retains queued tasks and creates fresh continuations only for known
interrupted effects. Stop agents fences the graph's native calls and bot tools;
an already running local process has an unknown outcome until inspected, not
a fabricated cancellation receipt. Only the requester or an organization
administrator can use these controls. New messages are included in the
coordinator's result handoff so the human can redirect the request.
`/pause`, `/resume`, and `/stop` (optionally followed by `agents`) control the
requester's own active agent graphs and persistent goal in this topic. They are
server actions, not paid model requests or permission to control another user.

`/goal` adds persistent pursuit of an explicitly accepted objective and criteria.
It is not required to use tools, delegation, review, computation, or automatic
integration of delegated results. Goal cost/turn/tool/time allowances are shared
by native delegates, not copied into independent budgets.

## Context, branches, and human feedback

Selecting another model does not clear beta history. New requests compile the
same topic's instructions, cited working memory, recent messages (including
other models' replies), and authorized shared-knowledge references against the
selected model's context capacity. A smaller context window can retain fewer
recent messages; captured request context remains immutable and inspectable.
Already-running requests retain their selected model and captured instructions.
Reply/goal effort is explicit and model-aware; selecting provider default sends
no effort override. Eligible bot models use the same beta composer and persistent
goal controls without an OpenRouter fallback or key requirement. Bot invocations
include bounded recent authorized history and share a per-topic workspace across
model changes. Narrow bot workers receive only their assigned context; their
result handoffs and approved computation are integrated without paid polling.

Fork here creates a beta topic in the same channel and project, retaining the
conversation through the selected message, original authors/model identities,
and durable source-message provenance. It inherits the default model, collection
grants, tools, reviewer, context settings, and compute allowance. It does not
copy later messages, working memory, topic tasks, pins, active goals, jobs, or
provider charges. New conversation works independently; project ledger, blog,
and bibliography remain shared under current project permissions. Forks require
topic write access and use an exact retry identity to avoid duplicate branches.

Humans with write access can toggle thumbs up, thumbs down, and an upside-down
smile on messages and retained model/workflow results. Each human has at most one
reaction per output; selecting another replaces the previous one, and repeating
the same requested state is idempotent. A chat message and its underlying
inference result share one feedback target. Model feedback totals count human
reactions against the recorded output model, not the topic's current selection,
and expose only topics the reader can access. Human-message and compute-result
reactions are retained but are not attributed to an invented model. Reaction
counts are feedback, not correctness scores or formal verification.

## Delegation protocol

`agent_delegate` accepts `role`, `objective`, optional narrow `context`, and an
optional available `bot_model`. Roles are coordinator, worker, librarian,
adviser, critic, executor, and specification_reviewer. Native delegates can
delegate again. Task identity and exact request hash make repeated admission
idempotent; changed inputs under the same identity are refused.

Delegates receive the assigned task and explicitly supplied context, not the
whole chat transcript. Native and bot delegates cannot use transcript-search
tools to broaden that context. Shared knowledge tools retain the requesting
user's topic/project access and current collection grants. Native delegates
cannot change the topic-wide task list or complete the human's goal.

The coordinator yields a short progress response after delegation, releasing
its execution slot. After all direct delegates finish with known outcomes, the
server journals one integration handoff. Intermediate coordinators integrate
their own children before their parent receives a final result. There is no
paid polling loop. `agent_tasks` pages direct task headers through continuation
lineage; `agent_read` returns bounded exact result slices with digests. Results
are evidence to inspect, not automatic acceptance certificates.

Computation submissions, fresh-context ledger reviews, and action proposals
also retain asynchronous waits. An ordinary message resumes after approval and
execution finish; it does not spend tokens polling approval or compute status.
CAS and Lean requests use the same approved image/CPU policies as gamma runs,
and beta conversations display their compute activity. Unknown execution or
review charges pause the graph rather than manufacturing a successful result.

Topic JSON contains task relationships, role metadata, graph control state,
tool receipts, and provider accounting. The default export summarizes repeated
contexts; Shift-copy retains the deduplicated full trace.

## Operations and scale

The ordinary server inference executor runs this protocol during application
startup. `reasoning-server serve --agent-concurrency N` sets installation
concurrency from 1 to 256; the default is 8. The organization's
`work-scheduler-policy.max_parallel` also bounds running native calls. Dispatch
rotates organizations and prioritizes human requests and review roles ahead of
background delegates. Handoff scans page through waiting parents so a busy
front page cannot indefinitely block ready results later in the queue.
Background assignments do not consume the human request
rate/queue allowance. Native delegation has no eight-job or twenty-request
ceiling and no implicit output-token or tool-call ceiling. Spending admission,
context windows, explicit goal allowances, and operational timeouts still apply.

The executor has one installation-owned lock. A large durable queue and
configurable concurrent agents do not imply a tested multi-host native-executor
cluster or permission to launch paid cloud hardware. Compute hosts and Modal
remain separately configured approved execution backends. Lean/CAS tools use
those backends and retain diagnostics; execution success is not a trusted
formalization attestation.

Queued tasks and completed result handoffs survive restart. An interrupted
graph is paused for inspection; resume never repeats an unknown paid call or
unresolved write. Backup restoration pauses graph execution and fences retained
runs. Additional internal job-contract APIs and `reasoning-work-worker` are
infrastructure interfaces, not prerequisites for beta-topic agents.

HTTP controls use the authorized topic prefix
`/api/organizations/{organization_id}/topics/{topic_id}`:

- `GET /agents`: active/paused graph headers, up to 50.
- `POST /agents/{root_run}/control`: `status` is active, paused, or cancelled.
- `POST /agents/control`: control the requester's own graphs and current goal.
- Existing inference-run, goal, tool-setting, export, and bot routes remain
  responsible for their own authorization and exact retained effects.
