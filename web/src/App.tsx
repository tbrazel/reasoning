import { lazy, useCallback, useEffect, useRef, useState } from "react";
import {
  ChatInfo,
  BibliographyConnection,
  ProjectInfo,
  WorkflowGraph,
  WorkflowHead,
  createChat,
  createProject,
  createWorkflow,
  deleteChat,
  deleteProject,
  deleteWorkflow,
  deleteWorkflowTemplate,
  fetchProjects,
  fetchWorkflowTemplates,
  moveChat,
  moveProject,
  reorderChats,
  reorderProjects,
  updateChat,
  updateProject,
  updateWorkflow,
} from "./api";
import {
  ARTIFACT_REVEAL_EVENT,
  type ArtifactRevealRequest,
} from "./ArtifactActions";
import { ActivityBar } from "./ActivityBar";
import { AsyncSurface } from "./AsyncSurface";
import type { BibliographyContext } from "./Bibliography";
import { Chat } from "./Chat";
import { CommandPalette } from "./CommandPalette";
import {
  ConfirmationDialog,
  type ConfirmationRequest,
} from "./ConfirmationDialog";
import { LedgerPanel } from "./Ledger";
import { NewChatDialog } from "./NewChatDialog";
import { SettingsDialog, type SettingsSection } from "./Settings";
import { Sidebar } from "./Sidebar";
import { useModalDialog } from "./useModalDialog";
import { ExplorerLoadState, readExplorerBootstrap } from "./explorerBootstrap";
import { authoredWorkflowTarget } from "./workflowNavigation";

const ArtifactPane = lazy(() =>
  import("./Artifact").then((module) => ({ default: module.ArtifactPane })),
);
const Bibliography = lazy(() =>
  import("./Bibliography").then((module) => ({ default: module.Bibliography })),
);

const PriceBrowser = lazy(() =>
  import("./PriceBrowser").then((module) => ({ default: module.PriceBrowser })),
);

const CollaborationWorkspace = lazy(() =>
  import("./CollaborationWorkspace").then((module) => ({ default: module.CollaborationWorkspace })),
);
const WorkflowStudio = lazy(() =>
  import("./WorkflowStudio").then((module) => ({ default: module.WorkflowStudio })),
);

interface Selection {
  projectId: string | null;
  chatId: string | null;
  workflowId: string | null;
  runId: string | null;
}

interface ArtifactSelection {
  projectId: string;
  name: string;
}

const MOBILE_QUERY = "(max-width: 768px)";
const COMPACT_DESKTOP_QUERY = "(max-width: 1180px)";
const MAC_PLATFORM = navigator.platform.toLowerCase().includes("mac");
const MAC_ELECTRON = navigator.userAgent.includes("Electron/") && MAC_PLATFORM;

function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(
    () => window.matchMedia(query).matches,
  );
  useEffect(() => {
    const mql = window.matchMedia(query);
    const onChange = () => setMatches(mql.matches);
    mql.addEventListener("change", onChange);
    setMatches(mql.matches);
    return () => mql.removeEventListener("change", onChange);
  }, [query]);
  return matches;
}

