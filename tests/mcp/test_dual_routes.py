"""The shared route registry exposes one contract through REST and MCP."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

import mcp.types as mt
import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict, Field

from eco_mcp_app.dual_routes import DualRouteRegistry, DualRouteResult
from eco_mcp_app.http_app import create_app
from eco_mcp_app.server import build_server
from eco_mcp_app.wave1_routes import WAVE1_TOOL_NAMES, register_wave1_routes


class EchoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1)
    repeat: int = Field(default=1, ge=1, le=3)


class EchoResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    echoed: str
    repeat: int


def _echo_registry(
    on_call: Callable[[EchoRequest], None] | None = None,
) -> DualRouteRegistry:
    registry = DualRouteRegistry()

    @registry.register(
        name="echo_route",
        title="Echo a message",
        description="Repeat one message through the shared route handler.",
        rest_path="/api/echo",
        rest_method="GET",
        input_model=EchoRequest,
        output_model=EchoResponse,
        annotations=mt.ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def echo(request: EchoRequest) -> DualRouteResult[EchoResponse]:
        if on_call is not None:
            on_call(request)
        return DualRouteResult(
            text=f"Echoed {request.repeat} time(s).",
            payload=EchoResponse(
                echoed=request.message * request.repeat,
                repeat=request.repeat,
            ),
        )

    return registry


@pytest.mark.asyncio
async def test_registration_adds_mcp_discovery_and_call() -> None:
    mcp = build_server(_echo_registry())
    list_handler = mcp.request_handlers[mt.ListToolsRequest]
    listed = await list_handler(mt.ListToolsRequest(method="tools/list"))
    tool = next(tool for tool in listed.root.tools if tool.name == "echo_route")

    assert tool.inputSchema["properties"]["repeat"]["type"] == "integer"
    assert tool.outputSchema is not None
    assert tool.outputSchema["properties"]["echoed"]["type"] == "string"
    assert tool.annotations is not None
    assert tool.annotations.readOnlyHint is True

    call_handler = mcp.request_handlers[mt.CallToolRequest]
    called = await call_handler(
        mt.CallToolRequest(
            method="tools/call",
            params=mt.CallToolRequestParams(
                name="echo_route",
                arguments={"message": "hi", "repeat": 2},
            ),
        )
    )

    assert called.root.isError is False
    assert called.root.structuredContent == {"echoed": "hihi", "repeat": 2}
    assert isinstance(called.root.content[0], mt.TextContent)
    assert called.root.content[0].text == "Echoed 2 time(s)."
    assert isinstance(called.root.content[1], mt.TextContent)
    assert json.loads(called.root.content[1].text) == called.root.structuredContent


def test_registration_adds_rest_route_with_shared_coercion() -> None:
    calls: list[EchoRequest] = []
    client = TestClient(create_app(_echo_registry(calls.append)))

    response = client.get("/api/echo", params={"message": "ha", "repeat": "3"})

    assert response.status_code == 200
    assert response.json() == {"echoed": "hahaha", "repeat": 3}
    assert calls == [EchoRequest(message="ha", repeat=3)]


@pytest.mark.asyncio
async def test_invalid_input_never_reaches_shared_handler() -> None:
    calls: list[EchoRequest] = []
    registry = _echo_registry(calls.append)

    rest = TestClient(create_app(registry)).get(
        "/api/echo",
        params={"message": "hi", "repeat": "4"},
    )
    assert rest.status_code == 422
    assert rest.json()["error"] == "invalid_arguments"

    mcp = build_server(registry)
    call_handler = mcp.request_handlers[mt.CallToolRequest]
    called = await call_handler(
        mt.CallToolRequest(
            method="tools/call",
            params=mt.CallToolRequestParams(
                name="echo_route",
                arguments={"message": "hi", "repeat": 4},
            ),
        )
    )
    assert called.root.isError is True
    assert calls == []


def test_registration_rejects_duplicate_surface_keys() -> None:
    registry = _echo_registry()

    with pytest.raises(ValueError, match="duplicate MCP tool name"):
        registry.register(
            name="echo_route",
            title="Duplicate",
            description="Duplicate tool name.",
            rest_path="/api/other",
            rest_method="GET",
            input_model=EchoRequest,
            output_model=EchoResponse,
        )

    with pytest.raises(ValueError, match="duplicate REST route"):
        registry.register(
            name="other_route",
            title="Duplicate",
            description="Duplicate REST route.",
            rest_path="/api/echo",
            rest_method="GET",
            input_model=EchoRequest,
            output_model=EchoResponse,
        )


WAVE1_PATHS = {
    "list_public_servers": "/preview/list_public_eco_servers.json",
    "get_server_status": "/preview.json",
    "get_currency": "/preview/currency.json",
    "get_market": "/preview/market.json",
    "get_stores": "/preview/stores.json",
    "find_trade": "/preview/logistics.json",
    "get_civics": "/preview/civics.json",
    "get_progression": "/preview/progression.json",
    "get_world": "/preview/world.json",
}


def _wave1_registry(
    invoke: Callable[[str, dict[str, Any]], Awaitable[mt.CallToolResult]],
) -> DualRouteRegistry:
    registry = DualRouteRegistry()
    register_wave1_routes(registry, invoke)
    return registry


@pytest.mark.parametrize(("name", "path"), sorted(WAVE1_PATHS.items()))
@pytest.mark.asyncio
async def test_wave1_routes_share_success_payloads(name: str, path: str) -> None:
    async def invoke(tool_name: str, arguments: dict[str, Any]) -> mt.CallToolResult:
        if tool_name == "list_public_servers":
            payload: dict[str, Any] = {
                "servers": [{"label": "Test", "host": "eco.test:3001", "notes": "Fixture"}]
            }
        else:
            payload = {"tool": tool_name, "arguments": arguments}
        return mt.CallToolResult(
            content=[
                mt.TextContent(type="text", text=f"Called {tool_name}."),
                mt.TextContent(type="text", text=json.dumps(payload)),
            ],
            structuredContent=payload,
        )

    registry = _wave1_registry(invoke)
    # `arguments` is what the caller sends; `resolved` is what the input model
    # hands the tool once defaults are filled in. They differ wherever a route
    # has an optional field the request omits.
    arguments: dict[str, Any]
    resolved: dict[str, Any]
    if name == "list_public_servers":
        arguments = resolved = {}
    elif name == "get_progression":
        # Per-citizen timelines are opt-in so the summary layer fits inside an
        # MCP response (eco-app#232).
        # `citizen` is left unset, and both transports drop unset optionals.
        arguments = resolved = {"server": "eco.test:3001", "include_timelines": False}
    elif name in ("get_civics", "get_stores", "get_currency", "get_world"):
        # Detail arrays are bounded by default so a no-argument call stays
        # inside an MCP client's response cap (eco-app#256).
        arguments = resolved = {"server": "eco.test:3001", "limit": 50}
    else:
        arguments = resolved = {"server": "eco.test:3001"}
    expected = (
        {"servers": [{"label": "Test", "host": "eco.test:3001", "notes": "Fixture"}]}
        if name == "list_public_servers"
        else {"tool": name, "arguments": resolved}
    )

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
async def test_wave1_validation_failure_has_transport_parity() -> None:
    calls: list[str] = []

    async def invoke(name: str, arguments: dict[str, Any]) -> mt.CallToolResult:
        calls.append(name)
        return mt.CallToolResult(content=[])

    registry = _wave1_registry(invoke)
    rest = TestClient(create_app(registry)).get("/preview.json", params={"unknown": "value"})
    assert rest.status_code == 422
    assert rest.json()["error"] == "invalid_arguments"

    mcp = build_server(registry)
    call_handler = mcp.request_handlers[mt.CallToolRequest]
    called = await call_handler(
        mt.CallToolRequest(
            method="tools/call",
            params=mt.CallToolRequestParams(
                name="get_server_status",
                arguments={"unknown": "value"},
            ),
        )
    )
    assert called.root.isError is True
    assert calls == []


@pytest.mark.asyncio
async def test_wave1_downstream_failure_has_transport_parity() -> None:
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

    registry = _wave1_registry(invoke)
    rest = TestClient(create_app(registry)).get("/preview.json")
    assert rest.status_code == 502
    assert rest.json() == error_payload

    mcp = build_server(registry)
    call_handler = mcp.request_handlers[mt.CallToolRequest]
    called = await call_handler(
        mt.CallToolRequest(
            method="tools/call",
            params=mt.CallToolRequestParams(name="get_server_status", arguments={}),
        )
    )
    assert called.root.isError is True
    assert called.root.structuredContent == error_payload


@pytest.mark.asyncio
async def test_wave1_unexpected_failure_stays_public_safe() -> None:
    async def invoke(name: str, arguments: dict[str, Any]) -> mt.CallToolResult:
        raise RuntimeError("private downstream detail")

    registry = _wave1_registry(invoke)
    expected = {
        "error": "operation_failed",
        "message": "The operation could not be completed.",
    }

    rest = TestClient(create_app(registry)).get("/preview.json")
    assert rest.status_code == 500
    assert rest.json() == expected

    mcp = build_server(registry)
    call_handler = mcp.request_handlers[mt.CallToolRequest]
    called = await call_handler(
        mt.CallToolRequest(
            method="tools/call",
            params=mt.CallToolRequestParams(name="get_server_status", arguments={}),
        )
    )
    assert called.root.isError is True
    assert isinstance(called.root.content[1], mt.TextContent)
    assert json.loads(called.root.content[1].text) == expected


def test_wave1_inventory_matches_registered_names() -> None:
    assert set(WAVE1_PATHS) == WAVE1_TOOL_NAMES
