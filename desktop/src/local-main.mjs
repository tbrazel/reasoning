import { execFileSync, spawn } from "node:child_process";
import { createWriteStream, existsSync, mkdirSync } from "node:fs";
import net from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { app, BrowserWindow, dialog, ipcMain, Menu, shell } from "electron";

import { resolveArtifactPath } from "./artifact-paths.mjs";
import { macApplicationMenuTemplate } from "./application-menu.mjs";
import { preferredRuntimePaths } from "./application-paths.mjs";

const desktopRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const repositoryRoot = path.resolve(desktopRoot, "..");
const preloadPath = path.join(desktopRoot, "src", "preload.mjs");

let backend = null;
let backendExit = null;
let backendLog = null;
let mainWindow = null;
let localOrigin = null;
let quitAfterBackendStops = false;
let stoppingBackend = false;
let shutdownRequested = false;

function workbenchDataRoot() {
  return path.resolve(
    process.env.REASONING_WORKBENCH_ROOT || path.join(app.getPath("userData"), "workbench"),
  );
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function page(title, detail, state = "opening") {
  const failed = state === "error";
  const closing = state === "closing";
  const html = `<!doctype html><html><head><meta charset="utf-8"><meta
    name="viewport" content="width=device-width,initial-scale=1"><title>Reasoning</title><style>
    :root{color-scheme:dark;--ink:#ececf1;--cream:#f4f4f5;--teal:#65d6b5;--blue:#83bff3;
    --canvas:#111315;--surface:#181b1e;--line:rgba(236,236,241,.14);--quiet:#a7adb5}
    *{box-sizing:border-box}body{margin:0;min-height:100vh;overflow:hidden;display:grid;place-items:center;
    background:radial-gradient(circle at 50% 36%,rgba(101,214,181,.07),transparent 32rem),
    radial-gradient(circle at 16% 86%,rgba(131,191,243,.04),transparent 28rem),var(--canvas);
    color:var(--ink);font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif;-webkit-app-region:drag}
    body:before{content:"";position:fixed;inset:0;opacity:.2;pointer-events:none;background-image:
    radial-gradient(circle,rgba(255,230,203,.32) 1px,transparent 1px);background-size:26px 26px;
    -webkit-mask-image:linear-gradient(to bottom,transparent,black 30%,black 72%,transparent)}
    .shell{position:relative;width:min(720px,calc(100vw - 64px));padding:44px 48px 40px;border:1px solid var(--line);
    border-radius:18px;background:linear-gradient(145deg,rgba(34,38,42,.96),rgba(24,27,30,.94));
    box-shadow:0 32px 90px rgba(0,0,0,.42),inset 0 1px rgba(255,255,255,.035)}
    .wordmark{display:flex;align-items:baseline;gap:12px;color:var(--cream);font:600 14px/1 ui-monospace,
    "SFMono-Regular",Menlo,Consolas,monospace;letter-spacing:.04em}.mark{color:#fff;font-size:19px;letter-spacing:-.08em}
    .eyebrow{margin-top:34px;color:var(--teal);font:600 11px/1.3 ui-monospace,"SFMono-Regular",Menlo,monospace;
    letter-spacing:.16em;text-transform:uppercase}.error .eyebrow{color:#f87171}.closing .eyebrow{color:#fbbf24}
    h1{max-width:560px;margin:12px 0 9px;color:var(--cream);font:560 30px/1.2 ui-monospace,
    "SFMono-Regular",Menlo,Consolas,monospace;letter-spacing:-.04em}p{max-width:570px;margin:0;color:var(--quiet)}
    .error p{white-space:pre-wrap}.constellation{position:relative;height:158px;margin:34px 0 25px;border-top:1px solid var(--line);
    border-bottom:1px solid var(--line);overflow:hidden}.constellation svg{position:absolute;inset:0;width:100%;height:100%}
    .edge{fill:none;stroke:rgba(125,211,252,.36);stroke-width:1.25;stroke-dasharray:5 7;animation:flow 5s linear infinite}
    .edge.strong{stroke:rgba(52,211,153,.55);stroke-dasharray:none}.node{fill:var(--surface);stroke:rgba(255,230,203,.55);stroke-width:1.25}
    .node.live{stroke:var(--teal);filter:drop-shadow(0 0 6px rgba(52,211,153,.35));animation:pulse 2.2s ease-in-out infinite}
    .constellation text{fill:rgba(255,255,255,.65);font:10px ui-monospace,"SFMono-Regular",Menlo,monospace;letter-spacing:.08em}
    .status{display:flex;align-items:center;gap:10px;color:rgba(255,255,255,.55);font:11px/1.4 ui-monospace,
    "SFMono-Regular",Menlo,monospace;letter-spacing:.08em}.status-dot{width:7px;height:7px;border-radius:50%;background:var(--teal);
    box-shadow:0 0 0 0 rgba(52,211,153,.4);animation:beacon 1.8s ease-out infinite}.error .status-dot{background:#f87171;animation:none}
    .closing .status-dot{background:#fbbf24;box-shadow:0 0 0 0 rgba(251,191,36,.4)}
    .progress{position:absolute;left:0;right:0;bottom:0;height:2px;overflow:hidden;border-radius:0 0 18px 18px}
    .progress:after{content:"";display:block;width:38%;height:100%;background:linear-gradient(90deg,transparent,var(--teal),transparent);
    animation:sweep 2.4s ease-in-out infinite}.error .progress{display:none}
    @keyframes flow{to{stroke-dashoffset:-48}}@keyframes pulse{50%{filter:drop-shadow(0 0 11px rgba(52,211,153,.7))}}
    @keyframes beacon{70%{box-shadow:0 0 0 8px rgba(52,211,153,0)}}@keyframes sweep{from{transform:translateX(-110%)}to{transform:translateX(365%)}}
    @media(max-width:560px){.shell{width:calc(100vw - 32px);padding:34px 28px 30px}h1{font-size:24px}.constellation{height:132px}}
    @media(prefers-reduced-motion:reduce){*{animation:none!important}.status-dot{box-shadow:none}}
    </style></head><body><main class="shell ${failed ? "error" : closing ? "closing" : ""}" ${failed ? "role=alert" : "role=status aria-live=polite"}>
    <div class="wordmark"><span class="mark">[r]</span><span>reasoning</span></div>
    <div class="eyebrow">${failed ? "attention needed" : closing ? "desktop shutdown" : "research workbench"}</div>
    <h1>${escapeHtml(title)}</h1><p>${escapeHtml(detail)}</p>
    <div class="constellation" aria-hidden="true"><svg viewBox="0 0 620 158" preserveAspectRatio="xMidYMid meet">
    <path class="edge" d="M90 82 C150 82 150 43 222 43 S312 82 370 82"/><path class="edge strong" d="M90 82 C164 82 160 119 254 119 S324 82 370 82"/>
    <path class="edge" d="M370 82 C430 82 440 48 522 48"/><path class="edge strong" d="M370 82 C442 82 449 116 535 116"/>
    <circle class="node" cx="76" cy="82" r="10"/><circle class="node" cx="222" cy="43" r="9"/><circle class="node" cx="254" cy="119" r="9"/>
    <circle class="node live" cx="370" cy="82" r="11"/><circle class="node" cx="522" cy="48" r="9"/><circle class="node" cx="535" cy="116" r="9"/>
    <text x="49" y="110">question</text><text x="192" y="24">sources</text><text x="225" y="146">agents</text><text x="346" y="111">reason</text>
    <text x="491" y="27">claims</text><text x="511" y="143">ledger</text></svg></div>
    <div class="status"><span class="status-dot"></span><span>${failed ? "Reasoning needs your attention" : closing ? "stopping local services" : "opening Reasoning"}</span></div>
    <div class="progress"></div></main></body></html>`;
  return `data:text/html;charset=utf-8,${encodeURIComponent(html)}`;
}

function availablePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      const port = typeof address === "object" && address ? address.port : null;
      server.close((error) => (error || port === null ? reject(error) : resolve(port)));
    });
  });
}

