# eco-mcp-app features

Baseline inventory of headline features. Use to evaluate scope changes.

## What this app is

MCP server exposing live data from Eco game servers. Production: `https://eco-mcp.coilysiren.me/mcp/`. Every tool returns markdown/text plus structured JSON; MCP Apps resources, widgets, and server-rendered cards were removed in [#113](https://forgejo.coilysiren.me/coilyco-gaming/eco-app/issues/113).

**Cross-origin.** `/mcp` and the read-only `/preview*` data plane send CORS for exactly `https://coilyco.dev`, so the coilyco.dev/dash/eco page can call them from a browser (`CoilycoDevCors`, teable:coilyco/eco-app#8362). Every other route and origin stays same-origin.

## MCP tools

Defined in [src/eco_mcp_app/server.py](../../src/eco_mcp_app/server.py) and the Wave 1 dual-route registry. Names are scoped to this MCP server and therefore omit a redundant Eco product prefix. Most accept an optional `server` argument, with each advertised input schema remaining authoritative. All return data-only results.

**Response-size contract.** Tools with an unbounded detail array bound it by default so a no-argument call stays inside an MCP client's response cap, and say what they dropped rather than truncating silently. `get_trades`, `get_currency`, `get_crafting_atlas` and `get_civics` take a `limit` (default 50); `get_stores` defaults to 5 rows with nested lists cut to 2, and `store`/`item` return whole matching rows (teable:coilyco/eco-app#8354); `get_species` thins its population curve to 120 evenly-spaced samples with the endpoints kept. `limit=0` returns everything and is what the SPA passes. Summary and aggregate fields always describe every row regardless ([#256](https://forgejo.coilysiren.me/coilyco-gaming/eco-app/issues/256), [#264](https://forgejo.coilysiren.me/coilyco-gaming/eco-app/issues/264)).

**Measured zero vs unmeasured.** A KPI reads `null` when the dataset behind it could not be read and `0` only when the server reported no activity. `get_economy` names the gap in `datasets_unavailable`; `get_civics` carries `adminAvailable` + `unavailableActions`. Neither derives a health verdict or a rate from a zero denominator ([#259](https://forgejo.coilysiren.me/coilyco-gaming/eco-app/issues/259), [#261](https://forgejo.coilysiren.me/coilyco-gaming/eco-app/issues/261)).

- **get_server_status** - Meteor countdown, players, world dims, cycle progress, version, economy summary.
- **get_economy** (disabled, teable:coilyco/eco-app#8345) - Trades/day, contract completion, loan defaults, wages, tax flow, government holdings (the same dataset `get_currency` reports), volatility sparklines. Admin `/datasets/get` ([#258](https://forgejo.coilysiren.me/coilyco-gaming/eco-app/issues/258)).
- **get_milestones** - Culture achievement tracker. Per-goal bars, server-wide culture.
- **get_species** - Species card. iNaturalist/Wikipedia taxonomy + in-game population chart, thinned to evenly-spaced samples for MCP callers ([#256](https://forgejo.coilysiren.me/coilyco-gaming/eco-app/issues/256)).
- **explain_item** - Wikidata + Wikipedia lookup. Images, category facts resolved to labels rather than raw entity ids, and canonical Eco item ids (`SteelAxeItem`) accepted alongside common names. 7-day cache ([#262](https://forgejo.coilysiren.me/coilyco-gaming/eco-app/issues/262)).
- **get_crafting_atlas** - Live crafting from action-log exporter. Top items, station util, leaderboard. `byMiner` ranks players by `DigOrMine` events alone, the board to read for the busiest miner, and is `null` when that exporter was not fetched. `byCitizen` and `byCitizenIterations` total all four action types and are not mining counts. Bounded by `limit` ([crafting.md](../crafting.md), COI-2049).
- **get_trades** - Detailed trade ledger with parties, items, stores, currencies, and price history. `item` filters the whole ledger, resolved like `price_by_stage`, before `limit`.
- **get_stores** - Store and trader directories derived from trade history.
- **get_progression** - Server-wide profession and specialty progression history. MCP only, no dedicated REST route (COI-2095).
- **get_social** - Community activity from the `Play`, `FirstLogin`, and `ReputationTransfer` action exporters: play volume, recent arrivals, and a who-reps-whom reputation graph. `ChatSent` is deliberately not fetched or returned ([#185](https://forgejo.coilysiren.me/coilyco-gaming/eco-app/issues/185)). Player names are hashed to stable handles by default. Names in the clear remain operator-gated (`ECO_SOCIAL_ALLOW_NAMES` + `reveal_names`) and never reach the public JSON path. MCP only, no dedicated REST route (COI-2095).
- **get_world** - World / industry activity from the action-log exporter. Construction, terraforming, roads, moved objects, explosions, garbage, and air pollution folded into a per-day mutation timeline by category, a top-world-shapers + top-polluters leaderboard, `byCitizenByCategory` (events per player within each category, keyed like `categories`, so `roads` names the top road builder, `players` is `null` when every action behind a category failed to fetch, each list bounded by `limit`, COI-2048), most-touched objects, and coarse-binned activity hotspots. No new mod, no restart - reuses the crafting atlas's streamed-CSV plumbing. Probe: [docs/world.md](../world.md).
- **get_market** - Per-item and per-currency price history, volume, and trend intelligence. With an `item` filter it also carries one optional `commodityBenchmark` field: the real-world FRED commodity series for copper, wheat, lumber, iron ore or crude, with its latest value and cadence-aware percent changes. The field is `null` for an item with no FRED mapping and `null` with a warning when the key, the fetch or the series fails, never zero, and is absent without an `item` filter. It replaced the retired `fair_price` tool (COI-2087).
- **find_trade** - Resale, arbitrage, and supply-gap decisions from history and live shelves. Only shelves with stock above 0 rank as `cheapest` or feed an arbitrage buy side. A market whose every seller is empty reports `cheapest: null` with a `soldOutNote`, never a 0 price or a zero-stock store as the pick (COI-2045).
- **price_by_stage** - One item word to one Eco item and its median trade price per upgrade stage, or a plain miss. No fuzzy match. Upgrade shorthand (`au3`, `sbu4`, `bu5`, `MU0`) is an item on its own and a stage qualifier beside another item, through the optional `stage` input or inside the phrase (`iron at au3`): the reply leads with that stage's row, says it is the market median and not the crafting cost, and says the item was never traded that early below its floor. A bare `bu5` lists the tier's specialist modules as `candidates`. `eco://vocab/stages` fills `stage` ([price-history.md](../price-history.md#upgrade-shorthand), COI-2107, teable:coilyco/eco-app#8423, #8425).
- **get_region** - WWF ecoregion classification. Donut, top-3 matches, boom/bust lists.
- **get_government** - Civic org chart. Elected titles, active elections, active laws (current-state snapshot from the live civic endpoints).
- **get_civics** - Civics & governance history + trend from the civic action exporters (`Vote`/`DidntVote`/`StartElection`/`WonElection`/`BecomeCitizen`/`SettlementFounded`/…) plus civics/people daily series: elections started + outcomes, voter turnout (cast vs abstained, participation rate, most-active-voter leaderboard), demographic movement (citizens gained/lost, residency moves), settlements founded + homesteads. Acting citizens resolved to names via the citizens surface (`Citizen #<id>` fallback). The website-and-MCP answer to DiscordLink's elections/votes/demographics displays, exceeding them with turnout + demographic trend over time; complements `get_government` (laws-in-effect aren't derivable from the action stream). Probe: [docs/civics.md](../civics.md).
- **get_climate** - CO2 ppm, sea-level + drift, ground pollution, avg temperature, NOAA Mauna Loa anchor, top polluters. Plus a pollution-machine-style explainer: CO2 sources & sinks breakdown (pollution/animals/plants, lifetime + per-day), the CO2-effects mechanic (warming + sea-level thresholds), and a plain-language "what to expect" narration. Tolerant to dataset-name drift.
- **get_currency** - Currency & money-supply surface, meets DiscordLink `Currencies` / `Currency <name>`. Roster split minted/backed vs personal/credit (each with issuance + trade activity), money-supply totals (player wealth + gov holdings) and 7d trade value. Optional `currency` arg gives the per-currency report, including the live top account holders (per-account balances from the `mods/stores` `/api/v1/currency-holdings` exporter, joined to citizen names; flagged unavailable rather than faked when that mod is not deployed - [#58](https://forgejo.coilysiren.me/coilyco-gaming/eco-app/issues/58)). Roster + issuance from the `CreateCurrency` / `MintCurrency` / `CurrencyTrade` action exporters, supply from `/datasets/get`; degrades to the public `/info` headline without an admin key. Probe: [docs/datasets/currency.md](../datasets/currency.md).
- **get_recipes** - Recipe graph slice. Filters accept ids or display names on product / skill / station, and a value matching no known key warns with near misses instead of returning a silent empty result. A filtered or truncated payload restricts its lookup maps to the recipes it returns ([#254](https://forgejo.coilysiren.me/coilyco-gaming/eco-app/issues/254), [#255](https://forgejo.coilysiren.me/coilyco-gaming/eco-app/issues/255)).
- **price_recipe** - Cost one product against live market prices. Returns costed recipes only, not the recipe-graph index ([#254](https://forgejo.coilysiren.me/coilyco-gaming/eco-app/issues/254)).

## Runtime surfaces

- **Stdio** - `python -m eco_mcp_app.__main__` for Claude Desktop.
- **HTTP** - MCP over Streamable-HTTP at `POST /mcp/`. Stateless.
- **Health probe** - `GET /healthz`.
- **Data plane** - `GET /preview.json`, `/preview-map.json`, `/preview/<tool>.json` return tool payloads as JSON for the SPA to consume. `get_currency` has no dedicated short path (COI-2095), and the generic adapter dispatches any tool by name. No HTML variant - the dev `/preview` card pages were removed.
- **Livereload WS** - Debug-mode hot reload.

## UI rendering

The React SPA (`frontend/`) is the product UI. The MCP service renders no HTML and registers no UI resources.

## External data sources

- **Eco public `/info`** - Default `http://eco.coilysiren.me:3001/info`, override `ECO_INFO_URL`.
- **Eco admin `/datasets/get`** - Economic time-series. `ECO_ADMIN_API_KEY`.
- **Eco admin `/exporter/*`** - Action logs (crafting, harvesting, mining), species, deeds. CSV stream-parsed.
- **Wikidata + Wikipedia** - Item taxonomy + images. 7-day TTL.
- **FRED** - Commodity benchmark on `get_market`. `FRED_API_KEY`, else SSM `/eco-mcp-app/fred-api-key`.
- **iNaturalist** - Species taxonomy + images. Wikipedia fallback.

## Bundled data assets

- **data/ecoregions.json** (~7KB) - WWF ecoregion defs. The former ecopedia/species blobs were dropped during consolidation - lookups go live.

## Source modules

- **server.py** - Core MCP server and tool handlers.
- **http_app.py** - Starlette ASGI + NormalizeMcpPath middleware.
- **crafting.py** / **map.py** / **ecoregion.py** / **species.py** / **commodity_benchmark.py** / **wikidata.py** / **telemetry.py** / **livereload.py**.

## Deployment

- **Docker image** - Alpine Python 3.13 + uv. `ghcr.io/coilysiren/eco-mcp-app/coilysiren-eco-mcp-app:latest`.
- **k8s manifest** - Namespace, Deployment, Service, Ingress, ExternalSecrets (GHCR pull-secret, FRED key, Eco admin token from SSM).
- **Tailscale + cert-manager** - Encrypted cluster access from GHA, auto TLS.
- **CI/CD** - GHA builds, pushes GHCR, deploys via kubectl over Tailscale. Trufflehog secret scan.
- **Public endpoint** - `https://eco-mcp.coilysiren.me/mcp/`. Local default port 4000.

## Dev tooling

- **dev verbs** in the [justfile](../../justfile), each delegating to Make.
  - `just smoke` - Stdio initialization, discovery, and representative tool calls.
  - `just http` - Local HTTP on 4000 with hot reload.
  - `just harness` - Browser dev harness on `:8765`.
  - `just install-desktop` - Auto-register in Claude Desktop config.
- **Pre-commit** - ruff + mypy.
- **Tests** - pytest, pytest-asyncio, respx.

## See also

- [README.md](../../README.md) - human-facing intro.
- [AGENTS.md](../../AGENTS.md) - agent-facing operating rules.
- [justfile](../../justfile) - dev verbs.

Cross-reference convention from [coilysiren/agentic-os#59](https://github.com/coilyco-flight-deck/agentic-os/issues/59).
