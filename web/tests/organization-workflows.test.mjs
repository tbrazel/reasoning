import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { after, before, test } from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

let server, api, components;
const org = "a".repeat(32), topic = "b".repeat(32), user = "c".repeat(32), workflow = "d".repeat(32);
before(async () => {
  server = await createServer({ appType: "custom", logLevel: "silent", server: { middlewareMode: true } });
  api = await server.ssrLoadModule("/src/workflow-api.ts");
  components = await server.ssrLoadModule("/src/WorkflowConversation.tsx");
});
after(async () => { await server?.close(); });
const source = () => JSON.stringify(api.WORKFLOW_EXAMPLE);
const attempt = () => api.captureWorkflowAttempt(source(), "workflow-request-001");
const receipt = (captured) => ({ organization_id: org, topic_id: topic, workflow_id: workflow, version: (captured.expected_version ?? 0) + 1,
  definition: captured.definition, created_by: user, created_at: 1, client_id: captured.client_id, node_order: ["review", "reason"],
  definition_digest: "sha256:" + createHash("sha256").update(api.canonicalWorkflow(captured.definition)).digest("hex") });

test("gamma comparisons preserve only explicitly selected supported effort levels", () => {
  const catalog = { models: [{ id: "example/one", reasoning_efforts: ["high"] }, { id: "example/two", reasoning_efforts: [] }] };
  const value = components.comparisonDefinition("Question", ["example/one", "example/two"], catalog,
    { "example/one": "high", "example/two": "high" });
  assert.equal(value.nodes[0].parameters.effort, "high");
  assert.equal(value.nodes[1].parameters.effort, undefined);
  assert.equal(components.comparisonDefinition("Question", ["example/one", "example/two"], catalog).nodes[0].parameters.effort, undefined);
});

test("capture normalizes dependencies and IDs and freezes the whole request", () => {
  const captured = attempt();
  assert.deepEqual(captured.definition.nodes.map((node) => node.id), ["reason", "review"]);
  assert.ok(Object.isFrozen(captured) && Object.isFrozen(captured.definition.nodes[0].parameters));
  assert.throws(() => { captured.definition.nodes[0].parameters.model = "changed"; });
  assert.throws(() => api.captureWorkflowAttempt(source(), "short"), /stable/);
  assert.throws(() => api.captureWorkflowAttempt("{}", "workflow-request-001"), /format-1/);
  const value = JSON.parse(source()); value.nodes[0].depends_on = [true];
  assert.throws(() => api.captureWorkflowAttempt(JSON.stringify(value), "workflow-request-001"), /depends_on/);
  const revised = api.captureWorkflowAttempt(source(), "workflow-request-002", receipt(captured));
  assert.equal(revised.workflow_id, workflow); assert.equal(revised.expected_version, 1);
  assert.throws(() => api.captureWorkflowAttempt(source(), "workflow-request-002", { workflow_id: workflow, version: 128 }), /cannot be revised/);
});

test("canonical bytes preserve Unicode, literal escape text, and JSON byte limits", () => {
  assert.equal(api.canonicalWorkflow({ z: "😀", a: [1, "line\n"] }), '{"a":[1,"line\\n"],"z":"😀"}');
  assert.equal(api.canonicalWorkflow("\\ud800"), '"\\\\ud800"');
  for (const invalid of ["\ud800", "\udfff", "x\ud800y", "\ud800\ud800"]) assert.throws(() => api.canonicalWorkflow(invalid), /Unicode/);
  for (const invalid of [undefined, NaN, Infinity]) assert.throws(() => api.canonicalWorkflow(invalid), /JSON/);
  const value = JSON.parse(source()); value.nodes[1].parameters.prompt = "😀".repeat(16000);
  assert.throws(() => api.captureWorkflowAttempt(JSON.stringify(value), "workflow-request-001"), /32 KiB/);
});

test("lost reply retries preserve the original body, credentials, and CSRF", async () => {
  const oldFetch = globalThis.fetch, oldDocument = Object.getOwnPropertyDescriptor(globalThis, "document");
  const calls = [], captured = attempt();
  Object.defineProperty(globalThis, "document", { configurable: true, value: { cookie: "__Host-reasoning_csrf=same-csrf" } });
  globalThis.fetch = async (url, options) => { calls.push({ url, ...options });
    if (calls.length === 1) throw new Error("Lost reply");
    return new Response(JSON.stringify(receipt(captured)), { status: 201, headers: { "content-type": "application/json" } });
  };
  try {
    await assert.rejects(api.deliverWorkflowAttempt(org, topic, user, captured), /Lost reply/);
    assert.deepEqual(await api.deliverWorkflowAttempt(org, topic, user, captured), receipt(captured));
    assert.equal(calls[0].body, calls[1].body); assert.equal(calls[0].credentials, "same-origin");
    assert.equal(calls[0].headers["X-CSRF-Token"], "same-csrf");
    assert.equal(calls[0].url, `/api/organizations/${org}/topics/${topic}/workflows`);
    const revised = api.captureWorkflowAttempt(source(), "workflow-request-002", receipt(captured));
    globalThis.fetch = async (url, options) => { calls.push({ url, ...options }); return new Response(JSON.stringify(receipt(revised)), { status: 201 }); };
    await api.deliverWorkflowAttempt(org, topic, user, revised);
    assert.equal(calls[2].url, `/api/organizations/${org}/workflows/${workflow}/versions`);
    assert.equal(JSON.parse(calls[2].body).expected_version, 1); assert.equal(JSON.parse(calls[2].body).workflow_id, undefined);
  } finally { globalThis.fetch = oldFetch;
    if (oldDocument) Object.defineProperty(globalThis, "document", oldDocument); else delete globalThis.document;
  }
});

