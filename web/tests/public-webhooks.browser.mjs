import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { mkdtemp, readFile, realpath, rm } from "node:fs/promises";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import { chromium } from "playwright";

const repository = fileURLToPath(new URL("../../", import.meta.url));
const password = "webhook browser fixture password";
async function until(predicate, description) {
  const deadline = Date.now() + 20000;
  while (Date.now() < deadline) {
    if (await predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error("Timed out: " + description);
}

test("rendered webhook administration recovers exact lost creation, inspects delivery, and revokes", { timeout: 120000 }, async () => {
  const temporary = await realpath(await mkdtemp(path.join(os.tmpdir(), "reasoning-webhook-browser-"))), state = path.join(temporary, "state");
  const listener = net.createServer(); listener.listen(0, "127.0.0.1"); await once(listener, "listening");
  const port = listener.address().port; await new Promise((resolve) => listener.close(resolve));
  const url = "http://127.0.0.1:" + port;
  const server = spawn(path.join(repository, ".venv/bin/python"), ["-m", "reasoning_ledger.server.cli", "--data-dir", state,
    "serve", "--public-url", url, "--port", String(port), "--frontend-dir", path.join(repository, "web/dist"), "--no-inference-worker"], { cwd: repository });
  let logs = "", browser;
  server.stdout.on("data", (chunk) => { logs += chunk; }); server.stderr.on("data", (chunk) => { logs += chunk; });
  try {
    await until(async () => { try { return (await fetch(url + "/api/health")).ok; } catch { return false; } }, "server readiness");
    const response = await fetch(url + "/api/setup", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
      setup_token: await readFile(path.join(state, "setup-token"), "utf8"), email: "owner@example.org", name: "Owner", password,
      organization_name: "Research", transport: "bearer",
    }) });
    assert.equal(response.status, 201); const session = await response.json(), root = "/organizations/" + session.organization_id;
    const call = async (endpoint, body, method = "POST") => {
      const result = await fetch(url + "/api" + endpoint, { method, headers: { "Content-Type": "application/json", Authorization: "Bearer " + session.access_token },
        body: body === undefined ? undefined : JSON.stringify(body) });
      assert.ok(result.ok, "Fixture " + method + " " + endpoint + ": HTTP " + result.status);
      return result.status === 204 ? undefined : result.json();
    };
    const project = await call(root + "/projects", { name: "Algebra" });
    const channel = await call(root + "/channels", { name: "research", project_id: project.id });
    const topic = await call(root + "/topics", { name: "Arithmetic", kind: "alpha", channel_id: channel.id });
    browser = await chromium.launch({ headless: true });
    const page = await (await browser.newContext({ viewport: { width: 1100, height: 950 }, reducedMotion: "reduce" })).newPage();
    const errors = []; page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(url); await page.getByLabel("Email", { exact: true }).fill("owner@example.org");
    await page.getByLabel("Password", { exact: true }).fill(password); await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await page.getByRole("button", { name: "Account menu", exact: true }).click();
    await page.getByRole("button", { name: "Administration" }).click();
    const dialog = page.getByRole("dialog"); await dialog.getByRole("button", { name: "Bots", exact: true }).click();
    await dialog.getByLabel("Bot name", { exact: true }).fill("Arithmetic <script>not code</script>");
    await dialog.getByRole("radio", { name: /Read events/ }).check();
    await dialog.getByRole("checkbox", { name: /Arithmetic/ }).check();
    await dialog.getByRole("button", { name: "Create bot and show credential", exact: true }).click();
    await dialog.getByLabel("Bot credential", { exact: true }).waitFor();
    const token = await dialog.getByLabel("Bot credential", { exact: true }).inputValue();
    const bots = await call(root + "/bots", undefined, "GET"); assert.equal(bots.length, 1);
    assert.equal(await page.locator("script").filter({ hasText: "not code" }).count(), 0);
    await dialog.getByRole("button", { name: "I saved the credential", exact: true }).click();
    const hooks = dialog.getByRole("region", { name: "Outgoing bot webhooks" });
    await hooks.getByLabel("Webhook bot", { exact: true }).selectOption(bots[0].id);
    // Reserved .invalid cannot reach a public receiver. TLS wire tests live in pytest.
    await hooks.getByLabel("Public HTTPS receiver URL", { exact: true }).fill("https://receiver.invalid/hooks");
    await hooks.getByLabel("Message prefix (optional)", { exact: false }).fill("!gcd");
    const requests = []; let dropped = false;
    await page.route("**/api" + root + "/bot-webhooks", async (route) => {
      if (route.request().method() !== "POST") { await route.continue(); return; }
      requests.push(route.request().postData());
      if (!dropped) {
        dropped = true; const result = await route.fetch(); assert.equal(result.status(), 201); await route.abort("failed");
      } else await route.continue();
    });
    await hooks.getByRole("button", { name: "Create webhook", exact: true }).click();
    await until(async () => await hooks.getByRole("button", { name: "Replay webhook creation" }).isEnabled(), "retained lost creation");
    await hooks.getByRole("button", { name: "Replay webhook creation" }).click();
    await hooks.getByLabel("Webhook signing secret", { exact: false }).waitFor();
    assert.equal(requests.length, 2); assert.equal(requests[0], requests[1]);
    const secret = await hooks.getByLabel("Webhook signing secret", { exact: false }).inputValue(); assert.match(secret, /^rnwh_/);
    assert.equal(await hooks.getByLabel("Webhook signing secret", { exact: false }).getAttribute("type"), "password");
    const storage = await page.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage } }));
    assert.ok(!storage.includes(secret) && !storage.includes(token));
    const subscriptions = await call(root + "/bot-webhooks", undefined, "GET");
    assert.equal(subscriptions.length, 1); assert.equal(subscriptions[0].enabled, true); assert.ok(!JSON.stringify(subscriptions).includes(secret));
    await call(root + "/topics/" + topic.id + "/messages", { content: "!gcd 24 36", client_id: "webhook-browser-message" });
    await hooks.getByRole("button", { name: "Inspect deliveries", exact: true }).click();
    const history = hooks.getByRole("region", { name: "Webhook delivery history" });
    await until(async () => await history.getByRole("listitem").count() === 1, "captured delivery record");
    const delivery = (await call(root + "/bot-webhooks/" + subscriptions[0].id + "/deliveries", undefined, "GET"))[0];
    assert.ok(delivery.id && delivery.attempts <= 1);
    assert.ok((await history.innerText()).includes(delivery.id));
    await page.setViewportSize({ width: 390, height: 850 });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    page.once("dialog", (confirmation) => confirmation.accept());
    await hooks.getByRole("button", { name: "Revoke webhook", exact: true }).click();
    await until(async () => (await call(root + "/bot-webhooks", undefined, "GET"))[0].disabled_at !== null, "revoked subscription");
    await until(async () => await hooks.getByRole("button", { name: "Revoke webhook", exact: true }).count() === 0, "revocation rendered");
    assert.equal(errors.length, 0, errors.join("\n"));
  } catch (error) { error.message += "\nServer logs:\n" + logs; throw error; }
  finally {
    await browser?.close();
    if (server.exitCode === null && server.signalCode === null) {
      server.kill("SIGTERM");
      await Promise.race([once(server, "exit"), new Promise((resolve) => setTimeout(resolve, 10000))]);
      if (server.exitCode === null && server.signalCode === null) { server.kill("SIGKILL"); await once(server, "exit"); }
    }
    await rm(temporary, { recursive: true, force: true });
  }
});
