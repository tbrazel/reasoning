// Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
import React, { useEffect, useState } from "react";
import { aborted, errorMessage, organizationPath, request, usd } from "./organization-api";

type KeyUsage = { key_id: string; label: string; calls: number; actual_microusd: number;
  reserved_microusd: number; uncertain_microusd: number; unknown_calls: number };

export function ProviderKeyUsage({ organizationId }: { organizationId: string }) {
  const [keys, setKeys] = useState<KeyUsage[]>([]), [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    const refresh = () => { void request<{ keys: KeyUsage[] }>(organizationPath(organizationId) + "/provider-key-usage", { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) { setKeys(value.keys); setError(""); } })
      .catch(cause => { if (!aborted(cause)) setError(errorMessage(cause)); }); };
    refresh(); const timer = setInterval(refresh, 30000);
    return () => { controller.abort(); clearInterval(timer); };
  }, [organizationId]);
  return <section aria-label="Per-key spend"><h4>Per-key spend</h4>
    <small>All-time Reasoning usage only. Excludes outside OpenRouter usage and bot subscriptions.</small>
    {error && <p role="alert">{error}</p>}
    <table><thead><tr><th>Key</th><th>Calls</th><th>Charged</th><th>Held</th><th>Unknown holds</th></tr></thead>
      <tbody>{keys.map(key => <tr key={key.key_id}><th scope="row">{key.label}</th><td>{key.calls}</td>
        <td>{usd(key.actual_microusd)}</td><td>{usd(key.reserved_microusd)}</td>
        <td title={`${key.unknown_calls} calls with unknown charge`}>{usd(key.uncertain_microusd)}</td></tr>)}</tbody></table>
  </section>;
}
