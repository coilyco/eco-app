# Live dataset survey - per-type files

Point-in-time capture: Eco via Sirens, cycle 13 day 56 (2026-06-12). Action datasets have CSV exporters at `/api/v1/exporter/actions?actionName=<name>`, series come from `/datasets/get` as daily samples. Part of the pull-everything survey, [#7](https://forgejo.coilysiren.me/coilyco-gaming/eco-app/issues/7). Format: `name - rows / points - latest / peak`.

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

Only four detail pages exist (industry, economy, climate, world). The rest are inventory-only until #7 fills them in.

## How to probe

* **Base URL** - [`scripts/resolve-eco-target.sh`](../../scripts/resolve-eco-target.sh) tries LAN mDNS `kai-server.local:3001`, then the SSM tailnet FQDN, then public `eco.coilysiren.me:3001`. `just http` wires it and the keys.
* **Auth** - admin endpoints take `X-API-Key` from SSM `/eco-mcp-app/api-admin-token` (us-east-1). Never echo or commit it.
* **Catalog** - `GET /datasets/flatlist` lists datasets with `IsAction`, `Unit`, `StatType`, `Tags` (205 this cycle).
* **Series** - `GET /datasets/get?dataset=<Name>&dayStart=0&dayEnd=<day>` returns `{"Times", "Values", "Interval": 86400, "Unit"}`. Times are seconds since cycle start. The day is `/info` `DaysRunning`.
* **Action rows** - CSV. Parse header-keyed but defensively: an undeclared extra tool column shifts later fields, and `crafting._corrected_index` absorbs it ([#5](https://forgejo.coilysiren.me/coilyco-gaming/eco-app/issues/5)). `Citizen`, `Buyer`, `Seller`, `ShopOwner` are numeric ids, joined to names via the jobs mod's `/api/v1/citizens` (fallback `Citizen #<id>`). `Time` is seconds since cycle start (day is `Time / 86400`). Enums arrive undecoded (`BoughtOrSold` is 32/33), so the ledger reads buyer and seller from their columns ([#6](https://forgejo.coilysiren.me/coilyco-gaming/eco-app/issues/6)).
* **Names** - `GET /api/v1/users` returns `Name`, `PlayFabId`, `SteamId` but not the numeric id the CSVs use.
* **Skills** - the jobs mod serves `GET /api/v1/skills`, consumed by `eco_spec_tracker`.
* **Consumers to crib from** - `eco_mcp_app/crafting.py`, `trades.py`, `progression.py` (progression column names are best-effort, headers were not capturable live, [#64](https://forgejo.coilysiren.me/coilyco-gaming/eco-app/issues/64)), `climate.py`.
* **Beyond the exporter** - the Eco source is at `~/projects/StrangeLoopGames/Eco` for data the server holds but does not export. Worthwhile finds get an issue per the AGENTS.md pull-everything rule.
