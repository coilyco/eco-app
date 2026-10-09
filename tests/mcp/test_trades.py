"""Tests for the trades ledger aggregator + tool wiring (eco-app#6).

Covers:
  - Folding a CurrencyTrade CSV into row-level trades with the right fields
    (party names, quantity, currency amount, unit price, decoded direction).
  - Time → in-game-day conversion (seconds / 86400, species convention).
  - Aggregates: top buyers / sellers by currency, per-currency volume,
    most-traded items, per-item price-over-time series.
  - Numeric party ids joined to names via /api/v1/citizens, `Citizen #<id>`
    fallback for a missing id (eco-app#5).
  - Defensive realignment of a row carrying an undeclared extra column.
  - Missing-endpoint (401 / connect error) becomes a non-fatal warning.
  - Empty CSVs produce a clean "no trades" ledger.
  - The in-memory TTL cache is per (base, api-key) and hits within TTL.
  - Tool wiring returns two TextContent blocks and no widget (just-data per eco-app#87).
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterator
from typing import Any

import httpx
import mcp.types as mt
import pytest
import respx

from eco_mcp_app import trades as trades_mod
from eco_mcp_app.server import build_server
from eco_mcp_app.trades import (
    SECONDS_PER_DAY,
    TradesLedger,
    _ParsedTrade,
    build_ledger,
    fetch_ledger,
    ledger_markdown,
    ledger_template_context,
    parse_trade_rows,
)

CURRENCY_URL = "http://eco.example.com:3001/api/v1/exporter/actions?actionName=CurrencyTrade"
BARTER_URL = "http://eco.example.com:3001/api/v1/exporter/actions?actionName=BarterTrade"
CITIZENS_URL = "http://eco.example.com:3001/api/v1/citizens"
BASE = "http://eco.example.com:3001"

_CITIZENS_JSON = [
    {"id": 129312, "name": "coilysiren"},
    {"id": 130409, "name": "ekans"},
    {"id": 129580, "name": "redwood"},
]

# Real cycle-13 column order: BankAccount, Currency, CurrencyAmount,
# NumberOfItems, BoughtOrSold, ShopOwner, Buyer, Seller, WorldObjectItem,
# ItemUsed, Citizen, ActionLocation, Count, Time.
_CURRENCY_CSV = (
    "BankAccount,Currency,CurrencyAmount,NumberOfItems,BoughtOrSold,ShopOwner,"
    "Buyer,Seller,WorldObjectItem,ItemUsed,Citizen,ActionLocation,Count,Time\n"
    '"ekans account",Credit,250.0,10,33,130409,129312,130409,StoreItem,'
    'IronIngotItem,129312,"1,2,3",1,300000\n'
    '"ekans account",Credit,140.0,5,33,130409,129580,130409,StoreItem,'
    'IronIngotItem,129580,"1,2,3",1,200000\n'
    '"coilysiren account",Credit,225.0,45,32,129312,129569,129312,StoreItem,'
    'WheatItem,129569,"4,5,6",1,100000\n'
)

_BARTER_EMPTY = "Buyer,Seller,ItemUsed,NumberOfItems,Count,Time\n"

# An old hourly CurrencyTrade rollup. CurrencyAmount and NumberOfItems are
# summed, but the buyer/seller/item/store fields are a representative event and
# therefore must not enter any attribution or price calculation (eco-app#132).
_CURRENCY_ROLLUP = (
    '"old account",Credit,900.0,90,33,130409,129312,130409,StoreItem,'
    'BogusRepresentativeItem,129312,"1,2,3",4,400000\n'
)


@pytest.fixture(autouse=True)
def _clear_cache() -> Iterator[None]:
    """The trades cache is a module-level TTLCache; clear it between tests."""
    trades_mod._trades_cache.clear()
    yield
    trades_mod._trades_cache.clear()


def _rows(csv_text: str) -> list[list[str]]:
    import csv

    return list(csv.reader(csv_text.splitlines()))


def test_parse_trade_rows_folds_currency_csv() -> None:
    ledger = TradesLedger(fetched_at_iso="t", source_base_url="b")
    parsed: list[_ParsedTrade] = []
    n = parse_trade_rows("CurrencyTrade", _rows(_CURRENCY_CSV), ledger, parsed)
    assert n == 3
    assert ledger.per_type_counts["CurrencyTrade"] == 3
    first = parsed[0]
    assert first.buyer_id == "129312"
    assert first.seller_id == "130409"
    assert first.item == "IronIngotItem"
    assert first.quantity == pytest.approx(10.0)
    assert first.currency == "Credit"
    assert first.currency_amount == pytest.approx(250.0)
    assert first.unit_price == pytest.approx(25.0)
    # BoughtOrSold 33 decodes to "sell"; 32 to "buy" (heuristic, eco-app#6).
    assert first.direction == "sell"
    assert parsed[2].direction == "buy"
    # Time → in-game day via the species /86400 convention.
    assert first.day == pytest.approx(300000 / SECONDS_PER_DAY)


def test_parse_trade_rows_coerces_nonfinite_amounts() -> None:
    """A CSV numeric field of ``Infinity`` (or ``NaN``) parses via ``float``
    without raising, but must not survive as a non-finite value that later 500s
    ``JSONResponse`` (eco-app#83). It is coerced to 0.0, so no unit price."""
    poisoned = (
        "BankAccount,Currency,CurrencyAmount,NumberOfItems,BoughtOrSold,ShopOwner,"
        "Buyer,Seller,WorldObjectItem,ItemUsed,Citizen,ActionLocation,Count,Time\n"
        '"ekans account",Credit,Infinity,10,33,130409,129312,130409,StoreItem,'
        'IronIngotItem,129312,"1,2,3",1,300000\n'
    )
    ledger = TradesLedger(fetched_at_iso="t", source_base_url="b")
    parsed: list[_ParsedTrade] = []
    parse_trade_rows("CurrencyTrade", _rows(poisoned), ledger, parsed)
    assert parsed[0].currency_amount == 0.0
    assert parsed[0].unit_price is None
    assert math.isfinite(parsed[0].currency_amount)


def test_build_ledger_aggregates_and_resolves_names() -> None:
    ledger = TradesLedger(fetched_at_iso="t", source_base_url="b")
    parsed: list[_ParsedTrade] = []
    parse_trade_rows("CurrencyTrade", _rows(_CURRENCY_CSV), ledger, parsed)
    name_map = {"129312": "coilysiren", "130409": "ekans", "129580": "redwood"}
    build_ledger(parsed, ledger, name_map)

    assert ledger.total_trades == 3
    assert ledger.total_currency_volume == pytest.approx(615.0)
    # by_item: iron (2 trades, 390 volume) ahead of wheat (1, 225).
    by_item = {item: (count, vol) for item, count, vol in ledger.by_item}
    assert by_item["IronIngotItem"] == (2, pytest.approx(390.0))
    assert by_item["WheatItem"] == (1, pytest.approx(225.0))
    assert ledger.by_item[0][0] == "IronIngotItem"
    # Currency volume.
    assert dict(ledger.by_currency) == {"Credit": pytest.approx(615.0)}
    # Sellers earn: ekans 390, coilysiren 225.
    top_sellers = dict(ledger.top_sellers)
    assert top_sellers["ekans"] == pytest.approx(390.0)
    assert top_sellers["coilysiren"] == pytest.approx(225.0)
    assert ledger.top_sellers[0][0] == "ekans"
    # Buyers spend: coilysiren 250, unmapped 129569 → "Citizen #129569" 225.
    top_buyers = dict(ledger.top_buyers)
    assert top_buyers["coilysiren"] == pytest.approx(250.0)
    assert top_buyers["Citizen #129569"] == pytest.approx(225.0)
    # Price series: iron has two days (mean unit price per day), sorted by day.
    iron_series = ledger.price_series["IronIngotItem"]
    assert iron_series == [
        (pytest.approx(2.0), pytest.approx(28.0)),
        (pytest.approx(3.0), pytest.approx(25.0)),
    ]
    # Row-level ledger is newest-first (highest Time first).
    assert [r["time"] for r in ledger.trades] == [300000.0, 200000.0, 100000.0]
    assert ledger.trades[0]["seller"] == "ekans"
    assert ledger.trades[2]["buyer"] == "Citizen #129569"


def test_build_ledger_excludes_hourly_rollups_from_attribution() -> None:
    ledger = TradesLedger(fetched_at_iso="t", source_base_url="b")
    parsed: list[_ParsedTrade] = []
    parse_trade_rows("CurrencyTrade", _rows(_CURRENCY_CSV + _CURRENCY_ROLLUP), ledger, parsed)
    build_ledger(parsed, ledger, {"129312": "coilysiren", "130409": "ekans"})

    # Count is the real merged-event total, unlike the single CSV rollup row.
    assert ledger.total_trades == 7
    assert ledger.detailed_trades == 3
    assert ledger.rollup_rows == 1
    assert ledger.rollup_trades == 4
    # CurrencyAmount is summed by the source action, so gross/currency totals
    # retain all history; party/item and unit-price views remain detailed-only.
    assert ledger.total_currency_volume == pytest.approx(1515.0)
    assert dict(ledger.by_currency)["Credit"] == pytest.approx(1515.0)
    assert "BogusRepresentativeItem" not in {item for item, _, _ in ledger.by_item}
    assert all(row["item"] != "BogusRepresentativeItem" for row in ledger.trades)
    assert "BogusRepresentativeItem" not in ledger.price_series
    assert dict(ledger.top_sellers)["ekans"] == pytest.approx(390.0)
    assert any("party, item, store, and unit-price" in w for w in ledger.warnings)


def test_parse_trade_rows_realigns_shifted_row() -> None:
    """A row with an undeclared extra column still folds with correct fields."""
    ledger = TradesLedger(fetched_at_iso="t", source_base_url="b")
    parsed: list[_ParsedTrade] = []
    # Extra "HandsItem" column inserted before ActionLocation shifts later
    # fields; the ActionLocation position triple anchors the realignment (same
    # shape as the crafting realign test).
    csv_text = (
        "Currency,CurrencyAmount,NumberOfItems,BoughtOrSold,ShopOwner,Buyer,"
        "Seller,WorldObjectItem,ItemUsed,Citizen,ActionLocation,Count,Time\n"
        "Credit,250.0,10,33,130409,129312,130409,StoreItem,IronIngotItem,"
        '129312,HandsItem,"1,2,3",1,300000\n'
    )
    parse_trade_rows("CurrencyTrade", _rows(csv_text), ledger, parsed)
    t = parsed[0]
    # Buyer/Seller land on the numeric ids, amount/item on their real columns.
    assert t.buyer_id == "129312"
    assert t.seller_id == "130409"
    assert t.currency_amount == pytest.approx(250.0)
    assert t.item == "IronIngotItem"
    assert t.store == "StoreItem"


def test_parse_trade_rows_respects_max_rows_cap() -> None:
    ledger = TradesLedger(fetched_at_iso="t", source_base_url="b")
    parsed: list[_ParsedTrade] = []
    n = parse_trade_rows("CurrencyTrade", _rows(_CURRENCY_CSV), ledger, parsed, max_rows=2)
    assert n == 2
    assert any("truncated" in w for w in ledger.warnings)


@pytest.mark.asyncio
@respx.mock
async def test_fetch_ledger_merges_actions_and_joins_names() -> None:
    respx.get(CURRENCY_URL).mock(return_value=httpx.Response(200, text=_CURRENCY_CSV))
    respx.get(BARTER_URL).mock(return_value=httpx.Response(200, text=_BARTER_EMPTY))
    respx.get(CITIZENS_URL).mock(return_value=httpx.Response(200, json=_CITIZENS_JSON))

    ledger = await fetch_ledger(base_url=BASE, api_key="secret", cache_ttl_s=0)
    assert ledger.total_trades == 3
    assert ledger.per_type_counts == {"CurrencyTrade": 3, "BarterTrade": 0}
    assert ledger.trades[0]["seller"] == "ekans"
    assert ledger.trades[0]["buyer"] == "coilysiren"
    # Unmapped id falls back rather than dropping.
    assert ledger.trades[2]["buyer"] == "Citizen #129569"
    assert ledger.warnings == []


@pytest.mark.asyncio
@respx.mock
async def test_fetch_ledger_tolerates_partial_failure() -> None:
    respx.get(CURRENCY_URL).mock(return_value=httpx.Response(200, text=_CURRENCY_CSV))
    respx.get(BARTER_URL).mock(return_value=httpx.Response(401))
    respx.get(CITIZENS_URL).mock(return_value=httpx.Response(200, json=_CITIZENS_JSON))

    ledger = await fetch_ledger(base_url=BASE, api_key=None, cache_ttl_s=0)
    assert ledger.total_trades == 3  # currency rows still folded
    assert any("BarterTrade" in w and "401" in w for w in ledger.warnings)


@pytest.mark.asyncio
@respx.mock
async def test_fetch_ledger_empty_is_clean() -> None:
    empty_csv = "BankAccount,Currency,Time\n"
    respx.get(CURRENCY_URL).mock(return_value=httpx.Response(200, text=empty_csv))
    respx.get(BARTER_URL).mock(return_value=httpx.Response(200, text=_BARTER_EMPTY))

    ledger = await fetch_ledger(base_url=BASE, api_key=None, cache_ttl_s=0)
    assert ledger.total_trades == 0
    assert ledger.trades == []
    assert ledger.by_item == []
    assert ledger_template_context(ledger)["empty"] is True
    assert "no trades" in ledger_markdown(ledger).lower()


@pytest.mark.asyncio
@respx.mock
async def test_fetch_ledger_cache_hits_within_ttl() -> None:
    route = respx.get(CURRENCY_URL).mock(return_value=httpx.Response(200, text=_CURRENCY_CSV))
    respx.get(BARTER_URL).mock(return_value=httpx.Response(200, text=_BARTER_EMPTY))
    respx.get(CITIZENS_URL).mock(return_value=httpx.Response(200, json=_CITIZENS_JSON))

    a1 = await fetch_ledger(base_url=BASE, api_key="k", cache_ttl_s=60)
    a2 = await fetch_ledger(base_url=BASE, api_key="k", cache_ttl_s=60)
    assert a1.total_trades == a2.total_trades == 3
    # Second call served from cache — exactly one upstream hit.
    assert route.call_count == 1


def test_ledger_template_context_ranks_items() -> None:
    ledger = TradesLedger(fetched_at_iso="t", source_base_url="b")
    parsed: list[_ParsedTrade] = []
    parse_trade_rows("CurrencyTrade", _rows(_CURRENCY_CSV), ledger, parsed)
    build_ledger(parsed, ledger, {"130409": "ekans", "129312": "coilysiren"})
    ctx = ledger_template_context(ledger)
    assert ctx["empty"] is False
    assert ctx["top_items"][0]["name"] == "IronIngotItem"
    assert ctx["top_items"][0]["pretty"] == "Iron Ingot"
    assert ctx["top_sellers"][0]["name"] == "ekans"


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
            name="get_trades",
            arguments={"server": "eco.example.com:3001"},
        ),
    )
    result = await handler(req)
    blocks = result.root.content
    assert len(blocks) == 2
    assert isinstance(blocks[0], mt.TextContent)
    assert "Trades ledger" in blocks[0].text
    # Just-data per eco-app#87: get_trades no longer emits a widget.
    assert result.root.meta is None


