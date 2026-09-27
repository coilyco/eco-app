import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"
import { MemoryRouter } from "react-router-dom"
import Items from "./Items"

const INDEX = {
  fetchedAtISO: "2026-06-12T13:00:00+00:00",
  sourceBaseUrl: "http://x:3001",
  totalItems: 3,
  items: [
    { item: "IronIngotItem", tradeCount: 42, tradeVolume: 3900, craftCount: 810 },
    { item: "DirtItem", tradeCount: 0, tradeVolume: 0, craftCount: 98695 },
    { item: "BeetItem", tradeCount: 5, tradeVolume: 50, craftCount: 0 },
  ],
  warnings: [],
}

function stubIndexFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify(INDEX), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  )
}

function renderItems(entry = "/items") {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Items />
    </MemoryRouter>,
  )
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe("Items", () => {
  it("hides untraded items by default and deep links traded ones to the pivot", async () => {
    stubIndexFetch()
    renderItems()

    await waitFor(() => {
      expect(screen.getByTestId("items-pill")).toHaveTextContent("3 distinct items")
    })
    // Only the two traded items show; Dirt (tradeCount 0) is hidden.
    expect(screen.getAllByTestId("item-row")).toHaveLength(2)
    expect(screen.getByText("Iron Ingot").closest("a")).toHaveAttribute(
      "href",
      "/item?id=IronIngot",
    )
    expect(screen.queryByText("Dirt")).not.toBeInTheDocument()
  })

  it("reveals untraded items when the toggle is checked", async () => {
    stubIndexFetch()
    renderItems()

    await waitFor(() => {
      expect(screen.getByText("Iron Ingot")).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTestId("untraded-toggle").querySelector("input")!)
    expect(screen.getByText("Dirt")).toBeInTheDocument()
    expect(screen.getAllByTestId("item-row")).toHaveLength(3)
  })

  it("sorts by trade volume when that chip is picked", async () => {
    stubIndexFetch()
    renderItems()

    await waitFor(() => {
      expect(screen.getAllByTestId("item-row")).toHaveLength(2)
    })
    fireEvent.click(screen.getByTestId("sort-volume"))
    const rows = screen.getAllByTestId("item-row")
    // Iron (3900) outranks Beet (50) by trade volume.
    expect(rows[0]).toHaveTextContent("Iron Ingot")
    expect(rows[1]).toHaveTextContent("Beet")
  })

  it("honors a ?q= deep link by filtering the list", async () => {
    stubIndexFetch()
    renderItems("/items?q=beet")

    await waitFor(() => {
      expect(screen.getByText("Beet")).toBeInTheDocument()
    })
    expect(screen.queryByText("Iron Ingot")).not.toBeInTheDocument()
    expect(screen.getByTestId("items-filter")).toHaveValue("beet")
  })

  it("typing into the filter narrows the list", async () => {
    stubIndexFetch()
    renderItems()

    await waitFor(() => {
      expect(screen.getByText("Iron Ingot")).toBeInTheDocument()
    })
    fireEvent.change(screen.getByTestId("items-filter"), { target: { value: "beet" } })
    expect(screen.getByText("Beet")).toBeInTheDocument()
    expect(screen.queryByText("Iron Ingot")).not.toBeInTheDocument()
  })

  it("shows the empty state when no items are recorded", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ ...INDEX, totalItems: 0, items: [] }), { status: 200 }),
      ),
    )
    renderItems()

    await waitFor(() => {
      expect(screen.getByTestId("items-empty")).toBeInTheDocument()
    })
  })

  it("degrades when the index fetch fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("boom")))
    renderItems()

    await waitFor(() => {
      expect(screen.getByTestId("items-error")).toBeInTheDocument()
    })
  })
})
