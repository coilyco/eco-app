"""Tests for the per-item market price intelligence layer (eco-app#49).

Covers:
  - `build_market` price math: daily buckets with median / min / max / volume,
    per-currency separation, overall median, latest read.
  - Trend classification: rising / falling / flat / insufficient over the
    short-vs-long window delta.
  - `item` / `currency` filters and the normalized-id matcher (`Iron` →
    `IronIngotItem`).
  - `fetch_market` folding the trades ledger over respx-mocked exporter CSVs
    (consuming eco-app#6, not re-parsing).
  - Thin-data / zero-trade render paths (markdown + card context).
  - Tool wiring: `get_market` registered, returns text blocks and no widget
    (just-data per eco-app#87); the optional `commodityBenchmark` field
    (respx-mocked FRED) leaves every existing payload key intact.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import mcp.types as mt
import pytest
import respx

from eco_mcp_app import commodity_benchmark as cb
from eco_mcp_app import market as market_mod
from eco_mcp_app import trades as trades_mod
from eco_mcp_app.market import (
    MarketIntelligence,
    build_market,
    fetch_market,
    market_markdown,
    market_template_context,
)
from eco_mcp_app.server import build_server


def _row(
    item: str,
    currency: str,
    day: float,
    unit_price: float | None,
    quantity: float = 1.0,
) -> dict[str, object]:
    """A ledger-shaped row dict (the normalized model from eco-app#6)."""
    return {
        "item": item,
        "currency": currency,
        "day": day,
        "unitPrice": unit_price,
        "quantity": quantity,
    }


@pytest.fixture(autouse=True)
def _clear_cache() -> Iterator[None]:
    trades_mod._trades_cache.clear()
    yield
    trades_mod._trades_cache.clear()


# ---------------------------------------------------------------------------
# build_market — bucket math
# ---------------------------------------------------------------------------


def test_build_market_buckets_median_min_max_volume() -> None:
    rows = [
        _row("WoodItem", "Credit", 1, 10.0, 2),
        _row("WoodItem", "Credit", 1, 20.0, 3),
        _row("WoodItem", "Credit", 1, 30.0, 1),  # day-1 median 20, min 10, max 30
        _row("WoodItem", "Credit", 2, 40.0, 5),  # day-2 single trade
    ]
    markets = build_market(rows)
    assert len(markets) == 1
    m = markets[0]
    assert m.item == "WoodItem"
    assert m.currency == "Credit"
    assert [b.day for b in m.buckets] == [1, 2]
    b1 = m.buckets[0]
    assert b1.median == pytest.approx(20.0)
    assert b1.minimum == pytest.approx(10.0)
    assert b1.maximum == pytest.approx(30.0)
    assert b1.volume == pytest.approx(6.0)  # 2 + 3 + 1 units
    assert b1.trades == 3
    assert m.total_volume == pytest.approx(11.0)
    assert m.total_trades == 4
    assert m.latest_price == pytest.approx(40.0)
    assert m.latest_day == 2
    # Overall median across all four unit prices: median(10,20,30,40) = 25.
    assert m.median_price == pytest.approx(25.0)


def test_build_market_separates_currencies() -> None:
    rows = [
        _row("WoodItem", "Credit", 1, 10.0),
        _row("WoodItem", "Gold", 1, 5.0),
    ]
    markets = build_market(rows)
    keys = {(m.item, m.currency) for m in markets}
    assert keys == {("WoodItem", "Credit"), ("WoodItem", "Gold")}


def test_build_market_skips_unpriced_and_barter_rows() -> None:
    rows = [
        _row("WoodItem", "Credit", 1, None),  # no unit price
        _row("WoodItem", "", 1, 10.0),  # barter (no currency)
        _row("", "Credit", 1, 10.0),  # no item
        _row("WoodItem", "Credit", 1, -3.0),  # nonsense negative price
        _row("WoodItem", "Credit", 1, 12.0),  # the only real one
    ]
    markets = build_market(rows)
    assert len(markets) == 1
    assert markets[0].total_trades == 1
    assert markets[0].median_price == pytest.approx(12.0)


# ---------------------------------------------------------------------------
# Trend classification
# ---------------------------------------------------------------------------


def test_trend_rising() -> None:
    rows = [
        _row("WoodItem", "Credit", 1, 10.0),
        _row("WoodItem", "Credit", 2, 12.0),
        _row("WoodItem", "Credit", 3, 20.0),
        _row("WoodItem", "Credit", 4, 22.0),
        _row("WoodItem", "Credit", 5, 24.0),
    ]
    m = build_market(rows)[0]
    assert m.trend == "rising"
    assert m.trend_delta_pct is not None and m.trend_delta_pct > 0


def test_trend_falling() -> None:
    rows = [
        _row("WoodItem", "Credit", 1, 24.0),
        _row("WoodItem", "Credit", 2, 22.0),
        _row("WoodItem", "Credit", 3, 20.0),
        _row("WoodItem", "Credit", 4, 12.0),
        _row("WoodItem", "Credit", 5, 10.0),
    ]
    m = build_market(rows)[0]
    assert m.trend == "falling"
    assert m.trend_delta_pct is not None and m.trend_delta_pct < 0


def test_trend_flat_within_band() -> None:
    rows = [_row("WoodItem", "Credit", d, 10.0) for d in range(1, 6)]
    m = build_market(rows)[0]
    assert m.trend == "flat"
    assert m.trend_delta_pct == pytest.approx(0.0)


def test_trend_insufficient_single_day() -> None:
    rows = [_row("WoodItem", "Credit", 1, 10.0), _row("WoodItem", "Credit", 1, 12.0)]
    m = build_market(rows)[0]
    assert m.trend == "insufficient"
    assert m.trend_delta_pct is None


# ---------------------------------------------------------------------------
# Filters + ranking
# ---------------------------------------------------------------------------


def test_build_market_item_filter_normalizes() -> None:
    rows = [
        _row("IronIngotItem", "Credit", 1, 25.0),
        _row("WheatItem", "Credit", 1, 3.0),
    ]
    # "Iron" should resolve to IronIngotItem via the normalized key.
    markets = build_market(rows, item="Iron")
    assert len(markets) == 1
    assert markets[0].item == "IronIngotItem"


def test_build_market_currency_filter() -> None:
    rows = [
        _row("WoodItem", "Credit", 1, 10.0),
        _row("WoodItem", "Gold", 1, 5.0),
    ]
    markets = build_market(rows, currency="gold")
    assert len(markets) == 1
    assert markets[0].currency == "Gold"


def test_build_market_ranks_by_trade_count() -> None:
    rows = [_row("BusyItem", "Credit", 1, 1.0) for _ in range(5)]
    rows += [_row("QuietItem", "Credit", 1, 1.0)]
    markets = build_market(rows)
    assert markets[0].item == "BusyItem"
    assert markets[-1].item == "QuietItem"


def test_build_market_top_markets_cap() -> None:
    rows = [_row(f"Item{i}", "Credit", 1, float(i + 1)) for i in range(30)]
    markets = build_market(rows, top_markets=5)
    assert len(markets) == 5


# ---------------------------------------------------------------------------
# Rendering / empty states
# ---------------------------------------------------------------------------


def test_market_markdown_empty() -> None:
    intel = MarketIntelligence(fetched_at_iso="t", source_base_url="eco.example:3001")
    md = market_markdown(intel)
    assert "no priced trades" in md.lower()


def test_market_template_context_empty() -> None:
    intel = MarketIntelligence(fetched_at_iso="t", source_base_url="b")
    ctx = market_template_context(intel)
    assert ctx["empty"] is True
    assert ctx["markets"] == []


def test_market_template_context_and_markdown_populated() -> None:
    intel = MarketIntelligence(fetched_at_iso="t", source_base_url="b", total_trades=5)
    intel.markets = build_market(
        [
            _row("IronIngotItem", "Credit", 1, 10.0),
            _row("IronIngotItem", "Credit", 2, 20.0),
        ]
    )
    ctx = market_template_context(intel)
    assert ctx["empty"] is False
    assert ctx["markets"][0]["pretty"] == "Iron Ingot"
    assert ctx["markets"][0]["spark"]  # sparkline points present
    md = market_markdown(intel)
    assert "Iron Ingot" in md
    assert "Credit/unit" in md


# ---------------------------------------------------------------------------
# fetch_market via respx (consumes the trades ledger)
# ---------------------------------------------------------------------------

BASE = "http://eco.example.com:3001"
CURRENCY_URL = f"{BASE}/api/v1/exporter/actions?actionName=CurrencyTrade"
BARTER_URL = f"{BASE}/api/v1/exporter/actions?actionName=BarterTrade"
CITIZENS_URL = f"{BASE}/api/v1/citizens"

_CITIZENS_JSON = [{"id": 1, "name": "alice"}, {"id": 2, "name": "bob"}]

# Two IronIngotItem trades on different in-game days (Time = day * 86400).
_CURRENCY_CSV = (
    "BankAccount,Currency,CurrencyAmount,NumberOfItems,BoughtOrSold,ShopOwner,"
    "Buyer,Seller,WorldObjectItem,ItemUsed,Citizen,ActionLocation,Count,Time\n"
    '"a",Credit,200.0,10,33,1,1,2,StoreItem,IronIngotItem,1,"1,2,3",1,86400\n'
    '"a",Credit,300.0,10,33,1,1,2,StoreItem,IronIngotItem,1,"1,2,3",1,172800\n'
)
_BARTER_EMPTY = "Buyer,Seller,ItemUsed,NumberOfItems,Count,Time\n"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_market_folds_ledger() -> None:
    respx.get(CURRENCY_URL).mock(return_value=httpx.Response(200, text=_CURRENCY_CSV))
    respx.get(BARTER_URL).mock(return_value=httpx.Response(200, text=_BARTER_EMPTY))
    respx.get(CITIZENS_URL).mock(return_value=httpx.Response(200, json=_CITIZENS_JSON))

    intel = await fetch_market(base_url=BASE, api_key="k")
    assert intel.total_trades == 2
    assert len(intel.markets) == 1
    m = intel.markets[0]
    assert m.item == "IronIngotItem"
    assert m.currency == "Credit"
    # Day 1 unit price 20, day 2 unit price 30.
    assert [b.median for b in m.buckets] == [pytest.approx(20.0), pytest.approx(30.0)]


@pytest.mark.asyncio
@respx.mock
async def test_fetch_market_empty_is_clean() -> None:
    empty = "BankAccount,Currency,Time\n"
    respx.get(CURRENCY_URL).mock(return_value=httpx.Response(200, text=empty))
    respx.get(BARTER_URL).mock(return_value=httpx.Response(200, text=_BARTER_EMPTY))

    intel = await fetch_market(base_url=BASE, api_key=None)
    assert intel.total_trades == 0
    assert intel.markets == []
    assert market_template_context(intel)["empty"] is True


# ---------------------------------------------------------------------------
# Tool wiring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_market_registered() -> None:
    mcp = build_server()
    handler = mcp.request_handlers[mt.ListToolsRequest]
    result = await handler(mt.ListToolsRequest(method="tools/list"))
    names = {t.name for t in result.root.tools}
    assert "get_market" in names


@pytest.mark.asyncio
@respx.mock
async def test_get_market_tool_returns_blocks_and_fragment(
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
            name="get_market", arguments={"server": "eco.example.com:3001"}
        ),
    )
    result = await handler(req)
    blocks = result.root.content
    assert len(blocks) == 2
    assert "Market price intelligence" in blocks[0].text
    import json as _json

    payload = _json.loads(blocks[1].text)
    assert payload["view"] == "market"
    assert payload["markets"][0]["item"] == "IronIngotItem"
    # Just-data per eco-app#87: get_market no longer emits a widget.
    assert result.root.meta is None


