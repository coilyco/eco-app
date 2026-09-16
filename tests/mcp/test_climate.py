"""Unit tests for the `get_climate` tool.

Covers:
- ``fetch_climate`` dataset fan-out, candidate-name fallthrough, and
  graceful degradation when the admin token is absent.
- ``compute_climate_payload`` math: % change, status classification, real-
  world Earth CO2 anchor.
- Polluter attribution via the action-exporter CSV stream.
- The MCP tool wiring end-to-end (call_tool returns markdown + JSON,
  and no widget — just-data per eco-app#87).
- The ``get_map`` pollution overlay pulls ``Layers/Pollution.gif`` and
  exposes it as ``pollutionDataUri``.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import mcp.types as mt
import pytest
import respx

from eco_mcp_app import climate as climate_mod
from eco_mcp_app import map as eco_map
from eco_mcp_app import server as eco_server
from eco_mcp_app.climate import (
    CO2_DATASET_CANDIDATES,
    EARTH_CO2_HISTORY,
    POLLUTION_ACTION_TYPES,
    POLLUTION_DATASET_CANDIDATES,
    SEA_LEVEL_DATASET_CANDIDATES,
    ClimateSnapshot,
    _earth_year_for_ppm,
    _parse_dataset_point,
    _parse_layer_pct,
    _percent_change,
    _resolve_rules,
    compute_climate_payload,
    fetch_climate,
)
from eco_mcp_app.server import (
    DEFAULT_ECO_INFO_URL,
    build_server,
)

_DEFAULT_BASE = DEFAULT_ECO_INFO_URL.rsplit("/info", 1)[0]
_DATASET_URL = f"{_DEFAULT_BASE}/datasets/get"
_WORLDLAYERS_URL = f"{_DEFAULT_BASE}/api/v1/worldlayers/layers"


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test starts with a clean cache + a known admin token."""
    eco_server._info_cache.clear()
    eco_server._economy_cache.clear()
    eco_server._admin_token_cache.clear()
    climate_mod._clear_cache()
    monkeypatch.setenv("ECO_ADMIN_TOKEN", "test-token")


def _info(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "Description": "Eco via Sirens",
        "Category": "Test",
        "DaysRunning": 4,
        "TimeSinceStart": 4 * 3600,
        "EconomyDesc": "100 trades, 0 contracts",
        "TotalCulture": 50.0,
    }
    base.update(overrides)
    return base


def _series(values: list[float]) -> list[dict[str, float]]:
    return [{"Time": float(i * 3600), "Value": float(v)} for i, v in enumerate(values)]


def _route_datasets(values: dict[str, list[float]]) -> None:
    """Mock /datasets/get with a per-dataset value table.

    Datasets not in `values` return an empty list (mirrors the live
    behavior for unknown / disabled stat names).
    """

    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.params.get("dataset", "")
        if name in values:
            return httpx.Response(200, json=_series(values[name]))
        return httpx.Response(200, json=[])

    respx.get(_DATASET_URL).mock(side_effect=handler)


def _parallel_series(values: list[float], unit: str = "PPM") -> dict[str, object]:
    """Eco 0.13.0.3 parallel-array dataset payload (Times+Values+Interval+Unit).

    Matches the live shape verified against ``eco.coilysiren.me:3001``.
    """
    return {
        "Times": [float(i * 86400) for i, _ in enumerate(values)],
        "Values": [float(v) for v in values],
        "Interval": 86400.0,
        "Unit": unit,
    }


def _route_datasets_parallel(
    values: dict[str, list[float]], *, units: dict[str, str] | None = None
) -> None:
    """Same as ``_route_datasets`` but returns parallel-arrays shape."""

    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.params.get("dataset", "")
        if name in values:
            unit = (units or {}).get(name, "PPM")
            return httpx.Response(200, json=_parallel_series(values[name], unit=unit))
        return httpx.Response(
            200, json={"Times": [], "Values": [], "Interval": 86400.0, "Unit": "PPM"}
        )

    respx.get(_DATASET_URL).mock(side_effect=handler)


def _route_worldlayers_empty() -> None:
    respx.get(_WORLDLAYERS_URL).mock(return_value=httpx.Response(200, json=[]))


def _route_actions_empty() -> None:
    """Stub every pollution action endpoint with an empty CSV (just header)."""
    for action in POLLUTION_ACTION_TYPES:
        url = f"{_DEFAULT_BASE}/api/v1/exporter/actions?actionName={action}"
        respx.get(url).mock(return_value=httpx.Response(200, text="Time,Citizen,Count\n"))


def _route_climate_settings(settings: dict[str, object] | None = None) -> None:
    """Stub the telemetry mod's ``/api/v1/climate-settings`` endpoint.

    ``None`` → 404 (endpoint absent, un-modded server → defaults fallback).
    A dict → 200 with the live per-server ruleset.
    """
    url = f"{_DEFAULT_BASE}/api/v1/climate-settings"
    if settings is None:
        respx.get(url).mock(return_value=httpx.Response(404))
    else:
        respx.get(url).mock(return_value=httpx.Response(200, json=settings))


def _route_flatlist(names: list[str]) -> None:
    """Stub flatlist with a plain list-of-strings (old Eco shape)."""
    respx.get(f"{_DEFAULT_BASE}/datasets/flatlist").mock(
        return_value=httpx.Response(200, json=names)
    )


def _route_flatlist_dicts(names: list[str]) -> None:
    """Stub flatlist with the dict-shape Eco 0.13.0.3 actually returns.

    Each entry is a minimal subset of the live-catalog response so the
    parser exercise is identical to production.
    """
    catalog = [
        {
            "Name": name,
            "DisplayName": name,
            "Unit": "PPM",
            "StatType": "ContinuousValue",
            "Tags": ["Climate"],
            "TimeKey": "_id",
        }
        for name in names
    ]
    respx.get(f"{_DEFAULT_BASE}/datasets/flatlist").mock(
        return_value=httpx.Response(200, json=catalog)
    )


