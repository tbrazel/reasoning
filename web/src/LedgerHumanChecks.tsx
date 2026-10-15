import { useEffect, useState } from "react";
import { errorMessage, request } from "./organization-api";
import "./ledger-human-checks.css";

type Check = { id: string; actor_id: string; actor_name: string; revision_id: string; created_at: number; current: boolean; note: string };

export function LedgerHumanChecks({ root, objectId, revisionId, canWrite }: {
  root: string; objectId: string; revisionId: string; canWrite: boolean;
}) {
  const [checks, setChecks] = useState<Check[]>([]), [error, setError] = useState("");
  const [busy, setBusy] = useState(false), [note, setNote] = useState("");
  const endpoint = root + "/" + encodeURIComponent(objectId) + "/human-checks";
  useEffect(() => {
    const controller = new AbortController(); setChecks([]); setError(""); setNote("");
    void request<Check[]>(endpoint, { signal: controller.signal }).then(value => {
      if (!controller.signal.aborted) setChecks(value);
    }).catch(cause => { if (!controller.signal.aborted) setError(errorMessage(cause)); });
    return () => controller.abort();
  }, [endpoint, revisionId]);
  return <section className="ledger-human-checks" aria-label="Human checks">
    <div className="ledger-human-check-list">{checks.map(check => <div key={check.id} className={check.current ? "" : "ledger-human-check-stale"}>
      <span>✓ Checked by {check.actor_name}</span> <time dateTime={new Date(check.created_at * 1000).toISOString()}>{new Date(check.created_at * 1000).toLocaleString()}</time>
      {!check.current && <small> · earlier revision</small>}{check.note && <p>{check.note}</p>}
    </div>)}</div>
    {canWrite && <form onSubmit={event => {
      event.preventDefault(); setBusy(true); setError("");
      void request<Check>(endpoint, { method: "POST", body: { revision_id: revisionId, note } }).then(check => {
        setChecks(current => current.some(item => item.id === check.id) ? current : [...current, check]);
      }).catch(cause => setError(errorMessage(cause))).finally(() => setBusy(false));
    }}><input aria-label="Human check note" placeholder="Optional check note" maxLength={2000} value={note} disabled={busy} onChange={event => setNote(event.target.value)} />
      <button disabled={busy}>{busy ? "Recording…" : "Checked by a human"}</button></form>}
    <small>Human judgment of this exact revision; not formal verification and not a change to mathematical status.</small>
    {error && <p role="alert">{error}</p>}
  </section>;
}
