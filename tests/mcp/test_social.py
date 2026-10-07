"""Tests for the chat-free community activity surface (eco-app#185)."""

from __future__ import annotations

import csv
import json
from collections.abc import Iterator
from typing import Any

import httpx
import mcp.types as mt
import pytest
import respx
from starlette.testclient import TestClient

from eco_mcp_app import server as eco_server
from eco_mcp_app import social as social_mod
from eco_mcp_app.http_app import create_app
from eco_mcp_app.server import build_server
from eco_mcp_app.social import (
    SocialSurface,
    _ActivityEvent,
    _surface_from_dict,
    build_surface,
    fetch_social,
    hash_handle,
    parse_activity_rows,
    parse_reputation_rows,
    social_markdown,
    social_template_context,
)

BASE = "http://eco.example.com:3001"
PLAY_URL = f"{BASE}/api/v1/exporter/actions?actionName=Play"
LOGIN_URL = f"{BASE}/api/v1/exporter/actions?actionName=FirstLogin"
REP_URL = f"{BASE}/api/v1/exporter/actions?actionName=ReputationTransfer"
CITIZENS_URL = f"{BASE}/api/v1/citizens"

_CITIZENS_JSON = [
    {"id": 129312, "name": "coilysiren"},
    {"id": 130409, "name": "ekans"},
    {"id": 129580, "name": "redwood"},
]
_REP_CSV = (
    "Citizen,ReceiverCitizen,Amount,Count,Time\n"
    "129312,130409,5.0,1,250000\n"
    "129580,130409,3.0,1,150000\n"
)
_LOGIN_CSV = "Citizen,Count,Time\n130409,1,100000\n"
_PLAY_CSV = "Citizen,Count,Time\n129312,1,50000\n129580,1,60000\n"
NAME_MAP = {"129312": "coilysiren", "130409": "ekans", "129580": "redwood"}


@pytest.fixture(autouse=True)
def _clear_cache() -> Iterator[None]:
    social_mod._social_cache.clear()
    yield
    social_mod._social_cache.clear()


def _rows(csv_text: str) -> list[list[str]]:
    return list(csv.reader(csv_text.splitlines()))


def _parse_all(surface: SocialSurface) -> tuple[list, list]:
    edges: list = []
    activity: list = []
    parse_reputation_rows(_rows(_REP_CSV), surface, edges)
    parse_activity_rows("FirstLogin", _rows(_LOGIN_CSV), surface, activity)
    parse_activity_rows("Play", _rows(_PLAY_CSV), surface, activity)
    return edges, activity


def test_parse_and_fold_shapes() -> None:
    surface = SocialSurface(fetched_at_iso="t", source_base_url="b")
    edges, activity = _parse_all(surface)
    build_surface(surface, edges, activity, NAME_MAP, show_names=True)

    assert surface.total_reputation_transfers == 2
    assert surface.total_first_logins == 1
    assert surface.total_play_events == 2
    assert surface.play_by_day == [(0, 0, 2)]
    assert surface.top_reputation_receivers[0] == ("ekans", pytest.approx(8.0))
    edge = next(edge for edge in surface.reputation_edges if edge["source"] == "coilysiren")
    assert edge["target"] == "ekans"
    assert edge["amount"] == pytest.approx(5.0)
    assert surface.new_arrivals == [{"label": "ekans", "day": 1}]
    assert "ChatSent" not in surface.per_type_counts
    assert "totalChat" not in surface.to_dict()


