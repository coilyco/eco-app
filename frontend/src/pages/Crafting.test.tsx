import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"
import { MemoryRouter } from "react-router-dom"
import Crafting from "./Crafting"

const ATLAS = {
  fetchedAtISO: "2026-06-12T13:00:00+00:00",
  sourceBaseUrl: "http://x:3001",
  totalEvents: 14251,
  byCrafted: [
    ["BoardItem", 2362],
    ["HewnLogItem", 1691],
  ],
  byGathered: [
    ["DirtItem", 8445],
    ["FlaxSeedItem", 2471],
  ],
  byStation: [
    ["(hand)", 2471],
    ["GreenhouseItem", 1003],
  ],
  byCitizen: [
    ["coilysiren", 3204],
    ["Citizen #129569", 1877],
  ],
  byCitizenIterations: [
    ["coilysiren", 8421],
    ["Citizen #129569", 5102],
  ],
  flows: [],
  perActionCounts: { ItemCraftedAction: 8261, HarvestOrHunt: 2471, ChopTree: 1076, DigOrMine: 2443 },
  warnings: [],
}

function stubAtlasFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify(ATLAS), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  )
}

function renderCrafting(entry = "/crafting") {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Crafting />
    </MemoryRouter>,
  )
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe("Crafting", () => {
  it("renders totals, prettified rank rows, and the item-directory cross-link", async () => {
    stubAtlasFetch()
    renderCrafting()

    await waitFor(() => {
      expect(screen.getByTestId("atlas-pill")).toHaveTextContent("14,251 production events")
    })
    expect(screen.getByTestId("atlas-pill")).toHaveTextContent("8,261 crafted")
    expect(screen.getByText("Flax Seed")).toBeInTheDocument()
    expect(screen.getByText("Greenhouse")).toBeInTheDocument()
    expect(screen.getByTestId("link-items")).toHaveAttribute("href", "/items")
  })

  it("ranks citizens by production with names shown verbatim", async () => {
    stubAtlasFetch()
    renderCrafting()

    await waitFor(() => {
      expect(screen.getByText("coilysiren")).toBeInTheDocument()
    })
    // Unmapped ids fall back to a "Citizen #<id>" label, rendered as-is.
    expect(screen.getByText("Citizen #129569")).toBeInTheDocument()
    expect(screen.getAllByTestId("crafter-row")).toHaveLength(2)
  })

  it("honors a ?q= deep link by filtering both tables", async () => {
    stubAtlasFetch()
    renderCrafting("/crafting?q=board")

    await waitFor(() => {
      expect(screen.getByText("Board")).toBeInTheDocument()
    })
    expect(screen.queryByText("Dirt")).not.toBeInTheDocument()
    expect(screen.getByText("No stations match.")).toBeInTheDocument()
    expect(screen.getByTestId("atlas-filter")).toHaveValue("board")
  })

  it("keeps the atlas filter action beside an item-pivot link", async () => {
    stubAtlasFetch()
    renderCrafting()

    await waitFor(() => {
      expect(screen.getByText("Dirt")).toBeInTheDocument()
    })
    expect(screen.getByRole("link", { name: "Dirt" })).toHaveAttribute("href", "/item?id=Dirt")
    fireEvent.click(screen.getByRole("button", { name: "Filter crafting atlas by Dirt" }))
    expect(screen.getByTestId("atlas-filter")).toHaveValue("Dirt")
    expect(screen.queryByText("Board")).not.toBeInTheDocument()
  })

  it("degrades when the atlas fetch fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("boom")))
    renderCrafting()

    await waitFor(() => {
      expect(screen.getByTestId("atlas-error")).toBeInTheDocument()
    })
  })
})