# ---------------------------------------------------------------------------
# fetch_climate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_fetch_climate_parses_parallel_arrays_shape() -> None:
    """Eco 0.13.0.3 returns ``{Times,Values,Interval,Unit}`` for ContinuousValue.

    Regression for the empty-card bug: ``_fetch_dataset`` previously only
    accepted ``list`` payloads and dropped the dict-shape series silently.
    Verified live against ``http://eco.coilysiren.me:3001/datasets/get``
    on 2026-05-10.
    """
    _route_datasets_parallel(
        {
            "TotalCO2": [325.0, 325.0, 410.04, 520.0],
            "SeaLevel": [60.0, 60.0, 60.13, 61.0],
            "TotalGroundPollution": [0.0, 16.8, 629.4, 705.9],
            "AverageGlobalTemperature": [14.0, 14.2, 14.5, 14.8],
        },
        units={
            "TotalCO2": "PPM",
            "SeaLevel": "Meters",
            "TotalGroundPollution": "PPM",
            "AverageGlobalTemperature": "Celsius",
        },
    )
    _route_flatlist([])
    _route_worldlayers_empty()
    _route_climate_settings()
    _route_actions_empty()

    snap = await fetch_climate(
        None,
        info=_info(),
        days_elapsed=4,
        admin_token="test-token",
        default_admin_base=_DEFAULT_BASE,
    )

    assert snap.co2_dataset_name == "TotalCO2"
    assert [v for _, v in snap.co2_series] == [325.0, 325.0, 410.04, 520.0]
    assert snap.sea_level_dataset_name == "SeaLevel"
    assert [v for _, v in snap.sea_level_series] == [60.0, 60.0, 60.13, 61.0]
    assert snap.pollution_dataset_name == "TotalGroundPollution"
    assert [v for _, v in snap.pollution_series] == [0.0, 16.8, 629.4, 705.9]
    assert snap.temperature_dataset_name == "AverageGlobalTemperature"
    assert snap.co2_unit == "PPM"
    assert snap.sea_level_unit == "Meters"
    assert snap.pollution_unit == "PPM"
    assert snap.temperature_unit == "Celsius"
    assert snap.co2_interval_seconds == 86400.0
    assert snap.sea_level_interval_seconds == 86400.0
    assert snap.pollution_interval_seconds == 86400.0
    assert snap.temperature_interval_seconds == 86400.0
    assert snap.dataset_metadata["TotalGroundPollution"] == {
        "unit": "PPM",
        "interval_seconds": 86400.0,
    }


@pytest.mark.asyncio
@respx.mock
async def test_fetch_climate_picks_first_nonempty_candidate() -> None:
    """Of the 4 CO2 candidates, only ``AverageCO2PPM`` returns data."""
    _route_datasets({"AverageCO2PPM": [400, 410, 420]})
    _route_flatlist([])
    _route_worldlayers_empty()
    _route_climate_settings()
    _route_actions_empty()

    snap = await fetch_climate(
        None,
        info=_info(),
        days_elapsed=4,
        admin_token="test-token",
        default_admin_base=_DEFAULT_BASE,
    )

    assert snap.co2_dataset_name == "AverageCO2PPM"
    assert [v for _, v in snap.co2_series] == [400.0, 410.0, 420.0]
    # Sea level + ground pollution are not in the values map → series stay empty.
    assert snap.sea_level_series == []
    assert snap.pollution_series == []


@pytest.mark.asyncio
@respx.mock
async def test_fetch_climate_reads_live_climate_settings() -> None:
    """The telemetry mod's climate-settings endpoint populates the snapshot,
    and the computed effects use the live per-server thresholds — the retuned
    340 / 420 / 20 the in-game pollution machine reported, not Eco defaults."""
    _route_datasets({"TotalCO2": [430, 435]})
    _route_flatlist([])
    _route_worldlayers_empty()
    _route_climate_settings(
        {
            "minCo2Ppm": 340.0,
            "temperatureThresholdPpm": 420.0,
            "co2PpmPerDegree": 20.0,
            "seaLevelThresholdPpm": 420.0,
            "co2PpmPerMeter": 20.0,
            "pollutionMultiplier": 1.5,
        }
    )
    _route_actions_empty()

    snap = await fetch_climate(
        None,
        info=_info(),
        days_elapsed=4,
        admin_token="test-token",
        default_admin_base=_DEFAULT_BASE,
    )
    assert snap.climate_settings == {
        "minCo2Ppm": 340.0,
        "temperatureThresholdPpm": 420.0,
        "co2PpmPerDegree": 20.0,
        "seaLevelThresholdPpm": 420.0,
        "co2PpmPerMeter": 20.0,
        "pollutionMultiplier": 1.5,
    }

    eff = compute_climate_payload(snap)["effects"]
    assert eff["source"] == "live"
    assert eff["min_floor_ppm"] == 340.0
    assert eff["temperature"]["threshold_ppm"] == 420.0
    assert eff["temperature"]["ppm_per_degree"] == 20.0
    assert eff["sea_level"]["threshold_ppm"] == 420.0
    assert eff["pollution_multiplier"] == 1.5


@pytest.mark.asyncio
@respx.mock
async def test_fetch_climate_defaults_when_settings_endpoint_absent() -> None:
    """A server without the telemetry mod 404s the endpoint; the card falls
    back to the documented Eco defaults and flags the source as ``default``."""
    _route_datasets({"TotalCO2": [400, 410]})
    _route_flatlist([])
    _route_worldlayers_empty()
    _route_climate_settings(None)  # 404 — endpoint absent
    _route_actions_empty()

    snap = await fetch_climate(
        None,
        info=_info(),
        days_elapsed=4,
        admin_token="test-token",
        default_admin_base=_DEFAULT_BASE,
    )
    assert snap.climate_settings is None

    eff = compute_climate_payload(snap)["effects"]
    assert eff["source"] == "default"
    assert eff["temperature"]["threshold_ppm"] == 400.0
    assert eff["temperature"]["ppm_per_degree"] == 25.0
    # No live extras when the endpoint is absent.
    assert "pollution_multiplier" not in eff


