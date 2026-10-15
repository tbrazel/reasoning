import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

let server;
let PortableHandoffControl;

before(async () => {
  server = await createServer({
    appType: "custom",
    logLevel: "silent",
    server: { middlewareMode: true },
  });
  ({ PortableHandoffControl } = await server.ssrLoadModule("/src/Chat.tsx"));
});

after(async () => server?.close());

test("offers a quiet, explicit new-chat handoff action", () => {
  const html = renderToStaticMarkup(
    React.createElement(PortableHandoffControl, {
      projectId: "project-a",
      chatId: "chat-a",
      operationId: "op_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      workspaceId: "workspace-a",
      onCreated: () => undefined,
    }),
  );

  assert.match(html, /class="portable-handoff"/);
  assert.match(html, />continue in a new chat</);
  assert.doesNotMatch(html, /attest and continue/);
});

test("keeps review and promotion language in the handoff surface", async () => {
  const source = await import("node:fs/promises").then((fs) =>
    fs.readFile(new URL("../src/Chat.tsx", import.meta.url), "utf8"),
  );

  assert.match(source, /Review required:/);
  assert.match(source, /Why is it safe to continue after these effects\?/);
  assert.match(source, /promotePortableCheckpoint/);
  assert.match(source, /attest and continue/);
  assert.match(source, /imported handoff/);
});
