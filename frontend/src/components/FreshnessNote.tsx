import { useEffect, useState } from "react"

import { contractFor, describeFreshness, isStale, type PlaneName } from "../lib/freshness"

// The user-visible half of the refresh contract (eco-app#201).
//
// Every page that fetches shows one of these, so a reader can always tell
// whether what they are looking at is still being updated. Three shapes,
// matching the three contract modes:
//
//   live    "updated 40s ago"                     (and re-renders as it ages)
//   manual  "loaded 3m ago · Refresh"
//   static  "loaded 5m ago (this data does not change while the page is open)"
//
// A `live` plane that has aged past its stale window says so plainly rather
// than continuing to look current — that is the exact failure eco-app#184
// reported, a page quietly showing 12-hour-old pollution as if it were now.

interface Props {
  plane: PlaneName
  loadedAt: number | null
  refreshing?: boolean
  refreshError?: string | null
  onRefresh?: () => void
  /**
   * The payload's own `fetchedAtISO`, when it has one. Shown separately from
   * the browser load age: backend fetch time is not source observation time,
   * and the two must not be conflated (eco-app#184).
   */
  observedAtISO?: string | null
}

// How often the age caption re-renders. Independent of the poll cadence: the
// caption has to keep ageing even on a `manual` plane that never refetches.
const TICK_MS = 15_000

export default function FreshnessNote({
  plane,
  loadedAt,
  refreshing = false,
  refreshError = null,
  onRefresh,
  observedAtISO = null,
}: Props) {
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), TICK_MS)
    return () => clearInterval(timer)
  }, [])

  const contract = contractFor(plane)
  const stale = isStale(plane, loadedAt, now)

  return (
    <p
      className={stale ? "freshness-note freshness-note-stale" : "freshness-note"}
      data-testid={`freshness-${plane}`}
      data-stale={stale ? "true" : "false"}
    >
      <span data-testid={`freshness-age-${plane}`}>
        {describeFreshness(plane, loadedAt, now)}
      </span>
      {refreshing && <span className="freshness-spinner"> · updating…</span>}
      {contract.mode !== "static" && onRefresh && (
        <>
          {" · "}
          <button
            type="button"
            className="freshness-refresh"
            onClick={onRefresh}
            disabled={refreshing}
            data-testid={`freshness-refresh-${plane}`}
          >
            Refresh
          </button>
        </>
      )}
      {observedAtISO && (
        <span className="freshness-observed" data-testid={`freshness-observed-${plane}`}>
          {" · server data from "}
          {observedAtISO}
        </span>
      )}
      {refreshError && (
        <span className="freshness-error" data-testid={`freshness-error-${plane}`}>
          {" · could not refresh, still showing the last data that loaded"}
        </span>
      )}
    </p>
  )
}
