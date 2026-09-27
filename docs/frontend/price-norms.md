# Price norms on the pages

Every item price on eco-app shows its historical norm beside it: how the price
compares with what the item usually sold for in past cycles at the live world's
upgrade stage (teable:coilyco/eco-app#8368). The data and method are in
[price-history.md](../price-history.md). This page covers how the frontend
shows it.

## One component

`frontend/src/components/ItemPrice.tsx` renders the price and its norm
together, so the norm looks the same on every page. The words come from
`lib/priceNorm.ts` `describeNorm`, one state per case, worst first:

* **missing** - the payload sent no norm. Nothing shows, but the element is
  marked for the checks below.
* **none** - no past trades of the item yet: "no history yet".
* **unidentified** - the price is in a bare ledger currency id (#217): "currency
  not identified, 134 trades". It never says "no history", because there is
  history, just no way to compare currencies.
* **reference** and **elsewhere** - no multiple is possible, either because the
  page has no currency for the price or because the currency differs. It shows
  the usual price in the live currency: "usual 0.84 Spectres, 134 trades".
* **median** - the in-currency median from the most recent cycle with this item
  in this currency.
* **fallback** - the live stage was too thin, so all stages stand in, and it
  says so: "1.3x usual (all stages), 134 trades".
* **ok** - "1.3x usual Modern 4, 134 trades".

The observation count is always there. Compact mode, the default, shows the
short form, and puts the full sentence in a screen-reader span and the hover
title. A multiple of 1.5x or more, or 0.7x or less, reads in full-weight text
instead of dim. Colour is not used, because a far price is good for one side
of a trade and bad for the other.

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
