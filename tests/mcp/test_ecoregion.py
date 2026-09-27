"""Tests for the biodiversity + ecoregion-match tool.

Uses respx to stub the public worldlayers endpoint and the admin exporter
routes, so we verify:

  - biome percentages parse out of the categorized response
  - the vector normalizes to sum=1.0 (acceptance)
  - cosine similarity is deterministic + sensible (acceptance)
  - drift ranking handles empty / sparse series without crashing
  - the full MCP tool call handles a missing API key gracefully
"""

from __future__ import annotations

import asyncio
import json
import math
import time

import httpx
import mcp.types as mt
import pytest
import respx

from eco_mcp_app import ecoregion as eco
from eco_mcp_app.server import (
    DEFAULT_ECO_INFO_URL,
    _format_ecoregion_markdown,
    build_server,
)


@pytest.fixture(autouse=True)
def _clear_caches() -> None:
    eco._clear_caches()


# ---- pure-function tests ----


def test_extract_biome_percents_picks_only_biome_category() -> None:
    cats = [
        {
            "Category": "Biome",
            "List": [
                {"LayerName": "TaigaBiome", "Summary": "1%"},
                {"LayerName": "ForestBiome", "Summary": "4.5%"},
                {"LayerName": "UnknownBiome", "Summary": "99%"},
            ],
        },
        {
            "Category": "Animal",
            "List": [{"LayerName": "TaigaBiome", "Summary": "50%"}],
        },
    ]
    out = eco.extract_biome_percents(cats)
    assert out["TaigaBiome"] == 1.0
    assert out["ForestBiome"] == 4.5
    # Every expected biome key is present, even when absent from the response.
    assert set(out) == set(eco.BIOME_LAYERS)
    # Animal-category rows must not leak through.
    assert out["DesertBiome"] == 0.0


def test_extract_water_percents_reads_world_and_moisture() -> None:
    cats = [
        {
            "Category": "World",
            "List": [
                {"LayerName": "SaltWater", "Summary": "57%"},
                {"LayerName": "Height", "Summary": "59.89 meters above bedrock"},
                {"LayerName": "CanopySpace", "Summary": "6.16"},
            ],
        },
        {
            "Category": "Moisture",
            "List": [
                {"LayerName": "FreshWater", "Summary": "5%"},
                {"LayerName": "Rainfall", "Summary": "50%"},
            ],
        },
    ]
    water = eco.extract_water_percents(cats)
    assert water["saltwater"] == 57.0
    assert water["freshwater"] == 5.0
    # A non-percentage summary (bare float / "N meters") never leaks in.
    assert eco._layer_percent(cats, "World", "Height") == 0.0
    assert eco._layer_percent(cats, "World", "CanopySpace") == 0.0
    # Missing category / layer degrades to 0.0, not a crash.
    assert eco.extract_water_percents([]) == {"saltwater": 0.0, "freshwater": 0.0}


def test_build_payload_reclassifies_water_into_named_slices() -> None:
    # Live-shaped biome mix (#82): Ocean 13 + DeepOcean 8 + land ~18 = ~39% of
    # world named as biome, leaving ~61% grey. SaltWater 57 + FreshWater 5
    # reclassify most of that gap into coastal-water + fresh-water slices.
    biomes = dict.fromkeys(eco.BIOME_LAYERS, 0.0)
    biomes.update(
        {
            "OceanBiome": 13.0,
            "DeepOceanBiome": 8.0,
            "GrasslandBiome": 7.0,
            "RainforestBiome": 3.0,
            "WarmForestBiome": 3.0,
            "DesertBiome": 2.0,
            "ColdForestBiome": 1.0,
            "TaigaBiome": 1.0,
            "WetlandBiome": 1.0,
        },
    )
    payload = eco.build_payload(
        biomes,
        matches=[],
        boom=[],
        bust=[],
        species_seen=0,
        species_with_drift=0,
        admin_available=False,
        source_url="http://eco.example.com:3001/info",
        saltwater_percent=57.0,
        freshwater_percent=5.0,
    )
    raw_sum = 13 + 8 + 7 + 3 + 3 + 2 + 1 + 1 + 1  # 39
    assert math.isclose(payload["rawSumPercent"], raw_sum)
    # Coastal water = SaltWater(57) - Ocean+DeepOcean biome(21) = 36.
    coastal = next(b for b in payload["biomes"] if b["name"] == "CoastalWater")
    assert math.isclose(coastal["percent"], 36.0)
    fresh = next(b for b in payload["biomes"] if b["name"] == "FreshWater")
    assert math.isclose(fresh["percent"], 5.0)
    # Classified now credits the water slices; the grey remainder is ~20%, not 61%.
    assert math.isclose(payload["classifiedPercent"], raw_sum + 36 + 5)
    assert math.isclose(payload["unclassifiedPercent"], 100 - (raw_sum + 36 + 5))
    # Water slices sit outside the WWF vector, so they carry no share weight.
    assert coastal["sharePercent"] == 0.0


