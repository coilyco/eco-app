import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"
import { MemoryRouter } from "react-router-dom"
import Jobs from "./Jobs"

const META = { mockData: true }
const PROFESSIONS = [
  { profession: "Carpentry", active: 2, covered: 1, total: 2, players: ["coilysiren", "ekans"] },
  { profession: "Masonry", active: 1, covered: 0, total: 1, players: ["hammerhand"] },
  // Universal starter professions — everyone has these, so they must be
  // filtered out of the surface (eco-app#94).
  { profession: "Self Improvement", active: 2, covered: 1, total: 2, players: ["coilysiren", "ekans"] },
  { profession: "Survivalist", active: 2, covered: 1, total: 2, players: ["coilysiren", "ekans"] },
]
const SPECIALTIES = [
  {
    specialty: "Basic Carpentry",
    profession: "Carpentry",
    active: 1,
    covered: 1,
    total: 2,
    holders: [
      { player: "coilysiren", level: 5, active: true, roles: ["Active", "Long Term"] },
      { player: "ekans", level: 2, active: false, roles: [] },
    ],
  },
  {
    specialty: "Self Improvement",
    profession: "Other",
    active: 1,
    covered: 1,
    total: 1,
    holders: [{ player: "coilysiren", level: 3, active: true, roles: ["Active"] }],
  },
]
const PLAYERS = [
  {
    name: "coilysiren",
    active: true,
    roles: ["Active", "Long Term"],
    specialties: [
      { specialty: "Basic Carpentry", level: 5, active: true },
      { specialty: "Survivalist", level: 4, active: true },
    ],
  },
  { name: "ekans", active: false, roles: [], specialties: [] },
  { name: "hammerhand", active: true, roles: [], specialties: [] },
]
const VALUE_RECIPES = {
  fetchedAtISO: "2026-07-07T13:00:00+00:00",
  source: "test",
  version: 1,
  counts: { recipes: 4, skills: 4, tags: 0, products: 4, stations: 2 },
  recipes: [
    {
      name: "PlankRecipe",
      displayName: "Plank",
      product: { item: "PlankItem", displayName: "Plank", quantity: 1, isTag: false },
      ingredients: [],
      byproducts: [],
      station: "WorkbenchItem",
      stationDisplayName: "Workbench",
      skill: { name: "CarpentrySkill", level: 1 },
      laborCost: 0,
      craftMinutes: 0,
      tableTierRequired: null,
      variants: [],
      family: "Plank",
      isDefault: true,
      isBlueprint: false,
      cost: { perUnitCost: 2, complete: true },
    },
    {
      name: "BeamRecipe",
      displayName: "Beam",
      product: { item: "BeamItem", displayName: "Beam", quantity: 1, isTag: false },
      ingredients: [],
      byproducts: [],
      station: "WorkbenchItem",
      stationDisplayName: "Workbench",
      skill: { name: "CarpentrySkill", level: 1 },
      laborCost: 0,
      craftMinutes: 0,
      tableTierRequired: null,
      variants: [],
      family: "Beam",
      isDefault: true,
      isBlueprint: false,
      cost: { perUnitCost: null, complete: false },
    },
    {
      name: "NeedleRecipe",
      displayName: "Needle",
      product: { item: "NeedleItem", displayName: "Needle", quantity: 1, isTag: false },
      ingredients: [],
      byproducts: [],
      station: "WorkbenchItem",
      stationDisplayName: "Workbench",
      skill: { name: "CarpentrySkill", level: 1 },
      laborCost: 0,
      craftMinutes: 0,
      tableTierRequired: null,
      variants: [],
      family: "Needle",
      isDefault: true,
      isBlueprint: false,
      cost: { perUnitCost: 1, complete: true },
    },
    {
      name: "BrickRecipe",
      displayName: "Brick",
      product: { item: "BrickItem", displayName: "Brick", quantity: 1, isTag: false },
      ingredients: [],
      byproducts: [],
      station: "KilnItem",
      stationDisplayName: "Kiln",
      skill: { name: "MasonrySkill", level: 1 },
      laborCost: 0,
      craftMinutes: 0,
      tableTierRequired: null,
      variants: [],
      family: "Brick",
      isDefault: true,
      isBlueprint: false,
      cost: { perUnitCost: 12, complete: true },
    },
  ],
  byProduct: {
    PlankItem: ["PlankRecipe"],
    BeamItem: ["BeamRecipe"],
    NeedleItem: ["NeedleRecipe"],
    BrickItem: ["BrickRecipe"],
  },
  bySkill: {
    CarpentrySkill: ["PlankRecipe", "BeamRecipe", "NeedleRecipe"],
    MasonrySkill: ["BrickRecipe"],
  },
  byStation: { WorkbenchItem: ["PlankRecipe", "BeamRecipe", "NeedleRecipe"], KilnItem: ["BrickRecipe"] },
  skills: [
    { name: "CarpenterSkill", displayName: "Carpenter", profession: null, maxLevel: 0, talents: [] },
    {
      name: "CarpentrySkill",
      displayName: "Carpentry",
      profession: "CarpenterSkill",
      maxLevel: 7,
      talents: [
        {
          name: "CarpentrySpeedTalent",
          displayName: "Quick Joinery",
          description: "Craft carpentry recipes faster.",
          level: 3,
          maxLevel: 1,
        },
      ],
    },
    { name: "MasonSkill", displayName: "Mason", profession: null, maxLevel: 0, talents: [] },
    {
      name: "MasonrySkill",
      displayName: "Masonry",
      profession: "MasonSkill",
      maxLevel: 7,
      talents: [],
    },
  ],
  tags: {},
  warnings: [],
}

