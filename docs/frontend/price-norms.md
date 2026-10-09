# Price norms on the pages

Every item price shows its historical norm beside it: how the price compares with what the item usually sold for in past cycles at the live upgrade stage (teable:coilyco/eco-app#8368). Data and method: [price-history.md](../price-history.md).

## One component

`frontend/src/components/ItemPrice.tsx` renders price and norm together. Words come from `describeNorm` in `lib/priceNorm.ts`, one state per case, worst first. The server's `fallback` reason (`src/eco_mcp_app/norms.py`) is appended to the full sentence as written.

* **missing** - no norm in the payload. Nothing shows, but the element is marked for the checks below.
* **none** - "no past sales yet".
* **unidentified** - price in a bare ledger currency id (#217): "can't compare currencies (134 sales)". Never "no past sales".
* **reference** and **elsewhere** - no comparison possible (no page currency, or a different one). Shows the usual price in the live currency: "usually 0.84 Spectres".
* **median** - the in-currency middle price from the latest cycle trading this item.
* **fallback** - too few sales at the live stage, so all stages stand in: "30% over the usual price across all stages (134 sales)".
* **ok** - "30% over the usual Modern 4 price (134 sales)".

`compareToUsual` turns the multiple into words. Percent is the distance from 1x, rounded. Under 5% reads "about the usual Modern 4 price". From 2x it reads "2.5 times the usual ..." (whole number from 10x). Jev (jev-1.13.0) picked the thresholds.

The sale count is always shown. Compact mode (default) shows the short form, with the full sentence in a screen-reader span and the hover title. A price 50% over or more, or 30% under or more, by the same rounded percent (`isFarFromUsual`), reads in full-weight text instead of dim, never colour, since a far price is good for one side of a trade and bad for the other.

## Where the multiple comes from

A payload holds one `norm` per item row and one `normContext` (stage, live currency, caveat). `hydrateNorms` folds the context into every norm when `fetchJsonOrNull` (or the item fetch) reads it. Nested offers use their row's norm. When currencies match the page divides its own price by `referencePrice` (one row can carry two prices), else it uses the server's `multiple`.

## The checks

* `frontend/src/test/priceNorms.test.ts` - only `ItemPrice` calls `formatPrice`, no page defines its own fractional money formatter, and every page or component loading price data renders `ItemPrice` unless allowlisted with a reason.
* `frontend/src/test/priceNormsRender.test.tsx` - renders every price page from payloads the backend annotator produced and fails on any price without its norm, with a negative control. `just frontend-norm-fixtures` regenerates the payloads, and `tests/mcp/test_frontend_norm_fixtures.py` fails on drift.

Below 600px `.ledger-table` scrolls inside its own frame, and `ItemPrice` is a positioning context so its screen-reader sentence cannot escape it.
