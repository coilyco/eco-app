import { useMemo } from "react"
import ChartFrame from "../components/ChartFrame"
import { Link, useSearchParams } from "react-router-dom"
import EcoRichText from "../components/EcoRichText"
import ItemLink from "../components/ItemLink"
import ItemPrice from "../components/ItemPrice"
import FreshnessNote from "../components/FreshnessNote"
import Layout from "../components/Layout"
import { fetchMarket, type ItemMarket, type MarketTrend } from "../lib/marketApi"
import { fetchStores } from "../lib/storesApi"
import { fetchLogistics, type GapReason, type SupplyGap } from "../lib/logisticsApi"
import { fetchTradesLedger, type Trade as TradeRow } from "../lib/tradesApi"
import { formatMoney, formatCount, prettifyEcoName, stripEcoMarkup } from "../lib/format"
import { useFreshData } from "../lib/useFreshData"

const TOP_MOVERS = 6
const TOP_TRADED = 12
const DIR_ROWS = 10
const LOGI_ROWS = 8
// Supply gaps are the valued board (eco-app#95): render the whole delivered list
// (the backend caps it at SUPPLY_GAP_ROWS) rather than clipping to LOGI_ROWS, so
// 20+ gaps surface when the server has them.
const GAP_ROWS = 40
const LEDGER_ROWS = 60

// Prices carry fractional cents; formatCount rounds to whole units, so trade
// surfaces get their own 2-dp formatter.
// Trend is encoded glyph + label + colour (never colour alone, per the dataviz
// non-negotiables): rising leans on the leaf green, falling on meteor amber,
// flat/insufficient stay muted ink.
const TREND: Record<MarketTrend, { glyph: string; label: string; color: string }> = {
  rising: { glyph: "▲", label: "rising", color: "var(--leaf)" },
  falling: { glyph: "▼", label: "falling", color: "var(--meteor)" },
  flat: { glyph: "▬", label: "flat", color: "var(--ink-faint)" },
  insufficient: { glyph: "·", label: "few sales", color: "var(--ink-faint)" },
}

function TrendTag({ trend, delta }: { trend: MarketTrend; delta: number | null }) {
  const t = TREND[trend]
  const pct = delta !== null && trend !== "flat" && trend !== "insufficient"
    ? ` ${delta > 0 ? "+" : ""}${Math.round(delta)}%`
    : ""
  return (
    <span className="trend-tag" style={{ color: t.color }} data-testid="trend-tag">
      <span aria-hidden="true">{t.glyph}</span> {t.label}
      {pct}
    </span>
  )
}

// Supply-gap severity, encoded glyph + label + colour (never colour alone, per
// the dataviz non-negotiables): an unmet buy order is the loudest (meteor
// amber), a lone monopolist is thin supply (bark), a merely over-priced shelf
// stays muted ink.
const GAP: Record<GapReason, { glyph: string; label: string; color: string }> = {
  no_supply: { glyph: "✖", label: "out of stock", color: "var(--meteor)" },
  thin_supply: { glyph: "◐", label: "low stock", color: "var(--meteor-deep)" },
  overpriced: { glyph: "▲", label: "overpriced", color: "var(--ink-faint)" },
}

// One supply gap: the item, a reason tag, and — the point of eco-app#77 — WHO
// needs it. A gap is only actionable if you know who is asking, so the buy-side
// citizens and how much each still wants get their own line rather than being
// crushed into the narrow count column.
function SupplyGapRow({ gap }: { gap: SupplyGap }) {
  const g = GAP[gap.reason]
  const summary =
    gap.reason === "overpriced" ? (
      <>
        cheapest{" "}
        <ItemPrice price={gap.cheapestSell} norm={gap.norm} currency={gap.currency} layout="inline" /> vs
        a middle price of <ItemPrice price={gap.median} norm={gap.norm} currency={gap.currency} layout="inline" />
        {gap.overMedianPct !== null ? ` (${Math.round(gap.overMedianPct)}% more)` : ""}
      </>
    ) : (
      `${formatCount(gap.demandQty)} wanted · ${formatCount(gap.buyerCount)} buyer${
          gap.buyerCount === 1 ? "" : "s"
        } · ${formatCount(gap.sellerCount)} seller${gap.sellerCount === 1 ? "" : "s"}`
    )
  return (
    <li className="gap-row" data-testid="gap-row">
      <div className="gap-head">
        <ItemLink className="linklike gap-name" item={gap.item}>
          {gap.itemPretty}
        </ItemLink>
        <span className="gap-tag" style={{ color: g.color }} data-testid="gap-tag">
          <span aria-hidden="true">{g.glyph}</span> {g.label}
        </span>
      </div>
      <p className="gap-summary">{summary}</p>
      {gap.buyers.length > 0 && (
        <p className="gap-who" data-testid="gap-who">
          <span className="gap-who-label">Who needs it:</span>{" "}
          {gap.buyers.map((b, i) => (
            <span key={`${b.owner}-${b.store}-${i}`} className="gap-buyer">
              <EcoRichText text={b.owner || b.store} /> wants{" "}
              <span className="gap-buyer-qty">{formatCount(b.quantity)}</span>
              {i < gap.buyers.length - 1 ? ", " : ""}
            </span>
          ))}
        </p>
      )}
    </li>
  )
}

