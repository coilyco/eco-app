# eco-store-exporter — C# side

The live stores/economy exporter. DiscordLink reads live game state in-process
to answer `Trades <item>` and the `Currency <name>` top-holders list; eco-app's
server exports only historical trade *events* and aggregate money-supply series,
never the current shelf or per-account balances. This mod closes both gaps from
inside the Eco process, over HTTP:

- **`GET /api/v1/stores`** — walks every live `StoreComponent` and exposes each
  store's current offers, so the Python store-directory and logistics-engine
  siblings move from history-derived to shelf-accurate.
  ([eco-app#55](https://forgejo.coilysiren.me/coilyco-gaming/eco-app/issues/55))
- **`GET /api/v1/currency-holdings`** — reads per-account, per-currency balances
  from the in-process `CurrencyManager` and joins account owners to citizen
  names via the same `UserManager` access `mods/jobs` uses, so
  `eco_mcp_app/currency.py`'s per-currency report meets the one piece of
  `Currency <name>` history cannot reconstruct: the top-holders list.
  ([eco-app#58](https://forgejo.coilysiren.me/coilyco-gaming/eco-app/issues/58))

Parent epic:
[eco-app#37](https://forgejo.coilysiren.me/coilyco-gaming/eco-app/issues/37).

Two projects share one solution (`eco-store-exporter.sln`):

| Project | Purpose | Runs where |
|---|---|---|
| `src/EcoStoreExporter.csproj` | The real mod. Exposes `GET /api/v1/stores` and `GET /api/v1/currency-holdings` from inside the Eco server process by declaring `[ApiController]` classes that Eco's ASP.NET Core host picks up via `AddApplicationPart`. | Eco dedicated server, after `dotnet build -c Release` and dropping the resulting DLL into `Server/Mods/<Name>/`. |
| `shell/EcoStoreExporter.Shell.csproj` | Standalone ASP.NET Core harness. Same routes, same DTOs, mock data. Lets the Python side iterate against a real C# HTTP server without booting Eco. | `localhost:5101`, launched by `just run-shell-stores` from the repo root. |

DTOs (`src/Dtos.cs`) are shared — the shell project `<Compile Include>`s the
file, so any change to the shape propagates to both. The contracts are
documented in [docs/dto.md](docs/dto.md) (stores) and
[docs/currency-holdings.md](docs/currency-holdings.md) (currency holdings).

## Local harness

```sh
just run-shell-stores   # -> http://localhost:5101/api/v1/stores
#                               http://localhost:5101/api/v1/currency-holdings
```

## Building the real mod

```sh
just build-mod-stores
# -> mods/stores/src/bin/Release/net10.0/EcoStoreExporter.dll
```

Copy the DLL into the Eco server's `Server/Mods/EcoStoreExporter/` directory and
restart the server. Eco's `ModKitPlugin` discovers mod DLLs on boot and
registers their MVC application parts automatically. **Deploy is out of band**:
the DLL lands at the next natural server restart, never as part of building or
testing this mod (loading a new plugin requires a restart, which #55 is
explicitly built to avoid).

## Auth

Both routes sit under Eco's existing admin-token surface, so the same
`X-API-Key` header the other admin routes use guards them. The mod adds no auth
of its own — same as `mods/jobs`.

## Why reflection in the scanners?

Both scanners (`StoreScanner`, `CurrencyHoldingsScanner`) read live game state
by member name, guarded, rather than through the typed game API — the shared
null- and exception-tolerant primitives live in `src/Reflect.cs`. This is
deliberate: the gaming-eco-investigation case library records reproducible NREs
in Eco's own trade/economy paths from orphaned objects and items removed by a
mod update surviving a save migration, so each walk assumes any referent can be
null and skips-and-continues. Reading by name also keeps the exporter building
across Eco reference-assembly versions that rename members — the currency
scanner in particular takes **no** compile-time dependency on the economy types
(it resolves `CurrencyManager` / `BankAccountManager` from the loaded
assemblies), so API drift degrades it to an empty response rather than breaking
the compile gate. The one typed touchpoint is `UserManager`, for the id→name
join, exactly as `mods/jobs` uses it. A partial answer is the right answer; a
500 is not. Full rationale is in the header comments of `src/StoreScanner.cs`
and `src/CurrencyHoldingsScanner.cs`.
