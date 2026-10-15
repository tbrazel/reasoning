import {
  type CSSProperties,
  type PointerEvent as ReactPointerEvent,
  ReactNode,
  useEffect,
  useState,
} from "react";
import {
  ChatInfo,
  ProjectInfo,
  WorkflowHead,
} from "./api";
import type { ExplorerLoadState } from "./explorerBootstrap";

const EXPLORER_DEFAULT_WIDTH = 240;
const EXPLORER_MIN_WIDTH = 208;
const EXPLORER_MAX_WIDTH = 384;

function storedExplorerWidth(): number {
  if (typeof window === "undefined") return EXPLORER_DEFAULT_WIDTH;
  const stored = Number(window.localStorage.getItem("reasoning-explorer-width"));
  return Number.isFinite(stored) && stored >= EXPLORER_MIN_WIDTH
    ? Math.min(EXPLORER_MAX_WIDTH, stored)
    : EXPLORER_DEFAULT_WIDTH;
}

function TreeIcon({ children }: { children: ReactNode }) {
  return (
    <svg className="tree-kind-icon" aria-hidden="true" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.25" strokeLinecap="round" strokeLinejoin="round">
      {children}
    </svg>
  );
}

function Chevron({ expanded }: { expanded: boolean }) {
  return <span className={`tree-chevron ${expanded ? "expanded" : ""}`} aria-hidden="true">›</span>;
}

function PinIcon() {
  return (
    <svg className="tree-pin-icon" role="img" aria-label="Pinned" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.25" strokeLinecap="round" strokeLinejoin="round">
      <path d="M5 2.5h6M6 2.5v4l-2 2h8l-2-2v-4M8 8.5v5" />
    </svg>
  );
}

function DevelopmentChatIcon() {
  return (
    <TreeIcon>
      <circle cx="4" cy="3" r="1.5" />
      <circle cx="12" cy="5" r="1.5" />
      <circle cx="4" cy="13" r="1.5" />
      <path d="M4 4.5v7M5.5 10c3.6 0 6.5-1.4 6.5-3.5" />
    </TreeIcon>
  );
}

type DragItem =
  | { kind: "project"; id: string }
  | { kind: "chat"; projectId: string; id: string };

type DropHint = DragItem & { position: "before" | "after" };

function reorderedIds(
  ids: string[],
  draggedId: string,
  targetId: string,
  position: "before" | "after",
): string[] {
  const next = ids.filter((id) => id !== draggedId);
  const targetIndex = next.indexOf(targetId);
  if (targetIndex < 0) return ids;
  next.splice(targetIndex + (position === "after" ? 1 : 0), 0, draggedId);
  return next;
}

function dropPosition(event: React.DragEvent<HTMLElement>): "before" | "after" {
  const bounds = event.currentTarget.getBoundingClientRect();
  return event.clientY >= bounds.top + bounds.height / 2 ? "after" : "before";
}

/** The URL that opens this selection in a fresh tab (see App's
 *  /project/<id>/<chat> routing). Rows are real links so
 *  ctrl/middle-click and mobile long-press get the browser's
 *  open-in-new-tab behavior for free. */
function selectionHref(projectId: string, chatId: string | null): string {
  return (
    `/project/${encodeURIComponent(projectId)}` +
    (chatId ? `/${encodeURIComponent(chatId)}` : "")
  );
}

/** Plain left-clicks stay in-app; modified clicks (new tab/window) are
 *  the browser's. */
function navigate(e: React.MouseEvent, select: () => void) {
  if (e.ctrlKey || e.metaKey || e.shiftKey || e.altKey || e.button !== 0) return;
  e.preventDefault();
  select();
}

