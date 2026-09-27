import { useMemo, useState } from "react"
import { Link, useSearchParams } from "react-router-dom"
import EcoRichText from "../components/EcoRichText"
import ItemLink from "../components/ItemLink"
import FreshnessNote from "../components/FreshnessNote"
import ItemPrice from "../components/ItemPrice"
import Layout from "../components/Layout"
import { fetchStores, type StoreProfile } from "../lib/storesApi"
import { fetchMarket, type ItemMarket } from "../lib/marketApi"
import { formatCount, stripEcoMarkup } from "../lib/format"
import type { PriceNorm } from "../lib/priceNorm"
import { useFreshData } from "../lib/useFreshData"

const PICK_ROWS = 200
// A shelf priced within ±this of the market median reads as "at market"; beyond
// it, the item is flagged notably over- or under-priced.
const NOTABLE_PCT = 15

type Verdict = "over" | "under" | "at" | "unknown"

// Price verdict, encoded glyph + label + colour (never colour alone, per the
// dataviz non-negotiables): over market is meteor amber (you may be scaring
// buyers off), under market is leaf green (leaving money on the table), at
// market is muted ink, and no-median stays faint.
const VERDICT: Record<Verdict, { glyph: string; label: string; color: string }> = {
  over: { glyph: "▲", label: "over market", color: "var(--meteor)" },
  under: { glyph: "▼", label: "under market", color: "var(--leaf)" },
  at: { glyph: "▬", label: "at market", color: "var(--ink-faint)" },
  unknown: { glyph: "·", label: "no market price yet", color: "var(--ink-faint)" },
}

interface CheckRow {
  item: string
  pretty: string
  avgUnitPrice: number | null
  avgNorm: PriceNorm | null | undefined
  median: number | null
  medianNorm: PriceNorm | null | undefined
  currency: string | null
  deltaPct: number | null
  verdict: Verdict
}

