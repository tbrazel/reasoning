import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

let server, BotWebhooks;
before(async () => {
  server = await createServer({ appType: "custom", logLevel: "silent", server: { middlewareMode: true } });
  ({ BotWebhooks } = await server.ssrLoadModule("/src/BotWebhooks.tsx"));
});
after(async () => { await server?.close(); });

test("webhook administration explains signature, deduplication, scope and history boundaries", () => {
  const markup = renderToStaticMarkup(React.createElement(BotWebhooks, { organizationId: "a".repeat(32), bots: [] }));
  assert.match(markup, /deduplicate delivery IDs/);
  assert.match(markup, /Bot and model replies are excluded/);
  assert.match(markup, /Private networks, redirects, and nonstandard ports are refused/);
  assert.match(markup, /no history replay/);
  assert.match(markup, /Public HTTPS receiver URL/);
});

test("webhook creation exposes only active read-capable bots as escaped options", () => {
  const markup = renderToStaticMarkup(React.createElement(BotWebhooks, { organizationId: "a".repeat(32), bots: [
    { id: "b".repeat(32), enabled: true, name: "<script>reader</script>", scopes: ["messages:read"] },
    { id: "c".repeat(32), enabled: true, name: "write-only", scopes: ["messages:write"] },
    { id: "d".repeat(32), enabled: false, name: "revoked-reader", scopes: ["messages:read"] },
  ] }));
  assert.match(markup, /&lt;script&gt;reader&lt;\/script&gt;/);
  assert.doesNotMatch(markup, /write-only|revoked-reader|<script>/);
});
