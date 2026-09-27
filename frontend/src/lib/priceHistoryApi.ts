import { fetchJsonOrNull } from "./api"
import type { PriceBucket } from "./marketApi"
import type { PriceNorm } from "./priceNorm"

export type PriceHistoryState =
  | "no_data"
  | "thin"
  | "stale"
  | "multimodal"
  | "missing_recipes"
  | "missing_progression"
  | "unobserved_unlocks"

export interface DistributionBucket {
  low: number
  high: number
  count: number
}

export interface PriceDistribution {
  sampleCount: number
  sampleState: "no_data" | "thin" | "representative"
  freshnessState: "unknown" | "stale" | "current"
  shapeState: "unknown" | "multimodal" | "observed"
  median: number | null
  min: number | null
  max: number | null
  percentiles: {
    p10: number
    p25: number
    p50: number
    p75: number
    p90: number
  } | null
  histogram: DistributionBucket[]
}

export interface SpecialtyUnlock {
  skill: string
  skillPretty: string
  day: number | null
  time: number | null
  status: "observed" | "unobserved" | "progression_unavailable"
  recipeVariants: string[]
}

export interface ItemPriceHistory {
  /** Historical price norm (eco-app#8368). Optional while payloads gain it. */
  norm?: PriceNorm | null
  view: "item-price-history"
  fetchedAtISO: string
  item: string
  itemPretty: string
  currency: string
  scope: {
    label: string
    cycle: "current"
    progressionRulesVersion: string
    historicalCyclesIncluded: false
  }
  window: {
    label: string
    firstObservedDay: number | null
    latestPriceDay: number | null
    observedThroughDay: number | null
  }
  distribution: PriceDistribution
  daily: PriceBucket[]
  totalVolume: number
  recipes: Array<{
    name: string
    displayName: string
    product: string
    skill: string | null
    skillPretty: string | null
    skillLevel: number
  }>
  specialtyUnlocks: SpecialtyUnlock[]
  states: PriceHistoryState[]
  warnings: string[]
}

export async function fetchItemPriceHistory(
  item: string,
  currency: string,
  signal?: AbortSignal,
): Promise<ItemPriceHistory | null> {
  const params = new URLSearchParams({ item, currency })
  const body = await fetchJsonOrNull<ItemPriceHistory>(
    `/preview/price-history.json?${params.toString()}`,
    signal,
  )
  if (!body || body.view !== "item-price-history" || !body.distribution) return null
  return body
}
