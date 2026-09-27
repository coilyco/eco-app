import Layout from "../components/Layout"
import { SERVER_BRIEF, type BenchedMod, type ModItem } from "../lib/serverBrief"

// The attributed mod catalog. It reads the same data/server_brief.json as the
// homepage, so the two pages cannot disagree about what is installed.
// game-dev owns the data; eco-ops is its source.

function slug(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
}

function SourceLine({ mod }: { mod: ModItem }) {
  if (mod.href) {
    return (
      <a className="k-btn k-btn--text mods-source" href={mod.href} target="_blank" rel="noreferrer">
        {mod.source ?? "Source"} ↗
      </a>
    )
  }
  return <span className="mods-nolink">{mod.source ?? "Source not published"}</span>
}

function ModCard({ mod, reason }: { mod: ModItem; reason?: string }) {
  const meta = [mod.author && `by ${mod.author}`, mod.version && `v${mod.version}`].filter(Boolean)
  return (
    <li className="k-card mods-card" data-testid={`mod-${slug(mod.name)}`}>
      <div className="k-stack k-stack--1">
        <h3 className="k-card__name">{mod.name}</h3>
        {meta.length > 0 && <p className="mods-meta">{meta.join(" // ")}</p>}
      </div>
      <p className="k-card__claim">{mod.summary}</p>
      {reason && (
        <p className="mods-reason">
          <strong>Why it sits out:</strong> {reason}
        </p>
      )}
      {mod.includes && mod.includes.length > 0 && (
        <ul className="mods-includes" aria-label={`${mod.name} modules`}>
          {mod.includes.map((part) => (
            <li key={part.name}>
              <strong>{part.name}</strong>
              <span>{part.summary}</span>
            </li>
          ))}
        </ul>
      )}
      <SourceLine mod={mod} />
    </li>
  )
}

function ModSection({
  id,
  title,
  intro,
  mods,
  benched,
}: {
  id: string
  title: string
  intro?: string
  mods: ModItem[] | BenchedMod[]
  benched?: boolean
}) {
  return (
    <section className="k-stack k-stack--4" aria-labelledby={`${id}-title`} data-testid={id}>
      <div className="k-section-heading">
        <h2 id={`${id}-title`} className="k-h2 mods-h2">
          {title} <span className="mods-count">{mods.length}</span>
        </h2>
        {intro && <p className="mods-sub">{intro}</p>}
      </div>
      <ul className="k-card-grid">
        {mods.map((mod) => (
          <ModCard
            key={mod.name}
            mod={mod}
            reason={benched ? (mod as BenchedMod).reason : undefined}
          />
        ))}
      </ul>
    </section>
  )
}

export default function Mods() {
  const brief = SERVER_BRIEF
  const gameplay = brief.mods.reduce((n, g) => n + g.items.length, 0)
  const plumbing = brief.serverPlumbing ?? []
  const plugins = brief.servicePlugins ?? []
  const benched = brief.benched ?? []

  return (
    <Layout>
      <section className="k-stack k-stack--4">
        <p className="k-eyebrow">Mods // reviewed {brief.reviewedOn}</p>
        <h1 className="k-display mods-title">Every mod on the server</h1>
        <p className="mods-sub">
          {gameplay} gameplay mods
          {plumbing.length > 0 && `, ${plumbing.length} pieces of server plumbing`}
          {plugins.length > 0 && `, and ${plugins.length} eco-app plugins`}. Each links to its
          source where one is published and says why where it is not.
        </p>
      </section>

      {brief.mods.map((group) => (
        <ModSection
          key={group.group}
          id={`mods-${slug(group.group)}`}
          title={group.group}
          mods={group.items}
        />
      ))}

      {benched.length > 0 && (
        <ModSection
          id="mods-benched"
          title="Benched this cycle"
          intro="Installed on an earlier cycle and sitting this one out."
          mods={benched}
          benched
        />
      )}

      {plumbing.length > 0 && (
        <ModSection
          id="mods-plumbing"
          title="Server plumbing"
          intro="What runs the server behind the scenes: tooling, shared runtimes, and exporters."
          mods={plumbing}
        />
      )}

      {plugins.length > 0 && (
        <ModSection
          id="mods-plugins"
          title="eco-app plugins"
          intro="Read-only server plugins that feed this site's data."
          mods={plugins}
        />
      )}
    </Layout>
  )
}
