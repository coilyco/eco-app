"""One item word to one Eco item and its median price per upgrade stage
(teable:coilyco/eco-app#8423). The expected figures are game-dev's prototype run
against the bundled norms file at aacb6bf."""

from __future__ import annotations

import json
from typing import Any

import mcp.types as mt
import pytest
from pydantic import AnyUrl

from eco_mcp_app import norms, upgrade_words
from eco_mcp_app.reply_templates import MAX_REPLY_CHARS, REPLY_TEMPLATES, render_reply
from eco_mcp_app.server import _recipe_items, build_server
from eco_mcp_app.vocab import PRICED_ITEMS_URI

LIVE = norms.LiveContext(stage="Modern 4", cycle=14)


def _stage(norm: norms.Norms, word: str) -> dict[str, Any]:
    return norm.price_by_stage(word, LIVE)


@pytest.fixture(scope="module")
def real() -> norms.Norms:
    loaded = norms.load()
    assert loaded is not None, "data/eco_trades_norms.json.gz is not bundled"
    return loaded


@pytest.mark.parametrize(
    ("word", "item"),
    [
        ("Iron Bar", "Iron Bar"),
        ("iron bar", "Iron Bar"),
        ("IronBarItem", "Iron Bar"),
        ("iron", "Iron Bar"),
        ("copper", "Copper Bar"),
        ("hewn logs", "Hewn Log"),
        ("  hewn   log ", "Hewn Log"),
        ("Basic Upgrade 4", "Basic Upgrade 4"),
        ("a basic upgrade 4", "Basic Upgrade 4"),
        ("my bricks", "Brick"),
        ("some iron", "Iron Bar"),
        ("The Grasshopper", "The Grasshopper"),
        ("the grasshopper", "The Grasshopper"),
        ("solar panels", "Solar Generator"),
    ],
)
def test_a_word_resolves_by_name_id_plural_or_metal(
    real: norms.Norms, word: str, item: str
) -> None:
    assert real.resolve(word) == item


@pytest.mark.parametrize(
    "word",
    ["unobtainium", "dragon scales", "glorbnite", "cotton", "", "ron", "the", "a unobtainium"],
)
def test_nothing_is_guessed(real: norms.Norms, word: str) -> None:
    out = _stage(real, word)
    assert out["resolved"] is None
    # An unresolved item carries no price field at all.
    assert set(out) == {"query", "resolved", "candidates", "reply"}


def test_candidates_list_only_two_to_five_whole_word_names(real: norms.Norms) -> None:
    # "cotton" is whole in 17 names, past the cap, and "ron" is inside words only.
    assert real.candidates("cotton") == []
    assert real.candidates("ron") == []
    tallow = ["Tallow", "Tallow Candle", "Tallow Lamp", "Tallow Wall Lamp"]
    assert real.candidates("tallow") == tallow
    assert real.price_by_stage("tallows wax", LIVE)["candidates"] == []


def test_iron_matches_the_prototype(real: norms.Norms) -> None:
    out = _stage(real, "iron")
    assert (out["item"], out["itemId"], out["currency"], out["cycle"], out["liveStage"]) == (
        "Iron Bar",
        "IronBarItem",
        "Spectres",
        14,
        "Modern 4",
    )
    rows = {r["stage"]: (r["median"], r["n"]) for r in out["stages"]}
    assert rows["Basic 2"] == (1.05, 28)
    assert rows["Basic 4"] == (1.31, 348)
    assert rows["Advanced 1"] == (0.89, 163)
    assert rows["Modern 1"] == (0.43, 278)
    assert rows["Modern 2"] == (0.58, 136)
    assert rows["Modern 4"] == (0.58, 181)


