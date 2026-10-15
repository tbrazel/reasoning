import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

let server;
let PlanStudio;
let WorkflowOverviewStudio;
let workflowRecipe;

before(async () => {
  server = await createServer({
    appType: "custom",
    logLevel: "silent",
    server: { middlewareMode: true },
  });
  ({ PlanStudio, WorkflowOverviewStudio, workflowRecipe } = await server.ssrLoadModule("/src/WorkflowStudio.tsx"));
});

after(async () => {
  await server?.close();
});

test("exposes the common cases × variants × graph × evaluators contract", () => {
  const graph = {
    workflow_id: "generic-sweep",
    revision_id: "wfrev_test",
    parent_revision_id: null,
    name: "Generic sweep",
    description: "Sweep",
    schema_version: 2,
    presentation: { recipe: "graph" },
    execution_policy: {
      max_concurrency: 4,
      failure_policy: "continue",
      max_calls: 4,
      max_total_cost_usd: 0.5,
    },
    iterations: 2,
    created_at: "2026-01-01T00:00:00Z",
    dataset: {
      kind: "case_dataset_v1",
      schema_version: 1,
      name: "Two cases",
      description: "Hidden labels",
      cases: [
        { case_id: "a", name: "A", inputs: { text: "A" }, expected: "yes" },
        { case_id: "b", name: "B", inputs: { text: "B" }, expected: "no" },
      ],
    },
    variants: [
      { variant_id: "small", name: "Small", node_overrides: {} },
      { variant_id: "large", name: "Large", node_overrides: {} },
    ],
    evaluators: [{ evaluator_id: "exact", kind: "exact_match_v1", config: {} }],
    nodes: [{
      id: "agent",
      kind: "agent",
      name: "Agent",
      position: { x: 0, y: 0 },
      config: {
        executor_id: "reasoning-native",
        model: "provider/model",
        skills: [],
        tools: [],
        mcp_connections: [],
        context_policy: { mode: "none", max_tokens: 8000 },
        budget: { max_cost_usd: 1 },
        sandbox: { mode: "read_only" },
      },
    }],
    edges: [],
  };

  const html = renderToStaticMarkup(React.createElement(PlanStudio, {
    graph,
    projectId: "project",
    disabled: false,
    onChange: () => undefined,
  }));

  assert.match(html, /cases × variants run through a graph of small harnesses/);
  assert.match(html, /model judge is just another downstream harness/);
  assert.match(html, /<strong>4<\/strong><span>scheduled calls/);
  assert.match(html, /Expected values are never exposed to agents/);
  assert.match(html, /Exact match/);
  assert.match(html, /run cost limit \(USD\)/);
  assert.match(html, /value="0.5"/);
});

test("uses the presentation recipe as the focused-studio contract", () => {
  assert.equal(workflowRecipe({ presentation: { recipe: "model_comparison" } }), "model_comparison");
  assert.equal(workflowRecipe({ presentation: { recipe: "paper_moderation" }, workflow_type: "graph" }), "paper_moderation");
  assert.equal(workflowRecipe({ presentation: { recipe: "graph" }, workflow_type: "model_benchmark" }), "paper_moderation");
});

test("renders an arbitrary authored graph as a readable immutable overview", () => {
  const graph = {
    workflow_id: "review-papers",
    revision_id: "wfrev_overview_test",
    parent_revision_id: null,
    name: "Review incoming papers",
    description: "Screen each submission, then preserve the moderation decision.",
    schema_version: 2,
    presentation: { recipe: "graph" },
    execution_policy: { max_concurrency: 2, failure_policy: "continue", max_calls: 8 },
    iterations: 2,
    created_at: "2026-01-01T00:00:00Z",
    variants: [{ variant_id: "default", name: "Default", node_overrides: {} }],
    evaluators: [{ evaluator_id: "moderation", kind: "paper_moderation_v1", config: {} }],
    nodes: [
      { id: "paper", kind: "input", name: "Submission", position: { x: 0, y: 0 } },
      {
        id: "moderator",
        kind: "agent",
        name: "Safety moderator",
        position: { x: 1, y: 0 },
        config: {
          executor_id: "reasoning-native",
          model: "provider/fast-model",
          model_provider: null,
          effort: "low",
          system_prompt: { prompt_id: "moderation-role", revision_id: "prmrev_role_exact" },
          user_prompt: { prompt_id: "moderation-case", revision_id: "prmrev_case_exact" },
          skills: [],
          tools: [],
          mcp_connections: [],
          context_policy: { mode: "predecessors", max_tokens: 8000 },
          budget: { max_cost_usd: 0.01, max_tool_calls: 0 },
          sandbox: { mode: "read_only", network: false },
          session: { mode: "fresh" },
        },
      },
      { id: "decision", kind: "output", name: "Decision", position: { x: 2, y: 0 } },
    ],
    edges: [
      { id: "a", source: "paper", target: "moderator" },
      { id: "b", source: "moderator", target: "decision" },
    ],
  };

  const html = renderToStaticMarkup(React.createElement(WorkflowOverviewStudio, {
    graph,
    projectName: "Moderation",
    onEditGraph: () => undefined,
    onReviewRun: () => undefined,
  }));

  assert.match(html, /What this workflow will do/);
  assert.match(html, /Organization and exact instructions/);
  assert.match(html, /Screen each submission/);
  assert.match(html, /provider\/fast-model/);
  assert.match(html, /reasoning low/);
  assert.match(html, /prmrev_role_exa/);
  assert.match(html, /paper moderation scorecard/);
  assert.match(html, /review run/);
});
