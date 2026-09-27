import { useMemo } from "react"
import { Link } from "react-router-dom"
import FreshnessNote from "../components/FreshnessNote"
import Layout from "../components/Layout"
import Loading from "../components/Loading"
import { fetchSocial, type ReputationEdge } from "../lib/socialApi"
import { formatCount } from "../lib/format"
import { useFreshData } from "../lib/useFreshData"

const TOP_N = 12
// Cap the reputation graph to the busiest nodes so the circular layout stays
// legible. The ranked giver/receiver lists below carry the long tail.
const MAX_GRAPH_NODES = 10

// Single-series bar chart of a per-in-game-day count series. One hue, no legend.
// The section title names the series. Hand-rolled SVG keeps the bundle lean
// and CSP trivial, matching the /trades PriceChart.
function VolumeChart({
  points,
  color,
  label,
  testid,
}: {
  points: Array<[number, number]>
  color: string
  label: string
  testid: string
}) {
  const width = 620
  const height = 150
  const pad = 28
  if (points.length === 0) {
    return <p className="empty-note">No {label.toLowerCase()} recorded yet.</p>
  }
  const days = points.map(([d]) => d)
  const counts = points.map(([, c]) => c)
  const minDay = Math.min(...days)
  const maxDay = Math.max(...days)
  const maxCount = Math.max(...counts, 1)
  const daySpan = maxDay - minDay || 1
  // Bar width scales to the day span. Clamp so a sparse series still reads.
  const slot = (width - 2 * pad) / (daySpan + 1)
  const barW = Math.max(3, Math.min(28, slot * 0.7))

  const x = (d: number) => pad + ((d - minDay) / daySpan) * (width - 2 * pad)
  const y = (c: number) => height - pad - (c / maxCount) * (height - 2 * pad)

  return (
    <svg
      className="price-chart"
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="xMidYMid meet"
      role="img"
      aria-label={`${label} by in-game day`}
      data-testid={testid}
    >
      {points.map(([d, c]) => (
        <rect
          key={d}
          x={x(d) - barW / 2}
          y={y(c)}
          width={barW}
          height={height - pad - y(c)}
          rx={3}
          fill={color}
        >
          <title>
            Day {d}: {formatCount(c)} {label.toLowerCase()}
          </title>
        </rect>
      ))}
      <line
        x1={pad}
        y1={height - pad}
        x2={width - pad}
        y2={height - pad}
        stroke="var(--card-border)"
        strokeWidth="1"
      />
      <text x={pad} y={height - 6} className="axis-label">
        day {minDay}
      </text>
      <text x={width - pad} y={height - 6} textAnchor="end" className="axis-label">
        day {maxDay}
      </text>
      <text x={pad} y={16} className="axis-label">
        {formatCount(maxCount)}
      </text>
    </svg>
  )
}

