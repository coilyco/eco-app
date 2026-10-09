"""Tests for the crafting atlas aggregator + tool wiring.

Covers:
  - Stream-parsing a respx-mocked CSV and getting the expected rollups.
  - Missing-action endpoint (401 / 404) becomes a non-fatal warning, not a
    crash — other action types still aggregate.
  - 20 MB synthetic CSV streams through without running afoul of the
    max-rows safety valve or blowing peak memory. (We bound it via
    MAX_ROWS_PER_ACTION, which the test sets low to exercise the cap.)
  - Empty CSVs produce a Day-3-safe "no events" atlas.
  - The tool wiring returns three TextContent blocks and no widget (just-data per eco-app#87).
  - SQLite cache is per (base, api-key) and hits within TTL.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from pathlib import Path

import httpx
import mcp.types as mt
import pytest
import respx

from eco_mcp_app import crafting as crafting_mod
from eco_mcp_app.crafting import (
    CITIZEN_NAMES_UNAVAILABLE_WARNING,
    CraftingAtlas,
    _corrected_index,
    aggregate_rows,
    atlas_template_context,
    fetch_atlas,
    prettify_eco_name,
)
from eco_mcp_app.server import build_server

CRAFT_URL = "http://eco.example.com:3001/api/v1/exporter/actions?actionName=ItemCraftedAction"
HARVEST_URL = "http://eco.example.com:3001/api/v1/exporter/actions?actionName=HarvestOrHunt"
CHOP_URL = "http://eco.example.com:3001/api/v1/exporter/actions?actionName=ChopTree"
DIG_URL = "http://eco.example.com:3001/api/v1/exporter/actions?actionName=DigOrMine"
CITIZENS_URL = "http://eco.example.com:3001/api/v1/citizens"

BASE = "http://eco.example.com:3001"

# id -> name join the crafting atlas fetches from the jobs mod (eco-app#5).
_CITIZENS_JSON = [
    {"id": 129312, "name": "coilysiren"},
    {"id": 130409, "name": "ekans"},
    {"id": 129580, "name": "redwood"},
    {"id": 129558, "name": "salt"},
    {"id": 4478, "name": "hammerhand"},
]


_CRAFT_CSV = (
    "ActionLocation,WorldObjectItem,Citizen,ItemUsed,"
    "OverrideHierarchyActionsToConsumer,Count,Time\n"
    # Three per-event rows (Count == 1, one crafting iteration each) plus two
    # server-side hourly rollups (Count > 1) whose item/station labels are one
    # arbitrary merged event's values (eco-app#131).
    '"418,75,460","CampfireItem",129312,"CharredMushroomsItem",false,1.0,6519\n'
    '"289,89,310","WorkbenchItem",130409,"AdobeItem",false,1.0,7118\n'
    '"99,89,123","WorkbenchItem",129580,"AdobeItem",false,1.0,7197\n'
    '"142,82,203","CampfireItem",4478,"BeetCampfireSaladItem",false,189.0,10798\n'
    '"417,89,531","ResearchTableItem",129558,"DendrologyResearchPaperBasicItem",false,13.0,10785\n'
)

_HARVEST_CSV = (
    "Species,DamagedOrDestroyed,DestroyedByBlock,CaloriesToConsume,"
    "Position,Citizen,ActionLocation,Count,Time\n"
    '"BunchgrassSpecies",88,true,0.0,"419,75,458",129312,"419,75,458",173.0,3599\n'
    '"HuckleberrySpecies",87,false,0.0,"495,77,549",129569,"495,77,549",113.0,3598\n'
)

_CHOP_CSV = (
    "OnGround,Felled,Species,BranchesTargeted,GrowthPercent,CaloriesToConsume,"
    "ToolUsed,Position,Citizen,ActionLocation,Count,Time\n"
    'false,true,"FirSpecies",false,100.0,20.0,"StoneAxeItem",'
    '"424,75,461",129312,"424,75,461",7.0,3403\n'
    'false,true,"OakSpecies",false,40.4,19.0,"StoneAxeItem",'
    '"90,96,124",129580,"90,96,124",4.0,3553\n'
)

_DIG_EMPTY = "Position,Citizen,Count,Time\n"


@pytest.fixture(autouse=True)
def _isolated_cache(monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Each test gets its own cache dir so SQLite state doesn't cross-leak."""
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("ECO_CACHE_DIR", tmp)
        yield Path(tmp)


