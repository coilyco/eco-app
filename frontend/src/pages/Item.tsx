import { useEffect, useState } from "react"
import { Link, Navigate, useLocation, useSearchParams } from "react-router-dom"
import EcoRichText from "../components/EcoRichText"
import FreshnessNote from "../components/FreshnessNote"
import ItemPrice from "../components/ItemPrice"
import Layout from "../components/Layout"
import RecipeCard from "../components/RecipeCard"
import { itemPageId, itemPageModel, pivotItemFor, recipeHref, sortMakes } from "../lib/itemPage"
import { fetchItemPivot, type ItemFeedRow, type ItemPivot } from "../lib/itemsApi"
import { fetchRecipeIndex, fetchRecipesForProduct, type Recipe } from "../lib/recipesApi"
import { formatCount, formatDuration, formatRelative, prettifyEcoName } from "../lib/format"
import type { PriceNorm } from "../lib/priceNorm"
import { useFreshData } from "../lib/useFreshData"

// Maps the raw production action id to a past-tense verb for the feed lines.
const ACTION_VERBS: Record<string, string> = {
  ItemCraftedAction: "crafted",
  HarvestOrHunt: "harvested",
  ChopTree: "felled",
  DigOrMine: "mined",
}

const PAGE_SIZE = 50
// "What it's used in" can run to hundreds for a tag member like a log. The
// recipe directory already filters by ingredient, so the rest go there.
const USED_IN_SHOWN = 30

type EventType = "all" | "craft" | "trade"

// A run of collapsed identical events reads as "(×N over 3 minutes)". A single
// event carries no run tail.
function runTail(row: ItemFeedRow): string {
  if (row.runCount <= 1) return ""
  const noun = row.kind === "craft" ? "crafts" : "trades"
  return ` (${formatCount(row.runCount)} ${noun} over ${formatDuration(row.spanSeconds)})`
}

// One feed row rendered as a single relative-time sentence. Compressed runs sum
// their quantity, so "crafted 100 Hewn Log" is the whole run, not one event.
// Feed rows and offers share the pivot's one item-level norm (eco-app#8368).
function FeedLine({ row, item, now, norm }: { row: ItemFeedRow; item: string; now: number; norm?: PriceNorm | null }) {
  const when = formatRelative(row.time, now)
  if (row.kind === "craft") {
    const verb = ACTION_VERBS[row.actionType] ?? "made"
    const at =
      row.station && row.station !== "(hand)" ? ` at ${prettifyEcoName(row.station)}` : ""
    return (
      <li data-testid="item-feed-row">
        <span className="pivot-day">{when}</span>{" "}
        <strong><EcoRichText text={row.actor || "someone"} /></strong> {verb} {formatCount(row.quantity)} {item}
        {at}
        {runTail(row)}
      </li>
    )
  }
  return (
    <li data-testid="item-feed-row">
      <span className="pivot-day">{when}</span>{" "}
      <strong><EcoRichText text={row.seller || "someone"} /></strong> sold {formatCount(row.quantity)} {item} to{" "}
      <strong><EcoRichText text={row.buyer || "someone"} /></strong>
      {row.unitPrice !== null && row.currency ? (
        <>
          {" "}
          <ItemPrice
            price={row.unitPrice}
            norm={row.norm ?? norm}
            currency={row.currency}
            showCurrency
            prefix="@ "
            layout="inline"
          />
        </>
      ) : null}
      {runTail(row)}
    </li>
  )
}

function RecipeLinks({ recipes, testId }: { recipes: Recipe[]; testId: string }) {
  return (
    <ul className="recipe-list" data-testid={testId}>
      {recipes.map((r) => (
        <li key={r.name}>
          <Link className="linklike" to={recipeHref(r)}>
            {r.displayName}
          </Link>{" "}
          <span className="section-sub">{r.station ? `at ${prettifyEcoName(r.station)}` : "by hand"}</span>
        </li>
      ))}
    </ul>
  )
}

