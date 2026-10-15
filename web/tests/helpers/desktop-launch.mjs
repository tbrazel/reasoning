import assert from "node:assert/strict";
import { realpath } from "node:fs/promises";
import path from "node:path";

export function assertDesktopPlatform(platform = process.platform) {
  assert.ok(
    ["linux", "darwin"].includes(platform),
    "Rendered desktop fixtures require Linux or macOS.",
  );
}

export function desktopLaunchOptions({
  repository,
  profileDirectory,
  platform = process.platform,
  environment = process.env,
  serverUrl,
}) {
  assertDesktopPlatform(platform);
  assert.ok(path.isAbsolute(repository), "Use an absolute repository path.");
  assert.ok(path.isAbsolute(profileDirectory), "Use an absolute fixture profile path.");
  const noSandbox = environment.REASONING_ELECTRON_TEST_NO_SANDBOX === "1";
  const env = {
    ...environment,
    XDG_CONFIG_HOME: profileDirectory,
    REASONING_DESKTOP_MODE: "organization",
  };
  delete env.REASONING_SERVER_URL;
  delete env.ELECTRON_RUN_AS_NODE;
  if (serverUrl !== undefined) env.REASONING_SERVER_URL = serverUrl;
  return {
    executablePath:
      environment.REASONING_ELECTRON_TEST_EXECUTABLE ||
      path.join(
        repository,
        "desktop/node_modules/electron/dist",
        platform === "darwin" ? "Electron.app/Contents/MacOS/Electron" : "electron",
      ),
    args: [
      `--user-data-dir=${profileDirectory}`,
      path.join(repository, "desktop"),
      ...(noSandbox ? ["--no-sandbox"] : []),
    ],
    // Playwright otherwise silently adds --no-sandbox on Linux.
    chromiumSandbox: !noSandbox,
    cwd: repository,
    env,
    timeout: 30000,
  };
}

export async function assertDesktopProfile(application, profileDirectory, { sandbox = true } = {}) {
  const actual = await application.evaluate(({ app }) => ({
    userData: app.getPath("userData"),
    sessionData: app.getPath("sessionData"),
    locked: app.hasSingleInstanceLock(),
    noSandbox: app.commandLine.hasSwitch("no-sandbox"),
  }));
  const expected = await realpath(profileDirectory);
  assert.equal(await realpath(actual.userData), expected, "Client settings must use the fixture profile.");
  assert.equal(await realpath(actual.sessionData), expected, "Client sessions must use the fixture profile.");
  assert.equal(actual.locked, true, "The actual public host must hold its profile lock.");
  assert.equal(actual.noSandbox, !sandbox, "The actual launch must match the explicit sandbox choice.");
}
