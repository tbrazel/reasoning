import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

let server;
let parseExplorerBootstrap;
let Sidebar;

before(async () => {
  server = await createServer({
    appType: "custom",
    logLevel: "silent",
    server: { middlewareMode: true },
  });
  ({ parseExplorerBootstrap } = await server.ssrLoadModule("/src/explorerBootstrap.ts"));
  ({ Sidebar } = await server.ssrLoadModule("/src/Sidebar.tsx"));
});

after(async () => {
  await server?.close();
});

test("accepts only the current explorer bootstrap schema", () => {
  const current = JSON.stringify({ version: 1, generated_at: "now", projects: [] });
  assert.deepEqual(parseExplorerBootstrap(current), {
    version: 1,
    generated_at: "now",
    projects: [],
  });
  assert.equal(parseExplorerBootstrap("not json"), null);
  assert.equal(
    parseExplorerBootstrap(JSON.stringify({ version: 2, generated_at: "later", projects: [] })),
    null,
  );
});

const sidebarProps = (loadState) => ({
  projects: [],
  loadState,
  loadError: "offline",
  onRetry: () => undefined,
  selectedProject: null,
  selectedChat: null,
  selectedWorkflow: null,
  selectedRun: null,
  onSelectChat: () => undefined,
  onSelectWorkflow: () => undefined,
  onNewProject: () => undefined,
  onNewChat: () => undefined,
  onNewWorkflow: () => undefined,
  onPinProject: () => undefined,
  onRenameProject: () => undefined,
  onArchiveProject: () => undefined,
  onDeleteProject: () => undefined,
  onRenameChat: () => undefined,
  onPinChat: () => undefined,
  onArchiveChat: () => undefined,
  onDeleteChat: () => undefined,
  onRenameWorkflow: () => undefined,
  onPinWorkflow: () => undefined,
  onArchiveWorkflow: () => undefined,
  onDeleteWorkflow: () => undefined,
  onMoveProject: () => undefined,
  onMoveChat: () => undefined,
  onReorderProjects: () => undefined,
  onReorderChats: () => undefined,
});

test("never presents a loading explorer as an empty workbench", () => {
  const loading = renderToStaticMarkup(
    React.createElement(Sidebar, sidebarProps("loading")),
  );
  assert.match(loading, /Restoring projects/);
  assert.doesNotMatch(loading, /No projects/);

  const fresh = renderToStaticMarkup(
    React.createElement(Sidebar, sidebarProps("fresh")),
  );
  assert.match(fresh, /No projects/);
});