@pytest.mark.asyncio
@respx.mock
async def test_fetch_climate_skips_admin_path_without_token() -> None:
    """No token → admin endpoints are never hit, only worldlayers is fetched."""
    ds_route = respx.get(_DATASET_URL).mock(return_value=httpx.Response(200, json=[]))
    fl_route = respx.get(f"{_DEFAULT_BASE}/datasets/flatlist").mock(
        return_value=httpx.Response(200, json=[])
    )
    wl_route = respx.get(_WORLDLAYERS_URL).mock(return_value=httpx.Response(200, json=[]))

    snap = await fetch_climate(
        None,
        info=_info(),
        days_elapsed=4,
        admin_token=None,
        default_admin_base=_DEFAULT_BASE,
    )

    assert snap.admin_ok is False
    assert ds_route.call_count == 0
    # Flatlist also requires admin perms in many setups; skipping it when no
    # token is consistent with the rest of the admin-gated path.
    assert fl_route.call_count == 0
    assert wl_route.called
    assert snap.co2_series == [] and snap.sea_level_series == []


@pytest.mark.asyncio
@respx.mock
async def test_fetch_climate_aggregates_polluter_attribution() -> None:
    _route_datasets({"CO2PPM": [400, 405]})
    _route_flatlist([])
    _route_worldlayers_empty()
    _route_climate_settings()
    # `PollutionAction` returns two rows; the rest 404.
    csv_body = (
        "Time,Citizen,Count,WorldObjectItem\n"
        "0,alice,5,GeneratorItem\n"
        "1,bob,3,GeneratorItem\n"
        "2,alice,2,SmelterItem\n"
    )
    base_actions = f"{_DEFAULT_BASE}/api/v1/exporter/actions"
    respx.get(f"{base_actions}?actionName=PollutionAction").mock(
        return_value=httpx.Response(200, text=csv_body)
    )
    for action in POLLUTION_ACTION_TYPES:
        if action == "PollutionAction":
            continue
        respx.get(f"{base_actions}?actionName={action}").mock(return_value=httpx.Response(404))

    snap = await fetch_climate(
        None,
        info=_info(),
        days_elapsed=4,
        admin_token="test-token",
        default_admin_base=_DEFAULT_BASE,
    )

    # alice: 5 + 2 = 7, bob: 3.
    assert snap.top_polluter_citizens[0] == ("alice", 7.0)
    assert snap.top_polluter_citizens[1] == ("bob", 3.0)
    # Stations: GeneratorItem 5+3=8, SmelterItem 2.
    assert snap.top_polluter_stations[0] == ("GeneratorItem", 8.0)
    assert snap.pollution_actions_total == 3
    assert "PollutionAction" in snap.pollution_action_types_seen
    # 404 is benign — the warnings list only carries non-404 problems.
    assert all("404" not in w for w in snap.warnings)


@pytest.mark.asyncio
@respx.mock
async def test_fetch_climate_caches_within_ttl() -> None:
    """Repeat calls within the TTL hit the cache, not the network."""
    _route_datasets_parallel({"TotalGroundPollution": [400]})
    _route_flatlist([])
    wl = respx.get(_WORLDLAYERS_URL).mock(return_value=httpx.Response(200, json=[]))
    _route_climate_settings()
    _route_actions_empty()

    first = await fetch_climate(
        None,
        info=_info(),
        days_elapsed=4,
        admin_token="test-token",
        default_admin_base=_DEFAULT_BASE,
    )
    second = await fetch_climate(
        None,
        info=_info(),
        days_elapsed=4,
        admin_token="test-token",
        default_admin_base=_DEFAULT_BASE,
    )
    # Worldlayers hit only once — second call short-circuited by the TTLCache.
    assert wl.call_count == 1
    assert first.pollution_unit == "PPM"
    assert second.pollution_interval_seconds == 86400.0


@pytest.mark.asyncio
@respx.mock
async def test_fetch_climate_falls_back_to_flatlist_discovery() -> None:
    """When no candidate matches but flatlist exposes a CO2-keyword name,
    the second-pass discovery probe finds it and returns its data."""
    # None of CO2_DATASET_CANDIDATES match the live name on this server.
    _route_datasets({"WorldCO2Level": [380, 410]})
    _route_flatlist(
        [
            "WorldCO2Level",  # CO2 match
            "TidalSeaLevelHeight",  # sea-level match
            "AirPollutionPerRegion",  # pollution match
            "OfferedLoanOrBond",  # noise — should be ignored
        ]
    )
    _route_worldlayers_empty()
    _route_climate_settings()
    _route_actions_empty()

    snap = await fetch_climate(
        None,
        info=_info(),
        days_elapsed=4,
        admin_token="test-token",
        default_admin_base=_DEFAULT_BASE,
    )

    # CO2 series populated via the discovery fallback, not the candidate list.
    assert snap.co2_dataset_name == "WorldCO2Level"
    assert [v for _, v in snap.co2_series] == [380.0, 410.0]
    # Catalog filtered to the climate-relevant subset (the noise name is gone).
    assert "OfferedLoanOrBond" not in snap.available_climate_datasets
    assert "WorldCO2Level" in snap.available_climate_datasets
    assert "TidalSeaLevelHeight" in snap.available_climate_datasets
    assert "AirPollutionPerRegion" in snap.available_climate_datasets


