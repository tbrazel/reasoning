import { useEffect, useState } from "react";
import { Markdown } from "./Markdown";
import type { KnowledgeEntry } from "./organization-api";
import "./organization-ledger-workbench.css";

export type OrganizationLedgerBinding = {
  linked: true; server: string; organization_id: string; scope_kind: "organization" | "project" | "topic"; scope_id: string | null;
};

type Page = { entries: KnowledgeEntry[]; next_cursor: string | null };

export function OrganizationLedgerPanel({ projectId, binding, hidden, onClose }: {
  projectId: string; binding: OrganizationLedgerBinding; hidden?: boolean; onClose?: () => void;
}) {
  const [query, setQuery] = useState("");
  const [page, setPage] = useState<Page | null>(null);
  const [before, setBefore] = useState<string | null>(null);
  const [history, setHistory] = useState<(string | null)[]>([]);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setError(""); setPage(null);
    const params = new URLSearchParams({ q: query, limit: "50" });
    if (before) params.set("before", before);
    void fetch(`/api/projects/${encodeURIComponent(projectId)}/ledger/organization/entries?${params}`, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error("Shared ledger unavailable. Check its bot access and private configuration.");
        return response.json() as Promise<Page>;
      }).then((value) => { if (!controller.signal.aborted) setPage(value); })
      .catch((cause) => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "Shared ledger unavailable."); });
    return () => controller.abort();
  }, [projectId, query, before, refresh]);
  return <section id="ledger-panel" data-pane="ledger" className={`ledger-col shared-ledger-panel ${hidden ? "pane-hidden" : ""}`}>
    <header className="panel-header"><span className="name">Shared ledger</span><span className="shared-ledger-authority">Organization</span>
      <button type="button" onClick={() => setRefresh((value) => value + 1)} aria-label="Refresh shared ledger">↻</button>
      {onClose && <button type="button" onClick={onClose} aria-label="Close Ledger panel">×</button>}</header>
    <div className="shared-ledger-toolbar"><input type="search" aria-label="Search shared ledger" placeholder="Search mathematics, sources, record IDs…" value={query}
      onChange={(event) => { setQuery(event.target.value); setBefore(null); setHistory([]); }} />
      <a href={binding.server} target="_blank" rel="noreferrer">Open organization ↗</a></div>
    <p className="shared-ledger-notice">This project uses the organization&apos;s canonical research ledger. Publish through its website or the shared ledger tools.</p>
    {error && <p role="alert" className="shared-ledger-error">{error}</p>}
    {!page && !error && <p role="status">Loading shared ledger…</p>}
    {page && <div className="shared-ledger-entries">{!page.entries.length && <p>No matching entries.</p>}
      {page.entries.map((entry) => {
        const marker = "<!-- reasoning-ledger-import:v1 -->";
        const imported = entry.body.indexOf(marker);
        const body = imported >= 0 ? entry.body.slice(0, imported) : entry.body;
        return <details className="shared-ledger-entry" key={entry.id}>
          <summary><span className={`shared-ledger-state status-${entry.state}`}>{entry.state}</span><strong>{entry.title}</strong>
            <small>{entry.scope_kind} · v{entry.revision}</small></summary>
          <div className="shared-ledger-body"><Markdown text={body} />
            <footer>{entry.author_name} · {new Date(entry.revised_at * 1000).toLocaleString()} · {entry.id}</footer>
            {imported >= 0 && <details className="shared-ledger-provenance"><summary>Original record and provenance</summary>
              <Markdown text={entry.body.slice(imported + marker.length)} /></details>}</div>
        </details>;
      })}</div>}
    <nav className="shared-ledger-pages" aria-label="Shared ledger pages"><button disabled={!history.length} onClick={() => {
      setBefore(history[history.length - 1]); setHistory(history.slice(0, -1));
    }}>Newer</button><button disabled={!page?.next_cursor} onClick={() => { setHistory([...history, before]); setBefore(page?.next_cursor ?? null); }}>Older</button></nav>
  </section>;
}
