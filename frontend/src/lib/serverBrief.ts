import type { EcoStatus } from "./api"
import brief from "../../../data/server_brief.json"

// The homepage's reviewed copy lives in data/server_brief.json (game-dev owns
// the wording). Anything that changes per cycle is deliberately absent there
// and parsed from live status below, because a second copy is how the old
// Discord info block went stale.

// `source` is the link label when `href` exists, and the reason there is no
// link when it does not. /mods renders both; the homepage shows name and
// summary only.
export interface ModItem {
  name: string
  summary: string
  href?: string
  source?: string
  author?: string
  version?: string
  includes?: Array<{ name: string; summary: string }>
}

export interface BenchedMod extends ModItem {
  reason: string
}

export interface ServerBrief {
  reviewedOn: string
  join: { serverName: string; discordUrl: string; steps: string[] }
  nextCycle?: string
  configs: Array<{ group: string; items: Array<{ label: string; value: string }> }>
  mods: Array<{ group: string; items: ModItem[] }>
  serverPlumbing?: ModItem[]
  servicePlugins?: ModItem[]
  benched?: BenchedMod[]
  skillTrees: { pickOne: string[]; chains: string[][] }
  gameplayNotes: Array<{ title: string; body: string }>
}

export const SERVER_BRIEF: ServerBrief = brief

// The server name, cycle, and world size only exist inside the free-text
// description ("... | Cycle 14 | High Collab | 100 x 100 | ..."), and the
// meteor length only inside the detailed one ("60-day meteor").
export function parseCycle(description: string | undefined): number | null {
  const m = description?.match(/\bcycle\s+(\d+)\b/i)
  return m ? Number(m[1]) : null
}

export function parseWorldSize(description: string | undefined): string | null {
  const m = description?.match(/\b(\d{2,4})\s*[x×]\s*(\d{2,4})\b/i)
  return m ? `${m[1]} × ${m[2]}` : null
}

export function parseMeteorLength(detailed: string | undefined): number | null {
  const m = detailed?.match(/\b(\d+)[-\s]day meteor\b/i)
  return m ? Number(m[1]) : null
}

// "0.13.0.4 beta release-1024" reads as 0.13.0.4 to a player.
export function parseEcoVersion(version: string | undefined): string | null {
  const m = version?.match(/\d+(?:\.\d+)+/)
  return m ? m[0] : null
}

// "HighCollaboration" -> "High collaboration".
export function humanizeEnum(value: string | undefined | null): string | null {
  if (!value) return null
  const words = value.replace(/([a-z])([A-Z])/g, "$1 $2").toLowerCase()
  return words.charAt(0).toUpperCase() + words.slice(1)
}

export type MeteorState =
  | { kind: "unknown" }
  | { kind: "none" }
  | { kind: "countdown"; days: number }
  | { kind: "destroyed"; day: number; time: string | null }

// Once the meteor is destroyed, /info stops sending a countdown and the only
// signal left is the world achievement text: "Destroyed the meteor on Day 57,
// 23:13". It wins over a countdown so a stale day count never contradicts it.
export function meteorDestroyed(
  achievements: EcoStatus["achievements"] | undefined,
): { day: number; time: string | null } | null {
  for (const a of achievements ?? []) {
    const m = a.text?.match(/destroyed the meteor on day\s+(\d+)(?:,\s*(\d{1,2}:\d{2}))?/i)
    if (m) return { day: Number(m[1]), time: m[2] ?? null }
  }
  return null
}

export function meteorState(status: EcoStatus | null): MeteorState {
  if (!status) return { kind: "unknown" }
  const destroyed = meteorDestroyed(status.achievements)
  if (destroyed) return { kind: "destroyed", ...destroyed }
  if (!status.cycle.hasMeteor) return { kind: "none" }
  if (status.cycle.daysUntilMeteor !== null) {
    return { kind: "countdown", days: status.cycle.daysUntilMeteor }
  }
  return { kind: "unknown" }
}

// Group the prerequisite chains by their first skill, so the tree reads as
// "Campfire Cooking" with its branches under it rather than 16 loose lines.
export function groupChains(chains: string[][]): Array<{ root: string; branches: string[][] }> {
  const groups: Array<{ root: string; branches: string[][] }> = []
  for (const chain of chains) {
    const [root, ...rest] = chain
    if (!root || rest.length === 0) continue
    const group = groups.find((g) => g.root === root)
    if (group) group.branches.push(rest)
    else groups.push({ root, branches: [rest] })
  }
  return groups
}
