// Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
import { useEffect, useRef, useState } from "react";
import { aborted, errorMessage, request } from "./organization-api";

type Status = { enabled: boolean; email_verified_at: number | null; delivery: { state: string; expires_at: number } | null };
export function EmailVerification({ onVerified }: { onVerified: () => void }) {
  const [status, setStatus] = useState<Status>(), [busy, setBusy] = useState(false), [error, setError] = useState("");
  const attempt = useRef<string | undefined>(undefined);
  useEffect(() => { const controller = new AbortController();
    void request<Status>("/auth/email-verification", { signal: controller.signal }).then(setStatus)
      .catch(cause => { if (!aborted(cause)) setError(errorMessage(cause)); });
    return () => controller.abort();
  }, []);
  return <section aria-label="Email verification">{status?.email_verified_at ? <p>Email verified.</p> : <>
    <p>{status?.enabled ? "Verify your email with a private, one-hour link sent to your inbox." : "Email verification requires the operator to configure Resend invitation mail."}</p>
    {status?.delivery && <small>Last email: {status.delivery.state}. A manually shared invitation does not verify inbox ownership.</small>}
    <button disabled={busy || !status?.enabled} onClick={() => {
      attempt.current ??= crypto.randomUUID(); setBusy(true); setError("");
      void request("/auth/email-verification", { method: "POST", body: { client_id: attempt.current } })
        .then(async () => { attempt.current = undefined; setStatus(await request<Status>("/auth/email-verification")); onVerified(); })
        .catch(cause => setError(errorMessage(cause))).finally(() => setBusy(false));
    }}>{busy ? "Requesting…" : status?.delivery ? "Send a new verification link" : "Verify email"}</button>
  </>}{error && <p role="alert">{error}</p>}</section>;
}

export function VerifyEmailBanner({ token, onDone }: { token: string; onDone: () => void }) {
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  return <div className="org-banner">Confirm ownership of your signed-in email address?
    <button disabled={busy} onClick={() => { setBusy(true); setError("");
      void request("/auth/email-verification/confirm", { method: "POST", body: { token } })
        .then(onDone).catch(cause => setError(errorMessage(cause))).finally(() => setBusy(false));
    }}>Verify email</button><button onClick={onDone}>Dismiss</button>{error && <span role="alert">{error}</span>}</div>;
}
