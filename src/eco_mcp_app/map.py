"""Fetch + shape the browser-only world-map plane (`/preview-map.json`).

The SPA `/map` page stacks the live WorldPreview.gif (inlined as a data URI so
no external origin is needed under the page CSP), the optional pollution raster,
and one raster per biome for the hover-highlight. There is no MCP tool for this
plane: a raster is a picture, not a tool result. The property-deed path and the
`get_map` tool that read it were deleted (COI-2092).

The Eco server reports `{x, y, z}` dimensions where `y` is elevation (0-200)
and the world is `x` by `z` in the horizontal plane.
"""

from __future__ import annotations

import asyncio
import base64
import os
from typing import Any, cast

import httpx

from .ecoregion import BIOME_COLORS, BIOME_DISPLAY, BIOME_LAYERS

ECO_BASE_URL_DEFAULT = os.environ.get("ECO_MAP_BASE_URL", "http://eco.coilysiren.me:3001").rstrip(
    "/"
)

# The logical square the SPA lays hotspot rings over, whatever size the browser
# scales the <img> stack to (it maps world coords by renderSize / worldDim).
MAP_RENDER_SIZE = 512


def _world_base_url(server: str | None) -> str:
    if not server:
        return ECO_BASE_URL_DEFAULT
    s = server.strip()
    if not s:
        return ECO_BASE_URL_DEFAULT
    if "://" not in s:
        s = f"http://{s}"
    return s.rstrip("/")


async def _fetch_biome_raster(
    client: httpx.AsyncClient, base: str, layer: str
) -> tuple[str, bytes] | None:
    """Best-effort GET of one biome layer's raster (`/Layers/<Layer>.gif`).

    Eco exposes each world layer as its own raster. We use these for the SPA's
    biome hover-highlight (eco-app#82): hovering a biome name overlays its
    raster so you can see *where* that biome is. Individual rasters can be
    disabled (401/404) or the flaky preview server can time one out — any
    failure just drops that biome's highlight, never the whole map.
    """
    try:
        r = await client.get(f"{base}/Layers/{layer}.gif")
    except httpx.HTTPError:
        return None
    if r.status_code == 200 and r.content:
        return (layer, r.content)
    return None


async def fetch_map_bundle(server: str | None = None) -> dict[str, Any]:
    """Fetch the upstream payloads needed to render the map page.

    Returns a dict with:
      * `dimension`: `{x, y, z}` — raw.
      * `preview_gif`: `bytes` of the animated GIF.
      * `pollution_gif`: `bytes` or `None`. Eco serves world-layer rasters at
        ``/Layers/<Name>.gif``; the pollution layer isn't always exposed (the
        config can disable individual rasters), so 404 is normal — we just
        omit the overlay.
      * `biome_rasters`: `{LayerName: bytes}` — one per layer that came back.
      * `base_url`: the base URL used (for display).
    """
    base = _world_base_url(server)
    async with httpx.AsyncClient(timeout=10.0) as client:
        dim_r = await client.get(f"{base}/api/v1/map/dimension")
        dim_r.raise_for_status()
        gif_r = await client.get(f"{base}/Layers/WorldPreview.gif")
        gif_r.raise_for_status()
        # Pollution overlay — best-effort. A 404 means the server config
        # didn't enable the raster; the map still renders without it.
        pollution_gif: bytes | None = None
        try:
            pol_r = await client.get(f"{base}/Layers/Pollution.gif")
            if pol_r.status_code == 200 and pol_r.content:
                pollution_gif = pol_r.content
        except httpx.HTTPError:
            pollution_gif = None
        # Per-biome rasters for the hover-highlight — fetched concurrently and
        # best-effort so a slow/disabled layer never stalls the map.
        results = await asyncio.gather(
            *(_fetch_biome_raster(client, base, layer) for layer in BIOME_LAYERS)
        )
        biome_rasters = dict(r for r in results if r is not None)
    return {
        "dimension": dim_r.json(),
        "preview_gif": gif_r.content,
        "pollution_gif": pollution_gif,
        "biome_rasters": biome_rasters,
        "base_url": base,
    }


def gif_to_data_uri(gif_bytes: bytes) -> str:
    return f"data:image/gif;base64,{base64.b64encode(gif_bytes).decode()}"


def build_map_payload(bundle: dict[str, Any]) -> dict[str, Any]:
    """Shape the payload the SPA `/map` page consumes."""
    dim = bundle.get("dimension") or {}
    pollution_bytes = bundle.get("pollution_gif")
    pollution_data_uri = gif_to_data_uri(cast(bytes, pollution_bytes)) if pollution_bytes else None
    # Biome rasters (SPA hover-highlight) — one entry per layer that came back,
    # in the canonical BIOME_LAYERS order so the SPA legend and overlay agree.
    biome_rasters = bundle.get("biome_rasters") or {}
    biome_layers = [
        {
            "name": layer,
            "display": BIOME_DISPLAY.get(layer, layer),
            "color": BIOME_COLORS.get(layer, "#888888"),
            "dataUri": gif_to_data_uri(biome_rasters[layer]),
        }
        for layer in BIOME_LAYERS
        if layer in biome_rasters
    ]
    return {
        "view": "eco_map",
        "sourceUrl": bundle.get("base_url"),
        "worldDim": {"x": dim.get("x"), "y": dim.get("y"), "z": dim.get("z")},
        "renderSize": MAP_RENDER_SIZE,
        "gifDataUri": gif_to_data_uri(cast(bytes, bundle.get("preview_gif") or b"")),
        "pollutionDataUri": pollution_data_uri,
        "biomeLayers": biome_layers,
    }
