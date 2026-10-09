// The coilyco kit's browser checks, run against a live eco-app: axe with the
// kit's tag set (colour contrast included), the layering rule, the 16px floor
// as rendered pixels (SVG chart labels included), horizontal overflow, and
// transfer weight. Ports of website cypress/e2e/{accessibility,layering,
// weight}.cy.ts. Walkthrough: docs/frontend/kit-checks.md.
//
//   pnpm --dir frontend kit-check [--base URL] [--only /path,/path] [--json]
//
// Needs a Chromium-family browser on the machine (playwright-core downloads
// none): it uses the installed Chrome, or CHROME_PATH.

import { readFileSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"
import { chromium } from "playwright-core"

const HERE = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.join(HERE, "..", "..")
const args = process.argv.slice(2)
const flag = (name, fallback) => {
  const i = args.indexOf(name)
  return i === -1 ? fallback : args[i + 1]
}
const BASE = flag("--base", "https://eco-app.coilysiren.me").replace(/\/$/, "")
const ONLY = flag("--only", "")
const JSON_OUT = args.includes("--json")

const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "best-practice"]
const VIEWPORTS = [
  [1000, 660],
  [320, 900],
]
const FLOOR_PX = 16

// Transfer budgets in KiB, measured on 2026-09-27 and set just above what each
// route moved then, so a regression fails while today's pages pass. eco-app is
// a data app: a route's weight is mostly its API payloads, not the shell.
const DEFAULT_BUDGET = 900
// /cycle-14/castle moves 11974K: 8386K splat, 2388K of its engine, 437K shell, 435K posters, 177K still.
// Measured live on 2026-10-09, kit-check at 1000px: castle 11990K, biodrive 15374K, terrace-farms 12020K.
// The splat host sends no Timing-Allow-Origin, so weight is summed on the wire, not by Resource Timing.
const BUDGETS = {
  "/jobs": 3200,
  "/recipes": 2000,
  "/recipe": 2000,
  "/uses/resolve": 3200,
  "/map": 2400,
  "/cycle-14/castle": 12300,
  "/cycle-14/biodrive": 15700,
  "/cycle-14/terrace-farms": 12400,
}

const SAMPLE = {
  "/jobs/*": "/jobs",
  "/item": "/item?id=IronBar",
  "/recipe": "/recipe?id=IronBar",
}
const manifest = JSON.parse(readFileSync(path.join(ROOT, "data", "spa_routes.json"), "utf8"))
let routes = manifest.routes.filter((r) => r.gate !== "password").map((r) => SAMPLE[r.path] ?? r.path)
if (ONLY) routes = routes.filter((r) => ONLY.split(",").includes(r.split("?")[0]))
const AXE = readFileSync(path.join(HERE, "..", "node_modules", "axe-core", "axe.min.js"), "utf8")

// Runs in the page. Returns everything but axe, which runs separately.
function inspect(floor) {
  const main = document.querySelector("main") || document.body
  const name = (el) =>
    el.tagName.toLowerCase() +
    (typeof el.className === "string" && el.className ? "." + el.className.trim().split(/\s+/).slice(0, 2).join(".") : "")
  const opaque = (c) => Boolean(c) && c !== "rgba(0, 0, 0, 0)" && c !== "transparent"
  const effective = (el) => {
    for (let n = el; n; n = n.parentElement) {
      const bg = getComputedStyle(n).backgroundColor
      if (opaque(bg)) return bg
    }
    return "rgb(0, 0, 0)"
  }

  // Rendered text below the floor, with SVG text scaled by its screen matrix.
  const small = new Map()
  for (const el of main.querySelectorAll("*")) {
    if (![...el.childNodes].some((c) => c.nodeType === 3 && c.textContent.trim())) continue
    const r = el.getBoundingClientRect()
    if (!r.width || !r.height) continue
    let px = parseFloat(getComputedStyle(el).fontSize)
    if (el.ownerSVGElement) {
      const m = el.ownerSVGElement.getScreenCTM()
      if (m) px *= Math.hypot(m.a, m.b)
    }
    if (px < floor - 0.1) {
      const k = name(el)
      small.set(k, Math.min(small.get(k) ?? 99, Math.round(px * 10) / 10))
    }
  }

  // Layering: a bordered element sharing its parent's fill (the kit's rule 4).
  const layering = []
  for (const el of main.querySelectorAll("*")) {
    const cs = getComputedStyle(el)
    const width = parseFloat(cs.borderTopWidth) || parseFloat(cs.borderLeftWidth) || 0
    if (width === 0 || cs.borderTopStyle === "none" || !el.parentElement) continue
    if (!opaque(cs.backgroundColor)) continue
    if (effective(el) !== effective(el.parentElement)) continue
    layering.push(`${name(el)} inside ${name(el.parentElement)}`)
  }

  return {
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    small: [...small].map(([k, px]) => `${k} ${px}px`),
    layering: [...new Set(layering)].slice(0, 8),
  }
}

