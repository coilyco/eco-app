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
    blurb: "Confirmed food recipes only, with live shelf, trade, and production evidence.",
  },
  {
    to: "/uses/demand",
    testid: "use-demand",
    title: "What's in demand right now",
    blurb: "The items buyers want that nobody is stocking — ranked, with who needs each.",
  },
  {
    to: "/uses/buy-sell",
    testid: "use-buy-sell",
    title: "Where to buy X cheapest / sell X highest",
    blurb: "Pick an item, see the cheapest shelves to buy from and the best shelves to sell into.",
  },
  {
    to: "/uses/arbitrage",
    testid: "use-arbitrage",
    title: "Buy low here, sell high there",
    blurb: "Cross-store spreads: buy at one shop, sell at another, ranked by the opportunity.",
  },
  {
    to: "/uses/resolve",
    testid: "use-resolve",
    title: "Should I make X, buy it, or find a crafter?",
    blurb: "Compare known recipes, current offers, and observed specialty holders without guessing availability.",
  },
  {
    to: "/uses/price",
    testid: "use-price",
    title: "How should I price X?",
    blurb: "Compare the market band, shelf comparison, and craft cost before you post an ask.",
  },
  {
    to: "/uses/shop-check",
    testid: "use-shop-check",
    title: "Is my shop priced right?",
    blurb: "Pick your store, compare every item's price against the market median.",
  },
  {
    to: "/recipes",
    testid: "use-recipe-graph",
    title: "What's X made from / used in",
    blurb: "Search the recipe graph by product or ingredient, then open the complete craft.",
  },
  {
    to: "/jobs",
    testid: "use-profession-value",
    title: "Value per profession",
    blurb: "See which liquid supply-gap crafts offer the best margin for each profession.",
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
          Task-framed pages that turn the live economy into a decision — pick the job you're doing.
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
