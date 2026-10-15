import {
  lazy,
  PointerEvent as ReactPointerEvent,
  Suspense,
  useEffect,
  useState,
} from "react";
import { RevealArtifactButton } from "./ArtifactActions";
import { Markdown, openArtifact } from "./Markdown";

const TEXT_EXT = /\.(md|markdown|txt|tex|log|json|jsonl|py|lean|toml|yaml|yml|csv|bib)$/i;
const MD_EXT = /\.(md|markdown)$/i;
const IMG_EXT = /\.(png|jpe?g|gif|svg|webp)$/i;
const PDF_EXT = /\.pdf$/i;
const PdfViewer = lazy(() =>
  import("./PdfViewer").then((module) => ({ default: module.PdfViewer })),
);

export const artifactUrl = (projectId: string, name: string): string =>
  `/api/projects/${projectId}/artifacts/` +
  name.split("/").map(encodeURIComponent).join("/");

/** Same-origin app route for a shareable artifact reader. Browser builds may
 * open it in another tab; desktop keeps the reader in the workbench pane. */
export const artifactViewerUrl = (projectId: string, name: string): string => {
  const params = new URLSearchParams({ p: projectId, artifact: name });
  return `${window.location.pathname}?${params.toString()}`;
};

const artifactDirectoryUrl = (projectId: string, name: string): string => {
  const params = new URLSearchParams({ path: name.replace(/\/+$/, "") });
  return `/api/projects/${encodeURIComponent(projectId)}/artifacts?${params.toString()}`;
};

type ArtifactDirectoryEntry = {
  name: string;
  path: string;
  kind: "directory" | "file";
  bytes: number | null;
};

type ArtifactDirectory = {
  path: string;
  entries: ArtifactDirectoryEntry[];
};

function formatBytes(bytes: number | null): string {
  if (bytes === null) return "directory";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function ArtifactDirectoryView({
  projectId,
  directory,
}: {
  projectId: string;
  directory: ArtifactDirectory;
}) {
  const current = directory.path.replace(/\/$/, "");
  const parent = current.includes("/")
    ? `${current.slice(0, current.lastIndexOf("/"))}/`
    : "";

  return (
    <div className="artifact-directory">
      <div className="artifact-directory-summary">
        {directory.entries.length} {directory.entries.length === 1 ? "item" : "items"}
      </div>
      <div className="artifact-directory-list" role="list">
        {current.includes("/") && (
          <div className="artifact-directory-row artifact-directory-parent" role="listitem">
            <a
              className="artifact-directory-link"
              href={artifactViewerUrl(projectId, parent)}
              onClick={(event) => {
                event.preventDefault();
                openArtifact(parent, projectId);
              }}
            >
              <span className="artifact-directory-name">..</span>
              <span className="artifact-directory-meta">parent directory</span>
            </a>
            <RevealArtifactButton projectId={projectId} name={parent} compact />
          </div>
        )}
        {directory.entries.map((entry) => (
          <div className="artifact-directory-row" role="listitem" key={entry.path}>
            <a
              className="artifact-directory-link"
              href={artifactViewerUrl(projectId, entry.path)}
              onClick={(event) => {
                event.preventDefault();
                openArtifact(entry.path, projectId);
              }}
            >
              <span className="artifact-directory-name">
                {entry.name}{entry.kind === "directory" ? "/" : ""}
              </span>
              <span className="artifact-directory-meta">{formatBytes(entry.bytes)}</span>
            </a>
            <RevealArtifactButton projectId={projectId} name={entry.path} compact />
          </div>
        ))}
      </div>
      {directory.entries.length === 0 && (
        <div className="empty">this artifact directory is empty</div>
      )}
    </div>
  );
}

function ArtifactImage({ url, name }: { url: string; name: string }) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    setLoading(true);
    setError(false);
  }, [url]);

  if (error) {
    return <div className="empty" role="alert">image could not be displayed</div>;
  }
  return (
    <div className="artifact-image-stage">
      {loading && <div className="empty artifact-media-status" role="status">loading image…</div>}
      <img
        className={`artifact-image ${loading ? "loading" : ""}`}
        src={url}
        alt={name}
        onLoad={() => setLoading(false)}
        onError={() => { setLoading(false); setError(true); }}
      />
    </div>
  );
}

/** Persistent workbench inspector for project artifacts. Directories list
 *  their contents, UTF-8 text can be rendered or edited, images and PDFs
 *  display inline, and anything else offers the raw file. */