@pytest.mark.parametrize(("word", "floor"), [("iron", "Basic 1"), ("hewn logs", "none")])
def test_acceptance_items_price_every_stage_from_their_floor(
    real: norms.Norms, word: str, floor: str
) -> None:
    # Kai on #8423: a guess beats silence. Iron's floor is a Basic 1 trade in a
    # non-primary currency, which crossCycle alone would miss.
    out = _stage(real, word)
    assert out["firstTradedStage"] == floor
    start = norms.STAGES.index(floor)
    assert [r["stage"] for r in out["stages"]] == list(norms.STAGES[start:])
    assert all(r["median"] is not None for r in out["stages"])
    for row in out["stages"]:
        assert row["estimated"] == (row["n"] < norms.MIN_N)


def test_a_thin_stage_is_estimated_by_flat_carry_or_linear_interpolation(
    real: norms.Norms,
) -> None:
    hewn = {r["stage"]: r for r in _stage(real, "hewn logs")["stages"]}
    # Below the lowest real stage: carried from Basic 1.
    none = hewn["none"]
    assert (none["median"], none["estimated"], none["n"]) == (0.52, True, 3)
    upgrade = {r["stage"]: r["median"] for r in _stage(real, "Basic Upgrade 4")["stages"]}
    # Advanced 3 has 3 trades, halfway between Advanced 2 and Advanced 4.
    assert upgrade["Advanced 3"] == 209.85
    assert (upgrade["Modern 2"], upgrade["Modern 3"]) == (176.0, 169.72)


@pytest.mark.parametrize(
    ("word", "floor"), [("Solar Generator", "Modern 1"), ("Combustion Engine", "Advanced 4")]
)
def test_nothing_is_priced_below_the_first_traded_stage(
    real: norms.Norms, word: str, floor: str
) -> None:
    out = _stage(real, word)
    assert out["firstTradedStage"] == floor
    assert out["stages"][0]["stage"] == floor
    assert out["reply"].endswith(f"None before {floor}.")


def test_solar_generator_carries_its_one_real_median_down_to_the_floor(
    real: norms.Norms,
) -> None:
    rows = [
        (r["stage"], r["median"], r["estimated"]) for r in _stage(real, "solar panel")["stages"]
    ]
    assert rows == [
        ("Modern 1", 421.11, True),
        ("Modern 2", 421.11, True),
        ("Modern 3", 421.11, True),
        ("Modern 4", 421.11, False),
    ]


def test_an_item_with_only_thin_stages_still_gets_estimates(real: norms.Norms) -> None:
    name = next(
        n
        for n, it in real.items.items()
        if it["crossCycle"] and all(b["n"] < norms.MIN_N for b in it["crossCycle"].values())
    )
    rows = _stage(real, name)["stages"]
    assert rows and all(r["median"] is not None and r["estimated"] for r in rows)


def test_an_upgrade_name_is_the_item_not_a_stage(real: norms.Norms) -> None:
    rows = {r["stage"]: (r["median"], r["n"]) for r in _stage(real, "Basic Upgrade 4")["stages"]}
    assert rows["Basic 4"] == (219.13, 13)
    assert rows["Modern 4"] == (163.44, 19)


def test_a_cycle_missing_from_the_file_prices_nothing_and_says_why(real: norms.Norms) -> None:
    out = real.price_by_stage("iron", norms.LiveContext(stage=None, cycle=99))
    assert out["stages"] and all(r["median"] is None for r in out["stages"])
    assert "cycle 99" in out["note"] and out["reply"].startswith("Iron Bar: cycle 99 is not")


def test_another_server_gets_no_stages(real: norms.Norms) -> None:
    out = real.price_by_stage("iron", norms.LiveContext(stage=None, cycle=None, home=False))
    assert out["stages"] == [] and out["note"] == "norms describe the Sirens server only"


async def _call(arguments: dict[str, Any]) -> tuple[mt.CallToolResult, str, dict[str, Any]]:
    """The result, its readable block, and its JSON block."""
    handler = build_server().request_handlers[mt.CallToolRequest]
    result = await handler(
        mt.CallToolRequest(
            method="tools/call",
            params=mt.CallToolRequestParams(name="price_by_stage", arguments=arguments),
        )
    )
    root = result.root
    assert isinstance(root, mt.CallToolResult)
    text, blob = root.content
    assert isinstance(text, mt.TextContent) and isinstance(blob, mt.TextContent)
    return root, text.text, json.loads(blob.text)