def test_live_exporter_reputation_columns_light_up_the_graph() -> None:
    """The header the live exporter actually emits must populate the graph (#260)."""
    csv_text = (
        "ReputationReceiver,ReputationSender,ReputationTransferredSign,"
        "ReputationSource,ReputationAmountTransferred,TargetType,"
        "ActionLocation,Count,Time\n"
        "130409,129312,1,Praise,5.0,Player,0 0 0,1,250000\n"
        "130409,129580,1,Praise,3.0,Player,0 0 0,1,150000\n"
    )
    surface = SocialSurface(fetched_at_iso="t", source_base_url="b")
    edges: list = []
    parse_reputation_rows(_rows(csv_text), surface, edges)
    build_surface(surface, edges, [], NAME_MAP, show_names=True)

    assert surface.total_reputation_transfers == 2
    assert surface.top_reputation_receivers[0] == ("ekans", pytest.approx(8.0))
    edge = next(edge for edge in surface.reputation_edges if edge["source"] == "coilysiren")
    assert edge["target"] == "ekans"
    assert edge["amount"] == pytest.approx(5.0)
    # A recognised header must not emit the "extend the candidate list" warning.
    assert not any("was not recognized" in warning for warning in surface.warnings)


def test_negative_reputation_sign_is_applied_to_magnitude() -> None:
    """`ReputationAmountTransferred` is unsigned; direction lives in its own column."""
    csv_text = (
        "ReputationReceiver,ReputationSender,ReputationTransferredSign,"
        "ReputationAmountTransferred,Count,Time\n"
        "130409,129312,-1,4.0,1,250000\n"
        "130409,129580,1,1.0,1,150000\n"
    )
    surface = SocialSurface(fetched_at_iso="t", source_base_url="b")
    edges: list = []
    parse_reputation_rows(_rows(csv_text), surface, edges)
    build_surface(surface, edges, [], NAME_MAP, show_names=True)

    assert surface.top_reputation_receivers[0] == ("ekans", pytest.approx(-3.0))
    negative = next(edge for edge in surface.reputation_edges if edge["source"] == "coilysiren")
    assert negative["amount"] == pytest.approx(-4.0)


def test_already_signed_amount_is_not_negated_twice() -> None:
    csv_text = (
        "ReputationReceiver,ReputationSender,ReputationTransferredSign,"
        "ReputationAmountTransferred,Count,Time\n"
        "130409,129312,-1,-4.0,1,250000\n"
    )
    surface = SocialSurface(fetched_at_iso="t", source_base_url="b")
    edges: list = []
    parse_reputation_rows(_rows(csv_text), surface, edges)

    assert edges[0].amount == pytest.approx(-4.0)


def test_empty_reputation_graph_is_diagnosed() -> None:
    csv_text = "Citizen,Beneficiary,Amount,Count,Time\n129312,130409,5.0,1,250000\n"
    surface = SocialSurface(fetched_at_iso="t", source_base_url="b")
    edges: list = []
    parse_reputation_rows(_rows(csv_text), surface, edges)
    build_surface(surface, edges, [], NAME_MAP, show_names=True)

    assert surface.total_reputation_transfers == 1
    assert surface.reputation_edges == []
    assert any(
        "ReputationTransfer" in warning and "receiver" in warning for warning in surface.warnings
    )


def test_public_path_redacts_player_names() -> None:
    surface = SocialSurface(fetched_at_iso="t", source_base_url="b")
    edges, activity = _parse_all(surface)
    build_surface(surface, edges, activity, NAME_MAP, show_names=False)

    payload = json.dumps(surface.to_dict())
    for real_name in ("coilysiren", "ekans", "redwood"):
        assert real_name not in payload
    assert surface.redacted is True
    assert surface.top_reputation_receivers[0][0] == hash_handle("ekans")
    assert surface.new_arrivals[0]["label"] == hash_handle("ekans")


def test_operator_mode_shows_names_only_when_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(social_mod.NAMES_ALLOW_ENV, raising=False)
    assert social_mod.effective_show_names(reveal_names=True) is False
    monkeypatch.setenv(social_mod.NAMES_ALLOW_ENV, "1")
    assert social_mod.effective_show_names(reveal_names=True) is True
    assert social_mod.effective_show_names(reveal_names=False) is False


