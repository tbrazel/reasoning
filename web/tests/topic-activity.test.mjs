import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { readFile } from "node:fs/promises";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

let server, activity, knowledge;
before(async () => {
  server = await createServer({ appType: "custom", logLevel: "silent", server: { middlewareMode: true } });
  activity = await server.ssrLoadModule("/src/TopicActivity.tsx");
  knowledge = await server.ssrLoadModule("/src/KnowledgeWorkspace.tsx");
});
after(async () => { await server?.close(); });

test("topic activity deduplicates updates and orders mixed messages, workflows and compute deterministically", () => {
  const records = [{ id: "workflow:a", created_at: 2, body: "old" }, { id: "message:a", created_at: 1, body: "note" },
    { id: "compute:a", created_at: 3, body: "computation" }, { id: "workflow:a", created_at: 2, body: "new" }];
  assert.deepEqual(activity.orderedActivity(records).map(item => item.body), ["note", "new", "computation"]);
  assert.deepEqual(activity.orderedActivity(records.reverse()).map(item => item.id), ["message:a", "workflow:a", "compute:a"]);
  const sameSecond = [{ id: "message:a", created_at: 1, sequence: 2 }, { id: "message:z", created_at: 1, sequence: 1 }];
  assert.deepEqual(activity.orderedActivity(sameSecond).map(item => item.sequence), [1, 2]);
});

test("bot and inference progress follows its triggering message even within the same second", () => {
  const records = [
    { id: "bot:z", created_at: 1, after_sequence: 2 },
    { id: "run:a", created_at: 1, after_sequence: 1 },
    { id: "message:z", created_at: 1, sequence: 1 },
    { id: "message:a", created_at: 1, sequence: 2 },
    { id: "message:b", created_at: 1, sequence: 3 },
  ];
  const expected = ["message:z", "run:a", "message:a", "bot:z", "message:b"];
  assert.deepEqual(activity.orderedActivity(records).map(item => item.id), expected);
  assert.deepEqual(activity.orderedActivity(records.reverse()).map(item => item.id), expected);
});

test("anchored progress tolerates an earlier run clock, grouped alpha messages, and missing older anchors", () => {
  const records = [
    { id: "message:merged-alpha", created_at: 3, sequence: 1 },
    { id: "bot:reply-to-second-in-group", created_at: 2, after_sequence: 2 },
    { id: "message:next", created_at: 3, sequence: 3 },
    { id: "run:older-page", created_at: 1, after_sequence: 0 },
    { id: "run:later", created_at: 4, after_sequence: 1 },
  ];
  assert.deepEqual(activity.orderedActivity(records).map(item => item.id), [
    "run:older-page", "message:merged-alpha", "bot:reply-to-second-in-group", "message:next", "run:later",
  ]);
});

test("compute and workflow sequence counters are not treated as topic-message anchors", () => {
  const records = [
    { id: "compute:local-counter", created_at: 1, sequence: 20 },
    { id: "message:a", created_at: 1, sequence: 5 },
    { id: "run:a", created_at: 1, after_sequence: 5 },
    { id: "workflow:a", created_at: 1, sequence: 1 },
  ];
  assert.deepEqual(activity.orderedActivity(records).map(item => item.id), ["message:a", "run:a", "compute:local-counter", "workflow:a"]);
});

test("workspace links use same-origin organization URLs in the current tab and no credentials", () => {
  assert.equal(knowledge.knowledgeURL("org", "ledger", "topic"), "/organization.html?organization=org&view=ledger&topic=topic");
  const url = knowledge.knowledgeURL("weird&organization=other", "journal");
  assert.equal(new URL(url, "https://example.org").searchParams.get("organization"), "weird&organization=other");
  const html = renderToStaticMarkup(React.createElement(knowledge.KnowledgeLinks, { organizationId: "org" }));
  assert.match(html, /Open ledger/); assert.match(html, /Open research blog/);
  assert.match(html, /href="\/blog\/org"/);
  assert.doesNotMatch(html, /view=journal/);
  assert.doesNotMatch(html, /target=|token|api_key/);
});

test("collapsed run cards do not mount expensive progress readers", () => {
  const html = renderToStaticMarkup(React.createElement(activity.ActivityCard, {
    title: "<script>Fixture</script>", status: "outcome_unknown", createdAt: 1, initiallyOpen: false,
  }, React.createElement("p", null, "Progress reader")));
  assert.match(html, /&lt;script&gt;/); assert.match(html, /outcome unknown/); assert.doesNotMatch(html, /Progress reader|<script>/);
});

test("topic tones remain in navigation and the top bar, not individual chat entries", async () => {
  const css = await readFile(new URL("../src/organization.css", import.meta.url), "utf8");
  const rules = [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)];
  assert.ok(rules.some(([, selector, style]) => selector.includes(".org-topic-color") && style.includes("--org-tree-tone")));
  for (const [, selector, style] of rules) {
    if (/\.org-(?:message|run-compact)\b/.test(selector)) assert.doesNotMatch(style, /--org-tree-tone/);
  }
});