// "Is my shop priced right?" (eco-app#99). The one demand-side page that joins
// two planes: the store directory (/preview/stores.json, each item's own
// avgUnitPrice) against the market medians (/preview/market.json). Either can
// 404 independently, so each degrades to a clear note rather than blanking the
// page.
export default function UsesShopCheck() {
  // Refresh contract lives in freshness.ts, not here (eco-app#201). Both
  // planes refresh together so the shop check is internally consistent.
  const shopPlane = useFreshData("shopCheck", async (signal) => ({
    stores: await fetchStores(signal).catch(() => null),
    market: await fetchMarket(signal).catch(() => null),
  }))
  const stores = shopPlane.data?.stores ?? null
  const market = shopPlane.data?.market ?? null
  const loaded = !shopPlane.loading
  const [params, setParams] = useSearchParams()
  const storeKey = params.get("store") ?? ""
  const [filter, setFilter] = useState("")

  const pickStore = (key: string) => {
    setParams(key ? { store: key } : {}, { replace: false })
  }

  // item id -> market row (first, i.e. most-traded, currency wins).
  const medians = useMemo(() => {
    const m = new Map<string, ItemMarket>()
    if (market) {
      for (const row of market.markets) {
        if (!m.has(row.item)) m.set(row.item, row)
      }
    }
    return m
  }, [market])

  const options = useMemo(
    () => (stores ? [...stores.stores].sort((a, b) => b.totalVolume - a.totalVolume) : []),
    [stores],
  )

  const needle = filter.trim().toLowerCase()
  const visibleOptions = useMemo(
    () =>
      (needle
        ? options.filter(
            (s) =>
              stripEcoMarkup(s.label).toLowerCase().includes(needle) ||
              stripEcoMarkup(s.owner).toLowerCase().includes(needle),
          )
        : options
      ).slice(0, PICK_ROWS),
    [options, needle],
  )

  const store: StoreProfile | null = useMemo(
    () => stores?.stores.find((s) => s.storeKey === storeKey) ?? null,
    [stores, storeKey],
  )

  const rows: CheckRow[] = useMemo(() => {
    if (!store) return []
    return store.topItems.map((it) => {
      const marketRow = medians.get(it.item) ?? null
      const median = marketRow?.medianPrice ?? null
      const avg = it.avgUnitPrice
      let deltaPct: number | null = null
      let verdict: Verdict = "unknown"
      if (avg != null && median != null && median > 0) {
        deltaPct = ((avg - median) / median) * 100
        verdict = deltaPct > NOTABLE_PCT ? "over" : deltaPct < -NOTABLE_PCT ? "under" : "at"
      }
      return {
        item: it.item,
        pretty: it.pretty,
        avgUnitPrice: avg,
        avgNorm: it.norm,
        median,
        medianNorm: marketRow?.norm,
        currency: marketRow?.currency ?? null,
        deltaPct,
        verdict,
      }
    })
  }, [store, medians])

  const flagged = useMemo(
    () => rows.filter((r) => r.verdict === "over" || r.verdict === "under").length,
    [rows],
  )

  return (
    <Layout fetchedAtISO={stores?.fetchedAtISO ?? market?.fetchedAtISO}>
      <section className="hero hero-compact">
        <p className="hero-kicker">
          <Link to="/uses" className="linklike" data-testid="back-to-uses">
            ← Use cases
          </Link>
        </p>
        <h1 className="hero-title">
          {store ? (
            <>
              Is <span className="accent"><EcoRichText text={store.label} /></span> priced right?
            </>
          ) : (
            <>
              Is my shop <span className="accent">priced right</span>?
            </>
          )}
        </h1>
        {store && (
          <p className="hero-pill" data-testid="shop-pill">
            <span className="pulse-dot" aria-hidden="true" />
            {formatCount(rows.length)} item{rows.length === 1 ? "" : "s"}, {formatCount(flagged)}{" "}
            priced far from the market
          </p>
        )}
        {loaded && !stores && (
          <p className="hero-pill hero-pill-muted" data-testid="shop-error">
            store list isn't available right now (check back once the game server sends store data)
          </p>
        )}
        <FreshnessNote
          plane="shopCheck"
          loadedAt={shopPlane.loadedAt}
          refreshing={shopPlane.refreshing}
          refreshError={shopPlane.refreshError}
          onRefresh={shopPlane.refresh}
        />
      </section>

      {!loaded && (
        <p className="empty-note" data-testid="shop-loading">
          Loading shops…
        </p>
      )}

      {store && !market && (
        <section>
          <p className="empty-note" data-testid="shop-no-market">
            Market prices aren't available right now, so this shows <EcoRichText text={store.label} />'s own prices
            with nothing to compare them to. Check back once market prices come in.
          </p>
        </section>
      )}

      {stores && (
        <section>
          <div className="filter-row">
            <input
              className="filter-input"
              type="search"
              placeholder="Pick a store to check…"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              data-testid="shop-filter"
            />
            {storeKey && (
              <button className="button" onClick={() => pickStore("")}>
                Clear
              </button>
            )}
          </div>
          {!storeKey && (
            <ul className="rank-rows" data-testid="shop-picker">
              {visibleOptions.length === 0 ? (
                <li>
                  <p className="empty-note">No stores found yet.</p>
                </li>
              ) : (
                visibleOptions.map((s) => (
                  <li key={s.storeKey}>
                    <button
                      className="rank-row"
                      onClick={() => pickStore(s.storeKey)}
                      data-testid="pick-store"
                    >
                      <span className="rank-name">
                        <EcoRichText text={s.label} />
                        <span className="section-sub"> · <EcoRichText text={s.owner} /></span>
                      </span>
                      <span className="rank-count">{formatCount(s.totalVolume)} in trades</span>
                    </button>
                  </li>
                ))
              )}
            </ul>
          )}
        </section>
      )}

      {store && (
        <section data-testid="shop-check">
          <h2 className="section-title">
            <EcoRichText text={store.label} /> vs market{" "}
            <span className="section-sub">
              (marked when more than {NOTABLE_PCT}% above or below the middle price)
            </span>
          </h2>
          {rows.length === 0 ? (
            <p className="empty-note" data-testid="shop-empty">
              No prices for this store's items yet.
            </p>
          ) : (
            <table tabIndex={0} className="ledger-table" data-testid="shop-table">
              <thead>
                <tr>
                  <th>Item</th>
                  <th className="num">Your price</th>
                  <th className="num">Middle price</th>
                  <th className="num">Above or below market</th>
                  <th>Price check</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const v = VERDICT[r.verdict]
                  return (
                    <tr key={r.item} data-testid="shop-row">
                      <td>
                        <ItemLink className="linklike" item={r.item}>
                          {r.pretty}
                        </ItemLink>
                      </td>
                      <td className="num">
                        <ItemPrice price={r.avgUnitPrice} norm={r.avgNorm} />
                      </td>
                      <td className="num">
                        <ItemPrice price={r.median} norm={r.medianNorm} currency={r.currency} />
                      </td>
                      <td className="num">
                        {r.deltaPct != null
                          ? `${r.deltaPct > 0 ? "+" : ""}${Math.round(r.deltaPct)}%`
                          : "—"}
                      </td>
                      <td>
                        <span
                          className="verdict-tag"
                          style={{ color: v.color }}
                          data-testid="shop-verdict"
                        >
                          <span aria-hidden="true">{v.glyph}</span> {v.label}
                        </span>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          )}
        </section>
      )}
    </Layout>
  )
}
