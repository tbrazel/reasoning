export interface ReferenceSummary {
  reference_id: string;
  revision_id: string;
  author: string;
  title: string;
  year: number | null;
  arxiv: string | null;
  url: string | null;
  doi: string | null;
  isbn: string | null;
  notes: string | null;
  /** cached copy of the source, relative to the project's sources dir */
  file: string | null;
}

export interface ClaimSummary {
  claim_id: string;
  kind: string;
  statement_text: string;
  reference_id: string | null;
  locator: string | null;
  reference: ReferenceSummary | null;
  status: string;
  authority: string | null;
  current_revision_id: string;
}

export interface PremiseReport {
  state: string;
  satisfied: boolean;
  claim_id?: string;
  revision_id?: string;
  unresolved?: string;
}

export interface ProofReport {
  claim_id: string;
  revision_id: string;
  abandoned: boolean;
  argument: string;
  argument_authority: string | null;
  artifacts: unknown[];
  artifact_checks: { verdict: string; authority: string }[];
  premises: PremiseReport[];
  target_fresh: boolean;
  stands: boolean;
  body?: { kind: string; text: string; format: string };
}

export interface LedgerOverview {
  counts: Record<string, number>;
  claims: ClaimSummary[];
  stale_proofs: string[];
  broken_proofs: string[];
  unresolved_premises: { proof_id: string; claim_id: string; needs: string[] }[];
  contradictions: string[];
  cycles: string[][];
  references: ReferenceSummary[];
}

/** /ledger/status answers relative to the version the client already
 *  holds (`since`): unchanged, a delta of changed/removed summaries, or
 *  the full overview when the server has no basis to diff against. */
export type OverviewPayload = { version: string } & (
  | { unchanged: true }
  | ({ delta_since: string } & Omit<LedgerOverview, "claims" | "references"> & {
        claims_changed: ClaimSummary[];
        claims_removed: string[];
        references_changed: ReferenceSummary[];
        references_removed: string[];
      })
  | LedgerOverview
);

export interface VerificationInfo {
  verification_id: string;
  proof_id: string | null;
  verdict: string;
  authority: string;
  kind: string | null;
  /** the display text (repaired when a repair exists), or null if none. */
  text: string | null;
  /** the author's original, present only when `text` is a repair. */
  original: string | null;
}

export interface ClaimDetail extends ClaimSummary {
  withdrawn: boolean;
  /** first-order dependencies: claims this one's live proofs rest on */
  dependencies?: ClaimSummary[];
  revisions: {
    revision_id: string;
    parent_revision_id: string | null;
    statement_text: string;
    refuted: boolean;
    established: boolean;
  }[];
  proofs: Record<string, ProofReport>;
  verifications?: VerificationInfo[];
}

export interface ChatEntry {
  role:
    | "user"
    | "assistant"
    | "reasoning"
    | "subagent"
    | "review"
    | "writer"
    | "activity"
    | "error";
  text: string;
  name?: string;
  /** when the entry was recorded (UTC ISO); shown on hover/tap. */
  at?: string;
  /** activity entries: the mapped item.completed payload, no text. */
  item?: Record<string, unknown>;
  /** assistant entries: id and the KaTeX-repaired display copy, if any. */
  message_id?: string;
  latex_repaired?: string;
  latex_original?: string;
  steered?: boolean;
  executor_id?: string;
  model?: string;
  model_provider?: string;
  /** Durable operation attribution used by portable checkpoint actions. */
  operation_id?: string;
  /** Present when this visible message was imported through a handoff. */
  imported_checkpoint_id?: string;
  source_chat_id?: string;
  review_id?: string;
  status?: string;
  verdict?: string | null;
  /** writer entries: handle, per-activation key, document, activation */
  writer_id?: string;
  writer_key?: string;
  filename?: string;
  activation?: number;
  subject?: {
    claim_id?: string;
    proof_id?: string;
    revision_id?: string;
    kind?: string;
    /** writeup reviews: the reviewed document's artifacts path */
    name?: string;
  };
}

export interface ReviewInfo {
  review_id: string;
  subject: {
    claim_id?: string;
    proof_id?: string;
    revision_id?: string;
    kind?: string;
    /** writeup reviews: the reviewed document's artifacts path */
    name?: string;
  };
  status: string;
  verdict?: string | null;
  /** running reviews: the reviewer's latest reasoning step (collapsed
   *  snippet) and the full list of steps so far (expanded view). */
  live?: string | null;
  steps?: string[] | null;
  /** finished reviews: codex exec usage (input_tokens, output_tokens, …). */
  usage?: Record<string, number> | null;
  model?: string;
  started_at?: string;
  report?: string;
  error?: string;
  requested_by?: { session?: string | null } | null;
}

/** Poll a session's reviews: its pending/running rows (slim — no
 *  reports or step traces) plus its all-time reviewer token usage. */
export const fetchReviews = (
  projectId: string,
  forSession: string,
): Promise<{
  reviews: ReviewInfo[];
  writers: WriterInfo[];
  usage: { input: number; cached: number; output: number };
}> =>
  fetch(
    `/api/projects/${projectId}/reviews?for_session=${encodeURIComponent(forSession)}`,
  ).then(json);

export interface WriterInfo {
  writer_id: string;
  status: string;
  /** the document's path relative to artifacts/ */
  filename: string;
  activation: number;
  model?: string;
  live?: string | null;
  error?: string | null;
}

export const fetchWriterDetail = (
  projectId: string,
  writerId: string,
): Promise<WriterInfo & { steps?: string[] | null; report?: string }> =>
  fetch(`/api/projects/${projectId}/writers/${writerId}`).then(json);

/** One review in full — the reasoning steps an expanded dock row shows. */
export const fetchReviewDetail = (
  projectId: string,
  reviewId: string,
): Promise<ReviewInfo> =>
  fetch(`/api/projects/${projectId}/reviews/${reviewId}`).then(json);

export interface TaskInfo {
  title: string;
  status: "pending" | "in_progress" | "completed";
}

/** The chat's single persistent goal; the harness keeps waking the
 *  model until it is marked completed, impossible, or blocked. */
export interface GoalInfo {
  text: string;
  status: "active" | "completed" | "impossible" | "blocked";
  reason?: string | null;
}

export const fetchTasks = (
  projectId: string,
  chatId: string,
): Promise<{ tasks: TaskInfo[]; goal?: GoalInfo | null }> =>
  fetch(`/api/projects/${projectId}/chats/${chatId}/tasks`).then(json);

export interface ChatInfo {
  id: string;
  title: string;
  mode?: "research" | "development";
  workspace?: DevelopmentWorkspaceInfo | null;
  archived?: boolean;
  pinned?: boolean;
  created_at?: string | null;
  updated_at?: string | null;
  has_unread?: boolean;
  running?: boolean;
  approval_pending?: boolean;
}

export interface ProjectInfo {
  id: string;
  name: string;
  archived?: boolean;
  pinned?: boolean;
  created_at?: string | null;
  updated_at?: string | null;
  chats: ChatInfo[];
  workflows: WorkflowHead[];
  workflow_runs: WorkflowRunSummary[];
}

const json = async (r: Response) => {
  if (!r.ok) {
    let message = `HTTP ${r.status}`;
    try {
      const body = await r.json();
      if (body?.detail) message = String(body.detail);
    } catch {
      // Some proxy and packaging errors are not JSON responses.
    }
    throw new Error(message);
  }
  return r.json();
};

export const fetchProjects = (): Promise<{ projects: ProjectInfo[] }> =>
  fetch("/api/projects?include_archived=true").then(json);

export const createProject = (name: string): Promise<{ id: string; name: string }> =>
  fetch("/api/projects", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  }).then(json);