@pytest.mark.asyncio
async def test_list_tools_includes_get_trades() -> None:
    mcp = build_server()
    handler = mcp.request_handlers[mt.ListToolsRequest]
    result = await handler(mt.ListToolsRequest(method="tools/list"))
    names = {tool.name for tool in result.root.tools}
    assert "get_trades" in names


def test_counts_block_reconciles_every_trade_number() -> None:
    """Four numbers presented as trade counts, now each labelled (eco-app#221).

    On Sirens `perTypeCounts` summed to 3,672 and reconciled with nothing else
    in the payload: not `detailedTrades` (526), not `rollupTrades` (22,365),
    not `totalTrades` (22,891) — while sitting in the same object as the first.
    It counts exporter *rows*; the rest count trade *events*.
    """
    ledger = TradesLedger(fetched_at_iso="t", source_base_url=BASE)
    ledger.detailed_trades = 526
    ledger.rollup_rows = 3146
    ledger.rollup_trades = 22365
    ledger.total_trades = 526 + 22365
    ledger.per_type_counts = {"CurrencyTrade": 3668, "BarterTrade": 4}

    counts = ledger.to_dict()["counts"]
    # perTypeCounts is a row count, and the block says so with a number that
    # actually matches it.
    assert counts["exporterRows"] == sum(ledger.per_type_counts.values())
    assert counts["exporterRows"] == counts["detailedRows"] + counts["rollupRows"]
    # And the event count reconciles the other way.
    assert counts["tradeEvents"] == counts["detailedRows"] + counts["rollupTrades"]
    assert "exporter rows, not trades" in counts["note"]
    # The /info counter is named as a separate population rather than left to
    # look like a contradiction.
    assert "economy line of get_server_status" in counts["note"]


