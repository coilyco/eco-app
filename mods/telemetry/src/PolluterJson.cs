// Copyright (c) Kai Ase Siren. Licensed under the MIT License.

namespace EcoTelemetry;

using System.Text.Json;
using System.Text.Json.Serialization;

// Renders the rows with System.Text.Json instead of handing the DTOs to Eco's MVC
// formatter. Eco's Newtonsoft setup (Eco.WebServer/Web/Utils/NewtonsoftUtils.cs)
// installs ExpandableObjectContractResolver, whose CreateContract turns every value
// type that is not primitive or an enum into an object contract. Nullable<double>
// and Nullable<bool> are such structs, so ratePerHour, operating,
// operatingEfficiency and totalTons all came out as `{}` live (COI-3157), while
// the string, array and plain-int fields were fine.
//
// The DTO keeps its Newtonsoft attributes for the other mods' pattern, but the
// controller never gives it to Eco's formatter. The same trap waits for any DTO
// here with a nullable struct field, which is why the test asserts on the bytes.
internal static class PolluterJson
{
    private static readonly JsonSerializerOptions Options = new()
    {
        DefaultIgnoreCondition = JsonIgnoreCondition.Never,
        WriteIndented = false,
    };

    public static string Render(PolluterDto[] rows) => JsonSerializer.Serialize(rows, Options);
}