const VALUE_LOGISTICS = {
  view: "logistics",
  fetchedAtISO: "2026-07-07T13:00:00+00:00",
  sourceBaseUrl: "http://x:3001",
  live: true,
  totalOffers: 0,
  totalStores: 0,
  cheapest: [],
  resale: [],
  arbitrage: [],
  supplyGaps: [
    {
      item: "PlankItem",
      itemPretty: "Plank",
      currency: "Credit",
      reason: "no_supply",
      sellerCount: 0,
      buyerCount: 2,
      demandQty: 20,
      supplyQty: 0,
      buyPrice: 6,
      cheapestSell: null,
      median: 6,
      overMedianPct: null,
      buyers: [],
    },
    {
      item: "BeamItem",
      itemPretty: "Beam",
      currency: "Credit",
      reason: "thin_supply",
      sellerCount: 1,
      buyerCount: 1,
      demandQty: 5,
      supplyQty: 1,
      buyPrice: 5,
      cheapestSell: 4,
      median: 5,
      overMedianPct: null,
      buyers: [],
    },
    {
      item: "BrickItem",
      itemPretty: "Brick",
      currency: "Credit",
      reason: "thin_supply",
      sellerCount: 1,
      buyerCount: 1,
      demandQty: 4,
      supplyQty: 1,
      buyPrice: 20,
      cheapestSell: 14,
      median: 20,
      overMedianPct: null,
      buyers: [],
    },
    {
      item: "NeedleItem",
      itemPretty: "Needle",
      currency: "Credit",
      reason: "no_supply",
      sellerCount: 0,
      buyerCount: 10,
      demandQty: 100,
      supplyQty: 0,
      buyPrice: 8,
      cheapestSell: null,
      median: 8,
      overMedianPct: null,
      buyers: [],
    },
  ],
  warnings: [],
}

const VALUE_MARKET = {
  view: "market",
  fetchedAtISO: "2026-07-07T13:00:00+00:00",
  sourceBaseUrl: "http://x:3001",
  totalTrades: 10,
  markets: [
    {
      item: "PlankItem",
      itemPretty: "Plank",
      currency: "Credit",
      buckets: [],
      medianPrice: 6,
      latestPrice: 6,
      latestDay: 1,
      trend: "flat",
      trendDeltaPct: 0,
      shortMedian: 6,
      longMedian: 6,
      totalVolume: 100,
      totalTrades: 10,
    },
    {
      item: "BeamItem",
      itemPretty: "Beam",
      currency: "Credit",
      buckets: [],
      medianPrice: 5,
      latestPrice: 5,
      latestDay: 1,
      trend: "flat",
      trendDeltaPct: 0,
      shortMedian: 5,
      longMedian: 5,
      totalVolume: 100,
      totalTrades: 10,
    },
    {
      item: "BrickItem",
      itemPretty: "Brick",
      currency: "Credit",
      buckets: [],
      medianPrice: 20,
      latestPrice: 20,
      latestDay: 1,
      trend: "flat",
      trendDeltaPct: 0,
      shortMedian: 20,
      longMedian: 20,
      totalVolume: 100,
      totalTrades: 10,
    },
    {
      item: "NeedleItem",
      itemPretty: "Needle",
      currency: "Credit",
      buckets: [],
      medianPrice: 8,
      latestPrice: 8,
      latestDay: 1,
      trend: "flat",
      trendDeltaPct: 0,
      shortMedian: 8,
      longMedian: 8,
      totalVolume: 100,
      totalTrades: 10,
    },
  ],
  warnings: [],
}