export const patchProject = (
  projectId: string,
  patch: { name?: string; archived?: boolean },
): Promise<{ id: string; name: string; archived: boolean }> =>
  fetch(`/api/projects/${projectId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  }).then(json);

export interface ModelInfo {
  id: string;
  name: string;
  efforts: string[];
  default_effort: string;
}

/** The chat models on offer and the default for new chats. */
export const fetchModels = (): Promise<{ models: ModelInfo[]; default: string }> =>
  fetch("/api/models").then(json);

export const patchChat = (
  projectId: string,
  chatId: string,
  patch: { title?: string; archived?: boolean; model?: string },
): Promise<{ id: string; title: string; archived: boolean; model?: string }> =>
  fetch(`/api/projects/${projectId}/chats/${chatId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  }).then(json);

export const moveProject = (
  projectId: string,
  direction: "up" | "down",
): Promise<{ moved: string }> =>
  fetch(`/api/projects/${projectId}/move`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ direction }),
  }).then(json);

export const moveChat = (
  projectId: string,
  chatId: string,
  direction: "up" | "down",
): Promise<{ moved: string }> =>
  fetch(`/api/projects/${projectId}/chats/${chatId}/move`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ direction }),
  }).then(json);

export const reorderProjects = (
  projectIds: string[],
): Promise<{ order: string[] }> =>
  fetch("/api/projects/reorder", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ids: projectIds }),
  }).then(json);

export const reorderChats = (
  projectId: string,
  chatIds: string[],
): Promise<{ order: string[] }> =>
  fetch(`/api/projects/${projectId}/chats/reorder`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ids: chatIds }),
  }).then(json);

export const createChat = (
  projectId: string,
  title?: string,
  workspaceId?: string,
): Promise<ChatInfo> =>
  fetch(`/api/projects/${projectId}/chats`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title: title ?? null, workspace_id: workspaceId ?? null }),
  }).then(json);

export const updateProject = (
  projectId: string,
  changes: { name?: string; archived?: boolean; pinned?: boolean },
): Promise<ProjectInfo> =>
  fetch(`/api/projects/${projectId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(changes),
  }).then(json);

export const deleteProject = (
  projectId: string,
): Promise<{ id: string; deleted: boolean; recoverable: boolean }> =>
  fetch(`/api/projects/${projectId}`, { method: "DELETE" }).then(json);

export const updateChat = (
  projectId: string,
  chatId: string,
  changes: { title?: string; archived?: boolean; pinned?: boolean },
): Promise<ChatInfo> =>
  fetch(`/api/projects/${projectId}/chats/${chatId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(changes),
  }).then(json);

export const deleteChat = (
  projectId: string,
  chatId: string,
): Promise<{ id: string; deleted: boolean; recoverable: boolean }> =>
  fetch(`/api/projects/${projectId}/chats/${chatId}`, { method: "DELETE" }).then(json);

export interface BibliographyViewLink {
  project_id: string;
  chat_id: string | null;
  notes: string | null;
  tags: string[];
  added_at: string;
  updated_at: string;
}

export interface BibliographyResource {
  resource_id: string;
  name: string;
  media_type: string;
  bytes: number;
  sha256: string;
  path: string;
  created_at: string;
}

export interface BibliographySummary {
  summary_id: string;
  kind: "overview" | "reading_note" | "relevance" | "critique";
  scope: "global" | "project" | "chat";
  title: string | null;
  text: string;
  project_id: string | null;
  chat_id: string | null;
  provenance: Record<string, unknown>;
  created_at: string;
}

export interface BibliographyConnection {
  connection_id: string;
  kind: "project" | "chat" | "ledger_reference" | "claim" | "workflow" | "run" | "artifact";
  project_id: string;
  project_name?: string | null;
  available?: boolean;
  chat_id?: string | null;
  claim_id?: string | null;
  reference_id?: string | null;
  workflow_id?: string | null;
  run_id?: string | null;
  artifact_path?: string | null;
  label?: string | null;
  notes?: string | null;
  status?: string | null;
  locator?: string | null;
  provenance?: Record<string, unknown>;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface BibliographyItem {
  bibliography_id: string;
  item_type: string;
  citation_key: string;
  title: string;
  authors: string[];
  year: number | null;
  doi: string | null;
  arxiv_id: string | null;
  url: string | null;
  container_title: string | null;
  publisher: string | null;
  volume: string | null;
  issue: string | null;
  pages: string | null;
  isbn: string | null;
  primary_category: string | null;
  notes: string | null;
  tags: string[];
  resources: BibliographyResource[];
  summaries: BibliographySummary[];
  created_at: string;
  updated_at: string;
  view: {
    project_attached: boolean;
    chat_attached: boolean;
    project_chat_count: number;
    project: BibliographyViewLink | null;
    chat: BibliographyViewLink | null;
  };
}

export interface BibliographyDraft {
  item_type: string;
  citation_key?: string | null;
  title: string;
  authors: string[];
  year?: number | null;
  doi?: string | null;
  arxiv_id?: string | null;
  url?: string | null;
  container_title?: string | null;
  notes?: string | null;
  tags?: string[];
}

function bibliographyBase(projectId?: string | null, chatId?: string | null): string {
  if (projectId && chatId) return `/api/projects/${projectId}/chats/${chatId}/bibliography`;
  if (projectId) return `/api/projects/${projectId}/bibliography`;
  return "/api/bibliography";
}

export const fetchBibliography = (
  projectId?: string | null,
  chatId?: string | null,
  query = "",
): Promise<{ scope: "global" | "project" | "chat"; items: BibliographyItem[] }> =>
  fetch(`${bibliographyBase(projectId, chatId)}?q=${encodeURIComponent(query)}`).then(json);

export const createBibliographyItem = (
  draft: BibliographyDraft,
): Promise<{ created: boolean; item: BibliographyItem }> =>
  fetch("/api/bibliography", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(draft),
  }).then(json);

