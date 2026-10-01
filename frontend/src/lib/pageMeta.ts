import manifest from "../../../data/spa_routes.json"

// Per-route title, description and OpenGraph tags, from the route manifest. The
// server sends the same shell for every route, so this is what fixes the tab and
// what Google reads once it runs the bundle. Link-preview scrapers do not run it:
// that half is eco-app#8576. docs/frontend/page-meta.md.

export interface MetaSpec {
  path: string
  crawl?: string
  deepCrawl?: string
  title?: string
  description?: string
  image?: string
}

export interface PageMetaValues {
  title?: string
  description?: string
  image?: string
  canonical?: string | null
}

// The address the server names canonical for this URL, or null when it names
// none: a noindex route, a query string, or a path deeper than a wildcard route.
export function canonicalFor(spec: MetaSpec, pathname: string, search: string): string | null {
  const wildcard = spec.path.endsWith("/*")
  const bare = wildcard ? spec.path.slice(0, -2) || "/" : spec.path
  const deep = wildcard && pathname.replace(/\/$/, "") !== bare
  const posture = deep ? (spec.deepCrawl ?? "noindex") : (spec.crawl ?? "index")
  if (posture !== "index" || search) return null
  return `${manifest.site}${bare}`
}

interface ShellDefaults {
  title: string
  description: string | null
  ogTitle: string | null
  ogDescription: string | null
  ogImage: string | null
  ogImageAlt: string | null
}

let shell: ShellDefaults | null = null

const read = (selector: string) => document.head.querySelector(selector)?.getAttribute("content") ?? null

function put(selector: string, attribute: "name" | "property", key: string, value: string | null) {
  let tag = document.head.querySelector(selector)
  if (value === null) {
    tag?.remove()
    return
  }
  if (!tag) {
    tag = document.createElement("meta")
    tag.setAttribute(attribute, key)
    document.head.appendChild(tag)
  }
  tag.setAttribute("content", value)
}

// A route with no words of its own gets the shell's, captured once before the
// first route writes anything, so leaving a titled page never leaves its title behind.
export function applyPageMeta({ title, description, image, canonical }: PageMetaValues): void {
  shell ??= {
    title: document.title,
    description: read('meta[name="description"]'),
    ogTitle: read('meta[property="og:title"]'),
    ogDescription: read('meta[property="og:description"]'),
    ogImage: read('meta[property="og:image"]'),
    ogImageAlt: read('meta[property="og:image:alt"]'),
  }
  document.title = title ?? shell.title
  put('meta[name="description"]', "name", "description", description ?? shell.description)
  put('meta[property="og:title"]', "property", "og:title", title ?? shell.ogTitle)
  put('meta[property="og:description"]', "property", "og:description", description ?? shell.ogDescription)
  put('meta[property="og:image"]', "property", "og:image", image ? `${manifest.site}${image}` : shell.ogImage)
  // Like the server: a route's own card takes the route's title as its alt.
  put('meta[property="og:image:alt"]', "property", "og:image:alt", image ? (title ?? shell.ogImageAlt) : shell.ogImageAlt)
  put('meta[property="og:url"]', "property", "og:url", canonical ?? null)
}

// For tests, which build their own document head.
export function resetPageMeta(): void {
  shell = null
}