const VALUE_TRADES = {
  fetchedAtISO: "2026-07-07T13:00:00+00:00",
  sourceBaseUrl: "http://x:3001",
  totalTrades: 10,
  perTypeCounts: {},
  trades: [],
  totalCurrencyVolume: 10,
  byItem: [
    ["PlankItem", 5, 500],
    ["BeamItem", 4, 120],
    ["BrickItem", 3, 150],
    ["NeedleItem", 1, 20],
  ],
  byCurrency: [["Credit", 10]],
  topBuyers: [],
  topSellers: [],
  priceSeries: {},
  warnings: [],
}

let recipesBody: unknown = VALUE_RECIPES

function stubJobsFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      let body: unknown = null
      if (url.endsWith("/meta")) body = META
      else if (url.endsWith("/professions")) body = PROFESSIONS
      else if (url.endsWith("/specialties")) body = SPECIALTIES
      else if (url.endsWith("/players")) body = PLAYERS
      else if (url.includes("/preview/recipes.json?cost=1")) body = recipesBody
      else if (url.endsWith("/preview/logistics.json")) body = VALUE_LOGISTICS
      else if (url.endsWith("/preview/market.json")) body = VALUE_MARKET
      else if (url.includes("/preview/get_trades.json")) body = VALUE_TRADES
      if (body === null) return Promise.reject(new Error(`unexpected fetch: ${url}`))
      return Promise.resolve(
        new Response(JSON.stringify(body), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      )
    }),
  )
}

function renderJobs() {
  return render(
    <MemoryRouter initialEntries={["/jobs"]}>
      <Jobs />
    </MemoryRouter>,
  )
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  recipesBody = VALUE_RECIPES
})

