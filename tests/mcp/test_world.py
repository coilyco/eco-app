"""Tests for the world / industry activity aggregator + tool wiring (eco-app#62).

Covers:
  - Folding a world-action CSV into categories / builders / objects / timeline /
    hotspots via the shared crafting plumbing.
  - The eco-app#5 column corrector realigning a shifted world row.
  - Day-3 empty CSVs producing a graceful "no events" report.
  - The per-action max-rows safety valve.
  - fetch_world merging multiple actions with the id→name citizen join.
  - Partial-failure tolerance (a disabled/erroring exporter is a warning).
  - Ids shown when the citizen join is unavailable.
  - SQLite cache per (base, api-key) hitting within TTL.
  - to_dict / from_dict round-trip.
  - Tool wiring returns two TextContent blocks + _meta.ui, and list_tools sees it.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from pathlib import Path

import httpx
import mcp.types as mt
import pytest
import respx

from eco_mcp_app.server import build_server
from eco_mcp_app.world import (
    WORLD_ACTIONS,
    WorldAccumulator,
    aggregate_world_rows,
    apply_citizen_names,
    fetch_world,
    finalize,
)

BASE = "http://eco.example.com:3001"
CITIZENS_URL = f"{BASE}/api/v1/citizens"


def _action_url(action: str) -> str:
    return f"{BASE}/api/v1/exporter/actions?actionName={action}"


_CITIZENS_JSON = [
    {"id": 129312, "name": "coilysiren"},
    {"id": 130409, "name": "ekans"},
    {"id": 129580, "name": "redwood"},
]

# Construction rows across two in-game days (Time in seconds; day = Time // 86400).
_CONSTRUCT_CSV = (
    "Block,Citizen,ActionLocation,Count,Time\n"
    '"StoneItem",129312,"418,75,460",12.0,6519\n'
    '"StoneItem",130409,"420,75,462",8.0,7000\n'
    '"BrickItem",129580,"100,80,120",5.0,95000\n'
)

# PlaceOrPickUpObject — a distinct object column.
_PLACE_CSV = (
    "WorldObjectItem,Citizen,ActionLocation,Count,Time\n"
    '"WorkbenchItem",129312,"418,75,460",1.0,6600\n'
    '"CampfireItem",129312,"418,75,460",1.0,6700\n'
)

_TAMP_CSV = 'Block,Citizen,ActionLocation,Count,Time\n"DirtRoadItem",130409,"300,70,300",4.0,8000\n'

_DIG_CSV = (
    "BlockItemOnDestroy,Citizen,Position,Count,Time\n"
    '"IronOreItem",129580,"500,40,500",30.0,9000\n'
    '"IronOreItem",129580,"501,40,500",22.0,9100\n'
)

_EMPTY_CSV = "Block,Citizen,ActionLocation,Count,Time\n"


@pytest.fixture(autouse=True)
def _isolated_cache(monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Each test gets its own cache dir so SQLite state doesn't cross-leak."""
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("ECO_CACHE_DIR", tmp)
        yield Path(tmp)


def _rows(csv_text: str) -> list[list[str]]:
    import csv

    return list(csv.reader(csv_text.splitlines()))


def _mock_all_actions(overrides: dict[str, str] | None = None) -> None:
    """Mock every world-action endpoint empty, overriding named ones with CSVs."""
    overrides = overrides or {}
    seen: set[str] = set()
    for action, _cat in WORLD_ACTIONS:
        if action in seen:
            continue
        seen.add(action)
        text = overrides.get(action, _EMPTY_CSV)
        respx.get(_action_url(action)).mock(return_value=httpx.Response(200, text=text))


def test_aggregate_world_rows_folds_construction() -> None:
    acc = WorldAccumulator()
    n = aggregate_world_rows("ConstructOrDeconstruct", "construction", _rows(_CONSTRUCT_CSV), acc)
    assert n == 3
    assert acc.total_events == 3
    assert acc.category_events["construction"] == 3
    # Volume is the summed Count (12 + 8 + 5).
    assert acc.category_volume["construction"] == pytest.approx(25.0)
    # Objects count touch *events*, not summed Count: StoneItem is placed in
    # two rows → 2 touches (was 12 + 8 = 20 under the old volume bug, #82).
    assert acc.by_object["StoneItem"] == 2
    # Citizen keyed by raw numeric id; each row is one event.
    assert acc.by_citizen["129312"] == 1
    assert acc.by_citizen["130409"] == 1
    # Timeline buckets by in-game day: 6519/7000 → day 0, 95000 → day 1.
    assert acc.timeline[0]["construction"] == 2
    assert acc.timeline[1]["construction"] == 1
    # Hotspots bin x/z to the 64-grid: (418,_,460) → (384, 448).
    assert acc.hotspots[(384, 448)] == 2


