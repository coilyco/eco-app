// Draws the link-preview card for each route, 1200x630, into public/og/.
//
//   pnpm --dir frontend og [--only /items,/map]
//
// The words come from data/spa_routes.json (title, description) and the output
// path from each route's `image`, so the manifest stays the one source. satori
// lays the card out as SVG and resvg rasterises it, with no browser. The art is
// seeded from the route's path, so a card differs per page and never changes
// between builds. docs/frontend/og-cards.md.
import { mkdirSync, readFileSync, writeFileSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"
import { Resvg } from "@resvg/resvg-js"
import satori from "satori"
import UPNG from "upng-js"

const HERE = path.dirname(fileURLToPath(import.meta.url))
const FRONTEND = path.join(HERE, "..")
const ROOT = path.join(FRONTEND, "..")
const manifest = JSON.parse(readFileSync(path.join(ROOT, "data", "spa_routes.json"), "utf8"))

export const WIDTH = 1200
export const HEIGHT = 630
export const DEFAULT_IMAGE = "/og/default.png"

// The eco theme's own primitives (src/eco-theme.css), as hex because satori
// takes no custom properties.
const INK = { deep: "#040b04", mid: "#10230e", lift: "#1d331b" }
const LEAF = "#60ac59"
const LEAF_LIGHT = "#acd8a8"
const OCEAN = "#45aade"
const TEXT = "#f1f5f0"
const DIM = "#a3aca2"

const font = (pkg, file) => readFileSync(path.join(FRONTEND, "node_modules", "@fontsource", pkg, "files", file))
const FONTS = [
  { name: "Chakra Petch", data: font("chakra-petch", "chakra-petch-latin-700-normal.woff"), weight: 700, style: "normal" },
  { name: "Roboto", data: font("roboto", "roboto-latin-400-normal.woff"), weight: 400, style: "normal" },
  { name: "Roboto", data: font("roboto", "roboto-latin-700-normal.woff"), weight: 700, style: "normal" },
]
const MARK = `data:image/png;base64,${readFileSync(path.join(FRONTEND, "src", "assets", "eco-icon.png")).toString("base64")}`

// FNV-1a into mulberry32: the same path always draws the same art.
export function seeded(text) {
  let h = 2166136261
  for (const ch of text) h = Math.imul(h ^ ch.codePointAt(0), 16777619)
  let a = h >>> 0
  return () => {
    a = (a + 0x6d2b79f5) >>> 0
    let t = a
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

// A planet, contour rings around it, and a scatter of stars, all from the seed.
function art(seed) {
  const rand = seeded(seed)
  const cx = 720 + rand() * 360
  const cy = 60 + rand() * 360
  const radius = 230 + rand() * 120
  const rings = 5 + Math.floor(rand() * 4)
  const shapes = [{ type: "circle", props: { cx, cy, r: radius, fill: LEAF, opacity: 0.1 } }]
  for (let i = 1; i <= rings; i++) {
    shapes.push({
      type: "circle",
      props: { cx, cy, r: radius + i * (34 + rand() * 18), fill: "none", stroke: i % 2 ? LEAF : OCEAN, "stroke-width": 2, opacity: 0.22 - i * 0.02 },
    })
  }
  for (let i = 0; i < 46; i++) {
    shapes.push({
      type: "circle",
      props: { cx: rand() * WIDTH, cy: rand() * HEIGHT, r: 1.5 + rand() * 3.5, fill: rand() > 0.5 ? LEAF_LIGHT : OCEAN, opacity: 0.18 + rand() * 0.4 },
    })
  }
  return { type: "svg", props: { width: WIDTH, height: HEIGHT, viewBox: `0 0 ${WIDTH} ${HEIGHT}`, children: shapes } }
}

// satori wants an explicit display on any element with more than one child, so
// flex is the default and a style that wants otherwise says so.
const h = (type, style, ...children) => ({
  type,
  props: { style: { display: "flex", ...style }, children: children.length === 1 ? children[0] : children.filter(Boolean) },
})

// The card shows the page's own part of a title, not the "| Eco via Sirens" suffix.
const cardTitle = (title) => title.split(" | ")[0]
const titleSize = (text) => (text.length > 46 ? 54 : text.length > 34 ? 64 : 76)

export function card({ title, description, url, seed, photo }) {
  const text = cardTitle(title)
  return h(
    "div",
    { width: WIDTH, height: HEIGHT, display: "flex", position: "relative", background: `linear-gradient(135deg, ${INK.deep} 0%, ${INK.mid} 58%, ${INK.lift} 100%)`, fontFamily: "Roboto" },
    // A real image, when a route names one, sits under a scrim that keeps the text
    // legible. Without one the card draws its own procedural art.
    ...(photo
      ? [
          { type: "img", props: { src: photo, width: WIDTH, height: HEIGHT, style: { position: "absolute", top: 0, left: 0 } } },
          h("div", { position: "absolute", top: 0, left: 0, width: WIDTH, height: HEIGHT, background: `linear-gradient(90deg, ${INK.deep}eb 0%, ${INK.deep}8c 52%, ${INK.deep}14 100%)` }),
        ]
      : [h("div", { position: "absolute", top: 0, left: 0 }, art(seed))]),
    h(
      "div",
      { display: "flex", flexDirection: "column", justifyContent: "space-between", width: WIDTH, height: HEIGHT, padding: "64px 76px" },
      h(
        "div",
        { display: "flex", alignItems: "center" },
        { type: "img", props: { src: MARK, width: 60, height: 60, style: { borderRadius: 30 } } },
        h("div", { marginLeft: 20, fontSize: 24, fontWeight: 700, letterSpacing: "0.16em", color: OCEAN }, "ECO VIA SIRENS"),
      ),
      h(
        "div",
        { display: "flex", flexDirection: "column", maxWidth: 940 },
        h("div", { fontFamily: "Chakra Petch", fontWeight: 700, fontSize: titleSize(text), lineHeight: 1.02, letterSpacing: "-0.02em", color: TEXT, lineClamp: 3 }, text),
        description ? h("div", { marginTop: 28, fontSize: 32, lineHeight: 1.3, color: DIM, lineClamp: 3 }, description) : null,
      ),
      h(
        "div",
        { display: "flex", alignItems: "center" },
        h("div", { width: 56, height: 4, background: LEAF, marginRight: 20 }),
        h("div", { fontSize: 26, color: LEAF_LIGHT }, url),
      ),
    ),
  )
}

// resvg writes only lossless RGBA PNG, and a photographic backdrop makes that
// about 670K. A 256-colour palette is about a quarter of it and reads the same at
// 1200x630 under the scrim. Cards without a photo are already small and keep the
// lossless PNG. The path stays .png, which the shell and the server name
// (eco-app#8586).
const PALETTE_COLOURS = 256
function palette(png) {
  const frames = UPNG.toRGBA8(UPNG.decode(png))
  return Buffer.from(UPNG.encode(frames, WIDTH, HEIGHT, PALETTE_COLOURS))
}

export async function render(spec) {
  const svg = await satori(card(spec), { width: WIDTH, height: HEIGHT, fonts: FONTS })
  const png = new Resvg(svg, { fitTo: { mode: "width", value: WIDTH } }).render().asPng()
  return spec.photo ? palette(png) : png
}

// The shell's own description, so the default card never disagrees with it.
function shellWords() {
  const html = readFileSync(path.join(FRONTEND, "index.html"), "utf8")
  return html.match(/name="description"\s+content="([^"]*)"/)?.[1] ?? ""
}

const photo = (file) => {
  const full = path.join(FRONTEND, "src", "assets", "og-art", file)
  const type = file.endsWith(".png") ? "image/png" : "image/jpeg"
  return `data:${type};base64,${readFileSync(full).toString("base64")}` // throws if the file is missing
}

export function jobs() {
  const out = [
    { image: DEFAULT_IMAGE, title: "Eco via Sirens", description: shellWords(), url: manifest.site.replace(/^https?:\/\//, ""), seed: "default" },
  ]
  for (const route of manifest.routes) {
    if (!route.image || !route.title) continue
    const bare = route.path.replace(/\/\*$/, "") || "/"
    out.push({ image: route.image, title: route.title, description: route.description, url: manifest.site.replace(/^https?:\/\//, "") + (bare === "/" ? "" : bare), seed: route.path, photo: route.art ? photo(route.art) : undefined })
  }
  return out
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const only = process.argv.includes("--only") ? process.argv[process.argv.indexOf("--only") + 1].split(",") : null
  const wanted = jobs().filter((job) => !only || only.includes(job.seed) || only.includes(job.image))
  for (const job of wanted) {
    const file = path.join(FRONTEND, "public", job.image)
    mkdirSync(path.dirname(file), { recursive: true })
    writeFileSync(file, await render(job))
    console.log(`og  ${job.image}`)
  }
  console.log(`\n${wanted.length} cards in frontend/public/og/`)
}
