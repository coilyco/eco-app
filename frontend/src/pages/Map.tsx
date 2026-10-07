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
  push("__unclassified", "Mountains and in-between land", UNCLASSIFIED_COLOR, snap.unclassifiedPercent)
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
      aria-label="How much of the world each biome and water covers"
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
        identified
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
  naturally_sparse: "naturally rare",
  insufficient: "not enough data",
  stale: "out of date",
  missing: "no data",
}

const FRESHNESS_LABEL: Record<SpeciesRisk["species"][number]["freshness"], string> = {
  current: "up to date",
  stale: "out of date",
  missing: "no data",
}

function pct(value: number | null): string {
  if (value === null) return "n/a"
  return `${value > 0 ? "+" : ""}${Math.round(value * 100)}%`
}

function observationWindow(seconds: number | null, samples: number): string {
  if (seconds === null) return `${samples} count${samples === 1 ? "" : "s"}`
  const hours = seconds / 3600
  const span = hours >= 24 ? `${(hours / 24).toFixed(1)} days` : `${hours.toFixed(1)} hours`
  return `${span} · ${samples} counts`
}

function SpeciesRiskSection({ risk }: { risk: SpeciesRisk }) {
  if (risk.sourceState === "unavailable") {
    return (
      <p className="empty-note" data-testid="species-risk-unavailable">
        Species at risk can't be shown. It needs species counts from the server's admin data, which isn't connected, so this page doesn't guess.
      </p>
    )
  }
  if (risk.species.length === 0) {
    return (
      <p className="empty-note" data-testid="species-risk-insufficient">
        There aren't enough species counts yet to tell which species are at risk.
      </p>
    )
  }

  return (
    <div data-testid="species-risk">
      <p className="intro">
        <span>
          {risk.threshold.description} A species with no counts, old counts, or too few counts is
          marked as not enough data. This table only shows counts. It can't change anything in the
          game.
        </span>
      </p>
      <p className={`hero-pill${risk.atRiskCount > 0 ? " hero-pill-warn" : ""}`}>
        <span className="pulse-dot" aria-hidden="true" />
        {formatCount(risk.atRiskCount)} species at risk · {formatCount(risk.species.length)} watched
      </p>
      <div className="ledger-scroll">
        <table tabIndex={0} className="ledger-table species-risk-table">
          <thead>
            <tr>
              <th>Species</th>
              <th>Status</th>
              <th className="num">Count now</th>
              <th className="num">Change this cycle</th>
              <th className="num">Recent change</th>
              <th>Counted over</th>
              <th>How recent</th>
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
                <td>{FRESHNESS_LABEL[row.freshness] ?? row.freshness}</td>
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
    return `${hours} game hour${hours === 1 ? "" : "s"}`
  }
  return `${Math.round(intervalSeconds)} game seconds`
}

