import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

let server;
let UxLab;
let UX_DIRECTIONS;
let parseUxLabState;
let isUxLabPath;

before(async () => {
  server = await createServer({
    appType: "custom",
    logLevel: "silent",
    server: { middlewareMode: true },
  });
  ({ UxLab, UX_DIRECTIONS, parseUxLabState } = await server.ssrLoadModule("/src/UxLab.tsx"));
  ({ isUxLabPath } = await server.ssrLoadModule("/src/uxLabRoute.ts"));
});

after(async () => server?.close());

test("recognizes only the explicit internal UX Lab route", () => {
  assert.equal(isUxLabPath("/ux-lab"), true);
  assert.equal(isUxLabPath("/ux-lab/"), true);
  assert.equal(isUxLabPath("/"), false);
  assert.equal(isUxLabPath("/project/ux-lab"), false);
});

test("parses stable shareable direction and viewport state", () => {
  assert.deepEqual(parseUxLabState("?direction=canvas&viewport=web"), {
    direction: "canvas",
    viewport: "web",
  });
  assert.deepEqual(parseUxLabState("?direction=unknown&viewport=huge"), {
    direction: "native",
    viewport: "desktop",
  });
});

test("renders five complete synthetic design directions without project loading", () => {
  assert.equal(UX_DIRECTIONS.length, 5);
  const html = renderToStaticMarkup(React.createElement(UxLab));
  for (const label of ["Quiet native", "Research studio", "Floating canvas", "Command first", "Editorial workspace"]) {
    assert.match(html, new RegExp(label));
  }
  assert.match(html, /Spectral sequence intuition/);
  assert.match(html, /Money/);
  assert.match(html, /Desktop/);
  assert.match(html, />Web</);
  assert.doesNotMatch(html, /Restoring projects/);
});
