import { existsSync, realpathSync } from "node:fs";
import path from "node:path";

function isWithin(root, candidate) {
  const relative = path.relative(root, candidate);
  return relative === "" || (
    relative !== ".." &&
    !relative.startsWith(`..${path.sep}`) &&
    !path.isAbsolute(relative)
  );
}

function validateProjectId(projectId) {
  if (
    typeof projectId !== "string" ||
    projectId.length === 0 ||
    projectId === "." ||
    projectId === ".." ||
    projectId === "projects" ||
    projectId.includes("/") ||
    projectId.includes("\\")
  ) {
    throw new Error("Invalid project identifier");
  }
}

function normalizeArtifactPath(artifactPath) {
  if (typeof artifactPath !== "string") throw new Error("Invalid artifact path");
  const portable = artifactPath.replaceAll("\\", "/").replace(/^artifacts\//, "");
  const parts = portable.split("/").filter(Boolean);
  if (
    path.posix.isAbsolute(portable) ||
    parts.some((part) => part === "." || part === ".." || part.startsWith("."))
  ) {
    throw new Error("Invalid artifact path");
  }
  return parts.join(path.sep);
}

/** Resolve an artifact selected by the trusted workbench UI without ever
 * accepting an arbitrary host path from the renderer. Both the current
 * <root>/projects/<id> layout and the legacy <root>/<id> layout remain
 * readable. Symlinks that leave the artifact tree are refused. */
export function resolveArtifactPath({ workbenchRoot, projectId, artifactPath }) {
  validateProjectId(projectId);
  const root = realpathSync(workbenchRoot);
  const projectCandidates = [
    path.join(root, "projects", projectId),
    path.join(root, projectId),
  ];
  const project = projectCandidates.find((candidate) =>
    existsSync(path.join(candidate, "project.json")),
  );
  if (!project) throw new Error("Unknown project");

  const projectRoot = realpathSync(project);
  if (!isWithin(root, projectRoot)) throw new Error("Project escapes the workbench");
  const artifacts = path.join(projectRoot, "artifacts");
  if (!existsSync(artifacts)) throw new Error("Artifact directory is unavailable");
  const artifactsRoot = realpathSync(artifacts);
  if (!isWithin(projectRoot, artifactsRoot)) throw new Error("Artifact directory escapes its project");
  const relative = normalizeArtifactPath(artifactPath);
  const unresolved = path.join(artifactsRoot, relative);
  if (!existsSync(unresolved)) throw new Error("Unknown artifact");
  const resolved = realpathSync(unresolved);
  if (!isWithin(artifactsRoot, resolved)) throw new Error("Artifact escapes its project");
  return resolved;
}
