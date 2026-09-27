import { Link } from "react-router-dom"
import Layout from "../components/Layout"

// The /uses hub: a single directory of task-framed use-case pages. This is the
// ONLY homepage card the whole use-case family gets (eco-app#99) — the
// individual pages are URL-only, reached from here, mirroring how /item is only
// reached from /items.
//
// The demand-side pages below read data eco-app already hydrates (the same
// planes /trade renders). The recipe graph and profession-value board shipped
// through eco-app#100-#103, so the hub links those existing product surfaces too.

interface UseCard {
  to: string
  testid: string
  title: string
  blurb: string
}

const LIVE: UseCard[] = [
  {
    to: "/uses/food",
    testid: "use-food",
    title: "Which food should we restock or watch?",
    blurb: "Food only, with what is in shops now, what has sold, and what people have made.",
  },
  {
    to: "/uses/demand",
    testid: "use-demand",
    title: "What people want to buy right now",
    blurb: "Items wanted but not for sale, low in stock, or overpriced, ranked, with who wants each.",
  },
  {
    to: "/uses/buy-sell",
    testid: "use-buy-sell",
    title: "Where to buy X cheapest / sell X highest",
    blurb: "Pick an item, see the cheapest shops to buy from and the shops that pay the most.",
  },
  {
    to: "/uses/arbitrage",
    testid: "use-arbitrage",
    title: "Buy low here, sell high there",
    blurb: "Items to buy low at one shop and sell high at another, biggest total profit first.",
  },
  {
    to: "/uses/resolve",
    testid: "use-resolve",
    title: "Should I make X, buy it, or find a crafter?",
    blurb: "See the recipes, what shops charge, and who has the specialty to craft it.",
  },
  {
    to: "/uses/price",
    testid: "use-price",
    title: "How should I price X?",
    blurb: "See the typical price range, what shops charge, and your craft cost before you set a price.",
  },
  {
    to: "/uses/shop-check",
    testid: "use-shop-check",
    title: "Is my shop priced right?",
    blurb: "Pick your shop and see which prices are above or below market.",
  },
  {
    to: "/recipes",
    testid: "use-recipe-graph",
    title: "What's X made from / used in",
    blurb: "Search recipes by what they make or what goes in, then open the full recipe.",
  },
  {
    to: "/jobs",
    testid: "use-profession-value",
    title: "Most profitable crafts per profession",
    blurb: "For each profession, crafts that people want, can't easily buy, and already sell well, ranked by profit × how many people want it.",
  },
]

export default function Uses() {
  return (
    <Layout>
      <section className="hero hero-compact">
        <h1 className="hero-title">
          What is eco-app <span className="accent">useful for</span>?
        </h1>
        <p className="hero-tagline">
          Each page answers one question about the server's economy. Pick the one you need.
        </p>
      </section>

      <section className="k-card-grid k-card-grid--condensed dir-cards" aria-label="use cases">
        {LIVE.map((c) => (
          <Link className="k-card dir-card" to={c.to} key={c.to} data-testid={c.testid}>
            <h2>{c.title} →</h2>
            <p>{c.blurb}</p>
          </Link>
        ))}
      </section>
    </Layout>
  )
}
