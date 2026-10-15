// Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
import React, { useEffect, useState } from "react";
import { aborted, errorMessage, organizationPath, request } from "./organization-api";

type Approval = { id: string; requester_name: string; topic_name: string; image_digest: string;
  parameters: { image_id: string; language: string; program: string; resources: Record<string, number> } };

export function BotComputeApprovals({ organizationId }: { organizationId: string }) {
  const [rows, setRows] = useState<Approval[]>([]), [error, setError] = useState("");
  const [busy, setBusy] = useState(false), [revision, setRevision] = useState(0);
  const path = organizationPath(organizationId) + "/my-bots/compute-approvals";
  useEffect(() => {
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try { const result = await request<Approval[]>(path, { signal: controller.signal });
        if (!controller.signal.aborted) { setRows(result); setError(""); }
      } catch (cause) { if (!aborted(cause)) setError(errorMessage(cause)); }
      if (!controller.signal.aborted) timer = setTimeout(poll, 2000);
    };
    void poll(); return () => { controller.abort(); clearTimeout(timer); };
  }, [path, revision]);
  const decide = async (id: string, allow: boolean) => {
    setBusy(true);
    try { await request(path + "/" + id, { method: "POST", body: { allow } }); setRevision(value => value + 1); }
    catch (cause) { setError(errorMessage(cause)); } finally { setBusy(false); }
  };
  return <section className="org-bot-approvals" aria-label="Container computation approvals">
    {error && <p className="org-error" role="alert">{error}</p>}
    {!!rows.length && <h3>Container computation requests</h3>}
    {rows.map(row => <article key={row.id}>
      <p>{row.requester_name} · {row.topic_name} · {row.parameters.image_id} · {row.parameters.language}</p>
      <pre><code>{row.parameters.program}</code></pre>
      <details><summary>Exact image and resource limits</summary><code>{row.image_digest}</code>
        <pre>{JSON.stringify(row.parameters.resources, null, 2)}</pre></details>
      <p className="org-help">Runs in the organization's isolated compute pool; provider charges may apply.</p>
      <button disabled={busy} onClick={() => void decide(row.id, true)}>Allow once</button>{" "}
      <button disabled={busy} onClick={() => void decide(row.id, false)}>Deny</button>
    </article>)}
  </section>;
}
