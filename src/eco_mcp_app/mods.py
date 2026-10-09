"""`get_mods`: a metadata-only inventory of the Eco mods installed on this server (COI-763).

Scavenged from the retired `/admin` MCP's `admin_mods_installed`. It reads names and
versions off a mounted mods tree and nothing else: no player names, no config files
(the old tool also read `Configs/Mods.diff.json` for a `configuredMissing` list, and
`Configs/` holds tokens, so that half is gone), and no file contents beyond the
`version` field of a manifest.

The tree mount is operator work that has not happened in prod (COI-764 owns it). Until
it does, `ECO_MODS_DIR` is unset and the answer is the explicit unavailable state:
`available: false`, `count: null`, `mods: null`, and a warning naming what is missing.
An empty list would read as "zero mods installed", which is a claim nobody checked.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

MODS_DIR_ENV = "ECO_MODS_DIR"
MAX_MODS = 500
MAX_MANIFEST_BYTES = 256 * 1024
_MANIFESTS = ("mod.json", "manifest.json", "package.json")
_VERSION_KEYS = ("version", "Version", "modVersion", "ModVersion")
# Eco keeps operator-written mods one level down, beside the vendor-shipped ones.
_USER_CODE = "UserCode"


def _unavailable(reason: str) -> dict[str, Any]:
    return {
        "view": "eco_mods",
        "warnings": [f"mods: unavailable, {reason}. This is not a count of zero mods."],
        "available": False,
        "count": None,
        "mods": None,
    }


def _manifest_version(directory: Path) -> str | None:
    for name in _MANIFESTS:
        manifest = directory / name
        try:
            if not manifest.is_file() or manifest.stat().st_size > MAX_MANIFEST_BYTES:
                continue
            data = json.loads(manifest.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            for key in _VERSION_KEYS:
                value = data.get(key)
                if isinstance(value, str | int | float) and not isinstance(value, bool):
                    return str(value)
    return None


def read_mods(raw_root: str | None = None) -> dict[str, Any]:
    """The mod inventory under `raw_root` (default `$ECO_MODS_DIR`), or the unavailable state."""
    configured = raw_root if raw_root is not None else os.environ.get(MODS_DIR_ENV)
    if not configured:
        return _unavailable(f"{MODS_DIR_ENV} is not set, so no mods tree is mounted into this app")
    try:
        root = Path(configured).resolve()
        if not root.is_dir():
            return _unavailable(f"the directory named by {MODS_DIR_ENV} does not exist")
        children = sorted(root.iterdir(), key=lambda item: item.name.casefold())
        found: list[tuple[str, Path]] = []
        for child in children:
            if child.name.startswith(".") or not child.is_dir():
                continue
            if child.name == _USER_CODE:
                found.extend(
                    (_USER_CODE, sub)
                    for sub in sorted(child.iterdir(), key=lambda item: item.name.casefold())
                    if sub.is_dir() and not sub.name.startswith(".")
                )
            else:
                found.append(("Mods", child))
        found.sort(key=lambda item: item[1].name.casefold())
        mods: list[dict[str, Any]] = []
        for group, path in found:
            # A symlink pointing out of the tree is skipped, never followed.
            if not path.resolve().is_relative_to(root):
                continue
            mods.append({"name": path.name, "group": group, "version": _manifest_version(path)})
    except OSError as exc:
        return _unavailable(f"the mods tree could not be read ({type(exc).__name__})")
    warnings: list[str] = []
    total = len(mods)
    if total > MAX_MODS:
        warnings.append(
            f"mods: the tree holds {total} mods and only the first {MAX_MODS} are listed"
        )
        mods = mods[:MAX_MODS]
    return {
        "view": "eco_mods",
        "warnings": warnings,
        "available": True,
        "count": total,
        "mods": mods,
    }
