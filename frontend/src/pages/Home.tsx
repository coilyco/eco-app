import { Link } from "react-router-dom"
import Layout from "../components/Layout"
import { useCivicsPulse } from "../hooks/useCivicsPulse"
import { useClimatePulse } from "../hooks/useClimatePulse"
import { useCraftingPulse } from "../hooks/useCraftingPulse"
import { useEcoregionPulse } from "../hooks/useEcoregionPulse"
import { useEcoStatus } from "../hooks/useEcoStatus"
import { useTradePulse } from "../hooks/useTradePulse"
import { useTradesPulse } from "../hooks/useTradesPulse"
import { useWorldPulse } from "../hooks/useWorldPulse"
import { formatCount, prettifyEcoName, safeHttpUrl } from "../lib/format"
import type { EcoStatus } from "../lib/api"
import {
  SERVER_BRIEF,
  humanizeEnum,
  meteorState,
  parseCycle,
  parseEcoVersion,
  parseMeteorLength,
  parseWorldSize,
  type MeteorState,
} from "../lib/serverBrief"
import {
  ConfigsSection,
  ModsSection,
  NotesSection,
  SkillTreesSection,
} from "../components/ServerBriefSections"

const STEAM_URL = "https://store.steampowered.com/app/382310/Eco/"
// Eco Gnome (MIT) is the crafting-cost calculator. It used to be its own
// /calculator page; eco-app#90 demoted it to this homepage card that links out
// to the gnome service. Roadmap (#40) self-hosts it at eco-gnome.coilysiren.me.
const ECO_GNOME_URL = "https://eco-gnome.coilysiren.me/"

// The homepage is the one link a player hands anyone (eco-app#8306): the live
// state of the world first, then the reviewed server brief, then the directory
// of every other surface. Per-cycle facts (cycle, world size, meteor) come from
// live status only, so they cannot go stale the way the Discord block did.

function LivePill({ status, error }: { status: EcoStatus | null; error: string | null }) {
  if (status) {
    const online = status.players.online
    return (
      <p className="hero-pill" data-testid="home-live">
        <span className="pulse-dot" aria-hidden="true" />
        {online === null ? "Players online unknown" : `${formatCount(online)} online now`}
        {status.players.total !== null && ` // ${formatCount(status.players.total)} settlers this cycle`}
      </p>
    )
  }
  if (error) {
    return (
      <p className="hero-pill hero-pill-muted" data-testid="home-live">
        Live status is unavailable right now. The server brief below still holds.
      </p>
    )
  }
  return (
    <p className="hero-pill hero-pill-muted" data-testid="home-live">
      Checking the server
    </p>
  )
}

function MeteorLine({ state, hasStatus }: { state: MeteorState; hasStatus: boolean }) {
  switch (state.kind) {
    case "countdown":
      return (
        <p className="home-meteor home-meteor--warn" data-testid="home-meteor">
          ☄ {state.days} {state.days === 1 ? "day" : "days"} until the meteor
        </p>
      )
    case "destroyed":
      return (
        <p className="home-meteor home-meteor--safe" data-testid="home-meteor">
          Meteor destroyed on day {state.day}
          {state.time ? ` at ${state.time}` : ""}. The world is safe this cycle.
        </p>
      )
    case "none":
      return (
        <p className="home-meteor" data-testid="home-meteor">
          No meteor this cycle.
        </p>
      )
    default:
      return hasStatus ? (
        <p className="home-meteor" data-testid="home-meteor">
          The server is not reporting the meteor date.
        </p>
      ) : null
  }
}

function Fact({ label, value }: { label: string; value: string | null }) {
  return (
    <div className="k-fact">
      <span className="k-fact__label">{label}</span>
      <span className={value ? "k-fact__value" : "k-fact__value home-unknown"}>
        {value ?? "Unknown"}
      </span>
    </div>
  )
}

function JoinSteps({ steps, serverName }: { steps: string[]; serverName: string }) {
  return (
    <ol className="home-join-steps">
      {steps.map((step) => {
        const at = step.indexOf(serverName)
        return (
          <li key={step}>
            {at < 0 ? (
              step
            ) : (
              <>
                {step.slice(0, at)}
                <strong>{serverName}</strong>
                {step.slice(at + serverName.length)}
              </>
            )}
          </li>
        )
      })}
    </ol>
  )
}

