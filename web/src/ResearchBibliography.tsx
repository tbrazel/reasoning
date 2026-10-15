import React, { useEffect, useState } from "react";
import { ApiError, Hierarchy, aborted, errorMessage, organizationPath, request } from "./organization-api";
import type { ClaimSummary } from "./api";
import { ResearchMarkdown } from "./MathematicsBrowser";

type Reference = { reference_id: string; revision_id: string; author: string; title: string; year?: number;
  doi?: string; arxiv?: string; url?: string; notes?: string };
export function ResearchBibliography({ organizationId, hierarchy, canWrite = false, onAuthLost }: {
  organizationId: string; hierarchy?: Hierarchy; canWrite?: boolean; onAuthLost: () => void;
}) {
  const [references, setReferences] = useState<Reference[]>([]), [query, setQuery] = useState(""), [error, setError] = useState("");
  const [claims, setClaims] = useState<ClaimSummary[]>([]), [refresh, setRefresh] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    void request<{ references: Reference[] }>(organizationPath(organizationId) + "/mathematics", {
      method: "POST", body: { name: "ledger_references", arguments: {} }, signal: controller.signal,
    }).then((value) => { if (!controller.signal.aborted) setReferences(value.references); }).catch((cause) => {
      if (aborted(cause)) return;
      if (cause instanceof ApiError && cause.status === 401) onAuthLost(); else setError(errorMessage(cause));
    });
    void request<{ claims: Record<string, ClaimSummary> }>(organizationPath(organizationId) + "/mathematics", { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) setClaims(Object.values(value.claims ?? {})); })
      .catch(cause => { if (!aborted(cause)) setError(errorMessage(cause)); });
    return () => controller.abort();
  }, [organizationId, onAuthLost, refresh]);
  return <section className="org-mathematics" aria-label="Research bibliography"><header><h3>Research bibliography</h3>
    <small>Sources, exact statements, and their ledger links</small></header>
    <input type="search" aria-label="Search research bibliography" value={query} placeholder="Author, title, DOI, or arXiv…" onChange={(event) => setQuery(event.target.value)} />
    {error && <p role="alert" className="org-error">{error}</p>}
    {references.filter((reference) => JSON.stringify(reference).toLowerCase().includes(query.toLowerCase())).map((reference) => <details key={reference.reference_id}>
      <summary>{reference.author} · {reference.title}{reference.year ? ` (${reference.year})` : ""}</summary>
      {reference.notes && <ResearchMarkdown>{reference.notes}</ResearchMarkdown>}
      <details><summary>Technical identifiers</summary><small>{reference.reference_id} · {reference.revision_id}</small></details>
      <p>{reference.doi && <a href={`https://doi.org/${encodeURIComponent(reference.doi)}`} target="_blank" rel="noreferrer">DOI</a>}
        {reference.arxiv && <> · <a href={`https://arxiv.org/abs/${encodeURIComponent(reference.arxiv)}`} target="_blank" rel="noreferrer">arXiv</a></>}
        {reference.url && /^https:\/\//.test(reference.url) && <> · <a href={reference.url} target="_blank" rel="noreferrer">Source</a></>}</p>
      {claims.filter(claim => claim.reference_id === reference.reference_id).map(claim => <section key={claim.claim_id} className="org-source-statement">
        <strong>{claim.locator ?? "Source statement"}</strong><ResearchMarkdown>{claim.statement_text}</ResearchMarkdown>
        <a href={"/organization.html?" + new URLSearchParams({ organization: organizationId, view: "ledger", claim: claim.claim_id })}>Open in ledger</a>
      </section>)}
      {canWrite && <SourceStatement reference={reference} organizationId={organizationId} hierarchy={hierarchy} onSaved={() => setRefresh(value => value + 1)} />}
    </details>)}
    {!references.length && <p className="org-help">Research agents save references here as they work.</p>}
  </section>;
}

function SourceStatement({ reference, organizationId, hierarchy, onSaved }: {
  reference: Reference; organizationId: string; hierarchy?: Hierarchy; onSaved: () => void;
}) {
  const [statement, setStatement] = useState(""), [locator, setLocator] = useState(""), [kind, setKind] = useState("claim");
  const [project, setProject] = useState(""), [busy, setBusy] = useState(false), [error, setError] = useState("");
  const [clientId, setClientId] = useState(() => crypto.randomUUID());
  return <details><summary>Attach a statement</summary><form onSubmit={event => {
    event.preventDefault(); setBusy(true); setError("");
    void request(organizationPath(organizationId) + "/mathematics", { method: "POST", body: {
      name: "ledger_claim", client_id: clientId, project_id: project || null,
      arguments: { statement, kind, reference_id: reference.reference_id, reference_revision_id: reference.revision_id, locator },
    } }).then(() => { setStatement(""); setLocator(""); setClientId(crypto.randomUUID()); onSaved(); })
      .catch(cause => setError(errorMessage(cause))).finally(() => setBusy(false));
  }}><label>Locator<input required maxLength={1000} placeholder="Theorem 3.2, p. 17" value={locator} disabled={busy}
    onChange={event => { setLocator(event.target.value); setClientId(crypto.randomUUID()); }} /></label>
    <label>Statement<textarea required maxLength={32000} rows={3} value={statement} disabled={busy}
      onChange={event => { setStatement(event.target.value); setClientId(crypto.randomUUID()); }} /></label>
    <label>Type<select value={kind} disabled={busy} onChange={event => { setKind(event.target.value); setClientId(crypto.randomUUID()); }}>
      <option value="claim">Claim / theorem / lemma</option><option value="definition">Definition</option></select></label>
    <label>Scope<select value={project} disabled={busy} onChange={event => { setProject(event.target.value); setClientId(crypto.randomUUID()); }}>
      <option value="">Organization</option>{hierarchy?.projects.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
    <small>This records a source-attributed statement, not a checked proof. Its source revision is pinned.</small>
    {error && <p role="alert">{error}</p>}<button disabled={busy}>{busy ? "Saving…" : "Save and link to ledger"}</button>
  </form></details>;
}