def test_ledger_markdown_names_both_units() -> None:
    ledger = TradesLedger(fetched_at_iso="t", source_base_url=BASE)
    ledger.detailed_trades = 526
    ledger.rollup_rows = 3146
    ledger.rollup_trades = 22365
    ledger.total_trades = 22891
    headline = ledger_markdown(ledger).splitlines()[0]
    assert "22,891 trade events" in headline
    assert "3,672 exporter rows" in headline


# --- response-cap bounding reaches the tool handler (#256) -------------------


@pytest.mark.asyncio
async def test_get_trades_bounds_its_ledger_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    """528 trade rows were 93% of a 187 KB response, with no way to bound it."""
    import mcp.types as mt

    from eco_mcp_app import server as eco_server

    ledger = TradesLedger(fetched_at_iso="t", source_base_url="b")
    ledger.total_trades = 528
    ledger.trades = [{"item": f"Item{i}", "price": float(i)} for i in range(528)]

    async def _fake_fetch(**_kwargs: object) -> TradesLedger:
        return ledger

    monkeypatch.setattr(eco_server, "fetch_ledger", _fake_fetch)
    handler = eco_server.build_server().request_handlers[mt.CallToolRequest]

    async def call(arguments: dict[str, object]) -> dict[str, Any]:
        result = await handler(
            mt.CallToolRequest(
                method="tools/call",
                params=mt.CallToolRequestParams(name="get_trades", arguments=arguments),
            )
        )
        for block in result.root.content:
            try:
                parsed = json.loads(getattr(block, "text", "") or "")
            except ValueError:
                continue
            if isinstance(parsed, dict):
                return parsed
        raise AssertionError("no JSON block")

    bounded = await call({})
    assert len(bounded["trades"]) == 50
    assert any("showing 50 of 528 rows" in w for w in bounded["warnings"])
    # The aggregate layer still describes every row.
    assert bounded["totalTrades"] == 528

    unbounded = await call({"limit": 0})
    assert len(unbounded["trades"]) == 528


