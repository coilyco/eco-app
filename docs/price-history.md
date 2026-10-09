# Price history

Norms, `price_by_stage`, upgrade shorthand. Code: `norms.py`, `upgrade_words.py`.

## Norms by cycle and stage

`data/eco_trades_norms.json.gz` is "what this usually goes for" per cycle and upgrade stage (teable:coilyco-gaming/eco-app#6215). `just trades-baseline fetch|norms` builds it from the #eco-trades dump (player names, never committed).

- **Cycles are inferred.** A 14+ day idle run is a boundary, as is a 3+ day run at currency turnover 0.5 or more. The newest is `--latest-cycle`, so one wrong boundary shifts every older number.
- **Upgrade stage** is the highest upgrade traded so far in the cycle. It lags the true tech level.
- **Cross-cycle** `crossCycle.<stage>` is each cycle's stage median over its `basketIndex`, medianed across cycles. 1.0 is a typical cycle. Tests: `tests/test_trades_norms.py`.

## The norm on every listed price

Kai's decision, teable:coilyco/eco-app#8368. `norms.py` owns the lookup and the dispatcher attaches it to every route in `PRICE_FIELDS`. A new tool joins that map or `NO_PRICE_TOOLS`, or `tests/mcp/test_norms.py` fails.

- **Live stage** is the highest upgrade in the current cycle's ledger, the cycle from `/info`. Both cache 10 minutes.
- **Per object** `norm`: `basis` (`stage`, `all`, null), `n`, `fallback`, `referencePrice`, `multiple` (live primary currency only), `median`, `p25`, `p75`. Per payload: `normContext`.
- Ledger prices carry currency ids, so they get no `multiple` until the name map is live (eco-app#217).

## Median price per stage

`price_by_stage(item, stage?)` returns one median per upgrade stage (teable:coilyco/eco-app#8423). It is in `NO_PRICE_TOOLS` because its stages already are the norm.

- **Resolution** is exact, never fuzzy. A miss returns `resolved: null`, with `candidates` only when 2 to 5 names hold the word.
- **Floor** is `firstTradedStage`. No row sits below it.
- **Estimates** fill the floor to Modern 4. Stages under `MIN_N` get `estimated: true`. Between real stages, linear on the stage index. Past the outermost, flat carry.
- **`reply`** is one line within the 280-character cap, `~` marking estimates. `item` fills from `eco://vocab/priced-items`.

## Upgrade shorthand

Members write `au3`, `SBU4`, `bu5` or `MU0` (teable:coilyco/eco-app#8425). `upgrade_words.py` reads `(s)?([bam])u ?([0-5])`, case-insensitive. `s` is Scholars.

- **As the item.** `xu1`-`xu4` is `<Tier> Upgrade n`. `xu0` and `sxu5` name no item.
- **Specialist modules are the 5 of their tier**, by the tier-4 module the Eco 0.14 recipe consumes. `upgrade_words.SPECIALISTS` holds the table. A bare `xu5` lists them.
- **As a stage qualifier**, through `stage` or a token in the phrase (`iron at au3`). `xu0` is the stage before the tier and `xu5` is the tier's 4. The phrase is tried as an item first.
- **The reply leads with the asked stage** and says it is the market median, not the crafting cost. `eco://vocab/stages` maps tokens to stages.

See also: [trades.md](trades.md), [cost.md](cost.md).
