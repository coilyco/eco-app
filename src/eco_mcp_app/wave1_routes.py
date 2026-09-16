"""Typed dual registrations for the first public read-only route wave."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .dual_routes import DualRouteRegistry, DualRouteResult
from .public_routes import (
    CURATED_SERVERS_ANNOTATIONS,
    BoundedServerInput,
    CurrencyInput,
    EmptyInput,
    ServerInput,
    ToolInvoker,
    TradeInput,
    extract_result,
    register_json_route,
)


class ProgressionInput(ServerInput):
    """Select an Eco server, and how much per-citizen detail to return."""

    include_timelines: bool = Field(
        default=False,
        description=(
            "Return the full per-citizen event timelines. Off by default: they run to ~266 KB "
            "of a 275 KB response on an 80-citizen server and will exceed an MCP client's "
            "response cap, hiding the summary layer behind them. Use `citizen` for one "
            "person's timeline instead."
        ),
    )
    citizen: str | None = Field(
        default=None,
        description=(
            "Return only this citizen's timeline. Exact name match preferred, substring otherwise."
        ),
    )


class PublicEcoServer(BaseModel):
    """One curated public Eco server."""

    model_config = ConfigDict(extra="forbid")

    label: str
    host: str
    notes: str


class PublicServersOutput(BaseModel):
    """The curated public Eco server directory."""

    model_config = ConfigDict(extra="forbid")

    servers: list[PublicEcoServer]


PUBLIC_SERVERS_OUTPUT_SCHEMA: dict[str, Any] = PublicServersOutput.model_json_schema()

WAVE1_TOOL_NAMES = frozenset(
    {
        "list_public_servers",
        "get_server_status",
        "get_currency",
        "get_market",
        "get_stores",
        "find_trade",
        "get_civics",
        "get_progression",
        "get_world",
    }
)


def register_wave1_routes(registry: DualRouteRegistry, invoke: ToolInvoker) -> None:
    """Register Wave 1 once, preserving its established names and REST paths."""
    present = {name for name in WAVE1_TOOL_NAMES if registry.has_tool(name)}
    if present:
        if present == WAVE1_TOOL_NAMES:
            return
        names = ", ".join(sorted(present))
        raise ValueError(f"Wave 1 routes partially overlap existing tools: {names}")

    _register_public_servers(registry, invoke)
    register_json_route(
        registry,
        invoke,
        name="get_server_status",
        title="Eco - server status",
        description=(
            "Show a public Eco server's online players, meteor countdown, world "
            "statistics, economy, and version. Returns a readable summary and "
            "structured JSON. Omit server to use the configured default."
        ),
        rest_path="/preview.json",
        input_model=ServerInput,
    )
    register_json_route(
        registry,
        invoke,
        name="get_currency",
        title="Eco - currency and money supply",
        description=(
            "Show live Eco currencies, issuance, trade activity, money supply, "
            "and optional per-currency holder detail. Admin-backed data degrades "
            "to the public server headline when the server-side key is absent."
        ),
        rest_path="/preview/currency.json",
        input_model=CurrencyInput,
    )
    register_json_route(
        registry,
        invoke,
        name="get_market",
        title="Eco - market price intelligence",
        description=(
            "Build per-item market history, volume, and price trends from the "
            "Eco trade ledger. Optional item and currency filters narrow the "
            "report. Requires the server-side admin API key."
        ),
        rest_path="/preview/market.json",
        input_model=TradeInput,
    )
    register_json_route(
        registry,
        invoke,
        name="get_stores",
        title="Eco - store and trader directory",
        description=(
            "Build store and trader profiles from Eco trade history, including "
            "owners, items, volumes, counterparties, and recent activity. Requires "
            "the server-side admin API key."
        ),
        rest_path="/preview/stores.json",
        input_model=BoundedServerInput,
    )
    register_json_route(
        registry,
        invoke,
        name="find_trade",
        title="Eco - trade and store logistics",
        description=(
            "Turn trade history and live store shelves into resale, arbitrage, "
            "and supply-gap decisions. Optional item and currency filters narrow "
            "the report. Requires the server-side admin API key."
        ),
        rest_path="/preview/logistics.json",
        input_model=TradeInput,
    )
    register_json_route(
        registry,
        invoke,
        name="get_civics",
        title="Eco - civics and governance",
        description=(
            "Show election outcomes, turnout, demographic movement, settlements, "
            "and homesteads from Eco's civic history. Requires the server-side "
            "admin API key."
        ),
        rest_path="/preview/civics.json",
        input_model=BoundedServerInput,
    )
    register_json_route(
        registry,
        invoke,
        name="get_progression",
        title="Eco - progression and skills history",
        description=(
            "Reconstruct server-wide skill trajectories and progression trends "
            "from Eco action exports and daily series. Requires the server-side "
            "admin API key."
        ),
        rest_path="/preview/progression.json",
        input_model=ProgressionInput,
    )
    register_json_route(
        registry,
        invoke,
        name="get_world",
        title="Eco - world and industry activity",
        description=(
            "Reconstruct construction, terraforming, roads, pollution, garbage, "
            "and other world activity from Eco's action history. Requires the "
            "server-side admin API key."
        ),
        rest_path="/preview/world.json",
        input_model=BoundedServerInput,
    )


def _register_public_servers(registry: DualRouteRegistry, invoke: ToolInvoker) -> None:
    @registry.register(
        name="list_public_servers",
        title="Eco - list public servers",
        description=(
            "List the curated public Eco servers known to this service. Feed a "
            "returned host into get_server_status to fetch live status."
        ),
        rest_path="/preview/list_public_eco_servers.json",
        rest_method="GET",
        input_model=EmptyInput,
        output_model=PublicServersOutput,
        annotations=CURATED_SERVERS_ANNOTATIONS,
    )
    async def public_servers(request: EmptyInput) -> DualRouteResult[PublicServersOutput]:
        result = await invoke("list_public_servers", request.model_dump())
        text, payload, is_error = extract_result(result)
        return DualRouteResult(
            text=text,
            payload=PublicServersOutput.model_validate(payload),
            is_error=is_error,
            rest_status=502 if is_error else 200,
        )
