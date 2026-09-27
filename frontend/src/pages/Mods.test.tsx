import { cleanup, render, screen, within } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"
import { MemoryRouter } from "react-router-dom"
import Mods from "./Mods"
import { SERVER_BRIEF, type ModItem } from "../lib/serverBrief"

afterEach(cleanup)

function renderMods() {
  return render(
    <MemoryRouter initialEntries={["/mods"]}>
      <Mods />
    </MemoryRouter>,
  )
}

const slug = (name: string) =>
  name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")

const everyMod: ModItem[] = [
  ...SERVER_BRIEF.mods.flatMap((g) => g.items),
  ...(SERVER_BRIEF.serverPlumbing ?? []),
  ...(SERVER_BRIEF.servicePlugins ?? []),
  ...(SERVER_BRIEF.benched ?? []),
]

describe("Mod catalog", () => {
  it("renders every mod in the brief and nothing else", () => {
    const { container } = renderMods()

    expect(container.querySelectorAll("li.mods-card")).toHaveLength(everyMod.length)
    for (const mod of everyMod) {
      expect(screen.getByTestId(`mod-${slug(mod.name)}`)).toHaveTextContent(mod.name)
    }
  })

  it("links out where a source is published and says why where it is not", () => {
    renderMods()

    for (const mod of everyMod) {
      const card = screen.getByTestId(`mod-${slug(mod.name)}`)
      const link = card.querySelector("a.mods-source")
      if (mod.href) {
        expect(link).toHaveAttribute("href", mod.href)
        expect(link).toHaveAttribute("target", "_blank")
      } else {
        expect(link).toBeNull()
        expect(card).toHaveTextContent(mod.source ?? "Source not published")
      }
    }
  })

  it("lists a toolbox's modules inside its own card", () => {
    renderMods()

    const toolboxes = everyMod.filter((m) => m.includes?.length)
    expect(toolboxes.length).toBeGreaterThan(0)
    for (const mod of toolboxes) {
      const list = within(screen.getByTestId(`mod-${slug(mod.name)}`)).getByRole("list", {
        name: `${mod.name} modules`,
      })
      expect(within(list).getAllByRole("listitem")).toHaveLength(mod.includes!.length)
    }
  })

  it("shows the benched section only when the brief benches something", () => {
    renderMods()

    const benched = SERVER_BRIEF.benched ?? []
    if (benched.length === 0) {
      expect(screen.queryByTestId("mods-benched")).not.toBeInTheDocument()
    } else {
      expect(screen.getByTestId("mods-benched")).toHaveTextContent(benched[0].reason)
    }
  })

  it("keeps mod names unique, since each card is addressed by its name", () => {
    const slugs = everyMod.map((m) => slug(m.name))
    expect(new Set(slugs).size).toBe(slugs.length)
  })
})
