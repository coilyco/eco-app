"""Caveat and coverage keys lead every tool's JSON, ahead of the bulk arrays (COI-757).

A consumer that truncates keeps the head, so a `warnings` list at the tail was the first
thing lost: Echo told a member an item did not exist from rows whose partial-coverage
warning it never saw. Each tool here answers at its largest, with `limit=0` so nothing is
bounded away, and a warning that is appended after `to_dict` returns.
"""

from __future__ import annotations

import json
from typing import Any

import mcp.types as mt
import pytest

from eco_mcp_app import caveats
from eco_mcp_app import climate as climate_mod
from eco_mcp_app import currency as currency_mod
from eco_mcp_app import server as eco_server
from eco_mcp_app.civics import CivicsReport
from eco_mcp_app.crafting import CraftingAtlas
from eco_mcp_app.logistics import LogisticsReport
from eco_mcp_app.market import MarketIntelligence
from eco_mcp_app.progression import ProgressionHistory
from eco_mcp_app.social import SocialSurface
from eco_mcp_app.species import PopulationSample, SpeciesPayload
from eco_mcp_app.stores import StoreDirectory, StoreProfile, TraderProfile
from eco_mcp_app.trades import TradesLedger
from eco_mcp_app.world import WorldActivity

BULK = 400
ROWS = [[f"row-{i}", i] for i in range(BULK)]
DICT_ROWS = [{"i": i, "item": f"Item{i}", "pretty": f"Item {i}"} for i in range(BULK)]
SERIES = [(float(i), float(i)) for i in range(BULK)]
HEAD: dict[str, Any] = {"fetched_at_iso": "t", "source_base_url": "u"}


def _trades() -> TradesLedger:
    return TradesLedger(
        **HEAD,
        total_trades=BULK,
        trades=list(DICT_ROWS),
        by_item=[(f"Item{i}", 1, 1.0) for i in range(BULK)],
        by_currency=[(f"C{i}", 1.0) for i in range(BULK)],
        top_buyers=[(f"B{i}", 1.0) for i in range(BULK)],
        top_sellers=[(f"S{i}", 1.0) for i in range(BULK)],
        price_series={"Iron": [(float(i), 1.0) for i in range(BULK)]},
        warnings=["exporter row 12 skipped"],
    )


def _stores() -> StoreDirectory:
    return StoreDirectory(
        **HEAD,
        stores=[
            StoreProfile(f"k{i}", f"O{i}", f"{i}", "x", "Store", 1, 1.0, 1, 0, 1, 1.0)
            for i in range(BULK)
        ],
        traders=[TraderProfile(f"T{i}", f"{i}", 1, 1.0, 1.0, 0.0, 1, 1.0) for i in range(BULK)],
        total_stores=BULK,
        total_traders=BULK,
        warnings=["partial"],
    )


def _crafting() -> CraftingAtlas:
    return CraftingAtlas(
        **HEAD,
        total_events=BULK,
        by_crafted=[(f"I{i}", 1.0) for i in range(BULK)],
        by_gathered=[(f"G{i}", 1) for i in range(BULK)],
        by_station=[(f"S{i}", 1) for i in range(BULK)],
        by_citizen=[(f"C{i}", 1) for i in range(BULK)],
        by_citizen_iterations=[(f"C{i}", 1) for i in range(BULK)],
        flows=[(f"S{i}", f"I{i}", 1.0) for i in range(BULK)],
        warnings=["citizen names unavailable"],
    )


def _world() -> WorldActivity:
    return WorldActivity(
        **HEAD,
        total_events=BULK,
        by_citizen=[(f"C{i}", 1) for i in range(BULK)],
        by_polluter=[(f"P{i}", 1) for i in range(BULK)],
        by_object=[(f"O{i}", 1) for i in range(BULK)],
        hotspots=[(i, i, 1) for i in range(BULK)],
        warnings=["hotspots binned coarsely"],
    )


def _social() -> SocialSurface:
    return SocialSurface(
        **HEAD,
        play_by_day=[(i, 1) for i in range(BULK)],
        new_arrivals=list(DICT_ROWS),
        reputation_edges=list(DICT_ROWS),
        top_reputation_givers=[(f"G{i}", 1.0) for i in range(BULK)],
        top_reputation_receivers=[(f"R{i}", 1.0) for i in range(BULK)],
        warnings=["reputation giver column not recognised"],
    )


