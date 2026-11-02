// Copyright (c) Kai Ase Siren. Licensed under the MIT License.

namespace EcoTelemetry;

using System;
using System.Globalization;
using System.Threading.Tasks;
using Microsoft.AspNetCore.Http;
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
// Python consumer treats as "endpoint absent", and so is the 503 a cold start
// answers while the first pass is still running.
//
// The body is rendered by PolluterJson and returned as content, never as DTOs,
// and the rows come from PolluterSnapshotCache (COI-3157 for both). Server-Timing
// reports how long the pass behind this snapshot took, so the cost of a world walk
// is read off any response instead of guessed. The X-Polluters-Typed-* headers carry
// the typed cross-check count and rate sum a seat compares against the body.
[ApiController]
[Route("api/v1/polluters")]
public class PolluterApiController : ControllerBase
{
    private static readonly TimeSpan SnapshotTtl = TimeSpan.FromSeconds(30);

    // Shorter than the 10s the eco-app caller allows, so a cold start answers 503
    // instead of timing the caller out.
    private static readonly TimeSpan FirstPassWait = TimeSpan.FromSeconds(4);

    private static readonly PolluterSnapshotCache Cache = new(PolluterReader.Scan, SnapshotTtl, TimeProvider.System);

    [HttpGet]
    public async Task<ActionResult> Get()
    {
        var result = await Cache.GetAsync(FirstPassWait).ConfigureAwait(false);

        switch (result.Status)
        {
            case PolluterSnapshotCache.Status.Ready when result.Scan is { } scan:
                var age = DateTimeOffset.UtcNow - scan.CompletedUtc;
                this.Response.Headers["Server-Timing"] = $"scan;dur={scan.ScanMs.ToString("F1", CultureInfo.InvariantCulture)}";
                this.Response.Headers["X-Polluters-Visited"] = scan.ObjectsVisited.ToString(CultureInfo.InvariantCulture);
                this.Response.Headers["X-Polluters-Typed-Rows"] = scan.TypedRows.ToString(CultureInfo.InvariantCulture);
                this.Response.Headers["X-Polluters-Typed-Rate-Sum"] = scan.TypedRateSum.ToString("R", CultureInfo.InvariantCulture);
                this.Response.Headers["X-Polluters-Age-Ms"] = ((long)age.TotalMilliseconds).ToString(CultureInfo.InvariantCulture);
                return this.Content(PolluterJson.Render(scan.Rows), "application/json");
            case PolluterSnapshotCache.Status.Warming:
                this.Response.Headers["Retry-After"] = "5";
                return this.StatusCode(StatusCodes.Status503ServiceUnavailable);
            default:
                return this.NotFound();
        }
    }
}
