import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";

const source = (path) =>
  readFile(new URL(`../src/${path}`, import.meta.url), "utf8");

test("offers persisted semantic appearance presets and system-following light mode", async () => {
  const [appearance, settings, main, css] = await Promise.all([
    source("appearance.ts"),
    source("Settings.tsx"),
    source("main.tsx"),
    source("styles.css"),
  ]);

  for (const preset of ["studio", "ink", "teal"]) {
    assert.match(appearance, new RegExp(`id: "${preset}"`));
  }
  assert.match(appearance, /reasoning\.appearance/);
  assert.match(main, /applyAppearance\(readAppearance\(\)\)/);
  assert.match(settings, /role="radiogroup" aria-label="Visual style"/);
  assert.match(settings, /Color theme/);
  assert.match(appearance, /prefers-color-scheme: dark/);
  assert.match(css, /data-theme="light"/);
  assert.match(css, /:root\[data-appearance="ink"\]/);
  assert.match(css, /:root\[data-appearance="teal"\]/);
  assert.match(css, /color-scheme: dark/);
});

test("keeps compact navigation and workflow actions usable", async () => {
  const [app, workflow, css] = await Promise.all([
    source("App.tsx"),
    source("WorkflowStudio.tsx"),
    source("styles.css"),
  ]);

  assert.match(app, /event\.key !== "Escape"/);
  assert.match(workflow, /className="workflow-toolbar-primary"/);
  assert.match(workflow, /className="workflow-toolbar-actions"/);
  assert.match(css, /@media \(max-width: 1180px\)[\s\S]*?\.ledger-overlay > \.pane-backdrop/);
  assert.match(css, /@media \(max-width: 1180px\)[\s\S]*?\.workflow-toolbar\s*{[\s\S]*?flex-wrap: wrap/);
});

test("keeps the idle composer compact and makes advanced controls one click away", async () => {
  const [chat, css] = await Promise.all([
    source("Chat.tsx"),
    source("styles.css"),
  ]);

  assert.match(chat, /const \[configurationOpen, setConfigurationOpen\]/);
  assert.match(chat, /className={`chat-configuration-summary/);
  assert.match(chat, /id="chat-run-configuration"/);
  assert.match(css, /\.chat-configuration-summary\s*{[\s\S]*?height: 2rem/);
});

test("makes artifact output searchable and grouped", async () => {
  const ledger = await source("Ledger.tsx");

  assert.match(ledger, /aria-label="Search artifacts"/);
  assert.match(ledger, /className="artifact-group"/);
  assert.match(ledger, /No artifacts match/);
});
