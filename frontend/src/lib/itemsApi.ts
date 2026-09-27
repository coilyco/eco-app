import { hydrateNorms, type PriceNorm } from "./priceNorm"

// Typed clients for the item directory + per-item pivot (eco-app#81).
//
// `/preview/items.json` returns ItemIndex.to_dict() from eco_mcp_app/items.py:
// the union of every item ever bought / sold / crafted, each row carrying its
// trade count / currency volume / crafted quantity.
//
// `/preview/item.json?item=<id>` returns ItemPivot.to_dict(): every individual
// trade and crafting event that pivots on one item id, newest first. Party /
// crafter ids are already joined to names (falling back to "Citizen #<id>").

export interface ItemStat {
  item: string
  tradeCount: number
  tradeVolume: number
  craftCount: number
}

export interface ItemIndex {
  fetchedAtISO: string
  sourceBaseUrl: string
  totalItems: number
  items: ItemStat[]
  warnings: string[]
}

// One trade row for an item — mirrors a trades-ledger row (tradesApi.Trade).
export interface ItemTrade {
  /** Historical price norm (eco-app#8368). Optional while payloads gain it. */
  norm?: PriceNorm | null
  tradeType: string
  time: number
  day: number
  buyer: string
  seller: string
  shopOwner: string
  item: string
  quantity: number
  currency: string
  currencyAmount: number
  unitPrice: number | null
  store: string
  location: string
  direction: string
}

// One crafting/production event for an item.
export interface ItemCraft {
  actionType: string
  time: number
  day: number
  citizen: string
  station: string
  quantity: number
}

// One row of the merged, compressed, reverse-chrono feed (eco-app#92). A run of
// identical consecutive events collapses to one row: `runCount` is how many
// folded, `quantity` / `currencyAmount` are summed, `time` / `day` are the
// newest in the run, `spanSeconds` is how long the run took.
export interface ItemFeedRow {
  /** Historical price norm (eco-app#8368). Optional while payloads gain it. */
  norm?: PriceNorm | null
  kind: "craft" | "trade"
  time: number
  day: number
  actor: string
  actionType: string
  station: string
  quantity: number
  buyer: string
  seller: string
  currency: string
  unitPrice: number | null
  currencyAmount: number
  runCount: number
  spanSeconds: number
}

// One store's shelf offer feeding the actionable summary (supply or demand).
export interface ItemShelfOffer {
  /** Historical price norm (eco-app#8368). Optional while payloads gain it. */
  norm?: PriceNorm | null
  store: string
  owner: string
  price: number | null
  quantity: number | null
  currency: string
  source: string
}

// A supply (stores selling) or demand (stores buying) block.
export interface ItemShelfSide {
  storeCount: number
  totalQuantity: number
  offers: ItemShelfOffer[]
  capped: boolean
}

// One crafter who can make the item, ranked by quantity produced.
export interface ItemCrafter {
  name: string
  quantity: number
  events: number
}

// The actionable top-of-page summary: who makes it, what's for sale, who buys.
export interface ItemSummary {
  crafters: ItemCrafter[]
  supply: ItemShelfSide
  demand: ItemShelfSide
  live: boolean
}

export interface ItemPivot {
  /** Historical price norm for this item, shared by its feed and offers (eco-app#8368). */
  norm?: PriceNorm | null
  fetchedAtISO: string
  sourceBaseUrl: string
  item: string
  trades: ItemTrade[]
  crafts: ItemCraft[]
  feed: ItemFeedRow[]
  feedTruncated: boolean
  summary: ItemSummary
  worldClockS: number | null
  tradeCount: number
  tradeVolume: number
  craftCount: number
  craftQuantity: number
  warnings: string[]
}

export async function fetchItemIndex(signal?: AbortSignal): Promise<ItemIndex> {
  const resp = await fetch("/preview/items.json", { signal })
  if (!resp.ok) {
    throw new Error(`item index fetch failed: HTTP ${resp.status}`)
  }
  return (await resp.json()) as ItemIndex
}

export async function fetchItemPivot(item: string, signal?: AbortSignal): Promise<ItemPivot> {
  const resp = await fetch(`/preview/item.json?item=${encodeURIComponent(item)}`, { signal })
  if (!resp.ok) {
    throw new Error(`item pivot fetch failed: HTTP ${resp.status}`)
  }
  return hydrateNorms((await resp.json()) as ItemPivot)
}
