import { useMemo } from "react"
import { Link, useSearchParams } from "react-router-dom"
import ItemLink, { itemHref } from "../components/ItemLink"
import FreshnessNote from "../components/FreshnessNote"
import Layout from "../components/Layout"
import {
  fetchRecipeIndex,
  type Recipe as RecipeDTO,
  type RecipeComponent,
  type RecipeIndex,
} from "../lib/recipesApi"
import { formatCount, formatDuration, prettifyEcoName } from "../lib/format"
import { useFreshData } from "../lib/useFreshData"

// Per-recipe detail (eco-app#101), modeled on pages/Item.tsx: a query-param key
// (?id=<recipe name>) reached only from /recipes. Unlike the item pivot there is
// no per-id endpoint — the whole recipe graph is one static bundled payload
// (recipes.py / eco-app#100) — so we fetch the index once and resolve one recipe
// client-side by name. Switching ?id= re-resolves against the same cached index
// (no refetch), so no per-fetch stale guard is needed; we only gate on the
// index being loaded and the id actually matching a recipe.

function craftTime(minutes: number): string {
  if (!minutes) return "—"
  return formatDuration(minutes * 60)
}

// The profession label: prefer the skill def's real display name, fall back to
// the id with its `Skill` suffix prettified off.
function skillLabel(skillName: string, index: RecipeIndex): string {
  const def = index.skills.find((s) => s.name === skillName)
  if (def) return def.displayName
  return prettifyEcoName(skillName.replace(/Skill$/, ""))
}

// One ingredient / byproduct / product row. Real items deep-link to their market
// history (/item); tags can't (they aren't a single item), so they only offer
// the reverse "what else uses this" lookup back into the directory.
function ComponentRow({ c }: { c: RecipeComponent }) {
  return (
    <li data-testid="recipe-component">
      <span className="recipe-qty">{formatCount(c.quantity)}×</span>{" "}
      {c.isTag ? (
        <span>
          {c.displayName} <span className="section-sub">(tag)</span>
        </span>
      ) : (
        <ItemLink className="linklike" item={c.item}>
          {c.displayName}
        </ItemLink>
      )}{" "}
      <Link
        className="recipe-sublink"
        to={`/recipes?ingredient=${encodeURIComponent(c.item)}`}
        data-testid="recipe-uses-link"
      >
        other uses →
      </Link>
    </li>
  )
}

