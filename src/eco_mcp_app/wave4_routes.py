"""Typed dual registration for `get_mods`, scavenged from the retired /admin MCP (COI-763)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .dual_routes import DualRouteRegistry
from .public_routes import LIMIT_DESCRIPTION, ToolInvoker, register_json_route

WAVE4_TOOL_NAMES = frozenset({"get_mods"})


class ModsInput(BaseModel):
    """Bound the mod list. There is no `server` argument: the tree is this app's own mount."""

    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=50, ge=0, description=LIMIT_DESCRIPTION)


def register_wave4_routes(registry: DualRouteRegistry, invoke: ToolInvoker) -> None:
    """Register the mod inventory once."""
    present = {name for name in WAVE4_TOOL_NAMES if registry.has_tool(name)}
    if present:
        return
    register_json_route(
        registry,
        invoke,
        name="get_mods",
        title="Eco - installed mods",
        description=(
            "List the mods installed on this app's Eco server: each mod's name, whether "
            "it ships in the base Mods tree or is operator-written under UserCode, and "
            "its manifest version when it has one. Answers 'which mods does the server "
            "run' and 'what version is a mod on'. Metadata only: no player names, no "
            "configuration, no mod files. When the mods tree is not mounted into this "
            "app the reply says so with available false and count null, which is not a "
            "count of zero mods. It reads this app's own server, so it takes no `server`."
        ),
        rest_path="/preview/get_mods.json",
        input_model=ModsInput,
    )


def mods_markdown(payload: dict[str, Any]) -> str:
    """The readable block that precedes the JSON block."""
    if not payload.get("available"):
        reason = "; ".join(str(w) for w in payload.get("warnings") or []) or "mods unavailable"
        return f"## Installed mods\n\nUnavailable. {reason}"
    lines = [f"## Installed mods ({payload['count']})", ""]
    for mod in payload.get("mods") or []:
        version = mod.get("version") or "no version in manifest"
        lines.append(f"- {mod['name']} ({mod['group']}) - {version}")
    return "\n".join(lines)
