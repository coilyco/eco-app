import type { EcoStatus } from "../lib/api"
import { formatCount, stripEcoMarkup } from "../lib/format"

interface HeroProps {
  status: EcoStatus | null
  error: string | null
}

// The server writes its whole pitch into one description ("Eco via Sirens |
// Cycle 14 | High Collab | 100 x 100 | ..."). As a single display heading it
// ran four lines, so the first segment is the name and the rest become chips.
function splitDescription(description: string): { name: string; tags: string[] } {
  const [name, ...tags] = stripEcoMarkup(description)
    .split("|")
    .map((part) => part.trim())
    .filter(Boolean)
  return { name: name ?? "", tags }
}

export default function Hero({ status, error }: HeroProps) {
  const { name, tags } = status ? splitDescription(status.server.description) : { name: "", tags: [] }
  return (
    <section className="k-stack k-stack--4 info-hero">
      <p className="k-eyebrow">Live server snapshot</p>
      <h1 className="k-display">{name || "Eco server"}</h1>
      {tags.length > 0 && (
        <p className="k-chip-row" data-testid="server-tags">
          {tags.map((tag) => (
            <span key={tag} className="k-chip k-chip--tag">
              {tag}
            </span>
          ))}
        </p>
      )}
      {status && (
        <p className="hero-pill info-pill" data-testid="live-pill">
          <span className="pulse-dot" aria-hidden="true" />
          {formatCount(status.players.online)} online now // {formatCount(status.players.total)}{" "}
          settlers all-cycle
        </p>
      )}
      {!status && error && (
        <p className="hero-pill hero-pill-muted info-pill" data-testid="live-pill">
          Live snapshot unavailable. The world keeps turning, and this page retries every
          minute.
        </p>
      )}
    </section>
  )
}
