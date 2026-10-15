import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { createServer } from "vite";

let server;
let nearestPdfPage;

before(async () => {
  server = await createServer({
    appType: "custom",
    logLevel: "silent",
    server: { middlewareMode: true },
  });
  ({ nearestPdfPage } = await server.ssrLoadModule("/src/pdfNavigation.ts"));
});

after(async () => {
  await server?.close();
});

test("tracks the page at the reader position during continuous scrolling", () => {
  const pages = [
    { pageNumber: 1, top: 0, bottom: 900 },
    { pageNumber: 2, top: 916, bottom: 1816 },
    { pageNumber: 3, top: 1832, bottom: 2732 },
  ];

  assert.equal(nearestPdfPage(pages, 600), 1);
  assert.equal(nearestPdfPage(pages, 910), 2);
  assert.equal(nearestPdfPage(pages, 1200), 2);
  assert.equal(nearestPdfPage(pages, 2200), 3);
});

test("defaults safely before page geometry is available", () => {
  assert.equal(nearestPdfPage([], 100), 1);
});