export const attachBibliographyItem = (
  bibliographyId: string,
  projectId: string,
  chatId?: string | null,
): Promise<BibliographyItem> =>
  fetch(`${bibliographyBase(projectId, chatId)}/${bibliographyId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  }).then(json);

export const detachBibliographyItem = (
  bibliographyId: string,
  projectId: string,
  chatId?: string | null,
): Promise<{ detached: boolean }> =>
  fetch(`${bibliographyBase(projectId, chatId)}/${bibliographyId}`, {
    method: "DELETE",
  }).then(json);

export const deleteBibliographyItem = (
  bibliographyId: string,
): Promise<{ deleted: boolean }> =>
  fetch(`/api/bibliography/${bibliographyId}`, { method: "DELETE" }).then(json);

export const bibliographyResourceUrl = (
  bibliographyId: string,
  resourceId: string,
): string => `/api/bibliography/${bibliographyId}/resources/${resourceId}`;

export const uploadBibliographyResource = (
  bibliographyId: string,
  file: File,
): Promise<{ created: boolean; resource: BibliographyResource }> =>
  fetch(`/api/bibliography/${bibliographyId}/resources?filename=${encodeURIComponent(file.name)}`, {
    method: "PUT",
    headers: { "Content-Type": file.type || "application/octet-stream" },
    body: file,
  }).then(json);

export const addBibliographySummary = (
  bibliographyId: string,
  draft: {
    kind: BibliographySummary["kind"];
    title?: string | null;
    text: string;
    provenance?: Record<string, unknown>;
  },
  projectId?: string | null,
  chatId?: string | null,
): Promise<{ summary: BibliographySummary; item: BibliographyItem }> =>
  fetch(`${bibliographyBase(projectId, chatId)}/${bibliographyId}/summaries`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(draft),
  }).then(json);

export const fetchBibliographyConnections = (
  bibliographyId: string,
): Promise<{ bibliography_id: string; connections: BibliographyConnection[] }> =>
  fetch(`/api/bibliography/${bibliographyId}/connections`).then(json);

export const addBibliographyConnection = (
  bibliographyId: string,
  connection: Omit<BibliographyConnection, "connection_id" | "project_name">,
): Promise<{ created: boolean; connection: BibliographyConnection }> =>
  fetch(`/api/bibliography/${bibliographyId}/connections`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(connection),
  }).then(json);

export const bibliographyExportUrl = (
  projectId?: string | null,
  chatId?: string | null,
  query = "",
): string => `${bibliographyBase(projectId, chatId)}/export.bib?q=${encodeURIComponent(query)}`;

/** Upload a message attachment into the project's artifacts folder;
 *  the server dedupes by content and disambiguates name collisions
 *  with a truncated hash. Returns the artifact path to cite. */
export const uploadArtifact = (
  projectId: string,
  file: File,
): Promise<{ path: string; name: string }> =>
  fetch(
    `/api/projects/${projectId}/artifacts?name=${encodeURIComponent(file.name)}`,
    { method: "POST", body: file },
  ).then(json);

/** Upload a copy of a cited source; the server stores it under the
 *  project's sources dir and points the reference's `file` at it. */
export const uploadReferenceFile = (
  projectId: string,
  referenceId: string,
  file: File,
): Promise<ReferenceSummary> =>
  fetch(
    `/api/projects/${projectId}/references/${referenceId}/file?name=${encodeURIComponent(file.name)}`,
    { method: "POST", body: file },
  ).then(json);

export interface ArtifactInfo {
  path: string;
  size: number;
  modified_at: string;
}

export const fetchArtifacts = (
  projectId: string,
): Promise<{ artifacts: ArtifactInfo[] }> =>
  fetch(`/api/projects/${projectId}/artifacts`).then(json);

/** Move/rename an artifact (paths relative to artifacts/, nesting
 *  allowed). The server refuses to overwrite an existing target. */
export const renameArtifact = (
  projectId: string,
  source: string,
  target: string,
): Promise<{ path: string }> =>
  fetch(`/api/projects/${projectId}/artifacts/rename`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source, target }),
  }).then(json);

export const fetchOverview = (
  projectId: string,
  since?: string,
): Promise<OverviewPayload> =>
  fetch(
    `/api/projects/${projectId}/ledger/status` +
      (since ? `?since=${encodeURIComponent(since)}` : ""),
  ).then(json);

export const fetchClaim = (
  projectId: string,
  id: string,
): Promise<ClaimDetail> =>
  fetch(`/api/projects/${projectId}/ledger/claims/${id}`).then(json);

/** Journal tail past the `after` entries the client already has. The
 *  total detects an out-of-sync client, which refetches from 0. */
export const fetchJournal = (
  projectId: string,
  after = 0,
): Promise<{ entries: Record<string, unknown>[]; total: number }> =>
  fetch(`/api/projects/${projectId}/ledger/journal?after=${after}`).then(json);

export const fetchVersion = (projectId: string): Promise<{ version: string }> =>
  fetch(`/api/projects/${projectId}/ledger/version`).then(json);

export interface GraphPayload {
  nodes: ClaimSummary[];
  edges: { from_claim: string; to_claim: string; state: string }[];
  unresolved: Record<string, number>;
}

export const fetchGraph = (projectId: string): Promise<GraphPayload> =>
  fetch(`/api/projects/${projectId}/ledger/graph`).then(json);

export const fetchResolve = (
  projectId: string,
  ledgerId: string,
): Promise<{ claim_id?: string; reference_id?: string }> =>
  fetch(`/api/projects/${projectId}/ledger/resolve/${ledgerId}`).then(json);

export interface ApprovalInfo {
  request_id: string;
  kind: string;
  command: string | null;
  cwd: string | null;
  reason: string | null;
  created_at: number;
  expires_at: number;
}

export const fetchHistory = (
  projectId: string,
  chatId: string,
  tail?: number,
  turns?: number,
  before?: number,
): Promise<{
  messages: ChatEntry[];
  seq: number;
  has_more: boolean;
  /** absolute transcript index of messages[0] (0 on a full fetch) */
  start_index?: number;
  thread_id: string | null;
  model_provider: string | null;
  model: string;
  effort: string;
  efforts: string[];
  turn_active: boolean;
  active_executor_id: string | null;
  active_model_provider: string | null;
  /** App-server requests currently waiting for a user decision. These are
   *  live RPC state rather than transcript entries, so history includes
   *  them explicitly to survive navigation and reconnects. */
  pending_approvals: ApprovalInfo[];
  /** transcript index of the newest turn-ending message the reader has
   *  reached; -1 = tracked but nothing read; null = never tracked. */
  read_index: number | null;
  /** last persisted token usage (same shape as token_usage events) */
  usage?: Record<string, unknown> | null;
}> =>
  fetch(
    `/api/projects/${projectId}/chats/${chatId}/history` +
      (before !== undefined
        ? `?before=${before}`
        : turns !== undefined
          ? `?turns=${turns}`
          : tail !== undefined
            ? `?tail=${tail}`
            : ""),
  ).then(json);

/** Advance the chat's shared read marker (server keeps the max). */
export const postReadMarker = (
  projectId: string,
  chatId: string,
  index: number,
): Promise<{ read_index: number }> =>
  fetch(`/api/projects/${projectId}/chats/${chatId}/read`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ index }),
  }).then(json);

export const interruptChat = (
  projectId: string,
  chatId: string,
): Promise<{ interrupted: boolean }> =>
  fetch(`/api/projects/${projectId}/chats/${chatId}/interrupt`, {
    method: "POST",
  }).then(json);

export const answerApproval = (
  projectId: string,
  chatId: string,
  requestId: string,
  decision: string,
): Promise<{ resolved: boolean }> =>
  fetch(`/api/projects/${projectId}/chats/${chatId}/approvals`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ request_id: requestId, decision }),
  }).then(json);

export interface HarnessInfo {
  harness_id: string;
  display_name: string;
  adapter_version: string;
  transport: "in_process" | "local_process" | "remote_service";
  prompt_fidelity: "wire_exact" | "controlled_input" | "opaque";
  session_modes: ("fresh" | "resume" | "fork")[];
  workspace_access: ("none" | "read_only" | "read_write")[];
  reasoning_efforts: string[];
  reasoning_visibility: "none" | "summary" | "streamed_summary";
  supports_streaming: boolean;
  supports_tools: boolean;
  supports_skills: boolean;
  supports_structured_output: boolean;
  supports_mcp: boolean;
  supports_approvals: boolean;
  supports_interrupt: boolean;
  supports_steering: boolean;
  supports_checkpoints: boolean;
  supports_subagents: boolean;
  supports_context_compaction: boolean;
  reports_usage: boolean;
  reports_cost: boolean;
  model_patterns: string[];
  model_options: string[];
}

export interface InferenceRouteInfo {
  route_id: string;
  display_name: string;
  provider: string;
  harness_ids: string[];
  model_patterns: string[];
  model_options: string[];
  reasoning_efforts: string[];
  ready: boolean;
  readiness: string;
}

export interface ExecutionTargetInfo {
  target_id: string;
  display_name: string;
  kind: "host" | "local_sandbox" | "remote_sandbox" | "stateless";
  isolation: "none" | "process" | "os_sandbox" | "container" | "vm";
  workspace_access: ("none" | "read_only" | "read_write")[];
  network_access: ("denied" | "allowlisted" | "unrestricted")[];
  supports_processes: boolean;
  durable_workspace: boolean;
}

export interface SelectedRunConfiguration {
  harness_id: string;
  inference_route_id: string;
  target_id: string;
  model: string;
  reasoning_effort: string | null;
}

export interface SessionBinding {
  harness_id: string;
  provider: string | null;
  target_id: string;
  model: string;
}

export interface ExecutionPreflightIssue {
  code: string;
  scope: "harness" | "route" | "target" | string;
  detail: string;
}

export interface ExecutionPreflight {
  compatible: boolean;
  issues: ExecutionPreflightIssue[];
  selection?: SelectedRunConfiguration;
  resolved?: {
    harness: HarnessInfo | null;
    inference_route: InferenceRouteInfo | null;
    execution_target: ExecutionTargetInfo | null;
  };
}

export interface ChatExecutionOptions {
  mode: "research" | "development";
  harnesses: HarnessInfo[];
  inference_routes: InferenceRouteInfo[];
  execution_targets: ExecutionTargetInfo[];
  requirements: Record<string, unknown>;
  default: SelectedRunConfiguration;
  session_binding: SessionBinding | null;
  default_preflight: ExecutionPreflight;
}

export type RoutingObjective = "balanced" | "quality" | "reliability" | "cost" | "latency";

export interface AutoSelectedRunConfiguration extends SelectedRunConfiguration {
  provider: string | null;
}

export interface AutoRoutingAssessment {
  selection: AutoSelectedRunConfiguration;
  eligible: boolean;
  score: number | null;
  components: {
    name: string;
    value: number;
    weight: number;
    contribution: number;
    source: string;
  }[];
  issues: ExecutionPreflightIssue[];
  explanations: string[];
  evidence: {
    observed_calls: number;
    successful_calls: number;
    benchmark_cases: number;
    quality_score: number | null;
    median_cost_usd: number | null;
    estimated_cost_usd: number | null;
    median_latency_seconds: number | null;
  } | null;
}

export interface AutoRoutingDecision {
  schema_version: 1;
  objective: RoutingObjective;
  scope: string;
  task_fingerprint: string;
  selected: AutoSelectedRunConfiguration | null;
  candidate_count: number;
  eligible_count: number;
  rejection_counts: Record<string, number>;
  assessments: AutoRoutingAssessment[];
  notes: string[];
}

export interface PortableCheckpointMessage {
  role: "user" | "assistant";
  content: string;
}

export interface PortableCheckpoint {
  checkpoint_id: string;
  sha256: string;
  schema_version: string;
  created_at: string;
  source: {
    harness_id: string;
    target_id: string;
    model: string;
    operation_id: string;
    inference_route_id?: string | null;
    model_provider?: string | null;
    project_id?: string | null;
    chat_id?: string | null;
  };
  messages: PortableCheckpointMessage[];
  safety: {
    effect_state: "none_observed" | "potential_effects" | "unknown";
    event_stream_complete: boolean;
    last_event_sequence: number | null;
    last_effect_sequence: number | null;
    advertised_checkpoint_sequences: number[];
    effect_kinds: string[];
  };
  artifacts: {
    artifact_id: string;
    path: string;
    sha256: string;
  }[];
  promotion: {
    actor: string;
    reason: string;
    promoted_at: string;
    safe_after_sequence: number | null;
  } | null;
  automatic_failover: {
    allowed: boolean;
    reason: string;
  };
}

export interface PortableCheckpointHandoff {
  chat: ChatInfo;
  checkpoint_id: string;
  automatic_failover: {
    allowed: boolean;
    reason: string;
  };
}

export const fetchChatExecutionOptions = (
  projectId: string,
  chatId: string,
): Promise<ChatExecutionOptions> =>
  fetch(`/api/projects/${projectId}/chats/${chatId}/execution-options`).then(json);

export const preflightChatExecution = (
  projectId: string,
  chatId: string,
  selection: SelectedRunConfiguration,
): Promise<ExecutionPreflight> =>
  fetch(`/api/projects/${projectId}/chats/${chatId}/execution-preflight`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(selection),
  }).then(json);

export const autoRouteChat = (
  projectId: string,
  chatId: string,
  prompt: string,
  objective: RoutingObjective,
  reasoningEffort: string | null,
): Promise<AutoRoutingDecision> =>
  fetch(`/api/projects/${projectId}/chats/${chatId}/auto-route`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      prompt,
      objective,
      reasoning_effort: reasoningEffort,
    }),
  }).then(json);

export const exportPortableCheckpoint = (
  projectId: string,
  chatId: string,
  operationId: string,
): Promise<PortableCheckpoint> =>
  fetch(
    `/api/projects/${projectId}/chats/${chatId}/operations/${operationId}/portable-checkpoints`,
    { method: "POST" },
  ).then(json);

export const fetchPortableCheckpoint = (
  projectId: string,
  chatId: string,
  checkpointId: string,
): Promise<PortableCheckpoint> =>
  fetch(
    `/api/projects/${projectId}/chats/${chatId}/portable-checkpoints/${checkpointId}`,
  ).then(json);

export const promotePortableCheckpoint = (
  projectId: string,
  chatId: string,
  checkpointId: string,
  reason: string,
): Promise<PortableCheckpoint> =>
  fetch(
    `/api/projects/${projectId}/chats/${chatId}/portable-checkpoints/${checkpointId}/promote`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason }),
    },
  ).then(json);

export const handoffPortableCheckpoint = (
  projectId: string,
  chatId: string,
  checkpointId: string,
  options: { title?: string; workspace_id?: string | null } = {},
): Promise<PortableCheckpointHandoff> =>
  fetch(
    `/api/projects/${projectId}/chats/${chatId}/portable-checkpoints/${checkpointId}/handoff`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(options),
    },
  ).then(json);

/** POST a chat message. Returns immediately: "steered" when it was
 *  injected into the running turn, "started" when it began a new one.
 *  The turn itself runs server-side; its events arrive on the chat's
 *  event stream (openEvents), same as for every other window. Failures
 *  throw with the server's human-readable detail when there is one. */
export const sendMessage = async (
  projectId: string,
  chatId: string,
  message: string,
  selection: SelectedRunConfiguration,
): Promise<"steered" | "started"> => {
  const response = await fetch(
    `/api/projects/${projectId}/chats/${chatId}/messages`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message,
        harness_id: selection.harness_id,
        inference_route_id: selection.inference_route_id,
        target_id: selection.target_id,
        model: selection.model,
        reasoning_effort: selection.reasoning_effort,
      }),
    },
  );
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body?.detail === "string") detail = body.detail;
      else if (body?.detail && typeof body.detail === "object") {
        const structured = body.detail as {
          message?: unknown;
          issues?: { detail?: unknown }[];
        };
        const issues = (structured.issues ?? [])
          .map((issue) => String(issue.detail ?? ""))
          .filter(Boolean);
        detail = [String(structured.message ?? "Invalid run configuration"), ...issues]
          .join(": ");
      }
    } catch {
      /* keep the bare status */
    }
    throw new Error(detail);
  }
  const payload = await response.json();
  return payload.steered ? "steered" : "started";
};

/** Subscribe to the chat's broadcast event stream. `after` is the seq
 *  cursor from the history fetch (events past it replay on connect);
 *  EventSource auto-reconnects resume via Last-Event-ID. */
export const openEvents = (
  projectId: string,
  chatId: string,
  after: number,
  onEvent: (event: Record<string, unknown>) => void,
): EventSource => {
  const source = new EventSource(
    `/api/projects/${projectId}/chats/${chatId}/events?after=${after}`,
  );
  source.onmessage = (e) => {
    try {
      onEvent(JSON.parse(e.data));
    } catch {
      /* ignore malformed event */
    }
  };
  return source;
};

// ---------------------------------------------------------------- agentic workflows

export interface ExecutorInfo {
  executor_id: string;
  display_name: string;
  prompt_fidelity: "wire_exact" | "controlled_input" | "opaque";
  supports_tools: boolean;
  supports_skills: boolean;
  supports_sessions: boolean;
  supports_streaming: boolean;
  supports_structured_output: boolean;
  supports_mcp: boolean;
  model_patterns: string[];
  model_options: string[];
}

export interface ConnectionStatus {
  executor_id: string;
  display_name: string;
  provider: string;
  kind: "cli" | "api";
  ready: boolean;
  installed?: boolean;
  authenticated?: boolean;
  validation_status?: "untested" | "valid" | "invalid";
  validated_at?: string | null;
  validation_detail?: string | null;
  tested_model?: string | null;
  readiness: string;
  setup: string;
}

export const fetchAgentExecutors = (
  projectId?: string,
): Promise<{ executors: ExecutorInfo[] }> =>
  fetch(
    projectId
      ? `/api/projects/${projectId}/agent/executors`
      : "/api/agent/executors",
  ).then(json);

export const fetchAgentConnections = (): Promise<{
  connections: ConnectionStatus[];
}> => fetch("/api/agent/connections").then(json);

export interface OpenRouterValidation {
  validation_status: "valid";
  validated_at: string;
  validation_detail: string;
  model_count: number;
}

export const validateOpenRouterConnection = (): Promise<OpenRouterValidation> =>
  fetch("/api/agent/connections/openrouter/validate", { method: "POST" }).then(json);

export interface CodexConfigurationTest {
  status: "ready" | "failed" | "error";
  model: string;
  tested_at: string;
  detail: string;
}

export const fetchCodexSetupPrompt = (
  model: string,
): Promise<{ model: string; prompt: string }> =>
  fetch(`/api/agent/connections/codex/setup?model=${encodeURIComponent(model)}`).then(json);

export const testCodexConfiguration = (
  model: string,
): Promise<CodexConfigurationTest> =>
  fetch("/api/agent/connections/codex/test", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model }),
  }).then(json);

export type ModelPriceScope = "all" | "account";

export interface ModelPriceSource {
  provider: "openrouter";
  label: string;
  scope: ModelPriceScope;
  status: "live" | "cached" | "stale";
  fetched_at: string;
  model_count: number;
  account_filter_available: boolean;
  catalog_url: string;
  detail: string;
  error?: string;
}

export interface ModelPriceInfo {
  model_id: string;
  canonical_slug: string;
  name: string;
  provider: string;
  hugging_face_id: string | null;
  source_availability: "open_weight" | "unreported";
  description: string;
  context_length: number | null;
  max_output_tokens: number | null;
  created_at: string | null;
  expiration_date: string | null;
  modality: string | null;
  input_modalities: string[];
  output_modalities: string[];
  supported_parameters: string[];
  supports_tools: boolean;
  supports_reasoning: boolean;
  input_mtok_usd: number | null;
  output_mtok_usd: number | null;
  cache_read_mtok_usd: number | null;
  cache_write_mtok_usd: number | null;
  reasoning_mtok_usd: number | null;
  request_usd: number | null;
  image_usd: number | null;
  web_search_usd: number | null;
}

export interface ModelPriceCoverage {
  executor_id: string;
  display_name: string;
  provider: string;
  ready: boolean;
  readiness: string;
  pricing_kind:
    | "live_catalog"
    | "token_estimate"
    | "reported_after_run"
    | "provider_delegated";
  detail: string;
  key_configured: boolean | null;
  model_options: string[];
}

export interface ModelPriceCatalog {
  source: ModelPriceSource;
  models: ModelPriceInfo[];
  coverage: ModelPriceCoverage[];
}

export const fetchModelPrices = (
  scope: ModelPriceScope = "all",
  refresh = false,
): Promise<ModelPriceCatalog> => {
  const query = new URLSearchParams({ scope });
  if (refresh) query.set("refresh", "true");
  return fetch(`/api/model-prices?${query}`).then(json);
};

export interface OpenRouterCallSummary {
  calls: number;
  completed_calls: number;
  failed_calls: number;
  priced_calls: number;
  unpriced_calls: number;
  cost_usd: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  first_call_at: string | null;
  last_call_at: string | null;
}

export interface OpenRouterProjectUsage extends OpenRouterCallSummary {
  project_id: string;
  project_name: string;
}

export interface OpenRouterCall {
  call_id: string;
  provider: "openrouter";
  project_id: string;
  project_name: string;
  run_id: string | null;
  sequence: number | null;
  status: "completed" | "failed";
  started_at: string;
  completed_at: string;
  duration_ms: number | null;
  requested_model: string | null;
  resolved_model: string | null;
  upstream_provider: string | null;
  response_id: string | null;
  finish_reason: string | null;
  native_finish_reason: string | null;
  error: string | null;
  cost_usd: number | null;
  upstream_cost_usd: number | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
  reasoning_tokens: number | null;
  cached_tokens: number | null;
  cache_write_tokens: number | null;
  usage: Record<string, unknown>;
}

export interface OpenRouterCallLedger {
  provider: "openrouter";
  scope: "reasoning-native";
  summary: OpenRouterCallSummary;
  projects: OpenRouterProjectUsage[];
  calls: OpenRouterCall[];
  total: number;
  limit: number;
  offset: number;
}

export const fetchOpenRouterCalls = (
  projectId: string | null = null,
  limit = 100,
  offset = 0,
): Promise<OpenRouterCallLedger> => {
  const query = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (projectId) query.set("project_id", projectId);
  return fetch(`/api/openrouter-calls?${query}`).then(json);
};

export interface ApiKeyStatus {
  provider: string;
  label: string;
  configured: boolean;
  source: "user" | "environment" | null;
  hint: string | null;
}

export const fetchApiKeyStatuses = (): Promise<{ providers: ApiKeyStatus[] }> =>
  fetch("/api/settings/api-keys").then(json);

export const saveApiKey = (
  provider: string,
  apiKey: string,
): Promise<{ providers: ApiKeyStatus[] }> =>
  fetch(`/api/settings/api-keys/${provider}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ api_key: apiKey }),
  }).then(json);

