// Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
import { useEffect, useState } from "react";
import { aborted, decimalBudget, errorMessage, organizationPath, request, usd } from "./organization-api";

type Run = { id: string; model: string; status: string; accounting_state: string; reserved_microusd: number; provider_generation_id: string | null };
type Snapshot = { inference: Run[]; bots: { id: string; status: string }[]; compute: { id: string; status: string }[]; truncated: boolean };
export function RetainedWork({ organizationId }: { organizationId: string }) {
  const [snapshot, setSnapshot] = useState<Snapshot>(), [refresh, setRefresh] = useState(0), [error, setError] = useState("");
  const root = organizationPath(organizationId);
  useEffect(() => { const controller = new AbortController();
    void request<Snapshot>(root + "/retained-work", { signal: controller.signal }).then(setSnapshot)
      .catch(cause => { if (!aborted(cause)) setError(errorMessage(cause)); });
    return () => controller.abort();
  }, [root, refresh]);
  return <details><summary>Retained work from deleted conversations</summary>
    <p>Deleting conversations does not erase provider charges or stop external work instantly. Only reconcile after checking provider activity; no zero-cost assumption is made.</p>
    <button onClick={() => setRefresh(value => value + 1)}>Refresh retained receipts</button>
    {error && <p role="alert">{error}</p>}
    {snapshot?.inference.map(run => <RetainedCharge key={run.id} root={root} run={run} onDone={() => setRefresh(value => value + 1)} />)}
    {snapshot && [...snapshot.bots.map(row => ({ ...row, kind: "Bot" })), ...snapshot.compute.map(row => ({ ...row, kind: "Compute" }))]
      .map(row => <p key={row.id}>{row.kind} · <code>{row.id}</code> · {row.status}. Retain the runner/worker receipt and confirm external cleanup.</p>)}
    {snapshot && !snapshot.inference.length && !snapshot.bots.length && !snapshot.compute.length && <p>No unresolved work from deleted conversations.</p>}
    {snapshot?.truncated && <p>Showing the newest 100 receipts per category.</p>}
  </details>;
}
function RetainedCharge({ root, run, onDone }: { root: string; run: Run; onDone: () => void }) {
  const [cost, setCost] = useState(""), [verified, setVerified] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState("");
  return <section><p>{run.model} · {run.status} · held {usd(run.reserved_microusd)} (not a confirmed charge) · <code>{run.id}</code>
    {run.provider_generation_id && <> · provider {run.provider_generation_id}</>}</p>
    {run.accounting_state === "uncertain" && <form onSubmit={event => { event.preventDefault(); if (!verified) return;
      try { const actual_microusd = decimalBudget(cost); setBusy(true); setError("");
        void request(root + "/inference-runs/" + run.id + "/cost", { method: "PUT", body: { actual_microusd } })
          .then(onDone).catch(cause => setError(errorMessage(cause))).finally(() => setBusy(false));
      } catch (cause) { setError(errorMessage(cause)); }
    }}><label>Verified actual USD<input required type="number" min="0" step="0.000001" value={cost} onChange={event => setCost(event.target.value)} /></label>
      <label><input type="checkbox" checked={verified} onChange={event => setVerified(event.target.checked)} />Checked against provider activity</label>
      <button disabled={busy || !verified}>Reconcile retained charge</button>{error && <p role="alert">{error}</p>}</form>}
  </section>;
}
