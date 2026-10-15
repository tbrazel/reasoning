import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

let server;
let ActivityBar;

before(async () => {
  server = await createServer({
    appType: "custom",
    logLevel: "silent",
    server: { middlewareMode: true },
  });
  ({ ActivityBar } = await server.ssrLoadModule("/src/ActivityBar.tsx"));
});

after(async () => server?.close());

test("uses compact standard symbols for workbench destinations", () => {
  const html = renderToStaticMarkup(React.createElement(ActivityBar, {
    explorerOpen: true,
    ledgerOpen: false,
    bibliographyOpen: false,
    pricingOpen: false,
    hasProject: true,
    onToggleExplorer: () => undefined,
    onOpenSearch: () => undefined,
    onToggleLedger: () => undefined,
    onOpenBibliography: () => undefined,
    onOpenPricing: () => undefined,
    onOpenSettings: () => undefined,
  }));
  assert.match(html, /aria-label="Toggle Explorer panel"/);
  assert.match(html, /aria-label="Toggle Ledger panel"/);
  assert.match(html, /data-icon="folder"/);
  assert.match(html, /data-icon="search"/);
  assert.match(html, /data-icon="ledger"/);
  assert.match(html, /data-icon="library"/);
  assert.match(html, /data-icon="prices"/);
  assert.match(html, /data-icon="settings"/);
  assert.doesNotMatch(html, /panel-icon-pane/);
});
