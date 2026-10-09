# Crafting atlas

What the server produces, from what, at which stations, and by whom.

## Surfaces

- **`get_crafting_atlas`** - `src/eco_mcp_app/crafting.py` plus `server.py` wiring. Returns markdown plus structured JSON.
- **`/crafting`** - `frontend/src/pages/Crafting.tsx`, consuming `/preview/get_crafting_atlas.json` through `lib/craftingApi.ts`.

## What it computes

- **Top items crafted** - `Count` summed by output item, `ItemCraftedAction` only, so a real unit count.
- **Top resources gathered** - `HarvestOrHunt`, `ChopTree`, `DigOrMine` output, by `Species` for harvests and chops and by block for mining.
- **Station utilization** - event count per `WorldObjectItem`, ranked.
- **Flows-into-what sankey** - `WorldObjectItem` to output edges, event-weighted, rendered layered because that reads better.
- **Per-citizen leaderboard** - production events across all four action types.
- **Per-miner leaderboard (`byMiner`)** - one `DigOrMine` row per player per event, never the `Count` magnitude. It is the only mining board, since `byCitizen` and `byCitizenIterations` total all four action types. `null` when the `DigOrMine` exporter was not fetched, `[]` when fetched and empty. `limit` bounds it and `byMinerNote` leads the payload. Ranking is `rank_citizen_counts`, shared with `get_world`.

## Messy bits handled

- **Numeric citizen ids** - joined to display names through the jobs mod's `/api/v1/citizens`.
- **Misalignment risk** - some exporter rows carry an undeclared extra tool column such as `HandsItem` that shifts later fields. The parser detects and realigns rather than trusting position.
- **Item-name prettifying** - `prettify_eco_name` turns `CampfireItem` into Campfire.

The atlas stream-parses through `_stream_csv_rows` with a batched fold and caches per base URL and api-key hash.
