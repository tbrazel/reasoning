import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

let server;
let PriceBrowser;

before(async () => {
  server = await createServer({
    appType: "custom",
    logLevel: "silent",
    server: { middlewareMode: true },
  });
  ({ PriceBrowser } = await server.ssrLoadModule("/src/PriceBrowser.tsx"));
});

after(async () => {
  await server?.close();
});

test("separates current prices from realized usage history", () => {
  const html = renderToStaticMarkup(React.createElement(PriceBrowser));

  assert.match(html, /Current prices/);
  assert.match(html, /Usage history/);
  assert.match(html, /Token rates/);
  assert.doesNotMatch(html, /CALL RECORDS/);

  const history = renderToStaticMarkup(
    React.createElement(PriceBrowser, { defaultView: "history" }),
  );
  assert.match(history, /OpenRouter call trail/);
  assert.match(history, /all projects/);
  assert.match(history, /reported spend/);
  assert.match(history, /CALL RECORDS/);
  assert.doesNotMatch(history, /Token rates/);
});

test("keeps the focused model chooser free of accounting tables", () => {
  const html = renderToStaticMarkup(
    React.createElement(PriceBrowser, {
      selection: { targetLabel: "Student", onSelect: () => undefined },
    }),
  );

  assert.match(html, /Choose a priced model/);
  assert.doesNotMatch(html, /OpenRouter call trail/);
});

test("offers stars, source filters, and task-specific evidence when choosing a workflow model", () => {
  const html = renderToStaticMarkup(
    React.createElement(PriceBrowser, {
      selection: {
        targetLabel: "Student",
        onSelect: () => undefined,
        rankingContext: { projectId: "prj_test", workflowId: "wf_test" },
      },
    }),
  );

  assert.match(html, /REASONING EVIDENCE/);
  assert.match(html, /compatible runs/);
  assert.match(html, /starred/);
  assert.match(html, /open-weight listing/);
  assert.match(html, /Catalog rates and capabilities are provider metadata/);
});
