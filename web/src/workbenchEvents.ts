export function openLedgerEntry(slug: string): void {
  window.dispatchEvent(new CustomEvent("ledger-open", { detail: slug }));
}

export function openArtifact(name: string, projectId?: string): void {
  window.dispatchEvent(new CustomEvent("artifact-open", {
    detail: projectId ? { projectId, name } : name,
  }));
}
