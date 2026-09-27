import { useMemo } from "react"
import { Link } from "react-router-dom"
import EcoRichText from "../components/EcoRichText"
import ItemLink from "../components/ItemLink"
import FreshnessNote from "../components/FreshnessNote"
import ItemPrice from "../components/ItemPrice"
import Layout from "../components/Layout"
import { fetchLogistics, type GapReason, type SupplyGap } from "../lib/logisticsApi"
import { formatCount } from "../lib/format"
import { useFreshData } from "../lib/useFreshData"

const GAP_ROWS = 40

// Supply-gap severity, encoded glyph + label + colour (never colour alone, per
// the dataviz non-negotiables) — mirrors the /trade board: an unmet buy order is
// the loudest (meteor amber), a lone monopolist is thin supply, a merely
// over-priced shelf stays muted ink.
const GAP: Record<GapReason, { glyph: string; label: string; color: string }> = {
  no_supply: { glyph: "✖", label: "no supply", color: "var(--meteor)" },
  thin_supply: { glyph: "◐", label: "thin supply", color: "var(--meteor-deep)" },
  overpriced: { glyph: "▲", label: "over-priced", color: "var(--ink-faint)" },
}

function DemandRow({ gap }: { gap: SupplyGap }) {
  const g = GAP[gap.reason]
  return (
    <li className="gap-row" data-testid="demand-row">
      <div className="gap-head">
        <ItemLink className="linklike gap-name" item={gap.item}>
          {gap.itemPretty}
        </ItemLink>
        <span className="gap-tag" style={{ color: g.color }} data-testid="demand-tag">
          <span aria-hidden="true">{g.glyph}</span> {g.label}
        </span>
      </div>
      <p className="gap-summary">
        {formatCount(gap.demandQty)} wanted · {formatCount(gap.buyerCount)} buyer
        {gap.buyerCount === 1 ? "" : "s"}
        {gap.sellerCount > 0
          ? ` · ${formatCount(gap.sellerCount)} seller${gap.sellerCount === 1 ? "" : "s"}`
          : ""}
      </p>
      {gap.buyers.length > 0 && (
        <p className="gap-who" data-testid="demand-who">
          <span className="gap-who-label">Who needs it:</span>{" "}
          {gap.buyers.map((b, i) => (
            <span key={`${b.owner}-${b.store}-${i}`} className="gap-buyer">
              <EcoRichText text={b.owner || b.store} />{" "}
              <span className="gap-buyer-qty">{formatCount(b.quantity)}</span>
              {b.price ? (
                <>
                  {" "}
                  <ItemPrice
                    price={b.price}
                    norm={b.norm ?? gap.norm}
                    currency={gap.currency}
                    showCurrency
                    prefix="@ "
                    layout="inline"
                  />
                </>
              ) : (
                ""
              )}
              {i < gap.buyers.length - 1 ? ", " : ""}
            </span>
          ))}
        </p>
      )}
    </li>
  )
}

// "What's in demand right now" — the supply-gap signal given its own focused
// page (eco-app#99). Reads the same /preview/logistics.json plane /trade does;
// the sibling can 404 on a reset-gated shelf, so fetchLogistics resolves to null
// and the page degrades to a clear note.
export default function UsesDemand() {
  // Refresh contract lives in freshness.ts, not here (eco-app#201).
  const logisticsPlane = useFreshData("logistics", (signal) => fetchLogistics(signal).catch(() => null))
  const logistics = logisticsPlane.data
  const loaded = !logisticsPlane.loading


  const gaps = useMemo(
    () => (logistics ? [...logistics.supplyGaps].sort((a, b) => b.demandQty - a.demandQty) : []),
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
          What's <span className="accent">in demand</span> right now
        </h1>
        {gaps.length > 0 && (
          <p className="hero-pill" data-testid="demand-pill">
            <span className="pulse-dot" aria-hidden="true" />
            {formatCount(gaps.length)} item{gaps.length === 1 ? "" : "s"} with unmet demand
          </p>
        )}
        {loaded && gaps.length === 0 && (
          <p className="hero-pill hero-pill-muted" data-testid="demand-empty">
            no supply gaps right now — either every buy order is being met, or the shelf data hasn't
            landed yet
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
        <p className="empty-note" data-testid="demand-loading">
          Loading demand…
        </p>
      )}

      {gaps.length > 0 && (
        <section data-testid="demand-list">
          <h2 className="section-title">
            Supply gaps <span className="section-sub">(ranked by quantity wanted)</span>
          </h2>
          <ul className="gap-list">
            {gaps.slice(0, GAP_ROWS).map((g) => (
              <DemandRow key={`${g.item}-${g.currency}`} gap={g} />
            ))}
          </ul>
        </section>
      )}

      {loaded && (
        <section className="k-card-grid k-card-grid--condensed dir-cards">
          <Link className="k-card dir-card" to="/uses/buy-sell" data-testid="link-buy-sell">
            <h2>Where to buy / sell →</h2>
            <p>Pick an item and see the cheapest shelves to buy from and the best to sell into.</p>
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
