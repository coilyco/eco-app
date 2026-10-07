# World and environment

Map, climate, biome, and species state.

## Map and environmental state

`get_world` covers terrain and world totals.
`/preview-map.json` is a browser-only projection of the world preview, pollution
and biome rasters for the SPA `/map` page. It has no MCP form: a raster is a
picture, not a tool result. Property deeds were dropped from it with the
`get_map` tool (COI-2092).

## Climate source freshness

Climate values carry the age of the observation the game server made, not just
the age of the fetch. A climate number without its observation age reads as
current when it may be an hour old. See [spa-freshness.md](spa-freshness.md).

## Biome and ecoregion evidence

`get_region` is the ecoregion classifier. Its answer carries the evidence it
classified from, so a surprising classification can be checked rather than
merely disbelieved.

## At-risk species

Species population series drive an at-risk read. The threshold is a stated
bound rather than a judgement, so the surface reports the number and the bound
and lets the reader draw the conclusion.

## World activity backend

World activity reads the same action-row exporter the crafting and trades
surfaces use, with the same realignment for undeclared extra columns and the
same numeric-id to name join.

Results cache in an in-process `TTLCache` keyed per base URL, matching the
other data surfaces.

See also: [civics.md](civics.md), [crafting.md](crafting.md).
