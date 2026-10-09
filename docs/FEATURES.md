# eco-app features

Living inventory of what ships from this monorepo. Component detail lives in
the per-component docs below, carried over from the four source repos during
consolidation (coilysiren/inbox#101).

**The fused service.** One image, one uvicorn process, entrypoint
`eco_mcp_app.http_app:app`, port 4000.

- **MCP server** - `src/eco_mcp_app/`. Stdio for Claude Desktop,
  Streamable-HTTP at `/mcp/`. Twenty read-only operations register once through
  `DualRouteRegistry`. See [dual-route-inventory.md](dual-route-inventory.md).
- **Reply templates** - one-line answers carried on a tool's MCP `_meta` under
  `coilyco/templates`, so a router that already picked the tool can reply
  without an LLM, plus item and currency vocabularies as MCP resources so it
  can fill the arguments without one. See [dual-route-inventory.md](dual-route-inventory.md#reply-templates).
- **Historical price norms** - every tool and data route that lists an item
  price carries its norm from past cycles at the live upgrade stage, from
  `data/eco_trades_norms.json.gz`. See [price-history.md](price-history.md).
  Every web page shows it beside the price through one component, see
  [frontend/price-norms.md](frontend/price-norms.md).
- **Privileged `/admin` MCP** - `src/eco_mcp_app/admin/`, flagged off in the
  ordinary app. See [admin-mcp.md](admin-mcp.md).
- **React frontend** - `frontend/`, a Vite SPA the fused service serves at `/`,
  styled by the coilyco kit under an Eco green theme. See
  [frontend/kit-theme.md](frontend/kit-theme.md). The homepage is the server's
  player-facing brief over live status, see [frontend/homepage.md](frontend/homepage.md).
  Each item has one page holding its market, recipes, and uses, and old
  `/recipe` links redirect to the recipe's card there, see
  [frontend/item-pages.md](frontend/item-pages.md).
- **Kit browser checks in CI** - the `kit-check` job in `build-publish.yml`
  drives a Chromium installed in the job over every public route at 1000px and
  320px, and fails a pull request on axe, text under 16px, layering, phone
  overflow, or transfer weight. See [frontend/kit-checks.md](frontend/kit-checks.md).
- **Jobs API** at `/jobs/api` and **Replay API** at `/replay/api`. See
  [progression.md](progression.md).
- **Discord worker** - `src/eco_discord/`, a separate Pycord gateway process.
  See [discord-bot.md](discord-bot.md), [discord-parity.md](discord-parity.md).
- **Telemetry** - shared OTLP init in `eco_mcp_app/telemetry.py`, plus Sentry
  for crashes only when `SENTRY_DSN` is set. Handled errors stay in SigNoz.
- **Crawl surface** - `robots.txt`, `sitemap.xml`, `301`s for retired paths,
  `404` for unrouted ones, and a per-response index posture, all derived from
  the shared route manifest `data/spa_routes.json`. See
  [frontend/crawl-surface.md](frontend/crawl-surface.md).
- **Link-preview cards** - a 1200x630 card per route, drawn at build time from
  the same manifest words and seeded art, named by `og:image`. See
  [frontend/og-cards.md](frontend/og-cards.md).

**Data surfaces.**
[civics.md](civics.md), [cost.md](cost.md), [crafting.md](crafting.md),
[price-history.md](price-history.md), [recipes.md](recipes.md),
[modded-recipes.md](modded-recipes.md), [trades.md](trades.md),
[uses.md](uses.md), [world.md](world.md), [calculator.md](calculator.md),
and [spa-freshness.md](spa-freshness.md).

**Mods, build, and dev.**
In-game C# plugins live in `mods/` (jobs, replay, telemetry, stores), built
with the `build-mod-*` ward verbs. Packaging is
[mod-packages.md](mod-packages.md), the offline dev loop is
[snapshot-harness.md](snapshot-harness.md), and deploy lives in
`coilyco-bridge/deploy/services/eco-app`. `just join-watch` proves a real client
reached the world and stayed there, from the server side.

## See also

- [README.md](../README.md), [AGENTS.md](../AGENTS.md), and
  [justfile](../justfile). Cross-reference convention from
  coilysiren/agentic-os#59.
