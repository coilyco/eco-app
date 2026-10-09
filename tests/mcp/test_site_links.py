"""Every MCP answer points at the page that carries it in full (eco-app#241).

An MCP response is a summary by necessity — the client's response cap is real,
and the 2026-08-12 sweep found 7 of 22 tools exceeding it. A curt answer plus a
link to the live page is the pressure relief: the caller gets the number and
knows where the rest lives.
"""

from __future__ import annotations

import json
from typing import Any

import mcp.types as mt
import pytest

from eco_mcp_app.dual_routes import DualRouteRegistry
from eco_mcp_app.server import (
    PUBLIC_SITE_URL,
    TOOL_SITE_PATHS,
    _append_site_link,
    build_server,
    site_url_for,
)
from eco_mcp_app.wave1_routes import register_wave1_routes


def _result(text: str) -> mt.CallToolResult:
    return mt.CallToolResult(content=[mt.TextContent(type="text", text=text)])


def test_every_registered_tool_has_a_page() -> None:
    """A tool with no link is a tool whose detail a caller cannot reach."""
    mcp = build_server()
    tools = mcp.request_handlers[mt.ListToolsRequest]
    assert tools is not None  # sanity: the handler is registered
    for name in TOOL_SITE_PATHS:
        assert site_url_for(name), f"{name} resolves no site URL"
        assert site_url_for(name).startswith(PUBLIC_SITE_URL)


def test_the_link_is_appended_to_the_markdown_block() -> None:
    result = _append_site_link("get_trades", _result("**Trades ledger** — 12 trades"))
    text = result.content[0].text
    assert text.startswith("**Trades ledger**")
    assert f"{PUBLIC_SITE_URL}/trade" in text


def test_an_unknown_tool_is_left_alone() -> None:
    result = _append_site_link("not_a_tool", _result("body"))
    assert result.content[0].text == "body"


def test_the_link_is_not_doubled_up() -> None:
    once = _append_site_link("get_civics", _result("body"))
    twice = _append_site_link("get_civics", once)
    assert twice.content[0].text.count(PUBLIC_SITE_URL) == 1


def test_an_empty_result_is_left_alone() -> None:
    empty = mt.CallToolResult(content=[])
    assert _append_site_link("get_civics", empty).content == []


@pytest.mark.asyncio
async def test_a_live_tool_call_carries_the_link() -> None:
    """End-to-end through the dispatcher, not just the helper.

    Every tool with a page reads a live Eco server, so the call goes through a
    registry whose invoker answers locally.
    """

    async def invoke(tool_name: str, arguments: dict[str, Any]) -> mt.CallToolResult:
        payload = {"tool": tool_name, "arguments": arguments}
        return mt.CallToolResult(
            content=[
                mt.TextContent(type="text", text=f"Called {tool_name}."),
                mt.TextContent(type="text", text=json.dumps(payload)),
            ],
            structuredContent=payload,
        )

    registry = DualRouteRegistry()
    register_wave1_routes(registry, invoke)
    handler = build_server(registry).request_handlers[mt.CallToolRequest]
    result = await handler(
        mt.CallToolRequest(
            method="tools/call",
            params=mt.CallToolRequestParams(name="get_server_status", arguments={}),
        )
    )
    assert f"{PUBLIC_SITE_URL}/info" in result.root.content[0].text


def _logistics_result(*item_ids: str) -> mt.CallToolResult:
    """A find_trade result shaped like the real one: markdown, then the JSON."""
    rows = [{"item": item_id, "itemPretty": item_id, "currency": "Credit"} for item_id in item_ids]
    payload = {"view": "logistics", "cheapest": rows, "marketSummaries": rows}
    return mt.CallToolResult(
        content=[
            mt.TextContent(type="text", text="**Trade logistics**"),
            mt.TextContent(type="text", text=json.dumps(payload)),
        ],
        structuredContent=payload,
    )


def test_find_trade_with_an_item_links_that_items_page() -> None:
    """COI-759: the link carries the resolved id, not the raw user string."""
    result = _append_site_link(
        "find_trade", _logistics_result("CharredSausageItem"), {"item": "charred sausage"}
    )
    assert result.content[0].text.endswith(
        f"Full detail: {PUBLIC_SITE_URL}/item?item=CharredSausageItem"
    )
    assert "arbitrage" not in result.content[0].text


def test_find_trade_without_an_item_keeps_the_arbitrage_page() -> None:
    for arguments in (None, {}, {"item": ""}, {"item": "   "}, {"currency": "Credit"}):
        result = _append_site_link("find_trade", _logistics_result("IronItem"), arguments)
        assert result.content[0].text.endswith(f"Full detail: {PUBLIC_SITE_URL}/uses/arbitrage")


def test_find_trade_item_that_matched_nothing_keeps_the_arbitrage_page() -> None:
    result = _append_site_link("find_trade", _logistics_result(), {"item": "nonsense"})
    assert result.content[0].text.endswith("/uses/arbitrage")


def test_find_trade_item_spanning_several_ids_does_not_guess() -> None:
    result = _append_site_link(
        "find_trade", _logistics_result("IronIngotItem", "IronOreItem"), {"item": "iron"}
    )
    assert result.content[0].text.endswith("/uses/arbitrage")


def test_find_trade_item_prefers_the_exact_id_among_several() -> None:
    result = _append_site_link(
        "find_trade", _logistics_result("IronIngotItem", "IronOreItem"), {"item": "Iron Ore"}
    )
    assert result.content[0].text.endswith("/item?item=IronOreItem")


def test_another_tool_ignores_its_item_argument() -> None:
    result = _append_site_link("get_trades", _result("body"), {"item": "Iron"})
    assert result.content[0].text.endswith(f"{PUBLIC_SITE_URL}/trade")


@pytest.mark.asyncio
async def test_a_find_trade_call_with_an_item_ends_on_the_item_link() -> None:
    """Through the real dispatcher and the dual-route registry, not just the helper."""

    async def invoke(tool_name: str, arguments: dict[str, Any]) -> mt.CallToolResult:
        return _logistics_result("CharredSausageItem")

    registry = DualRouteRegistry()
    register_wave1_routes(registry, invoke)
    handler = build_server(registry).request_handlers[mt.CallToolRequest]

    async def call(arguments: dict[str, Any]) -> str:
        result = await handler(
            mt.CallToolRequest(
                method="tools/call",
                params=mt.CallToolRequestParams(name="find_trade", arguments=arguments),
            )
        )
        return result.root.content[0].text

    assert (await call({"item": "charred sausage"})).endswith(
        f"Full detail: {PUBLIC_SITE_URL}/item?item=CharredSausageItem"
    )
    assert (await call({})).endswith(f"Full detail: {PUBLIC_SITE_URL}/uses/arbitrage")