// Directed "who reps whom" graph. Nodes are laid out on a circle. Each edge is
// a curved arrow from giver to receiver, thickness scaled to the reputation
// moved. Node radius scales to reputation received. The ranked lists below are
// the table-view companion (dataviz accessibility pass), so the graph never
// carries meaning by geometry alone.
function ReputationGraph({ edges }: { edges: ReputationEdge[] }) {
  const size = 460
  const cx = size / 2
  const cy = size / 2
  const radius = size / 2 - 70

  const layout = useMemo(() => {
    const received = new Map<string, number>()
    const given = new Map<string, number>()
    for (const e of edges) {
      received.set(e.target, (received.get(e.target) ?? 0) + e.amount)
      given.set(e.source, (given.get(e.source) ?? 0) + e.amount)
    }
    const weight = (n: string) =>
      Math.abs(received.get(n) ?? 0) + Math.abs(given.get(n) ?? 0)
    const nodes = Array.from(new Set([...received.keys(), ...given.keys()]))
      .sort((a, b) => weight(b) - weight(a))
      .slice(0, MAX_GRAPH_NODES)
    const nodeSet = new Set(nodes)
    const pos = new Map<string, { x: number; y: number; angle: number }>()
    nodes.forEach((n, i) => {
      const angle = (i / nodes.length) * Math.PI * 2 - Math.PI / 2
      pos.set(n, { x: cx + Math.cos(angle) * radius, y: cy + Math.sin(angle) * radius, angle })
    })
    const shown = edges.filter((e) => nodeSet.has(e.source) && nodeSet.has(e.target))
    const maxEdge = Math.max(...shown.map((e) => Math.abs(e.amount)), 1)
    const maxRecv = Math.max(...nodes.map((n) => Math.abs(received.get(n) ?? 0)), 1)
    return { nodes, pos, shown, maxEdge, received, maxRecv }
  }, [edges, cx, cy, radius])

  if (layout.nodes.length === 0) {
    return <p className="empty-note">No reputation transfers to graph yet.</p>
  }

  return (
    <svg
      className="rep-graph"
      viewBox={`0 0 ${size} ${size}`}
      preserveAspectRatio="xMidYMid meet"
      role="img"
      aria-label="Reputation transfer graph, who reps whom"
      data-testid="rep-graph"
    >
      <defs>
        <marker
          id="rep-arrow"
          viewBox="0 0 10 10"
          refX="9"
          refY="5"
          markerWidth="7"
          markerHeight="7"
          orient="auto-start-reverse"
        >
          <path d="M0,0 L10,5 L0,10 z" fill="var(--leaf-dim)" />
        </marker>
      </defs>
      {layout.shown.map((e) => {
        const a = layout.pos.get(e.source)!
        const b = layout.pos.get(e.target)!
        // Bow the edge toward the circle centre so reciprocal edges separate.
        const mx = (a.x + b.x) / 2
        const my = (a.y + b.y) / 2
        const qx = mx + (cx - mx) * 0.35
        const qy = my + (cy - my) * 0.35
        const w = 1 + (Math.abs(e.amount) / layout.maxEdge) * 6
        return (
          <path
            key={`${e.source}->${e.target}`}
            d={`M${a.x.toFixed(1)},${a.y.toFixed(1)} Q${qx.toFixed(1)},${qy.toFixed(1)} ${b.x.toFixed(1)},${b.y.toFixed(1)}`}
            fill="none"
            stroke="var(--leaf-dim)"
            strokeWidth={w}
            markerEnd="url(#rep-arrow)"
            data-testid="rep-edge"
          >
            <title>
              {e.source} → {e.target}: {e.amount > 0 ? "+" : ""}
              {formatCount(e.amount)} rep ({formatCount(e.count)}×)
            </title>
          </path>
        )
      })}
      {layout.nodes.map((n) => {
        const p = layout.pos.get(n)!
        const recv = Math.abs(layout.received.get(n) ?? 0)
        const r = 5 + (recv / layout.maxRecv) * 10
        // Push labels outward from the circle so they don't overlap the nodes.
        const lx = cx + Math.cos(p.angle) * (radius + 22)
        const ly = cy + Math.sin(p.angle) * (radius + 22)
        const anchor = Math.cos(p.angle) > 0.3 ? "start" : Math.cos(p.angle) < -0.3 ? "end" : "middle"
        return (
          <g key={n} data-testid="rep-node">
            <circle cx={p.x} cy={p.y} r={r} fill="var(--meteor)" stroke="var(--bg-deep)" strokeWidth="2">
              <title>
                {n}: {formatCount(recv)} reputation received
              </title>
            </circle>
            <text x={lx} y={ly + 4} textAnchor={anchor} className="rep-label">
              {n}
            </text>
          </g>
        )
      })}
    </svg>
  )
}

interface RankListProps {
  rows: Array<[string, number]>
  emptyNote: string
}

// Ranked bar list. This is the table-view companion for the charts. Labels are already
// redacted handles from the server, shown verbatim.
function RankList({ rows, emptyNote }: RankListProps) {
  const top = rows.slice(0, TOP_N)
  const max = Math.max(...top.map(([, n]) => Math.abs(n)), 1)
  if (top.length === 0) {
    return <p className="empty-note">{emptyNote}</p>
  }
  return (
    <ul className="rank-rows">
      {top.map(([name, n]) => (
        <li key={name}>
          <div className="rank-row" data-testid="rank-row">
            <span className="rank-name">{name}</span>
            <span className="rank-count">{formatCount(n)}</span>
            <span className="rank-bar" style={{ width: `${(Math.abs(n) / max) * 100}%` }} />
          </div>
        </li>
      ))}
    </ul>
  )
}

