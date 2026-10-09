"""`get_mods`: a metadata-only mod inventory that never reads as zero when it is unknown (COI-763).

The tree mount is not done in prod (COI-764), so the unavailable state is the shipped
state today and gets the same weight as the populated one.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import mcp.types as mt
import pytest
from starlette.testclient import TestClient

from eco_mcp_app import mods as mods_mod
from eco_mcp_app.http_app import create_app
from eco_mcp_app.mods import MODS_DIR_ENV, read_mods
from eco_mcp_app.server import build_server


def _mod(
    root: Path, rel: str, manifest: dict[str, Any] | None = None, name: str = "mod.json"
) -> None:
    directory = root / rel
    directory.mkdir(parents=True)
    if manifest is not None:
        (directory / name).write_text(json.dumps(manifest))


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "Mods"
    _mod(root, "AutoGen")
    _mod(root, "Zeta", {"version": "2.0"})
    _mod(root, "UserCode/Alpha", {"Version": 7}, name="manifest.json")
    _mod(root, "UserCode/Beta", {"author": "alice", "apiToken": "s3cret-token", "version": "1.2.3"})
    _mod(root, ".git")
    (root / "stray.txt").write_text("not a mod")
    return root


async def _call(arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    handler = build_server().request_handlers[mt.CallToolRequest]
    result = await handler(
        mt.CallToolRequest(
            method="tools/call",
            params=mt.CallToolRequestParams(name="get_mods", arguments=arguments),
        )
    )
    blocks = [b.text for b in result.root.content if isinstance(b, mt.TextContent)]
    return blocks[0], json.loads(blocks[1])


def test_unset_is_unavailable_with_null_not_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(MODS_DIR_ENV, raising=False)
    payload = read_mods()
    assert payload["available"] is False
    assert payload["count"] is None
    assert payload["mods"] is None
    assert any(MODS_DIR_ENV in w for w in payload["warnings"])


def test_a_missing_directory_is_unavailable(tmp_path: Path) -> None:
    payload = read_mods(str(tmp_path / "not-mounted"))
    assert payload["available"] is False
    assert payload["count"] is None
    assert payload["mods"] is None
    assert payload["warnings"]


def test_an_unreadable_tree_is_unavailable_and_leaks_no_path(
    tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _denied(self: Path) -> Any:
        raise PermissionError(f"denied: {self}")

    monkeypatch.setattr(Path, "iterdir", _denied)
    payload = read_mods(str(tree))
    assert payload["available"] is False
    assert payload["mods"] is None
    text = json.dumps(payload)
    assert "PermissionError" in text
    assert str(tree) not in text


def test_a_populated_tree_lists_names_groups_and_versions(tree: Path) -> None:
    payload = read_mods(str(tree))
    assert payload["available"] is True
    assert payload["count"] == 4
    assert payload["warnings"] == []
    assert payload["mods"] == [
        {"name": "Alpha", "group": "UserCode", "version": "7"},
        {"name": "AutoGen", "group": "Mods", "version": None},
        {"name": "Beta", "group": "UserCode", "version": "1.2.3"},
        {"name": "Zeta", "group": "Mods", "version": "2.0"},
    ]


def test_an_empty_mounted_tree_is_a_real_zero(tmp_path: Path) -> None:
    empty = tmp_path / "Mods"
    empty.mkdir()
    payload = read_mods(str(empty))
    assert payload["available"] is True
    assert payload["count"] == 0
    assert payload["mods"] == []


def test_manifest_contents_beyond_version_never_leave_the_tree(tree: Path) -> None:
    text = json.dumps(read_mods(str(tree)))
    for leaked in ("alice", "s3cret-token", "apiToken", "author"):
        assert leaked not in text


def test_a_symlink_out_of_the_tree_is_skipped(tree: Path, tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (tree / "Escape").symlink_to(outside, target_is_directory=True)
    names = [m["name"] for m in read_mods(str(tree))["mods"]]
    assert "Escape" not in names


def test_a_tree_over_the_cap_says_it_was_cut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "Mods"
    for i in range(5):
        _mod(root, f"Mod{i}")
    monkeypatch.setattr(mods_mod, "MAX_MODS", 3)
    payload = read_mods(str(root))
    assert payload["count"] == 5
    assert len(payload["mods"]) == 3
    assert any("only the first 3" in w for w in payload["warnings"])


@pytest.mark.asyncio
async def test_the_tool_reports_unavailable_through_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(MODS_DIR_ENV, raising=False)
    markdown, payload = await _call({})
    assert "Unavailable" in markdown
    assert payload["count"] is None and payload["mods"] is None
    assert list(payload)[:2] == ["view", "warnings"]


@pytest.mark.asyncio
async def test_the_tool_bounds_its_list_by_limit_and_says_so(
    tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(MODS_DIR_ENV, str(tree))
    _, payload = await _call({"limit": 2})
    assert payload["count"] == 4
    assert len(payload["mods"]) == 2
    assert any(w.startswith("mods: showing 2 of 4") for w in payload["warnings"])
    assert list(payload).index("warnings") < list(payload).index("mods")

    _, everything = await _call({"limit": 0})
    assert len(everything["mods"]) == 4


@pytest.mark.asyncio
async def test_the_tool_is_advertised_read_only() -> None:
    handler = build_server().request_handlers[mt.ListToolsRequest]
    result = await handler(mt.ListToolsRequest(method="tools/list"))
    tool = next(t for t in result.root.tools if t.name == "get_mods")
    assert tool.annotations is not None and tool.annotations.readOnlyHint is True
    assert "server" not in (tool.inputSchema.get("properties") or {})


def test_the_rest_route_serves_the_same_payload(
    tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(MODS_DIR_ENV, str(tree))
    response = TestClient(create_app()).get("/preview/get_mods.json", params={"limit": 0})
    assert response.status_code == 200
    assert response.json()["count"] == 4
