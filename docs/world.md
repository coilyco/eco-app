# World and environment

Map, climate, biome, and species state.

## Map and environmental state

`get_world` covers terrain and world totals. `/preview-map.json` is a browser-only projection of the world preview, pollution and biome rasters for the SPA `/map` page, with no MCP form. Property deeds were dropped from it with the `get_map` tool (COI-2092).

## Climate source freshness

Climate values carry the age of the game server's observation, not just the age of the fetch. See [spa-freshness.md](spa-freshness.md).

## Biome and ecoregion evidence

`get_region` is the ecoregion classifier. Its answer carries the evidence it classified from, so a surprising classification can be checked.

## At-risk species

Species population series drive an at-risk read. The surface reports the number and the stated threshold and leaves the conclusion to the reader.

## World activity backend

World activity reads the same action-row exporter as the crafting and trades surfaces, with the same column realignment and numeric-id to name join.

`byCitizenByCategory` splits the rows by player within each category, keyed like `categories` (COI-2048). Each group is `{key, label, events, players}` and each `players` pair is `[name, events]`, one event per exporter row. The first `roads` pair is the top road builder, the first `extraction` pair the top digger or chopper. `players` is `null` when every action behind the category failed to fetch, and a category fetched with no rows is absent. `limit` bounds every group's `players` and warns per group as `byCitizenByCategory.<key>.players`.

Results cache in an in-process `TTLCache` keyed per base URL.

See also: [civics.md](civics.md), [crafting.md](crafting.md).
