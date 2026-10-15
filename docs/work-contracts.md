# General work contracts

Internal job specifications describe objectives independently of any particular
research domain. Their task kinds are `explore`, `collect`, `build`, `review`, and
`verify`. A survey's acceptance criteria describe the survey deliverable, not the
truth of every claim it discusses.

## Versioning and authorization

The authenticated API lives at
`/api/organizations/{organization_id}/projects/{project_id}/work`:

| Method | Suffix | Operation |
| --- | --- | --- |
| POST | none | Create a draft program with `client_id` and `specification` |
| GET | none | List program headers using `after` and `limit` |
| GET | `/{program_id}` | Read the current specification, or `?revision=N` |
| PATCH | `/{program_id}` | Revise using `expected_revision`, `reason`, and `specification` |
| POST | `/{program_id}/tasks` | Create an immutable scoped task |
| GET | `/{program_id}/tasks` | List task headers with optional `status`, `after`, and `limit` |
| GET | `/{program_id}/tasks/{task_id}` | Read a task and its direct dependencies |
| POST | `/{program_id}/tasks/{task_id}/inputs/read` | Read a bounded slice of an exact assigned or bound input |
| GET | `/{program_id}/overview` | Current specification, incremental task counts, root controller, resource use and event cursor |
| GET | `/{program_id}/branches` | Bounded objective previews; optional `q`, `status`, `parent`, `after`, `limit` |
| GET | `/{program_id}/controllers` | Cursor-paged review/controller headers |
| GET | `/{program_id}/controllers/{controller_id}/checks` | Cursor-paged exact check headers |
| GET | `/{program_id}/controllers/{controller_id}/checks/{check_id}` | One retained check body and its digest |
| POST | `/{program_id}/relations` | Add an immutable knowledge relationship |
| GET | `/{program_id}/relations` | Page one exact version's incoming or outgoing relationships |

Specifications contain `objective`, `kind`, `role`, `controller`, `strategies`, `acceptance`,
`resources`, and `inputs`. The storage/API implementation is
`WorkSpecification` and `WorkStore`. A specification digest is SHA-256 of its
canonical UTF-8 JSON, including populated defaults. Revisions and provenance are
retained and immutable. Revision changes use compare-and-set; a stale edit is
HTTP 409. An exact retry matching the next retained revision's digest, reason
and author returns the current program without creating another revision, even
if subsequent edits exist. Changed retry input or a different author still
returns 409. Repeated creation IDs cannot silently change input.

The optional empty `strategies` list is omitted from canonical specification
JSON. Supplying an empty list and omitting it have the same digest.

Project permissions apply to every operation. Viewer accounts cannot create or
revise work. Private-project reads return 404 to unauthorized accounts. Program
inputs may reference organization-wide or same-project records, but not another
project or topic-scoped records. Owning both projects does not widen this rule.

## Tasks and exact input versions

A task request includes `expected_root_revision`, an optional `parent_id`, and
`depends_on` task IDs from the same program. Its specification and root revision
are pinned. Revising the program does not rewrite existing tasks. New assignments
require the current root revision. Lists return headers rather than transcripts;
page sizes are 1–100 and `next` is an exclusive cursor.

Input references have `kind`, `id`, `revision`, and `sha256`. Supported inputs:

- `work_spec`: program ID, numeric specification revision, specification digest.
- `work_task`: task ID, immutable specification revision `1`, task specification digest.
- `work_result`: job ID, numeric attempt fence, retained result digest.
- `compute_result`: computation ID, immutable receipt revision `1`, canonical
  server execution receipt digest. Execution outcomes are not proof certificates.
- `research_artifact`: artifact ID, numeric revision, existing content SHA-256.
- `source_snapshot`: server capture ID, revision `1`, extracted-text SHA-256;
  captures retain raw-body identity and transformation provenance separately.
- `knowledge`: knowledge entry ID, numeric revision; digest of canonical JSON
  containing exactly its `title`, `body`, and `state` fields.
- `mathematical`: immutable revision record path, its string `revision_id`, and
  SHA-256 of the stored `body_json` UTF-8 bytes. Mutable head paths are not inputs.

All references resolve to existing exact versions in the creation transaction.
Missing or out-of-scope inputs return 404; wrong digests return 409. The
mathematical ledger remains the authority for proof relationships and checks.

