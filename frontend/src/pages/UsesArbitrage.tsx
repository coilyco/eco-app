import { useMemo } from "react"
import { Link } from "react-router-dom"
import EcoRichText from "../components/EcoRichText"
import ItemLink from "../components/ItemLink"
import FreshnessNote from "../components/FreshnessNote"
import ItemPrice from "../components/ItemPrice"
import Layout from "../components/Layout"
import { fetchLogistics } from "../lib/logisticsApi"
import { formatCount, formatMoney } from "../lib/format"
import { useFreshData } from "../lib/useFreshData"

const ROWS = 40

// "Buy low here, sell high there" (eco-app#99): the cross-store arbitrage
// spreads from the logistics board, ranked by opportunity (spread × movable
// volume). Reads the same /preview/logistics.json plane /trade does, which can
// 404 on a reset-gated shelf, so the page degrades to a clear note.
export default function UsesArbitrage() {
  // Refresh contract lives in freshness.ts, not here (eco-app#201).
  const logisticsPlane = useFreshData("logistics", (signal) => fetchLogistics(signal).catch(() => null))
  const logistics = logisticsPlane.data
  const loaded = !logisticsPlane.loading


  const spreads = useMemo(
    () => (logistics ? [...logistics.arbitrage].sort((a, b) => b.opportunity - a.opportunity) : []),
    [logistics],
  )

  return (
    <Layout fetchedAtISO={logistics?.fetchedAtISO}>
      <section className="hero hero-compact">
        <p className="hero-kicker">
          <Link to="/uses" className="linklike" data-testid="back-to-uses">
            ← Use cases
          </Link>
        </p>
        <h1 className="hero-title">
          Buy low here, <span className="accent">sell high there</span>
        </h1>
        {spreads.length > 0 && (
          <p className="hero-pill" data-testid="arb-pill">
            <span className="pulse-dot" aria-hidden="true" />
            {formatCount(spreads.length)} item{spreads.length === 1 ? "" : "s"} to buy low and sell high
          </p>
        )}
        {loaded && spreads.length === 0 && (
          <p className="hero-pill hero-pill-muted" data-testid="arb-empty">
            nothing to buy low and sell high right now (every store charges about the same, or store
            prices haven't come in yet)
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
        <p className="empty-note" data-testid="arb-loading">
          Loading store prices…
        </p>
      )}

      {spreads.length > 0 && (
        <section data-testid="arb-list">
          <h2 className="section-title">
            Buy here, sell there <span className="section-sub">(biggest total profit first)</span>
          </h2>
          <table tabIndex={0} className="ledger-table" data-testid="arb-table">
            <thead>
              <tr>
                <th>Item</th>
                <th>Buy at</th>
                <th>Sell at</th>
                <th className="num">Profit each</th>
                <th className="num">How many you can move</th>
                <th className="num">Profit if you move them all</th>
              </tr>
            </thead>
            <tbody>
              {spreads.slice(0, ROWS).map((a) => (
                <tr
                  key={`${a.item}-${a.buyFrom.storeKey}-${a.sellTo.storeKey}`}
                  data-testid="arb-row"
                >
                  <td>
                    <ItemLink className="linklike" item={a.item}>
                      {a.itemPretty}
                    </ItemLink>
                  </td>
                  <td>
                    <ItemPrice price={a.buyFrom.price} norm={a.buyFrom.norm ?? a.norm} currency={a.currency} layout="inline" />{" "}
                    at <EcoRichText text={a.buyFrom.store} />
                  </td>
                  <td>
                    <ItemPrice price={a.sellTo.price} norm={a.sellTo.norm ?? a.norm} currency={a.currency} layout="inline" />{" "}
                    at <EcoRichText text={a.sellTo.store} />
                  </td>
                  <td className="num">
                    +{formatMoney(a.spread)} {a.currency} ({Math.round(a.spreadPct)}%)
                  </td>
                  <td className="num">{formatCount(a.volume)}</td>
                  <td className="num">{formatCount(a.opportunity)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {loaded && (
        <section className="k-card-grid k-card-grid--condensed dir-cards">
          <Link className="k-card dir-card" to="/uses/buy-sell" data-testid="link-buy-sell">
            <h2>Where to buy / sell →</h2>
            <p>Price one item across every shelf — the cheapest to buy, the best to sell into.</p>
          </Link>
          <Link className="k-card dir-card" to="/trade" data-testid="link-trade">
            <h2>Trade &amp; logistics →</h2>
            <p>The whole market — movers, price history, stores, and the full ledger.</p>
          </Link>
        </section>
      )}
    </Layout>
  )
}
