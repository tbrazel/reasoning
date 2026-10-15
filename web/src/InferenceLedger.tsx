import React, { useEffect, useState } from "react";
import { ApiError, InferenceCall, InferenceCallPage, aborted, decimalBudget, errorMessage, organizationPath, request, usd } from "./organization-api";
import "./inference-ledger.css";
import { estimateLabel } from "./usageFormat";

export function InferenceLedger({ organizationId, canManage, onAuthLost }: {
  organizationId: string; canManage: boolean; onAuthLost: () => void;
}) {
  const [page, setPage] = useState<InferenceCallPage | null>(null);
  const [selected, setSelected] = useState<InferenceCall | null>(null);
  const [model, setModel] = useState(""); const [actor, setActor] = useState(""); const [topic, setTopic] = useState("");
  const [project, setProject] = useState(""); const [status, setStatus] = useState(""); const [purpose, setPurpose] = useState("");
  const [since, setSince] = useState(""); const [until, setUntil] = useState("");
  const [before, setBefore] = useState<number | null>(null); const [history, setHistory] = useState<(number | null)[]>([]);
  const [refresh, setRefresh] = useState(0); const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  const [cost, setCost] = useState(""); const [checked, setChecked] = useState(false);
  const root = organizationPath(organizationId) + "/inference-ledger";
  const params = new URLSearchParams();
  for (const [name, value] of [["model", model], ["actor", actor], ["topic_id", topic], ["project_id", project], ["status", status], ["purpose", purpose]]) {
    if (value.trim()) params.set(name, value.trim());
  }
  if (since) params.set("since", String(Math.floor(new Date(since).getTime() / 1000)));
  if (until) params.set("until", String(Math.floor(new Date(until).getTime() / 1000)));
  const filters = params.toString();
  const fail = (cause: unknown) => { if (cause instanceof ApiError && cause.status === 401) onAuthLost(); else setError(errorMessage(cause)); };
  useEffect(() => { setBefore(null); setHistory([]); }, [filters]);
  useEffect(() => {
    const controller = new AbortController(); setError("");
    const query = new URLSearchParams(filters); if (before !== null) query.set("before", String(before));
    void request<InferenceCallPage>(root + "?" + query, { signal: controller.signal }).then((value) => {
      if (!controller.signal.aborted) setPage(value);
    }).catch((cause) => { if (!aborted(cause)) fail(cause); });
    return () => controller.abort();
  }, [root, filters, before, refresh, onAuthLost]);
  const inspect = async (id: string) => {
    setError(""); setCost(""); setChecked(false);
    try { setSelected(await request<InferenceCall>(root + "/" + encodeURIComponent(id))); } catch (cause) { fail(cause); }
  };
  const exportAll = async () => {
    setBusy(true); setError("");
    try {
      const records: InferenceCall[] = []; let cursor: number | null = null;
      do {
        const query = new URLSearchParams(filters); query.set("limit", "200"); if (cursor !== null) query.set("before", String(cursor));
        const result = await request<InferenceCallPage>(root + "?" + query);
        records.push(...result.calls); cursor = result.next_before;
      } while (cursor !== null);
      const url = URL.createObjectURL(new Blob([records.map((record) => JSON.stringify(record)).join("\n") + "\n"], { type: "application/x-ndjson" }));
      const link = document.createElement("a"); link.href = url; link.download = "reasoning-inference-ledger.jsonl"; link.click(); URL.revokeObjectURL(url);
    } catch (cause) { fail(cause); } finally { setBusy(false); }
  };
  return <section className="org-inference-ledger" aria-label="Inference ledger">
    <header><div><h3>Inference ledger</h3><p>Every model call, including continuations and compaction. Price quotes and receipts are preserved.</p></div>
      <button disabled={busy} onClick={() => void exportAll()}>{busy ? "Exporting…" : "Export matching calls"}</button>
      <button onClick={() => setRefresh((value) => value + 1)}>Refresh</button></header>
    <div className="org-ledger-filters">
      <label>Model<input type="search" placeholder="Exact model ID" value={model} onChange={(event) => setModel(event.target.value)} /></label>
      <label>Status<select value={status} onChange={(event) => setStatus(event.target.value)}><option value="">All statuses</option>
        {["running", "succeeded", "failed", "cancelled", "interrupted"].map((value) => <option key={value}>{value}</option>)}</select></label>
      <label>Purpose<select value={purpose} onChange={(event) => setPurpose(event.target.value)}><option value="">All purposes</option>
        {["answer", "continuation", "compaction", "workflow"].map((value) => <option key={value}>{value}</option>)}</select></label>
      <label>Since<input type="datetime-local" value={since} onChange={(event) => setSince(event.target.value)} /></label>
      <label>Until<input type="datetime-local" value={until} onChange={(event) => setUntil(event.target.value)} /></label>
    </div>
    <details><summary>Project, topic, and actor filters</summary><div className="org-ledger-filters">
      <label>Project ID<input value={project} onChange={(event) => setProject(event.target.value)} /></label>
      <label>Topic ID<input value={topic} onChange={(event) => setTopic(event.target.value)} /></label>
      <label>Actor ID<input value={actor} onChange={(event) => setActor(event.target.value)} /></label></div></details>
    {error && <p role="alert" className="org-error">{error}</p>}
    {!page && <p role="status">Loading provider receipts…</p>}
    <div className="org-ledger-table"><table><thead><tr><th>Call</th><th>Project / topic</th><th>Input</th><th>Output</th><th>Actual charge</th><th>Budget held</th><th>≈ Wh</th><th>≈ kg CO₂e</th></tr></thead>
      <tbody>{page?.calls.map((call) => <tr key={call.id}><td><button onClick={() => void inspect(call.id)}>{call.model}</button>
        <small>{new Date(call.started_at * 1000).toLocaleString()} · {call.purpose} · {call.status}</small></td>
        <td>{call.project_name} / {call.topic_name}<small>{call.actor_name}</small></td>
        <td>{call.prompt_tokens?.toLocaleString() ?? "—"}</td><td>{call.completion_tokens?.toLocaleString() ?? "—"}</td>
        <td>{call.actual_microusd == null ? call.accounting_state === "released" ? "No charge confirmed" : "Unknown" : usd(call.actual_microusd)}</td>
        <td>{["reserved", "uncertain"].includes(call.accounting_state) ? usd(call.reserved_microusd) : "—"}</td>
        <td title={call.usage?.estimate_kind === "reference" ? "Generic 1 J/token reference scenario, not a model measurement" : "Configured energy scenario"}>
          {estimateLabel(call.usage?.estimated_kwh == null ? null : call.usage.estimated_kwh * 1000)}{call.usage?.estimated_kwh != null && call.usage.estimate_kind === "reference" ? "*" : ""}</td>
        <td>{estimateLabel(call.usage?.estimated_kg_co2e)}</td></tr>)}</tbody></table></div>
    {page && !page.calls.length && <p>No matching provider calls.</p>}
    <small className="org-help">* Reference energy scenario: 1 J per processed token, not a model measurement. Blank carbon requires an inference-location/grid assumption.</small>
    <nav className="org-ledger-pagination"><button disabled={!history.length} onClick={() => { setBefore(history[history.length - 1]); setHistory(history.slice(0, -1)); }}>Newer</button>
      <button disabled={page?.next_before == null} onClick={() => { setHistory([...history, before]); setBefore(page?.next_before ?? null); }}>Older</button></nav>
    {selected && <section className="org-call-detail" aria-label="Provider call details"><header><h4>{selected.model}</h4><button onClick={() => setSelected(null)}>Close details</button></header>
      <p>{selected.purpose} · {selected.status} · {selected.accounting_state} · {selected.provider_generation_id ?? "Generation ID unavailable"}</p>
      <p>{selected.diagnostics?.message} {selected.diagnostics?.action}</p>
      <dl><dt>Call ID</dt><dd>{selected.id}</dd><dt>Run</dt><dd>{selected.run_id}</dd>
        <dt>Parent call</dt><dd>{selected.parent_call_id ? <button onClick={() => void inspect(selected.parent_call_id!)}>{selected.parent_call_id}</button> : "First call"}</dd>
        <dt>Harness / prompt</dt><dd>{selected.harness_version} / {selected.prompt_version}</dd>
        <dt>Response headers</dt><dd title="Includes network and upstream waiting, not a provider-only queue measurement">
          {selected.time_to_response_headers_ms == null ? "Not recorded" : `${(selected.time_to_response_headers_ms / 1000).toFixed(2)} seconds`}</dd>
        <dt>First token</dt><dd>{selected.time_to_first_token_ms == null ? "Not reported" : `${(selected.time_to_first_token_ms / 1000).toFixed(2)} seconds`}</dd>
        <dt>Total provider time</dt><dd>{selected.latency_ms == null ? "Not reported historically" : `${(selected.latency_ms / 1000).toFixed(2)} seconds`}</dd>
        <dt>Reported tokens</dt><dd>{selected.prompt_tokens ?? "?"} input; {selected.cached_tokens ?? "?"} cached; {selected.completion_tokens ?? "?"} output;
          {selected.reasoning_tokens ?? "?"} reasoning; {selected.tool_tokens ?? "?"} tool; {selected.total_tokens ?? "?"} total</dd>
        <dt>Context fingerprint</dt><dd>{selected.context_sha256 ?? "Not recorded historically"}</dd></dl>
      <details><summary>Captured prices and routing</summary><pre>{JSON.stringify({ quote: selected.quote, routing: selected.route }, null, 2)}</pre></details>
      <details><summary>Immutable receipts ({selected.receipts?.length ?? 0})</summary><pre>{JSON.stringify(selected.receipts, null, 2)}</pre></details>
      {canManage && ["uncertain", "reserved"].includes(selected.accounting_state) && <form onSubmit={(event) => {
        event.preventDefault(); setBusy(true); void request(root + "/" + selected.id + "/cost", { method: "PUT", body: { actual_microusd: decimalBudget(cost) } })
          .then(() => { setRefresh((value) => value + 1); return inspect(selected.id); }).catch(fail).finally(() => setBusy(false));
      }}><label>Verified provider cost (USD)<input required inputMode="decimal" value={cost} onChange={(event) => setCost(event.target.value)} /></label>
        <label><input type="checkbox" required checked={checked} onChange={(event) => setChecked(event.target.checked)} />I checked this call in the provider activity record.</label>
        <button disabled={busy || !checked}>Record verified cost</button></form>}
    </section>}
  </section>;
}
