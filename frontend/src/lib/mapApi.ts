// Typed client for the live world-map plane (/preview-map.json).
//
// The endpoint returns map.build_map_payload() from eco_mcp_app/map.py: the
// base world preview and optional pollution raster inlined as data URIs, and a
// biomeLayers list of per-biome raster overlays keyed to the ecoregion donut's
// biome names, so hovering a biome highlights where it sits on the map (#82).
// Property deeds are not part of this plane (COI-2092).

export interface MapBiomeLayer {
  // Matches an EcoregionSnapshot biome `name` (e.g. "OceanBiome").
  name: string
  display: string
  color: string
  // The biome's coverage raster, inlined so CSP needs no external origin.
  dataUri: string
}

export interface MapPayload {
  view: "eco_map"
  sourceUrl: string | null
  // World extent; the SPA scales hotspot world-coords by renderSize/worldDim.
  worldDim: { x: number; y: number; z: number }
  renderSize: number
  gifDataUri: string
  pollutionDataUri: string | null
  biomeLayers: MapBiomeLayer[]
}

export async function fetchMap(signal?: AbortSignal): Promise<MapPayload> {
  const resp = await fetch("/preview-map.json", { signal })
  if (!resp.ok) {
    throw new Error(`map fetch failed: HTTP ${resp.status}`)
  }
  return (await resp.json()) as MapPayload
}
