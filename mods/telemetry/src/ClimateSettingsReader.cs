// Copyright (c) Kai Ase Siren. Licensed under the MIT License.

namespace EcoTelemetry;

using System;
using System.Reflection;

// Reads the live per-server climate ruleset off EcoDef.Obj.ClimateSettings and
// flattens it into a ClimateSettingsDto for the /api/v1/climate-settings surface.
//
// Reflection, not typed access, for the same reasons StoreScanner (mods/stores)
// uses it:
//
//  1. Member drift. EcoDef / ClimateSettings member names shift across
//     Eco.ReferenceAssemblies beta releases, and this mod targets several.
//     Reading the documented field names by string, guarded, keeps the reader
//     building and running across versions instead of failing to compile on a
//     rename. We keep more than one candidate name where Eco has renamed a
//     field between releases.
//
//  2. Best-effort. Every accessor is null- and exception-tolerant. A field we
//     cannot read stays null, the DTO still serializes, and the Python consumer
//     falls back to the documented Eco default for just that field. A partial
//     answer is correct here; a 500 is not.
internal static class ClimateSettingsReader
{
    private const BindingFlags Members = BindingFlags.Public | BindingFlags.Instance;
    private const BindingFlags Statics = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static;

    public static ClimateSettingsDto? Read()
    {
        var settings = FindClimateSettings();
        return settings is null ? null : From(settings);
    }

    // Split out so a test can hand it an object carrying the EcoSim.eco member names.
    internal static ClimateSettingsDto From(object settings)
    {
        return new ClimateSettingsDto(
            MinCo2Ppm: AsDouble(GetMember(settings, "MinCO2ppm", "MinCo2Ppm", "MinCO2PPM")),
            TemperatureThresholdPpm: AsDouble(GetMember(settings, "TemperaturesRiseAtCO2ppm", "TemperatureRiseAtCO2ppm")),
            Co2PpmPerDegree: AsDouble(GetMember(settings, "CO2ppmPerDegreeTemperatureRise")),
            SeaLevelThresholdPpm: AsDouble(GetMember(settings, "SeaLevelsRiseAtCO2ppm", "SeaLevelRiseAtCO2ppm")),
            Co2PpmPerMeter: AsDouble(GetMember(settings, "CO2ppmPerSeaLevelMeterRise")),
            PollutionMultiplier: AsDouble(GetMember(settings, "PollutionMultiplier")),
            MaxCo2PerDayFromAnimals: AsDouble(GetMember(settings, "MaxCO2PerDayFromAnimals")),
            MinCo2PerDayFromPlants: AsDouble(GetMember(settings, "MinCO2PerDayFromPlants")),
            MaxSeaLevelRise: AsDouble(GetMember(settings, "MaxSeaLevelRise")),
            MaxSeaLevelRiseRatePerDay: AsDouble(GetMember(settings, "MaxSeaLevelRiseRatePerDay")),
            MaxTemperatureRise: AsDouble(GetMember(settings, "MaxTemperatureRise")),
            MaxTemperatureChangePerDay: AsDouble(GetMember(settings, "MaxTemperatureChangePerDay")));
    }

    // EcoDef.Obj.ClimateSettings. EcoDef is a singleton in Eco.Simulation; we
    // find the type by name across loaded assemblies so we take no compile-time
    // dependency on it, read its static Obj instance, then its ClimateSettings.
    private static object? FindClimateSettings()
    {
        try
        {
            var ecoDef = FindEcoType("EcoDef");
            if (ecoDef is null) return null;

            var obj = GetStatic(ecoDef, "Obj") ?? GetStatic(ecoDef, "Instance");
            if (obj is null) return null;

            return GetMember(obj, "ClimateSettings");
        }
        catch
        {
            // EcoDef unready during early init, or the shape moved. Report absent.
            return null;
        }
    }

    // Matches a type by simple name, preferring the Eco.Simulation namespace so
    // an unrelated "EcoDef" in some other assembly can never shadow the real one.
    private static Type? FindEcoType(string simpleName)
    {
        Type? loose = null;
        foreach (var asm in AppDomain.CurrentDomain.GetAssemblies())
        {
            Type[] types;
            try
            {
                types = asm.GetTypes();
            }
            catch
            {
                // A reflection-only or partially-loaded assembly. Skip it.
                continue;
            }

            foreach (var t in types)
            {
                if (t.Name != simpleName) continue;
                if (t.Namespace is { } ns && ns.StartsWith("Eco", StringComparison.Ordinal))
                {
                    return t;
                }

                loose ??= t;
            }
        }

        return loose;
    }

    private static object? GetStatic(Type type, string name)
    {
        try
        {
            var prop = type.GetProperty(name, Statics);
            if (prop is not null && prop.GetIndexParameters().Length == 0)
            {
                var value = prop.GetValue(null);
                if (value is not null) return value;
            }

            var field = type.GetField(name, Statics);
            if (field is not null) return field.GetValue(null);
        }
        catch
        {
            // Accessor threw during early init. Treat as absent.
        }

        return null;
    }

    private static object? GetMember(object? target, params string[] names)
    {
        if (target is null) return null;
        var type = target.GetType();

        foreach (var name in names)
        {
            try
            {
                var prop = type.GetProperty(name, Members);
                if (prop is not null && prop.GetIndexParameters().Length == 0)
                {
                    var value = prop.GetValue(target);
                    if (value is not null) return value;
                }

                var field = type.GetField(name, Members);
                if (field is not null)
                {
                    var value = field.GetValue(target);
                    if (value is not null) return value;
                }
            }
            catch
            {
                // Accessor threw. Try the next candidate name.
            }
        }

        return null;
    }

    private static double? AsDouble(object? value)
    {
        if (value is null) return null;
        try
        {
            return Convert.ToDouble(value);
        }
        catch
        {
            return null;
        }
    }
}