function backendExecutable() {
  if (process.env.REASONING_BACKEND_EXECUTABLE) {
    return path.resolve(process.env.REASONING_BACKEND_EXECUTABLE);
  }
  return app.isPackaged
    ? path.join(process.resourcesPath, "backend", "reasoning-ledger-web")
    : path.join(repositoryRoot, ".venv", "bin", "reasoning-ledger-web");
}

function ledgerExecutable() {
  return app.isPackaged
    ? path.join(process.resourcesPath, "backend", "reasoning-ledger")
    : path.join(repositoryRoot, ".venv", "bin", "reasoning-ledger");
}

function frontendDirectory() {
  return app.isPackaged
    ? path.join(process.resourcesPath, "web")
    : path.join(repositoryRoot, "web", "dist");
}

function applicationPath() {
  const entries = preferredRuntimePaths(process.platform);
  const shellPath = process.env.SHELL || (process.platform === "darwin" ? "/bin/zsh" : "/bin/sh");
  try {
    const discovered = execFileSync(shellPath, ["-ilc", 'printf %s "$PATH"'], {
      encoding: "utf8",
      timeout: 5000,
      stdio: ["ignore", "pipe", "ignore"],
    });
    entries.push(...discovered.split(path.delimiter));
  } catch {
    // GUI launches may not have a usable login shell. Common paths below
    // still cover system, Homebrew, standalone Codex, and user installs.
  }
  const home = app.getPath("home");
  entries.push(
    ...(process.env.PATH || "").split(path.delimiter),
    path.join(home, ".codex", "bin"),
    path.join(home, ".local", "bin"),
    path.join(home, ".npm-global", "bin"),
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/usr/bin",
    "/bin",
  );
  return [...new Set(entries.filter(Boolean))].join(path.delimiter);
}

