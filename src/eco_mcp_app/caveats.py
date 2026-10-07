"""Caveat and coverage fields lead every tool's JSON, ahead of the bulk arrays (COI-757).

A consumer that truncates a response keeps the head and drops the tail. `warnings` used
to be the last key, so the rows survived and the line saying they were partial did not.
Truncation notices are appended to `warnings` after `to_dict` returns, and `itemFilter`
is added the same way, so the order is fixed here once at the dispatch seam rather than
in each `to_dict`.
"""

from __future__ import annotations

import json
from typing import Any

from mcp.types import CallToolResult, TextContent

# Fronted in this order, after `view` when a payload has one. A key absent from a payload
# is skipped. `*Note` keys and `*_note` keys join them by suffix in `is_caveat`.
CAVEAT_KEYS: tuple[str, ...] = (
    "warnings",
    "warning",
    "itemFilter",
    "unavailableActions",
    "adminAvailable",
    "datasets_unavailable",
    "redacted",
    "live",
    "stale",
    "citizensAvailable",
    "citizensReturned",
    "recipesMatched",
    "recipesReturned",
    "indexScope",
    "skillsCrossChecked",
    "skillsInUseNotInGraph",
    "populationSampled",
    "populationTotalSamples",
    "feedTruncated",
    "error",
    "counts",
    "notes",
    "note",
    "caveat",
)


def is_caveat(key: str) -> bool:
    return key in CAVEAT_KEYS or key.endswith(("Note", "_note"))


def caveats_first(payload: dict[str, Any]) -> dict[str, Any]:
    """`payload` with its caveat keys moved up front, every other key left in place."""
    lead = [k for k in payload if k == "view"]
    named = [k for k in CAVEAT_KEYS if k in payload]
    suffixed = [k for k in payload if is_caveat(k) and k not in CAVEAT_KEYS]
    front = [*lead, *named, *suffixed]
    if list(payload)[: len(front)] == front:
        return payload
    rest = [k for k in payload if k not in front]
    return {k: payload[k] for k in [*front, *rest]}


def reorder_result(result: CallToolResult) -> CallToolResult:
    """Apply `caveats_first` to every JSON object block and to `structuredContent`."""
    for index, block in enumerate(result.content):
        if not isinstance(block, TextContent):
            continue
        try:
            payload = json.loads(block.text)
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        ordered = caveats_first(payload)
        if ordered is not payload:
            result.content[index] = TextContent(type="text", text=json.dumps(ordered))
    if isinstance(result.structuredContent, dict):
        result.structuredContent = caveats_first(result.structuredContent)
    return result
