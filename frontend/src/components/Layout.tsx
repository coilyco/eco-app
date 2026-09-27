import type { ReactNode } from "react"
import { Link, NavLink } from "react-router-dom"
import ecoIcon from "../assets/eco-icon.png"
import Footer from "./Footer"

interface LayoutProps {
  children: ReactNode
  fetchedAtISO?: string
}

// The nav mirrors the collapsed page set from the eco-app#90 IA cleanup —
// progression lives inside Jobs, the trades ledger inside Trade, and climate
// inside World, so they get no nav entry of their own; Server is renamed Info
// and Map is titled World. Recipes (eco-app#101) earns its own entry as a
// primary browse surface, peer to Items and Crafting.
const NAV: Array<[string, string]> = [
  ["/", "Home"],
  ["/info", "Info"],
  ["/mods", "Mods"],
  ["/wiki", "Wiki"],
  ["/jobs", "Jobs"],
  ["/trade", "Trade"],
  ["/items", "Items"],
  ["/recipes", "Recipes"],
  ["/crafting", "Crafting"],
  ["/map", "World"],
]

function Brand() {
  return (
    <Link to="/" className="k-nav__brand">
      <img className="eco-mark" src={ecoIcon} alt="" width={28} height={28} />
      <span>eco-app</span>
    </Link>
  )
}

// One flat surface on the kit's page shell (eco-app#8308): the nav and footer
// are the kit's frame bands, and .k-page paints the planet texture behind
// every page, so /, /info, and /jobs read as one site. Below 600px the kit
// hides the nav's brand and shows the stub's instead.
export default function Layout({ children, fetchedAtISO }: LayoutProps) {
  return (
    <div className="page k-page">
      <header className="k-nav-stub">
        <Brand />
      </header>
      <nav className="k-nav" aria-label="primary">
        <div className="k-nav__identity">
          <Brand />
          <p className="k-eyebrow k-nav__eyebrow">Eco via Sirens</p>
        </div>
        <ul className="k-nav__links k-nav__links--paired">
          {NAV.map(([to, label]) => (
            <li key={to}>
              <NavLink to={to} end={to === "/"}>
                {label}
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>

      <main className="content">{children}</main>

      <Footer fetchedAtISO={fetchedAtISO} />
    </div>
  )
}
