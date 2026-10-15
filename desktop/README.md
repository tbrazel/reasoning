# Reasoning desktop

The default Electron client connects to an organization’s public Reasoning
server and displays the same chat interface as the browser. It does not start
a Python backend, copy local credentials, or require a hosted identity provider.
The server owns accounts, invitations, organization keys, messages, and read
state. No installation-specific identity-provider profile is bundled. Optional
local-workbench hosted-provider authentication requires explicit
`REASONING_DESKTOP_LIVE_AUTH=1` and an operator-owned JSON profile selected by
`REASONING_DESKTOP_PROFILE`; the organization client does not use this integration.
Accept an invitation in the browser, then sign in to the same server in
the desktop client. Beta/gamma execution depends on the server; topic kinds
alone do not enable execution.

The same beta ledger/files/review panes, private blog editor, and context/goal
controls run in the organization browser and desktop. They depend on the
server's [research workbench](../docs/research-workbench.md), not a local Codex
installation. Upgrade the server to update these interfaces.

## Run from source on macOS or Linux

Install Node.js 24 and npm. macOS requires the Xcode command-line tools for
application builds (`xcode-select --install`). Use a native Node installation
matching the machine’s CPU architecture. For Homebrew’s keg-only runtime:

```bash
export PATH="/opt/homebrew/opt/node@24/bin:$PATH"
node --version
```

From the repository root:

```bash
npm --prefix desktop ci
npm --prefix desktop start
```

Enter the installation’s origin, for example `https://chat.example.org`. HTTPS
certificate verification is not bypassed. HTTP is accepted only for localhost,
127.0.0.1, and ::1 development. Paths, credentials, queries, and fragments are
not accepted in the server setting. An initial connection can also be selected
through the environment:

```bash
REASONING_SERVER_URL=https://chat.example.org npm --prefix desktop start
```

The Connection menu provides Change server (Ctrl/Command+L), Open Reasoning
(Ctrl/Command+0), and Forget server session. On Linux, press Alt to reveal the
menu bar. Each server origin, including its port, has a separate persistent
Chromium cookie/storage partition. Changing server retains its session for a
later return. Forget removes that server’s local cookies, storage, cache, and
saved connection, not its organization or sessions on other devices. Sign out
in the chat interface to revoke the current server session.

The private `connection.json` file in Electron’s application-data directory
contains only the selected origin. Passwords and provider keys are not stored
in that file. Authentication cookies are managed by Chromium; they are not a
promise of OS-keychain-backed encryption. Protect the computer’s user profile
and use disk encryption. macOS keeps the app available when its last window
closes; Dock activation reopens it. Linux quits when all windows close.

## Native boundary

The remote chat renderer has no preload, Node integration, filesystem bridge,
or connection IPC. Context isolation, renderer sandboxing, and web security are
enabled. Requests and navigation are restricted to the selected server origin.
Webviews, new Electron windows, downloads, and browser permission requests are
denied. HTTP(S) links can open in the system browser only after native
confirmation; other protocols and URL credentials are refused.

Only the app-owned connection screen receives its small context-isolated bridge.
It is served from an allowlisted custom protocol with a restrictive CSP. IPC
accepts requests only from that screen’s actual top-level frame. Server content
cannot navigate into that protocol or use the bridge. Keep Electron updated
and connect only to an installation whose operator you trust.

Linux needs a working Chromium sandbox, supplied through supported user
namespaces or the distribution’s installed sandbox helper. Do not disable the
sandbox to run a real organization. A missing sandbox produces a startup error;
it is not silently bypassed by the client.

## Build your own application

On the target OS and CPU architecture, run:

```bash
npm --prefix desktop ci
npm --prefix desktop run build:local
```

This regenerates the vector-derived icon and creates an unpacked application
under `desktop/dist/`, without freezing Python, building a frontend, uploading,
or consuming hosted build minutes. Open `desktop/dist/mac*/Reasoning.app` on
macOS, or `desktop/dist/linux*-unpacked/reasoning` on Linux. The organization
frontend is loaded from the selected server. The app bundles Apache-2.0 and
NOTICE material; Electron also supplies its dependency licenses.

Optional installer commands are `package:mac` (DMG/ZIP) and `package:linux`
(AppImage/DEB), with `:arm64` and `:x64` variants. Build on the matching OS.
Signing/notarization uses electron-builder’s standard Apple/`CSC_*` environment
variables and requires operator-owned credentials. No desktop binaries or
automatic update service are provided by this repository.

## Verification

```bash
npm --prefix desktop test
```

The tests cover connection validation, per-server partitioning, remote security
preferences, native-link filtering, IPC sender/frame identity, and private atomic
settings. The rendered desktop test uses Playwright from `web/`, installed with
`npm --prefix web ci`, a built server frontend, and the repository Python
environment. Run `npm --prefix web run test:public-desktop` on Linux or macOS
with a working Electron sandbox and graphical session. Each client uses a private
temporary `--user-data-dir` profile, including its connection settings, sessions
and single-instance lock. The fixture checks the actual client paths and lock;
the two participants use separate profiles, and reopening uses the same profile.
Home directories are not redirected. Its isolated temporary server requires no
paid service or provider credential.
The shared beta journey also accepts
`REASONING_BETA_TEST_DESKTOP=1 npm --prefix web run test:public-inference` on
Linux or macOS, using a desktop reviewer and two browser participants with a
local provider fixture. See [beta execution](../docs/inference.md).

Both desktop journeys explicitly enable Playwright's Chromium sandbox option;
its default disables sandboxing on Linux. The launch is checked for an unexpected
`--no-sandbox` switch. `REASONING_ELECTRON_TEST_EXECUTABLE` selects an unpacked
client executable instead of the source Electron binary. The optional
`REASONING_ELECTRON_TEST_NO_SANDBOX=1` waiver is for trusted synthetic fixtures
only: such a run does not verify production sandboxing. See the
[Playwright launch options](https://playwright.dev/docs/api/class-electron#electron-launch-option-chromium-sandbox).

These fixtures do not verify native OS credential storage or HTTPS deployment.
Complete release acceptance also requires the installed source-built client on
each supported OS, with sandboxing enabled, against the deployed HTTPS server.

## Separate local workbench development

The source checkout also contains an explicitly selected local workbench host:

```bash
UV_PYTHON=3.12 uv sync --locked --extra desktop
npm --prefix web ci
npm --prefix web run build
npm --prefix desktop run start:workbench
```

This mode starts `.venv/bin/reasoning-ledger-web`, has local filesystem/execution
capabilities, and uses its own backend/credential configuration. Its renderer
bridge supports validated local artifact reveal. It is not the public server
client and is not included in the default application package. Do not expose
its loopback backend as an organization server. `REASONING_BACKEND_EXECUTABLE`
and `REASONING_WORKBENCH_ROOT` configure this mode only.
