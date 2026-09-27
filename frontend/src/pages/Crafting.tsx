import { Link, useSearchParams } from "react-router-dom"
import ItemLink from "../components/ItemLink"
import FreshnessNote from "../components/FreshnessNote"
import Layout from "../components/Layout"
import { fetchCraftingAtlas } from "../lib/craftingApi"
import { formatCount, prettifyEcoName } from "../lib/format"
import { useFreshData } from "../lib/useFreshData"

const ACTION_LABELS: Record<string, string> = {
  ItemCraftedAction: "crafted",
  HarvestOrHunt: "harvested",
  ChopTree: "trees felled",
  DigOrMine: "mined",
}

const TOP_N = 25

interface RankTableProps {
  rows: Array<[string, number]>
  filter: string
  emptyNote: string
  onPick: (name: string) => void
}

// Ranked bar list. Concrete item names open their pivots; the explicit Filter
// control retains the atlas's existing in-page drill-down interaction.
function RankTable({ rows, filter, emptyNote, onPick }: RankTableProps) {
  const needle = filter.trim().toLowerCase()
  const matches = needle
    ? rows.filter(([name]) => prettifyEcoName(name).toLowerCase().includes(needle))
    : rows.slice(0, TOP_N)
  const max = Math.max(...matches.map(([, count]) => count), 1)

  if (matches.length === 0) {
    return <p className="empty-note">{emptyNote}</p>
  }
  return (
    <ul className="rank-rows">
      {matches.map(([name, count]) => (
        <li key={name}>
          <div className="rank-row">
            <ItemLink className="rank-name linklike" item={name}>
              {prettifyEcoName(name)}
            </ItemLink>
            <span className="rank-count">{formatCount(count)}</span>
            <button
              className="linklike"
              onClick={() => onPick(prettifyEcoName(name))}
              aria-label={`Filter crafting atlas by ${prettifyEcoName(name)}`}
            >
              Filter
            </button>
            <span className="rank-bar" style={{ width: `${(count / max) * 100}%` }} />
          </div>
        </li>
      ))}
    </ul>
  )
}

export default function Crafting() {
  // Refresh contract lives in freshness.ts, not here (eco-app#201).
  const craftingPlane = useFreshData("crafting", fetchCraftingAtlas)
  const atlas = craftingPlane.data
  const error = craftingPlane.error
  const [params, setParams] = useSearchParams()
  const q = params.get("q") ?? ""


  const setQuery = (value: string) => {
    setParams(value ? { q: value } : {}, { replace: false })
  }

  return (
    <Layout>
      {/* One heading + the live pill as the single intro line (eco-app#97). */}
      <section className="hero hero-compact">
        <h1 className="hero-title">Crafting atlas</h1>
        {atlas && (
          <p className="hero-pill" data-testid="atlas-pill">
            <span className="pulse-dot" aria-hidden="true" />
            {formatCount(atlas.totalEvents)} production events ·{" "}
            {Object.entries(atlas.perActionCounts)
              .filter(([, count]) => count > 0)
              .map(([name, count]) => `${formatCount(count)} ${ACTION_LABELS[name] ?? name}`)
              .join(" · ")}
          </p>
        )}
        {!atlas && error && (
          <p className="hero-pill hero-pill-muted" data-testid="atlas-error">
            crafting atlas unavailable right now
          </p>
        )}
        <FreshnessNote
          plane="crafting"
          loadedAt={craftingPlane.loadedAt}
          refreshing={craftingPlane.refreshing}
          refreshError={craftingPlane.refreshError}
          onRefresh={craftingPlane.refresh}
        />
      </section>

      {atlas && (
        <>
          <section className="filter-row">
            <input
              className="filter-input"
              type="search"
              placeholder="Filter items, resources, and stations… (deep-linkable as ?q=)"
              value={q}
              onChange={(e) => setQuery(e.target.value)}
              data-testid="atlas-filter"
            />
            {q && (
              <button className="button" onClick={() => setQuery("")}>
                Clear
              </button>
            )}
          </section>

          <div className="atlas-columns">
            <section>
              {/* Confirmed iterations: per-event rows count 1 each, hourly
                  rollups count only their proven representative (eco-app#131). */}
              <h2 className="section-title">Top crafted {q ? `matching "${q}"` : ""}</h2>
              <RankTable
                rows={atlas.byCrafted}
                filter={q}
                emptyNote="No crafted items match."
                onPick={setQuery}
              />
            </section>
            <section>
              <h2 className="section-title">Top gathered {q ? `matching "${q}"` : ""}</h2>
              <RankTable
                rows={atlas.byGathered}
                filter={q}
                emptyNote="No gathered resources match."
                onPick={setQuery}
              />
            </section>
          </div>

          <section>
            <h2 className="section-title">Top stations {q ? `matching "${q}"` : ""}</h2>
            <RankTable
              rows={atlas.byStation}
              filter={q}
              emptyNote="No stations match."
              onPick={setQuery}
            />
          </section>

          <section className="k-card-grid k-card-grid--condensed dir-cards">
            <Link className="k-card dir-card" to="/trade" data-testid="link-trade">
              <h2>Trade →</h2>
              <p>Where this production goes — the market and every individual trade, with price over time.</p>
            </Link>
            <Link className="k-card dir-card" to="/items" data-testid="link-items">
              <h2>Item directory →</h2>
              <p>Every item this production touches — click through to its full history.</p>
            </Link>
          </section>
        </>
      )}
    </Layout>
  )
}
