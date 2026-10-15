import { useCallback, useEffect, useRef, useState } from "react";
import {
  desktopAuth,
  desktopAuthAction,
  desktopCloudReadiness,
  desktopCloudDemo,
  desktopCloudRunEvents,
  desktopCloudArtifacts,
  desktopCloudArtifact,
  desktopCloudRun,
  desktopCloudRuns,
  desktopCloudSessionAction,
  type DesktopAuthPresentation,
  type DesktopCloudReadiness,
} from "./desktopCloudApi";

const terminalRunStates = new Set(["succeeded", "failed", "cancelled"]);

function messageFrom(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason);
}

function actionFailure(reason: unknown): string {
  const message = messageFrom(reason);
  if (message.includes("Cognito token endpoint is unavailable")) {
    return "Reasoning could not refresh your Cloud sign-in. This is usually temporary; wait a moment and try again. If it continues, sign out and sign in again.";
  }
  return message;
}

function refreshFailure(reason: unknown): string {
  const message = messageFrom(reason);
  if (message.includes("Cognito token endpoint is unavailable")) {
    return "Cloud status could not refresh because the sign-in service is temporarily unavailable. Any submitted run keeps running; Reasoning will retry automatically.";
  }
  return `Cloud status could not refresh (${message}). Reasoning will retry automatically.`;
}

function callbackFailure(code: string): string {
  if (code === "post_auth_boundary") {
    return "Sign-in reached Reasoning, but the local Cloud security boundary could not initialize.";
  }
  if (code === "desktop_cloud_state") {
    return "Sign-in reached Reasoning, but the Desktop could not safely reconcile its local Cloud state.";
  }
  if (code === "credential_store") {
    return "Sign-in reached Reasoning, but macOS Keychain could not securely save the session.";
  }
  return `Sign-in could not be completed (${code}).`;
}

