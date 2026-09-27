import { useMemo, useState } from "react"
import ChartFrame from "../components/ChartFrame"
import type { ReactNode } from "react"
import { Link } from "react-router-dom"
import EcoRichText from "../components/EcoRichText"
import FreshnessNote from "../components/FreshnessNote"
import Layout from "../components/Layout"
import { useJobsData } from "../hooks/useJobsData"
import { fetchLogistics, type GapReason } from "../lib/logisticsApi"
import { fetchMarket } from "../lib/marketApi"
import { formatCount, prettifyEcoName } from "../lib/format"
import {
  fetchRecipeIndexWithCost,
  type RecipeSkillDef,
} from "../lib/recipesApi"
import { fetchTradesLedger } from "../lib/tradesApi"
import type { ProfessionStat, SpecialtyStat } from "../lib/jobsApi"
import { useFreshData } from "../lib/useFreshData"
import {
  fetchProgressionHistory,
  KIND_LABELS,
  TREND_ORDER,
} from "../lib/progressionApi"

// Survivalist and Self Improvement are the universal starter skills — every
// citizen has them, so they carry no signal and only clutter the roster
// (eco-app#94). We filter them out of every jobs surface (professions,
// specialties, per-player skill lists, and the progression rank lists) in one
// place here. Matching on the prettified, whitespace-collapsed name catches
// both the jobs API's display names ("Self Improvement") and the progression
// endpoint's raw Eco ids ("SelfImprovement", "SurvivalistSkill").
const UNIVERSAL_SKILLS = new Set(["self improvement", "survivalist"])
const VALUE_ROWS = 5
const LIQUIDITY_FLOOR = 100

const GAP: Record<GapReason, { glyph: string; label: string; color: string }> = {
  no_supply: { glyph: "✖", label: "no supply", color: "var(--meteor)" },
  thin_supply: { glyph: "◐", label: "thin supply", color: "var(--meteor-deep)" },
  overpriced: { glyph: "▲", label: "over-priced", color: "var(--ink-faint)" },
}

function fmtPrice(n: number): string {
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 }).format(n)
}

function opportunityHref(
  item: string,
  gap: { demandQty: number; reason: GapReason },
  margin: number | null,
  confidence: "complete" | "incomplete",
): string {
  const params = new URLSearchParams({
    item,
    source: "jobs",
    demandQty: String(gap.demandQty),
    demandReason: gap.reason,
    confidence,
  })
  if (margin !== null) params.set("margin", String(margin))
  return `/uses/price?${params.toString()}`
}

function isUniversalSkill(name: string): boolean {
  const norm = prettifyEcoName(name)
    .toLowerCase()
    .replace(/\bskill\b/g, "")
    .replace(/\s+/g, " ")
    .trim()
  return UNIVERSAL_SKILLS.has(norm)
}

interface RankRow {
  key: string
  name: string
  count: number
  note?: ReactNode
}

interface ValueRow {
  key: string
  item: string
  href: string
  name: string
  score: number
  confidence: "complete" | "incomplete"
  note: ReactNode
}

type RankedRow = RankRow | ValueRow

interface ProfessionValueBoard {
  key: string
  label: string
  rows: ValueRow[]
}

interface SkillTree {
  key: string
  label: string
  specialties: RecipeSkillDef[]
}

