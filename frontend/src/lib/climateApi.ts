// Typed client for the climate snapshot (/preview/get_climate.json).
//
// The endpoint returns compute_climate_payload() from eco_mcp_app/climate.py:
// headline status + narrative, four atmosphere KPIs, the CO2 source/sink
// breakdown, the CO2-effects mechanic, and a plain-language explainer — the
// same data the in-game pollution-machine status panel surfaces.

export interface ClimateKpi {
  current: number | null
  dataset_name: string | null
  unit: string | null
  observation: ClimateObservation
  series: [number, number][]
}

export interface ClimateObservation {
  latest_game_time_seconds: number | null
  latest_game_day: number | null
  current_game_day: number
  interval_seconds: number | null
  lag_intervals: number | null
  freshness_state: "current" | "stale" | "unknown"
  freshness_reason:
    | "within_source_cadence"
    | "behind_current_game_day"
    | "unknown_cadence"
    | "no_sample"
}

export interface Co2Kpi extends ClimateKpi {
  change_pct: number | null
}

export interface SeaLevelKpi extends ClimateKpi {
  change_pct: number | null
  rate_per_day: number
}

export interface TemperatureKpi extends ClimateKpi {
  risen: number | null
  rate_per_day: number
}

export interface PollutionKpi extends ClimateKpi {
  source: string
  layer_summary: string | null
}

export interface SourceLine {
  lifetime: number
  per_day: number
}

export interface Breakdown {
  has_data: boolean
  pollution?: SourceLine
  animals?: SourceLine
  plants?: SourceLine
  net_per_day?: number
}

export interface EffectAxis {
  threshold_ppm: number
  headroom_ppm: number | null
  peak_drives_c?: number
  peak_drives_m?: number
  ppm_per_degree?: number
  ppm_per_meter?: number
  current_c?: number | null
  current_m?: number | null
  risen_c?: number | null
  risen_m?: number | null
}

export interface Effects {
  co2_now: number | null
  co2_peak: number | null
  min_floor_ppm: number
  at_floor: boolean
  // "live" when the telemetry mod's /api/v1/climate-settings endpoint supplied
  // the thresholds (real per-server values), "default" when it was absent and
  // we fell back to Eco's documented defaults. Drives the ruleset disclaimer.
  source: "live" | "default"
  temperature: EffectAxis
  sea_level: EffectAxis
  // Extra Eco climate knobs, present only when the server emits them.
  pollution_multiplier?: number
  max_co2_per_day_from_animals?: number
  min_co2_per_day_from_plants?: number
}

export interface EarthMatch {
  year: number
  ppm: number
  note: string
}

// One air-polluting device from the telemetry mod's /api/v1/polluters, as
// climate.py's _normalize_polluter shapes it. Every field but `type` and `name`
// can be null: the mod leaves a value null when reflection could not read it.
export interface Polluter {
  object_id: string | null
  type: string
  name: string
  owner: string | null
  owner_users: string[]
  position: { x: number; y: number; z: number } | null
  rate_per_hour: number | null
  rate_unit: string
  operating: boolean | null
  operating_efficiency: number | null
  total_tons: number | null
}

export interface PollutersBlock {
  // False when the server has no telemetry mod endpoint (or it was not ready),
  // which is different from a world that simply has no polluting devices.
  available: boolean
  devices: Polluter[]
  // Totals cover every device even when `devices` was cut down for an MCP caller.
  total_devices: number
  operating_devices: number
  total_rate_per_hour: number | null
  operating_rate_per_hour: number | null
  unknown_rate_count: number
  rate_unit: string | null
  // "per-object" is a rate the game reports for that device. "none" means no
  // device had a readable rate.
  rate_source: "per-object" | "none"
}

export interface ClimateSnapshot {
  server: { description: string; category: string | null; sourceUrl: string | null }
  days_elapsed: number
  admin_ok: boolean
  status: "stable" | "warming" | "critical" | "unknown"
  narrative: string
  co2: Co2Kpi
  sea_level: SeaLevelKpi
  pollution: PollutionKpi
  temperature: TemperatureKpi
  breakdown: Breakdown
  effects: Effects
  explainer: string[]
  earth_match: EarthMatch | null
  // Optional so a payload from before the device list shipped still parses.
  polluters?: PollutersBlock
  fetched_at_iso: string
}

async function getClimate(url: string, signal?: AbortSignal): Promise<ClimateSnapshot> {
  const resp = await fetch(url, { signal })
  if (!resp.ok) {
    throw new Error(`climate fetch failed: HTTP ${resp.status}`)
  }
  return (await resp.json()) as ClimateSnapshot
}

// The bounded payload: enough for the headline numbers, and the cut the home
// page pulse takes.
export function fetchClimate(signal?: AbortSignal): Promise<ClimateSnapshot> {
  return getClimate("/preview/get_climate.json", signal)
}

// limit=0: the pollution card renders every device. Without it the endpoint
// returns the bounded MCP default of 50 devices.
export function fetchClimateWithDevices(signal?: AbortSignal): Promise<ClimateSnapshot> {
  return getClimate("/preview/get_climate.json?limit=0", signal)
}