describe("Jobs", () => {
  it("renders professions and specialties without the standalone Players section", async () => {
    stubJobsFetch()
    renderJobs()

    await waitFor(() => {
      expect(screen.getByText("Professions")).toBeInTheDocument()
    })
    expect(screen.getByRole("button", { name: /Carpentry/ })).toBeInTheDocument()
    expect(screen.getAllByText("Basic Carpentry").length).toBeGreaterThan(0)
    expect(screen.queryByRole("heading", { name: /^Players/ })).not.toBeInTheDocument()
    expect(screen.getByTestId("mock-banner")).toBeInTheDocument()
  })

  it("ranks liquid supply-gap crafts per profession with severity callouts", async () => {
    stubJobsFetch()
    renderJobs()

    await waitFor(() => {
      expect(screen.getByTestId("jobs-value-boards")).toBeInTheDocument()
    })
    const valueBoards = screen.getAllByTestId("value-board")
    expect(valueBoards).toHaveLength(2)
    expect(valueBoards[0]).toHaveTextContent("Carpentry")
    expect(valueBoards[1]).toHaveTextContent("Masonry")
    const carpentryRows = valueBoards[0].querySelectorAll('[data-testid="rank-row"]')
    expect(carpentryRows).toHaveLength(2)
    expect(carpentryRows[0]).toHaveTextContent("Plank")
    expect(carpentryRows[1]).toHaveTextContent("Beam")
    expect(valueBoards[0].querySelector('[data-testid="value-tag"]')).toHaveTextContent(
      "out of stock",
    )
    expect(valueBoards[0]).not.toHaveTextContent("Needle")
    expect(valueBoards[0]).toHaveTextContent("some ingredient costs are unknown, so this is a rough guess")
    expect(screen.getByRole("link", { name: "Plank" })).toHaveAttribute(
      "href",
      "/uses/price?item=PlankItem&source=jobs&demandQty=20&demandReason=no_supply&confidence=complete&margin=4",
    )
    expect(screen.getByRole("link", { name: "Beam" })).toHaveAttribute(
      "href",
      expect.stringContaining("confidence=incomplete"),
    )
  })

  it("expands a profession to list its players", async () => {
    stubJobsFetch()
    renderJobs()

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Carpentry/ })).toBeInTheDocument()
    })
    const before = screen.getAllByText("coilysiren").length

    fireEvent.click(screen.getByRole("button", { name: /Carpentry/ }))
    expect(screen.getAllByText("coilysiren").length).toBe(before + 1)
    expect(screen.queryByText("ekans")).not.toBeInTheDocument()
  })

  it("renders profession, specialty, and level-gated talent branches", async () => {
    stubJobsFetch()
    renderJobs()

    await waitFor(() => {
      expect(screen.getByTestId("jobs-skill-trees")).toBeInTheDocument()
    })
    expect(screen.getAllByTestId("skill-tree")).toHaveLength(2)
    const carpentry = [...screen.getByTestId("jobs-skill-trees").querySelectorAll("summary")].find(
      (row) => row.textContent?.includes("Carpentry"),
    )
    expect(carpentry).toBeDefined()
    fireEvent.click(carpentry!)
    expect(screen.getByText("Quick Joinery")).toBeInTheDocument()
    expect(screen.getByText("Craft carpentry recipes faster.")).toBeInTheDocument()
    expect(screen.getByText("level 3")).toBeInTheDocument()
  })

  it("renders skill trees from the live AutoGen shape, which carries no talents", async () => {
    // 2026-09-27: live skills arrive without `talents`, and the page crashed on
    // skill.talents.length.
    recipesBody = {
      ...VALUE_RECIPES,
      skills: VALUE_RECIPES.skills.map(({ talents: _drop, ...skill }) => skill),
    }
    stubJobsFetch()
    renderJobs()

    await waitFor(() => {
      expect(screen.getByTestId("jobs-skill-trees")).toBeInTheDocument()
    })
    expect(screen.getAllByTestId("skill-tree")).toHaveLength(2)
    expect(screen.getAllByText("No talents listed for this specialty.").length).toBeGreaterThan(0)
  })

  it("asks the recipe plane for the whole graph, not the MCP slice", async () => {
    stubJobsFetch()
    renderJobs()

    await waitFor(() => {
      expect(screen.getByTestId("jobs-skill-trees")).toBeInTheDocument()
    })
    const urls = vi.mocked(fetch).mock.calls.map(([input]) => String(input))
    expect(urls.filter((u) => u.includes("/preview/recipes.json"))).toEqual([
      "/preview/recipes.json?cost=1&limit=0",
    ])
  })

  it("excludes the universal starter skills from every jobs surface", async () => {
    stubJobsFetch()
    renderJobs()

    await waitFor(() => {
      expect(screen.getByText("Professions")).toBeInTheDocument()
    })
    // Professions section: Self Improvement / Survivalist cards gone.
    expect(screen.queryByRole("button", { name: /Self Improvement/ })).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /Survivalist/ })).not.toBeInTheDocument()
    // Specialties + per-player rows: no trace anywhere.
    expect(screen.queryByText("Self Improvement")).not.toBeInTheDocument()
    expect(screen.queryByText("Survivalist")).not.toBeInTheDocument()
    // The real professions and specialties still render.
    expect(screen.getByRole("button", { name: /Carpentry/ })).toBeInTheDocument()
    expect(screen.getAllByText("Basic Carpentry").length).toBeGreaterThan(0)
  })

  it("renders the current-state tables when the enrichment plane is unavailable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input)
        const body = url.endsWith("/meta")
          ? META
          : url.endsWith("/professions")
            ? PROFESSIONS
            : url.endsWith("/specialties")
              ? SPECIALTIES
              : url.endsWith("/players")
                ? PLAYERS
                : null
        if (body === null) return Promise.reject(new Error(`no enrichment: ${url}`))
        return Promise.resolve(
          new Response(JSON.stringify(body), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }),
        )
      }),
    )
    renderJobs()

    await waitFor(() => {
      expect(screen.getByText("Professions")).toBeInTheDocument()
    })
  })

  it("shows the degraded note when the API is unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("boom")))
    renderJobs()

    await waitFor(() => {
      expect(screen.getByTestId("jobs-error")).toBeInTheDocument()
    })
    expect(screen.queryByTestId("mock-banner")).not.toBeInTheDocument()
  })
})
