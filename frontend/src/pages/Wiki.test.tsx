import { cleanup, render, screen, within } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"
import { MemoryRouter } from "react-router-dom"
import Wiki from "./Wiki"

afterEach(cleanup)

describe("Eco Wiki snapshot", () => {
  it("renders the curated stable-page index", () => {
    render(
      <MemoryRouter initialEntries={["/wiki"]}>
        <Wiki />
      </MemoryRouter>,
    )

    const topics = within(screen.getByTestId("wiki-topics")).getAllByRole("listitem")
    expect(topics).toHaveLength(14)
    expect(screen.getByRole("heading", { name: "Getting started" })).toBeInTheDocument()
    expect(screen.getByRole("heading", { name: "Modding" })).toBeInTheDocument()
    expect(screen.getAllByRole("link", { name: "Open wiki page ↗" })[0]).toHaveAttribute(
      "href",
      "https://wiki.play.eco/en/index.php?stable=1&title=Getting_Started",
    )
  })
})
