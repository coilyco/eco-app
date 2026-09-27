import { useMemo } from "react"
import { useParams } from "react-router-dom"
import ItemLink from "../components/ItemLink"
import FreshnessNote from "../components/FreshnessNote"
import ItemPrice from "../components/ItemPrice"
import Layout from "../components/Layout"
import {
  formatCount,
  formatEventDay,
  formatFetchedAt,
  formatRelativeTime,
  prettifyEcoName,
} from "../lib/format"
import {
  fetchLogistics,
  type GapReason,
  type LogisticsBoard,
  type ShelfOffer,
} from "../lib/logisticsApi"
import { fetchStores, type StoreDirectory } from "../lib/storesApi"
import { useFreshData } from "../lib/useFreshData"
import {
  decodeUserHex,
  fetchUserDossier,
  type UserDossier,
} from "../lib/usersApi"

// Supply-gap severity labels, mirroring the /trade board's glyph+label so the
// dossier's "what to make" opener reads the same as the market page. Colour is
// carried alongside the glyph, never alone (the dataviz non-negotiable).
const GAP: Record<GapReason, { glyph: string; label: string; color: string }> = {
  no_supply: { glyph: "✖", label: "no supply", color: "var(--meteor)" },
  thin_supply: { glyph: "◐", label: "thin supply", color: "var(--meteor-deep)" },
  overpriced: { glyph: "▲", label: "over-priced", color: "var(--ink-faint)" },
}

// A labelled stat tile, mirroring the /map stat grids.
function Stat({ value, label, detail }: { value: string; label: string; detail?: string }) {
  return (
    <div className="stat">
      <p className="stat-value">{value}</p>
      <p className="stat-label">{label}</p>
      {detail && <p className="stat-detail">{detail}</p>}
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h2 className="section-title">{title}</h2>
      {children}
    </section>
  )
}

