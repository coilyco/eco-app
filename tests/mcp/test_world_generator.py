"""World-generator metadata in `get_world`, null with a warning when unmounted (COI-763)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import mcp.types as mt
import pytest

from eco_mcp_app import server as eco_server
from eco_mcp_app.server import build_server
from eco_mcp_app.world import WorldActivity
from eco_mcp_app.worldgen import WORLD_GENERATOR_FILE_ENV, read_world_generator

GENERATOR = {
    "Config": {
        "Seed": "sample-seed",
        "WorldDimensions": {"Width": 140, "Height": 140},
        "SeaLevel": 0.42,
        "ClusterCenters": [{"X": 10, "Z": 20}, {"X": 70, "Z": 90}],
        "InternalTuning": "not-selected",
        "DiscordToken": "must-not-appear",
    }
}


def _file(tmp_path: Path, body: str) -> str:
    path = tmp_path / "WorldGenerator.eco"
    path.write_text(body)
    return str(path)


def test_unset_is_none_with_a_warning_naming_the_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(WORLD_GENERATOR_FILE_ENV, raising=False)
    metadata, warning = read_world_generator()
    assert metadata is None
    assert warning is not None and WORLD_GENERATOR_FILE_ENV in warning


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        (None, "does not exist"),
        ("{not json", "not valid JSON"),
        (json.dumps({"Config": {"Unrelated": 1}}), "none of the seed"),
    ],
)
def test_every_unusable_file_is_none_with_a_reason(
    tmp_path: Path, body: str | None, fragment: str
) -> None:
    path = str(tmp_path / "missing.eco") if body is None else _file(tmp_path, body)
    metadata, warning = read_world_generator(path)
    assert metadata is None
    assert warning is not None and fragment in warning


def test_seed_size_and_cluster_centers_are_picked_and_the_rest_is_not(tmp_path: Path) -> None:
    metadata, warning = read_world_generator(_file(tmp_path, json.dumps(GENERATOR)))
    assert warning is None
    assert metadata == {
        "Seed": "sample-seed",
        "WorldDimensions": {"Width": 140, "Height": 140},
        "SeaLevel": 0.42,
        "ClusterCenters": [{"X": 10, "Z": 20}, {"X": 70, "Z": 90}],
    }
    assert "must-not-appear" not in json.dumps(metadata)


async def _get_world(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    async def _fetch(**_: Any) -> WorldActivity:
        return WorldActivity(
            fetched_at_iso="t", source_base_url="u", total_events=0, warnings=["exporter slow"]
        )

    monkeypatch.setattr(eco_server, "fetch_world", _fetch)
    handler = build_server().request_handlers[mt.CallToolRequest]
    result = await handler(
        mt.CallToolRequest(
            method="tools/call",
            params=mt.CallToolRequestParams(name="get_world", arguments={}),
        )
    )
    blocks = [b.text for b in result.root.content if isinstance(b, mt.TextContent)]
    return json.loads(blocks[1])


@pytest.mark.asyncio
async def test_get_world_reports_null_and_a_warning_when_unmounted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(WORLD_GENERATOR_FILE_ENV, raising=False)
    payload = await _get_world(monkeypatch)
    assert payload["worldGenerator"] is None
    assert any(WORLD_GENERATOR_FILE_ENV in w for w in payload["warnings"])
    assert "exporter slow" in payload["warnings"]


@pytest.mark.asyncio
async def test_get_world_carries_the_generator_block_when_mounted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(WORLD_GENERATOR_FILE_ENV, _file(tmp_path, json.dumps(GENERATOR)))
    payload = await _get_world(monkeypatch)
    assert payload["worldGenerator"]["Seed"] == "sample-seed"
    assert not any("worldGenerator" in w for w in payload["warnings"])
    assert list(payload).index("warnings") < list(payload).index("worldGenerator")
