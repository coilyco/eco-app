"""The crawl surface: robots.txt, sitemap.xml, and the per-response index rule.

Every URL on this host answered 200 with the same SPA shell — `/robots.txt`
included, so Googlebot fetched crawl rules and got HTML. With no rules and no
404 anywhere, one shell became an unbounded index of soft 404s, retired paths,
and query-keyed detail pages, which is what filled Search Console.

The fix is four postures, all derived from `data/spa_routes.json` so the router
and the crawl policy cannot drift apart:

* a path no route owns is a **404**, not the shell
* a retired path is a **301** to its replacement, not a client-side rerender
* a page that is real but not canonical (a dossier, a gated page, anything
  carrying a query string) is served with **X-Robots-Tag: noindex, follow**
* every shell response carries a **canonical Link header** at its bare path

`noindex` and `Disallow` are deliberately not stacked on the same URL: a
disallowed URL is never fetched, so the crawler never sees the noindex and the
URL lingers in the index unresolved. `Disallow` covers only the JSON and MCP
planes, which are not pages at all.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from xml.sax.saxutils import escape

# Repo-root-relative, matching how `frontend/dist` is resolved: the image
# WORKDIR and a local `just http` both run from the root. ECO_SPA_ROUTES
# overrides for anything else.
_DEFAULT_MANIFEST = Path("data/spa_routes.json")

NOINDEX_HEADER = "noindex, follow"


@dataclass(frozen=True)
class Verdict:
    """What to do with one requested path."""

    known: bool
    indexable: bool
    canonical: str | None = None


@dataclass(frozen=True)
class Page:
    """Optional link-preview words and card for one route; each may be absent."""

    title: str | None = None
    description: str | None = None
    image: str | None = None


@dataclass(frozen=True)
class Head:
    """What the shell's `<head>` says for one URL. None keeps the shell default,
    except `url`, where None means the tag is dropped."""

    title: str | None
    description: str | None
    url: str | None
    image: str | None = None
    """Absolute URL of the route's own card, or None for the shell's default."""


@dataclass(frozen=True)
class RouteTable:
    site: str
    exact: dict[str, str]
    """Bare path -> crawl posture (``index`` / ``noindex``)."""
    prefixes: tuple[tuple[str, str, str], ...]
    """(bare path, own posture, deeper-path posture) for ``/jobs/*``-shaped routes."""
    params: tuple[tuple[str, str], ...]
    """(parent prefix, posture) for one-segment routes like ``/users/:hex``."""
    redirects: dict[str, str]
    disallow: tuple[str, ...]
    pages: dict[tuple[str, str], Page]
    """(kind, key) of the matched route -> its words. See `_match`."""

    @property
    def sitemap_paths(self) -> tuple[str, ...]:
        indexable = [p for p, crawl in self.exact.items() if crawl == "index"]
        indexable += [p for p, crawl, _ in self.prefixes if crawl == "index"]
        return tuple(sorted(indexable))


def _manifest_path() -> Path:
    return Path(os.getenv("ECO_SPA_ROUTES", str(_DEFAULT_MANIFEST)))


@lru_cache(maxsize=4)
def _load(path: str) -> RouteTable:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    exact: dict[str, str] = {}
    prefixes: list[tuple[str, str, str]] = []
    params: list[tuple[str, str]] = []
    pages: dict[tuple[str, str], Page] = {}
    for route in raw["routes"]:
        spec, crawl = route["path"], route.get("crawl", "index")
        if spec.endswith("/*"):
            bare = spec[: -len("/*")] or "/"
            prefixes.append((bare, crawl, route.get("deepCrawl", crawl)))
            key = ("prefix", bare)
        elif ":" in spec:
            parent = spec.rsplit("/", 1)[0]
            params.append((parent, crawl))
            key = ("param", parent)
        else:
            exact[spec] = crawl
            key = ("exact", spec)
        page = Page(
            _words(route, "title", spec),
            _words(route, "description", spec),
            _image(route, spec),
        )
        if page.title or page.description or page.image:
            pages[key] = page
    return RouteTable(
        site=raw["site"].rstrip("/"),
        exact=exact,
        prefixes=tuple(prefixes),
        params=tuple(params),
        redirects={r["from"]: r["to"] for r in raw["redirects"]},
        disallow=tuple(raw["disallow"]),
        pages=pages,
    )


def _words(route: dict[str, object], field: str, spec: str) -> str | None:
    """A route's optional `title` or `description`, which must be a nonblank string."""
    value = route.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{_DEFAULT_MANIFEST}: route {spec!r} {field} must be a nonblank string")
    return value.strip()


def _image(route: dict[str, object], spec: str) -> str | None:
    """A route's optional `image`, a site-relative path like `/og/items.png`."""
    value = route.get("image")
    if value is None:
        return None
    if not isinstance(value, str) or not value.startswith("/") or value.startswith("//"):
        raise ValueError(
            f"{_DEFAULT_MANIFEST}: route {spec!r} image must be a path starting with one /"
        )
    return value


def routes() -> RouteTable:
    return _load(str(_manifest_path()))


def _normalize(path: str) -> str:
    """`/trade/` and `trade` both mean `/trade`; `/` stays `/`."""
    stripped = "/" + path.strip("/")
    return stripped if stripped != "/" else "/"


def redirect_target(path: str) -> str | None:
    """The replacement for a retired path, or None if the path is not retired."""
    return routes().redirects.get(_normalize(path))


def _match(table: RouteTable, bare: str) -> tuple[tuple[str, str], str] | None:
    """The route owning a normalized path: its (kind, key) and its crawl posture.

    One matcher for the crawl policy and the link-preview words, so the two
    cannot disagree about which route a path belongs to.
    """
    crawl = table.exact.get(bare)
    if crawl is not None:
        return ("exact", bare), crawl
    for prefix, own, deep in table.prefixes:
        if bare == prefix:
            return ("prefix", prefix), own
        if bare.startswith(prefix + "/"):
            return ("prefix", prefix), deep
    for parent, posture in table.params:
        rest = bare[len(parent) + 1 :] if bare.startswith(parent + "/") else ""
        if rest and "/" not in rest:
            return ("param", parent), posture
    return None


def classify(path: str, query: str = "") -> Verdict:
    """Decide whether a path is a real page and whether it belongs in the index.

    A query string always costs indexability, whatever the route: `?name=`,
    `?id=`, and `?item=` multiply a handful of pages into an unbounded set of
    URLs that render one shell each.

    A canonical is returned only for a page that is actually indexable.
    Pairing `rel=canonical` with `noindex` is contradictory — one says index
    this URL's content under that address, the other says index none of it —
    and a crawler resolving the contradiction its own way is how a deliberate
    noindex turns back into an indexed URL.
    """
    table = routes()
    bare = _normalize(path)
    matched = _match(table, bare)
    if matched is None:
        return Verdict(known=False, indexable=False)
    _, crawl = matched
    indexable = crawl == "index" and not query
    canonical = f"{table.site}{bare}" if indexable else None
    return Verdict(known=True, indexable=indexable, canonical=canonical)


def head_for(path: str) -> Head | None:
    """What the shell's `<head>` should say for a URL, or None for an unknown path.

    The words come from the matched route, and a wildcard route's deeper paths
    take the parent's. `url` is the canonical of the bare path, so a query string
    never reaches it and a noindex route has none: it names no page that is not
    itself canonical. Link-preview scrapers read this and run no script.
    """
    table = routes()
    bare = _normalize(path)
    matched = _match(table, bare)
    if matched is None:
        return None
    page = table.pages.get(matched[0], Page())
    image = f"{table.site}{page.image}" if page.image else None
    return Head(page.title, page.description, classify(bare).canonical, image)


def robots_txt() -> str:
    """Crawl rules for every agent, plus the sitemap pointer.

    Only non-page surfaces are disallowed. The pages we want dropped from the
    index are left crawlable on purpose so the crawler can reach their noindex
    header and act on it.
    """
    table = routes()
    lines = ["User-agent: *"]
    lines += [f"Disallow: {path}" for path in table.disallow]
    lines += ["Allow: /", "", f"Sitemap: {table.site}/sitemap.xml", ""]
    return "\n".join(lines)


def sitemap_xml() -> str:
    """Every canonical page, and nothing else.

    No `lastmod`: the pages are live views over a running world, so every one
    of them changed since the crawler last looked, and saying so on all of them
    carries no signal.
    """
    table = routes()
    urls = "\n".join(
        f"  <url><loc>{escape(table.site + path)}</loc></url>" for path in table.sitemap_paths
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{urls}\n"
        "</urlset>\n"
    )
