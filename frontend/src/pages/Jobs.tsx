import { useMemo, useState } from "react"
import type { ReactNode } from "react"
import { Link } from "react-router-dom"
import EcoRichText from "../components/EcoRichText"
import FreshnessNote from "../components/FreshnessNote"
import Layout from "../components/Layout"
import { useJobsData } from "../hooks/useJobsData"
import { fetchLogistics, type GapReason } from "../lib/logisticsApi"
import { fetchMarket } from "../lib/marketApi"
import { formatCount, formatMoney, prettifyEcoName } from "../lib/format"
import {
  fetchRecipeIndexWithCost,
  type RecipeSkillDef,
} from "../lib/recipesApi"
import { fetchTradesLedger } from "../lib/tradesApi"
import type { ProfessionStat, SpecialtyStat } from "../lib/jobsApi"
import { useFreshData } from "../lib/useFreshData"

// Survivalist and Self Improvement are the universal starter skills — every
// citizen has them, so they carry no signal and only clutter the roster
// (eco-app#94). We filter them out of every jobs surface (professions,
// specialties, per-player skill lists, and skill trees) in one place here.
// Matching on the prettified, whitespace-collapsed name catches both the jobs
// API's display names ("Self Improvement") and raw Eco ids
// ("SelfImprovement", "SurvivalistSkill").
const UNIVERSAL_SKILLS = new Set(["self improvement", "survivalist"])
const VALUE_ROWS = 5
const LIQUIDITY_FLOOR = 100

const GAP: Record<GapReason, { glyph: string; label: string; color: string }> = {
  no_supply: { glyph: "✖", label: "no supply", color: "var(--meteor)" },
  thin_supply: { glyph: "◐", label: "thin supply", color: "var(--meteor-deep)" },
  overpriced: { glyph: "▲", label: "over-priced", color: "var(--ink-faint)" },
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

interface ValueRow {
  key: string
  item: string
  href: string
  name: string
  score: number
  confidence: "complete" | "incomplete"
  note: ReactNode
}

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
}: {
  stat: ProfessionStat
  rolesByPlayer: ReadonlyMap<string, string[]>
}) {
  const [open, setOpen] = useState(false)
  const visiblePlayers = stat.players.filter((player) =>
    coveredByRole(rolesByPlayer.get(player) ?? []),
  )
  return (
    <li className={`card card-tight${stat.total === 0 ? " dim" : ""}`}>
      <button className="prof-btn" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <span>{stat.profession}</span>
        <span className="count">
          ( {stat.covered} / {stat.total} covered )
        </span>
      </button>
      {open && (
        <div className="detail">
          {visiblePlayers.length > 0 ? (
            <ul className="rows">
              {visiblePlayers.map((p) => (
                <li key={p} className="role-holder">
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

function SpecialtyCard({ stat }: { stat: SpecialtyStat }) {
  const visibleHolders = stat.holders.filter((holder) => coveredByRole(holder.roles))
  return (
    <li className={`card${stat.total === 0 ? " dim" : ""}`}>
      <h3 className="card-title">
        {stat.specialty}
        <span className="count">
          ( {stat.covered} / {stat.total} covered )
        </span>
      </h3>
      <p className="kicker">{stat.profession}</p>
      <ul className="rows">
        {visibleHolders.map((h) => (
          <li key={h.player} className="role-holder">
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

function RankList({
  rows,
  emptyNote,
  formatValue = formatCount,
}: {
  rows: ValueRow[]
  emptyNote: string
  formatValue?: (n: number) => string
}) {
  const top = rows.slice(0, 15)
  const valueFor = (row: ValueRow) => row.score
  const max = Math.max(...top.map(valueFor), 1)
  if (top.length === 0) {
    return <p className="empty-note">{emptyNote}</p>
  }
  return (
    <ul className="rank-rows">
      {top.map((row) => (
        <li key={row.key}>
          <div className="rank-row" data-testid="rank-row">
            <Link className="rank-name linklike" to={row.href} data-testid="opportunity-price-link">
              {row.name}
            </Link>
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

export default function Jobs() {
  const { data, error, loading } = useJobsData()

  // Refresh contract lives in freshness.ts, not here (eco-app#201). The value
  // spine is enrichment over the current-state tables, and a failure leaves
  // those tables exactly as they were before this surface existed.
  const jobsPlane = useFreshData("jobs", async (signal) => {
    const [recipeIndex, logistics, market, trades] = await Promise.all([
      fetchRecipeIndexWithCost(signal).catch(() => null),
      fetchLogistics(signal).catch(() => null),
      fetchMarket(signal).catch(() => null),
      fetchTradesLedger(signal).catch(() => null),
    ])
    return { recipeIndex, logistics, market, trades }
  })
  const recipeIndex = jobsPlane.data?.recipeIndex ?? null
  const logistics = jobsPlane.data?.logistics ?? null
  const market = jobsPlane.data?.market ?? null
  const trades = jobsPlane.data?.trades ?? null
  const valueLoaded = !jobsPlane.loading


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
                {margin !== null ? `estimated margin ${formatMoney(margin)}` : "margin unavailable"} ·{" "}
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
        <FreshnessNote
          plane="jobs"
          loadedAt={jobsPlane.loadedAt}
          refreshing={jobsPlane.refreshing}
          refreshError={jobsPlane.refreshError}
          onRefresh={jobsPlane.refresh}
        />
      </section>

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
                      formatValue={formatMoney}
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
                <ProfessionCard key={s.profession} stat={s} rolesByPlayer={rolesByPlayer} />
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
                <SpecialtyCard key={s.specialty} stat={s} />
              ))}
            </ul>
          </section>
        </>
      )}
    </Layout>
  )
}
