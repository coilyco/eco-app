import asyncio

import httpx
import respx

from eco_discord.client import EcoAppClient
from eco_discord.commands import CommandService
from eco_discord.embeds import EmbedFactory, EmbedKind

BASE = "https://eco-app.test"


def service() -> CommandService:
    return CommandService(EcoAppClient(BASE), EmbedFactory("Sirens"), "https://eco.example", 42)


@respx.mock
async def test_status_maps_to_preview_and_info_url() -> None:
    route = respx.get(f"{BASE}/preview.json").mock(
        return_value=httpx.Response(
            200, json={"players": {"online": 2, "total": 9}, "cycle": {}, "server": {}}
        )
    )
    payload = await service().render("status")
    assert route.called
    assert payload.url == "https://eco.example/info"
    assert payload.kind is EmbedKind.SUCCESS


@respx.mock
async def test_economy_maps_to_public_data_planes_and_trade_url() -> None:
    for path, payload in (
        ("currency.json", {"money": {"activeCurrencies": 1}}),
        ("logistics.json", {"supplyGaps": []}),
        ("market.json", {"items": []}),
    ):
        respx.get(f"{BASE}/preview/{path}").mock(return_value=httpx.Response(200, json=payload))
    rendered = await service().render("economy")
    assert rendered.url == "https://eco.example/trade"
    assert rendered.kind is EmbedKind.SUCCESS


@respx.mock
async def test_timeout_resolves_error_embed_without_internal_detail() -> None:
    respx.get(f"{BASE}/preview/world.json").mock(
        side_effect=httpx.ConnectError("internal.host token")
    )
    payload = await service().render("world")
    assert payload.kind is EmbedKind.ERROR
    assert "internal.host" not in payload.description


class FakeResponse:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def defer(self) -> None:
        self.events.append("defer")

    async def send_message(self, content: str, *, ephemeral: bool) -> None:
        self.events.append("redirect")
        self.content = content
        self.ephemeral = ephemeral


class FakeInteraction:
    def __init__(self, channel_id: int = 42) -> None:
        self.events: list[str] = []
        self.response = FakeResponse(self.events)
        self.channel_id = channel_id

    async def edit_original_response(self, **kwargs: object) -> None:
        self.events.append("edit")
        assert kwargs["content"] is None
        assert kwargs["embed"] is not None


async def test_interaction_defers_before_upstream_and_edits_once(monkeypatch) -> None:
    interaction = FakeInteraction()
    command_service = service()

    async def fake_render(self, command: str, name: str | None = None):
        assert interaction.events == ["defer"]
        return command_service._help()

    monkeypatch.setattr(CommandService, "render", fake_render)
    monkeypatch.setattr(command_service.embeds, "to_discord", lambda payload: object())
    await command_service.resolve_interaction(interaction, "help")
    assert interaction.events == ["defer", "edit"]


async def test_interaction_outside_info_channel_redirects_without_fetch(monkeypatch) -> None:
    interaction = FakeInteraction(channel_id=99)
    command_service = service()

    async def should_not_render(*args: object, **kwargs: object) -> object:
        raise AssertionError("out-of-channel requests must not fetch Eco data")

    monkeypatch.setattr(CommandService, "render", should_not_render)
    await command_service.resolve_interaction(interaction, "world")
    assert interaction.events == ["redirect"]
    assert interaction.response.content == "Use <#42> for Eco rich previews."
    assert interaction.response.ephemeral is True


def test_help_needs_no_upstream() -> None:
    payload = asyncio.run(service().render("help"))
    assert payload.kind is EmbedKind.SUCCESS
    assert payload.url == "https://eco.example/"


@respx.mock
async def test_status_embed_names_a_destroyed_meteor() -> None:
    cycle = {
        "daysRunning": 70,
        "daysUntilMeteor": None,
        "meteor": {"state": "destroyed", "summary": "The meteor was destroyed on day 57."},
    }
    respx.get(f"{BASE}/preview.json").mock(
        return_value=httpx.Response(200, json={"players": {}, "cycle": cycle, "server": {}})
    )
    payload = await service().render("status")
    meteor = next(f for f in payload.fields if f.name == "Meteor")
    assert "destroyed on day 57" in meteor.value
    assert "unknown" not in meteor.value
