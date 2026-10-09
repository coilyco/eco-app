# Live dataset survey - per-type files

Point-in-time capture: Eco via Sirens, cycle 13 day 56 (2026-06-12). Action datasets have row-level CSV exporters at `/api/v1/exporter/actions?actionName=<name>`; series come from `/datasets/get` as daily samples. Format: `* name - rows (csv) / points with data - latest / peak`. Part of the pull-everything survey, [#7](https://forgejo.coilysiren.me/coilyco-gaming/eco-app/issues/7).

* Commerce & money (actions) - 15
* Civics & settlement (actions) - 14
* Social (actions) - 4
* Progression (actions) - 4
* Work & contracts (actions) - 11
* [Industry & world mutation (actions)](actions-industry.md) - 20
* [Economy (series)](series-economy.md) - 3
* Civics & people (series) - 10
* Progression (series) - 3
* [Climate & atmosphere (series)](series-climate.md) - 7
* Flora populations (series) - 68
* Fauna populations (series) - 26
* [World & misc (series)](series-world.md) - 7

Only four per-type detail pages were captured this cycle (industry, economy, climate, world); the rest are inventory-only until [#7](https://forgejo.coilysiren.me/coilyco-gaming/eco-app/issues/7) fills them in. Empty-this-cycle datasets are listed in the issue, not here, per review scope.

## How to probe (fresh-session bootstrap)

Everything below was derived live on 2026-06-12; it is the complete recipe for drilling into any dataset without re-deriving the mechanics.

* **Base URL** - resolve via [`scripts/resolve-eco-target.sh`](../../scripts/resolve-eco-target.sh): LAN mDNS `kai-server.local:3001` first (same-LAN tailnet is blackholed, infrastructure#294), then the SSM tailnet FQDN, then public `eco.coilysiren.me:3001`. `just http` wires all of this plus the keys into the dev loop automatically.
* **Auth** - admin endpoints take `X-API-Key`. The key is SSM `/eco-mcp-app/api-admin-token` (us-east-1), fetched via `aws ssm get-parameter --with-decryption`. Never echo or commit it.
* **Catalog** - `GET /datasets/flatlist` lists all datasets with `IsAction`, `Unit`, `StatType`, `Tags` metadata. 205 entries this cycle.
* **Series** - `GET /datasets/get?dataset=<Name>&dayStart=0&dayEnd=<day>` returns `{"Times": [...], "Values": [...], "Interval": 86400, "Unit": "..."}`. Times are **seconds since cycle start**, daily samples. Get the current day from `/info` `DaysRunning`.
* **Action rows** - `GET /api/v1/exporter/actions?actionName=<Name>` returns CSV. Parse header-keyed but defensively: some rows carry an undeclared extra tool column that shifts every later field - `crafting._corrected_index` absorbs it by scoring candidate insertion points against per-column value shapes ([#5](https://forgejo.coilysiren.me/coilyco-gaming/eco-app/issues/5)). `Citizen`/`Buyer`/`Seller`/`ShopOwner` are numeric in-game ids; join them to names via the jobs mod's `/api/v1/citizens` surface (the crafting atlas does this, falling back to `Citizen #<id>` when a name is missing). `Time` is seconds since cycle start (in-game day = `Time / 86400`, the species-CSV convention). Enums arrive undecoded (e.g. CurrencyTrade `BoughtOrSold` = 32/33); the trades ledger decodes these best-effort to buy/sell but reads the authoritative buyer/seller from the `Buyer`/`Seller` columns so a wrong decode never corrupts a row ([#6](https://forgejo.coilysiren.me/coilyco-gaming/eco-app/issues/6), consumed by `eco_mcp_app/trades.py`).
* **Names** - `GET /api/v1/users` returns `Name`/`PlayFabId`/`SteamId` for every settler, but not the numeric id the CSVs key on - that join is the #5 blocker.
* **Skills** - the jobs mod serves `GET /api/v1/skills` (same key), consumed by `eco_spec_tracker`.
* **Existing consumers to crib from** - `eco_mcp_app/crafting.py` (streamed CSV aggregation of 4 actions), `eco_mcp_app/trades.py` (row-level ledger from CurrencyTrade), `eco_mcp_app/progression.py` (skill-history trajectories from the 7 progression actions, [#64](https://forgejo.coilysiren.me/coilyco-gaming/eco-app/issues/64) - note its column-shape caveat: the progression exporter headers were not capturable live, so the skill/level column names are best-effort candidates keyed defensively off the header), `eco_mcp_app/climate.py` (`/datasets/get` series + flatlist discovery). The SPA consumes their `/preview/*.json` mirrors.
* **Beyond the exporter** - the Eco source is checked out at `~/projects/StrangeLoopGames/Eco` (access granted 2026-06-12) for finding data the server holds but does not export. Findings worth pulling get a Forgejo issue per the AGENTS.md pull-everything rule.
