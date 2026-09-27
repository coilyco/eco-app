import { describe, expect, it } from "vitest"
import manifest from "../../data/spa_routes.json"
import { redirectSpecs, routeSpecs, spaRoutes } from "./routes"

// The router and the crawl policy read one file. These assert the two halves
// stay joined: the Python service builds robots.txt, the sitemap, and each
// response's X-Robots-Tag from the same manifest, so a route the SPA renders
// but the manifest omits is a page the service answers with a 404.

describe("the shared route manifest", () => {
  it("builds a react-router element for every route and redirect in it", () => {
    const elements = spaRoutes()
    expect(elements).toHaveLength(manifest.routes.length + manifest.redirects.length)
    // Every element resolved a component — elementFor throws on an unknown name.
    expect(elements.every((el) => Boolean(el.props.element))).toBe(true)
    const paths = elements.map((el) => el.props.path)
    expect(paths).toContain("/replay")
    // /info folded into / and /social went away; both redirect home (eco-app#8385).
    expect(paths).toContain("/info")
    expect(paths).toContain("/social")
    expect(paths).not.toContain("/users/:hex")
  })

  it("declares a crawl posture on every route", () => {
    for (const spec of routeSpecs) {
      expect(["index", "noindex"], `${spec.path} crawl`).toContain(spec.crawl ?? "index")
    }
  })

  it("keeps every password-gated page out of the index", () => {
    // A gated page that is indexable would put a title and a URL in search
    // results for something a visitor cannot open.
    for (const spec of routeSpecs.filter((s) => s.gate === "password")) {
      expect(spec.crawl, `${spec.path} is gated but indexable`).toBe("noindex")
    }
  })

  it("points every redirect at a path that exists", () => {
    const live = new Set(routeSpecs.map((s) => s.path.replace("/*", "")))
    for (const { from, to } of redirectSpecs) {
      expect(live.has(to), `${from} redirects to ${to}, which no route owns`).toBe(true)
    }
  })
})
