import { useMemo } from "react"
import { Link, useSearchParams } from "react-router-dom"
import ItemLink from "../components/ItemLink"
import FreshnessNote from "../components/FreshnessNote"
import Layout from "../components/Layout"
import { recipeHref } from "../lib/itemPage"
import { fetchRecipeIndex, type Recipe } from "../lib/recipesApi"
import { formatCount, formatDuration, prettifyEcoName } from "../lib/format"
import { useFreshData } from "../lib/useFreshData"

const LIST_ROWS = 200

// The recipe browse surface (eco-app#101), modeled on pages/Items.tsx: every
// control is URL-driven and deep-linkable, rows deep-link to the per-recipe
// card on its item page (recipeHref, eco-app#8383). The whole recipe graph is one static bundled
// payload (recipes.py / eco-app#100), so filtering is entirely client-side.
//
// Filters: ?q= (product + ingredient name search, the default), ?skill= (the
// profession gate), ?station=, and ?ingredient= — the reverse lookup, "what can
// I make with X", the highest-value filter, reached by clicking an ingredient
// on the detail page. ?tier= (table tier) lights up only once the cost engine
// (eco-app#98 C) fills tableTierRequired; it is null across the vanilla seed.

// Craft time reads better as a coarse duration than a raw "0.64 min".
function craftTime(minutes: number): string {
  if (!minutes) return "—"
  return formatDuration(minutes * 60)
}

// A one-line ingredient summary for the list row: the first few pretty names,
// with a "+N more" tail so a long BOM stays on one line.
function ingredientSummary(r: Recipe) {
  if (r.ingredients.length === 0) return "—"
  const shown = r.ingredients.slice(0, 3)
  return (
    <>
      {shown.map((ingredient, i) => (
        <span key={`${ingredient.item}-${i}`}>
          <ItemLink className="linklike" item={ingredient.isTag ? null : ingredient.item}>
            {ingredient.displayName}
          </ItemLink>
          {i < shown.length - 1 ? ", " : ""}
        </span>
      ))}
      {r.ingredients.length > shown.length ? ` +${r.ingredients.length - shown.length} more` : ""}
    </>
  )
}

