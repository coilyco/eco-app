"""Every list-valued payload key is accounted for.

#267 records that "limit must bound every unbounded array" has not converged
across three rounds of fixing the next demonstrated instance. The recurrence
mode is an array added to a payload with nobody deciding whether it needs a
bound. A prose audit cannot catch that; this can.

The inventory below records what each surface serialises today. It carries no
opinion about whether a given array *should* be bounded — that call belongs to
#267 and is deliberately absent, so this file never asserts a judgement it did
not earn. What it does assert is that the set is complete: add an array and
this test fails until someone writes the row, which is the moment to decide.
"""

from __future__ import annotations

import importlib
from typing import Any

# surface class -> module, plus any constructor arguments beyond the two every
# surface shares. Derived from each surface's own to_dict, never hand-listed.
_SURFACES: dict[str, tuple[str, dict[str, Any]]] = {
    # byMiner is null until the DigOrMine fetch is recorded, so the probe records one.
    "CraftingAtlas": ("crafting", {"per_action_counts": {"DigOrMine": 0}}),
    "TradesLedger": ("trades", {}),
    "SocialSurface": ("social", {}),
    "CivicsReport": ("civics", {}),
    "StoreDirectory": ("stores", {}),
    "CurrencySnapshot": ("currency", {"info": {}, "days_elapsed": 1, "admin_ok": True}),
    "WorldActivity": ("world", {}),
}

_INVENTORY: dict[str, list[str]] = {
    "CraftingAtlas": [
        "byCitizen",
        "byCitizenIterations",
        "byCrafted",
        "byGathered",
        "byMiner",
        "byStation",
        "flows",
    ],
    "TradesLedger": ["byCurrency", "byItem", "topBuyers", "topSellers", "trades"],
    "SocialSurface": [
        "cycles",
        "firstLoginsByDay",
        "newArrivals",
        "playByDay",
        "reputationColumnsSeen",
        "reputationEdges",
        "topReputationGivers",
        "topReputationReceivers",
    ],
    "CivicsReport": [
        "recentDemographics",
        "recentElections",
        "recentOutcomes",
        "recentSettlements",
        "topVoters",
        "unavailableActions",
    ],
    "StoreDirectory": ["stores", "traders"],
    "CurrencySnapshot": [
        "activeCurrenciesSeries",
        "availableCurrencyDatasets",
        "currencies",
        "governmentHoldingsSeries",
        "personalWealthSeries",
        "trades7dSeries",
    ],
    "WorldActivity": [
        "byCitizen",
        # One group per category (at most seven), each holding a players list that
        # grows with the player count and that _bound_nested_rows bounds (COI-2048).
        "byCitizenByCategory",
        "byObject",
        "byPolluter",
        "categories",
        "categoryKeys",
        "hotspots",
        "timeline",
    ],
}


def _serialised_arrays(cls_name: str) -> list[str]:
    module, extra = _SURFACES[cls_name]
    surface = getattr(importlib.import_module(f"eco_mcp_app.{module}"), cls_name)
    obj = surface(fetched_at_iso="t", source_base_url="u", **extra)
    return sorted(
        key for key, value in obj.to_dict().items() if isinstance(value, list) and key != "warnings"
    )


def test_every_serialised_array_is_in_the_inventory() -> None:
    for cls_name in _SURFACES:
        derived = _serialised_arrays(cls_name)
        recorded = sorted(_INVENTORY[cls_name])
        added = sorted(set(derived) - set(recorded))
        removed = sorted(set(recorded) - set(derived))
        assert not added, (
            f"{cls_name} serialises {added}, which no row records. Add it to "
            "_INVENTORY, and decide whether it grows with world size and so "
            "needs limit to bound it (eco-app#267)."
        )
        assert not removed, (
            f"{cls_name} no longer serialises {removed}. Drop the row so the "
            "inventory keeps describing the payload."
        )


def test_the_inventory_covers_every_serialisable_surface() -> None:
    """A new surface with its own to_dict must be inventoried too."""
    assert set(_SURFACES) == set(_INVENTORY)
