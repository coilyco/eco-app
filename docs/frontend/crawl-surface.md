# The crawl surface

What a search engine is told about each URL on `eco-app.coilysiren.me`.

Every path used to answer `200` with the same React shell, `/robots.txt` included. That gave soft 404s, query-keyed duplicates (`/item?name=`), retired paths indexed beside their replacements, and unlinked pages crawlable. Nothing leaked, since the shell is `index.html`, never data.

## The four postures

All derive from [`data/spa_routes.json`](../../data/spa_routes.json), read by `frontend/src/routes.tsx` (router table) and `src/eco_mcp_app/seo.py` (crawl policy). One list, so a new route cannot miss the policy.

* **A path no route owns** - `404`.
* **A retired path** - `301` to its replacement, query string carried across.
* **Real but not canonical** - `200` + `X-Robots-Tag: noindex, follow`. Covers a route marked `noindex` (`/item`, `/recipe`, `/replay`), any URL with a query string, and anything under `/jobs/*` deeper than `/jobs`.
* **A canonical page** - `200` + `Link: <…>; rel="canonical"`.

## Two rules that look wrong until they don't

**`Disallow` and `noindex` are never stacked on one URL.** A disallowed URL is never refetched, so its `noindex` is never read. `robots.txt` disallows only the JSON and MCP planes. Pages to drop stay crawlable.

**A `noindex` response carries no canonical.** The two contradict, and a crawler resolving it its own way quietly stops honouring the `noindex`. `seo.classify` returns a canonical only for an indexable page.

## Link previews

A preview scraper runs no script, so `http_app._shell` rewrites `<head>` per request through `src/eco_mcp_app/shell_head.py` from each route's optional `title`, `description` and `image`: `<title>`, `description`, `og:title`, `og:description`, `og:url`, `og:image`, `og:image:alt`. The `<body>` is never touched. `og:url` is the bare path's canonical, and a `noindex` route carries none. `image` is site-relative (`/og/items.png`), made absolute against `site`. A route without words keeps the shell defaults, and `/jobs/*` takes the parent's. The `ETag` follows the rewritten head.

## Changing the route table

Add the route to `data/spa_routes.json` with a `component` and a `crawl` posture, optionally `title`, `description`, `image`. `routes.tsx` fails the build on an unknown component name. A `gate: "password"` route must be `noindex` (`frontend/src/routes.test.tsx` asserts it).

## Verifying

`tests/mcp/test_seo.py` covers the postures, `tests/mcp/test_shell_head.py` the head, `routes.test.tsx` manifest-to-router parity. Against a running server:

```sh
curl -sI localhost:4000/item?name=Iron+Ore   # X-Robots-Tag: noindex, follow
curl -sI localhost:4000/world                # 301 -> /map
curl -sI localhost:4000/some/future/page     # 404
```

Search Console recovery takes weeks for the long tail, as crawlers revisit.