@pytest.mark.asyncio
async def test_limit_reaches_every_growing_array(monkeypatch) -> None:
    """limit bounded only the trades array. byItem grows with the catalogue,
    byCurrency with the roster, and topBuyers/topSellers hold one row per
    trading citizen despite the name. See #267."""
    import json

    import mcp.types as mt

    from eco_mcp_app import server
    from eco_mcp_app.trades import TradesLedger

    ledger = TradesLedger(fetched_at_iso="2026-01-01T00:00:00Z", source_base_url="http://e")
    ledger.trades = [{"item": f"i{i}"} for i in range(40)]
    ledger.by_item = [(f"item{i}", i, float(i)) for i in range(40)]
    ledger.by_currency = [(f"cur{i}", float(i)) for i in range(40)]
    ledger.top_buyers = [(f"buyer{i}", float(i)) for i in range(40)]
    ledger.top_sellers = [(f"seller{i}", float(i)) for i in range(40)]
    ledger.total_currency_volume = 9999.0

    async def stub(**_):
        return ledger

    monkeypatch.setattr(server, "fetch_ledger", stub)
    mcp_server = server.build_server()
    handler = mcp_server.request_handlers[mt.CallToolRequest]
    result = await handler(
        mt.CallToolRequest(
            method="tools/call",
            params=mt.CallToolRequestParams(name="get_trades", arguments={"limit": 5}),
        )
    )
    payload = json.loads(result.root.content[-1].text)

    for key in ("trades", "byItem", "byCurrency", "topBuyers", "topSellers"):
        assert len(payload[key]) == 5, f"{key} ignored limit"
        assert any(w.startswith(f"{key}:") for w in payload["warnings"]), (
            f"{key} truncated without saying so"
        )
    assert payload["totalCurrencyVolume"] == 9999.0


