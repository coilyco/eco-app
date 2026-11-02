// Copyright (c) Kai Ase Siren. Licensed under the MIT License.

namespace EcoTelemetry;

using System;
using System.Threading.Tasks;

// One full pass over the world: the rows, how many objects the pass visited, and
// how long it took. TypedRows and TypedRateSum come from a second, typed read of
// the same objects (no reflection), so a seat can compare them with the rows
// without a human: equal count and equal rate sum mean the reflection read missed
// nothing and read nothing wrong. CompletedUtc is stamped by the cache when the pass returns.
internal sealed record PolluterScan(
    PolluterDto[] Rows,
    int ObjectsVisited,
    double ScanMs,
    int TypedRows,
    double TypedRateSum,
    DateTimeOffset CompletedUtc);

// Serves /api/v1/polluters from the last completed pass, so no HTTP request pays
// for a walk of the world. COI-3157 measured 134.9s for one request, far past the
// 10s the eco-app caller allows.
//
// Stale-while-revalidate: past the TTL the next request still gets the old rows at
// once and starts one background pass. Only the very first request has nothing to
// serve, and it waits `firstWait` for the pass before answering Warming.
//
// The TTL runs from when a pass finishes, so a slow pass cannot run back to back.
// Eco-free on purpose (the scan is injected) so the test project links this file.
internal sealed class PolluterSnapshotCache
{
    public enum Status
    {
        Ready,
        Unavailable,
        Warming,
    }

    public readonly record struct Result(Status Status, PolluterScan? Scan);

    private readonly Func<PolluterScan?> scan;
    private readonly TimeProvider clock;
    private readonly TimeSpan ttl;
    private readonly object gate = new();

    private PolluterScan? latest;
    private DateTimeOffset? lastFinishedUtc;
    private Task? inflight;

    public PolluterSnapshotCache(Func<PolluterScan?> scan, TimeSpan ttl, TimeProvider clock)
    {
        this.scan = scan;
        this.ttl = ttl;
        this.clock = clock;
    }

    public async Task<Result> GetAsync(TimeSpan firstWait)
    {
        Task? waitOn = null;

        lock (this.gate)
        {
            var stale = this.lastFinishedUtc is null || this.clock.GetUtcNow() - this.lastFinishedUtc.Value >= this.ttl;
            if (stale && this.inflight is null) this.inflight = Task.Run(this.RunScan);

            if (this.latest is not null) return new Result(Status.Ready, this.latest);
            if (this.inflight is null) return new Result(Status.Unavailable, null);
            waitOn = this.inflight;
        }

        await Task.WhenAny(waitOn, Task.Delay(firstWait, this.clock)).ConfigureAwait(false);

        lock (this.gate)
        {
            if (this.latest is not null) return new Result(Status.Ready, this.latest);
            return this.inflight is null ? new Result(Status.Unavailable, null) : new Result(Status.Warming, null);
        }
    }

    private void RunScan()
    {
        PolluterScan? result = null;
        try
        {
            result = this.scan();
        }
        catch
        {
            // A scan never throws into the server. No result reads as unavailable.
        }

        lock (this.gate)
        {
            var now = this.clock.GetUtcNow();
            if (result is not null) this.latest = result with { CompletedUtc = now };
            this.lastFinishedUtc = now;
            this.inflight = null;
        }
    }
}
