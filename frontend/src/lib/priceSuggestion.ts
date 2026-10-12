// A suggested ask and bid for the item page, from the sales its own pivot holds.
// Every store mints its own credit and the server has no exchange rate between
// them, so prices are pooled at face value and the basis says so (COI-2784).
import type { ItemTrade } from "./itemsApi"

export interface PriceSuggestion {
  /** The middle sale price: what a seller can expect. */
  ask: number
  /** A quarter of the sales went for less: a buyer's opening offer. */
  bid: number
  low: number
  high: number
  sales: number
  currencies: number
}

/** The smallest value with at least `share` of the weight at or below it. */
function weightedQuantile(rows: { value: number; weight: number }[], share: number): number {
  const sorted = [...rows].sort((a, b) => a.value - b.value)
  const total = sorted.reduce((sum, row) => sum + row.weight, 0)
  let seen = 0
  for (const row of sorted) {
    seen += row.weight
    if (seen >= share * total) return row.value
  }
  return sorted[sorted.length - 1]!.value
}

// Barter has no price, and a roll-up counts as the sales it folded together.
export function suggestPrice(trades: readonly ItemTrade[]): PriceSuggestion | null {
  const rows = trades
    .filter((t) => t.unitPrice != null && Number.isFinite(t.unitPrice) && t.unitPrice > 0)
    .filter((t) => t.currency && t.currency !== "Barter" && t.currency !== "unknown")
    .map((t) => ({ value: t.unitPrice as number, weight: Math.max(1, t.eventCount ?? 1), currency: t.currency }))
  if (rows.length === 0) return null
  const values = rows.map((r) => r.value)
  return {
    ask: weightedQuantile(rows, 0.5),
    bid: weightedQuantile(rows, 0.25),
    low: Math.min(...values),
    high: Math.max(...values),
    sales: rows.reduce((sum, r) => sum + r.weight, 0),
    currencies: new Set(rows.map((r) => r.currency)).size,
  }
}
