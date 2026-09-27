import type { Recipe, RecipeIndex } from "./recipesApi"

// One page per item (eco-app#8383). The page is keyed by the item id with its
// `Item` suffix dropped, which is the token a recipe URL already uses in the
// common case (`AcornPowder` makes `AcornPowderItem`). The same rule folds the
// export's split spellings onto one page: byproducts arrive as `GoldScrap` and
// ingredients as `GoldScrapItem`, and a player sees one "Gold Scrap".

export function itemPageId(item: string): string {
  return item.endsWith("Item") && item.length > "Item".length ? item.slice(0, -"Item".length) : item
}

export function itemPageHref(item: string): string {
  return `/item?id=${encodeURIComponent(itemPageId(item))}`
}

export function recipeAnchor(recipeName: string): string {
  return `recipe-${recipeName}`
}

// A recipe lives as a card on its primary product's page. Byproducts link back
// to that card rather than growing cards of their own.
export function recipeHref(recipe: Pick<Recipe, "name" | "product">): string {
  return `${itemPageHref(recipe.product.item)}#${recipeAnchor(recipe.name)}`
}

export interface ItemPageModel {
  pageId: string
  /** Every concrete graph id this page covers, suffixed spelling first. */
  itemIds: string[]
  displayName: string | null
  /** Recipes whose primary product is this item, the main recipe first. */
  makes: Recipe[]
  /** Recipes that yield this item as a byproduct. */
  byproductOf: Recipe[]
  /** Recipes that take this item as an ingredient, directly or through a tag. */
  usedIn: Recipe[]
}

interface GraphLookup {
  byName: Map<string, Recipe>
  displayNames: Map<string, string>
}

const lookups = new WeakMap<RecipeIndex, GraphLookup>()

function lookup(index: RecipeIndex): GraphLookup {
  let found = lookups.get(index)
  if (!found) {
    const byName = new Map<string, Recipe>()
    const displayNames = new Map<string, string>()
    for (const r of index.recipes) {
      byName.set(r.name, r)
      for (const c of [r.product, ...r.byproducts, ...r.ingredients]) {
        if (!c.isTag && !displayNames.has(c.item)) displayNames.set(c.item, c.displayName)
      }
    }
    found = { byName, displayNames }
    lookups.set(index, found)
  }
  return found
}

export function findRecipe(index: RecipeIndex, name: string): Recipe | null {
  return lookup(index).byName.get(name) ?? null
}

export function itemIdsFor(index: RecipeIndex, pageId: string): string[] {
  const known = lookup(index).displayNames
  return [`${pageId}Item`, pageId].filter((id) => known.has(id))
}

// The id the market pivot is keyed by. Trades carry the game's class name, so
// a recipe's product id wins (it finds skill books, which have no suffix), and
// otherwise the suffixed spelling, which also covers the split scrap items.
export function pivotItemFor(makes: Recipe[], pageId: string): string {
  return makes[0]?.product.item ?? `${pageId}Item`
}

const byDisplayName = (a: Recipe, b: Recipe) => a.displayName.localeCompare(b.displayName)

// isDefault is per recipe family, so two recipes for one item can both carry
// it. The recipe named like the page is the main one.
export function sortMakes(recipes: Recipe[], pageId: string): Recipe[] {
  return [...recipes].sort(
    (a, b) =>
      Number(b.name === pageId) - Number(a.name === pageId) ||
      Number(b.isDefault) - Number(a.isDefault) ||
      byDisplayName(a, b),
  )
}

export function itemPageModel(index: RecipeIndex, pageId: string): ItemPageModel {
  const { byName, displayNames } = lookup(index)
  const itemIds = itemIdsFor(index, pageId)
  const ids = new Set(itemIds)

  const makes = sortMakes(
    [...new Set(itemIds.flatMap((id) => index.byProduct[id] ?? []))]
      .map((n) => byName.get(n))
      .filter((r): r is Recipe => Boolean(r)),
    pageId,
  )

  const tagsHolding = new Set(
    Object.entries(index.tags)
      .filter(([, members]) => members.some((m) => ids.has(m)))
      .map(([tag]) => tag),
  )
  const byproductOf: Recipe[] = []
  const usedIn: Recipe[] = []
  for (const r of index.recipes) {
    if (r.byproducts.some((c) => !c.isTag && ids.has(c.item))) byproductOf.push(r)
    if (r.ingredients.some((c) => (c.isTag ? tagsHolding.has(c.item) : ids.has(c.item)))) usedIn.push(r)
  }

  return {
    pageId,
    itemIds,
    displayName: itemIds.map((id) => displayNames.get(id)).find(Boolean) ?? null,
    makes,
    byproductOf: byproductOf.sort(byDisplayName),
    usedIn: usedIn.sort(byDisplayName),
  }
}
