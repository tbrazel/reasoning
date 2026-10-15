// Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

let server, wizard;
before(async () => {
  server = await createServer({ appType: "custom", logLevel: "silent", server: { middlewareMode: true } });
  wizard = await server.ssrLoadModule("/src/CloudBotWizard.tsx");
});
after(async () => { await server?.close(); });

test("cloud bot wizard quotes before creation and separates provider sign-in and hardware billing", () => {
  const html = renderToStaticMarkup(React.createElement(wizard.CloudBotWizard, { organizationId: "a".repeat(32) }));
  for (const text of ["DigitalOcean", "Codex", "Claude Code", "Get quote", "no cloud resources created",
    "SymPy", "GAP", "PARI/GP", "Singular", "Lean 4", "mathlib", "encrypted", "owner-scoped", "your approval"])
    assert.ok(html.includes(text), text);
  assert.match(html, /type="password"/);
  assert.match(html, /Maximum hardware USD\/hour/);
  assert.doesNotMatch(html, /localStorage|api[_-]key=/);
});
