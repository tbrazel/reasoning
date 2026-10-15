export type NavigationState = { topic?: string; collapsedProjects: string[]; collapsedChannels: string[] };
const key = (user: string, organization: string) => `reasoning.navigation.v1/${user}/${organization}`;
export function readNavigation(user: string, organization: string): NavigationState {
  const empty = { collapsedProjects: [], collapsedChannels: [] };
  if (!user || !organization) return empty;
  try {
    const value = JSON.parse(localStorage.getItem(key(user, organization)) ?? "null");
    if (!value || typeof value !== "object") return empty;
    return { topic: typeof value.topic === "string" ? value.topic : undefined,
      collapsedProjects: Array.isArray(value.collapsedProjects) ? value.collapsedProjects.filter((id: unknown) => typeof id === "string") : [],
      collapsedChannels: Array.isArray(value.collapsedChannels) ? value.collapsedChannels.filter((id: unknown) => typeof id === "string") : [] };
  } catch { return empty; }
}
export function saveNavigation(user: string, organization: string, change: Partial<NavigationState>): void {
  if (!user || !organization) return;
  try { localStorage.setItem(key(user, organization), JSON.stringify({ ...readNavigation(user, organization), ...change })); }
  catch { /* Browsing remains functional when browser storage is unavailable. */ }
}
export type Spend = { actual_microusd: number; unknown_calls: number; external_runs: number };
export type NavigationUsage = { projects: Record<string, Spend>; channels: Record<string, Spend>; topics: Record<string, Spend> };