export const deleteApiKey = (
  provider: string,
): Promise<{ providers: ApiKeyStatus[] }> =>
  fetch(`/api/settings/api-keys/${provider}`, { method: "DELETE" }).then(json);

export interface RecentModels {
  openrouter: string[];
}

export const fetchRecentModels = (): Promise<RecentModels> =>
  fetch("/api/settings/recent-models").then(json);

export const rememberRecentModel = (
  provider: "openrouter",
  modelId: string,
): Promise<RecentModels> =>
  fetch(`/api/settings/recent-models/${provider}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model_id: modelId }),
  }).then(json);

export interface PinnedModels {
  openrouter: string[];
}

export const fetchPinnedModels = (): Promise<PinnedModels> =>
  fetch("/api/settings/pinned-models").then(json);

export const savePinnedModels = (
  provider: "openrouter",
  modelIds: string[],
): Promise<PinnedModels> =>
  fetch(`/api/settings/pinned-models/${provider}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model_ids: modelIds }),
  }).then(json);

export interface McpConnectionInfo {
  connection_id: string;
  name: string;
  transport: "stdio" | "http" | "sse";
  enabled: boolean;
  command: string | null;
  args: string[];
  url: string | null;
  environment_names: string[];
  header_names: string[];
  created_at: string;
  updated_at: string;
}

