"""Cycle segmentation, upgrade stage, and norms over synthetic trade lines."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from scripts.trades_norms import STAGES, norms, segment, upgrade_stage

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _o(day: float, item: str, unit: float, cur: str) -> dict:
    return {
        "item": item,
        "qty": 1.0,
        "unit": unit,
        "cur": cur,
        "side": "sold",
        "ts": (T0 + timedelta(days=day)).isoformat(),
    }


def _two_cycles() -> list[dict]:
    """Cycle A trades in Souls for 40 days, a 4-day gap, then cycle B in Sparks at
    ten times the price. One Lumber and eight Brick trade every day in both, enough
    volume for a turnover window."""
    obs = []
    for d in range(40):
        obs += [_o(d, "Lumber", 2.0, "Souls"), *[_o(d, "Brick", 1.0, "Souls")] * 8]
    for d in range(44, 90):
        obs += [_o(d, "Lumber", 20.0, "Sparks"), *[_o(d, "Brick", 10.0, "Sparks")] * 8]
    return obs


def test_upgrade_stage_orders_the_ladder_and_folds_scholars() -> None:
    assert upgrade_stage("Basic Upgrade 1") == 1
    assert upgrade_stage("Scholars Basic Upgrade 4") == 4
    assert upgrade_stage("Advanced Upgrade 1") == 5
    assert upgrade_stage("Modern Upgrade 4") == 12
    assert upgrade_stage("Smelting Upgrade") == 0
    assert STAGES[5] == "Advanced 1"


def test_segment_finds_the_currency_turnover_and_snaps_to_the_gap() -> None:
    cycles = segment(_two_cycles(), latest_cycle=14)
    assert [c["cycle"] for c in cycles] == [13, 14]
    assert cycles[1]["start"] == (T0 + timedelta(days=44)).date().isoformat()
    assert cycles[1]["boundary"] == "activity-gap+currency-turnover"
    assert cycles[0]["boundary"] == "first-trade"


def test_a_currency_change_inside_one_cycle_is_not_a_boundary() -> None:
    # A second currency joins mid-cycle while the first keeps trading.
    obs = [_o(d, "Lumber", 2.0, "Souls") for d in range(60) for _ in range(10)]
    obs += [_o(d, "Brick", 3.0, "Sparks") for d in range(30, 60) for _ in range(10)]
    assert len(segment(obs, latest_cycle=14)) == 1


def test_stage_is_the_highest_upgrade_traded_so_far_and_never_falls() -> None:
    obs = _two_cycles()
    obs += [_o(9.5, "Basic Upgrade 2", 50, "Souls"), _o(20.5, "Basic Upgrade 1", 40, "Souls")]
    out = norms(obs, latest_cycle=14)
    lumber = out["items"]["Lumber"]["cycles"]["13"]["stages"]
    assert lumber["none"]["Souls"]["n"] == 10
    assert lumber["Basic 2"]["Souls"]["n"] == 30
    assert "Basic 1" not in lumber
    onset = out["cycles"][0]["stageOnsetDay"]
    assert onset == {"Basic 2": 9.5}
    # The next cycle starts over with no upgrade traded.
    assert set(out["items"]["Lumber"]["cycles"]["14"]["stages"]) == {"none"}


def test_the_cross_cycle_view_cancels_the_currency_level() -> None:
    out = norms(_two_cycles(), latest_cycle=14)
    by_cycle = {c["cycle"]: c for c in out["cycles"]}
    assert by_cycle[13]["primaryCurrency"] == "Souls"
    assert by_cycle[14]["primaryCurrency"] == "Sparks"
    # Sparks prices are ten times Souls prices, so the index says so.
    ratio = by_cycle[14]["basketIndex"] / by_cycle[13]["basketIndex"]
    assert ratio == pytest.approx(10.0, rel=1e-3)
    cross = out["items"]["Lumber"]["crossCycle"]["none"]
    assert cross["cycles"] == 2
    # Both cycles agree once each is divided by its own basket level.
    per = out["items"]["Lumber"]["cycles"]
    assert per["13"]["byCurrency"]["Souls"]["median"] == 2.0
    assert per["14"]["byCurrency"]["Sparks"]["median"] == 20.0
    assert cross["median"] == pytest.approx(2.0 / by_cycle[13]["basketIndex"], rel=1e-3)


def test_a_long_idle_gap_splits_even_when_currency_names_recur() -> None:
    # Personal credits keep their player's name from one cycle to the next.
    obs = [_o(d, "Lumber", 2.0, "Theo Credit") for d in range(40)]
    obs += [_o(d, "Lumber", 3.0, "Theo Credit") for d in range(70, 110)]
    cycles = segment(obs, latest_cycle=14)
    assert [c["boundary"] for c in cycles] == ["first-trade", "activity-gap"]
    assert cycles[1]["idleDaysBefore"] == 30
    assert cycles[0]["end"] == (T0 + timedelta(days=39)).date().isoformat()
