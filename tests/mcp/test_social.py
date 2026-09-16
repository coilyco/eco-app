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

from eco_mcp_app import social as social_mod
from eco_mcp_app.http_app import create_app
from eco_mcp_app.server import build_server
from eco_mcp_app.social import (
    SocialSurface,
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
    assert surface.play_by_day == [(0, 2)]
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
