# Internal durable job execution

`WorkJobs` provides a durable job protocol for [general work contracts](work-contracts.md).
It journals assignments and effects. The inference endpoint brokers organization
provider calls; scoped work tools operate on authorized project records, not
arbitrary local commands. Worker credentials are separate
from user sessions and carry explicit project, role, and concurrency grants.

## Topic interface and infrastructure

[Beta-topic agents](topic-agents.md) use the server-managed native inference
executor and ordinary chat interface. No separate worker enrollment is needed.
The ledger, blog, and bibliography are their shared knowledge spaces.

The internal job-contract API below supports explicitly enrolled
`reasoning-work-worker` processes with project/role grants. It is separate from
the native topic executor. An ordinary server upgrade starts the native executor,
not an enrolled job-contract worker. Internal controller activation and exact
input inspection use API requests rather than a standalone work editor.

Internal compute requires approved organization images, explicit tool/image
grants and configured compute workers. Execution receipts are not
`tests_passed` or `proof_checked` attestations; trusted verifier adapters are not
installed by this protocol. Human/source check submission uses the API.

## Administration

At `/api/organizations/{organization_id}`:

- `POST /work-workers`: administrator enrollment with `name`, `project_ids`,
  `roles`, `max_parallel`, and optional `human_slots`/`review_slots`. The response
  returns the secret token once; store it privately. Database state keeps its hash.
- `DELETE /work-workers/{worker_id}`: revoke enrollment and request cancellation.
- `GET|PUT /work-scheduler-policy`: read or administratively set organization
  `max_parallel`, `human_slots`, and `review_slots`.

At the project work program prefix
`/api/organizations/{organization_id}/projects/{project_id}/work/{program_id}`:

- `POST /control`: `expected_revision`, `status=active|paused|cancelled`, `reason`.
- `POST /tasks/{task_id}/jobs`: `expected_epoch`, optional `lane=human|review|research`.
- `GET /jobs/{job_id}`: job header and retained result with its digest.
- `GET /jobs/{job_id}/events`: sequence-paged brief agent and provider stream events.
- `POST /jobs/{job_id}/cancel`: stop queued work or request running cancellation.
- `GET /jobs/{job_id}/effects`: cursor-paged accounting headers, not raw receipts.
- `POST /effects/{effect_id}/reconcile`: owner/administrator reconciliation with
  `expected_request_sha256`, `state=settled|rejected`, both actual resource amounts,
  and retained receipt description. A live worker must first stop or be fenced.
- `POST /tasks/{task_id}/reopen`: `expected_epoch` and `reason`; unresolved effects
  must be reconciled before authorizing another attempt.

Cancellation is an intent, not evidence that a remote process stopped. Pausing a
program stops new claims and requests running cancellation. Completed or cancelled
programs cannot be resumed by ordinary control. Task reopening advances its epoch,
retains previous results, and fences previous attempts.

## Worker protocol

Use `Authorization: Bearer rnaw_…`, never a user cookie or user token. Endpoints:

- `POST /api/work-worker/claim` with a stable `claim_id`.
- `POST /api/work-worker/jobs/{job_id}/heartbeat`.
- `POST /api/work-worker/jobs/{job_id}/context`.
- `POST /api/work-worker/jobs/{job_id}/inputs/read`.
- `POST /api/work-worker/jobs/{job_id}/compact`.
- `POST /api/work-worker/jobs/{job_id}/result`.
- `POST /api/work-worker/jobs/{job_id}/effects`.
- `POST /api/work-worker/jobs/{job_id}/effects/{effect_id}/receipt`.
- `POST /api/work-worker/jobs/{job_id}/inference`.
- `GET /api/work-worker/calls/{call_id}`.
- `POST /api/work-worker/jobs/{job_id}/progress`.
- `POST /api/work-worker/jobs/{job_id}/tools/{tool_name}` for [scoped work tools](work-tools.md).
- `POST /api/work-worker/jobs/{job_id}/computations/{compute_job_id}/wait`.

A claim is `null` when no authorized slot/assignment is available. Otherwise it
contains `dispatch`, job identity, `attempt_id`, `fence`, secret `lease`, expiry,
and a narrow packet containing the pinned task specification, role prompt, latest
internal compaction, and their digests. Claims
do not include organization secrets or complete chat histories. Reusing a claim ID
returns the same attempt, not another job; only `dispatch=true` permits execution.
Non-dispatch replays do not return a task packet. Revoking the worker's project
grant also hides its live context and assigned input content. Current role grants
are rechecked for tools, new effects, context and successful completions.

