// Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
import React, { useEffect, useState } from "react";
import { aborted, errorMessage, organizationPath, request } from "./organization-api";

type Approval = { id: string; run_id: string; bot_id: string; requested_by: string; topic_id: string;
  requester_name: string; topic_name: string;
  command_json: string; command_sha256: string; created_at: number };
export function BotExecutionApprovals({ organizationId }: { organizationId: string }) {
  const [approvals, setApprovals] = useState<Approval[]>([]), [error, setError] = useState("");
  const [busy, setBusy] = useState<string | null>(null), [refresh, setRefresh] = useState(0);
  const path = organizationPath(organizationId) + "/my-bots/execution-approvals";
  useEffect(() => {
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try { const rows = await request<Approval[]>(path, { signal: controller.signal });
        if (!controller.signal.aborted) { setApprovals(rows); setError(""); }
      } catch (cause) { if (!aborted(cause)) setError(errorMessage(cause)); }
      if (!controller.signal.aborted) timer = setTimeout(poll, 2000);
    };
    void poll(); return () => { controller.abort(); clearTimeout(timer); };
  }, [path, refresh]);
  const decide = async (id: string, allow: boolean) => {
    setBusy(id);
    try { await request(path + "/" + id, { method: "POST", body: { allow } }); setRefresh(value => value + 1); }
    catch (cause) { setError(errorMessage(cause)); } finally { setBusy(null); }
  };
  return <section className="org-bot-approvals" aria-label="Local execution approvals">
    {error && <p role="alert" className="org-error">{error}</p>}
    {approvals.length > 0 && <h3>Execution requests for your computer</h3>}
    {approvals.map(approval => <article key={approval.id}>
      <p>One execution, confined to this topic's bot workspace. No host home directory or network access.</p>
      <small>Requester {approval.requester_name} · Topic {approval.topic_name}</small>
      <pre><code>{JSON.parse(approval.command_json).map((part: string) => JSON.stringify(part)).join(" ")}</code></pre>
      <details><summary>Exact request identity</summary><code>SHA-256 {approval.command_sha256}</code></details>
      <button disabled={busy !== null} onClick={() => void decide(approval.id, true)}>Allow once</button>{" "}
      <button disabled={busy !== null} onClick={() => void decide(approval.id, false)}>Deny</button>
    </article>)}
  </section>;
}
