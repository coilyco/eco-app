"""Shared typed contracts for public REST and MCP operations."""

from __future__ import annotations

import json
import math
from collections.abc import Awaitable, Callable
from typing import Any

from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, RootModel

from .dual_routes import DualRouteRegistry, DualRouteResult

ToolInvoker = Callable[[str, dict[str, Any]], Awaitable[CallToolResult]]

READ_ONLY_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)
CURATED_SERVERS_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


class EmptyInput(BaseModel):
    """An operation with no inputs."""

    model_config = ConfigDict(extra="forbid")


class ServerInput(BaseModel):
    """Select an Eco server, or use the configured default."""

    model_config = ConfigDict(extra="forbid")

    server: str | None = Field(
        default=None,
        description="Eco server as a host, host:port, or full URL.",
    )


LIMIT_DESCRIPTION = (
    "Maximum detail rows to return per unbounded list. Defaults to a slice that "
    "keeps a no-argument call inside an MCP client's response cap; the summary and "
    "aggregate fields always cover every row regardless. 0 means no limit - the SPA "
    "uses that, an MCP caller should not."
)


class BoundedServerInput(ServerInput):
    """A server selector whose detail arrays are bounded by default.

    Six tools returned 60-220 KB on a no-argument call and were truncated by
    the client, most with no parameter available to bound the payload, so there
    was no caller-side workaround at all (#256).
    """

    limit: int = Field(default=50, ge=0, description=LIMIT_DESCRIPTION)


class CurrencyInput(BoundedServerInput):
    """Select an Eco server and optionally one currency."""

    currency: str | None = Field(
        default=None,
        description="Optional case-insensitive currency name.",
    )


class TradeInput(BoundedServerInput):
    """Select an Eco server and optional market filters, bounded.

    Bounded because `markets` grows with the number of distinct traded items
    and the logistics arrays with the number of stores (#6076).
    """

    item: str | None = Field(
        default=None,
        description="Optional case-insensitive Eco item filter.",
    )
    currency: str | None = Field(
        default=None,
        description="Optional case-insensitive currency name.",
    )


class JsonObjectOutput(RootModel[dict[str, Any]]):
    """A JSON object produced by an established Eco domain report."""


def register_json_route(
    registry: DualRouteRegistry,
    invoke: ToolInvoker,
    *,
    name: str,
    title: str,
    description: str,
    rest_path: str,
    input_model: type[BaseModel],
) -> None:
    """Register a read-only operation whose established output is a JSON object."""
    decorator = registry.register(
        name=name,
        title=title,
        description=description,
        rest_path=rest_path,
        rest_method="GET",
        input_model=input_model,
        output_model=JsonObjectOutput,
        annotations=READ_ONLY_ANNOTATIONS,
    )

    async def handler(request: BaseModel) -> DualRouteResult[JsonObjectOutput]:
        arguments = request.model_dump(mode="json", exclude_none=True)
        result = await invoke(name, arguments)
        text, payload, is_error = extract_result(result)
        return DualRouteResult(
            text=text,
            payload=JsonObjectOutput(payload),
            is_error=is_error,
            rest_status=502 if is_error else 200,
        )

    decorator(handler)


def extract_result(result: CallToolResult) -> tuple[str, dict[str, Any], bool]:
    """Extract the shared readable and JSON blocks from an established tool result."""
    text_blocks = [block.text for block in result.content if isinstance(block, TextContent)]
    text = text_blocks[0] if text_blocks else "Eco operation completed."
    payload: Any = result.structuredContent
    if not isinstance(payload, dict):
        for block in text_blocks[1:]:
            try:
                candidate = json.loads(block)
            except (TypeError, ValueError):
                continue
            if isinstance(candidate, dict):
                payload = candidate
                break

    is_error = bool(result.isError)
    if not isinstance(payload, dict):
        text = "Eco operation could not produce structured output."
        payload = {
            "view": "error",
            "message": "Structured output was unavailable.",
        }
        is_error = True
    elif is_error and "error" not in payload:
        payload = {
            **payload,
            "error": payload.get("message", "Eco operation failed."),
        }
    return text, _json_safe(payload), is_error


def _json_safe(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value