@pytest.mark.asyncio
@respx.mock
async def test_fetch_climate_parses_dict_shape_flatlist() -> None:
    """Eco 0.13.0.3 returns flatlist as a list of dicts with metadata.

    Real-world regression: the catalog leaked through as full ``{'Name': ...}``
    strings on the empty-state card because we were stringifying each entry
    instead of extracting ``Name``. Verify the canonical name is what we
    surface and what we probe.
    """
    _route_datasets({"TotalCO2": [400, 415]})
    _route_flatlist_dicts(
        [
            "TotalCO2",
            "SeaLevel",
            "TotalGroundPollution",
            "OfferedLoanOrBond",  # non-climate, must be filtered out
        ]
    )
    _route_worldlayers_empty()
    _route_climate_settings()
    _route_actions_empty()

    snap = await fetch_climate(
        None,
        info=_info(),
        days_elapsed=4,
        admin_token="test-token",
        default_admin_base=_DEFAULT_BASE,
    )

    # CO2 candidate hit on the first try (TotalCO2 is at the head now).
    assert snap.co2_dataset_name == "TotalCO2"
    # Clean names, not dict reprs.
    assert "TotalCO2" in snap.available_climate_datasets
    assert "SeaLevel" in snap.available_climate_datasets
    assert "TotalGroundPollution" in snap.available_climate_datasets
    assert not any("{" in n or "'Name'" in n for n in snap.available_climate_datasets)


@pytest.mark.asyncio
@respx.mock
async def test_fetch_climate_surfaces_catalog_when_all_series_empty() -> None:
    """Real-world failure mode: flatlist exposes climate names but every
    series returns empty (early cycle, or no industry yet). Card should
    name the available datasets so the empty state is debuggable."""
    # All explicit candidates AND the discovery-matched name return empty.
    respx.get(_DATASET_URL).mock(return_value=httpx.Response(200, json=[]))
    _route_flatlist(["CO2 PPM", "Pollution"])
    _route_worldlayers_empty()
    _route_climate_settings()
    _route_actions_empty()

    snap = await fetch_climate(
        None,
        info=_info(),
        days_elapsed=4,
        admin_token="test-token",
        default_admin_base=_DEFAULT_BASE,
    )

    assert snap.co2_series == []
    assert snap.available_climate_datasets == ["CO2 PPM", "Pollution"]

    payload = compute_climate_payload(snap)
    # Narrative explicitly references the catalog so users know the issue
    # is "no values yet", not "no climate stats at all".
    assert "CO2 PPM" in payload["narrative"]
    assert "exposes climate datasets" in payload["narrative"]


# ---------------------------------------------------------------------------
# compute_climate_payload
# ---------------------------------------------------------------------------


def _snap(**overrides: object) -> ClimateSnapshot:
    base = ClimateSnapshot(
        fetched_at_iso="2026-05-10T00:00:00+00:00",
        source_base_url=_DEFAULT_BASE,
        info={"Description": "Eco", "_sourceUrl": DEFAULT_ECO_INFO_URL},
        days_elapsed=4,
        admin_ok=True,
    )
    for k, v in overrides.items():
        setattr(base, k, v)
    return base


def test_payload_status_critical_at_500_ppm() -> None:
    snap = _snap(co2_series=[(0.0, 480.0), (1.0, 510.0)])
    p = compute_climate_payload(snap)
    assert p["status"] == "critical"
    # Real-world anchor still returns the most recent year, flagged as
    # "beyond present-day Earth".
    assert p["earth_match"]["year"] == EARTH_CO2_HISTORY[-1][0]


def test_payload_status_warming_above_350_ppm() -> None:
    snap = _snap(co2_series=[(0.0, 360.0), (1.0, 390.0)])
    p = compute_climate_payload(snap)
    assert p["status"] == "warming"


def test_payload_status_stable_below_threshold() -> None:
    snap = _snap(co2_series=[(0.0, 280.0), (1.0, 290.0)])
    p = compute_climate_payload(snap)
    assert p["status"] == "stable"


def test_payload_status_unknown_without_co2_series() -> None:
    p = compute_climate_payload(_snap())
    assert p["status"] == "unknown"
    assert p["co2"]["current"] is None


def test_payload_co2_change_pct_computed_from_first_to_last() -> None:
    snap = _snap(co2_series=[(0.0, 400.0), (1.0, 412.0)])
    p = compute_climate_payload(snap)
    # (412 - 400) / 400 = 3.0 %
    assert p["co2"]["change_pct"] == 3.0


def test_payload_sea_level_rate_per_day() -> None:
    snap = _snap(
        sea_level_series=[(0.0, 100.0), (1.0, 100.4)],
        days_elapsed=4,
    )
    p = compute_climate_payload(snap)
    # rate = (100.4 - 100) / 4 days = 0.1
    assert p["sea_level"]["rate_per_day"] == 0.1


def test_payload_pollution_falls_back_to_worldlayers_when_series_empty() -> None:
    snap = _snap(pollution_layer_summary="7%")
    p = compute_climate_payload(snap)
    assert p["pollution"]["current"] == 7.0
    assert p["pollution"]["source"] == "worldlayers"
    assert p["pollution"]["unit"] == "%"
    assert p["pollution"]["observation"]["freshness_state"] == "unknown"


def test_payload_pollution_prefers_live_series_over_layer() -> None:
    snap = _snap(
        pollution_series=[(0.0, 12.0), (1.0, 14.0)],
        pollution_layer_summary="7%",
        pollution_dataset_name="GroundPollution",
    )
    p = compute_climate_payload(snap)
    assert p["pollution"]["current"] == 14.0
    assert p["pollution"]["source"] == "GroundPollution"
    assert p["pollution"]["unit"] == "PPM"