function importCodexCredentials(environment) {
  // The workbench deliberately keeps its Codex state separate so an agent
  // never receives broad access to the user's personal sessions. Seed that
  // private home with only the files the ledger migration command allows.
  // Once copied, Codex owns and refreshes its desktop credentials there.
  if (environment.CODEX_HOME || environment.OPENAI_API_KEY) {
    return "Codex uses credentials supplied by the launch environment.";
  }

  const workbenchCodexHome = path.join(environment.REASONING_WORKBENCH_ROOT, "codex");
  if (existsSync(path.join(workbenchCodexHome, "auth.json"))) {
    return "Private Reasoning Codex credentials are available.";
  }

  const personalCodexHome = path.join(app.getPath("home"), ".codex");
  if (!existsSync(path.join(personalCodexHome, "auth.json"))) {
    return "Codex credentials are not configured; Reasoning will open with API-based agents available.";
  }

  const executable = ledgerExecutable();
  if (!existsSync(executable)) {
    return `Codex credential import skipped because the ledger executable is unavailable: ${executable}`;
  }
  try {
    const report = execFileSync(executable, ["migrate-codex-home", "--source", personalCodexHome], {
      cwd: app.isPackaged ? process.resourcesPath : repositoryRoot,
      env: environment,
      encoding: "utf8",
      timeout: 30_000,
      stdio: ["ignore", "pipe", "pipe"],
    }).trim();
    if (!existsSync(path.join(workbenchCodexHome, "auth.json"))) {
      return "Codex credential import did not produce private Reasoning credentials; continuing without Codex.";
    }
    return report || "Imported Codex credentials into Reasoning's private Codex home.";
  } catch (error) {
    return `Codex credential import failed; continuing without Codex: ${error instanceof Error ? error.message : String(error)}`;
  }
}

