"""Typed dual registrations for the second public read-only route wave."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .dual_routes import DualRouteRegistry
from .public_routes import (
    BoundedServerInput,
    ServerInput,
    ToolInvoker,
    TradesInput,
    register_json_route,
)


class SpeciesInput(BaseModel):
    """Select one Eco species by id or common name."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        description=("Species id or common name, such as WheatSpecies, Wheat, or Snapping Turtle.")
    )
    include_image: bool = Field(
        default=False,
        description=(
            "Inline the species photo as a base64 data URI. Off by default: the image runs "
            "to ~285 KB and will exceed an MCP client's response cap on its own. `photoUrl` "
            "is always returned, so fetch that instead unless you need the bytes inline."
        ),
    )
    limit: int = Field(
        default=120,
        ge=0,
        description=(
            "Maximum population samples to return. The raw curve runs to ~219 KB - far more "
            "than the image this tool already gates - so it is thinned to evenly-spaced "
            "samples with the endpoints preserved. populationFirst / populationLatest / "
            "populationDelta always describe the whole series. 0 means every sample."
        ),
    )


class ExplainItemInput(BaseModel):
    """Select one item and optional knowledge-graph category."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        description="Item name to look up, such as Iron, Oak, Bison, Wheat, or Quartz."
    )
    category: Literal["material", "plant", "animal", "mineral", "food"] | None = Field(
        default=None,
        description="Optional category used to disambiguate the item name.",
    )
    include_image: bool = Field(
        default=False,
        description=(
            "Inline the Wikimedia image as a base64 data URI. Off by default: the image runs "
            "to ~100 KB around a three-sentence description and will exceed an MCP client's "
            "response cap. `image_url` is always returned, so fetch that instead."
        ),
    )


class PriceByStageInput(BaseModel):
    """Name one item to price across upgrade stages."""

    model_config = ConfigDict(extra="forbid")

    item: str = Field(
        description=(
            "One Eco item as the player said it: a display name, an Eco id, a plural, "
            'or a bare metal word, such as "Hewn Log", IronBarItem, "hewn logs", or iron. '
            "Nothing is fuzzy-matched, so an unknown word returns no item rather than a guess."
        )
    )
    stage: str | None = Field(
        default=None,
        description=(
            "An upgrade stage to lead the answer with: shorthand such as au3, SBU4 or MU0, "
            'or a ladder name such as "Advanced 2". Shorthand inside item works too, as in '
            '"iron at au3".'
        ),
    )
    server: str | None = Field(
        default=None,
        description="Eco server whose live cycle sets the currency. Omit for the default.",
    )


WAVE2_PATHS = {
    "get_milestones": "/preview/get_milestones.json",
    "get_species": "/preview/get_species.json",
    "explain_item": "/preview/explain_item.json",
    "get_crafting_atlas": "/preview/get_crafting_atlas.json",
    "get_trades": "/preview/get_trades.json",
    "price_by_stage": "/preview/price_by_stage.json",
    "get_region": "/preview/get_region.json",
    "get_climate": "/preview/get_climate.json",
    "get_government": "/preview/get_government.json",
}
WAVE2_TOOL_NAMES = frozenset(WAVE2_PATHS)


def register_wave2_routes(registry: DualRouteRegistry, invoke: ToolInvoker) -> None:
    """Register the remaining straightforward read-only public operations."""
    present = {name for name in WAVE2_TOOL_NAMES if registry.has_tool(name)}
    if present:
        if present == WAVE2_TOOL_NAMES:
            return
        names = ", ".join(sorted(present))
        raise ValueError(f"Wave 2 routes partially overlap existing tools: {names}")

    register_json_route(
        registry,
        invoke,
        name="get_milestones",
        title="Eco - milestone tracker",
        description=(
            "Show the server's total culture score and how close it is to each "
            "server-wide culture achievement, for a public Eco server. Any question about "
            "culture points or culture goals lands here."
        ),
        rest_path=WAVE2_PATHS["get_milestones"],
        input_model=ServerInput,
    )
    register_json_route(
        registry,
        invoke,
        name="get_species",
        title="Eco - species profile",
        description=(
            "Profile one animal or plant species, such as elk, salmon, or cedar: its "
            "real-world taxonomy and imagery, plus its population on this server over "
            "time. Answers 'what is an elk', 'how many elk are left', and 'is the elk "
            "population falling'. For the "
            "whole world's biomes and every species at once use get_region."
        ),
        rest_path=WAVE2_PATHS["get_species"],
        input_model=SpeciesInput,
    )
    register_json_route(
        registry,
        invoke,
        name="explain_item",
        title="Eco - explain item",
        description=(
            "Explain what an Eco item is in real life: looks it up on Wikidata and "
            "Wikipedia and returns its image, a short description, and category-specific "
            "facts. Answers 'what is basalt in the real world'. Not for upgrade-module "
            "shorthand such as au3, bu5, sbu4, or mu0, which are items price_by_stage "
            "prices. For how to make it in game use get_recipes, and for an animal or "
            "plant use get_species."
        ),
        rest_path=WAVE2_PATHS["explain_item"],
        input_model=ExplainItemInput,
    )
    register_json_route(
        registry,
        invoke,
        name="get_crafting_atlas",
        title="Eco - crafting activity atlas",
        description=(
            "Show who has been producing the most and what: the busiest players by "
            "crafting, harvesting, hunting, tree chopping, and mining actions, the most "
            "crafted and gathered items, and the busiest crafting stations. Answers 'who is "
            "the busiest miner' and 'what gets crafted most'. byMiner ranks players by dig "
            "and mine events alone, so read it for the busiest miner, while byCitizen and "
            "byCitizenIterations total every action type and are not mining counts. For "
            "what a station can make use get_recipes. Requires the "
            "server-side admin API key."
        ),
        rest_path=WAVE2_PATHS["get_crafting_atlas"],
        input_model=BoundedServerInput,
    )
    register_json_route(
        registry,
        invoke,
        name="get_trades",
        title="Eco - trades ledger",
        description=(
            "List individual past trades: who bought what from whom, at which store, for "
            "how much, and when, plus top buyers and sellers per currency. Answers 'list "
            "today's copper ore trades' and 'who bought my iron'. Pass item to get only "
            "that item's trades, matched across the whole ledger before limit applies. For "
            "which players or stores trade the most overall use get_stores, for where to "
            "buy or sell an item now use find_trade, and for price trends get_market. "
            "Requires the server-side admin API key."
        ),
        rest_path=WAVE2_PATHS["get_trades"],
        input_model=TradesInput,
    )
    register_json_route(
        registry,
        invoke,
        name="price_by_stage",
        title="Eco - median price per upgrade stage",
        description=(
            "Answer what an Eco item is worth and what to pay or charge for it, upgrade "
            "modules included (Basic Upgrade 4, au3, sbu4, mining bu5): its median trade price "
            "at each upgrade stage (none, Basic 1 to Modern 4) across past cycles, with the "
            "trade count behind each, in the live cycle's currency, and marked estimates where "
            "trades are thin. A shorthand upgrade token by itself names the item to price: "
            "au3 is Advanced Upgrade 3, sbu4 is Scholars Basic Upgrade 4, and mu0 means there "
            "is no Modern upgrade. Answers 'how much for an au3', 'price check AU 3', 'how "
            "much should I sell X for', 'what should I pay for X', 'how much should I buy X "
            "for', 'what's a good price for X', 'what is X worth', and 'what does X go for at "
            "au3'. An item it cannot match returns no price. For which store to buy from or "
            "sell to right now use find_trade."
        ),
        rest_path=WAVE2_PATHS["price_by_stage"],
        input_model=PriceByStageInput,
    )
    register_json_route(
        registry,
        invoke,
        name="get_region",
        title="Eco - biodiversity and ecoregion match",
        description=(
            "Show what biomes the world is made of, which real-world WWF ecoregion it "
            "most resembles, and how every species' population is drifting. Answers "
            "'how much of the world is desert or forest'. For one species use get_species."
        ),
        rest_path=WAVE2_PATHS["get_region"],
        input_model=ServerInput,
    )
    register_json_route(
        registry,
        invoke,
        name="get_climate",
        title="Eco - climate and pollution",
        description=(
            "Show the world's climate and pollution levels now: atmospheric state and CO2, "
            "sea-level evidence, ground pollution, real-world CO2 context, and available "
            "pollution attribution by source. For which players built, dumped, or "
            "polluted use get_world."
        ),
        rest_path=WAVE2_PATHS["get_climate"],
        input_model=BoundedServerInput,
    )
    register_json_route(
        registry,
        invoke,
        name="get_government",
        title="Eco - government org chart",
        description=(
            "Show elected titles, active elections, and active laws for an Eco server's "
            "current civic state."
        ),
        rest_path=WAVE2_PATHS["get_government"],
        input_model=ServerInput,
    )
