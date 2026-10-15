import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ReactMarkdown, { defaultUrlTransform } from "react-markdown";
import { createServer } from "vite";

let server;
let artifactNameFromHref;
let artifactNameFromPath;
let remarkArtifactLinks;

before(async () => {
  server = await createServer({
    appType: "custom",
    logLevel: "silent",
    server: { middlewareMode: true },
  });
  ({ artifactNameFromHref, artifactNameFromPath, remarkArtifactLinks } =
    await server.ssrLoadModule("/src/artifactLinks.ts"));
});

after(async () => {
  await server?.close();
});

function render(text) {
  return renderToStaticMarkup(
    React.createElement(
      ReactMarkdown,
      {
        remarkPlugins: [remarkArtifactLinks],
        urlTransform: (url) => artifactNameFromPath(url) ? url : defaultUrlTransform(url),
        components: {
          a: ({ href, children }) => React.createElement(
            "a",
            { href, "data-artifact": artifactNameFromHref(href ?? "") ?? "" },
            children,
          ),
        },
      },
      text,
    ),
  );
}

test("keeps Codex absolute artifact destinations intact", () => {
  const target = "/Users/benjaminantieau/Library/Application Support/" +
    "reasoning-desktop/workbench/hodge-numbers-fourfolds/artifacts/" +
    "derived-signature-fourfolds/derived-signature-fourfolds.pdf";
  const html = render(`[PDF](<${target}>)`);

  assert.match(
    html,
    /data-artifact="derived-signature-fourfolds\/derived-signature-fourfolds\.pdf"/,
  );
  assert.match(html, /href="\/Users\/benjaminantieau\/Library\/Application%20Support\//);
  assert.doesNotMatch(html, /#art-derived-signature/);
  assert.equal((html.match(/<a /g) ?? []).length, 1);
});

test("links bare artifact paths only in ordinary prose", () => {
  const html = render("Open artifacts/notes/audit.md, not `artifacts/private.txt`.");

  assert.match(html, /data-artifact="notes\/audit\.md"/);
  assert.match(html, /<code>artifacts\/private\.txt<\/code>/);
  assert.equal((html.match(/<a /g) ?? []).length, 1);
});

test("links artifact directories and preserves their trailing slash", () => {
  const html = render("Browse artifacts/derived-signature-fourfolds/ for all outputs.");

  assert.match(html, /data-artifact="derived-signature-fourfolds\/"/);
  assert.match(html, />derived-signature-fourfolds<\/a>/);
  assert.equal((html.match(/<a /g) ?? []).length, 1);
});

test("recognizes only explicit project-artifact boundaries", () => {
  assert.equal(
    artifactNameFromPath("sandbox:/tmp/project/artifacts/report.pdf"),
    "report.pdf",
  );
  assert.equal(artifactNameFromPath("sandbox:/mnt/data/report.pdf"), null);
  assert.equal(artifactNameFromPath("https://example.com/artifacts/report.pdf"), null);
});

test("routes a Codex sandbox artifact target without exposing other sandbox links", () => {
  const html = render(
    "[shared](sandbox:/tmp/project/artifacts/report.pdf) " +
    "[scratch](sandbox:/mnt/data/report.pdf)",
  );

  assert.match(html, /href="sandbox:\/tmp\/project\/artifacts\/report\.pdf"/);
  assert.match(html, /data-artifact="report\.pdf"/);
  assert.match(html, /<a href="" data-artifact="">scratch<\/a>/);
});
