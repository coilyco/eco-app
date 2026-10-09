"""World-generator metadata (seed, size, cluster centers) for `get_world` (COI-763).

Scavenged from the retired `/admin` MCP's `admin_world_meta`, which read
`Configs/WorldGenerator.eco`. No HTTP source carries the seed: `/info` reports
`WorldSize` and nothing else, and the action exporter has no generator fields. So the
only source is that one host file, and `ECO_WORLD_GENERATOR_FILE` names exactly that
file. Pointing at the single file, not at `Configs/`, keeps the Discord and server API
tokens that live beside it out of the public app's mount.

Unset or unreadable is the explicit unavailable state, `None` plus a warning naming
what is missing, never an empty dict that reads as a world with no generator settings.
The tool was only ever tested on synthetic fixtures, so which keys a real file carries
is unverified, which is why matching is by key name anywhere in the document.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

WORLD_GENERATOR_FILE_ENV = "ECO_WORLD_GENERATOR_FILE"
MAX_FILE_BYTES = 1024 * 1024
MAX_FIELDS = 50
MAX_LIST_ITEMS = 20

_META_KEYS = frozenset(
    {
        "seed",
        "dimensions",
        "width",
        "height",
        "worldsize",
        "worlddimensions",
        "worldgenerator",
        "worldgeneratorversion",
        "sealevel",
        "climate",
        "meteor",
        "meteorimpactdays",
    }
)


def _wanted(key: str) -> bool:
    flat = key.replace("_", "").casefold()
    return flat in _META_KEYS or ("cluster" in flat and "center" in flat)


def _pick(document: Any) -> dict[str, Any]:
    selected: dict[str, Any] = {}

    def walk(value: Any) -> None:
        if len(selected) >= MAX_FIELDS:
            return
        if isinstance(value, dict):
            for key, child in value.items():
                if _wanted(str(key)):
                    selected.setdefault(str(key), child)
                else:
                    walk(child)
        elif isinstance(value, list):
            for child in value[:MAX_LIST_ITEMS]:
                walk(child)

    walk(document)
    return selected


def read_world_generator(raw_path: str | None = None) -> tuple[dict[str, Any] | None, str | None]:
    """`(metadata, None)` when readable, `(None, warning)` when not."""
    configured = raw_path if raw_path is not None else os.environ.get(WORLD_GENERATOR_FILE_ENV)
    if not configured:
        return None, (
            "worldGenerator: unavailable, "
            f"{WORLD_GENERATOR_FILE_ENV} is not set, so seed, size and cluster centers "
            "are not reported"
        )
    path = Path(configured)
    try:
        if not path.is_file():
            return None, (
                f"worldGenerator: unavailable, the file named by {WORLD_GENERATOR_FILE_ENV} "
                "does not exist"
            )
        if path.stat().st_size > MAX_FILE_BYTES:
            return None, "worldGenerator: unavailable, the generator file is over 1 MiB"
        document = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except OSError as exc:
        kind = type(exc).__name__
        return None, f"worldGenerator: unavailable, the file could not be read ({kind})"
    except ValueError:
        return None, "worldGenerator: unavailable, the generator file is not valid JSON"
    selected = _pick(document)
    if not selected:
        return None, (
            "worldGenerator: unavailable, the generator file holds none of the seed, size "
            "or cluster-center fields this tool reports"
        )
    return selected, None
