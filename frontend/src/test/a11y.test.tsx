import { act, cleanup, render } from "@testing-library/react"
import axe from "axe-core"
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest"
import App from "../App"
import { routeSpecs } from "../routes"
import SHELL from "../../index.html?raw"

// The kit's accessibility gate (website cypress/e2e/accessibility.cy.ts) with
// its exact tag set, run on every SPA route. jsdom has no layout, so colour
// contrast and scroll-region rules cannot be judged here: the contrast matrix
// test covers colour, and the browser check covers the rest at real viewports
// (docs/frontend/kit-checks.md).

const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "best-practice"]
const LAYOUT_RULES = ["color-contrast", "scrollable-region-focusable"]

// A concrete URL for each manifest path that takes a parameter.
const SAMPLE: Record<string, string> = {
  "/jobs/*": "/jobs",
  "/users/:hex": "/users/636f696c79736972656e",
  "/item": "/item?name=IronBarItem",
  "/recipe": "/recipe?id=IronBar",
}

const urls = routeSpecs.map((spec) => SAMPLE[spec.path] ?? spec.path)

// The shell's own <html lang> and <title>, so axe judges the document the
// browser gets rather than jsdom's empty one.

beforeAll(() => {
  document.documentElement.lang = SHELL.match(/<html lang="([^"]+)"/)?.[1] ?? ""
  document.title = SHELL.match(/<title>([^<]*)<\/title>/)?.[1] ?? ""
  // The worst state first: every data plane is down. Each page must still be
  // a well-formed document with its heading, landmarks, and labelled controls.
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")))
})

afterAll(() => {
  vi.unstubAllGlobals()
})

afterEach(cleanup)

describe("accessibility on every route, with every data plane down", () => {
  it("covers the whole route manifest, so a silent pass is not possible", () => {
    expect(urls.length).toBeGreaterThan(20)
  })

  it.each(urls)("raises no axe violation on %s", async (url) => {
    window.history.pushState({}, "", url)
    render(<App />)
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0))
    })
    const results = await axe.run(document, {
      runOnly: TAGS,
      rules: Object.fromEntries(LAYOUT_RULES.map((id) => [id, { enabled: false }])),
    })
    const report = results.violations
      .map((v) => `${v.impact ?? "?"} ${v.id} x${v.nodes.length}: ${v.nodes.slice(0, 2).map((n) => n.target.join(" ")).join(" | ")}`)
      .join("\n")
    expect(report, `${url}\n${report}`).toBe("")
  })
})
