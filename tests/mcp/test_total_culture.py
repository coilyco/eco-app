"""`/info`'s TotalCulture is reconciled against milestone progress (eco-app#237).

Sirens reports `TotalCulture: 0` while the same payload lists 910 culture of
milestone progress from 26 works by 18 artists. GreenLeaf Prime returns a real
number through the same code path, so the field is unreliable per server rather
than always broken — and publishing the zero as an economic KPI told a reader
the server had no cultural output, with no way to know the field was suspect.
"""

from __future__ import annotations

from typing import Any

from eco_mcp_app.server import (
    _format_milestones_markdown,
    build_milestones_payload,
    resolve_total_culture,
    to_payload,
)

# Trimmed to the shape parse_achievement reads: target on line one, current
# progress on line two.
_ACHIEVEMENTS = {
    "Ascendent Civilization": "Create 100000 total culture\n910.32 Culture",
    "Cultural Nexus": "Create 20000 total culture\n384.57 Culture",
    "Sparkling Canvas": "Create 50 total culture\n27.71 Culture",
}

_SIRENS_INFO: dict[str, Any] = {
    "Description": "Eco via Sirens",
    "WorldSize": "0.52 km²",
    "TotalCulture": 0.0,
    "ServerAchievementsDict": _ACHIEVEMENTS,
    "HasMeteor": True,
    "DaysUntilMeteor": 20,
}


def test_zero_culture_falls_back_to_the_milestone_floor() -> None:
    value, source = resolve_total_culture(_SIRENS_INFO)
    assert value == 910.32
    assert source == "milestones"


def test_a_real_culture_number_is_left_alone() -> None:
    # GreenLeaf Prime through the same code path.
    value, source = resolve_total_culture(dict(_SIRENS_INFO, TotalCulture=1291.86))
    assert value == 1291.86
    assert source == "info"


def test_zero_with_no_milestone_progress_stays_zero() -> None:
    value, source = resolve_total_culture(dict(_SIRENS_INFO, ServerAchievementsDict={}))
    assert value == 0.0
    assert source == "info"


def test_status_payload_publishes_the_reconciled_value_and_its_source() -> None:
    world = to_payload(_SIRENS_INFO)["world"]
    assert world["totalCulture"] == 910.32
    assert world["totalCultureSource"] == "milestones"


def test_milestones_payload_flags_the_substitution() -> None:
    payload = build_milestones_payload(_SIRENS_INFO)
    assert payload["totalCulture"] == 910.32
    assert payload["totalCultureSource"] == "milestones"
    assert payload["totalCultureNote"]
    markdown = _format_milestones_markdown(payload)
    # The headline must not read as the server's own counter.
    assert "910.3+" in markdown
    assert "from milestones" in markdown


def test_milestones_markdown_stays_plain_when_the_counter_works() -> None:
    payload = build_milestones_payload(dict(_SIRENS_INFO, TotalCulture=1291.86))
    assert payload["totalCultureNote"] is None
    markdown = _format_milestones_markdown(payload)
    assert "1291.9" in markdown
    assert "from milestones" not in markdown


def test_meteor_countdown_is_null_when_no_meteor_is_coming() -> None:
    # GreenLeaf returns daysUntilMeteor: -17 with hasMeteor: false — a negative
    # countdown to an event that is not happening.
    cycle = to_payload(dict(_SIRENS_INFO, HasMeteor=False, DaysUntilMeteor=-17))["cycle"]
    assert cycle["hasMeteor"] is False
    assert cycle["daysUntilMeteor"] is None


def test_meteor_countdown_is_null_once_the_meteor_date_has_passed() -> None:
    # Sirens reported hasMeteor: true with daysUntilMeteor: -22, which the direct
    # status reply rendered as "The meteor hits in -22 days".
    cycle = to_payload(dict(_SIRENS_INFO, HasMeteor=True, DaysUntilMeteor=-22))["cycle"]
    assert cycle["hasMeteor"] is True
    assert cycle["daysUntilMeteor"] is None


def test_meteor_countdown_survives_when_a_meteor_is_coming() -> None:
    cycle = to_payload(_SIRENS_INFO)["cycle"]
    assert cycle["hasMeteor"] is True
    assert cycle["daysUntilMeteor"] == 20


def test_a_zero_animal_count_is_marked_unreliable() -> None:
    """/info.Animals reads 0 even on servers with live fauna (eco-app#246).

    Both servers in the 2026-08-12 sweep reported 0 while get_region tracked
    Deer 248, Wolf 167 and Bison 114 on the same fetch. A zero here is not
    evidence of no animals, so it does not get to pass as a count.
    """
    world = to_payload(dict(_SIRENS_INFO, Animals=0))["world"]
    assert world["animals"] == 0
    assert world["animalsNote"]
    assert "get_region" in world["animalsNote"]


def test_a_real_animal_count_carries_no_caveat() -> None:
    world = to_payload(dict(_SIRENS_INFO, Animals=529))["world"]
    assert world["animals"] == 529
    assert world["animalsNote"] is None
