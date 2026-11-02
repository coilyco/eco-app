// Copyright (c) Kai Ase Siren. Licensed under the MIT License.

namespace EcoTelemetry.Tests;

// Stand-ins shaped like the Eco members PolluterRowBuilder reads by name. The
// builder finds the component by its type's Name, so these are named exactly as
// Eco's are. Shapes follow Eco.Gameplay (AirPollutionComponent, WorldObject).
internal sealed class FakeUser
{
    public string Name { get; init; } = "";
}

internal sealed class FakeAlias
{
    public string Name { get; init; } = "";

    public List<FakeUser> UserSet { get; init; } = new();
}

internal sealed class FakePosition
{
    public int X { get; init; }

    public int Y { get; init; }

    public int Z { get; init; }
}

internal sealed class AirPollutionComponent
{
    public float PollutionTonsPerHour { get; init; }

    public float TotalTonsPollution { get; init; }
}

internal class SteamTruckObject
{
    public Guid ObjectID { get; init; } = Guid.NewGuid();

    public string FriendlyName { get; init; } = "Steam Truck";

    public FakeAlias? Owners { get; init; }

    public string? NameOfCreator { get; init; }

    public bool Operating { get; init; }

    public float OperatingEfficiency { get; init; } = 1f;

    public FakePosition Position3i { get; init; } = new() { X = 1, Y = 2, Z = 3 };

    public List<object> Components { get; init; } = new();
}
