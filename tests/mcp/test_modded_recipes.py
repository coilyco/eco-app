"""Modded recipe exports, and never passing vanilla off as server-specific.

eco-app#179: pricing evidence built from a vanilla seed is misleading on a
modded server, whose recipe graph, output quantities and upgrades differ. The
app can read a DataExporter-shaped export instead — but the part that matters
is the failure mode. Every refusal falls back to the vanilla seed *and says so*,
because a pricing surface quietly showing vanilla numbers while a modded server
runs is worse than one that admits it does not know.

Production activation waits on eco-ops#71 supplying a verified export. These
tests use fixtures, which is what the issue scopes the app half to.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from eco_mcp_app import recipes as recipes_mod
from eco_mcp_app.recipes import (
    MODDED_EXPORT_ENV,
    SOURCE_KIND_MODDED_EXPORT,
    load_recipe_index,
)


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    recipes_mod._clear_cache()
    monkeypatch.delenv(MODDED_EXPORT_ENV, raising=False)


def _export(**overrides: Any) -> dict[str, Any]:
    """A minimal DataExporter-shaped payload — the same shape the vanilla seed has."""
    payload: dict[str, Any] = {
        "Version": 7,
        "ServerName": "Eco via Sirens",
        "ExportedAt": datetime.now(UTC).isoformat(),
        "Skills": [{"Name": "Smelting", "MaxLevel": 7}],
        "Items": [{"Name": "ModdedAlloyItem", "DisplayName": "Modded Alloy"}],
        "Tags": [],
        "Recipes": [
            {
                "Name": "ModdedAlloy",
                "SkillName": "Smelting",
                "CraftingTable": "ModdedFurnaceItem",
                "Ingredients": [{"Name": "IronIngotItem", "Quantity": 4}],
                "Products": [{"Name": "ModdedAlloyItem", "Quantity": 2}],
            }
        ],
    }
    payload.update(overrides)
    return payload


def _write(tmp_path: Path, payload: Any, name: str = "export.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload) if not isinstance(payload, str) else payload)
    return path


def test_a_valid_modded_export_is_served_and_labelled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write(tmp_path, _export())
    monkeypatch.setenv(MODDED_EXPORT_ENV, str(path))

    index = load_recipe_index()
    payload = index.to_dict()

    assert payload["sourceKind"] == SOURCE_KIND_MODDED_EXPORT
    assert payload["serverSpecific"] is True
    assert "Eco via Sirens" in payload["source"]
    assert payload["exportedAtISO"]
    assert payload["version"] == 7
    assert any(r["name"] == "ModdedAlloy" for r in payload["recipes"])
    # Nothing to apologise for when the real thing loaded.
    assert not any("vanilla" in w.lower() for w in payload["warnings"])


def test_no_export_configured_serves_the_seed_without_crying_wolf() -> None:
    """The default deploy. Vanilla is correct here, so there is nothing to warn about."""
    payload = load_recipe_index().to_dict()
    assert payload["serverSpecific"] is False
    assert payload["sourceKind"] != SOURCE_KIND_MODDED_EXPORT
    assert not any("Serving the vanilla recipe seed" in w for w in payload["warnings"])


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        pytest.param("{not json", "not valid JSON", id="malformed"),
        pytest.param({"Version": 1}, "not a DataExporter-shaped export", id="wrong-shape"),
        pytest.param({"Version": 1, "Recipes": []}, "not a DataExporter-shaped", id="no-recipes"),
    ],
)
def test_a_bad_export_falls_back_and_says_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: Any, expected: str
) -> None:
    path = _write(tmp_path, payload)
    monkeypatch.setenv(MODDED_EXPORT_ENV, str(path))

    result = load_recipe_index().to_dict()

    assert result["serverSpecific"] is False
    warning = " ".join(result["warnings"])
    assert "Serving the vanilla recipe seed" in warning
    assert expected in warning
    assert "NOT server-specific" in warning
    # And it still serves recipes — degraded, not broken.
    assert result["counts"]["recipes"] > 0


def test_a_missing_export_file_falls_back_and_says_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(MODDED_EXPORT_ENV, str(tmp_path / "does-not-exist.json"))
    result = load_recipe_index().to_dict()
    assert result["serverSpecific"] is False
    assert "could not be read" in " ".join(result["warnings"])


def test_a_stale_export_is_refused_rather_than_trusted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A modded graph that no longer matches the server is wrong *and* claims not to be."""
    old = (datetime.now(UTC) - timedelta(days=60)).isoformat()
    path = _write(tmp_path, _export(ExportedAt=old))
    monkeypatch.setenv(MODDED_EXPORT_ENV, str(path))

    result = load_recipe_index().to_dict()

    assert result["serverSpecific"] is False
    warning = " ".join(result["warnings"])
    assert "freshness bound" in warning
    assert "60." in warning  # names the actual age


