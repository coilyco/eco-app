// Copyright (c) Kai Ase Siren. Licensed under the MIT License.

namespace EcoTelemetry;

using System.Text.Json.Serialization;
using Newtonsoft.Json;

// The live per-server climate ruleset, read off EcoDef.Obj.ClimateSettings.
//
// Eco exposes no HTTP surface for these values, so the eco-app /climate card
// otherwise ships hardcoded Eco defaults that silently disagree with a retuned
// server (the pollution-machine status that prompted this showed 340 / 420 / 20,
// not the 325 / 400 / 25 defaults). This DTO mirrors the real thresholds so the
// card reads what the in-game UI shows. See eco-app#8.
//
// Dual JSON attributes so it serializes camelCase under Eco's Newtonsoft
// pipeline (the real mod) and System.Text.Json alike - same pattern as
// mods/stores. Every field is nullable: ClimateSettingsReader leaves a value
// null when the underlying setting cannot be read, and the Python consumer
// (eco_mcp_app/climate.py) falls back to the documented Eco default for just
// that field.
public record ClimateSettingsDto(
    [property: JsonPropertyName("minCo2Ppm"), JsonProperty("minCo2Ppm")] double? MinCo2Ppm,
    [property: JsonPropertyName("temperatureThresholdPpm"), JsonProperty("temperatureThresholdPpm")] double? TemperatureThresholdPpm,
    [property: JsonPropertyName("co2PpmPerDegree"), JsonProperty("co2PpmPerDegree")] double? Co2PpmPerDegree,
    [property: JsonPropertyName("seaLevelThresholdPpm"), JsonProperty("seaLevelThresholdPpm")] double? SeaLevelThresholdPpm,
    [property: JsonPropertyName("co2PpmPerMeter"), JsonProperty("co2PpmPerMeter")] double? Co2PpmPerMeter,
    [property: JsonPropertyName("pollutionMultiplier"), JsonProperty("pollutionMultiplier")] double? PollutionMultiplier,
    [property: JsonPropertyName("maxCo2PerDayFromAnimals"), JsonProperty("maxCo2PerDayFromAnimals")] double? MaxCo2PerDayFromAnimals,
    [property: JsonPropertyName("minCo2PerDayFromPlants"), JsonProperty("minCo2PerDayFromPlants")] double? MinCo2PerDayFromPlants,
    // The caps on how far the sim may move, so a retuned MaxSeaLevelRise is readable (COI-3216).
    // Trailing and defaulted, so callers built before they existed still compile.
    [property: JsonPropertyName("maxSeaLevelRise"), JsonProperty("maxSeaLevelRise")] double? MaxSeaLevelRise = null,
    [property: JsonPropertyName("maxSeaLevelRiseRatePerDay"), JsonProperty("maxSeaLevelRiseRatePerDay")] double? MaxSeaLevelRiseRatePerDay = null,
    [property: JsonPropertyName("maxTemperatureRise"), JsonProperty("maxTemperatureRise")] double? MaxTemperatureRise = null,
    [property: JsonPropertyName("maxTemperatureChangePerDay"), JsonProperty("maxTemperatureChangePerDay")] double? MaxTemperatureChangePerDay = null);
