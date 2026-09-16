"""The `limit` invariant, asserted as a property rather than per tool.

eco-app#6076 argues the class: `limit` must bound every array that grows with
world size, and truncation must never be silent. Three rounds of fixing the
next demonstrated instance did not converge, so the rule is pinned here.

What this covers and what it does not, stated plainly because a green suite
should not be read as more than it proves:

* The two bounding helpers are exercised exhaustively. Both are shared, so a
  tool routing through either inherits the contract.
* The set of tools advertising `limit` is pinned, so adding a `limit`-bearing
  tool without wiring it to a helper trips a test rather than shipping.

It does NOT drive all 25 tools end to end against truncating fixtures, which
would need each one's upstream mocked.
"""

from __future__ import annotations

import json
from typing import Any

import mcp.types as mt
import pytest

from eco_mcp_app import server as eco_server
from eco_mcp_app.server import build_server

# Every tool whose schema advertises `limit`. Adding one without routing it
# through _bound_rows or _thin_series is the regression this pins.
LIMIT_BEARING = {
    "find_trade",
    "get_civics",
    "get_climate",
    "get_crafting_atlas",
    "get_currency",
    "get_map",
    "get_market",
    "get_recipes",
    "get_social",
    "get_species",
    "get_stores",
    "get_trades",
    "get_world",
}


async def _advertised_tools() -> list[mt.Tool]:
    """Every tool the server lists, not only the typed dual routes.

    get_social and trade_watchers are hand-written Tools in server.py rather
    than registry entries, so enumerating the registry alone misses them. It
    missed get_social, which is one this record is about.
    """
    mcp_server = build_server()
    handler = mcp_server.request_handlers[mt.ListToolsRequest]
    result = await handler(mt.ListToolsRequest(method="tools/list"))
    return list(result.root.tools)


@pytest.mark.asyncio
async def test_the_surface_is_still_twenty_five_tools() -> None:
    """#6076 audits "all 25 MCP tools", so the count is part of the claim."""
    assert len(await _advertised_tools()) == 25


@pytest.mark.asyncio
async def test_the_set_of_limit_bearing_tools_is_pinned() -> None:
    """A new tool taking `limit` has to be wired, and this is what notices."""
    advertised = {
        tool.name
        for tool in await _advertised_tools()
        if "limit" in ((tool.inputSchema or {}).get("properties") or {})
    }
    assert advertised == LIMIT_BEARING, (
        "the tools advertising `limit` changed. Route the new one through "
        "_bound_rows (row lists) or _thin_series (time series) and update this set"
    )


@pytest.mark.parametrize("total", [0, 1, 2, 5, 50, 137])
@pytest.mark.parametrize("limit", [1, 2, 10, 50])
def test_bound_rows_never_exceeds_its_limit_and_never_truncates_silently(
    total: int, limit: int
) -> None:
    payload: dict[str, Any] = {"rows": [{"i": i} for i in range(total)]}

    eco_server._bound_rows(payload, limit, "rows")

    assert len(payload["rows"]) <= limit or total <= limit
    truncated = total > limit
    warned = any(w.startswith("rows:") for w in payload.get("warnings", []))
    assert warned == truncated, (
        f"total={total} limit={limit}: truncated={truncated} but warned={warned}. "
        "Silent truncation reads as the whole population"
    )
    if truncated:
        assert len(payload["rows"]) == limit


@pytest.mark.parametrize("total", [0, 1, 2, 5, 50, 137])
@pytest.mark.parametrize("limit", [1, 2, 10, 50])
def test_thin_series_never_exceeds_its_limit_and_never_thins_silently(
    total: int, limit: int
) -> None:
    points = [[float(i), float(i)] for i in range(total)]
    payload: dict[str, Any] = {"series": list(points)}

    eco_server._thin_series(payload, limit, "series")

    assert len(payload["series"]) <= max(limit, total if total <= limit else limit)
    thinned = total > limit
    warned = any(w.startswith("series:") for w in payload.get("warnings", []))
    assert warned == thinned, f"total={total} limit={limit}: thinned but silent"
    if thinned:
        assert len(payload["series"]) == limit
        # A curve keeps its true ends, or first/latest stop being honest. One
        # sample cannot keep both, and _downsample keeps the latest.
        assert payload["series"][-1] == points[-1]
        if limit > 1:
            assert payload["series"][0] == points[0]


@pytest.mark.parametrize("helper", ["_bound_rows", "_thin_series"])
def test_limit_zero_means_every_row(helper: str) -> None:
    """Both helpers read 0 as no limit, which is what the schema text promises."""
    rows = [{"i": i} for i in range(200)]
    payload: dict[str, Any] = {"rows": list(rows)}

    getattr(eco_server, helper)(payload, 0, "rows")

    assert payload["rows"] == rows
    assert "warnings" not in payload


class _Report:
    """Stands in for a market or logistics report, which only needs to_dict."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def to_dict(self) -> dict[str, Any]:
        return self._payload


@pytest.mark.parametrize(
    ("tool", "target", "markdown", "key"),
    [
        ("get_market", "fetch_market", "market_markdown", "markets"),
        ("find_trade", "fetch_logistics", "logistics_markdown", "cheapest"),
    ],
)
@pytest.mark.asyncio
async def test_the_two_newly_bounded_tools_actually_bound(
    monkeypatch: pytest.MonkeyPatch, tool: str, target: str, markdown: str, key: str
) -> None:
    """Their upstream answers 401 off the cluster, so the bound is pinned here.

    Both grew with world size and took no `limit` until eco-app#6076: markets
    with the count of distinct traded items, the logistics arrays with stores.
    """
    rows = [{"i": i} for i in range(40)]
    holder = eco_server.market_mod if tool == "get_market" else eco_server

    async def _fetch(**_: Any) -> _Report:
        return _Report({key: list(rows)})

    monkeypatch.setattr(holder, target, _fetch)
    monkeypatch.setattr(
        eco_server.market_mod if tool == "get_market" else eco_server,
        markdown,
        lambda _report: "stub",
    )

    handler = build_server().request_handlers[mt.CallToolRequest]
    result = await handler(
        mt.CallToolRequest(
            method="tools/call",
            params=mt.CallToolRequestParams(name=tool, arguments={"limit": 5}),
        )
    )
    payload = json.loads(result.root.content[-1].text)

    assert len(payload[key]) == 5
    assert any(w.startswith(f"{key}:") for w in payload["warnings"])