async function startBackend() {
  const executable = backendExecutable();

  const webDist = frontendDirectory();
  if (!existsSync(executable)) {
    throw new Error(`Backend executable not found:\n${executable}\n\nRun the desktop build first.`);
  }
  if (!existsSync(path.join(webDist, "index.html"))) {
    throw new Error(`Built frontend not found:\n${webDist}\n\nRun npm run build:web first.`);
  }

  const port = await availablePort();
  localOrigin = `http://127.0.0.1:${port}`;
  const logDirectory = process.env.REASONING_DESKTOP_LOG_DIR || app.getPath("logs");
  mkdirSync(logDirectory, { recursive: true });
  const logPath = path.join(logDirectory, "backend.log");
  backendLog = createWriteStream(logPath, { flags: "a" });
  backendLog.write(`\n--- launch ${new Date().toISOString()} ---\n`);

  const environment = {
    ...process.env,
    PATH: applicationPath(),
    PORT: String(port),
    REASONING_DESKTOP_RUNTIME: "1",
    REASONING_WEB_DIST: webDist,
    // A native workbench can always surface the backend's approval dock.
    // Keep browser-only deployments conservative by leaving the backend
    // default alone, but make desktop escalations explicitly interactive.
    REASONING_APPROVALS: process.env.REASONING_APPROVALS || "interactive",
    REASONING_WORKBENCH_ROOT:
      process.env.REASONING_WORKBENCH_ROOT || path.join(app.getPath("userData"), "workbench"),
  };

  const credentialReport = importCodexCredentials(environment);
  if (credentialReport) {
    backendLog.write(`Codex readiness: ${credentialReport}\n`);
  }
  backendExit = null;
  backend = spawn(executable, [], {
    cwd: app.isPackaged ? process.resourcesPath : repositoryRoot,
    env: environment,
    stdio: ["ignore", "pipe", "pipe"],
  });
  backend.stdout.pipe(backendLog, { end: false });
  backend.stderr.pipe(backendLog, { end: false });
  backend.once("exit", (code, signal) => {
    backendExit = { code, signal };
    backend = null;
    if (!stoppingBackend && mainWindow && !mainWindow.isDestroyed()) {
      void mainWindow.loadURL(
        page(
          "The workbench backend stopped",
          `Exit ${code ?? signal ?? "unknown"}. Details are in ${logPath}`,
        ),
      );
    }
  });
  backend.once("error", (error) => {
    backendExit = { error };
  });

  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    if (backendExit) {
      const description = backendExit.error?.message || backendExit.code || backendExit.signal;
      throw new Error(`Backend exited during startup (${description ?? "unknown"}).\nLog: ${logPath}`);
    }
    try {
      const response = await fetch(`${localOrigin}/api/health`, {
        signal: AbortSignal.timeout(1000),
      });
      if (response.ok) return localOrigin;
    } catch {
      // The service normally needs a fraction of a second; keep polling.
    }
    await new Promise((resolve) => setTimeout(resolve, 150));
  }
  throw new Error(`Backend did not become ready within 30 seconds.\nLog: ${logPath}`);
}

function isLocalUrl(url) {
  try {
    return localOrigin !== null && new URL(url).origin === localOrigin;
  } catch {
    return false;
  }
}

function protectNavigation(contents) {
  contents.on("will-attach-webview", (event) => event.preventDefault());
  contents.setWindowOpenHandler(({ url }) => {
    if (isLocalUrl(url)) {
      return {
        action: "allow",
        overrideBrowserWindowOptions: {
          backgroundColor: "#11100f",
          webPreferences: {
            contextIsolation: true,
            nodeIntegration: false,
            plugins: true,
            preload: preloadPath,
            sandbox: true,
            webSecurity: true,
          },
        },
      };
    }
    if (/^https?:/i.test(url)) void shell.openExternal(url);
    return { action: "deny" };
  });
  contents.on("did-create-window", (window) => protectNavigation(window.webContents));
  contents.on("will-frame-navigate", (details) => {
    if (details.isMainFrame || isLocalUrl(details.url)) return;
    details.preventDefault();
    if (/^https?:/i.test(details.url)) void shell.openExternal(details.url);
  });
  contents.on("will-navigate", (details) => {
    if (isLocalUrl(details.url)) return;
    details.preventDefault();
    if (/^https?:/i.test(details.url)) void shell.openExternal(details.url);
  });
}

function createWindow() {
  mainWindow = new BrowserWindow({
    title: "Reasoning",
    width: 1440,
    height: 920,
    minWidth: 920,
    minHeight: 640,
    backgroundColor: "#111315",
    autoHideMenuBar: process.platform === "linux",
    ...(process.platform === "darwin"
      ? {
          titleBarStyle: "hiddenInset",
          trafficLightPosition: { x: 16, y: 17 },
          // The rail and Explorer are translucent on macOS (see
          // html[data-platform="darwin"] in web/src/styles.css); every
          // other surface paints its own opaque canvas over this.
          vibrancy: "sidebar",
          visualEffectState: "active",
        }
      : {}),
    ...(process.platform === "linux"
      ? {
          titleBarStyle: "default",
          icon: app.isPackaged
            ? path.join(process.resourcesPath, "icon.png")
            : path.join(desktopRoot, "assets", "icon.png"),
        }
      : {}),
    show: false,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      plugins: true,
      preload: preloadPath,
      sandbox: true,
      webSecurity: true,
    },
  });
  protectNavigation(mainWindow.webContents);
  mainWindow.once("ready-to-show", () => mainWindow?.show());
  mainWindow.on("close", (event) => {
    if (process.platform !== "darwin" && !quitAfterBackendStops) {
      event.preventDefault();
      app.quit();
    }
  });
  mainWindow.on("closed", () => {
    mainWindow = null;
  });
  void mainWindow.loadURL(page(
    "Opening your research workspace",
    "Bringing the ledger, conversations, agents, and workflows into one view.",
  )).catch((error) => {
    // The ready workbench intentionally replaces this transient data URL.
    // Electron reports that successful handoff as an aborted navigation.
    if (error?.code !== "ERR_ABORTED") console.error("startup view failed", error);
  });
  return mainWindow;
}