def test_build_payload_without_water_matches_pre_82_behaviour() -> None:
    """Passing no water percents leaves the biome-only classification unchanged."""
    biomes = dict.fromkeys(eco.BIOME_LAYERS, 0.0)
    biomes["OceanBiome"] = 13.0
    biomes["GrasslandBiome"] = 8.0
    payload = eco.build_payload(
        biomes,
        matches=[],
        boom=[],
        bust=[],
        species_seen=0,
        species_with_drift=0,
        admin_available=False,
        source_url="x",
    )
    assert math.isclose(payload["rawSumPercent"], 21.0)
    assert math.isclose(payload["classifiedPercent"], 21.0)
    assert math.isclose(payload["unclassifiedPercent"], 79.0)
    # Water slices are present but zero, so the donut simply skips them.
    coastal = next(b for b in payload["biomes"] if b["name"] == "CoastalWater")
    assert coastal["percent"] == 0.0


def test_normalize_vector_sums_to_one() -> None:
    raw = {"A": 10.0, "B": 30.0, "C": 60.0}
    norm = eco.normalize_vector(raw)
    assert math.isclose(sum(norm.values()), 1.0)
    assert math.isclose(norm["B"], 0.3)


def test_normalize_vector_zero_input_returns_zero() -> None:
    raw = {"A": 0.0, "B": 0.0}
    norm = eco.normalize_vector(raw)
    assert all(v == 0.0 for v in norm.values())


def test_cosine_similarity_identical_vectors_is_one() -> None:
    a = {"X": 0.3, "Y": 0.7}
    assert math.isclose(eco.cosine_similarity(a, a), 1.0)


def test_cosine_similarity_orthogonal_is_zero() -> None:
    a = {"X": 1.0, "Y": 0.0}
    b = {"X": 0.0, "Y": 1.0}
    assert eco.cosine_similarity(a, b) == 0.0


def test_top_ecoregions_is_deterministic_and_ranked() -> None:
    # Craft a world that's obviously desert-y.
    normalized = eco.normalize_vector(
        {
            "DesertBiome": 60.0,
            "GrasslandBiome": 20.0,
            "OceanBiome": 10.0,
            "TaigaBiome": 0.0,
        }
    )
    regions = [
        {
            "name": "Sahara",
            "description": "desert",
            "biome_vector": {"DesertBiome": 0.9, "GrasslandBiome": 0.1},
        },
        {
            "name": "Taiga",
            "description": "conifer",
            "biome_vector": {"TaigaBiome": 1.0},
        },
        {
            "name": "Grassland",
            "description": "plain",
            "biome_vector": {"GrasslandBiome": 0.8, "DesertBiome": 0.2},
        },
    ]
    matches = eco.top_ecoregions(normalized, regions, n=3)
    # Sahara should beat Grassland should beat Taiga.
    assert [m.name for m in matches] == ["Sahara", "Grassland", "Taiga"]
    # Deterministic across calls.
    again = eco.top_ecoregions(normalized, regions, n=3)
    assert [m.name for m in again] == [m.name for m in matches]


def test_compute_drift_handles_single_sample() -> None:
    assert eco.compute_drift([(0, 100.0)]) is None


def test_compute_drift_relative_delta() -> None:
    d = eco.compute_drift([(0, 100.0), (600, 150.0)])
    assert d is not None
    assert math.isclose(d.delta_rel, 0.5)
    assert d.first == 100.0
    assert d.latest == 150.0


def test_compute_drift_sorts_by_time() -> None:
    d = eco.compute_drift([(1200, 80.0), (0, 100.0), (600, 90.0)])
    assert d is not None
    assert d.first == 100.0
    assert d.latest == 80.0


