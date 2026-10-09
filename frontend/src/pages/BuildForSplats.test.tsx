import { cleanup, render, screen, within } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { afterEach, describe, expect, it } from "vitest"
import manifest from "../../../data/spa_routes.json"
import BuildForSplats, { captureFacts } from "./BuildForSplats"

function renderGuide(props: Parameters<typeof BuildForSplats>[0] = {}) {
  return render(
    <MemoryRouter initialEntries={["/build-for-splats"]}>
      <BuildForSplats {...props} />
    </MemoryRouter>,
  )
}

const section = (name: RegExp) => screen.getByRole("heading", { level: 2, name }).closest("section") as HTMLElement

afterEach(cleanup)

describe("Build for a splat guide", () => {
  it("has one heading level 1 and Kai's three points as level 2 sections, each quoted", () => {
    renderGuide()
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1)
    expect(within(section(/Put the best part/)).getByText(/the most interesting parts need to be visible from a 45 degree orbit/)).toBeInTheDocument()
    expect(within(section(/Give each wall/)).getByText(/same color objects bleed into each other/)).toBeInTheDocument()
    expect(within(section(/Lean realistic/)).getByText(/more realistic builds render in higher resolution/)).toBeInTheDocument()
  })

  it("keeps the director's criteria: 45 degrees is a rule of thumb, and realism is not claimed alone", () => {
    renderGuide()
    expect(screen.getByRole("heading", { level: 2, name: /Put the best part/ })).not.toHaveTextContent(/45/)
    expect(screen.getByText(/That 45 degrees is Kai’s rule of thumb, not a measured setting/)).toBeInTheDocument()
    const realism = section(/Lean realistic/)
    expect(within(realism).getByText(/This one is Kai’s observation/)).toBeInTheDocument()
    expect(within(realism).getByText(/not a clean test of realism alone/)).toBeInTheDocument()
  })

  it("keeps game-dev's correction: no sharpest for BioDrive, no overstated only in point 2", () => {
    renderGuide()
    expect(section(/Lean realistic/)).not.toHaveTextContent(/sharpest/i)
    expect(section(/Lean realistic/)).toHaveTextContent(/the most points for its size and the finest small detail/)
    expect(section(/Give each wall/)).not.toHaveTextContent(/\bonly\b/i)
    expect(section(/Give each wall/)).toHaveTextContent(/learns an edge best where the color changes/)
    expect(section(/Give each wall/)).toHaveTextContent(/the edge often never forms/)
  })

  it("puts each point's own splat under it, as a captioned link, with the still left to the caption", () => {
    renderGuide()
    const expected = [
      [/Put the best part/, "The castle in 3D", "/cycle-14/castle", /bell tower and keep towers/],
      [/Give each wall/, "Terrace farms in 3D", "/cycle-14/terrace-farms", /Tan fences along each terrace edge/],
      [/Lean realistic/, "BioDrive station in 3D", "/cycle-14/biodrive", /lane lines, curbs, and lamp posts/],
    ] as const
    for (const [heading, label, href, caption] of expected) {
      const figure = within(section(heading)).getByRole("figure")
      expect(within(figure).getByRole("link", { name: label })).toHaveAttribute("href", href)
      expect(within(figure).getByText(caption)).toBeInTheDocument()
      // The caption is the words, so the picture is not announced as a second copy of them.
      expect(figure.querySelector("img")).toHaveAttribute("alt", "")
      expect(figure.querySelector("img")?.getAttribute("src")).toBeTruthy()
    }
  })

  it("shows no picture for a point whose splat is not in the table, and says so for an empty list", () => {
    const { container } = renderGuide({ specs: [] })
    expect(container.querySelectorAll("figure")).toHaveLength(0)
    expect(screen.getByTestId("guide-splats-empty")).toHaveTextContent("No 3D scenes are published yet.")
    for (const p of container.querySelectorAll(".guide-point p")) expect(p.textContent).toBeTruthy()
  })

  it("links the underground base nowhere, since it has no splat", () => {
    renderGuide()
    expect(screen.getByText(/there is no splat of it to show you here/)).toBeInTheDocument()
    expect(screen.getByText(/the slower interior method was dropped for time/)).toBeInTheDocument()
    expect(screen.queryByRole("link", { name: /undercastle/i })).not.toBeInTheDocument()
  })

  it("links every cycle 14 splat page in the manifest, and the index", () => {
    renderGuide()
    const list = section(/What the cycle 14 captures show/)
    const splats = manifest.routes.filter((r) => r.path.startsWith("/cycle-14/"))
    expect(splats.length).toBeGreaterThan(0)
    for (const route of splats) {
      expect(within(list).getByRole("link", { name: route.title as string })).toHaveAttribute("href", route.path)
    }
    expect(within(list).getByRole("link", { name: "All of cycle 14 in 3D" })).toHaveAttribute("href", "/cycle-14")
  })

  it("shows each capture's frames and points as the description states them", () => {
    renderGuide()
    expect(screen.getByText(/300 frames, 386,757 points/)).toBeInTheDocument()
    expect(screen.getByText(/315 frames, 616,523 points/)).toBeInTheDocument()
    expect(screen.getByText(/287 frames, 881,854 points/)).toBeInTheDocument()
    expect(captureFacts("Some other shape of words.")).toBe("Some other shape of words.")
  })

  it("invites a nomination in Discord and promises no capture, ending the page", () => {
    renderGuide()
    const closing = section(/Build for cycle 15/)
    expect(within(closing).getByText(/Not every build gets captured/)).toBeInTheDocument()
    expect(within(closing).getByText(/nominate it in #eco-cycle-15/)).toBeInTheDocument()
    const headings = screen.getAllByRole("heading", { level: 2 })
    expect(headings[headings.length - 1]).toHaveTextContent("Build for cycle 15")
  })
})