export default function Recipes() {
  // Refresh contract lives in freshness.ts, not here (eco-app#201).
  const recipesPlane = useFreshData("recipes", fetchRecipeIndex)
  const index = recipesPlane.data
  const error = recipesPlane.error
  const [params, setParams] = useSearchParams()
  const q = params.get("q") ?? ""
  const skill = params.get("skill") ?? ""
  const station = params.get("station") ?? ""
  const ingredient = params.get("ingredient") ?? ""
  const tier = params.get("tier") ?? ""


  // Preserve the other params when one control changes.
  const update = (patch: Record<string, string>) => {
    const next: Record<string, string> = {}
    if (q) next.q = q
    if (skill) next.skill = skill
    if (station) next.station = station
    if (ingredient) next.ingredient = ingredient
    if (tier) next.tier = tier
    for (const [k, v] of Object.entries(patch)) {
      if (v) next[k] = v
      else delete next[k]
    }
    setParams(next, { replace: false })
  }

  // Station facet options, prettified and sorted by display name.
  const stationOptions = useMemo(() => {
    if (!index) return []
    return Object.keys(index.byStation)
      .map((id) => ({ id, label: prettifyEcoName(id) }))
      .sort((a, b) => a.label.localeCompare(b.label))
  }, [index])

  // Distinct non-null table tiers — empty on the vanilla seed, so the facet
  // stays hidden until the cost engine derives tiers (eco-app#98 C).
  const tierOptions = useMemo(() => {
    if (!index) return []
    const tiers = new Set<number>()
    for (const r of index.recipes) {
      if (r.tableTierRequired != null) tiers.add(r.tableTierRequired)
    }
    return [...tiers].sort((a, b) => a - b)
  }, [index])

  const needle = q.trim().toLowerCase()
  const visible = useMemo(() => {
    if (!index) return []
    let rows = index.recipes
    if (skill) rows = rows.filter((r) => r.skill?.name === skill)
    if (station) rows = rows.filter((r) => r.station === station)
    if (ingredient) rows = rows.filter((r) => r.ingredients.some((i) => i.item === ingredient))
    if (tier) rows = rows.filter((r) => String(r.tableTierRequired) === tier)
    if (needle) {
      rows = rows.filter((r) => {
        if (r.displayName.toLowerCase().includes(needle)) return true
        return r.ingredients.some((i) => i.displayName.toLowerCase().includes(needle))
      })
    }
    return rows.slice(0, LIST_ROWS)
  }, [index, skill, station, ingredient, tier, needle])

  const total = index?.recipes.length ?? 0
  const anyFilter = Boolean(q || skill || station || ingredient || tier)

  return (
    <Layout fetchedAtISO={index?.fetchedAtISO}>
      <section className="hero hero-compact">
        <p className="hero-kicker">Recipe directory</p>
        <h1 className="hero-title">
          Every way to <span className="accent">craft</span> in Eco
        </h1>
        {index && (
          <p className="hero-pill" data-testid="recipes-pill">
            <span className="pulse-dot" aria-hidden="true" />
            {formatCount(index.counts.recipes)} recipes · {formatCount(index.counts.products)}{" "}
            products · {formatCount(index.counts.stations)} stations
          </p>
        )}
        {!index && error && (
          <p className="hero-pill hero-pill-muted" data-testid="recipes-error">
            recipe directory unavailable right now
          </p>
        )}
        <FreshnessNote plane="recipes" loadedAt={recipesPlane.loadedAt} />
      </section>

      {index && total === 0 && (
        <section>
          <p className="empty-note" data-testid="recipes-empty">
            No recipe data bundled. The recipe graph ships as vendored data (eco-app#100) — if
            this persists the bundle is missing from the build.
          </p>
          {index.warnings.length > 0 && (
            <ul className="warn-list" data-testid="recipes-warnings">
              {index.warnings.map((w) => (
                <li key={w}>⚠ {w}</li>
              ))}
            </ul>
          )}
        </section>
      )}

      {index && total > 0 && (
        <>
          <section className="filter-row">
            <input
              className="filter-input"
              type="search"
              placeholder="Filter by product or ingredient name… (deep-linkable as ?q=)"
              value={q}
              onChange={(e) => update({ q: e.target.value })}
              data-testid="recipes-filter"
            />
            <select
              className="filter-select"
              value={skill}
              onChange={(e) => update({ skill: e.target.value })}
              data-testid="recipes-skill-filter"
              aria-label="Filter by profession"
            >
              <option value="">Any profession</option>
              {index.skills.map((s) => (
                <option key={s.name} value={s.name}>
                  {s.displayName}
                </option>
              ))}
            </select>
            <select
              className="filter-select"
              value={station}
              onChange={(e) => update({ station: e.target.value })}
              data-testid="recipes-station-filter"
              aria-label="Filter by station"
            >
              <option value="">Any station</option>
              {stationOptions.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.label}
                </option>
              ))}
            </select>
            {tierOptions.length > 0 && (
              <select
                className="filter-select"
                value={tier}
                onChange={(e) => update({ tier: e.target.value })}
                data-testid="recipes-tier-filter"
                aria-label="Filter by table tier"
              >
                <option value="">Any tier</option>
                {tierOptions.map((t) => (
                  <option key={t} value={String(t)}>
                    Tier {t}
                  </option>
                ))}
              </select>
            )}
            {anyFilter && (
              <button
                className="button"
                onClick={() => setParams({}, { replace: false })}
                data-testid="recipes-clear"
              >
                Clear
              </button>
            )}
          </section>

          {ingredient && (
            <section className="controls-row" data-testid="recipes-ingredient-pill">
              <button
                className="chip chip-active"
                aria-label={`Clear ingredient filter: ${prettifyEcoName(ingredient)}`}
                onClick={() => update({ ingredient: "" })}
              >
                Made with {prettifyEcoName(ingredient)} ✕
              </button>
            </section>
          )}

          <section>
            <h2 className="section-title">
              Recipes{" "}
              <span className="section-sub">
                (showing {visible.length}
                {total > visible.length ? ` of ${formatCount(total)}` : ""})
              </span>
            </h2>
            {visible.length === 0 ? (
              <p className="empty-note" data-testid="recipes-no-match">
                No recipes match these filters.
              </p>
            ) : (
              <table tabIndex={0} className="ledger-table" data-testid="recipes-table">
                <thead>
                  <tr>
                    <th>Recipe</th>
                    <th>Station</th>
                    <th>Profession</th>
                    <th>Ingredients</th>
                    <th className="num">Labor</th>
                    <th className="num">Time</th>
                  </tr>
                </thead>
                <tbody>
                  {visible.map((r) => (
                    <tr key={r.name} data-testid="recipe-row">
                      <td>
                        <Link
                          className="linklike"
                          to={recipeHref(r)}
                        >
                          {r.displayName}
                        </Link>
                      </td>
                      <td>{r.station ? prettifyEcoName(r.station) : "—"}</td>
                      <td>
                        {r.skill
                          ? `${prettifyEcoName(r.skill.name.replace(/Skill$/, ""))} ${r.skill.level > 0 ? `L${r.skill.level}` : ""}`.trim()
                          : "—"}
                      </td>
                      <td>{ingredientSummary(r)}</td>
                      <td className="num">{r.laborCost ? formatCount(r.laborCost) : "—"}</td>
                      <td className="num">{craftTime(r.craftMinutes)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>

          {index.warnings.length > 0 && (
            <section>
              <ul className="warn-list" data-testid="recipes-warnings">
                {index.warnings.map((w) => (
                  <li key={w}>⚠ {w}</li>
                ))}
              </ul>
            </section>
          )}

          <section className="k-card-grid k-card-grid--condensed dir-cards">
            <Link className="k-card dir-card" to="/items" data-testid="link-items">
              <h2>Item directory →</h2>
              <p>Every item's market history — trades, price, who's making it, and shelves.</p>
            </Link>
            <Link className="k-card dir-card" to="/crafting" data-testid="link-crafting">
              <h2>Crafting atlas →</h2>
              <p>What the world is actually making — top items, stations, and crafters.</p>
            </Link>
          </section>
        </>
      )}
    </Layout>
  )
}