def test_rank_drift_splits_boom_and_bust() -> None:
    series = {
        "Rising": [(0, 100.0), (600, 200.0)],
        "Falling": [(0, 100.0), (600, 50.0)],
        "Flat": [(0, 100.0), (600, 100.0)],
    }
    boom, bust = eco.rank_drift(series, n=5)
    assert [d.name for d in boom] == ["Rising"]
    assert [d.name for d in bust] == ["Falling"]


def test_drift_entry_serializes_from_zero_grower_as_json_safe() -> None:
    # first == 0, latest > 0 → infinite relative delta. Must not leak `inf`
    # into the payload (Starlette's JSONResponse rejects it) — emit
    # deltaRel=None + fromZero=True instead.
    d = eco.compute_drift([(0, 0.0), (600, 40.0)])
    assert d is not None
    assert math.isinf(d.delta_rel)
    entry = eco._drift_entry(d)
    assert entry["deltaRel"] is None
    assert entry["fromZero"] is True
    # The payload has to survive json.dumps with allow_nan=False (Starlette).
    json.dumps(entry, allow_nan=False)


def test_species_risk_distinguishes_decline_stability_recovery_and_sparse() -> None:
    series = {
        "DecliningSpecies": [(0, 100.0), (2000, 80.0), (4000, 60.0), (6000, 40.0)],
        "StableSpecies": [(0, 100.0), (2000, 102.0), (4000, 101.0), (6000, 100.0)],
        "RecoveringSpecies": [(0, 100.0), (2000, 75.0), (4000, 50.0), (6000, 70.0)],
        "SparseSpecies": [(0, 10.0), (2000, 11.0), (4000, 10.0), (6000, 10.0)],
    }
    rows = {row.name: row for row in eco.classify_species_risk(series)}

    assert rows["DecliningSpecies"].state == "at_risk"
    assert rows["DecliningSpecies"].warning is True
    assert rows["StableSpecies"].state == "stable"
    assert rows["RecoveringSpecies"].state == "recovering"
    assert rows["SparseSpecies"].state == "naturally_sparse"
    assert rows["SparseSpecies"].warning is False


def test_species_risk_does_not_call_growth_a_decline() -> None:
    """The classifier tests the signed recent change, not its magnitude (#220).

    It used to bucket on `abs(recentChangePct)`, so Huckleberry (+425% over the
    cycle, +28% in the recent window) and Tomatoes (+635% / +74.5%) came back
    `declining` with the reason "Population is declining". This is the field a
    reader uses to decide what to protect, and it was naming the winners.
    """
    series = {
        # Up sharply in the recent window — the Huckleberry shape.
        "HuckleberrySpecies": [(0, 20.0), (2000, 60.0), (4000, 82.0), (6000, 105.0)],
        # Down sharply in the recent window — must stay `declining`.
        "FadingSpecies": [(0, 100.0), (2000, 130.0), (4000, 125.0), (6000, 90.0)],
    }
    rows = {row.name: row for row in eco.classify_species_risk(series)}

    assert rows["HuckleberrySpecies"].recent_change_pct is not None
    assert rows["HuckleberrySpecies"].recent_change_pct > 0
    assert rows["HuckleberrySpecies"].state == "growing"
    assert rows["HuckleberrySpecies"].warning is False
    assert "declining" not in rows["HuckleberrySpecies"].reason.lower()

    assert rows["FadingSpecies"].recent_change_pct is not None
    assert rows["FadingSpecies"].recent_change_pct < 0
    assert rows["FadingSpecies"].state == "declining"


def test_species_risk_flat_recent_window_stays_stable() -> None:
    # Movement inside the band either way is `stable`, not `growing`.
    series = {"SteadySpecies": [(0, 100.0), (2000, 104.0), (4000, 103.0), (6000, 106.0)]}
    rows = {row.name: row for row in eco.classify_species_risk(series)}
    assert rows["SteadySpecies"].state == "stable"


def test_stable_reason_names_a_large_cycle_decline() -> None:
    """Daisy: -58% across the cycle, -2% recently. The bucket was right, the caption was not.

    A flat recent window keeps it out of `declining`, but reporting it as
    plainly "stable" hid that the population is less than half what it was
    (#220).
    """
    series = {"DaisySpecies": [(0, 240.0), (2000, 140.0), (4000, 102.0), (6000, 100.0)]}
    row = next(iter(eco.classify_species_risk(series)))
    assert row.state == "stable"
    assert row.change_pct is not None and row.change_pct <= -0.30
    assert "down across the cycle" in row.reason


