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