// Ready is a loaded document with the app drawn into #root. Network quiet is
// awaited for SETTLE_MS only, and an open request is a note (eco-app#8680).
const SETTLE_MS = 15000
async function open(page, url, errors) {
  const inFlight = new Set()
  const sizes = []
  page.on("request", (r) => inFlight.add(r))
  page.on("requestfinished", (r) => {
    inFlight.delete(r)
    sizes.push(r.sizes().then((s) => s.responseBodySize + s.responseHeadersSize, () => 0))
  })
  page.on("requestfailed", (r) => inFlight.delete(r))
  try {
    await page.goto(url, { waitUntil: "load", timeout: 60000 })
    await page.waitForFunction(() => (document.querySelector("#root")?.childElementCount ?? 0) > 0, null, { timeout: 15000 })
  } catch (e) {
    errors.push(String(e).slice(0, 160))
  }
  await page.waitForLoadState("networkidle", { timeout: SETTLE_MS }).catch(() => {})
  const slow = [...inFlight].map((r) => new URL(r.url()).pathname + new URL(r.url()).search)
  return { slow, kib: async () => Math.round((await Promise.all(sizes)).reduce((a, b) => a + b, 0) / 1024) }
}

const browser = await chromium.launch(
  process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : { channel: "chrome" },
)
const results = []
for (const [width, height] of VIEWPORTS) {
  const context = await browser.newContext({ viewport: { width, height } })
  for (const route of routes) {
    const page = await context.newPage()
    const errors = []
    page.on("pageerror", (e) => errors.push(String(e).slice(0, 160)))
    const { slow, kib } = await open(page, BASE + route, errors)
    await page.waitForTimeout(800)
    const found = { ...(await page.evaluate(inspect, FLOOR_PX)), kib: await kib() }
    await page.evaluate(AXE)
    const axe = await page.evaluate(async (tags) => {
      const r = await window.axe.run(document, { runOnly: tags })
      return r.violations.map((v) => `${v.impact} ${v.id} x${v.nodes.length}: ${v.nodes.slice(0, 2).map((n) => n.target.join(" ")).join(" | ")}`)
    }, TAGS)
    const bare = route.split("?")[0]
    const budget = BUDGETS[bare] ?? DEFAULT_BUDGET
    const problems = [
      ...errors.map((e) => `page error: ${e}`),
      ...axe.map((a) => `axe ${a}`),
      ...found.small.map((s) => `below ${FLOOR_PX}px: ${s}`),
      ...found.layering.map((l) => `layering: ${l}`),
      ...(found.overflow > 0 ? [`scrolls sideways by ${found.overflow}px`] : []),
      ...(width === VIEWPORTS[0][0] && found.kib > budget ? [`transferred ${found.kib}K against a ${budget}K budget`] : []),
    ]
    results.push({ route, width, kib: found.kib, problems, slow })
    if (!JSON_OUT) console.log(`${problems.length ? "FAIL" : "ok  "} ${String(width).padStart(4)} ${route}${width === VIEWPORTS[0][0] ? ` (${found.kib}K)` : ""}`)
    for (const p of problems) if (!JSON_OUT) console.log(`       ${p}`)
    if (slow.length && !JSON_OUT) console.log(`       note: still loading after ${SETTLE_MS / 1000}s, not counted: ${slow.slice(0, 4).join(", ")}`)
    await page.close()
  }
  await context.close()
}
await browser.close()

const failed = results.filter((r) => r.problems.length)
if (JSON_OUT) console.log(JSON.stringify(results, null, 2))
else console.log(`\n${results.length - failed.length} of ${results.length} route-viewports clean against ${BASE}`)
process.exit(failed.length ? 1 : 0)
