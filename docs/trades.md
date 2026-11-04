# Trades ledger

Who bought and sold what, for how much, over the cycle.

## Surfaces

- **`get_trades`** - `src/eco_mcp_app/trades.py` plus `server.py` wiring. Returns markdown plus structured JSON.
- **The ledger in `/trade`** - `frontend/src/pages/Trade.tsx`, consuming `/preview/get_trades.json`.

Older history arrives as rollups rather than rows, so some values below are detailed-rows-only.

## What it computes

- **Detailed trades** - newest first, capped at `ECO_TRADES_LEDGER_ROWS` (default 4000).
- **Item filter** - `item` resolves through `Norms.find_qualified`, as in `price_by_stage`, and filters every trade before `limit`. It drops a stage qualifier. A resolved item rebuilds the summary arrays over its trades, so `topBuyers` is that item's. An unresolved word returns no rows and an `itemFilter` block with candidates, and the arrays stay whole.
- **Top buyers and sellers** - currency spent by `Buyer` and earned by `Seller`, detailed rows only.
- **Per-currency volume** - includes summed rollup amounts. Most-traded items use detailed rows only.
- **Price over time** - `CurrencyAmount / NumberOfItems` averaged per in-game day, for the busiest detailed items.

## Store and window

- **`window`** - on every answer: `label` (quote it), `startDay`, `endDay`, `firstTradeDay`, `lastTradeDay`, `requested`. Server days. With no window it reads "all detailed trades on record". `get_stores` carries the same block.
- **`days`** counts back from the newest trade on record. `since_day` and `until_day` bound it in server days. `days` with `since_day` is refused.
- **`store`** - a trade row names no store, only `ActionLocation` and the owner. The live store list (`/api/v1/stores`) ties a given name to a place, and a name a word or a letter off resolves as in [find_trade](dual-route-inventory.md) (`storeFilter.resolvedFrom`, `suggestions`). Without that list only an owner's handle matches.
- **A selection rebuilds every aggregate**, and `aggregateScope` says over what. `byItemUnits`, `topBuyersByUnits` and `topSellersByUnits` rank by units, since a currency sum ranks one currency against another. Detailed trades only.

## Freshness

The ledger reads Eco's action exporter and the cycle clock reads `/info`. They fail independently, so a ledger days behind `cycle.daysRunning` is a quiet server or a stalled exporter, and one read cannot say which (COI-2067). `get_trades` and `get_market` carry a `ledgerFreshness` block, warning first in `warnings`:

- `status` - `current`, `lagging` (newest trade more than `ECO_LEDGER_LAG_WARN_DAYS`, default 2, behind the cycle), `empty`, or `unverifiable` (no clock).
- `newestTradeDay`, `daysRunning`, `lagDays` - null when not measured.
- `infoReachable`, `infoError`, `infoTradesTotal` - Eco's own trade counter. A counter that rises while `newestTradeDay` stays put across two reads is a stalled exporter. An `/info` outage reports `unverifiable` with the error.

`newestTradeDay` counts rollup rows. `get_market` also reports `marketsTotal`, `newestPricedDay` and `newestBucketDay`: it keeps the 24 busiest markets, so buckets can end weeks before the ledger.

## Messy bits handled

- **Numeric ids** - `Buyer`, `Seller`, `ShopOwner`, `Citizen` joined to names through the jobs mod.
- **`BoughtOrSold`** - live values 32 and 33, decoded best-effort to buy and sell.
- **Time** - seconds since cycle start. Misalignment reuses the crafting realignment. Streaming and caching match `civics`.
