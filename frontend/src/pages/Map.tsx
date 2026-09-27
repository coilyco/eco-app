import { useMemo, useState } from "react"
import { Link } from "react-router-dom"
import FreshnessNote from "../components/FreshnessNote"
import Layout from "../components/Layout"
import Loading from "../components/Loading"
import { type ClimateSnapshot, fetchClimate } from "../lib/climateApi"
import { useFreshData } from "../lib/useFreshData"
import {
  type DriftRow,
  type EcoregionSnapshot,
  fetchEcoregion,
  type SpeciesRisk,
  type SpeciesRiskState,
} from "../lib/ecoregionApi"
import {
  formatCount,
  formatEventDay,
  formatFetchedAt,
  prettifyEcoName,
} from "../lib/format"
import { type MapPayload, fetchMap } from "../lib/mapApi"

// ---------------------------------------------------------------------------
// Donut geometry — shared with the in-chat MCP ecoregion card so the two
// surfaces read identically. A stroke-dashed circle draws each slice.
// ---------------------------------------------------------------------------
const DONUT_R = 40
const DONUT_C = 2 * Math.PI * DONUT_R
const UNCLASSIFIED_COLOR = "#3a4a40"

interface Slice {
  key: string
  label: string
  color: string
  percent: number
  length: number
  offset: number
}

// Raw biome percents plus the unclassified remainder, so the donut sums to a
// full 100% of world area. Water slices (eco-app#82) already ride in
// snap.biomes, so they slot in alongside the named biomes automatically.
function buildSlices(snap: EcoregionSnapshot): Slice[] {
  const slices: Slice[] = []
  let cursor = 0
  const push = (key: string, label: string, color: string, percent: number) => {
    if (percent <= 0) return
    const length = DONUT_C * (percent / 100)
    slices.push({ key, label, color, percent, length, offset: -DONUT_C * (cursor / 100) })
    cursor += percent
  }
  for (const b of snap.biomes) push(b.name, b.display, b.color, b.percent)
  push("__unclassified", "Unclassified / mixed terrain", UNCLASSIFIED_COLOR, snap.unclassifiedPercent)
  return slices
}

function Donut({ snap }: { snap: EcoregionSnapshot }) {
  const slices = buildSlices(snap)
  return (
    <svg
      className="eco-donut"
      viewBox="-50 -50 100 100"
      width="200"
      height="200"
      role="img"
      aria-label="Biome and water composition of the world"
    >
      {slices.map((s) => (
        <circle
          key={s.key}
          cx="0"
          cy="0"
          r={DONUT_R}
          fill="none"
          stroke={s.color}
          strokeWidth="14"
          strokeDasharray={`${s.length.toFixed(3)} ${(DONUT_C - s.length).toFixed(3)}`}
          strokeDashoffset={s.offset.toFixed(3)}
          transform="rotate(-90)"
        >
          <title>
            {s.label}: {Math.round(s.percent)}%
          </title>
        </circle>
      ))}
      <text x="0" y="-2" textAnchor="middle" className="eco-donut-num">
        {Math.round(snap.classifiedPercent)}%
      </text>
      <text x="0" y="12" textAnchor="middle" className="eco-donut-sub">
        classified
      </text>
    </svg>
  )
}

