# Currency & money-supply datasets - probe findings

Probe: Eco via Sirens, **cycle 14 day 1** (2026-07-05), Eco `0.13.0.4 beta release-1024`. Drives `get_currency` ([#53](https://forgejo.coilysiren.me/coilyco-gaming/eco-app/issues/53), under epic [#37](https://forgejo.coilysiren.me/coilyco-gaming/eco-app/issues/37)). Recipe: [README.md](README.md). This page records the reachable-vs-deferred line for currency data.

## Where the data is

`/info` carries no currency listing on 0.13.0.4, only `EconomyDesc` (`"417 trades, 0 contracts"`). The real surface is the dataset catalog. `GET /datasets/flatlist` and `/info` are public. Series (`/datasets/get`) and actions (`/api/v1/exporter/actions`, CSV) need the admin `X-API-Key`. Nine currency datasets:

* `ActiveCurrencies` - series - number of live currencies.
* `TradesInLast7Days` - series - rolling currency **value** traded over 7 days, not a count.
* `PersonalWealthInDefaultCurrency` - series - player-held money supply.
* `GovernmentHoldingsInDefaultCurrency` - series - government-held money supply.
* `CurrencyTrade` - action - buyer, seller, shop-owner ids, `BoughtOrSold` (32/33, [#6](https://forgejo.coilysiren.me/coilyco-gaming/eco-app/issues/6)), amount, currency.
* `MintCurrency` - action - minting events. A currency that mints is **backed/minted**, and the summed amount is its issuance.
* `CreateCurrency` - action - the full roster plus founder.
* `TransferMoney` - action - transfers.
* `BarterTrade` - action - itemless barter, no currency leg.

The probe ran without a token (exporter `401`), so columns parse defensively by candidate name, as in `crafting.py` and `climate.py`. Without a token the tool degrades to the public headline.

## Built into `get_currency`

* **Roster and type** - `CreateCurrency` union `MintCurrency`. In `MintCurrency` is minted/backed, otherwise personal/credit.
* **Issuance** - summed `MintCurrency` amount per currency.
* **Trade count and volume** - from `CurrencyTrade`, when the currency column exists.
* **Money supply** - latest personal wealth plus government holdings, with `ActiveCurrencies` and `TradesInLast7Days` as circulation signals.
* **Top holders** - served by the stores mod's `GET /api/v1/currency-holdings` ([#58](https://forgejo.coilysiren.me/coilyco-gaming/eco-app/issues/58)), folded in best-effort. Without the DLL the report says `holders unavailable`.

## Why the mod, not an export

No export carries per-account balances. `PersonalWealthInDefaultCurrency` is one aggregate for the default currency, `CurrencyTrade` gives flows, and its ids hit the [#5](https://forgejo.coilysiren.me/coilyco-gaming/eco-app/issues/5) id-to-name blocker. Only the live `CurrencyManager` holds per-account, per-currency balances, so the mod reads it in process and joins owners to names via `UserManager`. Contract: [mods/stores/docs/currency-holdings.md](../../mods/stores/docs/currency-holdings.md).
