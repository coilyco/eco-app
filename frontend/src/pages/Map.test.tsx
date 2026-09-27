import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { afterEach, describe, expect, it, vi } from "vitest"
import type { ClimateObservation, ClimateSnapshot } from "../lib/climateApi"
import type { EcoregionSnapshot, SpeciesRiskRow, SpeciesRiskState } from "../lib/ecoregionApi"
import type { MapPayload } from "../lib/mapApi"
import MapPage from "./Map"

function riskRow(name: string, state: SpeciesRiskState, warning = false): SpeciesRiskRow {
  const missing = state === "missing"
  return {
    name,
    state,
    warning,
    reason: `${state} evidence`,
    current: missing ? null : 40,
    changeAbs: missing ? null : -60,
    changePct: missing ? null : -0.6,
    recentChangePct: missing ? null : -0.2,
    observedPeak: missing ? null : 100,
    firstTime: missing ? null : 0,
    latestTime: missing ? null : 6000,
    recentFromTime: missing ? null : 4000,
    observationSeconds: missing ? null : 6000,
    sampleCount: missing ? 0 : 4,
    freshness: state === "stale" ? "stale" : missing ? "missing" : "current",
  }
}

const SNAP: EcoregionSnapshot = {
  view: "eco_ecoregion",
  sourceUrl: "http://eco.example.com:3001/info",
  biomes: [
    { name: "OceanBiome", display: "Ocean", percent: 13, sharePercent: 33, color: "#4a9cb8" },
    { name: "GrasslandBiome", display: "Grassland", percent: 7, sharePercent: 18, color: "#a5d14a" },
    { name: "CoastalWater", display: "Coastal & shallow sea", percent: 36, sharePercent: 0, color: "#5fb0cf", isWater: true },
    { name: "FreshWater", display: "Fresh water", percent: 5, sharePercent: 0, color: "#7fc8bf", isWater: true },
  ],
  unclassifiedPercent: 39,
  rawSumPercent: 20,
  classifiedPercent: 61,
  ecoregionMatches: [
    { name: "Indo-Pacific archipelago", description: "islands and warm seas", similarity: 0.83 },
  ],
  drift: {
    boom: [{ name: "Deer", first: 100, latest: 200, deltaRel: 1.0, fromZero: false }],
    bust: [{ name: "Wolf", first: 50, latest: 25, deltaRel: -0.5, fromZero: false }],
    speciesSeen: 2,
    speciesWithDrift: 2,
  },
  speciesRisk: {
    sourceState: "available",
    threshold: {
      currentPeakRatio: 0.25,
      cycleDeclinePct: -0.3,
      recentDeclinePct: -0.15,
      minSamples: 4,
      minObservationSeconds: 1800,
      staleLagSeconds: 1800,
      description: "Relative evidence threshold.",
    },
    counts: { at_risk: 1, stable: 1, recovering: 1, naturally_sparse: 1, missing: 1, stale: 1 },
    atRiskCount: 1,
    species: [
      riskRow("WolfSpecies", "at_risk", true),
      riskRow("DeerSpecies", "stable"),
      riskRow("BisonSpecies", "recovering"),
      riskRow("FoxSpecies", "naturally_sparse"),
      riskRow("OtterSpecies", "missing"),
      riskRow("ElkSpecies", "stale"),
    ],
  },
  adminAvailable: true,
}

const WORLD = {
  view: "world",
  fetchedAtISO: "2026-06-12T13:00:00+00:00",
  sourceBaseUrl: "http://x:3001",
  totalEvents: 421,
  perActionCounts: { ConstructOrDeconstruct: 300 },
  categories: [{ key: "construction", label: "Construction", events: 300, volume: 5400 }],
  categoryKeys: ["construction"],
  timeline: [{ day: 0, counts: { construction: 300 } }],
  byCitizen: [["coilysiren", 210]],
  byPolluter: [["coilysiren", 21]],
  // Touch counts (#82) — small integers, not runaway summed Count.
  byObject: [
    ["DirtRampItem", 42],
    ["StoneItem", 30],
  ],
  hotspots: [
    { x: 384, z: 448, events: 160 },
    { x: 512, z: 0, events: 52 },
  ],
  warnings: [],
}

