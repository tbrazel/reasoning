import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { createServer } from "vite";

let server;
let estimateOpenRouterTokenCost;

before(async () => {
  server = await createServer({
    appType: "custom",
    logLevel: "silent",
    server: { middlewareMode: true },
  });
  ({ estimateOpenRouterTokenCost } = await server.ssrLoadModule(
    "/src/costEstimate.ts",
  ));
});

after(async () => {
  await server?.close();
});

const price = {
  input_mtok_usd: 2,
  output_mtok_usd: 8,
  cache_read_mtok_usd: 0.5,
};

test("estimates Codex token spend using the selected OpenRouter model", () => {
  const value = estimateOpenRouterTokenCost(
    { input: 1_000_000, cached: 400_000, output: 100_000 },
    price,
  );
  assert.equal(value, 2.2);
});

test("falls back to the input rate when no cache-read rate is published", () => {
  const value = estimateOpenRouterTokenCost(
    { input: 1_000, cached: 1_000, output: 0 },
    { ...price, cache_read_mtok_usd: null },
  );
  assert.equal(value, 0.002);
});

test("does not imply a cost when the catalog lacks token rates", () => {
  assert.equal(
    estimateOpenRouterTokenCost(
      { input: 1_000, cached: 0, output: 100 },
      { ...price, output_mtok_usd: null },
    ),
    null,
  );
});
