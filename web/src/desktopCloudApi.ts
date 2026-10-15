export interface DesktopAuthPresentation {
  state: "signed_out" | "awaiting_callback" | "authenticated";
  cloud_connected: boolean;
  credential_custody: string;
  failure_code?: string;
  identity?: {
    account_id: string;
    actor: { type: string; id: string };
    tenant_id: string;
    expires_at: string;
  };
}

export interface DesktopCloudReadiness {
  status: "connected" | "disconnected";
  provider_injected: boolean;
}

async function result<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const document = await response.json();
      if (typeof document.detail === "string") detail = document.detail;
    } catch {
      // The app may have been closed while a request was in flight.
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

export const desktopAuth = (): Promise<DesktopAuthPresentation> =>
  fetch("/api/desktop/auth").then(result<DesktopAuthPresentation>);

export const desktopAuthAction = (
  action: "sign-in" | "resume" | "logout",
): Promise<DesktopAuthPresentation> =>
  fetch(`/api/desktop/auth/${action}`, { method: "POST" }).then(result<DesktopAuthPresentation>);

export const desktopCloudReadiness = (): Promise<DesktopCloudReadiness> =>
  fetch("/api/desktop/cloud/readiness").then(result<DesktopCloudReadiness>);

export const desktopCloudSessionAction = (
  action: "connect" | "disconnect",
): Promise<Record<string, unknown>> =>
  fetch(`/api/desktop/cloud/session/${action}`, { method: "POST" }).then(result<Record<string, unknown>>);

export const desktopCloudRuns = (): Promise<{ operation_ids: string[] }> =>
  fetch("/api/desktop/cloud/runs").then(result<{ operation_ids: string[] }>);

export const desktopCloudRun = (operationId: string): Promise<Record<string, unknown>> =>
  fetch(`/api/desktop/cloud/runs/${encodeURIComponent(operationId)}`).then(result<Record<string, unknown>>);

export const desktopCloudRunEvents = (operationId: string): Promise<Record<string, unknown>> =>
  fetch(`/api/desktop/cloud/runs/${encodeURIComponent(operationId)}/events`).then(result<Record<string, unknown>>);

export const desktopCloudDemo = (): Promise<Record<string, unknown>> =>
  fetch("/api/desktop/cloud/demo", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ opt_in: true }),
  }).then(result<Record<string, unknown>>);

export const desktopCloudArtifacts = (): Promise<{ artifact_ids: string[] }> =>
  fetch("/api/desktop/cloud/artifacts").then(result<{ artifact_ids: string[] }>);

export interface DesktopVerifiedArtifact {
  metadata: { artifact_id: string; media_type: string; digest: string; size_bytes: number };
  content_base64: string;
}

export const desktopCloudArtifact = (artifactId: string): Promise<DesktopVerifiedArtifact> =>
  fetch(`/api/desktop/cloud/artifacts/${encodeURIComponent(artifactId)}`).then(result<DesktopVerifiedArtifact>);

export interface CollaborationWorkspace {
  workspace_id: string;
  name: string;
  status: "active" | "archived";
  created_at: string;
}

export interface CollaborationOrganization {
  organization_id: string;
  name: string;
  kind: "personal" | "shared";
  role_ids: string[];
  permissions: string[];
  workspaces: CollaborationWorkspace[];
}

export interface CollaborationBootstrap {
  account_id: string;
  actor: { type: "user"; id: string };
  default_organization_id: string;
  default_workspace_id: string;
  organizations: CollaborationOrganization[];
}

export interface CollaborationTopic {
  topic_id: string;
  organization_id: string;
  workspace_id: string;
  title: string;
  state: "open" | "resolved" | "archived";
  created_by: { type: string; id: string };
  created_at: string;
  updated_at: string;
}

export interface CollaborationMessage {
  message_id: string;
  sequence: number;
  actor: { type: string; id: string };
  kind: "human" | "assistant" | "workflow" | "system";
  content: string;
  operation_id: string | null;
  occurred_at: string;
}

export interface CollaborationMessagePage {
  topic_id: string;
  messages: CollaborationMessage[];
  next_cursor: string | null;
  complete: boolean;
}

export interface CollaborationRunnerStatus {
  runner_id: string;
  enabled_organizations: string[];
  active: boolean;
  active_job: null | {
    organization_id: string;
    workspace_id: string;
    topic_id: string;
    operation_id: string;
    prompt: string;
    model: string;
    route: "contributed_codex" | "metered_openrouter";
    progress: string;
  };
  api_route_ready: boolean;
  last_error: string;
}

export interface CollaborationMember {
  account_id: string;
  user_id: string;
  email: string | null;
  status: "active";
  role_ids: string[];
  self: boolean;
}

