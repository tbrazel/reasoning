import React, { useState } from "react";
import { Hierarchy, Organization, Topic } from "./organization-api";
import { MathematicsBrowser } from "./MathematicsBrowser";
import { ResearchBibliography } from "./ResearchBibliography";
import { KnowledgeBrowser } from "./KnowledgeBrowser";
import { ResearchBlog } from "./ResearchBlog";

export type KnowledgeView = "ledger" | "bibliography" | "journal";
export const knowledgeTitle = (view: KnowledgeView) => view === "journal" ? "Research blog" : view === "ledger" ? "Ledger" : "Bibliography";
export function knowledgeURL(organizationId: string, view: KnowledgeView, topicId?: string, projectId?: string) {
  const parameters = new URLSearchParams({ organization: organizationId, view });
  if (topicId) parameters.set("topic", topicId);
  if (projectId) parameters.set("project", projectId);
  return "/organization.html?" + parameters;
}

export function KnowledgeLinks({ organizationId, topicId, onOpen }: { organizationId: string; topicId?: string; onOpen?: (view: KnowledgeView) => void }) {
  return <nav className="org-knowledge-links" aria-label="Research workspaces">{(["ledger", "bibliography", "journal"] as KnowledgeView[]).map(view =>
    <a key={view} className="org-icon-button" href={view === "journal" ? `/blog/${encodeURIComponent(organizationId)}` : knowledgeURL(organizationId, view, topicId)}
      onClick={event => { if (view !== "journal" && onOpen && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey && event.button === 0) { event.preventDefault(); onOpen(view); } }}
      aria-label={`Open ${knowledgeTitle(view).toLowerCase()}`} title={knowledgeTitle(view)}>
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
        {view === "ledger" ? <><circle cx="5" cy="12" r="2" /><circle cx="18" cy="5" r="2" /><circle cx="18" cy="19" r="2" /><path d="M7 11l9-5M7 13l9 5" /></> : view === "bibliography"
          ? <><path d="M3 4h7l2 2 2-2h7v15h-7l-2 2-2-2H3zM12 6v15" /></> : <><path d="M4 4h16v16H4zM8 8h8M8 12h8M8 16h5" /></>}
      </svg>
    </a>)}</nav>;
}

export function KnowledgeWorkspace({ organization, hierarchy, view, initialTopicId, initialProjectId, onAuthLost, embedded = false }: {
  organization: Organization; hierarchy: Hierarchy; view: KnowledgeView; initialTopicId?: string; initialProjectId?: string; onAuthLost: () => void;
  embedded?: boolean;
}) {
  const [scope, setScope] = useState(initialTopicId ?? (initialProjectId ? "project:" + initialProjectId : ""));
  const topicId = scope.startsWith("project:") ? "" : scope;
  const topic: Topic | undefined = hierarchy.topics.find(item => item.id === topicId);
  const channel = hierarchy.channels.find(item => item.id === topic?.channel_id);
  const project = hierarchy.projects.find(item => item.id === (scope.startsWith("project:") ? scope.slice(8) : channel?.project_id));
  const canWrite = organization.role !== "viewer" || project?.project_role === "owner";
  return <div className={embedded ? "org-knowledge-embedded" : "org-app org-knowledge-page"}>{!embedded && <header className="org-appbar">
    <a className="org-wordmark" href={"/organization.html?organization=" + encodeURIComponent(organization.id)}>{organization.name}</a>
    <h1>{knowledgeTitle(view)}</h1><KnowledgeLinks organizationId={organization.id} topicId={topicId || undefined} />
    <a href={"/organization.html?organization=" + encodeURIComponent(organization.id)}>Chat</a>
  </header>}<section className="org-knowledge-workspace" aria-label={knowledgeTitle(view)}>
    {view === "ledger" ? <>
      <label className="org-workspace-scope">Scope<select value={scope} onChange={event => {
        const next = event.target.value; setScope(next); window.history.replaceState(null, "", knowledgeURL(organization.id, view,
          next.startsWith("project:") ? undefined : next || undefined, next.startsWith("project:") ? next.slice(8) : undefined));
      }}>
        <option value="">All accessible mathematics</option>{hierarchy.projects.map(item => <option key={item.id} value={"project:" + item.id}>{item.name}</option>)}
        {hierarchy.topics.map(item => <option key={item.id} value={item.id}>
          {hierarchy.projects.find(p => p.id === hierarchy.channels.find(c => c.id === item.channel_id)?.project_id)?.name} / {item.name}
        </option>)}
      </select></label>
      {topicId && !topic || scope.startsWith("project:") && !project ? <p role="alert">This research scope is unavailable.</p> : <MathematicsBrowser key={scope} organizationId={organization.id}
        topicId={topic?.kind !== "alpha" ? topicId || undefined : undefined} projectId={project?.id} canWrite={canWrite} onAuthLost={onAuthLost} />}
      <details className="org-workspace-notes"><summary>Shared notes and source records</summary><KnowledgeBrowser organizationId={organization.id}
        hierarchy={hierarchy} canWrite={organization.role !== "viewer"} initialKind="ledger" notesOnly onAuthLost={onAuthLost} /></details>
    </> : view === "bibliography" ? <ResearchBibliography organizationId={organization.id} hierarchy={hierarchy} canWrite={canWrite} onAuthLost={onAuthLost} />
      : <ResearchBlog organizationId={organization.id} hierarchy={hierarchy} canWrite={organization.role !== "viewer"}
        initialProjectId={project?.id} onAuthLost={onAuthLost} />}
  </section></div>;
}
