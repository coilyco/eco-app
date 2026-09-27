using Microsoft.AspNetCore.Mvc;

namespace EcoStoreExporter;

// Lives inside the Eco server process. Eco's ASP.NET Core host picks up
// [ApiController] classes from mod assemblies via AddApplicationPart, so this
// route appears under the server's existing /api/v1/* surface. That surface is
// guarded by Eco's admin-token middleware, so the same `X-API-Key` header the
// other admin endpoints use authenticates this one - the mod adds no auth of
// its own (same as mods/jobs).
//
// Returns the current shelf of every live store: the shelf-accurate snapshot
// that upgrades the store-directory and logistics-engine siblings
// from history-derived to live `Trades <item>` parity. See ../docs/dto.md.
[ApiController]
[Route("api/v1/stores")]
public class StoresApiController : ControllerBase
{
    [HttpGet]
    public IEnumerable<StoreDto> Get() => StoreScanner.Scan();
}
