// Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
import assert from "node:assert/strict";
import { realpath } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import { chromium } from "playwright";
import { createServer } from "vite";

test("invitation layout supports direct paste/send, safe retries and compact CSV upload on desktop and phone", { timeout: 60000 }, async () => {
  const root = fileURLToPath(new URL("../", import.meta.url));
  const server = await createServer({ root, server: { host: "127.0.0.1", port: 0,
    fs: { allow: [root, await realpath(root + "node_modules")] } } });
  await server.listen(); const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    const errors = [], submissions = [];
    const user = "a".repeat(32), org = "b".repeat(32);
    let mailEnabled = true, invitations = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.route("**/api/**", async route => {
      const request = route.request(), path = new URL(request.url()).pathname;
      let data = [];
      if (path === "/api/health") data = { initialized: true };
      else if (path === "/api/auth/me") data = { id: user, name: "Alice", email: "alice@example.org" };
      else if (path === "/api/organizations") data = [{ id: org, name: "Research", role: "owner" }];
      else if (path.endsWith("/hierarchy")) data = { projects: [], channels: [], topics: [], topic_state: [] };
      else if (path.endsWith("/members")) data = [{ id: user, name: "Alice", email: "alice@example.org", role: "owner" }];
      else if (path.endsWith("/presence")) data = { humans: [], bots: [] };
      else if (path.endsWith("/navigation-usage")) data = { projects: {}, channels: {}, topics: {} };
      else if (path.endsWith("/model-prices")) data = { models: [] };
      else if (path.endsWith("/policy")) data = { monthly_budget_microusd: 10_000_000 };
      else if (path.endsWith("/invitation-mail")) data = { enabled: mailEnabled, daily_limit: 100, worker_running: true };
      else if (path.endsWith("/invitations")) data = invitations;
      else if (path.endsWith("/invitations/batch") && request.method() === "POST") {
        const body = request.postDataJSON(); submissions.push(body);
        if (submissions.length === 1) {
          await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "Fixture response lost" }) });
          return;
        }
        const rows = body.emails.map(email => {
          let invite = invitations.find(invite => invite.email === email);
          if (!invite) { invite = { id: `invite-${invitations.length}`, email, role: body.role, expires_at: 2_000_000_000,
            accepted_at: null, revoked_at: null, email_state: "queued" }; invitations.push(invite); }
          return { email, status: "queued", invitation_id: invite.id, email_state: invite.email_state };
        });
        data = { results: rows, retry_after_seconds: null };
      }
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(data) });
    });
    const openInvitations = async () => {
      const navigation = page.getByLabel("Toggle project navigation", { exact: true });
      await navigation.waitFor();
      if (await navigation.getAttribute("aria-expanded") === "false") await navigation.click();
      await page.getByRole("button", { name: "Account menu", exact: true }).click();
      await page.getByRole("button", { name: "Administration", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: "Research administration", exact: true });
      await dialog.getByRole("button", { name: "Invitations", exact: true }).click();
      await dialog.locator(".org-batch-invitations > summary").click();
      return dialog;
    };
    const assertLayout = async dialog => {
      const batch = dialog.locator(".org-batch-invitations");
      const textarea = await batch.getByRole("textbox", { name: "Email addresses", exact: false }).boundingBox();
      const upload = await batch.getByRole("button", { name: "Upload CSV", exact: true }).boundingBox();
      const role = await batch.getByRole("combobox", { name: "Invitation role" }).boundingBox();
      assert.ok(upload.y >= textarea.y + textarea.height, "upload control must not overlap the address field");
      assert.ok(role.y >= upload.y + upload.height, "role control must not overlap upload");
      assert.equal(await batch.locator('input[type="file"]').isVisible(), false);
      assert.equal(await dialog.evaluate(node => node.scrollWidth <= node.clientWidth + 1), true, "dialog must not overflow horizontally");
      const checkbox = await dialog.getByRole("checkbox", { name: "Send invitation by email" }).boundingBox();
      assert.equal(checkbox.width, 16); assert.equal(checkbox.height, 16);
    };
    await page.goto(server.resolvedUrls.local[0] + "organization.html");
    let dialog = await openInvitations();
    await dialog.getByRole("checkbox", { name: "Send invitation by email" }).waitFor();
    const batch = dialog.locator(".org-batch-invitations");
    const addresses = batch.getByRole("textbox", { name: "Email addresses", exact: false });
    const send = () => batch.getByRole("button", { name: "Send invitations", exact: true });
    assert.equal(await send().isVisible(), true); assert.equal(await send().isDisabled(), true);
    const allAddresses = ["alice@example.org", "bob@example.org", ...Array.from({ length: 30 }, (_, i) => `person${i}@example.org`)];
    await addresses.fill([...allAddresses, "Alice@example.org"].join("\n"));
    await batch.getByRole("status").filter({ hasText: "32 recipients · 1 duplicate removed" }).waitFor();
    assert.equal(await send().isEnabled(), true);
    assert.equal(await batch.locator(".org-batch-preview").getAttribute("open"), null, "preview is optional");
    await assertLayout(dialog);
    await send().click();
    const retry = batch.getByRole("button", { name: "Retry same batch", exact: true });
    await retry.waitFor();
    assert.equal(await addresses.isDisabled(), true);
    assert.equal(await batch.getByRole("combobox", { name: "Invitation role" }).isDisabled(), true);
    await retry.click();
    await batch.getByRole("button", { name: "Batch queued", exact: true }).waitFor();
    assert.deepEqual(submissions[0], submissions[1], "uncertain replies must retain the exact batch and client identity");
    assert.deepEqual(submissions[0].emails, allAddresses);
    assert.equal(await batch.getByRole("button", { name: "Batch queued", exact: true }).isDisabled(), true);
    const progress = batch.getByLabel("Invitation email progress", { exact: true });
    await progress.getByText("32 waiting", { exact: true }).waitFor();
    assert.equal(await batch.locator(".org-batch-results").isVisible(), false, "individual statuses should be collapsed");
    invitations.slice(0, 10).forEach(invite => { invite.email_state = "accepted"; });
    await progress.getByText("10 provider-accepted", { exact: true }).waitFor();
    await progress.getByText("22 waiting", { exact: true }).waitFor();
    assert.equal(submissions.length, 2, "progress polling must not resend the batch");
    invitations[0].accepted_at = 123; invitations[1].email_state = "unknown";
    await progress.getByText("1 need attention", { exact: true }).waitFor();
    await progress.getByText("1 joined", { exact: true }).waitFor();
    await batch.getByText("Recipient status", { exact: true }).click();
    await batch.locator(".org-batch-results").getByText("bob@example.org · Email submission uncertain; no automatic resend", { exact: true }).waitFor();
    await batch.getByText("Recipient status", { exact: true }).click();
    await batch.getByRole("button", { name: "New batch", exact: true }).click();
    await addresses.fill("bob@example.org");
    await batch.getByText("1 already invited;", { exact: false }).waitFor();
    await send().click(); await batch.getByRole("button", { name: "Batch queued", exact: true }).waitFor();
    assert.equal(submissions.length, 2, "an existing uncertain email must not be submitted again");
    await batch.getByRole("button", { name: "New batch", exact: true }).click();
    await page.setViewportSize({ width: 390, height: 844 });
    await assertLayout(dialog);
    await addresses.fill("bad-address");
    assert.equal(await send().isDisabled(), true);
    await batch.getByRole("alert").filter({ hasText: "Invalid entries" }).waitFor();
    await addresses.fill(Array.from({ length: 101 }, (_, i) => `person${i}@example.org`).join("\n"));
    assert.equal(await send().isDisabled(), true);
    await batch.getByRole("alert").filter({ hasText: "Use at most 100" }).waitFor();
    const chooserReady = page.waitForEvent("filechooser");
    await batch.getByRole("button", { name: "Upload CSV", exact: true }).click();
    const chooser = await chooserReady;
    await chooser.setFiles({ name: "emails.csv", mimeType: "text/csv", buffer: Buffer.from('email\n"carol@example.org"\ndave@example.org') });
    await batch.getByRole("status").filter({ hasText: "2 recipients" }).waitFor();
    assert.equal(await addresses.inputValue(), 'email\n"carol@example.org"\ndave@example.org');
    await batch.getByRole("combobox", { name: "Invitation role" }).selectOption("viewer");
    await assertLayout(dialog);
    await send().click(); await batch.getByRole("button", { name: "Batch queued", exact: true }).waitFor();
    assert.deepEqual(submissions[2].emails, ["carol@example.org", "dave@example.org"]);
    assert.equal(submissions[2].role, "viewer");
    assert.notEqual(submissions[2].client_id, submissions[0].client_id);
    await page.reload(); dialog = await openInvitations();
    const recoveredBatch = dialog.locator(".org-batch-invitations");
    await recoveredBatch.getByRole("textbox", { name: "Email addresses", exact: false }).fill("bob@example.org\nerin@example.org");
    await recoveredBatch.getByText("1 already invited;", { exact: false }).waitFor();
    await recoveredBatch.getByRole("button", { name: "Send invitations", exact: true }).click();
    await recoveredBatch.getByRole("button", { name: "Batch queued", exact: true }).waitFor();
    assert.deepEqual(submissions[3].emails, ["erin@example.org"], "refresh must preserve existing invitations without emailing them again");
    mailEnabled = false;
    await page.reload(); dialog = await openInvitations();
    const disabledBatch = dialog.locator(".org-batch-invitations");
    await disabledBatch.getByText("Email is not configured.", { exact: false }).waitFor();
    await disabledBatch.getByRole("textbox", { name: "Email addresses", exact: false }).fill("valid@example.org");
    assert.equal(await disabledBatch.getByRole("button", { name: "Send invitations", exact: true }).isDisabled(), true);
    assert.equal(submissions.length, 4);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); await server.close(); }
});