Knowledge relationships contain exact `source` and `target` input references,
a user-defined `relation` label, and an `explanation`. They annotate existing
knowledge, rather than creating another claim/bibliography store. They allow
cycles and equivalences. They are not execution prerequisites, proof premises,
or verification attestations. Execution dependencies live in `work_dependencies`;
version provenance lives in `work_provenance`. Relationship queries require
`kind`, `identity`, and `revision`, with `direction=in|out`, `after`, and `limit`.
Both directions use dedicated neighborhood indexes, not whole-graph loading.

## Resources and acceptance

Resource envelopes support optional `budget_microusd`, `cpu_seconds`,
`max_turns`, `max_tool_calls`, and `deadline_at`. An omitted ceiling is not an
implicit token/tool quota. `max_concurrency` defaults to 2 and is distinct from
the number of queued tasks. Empty `allowed_models`, `allowed_tools`, and
`allowed_images` lists authorize no such capabilities. Model and image grants
must also be allowed by organization policy.

Each child's authorization must fit both its parent and the current root
envelope. A task envelope is not a separate budget allocation. Accounting and
execution use [shared root and ancestor resource accounts](work-execution.md).

Each acceptance criterion has an ID, description, and a `check` kind:
`human_review`, `source_check`, `tests_passed`, `llm_review`, or `proof_checked`.
These are requested evidence types, not evidence that a check has passed.

The role values are `coordinator`, `worker`, `librarian`, `adviser`, `critic`,
`executor`, and `specification_reviewer`. Controller values are
`explore_review_revise`, `parallel_integrate`, and `build_check_repair`.
The contract API stores these assignments. The [durable worker protocol](work-execution.md)
queues and leases scoped tasks; server-brokered inference uses organization keys
and shared credit, while the executable worker runs pinned prompts and tools.
The contract endpoints store definitions. Explicitly started [controllers](work-execution.md#controllers)
advance durable jobs through review/revision, parallel integration, or checking/repair.
Programs begin in `draft` and tasks in `pending`.

## Internal inspection API

The overview API returns bounded objective previews, assignment-state totals,
acceptance criteria, and shared resource accounting. It is an infrastructure
inspection interface, not a separate user workspace. [Beta conversations](topic-agents.md)
are the agent interface. Assignment totals are not verified knowledge coverage.

Branch and controller headers are cursor-paged; exact specifications and check
bodies load separately. Search matches literal objective text with `%` and `_`
escaped. API pages allow 1–100 rows. Pause/activation use the current specification
revision and ordinary project-write authorization. Specification edits retain
existing assignments' exact pins.

Exact input reads use ordinary project-read authority, not a worker credential,
and do not charge worker tool accounting. The request supplies a complete
`reference`, a zero-based `offset` (default 0), and a `length` of 1–16,000
characters (default 12,000). Offsets count Unicode code points, not UTF-16 units.
Only that assignment's pinned or bound exact versions can be read. Wrong,
unassigned or inaccessible references are
refused. A computation receipt describes execution and its output manifest, not
a trusted test or proof attestation.

SQLite and PostgreSQL maintain task-state totals in the same transaction as
insertions, transitions and deletions, including rollback and recovery fencing.
The migration backfills populated installations once. PostgreSQL portable
snapshots rebuild the derived totals from task rows and verify them against the
source, rather than copying and double-counting them. Overview reads use those
bounded state buckets rather than loading or recounting all branches.

## Pinned role prompts and context

`GET /api/organizations/{organization_id}/work-prompts` exposes all seven role
templates, the common protocol, and four optional strategy modifiers: `breadth`,
`depth`, `adversarial`, and `retrieval_first`. The `strategies` list defaults to
empty, preserves order, and rejects duplicates. Modifiers guide an approach;
they do not grant tools, widen access, or change the deterministic controller.

Task creation pins the common prompt, selected role and modifiers, their content
digests, and the assembled system prompt. Enqueue also ensures a pinned prompt
exists before a lease can be granted. Template changes cannot rewrite pinned
material. `GET /{program_id}/tasks/{task_id}/prompt` reads this snapshot with the
same project permissions as the task. Prompt records are immutable and included
in portable backups.

The specification reviewer is one of the built-in roles; it needs no separate
review-service configuration. Its model opinion does not acquire the authority
of a human or an executable checker.