export function DesktopCloudSettings() {
  const [auth, setAuth] = useState<DesktopAuthPresentation | null>(null);
  const [cloud, setCloud] = useState<DesktopCloudReadiness | null>(null);
  const [runs, setRuns] = useState<Record<string, unknown>[]>([]);
  const [artifacts, setArtifacts] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [pendingAction, setPendingAction] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [warning, setWarning] = useState("");
  const refreshing = useRef(false);
  const actionInFlight = useRef(false);
  const observedTerminalEvents = useRef(new Set<string>());

  const refresh = useCallback(async () => {
    if (refreshing.current) return;
    refreshing.current = true;
    try {
      const [nextAuth, nextCloud] = await Promise.all([desktopAuth(), desktopCloudReadiness()]);
      setAuth(nextAuth);
      setCloud(nextCloud);
      if (nextCloud.status === "connected") {
        const { operation_ids: ids } = await desktopCloudRuns();
        setRuns(await Promise.all(ids.slice(0, 20).map(async (id) => {
          const run = await desktopCloudRun(id);
          const state = typeof run.state === "string" ? run.state : "";
          if (terminalRunStates.has(state) && !observedTerminalEvents.current.has(id)) {
            await desktopCloudRunEvents(id);
            observedTerminalEvents.current.add(id);
          }
          return run;
        })));
        setArtifacts((await desktopCloudArtifacts()).artifact_ids);
      } else {
        setRuns([]);
        setArtifacts([]);
      }
      setWarning("");
    } finally {
      refreshing.current = false;
    }
  }, []);

  useEffect(() => {
    let active = true;
    const poll = () => {
      if (actionInFlight.current) return;
      void refresh().catch((reason) => { if (active) setWarning(refreshFailure(reason)); });
    };
    poll();
    const interval = window.setInterval(poll, 5000);
    return () => { active = false; window.clearInterval(interval); };
  }, [refresh]);

  useEffect(() => {
    if (auth?.failure_code) setError(callbackFailure(auth.failure_code));
  }, [auth?.failure_code]);

  const act = <T,>(
    label: string,
    operation: () => Promise<T>,
    success: (value: T) => string,
    refreshAfter = true,
  ) => {
    setBusy(true);
    actionInFlight.current = true;
    setPendingAction(label);
    setNotice("");
    setError("");
    setWarning("");
    void operation()
      .then((value) => {
        setNotice(success(value));
        if (refreshAfter) {
          void refresh().catch((reason) => setWarning(refreshFailure(reason)));
        }
      })
      .catch((reason) => setError(actionFailure(reason)))
      .finally(() => {
        setBusy(false);
        setPendingAction("");
        actionInFlight.current = false;
      });
  };

  const download = (artifactId: string) => {
    setBusy(true);
    actionInFlight.current = true;
    setPendingAction("Verifying and downloading artifact");
    setNotice("");
    setError("");
    setWarning("");
    void desktopCloudArtifact(artifactId)
      .then((verified) => {
        const raw = window.atob(verified.content_base64);
        const buffer = new ArrayBuffer(raw.length);
        const bytes = new Uint8Array(buffer);
        for (let index = 0; index < raw.length; index += 1) bytes[index] = raw.charCodeAt(index);
        const url = URL.createObjectURL(new Blob([buffer], { type: verified.metadata.media_type }));
        const link = document.createElement("a");
        link.href = url;
        link.download = `reasoning-${artifactId}.json`;
        link.click();
        window.setTimeout(() => URL.revokeObjectURL(url), 1000);
        setNotice("Verified artifact downloaded.");
      })
      .catch((reason) => setError(actionFailure(reason)))
      .finally(() => {
        setBusy(false);
        setPendingAction("");
        actionInFlight.current = false;
      });
  };

  return (
    <section className="desktop-cloud-settings" role="tabpanel">
      <div className="settings-section-heading">
        <div>
          <strong>Private Cloud</strong>
          <span>Sign in through your system browser. Cloud stays disconnected until you choose Connect.</span>
        </div>
      </div>
      <p className="settings-note">Your refresh session stays in the operating-system credential store. Local research and files are not uploaded by signing in or connecting.</p>
      <div className="desktop-cloud-status" role="status">
        <strong>{auth?.state === "authenticated" ? "Signed in" : auth?.state === "awaiting_callback" ? "Waiting for browser sign-in" : "Signed out"}</strong>
        <span>{cloud?.status === "connected" ? "Cloud connected" : "Cloud disconnected"}</span>
        {auth?.identity && <small>Account {auth.identity.account_id} · user {auth.identity.actor.id}</small>}
      </div>
      {auth?.state === "awaiting_callback" && <p className="settings-note">
        Complete sign-in in your browser. After 2FA, verification normally takes a few seconds.
      </p>}
      <div className="desktop-cloud-actions" aria-busy={busy}>
        {auth?.state === "signed_out" && <>
          <button disabled={busy} onClick={() => act("Opening browser sign-in", () => desktopAuthAction("sign-in"), () => "Finish sign-in in your browser.")}>Sign in</button>
          <button disabled={busy} onClick={() => act("Resuming saved sign-in", () => desktopAuthAction("resume"), () => "Saved sign-in resumed.")}>Resume saved sign-in</button>
        </>}
        {auth?.state === "authenticated" && <>
          {cloud?.status === "disconnected"
            ? <button disabled={busy} onClick={() => act("Connecting Cloud", () => desktopCloudSessionAction("connect"), () => "Cloud connected.")}>Connect Cloud</button>
            : <button disabled={busy} onClick={() => act("Disconnecting Cloud", () => desktopCloudSessionAction("disconnect"), () => "Cloud disconnected. Hosted runs continue in the Cloud.")}>Disconnect Cloud</button>}
          {cloud?.status === "connected" && <button disabled={busy} onClick={() => act("Submitting synthetic Cloud check", desktopCloudDemo, (run) => {
            const state = typeof run.state === "string" ? run.state : "submitted";
            return `Synthetic check ${state}. Watch its state under Tracked hosted runs; completion requires a verified artifact.`;
          })}>
            Run synthetic Cloud check
          </button>}
          <button disabled={busy} onClick={() => act("Signing out", () => desktopAuthAction("logout"), () => "Signed out.")}>Sign out</button>
        </>}
        <button disabled={busy} onClick={() => act("Refreshing Cloud status", refresh, () => "Cloud status refreshed.", false)}>Refresh</button>
      </div>
      {busy && pendingAction && <p className="desktop-cloud-progress" role="status">
        <strong>{pendingAction}…</strong>
        <span>{pendingAction === "Connecting Cloud"
          ? "Verifying your account and Cloud capabilities. This normally takes a few seconds."
          : pendingAction === "Submitting synthetic Cloud check"
            ? "Submitting the hosted run. Its result normally appears within 5–15 seconds and continues if this window closes."
            : "Please keep Reasoning open while this step finishes."}</span>
      </p>}
      {notice && <p className="desktop-cloud-notice" role="status">{notice}</p>}
      {warning && <p className="desktop-cloud-warning" role="status">{warning}</p>}
      {error && <p className="desktop-cloud-error" role="alert">{error}</p>}
      {cloud?.status === "connected" && <p className="settings-note">
        The synthetic check uploads one fixed 68-byte text file, runs only fake inference and fake compute, and has a zero-dollar quote. No personal project files are read.
      </p>}
      {cloud?.status === "connected" && <div className="desktop-cloud-runs">
        <strong>Tracked hosted runs</strong>
        {runs.length === 0 ? <p>No hosted runs tracked on this Desktop yet.</p> : (
          <ul>{runs.map((run) => <li key={String(run.operation_id)}>
            <code>{String(run.operation_id)}</code> · {String(run.state)}
          </li>)}</ul>
        )}
      </div>}
      {cloud?.status === "connected" && artifacts.length > 0 && <div className="desktop-cloud-runs">
        <strong>Verified artifacts</strong>
        <ul>{artifacts.map((id) => <li key={id}>
          <code>{id}</code> <button disabled={busy} onClick={() => download(id)}>Download verified bytes</button>
        </li>)}</ul>
      </div>}
    </section>
  );
}
