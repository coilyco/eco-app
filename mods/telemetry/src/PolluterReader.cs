// Copyright (c) Kai Ase Siren. Licensed under the MIT License.

namespace EcoTelemetry;

using System;
using System.Collections;
using System.Collections.Generic;
using System.Reflection;
using System.Text.RegularExpressions;
using Eco.Gameplay.Objects;

// Walks every live WorldObject and flattens the ones carrying an
// AirPollutionComponent into PolluterDto rows for /api/v1/polluters.
//
// Reflection by member name, guarded, for the reasons ClimateSettingsReader and
// mods/stores give: Eco member names drift across ReferenceAssemblies betas, and
// a dangling object must cost one skipped row, never a 500. Row building takes a
// plain object so it can be driven without a running Eco server.
//
// Air only. GroundPolluterReportComponent exposes no public rate (its numbers
// come out of ComputePollutionForStacks over whatever the device holds), so
// ground polluters are not listed rather than listed with a made-up rate.
internal static class PolluterReader
{
    public const string AirRateUnit = "tons/hour";

    // A settlement or group owner can stand for hundreds of users. The alias name
    // is the row's owner, and this bounds the member list riding alongside it.
    private const int MaxOwnerUsers = 25;

    private const BindingFlags Members = BindingFlags.Public | BindingFlags.Instance;

    private static readonly Regex MarkupTag = new("<[^>]*>", RegexOptions.Compiled);

    // Null means the object manager was unreadable before any row came out (for
    // example queried before the simulation is up), which the controller answers
    // with 404. An empty array is a real answer: the world has no air polluters.
    public static PolluterDto[]? Read()
    {
        var rows = new List<PolluterDto>();
        var enumerated = false;

        try
        {
            // ForEach is the manager-owned iterator MetricsWorker already uses.
            WorldObjectManager.ForEach(wo =>
            {
                if (wo is null) return;
                try
                {
                    var row = BuildRow(wo);
                    if (row is not null) rows.Add(row);
                }
                catch
                {
                    // Orphaned object or mid-iteration mutation. Skip this one.
                }
            });
            enumerated = true;
        }
        catch
        {
            // Manager unready or the collection mutated under us. Keep what we have.
        }

        return enumerated || rows.Count > 0 ? rows.ToArray() : null;
    }

    internal static PolluterDto? BuildRow(object worldObject)
    {
        var air = FindComponent(worldObject, "AirPollutionComponent");
        if (air is null) return null;

        var type = worldObject.GetType().Name;
        var name = CleanName(AsString(GetMember(worldObject, "FriendlyName", "DisplayName", "Name")));

        var alias = GetMember(worldObject, "Owners");
        var owner = AsString(GetMember(alias, "Name")) ?? AsString(GetMember(worldObject, "NameOfCreator"));

        return new PolluterDto(
            ObjectId: AsString(GetMember(worldObject, "ObjectID")),
            Type: type,
            Name: name ?? type,
            Owner: owner,
            OwnerUsers: ReadOwnerUsers(alias),
            Position: ReadPosition(worldObject),
            RatePerHour: AsDouble(GetMember(air, "PollutionTonsPerHour")),
            RateUnit: AirRateUnit,
            Operating: GetMember(worldObject, "Operating") as bool?,
            OperatingEfficiency: AsDouble(GetMember(worldObject, "OperatingEfficiency")),
            TotalTons: AsDouble(GetMember(air, "TotalTonsPollution")));
    }

    private static object? FindComponent(object worldObject, string typeName)
    {
        var raw = GetMember(worldObject, "Components") ?? Invoke(worldObject, "GetComponents");
        if (raw is not IEnumerable seq || raw is string) return null;

        foreach (var component in seq)
        {
            if (component is not null && component.GetType().Name == typeName) return component;
        }

        return null;
    }

    private static string[] ReadOwnerUsers(object? alias)
    {
        var names = new List<string>();
        if (GetMember(alias, "UserSet") is IEnumerable users && users is not string)
        {
            foreach (var user in users)
            {
                var userName = AsString(GetMember(user, "Name"));
                if (userName is not null) names.Add(userName);
                if (names.Count >= MaxOwnerUsers) break;
            }
        }

        return names.ToArray();
    }

    private static PolluterPositionDto? ReadPosition(object worldObject)
    {
        var pos = GetMember(worldObject, "Position3i", "Position");
        if (pos is null) return null;

        var x = AsDouble(GetMember(pos, "X"));
        var y = AsDouble(GetMember(pos, "Y"));
        var z = AsDouble(GetMember(pos, "Z"));
        if (x is null || y is null || z is null) return null;

        return new PolluterPositionDto((int)Math.Round(x.Value), (int)Math.Round(y.Value), (int)Math.Round(z.Value));
    }

    // Eco display names can carry rich-text tags. The card renders plain text.
    private static string? CleanName(string? raw)
    {
        if (raw is null) return null;
        var cleaned = MarkupTag.Replace(raw, string.Empty).Trim();
        return cleaned.Length == 0 ? null : cleaned;
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
                // Accessor threw (dangling reference). Try the next candidate name.
            }
        }

        return null;
    }

    private static object? Invoke(object target, string name)
    {
        try
        {
            return target.GetType().GetMethod(name, Members, binder: null, types: Type.EmptyTypes, modifiers: null)?.Invoke(target, null);
        }
        catch
        {
            return null;
        }
    }

    private static string? AsString(object? value)
    {
        if (value is null) return null;
        var s = value.ToString();
        return string.IsNullOrWhiteSpace(s) ? null : s;
    }

    // Non-finite values would serialize as the string "NaN" under Newtonsoft and
    // break the consumer's number parse, so they read as unknown instead.
    private static double? AsDouble(object? value)
    {
        if (value is null) return null;
        try
        {
            var d = Convert.ToDouble(value);
            return double.IsFinite(d) ? d : null;
        }
        catch
        {
            return null;
        }
    }
}
