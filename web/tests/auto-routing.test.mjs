import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

let server;
let AutoRoutingControl;

before(async () => {
  server = await createServer({
    appType: "custom",
    logLevel: "silent",
    server: { middlewareMode: true },
  });
  ({ AutoRoutingControl } = await server.ssrLoadModule(
    "/src/AutoRoutingControl.tsx",
  ));
});

after(async () => {
  await server?.close();
});

test("chat Auto waits for a prompt and exposes explicit routing objectives", () => {
  const html = renderToStaticMarkup(
    React.createElement(AutoRoutingControl, {
      source: "chat",
      projectId: "project",
      resourceId: "chat",
      prompt: "   ",
      onSelect: () => undefined,
    }),
  );

  assert.match(html, /aria-label="Auto routing objective"/);
  assert.match(html, />balanced</);
  assert.match(html, />quality</);
  assert.match(html, />reliable</);
  assert.match(html, />lowest cost</);
  assert.match(html, />fastest</);
  assert.match(html, /disabled=""[^>]*>Auto<\/button>/);
  assert.match(html, /Type a prompt before asking Auto to route it/);
});

test("workflow Auto can inspect prior compatible runs without a prompt", () => {
  const html = renderToStaticMarkup(
    React.createElement(AutoRoutingControl, {
      source: "workflow",
      projectId: "project",
      resourceId: "workflow",
      onSelect: () => undefined,
    }),
  );

  assert.match(html, />Auto<\/button>/);
  assert.doesNotMatch(html, /disabled=""[^>]*>Auto<\/button>/);
  assert.match(html, /Choose a concrete configuration from live compatibility/);
});