export interface CollaborationOperation {
  operation_id: string;
  organization_id: string;
  workspace_id: string;
  topic_id: string;
  requested_by: { type: "user"; id: string };
  route: "contributed_codex" | "metered_openrouter";
  prompt: string;
  model: string;
  reasoning_effort: string;
  state: "queued" | "offered" | "accepted" | "running" | "succeeded" | "failed" | "cancelled" | "expired" | "outcome_unknown";
  runner: null | { runner_id: string; display_name: string; owner: { type: "user"; id: string } };
  progress: string;
  usage: null | {
    provider: "openrouter";
    prompt_tokens: number | null;
    completion_tokens: number | null;
    total_tokens: number | null;
    cost_usd: number | null;
  };
  created_at: string;
  updated_at: string;
}

const collaborationPath = (organizationId: string, workspaceId: string) =>
  `/api/desktop/collaboration/organizations/${encodeURIComponent(organizationId)}` +
  `/workspaces/${encodeURIComponent(workspaceId)}`;

export const collaborationBootstrap = (): Promise<CollaborationBootstrap> =>
  fetch("/api/desktop/collaboration/bootstrap").then(result<CollaborationBootstrap>);

export const collaborationMembers = (
  organizationId: string,
): Promise<{ members: CollaborationMember[] }> =>
  fetch(`/api/desktop/collaboration/organizations/${encodeURIComponent(organizationId)}/members`)
    .then(result<{ members: CollaborationMember[] }>);

export const collaborationInviteMember = (
  organizationId: string,
  email: string,
  roleId: string,
): Promise<CollaborationMember> =>
  fetch(`/api/desktop/collaboration/organizations/${encodeURIComponent(organizationId)}/members`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, role_id: roleId }),
  }).then(result<CollaborationMember>);

export const collaborationTopics = (
  organizationId: string,
  workspaceId: string,
): Promise<{ topics: CollaborationTopic[] }> =>
  fetch(`${collaborationPath(organizationId, workspaceId)}/topics`).then(result<{ topics: CollaborationTopic[] }>);

export const collaborationCreateTopic = (
  organizationId: string,
  workspaceId: string,
  title: string,
): Promise<CollaborationTopic> =>
  fetch(`${collaborationPath(organizationId, workspaceId)}/topics`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  }).then(result<CollaborationTopic>);

export const collaborationMessages = (
  organizationId: string,
  workspaceId: string,
  topicId: string,
): Promise<CollaborationMessagePage> =>
  fetch(`${collaborationPath(organizationId, workspaceId)}/topics/${encodeURIComponent(topicId)}/messages`)
    .then(result<CollaborationMessagePage>);

export const collaborationPostMessage = (
  organizationId: string,
  workspaceId: string,
  topicId: string,
  content: string,
): Promise<CollaborationMessage> =>
  fetch(`${collaborationPath(organizationId, workspaceId)}/topics/${encodeURIComponent(topicId)}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  }).then(result<CollaborationMessage>);

export const collaborationQueueInference = (
  organizationId: string,
  workspaceId: string,
  topicId: string,
  prompt: string,
  route: "contributed_codex" | "metered_openrouter",
): Promise<{ operation_id: string; state: "queued" }> =>
  fetch(`${collaborationPath(organizationId, workspaceId)}/topics/${encodeURIComponent(topicId)}/inference`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      prompt,
      route,
      model: route === "metered_openrouter" ? "openrouter/auto" : "gpt-5.6-sol",
      reasoning_effort: "high",
    }),
  }).then(result<{ operation_id: string; state: "queued" }>);

export const collaborationOperations = (
  organizationId: string,
  workspaceId: string,
  topicId: string,
): Promise<{ operations: CollaborationOperation[] }> =>
  fetch(`${collaborationPath(organizationId, workspaceId)}/topics/${encodeURIComponent(topicId)}/operations`)
    .then(result<{ operations: CollaborationOperation[] }>);

export const collaborationCancelInference = (
  organizationId: string,
  workspaceId: string,
  topicId: string,
  operationId: string,
): Promise<{ operation_id: string; state: string }> =>
  fetch(
    `${collaborationPath(organizationId, workspaceId)}/topics/${encodeURIComponent(topicId)}` +
    `/operations/${encodeURIComponent(operationId)}/cancel`,
    { method: "POST" },
  ).then(result<{ operation_id: string; state: string }>);

export const collaborationRunnerStatus = (): Promise<CollaborationRunnerStatus> =>
  fetch("/api/desktop/collaboration/runner").then(result<CollaborationRunnerStatus>);

export const collaborationConfigureRunner = (
  organizationId: string,
  workspaceIds: string[],
  enabled: boolean,
): Promise<CollaborationRunnerStatus> =>
  fetch("/api/desktop/collaboration/runner", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ organization_id: organizationId, workspace_ids: workspaceIds, enabled }),
  }).then(result<CollaborationRunnerStatus>);
