import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

let server;
let ComparisonStudio;
let isAgenticHarnessOnly;
let workflowReasoningBySubject;

before(async () => {
  server = await createServer({
    appType: "custom",
    logLevel: "silent",
    server: { middlewareMode: true },
  });
  ({
    ComparisonStudio,
    isAgenticHarnessOnly,
    workflowReasoningBySubject,
  } = await server.ssrLoadModule("/src/WorkflowStudio.tsx"));
});

after(async () => {
  await server?.close();
});

const config = (model) => ({
  executor_id: "reasoning-native",
  model,
  model_provider: null,
  system_prompt: { prompt_id: "role", revision_id: "role-1" },
  user_prompt: { prompt_id: "question", revision_id: "question-1" },
  skills: [],
  tools: [],
  mcp_connections: [],
  context_policy: { mode: "none", max_tokens: 8000 },
  budget: { max_cost_usd: 1, max_tool_calls: 12, timeout_seconds: 900 },
  sandbox: { mode: "read_only", network: false },
  session: { mode: "fresh" },
});

const graph = {
  workflow_id: "compare-models",
  revision_id: "wfrev_test",
  parent_revision_id: null,
  name: "Compare models",
  description: "Compare",
  workflow_type: "model_comparison",
  execution_policy: { max_concurrency: 4, failure_policy: "continue" },
  iterations: 1,
  created_at: "2026-01-01T00:00:00Z",
  nodes: [
    { id: "question", kind: "input", name: "Question", position: { x: 0, y: 0 } },
    { id: "a", kind: "agent", name: "Model A", position: { x: 1, y: 0 }, config: config("provider/model-a") },
    { id: "b", kind: "agent", name: "Model B", position: { x: 1, y: 1 }, config: config("provider/model-b") },
    { id: "responses", kind: "output", name: "Responses", position: { x: 2, y: 0 } },
  ],
  edges: [],
};

const catalog = {
  executors: [{
    executor_id: "reasoning-native",
    display_name: "Reasoning Native",
    model_options: ["provider/model-a", "provider/model-b"],
    prompt_fidelity: "wire_exact",
  }],
  connections: [{
    executor_id: "reasoning-native",
    provider: "OpenRouter",
    ready: true,
    readiness: "ready",
  }],
  prompts: [],
  skills: [],
  tools: [],
  mcpConnections: [],
};

const callbacks = {
  onInitialPrompt: () => undefined,
  onRun: () => undefined,
  onCancel: () => undefined,
  onSelectRun: () => undefined,
  onPrepareAnother: () => undefined,
  onChangeAgent: () => undefined,
  onAddAgent: () => undefined,
  onRemoveAgent: () => undefined,
  onSetConfigurations: () => undefined,
  onEditGraph: () => undefined,
};

test("presents one question and an editable model roster", () => {
  const html = renderToStaticMarkup(React.createElement(ComparisonStudio, {
    graph,
    catalog,
    projectId: "prj_test",
    projectName: "Research project",
    initialPrompt: "What is the obstruction?",
    run: null,
    history: [],
    ...callbacks,
  }));

  assert.match(html, /identical input for every model/);
  assert.match(html, /What is the obstruction\?/);
  assert.match(html, /provider\/model-a/);
  assert.match(html, /provider\/model-b/);
  assert.match(html, /branches run concurrently/);
  assert.match(html, /compare models/);
  assert.match(html, /edit graph/);
  assert.match(html, /Observed recommendations/);
  assert.match(html, /tested executor, provider, model, and reasoning effort/);
});

test("recognizes catalog-advertised agentic-harness restrictions narrowly", () => {
  assert.equal(
    isAgenticHarnessOnly(
      "The free endpoint is only available for use with agentic harnesses.",
    ),
    true,
  );
  assert.equal(
    isAgenticHarnessOnly("A model suited for agentic workflows and direct API use."),
    false,
  );
});

test("keeps successful and failed sibling results visible with provenance", () => {
  const run = {
    run_id: "wfrun_test",
    workflow_id: "compare-models",
    workflow_revision_id: "wfrev_test",
    status: "partial",
    iterations: 1,
    events: [
      { type: "node.completed", node_id: "a" },
      { type: "node.completed", node_id: "b" },
      {
        type: "harness.event",
        node_id: "a",
        harness_event: {
          session_id: "wfrun_test",
          turn_id: "run-a",
          sequence: 3,
          kind: "reasoning.delta",
          at: "2026-01-01T00:00:03Z",
          data: { text: "Checked the obstruction before answering." },
        },
      },
    ],
    outputs: { nodes: [
      {
        iteration: 1,
        node_id: "a",
        executor_id: "reasoning-native",
        model: "provider/model-a",
        agent_run_id: "run-a",
        status: "completed",
        text: "A useful answer.",
        usage: { total_tokens: 120 },
        resolved_model: "provider/model-a-versioned",
        serving_provider: "Provider Route",
        cost: { amount_usd: 0.003, basis: "provider_reported", source_field: "cost_usd" },
        duration_seconds: 1.25,
        prompt_fidelity: "wire_exact",
        finish_reason: "stop",
      },
      {
        iteration: 1,
        node_id: "b",
        executor_id: "reasoning-native",
        model: "provider/model-b",
        agent_run_id: "run-b",
        status: "failed",
        text: "",
        usage: { completion_tokens_details: { reasoning_tokens: 42 } },
        error: "provider unavailable",
        duration_seconds: 0.4,
        prompt_fidelity: "wire_exact",
        finish_reason: "error",
      },
    ] },
    cost: { total_usd: 0.003 },
  };
  const html = renderToStaticMarkup(React.createElement(ComparisonStudio, {
    graph,
    catalog,
    projectId: "prj_test",
    projectName: "Research project",
    initialPrompt: "What is the obstruction?",
    run,
    history: [],
    ...callbacks,
  }));

  assert.match(html, /partial/);
  assert.match(html, /A useful answer/);
  assert.match(html, /provider unavailable/);
  assert.match(html, /\$0\.003000/);
  assert.match(html, /Provider Route/);
  assert.match(html, /provider\/model-a-versioned/);
  assert.match(html, /42/);
  assert.match(html, /failed before usage/);
  assert.match(html, /wire exact/);
  assert.match(html, /provider reported/);
  assert.match(html, /provider reasoning/);
  assert.match(html, /Checked the obstruction before answering/);
});

test("collects bounded provider reasoning by workflow subject", () => {
  const run = {
    events: [
      {
        type: "harness.event",
        node_id: "solver",
        variant_id: "variant-a",
        harness_event: {
          session_id: "workflow-run",
          turn_id: "agent-run",
          sequence: 1,
          kind: "reasoning.delta",
          at: "2026-01-01T00:00:01Z",
          data: { text: "first " },
        },
      },
      {
        type: "harness.event",
        node_id: "solver",
        variant_id: "variant-a",
        harness_event: {
          session_id: "workflow-run",
          turn_id: "agent-run",
          sequence: 2,
          kind: "reasoning.delta",
          at: "2026-01-01T00:00:02Z",
          data: { text: "second" },
        },
      },
      {
        type: "harness.event",
        node_id: "solver",
        variant_id: "variant-a",
        harness_event: {
          session_id: "workflow-run",
          turn_id: "agent-run",
          sequence: 3,
          kind: "tool.completed",
          at: "2026-01-01T00:00:03Z",
          data: { name: "artifact_write", ok: true },
        },
      },
    ],
  };

  assert.equal(workflowReasoningBySubject(run).get("variant-a"), "first second");
  assert.equal(workflowReasoningBySubject(run, 6).get("variant-a"), "second");
});