// One page per item (eco-app#8383), keyed ?id=<item id minus "Item">: the market
// (an actionable summary over a reverse-chrono feed of crafts and trades), every
// recipe that makes it as a card, and, one tap away, what it goes into. Search / actor / type
// filters and the page are deep-linkable (?q= ?actor= ?type= ?page=), and
// /recipe?id= lands on its card here by anchor.
export default function Item() {
  const [params, setParams] = useSearchParams()
  const location = useLocation()
  const id = params.get("id") ?? ""
  const legacyItem = params.get("item") ?? ""
  const q = params.get("q") ?? ""
  const actor = params.get("actor") ?? ""
  const type = (params.get("type") ?? "all") as EventType
  const page = Math.max(1, Number.parseInt(params.get("page") ?? "1", 10) || 1)

  // This item's recipes come first and alone: the whole graph is 1.2 MB, too
  // heavy for the most-linked page, so it loads only when asked for (uses and
  // byproducts need it). The recipes also name the id the market is keyed by,
  // so the pivot waits for them, or for their failure and then guesses.
  const makesPlane = useFreshData(
    "recipes",
    (signal) => (id ? fetchRecipesForProduct(id, signal) : Promise.resolve(null)),
    [id],
  )
  const makesIndex = makesPlane.data
  const makes = makesIndex ? sortMakes(makesIndex.recipes, id) : []
  const item = !id ? "" : makesIndex ? pivotItemFor(makes, id) : makesPlane.error ? pivotItemFor([], id) : ""

  const [wantGraph, setWantGraph] = useState(false)
  const graphPlane = useFreshData(
    "recipes",
    (signal) => (wantGraph ? fetchRecipeIndex(signal) : Promise.resolve(null)),
    [wantGraph],
  )
  const model = graphPlane.data && id ? itemPageModel(graphPlane.data, id) : null

  // Refresh contract lives in freshness.ts, not here (eco-app#201). `item` is
  // in the deps, so a pivot always belongs to the item currently in the URL —
  // and so does an error.
  const itemPlane = useFreshData(
    "items",
    (signal) => (item ? fetchItemPivot(item, signal) : Promise.resolve(null)),
    [item],
  )
  const fetched: ItemPivot | null = itemPlane.data
  const erroredItem = itemPlane.error ? item : null

  // Gate on the item the state belongs to, so switching items shows a clean
  // loading gap rather than the previous item's data.
  const pivot = fetched && fetched.item === item ? fetched : null
  const error = erroredItem === item
  const pretty = makes[0]?.product.displayName ?? model?.displayName ?? (id ? prettifyEcoName(id) : "")

  // A /recipe?id= redirect arrives with #recipe-<name>. The card renders only
  // once the graph loads, so scroll and move focus to it then.
  const hash = location.hash
  useEffect(() => {
    if (!hash || !makesIndex) return
    const card = document.getElementById(decodeURIComponent(hash.slice(1)))
    if (!card) return
    card.scrollIntoView?.({ block: "start" })
    card.focus({ preventScroll: true })
  }, [hash, makesIndex])

  // Preserve the other params when one control changes; always reset to page 1
  // (except when the page itself changes).
  const update = (patch: Record<string, string>) => {
    const next: Record<string, string> = { id }
    if (q) next.q = q
    if (actor) next.actor = actor
    if (type !== "all") next.type = type
    if (page > 1) next.page = String(page)
    for (const [k, v] of Object.entries(patch)) {
      if (v) next[k] = v
      else delete next[k]
    }
    if (!("page" in patch)) delete next.page
    setParams(next, { replace: false })
  }

  const summary = pivot?.summary
  // Reference "now" for relative time: the world clock if we have it, else the
  // newest event on the page (formatRelative clamps future to "just now").
  // Plain consts — react-compiler memoizes; a manual useMemo here trips its
  // preserve-manual-memoization rule.
  const now = !pivot
    ? 0
    : pivot.worldClockS !== null
      ? pivot.worldClockS
      : pivot.feed.reduce((mx, r) => Math.max(mx, r.time), 0)

  // Distinct actors (crafters + both trade sides) for the actor dropdown.
  const actorSet = new Set<string>()
  for (const r of pivot?.feed ?? []) {
    for (const name of [r.actor, r.seller, r.buyer]) {
      if (name) actorSet.add(name)
    }
  }
  const actors = [...actorSet].sort((a, b) => a.localeCompare(b))

  const needle = q.trim().toLowerCase()
  const filtered = (pivot?.feed ?? []).filter((r) => {
    if (type !== "all" && r.kind !== type) return false
    if (actor && r.actor !== actor && r.seller !== actor && r.buyer !== actor) return false
    if (needle) {
      const hay = [r.actor, r.seller, r.buyer, r.station, r.currency, pretty]
        .join(" ")
        .toLowerCase()
      if (!hay.includes(needle)) return false
    }
    return true
  })

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))
  const clampedPage = Math.min(page, totalPages)
  const pageRows = filtered.slice((clampedPage - 1) * PAGE_SIZE, clampedPage * PAGE_SIZE)

  // Retired ?item=<full id> links keep working, filters included.
  if (legacyItem && !id) {
    const next = new URLSearchParams(params)
    next.delete("item")
    next.set("id", itemPageId(legacyItem))
    return <Navigate to={`/item?${next.toString()}${location.hash}`} replace />
  }

  return (
    <Layout fetchedAtISO={pivot?.fetchedAtISO}>
      <section className="hero hero-compact">
        <p className="hero-kicker">
          <Link to="/items" className="linklike" data-testid="back-to-items">
            ← Item directory
          </Link>
        </p>
        <h1 className="hero-title">
          {pretty ? (
            <>
              Everything about <span className="accent">{pretty}</span>
            </>
          ) : (
            <>Pick an item</>
          )}
        </h1>
        {pivot && (
          <p className="hero-pill" data-testid="item-pill">
            <span className="pulse-dot" aria-hidden="true" />
            {formatCount(pivot.tradeCount)} trades · {formatCount(pivot.craftQuantity)} made
            {pivot.tradeVolume ? ` · ${formatCount(pivot.tradeVolume)} currency moved` : ""}
          </p>
        )}
        {id && !pivot && error && (
          <p className="hero-pill hero-pill-muted" data-testid="item-error">
            item history unavailable right now
          </p>
        )}
        {item && (
          <p className="hero-kicker" data-testid="item-resolver-link">
            <Link className="linklike" to={`/uses/resolve?item=${encodeURIComponent(item)}`}>
              Make, buy, or find a crafter →
            </Link>
          </p>
        )}
        <FreshnessNote plane="items" loadedAt={itemPlane.loadedAt} />
      </section>

      {!id && (
        <section>
          <p className="empty-note" data-testid="item-missing">
            No item selected. Head to the{" "}
            <Link className="linklike" to="/items">
              item directory
            </Link>{" "}
            and pick one.
          </p>
        </section>
      )}

      {id && (
        <h2 className="section-title" id="market">
          Market
        </h2>
      )}
      {pivot && pivot.tradeCount === 0 && pivot.craftCount === 0 && (
        <section>
          <p className="empty-note" data-testid="item-empty">
            Nobody has traded or crafted {pretty} on this server yet.
          </p>
        </section>
      )}

      {/* Actionable summary — should I craft it, where do I buy it, who buys it. */}
      {pivot && summary && (pivot.tradeCount > 0 || pivot.craftCount > 0) && (
        <section className="summary-cols" data-testid="item-summary">
          <div>
            <h2 className="section-title">Who can make it</h2>
            {summary.crafters.length === 0 ? (
              <p className="empty-note">No recorded crafters.</p>
            ) : (
              <ul className="rank-rows" data-testid="item-crafters">
                {summary.crafters.map((c) => {
                  const max = Math.max(...summary.crafters.map((x) => x.quantity), 1)
                  return (
                    <li key={c.name}>
                      <button className="rank-row" onClick={() => update({ actor: c.name, type: "craft" })}>
                        <span className="rank-name">{c.name}</span>
                        <span className="rank-count">
                          {/* Confirmed floor - older history rolls up server-side
                              without item detail (eco-app#131). */}
                          {formatCount(c.quantity)}+ iteration{c.quantity === 1 ? "" : "s"} ·{" "}
                          {formatCount(c.events)} craft
                          {c.events === 1 ? "" : "s"}
                        </span>
                        <span className="rank-bar" style={{ width: `${(c.quantity / max) * 100}%` }} />
                      </button>
                    </li>
                  )
                })}
              </ul>
            )}
          </div>
          <div>
            <h2 className="section-title">
              Available now{" "}
              <span className="section-sub">
                ({summary.live ? "live shelf" : "history-derived"})
              </span>
            </h2>
            {summary.supply.offers.length === 0 ? (
              <p className="empty-note">No open sell offers.</p>
            ) : (
              <>
                <p className="hero-pill" data-testid="item-supply-total">
                  {formatCount(summary.supply.totalQuantity)}
                  {summary.supply.capped ? "+" : ""} in stock across{" "}
                  {formatCount(summary.supply.storeCount)} store
                  {summary.supply.storeCount === 1 ? "" : "s"}
                </p>
                <ul className="rank-rows" data-testid="item-supply">
                  {summary.supply.offers.map((o, i) => (
                    <li key={`${o.store}-${i}`}>
                      <div className="rank-row">
                        <span className="rank-name"><EcoRichText text={o.store} /></span>
                        <span className="rank-count">
                          <ItemPrice
                            price={o.price}
                            norm={o.norm ?? pivot?.norm}
                            currency={o.currency}
                            showCurrency
                            suffix={o.quantity !== null ? `, ${formatCount(o.quantity)} in stock` : ""}
                          />
                        </span>
                      </div>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </div>
          <div>
            <h2 className="section-title">Who is buying</h2>
            {summary.demand.offers.length === 0 ? (
              <p className="empty-note">No open buy orders.</p>
            ) : (
              <>
                <p className="hero-pill" data-testid="item-demand-total">
                  {formatCount(summary.demand.totalQuantity)}
                  {summary.demand.capped ? "+" : ""} wanted across{" "}
                  {formatCount(summary.demand.storeCount)} buyer
                  {summary.demand.storeCount === 1 ? "" : "s"}
                </p>
                <ul className="rank-rows" data-testid="item-demand">
                  {summary.demand.offers.map((o, i) => (
                    <li key={`${o.store}-${i}`}>
                      <div className="rank-row">
                        <span className="rank-name"><EcoRichText text={o.owner || o.store} /></span>
                        <span className="rank-count">
                          <ItemPrice
                            price={o.price}
                            norm={o.norm ?? pivot?.norm}
                            currency={o.currency}
                            showCurrency
                            suffix={o.quantity !== null ? `, wants ${formatCount(o.quantity)}` : ""}
                          />
                        </span>
                      </div>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </div>
        </section>
      )}

      {id && (
        <section aria-labelledby="how-to-make" data-testid="item-recipes">
          <h2 className="section-title" id="how-to-make">
            How to make it{" "}
            {makes.length > 0 && <span className="section-sub">({makes.length})</span>}
          </h2>
          {!makesIndex && !makesPlane.error && <p className="empty-note">Loading recipes…</p>}
          {!makesIndex && makesPlane.error && (
            <p className="empty-note" data-testid="item-recipes-error">
              Recipes can't load right now. Try again in a minute.
            </p>
          )}
          {makesIndex && makes.length === 0 && (
            <p className="empty-note" data-testid="item-no-recipe">
              No recipe makes {pretty} as its main product.
            </p>
          )}
          {makesIndex && makes.length > 0 && (
            <div className="recipe-cards">
              {makes.map((r, i) => (
                <RecipeCard key={r.name} recipe={r} index={makesIndex} main={i === 0 && makes.length > 1} />
              ))}
            </div>
          )}
        </section>
      )}

      {id && (
        <section aria-labelledby="used-in" data-testid="item-used-in">
          <h2 className="section-title" id="used-in">
            What it's used in{" "}
            {model && model.usedIn.length > 0 && <span className="section-sub">({model.usedIn.length})</span>}
          </h2>
          {!wantGraph && (
            <button className="button" onClick={() => setWantGraph(true)} data-testid="item-load-uses">
              Show what uses {pretty}
            </button>
          )}
          {wantGraph && !model && !graphPlane.error && <p className="empty-note">Loading every recipe…</p>}
          {wantGraph && graphPlane.error && (
            <p className="empty-note" data-testid="item-uses-error">
              Recipes can't load right now. Try again in a minute.
            </p>
          )}
          {model && model.usedIn.length === 0 && <p className="empty-note">No recipe uses {pretty}.</p>}
          {model && model.usedIn.length > 0 && (
            <>
              <RecipeLinks recipes={model.usedIn.slice(0, USED_IN_SHOWN)} testId="item-used-in-list" />
              {model.usedIn.length > USED_IN_SHOWN && (
                <p>
                  <Link className="linklike" to={`/recipes?ingredient=${encodeURIComponent(item)}`}>
                    See all {model.usedIn.length} in the recipe directory →
                  </Link>
                </p>
              )}
            </>
          )}
          {model && model.byproductOf.length > 0 && (
            <>
              <h3 className="section-title-sm">Also comes out of</h3>
              <RecipeLinks recipes={model.byproductOf} testId="item-byproduct-of" />
            </>
          )}
        </section>
      )}

      {/* Merged reverse-chrono feed with search / actor / type filters + paging. */}
      {pivot && pivot.feed.length > 0 && (
        <section>
          <div className="filter-row" data-testid="item-filters">
            <input
              className="filter-input"
              type="search"
              placeholder="Search the feed by name, station, currency… (deep-linkable as ?q=)"
              value={q}
              onChange={(e) => update({ q: e.target.value })}
              data-testid="item-filter"
            />
            <select
              className="filter-select"
              value={actor}
              onChange={(e) => update({ actor: e.target.value })}
              data-testid="item-actor-filter"
              aria-label="Filter by actor"
            >
              <option value="">Anyone</option>
              {actors.map((a) => (
                <option key={a} value={a}>
                  {a}
                </option>
              ))}
            </select>
            <select
              className="filter-select"
              value={type}
              onChange={(e) => update({ type: e.target.value })}
              data-testid="item-type-filter"
              aria-label="Filter by event type"
            >
              <option value="all">All events</option>
              <option value="craft">Crafts</option>
              <option value="trade">Trades</option>
            </select>
            {(q || actor || type !== "all") && (
              <button
                className="button"
                onClick={() => update({ q: "", actor: "", type: "" })}
                data-testid="item-clear"
              >
                Clear
              </button>
            )}
          </div>

          <h2 className="section-title">
            Timeline{" "}
            <span className="section-sub">
              ({formatCount(filtered.length)} event{filtered.length === 1 ? "" : "s"}
              {pivot.feedTruncated ? "+, compressed" : ", compressed"})
            </span>
          </h2>

          {pageRows.length === 0 ? (
            <p className="empty-note" data-testid="item-no-match">
              No events match these filters.
            </p>
          ) : (
            <ul className="pivot-lines" data-testid="item-feed">
              {pageRows.map((row, i) => (
                <FeedLine key={`${row.kind}-${row.time}-${i}`} row={row} item={pretty} now={now} norm={pivot?.norm} />
              ))}
            </ul>
          )}

          {totalPages > 1 && (
            <div className="pager" data-testid="item-pager">
              <button
                className="button"
                disabled={clampedPage <= 1}
                onClick={() => update({ page: String(clampedPage - 1) })}
                data-testid="item-prev"
              >
                ← Newer
              </button>
              <span className="pager-status">
                Page {clampedPage} of {totalPages}
              </span>
              <button
                className="button"
                disabled={clampedPage >= totalPages}
                onClick={() => update({ page: String(clampedPage + 1) })}
                data-testid="item-next"
              >
                Older →
              </button>
            </div>
          )}
        </section>
      )}

      {pivot && pivot.warnings.length > 0 && (
        <section>
          <ul className="warn-list" data-testid="item-warnings">
            {pivot.warnings.map((w) => (
              <li key={w}>⚠ {w}</li>
            ))}
          </ul>
        </section>
      )}
    </Layout>
  )
}
