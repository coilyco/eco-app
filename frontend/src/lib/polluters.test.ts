import { describe, expect, it } from "vitest"
import type { Polluter } from "./climateApi"
import {
  ALL_OWNERS,
  filterByOwner,
  NO_OWNER,
  ownerOptions,
  sortPolluters,
  totalPolluters,
} from "./polluters"

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

const DEVICES: Polluter[] = [
  device("Slow", { rate_per_hour: 1, owner: "bob" }),
  device("Fast", { rate_per_hour: 30 }),
  device("Idle", { rate_per_hour: 4, operating: false, owner: "bob" }),
  device("Mystery", { rate_per_hour: null, operating: null, owner: null }),
]

describe("ownerOptions", () => {
  it("lists each owner once, sorted, with No owner last", () => {
    expect(ownerOptions(DEVICES)).toEqual([
      { value: "alice", label: "alice" },
      { value: "bob", label: "bob" },
      { value: NO_OWNER, label: "No owner" },
    ])
  })

  it("omits No owner when every device has one", () => {
    expect(ownerOptions([device("A")]).map((o) => o.label)).toEqual(["alice"])
  })
})

describe("filterByOwner", () => {
  it("keeps everything for all owners", () => {
    expect(filterByOwner(DEVICES, ALL_OWNERS)).toHaveLength(4)
  })

  it("keeps one owner's devices", () => {
    expect(filterByOwner(DEVICES, "bob").map((d) => d.name)).toEqual(["Slow", "Idle"])
  })

  it("keeps the unowned devices for No owner", () => {
    expect(filterByOwner(DEVICES, NO_OWNER).map((d) => d.name)).toEqual(["Mystery"])
  })
})

describe("sortPolluters", () => {
  it("sorts by rate, biggest first", () => {
    expect(sortPolluters(DEVICES, "rate", "desc").map((d) => d.name)).toEqual(["Fast", "Idle", "Slow", "Mystery"])
  })

  it("keeps an unreadable rate last when the direction flips", () => {
    expect(sortPolluters(DEVICES, "rate", "asc").map((d) => d.name)).toEqual(["Slow", "Idle", "Fast", "Mystery"])
  })

  it("sorts by name and by owner, unowned last", () => {
    expect(sortPolluters(DEVICES, "name", "asc").map((d) => d.name)).toEqual(["Fast", "Idle", "Mystery", "Slow"])
    expect(sortPolluters(DEVICES, "owner", "asc").map((d) => d.name)).toEqual(["Fast", "Idle", "Slow", "Mystery"])
  })

  it("does not reorder its input", () => {
    const before = DEVICES.map((d) => d.name)
    sortPolluters(DEVICES, "rate", "desc")
    expect(DEVICES.map((d) => d.name)).toEqual(before)
  })
})

describe("totalPolluters", () => {
  it("sums known rates and counts what is running", () => {
    expect(totalPolluters(DEVICES)).toEqual({
      devices: 4,
      running: 2,
      unknownRate: 1,
      rate: 35,
      runningRate: 31,
    })
  })

  it("is zero for no devices", () => {
    expect(totalPolluters([])).toEqual({ devices: 0, running: 0, unknownRate: 0, rate: 0, runningRate: 0 })
  })
})
