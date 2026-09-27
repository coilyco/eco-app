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

## See also

- [trades.md](trades.md) - the ledger this derives from.
- [cost.md](cost.md) - the cost model prices are compared against.
