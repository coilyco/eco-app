"""`get_server_status` names the meteor's state instead of leaving a null (COI-2044).

Once the meteor is destroyed Eco's `/info` keeps `HasMeteor` true and drops the
countdown, so `daysUntilMeteor: null` read as unknown. The only record is the
`ServerAchievementsDict` line, which is the fixture for the destroyed case.
"""

from __future__ import annotations

from typing import Any

from eco_mcp_app.reply_templates import REPLY_TEMPLATES, render_reply
from eco_mcp_app.server import _format_markdown, to_payload

_BASE: dict[str, Any] = {
    "Description": "Eco via Sirens",
    "Category": "Test",
    "Version": "0.13.0.4",
    "WorldSize": "0.52 km²",
    "CollaborationLevel": "HighCollaboration",
    "GameSpeed": "Slow",
    "SimulationLevel": "Full",
    "OnlinePlayers": 7,
    "DaysRunning": 70,
}

# The live shape: HasMeteor stays true, no DaysUntilMeteor, and the world
# achievement carries the destruction day inside Eco's inline markup.
_DESTROYED: dict[str, Any] = {
    **_BASE,
    "HasMeteor": True,
    "ServerAchievementsDict": {
        "Saved the World": (
            "Destroyed the meteor on a server.\n"
            '<style="Positive">Destroyed the meteor on Day 57, 23:13</style>'
        ),
        "Culture": "Create 250 total culture.\n57.6 Culture",
    },
}

_PENDING: dict[str, Any] = {**_BASE, "HasMeteor": True, "DaysUntilMeteor": 20}


def test_destroyed_meteor_says_which_day() -> None:
    cycle = to_payload(_DESTROYED)["cycle"]
    assert cycle["meteor"]["state"] == "destroyed"
    assert cycle["meteor"]["destroyedOnDay"] == 57
    assert cycle["meteor"]["destroyedAtTime"] == "23:13"
    assert "destroyed on day 57 at 23:13" in cycle["meteor"]["summary"]
    # The countdown stays null, and the state beside it says why.
    assert cycle["daysUntilMeteor"] is None


def test_destroyed_meteor_wins_over_a_reported_countdown() -> None:
    cycle = to_payload({**_DESTROYED, "DaysUntilMeteor": 5})["cycle"]
    assert cycle["meteor"]["state"] == "destroyed"


def test_destroyed_markdown_names_the_day_not_a_bare_count() -> None:
    markdown = _format_markdown(to_payload(_DESTROYED))
    meteor_line = next(line for line in markdown.splitlines() if line.startswith("- Meteor:"))
    assert "destroyed on day 57 at 23:13, no countdown" in meteor_line
    assert "not reported" not in meteor_line


def test_destroyed_reply_template_states_the_day() -> None:
    reply = render_reply(REPLY_TEMPLATES["get_server_status"], to_payload(_DESTROYED), {})
    assert reply == (
        "7 players online on day 70. The meteor was destroyed on day 57. Server version 0.13.0.4."
    )


def test_pending_meteor_reports_the_countdown() -> None:
    cycle = to_payload(_PENDING)["cycle"]
    assert cycle["meteor"]["state"] == "pending"
    assert cycle["meteor"]["destroyedOnDay"] is None
    assert cycle["daysUntilMeteor"] == 20
    assert "20 days away" in cycle["meteor"]["summary"]


def test_pending_markdown_and_reply_keep_the_countdown() -> None:
    payload = to_payload(_PENDING)
    assert "- Meteor: **20 days away** ☄" in _format_markdown(payload)
    assert "The meteor hits in 20 days." in (
        render_reply(REPLY_TEMPLATES["get_server_status"], payload, {}) or ""
    )


def test_no_meteor_and_unreported_are_distinct_states() -> None:
    assert to_payload({**_BASE, "HasMeteor": False})["cycle"]["meteor"]["state"] == "none"
    # Flagged, no countdown, no destruction record: honest unknown.
    assert to_payload({**_BASE, "HasMeteor": True})["cycle"]["meteor"]["state"] == "unreported"
    # A negative countdown with no destruction record is also unreported (#237).
    passed = to_payload({**_BASE, "HasMeteor": True, "DaysUntilMeteor": -22})
    assert passed["cycle"]["meteor"]["state"] == "unreported"
