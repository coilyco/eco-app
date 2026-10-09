# eco-replay ("Kaihronicler")

Player-action recorder for an [Eco](https://play.eco/) server, surfaced read-only on the website. The mod is the source of truth, the Python API a thin re-server, the SPA the view.

1. **C# Eco mod** (`mods/replay/src/`) - implements `IGameActionAware`, receives each `GameAction`, appends one JSON object per line to `Storage/EcoReplay.jsonl`. No SQLite or native dependency.
2. **FastAPI JSON API** (`src/eco_replay/`) - reads the mod's `/api/v1/events` endpoint, the JSONL via `ECO_REPLAY_FILE`, or a mock fallback. Mounted by the fused service at `/replay/api`, rendered at `/replay`.

## Run and configure

```sh
just build-mod-replay
just http
just frontend-dev
```

Set `ECO_REPLAY_FILE` or `ECO_REPLAY_UPSTREAM_URL` (separate from the jobs `UPSTREAM_URL`). With neither, the API serves mock events and the SPA shows a banner. A source that rejects, times out, or returns malformed JSON gives a `503` with `error.code: replay_upstream_unavailable`.

## What gets recorded

Every `GameAction` through `ActionUtil.ActionPerformed`. Each row has `id` (monotonic, recovered after restart), `unixTime`, `gameTime`, `type`, `citizen`, and a best-effort bounded JSON `body`. `ItemCraftedAction` is the exception: each `WorkOrder.CompleteIteration` is one row with a fixed scalar `craft-iteration/v1` body (item, station, byproduct, position, `iterations: 1`).

## Storage and retention

`Storage/EcoReplay.jsonl` is append-only. A background writer owns a bounded 4,096-row channel and batches writes, so the game thread never does disk I/O, and a saturated queue sheds new rows. The newest 2,000,000 valid rows are kept, compacted by streaming into a temp sibling and atomically replacing the file. Queries stream and skip malformed rows and a partial final line. The legacy `Storage/EcoReplay.db` is never read or imported. Importing it is a separate operator-approved task.

## Endpoints

* Mod: `GET /api/v1/events` (`citizen`, `type`, `limit` up to 1,000, `since` in Unix seconds, exclusive `beforeId`) and `/api/v1/events/stats` (`{ ready, total }`).
* API: `GET /replay/api/v1/events` (`citizen`, `type`, `limit`), `/events/stats`, and `/meta` (`{ mockData }`).

## Validation

`just test`, `just build-mod-replay`, `just test-mod-replay`. Mod project: [`EcoReplay.csproj`](../../mods/replay/src/EcoReplay.csproj). Inventory: [docs/FEATURES.md](../FEATURES.md).
