import { ModelPrice, OrganizationPrices } from "./organization-api";

/** Chat selectors omit transport-only aliases; admission still accepts live router IDs. */
export function chatModel(id: string): boolean {
  return !/(?:^|[/:_-])(?:batch|embedding|embeddings|rerank|reranker)(?:$|[/:_-])/i.test(id);
}

/** Default to agent-capable entries without turning the catalog into an admission whitelist. */
export function usefulChatModel(id: string, model?: ModelPrice): boolean {
  return chatModel(id) && (!model || model.execution_source === "bot" || id.startsWith("openrouter/")
    || model.supports_tools == null && model.supports_reasoning == null
    || model.supports_tools === true || model.supports_reasoning === true);
}

export function recommendedAllowedModels(catalog: OrganizationPrices | null): ModelPrice[] {
  const allowed = new Set(catalog?.allowed_models ?? []);
  const metadata = new Map((catalog?.models ?? []).map((model) => [model.id, model]));
  return (catalog?.recommendations?.model_ids ?? []).flatMap((id) => {
    const model = metadata.get(id); return model && allowed.has(id) ? [model] : [];
  });
}

export function modelOptionGroups(allowedModels: string[], catalog: OrganizationPrices | null, query = "") {
  const allowed = new Set(allowedModels);
  const matching = (id: string) => {
    const metadata = catalog?.models.find((model) => model.id === id);
    return chatModel(id) && (id + " " + (metadata?.name ?? "")).toLowerCase().includes(query.trim().toLowerCase());
  };
  const recommended = (catalog?.recommendations?.model_ids ?? []).filter((id) => allowed.has(id) && matching(id));
  const preferred = new Set(recommended);
  return { recommended, other: allowedModels.filter((id) => !preferred.has(id) && matching(id)) };
}
