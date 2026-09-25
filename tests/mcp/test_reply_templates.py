"""Reply templates: the reference renderer and every shipped template.

Each shipped template renders against a payload from the same builder that
produces the tool's structuredContent, so a renamed field fails here.
"""

from __future__ import annotations

from typing import Any

import pytest
from mcp.types import Tool

from eco_mcp_app.cost import CostParams, annotate_payload
from eco_mcp_app.currency import (
    CurrencyHolder,
    CurrencyRecord,
    CurrencySnapshot,
    compute_currency_payload,
)
from eco_mcp_app.logistics import ShelfOffer, build_logistics
from eco_mcp_app.market import MarketIntelligence, build_market
from eco_mcp_app.recipes import filter_index, load_recipe_index
from eco_mcp_app.reply_templates import (
    MAX_REPLY_CHARS,
    REPLY_TEMPLATES,
    TEMPLATES_META_KEY,
    render_reply,
    with_reply_templates,
)
from eco_mcp_app.server import to_payload
from eco_mcp_app.vocab import ARGS_META_KEY, TOOL_ARGS


def _t(text: str, when_args: list[str] | None = None) -> dict[str, Any]:
    return {"when_args": when_args or [], "text": text}


# --- the contract ------------------------------------------------------------


def test_placeholder_paths_index_lists_and_read_args() -> None:
    payload = {"rows": [{"name": "Limestone", "price": 3}]}
    text = render_reply(
        [_t("{{rows.0.name}} for {{rows.0.price}} ({{args.item}})")], payload, {"item": "lime"}
    )
    assert text == "Limestone for 3 (lime)"


@pytest.mark.parametrize(
    "value",
    [None, "", "   ", [], {}, True, {"a": 1}, [1]],
)
def test_non_scalar_or_empty_values_make_the_template_ineligible(value: Any) -> None:
    assert render_reply([_t("x {{v}}")], {"v": value}, {}) is None


def test_missing_path_and_out_of_range_index_are_ineligible() -> None:
    assert render_reply([_t("{{a.b}}")], {"a": {}}, {}) is None
    assert render_reply([_t("{{rows.1}}")], {"rows": ["only"]}, {}) is None
    assert render_reply([_t("{{rows.x}}")], {"rows": ["only"]}, {}) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (3, "3"),
        (0, "0"),
        (2.5, "2.5"),
        (2.456, "2.46"),
        (2.0, "2"),
        (-0.001, "0"),
        (1234.5, "1234.5"),
    ],
)
def test_numbers_render_with_at_most_two_decimals(value: float, expected: str) -> None:
    assert render_reply([_t("{{v}}")], {"v": value}, {}) == expected


def test_first_eligible_template_wins_and_when_args_gates() -> None:
    templates = [_t("item {{args.item}}", ["item"]), _t("fallback {{v}}")]
    assert render_reply(templates, {"v": 1}, {"item": "Clay"}) == "item Clay"
    assert render_reply(templates, {"v": 1}, {}) == "fallback 1"
    assert render_reply(templates, {"v": 1}, {"item": " "}) == "fallback 1"


def test_a_reply_over_the_cap_is_ineligible() -> None:
    long = "x" * MAX_REPLY_CHARS
    assert render_reply([_t("{{v}}")], {"v": long}, {}) == long
    assert render_reply([_t("{{v}}!")], {"v": long}, {}) is None


def test_attaching_templates_keeps_other_meta_and_leaves_descriptions_alone() -> None:
    tools = [
        Tool(name="find_trade", description="d", inputSchema={"type": "object"}, _meta={"ui": 1}),
        Tool(name="get_world", description="w", inputSchema={"type": "object"}),
    ]
    attached = with_reply_templates(tools)
    assert attached[0].meta == {
        "ui": 1,
        TEMPLATES_META_KEY: REPLY_TEMPLATES["find_trade"],
        ARGS_META_KEY: TOOL_ARGS["find_trade"],
    }
    assert attached[0].description == "d"
    assert attached[1].meta is None


# --- every shipped template against its real payload ------------------------


