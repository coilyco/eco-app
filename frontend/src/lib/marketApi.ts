// Typed client for the market price-intelligence plane (/preview/market.json).
//
// The endpoint returns MarketIntelligence.to_dict() from eco_mcp_app/market.py:
// per-item, per-currency price series folded out of the trades ledger. Each
// ItemMarket carries daily buckets (median / min / max / units / trades), a
// median & latest price, and a short-vs-long-window trend verdict (rising /
// falling / flat, or "insufficient" when the history is too thin). Markets
// arrive already sorted by (totalTrades, totalVolume) descending, so the head
// of the list is the most-traded set. Resolves to null on a missing / failing
// endpoint so the /trade page degrades panel-by-panel (eco-app#54).

import { fetchJsonOrNull } from "./api"
import type { PriceNorm } from "./priceNorm"

export type MarketTrend = "rising" | "falling" | "flat" | "insufficient"

export interface PriceBucket {
  day: number
  median: number
  min: number
  max: number
  volume: number
  trades: number
}

export interface ItemMarket {
  /** Historical price norm (eco-app#8368). Optional while payloads gain it. */
  norm?: PriceNorm | null
  item: string
  itemPretty: string
  currency: string
  buckets: PriceBucket[]
  medianPrice: number
  latestPrice: number
  latestDay: number
  trend: MarketTrend
  trendDeltaPct: number | null
  shortMedian: number | null
  longMedian: number | null
  totalVolume: number
  totalTrades: number
}

export interface MarketIntelligence {
  view: string
  fetchedAtISO: string
  sourceBaseUrl: string
  totalTrades: number
  markets: ItemMarket[]
  warnings: string[]
}

export async function fetchMarket(signal?: AbortSignal): Promise<MarketIntelligence | null> {
  const body = await fetchJsonOrNull<MarketIntelligence>("/preview/market.json", signal)
  if (!body || !Array.isArray(body.markets)) return null
  return body
}
