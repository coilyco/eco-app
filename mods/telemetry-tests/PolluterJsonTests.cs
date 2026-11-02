// Copyright (c) Kai Ase Siren. Licensed under the MIT License.

namespace EcoTelemetry.Tests;

using System.Text.Json;
using Xunit;

// COI-3157: every numeric and boolean field of /api/v1/polluters came back as `{}`
// live, because Eco's Newtonsoft contract resolver treats Nullable<T> structs as
// objects. These tests read the rendered bytes, so a regression to any serializer
// that emits an object for those fields fails here.
public class PolluterJsonTests
{
    private static readonly string[] NumberFields = { "ratePerHour", "operatingEfficiency", "totalTons" };

    private static SteamTruckObject Truck(params object[] components) => new()
    {
        Owners = new FakeAlias { Name = "Jovi", UserSet = { new FakeUser { Name = "Jovi" } } },
        Operating = true,
        OperatingEfficiency = 0.5f,
        Components = components.ToList(),
    };

    private static JsonElement[] RenderRows(params object[] worldObjects)
    {
        var rows = worldObjects.Select(PolluterRowBuilder.BuildRow).OfType<PolluterDto>().ToArray();
        return JsonDocument.Parse(PolluterJson.Render(rows)).RootElement.EnumerateArray().Select(e => e.Clone()).ToArray();
    }

    // The invariant the live bug broke. Only position is an object and only
    // ownerUsers is an array, so any other object-valued field fails, and so does
    // a number field that is not a number or null, and operating that is not a boolean or null.
    private static void AssertWireShape(JsonElement row)
    {
        foreach (var property in row.EnumerateObject())
        {
            if (property.Name == "position") continue;
            if (property.Name == "ownerUsers")
            {
                Assert.Equal(JsonValueKind.Array, property.Value.ValueKind);
                continue;
            }

            Assert.True(
                property.Value.ValueKind != JsonValueKind.Object,
                $"field '{property.Name}' serialized as an object: {property.Value.GetRawText()}");
        }

        foreach (var field in NumberFields)
        {
            Assert.Contains(row.GetProperty(field).ValueKind, new[] { JsonValueKind.Number, JsonValueKind.Null });
        }

        Assert.Contains(row.GetProperty("operating").ValueKind, new[] { JsonValueKind.True, JsonValueKind.False, JsonValueKind.Null });
    }

    [Fact]
    public void Readable_values_serialize_as_numbers_and_booleans()
    {
        var component = new AirPollutionComponent { PollutionTonsPerHour = 0.25f, TotalTonsPollution = 12.5f };

        var row = Assert.Single(RenderRows(Truck(component)));

        AssertWireShape(row);
        Assert.Equal(0.25, row.GetProperty("ratePerHour").GetDouble());
        Assert.Equal(12.5, row.GetProperty("totalTons").GetDouble());
        Assert.Equal(0.5, row.GetProperty("operatingEfficiency").GetDouble());
        Assert.Equal(JsonValueKind.True, row.GetProperty("operating").ValueKind);
    }

    [Fact]
    public void An_idle_device_serializes_operating_as_false_not_null()
    {
        var idle = new SteamTruckObject
        {
            Operating = false,
            Components = { new AirPollutionComponent { PollutionTonsPerHour = 1f } },
        };

        var row = Assert.Single(RenderRows(idle));

        Assert.Equal(JsonValueKind.False, row.GetProperty("operating").ValueKind);
    }

    [Fact]
    public void Unreadable_values_serialize_as_null_never_as_an_object()
    {
        var row = Assert.Single(RenderRows(Truck(new Unreadable.AirPollutionComponent())));

        AssertWireShape(row);
        Assert.Equal(JsonValueKind.Null, row.GetProperty("ratePerHour").ValueKind);
        Assert.Equal(JsonValueKind.Null, row.GetProperty("totalTons").ValueKind);
    }

    [Fact]
    public void A_throwing_component_serializes_its_values_as_null()
    {
        var row = Assert.Single(RenderRows(Truck(new Throwing.AirPollutionComponent())));

        AssertWireShape(row);
        Assert.Equal(JsonValueKind.Null, row.GetProperty("ratePerHour").ValueKind);
    }

    [Fact]
    public void Position_and_text_fields_keep_their_wire_names_and_types()
    {
        var row = Assert.Single(RenderRows(Truck(new AirPollutionComponent { PollutionTonsPerHour = 1f })));

        Assert.Equal("SteamTruckObject", row.GetProperty("type").GetString());
        Assert.Equal("Jovi", row.GetProperty("owner").GetString());
        Assert.Equal("tons/hour", row.GetProperty("rateUnit").GetString());
        Assert.Equal(JsonValueKind.String, row.GetProperty("objectId").ValueKind);
        Assert.Equal(2, row.GetProperty("position").GetProperty("y").GetInt32());
    }

    [Fact]
    public void An_empty_world_renders_an_empty_array()
    {
        Assert.Equal("[]", PolluterJson.Render(Array.Empty<PolluterDto>()));
    }
}