/** Styled replacement for window.prompt: names a new project or chat. */
function NameDialog({
  title,
  placeholder,
  initialValue,
  submitLabel = "create",
  allowEmpty,
  onSubmit,
  onClose,
}: {
  title: string;
  placeholder: string;
  initialValue?: string;
  submitLabel?: string;
  allowEmpty?: boolean;
  onSubmit: (value: string) => void;
  onClose: () => void;
}) {
  const [value, setValue] = useState(initialValue ?? "");
  const dialogRef = useModalDialog(onClose);
  const submit = () => {
    const trimmed = value.trim();
    if (!trimmed && !allowEmpty) return;
    onClose();
    onSubmit(trimmed);
  };
  return (
    <div className="name-overlay" onClick={onClose}>
      <div ref={dialogRef} className="name-dialog" role="dialog" aria-modal="true" aria-labelledby="name-dialog-title" onClick={(e) => e.stopPropagation()}>
        <div className="name-dialog-title" id="name-dialog-title">{title}</div>
        <input
          autoFocus
          value={value}
          placeholder={placeholder}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") submit();
            else if (e.key === "Escape") onClose();
          }}
        />
        <div className="name-dialog-actions">
          <button className="icon-btn" onClick={onClose}>
            cancel
          </button>
          <button
            className="confirm"
            disabled={!allowEmpty && !value.trim()}
            onClick={submit}
          >
            {submitLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

interface NameRequest {
  title: string;
  placeholder: string;
  initialValue?: string;
  submitLabel?: string;
  allowEmpty?: boolean;
  onSubmit: (value: string) => void;
}

function NewWorkflowDialog({
  projectName,
  initialTemplateId,
  initialName,
  onCreate,
  onClose,
}: {
  projectName: string;
  initialTemplateId?: string;
  initialName?: string;
  onCreate: (name: string, templateId?: string) => Promise<void>;
  onClose: () => void;
}) {
  const [templates, setTemplates] = useState<WorkflowGraph[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [name, setName] = useState(initialName ?? "");
  const [deleteArmed, setDeleteArmed] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const dialogRef = useModalDialog(onClose);

  useEffect(() => {
    void fetchWorkflowTemplates()
      .then(({ workflows }) => {
        setTemplates(workflows);
        const initial = workflows.find((template) => template.workflow_id === initialTemplateId);
        if (initial) {
          setSelected(initial.workflow_id);
          setName(initial.name.replace(/^Example\s*·\s*/, ""));
        }
      })
      .catch((reason) => setError(String(reason)))
      .finally(() => setLoading(false));
  }, [initialTemplateId]);

  const choose = (template: WorkflowGraph | null) => {
    setSelected(template?.workflow_id ?? null);
    setDeleteArmed(null);
    if (template) setName(template.name.replace(/^Example\s*·\s*/, ""));
  };
  const remove = (template: WorkflowGraph) => {
    if (deleteArmed !== template.workflow_id) {
      setDeleteArmed(template.workflow_id);
      return;
    }
    setBusy(true);
    setError("");
    void deleteWorkflowTemplate(template.workflow_id)
      .then(() => {
        setTemplates((current) => current.filter((item) => item.workflow_id !== template.workflow_id));
        if (selected === template.workflow_id) {
          setSelected(null);
          setName("");
        }
        setDeleteArmed(null);
      })
      .catch((reason) => setError(String(reason)))
      .finally(() => setBusy(false));
  };
  const submit = () => {
    const trimmed = name.trim();
    if (!trimmed || busy) return;
    setBusy(true);
    setError("");
    void onCreate(trimmed, selected ?? undefined)
      .then(onClose)
      .catch((reason) => setError(String(reason)))
      .finally(() => setBusy(false));
  };

  return (
    <div className="name-overlay" onClick={onClose}>
      <div ref={dialogRef} className="name-dialog workflow-create-dialog" role="dialog" aria-modal="true" aria-labelledby="workflow-create-title" onClick={(event) => event.stopPropagation()}>
        <div>
          <div className="name-dialog-title" id="workflow-create-title">New workflow</div>
          <p>Choose a starting point for {projectName}. The new graph belongs only to this project.</p>
        </div>
        <div className="workflow-template-grid">
          <div className={`workflow-template-card ${selected === null ? "selected" : ""}`}>
            <button className="workflow-template-select" aria-pressed={selected === null} onClick={() => choose(null)}>
              <span className="workflow-template-mark">＋</span>
              <strong>Empty workspace</strong>
              <small>Build a scientific organization from the canvas.</small>
            </button>
          </div>
          {templates.map((template) => {
            const agentCount = template.nodes.filter((node) => node.kind === "agent").length;
            return (
              <div className={`workflow-template-card ${selected === template.workflow_id ? "selected" : ""}`} key={template.workflow_id}>
                <button className="workflow-template-select" aria-pressed={selected === template.workflow_id} onClick={() => choose(template)}>
                  <span className="workflow-template-mark">◇</span>
                  <strong>{template.name.replace(/^Example\s*·\s*/, "")}</strong>
                  <small>{template.description || "Reusable workflow template."}</small>
                  <span className="workflow-template-meta">{agentCount} agent{agentCount === 1 ? "" : "s"} · {template.iterations} iteration{template.iterations === 1 ? "" : "s"}</span>
                </button>
                <button className={`workflow-template-delete ${deleteArmed === template.workflow_id ? "armed" : ""}`} aria-label={`Delete template ${template.name}`} disabled={busy} onClick={() => remove(template)}>{deleteArmed === template.workflow_id ? "confirm delete" : "delete"}</button>
              </div>
            );
          })}
        </div>
        {loading && <div className="workflow-template-loading">loading templates…</div>}
        <label className="workflow-create-name">workflow name<input autoFocus value={name} placeholder="Name this project workflow" onChange={(event) => setName(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") submit(); }} /></label>
        {error && <div className="workflow-create-error">{error}</div>}
        <div className="name-dialog-actions">
          <button className="icon-btn" onClick={onClose}>cancel</button>
          <button className="confirm" disabled={!name.trim() || busy} onClick={submit}>{busy ? "working…" : selected ? "use template" : "create blank"}</button>
        </div>
      </div>
    </div>
  );
}

// The open project/chat live in /project/<id>/<chat>. Workflow and run
// selections remain query parameters because they are peers of chats,
// while the fragment stays reserved for ledger deep-links (#clm_…).
function selectionFromUrl(): Selection {
  const [prefix, pathProjectId, pathChatId] = window.location.pathname
    .split("/")
    .filter(Boolean)
    .map(decodeURIComponent);
  const params = new URLSearchParams(window.location.search);
  return {
    projectId: prefix === "project" ? pathProjectId ?? null : null,
    chatId: prefix === "project" ? pathChatId ?? null : null,
    workflowId: params.get("w"),
    runId: params.get("r"),
  };
}

function artifactFromUrl(): ArtifactSelection | null {
  const params = new URLSearchParams(window.location.search);
  const name = params.get("artifact");
  const projectId = params.get("artifactProject") ?? selectionFromUrl().projectId;
  return name !== null && projectId ? { projectId, name } : null;
}

function writeArtifactToUrl(artifact: ArtifactSelection | null) {
  const url = new URL(window.location.href);
  if (artifact) {
    url.searchParams.set("artifact", artifact.name);
    url.searchParams.set("artifactProject", artifact.projectId);
  } else {
    url.searchParams.delete("artifact");
    url.searchParams.delete("artifactProject");
  }
  window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
}

function writeSelectionToUrl(next: Selection, keepHash: boolean) {
  const params = new URLSearchParams();
  const current = new URLSearchParams(window.location.search);
  if (next.workflowId) params.set("w", next.workflowId);
  if (next.runId) params.set("r", next.runId);
  for (const key of ["artifact", "artifactProject"]) {
    const value = current.get(key);
    if (value) params.set(key, value);
  }
  const query = params.toString();
  const path = next.projectId
    ? `/project/${encodeURIComponent(next.projectId)}` +
      (next.chatId ? `/${encodeURIComponent(next.chatId)}` : "")
    : "/";
  const hash = keepHash && window.location.hash !== "#" ? window.location.hash : "";
  window.history.replaceState(null, "", `${path}${query ? `?${query}` : ""}${hash}`);
}

export const PRODUCTION_SHELL = "chat-first" as const;

export const CHAT_FIRST_SHELL_DEFAULTS = {
  explorerOpen: true,
  ledgerOpen: false,
} as const;

export function App() {
  const [explorerBootstrap] = useState(readExplorerBootstrap);
  const [projects, setProjects] = useState<ProjectInfo[]>(
    () => explorerBootstrap?.projects ?? [],
  );
  const projectsRef = useRef(projects);
  projectsRef.current = projects;
  const [explorerState, setExplorerState] = useState<ExplorerLoadState>(
    () => explorerBootstrap ? "cached" : "loading",
  );
  const [explorerError, setExplorerError] = useState("");
  const [selection, setSelectionState] = useState<Selection>(selectionFromUrl);
  const selectionRef = useRef(selection);
  selectionRef.current = selection;
  // The desktop shell keeps project navigation beside the conversation while
  // context opens on demand at the other edge. Phones collapse both panes so
  // the transcript remains the usable primary surface.
  const isMobile = useMediaQuery(MOBILE_QUERY);
  const isCompactDesktop = useMediaQuery(COMPACT_DESKTOP_QUERY);
  const [sidebarOpen, setSidebarOpen] = useState<boolean>(
    () => window.matchMedia(MOBILE_QUERY).matches
      ? false
      : CHAT_FIRST_SHELL_DEFAULTS.explorerOpen,
  );
  const [ledgerOpen, setLedgerOpen] = useState<boolean>(CHAT_FIRST_SHELL_DEFAULTS.ledgerOpen);
  const [artifactSelection, setArtifactSelection] = useState<ArtifactSelection | null>(artifactFromUrl);
  const [artifactDirty, setArtifactDirty] = useState(false);

  const [confirmRequest, setConfirmRequest] = useState<ConfirmationRequest | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [settingsSection, setSettingsSection] = useState<SettingsSection | null>(null);
  const [bibliographyOpen, setBibliographyOpen] = useState(false);
  const [bibliographyContext, setBibliographyContext] = useState<BibliographyContext>({
    scope: "global",
    projectId: null,
    projectName: null,
    chatId: null,
    chatName: null,
  });
  const [pricingOpen, setPricingOpen] = useState(false);

  const [collaborationOpen, setCollaborationOpen] = useState(false);
  const bump = useCallback(() => setRefreshKey((k) => k + 1), []);
  const closeArtifact = useCallback((afterClose?: () => void) => {
    const finish = () => {
      writeArtifactToUrl(null);
      setArtifactDirty(false);
      setArtifactSelection(null);
      afterClose?.();
    };
    if (artifactDirty) {
      setConfirmRequest({
        title: "Discard unsaved edits?",
        message: "The current inspector has changes that have not been saved. Discard them and continue?",
        confirmLabel: "discard edits",
        onConfirm: finish,
      });
      return false;
    }
    finish();
    return true;
  }, [artifactDirty]);
  const showArtifact = useCallback((name: string, projectId?: string | null) => {
    const targetProject = projectId ?? selectionRef.current.projectId;
    if (!targetProject) return;
    const artifact = { projectId: targetProject, name };
    const isCurrent = artifactSelection?.projectId === artifact.projectId &&
      artifactSelection.name === artifact.name;
    if (isCurrent) {
      if (isCompactDesktop) setSidebarOpen(false);
      setLedgerOpen(false);
      return;
    }
    closeArtifact(() => {
      if (isCompactDesktop) setSidebarOpen(false);
      setLedgerOpen(false);
      writeArtifactToUrl(artifact);
      setArtifactSelection(artifact);
    });
  }, [artifactSelection, closeArtifact, isCompactDesktop]);

  const openBibliography = useCallback(() => {
    setCollaborationOpen(false);
    setPricingOpen(false);
    setBibliographyContext({
      scope: "global",
      projectId: null,
      projectName: null,
      chatId: null,
      chatName: null,
    });
    setBibliographyOpen(true);
  }, []);
  const openPricing = useCallback(() => {
    closeArtifact(() => {
      setBibliographyOpen(false);
      setLedgerOpen(false);
      setCollaborationOpen(false);
      setPricingOpen(true);
    });
  }, [closeArtifact]);

  const openCollaboration = useCallback(() => {
    closeArtifact(() => {
      setBibliographyOpen(false);
      setPricingOpen(false);
      setLedgerOpen(false);
      setCollaborationOpen(true);
    });
  }, [closeArtifact]);

  // Ledger slugs and artifact paths share the optional right-hand inspector
  // position. Opening one closes the other while leaving the primary work
  // surface mounted and interactive.
  useEffect(() => {
    const onLedger = () => {
      closeArtifact(() => setLedgerOpen(true));
    };
    const onArtifact = (event: Event) => {
      const detail = (event as CustomEvent<string | ArtifactSelection>).detail;
      if (typeof detail === "string") showArtifact(detail);
      else if (detail && typeof detail.name === "string") {
        showArtifact(detail.name, detail.projectId);
      }
    };
    const onArtifactReveal = (event: Event) => {
      const request = (event as CustomEvent<ArtifactRevealRequest>).detail;
      const targetProject = request?.projectId ?? selectionRef.current.projectId;
      const bridge = window.reasoningDesktop;
      if (!request || !targetProject || !bridge) {
        request?.reject(new Error("Select a project before revealing its artifact"));
        return;
      }
      void bridge.revealArtifact(targetProject, request.name)
        .then(request.resolve, request.reject);
    };
    window.addEventListener("ledger-open", onLedger);
    window.addEventListener("artifact-open", onArtifact);
    window.addEventListener(ARTIFACT_REVEAL_EVENT, onArtifactReveal);
    return () => {
      window.removeEventListener("ledger-open", onLedger);
      window.removeEventListener("artifact-open", onArtifact);
      window.removeEventListener(ARTIFACT_REVEAL_EVENT, onArtifactReveal);
    };
  }, [closeArtifact, showArtifact]);

  const setSelection = useCallback((next: Selection) => {
    // Keep the claim hash only when staying inside the same project's
    // ledger; a project switch invalidates it.
    writeSelectionToUrl(next, next.projectId === selectionRef.current.projectId);
    setPricingOpen(false);
    setCollaborationOpen(false);
    setSelectionState(next);
  }, []);

  const reloadProjects = useCallback(
    () =>
      fetchProjects().then((payload) => {
        projectsRef.current = payload.projects;
        setProjects(payload.projects);
        setExplorerState("fresh");
        setExplorerError("");
        return payload.projects;
      }).catch((error: unknown) => {
        setExplorerState("error");
        setExplorerError(error instanceof Error ? error.message : String(error));
        throw error;
      }),
    [],
  );

  const workflowTurnBaselinesRef = useRef<Map<string, Map<string, string>>>(new Map());
  const noteChatStatus = useCallback((
    projectId: string,
    chatId: string,
    phase: "turn.started" | "turn.completed" | "activity",
  ) => {
    const key = `${projectId}/${chatId}`;
    if (phase === "turn.started" && !workflowTurnBaselinesRef.current.has(key)) {
      const project = projectsRef.current.find((candidate) => candidate.id === projectId);
      workflowTurnBaselinesRef.current.set(
        key,
        new Map((project?.workflows ?? []).map((workflow) => [workflow.workflow_id, workflow.revision_id])),
      );
    }
    if (phase !== "turn.completed") void reloadProjects().catch(() => undefined);
  }, [reloadProjects]);

  const openAuthoredWorkflow = useCallback((
    projectId: string,
    chatId: string,
    authored: { workflow_id: string; revision_id: string }[],
  ) => {
    const key = `${projectId}/${chatId}`;
    const baseline = workflowTurnBaselinesRef.current.get(key) ?? new Map<string, string>();
    workflowTurnBaselinesRef.current.delete(key);
    void reloadProjects().then((loaded) => {
      const project = loaded.find((candidate) => candidate.id === projectId);
      if (!project) return;
      const targetId = authoredWorkflowTarget(project, baseline, authored);
      if (!targetId) return;
      setSelection({ projectId, chatId: null, workflowId: targetId, runId: null });
    }).catch(() => undefined);
  }, [reloadProjects, setSelection]);

  const hasLiveProjectActivity = projects.some(
    (project) =>
      project.chats.some((chat) => chat.running || chat.approval_pending) ||
      project.workflow_runs.some((run) =>
        ["pending", "running", "cancelling"].includes(run.status),
      ),
  );

  useEffect(() => {
    if (!hasLiveProjectActivity) return;
    const refresh = () => {
      if (!document.hidden) void reloadProjects().catch(() => undefined);
    };
    const interval = window.setInterval(refresh, 2000);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      window.clearInterval(interval);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, [hasLiveProjectActivity, reloadProjects]);

  // Re-resolve the URL's selection against what the server actually has.
  // Runs at mount and whenever a component discovers its project is gone
  // (the server behind this origin may have changed, e.g. a different
  // workbench root). No fallback to "some project": a URL that names
  // nothing — or something that no longer exists — opens nothing.
  const revalidate = useCallback(() => {
    void reloadProjects().then((loaded) => {
      setSelectionState((current) => {
        const project = loaded.find((p) => p.id === current.projectId) ?? null;
        const run = project?.workflow_runs?.find((item) => item.run_id === current.runId) ?? null;
        const workflow = project?.workflows?.find(
          (item) => item.workflow_id === (run?.workflow_id ?? current.workflowId),
        ) ?? null;
        const chat = project?.chats.find((c) => c.id === current.chatId) ?? null;
        const fallbackChat = project?.chats[0] ?? null;
        const fallbackWorkflow = project?.workflows?.[0] ?? null;
        const next = run && workflow
          ? { projectId: project!.id, chatId: null, workflowId: workflow.workflow_id, runId: run.run_id }
          : workflow
            ? { projectId: project!.id, chatId: null, workflowId: workflow.workflow_id, runId: null }
            : chat
              ? { projectId: project!.id, chatId: chat.id, workflowId: null, runId: null }
              : fallbackChat
                ? { projectId: project!.id, chatId: fallbackChat.id, workflowId: null, runId: null }
                : fallbackWorkflow
                  ? { projectId: project!.id, chatId: null, workflowId: fallbackWorkflow.workflow_id, runId: null }
                  : { projectId: project?.id ?? null, chatId: null, workflowId: null, runId: null };
        if (
          next.projectId !== current.projectId ||
          next.chatId !== current.chatId ||
          next.workflowId !== current.workflowId ||
          next.runId !== current.runId
        ) {
          writeSelectionToUrl(next, next.projectId === current.projectId);
        }
        return next;
      });
    }).catch(() => undefined);
  }, [reloadProjects]);

  useEffect(revalidate, [revalidate]);

  // Size the app to the VISUAL viewport: when the mobile keyboard opens
  // the layout viewport keeps its height, so without this the input row
  // (and its send button) hides behind the keyboard; and when Firefox's
  // bottom URL bar collapses, dvh lags and leaves a dead strip. The
  // height × scale product keeps pinch-zoom from shrinking the app.
  useEffect(() => {
    const vv = window.visualViewport;
    if (!vv) return;
    const apply = () => {
      document.documentElement.style.setProperty(
        "--app-height",
        `${Math.round(vv.height * vv.scale)}px`,
      );
      // counter the browser scrolling the (unshrunk) layout viewport
      window.scrollTo(0, 0);
    };
    apply();
    vv.addEventListener("resize", apply);
    vv.addEventListener("scroll", apply);
    return () => {
      vv.removeEventListener("resize", apply);
      vv.removeEventListener("scroll", apply);
    };
  }, []);

  const [nameRequest, setNameRequest] = useState<NameRequest | null>(null);
  const [newChatProjectId, setNewChatProjectId] = useState<string | null>(null);
  const [workflowDialog, setWorkflowDialog] = useState<{ projectId: string; templateId?: string; initialName?: string; initialPrompt?: string } | null>(null);
  const [workflowDraft, setWorkflowDraft] = useState<{ projectId: string; workflowId: string; text: string } | null>(null);
  const [commandOpen, setCommandOpen] = useState(false);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey) || event.key.toLowerCase() !== "p") return;
      event.preventDefault();
      setCommandOpen((open) => !open);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  useEffect(() => {
    if (!ledgerOpen || !isCompactDesktop) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || document.querySelector('[role="dialog"]')) return;
      event.preventDefault();
      setLedgerOpen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [isCompactDesktop, ledgerOpen]);

  const onNewProject = useCallback(() => {
    setNameRequest({
      title: "New project",
      placeholder: "project name",
      onSubmit: (name) => {
        if (!name) return;
        void createProject(name)
          .then((created) =>
            createChat(created.id).then((chat) => {
              setSelection({ projectId: created.id, chatId: chat.id, workflowId: null, runId: null });
            }),
          )
          .then(reloadProjects);
      },
    });
  }, [reloadProjects, setSelection]);

  const onNewChat = useCallback(
    (projectId: string) => {
      setNewChatProjectId(projectId);
    },
    [],
  );

  const onNewWorkflow = useCallback(
    (projectId: string, templateId?: string) => {
      setWorkflowDialog({ projectId, templateId });
    },
    [],
  );

  const draftInResearchChat = useCallback((projectId: string, message: string, fallbackTitle: string) => {
    const target = projects.find((candidate) => candidate.id === projectId);
    const openDraft = (chatId: string) => {
      setBibliographyOpen(false);
      setSelection({ projectId, chatId, workflowId: null, runId: null });
      window.setTimeout(() => window.dispatchEvent(new CustomEvent("chat-draft", { detail: message })), 0);
    };
    const existing = target?.chats.find(
      (candidate) => !candidate.archived && candidate.mode !== "development",
    );
    if (existing) openDraft(existing.id);
    else void createChat(projectId, fallbackTitle).then((created) => {
      openDraft(created.id);
      return reloadProjects();
    });
  }, [projects, reloadProjects, setSelection]);

  const onRenameProject = useCallback((target: ProjectInfo) => {
    setNameRequest({
      title: "Rename project",
      placeholder: "project name",
      initialValue: target.name,
      submitLabel: "rename",
      onSubmit: (name) => void updateProject(target.id, { name }).then(reloadProjects),
    });
  }, [reloadProjects]);

  const onPinProject = useCallback((target: ProjectInfo) => {
    void updateProject(target.id, { pinned: !target.pinned }).then(reloadProjects);
  }, [reloadProjects]);

  const onArchiveProject = useCallback((target: ProjectInfo) => {
    const archiving = !target.archived;
    void updateProject(target.id, { archived: archiving }).then(() => {
      if (archiving && selectionRef.current.projectId === target.id) {
        setSelection({ projectId: null, chatId: null, workflowId: null, runId: null });
      }
      return reloadProjects();
    });
  }, [reloadProjects, setSelection]);

  const onDeleteProject = useCallback((target: ProjectInfo) => {
    setConfirmRequest({
      title: `Delete “${target.name}”?`,
      message: "The project, its chats, ledger, artifacts, and run records will leave the app. Reasoning keeps a recoverable copy in the workbench trash.",
      confirmLabel: "delete project",
      onConfirm: () => {
        void deleteProject(target.id).then(() => {
          if (selectionRef.current.projectId === target.id) setSelection({ projectId: null, chatId: null, workflowId: null, runId: null });
          return reloadProjects();
        });
      },
    });
  }, [reloadProjects, setSelection]);

  const onRenameChat = useCallback((target: ProjectInfo, targetChat: ChatInfo) => {
    setNameRequest({
      title: "Rename chat",
      placeholder: "chat title",
      initialValue: targetChat.title,
      submitLabel: "rename",
      onSubmit: (title) => void updateChat(target.id, targetChat.id, { title }).then(reloadProjects),
    });
  }, [reloadProjects]);

  const onPinChat = useCallback((target: ProjectInfo, targetChat: ChatInfo) => {
    void updateChat(target.id, targetChat.id, { pinned: !targetChat.pinned })
      .then(reloadProjects);
  }, [reloadProjects]);

  const onArchiveChat = useCallback((target: ProjectInfo, targetChat: ChatInfo) => {
    const archiving = !targetChat.archived;
    void updateChat(target.id, targetChat.id, { archived: archiving }).then(() => {
      if (archiving && selectionRef.current.chatId === targetChat.id) {
        const replacement = target.chats.find((candidate) => candidate.id !== targetChat.id && !candidate.archived);
        setSelection({ projectId: target.id, chatId: replacement?.id ?? null, workflowId: null, runId: null });
      }
      return reloadProjects();
    });
  }, [reloadProjects, setSelection]);

  const onDeleteChat = useCallback((target: ProjectInfo, targetChat: ChatInfo) => {
    setConfirmRequest({
      title: `Delete “${targetChat.title}”?`,
      message: "The chat and its local session records will leave the project. Reasoning keeps a recoverable copy in the project trash.",
      confirmLabel: "delete chat",
      onConfirm: () => {
        void deleteChat(target.id, targetChat.id).then(() => {
          if (selectionRef.current.chatId === targetChat.id) {
            const replacement = target.chats.find((candidate) => candidate.id !== targetChat.id && !candidate.archived);
            setSelection({ projectId: target.id, chatId: replacement?.id ?? null, workflowId: null, runId: null });
          }
          return reloadProjects();
        });
      },
    });
  }, [reloadProjects, setSelection]);

  const onRenameWorkflow = useCallback((target: ProjectInfo, targetWorkflow: WorkflowHead) => {
    setNameRequest({
      title: "Rename workflow",
      placeholder: "workflow name",
      initialValue: targetWorkflow.name,
      submitLabel: "rename",
      onSubmit: (name) => void updateWorkflow(
        target.id,
        targetWorkflow.workflow_id,
        { name },
      ).then(reloadProjects),
    });
  }, [reloadProjects]);

  const onPinWorkflow = useCallback((target: ProjectInfo, targetWorkflow: WorkflowHead) => {
    void updateWorkflow(target.id, targetWorkflow.workflow_id, {
      pinned: !targetWorkflow.pinned,
    }).then(reloadProjects);
  }, [reloadProjects]);

  const onArchiveWorkflow = useCallback((target: ProjectInfo, targetWorkflow: WorkflowHead) => {
    const archiving = !targetWorkflow.archived;
    void updateWorkflow(target.id, targetWorkflow.workflow_id, { archived: archiving })
      .then(() => {
        if (archiving && selectionRef.current.workflowId === targetWorkflow.workflow_id) {
          const replacementChat = target.chats.find((candidate) => !candidate.archived);
          const replacementWorkflow = target.workflows.find(
            (candidate) => candidate.workflow_id !== targetWorkflow.workflow_id
              && !candidate.archived,
          );
          setSelection(replacementChat
            ? { projectId: target.id, chatId: replacementChat.id, workflowId: null, runId: null }
            : replacementWorkflow
              ? { projectId: target.id, chatId: null, workflowId: replacementWorkflow.workflow_id, runId: null }
              : { projectId: target.id, chatId: null, workflowId: null, runId: null });
        }
        return reloadProjects();
      });
  }, [reloadProjects, setSelection]);

  const onDeleteWorkflow = useCallback((target: ProjectInfo, targetWorkflow: WorkflowHead) => {
    setConfirmRequest({
      title: `Delete “${targetWorkflow.name}”?`,
      message: "The workflow will leave the Explorer. Its immutable revisions and saved run artifacts remain recoverable for provenance.",
      confirmLabel: "delete workflow",
      onConfirm: () => {
        void deleteWorkflow(target.id, targetWorkflow.workflow_id).then(() => {
          if (selectionRef.current.workflowId === targetWorkflow.workflow_id) {
            const replacementChat = target.chats.find((candidate) => !candidate.archived);
            const replacementWorkflow = target.workflows.find(
              (candidate) => candidate.workflow_id !== targetWorkflow.workflow_id
                && !candidate.archived,
            );
            setSelection(replacementChat
              ? { projectId: target.id, chatId: replacementChat.id, workflowId: null, runId: null }
              : replacementWorkflow
                ? { projectId: target.id, chatId: null, workflowId: replacementWorkflow.workflow_id, runId: null }
                : { projectId: target.id, chatId: null, workflowId: null, runId: null });
          }
          return reloadProjects();
        });
      },
    });
  }, [reloadProjects, setSelection]);

  const onMoveProject = useCallback(
    (projectId: string, direction: "up" | "down") => {
      void moveProject(projectId, direction).then(reloadProjects);
    },
    [reloadProjects],
  );

  const onMoveChat = useCallback(
    (projectId: string, chatId: string, direction: "up" | "down") => {
      void moveChat(projectId, chatId, direction).then(reloadProjects);
    },
    [reloadProjects],
  );

  const onReorderProjects = useCallback(
    (projectIds: string[]) => {
      void reorderProjects(projectIds).then(reloadProjects).catch(reloadProjects);
    },
    [reloadProjects],
  );

  const onReorderChats = useCallback(
    (projectId: string, chatIds: string[]) => {
      void reorderChats(projectId, chatIds).then(reloadProjects).catch(reloadProjects);
    },
    [reloadProjects],
  );

  const project = projects.find((p) => p.id === selection.projectId) ?? null;
  const chat = project?.chats.find((c) => c.id === selection.chatId) ?? null;
  const workflow = project?.workflows?.find((item) => item.workflow_id === selection.workflowId) ?? null;
  const workflowRun = project?.workflow_runs?.find((item) => item.run_id === selection.runId) ?? null;
  const ledgerIsOverlay = isMobile || isCompactDesktop;

  useEffect(() => {
    if (selection.workflowId) setLedgerOpen(false);
  }, [selection.workflowId]);

  useEffect(() => {
    if (bibliographyOpen) setLedgerOpen(false);
  }, [bibliographyOpen]);

  useEffect(() => {
    if (isCompactDesktop) setLedgerOpen(false);
  }, [isCompactDesktop]);

  useEffect(() => {
    if (isMobile) setSidebarOpen(false);
  }, [isMobile]);

  return (
    <div className="app app-shell-chat-first" data-shell={PRODUCTION_SHELL}>
      <div className={`appbar ${MAC_ELECTRON ? "appbar-native-mac" : ""}`}>
        <div className="appbar-main">
          <div className="appbar-context">
            <span className="title"><strong>[r]</strong> reasoning</span>
            {(project || bibliographyOpen || pricingOpen || collaborationOpen) && (
              <span className="appbar-breadcrumb">
                {collaborationOpen
                  ? "Cloud workspaces · shared collaboration"
                  : pricingOpen
                  ? "Prices · models"
                  : bibliographyOpen
                  ? bibliographyContext.scope === "global"
                    ? "Library · global"
                    : bibliographyContext.scope === "chat"
                      ? `Library · ${bibliographyContext.projectName}: ${bibliographyContext.chatName}`
                      : `Library · ${bibliographyContext.projectName ?? "global"}`
                  : `${project!.name}${chat ? `: ${chat.title}` : workflow ? `: ${workflow.name}${workflowRun ? " · saved run" : ""}` : ""}`}
              </span>
            )}
          </div>
          <button className="appbar-command" aria-label="Open quick navigation" onClick={() => setCommandOpen(true)}>
            <svg aria-hidden="true" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
              <circle cx="7" cy="7" r="4.25" />
              <path d="m10.2 10.2 3.3 3.3" />
            </svg>
            <span>Ask, open, or run anything…</span>
            <kbd>{MAC_PLATFORM ? "⌘P" : "Ctrl P"}</kbd>
          </button>
          <span className="appbar-balance" aria-hidden="true" />
        </div>
      </div>
      <div className={`columns ${ledgerIsOverlay ? "ledger-overlay" : ""} ${
        artifactSelection && isMobile ? "artifact-pane-overlay" : ""
      }`}>
        <ActivityBar
          explorerOpen={sidebarOpen}
          ledgerOpen={ledgerOpen}
          bibliographyOpen={bibliographyOpen}
          pricingOpen={pricingOpen}
          collaborationOpen={collaborationOpen}
          hasProject={Boolean(project)}
          onToggleExplorer={() => setSidebarOpen((open) => !open)}
          onOpenSearch={() => setCommandOpen(true)}
          onToggleLedger={() => {
            closeArtifact(() => {
              setBibliographyOpen(false);
              setPricingOpen(false);
              setCollaborationOpen(false);
              setLedgerOpen((open) => !open);
            });
          }}
          onOpenBibliography={openBibliography}
          onOpenPricing={openPricing}
          onOpenCollaboration={openCollaboration}
          onOpenSettings={() => setSettingsSection("appearance")}
        />
        {sidebarOpen && (
          <>
            {isMobile && (
              <div
                className="pane-backdrop"
                onClick={() => setSidebarOpen(false)}
              />
            )}
            <Sidebar
              projects={projects}
              loadState={explorerState}
              loadError={explorerError}
              onRetry={() => void reloadProjects().catch(() => undefined)}
              selectedProject={selection.projectId}
              selectedChat={selection.chatId}
              selectedWorkflow={selection.workflowId}
              selectedRun={selection.runId}
              onSelectChat={(projectId, chatId) => {
                setBibliographyOpen(false);
                setSelection({ projectId, chatId, workflowId: null, runId: null });
                if (isMobile) setSidebarOpen(false);
              }}
              onSelectWorkflow={(projectId, workflowId, runId = null) => {
                setBibliographyOpen(false);
                setSelection({ projectId, chatId: null, workflowId, runId });
                if (isMobile) setSidebarOpen(false);
              }}
              onNewProject={onNewProject}
              onNewChat={onNewChat}
              onNewWorkflow={onNewWorkflow}
              onPinProject={onPinProject}
              onRenameProject={onRenameProject}
              onArchiveProject={onArchiveProject}
              onDeleteProject={onDeleteProject}
              onRenameChat={onRenameChat}
              onPinChat={onPinChat}
              onArchiveChat={onArchiveChat}
              onDeleteChat={onDeleteChat}
              onRenameWorkflow={onRenameWorkflow}
              onPinWorkflow={onPinWorkflow}
              onArchiveWorkflow={onArchiveWorkflow}
              onDeleteWorkflow={onDeleteWorkflow}
              onMoveProject={onMoveProject}
              onMoveChat={onMoveChat}
              onReorderProjects={onReorderProjects}
              onReorderChats={onReorderChats}
            />
          </>
        )}
        {collaborationOpen ? (
          <AsyncSurface label="shared workspaces">
            <CollaborationWorkspace />
          </AsyncSurface>
        ) : pricingOpen ? (
          <AsyncSurface label="model prices">
            <PriceBrowser onManageKeys={() => setSettingsSection("agents")} />
          </AsyncSurface>
        ) : bibliographyOpen ? (
          <AsyncSurface label="Library">
            <Bibliography
              projectId={project?.id ?? null}
              chatId={chat?.id ?? null}
              projects={projects}
              onScopeChange={setBibliographyContext}
              onBrowseArtifacts={(projectId) => showArtifact("", projectId)}
              onDraftPrompt={(projectId, message) => draftInResearchChat(projectId, message, "Library research")}
              onUseInWorkflow={(projectId, item, initialPrompt) => setWorkflowDialog({ projectId, initialName: `Study · ${item.citation_key}`, initialPrompt })}
              onNavigateConnection={(connection: BibliographyConnection) => {
                const target = projects.find((item) => item.id === connection.project_id);
                const targetRun = target?.workflow_runs?.find((item) => item.run_id === connection.run_id);
                const workflowId = connection.workflow_id || targetRun?.workflow_id || null;
                const fallbackChat = target?.chats.find((item) => !item.archived) ?? null;
                setBibliographyOpen(false);
                if (connection.kind === "chat" && connection.chat_id) {
                  setSelection({ projectId: connection.project_id, chatId: connection.chat_id, workflowId: null, runId: null });
                } else if ((connection.kind === "workflow" || connection.kind === "run") && workflowId) {
                  setSelection({ projectId: connection.project_id, chatId: null, workflowId, runId: connection.kind === "run" ? connection.run_id ?? null : null });
                } else {
                  setSelection({ projectId: connection.project_id, chatId: fallbackChat?.id ?? null, workflowId: null, runId: null });
                }
                if (connection.kind === "claim" && connection.claim_id) {
                  setLedgerOpen(true);
                  window.setTimeout(() => window.dispatchEvent(new CustomEvent("ledger-open", { detail: connection.claim_id })), 0);
                } else if (connection.kind === "ledger_reference") {
                  setLedgerOpen(true);
                } else if (connection.kind === "artifact" && connection.artifact_path) {
                  showArtifact(connection.artifact_path, connection.project_id);
                }
              }}
            />
          </AsyncSurface>
        ) : project && workflow ? (
          <AsyncSurface label="workflow studio">
            <WorkflowStudio
              key={`${project.id}/${workflow.workflow_id}`}
              projectId={project.id}
              projectName={project.name}
              workflowId={workflow.workflow_id}
              workflowRevisionId={workflow.revision_id}
              runId={workflowRun?.run_id ?? null}
              onChanged={reloadProjects}
              onOpenRun={(runId) => {
                setSelection({ projectId: project.id, chatId: null, workflowId: workflow.workflow_id, runId });
                void reloadProjects();
              }}
              onDraftPrompt={(message) => {
                draftInResearchChat(project.id, message, "Workflow review");
              }}
              draftInitialPrompt={workflowDraft?.projectId === project.id && workflowDraft.workflowId === workflow.workflow_id ? workflowDraft.text : null}
              onDraftInitialPromptConsumed={() => setWorkflowDraft(null)}
            />
          </AsyncSurface>
        ) : project && chat ? (
          <Chat
            key={`${project.id}/${chat.id}`}
            projectId={project.id}
            chatId={chat.id}
            chat={chat}
            onLedgerActivity={bump}
            onChatStatusChange={(phase) => noteChatStatus(project.id, chat.id, phase)}
            onTurnCompleted={(workflows) => openAuthoredWorkflow(project.id, chat.id, workflows)}
            onOpenConnections={() => setSettingsSection("agents")}
            onHandoffCreated={(destinationChatId) => {
              void reloadProjects().finally(() => {
                setSelection({
                  projectId: project.id,
                  chatId: destinationChatId,
                  workflowId: null,
                  runId: null,
                });
              });
            }}
          />
        ) : (
          <div className="chat-col">
            <div className="chat-scroll chat-scroll-centered">
              <div className="chat-inner">
                <div className="onboarding-empty">
                  <strong>{projects.length === 0 && (explorerState === "loading" || explorerState === "cached") ? "Restoring your workspace" : explorerState === "error" && projects.length === 0 ? "Projects are temporarily unavailable" : projects.filter((candidate) => !candidate.archived).length === 0 ? "Start your first research project" : project ? "Start a conversation" : "Choose where to continue"}</strong>
                  <p>{projects.length === 0 && (explorerState === "loading" || explorerState === "cached") ? "Checking your projects and chats…" : explorerState === "error" && projects.length === 0 ? explorerError || "Reasoning could not refresh the Explorer." : projects.filter((candidate) => !candidate.archived).length === 0 ? "A project keeps conversations, workflows, evidence, claims, and artifacts together." : project ? "Create a chat or workflow inside this project." : "Select a recent project from the sidebar to continue."}</p>
                  <div>
                    {projects.length === 0 && (explorerState === "loading" || explorerState === "cached") ? null : explorerState === "error" && projects.length === 0 ? <button className="confirm" onClick={() => void reloadProjects().catch(() => undefined)}>try again</button> : projects.filter((candidate) => !candidate.archived).length === 0 ? <button className="confirm" onClick={onNewProject}>create a research project</button> : project ? <button className="confirm" onClick={() => onNewChat(project.id)}>new chat</button> : <button className="confirm" onClick={() => setSidebarOpen(true)}>browse projects</button>}
                    {project && <button onClick={() => onNewWorkflow(project.id)}>new workflow</button>}
                    <button onClick={() => setSettingsSection("agents")}>connect an agent</button>
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}
        {artifactSelection && (
          <AsyncSurface label="artifact reader">
            <ArtifactPane
              projectId={artifactSelection.projectId}
              name={artifactSelection.name}
              onClose={() => { void closeArtifact(); }}
              onDirtyChange={setArtifactDirty}
            />
          </AsyncSurface>
        )}

        {project && (
          <>
            {ledgerIsOverlay && ledgerOpen && (
              <div
                className="pane-backdrop"
                onClick={() => setLedgerOpen(false)}
              />
            )}
            {/* Kept mounted while hidden so its "ledger-open" listener
                still resolves slug clicks that also reveal the pane. */}
            <LedgerPanel
              key={project.id}
              projectId={project.id}
              refreshKey={refreshKey}
              onMissing={revalidate}
              onClose={() => setLedgerOpen(false)}
              hidden={!ledgerOpen}
            />
          </>
        )}
      </div>
      {commandOpen && (
        <CommandPalette
          projects={projects}
          currentProjectId={project?.id ?? null}
          onClose={() => setCommandOpen(false)}
          onSelectProject={(projectId) => {
            setBibliographyOpen(false);
            const target = projects.find((item) => item.id === projectId);
            const targetChat = target?.chats.find((item) => !item.archived);
            const targetWorkflow = target?.workflows?.[0];
            setSelection(targetChat
              ? { projectId, chatId: targetChat.id, workflowId: null, runId: null }
              : targetWorkflow
                ? { projectId, chatId: null, workflowId: targetWorkflow.workflow_id, runId: null }
                : { projectId, chatId: null, workflowId: null, runId: null });
          }}
          onSelectChat={(projectId, chatId) => { setBibliographyOpen(false); setSelection({ projectId, chatId, workflowId: null, runId: null }); }}
          onSelectWorkflow={(projectId, workflowId, runId = null) => { setBibliographyOpen(false); setSelection({ projectId, chatId: null, workflowId, runId }); }}
          onNewProject={onNewProject}
          onNewChat={onNewChat}
          onNewWorkflow={onNewWorkflow}
          onOpenSettings={() => setSettingsSection("appearance")}
          onOpenBibliography={openBibliography}
          onOpenPricing={openPricing}
        />
      )}
      {nameRequest && (
        <NameDialog
          {...nameRequest}
          onClose={() => setNameRequest(null)}
        />
      )}
      {newChatProjectId && (
        <NewChatDialog
          projectId={newChatProjectId}
          projectName={projects.find((item) => item.id === newChatProjectId)?.name ?? "this project"}
          onClose={() => setNewChatProjectId(null)}
          onManageWorkspaces={() => setSettingsSection("workspaces")}
          onCreated={(chatId) => {
            setSelection({ projectId: newChatProjectId, chatId, workflowId: null, runId: null });
            void reloadProjects();
          }}
        />
      )}
      {workflowDialog && (
        <NewWorkflowDialog
          projectName={projects.find((item) => item.id === workflowDialog.projectId)?.name ?? "this project"}
          initialTemplateId={workflowDialog.templateId}
          initialName={workflowDialog.initialName}
          onClose={() => setWorkflowDialog(null)}
          onCreate={async (name, templateId) => {
            const workflow = await createWorkflow(workflowDialog.projectId, name, templateId);
            if (workflowDialog.initialPrompt) {
              setWorkflowDraft({ projectId: workflowDialog.projectId, workflowId: workflow.workflow_id, text: workflowDialog.initialPrompt });
            }
            setBibliographyOpen(false);
            setSelection({ projectId: workflowDialog.projectId, chatId: null, workflowId: workflow.workflow_id, runId: null });
            await reloadProjects();
          }}
        />
      )}
      {settingsSection && <SettingsDialog initialSection={settingsSection} onClose={() => setSettingsSection(null)} onWorkspacesChanged={reloadProjects} />}
      {confirmRequest && <ConfirmationDialog request={confirmRequest} onClose={() => setConfirmRequest(null)} />}
    </div>
  );
}
