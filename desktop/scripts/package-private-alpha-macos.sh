#!/usr/bin/env bash
set -euo pipefail

script_directory=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
desktop_root=$(cd "$script_directory/.." && pwd)
repository_root=$(cd "$desktop_root/.." && pwd)

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  exit 2
}

[[ "$(uname -s)" == "Darwin" ]] || fail "this package must be built on macOS"
[[ "$(uname -m)" == "arm64" ]] || fail "this command builds the Apple Silicon package only"

for command in git node npm uv xcode-select hdiutil codesign lipo curl shasum; do
  command -v "$command" >/dev/null 2>&1 || fail "$command is required"
done
xcode-select -p >/dev/null 2>&1 || fail "Xcode command-line tools are unavailable"

node_version=$(node -p 'process.versions.node')
[[ "$node_version" == 24.* ]] || fail "Node.js 24 is required; found $node_version"

cd "$repository_root"
source_revision=$(git rev-parse HEAD)
[[ "$source_revision" =~ ^[0-9a-f]{40}$ ]] || fail "source revision is not an exact commit"
[[ -z "$(git status --porcelain --untracked-files=all)" ]] || fail "the Reasoning checkout has tracked or untracked changes"

printf 'Building Reasoning from %s\n' "$source_revision"
npm --prefix web ci --ignore-scripts
npm --prefix desktop ci

CSC_IDENTITY_AUTO_DISCOVERY=false UV_PYTHON=3.12 \
  npm --prefix desktop run package:mac:arm64 -- \
    --config.mac.identity=- \
    --config.mac.entitlements=assets/entitlements.private-alpha.mac.plist \
    --config.mac.entitlementsInherit=assets/entitlements.private-alpha.mac.plist

version=$(node -p "require('$desktop_root/package.json').version")
artifact_stem="Reasoning-${version}-arm64"
dmg="$desktop_root/dist/${artifact_stem}.dmg"
archive="$desktop_root/dist/${artifact_stem}.zip"
[[ -f "$dmg" ]] || fail "package did not produce $dmg"
[[ -f "$archive" ]] || fail "package did not produce $archive"

mountpoint=$(mktemp -d "${TMPDIR:-/tmp}/reasoning-desktop-dmg.XXXXXX")
runtime_root=$(mktemp -d "${TMPDIR:-/tmp}/reasoning-desktop-smoke.XXXXXX")
backend_pid=""
mounted=0
cleanup() {
  if [[ -n "$backend_pid" ]]; then
    kill "$backend_pid" >/dev/null 2>&1 || true
    wait "$backend_pid" >/dev/null 2>&1 || true
  fi
  if [[ "$mounted" == 1 ]]; then
    hdiutil detach -quiet "$mountpoint" >/dev/null 2>&1 || hdiutil detach -force -quiet "$mountpoint" >/dev/null 2>&1 || true
  fi
  rm -rf -- "$mountpoint" "$runtime_root"
}
trap cleanup EXIT

hdiutil attach -quiet -nobrowse -readonly -mountpoint "$mountpoint" "$dmg"
mounted=1
app="$mountpoint/Reasoning.app"
resources="$app/Contents/Resources"
backend="$resources/backend/reasoning-ledger-web"
[[ -d "$app" ]] || fail "DMG does not contain the application bundle"
[[ -x "$backend" ]] || fail "DMG does not contain the frozen backend"
codesign --verify --deep --strict --verbose=4 "$app"
[[ "$(lipo -archs "$backend")" == "arm64" ]] || fail "frozen backend is not Apple Silicon"

port=$(uv run --no-project --python 3.12 python -c \
  'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')
PORT="$port" \
REASONING_DESKTOP_RUNTIME=1 \
REASONING_WEB_DIST="$resources/web" \
REASONING_WORKBENCH_ROOT="$runtime_root/workbench" \
  "$backend" >"$runtime_root/backend.log" 2>&1 &
backend_pid=$!

ready=0
for _attempt in {1..60}; do
  if curl --fail --silent --show-error --max-time 2 "http://127.0.0.1:${port}/api/health" >/dev/null 2>&1; then
    ready=1
    break
  fi
  if ! kill -0 "$backend_pid" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
if [[ "$ready" != 1 ]]; then
  sed -n '1,160p' "$runtime_root/backend.log" >&2
  fail "packaged backend did not become ready"
fi
auth_state=$(curl --fail --silent --show-error --max-time 5 \
  "http://127.0.0.1:${port}/api/desktop/auth" | \
  uv run --no-project --python 3.12 python -c 'import json,sys; print(json.load(sys.stdin)["state"])')
[[ "$auth_state" == "signed_out" ]] || fail "packaged backend did not start signed out"

kill "$backend_pid" >/dev/null 2>&1 || true
wait "$backend_pid" >/dev/null 2>&1 || true
backend_pid=""
hdiutil detach -quiet "$mountpoint"
mounted=0

(
  cd "$desktop_root/dist"
  shasum -a 256 "${artifact_stem}.dmg" "${artifact_stem}.zip" > SHA256SUMS.txt
  printf '%s\n' "$source_revision" > SOURCE_COMMIT.txt
  shasum -a 256 -c SHA256SUMS.txt
)

printf '\nPackage and smoke check succeeded.\n'
printf 'Source: %s\n' "$source_revision"
printf 'DMG: %s\n' "$dmg"
printf 'ZIP: %s\n' "$archive"
printf 'Checksums: %s\n' "$desktop_root/dist/SHA256SUMS.txt"
