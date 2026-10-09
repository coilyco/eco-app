"""Tests for the recipe bill-of-materials ingest (eco-app#100).

Covers:
  - build_recipe_index folds an Eco Gnome export into the #98 DTO: product /
    ingredients / byproducts / station / skill / labor / craftMinutes, with tag
    ingredients flagged and family siblings wired as `variants`.
  - Baseline quantities come from `BaseValue`, ignoring the per-player modifier
    stack.
  - A no-skill recipe and a recipe with no products degrade gracefully.
  - load_recipe_index serves the vendored AutoGen graph (1,487 recipes, eco-app#242)
    in preference to the Gnome seed, and memoizes it.
  - filter_index narrows by product / skill / station.
  - The dedicated `/preview/recipes.json` route serves the index and honors
    the filter params.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from eco_mcp_app import recipes as recipes_mod
from eco_mcp_app.http_app import create_app
from eco_mcp_app.recipes import build_recipe_index, filter_index, load_recipe_index

# A minimal export exercising every DTO field: two recipes in one family (so
# `variants` is non-empty), a tag ingredient, a byproduct, and a no-skill recipe.
_RAW = {
    "Version": 3,
    "Skills": [
        {"Name": "MasonSkill", "LocalizedName": {"en-US": "Mason"}, "MaxLevel": 0},
        {
            "Name": "MasonrySkill",
            "LocalizedName": {"en-US": "Masonry"},
            "Profession": "MasonSkill",
            "MaxLevel": 7,
            "Talents": [
                {
                    "Name": "MasonryMortarTalent",
                    "LocalizedName": {"en-US": "Mortar Saver"},
                    "LocalizedDescription": {"en-US": "Reduces mortar use."},
                    "Level": 3,
                    "MaxLevel": 1,
                }
            ],
        },
        {"Name": "CarpentrySkill", "LocalizedName": {"en-US": "Carpentry"}, "MaxLevel": 7},
    ],
    "Items": [
        {"Name": "ShaleItem", "LocalizedName": {"en-US": "Shale"}},
        {"Name": "AshlarShaleItem", "LocalizedName": {"en-US": "Ashlar Shale"}},
    ],
    "Tags": [
        {"Name": "Wood", "LocalizedName": {"en-US": "Wood"}, "AssociatedItems": ["LumberItem"]},
    ],
    "Recipes": [
        {
            "Name": "AshlarShaleRecipe",
            "LocalizedName": {"en-US": "Ashlar Shale"},
            "FamilyName": "Ashlar Shale",
            "CraftMinutes": {"BaseValue": 0.64, "Modifiers": []},
            "RequiredSkill": "MasonrySkill",
            "RequiredSkillLevel": 1,
            "IsBlueprint": False,
            "IsDefault": True,
            "Labor": {"BaseValue": 180.0, "Modifiers": []},
            "CraftingTable": "MasonryTableItem",
            "Ingredients": [
                # A modifier stack that would shrink the count — we must ignore it
                # and take BaseValue.
                {"ItemOrTag": "ShaleItem", "Quantity": {"BaseValue": 20.0, "Modifiers": [1, 2]}},
                {"ItemOrTag": "Wood", "Quantity": {"BaseValue": 4.0, "Modifiers": []}},
            ],
            "Products": [
                {"ItemOrTag": "AshlarShaleItem", "Quantity": {"BaseValue": 2.0, "Modifiers": []}},
                {"ItemOrTag": "CrushedShaleItem", "Quantity": {"BaseValue": 2.0, "Modifiers": []}},
            ],
        },
        {
            "Name": "AshlarShaleKilnRecipe",
            "LocalizedName": {"en-US": "Ashlar Shale"},
            "FamilyName": "Ashlar Shale",
            "CraftMinutes": {"BaseValue": 1.0, "Modifiers": []},
            "RequiredSkill": "CarpentrySkill",
            "RequiredSkillLevel": 3,
            "Labor": {"BaseValue": 90.0, "Modifiers": []},
            "CraftingTable": "KilnItem",
            "Ingredients": [
                {"ItemOrTag": "ShaleItem", "Quantity": {"BaseValue": 10.0, "Modifiers": []}},
            ],
            "Products": [
                {"ItemOrTag": "AshlarShaleItem", "Quantity": {"BaseValue": 1.0, "Modifiers": []}},
            ],
        },
        {
            # No required skill — skill should serialize to None.
            "Name": "PlankRecipe",
            "FamilyName": "Plank",
            "CraftMinutes": {"BaseValue": 0.5, "Modifiers": []},
            "RequiredSkill": None,
            "RequiredSkillLevel": 0,
            "Labor": {"BaseValue": 0.0, "Modifiers": []},
            "CraftingTable": "WorkbenchItem",
            "Ingredients": [{"ItemOrTag": "Wood", "Quantity": {"BaseValue": 1.0, "Modifiers": []}}],
            "Products": [
                {"ItemOrTag": "PlankItem", "Quantity": {"BaseValue": 4.0, "Modifiers": []}}
            ],
        },
    ],
}


def _recipe(index: recipes_mod.RecipeIndex, name: str) -> recipes_mod.Recipe:
    return next(r for r in index.recipes if r.name == name)


def test_build_recipe_index_maps_the_dto() -> None:
    index = build_recipe_index(_RAW)
    assert index.version == 3
    assert index.counts()["recipes"] == 3

    r = _recipe(index, "AshlarShaleRecipe")
    assert r.display_name == "Ashlar Shale"
    assert r.product.item == "AshlarShaleItem"
    assert r.product.quantity == 2.0
    # BaseValue, not the modifier-shrunk value.
    shale = next(i for i in r.ingredients if i.item == "ShaleItem")
    assert shale.quantity == 20.0 and shale.is_tag is False
    wood = next(i for i in r.ingredients if i.item == "Wood")
    assert wood.is_tag is True
    # The second product is a byproduct.
    assert [b.item for b in r.byproducts] == ["CrushedShaleItem"]
    assert r.station == "MasonryTableItem"
    assert r.skill_name == "MasonrySkill" and r.skill_level == 1
    assert r.labor_cost == 180.0 and r.craft_minutes == 0.64
    assert r.table_tier_required is None


def test_family_siblings_become_variants() -> None:
    index = build_recipe_index(_RAW)
    r = _recipe(index, "AshlarShaleRecipe")
    assert r.variants == ["AshlarShaleKilnRecipe"]
    # Both recipes produce AshlarShaleItem, so by_product lists both.
    assert set(index.by_product["AshlarShaleItem"]) == {
        "AshlarShaleRecipe",
        "AshlarShaleKilnRecipe",
    }


def test_no_skill_recipe_serializes_skill_none() -> None:
    index = build_recipe_index(_RAW)
    r = _recipe(index, "PlankRecipe")
    assert r.skill_name is None
    assert r.to_dict()["skill"] is None
    assert "PlankRecipe" not in {n for names in index.by_skill.values() for n in names}


def test_recipe_with_no_products_is_skipped_with_warning() -> None:
    raw = {"Version": 1, "Recipes": [{"Name": "Broken", "Products": []}]}
    index = build_recipe_index(raw)
    assert index.recipes == []
    assert any("Broken" in w for w in index.warnings)


def test_tags_map_exposes_associated_items() -> None:
    index = build_recipe_index(_RAW)
    assert index.tags["Wood"] == ["LumberItem"]


def test_skill_definitions_preserve_profession_and_talent_tree() -> None:
    index = build_recipe_index(_RAW)
    masonry = next(skill for skill in index.skills if skill["name"] == "MasonrySkill")
    assert masonry["profession"] == "MasonSkill"
    assert masonry["talents"] == [
        {
            "name": "MasonryMortarTalent",
            "displayName": "Mortar Saver",
            "description": "Reduces mortar use.",
            "level": 3,
            "maxLevel": 1,
        }
    ]


def test_to_dict_is_json_shaped() -> None:
    payload = build_recipe_index(_RAW).to_dict()
    assert set(payload) >= {
        "fetchedAtISO",
        "source",
        "version",
        "counts",
        "recipes",
        "byProduct",
        "bySkill",
        "byStation",
        "skills",
        "tags",
        "warnings",
    }
    row = next(r for r in payload["recipes"] if r["name"] == "AshlarShaleRecipe")
    assert row["skill"] == {"name": "MasonrySkill", "level": 1}
    assert row["stationDisplayName"] == "Masonry Table"


def test_filter_index_narrows_by_facet() -> None:
    index = build_recipe_index(_RAW)
    by_skill = filter_index(index, skill="MasonrySkill")
    assert {r["name"] for r in by_skill["recipes"]} == {"AshlarShaleRecipe"}
    by_station = filter_index(index, station="KilnItem")
    assert {r["name"] for r in by_station["recipes"]} == {"AshlarShaleKilnRecipe"}
    by_product = filter_index(index, product="AshlarShaleItem")
    assert {r["name"] for r in by_product["recipes"]} == {
        "AshlarShaleRecipe",
        "AshlarShaleKilnRecipe",
    }
    # A filtered payload restricts its lookup maps to the surviving recipes.
    # Shipping the whole graph beside a one-recipe answer was 99% of the
    # response and blew the MCP cap on every filtered call (#254).
    assert "PlankItem" not in by_skill["byProduct"]
    assert by_skill["byProduct"] == {"AshlarShaleItem": ["AshlarShaleRecipe"]}
    assert by_skill["indexScope"] == "filtered"


def test_filter_index_can_keep_the_whole_graph_for_facet_pickers() -> None:
    """The recipes page needs every facet value, not just the matching ones."""
    index = build_recipe_index(_RAW)
    by_skill = filter_index(index, skill="MasonrySkill", narrow_maps=False)
    assert {r["name"] for r in by_skill["recipes"]} == {"AshlarShaleRecipe"}
    assert "PlankItem" in by_skill["byProduct"]


def test_unfiltered_index_keeps_the_whole_graph() -> None:
    index = build_recipe_index(_RAW)
    payload = filter_index(index)
    assert "PlankItem" in payload["byProduct"]
    assert "indexScope" not in payload


def test_filter_accepts_display_names_and_ids_interchangeably() -> None:
    """A recipe's skill displayName "Masonry" must filter too (#255)."""
    index = build_recipe_index(_RAW)
    by_id = filter_index(index, skill="MasonrySkill")
    by_display = filter_index(index, skill="Masonry")
    assert {r["name"] for r in by_display["recipes"]} == {r["name"] for r in by_id["recipes"]}
    assert any("matched 'Masonry' to 'MasonrySkill'" in w for w in by_display["warnings"])


