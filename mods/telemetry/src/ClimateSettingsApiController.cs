// Copyright (c) Kai Ase Siren. Licensed under the MIT License.

namespace EcoTelemetry;

using Microsoft.AspNetCore.Mvc;

// Read-only surface for the live per-server climate ruleset. Eco exposes no
// HTTP endpoint for EcoDef.Obj.ClimateSettings, so the eco-app /climate card
// otherwise ships hardcoded Eco defaults that can silently disagree with a
// server that has retuned them. This mirrors those values so the card reads
// the real thresholds. See eco-app#8.
//
// Eco's ASP.NET Core host picks up [ApiController] classes from mod assemblies
// via AddApplicationPart, so this route appears under the server's existing
// /api/v1/* surface. That surface is guarded by Eco's admin-token middleware,
// so the same `X-API-Key` header the other admin endpoints use authenticates
// this one - the mod adds no auth of its own (same as mods/jobs, mods/stores).
//
// A 404 (settings unreadable, e.g. queried before the simulation is up) is a
// valid answer: the Python consumer treats any non-200 as "endpoint absent"
// and falls back to the documented Eco defaults.
//
// The body is rendered by WireJson and returned as content, never as the DTO:
// Eco's Newtonsoft resolver serializes double? as `{}` (COI-3157, COI-3160), which
// the consumer read as "no usable value" and fell back to defaults for every field.
[ApiController]
[Route("api/v1/climate-settings")]
public class ClimateSettingsApiController : ControllerBase
{
    [HttpGet]
    public ActionResult Get()
    {
        var dto = ClimateSettingsReader.Read();
        return dto is null ? this.NotFound() : this.Content(WireJson.Render(dto), "application/json");
    }
}
