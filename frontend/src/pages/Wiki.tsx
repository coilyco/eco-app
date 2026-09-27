import Layout from "../components/Layout"

interface WikiTopic {
  title: string
  page: string
  summary: string
}

const WIKI_ROOT = "https://wiki.play.eco/en/index.php?stable=1&title="
const WIKI_TOPICS: WikiTopic[] = [
  { title: "Getting started", page: "Getting_Started", summary: "Joining a world, controls, early priorities, and collaboration." },
  { title: "Skills", page: "Skills", summary: "Professions, specialties, talents, and skill progression." },
  { title: "Research", page: "Research", summary: "Skill books, scrolls, research tables, and unlocks." },
  { title: "Crafting", page: "Crafting", summary: "Recipes, workstations, labor, and production basics." },
  { title: "Food", page: "Food", summary: "Nutrition, food choices, and the systems around eating." },
  { title: "Agriculture", page: "Agriculture", summary: "Crops, farming, soil, and sustainable production." },
  { title: "Housing", page: "Housing", summary: "Rooms, tiers, furniture, and housing bonuses." },
  { title: "Pollution", page: "Pollution", summary: "Air, soil, water, waste, and climate consequences." },
  { title: "Economy", page: "Economy", summary: "Stores, contracts, work parties, banking, and currency." },
  { title: "Government", page: "Government", summary: "Constitutions, offices, districts, and civic institutions." },
  { title: "Laws", page: "Laws", summary: "Creating, proposing, voting on, and enforcing laws." },
  { title: "Server", page: "Server", summary: "Server hosting, configuration, administration, and operation." },
  { title: "Chat commands", page: "Chat_Commands", summary: "Player and administrator command reference." },
  { title: "Modding", page: "Modding", summary: "Official entry points for Eco server and client modding." },
]

export default function Wiki() {
  return (
    <Layout>
      <section className="hero hero-compact">
        <h1 className="hero-title">
          Official Eco Wiki <span className="accent">snapshot</span>
        </h1>
        <p className="hero-tagline">
          A compact in-app index of the official stable English wiki, centered on the
          systems Eco players use most.
        </p>
        <p className="hero-note">
          Reviewed 2026-08-01. Eco Wiki content remains on Strange Loop Games' site and
          opens there. The official wiki is authoritative and may update after this index.
        </p>
      </section>

      <section aria-label="Eco Wiki topics">
        <ul className="k-card-grid" data-testid="wiki-topics">
          {WIKI_TOPICS.map((topic) => (
            <li className="k-card" key={topic.page}>
              <h2 className="k-card__name">{topic.title}</h2>
              <p className="k-card__claim">{topic.summary}</p>
              <a
                className="k-btn k-btn--text wiki-link"
                href={`${WIKI_ROOT}${encodeURIComponent(topic.page)}`}
                target="_blank"
                rel="noreferrer"
              >
                Open stable wiki page ↗
              </a>
            </li>
          ))}
        </ul>
      </section>
    </Layout>
  )
}