// Boom/bust column, unchanged from the old /ecoregion page.
function DriftColumn({
  title,
  tone,
  rows,
}: {
  title: string
  tone: "add" | "remove"
  rows: DriftRow[]
}) {
  const magnitude = (d: DriftRow) => (d.deltaRel === null ? 1 : Math.abs(d.deltaRel))
  const max = Math.max(...rows.map(magnitude), 1e-9)
  return (
    <div className="eco-drift-col">
      <h3 className={`eco-drift-head eco-drift-${tone}`}>{title}</h3>
      {rows.length === 0 ? (
        <p className="empty-note">No species {tone === "add" ? "trending up" : "trending down"}.</p>
      ) : (
        <ul className="rank-rows">
          {rows.map((d) => (
            <li key={d.name}>
              <div className="rank-row" data-testid={`drift-${tone}-row`}>
                <span className="rank-name">{d.name}</span>
                <span className={`rank-count eco-delta-${tone}`}>
                  {d.deltaRel === null
                    ? "new"
                    : `${d.deltaRel > 0 ? "+" : ""}${Math.round(d.deltaRel * 100)}%`}
                </span>
                <span className="rank-detail">
                  {formatCount(Math.round(d.first))} → {formatCount(Math.round(d.latest))}
                </span>
                <span
                  className={`rank-bar eco-bar-${tone}`}
                  style={{ width: `${(magnitude(d) / max) * 100}%` }}
                />
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

const RISK_STATE_LABEL: Record<SpeciesRiskState, string> = {
  at_risk: "at risk",
  declining: "declining",
  recovering: "recovering",
  growing: "growing",
  stable: "stable",
  naturally_sparse: "naturally sparse",
  insufficient: "insufficient data",
  stale: "stale",
  missing: "missing",
}

function pct(value: number | null): string {
  if (value === null) return "n/a"
  return `${value > 0 ? "+" : ""}${Math.round(value * 100)}%`
}

function observationWindow(seconds: number | null, samples: number): string {
  if (seconds === null) return `${samples} sample${samples === 1 ? "" : "s"}`
  const hours = seconds / 3600
  const span = hours >= 24 ? `${(hours / 24).toFixed(1)} days` : `${hours.toFixed(1)} hours`
  return `${span} · ${samples} samples`
}

function SpeciesRiskSection({ risk }: { risk: SpeciesRisk }) {
  if (risk.sourceState === "unavailable") {
    return (
      <p className="empty-note" data-testid="species-risk-unavailable">
        At-risk species need the admin population exporter. No health claim is made without it.
      </p>
    )
  }
  if (risk.species.length === 0) {
    return (
      <p className="empty-note" data-testid="species-risk-insufficient">
        Population evidence is unavailable or too thin to classify species risk.
      </p>
    )
  }

  return (
    <div data-testid="species-risk">
      <p className="intro">
        <span>
          {risk.threshold.description} Missing, stale, and thin series remain explicitly
          insufficient. This is read-only coordination evidence, not an ecological control.
        </span>
      </p>
      <p className={`hero-pill${risk.atRiskCount > 0 ? " hero-pill-warn" : ""}`}>
        <span className="pulse-dot" aria-hidden="true" />
        {formatCount(risk.atRiskCount)} at-risk species · {formatCount(risk.species.length)} tracked
      </p>
      <div className="ledger-scroll">
        <table tabIndex={0} className="ledger-table species-risk-table">
          <thead>
            <tr>
              <th>Species</th>
              <th>Status</th>
              <th className="num">Current</th>
              <th className="num">Cycle change</th>
              <th className="num">Recent</th>
              <th>Observation</th>
              <th>Freshness</th>
            </tr>
          </thead>
          <tbody>
            {risk.species.map((row) => (
              <tr key={row.name} data-testid={`species-risk-${row.state}`}>
                <td>
                  <Link className="linklike" to={`/species?name=${encodeURIComponent(row.name)}`}>
                    {prettifyEcoName(row.name)}
                  </Link>
                  <span className="species-risk-reason">{row.reason}</span>
                </td>
                <td>
                  <span className={`species-state species-state-${row.state}`}>
                    {row.warning ? "⚠ " : ""}{RISK_STATE_LABEL[row.state]}
                  </span>
                </td>
                <td className="num">{row.current === null ? "n/a" : formatCount(row.current)}</td>
                <td className="num">
                  {row.changeAbs === null
                    ? "n/a"
                    : `${row.changeAbs > 0 ? "+" : ""}${formatCount(row.changeAbs)} (${pct(row.changePct)})`}
                </td>
                <td className="num">{pct(row.recentChangePct)}</td>
                <td>{observationWindow(row.observationSeconds, row.sampleCount)}</td>
                <td>{row.freshness}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Atmosphere & climate — folded in from the former /climate page as the world
// page's environmental overlay (eco-app#90). The map shows what the world *is*;
// this shows what the air is doing to it. Same CO2 / temperature / sea-level
// read and CO2 source/sink breakdown, condensed to sit inside the world page.
// ---------------------------------------------------------------------------

// Signed integer ppm with thousands separators, e.g. "+12,687" / "−28,989".
function signedPpm(n: number): string {
  const sign = n > 0 ? "+" : n < 0 ? "−" : ""
  return `${sign}${new Intl.NumberFormat("en-US").format(Math.round(Math.abs(n)))}`
}

function signed(n: number, digits = 2): string {
  const sign = n > 0 ? "+" : n < 0 ? "−" : ""
  return `${sign}${Math.abs(n).toFixed(digits)}`
}

function formatClimateValue(value: number, unit: string | null): string {
  const formatted = value.toFixed(1)
  if (unit === "%") return `${formatted}%`
  return unit ? `${formatted} ${unit}` : formatted
}

function formatSourceCadence(intervalSeconds: number): string {
  const days = intervalSeconds / 86400
  if (Number.isInteger(days) && days >= 1) {
    return `${days} game day${days === 1 ? "" : "s"}`
  }
  const hours = intervalSeconds / 3600
  if (Number.isInteger(hours) && hours >= 1) {
    return `${hours} source hour${hours === 1 ? "" : "s"}`
  }
  return `${Math.round(intervalSeconds)} source seconds`
}

function pollutionFreshness(snap: ClimateSnapshot): string {
  const observation = snap.pollution.observation
  if (snap.pollution.source === "worldlayers") {
    return "Ground pollution uses the world-layer percentage fallback. A source-series observation and cadence are unavailable."
  }
  if (observation.latest_game_day === null) {
    return "No ground-pollution source observation is available, so source freshness is unknown."
  }
  const latest = formatEventDay(observation.latest_game_day)
  if (observation.freshness_state === "stale") {
    const lag = observation.lag_intervals ?? 1
    return `Ground pollution data is stale. The source was last observed at ${latest}, ${lag} cadence${lag === 1 ? "" : "s"} behind current game time ${formatEventDay(observation.current_game_day)}.`
  }
  if (observation.freshness_state === "current" && observation.interval_seconds !== null) {
    return `Ground pollution was observed at ${latest} on a ${formatSourceCadence(observation.interval_seconds)} cadence. It is current for game time ${formatEventDay(observation.current_game_day)}.`
  }
  return `Ground pollution was observed at ${latest}. The source cadence is unavailable, so source freshness is unknown.`
}

interface ClimateStat {
  label: string
  value: string
  detail?: string
  tone?: "add" | "remove"
}

function ClimateCoordination({ snap }: { snap: ClimateSnapshot }) {
  const net = snap.breakdown.net_per_day
  const risk =
    snap.status === "critical"
      ? "Climate risk is elevated. Current measurements warrant a shared check before changing major production plans."
      : snap.status === "warming"
        ? "Climate is trending warmer. Coordinate around the observed direction, not assumed machine-level attribution."
        : snap.status === "stable"
          ? "Current readings are stable. Continue watching the observed trend rather than treating this as a permanent all-clear."
          : "Climate risk is unknown because the available readings are incomplete."
  const guidance =
    net == null
      ? "No net CO₂ direction is available, so this surface cannot recommend a production change."
      : net > 0
        ? "The observed CO₂ balance is rising. Compare active production and trade needs before coordinating voluntary reductions."
        : "The observed CO₂ balance is falling or steady. Keep watching the next snapshots before changing production plans."

  return (
    <section data-testid="climate-coordination">
      <h2 className="section-title">Climate coordination</h2>
      <p className="intro"><span><strong>Observed risk:</strong> {risk}</span></p>
      <p className="intro"><span><strong>Guidance:</strong> {guidance} This is read-only decision context, not a control panel.</span></p>
      <p className="gap-who">
        <Link className="linklike" to="/crafting">Crafting activity</Link>{" · "}
        <Link className="linklike" to="/trade">Trade and supply</Link>{" · "}
        <Link className="linklike" to="/jobs">Available specialties</Link>
      </p>
    </section>
  )
}

function ClimateSection({ snap, pageLoadedAt }: { snap: ClimateSnapshot; pageLoadedAt: Date }) {
  const atmosphere: ClimateStat[] = [
    {
      label: "CO₂",
      value: snap.co2.current != null ? `${Math.round(snap.co2.current)} ppm` : "—",
      detail:
        snap.co2.change_pct != null ? `${signed(snap.co2.change_pct)}% since cycle start` : undefined,
    },
    {
      label: "Avg temperature",
      value: snap.temperature.current != null ? `${snap.temperature.current.toFixed(1)} °C` : "—",
      detail:
        snap.temperature.risen != null && snap.temperature.risen !== 0
          ? `${signed(snap.temperature.risen)} °C this cycle`
          : undefined,
    },
    {
      label: "Sea level",
      value: snap.sea_level.current != null ? `${snap.sea_level.current.toFixed(2)} m` : "—",
      detail:
        snap.effects.sea_level.risen_m != null && snap.effects.sea_level.risen_m !== 0
          ? `${signed(snap.effects.sea_level.risen_m)} m this cycle`
          : undefined,
    },
    {
      label: "Ground pollution",
      value:
        snap.pollution.current != null
          ? formatClimateValue(snap.pollution.current, snap.pollution.unit)
          : "—",
      detail: snap.pollution.source !== "none" ? `source: ${snap.pollution.source}` : undefined,
    },
  ]

  const b = snap.breakdown
  const sources: ClimateStat[] =
    b?.has_data && b.pollution && b.animals && b.plants && b.net_per_day != null
      ? [
          {
            label: "From pollution",
            value: `${signedPpm(b.pollution.lifetime)} ppm`,
            detail: `${signed(b.pollution.per_day)} ppm/day`,
            tone: "add",
          },
          {
            label: "From animals",
            value: `${signedPpm(b.animals.lifetime)} ppm`,
            detail: `${signed(b.animals.per_day)} ppm/day`,
            tone: "add",
          },
          {
            label: "From plants",
            value: `${signedPpm(b.plants.lifetime)} ppm`,
            detail: `${signed(b.plants.per_day)} ppm/day`,
            tone: "remove",
          },
          {
            label: "Net change",
            value: `${signed(b.net_per_day)} ppm/day`,
            detail: b.net_per_day < 0 ? "CO₂ falling" : b.net_per_day > 0 ? "CO₂ rising" : "steady",
            tone: b.net_per_day <= 0 ? "remove" : "add",
          },
        ]
      : []

  const warnTone = snap.status !== "stable" && snap.status !== "unknown"

  return (
    <section data-testid="climate">
      <h2 className="section-title">Atmosphere &amp; climate</h2>
      <p className={`hero-pill${warnTone ? " hero-pill-warn" : ""}`} data-testid="climate-pill">
        <span className="pulse-dot" aria-hidden="true" />
        {snap.narrative}
      </p>
      <p className="gap-who" data-testid="climate-freshness" title={snap.fetched_at_iso}>
        Snapshot fetched {formatFetchedAt(snap.fetched_at_iso)}. The backend may reuse it for up to 60 seconds.
        This page loaded at {pageLoadedAt.toLocaleTimeString("en-US", { timeZone: "UTC", hour: "2-digit", minute: "2-digit" })} UTC.
      </p>
      <p
        className={
          snap.pollution.observation.freshness_state === "stale"
            ? "hero-pill hero-pill-warn"
            : "gap-who"
        }
        data-testid="pollution-source-freshness"
      >
        {pollutionFreshness(snap)}
      </p>
      <div className="stats">
        {atmosphere.map((s) => (
          <div className="stat" key={s.label}>
            <p className="stat-value">{s.value}</p>
            <p className="stat-label">{s.label}</p>
            {s.detail && <p className="stat-detail">{s.detail}</p>}
          </div>
        ))}
      </div>
      {sources.length > 0 && (
        <>
          <p className="intro">
            <span>
              Where the atmosphere's CO₂ comes from and goes — lifetime totals with each source's
              current daily push. Pollution and animals add CO₂, plants remove it.
            </span>
          </p>
          <div className="stats">
            {sources.map((s) => (
              <div className={`stat stat-${s.tone}`} key={s.label}>
                <p className="stat-value">{s.value}</p>
                <p className="stat-label">{s.label}</p>
                {s.detail && <p className="stat-detail">{s.detail}</p>}
              </div>
            ))}
          </div>
        </>
      )}
      {snap.explainer.length > 0 && (
        <ul className="explainer" data-testid="climate-explainer">
          {snap.explainer.map((line, i) => (
            <li key={i}>{line}</li>
          ))}
        </ul>
      )}
      <ClimateCoordination snap={snap} />
    </section>
  )
}

// ---------------------------------------------------------------------------
// The map itself: base preview plus per-biome highlight rasters (#82). The
// owner-coloured deed polygons were removed at Kai's request, since the overlay
// never lined up (eco-app#8349). The payload still carries them for MCP.
// ---------------------------------------------------------------------------
function WorldMap({
  map,
  hoveredBiome,
}: {
  map: MapPayload
  hoveredBiome: string | null
}) {
  return (
    <div className="map-figure">
      <div className="map-frame" data-testid="map-frame" style={{ aspectRatio: "1 / 1" }}>
        <img className="map-base" src={map.gifDataUri} alt="Eco world preview" draggable={false} />
        {map.pollutionDataUri && (
          <img className="map-pollution" src={map.pollutionDataUri} alt="" aria-hidden="true" />
        )}
        {/* Per-biome highlight rasters — invisible until their biome is hovered. */}
        {map.biomeLayers.map((b) => (
          <img
            key={b.name}
            className="map-biome"
            src={b.dataUri}
            alt=""
            aria-hidden="true"
            data-testid={`map-biome-${b.name}`}
            style={{ opacity: hoveredBiome === b.name ? 0.92 : 0 }}
          />
        ))}
      </div>
      <p className="map-meta" data-testid="map-meta">
        {map.deedCount} deed{map.deedCount === 1 ? "" : "s"} // {map.ownerCount} owner
        {map.ownerCount === 1 ? "" : "s"} // {map.worldDim.x} × {map.worldDim.z}
      </p>
    </div>
  )
}

export default function MapPage() {
  const [hoveredBiome, setHoveredBiome] = useState<string | null>(null)
  const [pageLoadedAt] = useState(() => new Date())

  // Three independent planes with three different refresh contracts
  // (eco-app#201). Climate advances with the simulation and polls; deed
  // polygons change on a land transfer and do not; species populations are
  // sampled slowly and refresh on demand. A failure in one degrades that
  // section rather than the page.
  const region = useFreshData("region", fetchEcoregion)
  const mapPlane = useFreshData("map", fetchMap)
  const climatePlane = useFreshData("climate", fetchClimate)

  const snap = region.data
  const map = mapPlane.data
  const climate = climatePlane.data
  const loading = region.loading && mapPlane.loading && climatePlane.loading

  // Which biomes can actually highlight on the map (have a fetched raster).
  const highlightable = useMemo(
    () => new Set((map?.biomeLayers ?? []).map((b) => b.name)),
    [map],
  )

  const topMatch = snap?.ecoregionMatches[0]
  return (
    <Layout>
      {/* One heading + the live pill as the single intro line (eco-app#97). */}
      <section className="hero hero-compact">
        <h1 className="hero-title">World</h1>
        {snap && (
          <p className="hero-pill" data-testid="map-pill">
            <span className="pulse-dot" aria-hidden="true" />
            {topMatch
              ? `Closest to ${topMatch.name} · ${Math.round(snap.classifiedPercent)}% classified`
              : `${Math.round(snap.classifiedPercent)}% of the map is classified`}
          </p>
        )}
        {!snap && !loading && (
          <p className="hero-pill hero-pill-muted" data-testid="map-error">
            world snapshot unavailable right now
          </p>
        )}
            <FreshnessNote
          plane="climate"
          loadedAt={climatePlane.loadedAt}
          refreshing={climatePlane.refreshing}
          refreshError={climatePlane.refreshError}
          onRefresh={climatePlane.refresh}
          observedAtISO={climate?.fetched_at_iso ?? null}
        />
        <FreshnessNote
          plane="region"
          loadedAt={region.loadedAt}
          refreshing={region.refreshing}
          refreshError={region.refreshError}
          onRefresh={region.refresh}
        />
        </section>

      {loading && <Loading label="Reading the world map, biomes, climate, and biodiversity…" />}

      {!loading && (
        <>
          {map ? (
            <section>
              <h2 className="section-title">World map</h2>
              <p className="intro">
                <span>Hover a biome below to light up where it sits.</span>
              </p>
              <WorldMap map={map} hoveredBiome={hoveredBiome} />
            </section>
          ) : (
            <section>
              <h2 className="section-title">World map</h2>
              <p className="empty-note" data-testid="map-unavailable">
                The world preview is unavailable right now.
              </p>
            </section>
          )}

          {snap && (
            <section>
              <h2 className="section-title">Biome &amp; water composition</h2>
              <p className="intro">
                <span>
                  Share of the whole map covered by each biome and by water — only genuine mountain
                  and transitional terrain ({Math.round(snap.unclassifiedPercent)}%) is left
                  unclassified.
                </span>
              </p>
              <div className="eco-donut-row">
                <Donut snap={snap} />
                <ul className="eco-legend" data-testid="eco-legend">
                  {snap.biomes
                    .filter((b) => b.percent > 0)
                    .map((b) => {
                      const canHighlight = highlightable.has(b.name)
                      return (
                        <li
                          key={b.name}
                          className={canHighlight ? "eco-legend-hoverable" : undefined}
                          data-testid={`biome-legend-${b.name}`}
                          onMouseEnter={() => canHighlight && setHoveredBiome(b.name)}
                          onMouseLeave={() => setHoveredBiome((h) => (h === b.name ? null : h))}
                          onFocus={() => canHighlight && setHoveredBiome(b.name)}
                          onBlur={() => setHoveredBiome((h) => (h === b.name ? null : h))}
                          tabIndex={canHighlight ? 0 : undefined}
                        >
                          <span className="eco-swatch" style={{ background: b.color }} />
                          <span className="eco-legend-label">{b.display}</span>
                          <span className="eco-legend-pct">{Math.round(b.percent)}%</span>
                        </li>
                      )
                    })}
                  <li>
                    <span className="eco-swatch" style={{ background: UNCLASSIFIED_COLOR }} />
                    <span className="eco-legend-label">Unclassified / mixed terrain</span>
                    <span className="eco-legend-pct">{Math.round(snap.unclassifiedPercent)}%</span>
                  </li>
                </ul>
              </div>
              {highlightable.size > 0 && (
                <p className="hint-line" data-testid="biome-hint">
                  Hover a highlighted biome name to see where it is on the map above.
                </p>
              )}
            </section>
          )}

          {/* Climate as the environmental overlay on the world's physical
              composition (eco-app#90) — sits with the biomes it acts on, not as
              a tacked-on trailer. */}
          {climate && <ClimateSection snap={climate} pageLoadedAt={pageLoadedAt} />}

          {snap && (
            <section>
              <h2 className="section-title">Biodiversity status</h2>
              <SpeciesRiskSection risk={snap.speciesRisk} />
              <h3 className="subsection-title">Cycle drift</h3>
              {!snap.adminAvailable ? (
                <p className="empty-note" data-testid="eco-drift-admin">
                  Population drift needs the server's admin exporter — configure the API key to see
                  which species are booming and busting.
                </p>
              ) : snap.drift.speciesWithDrift === 0 ? (
                <p className="empty-note" data-testid="eco-drift-minimal">
                  Drift minimal so far — tracked {snap.drift.speciesSeen} species with no net change
                  yet this cycle.
                </p>
              ) : (
                <div className="eco-drift" data-testid="eco-drift">
                  <DriftColumn title="Boom" tone="add" rows={snap.drift.boom} />
                  <DriftColumn title="Bust" tone="remove" rows={snap.drift.bust} />
                </div>
              )}
            </section>
          )}

        </>
      )}
    </Layout>
  )
}
