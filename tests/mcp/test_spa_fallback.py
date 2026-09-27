"""The catch-all SPA fallback: client-side routes survive hard refreshes."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from eco_mcp_app.http_app import create_app

# The `dist` fixture lives in tests/mcp/conftest.py - test_scanner_paths.py
# needs the same stand-in build.


@pytest.mark.usefixtures("dist")
def test_client_routes_serve_the_spa_shell() -> None:
    client = TestClient(create_app())
    for path in ("/trade", "/jobs", "/uses/food"):
        r = client.get(path)
        assert r.status_code == 200
        assert "spa-shell" in r.text


@pytest.mark.usefixtures("dist")
def test_the_shell_no_longer_answers_for_anything_typed() -> None:
    """A path in `data/spa_routes.json` gets the shell; nothing else does.

    `/server` and `/some/future/page` both used to. The first is a retired path
    that now 301s to `/info`, and the second is a 404 — see tests/mcp/test_seo.py
    for why the blanket 200 was the whole Search Console problem.
    """
    client = TestClient(create_app(), follow_redirects=False)
    assert client.get("/server").status_code == 301
    assert client.get("/some/future/page").status_code == 404


def test_real_dist_files_serve_themselves(dist: Path) -> None:
    (dist / "manifest.webmanifest").write_text('{"name":"eco-app"}')
    client = TestClient(create_app())
    assert client.get("/manifest.webmanifest").text == '{"name":"eco-app"}'


@pytest.mark.usefixtures("dist")
def test_the_app_owns_robots_even_when_dist_ships_one() -> None:
    """The dist fixture writes a `robots.txt`; the app's route still wins.

    Crawl rules are derived from the route table, so a stale file left in a
    build must not be able to shadow them.
    """
    client = TestClient(create_app())
    r = client.get("/robots.txt")
    assert r.text != "crawl away"
    assert r.text.startswith("User-agent: *")


@pytest.mark.usefixtures("dist")
def test_explicit_routes_still_win() -> None:
    client = TestClient(create_app())
    r = client.get("/healthz")
    assert r.json() == {"ok": True}


@pytest.mark.usefixtures("dist")
def test_info_hard_load_renders_the_spa_not_json() -> None:
    # eco-app#96: `/info` used to be the service-discovery JSON route, so a hard
    # refresh returned JSON instead of the React Info page. It now falls through
    # to the SPA catch-all and serves the shell as HTML.
    client = TestClient(create_app())
    r = client.get("/info")
    assert r.status_code == 200
    assert "spa-shell" in r.text
    assert "application/json" not in r.headers.get("content-type", "")


@pytest.mark.usefixtures("dist")
def test_service_discovery_json_lives_under_api() -> None:
    # The service-discovery blob relocated to the `/api`-style path (eco-app#96).
    client = TestClient(create_app())
    r = client.get("/api/service")
    assert r.status_code == 200
    body = r.json()
    assert body["service"] == "eco-app"
    assert body["self"] == "/api/service"
    assert body["mcp"] == "/mcp/"
    # `/info` no longer serves this blob.
    assert "service" not in client.get("/info").text


@pytest.mark.usefixtures("dist")
def test_traversal_outside_dist_is_refused() -> None:
    """A traversal path is not a client route, so it 404s now (eco-app#215).

    It used to answer 200 with the SPA shell. Contained either way — the file
    itself was never served — but a dot-segment path can never be a SPA route,
    and answering 200 kept obvious probes out of the 4XX metrics.
    """
    client = TestClient(create_app())
    r = client.get("/%2e%2e/%2e%2e/etc/passwd")
    assert r.status_code == 404
    # The part that always mattered: no file contents, either way.
    assert "root:" not in r.text
    assert "spa-shell" not in r.text


@pytest.mark.usefixtures("dist")
def test_jobs_page_is_spa_and_api_keeps_public_paths() -> None:
    client = TestClient(create_app())
    r = client.get("/jobs")
    assert "spa-shell" in r.text
    r = client.get("/jobs/api/v1/professions")
    assert r.status_code == 200
    assert isinstance(r.json(), list)
    r = client.get("/jobs/api/v1/meta")
    assert r.json() == {"mockData": True}


@pytest.mark.usefixtures("dist")
def test_frame_ancestors_csp_is_site_wide() -> None:
    client = TestClient(create_app())
    for path in ("/jobs", "/healthz"):
        r = client.get(path)
        assert "frame-ancestors" in r.headers.get("content-security-policy", "")


def test_no_build_returns_build_hint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # No frontend build → no HTML surface (the old /preview redirect is gone).
    # A real client route returns a 404 build hint instead.
    monkeypatch.setenv("FRONTEND_DIST", str(tmp_path / "missing"))
    client = TestClient(create_app(), follow_redirects=False)
    r = client.get("/trade")
    assert r.status_code == 404
    assert "frontend-build" in r.text


def test_the_shell_revalidates_and_hashed_assets_cache_forever(dist: Path) -> None:
    """A stale shell after a deploy names assets that are gone (eco-app#8324)."""
    (dist / "assets" / "index-3f9a1c.js").write_text("console.log(1)")
    client = TestClient(create_app())
    for path in ("/", "/trade", "/index.html"):
        assert client.get(path).headers["cache-control"] == "no-cache", path
    asset = client.get("/assets/index-3f9a1c.js")
    assert asset.status_code == 200
    assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert "cache-control" not in client.get("/assets/missing.js").headers
