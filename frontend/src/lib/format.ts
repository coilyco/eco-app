// Formatting helpers for raw Eco server payloads.

// Eco's /info strings carry Unity-style rich text and Eco's own UI markup:
// <color=green>, <style="Culture">, <icon name="Culture" type="nobg">, <b>.
// The web face wants plain text.
const ECO_MARKUP = /<\/?(?:color|style|icon|b|i|size|link)\b[^>]*>/gi

export function stripEcoMarkupInline(text: string): string {
  return text.replace(ECO_MARKUP, "")
}

export function stripEcoMarkup(text: string): string {
  return stripEcoMarkupInline(text).replace(/[ \t]+\n/g, "\n").trim()
}

// An unreported upstream field arrives as null (eco-app#214). It renders as a
// dash so a reader can tell "the server did not say" from a real zero.
export const UNREPORTED = "—"

const MONEY = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 })

// An item's unit price. Only components/ItemPrice calls this, so every item
// price carries its historical norm (eco-app#8368, enforced by test/priceNorms.test.ts).
export function formatPrice(n: number): string {
  return MONEY.format(n)
}

// Any other money: spreads, totals, margins, rates and chart axis labels.
export function formatMoney(n: number): string {
  return MONEY.format(n)
}

// A non-money quantity that can be fractional, such as craft minutes.
export function formatDecimal(n: number): string {
  return MONEY.format(n)
}

export function formatCount(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return UNREPORTED
  return new Intl.NumberFormat("en-US").format(Math.round(n))
}

// "2026-06-12T11:46:33.611416+00:00" -> "11:46 UTC"
export function formatFetchedAt(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ""
  const hh = String(d.getUTCHours()).padStart(2, "0")
  const mm = String(d.getUTCMinutes()).padStart(2, "0")
  return `${hh}:${mm} UTC`
}

// Age of an event as coarse relative time, e.g. "1 hour ago", "3 days ago".
// Both args are the exporter's `Time` (in-game seconds; one in-game day = 86400s,
// the species-CSV convention social.py folds by), so the gap is a real elapsed
// span. `latest` is the newest event on the surface — the reference "now", since
// `Time` is server seconds, not epoch. Clamped at 0 so clock skew never reads as
// the future. Mirrors the day = Time / 86400 scale used across the social feed.
export function formatRelativeTime(timeS: number, latest: number): string {
  const sec = Math.max(0, Math.round(latest - timeS))
  if (sec < 45) return "just now"
  const units: Array<[number, string]> = [
    [86400, "day"],
    [3600, "hour"],
    [60, "minute"],
  ]
  for (const [size, name] of units) {
    if (sec >= size) {
      const n = Math.round(sec / size)
      return `${n} ${name}${n === 1 ? "" : "s"} ago`
    }
  }
  return `${sec} seconds ago`
}

// A coarse duration, e.g. "3 minutes", "2 hours", "moments". Used for the
// compressed-feed "(N crafts over <span>)" label, where span is how long a run
// of identical events took (newest − oldest, in-game seconds). No "ago" suffix,
// unlike formatRelative.
export function formatDuration(seconds: number): string {
  const sec = Math.max(0, Math.round(seconds))
  if (sec < 45) return "moments"
  const units: Array<[number, string]> = [
    [86400, "day"],
    [3600, "hour"],
    [60, "minute"],
  ]
  for (const [size, name] of units) {
    if (sec >= size) {
      const n = Math.round(sec / size)
      return `${n} ${name}${n === 1 ? "" : "s"}`
    }
  }
  return `${sec} seconds`
}

// The world clock. Eco's /info exposes `TimeSinceStart` as real seconds since
// the cycle began, and one in-game *day* is 3600 of those seconds — server.py's
// convention, where `DaysRunning` is just floor(TimeSinceStart / 3600). Each day
// holds 24 in-game hours, so one in-game hour is 150 seconds. These constants
// are the calendar the day+hour helper folds against; they are deliberately
// distinct from the 86400s "day" the species-CSV social feed uses in
// formatRelativeTime above.
const WORLD_SECONDS_PER_DAY = 3600
const WORLD_HOURS_PER_DAY = 24

