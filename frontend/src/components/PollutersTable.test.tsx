import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"
import type { Polluter, PollutersBlock } from "../lib/climateApi"
import PollutersTable from "./PollutersTable"

function device(name: string, over: Partial<Polluter> = {}): Polluter {
  return {
    object_id: name,
    type: "AirPollutionGeneratorObject",
    name,
    owner: "alice",
    owner_users: ["alice"],
    position: { x: 1, y: 2, z: 3 },
    rate_per_hour: 10,
    rate_unit: "tons/hour",
    operating: true,
    operating_efficiency: 1,
    total_tons: 5,
    ...over,
  }
}

function block(devices: Polluter[], over: Partial<PollutersBlock> = {}): PollutersBlock {
  return {
    available: true,
    devices,
    total_devices: devices.length,
    operating_devices: devices.filter((d) => d.operating).length,
    total_rate_per_hour: null,
    operating_rate_per_hour: null,
    unknown_rate_count: 0,
    rate_unit: "tons/hour",
    rate_source: "per-object",
    ...over,
  }
}

const DEVICES = [
  device("Slow", { rate_per_hour: 1, owner: "bob" }),
  device("Fast", { rate_per_hour: 30 }),
  device("Idle", { rate_per_hour: 4, operating: false, owner: "bob" }),
  device("Mystery", { rate_per_hour: null, operating: null, owner: null, position: null }),
]

const names = () => screen.getAllByTestId("polluters-row").map((r) => within(r).getAllByRole("cell")[0]?.textContent)

afterEach(cleanup)

describe("PollutersTable", () => {
  it("lists every device, biggest emitter first, with its owner and amount", () => {
    render(<PollutersTable block={block(DEVICES)} />)
    expect(names()).toEqual(["Fast", "Idle", "Slow", "Mystery"])
    const fast = screen.getAllByTestId("polluters-row")[0]!
    expect(fast).toHaveTextContent("alice")
    expect(fast).toHaveTextContent("30")
    expect(fast).toHaveTextContent("1, 2, 3")
    expect(fast).toHaveTextContent("Yes")
    const mystery = screen.getAllByTestId("polluters-row")[3]!
    expect(mystery).toHaveTextContent("No owner")
    expect(mystery).toHaveTextContent("Unknown")
  })

  it("flips the sort when the same header is clicked, and keeps unknown amounts last", () => {
    render(<PollutersTable block={block(DEVICES)} />)
    expect(screen.getByTestId("polluters-sort-rate").closest("th")).toHaveAttribute("aria-sort", "descending")
    fireEvent.click(screen.getByTestId("polluters-sort-rate"))
    expect(names()).toEqual(["Slow", "Idle", "Fast", "Mystery"])
    expect(screen.getByTestId("polluters-sort-rate").closest("th")).toHaveAttribute("aria-sort", "ascending")
  })

  it("sorts by device name from its header", () => {
    render(<PollutersTable block={block(DEVICES)} />)
    fireEvent.click(screen.getByTestId("polluters-sort-name"))
    expect(names()).toEqual(["Fast", "Idle", "Mystery", "Slow"])
  })

  it("filters to one owner and the total row follows the filter", () => {
    render(<PollutersTable block={block(DEVICES)} />)
    expect(screen.getByTestId("polluters-total")).toHaveTextContent("Total, 4 devices")
    expect(screen.getByTestId("polluters-total-rate")).toHaveTextContent("35")

    fireEvent.change(screen.getByTestId("polluters-owner-filter"), { target: { value: "bob" } })
    expect(names()).toEqual(["Idle", "Slow"])
    expect(screen.getByTestId("polluters-total")).toHaveTextContent("Total, 2 devices")
    expect(screen.getByTestId("polluters-total-rate")).toHaveTextContent("5")
    expect(screen.getByTestId("polluters-total-running")).toHaveTextContent("1 running, making 1 tons/hour")
    // Mystery is filtered out, so the unknown-amount note goes with it.
    expect(screen.queryByTestId("polluters-unknown-rate")).not.toBeInTheDocument()
  })

  it("filters to devices with no owner", () => {
    render(<PollutersTable block={block(DEVICES)} />)
    fireEvent.change(screen.getByTestId("polluters-owner-filter"), { target: { value: "\u0000no-owner" } })
    expect(names()).toEqual(["Mystery"])
  })

  it("says how many devices are left out of the totals for lack of an amount", () => {
    render(<PollutersTable block={block(DEVICES)} />)
    expect(screen.getByTestId("polluters-unknown-rate")).toHaveTextContent(
      "1 device in this list has no readable amount and is left out of the totals.",
    )
  })

  it("says the list is unavailable, not empty, when the server does not report it", () => {
    render(<PollutersTable block={block([], { available: false, rate_source: "none", rate_unit: null })} />)
    expect(screen.getByTestId("polluters-unavailable")).toBeInTheDocument()
    expect(screen.queryByTestId("polluters-table")).not.toBeInTheDocument()
    expect(screen.queryByTestId("polluters-empty")).not.toBeInTheDocument()
  })

  it("says so when the world has no polluting devices", () => {
    render(<PollutersTable block={block([])} />)
    expect(screen.getByTestId("polluters-empty")).toBeInTheDocument()
    expect(screen.queryByTestId("polluters-table")).not.toBeInTheDocument()
  })
})
