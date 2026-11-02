// Sorting, filtering and totals for the pollution card's device table. Pure, so
// the component stays markup and the rules are testable without rendering.

import type { Polluter } from "./climateApi"

export type PolluterSortKey = "rate" | "name" | "owner"
export type SortDirection = "asc" | "desc"

// A select value cannot be null, and an owner could in principle be named
// anything, so devices with no readable owner get a value no name can match.
export const NO_OWNER = "\u0000no-owner"
export const ALL_OWNERS = ""

export function ownerOf(device: Polluter): string | null {
  return device.owner
}

export function ownerOptions(devices: Polluter[]): { value: string; label: string }[] {
  const names = new Set<string>()
  let unowned = false
  for (const d of devices) {
    if (d.owner) names.add(d.owner)
    else unowned = true
  }
  const options = [...names]
    .sort((a, b) => a.localeCompare(b))
    .map((name) => ({ value: name, label: name }))
  if (unowned) options.push({ value: NO_OWNER, label: "No owner" })
  return options
}

export function filterByOwner(devices: Polluter[], owner: string): Polluter[] {
  if (owner === ALL_OWNERS) return devices
  if (owner === NO_OWNER) return devices.filter((d) => !d.owner)
  return devices.filter((d) => d.owner === owner)
}

// Unknown values sort last in both directions: a device with no readable rate
// is not "the smallest emitter", and flipping the sort must not float it to the top.
export function sortPolluters(
  devices: Polluter[],
  key: PolluterSortKey,
  direction: SortDirection,
): Polluter[] {
  const sign = direction === "asc" ? 1 : -1
  const read = (d: Polluter): number | string | null =>
    key === "rate" ? d.rate_per_hour : key === "owner" ? d.owner : d.name
  return [...devices].sort((a, b) => {
    const x = read(a)
    const y = read(b)
    if (x === null && y === null) return a.name.localeCompare(b.name)
    if (x === null) return 1
    if (y === null) return -1
    const order = typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y))
    return order !== 0 ? sign * order : a.name.localeCompare(b.name)
  })
}

export interface PolluterTotals {
  devices: number
  running: number
  // Devices whose rate could not be read. Left out of both sums below.
  unknownRate: number
  rate: number
  runningRate: number
}

export function totalPolluters(devices: Polluter[]): PolluterTotals {
  const totals: PolluterTotals = { devices: devices.length, running: 0, unknownRate: 0, rate: 0, runningRate: 0 }
  for (const d of devices) {
    if (d.operating === true) totals.running += 1
    if (d.rate_per_hour === null) {
      totals.unknownRate += 1
      continue
    }
    totals.rate += d.rate_per_hour
    if (d.operating === true) totals.runningRate += d.rate_per_hour
  }
  return totals
}