// The hidden per-user dossier at /users/<hex> (eco-app#80). <hex> is the base16
// of the username; we decode it, fetch every per-user field the exporters
// carry, and render each surface as its own panel that degrades on its own.
export default function User() {
  const { hex = "" } = useParams()

  // Decode the base16 path segment to the username before any fetch. A
  // malformed segment is a bad link, surfaced distinctly from a fetch failure.
  const decoded = useMemo(() => {
    try {
      return { username: decodeUserHex(hex), badHex: false }
    } catch {
      return { username: "", badHex: true }
    }
  }, [hex])

  // Refresh contract lives in freshness.ts, not here (eco-app#201). `hex` is
  // in the deps, so the dossier always belongs to the user in the URL.
  //
  // The actionable summary reads the same market spine the /trade page and the
  // item pages use (logistics = live shelf offers + supply gaps, stores = the
  // per-trader footprint). Both are best-effort and independent of the dossier:
  // a missing shelf/stores exporter just thins the summary, it never sinks the
  // page, so each resolves to null on a miss like the /trade planes do.
  const userPlane = useFreshData(
    "user",
    async (signal) => {
      if (decoded.badHex) return null
      const [dossier, logistics, stores] = await Promise.all([
        fetchUserDossier(decoded.username, signal),
        fetchLogistics(signal).catch(() => null),
        fetchStores(signal).catch(() => null),
      ])
      return { dossier, logistics, stores }
    },
    [hex],
  )
  const dossier: UserDossier | null = userPlane.data?.dossier ?? null
  const logistics: LogisticsBoard | null = userPlane.data?.logistics ?? null
  const stores: StoreDirectory | null = userPlane.data?.stores ?? null
  const error = userPlane.error

  const username = decoded.username

  // --- Actionable summary (eco-app#93) -------------------------------------
  // Every memo below is declared before the bad-hex early return so the hook
  // order is stable; on a bad link `username` is "" and each falls out empty.

  // Live open offers on the market spine, owner-matched. cheapest carries sell
  // offers and resale the buy offers, but every row's offers[] tag their own
  // side, so we union them and split by side. Deduped per shelf line.
  const myOffers = useMemo(() => {
    const empty = { sells: [] as ShelfOffer[], buys: [] as ShelfOffer[] }
    if (!logistics || !username) return empty
    const all = [
      ...logistics.cheapest.flatMap((r) => r.offers),
      ...logistics.resale.flatMap((r) => r.offers),
    ]
    const seen = new Set<string>()
    const mine = all.filter((o) => {
      if (o.owner !== username) return false
      const key = `${o.item}|${o.side}|${o.store}|${o.price}`
      if (seen.has(key)) return false
      seen.add(key)
      return true
    })
    return {
      sells: mine.filter((o) => o.side === "sell"),
      buys: mine.filter((o) => o.side === "buy"),
    }
  }, [logistics, username])

  // The per-trade footprint from the stores directory — the fallback when the
  // live shelf is reset-gated (no current offers), and a richer "what they
  // move" view than the leaderboards alone.
  const trader = useMemo(
    () => stores?.traders.find((t) => t.name === username) ?? null,
    [stores, username],
  )

  // What this player is best positioned to make: their highest-level
  // specialties (what they can craft) crossed against the market's current
  // supply gaps (what it will pay for). Specialties come from the dossier's
  // own jobs surface; the gaps from the same logistics board /trade shows.
  const topSpecialties = useMemo(() => {
    const specs = dossier?.jobs?.specialties ?? []
    return [...specs].sort((a, b) => b.level - a.level).slice(0, 6)
  }, [dossier])
  const marketGaps = useMemo(() => (logistics ? logistics.supplyGaps.slice(0, 5) : []), [logistics])

  // Recent activity in relative time, not bare day floats: the player's own
  // trades, newest first, phrased "3 hours ago" against the latest of them.
  const recentActivity = useMemo(() => {
    const timed = (dossier?.trades?.trades ?? []).filter((t) => typeof t.time === "number")
    if (timed.length === 0) return { latest: 0, items: [] as typeof timed }
    const latest = Math.max(...timed.map((t) => t.time as number))
    const items = [...timed].sort((a, b) => (b.time as number) - (a.time as number)).slice(0, 6)
    return { latest, items }
  }, [dossier])

  const hasSummary =
    myOffers.sells.length > 0 ||
    myOffers.buys.length > 0 ||
    (trader?.topSells.length ?? 0) > 0 ||
    (trader?.topBuys.length ?? 0) > 0 ||
    topSpecialties.length > 0 ||
    marketGaps.length > 0 ||
    recentActivity.items.length > 0

  if (decoded.badHex) {
    return (
      <Layout>
        <section className="hero hero-compact">
          <p className="hero-kicker">User dossier</p>
          <h1 className="hero-title">Not a valid user link</h1>
          <p className="empty-note" data-testid="user-bad-hex">
            The <code>/users/&lt;hex&gt;</code> segment must be the base16 of a username.
          </p>
        </section>
      </Layout>
    )
  }

  return (
    <Layout fetchedAtISO={dossier?.fetchedAtISO}>
      <section className="hero hero-compact">
        <p className="hero-kicker">User dossier</p>
        <h1 className="hero-title">
          <span className="accent" data-testid="user-name">
            {username}
          </span>
        </h1>
        {dossier?.jobs && (
          <p className="hero-pill" data-testid="user-pill">
            <span className="pulse-dot" aria-hidden="true" />
            {dossier.jobs.active ? "active" : "inactive"}
            {dossier.jobs.lastSeenISO
              ? ` · last seen ${formatFetchedAt(dossier.jobs.lastSeenISO)}`
              : " · never logged in"}
          </p>
        )}
        {!dossier && error && (
          <p className="hero-pill hero-pill-muted" data-testid="user-error">
            dossier unavailable right now
          </p>
        )}
        <p className="redaction-note">
          Every field the server exports about one player, pivoted into one place. Hidden by design
          — no nav link, no password.
        </p>
        <FreshnessNote
          plane="user"
          loadedAt={userPlane.loadedAt}
          refreshing={userPlane.refreshing}
          refreshError={userPlane.refreshError}
          onRefresh={userPlane.refresh}
        />
      </section>

      {dossier && !dossier.found && (
        <section>
          <p className="empty-note" data-testid="user-not-found">
            No exported data mentions <strong>{username}</strong> yet. They may not have played, or
            the exporters that would carry their activity are unavailable.
          </p>
        </section>
      )}

      {/* Actionable summary (eco-app#93) — leads the dossier, orienting around
          what *this* player would want at a glance: what they're trading now,
          what they're best positioned to make, and what they've done lately.
          The full per-surface panels below stay unchanged. */}
      {dossier?.found && hasSummary && (
        <Section title="At a glance">
          <p className="section-sub">
            What {username} is trading, best positioned to make, and up to lately.
          </p>

          <div className="atlas-columns" data-testid="user-summary-offers">
            <div>
              <h3 className="card-title">Selling now</h3>
              {myOffers.sells.length > 0 ? (
                <ul className="rank-rows" data-testid="user-selling">
                  {myOffers.sells.map((o, i) => (
                    <li key={`sell-${o.item}-${o.store}-${i}`}>
                      <div className="rank-row">
                        <ItemLink className="rank-name linklike" item={o.item}>
                          {o.itemPretty || prettifyEcoName(o.item)}
                        </ItemLink>
                        <span className="rank-count">
                          <ItemPrice
                            price={o.price}
                            norm={o.norm}
                            currency={o.currency}
                            showCurrency
                            suffix={`, ${formatCount(o.quantity)} in stock`}
                          />
                        </span>
                      </div>
                    </li>
                  ))}
                </ul>
              ) : (trader?.topSells.length ?? 0) > 0 ? (
                <>
                  <p className="section-sub">No live shelf — recently sold:</p>
                  <ul className="rank-rows" data-testid="user-selling-recent">
                    {trader!.topSells.slice(0, 6).map((it) => (
                      <li key={`ts-${it.item}`}>
                        <div className="rank-row">
                          <ItemLink className="rank-name linklike" item={it.item}>
                            {it.pretty || prettifyEcoName(it.item)}
                          </ItemLink>
                          <span className="rank-count">{formatCount(it.volume)} volume</span>
                        </div>
                      </li>
                    ))}
                  </ul>
                </>
              ) : (
                <p className="empty-note">No open sell offers on the market spine.</p>
              )}
            </div>
            <div>
              <h3 className="card-title">Buying now</h3>
              {myOffers.buys.length > 0 ? (
                <ul className="rank-rows" data-testid="user-buying">
                  {myOffers.buys.map((o, i) => (
                    <li key={`buy-${o.item}-${o.store}-${i}`}>
                      <div className="rank-row">
                        <ItemLink className="rank-name linklike" item={o.item}>
                          {o.itemPretty || prettifyEcoName(o.item)}
                        </ItemLink>
                        <span className="rank-count">
                          <ItemPrice
                            price={o.price}
                            norm={o.norm}
                            currency={o.currency}
                            showCurrency
                            suffix={`, wants ${formatCount(o.quantity)}`}
                          />
                        </span>
                      </div>
                    </li>
                  ))}
                </ul>
              ) : (trader?.topBuys.length ?? 0) > 0 ? (
                <>
                  <p className="section-sub">No live orders — recently bought:</p>
                  <ul className="rank-rows" data-testid="user-buying-recent">
                    {trader!.topBuys.slice(0, 6).map((it) => (
                      <li key={`tb-${it.item}`}>
                        <div className="rank-row">
                          <ItemLink className="rank-name linklike" item={it.item}>
                            {it.pretty || prettifyEcoName(it.item)}
                          </ItemLink>
                          <span className="rank-count">{formatCount(it.volume)} volume</span>
                        </div>
                      </li>
                    ))}
                  </ul>
                </>
              ) : (
                <p className="empty-note">No open buy orders on the market spine.</p>
              )}
            </div>
          </div>

          {(topSpecialties.length > 0 || marketGaps.length > 0) && (
            <>
              <h3 className="card-title">Best positioned to make &amp; sell</h3>
              {topSpecialties.length > 0 ? (
                <p className="intro" data-testid="user-strengths">
                  <span>
                    Highest specialties:{" "}
                    {topSpecialties.map((s) => `${s.specialty} (lvl ${s.level})`).join(", ")}
                  </span>
                </p>
              ) : (
                <p className="empty-note">No high-level specialties recorded yet.</p>
              )}
              {marketGaps.length > 0 && (
                <>
                  <p className="section-sub">
                    What the market is short on right now — a skilled crafter&rsquo;s opening:
                  </p>
                  <ul className="gap-list" data-testid="user-gaps">
                    {marketGaps.map((g) => {
                      const tag = GAP[g.reason]
                      return (
                        <li className="gap-row" key={`${g.item}-${g.currency}`}>
                          <div className="gap-head">
                            <ItemLink className="gap-name linklike" item={g.item}>
                              {g.itemPretty}
                            </ItemLink>
                            <span className="gap-tag" style={{ color: tag.color }}>
                              <span aria-hidden="true">{tag.glyph}</span> {tag.label}
                            </span>
                          </div>
                          <p className="gap-summary">
                            {formatCount(g.demandQty)} wanted · {formatCount(g.buyerCount)} buyer
                            {g.buyerCount === 1 ? "" : "s"} · {formatCount(g.sellerCount)} seller
                            {g.sellerCount === 1 ? "" : "s"}
                          </p>
                        </li>
                      )
                    })}
                  </ul>
                </>
              )}
            </>
          )}

          {recentActivity.items.length > 0 && (
            <>
              <h3 className="card-title">Recent activity</h3>
              <ul className="warn-list" data-testid="user-recent">
                {recentActivity.items.map((t, i) => {
                  const role = t.buyer === username ? "bought" : "sold"
                  const counterparty = t.buyer === username ? t.seller : t.buyer
                  return (
                    <li key={`recent-${i}`}>
                      {formatRelativeTime(t.time as number, recentActivity.latest)} — {role}{" "}
                      <strong>
                        <ItemLink item={t.item}>{t.item ? prettifyEcoName(t.item) : "an item"}</ItemLink>
                      </strong>
                      {counterparty ? ` with ${counterparty}` : ""}
                    </li>
                  )
                })}
              </ul>
            </>
          )}
        </Section>
      )}

      {dossier?.jobs && (
        <Section title="Skills & jobs">
          {dossier.jobs.specialties.length === 0 ? (
            <p className="empty-note">No learned specialties.</p>
          ) : (
            <table tabIndex={0} className="ledger-table" data-testid="user-specialties">
              <thead>
                <tr>
                  <th>Specialty</th>
                  <th>Level</th>
                  <th>Active</th>
                </tr>
              </thead>
              <tbody>
                {dossier.jobs.specialties.map((s) => (
                  <tr key={s.specialty}>
                    <td>{s.specialty}</td>
                    <td>{s.level}</td>
                    <td>{s.active ? "yes" : "no"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Section>
      )}

      {dossier?.trades && (
        <Section title="Trades">
          <div className="stats">
            <Stat value={formatCount(dossier.trades.currencySpent)} label="Spent as buyer" />
            <Stat value={formatCount(dossier.trades.currencyEarned)} label="Earned as seller" />
            <Stat value={formatCount(dossier.trades.trades.length)} label="Trades on record" />
          </div>
          {dossier.trades.trades.length > 0 && (
            <table tabIndex={0} className="ledger-table" data-testid="user-trades">
              <thead>
                <tr>
                  <th>When</th>
                  <th>Item</th>
                  <th>Role</th>
                  <th>Counterparty</th>
                  <th>Amount</th>
                </tr>
              </thead>
              <tbody>
                {dossier.trades.trades.slice(0, 100).map((t, i) => {
                  const role = t.buyer === username ? "bought" : "sold"
                  const counterparty = t.buyer === username ? t.seller : t.buyer
                  return (
                    <tr key={`${t.item}-${i}`} data-testid="user-trade-row">
                      <td>{formatEventDay(t.day)}</td>
                      <td>{t.item ? prettifyEcoName(t.item) : "—"}</td>
                      <td>{role}</td>
                      <td>{counterparty}</td>
                      <td>
                        {t.currencyAmount != null
                          ? `${formatCount(t.currencyAmount)} ${t.currency ?? ""}`
                          : "—"}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          )}
        </Section>
      )}

      {dossier?.currency && (
        <Section title="Currency holdings">
          <table tabIndex={0} className="ledger-table" data-testid="user-holdings">
            <thead>
              <tr>
                <th>Currency</th>
                <th>Balance</th>
                <th>Account</th>
              </tr>
            </thead>
            <tbody>
              {dossier.currency.holdings.map((h) => (
                <tr key={`${h.currency}-${h.account}`}>
                  <td>{h.currency}</td>
                  <td>{formatCount(h.balance)}</td>
                  <td>{h.account}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>
      )}

      {(dossier?.crafting || dossier?.world) && (
        <Section title="Production & world">
          <div className="stats">
            {dossier.crafting && (
              <Stat value={formatCount(dossier.crafting.events)} label="Items crafted" />
            )}
            {dossier.world && (
              <>
                <Stat
                  value={formatCount(dossier.world.shaperEvents)}
                  label="World-shaping events"
                />
                <Stat
                  value={formatCount(dossier.world.pollutionEvents)}
                  label="Pollution events"
                />
              </>
            )}
          </div>
        </Section>
      )}

      {dossier?.civics && (
        <Section title="Civics">
          <div className="stats">
            <Stat value={formatCount(dossier.civics.votesCast)} label="Votes cast" />
            <Stat
              value={formatCount(dossier.civics.elections.length)}
              label="Elections proposed / won"
            />
            <Stat
              value={formatCount(dossier.civics.settlements.length)}
              label="Settlements founded"
            />
          </div>
          {(dossier.civics.elections.length > 0 || dossier.civics.settlements.length > 0) && (
            <ul className="warn-list" data-testid="user-civics-events">
              {dossier.civics.elections.map((e, i) => (
                <li key={`el-${i}`}>
                  {formatEventDay(e.day)}: {e.role} <strong>{e.subject}</strong>
                </li>
              ))}
              {dossier.civics.settlements.map((s, i) => (
                <li key={`st-${i}`}>
                  {formatEventDay(s.day)}: founded {s.kind} <strong>{s.subject}</strong>
                </li>
              ))}
            </ul>
          )}
        </Section>
      )}

      {dossier?.progression && (
        <Section title="Progression">
          <div className="stats">
            <Stat value={formatCount(dossier.progression.levelUpCount)} label="Level-ups" />
            {dossier.progression.trajectory?.characterLevel != null && (
              <Stat
                value={formatCount(dossier.progression.trajectory.characterLevel)}
                label="Character level"
              />
            )}
            {dossier.progression.trajectory && (
              <Stat
                value={formatCount(dossier.progression.trajectory.eventCount ?? 0)}
                label="Progression events"
              />
            )}
          </div>
          {dossier.progression.trajectory?.professions &&
            dossier.progression.trajectory.professions.length > 0 && (
              <p className="intro" data-testid="user-professions">
                <span>
                  Professions:{" "}
                  {dossier.progression.trajectory.professions.map((p) => p.pretty).join(", ")}
                </span>
              </p>
            )}
          {dossier.progression.trajectory?.timeline &&
            dossier.progression.trajectory.timeline.length > 0 && (
              <table tabIndex={0} className="ledger-table" data-testid="user-timeline">
                <thead>
                  <tr>
                    <th>When</th>
                    <th>Event</th>
                    <th>Skill</th>
                    <th>Level</th>
                  </tr>
                </thead>
                <tbody>
                  {dossier.progression.trajectory.timeline.map((ev, i) => (
                    <tr key={`tl-${i}`}>
                      <td>{formatEventDay(ev.day)}</td>
                      <td>{ev.kind}</td>
                      <td>{ev.pretty}</td>
                      <td>{ev.level ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
        </Section>
      )}

      {dossier && dossier.warnings.length > 0 && (
        <section>
          <ul className="warn-list" data-testid="user-warnings">
            {dossier.warnings.map((w) => (
              <li key={w}>⚠ {w}</li>
            ))}
          </ul>
        </section>
      )}
    </Layout>
  )
}