@respx.mock
def test_preview_market_json_route(monkeypatch: pytest.MonkeyPatch) -> None:
    """The dedicated `/preview/market.json` data-plane route serves the series."""
    from fastapi.testclient import TestClient

    from eco_mcp_app.http_app import create_app

    trades_mod._trades_cache.clear()
    monkeypatch.setenv("ECO_ADMIN_API_KEY", "k")
    respx.get(CURRENCY_URL).mock(return_value=httpx.Response(200, text=_CURRENCY_CSV))
    respx.get(BARTER_URL).mock(return_value=httpx.Response(200, text=_BARTER_EMPTY))
    respx.get(CITIZENS_URL).mock(return_value=httpx.Response(200, json=_CITIZENS_JSON))

    client = TestClient(create_app())
    r = client.get("/preview/market.json?server=eco.example.com:3001")
    assert r.status_code == 200
    payload = r.json()
    assert payload["view"] == "market"
    assert payload["markets"][0]["item"] == "IronIngotItem"


def _fred_meta(freq: str = "M") -> httpx.Response:
    return httpx.Response(200, json={"seriess": [{"id": "X", "frequency_short": freq}]})


def _fred_obs(values: list[tuple[str, str]]) -> httpx.Response:
    return httpx.Response(
        200, json={"observations": [{"date": d, "value": v} for d, v in reversed(values)]}
    )


