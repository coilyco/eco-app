"""Tests for the trade & store logistics engine (eco-app#51).

Covers:
  - `build_logistics` boards over normalized `ShelfOffer` lists (pure, no HTTP):
    cheapest-source ranking, best-resale ranking, arbitrage spread detection
    with the twin threshold + market-depth gates, and the supply-gap classifier
    (no-supply / thin-supply / over-priced).
  - Honest empty and single-store paths → "not enough market depth", never a
    fabricated spread.
  - `parse_live_stores` folding the `/api/v1/stores` DTO (null-tolerant, barter
    skipped) and live offers winning over history in `_merge`.
  - `fetch_logistics` folding the trades ledger (respx-mocked exporter CSVs) and
    a best-effort live shelf; the live-absent (404) path degrading to history.
  - Tool wiring: `find_trade` registered, returns text blocks and no widget
    (just-data per eco-app#87), and the `/preview/logistics.json` data plane.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import mcp.types as mt
import pytest
import respx

from eco_mcp_app import trades as trades_mod
from eco_mcp_app.logistics import (
    ARBITRAGE_MIN_ABS_SPREAD,
    ShelfOffer,
    _merge,
    build_logistics,
    fetch_logistics,
    logistics_markdown,
    logistics_template_context,
    parse_live_stores,
)
from eco_mcp_app.server import build_server


def _offer(
    store: str,
    item: str,
    side: str,
    price: float,
    *,
    currency: str = "Credit",
    quantity: float = 100.0,
    source: str = "history",
    owner: str = "",
) -> ShelfOffer:
    return ShelfOffer(
        store_key=store,
        store_label=store,
        owner=owner,
        item=item,
        item_display=item,
        currency=currency,
        side=side,
        price=price,
        quantity=quantity,
        source=source,
    )


@pytest.fixture(autouse=True)
def _clear_cache() -> Iterator[None]:
    trades_mod._trades_cache.clear()
    yield
    trades_mod._trades_cache.clear()


# ---------------------------------------------------------------------------
# Cheapest source / best resale
# ---------------------------------------------------------------------------


def test_cheapest_source_ranks_sellers_ascending() -> None:
    offers = [
        _offer("StoreA", "IronIngotItem", "sell", 3.10),
        _offer("StoreB", "IronIngotItem", "sell", 2.50),
        _offer("StoreC", "IronIngotItem", "sell", 4.00),
    ]
    report = build_logistics(offers)
    assert len(report.cheapest) == 1
    row = report.cheapest[0]
    assert row["item"] == "IronIngotItem"
    assert row["cheapest"] == pytest.approx(2.50)
    assert row["sellerCount"] == 3
    # Offers are cheapest-first, so the cheapest store leads.
    assert row["offers"][0]["store"] == "StoreB"
    assert [o["price"] for o in row["offers"]] == [2.50, 3.10, 4.00]


def test_cheapest_skips_zero_stock_offer_at_equal_price() -> None:
    """COI-2045: the Lumber case. A 0-stock shelf at 0.7 Barter sat first in the
    cheapest pick although a store with 185 in stock sold at the same price."""
    offers = [
        _offer(
            "RATWORKS",
            "LumberItem",
            "sell",
            0.7,
            currency="Barter",
            quantity=0,
            source="live",
            owner="TheBarracksRat",
        ),
        _offer(
            "Lumberaholics",
            "LumberItem",
            "sell",
            0.7,
            currency="Spectres",
            quantity=185,
            source="live",
            owner="Scuba Steve",
        ),
    ]
    report = build_logistics(offers)
    first = report.cheapest[0]
    assert first["currency"] == "Spectres"
    assert first["offers"][0]["store"] == "Lumberaholics"
    assert first["offers"][0]["quantity"] > 0
    # The sold-out market is still reported, after the buyable one, with null.
    sold_out = report.cheapest[1]
    assert sold_out["currency"] == "Barter"
    assert sold_out["cheapest"] is None
    assert sold_out["offers"] == []


def test_cheapest_in_one_market_never_picks_zero_stock_even_when_cheaper() -> None:
    offers = [
        _offer("Empty", "LumberItem", "sell", 0.1, quantity=0, source="live"),
        _offer("Stocked", "LumberItem", "sell", 0.9, quantity=12, source="live"),
    ]
    row = build_logistics(offers).cheapest[0]
    assert row["cheapest"] == pytest.approx(0.9)
    assert [o["store"] for o in row["offers"]] == ["Stocked"]
    assert row["soldOutCount"] == 1
    assert row["sellerCount"] == 1


def test_all_zero_stock_reports_null_and_says_so() -> None:
    offers = [
        _offer("A", "LumberItem", "sell", 0.7, quantity=0, source="live"),
        _offer("B", "LumberItem", "sell", 0.8, quantity=0, source="live"),
    ]
    report = build_logistics(offers)
    assert len(report.cheapest) == 1
    row = report.cheapest[0]
    assert row["cheapest"] is None  # null, never 0
    assert row["offers"] == []
    assert row["sellerCount"] == 0
    assert row["soldOutCount"] == 2
    assert "out of stock" in row["soldOutNote"]
    assert [o["store"] for o in row["soldOutOffers"]] == ["A", "B"]
    summary = report.market_summaries[0]
    assert summary["cheapestSell"] is None
    assert summary["supplyQty"] == 0


def test_arbitrage_never_buys_from_a_zero_stock_shelf() -> None:
    offers = [
        _offer("Empty", "IronIngotItem", "sell", 1.0, quantity=0, source="live"),
        _offer("Stocked", "IronIngotItem", "sell", 2.0, quantity=50, source="live"),
        _offer("Buyer", "IronIngotItem", "buy", 5.0, quantity=50, source="live"),
    ]
    report = build_logistics(offers)
    assert [a["buyFrom"]["store"] for a in report.arbitrage] == ["Stocked"]


def test_best_resale_ranks_buyers_descending() -> None:
    offers = [
        _offer("StoreA", "BoardItem", "buy", 0.55),
        _offer("StoreB", "BoardItem", "buy", 0.80),
    ]
    report = build_logistics(offers)
    assert len(report.resale) == 1
    row = report.resale[0]
    assert row["best"] == pytest.approx(0.80)
    assert row["offers"][0]["store"] == "StoreB"


def test_barter_and_zero_prices_skipped() -> None:
    offers = [
        _offer("StoreA", "CornItem", "sell", 0.0, currency=""),  # barter shelf
        _offer("StoreB", "CornItem", "sell", 1.5),
    ]
    report = build_logistics(offers)
    assert report.total_offers == 1
    assert report.cheapest[0]["cheapest"] == pytest.approx(1.5)


def test_market_summaries_preserve_stock_demand_and_provenance() -> None:
    report = build_logistics(
        [
            _offer("StoreA", "CornItem", "sell", 1.5, quantity=40, source="live"),
            _offer("StoreB", "CornItem", "buy", 2.0, quantity=10, source="live"),
        ]
    )

    assert report.market_summaries == [
        {
            "item": "CornItem",
            "itemPretty": "Corn",
            "currency": "Credit",
            "sellerCount": 1,
            "buyerCount": 1,
            "supplyQty": 40,
            "demandQty": 10,
            "cheapestSell": 1.5,
            "bestBuy": 2.0,
            "sources": ["live"],
        }
    ]


# ---------------------------------------------------------------------------
# Arbitrage spread detection + depth honesty
# ---------------------------------------------------------------------------


def test_arbitrage_detects_cross_store_spread() -> None:
    # Buy iron at StoreA for 2.0, sell it to StoreB for 3.0 → +1.0 (50%) spread.
    offers = [
        _offer("StoreA", "IronIngotItem", "sell", 2.0, quantity=200),
        _offer("StoreB", "IronIngotItem", "buy", 3.0, quantity=120),
    ]
    report = build_logistics(offers)
    assert len(report.arbitrage) == 1
    a = report.arbitrage[0]
    assert a["buyFrom"]["store"] == "StoreA"
    assert a["sellTo"]["store"] == "StoreB"
    assert a["spread"] == pytest.approx(1.0)
    assert a["spreadPct"] == pytest.approx(50.0)
    assert a["volume"] == pytest.approx(120)  # min of the two quantities
    assert a["storeCount"] == 2


def test_arbitrage_below_threshold_is_dropped() -> None:
    # 5% spread — under the 15% percent gate, so no arbitrage.
    offers = [
        _offer("StoreA", "IronIngotItem", "sell", 2.00),
        _offer("StoreB", "IronIngotItem", "buy", 2.10),
    ]
    report = build_logistics(offers)
    assert report.arbitrage == []


def test_arbitrage_absolute_gate_blocks_cheap_items() -> None:
    # 40% spread but only +0.20 absolute — under the 0.5 absolute gate.
    offers = [
        _offer("StoreA", "CornItem", "sell", 0.50),
        _offer("StoreB", "CornItem", "buy", 0.70),
    ]
    assert 0.70 - 0.50 < ARBITRAGE_MIN_ABS_SPREAD
    report = build_logistics(offers)
    assert report.arbitrage == []


def test_arbitrage_same_store_is_not_a_spread() -> None:
    # A single store quoting a buy under its own sell is not arbitrage.
    offers = [
        _offer("StoreA", "IronIngotItem", "sell", 3.0),
        _offer("StoreA", "IronIngotItem", "buy", 2.0),
    ]
    report = build_logistics(offers)
    assert report.arbitrage == []


def test_single_store_reports_not_enough_depth() -> None:
    offers = [
        _offer("StoreA", "IronIngotItem", "sell", 3.0),
        _offer("StoreA", "IronIngotItem", "buy", 2.0),
    ]
    report = build_logistics(offers)
    assert report.arbitrage == []
    # Cheapest-source still shows even with one store...
    assert report.cheapest
    # ...but the honest depth warning fires.
    assert any("not enough market depth" in w for w in report.warnings)


def test_empty_reports_nothing_to_route() -> None:
    report = build_logistics([])
    assert report.total_offers == 0
    assert report.cheapest == report.resale == report.arbitrage == report.supply_gaps == []
    assert any("nothing to route" in w for w in report.warnings)


# ---------------------------------------------------------------------------
# Supply gap classifier
# ---------------------------------------------------------------------------


def test_supply_gap_no_supply() -> None:
    # Demand (a buy order) with zero sellers → no-supply gap.
    offers = [_offer("StoreA", "NailItem", "buy", 1.0, quantity=500)]
    report = build_logistics(offers)
    assert len(report.supply_gaps) == 1
    g = report.supply_gaps[0]
    assert g["reason"] == "no_supply"
    assert g["buyerCount"] == 1
    assert g["sellerCount"] == 0
    assert g["demandQty"] == pytest.approx(500)


def test_supply_gap_names_who_needs_it() -> None:
    # A supply gap folds its buy-side orders into per-citizen demand rows,
    # biggest want first, so the SPA can answer "who needs it" (eco-app#77).
    offers = [
        _offer("StoreA", "NailItem", "buy", 1.0, quantity=100, owner="geodude"),
        _offer("StoreB", "NailItem", "buy", 1.2, quantity=250, owner="onix"),
        _offer("StoreC", "NailItem", "buy", 0.9, quantity=50, owner="onix"),
    ]
    report = build_logistics(offers)
    g = report.supply_gaps[0]
    buyers = g["buyers"]
    # onix folds across two stores (250 + 50 = 300) and outranks geodude (100).
    assert [b["owner"] for b in buyers] == ["onix", "geodude"]
    assert buyers[0]["quantity"] == pytest.approx(300)
    assert buyers[0]["price"] == pytest.approx(1.2)  # best (highest) price onix offers
    assert buyers[1]["quantity"] == pytest.approx(100)


def test_supply_gap_thin_supply() -> None:
    # Demand with a single monopolist seller → thin-supply gap (seed max = 1).
    offers = [
        _offer("StoreA", "NailItem", "buy", 1.0, quantity=500),
        _offer("StoreB", "NailItem", "sell", 2.0, quantity=50),
    ]
    report = build_logistics(offers)
    assert any(g["reason"] == "thin_supply" for g in report.supply_gaps)


def test_supply_gap_overpriced_vs_median() -> None:
    # Well-supplied (two sellers) but cheapest sits 50% over the in-game median.
    offers = [
        _offer("StoreA", "IronIngotItem", "sell", 3.0),
        _offer("StoreB", "IronIngotItem", "sell", 3.2),
    ]
    medians = {("IronIngotItem", "Credit"): 2.0}
    report = build_logistics(offers, medians=medians)
    gaps = [g for g in report.supply_gaps if g["reason"] == "overpriced"]
    assert len(gaps) == 1
    assert gaps[0]["overMedianPct"] == pytest.approx(50.0)
    assert gaps[0]["median"] == pytest.approx(2.0)


def test_supply_gap_board_surfaces_twenty_plus(monkeypatch: pytest.MonkeyPatch) -> None:
    # Kai wants the gap board in the 20+ range (eco-app#95): with enough
    # demanded-but-unsupplied items, the board must not clip to the old ~8.
    offers = [_offer("StoreA", f"Item{i:02d}", "buy", 1.0, quantity=100) for i in range(25)]
    report = build_logistics(offers)
    assert len(report.supply_gaps) == 25
    assert all(g["reason"] == "no_supply" for g in report.supply_gaps)


def test_healthy_market_has_no_gap() -> None:
    offers = [
        _offer("StoreA", "IronIngotItem", "sell", 2.0),
        _offer("StoreB", "IronIngotItem", "sell", 2.1),
        _offer("StoreC", "IronIngotItem", "buy", 1.9),
    ]
    medians = {("IronIngotItem", "Credit"): 2.0}
    report = build_logistics(offers, medians=medians)
    assert report.supply_gaps == []


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


def test_item_filter_normalizes() -> None:
    offers = [
        _offer("StoreA", "IronIngotItem", "sell", 3.0),
        _offer("StoreB", "WheatItem", "sell", 1.0),
    ]
    report = build_logistics(offers, item="Iron")
    assert len(report.cheapest) == 1
    assert report.cheapest[0]["item"] == "IronIngotItem"


def test_currency_filter() -> None:
    offers = [
        _offer("StoreA", "IronIngotItem", "sell", 3.0, currency="Credit"),
        _offer("StoreB", "IronIngotItem", "sell", 30.0, currency="Gold"),
    ]
    report = build_logistics(offers, currency="gold")
    assert len(report.cheapest) == 1
    assert report.cheapest[0]["currency"] == "Gold"


# ---------------------------------------------------------------------------
# Live shelf parsing + merge precedence
# ---------------------------------------------------------------------------


def _live_offer(
    item: str, type_name: str, buying: bool, price: float, quantity: float
) -> dict[str, Any]:
    return {
        "item": item,
        "itemTypeName": type_name,
        "buying": buying,
        "price": price,
        "quantity": quantity,
    }


_LIVE_STORES: list[dict[str, Any]] = [
    {
        "name": "Redwood Lumber Depot",
        "owner": "redwood",
        "currency": "Sirens Credit",
        "location": {"x": 412, "y": 68, "z": 1180},
        "offers": [
            _live_offer("Lumber", "LumberItem", False, 1.25, 480),
            _live_offer("Log", "LogItem", True, 0.35, 640),
        ],
    },
    {
        "name": "Roadside Barter Stall",
        "owner": None,  # unowned store is legal
        "currency": None,  # currency-less store
        "location": None,  # orphaned store
        "offers": [
            _live_offer("Corn", "CornItem", False, 0.0, 64),  # barter / free shelf
        ],
    },
]


def test_parse_live_stores_tolerates_nulls_and_skips_barter() -> None:
    offers = parse_live_stores(_LIVE_STORES)
    # Corn is a 0-price barter shelf → skipped; Lumber (sell) + Log (buy) kept.
    assert len(offers) == 2
    by_item = {o.item: o for o in offers}
    assert by_item["LumberItem"].side == "sell"
    assert by_item["LumberItem"].source == "live"
    assert by_item["LogItem"].side == "buy"
    assert by_item["LumberItem"].currency == "Sirens Credit"
    assert by_item["LumberItem"].owner == "redwood"


def test_merge_live_overrides_history() -> None:
    hist = [_offer("live:S|o", "IronIngotItem", "sell", 5.0, source="history")]
    live = [
        ShelfOffer(
            store_key="live:S|o",
            store_label="S",
            owner="o",
            item="IronIngotItem",
            item_display="Iron Ingot",
            currency="Credit",
            side="sell",
            price=3.0,
            quantity=10,
            source="live",
        )
    ]
    merged = _merge(hist, live)
    assert len(merged) == 1
    assert merged[0].source == "live"
    assert merged[0].price == pytest.approx(3.0)


# ---------------------------------------------------------------------------
# fetch_logistics via respx (folds the trades ledger + best-effort live shelf)
# ---------------------------------------------------------------------------

BASE = "http://eco.example.com:3001"
CURRENCY_URL = f"{BASE}/api/v1/exporter/actions?actionName=CurrencyTrade"
BARTER_URL = f"{BASE}/api/v1/exporter/actions?actionName=BarterTrade"
CITIZENS_URL = f"{BASE}/api/v1/citizens"
STORES_URL = f"{BASE}/api/v1/stores"

_CITIZENS_JSON = [{"id": 1, "name": "salt"}, {"id": 2, "name": "alice"}, {"id": 3, "name": "bob"}]

# Store 1 (salt) SELLS iron ingot to alice, then bob buys iron ore FROM store 1
# (store buys ore, i.e. store 1 is the buyer). Two distinct shelves, one store.
_CURRENCY_CSV = (
    "BankAccount,Currency,CurrencyAmount,NumberOfItems,BoughtOrSold,ShopOwner,"
    "Buyer,Seller,WorldObjectItem,ItemUsed,Citizen,ActionLocation,Count,Time\n"
    # store 1 sells IronIngot: owner=1, seller=1, buyer=2 → sell side, 3.0/unit
    '"a",Credit,30.0,10,33,1,2,1,StoreItem,IronIngotItem,1,"9,9,9",1,172800\n'
    # store 1 buys IronOre: owner=1, buyer=1, seller=3 → buy side, 0.9/unit
    '"a",Credit,9.0,10,32,1,1,3,StoreItem,IronOreItem,1,"9,9,9",1,172800\n'
)
_BARTER_EMPTY = "Buyer,Seller,ItemUsed,NumberOfItems,Count,Time\n"
_CURRENCY_ROLLUP = '"old",Credit,900.0,90,33,1,2,1,StoreItem,FakeItem,1,"9,9,9",4,400000\n'


@pytest.mark.asyncio
@respx.mock
async def test_fetch_logistics_history_only_when_live_absent() -> None:
    respx.get(CURRENCY_URL).mock(return_value=httpx.Response(200, text=_CURRENCY_CSV))
    respx.get(BARTER_URL).mock(return_value=httpx.Response(200, text=_BARTER_EMPTY))
    respx.get(CITIZENS_URL).mock(return_value=httpx.Response(200, json=_CITIZENS_JSON))
    respx.get(STORES_URL).mock(return_value=httpx.Response(404))  # exporter not deployed

    report = await fetch_logistics(base_url=BASE, api_key="k")
    assert report.live is False
    # Two history-derived shelf lines: iron ingot sell, iron ore buy.
    assert report.total_offers == 2
    assert report.total_stores == 1
    sell = {r["item"]: r for r in report.cheapest}
    assert sell["IronIngotItem"]["cheapest"] == pytest.approx(3.0)
    assert sell["IronIngotItem"]["offers"][0]["source"] == "history"
    assert sell["IronIngotItem"]["offers"][0]["store"] == "salt's Store"
    resale = {r["item"]: r for r in report.resale}
    assert resale["IronOreItem"]["best"] == pytest.approx(0.9)
    # Single store → no arbitrage, honest depth warning.
    assert report.arbitrage == []
    assert any("not enough market depth" in w for w in report.warnings)


@pytest.mark.asyncio
@respx.mock
async def test_fetch_logistics_excludes_rollup_history_quote() -> None:
    respx.get(CURRENCY_URL).mock(
        return_value=httpx.Response(200, text=_CURRENCY_CSV + _CURRENCY_ROLLUP)
    )
    respx.get(BARTER_URL).mock(return_value=httpx.Response(200, text=_BARTER_EMPTY))
    respx.get(CITIZENS_URL).mock(return_value=httpx.Response(200, json=_CITIZENS_JSON))
    respx.get(STORES_URL).mock(return_value=httpx.Response(404))

    report = await fetch_logistics(base_url=BASE, api_key="k")

    assert all(row["item"] != "FakeItem" for row in report.cheapest + report.resale)
    assert any("detailed rows only" in w for w in report.warnings)


@pytest.mark.asyncio
@respx.mock
async def test_fetch_logistics_live_shelf_sharpens_and_adds_depth() -> None:
    respx.get(CURRENCY_URL).mock(return_value=httpx.Response(200, text=_CURRENCY_CSV))
    respx.get(BARTER_URL).mock(return_value=httpx.Response(200, text=_BARTER_EMPTY))
    respx.get(CITIZENS_URL).mock(return_value=httpx.Response(200, json=_CITIZENS_JSON))
    # A second live store sells iron ore cheap and another buys iron ingot dear,
    # opening cross-store spreads history alone couldn't see.
    live = [
        {
            "name": "Cheap Ore Yard",
            "owner": "miner",
            "currency": "Credit",
            "location": {"x": 1, "y": 1, "z": 1},
            "offers": [
                _live_offer("Iron Ore", "IronOreItem", False, 0.30, 400),
                _live_offer("Iron Ingot", "IronIngotItem", True, 5.0, 50),
            ],
        }
    ]
    respx.get(STORES_URL).mock(return_value=httpx.Response(200, json=live))

    report = await fetch_logistics(base_url=BASE, api_key="k")
    assert report.live is True
    assert report.total_stores == 2
    # Iron ingot: buy from salt's history sell (3.0) → sell to the live buyer (5.0).
    iron = [a for a in report.arbitrage if a["item"] == "IronIngotItem"]
    assert len(iron) == 1
    assert iron[0]["spread"] == pytest.approx(2.0)
    assert iron[0]["sellTo"]["source"] == "live"


# ---------------------------------------------------------------------------
# Rendering / empty states
# ---------------------------------------------------------------------------


def test_logistics_markdown_and_context_empty() -> None:
    report = build_logistics([])
    report.source_base_url = "eco.example:3001"
    md = logistics_markdown(report)
    assert "no shelf offers" in md.lower()
    ctx = logistics_template_context(report)
    assert ctx["empty"] is True


def test_logistics_markdown_populated() -> None:
    offers = [
        _offer("StoreA", "IronIngotItem", "sell", 2.0, quantity=200),
        _offer("StoreB", "IronIngotItem", "buy", 3.0, quantity=100),
    ]
    report = build_logistics(offers)
    md = logistics_markdown(report)
    # The "Cheapest source" board was dropped as noise (eco-app#95); resale and
    # arbitrage remain.
    assert "Cheapest source" not in md
    assert "Best resale" in md
    assert "Arbitrage" in md
    ctx = logistics_template_context(report)
    assert ctx["empty"] is False
    assert ctx["arbitrage"]
    assert "cheapest" not in ctx


# ---------------------------------------------------------------------------
# Tool wiring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_trade_registered() -> None:
    mcp = build_server()
    handler = mcp.request_handlers[mt.ListToolsRequest]
    result = await handler(mt.ListToolsRequest(method="tools/list"))
    names = {t.name for t in result.root.tools}
    assert "find_trade" in names


@pytest.mark.asyncio
@respx.mock
async def test_find_trade_tool_returns_blocks_and_fragment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ECO_ADMIN_API_KEY", "k")
    respx.get(CURRENCY_URL).mock(return_value=httpx.Response(200, text=_CURRENCY_CSV))
    respx.get(BARTER_URL).mock(return_value=httpx.Response(200, text=_BARTER_EMPTY))
    respx.get(CITIZENS_URL).mock(return_value=httpx.Response(200, json=_CITIZENS_JSON))
    respx.get(STORES_URL).mock(return_value=httpx.Response(404))

    mcp = build_server()
    handler = mcp.request_handlers[mt.CallToolRequest]
    req = mt.CallToolRequest(
        method="tools/call",
        params=mt.CallToolRequestParams(
            name="find_trade", arguments={"server": "eco.example.com:3001"}
        ),
    )
    result = await handler(req)
    blocks = result.root.content
    assert len(blocks) == 2
    assert "Trade logistics" in blocks[0].text
    import json as _json

    payload = _json.loads(blocks[1].text)
    assert payload["view"] == "logistics"
    assert payload["cheapest"][0]["item"] == "IronIngotItem"
    # Just-data per eco-app#87: find_trade no longer emits a widget.
    assert result.root.meta is None


@respx.mock
def test_preview_logistics_json_route(monkeypatch: pytest.MonkeyPatch) -> None:
    """The dedicated `/preview/logistics.json` data-plane route serves the boards."""
    from fastapi.testclient import TestClient

    from eco_mcp_app.http_app import create_app

    trades_mod._trades_cache.clear()
    monkeypatch.setenv("ECO_ADMIN_API_KEY", "k")
    respx.get(CURRENCY_URL).mock(return_value=httpx.Response(200, text=_CURRENCY_CSV))
    respx.get(BARTER_URL).mock(return_value=httpx.Response(200, text=_BARTER_EMPTY))
    respx.get(CITIZENS_URL).mock(return_value=httpx.Response(200, json=_CITIZENS_JSON))
    respx.get(STORES_URL).mock(return_value=httpx.Response(404))

    client = TestClient(create_app())
    r = client.get("/preview/logistics.json?server=eco.example.com:3001")
    assert r.status_code == 200
    payload = r.json()
    assert payload["view"] == "logistics"
    assert payload["cheapest"][0]["item"] == "IronIngotItem"


# ---------------------------------------------------------------------------
# The item filter meets human phrasing (eco-app#247)
# ---------------------------------------------------------------------------


def _plank_offer(side: str = "sell", price: float = 1.0) -> ShelfOffer:
    return ShelfOffer(
        store_key="live:Scuba Steve's Store|scuba",
        store_label="Scuba Steve's Store",
        owner="scuba",
        item="WoodenHullPlanksItem",
        item_display="Wooden Hull Planks",
        currency="Spectres",
        side=side,
        price=price,
        quantity=913.0,
        source="live",
        last_day=None,
    )


@pytest.mark.parametrize(
    "query",
    [
        "Wooden Hull Planks",  # exactly the itemPretty this API emits
        "Wooden Hull Plank",  # the singular a person types
        "wooden hull planks",
        "WoodenHullPlanksItem",  # the internal key
        "woodenhull",  # a fragment
    ],
)
def test_every_human_phrasing_finds_the_item(query: str) -> None:
    """Internal keys have no spaces, so every spaced query returned 0 (#247).

    That zero was indistinguishable from "nothing for sale", and a Discord
    agent relayed it to a member as fact while 913 planks sat on one shelf.
    """
    report = build_logistics([_plank_offer()], item=query)
    assert report.total_offers == 1, f"{query!r} matched nothing"


def test_an_unrelated_query_still_matches_nothing() -> None:
    report = build_logistics([_plank_offer()], item="Iron Ingot")
    assert report.total_offers == 0


def test_an_unmatched_filter_says_lookup_miss_not_empty_market() -> None:
    """ "No offers exist" and "your filter matched nothing" are different facts."""
    report = build_logistics([_plank_offer()], item="Nonexistent Widget")
    assert report.total_offers == 0
    warning = " ".join(report.warnings)
    assert "not recognized" in warning
    assert "not an empty market" in warning
    # The old text asserted something about the world, not about the query.
    assert "nothing to route" not in warning


def test_a_genuinely_empty_market_still_says_so() -> None:
    report = build_logistics([], item="Wooden Hull Planks")
    assert report.total_offers == 0
    assert "nothing to route" in " ".join(report.warnings)


def test_an_unmatched_currency_is_also_a_filter_miss() -> None:
    report = build_logistics([_plank_offer()], currency="Racines")
    assert report.total_offers == 0
    warning = " ".join(report.warnings)
    assert "filter miss" in warning
    assert "nothing to route" not in warning