const _TINY = "data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw=="

const MAP: MapPayload = {
  view: "eco_map",
  sourceUrl: "http://eco.example.com:3001",
  worldDim: { x: 1000, y: 200, z: 1000 },
  renderSize: 512,
  gifDataUri: _TINY,
  pollutionDataUri: null,
  biomeLayers: [{ name: "OceanBiome", display: "Ocean", color: "#4a9cb8", dataUri: _TINY }],
  polygons: [
    {
      owner: "alice",
      deed: "Alice's Homestead",
      points: "10,10 20,10 20,20 10,20",
      seamCopy: false,
    },
  ],
  geometryIncluded: true,
  ownerStyles: { alice: { fill: "hsla(1,50%,50%,0.4)", stroke: "hsla(1,60%,35%,0.9)" } },
  deedCount: 1,
  ownerCount: 1,
  owners: ["alice"],
  polygonCount: 1,
  deeds: [
    {
      deed: "Alice's Homestead",
      owner: "alice",
      centroid: { x: 15, z: 15 },
      bbox: { minX: 10, minZ: 10, maxX: 20, maxZ: 20 },
      areaBlocks: 100,
      vertexCount: 4,
      seamCrossing: false,
    },
  ],
  deedsNote: "Areas are approximate.",
  seamCopyCount: 0,
  seamNote: "seamCopy: true marks wrap copies.",
}

// Climate folded into the world page as its environmental overlay (eco-app#90).
const CURRENT_OBSERVATION: ClimateObservation = {
  latest_game_time_seconds: 59 * 86400,
  latest_game_day: 59,
  current_game_day: 59,
  interval_seconds: 86400,
  lag_intervals: 0,
  freshness_state: "current",
  freshness_reason: "within_source_cadence",
}

const CLIMATE: ClimateSnapshot = {
  server: { description: "Eco via Sirens", category: "Established", sourceUrl: "http://x/info" },
  days_elapsed: 59,
  admin_ok: true,
  status: "warming",
  narrative: "Climate is warming — CO2 at 325 ppm, sea level +3.05%.",
  co2: {
    current: 325,
    change_pct: 0,
    dataset_name: "TotalCO2",
    unit: "PPM",
    observation: CURRENT_OBSERVATION,
    series: [],
  },
  sea_level: {
    current: 61.83,
    change_pct: 3.05,
    rate_per_day: 0.03,
    dataset_name: "SeaLevel",
    unit: "Meters",
    observation: CURRENT_OBSERVATION,
    series: [],
  },
  pollution: {
    current: 437.5,
    source: "TotalGroundPollution",
    dataset_name: "TotalGroundPollution",
    unit: "PPM",
    observation: CURRENT_OBSERVATION,
    layer_summary: null,
    series: [[59 * 86400, 437.5]],
  },
  temperature: {
    current: 14.86,
    risen: 0.86,
    rate_per_day: 0.01,
    dataset_name: "AverageGlobalTemperature",
    unit: "Celsius",
    observation: CURRENT_OBSERVATION,
    series: [],
  },
  breakdown: {
    has_data: true,
    pollution: { lifetime: 12687, per_day: 4.2 },
    animals: { lifetime: 1450, per_day: 0.5 },
    plants: { lifetime: -28989, per_day: -357.7 },
    net_per_day: -353.0,
  },
  effects: {
    co2_now: 325,
    co2_peak: 520,
    min_floor_ppm: 325,
    at_floor: true,
    source: "default",
    temperature: { threshold_ppm: 400, ppm_per_degree: 25, headroom_ppm: 75, current_c: 14.86, risen_c: 0.86, peak_drives_c: 4.8 },
    sea_level: { threshold_ppm: 400, ppm_per_meter: 25, headroom_ppm: 75, current_m: 61.83, risen_m: 1.83, peak_drives_m: 4.8 },
  },
  explainer: ["CO2 sits at 325 ppm — pinned to the simulation floor."],
  earth_match: null,
  fetched_at_iso: "2026-06-15T14:00:00+00:00",
}

