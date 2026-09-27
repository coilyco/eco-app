import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"
import ChartFrame from "./ChartFrame"

afterEach(cleanup)

describe("ChartFrame", () => {
  it("renders axis labels as page text outside the drawing", () => {
    const { container } = render(
      <ChartFrame above={["high 42", "low 12"]} start="day 3" end="day 84">
        <svg data-testid="plot" viewBox="0 0 10 10" />
      </ChartFrame>,
    )
    for (const label of ["high 42", "low 12", "day 3", "day 84"]) {
      expect(screen.getByText(label).closest("svg")).toBeNull()
    }
    expect(container.querySelectorAll("svg text")).toHaveLength(0)
  })

  it("omits empty rows", () => {
    const { container } = render(
      <ChartFrame>
        <svg viewBox="0 0 10 10" />
      </ChartFrame>,
    )
    expect(container.querySelectorAll(".chart-axis")).toHaveLength(0)
  })
})