def test_payload_pollution_source_is_current_on_current_game_day() -> None:
    snap = _snap(
        days_elapsed=28,
        pollution_series=[(27 * 86400.0, 243.7), (28 * 86400.0, 297.6)],
        pollution_dataset_name="TotalGroundPollution",
        pollution_unit="PPM",
        pollution_interval_seconds=86400.0,
    )
    pollution = compute_climate_payload(snap)["pollution"]
    assert pollution["unit"] == "PPM"
    assert pollution["observation"] == {
        "latest_game_time_seconds": 28 * 86400.0,
        "latest_game_day": 28.0,
        "current_game_day": 28,
        "interval_seconds": 86400.0,
        "lag_intervals": 0,
        "freshness_state": "current",
        "freshness_reason": "within_source_cadence",
    }


def test_payload_pollution_source_is_stale_when_behind_current_game_day() -> None:
    snap = _snap(
        days_elapsed=28,
        pollution_series=[(27 * 86400.0, 243.7)],
        pollution_dataset_name="TotalGroundPollution",
        pollution_unit="PPM",
        pollution_interval_seconds=86400.0,
    )
    observation = compute_climate_payload(snap)["pollution"]["observation"]
    assert observation["latest_game_day"] == 27.0
    assert observation["lag_intervals"] == 1
    assert observation["freshness_state"] == "stale"
    assert observation["freshness_reason"] == "behind_current_game_day"


def test_payload_pollution_source_freshness_unknown_without_cadence() -> None:
    snap = _snap(
        days_elapsed=28,
        pollution_series=[(28 * 86400.0, 297.6)],
        pollution_dataset_name="TotalGroundPollution",
        pollution_interval_seconds=None,
    )
    pollution = compute_climate_payload(snap)["pollution"]
    assert pollution["unit"] == "PPM"
    assert pollution["observation"]["latest_game_day"] == 28.0
    assert pollution["observation"]["freshness_state"] == "unknown"
    assert pollution["observation"]["freshness_reason"] == "unknown_cadence"


def test_payload_attribution_block_when_no_data() -> None:
    p = compute_climate_payload(_snap())
    assert p["attribution"]["has_data"] is False
    assert p["attribution"]["top_citizens"] == []


def test_payload_attribution_top_5_only() -> None:
    snap = _snap(
        top_polluter_citizens=[(f"player{i}", 10.0 - i) for i in range(8)],
    )
    p = compute_climate_payload(snap)
    assert len(p["attribution"]["top_citizens"]) == 5
    assert p["attribution"]["top_citizens"][0]["name"] == "player0"


def test_payload_admin_unavailable_narrative() -> None:
    p = compute_climate_payload(_snap(admin_ok=False))
    assert "Admin token unavailable" in p["narrative"]


# ---------------------------------------------------------------------------
# Verbose explainer: temperature, source/sink breakdown, CO2 effects
# ---------------------------------------------------------------------------


def test_payload_temperature_block_skips_leading_zero_baseline() -> None:
    """``AverageGlobalTemperature`` records 0 before the first real reading;
    the risen-since-start delta must measure from the first non-zero sample,
    not invent a bogus +15 jump."""
    snap = _snap(
        temperature_series=[(0.0, 0.0), (1.0, 14.0), (2.0, 14.9)],
        temperature_dataset_name="AverageGlobalTemperature",
    )
    p = compute_climate_payload(snap)
    assert p["temperature"]["current"] == 14.9
    # baseline is 14.0 (first non-zero), not 0.0 → risen 0.9, not 14.9.
    assert p["temperature"]["risen"] == 0.9


def test_payload_breakdown_sources_and_net() -> None:
    snap = _snap(
        co2_pollution_series=[(0.0, 0.0), (1.0, 100.0), (2.0, 150.0)],
        co2_animals_series=[(0.0, 0.0), (1.0, 10.0), (2.0, 25.0)],
        co2_plants_series=[(0.0, 0.0), (1.0, -300.0), (2.0, -500.0)],
    )
    p = compute_climate_payload(snap)
    b = p["breakdown"]
    assert b["has_data"] is True
    assert b["pollution"]["lifetime"] == 150.0
    assert b["plants"]["lifetime"] == -500.0
    # per-day = most recent day-over-day delta.
    assert b["pollution"]["per_day"] == 50.0
    assert b["plants"]["per_day"] == -200.0
    # net = 50 + 15 + (-200) = -135 → CO2 falling.
    assert b["net_per_day"] == -135.0


def test_payload_breakdown_absent_without_source_series() -> None:
    assert compute_climate_payload(_snap())["breakdown"] == {"has_data": False}


def test_payload_effects_thresholds_and_headroom() -> None:
    snap = _snap(
        co2_series=[(0.0, 325.0), (1.0, 520.0), (2.0, 325.0)],  # peaked then fell
        sea_level_series=[(0.0, 60.0), (1.0, 61.83)],
        temperature_series=[(0.0, 14.0), (1.0, 14.9)],
    )
    eff = compute_climate_payload(snap)["effects"]
    assert eff["co2_now"] == 325.0
    assert eff["co2_peak"] == 520.0
    assert eff["at_floor"] is True
    # default ruleset: warming starts at 400 ppm → 75 ppm of headroom.
    assert eff["temperature"]["headroom_ppm"] == 75.0
    assert eff["temperature"]["threshold_ppm"] == 400.0
    assert eff["sea_level"]["risen_m"] == 1.83


def test_resolve_rules_defaults_when_no_settings() -> None:
    rules = _resolve_rules(None)
    assert rules["source"] == "default"
    assert rules["temp_threshold_ppm"] == 400.0
    assert rules["ppm_per_degree"] == 25.0
    assert rules["min_co2_ppm"] == 325.0
    # Optional extras have no default — absent means None.
    assert rules["pollution_multiplier"] is None