@pytest.mark.asyncio
@respx.mock
async def test_fetch_social_uses_only_activity_and_reputation_exports() -> None:
    respx.get(PLAY_URL).mock(return_value=httpx.Response(200, text=_PLAY_CSV))
    respx.get(LOGIN_URL).mock(return_value=httpx.Response(200, text=_LOGIN_CSV))
    respx.get(REP_URL).mock(return_value=httpx.Response(200, text=_REP_CSV))
    respx.get(CITIZENS_URL).mock(return_value=httpx.Response(200, json=_CITIZENS_JSON))

    surface = await fetch_social(base_url=BASE, api_key="secret", cache_ttl_s=0)
    assert surface.per_type_counts == {
        "Play": 2,
        "FirstLogin": 1,
        "ReputationTransfer": 2,
    }
    assert surface.redacted is True
    assert "ekans" not in json.dumps(surface.to_dict())


@pytest.mark.asyncio
@respx.mock
async def test_fetch_social_tolerates_partial_failure() -> None:
    respx.get(PLAY_URL).mock(return_value=httpx.Response(401))
    respx.get(LOGIN_URL).mock(return_value=httpx.Response(200, text=_LOGIN_CSV))
    respx.get(REP_URL).mock(return_value=httpx.Response(200, text=_REP_CSV))
    respx.get(CITIZENS_URL).mock(return_value=httpx.Response(200, json=_CITIZENS_JSON))

    surface = await fetch_social(base_url=BASE, api_key=None, cache_ttl_s=0)
    assert surface.total_first_logins == 1
    assert any("Play" in warning and "401" in warning for warning in surface.warnings)


@pytest.mark.asyncio
@respx.mock
async def test_fetch_social_empty_is_clean() -> None:
    respx.get(PLAY_URL).mock(return_value=httpx.Response(200, text="Citizen,Count,Time\n"))
    respx.get(LOGIN_URL).mock(return_value=httpx.Response(200, text="Citizen,Count,Time\n"))
    respx.get(REP_URL).mock(
        return_value=httpx.Response(200, text="Citizen,ReceiverCitizen,Amount,Count,Time\n")
    )

    surface = await fetch_social(base_url=BASE, api_key=None, cache_ttl_s=0)
    assert surface.reputation_edges == []
    assert social_template_context(surface)["empty"] is True
    assert "no activity" in social_markdown(surface).lower()


@pytest.mark.asyncio
@respx.mock
async def test_tool_call_returns_text_and_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ECO_ADMIN_API_KEY", "k")
    respx.get(PLAY_URL).mock(return_value=httpx.Response(200, text=_PLAY_CSV))
    respx.get(LOGIN_URL).mock(return_value=httpx.Response(200, text=_LOGIN_CSV))
    respx.get(REP_URL).mock(return_value=httpx.Response(200, text=_REP_CSV))
    respx.get(CITIZENS_URL).mock(return_value=httpx.Response(200, json=_CITIZENS_JSON))

    mcp = build_server()
    handler = mcp.request_handlers[mt.CallToolRequest]
    request = mt.CallToolRequest(
        method="tools/call",
        params=mt.CallToolRequestParams(
            name="get_social",
            arguments={"server": "eco.example.com:3001"},
        ),
    )
    result = await handler(request)
    blocks = result.root.content
    assert len(blocks) == 2
    assert isinstance(blocks[0], mt.TextContent)
    assert "Community activity" in blocks[0].text
    assert result.root.meta is None
    assert "ekans" not in json.dumps([block.text for block in blocks])


@pytest.mark.asyncio
async def test_list_tools_includes_get_social() -> None:
    mcp = build_server()
    handler = mcp.request_handlers[mt.ListToolsRequest]
    result = await handler(mt.ListToolsRequest(method="tools/list"))
    names = {tool.name for tool in result.root.tools}
    assert "get_social" in names


@respx.mock
def test_preview_social_json_is_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ECO_ADMIN_API_KEY", "k")
    monkeypatch.setenv(social_mod.NAMES_ALLOW_ENV, "1")
    respx.get(PLAY_URL).mock(return_value=httpx.Response(200, text=_PLAY_CSV))
    respx.get(LOGIN_URL).mock(return_value=httpx.Response(200, text=_LOGIN_CSV))
    respx.get(REP_URL).mock(return_value=httpx.Response(200, text=_REP_CSV))
    respx.get(CITIZENS_URL).mock(return_value=httpx.Response(200, json=_CITIZENS_JSON))

    client = TestClient(create_app())
    response = client.get("/preview/social.json?server=eco.example.com:3001")
    assert response.status_code == 200
    payload = response.json()
    assert payload["redacted"] is True
    assert "totalChat" not in payload
    for real_name in ("coilysiren", "ekans", "redwood"):
        assert real_name not in json.dumps(payload)


