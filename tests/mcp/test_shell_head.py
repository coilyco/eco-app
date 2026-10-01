"""Link previews: what a scraper that runs no script reads from the shell's `<head>`.

Before this every route returned the same head: `og:title` "eco-app" and an
`og:url` naming the home page, which contradicts the canonical Link header the
same response carries. The body stays identical so the duplicate-content
protection in docs/frontend/crawl-surface.md is unchanged.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from eco_mcp_app import seo, shell_head
from eco_mcp_app.http_app import create_app

SITE = "https://eco-app.coilysiren.me"
SHELL = Path("frontend/index.html")
MANIFEST = Path("data/spa_routes.json")

WORDS = {
    "/items": ("Items", "Every item on the server."),
    "/map": ("Map", "The live world map."),
    "/cycle-14/castle": ("Castle", "The cycle 14 castle draft."),
    "/item": ("Item", "One item's market, recipes and uses."),
    "/jobs/*": ("Jobs", "Open jobs on the server."),
}
# Which of those routes draw their own card. `/item` has words and none, so it keeps the default.
IMAGES = {
    "/items": "/og/items.png",
    "/map": "/og/map.png",
    "/cycle-14/castle": "/og/cycle-14-castle.png",
    "/jobs/*": "/og/jobs.png",
}
DEFAULT_CARD = f"{SITE}/og-default.png"
PNG = b"\x89PNG\r\n\x1a\n"


@pytest.fixture(autouse=True)
def _fresh_manifest() -> None:
    seo._load.cache_clear()


@pytest.fixture
def served(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """The real shell head over a manifest carrying link-preview words."""
    dist = tmp_path / "dist"
    (dist / "og").mkdir(parents=True)
    # The fixture owns its default card, so "keeps the default" never rests on the real shell's.
    shell = shell_head._set_meta(
        SHELL.read_text(encoding="utf-8"), "property", "og:image", DEFAULT_CARD
    )
    (dist / "index.html").write_text(shell, encoding="utf-8")
    for image in IMAGES.values():
        (dist / image.lstrip("/")).write_bytes(PNG)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for route in manifest["routes"]:
        # Hermetic: only WORDS carry words, so the real manifest's strings never decide a test.
        route.pop("title", None)
        route.pop("description", None)
        route.pop("image", None)
        if route["path"] in WORDS:
            route["title"], route["description"] = WORDS[route["path"]]
        if route["path"] in IMAGES:
            route["image"] = IMAGES[route["path"]]
    path = tmp_path / "spa_routes.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setenv("FRONTEND_DIST", str(dist))
    monkeypatch.setenv("ECO_SPA_ROUTES", str(path))
    return TestClient(create_app())


def _tag(doc: str, attr: str, name: str) -> str | None:
    found = re.search(rf'<meta\s[^>]*{attr}="{re.escape(name)}"[^>]*content="([^"]*)"', doc)
    return found.group(1) if found else None


def _title(doc: str) -> str:
    return re.search(r"<title>(.*?)</title>", doc, re.DOTALL).group(1)  # type: ignore[union-attr]


def _body(doc: str) -> str:
    return doc[doc.index("<body") :]


def test_routes_get_their_own_title_description_and_canonical_url(served: TestClient) -> None:
    seen = set()
    for path in ("/items", "/map", "/cycle-14/castle"):
        doc = served.get(path).text
        title, description = WORDS[path]
        assert _title(doc) == title
        assert _tag(doc, "property", "og:title") == title
        assert _tag(doc, "name", "description") == description
        assert _tag(doc, "property", "og:description") == description
        # og:url is the canonical the server names for the path, and none where it names none.
        assert _tag(doc, "property", "og:url") == seo.classify(path).canonical
        seen.add(_title(doc))
    assert len(seen) == 3
    assert _tag(served.get("/items").text, "property", "og:url") == f"{SITE}/items"


def test_routes_with_a_card_name_it_and_the_rest_keep_the_default(served: TestClient) -> None:
    for path in ("/", "/items", "/map", "/cycle-14/castle"):
        doc = served.get(path).text
        image = _tag(doc, "property", "og:image")
        if path in IMAGES:
            assert image == f"{SITE}{IMAGES[path]}"
            assert _tag(doc, "property", "og:image:alt") == WORDS[path][0]
        else:
            assert image == DEFAULT_CARD
    # Words without a card: the title changes and the picture stays the shell's.
    doc = served.get("/item?name=Iron+Ore").text
    assert _title(doc) == "Item"
    assert _tag(doc, "property", "og:image") == DEFAULT_CARD
    assert _tag(doc, "property", "og:image:alt") != "Item"


def test_a_wildcard_route_gives_deeper_paths_its_card(served: TestClient) -> None:
    for path in ("/jobs", "/jobs/professions", "/jobs/professions/deep"):
        doc = served.get(path).text
        assert _tag(doc, "property", "og:image") == f"{SITE}/og/jobs.png"
        assert _tag(doc, "property", "og:image:alt") == "Jobs"


def test_the_card_a_head_names_is_served_as_a_png(served: TestClient) -> None:
    """The URL in og:image must answer, or the preview is a broken picture."""
    for path in IMAGES:
        url = _tag(served.get(path.replace("/*", "")).text, "property", "og:image")
        assert url is not None
        reply = served.get(url.removeprefix(SITE))
        assert reply.status_code == 200
        assert reply.headers["content-type"] == "image/png"
        assert reply.content == PNG


def test_a_card_never_changes_the_canonical_or_robots_behavior(served: TestClient) -> None:
    for path in ("/items", "/jobs/professions", "/item?name=Iron+Ore"):
        reply = served.get(path)
        assert ("link" in reply.headers) == (path == "/items")
        assert ("x-robots-tag" in reply.headers) == (path != "/items")


def test_the_etag_follows_the_card(served: TestClient) -> None:
    assert served.get("/items").headers["etag"] != served.get("/trade").headers["etag"]


def test_a_card_without_a_title_sets_the_picture_and_no_alt() -> None:
    doc = shell_head.apply(
        '<html><head><meta property="og:image:alt" content="stale" /></head></html>',
        seo.Head(None, None, None, f"{SITE}/og/x.png?a=1&b=2"),
    )
    assert _tag(doc, "property", "og:image") == f"{SITE}/og/x.png?a=1&amp;b=2"
    assert _tag(doc, "property", "og:image:alt") == "stale"


def test_a_head_with_no_card_leaves_og_image_alone() -> None:
    shell = f'<html><head><meta property="og:image" content="{DEFAULT_CARD}" /></head></html>'
    doc = shell_head.apply(shell, seo.Head("T", "D", f"{SITE}/x"))
    assert _tag(doc, "property", "og:image") == DEFAULT_CARD


@pytest.mark.parametrize(
    "bad",
    ["https://elsewhere.example/c.png", "//elsewhere.example/c.png", "og/c.png", 7, ""],
)
def test_a_manifest_image_must_be_a_site_relative_path(
    bad: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A card on another origin would let a manifest edit send scrapers anywhere."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest["routes"][0]["image"] = bad
    path = tmp_path / "spa_routes.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setenv("ECO_SPA_ROUTES", str(path))
    with pytest.raises(ValueError, match="image must be a path"):
        seo.routes()


def test_a_query_string_never_reaches_og_url(served: TestClient) -> None:
    """An indexable route with a query keeps its bare canonical, never the query URL."""
    doc = served.get("/items?page=2&sort=price").text
    assert _tag(doc, "property", "og:url") == f"{SITE}/items"
    assert "page=2" not in doc


def test_a_noindex_route_names_no_url_and_keeps_its_defaults(served: TestClient) -> None:
    """`/item?name=` is not canonical, and the shell's home-page og:url would lie."""
    doc = served.get("/item?name=Iron+Ore").text
    assert _tag(doc, "property", "og:url") is None
    assert "Iron+Ore" not in doc
    assert _title(doc) == "Item"
    assert _tag(doc, "property", "og:title") == "Item"