@pytest.fixture(autouse=True)
def _fresh_module_state() -> None:
    # No module-level caches on crafting_mod, but this hook is here so adding
    # one later doesn't silently share state across tests.
    return None


def _rows(csv_text: str) -> list[list[str]]:
    import csv

    return list(csv.reader(csv_text.splitlines()))


def test_prettify_eco_name_handles_common_shapes() -> None:
    assert prettify_eco_name("CampfireItem") == "Campfire"
    assert prettify_eco_name("BunWulfRawMeatItem") == "Bun Wulf Raw Meat"
    assert prettify_eco_name("OakSpecies") == "Oak"
    assert prettify_eco_name("") == ""


def test_aggregate_rows_folds_craft_csv() -> None:
    atlas = CraftingAtlas(fetched_at_iso="t", source_base_url="b")
    n = aggregate_rows("ItemCraftedAction", _rows(_CRAFT_CSV), atlas)
    assert n == 5
    # Every craft row contributes exactly 1 confirmed iteration to the item
    # board: per-event rows are one iteration, and a rollup's labels come from
    # its first merged event, so one iteration is proven and the remaining
    # count-1 land in the rollup tallies (eco-app#131).
    by_crafted = dict(atlas.by_crafted)
    assert by_crafted["AdobeItem"] == pytest.approx(2.0)
    assert by_crafted["BeetCampfireSaladItem"] == pytest.approx(1.0)
    assert by_crafted["DendrologyResearchPaperBasicItem"] == pytest.approx(1.0)
    assert atlas.rollup_events == 2
    assert atlas.rollup_iterations == pytest.approx(200.0)  # (189-1) + (13-1)
    # No gather rows here, so the gathered board stays empty.
    assert atlas.by_gathered == []
    by_station = dict(atlas.by_station)
    assert by_station["CampfireItem"] == 2
    assert by_station["WorkbenchItem"] == 2
    assert by_station["ResearchTableItem"] == 1
    # aggregate_rows keys both citizen boards by the raw numeric id; name
    # resolution happens later in fetch_atlas (eco-app#5).
    #
    # by_citizen counts rows — the unit get_world.byCitizen uses (eco-app#222).
    by_citizen = dict(atlas.by_citizen)
    assert by_citizen["129312"] == 1
    assert by_citizen["129580"] == 1
    assert by_citizen["4478"] == 1
    assert by_citizen["129558"] == 1
    # Nothing under an event-shaped name may exceed the atlas event total.
    assert max(by_citizen.values()) <= atlas.total_events
    # by_citizen_iterations weighs crafts by Count, which is 1 on per-event
    # rows and the true merged-iteration total on rollups (eco-app#131), so
    # citizen credit survives server-side aggregation.
    by_iterations = dict(atlas.by_citizen_iterations)
    assert by_iterations["129312"] == 1
    assert by_iterations["129580"] == 1
    assert by_iterations["4478"] == 189
    assert by_iterations["129558"] == 13
    # Flow edges carry one confirmed iteration per row, rollups included.
    flow_keys = {(s, t) for s, t, _ in atlas.flows}
    assert ("CampfireItem", "CharredMushroomsItem") in flow_keys
    assert ("WorkbenchItem", "AdobeItem") in flow_keys
    assert ("ResearchTableItem", "DendrologyResearchPaperBasicItem") in flow_keys


def test_corrected_index_absorbs_extra_tool_column() -> None:
    """A HarvestOrHunt row with an undeclared HandsItem column realigns."""
    header = [
        "Species",
        "DamagedOrDestroyed",
        "DestroyedByBlock",
        "CaloriesToConsume",
        "Position",
        "Citizen",
        "ActionLocation",
        "Count",
        "Time",
    ]
    # Extra "HandsItem" inserted before Position shifts every later field.
    row = [
        "BunchgrassSpecies",
        "88",
        "true",
        "0.0",
        "HandsItem",
        "254,86,313",
        "129312",
        "254,86,313",
        "173.0",
        "3599",
    ]
    idx = _corrected_index(header, row)
    # Citizen must land on the numeric id, Count on the numeric weight.
    assert row[idx[header.index("Citizen")]] == "129312"
    assert row[idx[header.index("Count")]] == "173.0"
    assert row[idx[header.index("Position")]] == "254,86,313"


