"""Typed dual registrations for the second public read-only route wave."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .dual_routes import DualRouteRegistry
from .public_routes import (
    BoundedServerInput,
    ServerInput,
    ToolInvoker,
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


class MapInput(BoundedServerInput):
    """Select an Eco server, and choose whether to include render geometry."""

    include_geometry: bool = Field(
        default=False,
        description=(
            "Include the SVG polygon geometry and per-owner colour map. Off by default: "
            "the `points` coordinate strings run to ~30 KB that no text consumer can "
            "interpret. `deeds` always carries each deed's owner, centroid, bounding box "
            "and approximate area, which is what a question about land actually needs."
        ),
    )


class FairPriceInput(ServerInput):
    """Select an Eco item and optional calibration context."""

    item: str = Field(
        description=(
            "Eco item name, including Copper, CopperIngot, Wheat, Board, Lumber, "
            "Iron, IronIngot, Oil, or Crude."
        )
    )
    cycle_id: str | None = Field(
        default=None,
        description="Optional cycle identifier used for stored in-game price calibration.",
    )


WAVE2_PATHS = {
    "get_economy": "/preview/get_economy.json",
    "get_map": "/preview/get_map.json",
    "get_milestones": "/preview/get_milestones.json",
    "get_species": "/preview/get_species.json",
    "explain_item": "/preview/explain_item.json",
    "get_crafting_atlas": "/preview/get_crafting_atlas.json",
    "get_trades": "/preview/get_trades.json",
    "fair_price": "/preview/fair_price.json",
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
        name="get_economy",
        title="Eco - economic health dashboard",
        description=(
            "Show live economic vitals for an Eco server, including trades, contracts, "
            "loans, wages, tax flow, and volatile-series trends. Answers whether the "
            "economy as a whole is healthy. For culture score and culture achievements "
            "use get_milestones, and for one currency use get_currency. A KPI is null "
            "when its dataset could not be read and zero only when the server reported "
            "no activity; `datasets_unavailable` names every dataset behind a null."
        ),
        rest_path=WAVE2_PATHS["get_economy"],
        input_model=ServerInput,
    )
    register_json_route(
        registry,
        invoke,
        name="get_map",
        title="Eco - world map and property deeds",
        description=(
            "Return the live Eco world preview and property deeds. Each deed carries its "
            "owner, centroid, bounding box and approximate area in world blocks. SVG "
            "polygon geometry is opt-in via `include_geometry`. The richer browser-only "
            "biome raster projection remains separate."
        ),
        rest_path=WAVE2_PATHS["get_map"],
        input_model=MapInput,
    )
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
            "facts. Answers 'what is basalt in the real world'. For how to make it in game "
            "use get_recipes, and for an animal or plant use get_species."
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
            "the busiest miner' and 'what gets crafted most'. For what a station can "
            "make use get_recipes. Requires the "
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
            "today's copper ore trades' and 'who bought my iron'. For which "
            "players or stores trade the most overall use get_stores, for where to buy "
            "or sell an item now use find_trade, and for price trends get_market. "
            "Requires the server-side admin API key."
        ),
        rest_path=WAVE2_PATHS["get_trades"],
        input_model=BoundedServerInput,
    )
    register_json_route(
        registry,
        invoke,
        name="fair_price",
        title="Eco - fair-price advisor",
        description=(
            "Judge what a fair price for an Eco item is: what it has actually sold for on "
            "this server beside an advisory real-world commodity benchmark, with optional "
            "cycle calibration. Answers 'am I overcharging for X' and 'what should I "
            "charge for X'. For how its price has moved over time use get_market."
        ),
        rest_path=WAVE2_PATHS["fair_price"],
        input_model=FairPriceInput,
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
