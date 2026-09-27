import Layout from "../components/Layout"

interface WikiTopic {
  title: string
  page: string
  summary: string
}

const WIKI_ROOT = "https://wiki.play.eco/en/index.php?stable=1&title="
const WIKI_TOPICS: WikiTopic[] = [
  { title: "Getting started", page: "Getting_Started", summary: "Joining a world, controls, what to do first, and working with others." },
  { title: "Skills", page: "Skills", summary: "Professions, specialties, talents, and leveling up your skills." },
  { title: "Research", page: "Research", summary: "Skill books, skill scrolls, research tables, and what they unlock." },
  { title: "Crafting", page: "Crafting", summary: "Recipes, workstations, labor, and how crafting works." },
  { title: "Food", page: "Food", summary: "Nutrition, what to eat, and how eating works." },
  { title: "Agriculture", page: "Agriculture", summary: "Crops, farming, soil, and farming without wearing out the land." },
  { title: "Housing", page: "Housing", summary: "Rooms, tiers, furniture, and housing bonuses." },
  { title: "Pollution", page: "Pollution", summary: "Air, soil, and water pollution, waste, and what it does to the climate." },
  { title: "Economy", page: "Economy", summary: "Stores, contracts, work parties, banking, and currency." },
  { title: "Government", page: "Government", summary: "Constitutions, elected offices, districts, and how government is set up." },
  { title: "Laws", page: "Laws", summary: "Creating, proposing, voting on, and enforcing laws." },
  { title: "Server", page: "Server", summary: "Hosting, setting up, and running a server." },
  { title: "Chat commands", page: "Chat_Commands", summary: "Commands players and admins can type in chat." },
  { title: "Modding", page: "Modding", summary: "Where to start modding Eco, for servers and for the game itself." },
]

export default function Wiki() {
  return (
    <Layout>
      <section className="hero hero-compact">
        <h1 className="hero-title">
          Official Eco Wiki <span className="accent">shortcuts</span>
        </h1>
        <p className="hero-tagline">
          Links to the official Eco wiki (English), for the parts of the game players use
          most.
        </p>
        <p className="hero-note">
          Checked 2026-08-01. Each link opens the wiki on Strange Loop Games' site. The wiki
          has the final word and may change after this list was made.
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
                Open wiki page ↗
              </a>
            </li>
          ))}
        </ul>
      </section>
    </Layout>
  )
}
