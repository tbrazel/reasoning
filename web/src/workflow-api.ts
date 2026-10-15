import { organizationPath, request, topicPath } from "./organization-api";

export type WorkflowNode = {
  id: string; kind: "compute" | "llm" | "approval"; depends_on: string[];
  inputs: Record<string, { node: string; output: string }>; parameters: Record<string, unknown>;
};
export type WorkflowDefinition = { format: 1; name: string; nodes: WorkflowNode[] };
export type WorkflowVersion = {
  organization_id: string; workflow_id: string; topic_id: string; version: number; definition: WorkflowDefinition;
  definition_digest: string; node_order: string[]; created_by: string; created_at: number; client_id: string;
};
export type WorkflowSummary = {
  id: string; topic_id: string; version: number; name: string; definition_digest: string; created_by: string; created_at: number;
};
export type VersionSummary = Pick<WorkflowVersion, "version" | "definition_digest" | "created_by" | "created_at">;
export type WorkflowAttempt = {
  client_id: string; definition: WorkflowDefinition; workflow_id?: string; expected_version?: number;
};

export const workflowPath = (organizationId: string, workflowId: string) => organizationPath(organizationId) + "/workflows/" + encodeURIComponent(workflowId);
export const WORKFLOW_EXAMPLE: WorkflowDefinition = {
  format: 1, name: "Review a research question", nodes: [
    { id: "review", kind: "approval", depends_on: [], inputs: {},
      parameters: { message: "Approve the research question before model execution.", required_role: "admin" } },
    { id: "reason", kind: "llm", depends_on: ["review"], inputs: { review: { node: "review", output: "decision" } },
      parameters: { model: "choose-an-organization-approved-model", prompt: "Investigate the approved research question and produce a cited draft.", max_completion_tokens: null } },
  ],
};

export const MODEL_COMPARISON_WORKFLOW: WorkflowDefinition = {
  format: 1, name: "Compare two models", nodes: [
    { id: "model_a", kind: "llm", depends_on: [], inputs: {},
      parameters: { model: "choose-first-organization-approved-model", prompt: "Answer the same research question independently.", max_completion_tokens: null } },
    { id: "model_b", kind: "llm", depends_on: [], inputs: {},
      parameters: { model: "choose-second-organization-approved-model", prompt: "Answer the same research question independently.", max_completion_tokens: null } },
  ],
};

export function canonicalWorkflow(value: unknown): string {
  if (Array.isArray(value)) return "[" + value.map(canonicalWorkflow).join(",") + "]";
  if (value !== null && typeof value === "object") return "{" + Object.keys(value).sort().map((key) =>
    JSON.stringify(key) + ":" + canonicalWorkflow((value as Record<string, unknown>)[key])).join(",") + "}";
  if (typeof value === "string") {
    for (let index = 0; index < value.length; index++) {
      const unit = value.charCodeAt(index);
      if (unit >= 0xD800 && unit <= 0xDBFF) {
        const next = value.charCodeAt(++index);
        if (!(next >= 0xDC00 && next <= 0xDFFF)) throw new Error("Workflow must contain valid Unicode text.");
      } else if (unit >= 0xDC00 && unit <= 0xDFFF) throw new Error("Workflow must contain valid Unicode text.");
    }
  }
  const result = JSON.stringify(value);
  if (result === undefined || (typeof value === "number" && !Number.isFinite(value))) throw new Error("Workflow must contain JSON values.");
  return result;
}

function immutable<T>(value: T): T {
  if (value !== null && typeof value === "object") { Object.values(value).forEach(immutable); Object.freeze(value); }
  return value;
}

export function captureWorkflowAttempt(source: string, clientId: string, version?: Pick<WorkflowVersion, "workflow_id" | "version">): WorkflowAttempt {
  if (!/^[A-Za-z0-9_-]{16,128}$/.test(clientId)) throw new Error("A stable workflow request ID is required.");
  const value = JSON.parse(source) as WorkflowDefinition;
  if (!value || value.format !== 1 || typeof value.name !== "string" || !value.name.trim() || !Array.isArray(value.nodes) || !value.nodes.length) {
    throw new Error("Use a format-1 workflow with a name and nodes. The server validates the entire DAG before saving.");
  }
  // Match server normalization without reordering execution dependencies by accident.
  if (value.nodes.some((node) => !node || typeof node.id !== "string" || !Array.isArray(node.depends_on) || node.depends_on.some((id) => typeof id !== "string"))) {
    throw new Error("Every node needs an identifier and a depends_on array.");
  }
  value.nodes.forEach((node) => node.depends_on.sort());
  value.nodes.sort((left, right) => left.id < right.id ? -1 : left.id > right.id ? 1 : 0);
  const encoded = canonicalWorkflow(value);
  if (new TextEncoder().encode(encoded).length > 32768) throw new Error("Workflow definition exceeds 32 KiB.");
  if (version && (!Number.isSafeInteger(version.version) || version.version < 1 || version.version >= 128)) throw new Error("Workflow version cannot be revised.");
  return immutable({ client_id: clientId, definition: value, ...(version ? { workflow_id: version.workflow_id, expected_version: version.version } : {}) });
}

export async function deliverWorkflowAttempt(organizationId: string, topicId: string, userId: string, attempt: WorkflowAttempt): Promise<WorkflowVersion> {
  const path = attempt.workflow_id ? workflowPath(organizationId, attempt.workflow_id) + "/versions" : topicPath(organizationId, topicId) + "/workflows";
  const body = { client_id: attempt.client_id, definition: attempt.definition,
    ...(attempt.workflow_id ? { expected_version: attempt.expected_version } : {}) };
  const result = await request<WorkflowVersion>(path, { method: "POST", body });
  const hash = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(canonicalWorkflow(attempt.definition)));
  const digest = "sha256:" + Array.from(new Uint8Array(hash), (byte) => byte.toString(16).padStart(2, "0")).join("");
  if (result.organization_id !== organizationId || result.topic_id !== topicId || result.created_by !== userId || result.client_id !== attempt.client_id ||
      (attempt.workflow_id && result.workflow_id !== attempt.workflow_id) || result.version !== (attempt.expected_version ?? 0) + 1 ||
      canonicalWorkflow(result.definition) !== canonicalWorkflow(attempt.definition) || result.definition_digest !== digest || !/^[a-f0-9]{32}$/.test(result.workflow_id)) {
    throw new Error("Workflow receipt does not match the saved request. Keep this request ID and retry to recover its original receipt.");
  }
  return result;
}
