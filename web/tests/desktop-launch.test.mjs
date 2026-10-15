import assert from "node:assert/strict";
import { mkdir, mkdtemp, realpath, rm, symlink } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import {
  assertDesktopPlatform,
  assertDesktopProfile,
  desktopLaunchOptions,
} from "./helpers/desktop-launch.mjs";

const repository = path.resolve("fixture checkout");
const profileDirectory = path.resolve("fixture profile α");
const environment = {
  HOME: "/existing/home",
  XDG_CONFIG_HOME: "/existing/config",
  REASONING_DESKTOP_MODE: "local-workbench",
  REASONING_SERVER_URL: "https://existing.example.org",
  ELECTRON_RUN_AS_NODE: "1",
};

for (const platform of ["linux", "darwin"]) {
  test(`${platform} launch isolates public-client profiles without disabling the sandbox`, () => {
    const options = desktopLaunchOptions({ repository, profileDirectory, environment, platform });
    assert.equal(options.chromiumSandbox, true);
    assert.deepEqual(options.args, [`--user-data-dir=${profileDirectory}`, path.join(repository, "desktop")]);
    assert.equal(options.cwd, repository);
    assert.equal(options.env.XDG_CONFIG_HOME, profileDirectory);
    assert.equal(options.env.HOME, environment.HOME);
    assert.equal(options.env.REASONING_DESKTOP_MODE, "organization");
    assert.equal(options.env.REASONING_SERVER_URL, undefined);
    assert.equal(options.env.ELECTRON_RUN_AS_NODE, undefined);
    assert.equal(options.executablePath, path.join(repository, "desktop/node_modules/electron/dist",
      platform === "darwin" ? "Electron.app/Contents/MacOS/Electron" : "electron"));
    assert.equal(environment.REASONING_SERVER_URL, "https://existing.example.org");
    assert.equal(environment.ELECTRON_RUN_AS_NODE, "1");
  });

  test(`${platform} trusted-fixture sandbox waiver is explicit and exact`, () => {
    for (const value of [undefined, "", "0", "true", "1"]) {
      const options = desktopLaunchOptions({ repository, profileDirectory, platform,
        environment: { ...environment, REASONING_ELECTRON_TEST_NO_SANDBOX: value } });
      assert.equal(options.chromiumSandbox, value !== "1");
      assert.equal(options.args.includes("--no-sandbox"), value === "1");
    }
  });

  test(`${platform} packaged executable and beta server keep the isolated profile`, () => {
    const executable = path.resolve("Reasoning app", "reasoning");
    const url = "http://127.0.0.1:12345";
    const options = desktopLaunchOptions({ repository, profileDirectory, platform, serverUrl: url,
      environment: { ...environment, REASONING_ELECTRON_TEST_EXECUTABLE: executable } });
    assert.equal(options.executablePath, executable);
    assert.equal(options.env.REASONING_SERVER_URL, url);
    assert.equal(options.chromiumSandbox, true);
    assert.equal(options.args[0], `--user-data-dir=${profileDirectory}`);
  });
}

test("unsupported desktop platforms and relative launch paths fail before launch", () => {
  for (const platform of ["win32", "freebsd", "", "Linux"]) {
    assert.throws(() => assertDesktopPlatform(platform), /Linux or macOS/);
    assert.throws(() => desktopLaunchOptions({ repository, profileDirectory, platform }), /Linux or macOS/);
  }
  assert.throws(() => desktopLaunchOptions({ repository: "relative", profileDirectory, platform: "linux" }), /absolute repository/);
  assert.throws(() => desktopLaunchOptions({ repository, profileDirectory: "relative", platform: "darwin" }), /absolute fixture profile/);
});

async function profiles(context) {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "reasoning-profile-assertion-"));
  context.after(() => rm(temporary, { recursive: true, force: true }));
  const first = path.join(temporary, "first"), second = path.join(temporary, "second");
  await mkdir(first, { mode: 0o700 });
  await mkdir(second, { mode: 0o700 });
  return { temporary, first, second };
}

// Policy/verification units: this object is not a native Electron installation.
function application({ userData, sessionData = userData, locked = true, noSandbox = false }) {
  return {
    evaluate: async (callback) => callback({ app: {
      getPath: (name) => ({ userData, sessionData })[name],
      hasSingleInstanceLock: () => locked,
      commandLine: { hasSwitch: (name) => name === "no-sandbox" && noSandbox },
    } }),
  };
}

test("runtime profile assertion compares canonical paths, including macOS-style aliases", async (context) => {
  const { temporary, first } = await profiles(context);
  const alias = path.join(temporary, "alias");
  await symlink(first, alias, "dir");
  await assertDesktopProfile(application({ userData: alias }), await realpath(first));
});

test("runtime profile assertion rejects foreign settings, sessions and absent locks", async (context) => {
  const { first, second } = await profiles(context);
  await assert.rejects(assertDesktopProfile(application({ userData: second }), first), /Client settings/);
  await assert.rejects(assertDesktopProfile(application({ userData: first, sessionData: second }), first), /Client sessions/);
  await assert.rejects(assertDesktopProfile(application({ userData: first, locked: false }), first), /profile lock/);
});

test("runtime assertion refuses an implicit sandbox waiver and requires the selected mode", async (context) => {
  const { first } = await profiles(context);
  await assert.rejects(assertDesktopProfile(application({ userData: first, noSandbox: true }), first), /sandbox choice/);
  await assertDesktopProfile(application({ userData: first, noSandbox: true }), first, { sandbox: false });
  await assert.rejects(assertDesktopProfile(application({ userData: first }), first, { sandbox: false }), /sandbox choice/);
});
