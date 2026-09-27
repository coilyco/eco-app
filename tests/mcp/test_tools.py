"""Exercise the tool surface end-to-end through the MCP server."""

from __future__ import annotations

import json
from typing import Any

import mcp.types as mt
import pytest

from eco_mcp_app.server import (
    DISABLED_TOOLS,
    KNOWN_PUBLIC_SERVERS,
    PUBLIC_SERVERS_OUTPUT_SCHEMA,
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
        "list_public_servers",
        "get_map",
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
    }


@pytest.mark.asyncio
async def test_list_public_servers_returns_curated_list() -> None:
    mcp = build_server()
    handler = mcp.request_handlers[mt.CallToolRequest]
    req = mt.CallToolRequest(
        method="tools/call",
        params=mt.CallToolRequestParams(name="list_public_servers", arguments={}),
    )
    result = await handler(req)
    assert result.root.structuredContent == {"servers": KNOWN_PUBLIC_SERVERS}
    blocks = result.root.content
    assert len(blocks) == 2
    # Both blocks are TextContent by construction; narrow for mypy.
    assert isinstance(blocks[0], mt.TextContent)
    assert isinstance(blocks[1], mt.TextContent)
    md = blocks[0].text
    payload = json.loads(blocks[1].text)
    assert payload["servers"] == KNOWN_PUBLIC_SERVERS
    for s in KNOWN_PUBLIC_SERVERS:
        assert s["label"] in md
        assert s["host"] in md


@pytest.mark.asyncio
async def test_list_public_servers_advertises_safe_structured_metadata() -> None:
    mcp = build_server()
    handler = mcp.request_handlers[mt.ListToolsRequest]
    result = await handler(mt.ListToolsRequest(method="tools/list"))
    tool = next(tool for tool in result.root.tools if tool.name == "list_public_servers")

    assert tool.annotations is not None
    assert tool.annotations.readOnlyHint is True
    assert tool.annotations.destructiveHint is False
    assert tool.annotations.idempotentHint is True
    assert tool.annotations.openWorldHint is False
    assert tool.outputSchema == PUBLIC_SERVERS_OUTPUT_SCHEMA


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
