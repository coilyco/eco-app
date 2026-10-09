# Currency & money-supply datasets - probe findings

Probe capture: Eco via Sirens, **cycle 14 day 1** (2026-07-05), server
`eco.coilysiren.me:3001`, Eco `0.13.0.4 beta release-1024`. Drives the
`get_currency` tool ([#53](https://forgejo.coilysiren.me/coilyco-gaming/eco-app/issues/53),
under the DiscordLink-replacement epic [#37](https://forgejo.coilysiren.me/coilyco-gaming/eco-app/issues/37)).
Recipe is the shared one in [README.md](README.md); this page records the
**reachable-vs-deferred** line for currency data so the tool's scope is
documented before it was finalized.

## Where the currency data actually is

The issue's no-reset spine assumed `/info` carries a currency listing. **It
does not** on 0.13.0.4 - `/info` exposes only `EconomyDesc` (`"417 trades, 0
contracts"`, a headline count) and no per-currency structure. The real surface
is the admin dataset catalog. `GET /datasets/flatlist` is **public** (no
`X-API-Key`) and names nine currency-relevant datasets:

* `ActiveCurrencies` - series (Count) - number of live currencies. Circulation signal.
* `TradesInLast7Days` - series (CurrencyAmount) - rolling **currency value** traded in the trailing 7 days (cycle-13 peak was ~1.8M). Despite the name it is a value, not a count.
* `PersonalWealthInDefaultCurrency` - series (CurrencyAmount) - aggregate player-held money supply.
* `GovernmentHoldingsInDefaultCurrency` - series (CurrencyAmount) - aggregate government-held money supply.
* `CurrencyTrade` - action (EventValue) - per-trade rows: buyer/seller/shop-owner ids, `BoughtOrSold` enum (32/33, [#6](https://forgejo.coilysiren.me/coilyco-gaming/eco-app/issues/6)), amount, and the currency traded.
* `MintCurrency` - action - minting events. A currency that mints is **backed/minted**; the summed amount is its issuance.
* `CreateCurrency` - action - currency-creation events. The full roster of currencies plus their founder.
* `TransferMoney` - action - money transfers.
* `BarterTrade` - action - itemless barter (no currency leg).

The four series come from `GET /datasets/get?dataset=<Name>&dayStart=0&dayEnd=<day>`
and the actions from `GET /api/v1/exporter/actions?actionName=<Name>` (CSV).
Both require the admin `X-API-Key`; only `/datasets/flatlist` and `/info` are
public. This probe ran without a token in the build container, so the exporter
returned `401` - column shapes are parsed defensively by candidate name (same
approach `crafting.py` and `climate.py` already use), and the tool degrades to
the public headline when the token is absent, exactly like the sibling
climate tool.

## Reachable now (built into `get_currency`)

* **Currency roster + type** - union of `CreateCurrency` (all currencies) and `MintCurrency` (the minted ones). A currency present in `MintCurrency` is classed **minted/backed**; otherwise **personal/credit**, matching the split `Currencies` shows.
* **Per-currency issuance** - summed `MintCurrency` amount per currency (the backing/minted-supply signal).
* **Per-currency trade count + volume** - aggregated from `CurrencyTrade` rows, when the exporter carries the currency column.
* **Money supply** - `PersonalWealthInDefaultCurrency` + `GovernmentHoldingsInDefaultCurrency` latest values, plus `ActiveCurrencies` and `TradesInLast7Days` as circulation signals.
* **Top holders / per-account balances** - the DiscordLink `Currency <name>` top-holders list. No *export* surface carries per-account balances (see below), so this is served by the stores/economy exporter mod's `GET /api/v1/currency-holdings` ([#58](https://forgejo.coilysiren.me/coilyco-gaming/eco-app/issues/58)), which reads them live from the in-process `CurrencyManager` and joins account owners to names via `UserManager` - sidestepping the [#5](https://forgejo.coilysiren.me/coilyco-gaming/eco-app/issues/5) id-to-name join blocker at the source. The per-currency report folds it in best-effort: reachable when the mod DLL is deployed, and a `holders unavailable` note otherwise (the DLL lands at the next server restart, out of band).

## Why the mod, not the export surface

The top-holders list was originally filed as deferred here because **no export
surface carries per-account currency balances**. `PersonalWealthInDefaultCurrency`
is a single aggregate series (default currency only), and `CurrencyTrade` rows
give flows, not balances - and the buyer/seller ids still hit the
[#5](https://forgejo.coilysiren.me/coilyco-gaming/eco-app/issues/5) id-to-name
join blocker. The reachable path is therefore an **in-process mod**
([#58](https://forgejo.coilysiren.me/coilyco-gaming/eco-app/issues/58), built in
`mods/stores`), not a dataset - the only place per-account, per-currency
holdings exist is the live `CurrencyManager`. Contract:
[mods/stores/docs/currency-holdings.md](../../mods/stores/docs/currency-holdings.md).