def _civics() -> CivicsReport:
    return CivicsReport(
        **HEAD,
        recent_elections=list(DICT_ROWS),
        recent_outcomes=list(DICT_ROWS),
        top_voters=[(f"V{i}", 1) for i in range(BULK)],
        unavailable_actions=["StartElection"],
        warnings=["one exporter refused"],
    )


def _progression() -> ProgressionHistory:
    return ProgressionHistory(
        **HEAD,
        citizens=[{"name": f"C{i}", "timeline": list(ROWS)} for i in range(BULK)],
        by_specialty=[(f"S{i}", 1) for i in range(BULK)],
        by_profession=[(f"P{i}", 1) for i in range(BULK)],
        warnings=["levels before the log began are unknown"],
    )


def _market() -> MarketIntelligence:
    return MarketIntelligence(**HEAD, markets=[], warnings=["no priced trades in window"])


def _logistics() -> LogisticsReport:
    return LogisticsReport(
        **HEAD,
        cheapest=list(DICT_ROWS),
        resale=list(DICT_ROWS),
        arbitrage=list(DICT_ROWS),
        supply_gaps=list(DICT_ROWS),
        market_summaries=list(DICT_ROWS),
        warnings=["stores offline"],
    )


# tool -> (where its fetcher lives, fetcher name, surface factory)
SURFACES: dict[str, tuple[Any, str, Any]] = {
    "get_trades": (eco_server, "fetch_ledger", _trades),
    "get_stores": (eco_server, "fetch_directory", _stores),
    "get_crafting_atlas": (eco_server, "fetch_atlas", _crafting),
    "get_world": (eco_server, "fetch_world", _world),
    "get_social": (eco_server, "fetch_social", _social),
    "get_civics": (eco_server, "fetch_civics", _civics),
    "get_progression": (eco_server, "fetch_history", _progression),
    "find_trade": (eco_server, "fetch_logistics", _logistics),
    "get_market": (eco_server.market_mod, "fetch_market", _market),
}


async def _call(tool: str, arguments: dict[str, Any]) -> list[mt.TextContent]:
    handler = eco_server.build_server().request_handlers[mt.CallToolRequest]
    result = await handler(
        mt.CallToolRequest(
            method="tools/call",
            params=mt.CallToolRequestParams(name=tool, arguments=arguments),
        )
    )
    return [b for b in result.root.content if isinstance(b, mt.TextContent)]


def _json_block(blocks: list[mt.TextContent]) -> dict[str, Any]:
    for block in blocks:
        try:
            payload = json.loads(block.text)
        except ValueError:
            continue
        if isinstance(payload, dict):
            return payload
    raise AssertionError("the response carried no JSON object block")


def assert_caveats_lead(payload: dict[str, Any]) -> None:
    """Every caveat key sits before the first non-caveat key holding a non-empty list or dict."""
    keys = list(payload)
    caveat = [i for i, k in enumerate(keys) if caveats.is_caveat(k)]
    bulk = [
        i
        for i, k in enumerate(keys)
        if not caveats.is_caveat(k) and isinstance(payload[k], list | dict) and payload[k]
    ]
    assert caveat, f"no caveat key in {keys}"
    assert bulk, f"no bulk key in {keys}, so the order proves nothing"
    assert max(caveat) < min(bulk), (
        f"caveat key {keys[max(caveat)]!r} sits after bulk key {keys[min(bulk)]!r}: {keys}"
    )


@pytest.mark.parametrize("tool", sorted(SURFACES))
@pytest.mark.asyncio
async def test_a_tools_largest_response_leads_with_its_caveats(
    monkeypatch: pytest.MonkeyPatch, tool: str
) -> None:
    holder, fetcher, factory = SURFACES[tool]

    async def _fetch(**_: Any) -> Any:
        return factory()

    monkeypatch.setattr(holder, fetcher, _fetch)
    if tool == "get_market":
        # market rows come from ItemMarket objects whose upstream 401s off the cluster
        monkeypatch.setattr(
            MarketIntelligence,
            "to_dict",
            lambda self: {
                "view": "market",
                "fetchedAtISO": "t",
                "sourceBaseUrl": "u",
                "totalTrades": 0,
                "markets": list(DICT_ROWS),
                "warnings": list(self.warnings),
            },
        )

    payload = _json_block(await _call(tool, {} if tool == "get_progression" else {"limit": 0}))

    assert_caveats_lead(payload)
    assert "warnings" in payload


