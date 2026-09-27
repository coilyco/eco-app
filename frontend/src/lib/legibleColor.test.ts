import { describe, expect, it } from "vitest"
import { contrast, legibleColor } from "./legibleColor"

const rgb = (hex: string) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16)) as [number, number, number]
const RAISED = rgb("#1d331b")

describe("legibleColor", () => {
  it("leaves a colour that already reads alone", () => {
    expect(legibleColor("#f0b35c")).toBe("#f0b35c")
  })

  it("lifts every failing colour to at least 4.5:1 on the raised surface", () => {
    for (const hex of ["#b72fc1", "#000000", "#800080", "#008080", "#0000ff", "#3a6935"]) {
      const out = legibleColor(hex)
      expect(out).not.toBe(hex)
      expect(contrast(rgb(out), RAISED)).toBeGreaterThanOrEqual(4.5)
    }
  })

  it("expands short hex and ignores an alpha channel", () => {
    expect(contrast(rgb(legibleColor("#80f")), RAISED)).toBeGreaterThanOrEqual(4.5)
    expect(legibleColor("#fffc")).toBe("#fffc")
  })
})
