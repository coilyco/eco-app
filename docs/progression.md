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
