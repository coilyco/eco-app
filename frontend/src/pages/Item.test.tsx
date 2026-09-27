import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom"
import Item from "./Item"

// A pivot with a compressed feed: a trade (newest), a 3-run craft, and a single
// craft by a second citizen. worldClockS ages events against real "now".
const PIVOT = {
  fetchedAtISO: "2026-06-12T13:00:00+00:00",
  sourceBaseUrl: "http://x:3001",
  item: "MortarItem",
  trades: [],
  crafts: [],
  feed: [
    {
      kind: "trade",
      time: 305000,
      day: 3.53,
      actor: "coilysiren",
      actionType: "CurrencyTrade",
      station: "StoreItem",
      quantity: 2,
      buyer: "rei",
      seller: "coilysiren",
      currency: "Credit",
      unitPrice: 10,
      currencyAmount: 20,
      runCount: 1,
      spanSeconds: 0,
    },
    {
      kind: "craft",
      time: 300000,
      day: 3.47,
      actor: "Reihtnog",
      actionType: "ItemCraftedAction",
      station: "MasonryTableItem",
      quantity: 4,
      buyer: "",
      seller: "",
      currency: "",
      unitPrice: null,
      currencyAmount: 0,
      runCount: 3,
      spanSeconds: 1800,
    },
    {
      kind: "craft",
      time: 100000,
      day: 1.16,
      actor: "elizacorn",
      actionType: "ItemCraftedAction",
      station: "(hand)",
      quantity: 1,
      buyer: "",
      seller: "",
      currency: "",
      unitPrice: null,
      currencyAmount: 0,
      runCount: 1,
      spanSeconds: 0,
    },
  ],
  feedTruncated: false,
  summary: {
    crafters: [
      { name: "Reihtnog", quantity: 12, events: 3 },
      { name: "elizacorn", quantity: 1, events: 1 },
    ],
    supply: {
      storeCount: 1,
      totalQuantity: 8,
      offers: [
        { store: "coilysiren's Store", owner: "coilysiren", price: 10, quantity: 8, currency: "Credit", source: "history" },
      ],
      capped: false,
    },
    demand: {
      storeCount: 1,
      totalQuantity: 5,
      offers: [
        { store: "rei's Stall", owner: "rei", price: 9, quantity: 5, currency: "Credit", source: "history" },
      ],
      capped: false,
    },
    live: false,
  },
  worldClockS: 310000,
  tradeCount: 1,
  tradeVolume: 20,
  craftCount: 4,
  craftQuantity: 13,
  warnings: [],
}

const component = (item: string, displayName: string, quantity = 1, isTag = false) => ({
  item,
  displayName,
  quantity,
  isTag,
})
const recipe = <T extends Record<string, unknown>>(over: T) => ({
  ingredients: [],
  byproducts: [],
  station: "",
  stationDisplayName: "",
  skill: null,
  laborCost: 0,
  craftMinutes: 1,
  tableTierRequired: null,
  variants: [],
  family: "",
  isDefault: false,
  isBlueprint: false,
  ...over,
})

// Mortar is made two ways (the main recipe listed first) and goes into a wall.
const INDEX = {
  fetchedAtISO: "2026-06-12T13:00:00+00:00",
  source: "test",
  version: 1,
  counts: { recipes: 3, skills: 0, tags: 0, products: 2, stations: 2 },
  recipes: [
    recipe({
      name: "BakedMortar",
      displayName: "Baked Mortar",
      product: component("MortarItem", "Mortar", 3),
      ingredients: [component("SandItem", "Sand", 2)],
      station: "KilnObject",
    }),
    recipe({
      name: "Mortar",
      displayName: "Mortar",
      product: component("MortarItem", "Mortar", 1),
      ingredients: [component("SandItem", "Sand", 1)],
      station: "MasonryTableObject",
      isDefault: true,
    }),
    recipe({
      name: "StoneWall",
      displayName: "Stone Wall",
      product: component("StoneWallItem", "Stone Wall"),
      ingredients: [component("MortarItem", "Mortar", 2)],
      station: "MasonryTableObject",
      isDefault: true,
    }),
  ],
  byProduct: { MortarItem: ["BakedMortar", "Mortar"], StoneWallItem: ["StoneWall"] },
  bySkill: {},
  byStation: {},
  skills: [],
  tags: {},
  warnings: [],
}

// The page loads the item's own recipes (the service's ?product= filter), its
// market pivot, and on request the whole recipe graph.
function answer(url: string, payload: unknown) {
  const u = new URL(url, "http://x")
  if (!u.pathname.endsWith("recipes.json")) return payload
  const product = u.searchParams.get("product")
  if (!product) return INDEX
  const recipes = INDEX.recipes.filter((r) => [product, `${product}Item`].includes(r.product.item))
  return { ...INDEX, recipes }
}

function stubPivotFetch(payload: unknown = PIVOT) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation(async (url: string) =>
      new Response(JSON.stringify(answer(String(url), payload)), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  )
}

function Where() {
  const loc = useLocation()
  return <p data-testid="where">{`${loc.pathname}${loc.search}${loc.hash}`}</p>
}

