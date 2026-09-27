import { useEffect, useMemo, useState } from "react"
import { Link, useSearchParams } from "react-router-dom"
import EcoRichText from "../components/EcoRichText"
import ItemLink from "../components/ItemLink"
import FreshnessNote from "../components/FreshnessNote"
import Layout from "../components/Layout"
import ItemPrice from "../components/ItemPrice"
import PriceHistoryPanel from "../components/PriceHistoryPanel"
import { fetchJsonOrNull } from "../lib/api"
import {
  fetchLogistics,
  type GapReason,
  type PricedBoardRow,
  type ShelfOffer,
} from "../lib/logisticsApi"
import { fetchMarket, type ItemMarket, type MarketTrend } from "../lib/marketApi"
import { formatCount, formatDecimal, formatMoney, prettifyEcoName } from "../lib/format"
import type { PriceNorm } from "../lib/priceNorm"
import { useFreshData } from "../lib/useFreshData"
import {
  fetchItemPriceHistory,
  type ItemPriceHistory,
} from "../lib/priceHistoryApi"

const PICK_ROWS = 200
const TARGET_MARKUP = 1.25

const DEMAND_REASON: Record<GapReason, string> = {
  no_supply: "out of stock",
  thin_supply: "low stock",
  overpriced: "overpriced",
}

type RecipeCostLine = {
  norm?: PriceNorm | null
  item: string
  displayName: string
  quantity: number
  isTag: boolean
  unitCost: number | null
  source: string
  subtotal: number | null
}

type RecipeCost = {
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
  ingredients: RecipeCostLine[]
}

type CostRecipe = {
  name: string
  displayName: string
  product: { item: string; displayName: string; quantity: number }
  cost?: RecipeCost
}

type CostRecipeIndex = {
  fetchedAtISO: string
  warnings: string[]
  recipes: CostRecipe[]
}

type MarketOption = {
  item: string
  pretty: string
  currency: string
  score: number
  detail: string
  market?: ItemMarket
}

type TrendMeta = { glyph: string; label: string; color: string }

const TREND: Record<MarketTrend, TrendMeta> = {
  rising: { glyph: "▲", label: "rising", color: "var(--leaf)" },
  falling: { glyph: "▼", label: "falling", color: "var(--meteor)" },
  flat: { glyph: "▬", label: "flat", color: "var(--ink-faint)" },
  insufficient: { glyph: "·", label: "too few sales", color: "var(--ink-faint)" },
}

function signedPrice(n: number, currency: string): string {
  const sign = n > 0 ? "+" : ""
  return `${sign}${formatMoney(n)} ${currency}`
}

function TrendTag({ trend, delta }: { trend: MarketTrend; delta: number | null }) {
  const t = TREND[trend]
  const pct =
    delta !== null && trend !== "flat" && trend !== "insufficient"
      ? ` ${delta > 0 ? "+" : ""}${Math.round(delta)}%`
      : ""
  return (
    <span className="trend-tag" style={{ color: t.color }} data-testid="price-trend">
      <span aria-hidden="true">{t.glyph}</span> {t.label}
      {pct}
    </span>
  )
}

function SourceTag({ source }: { source: string }) {
  const live = source === "live"
  return (
    <span className="source-tag" style={{ color: live ? "var(--leaf)" : "var(--ink-faint)" }}>
      {live ? "● listed now" : "○ from past sales"}
    </span>
  )
}

function offerRows(row: PricedBoardRow | null, side: "sell" | "buy"): ShelfOffer[] {
  if (!row) return []
  const sorted = [...row.offers].sort((a, b) => (side === "sell" ? a.price - b.price : b.price - a.price))
  return sorted
}

function quantile(sorted: number[], q: number): number {
  if (sorted.length === 0) return 0
  if (sorted.length === 1) return sorted[0]
  const pos = (sorted.length - 1) * q
  const base = Math.floor(pos)
  const rest = pos - base
  const next = sorted[Math.min(base + 1, sorted.length - 1)]
  return sorted[base] + (next - sorted[base]) * rest
}

