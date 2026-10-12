# eco-store-exporter - C# side

The live stores/economy exporter. eco-app's server exports only historical trade events and
aggregate money-supply series, never the current shelf or per-account balances. This mod
closes both gaps from inside the Eco process, over HTTP:

- **`GET /api/v1/stores`** walks every live `StoreComponent` and exposes its current offers,
  so the Python store directory and logistics engine become shelf-accurate
  ([eco-app#55](https://forgejo.coilysiren.me/coilyco-gaming/eco-app/issues/55)).
- **`GET /api/v1/currency-holdings`** reads per-account, per-currency balances from the
  in-process `CurrencyManager` and joins owners to citizen names through `UserManager`, as
  `mods/jobs` does ([eco-app#58](https://forgejo.coilysiren.me/coilyco-gaming/eco-app/issues/58)).
  Parent epic: [eco-app#37](https://forgejo.coilysiren.me/coilyco-gaming/eco-app/issues/37).

## Layout

One solution, `eco-store-exporter.sln`. `src/EcoStoreExporter.csproj` is the real mod, with
`[ApiController]` classes that Eco's ASP.NET Core host picks up via `AddApplicationPart`.
`shell/EcoStoreExporter.Shell.csproj` is a standalone harness with the same routes, DTOs and
mock data on `localhost:5101` (`just run-shell-stores`). `src/Dtos.cs` is shared, the shell
`<Compile Include>`s it. Contracts: [docs/dto.md](docs/dto.md) and
[docs/currency-holdings.md](docs/currency-holdings.md).

## Build, deploy, auth

`just build-mod-stores` writes `mods/stores/src/bin/Release/net10.0/EcoStoreExporter.dll`.
Copy it into `Server/Mods/EcoStoreExporter/` and restart. **Deploy is out of band**: the DLL
lands at the next natural restart, never as part of building or testing. Both routes sit
under Eco's admin-token surface (`X-API-Key`), and the mod adds no auth of its own.

## Why reflection in the scanners

`StoreScanner` and `CurrencyHoldingsScanner` read game state by member name, guarded
(primitives in `src/Reflect.cs`). The investigation case library records reproducible NREs
from orphaned objects and mod-removed items surviving a save migration, so each walk assumes
any referent can be null and skips-and-continues. It also survives reference-assembly
renames: the currency scanner has no compile-time economy dependency, so drift degrades to
an empty response. `UserManager` is the one typed touchpoint. A partial answer is right, a
500 is not. Rationale is in the scanner headers.
