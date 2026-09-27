"""The historical price norm rides every listed item price (teable:coilyco/eco-app#8368).

The fixtures under fixtures/price_payloads are live payloads cut to two rows per
list, with every player, store and location value redacted.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import mcp.types as mt
import pytest

from eco_mcp_app import market as market_mod
from eco_mcp_app import norms
from eco_mcp_app.server import build_server

FIXTURES = Path(__file__).parent / "fixtures" / "price_payloads"
LIVE = norms.LiveContext(stage="Modern 4", cycle=14, currency_names={"2533707": "Spectres"})
# Captured at import, before the autouse fixture in conftest pins it.
REAL_LIVE_CONTEXT = norms.live_context

# Each fixture and the route whose spec prices it.
ROUTES = {
    "get_trades": "get_trades",
    "get_market": "get_market",
    "get_stores": "get_stores",
    "find_trade": "find_trade",
    "price_recipe": "price_recipe",
    "get_recipes": "get_recipes",
    "item": "/preview/item.json",
    "price-history": "/preview/price-history.json",
}


def _s(n: int, median: float, p25: float, p75: float) -> dict[str, Any]:
    return {"n": n, "median": median, "p25": p25, "p75": p75}


def _tiny() -> norms.Norms:
    """Two cycles: 13 trades Sparks at basket 2.0, 14 trades Spectres at basket 1.0."""
    data = {
        "cycles": [
            {"cycle": 13, "primaryCurrency": "Sparks", "basketIndex": 2.0},
            {"cycle": 14, "primaryCurrency": "Spectres", "basketIndex": 1.0},
        ],
        "items": {
            "Lumber": {
                "cycles": {
                    "13": {
                        "byCurrency": {"Sparks": _s(20, 2.0, 1.8, 2.4)},
                        "stages": {"Modern 4": {"Sparks": _s(8, 1.6, 1.0, 2.0)}},
                    },
                    "14": {
                        "byCurrency": {"Spectres": _s(30, 1.0, 0.8, 1.2)},
                        "stages": {"Modern 4": {"Spectres": _s(3, 0.7, 0.7, 0.7)}},
                    },
                },
                "crossCycle": {
                    "Modern 4": {"median": 0.75, "cycles": 2, "n": 11},
                    "Basic 1": {"median": 1.5, "cycles": 1, "n": 2},
                },
            }
        },
    }
    return norms.Norms(data, {"LumberItem": "Lumber"})


def test_a_stage_with_enough_trades_is_the_basis_and_prices_the_multiple() -> None:
    n = _tiny().lookup("LumberItem", 1.5, "Spectres", LIVE)
    assert n["basis"] == "stage" and n["n"] == 11 and n["cycles"] == 2
    assert n["referencePrice"] == 0.75  # 0.75 x cycle 14's basket of 1.0
    assert n["multiple"] == 2.0
    # Cycle 14's Modern 4 bucket has 3 trades, so the in-currency figure is the whole cycle.
    assert (n["median"], n["currency"], n["cycle"], n["currencyN"]) == (1.0, "Spectres", 14, 30)


def test_a_thin_stage_falls_back_to_all_stages_and_says_so() -> None:
    ctx = norms.LiveContext(stage="Basic 1", cycle=14)
    n = _tiny().lookup("Lumber", None, None, ctx)
    assert n["basis"] == "all"
    assert n["fallback"] == "only 2 trades at Basic 1 in past cycles, using all stages"
    # All stages: median of (2.0 / 2.0, 1.0 / 1.0) over both cycles' primaries.
    assert (n["n"], n["cycles"], n["referencePrice"]) == (50, 2, 1.0)


def test_an_unnamed_currency_id_gets_no_multiple_and_no_in_currency_figure() -> None:
    ctx = norms.LiveContext(stage="Modern 4", cycle=14)
    n = _tiny().lookup("Lumber", 1.5, "2533707", ctx)
    assert "multiple" not in n and "median" not in n
    assert n["referencePrice"] == 0.75


def test_a_past_currency_gets_its_own_cycle_figure_but_no_multiple() -> None:
    n = _tiny().lookup("Lumber", 3.0, "Sparks", LIVE)
    assert "multiple" not in n
    assert (n["median"], n["cycle"], n["currencyN"]) == (1.6, 13, 8)


def test_no_history_and_another_server_still_carry_the_field() -> None:
    assert _tiny().lookup("MysteryItem", 1.0, "Spectres", LIVE) == {
        "basis": None,
        "n": 0,
        "fallback": "no trade history for this item",
    }
    away = norms.LiveContext(stage=None, cycle=None, home=False)
    assert _tiny().lookup("Lumber", 1.0, "Spectres", away)["n"] == 0


def test_a_cycle_the_file_has_not_seen_has_no_reference_price() -> None:
    ctx = norms.LiveContext(stage="Modern 4", cycle=15)
    tiny = _tiny()
    assert "referencePrice" not in tiny.lookup("Lumber", 1.0, "Spectres", ctx)
    assert any("cycle 15" in note for note in tiny.context(ctx)["notes"])


def test_an_eco_id_and_a_display_name_get_the_same_norm() -> None:
    real = norms.load()
    assert real is not None
    assert real.lookup("LumberItem", 1.0, "Spectres", LIVE) == real.lookup(
        "Lumber", 1.0, "Spectres", LIVE
    )
    assert real.key("ModernUpgradeLvl4Item") == "Modern Upgrade 4"
    assert real.stage_of("ScholarsBasicUpgradeLvl3Item") == 3


@pytest.mark.parametrize("fixture", sorted(ROUTES))
def test_every_listed_price_carries_a_norm(fixture: str) -> None:
    """The acceptance check: a priced object with no norm fails here."""
    raw = json.loads((FIXTURES / f"{fixture}.json").read_text())
    payload = copy.deepcopy(raw)
    assert norms.annotate(ROUTES[fixture], payload, LIVE)
    assert norms.unnormed(payload) == []
    assert "normContext" in payload
    blob = json.dumps(payload)
    assert blob.count('"norm"') >= 1
    # Negative control: the same payload unannotated is caught, where the check can see it.
    if fixture != "price-history":
        assert norms.unnormed(raw)


async def test_every_advertised_tool_is_placed_as_priced_or_not() -> None:
    server = build_server(disabled_tools=frozenset())
    listed = await server.request_handlers[mt.ListToolsRequest](
        mt.ListToolsRequest(method="tools/list")
    )
    names = {tool.name for tool in listed.root.tools}
    priced = {n for n in norms.PRICE_FIELDS if not n.startswith("/")}
    assert names - priced - norms.NO_PRICE_TOOLS == set(), "place each new tool in one list"
    assert priced & norms.NO_PRICE_TOOLS == set()


async def test_the_seam_adds_norms_to_a_real_tool_result(monkeypatch: pytest.MonkeyPatch) -> None:
    fixture: dict[str, Any] = json.loads((FIXTURES / "get_market.json").read_text())

    class Intel:
        def to_dict(self) -> dict[str, Any]:
            return copy.deepcopy(fixture)

    async def fake_fetch(**_: Any) -> Intel:
        return Intel()

    monkeypatch.setattr(market_mod, "fetch_market", fake_fetch)
    monkeypatch.setattr(market_mod, "market_markdown", lambda _: "markets")
    handler = build_server().request_handlers[mt.CallToolRequest]
    result = await handler(
        mt.CallToolRequest(
            method="tools/call", params=mt.CallToolRequestParams(name="get_market", arguments={})
        )
    )
    payload = json.loads(result.root.content[1].text)
    assert payload["normContext"]["stage"] == "Modern 4"
    assert all("norm" in row for row in payload["markets"])
    assert norms.unnormed(payload) == []


async def test_an_unreadable_ledger_is_an_unknown_stage_not_an_early_world() -> None:
    async def info(_: str | None) -> dict[str, Any]:
        return {"Description": "<color=green>Eco</color> | Cycle 14 | High Collab"}

    async def empty(_: str | None) -> list[str]:
        return []

    async def upgraded(_: str | None) -> list[str]:
        return ["LumberItem", "ModernUpgradeLvl4Item", "BasicUpgradeLvl1Item"]

    async def names(_: str | None) -> dict[str, str]:
        return {"2533707": "Spectres"}

    norms._live_cache.clear()
    blind = await REAL_LIVE_CONTEXT(
        None, fetch_info=info, fetch_items=empty, fetch_currency_names=names
    )
    assert (blind.stage, blind.stage_known, blind.cycle) == (None, False, 14)
    assert _tiny().lookup("Lumber", None, None, blind)["fallback"] == (
        "the live stage is unknown, using all stages"
    )
    norms._live_cache.clear()
    live = await REAL_LIVE_CONTEXT(
        None, fetch_info=info, fetch_items=upgraded, fetch_currency_names=names
    )
    assert (live.stage, live.stage_known) == ("Modern 4", True)
    norms._live_cache.clear()
