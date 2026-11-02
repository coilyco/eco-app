// Copyright (c) Kai Ase Siren. Licensed under the MIT License.

namespace EcoTelemetry;

using Microsoft.AspNetCore.Mvc;

// Read-only list of every air-polluting device in the world: type, owner,
// position, configured emission rate, and whether it is running. The eco-app
// pollution card builds its device table from this (COI-3149), since the
// /api/v1/exporter/actions stream it used before only carries PolluteAir tick
// counts per station type, with no instance, no rate, and no idle device.
//
// Same hosting and auth as ClimateSettingsApiController: Eco picks up
// [ApiController] classes from mod assemblies, so this rides the /api/v1/*
// admin-token (`X-API-Key`) middleware and adds no auth of its own. A 404 (object
// manager unreadable, e.g. before the simulation is up) is a valid answer the
// Python consumer treats as "endpoint absent".
[ApiController]
[Route("api/v1/polluters")]
public class PolluterApiController : ControllerBase
{
    [HttpGet]
    public ActionResult<PolluterDto[]> Get()
    {
        var rows = PolluterReader.Read();
        return rows is null ? this.NotFound() : rows;
    }
}
