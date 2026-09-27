import { useMemo } from "react"
import { Link, useSearchParams } from "react-router-dom"
import EcoRichText from "../components/EcoRichText"
import ItemLink from "../components/ItemLink"
import FreshnessNote from "../components/FreshnessNote"
import ItemPrice from "../components/ItemPrice"
import Layout from "../components/Layout"
import { formatCount, prettifyEcoName } from "../lib/format"
import { fetchJobsData } from "../lib/jobsApi"
import { fetchLogistics, type ShelfOffer } from "../lib/logisticsApi"
import { fetchMarket, type MarketTrend } from "../lib/marketApi"
import { fetchRecipeIndexWithCost } from "../lib/recipesApi"
import { useFreshData } from "../lib/useFreshData"

function skillKey(value: string): string {
  return value.replace(/Skill$/, "").replace(/[^a-z0-9]/gi, "").toLowerCase()
}

function sourceLabel(source: string): string {
  return source === "live" ? "listed now" : "from past sales"
}

const TREND_WORDS: Record<MarketTrend, string> = {
  rising: "rising",
  falling: "falling",
  flat: "flat",
  insufficient: "too few sales to tell",
}

function offersFor(row: { offers: ShelfOffer[] } | null, side: "sell" | "buy"): ShelfOffer[] {
  if (!row) return []
  return [...row.offers].filter((offer) => offer.side === side).sort((a, b) => a.price - b.price)
}