def test_an_unparseable_timestamp_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write(tmp_path, _export(ExportedAt="last tuesday"))
    monkeypatch.setenv(MODDED_EXPORT_ENV, str(path))
    result = load_recipe_index().to_dict()
    assert result["serverSpecific"] is False
    assert "unparseable ExportedAt" in " ".join(result["warnings"])


def test_an_export_without_a_timestamp_is_accepted_but_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The operator pointed at it deliberately; the missing stamp rides along."""
    payload = _export()
    del payload["ExportedAt"]
    path = _write(tmp_path, payload)
    monkeypatch.setenv(MODDED_EXPORT_ENV, str(path))

    result = load_recipe_index().to_dict()

    assert result["serverSpecific"] is True
    assert result["exportedAtISO"] is None
    assert "at an unstated time" in result["source"]


def test_the_freshness_bound_is_configurable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    path = _write(tmp_path, _export(ExportedAt=old))
    monkeypatch.setenv(MODDED_EXPORT_ENV, str(path))
    monkeypatch.setattr(recipes_mod, "MODDED_EXPORT_MAX_AGE_DAYS", 90.0)

    assert load_recipe_index().to_dict()["serverSpecific"] is True


def test_the_ingestion_never_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The contract is read-only: no game, player, store or pricing state is touched."""
    path = _write(tmp_path, _export())
    before = sorted(p.name for p in tmp_path.iterdir())
    stat_before = path.stat().st_mtime_ns
    monkeypatch.setenv(MODDED_EXPORT_ENV, str(path))

    load_recipe_index()

    assert sorted(p.name for p in tmp_path.iterdir()) == before
    assert path.stat().st_mtime_ns == stat_before


def test_an_export_that_cannot_be_parsed_falls_back_and_says_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write(tmp_path, {"Version": 5, "Recipes": ["not a recipe object"]})
    monkeypatch.setenv(MODDED_EXPORT_ENV, str(path))

    result = load_recipe_index().to_dict()

    assert result["serverSpecific"] is False
    assert "could not be parsed" in " ".join(result["warnings"])
    assert result["counts"]["recipes"] > 0


def test_an_export_whose_layout_changed_yields_no_recipes_and_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Renamed keys parse without raising. Serving the empty graph as server-specific is the bug."""
    renamed = [{"Name": f"R{i}", "Outputs": [{"Name": "X", "Quantity": 1}]} for i in range(10)]
    path = _write(tmp_path, _export(Version=5, Recipes=renamed))
    monkeypatch.setenv(MODDED_EXPORT_ENV, str(path))

    result = load_recipe_index().to_dict()

    assert result["serverSpecific"] is False
    assert "layout may have changed" in " ".join(result["warnings"])


def test_a_parseable_export_of_an_unchecked_version_is_served_with_a_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unchecked = max(recipes_mod.MODDED_EXPORT_CHECKED_VERSIONS) + 1
    path = _write(tmp_path, _export(Version=unchecked))
    monkeypatch.setenv(MODDED_EXPORT_ENV, str(path))

    result = load_recipe_index().to_dict()

    assert result["serverSpecific"] is True
    assert f"Version {unchecked} has not been checked" in " ".join(result["warnings"])


def test_a_checked_version_carries_no_version_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write(tmp_path, _export(Version=3))
    monkeypatch.setenv(MODDED_EXPORT_ENV, str(path))

    assert load_recipe_index().to_dict()["warnings"] == []