// A world-clock timestamp (in-game seconds since cycle start) rendered as
// "day D, Hh". Kai's rule for the site: every place that names a day also names
// the hour, so one helper renders both from a single seconds value. Drives the
// meteor banner's "into the cycle" caption from the /info TimeSinceStart.
//
// NOTE (eco-app#93): this is the /info world clock (3600s/day). The exporter
// CSVs (trades, civics, progression) count on a *different* calendar — Time /
// 86400 = day (see trades.SECONDS_PER_DAY) — so their pre-divided float "day"
// fields render through `formatEventDay` below, not this. Reconciling the two
// clocks into one helper is a follow-up for the site-wide day+hour sweep.
export function formatDayHour(timeSeconds: number): string {
  const t = Math.max(0, timeSeconds)
  const day = Math.floor(t / WORLD_SECONDS_PER_DAY)
  const withinDay = (t % WORLD_SECONDS_PER_DAY) / WORLD_SECONDS_PER_DAY
  const hour = Math.floor(withinDay * WORLD_HOURS_PER_DAY)
  return `day ${day}, ${hour}h`
}

// Coarse "how long ago" for a world-clock timestamp against a reference `now`,
// both in the same in-game seconds as formatDayHour. The gap is *real* elapsed
// time (TimeSinceStart counts real seconds), so this folds by real 60/3600/86400
// units for a human reading — the in-game 3600s/day calendar only governs which
// day+hour a timestamp lands on, not how long ago it was. Clamped at 0 so clock
// skew never reads as the future. The item and user surfaces consume this.
export function formatRelative(timeSeconds: number, nowSeconds: number): string {
  const sec = Math.max(0, Math.round(nowSeconds - timeSeconds))
  if (sec < 45) return "just now"
  const units: Array<[number, string]> = [
    [86400, "day"],
    [3600, "hour"],
    [60, "minute"],
  ]
  for (const [size, name] of units) {
    if (sec >= size) {
      const n = Math.round(sec / size)
      return `${n} ${name}${n === 1 ? "" : "s"} ago`
    }
  }
  return `${sec} seconds ago`
}

// The exporter-day clock, for the pre-divided float "day" the trades / civics /
// progression exporters emit (day = Time / 86400, trades.SECONDS_PER_DAY). A raw
// float ("Day 3.5127") reads as a bug, so every day-bearing dossier row shows the
// whole day AND the clock time folded out of the fraction (eco-app#93). Distinct
// from `formatDayHour` above, which folds the /info world clock's 3600s/day — the
// two exporter/world calendars differ, so they cannot share one helper until the
// site-wide sweep reconciles them.
export function formatEventDay(day: number | null | undefined): string {
  if (day == null || !Number.isFinite(day)) return "—"
  const whole = Math.floor(day)
  const minutesOfDay = Math.round((day - whole) * 24 * 60)
  // Rounding can push 23:59:30+ up to a full day — carry it so 3.9999 reads
  // "Day 4, 00:00", never "Day 3, 24:00".
  const dayNum = whole + Math.floor(minutesOfDay / (24 * 60))
  const rem = minutesOfDay % (24 * 60)
  const hh = String(Math.floor(rem / 60)).padStart(2, "0")
  const mm = String(rem % 60).padStart(2, "0")
  return `Day ${dayNum}, ${hh}:${mm}`
}

// "BunWulfRawMeatItem" -> "Bun Wulf Raw Meat", "OakSpecies" -> "Oak".
// Mirrors prettify_eco_name in eco_mcp_app/crafting.py.
export function prettifyEcoName(raw: string): string {
  let base = raw
  for (const suffix of ["Item", "Species"]) {
    if (base.endsWith(suffix) && base.length > suffix.length) {
      base = base.slice(0, -suffix.length)
    }
  }
  return base.replace(/(?<!^)(?=[A-Z])/g, " ").trim() || raw
}

// The discord link arrives from the game server's /info payload, i.e. from
// outside this codebase. Only let real web URLs through to href.
export function safeHttpUrl(url: string | null | undefined): string | undefined {
  if (!url) return undefined
  try {
    const parsed = new URL(url)
    return parsed.protocol === "https:" || parsed.protocol === "http:"
      ? parsed.toString()
      : undefined
  } catch {
    return undefined
  }
}

// Cycle progress toward the meteor, as a 0-100 integer. The payload gives
// days elapsed and days remaining, so the total is their sum. Either side may
// be unreported (eco-app#214), in which case there is no progress to draw.
export function meteorProgressPercent(
  daysRunning: number | null,
  daysUntilMeteor: number | null,
): number {
  if (daysRunning === null || daysUntilMeteor === null) return 0
  const total = daysRunning + daysUntilMeteor
  if (total <= 0) return 0
  return Math.min(100, Math.max(0, Math.round((daysRunning / total) * 100)))
}
