// Copyright (c) Kai Ase Siren. Licensed under the MIT License.

namespace EcoTelemetry.Tests;

using Xunit;

public class PolluterSnapshotCacheTests
{
    private static readonly TimeSpan Ttl = TimeSpan.FromSeconds(30);
    private static readonly TimeSpan Patient = TimeSpan.FromSeconds(10);

    // Moves only when told to. Timers still run on the system clock, which is all
    // Task.Delay needs here.
    private sealed class ManualClock : TimeProvider
    {
        private DateTimeOffset now = new(2026, 11, 2, 12, 0, 0, TimeSpan.Zero);

        public override DateTimeOffset GetUtcNow() => this.now;

        public void Advance(TimeSpan by) => this.now += by;
    }

    // Each call numbers itself in ScanMs so a test can tell which pass it holds.
    private sealed class CountingScan
    {
        private int calls;

        public int Calls => Volatile.Read(ref this.calls);

        public ManualResetEventSlim? Hold { get; set; }

        public bool ReturnNull { get; set; }

        public bool Throw { get; set; }

        public PolluterScan? Run()
        {
            var n = Interlocked.Increment(ref this.calls);
            this.Hold?.Wait(Patient);
            if (this.Throw) throw new InvalidOperationException("scan failed");
            return this.ReturnNull ? null : new PolluterScan(Array.Empty<PolluterDto>(), 100, n, 0, 0d, default);
        }
    }

    private static async Task<PolluterSnapshotCache.Result> PollUntil(PolluterSnapshotCache cache, Func<PolluterSnapshotCache.Result, bool> done)
    {
        for (var i = 0; i < 200; i++)
        {
            var result = await cache.GetAsync(Patient);
            if (done(result)) return result;
            await Task.Delay(10);
        }

        throw new Xunit.Sdk.XunitException("the cache never reached the expected state");
    }

    [Fact]
    public async Task Serves_the_cached_pass_inside_the_ttl()
    {
        var scan = new CountingScan();
        var clock = new ManualClock();
        var cache = new PolluterSnapshotCache(scan.Run, Ttl, clock);

        var first = await cache.GetAsync(Patient);
        clock.Advance(TimeSpan.FromSeconds(29));
        var second = await cache.GetAsync(Patient);

        Assert.Equal(PolluterSnapshotCache.Status.Ready, first.Status);
        Assert.Same(first.Scan, second.Scan);
        Assert.Equal(1, scan.Calls);
        Assert.Equal(100, first.Scan?.ObjectsVisited);
    }

    [Fact]
    public async Task Past_the_ttl_the_old_rows_are_served_at_once_and_one_pass_refreshes()
    {
        var scan = new CountingScan();
        var clock = new ManualClock();
        var cache = new PolluterSnapshotCache(scan.Run, Ttl, clock);
        var first = await cache.GetAsync(Patient);

        scan.Hold = new ManualResetEventSlim(false);
        clock.Advance(Ttl);
        var stale = await cache.GetAsync(TimeSpan.FromMilliseconds(50));
        var alsoStale = await cache.GetAsync(TimeSpan.FromMilliseconds(50));

        Assert.Same(first.Scan, stale.Scan);
        Assert.Same(first.Scan, alsoStale.Scan);

        scan.Hold.Set();
        var fresh = await PollUntil(cache, r => r.Scan?.ScanMs == 2);

        Assert.Equal(2, fresh.Scan?.ScanMs);
        Assert.Equal(2, scan.Calls);
    }

    [Fact]
    public async Task Concurrent_first_requests_share_one_pass()
    {
        var scan = new CountingScan { Hold = new ManualResetEventSlim(false) };
        var cache = new PolluterSnapshotCache(scan.Run, Ttl, new ManualClock());

        var waiting = Enumerable.Range(0, 10).Select(_ => cache.GetAsync(Patient)).ToArray();
        await Task.Delay(50);
        scan.Hold.Set();
        var results = await Task.WhenAll(waiting);

        Assert.All(results, r => Assert.Equal(PolluterSnapshotCache.Status.Ready, r.Status));
        Assert.Equal(1, scan.Calls);
    }

    [Fact]
    public async Task A_first_pass_slower_than_the_wait_answers_warming_then_ready()
    {
        var scan = new CountingScan { Hold = new ManualResetEventSlim(false) };
        var cache = new PolluterSnapshotCache(scan.Run, Ttl, new ManualClock());

        var cold = await cache.GetAsync(TimeSpan.FromMilliseconds(50));
        scan.Hold.Set();
        var warm = await PollUntil(cache, r => r.Status == PolluterSnapshotCache.Status.Ready);

        Assert.Equal(PolluterSnapshotCache.Status.Warming, cold.Status);
        Assert.NotNull(warm.Scan);
        Assert.Equal(1, scan.Calls);
    }

    [Fact]
    public async Task An_unreadable_world_is_unavailable_and_is_not_rescanned_inside_the_ttl()
    {
        var scan = new CountingScan { ReturnNull = true };
        var clock = new ManualClock();
        var cache = new PolluterSnapshotCache(scan.Run, Ttl, clock);

        var first = await cache.GetAsync(Patient);
        var second = await cache.GetAsync(Patient);
        clock.Advance(Ttl);
        await cache.GetAsync(Patient);

        Assert.Equal(PolluterSnapshotCache.Status.Unavailable, first.Status);
        Assert.Equal(PolluterSnapshotCache.Status.Unavailable, second.Status);
        await PollUntil(cache, _ => scan.Calls == 2);
    }

    [Fact]
    public async Task A_scan_that_throws_is_unavailable_not_an_exception()
    {
        var scan = new CountingScan { Throw = true };
        var cache = new PolluterSnapshotCache(scan.Run, Ttl, new ManualClock());

        var result = await cache.GetAsync(Patient);

        Assert.Equal(PolluterSnapshotCache.Status.Unavailable, result.Status);
    }

    [Fact]
    public async Task A_later_unreadable_pass_keeps_serving_the_last_good_rows()
    {
        var scan = new CountingScan();
        var clock = new ManualClock();
        var cache = new PolluterSnapshotCache(scan.Run, Ttl, clock);
        var first = await cache.GetAsync(Patient);

        scan.ReturnNull = true;
        clock.Advance(Ttl);
        await cache.GetAsync(Patient);
        await PollUntil(cache, _ => scan.Calls == 2);
        var after = await cache.GetAsync(Patient);

        Assert.Equal(PolluterSnapshotCache.Status.Ready, after.Status);
        Assert.Same(first.Scan, after.Scan);
    }
}