async def test_the_tool_answers_through_mcp_with_one_line_per_stage() -> None:
    result, text, payload = await _call({"item": "hewn logs"})
    assert result.isError is False
    assert text.startswith("**Hewn Log**") and "- Basic 4: 0.38 (178 trades)" in text
    assert "- none: ~0.52 estimated (3 trades)" in text
    assert payload["resolved"] == "Hewn Log"
    # No `norm`: it would carry a second median on another basis beside the stages.
    assert "norm" not in payload and "normContext" not in payload


async def test_the_tool_says_it_found_nothing() -> None:
    result, text, payload = await _call({"item": "glorbnite"})
    assert result.isError is False
    assert text == "Couldn't match 'glorbnite' to one Eco item."
    assert payload["resolved"] is None and "stages" not in payload


def test_the_reply_template_renders_for_found_and_missing(real: norms.Norms) -> None:
    templates = REPLY_TEMPLATES["price_by_stage"]
    found = render_reply(templates, _stage(real, "hewn logs"), {"item": "Hewn Log"})
    assert found == (
        "Hewn Log median Spectres by stage (trades, ~est.): no upgrade ~0.52. "
        "Basic 1 0.52 (9), 2 0.52 (169), 3 0.46 (21), 4 0.38 (178). "
        "Advanced 1 0.35 (90), 2 0.30 (76), 3 0.32 (37), 4 0.29 (132). "
        "Modern 1 0.36 (192), 2 0.31 (55), 3 0.33 (19), 4 0.26 (149)."
    )
    missing = render_reply(templates, _stage(real, "glorbnite"), {"item": "glorbnite"})
    assert missing == "Couldn't match 'glorbnite' to one Eco item."


def test_every_item_fits_the_template_cap(real: norms.Norms) -> None:
    # Past the cap the caller drops to its model path, which is the #8421 failure.
    over = [n for n in real.items if len(_stage(real, n)["reply"]) > MAX_REPLY_CHARS]
    assert over == []


def test_every_vocabulary_form_resolves_to_its_own_entry(real: norms.Norms) -> None:
    # A caller fills `item` from this vocabulary, so every form must land where it says.
    catalog = _recipe_items()
    pseudo = {e["name"] for e in upgrade_words.pseudo_entries()}
    for entry in real.vocabulary(catalog):
        for form in (entry["name"], entry["id"], *entry["aliases"]):
            name, miss = real.find(form, catalog)
            if entry["name"] in pseudo:
                assert name is None and miss["note"], form
            else:
                assert name == entry["name"], form
    iron = next(e for e in real.vocabulary(catalog) if e["name"] == "Iron Bar")
    assert iron == {"id": "IronBarItem", "name": "Iron Bar", "aliases": ["IronBarItem", "Iron"]}


async def test_the_priced_items_vocabulary_is_served() -> None:
    handler = build_server().request_handlers[mt.ReadResourceRequest]
    request = mt.ReadResourceRequest(
        method="resources/read",
        params=mt.ReadResourceRequestParams(uri=AnyUrl(PRICED_ITEMS_URI)),
    )
    contents = (await handler(request)).root.contents
    assert isinstance(contents[0], mt.TextResourceContents)
    names = {e["name"] for e in json.loads(contents[0].text)["entries"]}
    assert {"Iron Bar", "Hewn Log", "Basic Upgrade 4"} <= names


def test_the_miss_entry_is_literal_and_never_renders_over_a_payload(real: norms.Norms) -> None:
    miss = REPLY_TEMPLATES["price_by_stage"][-1]
    assert miss == {"when_unmatched": ["item"], "text": "Couldn't match that to one Eco item."}
    # Only the item template answers a found item, even with the miss listed.
    found = render_reply([miss], _stage(real, "iron"), {"item": "Iron Bar"})
    assert found is None
