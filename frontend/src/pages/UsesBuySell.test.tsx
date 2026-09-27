import { cleanup, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"
import { MemoryRouter } from "react-router-dom"
import UsesBuySell from "./UsesBuySell"

function offer(store: string, side: string, price: number, source = "live") {
  return {
    store,
    owner: `${store}-owner`,
    storeKey: store,
    item: "IronIngotItem",
    itemPretty: "Iron Ingot",
    currency: "Credit",
    side,
    price,
    quantity: 10,
    source,
    lastDay: 3,
  }
}

const LOGISTICS = {
  view: "logistics",
  fetchedAtISO: "2026-06-12T13:00:00+00:00",
  sourceBaseUrl: "http://x:3001",
  live: true,
  totalOffers: 4,
  totalStores: 3,
  cheapest: [
    {
      item: "IronIngotItem",
      itemPretty: "Iron Ingot",
      currency: "Credit",
      cheapest: 8,
      offers: [offer("Pricey", "sell", 12), offer("Cheap", "sell", 8)],
    },
  ],
  resale: [
    {
      item: "IronIngotItem",
      itemPretty: "Iron Ingot",
      currency: "Credit",
      best: 15,
      offers: [offer("LowBuy", "buy", 9, "history"), offer("HighBuy", "buy", 15)],
    },
  ],
  arbitrage: [],
  supplyGaps: [],
  warnings: [],
}

function stub(body: unknown, ok = true) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status: ok ? 200 : 404 })),
  )
}

function renderPage(entry = "/uses/buy-sell") {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <UsesBuySell />
    </MemoryRouter>,
  )
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe("UsesBuySell", () => {
  it("shows a pickable item list when no item is selected", async () => {
    stub(LOGISTICS)
    renderPage()
    await waitFor(() => {
      expect(screen.getByTestId("buy-sell-picker")).toBeInTheDocument()
    })
    expect(screen.getAllByTestId("pick-item")[0]).toHaveTextContent("Iron Ingot")
  })

  it("honors a ?item= deep link, sorting sells cheapest-first and buys highest-first", async () => {
    stub(LOGISTICS)
    renderPage("/uses/buy-sell?item=IronIngotItem")

    await waitFor(() => {
      expect(screen.getByTestId("buy-sell-boards")).toBeInTheDocument()
    })
    const sells = within(screen.getByTestId("sell-offers")).getAllByTestId("offer-row")
    expect(sells[0]).toHaveTextContent("Cheap") // 8 before 12
    const buys = within(screen.getByTestId("buy-offers")).getAllByTestId("offer-row")
    expect(buys[0]).toHaveTextContent("HighBuy") // 15 before 9
    // Source provenance renders a label, not colour alone.
    expect(within(screen.getByTestId("buy-offers")).getAllByTestId("source-tag")[1]).toHaveTextContent(
      "from past sales",
    )
  })

  it("degrades to a clear note when the shelf plane is unavailable", async () => {
    stub({}, false)
    renderPage("/uses/buy-sell?item=IronIngotItem")

    await waitFor(() => {
      expect(screen.getByTestId("buy-sell-error")).toBeInTheDocument()
    })
    expect(screen.queryByTestId("buy-sell-boards")).not.toBeInTheDocument()
  })
})
