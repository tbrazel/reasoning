// Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
import type { OrganizationPrices } from "./organization-api";

export function availableEfforts(catalog: OrganizationPrices | null, model: string): string[] {
  return catalog?.models.find(item => item.id === model)?.reasoning_efforts ?? [];
}
