import { cleanup, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"
import { MemoryRouter } from "react-router-dom"
import UsesDemand from "./UsesDemand"

const LOGISTICS = {
  view: "logistics",
  fetchedAtISO: "2026-06-12T13:00:00+00:00",
  sourceBaseUrl: "http://x:3001",
  live: true,
  totalOffers: 3,
  totalStores: 2,
  cheapest: [],
  resale: [],
  arbitrage: [],
  supplyGaps: [
    {
      item: "IronIngotItem",
      itemPretty: "Iron Ingot",
      currency: "Credit",
      reason: "no_supply",
      sellerCount: 0,
      buyerCount: 2,
      demandQty: 40,
      supplyQty: 0,
      buyPrice: 5,
      cheapestSell: null,
      median: null,
      overMedianPct: null,
      buyers: [
        { owner: "ekans", store: "Iron Emporium", quantity: 30, price: 5 },
        { owner: "coilysiren", store: "Forge", quantity: 10, price: 4 },
      ],
    },
    {
      item: "WheatItem",
      itemPretty: "Wheat",
      currency: "Credit",
      reason: "thin_supply",
      sellerCount: 1,
      buyerCount: 1,
      demandQty: 100,
      supplyQty: 5,
      buyPrice: 2,
      cheapestSell: null,
      median: null,
      overMedianPct: null,
      buyers: [{ owner: "growlithe", store: "Farm", quantity: 100, price: 2 }],
    },
  ],
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
    <MemoryRouter initialEntries={["/uses/demand"]}>
      <UsesDemand />
    </MemoryRouter>,
  )
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe("UsesDemand", () => {
  it("ranks supply gaps by quantity wanted and names who needs each", async () => {
    stub(LOGISTICS)
    renderPage()

    await waitFor(() => {
      expect(screen.getByTestId("demand-pill")).toHaveTextContent("2 items")
    })
    const rows = screen.getAllByTestId("demand-row")
    expect(rows).toHaveLength(2)
    // Wheat (100 wanted) outranks Iron Ingot (40 wanted).
    expect(rows[0]).toHaveTextContent("Wheat")
    expect(rows[1]).toHaveTextContent("Iron Ingot")
    // The reason tag carries a label, not colour alone.
    expect(screen.getAllByTestId("demand-tag")[1]).toHaveTextContent("out of stock")
    // Who-needs-it names the buyers.
    expect(screen.getAllByTestId("demand-who")[1]).toHaveTextContent("ekans")
  })

  it("degrades to a clear note when the logistics plane is unavailable", async () => {
    stub({}, false)
    renderPage()

    await waitFor(() => {
      expect(screen.getByTestId("demand-empty")).toBeInTheDocument()
    })
    expect(screen.queryByTestId("demand-list")).not.toBeInTheDocument()
  })
})
