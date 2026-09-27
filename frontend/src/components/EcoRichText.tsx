import type { ReactNode } from "react"
import { stripEcoMarkupInline } from "../lib/format"

const COLOR_TAG = /<color\s*=\s*([^>]+)>|<\/color\s*>/gi
const HEX_COLOR = /^#[0-9a-f]{3,8}$/i
const NAMED_COLORS = new Set([
  "aqua",
  "black",
  "blue",
  "brown",
  "cyan",
  "darkblue",
  "fuchsia",
  "green",
  "grey",
  "lightblue",
  "lime",
  "magenta",
  "maroon",
  "navy",
  "olive",
  "orange",
  "purple",
  "red",
  "silver",
  "teal",
  "white",
  "yellow",
])

// Unity's named colours are tuned for the game's own panels, and the dark ones
// (blue, navy, maroon) vanish on this site's ground. The server name itself is
// <color=green>Eco</color> via <color=blue>Sirens</color>, so those map onto the
// theme instead. Player-picked hex values stay exactly as written.
const THEMED_COLORS: Record<string, string> = {
  green: "var(--k-brand)",
  blue: "var(--k-accent)",
  darkblue: "var(--k-accent)",
  navy: "var(--k-accent)",
  red: "var(--k-cost)",
  maroon: "var(--k-cost)",
}

interface Frame {
  color?: string
  children: ReactNode[]
}

function safeColor(raw: string): string | undefined {
  const color = raw.trim().replace(/^['"]|['"]$/g, "").toLowerCase()
  if (HEX_COLOR.test(color) && [4, 5, 7, 9].includes(color.length)) return color
  if (!NAMED_COLORS.has(color)) return undefined
  return THEMED_COLORS[color] ?? color
}

function appendText(frame: Frame, text: string) {
  const clean = stripEcoMarkupInline(text)
  if (clean) frame.children.push(clean)
}

function closeFrame(stack: Frame[], key: number) {
  if (stack.length === 1) return
  const frame = stack.pop()!
  const node = frame.color ? (
    <span key={key} style={{ color: frame.color }}>
      {frame.children}
    </span>
  ) : (
    <span key={key}>{frame.children}</span>
  )
  stack.at(-1)!.children.push(node)
}

/** Render Eco/Unity color markup without injecting HTML or accepting CSS. */
export default function EcoRichText({ text }: { text: string }) {
  const stack: Frame[] = [{ children: [] }]
  let cursor = 0
  let key = 0

  for (const match of text.matchAll(COLOR_TAG)) {
    const index = match.index ?? cursor
    appendText(stack.at(-1)!, text.slice(cursor, index))
    if (match[1] !== undefined) {
      stack.push({ color: safeColor(match[1]), children: [] })
    } else {
      closeFrame(stack, key++)
    }
    cursor = index + match[0].length
  }
  appendText(stack.at(-1)!, text.slice(cursor))
  while (stack.length > 1) closeFrame(stack, key++)

  return <>{stack[0].children}</>
}
