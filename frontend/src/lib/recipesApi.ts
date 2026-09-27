// Typed client for the recipe bill-of-materials plane (eco-app#101), mirroring
// `/preview/recipes.json` — the `RecipeIndex` DTO from `eco_mcp_app/recipes.py`
// (the vendored Eco Gnome recipe graph, eco-app#100).
//
import { fetchJsonOrNull } from "./api"
import type { PriceNorm } from "./priceNorm"

// Unlike the item / trade planes this is ONE static bundled payload — no
// `?server=`, no per-id endpoint. So both the list (/recipes) and the detail
// (/recipe?id=) fetch the same URL, the browser caches it, and the detail page
// resolves a single recipe client-side by `name`.

// One ingredient / product / byproduct entry. `item` is the Eco id
// (`ShaleItem`) or a tag name (`Wood`) when `isTag`; `displayName` is Eco
// Gnome's real en-US name, already prettified server-side.
export interface RecipeComponent {
  item: string
  displayName: string
  quantity: number
  isTag: boolean
}

// The profession gate: skill id + the level the recipe unlocks at.
export interface RecipeSkill {
  name: string
  level: number
}

export interface Recipe {
  name: string
  displayName: string
  product: RecipeComponent
  ingredients: RecipeComponent[]
  byproducts: RecipeComponent[]
  station: string
  stationDisplayName: string
  skill: RecipeSkill | null
  laborCost: number
  craftMinutes: number
  // Null in the vanilla seed — the crafting-table upgrade tier is derived by
  // the cost engine (eco-app#98 C), not carried by the Eco Gnome export. The
  // list page only shows a tier facet once real values land.
  tableTierRequired: number | null
  // Other recipe names in the same FamilyName (alternate ways to craft it).
  variants: string[]
  family: string
  isDefault: boolean
  isBlueprint: boolean
}

export interface RecipeSkillTalent {
  name: string
  displayName: string
  description: string
  level: number
  maxLevel: number
}

// A skill/profession definition. Root profession rows have `profession: null`;
// specialty rows point back to that root and carry their level-gated talents.
export interface RecipeSkillDef {
  name: string
  displayName: string
  profession: string | null
  maxLevel: number
  // The AutoGen graph the server now ships carries no talents, so this is
  // absent on live data. Treat a missing list as "none recorded".
  talents?: RecipeSkillTalent[]
}

export interface RecipeCounts {
  recipes: number
  skills: number
  tags: number
  products: number
  stations: number
}

export interface RecipeIndex {
  fetchedAtISO: string
  source: string
  version: number
  counts: RecipeCounts
  recipes: Recipe[]
  // product item id -> recipe names that produce it (a product can be crafted
  // multiple ways). Powers the "other ways to make this" cross-link.
  byProduct: Record<string, string[]>
  bySkill: Record<string, string[]>
  byStation: Record<string, string[]>
  skills: RecipeSkillDef[]
  // tag name -> associated item ids (a tag ingredient expands to any of these).
  tags: Record<string, string[]>
  warnings: string[]
}

export interface RecipeCostIngredient {
  /** Historical price norm (eco-app#8368). Optional while payloads gain it. */
  norm?: PriceNorm | null
  item: string
  displayName: string
  quantity: number
  isTag: boolean
  unitCost: number | null
  source: string
  subtotal: number | null
}

export interface RecipeCost {
  /** Historical price norm (eco-app#8368). Optional while payloads gain it. */
  norm?: PriceNorm | null
  recipe: string
  product: string
  yield: number
  perUnitCost: number | null
  totalCost: number | null
  ingredientCost: number | null
  laborCost: number
  timeCost: number
  laborCalories: number
  craftMinutes: number
  complete: boolean
  unpricedInputs: string[]
  ingredients: RecipeCostIngredient[]
}

export interface RecipeIndexWithCost extends RecipeIndex {
  costParams?: {
    caloriePrice: number
    minutePrice: number
  }
  recipes: Array<Recipe & { cost?: RecipeCost }>
}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const resp = await fetch(path, { signal })
  if (!resp.ok) {
    throw new Error(`recipe index fetch failed: HTTP ${resp.status}`)
  }
  return (await resp.json()) as T
}

export async function fetchRecipeIndex(signal?: AbortSignal): Promise<RecipeIndex> {
  // limit=0 asks for the whole graph. Without it the endpoint returns the MCP
  // client's 25-recipe slice (wave3_routes.py), which is not the index.
  return await getJson<RecipeIndex>("/preview/recipes.json?limit=0", signal)
}

export async function fetchRecipeIndexWithCost(
  signal?: AbortSignal,
): Promise<RecipeIndexWithCost | null> {
  const body = await fetchJsonOrNull<RecipeIndexWithCost>("/preview/recipes.json?cost=1&limit=0", signal)
  if (!body || !Array.isArray(body.recipes)) return null
  return body
}