def test_unrecognised_giver_column_names_what_the_export_carries() -> None:
    """The best-behaved failure in the sweep, made actionable (eco-app#227).

    440 transfers parsed and the reputation graph came back empty. The warning
    already said which columns it tried; it could not say which columns exist,
    so extending the candidate list needed another live probe. Now the export's
    own header rides along.
    """
    surface = SocialSurface(fetched_at_iso="t", source_base_url=BASE)
    rows = [
        ["Time", "ActorCitizen", "ReceiverCitizen", "Amount"],
        ["100", "101", "102", "5"],
        ["200", "103", "102", "3"],
    ]
    edges: list[social_mod._RepEdge] = []
    parse_reputation_rows(rows, surface, edges)
    build_surface(surface, edges=edges, activity=[], name_map={}, show_names=False)

    assert surface.total_reputation_transfers == 2
    assert surface.reputation_edges == []
    assert surface.reputation_columns_seen == [
        "Time",
        "ActorCitizen",
        "ReceiverCitizen",
        "Amount",
    ]
    warning = next(w for w in surface.warnings if "not recognized" in w)
    # Both halves: what we tried, and what is actually there.
    assert "Giver" in warning
    assert "ActorCitizen" in warning
    assert "social.py" in warning


def test_a_recognised_giver_column_builds_the_graph() -> None:
    surface = SocialSurface(fetched_at_iso="t", source_base_url=BASE)
    rows = [
        ["Time", "Citizen", "ReceiverCitizen", "Amount"],
        ["100", "101", "102", "5"],
    ]
    edges: list[social_mod._RepEdge] = []
    parse_reputation_rows(rows, surface, edges)
    build_surface(surface, edges=edges, activity=[], name_map={}, show_names=False)
    assert len(surface.reputation_edges) == 1
    assert not any("not recognized" in w for w in surface.warnings)


def _arrival_events(count: int) -> list[Any]:
    from eco_mcp_app.social import _ActivityEvent

    return [
        _ActivityEvent(time_s=float(i), day=float(i), citizen_id=f"c{i}", kind="firstlogin")
        for i in range(count)
    ]


def test_new_arrivals_says_when_it_truncates() -> None:
    """The one silent truncation in the suite: 60 rows returned, 124 present,
    warnings empty. A caller had no way to see the cap. See #267."""
    from eco_mcp_app.social import MAX_NEW_ARRIVALS, SocialSurface, build_surface

    surface = SocialSurface(
        fetched_at_iso="2026-01-01T00:00:00Z", source_base_url="http://eco.example"
    )
    build_surface(surface, [], _arrival_events(MAX_NEW_ARRIVALS + 7), {}, False)

    assert len(surface.new_arrivals) == MAX_NEW_ARRIVALS
    assert surface.total_first_logins == MAX_NEW_ARRIVALS + 7
    warning = [w for w in surface.warnings if w.startswith("newArrivals:")]
    assert warning, "the truncation was silent, so the cap is invisible to a caller"
    assert str(MAX_NEW_ARRIVALS + 7) in warning[0].replace(",", "")


def test_new_arrivals_stays_quiet_when_nothing_is_cut() -> None:
    """A warning on an untruncated list would make the honest case
    indistinguishable from the capped one."""
    from eco_mcp_app.social import SocialSurface, build_surface

    surface = SocialSurface(
        fetched_at_iso="2026-01-01T00:00:00Z", source_base_url="http://eco.example"
    )
    build_surface(surface, [], _arrival_events(3), {}, False)

    assert len(surface.new_arrivals) == 3
    assert not [w for w in surface.warnings if w.startswith("newArrivals:")]