def test_aggregate_rows_realigns_shifted_rows() -> None:
    """Misaligned rows fold with correct Count and Citizen, not zeros/positions."""
    atlas = CraftingAtlas(fetched_at_iso="t", source_base_url="b")
    csv_text = (
        "Species,DamagedOrDestroyed,DestroyedByBlock,CaloriesToConsume,"
        "Position,Citizen,ActionLocation,Count,Time\n"
        # Aligned row.
        '"HuckleberrySpecies",87,false,0.0,"495,77,549",129569,"495,77,549",113.0,3598\n'
        # Shifted row: extra HandsItem column before Position.
        '"BunchgrassSpecies",88,true,0.0,"HandsItem","419,75,458",129312,'
        '"419,75,458",173.0,3599\n'
    )
    aggregate_rows("HarvestOrHunt", _rows(csv_text), atlas)
    # Harvest rows fold into by_gathered as one event each (not summed biomass);
    # the realign still has to land the right species on the right row so the
    # shifted row isn't dropped (before the fix it read Count as a position).
    by_gathered = dict(atlas.by_gathered)
    assert by_gathered["HuckleberrySpecies"] == 1
    assert by_gathered["BunchgrassSpecies"] == 1
    by_citizen = dict(atlas.by_citizen)
    assert by_citizen["129312"] == 1
    assert by_citizen["129569"] == 1
    # The position triple never becomes a citizen id.
    assert "419,75,458" not in by_citizen


def test_aggregate_rows_drops_position_and_numeric_keys() -> None:
    """Misaligned exporter rows read positions/numbers where names belong."""
    atlas = CraftingAtlas(fetched_at_iso="t", source_base_url="b")
    csv_text = (
        "WorldObjectItem,Citizen,ItemUsed,Count\n"
        '"254,86,313",129312,"0.0",1\n'
        "CampfireItem,129312,CharredMushroomsItem,1\n"
    )
    aggregate_rows("ItemCraftedAction", _rows(csv_text), atlas)
    by_crafted = dict(atlas.by_crafted)
    by_station = dict(atlas.by_station)
    assert "0.0" not in by_crafted
    assert "254,86,313" not in by_station
    assert by_crafted["CharredMushroomsItem"] == pytest.approx(1.0)
    assert by_station["CampfireItem"] == 1


def test_aggregate_rows_handles_harvest_and_chop_shapes() -> None:
    atlas = CraftingAtlas(fetched_at_iso="t", source_base_url="b")
    aggregate_rows("HarvestOrHunt", _rows(_HARVEST_CSV), atlas)
    aggregate_rows("ChopTree", _rows(_CHOP_CSV), atlas)
    by_gathered = dict(atlas.by_gathered)
    # Harvest/chop: species becomes the gathered resource, counted by event (its
    # biomass Count is never summed — eco-app#70).
    assert by_gathered["BunchgrassSpecies"] == 1
    assert by_gathered["FirSpecies"] == 1
    # Chop uses ToolUsed as station since no WorldObjectItem column.
    by_station = dict(atlas.by_station)
    assert by_station["StoneAxeItem"] == 2


def test_aggregate_rows_empty_csv_stays_empty() -> None:
    atlas = CraftingAtlas(fetched_at_iso="t", source_base_url="b")
    n = aggregate_rows("DigOrMine", _rows(_DIG_EMPTY), atlas)
    assert n == 0
    assert atlas.total_events == 0
    assert atlas.by_crafted == []
    assert atlas.by_gathered == []


def test_aggregate_rows_respects_max_rows_cap() -> None:
    atlas = CraftingAtlas(fetched_at_iso="t", source_base_url="b")
    # 3 data rows, cap at 2 → one warning emitted.
    n = aggregate_rows(
        "ItemCraftedAction",
        _rows(_CRAFT_CSV),
        atlas,
        max_rows=2,
    )
    assert n == 2
    assert any("truncated" in w for w in atlas.warnings)