def test_species_risk_marks_missing_stale_and_thin_data_insufficient() -> None:
    series = {
        "CurrentSpecies": [(0, 100.0), (2000, 100.0), (4000, 100.0), (6000, 100.0)],
        "StaleSpecies": [(0, 100.0), (600, 90.0), (1200, 80.0), (1800, 70.0)],
        "ThinSpecies": [(5000, 20.0), (6000, 10.0)],
    }
    rows = {
        row.name: row
        for row in eco.classify_species_risk(
            series,
            expected_species=["CurrentSpecies", "StaleSpecies", "ThinSpecies", "MissingSpecies"],
        )
    }

    assert rows["StaleSpecies"].state == "stale"
    assert rows["ThinSpecies"].state == "insufficient"
    assert rows["MissingSpecies"].state == "missing"
    assert not any(rows[name].warning for name in ("StaleSpecies", "ThinSpecies", "MissingSpecies"))


def test_load_ecoregions_bundled_returns_committed_fixture() -> None:
    regions = eco._load_ecoregions_bundled()
    # At least a handful of regions committed so the match section always
    # renders at least top-3.
    assert len(regions) >= 3
    # Every entry has the shape the payload builder assumes.
    for r in regions:
        assert "name" in r and "biome_vector" in r


# ---- integration through the MCP tool surface ----


@respx.mock
async def test_gather_ecoregion_payload_public_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """No API key available → drift section renders an empty state, tool still succeeds."""
    monkeypatch.delenv("ECO_ADMIN_TOKEN", raising=False)
    respx.get("http://eco.coilysiren.me:3001/api/v1/worldlayers/layers").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "Category": "Biome",
                    "List": [
                        {"LayerName": "OceanBiome", "Summary": "13%"},
                        {"LayerName": "ForestBiome", "Summary": "4%"},
                        {"LayerName": "DesertBiome", "Summary": "4%"},
                    ],
                }
            ],
        )
    )
    payload = await eco.gather_ecoregion_payload(DEFAULT_ECO_INFO_URL, api_key=None)
    assert payload["view"] == "eco_ecoregion"
    ocean = next(b for b in payload["biomes"] if b["name"] == "OceanBiome")
    assert ocean["percent"] == 13.0
    assert math.isclose(payload["rawSumPercent"], 21.0)
    assert math.isclose(payload["unclassifiedPercent"], 79.0)
    assert payload["adminAvailable"] is False
    assert payload["drift"]["boom"] == []
    assert payload["drift"]["bust"] == []


@respx.mock
async def test_gather_ecoregion_payload_with_admin() -> None:
    respx.get("http://eco.coilysiren.me:3001/api/v1/worldlayers/layers").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "Category": "Biome",
                    "List": [
                        {"LayerName": "ForestBiome", "Summary": "10%"},
                        {"LayerName": "GrasslandBiome", "Summary": "5%"},
                    ],
                }
            ],
        )
    )
    respx.get("http://eco.coilysiren.me:3001/api/v1/exporter/specieslist").mock(
        return_value=httpx.Response(200, text="Deer\nWolf\n\nRabbit\n"),
    )
    respx.get(
        "http://eco.coilysiren.me:3001/api/v1/exporter/species",
        params={"speciesName": "Deer"},
    ).mock(
        return_value=httpx.Response(
            200,
            text='"Time","Value"\n"0","100"\n"600","200"\n',
        )
    )
    respx.get(
        "http://eco.coilysiren.me:3001/api/v1/exporter/species",
        params={"speciesName": "Wolf"},
    ).mock(
        return_value=httpx.Response(
            200,
            text='"Time","Value"\n"0","50"\n"600","25"\n',
        )
    )
    respx.get(
        "http://eco.coilysiren.me:3001/api/v1/exporter/species",
        params={"speciesName": "Rabbit"},
    ).mock(
        return_value=httpx.Response(
            200,
            text='"Time","Value"\n"0","200"\n"600","200"\n',
        )
    )
    payload = await eco.gather_ecoregion_payload(DEFAULT_ECO_INFO_URL, api_key="test-token")
    assert payload["adminAvailable"] is True
    assert payload["drift"]["speciesSeen"] == 3
    boom_names = [d["name"] for d in payload["drift"]["boom"]]
    bust_names = [d["name"] for d in payload["drift"]["bust"]]
    assert "Deer" in boom_names
    assert "Wolf" in bust_names
    assert "Rabbit" not in boom_names and "Rabbit" not in bust_names