export default function Home() {
  const { status, error } = useEcoStatus()
  const brief = SERVER_BRIEF
  const tradePulse = useTradePulse()
  const civicsPulse = useCivicsPulse()
  const worldPulse = useWorldPulse()
  const tradesPulse = useTradesPulse()
  const craftingPulse = useCraftingPulse()
  const climatePulse = useClimatePulse()
  const ecoregionPulse = useEcoregionPulse()
  // Live first, because the server announces its own invite; the reviewed copy
  // keeps the CTA working through an outage.
  const discordUrl = safeHttpUrl(status?.server.discord) ?? safeHttpUrl(brief.join.discordUrl)
  const cycle = parseCycle(status?.server.description)
  const meteorDays = parseMeteorLength(status?.server.detailedDescription)
  const version = parseEcoVersion(status?.server.version)

  return (
    <Layout>
      <section className="k-hero--split home-hero" aria-labelledby="home-title">
        <div className="k-stack k-stack--4">
          <p className="k-eyebrow">
            {cycle !== null ? `Cycle ${cycle}` : "Eco server"}
            {version ? ` // Eco ${version}` : ""}
          </p>
          <h1 id="home-title" className="k-display">
            {brief.join.serverName}
          </h1>
          <LivePill status={status} error={error} />
          <MeteorLine state={meteorState(status)} hasStatus={status !== null} />
          {brief.nextCycle && (
            <div className="k-note k-note--grant" data-testid="home-next-cycle">
              <p className="k-note__label">Next cycle</p>
              <p>{brief.nextCycle}</p>
            </div>
          )}
        </div>

        <section className="k-panel k-stack k-stack--4" aria-labelledby="home-join">
          <h2 id="home-join" className="k-h3">
            Join the server
          </h2>
          <JoinSteps steps={brief.join.steps} serverName={brief.join.serverName} />
          <p className="k-btn-row">
            {discordUrl && (
              <a className="button button-discord" href={discordUrl}>
                Join the Discord
              </a>
            )}
            <a className="button" href={STEAM_URL}>
              Eco on Steam
            </a>
          </p>
        </section>
      </section>

      {/* Only with live status: six Unknown tiles read as a broken page, and the
          live pill above already says status is down. A single fact the server
          omits still shows as Unknown. */}
      {status && (
        <section aria-label="world at a glance">
          <div className="k-facts" data-testid="home-facts">
            <Fact label="Cycle" value={cycle !== null ? String(cycle) : null} />
            <Fact label="World size" value={parseWorldSize(status?.server.description)} />
            <Fact label="Meteor" value={meteorDays !== null ? `${meteorDays} days` : null} />
            <Fact
              label="Collaboration"
              value={humanizeEnum(status?.cycle.collaboration?.replace(/Collaboration$/, ""))}
            />
            <Fact label="Game speed" value={humanizeEnum(status?.cycle.gameSpeed)} />
            <Fact label="Eco version" value={version} />
          </div>
        </section>
      )}

      <ConfigsSection brief={brief} />
      <ModsSection brief={brief} />
      <SkillTreesSection brief={brief} />
      <NotesSection brief={brief} />

      <section className="k-stack k-stack--4" aria-labelledby="home-explore">
        <div className="k-section-heading">
          <h2 id="home-explore" className="k-h2 home-h2">
            Explore the live world
          </h2>
          <p className="home-sub">
            Intelligence and companionship for serious Eco servers. See what the world needs,
            where to trade, who can craft it, how civics changed, and what happened while you
            were away.
          </p>
        </div>
        <section className="k-card-grid k-card-grid--condensed dir-cards" aria-label="site directory">
          <Link className="k-card dir-card" to="/info" data-testid="dir-info">
            <h3 className="k-card__name">Info</h3>
            <p>Meteor countdown, players, world stats, and the economy at a glance.</p>
            {status && (
              <p className="dir-badges" data-testid="info-badges">
                {status.cycle.hasMeteor && status.cycle.daysUntilMeteor !== null && (
                  <span className="mini-pill">☄ {status.cycle.daysUntilMeteor}d to meteor</span>
                )}
                <span className="mini-pill">{formatCount(status.players.online)} online</span>
              </p>
            )}
          </Link>

          <Link className="k-card dir-card" to="/jobs" data-testid="dir-jobs">
            <h3 className="k-card__name">Jobs</h3>
            <p>Who can make what — professions, specialties, and every player's skills.</p>
            {status && (
              <p className="dir-badges">
                <span className="mini-pill">{formatCount(status.players.total)} settlers</span>
              </p>
            )}
          </Link>

          <Link className="k-card dir-card" to="/wiki" data-testid="dir-wiki">
            <h3 className="k-card__name">Eco Wiki</h3>
            <p>Stable official guides for getting started, skills, economy, civics, and modding.</p>
          </Link>

          {/* Trade + the trades ledger are one surface now (eco-app#90). The
              badge strip fixes the old empty bubbles: markets falls back to a
              graceful 0 (the market plane is empty on servers that trade but have
              no priced markets), while volume and the trade count come from the
              ledger's own totalCurrencyVolume / totalTrades. */}
          <Link className="k-card dir-card" to="/trade" data-testid="dir-trade">
            <h3 className="k-card__name">Trade &amp; logistics</h3>
            <p>
              Movers, price history, the full trade ledger, stores, and what to buy, sell, and ship
              next.
            </p>
            {(tradePulse || tradesPulse) && (
              <p className="dir-badges" data-testid="trade-badges">
                <span className="mini-pill">{formatCount(tradePulse?.markets ?? 0)} markets</span>
                {tradesPulse && (
                  <>
                    <span className="mini-pill">{formatCount(tradesPulse.volume)} volume</span>
                    <span className="mini-pill">{formatCount(tradesPulse.trades)} trades</span>
                  </>
                )}
                {tradesPulse?.topItem && (
                  <span className="mini-pill">top: {prettifyEcoName(tradesPulse.topItem)}</span>
                )}
              </p>
            )}
          </Link>

          <Link className="k-card dir-card" to="/crafting" data-testid="dir-crafting">
            <h3 className="k-card__name">Crafting atlas</h3>
            <p>What the world is making — top items and stations, deep-linkable.</p>
            {craftingPulse && (
              <p className="dir-badges" data-testid="crafting-badges">
                <span className="mini-pill">{formatCount(craftingPulse.crafts)} crafts</span>
                {craftingPulse.topItem && (
                  <span className="mini-pill">top: {prettifyEcoName(craftingPulse.topItem)}</span>
                )}
              </p>
            )}
          </Link>

          <Link className="k-card dir-card" to="/items" data-testid="dir-items">
            <h3 className="k-card__name">Item directory</h3>
            <p>Every item ever bought, sold, or crafted — click through to its full history.</p>
          </Link>

          {/* The recipe browse surface (eco-app#101): the bill-of-materials for
              every craftable, deep-linkable by product, profession, station, or
              ingredient. /recipe details are URL-only, reached from here. */}
          <Link className="k-card dir-card" to="/recipes" data-testid="dir-recipes">
            <h3 className="k-card__name">Recipes</h3>
            <p>How everything is made — ingredients, station, profession, labor, and craft time.</p>
          </Link>

          {/* The /uses hub is the ONLY homepage card the whole use-case family
              gets (eco-app#99): the demand-side pages (what's in demand, buy/sell,
              arbitrage, shop-check) are URL-only, reached from the hub. */}
          <Link className="k-card dir-card" to="/uses" data-testid="dir-uses">
            <h3 className="k-card__name">Use cases</h3>
            <p>Task-framed answers — what's in demand, where to buy or sell, arbitrage, shop pricing.</p>
          </Link>

          <Link className="k-card dir-card" to="/civics" data-testid="dir-civics">
            <h3 className="k-card__name">Civics &amp; governance</h3>
            <p>Elections, turnout, demographics, and new settlements over time.</p>
            {civicsPulse && (
              <p className="dir-badges" data-testid="civics-badges">
                <span className="mini-pill">{formatCount(civicsPulse.events)} civic events</span>
                {civicsPulse.turnoutPct !== null && (
                  <span className="mini-pill">{civicsPulse.turnoutPct}% turnout</span>
                )}
              </p>
            )}
          </Link>

          {/* Climate folded into the World card (eco-app#90). The focused page
              keeps the map, biomes, ecoregions, biodiversity, and atmosphere. */}
          <Link className="k-card dir-card" to="/map" data-testid="dir-map">
            <h3 className="k-card__name">World</h3>
            <p>
              The live map, biome &amp; water mix, closest real-world ecoregions, species risk,
              and the climate — CO₂, temperature, and sea level.
            </p>
            {(worldPulse || ecoregionPulse || climatePulse) && (
              <p className="dir-badges" data-testid="world-badges">
                {worldPulse && (
                  <span className="mini-pill">{formatCount(worldPulse.events)} events</span>
                )}
                {worldPulse?.topCategory && (
                  <span className="mini-pill">{worldPulse.topCategory}</span>
                )}
                {ecoregionPulse?.topBiome && (
                  <span className="mini-pill">{ecoregionPulse.topBiome}</span>
                )}
                {climatePulse && <span className="mini-pill">{climatePulse.status}</span>}
                {climatePulse?.co2Ppm != null && (
                  <span className="mini-pill">{formatCount(climatePulse.co2Ppm)} ppm CO₂</span>
                )}
              </p>
            )}
          </Link>

          <a
            className="k-card dir-card"
            href={ECO_GNOME_URL}
            target="_blank"
            rel="noreferrer"
            data-testid="dir-gnome"
          >
            <h3 className="k-card__name">Crafting calculator ↗</h3>
            <p>
              Price your craft with Eco Gnome (MIT) — optimal buy and sell prices from your
              professions and recipes. Opens the gnome service.
            </p>
          </a>
        </section>
      </section>
    </Layout>
  )
}
