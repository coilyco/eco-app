"""coilyco.dev may call `/mcp` and `/preview/` from a browser; nothing else may.

teable:coilyco/eco-app#8362. The dashboard at coilyco.dev/dash/eco fetches
from the viewer's browser, so these routes need CORS for exactly that origin.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from eco_mcp_app.http_app import create_app

DASH = "https://coilyco.dev"


@pytest.fixture
def client() -> Iterator[TestClient]:
    # Context manager engages the lifespan that starts the MCP session manager.
    with TestClient(create_app()) as c:
        yield c


def _preflight(client: TestClient, path: str, origin: str, method: str = "POST"):
    return client.options(
        path,
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": "content-type, mcp-protocol-version",
        },
    )


@pytest.mark.parametrize(
    "path", ["/mcp", "/mcp/", "/preview/get_government.json", "/preview.json", "/preview-map.json"]
)
def test_preflight_from_the_dashboard_is_granted(client: TestClient, path: str) -> None:
    r = _preflight(client, path, DASH)
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == DASH
    assert "DELETE" in r.headers["access-control-allow-methods"]
    assert r.headers["vary"] == "Origin"


def test_preflight_from_another_origin_gets_no_allow_header(client: TestClient) -> None:
    r = _preflight(client, "/mcp", "https://example.com")
    assert "access-control-allow-origin" not in r.headers


def test_mcp_post_carries_the_allow_and_expose_headers(client: TestClient) -> None:
    r = client.post(
        "/mcp",
        headers={"Origin": DASH, "Accept": "application/json, text/event-stream"},
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "cors-test", "version": "0"},
            },
        },
    )
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == DASH
    assert "Mcp-Session-Id" in r.headers["access-control-expose-headers"]
    assert r.headers["vary"] == "Origin"


def test_preview_get_carries_the_allow_header(client: TestClient) -> None:
    # price_by_stage without `item` fails validation before any network call.
    r = client.get("/preview/price_by_stage.json", headers={"Origin": DASH})
    assert r.status_code == 422
    assert r.headers["access-control-allow-origin"] == DASH


def test_other_origin_on_a_scoped_route_still_varies(client: TestClient) -> None:
    r = client.get("/preview/price_by_stage.json", headers={"Origin": "https://example.com"})
    assert "access-control-allow-origin" not in r.headers
    assert "Origin" in r.headers["vary"]


@pytest.mark.parametrize("path", ["/healthz", "/page-auth"])
def test_routes_outside_the_scope_stay_same_origin(client: TestClient, path: str) -> None:
    r = client.get(path, headers={"Origin": DASH})
    assert "access-control-allow-origin" not in r.headers
