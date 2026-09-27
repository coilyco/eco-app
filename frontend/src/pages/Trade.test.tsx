import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"
import { MemoryRouter } from "react-router-dom"
import Trade from "./Trade"

const MARKET = {
  view: "market",
  fetchedAtISO: "2026-06-12T13:00:00+00:00",
  sourceBaseUrl: "http://x:3001",
  totalTrades: 42,
  markets: [
    {
      item: "IronIngotItem",
      itemPretty: "Iron Ingot",
      currency: "Credit",
      buckets: [
        { day: 1, median: 20, min: 18, max: 22, volume: 10, trades: 2 },
        { day: 2, median: 25, min: 24, max: 26, volume: 8, trades: 1 },
      ],
      medianPrice: 22.5,
      latestPrice: 25,
      latestDay: 2,
      trend: "rising",
      trendDeltaPct: 12,
      shortMedian: 25,
      longMedian: 20,
      totalVolume: 180,
      totalTrades: 20,
    },
    {
      item: "WheatItem",
      itemPretty: "Wheat",
      currency: "Credit",
      buckets: [
        { day: 1, median: 6, min: 5, max: 7, volume: 40, trades: 3 },
        { day: 2, median: 5, min: 4, max: 6, volume: 30, trades: 2 },
      ],
      medianPrice: 5.5,
      latestPrice: 5,
      latestDay: 2,
      trend: "falling",
      trendDeltaPct: -10,
      shortMedian: 5,
      longMedian: 6,
      totalVolume: 120,
      totalTrades: 12,
    },
    {
      item: "BoardItem",
      itemPretty: "Board",
      currency: "Credit",
      buckets: [],
      medianPrice: 3,
      latestPrice: 3,
      latestDay: 2,
      trend: "flat",
      trendDeltaPct: 0,
      shortMedian: 3,
      longMedian: 3,
      totalVolume: 60,
      totalTrades: 6,
    },
  ],
  warnings: [],
}

const STORES = {
  view: "eco_stores",
  fetchedAtISO: "2026-06-12T13:00:00+00:00",
  sourceBaseUrl: "http://x:3001",
  totalTrades: 42,
  perTypeCounts: {},
  stores: [
    {
      storeKey: "s1",
      label: "Iron Emporium",
      owner: "ekans",
      ownerId: "1",
      location: "1,2,3",
      storeObject: "StoreItem",
      storeObjectPretty: "Store",
      tradeCount: 20,
      totalVolume: 400,
      sellCount: 18,
      buyCount: 2,
      uniqueCounterparties: 9,
      lastDay: 12,
      currencies: [["Credit", 400]],
      topItems: [],
      topCounterparties: [],
    },
    {
      storeKey: "s2",
      label: "Wheat Stand",
      owner: "coilysiren",
      ownerId: "2",
      location: "4,5,6",
      storeObject: "StoreItem",
      storeObjectPretty: "Store",
      tradeCount: 12,
      totalVolume: 220,
      sellCount: 10,
      buyCount: 2,
      uniqueCounterparties: 5,
      lastDay: 11,
      currencies: [["Credit", 220]],
      topItems: [],
      topCounterparties: [],
    },
  ],
  traders: [
    {
      name: "ekans",
      citizenId: "1",
      tradeCount: 20,
      totalVolume: 400,
      sellVolume: 380,
      buyVolume: 20,
      uniqueCounterparties: 9,
      lastDay: 12,
      storesOperated: [],
      topSells: [],
      topBuys: [],
    },
    {
      name: "coilysiren",
      citizenId: "2",
      tradeCount: 12,
      totalVolume: 220,
      sellVolume: 200,
      buyVolume: 20,
      uniqueCounterparties: 5,
      lastDay: 11,
      storesOperated: [],
      topSells: [],
      topBuys: [],
    },
  ],
  totalStores: 2,
  totalTraders: 2,
  warnings: [],
}

