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

      {loading && (
        <p className="loading" data-testid="loading">
          listening for the world…
        </p>
      )}

      {status && (
        <>
          <MeteorBanner cycle={status.cycle} achievements={status.achievements} />
          <StatGrid status={status} />
          <section className="k-stack k-stack--3" aria-labelledby="online-players-heading">
            <h2 className="k-h2 info-h2" id="online-players-heading">
              Online now
            </h2>
            {status.players.onlineNames.length > 0 ? (
              <ul className="online-player-list" data-testid="online-player-list">
                {status.players.onlineNames.map((name) => (
                  <li className="info-player" key={name}>
                    {name}
                  </li>
                ))}
              </ul>
            ) : (
              <div className="k-note" data-testid="online-player-empty">
                <p>Nobody is online right now.</p>
              </div>
            )}
          </section>
        </>
      )}

      <section className="k-stack k-stack--3" aria-labelledby="server-inspector-heading">
        <h2 className="k-h2 info-h2" id="server-inspector-heading">
          Inspect another Eco server
        </h2>
        <p className="info-sub">
          Enter any public Eco server address to see its status, online players, meteor
          timing, and world totals. No admin access is needed.
        </p>
        <form className="info-inspect" onSubmit={inspectServer}>
          <input
            aria-label="Eco server address"
            className="k-input"
            defaultValue={targetServer}
            key={targetServer}
            name="server"
            placeholder="host, host:port, or full /info URL"
            type="text"
          />
          <button className="k-btn k-btn--primary" type="submit">
            Inspect server
          </button>
          {targetServer && (
            <button className="k-btn k-btn--ghost" onClick={useSirensServer} type="button">
              Use Sirens server
            </button>
          )}
        </form>
        {targetServer && (
          <p className="info-sub" data-testid="server-target">
            Inspecting {targetServer}
          </p>
        )}
      </section>

      <section className="k-btn-row">
        {discordUrl && (
          <a className="button button-discord" href={discordUrl}>
            Join the Discord
          </a>
        )}
        <a className="k-btn k-btn--ghost" href={STEAM_URL}>
          Eco on Steam
        </a>
      </section>
    </Layout>
  )
}
