import { useMemo } from "react"
import { Link } from "react-router-dom"
import ItemLink from "../components/ItemLink"
import FreshnessNote from "../components/FreshnessNote"
import Layout from "../components/Layout"
import {
  fetchFoodReport,
  type FoodSignal,
  type FoodSignalKind,
} from "../lib/foodApi"
import { formatCount } from "../lib/format"
import { useFreshData } from "../lib/useFreshData"

const SIGNALS: Record<FoodSignalKind, { label: string; tone: string }> = {
  restock: { label: "restock", tone: "var(--meteor)" },
  balanced: { label: "about right", tone: "var(--moss)" },
  potential_overstock: { label: "maybe too much", tone: "var(--meteor-deep)" },
  insufficient: { label: "not enough data", tone: "var(--ink-faint)" },
}

function FoodRow({ row }: { row: FoodSignal }) {
  const signal = SIGNALS[row.signal]
  const query = encodeURIComponent(row.item)
  return (
    <li className="gap-row" data-testid="food-row">
      <div className="gap-head">
        <ItemLink className="linklike gap-name" item={row.item}>
          {row.itemPretty}
        </ItemLink>
        <span className="gap-tag" style={{ color: signal.tone }} data-testid="food-signal">
          {signal.label}
        </span>
      </div>
      <p className="gap-summary">{row.reason}</p>
      <p className="gap-who">
        {formatCount(row.supplyQty)} for sale · {formatCount(row.demandQty)} wanted · {formatCount(row.tradeCount)} trades · {formatCount(row.craftCount)} made
      </p>
      <p className="gap-who">
        <Link className="linklike" to={`/trade?q=${query}`}>trade</Link>{" · "}
        <Link className="linklike" to={`/recipes?q=${query}`}>recipe</Link>{" · "}
        <Link className="linklike" to={`/uses/price?item=${query}`}>price it</Link>
      </p>
    </li>
  )
}

export default function UsesFood() {
  // Refresh contract lives in freshness.ts, not here (eco-app#201).
  const foodPlane = useFreshData("food", (signal) => fetchFoodReport(signal).catch(() => null))
  const report = foodPlane.data
  const loaded = !foodPlane.loading


  const rows = useMemo(() => report?.signals ?? [], [report])
  return (
    <Layout fetchedAtISO={report?.fetchedAtISO}>
      <section className="hero hero-compact">
        <p className="hero-kicker"><Link to="/uses" className="linklike">← Use cases</Link></p>
        <h1 className="hero-title">Food <span className="accent">to restock or watch</span></h1>
        <p className="hero-tagline">
          Only food from cooking, baking, and chef recipes. Items we can't confirm as food are left out.
        </p>
        <FreshnessNote
          plane="food"
          loadedAt={foodPlane.loadedAt}
          refreshing={foodPlane.refreshing}
          refreshError={foodPlane.refreshError}
          onRefresh={foodPlane.refresh}
        />
      </section>
      {!loaded && <p className="empty-note">Loading food in shops and what people have made…</p>}
      {loaded && !report && <p className="empty-note" data-testid="food-unavailable">Food data is not available right now.</p>}
      {report && rows.length === 0 && <p className="empty-note" data-testid="food-empty">None of the known foods have been listed in shops or sold yet.</p>}
      {rows.length > 0 && (
        <section data-testid="food-list">
          <h2 className="section-title">Food to check <span className="section-sub">({formatCount(rows.length)} foods)</span></h2>
          <ul className="gap-list">{rows.map((row) => <FoodRow key={row.item} row={row} />)}</ul>
        </section>
      )}
      {report?.warnings.length ? <ul className="warn-list">{report.warnings.map((warning) => <li key={warning}>⚠ {warning}</li>)}</ul> : null}
    </Layout>
  )
}
