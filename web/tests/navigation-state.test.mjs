import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { createServer } from "vite";

let server, state;
before(async () => {
  server = await createServer({ server: { middlewareMode: true }, appType: "custom" });
  state = await server.ssrLoadModule("/src/navigationState.ts");
});
after(async () => { await server?.close(); delete globalThis.localStorage; });

test("navigation preferences are scoped by user and organization", () => {
  const values = new Map(); globalThis.localStorage = { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) };
  state.saveNavigation("alice", "org-a", { topic: "topic", collapsedProjects: ["project"] });
  state.saveNavigation("alice", "org-a", { collapsedChannels: ["channel"] });
  assert.deepEqual(state.readNavigation("alice", "org-a"), { topic: "topic", collapsedProjects: ["project"], collapsedChannels: ["channel"] });
  assert.deepEqual(state.readNavigation("bob", "org-a"), { collapsedProjects: [], collapsedChannels: [] });
  assert.deepEqual(state.readNavigation("alice", "org-b"), { collapsedProjects: [], collapsedChannels: [] });
  state.saveNavigation("", "org-a", { topic: "unsafe" }); assert.equal(values.size, 1);
});

test("malformed or unavailable storage does not break navigation", () => {
  globalThis.localStorage = { getItem: () => "{malformed", setItem: () => { throw new Error("disabled"); } };
  assert.deepEqual(state.readNavigation("user", "org"), { collapsedProjects: [], collapsedChannels: [] });
  assert.doesNotThrow(() => state.saveNavigation("user", "org", { topic: "topic" }));
});
