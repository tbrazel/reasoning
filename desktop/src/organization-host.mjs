import { mkdir, readFile, unlink } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  app,
  BrowserWindow,
  dialog,
  ipcMain,
  Menu,
  protocol,
  session,
  shell,
} from "electron";
import {
  externalLink,
  PICKER_URL,
  readConnection,
  remotePreferences,
  sameServer,
  saveConnection,
  serverOrigin,
  serverPartition,
  trustedPicker,
} from "./server-connection.mjs";

const sourceRoot = path.dirname(fileURLToPath(import.meta.url));
const guardedSessions = new WeakSet();
let chatWindow = null;
let pickerWindow = null;
let origin = null;
let connectionError = "";
let connecting = false;
let externalPrompt = false;
let quitting = false;

protocol.registerSchemesAsPrivileged([
  {
    scheme: "reasoning-app",
    privileges: { standard: true, secure: true, corsEnabled: true },
  },
]);
app.setName("Reasoning");
app.enableSandbox();

function denyPermissions(target) {
  if (guardedSessions.has(target)) return;
  guardedSessions.add(target);
  target.setPermissionRequestHandler((_contents, _permission, callback) =>
    callback(false),
  );
  target.setPermissionCheckHandler(() => false);
  target.on("will-download", (event) => event.preventDefault());
}

async function openExternal(value, window) {
  const url = externalLink(value);
  if (!url || externalPrompt || !window || window.isDestroyed()) return;
  externalPrompt = true;
  try {
    const answer = await dialog.showMessageBox(window, {
      type: "question",
      title: "Open link in your browser?",
      message: "This link leaves Reasoning.",
      detail: url,
      buttons: ["Cancel", "Open browser"],
      defaultId: 0,
      cancelId: 0,
      noLink: true,
    });
    if (answer.response === 1) await shell.openExternal(url);
  } catch {
    /* A closed window or unavailable system browser is not a native action. */
  } finally {
    externalPrompt = false;
  }
}

function guardChat(window, server) {
  const contents = window.webContents;
  contents.on("will-attach-webview", (event) => event.preventDefault());
  contents.setWindowOpenHandler(({ url }) => {
    void openExternal(url, window);
    return { action: "deny" };
  });
  const navigation = (event, url) => {
    const target = url || event.url;
    if (sameServer(target, server)) return;
    event.preventDefault();
    if (event.isMainFrame !== false) void openExternal(target, window);
  };
  contents.on("will-navigate", navigation);
  contents.on("will-redirect", navigation);
  contents.on("will-frame-navigate", navigation);
  contents.on(
    "did-fail-load",
    (_event, code, _description, _url, isMainFrame) => {
      if (
        !isMainFrame ||
        code === -3 ||
        window.isDestroyed() ||
        chatWindow !== window
      )
        return;
      connectionError =
        "The server could not load. Check its address, network connection, and HTTPS certificate. Certificate errors cannot be bypassed.";
      showPicker();
    },
  );
}

function showPicker() {
  if (quitting) return;
  if (pickerWindow && !pickerWindow.isDestroyed()) {
    pickerWindow.show();
    pickerWindow.focus();
    return;
  }
  pickerWindow = new BrowserWindow({
    title: "Connect to Reasoning",
    width: 600,
    height: 650,
    minWidth: 420,
    minHeight: 540,
    backgroundColor: "#111315",
    webPreferences: {
      partition: "reasoning-connection-picker",
      preload: path.join(sourceRoot, "connection-preload.cjs"),
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      webSecurity: true,
      webviewTag: false,
      navigateOnDragDrop: false,
    },
  });
  pickerWindow.webContents.on("will-navigate", (event) =>
    event.preventDefault(),
  );
  pickerWindow.webContents.on("will-frame-navigate", (event) =>
    event.preventDefault(),
  );
  pickerWindow.webContents.on("will-redirect", (event) =>
    event.preventDefault(),
  );
  pickerWindow.webContents.on("will-attach-webview", (event) =>
    event.preventDefault(),
  );
  pickerWindow.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  pickerWindow.on("closed", () => {
    pickerWindow = null;
  });
  void pickerWindow.loadURL(PICKER_URL);
}

async function connect(value) {
  if (connecting)
    return { ok: false, error: "A connection is already opening." };
  connecting = true;
  let window;
  try {
    const selected = serverOrigin(value);
    await saveConnection(app.getPath("userData"), selected);
    const target = session.fromPartition(serverPartition(selected));
    denyPermissions(target);
    // The organization UI has no third-party dependencies. Restrict requests as
    // well as navigation so remote content cannot probe other hosts or local files.
    target.webRequest.onBeforeRequest((details, callback) =>
      callback({ cancel: !sameServer(details.url, selected) }),
    );
    window = new BrowserWindow({
      title: "Reasoning",
      width: 1440,
      height: 920,
      minWidth: 600,
      minHeight: 540,
      backgroundColor: "#111315",
      autoHideMenuBar: process.platform === "linux",
      webPreferences: remotePreferences(selected),
    });
    guardChat(window, selected);
    const previous = chatWindow;
    chatWindow = window;
    origin = selected;
    connectionError = "";
    window.on("closed", () => {
      if (chatWindow === window) chatWindow = null;
    });
    previous?.close();
    await window.loadURL(selected);
    pickerWindow?.close();
    return { ok: true };
  } catch (error) {
    if (window && !window.isDestroyed()) window.close();
    connectionError =
      error instanceof TypeError
        ? error.message
        : "Could not connect. Check the server URL, network, and HTTPS certificate, then try again.";
    showPicker();
    return { ok: false, error: connectionError };
  } finally {
    connecting = false;
  }
}