export interface McpConnectionDraft {
  name: string;
  transport: McpConnectionInfo["transport"];
  enabled: boolean;
  command?: string;
  args?: string[];
  url?: string;
  environment?: Record<string, string>;
  headers?: Record<string, string>;
}

export const fetchMcpConnections = (): Promise<{
  connections: McpConnectionInfo[];
}> => fetch("/api/settings/mcp-connections").then(json);

export const saveMcpConnection = (
  connectionId: string,
  connection: McpConnectionDraft,
): Promise<{ connection: McpConnectionInfo; connections: McpConnectionInfo[] }> =>
  fetch(`/api/settings/mcp-connections/${encodeURIComponent(connectionId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(connection),
  }).then(json);

export const deleteMcpConnection = (
  connectionId: string,
): Promise<{ connection: McpConnectionInfo; connections: McpConnectionInfo[] }> =>
  fetch(`/api/settings/mcp-connections/${encodeURIComponent(connectionId)}`, {
    method: "DELETE",
  }).then(json);

export interface DevelopmentWorkspaceInfo {
  workspace_id: string;
  name: string;
  path: string;
  available: boolean;
  python_environment: {
    mode: "bundled" | "detected" | "configured";
    path: string | null;
    available: boolean;
    label: string;
    detail: string;
  };
  workspace_environment: {
    managed: boolean;
    ready: boolean;
    state: "not_declared" | "setup_required" | "running" | "ready" | "interrupted" | "invalid";
    label: string;
    detail: string;
    check_available: boolean;
    setup_available: boolean;
    check_command: string;
    setup_command: string;
    check_timeout_seconds: number;
    setup_timeout_seconds: number;
    updated_at?: string | null;
    output?: string;
  };
  created_at?: string | null;
  updated_at?: string | null;
}

export interface DevelopmentWorkspaceSuggestion {
  name: string;
  path: string;
}

export const fetchDevelopmentWorkspaces = (): Promise<{
  workspaces: DevelopmentWorkspaceInfo[];
  suggestions: DevelopmentWorkspaceSuggestion[];
}> => fetch("/api/settings/development-workspaces").then(json);

export const addDevelopmentWorkspace = (
  name: string,
  path: string,
): Promise<{
  workspace: DevelopmentWorkspaceInfo;
  workspaces: DevelopmentWorkspaceInfo[];
}> =>
  fetch("/api/settings/development-workspaces", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, path }),
  }).then(json);

export const setDevelopmentWorkspacePython = (
  workspaceId: string,
  pythonPath: string | null,
): Promise<{
  workspace: DevelopmentWorkspaceInfo;
  workspaces: DevelopmentWorkspaceInfo[];
}> =>
  fetch(`/api/settings/development-workspaces/${encodeURIComponent(workspaceId)}/python`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ python_path: pythonPath }),
  }).then(json);

export const runDevelopmentWorkspaceEnvironment = (
  workspaceId: string,
  action: "check" | "setup",
): Promise<{
  workspace: DevelopmentWorkspaceInfo;
  workspaces: DevelopmentWorkspaceInfo[];
}> =>
  fetch(`/api/settings/development-workspaces/${encodeURIComponent(workspaceId)}/environment/${action}`, {
    method: "POST",
  }).then(json);

export interface DevelopmentWorkspaceEnvironmentDraft {
  label: string;
  check_command: string;
  setup_command: string | null;
  check_timeout_seconds: number;
  setup_timeout_seconds: number;
}

export const setDevelopmentWorkspaceEnvironment = (
  workspaceId: string,
  environment: DevelopmentWorkspaceEnvironmentDraft,
): Promise<{
  workspace: DevelopmentWorkspaceInfo;
  workspaces: DevelopmentWorkspaceInfo[];
}> =>
  fetch(`/api/settings/development-workspaces/${encodeURIComponent(workspaceId)}/environment`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(environment),
  }).then(json);

export const clearDevelopmentWorkspaceEnvironment = (
  workspaceId: string,
): Promise<{
  workspace: DevelopmentWorkspaceInfo;
  workspaces: DevelopmentWorkspaceInfo[];
}> =>
  fetch(`/api/settings/development-workspaces/${encodeURIComponent(workspaceId)}/environment`, {
    method: "DELETE",
  }).then(json);

export const deleteDevelopmentWorkspace = (
  workspaceId: string,
): Promise<{
  workspace: DevelopmentWorkspaceInfo;
  workspaces: DevelopmentWorkspaceInfo[];
}> =>
  fetch(`/api/settings/development-workspaces/${encodeURIComponent(workspaceId)}`, {
    method: "DELETE",
  }).then(json);

export interface AgentToolInfo {
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
  output_schema: Record<string, unknown> | null;
  risk: "read" | "write" | "external" | "destructive";
  capabilities: string[];
}

export interface PromptHead {
  prompt_id: string;
  revision_id: string;
  name: string;
  kind: "system" | "user" | "context" | "evaluator";
  updated_at: string;
}

export interface PromptRevision extends PromptHead {
  parent_revision_id: string | null;
  description: string;
  template: string;
  variable_schema: Record<string, unknown>;
  slots: string[];
  created_at: string;
}

export interface SkillHead {
  skill_id: string;
  revision_id: string;
  name: string;
  description: string;
  updated_at: string;
}

export interface SkillRevision extends SkillHead {
  parent_revision_id: string | null;
  instructions: string;
  metadata: Record<string, unknown>;
  license: string | null;
  compatibility: string | null;
  allowed_tools: string | null;
  created_at: string;
}

export interface WorkflowNode {
  id: string;
  kind: "input" | "agent" | "output";
  name: string;
  position: { x: number; y: number };
  config?: AgentNodeConfig;
}

export interface AgentNodeConfig {
  executor_id: string;
  model: string;
  model_provider?: string | null;
  effort?: string | null;
  system_prompt?: { prompt_id: string; revision_id: string };
  user_prompt?: { prompt_id: string; revision_id: string };
  variables?: Record<string, unknown>;
  skills: { skill_id: string; revision_id: string }[];
  tools: string[];
  mcp_connections: string[];
  context_policy: {
    mode: "none" | "predecessors" | "all" | "selected" | "rolling";
    max_tokens?: number | null;
    include_agent_outputs?: string[];
  };
  output_schema?: Record<string, unknown> | null;
  completion?: {
    required_markers?: string[];
    required_artifacts?: string[];
    required_tool_calls?: Record<string, number>;
  };
  budget: {
    max_cost_usd?: number | null;
    max_output_tokens?: number | null;
    max_tool_calls?: number;
    timeout_seconds?: number;
  };
  sandbox: {
    mode: "read_only" | "workspace_write" | "none";
    network?: boolean;
    working_directory?: string | null;
    writable_paths?: string[];
  };
  session?: { mode: "fresh" | "resume" | "fork"; session_id?: string | null };
}

export interface WorkflowEdge {
  id: string;
  source: string;
  target: string;
}

export interface WorkflowGraph {
  workflow_id: string;
  revision_id: string;
  parent_revision_id: string | null;
  name: string;
  description: string;
  schema_version?: number;
  workflow_type?: "graph" | "model_comparison" | "model_benchmark";
  presentation?: {
    recipe: "graph" | "model_comparison" | "paper_moderation" | "dataset_generator";
    [key: string]: unknown;
  };
  execution_policy?: {
    max_concurrency: number;
    failure_policy: "fail_fast" | "continue";
    max_calls?: number | null;
    max_total_cost_usd?: number | null;
  };
  iterations: number;
  dataset?: WorkflowDataset;
  dataset_ref?: { dataset_id: string; revision_id: string } | null;
  variants?: WorkflowVariant[];
  evaluators?: WorkflowEvaluator[];
  benchmark?: PaperModerationBenchmark;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  created_at: string;
}

export interface WorkflowDatasetCase {
  case_id: string;
  name: string;
  inputs: Record<string, unknown>;
  expected?: unknown;
  metadata?: Record<string, unknown>;
}

export interface WorkflowDataset {
  kind: "case_dataset_v1";
  schema_version: 1;
  name: string;
  description: string;
  cases: WorkflowDatasetCase[];
}

export interface DatasetHead {
  dataset_id: string;
  revision_id: string;
  name: string;
  created_at: string;
  updated_at: string;
}

export interface DatasetRevision extends WorkflowDataset {
  dataset_id: string;
  revision_id: string;
  parent_revision_id: string | null;
  created_at: string;
}

export interface WorkflowVariant {
  variant_id: string;
  name: string;
  node_overrides: Record<string, Partial<AgentNodeConfig>>;
  metadata?: Record<string, unknown>;
}

export interface WorkflowEvaluator {
  evaluator_id: string;
  kind: "paper_moderation_v1" | "exact_match_v1" | "typed_answer_v1";
  config: Record<string, unknown>;
}

export interface AnswerKeyHead {
  answer_key_id: string;
  revision_id: string;
  name: string;
  description: string;
  dataset_ref: { dataset_id: string; revision_id: string };
  target_count: number;
  created_at: string;
  updated_at: string;
}

export interface WorkflowPreflight {
  ready: boolean;
  checked_at: string;
  source: Record<string, unknown> | null;
  plan: {
    recipe: string;
    dataset: string;
    dataset_ref: { dataset_id: string; revision_id: string } | null;
    case_count: number;
    variant_count: number;
    agent_count: number;
    scheduled_calls: number;
    max_calls: number | null;
    max_total_cost_usd: number | null;
    evaluators: string[];
  };
  checks: Array<{
    node_id?: string;
    variant_id?: string;
    executor_id: string;
    model: string;
    status: string;
    detail: string;
  }>;
  issues: string[];
  warnings: string[];
}

export type PaperModerationDecision = "allow" | "review" | "reject";
export type PaperModerationFlag =
  | "malicious_code"
  | "prompt_injection"
  | "slop"
  | "offensive_content";

export interface PaperModerationCase {
  case_id: string;
  name: string;
  paper_text: string;
  expected: {
    decision: PaperModerationDecision;
    flags: PaperModerationFlag[];
  };
}

export interface PaperModerationBenchmark {
  kind: "paper_moderation_v1";
  schema_version: 1;
  cases: PaperModerationCase[];
}

export interface WorkflowHead {
  workflow_id: string;
  revision_id: string;
  name: string;
  archived?: boolean;
  pinned?: boolean;
  created_at?: string;
  updated_at: string;
}

export interface WorkflowRunSummary {
  run_id: string;
  workflow_id: string;
  workflow_revision_id: string;
  status: WorkflowRunState["status"];
  iterations: number;
  cost?: WorkflowRunCost;
  artifact_path?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface WorkflowRunCost {
  currency: "USD";
  total_usd: number;
  provider_reported_usd: number;
  estimated_usd: number;
  priced_calls: number;
  estimated_calls: number;
  unpriced_calls: number;
  completed_unpriced_calls?: number;
  failed_unpriced_calls?: number;
  by_executor: {
    executor_id: string;
    model_provider?: string | null;
    model: string;
    calls: number;
    priced_calls: number;
    estimated_calls: number;
    unpriced_calls: number;
    completed_unpriced_calls?: number;
    failed_unpriced_calls?: number;
    total_usd: number;
  }[];
}

export interface AgentPreview {
  system_prompt: string;
  user_prompt: string;
  prompt_hashes: Record<string, string>;
  tools: string[];
  skills: { name: string; revision_id: string }[];
  mcp_connections: string[];
  prompt_fidelity: string;
}

export interface WorkflowRunState {
  run_id: string;
  workflow_id: string;
  workflow_revision_id: string;
  status: "pending" | "running" | "cancelling" | "cancelled" | "completed" | "partial" | "failed";
  iterations: number;
  events: Record<string, unknown>[];
  workflow_snapshot?: WorkflowGraph;
  variables?: Record<string, unknown>;
  outputs: {
    nodes?: WorkflowNodeOutput[];
    benchmark?: BenchmarkScorecard;
    evaluations?: Record<string, unknown>;
  };
  cost?: WorkflowRunCost;
  artifact_path?: string | null;
  artifact_json_path?: string | null;
  error?: string;
  cancel_requested?: boolean;
  created_at?: string;
  updated_at?: string;
}

export interface BenchmarkCaseResult {
  case_id: string;
  case_name: string;
  status: string;
  expected: {
    decision: PaperModerationDecision;
    flags: PaperModerationFlag[];
  };
  predicted: {
    decision: PaperModerationDecision;
    flags: PaperModerationFlag[];
    confidence: number;
    rationale: string;
  } | null;
  strict_json: boolean;
  parseable_json: boolean;
  schema_valid: boolean;
  decision_correct: boolean;
  flags_exact: boolean;
  error?: string | null;
}

export interface BenchmarkModelScore {
  rank: number;
  node_id: string;
  name: string;
  executor_id: string;
  model_provider?: string | null;
  model: string;
  cases: number;
  completed_calls: number;
  failed_calls: number;
  strict_json_rate: number | null;
  parseable_json_rate: number | null;
  schema_valid_rate: number | null;
  decision_accuracy: number | null;
  exact_flags_rate: number | null;
  flag_precision: number | null;
  flag_recall: number | null;
  flag_f1: number | null;
  false_allow_count: number;
  false_allow_rate: number | null;
  review_rate: number | null;
  average_latency_seconds: number | null;
  priced_calls: number;
  unpriced_calls: number;
  completed_unpriced_calls: number;
  failed_unpriced_calls: number;
  cost_complete: boolean;
  total_cost_usd: number;
  average_cost_usd: number | null;
  case_results: BenchmarkCaseResult[];
}

export interface BenchmarkScorecard {
  kind: "paper_moderation_v1";
  schema_version: 1;
  case_count: number;
  model_count: number;
  scheduled_calls: number;
  completed_calls: number;
  models: BenchmarkModelScore[];
}

export interface WorkflowNodeOutput {
  iteration: number;
  case_id?: string;
  case_name?: string;
  variant_id?: string;
  variant_name?: string;
  work_item_id?: string;
  node_id: string;
  executor_id: string;
  model_provider?: string | null;
  model: string;
  agent_run_id: string | null;
  status: "completed" | "failed" | "cancelled";
  text: string;
  structured_output?: unknown;
  usage?: Record<string, unknown>;
  cost?: {
    amount_usd: number;
    basis: "provider_reported" | "estimated";
    source_field: string;
  };
  error?: string | null;
  started_at?: string;
  completed_at?: string;
  duration_seconds?: number;
  prompt_fidelity?: string | null;
  resolved_model?: string | null;
  serving_provider?: string | null;
  response_id?: string | null;
  finish_reason?: string | null;
  native_finish_reason?: string | null;
}

export interface ModelRankingBenchmark {
  cases: number;
  false_allow_count: number;
  unsafe_cases: number;
  false_allow_rate: number | null;
  decision_accuracy?: number;
  flag_f1?: number;
  flag_precision?: number;
  flag_recall?: number;
  schema_valid_rate?: number;
}

export interface ModelRankingConfiguration {
  configuration_id: string;
  executor_id: string;
  model_provider?: string | null;
  model: string;
  effort?: string | null;
  calls: number;
  completed_calls: number;
  failed_calls: number;
  success_rate: number | null;
  median_latency_seconds: number | null;
  average_latency_seconds: number | null;
  priced_calls: number;
  unpriced_calls: number;
  median_cost_usd: number | null;
  average_cost_usd: number | null;
  serving_providers: string[];
  latest_at?: string | null;
  benchmark: ModelRankingBenchmark | null;
}

export interface ModelRankingMode {
  mode_id: "best_quality" | "best_value" | "fastest_reliable" | "cheapest_reliable" | "diverse";
  label: string;
  description: string;
  available: boolean;
  configuration_ids: string[];
  model_ids: string[];
}

export interface WorkflowModelRankings {
  schema_version: 1;
  workflow_id: string;
  task_fingerprint: string;
  scope: string;
  total_runs: number;
  compatible_runs: number;
  configurations: ModelRankingConfiguration[];
  modes: ModelRankingMode[];
  notes: string[];
}

export const fetchAgentCatalog = async (
  projectId: string | null,
): Promise<{
  executors: ExecutorInfo[];
  tools: AgentToolInfo[];
  prompts: PromptHead[];
  skills: SkillHead[];
  connections: ConnectionStatus[];
  mcpConnections: McpConnectionInfo[];
}> => {
  const [executors, tools, prompts, skills, connections, mcpConnections] = await Promise.all([
    fetchAgentExecutors(),
    projectId
      ? fetch(`/api/projects/${projectId}/agent/tools`).then(json)
      : Promise.resolve({ tools: [] }),
    fetch("/api/agent/prompts").then(json),
    fetch("/api/agent/skills").then(json),
    fetchAgentConnections(),
    fetchMcpConnections(),
  ]);
  return {
    ...executors,
    ...tools,
    ...prompts,
    ...skills,
    ...connections,
    mcpConnections: mcpConnections.connections,
  };
};

export const createAgentPrompt = (
  prompt: {
    name: string;
    kind: PromptHead["kind"];
    template: string;
    description?: string;
    variable_schema?: Record<string, unknown>;
  },
): Promise<PromptRevision> =>
  fetch("/api/agent/prompts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(prompt),
  }).then(json);

export const fetchPromptRevisions = (
  promptId: string,
): Promise<{ revisions: PromptRevision[] }> =>
  fetch(`/api/agent/prompts/${promptId}/revisions`).then(json);

export const fetchAgentPrompt = (
  promptId: string,
  revisionId?: string,
): Promise<PromptRevision> => {
  const query = revisionId
    ? `?revision_id=${encodeURIComponent(revisionId)}`
    : "";
  return fetch(`/api/agent/prompts/${encodeURIComponent(promptId)}${query}`).then(json);
};

export const reviseAgentPrompt = (
  promptId: string,
  revision: {
    name: string;
    kind: PromptHead["kind"];
    template: string;
    description?: string;
    variable_schema?: Record<string, unknown>;
    parent_revision_id?: string;
  },
): Promise<PromptRevision> =>
  fetch(`/api/agent/prompts/${promptId}/revisions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(revision),
  }).then(json);

