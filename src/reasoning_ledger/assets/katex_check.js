// KaTeX renderability checker. Reads a JSON array of {span, display} on
// stdin and writes a JSON array of {ok, error} — one per input, in order.
// `error` is KaTeX's message for the first parse failure, or null.
//
// Invoked by latex_check.py; katex.min.js sits beside this file (both
// vendored into the package, so this runs in the sandbox where only the
// package source and the system Node are visible).
"use strict";

const path = require("path");
const katex = require(path.join(__dirname, "katex.min.js"));

let input = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  input += chunk;
});
process.stdin.on("end", () => {
  let spans;
  try {
    spans = JSON.parse(input);
  } catch (e) {
    process.stdout.write(JSON.stringify({ fatal: String(e) }));
    return;
  }
  const results = spans.map((s) => {
    try {
      katex.renderToString(String(s.span), {
        displayMode: !!s.display,
        throwOnError: true,
        strict: false,
      });
      return { ok: true, error: null };
    } catch (e) {
      return { ok: false, error: e && e.message ? String(e.message) : String(e) };
    }
  });
  process.stdout.write(JSON.stringify(results));
});
