import assert from "node:assert/strict";
import {
  mkdtemp,
  readFile,
  chmod,
  lstat,
  rm,
  symlink,
  writeFile,
} from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import {
  externalLink,
  PICKER_URL,
  readConnection,
  remotePreferences,
  sameServer,
  saveConnection,
  serverOrigin,
  serverPartition,
  trustedPicker,
} from "../src/server-connection.mjs";

test("server selection accepts HTTPS and literal loopback HTTP only", () => {
  assert.equal(
    serverOrigin(" https://CHAT.example.org:443/ "),
    "https://chat.example.org",
  );
  for (const origin of [
    "http://localhost:8766",
    "http://127.0.0.1:8766",
    "http://[::1]:8766",
  ])
    assert.equal(serverOrigin(origin), origin);
  for (const value of [
    null,
    {},
    "http://chat.example.org",
    "http://192.168.1.2",
    "http://localhost.evil.org",
    "https://user:secret@chat.example.org",
    "https://chat.example.org/path",
    "https://chat.example.org?token=x",
    "https://chat.example.org#invite=x",
    "file:///etc/passwd",
    "javascript:alert(1)",
    "https://chat.example.org\\@evil.org",
    "https://chat.example.org\n",
    "https://chat.example.org/" + "a".repeat(2048),
  ])
    assert.throws(() => serverOrigin(value));
});

test("sessions are stable per origin and do not collide across servers or ports", () => {
  assert.equal(
    serverPartition("https://chat.example.org/"),
    serverPartition("https://CHAT.example.org"),
  );
  assert.notEqual(
    serverPartition("https://chat.example.org"),
    serverPartition("https://other.example.org"),
  );
  assert.notEqual(
    serverPartition("http://localhost:8766"),
    serverPartition("http://localhost:8767"),
  );
  assert.match(
    serverPartition("https://chat.example.org"),
    /^persist:reasoning-server-[0-9a-f]{64}$/,
  );
});

test("remote pages have no preload or Node and cannot navigate into native protocols", () => {
  const preferences = remotePreferences("https://chat.example.org");
  assert.equal(preferences.preload, undefined);
  assert.equal(preferences.nodeIntegration, false);
  assert.equal(preferences.contextIsolation, true);
  assert.equal(preferences.sandbox, true);
  assert.equal(preferences.webSecurity, true);
  assert.equal(preferences.webviewTag, false);
  assert.ok(
    sameServer(
      "https://chat.example.org/assets/main.js",
      "https://chat.example.org",
    ),
  );
  for (const url of [
    "https://chat.example.org.evil.org",
    "https://user@chat.example.org",
    "file:///etc/passwd",
    "reasoning-app://connection/index.html",
    "data:text/html,hi",
    "https://other.example.org",
    "http://chat.example.org",
  ]) {
    assert.equal(sameServer(url, "https://chat.example.org"), false);
  }
});

test("external links exclude credentials and non-browser protocols", () => {
  assert.equal(
    externalLink("https://example.org/paper?q=algebra"),
    "https://example.org/paper?q=algebra",
  );
  for (const url of [
    "file:///etc/passwd",
    "javascript:alert(1)",
    "mailto:someone@example.org",
    "https://user:secret@example.org",
    "data:text/html,hi",
    "https://example.org\\@evil.org",
    null,
  ])
    assert.equal(externalLink(url), null);
});

test("connection IPC trusts only the actual picker top-level frame", () => {
  const frame = { url: PICKER_URL };
  const contents = { mainFrame: frame, isDestroyed: () => false };
  assert.ok(trustedPicker({ sender: contents, senderFrame: frame }, contents));
  assert.equal(
    trustedPicker(
      { sender: contents, senderFrame: { url: PICKER_URL } },
      contents,
    ),
    false,
  );
  assert.equal(
    trustedPicker({ sender: {}, senderFrame: frame }, contents),
    false,
  );
  frame.url = "https://chat.example.org";
  assert.equal(
    trustedPicker({ sender: contents, senderFrame: frame }, contents),
    false,
  );
  assert.equal(
    trustedPicker({ sender: contents, senderFrame: frame }, null),
    false,
  );
});

test("connection settings persist only a validated URL in a private atomic file", async () => {
  const directory = await mkdtemp(
    path.join(os.tmpdir(), "reasoning-connection-test-"),
  );
  try {
    assert.equal(await readConnection(directory), null);
    await saveConnection(directory, "https://chat.example.org");
    assert.equal(await readConnection(directory), "https://chat.example.org");
    const filename = path.join(directory, "connection.json");
    assert.deepEqual(JSON.parse(await readFile(filename, "utf8")), {
      origin: "https://chat.example.org",
    });
    assert.equal((await lstat(filename)).mode & 0o777, 0o600);
    await assert.rejects(() =>
      saveConnection(directory, "https://user:secret@example.org"),
    );
    assert.equal(await readConnection(directory), "https://chat.example.org");
    await chmod(filename, 0o644);
    assert.equal(await readConnection(directory), null);
    await rm(filename);
    const target = path.join(directory, "other.json");
    await writeFile(target, '{"origin":"https://evil.example.org"}', {
      mode: 0o600,
    });
    await symlink(target, filename);
    assert.equal(await readConnection(directory), null);
    await saveConnection(directory, "https://good.example.org");
    assert.equal(await readConnection(directory), "https://good.example.org");
    assert.equal(
      await readFile(target, "utf8"),
      '{"origin":"https://evil.example.org"}',
    );
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});
