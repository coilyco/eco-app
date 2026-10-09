"""Unit coverage for the `/info` achievement parser.

`get_milestones` is gone (COI-2090), but `get_server_status` still reads the same
`ServerAchievementsDict` for its culture floor and the destroyed-meteor day, so the
parser keeps its tests.
"""

from __future__ import annotations

import pytest

from eco_mcp_app.server import parse_achievement

# Sample shape matching the live Day-3 /info response. Keep the strings
# byte-accurate so regressions in the markup stripper surface here.
_ACHIEVEMENTS = {
    "Cultural Awakening": (
        "Create 250 total culture as a world.\n"
        '<style="Culture"><icon name="Culture" type="nobg"></icon>57.6 Culture</style>'
        ' from <style="Positive">2</style> works from <style="Positive">1</style> artists.'
    ),
    "Sparkling Canvas": (
        "Create 50 total culture as a world.\n"
        '<style="Culture"><icon name="Culture" type="nobg"></icon>22.72 Culture</style>'
        ' from <style="Positive">1</style> work from <style="Positive">1</style> artist.'
    ),
    "Cultural Trailblazers ": (
        "Create 100 total culture as a world.\n"
        '<style="Culture"><icon name="Culture" type="nobg"></icon>29.36 Culture</style>'
        ' from <style="Positive">1</style> work from <style="Positive">1</style> artist.'
    ),
    "Incipient Renaissance": (
        "Create 500 total culture as a world.\n"
        '<style="Culture"><icon name="Culture" type="nobg"></icon>57.04 Culture</style>'
        ' from <style="Positive">4</style> works from <style="Positive">3</style> artists.'
    ),
    "Cultural Vanguard": (
        "Create 1000 total culture as a world.\n"
        '<style="Culture"><icon name="Culture" type="nobg"></icon>122.35 Culture</style>'
        ' from <style="Positive">4</style> works from <style="Positive">3</style> artists.'
    ),
}


def test_parse_achievement_extracts_target_and_current() -> None:
    row = parse_achievement(
        "Cultural Awakening",
        _ACHIEVEMENTS["Cultural Awakening"],
    )
    assert row["name"] == "Cultural Awakening"
    assert row["target"] == 250
    assert row["current"] == pytest.approx(57.6)
    # 57.6 / 250 ~= 23.04%
    assert 23.0 <= row["pct"] <= 23.1


def test_parse_achievement_strips_all_eco_markup() -> None:
    row = parse_achievement("X", _ACHIEVEMENTS["Sparkling Canvas"])
    stripped = row["stripped"]
    assert "<style" not in stripped
    assert "</style>" not in stripped
    assert "<icon" not in stripped
    assert "</icon>" not in stripped
    # Content survives
    assert "22.72 Culture" in stripped


def test_parse_achievement_handles_missing_numbers() -> None:
    # Nothing to parse — both fields end up None and pct is 0.0 rather than
    # crashing. Empty-state handling in the template depends on this.
    row = parse_achievement("weird", "Make culture happen.")
    assert row["target"] is None
    assert row["current"] is None
    assert row["pct"] == 0.0


def test_parse_achievement_handles_empty_string() -> None:
    row = parse_achievement("blank", "")
    assert row["target"] is None
    assert row["current"] is None
    assert row["pct"] == 0.0
    assert row["stripped"] == ""
