import { cleanup, render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { afterEach, describe, expect, it } from "vitest"
import manifest from "../../../data/spa_routes.json"
import Cycle14Index, { splatSpecs } from "./Cycle14Index"

function renderIndex(props: Parameters<typeof Cycle14Index>[0] = {}) {
  return render(
    <MemoryRouter initialEntries={["/cycle-14"]}>
      <Cycle14Index {...props} />
    </MemoryRouter>,
  )
}

afterEach(cleanup)

describe("Cycle 14 index", () => {
  it("says so when no scene is published, rather than showing an empty grid", () => {
    renderIndex({ specs: [] })
    expect(screen.getByTestId("cycle-14-empty")).toHaveTextContent("No 3D scenes are published yet.")
    expect(screen.queryByRole("region", { name: /3D scenes/i })).not.toBeInTheDocument()
  })

  it("lists a card for every /cycle-14/* route in the manifest, linked to it", () => {
    const expected = manifest.routes.filter((r) => r.path.startsWith("/cycle-14/"))
    expect(expected.length).toBeGreaterThan(0)
    renderIndex()
    for (const route of expected) {
      const link = screen.getByRole("link", { name: new RegExp(route.title as string) })
      expect(link).toHaveAttribute("href", route.path)
      expect(link).toHaveTextContent(route.description as string)
    }
    expect(screen.getAllByRole("link", { name: /→/ })).toHaveLength(expected.length)
  })

  it("picks up a route added to the table with no edit to the page", () => {
    renderIndex({
      specs: splatSpecs([
        ...(manifest.routes as never[]),
        { path: "/cycle-14/undercastle", title: "Undercastle", description: "Under the castle." } as never,
      ]),
    })
    expect(screen.getByRole("link", { name: /Undercastle/ })).toHaveAttribute("href", "/cycle-14/undercastle")
  })

  it("leaves out itself, other routes, and wildcards, and shows a still where the route has art", () => {
    const paths = splatSpecs().map((s) => s.path)
    expect(paths).not.toContain("/cycle-14")
    expect(paths.every((p) => p.startsWith("/cycle-14/") && !p.includes("*"))).toBe(true)
    const { container } = renderIndex()
    const stills = container.querySelectorAll("img.cycle-card__still")
    expect(stills.length).toBe(manifest.routes.filter((r) => r.path.startsWith("/cycle-14/") && "art" in r).length)
    for (const img of stills) expect(img.getAttribute("src")).toBeTruthy()
  })

  it("has one heading level 1 and each card named by its own level 2", () => {
    renderIndex()
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1)
    expect(screen.getAllByRole("heading", { level: 2 }).length).toBe(splatSpecs().length)
  })
})
