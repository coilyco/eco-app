import { cleanup, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"
import { MemoryRouter } from "react-router-dom"
import UsesArbitrage from "./UsesArbitrage"

function shelf(store: string, price: number) {
  return {
    store,
    owner: `${store}-owner`,
    storeKey: store,
    item: "IronIngotItem",
    itemPretty: "Iron Ingot",
    currency: "Credit",
    side: "sell",
    price,
    quantity: 20,
    source: "live",
    lastDay: 3,
  }
}

const LOGISTICS = {
  view: "logistics",
  fetchedAtISO: "2026-06-12T13:00:00+00:00",
  sourceBaseUrl: "http://x:3001",
  live: true,
  totalOffers: 4,
  totalStores: 4,
  cheapest: [],
  resale: [],
  arbitrage: [
    {
      item: "WheatItem",
      itemPretty: "Wheat",
      currency: "Credit",
      spread: 2,
      spreadPct: 20,
      volume: 10,
      opportunity: 20,
      storeCount: 2,
      buyFrom: shelf("A", 10),
      sellTo: shelf("B", 12),
    },
    {
      item: "IronIngotItem",
      itemPretty: "Iron Ingot",
      currency: "Credit",
      spread: 5,
      spreadPct: 50,
      volume: 30,
      opportunity: 150,
      storeCount: 2,
      buyFrom: shelf("C", 10),
      sellTo: shelf("D", 15),
    },
  ],
  supplyGaps: [],
  warnings: [],
}

function stub(body: unknown, ok = true) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status: ok ? 200 : 404 })),
  )
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/uses/arbitrage"]}>
      <UsesArbitrage />
    </MemoryRouter>,
  )
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe("UsesArbitrage", () => {
  it("ranks spreads by opportunity, highest first", async () => {
    stub(LOGISTICS)
    renderPage()

    await waitFor(() => {
      expect(screen.getByTestId("arb-pill")).toHaveTextContent("2 items to buy low and sell high")
    })
    const rows = screen.getAllByTestId("arb-row")
    expect(rows).toHaveLength(2)
    // Iron Ingot (opportunity 150) outranks Wheat (opportunity 20).
    expect(rows[0]).toHaveTextContent("Iron Ingot")
    expect(rows[1]).toHaveTextContent("Wheat")
  })

  it("degrades to a clear note when the logistics plane is unavailable", async () => {
    stub({}, false)
    renderPage()

    await waitFor(() => {
      expect(screen.getByTestId("arb-empty")).toBeInTheDocument()
    })
    expect(screen.queryByTestId("arb-table")).not.toBeInTheDocument()
  })
})
