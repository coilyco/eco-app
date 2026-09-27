"""Current-cycle price distribution and specialty-marker contract tests."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from eco_mcp_app.http_app import create_app
from eco_mcp_app.price_history import build_item_price_history
from eco_mcp_app.progression import ProgressionHistory
from eco_mcp_app.recipes import RecipeIndex, build_recipe_index


def _row(item: str, currency: str, day: int, price: float) -> dict[str, Any]:
    return {
        "item": item,
        "currency": currency,
        "day": day,
        "unitPrice": price,
        "quantity": 1,
    }


def _recipes() -> RecipeIndex:
    return build_recipe_index(
        {
            "Version": 1,
            "Skills": [
                {
                    "Name": "SmeltingSkill",
                    "LocalizedName": {"en-US": "Smelting"},
                    "MaxLevel": 7,
                },
                {
                    "Name": "AdvancedSmeltingSkill",
                    "LocalizedName": {"en-US": "Advanced Smelting"},
                    "MaxLevel": 7,
                },
            ],
            "Recipes": [
                {
                    "Name": "IronIngotBloomeryRecipe",
                    "LocalizedName": {"en-US": "Iron Ingot"},
                    "FamilyName": "IronIngot",
                    "RequiredSkill": "SmeltingSkill",
                    "RequiredSkillLevel": 2,
                    "Products": [{"ItemOrTag": "IronIngotItem", "Quantity": {"BaseValue": 1}}],
                },
                {
                    "Name": "IronIngotBlastRecipe",
                    "LocalizedName": {"en-US": "Iron Ingot"},
                    "FamilyName": "IronIngot",
                    "RequiredSkill": "AdvancedSmeltingSkill",
                    "RequiredSkillLevel": 4,
                    "Products": [{"ItemOrTag": "IronIngotItem", "Quantity": {"BaseValue": 2}}],
                },
            ],
        },
        source="test",
    )


def _progression(*, gains: bool = True, exporter: bool = True) -> ProgressionHistory:
    return ProgressionHistory(
        fetched_at_iso="2026-08-01T00:00:00+00:00",
        source_base_url="test",
        per_action_counts={"GainSpecialty": 2} if exporter else {},
        first_specialty_gains=(
            [
                {
                    "skill": "SmeltingSkill",
                    "pretty": "Smelting",
                    "day": 2,
                    "time": 172800,
                },
                {
                    "skill": "AdvancedSmeltingSkill",
                    "pretty": "Advanced Smelting",
                    "day": 5,
                    "time": 432000,
                },
            ]
            if gains
            else []
        ),
    )


def test_complete_join_covers_distribution_variants_and_unlock_days() -> None:
    rows = [
        _row("IronIngotItem", "Credit", 1, 8),
        _row("IronIngotItem", "Credit", 2, 9),
        _row("IronIngotItem", "Credit", 3, 10),
        _row("IronIngotItem", "Credit", 4, 11),
        _row("IronIngotItem", "Credit", 5, 12),
        _row("IronIngotItem", "Credit", 6, 13),
        _row("IronIngotItem", "Gold", 6, 1),
    ]
    payload = build_item_price_history("IronIngotItem", "Credit", rows, _recipes(), _progression())

    assert payload["scope"] == {
        "label": "Current cycle only",
        "cycle": "current",
        "progressionRulesVersion": "current-cycle-v1",
        "historicalCyclesIncluded": False,
    }
    assert payload["distribution"]["sampleCount"] == 6
    assert payload["distribution"]["sampleState"] == "representative"
    assert payload["distribution"]["percentiles"]["p50"] == pytest.approx(10.5)
    assert payload["totalVolume"] == pytest.approx(6)
    assert {recipe["name"] for recipe in payload["recipes"]} == {
        "IronIngotBloomeryRecipe",
        "IronIngotBlastRecipe",
    }
    markers = {marker["skill"]: marker for marker in payload["specialtyUnlocks"]}
    assert markers["SmeltingSkill"]["day"] == 2
    assert markers["AdvancedSmeltingSkill"]["day"] == 5
    assert markers["SmeltingSkill"]["recipeVariants"] == ["IronIngotBloomeryRecipe"]


def test_empty_thin_stale_and_multimodal_states_are_explicit() -> None:
    empty = build_item_price_history("IronIngotItem", "Credit", [], _recipes(), _progression())
    assert {"no_data"} <= set(empty["states"])
    assert empty["distribution"]["percentiles"] is None

    thin_rows = [_row("IronIngotItem", "Credit", 1, 10), _row("OtherItem", "Credit", 8, 2)]
    thin = build_item_price_history(
        "IronIngotItem", "Credit", thin_rows, _recipes(), _progression()
    )
    assert {"thin", "stale"} <= set(thin["states"])

    modes = [10, 10, 10, 10, 30, 30, 30, 30]
    multimodal = build_item_price_history(
        "IronIngotItem",
        "Credit",
        [_row("IronIngotItem", "Credit", index + 1, price) for index, price in enumerate(modes)],
        _recipes(),
        _progression(),
    )
    assert multimodal["distribution"]["shapeState"] == "multimodal"
    assert "multimodal" in multimodal["states"]


def test_outlier_does_not_become_a_second_mode() -> None:
    prices = [10, 10, 10, 11, 11, 11, 12, 12, 12, 100]
    payload = build_item_price_history(
        "IronIngotItem",
        "Credit",
        [_row("IronIngotItem", "Credit", index + 1, price) for index, price in enumerate(prices)],
        _recipes(),
        _progression(),
    )
    assert payload["distribution"]["max"] == 100
    assert payload["distribution"]["shapeState"] == "observed"
    assert payload["distribution"]["percentiles"]["p90"] < 25


def test_recipe_and_progression_degraded_states_do_not_imply_an_unlock() -> None:
    rows = [_row("IronIngotItem", "Credit", 1, 10)]
    missing_recipes = build_item_price_history(
        "IronIngotItem",
        "Credit",
        rows,
        RecipeIndex(fetched_at_iso="t", source="test"),
        _progression(),
    )
    assert "missing_recipes" in missing_recipes["states"]
    assert missing_recipes["specialtyUnlocks"] == []

    missing_progression = build_item_price_history(
        "IronIngotItem", "Credit", rows, _recipes(), _progression(exporter=False)
    )
    assert "missing_progression" in missing_progression["states"]
    assert {marker["status"] for marker in missing_progression["specialtyUnlocks"]} == {
        "progression_unavailable"
    }

    unobserved = build_item_price_history(
        "IronIngotItem", "Credit", rows, _recipes(), _progression(gains=False)
    )
    assert "unobserved_unlocks" in unobserved["states"]
    assert all(marker["day"] is None for marker in unobserved["specialtyUnlocks"])


def test_preview_route_requires_selection_and_returns_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_fetch(item: str, currency: str, **_: Any) -> dict[str, Any]:
        return {"view": "item-price-history", "item": item, "currency": currency}

    monkeypatch.setattr("eco_mcp_app.http_app.fetch_item_price_history", fake_fetch)
    client = TestClient(create_app())
    missing = client.get("/preview/price-history.json")
    assert missing.status_code == 400

    response = client.get(
        "/preview/price-history.json",
        params={"item": "IronIngotItem", "currency": "Credit"},
    )
    assert response.status_code == 200
    body = response.json()
    # The historical norm rides beside the price (#8368), tested in test_norms.py.
    assert "norm" in body and "normContext" in body
    del body["norm"], body["normContext"]
    assert body == {
        "view": "item-price-history",
        "item": "IronIngotItem",
        "currency": "Credit",
    }
