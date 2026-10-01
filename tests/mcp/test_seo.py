"""The crawl surface: what a search engine is told about each URL.

Every one of these was a 200 answering with the SPA shell before, which is how
one page became an unbounded index of soft 404s and query-keyed duplicates.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from eco_mcp_app import seo
from eco_mcp_app.http_app import create_app

MANIFEST = Path("data/spa_routes.json")


@pytest.fixture(autouse=True)
def _fresh_manifest() -> None:
    # The table is cached per path; a test that repoints ECO_SPA_ROUTES would
    # otherwise read whatever the previous test loaded.
    seo._load.cache_clear()


def test_robots_is_served_by_the_app_not_the_spa_shell() -> None:
    """The bug in one line: `/robots.txt` used to answer 200 with HTML.

    A crawler asking for rules and receiving a React shell has no rules, which
    is why every path on the host was fair game.
    """
    client = TestClient(create_app())
    r = client.get("/robots.txt")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert "<html" not in r.text.lower()
    assert r.text.startswith("User-agent: *")
    assert "Sitemap: https://eco-app.coilysiren.me/sitemap.xml" in r.text


def test_robots_disallows_the_data_planes_and_no_pages() -> None:
    """Disallow is for the JSON and MCP surfaces, never for a page.

    A page that should leave the index is left crawlable so the crawler can
    reach its noindex header. Disallow it instead and the URL is never fetched
    again, so it sits in the index forever with nothing to resolve it.
    """
    lines = seo.robots_txt().splitlines()
    disallowed = [line.split(": ", 1)[1] for line in lines if line.startswith("Disallow")]
    assert "/preview/" in disallowed
    assert "/mcp/" in disallowed
    assert "/jobs/api/" in disallowed
    for page in ("/item", "/users", "/social", "/replay", "/trade"):
        assert page not in disallowed


def test_sitemap_lists_the_canonical_pages_only() -> None:
    client = TestClient(create_app())
    r = client.get("/sitemap.xml")
    assert r.status_code == 200
    assert "xml" in r.headers["content-type"]
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    locs = {el.text for el in ET.fromstring(r.text).findall("s:url/s:loc", ns)}
    assert "https://eco-app.coilysiren.me/trade" in locs
    assert "https://eco-app.coilysiren.me/jobs" in locs
    # Noindex routes, retired paths, and wildcard sub-paths are not canonical.
    uncanonical = ("/item", "/recipe", "/social", "/replay", "/users", "/world")
    for absent in (*uncanonical, "/jobs/professions"):
        assert f"https://eco-app.coilysiren.me{absent}" not in locs


@pytest.mark.usefixtures("dist")
def test_a_path_no_route_owns_is_a_404() -> None:
    """The soft-404 firehose. `/some/future/page` used to answer 200."""
    client = TestClient(create_app())
    for path in ("/some/future/page", "/wp-login", "/nope", "/uses/not-a-use"):
        r = client.get(path)
        assert r.status_code == 404, path
        assert "spa-shell" not in r.text


@pytest.mark.usefixtures("dist")
def test_a_retired_path_301s_instead_of_rerendering() -> None:
    """A client-side <Navigate> is a 200 to a crawler, so both URLs index."""
    client = TestClient(create_app(), follow_redirects=False)
    for old, new in (
        ("/server", "/"),
        ("/info", "/"),
        ("/social", "/"),
        ("/world", "/map"),
        ("/trades", "/trade"),
    ):
        r = client.get(old)
        assert r.status_code == 301, old
        assert r.headers["location"] == new


@pytest.mark.usefixtures("dist")
def test_a_retired_path_keeps_its_query_string_across_the_redirect() -> None:
    client = TestClient(create_app(), follow_redirects=False)
    r = client.get("/climate?server=alt")
    assert r.headers["location"] == "/map?server=alt"


@pytest.mark.usefixtures("dist")
def test_a_canonical_page_carries_a_canonical_link_and_no_noindex() -> None:
    client = TestClient(create_app())
    for path in ("/", "/trade", "/uses/price", "/cycle-14/castle"):
        r = client.get(path)
        assert r.status_code == 200
        assert "spa-shell" in r.text
        assert r.headers["link"] == f'<https://eco-app.coilysiren.me{path}>; rel="canonical"'
        assert "x-robots-tag" not in r.headers, path


@pytest.mark.usefixtures("dist")
def test_a_query_string_costs_indexability_on_any_route() -> None:
    """`?item=`, `?name=`, `?id=` are what multiply a page into thousands."""
    client = TestClient(create_app())
    r = client.get("/uses/price?item=Iron+Ore&currency=Sun")
    assert r.status_code == 200
    assert "spa-shell" in r.text
    assert r.headers["x-robots-tag"] == "noindex, follow"
    # No canonical alongside it: the two directives contradict each other, and
    # a crawler resolving that its own way is how a noindex stops holding.
    assert "link" not in r.headers


@pytest.mark.usefixtures("dist")
def test_url_only_and_personal_pages_are_noindex_but_still_served() -> None:
    client = TestClient(create_app())
    for path in ("/item", "/recipe", "/replay", "/jobs/professions"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert "spa-shell" in r.text, path
        assert r.headers["x-robots-tag"] == "noindex, follow", path


@pytest.mark.usefixtures("dist")
def test_a_removed_player_dossier_is_gone_not_redirected() -> None:
    """/users/<hex> was one person's page (eco-app#8385), so it 404s rather than
    pointing somewhere that implies it still exists."""
    client = TestClient(create_app(), follow_redirects=False)
    assert client.get("/users/6b6169").status_code == 404


def test_trailing_slashes_and_case_do_not_fork_a_route() -> None:
    assert seo.classify("/trade/").indexable
    assert seo.classify("trade").indexable
    assert seo.classify("/").indexable


def test_every_manifest_route_resolves_to_itself() -> None:
    """A route in the table is a route the classifier recognizes.

    The drift this guards: a page added to the router that the crawl policy
    then treats as an unknown path and 404s.
    """
    manifest = json.loads(MANIFEST.read_text())
    for route in manifest["routes"]:
        path = route["path"].replace("/*", "").replace(":hex", "6b6169") or "/"
        assert seo.classify(path).known, route["path"]


def test_no_route_is_also_a_redirect() -> None:
    """`/users` redirects and `/users/:hex` renders — but not the same path."""
    manifest = json.loads(MANIFEST.read_text())
    live = {r["path"] for r in manifest["routes"]}
    moved = {r["from"] for r in manifest["redirects"]}
    assert not (live & moved)
