// Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { createServer } from "vite";

let server, editor;
before(async () => {
  server = await createServer({ appType: "custom", logLevel: "silent", server: { middlewareMode: true } });
  editor = await server.ssrLoadModule("/src/messageEditorMentions.ts");
});
after(async () => { await server?.close(); });

test("human message edits show names and preserve exact bot model identities", () => {
  const id = "a".repeat(32), other = "b".repeat(32);
  const wire = `Ask @[${id}:o3] and @[${other}] to check this.`;
  const draft = editor.readableMessageEdit(wire, [{ id, name: "ResearchBot" }, { id: other, name: "Ada" }]);
  assert.equal(draft.text, "Ask @ResearchBot-o3 and @Ada to check this.");
  assert.equal(editor.encodeMessageEdit(draft), wire);
  const changed = editor.updateMessageEdit(draft, "Please " + draft.text);
  assert.equal(editor.encodeMessageEdit(changed), "Please " + wire);
  const deleted = editor.updateMessageEdit(draft, draft.text.replace("@ResearchBot-o3", "someone else"));
  assert.equal(editor.encodeMessageEdit(deleted), `Ask someone else and @[${other}] to check this.`);
});

test("duplicate display names retain their respective mention identities", () => {
  const ids = ["c".repeat(32), "d".repeat(32)];
  const wire = `@[${ids[0]}] then @[${ids[1]}]`;
  const draft = editor.readableMessageEdit(wire, ids.map(id => ({ id, name: "Same Name" })));
  assert.equal(draft.text, "@Same Name then @Same Name");
  assert.equal(editor.encodeMessageEdit(editor.updateMessageEdit(draft, draft.text + "!")), wire + "!");
});
