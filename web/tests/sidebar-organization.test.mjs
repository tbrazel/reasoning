import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

let server;
let Sidebar;
const previousWindow = Object.getOwnPropertyDescriptor(globalThis, "window");

function props(selectedProject = "recent-project") {
  const recent = new Date().toISOString();
  const old = new Date(2020, 0, 1).toISOString();
  const projects = [
    {
      id: "recent-project",
      name: "Current moderation",
      updated_at: recent,
      chats: [
        { id: "recent-chat", title: "Today's review", updated_at: recent },
        ...Array.from({ length: 10 }, (_, index) => ({
          id: `old-chat-${index}`,
          title: `Old exact chat ${index}`,
          updated_at: old,
        })),
      ],
      workflows: [],
      workflow_runs: [],
    },
    ...Array.from({ length: 10 }, (_, index) => ({
      id: `old-project-${index}`,
      name: `Old exact project ${index}`,
      updated_at: old,
      chats: [],
      workflows: [],
      workflow_runs: [],
    })),
  ];
  const noop = () => undefined;
  return {
    projects,
    loadState: "fresh",
    loadError: "",
    onRetry: noop,
    selectedProject,
    selectedChat: selectedProject === "recent-project" ? "recent-chat" : null,
    selectedWorkflow: null,
    selectedRun: null,
    onSelectChat: noop,
    onSelectWorkflow: noop,
    onNewProject: noop,
    onNewChat: noop,
    onNewWorkflow: noop,
    onPinProject: noop,
    onRenameProject: noop,
    onArchiveProject: noop,
    onDeleteProject: noop,
    onRenameChat: noop,
    onPinChat: noop,
    onArchiveChat: noop,
    onDeleteChat: noop,
    onRenameWorkflow: noop,
    onPinWorkflow: noop,
    onArchiveWorkflow: noop,
    onDeleteWorkflow: noop,
    onMoveProject: noop,
    onMoveChat: noop,
    onReorderProjects: noop,
    onReorderChats: noop,
  };
}

before(async () => {
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: { localStorage: { getItem: () => null, setItem: () => undefined } },
  });
  server = await createServer({
    appType: "custom",
    logLevel: "silent",
    server: { middlewareMode: true },
  });
  ({ Sidebar } = await server.ssrLoadModule("/src/Sidebar.tsx"));
});

after(async () => {
  await server?.close();
  if (previousWindow) Object.defineProperty(globalThis, "window", previousWindow);
  else delete globalThis.window;
});

test("manual Explorer order is fully visible and draggable", () => {
  const html = renderToStaticMarkup(React.createElement(Sidebar, props()));
  assert.doesNotMatch(html, />history<\/button>/);
  assert.doesNotMatch(html, />Today<\/span>/);
  assert.match(html, /Old exact project 9/);
  assert.match(html, /Old exact chat 9/);
  assert.match(html, /Reorder Current moderation/);
  assert.match(html, /Reorder Today&#x27;s review/);
});

test("manual order keeps an older selected project visible", () => {
  const html = renderToStaticMarkup(
    React.createElement(Sidebar, props("old-project-9")),
  );
  assert.match(html, /Old exact project 9/);
});
