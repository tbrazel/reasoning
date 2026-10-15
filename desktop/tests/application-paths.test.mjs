import assert from "node:assert/strict";
import { test } from "node:test";

import { preferredRuntimePaths } from "../src/application-paths.mjs";

test("macOS prefers Homebrew's supported keg-only Node runtime", () => {
  assert.deepEqual(preferredRuntimePaths("darwin"), [
    "/opt/homebrew/opt/node@24/bin",
    "/usr/local/opt/node@24/bin",
  ]);
});

test("other platforms do not receive macOS Homebrew paths", () => {
  assert.deepEqual(preferredRuntimePaths("linux"), []);
  assert.deepEqual(preferredRuntimePaths("win32"), []);
});
