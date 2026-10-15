export function preferredRuntimePaths(platform) {
  if (platform !== "darwin") {
    return [];
  }
  // Homebrew's versioned Node formula is keg-only, so Finder-launched apps do
  // not normally see it. Prefer the supported runtime before an older or
  // partially upgraded unversioned Homebrew Node installation.
  return ["/opt/homebrew/opt/node@24/bin", "/usr/local/opt/node@24/bin"];
}
