"""Argument vocabularies for model-free callers (sirens-echo#8249)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import mcp.types as mt
import pytest
from pydantic import AnyUrl

from eco_mcp_app import recipes as recipes_mod
from eco_mcp_app import server as server_mod
from eco_mcp_app.recipes import Recipe, RecipeComponent, RecipeIndex
from eco_mcp_app.reply_templates import TEMPLATES_META_KEY
from eco_mcp_app.server import build_server
from eco_mcp_app.vocab import (
    ARGS_META_KEY,
    CURRENCIES_URI,
    ITEMS_URI,
    PRICED_ITEMS_URI,
    STAGES_URI,
    TOOL_ARGS,
    currency_vocabulary,
    item_vocabulary,
)


def _fixture_index() -> RecipeIndex:
    axe = Recipe(
        name="SteelAxe",
        display_name="Steel Axe",
        product=RecipeComponent(item="SteelAxeItem", quantity=1),
        ingredients=[
            RecipeComponent(item="SteelBarItem", quantity=4),
            RecipeComponent(item="Wood", quantity=2, is_tag=True),
        ],
    )
    return RecipeIndex(
        fetched_at_iso="2026-09-25T00:00:00Z",
        source="fixture",
        recipes=[axe],
        by_product={"SteelAxeItem": ["SteelAxe"]},
        tags={"Wood": ["BirchLogItem"]},
    )


async def _read(uri: str) -> dict[str, Any]:
    mcp = build_server()
    handler = mcp.request_handlers[mt.ReadResourceRequest]
    request = mt.ReadResourceRequest(
        method="resources/read", params=mt.ReadResourceRequestParams(uri=AnyUrl(uri))
    )
    result = await handler(request)
    contents = result.root.contents
    assert len(contents) == 1 and contents[0].mimeType == "application/json"
    return json.loads(contents[0].text)


@pytest.mark.asyncio
async def test_resources_list_every_vocabulary_without_annotations() -> None:
    mcp = build_server()
    handler = mcp.request_handlers[mt.ListResourcesRequest]
    result = await handler(mt.ListResourcesRequest(method="resources/list"))
    by_uri = {str(r.uri): r for r in result.root.resources}
    assert set(by_uri) == {ITEMS_URI, CURRENCIES_URI, PRICED_ITEMS_URI, STAGES_URI}
    for resource in by_uri.values():
        assert resource.mimeType == "application/json"
        # No assistant audience, so no client pulls a vocabulary into a prompt.
        assert resource.annotations is None


def test_item_vocabulary_covers_products_and_ingredients_but_not_tags() -> None:
    entries = {e["id"]: e for e in item_vocabulary(_fixture_index())}
    assert set(entries) == {"SteelAxeItem", "SteelBarItem"}
    assert entries["SteelAxeItem"] == {
        "id": "SteelAxeItem",
        "name": "Steel Axe",
        "aliases": ["SteelAxeItem", "SteelAxe"],
    }


@pytest.mark.asyncio
async def test_read_items_serves_the_loaded_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(recipes_mod, "load_recipe_index", _fixture_index)
    body = await _read(ITEMS_URI)
    assert [e["name"] for e in body["entries"]] == ["Steel Axe", "Steel Bar"]


@pytest.mark.asyncio
async def test_read_currencies_degrades_to_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    async def unreachable(server: str | None = None) -> dict[str, Any]:
        raise OSError("no route to the Eco server")

    monkeypatch.setattr(server_mod, "fetch_eco_info", unreachable)
    assert await _read(CURRENCIES_URI) == {"entries": []}


def test_currency_vocabulary_skips_records_known_only_by_id() -> None:
    records = [
        SimpleNamespace(name="Spectres", unresolved_id=False),
        SimpleNamespace(name="7f3a", unresolved_id=True),
        SimpleNamespace(name="  ", unresolved_id=False),
    ]
    assert currency_vocabulary(records) == [{"id": "Spectres", "name": "Spectres", "aliases": []}]


@pytest.mark.asyncio
async def test_four_tools_carry_args_beside_their_templates() -> None:
    mcp = build_server()
    handler = mcp.request_handlers[mt.ListToolsRequest]
    result = await handler(mt.ListToolsRequest(method="tools/list"))
    tools = {t.name: t for t in result.root.tools}
    for name, args in TOOL_ARGS.items():
        meta = tools[name].meta or {}
        assert meta[ARGS_META_KEY] == args
        assert TEMPLATES_META_KEY in meta
