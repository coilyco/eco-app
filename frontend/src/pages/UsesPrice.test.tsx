import { cleanup, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"
import { MemoryRouter } from "react-router-dom"
import UsesPrice from "./UsesPrice"

const MARKET = {
  view: "market",
  fetchedAtISO: "2026-07-07T13:00:00+00:00",
  sourceBaseUrl: "http://x:3001",
  totalTrades: 12,
  markets: [
    {
      item: "IronIngotItem",
      itemPretty: "Iron Ingot",
      currency: "Credit",
      buckets: [
        { day: 1, median: 8, min: 7, max: 9, volume: 12, trades: 3 },
        { day: 2, median: 10, min: 9, max: 11, volume: 15, trades: 4 },
      ],
      medianPrice: 9,
      latestPrice: 10,
      latestDay: 2,
      trend: "rising",
      trendDeltaPct: 11.1,
      shortMedian: 10,
      longMedian: 9,
      totalVolume: 27,
      totalTrades: 7,
    },
  ],
  warnings: [],
}

const LOGISTICS = {
  view: "logistics",
  fetchedAtISO: "2026-07-07T13:00:00+00:00",
  sourceBaseUrl: "http://x:3001",
  live: true,
  totalOffers: 4,
  totalStores: 2,
  cheapest: [
    {
      item: "IronIngotItem",
      itemPretty: "Iron Ingot",
      currency: "Credit",
      sellerCount: 2,
      buyerCount: 0,
      cheapest: 8,
      offers: [
        {
          store: "North Market",
          owner: "Ava",
          storeKey: "north",
          item: "IronIngotItem",
          itemPretty: "Iron Ingot",
          currency: "Credit",
          side: "sell",
          price: 8,
          quantity: 10,
          source: "live",
          lastDay: 2,
        },
      ],
    },
  ],
  resale: [
    {
      item: "IronIngotItem",
      itemPretty: "Iron Ingot",
      currency: "Credit",
      sellerCount: 0,
      buyerCount: 2,
      best: 11,
      offers: [
        {
          store: "Forge",
          owner: "Bo",
          storeKey: "forge",
          item: "IronIngotItem",
          itemPretty: "Iron Ingot",
          currency: "Credit",
          side: "buy",
          price: 11,
          quantity: 6,
          source: "history",
          lastDay: 2,
        },
      ],
    },
  ],
  arbitrage: [],
  supplyGaps: [],
  warnings: [],
}

const RECIPE_COST = {
  fetchedAtISO: "2026-07-07T13:00:00+00:00",
  warnings: [],
  recipes: [
    {
      name: "IronIngotRecipe",
      displayName: "Iron Ingot",
      product: { item: "IronIngotItem", displayName: "Iron Ingot", quantity: 1 },
      cost: {
        recipe: "IronIngotRecipe",
        product: "IronIngotItem",
        yield: 1,
        perUnitCost: 6.5,
        totalCost: 6.5,
        ingredientCost: 5,
        laborCost: 1,
        timeCost: 0.5,
        laborCalories: 100,
        craftMinutes: 1,
        complete: true,
        unpricedInputs: [],
        ingredients: [
          {
            item: "IronOreItem",
            displayName: "Iron Ore",
            quantity: 1,
            isTag: false,
            unitCost: 5,
            source: "market",
            subtotal: 5,
          },
        ],
      },
    },
  ],
}

const PRICE_HISTORY = {
  view: "item-price-history",
  fetchedAtISO: "2026-07-07T13:00:00+00:00",
  item: "IronIngotItem",
  itemPretty: "Iron Ingot",
  currency: "Credit",
  scope: {
    label: "Current cycle only",
    cycle: "current",
    progressionRulesVersion: "current-cycle-v1",
    historicalCyclesIncluded: false,
  },
  window: {
    label: "Current cycle",
    firstObservedDay: 1,
    latestPriceDay: 4,
    observedThroughDay: 4,
  },
  distribution: {
    sampleCount: 8,
    sampleState: "representative",
    freshnessState: "current",
    shapeState: "observed",
    median: 9.5,
    min: 7,
    max: 13,
    percentiles: { p10: 7.7, p25: 8.5, p50: 9.5, p75: 11, p90: 12.3 },
    histogram: [
      { low: 7, high: 9, count: 3 },
      { low: 9, high: 11, count: 3 },
      { low: 11, high: 13, count: 2 },
    ],
  },
  daily: [
    { day: 1, median: 8, min: 7, max: 9, volume: 12, trades: 3 },
    { day: 2, median: 10, min: 9, max: 11, volume: 15, trades: 4 },
    { day: 4, median: 12, min: 11, max: 13, volume: 4, trades: 1 },
  ],
  totalVolume: 31,
  recipes: [
    {
      name: "IronIngotBloomeryRecipe",
      displayName: "Iron Ingot",
      product: "IronIngotItem",
      skill: "SmeltingSkill",
      skillPretty: "Smelting",
      skillLevel: 2,
    },
    {
      name: "IronIngotBlastRecipe",
      displayName: "Iron Ingot",
      product: "IronIngotItem",
      skill: "AdvancedSmeltingSkill",
      skillPretty: "Advanced Smelting",
      skillLevel: 4,
    },
  ],
  specialtyUnlocks: [
    {
      skill: "SmeltingSkill",
      skillPretty: "Smelting",
      day: 1,
      time: 86400,
      status: "observed",
      recipeVariants: ["IronIngotBloomeryRecipe"],
    },
    {
      skill: "AdvancedSmeltingSkill",
      skillPretty: "Advanced Smelting",
      day: 3,
      time: 259200,
      status: "observed",
      recipeVariants: ["IronIngotBlastRecipe"],
    },
  ],
  states: [],
  warnings: [],
}

function stubFetch(route: {
  market?: unknown
  logistics?: unknown
  recipes?: unknown
  priceHistory?: unknown
  marketOk?: boolean
  logisticsOk?: boolean
  recipesOk?: boolean
  priceHistoryOk?: boolean
}) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((url: string) => {
      if (url.includes("/preview/market.json")) {
        return Promise.resolve(
          new Response(JSON.stringify(route.market ?? {}), {
            status: route.marketOk === false ? 404 : 200,
            headers: { "Content-Type": "application/json" },
          }),
        )
      }
      if (url.includes("/preview/logistics.json")) {
        return Promise.resolve(
          new Response(JSON.stringify(route.logistics ?? {}), {
            status: route.logisticsOk === false ? 404 : 200,
            headers: { "Content-Type": "application/json" },
          }),
        )
      }
      if (url.includes("/preview/recipes.json")) {
        return Promise.resolve(
          new Response(JSON.stringify(route.recipes ?? {}), {
            status: route.recipesOk === false ? 404 : 200,
            headers: { "Content-Type": "application/json" },
          }),
        )
      }
      if (url.includes("/preview/price-history.json")) {
        return Promise.resolve(
          new Response(JSON.stringify(route.priceHistory ?? PRICE_HISTORY), {
            status: route.priceHistoryOk === false ? 404 : 200,
            headers: { "Content-Type": "application/json" },
          }),
        )
      }
      return Promise.resolve(new Response("{}", { status: 404 }))
    }),
  )
}