def test_unknown_filter_value_warns_instead_of_matching_nothing_silently() -> None:
    """A silent empty result cannot be told from "this skill gates no recipes"."""
    index = build_recipe_index(_RAW)
    payload = filter_index(index, skill="NotARealSkill")
    assert payload["recipes"] == []
    assert any("no skill named 'NotARealSkill' exists" in w for w in payload["warnings"])


def test_unknown_filter_value_offers_near_misses() -> None:
    index = build_recipe_index(_RAW)
    payload = filter_index(index, skill="Masonrey")
    assert payload["recipes"] == []
    assert any("Did you mean" in w and "MasonrySkill" in w for w in payload["warnings"])


# ---------- the real vendored graph ----------


@pytest.fixture(autouse=True)
def _clear_recipe_cache() -> None:
    recipes_mod._clear_cache()


def test_load_recipe_index_serves_the_autogen_graph() -> None:
    """The loader prefers the AutoGen-derived graph over the Gnome seed (#242).

    Counts are asserted exactly so an Eco version bump shows up as a failing test
    rather than as a silently changed answer — the vendored file is regenerated
    by `just autogen-refresh`, and this is what says the regeneration ran.
    """
    index = load_recipe_index()
    # Parsed from Steam build 24618181 (Eco 0.13.0).
    assert index.counts()["recipes"] == 1487
    assert index.counts()["skills"] == 44
    assert index.counts()["tags"] == 112
    assert index.source.startswith("Eco dedicated server AutoGen")
    assert not index.warnings
    # Every recipe has a product and a station; the graph is well-formed.
    assert all(r.product.item for r in index.recipes)
    assert all(r.station for r in index.recipes)
    # The fields the Gnome seed could not supply are populated for every recipe.
    assert all(r.craft_minutes > 0 for r in index.recipes)
    assert all(r.labor_cost > 0 for r in index.recipes)
    # Tag ingredients are only useful if the tag is resolvable to its members.
    assert all(
        component.item in index.tags
        for recipe in index.recipes
        for component in recipe.ingredients
        if component.is_tag
    )


