# Price norms on the pages

Every item price on eco-app shows its historical norm beside it: how the price
compares with what the item usually sold for in past cycles at the live world's
upgrade stage (teable:coilyco/eco-app#8368). The data and method are in
[price-history.md](../price-history.md). This page covers how the frontend
shows it.

## One component

`frontend/src/components/ItemPrice.tsx` renders the price and its norm
together, so the norm looks the same on every page. The words come from
`lib/priceNorm.ts` `describeNorm`, one state per case, worst first. The
server's `fallback` reason (`src/eco_mcp_app/norms.py`) is added to the full
sentence as written.

* **missing** - the payload sent no norm. Nothing shows, but the element is
  marked for the checks below.
* **none** - no past sales of the item yet: "no past sales yet".
* **unidentified** - the price is in a bare ledger currency id (#217): "can't
  compare currencies (134 sales)". It never says there are no past sales,
  because there are, just no way to compare currencies.
* **reference** and **elsewhere** - no comparison is possible, either because
  the page has no currency for the price or because the currency differs. It
  shows the usual price in the live currency: "usually 0.84 Spectres (134
  sales)". With no usual price to show, elsewhere reads "no past sales in
  Barter, 134 sales in other currencies".
* **median** - the in-currency middle price from the most recent cycle with
  this item in this currency: "usually 0.7 Spectres (134 sales)".
* **fallback** - the live stage had too few sales, so all stages stand in, and
  it says so: "30% over the usual price across all stages (134 sales)".
* **ok** - "30% over the usual Modern 4 price (134 sales)", or "40% under the
  usual Modern 4 price (134 sales)".

`compareToUsual` turns the multiple into words. The percent is the multiple's
distance from 1x, rounded (0.6x is 40% under, 1.3x is 30% over). A rounded
percent under 5 reads "about the usual Modern 4 price". From 2x up it reads
"2.5 times the usual Modern 4 price" (a whole number from 10x), because "150%
over" is harder to take in. A priced item never reads "100% under". The
thresholds were picked by Jev (jev-1.13.0), within 5% at p=.53 and times from
2x at p=.72.

The sale count is always there. Compact mode, the default, shows the short
form, and puts the full sentence ("This is 30% over the usual Modern 4 price,
based on 134 sales over 11 cycles.") in a screen-reader span and the hover
title. A multiple of 1.5x or more, or 0.7x or less, rounded to one decimal,
reads in full-weight text instead of dim. In words that is about 50% over or
30% under, give or take the rounding. Colour is not used, because a far price
is good for one side of a trade and bad for the other.

## Where the multiple comes from

A payload holds one `norm` per item row and one `normContext` for the whole
payload (stage, live currency, caveat). `hydrateNorms` folds the context into
every norm when `fetchJsonOrNull` (or the item or user fetch) reads the
payload. Nested offers use their row's norm.

The page divides its own price by `referencePrice` when the currencies match,
because one row can carry two prices (buy and sell, cheapest and median). It
uses the server's `multiple` otherwise.

## The checks

* `frontend/src/test/priceNorms.test.ts` - only `ItemPrice` calls
  `formatPrice`, no page defines its own fractional money formatter, and every
  page or component that loads price data renders `ItemPrice`, unless it is
  allowlisted with a reason. Other money (spreads, totals, margins, rates, chart
  labels) uses `formatMoney`.
* `frontend/src/test/priceNormsRender.test.tsx` - renders every price page from
  payloads the backend's own annotator produced, and fails on any price without
  its norm. A negative control strips the norms and must be caught.
  `just frontend-norm-fixtures` regenerates the payloads, and
  `tests/mcp/test_frontend_norm_fixtures.py` fails if they drift.

## Layout

Dense tables gain a norm line per price, so below 600px `.ledger-table` scrolls
inside its own frame, and the page never scrolls sideways. `ItemPrice` is a
positioning context, so its screen-reader sentence cannot escape that frame.