// Mirrors the real backend `LogisticsReport.to_dict()` (logistics.py): boards
// carry nested `offers` / `buyFrom` / `sellTo`, and supply gaps carry a
// structured `reason` plus the per-citizen `buyers` demand (eco-app#77).
const LOGISTICS = {
  view: "logistics",
  fetchedAtISO: "2026-06-12T13:00:00+00:00",
  sourceBaseUrl: "http://x:3001",
  live: false,
  totalOffers: 5,
  totalStores: 3,
  cheapest: [
    {
      item: "IronIngotItem",
      itemPretty: "Iron Ingot",
      currency: "Credit",
      sellerCount: 1,
      cheapest: 24,
      offers: [
        {
          store: "Iron Emporium",
          owner: "ekans",
          storeKey: "iron|ekans",
          item: "IronIngotItem",
          itemPretty: "Iron Ingot",
          currency: "Credit",
          side: "sell",
          price: 24,
          quantity: 10,
          source: "history",
          lastDay: 12,
        },
      ],
    },
  ],
  resale: [],
  arbitrage: [
    {
      item: "WheatItem",
      itemPretty: "Wheat",
      currency: "Credit",
      spread: 3,
      spreadPct: 75,
      volume: 20,
      opportunity: 60,
      storeCount: 2,
      buyFrom: {
        store: "Wheat Stand",
        owner: "onix",
        storeKey: "wheat|onix",
        item: "WheatItem",
        itemPretty: "Wheat",
        currency: "Credit",
        side: "sell",
        price: 4,
        quantity: 20,
        source: "history",
        lastDay: 11,
      },
      sellTo: {
        store: "Iron Emporium",
        owner: "ekans",
        storeKey: "iron|ekans",
        item: "WheatItem",
        itemPretty: "Wheat",
        currency: "Credit",
        side: "buy",
        price: 7,
        quantity: 30,
        source: "history",
        lastDay: 12,
      },
    },
  ],
  supplyGaps: [
    {
      item: "BoardItem",
      itemPretty: "Board",
      currency: "Credit",
      reason: "no_supply",
      sellerCount: 0,
      buyerCount: 2,
      demandQty: 45,
      supplyQty: 0,
      buyPrice: 6,
      cheapestSell: null,
      median: null,
      overMedianPct: null,
      buyers: [
        { owner: "geodude", store: "Geodude's Yard", quantity: 30, price: 6 },
        { owner: "onix", store: "Onix's Depot", quantity: 15, price: 5 },
      ],
    },
  ],
  warnings: [],
}

const WATCHERS_REPORT = {
  view: "watcher_hits",
  advanced: false,
  hits: [
    {
      id: "w_abc123",
      kind: "price",
      value: "IronIngotItem",
      op: "under",
      threshold: 2.5,
      label: "cheap iron",
      server: null,
      lastSeen: 0,
      createdAt: 0,
      describe: "Iron Ingot under 2.5",
      feed: [],
      feedCount: 2,
      display: { matchCount: 5, recent: [], bestUnitPrice: 2, totalVolume: 40, lastMatchTime: 300000 },
      newLastSeen: 300000,
    },
  ],
}

// The trades ledger folded into /trade (eco-app#90).
const TRADES = {
  fetchedAtISO: "2026-06-12T13:00:00+00:00",
  sourceBaseUrl: "http://x:3001",
  totalTrades: 335,
  detailedTrades: 2,
  rollupRows: 1,
  rollupTrades: 333,
  perTypeCounts: { CurrencyTrade: 335 },
  trades: [
    {
      tradeType: "CurrencyTrade",
      time: 1000,
      day: 12,
      buyer: "onix",
      seller: "ekans",
      shopOwner: "ekans",
      item: "IronIngotItem",
      quantity: 5,
      currency: "Credit",
      currencyAmount: 120,
      unitPrice: 24,
      store: "Iron Emporium",
      location: "1,2,3",
      direction: "sell",
    },
    {
      tradeType: "CurrencyTrade",
      time: 900,
      day: 11,
      buyer: "geodude",
      seller: "coilysiren",
      shopOwner: "coilysiren",
      item: "WheatItem",
      quantity: 10,
      currency: "Credit",
      currencyAmount: 50,
      unitPrice: 5,
      store: "Wheat Stand",
      location: "4,5,6",
      direction: "sell",
    },
  ],
  totalCurrencyVolume: 4907,
  byItem: [
    ["IronIngotItem", 20, 480],
    ["WheatItem", 12, 60],
  ],
  byCurrency: [["Credit", 4907]],
  topBuyers: [
    ["onix", 300],
    ["geodude", 120],
  ],
  topSellers: [
    ["ekans", 400],
    ["coilysiren", 200],
  ],
  priceSeries: {
    IronIngotItem: [
      [11, 22],
      [12, 24],
    ],
  },
  warnings: [],
}