@pytest.mark.asyncio
async def test_a_warning_appended_after_to_dict_still_leads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`limit` truncation and the item filter both write after `to_dict` returns."""

    async def _fetch(**_: Any) -> TradesLedger:
        return _trades()

    monkeypatch.setattr(eco_server, "fetch_ledger", _fetch)

    payload = _json_block(await _call("get_trades", {"limit": 5, "item": "Item3"}))

    assert_caveats_lead(payload)
    keys = list(payload)
    assert keys.index("itemFilter") < keys.index("trades")
    assert any(w.startswith("trades: showing") or "item filter" in w for w in payload["warnings"])


@pytest.mark.parametrize("tool", ["get_currency", "get_climate"])
@pytest.mark.asyncio
async def test_the_series_tools_lead_with_their_caveats(
    monkeypatch: pytest.MonkeyPatch, tool: str
) -> None:
    async def _info(_server: str | None = None) -> dict[str, Any]:
        return {"DaysRunning": 3}

    monkeypatch.setattr(eco_server, "fetch_eco_info", _info)
    if tool == "get_currency":
        snap = currency_mod.CurrencySnapshot(
            **HEAD,
            info={},
            days_elapsed=3,
            admin_ok=True,
            active_currencies_series=list(SERIES),
            trades_7d_series=list(SERIES),
            personal_wealth_series=list(SERIES),
            government_holdings_series=list(SERIES),
            warnings=["holders unreachable"],
        )

        async def _fetch(*_: Any, **__: Any) -> Any:
            return snap

        monkeypatch.setattr(currency_mod, "fetch_currency", _fetch)
    else:
        csnap = climate_mod.ClimateSnapshot(
            **HEAD,
            info={},
            days_elapsed=3,
            admin_ok=True,
            co2_series=list(SERIES),
            sea_level_series=list(SERIES),
            pollution_series=list(SERIES),
            temperature_series=list(SERIES),
            warnings=["temperature dataset missing"],
        )

        async def _cfetch(*_: Any, **__: Any) -> Any:
            return csnap

        monkeypatch.setattr(climate_mod, "fetch_climate", _cfetch)

    payload = _json_block(await _call(tool, {"limit": 0}))

    assert_caveats_lead(payload)


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [("get_recipes", {"limit": 3}), ("get_skills", {})],
)
@pytest.mark.asyncio
async def test_the_bundled_recipe_tools_lead_with_their_coverage(
    tool: str, arguments: dict[str, Any]
) -> None:
    """Offline and real: the largest list in the bundled recipe graph, no stub."""
    payload = _json_block(await _call(tool, arguments))

    assert_caveats_lead(payload)
    if tool == "get_recipes":
        keys = list(payload)
        assert keys.index("recipesMatched") < keys.index("recipes")
        assert keys.index("recipesReturned") < keys.index("recipes")


@pytest.mark.asyncio
async def test_get_species_leads_with_its_sampling_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _build(_species_id: str, **_: Any) -> SpeciesPayload:
        return SpeciesPayload(
            name="Fir",
            population=[PopulationSample(float(i), i) for i in range(BULK)],
            population_first=0,
            population_latest=BULK,
        )

    monkeypatch.setattr(eco_server.species_mod, "build_species_payload", _build)

    payload = _json_block(await _call("get_species", {"name": "fir"}))

    assert_caveats_lead(payload)
    keys = list(payload)
    assert keys.index("populationSampled") < keys.index("population")
    assert keys.index("warnings") < keys.index("population")


def test_caveats_first_keeps_every_other_key_where_it_was() -> None:
    payload = {"view": "x", "a": [1], "b": 2, "warnings": ["w"], "volumeNote": "n", "c": {"d": 1}}

    ordered = caveats.caveats_first(payload)

    assert list(ordered) == ["view", "warnings", "volumeNote", "a", "b", "c"]
    assert ordered == payload


def test_caveats_first_returns_the_same_object_when_already_ordered() -> None:
    payload = {"warnings": ["w"], "rows": [1]}

    assert caveats.caveats_first(payload) is payload