async def _call_get_market(arguments: dict[str, str]) -> tuple[str, dict[str, Any]]:
    import json as _json

    mcp = build_server()
    handler = mcp.request_handlers[mt.CallToolRequest]
    result = await handler(
        mt.CallToolRequest(
            method="tools/call",
            params=mt.CallToolRequestParams(name="get_market", arguments=arguments),
        )
    )
    blocks = result.root.content
    return blocks[0].text, _json.loads(blocks[1].text)


def _mock_exporter() -> None:
    respx.get(CURRENCY_URL).mock(return_value=httpx.Response(200, text=_CURRENCY_CSV))
    respx.get(BARTER_URL).mock(return_value=httpx.Response(200, text=_BARTER_EMPTY))
    respx.get(CITIZENS_URL).mock(return_value=httpx.Response(200, json=_CITIZENS_JSON))


@pytest.fixture()
def _fred_env(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> Iterator[None]:
    monkeypatch.setenv("ECO_MCP_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("ECO_ADMIN_API_KEY", "k")
    monkeypatch.setenv("FRED_API_KEY", "k")
    trades_mod._trades_cache.clear()
    cb._reset_api_key_cache()
    yield
    cb._reset_api_key_cache()


@pytest.mark.asyncio
@respx.mock
async def test_get_market_without_item_has_no_benchmark_field(_fred_env: None) -> None:
    """The field is optional: no item filter, no key, and no FRED call."""
    _mock_exporter()
    fred = respx.get(f"{cb.FRED_BASE_URL}/series")
    _, payload = await _call_get_market({"server": "eco.example.com:3001"})
    assert "commodityBenchmark" not in payload
    assert not fred.called


@pytest.mark.asyncio
@respx.mock
async def test_get_market_benchmark_leaves_existing_keys_intact(_fred_env: None) -> None:
    _mock_exporter()
    respx.get(f"{cb.FRED_BASE_URL}/series").mock(return_value=_fred_meta("M"))
    respx.get(f"{cb.FRED_BASE_URL}/series/observations").mock(
        return_value=_fred_obs([("2026-03-01", "100"), ("2026-04-01", "120")])
    )
    text, payload = await _call_get_market({"server": "eco.example.com:3001", "item": "Iron"})
    # The ledger is cached by the call above, so this is the same data the tool folded.
    intel = await fetch_market(base_url="eco.example.com:3001", api_key="k", item="Iron")
    for key, value in intel.to_dict().items():
        got = payload[key]
        if key == "markets":
            # The dispatch layer adds a price `norm` to each row, which is not ours to compare.
            got = [{k: v for k, v in row.items() if k != "norm"} for row in got]
        if key == "warnings":
            # The freshness warning (COI-2067) leads the list, and is not the benchmark's to match.
            got = [w for w in got if not w.startswith("ledger freshness")]
        assert got == value, key
    benchmark = payload["commodityBenchmark"]
    assert isinstance(benchmark, dict)
    assert benchmark["seriesId"] == "PIORECRUSDM"
    assert benchmark["latestValue"] == 120.0
    assert benchmark["changes"]["1m"] == pytest.approx(20.0)
    assert benchmark["note"]
    assert "Real-world benchmark: iron ore" in text
    # Caveat keys still lead the payload, ahead of the bulk and the new field.
    assert list(payload)[:2] == ["view", "warnings"]


@pytest.mark.asyncio
@respx.mock
async def test_get_market_benchmark_is_null_for_an_unmapped_item(_fred_env: None) -> None:
    _mock_exporter()
    fred = respx.get(f"{cb.FRED_BASE_URL}/series")
    _, payload = await _call_get_market({"server": "eco.example.com:3001", "item": "Cement"})
    assert payload["commodityBenchmark"] is None
    assert not any("benchmark" in w.lower() for w in payload["warnings"])
    assert not fred.called


@pytest.mark.asyncio
@respx.mock
async def test_get_market_benchmark_is_null_with_a_warning_when_fred_fails(
    _fred_env: None,
) -> None:
    _mock_exporter()
    respx.get(f"{cb.FRED_BASE_URL}/series").mock(return_value=httpx.Response(500))
    text, payload = await _call_get_market({"server": "eco.example.com:3001", "item": "Iron"})
    assert payload["commodityBenchmark"] is None
    assert any("Commodity benchmark for Iron unavailable" in w for w in payload["warnings"])
    assert "Commodity benchmark for Iron unavailable" in text
    # The market rows still arrive.
    assert payload["markets"][0]["item"] == "IronIngotItem"


# ---------------------------------------------------------------------------
# Empty-market diagnostics (eco-app#218)
# ---------------------------------------------------------------------------


def _ledger_row(**kw: object) -> dict[str, object]:
    row: dict[str, object] = {
        "item": "CementItem",
        "currency": "Spectres",
        "unitPrice": 0.6,
        "quantity": 3.0,
        "day": 40.0,
    }
    row.update(kw)
    return row


def test_currencyless_rows_build_no_market_and_say_why() -> None:
    """`markets: []` for every query, filtered or not (eco-app#218).

    A price series needs an item, a currency and a unit price. The currency-id
    join failure (eco-app#217) blanked every row's currency, so every row was
    skipped — while the same payload reported totalTrades: 22891, which made
    the tool look like it was reaching data and finding nothing in it.
    """
    rows = [_ledger_row(currency=""), _ledger_row(currency="")]
    assert build_market(rows) == []
    warnings = market_mod._explain_empty_market(rows, [], item=None, currency=None)
    assert warnings
    assert "missing a currency" in warnings[0]
    assert "eco-app#217" in warnings[0]


def test_named_currency_rows_do_build_a_market() -> None:
    # The same rows, once the currency join has run.
    markets = build_market([_ledger_row(), _ledger_row(unitPrice=0.7)])
    assert len(markets) == 1
    assert markets[0].item == "CementItem"
    assert markets[0].currency == "Spectres"
    assert markets[0].total_trades == 2


def test_a_filter_that_matches_nothing_says_so() -> None:
    rows = [_ledger_row()]
    markets = build_market(rows, item="Wood Pulp")
    assert markets == []
    warnings = market_mod._explain_empty_market(rows, markets, item="Wood Pulp", currency=None)
    assert "No markets matched" in warnings[0]
    assert "Wood Pulp" in warnings[0]


def test_a_populated_market_adds_no_diagnostic() -> None:
    rows = [_ledger_row()]
    markets = build_market(rows)
    assert market_mod._explain_empty_market(rows, markets, item=None, currency=None) == []
