// Typed client for the crafting atlas (/preview/get_crafting_atlas.json).
// byCitizen carries display names joined from the jobs mod's /api/v1/citizens
// surface, falling back to "Citizen #<id>" when a name is missing (eco-app#5).
//
// Produced items are split into two boards (eco-app#70): byCrafted counts
// crafting *iterations* from per-event ItemCraftedAction rows, byGathered
// counts harvest/chop/dig *events* (their raw Count is biomass magnitude, not
// a unit count, so summing it buried player crafting under plant biomass).
//
// The server rolls craft events older than its detail window into per-citizen
// hourly aggregates whose item/station labels are unreliable, so those rows
// are excluded from the item and station boards and surfaced via
// rollupEvents / rollupIterations instead. The citizen boards keep them - the
// citizen is the rollup's grouping key, so they span all history (eco-app#131).
//
// Two citizen boards, because they carry two different units (eco-app#222):
// byCitizen counts events, matching get_world.byCitizen, while
// byCitizenIterations weighs crafts by Count. The iteration board legitimately
// exceeds totalEvents, so it has to be labelled wherever it is shown.
//
// byMiner is the one mining board (COI-2049): DigOrMine rows per player, one
// per dig or mine event. Neither citizen board above is a mining count. It is
// null when the DigOrMine exporter was not fetched, and optional here because
// the page does not render it yet.

export interface CraftingAtlas {
  fetchedAtISO: string
  sourceBaseUrl: string
  totalEvents: number
  byCrafted: Array<[string, number]>
  byGathered: Array<[string, number]>
  byStation: Array<[string, number]>
  byCitizen: Array<[string, number]>
  byCitizenIterations: Array<[string, number]>
  byMiner?: Array<[string, number]> | null
  byMinerNote?: string
  flows: Array<[string, string, number]>
  perActionCounts: Record<string, number>
  rollupEvents: number
  rollupIterations: number
  warnings: string[]
}

export async function fetchCraftingAtlas(signal?: AbortSignal): Promise<CraftingAtlas> {
  // limit=0: the SPA renders the full set; the bounded default exists for
  // MCP callers, who have a response cap the browser does not (eco-app#256).
  const resp = await fetch("/preview/get_crafting_atlas.json?limit=0", { signal })
  if (!resp.ok) {
    throw new Error(`crafting atlas fetch failed: HTTP ${resp.status}`)
  }
  return (await resp.json()) as CraftingAtlas
}
