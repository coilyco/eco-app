// Refresh contracts for every SPA data plane (eco-app#201).
//
// eco-app#184 found the public world page showing pollution numbers that had
// not moved in over 12 hours. The audit behind eco-app#201 found the general
// case: every page fetched once on mount and never again, so any page left
// open silently drifted from its source with nothing on screen to say so.
//
// This module is the single place that answers, per plane: how often can the
// source advance, and what does an open page do about it. A plane is either
// `live` (polls), `manual` (a refresh control, no timer), or `static` (the
// data cannot advance within a session). `unknown` is a real answer and is
// rendered as one — an honest "we don't know how fresh this is" beats a
// confident timestamp we cannot stand behind.
//
// Cadences live here rather than in components so they are tunable in one
// place, and so tests assert behaviour rather than restating the constants.

export type RefreshMode = "live" | "manual" | "static"

export interface RefreshContract {
  mode: RefreshMode
  /** Poll period in ms. Only meaningful for `live`. */
  pollMs?: number
  /**
   * How long after a load the data should be treated as possibly stale. Undefined
   * means the cadence is genuinely unknown, which the UI says out loud.
   */
  staleAfterMs?: number
  /** Why this plane has the contract it has — shown in docs, not in the UI. */
  rationale: string
}

const SECOND = 1000
const MINUTE = 60 * SECOND

// Server-side caches bound how fresh a poll can possibly be, so polling faster
// than the cache TTL only burns requests. These mirror the backend TTLs:
// trades/civics/progression 60s, currency 45s, climate/world per-fetch.
export const LIVE_POLL_MS = 2 * MINUTE
export const SLOW_POLL_MS = 5 * MINUTE

export const REFRESH_CONTRACTS = {
  // --- Live: the source moves on its own while a page sits open -----------
  status: {
    mode: "live",
    // 60s, preserved from useEcoStatus — the one plane that already polled
    // before this audit. Online player counts are the fastest-moving thing on
    // the site and /info is cheap.
    pollMs: MINUTE,
    staleAfterMs: 5 * MINUTE,
    rationale: "Online players and the meteor countdown change continuously.",
  },
  world: {
    mode: "live",
    pollMs: LIVE_POLL_MS,
    staleAfterMs: 2 * LIVE_POLL_MS,
    rationale:
      "Pollution and world activity advance with play. This is the plane eco-app#184 " +
      "found frozen for 12 hours.",
  },
  climate: {
    mode: "live",
    pollMs: LIVE_POLL_MS,
    staleAfterMs: 2 * LIVE_POLL_MS,
    rationale: "CO2, temperature and sea level advance with the simulation.",
  },
  trades: {
    mode: "live",
    pollMs: LIVE_POLL_MS,
    staleAfterMs: 2 * LIVE_POLL_MS,
    rationale: "Players trade continuously, so the ledger grows during a session.",
  },

  // --- Manual: advances, but slowly enough that a timer is not justified ---
  civics: {
    mode: "manual",
    staleAfterMs: 15 * MINUTE,
    rationale: "Elections and settlements move on human timescales, not per-minute.",
  },
  crafting: {
    mode: "manual",
    staleAfterMs: 15 * MINUTE,
    rationale: "Crafting totals accumulate steadily, and a stale read misleads nobody in minutes.",
  },
  progression: {
    mode: "manual",
    staleAfterMs: 15 * MINUTE,
    rationale: "Skill level-ups are infrequent per player.",
  },
  jobs: {
    mode: "manual",
    staleAfterMs: 15 * MINUTE,
    rationale: "Specialty holders change only when someone levels, and few do within a session.",
  },
  logistics: {
    mode: "manual",
    staleAfterMs: 15 * MINUTE,
    rationale:
      "Live shelf offers change with play, but the arbitrage and supply-gap boards are read " +
      "to make a decision, then acted on. A timer would move the answer mid-read.",
  },
  food: {
    mode: "manual",
    staleAfterMs: 15 * MINUTE,
    rationale: "Food and nutrition signals follow crafting output, which accrues slowly.",
  },
  stores: {
    mode: "manual",
    staleAfterMs: 15 * MINUTE,
    rationale: "Shelf contents change with play, but a shopper re-checks deliberately.",
  },
  market: {
    mode: "manual",
    staleAfterMs: 15 * MINUTE,
    rationale: "Per-item price history moves with the ledger, on the same slow cadence.",
  },
  trade: {
    mode: "live",
    pollMs: LIVE_POLL_MS,
    staleAfterMs: 2 * LIVE_POLL_MS,
    rationale:
      "Composes market, stores, logistics and the ledger. The ledger is live, " +
      "and this is the page a trader leaves open while deciding, so the whole " +
      "board keeps up rather than going quietly stale under them.",
  },

  // Composite planes. A page that fans out to several sources refreshes them
  // together, because a summary stitched from reads minutes apart is worse
  // than one that is uniformly a few minutes old. The contract is the
  // fastest-moving member's.
  shopCheck: {
    mode: "manual",
    staleAfterMs: 15 * MINUTE,
    rationale: "Composes stores and market, both manual on the same cadence.",
  },
  resolve: {
    mode: "manual",
    staleAfterMs: 15 * MINUTE,
    rationale:
      "Composes recipes, logistics, market and jobs. Refreshed as one so the answer is " +
      "internally consistent rather than stitched from reads minutes apart.",
  },
  region: {
    mode: "manual",
    staleAfterMs: 15 * MINUTE,
    rationale: "Species populations are sampled on a slow server cadence.",
  },
  replay: {
    mode: "manual",
    staleAfterMs: 15 * MINUTE,
    rationale: "The replay log appends as events happen, but is browsed historically.",
  },

  // --- Static: cannot advance within a session ----------------------------
  recipes: {
    mode: "static",
    rationale: "The recipe graph is a vendored build artifact that changes on deploy.",
  },
  items: {
    mode: "static",
    rationale: "The item catalogue is fixed for a server build.",
  },
  species: {
    mode: "static",
    rationale: "Taxonomy and wiki text are external reference data on a 7-day cache.",
  },
  map: {
    mode: "static",
    rationale:
      "Deed polygons and biome rasters change when land changes hands, which is a reload-" +
      "worthy event rather than a per-minute one.",
  },
  fairPrice: {
    mode: "static",
    rationale: "FRED commodity series are monthly or daily, so a session never outlives one.",
  },
  priceHistory: {
    mode: "static",
    rationale: "Cycle-relative price history is closed for past cycles.",
  },
} as const satisfies Record<string, RefreshContract>