def test_resolve_rules_partial_settings_fall_back_per_field() -> None:
    """A server that emits only some of the block gets live values for those
    fields and Eco defaults for the rest — the merge is per-field, not
    all-or-nothing."""
    rules = _resolve_rules({"temperatureThresholdPpm": 420.0, "co2PpmPerDegree": 20.0})
    assert rules["source"] == "live"  # at least one core field was live
    assert rules["temp_threshold_ppm"] == 420.0
    assert rules["ppm_per_degree"] == 20.0
    # Sea-level fields weren't emitted → Eco defaults.
    assert rules["sea_threshold_ppm"] == 400.0
    assert rules["ppm_per_meter"] == 25.0
    assert rules["min_co2_ppm"] == 325.0


def test_resolve_rules_ignores_non_numeric_and_bool() -> None:
    """Bogus/None/bool values are ignored so they can't poison a threshold."""
    rules = _resolve_rules(
        {"temperatureThresholdPpm": None, "co2PpmPerDegree": True, "minCo2Ppm": "nope"}
    )
    assert rules["source"] == "default"
    assert rules["temp_threshold_ppm"] == 400.0
    assert rules["ppm_per_degree"] == 25.0
    assert rules["min_co2_ppm"] == 325.0


def test_payload_effects_use_live_settings_thresholds() -> None:
    snap = _snap(
        co2_series=[(0.0, 340.0), (1.0, 460.0), (2.0, 340.0)],
        sea_level_series=[(0.0, 60.0), (1.0, 62.0)],
        temperature_series=[(0.0, 14.0), (1.0, 14.9)],
        climate_settings={
            "minCo2Ppm": 340.0,
            "temperatureThresholdPpm": 420.0,
            "co2PpmPerDegree": 20.0,
            "seaLevelThresholdPpm": 420.0,
            "co2PpmPerMeter": 20.0,
        },
    )
    eff = compute_climate_payload(snap)["effects"]
    assert eff["source"] == "live"
    assert eff["at_floor"] is True  # 340 == live floor
    # headroom = 420 - 340 = 80 (live threshold), not 75 (default).
    assert eff["temperature"]["headroom_ppm"] == 80.0
    # peak_drives_c = (460 - 420) / 20 = 2.0 using live rate.
    assert eff["temperature"]["peak_drives_c"] == 2.0


def test_explainer_tells_at_floor_recovery_story() -> None:
    snap = _snap(
        co2_series=[(0.0, 325.0), (1.0, 520.0), (2.0, 325.0)],
        sea_level_series=[(0.0, 60.0), (1.0, 61.83)],
        temperature_series=[(0.0, 14.0), (1.0, 14.9)],
        co2_pollution_series=[(0.0, 0.0), (1.0, 12000.0), (2.0, 12687.0)],
        co2_animals_series=[(0.0, 0.0), (1.0, 1400.0), (2.0, 1450.0)],
        co2_plants_series=[(0.0, 0.0), (1.0, -28000.0), (2.0, -28989.0)],
    )
    payload = compute_climate_payload(snap)
    joined = " ".join(payload["explainer"])
    assert "pinned to the simulation floor" in joined
    assert "headroom" in joined
    assert "peaked at 520 ppm" in joined

    # A value pinned at its floor is not falling. The explainer used to read
    # the source/sink flux as the level's rate of change and announce "CO2 is
    # falling about N ppm per day" over a series sitting flat at 325 (#235).
    assert "falling" not in joined
    assert "capacity to spare" in joined
    assert "not moving" in joined


def test_breakdown_separates_observed_movement_from_source_sink_flux() -> None:
    """`net_per_day` is a flux; the level's own movement is its own field (#235)."""
    snap = _snap(
        # Level flat at the floor across the last two samples.
        co2_series=[(0.0, 325.0), (1.0, 325.0), (2.0, 325.0)],
        co2_pollution_series=[(0.0, 0.0), (1.0, 12000.0), (2.0, 12687.0)],
        co2_animals_series=[(0.0, 0.0), (1.0, 1400.0), (2.0, 1450.0)],
        co2_plants_series=[(0.0, 0.0), (1.0, -28000.0), (2.0, -28989.0)],
    )
    breakdown = compute_climate_payload(snap)["breakdown"]
    # Sinks still outrun sources by a wide margin...
    assert breakdown["net_per_day"] < 0
    # ...while the observed level has not moved at all.
    assert breakdown["observed_per_day"] == 0.0


def test_observed_per_day_tracks_a_level_that_is_actually_moving() -> None:
    snap = _snap(
        co2_series=[(0.0, 400.0), (1.0, 405.0), (2.0, 412.0)],
        co2_pollution_series=[(0.0, 0.0), (1.0, 100.0), (2.0, 210.0)],
        co2_animals_series=[(0.0, 0.0), (1.0, 10.0), (2.0, 20.0)],
        co2_plants_series=[(0.0, 0.0), (1.0, -50.0), (2.0, -95.0)],
    )
    payload = compute_climate_payload(snap)
    assert payload["breakdown"]["observed_per_day"] == 7.0
    joined = " ".join(payload["explainer"])
    assert "climbing about 7.0 ppm per day" in joined


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_percent_change_handles_zero_baseline() -> None:
    assert _percent_change(0.0, 100.0) is None
    assert _percent_change(100.0, 110.0) == 10.0


def test_earth_year_for_ppm_returns_closest() -> None:
    # 339 ppm matches 1980 exactly.
    match = _earth_year_for_ppm(339.0)
    assert match is not None and match["year"] == 1980


def test_earth_year_for_ppm_flags_beyond_present() -> None:
    match = _earth_year_for_ppm(500.0)
    assert match is not None
    assert "beyond present-day Earth" in match["note"]


def test_earth_year_for_ppm_returns_none_far_below_baseline() -> None:
    assert _earth_year_for_ppm(50.0) is None


def test_parse_layer_pct() -> None:
    assert _parse_layer_pct("4%") == 4.0
    assert _parse_layer_pct("  12.5 %  ") == 12.5
    assert _parse_layer_pct(None) is None
    assert _parse_layer_pct("not a number") is None


