"""The generic `/preview/<tool>.json` dispatcher defers to typed dual routes.

teable:coilyco/eco-app#8363: `/preview/get_stores.json?limit=0` passed `limit`
as the string "0", failed the tool schema, and answered 404 "did not return a
JSON content block". The typed route at `/preview/stores.json` coerces it.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from eco_mcp_app.http_app import create_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(), follow_redirects=False)


@pytest.mark.parametrize(
    ("path", "location"),
    [
        ("/preview/get_stores.json?limit=0", "/preview/stores.json?limit=0"),
        (
            "/preview/get_market.json?limit=0&item=Cement",
            "/preview/market.json?limit=0&item=Cement",
        ),
        ("/preview/get_stores.json", "/preview/stores.json"),
    ],
)
def test_tool_name_redirects_to_its_typed_route(
    client: TestClient, path: str, location: str
) -> None:
    r = client.get(path)
    assert r.status_code == 307
    assert r.headers["location"] == location


def test_redirect_carries_cors_for_the_dashboard(client: TestClient) -> None:
    r = client.get("/preview/get_stores.json?limit=0", headers={"Origin": "https://coilyco.dev"})
    assert r.status_code == 307
    assert r.headers["access-control-allow-origin"] == "https://coilyco.dev"


def test_a_tool_error_is_a_400_with_its_own_text(client: TestClient) -> None:
    # Not a dual route, so it stays on the generic path and its error surfaces.
    r = client.get("/preview/no_such_tool.json")
    assert r.status_code == 400
    assert "no_such_tool" in r.json()["error"]
