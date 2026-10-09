"""Exercise the tool surface end-to-end through the MCP server."""

from __future__ import annotations

from typing import Any

import mcp.types as mt
import pytest

from eco_mcp_app.server import (
    DISABLED_TOOLS,
    build_server,
)


@pytest.mark.asyncio
async def test_list_tools_advertises_all_tools() -> None:
    mcp = build_server()
    handler = mcp.request_handlers[mt.ListToolsRequest]
    result = await handler(mt.ListToolsRequest(method="tools/list"))
    names = {tool.name for tool in result.root.tools}
    assert names == {
        "get_server_status",
        "get_milestones",
        "get_species",
        "explain_item",
        "get_crafting_atlas",
        "get_world",
        "get_trades",
        "get_market",
        "get_stores",
        "get_progression",
        "get_social",
        "find_trade",
        "get_government",
        "get_civics",
        "get_region",
        "get_climate",
        "get_currency",
        # The Eco Gnome recipe/cost plane, which had no MCP surface at all
        # until eco-app#242.
        "get_recipes",
        "price_recipe",
        "get_skills",
        # Per-stage median price for one item (teable:coilyco/eco-app#8423).
        "price_by_stage",
    }


# --- response-cap bounding (#256) -------------------------------------------


def test_bound_rows_truncates_and_says_so() -> None:
    """Silent truncation reads as "covered everything" when it did not."""
    from eco_mcp_app.server import _bound_rows

    payload: dict[str, Any] = {"trades": list(range(500)), "warnings": []}
    _bound_rows(payload, 50, "trades")
    assert payload["trades"] == list(range(50))
    assert any("showing 50 of 500 rows" in w for w in payload["warnings"])
    assert any("limit=0" in w for w in payload["warnings"])


def test_bound_rows_leaves_short_lists_and_limit_zero_alone() -> None:
    from eco_mcp_app.server import _bound_rows

    short: dict[str, Any] = {"trades": [1, 2, 3]}
    _bound_rows(short, 50, "trades")
    assert short["trades"] == [1, 2, 3]
    assert "warnings" not in short

    unbounded: dict[str, Any] = {"trades": list(range(500))}
    _bound_rows(unbounded, 0, "trades")
    assert len(unbounded["trades"]) == 500


def test_resolve_limit_reads_strings_and_rejects_junk() -> None:
    """REST query params arrive as strings; a bad value falls back to default."""
    from eco_mcp_app.server import _resolve_limit

    assert _resolve_limit({}) == 50
    assert _resolve_limit({"limit": 10}) == 10
    assert _resolve_limit({"limit": "10"}) == 10
    assert _resolve_limit({"limit": 0}) == 0
    assert _resolve_limit({"limit": -5}) == 0
    assert _resolve_limit({"limit": "nonsense"}) == 50


def test_downsample_preserves_shape_and_endpoints() -> None:
    """A head slice would report day one and call it the trend."""
    from eco_mcp_app.server import _downsample

    points = [{"day": i, "value": i * 2} for i in range(1000)]
    thinned, was_thinned = _downsample(points, 10)
    assert was_thinned is True
    assert len(thinned) == 10
    # True endpoints survive, so first/latest stay honest.
    assert thinned[0] == points[0]
    assert thinned[-1] == points[-1]
    # Evenly spaced, not a head slice.
    assert thinned[5]["day"] > 400
    # Monotonic in the original order.
    assert [p["day"] for p in thinned] == sorted(p["day"] for p in thinned)


def test_downsample_leaves_short_series_untouched() -> None:
    from eco_mcp_app.server import _downsample

    points = [{"day": i} for i in range(10)]
    thinned, was_thinned = _downsample(points, 120)
    assert was_thinned is False
    assert thinned == points


@pytest.mark.asyncio
async def test_disabled_tools_are_unlisted_and_refused() -> None:
    mcp = build_server()
    list_handler = mcp.request_handlers[mt.ListToolsRequest]
    listed = await list_handler(mt.ListToolsRequest(method="tools/list"))
    assert not DISABLED_TOOLS & {tool.name for tool in listed.root.tools}
    call = mcp.request_handlers[mt.CallToolRequest]
    for name in sorted(DISABLED_TOOLS):
        result = await call(
            mt.CallToolRequest(
                method="tools/call", params=mt.CallToolRequestParams(name=name, arguments={})
            )
        )
        assert result.root.isError is True
        assert result.root.content[0].text == f"{name} is disabled until it is fixed."
