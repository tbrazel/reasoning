// Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
import assert from "node:assert/strict";
import { before, after, test } from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

let server, models, help, bibliography;
before(async () => {
  server = await createServer({ root: fileURLToPath(new URL("../", import.meta.url)), appType: "custom", logLevel: "silent", server: { middlewareMode: true } });
  models = await server.ssrLoadModule("/src/ModelBrowser.tsx");
  help = await server.ssrLoadModule("/src/HelpPage.tsx");
  bibliography = await server.ssrLoadModule("/src/ResearchBibliography.tsx");
});
after(async () => { await server?.close(); });

test("favorites precede other sections without duplicating or whitelisting models", () => {
  const catalog = { models: [], recommendations: { model_ids: ["test/math"] } };
  assert.deepEqual(models.modelSections(["test/math", "test/other"], catalog, "", ["test/other"]), [
    { title: "Favorites", ids: ["test/other"] }, { title: "Best for math", ids: ["test/math"] },
  ]);
  assert.deepEqual(models.modelSections(["test/math", "test/other"], catalog, "math", ["test/other"]), [
    { title: "Best for math", ids: ["test/math"] },
  ]);
});

test("help covers real roles, scoped tools, approvals, test recipes and commands", () => {
  const html = renderToStaticMarkup(React.createElement(help.HelpPage, { onClose() {} }));
  for (const text of ["librarians", "formalizers", "verifiers", "make bot-doctor", "GitHub", "Lean", "CAS", "Shift+Enter", "/compute", "/goal"])
    assert.ok(html.includes(text), text);
});

test("composer actions and model metadata use separate compact flex groups", async () => {
  const app = await readFile(new URL("../src/OrganizationApp.tsx", import.meta.url), "utf8");
  const css = await readFile(new URL("../src/organization.css", import.meta.url), "utf8");
  assert.ok(app.includes('className="org-composer-actions"') && app.includes('className="org-composer-model"'));
  assert.match(css, /org-composer-model \.org-model-menu[^}]*min-width:0/);
  assert.doesNotMatch(app, /<ReasoningEffort/);
  assert.ok(app.includes("stop_active: true"));
  assert.ok(!app.includes("Stop active work first:"));
});

test("bibliography explains statements instead of exposing technical hashes in its main heading", () => {
  const html = renderToStaticMarkup(React.createElement(bibliography.ResearchBibliography, { organizationId: "org", onAuthLost() {} }));
  assert.ok(html.includes("Sources, exact statements, and their ledger links"));
  assert.ok(!html.includes("SHA-256"));
});