@pytest.mark.asyncio
@respx.mock
async def test_fetch_atlas_merges_multiple_actions() -> None:
    respx.get(CRAFT_URL).mock(return_value=httpx.Response(200, text=_CRAFT_CSV))
    respx.get(HARVEST_URL).mock(return_value=httpx.Response(200, text=_HARVEST_CSV))
    respx.get(CHOP_URL).mock(return_value=httpx.Response(200, text=_CHOP_CSV))
    respx.get(DIG_URL).mock(return_value=httpx.Response(200, text=_DIG_EMPTY))
    respx.get(CITIZENS_URL).mock(return_value=httpx.Response(200, json=_CITIZENS_JSON))

    atlas = await fetch_atlas(base_url=BASE, api_key="secret", cache_ttl_s=0)
    assert atlas.total_events == 5 + 2 + 2 + 0
    # Crafts land on the crafted board as confirmed iterations (rollup rows
    # count their one proven representative, eco-app#131); harvests/chops on
    # the gathered board by event count (eco-app#70).
    by_crafted = dict(atlas.by_crafted)
    assert by_crafted["AdobeItem"] == pytest.approx(2.0)
    assert by_crafted["BeetCampfireSaladItem"] == pytest.approx(1.0)
    by_gathered = dict(atlas.by_gathered)
    assert by_gathered["BunchgrassSpecies"] == 1
    assert by_gathered["FirSpecies"] == 1
    assert atlas.per_action_counts == {
        "ItemCraftedAction": 5,
        "HarvestOrHunt": 2,
        "ChopTree": 2,
        "DigOrMine": 0,
    }
    # Citizen ids resolved to names via the /api/v1/citizens join — both boards
    # get relabelled (eco-app#5, eco-app#222). coilysiren (129312) has 1 craft
    # + 1 harvest + 1 chop = 3 events, and the same 3 iterations.
    by_citizen = dict(atlas.by_citizen)
    assert by_citizen["coilysiren"] == 3
    # hammerhand's rollup is one row: one event, 189 iterations.
    assert by_citizen["hammerhand"] == 1
    assert by_citizen["salt"] == 1
    # An id with no mapping falls back to a "Citizen #<id>" label, not dropped.
    assert by_citizen["Citizen #129569"] == 1

    by_iterations = dict(atlas.by_citizen_iterations)
    assert by_iterations["coilysiren"] == 3
    # Full credit for the 189-iteration rollup (eco-app#131).
    assert by_iterations["hammerhand"] == 189
    assert by_iterations["salt"] == 13
    assert by_iterations["Citizen #129569"] == 1
    # The only warning is the rollup floor note.
    assert len(atlas.warnings) == 1
    assert "rollups" in atlas.warnings[0]
    assert "200" in atlas.warnings[0]


@pytest.mark.asyncio
@respx.mock
async def test_fetch_atlas_tolerates_partial_failures() -> None:
    respx.get(CRAFT_URL).mock(return_value=httpx.Response(200, text=_CRAFT_CSV))
    respx.get(HARVEST_URL).mock(return_value=httpx.Response(401))
    respx.get(CHOP_URL).mock(side_effect=httpx.ConnectError("nope"))
    respx.get(DIG_URL).mock(return_value=httpx.Response(200, text=_DIG_EMPTY))
    respx.get(CITIZENS_URL).mock(return_value=httpx.Response(200, json=_CITIZENS_JSON))

    atlas = await fetch_atlas(base_url=BASE, api_key=None, cache_ttl_s=0)
    # Still got the craft rows.
    assert atlas.per_action_counts["ItemCraftedAction"] == 5
    # Two warnings — one per failing action.
    assert any("HarvestOrHunt" in w and "401" in w for w in atlas.warnings)
    assert any("ChopTree" in w for w in atlas.warnings)