export const deleteAgentPrompt = (promptId: string): Promise<{ prompt_id: string }> =>
  fetch(`/api/agent/prompts/${promptId}`, { method: "DELETE" }).then(json);

export const renderAgentPrompt = (
  promptId: string,
  revisionId: string,
  variables: Record<string, unknown>,
): Promise<{ text: string }> =>
  fetch(`/api/agent/prompts/${promptId}/render`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ revision_id: revisionId, variables }),
  }).then(json);

export const createAgentSkill = (
  skill: {
    name: string;
    description: string;
    instructions: string;
    metadata?: Record<string, unknown>;
  },
): Promise<SkillRevision> =>
  fetch("/api/agent/skills", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(skill),
  }).then(json);

export const fetchSkillRevisions = (
  skillId: string,
): Promise<{ revisions: SkillRevision[] }> =>
  fetch(`/api/agent/skills/${skillId}/revisions`).then(json);

export const reviseAgentSkill = (
  skillId: string,
  revision: {
    description?: string;
    instructions?: string;
    metadata?: Record<string, unknown>;
    parent_revision_id?: string;
  },
): Promise<SkillRevision> =>
  fetch(`/api/agent/skills/${skillId}/revisions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(revision),
  }).then(json);

export const deleteAgentSkill = (skillId: string): Promise<{ skill_id: string }> =>
  fetch(`/api/agent/skills/${skillId}`, { method: "DELETE" }).then(json);

export const fetchWorkflows = (
  projectId: string,
): Promise<{ workflows: WorkflowHead[] }> =>
  fetch(`/api/projects/${projectId}/workflows`).then(json);

export const fetchDatasets = (
  projectId: string,
): Promise<{ datasets: DatasetHead[] }> =>
  fetch(`/api/projects/${projectId}/datasets`).then(json);

export const fetchDataset = (
  projectId: string,
  datasetId: string,
  revisionId?: string,
): Promise<DatasetRevision> =>
  fetch(
    `/api/projects/${projectId}/datasets/${encodeURIComponent(datasetId)}`
      + (revisionId ? `?revision_id=${encodeURIComponent(revisionId)}` : ""),
  ).then(json);

export const createDataset = (
  projectId: string,
  dataset: Pick<WorkflowDataset, "name" | "description" | "cases">,
): Promise<DatasetRevision> =>
  fetch(`/api/projects/${projectId}/datasets`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(dataset),
  }).then(json);

export const fetchAnswerKeys = (
  projectId: string,
): Promise<{ answer_keys: AnswerKeyHead[] }> =>
  fetch(`/api/projects/${projectId}/answer-keys`).then(json);

export const createAnswerKey = (
  projectId: string,
  answerKey: {
    name: string;
    description?: string;
    dataset_ref: { dataset_id: string; revision_id: string };
    targets: Array<Record<string, unknown>>;
  },
): Promise<AnswerKeyHead> =>
  fetch(`/api/projects/${projectId}/answer-keys`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(answerKey),
  }).then(json);

export const createDatasetFromRun = (
  projectId: string,
  runId: string,
  name?: string,
): Promise<DatasetRevision> =>
  fetch(`/api/projects/${projectId}/workflow-runs/${runId}/dataset`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: name || null }),
  }).then(json);

export const fetchWorkflowTemplates = (): Promise<{ workflows: WorkflowGraph[] }> =>
  fetch("/api/workflow-templates").then(json);

export const deleteWorkflowTemplate = (
  workflowId: string,
): Promise<{ workflow_id: string }> =>
  fetch(`/api/workflow-templates/${encodeURIComponent(workflowId)}`, {
    method: "DELETE",
  }).then(json);

export const createWorkflow = (
  projectId: string,
  name: string,
  templateId?: string,
): Promise<WorkflowGraph> =>
  fetch(`/api/projects/${projectId}/workflows`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, template_id: templateId || null }),
  }).then(json);

export const fetchWorkflow = (
  projectId: string,
  workflowId: string,
  revisionId?: string,
): Promise<WorkflowGraph> =>
  fetch(
    `/api/projects/${projectId}/workflows/${workflowId}`
      + (revisionId ? `?revision_id=${encodeURIComponent(revisionId)}` : ""),
  ).then(json);

export const saveWorkflow = async (
  projectId: string,
  graph: WorkflowGraph,
): Promise<WorkflowGraph> => {
  const response = await fetch(
    `/api/projects/${projectId}/workflows/${graph.workflow_id}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ graph, expected_revision_id: graph.revision_id }),
    },
  );
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || `HTTP ${response.status}`);
  }
  return response.json();
};