function reopen() {
  const window = chatWindow || pickerWindow;
  if (window && !window.isDestroyed()) {
    if (window.isMinimized()) window.restore();
    window.show();
    window.focus();
  } else if (origin) void connect(origin);
  else showPicker();
}

async function forgetSession() {
  if (!origin || connecting) return;
  const window = chatWindow || pickerWindow;
  const answer = await dialog.showMessageBox(window, {
    type: "question",
    title: "Forget this server session?",
    message:
      "Remove this server’s cookies and saved connection from this computer?",
    detail:
      "This does not delete the organization, its messages, or sessions on other devices.",
    buttons: ["Cancel", "Forget session"],
    defaultId: 0,
    cancelId: 0,
    noLink: true,
  });
  if (answer.response !== 1) return;
  connecting = true;
  try {
    const previous = origin;
    origin = null;
    showPicker();
    chatWindow?.close();
    const target = session.fromPartition(serverPartition(previous));
    await target.clearStorageData();
    await target.clearCache();
    await unlink(path.join(app.getPath("userData"), "connection.json")).catch(
      (error) => {
        if (error.code !== "ENOENT") throw error;
      },
    );
  } catch {
    connectionError =
      "Could not remove all local session data. Close Reasoning and check application-data permissions.";
  } finally {
    connecting = false;
  }
}

function installMenu() {
  const open = {
    label: "Open Reasoning",
    accelerator: "CommandOrControl+0",
    click: reopen,
  };
  Menu.setApplicationMenu(
    Menu.buildFromTemplate([
      ...(process.platform === "darwin"
        ? [
            {
              label: "Reasoning",
              submenu: [
                { role: "about" },
                open,
                { type: "separator" },
                { role: "services" },
                { type: "separator" },
                { role: "hide" },
                { role: "hideOthers" },
                { role: "unhide" },
                { type: "separator" },
                { role: "quit" },
              ],
            },
          ]
        : []),
      {
        label: "Connection",
        submenu: [
          open,
          {
            label: "Change server…",
            accelerator: "CommandOrControl+L",
            click: showPicker,
          },
          {
            label: "Forget server session…",
            click: () => void forgetSession(),
          },
          { type: "separator" },
          { role: process.platform === "darwin" ? "close" : "quit" },
        ],
      },
      { role: "editMenu" },
      { role: "viewMenu" },
      { role: "windowMenu" },
    ]),
  );
  if (process.platform === "darwin")
    app.dock.setMenu(Menu.buildFromTemplate([open]));
}

if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on("second-instance", reopen);
  app.on("activate", () => {
    if (app.isReady()) reopen();
  });
  app.on("before-quit", () => {
    quitting = true;
  });
  app.on("window-all-closed", () => {
    if (process.platform !== "darwin" && !connecting) app.quit();
  });
  process.on("SIGINT", () => app.quit());
  process.on("SIGTERM", () => app.quit());
  // Electron signals ready after the main module finishes evaluating. Do not
  // hold that module's top-level await on app.whenReady().
  void app
    .whenReady()
    .then(async () => {
      await mkdir(app.getPath("userData"), { recursive: true, mode: 0o700 });
      const pickerSession = session.fromPartition(
        "reasoning-connection-picker",
      );
      denyPermissions(pickerSession);
      pickerSession.protocol.handle("reasoning-app", async (request) => {
        const url = new URL(request.url);
        const names = {
          "/index.html": ["index.html", "text/html"],
          "/style.css": ["style.css", "text/css"],
          "/picker.js": ["picker.js", "text/javascript"],
        };
        const resource = Object.hasOwn(names, url.pathname)
          ? names[url.pathname]
          : null;
        if (
          url.hostname !== "connection" ||
          url.search ||
          url.hash ||
          request.method !== "GET" ||
          !resource
        )
          return new Response(null, { status: 404 });
        return new Response(
          await readFile(path.join(sourceRoot, "connection", resource[0])),
          {
            headers: {
              "Content-Type": resource[1] + "; charset=utf-8",
              "X-Content-Type-Options": "nosniff",
              "Content-Security-Policy":
                "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'none'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
            },
          },
        );
      });
      pickerSession.webRequest.onBeforeRequest((details, callback) =>
        callback({
          cancel: !details.url.startsWith("reasoning-app://connection/"),
        }),
      );
      const requirePicker = (event) => {
        if (!trustedPicker(event, pickerWindow?.webContents))
          throw new Error("Untrusted connection request");
      };
      ipcMain.handle("reasoning:connection-status", (event) => {
        requirePicker(event);
        return { origin, error: connectionError };
      });
      ipcMain.handle("reasoning:connect", (event, value) => {
        requirePicker(event);
        return connect(value);
      });
      installMenu();
      try {
        origin = process.env.REASONING_SERVER_URL
          ? serverOrigin(process.env.REASONING_SERVER_URL)
          : await readConnection(app.getPath("userData"));
      } catch (error) {
        connectionError = error.message;
      }
      if (origin) await connect(origin);
      else showPicker();
    })
    .catch(() => {
      dialog.showErrorBox(
        "Reasoning could not start",
        "Connection settings or application resources could not be opened.",
      );
      app.quit();
    });
}
