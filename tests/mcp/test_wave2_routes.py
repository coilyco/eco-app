"""Wave 2 public operations share one typed REST and MCP contract."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

import mcp.types as mt
import pytest
from fastapi.testclient import TestClient

from eco_mcp_app.dual_routes import DualRouteRegistry
from eco_mcp_app.http_app import create_app
from eco_mcp_app.server import build_server
from eco_mcp_app.wave2_routes import WAVE2_PATHS, WAVE2_TOOL_NAMES, register_wave2_routes

WAVE2_ARGUMENTS: dict[str, dict[str, Any]] = {
    "get_economy": {"server": "eco.test:3001"},
    # include_geometry is spelled out for the same reason as include_image:
    # it defaults to False and the registry passes the resolved model through.
    # SVG coordinates are opt-in so an MCP caller stays readable (eco-app#264).
    "get_map": {"server": "eco.test:3001", "include_geometry": False, "limit": 50},
    "get_milestones": {"server": "eco.test:3001"},
    # include_image is spelled out because it defaults to False and the
    # registry passes the resolved model through — inlined images are opt-in so
    # an MCP caller stays under its response cap (eco-app#230).
    "get_species": {"name": "Bison", "include_image": False, "limit": 120},
    "explain_item": {"name": "Iron", "category": "material", "include_image": False},
    # limit is spelled out because it defaults to a bounded slice and the
    # registry passes the resolved model through — detail arrays are capped so
    # an MCP caller stays under its response cap (eco-app#256).
    "get_crafting_atlas": {"server": "eco.test:3001", "limit": 50},
    "get_trades": {"server": "eco.test:3001", "limit": 50},
    "fair_price": {
        "item": "Copper",
        "cycle_id": "cycle-test",
        "server": "eco.test:3001",
    },
    "get_region": {"server": "eco.test:3001"},
    "get_climate": {"server": "eco.test:3001", "limit": 50},
    "get_government": {"server": "eco.test:3001"},
}


def _wave2_registry(
    invoke: Callable[[str, dict[str, Any]], Awaitable[mt.CallToolResult]],
) -> DualRouteRegistry:
    registry = DualRouteRegistry()
    register_wave2_routes(registry, invoke)
    return registry


@pytest.mark.parametrize(("name", "path"), sorted(WAVE2_PATHS.items()))
@pytest.mark.asyncio
async def test_wave2_routes_share_success_payloads(name: str, path: str) -> None:
    async def invoke(tool_name: str, arguments: dict[str, Any]) -> mt.CallToolResult:
        payload = {"tool": tool_name, "arguments": arguments}
        return mt.CallToolResult(
            content=[
                mt.TextContent(type="text", text=f"Called {tool_name}."),
                mt.TextContent(type="text", text=json.dumps(payload)),
            ],
            structuredContent=payload,
        )

    registry = _wave2_registry(invoke)
    arguments = WAVE2_ARGUMENTS[name]
    expected = {"tool": name, "arguments": arguments}

    rest = TestClient(create_app(registry)).get(path, params=arguments)
    assert rest.status_code == 200
    assert rest.json() == expected

    mcp = build_server(registry)
    call_handler = mcp.request_handlers[mt.CallToolRequest]
    called = await call_handler(
        mt.CallToolRequest(
            method="tools/call",
            params=mt.CallToolRequestParams(name=name, arguments=arguments),
        )
    )
    assert called.root.isError is False
    assert called.root.structuredContent == expected


@pytest.mark.asyncio
async def test_wave2_required_and_constrained_inputs_have_transport_parity() -> None:
    calls: list[str] = []

    async def invoke(name: str, arguments: dict[str, Any]) -> mt.CallToolResult:
        calls.append(name)
        return mt.CallToolResult(content=[])

    registry = _wave2_registry(invoke)
    client = TestClient(create_app(registry))
    assert client.get(WAVE2_PATHS["get_species"]).status_code == 422
    assert (
        client.get(
            WAVE2_PATHS["explain_item"],
            params={"name": "Iron", "category": "legend"},
        ).status_code
        == 422
    )

    mcp = build_server(registry)
    call_handler = mcp.request_handlers[mt.CallToolRequest]
    missing = await call_handler(
        mt.CallToolRequest(
            method="tools/call",
            params=mt.CallToolRequestParams(name="get_species", arguments={}),
        )
    )
    constrained = await call_handler(
        mt.CallToolRequest(
            method="tools/call",
            params=mt.CallToolRequestParams(
                name="explain_item",
                arguments={"name": "Iron", "category": "legend"},
            ),
        )
    )
    assert missing.root.isError is True
    assert constrained.root.isError is True
    assert calls == []


@pytest.mark.asyncio
async def test_wave2_downstream_failure_has_transport_parity() -> None:
    error_payload = {
        "view": "error",
        "message": "Eco server was unavailable.",
        "error": "Eco server was unavailable.",
    }

    async def invoke(name: str, arguments: dict[str, Any]) -> mt.CallToolResult:
        return mt.CallToolResult(
            content=[
                mt.TextContent(type="text", text="Eco server was unavailable."),
                mt.TextContent(type="text", text=json.dumps(error_payload)),
            ],
            isError=True,
        )

    registry = _wave2_registry(invoke)
    rest = TestClient(create_app(registry)).get(WAVE2_PATHS["get_economy"])
    assert rest.status_code == 502
    assert rest.json() == error_payload

    mcp = build_server(registry)
    call_handler = mcp.request_handlers[mt.CallToolRequest]
    called = await call_handler(
        mt.CallToolRequest(
            method="tools/call",
            params=mt.CallToolRequestParams(name="get_economy", arguments={}),
        )
    )
    assert called.root.isError is True
    assert called.root.structuredContent == error_payload


@pytest.mark.asyncio
async def test_wave2_unexpected_failure_stays_public_safe() -> None:
    async def invoke(name: str, arguments: dict[str, Any]) -> mt.CallToolResult:
        raise RuntimeError("private downstream detail")

    registry = _wave2_registry(invoke)
    expected = {
        "error": "operation_failed",
        "message": "The operation could not be completed.",
    }

    rest = TestClient(create_app(registry)).get(WAVE2_PATHS["get_economy"])
    assert rest.status_code == 500
    assert rest.json() == expected

    mcp = build_server(registry)
    call_handler = mcp.request_handlers[mt.CallToolRequest]
    called = await call_handler(
        mt.CallToolRequest(
            method="tools/call",
            params=mt.CallToolRequestParams(name="get_economy", arguments={}),
        )
    )
    assert called.root.isError is True
    assert isinstance(called.root.content[1], mt.TextContent)
    assert json.loads(called.root.content[1].text) == expected


@pytest.mark.asyncio
async def test_wave2_tools_are_discovered_once_with_typed_outputs() -> None:
    mcp = build_server()
    list_handler = mcp.request_handlers[mt.ListToolsRequest]
    listed = await list_handler(mt.ListToolsRequest(method="tools/list"))
    tools = [tool for tool in listed.root.tools if tool.name in WAVE2_TOOL_NAMES]

    assert {tool.name for tool in tools} == WAVE2_TOOL_NAMES
    assert len(tools) == len(WAVE2_TOOL_NAMES)
    assert all(tool.outputSchema is not None for tool in tools)
    assert all(tool.annotations is not None for tool in tools)
    assert all(tool.annotations.readOnlyHint is True for tool in tools if tool.annotations)
