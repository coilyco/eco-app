import { cleanup, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"
import { MemoryRouter } from "react-router-dom"
import Recipes from "./Recipes"

// A tiny index: two recipes at different stations/skills sharing one ingredient
// (SteelBar), so the reverse-lookup and facet filters have something to bite on.
const INDEX = {
  fetchedAtISO: "2026-07-07T13:00:00+00:00",
  source: "test",
  version: 1,
  counts: { recipes: 2, skills: 2, tags: 0, products: 2, stations: 2 },
  recipes: [
    {
      name: "SteelAxeRecipe",
      displayName: "Steel Axe",
      product: { item: "SteelAxeItem", displayName: "Steel Axe", quantity: 1, isTag: false },
      ingredients: [
        { item: "SteelBarItem", displayName: "Steel Bar", quantity: 5, isTag: false },
        { item: "Wood", displayName: "Wood", quantity: 10, isTag: true },
      ],
      byproducts: [],
      station: "BlacksmithTableItem",
      stationDisplayName: "Blacksmith Table",
      skill: { name: "BlacksmithSkill", level: 3 },
      laborCost: 250,
      craftMinutes: 1.5,
      tableTierRequired: null,
      variants: [],
      family: "Steel Axe",
      isDefault: true,
      isBlueprint: false,
    },
    {
      name: "SteelBarRecipe",
      displayName: "Steel Bar",
      product: { item: "SteelBarItem", displayName: "Steel Bar", quantity: 2, isTag: false },
      ingredients: [
        { item: "IronBarItem", displayName: "Iron Bar", quantity: 4, isTag: false },
      ],
      byproducts: [],
      station: "BloomeryItem",
      stationDisplayName: "Bloomery",
      skill: { name: "SmeltingSkill", level: 1 },
      laborCost: 100,
      craftMinutes: 0.5,
      tableTierRequired: null,
      variants: [],
      family: "Steel Bar",
      isDefault: true,
      isBlueprint: false,
    },
  ],
  byProduct: { SteelAxeItem: ["SteelAxeRecipe"], SteelBarItem: ["SteelBarRecipe"] },
  bySkill: { BlacksmithSkill: ["SteelAxeRecipe"], SmeltingSkill: ["SteelBarRecipe"] },
  byStation: { BlacksmithTableItem: ["SteelAxeRecipe"], BloomeryItem: ["SteelBarRecipe"] },
  skills: [
    { name: "BlacksmithSkill", displayName: "Blacksmith", maxLevel: 7 },
    { name: "SmeltingSkill", displayName: "Smelting", maxLevel: 7 },
  ],
  tags: {},
  warnings: [],
}

function stubFetch(payload: unknown, ok = true) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify(payload), {
        status: ok ? 200 : 500,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  )
}

function renderRecipes(entry = "/recipes") {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Recipes />
    </MemoryRouter>,
  )
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe("Recipes", () => {
  it("lists recipes and deep-links each row to its card on the item page", async () => {
    stubFetch(INDEX)
    renderRecipes()

    await waitFor(() => {
      expect(screen.getByTestId("recipes-pill")).toHaveTextContent("2 recipes")
    })
    expect(screen.getAllByTestId("recipe-row")).toHaveLength(2)
    expect(screen.getByText("Steel Axe").closest("a")).toHaveAttribute(
      "href",
      "/item?id=SteelAxe#recipe-SteelAxeRecipe",
    )
  })

  it("filters by product/ingredient name via ?q=", async () => {
    stubFetch(INDEX)
    // "Iron Bar" is an ingredient of Steel Bar only, so the search on the
    // ingredient name keeps that one row.
    renderRecipes("/recipes?q=iron")

    await waitFor(() => {
      expect(screen.getAllByTestId("recipe-row")).toHaveLength(1)
    })
    expect(screen.getByText("Steel Bar")).toBeInTheDocument()
    expect(screen.queryByText("Steel Axe")).not.toBeInTheDocument()
  })

  it("reverse-lookup ?ingredient= keeps only recipes that consume it", async () => {
    stubFetch(INDEX)
    // SteelBarItem is consumed only by the Steel Axe recipe.
    renderRecipes("/recipes?ingredient=SteelBarItem")

    await waitFor(() => {
      expect(screen.getByTestId("recipes-ingredient-pill")).toHaveTextContent("Made with Steel Bar")
    })
    expect(screen.getAllByTestId("recipe-row")).toHaveLength(1)
    expect(screen.getByText("Steel Axe")).toBeInTheDocument()
  })

  it("filters by profession via ?skill=", async () => {
    stubFetch(INDEX)
    renderRecipes("/recipes?skill=SmeltingSkill")

    await waitFor(() => {
      expect(screen.getAllByTestId("recipe-row")).toHaveLength(1)
    })
    expect(screen.getByText("Steel Bar")).toBeInTheDocument()
  })

  it("shows a no-match note when filters exclude everything", async () => {
    stubFetch(INDEX)
    renderRecipes("/recipes?q=zzznotathing")

    await waitFor(() => {
      expect(screen.getByTestId("recipes-no-match")).toBeInTheDocument()
    })
  })

  it("shows the degraded empty state when the bundle is missing", async () => {
    // recipes.py returns an empty index with a warning when the graph is gone.
    stubFetch({
      ...INDEX,
      counts: { ...INDEX.counts, recipes: 0 },
      recipes: [],
      warnings: ["eco_gnome_data.json not found (bundled recipe graph missing)"],
    })
    renderRecipes()

    await waitFor(() => {
      expect(screen.getByTestId("recipes-empty")).toBeInTheDocument()
    })
    expect(screen.getByTestId("recipes-warnings")).toHaveTextContent("not found")
  })

  it("surfaces a fetch error without crashing", async () => {
    stubFetch({}, false)
    renderRecipes()

    await waitFor(() => {
      expect(screen.getByTestId("recipes-error")).toBeInTheDocument()
    })
  })
})
