import { afterEach, beforeEach, describe, expect, it } from "vitest"
import { applyPageMeta, canonicalFor, resetPageMeta } from "./pageMeta"

const SHELL = `
  <title>shell title</title>
  <meta name="description" content="shell description" />
  <meta property="og:title" content="shell og title" />
  <meta property="og:description" content="shell og description" />
  <meta property="og:url" content="https://eco-app.coilysiren.me/" />
`
const content = (selector: string) => document.head.querySelector(selector)?.getAttribute("content") ?? null

beforeEach(() => {
  document.head.innerHTML = SHELL
  resetPageMeta()
})
afterEach(() => {
  document.head.innerHTML = ""
  resetPageMeta()
})

describe("applyPageMeta", () => {
  it("sets the title, description and the OpenGraph pair from a route's words", () => {
    applyPageMeta({ title: "Castle", description: "A splat.", canonical: "https://eco-app.coilysiren.me/cycle-14/castle" })
    expect(document.title).toBe("Castle")
    expect(content('meta[name="description"]')).toBe("A splat.")
    expect(content('meta[property="og:title"]')).toBe("Castle")
    expect(content('meta[property="og:description"]')).toBe("A splat.")
    expect(content('meta[property="og:url"]')).toBe("https://eco-app.coilysiren.me/cycle-14/castle")
  })

  it("puts the shell's words back for a route with none, so a title never outlives its page", () => {
    applyPageMeta({ title: "Castle", description: "A splat.", canonical: "https://eco-app.coilysiren.me/cycle-14/castle" })
    applyPageMeta({ canonical: "https://eco-app.coilysiren.me/items" })
    expect(document.title).toBe("shell title")
    expect(content('meta[name="description"]')).toBe("shell description")
    expect(content('meta[property="og:title"]')).toBe("shell og title")
    expect(content('meta[property="og:description"]')).toBe("shell og description")
    expect(content('meta[property="og:url"]')).toBe("https://eco-app.coilysiren.me/items")
  })

  it("removes og:url where the server names no canonical, rather than leaving the home page's", () => {
    applyPageMeta({ title: "x", description: "y", canonical: null })
    expect(document.head.querySelector('meta[property="og:url"]')).toBeNull()
  })

  it("creates a tag the shell lacks instead of failing", () => {
    document.head.innerHTML = "<title>bare</title>"
    resetPageMeta()
    applyPageMeta({ title: "T", description: "D", canonical: "https://eco-app.coilysiren.me/map" })
    expect(content('meta[name="description"]')).toBe("D")
    expect(content('meta[property="og:url"]')).toBe("https://eco-app.coilysiren.me/map")
  })
})

describe("canonicalFor", () => {
  const site = "https://eco-app.coilysiren.me"
  it("names an indexed route's own path", () => {
    expect(canonicalFor({ path: "/trade" }, "/trade", "")).toBe(`${site}/trade`)
    expect(canonicalFor({ path: "/", crawl: "index" }, "/", "")).toBe(`${site}/`)
  })
  it("names none for a noindex route", () => {
    expect(canonicalFor({ path: "/item", crawl: "noindex" }, "/item", "")).toBeNull()
  })
  it("names none for a query string, which costs a page its canonical", () => {
    expect(canonicalFor({ path: "/trade" }, "/trade", "?item=Iron")).toBeNull()
  })
  it("treats a path deeper than a wildcard route by its deep posture", () => {
    const jobs = { path: "/jobs/*", crawl: "index", deepCrawl: "noindex" }
    expect(canonicalFor(jobs, "/jobs", "")).toBe(`${site}/jobs`)
    expect(canonicalFor(jobs, "/jobs/professions", "")).toBeNull()
  })
})
