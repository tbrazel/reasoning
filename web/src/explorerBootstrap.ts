import type { ProjectInfo } from "./api";

export type ExplorerLoadState = "loading" | "cached" | "fresh" | "error";

export interface ExplorerBootstrap {
  version: 1;
  generated_at: string;
  projects: ProjectInfo[];
}

export function parseExplorerBootstrap(value: string | null): ExplorerBootstrap | null {
  if (!value) return null;
  try {
    const parsed = JSON.parse(value) as Partial<ExplorerBootstrap>;
    if (
      parsed.version !== 1 ||
      typeof parsed.generated_at !== "string" ||
      !Array.isArray(parsed.projects) ||
      parsed.projects.some((project) => !project || typeof project !== "object")
    ) return null;
    return parsed as ExplorerBootstrap;
  } catch {
    return null;
  }
}

export function readExplorerBootstrap(): ExplorerBootstrap | null {
  if (typeof document === "undefined") return null;
  return parseExplorerBootstrap(
    document.getElementById("reasoning-explorer-bootstrap")?.textContent ?? null,
  );
}

