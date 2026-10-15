import { useEffect, useMemo, useState } from "react";
import { ProjectInfo, WorkflowGraph, fetchWorkflowTemplates } from "./api";
import { useModalDialog } from "./useModalDialog";

interface CommandItem {
  id: string;
  group: string;
  label: string;
  detail: string;
  glyph: string;
  keywords: string;
  run: () => void;
}

function templateName(template: WorkflowGraph): string {
  return template.name.replace(/^Example\s*·\s*/, "");
}

export function CommandPalette({
  projects,
  currentProjectId,
  onSelectProject,
  onSelectChat,
  onSelectWorkflow,
  onNewProject,
  onNewChat,
  onNewWorkflow,
  onOpenSettings,
  onOpenBibliography,
  onOpenPricing,
  onClose,
}: {
  projects: ProjectInfo[];
  currentProjectId: string | null;
  onSelectProject: (projectId: string) => void;
  onSelectChat: (projectId: string, chatId: string) => void;
  onSelectWorkflow: (projectId: string, workflowId: string, runId?: string | null) => void;
  onNewProject: () => void;
  onNewChat: (projectId: string) => void;
  onNewWorkflow: (projectId: string, templateId?: string) => void;
  onOpenSettings: () => void;
  onOpenBibliography: () => void;
  onOpenPricing: () => void;
  onClose: () => void;
}) {
  const [query, setQuery] = useState("");
  const [templates, setTemplates] = useState<WorkflowGraph[]>([]);
  const [activeIndex, setActiveIndex] = useState(0);
  const dialogRef = useModalDialog(onClose);
  const currentProject = projects.find((project) => project.id === currentProjectId) ?? null;

  useEffect(() => {
    void fetchWorkflowTemplates().then(({ workflows }) => setTemplates(workflows)).catch(() => setTemplates([]));
  }, []);

  const items = useMemo(() => {
    const closeThen = (action: () => void) => () => {
      onClose();
      action();
    };
    const values: CommandItem[] = [
      { id: "action:new-project", group: "Actions", label: "New project", detail: "Create a research workspace", glyph: "+", keywords: "create workspace", run: closeThen(onNewProject) },
      { id: "action:bibliography", group: "Actions", label: "Library", detail: "Global sources, documents, and local research views", glyph: "▥", keywords: "bibliography papers references bibtex citations", run: closeThen(onOpenBibliography) },
      { id: "action:prices", group: "Actions", label: "Model prices", detail: "Live token rates and executor billing coverage", glyph: "$", keywords: "cost pricing tokens models openrouter budget", run: closeThen(onOpenPricing) },
      { id: "action:settings", group: "Actions", label: "Settings", detail: "Agents, repositories, and MCP tools", glyph: "⋯", keywords: "preferences api keys models repositories mcp", run: closeThen(onOpenSettings) },
    ];
    if (currentProject) {
      values.splice(1, 0,
        { id: "action:new-chat", group: "Actions", label: `New chat in ${currentProject.name}`, detail: "Choose research or repository workspace", glyph: "+", keywords: "create conversation development repository", run: closeThen(() => onNewChat(currentProject.id)) },
        { id: "action:new-workflow", group: "Actions", label: `New workflow in ${currentProject.name}`, detail: "Choose a template or empty canvas", glyph: "+", keywords: "create graph organization", run: closeThen(() => onNewWorkflow(currentProject.id)) },
      );
    }
    for (const project of projects.filter((project) => !project.archived)) {
      values.push({ id: `project:${project.id}`, group: "Projects", label: project.name, detail: "Project", glyph: "▰", keywords: "workspace", run: closeThen(() => onSelectProject(project.id)) });
      for (const chat of project.chats.filter((chat) => !chat.archived)) {
        values.push({ id: `chat:${project.id}:${chat.id}`, group: "Chats", label: chat.title, detail: chat.mode === "development" ? `${project.name} · ${chat.workspace?.name ?? "repository"}` : project.name, glyph: chat.mode === "development" ? "⌁" : "›", keywords: `conversation ${chat.mode ?? "research"} ${project.name}`, run: closeThen(() => onSelectChat(project.id, chat.id)) });
      }
      for (const workflow of project.workflows ?? []) {
        values.push({ id: `workflow:${project.id}:${workflow.workflow_id}`, group: "Workflows", label: workflow.name, detail: project.name, glyph: "◇", keywords: `graph organization ${project.name}`, run: closeThen(() => onSelectWorkflow(project.id, workflow.workflow_id)) });
        for (const run of (project.workflow_runs ?? []).filter((candidate) => candidate.workflow_id === workflow.workflow_id)) {
          values.push({ id: `run:${project.id}:${run.run_id}`, group: "Saved runs", label: `${workflow.name} · ${run.status} run`, detail: `${project.name}${run.cost?.priced_calls ? ` · $${run.cost.total_usd.toFixed(4)}` : ""}`, glyph: "●", keywords: `artifact result ${project.name} ${run.status}`, run: closeThen(() => onSelectWorkflow(project.id, workflow.workflow_id, run.run_id)) });
        }
      }
    }
    if (currentProject) {
      for (const template of templates) {
        values.push({ id: `template:${template.workflow_id}`, group: "Templates", label: templateName(template), detail: `Use in ${currentProject.name} · ${template.nodes.filter((node) => node.kind === "agent").length} agents`, glyph: "◫", keywords: `starter example ${template.description}`, run: closeThen(() => onNewWorkflow(currentProject.id, template.workflow_id)) });
      }
    }
    const groupOrder = ["Actions", "Projects", "Chats", "Workflows", "Saved runs", "Templates"];
    return values.sort((left, right) => groupOrder.indexOf(left.group) - groupOrder.indexOf(right.group));
  }, [currentProject, onClose, onNewChat, onNewProject, onNewWorkflow, onOpenBibliography, onOpenPricing, onOpenSettings, onSelectChat, onSelectProject, onSelectWorkflow, projects, templates]);

  const filtered = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) return items;
    return items
      .map((item) => {
        const label = item.label.toLowerCase();
        const haystack = `${label} ${item.detail.toLowerCase()} ${item.keywords.toLowerCase()}`;
        const index = haystack.indexOf(normalized);
        const words = normalized.split(/\s+/).filter(Boolean);
        if (index < 0 && !words.every((word) => haystack.includes(word))) return null;
        const score = label.startsWith(normalized) ? 0 : index >= 0 ? index + 10 : 100;
        return { item, score };
      })
      .filter((value): value is { item: CommandItem; score: number } => value !== null)
      .sort((left, right) => left.score - right.score || left.item.label.localeCompare(right.item.label))
      .map(({ item }) => item);
  }, [items, query]);

  useEffect(() => setActiveIndex(0), [query]);
  const active = filtered[Math.min(activeIndex, Math.max(0, filtered.length - 1))];

  return (
    <div className="command-overlay" onClick={onClose}>
      <div ref={dialogRef} className="command-palette" role="dialog" aria-modal="true" aria-label="Quick navigation" onClick={(event) => event.stopPropagation()}>
        <div className="command-input-row">
          <svg aria-hidden="true" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
            <circle cx="7" cy="7" r="4.25" />
            <path d="m10.2 10.2 3.3 3.3" />
          </svg>
          <input
            autoFocus
            value={query}
            placeholder="Go to a project, chat, workflow, run, template, or action…"
            aria-label="Search everything"
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "ArrowDown") {
                event.preventDefault();
                setActiveIndex((index) => Math.min(Math.max(0, filtered.length - 1), index + 1));
              } else if (event.key === "ArrowUp") {
                event.preventDefault();
                setActiveIndex((index) => Math.max(0, index - 1));
              } else if (event.key === "Enter" && active) {
                event.preventDefault();
                active.run();
              }
            }}
          />
          <kbd>esc</kbd>
        </div>
        <div className="command-results" role="listbox" aria-label="Navigation results">
          {filtered.length === 0 && <div className="command-empty">No matching place or action.</div>}
          {filtered.map((item, index) => {
            const showGroup = index === 0 || filtered[index - 1].group !== item.group;
            return (
              <div key={item.id}>
                {showGroup && <div className="command-group">{item.group}</div>}
                <button
                  role="option"
                  aria-selected={item.id === active?.id}
                  className={item.id === active?.id ? "selected" : ""}
                  onMouseEnter={() => setActiveIndex(index)}
                  onClick={item.run}
                >
                  <span className="command-glyph">{item.glyph}</span>
                  <strong>{item.label}</strong>
                  <small>{item.detail}</small>
                </button>
              </div>
            );
          })}
        </div>
        <div className="command-footer"><span>↑↓ navigate</span><span>↵ open</span><span>esc close</span></div>
      </div>
    </div>
  );
}
