"""The tech-progression view on get_progression (COI-2090).

It replaced get_milestones. Highest upgrade crafted comes from the crafting atlas, so
the board is a confirmed floor. Specialties taken come from the uncapped progression
events. Each half reads null when its exporter could not be read and never as zero.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import httpx
import mcp.types as mt
import pytest
import respx
from fastapi.testclient import TestClient

from eco_mcp_app import progression as prog_mod
from eco_mcp_app.crafting import CraftingAtlas
from eco_mcp_app.http_app import create_app
from eco_mcp_app.progression import (
    MAX_CITIZENS,
    ProgressionHistory,
    _ParsedEvent,
    build_history,
    fetch_history,
)
from eco_mcp_app.server import build_server
from eco_mcp_app.tech_progression import (
    build_tech_progression,
    tech_progression_markdown,
    upgrades_crafted,
)

BASE = "http://eco.example.com:3001"
SERVER = "eco.example.com:3001"


def _url(action: str) -> str:
    return f"{BASE}/api/v1/exporter/actions?actionName={action}"


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    monkeypatch.setenv("ECO_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("ECO_ADMIN_API_KEY", "k")
    prog_mod._progression_cache.clear()
    yield
    prog_mod._progression_cache.clear()


def _event(kind: str, skill: str, citizen: str, time_s: float = 86400.0) -> _ParsedEvent:
    return _ParsedEvent(
        action=kind,
        kind=kind,
        time_s=time_s,
        day=time_s / 86400.0,
        citizen_id=citizen,
        skill=skill,
        level=None,
    )


def _history(parsed: list[_ParsedEvent], fetched: tuple[str, ...]) -> ProgressionHistory:
    history = ProgressionHistory(fetched_at_iso="t", source_base_url=BASE)
    history.per_action_counts = dict.fromkeys(fetched, 0)
    build_history(parsed, history, {})
    return history


def _atlas(crafted: list[tuple[str, float]], fetched: bool = True) -> CraftingAtlas:
    atlas = CraftingAtlas(fetched_at_iso="t", source_base_url=BASE, by_crafted=crafted)
    if fetched:
        atlas.per_action_counts["ItemCraftedAction"] = len(crafted)
    return atlas


# --- upgrades ---------------------------------------------------------------


def test_upgrades_crafted_orders_by_stage_and_folds_scholars_into_the_ladder() -> None:
    rows = upgrades_crafted(
        [
            ("IronBarItem", 90.0),
            ("BasicUpgradeLvl1Item", 12.0),
            ("ScholarsAdvancedUpgradeLvl3Item", 2.0),
            ("AdvancedUpgradeLvl3Item", 5.0),
            ("AdvancedUpgradeLvl5Item", 1.0),
            ("MasonryUpgradeLvl5Item", 1.0),
        ]
    )
    assert [(r["item"], r["stage"]) for r in rows] == [
        ("AdvancedUpgradeLvl3Item", 7),
        ("ScholarsAdvancedUpgradeLvl3Item", 7),
        ("BasicUpgradeLvl1Item", 1),
    ]
    assert rows[1]["name"] == "Scholars Advanced Upgrade 3"
    assert rows[0]["crafts"] == 5


def test_highest_upgrade_is_the_top_stage_with_every_item_at_it() -> None:
    section, warnings = build_tech_progression(
        _history([], ("GainSpecialty",)),
        _atlas([("AdvancedUpgradeLvl3Item", 5.0), ("ScholarsAdvancedUpgradeLvl3Item", 2.0)]),
    )
    assert section["highestStage"] == 7
    assert section["highest"] == {
        "stageName": "Advanced 3",
        "tier": "Advanced",
        "level": 3,
        "items": ["AdvancedUpgradeLvl3Item", "ScholarsAdvancedUpgradeLvl3Item"],
        "crafts": 7,
    }
    assert warnings == []


def test_no_upgrade_crafted_is_stage_zero_when_the_exporter_was_read() -> None:
    section, _ = build_tech_progression(
        _history([], ("GainSpecialty",)), _atlas([("IronBarItem", 4.0)])
    )
    assert section["highestStage"] == 0
    assert section["highest"] is None
    assert section["crafted"] == []


def test_unreadable_craft_exporter_is_null_with_a_warning_never_zero() -> None:
    atlas = _atlas([], fetched=False)
    atlas.warnings.append("ItemCraftedAction: HTTP 401")
    section, warnings = build_tech_progression(_history([], ("GainSpecialty",)), atlas)
    assert section["highestStage"] is None
    assert section["highest"] is None
    assert section["crafted"] is None
    assert len(warnings) == 1
    assert "highest upgrade crafted is unmeasured" in warnings[0]
    assert "HTTP 401" in warnings[0]


def test_an_unreachable_atlas_is_null_and_names_the_error() -> None:
    section, warnings = build_tech_progression(
        _history([], ("GainSpecialty",)), None, "ConnectError: refused"
    )
    assert section["highestStage"] is None
    assert "ConnectError: refused" in warnings[0]


# --- specialties ------------------------------------------------------------


def test_specialties_count_every_citizen_not_only_the_capped_cards() -> None:
    """Citizen cards keep the busiest MAX_CITIZENS. A specialty only the quietest
    citizen took must still be reported as taken."""
    parsed = [_event("specialty", "Smithing", str(i), 86400.0 * (i + 1)) for i in range(120)]
    parsed.append(_event("specialty", "Cooking", "999", 86400.0 * 40))
    history = _history(parsed, ("GainSpecialty",))
    assert len(history.citizens) == MAX_CITIZENS
    section, warnings = build_tech_progression(history, _atlas([]))
    taken = {r["name"]: r for r in section["specialties"]["taken"]}
    assert section["specialties"]["takenCount"] == 2
    assert taken["Smithing"]["takenBy"] == 120
    assert taken["Smithing"]["holders"] == 120
    assert taken["Smithing"]["firstDay"] == 1
    assert taken["Cooking"]["takenBy"] == 1
    assert taken["Cooking"]["firstDay"] == 40
    assert warnings == []


def test_a_dropped_specialty_stays_taken_with_fewer_holders() -> None:
    parsed = [
        _event("specialty", "Mining", "1", 100.0),
        _event("specialty", "Mining", "2", 200.0),
        _event("specialty_loss", "Mining", "1", 300.0),
        _event("specialty", "Farming", "1", 400.0),
        _event("specialty_loss", "Farming", "1", 500.0),
        _event("specialty", "Farming", "1", 600.0),
    ]
    section, _ = build_tech_progression(_history(parsed, ("GainSpecialty",)), _atlas([]))
    taken = {r["name"]: r for r in section["specialties"]["taken"]}
    assert (taken["Mining"]["takenBy"], taken["Mining"]["holders"]) == (2, 1)
    # Lost then regained: holds it again, and is one citizen.
    assert (taken["Farming"]["takenBy"], taken["Farming"]["holders"]) == (1, 1)


def test_zero_specialties_taken_is_zero_when_the_exporter_was_read() -> None:
    section, _ = build_tech_progression(_history([], ("GainSpecialty",)), _atlas([]))
    assert section["specialties"] == {"takenCount": 0, "taken": []}


def test_unreadable_specialty_exporter_is_null_with_a_warning() -> None:
    section, warnings = build_tech_progression(_history([], ()), _atlas([]))
    assert section["specialties"] == {"takenCount": None, "taken": None}
    assert any("specialties taken are unmeasured" in w for w in warnings)


def test_gains_with_no_skill_column_are_warned_not_dropped_silently() -> None:
    parsed = [_event("specialty", "", "1"), _event("specialty", "Mining", "2")]
    section, warnings = build_tech_progression(_history(parsed, ("GainSpecialty",)), _atlas([]))
    assert section["specialties"]["takenCount"] == 1
    assert any("1 GainSpecialty rows carry no recognised skill column" in w for w in warnings)


def test_markdown_says_not_measured_instead_of_none_or_zero() -> None:
    section, _ = build_tech_progression(_history([], ()), None, "boom")
    md = tech_progression_markdown(section)
    assert "highest upgrade crafted: not measured" in md
    assert "specialties taken: not measured" in md
    assert "none yet" not in md


@pytest.mark.asyncio
@respx.mock
async def test_specialty_rows_survive_the_history_cache() -> None:
    for action in prog_mod.PROGRESSION_ACTION_TYPES:
        text = "Citizen,Specialty,Time\n1,Mining,86400\n" if action == "GainSpecialty" else ""
        respx.get(_url(action)).mock(return_value=httpx.Response(200, text=text))
    respx.get(f"{BASE}/api/v1/citizens").mock(return_value=httpx.Response(200, json=[]))
    respx.get(f"{BASE}/datasets/flatlist").mock(return_value=httpx.Response(200, json=[]))
    first = await fetch_history(base_url=SERVER, api_key="k")
    cached = await fetch_history(base_url=SERVER, api_key="k")
    assert cached.specialties_taken == first.specialties_taken != []
    assert cached.unnamed_specialty_gains == first.unnamed_specialty_gains


# --- the callers: MCP tool and the REST plane the SPA reads ------------------


def _csv(header: str, rows: list[str]) -> str:
    return header + "\n" + "\n".join(rows) + ("\n" if rows else "")


def _mock_world(
    *, specialties: int, citizens: int, plain_crafts: int, craft_status: int = 200
) -> None:
    """A late-cycle Sirens: every ladder item crafted, many specialties, many crafts."""
    gains = [
        f"{c},Skill{s},1,{86400 * (1 + (c + s) % 50)}"
        for c in range(citizens)
        for s in range(specialties)
        if (c + s) % 3 == 0
    ]
    respx.get(_url("GainSpecialty")).mock(
        return_value=httpx.Response(200, text=_csv("Citizen,Specialty,Level,Time", gains))
    )
    for action in prog_mod.PROGRESSION_ACTION_TYPES:
        if action != "GainSpecialty":
            respx.get(_url(action)).mock(return_value=httpx.Response(200, text="Citizen,Time\n"))
    respx.get(f"{BASE}/api/v1/citizens").mock(
        return_value=httpx.Response(
            200, json=[{"id": i, "name": f"player{i}"} for i in range(citizens)]
        )
    )
    respx.get(f"{BASE}/datasets/flatlist").mock(return_value=httpx.Response(200, json=[]))

    ladder = [
        f"{scholars}{tier}UpgradeLvl{n}Item"
        for scholars in ("", "Scholars")
        for tier in ("Basic", "Advanced", "Modern")
        for n in range(1, 5)
    ]
    crafts = [f"1,{item},BenchItem,1,{86400 * 5}" for item in ladder]
    crafts += [f"2,Plain{i % 400}Item,BenchItem,1,{86400 * 5}" for i in range(plain_crafts)]
    respx.get(_url("ItemCraftedAction")).mock(
        return_value=httpx.Response(
            craft_status, text=_csv("Citizen,ItemUsed,WorldObjectItem,Count,Time", crafts)
        )
    )
    for action in ("HarvestOrHunt", "ChopTree", "DigOrMine"):
        respx.get(_url(action)).mock(return_value=httpx.Response(200, text="Citizen,Time\n"))


@pytest.mark.asyncio
@respx.mock
async def test_get_progression_carries_the_tech_view_at_its_largest() -> None:
    _mock_world(specialties=60, citizens=150, plain_crafts=3000)
    handler = build_server().request_handlers[mt.CallToolRequest]
    result = await handler(
        mt.CallToolRequest(
            method="tools/call",
            params=mt.CallToolRequestParams(name="get_progression", arguments={"server": SERVER}),
        )
    )
    md, body = result.root.content
    assert isinstance(md, mt.TextContent) and isinstance(body, mt.TextContent)
    payload = json.loads(body.text)
    tech = payload["techProgression"]
    # Every Scholars and plain ladder item, 24 rows, highest first.
    assert tech["highestStage"] == 12
    assert tech["highest"]["stageName"] == "Modern 4"
    assert len(tech["crafted"]) == 24
    assert tech["specialties"]["takenCount"] == 60
    assert "highest upgrade crafted: **Modern 4**" in md.text
    # The view is small even beside 3000 other crafts and 150 citizens: the whole
    # default response stays well inside an MCP client's cap.
    assert len(json.dumps(tech)) < 20_000
    assert len(body.text) < 100_000
    # Caveats still lead the JSON (COI-757), and nothing is duplicated at top level.
    assert next(iter(payload)) == "warnings"
    assert "specialtiesTaken" not in payload


@pytest.mark.asyncio
@respx.mock
async def test_an_unreadable_craft_exporter_nulls_upgrades_but_keeps_the_history() -> None:
    _mock_world(specialties=5, citizens=10, plain_crafts=10, craft_status=401)
    handler = build_server().request_handlers[mt.CallToolRequest]
    result = await handler(
        mt.CallToolRequest(
            method="tools/call",
            params=mt.CallToolRequestParams(name="get_progression", arguments={"server": SERVER}),
        )
    )
    assert result.root.isError is False
    payload = json.loads(result.root.content[1].text)
    tech = payload["techProgression"]
    assert tech["highestStage"] is None and tech["crafted"] is None
    assert tech["specialties"]["takenCount"] > 0
    assert any("highest upgrade crafted is unmeasured" in w for w in payload["warnings"])


@pytest.mark.asyncio
async def test_get_milestones_is_gone_from_the_mcp_surface() -> None:
    handler = build_server(disabled_tools=frozenset()).request_handlers[mt.ListToolsRequest]
    result = await handler(mt.ListToolsRequest(method="tools/list"))
    assert "get_milestones" not in {tool.name for tool in result.root.tools}
    assert TestClient(create_app()).get("/preview/get_milestones.json").status_code != 200
