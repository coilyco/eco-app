// Copyright (c) Kai Ase Siren. Licensed under the MIT License.

namespace EcoTelemetry.Tests;

using System.Text.Json;
using Xunit;

// COI-3160: ClimateSettingsDto is eight double? fields, the shape Eco's Newtonsoft
// resolver serializes as `{}`. These read the rendered bytes.
public class ClimateSettingsWireTests
{
    private static readonly string[] Fields =
    {
        "minCo2Ppm", "temperatureThresholdPpm", "co2PpmPerDegree", "seaLevelThresholdPpm",
        "co2PpmPerMeter", "pollutionMultiplier", "maxCo2PerDayFromAnimals", "minCo2PerDayFromPlants",
    };

    private static JsonElement Render(ClimateSettingsDto dto) => JsonDocument.Parse(WireJson.Render(dto)).RootElement;

    [Fact]
    public void Readable_settings_serialize_as_numbers()
    {
        var root = Render(new ClimateSettingsDto(340, 420, 20, 420, 20, 1.5, 30, 10));

        foreach (var field in Fields) Assert.Equal(JsonValueKind.Number, root.GetProperty(field).ValueKind);
        Assert.Equal(340, root.GetProperty("minCo2Ppm").GetDouble());
        Assert.Equal(420, root.GetProperty("temperatureThresholdPpm").GetDouble());
        Assert.Equal(20, root.GetProperty("co2PpmPerDegree").GetDouble());
    }

    [Fact]
    public void Unreadable_settings_serialize_as_null_never_as_an_object()
    {
        var root = Render(new ClimateSettingsDto(null, null, null, null, null, null, null, null));

        foreach (var field in Fields) Assert.Equal(JsonValueKind.Null, root.GetProperty(field).ValueKind);
    }

    [Fact]
    public void A_partly_readable_ruleset_keeps_each_field_independent()
    {
        var root = Render(new ClimateSettingsDto(340, null, 20, null, null, null, null, null));

        Assert.Equal(JsonValueKind.Number, root.GetProperty("minCo2Ppm").ValueKind);
        Assert.Equal(JsonValueKind.Null, root.GetProperty("temperatureThresholdPpm").ValueKind);
    }
}
