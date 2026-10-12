using System.Diagnostics;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using Xunit;

namespace EcoReplay.Tests;

// Regression tests for issue #28, bug 1: ActionMapper.ToRow serialization was
// bounded in depth but not breadth, so a click action carrying a wide entity
// collection allocated gigabytes before erroring. These exercise the extracted
// BodySerializer against synthetic stand-ins for those wide references.
public class BodySerializerTests
{
    // A stand-in for an Eco entity: has a Name and a self-referential-ish graph
    // that a naive walk would happily expand.
    private sealed class FakeEntity
    {
        public string Name { get; set; } = "";
        public string Description { get; set; } = new string('x', 512);
        public FakeEntity? Parent { get; set; }
        public List<FakeEntity> Children { get; set; } = new();
    }

    private sealed class WideAction
    {
        public string ActionType => "Interact";
        public int Count { get; set; } = 3;
        // The pathological property: a huge collection of complex entities.
        public IEnumerable<FakeEntity> Inventory { get; set; } = new List<FakeEntity>();
        public FakeEntity Actor { get; set; } = new() { Name = "Alice" };
    }

    private static IEnumerable<FakeEntity> Lazy(int n)
    {
        for (var i = 0; i < n; i++)
            yield return new FakeEntity { Name = $"item-{i}", Description = new string('y', 256) };
    }

    [Fact]
    public void TenThousandItemCollection_IsBounded_InSizeAndTime()
    {
        var action = new WideAction
        {
            Inventory = Enumerable.Range(0, 10_000)
                .Select(i => new FakeEntity { Name = $"stack-{i}" })
                .ToList(),
        };

        var sw = Stopwatch.StartNew();
        var body = BodySerializer.Serialize(action);
        sw.Stop();

        // Bounded time: summarizing a 10k collection must be near-instant, not
        // the multi-second GB-allocating walk of the original bug.
        Assert.True(sw.ElapsedMilliseconds < 1000,
            $"serialization took {sw.ElapsedMilliseconds}ms, expected < 1s");

        // Bounded size: well under the hard cap, because the collection became
        // a count summary rather than 10k expanded entities.
        Assert.True(body.Length < BodySerializer.MaxBodyBytes,
            $"body was {body.Length} bytes, expected < {BodySerializer.MaxBodyBytes}");

        // The collection is a count summary, and its elements never appear.
        Assert.Contains("<10000 items>", body);
        Assert.DoesNotContain("stack-500", body);
    }

    [Fact]
    public void LazyEnumerable_IsCounted_WithACap_AndDoesNotRunAway()
    {
        var action = new WideAction { Inventory = Lazy(1_000_000) };

        var sw = Stopwatch.StartNew();
        var body = BodySerializer.Serialize(action);
        sw.Stop();

        Assert.True(sw.ElapsedMilliseconds < 1000,
            $"lazy count took {sw.ElapsedMilliseconds}ms, expected < 1s");
        // A lazy (non-ICollection) sequence is counted only up to the cap.
        Assert.Contains("1000+ items", body);
        Assert.DoesNotContain("item-500", body);
    }

    [Fact]
    public void NamedEntity_IsCollapsedToItsName()
    {
        var action = new WideAction { Actor = new FakeEntity { Name = "Bob" } };
        var body = BodySerializer.Serialize(action);

        var json = JObject.Parse(body);
        Assert.Equal("Bob", (string?)json["Actor"]);
    }

    [Fact]
    public void ScalarProperties_PassThroughUntouched()
    {
        var action = new WideAction { Count = 42 };
        var json = JObject.Parse(BodySerializer.Serialize(action));

        Assert.Equal(42, (int?)json["Count"]);
        Assert.Equal("Interact", (string?)json["ActionType"]);
    }

    [Fact]
    public void ObjectTypedProperty_HoldingAnEntity_IsSummarized_NotWalked()
    {
        // Declared `object`, runtime an entity graph — the original resolver's
        // exact-FullName match missed this; the allow-list resolver catches it.
        var holder = new { Payload = (object)new FakeEntity { Name = "Carol" } };
        var body = BodySerializer.Serialize(holder);

        var json = JObject.Parse(body);
        Assert.Equal("Carol", (string?)json["Payload"]);
        Assert.DoesNotContain("Children", body);
    }

    // Stand-ins for Eco's Vector3i: one struct that overrides ToString, one that
    // inherits ValueType.ToString, and one whose override is far too chatty.
    private readonly struct FakeVector3i
    {
        public FakeVector3i(int x, int y, int z) => (X, Y, Z) = (x, y, z);
        public int X { get; }
        public int Y { get; }
        public int Z { get; }
        public override string ToString() => $"({X}, {Y}, {Z})";
    }

    private readonly struct PlainStruct
    {
        public int X { get; init; }
    }

    private readonly struct ChattyStruct
    {
        public override string ToString() => new string('z', 10_000);
    }

    [Fact]
    public void StructWithToString_IsRenderedAsItsText()
    {
        var holder = new { ActionLocation = new FakeVector3i(164, 83, -783) };
        var json = JObject.Parse(BodySerializer.Serialize(holder));
        Assert.Equal("(164, 83, -783)", (string?)json["ActionLocation"]);
    }

    [Fact]
    public void StructWithoutToString_FallsBackToTypeName()
    {
        var holder = new { Thing = new PlainStruct { X = 1 } };
        var json = JObject.Parse(BodySerializer.Serialize(holder));
        Assert.Equal(nameof(PlainStruct), (string?)json["Thing"]);
    }

    [Fact]
    public void StructText_IsCapped()
    {
        var holder = new { Thing = new ChattyStruct() };
        var json = JObject.Parse(BodySerializer.Serialize(holder));
        Assert.Equal(BodySerializer.MaxStructTextChars, ((string?)json["Thing"])!.Length);
    }

    [Fact]
    public void ObjectTypedProperty_HoldingAScalar_KeepsTheScalar()
    {
        var holder = new { Payload = (object)7 };
        var json = JObject.Parse(BodySerializer.Serialize(holder));
        Assert.Equal(7, (int?)json["Payload"]);
    }

    [Fact]
    public void OversizedBody_IsReplacedWithATruncationStub()
    {
        // A single scalar string that on its own exceeds the 16 KB cap: the
        // structural bound can't help (it's genuinely one big value), so the
        // hard byte cap must kick in.
        var holder = new { Blob = new string('z', BodySerializer.MaxBodyBytes + 1) };
        var body = BodySerializer.Serialize(holder);

        var json = JObject.Parse(body);
        Assert.True((bool?)json["truncated"] == true, "expected truncation stub");
        Assert.NotNull(json["action_type"]);
        Assert.True(body.Length < BodySerializer.MaxBodyBytes);
    }

    [Fact]
    public void Null_SerializesToEmptyObject()
    {
        Assert.Equal("{}", BodySerializer.Serialize(null));
    }

    [Fact]
    public void DeeplyNestedEntityGraph_DoesNotRecurse()
    {
        // Build a chain a naive walk would follow; the resolver must flatten it.
        var root = new FakeEntity { Name = "root" };
        var cur = root;
        for (var i = 0; i < 100; i++)
        {
            var child = new FakeEntity { Name = $"n{i}", Parent = cur };
            cur.Children.Add(child);
            cur = child;
        }

        var body = BodySerializer.Serialize(new { Root = root });
        var json = JObject.Parse(body);

        // Root collapses to its name; the 100-deep chain never materializes.
        Assert.Equal("root", (string?)json["Root"]);
        Assert.True(body.Length < 1024);
    }
}