export type PlaneName = keyof typeof REFRESH_CONTRACTS

export function contractFor(plane: PlaneName): RefreshContract {
  return REFRESH_CONTRACTS[plane]
}

/**
 * How the page should describe its own freshness right now.
 *
 * `loadedAt` is *browser load age* — when this tab last received data. It is
 * deliberately not presented as the age of the observation itself: the backend
 * cache and the game server's own sampling sit in between, and conflating them
 * is what eco-app#184 warns against. When a payload carries its own
 * `fetchedAtISO`, pass it as `observedAt` and both are shown.
 */
export function describeFreshness(
  plane: PlaneName,
  loadedAt: number | null,
  now: number,
): string {
  const contract = contractFor(plane)
  if (loadedAt === null) return "not loaded yet"
  const ageMs = Math.max(0, now - loadedAt)
  const age = formatAge(ageMs)
  if (contract.mode === "static") {
    return `loaded ${age} ago (this data does not change while the page is open)`
  }
  if (contract.staleAfterMs === undefined) {
    return `loaded ${age} ago (not sure how often this updates, reload to be sure)`
  }
  if (ageMs > contract.staleAfterMs) {
    return contract.mode === "live"
      ? `last updated ${age} ago (it stopped updating, reload the page)`
      : `loaded ${age} ago (may be out of date, refresh to update)`
  }
  return contract.mode === "live" ? `updated ${age} ago` : `loaded ${age} ago`
}

export function isStale(plane: PlaneName, loadedAt: number | null, now: number): boolean {
  const contract = contractFor(plane)
  if (loadedAt === null || contract.staleAfterMs === undefined) return false
  return now - loadedAt > contract.staleAfterMs
}

export function formatAge(ms: number): string {
  const seconds = Math.round(ms / SECOND)
  if (seconds < 60) return `${seconds}s`
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes}m`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours}h`
  return `${Math.round(hours / 24)}d`
}
