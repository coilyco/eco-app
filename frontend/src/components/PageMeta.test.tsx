import { act, cleanup, render } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { afterEach, beforeEach, describe, expect, it } from "vitest"
import { resetPageMeta } from "../lib/pageMeta"
import PageMeta from "./PageMeta"

const spec = { path: "/cycle-14/castle", crawl: "index", title: "Castle", description: "A splat." }

beforeEach(() => {
  document.head.innerHTML = '<title>shell</title><meta property="og:url" content="https://eco-app.coilysiren.me/" />'
  resetPageMeta()
})
afterEach(() => {
  cleanup()
  document.head.innerHTML = ""
  resetPageMeta()
})

const ogUrl = () => document.head.querySelector('meta[property="og:url"]')?.getAttribute("content") ?? null

describe("PageMeta", () => {
  it("applies the route's words and its own canonical on arrival", async () => {
    render(
      <MemoryRouter initialEntries={["/cycle-14/castle"]}>
        <PageMeta spec={spec} />
      </MemoryRouter>,
    )
    await act(async () => {})
    expect(document.title).toBe("Castle")
    expect(ogUrl()).toBe("https://eco-app.coilysiren.me/cycle-14/castle")
  })

  it("drops og:url once a query string makes the URL non-canonical", async () => {
    render(
      <MemoryRouter initialEntries={["/cycle-14/castle?x=1"]}>
        <PageMeta spec={spec} />
      </MemoryRouter>,
    )
    await act(async () => {})
    expect(ogUrl()).toBeNull()
  })
})
