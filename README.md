[![Eco by Strange Loop Games](https://cdn.cloudflare.steamstatic.com/steam/apps/382310/header.jpg)](https://store.steampowered.com/app/382310/Eco/)

<sub>Banner: Steam header for Eco by [Strange Loop Games](https://strangeloopgames.com/). Used here for attribution; not my artwork.</sub>

# eco-app

The application monorepo for the "Eco via Sirens" game server's companion services, consolidating four former repos (eco-mcp-app, eco-jobs-tracker, eco-replay, eco-telemetry) into one deployable. See [coilysiren/inbox#101](https://forgejo.coilysiren.me/coilysiren/inbox/issues/101) for the merge rationale.

One fused service ships from this repo. `eco_mcp_app` is the core: an MCP server (Claude Desktop over stdio, hosts over Streamable-HTTP at `/mcp/`) plus a browser-facing preview UI for its tool cards. The jobs JSON API (`eco_spec_tracker`, row-shaping over the in-game skills mod) mounts inside it at `/jobs/api`; the jobs UI is the SPA's `/jobs` page. `eco_replay` is a small FastAPI browser for the replay mod's SQLite event log, runnable locally and not yet wired into the fused service.

`frontend/` is the browser face: a Vite + React + TypeScript SPA the fused service serves at `/`, headed for `eco-app.coilysiren.me`. Today it is a placeholder landing page; the real design lands through rapid iteration.

The in-game halves live under `mods/` as C# Eco server plugins: `mods/jobs` (skills API the tracker consumes), `mods/replay` (player-action event store), and `mods/telemetry` (logs, metrics, and exception capture). Gameplay mods live in the sibling [eco-mods](https://forgejo.coilysiren.me/coilyco-gaming/eco-mods) repo, not here.

## Commands

Dev commands are declared in [`.ward/ward.yaml`](.ward/ward.yaml). Run them as `ward exec <verb>`.

## See also

- [AGENTS.md](AGENTS.md) - agent-facing operating rules.
- [docs/FEATURES.md](docs/FEATURES.md) - inventory of what ships today.
- [docs/discord-bot.md](docs/discord-bot.md) - implementation specification for rich Discord slash-command embeds.
- [.ward/ward.yaml](.ward/ward.yaml) - allowlisted commands. Agents route through ward, not bare `make` / `uv` / `python` / `dotnet`.

Cross-reference convention from [coilysiren/agentic-os#59](https://github.com/coilyco-flight-deck/agentic-os/issues/59).