const NOT_FOUND = Symbol("404")

function jsonResponse(payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  })
}

// The page fans out to five planes; a URL check routes each to its payload. A
// plane set to NOT_FOUND resolves to a 404 so we can exercise graceful degrade.
function stub(
  overrides: {
    market?: unknown
    stores?: unknown
    logistics?: unknown
    trades?: unknown
    watchers?: unknown
  } = {},
) {
  const planes: Record<string, unknown> = {
    "market.json": overrides.market ?? MARKET,
    "stores.json": overrides.stores ?? STORES,
    "logistics.json": overrides.logistics ?? LOGISTICS,
    "get_trades.json": overrides.trades ?? TRADES,
    "watchers.json": overrides.watchers ?? { hits: [] },
  }
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      const u = String(url)
      const key = Object.keys(planes).find((k) => u.includes(k))
      const payload = key ? planes[key] : null
      if (payload === NOT_FOUND || payload == null) {
        return Promise.resolve(new Response("not found", { status: 404 }))
      }
      return Promise.resolve(jsonResponse(payload))
    }),
  )
}

function renderTrade(entry = "/trade") {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Trade />
    </MemoryRouter>,
  )
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe("Trade", () => {
  it("renders the movers, drill chart, and cross-links", async () => {
    stub()
    renderTrade()

    // Rising / falling movers land in their own lists.
    await waitFor(() => {
      expect(within(screen.getByTestId("risers")).getByText("Iron Ingot")).toBeInTheDocument()
    })
    expect(within(screen.getByTestId("fallers")).getByText("Wheat")).toBeInTheDocument()
    // Default drill is the busiest market (Iron Ingot), charted.
    const drill = screen.getByTestId("drill")
    expect(within(drill).getByTestId("price-chart")).toBeInTheDocument()
    expect(screen.getByTestId("link-crafting")).toHaveAttribute("href", "/crafting")
    expect(screen.getByTestId("link-jobs")).toHaveAttribute("href", "/jobs")
  })

  it("folds the row-level trades ledger into the page", async () => {
    stub()
    renderTrade()

    await waitFor(() => {
      expect(screen.getByTestId("ledger")).toBeInTheDocument()
    })
    // Newest-first row-level trades.
    expect(screen.getAllByTestId("trade-row").length).toBeGreaterThan(0)
    expect(screen.getByTestId("trades-table")).toHaveTextContent("ekans")
    expect(screen.getByText("Detailed trades ledger")).toBeInTheDocument()
  })

  it("deep-links a drill target via ?q=", async () => {
    stub()
    renderTrade("/trade?q=wheat")

    await waitFor(() => {
      expect(screen.getByTestId("trade-filter")).toHaveValue("wheat")
    })
    const drill = screen.getByTestId("drill")
    // Median price of Wheat (5.5) shows in the drill stat tiles.
    expect(within(drill).getByText("5.5")).toBeInTheDocument()
  })

  it("links market names to their pivots while retaining the ledger filter action", async () => {
    stub()
    renderTrade()

    await waitFor(() => {
      expect(screen.getByTestId("most-traded")).toBeInTheDocument()
    })
    const list = within(screen.getByTestId("most-traded"))
    expect(list.getByRole("link", { name: "Wheat" })).toHaveAttribute("href", "/item?id=Wheat")
    fireEvent.click(list.getByRole("button", { name: "Filter trade ledger by Wheat" }))
    expect(screen.getByTestId("trade-filter")).toHaveValue("Wheat")
  })

  it("renders the store directory", async () => {
    stub()
    renderTrade()

    await waitFor(() => {
      expect(screen.getByTestId("directory")).toBeInTheDocument()
    })
    expect(within(screen.getByTestId("store-list")).getByText("Iron Emporium")).toBeInTheDocument()
  })

  it("renders the logistics board when the plane has landed", async () => {
    stub()
    renderTrade()

    await waitFor(() => {
      expect(screen.getByTestId("logistics")).toBeInTheDocument()
    })
    expect(screen.getByTestId("arbitrage-row")).toHaveTextContent("Wheat Stand")
    // The "Cheapest source" board was dropped as noise (eco-app#95).
    expect(screen.queryByTestId("cheapest-list")).toBeNull()
    // Supply gap now names the reason and WHO needs it, not a prose note.
    const gaps = screen.getByTestId("gaps-list")
    expect(gaps).toHaveTextContent("Board")
    expect(gaps).toHaveTextContent("no supply")
    expect(within(gaps).getByTestId("gap-who")).toHaveTextContent("geodude")
    expect(within(gaps).getByTestId("gap-who")).toHaveTextContent("onix")
  })

  it("renders every supply gap, not just the first eight (eco-app#95)", async () => {
    const manyGaps = {
      ...LOGISTICS,
      supplyGaps: Array.from({ length: 22 }, (_, i) => ({
        item: `Item${i}`,
        itemPretty: `Item ${i}`,
        currency: "Credit",
        reason: "no_supply" as const,
        sellerCount: 0,
        buyerCount: 1,
        demandQty: 10,
        supplyQty: 0,
        buyPrice: 1,
        cheapestSell: null,
        median: null,
        overMedianPct: null,
        buyers: [{ owner: "onix", store: "Onix's Depot", quantity: 10, price: 1 }],
      })),
    }
    stub({ logistics: manyGaps })
    renderTrade()

    await waitFor(() => {
      expect(screen.getByTestId("gaps-list")).toBeInTheDocument()
    })
    expect(screen.getAllByTestId("gap-row")).toHaveLength(22)
  })

  it("shows the watcher panel with a feed badge when watchers exist", async () => {
    stub({ watchers: WATCHERS_REPORT })
    renderTrade()

    await waitFor(() => {
      expect(screen.getByTestId("watchers-section")).toBeInTheDocument()
    })
    expect(screen.getByTestId("watcher-row")).toHaveTextContent("cheap iron")
    expect(screen.getByTestId("watcher-badge")).toHaveTextContent("+2 new")
  })

  it("still renders when the logistics plane 404s", async () => {
    stub({ logistics: NOT_FOUND })
    renderTrade()

    await waitFor(() => {
      expect(screen.getByTestId("drill")).toBeInTheDocument()
    })
    // The board is absent, but the rest of the page stands.
    expect(screen.queryByTestId("logistics")).not.toBeInTheDocument()
    expect(screen.getByTestId("directory")).toBeInTheDocument()
  })

  it("degrades to an unavailable pill when every plane 404s", async () => {
    stub({
      market: NOT_FOUND,
      stores: NOT_FOUND,
      logistics: NOT_FOUND,
      trades: NOT_FOUND,
      watchers: NOT_FOUND,
    })
    renderTrade()

    await waitFor(() => {
      expect(screen.getByTestId("trade-error")).toBeInTheDocument()
    })
    // No panels, but the shell and cross-links survive — no hard crash.
    expect(screen.queryByTestId("drill")).not.toBeInTheDocument()
    expect(screen.queryByTestId("ledger")).not.toBeInTheDocument()
    expect(screen.getByTestId("link-crafting")).toBeInTheDocument()
  })
})
