import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { createServer } from "vite";

let server;
let authoredWorkflowTarget;

before(async () => {
  server = await createServer({
    appType: "custom",
    logLevel: "silent",
    server: { middlewareMode: true },
  });
  ({ authoredWorkflowTarget } = await server.ssrLoadModule("/src/workflowNavigation.ts"));
});

after(async () => {
  await server?.close();
});

const project = {
  id: "moderation",
  name: "Moderation",
  chats: [],
  workflow_runs: [],
  workflows: [
    { workflow_id: "corpus", revision_id: "rev-2", name: "Corpus", updated_at: "2026-08-29T12:00:00Z" },
    { workflow_id: "benchmark", revision_id: "rev-4", name: "Benchmark", updated_at: "2026-08-29T12:01:00Z" },
  ],
};

test("opens the last workflow explicitly authored by the completed chat turn", () => {
  const target = authoredWorkflowTarget(
    project,
    new Map([["corpus", "rev-1"], ["benchmark", "rev-3"]]),
    [
      { workflow_id: "corpus", revision_id: "rev-2" },
      { workflow_id: "benchmark", revision_id: "rev-4" },
    ],
  );
  assert.equal(target, "benchmark");
});

test("falls back to the newest changed head for executors without live tool events", () => {
  const target = authoredWorkflowTarget(
    project,
    new Map([["corpus", "rev-1"], ["benchmark", "rev-3"]]),
    [],
  );
  assert.equal(target, "benchmark");
});

test("does not navigate when the turn authored no workflow revision", () => {
  const target = authoredWorkflowTarget(
    project,
    new Map([["corpus", "rev-2"], ["benchmark", "rev-4"]]),
    [],
  );
  assert.equal(target, null);
});
