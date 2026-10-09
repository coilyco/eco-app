"""The SPA's own routes carry the per-player boards (COI-2049, COI-2048).

The tool-handler tests in test_crafting.py and test_world.py reach the payload
through the MCP transport. The SPA reads `/preview/*.json?limit=0` through the
dual-route registry instead, so this drives that consumer with the real server
dispatch and only the exporter fetchers stubbed with objects folded from CSV.
"""

from __future__ import annotations

import csv
from typing import Any

import pytest
from fastapi.testclient import TestClient

from eco_mcp_app import server as eco_server
from eco_mcp_app.crafting import CraftingAtlas, aggregate_rows, rank_citizen_counts
from eco_mcp_app.http_app import create_app
from eco_mcp_app.world import (
    WorldAccumulator,
    aggregate_world_rows,
    apply_citizen_names,
    finalize,
)

NAMES = {"1": "ekans", "2": "redwood", "3": "salt"}

_DIG = "BlockItemOnDestroy,Citizen,Count,Time\n" + "".join(
    f'"DirtItem",{cid},1.0,{t}\n' for t, cid in enumerate([2, 2, 2, 1, 1, 3], 100)
)
_ROADS = "Block,Citizen,ActionLocation,Count,Time\n" + "".join(
    f'"DirtRoadItem",{cid},"1,2,3",1.0,{t}\n' for t, cid in enumerate([1, 1, 1, 3], 100)
)


def _rows(text: str) -> list[list[str]]:
    return list(csv.reader(text.splitlines()))


def _atlas() -> CraftingAtlas:
    atlas = CraftingAtlas(fetched_at_iso="t", source_base_url="u")
    aggregate_rows("DigOrMine", _rows(_DIG), atlas)
    atlas.by_miner = rank_citizen_counts(dict(atlas.by_miner), NAMES)
    return atlas


def _world() -> Any:
    acc = WorldAccumulator()
    aggregate_world_rows("TampRoad", "roads", _rows(_ROADS), acc)
    apply_citizen_names(acc, NAMES)
    return finalize(acc, "t", "u")


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("ECO_ADMIN_API_KEY", "k")

    async def atlas(**_: Any) -> CraftingAtlas:
        return _atlas()

    async def world(**_: Any) -> Any:
        return _world()

    monkeypatch.setattr(eco_server, "fetch_atlas", atlas)
    monkeypatch.setattr(eco_server, "fetch_world", world)
    return TestClient(create_app())


def test_atlas_route_serves_the_busiest_miner_whole_and_bounded(client: TestClient) -> None:
    whole = client.get("/preview/get_crafting_atlas.json", params={"limit": 0}).json()
    bounded = client.get("/preview/get_crafting_atlas.json", params={"limit": 1}).json()

    assert whole["byMiner"] == [["redwood", 3], ["ekans", 2], ["salt", 1]]
    assert whole["perActionCounts"]["DigOrMine"] == 6
    assert bounded["byMiner"] == [["redwood", 3]]
    assert any(w.startswith("byMiner:") for w in bounded["warnings"])


def test_world_route_serves_the_top_road_builder_whole_and_bounded(client: TestClient) -> None:
    whole = client.get("/preview/world.json", params={"limit": 0}).json()
    bounded = client.get("/preview/world.json", params={"limit": 1}).json()

    (roads,) = whole["byCitizenByCategory"]
    assert roads["key"] == "roads"
    assert roads["players"] == [["ekans", 3], ["salt", 1]]
    assert whole["categories"][0]["key"] == roads["key"]
    assert bounded["byCitizenByCategory"][0]["players"] == [["ekans", 3]]
    assert any(w.startswith("byCitizenByCategory.roads.players:") for w in bounded["warnings"])


def test_additive_keys_leave_the_keys_the_spa_reads_in_place(client: TestClient) -> None:
    atlas = client.get("/preview/get_crafting_atlas.json", params={"limit": 0}).json()
    world = client.get("/preview/world.json", params={"limit": 0}).json()

    assert {"byCitizen", "byCitizenIterations", "byCrafted", "flows"} <= set(atlas)
    assert "perActionCounts" in atlas
    assert {"byCitizen", "byPolluter", "categories", "timeline", "hotspots"} <= set(world)
