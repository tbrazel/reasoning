import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

let server;
let ArtifactPane;
let RichTextRenderer;
const previousNavigator = Object.getOwnPropertyDescriptor(globalThis, "navigator");
const previousWindow = Object.getOwnPropertyDescriptor(globalThis, "window");

before(async () => {
  Object.defineProperty(globalThis, "navigator", {
    configurable: true,
    value: { userAgent: "node-test" },
  });
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: {
      localStorage: { getItem: () => null },
      location: { pathname: "/" },
    },
  });
  server = await createServer({
    appType: "custom",
    logLevel: "silent",
    server: { middlewareMode: true },
  });
  ({ ArtifactPane } = await server.ssrLoadModule("/src/Artifact.tsx"));
  ({ RichTextRenderer } = await server.ssrLoadModule("/src/MarkdownRenderer.tsx"));
});

after(async () => {
  await server?.close();
  if (previousNavigator) Object.defineProperty(globalThis, "navigator", previousNavigator);
  else delete globalThis.navigator;
  if (previousWindow) Object.defineProperty(globalThis, "window", previousWindow);
  else delete globalThis.window;
});

test("renders artifacts as a non-modal workbench pane", () => {
  const html = renderToStaticMarkup(
    React.createElement(ArtifactPane, {
      projectId: "project-a",
      name: "notes.txt",
      onClose: () => undefined,
    }),
  );

  assert.match(html, /<aside id="artifact-panel"/);
  assert.match(html, /aria-label="Artifact viewer"/);
  assert.match(html, /role="separator"/);
  assert.doesNotMatch(html, /aria-modal=/);
  assert.doesNotMatch(html, /role="dialog"/);
  assert.doesNotMatch(html, /artifact-overlay/);
});

test("offers the desktop's native artifact reveal action", () => {
  globalThis.window.reasoningDesktop = {
    platform: "darwin",
    revealArtifact: async () => undefined,
  };
  const html = renderToStaticMarkup(
    React.createElement(ArtifactPane, {
      projectId: "project-a",
      name: "notes.txt",
      onClose: () => undefined,
    }),
  );
  assert.match(html, />Reveal in Finder<\/button>/);
  const reference = renderToStaticMarkup(
    React.createElement(RichTextRenderer, {
      inline: false,
      text: "Open artifacts/notes/audit.md",
    }),
  );
  assert.match(reference, /aria-label="Reveal in Finder: artifacts\/notes\/audit\.md"/);
  delete globalThis.window.reasoningDesktop;
});