def test_a_wildcard_route_gives_deeper_paths_the_parents_words(served: TestClient) -> None:
    doc = served.get("/jobs/professions").text
    assert _title(doc) == "Jobs"
    assert _tag(doc, "property", "og:description") == "Open jobs on the server."
    assert _tag(doc, "property", "og:url") is None


def test_a_route_without_words_keeps_the_shell_defaults_but_gets_its_own_url(
    served: TestClient,
) -> None:
    default = SHELL.read_text(encoding="utf-8")
    doc = served.get("/trade").text
    assert _title(doc) == _title(default)
    assert _tag(doc, "property", "og:title") == _tag(default, "property", "og:title")
    assert _tag(doc, "name", "description") == _tag(default, "name", "description")
    assert _tag(doc, "property", "og:url") == f"{SITE}/trade"


def test_the_body_is_identical_across_routes(served: TestClient) -> None:
    """Only the head differs, so the duplicate-content protection is unchanged."""
    bodies = {_body(served.get(p).text) for p in ("/", "/items", "/map", "/item?id=1", "/trade")}
    assert len(bodies) == 1


def test_crawl_headers_and_cache_policy_are_unchanged(served: TestClient) -> None:
    indexable = served.get("/items")
    assert indexable.headers["cache-control"] == "no-cache"
    assert indexable.headers["link"] == f'<{SITE}/items>; rel="canonical"'
    assert "x-robots-tag" not in indexable.headers
    noindex = served.get("/item?name=Iron+Ore")
    assert noindex.headers["x-robots-tag"] == "noindex, follow"
    assert "link" not in noindex.headers


