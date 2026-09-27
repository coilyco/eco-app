/// <reference types="node" />
import { readFileSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"
import { describe, expect, it } from "vitest"

// Read from disk: vitest stubs every CSS import, ?raw included, to "".
const SRC = path.join(fileURLToPath(import.meta.url), "../..")
const read = (rel: string) => readFileSync(path.join(SRC, rel), "utf8")

// The kit's "check a matrix, never a list" rule, for eco-app's theme: every
// text colour against every surface it can land on, WCAG 2.2. Tokens resolve
// from the vendored kit's :root with eco-theme.css on top, the same cascade
// the browser applies, so a primitive edit that breaks a pair fails here.

function rootBlock(css: string): string {
  const start = css.indexOf(":root {")
  let depth = 0
  for (let i = css.indexOf("{", start); i < css.length; i++) {
    if (css[i] === "{") depth++
    if (css[i] === "}" && --depth === 0) return css.slice(start, i)
  }
  return ""
}

function declarations(block: string): Map<string, string> {
  const out = new Map<string, string>()
  for (const m of block.matchAll(/(--[a-z0-9-]+):\s*([^;]+);/g)) out.set(m[1] ?? "", (m[2] ?? "").trim())
  return out
}

const tokens = new Map([
  ...declarations(rootBlock(read("vendor/coilyco-kit/coilyco-kit.css"))),
  ...declarations(rootBlock(read("eco-theme.css"))),
])

function resolve(name: string, seen = new Set<string>()): string {
  if (seen.has(name)) throw new Error(`token cycle at ${name}`)
  seen.add(name)
  const raw = tokens.get(name)
  if (!raw) throw new Error(`unknown token ${name}`)
  const ref = raw.match(/^var\((--[a-z0-9-]+)\)$/)
  return ref ? resolve(ref[1] ?? "", seen) : raw
}

const rgb = (hex: string) => {
  const m = hex.match(/^#([0-9a-f]{6})$/i)
  if (!m) throw new Error(`not a 6-digit hex: ${hex}`)
  return [0, 2, 4].map((i) => parseInt((m[1] ?? "").slice(i, i + 2), 16))
}
const lin = (c: number) => {
  const v = c / 255
  return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4
}
const lum = (hex: string) => {
  const [r, g, b] = rgb(hex).map(lin)
  return 0.2126 * (r ?? 0) + 0.7152 * (g ?? 0) + 0.0722 * (b ?? 0)
}
const ratio = (a: string, b: string) => {
  const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p)
  return ((x ?? 0) + 0.05) / ((y ?? 0) + 0.05)
}

// eco-app is ink-ground only, so these are the surfaces text lands on.
const SURFACES = ["--k-ground", "--k-frame", "--k-surface", "--k-raised", "--k-tile-1"]
const TEXT = [
  "--k-text",
  "--k-dim",
  "--k-brand",
  "--k-accent",
  "--eco-meteor",
  "--k-cost",
  "--k-refuse",
  "--k-grant",
  "--k-context",
  "--k-reference",
]

describe("eco theme contrast matrix", () => {
  it("resolves every token to a colour, so a silent pass is not possible", () => {
    for (const t of [...SURFACES, ...TEXT]) expect(resolve(t), t).toMatch(/^#[0-9a-f]{6}$/i)
  })

  it("holds every text colour at 4.5:1 on every surface", () => {
    const failures: string[] = []
    for (const fg of TEXT) {
      for (const bg of SURFACES) {
        const r = ratio(resolve(fg), resolve(bg))
        if (r < 4.5) failures.push(`${fg} on ${bg}: ${r.toFixed(2)}:1`)
      }
    }
    expect(failures).toEqual([])
  })

  it("holds labels on filled buttons at 4.5:1", () => {
    expect(ratio(resolve("--k-brand-fg"), resolve("--k-brand"))).toBeGreaterThanOrEqual(4.5)
    expect(ratio(resolve("--k-accent-fg"), resolve("--k-accent"))).toBeGreaterThanOrEqual(4.5)
  })

  it("holds the focus ring at 3:1 against every surface, as a non-text indicator", () => {
    const failures = SURFACES.map((bg) => [bg, ratio(resolve("--k-focus"), resolve(bg))] as const)
      .filter(([, r]) => r < 3)
      .map(([bg, r]) => `--k-focus on ${bg}: ${r.toFixed(2)}:1`)
    expect(failures).toEqual([])
  })
})