# --- the item filter resolves like price_by_stage and runs before limit ------


async def _filtered_trades(
    monkeypatch: pytest.MonkeyPatch, arguments: dict[str, object]
) -> tuple[str, dict[str, Any]]:
    """get_trades over a ledger holding 60 newer wheat rows ahead of 3 iron bars and 2
    Basic Upgrade 1 rows, so a limit applied first would hide every match."""
    from eco_mcp_app import server as eco_server

    ledger = TradesLedger(fetched_at_iso="t", source_base_url="b")
    ledger.total_trades = 65
    ledger.trades = (
        [{"item": "WheatItem", "buyer": f"w{i}"} for i in range(60)]
        + [{"item": "IronBarItem", "buyer": f"iron{i}"} for i in range(3)]
        + [{"item": "BasicUpgradeLvl1Item", "buyer": f"bu{i}"} for i in range(2)]
    )

    async def _fake_fetch(**_kwargs: object) -> TradesLedger:
        return ledger

    monkeypatch.setattr(eco_server, "fetch_ledger", _fake_fetch)
    handler = eco_server.build_server().request_handlers[mt.CallToolRequest]
    result = await handler(
        mt.CallToolRequest(
            method="tools/call",
            params=mt.CallToolRequestParams(name="get_trades", arguments=arguments),
        )
    )
    text, blob = result.root.content
    return getattr(text, "text", ""), json.loads(getattr(blob, "text", ""))


