/// <reference types="node" />
import { readFileSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"
import { gunzipSync } from "node:zlib"
import { describe, expect, it } from "vitest"
import {
  findRecipe,
  itemPageHref,
  itemPageId,
  itemPageModel,
  recipeAnchor,
  recipeHref,
} from "../lib/itemPage"
import type { RecipeIndex } from "../lib/recipesApi"

// eco-app#8383's acceptance check over the whole recipe graph the service ships
// (data/eco_autogen_data.json.gz, already in the RecipeIndex shape the page
// receives): every recipe and every item it touches resolves to one item page,
// and that page carries the recipe.

const GRAPH = path.join(fileURLToPath(import.meta.url), "../../../../data/eco_autogen_data.json.gz")
const index = JSON.parse(gunzipSync(readFileSync(GRAPH)).toString("utf8")) as RecipeIndex

const pageOf = (href: string) => new URL(href, "https://x").searchParams.get("id") ?? ""

describe("one page per item, over the whole recipe graph", () => {
  it("loads a real graph", () => {
    expect(index.recipes.length).toBeGreaterThan(1000)
  })

  it("sends every /recipe?id= to its product's page, and that page shows the recipe card", () => {
    const misses = index.recipes.filter((r) => {
      const target = recipeHref(r)
      const model = itemPageModel(index, pageOf(target))
      return !target.endsWith(`#${recipeAnchor(r.name)}`) || !model.makes.some((m) => m.name === r.name)
    })
    expect(misses.map((r) => r.name)).toEqual([])
  })

  it("links every byproduct's page back to the recipe card on the primary product's page", () => {
    const misses = index.recipes.flatMap((r) =>
      r.byproducts
        .filter((b) => !b.isTag)
        .filter((b) => !itemPageModel(index, itemPageId(b.item)).byproductOf.some((m) => m.name === r.name))
        .map((b) => `${r.name} -> ${b.item}`),
    )
    expect(misses).toEqual([])
  })

  it("resolves every concrete item id back to a page that covers it", () => {
    const ids = new Set(
      index.recipes.flatMap((r) => [r.product, ...r.byproducts, ...r.ingredients].filter((c) => !c.isTag).map((c) => c.item)),
    )
    const misses = [...ids].filter((id) => !itemPageModel(index, pageOf(itemPageHref(id))).itemIds.includes(id))
    expect(misses).toEqual([])
  })

  it("lands both of Kai's example URLs on the same Acorn Powder page with both recipes", () => {
    const fromRecipe = findRecipe(index, "AcornPowder")
    expect(fromRecipe).not.toBeNull()
    expect(pageOf(recipeHref(fromRecipe!))).toBe("AcornPowder")
    expect(pageOf(itemPageHref("AcornPowderItem"))).toBe("AcornPowder")
    const model = itemPageModel(index, "AcornPowder")
    expect(model.displayName).toBe("Acorn Powder")
    expect(model.makes.map((r) => r.name)).toEqual(["AcornPowder", "ProcessedAcornPowder"])
  })

  it("folds the export's split spellings onto one page", () => {
    const model = itemPageModel(index, "GoldScrap")
    expect(model.itemIds).toEqual(["GoldScrapItem", "GoldScrap"])
    expect(model.byproductOf.length).toBeGreaterThan(0)
    expect(model.usedIn.length).toBeGreaterThan(0)
  })
})
