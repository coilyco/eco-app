# Progression history

Who learned what, when, across the cycle.

## Surfaces

- **`get_progression`** - `src/eco_mcp_app/progression.py` plus `server.py` wiring. Returns markdown plus structured JSON.
- **MCP only.** The `/preview/progression.json` plane was deleted when the SPA stopped reading it (COI-2095).

## What it computes

- **Per-citizen trajectories** - a chronological event timeline plus professions gained, currently-held specialties, and levels.
- **Server-wide trends** - per-in-game-day counts of specialties gained, level-ups, professions, and classes.
- **Leaderboards** - most-gained specialties and professions, class completions, busiest levelers.

## Tech progression

`techProgression` on `get_progression` (`src/eco_mcp_app/tech_progression.py`, COI-2090) is the server's tech level. It replaced `get_milestones`.

- **`highestStage` and `highest`** - the top rung of the Basic 1 to Modern 4 ladder (`norms.STAGES`) among crafted items, Scholars variants folded onto the same rung. `crafted` lists every ladder item seen. Level 5 specialist modules are off the ladder.
- **Floor, not total** - the source is the crafting atlas's `by_crafted` board. Old crafts are merged into hourly per-citizen rollups with one item label (eco-app#131), so a rung crafted only inside a rollup is missed. The stage can read low, never high, and `upgradesNote` says so.
- **`specialties`** - every specialty with a `GainSpecialty` row, from uncapped events. `takenBy` is distinct citizens who ever gained it, `holders` those whose last gain or loss was a gain, `firstDay` the first gain.
- **Null never zero** - an unreadable exporter leaves `highestStage`, `highest`, `crafted`, `takenCount` or `taken` as `null` plus a warning. `0` means the exporter was read and found none.
- **Not here** - when each rung was first crafted, since the atlas keeps no per-item day.

## Messy bits handled

- **Numeric citizen ids** - joined to names through the jobs mod.
- **Column-shape uncertainty** - the progression exporters were unreachable from the build container, so columns were inferred. Treat the parser as best-effort until a live capture confirms it.
- **Time and misalignment** - seconds since cycle start, and the crafting realignment is reused. Streaming and caching match `civics`.