function bandFor(market: ItemMarket) {
  const medians = [...market.buckets.map((b) => b.median)].sort((a, b) => a - b)
  const q1 = quantile(medians, 0.25)
  const q3 = quantile(medians, 0.75)
  const iqr = q3 - q1
  return {
    q1,
    q3,
    iqr,
    low: market.medianPrice - iqr,
    high: market.medianPrice + iqr,
  }
}

function bandFit(price: number, low: number, high: number): string {
  if (price < low) return "below the range"
  if (price > high) return "above the range"
  return "inside the range"
}

function MarketSummary({ row }: { row: ItemMarket }) {
  return (
    <>
      <ItemPrice
        price={row.medianPrice}
        norm={row.norm}
        currency={row.currency}
        showCurrency
        prefix="Middle price "
        layout="inline"
      />
      , {formatCount(row.totalTrades)} trades, {formatCount(row.totalVolume)} units traded
    </>
  )
}

// The flagship "How should I price X?" page (eco-app#104). It reads the live
// market band, the current shelf comparison, and the recipe cost roll-up when
// available. Each fetch is
// independent so a missing plane degrades in place instead of blanking the page.
export default function UsesPrice() {
  const [params, setParams] = useSearchParams()
  const item = params.get("item") ?? ""
  const requestedCurrency = params.get("currency") ?? ""
  const opportunitySource = params.get("source")
  const demandReason = params.get("demandReason") as GapReason | null
  const demandQtyRaw = Number(params.get("demandQty"))
  const marginRaw = Number(params.get("margin"))
  const confidence = params.get("confidence") === "incomplete" ? "incomplete" : "complete"
  const opportunityContext =
    opportunitySource === "jobs" &&
    demandReason !== null &&
    demandReason in DEMAND_REASON &&
    Number.isFinite(demandQtyRaw) &&
    demandQtyRaw > 0
      ? {
          demandReason,
          demandQty: demandQtyRaw,
          margin: params.has("margin") && Number.isFinite(marginRaw) ? marginRaw : null,
          confidence,
        }
      : null
  // Refresh contract lives in freshness.ts, not here (eco-app#201). This is
  // the page-level market spine — the only plane here that was locked to
  // initial load. The item and currency detail fetches below already re-run on
  // their own parameters.
  const spinePlane = useFreshData("shopCheck", async (signal) => ({
    market: await fetchMarket(signal).catch(() => null),
    logistics: await fetchLogistics(signal).catch(() => null),
  }))
  const market = spinePlane.data?.market ?? null
  const logistics = spinePlane.data?.logistics ?? null
  const [recipes, setRecipes] = useState<CostRecipeIndex | null>(null)
  const [priceHistory, setPriceHistory] = useState<ItemPriceHistory | null>(null)
  const loaded = !spinePlane.loading
  const [detailLoadedFor, setDetailLoadedFor] = useState("")
  const [recipesFor, setRecipesFor] = useState("")
  const [priceHistoryFor, setPriceHistoryFor] = useState("")

  const marketRowsForItem = useMemo(
    () => market?.markets.filter((row) => row.item === item) ?? [],
    [market, item],
  )
  const fallbackShelfCurrency = useMemo(() => {
    const row =
      logistics?.cheapest.find((candidate) => candidate.item === item) ??
      logistics?.resale.find((candidate) => candidate.item === item)
    return row?.currency ?? ""
  }, [logistics, item])
  const requestedMarketRow = marketRowsForItem.find(
    (row) => row.currency.toLowerCase() === requestedCurrency.toLowerCase(),
  )
  const selectedCurrency =
    requestedMarketRow?.currency ||
    marketRowsForItem[0]?.currency ||
    requestedCurrency ||
    fallbackShelfCurrency
  const selectedMarketRow = requestedMarketRow ?? marketRowsForItem[0] ?? null

  useEffect(() => {
    if (!item) {
      return
    }
    const controller = new AbortController()
    const s = controller.signal
    const recipeUrl = `/preview/recipes.json?cost=1&product=${encodeURIComponent(item)}`
    fetchJsonOrNull<CostRecipeIndex>(recipeUrl, s)
      .then(
        (result) => {
          setRecipes(result)
          setRecipesFor(item)
        },
        () => {
          setRecipes(null)
          setRecipesFor(item)
        },
      )
      .finally(() => {
        if (!s.aborted) setDetailLoadedFor(item)
      })
    return () => controller.abort()
  }, [item])

  useEffect(() => {
    if (!item || !selectedCurrency) return
    const controller = new AbortController()
    const key = `${item}\u0000${selectedCurrency}`
    fetchItemPriceHistory(item, selectedCurrency, controller.signal).then(
      (result) => {
        if (!controller.signal.aborted) {
          setPriceHistory(result)
          setPriceHistoryFor(key)
        }
      },
      () => {
        if (!controller.signal.aborted) {
          setPriceHistory(null)
          setPriceHistoryFor(key)
        }
      },
    )
    return () => controller.abort()
  }, [item, selectedCurrency])

  const pickItem = (nextItem: string, nextCurrency = "") => {
    setParams(
      nextItem ? { item: nextItem, ...(nextCurrency ? { currency: nextCurrency } : {}) } : {},
      { replace: false },
    )
  }

  const options = useMemo(() => {
    const byItem = new Map<string, MarketOption>()
    if (market) {
      for (const row of market.markets) {
        const detail = `${formatCount(row.totalTrades)} trades`
        const score = row.totalTrades * 1000 + row.totalVolume
        const previous = byItem.get(row.item)
        if (!previous || score > previous.score) {
          byItem.set(row.item, {
            item: row.item,
            pretty: row.itemPretty,
            currency: row.currency,
            score,
            detail,
            market: row,
          })
        }
      }
    }
    if (logistics) {
      const absorb = (row: PricedBoardRow) => {
        const prev = byItem.get(row.item)
        const offers = row.offers.length
        const score = (prev?.score ?? 0) + offers * 10
        const detail = prev?.detail ?? `${formatCount(offers)} shop listings`
        byItem.set(row.item, {
          item: row.item,
          pretty: row.itemPretty,
          currency: row.currency,
          score,
          detail,
          market: prev?.market,
        })
      }
      logistics.cheapest.forEach(absorb)
      logistics.resale.forEach(absorb)
    }
    return [...byItem.values()].sort((a, b) => b.score - a.score || a.pretty.localeCompare(b.pretty))
  }, [market, logistics])

  const visibleOptions = useMemo(() => options.slice(0, PICK_ROWS), [options])

  const marketRow = selectedMarketRow
  const cheapest = useMemo(() => logistics?.cheapest.find((row) => row.item === item) ?? null, [
    logistics,
    item,
  ])
  const resale = useMemo(() => logistics?.resale.find((row) => row.item === item) ?? null, [
    logistics,
    item,
  ])

  const selectedOption = options.find((row) => row.item === item) ?? null
  const pretty = item ? marketRow?.itemPretty ?? selectedOption?.pretty ?? prettifyEcoName(item) : ""
  const currency = selectedCurrency || cheapest?.currency || resale?.currency || ""
  const moneyUnit = currency || "currency"
  const band = marketRow ? bandFor(marketRow) : null

  const detailReady = !item || detailLoadedFor === item
  const currentRecipes = recipesFor === item ? recipes : null
  const currentPriceHistory =
    priceHistoryFor === `${item}\u0000${selectedCurrency}` ? priceHistory : null
  const priceHistoryReady =
    !item || !selectedCurrency || priceHistoryFor === `${item}\u0000${selectedCurrency}`

  const recipeRows = useMemo(() => {
    if (!currentRecipes) return []
    return [...currentRecipes.recipes]
      .filter((r) => r.product.item === item && r.cost)
      .sort((a, b) => {
        const ac = a.cost!
        const bc = b.cost!
        const aRank = ac.complete ? 0 : 1
        const bRank = bc.complete ? 0 : 1
        if (aRank !== bRank) return aRank - bRank
        const ap = ac.perUnitCost ?? Number.POSITIVE_INFINITY
        const bp = bc.perUnitCost ?? Number.POSITIVE_INFINITY
        if (ap !== bp) return ap - bp
        return a.displayName.localeCompare(b.displayName)
      })
  }, [currentRecipes, item])

  const bestRecipe = recipeRows[0] ?? null
  const craftedPrice = bestRecipe?.cost?.perUnitCost ?? null
  const suggestedAsk =
    craftedPrice !== null ? craftedPrice * TARGET_MARKUP : marketRow?.medianPrice ?? null
  const priceVsMedian =
    suggestedAsk !== null && marketRow ? suggestedAsk - marketRow.medianPrice : null
  const priceVsCraft =
    suggestedAsk !== null && craftedPrice !== null ? suggestedAsk - craftedPrice : null
  const bandFitLabel =
    suggestedAsk !== null && band ? bandFit(suggestedAsk, band.low, band.high) : "no range yet"
  const bandWidth = band ? band.iqr : null
  const currentCount = marketRow ? marketRow.totalTrades : (cheapest?.offers.length ?? 0) + (resale?.offers.length ?? 0)

  return (
    <Layout fetchedAtISO={market?.fetchedAtISO ?? logistics?.fetchedAtISO}>
      <section className="hero hero-compact">
        <p className="hero-kicker">
          <Link to="/uses" className="linklike" data-testid="back-to-uses">
            ← Use cases
          </Link>
        </p>
        <h1 className="hero-title">
          How should I price{" "}
          {pretty ? (
            <ItemLink className="accent linklike" item={item}>
              {pretty}
            </ItemLink>
          ) : (
            <span className="accent">X</span>
          )}?
        </h1>
        {item && marketRow && (
          <p className="hero-pill" data-testid="price-pill">
            <span className="pulse-dot" aria-hidden="true" />
            <MarketSummary row={marketRow} />
          </p>
        )}
        {loaded && !market && !logistics && (
          <p className="hero-pill hero-pill-muted" data-testid="price-error">
            market data unavailable right now
          </p>
        )}
        <p className="hero-tagline">
          Pick an item, see what shops charge, and use the craft cost to find your profit.
        </p>
        <FreshnessNote
          plane="shopCheck"
          loadedAt={spinePlane.loadedAt}
          refreshing={spinePlane.refreshing}
          refreshError={spinePlane.refreshError}
          onRefresh={spinePlane.refresh}
        />
      </section>

      {item && opportunityContext && (
        <section className="opportunity-context" data-testid="opportunity-context">
          <h2 className="section-title">Why the jobs page sent you here</h2>
          <p className="hero-pill">
            <span className="pulse-dot" aria-hidden="true" />
            {formatCount(opportunityContext.demandQty)} wanted by buyers ·{" "}
            {DEMAND_REASON[opportunityContext.demandReason]} ·{" "}
            {opportunityContext.margin !== null
              ? `estimated profit ${formatMoney(opportunityContext.margin)} each`
              : "estimated profit unknown"}
          </p>
          <p className="empty-note" data-testid="opportunity-confidence">
            {opportunityContext.confidence === "complete"
              ? "Every ingredient had a price, so the profit estimate is complete."
              : "Rough guess: some ingredients have no price, so buyers want it but the profit is unknown."}{" "}
            This is a tip to talk over with other players, not an order to craft it.
          </p>
        </section>
      )}

      {!loaded && (
        <p className="empty-note" data-testid="price-loading">
          Loading prices and shop listings…
        </p>
      )}

      {loaded && options.length > 0 && !item && (
        <section>
          <div className="filter-row">
            <button className="button" onClick={() => pickItem("")} disabled>
              Select an item below
            </button>
          </div>
          <ul className="rank-rows" data-testid="price-picker">
            {visibleOptions.map((o) => (
              <li key={o.item}>
                <div className="rank-row" data-testid="pick-item">
                  <ItemLink className="rank-name linklike" item={o.item}>
                    {o.pretty}
                  </ItemLink>
                  <span className="rank-count">
                    {o.market ? (
                      <ItemPrice
                        price={o.market.medianPrice}
                        norm={o.market.norm}
                        currency={o.market.currency}
                        showCurrency
                        prefix="middle price "
                        suffix={`, ${formatCount(o.market.totalTrades)} trades`}
                      />
                    ) : (
                      o.detail
                    )}
                  </span>
                  <button
                    className="linklike"
                    onClick={() => pickItem(o.item, o.currency)}
                    aria-label={`Price ${o.pretty}`}
                  >
                    Price
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}

      {loaded && options.length === 0 && !item && (
        <section>
          <p className="empty-note" data-testid="price-no-options">
            No items have prices yet. Once sales data loads, you can pick one here.
          </p>
        </section>
      )}

      {item && (
        <>
          {!selectedCurrency ? (
            <section>
              <p className="empty-note" data-testid="price-history-no-currency">
                We don't know which currency this item sells for yet, so its price history can't be
                shown.
              </p>
            </section>
          ) : !priceHistoryReady ? (
            <section>
              <p className="empty-note" data-testid="price-history-loading">
                Loading this cycle's prices and specialty unlocks…
              </p>
            </section>
          ) : currentPriceHistory ? (
            <PriceHistoryPanel history={currentPriceHistory} />
          ) : (
            <section>
              <p className="empty-note" data-testid="price-history-unavailable">
                This cycle's price history is not available right now. The prices and costs below
                still work.
              </p>
            </section>
          )}

          <section className="atlas-columns">
            <div data-testid="price-market-band">
              <h2 className="section-title">
                Typical price range{" "}
                <span className="section-sub">(the middle price, give or take its usual daily swing)</span>
              </h2>
              {!marketRow ? (
                <p className="empty-note" data-testid="price-market-empty">
                  Past sales for this item are not available right now.
                </p>
              ) : (
                <>
                  <p className="hero-pill" data-testid="price-band-pill">
                    <span className="pulse-dot" aria-hidden="true" />
                    <MarketSummary row={marketRow} />
                    {bandWidth !== null ? `, give or take ${formatMoney(bandWidth)} ${moneyUnit}` : ""}
                  </p>
                  <table tabIndex={0} className="ledger-table" data-testid="price-band-table">
                    <thead>
                      <tr>
                        <th>Day</th>
                        <th className="num">Middle price</th>
                        <th className="num">Low</th>
                        <th className="num">High</th>
                        <th className="num">Units</th>
                        <th className="num">Trades</th>
                      </tr>
                    </thead>
                    <tbody>
                      {marketRow.buckets.slice(-8).map((b) => (
                        <tr key={b.day} data-testid="price-band-row">
                          <td>Day {b.day}</td>
                          <td className="num">
                            <ItemPrice price={b.median} norm={marketRow?.norm} currency={marketRow?.currency} />
                          </td>
                          <td className="num">
                            <ItemPrice price={b.min} norm={marketRow?.norm} currency={marketRow?.currency} />
                          </td>
                          <td className="num">
                            <ItemPrice price={b.max} norm={marketRow?.norm} currency={marketRow?.currency} />
                          </td>
                          <td className="num">{formatCount(b.volume)}</td>
                          <td className="num">{formatCount(b.trades)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </>
              )}
            </div>
            <div>
              <h2 className="section-title">
                What shops charge now{" "}
                <span className="section-sub">(cheapest to buy from, best to sell to, and price trend)</span>
              </h2>
              {!cheapest && !resale ? (
                <p className="empty-note" data-testid="price-comparison-empty">
                  Shop prices are not available right now.
                </p>
              ) : (
                <>
                  {marketRow && (
                    <p className="hero-pill" data-testid="price-trend-pill">
                      <TrendTag trend={marketRow.trend} delta={marketRow.trendDeltaPct} />
                    </p>
                  )}
                  <table tabIndex={0} className="ledger-table" data-testid="price-comparison-table">
                    <thead>
                      <tr>
                        <th>Shop is</th>
                        <th>Store</th>
                        <th>Owner</th>
                        <th className="num">Price</th>
                        <th className="num">Qty</th>
                        <th>Source</th>
                      </tr>
                    </thead>
                    <tbody>
                      {offerRows(cheapest, "sell").slice(0, 5).map((o, i) => (
                        <tr key={`sell-${o.storeKey}-${i}`} data-testid="price-sell-row">
                          <td>Selling</td>
                          <td><EcoRichText text={o.store} /></td>
                          <td>{o.owner ? <EcoRichText text={o.owner} /> : "—"}</td>
                          <td className="num">
                            <ItemPrice price={o.price} norm={o.norm ?? cheapest?.norm} currency={o.currency || currency} showCurrency />
                          </td>
                          <td className="num">{formatCount(o.quantity)}</td>
                          <td>
                            <SourceTag source={o.source} />
                          </td>
                        </tr>
                      ))}
                      {offerRows(resale, "buy").slice(0, 5).map((o, i) => (
                        <tr key={`buy-${o.storeKey}-${i}`} data-testid="price-buy-row">
                          <td>Buying</td>
                          <td><EcoRichText text={o.store} /></td>
                          <td>{o.owner ? <EcoRichText text={o.owner} /> : "—"}</td>
                          <td className="num">
                            <ItemPrice price={o.price} norm={o.norm ?? resale?.norm} currency={o.currency || currency} showCurrency />
                          </td>
                          <td className="num">{formatCount(o.quantity)}</td>
                          <td>
                            <SourceTag source={o.source} />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <p className="section-sub" data-testid="price-comparison-summary">
                    {marketRow
                      ? <><MarketSummary row={marketRow} />.</>
                      : `${formatCount(currentCount)} shop listings for this item.`}
                  </p>
                </>
              )}
            </div>
          </section>

          <section className="atlas-columns">
            <div>
              <h2 className="section-title">
                Cost breakdown{" "}
                <span className="section-sub">(every ingredient down the chain, plus labor)</span>
              </h2>
              {!detailReady ? (
                <p className="empty-note" data-testid="price-cost-loading">
                  Loading craft costs…
                </p>
              ) : !currentRecipes ? (
                <p className="empty-note" data-testid="price-cost-pending">
                  Craft costs are not available yet. They will show here once recipe data loads.
                </p>
              ) : !bestRecipe ? (
                <p className="empty-note" data-testid="price-no-recipe">
                  We have no recipe for this item yet.
                </p>
              ) : (
                <>
                  <p className="hero-pill" data-testid="price-cost-pill">
                    <span className="pulse-dot" aria-hidden="true" />
                    <ItemLink className="linklike" item={bestRecipe.product.item}>
                      {bestRecipe.displayName}
                    </ItemLink>{" "}
                    ·{" "}
                    {bestRecipe.cost?.perUnitCost !== null && bestRecipe.cost?.perUnitCost !== undefined
                      ? <ItemPrice price={bestRecipe.cost.perUnitCost} norm={bestRecipe.cost.norm} currency={moneyUnit} showCurrency suffix="/unit" />
                      : "no price"}
                    {bestRecipe.cost?.complete ? "" : " · some prices missing"}
                  </p>
                  <table tabIndex={0} className="ledger-table" data-testid="price-cost-table">
                    <thead>
                      <tr>
                        <th>Ingredient</th>
                        <th className="num">Qty</th>
                        <th className="num">Price each</th>
                        <th className="num">Total</th>
                        <th>Price from</th>
                      </tr>
                    </thead>
                    <tbody>
                      {bestRecipe.cost?.ingredients.map((line) => (
                        <tr key={`${line.item}-${line.displayName}`} data-testid="price-cost-row">
                          <td>
                            <ItemLink className="linklike" item={line.isTag ? null : line.item}>
                              {line.displayName}
                            </ItemLink>
                            {line.isTag ? <span className="section-sub"> (any of this kind)</span> : null}
                          </td>
                          <td className="num">{formatCount(line.quantity)}</td>
                          <td className="num">
                            <ItemPrice price={line.unitCost} norm={line.norm} currency={moneyUnit} showCurrency />
                          </td>
                          <td className="num">
                            {line.subtotal !== null ? `${formatMoney(line.subtotal)} ${moneyUnit}` : "—"}
                          </td>
                          <td>{line.source}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <ul className="rank-rows" data-testid="price-cost-summary">
                    <li>
                      <div className="rank-row">
                        <span className="rank-name">Ingredient cost</span>
                        <span className="rank-count">
                          {bestRecipe.cost?.ingredientCost != null
                            ? `${formatMoney(bestRecipe.cost.ingredientCost)} ${moneyUnit}`
                            : "—"}
                        </span>
                      </div>
                    </li>
                    <li>
                      <div className="rank-row">
                        <span className="rank-name">Labor</span>
                        <span className="rank-count">
                          {bestRecipe.cost ? `${formatCount(bestRecipe.cost.laborCalories)} cal` : "—"}
                        </span>
                      </div>
                    </li>
                    <li>
                      <div className="rank-row">
                        <span className="rank-name">Time</span>
                        <span className="rank-count">
                          {bestRecipe.cost ? `${formatDecimal(bestRecipe.cost.craftMinutes)} min` : "—"}
                        </span>
                      </div>
                    </li>
                    {bestRecipe.cost && !bestRecipe.cost.complete && (
                      <li>
                        <div className="rank-row">
                          <span className="rank-name">Ingredients with no price</span>
                          <span className="rank-count">{bestRecipe.cost.unpricedInputs.join(", ")}</span>
                        </div>
                      </li>
                    )}
                  </ul>
                </>
              )}
            </div>
            <div>
              <h2 className="section-title">
                Suggested price and profit{" "}
                <span className="section-sub">(craft cost plus a markup when we know it, otherwise the middle price)</span>
              </h2>
              {suggestedAsk === null || !marketRow ? (
                <p className="empty-note" data-testid="price-suggestion-empty">
                  No middle price yet, so there is no fair price to suggest.
                </p>
              ) : (
                <>
                  <ul className="rank-rows" data-testid="price-suggestion-list">
                    <li>
                      <div className="rank-row">
                        <span className="rank-name">Price to ask</span>
                        <span className="rank-count">
                          <ItemPrice
                            price={suggestedAsk}
                            norm={marketRow?.norm ?? bestRecipe?.cost?.norm}
                            currency={moneyUnit}
                            showCurrency
                          />
                        </span>
                      </div>
                    </li>
                    <li>
                      <div className="rank-row">
                        <span className="rank-name">Compared to craft cost</span>
                        <span className="rank-count">
                          {priceVsCraft !== null
                            ? signedPrice(priceVsCraft, moneyUnit)
                            : <ItemPrice price={marketRow.medianPrice} norm={marketRow.norm} currency={moneyUnit} showCurrency prefix="middle price " layout="inline" />}
                        </span>
                      </div>
                    </li>
                    <li>
                      <div className="rank-row">
                        <span className="rank-name">Compared to the middle price</span>
                        <span className="rank-count">
                          {priceVsMedian !== null ? signedPrice(priceVsMedian, moneyUnit) : "—"}
                        </span>
                      </div>
                    </li>
                    <li>
                      <div className="rank-row">
                        <span className="rank-name">In the typical range?</span>
                        <span className="rank-count">{bandFitLabel}</span>
                      </div>
                    </li>
                  </ul>
                  <p className="section-sub" data-testid="price-suggestion-note">
                    {craftedPrice !== null
                      ? `Craft cost plus ${Math.round((TARGET_MARKUP - 1) * 100)}%.`
                      : "No craft cost yet, so this is the middle price."}
                    {band ? (
                      <>
                        {" "}The typical range is{" "}
                        <ItemPrice price={band.low} norm={marketRow?.norm} currency={moneyUnit} layout="inline" /> to{" "}
                        <ItemPrice price={band.high} norm={marketRow?.norm} currency={moneyUnit} showCurrency layout="inline" />.
                      </>
                    ) : (
                      ""
                    )}
                  </p>
                </>
              )}
            </div>
          </section>
        </>
      )}
    </Layout>
  )
}
