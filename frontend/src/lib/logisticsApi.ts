// Typed client for the logistics board (/preview/logistics.json).
//
// This is the "what should I do" plane: cheapest-source lookups, best-resale,
// cross-store arbitrage spreads, and supply gaps folded out of the live store
// offers (and history-derived shelf). The backend is `logistics.py`'s
// `LogisticsReport.to_dict()` — this client mirrors that shape exactly. An
// earlier draft of this file guessed a flatter contract (`cheapestSources`,
// `arbitrage.buyPrice`, `supplyGaps.note`) the backend never emitted, so every
// logistics row rendered as `NaN`; it now tracks the real payload (eco-app#77).
//
// The backend sibling may still 404 (reset-gated live shelf, empty history), so
// fetchLogistics resolves to null on a miss and normalises every array so a
// partial payload never crashes the panel (eco-app#54).

import { fetchJsonOrNull } from "./api"
import type { PriceNorm } from "./priceNorm"

// One store's offer for one item on one side — `ShelfOffer.to_dict()`.
export interface ShelfOffer {
  /** Historical price norm (eco-app#8368). Optional while payloads gain it. */
  norm?: PriceNorm | null
  store: string
  owner: string
  storeKey: string
  item: string
  itemPretty: string
  currency: string
  side: string // "sell" | "buy"
  price: number
  quantity: number
  source: string // "live" | "history"
  lastDay: number | null
}

// Cheapest-source / best-resale row: a market with its ranked offers. `cheapest`
// is the lowest sell price; `best` the highest buy price.
export interface PricedBoardRow {
  /** Historical price norm (eco-app#8368). Optional while payloads gain it. */
  norm?: PriceNorm | null
  item: string
  itemPretty: string
  currency: string
  sellerCount?: number
  buyerCount?: number
  /** null when every seller of this market is out of stock (COI-2045). */
  cheapest?: number | null
  best?: number
  soldOutCount?: number
  /** Present only when every seller is out of stock; `offers` is then empty. */
  soldOutOffers?: ShelfOffer[]
  soldOutNote?: string
  offers: ShelfOffer[]
}

export interface ArbitrageSpread {
  /** Historical price norm (eco-app#8368). Optional while payloads gain it. */
  norm?: PriceNorm | null
  item: string
  itemPretty: string
  currency: string
  spread: number
  spreadPct: number
  volume: number
  opportunity: number
  storeCount: number
  buyFrom: ShelfOffer
  sellTo: ShelfOffer
}

// Who needs a supply-gap item — one citizen's folded demand.
export interface GapBuyer {
  /** Historical price norm (eco-app#8368). Optional while payloads gain it. */
  norm?: PriceNorm | null
  owner: string
  store: string
  quantity: number
  price: number
}

export type GapReason = "no_supply" | "thin_supply" | "overpriced"

export interface SupplyGap {
  /** Historical price norm (eco-app#8368). Optional while payloads gain it. */
  norm?: PriceNorm | null
  item: string
  itemPretty: string
  currency: string
  reason: GapReason
  sellerCount: number
  buyerCount: number
  demandQty: number
  supplyQty: number
  buyPrice: number | null
  cheapestSell: number | null
  median: number | null
  overMedianPct: number | null
  buyers: GapBuyer[]
}

export interface LogisticsBoard {
  view: string
  fetchedAtISO: string
  sourceBaseUrl: string
  live: boolean
  totalOffers: number
  totalStores: number
  cheapest: PricedBoardRow[]
  resale: PricedBoardRow[]
  arbitrage: ArbitrageSpread[]
  supplyGaps: SupplyGap[]
  warnings: string[]
}

function asArray<T>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : []
}

export async function fetchLogistics(signal?: AbortSignal): Promise<LogisticsBoard | null> {
  const body = await fetchJsonOrNull<Partial<LogisticsBoard>>("/preview/logistics.json", signal)
  if (!body) return null
  return {
    view: body.view ?? "logistics",
    fetchedAtISO: body.fetchedAtISO ?? "",
    sourceBaseUrl: body.sourceBaseUrl ?? "",
    live: Boolean(body.live),
    totalOffers: body.totalOffers ?? 0,
    totalStores: body.totalStores ?? 0,
    cheapest: asArray<PricedBoardRow>(body.cheapest),
    resale: asArray<PricedBoardRow>(body.resale),
    arbitrage: asArray<ArbitrageSpread>(body.arbitrage),
    supplyGaps: asArray<SupplyGap>(body.supplyGaps).map((g) => ({
      ...g,
      buyers: asArray<GapBuyer>(g.buyers),
    })),
    warnings: asArray<string>(body.warnings),
  }
}