test("receipts must bind requester, topic, organization, original client ID, version, definition, and digest", async () => {
  const oldFetch = globalThis.fetch, oldDocument = Object.getOwnPropertyDescriptor(globalThis, "document");
  Object.defineProperty(globalThis, "document", { configurable: true, value: { cookie: "" } });
  const captured = attempt(), original = receipt(captured);
  try {
    for (const changed of [
      { organization_id: "other" }, { topic_id: "other" }, { created_by: "other" }, { client_id: "other" },
      { version: 2 }, { definition: { ...captured.definition, name: "other" } }, { definition_digest: "sha256:" + "0".repeat(64) }, { workflow_id: "invalid" },
    ]) {
      globalThis.fetch = async () => new Response(JSON.stringify({ ...original, ...changed }), { status: 201 });
      await assert.rejects(api.deliverWorkflowAttempt(org, topic, user, captured), /does not match/);
    }
  } finally { globalThis.fetch = oldFetch;
    if (oldDocument) Object.defineProperty(globalThis, "document", oldDocument); else delete globalThis.document;
  }
});

test("workflow composer keeps definitions and execution receipts separate without list dashboards", () => {
  const props = { organizationId: org, topicId: topic, userId: user, canWrite: true, canManage: false,
    onAttempt: () => {}, onExecutionAttempt: () => {}, onAuthLost: () => {} };
  const markup = renderToStaticMarkup(React.createElement(components.WorkflowConversation, props));
  assert.match(markup, /Loading workflow configuration/);
  assert.doesNotMatch(markup, /org-workflow-list|org-workflow-run-list|Find a run by ID|No workflows yet/);
  const readonly = renderToStaticMarkup(React.createElement(components.WorkflowConversation, { ...props, canWrite: false }));
  assert.doesNotMatch(readonly, /Compare models|New workflow|New from JSON|Save version/);
  const pending = renderToStaticMarkup(React.createElement(components.WorkflowConversation, { ...props, attempt: attempt() }));
  assert.match(pending, /workflow-request-001/); assert.match(pending, /Retry unchanged definition/); assert.match(pending, /exact same request/);
  assert.doesNotMatch(pending, /localStorage|sessionStorage|api_key/);
});

test("version inspection escapes source and exposes immutable evidence and dependency order", () => {
  const value = receipt(attempt()); value.definition = structuredClone(value.definition);
  value.definition.name = "<script>untrusted</script>";
  const markup = renderToStaticMarkup(React.createElement(components.WorkflowVersionDetail, { version: value }));
  assert.match(markup, /&lt;script&gt;untrusted&lt;\/script&gt;/); assert.doesNotMatch(markup, /<script>/);
  assert.match(markup, /Technical details/); assert.match(markup, /sha256:/); assert.match(markup, /Approval/); assert.match(markup, /AI/);
});

test("guided comparison and builder produce valid bounded workflow definitions", () => {
  const comparison = components.comparisonDefinition("  Compare these proofs.  ", ["model/a", "model/b"]);
  const multiline = components.comparisonDefinition("First line\nSecond line", ["model/a", "model/b"]);
  assert.equal(multiline.name, "First line Second line");
  assert.equal(multiline.nodes[0].parameters.prompt, "First line\nSecond line");
  assert.equal(comparison.name, "Compare these proofs."); assert.deepEqual(comparison.nodes.map((node) => node.depends_on), [[], []]);
  assert.deepEqual(comparison.nodes.map((node) => node.parameters.model), ["model/a", "model/b"]);
  assert.equal(comparison.nodes[0].parameters.max_completion_tokens, null);
  assert.equal(components.comparisonDefinition("Question", Array.from({ length: 8 }, (_, index) => `model/${index}`)).nodes.length, 8);
  assert.throws(() => components.comparisonDefinition("Question", Array.from({ length: 9 }, (_, index) => `model/${index}`)), /two to eight/);
  assert.throws(() => components.comparisonDefinition("Question", ["model/a", "model/a"]), /different models/);
  const limits = { cpu: 2, memory_bytes: 2147483648, disk_bytes: 536870912, pids: 256, wall_time_seconds: 300, artifact_bytes: 1048576 };
  const compute = { policy: { allowed_images: ["research"], monthly_cpu_seconds: 100, max_active_jobs: 1 },
    images: [{ id: "research", name: "Research", languages: ["python"], maximum_resources: limits }], balance: {} };
  const blank = { model: "", prompt: "", message: "", requiredRole: "member", imageId: "", language: "", program: "" };
  const built = components.builderDefinition("Research pass", [
    { ...blank, kind: "llm", model: "model/a", prompt: "Draft" },
    { ...blank, kind: "approval", message: "Review the draft", requiredRole: "admin" },
    { ...blank, kind: "compute", imageId: "research", language: "python", program: "print(1)" },
  ], compute);
  assert.deepEqual(built.nodes.map((node) => node.depends_on), [[], ["step_1"], ["step_2"]]);
  assert.deepEqual(built.nodes[2].inputs, { previous: { node: "step_2", output: "decision" } });
});
