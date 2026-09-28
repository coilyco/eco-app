"""Argument vocabularies a client can match a player's words against.

A client that calls a tool without a model (Sirens Echo's direct tool reply,
sirens-echo#8249) has to turn "where can I buy limestone" into `item=...`
deterministically. These resources publish the closed sets to match against,
and `_meta["coilyco/args"]` on each tool says which set feeds which argument.
Contract: docs/dual-route-inventory.md#reply-templates.
"""

from __future__ import annotations

from typing import Any

from .crafting import prettify_eco_name
from .recipes import RecipeIndex

ITEMS_URI = "eco://vocab/items"
CURRENCIES_URI = "eco://vocab/currencies"
ARGS_META_KEY = "coilyco/args"

# Which vocabulary and which entry field each templated argument takes. The
# field is what the tool's own handler accepts (see the contract doc).
# Shop words are items too (Store), and in a trade question they mean the shop,
# so the trade tools never take them as the item (sirens-echo#8249).
SHOP_WORDS = ["store", "shop", "market"]
TOOL_ARGS: dict[str, dict[str, dict[str, Any]]] = {
    "find_trade": {"item": {"vocabulary": ITEMS_URI, "field": "name", "ignore": SHOP_WORDS}},
    # get_market folds spaces out only on the id side, so a multi-word display
    # name would never match: it takes the id.
    "get_market": {"item": {"vocabulary": ITEMS_URI, "field": "id", "ignore": SHOP_WORDS}},
    "price_recipe": {"product": {"vocabulary": ITEMS_URI, "field": "id"}},
    "get_currency": {"currency": {"vocabulary": CURRENCIES_URI, "field": "name"}},
    "price_by_stage": {"item": {"vocabulary": ITEMS_URI, "field": "name", "ignore": SHOP_WORDS}},
}


def item_vocabulary(index: RecipeIndex) -> list[dict[str, Any]]:
    """Every item the recipe graph names as a product or ingredient, tags excluded."""
    ids: set[str] = set(index.by_product)
    for recipe in index.recipes:
        for component in (recipe.product, *recipe.ingredients, *recipe.byproducts):
            if component.item and not component.is_tag:
                ids.add(component.item)
    entries = []
    for item_id in sorted(ids):
        name = prettify_eco_name(item_id)
        stem = item_id[: -len("Item")] if item_id.endswith("Item") and item_id != "Item" else ""
        aliases = [a for a in dict.fromkeys((item_id, stem)) if a and a != name]
        entries.append({"id": item_id, "name": name, "aliases": aliases})
    return entries


def currency_vocabulary(records: Any) -> list[dict[str, Any]]:
    """Every named currency on the roster. A record known only by id is skipped."""
    entries: list[dict[str, Any]] = []
    for record in records:
        name = (getattr(record, "name", "") or "").strip()
        if not name or getattr(record, "unresolved_id", False):
            continue
        entries.append({"id": name, "name": name, "aliases": []})
    return sorted(entries, key=lambda e: e["id"].lower())
