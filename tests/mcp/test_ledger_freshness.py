"""The ledger freshness check: ledger newest day against the cycle clock (COI-2067).

Cycle 14 showed the failure this pins. The ledger's newest trade sat 37 days behind
`cycle.daysRunning` and nothing in the payload said whether nobody had traded or the
exporter had stopped. Each case below is one state the check has to name.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import mcp.types as mt
import pytest

from eco_mcp_app import caveats
from eco_mcp_app import server as eco_server
from eco_mcp_app.ledger_freshness import WorldClock, apply_freshness, assess, read_world_clock
from eco_mcp_app.market import MarketIntelligence
from eco_mcp_app.trades import (
    SECONDS_PER_DAY,
    TradesLedger,
    _ParsedTrade,
    build_ledger,
)

_REAL_WORLD_CLOCK = eco_server._world_clock  # conftest swaps it per test, this keeps the real one


def _trade(day: float, *, rollup: bool = False) -> _ParsedTrade:
    return _ParsedTrade(
        trade_type="CurrencyTrade",
        time_s=day * SECONDS_PER_DAY,
        day=day,
        buyer_id="1",
        seller_id="2",
        shop_owner_id="2",
        item="IronIngotItem",
        quantity=1.0,
        currency="Credit",
        currency_amount=1.0,
        unit_price=1.0,
        store="StoreItem",
        location="1,2,3",
        direction="sell",
        event_count=4 if rollup else 1,
        is_rollup=rollup,
    )


# ---- the comparison itself -------------------------------------------------


def test_current_when_the_newest_trade_is_inside_the_threshold() -> None:
    block, warning = assess(9.4, WorldClock(days_running=10, trades_total=40))
    assert block["status"] == "current"
    assert block["lagDays"] == 0.6
    assert warning is None


def test_lagging_names_both_causes_and_the_counter_that_settles_it() -> None:
    """The Cycle 14 numbers: newest trade day 57.96, cycle day 95, Eco counter 1,466."""
    block, warning = assess(57.96, WorldClock(days_running=95, trades_total=1466))
    assert block["status"] == "lagging"
    assert block["lagDays"] == 37.04
    assert block["infoTradesTotal"] == 1466
    assert warning is not None
    assert "day 57.96" in warning
    assert "cycle is on day 95" in warning
    assert "nobody has traded" in warning
    assert "1,466" in warning
    assert "stalled" in warning


def test_lag_is_never_negative() -> None:
    """DaysRunning is a whole day and the newest trade a fraction of one."""
    block, _ = assess(4.9, WorldClock(days_running=4, trades_total=None))
    assert block["lagDays"] == 0.0


def test_an_unreachable_clock_is_unverifiable_never_current() -> None:
    clock = WorldClock(days_running=None, trades_total=None, error="ConnectError: refused")
    block, warning = assess(57.96, clock)
    assert block["status"] == "unverifiable"
    assert block["infoReachable"] is False
    assert block["infoError"] == "ConnectError: refused"
    # null, never zero: nothing was measured, so nothing is reported as a number
    assert block["daysRunning"] is None
    assert block["lagDays"] is None
    assert block["infoTradesTotal"] is None
    assert warning is not None
    assert "unverifiable" in warning
    assert "ConnectError" in warning
    assert "day 57.96" in warning


def test_info_without_days_running_is_unverifiable_too() -> None:
    block, warning = assess(3.0, WorldClock(days_running=None, trades_total=5))
    assert block["status"] == "unverifiable"
    assert block["infoReachable"] is True
    assert warning is not None
    assert "sent no DaysRunning" in warning


def test_an_empty_ledger_beside_a_nonzero_counter_warns() -> None:
    block, warning = assess(None, WorldClock(days_running=2, trades_total=7))
    assert block["status"] == "empty"
    assert block["newestTradeDay"] is None
    assert warning is not None
    assert "holds no trades" in warning
    assert "7" in warning


@pytest.mark.parametrize("counter", [0, None])
def test_an_empty_ledger_on_a_quiet_new_cycle_is_not_an_alarm(counter: int | None) -> None:
    block, warning = assess(None, WorldClock(days_running=0, trades_total=counter))
    assert block["status"] == "empty"
    assert warning is None


def test_read_world_clock_parses_days_and_the_trade_counter() -> None:
    clock = read_world_clock({"DaysRunning": 95, "EconomyDesc": "1466 trades, 0 contracts"})
    assert clock == WorldClock(days_running=95, trades_total=1466)


@pytest.mark.parametrize(
    "info", [{}, {"DaysRunning": "", "EconomyDesc": None}, {"DaysRunning": "x"}]
)
def test_read_world_clock_leaves_absent_fields_none(info: dict[str, Any]) -> None:
    clock = read_world_clock(info)
    assert clock.days_running is None
    assert clock.trades_total is None


def test_apply_freshness_puts_the_warning_first_and_returns_it() -> None:
    payload: dict[str, Any] = {"newestTradeDay": 1.0, "warnings": ["older note"]}
    warning = apply_freshness(payload, WorldClock(days_running=30, trades_total=None))
    assert payload["warnings"][0] == warning
    assert payload["warnings"][1] == "older note"
    assert payload["ledgerFreshness"]["status"] == "lagging"


# ---- what the ledger records -----------------------------------------------


def test_newest_trade_day_counts_rollups_and_is_none_when_empty() -> None:
    ledger = TradesLedger(fetched_at_iso="t", source_base_url="b")
    build_ledger([_trade(3.5), _trade(9.25, rollup=True), _trade(1.0)], ledger, {})
    assert ledger.newest_trade_day == 9.25
    assert ledger.to_dict()["newestTradeDay"] == 9.25

    empty = TradesLedger(fetched_at_iso="t", source_base_url="b")
    build_ledger([], empty, {})
    assert empty.newest_trade_day is None
    assert empty.to_dict()["newestTradeDay"] is None


# ---- through the tools -----------------------------------------------------


async def _call(tool: str, arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    handler = eco_server.build_server().request_handlers[mt.CallToolRequest]
    result = await handler(
        mt.CallToolRequest(
            method="tools/call",
            params=mt.CallToolRequestParams(name=tool, arguments=arguments),
        )
    )
    blocks = [b.text for b in result.root.content if isinstance(b, mt.TextContent)]
    return blocks[0], json.loads(blocks[-1])


def _ledger_with_newest(day: float) -> TradesLedger:
    ledger = TradesLedger(fetched_at_iso="t", source_base_url="b")
    build_ledger([_trade(day), _trade(day - 1)], ledger, {})
    return ledger


@pytest.fixture
def real_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(eco_server, "_world_clock", _REAL_WORLD_CLOCK)


@pytest.mark.asyncio
async def test_get_trades_flags_a_lagging_ledger_in_payload_and_markdown(
    monkeypatch: pytest.MonkeyPatch, real_clock: None
) -> None:
    async def fetch_ledger(**_: Any) -> TradesLedger:
        return _ledger_with_newest(57.96)

    async def info(_server: str | None = None) -> dict[str, Any]:
        return {"DaysRunning": 95, "EconomyDesc": "1466 trades, 0 contracts"}

    monkeypatch.setattr(eco_server, "fetch_ledger", fetch_ledger)
    monkeypatch.setattr(eco_server, "fetch_eco_info", info)

    text, payload = await _call("get_trades", {"limit": 0})

    assert payload["ledgerFreshness"]["status"] == "lagging"
    assert payload["ledgerFreshness"]["daysRunning"] == 95
    assert payload["ledgerFreshness"]["infoTradesTotal"] == 1466
    assert payload["warnings"][0].startswith("the newest ledger trade is day 57.96")
    assert "37.0 days behind" in text
    keys = list(payload)
    assert keys.index("ledgerFreshness") < keys.index("trades")
    assert caveats.is_caveat("ledgerFreshness")


@pytest.mark.asyncio
async def test_a_connect_error_from_info_warns_and_the_ledger_still_answers(
    monkeypatch: pytest.MonkeyPatch, real_clock: None
) -> None:
    """The ledger reads from the exporter. `/info` going dark must not blank or hide it."""

    async def fetch_ledger(**_: Any) -> TradesLedger:
        return _ledger_with_newest(57.96)

    async def info(_server: str | None = None) -> dict[str, Any]:
        raise httpx.ConnectError(
            "All connection attempts failed",
            request=httpx.Request("GET", "http://eco.example.com:3001/info"),
        )

    monkeypatch.setattr(eco_server, "fetch_ledger", fetch_ledger)
    monkeypatch.setattr(eco_server, "fetch_eco_info", info)

    text, payload = await _call("get_trades", {"limit": 0})

    fresh = payload["ledgerFreshness"]
    assert fresh["status"] == "unverifiable"
    assert fresh["infoReachable"] is False
    assert "ConnectError" in fresh["infoError"]
    assert fresh["daysRunning"] is None
    assert fresh["lagDays"] is None
    assert len(payload["trades"]) == 2
    assert payload["warnings"][0].startswith("ledger freshness unverifiable")
    assert "unverifiable" in text


@pytest.mark.asyncio
async def test_get_market_carries_the_same_block(
    monkeypatch: pytest.MonkeyPatch, real_clock: None
) -> None:
    async def fetch_market(**_: Any) -> MarketIntelligence:
        return MarketIntelligence(
            fetched_at_iso="t", source_base_url="b", newest_trade_day=57.96, markets_total=0
        )

    async def info(_server: str | None = None) -> dict[str, Any]:
        return {"DaysRunning": 58}

    monkeypatch.setattr(eco_server.market_mod, "fetch_market", fetch_market)
    monkeypatch.setattr(eco_server, "fetch_eco_info", info)

    _, payload = await _call("get_market", {"limit": 0})

    assert payload["ledgerFreshness"]["status"] == "current"
    assert payload["newestTradeDay"] == 57.96
    assert payload["newestBucketDay"] is None


@pytest.mark.asyncio
async def test_the_market_cap_says_what_it_hid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cycle 14: buckets ended at day 40 while the ledger ran to 57.96. The top-N cap kept
    the busiest markets and dropped the thin ones, which held every later trade."""
    from eco_mcp_app import market as market_mod

    def row(item: str, day: float) -> dict[str, Any]:
        return {"item": item, "currency": "Credit", "unitPrice": 2.0, "quantity": 1, "day": day}

    ledger = TradesLedger(fetched_at_iso="t", source_base_url="b", newest_trade_day=57.96)
    ledger.trades = [
        row("IronItem", 10.2), row("IronItem", 11.3), row("IronItem", 40.1),
        row("CopperItem", 12.2), row("CopperItem", 13.3),
        row("LateItem", 57.96),
    ]  # fmt: skip

    async def fetch_ledger(**_: Any) -> TradesLedger:
        return ledger

    monkeypatch.setattr(market_mod, "fetch_ledger", fetch_ledger)
    monkeypatch.setattr(market_mod, "TOP_MARKETS", 2)

    intel = await market_mod.fetch_market()
    payload = intel.to_dict()

    assert [m.item for m in intel.markets] == ["IronItem", "CopperItem"]
    assert payload["marketsTotal"] == 3
    assert payload["newestBucketDay"] == 40
    assert payload["newestPricedDay"] == 57
    assert payload["newestTradeDay"] == 57.96
    capped = [w for w in intel.warnings if w.startswith("market list capped")]
    assert len(capped) == 1
    assert "top 2 of 3" in capped[0]
    assert "day 57, against day 40" in capped[0]


@pytest.mark.asyncio
async def test_an_uncapped_market_list_adds_no_cap_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from eco_mcp_app import market as market_mod

    ledger = TradesLedger(fetched_at_iso="t", source_base_url="b", newest_trade_day=3.0)
    ledger.trades = [
        {"item": "IronItem", "currency": "Credit", "unitPrice": 2.0, "quantity": 1, "day": 3.0}
    ]

    async def fetch_ledger(**_: Any) -> TradesLedger:
        return ledger

    monkeypatch.setattr(market_mod, "fetch_ledger", fetch_ledger)

    intel = await market_mod.fetch_market()

    assert intel.markets_total == 1
    assert not any(w.startswith("market list capped") for w in intel.warnings)
