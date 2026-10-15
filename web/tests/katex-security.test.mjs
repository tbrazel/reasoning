// Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { test } from "node:test";
import katex from "katex";

const require = createRequire(import.meta.url);
const vendored = require("../../src/reasoning_ledger/assets/katex.min.js");

test("web adapters and the standalone TeX checker use the same patched renderer", () => {
  assert.equal(katex.version, vendored.version);
  const [major, minor, patch] = katex.version.split(".").map(Number);
  assert.ok(major > 0 || minor > 18 || (minor === 18 && patch >= 2));
});

for (const [name, renderer] of [["web", katex], ["vendored", vendored]]) {
  test(`${name} math cannot inherit trusted rendering from a polluted prototype`, () => {
    Object.defineProperty(Object.prototype, "trust", { value: true, writable: true, configurable: true });
    try {
      for (const expression of [String.raw`\href{javascript:alert(1)}{click}`, String.raw`\includegraphics{https://tracker.invalid/image.png}`]) {
        const html = renderer.renderToString(expression, { throwOnError: false, strict: false });
        assert.doesNotMatch(html, /<(?:a|img)\b|\s(?:href|src)=/);
      }
    } finally { delete Object.prototype.trust; }
  });
}
