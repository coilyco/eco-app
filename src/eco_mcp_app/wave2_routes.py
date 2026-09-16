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
            "loans, wages, tax flow, culture, and volatile-series trends. A KPI is null "
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
            "Show progress toward server-wide culture achievements and total culture "
            "for a public Eco server."
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
            "Show real-world taxonomy and imagery plus live in-server population history "
            "for one Eco species."
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
            "Look up an Eco item on Wikidata and Wikipedia and return its image, short "
            "description, and category-specific facts."
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
            "Reconstruct bounded crafting, harvesting, and mining activity from Eco's "
            "action exporter. Requires the server-side admin API key."
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
            "Return the detailed Eco trade ledger and its buyer, seller, currency, item, "
            "and price-history aggregates. Requires the server-side admin API key."
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
            "Compare an Eco item's in-game market evidence with an advisory real-world "
            "commodity benchmark and optional cycle calibration."
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
            "Classify the world's biome composition against WWF ecoregions and report "
            "per-species population drift."
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
            "Show atmospheric state, sea-level evidence, ground pollution, real-world "
            "CO2 context, and available Eco pollution attribution."
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
