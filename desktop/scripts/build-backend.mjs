import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const desktopRoot = path.resolve(scriptDirectory, "..");
const repositoryRoot = path.resolve(desktopRoot, "..");

if (!new Set(["darwin", "linux"]).has(process.platform)) {
  throw new Error("Desktop backend builds are supported on macOS and Linux.");
}

const result = spawnSync(
  process.env.UV_EXECUTABLE || "uv",
  [
    "run",
    "--project",
    repositoryRoot,
    "--frozen",
    "--extra",
    "desktop",
    "pyinstaller",
    "--clean",
    "--noconfirm",
    "--distpath",
    path.join(desktopRoot, "build", "backend"),
    "--workpath",
    path.join(desktopRoot, "build", "pyinstaller"),
    path.join(desktopRoot, "reasoning-backend.spec"),
  ],
  { cwd: repositoryRoot, stdio: "inherit" },
);

if (result.error) throw result.error;
if (result.status !== 0) process.exit(result.status ?? 1);

for (const name of [
  "reasoning-ledger-web",
  "reasoning-ledger",
]) {
  const output = path.join(desktopRoot, "build", "backend", name);
  if (!existsSync(output)) throw new Error(`Backend build did not produce ${output}`);
}
