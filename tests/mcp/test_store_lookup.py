"""A named-store stock question, one test per way it went wrong (COI-3279).

Fixture shelves only, nothing live. The four modes are the ones members hit:
a store asked for by name answered as a server-wide search, a store asked for
with a swapped word or a typo getting no match, a long shelf's count read as the
store's total, and a listing denied because the board never showed it.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import mcp.types as mt
import pytest
import respx
from pydantic import AnyUrl

from eco_mcp_app.logistics import (
    build_logistics,
    fetch_store_names,
    logistics_markdown,
    parse_live_stores,
)
from eco_mcp_app.reply_templates import REPLY_TEMPLATES
from eco_mcp_app.server import build_server
from eco_mcp_app.vocab import STORES_URI, TOOL_ARGS, store_vocabulary

from .test_logistics import (
    _BARTER_EMPTY,
    _CITIZENS_JSON,
    _CURRENCY_CSV,
    BARTER_URL,
    CITIZENS_URL,
    CURRENCY_URL,
    STORES_URL,
)


def _line(item: str, price: float, quantity: float, *, buying: bool = False) -> dict[str, Any]:
    return {
        "item": item,
        "itemTypeName": item.replace(" ", "") + "Item" if item else "",
        "buying": buying,
        "price": price,
        "quantity": quantity,
    }


def _store(name: str, owner: str, offers: list[dict[str, Any]]) -> dict[str, Any]:
    return {"name": name, "owner": owner, "currency": "Embers", "location": None, "offers": offers}


def _big_shelf() -> dict[str, Any]:
    """265 listings: 150 sold, 50 bought, 60 free, 5 with no item name."""
    offers = [_line(f"Dish {n:03d}", 0.5, 10 + n) for n in range(150)]
    offers += [_line(f"Wanted {n:03d}", 1.0, 5, buying=True) for n in range(50)]
    offers += [_line(f"Gift {n:03d}", 0.0, 3) for n in range(60)]
    offers += [_line("", 2.0, 4) for _ in range(5)]
    return _store("Food Coming Soon", "quillfeather", offers)


def _fixture_stores(*, twin: bool = True) -> list[dict[str, Any]]:
    stores = [
        _big_shelf(),
        _store(
            "Recycle Mania", "Marlow Fenn", [_line("Compost", 0.05, 1447), _line("Trash", 0.0, 9)]
        ),
        _store("Lumberaholics", "redwood", [_line("Lumber", 0.7, 195)]),
        _store("Iron Meats Butchery", "Pip Okoro", [_line("Scrap Meat", 0.05, 216)]),
    ]
    if twin:
        stores.append(_store("Food Coming Soon", "tarnbright", [_line("Meaty Stew", 0.95, 12)]))
    return stores


def _board(query: str, *, twin: bool = True, item: str | None = None):
    unlisted: dict[str, dict[str, int]] = {}
    offers = parse_live_stores(_fixture_stores(twin=twin), unlisted)
    return build_logistics(offers, store=query, item=item, unlisted=unlisted)


# Mode 1: the named store is looked up, and the harness can find the name in the words.


def test_find_trade_publishes_a_store_vocabulary_and_a_store_only_reply() -> None:
    assert TOOL_ARGS["find_trade"]["store"]["vocabulary"] == STORES_URI
    when = [tuple(t["when_args"]) for t in REPLY_TEMPLATES["find_trade"]]
    # Store and item first, so "iron at food coming soon" fills both; store alone next.
    assert when.index(("store", "item")) < when.index(("store",)) < when.index(("item",))


def test_store_vocabulary_names_each_store_once_and_by_owner_and_skips_one_word_names() -> None:
    entries = store_vocabulary(
        [
            ("Food Coming Soon", "quillfeather"),
            ("Food Coming Soon", "tarnbright"),
            ("Food", "someone"),
            ("Store", "Ada"),
        ]
    )
    names = [e["name"] for e in entries]
    assert names.count("Food Coming Soon") == 1
    assert "quillfeather's Food Coming Soon" in names and "tarnbright's Food Coming Soon" in names
    # "Food" alone would match the word anywhere. "Ada's Store" has two words and stays.
    assert "Food" not in names and "Store" not in names and "Ada's Store" in names


@pytest.mark.asyncio
@respx.mock
async def test_the_live_exporter_stores_become_vocabulary_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from eco_mcp_app import server as server_mod
    from eco_mcp_app.trades import _trades_cache

    _trades_cache.clear()
    respx.get(CURRENCY_URL).mock(return_value=httpx.Response(200, text=_CURRENCY_CSV))
    respx.get(BARTER_URL).mock(return_value=httpx.Response(200, text=_BARTER_EMPTY))
    respx.get(CITIZENS_URL).mock(return_value=httpx.Response(200, json=_CITIZENS_JSON))
    respx.get(STORES_URL).mock(return_value=httpx.Response(200, json=_fixture_stores()))
    pairs = await fetch_store_names("eco.example.com:3001", "k")
    assert ("Food Coming Soon", "quillfeather") in pairs
    assert ("Food Coming Soon", "tarnbright") in pairs

    # The resource a client reads is those pairs as vocabulary entries.
    async def served(*_: Any) -> list[tuple[str, str]]:
        return pairs

    monkeypatch.setattr(server_mod, "fetch_store_names", served)
    mcp = build_server()
    listed = await mcp.request_handlers[mt.ListResourcesRequest](
        mt.ListResourcesRequest(method="resources/list")
    )
    assert STORES_URI in {str(r.uri) for r in listed.root.resources}
    read = await mcp.request_handlers[mt.ReadResourceRequest](
        mt.ReadResourceRequest(
            method="resources/read", params=mt.ReadResourceRequestParams(uri=AnyUrl(STORES_URI))
        )
    )
    names = {e["name"] for e in json.loads(read.root.contents[0].text)["entries"]}
    assert {"Food Coming Soon", "tarnbright's Food Coming Soon"} <= names


def test_a_store_only_question_gets_each_named_stores_true_count_in_one_short_line() -> None:
    report = _board("Food Coming Soon")
    assert report.stores_matched == 2
    assert report.reply is not None and len(report.reply) <= 280
    assert "quillfeather's Food Coming Soon: 265 listings, 150 in stock" in report.reply
    assert "tarnbright's Food Coming Soon: 1 listings, 1 in stock" in report.reply


def test_a_summary_that_will_not_fit_is_left_to_the_model_rather_than_cut() -> None:
    shops = [_store("Food Coming Soon", f"owner{n}", [_line("Meaty Stew", 1, 1)]) for n in range(4)]
    report = build_logistics(parse_live_stores(shops), store="Food Coming Soon")
    assert report.stores_matched == 4 and report.reply is None


def test_one_store_and_an_item_still_answers_with_the_item_line() -> None:
    report = _board("tarnbright", item="Meaty Stew")
    assert report.reply is not None and "Meaty Stew" in report.reply


# Mode 2: a swapped word or a typo is the store, or is offered as the likely one.


def test_a_swapped_word_resolves_to_the_one_store_it_is_near() -> None:
    report = _board("More Food Soon", twin=False)
    assert report.stores_matched == 1
    assert report.store_resolved_from == "More Food Soon"
    assert report.stores[0]["store"] == "quillfeather's Food Coming Soon"
    assert any("closest" in w.lower() and "Food Coming Soon" in w for w in report.warnings)


def test_a_one_letter_typo_resolves_to_the_one_store_it_is_near() -> None:
    # One letter doubled, built here so the spelling hook does not flag the fixture.
    typo = "Food Coming Soon".replace("min", "mmin")
    assert typo != "Food Coming Soon"
    report = _board(typo, twin=False)
    assert report.stores_matched == 1 and report.store_resolved_from == typo
    assert report.stores[0]["owner"] == "quillfeather"


def test_two_equally_near_stores_are_offered_not_guessed() -> None:
    report = _board("More Food Soon")
    assert report.stores_matched == 0 and report.stores == []
    assert report.store_resolved_from is None
    offered = {s["store"] for s in report.store_suggestions}
    assert offered == {"quillfeather's Food Coming Soon", "tarnbright's Food Coming Soon"}
    assert any("Closest" in w for w in report.warnings)
    assert "Closest" in logistics_markdown(report)


def test_a_typo_in_an_owner_handle_is_near_too() -> None:
    report = _board("quillfeathor", twin=False)
    assert report.store_resolved_from == "quillfeathor"
    assert report.stores[0]["owner"] == "quillfeather"


@pytest.mark.parametrize("query", ["nobody home", "pizza", "Food Truck Rally"])
def test_a_name_near_nothing_is_still_an_explicit_miss(query: str) -> None:
    report = _board(query)
    assert report.stores_matched == 0 and report.stores == []
    assert report.store_suggestions == [] and report.store_resolved_from is None
    assert any("lookup miss" in w for w in report.warnings)


def test_a_name_that_matches_is_never_replaced_by_a_nearer_one() -> None:
    report = _board("Lumberaholics")
    assert report.store_resolved_from is None and report.stores_matched == 1


# Mode 3: a long shelf reports its true count, or says the list was cut.


def test_a_store_of_265_listings_reports_265_and_says_the_shelf_was_cut() -> None:
    row = _board("quillfeather", twin=False).stores[0]
    assert row["listings"] == 265 and row["shelfLines"] == 200
    assert row["truncated"] is True and len(row["shelf"]) == 60
    assert "shelf shows 60 of 200 lines" in row["note"]
    assert "65 more listing(s) are not on this shelf" in row["note"]


def test_the_markdown_a_model_reads_carries_the_true_count_too() -> None:
    md = logistics_markdown(_board("quillfeather", twin=False))
    assert "265 listing(s) in all" in md and "shelf shows 60 of 200 lines" in md


def test_a_short_shelf_is_not_marked_cut() -> None:
    row = _board("lumberaholics").stores[0]
    assert row["listings"] == row["shelfLines"] == 1 and row["truncated"] is False
    assert row["unlistedLines"] == 0 and "note" not in row


# Mode 4: a listing the board never showed is counted, never denied.


def test_free_and_unnamed_lines_are_counted_so_a_missing_item_is_not_a_denial() -> None:
    row = _board("recycle mania").stores[0]
    assert [o["item"] for o in row["shelf"]] == ["CompostItem"]
    assert row["unlistedLines"] == 1 and row["listings"] == 2
    assert "1 free or barter (price 0)" in row["note"]


def test_unnamed_lines_are_counted_apart_from_free_ones() -> None:
    note = _board("quillfeather", twin=False).stores[0]["note"]
    assert "60 free or barter (price 0), 5 with no item name" in note


def test_the_summary_says_unlisted_lines_exist() -> None:
    reply = _board("recycle mania").reply
    assert reply is not None and "1 not shown (free, barter or unnamed)" in reply
