"""One-line reply templates carried on each tool's MCP ``_meta``.

A router that has already picked a tool can answer from the call result
without an LLM when a template renders. The contract, shared with the Go
renderer in Sirens Echo, is docs/dual-route-inventory.md#reply-templates. This module is the
reference implementation and the single source of the shipped templates.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from mcp.types import Tool

from .vocab import ARGS_META_KEY, TOOL_ARGS

TEMPLATES_META_KEY = "coilyco/templates"
MAX_REPLY_CHARS = 280

_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z0-9_.]+)\s*\}\}")

# Every path here must resolve against a real payload: tests render each one
# through the builder that produces the tool's structuredContent.
REPLY_TEMPLATES: dict[str, list[dict[str, Any]]] = {
    "find_trade": [
        {
            "when_args": ["item"],
            "text": (
                "The cheapest {{cheapest.0.itemPretty}} is {{cheapest.0.cheapest}} "
                "{{cheapest.0.currency}} at {{cheapest.0.offers.0.store}}, owned by "
                "{{cheapest.0.offers.0.owner}}."
            ),
        },
    ],
    "get_market": [
        {
            "when_args": ["item"],
            "text": (
                "{{markets.0.itemPretty}} last traded at a median of "
                "{{markets.0.latestPrice}} {{markets.0.currency}} on day "
                "{{markets.0.latestDay}}. Price trend: {{markets.0.trend}}."
            ),
        },
    ],
    "price_recipe": [
        {
            "when_args": ["product"],
            "text": (
                "Making one {{recipes.0.displayName}} costs about "
                "{{recipes.0.cost.perUnitCost}} at current market prices, each "
                "ingredient priced in its busiest currency."
            ),
        },
    ],
    # The payload carries the per-stage line itself, since a template has no loop.
    "price_by_stage": [
        # Stage first, so a caller filling both takes the shorthand off the item.
        {"when_args": ["stage", "item"], "text": "{{reply}}"},
        {"when_args": ["item"], "text": "{{reply}}"},
        # Last, so a caller without `when_unmatched` reaches it only after a miss.
        {"when_unmatched": ["item"], "text": "Couldn't match that to one Eco item."},
    ],
    "get_currency": [
        {
            "when_args": ["currency"],
            "text": (
                "The top holder of {{selected.name}} is "
                "{{selected.holders.list.0.holder}} with "
                "{{selected.holders.list.0.balance}}."
            ),
        },
        # A government or company account has no single owner, so holder is null.
        {
            "when_args": ["currency"],
            "text": (
                "The top holder of {{selected.name}} is the account "
                "{{selected.holders.list.0.account}} with "
                "{{selected.holders.list.0.balance}}."
            ),
        },
    ],
    "get_server_status": [
        # Renders only once the meteor is destroyed: destroyedOnDay is null in
        # every other state, so this entry drops out and the next one answers.
        {
            "when_args": [],
            "text": (
                "{{players.online}} players online on day {{cycle.daysRunning}}. "
                "The meteor was destroyed on day {{cycle.meteor.destroyedOnDay}}. "
                "Server version {{server.version}}."
            ),
        },
        {
            "when_args": [],
            "text": (
                "{{players.online}} players online on day {{cycle.daysRunning}}. "
                "The meteor hits in {{cycle.daysUntilMeteor}} days. Server version "
                "{{server.version}}."
            ),
        },
        {
            "when_args": [],
            "text": (
                "{{players.online}} players online on day {{cycle.daysRunning}}. "
                "Server version {{server.version}}."
            ),
        },
    ],
}


def with_reply_templates(tools: Sequence[Tool]) -> list[Tool]:
    """Attach each shipped template list and argument vocabulary map to its
    tool, keeping any other _meta."""
    attached: list[Tool] = []
    for tool in tools:
        extra: dict[str, Any] = {}
        if (templates := REPLY_TEMPLATES.get(tool.name)) is not None:
            extra[TEMPLATES_META_KEY] = templates
        if (args := TOOL_ARGS.get(tool.name)) is not None:
            extra[ARGS_META_KEY] = args
        if not extra:
            attached.append(tool)
            continue
        attached.append(tool.model_copy(update={"meta": {**(tool.meta or {}), **extra}}))
    return attached


def render_reply(
    templates: Sequence[Mapping[str, Any]],
    payload: Mapping[str, Any],
    args: Mapping[str, Any],
) -> str | None:
    """The first eligible template rendered, or None when none is eligible."""
    for template in templates:
        # A `when_unmatched` entry answers before any call, so never over a payload.
        if template.get("when_unmatched"):
            continue
        if not all(_present(args.get(name)) for name in template.get("when_args", [])):
            continue
        rendered = _render_one(str(template.get("text", "")), payload, args)
        if rendered is not None and len(rendered) <= MAX_REPLY_CHARS:
            return rendered
    return None


def _render_one(text: str, payload: Mapping[str, Any], args: Mapping[str, Any]) -> str | None:
    missing = False

    def substitute(match: re.Match[str]) -> str:
        nonlocal missing
        value = _resolve(match.group(1), payload, args)
        formatted = _format(value)
        if formatted is None:
            missing = True
            return ""
        return formatted

    rendered = _PLACEHOLDER.sub(substitute, text)
    return None if missing else rendered


def _resolve(path: str, payload: Mapping[str, Any], args: Mapping[str, Any]) -> Any:
    segments = path.split(".")
    node: Any = payload
    if segments[0] == "args":
        node, segments = args, segments[1:]
    for segment in segments:
        if isinstance(node, Mapping):
            node = node.get(segment)
        elif isinstance(node, list) and segment.isdigit() and int(segment) < len(node):
            node = node[int(segment)]
        else:
            return None
    return node


def _format(value: Any) -> str | None:
    # bool is an int subclass, so it is excluded before the number branch.
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str):
        return value if value.strip() else None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        text = f"{value:.2f}".rstrip("0").rstrip(".")
        return "0" if text == "-0" else text
    return None


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True
