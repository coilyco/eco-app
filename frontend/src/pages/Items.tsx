import { useMemo } from "react"
import { Link, useSearchParams } from "react-router-dom"
import ItemLink from "../components/ItemLink"
import FreshnessNote from "../components/FreshnessNote"
import Layout from "../components/Layout"
import { fetchItemIndex, type ItemStat } from "../lib/itemsApi"
import { formatCount, prettifyEcoName } from "../lib/format"
import { useFreshData } from "../lib/useFreshData"

const LIST_ROWS = 200

// Sort modes for the directory. "activity" is the index's own ranking (total
// trades + crafted); the others let a shopper re-rank by a single column.
type SortKey = "activity" | "trades" | "volume" | "crafted"

const SORTS: Array<{ key: SortKey; label: string }> = [
  { key: "activity", label: "Most activity" },
  { key: "trades", label: "Most trades" },
  { key: "volume", label: "Most money spent" },
  { key: "crafted", label: "Most crafted" },
]

function sortItems(items: ItemStat[], key: SortKey): ItemStat[] {
  if (key === "activity") return items // the index is already activity-ranked
  const pick: Record<Exclude<SortKey, "activity">, (r: ItemStat) => number> = {
    trades: (r) => r.tradeCount,
    volume: (r) => r.tradeVolume,
    crafted: (r) => r.craftCount,
  }
  const f = pick[key]
  return [...items].sort((a, b) => f(b) - f(a))
}

// Directory of every item ever bought, sold, or crafted. Each row deep-links to
// the per-item pivot (/item?item=<id>). The ?q= name filter and the ?sort=
// column are deep-linkable. Untraded (craft-only) items are always hidden —
// most of the directory is noise for someone shopping.
export default function Items() {
  // Refresh contract lives in freshness.ts, not here (eco-app#201).
  const itemsPlane = useFreshData("items", fetchItemIndex)
  const index = itemsPlane.data
  const error = itemsPlane.error
  const [params, setParams] = useSearchParams()
  const q = params.get("q") ?? ""
  const sort = (params.get("sort") ?? "activity") as SortKey

  // Preserve the other params when one control changes.
  const update = (patch: Record<string, string>) => {
    const next: Record<string, string> = {}
    if (q) next.q = q
    if (sort !== "activity") next.sort = sort
    for (const [k, v] of Object.entries(patch)) {
      if (v) next[k] = v
      else delete next[k]
    }
    setParams(next, { replace: false })
  }

  const needle = q.trim().toLowerCase()
  const visible = useMemo(() => {
    if (!index) return []
    let rows = index.items.filter((r) => r.tradeCount > 0)
    if (needle) rows = rows.filter((r) => prettifyEcoName(r.item).toLowerCase().includes(needle))
    return sortItems(rows, sort).slice(0, LIST_ROWS)
  }, [index, needle, sort])

  return (
    <Layout fetchedAtISO={index?.fetchedAtISO}>
      <section className="hero hero-compact">
        <p className="hero-kicker">Item directory</p>
        <h1 className="hero-title">
          Every <span className="accent">item</span> the world has touched
        </h1>
        {!index && error && (
          <p className="hero-pill hero-pill-muted" data-testid="items-error">
            item directory unavailable right now
          </p>
        )}
        <FreshnessNote plane="items" loadedAt={itemsPlane.loadedAt} />
      </section>

      {index && index.totalItems === 0 && (
        <section>
          <p className="empty-note" data-testid="items-empty">
            No items recorded on this server yet. Early in a cycle this is normal, so check back
            after a few days of trading and crafting.
          </p>
        </section>
      )}

      {index && index.totalItems > 0 && (
        <>
          <section className="filter-row">
            <input
              className="filter-input"
              type="search"
              placeholder="Filter items by name…"
              value={q}
              onChange={(e) => update({ q: e.target.value })}
              data-testid="items-filter"
            />
            {q && (
              <button className="button" onClick={() => update({ q: "" })}>
                Clear
              </button>
            )}
          </section>

          <section className="controls-row" data-testid="items-controls">
            <div className="sort-buttons" role="group" aria-label="Sort items">
              <span className="sort-label">Sort by:</span>
              {SORTS.map((s) => (
                <button
                  key={s.key}
                  className={`chip ${sort === s.key ? "chip-active" : ""}`}
                  onClick={() => update({ sort: s.key === "activity" ? "" : s.key })}
                  data-testid={`sort-${s.key}`}
                  aria-pressed={sort === s.key}
                >
                  {s.label}
                </button>
              ))}
            </div>
          </section>

          <section>
            <h2 className="section-title">
              Items {q ? `matching "${q}"` : ""}{" "}
              <span className="section-sub">
                (showing {visible.length}
                {index.items.length > visible.length
                  ? ` of ${formatCount(index.items.length)}`
                  : ""}
                )
              </span>
            </h2>
            {visible.length === 0 ? (
              <p className="empty-note" data-testid="items-no-match">
                No items match.
              </p>
            ) : (
              <table tabIndex={0} className="ledger-table" data-testid="items-table">
                <thead>
                  <tr>
                    <th>Item</th>
                    <th className="num">Trades</th>
                    <th className="num">Money spent</th>
                    <th className="num">Crafted</th>
                  </tr>
                </thead>
                <tbody>
                  {visible.map((r) => (
                    <tr key={r.item} data-testid="item-row">
                      <td>
                        <ItemLink className="linklike" item={r.item}>
                          {prettifyEcoName(r.item)}
                        </ItemLink>
                      </td>
                      <td className="num">{formatCount(r.tradeCount)}</td>
                      <td className="num">
                        {r.tradeVolume ? formatCount(r.tradeVolume) : "—"}
                      </td>
                      <td className="num">{r.craftCount ? formatCount(r.craftCount) : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>

          {index.warnings.length > 0 && (
            <section>
              <ul className="warn-list" data-testid="items-warnings">
                {index.warnings.map((w) => (
                  <li key={w}>⚠ {w}</li>
                ))}
              </ul>
            </section>
          )}

          <section className="k-card-grid k-card-grid--condensed dir-cards">
            <Link className="k-card dir-card" to="/trade" data-testid="link-trade">
              <h2>Trade →</h2>
              <p>The market, and every single trade with its price over time.</p>
            </Link>
            <Link className="k-card dir-card" to="/crafting" data-testid="link-crafting">
              <h2>Crafting atlas →</h2>
              <p>What the world is making: the top items and stations.</p>
            </Link>
          </section>
        </>
      )}
    </Layout>
  )
}