// Compact inline SVG line of an item's median unit price per in-game day. No
// chart lib — a single sparkline keeps the bundle lean and CSP trivial, matching
// the sibling /trades chart.
function PriceChart({ points }: { points: Array<[number, number]> }) {
  const width = 620
  const height = 160
  const pad = 28
  if (points.length < 2) {
    return <p className="empty-note">Not enough past sales to draw a chart for this item yet.</p>
  }
  const days = points.map(([d]) => d)
  const prices = points.map(([, p]) => p)
  const minDay = Math.min(...days)
  const maxDay = Math.max(...days)
  const minPrice = Math.min(...prices)
  const maxPrice = Math.max(...prices)
  const daySpan = maxDay - minDay || 1
  const priceSpan = maxPrice - minPrice || 1

  const x = (d: number) => pad + ((d - minDay) / daySpan) * (width - 2 * pad)
  const y = (p: number) => height - pad - ((p - minPrice) / priceSpan) * (height - 2 * pad)
  const line = points.map(([d, p]) => `${x(d).toFixed(1)},${y(p).toFixed(1)}`).join(" ")

  return (
    <ChartFrame
      above={[`highest ${formatMoney(maxPrice)}`, `lowest ${formatMoney(minPrice)}`]}
      start={`day ${minDay}`}
      end={`day ${maxDay}`}
    >
    <svg
      className="price-chart"
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="xMidYMid meet"
      role="img"
      aria-label="Middle price for one, by in-game day"
      data-testid="price-chart"
    >
      <polyline points={line} fill="none" stroke="var(--leaf)" strokeWidth="2" />
      {points.map(([d, p]) => (
        <circle key={d} cx={x(d)} cy={y(p)} r="3" fill="var(--meteor)">
          <title>
            Day {d}: {formatMoney(p)}
          </title>
        </circle>
      ))}
    </svg>
    </ChartFrame>
  )
}

// A ranked market list used for movers and most-traded. Concrete item ids link
// to their pivots; the surrounding row remains a non-interactive summary.
function MarketList({
  rows,
  onPick,
  testid,
}: {
  rows: ItemMarket[]
  onPick: (item: string) => void
  testid: string
}) {
  if (rows.length === 0) {
    return <p className="empty-note">Nothing here yet.</p>
  }
  const max = Math.max(...rows.map((m) => m.totalVolume), 1)
  return (
    <ul className="rank-rows" data-testid={testid}>
      {rows.map((m) => (
        <li key={`${m.item}-${m.currency}`}>
          <div className="rank-row" data-testid="market-row">
            <ItemLink className="rank-name linklike" item={m.item}>
              {m.itemPretty}
            </ItemLink>
            <span className="rank-count">
              <ItemPrice price={m.latestPrice} norm={m.norm} currency={m.currency} showCurrency layout="inline" />{" "}
              <TrendTag trend={m.trend} delta={m.trendDeltaPct} />
            </span>
            <button
              className="linklike"
              onClick={() => onPick(m.itemPretty)}
              aria-label={`Filter trades by ${m.itemPretty}`}
            >
              Filter
            </button>
            <span className="rank-bar" style={{ width: `${(m.totalVolume / max) * 100}%` }} />
          </div>
        </li>
      ))}
    </ul>
  )
}

function matchesTrade(t: TradeRow, needle: string): boolean {
  if (!needle) return true
  const hay = [stripEcoMarkup(t.seller), stripEcoMarkup(t.buyer), prettifyEcoName(t.item), t.currency]
    .join(" ")
    .toLowerCase()
  return hay.includes(needle)
}