export function ArtifactPane({
  projectId,
  name,
  onClose,
  onDirtyChange,
}: {
  projectId: string;
  name: string;
  onClose: () => void;
  onDirtyChange?: (dirty: boolean) => void;
}) {
  const desktopBridge = typeof window !== "undefined" ? window.reasoningDesktop : undefined;
  const url = artifactUrl(projectId, name);
  const isText = TEXT_EXT.test(name);
  const isImage = IMG_EXT.test(name);
  const isPdf = PDF_EXT.test(name);
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [directory, setDirectory] = useState<ArtifactDirectory | null>(null);
  const [directoryChecked, setDirectoryChecked] = useState(false);
  const [directoryError, setDirectoryError] = useState<string | null>(null);
  const [width, setWidth] = useState<number | null>(() => {
    const stored = Number(window.localStorage.getItem("reasoning-artifact-width"));
    return Number.isFinite(stored) && stored >= 320 ? stored : null;
  });
  const [resizing, setResizing] = useState(false);

  useEffect(() => {
    setDirectory(null);
    setDirectoryError(null);
    const leaf = name.replace(/\/+$/, "").split("/").pop() ?? "";
    const mightBeDirectory = name.endsWith("/") || !leaf.includes(".");
    if (!mightBeDirectory) {
      setDirectoryChecked(true);
      return;
    }

    setDirectoryChecked(false);
    let cancelled = false;
    fetch(artifactDirectoryUrl(projectId, name))
      .then(async (response) => {
        if (response.ok) return response.json() as Promise<ArtifactDirectory>;
        if (response.status === 404) return null;
        throw new Error(`HTTP ${response.status}`);
      })
      .then((listing) => {
        if (!cancelled) setDirectory(listing);
      })
      .catch((caught: unknown) => {
        if (!cancelled) {
          setDirectoryError(
            caught instanceof Error ? caught.message : "failed to list artifact directory",
          );
        }
      })
      .finally(() => {
        if (!cancelled) setDirectoryChecked(true);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, name]);

  useEffect(() => {
    setText(null);
    setError(null);
    setEditing(false);
    setDraft("");
    setSaving(false);
    setSaveError(null);
    if (!isText) return;
    let cancelled = false;
    fetch(url)
      .then(async (r) => {
        if (r.ok) return r.text();
        // Surface the server's own detail: "unknown artifact: x" for a
        // missing file vs a bare "Not Found" from a server too old to
        // have the artifacts endpoint at all.
        let detail = `HTTP ${r.status}`;
        try {
          const body = (await r.json()) as { detail?: unknown };
          if (body?.detail) detail = String(body.detail);
        } catch {
          /* keep the bare status */
        }
        throw new Error(detail);
      })
      .then((body) => {
        if (!cancelled) {
          setText(body);
          setDraft(body);
          setEditing(true);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled)
          setError(err instanceof Error ? err.message : "failed to load artifact");
      });
    return () => {
      cancelled = true;
    };
  }, [url, isText]);

  useEffect(() => {
    onDirtyChange?.(Boolean(isText && text !== null && draft !== text));
  }, [draft, isText, onDirtyChange, text]);

  const beginResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    event.preventDefault();
    const panel = event.currentTarget.parentElement;
    if (!panel) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    const startX = event.clientX;
    const startWidth = panel.getBoundingClientRect().width;
    setResizing(true);
    const clamp = (candidate: number) => Math.min(
      Math.max(320, window.innerWidth * 0.68),
      Math.max(320, candidate),
    );
    const move = (next: PointerEvent) => {
      setWidth(clamp(startWidth + startX - next.clientX));
    };
    const finish = (next: PointerEvent) => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", finish);
      setResizing(false);
      const nextWidth = clamp(startWidth + startX - next.clientX);
      setWidth(nextWidth);
      window.localStorage.setItem("reasoning-artifact-width", String(Math.round(nextWidth)));
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", finish, { once: true });
  };
  const resizeWithKeyboard = (direction: number) => {
    const panel = document.getElementById("artifact-panel");
    const current = width ?? panel?.getBoundingClientRect().width ?? 560;
    const maximum = Math.max(320, window.innerWidth * 0.68);
    const next = Math.min(maximum, Math.max(320, current + direction * 24));
    setWidth(next);
    window.localStorage.setItem("reasoning-artifact-width", String(Math.round(next)));
  };

  const save = async () => {
    setSaving(true);
    setSaveError(null);
    try {
      const response = await fetch(url, {
        method: "PUT",
        headers: { "Content-Type": "text/plain; charset=utf-8" },
        body: draft,
      });
      if (!response.ok) {
        let detail = `HTTP ${response.status}`;
        try {
          const body = (await response.json()) as { detail?: unknown };
          if (body?.detail) detail = String(body.detail);
        } catch {
          /* retain the HTTP status */
        }
        throw new Error(detail);
      }
      setText(draft);
    } catch (caught: unknown) {
      setSaveError(caught instanceof Error ? caught.message : "failed to save artifact");
    } finally {
      setSaving(false);
    }
  };

  return (
    <aside
      id="artifact-panel"
      className={`artifact-pane ${isImage || isPdf ? "artifact-pane-media" : ""} ${
        resizing ? "artifact-pane-resizing" : ""
      }`}
      style={width === null ? undefined : { width }}
      aria-label="Artifact viewer"
    >
      <div
        className="artifact-resizer"
        role="separator"
        aria-label="Resize artifact pane"
        aria-orientation="vertical"
        tabIndex={0}
        onPointerDown={beginResize}
        onKeyDown={(event) => {
          if (event.key === "ArrowLeft") { event.preventDefault(); resizeWithKeyboard(1); }
          if (event.key === "ArrowRight") { event.preventDefault(); resizeWithKeyboard(-1); }
          if (event.key === "Home") {
            event.preventDefault();
            setWidth(null);
            window.localStorage.removeItem("reasoning-artifact-width");
          }
        }}
        onDoubleClick={() => {
          setWidth(null);
          window.localStorage.removeItem("reasoning-artifact-width");
        }}
      />
        <div className="artifact-head">
          <span className="artifact-name" title={name}>
            artifacts/{name}
          </span>
          <span className="spacer" />
          {isText && text !== null && !error && !editing && (
            <button
              className="icon-btn"
              onClick={() => {
                setSaveError(null);
                setEditing(true);
              }}
            >
              edit
            </button>
          )}
          {editing && (
            <>
              <button className="icon-btn" disabled={saving || draft === text} onClick={() => void save()}>
                {saving ? "saving…" : "save"}
              </button>
              <button
                className="icon-btn"
                disabled={saving}
                onClick={() => setEditing(false)}
              >
                preview
              </button>
              <button
                className="icon-btn"
                disabled={saving || draft === text}
                onClick={() => {
                  setDraft(text ?? "");
                  setSaveError(null);
                }}
              >
                revert
              </button>
            </>
          )}
          {desktopBridge ? (
            <RevealArtifactButton projectId={projectId} name={name} />
          ) : (
            <a
              className="icon-btn"
              href={artifactViewerUrl(projectId, name)}
              target="_blank"
              rel="noreferrer"
            >
              open in window
            </a>
          )}
          <button className="icon-btn" aria-label="Close artifact" onClick={onClose}>
            ✕
          </button>
        </div>
        <div className={`artifact-body ${isImage || isPdf ? "artifact-body-media" : ""}`}>
          {directory ? (
            <ArtifactDirectoryView projectId={projectId} directory={directory} />
          ) : directoryError ? (
            <div className="empty">{directoryError}</div>
          ) : !directoryChecked ? (
            <div className="empty">loading artifact directory…</div>
          ) : isImage ? (
            <ArtifactImage url={url} name={name} />
          ) : isPdf ? (
            <Suspense fallback={<div className="empty" role="status">loading PDF reader…</div>}>
              <PdfViewer url={url} name={name} />
            </Suspense>
          ) : !isText ? (
            <div className="empty">
              no inline view for this file —{" "}
              <a href={url} target="_blank" rel="noreferrer">
                open raw
              </a>
            </div>
          ) : editing ? (
            <div className="artifact-editor">
              <textarea
                aria-label={`Edit ${name}`}
                spellCheck={false}
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
              />
              {saveError && <div className="artifact-editor-error" role="alert">{saveError}</div>}
            </div>
          ) : error ? (
            <div className="empty">{error}</div>
          ) : text === null ? (
            <div className="empty">loading…</div>
          ) : MD_EXT.test(name) ? (
            <Markdown text={draft} />
          ) : (
            <pre className="artifact-pre">{draft}</pre>
          )}
        </div>
    </aside>
  );
}
