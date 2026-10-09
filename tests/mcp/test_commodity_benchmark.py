"""Tests for the FRED commodity benchmark behind `get_market`'s `commodityBenchmark`.

Covers alias resolution, key lookup, the cadence-branching math, the sqlite cache, and
`fetch_benchmark`'s three outcomes: a benchmark, a silent null (unmapped item), and a null
with a warning (no key, FRED fault, empty series). The tool wiring is in `test_market.py`.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest
import respx

from eco_mcp_app import commodity_benchmark as cb


@pytest.fixture(autouse=True)
def _isolate_cache_and_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("ECO_MCP_CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    cb._reset_api_key_cache()
    yield
    cb._reset_api_key_cache()


def _meta(freq: str = "M") -> httpx.Response:
    return httpx.Response(200, json={"seriess": [{"id": "X", "frequency_short": freq}]})


def _obs(values: list[tuple[str, str]]) -> httpx.Response:
    # FRED answers sort_order=desc, newest first.
    return httpx.Response(
        200, json={"observations": [{"date": d, "value": v} for d, v in reversed(values)]}
    )


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Copper", "Copper"),
        ("copper", "Copper"),
        ("CopperIngot", "Copper"),
        ("CopperIngotItem", "Copper"),
        ("IronIngot", "Iron"),
        ("IronIngotItem", "Iron"),
        ("lumber", "Board"),
        ("BoardItem", "Board"),
        ("crude", "Oil"),
        ("Wheat", "Wheat"),
    ],
)
def test_resolve_item_aliases(raw: str, expected: str) -> None:
    assert cb.resolve_item(raw) == expected


@pytest.mark.parametrize("raw", ["", None, "Gold", "Item", "nonsense"])
def test_resolve_item_unknown(raw: str | None) -> None:
    assert cb.resolve_item(raw) is None


def test_api_key_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRED_API_KEY", " test-key-abc ")
    assert cb.get_fred_api_key() == "test-key-abc"


def test_api_key_missing_returns_none() -> None:
    assert cb.get_fred_api_key() is None


def test_pct_at_offset_monthly() -> None:
    obs = [("2026-01-01", 100.0), ("2026-02-01", 110.0), ("2026-03-01", 121.0)]
    assert cb._pct_at_offset(obs, 1) == pytest.approx(10.0)
    assert cb._pct_at_offset(obs, 2) == pytest.approx(21.0)
    assert cb._pct_at_offset([("2026-01-01", 1.0)], 1) is None


def test_pct_by_days_walks_to_nearest_gap() -> None:
    obs = [
        ("2026-01-20", 70.0),
        ("2026-03-20", 75.0),
        ("2026-04-13", 78.0),
        ("2026-04-17", 79.0),
        ("2026-04-20", 80.0),
    ]
    assert cb._pct_by_days(obs, 7) == pytest.approx((80 - 78) / 78 * 100)
    assert cb._pct_by_days(obs, 30) == pytest.approx((80 - 75) / 75 * 100)
    assert cb._pct_by_days(obs, 90) == pytest.approx((80 - 70) / 70 * 100)


def test_a_zero_baseline_gives_no_percent_not_a_zero() -> None:
    assert cb._pct(5.0, 0.0) is None


def test_monthly_series_never_gets_day_keys() -> None:
    changes, label = cb.latest_pct_changes([("2026-01-01", 100.0), ("2026-02-01", 110.0)], "M")
    assert label == "monthly"
    assert set(changes) == {"1m", "3m", "12m"}


def test_clean_observations_drops_missing_and_orders_oldest_first() -> None:
    raw = [
        {"date": "2026-04-20", "value": "80.0"},
        {"date": "2026-04-19", "value": "."},
        {"date": "2026-04-18", "value": "78.5"},
    ]
    assert cb._clean_observations(raw) == [("2026-04-18", 78.5), ("2026-04-20", 80.0)]


@pytest.mark.asyncio
async def test_unmapped_item_is_a_silent_null() -> None:
    assert await cb.fetch_benchmark("Cement") == (None, None)
    assert await cb.fetch_benchmark(None) == (None, None)


@pytest.mark.asyncio
async def test_missing_key_is_a_null_with_a_warning() -> None:
    benchmark, warning = await cb.fetch_benchmark("Copper")
    assert benchmark is None
    assert warning is not None and "FRED API key not configured" in warning


@pytest.mark.asyncio
@respx.mock
async def test_monthly_benchmark(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRED_API_KEY", "k")
    values = [(f"2025-{m:02d}-01", str(8000 + 100 * m)) for m in range(4, 13)]
    values += [("2026-01-01", "9000"), ("2026-02-01", "9100"), ("2026-03-01", "9200")]
    values += [("2026-04-01", "9300")]
    respx.get(f"{cb.FRED_BASE_URL}/series").mock(return_value=_meta("M"))
    respx.get(f"{cb.FRED_BASE_URL}/series/observations").mock(return_value=_obs(values))
    benchmark, warning = await cb.fetch_benchmark("CopperIngot")
    assert warning is None and benchmark is not None
    assert benchmark["seriesId"] == "PCOPPUSDM"
    assert benchmark["requested"] == "CopperIngot"
    assert benchmark["item"] == "Copper"
    assert benchmark["frequency"] == "M"
    assert benchmark["changesLabel"] == "monthly"
    assert benchmark["latestValue"] == 9300.0
    assert benchmark["latestDate"] == "2026-04-01"
    assert benchmark["changes"]["1m"] == pytest.approx((9300 - 9200) / 9200 * 100)
    assert benchmark["note"] is None
    assert benchmark["cached"] is False
    assert "Real-world benchmark: copper" in cb.benchmark_markdown(benchmark)


@pytest.mark.asyncio
@respx.mock
async def test_daily_benchmark(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRED_API_KEY", "k")
    values = [
        ((date(2026, 1, 1) + timedelta(days=i)).isoformat(), f"{60 + i * 0.2:.2f}")
        for i in range(110)
        if (date(2026, 1, 1) + timedelta(days=i)).weekday() < 5
    ]
    respx.get(f"{cb.FRED_BASE_URL}/series").mock(return_value=_meta("D"))
    respx.get(f"{cb.FRED_BASE_URL}/series/observations").mock(return_value=_obs(values))
    benchmark, _ = await cb.fetch_benchmark("Oil")
    assert benchmark is not None
    assert benchmark["changesLabel"] == "daily"
    assert set(benchmark["changes"]) == {"7d", "30d", "90d"}
    assert all(v is not None for v in benchmark["changes"].values())


@pytest.mark.asyncio
@respx.mock
async def test_a_proxy_series_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRED_API_KEY", "k")
    respx.get(f"{cb.FRED_BASE_URL}/series").mock(return_value=_meta("M"))
    respx.get(f"{cb.FRED_BASE_URL}/series/observations").mock(
        return_value=_obs([("2026-03-01", "100"), ("2026-04-01", "110")])
    )
    benchmark, _ = await cb.fetch_benchmark("IronIngot")
    assert benchmark is not None
    assert benchmark["benchmarkedAs"] == "iron ore"
    assert "different good" in benchmark["note"]


@pytest.mark.asyncio
@respx.mock
async def test_second_call_hits_the_sqlite_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRED_API_KEY", "k")
    meta_route = respx.get(f"{cb.FRED_BASE_URL}/series").mock(return_value=_meta("M"))
    obs_route = respx.get(f"{cb.FRED_BASE_URL}/series/observations").mock(
        return_value=_obs([("2026-03-01", "100"), ("2026-04-01", "110")])
    )
    first, _ = await cb.fetch_benchmark("Wheat")
    second, _ = await cb.fetch_benchmark("Wheat")
    assert meta_route.call_count == 1 and obs_route.call_count == 1
    assert first is not None and first["cached"] is False
    assert second is not None and second["cached"] is True


@pytest.mark.asyncio
@respx.mock
async def test_empty_series_is_a_null_with_a_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRED_API_KEY", "k")
    respx.get(f"{cb.FRED_BASE_URL}/series").mock(return_value=_meta("M"))
    respx.get(f"{cb.FRED_BASE_URL}/series/observations").mock(
        return_value=httpx.Response(200, json={"observations": [{"date": "x", "value": "."}]})
    )
    benchmark, warning = await cb.fetch_benchmark("Iron")
    assert benchmark is None
    assert warning is not None and "PIORECRUSDM returned no observations" in warning


@pytest.mark.asyncio
@respx.mock
async def test_http_error_warning_names_the_status_and_never_the_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FRED_API_KEY", "secret-key-123")
    respx.get(f"{cb.FRED_BASE_URL}/series").mock(return_value=httpx.Response(500))
    benchmark, warning = await cb.fetch_benchmark("Board")
    assert benchmark is None
    assert warning is not None and "HTTP 500" in warning
    assert "secret-key-123" not in warning


@pytest.mark.asyncio
@respx.mock
async def test_a_transport_fault_and_a_garbled_body_both_degrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FRED_API_KEY", "secret-key-123")
    respx.get(f"{cb.FRED_BASE_URL}/series").mock(side_effect=httpx.ConnectError("boom"))
    benchmark, warning = await cb.fetch_benchmark("Oil")
    assert benchmark is None
    assert warning is not None and "ConnectError" in warning

    respx.get(f"{cb.FRED_BASE_URL}/series").mock(return_value=httpx.Response(200, text="<html>"))
    benchmark, warning = await cb.fetch_benchmark("Wheat")
    assert benchmark is None
    assert warning is not None and "unavailable" in warning
