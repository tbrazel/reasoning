import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

let server;
let AgentModelPicker;
let addRecentOpenRouterModel;
let parseRecentOpenRouterModels;
let recentKey;
const previousWindow = Object.getOwnPropertyDescriptor(globalThis, "window");

const stored = new Map();

before(async () => {
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: {
      localStorage: {
        getItem: (key) => stored.get(key) ?? null,
        setItem: (key, value) => stored.set(key, value),
      },
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      dispatchEvent: () => true,
    },
  });
  server = await createServer({
    appType: "custom",
    logLevel: "silent",
    server: { middlewareMode: true },
  });
  const picker = await server.ssrLoadModule("/src/AgentModelPicker.tsx");
  ({ AgentModelPicker, addRecentOpenRouterModel, parseRecentOpenRouterModels } = picker);
  recentKey = picker.RECENT_OPENROUTER_MODELS_KEY;
});

after(async () => {
  await server?.close();
  if (previousWindow) Object.defineProperty(globalThis, "window", previousWindow);
  else delete globalThis.window;
});

test("normalizes and bounds remembered OpenRouter models", () => {
  const values = Array.from({ length: 15 }, (_, index) => `provider/model-${index}`);
  assert.deepEqual(
    parseRecentOpenRouterModels(JSON.stringify([" provider/model ", "provider/model", "", 42])),
    ["provider/model"],
  );
  assert.deepEqual(addRecentOpenRouterModel(values, "provider/model-4"), [
    "provider/model-4",
    "provider/model-0",
    "provider/model-1",
    "provider/model-2",
    "provider/model-3",
    "provider/model-5",
    "provider/model-6",
    "provider/model-7",
    "provider/model-8",
    "provider/model-9",
    "provider/model-10",
    "provider/model-11",
  ]);
  assert.deepEqual(parseRecentOpenRouterModels("not json"), []);
});

test("puts catalog discovery and remembered models in the dropdown", () => {
  stored.set(recentKey, JSON.stringify(["stealth/ox-alpha"]));
  const html = renderToStaticMarkup(
    React.createElement(AgentModelPicker, {
      sources: [
        {
          executorId: "codex",
          label: "Codex CLI",
          provider: "OpenAI",
          models: ["gpt-5.6-sol"],
          ready: true,
          readiness: "ready",
        },
        {
          executorId: "reasoning-native",
          label: "Reasoning Native",
          provider: "OpenRouter",
          models: ["openrouter/auto"],
          ready: true,
          readiness: "ready",
        },
      ],
      executorId: "reasoning-native",
      model: "stealth/ox-alpha",
      onChange: () => undefined,
    }),
  );

  assert.match(html, /Recent · OpenRouter via Reasoning Native/);
  assert.match(html, /stealth\/ox-alpha · recent/);
  assert.match(html, /OpenRouter · Reasoning Native/);
  assert.match(html, /Other model…/);
  assert.match(html, /Custom model ID…/);
  assert.doesNotMatch(html, /price-picker-trigger/);
});
