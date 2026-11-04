"""Per-store and date-range trade history, and the window every answer names (COI-3316).

Fixture trades only, with invented names. The two stores sit at fixed block coordinates, a
trade row names only where it happened, and the live store list is what ties a given name
to a place.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx
import mcp.types as mt
import pytest
import respx

from eco_mcp_app import trades as trades_mod
from eco_mcp_app.ledger_window import parse_window, resolve_bounds
from eco_mcp_app.server import build_server

BASE = "http://eco.example.com:3001"
CURRENCY_URL = f"{BASE}/api/v1/exporter/actions?actionName=CurrencyTrade"
BARTER_URL = f"{BASE}/api/v1/exporter/actions?actionName=BarterTrade"
CITIZENS_URL = f"{BASE}/api/v1/citizens"
STORES_URL = f"{BASE}/api/v1/stores"

DAY = 86400
SMITHY = "10,20,30"
LANTERN = "40,50,60"

_CITIZENS = [
    {"id": 201, "name": "Fenwick"},
    {"id": 202, "name": "Odalys"},
    {"id": 203, "name": "Teague"},
    {"id": 204, "name": "Marisol"},
    {"id": 205, "name": "Quill"},
]

_HEADER = (
    "BankAccount,Currency,CurrencyAmount,NumberOfItems,BoughtOrSold,ShopOwner,"
    "Buyer,Seller,WorldObjectItem,ItemUsed,Citizen,ActionLocation,Count,Time\n"
)


def _row(day: float, owner: int, buyer: int, item: str, qty: int, amount: float, at: str) -> str:
    return (
        f'"acct",Credit,{amount},{qty},33,{owner},{buyer},{owner},StoreItem,{item},{buyer},'
        f'"{at}",1,{int(day * DAY)}\n'
    )


# Newest trade is day 10. The smithy sells on days 6, 8.5, 9.5 and 10, the lantern shop on
# days 9 and 9.9. A last row is an old hourly rollup (Count 4), which names no store or buyer.
_CSV = (
    _HEADER
    + _row(6.0, 201, 203, "IronBarItem", 30, 15.0, SMITHY)
    + _row(8.5, 201, 203, "IronBarItem", 20, 10.0, SMITHY)
    + _row(9.5, 201, 204, "WheatItem", 40, 20.0, SMITHY)
    + _row(10.0, 201, 204, "IronBarItem", 5, 3.0, SMITHY)
    + _row(9.0, 202, 205, "IronBarItem", 100, 50.0, LANTERN)
    + _row(9.9, 202, 205, "WheatItem", 100, 50.0, LANTERN)
    + '"acct",Credit,900.0,90,33,201,203,201,StoreItem,GoldBarItem,203,"10,20,30",4,100000\n'
)

_STORES = [
    {
        "name": "Bramble Smithy",
        "owner": "Fenwick",
        "currency": "Credit",
        "offers": [],
        "location": {"x": 10, "y": 20, "z": 30},
    },
    {
        "name": "Lantern Row Goods",
        "owner": "Odalys",
        "currency": "Credit",
        "offers": [],
        "location": {"x": 40, "y": 50, "z": 60},
    },
]


@pytest.fixture(autouse=True)
def _clear_cache() -> Iterator[None]:
    trades_mod._trades_cache.clear()
    yield
    trades_mod._trades_cache.clear()


def _mock(*, stores: list[dict[str, Any]] | None = _STORES) -> None:
    respx.get(CURRENCY_URL).mock(return_value=httpx.Response(200, text=_CSV))
    respx.get(BARTER_URL).mock(return_value=httpx.Response(200, text="Buyer,Seller\n"))
    respx.get(CITIZENS_URL).mock(return_value=httpx.Response(200, json=_CITIZENS))
    if stores is None:
        respx.get(STORES_URL).mock(return_value=httpx.Response(404))
    else:
        respx.get(STORES_URL).mock(return_value=httpx.Response(200, json=stores))


async def _trades(
    arguments: dict[str, object], tool: str = "get_trades"
) -> tuple[str, dict[str, Any], bool]:
    handler = build_server().request_handlers[mt.CallToolRequest]
    result = await handler(
        mt.CallToolRequest(
            method="tools/call",
            params=mt.CallToolRequestParams(
                name=tool, arguments={"server": "eco.example.com:3001", **arguments}
            ),
        )
    )
    blocks = result.root.content
    text = getattr(blocks[0], "text", "")
    if result.root.isError:
        return text, {}, True
    return text, json.loads(getattr(blocks[1], "text", "")), False


@pytest.fixture(autouse=True)
def _key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ECO_ADMIN_API_KEY", "k")


# One store over a window returns only that store's trades in that window.


@pytest.mark.asyncio
@respx.mock
async def test_one_store_over_a_window_returns_only_its_trades_in_that_window() -> None:
    _mock()
    _, payload, _ = await _trades({"store": "Bramble Smithy", "days": 2, "limit": 0})

    rows = payload["trades"]
    assert [r["day"] for r in rows] == [10.0, 9.5, 8.5]
    assert {r["shopOwner"] for r in rows} == {"Fenwick"}
    assert {r["location"] for r in rows} == {SMITHY}
    assert payload["totalTrades"] == 3
    assert payload["storeFilter"]["matched"][0]["label"] == "Fenwick's Bramble Smithy"


@pytest.mark.asyncio
@respx.mock
async def test_the_other_store_and_the_day_outside_the_window_are_left_out() -> None:
    _mock()
    _, payload, _ = await _trades({"store": "Bramble Smithy", "days": 2, "limit": 0})

    assert 6.0 not in [r["day"] for r in payload["trades"]]
    assert LANTERN not in {r["location"] for r in payload["trades"]}
    assert "Quill" not in {name for name, _ in payload["topBuyers"]}


@pytest.mark.asyncio
@respx.mock
async def test_aggregates_describe_the_store_and_window_not_the_whole_ledger() -> None:
    _mock()
    _, payload, _ = await _trades({"store": "Bramble Smithy", "days": 2, "limit": 0})

    # Wheat 40 units, iron 25 (20 + 5): the day-6 sale of 30 iron falls outside the window.
    assert [(i, u) for i, u, _, _ in payload["byItemUnits"]] == [
        ("WheatItem", 40.0),
        ("IronBarItem", 25.0),
    ]
    assert payload["topBuyersByUnits"] == [["Marisol", 45.0], ["Teague", 20.0]]
    assert payload["aggregateScope"].startswith("topBuyers, topSellers, byItem")
    assert "the matched store" in payload["aggregateScope"]


@pytest.mark.asyncio
@respx.mock
async def test_a_store_with_no_window_covers_every_detailed_trade_it_made() -> None:
    _mock()
    _, payload, _ = await _trades({"store": "Bramble Smithy", "limit": 0})

    assert [r["day"] for r in payload["trades"]] == [10.0, 9.5, 8.5, 6.0]


# Every trade or top-buyer answer carries the window it covers.


@pytest.mark.asyncio
@respx.mock
async def test_a_top_buyer_result_carries_its_window() -> None:
    _mock()
    text, payload, _ = await _trades({"store": "Bramble Smithy", "days": 2, "limit": 0})

    window = payload["window"]
    assert window["label"] == "the last 2 server days (days 8 to 10)"
    assert window["unit"] == "server days"
    assert window["requested"] == {"days": 2.0, "sinceDay": None, "untilDay": None}
    assert (window["startDay"], window["endDay"]) == (8.0, 10.0)
    assert (window["firstTradeDay"], window["lastTradeDay"]) == (8.5, 10.0)
    assert "Window: the last 2 server days" in text
    assert "Top buyers by units: Marisol (45)" in text


@pytest.mark.asyncio
@respx.mock
async def test_an_unfiltered_answer_still_says_which_trades_it_covers() -> None:
    _mock()
    text, payload, _ = await _trades({"limit": 0})

    window = payload["window"]
    assert window["requested"] is None
    assert window["label"] == "all detailed trades on record (days 6 to 10)"
    assert "detailed trades only" in window["basis"]
    assert "Window: all detailed trades on record" in text


@pytest.mark.asyncio
@respx.mock
async def test_the_window_leads_the_payload_with_the_other_caveats() -> None:
    _mock()
    _, payload, _ = await _trades({"store": "Bramble Smithy", "days": 2})

    keys = list(payload)
    assert keys.index("window") < keys.index("trades")
    assert keys.index("storeFilter") < keys.index("topBuyers")


@pytest.mark.asyncio
@respx.mock
async def test_a_day_range_names_both_ends() -> None:
    _mock()
    _, payload, _ = await _trades({"since_day": 9, "until_day": 9.6, "limit": 0})

    assert payload["window"]["label"] == "day 9 to day 9.6"
    assert sorted(r["day"] for r in payload["trades"]) == [9.0, 9.5]


# The item filter narrows the aggregates too, so a top buyer is the item's.


@pytest.mark.asyncio
@respx.mock
async def test_the_top_buyer_of_an_item_is_that_items_not_the_ledgers() -> None:
    _mock()
    _, payload, _ = await _trades({"item": "iron", "limit": 0})

    assert payload["itemFilter"]["resolved"] == "Iron Bar"
    assert payload["itemFilter"]["of"] == 6 and payload["itemFilter"]["matched"] == 4
    # Quill bought 100 iron, Teague 50, Marisol 5. Wheat (the other 140 units) is not counted.
    assert payload["topBuyersByUnits"] == [["Quill", 100.0], ["Teague", 50.0], ["Marisol", 5.0]]
    assert "item Iron Bar" in payload["aggregateScope"]
    assert any("describe Iron Bar only" in w for w in payload["warnings"])


# A name a word or a letter off still finds the store, or lists the closest.


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize("asked", ["Bramble Smithey", "Smithy Bramble", "fenwick"])
async def test_a_swapped_word_a_typo_or_the_owners_handle_finds_the_store(asked: str) -> None:
    _mock()
    _, payload, _ = await _trades({"store": asked, "limit": 0})

    assert {r["location"] for r in payload["trades"]} == {SMITHY}
    if asked != "fenwick":
        assert payload["storeFilter"]["resolvedFrom"] == asked
        assert any("showing the closest" in w for w in payload["warnings"])


@pytest.mark.asyncio
@respx.mock
async def test_a_store_name_near_nothing_returns_no_trades_and_says_so() -> None:
    _mock()
    text, payload, _ = await _trades({"store": "Zephyr Anvil", "limit": 0})

    assert payload["trades"] == [] and payload["storeFilter"]["matched"] == []
    assert any("no store matched" in w for w in payload["warnings"])
    assert text.startswith("**Trades ledger** — no trades match")


@pytest.mark.asyncio
@respx.mock
async def test_without_the_live_store_list_a_store_is_matched_by_its_owner_only() -> None:
    _mock(stores=None)
    _, by_owner, _ = await _trades({"store": "Fenwick", "limit": 0})
    assert {r["location"] for r in by_owner["trades"]} == {SMITHY}
    assert by_owner["storeFilter"]["rosterSource"] == "trade owners only"
    assert any("live store list is unavailable" in w for w in by_owner["warnings"])

    trades_mod._trades_cache.clear()
    _, by_name, _ = await _trades({"store": "Bramble Smithy", "limit": 0})
    assert by_name["trades"] == []


# Arguments with no meaning are refused, not guessed at.


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize(
    ("arguments", "fragment"),
    [
        ({"days": 2, "since_day": 1}, "not both"),
        ({"since_day": 9, "until_day": 3}, "after until_day"),
    ],
)
async def test_a_combination_that_cannot_mean_anything_is_refused_in_words(
    arguments: dict[str, object], fragment: str
) -> None:
    _mock()
    text, _, errored = await _trades(arguments)

    assert errored and fragment in text


@pytest.mark.parametrize(
    ("arguments", "fragment"),
    [
        ({"days": 0}, "days must be more than 0"),
        ({"days": 2, "since_day": 1}, "not both"),
        ({"since_day": 9, "until_day": 3}, "after until_day"),
        ({"days": "soon"}, "must be a number"),
        ({"until_day": -1}, "0 or more"),
    ],
)
def test_parse_window_names_what_is_wrong(arguments: dict[str, object], fragment: str) -> None:
    with pytest.raises(ValueError, match=fragment):
        parse_window(arguments)


def test_a_relative_window_counts_back_from_its_end_or_the_newest_trade() -> None:
    assert resolve_bounds(parse_window({"days": 2}), 10.0) == (8.0, 10.0)
    assert resolve_bounds(parse_window({"days": 2, "until_day": 6}), 10.0) == (4.0, 6.0)
    assert resolve_bounds(parse_window({"days": 50}), 10.0) == (0.0, 10.0)
    assert resolve_bounds(parse_window({"since_day": 3}), 10.0) == (3.0, None)
    assert resolve_bounds(parse_window({}), 10.0) == (None, None)


@pytest.mark.asyncio
@respx.mock
async def test_the_store_directory_says_which_trades_its_profiles_cover() -> None:
    _mock()
    _, payload, _ = await _trades({"limit": 0}, tool="get_stores")

    assert payload["window"]["label"] == "all detailed trades on record (days 6 to 10)"
    assert payload["window"]["requested"] is None