function pollutionFreshness(snap: ClimateSnapshot): string {
  const observation = snap.pollution.observation
  if (snap.pollution.source === "worldlayers") {
    return "Ground pollution here is a backup number: the percentage from the world map's pollution layer. There is no reading time or update schedule for it."
  }
  if (observation.latest_game_day === null) {
    return "There is no ground pollution reading yet, so there is no way to tell how recent it is."
  }
  const latest = formatEventDay(observation.latest_game_day)
  if (observation.freshness_state === "stale") {
    const lag = observation.lag_intervals ?? 1
    return `Ground pollution data is out of date. The last reading was at ${latest}, and ${lag} update${lag === 1 ? " was" : "s were"} missed since then. Game time is now ${formatEventDay(observation.current_game_day)}.`
  }
  if (observation.freshness_state === "current" && observation.interval_seconds !== null) {
    return `Ground pollution was read at ${latest}. It updates every ${formatSourceCadence(observation.interval_seconds)}, so it is up to date for game time ${formatEventDay(observation.current_game_day)}.`
  }
  return `Ground pollution was read at ${latest}. How often it updates is unknown, so there is no way to tell if it is up to date.`
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
      ? "Climate risk is high. Talk it over together before making big changes to what gets produced."
      : snap.status === "warming"
        ? "The climate is getting warmer. Plan around that trend, and don't guess which machines are causing it."
        : snap.status === "stable"
          ? "Readings are steady for now. Keep watching them, since this is not an all-clear for good."
          : "Climate risk is unknown because some readings are missing."
  const guidance =
    net == null
      ? "It's not clear yet whether CO₂ is going up or down, so there is no advice on changing production."
      : net > 0
        ? "CO₂ is going up overall. Before agreeing together to cut back, weigh what is being produced now against what people need to trade."
        : "CO₂ is going down or holding steady. Watch the next few readings before changing production plans."

  return (
    <section data-testid="climate-coordination">
      <h2 className="section-title">Climate: what to do</h2>
      <p className="intro"><span><strong>Risk right now:</strong> {risk}</span></p>
      <p className="intro"><span><strong>What to do:</strong> {guidance} This page only shows information. It can't change anything in the game.</span></p>
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
        snap.co2.change_pct != null ? `${signed(snap.co2.change_pct)}% since this cycle began` : undefined,
    },
    {
      label: "Average temperature",
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
      detail: snap.pollution.source !== "none" ? `data from: ${snap.pollution.source}` : undefined,
    },
  ]

  const b = snap.breakdown
  const sources: ClimateStat[] =
    b?.has_data && b.pollution && b.animals && b.plants && b.net_per_day != null
      ? [
          {
            label: "From pollution",
            value: `${signedPpm(b.pollution.lifetime)} ppm`,
            detail: `${signed(b.pollution.per_day)} ppm a day`,
            tone: "add",
          },
          {
            label: "From animals",
            value: `${signedPpm(b.animals.lifetime)} ppm`,
            detail: `${signed(b.animals.per_day)} ppm a day`,
            tone: "add",
          },
          {
            label: "From plants",
            value: `${signedPpm(b.plants.lifetime)} ppm`,
            detail: `${signed(b.plants.per_day)} ppm a day`,
            tone: "remove",
          },
          {
            label: "Overall change",
            value: `${signed(b.net_per_day)} ppm a day`,
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
        Climate data fetched {formatFetchedAt(snap.fetched_at_iso)}. The server may reuse it for up to 60 seconds.
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
              Where the air's CO₂ comes from and where it goes. The big number is the total so far,
              the small one is how much it adds or takes away each day. Pollution and animals add
              CO₂, plants take it away. ppm means parts per million.
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
// The map itself: base preview plus per-biome highlight rasters (#82). Deed
// polygons never lined up (eco-app#8349) and the payload no longer carries them.
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
        <img className="map-base" src={map.gifDataUri} alt="Map of the Eco world" draggable={false} />
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
        world size {map.worldDim.x} × {map.worldDim.z}
      </p>
    </div>
  )
}

export default function MapPage() {
  const [hoveredBiome, setHoveredBiome] = useState<string | null>(null)
  const [pageLoadedAt] = useState(() => new Date())

  // Three independent planes with three different refresh contracts
  // (eco-app#201). Climate advances with the simulation and polls; the map
  // rasters change with the terrain and do not; species populations are
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
              ? `Most like ${topMatch.name} · ${Math.round(snap.classifiedPercent)}% of the map identified`
              : `${Math.round(snap.classifiedPercent)}% of the map is identified`}
          </p>
        )}
        {!snap && !loading && (
          <p className="hero-pill hero-pill-muted" data-testid="map-error">
            world info unavailable right now
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
                <span>Point at a biome name below, or tab to it, to light it up on the map.</span>
              </p>
              <WorldMap map={map} hoveredBiome={hoveredBiome} />
            </section>
          ) : (
            <section>
              <h2 className="section-title">World map</h2>
              <p className="empty-note" data-testid="map-unavailable">
                The world map picture isn't available right now.
              </p>
            </section>
          )}

          {snap && (
            <section>
              <h2 className="section-title">Biomes and water</h2>
              <p className="intro">
                <span>
                  How much of the map each biome and water covers. Only mountains and in-between
                  land ({Math.round(snap.unclassifiedPercent)}%) are left unidentified.
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
                    <span className="eco-legend-label">Mountains and in-between land</span>
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
              <h3 className="subsection-title">Change this cycle</h3>
              {!snap.adminAvailable ? (
                <p className="empty-note" data-testid="eco-drift-admin">
                  Species changes need the server's admin data. Once a site admin sets the API key,
                  this shows which species are booming and which are crashing.
                </p>
              ) : snap.drift.speciesWithDrift === 0 ? (
                <p className="empty-note" data-testid="eco-drift-minimal">
                  Little change so far. {snap.drift.speciesSeen} species watched, and none is up or
                  down overall yet this cycle.
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
