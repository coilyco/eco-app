# `/api/v1/stores` DTO contract

The shape the live store-offer exporter emits, and the contract the Python
siblings upgrade to. Built in
[eco-app#55](https://forgejo.coilysiren.me/coilyco-gaming/eco-app/issues/55)
under the DiscordLink-replacement epic
([#37](https://forgejo.coilysiren.me/coilyco-gaming/eco-app/issues/37)).

Consumers that wire the live path against this contract:

- **Store & trader directory** — [eco-app#50](https://forgejo.coilysiren.me/coilyco-gaming/eco-app/issues/50). Uses `name`, `owner`, `currency`, `location`.
- **Logistics engine** (cheapest-source / arbitrage / supply-gap) — [eco-app#51](https://forgejo.coilysiren.me/coilyco-gaming/eco-app/issues/51). Uses `offers[]` with `item`, `buying`, `price`, `quantity`.

Every sibling works from historical trade *events* today and sharpens to this
shelf-accurate feed automatically once the mod deploys.

## Endpoint

```
GET /api/v1/stores
X-API-Key: <admin token>
```

Same admin-token surface as the other `/api/v1/*` admin routes. Returns a JSON
array of stores.

## Shape

```jsonc
[
  {
    "name": "Redwood Lumber Depot",   // store display name
    "owner": "redwood",               // owner name, or null (unowned store is legal)
    "currency": "Sirens Credit",      // store currency name, or null (no currency set)
    "location": { "x": 412, "y": 68, "z": 1180 },  // world block coords, or null (orphaned store)
    "offers": [
      {
        "item": "Lumber",             // item display name
        "itemTypeName": "LumberItem", // item type/code name, for programmatic joins
        "buying": false,              // STORE's perspective: false = store sells, true = store buys
        "price": 1.25,                // price in the store's `currency`
        "quantity": 480               // stock available (sell) or amount still wanted (buy)
      }
    ]
  }
]
```

### Field semantics

- **`buying`** is from the **store's** point of view, matching DiscordLink's
  buy/sell split: `false` = the store **sells** this item (players buy from it),
  `true` = the store **buys** this item (players sell to it).
- **`quantity`** is current stock for a sell offer, or the amount the store
  still wants for a buy offer.
- **`price`** is denominated in the store's `currency`. A `0.0` price on a store
  with `currency: null` is a barter/free shelf, not a currency price.
- **`owner`**, **`currency`**, and **`location`** are nullable by design. Eco
  permits unowned stores, currency-less stores, and — after a save migration —
  orphaned store objects whose position can no longer be read. Consumers must
  tolerate nulls in any of these.

## Nulls and dangling references

The exporter walks live game state, and that state is not clean. The
gaming-eco-investigation case library records a reproducible NRE in Eco's own
trade path (`StoreComponent → TradeOffer → Stack.Item`, plus `store.Parent` and
currency accessors) caused by orphaned stores and items removed by a mod update
surviving a save migration. The mod therefore skips any store or offer it cannot
read cleanly rather than failing the whole response — a **partial shelf is a
valid response**. Consumers should treat the array as best-effort-complete, not
authoritative-complete, and never assume an item they saw last snapshot is
present this one.

## Local development

The shell harness (`mods/stores/shell`) serves this exact shape with mock data
on `:5101`, so the Python consumers can be built and tested with no live Eco
server:

```sh
just run-shell-stores   # -> http://localhost:5101/api/v1/stores
```
