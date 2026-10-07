"""Command service: data-plane mapping and exactly-one-embed interaction flow."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from .client import EcoAppClient, EcoAppError
from .embeds import EmbedFactory, EmbedField, EmbedPayload

LOG = logging.getLogger(__name__)


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _meteor_text(cycle: dict[str, Any]) -> str:
    """The meteor field, naming a destroyed or absent meteor instead of a null countdown."""
    meteor = _mapping(cycle.get("meteor"))
    if meteor.get("state") == "destroyed":
        return str(meteor.get("summary") or "Destroyed")
    if meteor.get("state") == "none":
        return "No meteor in this world"
    return f"{_reported(cycle.get('daysUntilMeteor'))} days remaining"


def _reported(value: Any) -> str:
    """Render an optional status number, naming the absent case.

    `/info` omits fields per server version, and eco-app now passes that
    through as null rather than zero (eco-app#214). An embed that printed
    "Day 0" for an unreported day would be confidently wrong.
    """
    return "unknown" if value is None else str(value)


@dataclass(frozen=True)
class CommandService:
    client: EcoAppClient
    embeds: EmbedFactory
    public_url: str
    info_channel_id: int

    def _url(self, path: str) -> str:
        return f"{self.public_url.rstrip('/')}{path}"

    async def render(self, command: str, name: str | None = None) -> EmbedPayload:
        try:
            if command == "status":
                return self._status(await self.client.status())
            if command == "world":
                return self._world(await self.client.world())
            if command == "economy":
                return self._economy(*(await self.client.economy()))
            if command == "player" and name:
                return self._player(name, await self.client.player(name))
            if command == "help":
                return self._help()
            return self._error(command)
        except (TimeoutError, EcoAppError):
            return self._error(command)
        except Exception:
            LOG.exception("discord_command_unexpected", extra={"command": command})
            return self._error(command)

    async def resolve_interaction(
        self, interaction: object, command: str, name: str | None = None
    ) -> None:
        """Redirect out-of-channel requests, otherwise defer before every fetch."""
        started = time.monotonic()
        channel_id = getattr(interaction, "channel_id", None)
        if channel_id != self.info_channel_id:
            await interaction.response.send_message(  # type: ignore[attr-defined]
                f"Use <#{self.info_channel_id}> for Eco rich previews.", ephemeral=True
            )
            LOG.info(
                "discord_command_redirected",
                extra={"command": command, "outcome": "redirect"},
            )
            return
        await interaction.response.defer()  # type: ignore[attr-defined]
        payload = await self.render(command, name)
        await interaction.edit_original_response(  # type: ignore[attr-defined]
            embed=self.embeds.to_discord(payload), content=None
        )
        LOG.info(
            "discord_command_complete",
            extra={
                "command": command,
                "outcome": payload.kind,
                "duration_ms": int((time.monotonic() - started) * 1000),
            },
        )

    def _status(self, data: dict[str, Any]) -> EmbedPayload:
        players = _mapping(data.get("players"))
        cycle = _mapping(data.get("cycle"))
        server = _mapping(data.get("server"))
        return self.embeds.success(
            title="Eco server status",
            url=self._url("/info"),
            description="Current Sirens server state.",
            fields=[
                EmbedField(
                    "Players online",
                    f"{_reported(players.get('online'))} / {_reported(players.get('total'))}",
                    True,
                ),
                EmbedField("World age", f"Day {_reported(cycle.get('daysRunning'))}", True),
                EmbedField("Meteor", _meteor_text(cycle), True),
                EmbedField("Version", str(server.get("version") or "Unavailable"), True),
            ],
        )

    def _world(self, data: dict[str, Any]) -> EmbedPayload:
        summary = str(
            data.get("narrative") or data.get("summary") or "World and climate data are live."
        )
        return self.embeds.success(title="Eco world", url=self._url("/map"), description=summary)

    def _economy(
        self, currency: dict[str, Any], logistics: dict[str, Any], market: dict[str, Any]
    ) -> EmbedPayload:
        money = _mapping(currency.get("money"))
        gaps = logistics.get("supplyGaps") or logistics.get("gaps") or []
        fields = [EmbedField("Active currencies", str(money.get("activeCurrencies", 0)), True)]
        if isinstance(gaps, list):
            fields.append(EmbedField("Supply gaps", str(len(gaps)), True))
        fields.append(EmbedField("Market items", str(len(market.get("items") or [])), True))
        return self.embeds.success(
            title="Eco economy",
            url=self._url("/trade"),
            description=str(
                currency.get("narrative") or "Current trade, currency, and supply highlights."
            ),
            fields=fields,
        )

    def _player(self, name: str, data: dict[str, Any]) -> EmbedPayload:
        encoded = quote(name.encode("utf-8").hex())
        if not data.get("found"):
            return self.embeds.empty(
                title="Eco player",
                url=self._url(f"/users/{encoded}"),
                description="No public dossier data is available for that citizen yet.",
            )
        section_keys = ("jobs", "trades", "crafting", "civics", "currency", "world")
        sections = [key for key in section_keys if data.get(key)]
        return self.embeds.success(
            title=f"Eco player: {name}",
            url=self._url(f"/users/{encoded}"),
            description="Public citizen dossier summary.",
            fields=[EmbedField("Available activity", ", ".join(sections) or "Limited", False)],
        )

    def _help(self) -> EmbedPayload:
        return self.embeds.success(
            title="Eco command help",
            url=self._url("/"),
            description=(
                "Use /eco rich status, world, economy, player, or help. "
                "Every command links to its full Eco page."
            ),
        )

    def _error(self, command: str) -> EmbedPayload:
        paths = {"status": "/info", "world": "/map", "economy": "/trade", "player": "/"}
        return self.embeds.error(
            title="Eco data temporarily unavailable",
            url=self._url(paths.get(command, "/")),
            description="Eco could not refresh this view. Try again shortly or open the live site.",
        )
