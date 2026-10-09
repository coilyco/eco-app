"""Upgrade shorthand as an item and as a stage qualifier (teable:coilyco/eco-app#8425).

Each case is a line of game-dev's done list, with the scientist's corrections
from the norms file: a recipe item with no trades is an item, not a miss, and
MU0 on an item first traded at Modern 1 has nothing to show."""

from __future__ import annotations

import json
from typing import Any

import mcp.types as mt
import pytest
from pydantic import AnyUrl

from eco_mcp_app import norms, upgrade_words
from eco_mcp_app.reply_templates import MAX_REPLY_CHARS, REPLY_TEMPLATES, render_reply
from eco_mcp_app.server import _recipe_items, build_server
from eco_mcp_app.vocab import ARGS_META_KEY, STAGES_URI

LIVE = norms.LiveContext(stage="Modern 4", cycle=14)


@pytest.fixture(scope="module")
def real() -> norms.Norms:
    loaded = norms.load()
    assert loaded is not None
    return loaded


def _ask(real: norms.Norms, word: str, stage: str | None = None) -> dict[str, Any]:
    return real.price_by_stage(word, LIVE, stage=stage, catalog=_recipe_items())


@pytest.mark.parametrize(
    ("token", "stage"),
    [
        ("au3", "Advanced 3"),
        ("AU 3", "Advanced 3"),
        ("sbu4", "Basic 4"),
        ("bu0", "none"),
        ("au0", "Basic 4"),
        ("MU0", "Advanced 4"),
        ("bu5", "Basic 4"),
        ("Advanced 2", "Advanced 2"),
        ("mu9", None),
    ],
)
def test_a_qualifier_names_a_ladder_stage(token: str, stage: str | None) -> None:
    assert upgrade_words.parse_stage(token) == stage


@pytest.mark.parametrize(
    ("word", "item"),
    [
        ("au3", "Advanced Upgrade 3"),
        ("AU 3", "Advanced Upgrade 3"),
        ("sbu4", "Scholars Basic Upgrade 4"),
        ("smu2", "Scholars Modern Upgrade 2"),
    ],
)
def test_shorthand_alone_is_the_upgrade_item(real: norms.Norms, word: str, item: str) -> None:
    out = _ask(real, word)
    assert out["resolved"] == item and "stage" not in out


def test_bu5_lists_the_basic_specialists_with_no_price(real: norms.Norms) -> None:
    out = _ask(real, "bu5")
    assert out["resolved"] is None and "stages" not in out
    # 18 vanilla and 3 mod modules, per the Eco 0.14 source table.
    assert "Mining Basic Upgrade" in out["candidates"] and len(out["candidates"]) == 21
    assert out["reply"].startswith("BU5 is a Basic specialist: engineering, blacksmith,")
    assert len(out["reply"]) <= 280


def test_a_specialty_word_picks_one_module_even_with_no_trades(real: norms.Norms) -> None:
    out = _ask(real, "mining bu5")
    assert (out["resolved"], out["traded"], out["stages"]) == ("Mining Basic Upgrade", False, [])
    assert out["reply"] == "Mining Basic Upgrade has no recorded trades."


@pytest.mark.parametrize(
    ("word", "item"),
    [
        ("Basic Gathering Upgrade", "Gathering Basic Upgrade"),
        ("basic gathering upgrades", "Gathering Basic Upgrade"),
        ("Gathering Basic Upgrade", "Gathering Basic Upgrade"),
        # Both orders exist for Masonry at Advanced, so each resolves as written.
        ("Advanced Masonry Upgrade", "Advanced Masonry Upgrade"),
        ("Masonry Advanced Upgrade", "Masonry Advanced Upgrade"),
    ],
)
def test_either_word_order_names_a_tiered_module(real: norms.Norms, word: str, item: str) -> None:
    assert _ask(real, word)["resolved"] == item


@pytest.mark.parametrize(
    ("word", "item"),
    [
        # The tier is the recipe's tier-4 input, not the name: Advanced Masonry is MU5.
        ("masonry bu5", "Masonry Basic Upgrade"),
        ("masonry au5", "Masonry Advanced Upgrade"),
        ("masonry mu5", "Advanced Masonry Upgrade"),
        ("smelting bu5", "Smelting Basic Upgrade"),
        # Two AU5 smelting modules, and the one named exactly "Smelting" wins.
        ("smelting au5", "Smelting Upgrade"),
        ("advanced smelting au5", "Advanced Smelting Upgrade"),
        ("blacksmith bu5", "Blacksmith Upgrade"),
        ("bu5 gathering", "Gathering Basic Upgrade"),
        ("tailoring mu5", "Tailoring Modern Upgrade"),
    ],
)
def test_a_specialty_and_its_5_resolve_through_the_source_table(
    real: norms.Norms, word: str, item: str
) -> None:
    assert _ask(real, word)["resolved"] == item


