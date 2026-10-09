// Typed client for the world / industry activity surface
// (/preview/world.json → get_world). byCitizen / byPolluter carry display
// names joined from the jobs mod's /api/v1/citizens surface, falling back to
// "Citizen #<id>" when a name is missing (eco-app#5, eco-app#62).

export interface WorldCategory {
  key: string
  label: string
  events: number
  volume: number
}

// One category's players, ranked (COI-2048). Each pair is [name, events], the
// unit of WorldCategory.events. players is null when every action behind the
// category failed to fetch, never an empty list standing in for "nobody".
export interface WorldCategoryPlayers {
  key: string
  label: string
  events: number | null
  players: Array<[string, number]> | null
}

export interface WorldTimelineDay {
  day: number
  counts: Record<string, number>
}

export interface WorldHotspot {
  x: number
  z: number
  events: number
}

export interface WorldActivity {
  view: string
  fetchedAtISO: string
  sourceBaseUrl: string
  totalEvents: number
  perActionCounts: Record<string, number>
  categories: WorldCategory[]
  categoryKeys: string[]
  timeline: WorldTimelineDay[]
  byCitizen: Array<[string, number]>
  byPolluter: Array<[string, number]>
  byCitizenByCategory?: WorldCategoryPlayers[]
  byCitizenByCategoryNote?: string
  byObject: Array<[string, number]>
  hotspots: WorldHotspot[]
  warnings: string[]
}

export async function fetchWorld(signal?: AbortSignal): Promise<WorldActivity> {
  const resp = await fetch("/preview/world.json", { signal })
  if (!resp.ok) {
    throw new Error(`world activity fetch failed: HTTP ${resp.status}`)
  }
  return (await resp.json()) as WorldActivity
}