def test_load_recipe_index_is_memoized() -> None:
    assert load_recipe_index() is load_recipe_index()


def test_corrupt_autogen_bundle_falls_back_to_the_gnome_seed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broken AutoGen file must degrade to the seed, and say that it did.

    The whole point of keeping the Gnome graph is that an Eco layout change which
    breaks the parse still serves recipes. Silently serving the older graph would
    be worse than failing, so the fallback is announced in `warnings`.
    """
    corrupt = tmp_path / recipes_mod._AUTOGEN_DATA_FILENAME
    corrupt.write_bytes(b"this is not gzip")
    real_resolver = recipes_mod._bundled_data_path

    def fake_resolver(filename: str = recipes_mod._DATA_FILENAME) -> Path | None:
        if filename == recipes_mod._AUTOGEN_DATA_FILENAME:
            return corrupt
        return real_resolver(filename)

    monkeypatch.setattr(recipes_mod, "_bundled_data_path", fake_resolver)
    recipes_mod._clear_cache()

    index = load_recipe_index()
    assert index.counts()["recipes"] == 1453
    assert index.source.startswith("eco-gnome-website")
    assert any("fell back to the Eco Gnome seed" in w for w in index.warnings)


def test_preview_recipes_route_serves_and_filters() -> None:
    client = TestClient(create_app())

    r = client.get("/preview/recipes.json")
    assert r.status_code == 200
    payload = r.json()
    assert payload["counts"]["recipes"] == 1487
    assert payload["source"].startswith("Eco dedicated server AutoGen")

    # A skill filter narrows the recipes but keeps the lookup maps.
    some_skill = payload["recipes"][0]["skill"]
    if some_skill:
        r2 = client.get("/preview/recipes.json", params={"skill": some_skill["name"]})
        narrowed = r2.json()
        assert narrowed["counts"]["recipes"] <= payload["counts"]["recipes"]
        assert all(
            (row["skill"] or {}).get("name") == some_skill["name"] for row in narrowed["recipes"]
        )