function renderItem(entry = "/item?id=Mortar") {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route
          path="/item"
          element={
            <>
              <Item />
              <Where />
            </>
          }
        />
      </Routes>
    </MemoryRouter>,
  )
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe("Item", () => {
  it("renders the summary, the merged feed, and compressed relative-time rows", async () => {
    stubPivotFetch()
    renderItem()

    await waitFor(() => {
      expect(screen.getByTestId("item-pill")).toHaveTextContent("1 trades · 13 made")
    })
    // Actionable summary: crafters, supply, demand.
    expect(screen.getByTestId("item-crafters")).toHaveTextContent("Reihtnog")
    expect(screen.getByTestId("item-supply")).toHaveTextContent("coilysiren's Store")
    expect(screen.getByTestId("item-demand")).toHaveTextContent("rei")

    // Merged feed: a trade line and a compressed craft run.
    const rows = screen.getAllByTestId("item-feed-row")
    expect(rows).toHaveLength(3)
    expect(rows[0]).toHaveTextContent("coilysiren sold 2 Mortar to rei @ 10 Credit")
    expect(rows[1]).toHaveTextContent("Reihtnog crafted 4 Mortar at Masonry Table")
    expect(rows[1]).toHaveTextContent("3 crafts over 30 minutes")
    expect(screen.getByTestId("back-to-items")).toHaveAttribute("href", "/items")
  })

  it("filters the feed by event type", async () => {
    stubPivotFetch()
    renderItem()

    await waitFor(() => {
      expect(screen.getAllByTestId("item-feed-row")).toHaveLength(3)
    })
    fireEvent.change(screen.getByTestId("item-type-filter"), { target: { value: "trade" } })
    const rows = screen.getAllByTestId("item-feed-row")
    expect(rows).toHaveLength(1)
    expect(rows[0]).toHaveTextContent("coilysiren sold 2 Mortar")
  })

  it("honors a ?q= deep link by filtering the feed", async () => {
    stubPivotFetch()
    renderItem("/item?id=Mortar&q=reihtnog")

    await waitFor(() => {
      expect(screen.getByTestId("item-filter")).toHaveValue("reihtnog")
    })
    const rows = screen.getAllByTestId("item-feed-row")
    expect(rows).toHaveLength(1)
    expect(rows[0]).toHaveTextContent("Reihtnog crafted 4 Mortar")
  })

  it("prompts for a selection when no ?id= is present", async () => {
    stubPivotFetch()
    renderItem("/item")

    await waitFor(() => {
      expect(screen.getByTestId("item-missing")).toBeInTheDocument()
    })
    expect(screen.queryByTestId("item-pill")).not.toBeInTheDocument()
  })

  it("shows the empty state for an item with no recorded events", async () => {
    stubPivotFetch({
      ...PIVOT,
      feed: [],
      tradeCount: 0,
      craftCount: 0,
      summary: { ...PIVOT.summary, crafters: [] },
    })
    renderItem()

    await waitFor(() => {
      expect(screen.getByTestId("item-empty")).toBeInTheDocument()
    })
  })

  it("redirects a retired ?item= link to the ?id= page, filters intact", async () => {
    stubPivotFetch()
    renderItem("/item?item=MortarItem&q=reihtnog")

    await waitFor(() => {
      expect(screen.getByTestId("where")).toHaveTextContent("/item?q=reihtnog&id=Mortar")
    })
    await waitFor(() => {
      expect(screen.getAllByTestId("item-feed-row")).toHaveLength(1)
    })
  })

  it("shows every recipe that makes it, main recipe first, and what it goes into on request", async () => {
    stubPivotFetch()
    renderItem()

    await waitFor(() => {
      expect(screen.getAllByTestId("recipe-card")).toHaveLength(2)
    })
    const cards = screen.getAllByTestId("recipe-card")
    expect(cards[0]).toHaveAttribute("id", "recipe-Mortar")
    expect(cards[0]).toHaveTextContent("main recipe")
    expect(cards[0]).toHaveTextContent("Masonry Table")
    expect(cards[0]).not.toHaveTextContent("Object")
    expect(cards[1]).toHaveAttribute("id", "recipe-BakedMortar")
    expect(screen.getByRole("heading", { name: /Everything about Mortar/ })).toBeInTheDocument()
    // The whole graph stays unloaded until asked for.
    expect(screen.queryByTestId("item-used-in-list")).not.toBeInTheDocument()
    fireEvent.click(screen.getByTestId("item-load-uses"))
    await waitFor(() => {
      expect(screen.getByTestId("item-used-in-list")).toHaveTextContent("Stone Wall")
    })
    expect(screen.getByRole("link", { name: "Stone Wall" })).toHaveAttribute(
      "href",
      "/item?id=StoneWall#recipe-StoneWall",
    )
  })

  it("lands on and focuses the recipe card a /recipe redirect anchors to", async () => {
    stubPivotFetch()
    renderItem("/item?id=Mortar#recipe-BakedMortar")

    await waitFor(() => {
      expect(document.getElementById("recipe-BakedMortar")).toHaveFocus()
    })
  })

  it("says so when no recipe makes the item", async () => {
    stubPivotFetch({ ...PIVOT, item: "SandItem" })
    renderItem("/item?id=Sand")

    await waitFor(() => {
      expect(screen.getByTestId("item-no-recipe")).toHaveTextContent("No recipe makes Sand as its main product.")
    })
  })

  it("degrades when the pivot fetch fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("boom")))
    renderItem()

    await waitFor(() => {
      expect(screen.getByTestId("item-error")).toBeInTheDocument()
    })
  })
})
