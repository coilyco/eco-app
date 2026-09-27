// Typed client for the trade watchers (/preview/watchers.json).
//
// The endpoint evaluates every stored watcher against the live ledger in *peek*
// mode (it never advances a watcher's last-seen mark — only the MCP `evaluate`
// verb does), and returns one hit per watcher. Each hit carries both
// DiscordLink semantics: the feed (matching trades new since the watcher last
// checked, as a count here) and the display (the current matching state). See
// eco_mcp_app/watchers.py (eco-app#52). Watchers are created / removed over MCP
// (`trade_watchers`); the SPA is a read-only surface for them.

import type { Trade } from "./tradesApi"
import { hydrateNorms, type PriceNorm } from "./priceNorm"

export interface WatcherDisplay {
  /** Historical price norm (eco-app#8368). Optional while payloads gain it. */
  norm?: PriceNorm | null
  matchCount: number
  recent: Trade[]
  bestUnitPrice: number | null
  totalVolume: number
  lastMatchTime: number | null
}

export interface WatcherHit {
  id: string
  kind: string
  value: string
  op: string | null
  threshold: number | null
  label: string
  server: string | null
  lastSeen: number
  createdAt: number
  describe: string
  feed: Trade[]
  feedCount: number
  display: WatcherDisplay
  newLastSeen: number
}

export interface WatchersReport {
  view: string
  fetchedAtISO?: string
  sourceBaseUrl?: string
  advanced: boolean
  hits: WatcherHit[]
}

// Non-fatal by contract: a watcher store that's empty, unreachable, or served
// without an admin key still resolves to an empty list rather than throwing, so
// the trades page never breaks over its watcher sidebar.
export async function fetchWatchers(signal?: AbortSignal): Promise<WatcherHit[]> {
  const resp = await fetch("/preview/watchers.json", { signal })
  if (!resp.ok) {
    return []
  }
  const body = hydrateNorms((await resp.json()) as Partial<WatchersReport>)
  return Array.isArray(body.hits) ? body.hits : []
}
