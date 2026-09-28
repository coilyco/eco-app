# Price history

Per-item price movement over a cycle, and how it is evidenced.

## Distribution evidence

A price series is only meaningful with the spread behind it, so each point
carries the distribution rather than a bare mean. A single trade and fifty
trades at the same mean are different facts, and the surface says which.

## Specialty markers

Progression events are overlaid on the series, so a price move that follows a
citizen gaining the relevant specialty is visible as such rather than being
left for the reader to correlate by eye.

## Cycle boundary

Prices do not carry across a cycle boundary. A new cycle is a new world with a
new economy, so the series restarts rather than continuing a line that would
imply continuity that does not exist.

## Verification

The series is checked against the trades ledger for the same item and window.
They are derived from the same detailed rows, so a divergence is a parser bug
rather than a modelling choice.

## Norms by cycle and upgrade stage

`data/eco_trades_norms.json.gz` gives "what this usually goes for" in the cycle
it traded in, and at the world's tech level when it traded (Kai's decision on
teable:coilyco-gaming/eco-app#6215). It is generated from the #eco-trades dump
behind `data/eco_trades_baseline.json.gz`, and nothing in `src/` reads it yet.

```
just trades-baseline fetch /tmp/eco-trades.jsonl
just trades-baseline norms /tmp/eco-trades.jsonl --latest-cycle 14
```

`norms` takes several dumps and reads each message once. The dump carries player
names and is never committed.

**Cycles are inferred.** No canonical start list exists (the eco-ops
`CYCLE_*_START_TS` values are test fixtures, two of them equal). A world reset
leaves the channel idle, so a run of 14+ idle days is a boundary. A 3+ day run is
one when currency turnover is 0.5 or more: the lesser of the share of the prior
active week's trade lines in currencies absent afterwards, and the share of the
next week's absent before. Turnover alone needs 50 lines each side, and it never
compares across a gap. It compares neighbours rather than all-time spans,
because currency names recur across cycles (player credits, reused mints).
Boundaries sit 21+ days apart. Each cycle records `boundary`, `turnover` and
`idleDaysBefore`. The newest is `--latest-cycle` (14, the live server's), and
older ones count back from it, so one wrong boundary shifts every older number.

**Upgrade stage** is the highest upgrade traded in the cycle so far, on a
12-step ladder (Basic 1-4, Advanced 1-4, Modern 1-4, Scholars folded in). It never
falls within a cycle and restarts at `none`. It lags the true tech level by the
time from an upgrade's first craft to its first trade. `stageOnsetDay` gives when
each stage was first traded, and the craft side is not in #eco-trades.

**Per item:** `cycles.<n>.byCurrency.<cur>` and `cycles.<n>.stages.<stage>.<cur>`,
each with n, median, p25, p75, in-currency. `crossCycle.<stage>` divides each
cycle's `primaryCurrency` (stage) median by the cycle's `basketIndex`, and
takes the median across cycles. The index is chained over the `basket` (items
with 5+ primary-currency trades in 75%+ of cycles): each basket item's cycle
median over its own cross-cycle geometric mean, then the geometric mean per cycle. So a
missing item does not move it, and 1.0 is a typical cycle. It compares relative
levels, never a cost in one currency. Tests: `tests/test_trades_norms.py`.

## The norm on every listed price

Kai's decision on teable:coilyco/eco-app#8368: wherever a tool or page lists an
item price, the norm rides beside it. `eco_mcp_app/norms.py` owns the lookup, and
the tool dispatcher attaches it to every route in `PRICE_FIELDS`. A new tool must
join that map or `NO_PRICE_TOOLS`, or `tests/mcp/test_norms.py` fails.

- **Live stage** is the highest upgrade in the current cycle's trade ledger. The
  live cycle comes from the server's `/info` description. Both are cached ten
  minutes, and a failed fetch leaves a note rather than failing the tool.
- **Per object** `norm`: `basis` (`stage`, `all`, or null for no history) and
  `n` always, then `fallback` (why the stage bucket was not used, under 5
  trades), `cycles`, `referencePrice` (the basis figure times the live cycle's
  basket index), `multiple` (this price over `referencePrice`, only when the
  currency is the live primary), and the in-currency `median`, `p25`, `p75`,
  `currency`, `currencyN` and `cycle` from the latest cycle it traded in.
- **Per payload** `normContext`: `stage`, `cycle`, `referenceCurrency` and its
  `referenceCurrencyId`, `caveat`, `source`, and `notes`.
- An offer under its item's row shares the row's norm rather than repeating it.
- Ledger prices carry currency ids. Until the id-to-name map is live
  (eco-app#217), they get no `multiple` and no in-currency figure.

## Median price per stage

`price_by_stage(item)` answers "how much should I sell or buy X for" with one
median per upgrade stage and nothing else priced (teable:coilyco/eco-app#8423).
It sits in `NO_PRICE_TOOLS`: its stages already are the norm, and an attached
`norm` would add this cycle's own median beside them on the other basis.

- **Resolution** takes an exact name or Eco id, then a plural's singular, then a
  bare metal word as its bar (`iron` is Iron Bar), and retries once without a
  leading `a`, `the` or `my`. `solar panel` is an alias of Solar Generator.
  Nothing is fuzzy-matched. A
  miss returns `resolved: null` and no price, with `candidates` only when 2 to 5
  item names hold the word whole.
- **Basis** is the cross-cycle stage median times the live basket index, the
  `referencePrice` basis. Sold and Bought lines pool.
- **Floor** is `firstTradedStage`, the lowest stage the item traded at in any
  cycle, any currency or barter. No row sits below it, and `reply` ends
  `None before <stage>.` Kai's rule: nobody sold solar panels at `none`.
- **Estimates** fill every stage from the floor to Modern 4 (Kai, 2026-09-27: a
  guess beats silence). A stage with `MIN_N` trades or more is real. Any other
  stage gets `estimated: true` and keeps its own `n`. The method is Jev's pick
  on a holdout backtest over this file, error as median `|log(pred/true)|`:
  - **Between two real stages**, linear on the stage index in basket units
    (0.213, tied with log-linear, where copying the nearest stage scored 0.235).
  - **Past the outermost real stage**, up or down to the floor, flat carry of
    the nearest real median (0.259, against 0.567 for a log-linear slope).
  - **No stage at `MIN_N`** (665 of 1,598 items): the thin stages' own medians
    anchor, and every row is estimated.
- **`reply`** is the whole answer on one line, grouped by tier so every item fits
  the 280-character template cap (279 at most today). `~` marks an estimate, and
  only a real median shows its trade count. The tool's template is `{{reply}}`, and its
  `item` fills from `eco://vocab/priced-items`, built by `Norms.vocabulary` from
  the same rules `resolve` applies. A word no entry matches takes the caller's
  model path, which the server instructions tell to report a miss and stop.

## See also

- [trades.md](trades.md) - the ledger this derives from.
- [cost.md](cost.md) - the cost model prices are compared against.
