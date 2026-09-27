import { hydrateNorms } from "./priceNorm"
// Typed client for the fused service's JSON surfaces. The SPA talks to the
// same origin in production; in dev, Vite proxies these routes to :4000.

export interface EcoServerInfo {
  description: string
  detailedDescription: string
  category: string
  discord: string | null
  version: string
  language: string
  paused: boolean
  hasPassword: boolean
  adminOnline: boolean
}

// Every /info-sourced number is nullable: the game server omits fields by
// version and mod set, and eco-app passes an omission through as null rather
// than defaulting it to 0 (eco-app#214). Render null as unknown, never as zero.
export interface EcoPlayers {
  online: number | null
  onlineNames: string[]
  total: number | null
  activeAndOnline: number | null
  peakActive: number | null
}

export interface EcoWorld {
  size: string
  plants: number | null
  animals: number | null
  // Set when the animal count is not trustworthy: /info reports 0 even on
  // servers with live fauna, so get_region is the real source (eco-app#246).
  animalsNote: string | null
  laws: number | null
  totalCulture: number | null
  // "milestones" when /info reported 0 culture but the milestone list showed
  // real progress, so the number is a floor rather than the server's counter
  // (eco-app#237).
  totalCultureSource: "info" | "milestones"
}

export interface EcoCycle {
  daysRunning: number | null
  daysUntilMeteor: number | null
  // Real seconds since cycle start (the /info world clock); 1 in-game day = 3600s.
  // Eco 0.13 does not send it at all, so null is the common case.
  timeSinceStartS: number | null
  hasMeteor: boolean
  collaboration: string
  gameSpeed: string
  simulationLevel: string
}

export interface EcoStatus {
  view: "eco_status"
  fetchedAtISO: string
  sourceUrl: string
  server: EcoServerInfo
  players: EcoPlayers
  world: EcoWorld
  cycle: EcoCycle
  economy: { description: string }
  achievements: Array<{ name: string; text: string }>
}

export async function fetchEcoStatus(
  server = "",
  signal?: AbortSignal,
): Promise<EcoStatus> {
  const target = server.trim()
  const query = target ? `?${new URLSearchParams({ server: target })}` : ""
  const resp = await fetch(`/preview.json${query}`, { signal })
  if (!resp.ok) {
    throw new Error(`status fetch failed: HTTP ${resp.status}`)
  }
  return (await resp.json()) as EcoStatus
}

// Best-effort JSON fetch for the multi-panel /trade surface: a missing, empty,
// or failed endpoint resolves to null so each panel degrades on its own rather
// than taking the whole page down. The sibling data planes (market, stores,
// logistics, currency) land independently, so a 404 here is expected
// wiring, not an exceptional error. An aborted fetch also resolves to null —
// the caller unmounts, so there is nothing to surface.
export async function fetchJsonOrNull<T>(url: string, signal?: AbortSignal): Promise<T | null> {
  try {
    const resp = await fetch(url, { signal })
    if (!resp.ok) return null
    // Folds each payload's normContext into its norms (eco-app#8368).
    return hydrateNorms((await resp.json()) as T)
  } catch {
    return null
  }
}
