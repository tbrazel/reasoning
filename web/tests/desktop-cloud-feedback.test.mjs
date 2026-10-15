import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";

test("Cloud actions show progress and distinguish submission from completion", async () => {
  const source = await readFile(
    new URL("../src/DesktopCloudSettings.tsx", import.meta.url),
    "utf8",
  );

  assert.match(source, /setPendingAction\(label\)/);
  assert.match(source, /desktop-cloud-progress" role="status"/);
  assert.match(source, /Connecting Cloud/);
  assert.match(source, /Submitting synthetic Cloud check/);
  assert.match(source, /completion requires a verified artifact/);
  assert.match(source, /normally appears within 5–15 seconds/);
  assert.match(source, /Any submitted run keeps running/);
  assert.match(source, /actionInFlight\.current/);
  assert.match(source, /terminalRunStates\.has\(state\)/);
  assert.match(source, /Verified artifact downloaded/);
  assert.match(source, /auth\?\.failure_code/);
  assert.match(source, /local Cloud security boundary could not initialize/);
  assert.match(source, /macOS Keychain could not securely save the session/);
  assert.doesNotMatch(source, /setError\(String\(reason\)\)/);
});
