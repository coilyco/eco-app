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
    // Cyan clears 4.5:1 on the raised surface, so it renders as chosen. The
    // orange sits at 4.31:1 and comes back one step lighter.
    expect(screen.getByText("Trade", { exact: false }).closest("span")).toHaveStyle({ color: "#00ffff" })
    expect(screen.getByText("Emprorium")).not.toHaveStyle({ color: "#e47028" })
    expect(screen.getByText("StiFFFs", { exact: false }).closest("span")).toHaveAttribute("style")
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
    // Teal fails on the dark ground, so it renders as a lighter teal.
    expect(screen.getByText("x")).not.toHaveStyle({ color: "rgb(0, 128, 128)" })
  })

  it("lifts a player colour that fails contrast, keeping its hue", () => {
    render(<EcoRichText text={"<color=#b72fc1>Dark Orchid Goods</color>"} />)
    const color = screen.getByText("Dark Orchid Goods").style.color
    const [r, g, b] = color.match(/\d+/g)!.map(Number)
    expect([r, g, b]).not.toEqual([0xb7, 0x2f, 0xc1])
    // Lightened toward white, so blue stays the strongest channel and green the weakest.
    expect(b).toBeGreaterThan(g)
    expect(r).toBeGreaterThan(g)
  })
})
