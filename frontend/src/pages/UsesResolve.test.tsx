import { cleanup, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"
import { MemoryRouter } from "react-router-dom"
import UsesResolve from "./UsesResolve"

const RECIPES = {
  fetchedAtISO: "2026-07-31T12:00:00Z",
  recipes: [{ name: "IronIngotRecipe", displayName: "Iron Ingot", product: { item: "IronIngotItem", displayName: "Iron Ingot", quantity: 1, isTag: false }, ingredients: [], byproducts: [], station: "BloomeryItem", stationDisplayName: "Bloomery", skill: { name: "SmeltingSkill", level: 2 }, laborCost: 0, craftMinutes: 1, tableTierRequired: null, variants: [], family: "Iron", isDefault: true, isBlueprint: false, cost: { perUnitCost: 4, unpricedInputs: ["CoalItem"] } }],
}

const LOGISTICS = { fetchedAtISO: "2026-07-31T12:00:00Z", live: true, cheapest: [{ item: "IronIngotItem", itemPretty: "Iron Ingot", currency: "Credit", offers: [{ store: "Forge", owner: "Ava", storeKey: "forge", item: "IronIngotItem", itemPretty: "Iron Ingot", currency: "Credit", side: "sell", price: 8, quantity: 2, source: "live", lastDay: 2 }] }], resale: [], arbitrage: [], supplyGaps: [], warnings: [] }
const MARKET = { fetchedAtISO: "2026-07-31T12:00:00Z", markets: [{ item: "IronIngotItem", itemPretty: "Iron Ingot", currency: "Credit", medianPrice: 9, totalTrades: 7, trend: "rising" }], warnings: [] }
const JOBS = { mockData: false, professions: [], specialties: [], players: [{ name: "Ava", active: true, specialties: [{ specialty: "Smelting", level: 3, active: true }] }] }

function response(body: unknown, ok = true) {
  return Promise.resolve(new Response(JSON.stringify(body), { status: ok ? 200 : 404, headers: { "Content-Type": "application/json" } }))
}

function stubFetch({ jobs = JOBS, logistics = LOGISTICS, recipes = RECIPES, market = MARKET }: { jobs?: unknown; logistics?: unknown; recipes?: unknown; market?: unknown } = {}) {
  vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => {
    if (url.includes("/preview/recipes.json")) return response(recipes)
    if (url.includes("/preview/logistics.json")) return response(logistics)
    if (url.includes("/preview/market.json")) return response(market)
    if (url.includes("/jobs/api/v1/meta")) return response({ mockData: (jobs as typeof JOBS).mockData })
    if (url.includes("/jobs/api/v1/professions")) return response((jobs as typeof JOBS).professions)
    if (url.includes("/jobs/api/v1/specialties")) return response((jobs as typeof JOBS).specialties)
    if (url.includes("/jobs/api/v1/players")) return response((jobs as typeof JOBS).players)
    return response({}, false)
  }))
}

function renderPage(entry = "/uses/resolve?item=IronIngotItem") {
  return render(<MemoryRouter initialEntries={[entry]}><UsesResolve /></MemoryRouter>)
}

afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe("UsesResolve", () => {
  it("joins recipe, shelf, market, and observed crafter evidence", async () => {
    stubFetch()
    renderPage()
    await waitFor(() => expect(screen.getByTestId("resolve-recipe")).toBeInTheDocument())
    expect(screen.getByTestId("resolve-recipe")).toHaveTextContent("Bloomery")
    expect(screen.getByTestId("resolve-recipe")).toHaveTextContent("Unpriced inputs: CoalItem")
    expect(screen.getByTestId("resolve-offers")).toHaveTextContent("Buy from Forge")
    expect(screen.getByTestId("resolve-market")).toHaveTextContent("9 Credit median")
    expect(screen.getByTestId("resolve-crafter")).toHaveTextContent("Ava")
  })

  it("makes mock specialty data explicitly non-actionable", async () => {
    stubFetch({ jobs: { ...JOBS, mockData: true } })
    renderPage()
    await waitFor(() => expect(screen.getByTestId("resolve-mock")).toBeInTheDocument())
  })

  it("handles an unknown deep link without inventing a recipe or crafter", async () => {
    stubFetch()
    renderPage("/uses/resolve?item=UnknownItem")
    await waitFor(() => expect(screen.getByTestId("resolve-no-recipe")).toBeInTheDocument())
    expect(screen.getByTestId("resolve-no-offers")).toBeInTheDocument()
    expect(screen.getByTestId("resolve-no-skill")).toBeInTheDocument()
  })
})
