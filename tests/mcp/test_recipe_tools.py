"""The recipe / cost plane finally has an MCP surface (eco-app#242).

Eco Gnome was the one substantial dataset in eco-app answering only a browser.
An agent asking "what does a Steel Axe cost to make, and is that above or below
market?" could not get there, even though the app computes exactly that for the
SPA.

Response size is designed in rather than retrofitted — the graph is ~1,450
recipes, which is the eco-app#240 family-3 trap.
"""

from __future__ import annotations

import json
from typing import Any

import mcp.types as mt
import pytest
from starlette.testclient import TestClient

from eco_mcp_app.http_app import create_app
from eco_mcp_app.recipes import load_recipe_index
from eco_mcp_app.wave3_routes import DEFAULT_RECIPE_LIMIT


async def _call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    from eco_mcp_app.server import build_server

    mcp = build_server()
    handler = mcp.request_handlers[mt.CallToolRequest]
    result = await handler(
        mt.CallToolRequest(
            method="tools/call",
            params=mt.CallToolRequestParams(name=name, arguments=arguments),
        )
    )
    for block in result.root.content:
        try:
            parsed = json.loads(getattr(block, "text", "") or "")
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise AssertionError(f"{name} returned no JSON block")


@pytest.mark.asyncio
async def test_get_recipes_is_summary_first() -> None:
    """Unfiltered, the graph is far past an MCP response cap."""
    payload = await _call("get_recipes", {})
    assert payload["recipesReturned"] <= DEFAULT_RECIPE_LIMIT
    assert payload["recipesMatched"] > DEFAULT_RECIPE_LIMIT
    # The truncation announces itself rather than looking like the whole graph.
    assert any("showing" in w for w in payload["warnings"])


@pytest.mark.asyncio
async def test_a_filter_narrows_to_a_real_answer() -> None:
    index = load_recipe_index()
    skill = next(r.skill_name for r in index.recipes if r.skill_name)
    payload = await _call("get_recipes", {"skill": skill, "limit": 5})
    assert payload["recipesReturned"] <= 5
    assert payload["recipesMatched"] >= 1


@pytest.mark.asyncio
async def test_limit_zero_returns_everything() -> None:
    """The SPA's contract: it renders the whole graph."""
    payload = await _call("get_recipes", {"limit": 0})
    assert payload["recipesReturned"] == payload["recipesMatched"]


@pytest.mark.asyncio
async def test_the_payload_names_its_provenance() -> None:
    """A consumer must be able to tell vanilla-seed from a modded export (#179)."""
    payload = await _call("get_recipes", {"limit": 1})
    assert payload["source"]
    assert payload["sourceKind"]
    assert "serverSpecific" in payload


@pytest.mark.asyncio
async def test_a_filtered_call_does_not_ship_the_whole_graph_index() -> None:
    """The filters narrowed `recipes` but the index maps came back whole (#254).

    A one-recipe answer arrived wrapped in ~189 KB of graph index, putting the
    signal at 0.34% of the payload and blowing the MCP response cap.
    """
    index = load_recipe_index()
    product = index.recipes[0].product.item
    payload = await _call("get_recipes", {"product": product})

    assert payload["recipesMatched"] >= 1
    # Every index map is restricted to the recipes actually returned.
    returned = {r["name"] for r in payload["recipes"]}
    for key in ("byProduct", "bySkill", "byStation"):
        for names in payload[key].values():
            assert set(names) <= returned, key
    assert payload["indexScope"] == "filtered"
    # And the whole thing now fits comfortably inside a client response cap.
    assert len(json.dumps(payload)) < 25_000


@pytest.mark.asyncio
async def test_a_truncated_call_narrows_its_index_too() -> None:
    """A 5-recipe slice must not carry the 1,450-recipe index."""
    payload = await _call("get_recipes", {"limit": 5})
    returned = {r["name"] for r in payload["recipes"]}
    assert payload["recipesMatched"] > 5
    for names in payload["byProduct"].values():
        assert set(names) <= returned


@pytest.mark.asyncio
async def test_price_recipe_returns_costs_not_a_recipe_graph() -> None:
    """price_recipe has one job; the graph schema was 99% of its response (#254)."""
    index = load_recipe_index()
    product = index.recipes[0].product.item
    payload = await _call("price_recipe", {"product": product})

    for graph_key in ("byProduct", "bySkill", "byStation", "tags", "skills"):
        assert graph_key not in payload, graph_key
    assert payload["indexScope"] == "omitted"
    assert payload["recipes"]
    assert len(json.dumps(payload, default=str)) < 25_000


@pytest.mark.asyncio
async def test_skill_display_name_matches_the_same_recipes_as_the_id() -> None:
    """The tool description's own example must not match zero rows (#255)."""
    by_id = await _call("get_recipes", {"skill": "SmeltingSkill", "limit": 0})
    by_display = await _call("get_recipes", {"skill": "Smelting", "limit": 0})
    assert by_id["recipesMatched"] > 0
    assert by_display["recipesMatched"] == by_id["recipesMatched"]


@pytest.mark.asyncio
async def test_an_unknown_skill_filter_says_so() -> None:
    """A silent empty result cannot be told from "this skill gates no recipes"."""
    payload = await _call("get_recipes", {"skill": "NotARealSkill", "limit": 0})
    assert payload["recipesMatched"] == 0
    assert any("no skill named" in w for w in payload["warnings"])


@pytest.mark.asyncio
async def test_price_recipe_costs_a_product() -> None:
    index = load_recipe_index()
    product = index.recipes[0].product.item
    payload = await _call("price_recipe", {"product": product})
    rows = payload["recipes"]
    assert rows, "no recipe matched a product taken from the index itself"
    # The cost engine ran: every row carries a breakdown, priced or not.
    assert all("cost" in row for row in rows)


def test_the_spa_recipe_route_keeps_its_contract() -> None:
    """`/preview/recipes.json` moved onto the registry but kept its params."""
    client = TestClient(create_app())
    r = client.get("/preview/recipes.json", params={"limit": 0})
    assert r.status_code == 200
    payload = r.json()
    # The shape the recipes page reads.
    for key in ("recipes", "byProduct", "bySkill", "byStation", "skills", "tags", "source"):
        assert key in payload, key

    # And the filters still filter.
    filtered = client.get(
        "/preview/recipes.json", params={"skill": "__no_such_skill__", "limit": 0}
    )
    assert filtered.status_code == 200
    assert filtered.json()["recipes"] == []
