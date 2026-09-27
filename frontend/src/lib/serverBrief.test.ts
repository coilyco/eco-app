import { describe, expect, it } from "vitest"
import { SAMPLE_STATUS } from "../test/fixtures"
import {
  SERVER_BRIEF,
  groupChains,
  humanizeEnum,
  meteorState,
  parseCycle,
  parseEcoVersion,
  parseMeteorLength,
  parseWorldSize,
} from "./serverBrief"

const LIVE_DESCRIPTION =
  "<color=green>Eco</color> via <color=blue>Sirens</color> | Cycle 14 | High Collab | 100 x 100 | inclusive, friendly, highly modded"

describe("serverBrief parsers", () => {
  it("reads cycle and world size out of the live description", () => {
    expect(parseCycle(LIVE_DESCRIPTION)).toBe(14)
    expect(parseWorldSize(LIVE_DESCRIPTION)).toBe("100 × 100")
    expect(parseMeteorLength("Cycle 14 on Eco via Sirens. 60-day meteor, High Collaboration")).toBe(60)
    expect(parseEcoVersion("0.13.0.4 beta release-1024")).toBe("0.13.0.4")
    expect(humanizeEnum("HighCollaboration")).toBe("High collaboration")
  })

  it("returns null rather than guessing when the text is missing or odd", () => {
    for (const bad of [undefined, "", "Eco via Sirens", "cycle fourteen", "1km²", "x x x"]) {
      expect(parseCycle(bad)).toBeNull()
      expect(parseWorldSize(bad)).toBeNull()
      expect(parseMeteorLength(bad)).toBeNull()
      expect(parseEcoVersion(bad)).toBeNull()
    }
    expect(humanizeEnum("")).toBeNull()
    expect(humanizeEnum(null)).toBeNull()
  })

  it("prefers a destroyed meteor over any countdown", () => {
    const destroyed = {
      ...SAMPLE_STATUS,
      achievements: [
        { name: "Saved the World", text: "Destroyed the meteor on a server.\nDestroyed the meteor on Day 57, 23:13" },
      ],
    }
    expect(meteorState(destroyed)).toEqual({ kind: "destroyed", day: 57, time: "23:13" })
  })

  it("covers the countdown, no-meteor, and unknown states", () => {
    expect(meteorState(SAMPLE_STATUS)).toEqual({ kind: "countdown", days: 3 })
    expect(meteorState({ ...SAMPLE_STATUS, cycle: { ...SAMPLE_STATUS.cycle, hasMeteor: false } })).toEqual({
      kind: "none",
    })
    expect(
      meteorState({ ...SAMPLE_STATUS, cycle: { ...SAMPLE_STATUS.cycle, daysUntilMeteor: null } }),
    ).toEqual({ kind: "unknown" })
    expect(meteorState(null)).toEqual({ kind: "unknown" })
  })

  it("groups skill chains by their first skill and drops empty ones", () => {
    expect(groupChains([["A", "B", "C"], ["A", "D"], ["E", "F"], ["G"], []])).toEqual([
      { root: "A", branches: [["B", "C"], ["D"]] },
      { root: "E", branches: [["F"]] },
    ])
  })

  it("keeps per-cycle facts out of the reviewed brief", () => {
    const text = JSON.stringify(SERVER_BRIEF)
    expect(text).not.toMatch(/\bcycle\s+\d+\s*\|/i)
    expect(text).not.toMatch(/\b100\s*x\s*100\b/i)
    expect(text).not.toMatch(/\b\d+-day meteor\b/i)
  })
})
