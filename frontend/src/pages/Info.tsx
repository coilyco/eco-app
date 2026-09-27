import type { FormEvent } from "react"
import { useSearchParams } from "react-router-dom"
import Layout from "../components/Layout"
import { useEcoStatus } from "../hooks/useEcoStatus"
import { safeHttpUrl } from "../lib/format"
import Hero from "../components/Hero"
import MeteorBanner from "../components/MeteorBanner"
import StatGrid from "../components/StatGrid"

const STEAM_URL = "https://store.steampowered.com/app/382310/Eco/"

// The live world snapshot: everything the old landing page carried, now one
// level down so the homepage stays a thin directory. Formerly "/server";
// renamed to "/info" in the eco-app#90 IA cleanup (the old path redirects here).
export default function Info() {
  const [searchParams, setSearchParams] = useSearchParams()
  const targetServer = searchParams.get("server")?.trim() ?? ""
  const { status, error, loading } = useEcoStatus(targetServer)
  const discordUrl = safeHttpUrl(status?.server.discord)

  function inspectServer(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const server = String(new FormData(event.currentTarget).get("server") ?? "").trim()
    setSearchParams(server ? { server } : {})
  }

  function useSirensServer() {
    setSearchParams({})
  }

  return (
    <Layout fetchedAtISO={status?.fetchedAtISO}>
      <Hero status={status} error={error} />

      <section className="server-inspector" aria-labelledby="server-inspector-heading">
        <h2 className="section-title" id="server-inspector-heading">
          Inspect another Eco server
        </h2>
        <p className="section-subcopy">
          Enter any public Eco server address to see its status, online players, meteor
          timing, and world totals. No admin access is needed.
        </p>
        <form className="filter-row server-inspector-form" onSubmit={inspectServer}>
          <input
            aria-label="Eco server address"
            className="filter-input"
            defaultValue={targetServer}
            key={targetServer}
            name="server"
            placeholder="host, host:port, or full /info URL"
            type="text"
          />
          <button className="button" type="submit">
            Inspect server
          </button>
          {targetServer && (
            <button className="button" onClick={useSirensServer} type="button">
              Use Sirens server
            </button>
          )}
        </form>
        {targetServer && (
          <p className="empty-note" data-testid="server-target">
            Inspecting {targetServer}
          </p>
        )}
      </section>

      {loading && (
        <p className="loading" data-testid="loading">
          listening for the world…
        </p>
      )}

      {status && (
        <>
          <MeteorBanner cycle={status.cycle} achievements={status.achievements} />
          <StatGrid status={status} />
          <section aria-labelledby="online-players-heading">
            <h2 className="section-title" id="online-players-heading">
              Online now
            </h2>
            {status.players.onlineNames.length > 0 ? (
              <ul className="online-player-list" data-testid="online-player-list">
                {status.players.onlineNames.map((name) => (
                  <li className="mini-pill" key={name}>
                    {name}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="empty-note" data-testid="online-player-empty">
                Nobody is online right now.
              </p>
            )}
          </section>
        </>
      )}

      <section className="cta-row">
        {discordUrl && (
          <a className="button button-discord" href={discordUrl}>
            Join the Discord
          </a>
        )}
        <a className="button" href={STEAM_URL}>
          Eco on Steam
        </a>
      </section>
    </Layout>
  )
}
