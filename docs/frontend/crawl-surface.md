# The crawl surface

What a search engine is told about each URL on `eco-app.coilysiren.me`, and why
it is told that.

## What went wrong

Every path on the host answered `200` with the same React shell. `/robots.txt`
included — so Googlebot asked for crawl rules and received HTML, which is not a
rejection it can act on. It read no rules and applied none.

With no rules and no `404` anywhere, one shell became an unbounded index:

* **Soft 404s.** `/some/future/page`, `/wp-login.php`, anything anyone typed or
  probed, all `200`. A crawler cannot tell a real page from a typo when both
  return the same bytes with the same status.
* **Query-keyed duplicates.** `/item?name=`, `/recipe?id=`, `/uses/price?item=`
  turn a handful of pages into as many URLs as there are items, recipes, and
  currency pairs — each one indexed separately, each one the identical shell.
* **Retired paths served twice.** `/world`, `/economy`, `/trades` and six more
  rerendered client-side through `<Navigate>`. A visitor sees a redirect; a
  crawler sees `200` at the old address and `200` at the new one, and indexes
  both as competing originals.
* **Pages that were never meant to be found.** `/users/<hex>` is one player's
  dossier, unlinked and unbounded. `/social` and `/replay` sit behind a soft
  password gate. All crawlable. (`/social` and `/users/<hex>` were removed in
  eco-app#8385. `/social` redirects home, and `/users/<hex>` now 404s because it
  was about one person.)

None of that leaked anything: the shell is `index.html`, never the data. It
filled Search Console.

## The four postures

All of them derive from [`data/spa_routes.json`](../../data/spa_routes.json), read
by both consumers — `frontend/src/routes.tsx` builds the react-router table
from it, `src/eco_mcp_app/seo.py` builds the crawl policy. Two hand-maintained
lists is how a route added to the router becomes a page the policy does not
know about, so there is one list.

| Request | Answer |
| --- | --- |
| a path no route owns | `404` |
| a retired path | `301` to its replacement, query string carried across |
| a page that is real but not canonical | `200` + `X-Robots-Tag: noindex, follow` |
| a canonical page | `200` + `Link: <…>; rel="canonical"` |

"Real but not canonical" covers three cases: a route marked `noindex` in the
manifest (`/item`, `/recipe`, `/replay`), any URL
carrying a query string whatever its route, and anything under the `/jobs/*`
wildcard deeper than `/jobs` itself.

## Two rules that look wrong until they don't

**`Disallow` and `noindex` are never stacked on the same URL.** A disallowed
URL is never fetched again, so the crawler never reaches its `noindex` and the
URL sits in the index unresolved — the opposite of the intent. `robots.txt`
disallows only the JSON and MCP planes, which are not pages. Every page we want
dropped stays crawlable so the crawler can read the header telling it to drop
the page.

**A `noindex` response carries no canonical.** `rel=canonical` says index this
content under that address; `noindex` says index none of it. A crawler
resolving that contradiction its own way is how a deliberate `noindex` quietly
stops holding. `seo.classify` returns a canonical only for a page that is
actually indexable.

## Changing the route table

Add the route to `data/spa_routes.json` with a `component` and a `crawl`
posture. `frontend/src/routes.tsx` maps the component name to code and fails the
build if the name is unknown; the service picks up the crawl posture with no
further edit. A `gate: "password"` route must be `noindex` — a gated page in
search results is a title and a URL for something the visitor cannot open, and
`frontend/src/routes.test.tsx` asserts it.

## Verifying

`tests/mcp/test_seo.py` covers every posture above.
`frontend/src/routes.test.tsx` covers manifest-to-router parity. Against a
running server:

```sh
curl -s localhost:4000/robots.txt
curl -sI localhost:4000/item?name=Iron+Ore   # X-Robots-Tag: noindex, follow
curl -sI localhost:4000/world                # 301 -> /map
curl -sI localhost:4000/some/future/page     # 404
```

Recovery in Search Console is not instant. The `301`s and `404`s resolve as the
crawler revisits, and a `noindex` drops a URL on its next fetch — weeks for the
long tail, not hours. Submitting `/sitemap.xml` concentrates crawl budget on
the twenty canonical pages in the meantime.
