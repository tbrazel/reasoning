import { useState } from "react";

export interface ArtifactRevealSelection {
  projectId?: string;
  name: string;
}

interface ArtifactRevealRequest extends ArtifactRevealSelection {
  resolve: () => void;
  reject: (reason: unknown) => void;
}

export const ARTIFACT_REVEAL_EVENT = "artifact-reveal";

export function artifactRevealLabel(): string {
  return window.reasoningDesktop?.platform === "darwin"
    ? "Reveal in Finder"
    : "Show in folder";
}

/** Ask the workbench shell to reveal a validated project artifact.
 *
 * A caller with an explicit project can use the constrained Electron bridge
 * directly. Chat-rendered artifact references omit that plumbing and let App
 * resolve the currently selected project through a request event instead.
 */
export function revealArtifact(
  name: string,
  projectId?: string,
): Promise<void> {
  const bridge = window.reasoningDesktop;
  if (!bridge) return Promise.reject(new Error("Native file reveal is unavailable"));
  if (projectId) return bridge.revealArtifact(projectId, name);
  return new Promise((resolve, reject) => {
    window.dispatchEvent(new CustomEvent<ArtifactRevealRequest>(
      ARTIFACT_REVEAL_EVENT,
      { detail: { name, resolve, reject } },
    ));
  });
}

function RevealIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.25" strokeLinecap="round" strokeLinejoin="round">
      <path d="M1.75 4.25h4l1.3 1.5h7.2v7.5H1.75z" />
      <path d="M1.75 4.25v-1.5h4l1.3 1.5" />
      <path d="M8.75 9.5h3.5M10.75 7.5l2 2-2 2" />
    </svg>
  );
}

export function RevealArtifactButton({
  projectId,
  name,
  compact = false,
}: ArtifactRevealSelection & { compact?: boolean }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (typeof window === "undefined" || !window.reasoningDesktop) return null;
  const label = artifactRevealLabel();
  return (
    <button
      type="button"
      className={compact ? "artifact-reveal compact" : "icon-btn artifact-reveal"}
      aria-label={`${label}: artifacts/${name}`}
      title={error ?? `${label}: artifacts/${name}`}
      disabled={busy}
      onClick={(event) => {
        event.preventDefault();
        event.stopPropagation();
        setBusy(true);
        setError(null);
        void revealArtifact(name, projectId)
          .catch((reason: unknown) => {
            setError(reason instanceof Error ? reason.message : String(reason));
          })
          .finally(() => setBusy(false));
      }}
    >
      {compact ? <RevealIcon /> : busy ? "revealing…" : label}
    </button>
  );
}

export type { ArtifactRevealRequest };
