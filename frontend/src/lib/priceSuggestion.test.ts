import { describe, expect, it } from "vitest"
import type { ItemTrade } from "./itemsApi"
import { suggestPrice } from "./priceSuggestion"

const trade = (unitPrice: number | null, currency: string, eventCount?: number): ItemTrade => ({
  tradeType: "CurrencyTrade",
  time: 1,
  day: 2,
  buyer: "b",
  seller: "s",
  shopOwner: "s",
  item: "HewnLogItem",
  quantity: 1,
  currency,
  currencyAmount: unitPrice ?? 0,
  unitPrice,
  store: "StoreItem",
  location: "",
  direction: "buy",
  eventCount,
})

// The six credits and twenty sales on HewnLog's market, 2026-10-12.
const HEWN_LOG: [number, string, number][] = [
  [0.14, "Zynx Credit", 6],
  [0.3, "Upgrade Credit", 4],
  [0.29, "Ice Dragon Credit", 3],
  [0.3, "reihtnog Credit", 3],
  [0.4, "Cookie Credit", 3],
  [0.4, "elizacorn Credit", 1],
]
const hewnLog = () => HEWN_LOG.flatMap(([p, c, n]) => Array.from({ length: n }, () => trade(p, c)))

describe("suggestPrice", () => {
  it("pools sales across credits at face value, and counts each credit", () => {
    expect(suggestPrice(hewnLog())).toEqual({ ask: 0.3, bid: 0.14, low: 0.14, high: 0.4, sales: 20, currencies: 6 })
  })

  it("counts a rolled-up row as the sales it folded together", () => {
    const s = suggestPrice([trade(1, "A", 9), trade(5, "A")])
    expect(s).toMatchObject({ ask: 1, sales: 10 })
  })

  it("is null with no priced sale, and skips barter and unpriced rows", () => {
    expect(suggestPrice([])).toBeNull()
    expect(suggestPrice([trade(null, "A"), trade(0, "A"), trade(3, "Barter"), trade(3, "")])).toBeNull()
  })

  it("stays one number for one sale", () => {
    expect(suggestPrice([trade(2, "A")])).toEqual({ ask: 2, bid: 2, low: 2, high: 2, sales: 1, currencies: 1 })
  })
})
