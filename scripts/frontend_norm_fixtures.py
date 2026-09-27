"""Annotated price payloads for the frontend's norm render tests (eco-app#8368).

Runs the backend's own annotator over tests/mcp/fixtures/price_payloads and
writes the results to frontend/src/test/fixtures/norms, so the page tests see
the exact shape production sends. tests/mcp/test_frontend_norm_fixtures.py fails
when the committed copies drift. Regenerate with `just frontend-norm-fixtures`.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from eco_mcp_app import norms

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "tests" / "mcp" / "fixtures" / "price_payloads"
TARGET = ROOT / "frontend" / "src" / "test" / "fixtures" / "norms"
LIVE = norms.LiveContext(stage="Modern 4", cycle=14, currency_names={"2533707": "Spectres"})
ROUTES = {
    "get_trades": "get_trades",
    "get_market": "get_market",
    "get_stores": "get_stores",
    "find_trade": "find_trade",
    "get_recipes": "get_recipes",
    "item": "/preview/item.json",
    "price-history": "/preview/price-history.json",
}


def build() -> dict[str, str]:
    out: dict[str, str] = {}
    for name, route in ROUTES.items():
        payload: Any = copy.deepcopy(json.loads((SOURCE / f"{name}.json").read_text()))
        norms.annotate(route, payload, LIVE)
        out[f"{name}.json"] = json.dumps(payload, indent=1, sort_keys=True) + "\n"
    return out


def main() -> None:
    TARGET.mkdir(parents=True, exist_ok=True)
    for name, text in build().items():
        (TARGET / name).write_text(text)
    print(f"wrote {len(ROUTES)} fixtures to {TARGET.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
