// Player-picked Eco colours (store names, titles) render on the site's dark
// green ground, where plenty of them fall under WCAG's 4.5:1. Keep the hue the
// player chose and lift it toward white only as far as legibility needs.

// The lightest surface Eco markup renders on: --k-raised, the eco theme's
// p-750 (frontend/src/eco-theme.css). Read live when a stylesheet is loaded,
// with the theme's value as the fallback for tests and early renders.
const RAISED_FALLBACK = "#1d331b"
const TARGET = 4.5

function parseHex(hex: string): [number, number, number] | null {
  const h = hex.replace("#", "")
  if (h.length === 3 || h.length === 4) {
    return [0, 1, 2].map((i) => parseInt(h[i] + h[i], 16)) as [number, number, number]
  }
  if (h.length === 6 || h.length === 8) {
    return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)) as [number, number, number]
  }
  return null
}

const lin = (c: number) => {
  const v = c / 255
  return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4
}
const luminance = ([r, g, b]: [number, number, number]) =>
  0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)

export function contrast(a: [number, number, number], b: [number, number, number]): number {
  const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p)
  return (x + 0.05) / (y + 0.05)
}

function raisedSurface(): [number, number, number] {
  if (typeof document !== "undefined") {
    const live = getComputedStyle(document.documentElement).getPropertyValue("--k-raised").trim()
    const parsed = /^#[0-9a-f]{6}$/i.test(live) ? parseHex(live) : null
    if (parsed) return parsed
  }
  return parseHex(RAISED_FALLBACK)!
}

const toHex = (rgb: number[]) =>
  "#" + rgb.map((c) => Math.round(c).toString(16).padStart(2, "0")).join("")

const cache = new Map<string, string>()

/** The player's colour if it reads at 4.5:1 on the raised surface, else the
 * nearest lighter tint of it that does. Non-hex input passes through. */
export function legibleColor(hex: string): string {
  const cached = cache.get(hex)
  if (cached) return cached
  const rgb = parseHex(hex)
  if (!rgb) return hex
  const bg = raisedSurface()
  let out = hex
  if (contrast(rgb, bg) < TARGET) {
    for (let t = 0.05; t <= 1; t += 0.05) {
      const mixed = rgb.map((c) => c + (255 - c) * t) as [number, number, number]
      if (contrast(mixed, bg) >= TARGET) {
        out = toHex(mixed)
        break
      }
    }
  }
  cache.set(hex, out)
  return out
}
