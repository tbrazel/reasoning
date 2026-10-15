// Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { readFileSync } from "node:fs";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

let server, batch, keys;
before(async () => {
  server = await createServer({ appType: "custom", logLevel: "silent", server: { middlewareMode: true } });
  batch = await server.ssrLoadModule("/src/BatchInvitations.tsx");
  keys = await server.ssrLoadModule("/src/ProviderKeyUsage.tsx");
});
after(async () => { await server?.close(); });

test("batch parser accepts plain lists and one-column CSV, deduplicates and reports invalid entries", () => {
  assert.deepEqual(batch.invitationAddresses('email\n"A@example.org"\na@example.org\nb@example.org;bad'),
    { addresses: ["a@example.org", "b@example.org"], invalid: ["bad"], duplicates: 1 });
});
test("batch UI explains missing email setup and only owners may invite admins", () => {
  const html = renderToStaticMarkup(React.createElement(batch.BatchInvitations,
    { organizationId: "org", owner: false, mailEnabled: false, onChanged() {} }));
  assert.match(html, /make email-setup/); assert.doesNotMatch(html, /<option>admin/);
  assert.match(html, /Send invitations/); assert.match(html, /Upload email CSV/);
  assert.match(html, /hidden="" type="file"/);
  assert.doesNotMatch(html, />Preview<\/button>/);
});
test("invitation progress uses live receipts, distinguishes uncertainty and never claims delivery", () => {
  const results = ["queued", "sending", "accepted", "unknown", "rejected", "cancelled"].map((email_state, i) =>
    ({ email: `person${i}@example.org`, status: "queued", invitation_id: String(i), email_state: "queued" }));
  results.push({ email: "limited@example.org", status: "retryable" }, { email: "invalid", status: "failed", error: "invalid address" });
  const invitations = ["queued", "sending", "accepted", "unknown", "rejected", "cancelled"].map((email_state, i) =>
    ({ id: String(i), email_state, accepted_at: i === 2 ? 123 : null }));
  const progress = batch.invitationProgress(results, invitations);
  assert.deepEqual(progress.totals, { waiting: 2, accepted: 1, attention: 3, notQueued: 2, joined: 1 });
  assert.match(progress.rows[2].label, /Joined.*accepted by provider.*unconfirmed/);
  assert.match(progress.rows[3].label, /uncertain.*no automatic resend/);
  assert.match(progress.rows[6].label, /Not queued.*daily email limit/);
  const html = renderToStaticMarkup(React.createElement(batch.InvitationDeliveryProgress, { results, invitations }));
  assert.match(html, /Provider acceptance does not confirm inbox delivery/);
  assert.match(html, /send automatically/);
});
test("key usage clearly separates charges from held/unknown allocations", () => {
  const html = renderToStaticMarkup(React.createElement(keys.ProviderKeyUsage, { organizationId: "org" }));
  assert.match(html, /Reasoning usage only/); assert.match(html, /Charged/);
  assert.match(html, /Unknown holds/); assert.doesNotMatch(html, /API key|fingerprint|ciphertext/);
});
test("organization creation UI is owner-only", () => {
  const source = readFileSync(new URL("../src/OrganizationApp.tsx", import.meta.url), "utf8");
  assert.match(source, /const canCreateOrganization = organizations.some\(\(item\) => item.role === "owner"\)/);
});
test("independent reviewer selection does not advertise unsupported bot routes", () => {
  const source = readFileSync(new URL("../src/ResearchControls.tsx", import.meta.url), "utf8");
  assert.match(source, /label="Reviewer override" models=\{allowedModels.filter\(id => !id.startsWith\("bot\/"\)\)\}/);
});
