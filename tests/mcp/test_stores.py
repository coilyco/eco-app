"""Tests for the store & trader directory aggregator + tool wiring (eco-app#50).

Covers:
  - Folding a CurrencyTrade CSV into store profiles keyed by (location, owner),
    with the owner resolved to a citizen name (`Citizen #<id>` fallback).
  - Buy-vs-sell mix read off the party columns (owner-as-seller = a store sale,
    owner-as-buyer = a store purchase), not the undecoded BoughtOrSold enum.
  - Trader profiles: per-citizen buy / sell volume, top items, unique
    counterparties, and the stores each citizen operates.
  - Unknown-id and empty-history paths degrade gracefully.
  - The shared `trades.fetch_parsed_trades` spine + `/api/v1/citizens` join.
  - Tool wiring returns two TextContent blocks and no widget (just-data per eco-app#87), and the
    tool is advertised in tools/list.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx
import mcp.types as mt
import pytest
import respx

from eco_mcp_app import stores as stores_mod
from eco_mcp_app.server import build_server
from eco_mcp_app.stores import (
    StoreDirectory,
    build_directory,
    directory_markdown,
    directory_template_context,
    fetch_directory,
)
from eco_mcp_app.trades import ParsedTradeFetch, _ParsedTrade, fetch_parsed_trades

CURRENCY_URL = "http://eco.example.com:3001/api/v1/exporter/actions?actionName=CurrencyTrade"
BARTER_URL = "http://eco.example.com:3001/api/v1/exporter/actions?actionName=BarterTrade"
CITIZENS_URL = "http://eco.example.com:3001/api/v1/citizens"
BASE = "http://eco.example.com:3001"

_CITIZENS_JSON = [
    {"id": 129312, "name": "coilysiren"},
    {"id": 130409, "name": "ekans"},
    {"id": 129580, "name": "redwood"},
]

# Column order matches cycle-13: BankAccount, Currency, CurrencyAmount,
# NumberOfItems, BoughtOrSold, ShopOwner, Buyer, Seller, WorldObjectItem,
# ItemUsed, Citizen, ActionLocation, Count, Time.
#
# Rows (owner = ShopOwner):
#   1. ekans store, ekans sells IronIngot to coilysiren  (250, day~3.5)
#   2. ekans store, ekans sells IronIngot to redwood     (140, day~2.3)
#   3. coilysiren store, coilysiren buys Wheat from 129569 (225, day~1.2)
_CURRENCY_CSV = (
    "BankAccount,Currency,CurrencyAmount,NumberOfItems,BoughtOrSold,ShopOwner,"
    "Buyer,Seller,WorldObjectItem,ItemUsed,Citizen,ActionLocation,Count,Time\n"
    '"ekans account",Credit,250.0,10,33,130409,129312,130409,StoreItem,'
    'IronIngotItem,129312,"1,2,3",1,300000\n'
    '"ekans account",Credit,140.0,5,33,130409,129580,130409,StoreItem,'
    'IronIngotItem,129580,"1,2,3",1,200000\n'
    '"coilysiren account",Credit,225.0,45,32,129312,129312,129569,StoreItem,'
    'WheatItem,129569,"4,5,6",1,100000\n'
)

_BARTER_EMPTY = "Buyer,Seller,ItemUsed,NumberOfItems,Count,Time\n"

_CURRENCY_ROLLUP = (
    '"old account",Credit,900.0,90,33,130409,129312,130409,StoreItem,'
    'BogusRepresentativeItem,129312,"1,2,3",4,400000\n'
)


@pytest.fixture(autouse=True)
def _clear_cache() -> Iterator[None]:
    stores_mod._clear_cache()
    yield
    stores_mod._clear_cache()


@pytest.mark.asyncio
@respx.mock
async def test_build_directory_store_profiles() -> None:
    respx.get(CURRENCY_URL).mock(return_value=httpx.Response(200, text=_CURRENCY_CSV))
    respx.get(BARTER_URL).mock(return_value=httpx.Response(200, text=_BARTER_EMPTY))
    respx.get(CITIZENS_URL).mock(return_value=httpx.Response(200, json=_CITIZENS_JSON))

    fetch = await fetch_parsed_trades(base_url=BASE, api_key="secret")
    directory = build_directory(fetch)

    assert directory.total_trades == 3
    # Two distinct stores: ekans @ "1,2,3" and coilysiren @ "4,5,6".
    assert directory.total_stores == 2
    by_owner = {s.owner: s for s in directory.stores}
    ekans = by_owner["ekans"]
    # ekans's store sold twice (owner == seller both rows), for 390 total.
    assert ekans.trade_count == 2
    assert ekans.sell_count == 2
    assert ekans.buy_count == 0
    assert ekans.total_volume == pytest.approx(390.0)
    assert ekans.store_object == "StoreItem"
    assert ekans.location == "1,2,3"
    # Two distinct buyers -> two counterparties.
    assert ekans.unique_counterparties == 2
    # Top item is IronIngot with a mean unit price ((25 + 28) / 2 folded per-item).
    assert ekans.top_items[0]["item"] == "IronIngotItem"
    assert ekans.top_items[0]["pretty"] == "Iron Ingot"
    assert ekans.top_items[0]["tradeCount"] == 2

    # coilysiren's store bought once (owner == buyer), counterparty is unmapped.
    coil = by_owner["coilysiren"]
    assert coil.buy_count == 1
    assert coil.sell_count == 0
    assert coil.top_counterparties == ["Citizen #129569"]

    # Stores rank by value: ekans (390) ahead of coilysiren (225).
    assert directory.stores[0].owner == "ekans"


@pytest.mark.asyncio
@respx.mock
async def test_build_directory_trader_profiles() -> None:
    respx.get(CURRENCY_URL).mock(return_value=httpx.Response(200, text=_CURRENCY_CSV))
    respx.get(BARTER_URL).mock(return_value=httpx.Response(200, text=_BARTER_EMPTY))
    respx.get(CITIZENS_URL).mock(return_value=httpx.Response(200, json=_CITIZENS_JSON))

    fetch = await fetch_parsed_trades(base_url=BASE, api_key="secret")
    directory = build_directory(fetch)

    traders = {t.name: t for t in directory.traders}
    # ekans sold 390 across two trades and operates one store.
    assert traders["ekans"].sell_volume == pytest.approx(390.0)
    assert traders["ekans"].buy_volume == pytest.approx(0.0)
    assert len(traders["ekans"].stores_operated) == 1
    assert traders["ekans"].stores_operated[0]["location"] == "1,2,3"
    # coilysiren bought iron (250) and — as its own store — bought wheat (225).
    assert traders["coilysiren"].buy_volume == pytest.approx(475.0)
    assert len(traders["coilysiren"].stores_operated) == 1
    # The unmapped seller on row 3 falls back rather than dropping.
    assert "Citizen #129569" in traders
    assert traders["Citizen #129569"].sell_volume == pytest.approx(225.0)


@pytest.mark.asyncio
@respx.mock
async def test_build_directory_excludes_rollup_party_and_store_attribution() -> None:
    respx.get(CURRENCY_URL).mock(
        return_value=httpx.Response(200, text=_CURRENCY_CSV + _CURRENCY_ROLLUP)
    )
    respx.get(BARTER_URL).mock(return_value=httpx.Response(200, text=_BARTER_EMPTY))
    respx.get(CITIZENS_URL).mock(return_value=httpx.Response(200, json=_CITIZENS_JSON))

    directory = build_directory(await fetch_parsed_trades(base_url=BASE, api_key="secret"))

    # The total keeps Count's four merged events, while the false representative
    # item and parties never inflate an individual store/trader profile.
    assert directory.total_trades == 7
    ekans = {s.owner: s for s in directory.stores}["ekans"]
    assert ekans.trade_count == 2
    assert ekans.total_volume == pytest.approx(390.0)
    assert all("BogusRepresentativeItem" not in str(s.top_items) for s in directory.stores)
    assert any("detailed rows only" in w for w in directory.warnings)


@pytest.mark.asyncio
@respx.mock
async def test_fetch_directory_end_to_end_and_cache() -> None:
    route = respx.get(CURRENCY_URL).mock(return_value=httpx.Response(200, text=_CURRENCY_CSV))
    respx.get(BARTER_URL).mock(return_value=httpx.Response(200, text=_BARTER_EMPTY))
    respx.get(CITIZENS_URL).mock(return_value=httpx.Response(200, json=_CITIZENS_JSON))

    d1 = await fetch_directory(base_url=BASE, api_key="k", cache_ttl_s=60)
    d2 = await fetch_directory(base_url=BASE, api_key="k", cache_ttl_s=60)
    assert d1.total_stores == d2.total_stores == 2
    # Second call served from cache — one upstream hit.
    assert route.call_count == 1
    # to_dict is JSON-ready camelCase.
    payload = d1.to_dict()
    assert payload["view"] == "eco_stores"
    assert payload["stores"][0]["owner"] in {"ekans", "coilysiren"}
    assert "label" in payload["stores"][0]


@pytest.mark.asyncio
@respx.mock
async def test_fetch_directory_empty_is_clean() -> None:
    empty_csv = "BankAccount,Currency,ShopOwner,Buyer,Seller,Time\n"
    respx.get(CURRENCY_URL).mock(return_value=httpx.Response(200, text=empty_csv))
    respx.get(BARTER_URL).mock(return_value=httpx.Response(200, text=_BARTER_EMPTY))

    directory = await fetch_directory(base_url=BASE, api_key=None, cache_ttl_s=0)
    assert directory.total_stores == 0
    assert directory.total_traders == 0
    assert directory.stores == []
    ctx = directory_template_context(directory)
    assert ctx["empty"] is True
    assert "no trade history" in directory_markdown(directory).lower()


@pytest.mark.asyncio
@respx.mock
async def test_fetch_directory_unknown_ids_fall_back() -> None:
    # No citizens endpoint mocked -> id->name map empty -> Citizen #<id> labels.
    respx.get(CURRENCY_URL).mock(return_value=httpx.Response(200, text=_CURRENCY_CSV))
    respx.get(BARTER_URL).mock(return_value=httpx.Response(200, text=_BARTER_EMPTY))
    respx.get(CITIZENS_URL).mock(return_value=httpx.Response(404))

    directory = await fetch_directory(base_url=BASE, api_key="k", cache_ttl_s=0)
    owners = {s.owner for s in directory.stores}
    assert owners == {"Citizen #130409", "Citizen #129312"}
    assert any("citizens" in w for w in directory.warnings)


@pytest.mark.asyncio
@respx.mock
async def test_fetch_directory_tolerates_partial_failure() -> None:
    respx.get(CURRENCY_URL).mock(return_value=httpx.Response(200, text=_CURRENCY_CSV))
    respx.get(BARTER_URL).mock(return_value=httpx.Response(401))
    respx.get(CITIZENS_URL).mock(return_value=httpx.Response(200, json=_CITIZENS_JSON))

    directory = await fetch_directory(base_url=BASE, api_key=None, cache_ttl_s=0)
    assert directory.total_trades == 3  # currency rows still folded
    assert any("BarterTrade" in w and "401" in w for w in directory.warnings)


def test_directory_markdown_surfaces_owner() -> None:
    # A store's markdown line names the owner — the DiscordLink gap this closes.
    from eco_mcp_app.stores import StoreDirectory, StoreProfile

    directory = StoreDirectory(fetched_at_iso="t", source_base_url="b")
    directory.total_trades = 2
    directory.total_stores = 1
    directory.stores = [
        StoreProfile(
            store_key="1,2,3|130409",
            owner="ekans",
            owner_id="130409",
            location="1,2,3",
            store_object="StoreItem",
            trade_count=2,
            total_volume=390.0,
            sell_count=2,
            buy_count=0,
            unique_counterparties=2,
            last_day=3.5,
            top_items=[{"item": "IronIngotItem", "pretty": "Iron Ingot"}],
        )
    ]
    md = directory_markdown(directory)
    assert "ekans" in md
    assert "Iron Ingot" in md


@pytest.mark.asyncio
@respx.mock
async def test_tool_call_returns_text_blocks_and_fragment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ECO_ADMIN_API_KEY", "k")
    respx.get(CURRENCY_URL).mock(return_value=httpx.Response(200, text=_CURRENCY_CSV))
    respx.get(BARTER_URL).mock(return_value=httpx.Response(200, text=_BARTER_EMPTY))
    respx.get(CITIZENS_URL).mock(return_value=httpx.Response(200, json=_CITIZENS_JSON))

    mcp = build_server()
    handler = mcp.request_handlers[mt.CallToolRequest]
    req = mt.CallToolRequest(
        method="tools/call",
        params=mt.CallToolRequestParams(
            name="get_stores",
            arguments={"server": "eco.example.com:3001"},
        ),
    )
    result = await handler(req)
    blocks = result.root.content
    assert len(blocks) == 2
    assert isinstance(blocks[0], mt.TextContent)
    assert "Store & trader directory" in blocks[0].text
    # Just-data per eco-app#87: get_stores no longer emits a widget.
    assert result.root.meta is None


@respx.mock
def test_preview_stores_json_route(monkeypatch: pytest.MonkeyPatch) -> None:
    """The dedicated `/preview/stores.json` data-plane route serves the directory."""
    from fastapi.testclient import TestClient

    from eco_mcp_app.http_app import create_app

    stores_mod._clear_cache()
    monkeypatch.setenv("ECO_ADMIN_API_KEY", "k")
    respx.get(CURRENCY_URL).mock(return_value=httpx.Response(200, text=_CURRENCY_CSV))
    respx.get(BARTER_URL).mock(return_value=httpx.Response(200, text=_BARTER_EMPTY))
    respx.get(CITIZENS_URL).mock(return_value=httpx.Response(200, json=_CITIZENS_JSON))

    client = TestClient(create_app())
    r = client.get("/preview/stores.json?server=eco.example.com:3001")
    assert r.status_code == 200
    payload = r.json()
    assert payload["view"] == "eco_stores"
    assert payload["totalStores"] == 2
    assert {s["owner"] for s in payload["stores"]} == {"ekans", "coilysiren"}


@pytest.mark.asyncio
async def test_list_tools_includes_get_stores() -> None:
    mcp = build_server()
    handler = mcp.request_handlers[mt.ListToolsRequest]
    result = await handler(mt.ListToolsRequest(method="tools/list"))
    names = {tool.name for tool in result.root.tools}
    assert "get_stores" in names


# ---------------------------------------------------------------------------
# Currency attribution on store profiles (eco-app#236)
# ---------------------------------------------------------------------------


def _currency_trade(owner: str, currency: str, amount: float) -> _ParsedTrade:
    return _ParsedTrade(
        trade_type="CurrencyTrade",
        time_s=1000.0,
        day=0.01,
        buyer_id="9",
        seller_id=owner,
        shop_owner_id=owner,
        item="ReinforcedConcreteItem",
        quantity=1.0,
        currency=currency,
        currency_amount=amount,
        unit_price=amount,
        store="StoreItem",
        location="237,68,962",
        direction="sell",
        event_count=1,
        is_rollup=False,
    )


def _directory_for(trades: list[_ParsedTrade]) -> StoreDirectory:
    return build_directory(
        ParsedTradeFetch(
            normalized_base_url=BASE,
            parsed=trades,
            name_map={"7": "Upgrade", "9": "buyer"},
        )
    )


def test_store_currencies_populate_once_the_ledger_names_them() -> None:
    """The busiest store came back denominated in nothing (eco-app#236).

    `currencies: []` next to `totalVolume: 4615.47` was the currency-id join
    failure (eco-app#217) showing through: every trade row's currency had been
    blanked, so there was nothing to group by.
    """
    directory = _directory_for(
        [
            _currency_trade("7", "Spectres", 100.0),
            _currency_trade("7", "Spectres", 50.0),
            _currency_trade("7", "Racines", 25.0),
        ]
    )
    store = directory.stores[0].to_dict()
    assert dict(store["currencies"]) == {"Spectres": 150.0, "Racines": 25.0}
    # Two currencies, so the single scalar adds unlike units and says so.
    assert store["mixedCurrencyVolume"] is True
    assert store["totalVolume"] == 175.0


def test_a_single_currency_store_is_not_flagged_as_mixed() -> None:
    directory = _directory_for(
        [
            _currency_trade("7", "Spectres", 100.0),
            _currency_trade("7", "Spectres", 50.0),
        ]
    )
    store = directory.stores[0].to_dict()
    assert store["mixedCurrencyVolume"] is False
    assert store["totalVolume"] == 150.0
    assert "per-currency breakout" in directory.to_dict()["volumeNote"]


# ---------------------------------------------------------------------------
# A default call fits the tool bound, and a filter returns whole rows (#8354)
# ---------------------------------------------------------------------------


def _directory_rows() -> dict[str, Any]:
    items = [{"item": f"{name}Item", "pretty": name} for name in ("Iron", "Copper", "Wood", "Clay")]
    return {
        "stores": [
            {
                "label": "Iron Works",
                "owner": "ada",
                "storeKey": "k1",
                "topItems": list(items),
                "topCounterparties": [{"name": n} for n in "abcd"],
            },
            {
                "label": "Farm Stand",
                "owner": "bo",
                "storeKey": "k2",
                "topItems": [{"item": "WheatItem", "pretty": "Wheat"}],
                "topCounterparties": [],
            },
        ],
        "traders": [
            {"name": "ada", "topSells": list(items), "topBuys": []},
            {"name": "bo", "topSells": [], "topBuys": [{"item": "WheatItem", "pretty": "Wheat"}]},
        ],
        "warnings": [],
    }


def test_default_directory_lists_are_shortened_and_say_so() -> None:
    from eco_mcp_app.server import _shorten_directory_lists

    payload = _directory_rows()
    _shorten_directory_lists(payload, 2)
    assert len(payload["stores"][0]["topItems"]) == 2
    assert len(payload["stores"][0]["topCounterparties"]) == 2
    assert len(payload["traders"][0]["topSells"]) == 2
    assert any("cut to its top 2" in w for w in payload["warnings"])


def test_a_filter_keeps_matching_rows_whole() -> None:
    from eco_mcp_app.server import _filter_directory

    payload = _directory_rows()
    _filter_directory(payload, None, "copper")
    assert [s["label"] for s in payload["stores"]] == ["Iron Works"]
    assert len(payload["stores"][0]["topItems"]) == 4
    assert [t["name"] for t in payload["traders"]] == ["ada"]

    payload = _directory_rows()
    _filter_directory(payload, "farm", None)
    assert [s["label"] for s in payload["stores"]] == ["Farm Stand"]
    assert payload["traders"] == []


def test_stores_input_defaults_to_a_small_page() -> None:
    from eco_mcp_app.public_routes import STORES_ROW_LIMIT, StoresInput

    assert StoresInput().limit == STORES_ROW_LIMIT
    assert StoresInput(store="x", item="y").store == "x"


def test_a_bounded_directory_drops_whole_rows_to_fit_and_says_so() -> None:
    from eco_mcp_app.server import _fit_directory

    payload = _directory_rows()
    for row in payload["stores"] + payload["traders"]:
        row["pad"] = "x" * 400
    _fit_directory(payload, 1200)
    assert len(json.dumps(payload)) <= 1200
    assert payload["stores"] or payload["traders"]
    assert any("to fit the response" in w for w in payload["warnings"])
    # Rows that stay are whole.
    assert all(len(s["topItems"]) in (1, 4) for s in payload["stores"])
