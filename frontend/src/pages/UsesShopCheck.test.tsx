import { cleanup, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"
import { MemoryRouter } from "react-router-dom"
import UsesShopCheck from "./UsesShopCheck"

function storeItem(item: string, pretty: string, avgUnitPrice: number | null) {
  return { item, pretty, tradeCount: 5, volume: 100, quantity: 20, avgUnitPrice }
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
      tradeCount: 10,
      totalVolume: 500,
      sellCount: 8,
      buyCount: 2,
      uniqueCounterparties: 4,
      lastDay: 5,
      currencies: [["Credit", 500]],
      topItems: [
        storeItem("IronIngotItem", "Iron Ingot", 20), // median 10 -> +100% over
        storeItem("WheatItem", "Wheat", 5), // median 5 -> at
        storeItem("BoardItem", "Board", 2), // median 4 -> -50% under
      ],
      topCounterparties: [],
    },
  ],
  traders: [],
  totalStores: 1,
  totalTraders: 0,
  warnings: [],
}

function market(item: string, medianPrice: number) {
  return {
    item,
    itemPretty: item,
    currency: "Credit",
    buckets: [],
    medianPrice,
    latestPrice: medianPrice,
    latestDay: 5,
    trend: "flat",
    trendDeltaPct: 0,
    shortMedian: medianPrice,
    longMedian: medianPrice,
    totalVolume: 100,
    totalTrades: 10,
  }
}

const MARKET = {
  view: "market",
  fetchedAtISO: "2026-06-12T13:00:00+00:00",
  sourceBaseUrl: "http://x:3001",
  totalTrades: 42,
  markets: [market("IronIngotItem", 10), market("WheatItem", 5), market("BoardItem", 4)],
  warnings: [],
}

// Route the fetch by URL: stores and market are separate planes that can 404
// independently.
function stubBoth(opts: { stores?: unknown; market?: unknown; storesOk?: boolean; marketOk?: boolean }) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((url: string) => {
      if (url.includes("stores.json")) {
        return Promise.resolve(
          new Response(JSON.stringify(opts.stores ?? {}), {
            status: opts.storesOk === false ? 404 : 200,
          }),
        )
      }
      return Promise.resolve(
        new Response(JSON.stringify(opts.market ?? {}), {
          status: opts.marketOk === false ? 404 : 200,
        }),
      )
    }),
  )
}

function renderPage(entry = "/uses/shop-check") {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <UsesShopCheck />
    </MemoryRouter>,
  )
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe("UsesShopCheck", () => {
  it("lists stores to pick when none is selected", async () => {
    stubBoth({ stores: STORES, market: MARKET })
    renderPage()
    await waitFor(() => {
      expect(screen.getByTestId("shop-picker")).toBeInTheDocument()
    })
    expect(screen.getByTestId("pick-store")).toHaveTextContent("Iron Emporium")
  })

  it("joins store prices against market medians and flags over/under", async () => {
    stubBoth({ stores: STORES, market: MARKET })
    renderPage("/uses/shop-check?store=s1")

    await waitFor(() => {
      expect(screen.getByTestId("shop-table")).toBeInTheDocument()
    })
    const rows = screen.getAllByTestId("shop-row")
    expect(rows).toHaveLength(3)
    expect(within(rows[0]).getByTestId("shop-verdict")).toHaveTextContent("over market")
    expect(within(rows[1]).getByTestId("shop-verdict")).toHaveTextContent("at market")
    expect(within(rows[2]).getByTestId("shop-verdict")).toHaveTextContent("under market")
    expect(screen.getByTestId("shop-pill")).toHaveTextContent("2 priced far from the market")
  })

  it("degrades to a clear note when the market plane is unavailable", async () => {
    stubBoth({ stores: STORES, marketOk: false })
    renderPage("/uses/shop-check?store=s1")

    await waitFor(() => {
      expect(screen.getByTestId("shop-no-market")).toBeInTheDocument()
    })
    // Store prices still render, just without a comparison verdict.
    expect(screen.getAllByTestId("shop-verdict")[0]).toHaveTextContent("no market price yet")
  })

  it("degrades to a clear note when the store directory is unavailable", async () => {
    stubBoth({ storesOk: false, market: MARKET })
    renderPage("/uses/shop-check?store=s1")

    await waitFor(() => {
      expect(screen.getByTestId("shop-error")).toBeInTheDocument()
    })
    expect(screen.queryByTestId("shop-table")).not.toBeInTheDocument()
  })
})
