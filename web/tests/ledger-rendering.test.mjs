// Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";
import { fileURLToPath } from "node:url";

let server, renderer;
before(async () => {
  server = await createServer({ root: fileURLToPath(new URL("../", import.meta.url)), appType: "custom", logLevel: "silent", server: { middlewareMode: true } });
  renderer = await server.ssrLoadModule("/src/MarkdownRenderer.tsx");
});
after(async () => { await server?.close(); });

test("inline ledger mathematics preserves accessible MathML and one visual KaTeX tree", () => {
  const html = renderToStaticMarkup(React.createElement(renderer.RichTextRenderer, { inline: true,
    text: "A statement: $\\int_M d\\alpha = \\int_{\\partial M} \\alpha$ and $\\widehat{x+y}$." }));
  assert.equal((html.match(/class="katex-mathml"/g) ?? []).length, 2);
  assert.equal((html.match(/class="katex-html"/g) ?? []).length, 2);
  assert.equal((html.match(/<math /g) ?? []).length, 2);
  assert.match(html, /<annotation encoding="application\/x-tex">/);
  assert.match(html, /<svg[^>]*>/);
  assert.doesNotMatch(html, /katex-error/);
});

test("allowing generated math does not expose executable author HTML or unsafe links", () => {
  const html = renderToStaticMarkup(React.createElement(renderer.RichTextRenderer, { inline: true,
    text: "$x^2$ <script>alert(1)</script> <svg onload=alert(1)></svg> [unsafe](javascript:alert%281%29)" }));
  assert.match(html, /<math /);
  assert.doesNotMatch(html, /<script|<svg[^>]*onload|href="javascript:/);
});