function renderPage(entry = "/uses/price") {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <UsesPrice />
    </MemoryRouter>,
  )
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe("UsesPrice", () => {
  it("lists candidate items when no item is selected", async () => {
    stubFetch({ market: MARKET, logistics: LOGISTICS, recipes: RECIPE_COST })
    renderPage()

    await waitFor(() => {
      expect(screen.getByTestId("price-picker")).toBeInTheDocument()
    })
    expect(screen.getAllByTestId("pick-item")[0]).toHaveTextContent("Iron Ingot")
  })

  it("renders the market, shelf, and craft-cost panels for a selected item", async () => {
    stubFetch({ market: MARKET, logistics: LOGISTICS, recipes: RECIPE_COST })
    renderPage("/uses/price?item=IronIngotItem")

    await waitFor(() => {
      expect(screen.getByTestId("price-band-table")).toBeInTheDocument()
    })
    expect(screen.getByTestId("price-pill")).toHaveTextContent("Middle price 9 Credit")
    expect(screen.getByTestId("price-band-pill")).toHaveTextContent(/give or take \S+ Credit/)
    expect(screen.getByTestId("price-trend")).toHaveTextContent("rising")
    expect(screen.getByTestId("price-comparison-table")).toBeInTheDocument()
    expect(screen.getByTestId("price-cost-table")).toBeInTheDocument()
    expect(screen.getByTestId("price-suggestion-list")).toHaveTextContent("Price to ask")
    expect(within(screen.getByTestId("price-comparison-table")).getAllByTestId("price-sell-row")).toHaveLength(1)
  })

  it("retains Jobs demand and margin context beside the pricing evidence", async () => {
    stubFetch({ market: MARKET, logistics: LOGISTICS, recipes: RECIPE_COST })
    renderPage(
      "/uses/price?item=IronIngotItem&source=jobs&demandQty=20&demandReason=no_supply&confidence=complete&margin=4.5",
    )

    await waitFor(() => expect(screen.getByTestId("opportunity-context")).toBeInTheDocument())
    expect(screen.getByTestId("opportunity-context")).toHaveTextContent("20 wanted by buyers")
    expect(screen.getByTestId("opportunity-context")).toHaveTextContent("out of stock")
    expect(screen.getByTestId("opportunity-context")).toHaveTextContent("estimated profit 4.5 each")
    expect(screen.getByTestId("opportunity-confidence")).toHaveTextContent(
      "tip to talk over with other players, not an order to craft it",
    )
  })

  it("keeps incomplete opportunity evidence explicitly low confidence", async () => {
    stubFetch({ market: MARKET, logistics: LOGISTICS, recipes: RECIPE_COST })
    renderPage(
      "/uses/price?item=IronIngotItem&source=jobs&demandQty=7&demandReason=thin_supply&confidence=incomplete",
    )

    await waitFor(() => expect(screen.getByTestId("opportunity-context")).toBeInTheDocument())
    expect(screen.getByTestId("opportunity-context")).toHaveTextContent("estimated profit unknown")
    expect(screen.getByTestId("opportunity-confidence")).toHaveTextContent(
      "Rough guess: some ingredients have no price",
    )
  })

  it("renders a representative current-cycle distribution with every recipe specialty marker", async () => {
    stubFetch({
      market: MARKET,
      logistics: LOGISTICS,
      recipes: RECIPE_COST,
      priceHistory: PRICE_HISTORY,
    })
    renderPage("/uses/price?item=IronIngotItem&currency=Credit")

    await waitFor(() => {
      expect(screen.getByTestId("price-history")).toBeInTheDocument()
    })
    expect(screen.getByTestId("price-history-scope")).toHaveTextContent("Older cycles are excluded")
    expect(screen.getByTestId("price-histogram")).toHaveAccessibleName(
      "Chart of 8 sale prices, grouped by price",
    )
    expect(screen.getByTestId("price-distribution-evidence")).toHaveTextContent(
      "8 trades, enough to go on, recent",
    )
    expect(screen.getByTestId("price-history-chart")).toBeInTheDocument()
    expect(screen.getAllByTestId("specialty-marker")).toHaveLength(2)
    expect(screen.getByTestId("price-unlocks")).toHaveTextContent("Smelting")
    expect(screen.getByTestId("price-unlocks")).toHaveTextContent("Advanced Smelting")
    expect(screen.getByTestId("price-unlocks")).toHaveTextContent("first learned on day 3")
    expect(screen.getByTestId("price-unlocks")).toHaveTextContent("Iron Ingot Blast Recipe")
  })

  it("keeps thin and stale samples explicit", async () => {
    const thin = {
      ...PRICE_HISTORY,
      window: { ...PRICE_HISTORY.window, latestPriceDay: 1, observedThroughDay: 8 },
      distribution: {
        ...PRICE_HISTORY.distribution,
        sampleCount: 1,
        sampleState: "thin",
        freshnessState: "stale",
        shapeState: "unknown",
        median: 10,
        min: 10,
        max: 10,
        percentiles: { p10: 10, p25: 10, p50: 10, p75: 10, p90: 10 },
        histogram: [{ low: 10, high: 10, count: 1 }],
      },
      daily: [{ day: 1, median: 10, min: 10, max: 10, volume: 1, trades: 1 }],
      states: ["thin", "stale"],
    }
    stubFetch({
      market: MARKET,
      logistics: LOGISTICS,
      recipes: RECIPE_COST,
      priceHistory: thin,
    })
    renderPage("/uses/price?item=IronIngotItem&currency=Credit")

    await waitFor(() => {
      expect(screen.getByTestId("price-history-states")).toBeInTheDocument()
    })
    expect(screen.getByTestId("price-history-states")).toHaveTextContent("Only a few sales so far")
    expect(screen.getByTestId("price-history-states")).toHaveTextContent("Out of date")
  })

  it("labels a multimodal distribution instead of collapsing it to one curve", async () => {
    const multimodal = {
      ...PRICE_HISTORY,
      distribution: {
        ...PRICE_HISTORY.distribution,
        shapeState: "multimodal",
        histogram: [
          { low: 7, high: 9, count: 4 },
          { low: 9, high: 11, count: 0 },
          { low: 11, high: 13, count: 4 },
        ],
      },
      states: ["multimodal"],
    }
    stubFetch({
      market: MARKET,
      logistics: LOGISTICS,
      recipes: RECIPE_COST,
      priceHistory: multimodal,
    })
    renderPage("/uses/price?item=IronIngotItem&currency=Credit")

    await waitFor(() => {
      expect(screen.getByTestId("price-history-states")).toBeInTheDocument()
    })
    expect(screen.getByTestId("price-history-states")).toHaveTextContent(
      "Sales bunch up at a few different prices",
    )
    expect(screen.getByTestId("price-histogram")).toBeInTheDocument()
  })

  it("renders empty, missing-recipe, and missing-progression states without implying unlocks", async () => {
    const empty = {
      ...PRICE_HISTORY,
      distribution: {
        sampleCount: 0,
        sampleState: "no_data",
        freshnessState: "unknown",
        shapeState: "unknown",
        median: null,
        min: null,
        max: null,
        percentiles: null,
        histogram: [],
      },
      daily: [],
      recipes: [],
      specialtyUnlocks: [],
      states: ["no_data", "missing_recipes", "missing_progression"],
    }
    stubFetch({
      market: MARKET,
      logistics: LOGISTICS,
      recipes: RECIPE_COST,
      priceHistory: empty,
    })
    renderPage("/uses/price?item=IronIngotItem&currency=Credit")

    await waitFor(() => {
      expect(screen.getByTestId("price-history-states")).toBeInTheDocument()
    })
    expect(screen.getByTestId("price-history-states")).toHaveTextContent(
      "No sales of this item in this currency yet this cycle",
    )
    expect(screen.getByTestId("price-history-states")).toHaveTextContent(
      "No known recipe makes this item",
    )
    expect(screen.getByTestId("price-history-states")).toHaveTextContent(
      "record of when players learned specialties is not available",
    )
    expect(screen.getByTestId("price-unlocks-empty")).toBeInTheDocument()
    expect(screen.queryByTestId("specialty-marker")).not.toBeInTheDocument()
  })

  it("shows an unobserved specialty separately from a missing progression export", async () => {
    const unobserved = {
      ...PRICE_HISTORY,
      specialtyUnlocks: [
        {
          ...PRICE_HISTORY.specialtyUnlocks[0],
          day: null,
          time: null,
          status: "unobserved",
        },
      ],
      states: ["unobserved_unlocks"],
    }
    stubFetch({
      market: MARKET,
      logistics: LOGISTICS,
      recipes: RECIPE_COST,
      priceHistory: unobserved,
    })
    renderPage("/uses/price?item=IronIngotItem&currency=Credit")

    await waitFor(() => {
      expect(screen.getByTestId("price-unlocks")).toBeInTheDocument()
    })
    expect(screen.getByTestId("price-unlocks")).toHaveTextContent(
      "nobody seen learning it this cycle",
    )
    expect(screen.getByTestId("price-history-states")).toHaveTextContent(
      "That does not prove nobody has it",
    )
  })

  it("keeps an outlier visible without rendering a normal curve", async () => {
    const outlier = {
      ...PRICE_HISTORY,
      distribution: {
        ...PRICE_HISTORY.distribution,
        sampleCount: 10,
        shapeState: "observed",
        median: 11,
        min: 10,
        max: 100,
        percentiles: { p10: 10, p25: 10, p50: 11, p75: 12, p90: 21 },
        histogram: [
          { low: 10, high: 40, count: 9 },
          { low: 40, high: 70, count: 0 },
          { low: 70, high: 100, count: 1 },
        ],
      },
    }
    stubFetch({
      market: MARKET,
      logistics: LOGISTICS,
      recipes: RECIPE_COST,
      priceHistory: outlier,
    })
    renderPage("/uses/price?item=IronIngotItem&currency=Credit")

    await waitFor(() => {
      expect(screen.getByTestId("price-distribution-evidence")).toBeInTheDocument()
    })
    expect(screen.getByTestId("price-distribution-evidence")).toHaveTextContent("10 to 100")
    expect(screen.getByTestId("price-distribution-evidence")).toHaveTextContent("10% for 21 or more")
    expect(screen.queryByText(/normal curve/i)).not.toBeInTheDocument()
  })

  it("shows the cost-model-pending note when the recipe plane is missing", async () => {
    stubFetch({
      market: MARKET,
      logistics: LOGISTICS,
      recipes: {},
      recipesOk: false,
    })
    renderPage("/uses/price?item=IronIngotItem")

    await waitFor(() => {
      expect(screen.getByTestId("price-cost-pending")).toBeInTheDocument()
    })
    expect(screen.queryByTestId("price-cost-table")).not.toBeInTheDocument()
  })

  it("degrades the shelf comparison independently when logistics is unavailable", async () => {
    stubFetch({
      market: MARKET,
      logistics: {},
      recipes: RECIPE_COST,
      logisticsOk: false,
    })
    renderPage("/uses/price?item=IronIngotItem")

    await waitFor(() => {
      expect(screen.getByTestId("price-market-band")).toBeInTheDocument()
    })
    expect(screen.getByTestId("price-comparison-empty")).toBeInTheDocument()
    expect(screen.queryByTestId("price-comparison-table")).not.toBeInTheDocument()
  })
})