def test_aggregate_world_rows_realigns_shifted_row() -> None:
    """An undeclared extra column shifts fields; the corrector recovers them."""
    acc = WorldAccumulator()
    csv_text = (
        "Block,Citizen,ActionLocation,Count,Time\n"
        # Aligned row.
        '"StoneItem",129312,"418,75,460",12.0,6519\n'
        # Shifted: an undeclared HandsItem column before ActionLocation.
        '"BrickItem",130409,"HandsItem","420,75,462",8.0,7000\n'
    )
    aggregate_world_rows("ConstructOrDeconstruct", "construction", _rows(csv_text), acc)
    # Both rows fold with real Count and Citizen despite the shift.
    assert acc.category_volume["construction"] == pytest.approx(20.0)
    assert acc.by_citizen["130409"] == 1
    # The position triple never becomes an object key.
    assert "420,75,462" not in acc.by_object


def test_aggregate_world_rows_empty_stays_empty() -> None:
    acc = WorldAccumulator()
    n = aggregate_world_rows("TampRoad", "roads", _rows(_EMPTY_CSV), acc)
    assert n == 0
    assert acc.total_events == 0
    assert dict(acc.category_events) == {}


def test_aggregate_world_rows_respects_max_rows_cap() -> None:
    acc = WorldAccumulator()
    n = aggregate_world_rows(
        "ConstructOrDeconstruct", "construction", _rows(_CONSTRUCT_CSV), acc, max_rows=2
    )
    assert n == 2
    assert any("truncated" in w for w in acc.warnings)


def test_finalize_ranks_and_round_trips() -> None:
    acc = WorldAccumulator()
    aggregate_world_rows("ConstructOrDeconstruct", "construction", _rows(_CONSTRUCT_CSV), acc)
    aggregate_world_rows("DigOrMine", "extraction", _rows(_DIG_CSV), acc)
    activity = finalize(acc, "t", BASE)
    # Categories ordered by CATEGORY_ORDER, only non-empty ones present.
    keys = activity.category_keys
    assert "construction" in keys and "extraction" in keys
    # Objects ranked by touch-event count (#82), not summed Count: StoneItem
    # (2 place rows) and IronOreItem (2 dig rows) each score 2 touches and lead
    # BrickItem's single touch — no more runaway summed-Count headline numbers.
    obj = dict(activity.by_object)
    assert obj["StoneItem"] == 2
    assert obj["IronOreItem"] == 2
    assert obj["BrickItem"] == 1
    assert activity.by_object[-1] == ("BrickItem", 1)
    # to_dict / from_dict round-trip preserves the ranked shape.
    from eco_mcp_app.world import WorldActivity

    again = WorldActivity.from_dict(activity.to_dict())
    assert again.total_events == activity.total_events
    assert again.categories == activity.categories
    assert again.timeline == activity.timeline
    assert again.hotspots == activity.hotspots


def test_apply_citizen_names_resolves_and_falls_back() -> None:
    acc = WorldAccumulator()
    acc.by_citizen = {"129312": 5, "999999": 2}
    apply_citizen_names(acc, {"129312": "coilysiren"})
    assert acc.by_citizen["coilysiren"] == 5
    assert acc.by_citizen["Citizen #999999"] == 2


@pytest.mark.asyncio
@respx.mock
async def test_fetch_world_merges_actions_and_joins_names() -> None:
    _mock_all_actions(
        {
            "ConstructOrDeconstruct": _CONSTRUCT_CSV,
            "PlaceOrPickUpObject": _PLACE_CSV,
            "TampRoad": _TAMP_CSV,
            "DigOrMine": _DIG_CSV,
        }
    )
    respx.get(CITIZENS_URL).mock(return_value=httpx.Response(200, json=_CITIZENS_JSON))

    activity = await fetch_world(base_url=BASE, api_key="secret", cache_ttl_s=0)
    # 3 construct + 2 place + 1 tamp + 2 dig = 8 events.
    assert activity.total_events == 8
    keys = activity.category_keys
    assert {"construction", "objects", "roads", "extraction"} <= set(keys)
    # Citizen ids resolved to names. coilysiren (129312): 1 construct + 2 place = 3.
    by_citizen = dict(activity.by_citizen)
    assert by_citizen["coilysiren"] == 3
    assert by_citizen["redwood"] == 3  # 129580: 1 construct + 2 dig
    assert activity.warnings == []
    assert activity.per_action_counts["ConstructOrDeconstruct"] == 3


