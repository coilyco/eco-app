# `/api/v1/stores` DTO contract

The shape the live store-offer exporter emits and the Python siblings upgrade to
([eco-app#55](https://forgejo.coilysiren.me/coilyco-gaming/eco-app/issues/55), under epic
[#37](https://forgejo.coilysiren.me/coilyco-gaming/eco-app/issues/37)). Consumers: the store and
trader directory ([#50](https://forgejo.coilysiren.me/coilyco-gaming/eco-app/issues/50), uses
`name`, `owner`, `currency`, `location`) and the logistics engine
([#51](https://forgejo.coilysiren.me/coilyco-gaming/eco-app/issues/51), uses `offers[]`). Both sharpen to this shelf-accurate feed once the mod deploys.
## Endpoint and shape
`GET /api/v1/stores` with `X-API-Key: <admin token>`, the same surface as the other
`/api/v1/*` admin routes. Returns an array of stores:
```jsonc
{ "name": "Redwood Lumber Depot",   // store display name
  "owner": "redwood",               // or null (unowned store is legal)
  "currency": "Sirens Credit",      // or null (no currency set)
  "location": { "x": 412, "y": 68, "z": 1180 },  // block coords, or null (orphaned store)
  "offers": [ { "item": "Lumber", "itemTypeName": "LumberItem",  // type name for joins
                "buying": false,    // STORE's view: false = sells, true = buys
                "price": 1.25,      // in the store's `currency`
                "quantity": 480 } ] }  // stock (sell) or amount still wanted (buy)
```
## Field semantics
- **`buying`** is from the **store's** point of view, matching DiscordLink's buy/sell split.
- **`price`** is in the store's `currency`. `0.0` with `currency: null` is a barter or free
  shelf, not a currency price.
- **`owner`**, **`currency`**, **`location`** are nullable by design: Eco allows unowned and
  currency-less stores, and orphaned store objects whose position cannot be read after a save
  migration. Consumers must tolerate nulls.
## Nulls and dangling references
The exporter walks live game state, which is not clean. The investigation case library records
a reproducible NRE in Eco's trade path (`StoreComponent -> TradeOffer -> Stack.Item`, plus
`store.Parent` and currency accessors) from orphaned stores and mod-removed items surviving a
save migration. The mod skips any store or offer it cannot read cleanly, so a **partial shelf
is a valid response**. Never assume an item from the last snapshot is still present.
## Local development
The shell harness (`mods/stores/shell`) serves this shape with mock data on `:5101`, so the
Python consumers need no live Eco server: `just run-shell-stores`, then `/api/v1/stores`.
