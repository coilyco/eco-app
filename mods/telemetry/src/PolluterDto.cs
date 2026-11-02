// Copyright (c) Kai Ase Siren. Licensed under the MIT License.

namespace EcoTelemetry;

using System.Text.Json.Serialization;
using Newtonsoft.Json;

// One world object that carries an AirPollutionComponent, for GET /api/v1/polluters.
//
// Dual JSON attributes and all-nullable fields follow ClimateSettingsDto: the
// reader leaves anything it cannot read null, and the eco-app consumer
// (eco_mcp_app/climate.py) renders null as "unknown" rather than dropping the row.
//
// RatePerHour is AirPollutionComponent.PollutionTonsPerHour, the configured rate
// and not a measured one, so it reads the same whether or not the device is
// running right now. Operating says whether it is.
public record PolluterDto(
    [property: JsonPropertyName("objectId"), JsonProperty("objectId")] string? ObjectId,
    [property: JsonPropertyName("type"), JsonProperty("type")] string Type,
    [property: JsonPropertyName("name"), JsonProperty("name")] string? Name,
    [property: JsonPropertyName("owner"), JsonProperty("owner")] string? Owner,
    [property: JsonPropertyName("ownerUsers"), JsonProperty("ownerUsers")] string[] OwnerUsers,
    [property: JsonPropertyName("position"), JsonProperty("position")] PolluterPositionDto? Position,
    [property: JsonPropertyName("ratePerHour"), JsonProperty("ratePerHour")] double? RatePerHour,
    [property: JsonPropertyName("rateUnit"), JsonProperty("rateUnit")] string RateUnit,
    [property: JsonPropertyName("operating"), JsonProperty("operating")] bool? Operating,
    [property: JsonPropertyName("operatingEfficiency"), JsonProperty("operatingEfficiency")] double? OperatingEfficiency,
    [property: JsonPropertyName("totalTons"), JsonProperty("totalTons")] double? TotalTons);

public record PolluterPositionDto(
    [property: JsonPropertyName("x"), JsonProperty("x")] int X,
    [property: JsonPropertyName("y"), JsonProperty("y")] int Y,
    [property: JsonPropertyName("z"), JsonProperty("z")] int Z);