`context` takes the usual lease fields and returns the current scoped packet.
`inputs/read` adds `reference`, `offset` (default 0), and `length` (1–16,000
characters, default 12,000). Exact references assigned to the task, including
bindings created by authorized source discovery/result inspection, are
readable, even if the original user can read other project records. Responses
include the reference, text slice, total characters, and truncation flag.
Referenced content is not substituted with its current head version.

`compact` adds stable `client_id`, `summary`, `retained_inputs`, `unresolved`,
`next_actions`, and `obstructions`. Retained references must belong to the task
and still resolve exactly. Notes and their provenance are immutable; replay with
changed content is refused. Internal notes survive lease reassignment and backup
recovery without copying full transcripts. They do not replace the specification,
source records, acceptance evidence, or the original result. This endpoint
records a worker-supplied summary; it does not perform an inference call itself.

The native work worker performs automatic context maintenance. It reads the
model's advertised capacity through the read-only leased `POST /model-info`
endpoint, and uses a conservative UTF-8/framing bound to compact completed
conversation groups before the context fills. Initial input previews share a
context-dependent allowance; exact originals remain accessible by slice.
The pinned system prompt, objective, acceptance criteria, and current complete
tool-call/result group are retained. Old native conversation messages are evicted
after their immutable journals and durable internal notes are retained.

Compaction uses `inference` with `purpose=compaction` rather than the default
`purpose=work`; it shares the model grants, credit reservations, ancestor budgets,
lease, prompt pinning, streaming and paid-call replay journal. Valid memory records
summary, unresolved questions, next actions, obstructions and exact bound inputs.
Malformed confirmed responses can receive fresh authorized repair calls without
an implicit retry ceiling. Unknown paid outcomes and lost note receipts stop
submission; journal replay recovers the same operations. A journal pins its
context-maintenance mode before execution so resumptions do not change request
identities. Model memory is untrusted navigation, not acceptance evidence.
If the pinned specification, schemas or current indivisible tool group cannot
fit, the assignment needs a larger-context model or narrower capabilities;
compaction cannot erase those obligations to make a request appear valid.

`progress` adds stable `client_id`, `phase`, and a summary of at most 400 characters.
It retains immutable brief events, rejects conflicting replays, and requires the
live assignment and current project authority.

## Brokered inference

`inference` takes lease fields, a stable `client_id`, and `payload`: model, text/tool
messages, and optional granted tool schemas, tool choice, reasoning settings,
response format, or explicit `max_tokens`. Tools must exactly match the assignment's
advertised schemas. The server checks model grants, organization policy, and the
exact pinned system prompt, and the enabled encrypted organization key. A worker
cannot replace it or append another system message. The credential never leaves the server for
the worker. Routing policy is pinned to the call.

The server retains an effect intent before submission and journals the request
digest, quote, response, safe failure diagnostics, and actual provider usage.
Exact retries read retained outcomes instead of resubmitting. A preparation gap
after retaining an intent is uncertain and requires inspection/reconciliation;
it is not permission to repeat a possibly billed request. A retained `running`
call is also not a new submission authorization. Worker receipt endpoints cannot
overwrite brokered accounting. Usage received before a later stream error remains
billable evidence. HTTP 401/402/403 refusals before streaming release the hold;
unconfirmed charges retain it.

Chat and work inference share the organization's monthly credit accounting and
transactional reservation lock. Program and ancestor budgets remain additional
constraints. Output reservation uses a price estimate, not an implicit output
token limit. An omitted `max_tokens` is omitted on the provider wire. Shared stream
parsing has wire-size bounds but no numeric per-response tool-call ceiling.
Only enrolled workers assigned the leased job can upload inference bodies up to
32 MiB; ordinary API requests retain their smaller body allowance.

## Executable project worker

Install the Python distribution, enroll a worker through the administrator API,
and privately enter its returned `rnaw_…` credential:

```sh
reasoning-work-worker configure --server https://chat.example.org
reasoning-work-worker run --once
reasoning-work-worker run
```

