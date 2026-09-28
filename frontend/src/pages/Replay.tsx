import { useMemo } from "react"
import { useSearchParams } from "react-router-dom"
import FreshnessNote from "../components/FreshnessNote"
import Layout from "../components/Layout"
import Loading from "../components/Loading"
import { fetchReplayData, type ReplayEvent } from "../lib/replayApi"
import { formatCount } from "../lib/format"
import { useFreshData } from "../lib/useFreshData"

const VISIBLE_ROWS = 100

// Render a recorded event's UTC wall-clock from its unix-seconds stamp. Kept
// local (not format.ts) because it's the only unix-seconds surface — every
// other page formats an ISO string via formatFetchedAt.
function formatEventTime(unixSeconds: number): string {
  const d = new Date(unixSeconds * 1000)
  if (Number.isNaN(d.getTime())) return "—"
  return d.toISOString().replace("T", " ").replace(/\.\d+Z$/, " UTC")
}

function matchesEvent(e: ReplayEvent, needle: string): boolean {
  if (!needle) return true
  const hay = [e.citizen ?? "", e.type, e.body].join(" ").toLowerCase()
  return hay.includes(needle)
}

export default function Replay() {
  // Refresh contract lives in freshness.ts, not here (eco-app#201).
  const replayPlane = useFreshData("replay", (signal) => fetchReplayData(200, signal))
  const events = replayPlane.data?.events ?? null
  const total = replayPlane.data?.total ?? 0
  const mockData = replayPlane.data?.mockData ?? false
  const error = replayPlane.error
  const [params, setParams] = useSearchParams()
  const q = params.get("q") ?? ""

  const setQuery = (value: string) => {
    setParams(value ? { q: value } : {}, { replace: false })
  }

  const needle = q.trim().toLowerCase()
  const visible = useMemo(
    () => (events ? events.filter((e) => matchesEvent(e, needle)).slice(0, VISIBLE_ROWS) : []),
    [events, needle],
  )

  return (
    <Layout>
      {mockData && (
        <div className="mock-banner" data-testid="mock-banner">
          ⚠️ FAKE DATA. Every event on this page is made up. To show the real Chronicle, the site
          admin sets <code>ECO_REPLAY_FILE</code> or <code>ECO_REPLAY_UPSTREAM_URL</code> on the
          service. ⚠️
        </div>
      )}

      <section className="hero hero-compact">
        <p className="hero-kicker">Kaihronicler</p>
        <h1 className="hero-title">
          The server's <span className="accent">chronicle</span>, event by event
        </h1>
        <p className="hero-tagline">
          Every recorded player action (logins, chat, blocks placed), newest first. A read-only
          mirror of the in-game Chronicler.
        </p>
        {events && (
          <p className="hero-pill" data-testid="replay-pill">
            <span className="pulse-dot" aria-hidden="true" />
            {formatCount(total)} events recorded
          </p>
        )}
        {!events && error && (
          <p className="hero-pill hero-pill-muted" data-testid="replay-error">
            chronicle unavailable right now
          </p>
        )}
        <FreshnessNote
          plane="replay"
          loadedAt={replayPlane.loadedAt}
          refreshing={replayPlane.refreshing}
          refreshError={replayPlane.refreshError}
          onRefresh={replayPlane.refresh}
        />
      </section>

      {!events && !error && <Loading label="Reading the chronicle…" testid="replay-loading" />}

      {events && total === 0 && (
        <section>
          <p className="empty-note" data-testid="replay-empty">
            No events recorded yet. Once the replay mod is running and players are active, the
            chronicle fills in here.
          </p>
        </section>
      )}

      {events && total > 0 && (
        <>
          <section className="filter-row">
            <input
              className="filter-input"
              type="search"
              placeholder="Filter by citizen, action, or details…"
              value={q}
              onChange={(e) => setQuery(e.target.value)}
              data-testid="replay-filter"
            />
            {q && (
              <button className="button" onClick={() => setQuery("")}>
                Clear
              </button>
            )}
          </section>

          <section>
            <h2 className="section-title">
              Timeline{" "}
              <span className="section-sub">
                (showing the newest {visible.length}
                {(events?.length ?? 0) > visible.length
                  ? ` of ${formatCount(events?.length ?? 0)}`
                  : ""}
                )
              </span>
            </h2>
            {visible.length === 0 ? (
              <p className="empty-note" data-testid="replay-no-match">
                No events match.
              </p>
            ) : (
              <table tabIndex={0} className="ledger-table" data-testid="replay-table">
                <thead>
                  <tr>
                    <th>Time (UTC)</th>
                    <th>Citizen</th>
                    <th>Action</th>
                    <th>Details</th>
                  </tr>
                </thead>
                <tbody>
                  {visible.map((e) => (
                    <tr key={e.id} data-testid="replay-row">
                      <td>{formatEventTime(e.unixTime)}</td>
                      <td>{e.citizen || "—"}</td>
                      <td>
                        <button className="linklike" onClick={() => setQuery(e.type)}>
                          {e.type}
                        </button>
                      </td>
                      <td>
                        <pre className="replay-body">{e.body}</pre>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
        </>
      )}
    </Layout>
  )
}