def test_dataset_candidate_constants_are_nonempty() -> None:
    """Defensive: ensure we still ship some candidate names if a refactor
    accidentally empties the tuples."""
    assert CO2_DATASET_CANDIDATES
    assert SEA_LEVEL_DATASET_CANDIDATES
    assert POLLUTION_DATASET_CANDIDATES


def test_canonical_eco_0_13_names_in_candidates() -> None:
    """Live-catalog regression: the canonical Eco 0.13.0.3 names must be in
    the candidate lists so the first probe hits without needing flatlist."""
    assert "TotalCO2" in CO2_DATASET_CANDIDATES
    assert "SeaLevel" in SEA_LEVEL_DATASET_CANDIDATES
    assert "TotalGroundPollution" in POLLUTION_DATASET_CANDIDATES
    assert "PolluteAir" in POLLUTION_ACTION_TYPES


def test_parse_dataset_point_legacy_time_value_shape() -> None:
    assert _parse_dataset_point({"Time": 100, "Value": 1.5}) == (100.0, 1.5)
    assert _parse_dataset_point({"time": 100, "value": 1.5}) == (100.0, 1.5)


def test_parse_dataset_point_continuousvalue_id_shape() -> None:
    """Eco 0.13.0.3 ContinuousValue stats key the time on ``_id``."""
    assert _parse_dataset_point({"_id": 250, "Value": 414.2}) == (250.0, 414.2)


def test_parse_dataset_point_shortname_value_shape() -> None:
    """Some ContinuousValue stats put the value under the stat's ShortName
    (e.g. ``"tl"`` for ``TotalCO2``). We accept "the lone numeric field that
    isn't the time key" so the value still flows through."""
    pt = {"_id": 300, "tl": 416.5}
    assert _parse_dataset_point(pt) == (300.0, 416.5)


def test_parse_dataset_point_flat_pair_shape() -> None:
    assert _parse_dataset_point([100, 1.5]) == (100.0, 1.5)
    assert _parse_dataset_point((100, 1.5)) == (100.0, 1.5)


def test_parse_dataset_point_drops_invalid() -> None:
    assert _parse_dataset_point({"Value": 1.5}) is None  # no time key
    assert _parse_dataset_point({"Time": 100}) is None  # no value
    assert _parse_dataset_point("garbage") is None
    assert _parse_dataset_point(None) is None
    # Boolean fields must not be picked up as the numeric value.
    assert _parse_dataset_point({"_id": 1, "IsPaused": True}) is None


# ---------------------------------------------------------------------------
# MCP tool wiring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tools_includes_get_climate() -> None:
    mcp = build_server()
    handler = mcp.request_handlers[mt.ListToolsRequest]
    result = await handler(mt.ListToolsRequest(method="tools/list"))
    names = {tool.name for tool in result.root.tools}
    assert "get_climate" in names


@pytest.mark.asyncio
@respx.mock
async def test_call_get_climate_returns_iframe_fragment() -> None:
    respx.get(DEFAULT_ECO_INFO_URL).mock(return_value=httpx.Response(200, json=_info()))
    _route_datasets(
        {
            "CO2PPM": [400, 405, 410, 415],
            "SeaLevel": [100.0, 100.05, 100.1, 100.15],
            "GroundPollution": [3, 4, 5, 6],
        }
    )
    _route_flatlist([])
    _route_worldlayers_empty()
    _route_climate_settings()
    _route_actions_empty()

    mcp = build_server()
    handler = mcp.request_handlers[mt.CallToolRequest]
    req = mt.CallToolRequest(
        method="tools/call",
        params=mt.CallToolRequestParams(name="get_climate", arguments={}),
    )
    result = await handler(req)
    blocks = result.root.content
    assert len(blocks) == 2
    assert isinstance(blocks[0], mt.TextContent)
    assert isinstance(blocks[1], mt.TextContent)

    # Markdown mentions the headline.
    md = blocks[0].text
    assert "climate" in md.lower()
    assert "ppm" in md.lower()
    assert "Ground pollution: **6.0 PPM**" in md
    assert "Ground pollution: **6.0%**" not in md
    assert "source cadence is unavailable" in md

    # JSON block is the computed payload (what the SPA Climate page consumes),
    # not the raw snapshot — carries the KPI blocks with dataset names.
    payload = json.loads(blocks[1].text)
    assert payload["co2"]["dataset_name"] == "CO2PPM"
    assert payload["sea_level"]["dataset_name"] == "SeaLevel"
    assert payload["pollution"]["dataset_name"] == "GroundPollution"
    assert payload["pollution"]["unit"] == "PPM"
    assert payload["pollution"]["observation"]["freshness_state"] == "unknown"
    assert payload["status"] in {"stable", "warming", "critical", "unknown"}
    assert "explainer" in payload

    # Just-data per eco-app#87: get_climate no longer emits a widget.
    assert result.root.meta is None


@pytest.mark.asyncio
@respx.mock
async def test_call_get_climate_handles_info_failure() -> None:
    respx.get(DEFAULT_ECO_INFO_URL).mock(side_effect=httpx.ConnectError("refused"))
    mcp = build_server()
    handler = mcp.request_handlers[mt.CallToolRequest]
    req = mt.CallToolRequest(
        method="tools/call",
        params=mt.CallToolRequestParams(name="get_climate", arguments={}),
    )
    result = await handler(req)
    assert result.root.isError is True
    assert "unreachable" in result.root.content[0].text.lower()
    # Just-data per eco-app#87: get_climate no longer emits a widget.
    assert result.root.meta is None


# ---------------------------------------------------------------------------
# Map pollution-overlay extension
# ---------------------------------------------------------------------------