The default private state is `$XDG_STATE_HOME/reasoning/work-worker`, or
`~/.local/state/reasoning/work-worker` when XDG state is unset. Use `--state` before
the subcommand to select a separate enrollment. Configuration files and journals
are owner-only; one process holds the state lock. Configuration refuses an
existing credential file rather than silently replacing enrollment.

The worker chooses the first granted model, or the explicit `run --model` within
each assignment's grants. It uses the pinned seven-role/strategy prompts, latest
internal compaction, and exact advertised tools with the native agent loop.
Assigned input heads are read by exact version, at most 16,000 characters per
input, with explicit truncation metadata. Larger inputs remain available through
scoped slice reads. Controller packets retain the original target specification
and require structured review output for review/checking jobs.
Server-tool output references are retained in the worker's result. Controller
successors pin those exact outputs alongside the prior result, so reviewers can
read captured sources without discovering a mutable head or a guessed ID.
Inference and tool RPCs retain stable identities before crossing the network.
Lost replies retry those identities; terminal-result recovery does not start a
fresh agent. Heartbeats run during inference, and failed renewal stops further
spending. The server independently checks cancellation and grants during streams.

The worker supplies no implicit token, turn, tool-count, or total-run deadline;
authorized server envelopes enforce explicit limits. Individual network exchanges
retain transport timeouts. Brief run/tool events and provider content/reasoning
events are available through the job event page. Full agent output remains in its
private journal and retained provider responses; the bounded job summary enters
review, not accepted completion. Provider failures stop the assignment for
inspection rather than selecting another paid route automatically. The worker
queues approved images through `compute_submit` and awaits their retained outcome
with lease heartbeats and scoped status polling. It does not launch local code,
bots, or compute images itself. Polling consumes no model calls or extra tool
quota; interrupted waits resume the same compute identity. See
[approved computation](work-tools.md#approved-computation) for authorization,
reservations, cancellation and the limits of execution evidence.

Compute wait takes the ordinary attempt/fence/lease envelope and returns status,
the exact terminal receipt and committed output headers. Polling detects an
expired compute-host lease without requiring an unrelated browser/API read.
Unknown execution stops the native assignment for inspection and retains its
CPU reservation; it does not wait forever, invent completion or launch a retry.
`compute_output_read` reads bounded receipt-bound files through the same scoped
tool protocol, including files explicitly delegated to a critic.

## Controllers

At the program prefix, `POST /controllers` starts a controller with stable
`client_id`, `expected_revision`, and optional pending `target_task`. The program
must be active. Omitting the target creates a root assignment from its current
specification. `GET /controllers/{controller_id}` reports phase, version, current
job, exact result/review references, error and missing checks. `GET
/controllers/{controller_id}/steps` uses `after`/`limit` sequence pagination.

The three controllers execute durable state transitions:

- `explore_review_revise`: specification review, candidate work, critical review,
  and revision until approval and all requested checks are present.
- `parallel_integrate`: coordinator work, independently controlled/reviewed
  children, integration of exact accepted child results, then critical review.
- `build_check_repair`: work, an executor checking assignment, repair when needed,
  and a separate critical validation assignment. A checking agent's text is not
  itself a test or proof receipt.

Review JSON has `decision=approve|revise|blocked`, a summary, and an assessment of
every exact target criterion. Malformed reviews receive new repair assignments;
the old job and paid outcome are retained, not resubmitted. Specification
objections require human revision. Candidate objections create new revision tasks
under the same semantic target and shared ancestor resource accounts. Librarian
and other specialized task roles remain specialized when revising.

An automated review must match the final retained `purpose=work` server-provider response for
that job/fence. Submission sequences establish call order even when several
rounds share a timestamp. Matching critical approval records `llm_review` with
`source=server_model_review`; worker assertions alone do not satisfy that check.
Critical and specification reviewers receive only read/discovery tool grants.
Compaction responses cannot substitute for candidate/review output or satisfy
an acceptance check.
Current controller state and program revision are rechecked before and during
provider submission.

Checks are immutable and pin the exact candidate result. Model approval cannot
substitute for `human_review`, `source_check`, `tests_passed`, or `proof_checked`.
Missing checks leave the controller visibly waiting in `acceptance`. `POST
/controllers/{controller_id}/checks` accepts human/source attestations only:
stable `client_id`, `expected_version`, exact `subject`, `criterion_id`, `check`,
`passed`, explanation and exact-version evidence. Refusal starts a revision;
approval wakes acceptance. Test/proof checks need trusted verifier adapters,
not this human endpoint. These controllers do not promote mathematical claim
states or manufacture proof certificates.

Parallel integration pins its accepted child-result set and reduces it through
eight-result batches when necessary. Each batch has its own specification and
critical review, explicitly limited to faithful partial integration rather than
full-project coverage. Additional levels reduce reviewed batches until the final
coordinator sees at most eight aggregate results plus decomposition. Every level
retains exact inputs, shares the semantic target's ancestor budgets, and uses
bounded database reads. Controller headers expose the reduction level, fan-in,
stage and pinned leaf count through persisted counters. New branches under a
pinned decomposition are refused; a revised controller can capture a new set.
Revision rounds retain separate reduction snapshots instead of reusing old
accepted inputs or deleting their provenance.
Only internal controller steps can extend that frozen parent, with all resource
and tenant checks still enforced. Root acceptance still checks
the original criteria; batch approval does not establish them.

`POST /controllers/{controller_id}/cancel` and `/resume` take `expected_version`
and `reason`. Cancellation requests stop for the target subtree, cancels queued
jobs and retains completed work/unknown charges. A failed job is not silently
replayed: reconcile unknown effects, explicitly reopen and queue its task, then
resume the blocked controller. A known-charged transient protocol/network failure
can instead produce a fresh recovery assignment under the existing authorization;
unknown charges, credit refusals and cancellation do not trigger that retry.
Program specification changes block pinned controllers. Cancel/restart against
the new revision to redirect their work.

Work events maintain an indexed program wake frontier. Independent API
maintenance consumes bounded program/controller pages under program transaction
locks. Each transition uses a savepoint so a refused successor cannot leave
partial assignments or discard its prior result. Multiple consumers do not create
duplicate successor jobs. A root controller finalizes its program only after
other controllers and live/uncertain jobs have finished. This is not a measured
fleet-capacity guarantee or evidence that domain verifiers are installed.

Mutations require the exact attempt, fence, and lease. Leases last 60 seconds and
heartbeats renew them. Every API process runs independent five-second lease
maintenance. PostgreSQL dispatch is serialized per organization and accounting
per program; competing maintenance processes use the same lock order. SQLite
serializes writes and is intended for small installations.

An effect intent includes `client_id`, `request_sha256`, `kind`, held monetary/CPU
amounts, and exactly one capability: `model` for inference, `image_id` for compute,
or `tool` for local execution. Current project authority, worker grants, root and
ancestor envelopes, and organization model/image policy are rechecked. The first
intent returns `submit=true`. Its exact replay returns `submit=false`: do not
resubmit an external operation merely because a network reply was lost.

Receipts distinguish `settled`, `rejected`, and `unknown`. Settled receipts require
both actual amounts; rejected operations report no charge; unknown outcomes retain
their reservation. Worker receipts are labeled worker reports, not independent
provider attestations. Human reconciliation records its separate source and pins
the request digest. Actual charges can exceed an estimate; accounting records
them rather than silently clipping them to the reservation.

Completion records retain immutable canonical JSON and SHA-256. Exact completion
replays are idempotent; conflicting or stale results are refused. A successful
job moves its task to `review`, not accepted `complete`. Acceptance kinds remain
distinct requested checks; a worker summary is not a proof certificate.

## Fairness and resources

Queued task count is independent of concurrency. Dispatch checks organization,
worker, program, and ancestor concurrency. Uncertain work retains capacity until
explicitly reconciled/reopened. Human and review lanes reserve unfilled slots;
remaining choices rotate by least recent lane/project/program/branch dispatch.
Enrollment defaults reserve one human slot at concurrency 2 and one additional
review slot at concurrency 3; administrators can set explicit values.

Resource accounts are incrementally updated in the same transaction as effect
intents and receipts. Each effect charges its program and every task ancestor.
Sibling tasks therefore share their parent's budget rather than receiving copies.
Optional turn/tool ceilings count intents, not retries. Omitted numeric ceilings
are unlimited; empty capability grants authorize nothing. Obsolete assignments
become visibly blocked rather than preventing unrelated work from dispatching.

Backup recovery revokes work workers, cancels queued jobs, pauses active programs,
and holds leased jobs with uncertain outcomes. Completed results and resource
accounts survive. Remote physical cleanup and charge reconciliation remain
operator responsibilities; restoration never automatically resubmits work.