export const deleteWorkflow = (
  projectId: string,
  workflowId: string,
): Promise<{ workflow_id: string }> =>
  fetch(`/api/projects/${projectId}/workflows/${workflowId}`, {
    method: "DELETE",
  }).then(json);

export const updateWorkflow = (
  projectId: string,
  workflowId: string,
  changes: { name?: string; archived?: boolean; pinned?: boolean },
): Promise<WorkflowHead> =>
  fetch(`/api/projects/${projectId}/workflows/${workflowId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(changes),
  }).then(json);

export const previewAgentNode = (
  projectId: string,
  config: AgentNodeConfig,
  variables: Record<string, unknown>,
): Promise<AgentPreview> =>
  fetch(`/api/projects/${projectId}/agent/preview`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ config, variables }),
  }).then(json);

export const preflightWorkflow = (
  projectId: string,
  graph: WorkflowGraph,
  refresh = false,
): Promise<WorkflowPreflight> =>
  fetch(`/api/projects/${projectId}/workflows/preflight?refresh=${refresh ? "true" : "false"}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ graph, expected_revision_id: graph.revision_id }),
  }).then(json);

export const startWorkflowRun = (
  projectId: string,
  workflowId: string,
  iterations: number,
  variables: Record<string, unknown>,
  initialPrompt: string,
  expectedRevisionId?: string,
): Promise<{ run_id: string; status: string }> =>
  fetch(`/api/projects/${projectId}/workflows/${workflowId}/runs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      iterations,
      variables,
      initial_prompt: initialPrompt,
      expected_revision_id: expectedRevisionId ?? null,
    }),
  }).then(json);

export const fetchWorkflowRun = (
  projectId: string,
  runId: string,
): Promise<WorkflowRunState> =>
  fetch(`/api/projects/${projectId}/workflow-runs/${runId}`).then(json);

export const fetchWorkflowRuns = (
  projectId: string,
  workflowId: string,
): Promise<{ runs: WorkflowRunState[] }> =>
  fetch(
    `/api/projects/${projectId}/workflow-runs?workflow_id=${encodeURIComponent(workflowId)}`,
  ).then(json);

export const fetchWorkflowModelRankings = (
  projectId: string,
  workflowId: string,
): Promise<WorkflowModelRankings> =>
  fetch(
    `/api/projects/${projectId}/workflows/${workflowId}/model-rankings`,
  ).then(json);

export const autoRouteWorkflow = (
  projectId: string,
  workflowId: string,
  objective: RoutingObjective,
): Promise<AutoRoutingDecision> =>
  fetch(`/api/projects/${projectId}/workflows/${workflowId}/auto-route`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ objective }),
  }).then(json);

export const cancelWorkflowRun = (
  projectId: string,
  runId: string,
): Promise<WorkflowRunState> =>
  fetch(`/api/projects/${projectId}/workflow-runs/${runId}/cancel`, {
    method: "POST",
  }).then(json);
