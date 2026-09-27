from __future__ import annotations

import gzip
import json
from pathlib import Path

from scripts.trades_baseline import build


def _msg(i: int, field: str, lines: list[str], result: str) -> dict:
    return {
        "id": str(i),
        "timestamp": "2026-08-01T00:00:00+00:00",
        "author": {"username": "eco-sirens", "bot": True},
        "content": "",
        "embeds": [
            {
                "title": "t",
                "fields": [
                    {"name": field, "value": "\n".join(lines) + "\n\nTotal = 0"},
                    {"name": "Result", "value": result},
                ],
            }
        ],
    }


def _build(tmp_path: Path, messages: list[dict]) -> tuple[dict, list[str]]:
    raw = tmp_path / "raw.jsonl"
    raw.write_text("\n".join(json.dumps(m) for m in messages) + "\n")
    autogen = tmp_path / "autogen.json.gz"
    with gzip.open(autogen, "wt") as fh:
        json.dump(
            {
                "recipes": [
                    {
                        "product": {
                            "item": "EmptyCanItem",
                            "displayName": "Empty Can",
                            "isTag": False,
                        },
                        "byproducts": [],
                        "ingredients": [],
                    }
                ]
            },
            fh,
        )
    return build(raw, autogen)


def test_prices_currency_side_and_item_id(tmp_path: Path) -> None:
    out, bad = _build(
        tmp_path,
        [
            _msg(1, "Sold", ["248 X Empty Can * 0.14 = 34.72"], "*A* gained 34.72 *Racines*."),
            _msg(2, "Bought", ["2 X Empty Can * 0.20 = 0.40"], "*B* paid 0.40 *Racines*."),
            _msg(3, "Bought", ["1,000 X Empty Can * 0.10 = 100"], "*C* paid 100 *Spectres*."),
        ],
    )
    can = out["items"]["Empty Can"]
    assert can["itemId"] == "EmptyCanItem"
    assert can["pooled"]["observations"] == 3
    assert can["pooled"]["median"] == 0.14
    assert can["pooled"]["weightedMean"] == round((34.72 + 0.4 + 100) / 1250, 4)
    assert can["byCurrency"]["Racines"]["observations"] == 2
    assert can["byCurrency"]["Spectres"]["quantity"] == 1000
    assert can["sides"] == {"bought": 2, "sold": 1}
    assert bad == [] and out["source"]["totalMismatches"] == 0


def test_negative_fees_numbered_baskets_and_duplicate_messages(tmp_path: Path) -> None:
    fee = _msg(1, "Bought (1)", ["1 X Garbage * -0.01 = -0.01"], "*A* gained 0.01 *Souls*.")
    out, bad = _build(
        tmp_path, [fee, fee, _msg(2, "Sold", ["garbled line"], "*A* gained 1 *Souls*.")]
    )
    assert out["items"]["Garbage"]["pooled"]["median"] == -0.01
    assert out["source"]["messages"] == 2
    assert bad == ["garbled line"]


def test_no_currency_exchanged_is_barter(tmp_path: Path) -> None:
    out, _ = _build(
        tmp_path, [_msg(1, "Bought", ["5 X Board * 0.50 = 2.50"], "No currency was exchanged.")]
    )
    assert list(out["items"]["Board"]["byCurrency"]) == ["Barter"]


def test_total_that_does_not_reproduce_is_counted(tmp_path: Path) -> None:
    out, _ = _build(
        tmp_path, [_msg(1, "Sold", ["10 X Board * 1.00 = 50"], "*A* gained 50 *Souls*.")]
    )
    assert out["source"]["totalMismatches"] == 1