def test_a_trade_only_in_an_incomparable_currency_says_so(real: norms.Norms) -> None:
    # Advanced Masonry's one trade was cycle 11, Modern 4, in Nookies, which is
    # not that cycle's primary currency, so no crossCycle median exists.
    out = _ask(real, "masonry mu5")
    assert (out["resolved"], out["trades"], out["stages"]) == ("Advanced Masonry Upgrade", 1, [])
    assert out["reply"] == (
        "Advanced Masonry Upgrade has 1 recorded trade, none in a comparable currency, "
        "so no median."
    )


def test_a_specialty_not_in_that_tier_is_no_module(real: norms.Norms) -> None:
    assert _ask(real, "pottery bu5")["resolved"] is None


@pytest.mark.parametrize(
    ("word", "reply"),
    [
        ("mu0", "MU0 means no Modern upgrade, it is not an item."),
        ("sbu5", "SBU5 is not an Eco module: specialist upgrades have no Scholars form."),
    ],
)
def test_shorthand_naming_no_item_says_what_it_means(
    real: norms.Norms, word: str, reply: str
) -> None:
    out = _ask(real, word)
    assert out["resolved"] is None and out["reply"] == reply


def test_the_shorthand_in_a_phrase_qualifies_and_the_rest_is_the_item(real: norms.Norms) -> None:
    out = _ask(real, "iron at au3")
    got = (out["resolved"], out["stage"], out["stageShorthand"])
    assert got == ("Iron Bar", "Advanced 3", "AU3")
    assert out["reply"].startswith("Iron Bar at Advanced 3 (AU3): 0.97 (38 trades) median Spectres")
    assert "not your cost to craft with that module" in out["reply"]
    assert out == _ask(real, "iron", stage="au3") | {"query": "iron at au3"}


def test_a_qualifier_below_the_floor_prices_nothing_there(real: norms.Norms) -> None:
    out = _ask(real, "nylon fabric at MU0")
    assert (out["resolved"], out["stage"], out["firstTradedStage"]) == (
        "Nylon Fabric",
        "Advanced 4",
        "Modern 1",
    )
    assert out["reply"].startswith(
        "Nylon Fabric was never traded before Modern 1, so nothing at Advanced 4 (MU0), "
        "the market price at that world stage, not your cost to craft with that module."
    )


def test_a_5_qualifies_as_its_tiers_4(real: norms.Norms) -> None:
    out = _ask(real, "bricks at bu5")
    assert (out["resolved"], out["stage"]) == ("Brick", "Basic 4")
    assert out["reply"].startswith("Brick at Basic 4 (BU5): ")


def test_every_shorthand_reply_fits_the_template_cap(real: norms.Norms) -> None:
    words = [e["name"] for e in upgrade_words.pseudo_entries()]
    words += ["iron at au3", "nylon fabric at MU0", "bricks at bu5", "hewn logs at smu4"]
    over = {w: len(r) for w in words if len(r := _ask(real, w)["reply"]) > 280}
    assert over == {}


def test_the_stage_vocabulary_maps_each_token_to_its_stage() -> None:
    by_alias = {a: e["name"] for e in upgrade_words.stage_vocabulary() for a in e["aliases"]}
    assert by_alias["mu0"] == "Advanced 4" and by_alias["au 5"] == "Advanced 4"
    assert by_alias["bu0"] == "none" and "sbu5" not in by_alias


async def _tool_reply(arguments: dict[str, Any]) -> dict[str, Any]:
    """The JSON block `price_by_stage` returns over MCP, the path a deployed caller takes."""
    handler = build_server().request_handlers[mt.CallToolRequest]
    result = await handler(
        mt.CallToolRequest(
            method="tools/call",
            params=mt.CallToolRequestParams(name="price_by_stage", arguments=arguments),
        )
    )
    assert isinstance(result.root, mt.CallToolResult) and not result.root.isError
    blob = result.root.content[1]
    assert isinstance(blob, mt.TextContent)
    return json.loads(blob.text)


