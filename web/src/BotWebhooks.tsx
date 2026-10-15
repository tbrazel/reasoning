import React, { useCallback, useEffect, useRef, useState } from "react";
import { BotMetadata, errorMessage, organizationPath, request } from "./organization-api";

type Subscription = { id: string; organization_id: string; bot_id: string; url: string; prefix: string;
  enabled: boolean; disabled_at: number | null; pending: number; dropped: number };
type Delivery = { id: string; sequence: number; state: string; attempts: number; created_at: number; error_code: string | null };
type Creation = Readonly<{ bot_id: string; url: string; prefix: string; client_id: string }>;

export function BotWebhooks({ organizationId, bots }: { organizationId: string; bots: BotMetadata[] }) {
  const root = organizationPath(organizationId) + "/bot-webhooks";
  const labelId = "webhook-" + organizationId + "-";
  const [hooks, setHooks] = useState<Subscription[]>([]);
  const [running, setRunning] = useState<boolean | null>(null);
  const [bot, setBot] = useState("");
  const [url, setUrl] = useState("");
  const [prefix, setPrefix] = useState("");
  const [pending, setPending] = useState<Creation | null>(null);
  const [secret, setSecret] = useState("");
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState("");
  const [deliveries, setDeliveries] = useState<Delivery[]>([]);
  const alive = useRef(false);
  const eligible = bots.filter((record) => record.enabled && record.scopes.includes("messages:read") &&
    !hooks.some((hook) => hook.bot_id === record.id && !hook.disabled_at));
  const load = useCallback(async (signal?: AbortSignal) => {
    const [records, status] = await Promise.all([
      request<Subscription[]>(root, { signal }), request<{ worker_running: boolean }>(root + "/status", { signal }),
    ]);
    if (alive.current) { setHooks(records); setRunning(status.worker_running); }
  }, [root]);
  useEffect(() => {
    alive.current = true;
    const controller = new AbortController();
    void load(controller.signal).catch((cause) => { if (!controller.signal.aborted) setError(errorMessage(cause)); });
    return () => { alive.current = false; controller.abort(); };
  }, [load]);
  useEffect(() => {
    if (!pending) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [pending]);
  useEffect(() => {
    if (!selected) return;
    const controller = new AbortController();
    let loading = false;
    const refresh = async () => {
      if (loading) return;
      loading = true;
      try {
        const page = await request<Delivery[]>(root + "/" + selected + "/deliveries?limit=100", { signal: controller.signal });
        if (!controller.signal.aborted) setDeliveries(page);
        await load(controller.signal);
      } catch (cause) { if (!controller.signal.aborted) setError(errorMessage(cause)); }
      finally { loading = false; }
    };
    void refresh();
    const timer = window.setInterval(() => { void refresh(); }, 5000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [selected, root, load]);
  const create = async (attempt: Creation) => {
    setBusy(true); setError(""); setSecret(""); setShow(false);
    try {
      const result = await request<Subscription & { signing_secret: string }>(root, { method: "POST", body: attempt });
      if (result.organization_id !== organizationId || result.bot_id !== attempt.bot_id || result.prefix !== attempt.prefix ||
          !/^[a-f0-9]{32}$/.test(result.id) || !/^rnwh_[A-Za-z0-9_-]{43}$/.test(result.signing_secret)) {
        throw new Error("Webhook creation receipt is invalid. Keep and replay this exact request.");
      }
      if (alive.current) { setSecret(result.signing_secret); setPending(null); setBot(""); setUrl(""); setPrefix(""); }
      await load();
    } catch (cause) { if (alive.current) setError(errorMessage(cause)); }
    finally { if (alive.current) setBusy(false); }
  };
  return <section aria-label="Outgoing bot webhooks">
    <h3>Outgoing webhooks</h3>
    <p className="org-help">Send new human messages from a bot’s granted topics to one public HTTPS receiver.
      Bot and model replies are excluded. Receivers must verify signatures and deduplicate delivery IDs.
      Private networks, redirects, and nonstandard ports are refused.</p>
    {running === false && <p role="alert">The webhook worker is not running. Captures remain queued within their limits and expire after 24 hours.</p>}
    {error && <p role="alert" className="org-error">{error}</p>}
    {secret && <div className="org-invite-link"><label htmlFor={labelId + "secret"}>Webhook signing secret</label>
      <input id={labelId + "secret"} readOnly type={show ? "text" : "password"} value={secret} autoComplete="off" onFocus={(event) => event.target.select()} />
      <small>Save in the receiver’s private secret file. Lists do not return it; it is not saved in browser storage.</small>
      <button type="button" onClick={() => setShow(!show)}>{show ? "Hide signing secret" : "Show signing secret"}</button>
      <button type="button" onClick={() => setSecret("")}>Dismiss signing secret</button>
    </div>}
    {pending && <div role="status">
      <p>A creation request is retained for {pending.url}. It may already have succeeded. Replay sends the same input and request ID.
        This recovery is held only while these settings stay open.</p>
      <button disabled={busy} onClick={() => { void create(pending); }}>Replay webhook creation</button>
      <button disabled={busy} onClick={() => { if (window.confirm("Forget this request? Any created subscription remains active. Inspect the list and revoke it before making a replacement.")) setPending(null); }}>Forget webhook request</button>
    </div>}
    <form onSubmit={(event) => {
      event.preventDefault();
      const attempt = Object.freeze({ bot_id: bot, url, prefix, client_id: crypto.randomUUID() });
      setPending(attempt); void create(attempt);
    }}>
      <fieldset disabled={busy || !!pending}><legend>New subscription</legend>
        <label htmlFor={labelId + "bot"}>Webhook bot</label><select id={labelId + "bot"} required value={bot} onChange={(event) => setBot(event.target.value)}>
          <option value="">Select an active read-capable bot</option>
          {eligible.map((record) => <option key={record.id} value={record.id}>{record.name}</option>)}
        </select>
        <label htmlFor={labelId + "url"}>Public HTTPS receiver URL</label><input id={labelId + "url"} type="url" required maxLength={2048} placeholder="https://bot.example.org/hooks"
          value={url} onChange={(event) => setUrl(event.target.value)} />
        <label htmlFor={labelId + "prefix"}>Message prefix (optional)</label><input id={labelId + "prefix"} aria-describedby={labelId + "prefix-help"}
          maxLength={64} value={prefix} onChange={(event) => setPrefix(event.target.value)} />
        <small id={labelId + "prefix-help"}>Exact, case-sensitive prefix. Empty means every new human message in granted topics; no history replay.</small>
        <button className="org-primary" disabled={!bot || !url}>Create webhook</button>
      </fieldset>
    </form>
    <button disabled={busy} onClick={() => { void load().catch((cause) => setError(errorMessage(cause))); }}>Refresh webhooks</button>
    <ul className="org-admin-list">{hooks.map((hook) => <li key={hook.id}>
      <div><strong>{bots.find((record) => record.id === hook.bot_id)?.name ?? "Bot"}</strong>
        <small>Subscription ID: <code>{hook.id}</code></small>
        <small>{hook.url} · {hook.disabled_at ? "Revoked" : hook.enabled ? "Enabled" : "Authority unavailable"}</small>
        <small>Prefix: {hook.prefix || "all human messages"} · {hook.pending} pending · {hook.dropped} dropped at capture limits</small></div>
      <button disabled={busy} onClick={() => { setDeliveries([]); setSelected(selected === hook.id ? "" : hook.id); }}>Inspect deliveries</button>
      {!hook.disabled_at && <button disabled={busy} onClick={() => {
        if (!window.confirm("Revoke this webhook and erase its signing secret? Queued messages are cancelled. In-flight requests may already have reached the receiver. Create a replacement to change the endpoint or secret.")) return;
        setBusy(true); setError(""); setSecret("");
        void request(root + "/" + hook.id, { method: "DELETE" }).then(() => load()).catch((cause) => {
          if (alive.current) setError(errorMessage(cause));
        }).finally(() => { if (alive.current) setBusy(false); });
      }}>Revoke webhook</button>}
    </li>)}</ul>
    {selected && <section aria-label="Webhook delivery history"><h4>Recent deliveries</h4>
      <p className="org-help">Up to 100 recent records. Accepted means the receiver acknowledged, not that its work finished.
        Unknown means it might have acted; automatic retries use the same ID and stop after five attempts.</p>
      <ul>{deliveries.map((delivery) => <li key={delivery.id}><code>{delivery.id}</code> · {delivery.state} · {delivery.attempts} attempts
        {delivery.error_code && <> · {delivery.error_code}</>}</li>)}</ul>
    </section>}
  </section>;
}