/** Show the existing workbench window or recreate it after its last window
 *  was closed. Used by macOS Dock activation and by a second launch attempt. */
function reopenMainWindow() {
  if (shutdownRequested) {
    if (mainWindow && !mainWindow.isDestroyed()) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.show();
      mainWindow.focus();
    }
    return mainWindow;
  }
  let window = mainWindow;
  if (!window || window.isDestroyed()) {
    window = createWindow();
    if (localOrigin && !backendExit) void window.loadURL(localOrigin);
  }
  if (window.isMinimized()) window.restore();
  window.show();
  window.focus();
  return window;
}

function showClosingScreen() {
  const window = mainWindow;
  if (!window || window.isDestroyed()) return;
  window.setClosable(false);
  if (window.isMinimized()) window.restore();
  window.show();
  window.focus();
  void window.loadURL(page(
    "Closing Reasoning",
    "Preserving your workspace and stopping local services. This window will close automatically.",
    "closing",
  )).catch((error) => {
    if (error?.code !== "ERR_ABORTED") console.error("shutdown view failed", error);
  });
}

function stopBackend() {
  if (!backend) {
    backendLog?.end();
    backendLog = null;
    return Promise.resolve();
  }
  stoppingBackend = true;
  const processToStop = backend;
  return new Promise((resolve) => {
    let finished = false;
    const finish = () => {
      if (finished) return;
      finished = true;
      backendLog?.end();
      backendLog = null;
      resolve();
    };
    processToStop.once("exit", finish);
    processToStop.kill("SIGTERM");
    setTimeout(() => {
      if (processToStop.exitCode === null && processToStop.signalCode === null) {
        processToStop.kill("SIGKILL");
      }
      finish();
    }, 5000).unref();
  });
}

const hasSingleInstanceLock = app.requestSingleInstanceLock();
if (!hasSingleInstanceLock) {
  app.quit();
} else {
  app.setName("Reasoning");
  const requestQuit = () => app.quit();
  process.on("SIGINT", requestQuit);
  process.on("SIGTERM", requestQuit);
  if (process.platform === "linux") Menu.setApplicationMenu(null);
  app.on("second-instance", () => {
    if (app.isReady()) reopenMainWindow();
  });
  app.on("activate", () => {
    reopenMainWindow();
  });

  app.whenReady().then(async () => {
    ipcMain.handle("reasoning:reveal-artifact", (event, projectId, artifactPath) => {
      if (!isLocalUrl(event.senderFrame.url)) throw new Error("Untrusted reveal request");
      const target = resolveArtifactPath({
        workbenchRoot: workbenchDataRoot(),
        projectId,
        artifactPath,
      });
      shell.showItemInFolder(target);
    });
    const window = createWindow();
    if (process.platform === "darwin") {
      Menu.setApplicationMenu(Menu.buildFromTemplate(macApplicationMenuTemplate({
        appName: "Reasoning",
        reopen: reopenMainWindow,
      })));
      app.dock.setMenu(
        Menu.buildFromTemplate([
          { label: "Open Reasoning", click: reopenMainWindow },
        ]),
      );
    }
    try {
      const origin = await startBackend();
      if (!window.isDestroyed()) await window.loadURL(origin);
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      if (!window.isDestroyed()) await window.loadURL(page("Reasoning could not open", detail, "error"));
      dialog.showErrorBox("Reasoning could not start", detail);
    }
  });

  app.on("window-all-closed", () => {
    if (process.platform !== "darwin") app.quit();
  });

  app.on("before-quit", (event) => {
    if (quitAfterBackendStops) return;
    event.preventDefault();
    if (shutdownRequested) return;
    shutdownRequested = true;
    showClosingScreen();
    const minimumClosingDisplay = new Promise((resolve) => setTimeout(resolve, 600));
    void Promise.all([stopBackend(), minimumClosingDisplay]).finally(() => {
      quitAfterBackendStops = true;
      if (mainWindow && !mainWindow.isDestroyed()) mainWindow.setClosable(true);
      app.quit();
    });
  });
}
