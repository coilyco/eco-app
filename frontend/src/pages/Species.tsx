import { Link, useSearchParams } from "react-router-dom"
import ChartFrame from "../components/ChartFrame"
import FreshnessNote from "../components/FreshnessNote"
import Layout from "../components/Layout"
import Loading from "../components/Loading"
import { formatCount } from "../lib/format"
import { fetchSpecies, type SpeciesPopulationSample, type SpeciesProfile } from "../lib/speciesApi"
import { useFreshData } from "../lib/useFreshData"

function PopulationCurve({ samples }: { samples: SpeciesPopulationSample[] }) {
  if (samples.length < 2) {
    return <p className="empty-note">Not enough population samples to draw a curve.</p>
  }
  const width = 720
  const height = 220
  const pad = 30
  const days = samples.map((sample) => sample.day)
  const values = samples.map((sample) => sample.value)
  const minDay = Math.min(...days)
  const maxDay = Math.max(...days)
  const minValue = Math.min(...values)
  const maxValue = Math.max(...values)
  const daySpan = maxDay - minDay || 1
  const valueSpan = maxValue - minValue || 1
  const x = (day: number) => pad + ((day - minDay) / daySpan) * (width - pad * 2)
  const y = (value: number) => height - pad - ((value - minValue) / valueSpan) * (height - pad * 2)
  const points = samples.map((sample) => `${x(sample.day)},${y(sample.value)}`).join(" ")

  return (
    <ChartFrame
      above={[`high ${formatCount(maxValue)}`, `low ${formatCount(minValue)}`]}
      start={`day ${minDay.toFixed(1)}`}
      end={`day ${maxDay.toFixed(1)}`}
    >
    <svg
      className="price-chart"
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label="Population curve across the current Eco cycle"
      data-testid="species-population-curve"
    >
      <polyline points={points} fill="none" stroke="var(--leaf)" strokeWidth="3" />
      {samples.map((sample) => (
        <circle key={`${sample.day}-${sample.value}`} cx={x(sample.day)} cy={y(sample.value)} r="3">
          <title>Day {sample.day.toFixed(1)}: {formatCount(sample.value)}</title>
        </circle>
      ))}
    </svg>
    </ChartFrame>
  )
}

export default function Species() {
  const [params] = useSearchParams()
  const name = params.get("name") ?? ""

  // Refresh contract lives in freshness.ts, not here (eco-app#201). The
  // fetcher carries the name it answered for so a slow response for the
  // previous species can never be shown against the current one — the
  // stale-result guard this page already had.
  const speciesPlane = useFreshData(
    "species",
    async (signal): Promise<{ name: string; profile: SpeciesProfile | null }> => {
      if (!name) return { name, profile: null }
      try {
        return { name, profile: await fetchSpecies(name, signal) }
      } catch {
        // A miss is a normal state here, not a page error.
        return { name, profile: null }
      }
    },
    [name],
  )
  const result = speciesPlane.data

  const loaded = !name || result?.name === name
  const profile = result?.name === name ? result.profile : null

  return (
    <Layout>
      <section className="hero hero-compact">
        <p className="hero-kicker"><Link className="linklike" to="/map">← World</Link></p>
        <h1 className="hero-title">{profile?.name ?? "Species profile"}</h1>
        {profile?.populationLatest !== null && profile?.populationLatest !== undefined && (
          <p className="hero-pill">
            <span className="pulse-dot" aria-hidden="true" />
            {formatCount(profile.populationLatest)} alive now · {profile.populationDelta !== null && profile.populationDelta >= 0 ? "+" : ""}
            {profile.populationDelta === null ? "change unknown" : formatCount(profile.populationDelta)} this cycle
          </p>
        )}
        <FreshnessNote plane="species" loadedAt={speciesPlane.loadedAt} />
      </section>

      {!loaded && <Loading label="Reading the population curve…" />}
      {loaded && !profile && (
        <p className="empty-note" data-testid="species-unavailable">
          {name ? "Species profile unavailable right now." : "Choose a species from the World biodiversity table."}
        </p>
      )}
      {profile && (
        <section>
          <h2 className="section-title">Population curve</h2>
          <PopulationCurve samples={profile.population} />
          {profile.error && <p className="empty-note">{profile.error}</p>}
        </section>
      )}
    </Layout>
  )
}
