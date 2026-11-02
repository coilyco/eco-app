// Copyright (c) Kai Ase Siren. Licensed under the MIT License.

namespace EcoTelemetry;

using System;
using System.Collections.Generic;
using System.Diagnostics;
using Eco.Gameplay.Components;
using Eco.Gameplay.Objects;

// The one Eco-bound piece of /api/v1/polluters: walks every live WorldObject and
// hands each to PolluterRowBuilder. Everything else in the endpoint names no Eco
// type, so the test project can drive it.
//
// WorldObjectManager.ForEach is `objectsByID.Select(...)` over a ConcurrentDictionary
// (Eco source, Eco.Gameplay/Objects/WorldObjectManager.cs), so this walk takes no
// world lock and runs on the caller's thread, not the simulation thread. The same
// walk backs the eco.world_objects.count metric in MetricsWorker.
internal static class PolluterReader
{
    // Null means the object manager was unreadable before any row came out (for
    // example queried before the simulation is up), which the controller answers
    // with 404. An empty row set is a real answer: the world has no air polluters.
    public static PolluterScan? Scan()
    {
        var clock = Stopwatch.StartNew();
        var rows = new List<PolluterDto>();
        var visited = 0;
        var typedRows = 0;
        var typedRateSum = 0d;
        var enumerated = false;

        try
        {
            WorldObjectManager.ForEach(wo =>
            {
                if (wo is null) return;
                visited++;
                try
                {
                    var row = PolluterRowBuilder.BuildRow(wo);
                    if (row is not null) rows.Add(row);
                }
                catch
                {
                    // Orphaned object or mid-iteration mutation. Skip this one.
                }

                try
                {
                    var rate = TypedAirRate(wo);
                    if (rate is not null)
                    {
                        typedRows++;
                        typedRateSum += rate.Value;
                    }
                }
                catch
                {
                    // Same: the cross-check skips what it cannot read.
                }
            });
            enumerated = true;
        }
        catch
        {
            // Manager unready or the collection mutated under us. Keep what we have.
        }

        if (!enumerated && rows.Count == 0) return null;
        return new PolluterScan(rows.ToArray(), visited, clock.Elapsed.TotalMilliseconds, typedRows, typedRateSum, default);
    }

    // The cross-check read: the component by type and the rate by its compiled
    // property, so it fails to build, instead of silently reading null, if Eco
    // renames either. Disagreement with the reflected rows is the signal.
    private static double? TypedAirRate(WorldObject wo)
    {
        foreach (var component in wo.Components)
        {
            if (component is AirPollutionComponent air) return air.PollutionTonsPerHour;
        }

        return null;
    }
}
