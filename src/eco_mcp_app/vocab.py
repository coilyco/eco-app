"""Argument vocabularies a client can match a player's words against.

A client that calls a tool without a model (Sirens Echo's direct tool reply,
sirens-echo#8249) has to turn "where can I buy limestone" into `item=...`
deterministically. These resources publish the closed sets to match against,
and `_meta["coilyco/args"]` on each tool says which set feeds which argument.
Contract: docs/dual-route-inventory.md#reply-templates.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .crafting import prettify_eco_name
from .recipes import RecipeIndex

ITEMS_URI = "eco://vocab/items"
CURRENCIES_URI = "eco://vocab/currencies"
PRICED_ITEMS_URI = "eco://vocab/priced-items"
STORES_URI = "eco://vocab/stores"
STAGES_URI = "eco://vocab/stages"
ARGS_META_KEY = "coilyco/args"

# Which vocabulary and which entry field each templated argument takes. The
# field is what the tool's own handler accepts (see the contract doc).
# Shop words are items too (Store), and in a trade question they mean the shop,
# so the trade tools never take them as the item (sirens-echo#8249).
SHOP_WORDS = ["store", "shop", "market"]
TOOL_ARGS: dict[str, dict[str, dict[str, Any]]] = {
    # A store is named by its own words, which are often item words too ("Food Coming
    # Soon"). The harness spends the words a store name matched before it fills item.
    "find_trade": {
        # `evidence`: a hit is told to the caller's tool picker, since a store is often named
        # without the word "store" ("stock check food coming soon"), and the picker reads that
        # as a general question (COI-3279).
        "store": {
            "vocabulary": STORES_URI,
            "field": "name",
            "ignore": SHOP_WORDS,
            "evidence": True,
        },
        "item": {"vocabulary": ITEMS_URI, "field": "name", "ignore": SHOP_WORDS},
    },
    # The store's given name, as in find_trade: the owner question ("who owns <store>").
    "get_stores": {"store": {"vocabulary": STORES_URI, "field": "name", "ignore": SHOP_WORDS}},
    # get_market folds spaces out only on the id side, so a multi-word display
    # name would never match: it takes the id.
    "get_market": {"item": {"vocabulary": ITEMS_URI, "field": "id", "ignore": SHOP_WORDS}},
    "price_recipe": {"product": {"vocabulary": ITEMS_URI, "field": "id"}},
    "get_currency": {"currency": {"vocabulary": CURRENCIES_URI, "field": "name"}},
    # Its own set: the items with trade history, plus "iron" for Iron Bar, exactly
    # what the tool resolves (teable:coilyco/eco-app#8423).
    "price_by_stage": {
        "item": {"vocabulary": PRICED_ITEMS_URI, "field": "name", "ignore": SHOP_WORDS},
        # "none" is a stage and an everyday word, so only BU0 fills it.
        "stage": {"vocabulary": STAGES_URI, "field": "name", "ignore": ["none"]},
    },
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


def store_vocabulary(stores: Iterable[tuple[str, str]]) -> list[dict[str, Any]]:
    """Store names a player's words can be matched against, from (name, owner) pairs.

    A name alone is one entry however many stores carry it, since the tool's filter
    answers every store of that name. `Owner's Name` is an entry of its own, longer
    and so preferred when a player says it. A one-word name is left out: it would
    match a store whenever the word appears, "food" and "tools" among them.
    """
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for name, owner in sorted(set(stores)):
        name, owner = name.strip(), owner.strip()
        for form in (name, f"{owner}'s {name}" if owner else ""):
            key = form.lower()
            if form and len(form.split()) >= 2 and key not in seen:
                seen.add(key)
                entries.append({"id": form, "name": form, "aliases": []})
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
