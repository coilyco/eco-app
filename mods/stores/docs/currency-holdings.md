# `/api/v1/currency-holdings` DTO contract

The shape the live exporter emits and `eco_mcp_app/currency.py`'s per-currency report
consumes ([eco-app#58](https://forgejo.coilysiren.me/coilyco-gaming/eco-app/issues/58), under
epic [#37](https://forgejo.coilysiren.me/coilyco-gaming/eco-app/issues/37)).
## Why this exists
History cannot reconstruct the **top-holders list**: `PersonalWealthInDefaultCurrency` and
`GovernmentHoldingsInDefaultCurrency` are aggregate series, and `CurrencyTrade` rows are
flows with ids that still hit the id-to-name blocker
([#5](https://forgejo.coilysiren.me/coilyco-gaming/eco-app/issues/5)). This is also the only
place a currency's id and name appear together, which trade attribution needs
([#217](https://forgejo.coilysiren.me/coilyco-gaming/eco-app/issues/217)).

## Endpoint and shape
`GET /api/v1/currency-holdings` with `X-API-Key: <admin token>`. Returns an array, one entry
per currency:
```jsonc
{ "currency": "Sirens Credit",  // display name
  "id": "2533707",              // in-game Currency id, or null (unread)
  "backed": true,               // minted/backed vs personal, or null (unread)
  "accountsCounted": 4,         // ALL holding accounts, not just topHolders
  "totalHoldings": 18250.0,     // sum over ALL accounts
  "topHolders": [ { "account": "Treasury", "holder": null, "balance": 9000.0 } ] }
```
- **`backed`** is best-effort. Python keeps its own classification from `MintCurrency`.
- **`accountsCounted`, `totalHoldings`** cover all accounts, so truncation never distorts
  the money supply and the report can say "top N of `accountsCounted`".
- **`topHolders`** is capped at 25 by the mod and 15 by the consumer, ranked by balance.
- **`holder`** is the owner citizen name via `UserManager`, null for a government or company
  account with no single owner or a missed join. `account` always carries the row.

## Nulls and the Python side
Live economy state is not clean (orphaned accounts and currencies surviving a save
migration), so `CurrencyHoldingsScanner` reads members by name, guarded, and
skips-and-continues. A partial table is valid, a 500 is not
([`src/CurrencyHoldingsScanner.cs`](../src/CurrencyHoldingsScanner.cs)). The DLL deploys out
of band, so the route 404s until the next restart: `currency.py` then leaves
`holdersReachable` false and renders `HOLDERS_UNAVAILABLE_NOTE`. Reachable-but-empty is a
distinct, valid state. `just run-shell-stores` serves mock data on `:5101`.