def test_the_shell_revalidates_per_route(served: TestClient) -> None:
    """The ETag follows the rewritten head, so two routes never share one."""
    first = served.get("/items")
    assert first.headers["etag"] != served.get("/map").headers["etag"]
    again = served.get("/items", headers={"If-None-Match": first.headers["etag"]})
    assert again.status_code == 304
    assert again.headers["link"] == first.headers["link"]
    assert served.get("/items", headers={"If-None-Match": '"stale"'}).status_code == 200


def test_words_are_html_escaped() -> None:
    head = seo.Head('Tom & "Jerry" <b>', "a < b > c \" ' &", f"{SITE}/x")
    doc = shell_head.apply(SHELL.read_text(encoding="utf-8"), head)
    assert "<b>" not in doc
    assert '<title>Tom &amp; "Jerry" &lt;b&gt;</title>' in doc
    assert _tag(doc, "property", "og:title") == "Tom &amp; &quot;Jerry&quot; &lt;b&gt;"
    assert _tag(doc, "name", "description") == "a &lt; b &gt; c &quot; &#x27; &amp;"


def test_a_build_that_dropped_a_tag_gets_it_back() -> None:
    bare = "<html><head><title>t</title></head><body>b</body></html>"
    doc = shell_head.apply(bare, seo.Head("T", "D", f"{SITE}/x"))
    assert _tag(doc, "name", "description") == "D"
    assert _tag(doc, "property", "og:url") == f"{SITE}/x"
    assert doc.index("og:url") < doc.index("</head>") < doc.index("<body")


def test_no_head_tag_and_no_words_leaves_the_document_alone() -> None:
    assert shell_head.apply("<html>spa-shell</html>", seo.Head("T", "D", f"{SITE}/x")) == (
        "<html>spa-shell</html>"
    )


def test_a_blank_or_non_string_manifest_field_fails_the_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest["routes"][0]["title"] = "   "
    path = tmp_path / "spa_routes.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setenv("ECO_SPA_ROUTES", str(path))
    with pytest.raises(ValueError, match="title must be a nonblank string"):
        seo.routes()


def test_every_manifest_route_resolves_to_a_head() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for route in manifest["routes"]:
        path = route["path"].replace("/*", "").replace(":hex", "6b6169") or "/"
        assert seo.head_for(path) is not None, route["path"]
    assert seo.head_for("/not/a/route") is None