@pytest.mark.asyncio
async def test_item_filter_runs_over_the_whole_ledger_before_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text, payload = await _filtered_trades(monkeypatch, {"item": "iron", "limit": 2})
    assert [t["buyer"] for t in payload["trades"]] == ["iron0", "iron1"]
    assert payload["itemFilter"] == {
        "query": "iron",
        "resolved": "Iron Bar",
        "of": 65,
        "matched": 3,
    }
    assert any("showing 2 of 3 rows" in w for w in payload["warnings"])
    assert any(w.startswith("item filter:") for w in payload["warnings"])
    assert "Item filter: Iron Bar, 3 of 65 ledger rows" in text


@pytest.mark.asyncio
@pytest.mark.parametrize("word", ["Iron Bar", "IronBarItem", "irons", "some iron"])
async def test_item_filter_takes_a_name_an_id_a_plural_or_a_bare_metal(
    monkeypatch: pytest.MonkeyPatch, word: str
) -> None:
    _, payload = await _filtered_trades(monkeypatch, {"item": word, "limit": 0})
    assert len(payload["trades"]) == 3 and payload["itemFilter"]["resolved"] == "Iron Bar"


@pytest.mark.asyncio
@pytest.mark.parametrize("word", ["bu1", "BU 1", "basic upgrade 1"])
async def test_item_filter_reads_upgrade_shorthand_as_the_item(
    monkeypatch: pytest.MonkeyPatch, word: str
) -> None:
    _, payload = await _filtered_trades(monkeypatch, {"item": word, "limit": 0})
    assert [t["buyer"] for t in payload["trades"]] == ["bu0", "bu1"]
    assert payload["itemFilter"]["resolved"] == "Basic Upgrade 1"


@pytest.mark.asyncio
async def test_item_filter_drops_a_stage_qualifier_and_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, payload = await _filtered_trades(monkeypatch, {"item": "iron at au3", "limit": 0})
    assert len(payload["trades"]) == 3
    assert payload["itemFilter"]["stageIgnored"] == "au3"


@pytest.mark.asyncio
@pytest.mark.parametrize("word", ["unobtainium", "mu0"])
async def test_an_unresolved_item_empties_trades_instead_of_dumping_the_ledger(
    monkeypatch: pytest.MonkeyPatch, word: str
) -> None:
    text, payload = await _filtered_trades(monkeypatch, {"item": word, "limit": 0})
    assert payload["trades"] == []
    assert payload["itemFilter"]["resolved"] is None and payload["itemFilter"]["matched"] == 0
    assert "could not be matched" in text


@pytest.mark.asyncio
async def test_no_item_leaves_the_ledger_unfiltered(monkeypatch: pytest.MonkeyPatch) -> None:
    _, payload = await _filtered_trades(monkeypatch, {"limit": 0})
    assert len(payload["trades"]) == 65 and "itemFilter" not in payload


@pytest.mark.asyncio
async def test_the_tool_description_and_schema_name_the_item_filter() -> None:
    handler = build_server().request_handlers[mt.ListToolsRequest]
    result = await handler(mt.ListToolsRequest(method="tools/list"))
    tool = next(t for t in result.root.tools if t.name == "get_trades")
    assert "Pass item" in (tool.description or "")
    assert "item" in tool.inputSchema["properties"]