@respx.mock
async def test_gather_ecoregion_handles_admin_403() -> None:
    """A 4xx on the admin endpoint should degrade, not crash."""
    respx.get("http://eco.coilysiren.me:3001/api/v1/worldlayers/layers").mock(
        return_value=httpx.Response(
            200,
            json=[{"Category": "Biome", "List": [{"LayerName": "OceanBiome", "Summary": "13%"}]}],
        )
    )
    respx.get("http://eco.coilysiren.me:3001/api/v1/exporter/specieslist").mock(
        return_value=httpx.Response(403, text="forbidden")
    )
    payload = await eco.gather_ecoregion_payload(DEFAULT_ECO_INFO_URL, api_key="bad-token")
    assert payload["adminAvailable"] is False
    assert payload["drift"]["boom"] == []


def test_format_ecoregion_markdown_smoke() -> None:
    """The data-only summary handles a minimal payload."""
    payload = {
        "view": "eco_ecoregion",
        "sourceUrl": "http://eco.example.com:3001/info",
        "biomes": [
            {"name": "OceanBiome", "display": "Ocean", "percent": 13.0, "color": "#4a9cb8"},
            {"name": "ForestBiome", "display": "Forest", "percent": 4.0, "color": "#5a8a3a"},
        ],
        "unclassifiedPercent": 83.0,
        "rawSumPercent": 17.0,
        "ecoregionMatches": [
            {"name": "Indo-Pacific archipelago", "description": "islands", "similarity": 0.82},
        ],
        "drift": {
            "boom": [],
            "bust": [],
            "speciesSeen": 0,
            "speciesWithDrift": 0,
        },
        "adminAvailable": False,
    }
    markdown = _format_ecoregion_markdown(payload)
    assert "Biodiversity" in markdown
    assert "Indo-Pacific archipelago" in markdown
    assert "Admin endpoints unavailable" in markdown


@respx.mock
async def test_mcp_tool_call_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ECO_ADMIN_TOKEN", raising=False)
    respx.get("http://eco.coilysiren.me:3001/api/v1/worldlayers/layers").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "Category": "Biome",
                    "List": [{"LayerName": "OceanBiome", "Summary": "13%"}],
                }
            ],
        )
    )
    mcp = build_server()
    handler = mcp.request_handlers[mt.CallToolRequest]
    req = mt.CallToolRequest(
        method="tools/call",
        params=mt.CallToolRequestParams(name="get_region", arguments={}),
    )
    result = await handler(req)
    blocks = result.root.content
    assert len(blocks) == 2
    assert isinstance(blocks[0], mt.TextContent)
    assert isinstance(blocks[1], mt.TextContent)
    md = blocks[0].text
    payload = json.loads(blocks[1].text)
    assert "Biome composition" in md
    assert payload["view"] == "eco_ecoregion"
    assert result.root.meta is None


# The species fan-out stays inside one budget and says what it dropped (#8321).
async def test_species_fetch_counts_what_the_budget_ran_out_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake(_base: str, name: str, _key: str) -> list[tuple[int, float]]:
        if name == "Slow":
            await asyncio.sleep(5)
        return [(0, 1.0), (600, 2.0)]

    monkeypatch.setattr(eco, "fetch_species_samples", fake)
    monkeypatch.setattr(eco, "_SPECIES_BUDGET_S", 0.2)
    started = time.monotonic()
    series, unfetched = await eco._fetch_species_series("http://x", ["Deer", "Slow", "Wolf"], "k")
    assert time.monotonic() - started < 1.0
    assert list(series) == ["Deer", "Wolf"]
    assert unfetched == 1


async def test_species_fetch_runs_concurrently(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(_base: str, _name: str, _key: str) -> list[tuple[int, float]]:
        await asyncio.sleep(0.1)
        return [(0, 1.0)]

    monkeypatch.setattr(eco, "fetch_species_samples", fake)
    names = [f"S{i}" for i in range(16)]
    started = time.monotonic()
    series, unfetched = await eco._fetch_species_series("http://x", names, "k")
    # Sequential would be 1.6s. Eight at a time is two waves.
    assert time.monotonic() - started < 0.8
    assert len(series) == 16 and unfetched == 0
