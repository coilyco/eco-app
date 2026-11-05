"""get_stores by a store's given name, and the one-line owner answer (COI-3407)."""

from __future__ import annotations

import json
from typing import Any

import mcp.types as mt
import pytest

from eco_mcp_app import server as eco_server
from eco_mcp_app.reply_templates import REPLY_TEMPLATES, render_reply
from eco_mcp_app.server import build_server
from eco_mcp_app.store_match import RosterStore
from eco_mcp_app.store_owner import narrow_to_roster, owner_reply

ROSTER = [
    RosterStore(name="Fern & Co", owner="Mossbank", location="10,20,30"),
    RosterStore(name="Quill Corner", owner="Tamsin", location="40,50,60"),
    RosterStore(name="Quill Annex", owner="Odalys", location="70,80,90"),
]


def _payload() -> dict[str, Any]:
    return {
        "stores": [
            {
                "storeKey": "10,20,30|9",
                "label": "Mossbank's Store Block",
                "owner": "Mossbank",
                "tradeCount": 1204,
                "lastDay": 27.46,
            },
            {
                "storeKey": "40,50,60|8",
                "label": "Tamsin's Store Block",
                "owner": "Tamsin",
                "tradeCount": 3,
                "lastDay": 12.0,
            },
        ],
        "traders": [{"name": "Mossbank"}, {"name": "Tamsin"}],
        "warnings": [],
    }


def test_a_given_name_finds_the_history_row_by_location_not_by_label() -> None:
    payload = _payload()

    match = narrow_to_roster(payload, "fern & co", ROSTER)

    assert [r["owner"] for r in payload["stores"]] == ["Mossbank"]
    assert [t["name"] for t in payload["traders"]] == ["Mossbank"]
    assert [s.name for s in match.matched] == ["Fern & Co"]


def test_the_owner_is_named_with_the_store_s_own_trade_count() -> None:
    payload = _payload()
    match = narrow_to_roster(payload, "Fern & Co", ROSTER)

    assert owner_reply(match, payload) == (
        "Fern & Co is owned by Mossbank. 1,204 trades on record, the latest on day 27."
    )


def test_a_named_store_with_no_history_row_still_names_its_owner() -> None:
    payload: dict[str, Any] = {"stores": [], "traders": [], "warnings": []}
    match = narrow_to_roster(payload, "Quill Corner", ROSTER)

    assert (
        owner_reply(match, payload)
        == "Quill Corner is owned by Tamsin. It has no trades on record."
    )


def test_two_stores_matching_one_name_get_no_reply_for_the_model_to_settle() -> None:
    payload = _payload()
    match = narrow_to_roster(payload, "Quill", ROSTER)

    assert len(match.matched) == 2
    assert owner_reply(match, payload) is None


def test_a_near_name_is_not_answered_as_if_it_were_exact() -> None:
    payload = _payload()
    match = narrow_to_roster(payload, "Fern and Co.", ROSTER)

    assert match.resolved_from
    assert owner_reply(match, payload) is None


def test_a_name_no_store_has_leaves_the_payload_for_the_old_filter() -> None:
    payload = _payload()

    match = narrow_to_roster(payload, "Nowhere Emporium", ROSTER)

    assert not match.matched
    assert len(payload["stores"]) == 2
    assert owner_reply(match, payload) is None


def test_the_get_stores_template_renders_the_reply_and_skips_a_null() -> None:
    template = REPLY_TEMPLATES["get_stores"]

    assert render_reply(template, {"reply": "Fern & Co is owned by Mossbank."}, {"store": "x"})
    assert render_reply(template, {"reply": None}, {"store": "x"}) is None


@pytest.mark.asyncio
async def test_get_stores_by_given_name_carries_the_owner_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Directory:
        def to_dict(self) -> dict[str, Any]:
            return _payload()

    async def _fetch(**_: Any) -> _Directory:
        return _Directory()

    async def _roster(*_: Any) -> list[RosterStore]:
        return ROSTER

    monkeypatch.setattr(eco_server, "fetch_directory", _fetch)
    monkeypatch.setattr(eco_server, "live_roster", _roster)
    monkeypatch.setattr(eco_server, "directory_markdown", lambda _d: "stub")
    handler = build_server().request_handlers[mt.CallToolRequest]

    result = await handler(
        mt.CallToolRequest(
            method="tools/call",
            params=mt.CallToolRequestParams(name="get_stores", arguments={"store": "Fern & Co"}),
        )
    )

    payload = json.loads(result.root.content[-1].text)
    assert payload["reply"].startswith("Fern & Co is owned by Mossbank.")
    assert [r["owner"] for r in payload["stores"]] == ["Mossbank"]
