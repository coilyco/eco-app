"""Unit tests for the browser-only world-map plane (`/preview-map.json`).

The plane is the raster stack the SPA `/map` page reads: the world preview,
the optional pollution raster, and per-biome highlight rasters. The `get_map`
tool and its property-deed path are gone (COI-2092), so the last tests here pin
that they stay gone.
"""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import mcp.types as mt
import pytest
import respx
from fastapi.testclient import TestClient

from eco_mcp_app import map as eco_map
from eco_mcp_app.ecoregion import BIOME_LAYERS
from eco_mcp_app.http_app import create_app
from eco_mcp_app.map import ECO_BASE_URL_DEFAULT, build_map_payload
from eco_mcp_app.server import build_server

# Minimal 1x1 transparent GIF — enough bytes for the data-uri test to pass
# without pulling in real Eco map art.
_TINY_GIF = bytes.fromhex(
    "47494638396101000100800000000000ffffff21f90401000000002c000000000100010000020144003b"
)


def _fake_dimension() -> dict[str, int]:
    return {"x": 720, "y": 200, "z": 720}


def _mock_upstream(
    base: str = ECO_BASE_URL_DEFAULT,
    *,
    pollution: bool = True,
    served_biomes: frozenset[str] = frozenset(),
) -> None:
    """Stub the upstream rasters. Biome layers not in `served_biomes` answer 401."""
    respx.get(f"{base}/api/v1/map/dimension").mock(
        return_value=httpx.Response(200, json=_fake_dimension())
    )
    respx.get(f"{base}/Layers/WorldPreview.gif").mock(
        return_value=httpx.Response(200, content=_TINY_GIF)
    )
    respx.get(f"{base}/Layers/Pollution.gif").mock(
        return_value=httpx.Response(200, content=_TINY_GIF) if pollution else httpx.Response(404)
    )
    for layer in BIOME_LAYERS:
        response = (
            httpx.Response(200, content=_TINY_GIF)
            if layer in served_biomes
            else httpx.Response(401)
        )
        respx.get(f"{base}/Layers/{layer}.gif").mock(return_value=response)


# ---- payload shaping ------------------------------------------------------


def test_build_map_payload_is_the_raster_plane_only() -> None:
    bundle = {
        "dimension": _fake_dimension(),
        "preview_gif": _TINY_GIF,
        "base_url": "http://eco.example.com:3001",
    }
    payload = build_map_payload(bundle)
    assert set(payload) == {
        "view",
        "sourceUrl",
        "worldDim",
        "renderSize",
        "gifDataUri",
        "pollutionDataUri",
        "biomeLayers",
    }
    assert payload["view"] == "eco_map"
    assert payload["sourceUrl"] == "http://eco.example.com:3001"
    assert payload["worldDim"] == {"x": 720, "y": 200, "z": 720}
    assert payload["renderSize"] == eco_map.MAP_RENDER_SIZE
    # GIF bytes round-trip into a data URI.
    assert payload["gifDataUri"].startswith("data:image/gif;base64,")
    assert payload["pollutionDataUri"] is None
    assert payload["biomeLayers"] == []


# ---- upstream fetch -------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_fetch_map_bundle_never_asks_for_property_deeds() -> None:
    # The deed endpoint is deliberately unmocked: respx fails the test on any
    # request it has no route for, so a stray property fetch cannot pass.
    _mock_upstream()
    bundle = await eco_map.fetch_map_bundle()
    assert "property" not in bundle
    assert bundle["dimension"]["x"] == 720
    assert bundle["preview_gif"] == _TINY_GIF
    assert bundle["pollution_gif"] == _TINY_GIF
    assert bundle["base_url"] == ECO_BASE_URL_DEFAULT


@pytest.mark.asyncio
@respx.mock
async def test_fetch_map_bundle_omits_pollution_on_404() -> None:
    # Pollution raster disabled in server config — 404 is normal, overlay omitted.
    _mock_upstream(pollution=False)
    bundle = await eco_map.fetch_map_bundle()
    assert bundle["pollution_gif"] is None
    assert build_map_payload(bundle)["pollutionDataUri"] is None


@pytest.mark.asyncio
@respx.mock
async def test_fetch_map_bundle_biome_rasters_best_effort() -> None:
    """Per-biome rasters are fetched; disabled/failing layers drop out."""
    # Two biome rasters served, the rest 401 (disabled) — must not fail the map.
    served = frozenset({"OceanBiome", "GrasslandBiome"})
    _mock_upstream(pollution=False, served_biomes=served)

    bundle = await eco_map.fetch_map_bundle()
    assert set(bundle["biome_rasters"]) == served

    payload = build_map_payload(bundle)
    names = [b["name"] for b in payload["biomeLayers"]]
    assert names == [layer for layer in BIOME_LAYERS if layer in served]
    for b in payload["biomeLayers"]:
        assert b["dataUri"].startswith("data:image/gif;base64,")
        assert b["display"] and b["color"]


@pytest.mark.asyncio
@respx.mock
async def test_fetch_map_bundle_respects_server_arg() -> None:
    base = "http://eco.example.com:5679"
    _mock_upstream(base, pollution=False)
    bundle = await eco_map.fetch_map_bundle("eco.example.com:5679")
    assert bundle["base_url"] == base


@pytest.mark.asyncio
@respx.mock
async def test_fetch_map_bundle_raises_on_5xx() -> None:
    respx.get(f"{ECO_BASE_URL_DEFAULT}/api/v1/map/dimension").mock(return_value=httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        await eco_map.fetch_map_bundle()


# ---- the SPA's data plane -------------------------------------------------


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as c:
        yield c


@respx.mock
def test_preview_map_json_serves_the_raster_stack(client: TestClient) -> None:
    """What the /map page reads: preview, pollution and biome rasters, no deeds."""
    _mock_upstream(served_biomes=frozenset({"OceanBiome"}))
    r = client.get("/preview-map.json")
    assert r.status_code == 200
    body = r.json()
    assert body["view"] == "eco_map"
    assert body["gifDataUri"].startswith("data:image/gif;base64,")
    assert body["pollutionDataUri"].startswith("data:image/gif;base64,")
    assert [b["name"] for b in body["biomeLayers"]] == ["OceanBiome"]
    assert body["worldDim"] == {"x": 720, "y": 200, "z": 720}
    for gone in ("deeds", "deedCount", "polygons", "owners", "ownerStyles", "seamNote"):
        assert gone not in body


@respx.mock
def test_preview_map_json_is_a_502_when_the_upstream_is_down(client: TestClient) -> None:
    respx.get(f"{ECO_BASE_URL_DEFAULT}/api/v1/map/dimension").mock(
        side_effect=httpx.ConnectError("refused")
    )
    assert client.get("/preview-map.json").status_code == 502


# ---- the deleted tool stays deleted ---------------------------------------


@pytest.mark.asyncio
async def test_get_map_is_not_a_tool() -> None:
    mcp = build_server()
    handler = mcp.request_handlers[mt.ListToolsRequest]
    result = await handler(mt.ListToolsRequest(method="tools/list"))
    assert "get_map" not in {tool.name for tool in result.root.tools}


def test_get_map_has_no_rest_route(client: TestClient) -> None:
    r = client.get("/preview/get_map.json")
    assert r.status_code == 400
    assert r.json() == {"error": "Unknown tool: get_map"}
