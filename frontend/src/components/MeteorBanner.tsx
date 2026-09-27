import type { EcoCycle, EcoStatus } from "../lib/api"
import { formatDayHour, meteorProgressPercent } from "../lib/format"
import { meteorDestroyed } from "../lib/serverBrief"

// The meteor is the heartbeat of an Eco cycle: the single shared deadline
// the whole server organizes around. It gets the loudest visual on the page.
// The caption names both the day and the hour (eco-app#97) via the shared
// world-clock helper, folding the /info TimeSinceStart snapshot.
export default function MeteorBanner({
  cycle,
  achievements,
}: {
  cycle: EcoCycle
  achievements?: EcoStatus["achievements"]
}) {
  // TimeSinceStart is absent from Eco 0.13's /info, so the world clock is
  // routinely unknown (eco-app#214). Drop the clause rather than caption
  // "day 0, 0h", which reads as a fresh restart.
  const dayHour = cycle.timeSinceStartS === null ? null : formatDayHour(cycle.timeSinceStartS)
  // After the meteor is destroyed /info drops the countdown, and "a meteor is
  // coming, 0%" would be a false fact on a public page. The world achievement
  // is the only record of it.
  const destroyed = meteorDestroyed(achievements)
  if (destroyed) {
    return (
      <section className="meteor meteor-clear" data-testid="meteor-destroyed">
        <p className="meteor-count">The meteor is destroyed</p>
        <p className="meteor-caption">
          {`Destroyed on day ${destroyed.day}${destroyed.time ? ` at ${destroyed.time}` : ""}.`}
        </p>
      </section>
    )
  }
  if (!cycle.hasMeteor) {
    return (
      <section className="meteor meteor-clear">
        <p className="meteor-count">The sky is clear</p>
        <p className="meteor-caption">
          {dayHour ? `No meteor this cycle - ${dayHour} and counting.` : "No meteor this cycle."}
        </p>
      </section>
    )
  }

  const pct = meteorProgressPercent(cycle.daysRunning, cycle.daysUntilMeteor)
  const days = cycle.daysUntilMeteor
  return (
    <section className="meteor" aria-label="meteor countdown">
      <p className="meteor-count" data-testid="meteor-count">
        {days === null
          ? "☄ A meteor is coming"
          : `☄ ${days} ${days === 1 ? "day" : "days"} until the meteor`}
      </p>
      <div
        className="meteor-track"
        role="progressbar"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div className="meteor-fill" style={{ width: `${pct}%` }} />
      </div>
      <p className="meteor-caption">
        {dayHour ? `${dayHour} into the cycle · ` : ""}
        {pct}% of the way there
      </p>
    </section>
  )
}
