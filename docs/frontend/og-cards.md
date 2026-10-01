# Link-preview cards

Every route with words gets a 1200x630 card, drawn at build time from the route manifest. A pasted link shows the card instead of a bare URL.

## How a card is made

* **Words** - `title` and `description` from `data/spa_routes.json`, the same words the tab and the server head use. The card drops the `| Eco via Sirens` suffix from the title and shows the site name above it.
* **Output** - the route's `image` field, such as `/og/items.png`. Cards land in `frontend/public/og/`, are gitignored, and are drawn by `pnpm build`, so the image build carries them to `dist/og/` with no extra step.
* **Art** - by default a planet, contour rings and stars, seeded from the route path. The same path draws the same art on every build. A route that names `art` in the manifest (a file under `frontend/src/assets/og-art/`) uses that image instead, under a left-to-right scrim that keeps the words readable. A missing art file fails the build.
* **Engine** - `satori` lays the card out as SVG and `@resvg/resvg-js` rasterises it. No browser. Fonts are the `@fontsource` Chakra Petch and Roboto files in `node_modules`, since satori reads TTF, OTF and WOFF but not WOFF2.

## Run it

* `just frontend-og` (or `pnpm --dir frontend og`) draws every card.
* `just frontend-og --only /items,default` draws some. A value matches a route path, `default`, or an image path.
* `frontend/scripts/og-cards.test.mjs` checks that every route with a title has an image, that file names are unique, that named art exists, and that a very long title still renders at 1200x630.

## Add a page

Give the route a `title`, a `description` and `"image": "/og/<slug>.png"` in the manifest. The test fails if a titled route has no image. Add `"art": "<file>"` only for a page that has a real picture worth showing.

## Where the tags come from

* **Shell** - `frontend/index.html` carries the default card as `og:image`, with width, height and `twitter:card`. Every shared link gets at least this one.
* **Client** - `applyPageMeta` swaps `og:image` to the route's card on navigation. This serves tabs and Google, which run the bundle.
* **Scrapers** - Slack, Discord and the rest do not run it, so a per-route `og:image` in the served HTML is the server's job, in `shell_head.py` (eco-app#8576).

Pairs with [page-meta](page-meta.md), which owns the words.
