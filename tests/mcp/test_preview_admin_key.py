"""The five /preview endpoints still get the Eco server's HTTP API key (COI-763).

`ECO_ADMIN_TOKEN` and `_resolve_admin_key()` are the Eco server's own API key, not part
of the retired /admin MCP. Deleting that surface by grepping for ADMIN would have broken
`items`, `food`, `item`, `price-history` and `recipes` without failing a test that
asserted only on status. So each endpoint is driven end to end and the key the upstream
fetcher actually receives is captured, with an unkeyed control to show the capture can
fail.
"""

from __future__ import annotations

from typing import Any

import pytest
from starlette.testclient import TestClient

from eco_mcp_app import server as eco_server
from eco_mcp_app.http_app import create_app

KEY = "eco-http-api-key"
SERVER = "eco.test:3001"


class _Payload:
    def __init__(self, body: dict[str, Any]) -> None:
        self._body = body

    def to_dict(self) -> dict[str, Any]:
        return dict(self._body)


@pytest.fixture
def seen(monkeypatch: pytest.MonkeyPatch) -> dict[str, str | None]:
    """The api_key each upstream fetcher received, keyed by endpoint."""
    captured: dict[str, str | None] = {}

    async def items(**kwargs: Any) -> _Payload:
        captured["items"] = kwargs.get("api_key")
        return _Payload({"view": "items", "items": []})

    async def food(**kwargs: Any) -> _Payload:
        captured["food"] = kwargs.get("api_key")
        return _Payload({"view": "food_signals"})

    async def item(_item: str, **kwargs: Any) -> _Payload:
        captured["item"] = kwargs.get("api_key")
        return _Payload({"view": "item", "item": _item})

    async def price_history(_item: str, _currency: str, **kwargs: Any) -> dict[str, Any]:
        captured["price-history"] = kwargs.get("api_key")
        return {"view": "item-price-history"}

    async def price_map(*_args: Any, **kwargs: Any) -> dict[str, float]:
        captured["recipes"] = kwargs.get("api_key")
        return {}

    monkeypatch.setattr("eco_mcp_app.http_app.fetch_item_index", items)
    monkeypatch.setattr("eco_mcp_app.http_app.fetch_food_report", food)
    monkeypatch.setattr("eco_mcp_app.http_app.fetch_item_pivot", item)
    monkeypatch.setattr("eco_mcp_app.http_app.fetch_item_price_history", price_history)
    monkeypatch.setattr("eco_mcp_app.market.fetch_price_map", price_map)
    return captured


def _drive(client: TestClient) -> dict[str, int]:
    return {
        "items": client.get("/preview/items.json", params={"server": SERVER}).status_code,
        "food": client.get("/preview/food.json", params={"server": SERVER}).status_code,
        "item": client.get(
            "/preview/item.json", params={"server": SERVER, "item": "BeetItem"}
        ).status_code,
        "price-history": client.get(
            "/preview/price-history.json",
            params={"server": SERVER, "item": "BeetItem", "currency": "Credit"},
        ).status_code,
        "recipes": client.get(
            "/preview/recipes.json", params={"server": SERVER, "cost": "1", "limit": 1}
        ).status_code,
    }


def test_all_five_endpoints_answer_and_receive_the_resolved_key(
    monkeypatch: pytest.MonkeyPatch, seen: dict[str, str | None]
) -> None:
    monkeypatch.delenv("ECO_ADMIN_API_KEY", raising=False)
    monkeypatch.setenv("ECO_ADMIN_TOKEN", KEY)
    monkeypatch.setattr(eco_server, "_ECO_ADMIN_TOKEN_LOADED", False)
    monkeypatch.setattr(eco_server, "_ECO_ADMIN_TOKEN", None)

    statuses = _drive(TestClient(create_app()))

    assert statuses == dict.fromkeys(statuses, 200)
    assert seen == dict.fromkeys(statuses, KEY)


def test_the_capture_can_fail_when_no_key_resolves(
    monkeypatch: pytest.MonkeyPatch, seen: dict[str, str | None]
) -> None:
    monkeypatch.delenv("ECO_ADMIN_API_KEY", raising=False)
    monkeypatch.delenv("ECO_ADMIN_TOKEN", raising=False)
    # Loaded with nothing in it: the resolver returns None without reaching for SSM.
    monkeypatch.setattr(eco_server, "_ECO_ADMIN_TOKEN_LOADED", True)
    monkeypatch.setattr(eco_server, "_ECO_ADMIN_TOKEN", None)

    _drive(TestClient(create_app()))

    assert seen == dict.fromkeys(["items", "food", "item", "price-history", "recipes"])


def test_no_admin_mcp_route_or_flag_survives(monkeypatch: pytest.MonkeyPatch) -> None:
    """Setting the dead flag does not resurrect a mount, and /admin is not an MCP endpoint."""
    monkeypatch.setenv("ECO_ADMIN_ENABLED", "1")
    client = TestClient(create_app())

    assert client.post("/admin", json={}).status_code in {404, 405}
    assert client.post("/admin/", json={}).status_code in {404, 405}
    assert not [r for r in create_app().routes if getattr(r, "path", "").startswith("/admin")]