async def test_the_tool_takes_stage_as_its_own_argument() -> None:
    by_arg = await _tool_reply({"item": "iron", "stage": "au3"})
    in_phrase = await _tool_reply({"item": "iron at au3"})
    assert (by_arg["resolved"], by_arg["stage"]) == ("Iron Bar", "Advanced 3")
    assert by_arg["reply"] == in_phrase["reply"]
    ladder = await _tool_reply({"item": "iron", "stage": "Advanced 3"})
    assert ladder["reply"].startswith("Iron Bar at Advanced 3: ")


async def test_the_tool_schema_and_meta_advertise_stage() -> None:
    handler = build_server().request_handlers[mt.ListToolsRequest]
    listed = (await handler(mt.ListToolsRequest(method="tools/list"))).root.tools
    tool = next(t for t in listed if t.name == "price_by_stage")
    assert {"item", "stage"} <= set(tool.inputSchema["properties"])
    assert tool.inputSchema["required"] == ["item"]
    assert tool.meta is not None
    # Echo's matchVocab fills `stage` from the stage vocabulary, `ignore` keeping the word "none".
    assert tool.meta[ARGS_META_KEY]["stage"] == {
        "vocabulary": STAGES_URI,
        "field": "name",
        "ignore": ["none"],
    }


async def test_the_stage_vocabulary_is_served_and_holds_no_item_word() -> None:
    handler = build_server().request_handlers[mt.ReadResourceRequest]
    request = mt.ReadResourceRequest(
        method="resources/read", params=mt.ReadResourceRequestParams(uri=AnyUrl(STAGES_URI))
    )
    contents = (await handler(request)).root.contents
    assert isinstance(contents[0], mt.TextResourceContents)
    entries = json.loads(contents[0].text)["entries"]
    by_alias = {a: e["name"] for e in entries for a in e["aliases"]}
    assert by_alias["au3"] == "Advanced 3" and by_alias["sbu4"] == "Basic 4"
    # A stage is never an item: no stage entry is named like one of the 24 upgrade items.
    assert not {e["name"] for e in entries} & {"Advanced Upgrade 3", "Basic Upgrade 4"}


def test_the_template_fills_item_and_stage_and_fits_the_cap(real: norms.Norms) -> None:
    templates = REPLY_TEMPLATES["price_by_stage"]
    # What matchVocab hands the template for "iron at au3": item and stage, never `au3` as item.
    payload = _ask(real, "iron", stage="au3")
    both = render_reply(templates, payload, {"item": "Iron Bar", "stage": "Advanced 3"})
    assert both is not None
    assert both == payload["reply"] and both.startswith("Iron Bar at Advanced 3 (AU3): 0.97")
    assert len(both) <= MAX_REPLY_CHARS
    # With only the item the same payload still renders: the stage template needs both args.
    assert render_reply(templates, payload, {"item": "Iron Bar"}) == payload["reply"]


def test_the_item_vocabulary_carries_shorthand_but_not_a_ladder_stage(real: norms.Norms) -> None:
    entries = real.vocabulary(_recipe_items())
    by_alias = {a: e["name"] for e in entries for a in e["aliases"]}
    # A bare token is the item (S01-S03), so "au3" fills item. "iron at au3" is split before this.
    assert by_alias["au3"] == "Advanced Upgrade 3"
    assert by_alias["sbu 4"] == "Scholars Basic Upgrade 4"
    assert by_alias["mining bu5"] == "Mining Basic Upgrade"
    assert "Advanced 3" not in {e["name"] for e in entries}


@pytest.mark.parametrize("word", ["mu5", "au5", "bu5"])
def test_a_bare_5_lists_every_module_of_its_tier_beyond_the_five_name_cap(
    real: norms.Norms, word: str
) -> None:
    out = _ask(real, word)
    tier = upgrade_words.TIERS[word[0]]
    modules = {f"{m} Upgrade" for m in upgrade_words.SPECIALISTS[tier]}
    assert out["resolved"] is None and len(out["candidates"]) > 5
    assert set(out["candidates"]) <= modules and len(out["reply"]) <= MAX_REPLY_CHARS


@pytest.mark.parametrize("word", ["sbu5", "smu5", "sau5", "SMU0", "sau 0"])
def test_a_scholars_5_or_0_is_a_miss_with_its_meaning(real: norms.Norms, word: str) -> None:
    out = _ask(real, word)
    assert out["resolved"] is None and out["candidates"] == [] and out["reply"].endswith(".")
    assert out["reply"] == upgrade_words.meaning(word)