@pytest.mark.asyncio
@respx.mock
async def test_fetch_atlas_shows_ids_when_citizen_join_unavailable() -> None:
    respx.get(CRAFT_URL).mock(return_value=httpx.Response(200, text=_CRAFT_CSV))
    respx.get(HARVEST_URL).mock(return_value=httpx.Response(200, text=_DIG_EMPTY))
    respx.get(CHOP_URL).mock(return_value=httpx.Response(200, text=_DIG_EMPTY))
    respx.get(DIG_URL).mock(return_value=httpx.Response(200, text=_DIG_EMPTY))
    # No citizens surface deployed yet (404) — ids show, dimension not dropped.
    respx.get(CITIZENS_URL).mock(return_value=httpx.Response(404))

    atlas = await fetch_atlas(base_url=BASE, api_key=None, cache_ttl_s=0)
    by_citizen = dict(atlas.by_citizen)
    # One craft event for 129312 (event-counted, eco-app#70).
    assert by_citizen["Citizen #129312"] == 1
    assert CITIZEN_NAMES_UNAVAILABLE_WARNING in atlas.warnings


@pytest.mark.asyncio
@respx.mock
async def test_fetch_atlas_cache_hits_within_ttl() -> None:
    craft_route = respx.get(CRAFT_URL).mock(return_value=httpx.Response(200, text=_CRAFT_CSV))
    respx.get(HARVEST_URL).mock(return_value=httpx.Response(200, text=_HARVEST_CSV))
    respx.get(CHOP_URL).mock(return_value=httpx.Response(200, text=_CHOP_CSV))
    respx.get(DIG_URL).mock(return_value=httpx.Response(200, text=_DIG_EMPTY))
    respx.get(CITIZENS_URL).mock(return_value=httpx.Response(200, json=_CITIZENS_JSON))

    a1 = await fetch_atlas(base_url=BASE, api_key="k", cache_ttl_s=60)
    a2 = await fetch_atlas(base_url=BASE, api_key="k", cache_ttl_s=60)
    assert a1.total_events == a2.total_events
    # Exactly one upstream hit — second call served from SQLite.
    assert craft_route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_fetch_atlas_large_stream_stays_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Synthetic 20-MB-class stream — verifies the row cap keeps memory sane.

    We don't measure peak RSS (fragile under pytest), but we do prove:
      - the stream completes without OOM / timeout under the cap;
      - the aggregator reports a single 'truncated' warning at the cap;
      - the resulting atlas has the expected bounded row count.
    """
    monkeypatch.setattr(crafting_mod, "MAX_ROWS_PER_ACTION", 5000)

    def _huge_csv() -> str:
        header = (
            "ActionLocation,WorldObjectItem,Citizen,ItemUsed,"
            "OverrideHierarchyActionsToConsumer,Count,Time\n"
        )
        # ~120 bytes per row x 200_000 is about 24 MB.
        rows = (
            f'"0,0,0","WorkbenchItem",{1000 + i % 50},"AdobeItem",false,1.0,{i}\n'
            for i in range(200_000)
        )
        return header + "".join(rows)

    respx.get(CRAFT_URL).mock(return_value=httpx.Response(200, text=_huge_csv()))
    # Other endpoints empty so we only time the one under test.
    respx.get(HARVEST_URL).mock(return_value=httpx.Response(200, text=_DIG_EMPTY))
    respx.get(CHOP_URL).mock(return_value=httpx.Response(200, text=_DIG_EMPTY))
    respx.get(DIG_URL).mock(return_value=httpx.Response(200, text=_DIG_EMPTY))
    respx.get(CITIZENS_URL).mock(return_value=httpx.Response(200, json=[]))

    atlas = await fetch_atlas(base_url=BASE, api_key="k", cache_ttl_s=0)
    assert atlas.per_action_counts["ItemCraftedAction"] == 5000
    assert any("truncated" in w for w in atlas.warnings)


def test_atlas_template_context_empty_state_is_clean() -> None:
    atlas = CraftingAtlas(fetched_at_iso="t", source_base_url="b")
    ctx = atlas_template_context(atlas)
    assert ctx["empty"] is True
    assert ctx["top_crafted"] == []
    assert ctx["top_gathered"] == []
    assert ctx["sankey"] is None


def test_atlas_template_context_ranks_and_percents() -> None:
    atlas = CraftingAtlas(fetched_at_iso="t", source_base_url="b")
    aggregate_rows("ItemCraftedAction", _rows(_CRAFT_CSV), atlas)
    ctx = atlas_template_context(atlas)
    assert ctx["empty"] is False
    # Top crafted item is AdobeItem with 2 iterations, percent must be 100.
    assert ctx["top_crafted"][0]["name"] == "AdobeItem"
    assert ctx["top_crafted"][0]["pct"] == pytest.approx(100.0)
    # Sankey has nodes for both columns.
    assert ctx["sankey"] is not None
    assert ctx["sankey"]["edges"], "expected at least one flow edge"


@pytest.mark.asyncio
@respx.mock
async def test_tool_call_returns_three_text_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ECO_ADMIN_API_KEY", "k")
    respx.get(CRAFT_URL).mock(return_value=httpx.Response(200, text=_CRAFT_CSV))
    respx.get(HARVEST_URL).mock(return_value=httpx.Response(200, text=_HARVEST_CSV))
    respx.get(CHOP_URL).mock(return_value=httpx.Response(200, text=_CHOP_CSV))
    respx.get(DIG_URL).mock(return_value=httpx.Response(200, text=_DIG_EMPTY))
    respx.get(CITIZENS_URL).mock(return_value=httpx.Response(200, json=_CITIZENS_JSON))

    mcp = build_server()
    handler = mcp.request_handlers[mt.CallToolRequest]
    req = mt.CallToolRequest(
        method="tools/call",
        params=mt.CallToolRequestParams(
            name="get_crafting_atlas",
            arguments={"server": "eco.example.com:3001"},
        ),
    )
    result = await handler(req)
    blocks = result.root.content
    assert len(blocks) == 2
    assert isinstance(blocks[0], mt.TextContent)
    md = blocks[0].text
    assert "Crafting atlas" in md
    assert "Adobe" in md  # prettified AdobeItem
    # Just-data per eco-app#87: get_crafting_atlas no longer emits a widget.
    assert result.root.meta is None


@pytest.mark.asyncio
async def test_list_tools_now_includes_crafting_atlas() -> None:
    mcp = build_server()
    handler = mcp.request_handlers[mt.ListToolsRequest]
    result = await handler(mt.ListToolsRequest(method="tools/list"))
    names = {tool.name for tool in result.root.tools}
    assert "get_crafting_atlas" in names


def test_by_citizen_never_exceeds_total_events() -> None:
    """`byCitizen` means the same thing here as in `get_world` (eco-app#222).

    It used to sum craft iterations under an event-shaped name, so Kirdec came
    back at 281,506 against a server `totalEvents` of 18,092 — 15x the whole
    event count. Two tools, one field name, two units, neither labelled.
    """
    atlas = CraftingAtlas(fetched_at_iso="t", source_base_url=BASE)
    csv_text = (
        "ActionLocation,WorldObjectItem,Citizen,ItemUsed,Count,Time\n"
        # One 500-iteration hourly rollup plus one per-event row.
        '"1,2,3","WorkbenchItem",4478,"AdobeItem",500.0,1000\n'
        '"1,2,3","WorkbenchItem",4478,"AdobeItem",1.0,2000\n'
    )
    aggregate_rows("ItemCraftedAction", _rows(csv_text), atlas)

    assert atlas.total_events == 2
    assert dict(atlas.by_citizen)["4478"] == 2
    assert dict(atlas.by_citizen_iterations)["4478"] == 501
    # The invariant the sweep's cross-check would have caught.
    assert all(count <= atlas.total_events for _, count in atlas.by_citizen)


@pytest.mark.asyncio
async def test_the_atlas_bounds_every_array_not_only_flows(monkeypatch) -> None:
    """limit bounded 1 of 6 arrays, leaving ~45 KB at limit=1. Each array
    grows with world size, so each has to honour it. See #267."""
    import json

    import mcp.types as mt

    from eco_mcp_app import server
    from eco_mcp_app.crafting import CraftingAtlas

    atlas = CraftingAtlas(fetched_at_iso="2026-01-01T00:00:00Z", source_base_url="http://e")
    atlas.total_events = 900
    atlas.by_crafted = [(f"item{i}", float(i)) for i in range(40)]
    atlas.by_gathered = [(f"g{i}", i) for i in range(40)]
    atlas.by_station = [(f"s{i}", i) for i in range(40)]
    atlas.by_citizen = [(f"c{i}", i) for i in range(40)]
    atlas.by_citizen_iterations = [(f"c{i}", i) for i in range(40)]
    atlas.by_miner = [(f"c{i}", i) for i in range(40)]
    atlas.per_action_counts = {"DigOrMine": 780}
    atlas.flows = [(f"a{i}", f"b{i}", i) for i in range(40)]

    async def stub(**_):
        return atlas

    monkeypatch.setattr(server, "fetch_atlas", stub)
    mcp_server = server.build_server()
    handler = mcp_server.request_handlers[mt.CallToolRequest]
    result = await handler(
        mt.CallToolRequest(
            method="tools/call",
            params=mt.CallToolRequestParams(name="get_crafting_atlas", arguments={"limit": 5}),
        )
    )
    payload = json.loads(result.root.content[-1].text)

    for key in (
        "byCrafted",
        "byGathered",
        "byStation",
        "byCitizen",
        "byCitizenIterations",
        "byMiner",
        "flows",
    ):
        assert len(payload[key]) == 5, f"{key} ignored limit"
        assert any(w.startswith(f"{key}:") for w in payload["warnings"]), (
            f"{key} truncated without saying so"
        )

    # Rule 5: the summary still describes the whole population.
    assert payload["totalEvents"] == 900


# DigOrMine rows. Count is a magnitude (one row carries 500), so a miner who
# dug once big must not outrank one who dug often. Citizen 129312 digs twice,
# 129580 digs three times, and 4478 once with Count 500.
_DIG_CSV = (
    "BlockItemOnDestroy,Position,Citizen,Count,Time\n"
    '"IronOreItem","1,2,3",129312,1.0,100\n'
    '"IronOreItem","1,2,3",129312,1.0,200\n'
    '"DirtItem","1,2,3",129580,1.0,300\n'
    '"DirtItem","1,2,3",129580,1.0,400\n'
    '"DirtItem","1,2,3",129580,1.0,500\n'
    '"StoneItem","1,2,3",4478,500.0,600\n'
)


def test_aggregate_rows_counts_dig_events_per_miner_not_count_magnitude() -> None:
    atlas = CraftingAtlas(fetched_at_iso="t", source_base_url="b")
    aggregate_rows("DigOrMine", _rows(_DIG_CSV), atlas)

    assert dict(atlas.by_miner) == {"129580": 3, "129312": 2, "4478": 1}
    assert atlas.by_miner[0] == ("129580", 3)
    # The per-miner events reconcile with the server-wide count in the payload.
    assert sum(c for _, c in atlas.by_miner) == atlas.per_action_counts["DigOrMine"]


def test_other_actions_never_feed_the_miner_board() -> None:
    atlas = CraftingAtlas(fetched_at_iso="t", source_base_url="b")
    aggregate_rows("ItemCraftedAction", _rows(_CRAFT_CSV), atlas)
    aggregate_rows("ChopTree", _rows(_CHOP_CSV), atlas)
    aggregate_rows("HarvestOrHunt", _rows(_HARVEST_CSV), atlas)

    assert atlas.by_miner == []
    # Those rows still land on the all-actions boards that were being misread.
    assert atlas.by_citizen and atlas.by_citizen_iterations


@pytest.mark.asyncio
@respx.mock
async def test_fetch_atlas_names_the_busiest_miner_apart_from_the_busiest_producer() -> None:
    """The scientist's probe: Kirdec led byCitizenIterations from crafting alone."""
    respx.get(CRAFT_URL).mock(return_value=httpx.Response(200, text=_CRAFT_CSV))
    respx.get(HARVEST_URL).mock(return_value=httpx.Response(200, text=_HARVEST_CSV))
    respx.get(CHOP_URL).mock(return_value=httpx.Response(200, text=_CHOP_CSV))
    respx.get(DIG_URL).mock(return_value=httpx.Response(200, text=_DIG_CSV))
    respx.get(CITIZENS_URL).mock(return_value=httpx.Response(200, json=_CITIZENS_JSON))

    atlas = await fetch_atlas(base_url=BASE, api_key="k", cache_ttl_s=0)
    payload = atlas.to_dict()

    assert payload["byMiner"] == [["redwood", 3], ["coilysiren", 2], ["hammerhand", 1]]
    # hammerhand leads the iteration board on a 189-iteration craft rollup, and is
    # the last miner: the two boards answer different questions.
    assert payload["byCitizenIterations"][0][0] == "hammerhand"
    assert "busiest miner" in payload["byMinerNote"]
    assert payload["perActionCounts"]["DigOrMine"] == 6


@pytest.mark.asyncio
@respx.mock
async def test_byminer_is_null_not_empty_when_the_dig_exporter_is_unavailable() -> None:
    respx.get(CRAFT_URL).mock(return_value=httpx.Response(200, text=_CRAFT_CSV))
    respx.get(HARVEST_URL).mock(return_value=httpx.Response(200, text=_HARVEST_CSV))
    respx.get(CHOP_URL).mock(return_value=httpx.Response(200, text=_CHOP_CSV))
    respx.get(DIG_URL).mock(return_value=httpx.Response(401))
    respx.get(CITIZENS_URL).mock(return_value=httpx.Response(200, json=_CITIZENS_JSON))

    payload = (await fetch_atlas(base_url=BASE, api_key="k", cache_ttl_s=0)).to_dict()

    assert payload["byMiner"] is None
    assert any(w.startswith("DigOrMine:") for w in payload["warnings"])


@pytest.mark.asyncio
@respx.mock
async def test_byminer_survives_the_sqlite_cache_round_trip() -> None:
    respx.get(CRAFT_URL).mock(return_value=httpx.Response(200, text=_CRAFT_CSV))
    respx.get(HARVEST_URL).mock(return_value=httpx.Response(200, text=_HARVEST_CSV))
    respx.get(CHOP_URL).mock(return_value=httpx.Response(200, text=_CHOP_CSV))
    respx.get(DIG_URL).mock(return_value=httpx.Response(200, text=_DIG_CSV))
    respx.get(CITIZENS_URL).mock(return_value=httpx.Response(200, json=_CITIZENS_JSON))

    first = await fetch_atlas(base_url=BASE, api_key="k", cache_ttl_s=60)
    cached = await fetch_atlas(base_url=BASE, api_key="k", cache_ttl_s=60)

    assert cached.to_dict()["byMiner"] == first.to_dict()["byMiner"]
    assert cached.to_dict()["byMiner"][0] == ["redwood", 3]


@pytest.mark.asyncio
@respx.mock
async def test_the_tool_bounds_byminer_and_leads_with_its_note(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """At limit=0 the whole board ships, at limit=1 one row and a warning."""
    import json

    monkeypatch.setenv("ECO_ADMIN_API_KEY", "k")
    respx.get(CRAFT_URL).mock(return_value=httpx.Response(200, text=_CRAFT_CSV))
    respx.get(HARVEST_URL).mock(return_value=httpx.Response(200, text=_HARVEST_CSV))
    respx.get(CHOP_URL).mock(return_value=httpx.Response(200, text=_CHOP_CSV))
    respx.get(DIG_URL).mock(return_value=httpx.Response(200, text=_DIG_CSV))
    respx.get(CITIZENS_URL).mock(return_value=httpx.Response(200, json=_CITIZENS_JSON))
    handler = build_server().request_handlers[mt.CallToolRequest]

    async def call(limit: int) -> dict:
        result = await handler(
            mt.CallToolRequest(
                method="tools/call",
                params=mt.CallToolRequestParams(
                    name="get_crafting_atlas",
                    arguments={"server": "eco.example.com:3001", "limit": limit},
                ),
            )
        )
        return json.loads(result.root.content[-1].text)

    full = await call(0)
    one = await call(1)

    assert len(full["byMiner"]) == 3
    assert not any(w.startswith("byMiner:") for w in full.get("warnings", []))
    assert one["byMiner"] == [["redwood", 3]]
    assert any(w.startswith("byMiner:") for w in one["warnings"])
    assert list(one).index("byMinerNote") < list(one).index("byCrafted")