@pytest.mark.asyncio
@respx.mock
async def test_fetch_world_tolerates_partial_failures() -> None:
    _mock_all_actions({"ConstructOrDeconstruct": _CONSTRUCT_CSV})
    # Override two actions with faults.
    respx.get(_action_url("TampRoad")).mock(return_value=httpx.Response(401))
    respx.get(_action_url("ObjectExplosion")).mock(side_effect=httpx.ConnectError("nope"))
    respx.get(CITIZENS_URL).mock(return_value=httpx.Response(200, json=_CITIZENS_JSON))

    activity = await fetch_world(base_url=BASE, api_key=None, cache_ttl_s=0)
    assert activity.per_action_counts["ConstructOrDeconstruct"] == 3
    assert any("TampRoad" in w and "401" in w for w in activity.warnings)
    assert any("ObjectExplosion" in w for w in activity.warnings)


@pytest.mark.asyncio
@respx.mock
async def test_fetch_world_shows_ids_when_join_unavailable() -> None:
    _mock_all_actions({"ConstructOrDeconstruct": _CONSTRUCT_CSV})
    respx.get(CITIZENS_URL).mock(return_value=httpx.Response(404))

    activity = await fetch_world(base_url=BASE, api_key=None, cache_ttl_s=0)
    by_citizen = dict(activity.by_citizen)
    assert by_citizen["Citizen #129312"] == 1


@pytest.mark.asyncio
@respx.mock
async def test_fetch_world_cache_hits_within_ttl() -> None:
    _mock_all_actions({"ConstructOrDeconstruct": _CONSTRUCT_CSV})
    route = respx.get(_action_url("ConstructOrDeconstruct")).mock(
        return_value=httpx.Response(200, text=_CONSTRUCT_CSV)
    )
    respx.get(CITIZENS_URL).mock(return_value=httpx.Response(200, json=_CITIZENS_JSON))

    a1 = await fetch_world(base_url=BASE, api_key="k", cache_ttl_s=60)
    a2 = await fetch_world(base_url=BASE, api_key="k", cache_ttl_s=60)
    assert a1.total_events == a2.total_events
    assert route.call_count == 1  # second call served from SQLite


@pytest.mark.asyncio
@respx.mock
async def test_fetch_world_empty_server_degrades() -> None:
    _mock_all_actions()
    respx.get(CITIZENS_URL).mock(return_value=httpx.Response(200, json=[]))
    activity = await fetch_world(base_url=BASE, api_key="k", cache_ttl_s=0)
    assert activity.total_events == 0
    assert activity.categories == []
    assert activity.warnings == []


@pytest.mark.asyncio
@respx.mock
async def test_tool_call_returns_two_text_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ECO_ADMIN_API_KEY", "k")
    _mock_all_actions({"ConstructOrDeconstruct": _CONSTRUCT_CSV, "DigOrMine": _DIG_CSV})
    respx.get(CITIZENS_URL).mock(return_value=httpx.Response(200, json=_CITIZENS_JSON))

    mcp = build_server()
    handler = mcp.request_handlers[mt.CallToolRequest]
    req = mt.CallToolRequest(
        method="tools/call",
        params=mt.CallToolRequestParams(
            name="get_world",
            arguments={"server": "eco.example.com:3001"},
        ),
    )
    result = await handler(req)
    blocks = result.root.content
    assert len(blocks) == 2
    assert isinstance(blocks[0], mt.TextContent)
    assert "World activity" in blocks[0].text
    assert result.root.meta is None


@pytest.mark.asyncio
async def test_list_tools_includes_get_world() -> None:
    mcp = build_server()
    handler = mcp.request_handlers[mt.ListToolsRequest]
    result = await handler(mt.ListToolsRequest(method="tools/list"))
    names = {tool.name for tool in result.root.tools}
    assert "get_world" in names


# Roads: ekans tamps three times, coilysiren once, so ekans is the top road
# builder even though coilysiren has more events overall (COI-2048).
_ROADS_CSV = (
    "Block,Citizen,ActionLocation,Count,Time\n"
    '"DirtRoadItem",130409,"300,70,300",4.0,8000\n'
    '"DirtRoadItem",130409,"301,70,300",4.0,8100\n'
    '"DirtRoadItem",130409,"302,70,300",4.0,8200\n'
    '"DirtRoadItem",129312,"303,70,300",4.0,8300\n'
)


def _fold_category(acc: WorldAccumulator, action: str, category: str, text: str) -> None:
    aggregate_world_rows(action, category, _rows(text), acc)


def test_fold_splits_events_per_player_by_category() -> None:
    acc = WorldAccumulator()
    _fold_category(acc, "ConstructOrDeconstruct", "construction", _CONSTRUCT_CSV)
    _fold_category(acc, "TampRoad", "roads", _ROADS_CSV)

    assert acc.by_citizen_category["roads"] == {"130409": 3, "129312": 1}
    assert acc.by_citizen_category["construction"] == {"129312": 1, "130409": 1, "129580": 1}
    # The split reconciles with the totals it came from.
    for category, players in acc.by_citizen_category.items():
        assert sum(players.values()) == acc.category_events[category]
    assert acc.by_citizen["130409"] == 4


