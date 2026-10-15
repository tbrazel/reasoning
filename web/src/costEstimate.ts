import type { ModelPriceInfo } from "./api";

export interface TokenCostUsage {
  input: number;
  cached: number;
  output: number;
}

/**
 * Estimate OpenRouter spend from Codex's aggregate token counters.
 *
 * OpenRouter remains the authority for billed spend. Codex does not expose
 * the provider response ID, so this deliberately excludes request, image,
 * web-search, and separately metered reasoning charges that its token event
 * cannot identify.
 */
export function estimateOpenRouterTokenCost(
  usage: TokenCostUsage,
  price: ModelPriceInfo | null,
): number | null {
  if (
    price?.input_mtok_usd == null ||
    price.output_mtok_usd == null ||
    usage.input < 0 ||
    usage.cached < 0 ||
    usage.output < 0
  ) {
    return null;
  }
  const cached = Math.min(usage.input, usage.cached);
  const uncached = usage.input - cached;
  const cacheRate = price.cache_read_mtok_usd ?? price.input_mtok_usd;
  return (
    uncached * price.input_mtok_usd +
    cached * cacheRate +
    usage.output * price.output_mtok_usd
  ) / 1_000_000;
}

export function formatEstimatedCost(value: number): string {
  const digits = value < 0.0001 ? 6 : value < 0.01 ? 5 : value < 1 ? 4 : 2;
  return `$${value.toLocaleString(undefined, {
    minimumFractionDigits: 0,
    maximumFractionDigits: digits,
  })}`;
}