def _offer(store: str, price: float, owner: str) -> ShelfOffer:
    return ShelfOffer(
        store_key=store,
        store_label=store,
        owner=owner,
        item="LimestoneItem",
        item_display="Limestone",
        currency="Credits",
        side="sell",
        price=price,
        quantity=40.0,
        source="live",
    )


def test_find_trade_names_the_cheapest_seller() -> None:
    report = build_logistics(
        [_offer("Shiny Rocks", 3.0, "Snorf"), _offer("Quarry", 4.5, "Ada")], item="limestone"
    )
    text = render_reply(REPLY_TEMPLATES["find_trade"], report.to_dict(), {"item": "limestone"})
    assert text == "The cheapest Limestone is 3 Credits at Shiny Rocks, owned by Snorf."


def test_find_trade_with_no_offers_falls_back() -> None:
    report = build_logistics([], item="limestone")
    assert (
        render_reply(REPLY_TEMPLATES["find_trade"], report.to_dict(), {"item": "limestone"}) is None
    )


def test_get_market_reports_latest_median_and_trend() -> None:
    rows = [
        {
            "item": "CopperPlateItem",
            "currency": "Credits",
            "day": d,
            "unitPrice": p,
            "quantity": 5.0,
        }
        for d, p in [(1.0, 2.0), (2.0, 2.0), (6.0, 3.0), (7.0, 3.2)]
    ]
    intel = MarketIntelligence(fetched_at_iso="", source_base_url="", markets=build_market(rows))
    text = render_reply(REPLY_TEMPLATES["get_market"], intel.to_dict(), {"item": "copper plate"})
    assert text is not None
    assert text.startswith(
        "Copper Plate last traded at a median of 3.2 Credits on day 7. Price trend: rising."
    )


def test_price_recipe_needs_a_complete_cost() -> None:
    index = load_recipe_index()
    payload = filter_index(index, product="HewnLog")
    annotate_payload(payload, index, {}, CostParams())
    assert render_reply(REPLY_TEMPLATES["price_recipe"], payload, {"product": "HewnLog"}) is None

    items: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "item" and isinstance(value, str):
                    items.add(value)
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(filter_index(index))
    payload = filter_index(index, product="HewnLog")
    annotate_payload(payload, index, dict.fromkeys(items, 1.0), CostParams())
    text = render_reply(REPLY_TEMPLATES["price_recipe"], payload, {"product": "HewnLog"})
    assert text is not None
    assert text.startswith("Making one Hewn Log costs about ")


def _currency_payload(holder: str | None) -> dict[str, Any]:
    record = CurrencyRecord(
        "Spectres",
        is_minted=True,
        holders_reachable=True,
        accounts_counted=3,
        total_holdings=150.0,
        top_holders=[CurrencyHolder(account="Treasury", holder=holder, balance=120.5)],
    )
    snapshot = CurrencySnapshot(
        fetched_at_iso="",
        source_base_url="http://example.invalid",
        info={"Description": "Sirens"},
        days_elapsed=4,
        admin_ok=True,
    )
    snapshot.currencies[record.name] = record
    return compute_currency_payload(snapshot, currency="spectres")


def test_get_currency_names_the_top_holder_or_its_account() -> None:
    templates = REPLY_TEMPLATES["get_currency"]
    assert (
        render_reply(templates, _currency_payload("Snorf"), {"currency": "spectres"})
        == "The top holder of Spectres is Snorf with 120.5."
    )
    assert (
        render_reply(templates, _currency_payload(None), {"currency": "spectres"})
        == "The top holder of Spectres is the account Treasury with 120.5."
    )


def test_get_server_status_mentions_the_meteor_only_when_one_is_coming() -> None:
    info = {
        "OnlinePlayers": 7,
        "DaysRunning": 12,
        "Version": "0.12.0.6",
        "HasMeteor": True,
        "DaysUntilMeteor": 18,
    }
    templates = REPLY_TEMPLATES["get_server_status"]
    assert (
        render_reply(templates, to_payload(info), {})
        == "7 players online on day 12. The meteor hits in 18 days. Server version 0.12.0.6."
    )
    no_meteor = {**info, "HasMeteor": False}
    assert (
        render_reply(templates, to_payload(no_meteor), {})
        == "7 players online on day 12. Server version 0.12.0.6."
    )