function SkillTreeCard({ tree }: { tree: SkillTree }) {
  return (
    <section className="skill-tree card" data-testid="skill-tree">
      <h3 className="card-title skill-tree-root">{tree.label}</h3>
      <ul className="skill-tree-branches">
        {tree.specialties.map((skill) => {
          const talents = skill.talents ?? []
          return (
          <li key={skill.name}>
            <details className="skill-tree-specialty">
              <summary>
                <span>{skill.displayName}</span>
                <span className="section-sub">
                  level {skill.maxLevel}
                  {talents.length > 0 && ` // ${formatCount(talents.length)} talents`}
                </span>
              </summary>
              {talents.length === 0 ? (
                <p className="empty-note">No talent branches recorded for this specialty.</p>
              ) : (
                <ul className="skill-tree-talents">
                  {talents.map((talent) => (
                    <li key={talent.name}>
                      <span className="pill pill-active">level {talent.level}</span>
                      <span>
                        <strong>{talent.displayName}</strong>
                        {talent.description && (
                          <span className="skill-tree-description">{talent.description}</span>
                        )}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </details>
          </li>
          )
        })}
      </ul>
    </section>
  )
}

const COVERAGE_ROLES = new Set(["Active", "Long Term"])

function coveredByRole(roles: string[]): boolean {
  return roles.some((role) => COVERAGE_ROLES.has(role))
}

function RoleBadges({ roles }: { roles: string[] }) {
  const visible = roles.filter((role) => COVERAGE_ROLES.has(role))
  return visible.map((role) => (
    <span className="pill pill-active" key={role}>
      {role}
    </span>
  ))
}

function ProfessionCard({
  stat,
  rolesByPlayer,
  showAllPeople,
}: {
  stat: ProfessionStat
  rolesByPlayer: ReadonlyMap<string, string[]>
  showAllPeople: boolean
}) {
  const [open, setOpen] = useState(false)
  const visiblePlayers = stat.players.filter(
    (player) => showAllPeople || coveredByRole(rolesByPlayer.get(player) ?? []),
  )
  const uncovered = stat.total > 0 && stat.covered === 0
  return (
    <li className={`card card-tight${stat.total === 0 ? " dim" : ""}`}>
      <button className="prof-btn" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <span>{stat.profession}</span>
        <span className="count">
          ( {stat.covered} / {stat.total} covered )
        </span>
      </button>
      {uncovered && (
        <p className="warn-note" data-testid="uncovered-job">
          ⚠ No Active or Long Term holder
        </p>
      )}
      {open && (
        <div className="detail">
          {visiblePlayers.length > 0 ? (
            <ul className="rows">
              {visiblePlayers.map((p) => (
                <li key={p} className={coveredByRole(rolesByPlayer.get(p) ?? []) ? "role-holder" : "faded"}>
                  <span><EcoRichText text={p} /></span>
                  <span className="role-badges">
                    <RoleBadges roles={rolesByPlayer.get(p) ?? []} />
                  </span>
                </li>
              ))}
            </ul>
          ) : stat.players.length > 0 ? (
            <p className="empty-note">People outside Active and Long Term are hidden.</p>
          ) : (
            <p className="empty-note">No players with specialties in this profession.</p>
          )}
        </div>
      )}
    </li>
  )
}

function SpecialtyCard({ stat, showAllPeople }: { stat: SpecialtyStat; showAllPeople: boolean }) {
  const visibleHolders = stat.holders.filter((holder) => showAllPeople || coveredByRole(holder.roles))
  const uncovered = stat.total > 0 && stat.covered === 0
  return (
    <li className={`card${stat.total === 0 ? " dim" : ""}`}>
      <h3 className="card-title">
        {stat.specialty}
        <span className="count">
          ( {stat.covered} / {stat.total} covered )
        </span>
      </h3>
      <p className="kicker">{stat.profession}</p>
      {uncovered && (
        <p className="warn-note" data-testid="uncovered-specialty">
          ⚠ No Active or Long Term holder
        </p>
      )}
      <ul className="rows">
        {visibleHolders.map((h) => (
          <li key={h.player} className={coveredByRole(h.roles) ? "role-holder" : "faded"}>
            <span><EcoRichText text={h.player} /></span>
            <span className="role-badges">
              <RoleBadges roles={h.roles} />
              <span className="lvl">lvl {h.level}</span>
            </span>
          </li>
        ))}
      </ul>
      {visibleHolders.length === 0 && stat.holders.length > 0 && (
        <p className="empty-note">People outside Active and Long Term are hidden.</p>
      )}
    </li>
  )
}

// One small-multiple sparkline of a single progression trend series (events of
// one kind per in-game day). Small multiples — one single-hue chart per kind —
// deliberately sidestep the multi-series categorical-color problem: every panel
// reads on the same --leaf hue, and its title names the single series (no legend
// box needed). Folded into /jobs from the former /progression page (eco-app#90).
function TrendSparkline({
  label,
  points,
}: {
  label: string
  points: Array<[number, number]>
}) {
  const width = 300
  const height = 96
  const pad = 16
  const total = points.reduce((sum, [, c]) => sum + c, 0)

  let body
  if (points.length < 2) {
    // A single day (or none) can't draw a line; show the headline count instead.
    body = (
      <p className="prog-trend-single" data-testid="trend-single">
        {formatCount(total)} total{points.length === 1 ? ` · day ${points[0][0]}` : ""}
      </p>
    )
  } else {
    const days = points.map(([d]) => d)
    const counts = points.map(([, c]) => c)
    const minDay = Math.min(...days)
    const maxDay = Math.max(...days)
    const maxCount = Math.max(...counts)
    const daySpan = maxDay - minDay || 1
    const countSpan = maxCount || 1
    const x = (d: number) => pad + ((d - minDay) / daySpan) * (width - 2 * pad)
    const y = (c: number) => height - pad - (c / countSpan) * (height - 2 * pad)
    const line = points.map(([d, c]) => `${x(d).toFixed(1)},${y(c).toFixed(1)}`).join(" ")
    const area = `${x(minDay).toFixed(1)},${(height - pad).toFixed(1)} ${line} ${x(
      maxDay,
    ).toFixed(1)},${(height - pad).toFixed(1)}`
    body = (
      <ChartFrame above={[`peak ${formatCount(maxCount)}`]} start={`day ${minDay}`} end={`day ${maxDay}`}>
      <svg
        className="prog-trend-chart"
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="xMidYMid meet"
        role="img"
        aria-label={`${KIND_LABELS[label] ?? label} per in-game day`}
        data-testid="trend-chart"
      >
        <polygon points={area} fill="var(--leaf-wash)" stroke="none" />
        <polyline points={line} fill="none" stroke="var(--leaf)" strokeWidth="2" />
      </svg>
      </ChartFrame>
    )
  }

  return (
    <div className="prog-trend" data-testid="trend-panel">
      <div className="prog-trend-title">
        {KIND_LABELS[label] ?? label}
        <span className="prog-trend-total">{formatCount(total)}</span>
      </div>
      {body}
    </div>
  )
}

// Ranked bar list of [name, count] pairs. `pretty` prettifies Eco skill ids;
// citizen-name lists (already resolved server-side) pass pretty={false}.
function RankList({
  rows,
  emptyNote,
  pretty = true,
  formatValue = formatCount,
}: {
  rows: RankedRow[]
  emptyNote: string
  pretty?: boolean
  formatValue?: (n: number) => string
}) {
  const top = rows.slice(0, 15)
  const valueFor = (row: RankedRow) => ("count" in row ? row.count : row.score)
  const max = Math.max(...top.map(valueFor), 1)
  if (top.length === 0) {
    return <p className="empty-note">{emptyNote}</p>
  }
  return (
    <ul className="rank-rows">
      {top.map((row) => (
        <li key={row.key}>
          <div className="rank-row" data-testid="rank-row">
            {"item" in row ? (
              <Link className="rank-name linklike" to={row.href} data-testid="opportunity-price-link">
                {row.name}
              </Link>
            ) : (
              <span className="rank-name">{pretty ? prettifyEcoName(row.name) : row.name}</span>
            )}
            <span className="rank-count">{formatValue(valueFor(row))}</span>
            <span className="rank-bar" style={{ width: `${(valueFor(row) / max) * 100}%` }} />
          </div>
          {row.note && <p className="section-sub">{row.note}</p>}
        </li>
      ))}
    </ul>
  )
}

function ValueTag({ reason }: { reason: GapReason }) {
  const tag = GAP[reason]
  return (
    <span className="gap-tag" style={{ color: tag.color }} data-testid="value-tag">
      <span aria-hidden="true">{tag.glyph}</span> {tag.label}
    </span>
  )
}

function makeRankRow(
  key: string,
  name: string,
  count: number,
  note?: ReactNode,
): RankRow {
  return { key, name, count, note }
}

export default function Jobs() {
  const { data, error, loading } = useJobsData()
  // Progression is the temporal layer of this page (eco-app#90): the current
  // roster shows who does what now, progression shows how they got there. It is
  // a best-effort enrichment — a failure leaves the current-state tables exactly
  // as they were before this surface existed, so we swallow errors.
  const [showAllPeople, setShowAllPeople] = useState(false)

  // Refresh contract lives in freshness.ts, not here (eco-app#201). The
  // progression layer and the value spine are one plane: both are enrichment
  // over the current-state tables, and a failure in either leaves those tables
  // exactly as they were before these surfaces existed.
  const jobsPlane = useFreshData("jobs", async (signal) => {
    const [progression, recipeIndex, logistics, market, trades] = await Promise.all([
      fetchProgressionHistory(signal).catch(() => null),
      fetchRecipeIndexWithCost(signal).catch(() => null),
      fetchLogistics(signal).catch(() => null),
      fetchMarket(signal).catch(() => null),
      fetchTradesLedger(signal).catch(() => null),
    ])
    return { progression, recipeIndex, logistics, market, trades }
  })
  const progression = jobsPlane.data?.progression ?? null
  const recipeIndex = jobsPlane.data?.recipeIndex ?? null
  const logistics = jobsPlane.data?.logistics ?? null
  const market = jobsPlane.data?.market ?? null
  const trades = jobsPlane.data?.trades ?? null
  const valueLoaded = !jobsPlane.loading


  const trendPanels = useMemo(() => {
    if (!progression) return []
    return TREND_ORDER.filter((kind) => (progression.trends[kind]?.length ?? 0) > 0).map(
      (kind) => ({ kind, points: progression.trends[kind] }),
    )
  }, [progression])

  const hasHistory = (progression?.totalEvents ?? 0) > 0

  // Drop universal starter skills from the current-state surfaces (eco-app#94).
  const professions = useMemo(
    () => (data?.professions ?? []).filter((s) => !isUniversalSkill(s.profession)),
    [data],
  )
  const specialties = useMemo(
    () => (data?.specialties ?? []).filter((s) => !isUniversalSkill(s.specialty)),
    [data],
  )
  const rolesByPlayer = useMemo(
    () => new Map((data?.players ?? []).map((player) => [player.name, player.roles] as const)),
    [data?.players],
  )

  const valueBoards = useMemo<ProfessionValueBoard[] | null>(() => {
    if (!recipeIndex || !logistics || !market || !trades) return null

    const marketMedians = new Map(market.markets.map((m) => [m.item, m.medianPrice] as const))
    const gaps = new Map(logistics.supplyGaps.map((g) => [g.item, g] as const))
    const liquidity = new Map(trades.byItem.map(([item, , volume]) => [item, volume] as const))
    const recipesByName = new Map(recipeIndex.recipes.map((r) => [r.name, r] as const))

    const boards = recipeIndex.skills
      .map((skill) => {
        const bestByItem = new Map<string, ValueRow>()
        for (const recipeName of recipeIndex.bySkill[skill.name] ?? []) {
          const recipe = recipesByName.get(recipeName)
          if (!recipe?.cost) continue
          const item = recipe.product.item
          const gap = gaps.get(item)
          const median = marketMedians.get(item)
          const traded = liquidity.get(item) ?? 0
          if (!gap || median == null || traded < LIQUIDITY_FLOOR || gap.demandQty <= 0) continue
          const complete = recipe.cost.complete && recipe.cost.perUnitCost != null
          const margin = complete ? median - recipe.cost.perUnitCost! : null
          if (margin !== null && margin <= 0) continue
          const boost = gap.reason === "no_supply" ? 1.5 : gap.reason === "thin_supply" ? 1.25 : 1.0
          const score = margin !== null ? margin * gap.demandQty * boost : gap.demandQty * boost
          const confidence = complete ? "complete" : "incomplete"
          const note = (
            <>
              <ValueTag reason={gap.reason} />{" "}
              <span>
                {margin !== null ? `estimated margin ${fmtPrice(margin)}` : "margin unavailable"} ·{" "}
                {formatCount(gap.demandQty)} observed demand ·{" "}
                {formatCount(traded)} traded volume
                {!complete && " · incomplete cost inputs, low confidence"}
              </span>
            </>
          )
          const current = bestByItem.get(item)
          if (
            !current ||
            (confidence === "complete" && current.confidence === "incomplete") ||
            (confidence === current.confidence && score > current.score)
          ) {
            bestByItem.set(item, {
              key: recipe.name,
              item,
              href: opportunityHref(item, gap, margin, confidence),
              name: recipe.product.displayName,
              score,
              confidence,
              note,
            })
          }
        }

        const rows = [...bestByItem.values()]
          .sort(
            (a, b) =>
              Number(a.confidence === "incomplete") - Number(b.confidence === "incomplete") ||
              b.score - a.score,
          )
          .slice(0, VALUE_ROWS)
        return rows.length > 0
          ? {
              key: skill.name,
              label: skill.displayName || prettifyEcoName(skill.name),
              rows,
            }
          : null
      })
      .filter((board): board is ProfessionValueBoard => board !== null)
      .sort((a, b) => (b.rows[0]?.score ?? 0) - (a.rows[0]?.score ?? 0) || a.label.localeCompare(b.label))

    return boards
  }, [recipeIndex, logistics, market, trades])

  const skillTrees = useMemo<SkillTree[]>(() => {
    if (!recipeIndex) return []
    const roots = new Map(
      recipeIndex.skills
        .filter((skill) => !skill.profession)
        .map((skill) => [skill.name, skill] as const),
    )
    const byProfession = new Map<string, RecipeSkillDef[]>()
    for (const skill of recipeIndex.skills) {
      if (!skill.profession || isUniversalSkill(skill.name)) continue
      const siblings = byProfession.get(skill.profession) ?? []
      siblings.push(skill)
      byProfession.set(skill.profession, siblings)
    }
    return [...byProfession.entries()]
      .map(([profession, treeSkills]) => ({
        key: profession,
        label: roots.get(profession)?.displayName ?? prettifyEcoName(profession),
        specialties: treeSkills.sort((a, b) => a.displayName.localeCompare(b.displayName)),
      }))
      .filter((tree) => !isUniversalSkill(tree.label) && tree.specialties.length > 0)
      .sort((a, b) => a.label.localeCompare(b.label))
  }, [recipeIndex])

  // Same exclusion for the progression rank lists (name-keyed leaderboards).
  const dropUniversal = (rows: Array<[string, number]>) =>
    rows.filter(([name]) => !isUniversalSkill(name))

  return (
    <Layout>
      {data?.mockData && (
        <div className="mock-banner" data-testid="mock-banner">
          ⚠️ MOCK DATA — every player, skill, and count on this page is fabricated. Set the{" "}
          <code>UPSTREAM_URL</code> env var on the service to pull real data. ⚠️
        </div>
      )}

      <section className="hero hero-compact">
        <h1 className="hero-title">Jobs</h1>
      </section>

      <section className="intro">
        <p>
          Who does what on the Eco server and how they got there. Active and Long Term are literal
          Eco demographic roles. Their union is the default job-coverage roster.
        </p>
        <label className="jobs-people-toggle">
          <input
            type="checkbox"
            checked={showAllPeople}
            onChange={(event) => setShowAllPeople(event.target.checked)}
          />{" "}
          Show people outside Active and Long Term
        </label>
        <FreshnessNote
          plane="jobs"
          loadedAt={jobsPlane.loadedAt}
          refreshing={jobsPlane.refreshing}
          refreshError={jobsPlane.refreshError}
          onRefresh={jobsPlane.refresh}
        />
      </section>

      {/* The server-wide trajectory layer: how the current roster below formed.
          Moved above the current-state tables so the history reads first
          (eco-app#94); folded in from the former /progression page (eco-app#90). */}
      {hasHistory && (
        <section className="jobs-progression" data-testid="jobs-progression">
          <h2 className="section-title">
            How the world got here{" "}
            <span className="section-sub">
              ({formatCount(progression!.totalEvents)} recorded skill events —{" "}
              {formatCount(progression!.citizens.length)} citizens)
            </span>
          </h2>

          {trendPanels.length > 0 && (
            <div className="prog-trend-grid" data-testid="trend-grid">
              {trendPanels.map(({ kind, points }) => (
                <TrendSparkline key={kind} label={kind} points={points} />
              ))}
            </div>
          )}

          <div className="atlas-columns">
            <div>
              <h3 className="subsection-title">Most-gained specialties</h3>
              <RankList
                rows={dropUniversal(progression!.bySpecialty).map(([name, count]) =>
                  makeRankRow(name, name, count),
                )}
                emptyNote="No specialties gained yet."
              />
            </div>
            <div>
              <h3 className="subsection-title">Busiest levelers</h3>
              <RankList
                rows={progression!.topLevelers.map(([name, count]) => makeRankRow(name, name, count))}
                emptyNote="No level-ups recorded yet."
                pretty={false}
              />
            </div>
          </div>

          {dropUniversal(progression!.classCompletions).length > 0 && (
            <>
              <h3 className="subsection-title">Classes completed</h3>
              <RankList
                rows={dropUniversal(progression!.classCompletions).map(([name, count]) =>
                  makeRankRow(name, name, count),
                )}
                emptyNote="No classes completed yet."
              />
            </>
          )}

          {progression!.warnings.length > 0 && (
            <ul className="warn-list" data-testid="progression-warnings">
              {progression!.warnings.map((w) => (
                <li key={w}>⚠ {w}</li>
              ))}
            </ul>
          )}
        </section>
      )}

      {loading && (
        <p className="loading" data-testid="loading">
          tallying the workshops…
        </p>
      )}

      {error && !data && (
        <p className="loading" data-testid="jobs-error">
          jobs data unavailable right now — the world spins on without us for a moment
        </p>
      )}

      {data && (
        <>
          <section data-testid="jobs-value">
            <h2 className="section-title">
              Most valuable to craft{" "}
              <span className="section-sub">(per profession, true margin × demand)</span>
            </h2>
            {!valueLoaded ? (
              <p className="empty-note" data-testid="jobs-value-loading">
                tallying craft margins…
              </p>
            ) : valueBoards === null ? (
              <p className="empty-note" data-testid="jobs-value-empty">
                Need recipes, market medians, logistics gaps, and trade volume to rank crafts.
              </p>
            ) : valueBoards.length === 0 ? (
              <p className="empty-note" data-testid="jobs-value-empty">
                No liquid supply-gap crafts yet.
              </p>
            ) : (
              <div className="value-boards" data-testid="jobs-value-boards">
                {valueBoards.map((board) => (
                  <section className="value-board" key={board.key} data-testid="value-board">
                    <h3 className="subsection-title">{board.label}</h3>
                    <RankList
                      rows={board.rows}
                      emptyNote={`No liquid supply-gap crafts for ${board.label} yet.`}
                      pretty={false}
                      formatValue={fmtPrice}
                    />
                  </section>
                ))}
              </div>
            )}
          </section>

          <section>
            <h2 className="section-title">Professions</h2>
            <ul className="cards">
              {professions.map((s) => (
                <ProfessionCard
                  key={s.profession}
                  stat={s}
                  rolesByPlayer={rolesByPlayer}
                  showAllPeople={showAllPeople}
                />
              ))}
            </ul>
          </section>

          {skillTrees.length > 0 && (
            <section data-testid="jobs-skill-trees">
              <h2 className="section-title">
                Skill trees{" "}
                <span className="section-sub">
                  (profession → specialty → level-gated talents)
                </span>
              </h2>
              <div className="skill-tree-grid">
                {skillTrees.map((tree) => (
                  <SkillTreeCard key={tree.key} tree={tree} />
                ))}
              </div>
            </section>
          )}

          <section>
            <h2 className="section-title">Specialties</h2>
            <ul className="cards">
              {specialties.map((s) => (
                <SpecialtyCard key={s.specialty} stat={s} showAllPeople={showAllPeople} />
              ))}
            </ul>
          </section>
        </>
      )}
    </Layout>
  )
}