def test_the_reputation_graph_announces_its_truncation() -> None:
    """The other array in this tool that grows with world size.

    newArrivals was fixed at #267 and this one was left, so a caller read a
    truncated graph with no way to tell. The graph grows with the square of the
    citizen count, so it is the one that runs away. See eco-app#6076.
    """
    cap = social_mod.MAX_REPUTATION_EDGES
    total = cap + 25
    header = "Citizen,ReceiverCitizen,Amount,Count,Time\n"
    body = "".join(
        f"{200000 + i},{300000 + i},{float(total - i)},1,{1000 + i}\n" for i in range(total)
    )

    surface = SocialSurface(fetched_at_iso="t", source_base_url="b")
    edges: list = []
    parse_reputation_rows(_rows(header + body), surface, edges)
    build_surface(surface, edges, [], {}, show_names=False)

    assert len(surface.reputation_edges) == cap
    warning = next((w for w in surface.warnings if w.startswith("reputationEdges:")), None)
    assert warning is not None, f"truncation was silent: {surface.warnings}"
    assert f"{cap:,} of {total:,}" in warning

    amounts = [edge["amount"] for edge in surface.reputation_edges]
    assert amounts == sorted(amounts, key=abs, reverse=True)


def test_a_small_reputation_graph_stays_quiet() -> None:
    """The negative control: under the cap, no warning."""
    surface = SocialSurface(fetched_at_iso="t", source_base_url="b")
    edges, activity = _parse_all(surface)
    build_surface(surface, edges, activity, NAME_MAP, show_names=True)

    assert not [w for w in surface.warnings if w.startswith("reputationEdges:")]


@pytest.mark.asyncio
@respx.mock
async def test_get_social_accepts_a_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """One of the four tools #6076 names as taking no `limit` at all.

    Both its arrays grow with world size, and warning about truncation is not
    the same as letting a caller ask for fewer rows. See eco-app#6076.
    """
    monkeypatch.setenv("ECO_ADMIN_API_KEY", "k")
    total = 6
    rep = "Citizen,ReceiverCitizen,Amount,Count,Time\n" + "".join(
        f"{200000 + i},{300000 + i},{float(total - i)},1,{1000 + i}\n" for i in range(total)
    )
    respx.get(PLAY_URL).mock(return_value=httpx.Response(200, text=_PLAY_CSV))
    respx.get(LOGIN_URL).mock(return_value=httpx.Response(200, text=_LOGIN_CSV))
    respx.get(REP_URL).mock(return_value=httpx.Response(200, text=rep))
    respx.get(CITIZENS_URL).mock(return_value=httpx.Response(200, json=_CITIZENS_JSON))

    mcp = build_server()
    handler = mcp.request_handlers[mt.CallToolRequest]
    result = await handler(
        mt.CallToolRequest(
            method="tools/call",
            params=mt.CallToolRequestParams(
                name="get_social",
                arguments={"server": "eco.example.com:3001", "limit": 2},
            ),
        )
    )
    payload = json.loads(result.root.content[1].text)

    assert len(payload["reputationEdges"]) == 2
    warning = next((w for w in payload["warnings"] if w.startswith("reputationEdges:")), None)
    assert warning is not None, f"bounded without saying so: {payload['warnings']}"
    assert f"showing 2 of {total}" in warning

    # Rule 5: the aggregate still describes every row.
    assert payload["totalReputationTransfers"] >= total


@pytest.mark.asyncio
async def test_get_social_declares_its_limit() -> None:
    """The schema has to advertise it, or no caller knows it exists."""
    mcp = build_server()
    handler = mcp.request_handlers[mt.ListToolsRequest]
    result = await handler(mt.ListToolsRequest(method="tools/list"))
    tool = next(t for t in result.root.tools if t.name == "get_social")

    assert "limit" in tool.inputSchema["properties"]


DAY = social_mod.SECONDS_PER_DAY


def _events(kind: str, days: list[float]) -> list[_ActivityEvent]:
    return [_ActivityEvent(time_s=d * DAY, day=d, citizen_id="1", kind=kind) for d in days]


