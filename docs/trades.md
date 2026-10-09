# Trades ledger

Who bought and sold what, for how much, over the cycle.

## Surfaces

- **`get_trades`** - `src/eco_mcp_app/trades.py` plus `server.py` wiring.
  Returns markdown plus structured JSON.
- **The ledger in `/trade`** - `frontend/src/pages/Trade.tsx`, consuming
  `/preview/get_trades.json`.

Older history arrives as rollups rather than rows, which is why several of the
computed values below are explicitly detailed-rows-only.

## What it computes

- **Detailed row-level trades** - newest first, capped at
  `ECO_TRADES_LEDGER_ROWS` (default 4000). Older rollups are not rows.
- **Item filter** - `item` resolves through `Norms.find_qualified`, the same
  resolver `price_by_stage` uses (names, ids, plurals, bare metals, upgrade
  shorthand). It filters every ledger row before `limit` applies, and drops a
  stage qualifier since a trade has none. An unresolved word returns no rows
  and an `itemFilter` block with candidates. The summary arrays stay whole.
- **Top buyers and sellers** - currency spent by `Buyer` and earned by
  `Seller`, from detailed rows only.
- **Per-currency volume** - includes summed rollup amounts. Most-traded items
  use detailed rows only.
- **Price over time** - unit price is `CurrencyAmount / NumberOfItems`, averaged
  per in-game day, for the busiest detailed items.

## Freshness

The ledger reads Eco's action exporter. The cycle clock reads `/info`. They fail
independently, so a ledger days behind `cycle.daysRunning` is either a quiet
server or a stalled exporter, and one read cannot say which (COI-2067).
`get_trades` and `get_market` both carry a `ledgerFreshness` block, with its
warning first in `warnings`:

- `status` - `current`, `lagging` (newest trade more than `ECO_LEDGER_LAG_WARN_DAYS`,
  default 2, behind the cycle), `empty`, or `unverifiable` (no clock, so no lag).
- `newestTradeDay`, `daysRunning`, `lagDays` - null when not measured, never zero.
- `infoReachable`, `infoError`, `infoTradesTotal` - Eco's own trade counter beside the
  ledger. A counter that rises while `newestTradeDay` stays put across two reads is a
  stalled exporter. An `/info` outage leaves the ledger answering and reports
  `unverifiable` with the error.

`newestTradeDay` counts rollup rows too. `get_market` also reports `marketsTotal`,
`newestPricedDay` and `newestBucketDay`: its list keeps the 24 busiest markets, so
buckets can end weeks before the ledger does, and the cap now says so.

## Messy bits handled

- **Numeric party ids** - `Buyer`, `Seller`, `ShopOwner`, and `Citizen` are
  numeric in-game ids, joined to names through the jobs mod.
- **`BoughtOrSold` enum** - live values 32 and 33, undecoded in the exporter,
  decoded best-effort to buy and sell.
- **Time semantics** - integer seconds since cycle start, the same convention
  as the species population CSV.
- **Gather labels** - `HarvestOrHunt`, `ChopTree`, and `DigOrMine` remain a
  separate caveat rather than trades.
- **Misalignment risk** - an undeclared extra tool column shifts later fields.
  The ledger reuses the crafting realignment.

Streaming and caching match `civics`: a batched fold with a per-key `TTLCache`.
