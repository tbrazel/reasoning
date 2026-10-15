import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

let server;
let RunConfigurationControls;

before(async () => {
  server = await createServer({
    appType: "custom",
    logLevel: "silent",
    server: { middlewareMode: true },
  });
  ({ RunConfigurationControls } = await server.ssrLoadModule(
    "/src/RunConfigurationControls.tsx",
  ));
});

after(async () => {
  await server?.close();
});

test("renders harness, route, model, target, reasoning, and live preflight separately", () => {
  const html = renderToStaticMarkup(
    React.createElement(RunConfigurationControls, {
      options: {
        mode: "research",
        harnesses: [
          {
            harness_id: "reasoning-native",
            display_name: "Reasoning Native",
            model_options: [],
            reasoning_efforts: ["low", "high"],
          },
          {
            harness_id: "codex",
            display_name: "Codex app-server",
            model_options: [],
            reasoning_efforts: ["low", "high"],
          },
        ],
        inference_routes: [
          {
            route_id: "openrouter",
            display_name: "OpenRouter",
            harness_ids: ["reasoning-native"],
            model_options: ["openrouter/auto"],
            reasoning_efforts: ["low", "high"],
            ready: true,
          },
          {
            route_id: "codex-openai",
            display_name: "OpenAI via Codex",
            harness_ids: ["codex"],
            model_options: ["gpt-5.6-sol"],
            reasoning_efforts: ["low", "high"],
            ready: true,
          },
        ],
        execution_targets: [
          {
            target_id: "project-tools",
            display_name: "Mediated project tools",
          },
        ],
        session_binding: null,
      },
      selection: {
        harness_id: "reasoning-native",
        inference_route_id: "openrouter",
        target_id: "project-tools",
        model: "openrouter/auto",
        reasoning_effort: "high",
      },
      preflight: { compatible: true, issues: [] },
      disabled: false,
      onChange: () => undefined,
    }),
  );

  assert.match(html, />Harness</);
  assert.match(html, />Provider route</);
  assert.match(html, />Model</);
  assert.match(html, />Target</);
  assert.match(html, />Reasoning</);
  assert.match(html, /compatible/);
  assert.match(html, /value="codex-openai" disabled=""/);
  assert.match(html, /value="high" selected=""/);
});

test("locks persistent session identity but keeps reasoning adjustable", () => {
  const issue = "This persistent chat is pinned to model openrouter/auto.";
  const html = renderToStaticMarkup(
    React.createElement(RunConfigurationControls, {
      options: {
        mode: "research",
        harnesses: [{
          harness_id: "reasoning-native",
          display_name: "Reasoning Native",
          model_options: ["openrouter/auto"],
          reasoning_efforts: ["low", "high"],
        }],
        inference_routes: [{
          route_id: "openrouter",
          display_name: "OpenRouter",
          harness_ids: ["reasoning-native"],
          model_options: ["openrouter/auto"],
          reasoning_efforts: ["low", "high"],
          ready: true,
        }],
        execution_targets: [{
          target_id: "project-tools",
          display_name: "Mediated project tools",
        }],
        session_binding: {
          harness_id: "reasoning-native",
          provider: "openrouter",
          target_id: "project-tools",
          model: "openrouter/auto",
        },
      },
      selection: {
        harness_id: "reasoning-native",
        inference_route_id: "openrouter",
        target_id: "project-tools",
        model: "openrouter/auto",
        reasoning_effort: "low",
      },
      preflight: {
        compatible: false,
        issues: [{ code: "session_model", scope: "route", detail: issue }],
      },
      disabled: false,
      onChange: () => undefined,
    }),
  );

  assert.match(html, /aria-label="Harness"[^>]*disabled=""/);
  assert.match(html, /aria-label="Model"[^>]*disabled=""/);
  assert.match(html, /aria-label="Execution target"[^>]*disabled=""/);
  assert.doesNotMatch(html, /aria-label="Reasoning effort"[^>]*disabled=""/);
  assert.match(html, new RegExp(issue.replaceAll("/", "\\/")));
});
