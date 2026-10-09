import { Link } from "react-router-dom"
import Layout from "../components/Layout"
import manifest from "../../../data/spa_routes.json"

// The cycle 14 splat pages, listed from the route table so a new /cycle-14/<slug>
// route shows up here with no edit to this file. docs/frontend/cycle-14-index.md.
const STILLS = import.meta.glob<string>("../assets/og-art/*.jpg", { eager: true, query: "?url", import: "default" })

const PREFIX = "/cycle-14/"

// Read from the manifest, not ../routes: routes.tsx imports this page, and a
// page importing the router table back is a cycle.
interface SplatSpec {
  path: string
  title?: string
  description?: string
  art?: string
}

export function splatSpecs(specs: SplatSpec[] = manifest.routes as SplatSpec[]): SplatSpec[] {
  return specs.filter((s) => s.path.startsWith(PREFIX) && !s.path.includes("*") && s.title)
}

const stillFor = (spec: SplatSpec) => (spec.art ? STILLS[`../assets/og-art/${spec.art}`] : undefined)

// Placeholder words: Kai's or dev-advocate's to replace (COI-2401).
export default function Cycle14Index({ specs = splatSpecs() }: { specs?: SplatSpec[] } = {}) {
  return (
    <Layout>
      <section className="k-stack k-stack--4">
        <h1 className="k-display">Cycle 14 in 3D</h1>
        <p className="castle-sub">Places on the server captured as 3D scenes. Open one and look around.</p>
      </section>

      {specs.length === 0 ? (
        <p className="k-hint" data-testid="cycle-14-empty">
          No 3D scenes are published yet.
        </p>
      ) : (
        <section className="k-card-grid k-card-grid--condensed dir-cards" aria-label="3D scenes">
          {specs.map((s) => {
            const still = stillFor(s)
            return (
              <Link className="k-card dir-card cycle-card" to={s.path} key={s.path} data-testid={`cycle-14-${s.path.slice(PREFIX.length)}`}>
                {still ? <img className="cycle-card__still" src={still} width={1200} height={630} alt="" loading="lazy" /> : null}
                <h2>{s.title} →</h2>
                <p>{s.description}</p>
              </Link>
            )
          })}
        </section>
      )}
    </Layout>
  )
}
