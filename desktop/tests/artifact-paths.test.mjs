import assert from "node:assert/strict";
import { mkdirSync, realpathSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";

import { resolveArtifactPath } from "../src/artifact-paths.mjs";

function fixture() {
  const root = path.join(tmpdir(), `reasoning-artifact-paths-${process.pid}-${Date.now()}-${Math.random()}`);
  const project = path.join(root, "projects", "project-a");
  const artifacts = path.join(project, "artifacts");
  mkdirSync(path.join(artifacts, "reports"), { recursive: true });
  writeFileSync(path.join(project, "project.json"), "{}", "utf8");
  writeFileSync(path.join(artifacts, "reports", "result.md"), "result", "utf8");
  return { root, artifacts };
}

test("resolves only artifacts within the selected project", () => {
  const { root, artifacts } = fixture();
  assert.equal(
    resolveArtifactPath({
      workbenchRoot: root,
      projectId: "project-a",
      artifactPath: "artifacts/reports/result.md",
    }),
    realpathSync(path.join(artifacts, "reports", "result.md")),
  );
  assert.equal(
    resolveArtifactPath({ workbenchRoot: root, projectId: "project-a", artifactPath: "reports/" }),
    realpathSync(path.join(artifacts, "reports")),
  );
});

test("refuses traversal, hidden paths, unknown projects, and escaping symlinks", () => {
  const { root, artifacts } = fixture();
  const outside = path.join(root, "outside.txt");
  writeFileSync(outside, "private", "utf8");
  symlinkSync(outside, path.join(artifacts, "escape.txt"));

  for (const [projectId, artifactPath] of [
    ["project-a", "../outside.txt"],
    ["project-a", ".private"],
    ["../project-a", "reports/result.md"],
    ["missing", "reports/result.md"],
    ["project-a", "escape.txt"],
  ]) {
    assert.throws(() => resolveArtifactPath({ workbenchRoot: root, projectId, artifactPath }));
  }
});
