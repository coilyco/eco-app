# Progression history

Who learned what, when, across the cycle.

## Surfaces

- **`get_progression`** - `src/eco_mcp_app/progression.py` plus `server.py`
  wiring. Returns markdown plus structured JSON.
- **MCP only.** The `/preview/progression.json` plane was deleted when the SPA
  stopped reading it (COI-2095). The tool keeps its MCP registration.

## What it computes

- **Per-citizen trajectories** - a chronological event timeline plus derived
  summaries: professions gained, currently-held specialties, and levels.
- **Server-wide trends** - per-in-game-day counts of each event kind, covering
  specialties gained, level-ups, professions, and classes.
- **Leaderboards** - most-gained specialties, most-gained professions, class
  completions, and busiest levelers.

## Tech progression

`techProgression` on `get_progression` (`src/eco_mcp_app/tech_progression.py`,
COI-2090) is the server's tech level. It replaced `get_milestones`, which read
a different culture figure on each row.

- **`highestStage` and `highest`** - the top rung of the Basic 1 to Modern 4
  ladder (`norms.STAGES`) that appears among crafted items, with Scholars
  variants folded onto the same rung. `crafted` lists every ladder item seen.
  Level 5 specialist modules are not on the ladder and are left out.
- **Floor, not total** - the source is the crafting atlas's `by_crafted` board.
  Old crafts are merged into hourly per-citizen rollups that keep one item
  label (eco-app#131), so a rung crafted only inside a rollup is missed. The
  stage can read low, never high, and `upgradesNote` says so.
- **`specialties`** - every specialty with a `GainSpecialty` row, from the
  uncapped events, so it does not stop at the 80 citizen cards. `takenBy` is
  distinct citizens who ever gained it, `holders` those whose last gain or
  loss was a gain, and `firstDay` the first gain.
- **Null never zero** - an unreadable exporter leaves `highestStage`,
  `highest`, `crafted`, `takenCount` or `taken` as `null` and adds a warning.
  `highestStage` is `0` and `takenCount` is `0` only when the exporter was read
  and found none. Gains with no recognised skill column are warned about.
- **Not here** - when each rung was first crafted. The atlas keeps no per-item
  day, so it would need a second fold of the craft log.

## Messy bits handled

- **Numeric citizen ids** - joined to names through the jobs mod's
  `/api/v1/citizens`.
- **Column-shape uncertainty** - the progression exporters were not reachable
  from the build container, so the column set was inferred rather than
  captured live. Treat the parser as best-effort until a live capture confirms
  it.
- **Time semantics** - integer seconds since cycle start, the same convention
  as the species population CSV.
- **Misalignment risk** - an undeclared extra column shifts later fields, and
  this reuses the crafting realignment.

Streaming and caching match `civics`: a stream-parse with a batched fold and a
per-key `TTLCache`, so a late-cycle log never buffers whole.
