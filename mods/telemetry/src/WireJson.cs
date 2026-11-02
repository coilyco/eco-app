// Copyright (c) Kai Ase Siren. Licensed under the MIT License.

namespace EcoTelemetry;

using System.Text.Json;
using System.Text.Json.Serialization;

// Renders a response DTO with System.Text.Json instead of handing it to Eco's MVC
// formatter. Eco's Newtonsoft setup (Eco.WebServer/Web/Utils/NewtonsoftUtils.cs)
// installs ExpandableObjectContractResolver, whose CreateContract turns every value
// type that is not primitive or an enum into an object contract. Nullable<double>
// and Nullable<bool> are such structs, so ratePerHour, operating,
// operatingEfficiency and totalTons all came out as `{}` live (COI-3157), while
// the string, array and plain-int fields were fine. ClimateSettingsDto has the same
// double? fields (COI-3160).
//
// The DTOs keep their Newtonsoft attributes for the other mods' pattern, but a
// controller here never gives them to Eco's formatter: it returns this string as
// content. Any new DTO with a nullable struct field must go the same way, which is
// why the tests assert on the bytes.
internal static class WireJson
{
    private static readonly JsonSerializerOptions Options = new()
    {
        DefaultIgnoreCondition = JsonIgnoreCondition.Never,
        WriteIndented = false,
    };

    public static string Render<T>(T value) => JsonSerializer.Serialize(value, Options);
}