function stubFetch(ecoregion: EcoregionSnapshot = SNAP, climate: ClimateSnapshot = CLIMATE) {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      let body: unknown = {}
      if (url.includes("get_region")) body = ecoregion
      else if (url.includes("world.json")) body = WORLD
      else if (url.includes("preview-map.json")) body = MAP
      else if (url.includes("get_climate")) body = climate
      return Promise.resolve(
        new Response(JSON.stringify(body), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      )
    }),
  )
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/map"]}>
      <MapPage />
    </MemoryRouter>,
  )
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe("Map page", () => {
  it("shows a loading state before data resolves", () => {
    stubFetch()
    renderPage()
    expect(screen.getByTestId("loading")).toBeInTheDocument()
  })

  it("draws the map without the deed overlay or its owner legend", async () => {
    stubFetch()
    renderPage()
    await waitFor(() => expect(screen.getByTestId("map-frame")).toBeInTheDocument())
    // Removed at Kai's request (eco-app#8349): the overlay never lined up.
    expect(screen.queryByTestId("map-overlay")).not.toBeInTheDocument()
    expect(screen.getByTestId("map-frame").querySelector("polygon")).toBeNull()
    expect(screen.queryByTestId("map-owners")).not.toBeInTheDocument()
    expect(screen.getByTestId("map-meta")).toHaveTextContent("1 deed")
  })

  it("reclassifies water so unclassified is far below the old 61%", async () => {
    stubFetch()
    renderPage()
    await waitFor(() => expect(screen.getByTestId("eco-legend")).toBeInTheDocument())
    const legend = screen.getByTestId("eco-legend")
    expect(legend).toHaveTextContent("Coastal & shallow sea")
    expect(legend).toHaveTextContent("Fresh water")
    // 39% unclassified, not 61%.
    expect(legend).toHaveTextContent("39%")
  })

  it("highlights a biome raster on the map when its legend row is hovered", async () => {
    stubFetch()
    renderPage()
    await waitFor(() => expect(screen.getByTestId("map-biome-OceanBiome")).toBeInTheDocument())
    const raster = screen.getByTestId("map-biome-OceanBiome")
    expect(raster).toHaveStyle({ opacity: "0" })
    fireEvent.mouseEnter(screen.getByTestId("biome-legend-OceanBiome"))
    expect(raster).toHaveStyle({ opacity: "0.92" })
    fireEvent.mouseLeave(screen.getByTestId("biome-legend-OceanBiome"))
    expect(raster).toHaveStyle({ opacity: "0" })
  })

  it("removes Mutation timeline plus everything below it", async () => {
    stubFetch()
    renderPage()
    await waitFor(() => expect(screen.getByTestId("species-risk")).toBeInTheDocument())
    expect(screen.queryByText("Mutation timeline")).not.toBeInTheDocument()
    expect(screen.queryByText("By category")).not.toBeInTheDocument()
    expect(screen.queryByText("Top world-shapers")).not.toBeInTheDocument()
    expect(screen.queryByTestId("link-crafting")).not.toBeInTheDocument()
    expect(screen.queryByTestId("link-jobs")).not.toBeInTheDocument()
  })

  it("shows deterministic species states and links to population profiles", async () => {
    stubFetch()
    renderPage()
    await waitFor(() => expect(screen.getByTestId("species-risk")).toBeInTheDocument())
    expect(screen.getByTestId("species-risk-at_risk")).toHaveTextContent("at risk")
    expect(screen.getByTestId("species-risk-stable")).toHaveTextContent("stable")
    expect(screen.getByTestId("species-risk-recovering")).toHaveTextContent("recovering")
    expect(screen.getByTestId("species-risk-naturally_sparse")).toHaveTextContent("naturally sparse")
    expect(screen.getByTestId("species-risk-missing")).toHaveTextContent("missing")
    expect(screen.getByTestId("species-risk-stale")).toHaveTextContent("stale")
    expect(screen.getByRole("link", { name: "Wolf" })).toHaveAttribute(
      "href",
      "/species?name=WolfSpecies",
    )
  })

  it("makes exporter failure an explicit unavailable evidence state", async () => {
    stubFetch({
      ...SNAP,
      speciesRisk: {
        ...SNAP.speciesRisk,
        sourceState: "unavailable",
        atRiskCount: 0,
        species: [],
      },
      adminAvailable: false,
    })
    renderPage()

    await waitFor(() => {
      expect(screen.getByTestId("species-risk-unavailable")).toBeInTheDocument()
    })
    expect(screen.getByTestId("species-risk-unavailable")).toHaveTextContent(
      "No health claim is made without it",
    )
    expect(screen.queryByTestId("species-risk")).not.toBeInTheDocument()
  })

  it("folds the climate atmosphere overlay into the world page", async () => {
    stubFetch()
    renderPage()
    await waitFor(() => expect(screen.getByTestId("climate")).toBeInTheDocument())
    // The climate narrative pill and the CO2 atmosphere tile both render.
    expect(screen.getByTestId("climate-pill")).toHaveTextContent("warming")
    expect(screen.getByTestId("climate")).toHaveTextContent("325 ppm")
    expect(screen.getByTestId("climate")).toHaveTextContent("437.5 PPM")
    expect(screen.getByTestId("climate-freshness")).toHaveTextContent("Snapshot fetched")
    expect(screen.getByTestId("pollution-source-freshness")).toHaveTextContent(
      "current for game time Day 59, 00:00",
    )
    expect(screen.getByTestId("climate-coordination")).toHaveTextContent("Observed risk")
    // The former standalone /climate cross-link card is gone — it's folded in.
    expect(screen.queryByTestId("link-climate")).not.toBeInTheDocument()
  })

  it("visibly reports pollution when the source is behind the current game day", async () => {
    stubFetch(SNAP, {
      ...CLIMATE,
      pollution: {
        ...CLIMATE.pollution,
        observation: {
          ...CURRENT_OBSERVATION,
          latest_game_time_seconds: 58 * 86400,
          latest_game_day: 58,
          lag_intervals: 1,
          freshness_state: "stale",
          freshness_reason: "behind_current_game_day",
        },
      },
    })
    renderPage()

    const freshness = await screen.findByTestId("pollution-source-freshness")
    expect(freshness).toHaveClass("hero-pill-warn")
    expect(freshness).toHaveTextContent("Ground pollution data is stale")
    expect(freshness).toHaveTextContent("Day 58, 00:00")
    expect(freshness).toHaveTextContent("Day 59, 00:00")
  })

  it("keeps source freshness unknown when cadence metadata is absent", async () => {
    stubFetch(SNAP, {
      ...CLIMATE,
      pollution: {
        ...CLIMATE.pollution,
        observation: {
          ...CURRENT_OBSERVATION,
          interval_seconds: null,
          lag_intervals: null,
          freshness_state: "unknown",
          freshness_reason: "unknown_cadence",
        },
      },
    })
    renderPage()

    expect(await screen.findByTestId("pollution-source-freshness")).toHaveTextContent(
      "source cadence is unavailable, so source freshness is unknown",
    )
  })

  it("reserves percent for the world-layer pollution fallback", async () => {
    stubFetch(SNAP, {
      ...CLIMATE,
      pollution: {
        ...CLIMATE.pollution,
        current: 4,
        source: "worldlayers",
        dataset_name: null,
        unit: "%",
        observation: {
          latest_game_time_seconds: null,
          latest_game_day: null,
          current_game_day: 59,
          interval_seconds: null,
          lag_intervals: null,
          freshness_state: "unknown",
          freshness_reason: "no_sample",
        },
        layer_summary: "4%",
        series: [],
      },
    })
    renderPage()

    expect(await screen.findByTestId("climate")).toHaveTextContent("4.0%")
    expect(screen.getByTestId("pollution-source-freshness")).toHaveTextContent(
      "world-layer percentage fallback",
    )
  })
})
