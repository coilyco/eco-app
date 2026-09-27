import type { ReactNode } from "react"
import { groupChains, type ServerBrief } from "../lib/serverBrief"

// The reviewed half of the homepage (eco-app#8306): settings, skill trees,
// and notes, rendered from data/server_brief.json. Nothing here reads
// live status, so all of it stays on screen through an outage.

function SectionHeading({ id, title, sub }: { id: string; title: string; sub?: ReactNode }) {
  return (
    <div className="k-section-heading">
      <h2 id={id} className="k-h2 home-h2">
        {title}
      </h2>
      {sub && <p className="home-sub">{sub}</p>}
    </div>
  )
}

export function ConfigsSection({ brief }: { brief: ServerBrief }) {
  return (
    <section className="k-stack k-stack--4" aria-labelledby="home-configs">
      <SectionHeading
        id="home-configs"
        title="Server settings"
        sub={`How this server differs from normal Eco. Last checked ${brief.reviewedOn}.`}
      />
      <div className="home-grid" data-testid="home-configs">
        {brief.configs.map((group) => (
          <div key={group.group} className="k-panel k-stack k-stack--3">
            <h3 className="k-h3">{group.group}</h3>
            <dl className="k-deflist home-deflist">
              {group.items.map((item) => (
                <div key={item.label} className="home-defrow">
                  <dt>{item.label}</dt>
                  <dd>{item.value}</dd>
                </div>
              ))}
            </dl>
          </div>
        ))}
      </div>
    </section>
  )
}

export function SkillTreesSection({ brief }: { brief: ServerBrief }) {
  const pickOne = new Set(brief.skillTrees.pickOne)
  const groups = groupChains(brief.skillTrees.chains)
  const Skill = ({ name }: { name: string }) =>
    pickOne.has(name) ? (
      <span className="home-skill home-skill--pick">
        {name}
        <span className="k-sr-only"> (pick one)</span>
      </span>
    ) : (
      <span className="home-skill">{name}</span>
    )

  return (
    <section className="k-stack k-stack--4" aria-labelledby="home-skills">
      <SectionHeading
        id="home-skills"
        title="Skill trees"
        sub="Advanced skills need the skill before them. Read each line left to right."
      />
      <div className="k-note" data-testid="home-pick-one">
        <p className="k-note__label">Pick one</p>
        <p>
          You can learn only one of these four, so choose with your town.
        </p>
        <p className="k-chip-row">
          {brief.skillTrees.pickOne.map((name) => (
            <span key={name} className="k-chip home-chip-pick">
              {name}
            </span>
          ))}
        </p>
      </div>
      <div className="home-grid home-grid--trees" data-testid="home-skill-trees">
        {groups.map((g) => (
          <div key={g.root} className="home-tree">
            <p className="home-tree__root">{g.root}</p>
            <ul className="home-tree__branches">
              {g.branches.map((branch) => (
                <li key={branch.join(">")}>
                  {branch.map((name) => (
                    <span key={name} className="home-tree__step">
                      <span className="home-tree__arrow" aria-hidden="true">
                        →
                      </span>
                      <span className="k-sr-only">then </span>
                      <Skill name={name} />
                    </span>
                  ))}
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </section>
  )
}

export function NotesSection({ brief }: { brief: ServerBrief }) {
  return (
    <section className="k-stack k-stack--4" aria-labelledby="home-notes">
      <SectionHeading id="home-notes" title="Good to know" />
      <ul className="k-card-grid" data-testid="home-notes">
        {brief.gameplayNotes.map((note) => (
          <li key={note.title} className="k-card">
            <h3 className="k-card__name">{note.title}</h3>
            <p className="k-card__claim">{note.body}</p>
          </li>
        ))}
      </ul>
    </section>
  )
}
