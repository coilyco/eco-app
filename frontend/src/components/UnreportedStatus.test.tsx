import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"
import StatGrid from "./StatGrid"
import { SAMPLE_STATUS } from "../test/fixtures"
import type { EcoStatus } from "../lib/api"

// The service passes an /info field the game server did not send through as
// null rather than 0 (eco-app#214). The SPA has to say "unknown", never "0" —
// a zero here reads as a real measurement.
const UNREPORTED: EcoStatus = {
  ...SAMPLE_STATUS,
  players: { ...SAMPLE_STATUS.players, online: null, total: null, peakActive: null },
  world: { ...SAMPLE_STATUS.world, plants: null, laws: null, totalCulture: null },
  cycle: { ...SAMPLE_STATUS.cycle, timeSinceStartS: null, daysUntilMeteor: null },
}

// vitest runs without `globals`, so testing-library's auto-cleanup afterEach
// never registers. These cases render the same component twice, so unmount
// explicitly rather than letting the second render find two matches.
afterEach(cleanup)

describe("unreported /info fields", () => {
  it("renders stat tiles as a dash rather than zero", () => {
    render(<StatGrid status={UNREPORTED} />)
    const values = screen.getAllByText("—")
    expect(values.length).toBeGreaterThanOrEqual(5)
    expect(screen.queryByText("0")).toBeNull()
  })

  it("keeps a genuine zero visible as zero", () => {
    const zeroed: EcoStatus = {
      ...SAMPLE_STATUS,
      world: { ...SAMPLE_STATUS.world, laws: 0 },
    }
    render(<StatGrid status={zeroed} />)
    expect(screen.getByText("0")).toBeInTheDocument()
  })
})
