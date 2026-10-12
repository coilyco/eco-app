# Suggested price on the item page

Under the `Market` heading the page suggests a price to ask and a price to bid (COI-2784). It is a guide from this server's own sales, and the page says so.

## How it is worked out

`lib/priceSuggestion.ts` takes the pivot's `trades`. The ask is the middle sale price and the bid is the 25th percentile, each sale weighted by its `eventCount`. Barter and unpriced rows are skipped. The basis sentence gives the sale count, the number of shop credits and the range.

## Credits at face value

Each store mints its own credit and the server has no exchange rate between them, so the sales are pooled as they stand and the basis says so. A real conversion needs rates from observed trades or a reference currency, which is server work (COI-2789). Until then the number is a guide, and a few sales read as a guess.

## States

* **No priced sale** - "No sale of X has a price yet, so there is nothing to suggest."
* **No sales** - the block is absent.
* **Pivot failed** - the page's "item history unavailable" pill stands, with no block.

## The norm beside it

The price renders through `ItemPrice` with `POOLED_CURRENCY`, so its norm is the `pooled` state, "N sales in past cycles", never "no past sales". A listed price whose credit has no history reads "N sales, all in other currencies". Both are in `lib/priceNorm.ts`.

`/uses/price` suggests differently: from one currency's market row, or a craft cost plus markup.