export function Sidebar({
  projects,
  loadState,
  loadError,
  onRetry,
  selectedProject,
  selectedChat,
  selectedWorkflow,
  selectedRun,
  onSelectChat,
  onSelectWorkflow,
  onNewProject,
  onNewChat,
  onNewWorkflow,
  onPinProject,
  onRenameProject,
  onArchiveProject,
  onDeleteProject,
  onRenameChat,
  onPinChat,
  onArchiveChat,
  onDeleteChat,
  onRenameWorkflow,
  onPinWorkflow,
  onArchiveWorkflow,
  onDeleteWorkflow,
  onMoveProject,
  onMoveChat,
  onReorderProjects,
  onReorderChats,
}: {
  projects: ProjectInfo[];
  loadState: ExplorerLoadState;
  loadError?: string;
  onRetry: () => void;
  selectedProject: string | null;
  selectedChat: string | null;
  selectedWorkflow: string | null;
  selectedRun: string | null;

  onSelectChat: (projectId: string, chatId: string) => void;
  onSelectWorkflow: (projectId: string, workflowId: string, runId?: string | null) => void;
  onNewProject: () => void;
  onNewChat: (projectId: string) => void;
  onNewWorkflow: (projectId: string) => void;

  onPinProject: (project: ProjectInfo) => void;
  onRenameProject: (project: ProjectInfo) => void;
  onArchiveProject: (project: ProjectInfo) => void;
  onDeleteProject: (project: ProjectInfo) => void;
  onRenameChat: (project: ProjectInfo, chat: ChatInfo) => void;
  onPinChat: (project: ProjectInfo, chat: ChatInfo) => void;
  onArchiveChat: (project: ProjectInfo, chat: ChatInfo) => void;
  onDeleteChat: (project: ProjectInfo, chat: ChatInfo) => void;
  onRenameWorkflow: (project: ProjectInfo, workflow: WorkflowHead) => void;
  onPinWorkflow: (project: ProjectInfo, workflow: WorkflowHead) => void;
  onArchiveWorkflow: (project: ProjectInfo, workflow: WorkflowHead) => void;
  onDeleteWorkflow: (project: ProjectInfo, workflow: WorkflowHead) => void;
  onMoveProject: (projectId: string, direction: "up" | "down") => void;
  onMoveChat: (
    projectId: string,
    chatId: string,
    direction: "up" | "down",
  ) => void;
  onReorderProjects: (projectIds: string[]) => void;
  onReorderChats: (projectId: string, chatIds: string[]) => void;
}) {
  const [width, setWidth] = useState(storedExplorerWidth);
  const [resizing, setResizing] = useState(false);
  const [showArchived, setShowArchived] = useState(false);
  const [menu, setMenu] = useState<string | null>(null);
  const [dragging, setDragging] = useState<DragItem | null>(null);
  const [dropHint, setDropHint] = useState<DropHint | null>(null);
  const [expandedProjects, setExpandedProjects] = useState<Set<string>>(
    () => new Set(selectedProject ? [selectedProject] : []),
  );
  const [expandedWorkflows, setExpandedWorkflows] = useState<Set<string>>(
    () => new Set(selectedWorkflow ? [selectedWorkflow] : []),
  );

  useEffect(() => {
    if (!selectedProject) return;
    setExpandedProjects((current) => new Set(current).add(selectedProject));
  }, [selectedProject]);

  useEffect(() => {
    if (!selectedWorkflow) return;
    setExpandedWorkflows((current) => new Set(current).add(selectedWorkflow));
  }, [selectedWorkflow]);

  useEffect(() => {
    const dismiss = (event: PointerEvent) => {
      const target = event.target instanceof Element ? event.target : null;
      if (!target?.closest(".tree-popover, .tree-action")) setMenu(null);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenu(null);
    };
    document.addEventListener("pointerdown", dismiss);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", dismiss);
      document.removeEventListener("keydown", escape);
    };
  }, []);

  const toggleProject = (projectId: string) => {
    setExpandedProjects((current) => {
      const next = new Set(current);
      if (next.has(projectId)) next.delete(projectId);
      else next.add(projectId);
      return next;
    });
  };
  const toggleWorkflow = (workflowId: string) => {
    setExpandedWorkflows((current) => {
      const next = new Set(current);
      if (next.has(workflowId)) next.delete(workflowId);
      else next.add(workflowId);
      return next;
    });
  };
  const closeAnd = (action: () => void) => {
    setMenu(null);
    action();
  };
  const explorerMaximum = () => Math.max(
    EXPLORER_MIN_WIDTH,
    Math.min(EXPLORER_MAX_WIDTH, window.innerWidth * 0.34),
  );
  const persistWidth = (next: number) => {
    const clamped = Math.min(explorerMaximum(), Math.max(EXPLORER_MIN_WIDTH, next));
    setWidth(clamped);
    window.localStorage.setItem("reasoning-explorer-width", String(Math.round(clamped)));
  };
  const beginResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    const startX = event.clientX;
    const startWidth = width;
    setResizing(true);
    const move = (next: PointerEvent) => {
      setWidth(Math.min(explorerMaximum(), Math.max(EXPLORER_MIN_WIDTH, startWidth + next.clientX - startX)));
    };
    const finish = (next: PointerEvent) => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", finish);
      setResizing(false);
      persistWidth(startWidth + next.clientX - startX);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", finish, { once: true });
  };

  const visibleProjects = showArchived
    ? projects.filter(
        (project) => project.archived || project.chats.some((chat) => chat.archived)
          || project.workflows.some((workflow) => workflow.archived),
      )
    : projects.filter((project) => !project.archived);
  const archivedCount = projects.filter((project) => project.archived).length
    + projects.flatMap((project) => project.chats).filter((chat) => chat.archived).length
    + projects.flatMap((project) => project.workflows).filter((workflow) => workflow.archived).length;
  const projectRows = visibleProjects.map((project) => ({ project }));

  return (
    <aside
      className={`sidebar-col explorer-panel ${resizing ? "sidebar-col-resizing" : ""}`}
      aria-label="Explorer"
      data-pane="explorer"
      style={{ "--explorer-width": `${width}px` } as CSSProperties}
    >
      <div
        className="sidebar-resizer"
        role="separator"
        aria-label="Resize Explorer pane"
        aria-orientation="vertical"
        tabIndex={0}
        onPointerDown={beginResize}
        onKeyDown={(event) => {
          if (event.key === "ArrowLeft") { event.preventDefault(); persistWidth(width - 24); }
          if (event.key === "ArrowRight") { event.preventDefault(); persistWidth(width + 24); }
          if (event.key === "Home") {
            event.preventDefault();
            setWidth(EXPLORER_DEFAULT_WIDTH);
            window.localStorage.removeItem("reasoning-explorer-width");
          }
        }}
        onDoubleClick={() => {
          setWidth(EXPLORER_DEFAULT_WIDTH);
          window.localStorage.removeItem("reasoning-explorer-width");
        }}
      />
      <header className="explorer-header">
        <span>{showArchived ? "Archive" : "Explorer"}</span>
        {!showArchived && loadState !== "fresh" && (
          <small
            className={`explorer-load-state ${loadState}`}
            title={loadState === "error" ? loadError : undefined}
          >
            {loadState === "cached" ? "refreshing" : loadState}
          </small>
        )}
        {!showArchived && <button aria-label="New project" title="New project" onClick={onNewProject}>＋</button>}
      </header>
      {!showArchived && (
        <button
          className="explorer-new"
          onClick={() =>
            selectedProject ? onNewChat(selectedProject) : onNewProject()
          }
        >
          <svg aria-hidden="true" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round">
            <path d="M8 3.5v9M3.5 8h9" />
          </svg>
          <span>{selectedProject ? "New chat" : "New project"}</span>
        </button>
      )}
      <div className="explorer-tree" role="tree" aria-label="Projects, chats, workflows, and runs">
        {projects.length === 0 && (loadState === "loading" || loadState === "cached") && (
          <div className="explorer-empty explorer-restoring" aria-live="polite">
            <span>Restoring projects…</span>
          </div>
        )}
        {projects.length === 0 && loadState === "error" && (
          <div className="explorer-empty" title={loadError}>
            <span>Projects unavailable</span>
            <button onClick={onRetry}>Try again</button>
          </div>
        )}
        {projects.length === 0 && loadState === "fresh" && (
          <div className="explorer-empty">
            <span>No projects</span>
            <button onClick={onNewProject}>New project</button>
          </div>
        )}
        {showArchived && visibleProjects.length === 0 && (
          <div className="explorer-empty">
            <span>Archive is empty</span>
          </div>
        )}
        {projectRows.map((projectRow) => {
          const { project } = projectRow;
          const expanded = expandedProjects.has(project.id);
          const chats = showArchived
            ? project.chats.filter((chat) => project.archived || chat.archived)
            : project.chats.filter((chat) => !chat.archived);
          const workflows = showArchived
            ? project.workflows.filter((workflow) => project.archived || workflow.archived)
            : project.workflows.filter((workflow) => !workflow.archived);

          const projectApprovalPending = project.chats.some(
            (chat) => chat.approval_pending,
          );
          const projectRunning =
            project.chats.some((chat) => chat.running) ||
            (project.workflow_runs ?? []).some((run) =>
              ["pending", "running", "cancelling"].includes(run.status),
            );
          const selectProject = () => {
            if (!expanded) toggleProject(project.id);
            if (chats[0]) onSelectChat(project.id, chats[0].id);
            else if (workflows[0]) onSelectWorkflow(project.id, workflows[0].workflow_id);
          };
          return (
              <div
                key={project.id}
                className={`tree-project ${project.archived ? "archived" : ""} ${
                  dropHint?.kind === "project" && dropHint.id === project.id
                    ? `drop-${dropHint.position}`
                    : ""
                }`}
                role="treeitem"
                aria-expanded={expanded}
                onDragOver={(event) => {
                  if (showArchived || dragging?.kind !== "project" || dragging.id === project.id) return;
                  event.preventDefault();
                  event.dataTransfer.dropEffect = "move";
                  setDropHint({ kind: "project", id: project.id, position: dropPosition(event) });
                }}
                onDrop={(event) => {
                  if (showArchived || dragging?.kind !== "project" || dragging.id === project.id) return;
                  event.preventDefault();
                  const position = dropPosition(event);
                  onReorderProjects(reorderedIds(
                    visibleProjects.map((candidate) => candidate.id),
                    dragging.id,
                    project.id,
                    position,
                  ));
                  setDragging(null);
                  setDropHint(null);
                }}
              >
              <div className={`tree-row tree-project-row ${project.id === selectedProject ? "current-project" : ""}`}>
                {!showArchived && (
                  <button
                    type="button"
                    className="tree-drag-handle"
                    draggable
                    aria-label={`Reorder ${project.name}`}
                    title="Drag, or use arrow keys, to reorder"
                    onKeyDown={(event) => {
                      if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
                      event.preventDefault();
                      onMoveProject(project.id, event.key === "ArrowUp" ? "up" : "down");
                    }}
                    onDragStart={(event) => {
                      event.stopPropagation();
                      event.dataTransfer.effectAllowed = "move";
                      event.dataTransfer.setData("text/plain", project.id);
                      setDragging({ kind: "project", id: project.id });
                    }}
                    onDragEnd={() => {
                      setDragging(null);
                      setDropHint(null);
                    }}
                  >⠿</button>
                )}
                <button className="tree-disclosure" aria-label={`${expanded ? "Collapse" : "Expand"} ${project.name}`} onClick={() => toggleProject(project.id)}>
                  <Chevron expanded={expanded} />
                </button>
                <a
                  className="tree-label"
                  href={selectionHref(project.id, chats[0]?.id ?? null)}
                  onClick={(event) => navigate(event, selectProject)}
                  title={project.name}
                >
                  <span>{project.name}</span>
                  {project.pinned && <PinIcon />}
                  {!showArchived && !expanded && projectApprovalPending ? (
                    <i className="tree-status approval" aria-label="Permission needed" title="Permission needed" />
                  ) : !showArchived && !expanded && projectRunning ? (
                    <i className="tree-status running" aria-label="Running activity" />
                  ) : null}
                </a>
                {!showArchived && !project.archived && <button className="tree-action" aria-label={`Create in ${project.name}`} title="Create chat or workflow" onClick={() => setMenu(menu === `create:${project.id}` ? null : `create:${project.id}`)}>＋</button>}
                <button className="tree-action" aria-label={`Project actions for ${project.name}`} aria-haspopup="menu" aria-expanded={menu === `project:${project.id}`} onClick={() => setMenu(menu === `project:${project.id}` ? null : `project:${project.id}`)}>…</button>
              </div>
              {menu === `create:${project.id}` && (
                <div className="sidebar-action-menu tree-popover" role="menu">
                  <button role="menuitem" onClick={() => closeAnd(() => onNewChat(project.id))}>Chat</button>
                  <button role="menuitem" onClick={() => closeAnd(() => onNewWorkflow(project.id))}>Workflow</button>
                </div>
              )}
              {menu === `project:${project.id}` && (
                <div className="sidebar-action-menu tree-popover" role="menu">
                  {!project.archived && <button role="menuitem" onClick={() => closeAnd(() => onPinProject(project))}>{project.pinned ? "Unpin" : "Pin"}</button>}
                  <button role="menuitem" onClick={() => closeAnd(() => onRenameProject(project))}>Rename</button>
                  <button role="menuitem" onClick={() => closeAnd(() => onMoveProject(project.id, "up"))}>Move up</button>
                  <button role="menuitem" onClick={() => closeAnd(() => onMoveProject(project.id, "down"))}>Move down</button>
                  <button role="menuitem" onClick={() => closeAnd(() => onArchiveProject(project))}>{project.archived ? "Restore" : "Archive"}</button>
                  <button role="menuitem" className="danger" onClick={() => closeAnd(() => onDeleteProject(project))}>Delete…</button>
                </div>
              )}
              {expanded && (
                <div role="group">
                  {chats.map((chat) => {
                    return (
                    <div
                      className={`tree-row-shell ${
                        dropHint?.kind === "chat" && dropHint.projectId === project.id && dropHint.id === chat.id
                          ? `drop-${dropHint.position}`
                          : ""
                      }`}
                      key={chat.id}
                      onDragOver={(event) => {
                        if (
                          showArchived || dragging?.kind !== "chat" ||
                          dragging.projectId !== project.id || dragging.id === chat.id
                        ) return;
                        event.preventDefault();
                        event.stopPropagation();
                        event.dataTransfer.dropEffect = "move";
                        setDropHint({
                          kind: "chat",
                          projectId: project.id,
                          id: chat.id,
                          position: dropPosition(event),
                        });
                      }}
                      onDrop={(event) => {
                        if (
                          showArchived || dragging?.kind !== "chat" ||
                          dragging.projectId !== project.id || dragging.id === chat.id
                        ) return;
                        event.preventDefault();
                        event.stopPropagation();
                        onReorderChats(project.id, reorderedIds(
                          chats.map((candidate) => candidate.id),
                          dragging.id,
                          chat.id,
                          dropPosition(event),
                        ));
                        setDragging(null);
                        setDropHint(null);
                      }}
                    >
                      <div className={`tree-row tree-item-row level-1 ${chat.id === selectedChat ? "selected" : ""} ${chat.archived ? "archived" : ""}`} role="treeitem" aria-selected={chat.id === selectedChat}>
                        {!showArchived ? (
                          <button
                            type="button"
                            className="tree-drag-handle"
                            draggable
                            aria-label={`Reorder ${chat.title}`}
                            title="Drag, or use arrow keys, to reorder"
                            onKeyDown={(event) => {
                              if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
                              event.preventDefault();
                              onMoveChat(project.id, chat.id, event.key === "ArrowUp" ? "up" : "down");
                            }}
                            onDragStart={(event) => {
                              event.stopPropagation();
                              event.dataTransfer.effectAllowed = "move";
                              event.dataTransfer.setData("text/plain", chat.id);
                              setDragging({ kind: "chat", projectId: project.id, id: chat.id });
                            }}
                            onDragEnd={() => {
                              setDragging(null);
                              setDropHint(null);
                            }}
                          >⠿</button>
                        ) : <span className="tree-indent" />}
                        <a
                          className="tree-label"
                          href={selectionHref(project.id, chat.id)}
                          onClick={(event) =>
                            navigate(event, () => onSelectChat(project.id, chat.id))
                          }
                          title={chat.mode === "development" ? `${chat.title} · Development chat · ${chat.workspace?.path ?? "repository workspace"}` : chat.title}
                        >
                          {chat.mode === "development" ? (
                            <DevelopmentChatIcon />
                          ) : (
                            <TreeIcon><path d="M2.5 3.25h11v7.5h-6L4 13v-2.25H2.5z" /></TreeIcon>
                          )}
                          <span>{chat.title}</span>
                          {chat.pinned && <PinIcon />}
                          {chat.approval_pending ? (
                            <i className="tree-status approval" aria-label="Permission needed" title="Permission needed" />
                          ) : chat.running ? (
                            <i className="tree-status running" aria-label="Running" />
                          ) : chat.has_unread ? (
                            <i className="tree-status unread" aria-label="Unread" />
                          ) : null}
                        </a>
                        <button className="tree-action" aria-label={`Chat actions for ${chat.title}`} aria-haspopup="menu" aria-expanded={menu === `chat:${chat.id}`} onClick={() => setMenu(menu === `chat:${chat.id}` ? null : `chat:${chat.id}`)}>…</button>
                      </div>
                      {menu === `chat:${chat.id}` && (
                        <div className="sidebar-action-menu tree-popover tree-item-popover" role="menu">
                          {!showArchived && !chat.archived && <button role="menuitem" onClick={() => closeAnd(() => onPinChat(project, chat))}>{chat.pinned ? "Unpin" : "Pin"}</button>}
                          <button role="menuitem" onClick={() => closeAnd(() => onRenameChat(project, chat))}>Rename</button>
                          <button role="menuitem" onClick={() => closeAnd(() => onMoveChat(project.id, chat.id, "up"))}>Move up</button>
                          <button role="menuitem" onClick={() => closeAnd(() => onMoveChat(project.id, chat.id, "down"))}>Move down</button>
                          <button role="menuitem" onClick={() => closeAnd(() => onArchiveChat(project, chat))}>{chat.archived ? "Restore" : "Archive"}</button>
                          <button role="menuitem" className="danger" onClick={() => closeAnd(() => onDeleteChat(project, chat))}>Delete…</button>
                        </div>
                      )}
                    </div>
                    );
                  })}

                  {workflows.map((workflow) => {
                    const runs = (project.workflow_runs ?? []).filter((run) => run.workflow_id === workflow.workflow_id);
                    const workflowExpanded = expandedWorkflows.has(workflow.workflow_id);
                    return (
                      <div className="tree-workflow" key={workflow.workflow_id} role="treeitem" aria-expanded={runs.length ? workflowExpanded : undefined}>
                        <div className={`tree-row tree-item-row level-1 ${workflow.workflow_id === selectedWorkflow && !selectedRun ? "selected" : ""} ${workflow.archived ? "archived" : ""}`}>
                          {runs.length ? (
                            <button className="tree-disclosure" aria-label={`${workflowExpanded ? "Collapse" : "Expand"} runs for ${workflow.name}`} onClick={() => toggleWorkflow(workflow.workflow_id)}><Chevron expanded={workflowExpanded} /></button>
                          ) : <span className="tree-indent" />}
                          <button className="tree-label" aria-current={workflow.workflow_id === selectedWorkflow && !selectedRun ? "page" : undefined} onClick={() => onSelectWorkflow(project.id, workflow.workflow_id)} title={workflow.name}>
                            <TreeIcon><circle cx="3" cy="4" r="1.3" /><circle cx="12.5" cy="3" r="1.3" /><circle cx="12.5" cy="12.5" r="1.3" /><path d="M4.3 4h2.2A2.5 2.5 0 0 1 9 6.5V10a2.5 2.5 0 0 0 2.2 2.5M9 7V5.5A2.5 2.5 0 0 1 11.2 3" /></TreeIcon>
                            <span>{workflow.name}</span>
                            {workflow.pinned && <PinIcon />}
                          </button>
                          <button className="tree-action" aria-label={`Workflow actions for ${workflow.name}`} aria-haspopup="menu" aria-expanded={menu === `workflow:${workflow.workflow_id}`} onClick={() => setMenu(menu === `workflow:${workflow.workflow_id}` ? null : `workflow:${workflow.workflow_id}`)}>…</button>
                        </div>
                        {menu === `workflow:${workflow.workflow_id}` && (
                          <div className="sidebar-action-menu tree-popover tree-item-popover" role="menu">
                            {!showArchived && !workflow.archived && <button role="menuitem" onClick={() => closeAnd(() => onPinWorkflow(project, workflow))}>{workflow.pinned ? "Unpin" : "Pin"}</button>}
                            <button role="menuitem" onClick={() => closeAnd(() => onRenameWorkflow(project, workflow))}>Rename</button>
                            <button role="menuitem" onClick={() => closeAnd(() => onArchiveWorkflow(project, workflow))}>{workflow.archived ? "Restore" : "Archive"}</button>
                            <button role="menuitem" className="danger" onClick={() => closeAnd(() => onDeleteWorkflow(project, workflow))}>Delete…</button>
                          </div>
                        )}
                        {workflowExpanded && runs.length > 0 && (
                          <div role="group">
                            {runs.map((run) => (
                              <div className={`tree-row tree-item-row level-2 ${run.run_id === selectedRun ? "selected" : ""}`} role="treeitem" aria-selected={run.run_id === selectedRun} key={run.run_id}>
                                <span className="tree-indent" />
                                <button className="tree-label" onClick={() => onSelectWorkflow(project.id, workflow.workflow_id, run.run_id)} title={`${run.status} run`}>
                                  <i className={`tree-run-status run-${run.status}`} aria-hidden="true" />
                                  <span>{run.status} run{run.cost && run.cost.priced_calls > 0 ? ` · $${run.cost.total_usd.toFixed(4)}` : ""}</span>
                                </button>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
              </div>
          );
        })}
      </div>
      <footer className="explorer-footer">
        <button
          aria-pressed={showArchived}
          onClick={() => setShowArchived((value) => !value)}
        >
          <TreeIcon>
            <path d="M2.5 5.25h11v8h-11zM2 2.5h12v2.75H2zM6 8h4" />
          </TreeIcon>
          <span>{showArchived ? "Back to Explorer" : "Archive"}</span>
          {!showArchived && <span className="archive-count">{archivedCount}</span>}
        </button>
      </footer>
    </aside>
  );
}