_TINY_GIF = bytes.fromhex(
    "47494638396101000100800000000000ffffff21f90401000000002c000000000100010000020144003b"
)


@pytest.mark.asyncio
@respx.mock
async def test_map_bundle_includes_pollution_overlay_when_present() -> None:
    base = "http://eco.coilysiren.me:3001"
    respx.get(f"{base}/api/v1/map/dimension").mock(
        return_value=httpx.Response(200, json={"x": 720, "y": 200, "z": 720})
    )
    respx.get(f"{base}/api/v1/map/property").mock(return_value=httpx.Response(200, json={}))
    respx.get(f"{base}/Layers/WorldPreview.gif").mock(
        return_value=httpx.Response(200, content=_TINY_GIF)
    )
    respx.get(f"{base}/Layers/Pollution.gif").mock(
        return_value=httpx.Response(200, content=_TINY_GIF)
    )

    bundle = await eco_map.fetch_map_bundle()
    assert bundle["pollution_gif"] == _TINY_GIF
    payload = eco_map.build_map_payload(bundle)
    assert payload["pollutionDataUri"] is not None
    assert payload["pollutionDataUri"].startswith("data:image/gif;base64,")


@pytest.mark.asyncio
@respx.mock
async def test_map_bundle_omits_pollution_overlay_when_404() -> None:
    """Pollution.gif 404 is normal — the map renders fine without it."""
    base = "http://eco.coilysiren.me:3001"
    respx.get(f"{base}/api/v1/map/dimension").mock(
        return_value=httpx.Response(200, json={"x": 720, "y": 200, "z": 720})
    )
    respx.get(f"{base}/api/v1/map/property").mock(return_value=httpx.Response(200, json={}))
    respx.get(f"{base}/Layers/WorldPreview.gif").mock(
        return_value=httpx.Response(200, content=_TINY_GIF)
    )
    respx.get(f"{base}/Layers/Pollution.gif").mock(return_value=httpx.Response(404))

    bundle = await eco_map.fetch_map_bundle()
    assert bundle["pollution_gif"] is None
    payload = eco_map.build_map_payload(bundle)
    assert payload["pollutionDataUri"] is None


# ---------------------------------------------------------------------------
# Pollution attribution (eco-app#226)
# ---------------------------------------------------------------------------


def test_unattributed_pollution_rows_are_not_named_a_station() -> None:
    """`top_stations: [{"(unknown)": 6858.75}]` was one fake bucket (#226).

    Falling back to a literal "(unknown)" station name put the whole server's
    emissions into a single row that reads as a real station, and the value —
    summed emissions — sat under a `count` key, so a fractional "count" of
    6,858.75 against 816 actions looked like a broken counter.
    """
    snap = _snap(co2_series=[(0.0, 400.0), (1.0, 400.0)])
    snap.pollution_actions_total = 816
    snap.pollution_action_types_seen = ["PolluteAir"]
    snap.unattributed_polluter_rows = 816
    snap.unattributed_station_rows = 816
    snap.warnings.append("816 pollution row(s) carry no recognised actor column (tried Citizen)")

    attribution = compute_climate_payload(snap)["attribution"]
    assert attribution["top_citizens"] == []
    assert attribution["top_stations"] == []
    # has_data no longer claims attribution exists when nothing resolved.
    assert attribution["has_data"] is False
    assert attribution["unattributed_rows"] == {"citizen": 816, "station": 816}


def test_attributed_pollution_reports_emissions_not_counts() -> None:
    snap = _snap(co2_series=[(0.0, 400.0), (1.0, 400.0)])
    snap.pollution_actions_total = 2
    snap.top_polluter_citizens = [("coilysiren", 12.5)]
    snap.top_polluter_stations = [("BlastFurnaceItem", 6858.75)]

    attribution = compute_climate_payload(snap)["attribution"]
    assert attribution["has_data"] is True
    # A fractional value is expected under `emissions`; it was not under `count`.
    assert attribution["top_stations"][0] == {"name": "BlastFurnaceItem", "emissions": 6858.75}
    assert attribution["top_citizens"][0] == {"name": "coilysiren", "emissions": 12.5}
    assert "count" not in attribution["top_stations"][0]


def test_thin_series_keeps_the_endpoints_and_says_so() -> None:
    """The curve-shaped sibling of _bound_rows, for get_climate's seven series.

    A head slice of a series reports the shape of day one and calls it the
    trend, so these thin by even spacing with the true endpoints preserved.
    See eco-app#6076.
    """
    points = [[float(i), float(i * 2)] for i in range(100)]
    payload: dict[str, Any] = {"co2Series": list(points), "seaLevelSeries": list(points)}

    eco_server._thin_series(payload, 10, "co2Series", "seaLevelSeries")

    for key in ("co2Series", "seaLevelSeries"):
        assert len(payload[key]) == 10
        assert payload[key][0] == points[0], "the first sample must stay true"
        assert payload[key][-1] == points[-1], "the last sample must stay true"
        warning = next(w for w in payload["warnings"] if w.startswith(f"{key}:"))
        assert "10 evenly-spaced samples of 100" in warning
        assert "endpoints preserved" in warning


def test_thin_series_leaves_a_short_curve_alone() -> None:
    """The negative control: under the limit, untouched and unannounced."""
    points = [[0.0, 1.0], [1.0, 2.0]]
    payload: dict[str, Any] = {"co2Series": list(points)}

    eco_server._thin_series(payload, 10, "co2Series")

    assert payload["co2Series"] == points
    assert "warnings" not in payload


def test_thin_series_at_limit_zero_is_a_no_op() -> None:
    """limit=0 means every sample, matching _bound_rows and the schema text."""
    points = [[float(i), float(i)] for i in range(50)]
    payload: dict[str, Any] = {"co2Series": list(points)}

    eco_server._thin_series(payload, 0, "co2Series")

    assert payload["co2Series"] == points
