# Crafting atlas

What the server produces, from what, at which stations, and by whom.

## Surfaces

- **`get_crafting_atlas`** - `src/eco_mcp_app/crafting.py` plus `server.py`
  wiring. Returns markdown plus structured JSON.
- **`/crafting`** - `frontend/src/pages/Crafting.tsx`, consuming
  `/preview/get_crafting_atlas.json` through `lib/craftingApi.ts`.

## What it computes

- **Top items crafted** - `Count` summed by output item, `ItemCraftedAction`
  only, so this is a real unit count rather than an event count.
- **Top resources gathered** - `HarvestOrHunt`, `ChopTree`, and `DigOrMine`
  output, taking `Species` for harvests and chops and the block for mining.
- **Station utilization** - event count per `WorldObjectItem` (Campfire,
  Workbench, Carpentry Table), ranked.
- **Flows-into-what sankey** - edges from `WorldObjectItem` to output, with
  thickness as event count, so it is event-weighted.
- **Per-citizen leaderboard** - top producers by total production events across
  all four action types.
- **Per-miner leaderboard (`byMiner`)** - `DigOrMine` rows per player, one per
  dig or mine event, never the `Count` magnitude. It is the only mining board:
  `byCitizen` (events) and `byCitizenIterations` (craft iterations plus gather
  events) both total all four action types, so a crafter leads them without
  mining at all. `byMiner` is `null` when the `DigOrMine` exporter was not
  fetched and `[]` when it was fetched and empty. It sums to
  `perActionCounts.DigOrMine` less rows with no numeric `Citizen`. `limit`
  bounds it like the other boards, and `byMinerNote` leads the payload.
  The per-player ranking is `rank_citizen_counts`, which `get_world` shares.

## Messy bits handled

- **Numeric citizen ids** - `Citizen` is a numeric in-game user id, joined to
  display names through the jobs mod's `/api/v1/citizens`.
- **Misalignment risk** - some exporter rows carry an undeclared extra tool
  column, such as `HandsItem`, that shifts every later field. The parser
  detects and realigns rather than trusting position.
- **Item-name prettifying** - `prettify_eco_name` turns `CampfireItem` into
  Campfire and `BunWulfRawMeatItem` into Bun Wulf Raw Meat.

The atlas stream-parses through `_stream_csv_rows` with a batched fold, never
buffering the whole body, and caches per base URL and api-key hash.

The sankey renders as a layered flow rather than the issue's original chart
choice, because event-weighted edges read better layered.