def _surface_for(play_days: list[float], login_days: list[float] | None = None) -> SocialSurface:
    surface = SocialSurface(fetched_at_iso="t", source_base_url="b")
    activity = _events("play", play_days) + _events("firstlogin", login_days or [])
    build_surface(surface, [], activity, {}, show_names=False)
    return surface


def test_the_day_series_says_what_its_index_is() -> None:
    """COI-750: a bare [index, count] read as days ago inverts the trend."""
    surface = _surface_for([0, 0, 0, 1, 1, 2])
    surface.apply_world_clock({"DaysRunning": 5, "Description": "<b>Eco</b> | Cycle 14 | x"})

    payload = surface.to_dict()

    assert "not days ago" in payload["dayAxisNote"]
    assert "oldest first" in payload["dayAxisNote"]
    assert payload["playByDay"] == [
        {"cycle": 14, "cyclesAgo": 0, "day": 0, "count": 3},
        {"cycle": 14, "cyclesAgo": 0, "day": 1, "count": 2},
        {"cycle": 14, "cyclesAgo": 0, "day": 2, "count": 1},
    ]
    assert payload["today"] == {"cycle": 14, "day": 5}
    assert payload["recentWindow"] == {
        "cycle": 14,
        "fromDay": -1,
        "toDay": 5,
        "endsAt": "today",
        "playEvents": 6,
        "firstLogins": 0,
    }
    assert payload["cycles"] == [
        {
            "cycle": 14,
            "cyclesAgo": 0,
            "current": True,
            "firstDay": 0,
            "lastDay": 2,
            "playEvents": 6,
            "firstLogins": 0,
        }
    ]


def test_the_recent_window_counts_only_the_last_seven_days() -> None:
    surface = _surface_for([0, 1, 1, 86, 88, 88, 88, 91], login_days=[0, 90])
    surface.apply_world_clock({"DaysRunning": 94})

    window = surface.to_dict()["recentWindow"]

    assert (window["fromDay"], window["toDay"]) == (88, 94)
    assert window["playEvents"] == 4
    assert window["firstLogins"] == 1


def test_without_the_world_clock_today_is_null_and_the_window_ends_at_the_last_active_day() -> None:
    payload = _surface_for([0, 1, 2]).to_dict()

    assert payload["today"] is None
    assert payload["recentWindow"]["endsAt"] == "lastActiveDay"
    assert payload["recentWindow"]["toDay"] == 2
    assert all(bucket["cycle"] is None for bucket in payload["playByDay"])


def test_a_clock_restart_splits_the_series_into_cycles_instead_of_merging_day_five() -> None:
    """The replay file survives a cycle cut while the game clock restarts at zero."""
    surface = _surface_for([0.2, 5, 5, 59, 0.1, 5, 5, 5, 7])
    surface.apply_world_clock({"DaysRunning": 8, "Description": "Cycle 15"})

    buckets = surface.to_dict()["playByDay"]

    day_five = [b for b in buckets if b["day"] == 5]
    assert [(b["cycle"], b["count"]) for b in day_five] == [(14, 2), (15, 3)]
    ages = [b["cyclesAgo"] for b in buckets]
    assert ages == sorted(ages, reverse=True)
    older, newest = surface.to_dict()["cycles"]
    assert (older["cycle"], older["current"], older["lastDay"]) == (14, False, 59)
    assert (newest["cycle"], newest["current"], newest["playEvents"]) == (15, True, 5)


def test_jitter_inside_one_cycle_and_untimed_rows_do_not_invent_a_cycle() -> None:
    surface = SocialSurface(fetched_at_iso="t", source_base_url="b")
    events = _events("play", [3, 2.5, 4, 3.9, 6])
    events.insert(2, _ActivityEvent(time_s=0.0, day=0.0, citizen_id="1", kind="play"))
    build_surface(surface, [], events, {}, show_names=False)

    assert {ago for ago, _, _ in surface.play_by_day} == {0}


