import type { ProjectInfo } from "./api";

export interface AuthoredWorkflowRevision {
  workflow_id: string;
  revision_id: string;
}

export function authoredWorkflowTarget(
  project: ProjectInfo,
  baseline: ReadonlyMap<string, string>,
  authored: AuthoredWorkflowRevision[],
): string | null {
  const authoredIds = authored.map((workflow) => workflow.workflow_id);
  const explicit = [...authoredIds].reverse().find((workflowId) => (
    project.workflows.some((workflow) => workflow.workflow_id === workflowId)
  ));
  if (explicit) return explicit;
  const changed = project.workflows
    .filter((workflow) => baseline.get(workflow.workflow_id) !== workflow.revision_id)
    .sort((left, right) => left.updated_at.localeCompare(right.updated_at));
  return changed.at(-1)?.workflow_id ?? null;
}
