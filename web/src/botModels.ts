// Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
import type { BotModel, ModelPrice, OrganizationPrices } from "./organization-api";

export function withBotModels(catalog: OrganizationPrices | null, bots: BotModel[]): OrganizationPrices | null {
  if (!bots.length) return catalog;
  const local: ModelPrice[] = bots.map(bot => ({ id: bot.id, name: bot.name, provider: bot.engine, execution_source: "bot",
    reasoning_efforts: bot.efforts ?? [],
    bot_online: bot.online ?? true,
    input_usd_per_million: null, output_usd_per_million: null, description: "Runs through its owner's authenticated CLI account.",
    modality: "text", input_modalities: ["text"], output_modalities: ["text"], supported_parameters: [], supports_tools: true,
    supports_reasoning: true, request_usd: null, cache_read_usd_per_million: null, cache_write_usd_per_million: null,
    reasoning_usd_per_million: null, image_usd: null, web_search_usd: null, context_length: 0, max_completion_tokens: null,
  }));
  return { ...(catalog ?? { fetched_at: 0, routing: "Owner's CLI", source: "Local bot", allowed_models: [] }),
    models: [...(catalog?.models ?? []), ...local], allowed_models: [...(catalog?.allowed_models ?? []), ...bots.map(bot => bot.id)] };
}
