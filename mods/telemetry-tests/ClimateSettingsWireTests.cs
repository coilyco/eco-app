// Copyright (c) Kai Ase Siren. Licensed under the MIT License.

namespace EcoTelemetry.Tests;

using System.Text.Json;
using Xunit;

// COI-3160: ClimateSettingsDto is twelve double? fields, the shape Eco's Newtonsoft
// resolver serializes as `{}`. These read the rendered bytes.
public class ClimateSettingsWireTests
{
    private static readonly string[] Fields =
    {
        "minCo2Ppm", "temperatureThresholdPpm", "co2PpmPerDegree", "seaLevelThresholdPpm",
        "co2PpmPerMeter", "pollutionMultiplier", "maxCo2PerDayFromAnimals", "minCo2PerDayFromPlants",
        "maxSeaLevelRise", "maxSeaLevelRiseRatePerDay", "maxTemperatureRise", "maxTemperatureChangePerDay",
    };

    private static JsonElement Render(ClimateSettingsDto dto) => JsonDocument.Parse(WireJson.Render(dto)).RootElement;

    [Fact]
    public void Readable_settings_serialize_as_numbers()
    {
        var root = Render(new ClimateSettingsDto(340, 420, 20, 420, 20, 1.5, 30, 10, 3, 1, 5, 0.5));

        foreach (var field in Fields) Assert.Equal(JsonValueKind.Number, root.GetProperty(field).ValueKind);
        Assert.Equal(340, root.GetProperty("minCo2Ppm").GetDouble());
        Assert.Equal(420, root.GetProperty("temperatureThresholdPpm").GetDouble());
        Assert.Equal(20, root.GetProperty("co2PpmPerDegree").GetDouble());
    }

    [Fact]
    public void The_caps_on_sea_level_and_temperature_rise_read_back_as_set()
    {
        // The post-restart values on the Sirens server (COI-3217), the readback COI-3216 asks for.
        var root = Render(new ClimateSettingsDto(325, 340, 20, 420, 20, 0.5, 25, -500, 3, 1, 5, 0.5));

        Assert.Equal(3, root.GetProperty("maxSeaLevelRise").GetDouble());
        Assert.Equal(1, root.GetProperty("maxSeaLevelRiseRatePerDay").GetDouble());
        Assert.Equal(5, root.GetProperty("maxTemperatureRise").GetDouble());
        Assert.Equal(0.5, root.GetProperty("maxTemperatureChangePerDay").GetDouble());
        Assert.Equal(0.5, root.GetProperty("pollutionMultiplier").GetDouble());
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