def test_two_actions_in_one_category_share_one_board() -> None:
    """objects is PlaceOrPickUpObject plus MoveWorldObject, one board."""
    acc = WorldAccumulator()
    _fold_category(acc, "PlaceOrPickUpObject", "objects", _PLACE_CSV)
    _fold_category(acc, "MoveWorldObject", "objects", _PLACE_CSV)

    assert acc.by_citizen_category["objects"] == {"129312": 4}


def test_finalize_ranks_each_category_and_resolves_names() -> None:
    acc = WorldAccumulator()
    _fold_category(acc, "TampRoad", "roads", _ROADS_CSV)
    apply_citizen_names(acc, {"130409": "ekans"})  # 129312 is a join miss

    activity = finalize(acc, "t", "u")
    payload = activity.to_dict()

    roads = next(g for g in payload["byCitizenByCategory"] if g["key"] == "roads")
    assert roads["label"] == "Roads"
    assert roads["events"] == 4
    assert roads["players"] == [["ekans", 3], ["Citizen #129312", 1]]
    assert "TampRoad" in payload["byCitizenByCategoryNote"]
    # Keyed by the same category names the server-wide split already uses.
    assert {g["key"] for g in payload["byCitizenByCategory"]} <= {
        c["key"] for c in payload["categories"]
    }


def test_a_category_whose_actions_all_failed_is_null_not_empty() -> None:
    acc = WorldAccumulator()
    _fold_category(acc, "ConstructOrDeconstruct", "construction", _CONSTRUCT_CSV)
    acc.failed_actions.add("TampRoad")
    acc.failed_actions.add("PolluteAir")  # pollution's only action
    acc.failed_actions.add("MoveWorldObject")  # objects keeps PlaceOrPickUpObject

    groups = {g["key"]: g for g in finalize(acc, "t", "u").to_dict()["byCitizenByCategory"]}

    assert groups["roads"] == {"key": "roads", "label": "Roads", "events": None, "players": None}
    assert groups["pollution"]["players"] is None
    assert groups["construction"]["players"] is not None
    # A fetched category with no rows is simply absent, as in categories.
    assert "garbage" not in groups
    assert "objects" not in groups


def test_world_activity_round_trips_the_per_category_boards() -> None:
    from eco_mcp_app.world import WorldActivity

    acc = WorldAccumulator()
    _fold_category(acc, "TampRoad", "roads", _ROADS_CSV)
    acc.failed_actions.add("PolluteAir")
    payload = finalize(acc, "t", "u").to_dict()

    again = WorldActivity.from_dict(payload).to_dict()

    assert again["byCitizenByCategory"] == payload["byCitizenByCategory"]


@pytest.mark.asyncio
@respx.mock
async def test_the_tool_names_the_top_road_builder_at_every_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Through the registered handler, at limit=0 (the SPA) and limit=1 (an MCP client)."""
    import json

    monkeypatch.setenv("ECO_ADMIN_API_KEY", "k")
    _mock_all_actions({"ConstructOrDeconstruct": _CONSTRUCT_CSV, "TampRoad": _ROADS_CSV})
    respx.get(CITIZENS_URL).mock(return_value=httpx.Response(200, json=_CITIZENS_JSON))
    handler = build_server().request_handlers[mt.CallToolRequest]

    async def call(limit: int) -> dict:
        result = await handler(
            mt.CallToolRequest(
                method="tools/call",
                params=mt.CallToolRequestParams(
                    name="get_world",
                    arguments={"server": "eco.example.com:3001", "limit": limit},
                ),
            )
        )
        assert "Top player per category" in result.root.content[0].text
        return json.loads(result.root.content[-1].text)

    full = await call(0)
    one = await call(1)

    full_roads = next(g for g in full["byCitizenByCategory"] if g["key"] == "roads")
    one_roads = next(g for g in one["byCitizenByCategory"] if g["key"] == "roads")
    assert full_roads["players"] == [["ekans", 3], ["coilysiren", 1]]
    assert one_roads["players"] == [["ekans", 3]]
    assert any(w.startswith("byCitizenByCategory.roads.players:") for w in one["warnings"])
    assert not any(w.startswith("byCitizenByCategory.") for w in full.get("warnings", []))
    # Every group's list obeys limit, and the server-wide summary is whole.
    assert all(len(g["players"]) <= 1 for g in one["byCitizenByCategory"])
    assert one["perActionCounts"]["TampRoad"] == 4
    assert list(one).index("byCitizenByCategoryNote") < list(one).index("byCitizen")