def test_older_cycles_align_from_the_newest_end_per_series() -> None:
    """FirstLogin may hold one cycle where Play holds two, so ages count back from the newest."""
    surface = _surface_for([0, 59, 0, 3], login_days=[0, 2])

    assert {a for a, _, _ in surface.play_by_day} == {0, 1}
    assert {a for a, _, _ in surface.first_logins_by_day} == {0}


def test_the_markdown_states_direction_and_today() -> None:
    surface = _surface_for([0, 0, 0, 1, 40, 41], login_days=[0])
    surface.total_play_events = 6
    surface.apply_world_clock({"DaysRunning": 44, "Description": "Cycle 14"})

    text = social_markdown(surface)

    assert "not days ago" in text
    assert "cycle 14" in text
    assert "today is day 44" in text
    assert "Peak was day 0 (3 events)" in text
    assert "days 38 to 44 held 2 play events" in text


def test_the_markdown_warns_when_one_day_number_covers_two_cycles() -> None:
    surface = _surface_for([0, 59, 0, 3])
    surface.total_play_events = 4

    assert "spans 2 cycles" in social_markdown(surface)


def test_the_cache_round_trip_keeps_the_cycle_buckets() -> None:
    surface = _surface_for([0, 59, 0, 3], login_days=[0, 2])

    again = _surface_from_dict(surface.to_dict())

    assert again.play_by_day == surface.play_by_day
    assert again.first_logins_by_day == surface.first_logins_by_day


async def _call_get_social() -> tuple[str, dict[str, Any]]:
    handler = build_server().request_handlers[mt.CallToolRequest]
    result = await handler(
        mt.CallToolRequest(
            method="tools/call",
            params=mt.CallToolRequestParams(
                name="get_social", arguments={"server": "eco.example.com:3001"}
            ),
        )
    )
    blocks = result.root.content
    return blocks[0].text, json.loads(blocks[1].text)


@pytest.mark.asyncio
@respx.mock
async def test_get_social_labels_the_axis_and_names_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ECO_ADMIN_API_KEY", "k")
    respx.get(PLAY_URL).mock(return_value=httpx.Response(200, text=_PLAY_CSV))
    respx.get(LOGIN_URL).mock(return_value=httpx.Response(200, text=_LOGIN_CSV))
    respx.get(REP_URL).mock(return_value=httpx.Response(200, text=_REP_CSV))
    respx.get(CITIZENS_URL).mock(return_value=httpx.Response(200, json=_CITIZENS_JSON))

    async def _info(_server: str | None = None) -> dict[str, Any]:
        return {"DaysRunning": 3, "Description": "<color=green>Eco</color> | Cycle 14"}

    monkeypatch.setattr(eco_server, "fetch_eco_info", _info)

    text, payload = await _call_get_social()

    assert payload["today"] == {"cycle": 14, "day": 3}
    assert payload["playByDay"] == [{"cycle": 14, "cyclesAgo": 0, "day": 0, "count": 2}]
    assert "not days ago" in text
    assert "today is day 3" in text
    keys = list(payload)
    assert keys.index("dayAxisNote") < keys.index("playByDay")
    assert keys.index("warnings") < keys.index("playByDay")


@pytest.mark.asyncio
@respx.mock
async def test_get_social_survives_an_unreachable_info(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ECO_ADMIN_API_KEY", "k")
    respx.get(PLAY_URL).mock(return_value=httpx.Response(200, text=_PLAY_CSV))
    respx.get(LOGIN_URL).mock(return_value=httpx.Response(200, text=_LOGIN_CSV))
    respx.get(REP_URL).mock(return_value=httpx.Response(200, text=_REP_CSV))
    respx.get(CITIZENS_URL).mock(return_value=httpx.Response(200, json=_CITIZENS_JSON))

    async def _down(_server: str | None = None) -> dict[str, Any]:
        raise httpx.ConnectError("down")

    monkeypatch.setattr(eco_server, "fetch_eco_info", _down)

    _text, payload = await _call_get_social()

    assert payload["today"] is None
    assert payload["playByDay"][0]["cycle"] is None
    assert any(w.startswith("world clock:") for w in payload["warnings"])