export default function Social() {
  // Refresh contract lives in freshness.ts, not here (eco-app#201).
  const socialPlane = useFreshData("social", fetchSocial)
  const surface = socialPlane.data
  const error = socialPlane.error


  const isEmpty =
    surface !== null &&
    surface.totalReputationTransfers === 0 &&
    surface.totalFirstLogins === 0 &&
    surface.totalPlayEvents === 0

  return (
    <Layout>
      <section className="hero hero-compact">
        <p className="hero-kicker">Community activity</p>
        <h1 className="hero-title">
          The <span className="accent">people</span> behind the world
        </h1>
        {surface && !isEmpty && (
          <p className="hero-pill" data-testid="social-pill">
            <span className="pulse-dot" aria-hidden="true" />
            {formatCount(surface.totalPlayEvents)} play events ·{" "}
            {formatCount(surface.totalReputationTransfers)} rep transfers ·{" "}
            {formatCount(surface.totalFirstLogins)} new arrivals
          </p>
        )}
        {surface?.redacted && (
          <p className="redaction-note" data-testid="redaction-note">
            Names are redacted to stable handles. Names in the clear are an operator-only mode.
          </p>
        )}
        {!surface && error && (
          <p className="hero-pill hero-pill-muted" data-testid="social-error">
            social surface unavailable right now
          </p>
        )}
        <FreshnessNote
          plane="social"
          loadedAt={socialPlane.loadedAt}
          refreshing={socialPlane.refreshing}
          refreshError={socialPlane.refreshError}
          onRefresh={socialPlane.refresh}
        />
      </section>

      {!surface && !error && <Loading label="Reading community activity..." testid="social-loading" />}

      {surface && isEmpty && (
        <section>
          <p className="empty-note" data-testid="social-empty">
            No community activity recorded on this server yet. Early in a cycle this is normal. Check
            back after a few days of play.
          </p>
        </section>
      )}

      {surface && !isEmpty && (
        <>
          <section>
            <h2 className="section-title">
              Reputation graph{" "}
              <span className="section-sub">(who reps whom, top {MAX_GRAPH_NODES} by volume)</span>
            </h2>
            <div className="rep-layout">
              <ReputationGraph edges={surface.reputationEdges} />
              <div className="rep-lists">
                <div>
                  <h3 className="section-title-sm">Most-repped</h3>
                  <RankList
                    rows={surface.topReputationReceivers}
                    emptyNote="No reputation received yet."
                  />
                </div>
                <div>
                  <h3 className="section-title-sm">Top givers</h3>
                  <RankList
                    rows={surface.topReputationGivers}
                    emptyNote="No reputation given yet."
                  />
                </div>
              </div>
            </div>
          </section>

          {surface.firstLoginsByDay.length > 0 && (
            <section>
              <h2 className="section-title">
                New arrivals over time <span className="section-sub">(first logins per day)</span>
              </h2>
              <VolumeChart
                points={surface.firstLoginsByDay}
                color="var(--meteor)"
                label="New arrivals"
                testid="arrivals-chart"
              />
            </section>
          )}

          {surface.warnings.length > 0 && (
            <section>
              <ul className="warn-list" data-testid="social-warnings">
                {surface.warnings.map((w) => (
                  <li key={w}>⚠ {w}</li>
                ))}
              </ul>
            </section>
          )}

          <section className="k-card-grid k-card-grid--condensed dir-cards">
            <Link className="k-card dir-card" to="/trade" data-testid="link-trade">
              <h3>Trade →</h3>
              <p>The material side: the market, the trade ledger, and money supply.</p>
            </Link>
            <Link className="k-card dir-card" to="/jobs" data-testid="link-jobs">
              <h3>Jobs →</h3>
              <p>Who can make what: professions, specialties, and every settler's skills.</p>
            </Link>
          </section>
        </>
      )}
    </Layout>
  )
}