export default function Trade() {
  // Refresh contract lives in freshness.ts, not here (eco-app#201). This is a
  // `live` composite: two of its five planes advance continuously and a trader
  // leaves the page open while deciding, so the board keeps up rather than
  // going quietly stale under them.
  //
  // Every plane stays best-effort and independent: siblings land on their own
  // schedule, so one 404 must never take the page down. Each resolves to null
  // and its panel degrades in place. The trades ledger
  // (folded in from the former /trades page, eco-app#90) is one more such
  // plane: the market plane carries the price-intelligence view, the ledger
  // the row-level trades.
  const tradePlane = useFreshData("trade", async (signal) => {
    const [market, stores, logistics, ledger] = await Promise.all([
      fetchMarket(signal).catch(() => null),
      fetchStores(signal).catch(() => null),
      fetchLogistics(signal).catch(() => null),
      fetchTradesLedger(signal).catch(() => null),
    ])
    return { market, stores, logistics, ledger }
  })
  const market = tradePlane.data?.market ?? null
  const stores = tradePlane.data?.stores ?? null
  const logistics = tradePlane.data?.logistics ?? null
  const ledger = tradePlane.data?.ledger ?? null
  const loaded = !tradePlane.loading
  const [params, setParams] = useSearchParams()
  const q = params.get("q") ?? ""

  const setQuery = (value: string) => {
    setParams(value ? { q: value } : {}, { replace: false })
  }

  const markets = useMemo(() => market?.markets ?? [], [market])
  const needle = q.trim().toLowerCase()

  const risers = useMemo(
    () =>
      markets
        .filter((m) => m.trend === "rising")
        .sort((a, b) => (b.trendDeltaPct ?? 0) - (a.trendDeltaPct ?? 0))
        .slice(0, TOP_MOVERS),
    [markets],
  )
  const fallers = useMemo(
    () =>
      markets
        .filter((m) => m.trend === "falling")
        .sort((a, b) => (a.trendDeltaPct ?? 0) - (b.trendDeltaPct ?? 0))
        .slice(0, TOP_MOVERS),
    [markets],
  )
  const mostTraded = useMemo(() => markets.slice(0, TOP_TRADED), [markets])

  const visibleTrades = useMemo(
    () => (ledger ? ledger.trades.filter((t) => matchesTrade(t, needle)).slice(0, LEDGER_ROWS) : []),
    [ledger, needle],
  )

  // The drilled item: the ?q= match if there is one, else the busiest market.
  const drill = useMemo(() => {
    if (markets.length === 0) return null
    if (needle) {
      const hit = markets.find(
        (m) => m.itemPretty.toLowerCase().includes(needle) || m.item.toLowerCase().includes(needle),
      )
      if (hit) return hit
    }
    return markets[0]
  }, [markets, needle])

  const drillPoints = useMemo<Array<[number, number]>>(
    () => (drill ? drill.buckets.map((b) => [b.day, b.median]) : []),
    [drill],
  )
  const drillSource = useMemo(() => {
    if (!drill || !logistics) return null
    const row = logistics.cheapest.find(
      (c) => c.item === drill.item || c.itemPretty.toLowerCase() === drill.itemPretty.toLowerCase(),
    )
    const best = row?.offers[0]
    if (!row || !best) return null
    return { unitPrice: best.price, currency: row.currency, store: best.store, owner: best.owner, norm: best.norm ?? row.norm }
  }, [drill, logistics])

  const topStores = useMemo(
    () => (stores ? [...stores.stores].sort((a, b) => b.totalVolume - a.totalVolume).slice(0, DIR_ROWS) : []),
    [stores],
  )

  const nothing =
    loaded && !market && !stores && !logistics && !ledger

  return (
    <Layout fetchedAtISO={market?.fetchedAtISO ?? stores?.fetchedAtISO}>
      <section className="hero hero-compact">
        <p className="hero-kicker">Trade &amp; store logistics</p>
        <h1 className="hero-title">
          What to <span className="accent">buy, sell, and ship</span>
        </h1>
        <p className="hero-tagline">
          The whole market on one always-on page: movers, price history, every store, and the
          logistics of what to do next.
        </p>
        {nothing && (
          <p className="hero-pill hero-pill-muted" data-testid="trade-error">
            Trade data is not available right now. Check back once players have traded on the server.
          </p>
        )}
        <FreshnessNote
          plane="trade"
          loadedAt={tradePlane.loadedAt}
          refreshing={tradePlane.refreshing}
          refreshError={tradePlane.refreshError}
          onRefresh={tradePlane.refresh}
        />
      </section>

      {!loaded && (
        <p className="empty-note" data-testid="trade-loading">
          Loading the market…
        </p>
      )}

      {/* Market overview — movers + most-traded. */}
      {market && markets.length > 0 && (
        <>
          <section className="atlas-columns" data-testid="movers">
            <div>
              <h2 className="section-title">Rising</h2>
              <MarketList rows={risers} onPick={setQuery} testid="risers" />
            </div>
            <div>
              <h2 className="section-title">Falling</h2>
              <MarketList rows={fallers} onPick={setQuery} testid="fallers" />
            </div>
          </section>

          <section>
            <h2 className="section-title">
              Most traded <span className="section-sub">(most trades first, press Filter to see one up close)</span>
            </h2>
            <MarketList rows={mostTraded} onPick={setQuery} testid="most-traded" />
          </section>
        </>
      )}

      {market && markets.length === 0 && (
        <section>
          <p className="empty-note" data-testid="market-empty">
            Nothing has a price yet. That is normal early in a cycle, so check back after a few days of
            trading.
          </p>
        </section>
      )}

      {/* Item drill-down. */}
      {drill && (
        <section data-testid="drill">
          <h2 className="section-title">
            <ItemLink className="linklike" item={drill.item}>
              {drill.itemPretty}
            </ItemLink>{" "}
            <span className="section-sub">
              ({drill.currency} · <TrendTag trend={drill.trend} delta={drill.trendDeltaPct} />)
            </span>
          </h2>
          <div className="filter-row">
            <input
              className="filter-input"
              type="search"
              placeholder="Look up an item…"
              value={q}
              onChange={(e) => setQuery(e.target.value)}
              data-testid="trade-filter"
            />
            {q && (
              <button className="button" onClick={() => setQuery("")}>
                Clear
              </button>
            )}
          </div>
          <div className="stats">
            <div className="stat">
              <p className="stat-value">
                <ItemPrice price={drill.latestPrice} norm={drill.norm} currency={drill.currency} />
              </p>
              <p className="stat-label">Latest price</p>
              <p className="stat-detail">day {drill.latestDay}</p>
            </div>
            <div className="stat">
              <p className="stat-value">
                <ItemPrice price={drill.medianPrice} norm={drill.norm} currency={drill.currency} />
              </p>
              <p className="stat-label">Middle price</p>
            </div>
            <div className="stat">
              <p className="stat-value">{formatCount(drill.totalVolume)}</p>
              <p className="stat-label">Units sold</p>
            </div>
            <div className="stat">
              <p className="stat-value">{formatCount(drill.totalTrades)}</p>
              <p className="stat-label">Sales</p>
            </div>
          </div>
          <PriceChart points={drillPoints} />
          {drillSource && (
            <p className="hero-pill" data-testid="drill-source">
              Cheapest right now:{" "}
              <ItemPrice
                price={drillSource.unitPrice}
                norm={drillSource.norm}
                currency={drillSource.currency}
                showCurrency
                layout="inline"
              />{" "}
              at{" "}
              <EcoRichText text={drillSource.store} /> (<EcoRichText text={drillSource.owner} />)
            </p>
          )}
          <p className="section-sub">
            <button className="linklike" onClick={() => setQuery(drill.itemPretty)}>
              Show only this item's trades below →
            </button>
          </p>
        </section>
      )}

      {/* Logistics board — the headline "what should I do" panel. The
          "Cheapest source" board was dropped as noise (eco-app#95); the
          per-item cheapest source still shows in the drill-down pill above. */}
      {logistics &&
        (logistics.arbitrage.length > 0 || logistics.supplyGaps.length > 0) && (
          <section data-testid="logistics">
            <h2 className="section-title">
              Logistics <span className="section-sub">(what to do next)</span>
            </h2>
            {logistics.arbitrage.length > 0 && (
              <>
                <h3 className="card-title">Buy here, sell there</h3>
                <table tabIndex={0} className="ledger-table" data-testid="arbitrage-table">
                  <thead>
                    <tr>
                      <th>Item</th>
                      <th>Buy at</th>
                      <th>Sell at</th>
                      <th className="num">Profit each</th>
                    </tr>
                  </thead>
                  <tbody>
                    {logistics.arbitrage.slice(0, LOGI_ROWS).map((a) => (
                      <tr
                        key={`${a.item}-${a.buyFrom.storeKey}-${a.sellTo.storeKey}`}
                        data-testid="arbitrage-row"
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
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            )}
            {logistics.supplyGaps.length > 0 && (
              <>
                <h3 className="card-title">
                  Wanted but not for sale <span className="section-sub">(what to stock, and who needs it)</span>
                </h3>
                <ul className="gap-list" data-testid="gaps-list">
                  {logistics.supplyGaps.slice(0, GAP_ROWS).map((g) => (
                    <SupplyGapRow key={`${g.item}-${g.currency}`} gap={g} />
                  ))}
                </ul>
              </>
            )}
          </section>
        )}

      {/* Store & trader directory. */}
      {stores && (stores.stores.length > 0 || stores.traders.length > 0) && (
        <section className="atlas-columns" data-testid="directory">
          <div>
            <h2 className="section-title">
              Stores <span className="section-sub">({formatCount(stores.totalStores)})</span>
            </h2>
            {topStores.length === 0 ? (
              <p className="empty-note">No stores found yet.</p>
            ) : (
              <ul className="rank-rows" data-testid="store-list">
                {topStores.map((st) => {
                  const max = Math.max(...topStores.map((x) => x.totalVolume), 1)
                  return (
                    <li key={st.storeKey}>
                      <div className="rank-row" data-testid="store-row">
                        <span className="rank-name">
                          <EcoRichText text={st.label} />
                          <span className="section-sub"> · <EcoRichText text={st.owner} /></span>
                        </span>
                        <span className="rank-count">{formatCount(st.totalVolume)} in trades</span>
                        <span className="rank-bar" style={{ width: `${(st.totalVolume / max) * 100}%` }} />
                      </div>
                    </li>
                  )
                })}
              </ul>
            )}
          </div>
        </section>
      )}

      {/* Row-level detailed-trades ledger — folded in from the former /trades page
          (eco-app#90). The same ?q= filter drives both the market drill above
          and this ledger, so drilling an item narrows the rows here too. Older
          hourly rollups do not have reliable party/item attribution. */}
      {ledger && ledger.totalTrades > 0 && (
        <section id="trade-ledger" data-testid="ledger">
          <h2 className="section-title">
            All trades {q ? `matching "${q}"` : ""}{" "}
            <span className="section-sub">
              (newest {visibleTrades.length}
              {ledger.trades.length > visibleTrades.length
                ? ` of ${formatCount(ledger.trades.length)}`
                : ""}
              )
            </span>
          </h2>
          {visibleTrades.length === 0 ? (
            <p className="empty-note">No trades match.</p>
          ) : (
            <table tabIndex={0} className="ledger-table" data-testid="trades-table">
              <thead>
                <tr>
                  <th>Day</th>
                  <th>Seller</th>
                  <th>Buyer</th>
                  <th>Item</th>
                  <th className="num">How many</th>
                  <th className="num">Paid</th>
                </tr>
              </thead>
              <tbody>
                {visibleTrades.map((t, i) => (
                  <tr key={`${t.time}-${i}`} data-testid="trade-row">
                    <td>{Math.floor(t.day)}</td>
                    <td>{t.seller ? <EcoRichText text={t.seller} /> : "unknown"}</td>
                    <td>{t.buyer ? <EcoRichText text={t.buyer} /> : "unknown"}</td>
                    <td>
                      {t.item ? (
                        <ItemLink className="linklike" item={t.item}>
                          {prettifyEcoName(t.item)}
                        </ItemLink>
                      ) : (
                        "unknown"
                      )}
                    </td>
                    <td className="num">{formatCount(t.quantity)}</td>
                    <td className="num">
                      {t.currencyAmount
                        ? `${formatCount(t.currencyAmount)} ${t.currency}`.trim()
                        : t.tradeType === "BarterTrade"
                          ? "item swap"
                          : "unknown"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {ledger.warnings.length > 0 && (
            <ul className="warn-list" data-testid="ledger-warnings">
              {ledger.warnings.map((w) => (
                <li key={w}>⚠ {w}</li>
              ))}
            </ul>
          )}
        </section>
      )}

      {/* Cross-links. */}
      {loaded && (
        <section className="k-card-grid k-card-grid--condensed dir-cards">
          <Link className="k-card dir-card" to="/crafting" data-testid="link-crafting">
            <h2>Crafting atlas →</h2>
            <p>Where the traded goods come from: what's made, where, and from what.</p>
          </Link>
          <Link className="k-card dir-card" to="/jobs" data-testid="link-jobs">
            <h2>Jobs →</h2>
            <p>Who can make the traded goods: professions, specialties, and skill history.</p>
          </Link>
        </section>
      )}
    </Layout>
  )
}
