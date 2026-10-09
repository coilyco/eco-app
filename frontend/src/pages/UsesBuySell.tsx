import { useMemo, useState } from "react"
import { Link, useSearchParams } from "react-router-dom"
import EcoRichText from "../components/EcoRichText"
import ItemLink from "../components/ItemLink"
import FreshnessNote from "../components/FreshnessNote"
import ItemPrice from "../components/ItemPrice"
import Layout from "../components/Layout"
import {
  fetchLogistics,
  type PricedBoardRow,
  type ShelfOffer,
} from "../lib/logisticsApi"
import { formatCount, prettifyEcoName } from "../lib/format"
import type { PriceNorm } from "../lib/priceNorm"
import { useFreshData } from "../lib/useFreshData"

const PICK_ROWS = 200
const OFFER_ROWS = 12

// Source provenance, encoded label + colour (never colour alone): a live shelf
// offer is the leaf green, a history-derived reconstruction stays muted ink.
function SourceTag({ source }: { source: string }) {
  const live = source === "live"
  return (
    <span
      className="source-tag"
      style={{ color: live ? "var(--leaf)" : "var(--ink-faint)" }}
      data-testid="source-tag"
    >
      {live ? "● in stores now" : "○ from past sales"}
    </span>
  )
}

function OfferTable({
  rows,
  currency,
  testid,
  norm,
}: {
  rows: ShelfOffer[]
  currency: string
  testid: string
  norm?: PriceNorm | null
}) {
  if (rows.length === 0) {
    return <p className="empty-note">No offers on this side right now.</p>
  }
  return (
    <table tabIndex={0} className="ledger-table" data-testid={testid}>
      <thead>
        <tr>
          <th>Store</th>
          <th>Owner</th>
          <th className="num">Price</th>
          <th className="num">How many</th>
          <th>How we know</th>
        </tr>
      </thead>
      <tbody>
        {rows.slice(0, OFFER_ROWS).map((o, i) => (
          <tr key={`${o.storeKey}-${o.side}-${i}`} data-testid="offer-row">
            <td><EcoRichText text={o.store} /></td>
            <td>{o.owner ? <EcoRichText text={o.owner} /> : "—"}</td>
            <td className="num">
              <ItemPrice price={o.price} norm={o.norm ?? norm} currency={o.currency || currency} showCurrency />
            </td>
            <td className="num">{formatCount(o.quantity)}</td>
            <td>
              <SourceTag source={o.source} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

// "Where to buy X cheapest / sell X highest" (eco-app#99). The item is picked
// via ?item=<id> (deep-linkable). Cheapest sells come off the logistics
// `cheapest` board (lowest sell price), best buys off `resale` (highest buy
// price) — both from the same /preview/logistics.json plane /trade reads, which
// can 404 on a reset-gated shelf, so the page degrades to a clear note.
export default function UsesBuySell() {
  // Refresh contract lives in freshness.ts, not here (eco-app#201).
  const logisticsPlane = useFreshData("logistics", (signal) => fetchLogistics(signal).catch(() => null))
  const logistics = logisticsPlane.data
  const loaded = !logisticsPlane.loading
  const [params, setParams] = useSearchParams()
  const item = params.get("item") ?? ""
  const [filter, setFilter] = useState("")


  const pickItem = (id: string) => {
    setParams(id ? { item: id } : {}, { replace: false })
  }

  // The union of every item on either board, ranked by how many total offers
  // back it — the busiest markets pick first.
  const options = useMemo(() => {
    if (!logistics) return []
    const byId = new Map<string, { item: string; pretty: string; offers: number }>()
    const add = (row: PricedBoardRow) => {
      const prev = byId.get(row.item)
      const offers = row.offers.length
      if (prev) prev.offers += offers
      else byId.set(row.item, { item: row.item, pretty: row.itemPretty, offers })
    }
    logistics.cheapest.forEach(add)
    logistics.resale.forEach(add)
    return [...byId.values()].sort((a, b) => b.offers - a.offers)
  }, [logistics])

  const needle = filter.trim().toLowerCase()
  const visibleOptions = useMemo(
    () =>
      (needle
        ? options.filter((o) => o.pretty.toLowerCase().includes(needle))
        : options
      ).slice(0, PICK_ROWS),
    [options, needle],
  )

  const sellRow = useMemo(
    () => logistics?.cheapest.find((r) => r.item === item) ?? null,
    [logistics, item],
  )
  const buyRow = useMemo(
    () => logistics?.resale.find((r) => r.item === item) ?? null,
    [logistics, item],
  )

  const cheapestSells = useMemo(
    () => (sellRow ? [...sellRow.offers].sort((a, b) => a.price - b.price) : []),
    [sellRow],
  )
  const bestBuys = useMemo(
    () => (buyRow ? [...buyRow.offers].sort((a, b) => b.price - a.price) : []),
    [buyRow],
  )

  const pretty = item ? prettifyEcoName(item) : ""
  const currency = sellRow?.currency || buyRow?.currency || ""

  return (
    <Layout fetchedAtISO={logistics?.fetchedAtISO}>
      <section className="hero hero-compact">
        <p className="hero-kicker">
          <Link to="/uses" className="linklike" data-testid="back-to-uses">
            ← Use cases
          </Link>
        </p>
        <h1 className="hero-title">
          {pretty ? (
            <>
              Where to buy &amp; sell{" "}
              <ItemLink className="accent linklike" item={item}>
                {pretty}
              </ItemLink>
            </>
          ) : (
            <>
              Where to <span className="accent">buy &amp; sell</span>
            </>
          )}
        </h1>
        {loaded && !logistics && (
          <p className="hero-pill hero-pill-muted" data-testid="buy-sell-error">
            store prices aren't available right now (check back once the game server sends store shelves)
          </p>
        )}
        <FreshnessNote
          plane="logistics"
          loadedAt={logisticsPlane.loadedAt}
          refreshing={logisticsPlane.refreshing}
          refreshError={logisticsPlane.refreshError}
          onRefresh={logisticsPlane.refresh}
        />
      </section>

      {!loaded && (
        <p className="empty-note" data-testid="buy-sell-loading">
          Loading store prices…
        </p>
      )}

      {logistics && (
        <section>
          <div className="filter-row">
            <input
              className="filter-input"
              type="search"
              placeholder="Pick an item to price…"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              data-testid="buy-sell-filter"
            />
            {item && (
              <button className="button" onClick={() => pickItem("")}>
                Clear
              </button>
            )}
          </div>
          {!item && (
            <ul className="rank-rows" data-testid="buy-sell-picker">
              {visibleOptions.length === 0 ? (
                <li>
                  <p className="empty-note">No items with store prices yet.</p>
                </li>
              ) : (
                visibleOptions.map((o) => (
                  <li key={o.item}>
                    <div className="rank-row" data-testid="pick-item">
                      <ItemLink className="rank-name linklike" item={o.item}>
                        {o.pretty}
                      </ItemLink>
                      <span className="rank-count">
                        {formatCount(o.offers)} offer{o.offers === 1 ? "" : "s"}
                      </span>
                      <button
                        className="linklike"
                        onClick={() => pickItem(o.item)}
                        aria-label={`Compare store prices for ${o.pretty}`}
                      >
                        Compare stores
                      </button>
                    </div>
                  </li>
                ))
              )}
            </ul>
          )}
        </section>
      )}

      {item && logistics && (
        <>
          {!sellRow && !buyRow && (
            <section>
              <p className="empty-note" data-testid="buy-sell-none">
                No store prices for{" "}
                <ItemLink className="linklike" item={item}>
                  {pretty}
                </ItemLink>
                , either in stores now or from past sales.
              </p>
            </section>
          )}
          {(sellRow || buyRow) && (
            <section className="atlas-columns" data-testid="buy-sell-boards">
              <div>
                <h2 className="section-title">
                  Buy it cheapest{" "}
                  {sellRow?.cheapest != null && (
                    <span className="section-sub">
                      (
                      <ItemPrice
                        price={sellRow.cheapest}
                        norm={sellRow.norm}
                        currency={currency}
                        showCurrency
                        prefix="from "
                        layout="clause"
                      />
                      )
                    </span>
                  )}
                </h2>
                <OfferTable rows={cheapestSells} currency={currency} testid="sell-offers" norm={sellRow?.norm} />
              </div>
              <div>
                <h2 className="section-title">
                  Sell it highest{" "}
                  {buyRow?.best != null && (
                    <span className="section-sub">
                      (
                      <ItemPrice
                        price={buyRow.best}
                        norm={buyRow.norm}
                        currency={currency}
                        showCurrency
                        prefix="up to "
                        layout="clause"
                      />
                      )
                    </span>
                  )}
                </h2>
                <OfferTable rows={bestBuys} currency={currency} testid="buy-offers" norm={buyRow?.norm} />
              </div>
            </section>
          )}
        </>
      )}
    </Layout>
  )
}
