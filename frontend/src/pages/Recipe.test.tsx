import { cleanup, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom"
import RecipeCard from "../components/RecipeCard"
import type { RecipeIndex } from "../lib/recipesApi"
import Recipe from "./Recipe"

// Two recipes producing the SAME product (SteelBar) two ways, so the "other
// ways to make it" cross-link (built from byProduct, not just same-family
// variants) has a sibling to point at.
const INDEX = {
  fetchedAtISO: "2026-07-07T13:00:00+00:00",
  source: "test",
  version: 1,
  counts: { recipes: 2, skills: 1, tags: 1, products: 1, stations: 2 },
  recipes: [
    {
      name: "SteelBarRecipe",
      displayName: "Steel Bar",
      product: { item: "SteelBarItem", displayName: "Steel Bar", quantity: 2, isTag: false },
      ingredients: [
        { item: "IronBarItem", displayName: "Iron Bar", quantity: 4, isTag: false },
        { item: "Charcoal", displayName: "Charcoal", quantity: 3, isTag: true },
      ],
      byproducts: [{ item: "SlagItem", displayName: "Slag", quantity: 1, isTag: false }],
      station: "BloomeryItem",
      stationDisplayName: "Bloomery",
      skill: { name: "SmeltingSkill", level: 2 },
      laborCost: 100,
      craftMinutes: 0.5,
      tableTierRequired: null,
      variants: [],
      family: "Steel Bar",
      isDefault: true,
      isBlueprint: false,
    },
    {
      name: "SteelBarBlastRecipe",
      displayName: "Steel Bar (Blast Furnace)",
      product: { item: "SteelBarItem", displayName: "Steel Bar", quantity: 5, isTag: false },
      ingredients: [{ item: "IronBarItem", displayName: "Iron Bar", quantity: 8, isTag: false }],
      byproducts: [],
      station: "BlastFurnaceItem",
      stationDisplayName: "Blast Furnace",
      skill: { name: "AdvancedSmeltingSkill", level: 4 },
      laborCost: 200,
      craftMinutes: 1,
      tableTierRequired: null,
      variants: [],
      family: "Steel Bar",
      isDefault: false,
      isBlueprint: false,
    },
  ],
  byProduct: { SteelBarItem: ["SteelBarRecipe", "SteelBarBlastRecipe"] },
  bySkill: {
    SmeltingSkill: ["SteelBarRecipe"],
    AdvancedSmeltingSkill: ["SteelBarBlastRecipe"],
  },
  byStation: { BloomeryItem: ["SteelBarRecipe"], BlastFurnaceItem: ["SteelBarBlastRecipe"] },
  skills: [{ name: "SmeltingSkill", displayName: "Smelting", maxLevel: 7 }],
  tags: { Charcoal: ["CharcoalItem"] },
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

function Where() {
  const loc = useLocation()
  return <p data-testid="where">{`${loc.pathname}${loc.search}${loc.hash}`}</p>
}

function renderRecipe(entry: string) {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/recipe" element={<Recipe />} />
        <Route path="/item" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  )
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

// A recipe has no page of its own any more (eco-app#8383): /recipe?id= lands on
// its card on the product's item page.
describe("Recipe", () => {
  it("redirects to the recipe's card on its product's item page", async () => {
    stubFetch(INDEX)
    renderRecipe("/recipe?id=SteelBarBlastRecipe")

    await waitFor(() => {
      expect(screen.getByTestId("where")).toHaveTextContent("/item?id=SteelBar#recipe-SteelBarBlastRecipe")
    })
  })

  it("sends an item id that arrived here to that item's page", async () => {
    stubFetch(INDEX)
    renderRecipe("/recipe?id=SteelBar")

    await waitFor(() => {
      expect(screen.getByTestId("where")).toHaveTextContent("/item?id=SteelBar")
    })
  })

  it("shows the missing-selection note with no id", async () => {
    stubFetch(INDEX)
    renderRecipe("/recipe")

    await waitFor(() => {
      expect(screen.getByTestId("recipe-missing")).toBeInTheDocument()
    })
  })

  it("shows a not-found note for an unknown id (degraded deep link)", async () => {
    stubFetch(INDEX)
    renderRecipe("/recipe?id=NoSuchRecipe")

    await waitFor(() => {
      expect(screen.getByTestId("recipe-not-found")).toBeInTheDocument()
    })
  })

  it("surfaces a fetch error without crashing", async () => {
    stubFetch({}, false)
    renderRecipe("/recipe?id=SteelBarRecipe")

    await waitFor(() => {
      expect(screen.getByTestId("recipe-error")).toBeInTheDocument()
    })
  })
})

describe("RecipeCard", () => {
  const renderCard = () =>
    render(
      <MemoryRouter>
        <RecipeCard recipe={INDEX.recipes[0] as unknown as RecipeIndex["recipes"][number]} index={INDEX as unknown as RecipeIndex} />
      </MemoryRouter>,
    )

  it("renders the ingredients, what it makes, and the facts", () => {
    renderCard()
    expect(screen.getByTestId("recipe-card")).toHaveAttribute("id", "recipe-SteelBarRecipe")
    expect(screen.getByTestId("recipe-ingredients")).toHaveTextContent("Iron Bar")
    expect(screen.getByTestId("recipe-ingredients")).toHaveTextContent("Charcoal")
    expect(screen.getByTestId("recipe-products")).toHaveTextContent("Slag")
    expect(screen.getByTestId("recipe-facts")).toHaveTextContent("Smelting")
    expect(screen.getByTestId("recipe-facts")).toHaveTextContent("100 cal")
  })

  it("links item ingredients to their page and gives tags the directory lookup", () => {
    renderCard()
    expect(screen.getByText("Iron Bar").closest("a")).toHaveAttribute("href", "/item?id=IronBar")
    expect(screen.getByText("Charcoal").closest("a")).toBeNull()
    expect(screen.getByTestId("recipe-uses-link")).toHaveAttribute("href", "/recipes?ingredient=Charcoal")
  })
})