// A read-only item resolver (eco-app#180). It composes existing recipe, market,
// shelf, and observed-specialty planes. It never infers a player's inventory or
// online state: missing inputs are only unpriced recipe inputs, and crafters are
// holders observed by the jobs snapshot.
export default function UsesResolve() {
  const [params, setParams] = useSearchParams()
  const item = params.get("item") ?? ""
  // Refresh contract lives in freshness.ts, not here (eco-app#201). All four
  // planes refresh together so the resolve answer is internally consistent
  // rather than stitched from reads minutes apart.
  const resolvePlane = useFreshData("resolve", async (signal) => {
    const [recipes, logistics, market, jobs] = await Promise.all([
      fetchRecipeIndexWithCost(signal).catch(() => null),
      fetchLogistics(signal).catch(() => null),
      fetchMarket(signal).catch(() => null),
      fetchJobsData(signal).catch(() => null),
    ])
    return { recipes, logistics, market, jobs }
  })
  const recipes = resolvePlane.data?.recipes ?? null
  const logistics = resolvePlane.data?.logistics ?? null
  const market = resolvePlane.data?.market ?? null
  const jobs = resolvePlane.data?.jobs ?? null
  const loaded = !resolvePlane.loading

  const options = useMemo(() => {
    const values = new Map<string, string>()
    recipes?.recipes.forEach((recipe) => values.set(recipe.product.item, recipe.product.displayName))
    logistics?.cheapest.forEach((row) => values.set(row.item, row.itemPretty))
    logistics?.resale.forEach((row) => values.set(row.item, row.itemPretty))
    market?.markets.forEach((row) => values.set(row.item, row.itemPretty))
    return [...values].sort((a, b) => a[1].localeCompare(b[1]))
  }, [logistics, market, recipes])
  const selectedRecipes = useMemo(
    () => recipes?.recipes.filter((recipe) => recipe.product.item === item) ?? [],
    [item, recipes],
  )
  const sellRow = useMemo(
    () => logistics?.cheapest.find((row) => row.item === item) ?? null,
    [item, logistics],
  )
  const buyRow = useMemo(
    () => logistics?.resale.find((row) => row.item === item) ?? null,
    [item, logistics],
  )
  const marketRow = useMemo(() => market?.markets.find((row) => row.item === item) ?? null, [item, market])
  const pretty = item ? options.find(([id]) => id === item)?.[1] ?? prettifyEcoName(item) : ""
  const requiredSkills = useMemo(
    () => new Set(selectedRecipes.flatMap((recipe) => (recipe.skill ? [skillKey(recipe.skill.name)] : []))),
    [selectedRecipes],
  )
  const crafters = useMemo(
    () =>
      jobs?.players
        .flatMap((player) =>
          player.specialties
            .filter((specialty) => requiredSkills.has(skillKey(specialty.specialty)))
            .map((specialty) => ({ name: player.name, active: player.active && specialty.active, specialty })),
        )
        .sort((a, b) => Number(b.active) - Number(a.active) || b.specialty.level - a.specialty.level || a.name.localeCompare(b.name)) ?? [],
    [jobs, requiredSkills],
  )

  const selectItem = (next: string) => setParams(next ? { item: next } : {}, { replace: false })
  const sells = offersFor(sellRow, "sell")
  const buys = offersFor(buyRow, "buy").sort((a, b) => b.price - a.price)

  return (
    <Layout fetchedAtISO={recipes?.fetchedAtISO ?? logistics?.fetchedAtISO ?? market?.fetchedAtISO}>
      <section className="hero hero-compact">
        <p className="hero-kicker">
          <Link to="/uses" className="linklike">← Use cases</Link>
        </p>
        <h1 className="hero-title">
          Make, buy, or find a crafter for {pretty ? <ItemLink className="accent linklike" item={item}>{pretty}</ItemLink> : <span className="accent">X</span>}?
        </h1>
        <p className="hero-tagline">What it takes to craft it, what shops charge, and who has the specialty to make it, all on one page.</p>
        {loaded && !recipes && !logistics && !market && !jobs && (
          <p className="hero-pill hero-pill-muted" data-testid="resolve-error">Data for this page is not available right now.</p>
        )}
        <FreshnessNote
          plane="resolve"
          loadedAt={resolvePlane.loadedAt}
          refreshing={resolvePlane.refreshing}
          refreshError={resolvePlane.refreshError}
          onRefresh={resolvePlane.refresh}
        />
      </section>

      {!loaded && <p className="empty-note" data-testid="resolve-loading">Loading recipes, shop listings, and crafters…</p>}

      {loaded && !item && (
        <section>
          <h2 className="section-title">Pick an item</h2>
          {options.length === 0 ? (
            <p className="empty-note" data-testid="resolve-no-options">No items to pick from yet.</p>
          ) : (
            <ul className="rank-rows" data-testid="resolve-picker">
              {options.slice(0, 200).map(([id, name]) => (
                <li key={id}><button className="rank-row" onClick={() => selectItem(id)}><span className="rank-name">{name}</span><span className="rank-count">See options →</span></button></li>
              ))}
            </ul>
          )}
        </section>
      )}

      {item && loaded && (
        <>
          <section className="atlas-columns" data-testid="resolve-make">
            <div>
              <h2 className="section-title">Make</h2>
              {!recipes ? <p className="empty-note">Recipes are not available right now.</p> : selectedRecipes.length === 0 ? <p className="empty-note" data-testid="resolve-no-recipe">We have no recipe for this item.</p> : (
                <ul className="rank-rows">
                  {selectedRecipes.map((recipe) => {
                    const unpriced = recipe.cost?.unpricedInputs ?? []
                    return <li key={recipe.name} data-testid="resolve-recipe"><div className="rank-row"><span className="rank-name">{recipe.displayName}<br /><span className="section-sub">{recipe.station ? prettifyEcoName(recipe.station) : "Hand craft"}{recipe.skill ? ` · ${prettifyEcoName(recipe.skill.name.replace(/Skill$/, ""))} ${recipe.skill.level}+` : ""}</span></span><span className="rank-count">{recipe.cost?.perUnitCost != null ? <ItemPrice price={recipe.cost.perUnitCost} norm={recipe.cost.norm} suffix="/unit" data-testid="resolve-unit-cost" /> : "cost unknown"}</span></div>{unpriced.length > 0 && <p className="empty-note">Ingredients with no price: {unpriced.join(", ")}. We can't tell if you can get them.</p>}</li>
                  })}
                </ul>
              )}
            </div>
            <div>
              <h2 className="section-title">Buy or sell</h2>
              {!logistics ? <p className="empty-note">Shop listings are not available right now.</p> : sells.length === 0 && buys.length === 0 ? <p className="empty-note" data-testid="resolve-no-offers">No shop listings for this item, now or from past sales.</p> : <ul className="rank-rows" data-testid="resolve-offers">{[...sells.slice(0, 4), ...buys.slice(0, 4)].map((offer, index) => <li key={`${offer.storeKey}-${offer.side}-${index}`}><div className="rank-row"><span className="rank-name">{offer.side === "sell" ? "Buy from" : "Sell to"} <EcoRichText text={offer.store} /><br /><span className="section-sub">{sourceLabel(offer.source)}</span></span><span className="rank-count"><ItemPrice price={offer.price} norm={offer.norm} currency={offer.currency} showCurrency suffix={`, ${formatCount(offer.quantity)} qty`} /></span></div></li>)}</ul>}
              {marketRow && <p className="section-sub" data-testid="resolve-market">Recent sales: <ItemPrice price={marketRow.medianPrice} norm={marketRow.norm} currency={marketRow.currency} showCurrency prefix="middle price " layout="inline" />, {formatCount(marketRow.totalTrades)} trades, {TREND_WORDS[marketRow.trend]}.</p>}
            </div>
          </section>

          <section data-testid="resolve-crafters">
            <h2 className="section-title">Find someone who can craft it</h2>
            {!jobs ? <p className="empty-note">The crafter list is not available right now.</p> : jobs.mockData ? <p className="empty-note" data-testid="resolve-mock">The crafter list is made-up test data right now, so it can't name a real crafter.</p> : requiredSkills.size === 0 ? <p className="empty-note" data-testid="resolve-no-skill">No specialty is listed for this recipe.</p> : crafters.length === 0 ? <p className="empty-note" data-testid="resolve-no-crafter">We haven't seen anyone with the specialty this recipe needs. Someone may still be able to craft it.</p> : <ul className="rank-rows">{crafters.map((crafter) => <li key={`${crafter.name}-${crafter.specialty.specialty}`} data-testid="resolve-crafter"><div className="rank-row"><span className="rank-name">{crafter.name}<br /><span className="section-sub">{prettifyEcoName(crafter.specialty.specialty.replace(/Skill$/, ""))} level {crafter.specialty.level}</span></span><span className="rank-count">{crafter.active ? "active at last check" : "not active at last check"}</span></div></li>)}</ul>}
            {jobs && !jobs.mockData && <p className="section-sub">This list shows who had the specialty at the last check. It can't tell you if they are free, have the materials, or want to craft for you.</p>}
          </section>
        </>
      )}
    </Layout>
  )
}
