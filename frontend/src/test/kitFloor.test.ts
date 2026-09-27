/// <reference types="node" />
import { readFileSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"
import { describe, expect, it } from "vitest"

// The coilyco kit's type floor, ported from website packages/kit/tests/
// type-floor.test.ts: nothing renders text below 16px, in any register. The
// kit checks its own rules; this checks eco-app's, which is where the site's
// page styles live.

// Read from disk: vitest stubs every CSS import, ?raw included, to "".
const SRC = path.join(fileURLToPath(import.meta.url), "../..")
const read = (rel: string) => readFileSync(path.join(SRC, rel), "utf8")
const KIT = read("vendor/coilyco-kit/coilyco-kit.css")
const SITE = read("index.css") + "\n" + read("eco-theme.css")
const FLOOR_PX = 16
const ROOT_PX = 16

// The /map donut draws 100 viewBox units at a fixed 200px, so its SVG text
// renders at twice its CSS number: 8 units is 16px. Every other in-drawing
// label sizes itself with calc() through useSvgTextScale, and the browser
// check measures all of them where they render (docs/frontend/kit-checks.md).
const SVG_TEXT = /\.eco-donut-(num|sub)\b/

const literalPx = (raw: string): number | null => {
  const m = raw.match(/^([\d.]*\.?\d+)(rem|px|em)$/)
  if (!m) return null
  const n = parseFloat(m[1] ?? "")
  return m[2] === "px" ? n : n * ROOT_PX
}

const typeTokens = (): Map<string, number> => {
  const tokens = new Map<string, number>()
  for (const m of KIT.matchAll(/(--k-t-[a-z0-9-]+):\s*([^;}]+)/g)) {
    const raw = (m[2] ?? "").trim()
    const px = literalPx(raw) ?? literalPx((raw.match(/clamp\(([^,]+),/)?.[1] ?? "").trim())
    if (px !== null) tokens.set(m[1] ?? "", px)
  }
  return tokens
}

const siteSizes = () => {
  const tokens = typeTokens()
  const found: { selector: string; size: string; px: number }[] = []
  for (const rule of SITE.replace(/\/\*[\s\S]*?\*\//g, "").split("}")) {
    const brace = rule.indexOf("{")
    if (brace === -1) continue
    const selector = rule.slice(0, brace).trim()
    if (SVG_TEXT.test(selector)) continue
    for (const decl of rule.slice(brace).matchAll(/font-size:\s*([^;}]+)/g)) {
      const raw = (decl[1] ?? "").trim()
      const token = raw.match(/^var\((--k-t-[a-z0-9-]+)\)$/)
      const clampMin = raw.match(/^clamp\(([^,]+),/)?.[1]?.trim() ?? ""
      const px = token
        ? (tokens.get(token[1] ?? "") ?? null)
        : (literalPx(raw) ?? literalPx(clampMin))
      if (px !== null) found.push({ selector, size: raw, px })
    }
  }
  return found
}

describe("eco-app type floor", () => {
  it("resolves the kit's type scale, so a silent pass is not possible", () => {
    expect([...typeTokens().keys()].sort()).toEqual([
      "--k-t-body",
      "--k-t-display",
      "--k-t-h1",
      "--k-t-h2",
      "--k-t-h3",
      "--k-t-lead",
    ])
  })

  it("finds site font sizes to check, so a silent pass is not possible", () => {
    expect(siteSizes().length).toBeGreaterThan(40)
  })

  it("renders no site text below the floor", () => {
    const under = siteSizes().filter((d) => d.px < FLOOR_PX)
    expect(under.map((d) => `${d.selector} { font-size: ${d.size} }`)).toEqual([])
  })

  it("keeps the root free, so the floor scales with the reader", () => {
    expect(SITE).not.toMatch(/(^|[\s,}])html[^{]*\{[^}]*font-size:\s*\d/)
  })
})
