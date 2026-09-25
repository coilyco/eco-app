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
            "Show a public Eco server as it is right now: whether it is up, who is "
            "online, how many days the current cycle has run, the meteor countdown, "
            "world statistics, economy headline, and game version. It reports live "
            "state only. It holds no wipe, reset, restart, or patch schedule, and "
            "knows nothing about a player's own game install or computer, so "
            "crashes, kicks, disconnects, and launch problems are outside it and "
            "outside every other tool here. For how active the community has been "
            "over recent days use get_social. Omit server to use the configured "
            "default."
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
            "Show the currencies on this server and who holds them. Currency names "
            "are invented by players and can be any word, often a plural noun like "
            "Credits, Crowns, or Shells, so 'who holds the most "
            "X' or 'who is richest in X', where X is not an item, animal, or "
            "reputation, is a currency question and belongs here. Reputation is "
            "get_social's. Covers which "
            "currencies exist and who founded them, which are minted or backed and "
            "by how much, trade activity per currency, total money supply, and each "
            "currency's top holders. Pass currency to get one currency's holders. Questions about "
            "exchanging or converting "
            "one currency into another start here, with which currencies are "
            "backed and actively traded. For stores pricing in one currency use "
            "find_trade with its currency filter. "
            "Admin-backed data degrades to the public server headline when the "
            "server-side key is absent."
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
            "Show how an item's price has moved over time: daily median, low, and "
            "high price, trade volume, and whether the price is rising, falling, "
            "or flat, per item and currency, from past trades. Answers 'is X "
            "getting cheaper' and 'which items change hands most by volume'. "
            "Optional item and currency filters narrow the report. For whether a "
            "given price is fair use fair_price, for where to buy or sell an item "
            "right now use find_trade, and for individual trades use get_trades. "
            "Requires the server-side admin API key."
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
            "Profile the stores and traders on this server: who owns each store, "
            "what it trades, its volume, its customers, and when it last traded, "
            "plus what each player buys and sells as a trader. Answers 'who runs "
            "the biggest store', 'what does this shop trade', and 'which player "
            "moves the most goods'. It has no item filter, so for where to buy or sell "
            "a specific item use find_trade. Requires the server-side admin API "
            "key."
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
            "Answer where to buy or sell an item on this server: its current "
            "asking prices, the cheapest store selling it, the store paying the most for it, "
            "whether anyone "
            "is buying it, buy-low-sell-high spreads (which store to buy from, which store to "
            "resell to, and the profit per unit), and items "
            "with buyers but no sellers. That supply-gap board is the answer to "
            "what a store should carry to earn money. Reads live store shelves, falling back to "
            "recent "
            "trade prices. Pass item for one item, or currency for offers priced "
            "in that currency. For price trends use get_market, for individual "
            "past trades get_trades, and for who owns which store get_stores. "
            "Requires the server-side admin API key."
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
            "Show the civic history of this server: past election outcomes and "
            "turnout, population movement, and settlements, including how many "
            "settlements and homesteads have been started. For the laws and elected titles in "
            "force right now use "
            "get_government, and for who owns one particular plot or deed use "
            "get_map. "
            "Requires the server-side admin API key."
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
            "Show which professions and specialties players have gained and "
            "when, including whether anyone on the server holds a given "
            "specialty yet (a tailor, a mason, a cook), plus level-ups, class "
            "completions, leaderboards, and per-day trends in how fast the server "
            "is moving through the skill tree. Pass citizen for one player's "
            "history. For the fixed list of skills and how many recipes each "
            "unlocks use get_skills. Requires the server-side admin API key."
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
            "Show what players have done to the world and who did it: "
            "construction, terraforming, road building, polluting actions, "
            "garbage dumping, and other world activity from the action history. "
            "For current pollution levels in the air, water, and ground use "
            "get_climate. Requires the server-side admin API key."
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
