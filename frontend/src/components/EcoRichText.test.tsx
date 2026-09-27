import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"
import EcoRichText from "./EcoRichText"

afterEach(cleanup)

describe("EcoRichText", () => {
  it("renders nested Eco color markup as safe spans", () => {
    render(
      <p data-testid="name">
        <EcoRichText text={'StiFFFy The Smithy\'s <color=#FF00FF>StiFFFs <color=#00FFFF>Trade <color=#e47028>Emprorium</color></color></color>'} />
      </p>,
    )

    const name = screen.getByTestId("name")
    expect(name).toHaveTextContent("StiFFFy The Smithy's StiFFFs Trade Emprorium")
    expect(screen.getByText("StiFFFs", { exact: false }).closest("span")).toHaveStyle({ color: "#ff00ff" })
    expect(screen.getByText("Emprorium")).toHaveStyle({ color: "#e47028" })
    expect(name).not.toHaveTextContent("<color")
  })

  it("drops unsupported markup and rejects arbitrary color values", () => {
    render(
      <p data-testid="name">
        <EcoRichText text={'<color=red;background:url(x)><b>Safe</b></color>'} />
      </p>,
    )

    expect(screen.getByTestId("name")).toHaveTextContent("Safe")
    expect(screen.getByText("Safe")).not.toHaveAttribute("style")
  })

  it("maps the game's dark named colours onto the theme", () => {
    render(<EcoRichText text={"<color=green>Eco</color> via <color=blue>Sirens</color> <color=teal>x</color>"} />)

    expect(screen.getByText("Eco").getAttribute("style")).toContain("var(--k-brand)")
    expect(screen.getByText("Sirens").getAttribute("style")).toContain("var(--k-accent)")
    expect(screen.getByText("x")).toHaveStyle({ color: "rgb(0, 128, 128)" })
  })
})
