import FreshnessNote from "../components/FreshnessNote"
import Layout from "../components/Layout"
import { fetchCivics } from "../lib/civicsApi"
import { formatCount } from "../lib/format"
import { useFreshData } from "../lib/useFreshData"

const RECENT = 12

// Render a resolved name, or the raw id when the citizens join missed it.
// Never "Citizen #<id>": some of those ids are election titles, not people
// (eco-app#223). Showing "#456767" keeps the information without asserting
// that a player by that name exists.
function entity(name: string | null, id: string | null): string {
  if (name) return name
  return id ? `#${id}` : "—"
}

export default function Civics() {
  // Refresh contract lives in freshness.ts, not here (eco-app#201).
  const civicsPlane = useFreshData("civics", fetchCivics)
  const report = civicsPlane.data
  const error = civicsPlane.error


  const turnoutPct =
    report && report.turnoutRate !== null ? Math.round(report.turnoutRate * 100) : null

  return (
    <Layout>
      {/* One heading + the live pill as the single intro line (eco-app#97). The
          old tagline's cross-link to Info survives as the dir-card below. */}
      <section className="hero hero-compact">
        <h1 className="hero-title">Civics &amp; governance</h1>
        {report && (report.totalEvents ?? 0) > 0 && (
          <p className="hero-pill" data-testid="civics-pill">
            <span className="pulse-dot" aria-hidden="true" />
            {formatCount(report.totalEvents)} civic events
            {turnoutPct !== null ? ` · ${turnoutPct}% turnout` : ""}
          </p>
        )}
        {!report && error && (
          <p className="hero-pill hero-pill-muted" data-testid="civics-error">
            civics report unavailable right now
          </p>
        )}
        <FreshnessNote
          plane="civics"
          loadedAt={civicsPlane.loadedAt}
          refreshing={civicsPlane.refreshing}
          refreshError={civicsPlane.refreshError}
          onRefresh={civicsPlane.refresh}
        />
      </section>

      {report && !report.adminAvailable && (
        <section>
          <p className="empty-note" data-testid="civics-unmeasured">
            No civic exporter on this server could be read, so nothing below was measured.
            This is not the same as a quiet server — the counts are unknown, not zero.
          </p>
        </section>
      )}

      {report && report.adminAvailable && report.totalEvents === 0 && (
        <section>
          <p className="empty-note" data-testid="civics-empty">
            No civic events recorded on this server yet. Early in a cycle this is normal —
            elections, citizenships, and settlements show up here as they happen.
          </p>
        </section>
      )}

      {report && (report.totalEvents ?? 0) > 0 && (
        <>
          <section className="stats" aria-label="civic snapshot" data-testid="civics-stats">
            <div className="stat">
              <p className="stat-value">{turnoutPct !== null ? `${turnoutPct}%` : "—"}</p>
              <p className="stat-label">Turnout</p>
              <p className="stat-detail">
                {formatCount(report.votesCast)} cast · {formatCount(report.abstentions)} abstained
              </p>
            </div>
            <div className="stat">
              <p className="stat-value">{formatCount(report.electionsStarted)}</p>
              <p className="stat-label">Elections</p>
              <p className="stat-detail">{formatCount(report.electionsWon)} won</p>
            </div>
            <div className="stat">
              {/* Distinct people, not repeated exporter events (eco-app#224). */}
              <p className="stat-value">
                {report.netDistinctCitizens !== null && report.netDistinctCitizens >= 0
                  ? "+"
                  : ""}
                {formatCount(report.netDistinctCitizens)}
              </p>
              <p className="stat-label">Net citizens</p>
              <p className="stat-detail">
                +{formatCount(report.distinctCitizensGained)} / -
                {formatCount(report.distinctCitizensLost)} people ·{" "}
                {formatCount(
                  report.citizensGained === null || report.citizensLost === null
                    ? null
                    : report.citizensGained + report.citizensLost,
                )}{" "}
                events
              </p>
            </div>
            <div className="stat">
              <p className="stat-value">{formatCount(report.residencyMoves)}</p>
              <p className="stat-label">Residency moves</p>
            </div>
            <div className="stat">
              <p className="stat-value">{formatCount(report.settlementsFounded)}</p>
              <p className="stat-label">Settlements</p>
              <p className="stat-detail">
                {formatCount(report.settlementFoundationsPlaced)} foundations staked ·{" "}
                {formatCount(report.homesteadsStarted)} homesteads
              </p>
            </div>
          </section>

          {report.recentElections.length > 0 && (
            <section>
              <h2 className="section-title">
                Recent elections{" "}
                <span className="section-sub">(newest {Math.min(RECENT, report.recentElections.length)})</span>
              </h2>
              <table tabIndex={0} className="ledger-table" data-testid="elections-table">
                <thead>
                  <tr>
                    <th>Day</th>
                    <th>Election</th>
                    <th>Proposed by</th>
                  </tr>
                </thead>
                <tbody>
                  {report.recentElections.slice(0, RECENT).map((e, i) => (
                    <tr key={`${e.day}-${i}`} data-testid="election-row">
                      <td>{e.day}</td>
                      <td>{entity(e.subject, e.subjectId) || "Election"}</td>
                      <td>{entity(e.proposer, e.proposerId)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          )}

          {report.warnings.length > 0 && (
            <section>
              <ul className="warn-list" data-testid="civics-warnings">
                {report.warnings.map((w) => (
                  <li key={w}>⚠ {w}</li>
                ))}
              </ul>
            </section>
          )}
        </>
      )}
    </Layout>
  )
}