export default function Recipe() {
  const [params] = useSearchParams()
  const id = params.get("id") ?? ""

  // Refresh contract lives in freshness.ts, not here (eco-app#201).
  const recipesPlane = useFreshData("recipes", fetchRecipeIndex)
  const index = recipesPlane.data
  const error = recipesPlane.error

  const recipe: RecipeDTO | null = useMemo(
    () => (index && id ? (index.recipes.find((r) => r.name === id) ?? null) : null),
    [index, id],
  )

  // Every other recipe that produces this same product — the "other ways to
  // make it" cross-link. byProduct is the authoritative map (a superset of the
  // same-family `variants`), so a product craftable at two unrelated tables is
  // fully covered.
  const alternates = useMemo(() => {
    if (!index || !recipe) return []
    const names = index.byProduct[recipe.product.item] ?? []
    return names
      .filter((n) => n !== recipe.name)
      .map((n) => index.recipes.find((r) => r.name === n))
      .filter((r): r is RecipeDTO => Boolean(r))
  }, [index, recipe])

  const pretty = recipe?.displayName ?? (id ? prettifyEcoName(id) : "")

  return (
    <Layout fetchedAtISO={index?.fetchedAtISO}>
      <section className="hero hero-compact">
        <p className="hero-kicker">
          <Link to="/recipes" className="linklike" data-testid="back-to-recipes">
            ← Recipe directory
          </Link>
        </p>
        <h1 className="hero-title">
          {pretty ? (
            <>
              How to craft <span className="accent">{pretty}</span>
            </>
          ) : (
            <>Pick a recipe</>
          )}
        </h1>
        {recipe && (
          <p className="hero-pill" data-testid="recipe-pill">
            <span className="pulse-dot" aria-hidden="true" />
            makes {formatCount(recipe.product.quantity)}×{" "}
            <ItemLink className="linklike" item={recipe.product.item}>
              {recipe.product.displayName}
            </ItemLink>
            {recipe.station ? ` · at ${prettifyEcoName(recipe.station)}` : ""}
          </p>
        )}
        {id && !recipe && error && (
          <p className="hero-pill hero-pill-muted" data-testid="recipe-error">
            recipe data unavailable right now
          </p>
        )}
        <FreshnessNote plane="recipes" loadedAt={recipesPlane.loadedAt} />
      </section>

      {!id && (
        <section>
          <p className="empty-note" data-testid="recipe-missing">
            No recipe selected. Head to the{" "}
            <Link className="linklike" to="/recipes">
              recipe directory
            </Link>{" "}
            and pick one.
          </p>
        </section>
      )}

      {/* Loaded, but the id matches nothing — a bad/stale deep link. */}
      {id && index && !recipe && !error && (
        <section>
          <p className="empty-note" data-testid="recipe-not-found">
            No recipe with id “{id}”. It may have been renamed — browse the{" "}
            <Link className="linklike" to="/recipes">
              recipe directory
            </Link>{" "}
            instead.
          </p>
        </section>
      )}

      {recipe && index && (
        <>
          <section className="recipe-facts" data-testid="recipe-facts">
            <div className="fact">
              <span className="fact-label">Station</span>
              <span className="fact-value">
                {recipe.station ? (
                  <Link
                    className="linklike"
                    to={`/recipes?station=${encodeURIComponent(recipe.station)}`}
                  >
                    {prettifyEcoName(recipe.station)}
                  </Link>
                ) : (
                  "By hand"
                )}
              </span>
            </div>
            <div className="fact">
              <span className="fact-label">Profession</span>
              <span className="fact-value">
                {recipe.skill ? (
                  <Link
                    className="linklike"
                    to={`/recipes?skill=${encodeURIComponent(recipe.skill.name)}`}
                  >
                    {skillLabel(recipe.skill.name, index)}
                    {recipe.skill.level > 0 ? ` · level ${recipe.skill.level}` : ""}
                  </Link>
                ) : (
                  "None"
                )}
              </span>
            </div>
            <div className="fact">
              <span className="fact-label">Labor</span>
              <span className="fact-value">
                {recipe.laborCost ? `${formatCount(recipe.laborCost)} cal` : "—"}
              </span>
            </div>
            <div className="fact">
              <span className="fact-label">Craft time</span>
              <span className="fact-value">{craftTime(recipe.craftMinutes)}</span>
            </div>
            {recipe.tableTierRequired != null && (
              <div className="fact">
                <span className="fact-label">Table tier</span>
                <span className="fact-value">Tier {recipe.tableTierRequired}</span>
              </div>
            )}
          </section>

          <section className="recipe-bom" data-testid="recipe-bom">
            <div>
              <h2 className="section-title">Ingredients</h2>
              {recipe.ingredients.length === 0 ? (
                <p className="empty-note">No ingredients — crafted from nothing.</p>
              ) : (
                <ul className="recipe-list" data-testid="recipe-ingredients">
                  {recipe.ingredients.map((c) => (
                    <ComponentRow key={`${c.item}-${c.isTag}`} c={c} />
                  ))}
                </ul>
              )}
            </div>
            <div>
              <h2 className="section-title">Products</h2>
              <ul className="recipe-list" data-testid="recipe-products">
                <ComponentRow c={recipe.product} />
                {recipe.byproducts.map((c) => (
                  <ComponentRow key={`${c.item}-${c.isTag}`} c={c} />
                ))}
              </ul>
            </div>
          </section>

          {/* The market / crafters / buy-sell cross-link: the item pivot already
              aggregates market price (market.py), who's crafted it (crafting.py
              by_crafted), and where to buy or sell it (logistics.py). */}
          <section className="k-card-grid k-card-grid--condensed dir-cards">
            <Link
              className="k-card dir-card"
              to={itemHref(recipe.product.item)}
              data-testid="recipe-market-link"
            >
              <h3>{recipe.product.displayName} on the market →</h3>
              <p>
                Current price, who's been crafting it, and where to buy or sell it — the item's
                full market history.
              </p>
            </Link>
            <Link
              className="k-card dir-card"
              to={`/uses/resolve?item=${encodeURIComponent(recipe.product.item)}`}
              data-testid="recipe-resolver-link"
            >
              <h3>Make, buy, or find a crafter →</h3>
              <p>Compare recipe requirements, current offers, and observed specialty holders.</p>
            </Link>
            <Link
              className="k-card dir-card"
              to={`/recipes?ingredient=${encodeURIComponent(recipe.product.item)}`}
              data-testid="recipe-consumers-link"
            >
              <h3>What it's used in →</h3>
              <p>Every recipe that takes {recipe.product.displayName} as an ingredient.</p>
            </Link>
          </section>

          {alternates.length > 0 && (
            <section>
              <h2 className="section-title">
                Other ways to make {recipe.product.displayName}{" "}
                <span className="section-sub">({alternates.length})</span>
              </h2>
              <ul className="recipe-list" data-testid="recipe-alternates">
                {alternates.map((a) => (
                  <li key={a.name}>
                    <Link className="linklike" to={`/recipe?id=${encodeURIComponent(a.name)}`}>
                      {a.displayName}
                    </Link>{" "}
                    <span className="section-sub">
                      {a.station ? `at ${prettifyEcoName(a.station)}` : "by hand"}
                      {a.skill ? ` · ${skillLabel(a.skill.name, index)}` : ""}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </>
      )}
    </Layout>
  )
}
