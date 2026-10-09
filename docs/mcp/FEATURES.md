# eco-mcp-app features

MCP server at `https://eco-mcp.coilysiren.me/mcp/`. Every tool returns markdown plus structured JSON, data only. `/mcp` and `/preview*` send CORS for exactly `https://coilyco.dev`.

## MCP tools

Input schemas in [server.py](../../src/eco_mcp_app/server.py) are authoritative.

- **Response size** - detail arrays are bounded and say what they dropped. `limit` (default 50) on `get_trades`, `get_currency`, `get_crafting_atlas`, `get_civics`, plus `get_server_status` `players.onlineNames` and `get_government` `settlements` and `titles` (COI-1649). `limit=0` returns all. Summaries cover every row.
- **Null is unmeasured** - a KPI is `null` when its dataset was unreadable and `0` only when the server reported no activity.
- **get_world** - Mutation timeline, top shapers, `byCitizenByCategory`, `worldGenerator` ([world.md](../world.md)).
- **get_server_status** / **get_climate** / **get_region** / **get_species** - Status, climate, ecoregion, species.
- **explain_item** - Wikidata and Wikipedia lookup.
- **get_crafting_atlas** - Top items, stations, leaderboards, `byMiner` ([crafting.md](../crafting.md)).
- **get_trades** - Detailed ledger, `item` filter, `ledgerFreshness` ([trades.md](../trades.md#freshness)).
- **get_market** - Price history and trend, `ledgerFreshness`, `commodityBenchmark` (FRED, `null` on failure).
- **get_stores** - Store directories from trade history only. Live shelves: `find_trade` with `store`.
- **find_trade** - Resale, arbitrage, supply gaps. Only stocked shelves rank `cheapest`, else `soldOutNote`. `store` returns a whole shelf.
- **price_by_stage** - Median trade price per upgrade stage, no fuzzy match ([price-history.md](../price-history.md#upgrade-shorthand)).
- **get_recipes** / **price_recipe** - Recipe graph slice, and a product costed live.
- **get_progression** - Profession and specialty history plus `techProgression` ([progression.md](../progression.md#tech-progression)). MCP only.
- **get_social** - Play and reputation graph. `ChatSent` is never fetched. Names hashed unless `ECO_SOCIAL_ALLOW_NAMES` and `reveal_names`. MCP only.
- **get_government** / **get_civics** - Civic org chart, election and turnout history ([civics.md](../civics.md)).
- **get_currency** - Minted and personal roster, money supply, holders ([currency.md](../datasets/currency.md)). MCP only.

## Host-file tools

`get_mods` and `worldGenerator` read a path from an env var and answer with an unavailable state when unset, never an empty list.

- **`ECO_MODS_DIR`** - the server's `Mods/`. `get_mods` lists names and `version` only.
- **`ECO_WORLD_GENERATOR_FILE`** - the file `Configs/WorldGenerator.eco`, never `Configs/`, which holds API tokens. Reported as `worldGenerator`.

## Runtime and sources

- **Surfaces** - stdio, stateless `POST /mcp/`, `/healthz`, `/preview/<tool>.json`.
- **Sources** - Eco `/info`, admin `/datasets/get` and `/exporter/*` (`ECO_ADMIN_API_KEY`), Wikidata, iNaturalist, FRED (`FRED_API_KEY`).
