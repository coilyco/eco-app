# eco-app features

Inventory of what ships from this monorepo. Component detail lives in the docs linked below.

- **Fused service** - one image, one uvicorn process, `eco_mcp_app.http_app:app`, port 4000.
- **MCP server** - `src/eco_mcp_app/`, stdio and Streamable-HTTP at `/mcp/`. Twenty read-only operations register once through `DualRouteRegistry` ([dual-route-inventory.md](dual-route-inventory.md), [mcp/FEATURES.md](mcp/FEATURES.md)).
- **Reply templates** - one-line answers on a tool's `_meta` under `coilyco/templates`, plus vocabulary resources, so a router answers without an LLM ([contract](dual-route-inventory.md#reply-templates)).
- **Historical price norms** - every listed item price carries its past-cycle norm at the live upgrade stage, from `data/eco_trades_norms.json.gz` ([price-history.md](price-history.md), [frontend/price-norms.md](frontend/price-norms.md)).
- **Mod inventory and world-generator metadata** - `get_mods` and `worldGenerator` on `get_world`, null with a warning when unmounted. They replace the retired `/admin` MCP (COI-763), see [host-file tools](mcp/FEATURES.md#host-file-tools).
- **React frontend** - `frontend/`, a Vite SPA served at `/`, coilyco kit under an Eco green theme ([kit-theme](frontend/kit-theme.md), [homepage](frontend/homepage.md), [item-pages](frontend/item-pages.md)).
- **Kit browser checks** - the `kit-check` job in `build-publish.yml` drives Chromium over every public route at 1000px and 320px ([kit-checks](frontend/kit-checks.md)).
- **Jobs API** at `/jobs/api` ([jobs/FEATURES.md](jobs/FEATURES.md)) and **Replay API** at `/replay/api` ([replay/README.md](replay/README.md)).
- **Telemetry** - OTLP init in `eco_mcp_app/telemetry.py`, plus Sentry for crashes only when `SENTRY_DSN` is set.
- **Crawl surface** - `robots.txt`, `sitemap.xml`, `301`s, `404`s and index posture from `data/spa_routes.json` ([crawl-surface](frontend/crawl-surface.md)).
- **Link-preview cards** - a 1200x630 card per route, drawn at build time ([og-cards](frontend/og-cards.md)).

**Data surfaces.** [civics](civics.md), [cost](cost.md), [crafting](crafting.md), [progression](progression.md), [recipes](recipes.md), [modded-recipes](modded-recipes.md), [trades](trades.md), [uses](uses.md), [world](world.md), [calculator](calculator.md), [spa-freshness](spa-freshness.md).

**Mods, build, dev.** In-game C# plugins live in `mods/`, built with the `build-mod-*` ward verbs ([mod-packages](mod-packages.md)). The offline dev loop is [snapshot-harness](snapshot-harness.md). Deploy lives in `coilyco-bridge/deploy/services/eco-app`. `just join-watch` proves a real client reached the world and stayed.

## See also

- [README.md](../README.md), [AGENTS.md](../AGENTS.md), [justfile](../justfile).
